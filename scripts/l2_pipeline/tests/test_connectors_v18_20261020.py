"""T184 (2026-10-20): catalog V18 source parsers, artifact build and registration.

Synthetic fixtures for every parser quirk; real-data checks read the pinned local files and skip when a file is absent.
No network, no policy or treatment input."""
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.l2_pipeline.connectors import v18_build, v18_sources as src, v18_xls, v18_xlsx
from scripts.l2_pipeline.connectors.common import ConnectorError

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / 'data/l2_pipeline/connectors_v2'


def need(*paths):
    return unittest.skipUnless(all(Path(p).is_file() for p in paths), 'pinned local source file absent')


def make_xlsx(path, rows, sheet='S'):
    """Smallest valid workbook: shared strings, one sheet; rows is a list of lists (None skips a cell)."""
    strings, cells = [], []
    for r, values in enumerate(rows, 1):
        out = []
        for c, v in enumerate(values):
            if v is None:
                continue
            ref = chr(65 + c) + str(r)
            if isinstance(v, str):
                if v not in strings:
                    strings.append(v)
                out.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, strings.index(v)))
            else:
                out.append('<c r="%s"><v>%s</v></c>' % (ref, v))
        cells.append('<row r="%d">%s</row>' % (r, ''.join(out)))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % (ns, sheet))
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/sharedStrings.xml', '<sst %s>%s</sst>' % (ns, ''.join('<si><t>%s</t></si>' % s for s in strings)))
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, ''.join(cells)))


class ReaderTests(unittest.TestCase):
    def test_xlsx_reader_returns_text_and_numbers_with_one_based_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'book.xlsx'
            make_xlsx(path, [['State', 'HPI'], ['Alabama', 100.5], [None, 7]])
            sheets = v18_xlsx.read_xlsx(path)
        self.assertEqual(sheets['S'], {(1, 1): 'State', (1, 2): 'HPI', (2, 1): 'Alabama', (2, 2): 100.5, (3, 2): 7.0})

    def test_xlsx_reader_refuses_a_file_that_is_not_a_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.xlsx'
            path.write_bytes(b'not a zip')
            with self.assertRaises(Exception):
                v18_xlsx.read_xlsx(path)

    def test_xls_reader_refuses_a_file_that_is_not_ole2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.xls'
            path.write_bytes(b'<html>not an xls</html>')
            with self.assertRaises(ConnectorError):
                v18_xls.read_xls(path)

    @need(src.SNAP_ZIP)
    def test_xls_reader_reads_the_published_snap_workbook(self):
        with zipfile.ZipFile(src.SNAP_ZIP) as z, tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'FY00.xls'
            target.write_bytes(z.read('FY00.xls'))
            sheets = v18_xls.read_xls(target)
        grid = sheets['NERO']
        self.assertEqual(grid[6, 1], 'Connecticut')
        self.assertEqual(grid[7, 1], 'Oct 1999')
        self.assertEqual(grid[7, 2], 84900.0)
        self.assertEqual(grid[7, 3], 169014.0)
        self.assertAlmostEqual(grid[19, 3], 165058.5833, places=3)


