"""V2 registry checks: metadata and target bindings, without fitting outcomes."""
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady, ConnectorRequest
from scripts.l2_pipeline.connectors import outcome_v2


class RegistryTests(unittest.TestCase):
    def test_all_catalog_series_match_adapter_metadata(self):
        data = outcome_v2.catalog()
        self.assertTrue(data['series'])
        for sid, spec in data['series'].items():
            adapter = outcome_v2._adapter(sid)
            self.assertIn(sid, adapter.SERIES)
            for key in ('geography', 'units', 'population', 'release_lag', 'source_url'):
                self.assertEqual(spec[key], adapter.SERIES[sid][key])
            self.assertTrue(spec['years'])
            self.assertEqual(spec['years'], sorted(set(spec['years'])))
            self.assertIn('license', spec)
            self.assertIn('license_url', spec)

    def test_first_order_targets_bind_exact_populations(self):
        targets = outcome_v2.catalog()['targets']
        self.assertIn('cdc_firearm_deaths', targets['firearm_law']['series'])
        self.assertIn('cps_teen_weekly_earnings', targets['minimum_wage']['series'])
        self.assertIn('cps_young_adult_employment', targets['minimum_wage']['series'])
        self.assertIn('cps_single_mother_employment', targets['tanf_time_limits']['series'])
        self.assertIn('acs1_single_mother_median_family_income', targets['tanf_time_limits']['series'])
        self.assertIn('cps_single_mother_employment', targets['eitc']['series'])
        self.assertIn('acs1_snap_household_receipt_rate', targets['snap_policies']['series'])
        self.assertEqual(targets['snap_policies']['food_insecurity']['status'], 'not_implemented')

    def test_missing_unknown_and_treatment_inputs_refused(self):
        req = ConnectorRequest({}, {}, (2023,), ('CA',), 'annual', Path.cwd())
        with self.assertRaises(ConnectorError):
            outcome_v2.construct(req)
        with self.assertRaises(ConnectorNotReady):
            outcome_v2._adapter('made_up')
        bad = ConnectorRequest({'series_id': 'cps_teen_employment', 'party': 'example'},
                               {}, (2023,), ('CA',), 'annual', Path.cwd())
        with self.assertRaises(ConnectorError):
            outcome_v2.construct(bad)

    def test_acquire_validation_happens_before_network(self):
        for years in [(), (2023, 2022), (2023, 2023), ('2023',)]:
            with self.assertRaises(ConnectorError):
                outcome_v2.acquire('cps_teen_employment', years, Path('unused'))


if __name__ == '__main__':
    unittest.main()
