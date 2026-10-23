"""C35 pinned local election/UCR inputs; no treatment labels or associations."""
from __future__ import annotations
import csv
import math
import re
from pathlib import Path
from .common import ConnectorError, NAMES, STATES, metadata, row
from .c35_core import REPO, save_panel, source_record

TURNOUT_IDS = ('turnout_vep_highest_office_rate', 'turnout_vep_total_ballots_rate')
TURNOUT_URL = 'https://election.lab.ufl.edu/data-downloads/turnoutdata/'
UCR_URL = 'https://cde.ucr.cjis.gov/LATEST/s3/signedurl?key=additional-datasets/srs/estimated_crimes_1979_2025.csv'
TURNOUT_LICENSE = 'Conflicting CC BY 4.0 file documentation / CC BY-NC-ND 4.0 archive; analysis only; no dataset republication'
ALL_UNITS = tuple(sorted(STATES.values()))


def _read(path, required):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise ConnectorError('C35 source schema mismatch')
        for source in reader:
            if None in source or any(v is None for v in source.values()):
                raise ConnectorError('C35 malformed CSV row')
            yield source


def _count(text):
    text = text.strip()
    if text.lower() in ('', 'na', 'n/a', 'null', 'nan', 'tbd', 'suppressed'):
        return None
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)', text):
        raise ConnectorError('C35 invalid nonnegative integer count')
    return int(text.replace(',', ''))


def _state(source, *, turnout=False, year=None):
    name = source['STATE' if turnout else 'state_name'].strip()
    abbr = source['STATE_ABV' if turnout else 'state_abbr'].strip()
    if abbr in ('', 'US'):
        if name not in ('', 'United States', 'United States Total'):
            raise ConnectorError('C35 unidentified nonnational geography')
        return None, False, False
    if turnout:
        normalized = name.rstrip('*')
        limited = False
    else:
        normalized = re.sub(r'[34]$', '', name) if year == 2025 else name
        limited = year == 2025 and name in ('Florida3', 'North Dakota3')
    canonical = NAMES.get(normalized)
    corrected = not turnout and year == 2022 and abbr == 'NC' and canonical == 'ND'
    if canonical is None or (canonical != abbr and not corrected):
        raise ConnectorError('C35 state name/abbreviation mismatch')
    return canonical, corrected, limited


def parse_turnout(historical, provisional, units=ALL_UNITS):
    """Return two explicit numerators over the VEP; never replace a numerator."""
    values = {}
    for path, fixed_year in ((historical, None), (provisional, 2024)):
        required = {'STATE', 'STATE_ABV', 'TOTAL_BALLOTS_COUNTED', 'VEP'}
        if fixed_year is None:
            required |= {'YEAR', 'VOTE_FOR_HIGHEST_OFFICE'}
        for source in _read(path, required):
            try:
                year = fixed_year if fixed_year is not None else int(source['YEAR'])
            except ValueError as exc:
                raise ConnectorError('C35 invalid election year') from exc
            if year not in range(1980, 2025, 2) or (fixed_year is None and year > 2022):
                raise ConnectorError('C35 election year outside pinned vintage')
            unit, _, _ = _state(source, turnout=True, year=year)
            if unit is None:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('C35 duplicate election state/year')
            denominator = _count(source['VEP'])
            raw_primary = source.get('VOTE_FOR_HIGHEST_OFFICE', '').strip()
            source_issue = 'source_url_in_count_column' if re.match(r'https?://', raw_primary) else ''
            if source_issue and year != 2022:
                raise ConnectorError('C35 unexpected URL in election count')
            highest = None if source_issue else _count(raw_primary)
            ballots = _count(source['TOTAL_BALLOTS_COUNTED'])
            values[key] = (denominator, highest, ballots, fixed_year is not None, str(path), source_issue)
    panels = {}
    for sid, numerator_index in zip(TURNOUT_IDS, (1, 2)):
        rows, support = [], []
        for unit in sorted(units):
            if unit not in ALL_UNITS:
                raise ConnectorError('C35 invalid requested unit')
            for year in range(1980, 2025, 2):
                source = values.get((unit, year))
                denominator, numerator = (source[0], source[numerator_index]) if source else (None, None)
                denominator_available = denominator is not None and denominator > 0
                available = denominator_available and numerator is not None
                provisional_flag = year == 2024
                issue = source[5] if source and numerator_index == 1 else ''
                reason = ('missing_source_cell' if source is None else 'denominator_missing_or_zero'
                          if not denominator_available else issue or 'highest_office_not_published'
                          if numerator is None and numerator_index == 1 else 'total_ballots_not_published'
                          if numerator is None else '')
                value = 100.0 * numerator / denominator if available else ''
                if value != '' and not math.isfinite(value):
                    raise ConnectorError('C35 invalid turnout rate')
                record = row(unit, year, value, 'percent of voting-eligible population', 'biennial',
                             source=source[4] if source else '', provisional=int(provisional_flag))
                rows.append(record)
                support.append(dict(metadata(record, available, source is not None),
                    annual_usable=int(available and not provisional_flag), provisional=int(provisional_flag),
                    published_cell_present=int(source is not None), denominator_available=int(denominator_available),
                    missing_reason=reason, source_issue=issue))
        panels[sid] = (rows, support)
    return panels


