"""Prospective IPEDS coverage sensitivity is registered before panel construction."""
import copy
import unittest
from scripts.l2_pipeline.connectors.rates import prospective_card
from scripts.l2_pipeline.connectors.common import ConnectorError
from scripts.l2_pipeline.variants import registered_cards,registered_names
from scripts.l2_pipeline.tests.test_pipeline import card


class CoverageRegistration(unittest.TestCase):
    def fixture(self,version=15):
        value=card();value['outcome'].update(connector=f'outcome_v{version}_ipeds_enrollment_per_population',
            series_id='ipeds_enrollment_per_population',measure_id='ipeds_enrollment_per_population',outcome_unit='per 100,000 population')
        return value

    def test_new_card_gets_exact_99_variant_once_without_source_mutation(self):
        source=self.fixture();before=copy.deepcopy(source)
        value=prospective_card(source)
        self.assertEqual(source,before);self.assertEqual(prospective_card(value),value)
        self.assertIn('ipeds_coverage_99',registered_names(value))
        variants=registered_cards(value)
        self.assertEqual(variants['primary']['outcome']['institution_coverage_min'],.95)
        self.assertEqual(variants['ipeds_coverage_99']['outcome']['institution_coverage_min'],.99)

    def test_old_bindings_unchanged_and_conflicts_rejected(self):
        old=self.fixture(14)
        self.assertNotIn('ipeds_coverage_99',registered_names(prospective_card(old)))
        value=self.fixture();value['sensitivities'].append(dict(name='ipeds_coverage_99',changes={}))
        with self.assertRaises(ConnectorError):prospective_card(value)
