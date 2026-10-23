"""C21 FARS role/age census contracts; no outcome associations or fits."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from .c21_fars import FILTERS, _rows, construct, parse_archive
from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, STATES, sha


def csv_bytes(rows):
    buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return buffer.getvalue().encode()


def archive(base, year, overrides=None, malformed=None):
    accidents, persons = [], []
    for fips in STATES:
        accidents.append(dict(STATE=int(fips), ST_CASE=int(fips)*10000+1, YEAR=year, FATALS=1))
        persons.append(dict(STATE=int(fips), ST_CASE=int(fips)*10000+1, VEH_NO=1, PER_NO=1,
                            N_MOT_NO=0, INJ_SEV=4, PER_TYP=1, AGE=25))
    if overrides:
        for fips, change in overrides.items():
            persons[list(STATES).index(fips)].update(change)
    if malformed:
        malformed(accidents, persons)
    path = base / f'FARS{year}NationalCSV.zip'
    with zipfile.ZipFile(path, 'w') as zipped:
        zipped.writestr('ACCIDENT.csv', csv_bytes(accidents)); zipped.writestr('PERSON.csv', csv_bytes(persons))
    return path


class C21FarsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_pedalcyclist_codes_age_core_and_zero(self):
        for year in (1999, 2021, 2022, 2024):
            p = archive(self.base, year, {'01': {'PER_TYP': 6, 'AGE': 5}, '02': {'PER_TYP': 7, 'AGE': 14},
                                         '04': {'PER_TYP': 7, 'AGE': 15}, '05': {'PER_TYP': 5, 'AGE': 10}})
            data = parse_archive(p, year)
            self.assertEqual(data['AL']['pedalcyclist_age5_14'], 1)
            self.assertEqual(data['AK']['pedalcyclist_age5_14'], 1)
            self.assertEqual(data['AZ']['pedalcyclist_age5_14'], 0)
            self.assertEqual(data['AR']['pedalcyclist_age5_14'], 0)
            self.assertEqual(len(data), 51)

    def test_all_age_occupants_include_unknown_age_and_nontransport(self):
        p = archive(self.base, 2024, {'01': {'PER_TYP': 3, 'AGE': 999}, '02': {'PER_TYP': 9, 'AGE': 998},
                                     '04': {'PER_TYP': 2, 'AGE': 10}, '05': {'PER_TYP': 6, 'AGE': 999}})
        data = parse_archive(p, 2024)
        self.assertEqual(data['AL']['occupant'], 1); self.assertEqual(data['AL']['occupant_not_in_transport'], 1)
        self.assertEqual(data['AK']['occupant'], 1)
        self.assertEqual(data['AR']['pedalcyclist_age_unknown'], 1)
        self.assertEqual(data['AR']['pedalcyclist_age5_14'], 0)

    def test_unknown_age_vintage(self):
        for year, age in [(1999, 99), (2008, 99), (2009, 999), (2024, 998)]:
            data = parse_archive(archive(self.base, year, {'01': {'PER_TYP': 7, 'AGE': age}}), year)
            self.assertEqual(data['AL']['pedalcyclist_age_unknown'], 1)
        with self.assertRaisesRegex(ConnectorError, 'vintage age'):
            parse_archive(archive(self.base, 2024, {'01': {'PER_TYP': 7, 'AGE': 997}}), 2024)

    def test_legacy_label_encoding_cannot_change_numeric_contract(self):
        p = self.base / 'labels.zip'
        with zipfile.ZipFile(p, 'w') as zipped:
            zipped.writestr('person.csv', b'STATE,AGE,LABEL\n1,14,legacy\x82label\n')
        with zipfile.ZipFile(p) as zipped:
            self.assertEqual(list(_rows(zipped, 'person.csv', {'STATE', 'AGE'}))[0]['AGE'], '14')
        with zipfile.ZipFile(p, 'w') as zipped:
            zipped.writestr('person.csv', b'\xef\xbb\xbfSTATE,AGE,LABEL\n1,14,label\n')
        with zipfile.ZipFile(p) as zipped:
            self.assertEqual(list(_rows(zipped, 'person.csv', {'STATE', 'AGE'}))[0]['STATE'], '1')
        with zipfile.ZipFile(p, 'w') as zipped:
            zipped.writestr('person.csv', b'STATE,AGE,LABEL\n1,1\x824,label\n')
        with zipfile.ZipFile(p) as zipped, self.assertRaisesRegex(ConnectorError, 'Non-ASCII'):
            list(_rows(zipped, 'person.csv', {'STATE', 'AGE'}))

    def test_crash_reconciliation_and_person_linkage_fail_closed(self):
        for mutate in [lambda a,p: p.pop(), lambda a,p: p.append(dict(p[0])),
                       lambda a,p: p[0].update(ST_CASE=999999), lambda a,p: a[0].update(FATALS=2),
                       lambda a,p: a.pop()]:
            with self.subTest(mutate=mutate), self.assertRaises(ConnectorError):
                parse_archive(archive(self.base, 2024, malformed=mutate), 2024)

    def test_artifact_requires_hash_exact_population_and_complete_cells(self):
        sid = 'fars_motor_vehicle_occupant_fatality_rate'; path = self.base / 'panel.json'
        record = dict(unit='AL', year=2024, period='2024', frequency='annual', outcome_value=1.0,
                      outcome_unit='deaths per 100,000 residents')
        data = dict(series_id=sid, population_filters=FILTERS[sid], years=[2024], rows=[record],
                    metadata=[dict(record, available=1, linkage_certified=1)], inventory=[], limitations=[], details={})
        path.write_text(json.dumps(data))
        options = dict(series_id=sid, paths=['panel.json'], source_sha256={'panel.json': sha(path)})
        request = ConnectorRequest(options, FILTERS[sid], (2024,), ('AL',), 'annual', self.base)
        self.assertEqual(construct(request).rows[0]['outcome_value'], 1.0)
        absolute = dict(options, paths=[str(path)], source_sha256={str(path): sha(path)})
        self.assertEqual(construct(ConnectorRequest(absolute, FILTERS[sid], (2024,), ('AL',), 'annual', self.base)).rows[0]['outcome_value'], 1.0)
        conflict = dict(absolute, source_sha256={str(path): sha(path), 'panel.json': '0'*64})
        with self.assertRaisesRegex(ConnectorError, 'SHA-256'):
            construct(ConnectorRequest(conflict, FILTERS[sid], (2024,), ('AL',), 'annual', self.base))
        with self.assertRaises(ConnectorNotReady):
            construct(ConnectorRequest(options, {'population': 'all'}, (2024,), ('AL',), 'annual', self.base))
        with self.assertRaises(ConnectorError):
            construct(ConnectorRequest(dict(options, source_sha256={}), FILTERS[sid], (2024,), ('AL',), 'annual', self.base))
        with self.assertRaisesRegex(ConnectorError, 'Incomplete'):
            construct(ConnectorRequest(options, FILTERS[sid], (2024,), ('AL', 'AK'), 'annual', self.base))


if __name__ == '__main__':
    unittest.main()
