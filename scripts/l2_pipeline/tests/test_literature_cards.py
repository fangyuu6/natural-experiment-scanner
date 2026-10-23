"""Metadata-only collected literature intake: construct/window failures park."""
import copy
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.cards import validate_card
from scripts.l2_pipeline.literature_cards import literature_record_to_card


def record():
    url = 'https://author.example/paper.pdf'
    return dict(status='VERIFIED', study_id='invented_open_paper', doi='10.0000/example',
        policy_id='invented_road_rule', policy_definition='Located graduated licensing rule',
        target_outcome='teen_traffic_deaths_count', primary_outcome='teen_traffic_deaths_count',
        target_population='Persons ages 15--17', population='Persons ages 15--17',
        policy_evidence={'source_url': url, 'locator': 'PDF p2, policy definition'},
        outcome_evidence={'source_url': url, 'locator': 'PDF p8, Table 4, population'},
        study_window={'start': 1988, 'end': 2004, 'source_url': url, 'locator': 'PDF p8, Table 4 note'},
        adoption={'states': [{'state': 'California', 'year': 1995, 'source_url': url,
                              'locator': 'PDF p3, Table 1, California'}],
                  'universe': ['CA', 'AZ', 'NY'], 'reported_scope_complete': True,
                  'complete_list': False, 'coverage_start': 1988, 'coverage_end': 2002,
                  'source_url': url, 'locator': 'PDF p3, Table 1, full roster'},
        outcome_mapping={'population_filters': {'age_min': 15, 'age_max': 17},
                         'source_population_filters': {'age_min': 15, 'age_max': 17},
                         'outcome_unit': 'fatal persons per state-year',
                         'source_url': url, 'locator': 'PDF p8, Table 4, population'})


