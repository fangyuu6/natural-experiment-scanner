"""Official history readiness keeps blind correction and engine clocks distinct."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.contracts import Disposition
from scripts.l2_pipeline.intake.official_history import conflict_scope, corrected_comparison, engine_records, main, run
from scripts.l2_pipeline.support import support_check


class OfficialHistoryIntakeTests(unittest.TestCase):
    def setUp(self):
        self.sample = ['S' + str(i) for i in range(10)]
        self.rows = [dict(unit=u, status='treated', start_period=2005,
                          best_evidence_start_year=2004, mechanism='M', locator='official')
                     for u in self.sample]
        self.comparison = dict(accepted=self.rows, gaps=[])
        self.checks = [dict(unit=u, status='match', official_locator='official') for u in self.sample]

    def test_correction_is_immutable_and_unresolved_not_corrected(self):
        before = deepcopy(self.comparison)
        self.checks[0].update(status='known_error', corrected_start_period=1995)
        self.checks[1]['status'] = 'unresolved'
        result, audit, conflicts = corrected_comparison(self.comparison, self.checks, self.sample)
        self.assertTrue(audit['passed'])
        self.assertEqual(conflicts, [])
        self.assertEqual(result['accepted'][0]['start_period'], 1995)
        self.assertTrue(result['accepted'][1]['audit_unresolved'])
        self.assertEqual(result['accepted'][1]['start_period'], 2005)
        self.assertEqual(self.comparison, before)

    def test_passing_v3_does_not_close_binary_or_operation_conflicts(self):
        self.comparison['gaps'] = [dict(unit='X', kind='known_conflict'),
                                   dict(unit='Y', kind='best_evidence_year_conflict'),
                                   dict(unit='Z', kind='both_unknown')]
        _, audit, conflicts = corrected_comparison(self.comparison, self.checks, self.sample)
        self.assertTrue(audit['passed'])
        self.assertEqual(conflicts, ['X', 'Y'])

    def test_engine_drops_operation_year_and_shifts_once(self):
        records = engine_records(self.rows, set())
        self.assertEqual(records[0]['start_period'], 2004)
        self.assertEqual(records[0]['audited_first_full_start_period'], 2005)
        controls = [dict(unit='C' + str(i), status='never_treated', start_period=None) for i in range(5)]
        keys = {(r['unit'], y) for r in records + controls for y in range(2000, 2008)}
        support = support_check(keys, records + controls,
            dict(min_treated=6, min_cohorts=1, min_controls=5, min_pre_periods=3),
            period_years=list(range(2000, 2008)), transition_year_drop=True)
        self.assertTrue(support['passed'])
        self.assertEqual(support['transition_years_dropped'], [[u, 2004] for u in sorted(self.sample)])
        self.assertEqual(self.rows[0]['start_period'], 2005)

    def test_ambiguous_january1_or_enactment_clock_requires_mask(self):
        self.rows[0]['best_evidence_start_year'] = 2005
        with self.assertRaisesRegex(ValueError, 'located calendar mask'):
            engine_records(self.rows, set())
        self.assertEqual(len(engine_records(self.rows, {'S0'})), 9)

    def test_v3_failure_keeps_its_gate(self):
        for row in self.checks[:3]:
            row.update(status='known_error', corrected_start_period=2006)
        _, audit, _ = corrected_comparison(self.comparison, self.checks, self.sample)
        self.assertFalse(audit['passed'])

    def gate_fixture(self, root, *, excluded=None, declared=None, conflicts=('X',)):
        rows = deepcopy(self.rows)
        for row in rows[5:]:
            row.update(start_period=2006, best_evidence_start_year=2005)
        controls = [dict(unit='C' + str(i), status='never_treated', start_period=None)
                    for i in range(5)]
        comparison = dict(accepted=rows + controls,
                          gaps=[dict(unit=u, kind='known_conflict', a={'original': 'A'},
                                     b={'original': 'B'}) for u in conflicts])
        comparison, audit, all_conflicts = corrected_comparison(comparison, self.checks, self.sample)
        treatment = dict(universe=self.sample + [r['unit'] for r in controls] + ['X', 'Y'],
                         excluded_units=excluded or [])
        if declared is not None:
            treatment['excluded_conflict_states'] = declared
        payload = dict(treatment=treatment, sensitivities=[], transition_year_drop=True,
                       analysis=dict(min_treated=6, min_cohorts=2, min_controls=5, min_pre_periods=3))
        ctx = SimpleNamespace(card=SimpleNamespace(payload=payload, design_id='synthetic'),
                              design_dir=Path(root), repo=Path(root))
        value = dict(comparison=comparison, audit=audit, sample=self.sample,
                     conflicts=all_conflicts, uncertain_history_units=[], evidence=[])
        return ctx, value

    def test_excluded_conflict_passes_with_original_ledger_and_both_arm_drops(self):
        with tempfile.TemporaryDirectory() as root:
            ctx, value = self.gate_fixture(root, excluded=['X', 'S0', 'C0'], declared=['X'])
            original = deepcopy(value)
            with patch('scripts.l2_pipeline.intake.official_history.derive', return_value=value):
                result = run(ctx)
            self.assertEqual(result.disposition, Disposition.READY)
            certificate = json.loads((Path(root) / 'CERTIFIED_HISTORY.json').read_text())
            treatment = json.loads((Path(root) / 'TREATMENT.json').read_text())
            self.assertEqual(certificate['comparison'], original['comparison'])
            self.assertEqual(certificate['conflicts'], ['X'])
            self.assertEqual(certificate['excluded_conflict_states'], ['X'])
            self.assertEqual(certificate['retained_conflict_states'], [])
            self.assertEqual(treatment['all_records'], original['comparison']['accepted'])
            self.assertEqual(treatment['audit'], original['audit'])
            self.assertEqual(treatment['sample'], self.sample)
            self.assertEqual(treatment['excluded_conflict_states'], ['X'])
            self.assertFalse({'X', 'S0', 'C0'} & {r['unit'] for r in treatment['records']})
            self.assertEqual(value, original)

    def test_retained_conflict_parks_even_with_an_excluded_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            ctx, value = self.gate_fixture(root, excluded=['X'], declared=['X'], conflicts=('X', 'Y'))
            # A supplied certificate cannot override card-scoped certification.
            value['retained_conflict_states'] = []
            with patch('scripts.l2_pipeline.intake.official_history.derive', return_value=value):
                result = run(ctx)
            self.assertEqual(result.disposition, Disposition.PARKED)
            self.assertEqual(result.reason, 'Known history conflicts: Y')
            self.assertFalse((Path(root) / 'TREATMENT.json').exists())
            certificate = json.loads((Path(root) / 'CERTIFIED_HISTORY.json').read_text())
            self.assertEqual(certificate['conflicts'], ['X', 'Y'])
            self.assertEqual(certificate['retained_conflict_states'], ['Y'])

    def test_ordinary_exclusion_without_conflict_declaration_still_parks(self):
        with tempfile.TemporaryDirectory() as root:
            ctx, value = self.gate_fixture(root, excluded=['X'])
            with patch('scripts.l2_pipeline.intake.official_history.derive', return_value=value):
                result = run(ctx)
            self.assertEqual(result.disposition, Disposition.PARKED)
            self.assertEqual(result.reason, 'Known history conflicts: X')

    def test_conflict_declaration_requires_explicit_whole_state_drop(self):
        cfg = dict(universe=['X', 'Y'], excluded_units=['X'])
        for declared in ('X', ['X', 'X'], [False], ['Y'], ['outside']):
            with self.subTest(declared=declared), self.assertRaises(ValueError):
                conflict_scope(dict(cfg, excluded_conflict_states=declared), ['X'])

    def test_excluded_conflict_does_not_relax_v3_errors_or_unresolved(self):
        for status, count in [('known_error', 3), ('unresolved', 6)]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                self.checks = [dict(unit=u, status='match', official_locator='official')
                               for u in self.sample]
                for check in self.checks[:count]:
                    check.update(status=status, corrected_start_period=2005)
                ctx, value = self.gate_fixture(root, excluded=['X'], declared=['X'])
                with patch('scripts.l2_pipeline.intake.official_history.derive', return_value=value):
                    result = run(ctx)
                self.assertEqual(result.disposition, Disposition.PARKED)
                self.assertEqual(result.reason, 'V3 audit failed')
                self.assertEqual(result.metrics, value['audit'])
                self.assertFalse((Path(root) / 'TREATMENT.json').exists())

    def test_conflict_exclusion_does_not_relax_remaining_support(self):
        with tempfile.TemporaryDirectory() as root:
            ctx, value = self.gate_fixture(root, excluded=['X'] + self.sample[:5], declared=['X'])
            analysis = deepcopy(ctx.card.payload['analysis'])
            with patch('scripts.l2_pipeline.intake.official_history.derive', return_value=value):
                self.assertEqual(run(ctx).disposition, Disposition.READY)
            rows = json.loads((Path(root) / 'TREATMENT.json').read_text())['records']
            keys = {(r['unit'], y) for r in rows for y in range(2000, 2009)}
            support = support_check(keys, rows, analysis, period_years=list(range(2000, 2009)),
                                    transition_year_drop=True)
            self.assertFalse(support['passed'])
            self.assertEqual(support['effective_support_minimums'], analysis)
            self.assertEqual(support['issues']['insufficient_treated'], 1)
            self.assertEqual(ctx.card.payload['analysis'], analysis)

    def test_later_operation_cannot_use_nominal_effective_year(self):
        self.rows[0]['timing'] = dict(operational_activation_date='2006-01-01')
        with self.assertRaisesRegex(ValueError, 'Later operation contradicts'):
            engine_records(self.rows, set())

    def test_explicit_entry_restores_the_archived_s1_route(self):
        from scripts.l2_pipeline.stages import s1_treatment
        previous = s1_treatment.run
        with patch('scripts.l2_pipeline.hub.main', side_effect=ValueError('preflight')):
            with self.assertRaisesRegex(ValueError, 'preflight'):
                main(['status'])
        self.assertIs(s1_treatment.run, previous)


if __name__ == '__main__':
    unittest.main()
