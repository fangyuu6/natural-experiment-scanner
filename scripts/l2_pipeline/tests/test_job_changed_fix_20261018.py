"""Invented, outcome-free tests of the 2026-10-18 'Existing job changed' incident (history-completion slot).

b1011eh_complete_streets_statute, attempt requeue_v5, was STOPped by ``ValueError: Existing job changed`` raised for the
Oct-12 completion job of an earlier attempt. The completion slot was keyed by design only and its job was rendered again on
every tick, so a reopened attempt (and any later template change) rewrote the bytes of a file that must never change.
"""
import json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline import c83_history as h
from scripts.l2_pipeline.agents import history as history_module, self_check
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.contract_tests import golden_output
from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.storage import ensure_once


def drifted(original):
    """The prompt text a later code window would produce: one more instruction line (as self_check.py gained on 2026-10-12)."""
    return lambda path: original(path) + "\nInvented later instruction line.\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.job = emit_fixture_job(self.root, 's1_official_history_v9')   # contract attempt_id: v9_fixture
        self.original = golden_output(self.job)
        self.original['records'][0].update(status=None, start_period=None)
        self.original['collection_complete'] = False
        ensure_once(Path(self.job.output_path), self.original)

        class Backend:
            def root(s, row):
                return self.root / 'design'

            def job_state(s, path):
                return 'done' if Path(load_job(Path(path)).output_path).exists() else 'pending'

            def collect(s, path):
                return collect_job(path)

        self.pilot = SimpleNamespace(registry=self.root / 'data/l2_pipeline/registry', backend=Backend(), pending=[],
                                     ledger=lambda *a, **k: None)
        self.design = self.job.design_id
        self.owner = dict(design_id=self.design, attempt_id='v9_fixture')   # the attempt that produced self.job
        self.next = dict(design_id=self.design, attempt_id='v9_next')       # a reopened attempt (requeue)

    def reopened_original(self):
        """The same design's official-history packet of the reopened attempt: its own path, attempt id and incomplete output."""
        previous = self.root / 'previous/A.md'
        job = history_module.emit_history_job(previous, jobs_root=self.root / 'jobs_next', attempt_id='v9_next')
        value = golden_output(job)
        value['records'][1].update(status=None, start_period=None)
        value['collection_complete'] = False
        ensure_once(Path(job.output_path), value)
        return job

    def finish(self, job):
        """A complete, V9-valid answer to a completion job (source ids prefixed c81_ as the packet asks)."""
        answer = golden_output(job)
        for source in answer['sources']:
            source['source_id'] = 'c81_' + source['source_id']
        for record in answer['records']:
            record['source_id'] = 'c81_' + record['source_id']
            for ref in record['evidence_refs']:
                ref['source_id'] = 'c81_' + ref['source_id']
        ensure_once(Path(job.output_path), answer)

    def legacy_completion(self):
        """A completion written the way the code did before attempt keying: slot = the design directory (row without attempt)."""
        self.assertFalse(h.complete_history(self.pilot, dict(design_id=self.design), self.job.job_path))
        return load_job(Path(self.pilot.pending[-1][1]))


class ReopenedAttempt(Base):
    def test_reopened_attempt_gets_its_own_slot_and_the_old_bytes_stay(self):
        old = self.legacy_completion()
        legacy_slot = h.slot(self.pilot, dict(design_id=self.design))
        old_bytes = (Path(old.job_path).read_bytes(), Path(old.job_path).with_suffix('.job.json').read_bytes(),
                     (legacy_slot / 'INTENT.json').read_bytes())
        successor = self.reopened_original()
        # Later code: the completion prompt has one more line, so the old file could not be written again byte for byte.
        with patch.object(self_check, 'instruction', drifted(self_check.instruction)):
            self.assertFalse(h.complete_history(self.pilot, self.next, successor.job_path))
        new = load_job(Path(self.pilot.pending[-1][1]))
        self.assertNotEqual(new.job_path, old.job_path)
        self.assertEqual(new.contract['manual']['universe'], ['U1'])
        self.assertEqual(old.contract['manual']['universe'], ['U0'])
        self.assertEqual(h.slot(self.pilot, self.next), legacy_slot / 'attempts' / 'v9_next')
        self.assertTrue(Path(new.job_path).is_relative_to(h.slot(self.pilot, self.next)))
        self.assertEqual(old_bytes, (Path(old.job_path).read_bytes(), Path(old.job_path).with_suffix('.job.json').read_bytes(),
                                     (legacy_slot / 'INTENT.json').read_bytes()))
        self.assertIn('Invented later instruction line', Path(new.job_path).read_text())

    def test_reopened_attempt_completion_merges_in_its_own_slot(self):
        self.legacy_completion()
        successor = self.reopened_original()
        self.assertFalse(h.complete_history(self.pilot, self.next, successor.job_path))
        job = load_job(Path(self.pilot.pending[-1][1]))
        self.finish(job)
        before = Path(successor.output_path).read_bytes()
        self.assertTrue(h.complete_history(self.pilot, self.next, successor.job_path))
        self.assertTrue(h.complete_history(self.pilot, self.next, successor.job_path))   # idempotent
        self.assertTrue((h.slot(self.pilot, self.next) / 'MERGED_HISTORY.json').is_file())
        self.assertFalse((h.slot(self.pilot, dict(design_id=self.design)) / 'MERGED_HISTORY.json').exists())
        self.assertEqual(Path(successor.output_path).read_bytes(), before)
        self.assertTrue(collect_job(successor.job_path)['validated'])

    def test_reopened_attempt_is_not_judged_by_an_earlier_attempts_finished_completion(self):
        # The earlier attempt used its sole completion and merged it. Before the keying, a reopened attempt whose role was
        # also incomplete was PARKed final ("peer role also incomplete after sole design completion") without any completion.
        first = self.legacy_completion()
        self.finish(first)
        self.assertTrue(h.complete_history(self.pilot, dict(design_id=self.design), self.job.job_path))
        successor = self.reopened_original()
        self.assertFalse(h.complete_history(self.pilot, self.next, successor.job_path))
        second = load_job(Path(self.pilot.pending[-1][1]))
        self.assertEqual(second.contract['manual']['universe'], ['U1'])
        self.assertNotEqual(second.job_path, first.job_path)

    def test_one_completion_per_attempt_is_kept(self):
        self.legacy_completion()
        successor = self.reopened_original()
        self.assertFalse(h.complete_history(self.pilot, self.next, successor.job_path))
        job = load_job(Path(self.pilot.pending[-1][1]))
        answer = golden_output(job)
        answer['collection_complete'] = False
        ensure_once(Path(job.output_path), answer)
        with self.assertRaises(Park) as caught:
            h.complete_history(self.pilot, self.next, successor.job_path)
        self.assertTrue(caught.exception.metrics['final'])
        slot = h.slot(self.pilot, self.next)
        self.assertEqual(len(list(slot.rglob('*.job.json'))), 1)

    def test_slot_owned_by_the_current_attempt_is_resumed_in_place(self):
        old = self.legacy_completion()
        self.assertEqual(h.slot(self.pilot, self.owner), h.slot(self.pilot, dict(design_id=self.design)))
        self.assertFalse(h.complete_history(self.pilot, self.owner, self.job.job_path))
        self.assertEqual(self.pilot.pending[-1][1], old.job_path)
        self.assertFalse((h.slot(self.pilot, self.owner) / 'attempts').exists())

    def test_row_without_attempt_keeps_the_design_slot(self):
        base = self.pilot.registry.parent / 'autopilot/history_completion' / self.design
        self.assertEqual(h.slot(self.pilot, dict(design_id=self.design)), base)


