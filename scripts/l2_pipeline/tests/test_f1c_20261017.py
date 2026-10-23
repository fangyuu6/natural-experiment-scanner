"""F1-C: purpose_direction gate (estimate artifact only), pipeline stamp, S7 binding. Invented, outcome-free fixtures."""
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import c70_l3, f1_coding as f
from scripts.l2_pipeline.agents import purpose_direction as d
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.contract_tests import golden_output
from scripts.l2_pipeline.agents.contract_tests.packets import _context
from scripts.l2_pipeline.storage import ensure_once, sha


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)

    def touch(self, name):
        (self.root / name).write_text('{}')

    def test_freeze_approval_and_forecast_files_do_not_protect(self):
        for n in ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'APPROVAL_PREP.json', 'APPROVAL_FINAL.json', 'PREDICTIONS_LOCK.json', 'FORECAST_SNAPSHOT_DRAFT.json'):
            self.touch(n)
        self.assertFalse(f.f1_protected(self.root))
        self.assertTrue(c70_l3.protected(self.root))   # the shared rule is unchanged

    def test_each_estimate_artifact_protects(self):
        for n in f.ESTIMATE_FILES + ('SEALED_x.json',):
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / n).write_text('{}')
                self.assertTrue(f.f1_protected(Path(tmp)), n)
        self.assertFalse(f.f1_protected(self.root))


class Coding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name).resolve()
        self.ctx = _context(self.root, 's0_direction')
        ensure_once(self.ctx.design_dir / 'PURPOSE_VERIFICATION.json', dict(citations=[dict(source_ref='https://example.invalid/statute', locator='section 1', source_type='statute', supports=['target_outcome'])]))

    def output(self, job, sign, worker):
        value = golden_output(job); value.update(purpose_direction=sign, coder_id=worker)
        ensure_once(Path(job.output_path), value); collect_job(job.job_path)

    def code(self, sign=1):
        jobs = [d.emit(self.ctx, r, self.root / 'jobs') for r in d.ROLES[:2]]
        for i, j in enumerate(jobs): self.output(j, sign, 'worker' + str(i))
        return d.resolve(self.ctx, [j.job_path for j in jobs])

    def test_coding_after_freeze_and_forecast_is_allowed_and_stamped(self):
        ensure_once(self.ctx.design_dir / 'FREEZE_PREP.json', dict(files={}))
        ensure_once(self.ctx.design_dir / 'PREDICTIONS_LOCK.json', dict(marker='FORECAST_SECRET_MARKER'))
        jobs = [d.emit(self.ctx, r, self.root / 'jobs') for r in d.ROLES[:2]]
        for j in jobs:   # forecast content never reaches the coder packet
            self.assertNotIn('FORECAST_SECRET_MARKER', Path(j.job_path).read_text())
            d.validate_job(j)
        for i, j in enumerate(jobs): self.output(j, -1, 'worker' + str(i))
        self.assertEqual(d.resolve(self.ctx, [j.job_path for j in jobs])['purpose_direction'], -1)
        stamp = json.loads((self.ctx.design_dir / f.STAMP).read_text())
        self.assertEqual(stamp['coding_file_sha256'], sha(self.ctx.design_dir / f.CODING))
        self.assertEqual(stamp['f1_gate'], 'no_estimate_artifact')
        self.assertEqual(f.stamp(self.ctx.design_dir), stamp)   # idempotent, never rewritten

    def test_packet_with_forecast_field_is_rejected(self):
        job = d.emit(self.ctx, 'purpose_direction_A', self.root / 'jobs')
        from dataclasses import replace
        bad = replace(job, contract=dict(job.contract, forecast_summary='x'))
        with self.assertRaises(ValueError): d.validate_job(bad)

    def test_estimate_artifact_blocks_new_coding_and_stamp(self):
        ensure_once(self.ctx.design_dir / 'RESULT.json', dict(x=1))
        with self.assertRaises(ValueError): d.emit(self.ctx, 'purpose_direction_A', self.root / 'jobs')
        self.assertIsNone(f.stamp(self.ctx.design_dir))

    def test_estimate_after_coding_closes_stamp_for_a_late_file(self):
        with patch('scripts.l2_pipeline.f1_coding.stamp'): self.code(1)   # coded earlier, never stamped
        self.assertFalse((self.ctx.design_dir / f.STAMP).exists())
        ensure_once(self.ctx.design_dir / 'TERMINAL.json', dict(status='COMPLETED'))
        self.assertIsNone(f.stamp(self.ctx.design_dir))

    def test_bind_fills_unbound_direction_and_adds_time_hash_epoch(self):
        self.code(1)
        reg = dict(first_order=dict(purpose_direction=None, purpose_direction_evidence=None), purpose_direction_status='FIELD_MISSING')
        out = f.bind(reg, self.ctx.design_dir)
        fo = out['first_order']
        self.assertEqual(fo['purpose_direction'], 1); self.assertEqual(out['purpose_direction_status'], 'EVAL')
        self.assertEqual(fo['coding_file_sha256'], sha(self.ctx.design_dir / f.CODING)); self.assertEqual(fo['coding_epoch'], 'prospective')
        self.assertEqual(fo['purpose_direction_coded_at'], json.loads((self.ctx.design_dir / f.STAMP).read_text())['accepted_at'])
        self.assertIsNone(reg['first_order']['purpose_direction'])   # input untouched

    def test_bind_keeps_manifest_bound_direction(self):
        with patch('scripts.l2_pipeline.f1_coding.stamp'): self.code(1)   # manifest-bound designs may predate the stamp
        reg = dict(first_order=dict(purpose_direction=-1, purpose_direction_evidence={'bound': True}), purpose_direction_status='EVAL')
        fo = f.bind(reg, self.ctx.design_dir)['first_order']
        self.assertEqual(fo['purpose_direction'], -1); self.assertEqual(fo['purpose_direction_evidence'], {'bound': True})
        self.assertEqual(fo['coding_epoch'], 'prospective'); self.assertNotIn('purpose_direction_coded_at', fo)

    def test_bind_without_file_or_registration_is_a_no_op_and_changed_file_is_refused(self):
        self.assertIsNone(f.bind(None, self.ctx.design_dir))
        reg = dict(first_order=dict(purpose_direction=None, purpose_direction_evidence=None))
        self.assertEqual(f.bind(reg, self.ctx.design_dir), reg)
        self.code(1)
        stamp = self.ctx.design_dir / f.STAMP
        stamp.write_text(json.dumps(dict(json.loads(stamp.read_text()), coding_file_sha256='0' * 64)))
        with self.assertRaises(ValueError): f.bind(reg, self.ctx.design_dir)


if __name__ == '__main__':
    unittest.main()
