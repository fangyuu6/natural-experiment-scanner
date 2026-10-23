"""T202 item 2: per-reason tick blocker breakdown and STARVED_BLOCKED note (logging only; invented inputs)."""
import json
import unittest
from scripts.l2_pipeline.tests.test_autopilot import AutopilotTests as BasePilot
from scripts.l2_pipeline.autopilot import Autopilot, atomic, read
from scripts.l2_pipeline.storage import ensure_once
from scripts.l2_pipeline import tick_blockers as tb


class ClassifyTests(unittest.TestCase):
    def test_labels(self):
        c = tb.classify
        self.assertEqual(c(dict(next='FINISHED')), 'FINISHED')
        self.assertEqual(c(dict(next='GATE0_PARK')), 'GATE0_PARK')
        self.assertEqual(c(dict(next='draft')), 'RUNNABLE')
        self.assertEqual(c(dict(next='STOP', reason='ValueError: NEEDS_RULING: NOVEL/science finding requires explicit validated hub closure')), 'NOVEL_GATE')
        self.assertEqual(c(dict(next='STOP', reason='ValueError: NEEDS_RULING: review SHOULD requires evidence-backed hub closure')), 'NOVEL_GATE')
        self.assertEqual(c(dict(next='STOP', reason='Missing dependency: x')), 'PREP_DEPENDENCY')
        self.assertEqual(c(dict(next='PARK', reason='QUEUE_PARK: INSUFFICIENT_WORK (queue event, reopenable): Sonnet lease expired three times')), 'QUEUE_PARK')
        self.assertEqual(c(dict(next='PARK', reason='INSUFFICIENT_WORK: completion incomplete or invalid; final')), 'HISTORY_FINAL')
        self.assertEqual(c(dict(next='PARK', reason='S3 Disposition.STOPPED: Registered support insufficient')), 'SUPPORT_OR_RI_GATE')
        self.assertEqual(c(dict(next='PARK', reason='S2 Disposition.PARKED: No local public files declared in outcome.paths')), 'CARD_BOUND_S0_S2')
        self.assertEqual(c(dict(next='STOP', reason='something new')), 'STOP_OTHER')
        self.assertEqual(c(dict(next='PARK')), 'PARK_OTHER')

    def test_closed_design_is_its_own_label(self):
        self.assertEqual(tb.classify(dict(design_id='d', next='PARK', reason='INSUFFICIENT_WORK'), {'d'}), 'CLOSED_FINAL')

    def test_breakdown_counts_sum_to_report(self):
        report = [dict(design_id='a', next='PARK', reason='INSUFFICIENT_WORK'), dict(design_id='b', next='FINISHED'),
                  dict(design_id='c', next='STOP', reason='Missing dependency')]
        counts, members = tb.breakdown(report)
        self.assertEqual(sum(counts.values()), 3)
        self.assertEqual(members['HISTORY_FINAL'], ['a'])


class TickTests(unittest.TestCase):
    setUp = BasePilot.setUp
    seed = BasePilot.seed

    def rows(self):
        path = self.registry / 'AUTOPILOT_LEDGER.jsonl'
        return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []

    def park(self, reason):
        self.seed(1)
        row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        ensure_once(self.pilot.case_dir(row) / 'PARKED.json', dict(reason=reason))
        return row

    def test_idle_row_keeps_blockers_and_adds_reasons(self):
        self.park('INSUFFICIENT_WORK: completion incomplete or invalid; final')
        self.pilot.tick()
        idle = [r for r in self.rows() if r['decision'] == 'IDLE']
        self.assertEqual(len(idle), 1)
        self.assertEqual(idle[0]['blockers'], {'PARK': 1})
        self.assertEqual(sum(idle[0]['blocker_reasons'].values()), 1)
        self.assertIn('blocker_reasons', idle[0])

    def test_starved_blocked_written_once_with_top_reason(self):
        self.park('INSUFFICIENT_WORK: completion incomplete or invalid; final')
        for _ in range(3):
            self.pilot.tick()
        rows = [r for r in self.rows() if r['decision'] == 'STARVED_BLOCKED']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['top_count'], 1)
        self.assertEqual(rows[0]['top_reason'], max(rows[0]['blocker_reasons'], key=rows[0]['blocker_reasons'].get))
        self.assertEqual(rows[0]['top_designs'], [self.pilot_row_id()])

    def pilot_row_id(self):
        return read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]['design_id']

    def test_no_starved_row_when_queue_has_pending_packet(self):
        self.park('offline park')
        from unittest.mock import patch
        with patch.object(tb, 'queue_counts', return_value=(1, 0)):
            self.pilot.tick()
        self.assertFalse(any(r['decision'] == 'STARVED_BLOCKED' for r in self.rows()))
        self.assertTrue(any(r['decision'] == 'IDLE' for r in self.rows()))

    def test_dry_run_writes_nothing(self):
        self.park('offline park')
        before = len(self.rows())
        self.pilot.tick(dry_run=True)
        self.assertEqual(len(self.rows()), before)

    def test_logging_failure_never_breaks_tick(self):
        self.park('offline park')
        from unittest.mock import patch
        with patch.object(tb, 'breakdown', side_effect=RuntimeError('boom')):
            self.pilot.tick()
        idle = [r for r in self.rows() if r['decision'] == 'IDLE']
        self.assertEqual(idle[0]['blockers'], {'PARK': 1})
        self.assertNotIn('blocker_reasons', idle[0])
        self.assertFalse(any(r['decision'] == 'STARVED_BLOCKED' for r in self.rows()))

    def test_closed_final_design_is_not_a_top_reason(self):
        row = self.park('offline park')
        atomic(self.registry / 'FINAL_DECISIONS_20261020.json', dict(designs=[dict(design_id=row['design_id'])]))
        self.pilot.tick()
        idle = [r for r in self.rows() if r['decision'] == 'IDLE'][0]
        self.assertEqual(idle['blocker_reasons'], {'CLOSED_FINAL': 1})
        self.assertFalse(any(r['decision'] == 'STARVED_BLOCKED' for r in self.rows()))

    def test_watchdog_idle_proof_inputs_unchanged(self):
        # idle_proof compares note['blockers'] with LOOP_STATE next_counts; the new key must not disturb that.
        self.park('offline park')
        self.pilot.tick()
        idle = [r for r in self.rows() if r['decision'] == 'IDLE'][0]
        self.assertEqual(idle['blockers'], read(self.registry / 'LOOP_STATE.json')['next_counts'])


del BasePilot
