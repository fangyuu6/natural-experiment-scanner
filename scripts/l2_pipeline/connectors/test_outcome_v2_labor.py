"""Synthetic labor adapter checks; no treatment data or new causal estimates."""
import csv
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady, ConnectorRequest
from scripts.l2_pipeline.connectors import outcome_v2_labor as labor

ROOT = Path(__file__).resolve().parents[3]
DICTIONARIES = ROOT / 'scripts/l2_batch1/raw/metadata'


class LaborTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='outcome-v2-labor-test-', dir=Path(__file__).parent)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def request(self, series_id, paths, years=(2024,), frequency='annual', units=('CA',)):
        return ConnectorRequest(dict(series_id=series_id, paths=[str(p) for p in paths], dictionary_dir=str(DICTIONARIES)),
                                {}, years, units, frequency, ROOT)

    def bls(self, values, sid='LAUST060000000000005'):
        path = self.directory / 'public.tsv'
        with path.open('w', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerow(['series_id', 'year', 'period', 'value', 'footnote_codes'])
            for period, value in values:
                writer.writerow([sid, 2024, period, value, 'P' if period == 'M12' else ''])
        return path

    def record(self, **updates):
        locations = dict(interview_month=(16, 17), interview_year=(18, 21), HRMIS=(63, 64), GESTFIPS=(93, 94), age=(122, 123), PEMARITL=(125, 126),
                         PESEX=(129, 130), PRPERTYP=(161, 162), PEMLR=(180, 181), PEHRUSLT=(224, 226),
                         PEIO1COW=(432, 433), PRERELG=(498, 499), earnings=(527, 534),
                         PWORWGT=(603, 612), PWSSWGT=(613, 622), PRCHLD=(633, 634))
        values = dict(interview_month=1, interview_year=2003, HRMIS=4, GESTFIPS=6, age=17, PEMARITL=6, PESEX=2, PRPERTYP=2,
                      PEMLR=1, PEHRUSLT=20, PEIO1COW=4, PRERELG=1, earnings=12345,
                      PWORWGT=20000, PWSSWGT=10000, PRCHLD=1)
        values.update(updates)
        record = bytearray(b' ' * 700 + b'\n')
        for name, (start, end) in locations.items():
            record[start-1:end] = str(values[name]).rjust(end-start+1).encode()
        return bytes(record)

    def cps_zip(self, records, month='jan', year=2003):
        path = self.directory / f'{year}_{month}.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('public.dat', b''.join(records))
        return path

    def test_annual_requires12months_and_preserves_preliminary(self):
        path = self.bls([(f'M{m:02}', 10*m) for m in range(1, 13)])
        panel = labor.build(self.request('bls_laus_employment', [path]))
        self.assertEqual(panel.rows[0]['outcome_value'], 65)
        self.assertEqual(panel.metadata[0]['preliminary'], 1)
        self.assertIn('public domain', panel.inventory[0]['license'])
        self.assertEqual(panel.inventory[0]['url'], labor.LAUS_URL)
        path = self.bls([(f'M{m:02}', 10*m) for m in range(1, 12)])
        self.assertEqual(labor.build(self.request('bls_laus_employment', [path])).rows[0]['outcome_value'], '')

    def test_annual_unemployment_does_not_average_monthly_rates(self):
        path = self.bls([(f'M{m:02}', m) for m in range(1, 13)], 'LAUST060000000000003')
        request = self.request('bls_laus_unemployment_rate', [path])
        self.assertEqual(labor.build(request).rows[0]['outcome_value'], '')
        with path.open('a') as handle:
            handle.write('LAUST060000000000003\t2024\tM13\t4.2\t\n')
        self.assertEqual(labor.build(request).rows[0]['outcome_value'], 4.2)

    def test_ces_units_geography_and_duplicate_rejection(self):
        path = self.bls([('M01', 100)], 'SMU06000000500000001')
        request = self.request('bls_ces_private_employment', [path], frequency='monthly')
        panel = labor.build(request)
        self.assertEqual(panel.rows[0]['outcome_value'], 100)
        self.assertEqual(panel.rows[0]['outcome_unit'], 'thousands of payroll jobs')
        with path.open('a') as handle:
            handle.write('SMU06000000500000001\t2024\tM01\t101\t\n')
        with self.assertRaises(ConnectorError):
            labor.build(request)

    def test_qcew_ownership_and_suppression(self):
        path = self.directory / 'qcew_2024.csv'
        keys = ['area_fips', 'own_code', 'industry_code', 'size_code', 'year', 'qtr', 'disclosure_code', 'annual_avg_wkly_wage']
        with path.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(keys)
            writer.writerows([['06000', '0', '10', '0', '2024', 'A', '', '9999'],
                              ['06000', '5', '10', '0', '2024', 'A', '', '1234'],
                              ['01000', '5', '10', '0', '2024', 'A', 'N', '0']])
        panel = labor.build(self.request('bls_qcew_private_weekly_wage', [path], units=('CA', 'AL')))
        self.assertEqual(panel.rows[0]['outcome_value'], 1234)
        self.assertEqual(panel.rows[1]['outcome_value'], '')

    def test_bls_api_error_and_nested_result(self):
        path = self.directory / 'api.json'
        path.write_text(json.dumps(dict(status='REQUEST_SUCCEEDED', Results=dict(series=[dict(
            seriesID='LAUST060000000000005', data=[dict(year='2024', period='M01', value='42', footnotes=[])])]))))
        panel = labor.build(self.request('bls_laus_employment', [path], frequency='monthly'))
        self.assertEqual(panel.rows[0]['outcome_value'], 42)
        path.write_text(json.dumps(dict(status='REQUEST_NOT_PROCESSED')))
        with self.assertRaises(ConnectorNotReady):
            labor.build(self.request('bls_laus_employment', [path]))

    def test_org_weight_and_earnings_implied_decimals(self):
        path = self.cps_zip([self.record(earnings=10000, PWORWGT=10000),
            self.record(earnings=20000, PWORWGT=30000), self.record(HRMIS=1, earnings=999999),
            self.record(PEIO1COW=6, earnings=999999)])
        panel = labor.build(self.request('cps_teen_weekly_earnings', [path], years=(2003,), frequency='monthly'))
        self.assertEqual(panel.rows[0]['outcome_value'], 175)
        self.assertEqual(panel.metadata[0]['weight_sum'], 4)
        self.assertEqual(panel.metadata[0]['n_valid'], 2)

    def test_single_mother_marital_and_ownchild_restrictions(self):
        path = self.cps_zip([self.record(age=30, PEMLR=1), self.record(age=30, PEMLR=3),
                            self.record(age=30, PEMARITL=1), self.record(age=30, PRCHLD=0)])
        panel = labor.build(self.request('cps_single_mother_employment', [path], years=(2003,), frequency='monthly'))
        self.assertEqual(panel.rows[0]['outcome_value'], 50)
        self.assertEqual(panel.metadata[0]['n_valid'], 2)

    def test_unknown_single_mother_linkage_parks_cell(self):
        path = self.cps_zip([self.record(age=30), self.record(age=30, PRCHLD=99)])
        panel = labor.build(self.request('cps_single_mother_employment', [path], years=(2003,), frequency='monthly'))
        self.assertEqual(panel.rows[0]['outcome_value'], '')
        self.assertEqual(panel.metadata[0]['linkage_certified'], 0)

    def test_hours_varies_counted_and_employment_weights_differ(self):
        path = self.cps_zip([self.record(PEHRUSLT=20), self.record(PEHRUSLT=-4), self.record(PEMLR=3)])
        panel = labor.build(self.request('cps_teen_hours', [path], years=(2003,), frequency='monthly'))
        self.assertEqual(panel.rows[0]['outcome_value'], 20)
        self.assertEqual(panel.metadata[0]['n_missing'], 1)
        strict = self.request('cps_teen_hours', [path], years=(2003,), frequency='monthly')
        strict = replace(strict, options=dict(strict.options, missing_person_rule='require_all_valid'))
        self.assertEqual(labor.build(strict).rows[0]['outcome_value'], '')

    def test_cps_annual_completeness_and_unknown_population(self):
        paths = [self.cps_zip([self.record(interview_month=i)], month=m) for i,m in enumerate(labor.MONTHS, 1)]
        request = self.request('cps_teen_employment', paths, years=(2003,))
        self.assertEqual(labor.build(request).metadata[0]['annual_usable'], 1)
        partial = replace(request, options=dict(request.options, paths=[str(p) for p in paths[:-1]]))
        self.assertEqual(labor.build(partial).metadata[0]['annual_usable'], 0)
        with self.assertRaises(ConnectorNotReady):
            labor.build(replace(request, filters={'race': 'white'}))

    def test_dictionary_vintages_and_offset_failure(self):
        from scripts.l2_batch1.dictionary_audit import EPOCHS
        for start, name in EPOCHS:
            labor.verify_dictionary(DICTIONARIES / name, 'weekly_earnings', start >= 200001)
        labor.verify_dictionary(labor.dictionary_for(2000, 1, DICTIONARIES), 'weekly_earnings', True)
        wrong = self.directory / 'bad_dictionary.txt'
        wrong.write_text((DICTIONARIES / '2003_jan03dd.txt').read_text().replace('(603 - 612)', '(604 - 613)'))
        with self.assertRaises(ConnectorNotReady):
            labor.verify_dictionary(wrong, 'weekly_earnings')

    def test_acquire_is_keyless_and_bounded_to_source_coverage(self):
        class Client:
            def fetch(self, url, destination, license, license_url):
                return dict(url=url, path=str(destination), license=license, license_url=license_url)
        receipts = labor.acquire('bls_qcew_private_weekly_wage', [2023, 2024], self.directory, Client())
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all('key=' not in r['url'] and 'token=' not in r['url'] for r in receipts))
        with self.assertRaises(ConnectorNotReady):
            labor.acquire('bls_qcew_private_weekly_wage', [2000], self.directory, Client())
        with self.assertRaises(ConnectorNotReady):
            labor.acquire('cps_teen_employment', [2024], self.directory, Client())

    def test_api_batch_limits_persistent_retry_reservation(self):
        class Client:
            retries = 2
            calls = []
            def fetch(self, url, destination, license, license_url, *, json_body):
                self.calls.append(json_body)
                return dict(url=url, path=str(destination))
        client = Client()
        receipts = labor.acquire('bls_laus_employment', [2000, 2014], self.directory, client, source='api_v1')
        self.assertEqual(len(receipts), 6)  #51states ->3batches;15years ->2yearblocks
        self.assertTrue(all(len(c['seriesid']) <= 25 and int(c['endyear'])-int(c['startyear']) <= 9 for c in client.calls))
        self.assertTrue(all(set(c) == {'seriesid', 'startyear', 'endyear'} for c in client.calls))
        ledger = json.loads((self.directory / '.bls_v1_quota.json').read_text())
        self.assertEqual(ledger['reserved_queries'], 18)
        with self.assertRaises(ConnectorNotReady):
            labor.acquire('bls_laus_employment', [2024], self.directory, client, source='api_v1')
        labor.acquire('bls_laus_employment', [2024], self.directory, client, source='api_v1', units=['CA'])
        self.assertEqual(client.calls[-1]['seriesid'], ['LAUST060000000000005'])

    def test_api_failure_consumes_reservation_and_external_quota_persists(self):
        class Client:
            retries = 0
            def fetch(self, *args, **kwargs):
                raise ConnectorNotReady('temporary public endpoint failure')
        with self.assertRaises(ConnectorNotReady):
            labor.acquire('bls_ces_private_weekly_earnings', [2024], self.directory, Client(), source='api_v1', units=['CA'], external_daily_queries=24)
        with self.assertRaises(ConnectorNotReady):
            labor.acquire('bls_ces_private_weekly_earnings', [2024], self.directory, Client(), source='api_v1', units=['CA'])
        self.assertEqual(json.loads((self.directory / '.bls_v1_quota.json').read_text())['reserved_queries'], 1)

    def test_cps_renamed_zip_cannot_change_interview_dates(self):
        for updates in [dict(interview_month=2), dict(interview_year=2004)]:
            path = self.cps_zip([self.record(**updates)])
            with self.assertRaisesRegex(ConnectorError, 'interview year/month'):
                labor.build(self.request('cps_teen_employment', [path], years=(2003,), frequency='monthly'))

    def test_receipt_symlink_is_rejected_without_reading_target(self):
        path = self.bls([('M13', 10)])
        target = self.directory / 'arbitrary.json'
        target.write_text('invalid json which must not be read')
        path.with_name(path.name+'.receipt.json').symlink_to(target)
        with self.assertRaisesRegex(ConnectorError, 'symlink'):
            labor.build(self.request('bls_laus_employment', [path]))


if __name__ == '__main__':
    unittest.main()
