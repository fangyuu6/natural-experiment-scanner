"""Read-only archival reproduction plus invented-world engine regression tests.

Only automatic_voter's already-frozen inputs are fit. No official result is
created, and no future study's outcome associations are read or estimated.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest

from scripts.l2_pipeline.engine import calibration, estimator, inference, noise, panels, ri
from scripts.l2_pipeline.engine.design import declaration_from_support
from scripts.l2_pipeline.engine.runner import fit_primary

ROOT = Path(__file__).resolve().parents[3]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def invented_fixture():
    units = list('ABCDEFGHIJKL')
    cohorts = {u: 6 for u in 'ABC'} | {u: 9 for u in 'DEF'}
    declaration = dict(key='invented', variant='primary', cohorts=cohorts,
        operative_years=dict(cohorts), never_controls=list('GHIJKL'),
        case=dict(panel=dict(frequency='annual', time_basis=dict(kind='calendar_year')),
                  windows=dict(primary=dict(start=0, end=13, excluded_periods=[])),
                  sample_mask=dict(include_units=units), comparison=dict(exclusions=[]),
                  exposure_clock=dict(date_column='operative_date')))
    rng = random.Random(77)
    rows = []
    for j, u in enumerate(units):
        level = 45+j
        for t in range(14):
            level += rng.gauss(0, .4)
            rows.append((u, t, level+(1.5 if u in cohorts and t >= cohorts[u] else 0), cohorts.get(u)))
    return declaration, rows


class ArchivedPrimaryEquivalence(unittest.TestCase):
    def test_batch2_automatic_voter_points_match_and_legacy_intervals_are_suppressed(self):
        base = ROOT/'scripts/l2_batch2'
        source_path = ROOT/'data/l2_batch2/DECLARATIONS.json'
        support_path = base/'SUPPORT.json'
        evaluation = base/'calibration/INTERVAL_EVALUATION.json'
        frozen_result = base/'results/batch2_20261007_final/automatic_voter.json'
        manifest = json.loads((base/'FREEZE_MANIFEST_FINAL.json').read_text())
        def verify_frozen(path):
            self.assertEqual(panels.sha(path), manifest['files'][path.relative_to(ROOT).as_posix()])
        for path in (source_path, support_path, evaluation):
            verify_frozen(path)
        source = json.loads(source_path.read_text())['automatic_voter']
        summary = json.loads(support_path.read_text())['cases']['automatic_voter']['variants']['primary']
        declaration = declaration_from_support(source, summary, key='automatic_voter')
        binding = panels.PanelBinding(
            source_path=ROOT/source['outcome_path'], panel_path=ROOT/summary['panel_path'],
            metadata_path=ROOT/summary['metadata_path'],
            source_sha256=manifest['files'][source['outcome_path']],
            panel_sha256=summary['panel_sha256'], metadata_sha256=summary['metadata_sha256'])
        for path in (binding.source_path, binding.panel_path, binding.metadata_path):
            verify_frozen(path)
        before = panels.sha(frozen_result)
        rows, identity = panels.load_bound_panel(binding)
        self.assertTrue(identity['passed'])
        fitted, _, _ = fit_primary(declaration, rows, interval_evaluation_path=evaluation)
        # Captured from the finished archival design before V8b implementation.
        # The absent default preserves the entire output, including reporting.
        self.assertNotIn('transition_year_drop', declaration)
        self.assertEqual(hashlib.sha256(encoded(fitted)).hexdigest(),
            'dcfee8d1c664c2936e663d3d8125ac9e73cd12caedf6d0d3f71fb79b4c0624e3')
        expected = json.loads(frozen_result.read_text())['registered_results']['primary']
        # The legacy CT artifact has no current production certificate. Preserve
        # archival point/SE/reference equivalence and suppress its old intervals.
        def point_payload(payload):
            payload=copy.deepcopy(payload)
            value=payload['result']
            value['inference'].pop('interval_reporting',None)
            for row in [*value['group_time'],*value['event_study'],*value['group'],
                        *value['calendar'],value['overall']]:
                for field in list(row):
                    if field=='interval' or field=='ci95' or 'simultaneous95' in field:
                        row.pop(field)
            return payload
        self.assertEqual(encoded(point_payload(fitted)), encoded(point_payload(expected)))
        self.assertIsNone(fitted['result']['overall']['ci95'])
        self.assertFalse(fitted['result']['overall']['interval']['reported'])
        self.assertEqual(panels.sha(frozen_result), before)
        print('ARCHIVAL_EQUIVALENCE '+json.dumps(dict(
            design='automatic_voter', primary_bytes=len(encoded(fitted)),
            primary_sha256=hashlib.sha256(encoded(fitted)).hexdigest(),
            att=fitted['result']['overall']['att'], official_result_written=False)))


class NumericalEngineTests(unittest.TestCase):
    def test_negative_generic_outcomes_and_anchor_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            panel, metadata = root/'panel.csv', root/'metadata.csv'
            panel.write_text('unit,year,outcome_value\nA,1,-2\nA,2,-5\nB,1,-5\nB,2,-7\n')
            metadata.write_text('unit,year\nA,1\nA,2\nB,1\nB,2\n')
            binding = panels.PanelBinding(panel, panel, metadata,
                panels.sha(panel), panels.sha(panel), panels.sha(metadata))
            rows, identity = panels.load_bound_panel(binding)
            self.assertEqual([row[2] for row in rows], [-2., -5., -5., -7.])
            self.assertTrue(identity['passed'])
            anchor = calibration.blind_anchor(panel, units=['A', 'B'], periods=[1, 2],
                earliest_operative_period=3, value_column='outcome_value')
            self.assertGreater(anchor['value'], 0)
            with self.assertRaisesRegex(ValueError, 'anchor value'):
                calibration.blind_anchor(panel, units=['A', 'B'], periods=[1, 2],
                    earliest_operative_period=3, value_column='outcome_value', lower_bound=0)
            with self.assertRaisesRegex(RuntimeError, 'range failure'):
                panels._value(dict(turnout_percent='-1'))

    def test_duplicate_and_nonfinite_values_fail_before_numerical_fit(self):
        d, rows = invented_fixture()
        with self.assertRaisesRegex(ValueError, 'Duplicate outcome key'):
            fit_primary(d, rows+[rows[0]], B=9)
        invalid = list(rows)
        u, t, _, g = invalid[0]
        invalid[0] = (u, t, float('nan'), g)
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            fit_primary(d, invalid, B=9)

    def test_independent_endpoint_and_unvalidated_interval_suppression(self):
        d, rows = invented_fixture()
        row, design, _ = fit_primary(d, rows, B=99)
        self.assertTrue(row['reference']['passed'])
        self.assertLessEqual(row['reference']['maximum_cell_absolute_difference'], 1e-8)
        self.assertIsNone(row['result']['overall']['ci95'])
        self.assertTrue(all(c['ci95'] is None for c in row['result']['group_time']))
        self.assertEqual(len(design['groups']), 2)

    def test_fixed_ri_feasibility_threshold_never_redraws(self):
        for failed, passed in [(10, True), (11, False)]:
            rec = ri.inventory([dict(infeasible=True, reason='no support')]*failed +
                               [dict(infeasible=False)]*(100-failed))
            self.assertEqual(rec['support_pass'], passed)
            self.assertEqual(rec['redrawn'], 0)
            self.assertEqual(rec['infeasible_draws'][0]['draw_index'], 0)
            if not passed:
                with self.assertRaisesRegex(ValueError, 'below 90%'):
                    ri.require_feasible(rec)

    def test_missing_ct_donor_cells_fail_independently(self):
        d, rows = invented_fixture()
        _, design, outcomes = fit_primary(d, rows, B=9)
        g = inference.geometry(design)
        del outcomes[('G', 0)]
        Y=inference.outcome_matrix(g,outcomes)
        rec=inference.candidates(g,Y,inference.draws(g,99,1))
        self.assertIsNone(rec['control_path_ct'])
        self.assertIn('complete CT donor',rec['failures']['control_path_ct'])
        self.assertIsNotNone(rec['webb_wcu_t'])

    def test_webb_precedes_ct_and_one_failed_design_blocks_it(self):
        passed = dict(summaries={k: dict(passed=True) for k in inference.METHODS})
        cases = dict(a=copy.deepcopy(passed), b=copy.deepcopy(passed))
        self.assertEqual(inference.select_method(cases, expected_designs=['a','b']), 'webb_wcu_t')
        cases['b']['summaries']['webb_wcu_t']['passed'] = False
        self.assertEqual(inference.select_method(cases, expected_designs=['a','b']), 'control_path_ct')
        cases['a']['summaries']['control_path_ct']['passed'] = False
        self.assertIsNone(inference.select_method(cases, expected_designs=['a','b']))
        with self.assertRaises(ValueError):
            inference.select_method(cases, expected_designs=['a', 'b', 'c'])

    def test_blind_anchor_does_not_parse_ineligible_tails(self):
        with tempfile.TemporaryDirectory() as temp:
            panel = Path(temp)/'panel.csv'
            panel.write_text('unit,year,y\nA,1,2\nA,2,5\nB,1,5\nB,2,7\nA,3,unparseable future tail\nB,3,also opaque\n')
            rec = calibration.blind_anchor(panel, units=['A', 'B'], periods=[1, 2],
                earliest_operative_period=3, value_column='y')
            self.assertEqual(rec['outcome_values_converted'], 4)
            self.assertEqual(rec['treated_post_outcome_values_converted'], 0)
            self.assertEqual(rec['actual_data_estimation_calls'], 0)

    def test_metadata_audit_keeps_invalid_numeric_tails_opaque(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, variant, metadata = [root/name for name in ('source.csv', 'variant.csv', 'metadata.csv')]
            source.write_text('unit,year,y\nA,1,not a numeric value\nA,2,opaque\n')
            variant.write_text('unit,year,outcome_value\nA,1,invalid\n')
            metadata.write_text('unit,year\nA,1\n')
            binding = panels.PanelBinding(source, variant, metadata,
                panels.sha(source), panels.sha(variant), panels.sha(metadata))
            self.assertEqual(panels.audit_binding(binding)['outcome_values_parsed'], 0)
            with self.assertRaises(ValueError):
                panels.load_bound_panel(binding)
            variant.write_text('unit,year,outcome_value\nA,1,3\nA,2,4\n')
            binding = panels.PanelBinding(source, variant, metadata,
                panels.sha(source), panels.sha(variant), panels.sha(metadata))
            with self.assertRaisesRegex(RuntimeError, 'inventory mismatch'):
                panels.audit_binding(binding)

    def test_synthetic_calibration_uses_zero_actual_data_calls(self):
        d, rows = invented_fixture()
        structure = [(u, t, 0., g) for u, t, _, g in rows]
        op = calibration.calibration_operators(d, structure, {u: {'policy_status': 'never-treated'}
            for u in d['case']['sample_mask']['include_units']}, permutations=19)
        rec = calibration.calibrate_synthetic('invented', op, .5,
            counts=dict(training=20, power=20, heldout=10, B=9, RI=19), include_power=False)
        self.assertEqual(rec['actual_data_estimation_calls'], 0)
        self.assertEqual(rec['clean_effect'], 0)
        self.assertEqual(rec['synthetic_executor_calls'], 10)
        self.assertEqual(set(rec['critical_values']), set(estimator.CHECKS))

    def test_relabeling_noise_floor_retains_draws_and_prospective_clock_offsets(self):
        d, rows = invented_fixture()
        d['cohorts']['A'] = 7
        d['operative_years']['A'] = 6
        d['partial_periods_dropped'] = [dict(unit='A', year=6)]
        d['cohorts']['G'] = 16
        d['operative_years']['G'] = 16
        d['never_controls'].remove('G')
        _, design, outcomes = fit_primary(d, rows, B=9)
        labels = ri.predraw_labels(design, 1)[0]
        fake = ri.relabeled_design(d, design, labels)
        self.assertEqual(fake['mask'], design['mask'])
        future = next(u for u in fake['units'] if fake['adoption'][u] == 16)
        self.assertFalse(fake['whole_window_clean'][future])
        self.assertTrue(all(future not in c['controls'] for c in fake['cells']))
        shifted = next(u for u in fake['units'] if fake['adoption'][u] == 7)
        self.assertEqual(fake['operative'][shifted], 6)
        # This fixture's altered supported lead family may fail the prospective
        # RI gate; do not redraw or invent a smaller lead family to rescue it.
        clean_d, clean_rows = invented_fixture()
        _, clean_design, clean_outcomes = fit_primary(clean_d, clean_rows, B=9)
        ref = ri.permutation_reference(clean_d, clean_design, clean_outcomes, count=999)
        summary = noise.noise_summary(ref, .5)
        self.assertEqual(summary['draws'], ref['feasibility']['feasible_draws'])
        self.assertEqual(summary['permutation_draws'], ref['overall'])
        self.assertFalse(summary['quantiles_are_confidence_interval'])
        self.assertEqual(ref['feasibility']['redrawn'], 0)
        self.assertEqual(summary['absolute_cutoff'], sorted(abs(v) for v in ref['overall'])[949])


if __name__ == '__main__':
    unittest.main()
