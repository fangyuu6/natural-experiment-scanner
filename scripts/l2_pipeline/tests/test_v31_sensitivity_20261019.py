"""2026-10-19 V3.1 incident: `ValueError: V3.1 mechanical sensitivity differs` (b1011cfl_click_through_affiliate_nexus_sales_tax, requeue_v5, S1).

A fresh audit can date a conflict state (R2: AUDIT_RESOLUTIONS.json, written when S1 accepts the audit) and also leave a sampled state's
date open (V3.1: C48_AUDIT_PREPARATION.json). The V3.1 file was registered at the S1 entry, before the resolutions existed, so its
sensitivity still dropped the dated state; once the resolutions were written the same card derived a different sensitivity and every
later context build raised. The same order also made S0's retained CARD.json differ from the card at the next preparation step
(`Existing preparation artifact changed`, b1015q3_pdmp_mandatory_query_opioid_overdose_deaths at draft-dry-load).

Synthetic data only (the R2 conflict fixture of test_redesign_w2); no outcome value exists or is read.
"""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline import c48_audit
from scripts.l2_pipeline.agents import collect_job
from scripts.l2_pipeline.agents.contract_tests import check_job_types, golden_output
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.autopilot import Backend, atomic, read
from scripts.l2_pipeline.autopilot_history import accept, bind
from scripts.l2_pipeline.c42 import Park, effective_context as c42_context
from scripts.l2_pipeline.c48_audit import NAME, bound_comparison, effective_context as c48_context, prepare_s1, register
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests import test_c48
from scripts.l2_pipeline.tests.test_redesign_w2 import ConflictBase, rules_value_r2

LOCATOR = 'https://example.invalid/law'


class Scenario(ConflictBase):
    """Two disputed states (U2 dated by the audit, U3 left open) and one sampled state whose date the audit leaves open."""

    def audit_open(self, job, dated=('U2',), open_conflicts=('U3',), open_sample=1):
        check_job_types([job])
        output = golden_output(job)
        self.open_units = [c['unit'] for c in output['checks'][:open_sample]]
        for check in output['checks'][:open_sample]:
            check.update(status='unresolved')
        output['conflict_checks'] = (
            [dict(unit=u, status='official_date', official_locator=LOCATOR, corrected_start_period=2005) for u in dated]
            + [dict(unit=u, status='unresolved', official_locator=LOCATOR) for u in open_conflicts])
        path = Path(job.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output))
        return collect_job(job.job_path)

    def audited_job(self):
        self.disagree(['U2', 'U3'])
        self.policy['excluded_units'] = []
        job = self.prepare_r2()
        self.audit_open(job)
        return job

    def before_s1(self):
        """What Backend.ctx builds when S1 starts: the C42 overlay; no V3.1 file, no resolutions."""
        return c42_context(self.ctx)

    def rebuilt(self):
        """What Backend.ctx builds on every later tick."""
        return c48_context(c42_context(self.ctx))

    def s1_entry(self, job):
        """autopilot.perform for S1 on route V9: prepare_s1, then accept."""
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_value_r2()):
            ctx = prepare_s1(self.before_s1(), job.job_path)
            return ctx, accept(ctx, job.job_path)

    def registered_before_the_resolutions(self, job):
        """The V3.1 file as the code before this fix wrote it at the S1 entry (register only, no resolutions yet)."""
        job = load_job(Path(job.job_path))
        evidence = [bind(job.output_path), bind(job.promote_path), bind(Path(job.output_dir) / 'COLLECTION.json'),
                    bind(job.contract['reconciliation_path'])]
        return register(self.before_s1(), bound_comparison(evidence), evidence)

    def sensitivity(self, ctx):
        found = [s for s in ctx.card.payload['sensitivities'] if s['name'] == NAME]
        self.assertEqual(len(found), 1)
        return found[0]['changes']['treatment']['excluded_units']

    def prep(self):
        return read(self.ctx.design_dir / 'C48_AUDIT_PREPARATION.json')


