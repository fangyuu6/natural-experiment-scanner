"""T202 item 4: append-only idempotent final-decision ledger writer (invented inputs, temp registry)."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from scripts.l2_pipeline import final_decisions as fd

OLD = dict(design_id='old_design', attempt='autopilot_v1', decision='NOT_EVALUABLE', gate='S3_COUNT_FLOOR',
           basis='b', state_at_closure='PARK', park_time_utc='2026-10-10T00:00Z', source='s',
           recorded_utc='2026-10-20T07:33:36Z')


def row(design='new_design', gate='S3_COUNT_FLOOR', **kw):
    return dict(dict(design_id=design, attempt='requeue_v4', decision='NOT_EVALUABLE', gate=gate,
                     basis='S3 support: x', state_at_closure='STOP', park_time_utc='2026-10-17T07:51Z',
                     source='HUB_RULING_20261020_R3R4_CARDS.md'), **kw)


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reg = Path(self.tmp.name)
        value = dict(schema='l2_final_decisions_v1', ledger='FINAL_DECISIONS_20261020', recorded_utc='x', task='T181',
                     note='n', by_gate={'S3_COUNT_FLOOR': 1}, count=1, designs=[OLD])
        self.path = self.reg / fd.LEDGER
        self.path.write_text(json.dumps(value, indent=1, ensure_ascii=False))
        self.raw = self.path.read_bytes()
        self.now = datetime(2026, 10, 21, 7, 30, tzinfo=timezone.utc)

    def run_append(self, rows, **kw):
        return fd.append(rows, self.reg, task='T202x', now=self.now, **kw)

    def test_dry_run_default_writes_nothing(self):
        result = self.run_append([row()])
        self.assertEqual(result['new'], ['new_design'])
        self.assertFalse(result['written'])
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(sorted(p.name for p in self.reg.iterdir()), [fd.LEDGER])

    def test_apply_appends_keeps_old_rows_and_updates_counts(self):
        result = self.run_append([row(), row('other', 'RI_FEASIBILITY_0.90')], apply=True)
        self.assertTrue(result['written'])
        value = json.loads(self.path.read_text())
        self.assertEqual(value['designs'][0], OLD)
        self.assertEqual([r['design_id'] for r in value['designs']], ['old_design', 'new_design', 'other'])
        self.assertEqual(value['count'], 3)
        self.assertEqual(value['by_gate'], {'S3_COUNT_FLOOR': 2, 'RI_FEASIBILITY_0.90': 1})
        self.assertEqual(value['designs'][1]['recorded_utc'], '2026-10-21T07:30:00Z')
        before = Path(result['before_image'])
        self.assertEqual(before.read_bytes(), self.raw)
        log = (self.reg / (fd.LEDGER + '.appends.jsonl')).read_text().splitlines()
        self.assertEqual(json.loads(log[0])['before_sha256'], hashlib.sha256(self.raw).hexdigest())

    def test_idempotent(self):
        self.run_append([row()], apply=True)
        after = self.path.read_bytes()
        again = self.run_append([row()], apply=True)
        self.assertFalse(again['written'])
        self.assertEqual(again['present'], ['new_design'])
        self.assertEqual(self.path.read_bytes(), after)

    def test_unapproved_gate_refused(self):
        with self.assertRaisesRegex(ValueError, 'approved class table'):
            self.run_append([row(gate='MY_NEW_GATE')], apply=True)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_bad_rows_refused(self):
        for bad in (row(decision='FINISHED'), row(state_at_closure='RUNNING'), row(basis=' '), row(design='../x'),
                    {k: v for k, v in row().items() if k != 'gate'}, row(extra_field=1)):
            with self.assertRaises(ValueError):
                self.run_append([bad], apply=True)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_conflicting_gate_for_same_design_refused(self):
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.run_append([dict(OLD, gate='S3_SERIES_HOLES')], apply=True)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_duplicate_in_request_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.run_append([row(), row()], apply=True)

    def test_existing_before_image_never_overwritten(self):
        (self.reg / (fd.LEDGER + '.before_T202x')).write_text('keep')
        with self.assertRaisesRegex(ValueError, 'before-image'):
            self.run_append([row()], apply=True)
        self.assertEqual((self.reg / (fd.LEDGER + '.before_T202x')).read_text(), 'keep')
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_noop_roundtrip_is_byte_identical_format(self):
        value = json.loads(self.raw)
        self.assertEqual(json.dumps(value, indent=1, ensure_ascii=False).encode(), self.raw)

    def test_live_ledger_format_roundtrips(self):
        live = Path(__file__).resolve().parents[3] / 'data/l2_pipeline/registry' / fd.LEDGER
        if not live.is_file():
            self.skipTest('no live ledger')
        raw = live.read_bytes()
        self.assertEqual(json.dumps(json.loads(raw), indent=1, ensure_ascii=False).encode(), raw)
        for r in json.loads(raw)['designs']:
            fd.validate({k: v for k, v in r.items()})


if __name__ == '__main__':
    unittest.main()
