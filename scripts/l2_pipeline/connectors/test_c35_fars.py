"""Synthetic C35 historical role, exact-band and fail-closed tests; no fits."""
import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from .common import ConnectorError, STATES
from .c35_fars import historical_rows, parse_archive, parse_pe19, parse_s5yr, parse_counties, RACE_SEX_1970


def csv_bytes(rows):
    out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows); return out.getvalue().encode()


def archive(base, year, changes=None, mutation=None, two_digit=True):
    accidents, persons = [], []
    for fips in STATES:
        accidents.append(dict(STATE=int(fips), ST_CASE=int(fips)*10000+1,
            YEAR=year % 100 if two_digit else year, FATALS=1))
        persons.append(dict(STATE=int(fips), ST_CASE=int(fips)*10000+1, VEH_NO=1, PER_NO=1,
            INJ_SEV=4, PER_TYP=1, AGE=25))
    for fips, change in (changes or {}).items():
        persons[list(STATES).index(fips)].update(change)
    if mutation:
        mutation(accidents, persons)
    path = base / f'FARS{year}NationalCSV.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('ACCIDENT.CSV', csv_bytes(accidents)); z.writestr('PERSON.CSV', csv_bytes(persons))
    return path


class C35FarsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_1975_1981_cyclists4_pedestrians3_occupants129(self):
        for year in (1975, 1981):
            counts = parse_archive(archive(self.base, year, {'01':dict(PER_TYP=4, AGE=5),
                '02':dict(PER_TYP=4, AGE=14), '04':dict(PER_TYP=3, AGE=10),
                '05':dict(PER_TYP=9, AGE=99), '06':dict(PER_TYP=4, AGE=15)}), year)
            self.assertEqual(counts['AL']['pedalcyclist_age5_14'], 1)
            self.assertEqual(counts['AK']['pedalcyclist_age5_14'], 1)
            self.assertEqual(counts['AZ']['pedalcyclist_age5_14'], 0)
            self.assertEqual(counts['AZ']['occupant'], 0)
            self.assertEqual(counts['AR']['occupant'], 1)
            self.assertEqual(counts['CA']['pedalcyclist_age5_14'], 0)

    def test_1982_recoding_and_unknown_age_support(self):
        for year in (1982, 1998):
            counts = parse_archive(archive(self.base, year, {'01':dict(PER_TYP=6, AGE=5),
                '02':dict(PER_TYP=7, AGE=14), '04':dict(PER_TYP=3, AGE=99),
                '05':dict(PER_TYP=7, AGE=99), '06':dict(PER_TYP=4, AGE=10)}, two_digit=year != 1998), year)
            self.assertEqual(counts['AL']['pedalcyclist_age5_14'], 1)
            self.assertEqual(counts['AK']['pedalcyclist_other_cycle'], 1)
            self.assertEqual(counts['AZ']['occupant_not_in_transport'], 1)
            self.assertEqual(counts['AR']['pedalcyclist_age_unknown'], 1)
            self.assertEqual(counts['AR']['pedalcyclist_age5_14'], 0)
            self.assertEqual(counts['CA']['pedalcyclist_age5_14'], 0)

    def test_unknown_source_crash_year_flag_and_no_other_year_substitution(self):
        path = archive(self.base, 1975, mutation=lambda a,p:a[0].update(YEAR=99))
        self.assertTrue(parse_archive(path, 1975)['AL']['unknown_year_present'])
        with self.assertRaisesRegex(ConnectorError, 'wrong year'):
            parse_archive(archive(self.base, 1975, mutation=lambda a,p:a[0].update(YEAR=76)), 1975)

    def test_published_zero_FATALS_and_blank_optional_identifier(self):
        def zero_crash(a, p):
            a[0]['FATALS'] = 0; p[0]['INJ_SEV'] = 0
        counts = parse_archive(archive(self.base, 1975, mutation=zero_crash), 1975)
        self.assertTrue(counts['AL']['zero_fatals_crash_present'])
        self.assertEqual(counts['AL']['all_road'], 0)
        def blank_optional(a, p):
            for row in p:row['N_MOT_NO'] = 0
            p[0].update(N_MOT_NO='', PER_TYP=5, VEH_NO=0)
        counts = parse_archive(archive(self.base, 1985, mutation=blank_optional), 1985)
        self.assertTrue(counts['AL']['optional_nonmotor_id_missing_present'])
        self.assertEqual(counts['AL']['occupant'], 0)

    def test_ancillary_nul_explicit_and_numeric_nul_rejected(self):
        path = self.base / 'labels.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('PERSON.CSV', b'STATE,AGE,SER_TR\n1,14,S35\x00\x00\x00\n')
        with zipfile.ZipFile(path) as z:
            row = list(historical_rows(z, 'person.csv', {'STATE', 'AGE'}))[0]
            self.assertEqual(row['AGE'], '14'); self.assertIn('\u2400', row['SER_TR'])
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('PERSON.CSV', b'STATE,AGE,SER_TR\n1,1\x004,S35\n')
        with zipfile.ZipFile(path) as z, self.assertRaisesRegex(ConnectorError, 'Non-ASCII'):
            list(historical_rows(z, 'person.csv', {'STATE', 'AGE'}))

    def test_reconciliation_and_links_fail_closed(self):
        for mutation in [lambda a,p:p.pop(), lambda a,p:p.append(dict(p[0])),
                         lambda a,p:a[0].update(FATALS=2), lambda a,p:p[0].update(ST_CASE=999999),
                         lambda a,p:a.pop()]:
            with self.subTest(mutation=mutation), self.assertRaises(ConnectorError):
                parse_archive(archive(self.base, 1981, mutation=mutation), 1981)
        with self.assertRaisesRegex(ConnectorError, 'age code'):
            parse_archive(archive(self.base, 1981, {'01':dict(PER_TYP=4, AGE=98)}), 1981)

    def make_pe19(self, missing=False, duplicate=False):
        rows = []
        for year in range(1970, 1980):
            for stratum in sorted(RACE_SEX_1970):
                rows.append([year,'01','Alabama',stratum]+[1,2,3]+[4]*15)
        if missing:rows.pop()
        if duplicate:rows.append(list(rows[0]))
        path = self.base / 'pe-19.csv'
        with path.open('w', newline='') as f:
            csv.writer(f).writerows([['Title'],['Year of Estimate']]+rows)
        return path

    def test_pe19_exclusive_sex_race_and_exact5_9_plus10_14(self):
        total, age = parse_pe19(self.make_pe19(), ('AL',))
        self.assertEqual(len(total), 10); self.assertEqual(total['AL',1975], 6*66)
        self.assertEqual(age['AL',1975], 6*5)
        with self.assertRaises(ConnectorError):parse_pe19(self.make_pe19(missing=True), ('AL',))
        with self.assertRaises(ConnectorError):parse_pe19(self.make_pe19(duplicate=True), ('AL',))

    def test_s5yr_july_columns_and_first_both_sex_only(self):
        path = self.base / 's5yr8090.txt'
        columns = lambda values:' '.join(map(str,values))
        path.write_text('Title\nAlabama\nTotal '+columns([999]+list(range(100,110))+[888])+
            '\n5 to 9 years '+columns([999]+list(range(10,20))+[888])+
            '\n10 to 14 years '+columns([999]+list(range(20,30))+[888])+
            '\nMale 0\n5 to 9 years '+columns([1000]*12)+
            '\n10 to 14 years '+columns([1000]*12)+'\n')
        total, age = parse_s5yr(path, ('AL',))
        self.assertEqual(total['AL',1980], 100); self.assertEqual(total['AL',1989], 109)
        self.assertEqual(age['AL',1980], 30); self.assertEqual(age['AL',1989], 48)

    def county_file(self, missing=False, duplicate=False, state_total=False):
        lines = [f'90 {county} {band} {race} {origin} 2\n'
            for county in ['01001','01003'] for band in range(19)
            for race in range(1,9) for origin in [1,2]]
        if missing:lines.pop()
        if duplicate:lines.append(lines[0])
        if state_total:lines[0]=lines[0].replace('01001','01000')
        path=self.base/'stch-icen1990.txt';path.write_text(''.join(lines));return path

    def test_1990s_sum_counties_all_exclusive_cells_no_subtotal(self):
        total, age, check = parse_counties(self.county_file(), 1990, ('AL',))
        self.assertEqual(total['AL',1990], 2*19*8*2*2)
        self.assertEqual(age['AL',1990], 2*2*8*2*2);self.assertEqual(check['counties'],2)
        for kw in ['missing','duplicate','state_total']:
            with self.assertRaises(ConnectorError):parse_counties(self.county_file(**{kw:True}),1990,('AL',))
        with self.assertRaises(ConnectorError):parse_counties(self.county_file(),1991,('AL',))


if __name__ == '__main__':
    unittest.main()