class OrderTests(Scenario):
    def test_a_dated_conflict_state_and_an_open_sample_date_pass_s1(self):
        job = self.audited_job()
        ctx, result = self.s1_entry(job)
        self.assertEqual(result.disposition, 'ready')
        self.assertTrue((self.ctx.design_dir / 'AUDIT_RESOLUTIONS.json').is_file())
        primary = ctx.card.payload['treatment']['excluded_units']
        self.assertNotIn('U2', primary)                                            # dated by the audit: back in the primary sample
        self.assertIn('U3', primary)                                               # still open: stays out
        excluded = self.sensitivity(ctx)
        self.assertEqual(excluded, sorted(set(primary) | set(self.open_units)))
        self.assertNotIn('U2', excluded)                                           # a dated state is not an unresolved date
        self.assertIn('U3', excluded)

    def test_the_context_rebuilt_on_every_tick_stays_valid_and_equals_the_registered_record(self):
        job = self.audited_job()
        ctx, _ = self.s1_entry(job)
        again = self.rebuilt()                                                     # raised `V3.1 mechanical sensitivity differs` before the fix
        self.assertEqual(again.card.payload['sensitivities'], ctx.card.payload['sensitivities'])
        self.assertEqual(self.prep()['sensitivities'], again.card.payload['sensitivities'])   # the written record is the effective one
        self.assertEqual(self.prep()['unresolved_units'], sorted(self.open_units))

    def test_a_design_without_dated_states_is_unchanged(self):
        self.disagree(['U3'])
        self.policy['excluded_units'] = []
        job = self.prepare_r2()
        self.audit_open(job, dated=(), open_conflicts=('U3',))
        ctx, result = self.s1_entry(job)
        self.assertEqual(result.disposition, 'ready')
        self.assertFalse((self.ctx.design_dir / 'AUDIT_RESOLUTIONS.json').is_file() and read(self.ctx.design_dir / 'AUDIT_RESOLUTIONS.json')['resolved'])
        self.assertEqual(self.sensitivity(ctx), sorted(set(ctx.card.payload['treatment']['excluded_units']) | set(self.open_units)))
        self.assertEqual(self.rebuilt().card.payload['sensitivities'], ctx.card.payload['sensitivities'])


class StaleRecordTests(Scenario):
    """The state b1011cfl_click_through_affiliate_nexus_sales_tax (requeue_v5) is in: V3.1 file written before the resolutions."""

    def stale(self):
        job = self.audited_job()
        self.registered_before_the_resolutions(job)
        self.assertIn('U2', self.prep()['sensitivities'][-1]['changes']['treatment']['excluded_units'])   # the precondition of the incident
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_value_r2()):
            return job, accept(c48_context(self.before_s1()), job.job_path)

    def test_accept_after_a_stale_registration_completes_with_the_current_derivation(self):
        _, result = self.stale()                                                   # raised ValueError `mechanical sensitivity differs` before the fix
        self.assertEqual(result.disposition, 'ready')

    def test_a_stale_record_is_read_back_as_the_current_derivation_not_the_stored_one(self):
        self.stale()
        rebuilt = self.rebuilt()
        excluded = self.sensitivity(rebuilt)
        self.assertNotIn('U2', excluded)
        self.assertIn('U3', excluded)
        self.assertEqual(excluded, sorted(set(rebuilt.card.payload['treatment']['excluded_units']) | set(self.open_units)))
        self.assertIn('U2', self.prep()['sensitivities'][-1]['changes']['treatment']['excluded_units'])   # the old file is never edited

    def test_the_effective_context_is_idempotent_on_its_own_output(self):
        self.stale()
        once = self.rebuilt()
        self.assertEqual(c48_context(once).card.payload, once.card.payload)

    def test_no_other_stored_sensitivity_is_accepted(self):
        self.stale()
        path = self.ctx.design_dir / 'C48_AUDIT_PREPARATION.json'
        value = read(path)
        value['sensitivities'][-1]['changes']['treatment']['excluded_units'].append('U9')
        atomic(path, value)
        with self.assertRaisesRegex(ValueError, 'V3.1 mechanical sensitivity differs'):
            self.rebuilt()

    def test_the_unresolved_units_are_still_checked(self):
        self.stale()
        path = self.ctx.design_dir / 'C48_AUDIT_PREPARATION.json'
        value = read(path)
        value['unresolved_units'] = sorted(value['unresolved_units'] + ['U9'])
        atomic(path, value)
        with self.assertRaisesRegex(ValueError, 'V3.1 mechanical sensitivity differs'):
            self.rebuilt()


