"""Exposure replay regressions; fit invented values, inspect real metadata only."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import unittest

from scripts.l2_pipeline.engine import estimator
from scripts.l2_pipeline.engine.design import build_design, declaration_from_support
from scripts.l2_pipeline.full_year import (
    construct_full_year, declaration_from_card_support, effective_full_year_declaration,
)

ROOT = Path(__file__).resolve().parents[3]


def invented_source():
    years = list(range(2001, 2019))
    source = dict(key='invented', period_years=years, window=[2001, 2018],
                  operative={u: 2006 for u in 'ABC'} | {u: 2009 for u in 'DEF'} | {'X': 2018},
                  never_controls=list('GHIJK') + ['S'], source_ready=True,
                  actual_clean_onset={'X': 2007, 'S': 2008},
                  initial_step_starts={'X': 2008, 'D': 2007},
                  scope_ambiguous_starts={'S': 2011}, exclusions=['S'],
                  disputed_audit_error_units=['A'], unresolved_audit_units=['B'],
                  competing_mechanism_units=['C'], narrow_coverage_units=['D'])
    metadata = [dict(unit=u, year=str(y), annual_usable='1')
                for u in sorted(set(source['operative']) | set(source['never_controls']))
                for y in years]
    return source, metadata


def replay(source, variant, metadata):
    summary, required = construct_full_year(source, variant, metadata, {})
    declaration = declaration_from_support(source, summary, key=source['key'], variant=variant)
    # No archived outcomes are used: these are invented values or metadata zeros.
    rows = [(u, y, 0., None) for u, y in sorted(required)]
    return summary, declaration, build_design(declaration, rows)


class ExposureReviewFixes(unittest.TestCase):
    def test_actual_clean_onset_preserved_for_included_treated_state(self):
        source, metadata = invented_source()
        source['actual_clean_onset']['A'] = 2004
        summary, declaration, design = replay(source, 'primary', metadata)
        self.assertFalse(declaration['exposure']['A'][3]['clean'])  # calendar 2004
        self.assertEqual(design['cells'], summary['cell_inventory'])
        self.assertEqual(design['cohort_bases']['A'], 2)  # final clean calendar 2003

    def test_future_adopter_never_compares_after_actual_exposure(self):
        source, metadata = invented_source()
        summary, declaration, design = replay(source, 'not_yet_controls', metadata)
        self.assertEqual(design['cells'], summary['cell_inventory'])
        exposed = [c for c in design['cells'] if 'X' in c['controls'] and
                   max(c['t'], c['base_period']) >= 6]
        self.assertEqual(exposed, [])
        # A synthetic contaminated future-control outcome would bias this ATT.
        outcomes = {(u, t): 0. if u not in declaration['cohorts'] else
                    (100. if u == 'X' and t >= 6 else float(t >= declaration['cohorts'][u]))
                    for u, t in design['mask']}
        fitted = estimator.estimate_design(design, outcomes, B=9)
        self.assertAlmostEqual(fitted['overall']['att'], 1.)
        broken = copy.deepcopy(declaration)
        broken['exposure']['X'] = {t: dict(clean=t < 17, full=False) for t in range(18)}
        bad_design = build_design(broken, [(u, source['period_years'][t], y, None)
                                        for (u, t), y in outcomes.items()])
        bad_fit = estimator.estimate_design(bad_design, outcomes, B=9)
        self.assertNotAlmostEqual(bad_fit['overall']['att'], 1.)

    def test_every_clock_scope_exclusion_variant_replays_constructor_cells(self):
        source, metadata = invented_source()
        original = copy.deepcopy(source)
        variants = ['primary', 'not_yet_controls', 'drop_disputed_audit_error',
                    'drop_unresolved_audit_dates', 'exclude_convention_controls',
                    'start_plus_one', 'start_minus_one', 'exclude_2020_2021',
                    'include_2020_2021', 'exclude_competing_mechanism',
                    'include_scope_ambiguous', 'drop_narrow_coverage',
                    'initial_step_clock', 'pre_step_anticipation',
                    'omit_state_A', 'omit_cohort_2007']
        for variant in variants:
            with self.subTest(variant=variant):
                summary, required = construct_full_year(source, variant, metadata, {})
                card = dict(design_id='invented', treatment=dict(full_year_declaration=source),
                            analysis=dict(support_adapter='annual_full_year_v1', full_year_variant=variant))
                declaration = declaration_from_card_support(card, summary)
                rows = [(u, y, 0., None) for u, y in sorted(required)]
                if summary['engine_error']:
                    with self.assertRaises(ValueError):
                        build_design(declaration, rows)
                else:
                    design = build_design(declaration, rows)
                    self.assertEqual(design['cells'], summary['cell_inventory'])
                self.assertEqual(source, original)
        effective, routed = effective_full_year_declaration(source, 'pre_step_anticipation')
        self.assertEqual(effective['actual_clean_onset']['X'], 2006)
        self.assertEqual(routed, 'primary')
        effective, _ = effective_full_year_declaration(source, 'initial_step_clock')
        self.assertEqual(effective['operative']['X'], 2008)
        effective, _ = effective_full_year_declaration(source, 'include_scope_ambiguous')
        self.assertEqual(effective['operative']['S'], 2011)
        self.assertNotIn('S', effective['never_controls'])

    def test_card_support_identity_mismatch_is_rejected(self):
        source, metadata = invented_source()
        summary, _ = construct_full_year(source, 'primary', metadata, {})
        card = dict(design_id='another', treatment=dict(full_year_declaration=source),
                    analysis=dict(support_adapter='annual_full_year_v1', full_year_variant='primary'))
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            declaration_from_card_support(card, summary)

    def test_existing_batch4_all_124_metadata_topologies_replay(self):
        checked = 0
        for key in ('pay_secrecy', 'pregnancy_accommodation'):
            base = ROOT/'data/l2_pipeline/acceptance/batch4'/key/'v1'
            summaries = json.loads((base/'SUPPORT.json').read_text())['variants']
            for summary in summaries:
                name = summary['variant']
                with self.subTest(design=key, variant=name):
                    directory = base/'panels'/name
                    card = json.loads((directory/'VARIANT_CARD.json').read_text())
                    declaration = declaration_from_card_support(card, summary)
                    # Only unit/year metadata is opened; panel.csv remains unread.
                    with (directory/'metadata.csv').open(newline='') as handle:
                        rows = [(r['unit'], int(r['year']), 0., None) for r in csv.DictReader(handle)]
                    if summary['engine_error']:
                        with self.assertRaises(ValueError):
                            build_design(declaration, rows)
                    else:
                        self.assertEqual(build_design(declaration, rows)['cells'], summary['cell_inventory'])
                    checked += 1
        self.assertEqual(checked, 124)


if __name__ == '__main__':
    unittest.main()
