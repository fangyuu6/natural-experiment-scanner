"""Synthetic V10 threshold/repeal/cap checks; no real outcomes or fits."""
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.intake import c36_intake as intake
from scripts.l2_pipeline.sources.research_panel.core import STATES, history


class C36ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.rule = dict(class_='a', minimum=0, allowed_source_codes=[0, 0.5, 1, 2])
        self.rule['class'] = self.rule.pop('class_')

    def test_local_policy_and_statewide_policy_are_same_adoption(self):
        self.assertEqual([intake.binary_value(v, self.rule) for v in [0, 0.5, 1, 2]], [0, 1, 1, 1])

    def test_missing_never_becomes_absence(self):
        self.assertIsNone(intake.binary_value(None, self.rule))

    def test_age_coding_does_not_become_policy_adoption(self):
        rule = dict(self.rule, allowed_source_codes=[0, 1])
        self.assertIsNone(intake.binary_value(14, rule))
        self.assertIsNone(intake.binary_value(15, rule))

    def test_below_documented_minimum_is_not_absence(self):
        with self.assertRaises(ValueError):
            intake.binary_value(-1, self.rule)

    def test_repeal_and_readoption_preserve_both_spells(self):
        v = dict(policy_id='synthetic:policy', kind='binary_adoption', definition='any version', clock='source annual stock',
                 coverage=dict(start_year=1980, end_year=1986))
        values = [0, 0.5, 2, 0, 1, 1, 1]
        rows = [dict(policy_id=v['policy_id'], state=u, year=1980 + i,
                     value=intake.binary_value(value if u == 'AL' else 0, self.rule))
                for u in STATES for i, value in enumerate(values)]
        imported = history(rows, v, [1980, 1986])
        state = next(r for r in imported['records'] if r['unit'] == 'AL')
        self.assertEqual(state['start_period'], 1981)
        self.assertEqual(state['reversal_years'], [1983])
        self.assertEqual(state['intervals'], [dict(start_period=1981, end_period=1982), dict(start_period=1984, end_period=1986)])
        self.assertTrue(state['primary_excluded'])
        self.assertFalse(state['never_control_eligible'])
        self.assertEqual([r['year'] for r in imported['events'] if r['unit'] == 'AL'], [1981, 1983, 1984])

    def test_prior_waves_consume_capacity(self):
        ledger = dict(families={'firearms': dict(prior_distinct_design_lower_bound=6),
                                'new_family': dict(prior_distinct_design_lower_bound=4)})
        self.assertEqual(intake.capacity('firearms', ledger), 0)
        self.assertEqual(intake.capacity('new_family', ledger), 2)
        self.assertEqual(intake.capacity('unseen_family', ledger), 6)

    def test_overfull_legacy_family_never_adds_new_slots(self):
        self.assertEqual(intake.capacity('road_safety', dict(families={'road_safety': dict(prior_distinct_design_lower_bound=11)})), 0)

    def test_empty_wave_cannot_dispatch_even_with_host_flag(self):
        with patch.object(intake, 'read', return_value=dict(summary=dict(emitted=0))), patch.object(intake.outcome_v7, 'register'):
            with self.assertRaisesRegex(ValueError, 'no launchable jobs'):
                intake.dispatch(['S0', '--jobs-root', str(intake.ROOT / 's0_jobs'), '--hub-outside-sandbox', '--max-concurrency', '4'])

    def test_empty_wave_cannot_cross_stage_roots(self):
        with patch.object(intake, 'read', return_value=dict(summary=dict(emitted=0))), patch.object(intake.outcome_v7, 'register'):
            with self.assertRaisesRegex(ValueError, 'Stage/root mismatch'):
                intake.dispatch(['S1', '--jobs-root', str(intake.ROOT / 's0_jobs'), '--hub-outside-sandbox', '--max-concurrency', '4', '--preflight-only'])


if __name__ == '__main__':
    unittest.main()