def parse_ucr(path, units=ALL_UNITS):
    values = {}
    required = {'year', 'state_abbr', 'state_name', 'population', 'homicide', 'caveats'}
    for source in _read(path, required):
        try:
            year = int(source['year'])
        except ValueError as exc:
            raise ConnectorError('C35 invalid UCR year') from exc
        if not 1979 <= year <= 2025:
            raise ConnectorError('C35 UCR year outside pinned vintage')
        unit, corrected, limited = _state(source, year=year)
        if unit is None:
            continue
        key = unit, year
        if key in values:
            raise ConnectorError('C35 duplicate estimated-crime state/year')
        denominator, numerator = _count(source['population']), _count(source['homicide'])
        caveat = source['caveats'].strip()
        reporting_break = year in (2011, 2012) and bool(re.search(r'not comparable', caveat, re.I))
        values[key] = (denominator, numerator, corrected, limited, reporting_break, caveat,
                       source['state_name'], source['state_abbr'])
    rows, support = [], []
    for unit in sorted(units):
        if unit not in ALL_UNITS:
            raise ConnectorError('C35 invalid requested unit')
        for year in range(1979, 2026):
            source = values.get((unit, year))
            denominator, numerator = source[:2] if source else (None, None)
            denominator_available = denominator is not None and denominator > 0
            available = denominator_available and numerator is not None
            limited = bool(source and source[3])
            value = 100000.0 * numerator / denominator if available else ''
            record = row(unit, year, value, 'estimated homicide offenses per 100000 residents',
                         source=str(path) if source else '',
                         source_caveats=source[5] if source else '',
                         source_state_name=source[6] if source else '',
                         source_state_abbreviation=source[7] if source else '')
            rows.append(record)
            support.append(dict(metadata(record, available, source is not None),
                annual_usable=int(available and not limited), limited_data=int(limited),
                provisional=0, published_cell_present=int(source is not None),
                denominator_available=int(denominator_available),
                source_id_corrected=int(bool(source and source[2])),
                series_break='reporting_practice_change' if source and source[4] else '',
                missing_reason='missing_source_cell' if source is None else
                    'denominator_missing_or_zero' if not denominator_available else
                    'homicide_count_not_published' if numerator is None else '',
                source_issue='limited_2025_data' if limited else
                    'ND2022_abbreviation_corrected_by_state_name' if source and source[2] else ''))
    return rows, support


def build():
    base = REPO / 'data/l2_pipeline/connectors_v2'
    turnout = base / 'turnout_vep/raw'
    historical = turnout / 'Turnout_1980_2022_v1.2.csv'
    provisional = turnout / 'Turnout_2024G_v0.4.csv'
    turnout_inventory = [source_record(p) for p in (historical, provisional,
        turnout / 'Turnout_1980_2022_v1.2_doc.txt', turnout / 'Turnout_2024G_v0.4_doc.txt')]
    specs = {}
    for sid, (rows, support) in parse_turnout(historical, provisional).items():
        numerator = 'highest_office' if sid == TURNOUT_IDS[0] else 'total_ballots'
        specs[sid] = save_panel(sid, {'population': 'voting_eligible', 'numerator': numerator},
            rows, support, turnout_inventory, (
                TURNOUT_LICENSE,
                'Highest-office votes and total ballots are separate measures; missing numerators never substituted.',
                'Historical highest-office2020 absent;2022 numeric column contains source URLs;2024 not published.',
                '2024 v0.4 is provisional and excluded from default usable support.'),
            dict(analysis_only=True, republication_prohibited=True,
                 license_ambiguity={'file_documentation': 'CC BY 4.0', 'archive_page': 'CC BY-NC-ND 4.0'},
                 attribution='Michael McDonald, University of Florida Election Lab',
                 numerator=numerator, denominator='published VEP', source_vintages=['v1.2 2024-10-08', 'v0.4 2026-03-02']),
            'Voting-eligible state population; published VEP', TURNOUT_URL, frequency='biennial',
            analysis_only=True, republication_prohibited=True, license_status='ambiguous_restricted_analysis_only')
    path = base / 'fbi_ucr_state/raw/estimated_crimes_1979_2025.extracted.csv'
    rows, support = parse_ucr(path)
    inventory = [source_record(path), source_record(path.with_name('estimated_crimes_1979_2025.xlsx'))]
    sid = 'fbi_ucr_homicide_rate'
    specs[sid] = save_panel(sid, {'series': 'estimated_crimes', 'offense': 'homicide'},
        rows, support, inventory, (
            'FBI estimated SRS homicide offenses, alternative source to resident death-certificate homicide; not firearm-specific.',
            'Population denominator comes from same estimated-crimes source; rate is crude.',
            'ND2022 source abbreviation NC is corrected only from explicit North Dakota state name.',
            '2025 Florida and North Dakota limited-data footnote excludes default usable support.',
            'Publisher reporting-practice breaks and source caveats remain recorded.'),
        dict(alternative_homicide_source=True, denominator='publisher population',
             offense_definition='FBI UCR estimated SRS homicide offenses',
             source_id_corrections=[{'year': 2022, 'state_name': 'North Dakota', 'source_abbreviation': 'NC', 'unit': 'ND'}],
             limited_data_states_2025=['FL', 'ND'], license_status='federal_source_explicit_license_unverified'),
        'State population; FBI UCR estimated offenses', UCR_URL,
        alternative_homicide_source=True)
    return specs
