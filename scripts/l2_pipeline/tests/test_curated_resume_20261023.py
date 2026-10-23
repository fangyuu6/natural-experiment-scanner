"""T287 Q5 (HUB_RULING_20261023_T287_CLOSURES): a later attempt of b1020rn_state_preemption_punitive may reuse the SAME open ledger claim of a first
audit that never produced a verdict. Synthetic repo and data only; no outcome, estimate or forecast value exists in any fixture."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import curated_history as ch, curated_route as cr
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_curated_route_20261021 import Fixture


class ResumeBase(Fixture):
    def setUp(self):
        super().setUp()
        self.design = self.card['design_id']
        p = patch.object(cr, 'RESUME_DESIGNS', frozenset({self.design}))
        p.start()
        self.addCleanup(p.stop)
        self.row, self.payload = self.declare()
        for q in self.fake_xlsx():
            q.start()
            self.addCleanup(q.stop)
        self.ruling = self.repo / cr.RESUME_RULING
        self.ruling.parent.mkdir(parents=True, exist_ok=True)
        self.ruling.write_text('synthetic T287 ruling')
        self.first = cr.prepare(self.pilot, self.row)                       # first attempt: claims the ledger, never gets a verdict
        self.new_row = self.later(self.row, 'requeue_v3', 'v10')

    def later(self, row, attempt, version):
        from scripts.l2_pipeline.autopilot import atomic
        payload = dict(deepcopy(self.payload), version=version)
        path = self.repo / ('data/l2_pipeline/cards/resume_%s.json' % version)
        atomic(path, payload)
        return dict(row, card=str(path), card_sha256=sha(path), attempt_id=attempt)

    def ledger_path(self):
        return next((self.repo / cr.LEDGER_DIR).glob('*.json'))

    def build(self, attempt='requeue_v3'):
        return cr.build_resume_receipt(self.pilot, self.payload, self.row['attempt_id'], self.pilot.backend.root(self.row),
                                       self.pilot.case_dir(self.row), attempt)

    def receipt(self, **changes):
        value = self.build()
        value.update(changes)
        return ensure_once(cr.resume_path(self.repo, self.design), value)


class ResumeTests(ResumeBase):
    def test_without_a_receipt_the_second_attempt_still_raises_no_second_draw(self):
        with self.assertRaisesRegex(ValueError, 'no second draw'):
            cr.prepare(self.pilot, self.new_row)

    def test_with_the_receipt_the_same_audit_is_rebuilt_and_the_ledger_is_unchanged(self):
        self.receipt()
        before = sha(self.ledger_path())
        build = cr.prepare(self.pilot, self.new_row)
        self.assertEqual((build['plan']['sample'], build['plan']['seed']), (self.first['plan']['sample'], self.first['plan']['seed']))
        self.assertEqual(build['mapping_sha256'], self.first['mapping_sha256'])
        self.assertEqual(sha(self.ledger_path()), before)                 # no second claim appended, no result written
        self.assertEqual(len(json.loads(self.ledger_path().read_text())['audits']), 1)
        self.assertEqual(cr.prepare(self.pilot, self.new_row), build)     # idempotent

    def test_receipt_is_bound_to_one_attempt(self):
        self.receipt()
        with self.assertRaisesRegex(ValueError, 'no second draw'):
            cr.prepare(self.pilot, self.later(self.row, 'requeue_v4', 'v11'))

    def test_a_design_outside_the_list_never_resumes(self):
        self.receipt()
        with patch.object(cr, 'RESUME_DESIGNS', frozenset({'someone_else'})):
            with self.assertRaisesRegex(ValueError, 'no second draw'):
                cr.prepare(self.pilot, self.new_row)
            with self.assertRaisesRegex(ValueError, 'outside the ruling list'):
                self.build()

    def test_receipt_with_a_changed_ruling_file_is_ignored(self):
        self.receipt()
        self.ruling.write_text('changed after the receipt')
        with self.assertRaisesRegex(ValueError, 'no second draw'):
            cr.prepare(self.pilot, self.new_row)

    def test_a_different_plan_in_the_receipt_is_refused(self):
        value = self.build()
        value['first_audit'] = dict(value['first_audit'], seed=value['first_audit']['seed'] + 1)
        ensure_once(cr.resume_path(self.repo, self.design), value)
        with self.assertRaisesRegex(ValueError, 'CURATED_RETRY_MISMATCH'):
            cr.prepare(self.pilot, self.new_row)

    def test_a_recorded_result_closes_the_resume(self):
        state = json.loads(self.ledger_path().read_text())
        state['audits'][0]['result'] = 'fail'
        self.ledger_path().write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'already has a result'):
            self.build()

    def test_a_verdict_file_of_the_first_attempt_blocks_the_receipt(self):
        root = self.pilot.backend.root(self.row)
        ensure_once(cr._verdict_path(root), dict(verdict=dict(verdict='fail')))
        with self.assertRaisesRegex(ValueError, 'produced a verdict'):
            self.build()

    def test_resume_claim_needs_the_same_design(self):
        spec = self.spec
        with self.assertRaisesRegex(ValueError, 'another design'):
            ch.resume_claim(self.repo / cr.LEDGER_DIR, spec, spec['universe'], 'other_design')
        ch.resume_claim(self.repo / cr.LEDGER_DIR, spec, spec['universe'], self.design)
        with self.assertRaisesRegex(ValueError, 'No ledger claim'):
            ch.resume_claim(self.repo / 'nowhere', spec, spec['universe'], self.design)


if __name__ == '__main__':
    unittest.main()