class LiteratureCardsTests(unittest.TestCase):
    def test_unique_exact_mapping_and_bounded_card_without_source_reads(self):
        value = record()
        before = copy.deepcopy(value)
        # Only repository-owned code configuration may be read. Raw source,
        # outcome, literature-record and result paths remain forbidden, even
        # when their basenames imitate a permitted configuration file.
        from pathlib import Path
        from scripts.l2_pipeline.connectors.rates import source_units
        source_units.cache_clear()
        code_root = Path(__file__).resolve().parents[1]
        allowed = {code_root / 'design_card.schema.json'} | {
            code_root / 'connectors' / name for name in (
                'LEGACY_OUTCOME_SCALES.json', 'OUTCOME_RATE_CATALOG.json',
                'OUTCOME_CATALOG_V2.json', 'OUTCOME_CATALOG_V3.json', 'OUTCOME_CATALOG_V4.json')}
        real_read = Path.read_text
        def only_schema(path, *args, **kwargs):
            self.assertIn(path.resolve(), allowed)
            return real_read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', only_schema), \
             patch('scripts.l2_pipeline.connectors.construct', side_effect=AssertionError('outcome access')):
            result = literature_record_to_card(value, record_ref='/not-opened/LITERATURE_RECORD.json')
        self.assertEqual(result['disposition'], 'CARD', result['reason'])
        self.assertEqual(value, before)
        card = result['card']
        self.assertEqual(validate_card(card), [])
        self.assertEqual(card['outcome']['connector'], 'fars_state')
        self.assertEqual(card['outcome']['years'], [1988, 2002])
        self.assertEqual(card['treatment']['reported_adoption_records'][0]['state'], 'CA')
        self.assertEqual(card['outcome']['paths'], [])
        self.assertIn('remain unknown until S1', card['treatment']['historical_convention'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(card['automation']['literature_record']['s1_certified'])
        self.assertFalse(result['first_order_gate']['passed'])
        self.assertEqual(card['first_order']['evidence_status'], 'collector_verified_paper_record')
        self.assertNotIn('path', card['sources'][0])
        self.assertNotIn('sha256', card['sources'][0])
        self.assertTrue(card['automation']['literature_record']['requires_purpose_verification'])

    def test_published_numbers_signs_and_uncertainty_never_change_selection(self):
        value = record()
        first = literature_record_to_card(value)
        value.update(expected_sign='negative', estimate=99999, p_value=0.00001,
                     published_results=[{'estimate_as_reported': '-99999', 'se_as_reported': '0.0001'}])
        value['outcome_mapping']['paths'] = ['/forbidden/results.csv']
        self.assertEqual(literature_record_to_card(value), first)

    def test_descriptive_connector_lead_does_not_override_exact_catalog_binding(self):
        value = record()
        value['connector_lead'] = 'Public road safety data if available'
        result = literature_record_to_card(value)
        self.assertEqual(result['disposition'], 'CARD', result['reason'])
        self.assertEqual(result['card']['outcome']['connector'], 'fars_state')

    def test_unverified_and_missing_mapping_park_with_reasons(self):
        for alter, reason in ((lambda r: r.update(status='UNRESOLVED'), 'collector-verified'),
                             (lambda r: r.pop('outcome_mapping'), 'filters required'),
                             (lambda r: r['adoption'].pop('universe'), 'paper state universe'),
                             (lambda r: r['adoption'].pop('reported_scope_complete'), 'adoption scope'),
                             (lambda r: r['study_window'].pop('locator'), 'page/table locators')):
            value = record()
            alter(value)
            result = literature_record_to_card(value)
            self.assertEqual(result['disposition'], 'PARK')
            self.assertIn(reason, result['reason'])
            self.assertIsNone(result['card'])

    def test_proposed_employment_substitution_cannot_hide_road_target(self):
        value = record()
        value['outcome_mapping'].update(target_outcome='employment', primary_outcome='employment',
                                        connector='cps_monthly')
        result = literature_record_to_card(value)
        self.assertEqual(result['disposition'], 'PARK')
        self.assertIn('verbatim paper definition', result['reason'])

    def test_canonical_mapping_requires_verbatim_binding_and_catalog_measure(self):
        value = record()
        value.update(target_outcome='Teen traffic fatalities', primary_outcome='Teen traffic fatalities')
        value['outcome_mapping'].update(target_outcome='teen_traffic_deaths_count',
            primary_outcome='teen_traffic_deaths_count', target_definition='Teen traffic fatalities',
            primary_definition='Teen traffic fatalities')
        self.assertEqual(literature_record_to_card(value)['disposition'], 'CARD')
        value['outcome_mapping']['primary_outcome'] = 'employment'
        self.assertEqual(literature_record_to_card(value)['disposition'], 'PARK')

    def test_source_and_mapping_locator_cannot_disagree(self):
        value = record()
        value['outcome_mapping']['locator'] = 'Another table with another construct'
        result = literature_record_to_card(value)
        self.assertEqual(result['disposition'], 'PARK')
        self.assertIn('verified outcome evidence locator', result['reason'])

    def test_no_connector_window_overlap_parks_and_preserves_coverage(self):
        value = record()
        value['study_window'].update(start=1960, end=1970)
        value['adoption'].update(coverage_start=1960, coverage_end=1970)
        before = copy.deepcopy(value)
        result = literature_record_to_card(value)
        self.assertEqual(result['disposition'], 'PARK')
        self.assertEqual(result['coverage_window']['status'], 'empty')
        self.assertIn('No requested outcome periods overlap', result['reason'])
        self.assertEqual(value, before)

    def test_duplicate_states_and_invented_state_universe_park(self):
        value = record()
        value['adoption']['states'].append(dict(value['adoption']['states'][0], year=1996))
        self.assertIn('duplicate', literature_record_to_card(value)['reason'])
        value = record()
        value['adoption']['universe'].append('Atlantis')
        self.assertIn('paper state universe', literature_record_to_card(value)['reason'])
        value = record()
        value['adoption']['states'].append(dict(value['adoption']['states'][0], state='CA'))
        self.assertIn('duplicate', literature_record_to_card(value)['reason'])

    def test_connector_overlap_cannot_drop_early_cohort_to_pass_preperiod_support(self):
        value = record()
        value['study_window'].update(start=1970, end=2000)
        value['adoption'].update(coverage_start=1970, coverage_end=2000)
        value['adoption']['states'][0]['year'] = 1983
        value['adoption']['states'].append(dict(value['adoption']['states'][0], state='AZ', year=1995))
        before = copy.deepcopy(value)
        result = literature_record_to_card(value)
        self.assertEqual(result['disposition'], 'PARK')
        self.assertEqual(result['coverage_window']['effective_years'], [1982, 2000])
        self.assertEqual(result['window_support']['earliest_reported_cohort'], 1983)
        self.assertFalse(result['window_support']['early_cohorts_dropped'])
        self.assertIn('no early cohort may be dropped', result['reason'])
        self.assertEqual(value, before)

    def test_broader_population_needs_same_construct_dilution_proxy(self):
        value = record()
        value['outcome_mapping']['target_population_gap'] = True
        result = literature_record_to_card(value)
        self.assertEqual(result['disposition'], 'PARK')
        value['outcome_mapping'].update(relation='closest_proxy',
            proxy_justification='Broader published age group measures the same target with dilution.')
        self.assertEqual(literature_record_to_card(value)['disposition'], 'CARD')

    def test_sanitized_study_identifiers_remain_distinct(self):
        one, two = record(), record()
        one['study_id'], two['study_id'] = 'A/B', 'A B'
        self.assertNotEqual(literature_record_to_card(one)['card']['design_id'],
                            literature_record_to_card(two)['card']['design_id'])


if __name__ == '__main__':
    unittest.main()
