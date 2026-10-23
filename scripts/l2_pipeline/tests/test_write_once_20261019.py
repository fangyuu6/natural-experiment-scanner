"""Invented, outcome-free tests of the 2026-10-19 write-once sweep (WRITE_ONCE_SWEEP_20261019.md).

A guard that refuses to write a file again with other bytes is right; what went wrong is that a path keyed by the design (or a
record derived again from state that moved on) is met by an attempt opened later by a requeue. These tests pin: the
contract-replacement slot and the S0 purpose packet are no longer shared between attempts (old files read as written, never
overwritten); a write-once guard failure of an unfrozen attempt is a reopenable PARK, not a STOP; a frozen attempt keeps its STOP.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline import redesign, release_recovery
try:
    from scripts.l2_pipeline import write_once
except ImportError:   # before the fix this module does not exist: the tests then fail one by one instead of the file failing as a whole
    write_once = None
from scripts.l2_pipeline.agents.contract_tests import golden_output
from scripts.l2_pipeline.agents.contract_tests.packets import _context
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.agents.purpose import emit_purpose_verification
from scripts.l2_pipeline.agents.replacement import ContractRejection
from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, read
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import ensure_once, sha, write_once as write_json_once
from scripts.l2_pipeline.tests import test_c45, test_redesign_w1 as w1
from scripts.l2_pipeline.tests.test_pipeline import card as pipeline_card

REPO = Path(__file__).resolve().parents[3]


def sha_tree(root):
    return {str(p): sha(p) for p in sorted(Path(root).rglob('*')) if p.is_file()}


class SlotUnit(unittest.TestCase):
    """write_once.replacement_slot on invented slot records."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def put(self, slot, name, **value):
        Path(slot).mkdir(parents=True, exist_ok=True)
        atomic(Path(slot) / name, value)

    def test_fresh_design_keeps_the_layout_of_every_old_slot(self):
        self.assertEqual(write_once.replacement_slot(self.dir, 'd', 'a1', 'A', '/jobs/a1/A.md'), self.dir / 'replacements/d/A')

    def test_a_slot_that_names_the_job_wins_so_old_slots_are_read_as_written(self):
        legacy = self.dir / 'replacements/d/A'
        self.put(legacy, 'INTENT.json', original_job='/jobs/a1/A.md')
        self.put(legacy, 'REPLACEMENT.json', original_job='/jobs/a1/A.md', replacement_job='/jobs/a1/A.c45_contract_x.md')
        for named in ('/jobs/a1/A.md', '/jobs/a1/A.c45_contract_x.md'):
            self.assertEqual(write_once.replacement_slot(self.dir, 'd', 'a2', 'A', named), legacy)   # even for a later attempt (retained inventory)

    def test_another_job_of_the_design_gets_a_slot_per_attempt(self):
        legacy = self.dir / 'replacements/d/A'
        self.put(legacy, 'INTENT.json', original_job='/jobs/a1/A.md')
        self.put(legacy, 'REPLACEMENT.json', original_job='/jobs/a1/A.md', replacement_job='/jobs/a1/A.c45_contract_x.md')
        keyed = write_once.replacement_slot(self.dir, 'd', 'requeue_v2', 'A', '/jobs/a2/A.md')
        self.assertEqual(keyed, self.dir / 'replacements/d/attempts/requeue_v2/A')
        self.put(keyed, 'REPLACEMENT.json', original_job='/jobs/a2/A.md', replacement_job='/jobs/a2/A.c45_contract_y.md')
        for named in ('/jobs/a2/A.md', '/jobs/a2/A.c45_contract_y.md'):
            self.assertEqual(write_once.replacement_slot(self.dir, 'd', 'requeue_v2', 'A', named), keyed)
        self.assertEqual(write_once.replacement_slot(self.dir, 'd', 'requeue_v3', 'A', '/jobs/a3/A.md'),
                         self.dir / 'replacements/d/attempts/requeue_v3/A')

    def test_roles_do_not_share_and_a_transport_repair_names_its_jobs(self):
        legacy = self.dir / 'replacements/d/A'
        self.put(legacy, 'TRANSPORT_REPAIR.json', original_job='/jobs/a1/A.md', failed_replacement_job='/jobs/a1/A.r1.md',
                 repaired_job=dict(path='/jobs/a1/A.r2.md'))
        self.assertEqual(write_once.replacement_slot(self.dir, 'd', 'a1', 'B', '/jobs/a1/B.md'), self.dir / 'replacements/d/B')
        self.assertEqual(write_once.replacement_slot(self.dir, 'd', 'a2', 'A', '/jobs/a1/A.r2.md'), legacy)

    def test_a_row_without_attempt_keeps_the_design_directory(self):
        legacy = self.dir / 'replacements/d/A'
        self.put(legacy, 'INTENT.json', original_job='/jobs/a1/A.md')
        self.assertEqual(write_once.replacement_slot(self.dir, 'd', None, 'A', '/jobs/other/A.md'), legacy)

    def test_the_guard_texts_are_the_write_once_ones_only(self):
        for text in ('ValueError: Existing preparation artifact changed: /x/CARD.json', 'Existing job changed: /x/A.md',
                     'S0 Disposition.FAILED: Existing purpose packet differs from card',
                     'Existing completion job is not bound to this original and gap: /x',
                     'Existing clean history predecessor changed'):
            self.assertTrue(write_once.matches(text), text)
        for text in ('Locked prediction changed', 'Frozen C83 addendum changed', 'NEEDS_RULING: intake card bytes changed',
                     'C42 original evidence/ruling changed: /x', 'Agent output already exists; no retry: /x', 'Snapshot bytes changed: a'):
            self.assertFalse(write_once.matches(text), text)