class NoRewrite(Base):
    def test_in_flight_completion_survives_a_later_prompt_change(self):
        first = self.legacy_completion()
        before = Path(first.job_path).read_bytes()
        with patch.object(self_check, 'instruction', drifted(self_check.instruction)):
            self.assertFalse(h.complete_history(self.pilot, self.owner, self.job.job_path))
            self.assertFalse(h.complete_history(self.pilot, dict(design_id=self.design), self.job.job_path))
        self.assertEqual({p for _, p in self.pilot.pending}, {first.job_path})
        self.assertEqual(Path(first.job_path).read_bytes(), before)

    def test_in_flight_completion_survives_a_changed_template_file_path(self):
        # n12 will point history.CURRENT_TEMPLATE at a new template; a job already written keeps the template it names.
        first = self.legacy_completion()
        newer = self.root / 'newer_template.md'
        newer.write_text(Path(history_module.CURRENT_TEMPLATE).read_text() + "\nNewer template paragraph.\n")
        with patch.object(history_module, 'CURRENT_TEMPLATE', newer):
            self.assertFalse(h.complete_history(self.pilot, dict(design_id=self.design), self.job.job_path))
        self.assertEqual(self.pilot.pending[-1][1], first.job_path)
        self.assertEqual(load_job(Path(first.job_path)).template_path, first.template_path)

    def test_written_job_must_still_bind_to_its_original(self):
        first = self.legacy_completion()
        slot = h.slot(self.pilot, dict(design_id=self.design))
        job = load_job(self.job.job_path)
        self.assertEqual(h.completion_job(self.job.job_path, slot, job, ['U0']).job_path, first.job_path)
        with self.assertRaises(Park) as caught:
            h.completion_job(self.job.job_path, slot, job, ['U1'])
        self.assertEqual(caught.exception.metrics['kind'], 'QUEUE_PARK')


class GuardNeverStops(Base):
    def test_write_once_failure_is_a_reopenable_park_not_a_value_error(self):
        with patch.object(history_module, '_write_text_once', side_effect=ValueError('Existing job changed: invented/path.md')):
            with self.assertRaises(Park) as caught:
                h.complete_history(self.pilot, self.next, self.job.job_path)
        exc = caught.exception
        self.assertTrue(str(exc).startswith('QUEUE_PARK: INSUFFICIENT_WORK (queue event, reopenable): '))
        self.assertEqual(exc.metrics['kind'], 'QUEUE_PARK')
        self.assertEqual(exc.metrics['reopen'], 'release_recovery')
        self.assertEqual(exc.metrics['outcome_values_read'], 0)
        self.assertNotIn('final', exc.metrics)
        self.assertIn('Existing job changed', str(exc))

    def test_other_errors_still_propagate(self):
        with patch.object(history_module, '_write_text_once', side_effect=ValueError('Unsafe job identity')):
            with self.assertRaises(ValueError) as caught:
                h.complete_history(self.pilot, self.next, self.job.job_path)
        self.assertNotIsInstance(caught.exception, Park)

    def test_queue_park_text_is_what_release_recovery_and_l3_read(self):
        from scripts.l2_pipeline import release_policy
        with patch.object(history_module, '_write_text_once', side_effect=ValueError('Existing job changed: invented/path.md')):
            with self.assertRaises(Park) as caught:
                h.complete_history(self.pilot, self.next, self.job.job_path)
        self.assertIn('INSUFFICIENT_WORK', str(caught.exception))
        self.assertTrue(str(caught.exception).startswith(release_policy.PREFIX))
        self.assertEqual(caught.exception.metrics['kind'], release_policy.PARK_KIND)


if __name__ == '__main__':
    unittest.main()
