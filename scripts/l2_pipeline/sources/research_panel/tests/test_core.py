import json
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline.sources.research_panel.core import STATES, canonicalize, digest, history, threshold_rule
from scripts.l2_pipeline.sources.research_panel.registration import register


def definition(kind='binary_adoption'):
    return dict(policy_id='test:law', source_id='test', definition='Test law presence',
        kind=kind, unit='indicator' if kind == 'binary_adoption' else 'USD', clock='annual stock', outcomes=['declared direct measure'])


class Histories(unittest.TestCase):
    def test_zero_missing_repeal_and_left_censor_survive(self):
        rows = [dict(state=s, year=y, policy_id='test:law', value=0) for s in STATES for y in range(2000, 2005)]
        for row in rows:
            if row['state'] == 'CA':
                row['value'] = {2000: 0, 2001: 1, 2002: 0, 2003: 1, 2004: 1}[row['year']]
            if row['state'] == 'TX':
                row['value'] = 1
            if row['state'] == 'WA' and row['year'] == 2002:
                row['value'] = None
        rows = [r for r in rows if (r['state'], r['year']) != ('OR', 2003)]
        canonical, catalog = canonicalize(rows, [definition()])
        self.assertEqual(len(canonical), 250)
        cov = catalog[0]['coverage']
        self.assertEqual(cov['source_missing'], 1)
        self.assertEqual(cov['missing_source_cell'], 1)
        self.assertEqual(cov['complete_50_state_windows'], [[2000, 2001], [2004, 2004]])
        hist = history(canonical, catalog[0], [2000, 2004])
        keyed = {r['unit']: r for r in hist['records']}
        self.assertEqual(keyed['CA']['reversal_years'], [2002])
        self.assertEqual(keyed['CA']['intervals'], [dict(start_period=2001, end_period=2001), dict(start_period=2003, end_period=2004)])
        self.assertTrue(keyed['CA']['primary_excluded'])
        self.assertFalse(keyed['CA']['never_control_eligible'])
        self.assertTrue(keyed['TX']['left_censored'])
        self.assertIsNone(keyed['TX']['start_period'])
        self.assertEqual(keyed['WA']['status'], 'unknown')
        self.assertEqual(keyed['OR']['unknown_years'], [2003])
        self.assertTrue(keyed['AL']['never_control_eligible'])
        self.assertFalse(hist['history_complete'])

    def test_continuous_threshold_is_hash_bound_and_reversible(self):
        var = definition('continuous_intensity')
        rows = [dict(state=s, year=y, policy_id='test:law', value={2000: 3, 2001: 7, 2002: 4}[y])
                for s in STATES for y in range(2000, 2003)]
        canonical, catalog = canonicalize(rows, [var])
        with self.assertRaises(ValueError):
            history(canonical, catalog[0], [2000, 2002])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'threshold.json'
            rule = dict(policy_id='test:law', unit='USD', operator='>=', threshold=5,
                rationale='Example prospective rule', declared_at='2026-10-09', outcome_associations_examined=False)
            path.write_text(json.dumps(rule))
            bound = threshold_rule(path, digest(path), var)
            hist = history(canonical, catalog[0], [2000, 2002], threshold=bound)
            self.assertEqual(hist['records'][0]['start_period'], 2001)
            self.assertEqual(hist['records'][0]['reversal_years'], [2002])
            with self.assertRaises(ValueError):
                threshold_rule(path, '0' * 64, var)
            rule['outcome_associations_examined'] = True
            path.write_text(json.dumps(rule))
            with self.assertRaises(ValueError):
                threshold_rule(path, digest(path), var)

    def test_rectangular_keys_do_not_claim_future_history(self):
        var = dict(definition(), historical_year_end=2025)
        rows = [dict(state=s, year=y, policy_id='test:law', value=0,
            year_status='future_snapshot_unverified' if y == 2027 else 'current_year_partial' if y == 2026 else 'historical_candidate')
            for s in STATES for y in range(2025, 2028)]
        canonical, catalog = canonicalize(rows, [var])
        self.assertEqual(catalog[0]['coverage']['historical_complete_50_state_windows'], [[2025, 2025]])
        self.assertEqual(catalog[0]['coverage']['complete_50_state_windows'], [[2025, 2027]])
        with self.assertRaises(ValueError):
            history(canonical, catalog[0], [2025, 2027])

    def test_invalid_duplicate_nonfinite_and_bool_rejected_under_optimization(self):
        base = dict(state='CA', year=2000, policy_id='test:law', value=0)
        for rows in ([base, base], [dict(base, year=True)], [dict(base, value=float('nan'))], [dict(base, value=True)], [dict(base, value=2)], [dict(base, state='ZZ')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                canonicalize(rows, [definition()])

    def test_additive_registration(self):
        existing = {'policy_tracker': 'other'}
        register(existing)
        self.assertEqual(existing['policy_tracker'], 'other')
        self.assertIn('research_panel', existing)
        with self.assertRaises(ValueError):
            register({'research_panel': 'wrong'})


if __name__ == '__main__':
    unittest.main()
