import unittest
from scripts.l2_pipeline.run2_preparation import prepared_payload
from scripts.l2_pipeline.tests.test_pipeline import card


class PreparationTests(unittest.TestCase):
    def test_historical_adopters_excluded_without_becoming_controls(self):
        source = card()
        source['outcome']['years'] = [2000, 2010]
        source['analysis'] = {'min_pre_periods': 3, 'min_controls': 5, 'min_cohorts': 2}
        source['automation'] = {}
        records = [
            {'unit': 'CA', 'status': 'treated', 'start_period': 1999},
            {'unit': 'NY', 'status': 'treated', 'start_period': 2002},
            {'unit': 'TX', 'status': 'treated', 'start_period': 2003},
            {'unit': 'WA', 'status': 'never_treated', 'start_period': None},
        ]
        result = prepared_payload(source, records)
        self.assertEqual(result['treatment']['excluded_units'], ['CA', 'NY'])
        self.assertEqual(records[0]['status'], 'treated')
        self.assertEqual(source['treatment'], {})
        self.assertFalse(result['analysis']['cohort_eligibility_rule']['outcome_values_used'])
        self.assertEqual(result['analysis']['min_pre_periods'], 3)
        self.assertEqual(len(result['automation']['personas']), 5)

    def test_predeclared_exclusions_preserved(self):
        source = card()
        source['outcome']['years'] = [2000, 2010]
        source['analysis'] = {'min_pre_periods': 3, 'min_controls': 5, 'min_cohorts': 2}
        source['treatment']['excluded_units'] = ['FL']
        source['automation'] = {}
        self.assertEqual(prepared_payload(source, [])['treatment']['excluded_units'], ['FL'])


if __name__ == '__main__':
    unittest.main()
