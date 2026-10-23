"""Generic run1 review regressions using invented metadata and outcomes only."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from scripts.l2_pipeline.engine.design import build_design, declaration_from_support
from scripts.l2_pipeline.engine import estimator
from scripts.l2_pipeline.full_year import construct_full_year, effective_full_year_declaration


def fixture():
    years = list(range(2000, 2025))
    source = dict(key='invented_reversal', period_years=years, window=[2000, 2024],
                  operative={u: 2006 for u in 'ABCD'} | {u: 2009 for u in 'EFG'},
                  never_controls=list('HIJKLMNO'), convention_filled_controls=list('HIL'),
                  exposure_end_dates={'A': '2019-11-30'}, reversal_handling='drop_states',
                  actual_clean_onset={}, exclusions=[], omitted_years=[], source_ready=True)
    metadata = [dict(unit=u, year=str(y), annual_usable='1')
                for u in list('ABCDEFGHIJKLMNO') for y in years]
    return source, metadata


def replay(source, metadata, variant):
    summary, mask = construct_full_year(source, variant, metadata, {})
    declaration = declaration_from_support(source, summary, key=source['key'], variant=variant)
    design = build_design(declaration, [(u, y, 0., None) for u, y in sorted(mask)])
    return summary, mask, declaration, design


class ReversalControlTests(unittest.TestCase):
    def test_archival_enrichment_requires_exact_verified_convention_inventory(self):
        from scripts.l2_pipeline.acceptance import ROOT, validate_archival_s1
        source = json.loads((ROOT/'scratchpad/l2_batch4/DECLARATIONS.json').read_text())['pay_secrecy']
        enriched = dict(source, convention_filled_controls=list(source['never_controls']))
        _, proof = validate_archival_s1(ROOT, 'pay_secrecy', enriched)
        self.assertEqual(proof['convention_filled_controls'], source['never_controls'])
        self.assertTrue(any(Path(r['path']).name == 'COMMON_CONVENTION.json' for r in proof['evidence']))
        enriched['convention_filled_controls'] = []
        with self.assertRaisesRegex(ValueError, 'verified archival convention'):
            validate_archival_s1(ROOT, 'pay_secrecy', enriched)

    def test_primary_excludes_reversed_state_entirely(self):
        source, metadata = fixture()
        summary, mask, declaration, design = replay(source, metadata, 'primary')
        self.assertTrue(summary['support_pass'])
        self.assertIn('A', summary['metadata_excluded_units'])
        self.assertNotIn('A', summary['retained_treated'])
        self.assertNotIn('A', summary['never_controls'])
        self.assertFalse(any(u == 'A' for u, _ in mask))
        self.assertEqual(design['cells'], summary['cell_inventory'])

    def test_registered_sensitivity_preserves_history_and_censors_cessation(self):
        source, metadata = fixture()
        summary, mask, declaration, design = replay(source, metadata, 'retain_ended_exposure')
        self.assertTrue(summary['support_pass'])
        self.assertIn('A', summary['retained_treated'])
        self.assertEqual([y for u, y in sorted(mask) if u == 'A' and y >= 2007],
                         list(range(2007, 2019)))
        self.assertFalse(declaration['exposure']['A'][19]['full'])
        self.assertFalse(declaration['exposure']['A'][20]['clean'])
        self.assertTrue(declaration['exposure']['A'][18]['full'])
        self.assertFalse(any('A' in c['controls'] for c in design['cells']))
        self.assertEqual(design['cells'], summary['cell_inventory'])
        outcomes = {(u, t): 100. if u == 'A' and t >= 19 else 0.
                    for u, t in design['mask']}
        result = estimator.estimate_design(design, outcomes, B=9)
        self.assertAlmostEqual(result['overall']['att'], 0.)
        # A contaminated absorbing fixture would have a positive ATT.
        absorbing = copy.deepcopy(source)
        absorbing['exposure_end_dates'] = {}
        _, _, _, bad = replay(absorbing, metadata, 'primary')
        bad_outcomes = {(u, t): 100. if u == 'A' and t >= 19 else 0.
                        for u, t in bad['mask']}
        self.assertGreater(estimator.estimate_design(bad, bad_outcomes, B=9)['overall']['att'], 0.)

    def test_january_first_cessation_keeps_entire_prior_year(self):
        source, metadata = fixture()
        source['exposure_end_dates']['A'] = '2020-01-01'
        summary, mask, declaration, design = replay(source, metadata, 'retain_ended_exposure')
        self.assertIn(('A', 2019), mask)
        self.assertNotIn(('A', 2020), mask)
        self.assertTrue(declaration['exposure']['A'][19]['full'])
        self.assertFalse(declaration['exposure']['A'][20]['full'])
        self.assertEqual(design['cells'], summary['cell_inventory'])

    def test_convention_sensitivity_drops_only_registered_fills(self):
        source, metadata = fixture()
        summary, mask, declaration, design = replay(source, metadata, 'exclude_convention_controls')
        self.assertEqual(summary['never_controls'], list('JKMNO'))
        self.assertTrue(summary['support_pass'])
        self.assertEqual(set(declaration['never_controls']), set('JKMNO'))
        self.assertFalse(any(u in 'HIL' for u, _ in mask))
        self.assertEqual(design['cells'], summary['cell_inventory'])

    def test_empty_convention_set_retains_all_controls(self):
        source, metadata = fixture()
        source['convention_filled_controls'] = []
        summary, _, _, _ = replay(source, metadata, 'exclude_convention_controls')
        self.assertEqual(summary['never_controls'], list('HIJKLMNO'))

    def test_independent_registered_variants_preserve_reversal_exclusion(self):
        source, metadata = fixture()
        before = copy.deepcopy(source)
        for variant in ('primary', 'not_yet_controls', 'start_plus_one', 'start_minus_one',
                        'exclude_2020_2021', 'omit_state_B', 'constant_definition_window'):
            current = copy.deepcopy(source)
            current['constant_definition_window'] = [2001, 2024]
            with self.subTest(variant=variant):
                summary, mask, _, design = replay(current, metadata, variant)
                self.assertFalse(any(u == 'A' for u, _ in mask))
                self.assertEqual(design['cells'], summary['cell_inventory'])
        self.assertEqual(source, before)

    def test_bad_binding_rejected_without_assert_statements(self):
        source, _ = fixture()
        for field, value in [('convention_filled_controls', ['A']),
                             ('convention_filled_controls', ['H', 'H']),
                             ('exposure_end_dates', {'Z': '2019-11-30'}),
                             ('exposure_end_dates', {'A': '2019'}),
                             ('exposure_end_dates', {'A': '2005-01-01'}),
                             ('reversal_handling', 'invented')]:
            current = copy.deepcopy(source)
            current[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                effective_full_year_declaration(current, 'primary')


if __name__ == '__main__':
    unittest.main()
