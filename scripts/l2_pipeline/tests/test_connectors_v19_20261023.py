"""T273E/T277 (2026-10-23): catalog V19 = V18 plus six BLS QCEW private-ownership industry series (NAICS 6213, 4853, 23).

Synthetic fixtures for the parser and the derive step; real-data checks read the pinned local files and skip when a file is
absent or V19 is not built. No network, no policy or treatment input, no outcome association."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.connectors import v18_sources, v19_build, v19_sources
from scripts.l2_pipeline.connectors.common import ConnectorError, STATES

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / 'data/l2_pipeline/connectors_v2'
RAW = ROOT / 'bls_qcew_industry/raw'
CONNECTORS = REPO / 'scripts/l2_pipeline/connectors'
NEW = {s[0]: s for s in v19_sources.QCEW_SLICES_V19}
HEADER = 'area_fips,own_code,industry_code,agglvl_code,size_code,year,qtr,disclosure_code,%s,annual_avg_emplvl,total_annual_wages,annual_avg_wkly_wage'
V17C_CHANGE = {'rape': {'preferred_series': 'fbi_ucr_rape_legacy_rate'}}


def raw_files_present():
    need = [RAW / ('%d_%s.csv' % (y, n)) for y in range(2001, 2026) for n in ('6213', '4853', '23')]
    return all(p.is_file() and p.with_name(p.name + '.receipt.json').is_file() for p in need)


def built():
    return (CONNECTORS / 'OUTCOME_CATALOG_V19.json').is_file() and (CONNECTORS / 'OUTCOME_POPULATIONS_V19.json').is_file()


def raw_value(naics, year, unit_fips, field):
    """Independent csv read of one state-year cell (private ownership, state total, all sizes)."""
    with (RAW / ('%d_%s.csv' % (year, naics))).open(newline='', encoding='utf-8') as stream:
        for r in csv.DictReader(stream):
            if r['own_code'] == '5' and r['industry_code'] == naics and r['area_fips'] == unit_fips + '000' and r['size_code'] == '0':
                return r['disclosure_code'].strip(), r.get(field, r.get(field.replace('_count', '')))
    return None, None


class ParserFixtureTests(unittest.TestCase):
    def series(self, files, spec):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, rows in files.items():
                (root / name).write_text('\n'.join(rows) + '\n')
            with patch.object(v18_sources, 'QCEW', root), patch.object(v19_sources, 'QCEW_SLICES_V19', [spec]):
                return v19_sources.all_series()[0]

    def test_disclosure_cells_stay_missing_flagged_and_other_rows_are_ignored_in_both_column_spellings(self):
        spec = NEW['bls_qcew_private_taxi_limousine_establishments']
        archive = [HEADER % 'annual_avg_estabs_count',
                   '01000,5,4853,56,0,2010,A,,10,100,5000,640',
                   '02000,5,4853,56,0,2010,A,N,0,0,0,0',
                   '01001,5,4853,76,0,2010,A,,3,30,900,500',    # county row
                   '04000,3,4853,56,0,2010,A,,5,50,2000,400',    # not private
                   '05000,5,4853,71,0,2010,A,,7,70,2000,400',    # agglvl 71
                   'US000,5,4853,56,0,2010,A,,99,999,9999,999']  # nation
        api = [HEADER % 'annual_avg_estabs',
               '01000,5,4853,56,0,2020,A,,12,120,6000,650',
               '02000,5,4853,56,0,2020,A,,2,20,1000,500']
        series = self.series({'2010_4853.csv': archive, '2020_4853.csv': api}, spec)
        self.assertEqual(series.values, {('AL', 2010): 10.0, ('AL', 2020): 12.0, ('AK', 2020): 2.0})
        self.assertEqual(series.flags['AK', 2010], dict(suppressed=True, missing_reason='BLS_disclosure_N', suppression_reason='disclosure'))
        self.assertEqual(series.years, [2010, 2020])
        self.assertEqual(series.units, 'establishments (annual average)')
        self.assertEqual(series.filters, dict(ownership='private', industry='4853'))
        self.assertTrue(series.limitations[-1].startswith('QCEW counts unemployment-insurance-covered payroll employers only'))

    def test_missing_state_row_stays_missing_without_a_flag_and_duplicates_refuse(self):
        spec = NEW['bls_qcew_private_construction_employment']
        rows = [HEADER % 'annual_avg_estabs', '01000,5,23,54,0,2010,A,,10,100,5000,640']
        series = self.series({'2010_23.csv': rows}, spec)
        self.assertNotIn(('AK', 2010), series.flags)
        self.assertEqual(set(series.values), {('AL', 2010)})
        with self.assertRaises(ConnectorError):
            self.series({'2010_23.csv': rows + [rows[1]]}, spec)

    def test_a_naics_with_no_files_refuses(self):
        with self.assertRaises(ConnectorError):
            self.series({}, NEW['bls_qcew_private_construction_employment'])

    def test_new_ids_constructs_and_units_follow_the_t273e_table(self):
        self.assertEqual(len(v19_sources.QCEW_SLICES_V19), 6)
        self.assertEqual(len({s[0] for s in v19_sources.QCEW_SLICES_V19}), 6)
        self.assertEqual(len({s[4] for s in v19_sources.QCEW_SLICES_V19}), 6)
        by_naics = {}
        for sid, naics, field, units, construct, text in v19_sources.QCEW_SLICES_V19:
            by_naics.setdefault(naics, []).append(units)
            self.assertTrue(sid.startswith('bls_qcew_private_'))
            self.assertIn(naics if len(naics) == 4 else 'sector ' + naics, text)
        self.assertEqual({k: sorted(v) for k, v in by_naics.items()},
                         {'6213': ['establishments (annual average)', 'jobs'], '4853': ['establishments (annual average)', 'jobs'],
                          '23': ['USD per week (nominal)', 'jobs']})


@unittest.skipUnless(raw_files_present(), 'pinned local QCEW files for NAICS 6213, 4853 and 23 absent')
class RealSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.series = {s.sid: s for s in v19_build.all_series()}

    def test_six_series_with_1275_cells_and_ids_not_in_v18(self):
        self.assertEqual(len(self.series), 6)
        v18 = json.loads((CONNECTORS / 'OUTCOME_CATALOG_V18.json').read_text())
        for sid, s in self.series.items():
            self.assertNotIn(sid, v18['series'])
            self.assertEqual(s.years, list(range(2001, 2026)))
            self.assertEqual(len(s.years) * len(v19_build.UNITS), 1275)
            self.assertEqual(len(v19_build.UNITS), 51)

    def test_receipts_bind_every_raw_file(self):
        for s in self.series.values():
            self.assertEqual(len(s.paths), 25)
            for p in s.paths:
                receipt = json.loads(Path(str(p) + '.receipt.json').read_text())
                for key in ('url', 'sha256', 'size_bytes', 'license', 'retrieved_utc', 'description', 'path', 'http_status', 'content_type'):
                    self.assertIn(key, receipt, p)
                self.assertTrue(receipt['url'].startswith('https://data.bls.gov/cew/data/'))
                self.assertIn(receipt['http_status'], (200, 206))

    def test_spot_values_match_the_raw_files_read_independently(self):
        cases = [('bls_qcew_private_offices_of_other_health_practitioners_establishments', 'CA', '06', 2010, '6213', 'annual_avg_estabs_count'),
                 ('bls_qcew_private_offices_of_other_health_practitioners_employment', 'TX', '48', 2018, '6213', 'annual_avg_emplvl'),
                 ('bls_qcew_private_taxi_limousine_employment', 'NY', '36', 2005, '4853', 'annual_avg_emplvl'),
                 ('bls_qcew_private_taxi_limousine_establishments', 'FL', '12', 2022, '4853', 'annual_avg_estabs_count'),
                 ('bls_qcew_private_construction_avg_weekly_wage', 'CA', '06', 2015, '23', 'annual_avg_wkly_wage'),
                 ('bls_qcew_private_construction_employment', 'OH', '39', 2001, '23', 'annual_avg_emplvl')]
        for sid, unit, fips, year, naics, field in cases:
            code, text = raw_value(naics, year, fips, field)
            self.assertEqual(code, '', sid)
            self.assertEqual(self.series[sid].values[unit, year], float(text.replace(',', '')), sid)

    def test_naics_23_2025_has_17_disclosure_cells_for_employment_and_wage(self):
        for sid in ('bls_qcew_private_construction_employment', 'bls_qcew_private_construction_avg_weekly_wage'):
            s = self.series[sid]
            missing = [u for u in v19_build.UNITS if (u, 2025) not in s.values]
            self.assertEqual(len(missing), 17, sid)
            self.assertTrue(all(s.flags[u, 2025]['suppressed'] and s.flags[u, 2025]['missing_reason'] == 'BLS_disclosure_N' for u in missing))
            self.assertEqual(sum(1 for u in v19_build.UNITS if (u, 2025) in s.values), 34)

    def test_no_stored_zero_value_and_no_absent_state_row_in_any_series(self):
        for d in v19_build.diagnostics(list(self.series.values())):
            self.assertEqual(d['zero_values'], 0, d['series_id'])
            self.assertEqual(d['absent_rows'], 0, d['series_id'])

    def test_6213_is_fully_published_for_all_51_units_in_all_25_years(self):
        for sid in ('bls_qcew_private_offices_of_other_health_practitioners_establishments', 'bls_qcew_private_offices_of_other_health_practitioners_employment'):
            self.assertEqual(len(self.series[sid].values), 1275)
            self.assertFalse(self.series[sid].flags)

    def test_units_and_outcome_scale_follow_the_count_rule(self):
        from scripts.l2_pipeline.connectors.rates import count_kind
        for s in self.series.values():
            kind = count_kind(dict(series_id=s.sid, outcome_unit=s.units))
            self.assertEqual(kind, 'events' if s.units == 'jobs' else None, s.sid)

    def test_taxi_series_carry_cell_breaks_for_the_measured_state_steps(self):
        emp = self.series['bls_qcew_private_taxi_limousine_employment']
        est = self.series['bls_qcew_private_taxi_limousine_establishments']
        self.assertEqual({y for (u, y), f in emp.flags.items() if f.get('series_break')}, {2023, 2024})
        self.assertEqual({y for (u, y), f in est.flags.items() if f.get('series_break')}, {2023})
        self.assertEqual({u for (u, y), f in emp.flags.items() if f.get('series_break') and y == 2023}, set(v19_build.UNITS))
        # the step itself: WA 2022 -> 2023 and NY 2023 -> 2024 (read from the independent csv reader, not the parser)
        wa22 = float(raw_value('4853', 2022, '53', 'annual_avg_emplvl')[1]); wa23 = float(raw_value('4853', 2023, '53', 'annual_avg_emplvl')[1])
        ny23 = float(raw_value('4853', 2023, '36', 'annual_avg_emplvl')[1]); ny24 = float(raw_value('4853', 2024, '36', 'annual_avg_emplvl')[1])
        self.assertGreater(wa23, 10 * wa22)
        self.assertGreater(ny24, 10 * ny23)
        for sid in ('bls_qcew_private_construction_employment', 'bls_qcew_private_offices_of_other_health_practitioners_employment'):
            self.assertFalse(any(f.get('series_break') for f in self.series[sid].flags.values()))

    def test_step_check_reports_the_sum_without_the_driving_state_as_ordinary(self):
        check = v19_build.step_check(list(self.series.values()))['bls_qcew_private_taxi_limousine_employment']
        self.assertEqual(check['largest_state_change']['2022_2023']['state'], 'WA')
        self.assertEqual(check['largest_state_change']['2023_2024']['state'], 'NY')
        self.assertLess(abs(check['largest_state_change']['2022_2023']['log_change_of_sum_without_that_state']), 0.08)


class DeriveTests(unittest.TestCase):
    def fixture(self):
        v18 = dict(catalog_id='first_order_outcomes_v18', schema_version=18,
                   series={'old': dict(series_id='old', registered_connector='outcome_v18_old')},
                   targets={'old_construct': dict(series=['old'], selection_authorized=True)})
        p18 = dict(catalog_id='first_order_outcomes_v18', series={'old': dict(construct='old_construct')},
                   targets=[dict(construct='old_construct', aliases=['old_construct'], ages=[0, 120], restrictions={}),
                            dict(construct='rape', aliases=['rape'], ages=[0, 120], restrictions={})])
        new = v18_sources.Series('new_a', [2000], {('AL', 2000): 1.0}, 'jobs', 'pop', 'u', [], 'taxi_limousine_payroll_employment',
                                 filters=dict(industry='4853', ownership='private'), population_unit='state_measure')
        return v18, p18, {'new_a': dict(series_id='new_a')}, {'new_a': new}

    def test_v18_content_is_unchanged_and_new_target_carries_construct_and_phrase(self):
        v18, p18, specs, by_id = self.fixture()
        before = deepcopy((v18, p18))
        data, meta, held = v19_build.derive(v18, p18, specs, by_id, v18_ref={}, p18_ref={}, v17c_change=V17C_CHANGE, family_pins=())
        self.assertEqual((v18, p18), before)
        self.assertEqual(data['series']['old']['registered_connector'], 'outcome_v19_old')
        self.assertEqual(data['targets']['old_construct'], v18['targets']['old_construct'])
        self.assertEqual(data['targets']['taxi_limousine_payroll_employment']['series'], ['new_a'])
        self.assertEqual(data['schema_version'], 19)
        self.assertEqual(meta['catalog_id'], 'first_order_outcomes_v19')
        new_target = [t for t in meta['targets'] if t['construct'] == 'taxi_limousine_payroll_employment']
        self.assertEqual(len(new_target), 1)
        self.assertEqual(new_target[0]['aliases'], ['taxi_limousine_payroll_employment', 'employment in taxi and limousine service'])
        self.assertEqual(new_target[0]['rule'], 'V19 exact published construct; outcome-only')
        self.assertEqual(held, {})
        self.assertEqual([t['preferred_series'] for t in meta['targets'] if t['construct'] == 'rape'], ['fbi_ucr_rape_legacy_rate'])

    def test_clashes_refuse(self):
        v18, p18, specs, by_id = self.fixture()
        v18['series']['new_a'] = dict(series_id='new_a')
        with self.assertRaises(ConnectorError):
            v19_build.derive(v18, p18, specs, by_id, v18_ref={}, p18_ref={}, v17c_change=V17C_CHANGE, family_pins=())
        v18, p18, specs, by_id = self.fixture()
        v18['targets']['taxi_limousine_payroll_employment'] = dict(series=['old'])
        with self.assertRaises(ConnectorError):
            v19_build.derive(v18, p18, specs, by_id, v18_ref={}, p18_ref={}, v17c_change=V17C_CHANGE, family_pins=())
        v18, p18, specs, by_id = self.fixture()
        p18['targets'][0]['aliases'].append('employment in taxi and limousine service')
        with self.assertRaises(ConnectorError):
            v19_build.derive(v18, p18, specs, by_id, v18_ref={}, p18_ref={}, v17c_change=V17C_CHANGE, family_pins=())

    def test_family_pins_copy_the_generic_target_with_family_and_preferred_series_and_refuse_ambiguity(self):
        meta = dict(series={'a': dict(construct='c1'), 'b': dict(construct='c1')},
                    targets=[dict(construct='c1', aliases=['c1', 'alias one'], ages=[0, 120], restrictions={}, rule='R')])
        out = v19_build.family_pin_targets(meta, (('c1', 'fam_x', 'a'),))
        self.assertEqual(out, [dict(construct='c1', aliases=['c1', 'alias one'], ages=[0, 120], restrictions={}, rule='R', family='fam_x', preferred_series='a')])
        self.assertNotIn('family', meta['targets'][0])
        for bad in (('c2', 'fam_x', 'a'), ('c1', 'fam_x', 'zzz')):
            with self.assertRaises(ConnectorError):
                v19_build.family_pin_targets(meta, (bad,))
        meta['targets'].append(dict(meta['targets'][0], family='fam_x'))
        with self.assertRaises(ConnectorError):
            v19_build.family_pin_targets(meta, (('c1', 'fam_x', 'a'),))

    def test_v17c_field_is_refused_when_already_present_or_when_v17c_changes_more(self):
        v18, p18, specs, by_id = self.fixture()
        p18['targets'][1]['preferred_series'] = 'x'
        with self.assertRaises(ConnectorError):
            v19_build.derive(v18, p18, specs, by_id, v18_ref={}, p18_ref={}, v17c_change=V17C_CHANGE, family_pins=())
        b = json.loads((CONNECTORS / 'OUTCOME_POPULATIONS_V17B.json').read_text())
        c = json.loads((CONNECTORS / 'OUTCOME_POPULATIONS_V17C.json').read_text())
        self.assertEqual(v19_build.rape_field(b, c), V17C_CHANGE)
        c2 = deepcopy(c)
        c2['targets'][0] = dict(c2['targets'][0], rule='changed')
        with self.assertRaises(ConnectorError):
            v19_build.rape_field(b, c2)


@unittest.skipUnless(built(), 'V19 not built yet')
class BuiltCatalogTests(unittest.TestCase):
    @classmethod
    def current(cls):
        from scripts.l2_pipeline.connectors import population_v63
        return population_v63.catalog(source=cls.v19.catalog(verify_sources=False), path=CONNECTORS / 'OUTCOME_POPULATIONS_V19.json')

    @classmethod
    def setUpClass(cls):
        from scripts.l2_pipeline.connectors import outcome_v18, outcome_v19
        cls.v18, cls.v19 = outcome_v18, outcome_v19
        cls.old = json.loads(outcome_v18.CATALOG_PATH.read_text())
        cls.new = json.loads(outcome_v19.CATALOG_PATH.read_text())
        cls.pold = json.loads((CONNECTORS / 'OUTCOME_POPULATIONS_V18.json').read_text())
        cls.pnew = json.loads((CONNECTORS / 'OUTCOME_POPULATIONS_V19.json').read_text())

    def test_v19_is_v18_plus_six_series_and_six_targets_only(self):
        added = set(self.new['series']) - set(self.old['series'])
        self.assertEqual(added, set(NEW))
        self.assertFalse(set(self.old['series']) - set(self.new['series']))
        for sid, spec in self.old['series'].items():
            self.assertEqual(dict(self.new['series'][sid], registered_connector=None), dict(spec, registered_connector=None), sid)
            self.assertEqual(self.new['series'][sid]['registered_connector'], 'outcome_v19_' + sid)
        for name, target in self.old['targets'].items():
            self.assertEqual(self.new['targets'][name], target, name)
        self.assertEqual(set(self.new['targets']) - set(self.old['targets']), {s[4] for s in NEW.values()})
        for name in set(self.new['targets']) - set(self.old['targets']):
            self.assertEqual(len(self.new['targets'][name]['series']), 1)
            self.assertTrue(set(self.new['targets'][name]['series']) <= added, name)
        self.assertEqual(self.new['inherited_catalog']['sha256'], self.v18.c46_core.sha(self.v18.CATALOG_PATH))
        self.assertEqual(self.new['catalog_id'], 'first_order_outcomes_v19')
        self.assertEqual({k: v for k, v in self.new.items() if k not in ('series', 'targets', 'catalog_id', 'schema_version', 'inherited_catalog', 'registered_entrypoint', 'registration_entrypoint', 'acquisition_entrypoint')},
                         {k: v for k, v in self.old.items() if k not in ('series', 'targets', 'catalog_id', 'schema_version', 'inherited_catalog', 'registered_entrypoint', 'registration_entrypoint', 'acquisition_entrypoint')})

    def test_new_series_entries_follow_the_v18_qcew_shape(self):
        ref = self.old['series']['bls_qcew_private_child_day_care_establishments']
        for sid, (_, naics, field, units, construct, text) in NEW.items():
            spec = self.new['series'][sid]
            self.assertEqual(set(spec), set(ref), sid)
            self.assertEqual((spec['adapter'], spec['adapter_version'], spec['frequency'], spec['legal_certification_granted'], spec['public_available'], spec['status']),
                             (ref['adapter'], ref['adapter_version'], ref['frequency'], False, True, ref['status']), sid)
            self.assertEqual((spec['construct'], spec['units'], spec['population'], spec['population_filters']), (construct, units, text, dict(industry=naics, ownership='private')), sid)
            self.assertEqual(spec['years'], list(range(2001, 2026)))
            self.assertTrue(spec['artifact']['path'].startswith('data/l2_pipeline/connectors_v2/v19_20261023/parsed/'))
            self.assertEqual(spec['license'], ref['license'])
            self.assertEqual(spec['source_url'], ref['source_url'])

    def test_population_table_is_v18_plus_rape_field_plus_snap_family_pins_plus_six_series_and_six_targets(self):
        old, new = deepcopy(self.pold), deepcopy(self.pnew)
        self.assertEqual(new['catalog_id'], 'first_order_outcomes_v19')
        self.assertEqual(new['inherited_population_metadata']['path'], 'scripts/l2_pipeline/connectors/OUTCOME_POPULATIONS_V18.json')
        self.assertEqual(new['inherited_population_metadata']['sha256'], self.v18.c46_core.sha(CONNECTORS / 'OUTCOME_POPULATIONS_V18.json'))
        for sid in NEW:
            self.assertEqual(new['series'].pop(sid)['construct'], NEW[sid][4])
        self.assertEqual(set(old['series']), set(new['series']))
        self.assertEqual(old['series'], new['series'])
        extra = [t for t in new['targets'] if t['construct'] in {s[4] for s in NEW.values()}]
        self.assertEqual(len(extra), 6)
        for t in extra:
            self.assertIn(t['construct'], t['aliases'])
            self.assertNotIn('preferred_series', t)
        pins = [t for t in new['targets'] if t.get('family') in {f for _, f, _ in v19_build.FAMILY_PINS}]
        self.assertEqual(len(pins), 4)
        generic = [t for t in old['targets'] if t['construct'] == 'snap_participation']
        self.assertEqual(len(generic), 1)
        for t in pins:
            self.assertEqual({k: v for k, v in t.items() if k not in ('family', 'preferred_series')}, generic[0])
            self.assertEqual(t['preferred_series'], 'acs1_snap_household_receipt_rate')
        rest = [t for t in new['targets'] if t not in extra and t not in pins]
        rape = [t for t in rest if t['construct'] == 'rape']
        self.assertEqual(rape[0]['preferred_series'], 'fbi_ucr_rape_legacy_rate')
        rape[0].pop('preferred_series')
        self.assertEqual(rest, old['targets'])
        for key in set(old) - {'series', 'targets', 'catalog_id', 'inherited_population_metadata'}:
            self.assertEqual(old[key], new[key], key)

    def test_phrase_aliases_are_unique_across_the_whole_table(self):
        names = [a for t in self.pnew['targets'] for a in dict.fromkeys(t['aliases'])]
        for phrase in v19_sources.PHRASE_ALIASES.values():
            self.assertEqual(names.count(phrase), 1, phrase)
        for construct in (s[4] for s in NEW.values()):
            self.assertEqual([t['construct'] for t in self.pnew['targets'] if construct in t['aliases']], [construct])

    def test_v17b_and_v17c_stay_byte_identical(self):
        from scripts.l2_pipeline.storage import sha
        self.assertEqual(sha(CONNECTORS / 'OUTCOME_POPULATIONS_V17B.json'), 'b9544f7d9ae532caabab391c81a50cf249c5f16ab431adbcc850c805e667bb40')
        self.assertEqual(sha(CONNECTORS / 'OUTCOME_POPULATIONS_V17C.json'), '10b95da4c6621f1213c0692f933ff2ade1a8a6e55736ca02c3d833de1276f305')

    def test_v19_population_table_loads_against_the_v19_catalog(self):
        from scripts.l2_pipeline.connectors import population_v63
        source, meta = self.current()
        self.assertEqual((source['catalog_id'], meta['catalog_id']), ('first_order_outcomes_v19',) * 2)
        self.assertIs(population_v63.catalog_module(19), self.v19)
        self.assertTrue(population_v63.same_construct('construction wages', 'bls_qcew_private_construction_avg_weekly_wage', version=19))
        self.assertFalse(population_v63.same_construct('construction wages', 'bls_qcew_private_construction_employment', version=19))

    @unittest.skipUnless((CONNECTORS / 'population_v63.py').read_text().count("with_name('OUTCOME_POPULATIONS_V19.json')") == 1, 'pointer not switched to V19')
    def test_pointer_is_v19(self):
        from scripts.l2_pipeline.connectors import population_v63
        self.assertEqual(population_v63.CURRENT_PATH.name, 'OUTCOME_POPULATIONS_V19.json')
        source, meta = population_v63.current_catalog()
        self.assertEqual((source['catalog_id'], meta['catalog_id']), ('first_order_outcomes_v19',) * 2)

    def test_choose_under_v19_equals_v17c_for_every_v17c_construct_and_the_snap_families_keep_their_series(self):
        from scripts.l2_pipeline import population_resolution
        from scripts.l2_pipeline.connectors import outcome_v17, population_v63
        s17 = outcome_v17.catalog(verify_sources=False)
        _, m17 = population_v63.catalog(source=s17, path=CONNECTORS / 'OUTCOME_POPULATIONS_V17C.json')
        s19, m19 = population_v63.catalog(source=self.v19.catalog(verify_sources=False), path=CONNECTORS / 'OUTCOME_POPULATIONS_V19.json')

        def run(src, meta, name, family, construct):
            purpose = dict(status='VERIFIED', target_outcome=name, target_construct=construct)
            try:
                r = population_resolution.choose(dict(policy_family=family), purpose, src, meta)
            except Exception as exc:
                return 'err', type(exc).__name__ + ':' + str(exc)
            # eligible_scope of a pinned family target carries the pin keys; everything that decides the series is compared
            return 'ok', json.dumps({k: r[k] for k in ('construct', 'selected', 'candidates', 'decision', 'reason', 'secondary_series')}, sort_keys=True, default=str)
        pinned = [f for _, f, _ in v19_build.FAMILY_PINS]
        checked = generic_changed = 0
        for t in m17['targets']:
            for alias in dict.fromkeys([t['construct']] + t['aliases']):
                families = [t.get('family') or 'no_family'] + (pinned if t['construct'] == 'snap_participation' else [])
                for family in families:
                    a, b = run(s17, m17, alias, family, t['construct']), run(s19, m19, alias, family, t['construct'])
                    checked += 1
                    if a != b:
                        # only a family without a pin may see the new FNS candidate (hub B1: new designs choose by the normal rule)
                        generic_changed += 1
                        self.assertEqual((t['construct'], family), ('snap_participation', 'no_family'), alias)
                        self.assertEqual(json.loads(a[1])['selected']['series_id'], 'acs1_snap_household_receipt_rate')
                        self.assertEqual(json.loads(b[1])['selected']['series_id'], 'fns_snap_participation_pct_of_population')
        self.assertGreater(checked, 200)
        self.assertEqual(generic_changed, 5)       # the five SNAP aliases, family without a pin
        for family in pinned:
            r = population_resolution.choose(dict(policy_family=family), dict(status='VERIFIED', target_outcome='snap_participation', target_construct='snap_participation'), s19, m19)
            self.assertEqual(r['selected']['series_id'], 'acs1_snap_household_receipt_rate', family)
            self.assertEqual([c['series_id'] for c in r['candidates']], ['acs1_snap_household_receipt_rate'])

    def test_new_constructs_resolve_to_exactly_their_series_without_a_tie(self):
        from scripts.l2_pipeline import population_resolution
        source, meta = self.current()
        for sid, (_, naics, field, units, construct, text) in NEW.items():
            for name in [construct] + [p for c, p in v19_sources.PHRASE_ALIASES.items() if c == construct]:
                result = population_resolution.choose(dict(policy_family='any_family'), dict(status='VERIFIED', target_outcome=name), source, meta)
                self.assertEqual(result['selected']['series_id'], sid, name)
                self.assertEqual([c['series_id'] for c in result['candidates']], [sid], name)
                self.assertEqual(result['decision'], 'accept')

    def test_catalog_verifies_every_artifact_hash(self):
        data = self.v19.catalog(verify_sources=True)
        self.assertEqual(data['schema_version'], 19)

    def test_support_sidecars_are_value_free(self):
        for sid in NEW:
            spec = self.new['series'][sid]
            side = json.loads((REPO / spec['support_artifact']['path']).read_text())
            self.assertIs(side['outcome_values_in_sidecar'], False)
            self.assertEqual(side['outcome_association_calls'], 0)
            self.assertNotIn('rows', side)
        receipt = json.loads((ROOT / 'v19_20261023/BUILD_RECEIPT.json').read_text())
        self.assertEqual((receipt['schema'], receipt['outcome_association_calls'], receipt['actual_data_estimation_calls']), ('l2_v19_build_v1', 0, 0))
        self.assertEqual(len(receipt['sources']), 75)

    def test_every_new_series_registers_and_constructs_a_full_grid(self):
        from scripts.l2_pipeline import connectors
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        data = self.v19.catalog(verify_sources=False)
        self.v19.register()
        units = tuple(sorted(set(STATES.values()) - {'DC'}))
        for sid in sorted(NEW):
            spec = data['series'][sid]
            name = 'outcome_v19_' + sid
            self.assertIn(name, connectors.CONNECTOR_CATALOG, sid)
            self.assertEqual(connectors.FIRST_ORDER_MEASURES[name], {sid})
            years = tuple(spec['years'][-3:])
            artifact = spec['artifact']
            options = dict(connector=name, series_id=sid, measure_id=sid, population_filters=spec['population_filters'], frequency='annual',
                           paths=[artifact['path']], source_sha256={artifact['path']: artifact['sha256']})
            request = ConnectorRequest(options, dict(spec['population_filters']), years, units, 'annual', REPO)
            panel = self.v19._construct(request, data)
            self.assertEqual(len(panel.rows), len(units) * len(years), sid)
            self.assertEqual(len(panel.metadata), len(panel.rows), sid)
            self.assertTrue(all('outcome_value' in r for r in panel.rows), sid)
            self.assertEqual(all('outcome_scale' not in r for r in panel.rows), spec['units'] == 'jobs', sid)
            self.assertTrue(panel.inventory, sid)

    def test_inherited_v18_series_construct_through_v19(self):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        data = self.v19.catalog(verify_sources=False)
        self.v19.register()
        sid = 'bls_qcew_private_child_day_care_establishments'
        spec = data['series'][sid]
        options = dict(connector='outcome_v19_' + sid, series_id=sid, measure_id=sid, population_filters=spec['population_filters'], frequency='annual',
                       paths=[spec['artifact']['path']], source_sha256={spec['artifact']['path']: spec['artifact']['sha256']})
        request = ConnectorRequest(options, dict(spec['population_filters']), (2023,), ('AL', 'AK'), 'annual', REPO)
        self.assertEqual(len(self.v19._construct(request, data).rows), 2)

    def test_card_with_changed_hash_refuses(self):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        data = self.v19.catalog(verify_sources=False)
        self.v19.register()
        sid = 'bls_qcew_private_construction_employment'
        spec = data['series'][sid]
        options = dict(connector='outcome_v19_' + sid, series_id=sid, measure_id=sid, population_filters=spec['population_filters'], frequency='annual',
                       paths=[spec['artifact']['path']], source_sha256={spec['artifact']['path']: '0' * 64})
        request = ConnectorRequest(options, dict(spec['population_filters']), (2020,), tuple(sorted(set(STATES.values()) - {'DC'})), 'annual', REPO)
        with self.assertRaises(ConnectorError):
            self.v19._construct(request, data)

    def test_outcome_only_boundary_and_exact_identity(self):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        data = self.v19.catalog(verify_sources=False)
        request = ConnectorRequest(dict(connector='outcome_v19_x', series_id='x', treatment={}), {}, (2000,), ('AL',), 'annual', REPO)
        with self.assertRaises(ConnectorError):
            self.v19._construct(request, data)
        sid = 'bls_qcew_private_construction_employment'
        request = ConnectorRequest(dict(connector='outcome_v18_' + sid, series_id=sid, measure_id=sid), {}, (2000,), ('AL',), 'annual', REPO)
        with self.assertRaises(ConnectorError):
            self.v19._construct(request, data)

    def test_first_order_target_mapping_is_true_for_construct_and_phrase_and_false_for_a_wrong_measure(self):
        from scripts.l2_pipeline import first_order
        self.v19.register()
        rows = [('bls_qcew_private_offices_of_other_health_practitioners_establishments', 'other_health_practitioner_office_establishments', 'entry of independent practitioner practices'),
                ('bls_qcew_private_taxi_limousine_employment', 'taxi_limousine_payroll_employment', 'employment in taxi and limousine service'),
                ('bls_qcew_private_construction_avg_weekly_wage', 'construction_wages', 'construction wages')]
        for sid, construct, phrase in rows:
            for target in (construct, phrase):
                card = dict(design_id='test_design', outcome=dict(connector='outcome_v19_' + sid, measure_id=sid, units=['AL', 'AK']),
                            first_order=dict(target_outcome=target, primary_outcome=sid, relation='closest_proxy', proxy_justification='Payroll series proxy for the targeted activity.'))
                self.assertIs(first_order.target_mapping(card)['mapped'], True, (sid, target))
                wrong = deepcopy(card)
                wrong['outcome']['measure_id'] = 'bls_qcew_private_construction_employment' if sid != 'bls_qcew_private_construction_employment' else 'bls_qcew_private_taxi_limousine_employment'
                wrong['outcome']['connector'] = 'outcome_v19_' + wrong['outcome']['measure_id']
                self.assertIs(first_order.target_mapping(wrong)['mapped'], False, (sid, target))

    def test_c50_and_first_order_accept_the_v19_catalog_id_and_names(self):
        from scripts.l2_pipeline import c50, first_order
        _, meta = self.current()
        self.assertEqual(meta['catalog_id'], 'first_order_outcomes_v19')
        # c50 answers for a reviewed aggregate construct under the V19 table (it returns None only for a catalog id outside its set)
        self.assertIsNotNone(c50.equivalence_target('violent crime offense incidence', meta=meta))
        self.assertTrue(first_order._same_construct(dict(connector='outcome_v19_bls_qcew_private_construction_employment'), 'construction_payroll_employment',
                                                    'bls_qcew_private_construction_employment'))

    def test_the_ipeds_c66_names_include_v19(self):
        from scripts.l2_pipeline.connectors import rates
        card = dict(outcome=dict(connector='outcome_v19_ipeds_enrollment_per_population'))
        self.assertTrue(any(s.get('name') == 'ipeds_coverage_99' for s in rates.prospective_card(card).get('sensitivities', [])))


if __name__ == '__main__':
    unittest.main()
