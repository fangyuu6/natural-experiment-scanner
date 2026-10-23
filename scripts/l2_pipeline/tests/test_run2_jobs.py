"""Run2 job isolation, stage gates and sealed-root integrity on invented inputs."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.jobs import emit_stage_jobs, load_job, PERSONAS
from scripts.l2_pipeline.forecast_slots import ROSTER
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.run2_jobs import emit_downstream_jobs, emit_ready_designs, emit_nurse_sealed, verify_root_seal
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card, REPO


class Run2JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def context(self, identity='run2_invented', base=None):
        base = base or self.root
        value = card()
        value['design_id'] = identity
        path = write_once(base / 'cards' / (identity + '.json'), value)
        ctx = StageContext(load_card(path), base / 'work', REPO)
        write_once(ctx.design_dir / 'CARD.json', value)
        write_once(ctx.design_dir / 'LITERATURE_IMPORT_LOCK.json', {'card_path':str(path)})
        return ctx

    def s4(self, ctx):
        spec = {'schema':'l2_spec_v1', 'design_id':ctx.card.design_id, 'version':ctx.card.version,
                'card_sha256':sha(ctx.card.path), 'support':{'primary_passed':True}}
        write_once(ctx.design_dir / 'SPEC.json', spec)
        write_once(ctx.design_dir / 'SUPPORT.json', {'primary_passed':True})
        write_once(ctx.design_dir / 'receipts/S4/0001.json',
                   {'card_sha256':sha(ctx.card.path), 'result':{'disposition':'ready'}})

    def test_new_three_forecasts_and_evidenced_review_use_canonical_stage_paths(self):
        ctx = self.context()
        self.s4(ctx)
        root = self.root / 'downstream_jobs'
        report = emit_downstream_jobs(ctx, jobs_root=root)
        self.assertEqual(report['disposition'], 'READY')
        jobs = [load_job(Path(row['job_path'])) for row in report['jobs']]
        self.assertEqual({job.role for job in jobs if job.stage == 'S5'}, set(ROSTER))
        self.assertEqual(report['forecasts'],3)
        self.assertEqual(len(jobs), 4)
        for job in jobs:
            self.assertTrue(Path(job.output_dir).is_relative_to(ctx.design_dir))
            self.assertTrue(Path(job.promote_path).is_relative_to(ctx.design_dir))
            self.assertFalse(job.network_access)
            self.assertEqual(job.retries, 0)
            self.assertFalse(Path(job.output_dir).exists())
            self.assertNotIn('run2-packets-', Path(job.job_path).read_text())
        review = next(job for job in jobs if job.stage == 'S6')
        self.assertEqual(review.contract['review_scope'], 'draft_readiness')
        for bucket in ('must', 'should'):
            self.assertIn('evidence', review.contract['review_output_schema']['properties'][bucket]['items']['required'])
        value = {'design_id':review.design_id, 'candidate_sha256':review.contract['candidate_sha256'],
                 'review_scope':'draft_readiness', 'complete_sweeps':1, 'fix_cycles':0,
                 'must':[], 'should':[], 'verdict':'PASS',
                 'independence_attestation':{'wrote_implementation':False, 'wrote_fixes':False,
                                             'real_outcome_associations_read':False}}
        out = Path(review.output_path)
        out.parent.mkdir(parents=True)
        out.write_text(json.dumps(value))
        self.assertTrue(collect_job(Path(review.job_path))['validated'])
        self.assertTrue(Path(review.promote_path).exists())
        self.assertTrue((ctx.design_dir / 'REVIEW_DRAFT_run2-readiness.json').exists())

    def test_existing_five_persona_lock_preserves_bytes_and_roster(self):
        ctx=self.context();self.s4(ctx)
        lock=write_once(ctx.design_dir/'PREDICTIONS_LOCK.json',{'personas':list(PERSONAS),'fixture':'retained old lock'})
        before=lock.read_bytes()
        report=emit_downstream_jobs(ctx,jobs_root=self.root/'old_five')
        self.assertEqual(report['forecasts'],5)
        self.assertEqual([j['role'] for j in report['jobs'] if j['stage']=='S5'],list(PERSONAS))
        self.assertEqual(lock.read_bytes(),before)
        self.assertFalse((ctx.design_dir/'FORECAST_SLOTS.json').exists())

    def test_incomplete_or_changed_stage_card_is_parked_without_packets(self):
        ctx = self.context()
        root = self.root / 'jobs'
        self.assertEqual(emit_downstream_jobs(ctx, jobs_root=root)['disposition'], 'PARKED')
        self.s4(ctx)
        ctx.card.path.write_text(ctx.card.path.read_text() + '\n')
        report = emit_downstream_jobs(ctx, jobs_root=root)
        self.assertEqual(report['disposition'], 'PARKED')
        self.assertIn('current source card', report['reason'])
        self.assertFalse(root.exists())

    def test_design_failure_does_not_abort_other_designs_and_filter_is_exact(self):
        good = self.context('run2_good')
        bad = self.context('run2_bad')
        parked = self.context('run2_parked')
        self.s4(good)
        self.s4(bad)
        def prepare(ctx):
            if ctx.card.design_id == 'run2_bad':
                raise ValueError('invented source linkage failure')
            return ctx
        with patch('scripts.l2_pipeline.run2_preparation.prepare_context', side_effect=prepare):
            result = emit_ready_designs(workspace=self.root / 'work', jobs_root=self.root / 'jobs')
        self.assertEqual(result['counts'], {'READY':1, 'PARKED':1, 'FAILED':1})
        failure = next(row for row in result['results'] if row['design_id'] == 'run2_bad')
        self.assertIn('invented source linkage failure', failure['reason'])
        with patch('scripts.l2_pipeline.run2_preparation.prepare_context', side_effect=prepare):
            selected = emit_ready_designs(workspace=self.root / 'work', jobs_root=self.root / 'filtered', design_ids=['run2_good'])
        self.assertEqual([row['design_id'] for row in selected['results']], ['run2_good'])
        with self.assertRaisesRegex(ValueError, 'Unknown design'):
            emit_ready_designs(workspace=self.root / 'work', jobs_root=self.root / 'filtered', design_ids=['run2_missing'])

    def nurse(self):
        old = self.root / 'old_v5'
        ctx = self.context('run1_nurse_overtime', old)
        self.s4(ctx)
        write_once(ctx.design_dir / 'FREEZE_FINAL.json', {'invented_frozen_candidate':True})
        write_once(ctx.design_dir / 'PREDICTIONS_LOCK.json', {'invented_forecasts':5})
        originals = emit_stage_jobs(ctx, 'S6', jobs_root=old / 'sealed_jobs', attempt_id='20261007-sealed')
        # The shared dispatch root is blocked by an unrelated reserved design.
        reserved = old / 'sealed_jobs/S6/run1_ems_compact/reviewer.20261007-sealed.md'
        reserved.parent.mkdir(parents=True)
        reserved.write_text('Invented unrelated reserved job')
        return old, ctx, originals[0]

    def test_nurse_seal_is_only_nurse_preserves_originals_and_rebinds_all_metadata(self):
        old, ctx, original = self.nurse()
        before = {str(p):sha(p) for p in old.rglob('*') if p.is_file()}
        root = self.root / 'nurse_sealed'
        report = emit_nurse_sealed(jobs_root=root, nurse_v5=old)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['jobs'], 1)
        self.assertEqual({str(p):sha(p) for p in old.rglob('*') if p.is_file()}, before)
        rebound = load_job(Path(report['job_paths'][0]))
        self.assertEqual(rebound.contract['candidate_sha256'], sha(ctx.design_dir / 'FREEZE_FINAL.json'))
        self.assertEqual(rebound.contract['review_scope'], 'sealed_candidate')
        self.assertFalse(rebound.contract['execution_authorized'])
        self.assertTrue(Path(rebound.promote_path).is_relative_to(root))
        self.assertNotEqual(rebound.output_dir, original.output_dir)
        self.assertNotIn(original.output_dir, Path(rebound.job_path).read_text())
        self.assertEqual(verify_root_seal(root)['status'], 'PASS')
        intruder = root / 'S6/run1_pt_compact/reviewer.md'
        intruder.parent.mkdir(parents=True)
        intruder.write_text('Invented extraneous job')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            verify_root_seal(root)

    def test_nurse_retarget_retains_large_transport_hash_binding(self):
        old,ctx,original=self.nurse()
        before={str(p):sha(p) for p in old.rglob('*') if p.is_file()}
        with patch('scripts.l2_pipeline.agents.jobs.REVIEW_PROMPT_CHAR_LIMIT',30000):
            report=emit_nurse_sealed(jobs_root=self.root/'transport_nurse',nurse_v5=old)
        job=load_job(Path(report['job_paths'][0]))
        transport=Path(job.job_path).with_suffix('.review-inputs.json')
        self.assertTrue(transport.is_file())
        self.assertIn(str(transport),job.contract['permitted_metadata_paths'])
        self.assertEqual({str(p):sha(p) for p in old.rglob('*') if p.is_file()},before)
        value=json.loads(transport.read_text())
        self.assertEqual(value['binding']['output_path'],job.output_path)
        self.assertEqual(value['binding']['contract'],job.contract)
        transport.write_text(transport.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'transport binding changed'):load_job(Path(job.job_path))

    def test_nurse_root_refuses_wrong_design_and_existing_unrelated_root(self):
        old, _, _ = self.nurse()
        with self.assertRaisesRegex(ValueError, 'accepts run1_nurse_overtime only'):
            emit_nurse_sealed(jobs_root=self.root / 'nurse', nurse_v5=old, design_id='run1_ems_compact')
        root = self.root / 'contaminated'
        other = root / 'S6/run1_ems_compact/reviewer.md'
        other.parent.mkdir(parents=True)
        other.write_text('Invented reserved EMS packet')
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            emit_nurse_sealed(jobs_root=root, nurse_v5=old)
        self.assertEqual(list(root.rglob('*.*')), [other])


if __name__ == '__main__':
    unittest.main()