class ReplacementAttempts(unittest.TestCase):
    """The contract replacement of a role is per job lineage: a requeued attempt has its own (the C45 fixtures)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.registry = self.repo / 'data/l2_pipeline/registry'
        self.backend = test_c45.FakeHost(self.repo)
        self.pilot = Autopilot(self.registry, self.backend)
        self.first = dict(design_id='contract-fixture', attempt_id='original')
        self.second = dict(design_id='contract-fixture', attempt_id='requeue_v2')

    def pair(self, name):
        root = self.repo / name
        ctx = _context(root, 's1_coder')
        a = emit_job(ctx, 'S1', 'A', jobs_root=root / 'jobs')
        b = emit_job(ctx, 'S1', 'B', jobs_root=root / 'jobs')
        for job in (a, b):
            atomic(Path(job.output_path), golden_output(job))
        return a, b

    def slots(self):
        return self.registry.parent / 'autopilot/replacements/contract-fixture'

    def test_a_requeued_attempt_gets_its_own_replacement_and_the_old_slot_stays(self):
        a1, _ = self.pair('first')
        path1 = self.pilot.replace_submission(self.first, 'history', ContractRejection(a1.job_path, 'SCHEMA_FORMAT', 'invented format'))
        old = sha_tree(self.slots())
        self.assertTrue((self.slots() / 'A/REPLACEMENT.json').is_file())
        a2, _ = self.pair('second')      # the reopened attempt's own packet of the same role (its own path)
        path2 = self.pilot.replace_submission(self.second, 'history', ContractRejection(a2.job_path, 'SCHEMA_FORMAT', 'invented format'))
        self.assertNotEqual(path1, path2)
        self.assertTrue(Path(path2).is_relative_to(self.slots() / 'attempts/requeue_v2/A'))
        self.assertEqual({k: v for k, v in sha_tree(self.slots()).items() if k in old}, old)    # not a byte of the old slot moved
        self.assertEqual(self.pilot.effective_job(self.second, a2.job_path), path2)
        self.assertEqual(self.pilot.effective_job(self.first, a1.job_path), path1)

    def test_a_retained_job_of_the_new_attempt_keeps_the_lineage_of_the_slot_that_holds_it(self):
        a1, _ = self.pair('first')
        path1 = self.pilot.replace_submission(self.first, 'history', ContractRejection(a1.job_path, 'SCHEMA_FORMAT', 'invented format'))
        # requeue 'resume' retains the inventory of history/reconcile/audit: the new attempt names the old attempt's job
        self.assertEqual(self.pilot.effective_job(self.second, a1.job_path), path1)
        with self.assertRaisesRegex(ValueError, 'replacement failed'):    # one contract replacement per job lineage, as before
            self.pilot.replace_submission(self.second, 'history', ContractRejection(a1.job_path, 'SCHEMA_FORMAT', 'again'))

    def test_the_replacement_of_a_replacement_is_still_exhausted_in_the_new_slot(self):
        self.pair('first')
        a2, _ = self.pair('second')
        path2 = self.pilot.replace_submission(self.second, 'history', ContractRejection(a2.job_path, 'SCHEMA_FORMAT', 'invented format'))
        with self.assertRaisesRegex(ValueError, 'replacement failed'):
            self.pilot.replace_submission(self.second, 'history', ContractRejection(path2, 'SCHEMA_FORMAT', 'again'))

    def test_a_design_without_an_old_slot_keeps_the_old_layout(self):
        a1, _ = self.pair('first')
        path1 = self.pilot.replace_submission(self.first, 'history', ContractRejection(a1.job_path, 'SCHEMA_FORMAT', 'invented format'))
        self.assertTrue(Path(path1).is_relative_to(self.slots() / 'A'))
        self.assertFalse((self.slots() / 'attempts').exists())


def purpose_ctx(root, version, name=None, **changes):
    value = pipeline_card()
    value.update(automation={'intake': {'domain': 'Health'}}, version=version, **changes)
    value['first_order'].update(target_outcome='health_insurance_coverage')
    path = root / (name or 'card_' + version + '.json')
    write_json_once(path, value)
    return StageContext(load_card(path), root / 'work', REPO)


class PurposePacketVersions(unittest.TestCase):
    """autopilot/S0/<design>/purpose_verifier is written by S0 under a root that does not name the attempt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_a_later_version_gets_its_own_packet_and_the_old_bytes_stay(self):
        v1 = purpose_ctx(self.root, 'v1')
        old = emit_purpose_verification(v1, self.root / 'jobs')
        before = sha_tree(self.root / 'jobs')
        v2 = purpose_ctx(self.root, 'v2')
        new = emit_purpose_verification(v2, self.root / 'jobs')
        self.assertNotEqual(new.job_path, old.job_path)
        self.assertEqual((new.design_version, old.design_version), ('v2', 'v1'))
        self.assertTrue(Path(new.job_path).is_relative_to(self.root / 'jobs/S0' / new.design_id / 'versions/v2'))
        self.assertEqual({k: v for k, v in sha_tree(self.root / 'jobs').items() if k in before}, before)
        # both are read back as written, in either order, and the check of the loader still binds each to its card
        self.assertEqual(emit_purpose_verification(v2, self.root / 'jobs'), new)
        self.assertEqual(emit_purpose_verification(v1, self.root / 'jobs'), old)
        self.assertEqual(load_job(Path(new.job_path)).contract['card_sha256'], sha(v2.card.path))

    def test_the_same_version_with_other_card_bytes_is_still_refused(self):
        emit_purpose_verification(purpose_ctx(self.root, 'v1'), self.root / 'jobs')
        other = purpose_ctx(self.root, 'v1', name='card_v1_other.json', policy_family='another_family')   # same version, other bytes
        with self.assertRaisesRegex(ValueError, 'Existing purpose packet differs from card'):
            emit_purpose_verification(other, self.root / 'jobs')