class ParkNotStopTests(unittest.TestCase):
    """A stored V3.1 value that no longer equals its re-derivation is an engineering fault: a reopenable PARK before the freeze."""

    def setUp(self):
        helper = test_c48.AuditTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.helper = helper
        self.ctx = helper.ctx
        register(self.ctx, helper.comparison, helper.evidence)
        path = self.ctx.design_dir / 'C48_AUDIT_PREPARATION.json'
        value = read(path)
        value['sensitivities'][0]['changes']['treatment']['excluded_units'] = []
        atomic(path, value)

    def test_unfrozen_design_parks_with_the_queue_park_shape(self):
        with self.assertRaises(Park) as caught:
            c48_context(self.ctx)
        self.assertEqual(caught.exception.metrics['kind'], 'QUEUE_PARK')
        self.assertEqual(caught.exception.metrics['reopen'], 'release_recovery')
        self.assertEqual(caught.exception.metrics['outcome_values_read'], 0)
        self.assertTrue(str(caught.exception).startswith('QUEUE_PARK: INSUFFICIENT_WORK ('))
        self.assertIn('V3.1 mechanical sensitivity differs', str(caught.exception))

    def test_the_park_is_still_a_value_error_for_every_existing_caller(self):
        with self.assertRaises(ValueError):
            c48_context(self.ctx)

    def test_a_frozen_or_sealed_design_keeps_the_stop(self):
        for name in ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'APPROVAL_PREP.json', 'APPROVAL_FINAL.json', 'TERMINAL.json'):
            marker = ensure_once(self.ctx.design_dir / name, dict(fixture=True))
            with self.assertRaises(ValueError) as caught:
                c48_context(self.ctx)
            self.assertNotIsInstance(caught.exception, Park, name)
            marker.unlink()

    def test_changed_bytes_of_the_bound_evidence_are_still_a_stop(self):
        path = Path(self.helper.evidence[1]['path'])
        path.write_text(path.read_text() + ' ')
        with self.assertRaises(ValueError) as caught:
            c48_context(self.ctx)
        self.assertNotIsInstance(caught.exception, Park)
        self.assertIn('evidence/ruling changed', str(caught.exception))


class RetainedCardTests(Scenario):
    """S0 keeps the card as it stood then; the audit's dated states leave the conflict exclusions only at S1."""

    def retain(self, ctx):
        from scripts.l2_pipeline.registration import _retain_cards
        from scripts.l2_pipeline.contracts import DesignCard
        card_loader = lambda path: DesignCard.from_mapping(read(Path(path)), Path(path))
        with patch('scripts.l2_pipeline.cards.load_card', side_effect=card_loader), \
                patch('scripts.l2_pipeline.connectors.rates.prospective_card', side_effect=lambda payload: dict(payload)), \
                patch('scripts.l2_pipeline.hygiene.scientific_card', side_effect=lambda payload, root: dict(payload)):
            return _retain_cards(ctx)

    def s0_then_s1(self):
        job = self.audited_job()
        ensure_once(self.ctx.design_dir / 'CARD.json', dict(self.before_s1().card.payload))          # S0: dict(ctx.card.payload) at that time
        return job, self.s1_entry(job)[0]

    def test_the_card_s0_kept_before_the_resolutions_is_accepted_with_a_v31_file(self):
        self.s0_then_s1()
        ctx = self.rebuilt()
        self.assertNotEqual(read(self.ctx.design_dir / 'CARD.json'), dict(ctx.card.payload))
        self.retain(ctx)                                                           # raised `Existing preparation artifact changed` before the fix
        self.assertTrue((self.ctx.design_dir / 'SCIENTIFIC_CARD.json').is_file())

    def test_the_card_s0_kept_before_the_resolutions_is_accepted_without_a_v31_file(self):
        self.disagree(['U2', 'U3'])
        self.policy['excluded_units'] = []
        job = self.prepare_r2()
        self.audit_open(job, open_sample=0)                                        # nothing open in the sample: no V3.1 file (pdmp_mandatory_query case)
        ensure_once(self.ctx.design_dir / 'CARD.json', dict(self.before_s1().card.payload))
        self.s1_entry(job)
        self.assertFalse((self.ctx.design_dir / 'C48_AUDIT_PREPARATION.json').exists())
        ctx = self.rebuilt()
        self.assertNotEqual(read(self.ctx.design_dir / 'CARD.json'), dict(ctx.card.payload))
        self.retain(ctx)                                                           # raised `Existing preparation artifact changed` before the fix

    def test_any_other_retained_card_is_still_refused(self):
        self.s0_then_s1()
        path = self.ctx.design_dir / 'CARD.json'
        card = read(path)
        card['treatment']['excluded_units'] = sorted(card['treatment']['excluded_units'] + ['U9'])
        atomic(path, card)
        with self.assertRaisesRegex(ValueError, 'Existing preparation artifact changed'):
            self.retain(self.rebuilt())


