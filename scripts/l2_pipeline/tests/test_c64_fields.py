"""Synthetic-only checks of C64 registered numerical exports."""
import copy
import statistics
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.l3_addendum import observed_fields, placebo_fields


def fixture():
    units = ['A', 'B', 'C', 'X', 'Y']
    adoption = {u: 6 if u in 'ABC' else None for u in units}
    periods = list(range(12))
    design = dict(units=units, periods=periods, groups=[6], adoption=adoption,
                  mask={(u, t) for u in units for t in periods},
                  exposure={u: {t: dict(clean=adoption[u] is None or t < adoption[u])
                                for t in periods} for u in units}, cells=[])
    for t in range(6, 12):
        design['cells'].append(dict(g=6, t=t, event_time=t-6, base_period=5,
                                    treated=['A', 'B', 'C'], controls=['X', 'Y'],
                                    n_treated=3, post=True))
    outcomes = {(u, t): float(t + i + (4 if u == 'A' and 3 <= t <= 5 else 0))
                for i, u in enumerate(units) for t in periods}
    return dict(period_years=list(range(2000, 2012))), design, outcomes


class AddendumFieldsTests(unittest.TestCase):
    def test_pooled_sample_sd_and_balanced_year_horizon(self):
        declaration, design, outcomes = fixture()
        fields = observed_fields(declaration, design, outcomes, 1)
        values = [v for (u, t), v in outcomes.items() if u in ('X', 'Y') or t < 6]
        self.assertAlmostEqual(fields['s_pre'], statistics.stdev(values))
        self.assertEqual(fields['s_pre_n'], 42)
        self.assertEqual(fields['s_pre_ddof'], 1)
        self.assertEqual(fields['H'], 6)
        self.assertEqual(fields['balanced_profile']['cohorts'], [2006])
        self.assertEqual(fields['balanced_profile']['event_times'], list(range(6)))

    def test_trough_exact_year_demeaning_and_sign(self):
        declaration, design, outcomes = fixture()
        row = observed_fields(declaration, design, outcomes, 1)['trough'][0]
        self.assertEqual(row['base_years'], [2000, 2001, 2002])
        self.assertEqual(row['recent_years'], [2003, 2004, 2005])
        self.assertAlmostEqual(row['base_mean'], -2.)
        self.assertAlmostEqual(row['recent_mean'], 1.2)
        self.assertAlmostEqual(row['depth'], -3.2)
        opposite = observed_fields(declaration, design, outcomes, -1)['trough'][0]
        self.assertAlmostEqual(opposite['depth'], 3.2)
        for direction, status in ((0, 'DIRECTION_UNDETERMINED'), (None, 'FIELD_MISSING')):
            unclear = observed_fields(declaration, design, outcomes, direction)['trough'][0]
            self.assertIsNone(unclear['depth'])
            self.assertEqual(unclear['status'], status)

    def test_trough_is_invariant_to_all_post_values(self):
        declaration, design, outcomes = fixture()
        original = observed_fields(declaration, design, outcomes, 1)
        changed = dict(outcomes)
        for key in changed:
            if key[1] >= 6:
                changed[key] = -999999.
        self.assertEqual(original['trough'], observed_fields(declaration, design, changed, 1)['trough'])

    def test_no_treated_post_value_is_read_by_observed_fields(self):
        declaration, design, outcomes = fixture()
        class PreOnly(dict):
            def __getitem__(self, key):
                if key[0] in 'ABC' and key[1] >= 6:
                    raise AssertionError('Post value read')
                return super().__getitem__(key)
        observed_fields(declaration, design, PreOnly(outcomes), 1)

    def test_three_baseline_years_and_full_recent_window_required(self):
        declaration, design, outcomes = fixture()
        design['mask'].remove(('A', 0))
        row = observed_fields(declaration, design, outcomes, 1)['trough'][0]
        self.assertIsNone(row['depth'])
        self.assertIn('three clean', row['reason'])
        declaration, design, outcomes = fixture()
        design['mask'].remove(('A', 4))
        row = observed_fields(declaration, design, outcomes, 1)['trough'][0]
        self.assertIsNone(row['depth'])
        self.assertIn('Incomplete clean', row['reason'])

    def test_no_post_contamination_in_full_u_demeaning(self):
        declaration, design, outcomes = fixture()
        design['adoption']['X'] = 3
        for t in range(3, 12):
            design['exposure']['X'][t]['clean'] = False
        rows = observed_fields(declaration, design, outcomes, 1)['trough']
        self.assertTrue(all(row['depth'] is None for row in rows))
        self.assertTrue(all('post/exposed' in row['reason'] for row in rows))

    def test_calendar_gaps_do_not_become_years_or_balanced_profile(self):
        declaration, design, outcomes = fixture()
        declaration['period_years'] = list(range(2000, 2024, 2))
        fields = observed_fields(declaration, design, outcomes, 1)
        self.assertIsNone(fields['H'])
        self.assertTrue(all(row['depth'] is None for row in fields['trough']))
        self.assertTrue(all('Incomplete clean' in row['reason'] for row in fields['trough']))
        profile = placebo_fields(declaration, design, outcomes, 1)['group_time']
        self.assertEqual([r['event_time'] for r in profile], [0, 2, 4])

    def test_profile_contrast_no_fit_and_original_inputs_unchanged(self):
        declaration, design, outcomes = fixture()
        original = copy.deepcopy((declaration, design, outcomes))
        with patch('scripts.l2_pipeline.engine.estimator.estimate_design', side_effect=AssertionError('fit forbidden')):
            profile = placebo_fields(declaration, design, outcomes, 1)['group_time']
        self.assertEqual(len(profile), 6)
        self.assertEqual(profile[0]['g'], 2006)
        self.assertEqual(profile[0]['n_treated'], 3)
        self.assertAlmostEqual(profile[0]['att'], -4 / 3)
        self.assertEqual((declaration, design, outcomes), original)

    def test_fixed_cohort_support_holes_are_explicit(self):
        declaration, design, outcomes = fixture()
        design['cells'] = [c for c in design['cells'] if c['event_time'] != 2]
        fields = observed_fields(declaration, design, outcomes, 1)
        self.assertEqual(fields['H'], 2)
        self.assertEqual(fields['balanced_profile']['E'], 5)
        self.assertEqual(fields['balanced_profile']['common_event_times'], [0, 1, 3, 4, 5])

    def test_placebo_trough_cannot_reuse_original_post_as_fake_pre(self):
        declaration, original, outcomes = fixture()
        fake = copy.deepcopy(original)
        fake['adoption']['X'] = 9
        for u in 'ABC':
            fake['adoption'][u] = None
            for t in range(12):
                fake['exposure'][u][t]['clean'] = True
        fake['groups'] = [9]
        fake['cells'] = []  # Isolate trough; profile necessarily reads post ATT cells.
        for t in range(12):
            fake['exposure']['X'][t]['clean'] = t < 9
        changed = dict(outcomes)
        for key in changed:
            if key[0] in 'ABC' and key[1] >= 6:
                changed[key] = float('nan')
        result = placebo_fields(declaration, fake, changed, 1, original_design=original)
        self.assertIsNone(result['trough'][0]['depth'])
        self.assertIn('post/exposed', result['trough'][0]['reason'])

    def test_registered_universe_cannot_silently_shrink_to_estimation_units(self):
        declaration, design, outcomes = fixture()
        declaration['l3_universe'] = design['units'] + ['ABSENT']
        rows = observed_fields(declaration, design, outcomes, 1)['trough']
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row['status'], 'SUPPORT')
            self.assertIsNone(row['base_mean'])
            self.assertIsNone(row['recent_mean'])
            self.assertIsNone(row['depth'])
            self.assertIn('Full U_i', row['reason'])
        placebo = placebo_fields(declaration, design, outcomes, 1, original_design=design)
        self.assertEqual(placebo['trough'], rows)

    def test_empty_registered_universe_fails_without_reading_outcomes(self):
        from scripts.l2_pipeline.l3_addendum import _trough
        declaration, design, _ = fixture()
        declaration['l3_universe'] = []
        class NoRead(dict):
            def __getitem__(self, key):
                raise AssertionError('Unknown U_i must not read outcomes')
        rows = _trough(declaration, design, NoRead(), 1)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row['status'], 'SUPPORT')
            self.assertEqual(row['reason'], 'Registered U_i unavailable')
            self.assertIsNone(row['base_mean'])
            self.assertIsNone(row['recent_mean'])
            self.assertIsNone(row['depth'])

    def test_small_sd_sample_invalid_direction_and_nonfinite_fail_closed(self):
        declaration, design, outcomes = fixture()
        design['mask'] = {('A', 0)}
        self.assertIsNone(observed_fields(declaration, design, outcomes, 0)['s_pre'])
        for direction in (True, 2, '1'):
            with self.assertRaises(ValueError):
                observed_fields(declaration, design, outcomes, direction)
        outcomes['A', 0] = float('nan')
        with self.assertRaises(ValueError):
            observed_fields(declaration, design, outcomes, 0)


if __name__ == '__main__':
    unittest.main()
