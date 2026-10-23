"""Historical draft collection preserves evidence and never relaxes sealed gates."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.cli import main
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.freeze import code_inventory
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card


class HistoricalDraftCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.ctx=StageContext(load_card(write_once(self.root/'card.json',card())),self.root/'work',self.root)
        self.spec=write_once(self.ctx.design_dir/'SPEC.json',{'estimand':'invented original-units ATT'})
        self.code=self.root/'scripts/l2_pipeline/invented.py'
        self.code.parent.mkdir(parents=True)
        self.code.write_text('# invented reviewed implementation\n')
        self.templates=self.root/'templates/v1'
        template=self.templates.parent/'v3/reviewer.md'
        template.parent.mkdir(parents=True)
        template.write_text('Invented reviewer {{ROLE}} writes {{OUTPUT}}.\n')

    def tearDown(self):
        self.tmp.cleanup()

    def job(self,sealed=False,inventory=None):
        if sealed: write_once(self.ctx.design_dir/'FREEZE_FINAL.json',{'invented':'sealed candidate'})
        if inventory is None: inventory={str(self.code):sha(self.code)}
        with patch('scripts.l2_pipeline.agents.jobs.TEMPLATES',self.templates),patch('scripts.l2_pipeline.freeze.code_inventory',return_value=inventory):
            job=emit_job(self.ctx,'S6','reviewer',jobs_root=self.root/'jobs',allow_draft=True)
        # Reconstruct a legacy pre-snapshot packet to keep testing the historical
        # collection exception used by immutable archived jobs.
        from dataclasses import asdict, replace
        contract = dict(job.contract)
        for key in ('code_snapshot', 'snapshot_root', 'snapshot_source_inventory'): contract.pop(key)
        contract['code_inventory'] = inventory
        contract['permitted_code_paths'] = list(inventory)
        job = replace(job, contract=contract)
        binding = asdict(job); binding.pop('job_sha256')
        packet = Path(job.job_path)
        text = packet.read_text()
        marker = '## Execution metadata binding (data)\n```json\n'
        prefix, tail = text.split(marker, 1)
        text = prefix + marker + json.dumps(binding, indent=2, sort_keys=True) + '\n```' + tail.split('\n```', 1)[1]
        packet.write_text(text)
        job = replace(job, job_sha256=sha(packet))
        packet.with_suffix('.job.json').write_text(json.dumps(asdict(job)))
        write_once(Path(job.output_path),{
            'design_id':job.design_id,'candidate_sha256':job.contract['candidate_sha256'],
            'review_scope':job.contract['review_scope'],'complete_sweeps':1,'fix_cycles':0,
            'must':[],'should':[],'verdict':'PASS',
            'independence_attestation':{'wrote_implementation':False,'wrote_fixes':False,'real_outcome_associations_read':False}})
        out=Path(job.output_dir)
        write_once(out/'DISPATCH_ONCE.json',{'job_sha256':job.job_sha256,'retries':0,'host_assertion':True,'started_at':'2026-10-09T01:00:00+00:00'})
        write_once(out/'DISPATCH_RESULT.json',{'status':'COMPLETED','exit_code':0,'job_path':job.job_path,'role':job.role,'retries':0,
                                             'started_at':'2026-10-09T01:00:00+00:00','completed_at':'2026-10-09T01:00:05+00:00','elapsed_seconds':5.0})
        self.code.write_text('# invented corrected implementation\n')
        return job

    def change(self,path,**updates):
        value=json.loads(path.read_text());value.update(updates);path.write_text(json.dumps(value))

    def test_stale_draft_requires_opt_in_and_retains_evidence_without_authority(self):
        job=self.job()
        before={str(p):sha(p) for p in (Path(job.job_path),Path(job.job_path).with_suffix('.job.json'),Path(job.output_path),self.spec)}
        with self.assertRaisesRegex(ValueError,'Reviewed code input changed'): load_job(Path(job.job_path))
        with self.assertRaisesRegex(ValueError,'Reviewed code input changed'): collect_job(job.job_path)
        self.assertEqual(load_job(Path(job.job_path),allow_historical_draft=True),job)
        receipt=collect_job(job.job_path,allow_historical_draft=True)
        review=json.loads(Path(job.promote_path).read_text())
        history=receipt['historical_collection']
        self.assertEqual(review['historical_collection'],history)
        self.assertTrue(history['findings_only'])
        self.assertTrue(review['sealed_review_required'])
        self.assertFalse(review['execution_authorized'])
        self.assertFalse(receipt['execution_authorized'])
        self.assertEqual(history['code_inventory_mismatches'],[{'path':str(self.code),'reviewed_sha256':job.contract['code_inventory'][str(self.code)],'current_sha256':sha(self.code)}])
        for path,digest in history['dispatch_evidence_hashes'].items(): self.assertEqual(sha(Path(path)),digest)
        self.assertTrue(all(sha(Path(p))==digest for p,digest in before.items()))
        self.assertEqual(receipt,collect_job(job.job_path,allow_historical_draft=True))

    def test_sealed_candidate_cannot_use_historical_exception(self):
        job=self.job(sealed=True)
        for call in (load_job,collect_job):
            with self.assertRaisesRegex(ValueError,'draft_readiness'): call(Path(job.job_path),allow_historical_draft=True)
        with self.assertRaisesRegex(ValueError,'Reviewed code input changed'): collect_job(job.job_path)
        self.assertFalse(Path(job.promote_path).exists())

    def test_changed_scientific_input_prompt_and_template_still_refused(self):
        job=self.job()
        for path,message in ((self.spec,'Agent input changed'),(Path(job.job_path),'prompt/template changed'),(Path(job.template_path),'prompt/template changed')):
            original=path.read_bytes()
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,message): collect_job(job.job_path,allow_historical_draft=True)
            path.write_bytes(original)
        self.assertFalse(Path(job.promote_path).exists())

    def test_incomplete_or_misbound_dispatch_refused(self):
        job=self.job();path=Path(job.output_dir)/'DISPATCH_RESULT.json';original=path.read_bytes()
        for updates in ({'status':'FAILED_NO_RETRY'},{'exit_code':1},{'exit_code':False},{'job_path':'invented-other.md'},
                        {'role':'Card'},{'retries':1},{'retries':False}):
            path.write_bytes(original);self.change(path,**updates)
            with self.assertRaisesRegex(ValueError,'exact COMPLETED'): collect_job(job.job_path,allow_historical_draft=True)
        path.unlink()
        with self.assertRaises(FileNotFoundError): collect_job(job.job_path,allow_historical_draft=True)
        self.assertFalse(Path(job.promote_path).exists())

    def test_changed_reservation_refused(self):
        job=self.job();path=Path(job.output_dir)/'DISPATCH_ONCE.json';original=path.read_bytes()
        for updates in ({'job_sha256':'invented-other-hash'},{'host_assertion':False},{'host_assertion':1},{'retries':1},{'retries':False}):
            path.write_bytes(original);self.change(path,**updates)
            with self.assertRaisesRegex(ValueError,'reservation differs'): collect_job(job.job_path,allow_historical_draft=True)
        self.assertFalse(Path(job.promote_path).exists())

    def test_timestamps_must_be_coherent_and_aware(self):
        job=self.job();path=Path(job.output_dir)/'DISPATCH_RESULT.json';original=path.read_bytes()
        for updates in ({'started_at':'bad'},{'started_at':'2026-10-09T01:00:01+00:00'},
                        {'completed_at':'2026-10-09T00:59:59+00:00'},{'completed_at':'2026-10-09T01:00:05'},
                        {'elapsed_seconds':False},{'elapsed_seconds':-1},{'elapsed_seconds':float('nan')},{'elapsed_seconds':500}):
            path.write_bytes(original);self.change(path,**updates)
            with self.assertRaisesRegex(ValueError,'timestamps'): collect_job(job.job_path,allow_historical_draft=True)
        self.assertFalse(Path(job.promote_path).exists())

    def test_cli_requires_explicit_historical_option(self):
        job=self.job()
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught: main(['collect','--job',job.job_path])
            self.assertEqual(caught.exception.code,1)
            self.assertEqual(main(['collect','--job',job.job_path,'--allow-historical-draft']),0)

    def test_s6_inventory_matches_full_execution_closure_with_exact_permissions(self):
        repo=Path(__file__).resolve().parents[3]
        ctx=StageContext(self.ctx.card,self.root/'closure_work',repo)
        write_once(ctx.design_dir/'SPEC.json',{'estimand':'invented closure test'})
        with patch('scripts.l2_pipeline.agents.jobs.TEMPLATES',self.templates):
            job=emit_job(ctx,'S6','reviewer',jobs_root=self.root/'closure_jobs',allow_draft=True)
        inventory=code_inventory()
        self.assertEqual(job.contract['snapshot_source_inventory'],inventory)
        tree = Path(job.contract['snapshot_root'])/job.contract['code_snapshot']['base']/'tree'
        self.assertEqual(job.contract['permitted_code_roots'],[str(tree/'scripts/l2_pipeline')])
        inventory = job.contract['code_inventory']
        code={p for p in inventory if Path(p).suffix=='.py'}
        metadata=set(inventory)-code
        self.assertEqual(set(job.contract['permitted_code_paths']),code)
        self.assertTrue(metadata<=set(job.contract['permitted_metadata_paths']))
        outside={p for p in inventory if not Path(p).is_relative_to(tree/'scripts/l2_pipeline')}
        self.assertTrue(outside)
        self.assertTrue(outside<=set(job.contract['permitted_code_paths'])|set(job.contract['permitted_metadata_paths']))
        self.assertNotIn(str(repo/'scripts/did_samples'),job.contract['permitted_code_roots'])
        self.assertNotIn(str(repo/'results'),job.contract['permitted_code_roots'])
        self.assertEqual(load_job(Path(job.job_path)),job)

    def test_outside_dependency_hash_mutation_refused_by_strict_loader(self):
        outside=self.root/'scripts/did_samples/cases/invented_dependency.py'
        outside.parent.mkdir(parents=True)
        outside.write_text('# invented outside source dependency\n')
        job=self.job(inventory={str(self.code):sha(self.code),str(outside):sha(outside)})
        self.code.write_text('# invented reviewed implementation\n')
        self.assertEqual(load_job(Path(job.job_path)),job)
        self.assertIn(str(outside),job.contract['permitted_code_paths'])
        self.assertNotIn(str(outside.parent),job.contract['permitted_code_roots'])
        outside.write_text('# invented changed dependency\n')
        with self.assertRaisesRegex(ValueError,'Reviewed code input changed'): load_job(Path(job.job_path))


if __name__=='__main__':
    unittest.main()