class ParserFixtureTests(unittest.TestCase):
    def test_state_names_lose_footnote_markers_and_non_states_are_dropped(self):
        self.assertEqual(src.unit_of_name('Alabama\\12\\ '), 'AL')
        self.assertEqual(src.unit_of_name('  District of Columbia'), 'DC')
        self.assertIsNone(src.unit_of_name('Puerto Rico'))
        self.assertIsNone(src.unit_of_name('United States'))

    def test_school_year_label_is_the_spring_year_and_malformed_labels_refuse(self):
        self.assertEqual(src.school_year_end('2009-10'), 2010)
        self.assertEqual(src.school_year_end('1999-00'), 2000)
        with self.assertRaises(ConnectorError):
            src.school_year_end('2009-12')

    def test_niaaa_layout_divides_per_capita_by_ten_thousand_and_keeps_beverages_apart(self):
        lines = ['1970  1 1    3863000   1738350    2499000  6956  9    2020000  8606  9 .   .         .',
                 '1970  1 4          .   3453680    2499000 13820 10    2020000 17097 10 .   .         .',
                 '2021  1 4          .   3453680    2499000 14000 10    2020000 17097 10 .   .         .']
        for fips in (2, 4):  # a second and third jurisdiction
            lines.append('1970 %2d 4          .   3453680    2499000 20000 10    2020000 17097 10 .   .         .' % fips)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pc.txt'
            path.write_text('header\n' + '\n'.join(lines) + '\n')
            with patch.object(src, 'NIAAA', path):
                series = {s.sid.rsplit('_', 1)[-1] + s.sid.split('age')[1][:2]: s for s in src.niaaa()}
        spirits = [s for s in series.values() if s.sid.endswith('14plus_spirits')][0]
        total = [s for s in series.values() if s.sid.endswith('14plus_all_beverages')][0]
        self.assertAlmostEqual(spirits.values['AL', 1970], 0.6956)
        self.assertAlmostEqual(total.values['AL', 1970], 1.382)
        self.assertAlmostEqual(total.values['AK', 1970], 2.0)
        self.assertNotIn(('AL', 1970), [k for k in total.values if k[0] == 'DC'])

    def test_niaaa_duplicate_cell_refuses(self):
        line = '1970  1 4          .   3453680    2499000 13820 10    2020000 17097 10 .   .         .'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pc.txt'
            path.write_text(line + '\n' + line + '\n')
            with patch.object(src, 'NIAAA', path), self.assertRaises(ConnectorError):
                src.niaaa()

    def test_schoolvaxview_flags_survey_type_and_keeps_na_missing(self):
        header = 'vaccine,dose,geography_type,geography,year_season,coverage_estimate,population_sample_size,percent_surveyed,foot_notes,number_of_exemptions,survey_type'
        rows = [header,
                'MMR,,States,Alabama,2009-10,94.0,75358,100,x,,Census',
                'MMR,,States,Alaska,2009-10,NA,9641,76.4,,,Voluntary response',
                'MMR,,States,Arizona,2009-10,91.5,90000,85,,,Sample',
                'MMR,,National,United States,2009-10,92.0,1,100,,,Census',
                'Exemption,Any Exemption,States,Alabama,2009-10,0.5,75358,,,373,Census']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'svv.csv'
            path.write_text('\n'.join(rows) + '\n')
            with patch.object(src, 'SVV', path):
                series = {s.sid: s for s in src.svv()}
        mmr = series['cdc_svv_kindergarten_mmr_coverage_pct']
        self.assertEqual(mmr.values, {('AL', 2010): 94.0, ('AZ', 2010): 91.5})
        self.assertEqual(mmr.flags['AK', 2010]['missing_reason'], 'CDC_NA')
        self.assertTrue(mmr.flags['AK', 2010]['unreliable'])
        self.assertTrue(mmr.flags['AZ', 2010]['sampling_error'])
        self.assertFalse(mmr.flags['AL', 2010]['sampling_error'])
        self.assertEqual(series['cdc_svv_kindergarten_any_exemption_pct'].values, {('AL', 2010): 0.5})

    def test_afgr_row_with_footnote_markers_between_values_keeps_year_alignment(self):
        names = [n for n in src.NAMES]
        head = ['State or jurisdiction'] + ['1990-91', '1995-96', '1999-2000', '2000-01', '2002-03', '2003-04', '2004-05', '2005-06', '2006-07', '2007-08', '2008-09', '2009-10', '2010-11', '2011-12', '2012-13']
        def tr(cells):
            return '<tr>' + ''.join('<td>%s</td>' % c for c in cells) + '</tr>'
        body = [tr(head), tr(['United States', '73.7', '71.0', '71.7', '71.7', '73.9', '74.3', '1', '74.7', '73.4', '2', '73.9', '74.7', '', '75.5', '2', '78.2', '', '79.6', '80.8', '81.9'])]
        for name in names:
            body.append(tr([name, '70.0', '71.0', '72.0', '73.0', '74.0', '75.0', '', '76.0', '77.0', '', '78.0', '79.0', '', '80.0', '', '81.0', '', '82.0', '83.0', '84.0']))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 't.html'
            path.write_text('<table>' + ''.join(body) + '</table>')
            with patch.object(src, 'AFGR', path):
                series = src.afgr()
        self.assertEqual(len(series.values), 51 * 15)
        self.assertEqual(series.years[:6], [1991, 1996, 2000, 2001, 2003, 2004])
        self.assertEqual(series.values['AL', 2013], 84.0)
        self.assertEqual(series.values['AL', 2001], 73.0)
        self.assertNotIn(('US', 1991), series.values)

    def test_afgr_row_with_wrong_value_count_refuses(self):
        def tr(cells):
            return '<tr>' + ''.join('<td>%s</td>' % c for c in cells) + '</tr>'
        head = ['State or jurisdiction'] + ['1990-91', '1995-96'] + ['19%02d-%02d' % (a, a + 1) for a in range(96, 99)] * 4
        rows = [tr(head)] + [tr(['Alabama', '70.0', '71.0'])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 't.html'
            path.write_text('<table>' + ''.join(rows) + '</table>')
            with patch.object(src, 'AFGR', path), self.assertRaises(ConnectorError):
                src.afgr()

    def test_state_finance_1999_layout_and_year_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.txt').write_text('01000000000000   24  E62   1234567 \n01000000000000   24  E62   1 \n')
            with patch.object(src, 'ASFIN', root), patch.dict(src.ASFIN_FILES, {1999: 'a.txt'}):
                with self.assertRaises(ConnectorError):  # duplicate item
                    src.asfin_hist_year(1999)
            (root / 'b.txt').write_text('01000000000000E62' + '12345'.rjust(12) + '050524\n')
            with patch.object(src, 'ASFIN', root), patch.dict(src.ASFIN_FILES, {2005: 'b.txt'}):
                path, items = src.asfin_hist_year(2005)
                self.assertEqual(items[('AL', 'E62')], 12345.0)
            with patch.object(src, 'ASFIN', root), patch.dict(src.ASFIN_FILES, {2006: 'b.txt'}):
                with self.assertRaises(ConnectorError):  # file holds 2005, expected 2006
                    src.asfin_hist_year(2006)

    def test_qcew_disclosure_cells_stay_missing_and_county_rows_are_ignored(self):
        header = 'area_fips,own_code,industry_code,agglvl_code,size_code,year,qtr,disclosure_code,annual_avg_estabs_count,annual_avg_emplvl,total_annual_wages,annual_avg_wkly_wage'
        rows = [header,
                '01000,5,11,54,0,2010,A,,10,100,5000,640',
                '02000,5,11,54,0,2010,A,N,0,0,0,0',
                '01001,5,11,74,0,2010,A,,3,30,900,500',
                '04000,3,11,54,0,2010,A,,5,50,2000,400',
                'US000,5,11,54,0,2010,A,,99,999,9999,999']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '2010_11.csv').write_text('\n'.join(rows) + '\n')
            with patch.object(src, 'QCEW', root), patch.object(src, 'QCEW_SLICES', [src.QCEW_SLICES[0]]):
                series = src.qcew_slices()[0]
        self.assertEqual(series.values, {('AL', 2010): 100.0})
        self.assertTrue(series.flags['AK', 2010]['suppressed'])

    def test_state_finance_codes_are_alphabetical_with_dc_at_nine(self):
        self.assertEqual(src.ASFIN_STATE_CODES[1], 'AL')
        self.assertEqual(src.ASFIN_STATE_CODES[8], 'DE')
        self.assertEqual(src.ASFIN_STATE_CODES[9], 'DC')
        self.assertEqual(src.ASFIN_STATE_CODES[10], 'FL')
        self.assertEqual(src.ASFIN_STATE_CODES[14], 'IL')
        self.assertEqual(src.ASFIN_STATE_CODES[51], 'WY')


