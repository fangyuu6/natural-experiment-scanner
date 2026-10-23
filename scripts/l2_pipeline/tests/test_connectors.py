"""Construction/support tests; never compute policy outcome associations."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from scripts.l2_pipeline.connectors import (ConnectorError, ConnectorNotReady, ConnectorRequest,
                                          construct, connector_inventory, download_plan, fetch_public)
from scripts.l2_pipeline.connectors.cps import LOC, annualize
from scripts.l2_pipeline.panel_builder import build_panel

ROOT = Path(__file__).resolve().parents[3]


def write_csv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def card(connector='standardized_csv', **options):
    outcome = dict(connector=connector, paths=['source.csv'], years=[2000, 2000], units=['AL'],
                   frequency='annual', population_filters={'age_min': 25},
                   source_population_filters={'age_min': 25})
    outcome.update(options)
    return {'design_id': 'synthetic', 'population': {'filters': outcome['population_filters']}, 'outcome': outcome}


class ConnectorTests(unittest.TestCase):
    def test_standard_panel_preserves_value_and_write_once(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_csv(base / 'source.csv', [dict(unit='AL', year=2000, outcome_value='1.2300')])
            report = build_panel(card(), base, base / 'construction')
            with Path(report['panel_path']).open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]['outcome_value'], '1.2300')
            self.assertEqual(report['outcome_association_calls'], 0)
            self.assertEqual(report['available_cells'], 1)
            with Path(report['metadata_path']).open() as handle:
                support = list(csv.DictReader(handle))
            self.assertEqual((support[0]['state'], support[0]['period'], support[0]['available']), ('AL', '2000', '1'))
            original = Path(report['panel_path']).read_bytes()
            with self.assertRaises(FileExistsError):
                build_panel(card(), base, base / 'construction')
            self.assertEqual(Path(report['panel_path']).read_bytes(), original)

    def test_population_mismatch_and_absent_connector_park(self):
        request = ConnectorRequest.from_card(card(source_population_filters={'age_min': 16}), ROOT)
        with self.assertRaises(ConnectorNotReady):
            construct(request)
        with self.assertRaises(ConnectorNotReady):
            construct(ConnectorRequest.from_card(card('fbi'), ROOT))
        malformed = card()
        malformed['population']['filters'] = {'age_min': 16}
        with self.assertRaises(ConnectorError):
            ConnectorRequest.from_card(malformed, ROOT)

    def test_standard_panel_preserves_source_support_and_linkage_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_csv(base / 'source.csv', [dict(unit='AL', year=2000, outcome_value='1.0')])
            write_csv(base / 'support.csv', [dict(unit='AL', year=2000, annual_usable='0', linkage_certified='0')])
            request = ConnectorRequest.from_card(card(metadata_path='support.csv'), base)
            result = construct(request)
            self.assertEqual(result.metadata[0]['available'], 0)
            self.assertEqual(result.metadata[0]['linkage_certified'], 0)

    def test_unobserved_acs_target_refuses_before_values(self):
        request = ConnectorRequest.from_card(card('acs', population_filters={'undocumented': True}), ROOT)
        with self.assertRaises(ConnectorNotReady):
            construct(request)

    def test_cps_cannot_silently_apply_private_sector_target(self):
        filters = {'civilian_noninstitutional': True, 'age_min': 25, 'age_max': 54,
                   'sector': 'private'}
        request = ConnectorRequest.from_card(card('cps_monthly', population_filters=filters), ROOT)
        with self.assertRaises(ConnectorNotReady):
            construct(request)

    def test_missing_source_and_credential_source_fail_before_read(self):
        request = ConnectorRequest.from_card(card(paths=['absent.csv']), ROOT)
        with self.assertRaises(ConnectorNotReady):
            construct(request)
        request = ConnectorRequest.from_card(card(paths=['.env']), ROOT)
        with self.assertRaises(ConnectorError):
            construct(request)

    def test_cps_ratio_of_sums_and_incomplete_months(self):
        filters = {'age_min': 25, 'age_max': 54, 'sex': 'male', 'civilian_noninstitutional': True}
        request = ConnectorRequest.from_card(card('cps_monthly', population_filters=filters), ROOT)
        monthly = [dict(unit='AL', year=2000, month=m, n_target=2, n_valid=2, n_missing=0,
                        weight_sum=1 if m == 1 else 10, employed_weight_sum=1 if m == 1 else 0)
                   for m in range(1, 13)]
        complete = annualize(monthly, request)
        self.assertEqual(complete.rows[0]['outcome_value'], 100 / 111)
        self.assertEqual(complete.metadata[0]['available'], 1)
        incomplete = annualize(monthly[:-1], request)
        self.assertEqual(incomplete.rows[0]['outcome_value'], '')
        self.assertEqual(incomplete.metadata[0]['months_present'], 11)
        with self.assertRaises(ConnectorError):
            annualize(monthly + monthly[:1], request)

    def test_cps_raw_applies_population_weights_and_missing_counts(self):
        filters = {'age_min': 25, 'age_max': 54, 'sex': 'male', 'civilian_noninstitutional': True}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            def person(age, sex, employment, weight, person_type=2):
                raw = bytearray(b' ' * 900)
                fields = {'state': 1, 'age': age, 'sex': sex, 'person_type': person_type,
                          'employment': employment, 'weight': weight}
                for name, value in fields.items():
                    first, last = LOC[name]
                    raw[first-1:last] = str(value).rjust(last-first+1).encode()
                return bytes(raw) + b'\n'
            raw = (person(30, 1, 1, 10000) + person(30, 1, 5, 30000)
                   + person(30, 2, 1, 10000) + person(60, 1, 1, 10000)
                   + person(30, 1, -1, 10000) + person(30, 1, 1, 10000, 1))
            with zipfile.ZipFile(base / '2000_jan.zip', 'w') as archive:
                archive.writestr('cps.dat', raw)
            request = ConnectorRequest.from_card(card('cps_monthly', population_filters=filters,
                                                       paths=['2000_jan.zip'], frequency='monthly'), base)
            panel = construct(request)
            self.assertEqual(panel.rows[0]['outcome_value'], 25.0)
            self.assertEqual(panel.metadata[0]['n_target'], 3)
            self.assertEqual(panel.metadata[0]['n_missing'], 1)
            self.assertEqual(panel.metadata[1]['available'], 0)

    def test_qcew_suppression_is_missing_never_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fields = dict(area_fips='01000', own_code='5', industry_code='23', size_code='0',
                          year='2000', disclosure_code='N', annual_avg_emplvl='0')
            write_csv(base / 'source.csv', [fields])
            request = ConnectorRequest.from_card(card('qcew', population_filters={'ownership': 'private', 'industry': '23'}), base)
            result = construct(request)
            self.assertEqual(result.rows[0]['outcome_value'], '')
            self.assertEqual(result.metadata[0]['available'], 0)
            self.assertEqual(result.metadata[0]['suppression_flag'], 1)

    def test_qcew_official_annual_wage_zip_and_duplicate_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fields = dict(area_fips='01000', own_code='5', industry_code='23', size_code='0',
                qtr='A', year='2000', disclosure_code='', annual_avg_wkly_wage='1234',
                annual_avg_emplvl='20', total_annual_wages='1283360')
            write_csv(base / 'raw.csv', [fields, dict(fields, area_fips='01001', annual_avg_wkly_wage='999')])
            with zipfile.ZipFile(base / 'annual.zip', 'w') as archive:
                archive.write(base / 'raw.csv', 'nested/annual.csv')
            request = ConnectorRequest.from_card(card('qcew', population_filters={'ownership': 'private', 'industry': '23'},
                paths=['annual.zip'], value_field='annual_avg_wkly_wage'), base)
            panel = construct(request)
            self.assertEqual(panel.rows[0]['outcome_value'], '1234')
            self.assertEqual(panel.metadata[0]['available'], 1)
            self.assertIn('public domain', panel.inventory[0]['license'])
            duplicate = ConnectorRequest.from_card(card('qcew', population_filters=request.filters,
                paths=['annual.zip', 'raw.csv'], value_field='annual_avg_wkly_wage'), base)
            with self.assertRaises(ConnectorError):
                construct(duplicate)

    def test_qcew_county_weights_complete_roster_and_suppression(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fields = dict(area_fips='01001', own_code='5', industry_code='23', size_code='0', qtr='A',
                year='2000', disclosure_code='', annual_avg_wkly_wage='100',
                annual_avg_emplvl='10', total_annual_wages='52000')
            second = dict(fields, area_fips='01003', annual_avg_wkly_wage='300',
                annual_avg_emplvl='30', total_annual_wages='468000')
            write_csv(base / 'source.csv', [fields, second])
            options = dict(population_filters={'ownership': 'private', 'industry': '23'},
                source_geography='county', county_fips={'AL': ['01001', '01003']},
                value_field='annual_avg_wkly_wage')
            panel = construct(ConnectorRequest.from_card(card('qcew_annual', **options), base))
            self.assertEqual(panel.rows[0]['outcome_value'], 250)
            self.assertEqual(panel.metadata[0]['counties_present'], 2)
            options['county_fips'] = {'AL': ['01001', '01003', '01005']}
            missing = construct(ConnectorRequest.from_card(card('qcew_annual', **options), base))
            self.assertEqual(missing.rows[0]['outcome_value'], '')
            self.assertEqual(missing.metadata[0]['linkage_certified'], 0)
            options['county_fips'] = {'AL': ['01001', '01003']}
            write_csv(base / 'source.csv', [fields, dict(second, disclosure_code='N')])
            suppressed = construct(ConnectorRequest.from_card(card('qcew_annual', **options), base))
            self.assertEqual(suppressed.rows[0]['outcome_value'], '')
            self.assertEqual(suppressed.metadata[0]['suppression_flag'], 1)
            options.pop('county_fips')
            with self.assertRaises(ConnectorNotReady):
                construct(ConnectorRequest.from_card(card('qcew_annual', **options), base))

    def test_acs1_api_and_summary_agree_ratios_and_missing_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'acs1_2023.json').write_text(json.dumps([
                ['NAME', 'B23025_003E', 'B23025_005E', 'state'],
                ['Alabama', '100', '7', '01'], ['Alaska', '80', '-888888888', '02'],
                ['Puerto Rico', '100', '99', '72']]))
            (base / 'acsdt1y2023-b23025.dat').write_text(
                'GEO_ID|B23025_E003|B23025_E005\n0400000US01|100|7\n0400000US02|80|-888888888\n0500000US01001|20|19\n')
            filters = {'age_min': 16, 'employment_population': 'civilian_labor_force'}
            options = dict(population_filters=filters, source_population_filters=filters,
                years=[2023, 2023], units=['AL', 'AK'], numerator_variables=['B23025_005E'],
                denominator_variables=['B23025_003E'], outcome_unit='percent')
            api = construct(ConnectorRequest.from_card(card('acs1_state', paths=['acs1_2023.json'], **options), base))
            summary = construct(ConnectorRequest.from_card(card('acs1_state', paths=['acsdt1y2023-b23025.dat'], **options), base))
            self.assertEqual([r['outcome_value'] for r in api.rows], [r['outcome_value'] for r in summary.rows])
            self.assertEqual({r['unit']: r['outcome_value'] for r in api.rows}, {'AL': 7.0, 'AK': ''})
            self.assertEqual({r['unit']: r['available'] for r in api.metadata}, {'AL': 1, 'AK': 0})
            with self.assertRaises(ConnectorError):
                construct(ConnectorRequest.from_card(card('acs1_state', paths=['acs1_2023.json', 'acsdt1y2023-b23025.dat'], **options), base))

    def test_acs1_missing_variable_and_zero_denominator_stay_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'acs1_2023.json').write_text(json.dumps([
                ['NAME', 'B23025_003E', 'B23025_005E', 'state'], ['Alabama', '0', '0', '01']]))
            options = dict(years=[2023, 2023], population_filters={}, source_population_filters={},
                paths=['acs1_2023.json'], numerator_variables=['B23025_005E'], denominator_variables=['B23025_003E'])
            panel = construct(ConnectorRequest.from_card(card('acs1_state', **options), base))
            self.assertEqual(panel.rows[0]['outcome_value'], '')
            options['denominator_variables'] = ['B23025_007E']
            panel = construct(ConnectorRequest.from_card(card('acs1_state', **options), base))
            self.assertEqual(panel.metadata[0]['available'], 0)
            options['source_population_filters'] = {'undocumented': True}
            with self.assertRaises(ConnectorNotReady):
                construct(ConnectorRequest.from_card(card('acs1_state', **options), base))

    def test_laus_official_annual_only_and_json_tsv_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'source.csv').write_text('series_id\tyear\tperiod\tvalue\tfootnote_codes\n'
                'LAUST010000000000003\t2000\tM13\t4.5\t\n'
                'LAUST010000000000003\t2000\tM01\t99\t\n'
                'LASST010000000000003\t2000\tM13\t98\t\n'
                'LAUCT010010000000003\t2000\tM13\t97\t\n')
            (base / 'source.json').write_text(json.dumps({'status': 'REQUEST_SUCCEEDED', 'Results': {
                'series': [{'seriesID': 'LAUST010000000000003', 'data': [
                    {'year': '2000', 'period': 'M13', 'value': '4.5'},
                    {'year': '2000', 'period': 'M01', 'value': '99'}]}]}}))
            options = dict(population_filters={'age_min': 16, 'civilian_noninstitutional': True}, years=[2000, 2001])
            tsv = construct(ConnectorRequest.from_card(card('laus', **options), base))
            api = construct(ConnectorRequest.from_card(card('laus_state', paths=['source.json'], **options), base))
            self.assertEqual([r['outcome_value'] for r in tsv.rows], ['4.5', ''])
            self.assertEqual([r['outcome_value'] for r in api.rows], ['4.5', ''])
            self.assertEqual(tsv.metadata[1]['available'], 0)
            options['seasonal_adjustment'] = 'seasonally_adjusted'
            with self.assertRaises(ConnectorNotReady):
                construct(ConnectorRequest.from_card(card('laus', **options), base))

    def test_public_download_plans_are_explicit_and_key_free(self):
        qcew = download_plan(ConnectorRequest.from_card(card('qcew', years=[2022, 2023]), ROOT))
        self.assertEqual(len(qcew), 2)
        self.assertTrue(qcew[0]['url'].endswith('2022.annual.singlefile.zip'))
        acs = download_plan(ConnectorRequest.from_card(card('acs1_state', years=[2023, 2023],
            variables=['B23025_005E']), ROOT))
        self.assertTrue(acs[0]['url'].endswith('acsdt1y2023-b23025.dat'))
        self.assertNotIn('key=', acs[0]['keyless_api_alternative'])
        laus = download_plan(ConnectorRequest.from_card(card('laus'), ROOT))
        self.assertTrue(laus[0]['url'].endswith('la.data.2.AllStatesU'))
        for name in ('qcew_annual', 'acs1_state', 'laus_state'):
            self.assertEqual(connector_inventory()[name]['status'], 'implemented_local_files')

    def test_public_fetch_manifest_and_credential_refusal_without_network(self):
        class Response:
            headers = {'Content-Type': 'text/plain'}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return 'https://download.bls.gov/pub/time.series/la/la.data.2.AllStatesU'
            def read(self, limit): return b'synthetic data\n'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'synthetic.txt'
            plan = dict(url=Response().geturl(), license='US government public domain')
            with patch('urllib.request.OpenerDirector.open', return_value=Response()) as network:
                result = fetch_public(plan, target)
                self.assertEqual(result['size_bytes'], len(b'synthetic data\n'))
                self.assertEqual(result['retries'], 0)
                network.assert_called_once()
                with self.assertRaises(FileExistsError):
                    fetch_public(plan, target)
            with self.assertRaises(ConnectorError):
                fetch_public(dict(plan, url='https://api.census.gov/data/2023/acs/acs1?key=secret'), target)

    def test_cdc_explicit_demographic_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_csv(base / 'source.csv', [dict(state='Alabama', sex=sex, age_group='All Ages',
                                                race_and_hispanic_origin='All Races-All Origins',
                                                year='2000', age_adjusted_rate=value)
                                          for sex, value in [('Both Sexes', '12.3'), ('Male', '99.9')]])
            request = ConnectorRequest.from_card(card('cdc_public', population_filters={'sex': 'all', 'age': 'all', 'race_ethnicity': 'all'}), base)
            result = construct(request)
            self.assertEqual(result.rows[0]['outcome_value'], '12.3')

    def test_frozen_batch2_cps_construction_value_text_equivalence(self):
        """Existing frozen target construction only; this does not fit its design."""
        monthly = ROOT / 'data/l2_batch2/panels/btb_private_monthly.csv'
        panel = ROOT / 'data/l2_batch2/panels/btb_private_panel.csv'
        if not monthly.is_file() or not panel.is_file():
            self.skipTest('Frozen local CPS construction inputs unavailable')
        filters = {'civilian_noninstitutional': True, 'sex': 'male', 'age_min': 25, 'age_max': 54}
        request = ConnectorRequest.from_card(card('cps_monthly_aggregates',
            paths=[str(monthly.relative_to(ROOT))], years=[1994, 1995],
            population_filters=filters, source_population_filters=filters), ROOT)
        result = construct(request)
        with panel.open() as handle:
            frozen = {(r['unit'], int(r['year'])): r for r in csv.DictReader(handle)}
        for record in result.rows:
            original = frozen[record['unit'], record['year']]
            self.assertEqual(str(record['outcome_value']), original['employment_percent'])
            self.assertEqual(str(record['employment_share']), original['employment_share'])


if __name__ == '__main__':
    unittest.main()