class Tick(w1.Base):
    """A write-once guard failure raised inside a tick (the incident's STOP) becomes a reopenable PARK for an unfrozen attempt."""

    def run_tick(self, reason, freeze=False):
        self.publish()
        pinned = self.make_row('p1')
        legacy = self.make_row('r0', pinned=False)
        self.done(pinned, 'history', 'reconcile', 'screen', 'audit')
        self.done(legacy, 'history', 'reconcile', 'audit', 'purpose', 'purpose-direction')
        if freeze:
            for row in (pinned, legacy):
                root = self.pilot.backend.root(row)
                root.mkdir(parents=True, exist_ok=True)
                (root / 'FREEZE_PREP.json').write_text('{}')
        with patch.object(Backend, 'perform', side_effect=ValueError(reason)), patch('scripts.l2_pipeline.autopilot.Autopilot.schedule'):
            self.pilot.tick(max_steps=1)
        return pinned, legacy

    GUARD = 'Existing preparation artifact changed: /repo/data/l2_pipeline/autopilot/work/d/v4/CARD.json'

    def test_guard_failure_parks_pinned_and_legacy_rows_alike_and_release_recovery_lists_them(self):
        pinned, legacy = self.run_tick(self.GUARD)
        for row in (pinned, legacy):
            case = self.pilot.case_dir(row)
            self.assertFalse((case / 'STOP.json').exists())
            parked = read(case / 'PARKED.json')
            self.assertEqual(parked['metrics']['kind'], 'QUEUE_PARK')
            self.assertEqual(parked['metrics']['reopen'], 'release_recovery')
            self.assertEqual(parked['metrics']['outcome_values_read'], 0)
            self.assertTrue(parked['reason'].startswith(write_once.PARK_PREFIX))    # L3 counts INSUFFICIENT_WORK parks as not-counted work
            self.assertIn('CARD.json', parked['reason'])
            self.assertEqual(self.pilot.next_action(row), 'PARK')
        self.assertEqual(read(self.registry / 'HUB_QUEUE.json', dict(entries=[]))['entries'], [])
        self.assertTrue(any(r['decision'] == 'PARK_WRITE_ONCE' for r in self.ledger_rows()))
        candidates, kept = release_recovery.plan(self.pilot, include_parks=True)
        self.assertEqual(sorted(c['row']['design_id'] for c in candidates), ['p1', 'r0'])
        self.assertEqual(kept, [])

    def assert_parked(self, text):
        pinned, legacy = self.run_tick(text)
        for row in (pinned, legacy):
            self.assertTrue((self.pilot.case_dir(row) / 'PARKED.json').exists(), text)
            self.assertFalse((self.pilot.case_dir(row) / 'STOP.json').exists(), text)

    def test_the_other_tick_guard_texts_park_too(self):
        self.assert_parked('Existing job changed: /x/A.md')

    def test_a_stage_wrapped_purpose_packet_guard_parks(self):
        self.assert_parked('S0 Disposition.FAILED: Existing purpose packet differs from card')

    def test_the_clean_history_predecessor_guard_parks(self):
        self.assert_parked('Existing clean history predecessor changed')

    def test_a_frozen_attempt_keeps_its_stop(self):
        pinned, legacy = self.run_tick(self.GUARD, freeze=True)
        for row in (pinned, legacy):
            self.assertTrue((self.pilot.case_dir(row) / 'STOP.json').exists())
            self.assertFalse((self.pilot.case_dir(row) / 'PARKED.json').exists())

    def assert_stopped(self, text):
        pinned, legacy = self.run_tick(text)
        self.assertTrue((self.pilot.case_dir(legacy) / 'STOP.json').exists(), text)
        self.assertFalse((self.pilot.case_dir(legacy) / 'PARKED.json').exists(), text)

    def test_a_locked_prediction_changing_keeps_its_stop(self):
        self.assert_stopped('Locked prediction changed')

    def test_a_card_whose_bytes_changed_keeps_its_stop(self):
        self.assert_stopped('NEEDS_RULING: intake card bytes changed')

    def test_an_unknown_reason_keeps_its_stop(self):
        self.assert_stopped('a reason nobody classified')

    def test_an_attempt_that_is_already_parked_is_not_parked_twice(self):
        self.publish()
        row = self.make_row('r0', pinned=False)
        ensure_once(self.pilot.case_dir(row) / 'PARKED.json', dict(design_id='r0', attempt_id='a1', status='PARKED', reason='earlier'))
        self.assertFalse(redesign.write_once_park(self.pilot, row, 'S0', self.GUARD))


if __name__ == '__main__':
    unittest.main()