@need(src.NIAAA, src.BDS, src.SVV, src.ACGR, src.AFGR, src.SNAP_ZIP, src.SEDS, src.FHFA, src.BEA / 'SAINC.zip', src.BEA / 'SAGDP.zip')
class RealSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.series = {s.sid: s for s in src.all_series()}
        cls.deferred = {s.sid: s for b in src.DEFERRED_BUILDERS for s in b()}

    def test_thirty_nine_new_series_with_unique_ids(self):
        self.assertEqual(len(self.series), 39)
        self.assertFalse(any(k.startswith(('bea_', 'fhfa_')) for k in self.series))
        self.assertEqual(len(self.deferred), 12)

    def test_every_value_is_finite_and_every_state_is_a_published_unit(self):
        for s in self.series.values():
            for (u, y), v in s.values.items():
                self.assertIn(u, src.UNITS, s.sid)
                self.assertTrue(math.isfinite(v), s.sid)
                self.assertIn(y, s.years, s.sid)
            self.assertEqual(s.years, sorted(set(s.years)), s.sid)
            self.assertTrue(s.values, s.sid)

    def test_percent_series_stay_between_zero_and_one_hundred(self):
        for s in self.series.values():
            if s.units == 'percent' and ('pct' in s.sid or 'graduation' in s.sid) and 'change' not in s.sid:
                self.assertTrue(all(0 <= v <= 100 for v in s.values.values()), s.sid)

    def test_flags_use_only_support_schema_fields(self):
        from scripts.l2_pipeline.connectors.c46_core import SUPPORT_FIELDS
        for s in self.series.values():
            for flag in s.flags.values():
                self.assertFalse(set(flag) - SUPPORT_FIELDS, s.sid)

    def test_niaaa_spot_values_match_the_file(self):
        s = self.series['niaaa_apparent_ethanol_gallons_per_capita_age14plus_spirits']
        self.assertAlmostEqual(s.values['AL', 1970], 0.6956)
        self.assertEqual((s.years[0], s.years[-1]), (1970, 2024))
        self.assertEqual(len(s.values), 51 * 55)

    def test_bds_spot_value_and_full_grid(self):
        s = self.series['census_bds_job_creation_rate']
        self.assertAlmostEqual(s.values['AL', 1978], 21.484)
        self.assertEqual(len(s.values), 51 * 46)

    def test_graduation_series_are_separate_and_acgr_marks_the_2012_break(self):
        acgr = self.series['nces_acgr_public_hs_4yr_graduation_rate']
        afgr = self.series['nces_afgr_public_hs_graduation_rate']
        self.assertEqual((acgr.years[0], acgr.years[-1]), (2012, 2022))
        self.assertEqual(afgr.years[-1], 2013)
        self.assertEqual(acgr.values['AL', 2012], 75.0)
        self.assertEqual(acgr.values['AL', 2022], 88.2)
        self.assertEqual(afgr.values['AL', 1991], 69.8)
        self.assertEqual(acgr.flags['AL', 2012]['series_break'], 'afgr_to_acgr_cohort_definition_2012')
        self.assertNotIn(('ID', 2012), acgr.values)  # published '---'
        self.assertEqual(acgr.flags['ID', 2012]['missing_reason'], 'not_available')

    def test_snap_participation_matches_the_published_total_row(self):
        s = self.series['fns_snap_participation_pct_of_population']
        pop = src.seds_population()
        self.assertAlmostEqual(s.values['CT', 2000], 100 * 165058.5833 / pop['CT', 2000], places=4)
        self.assertEqual(s.flags['CT', 2019]['source_issue'], 'footnoted_months_excluded_from_participation_mean')
        self.assertEqual(s.years, list(range(1989, 2026)))

    def test_state_finance_continuous_with_lottery_and_dc_absent(self):
        s = self.series['census_state_finance_hist_corrections_per_capita']
        self.assertEqual(s.years, list(range(1996, 2012)))
        self.assertNotIn('DC', {u for u, y in s.values})
        self.assertTrue(all(('FL', y) in s.values for y in s.years))
        self.assertEqual(s.flags['DC', 2005]['missing_reason'], 'DC_not_in_state_files')
        self.assertGreater(s.values['AL', 2011], s.values['AL', 1996])

    def test_state_finance_hist_connects_to_the_labeled_2012_aggregates(self):
        # The 1996-2011 item-code sums must be on the same footing as the 2012+ labeled ASFIN aggregates (never spliced, but comparable).
        import statistics
        z = zipfile.ZipFile(src.ASFIN / 'GS00STATEFIN01.zip')
        rows = csv.DictReader(io.StringIO(z.read('GS00STATEFIN01.dat').decode()), delimiter='|')
        by = {(r['AGG_DESC'], src.STATES[r['ST']]): float(r['AMOUNT']) for r in rows
              if r['GOVTYPE'] == '002' and r['#YEAR'] == '2012' and r['ST'] in src.STATES and not r['AMOUNT_F'].strip()}
        pop = src.seds_population()
        for name, aggs in (('corrections', ['SF0192', 'SF0198']), ('police_protection', ['SF0341']), ('hospitals', ['SF0275'])):
            s = self.series['census_state_finance_hist_%s_per_capita' % name]
            ratios = [sum(by.get((g, u), 0) for g in aggs) * 1000 / pop[u, 2012] / s.values[u, 2011]
                      for u in src.UNITS if (u, 2011) in s.values and sum(by.get((g, u), 0) for g in aggs) > 0]
            self.assertGreater(len(ratios), 40, name)
            self.assertTrue(0.9 < statistics.median(ratios) < 1.1, name)

    def test_bea_per_capita_income_matches_the_csv(self):
        with zipfile.ZipFile(src.BEA / 'SAINC.zip') as z:
            rows = list(csv.reader(io.StringIO(z.read(src.SAINC1).decode('latin1'))))
        header = rows[0]
        record = next(r for r in rows if r[0].strip(' "') == '01000' and r[4].strip() == '3')
        self.assertEqual(self.deferred['bea_per_capita_personal_income'].values['AL', 2010], float(record[header.index('2010')]))

    def test_fhfa_index_base_year_is_100(self):
        s = self.deferred['fhfa_all_transactions_hpi_1975_base']
        self.assertTrue(all(abs(s.values[u, 1975] - 100.0) < 1e-9 for u in src.UNITS))

    def test_qcew_slices_real_values(self):
        s = self.series['bls_qcew_private_agriculture_employment']
        self.assertEqual(s.values['CA', 2010], 382857.0)
        wage = self.series['bls_qcew_private_child_day_care_avg_weekly_wage']
        self.assertEqual(wage.values['CA', 2024], 800.0)

    def test_counts_use_the_jobs_unit_only_where_the_rate_machinery_expects_it(self):
        from scripts.l2_pipeline.connectors.rates import count_kind
        for s in self.series.values():
            kind = count_kind(dict(series_id=s.sid, outcome_unit=s.units))
            if s.sid == 'bls_qcew_private_agriculture_employment':
                self.assertEqual(kind, 'events')
            else:
                self.assertIsNone(kind, s.sid)