class RootAfterAnOverlayParkTests(unittest.TestCase):
    """The reopen tools need the design directory of a row whose overlay raised a PARK; any other error still stops the row."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        card = dict(schema_version='1.0', design_id='dir-fixture', version='v7', policy_family='fixture', mechanism='fixture', track='DiD',
                    unit='state', outcome_family='fixture', treatment=dict(excluded_units=[]), sensitivities=[])
        self.card = self.repo / 'data/l2_pipeline/cards/dir.json'
        atomic(self.card, card)
        self.row = dict(design_id='dir-fixture', attempt_id='a', card=str(self.card), card_sha256=sha(self.card),
                        work_dir=str(self.repo / 'data/l2_pipeline/work'), route='V9')
        self.backend = Backend(self.repo)
        self.expected = self.repo / 'data/l2_pipeline/work/dir-fixture/v7'

    def test_an_overlay_park_does_not_hide_the_directory(self):
        with patch.object(Backend, 'ctx', side_effect=Park('QUEUE_PARK: fixture', dict(kind='QUEUE_PARK'))):
            self.assertEqual(self.backend.root(self.row), self.expected)

    def test_ctx_itself_still_raises_the_park(self):
        identity = lambda ctx: ctx
        with patch('scripts.l2_pipeline.c48_audit.effective_context', side_effect=Park('QUEUE_PARK: fixture', dict(kind='QUEUE_PARK'))), \
                patch('scripts.l2_pipeline.run2_preparation.effective_context', side_effect=identity), \
                patch('scripts.l2_pipeline.c42.effective_context', side_effect=identity), \
                patch('scripts.l2_pipeline.hub_adjudication.effective_context', side_effect=identity), \
                patch('scripts.l2_pipeline.rules.of_payload', return_value=None):
            with self.assertRaises(Park):
                self.backend.ctx(self.row)

    def test_any_other_error_still_propagates(self):
        with patch.object(Backend, 'ctx', side_effect=ValueError('C42 original evidence/ruling changed: x')):
            with self.assertRaisesRegex(ValueError, 'C42 original'):
                self.backend.root(self.row)


class ReopenTests(unittest.TestCase):
    """A V3.1 PARK whose context raises the PARK again is listed and reopened by release_recovery (the existing card-bound requeue)."""

    def setUp(self):
        from scripts.l2_pipeline import release_recovery as rr
        from scripts.l2_pipeline.tests import test_c46
        self.rr = rr
        test_c46.RequeueTests.setUp(self)
        self.design = 'v31-park'
        path = self.repo / 'data/l2_pipeline/cards' / (self.design + '.json')
        atomic(path, dict(self.card, design_id=self.design))
        self.parked = dict(self.row, design_id=self.design, card=str(path), card_sha256=sha(path))
        self.reason = c48_audit.PARK_PREFIX + 'V3.1 mechanical sensitivity differs'
        ensure_once(self.pilot.case_dir(self.parked) / 'PARKED.json', dict(
            design_id=self.design, attempt_id='old', action='S1', status='PARKED', reason=self.reason,
            metrics=dict(kind='QUEUE_PARK', reopen='release_recovery', outcome_values_read=0)))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=read(self.registry / 'ACTIVE_DESIGNS.json')['designs'] + [self.parked]))
        (self.repo / rr.RULING).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / rr.RULING).write_text('fixture hub order')
        (self.registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task='WINDOW_V31', time='2026-10-19T07:00:00+00:00')))

    def raising(self):
        """The V3.1 overlay raises its PARK for the parked card (version v1) and for nothing else."""
        def effective(ctx):
            if ctx.card.design_id == self.design and ctx.card.version == 'v1':
                raise Park(self.reason, dict(kind='QUEUE_PARK', reopen='release_recovery', outcome_values_read=0))
            return ctx
        return patch('scripts.l2_pipeline.c48_audit.effective_context', side_effect=effective)

    def test_the_park_is_listed_and_requeued_while_its_context_still_raises_it(self):
        with self.raising():
            candidates, kept = self.rr.plan(self.pilot, include_parks=True)
            self.assertIn(self.design, [c['row']['design_id'] for c in candidates])
            self.assertEqual(kept, [])
            done = self.rr.apply(self.pilot, include_parks=True)
        self.assertIn(self.design, [r['design_id'] for r in done['records']])
        self.assertEqual(done['refused'], [])
        active = {r['design_id']: r for r in read(self.registry / 'ACTIVE_DESIGNS.json')['designs']}
        self.assertEqual(active[self.design]['attempt_id'], 'requeue_v2')
        self.assertTrue((self.pilot.case_dir(self.parked) / 'PARKED.json').is_file())          # the old attempt's record stays


if __name__ == '__main__':
    unittest.main()
