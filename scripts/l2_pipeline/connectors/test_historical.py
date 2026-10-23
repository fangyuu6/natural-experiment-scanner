"""Historical source contracts; synthetic outcome data only."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady, ConnectorRequest
from scripts.l2_pipeline.connectors.outcome_v2_core import file_receipt, PUBLIC_DOMAIN
from scripts.l2_pipeline.connectors import historical as h


class HistoricalContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, rows, filename='source.csv'):
        path = self.root / filename
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return filename

    def drug(self, **changes):
        return dict(state='California', year='2018', sex='Both Sexes', age='All Ages',
                    race='All Races-All Origins', deaths='100', popul='1000000', rate='10',
                    ageadjrate='9.5', unit='per 100,000 population', **changes)

    def request(self, sid, rows, filters=None, years=(2018,), units=('CA',), **options):
        return ConnectorRequest({'series_id': sid, 'paths': [self.source(rows)], **options},
                                h.DRUG_FILTERS if filters is None else filters,
                                years, units, 'annual', self.root)

    def test_drug_fields_are_separate_and_population_is_published(self):
        for suffix, expected in [('deaths', 100), ('crude_rate', 10), ('age_adjusted_rate', 9.5)]:
            panel = h.construct(self.request('nchs_historical_drug_overdose_' + suffix, [self.drug()]))
            self.assertEqual(panel.rows[0]['outcome_value'], expected)
            self.assertEqual(panel.rows[0]['source_population'], 1000000)
            self.assertEqual(panel.metadata[0]['available'], 1)
            self.assertEqual(panel.details['outcome_association_calls'], 0)

    def test_suppression_absence_and_unreliability_remain_distinct(self):
        source = self.drug(); source['deaths'] = '*'
        panel = h.build(self.request('nchs_historical_drug_overdose_deaths', [source], units=('CA', 'AK')))
        self.assertIsNone(panel.rows[0]['outcome_value'])
        self.assertTrue(panel.rows[0]['suppressed'])
        self.assertEqual(panel.rows[0]['published_value_token'], '*')
        self.assertFalse(panel.rows[1]['suppressed'])
        self.assertEqual(panel.metadata[1]['published_cell_present'], 0)
        source['ageadjrate'] = 'Unreliable'
        panel = h.build(self.request('nchs_historical_drug_overdose_age_adjusted_rate', [source]))
        self.assertIsNone(panel.rows[0]['outcome_value'])
        self.assertTrue(panel.rows[0]['unreliable'])

    def test_no_national_substitution(self):
        source = self.drug(); source['state'] = 'United States'; source['sex'] = 'Female'
        panel = h.build(self.request('nchs_historical_drug_overdose_deaths', [source]))
        self.assertIsNone(panel.rows[0]['outcome_value'])

    def test_exact_strata_and_count_units_required(self):
        sid = 'nchs_historical_drug_overdose_deaths'
        with self.assertRaises(ConnectorNotReady):
            h.build(self.request(sid, [self.drug()], filters={'age_group': 'All Ages'}))
        for field, invalid in [('sex', 'Female'), ('age', '15–24'), ('race', 'Hispanic'),
                               ('unit', 'per 1,000'), ('deaths', '10.5'), ('popul', '-1'),
                               ('year', '2019'), ('state', 'New York City')]:
            source = self.drug(); source[field] = invalid
            with self.subTest(field=field), self.assertRaises(ConnectorError):
                h.build(self.request(sid, [source]))
        with self.assertRaises(ConnectorError):
            h.build(self.request(sid, [self.drug()], value_field='rate'))

    def test_duplicates_fail_even_identical(self):
        with self.assertRaisesRegex(ConnectorError, 'Duplicate'):
            h.build(self.request('nchs_historical_drug_overdose_deaths', [self.drug(), self.drug()]))

    def test_no_extrapolation_outside_observed_vintage(self):
        panel = h.build(self.request('nchs_historical_drug_overdose_deaths', [self.drug()], years=(2018, 2019)))
        self.assertEqual(panel.rows[0]['outcome_value'], 100)
        self.assertIsNone(panel.rows[1]['outcome_value'])

    def test_existing_leading_cause_contract_retained(self):
        source = dict(state='California', year='2017', cause_name='Suicide', deaths='*', aadr='**',
                      _113_cause_name='Intentional self-harm (suicide) (*U03,X60-X84,Y87.0)')
        panel = h.build(self.request('cdc_leading_cause_deaths', [source],
                                    {'cause_name': 'Suicide'}, years=(2017,)))
        self.assertTrue(panel.rows[0]['suppressed'])
        self.assertIsNone(panel.rows[0]['outcome_value'])
        with self.assertRaises(ConnectorNotReady):
            h.build(self.request('cdc_leading_cause_deaths', [source],
                                 {'cause_name': 'Firearm'}, years=(2017,)))

    def test_existing_teen_age_denominator_retained(self):
        source = dict(state='California', year='2019', age_years='15-17 years',
                      state_births='100', state_rate='1.2', unit='per 1,000')
        panel = h.build(self.request('cdc_teen_birth_rate', [source],
                                    {'age_group': '15-17'}, years=(2019,)))
        self.assertEqual(panel.rows[0]['outcome_value'], 1.2)
        with self.assertRaises(ConnectorNotReady):
            h.build(self.request('cdc_teen_birth_rate', [source],
                                 {'age_group': '20-24'}, years=(2019,)))

    def test_download_receipt_tampering_detected(self):
        request = self.request('nchs_historical_drug_overdose_deaths', [self.drug()])
        path = request.paths()[0]
        receipt = file_receipt(path, h.SERIES[request.options['series_id']]['source_url'],
                               PUBLIC_DOMAIN, h.LICENSE_URL)
        path.with_name(path.name + '.receipt.json').write_text(json.dumps(receipt))
        with path.open('a') as stream:
            stream.write('\n')
        with self.assertRaisesRegex(ConnectorError, 'receipt'):
            h.build(request)

    def test_full_resource_query_once_and_completeness_required(self):
        calls = []
        def fetch(url, path, license, license_url):
            calls.append(url); path.parent.mkdir(parents=True, exist_ok=True)
            if '_metadata.' in path.name:
                path.write_text(json.dumps({'licenseId': 'USGOV_WORKS', 'name': 'Synthetic', 'description': ''}))
            elif '_count.' in path.name:
                path.write_text('[{"n":"2"}]')
            else:
                path.write_text('state,year\nCalifornia,2018\n')
            return file_receipt(path, url, license, license_url)
        with self.assertRaisesRegex(ConnectorError, 'incomplete'):
            h.acquire(self.root, Mock(fetch=fetch))
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum('.csv?' in url for url in calls), 1)
        self.assertIn('%24limit=1000000', calls[1])

    def test_actual_download_coverage_without_outcome_associations(self):
        root = h.ROOT
        if not (root / 'raw' / 'historical_44rk-q6r2.csv').exists():
            self.skipTest('No archived download')
        request = ConnectorRequest(
            {'series_id': 'nchs_historical_drug_overdose_deaths',
             'paths': ['raw/historical_44rk-q6r2.csv']}, h.DRUG_FILTERS,
            tuple(range(1999, 2019)), tuple(sorted(h.STATES.values())), 'annual', root)
        panel = h.build(request)
        self.assertEqual(len(panel.rows), 1020)
        self.assertEqual(sum(m['available'] for m in panel.metadata), 1020)
        self.assertEqual(panel.details['outcome_association_calls'], 0)


if __name__ == '__main__':
    unittest.main()
