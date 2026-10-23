"""Synthetic C35 public-source tests; no archival fitting or associations."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from . import c35_public as public
from .common import ConnectorError
from .c35_core import SUPPORT_FIELDS


class C35PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def write(self, name, rows, fields):
        path = self.base / name
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def turnout(self, historical, provisional=()):
        fields = ['YEAR', 'STATE', 'STATE_ABV', 'TOTAL_BALLOTS_COUNTED', 'VOTE_FOR_HIGHEST_OFFICE', 'VEP']
        hist = self.write('history.csv', historical, fields)
        prov = self.write('provisional.csv', provisional, ['STATE', 'STATE_ABV', 'TOTAL_BALLOTS_COUNTED', '', 'VEP'])
        return hist, prov

    def election(self, year=1980, unit='AL', name='Alabama', highest='400', ballots='500', vep='1,000'):
        return dict(YEAR=year, STATE=name, STATE_ABV=unit,
                    TOTAL_BALLOTS_COUNTED=ballots, VOTE_FOR_HIGHEST_OFFICE=highest, VEP=vep)

    def cell(self, panel, unit='AL', year=1980):
        rows, meta = panel
        return next(r for r in rows if r['unit'] == unit and r['year'] == year), next(m for m in meta if m['unit'] == unit and m['year'] == year)

    def crime(self, year=1979, unit='AL', name='Alabama', homicide='10', population='100000', caveats=''):
        return dict(year=year, state_abbr=unit, state_name=name, homicide=homicide, population=population, caveats=caveats)

    def ucr(self, rows):
        return self.write('ucr.csv', rows, ['year', 'state_abbr', 'state_name', 'population', 'homicide', 'caveats'])

    def test_turnout_explicit_numerators_comma_denominator_percent(self):
        panels = public.parse_turnout(*self.turnout([self.election()]), units=('AL',))
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[0]])[0]['outcome_value'], 40)
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[1]])[0]['outcome_value'], 50)
        self.assertEqual(len(panels[public.TURNOUT_IDS[0]][1]), 23)
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[0]])[0]['frequency'], 'biennial')

    def test_missing_primary_never_falls_back_to_ballots(self):
        panels = public.parse_turnout(*self.turnout([self.election(highest='')]), units=('AL',))
        primary, meta = self.cell(panels[public.TURNOUT_IDS[0]])
        self.assertEqual(primary['outcome_value'], '')
        self.assertEqual(meta['annual_usable'], 0)
        self.assertEqual(meta['missing_reason'], 'highest_office_not_published')
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[1]])[1]['available'], 1)

    def test_2022_source_url_is_flagged_missing_count(self):
        panels = public.parse_turnout(*self.turnout([self.election(year=2022, highest='https://example.org/source')]), units=('AL',))
        row, meta = self.cell(panels[public.TURNOUT_IDS[0]], year=2022)
        self.assertEqual(row['outcome_value'], '')
        self.assertEqual(meta['source_issue'], 'source_url_in_count_column')
        self.assertEqual(meta['available'], 0)
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[1]], year=2022)[1]['annual_usable'], 1)

    def test_2024_primary_absent_ballots_available_provisional_unusable(self):
        provisional = dict(STATE='Alabama', STATE_ABV='AL', TOTAL_BALLOTS_COUNTED='500', VEP='1000')
        panels = public.parse_turnout(*self.turnout([], [provisional]), units=('AL',))
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[0]], year=2024)[0]['outcome_value'], '')
        row, meta = self.cell(panels[public.TURNOUT_IDS[1]], year=2024)
        self.assertEqual(row['outcome_value'], 50)
        self.assertEqual((meta['available'], meta['provisional'], meta['annual_usable']), (1, 1, 0))

    def test_turnout_state_footnote_and_national_ignored(self):
        panels = public.parse_turnout(*self.turnout([
            self.election(unit='MT', name='Montana*'), self.election(unit='', name='United States')]), units=('MT',))
        self.assertEqual(self.cell(panels[public.TURNOUT_IDS[0]], unit='MT')[1]['available'], 1)

    def test_turnout_duplicate_and_odd_year_rejected(self):
        for sources in ([self.election(), self.election()], [self.election(year=1981)]):
            with self.assertRaises(ConnectorError):
                public.parse_turnout(*self.turnout(sources), units=('AL',))

    def test_zero_or_missing_denominator_unavailable(self):
        for vep in ('0', ''):
            panels = public.parse_turnout(*self.turnout([self.election(vep=vep)]), units=('AL',))
            row, meta = self.cell(panels[public.TURNOUT_IDS[0]])
            self.assertEqual(row['outcome_value'], '')
            self.assertEqual(meta['denominator_available'], 0)

    def test_bad_count_and_unexpected_source_url_rejected(self):
        for value in ('1,2', '-1', 'nanx', 'https://example.org/source'):
            with self.assertRaises(ConnectorError):
                public.parse_turnout(*self.turnout([self.election(highest=value)]), units=('AL',))

    def test_ucr_estimated_rate_population_and_zero_offenses(self):
        panel = public.parse_ucr(self.ucr([self.crime(homicide='10', population='200000'), self.crime(year=1980, homicide='0')]), units=('AL',))
        self.assertEqual(self.cell(panel, year=1979)[0]['outcome_value'], 5)
        self.assertEqual(self.cell(panel, year=1980)[0]['outcome_value'], 0)
        self.assertEqual(self.cell(panel, year=1980)[1]['available'], 1)

    def test_ucr_pinned_nd2022_correction(self):
        panel = public.parse_ucr(self.ucr([self.crime(year=2022, unit='NC', name='     North Carolina'),
                                         self.crime(year=2022, unit='NC', name='     North Dakota')]), units=('NC', 'ND'))
        self.assertEqual(self.cell(panel, unit='ND', year=2022)[1]['source_id_corrected'], 1)
        self.assertEqual(self.cell(panel, unit='NC', year=2022)[1]['source_id_corrected'], 0)
        with self.assertRaises(ConnectorError):
            public.parse_ucr(self.ucr([self.crime(year=2021, unit='NC', name='North Dakota')]))

    def test_ucr_2025_limited_flags_both_states_no_blanket_provisional(self):
        sources = [self.crime(year=2025, unit='FL', name='     Florida3'),
                   self.crime(year=2025, unit='ND', name='     North Dakota3', caveats='3 Limited data for 2025 were available for Florida and North Dakota.'),
                   self.crime(year=2025, unit='DC', name='     District of Columbia4')]
        panel = public.parse_ucr(self.ucr(sources), units=('FL', 'ND', 'DC'))
        for unit in ('FL', 'ND'):
            meta = self.cell(panel, unit=unit, year=2025)[1]
            self.assertEqual((meta['available'], meta['limited_data'], meta['annual_usable']), (1, 1, 0))
        self.assertEqual(self.cell(panel, unit='DC', year=2025)[1]['annual_usable'], 1)

    def test_ucr_reporting_break_metadata_and_preserved_caveat(self):
        panel = public.parse_ucr(self.ucr([self.crime(year=2011, caveats="figures are not comparable to previous years' data")]), units=('AL',))
        row, meta = self.cell(panel, year=2011)
        self.assertEqual(meta['series_break'], 'reporting_practice_change')
        self.assertIn('not comparable', row['source_caveats'])

    def test_ucr_missing_and_zero_denominator_never_filled(self):
        panel = public.parse_ucr(self.ucr([self.crime(population='0')]), units=('AL',))
        self.assertEqual(self.cell(panel, year=1979)[1]['annual_usable'], 0)
        self.assertEqual(self.cell(panel, year=1980)[1]['missing_reason'], 'missing_source_cell')

    def test_ucr_duplicate_and_other_state_mismatch_rejected(self):
        for sources in ([self.crime(), self.crime()], [self.crime(unit='AK', name='Alabama')]):
            with self.assertRaises(ConnectorError):
                public.parse_ucr(self.ucr(sources))

    def test_support_metadata_has_no_values_or_counts(self):
        panels = public.parse_turnout(*self.turnout([self.election()]), units=('AL',))
        panels['ucr'] = public.parse_ucr(self.ucr([self.crime()]), units=('AL',))
        for _, meta in panels.values():
            for cell in meta:
                self.assertTrue(set(cell) <= SUPPORT_FIELDS)
                self.assertFalse({'outcome_value', 'population', 'homicide', 'VEP', 'numerator'} & set(cell))

    def test_build_records_turnout_republication_restriction(self):
        def fake_save(sid, filters, rows, meta, inventory, limitations, details, population, source_url, **extra):
            return dict(series_id=sid, details=details, limitations=limitations, **extra)
        with patch.object(public, 'parse_turnout', return_value={sid: ([], []) for sid in public.TURNOUT_IDS}), \
             patch.object(public, 'parse_ucr', return_value=([], [])), \
             patch.object(public, 'source_record', return_value={}), \
             patch.object(public, 'save_panel', side_effect=fake_save):
            specs = public.build()
        for sid in public.TURNOUT_IDS:
            self.assertTrue(specs[sid]['analysis_only'])
            self.assertTrue(specs[sid]['republication_prohibited'])
            self.assertTrue(specs[sid]['details']['license_ambiguity'])
        self.assertTrue(specs['fbi_ucr_homicide_rate']['alternative_homicide_source'])


if __name__ == '__main__':
    unittest.main()
