"""Prospective scale and legacy-byte regression; no fitted associations."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.cards import load_card, validate_card
from scripts.l2_pipeline.connectors import construct
from scripts.l2_pipeline.connectors.common import (ConnectorError, ConnectorNotReady,
    ConnectorRequest, PanelData, metadata, row, sha)
from scripts.l2_pipeline.connectors.rates import (ROOT, LEGACY, apply_scale, card_hash,
    count_kind, legacy_card, prospective_card, resolved_scale, _census_total)
from scripts.l2_pipeline.variants import registered_cards, secondary_card


def panel(value=20):
    record = row('AL', 2001, value, 'deaths')
    return PanelData((record,), (metadata(record, value not in ('', None)),))


class OutcomeRateTests(unittest.TestCase):
    def request(self, outcome, **card_fields):
        return ConnectorRequest.from_card(dict(design_id='new_rate_design', version='v1',
            outcome=dict(connector='standardized_csv', measure_id='mortality_deaths_count',
                         years=[2001, 2001], units=['AL'], **outcome), **card_fields), ROOT)

    def test_new_hand_authored_count_defaults_rate_and_explicit_count_preserved(self):
        request = self.request({})
        self.assertEqual(request.options['outcome_scale'], 'rate')
        original = panel()
        count = replace(request, options=dict(request.options, outcome_scale='count'))
        self.assertIs(apply_scale(count, original), original)
        archival_options = {k: v for k, v in request.options.items() if k != 'outcome_scale'}
        self.assertNotIn('outcome_scale', ConnectorRequest.from_card(
            {'outcome': archival_options}, ROOT).options)

    def test_source_identity_prevents_misleading_rate_label_bypass(self):
        for sid in ('wonder_firearm_deaths', 'cdc_firearm_deaths', 'mortality_deaths_count'):
            self.assertEqual(count_kind(dict(series_id=sid, outcome_unit='deaths per 100,000')), 'deaths')
        self.assertIsNone(count_kind(dict(series_id='wonder_fertility_rate_by_maternal_age', outcome_unit='count')))
        self.assertEqual(count_kind(dict(series_id='bls_ces_private_employment')), 'events')

    def test_unsupported_secondary_null_unit_preserves_parked_metadata(self):
        base = json.loads((ROOT / 'scripts/l2_pipeline/examples/design.json').read_text())
        base.update(design_id='prospective_null_secondary')
        base['outcome']['outcome_unit'] = None
        entry = dict(role='secondary', outcome='not_implemented', connector='not_implemented')
        result = secondary_card(base, entry)
        self.assertIsNone(count_kind(result['outcome']))
        self.assertIsNone(resolved_scale(result))
        self.assertIn('_secondary_unsupported_reason', result)
        self.assertIsNone(result['outcome']['outcome_unit'])

    def test_card_declares_rate_units_and_native_fertility_denominator(self):
        card = prospective_card(dict(design_id='new', outcome=dict(series_id='nchs_summary_births', outcome_unit='live births')))
        self.assertEqual(card['outcome_scale'], 'rate')
        self.assertIn('15–44', card['outcome']['outcome_unit'])
        self.assertEqual(card['outcome']['source_outcome_unit'], 'live births')

    def test_scale_enum_and_sensitivity_secondary_resolution(self):
        base = json.loads((ROOT / 'scripts/l2_pipeline/examples/design.json').read_text())
        base.update(design_id='new_rate_schema', outcome_scale='invalid')
        self.assertTrue(validate_card(base))
        base['outcome_scale'] = 'rate'
        secondary = secondary_card(base, dict(role='secondary', outcome='cdc_firearm_deaths',
            connector='outcome_v2_cdc_firearm_deaths'))
        self.assertEqual(secondary['outcome_scale'], 'rate')
        self.assertIn('per 100,000', secondary['outcome']['outcome_unit'])

    def test_legacy_registered_cards_and_variants_exact_semantics_preserved(self):
        catalog = json.loads(LEGACY.read_text())['cards']
        self.assertGreaterEqual(len(catalog), 242)
        checked = 0
        for digest, record in catalog.items():
            path = ROOT / record['card_path']
            if not path.is_file():
                continue
            original_bytes = path.read_bytes()
            original = json.loads(original_bytes)
            variant = registered_cards(original)[record['variant']]
            self.assertEqual(card_hash(variant), digest)
            self.assertTrue(legacy_card(variant))
            self.assertEqual(prospective_card(variant), variant)
            self.assertEqual(path.read_bytes(), original_bytes)
            changed = copy.deepcopy(variant)
            changed['outcome']['years'] = [2098, 2099]
            self.assertFalse(legacy_card(changed))
            checked += 1
        self.assertGreaterEqual(checked, 242)

    def test_wonder_mortality_population_seam_public_routing_and_direct_api(self):
        from scripts.l2_pipeline.connectors.wonder_registry import catalog, construct as direct
        sid = 'wonder_firearm_deaths'
        spec = catalog()['series'][sid]
        outcome = dict(connector='outcome_v4_' + sid, series_id=sid, measure_id=sid,
            population_filters=spec['population_filters'], paths=spec['local_source_paths'],
            years=[2017, 2018], units=['AL'])
        request = ConnectorRequest.from_card(dict(design_id='prospective_seam', outcome=outcome), ROOT)
        prospective = construct(request)
        direct_panel = direct(request)
        self.assertEqual(prospective, direct_panel)
        count = construct(replace(request, options=dict(request.options, outcome_scale='count')))
        for rate, original in zip(prospective.rows, count.rows):
            self.assertAlmostEqual(rate['outcome_value'], original['outcome_value'] / rate['population_denominator'] * 100000)
            self.assertEqual(rate['rate_multiplier'], 100000)
        self.assertNotIn('outcome_scale', count.rows[0])

    def test_wonder_births_exact_female_population_and_d10_blocker(self):
        from scripts.l2_pipeline.connectors.wonder_registry import catalog
        sid = 'wonder_births_by_maternal_age'
        spec = catalog()['series'][sid]
        outcome = dict(connector='outcome_v4_' + sid, series_id=sid, measure_id=sid,
            population_filters={'age_group': '20-24'}, paths=spec['local_source_paths'], years=[2003, 2003], units=['AL'])
        request = ConnectorRequest.from_card(dict(design_id='prospective_births', outcome=outcome), ROOT)
        result = construct(request)
        self.assertEqual(result.rows[0]['rate_multiplier'], 1000)
        self.assertGreater(result.rows[0]['population_denominator'], 0)
        for years, age in [([1999, 1999], '20-24'), ([2003, 2003], 'under15')]:
            bad = dict(outcome, years=years, population_filters={'age_group': age})
            with self.assertRaisesRegex(ConnectorNotReady, 'Population denominator unavailable'):
                construct(ConnectorRequest.from_card(dict(design_id='new_missing_female', outcome=bad), ROOT))

    def test_unreliable_source_rate_never_rescued_and_suppression_preserved(self):
        request = self.request({'series_id': 'wonder_firearm_deaths'})
        with patch('scripts.l2_pipeline.connectors.rates._wonder_denominators',
                   return_value=({('AL', 2001): 100000}, {('AL', 2001): {'unreliable': True}})):
            result = apply_scale(request, panel(15))
            self.assertEqual(result.rows[0]['outcome_value'], '')
            self.assertEqual(result.metadata[0]['available'], 0)
            self.assertTrue(result.metadata[0]['unreliable'])
            suppressed = apply_scale(request, panel(''))
            self.assertEqual(suppressed.rows[0]['outcome_value'], '')
            with patch('scripts.l2_pipeline.connectors.rates._declared_denominators',
                       return_value=({('AL', 2001): 900000}, ())):
                explicit = apply_scale(request, panel(15))
                self.assertEqual(explicit.rows[0]['outcome_value'], '')
                self.assertTrue(explicit.metadata[0]['unreliable'])

    def test_generic_census_population_all_decades_from_existing_disk(self):
        request = self.request({})
        request = replace(request, years=(1981, 1999, 2001, 2011, 2021))
        values, inventory = _census_total(request)
        self.assertEqual(len(inventory), 5)
        self.assertTrue(all(values['AL', year] > 0 for year in request.years))
        self.assertTrue(all(item['url'] and item['sha256'] and item['license'] for item in inventory))

    def test_declared_denominator_hash_scope_and_sex_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pop.csv'
            path.write_text('unit,year,population\nAL,2001,1000000\n')
            config = dict(paths=[str(path)], source_sha256={str(path): sha(path)},
                source_urls={str(path): 'https://www.census.gov/population.csv'},
                license='US federal government public domain', population_filters={}, sex='all')
            result = apply_scale(self.request({'population_denominator': config}), panel())
            self.assertAlmostEqual(result.rows[0]['outcome_value'], 2)
            for change in ({'population_filters': {'age_group': '15-24'}}, {'sex': 'female'},
                           {'source_sha256': {}}, {'source_urls': {'wrong.csv': 'https://www.census.gov'}}):
                with self.assertRaises(ConnectorError):
                    apply_scale(self.request({'population_denominator': dict(config, **change)}), panel())

    def test_age_specific_mortality_does_not_use_total_population(self):
        for filters in ({'age_min': 0, 'age_max': 14}, {'race': 'Black'}, {'sex': 'female'},
                        {'population': 'black_residents'}, {'income_group': 'low_income'}):
            request = replace(self.request({}), filters=filters)
            with self.assertRaisesRegex(ConnectorNotReady, 'exact age/sex'):
                apply_scale(request, panel())


if __name__ == '__main__':
    unittest.main()