class DeriveTests(unittest.TestCase):
    def fixture(self):
        v17 = dict(catalog_id='first_order_outcomes_v17', schema_version=17,
                   series={'old': dict(series_id='old', registered_connector='outcome_v17_old')},
                   targets={'alcohol_use': dict(series=['old'], selection_authorized=True)})
        p17 = dict(catalog_id='first_order_outcomes_v17', series={'old': dict(construct='alcohol_use')},
                   targets=[dict(construct='alcohol_use', aliases=['alcohol_use'], ages=[0, 120], restrictions={})])
        new = src.Series('new_a', [2000], {('AL', 2000): 1.0}, 'percent', 'pop', 'u', [], 'brand_new', aliases=['alias_x'])
        clash = src.Series('new_b', [2000], {('AL', 2000): 2.0}, 'percent', 'pop', 'u', [], 'alcohol_use')
        specs = {s.sid: dict(series_id=s.sid) for s in (new, clash)}
        return v17, p17, specs, {new.sid: new, clash.sid: clash}

    def test_v17_content_is_unchanged_and_existing_targets_are_never_extended(self):
        v17, p17, specs, by_id = self.fixture()
        before = deepcopy((v17, p17))
        data, meta, held = v18_build.derive(v17, p17, specs, by_id, v17_ref={}, p17_ref={})
        self.assertEqual((v17, p17), before)  # inputs untouched
        self.assertEqual(data['series']['old']['registered_connector'], 'outcome_v18_old')
        self.assertEqual(data['targets']['alcohol_use']['series'], ['old'])
        self.assertEqual(held, {'alcohol_use': ['new_b']})
        self.assertEqual(data['targets']['brand_new']['series'], ['new_a'])
        self.assertEqual(data['targets']['alias_x']['series'], ['new_a'])
        self.assertEqual(data['schema_version'], 18)
        self.assertEqual(meta['catalog_id'], 'first_order_outcomes_v18')
        self.assertIn('new_a', meta['series'])
        self.assertTrue(any(t['construct'] == 'brand_new' for t in meta['targets']))

    def test_duplicate_series_id_refuses(self):
        v17, p17, specs, by_id = self.fixture()
        v17['series']['new_a'] = dict(series_id='new_a')
        with self.assertRaises(ConnectorError):
            v18_build.derive(v17, p17, specs, by_id, v17_ref={}, p17_ref={})


