"""Outcome construction support; synthetic person records only, no estimates."""
import tempfile
import unittest
import zipfile
import csv
import json
from pathlib import Path

from scripts.l2_pipeline.connectors import ConnectorError, ConnectorNotReady, ConnectorRequest, download_plan
from scripts.l2_pipeline.connectors.cps_nurse import annualize, audit_dictionaries, nurse_codes, nurse_locations, parse_month
from scripts.l2_pipeline.connectors import construct
from scripts.l2_pipeline.connectors.common import sha
from scripts.l2_pipeline.connectors.qcew_total import BASIS_URLS


def request(year=2000, **options):
    filters = dict(age_min=18, age_max=64, civilian_noninstitutional=True,
                   occupation='registered_nurses', industry='hospitals')
    outcome = dict(connector='cps', years=[year, year], units=['AL'], frequency='annual',
                   value_field='weekly_hours', population_filters=filters)
    outcome.update(options)
    return ConnectorRequest.from_card({'outcome': outcome}, Path.cwd())


class Run1ConnectorTests(unittest.TestCase):
    def test_total_ownership_requires_full_slice_certificate_and_preserves_suppression(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            template = dict(area_fips='01000', own_code='5', industry_code='621910', size_code='0',
                            year='2014', qtr='A', disclosure_code='', annual_avg_emplvl='10')
            bindings = []
            for year, rows in [(2014, [template, dict(template, own_code='3', annual_avg_emplvl='20')]),
                               (2015, [dict(template, year='2015', disclosure_code='N', annual_avg_emplvl='0')])]:
                path = base / f'{year}_621910.csv'
                with path.open('w', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(template))
                    writer.writeheader(); writer.writerows(rows)
                bindings.append(dict(path=str(path), year=year, sha256=sha(path), size_bytes=path.stat().st_size,
                    url=f'https://data.bls.gov/cew/data/api/{year}/a/industry/621910.csv'))
            outcome = dict(connector='qcew', years=[2014, 2015], units=['AL', 'WY'], paths=[s['path'] for s in bindings],
                population_filters=dict(ownership='total', industry='621910'), ownership_aggregation='certified_sum')
            def make_request():
                return ConnectorRequest.from_card({'outcome': outcome}, base)
            with self.assertRaises(ConnectorNotReady):
                construct(make_request())
            cert = dict(schema='qcew_complete_industry_ownership_v1', industry='621910', years=[2014, 2015],
                        full_industry_slices=True, absent_component_rule='nonexistent_macro_cell_zero',
                        ownership_components=['1', '2', '3', '5'], source_files=bindings,
                        official_basis=[dict(url=u) for u in BASIS_URLS])
            certificate = base / 'certificate.json'
            certificate.write_text(json.dumps(cert))
            outcome['ownership_completeness_certificate'] = str(certificate)
            panel = construct(make_request())
            values = {(r['unit'], r['year']): r['outcome_value'] for r in panel.rows}
            self.assertEqual(values['AL', 2014], 30)
            self.assertEqual(values['AL', 2015], '')  # N is never an absent component zero.
            self.assertEqual(values['WY', 2014], 0)  # Full slice certifies no economic macro cells.
            meta = {(r['unit'], r['year']): r for r in panel.metadata}
            self.assertEqual(meta['AL', 2014]['certified_absent_components'], 2)
            self.assertEqual(meta['AL', 2015]['suppression_flag'], 1)
            cert['source_files'][0]['sha256'] = '0' * 64
            certificate.write_text(json.dumps(cert))
            with self.assertRaises(ConnectorError):
                construct(make_request())
            outcome['years'] = [2003, 2003]
            with self.assertRaises(ConnectorNotReady):
                construct(make_request())

    def test_qcew_industry_slice_has_exact_official_url_and_rights(self):
        outcome = dict(connector='qcew', years=[2014, 2015], download_strategy='industry_slice',
                       population_filters=dict(ownership='private', industry='621340'))
        plans = download_plan(ConnectorRequest.from_card({'outcome': outcome}, Path.cwd()))
        self.assertEqual([p['url'] for p in plans], [
            'https://data.bls.gov/cew/data/api/2014/a/industry/621340.csv',
            'https://data.bls.gov/cew/data/api/2015/a/industry/621340.csv'])
        self.assertTrue(all('public domain' in p['license'] for p in plans))
        outcome['years'] = [2013, 2013]
        with self.assertRaises(ConnectorNotReady):
            download_plan(ConnectorRequest.from_card({'outcome': outcome}, Path.cwd()))
        outcome['years'] = [2014, 2014]
        outcome['population_filters']['industry'] = '../secret'
        with self.assertRaises(ConnectorError):
            download_plan(ConnectorRequest.from_card({'outcome': outcome}, Path.cwd()))

    def test_nurse_classification_boundaries(self):
        self.assertEqual(nurse_codes(2002), ((95,), (831,)))
        self.assertEqual(nurse_codes(2003), ((3130,), (8190,)))
        self.assertEqual(nurse_codes(2010), ((3130,), (8190,)))
        self.assertEqual(nurse_codes(2011), ((3255,), (8190,)))
        self.assertEqual(nurse_codes(2020), ((3255,), (8191, 8192)))
        with self.assertRaises(ConnectorNotReady):
            nurse_codes(1993)

    def test_nurse_hours_population_and_missing_rules_across_layouts(self):
        for year in (2000, 2003, 2011, 2020):
            with self.subTest(year=year), tempfile.TemporaryDirectory() as directory:
                loc = nurse_locations(year)
                occ, industries = nurse_codes(year)
                def person(hours, weight, occupation=occ[0], industry=industries[0], employment=1, age=30):
                    raw = bytearray(b' ' * 900)
                    values = dict(state=1, age=age, sex=2, person_type=2, employment=employment,
                                  weight=weight, hours=hours, occupation=occupation, industry=industry)
                    for key, value in values.items():
                        first, last = loc[key]
                        raw[first - 1:last] = str(value).rjust(last - first + 1).encode()
                    return bytes(raw) + b'\n'
                # Non-nurses, non-hospital jobs, and absentees cannot contribute.
                raw = (person(40, 10000) + person(60, 30000) + person(-1, 10000)
                       + person(100, 10000, occupation=999) + person(100, 10000, industry=999)
                       + person(0, 10000, employment=2) + person(20, 10000, age=65))
                path = Path(directory) / f'{year}_jan.zip'
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('cps.dat', raw)
                r = request(year)
                month = parse_month(path, year, 1, r)[0]
                self.assertEqual((month['n_target'], month['n_valid'], month['n_missing']), (3, 2, 1))
                months = [dict(month, month=m) for m in range(1, 13)]
                panel = annualize(months, r)
                self.assertEqual(panel.rows[0]['outcome_value'], 55)
                self.assertEqual(panel.metadata[0]['available'], 1)
                long = annualize(months, request(year, value_field='long_hours_percent'))
                self.assertEqual(long.rows[0]['outcome_value'], 75)
                strict = annualize(months, request(year, missing_person_rule='require_all_valid'))
                self.assertEqual(strict.metadata[0]['available'], 0)
                self.assertEqual(annualize(months[:-1], r).rows[0]['outcome_value'], '')
                with self.assertRaises(ConnectorError):
                    annualize(months + months[:1], r)

    def test_nurse_dictionary_proof_required(self):
        with self.assertRaises(ConnectorNotReady):
            audit_dictionaries(request())
        root = Path(__file__).resolve().parents[3] / 'scripts/l2_batch1/raw/metadata'
        if root.is_dir():
            r = request(2024, dictionary_root=str(root))
            audited = audit_dictionaries(r)
            self.assertEqual(len(audited), 2)
            self.assertTrue(all(a['selected_locations']['hours'] == (247, 249) for a in audited))
            self.assertTrue(all(not a['initial_check']['suspicious'] for a in audited))


if __name__ == '__main__':
    unittest.main()
