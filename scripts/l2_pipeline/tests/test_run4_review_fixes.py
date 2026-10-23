"""Run4 draft-review regressions using invented outcomes and person counts."""
import math
import random
import unittest
from pathlib import Path

import numpy as np

from scripts.l2_pipeline.connectors import ConnectorRequest
from scripts.l2_pipeline.connectors.cps_nurse import annualize
from scripts.l2_pipeline.engine import calibration, estimator, ri
from scripts.l2_pipeline.engine.design import build_design, structural_rows, treatment_rows
from scripts.l2_pipeline.support import support_check, standard_annual_declaration
from scripts.l2_pipeline.stages.s7_export import exports


class Run4ReviewFixTests(unittest.TestCase):
    def test_calibration_replays_execution_draws_with_retained_future_adopter(self):
        records = [dict(unit=u, status='treated', start_period=6 if u in 'ABC' else 9)
                   for u in 'ABCDEF']
        records += [dict(unit=u, status='never_treated', start_period=None)
                    for u in 'GHIJKL']
        records += [dict(unit='M', status='not_yet_treated', start_period=14)]
        keys = {(r['unit'], t) for r in records for t in range(14)}
        config = dict(min_treated=6, min_controls=5, min_cohorts=2,
                      min_pre_periods=3, support_gap_rule='drop_affected_state_v7')
        summary = support_check(keys, records, config, period_years=list(range(14)))
        self.assertTrue(summary['passed'])
        declaration = standard_annual_declaration(
            records, config, list(range(14)), key='invented', summary=summary)
        rows = [(u, t, 0., None) for u, t in sorted(keys)]
        design = build_design(declaration, rows)
        self.assertIsNone(design['adoption']['M'])
        self.assertFalse(design['whole_window_clean']['M'])
        operators = calibration.calibration_operators(
            declaration, structural_rows(declaration, rows),
            treatment_rows(declaration), permutations=199)
        index = {key: i for i, key in enumerate(operators['keys'])}
        family = ri.pre_family(design)
        runtime_pre, runtime_post = [], []
        for labels in ri.predraw_labels(design, 199):
            fake = ri.relabeled_design(declaration, design, labels)
            # The future-adopter label remains ineligible even though g=None;
            # exactly six registered never labels supply every contrast.
            allowed = {u for u, label in zip(fake['units'], labels) if label[3]}
            self.assertEqual(len(allowed), 6)
            self.assertTrue(all(set(cell['controls']) == allowed for cell in fake['cells']))
            self.assertEqual(ri.pre_family(fake), family)
            event_ops = estimator.event_operators(fake)
            pre = np.zeros((len(family), len(index)))
            for j, event in enumerate(family):
                for key, weight in event_ops[event]['weights'].items():
                    pre[j, index[key]] += weight
            post = np.zeros(len(index))
            cells = [cell for cell in fake['cells'] if cell['post']]
            mass = sum(cell['n_treated'] for cell in cells)
            for cell in cells:
                for key, weight in estimator.contrast_weights(cell).items():
                    post[index[key]] += cell['n_treated'] / mass * weight
            runtime_pre.append(pre)
            runtime_post.append(post)
        np.testing.assert_array_equal(operators['ri'], np.array(runtime_pre))
        np.testing.assert_array_equal(operators['ri_overall'], np.array(runtime_post))
        self.assertEqual(operators['ri_feasibility']['requested_draws'], 199)
        self.assertEqual(operators['ri_feasibility']['infeasible_count'], 0)
        self.assertEqual(operators['ri_feasibility']['redrawn'], 0)

        rng = random.Random(77)
        outcomes = {key: rng.gauss(0., 1.) for key in sorted(keys)}
        vector = np.array([outcomes[key] for key in operators['keys']])
        pre = operators['pre'] @ vector
        rms = math.sqrt(float(np.mean(pre * pre)))
        reference = ri.permutation_reference(declaration, design, outcomes, count=199)
        calibration_nulls = np.sqrt(np.mean((operators['ri'] @ vector) ** 2, axis=1))
        np.testing.assert_allclose(calibration_nulls, reference['rms'], atol=1e-15, rtol=0)
        calibration_p = (1 + sum(value >= rms for value in calibration_nulls)) / 200
        execution_p = (1 + sum(value >= rms for value in reference['rms'])) / 200
        self.assertEqual(calibration_p, execution_p)
        self.assertEqual(execution_p, .8)

    def test_nurse_limitations_do_not_promise_an_unregistered_sensitivity(self):
        request = ConnectorRequest.from_card({'outcome': dict(
            connector='cps', years=[2000, 2000], units=['AL'], frequency='annual',
            value_field='weekly_hours', population_filters=dict(
                age_min=18, age_max=64, civilian_noninstitutional=True,
                occupation='registered_nurses', industry='hospitals'))}, Path.cwd())
        monthly = [dict(unit='AL', year=2000, month=month, n_target=10,
                        n_valid=10, n_missing=0, weight_sum=10,
                        hours_weight_sum=400, long_hours_weight_sum=0)
                   for month in range(1, 13)]
        panel = annualize(monthly, request)
        self.assertEqual(panel.rows[0]['outcome_value'], 40)
        limitation = panel.limitations[0]
        self.assertIn('includes advanced-practice nurses', limitation)
        self.assertIn('If separately registered', limitation)
        self.assertIn('would use the narrower definition', limitation)
        self.assertNotIn('A 2011+ sensitivity uses', limitation)

    def test_exports_retain_source_scope_and_partial_adoption_year_clock(self):
        source = dict(basis='paper-reported roster and sampled official-date audit',
                      historical_zero_certification=False,
                      withdrawal_history_complete=False)
        clock = dict(convention='calendar_year', partial_adoption_year_included=True,
                     interpretation='adoption-year effects include unexposed months')
        card = dict(design_id='invented', version='v2', policy_family='invented',
                    track='DiD', lineage={'screen_version': 'invented'},
                    outcome_family='employment', analysis=dict(
                        source_certification=source, annual_clock_disposition=clock))
        provenance = {'terminal_sha256': 'a' * 64, 'result_sha256': None,
                      'additional_evidence': {'path': 'invented.json'}}
        fragments = exports(card, {'status': 'STOPPED', 'reason': 'invented support'},
                            None, provenance)
        self.assertEqual(set(fragments), {'registry_event', 'estimates_page', 'l3_meta'})
        for fragment in fragments.values():
            evidence = fragment['provenance']
            self.assertEqual(evidence['source_certification'], source)
            self.assertIs(evidence['source_certification']['historical_zero_certification'], False)
            self.assertEqual(evidence['annual_clock_disposition'], clock)
            for key, value in provenance.items():
                self.assertEqual(evidence[key], value)
        self.assertNotIn('source_certification', provenance)
        fragments['l3_meta']['provenance']['source_certification']['historical_zero_certification'] = True
        self.assertIs(source['historical_zero_certification'], False)

        card['analysis'] = {}
        for fragment in exports(card, {'status': 'NOT_RUN'}, None, provenance).values():
            self.assertEqual(fragment['provenance'], provenance)


if __name__ == '__main__':
    unittest.main()
