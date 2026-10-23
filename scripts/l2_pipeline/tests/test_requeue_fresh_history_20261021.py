"""Opt-in `fresh_history_rule: R5` on requeue: one fresh history attempt for a RECONCILE_FLOOR park. Synthetic data only."""
import unittest
from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.requeue import requeue
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests import test_c46

FLOOR = 'V7 retained treated below minimum: 3 < 6'


class FreshHistoryTests(unittest.TestCase):
    def setUp(self):
        test_c46.RequeueTests.setUp(self)      # one design stopped at 'purpose', retained history/reconcile/audit, a v2 card and a ruling

    def park(self, reason=FLOOR, action='reconcile'):
        """Turn the fixture's STOP into a PARK of the current attempt (temp directory only)."""
        (self.pilot.case_dir(self.row) / 'STOP.json').unlink()
        ensure_once(self.pilot.case_dir(self.row) / 'PARKED.json', dict(design_id=self.row['design_id'], attempt_id='old', action=action,
                                                                        status='PARKED', reason=reason, metrics={}))

    def go(self, **extra):
        atomic(self.ruling, dict(self.value, decision='resume', **extra))
        return requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)

    def new_root(self):
        return self.pilot.backend.root(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0])

    def test_field_absent_keeps_the_history_as_before(self):
        self.park()
        value = self.go()
        self.assertEqual(value['retained_actions'], ['history', 'reconcile', 'audit'])
        self.assertNotIn('fresh_history', value)
        self.assertTrue((self.new_root() / 'RECONCILIATION.json').exists())

    def test_floor_park_restarts_history_and_keeps_nothing(self):
        self.park()
        old_jobs = self.pilot.case_dir(self.row) / 'jobs/history.json'
        ensure_once(old_jobs, dict(jobs=['/old/A.md', '/old/B.md']))
        value = self.go(fresh_history_rule='R5')
        self.assertEqual(value['restart_action'], 'history')
        self.assertEqual(value['retained_actions'], [])
        self.assertTrue(value['fresh_history'])
        row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual(row['attempt_id'], 'requeue_v2')
        self.assertEqual(self.pilot.next_action(row), 'history')
        for name in ('RECONCILIATION.json', 'CODING_LOCK.json', 'AUDIT_SELECTION.json', 'C42_PREPARATION.json'):
            self.assertFalse((self.new_root() / name).exists(), name)
        for action in ('history', 'reconcile', 'audit'):
            self.assertFalse(self.pilot.checkpoint(row, action).exists())
            self.assertFalse((self.pilot.case_dir(row) / 'jobs' / (action + '.json')).exists())
        self.assertEqual(read(self.registry / 'HUB_QUEUE.json')['entries'][0]['status'], 'SUPERSEDED_C46')
        self.assertTrue(old_jobs.exists())                                   # the old attempt is untouched
        self.assertTrue((self.registry.parent / 'autopilot/requeues' / self.card['design_id'] / 'v2/REQUEUE.json').exists())
        self.assertEqual(value, requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot))      # idempotent

    def test_pinned_history_jobs_do_not_follow_the_row(self):
        self.park()
        row = dict(self.row, history_jobs=['/old/A.md', '/old/B.md'])
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        self.go(fresh_history_rule='R5')
        self.assertNotIn('history_jobs', read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0])

    def test_stop_or_other_reason_or_action_or_value_is_refused_without_mutation(self):
        before = read(self.registry / 'ACTIVE_DESIGNS.json')
        with self.assertRaisesRegex(ValueError, 'RECONCILE_FLOOR'):          # the fixture is a STOP
            self.go(fresh_history_rule='R5')
        self.park('V7 something else')
        with self.assertRaisesRegex(ValueError, 'RECONCILE_FLOOR'):
            self.go(fresh_history_rule='R5')
        with self.assertRaisesRegex(ValueError, 'RECONCILE_FLOOR'):
            (self.pilot.case_dir(self.row) / 'PARKED.json').unlink()
            ensure_once(self.pilot.case_dir(self.row) / 'PARKED.json', dict(action='audit', status='PARKED', reason=FLOOR))
            self.go(fresh_history_rule='R5')
        (self.pilot.case_dir(self.row) / 'PARKED.json').unlink()
        ensure_once(self.pilot.case_dir(self.row) / 'PARKED.json', dict(action='reconcile', status='PARKED', reason=FLOOR))
        with self.assertRaisesRegex(ValueError, 'Invalid fresh_history_rule'):
            self.go(fresh_history_rule='R4')
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json'), before)

    def test_accept_is_refused(self):
        self.park()
        atomic(self.ruling, dict(self.value, decision='accept', fresh_history_rule='R5'))
        with self.assertRaisesRegex(ValueError, 'purpose acceptance'):
            requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)

    def test_frozen_is_still_refused(self):
        self.park()
        ensure_once(self.pilot.backend.root(self.row) / 'FREEZE_PREP.json', dict(fixture=True))
        with self.assertRaisesRegex(ValueError, 'Frozen'):
            self.go(fresh_history_rule='R5')
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'], [self.row])


if __name__ == '__main__':
    unittest.main()
