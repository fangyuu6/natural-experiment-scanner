"""Synthetic official ACS API/summary schemas; no outcome-policy association."""
import json
import tempfile
import unittest
import zipfile
import hashlib
from pathlib import Path

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest
from .outcome_v2_acs import SERIES, acquire, build, variables


class ACSTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def request(self, sid, years=(2023,), paths=None, **options):
        return ConnectorRequest(dict(series_id=sid, paths=paths or ['acs_2023.json'], **options),
                                {}, years, ('CA', 'TX'), 'annual', self.base)

    def api(self, sid, values, name='acs_2023.json', duplicate=False):
        fields = variables(sid)
        header = ['NAME', *fields, 'state']
        source = ['California', *[str(values.get(v, 2 if v.endswith('M') else 10)) for v in fields], '06']
        (self.base / name).write_text(json.dumps([header, source] + ([source] if duplicate else [])))

    def test_poverty_uses_counts_not_mean_of_bin_rates(self):
        sid = 'acs1_poverty_rate_age_5_11'
        nums = SERIES[sid]['numerator_variables']
        dens = SERIES[sid]['denominator_variables']
        estimates = dict(zip(nums, [1, 20, 3, 16]))
        estimates.update(dict.fromkeys(set(dens) - set(nums), 90))
        self.api(sid, estimates)
        panel = build(self.request(sid))
        ca = panel.rows[0]
        self.assertEqual(ca['numerator'], 40)
        self.assertEqual(ca['denominator'], 400)
        self.assertEqual(ca['outcome_value'], 10)
        self.assertEqual(len(ca['cell_moes']), 8)
        self.assertIsNone(ca['outcome_moe'])
        self.assertEqual(panel.rows[1]['outcome_value'], '')
        self.assertEqual(panel.inventory[0]['license'], SERIES[sid]['license'])

    def test_direct_median_moe_and_householder_age(self):
        sid = 'acs1_median_household_income_age_under25'
        self.api(sid, {'B19049_002E': 45000, 'B19049_002M': 900})
        panel = build(self.request(sid))
        self.assertEqual(panel.rows[0]['outcome_value'], 45000)
        self.assertEqual(panel.rows[0]['outcome_moe'], 900)
        self.assertEqual(panel.rows[0]['dollar_price_year'], 2023)
        self.assertIn('householder', panel.rows[0]['population'])

    def test_sentinels_do_not_become_zero(self):
        sid = 'acs1_snap_household_receipt_rate'
        self.api(sid, {'B22003_002E': -666666666, 'B22003_001E': 100})
        row = build(self.request(sid)).rows[0]
        self.assertEqual(row['outcome_value'], '')
        self.assertIsNone(row['numerator'])

    def test_moe_sentinel_preserves_valid_estimate(self):
        sid = 'acs1_single_mother_median_family_income'
        self.api(sid, {'B19126_010E': 40000, 'B19126_010M': -222222222})
        result = build(self.request(sid)).rows[0]
        self.assertEqual(result['outcome_value'], 40000)
        self.assertIsNone(result['outcome_moe'])

    def test_2020_explicit_missing_and_source_refusal(self):
        sid = 'acs1_snap_household_receipt_rate'
        self.api(sid, {'B22003_002E': 10, 'B22003_001E': 100})
        panel = build(self.request(sid, years=(2020, 2023)))
        self.assertEqual(panel.rows[0]['missing_reason'], 'standard_2020_not_released')
        self.api(sid, {}, name='acs_2020.json')
        with self.assertRaises(ConnectorNotReady):
            build(self.request(sid, years=(2020,), paths=['acs_2020.json']))

    def test_duplicate_state_year_refused(self):
        sid = 'acs1_snap_household_receipt_rate'
        self.api(sid, {}, duplicate=True)
        with self.assertRaises(ConnectorError):
            build(self.request(sid))

    def test_safe_insurance_age_bins_and_numerator_bound(self):
        sid = 'acs1_insurance_coverage_age_19_25'
        self.assertEqual(SERIES[sid]['numerator_variables'], ('B27001_010E', 'B27001_038E'))
        self.api(sid, {'B27001_010E': 90, 'B27001_038E': 80,
                       'B27001_009E': 100, 'B27001_037E': 100})
        self.assertEqual(build(self.request(sid)).rows[0]['outcome_value'], 85)
        self.api(sid, {'B27001_010E': 500})
        with self.assertRaises(ConnectorError):
            build(self.request(sid))

    def test_insurance_2017_age_schema_break_is_not_spliced(self):
        old = 'acs1_insurance_coverage_age_18_24'
        new = 'acs1_insurance_coverage_age_19_25'
        self.assertEqual(SERIES[old]['years'], list(range(2008, 2017)))
        self.assertEqual(min(SERIES[new]['years']), 2017)
        self.assertEqual(max(SERIES[old]['public_download_years']), 2016)
        with self.assertRaises(ConnectorNotReady):
            build(self.request(new, years=(2016,)))
        with self.assertRaises(ConnectorNotReady):
            build(self.request(old, years=(2017,)))

    def test_summary_geography_and_moe_names(self):
        sid = 'acs1_median_household_income_age_all'
        path = self.base / 'acsdt1y2023-b19049.dat'
        path.write_text('GEO_ID|B19049_E001|B19049_M001\n0100000US|100|10\n'
                        '0400000US06|78000|900\n0500000US06001|150000|100\n')
        panel = build(self.request(sid, paths=[path.name]))
        self.assertEqual(panel.rows[0]['outcome_value'], 78000)
        self.assertEqual(panel.rows[0]['outcome_moe'], 900)

    def test_malformed_nonfinite_population_and_year_fail_closed(self):
        sid = 'acs1_median_household_income_age_all'
        self.api(sid, {'B19049_001E': 'NaN'})
        with self.assertRaises(ConnectorError):
            build(self.request(sid))
        self.api(sid, {})
        with self.assertRaises(ConnectorError):
            build(self.request(sid, source_years={'acs_2023.json': 2022}))
        req = self.request(sid)
        req = ConnectorRequest(req.options, {'age_group': 'teen'}, req.years, req.units, req.frequency, req.base_dir)
        with self.assertRaises(ConnectorNotReady):
            build(req)

    def test_receipt_symlink_is_rejected_without_reading_target(self):
        sid = 'acs1_median_household_income_age_all'
        self.api(sid, {})
        (self.base / 'acs_2023.json.receipt.json').symlink_to(self.base / 'unreadable_target')
        with self.assertRaisesRegex(ConnectorError, 'receipt cannot be a symlink'):
            build(self.request(sid))

    def test_legacy_sequence_logrecno_moe_provenance_and_tamper(self):
        sid = 'acs1_median_household_income_age_all'
        class Legacy:
            def fetch(inner, url, destination, **license_info):
                if destination.name.startswith('acs_lookup'):
                    destination.write_text('File ID,Table ID,Sequence Number,Line Number,Start Position,Total Cells in Table\n'
                        'ACSSF,B19049,0081,,7,5 CELLS\n')
                elif destination.suffix == '.csv':
                    destination.write_text('ACSSF,CA,040,00,0000001,,,,,06\nACSSF,CA,040,01,0000002,,,,,06\n')
                else:
                    with zipfile.ZipFile(destination, 'w') as archive:
                        for m, vals in [('e', '70000,40000,80000,90000,50000'), ('m', '100,200,300,400,500')]:
                            archive.writestr(f'{m}20191ca0081000.txt', f'ACSSF,2019{m}1,ca,000,0081,0000001,{vals}\n')
                return dict(path=str(destination), url=url, size_bytes=destination.stat().st_size,
                    sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), **license_info)
        receipts = acquire(sid, (2019,), self.base, Legacy(), units=('CA',))
        self.assertEqual(len(receipts), 4)
        derived = receipts[-1]
        self.assertEqual(derived['url'], '')
        self.assertEqual(len(derived['source_receipts']), 3)
        panel = build(self.request(sid, years=(2019,), paths=[Path(r['path']).name for r in receipts]))
        self.assertEqual(panel.rows[0]['outcome_value'], 70000)
        self.assertEqual(panel.rows[0]['outcome_moe'], 100)
        (self.base / 'g20191ca.csv').write_text('tampered')
        with self.assertRaisesRegex(ConnectorError, 'source hash mismatch'):
            build(self.request(sid, years=(2019,), paths=[Path(derived['path']).name]))

    def test_acquire_receipts_2020_skip_and_missing_key(self):
        sid = 'acs1_median_household_income_age_all'
        class Fake:
            calls = []
            def fetch(inner, url, destination, **license_info):
                inner.calls.append(url)
                destination.write_text('GEO_ID|B19049_E001|B19049_M001\n0400000US06|70000|100\n')
                return dict(path=str(destination), url=url, size_bytes=destination.stat().st_size,
                            sha256='fixture', **license_info)
        fake = Fake()
        receipts = acquire(sid, (2020, 2023), self.base, fake)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn('key=', receipts[0]['url'])
        with self.assertRaises(ConnectorNotReady):
            acquire(sid, (2009,), self.base, fake)
        class Blocked:
            def fetch(inner, url, destination, **info):
                destination.write_text('<html><title>Missing Key</title></html>')
                return {}
        with self.assertRaisesRegex(ConnectorNotReady, 'requires a key'):
            acquire(sid, (2023,), self.base, Blocked(), source='api')


if __name__ == '__main__':
    unittest.main()