@unittest.skipUnless((Path(v18_build.__file__).with_name('OUTCOME_CATALOG_V18.json')).is_file(), 'V18 not built yet')
class BuiltCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.l2_pipeline.connectors import outcome_v17, outcome_v18
        cls.v17, cls.v18 = outcome_v17, outcome_v18
        cls.old = json.loads(outcome_v17.CATALOG_PATH.read_text())
        cls.new = json.loads(outcome_v18.CATALOG_PATH.read_text())

    def test_v18_is_v17_plus_new_series_and_new_targets_only(self):
        added = set(self.new['series']) - set(self.old['series'])
        self.assertEqual(len(added), 39)
        self.assertFalse(set(self.old['series']) - set(self.new['series']))
        for sid, spec in self.old['series'].items():
            self.assertEqual(dict(self.new['series'][sid], registered_connector=None), dict(spec, registered_connector=None), sid)
            self.assertEqual(self.new['series'][sid]['registered_connector'], 'outcome_v18_' + sid)
        for name, target in self.old['targets'].items():
            self.assertEqual(self.new['targets'][name], target, name)
        for name in set(self.new['targets']) - set(self.old['targets']):
            self.assertTrue(set(self.new['targets'][name]['series']) <= added, name)
        self.assertEqual(self.new['inherited_catalog']['sha256'], self.v17.c46_core.sha(self.v17.CATALOG_PATH))

    def test_catalog_verifies_every_artifact_hash(self):
        data = self.v18.catalog(verify_sources=True)
        self.assertEqual(data['schema_version'], 18)

    def test_every_new_series_registers_and_constructs_a_full_grid(self):
        from scripts.l2_pipeline import connectors
        from scripts.l2_pipeline.connectors.common import ConnectorRequest, STATES
        data = self.v18.catalog(verify_sources=False)
        self.v18.register()
        units = tuple(sorted(set(STATES.values()) - {'DC'}))
        for sid in sorted(set(self.new['series']) - set(self.old['series'])):
            spec = data['series'][sid]
            name = 'outcome_v18_' + sid
            self.assertIn(name, connectors.CONNECTOR_CATALOG, sid)
            years = tuple(spec['years'][-3:])
            artifact = spec['artifact']
            options = dict(connector=name, series_id=sid, measure_id=sid, population_filters=spec['population_filters'], frequency='annual',
                           paths=[artifact['path']], source_sha256={artifact['path']: artifact['sha256']})
            request = ConnectorRequest(options, dict(spec['population_filters']), years, units, 'annual', REPO)
            panel = self.v18._construct(request, data)
            self.assertEqual(len(panel.rows), len(units) * len(years), sid)
            self.assertEqual(len(panel.metadata), len(panel.rows), sid)
            self.assertTrue(all('outcome_value' in r for r in panel.rows), sid)
            self.assertTrue(panel.inventory, sid)

    def test_card_with_changed_hash_refuses(self):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest, STATES
        data = self.v18.catalog(verify_sources=False)
        self.v18.register()
        sid = 'nces_afgr_public_hs_graduation_rate'
        spec = data['series'][sid]
        options = dict(connector='outcome_v18_' + sid, series_id=sid, measure_id=sid, population_filters=spec['population_filters'], frequency='annual',
                       paths=[spec['artifact']['path']], source_sha256={spec['artifact']['path']: '0' * 64})
        request = ConnectorRequest(options, dict(spec['population_filters']), (2020,), tuple(sorted(set(STATES.values()) - {'DC'})), 'annual', REPO)
        with self.assertRaises(ConnectorError):
            self.v18._construct(request, data)

    def test_outcome_only_boundary(self):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        data = self.v18.catalog(verify_sources=False)
        request = ConnectorRequest(dict(connector='outcome_v18_x', series_id='x', treatment={}), {}, (2000,), ('AL',), 'annual', REPO)
        with self.assertRaises(ConnectorError):
            self.v18._construct(request, data)


if __name__ == '__main__':
    unittest.main()
