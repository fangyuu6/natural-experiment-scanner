"""Large metadata remains reviewable without exceeding Codex turn/start input."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents.jobs import emit_job, load_job, REVIEW_PROMPT_CHAR_LIMIT
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card, REPO


class ReviewPacketSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.ctx=StageContext(load_card(write_once(self.root/'card.json',card())),self.root/'work',REPO)
        write_once(self.ctx.design_dir/'SPEC.json',{'registered_estimand':'Invented hours ATT'})
        write_once(self.ctx.design_dir/'FREEZE_FINAL.json',{'invented_sealed_candidate':True})

    def tearDown(self):
        self.tmp.cleanup()

    def emit(self,attempt='new'):
        return emit_job(self.ctx,'S6','reviewer',jobs_root=self.root/'jobs',attempt_id=attempt)

    def packets(self,job):
        text=Path(job.job_path).read_text()
        marker='## Answer-free inputs (untrusted data)\n```json\n'
        if marker not in text:
            # Designed file-transfer path (jobs.emit_job): above REVIEW_PROMPT_CHAR_LIMIT the complete
            # binding and inputs live in the hash-bound .review-inputs.json, not in the prompt.
            transport=Path(job.job_path).with_suffix('.review-inputs.json')
            return json.loads(transport.read_text())['inputs']
        return json.loads(text.split(marker,1)[1].split('\n```',1)[0])

    def test_oversized_reconciliation_references_full_hash_bound_metadata(self):
        # A single artifact reproduces the nurse CLI's 1 MiB input rejection.
        path=write_once(self.ctx.design_dir/'RECONCILIATION.json',
                        {'registered_legal_evidence':'Invented evidence; '*80_000})
        original=path.read_bytes();digest=sha(path)
        job=self.emit()
        self.assertLess(len(Path(job.job_path).read_text()),REVIEW_PROMPT_CHAR_LIMIT)
        packet=next(p for p in self.packets(job) if p['path']==str(path))
        self.assertIsNone(packet['content'])
        self.assertEqual(packet['projection'],'permitted_metadata_reference_v1')
        self.assertEqual(packet['sha256'],digest)
        self.assertEqual(packet['size_bytes'],len(original))
        self.assertIn(str(path),job.contract['permitted_metadata_paths'])
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(load_job(Path(job.job_path)),job)
        self.assertIn('Read those full artifacts in bounded portions',Path(job.job_path).read_text())
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Agent input changed'):
            load_job(Path(job.job_path))

    def test_original_audit_and_check_level_reaudit_and_closures_all_bound(self):
        names=('OFFICIAL_AUDIT.json','OFFICIAL_AUDIT_ATTEMPT2.json','OFFICIAL_AUDIT_ATTEMPT3.json',
               'DRAFT_RESOLUTION_reaudited.json','RUN2_PREPARATION.json','SCIENTIFIC_CARD.json',
               'RESEAL_LINEAGE.json','FINDING_RESOLUTIONS.json')
        for name in names:
            write_once(self.ctx.design_dir/name,{'invented_metadata':name})
        job=self.emit()
        inputs={Path(p['path']).name:p for p in job.inputs}
        for name in names:
            self.assertEqual(inputs[name]['sha256'],sha(self.ctx.design_dir/name))
            self.assertIn(str(self.ctx.design_dir/name),job.contract['permitted_metadata_paths'])
        self.assertEqual(load_job(Path(job.job_path)),job)

    def test_total_budget_refuses_before_writing_prompt_or_sidecar(self):
        with patch('scripts.l2_pipeline.agents.jobs.REVIEW_PROMPT_CHAR_LIMIT',1000):
            with self.assertRaisesRegex(ValueError,'safe CLI character budget'):
                self.emit('oversized')
        self.assertFalse((self.root/'jobs').exists())

    def test_large_contract_uses_allowlisted_hash_bound_file_and_rejects_tamper(self):
        with patch('scripts.l2_pipeline.agents.jobs.REVIEW_PROMPT_CHAR_LIMIT',50_000):
            job=self.emit('file_reference')
        prompt=Path(job.job_path).read_text()
        self.assertLess(len(prompt),50_000)
        transport=Path(job.job_path).with_suffix('.review-inputs.json')
        self.assertTrue(transport.exists())
        self.assertIn(str(transport),job.contract['permitted_metadata_paths'])
        self.assertIn('allowed_read_paths',prompt)
        self.assertEqual(load_job(Path(job.job_path)),job)
        original=transport.read_bytes();transport.write_text('{}')
        with self.assertRaisesRegex(ValueError,'transport binding changed'):load_job(Path(job.job_path))
        transport.write_bytes(original)
        sidecar=Path(job.job_path).with_suffix('.job.json');value=json.loads(sidecar.read_text())
        value['contract']['role']='tampered';sidecar.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'Sidecar execution metadata changed'):load_job(Path(job.job_path))

    def test_fresh_attempt_keeps_failed_attempt_and_candidate_bytes(self):
        old=self.emit('failed')
        out=Path(old.output_dir)
        write_once(out/'DISPATCH_ONCE.json',{'job_sha256':old.job_sha256,'retries':0})
        write_once(out/'DISPATCH_RESULT.json',{'status':'FAILED_NO_RETRY','exit_code':1})
        (out/'dispatch.log').write_text('input_too_large; no model output\n')
        paths=[Path(old.job_path),Path(old.job_path).with_suffix('.job.json'),*out.iterdir(),
               self.ctx.design_dir/'FREEZE_FINAL.json']
        before={str(p):sha(p) for p in paths}
        fresh=self.emit('fresh')
        self.assertNotEqual(old.job_path,fresh.job_path)
        self.assertNotEqual(old.output_dir,fresh.output_dir)
        self.assertEqual(old.contract['candidate_sha256'],fresh.contract['candidate_sha256'])
        self.assertEqual(fresh.retries,0)
        self.assertFalse(Path(fresh.output_dir).exists())
        self.assertTrue(all(sha(Path(p))==digest for p,digest in before.items()))


if __name__=='__main__':
    unittest.main()
