"""Prospective fresh dispatch gates reject incomplete or stale hub receipts."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.intake.fresh_gate import fresh_gate
from scripts.l2_pipeline.intake.v8b_calendar import validate_audit_clock
from scripts.l2_pipeline.storage import sha


class FreshGateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)/'prospective_attempt'
        self.root.mkdir()
        self.did = 'prospective_design'
        self.card = self.write('cards/design.json', {'design_id': self.did})
        self.fresh_path = self.write('fresh_audit_jobs/S1/'+self.did+'/auditor.md', {})
        self.cal_path = self.write('calibration_jobs/S1/'+self.did+'/auditor.'+self.root.name+'.md', {})
        self.sample = ['U'+str(i) for i in range(8)]
        self.answer = dict(import_sha256='locked-import', checks=[dict(
            unit=u, status='match', panel_year=2000, official_locator='synthetic statute',
            official_operational_calendar_year=2000, official_operational_date='2000-01-01',
            class_a_offset=True, direction_match=True) for u in self.sample])
        self.output = self.write('work/calibration/audit.json', self.answer)
        self.purpose_data = dict(decision='accept', design_id=self.did,
                                 card_sha256=sha(self.card), purpose_rule='V6.2')
        self.purpose = self.write('purpose_work/ruling.json', self.purpose_data)
        self.receipt_data = dict(confirmed=True, calibration_job_sha256=sha(self.cal_path),
                                 calibration_output_sha256=sha(self.output),
                                 purpose_ruling_path=str(self.purpose),
                                 purpose_ruling_sha256=sha(self.purpose))
        self.receipt = self.write('work/calibration/CALIBRATION_HUB_ACCEPTANCE.json', self.receipt_data)
        common = dict(card_path=str(self.card), card_sha256=sha(self.card))
        self.fresh = SimpleNamespace(design_id=self.did, contract=dict(
            **common, direction_gate='SCREENED_PENDING_CALIBRATION', sample=['F'+str(i) for i in range(10)]))
        self.cal = SimpleNamespace(design_id=self.did, output_path=str(self.output),
            output_dir=str(self.output.parent), contract=dict(**common,
            audit_target='official_operational_calendar_year', import_sha256='locked-import',
            sample=self.sample, sampled_tuples=[dict(unit=u, status='treated', start_period=2000) for u in self.sample]))
        self.write('JOBS_MANIFEST.json', dict(jobs=[dict(path=str(self.fresh_path), sha256=sha(self.fresh_path))]))
        self.mock = patch('scripts.l2_pipeline.agents.jobs.load_job', side_effect=lambda p:
                          self.fresh if Path(p)==self.fresh_path else self.cal)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def write(self, relative, data):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def gate(self):
        return fresh_gate(self.root, validate_audit_clock=validate_audit_clock)

    def test_bound_confirmed_receipts_allow_fresh_gate_without_writes(self):
        before = {p: sha(p) for p in self.root.rglob('*') if p.is_file()}
        self.gate()
        self.assertEqual(before, {p: sha(p) for p in self.root.rglob('*') if p.is_file()})

    def test_calibration_receipt_must_exist(self):
        self.receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            self.gate()

    def test_calibration_confirmation_and_output_hash_are_required(self):
        for key, value in [('confirmed', False), ('calibration_job_sha256', 'stale'),
                           ('calibration_output_sha256', 'stale')]:
            with self.subTest(key=key):
                self.write(str(self.receipt.relative_to(self.root)), dict(self.receipt_data, **{key: value}))
                with self.assertRaisesRegex(ValueError, 'hub-confirmed calibration'):
                    self.gate()

    def test_v62_accepted_purpose_and_same_card_are_required(self):
        for key, value in [('decision', 'reject'), ('purpose_rule', 'V6.1'),
                           ('card_sha256', 'different-card'), ('design_id', 'other')]:
            with self.subTest(key=key):
                self.write(str(self.purpose.relative_to(self.root)), dict(self.purpose_data, **{key: value}))
                self.write(str(self.receipt.relative_to(self.root)), dict(self.receipt_data,
                           purpose_ruling_sha256=sha(self.purpose)))
                with self.assertRaisesRegex(ValueError, 'V6.2 purpose acceptance'):
                    self.gate()

    def test_changed_card_and_calibration_card_are_refused(self):
        self.cal.contract['card_sha256'] = 'other'
        with self.assertRaisesRegex(ValueError, 'cards differ'):
            self.gate()
        self.cal.contract['card_sha256'] = self.fresh.contract['card_sha256']
        self.card.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Fresh card binding'):
            self.gate()

    def test_direction_and_operational_clock_evidence_cannot_be_missing(self):
        for key, value in [('direction_match', False), ('official_operational_date', '2001-01-01')]:
            with self.subTest(key=key):
                answer = json.loads(json.dumps(self.answer))
                answer['checks'][0][key] = value
                self.write(str(self.output.relative_to(self.root)), answer)
                self.write(str(self.receipt.relative_to(self.root)), dict(self.receipt_data,
                           calibration_output_sha256=sha(self.output)))
                with self.assertRaises(ValueError):
                    self.gate()

    def test_unaudited_policy_needs_separate_direction_receipt(self):
        self.fresh.contract['direction_gate'] = 'UNAUDITED_PENDING_DIRECTION_CALIBRATION'
        with self.assertRaises(FileNotFoundError):
            self.gate()

    def test_never_treated_checks_do_not_contribute_direction_evidence(self):
        answer=json.loads(json.dumps(self.answer))
        for row in answer['checks'][4:]:
            row.update(status='unresolved', class_a_offset=False, direction_match=None,
                       panel_year=None, official_operational_calendar_year=None,
                       official_operational_date=None)
            treatment=next(t for t in self.cal.contract['sampled_tuples'] if t['unit']==row['unit'])
            treatment.update(status='never_treated', start_period=None)
        self.write(str(self.output.relative_to(self.root)), answer)
        self.write(str(self.receipt.relative_to(self.root)), dict(self.receipt_data,
                   calibration_output_sha256=sha(self.output)))
        self.gate()
        answer['checks'][0].update(status='unresolved', direction_match=None)
        self.write(str(self.output.relative_to(self.root)), answer)
        self.write(str(self.receipt.relative_to(self.root)), dict(self.receipt_data,
                   calibration_output_sha256=sha(self.output)))
        with self.assertRaisesRegex(ValueError, 'direction evidence'):
            self.gate()

    def test_unknown_selector_and_empty_manifest_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Unknown intake'):
            fresh_gate(self.root, ['missing'], validate_audit_clock=validate_audit_clock)
        self.write('JOBS_MANIFEST.json', dict(jobs=[]))
        with self.assertRaisesRegex(ValueError, 'No manifest-bound'):
            self.gate()


if __name__ == '__main__':
    unittest.main()
