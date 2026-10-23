"""Window 2026-10-17: INTAKE_PANEL Q2 (Q1 rule once on the post-window S3 parks) and Q4 (12 history reopens). Synthetic data only."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, read
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests import test_named_panel_20261016 as q1tests


WINDOW_OPEN = '2026-10-17T07:23:00+00:00'  # what begin_code_task writes as the lock's 'time'


def lock(registry, task, time=WINDOW_OPEN):
    (registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task=task, time=time)))


class Q2Tests(unittest.TestCase):
    def setUp(self):
        q1tests.Q1RequeueTests.setUp(self)  # fixture only; its own tests are not inherited
        from scripts.l2_pipeline import named_panel as n
        self.n = n
        (self.repo / n.Q2_RULING).write_text('fixture INTAKE_PANEL ruling')
        lock(self.registry, n.Q2_TASK)

    def park_event(self, stamp, design='q1-fixture', attempt='requeue_v3'):
        self.pilot.ledger({}, 'S3', 'PARK', 'exclude')  # a real ledger line, then overwrite its identity and time
        path = self.registry / 'AUTOPILOT_LEDGER.jsonl'
        lines = path.read_text().splitlines()
        event = json.loads(lines[-1])
        event.update(design_id=design, attempt_id=attempt, time=stamp)
        lines[-1] = json.dumps(event)
        path.write_text('\n'.join(lines) + '\n')

    def test_scope_is_the_s3_parks_filed_after_the_q1_window_and_up_to_the_window_open_time(self):
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            self.park_event('2026-10-16T06:04:55+00:00')                   # before the Q1 window: Q1 saw it
            self.assertEqual(self.n.q2_rows(self.pilot), [])
            self.park_event('2026-10-16T09:18:25+00:00')
            self.assertEqual(self.n.q2_rows(self.pilot), [self.row])
            self.park_event('2026-10-16T23:00:00+00:00')                   # after the old 21:00Z cut, before the window opened: inside
            self.assertEqual(self.n.q2_rows(self.pilot), [self.row])
            self.park_event('2026-10-17T07:30:00+00:00')                   # after the window opened: outside
            self.assertEqual(self.n.q2_rows(self.pilot), [])
            lock(self.registry, self.n.Q2_TASK, '2026-10-17T08:00:00+00:00')  # the cut is the lock's open time, nothing fixed
            self.assertEqual(self.n.q2_rows(self.pilot), [self.row])

    def test_the_cut_needs_the_window_lock_but_a_preview_may_name_its_own_time(self):
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            self.park_event('2026-10-16T23:00:00+00:00')
            lock(self.registry, 'OTHER')
            with self.assertRaisesRegex(ValueError, 'open time required'):
                self.n.q2_rows(self.pilot)
            (self.registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task=self.n.Q2_TASK)))  # no time recorded
            with self.assertRaisesRegex(ValueError, 'open time required'):
                self.n.q2_rows(self.pilot)
            self.assertEqual(self.n.q2_rows(self.pilot, until='2026-10-17T00:00:00+00:00'), [self.row])
            self.assertEqual(self.n.q2_rows(self.pilot, until='2026-10-16T21:00:00+00:00'), [])

    def test_a_park_without_an_s3_event_is_not_in_scope(self):
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            self.assertEqual(self.n.q2_rows(self.pilot), [])

    def test_apply_needs_its_window_records_the_ruling_first_requeues_once_and_is_idempotent(self):
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            self.park_event('2026-10-16T09:18:25+00:00')
            lock(self.registry, 'OTHER')
            with self.assertRaisesRegex(ValueError, 'stopped code window'):
                self.n.apply_q2(self.pilot)
            lock(self.registry, self.n.Q2_TASK)
            value = self.n.apply_q2(self.pilot)
            self.assertEqual([r['restart'] for r in value['requeued']], ['purpose-direction'])
            self.assertEqual(value['dispatches'], 0)
            self.assertEqual(read(self.repo / 'data/l2_pipeline/autopilot/named_panel/q2/CLASS_RULING.json')['scope_frozen_at'], WINDOW_OPEN)
            ledger = [json.loads(l) for l in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
            kinds = [e['decision'] for e in ledger]
            self.assertLess(kinds.index('RULING_RECORDED'), kinds.index('SUPERSEDED_NAMED_PANEL_Q1'))
            self.assertEqual(self.n.apply_q2(self.pilot), value)  # second call returns the receipt; nothing runs twice
        row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual(row['attempt_id'], 'requeue_v4')
        card = read(row['card'])
        self.assertEqual(card['automation']['named_panel_q1']['last_support_rescue'], True)
        self.assertEqual(sorted(card['treatment']['excluded_units']), ['AL'])

    def test_a_design_whose_floors_fail_stays_stopped_and_is_exported(self):
        self.plan.update(decision='stay_stopped', stay_stopped_because=['fewer than 10 treated states retained'])
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            self.park_event('2026-10-16T09:18:25+00:00')
            value = self.n.apply_q2(self.pilot)
        self.assertEqual(value['requeued'], [])
        self.assertEqual(value['stayed_stopped'], ['q1-fixture'])
        out = self.repo / 'data/l2_pipeline/autopilot/named_panel/q2/exports/q1-fixture.json'
        self.assertEqual(read(out)['requeued'], False)
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]['attempt_id'], 'requeue_v3')


class Q4Tests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.tests import test_c46
        from scripts.l2_pipeline import intake_panel_q4 as q4
        self.q4 = q4
        test_c46.RequeueTests.setUp(self)  # one design stopped at 'purpose' (not in the class)
        self.reopen_id, self.final_id = q4.REOPEN[0], q4.STAY_TERMINAL[0]
        rows = read(self.registry / 'ACTIVE_DESIGNS.json')['designs']
        self.extra = []
        for design in (self.reopen_id, self.final_id):
            card = dict(self.card, design_id=design)
            path = self.repo / 'data/l2_pipeline/cards' / (design + '.json')
            atomic(path, card)
            row = dict(self.row, design_id=design, card=str(path), card_sha256=sha(path))
            self.extra.append(row)
            ensure_once(self.pilot.case_dir(row) / 'PARKED.json', dict(design_id=design, attempt_id='old', action='history', status='PARKED',
                        reason='INSUFFICIENT_WORK: completion incomplete or invalid; final'))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=rows + self.extra))
        (self.repo / q4.RULING).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / q4.RULING).write_text('fixture INTAKE_PANEL ruling')
        lock(self.registry, q4.TASK)

    def test_class_is_the_fourteen_minus_the_two_that_stay_terminal(self):
        self.assertEqual(len(self.q4.CLASS), 14)
        self.assertEqual(len(self.q4.REOPEN), 12)
        self.assertEqual(set(self.q4.CLASS) - set(self.q4.REOPEN), {'b1011c_power_sector_co2_cap', 'b1012cr_arrestee_dna_collection'})

    def test_only_a_parked_history_design_of_the_twelve_is_reopened_with_the_new_status(self):
        done = self.q4.apply(self.pilot)
        self.assertEqual([d['design_id'] for d in done], [self.reopen_id])
        self.assertEqual(done[0]['restart_action'], 'history')
        self.assertEqual(done[0]['retained_actions'], [])
        statuses = {e['design_id']: e['status'] for e in read(self.registry / 'HUB_QUEUE.json')['entries']}
        self.assertNotIn(self.final_id, statuses)
        rows = {r['design_id']: r for r in read(self.registry / 'ACTIVE_DESIGNS.json')['designs']}
        self.assertEqual(rows[self.reopen_id]['attempt_id'], 'requeue_v2')
        self.assertEqual(rows[self.final_id]['attempt_id'], 'old')                 # arrestee_dna / power_sector stay terminal
        self.assertEqual(self.q4.apply(self.pilot), [])                              # idempotent
        self.assertTrue((self.pilot.case_dir(self.extra[0]) / 'PARKED.json').exists())  # the old park marker is untouched

    def test_needs_its_window(self):
        lock(self.registry, 'OTHER')
        with self.assertRaisesRegex(ValueError, 'stopped code window'):
            self.q4.apply(self.pilot)

    def test_status_is_in_the_requeue_whitelist_and_lands_on_the_hub_queue_entry(self):
        from scripts.l2_pipeline.requeue import requeue
        atomic(self.ruling, dict(self.value, decision='resume', superseded_status='SUPERSEDED_INTAKE_PANEL_Q4'))
        requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(read(self.registry / 'HUB_QUEUE.json')['entries'][0]['status'], 'SUPERSEDED_INTAKE_PANEL_Q4')


if __name__ == '__main__':
    unittest.main()
