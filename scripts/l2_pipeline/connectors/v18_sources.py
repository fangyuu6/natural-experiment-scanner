"""Catalog V18 source parsers: public state-year series from files already on disk or downloaded 2026-10-20.

Every parser reads local bytes only (standard library; no network, no execution of anything inside the files) and
returns plain ``Series`` records: state-year values, per-cell flags, units, coverage and the source files used.
Nothing here joins a policy, treatment or outcome association. Definition breaks are separate series or flagged
cells, never silently spliced.
"""
from dataclasses import dataclass, field
import csv
import html
import io
import json
import math
from pathlib import Path
import re
import zipfile

from . import v18_xls, v18_xlsx
from .common import ConnectorError, NAMES, STATES

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / 'data/l2_pipeline/connectors_v2'
UNITS = tuple(sorted(STATES.values()))
NAME_TO_UNIT = {name.lower(): unit for name, unit in NAMES.items()}
FIPS_TO_UNIT = {int(k): v for k, v in STATES.items()}
SEDS = ROOT / 'eia_state_electricity/raw/SEDS.zip'
PUBLIC = 'US federal government public data'


@dataclass
class Series:
    sid: str
    years: list
    values: dict            # (unit, year) -> float
    units: str
    population: str
    url: str
    paths: list             # source files (repo-relative or absolute under data/)
    construct: str
    ages: tuple = (0, 120)
    flags: dict = field(default_factory=dict)   # (unit, year) -> support/metadata flags
    limitations: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    filters: dict = field(default_factory=dict)
    population_unit: str = 'person'
    aliases: list = field(default_factory=list)


def number(text):
    if text is None:
        return None
    text = str(text).strip().replace(',', '')
    if text in ('', '.', 'NA', 'N/A', 'NR', 'NReq', '--', '---', 'X', 'D', '(D)', '(NA)', '(X)', '(NM)', '*', '-', '‡', 'S'):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def unit_of_name(text):
    """State name (footnote markers and spacing removed) -> USPS code, or None for non-states."""
    clean = re.sub(r'\\\d+\\', '', str(text)).replace('\n', ' ')
    clean = re.sub(r'\s+', ' ', clean).strip().rstrip('*').strip().lower()
    return NAME_TO_UNIT.get(clean)


def _put(values, flags, key, value, flag=None):
    if key in values or key in flags:
        raise ConnectorError('Duplicate source cell %s' % (key,))
    if value is not None:
        values[key] = value
    if flag:
        flags[key] = flag


# ---------------------------------------------------------------- population denominators
def seds_population():
    """Resident population (persons), SEDS TPOPP annual, states and DC: the denominator C59 already uses."""
    out = {}
    with zipfile.ZipFile(SEDS) as z, z.open('SEDS.txt') as stream:
        for line in stream:
            record = json.loads(line)
            parts = record.get('series_id', '').split('.')
            if len(parts) != 4 or parts[1] != 'TPOPP' or parts[2] not in UNITS or parts[3] != 'A':
                continue
            if record['units'] != 'Thousand':
                raise ConnectorError('SEDS population units changed')
            for year, v in record['data']:
                n = number(v)
                if n is not None and n > 0:
                    out[parts[2], int(year)] = 1000 * n
    return out


# ---------------------------------------------------------------- NIAAA apparent alcohol consumption
NIAAA = ROOT / 'niaaa_ethanol/raw/pcyr1970-2024_final.txt'
NIAAA_URL = 'https://www.niaaa.nih.gov/sites/default/files/pcyr1970-2024_final.txt'
NIAAA_BEVERAGES = {4: 'all_beverages', 3: 'beer', 2: 'wine', 1: 'spirits'}


def niaaa():
    cells = {}
    for line in NIAAA.read_text(encoding='latin1').splitlines():
        parts = line.split()
        if len(parts) != 14 or not re.fullmatch(r'(19|20)\d\d', parts[0]) or not parts[1].isdigit():
            continue
        fips, bev = int(parts[1]), int(parts[2])
        if fips not in FIPS_TO_UNIT or bev not in NIAAA_BEVERAGES:
            continue
        key = FIPS_TO_UNIT[fips], int(parts[0]), bev
        if key in cells:
            raise ConnectorError('Duplicate NIAAA cell')
        cells[key] = parts
    if not cells:
        raise ConnectorError('NIAAA file layout changed')
    out = []
    years = sorted({k[1] for k in cells})
    for bev, label in NIAAA_BEVERAGES.items():
        for age, col, name in ((14, 6, '14plus'), (21, 9, '21plus')):
            if age == 21 and bev != 4:
                continue
            values, flags = {}, {}
            for (u, y, b), parts in cells.items():
                if b != bev:
                    continue
                v = number(parts[col])
                if v is None:
                    flags[u, y] = dict(missing_reason='published_cell_absent')
                    continue
                values[u, y] = v / 10000.0
                if y >= 2020:
                    flags[u, y] = dict(source_issue='2020_2023_population_estimates_revised_in_2026_file')
            sid = 'niaaa_apparent_ethanol_gallons_per_capita_age%s_%s' % (name, label)
            construct = {'all_beverages': 'alcohol_consumption', 'beer': 'beer_consumption', 'wine': 'wine_consumption',
                         'spirits': 'spirits_consumption'}[label] + ('_age21plus' if age == 21 else '')
            out.append(Series(sid, years, values, 'gallons of ethanol per capita',
                'Resident population age %d and older; apparent consumption from state sales, taxation or receipt data' % age,
                NIAAA_URL, [NIAAA], construct, ages=(age, 120), flags=flags,
                filters=dict(age_min=age, beverage=label), population_unit='person', aliases=[construct, construct + '_per_capita'],
                limitations=['Apparent consumption (sales, shipments or tax receipts divided by population), not self-reported drinking; cross-border purchases are not adjusted.',
                             'Per-capita gallons are the published column divided by 10,000 as the file layout specifies. 2020-2024 population denominators were revised in the 2026 file (flagged in source_issue, not excluded).'],
                details=dict(file_columns='gallons ethanol per capita, population age %d and older' % age, beverage_code=bev)))
    return out


# ---------------------------------------------------------------- Census BDS
BDS = ROOT / 'census_bds/raw/bds2023_st.csv'
BDS_URL = 'https://www2.census.gov/programs-surveys/bds/tables/time-series/2023/bds2023_st.csv'
BDS_FIELDS = {
    'estabs_entry_rate': ('establishment_entry', 'Establishment entry (birth) rate'),
    'estabs_exit_rate': ('establishment_exit', 'Establishment exit (death) rate'),
    'job_creation_rate': ('job_creation', 'Job creation rate'),
    'job_creation_rate_births': ('job_creation_births', 'Job creation rate from establishment births'),
    'job_destruction_rate': ('job_destruction', 'Job destruction rate'),
    'net_job_creation_rate': ('net_job_creation', 'Net job creation rate'),
    'reallocation_rate': ('job_reallocation', 'Excess job reallocation rate'),
}


def bds():
    with BDS.open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    out = []
    years = sorted({int(r['year']) for r in rows})
    for column, (construct, label) in BDS_FIELDS.items():
        values, flags = {}, {}
        for r in rows:
            unit = FIPS_TO_UNIT.get(int(r['st']))
            if unit is None:
                continue
            key = unit, int(r['year'])
            v = number(r[column])
            if v is None:
                flags[key] = dict(suppressed=r[column].strip() == 'X', missing_reason='disclosure_withheld_X' if r[column].strip() == 'X' else 'published_cell_absent')
                if key in values:
                    raise ConnectorError('Duplicate BDS cell')
                continue
            if key in values:
                raise ConnectorError('Duplicate BDS cell')
            values[key] = v
        out.append(Series('census_bds_%s' % column, years, values, 'percent',
            'Private nonfarm employer establishments and jobs in the state; DHS-denominator rates, year t compared with t-1 (March 12 reference)',
            BDS_URL, [BDS], 'business_dynamics_' + construct, filters=dict(measure=column), flags=flags,
            limitations=['Rates use the Davis-Haltiwanger-Schuh denominator, the mean of employment at t and t-1; ' + label + '.',
                         'Employer establishments only (no nonemployers, no farms or private households); the 2023 release revises earlier years.',
                         'A multi-state firm is counted in each state where it operates (firm counts not used here).'],
            details=dict(source_column=column, label=label, release='BDS 2023')))
    return out


# ---------------------------------------------------------------- CDC SchoolVaxView
SVV = ROOT / 'cdc_schoolvaxview/raw/schoolvaxview_kindergarten_coverage_exemptions.csv'
SVV_URL = 'https://data.cdc.gov/resource/ijqb-a7ye.csv'
SVV_SERIES = {
    # sid: (vaccine, dose, construct)
    'cdc_svv_kindergarten_mmr_coverage_pct': ('MMR', '', 'mmr_vaccination_coverage'),
    'cdc_svv_kindergarten_dtap_coverage_pct': ('DTP, DTaP, or DT', '', 'dtap_vaccination_coverage'),
    'cdc_svv_kindergarten_polio_coverage_pct': ('Polio', '', 'polio_vaccination_coverage'),
    'cdc_svv_kindergarten_hepb_coverage_pct': ('Hepatitis B', '', 'hepatitis_b_vaccination_coverage'),
    'cdc_svv_kindergarten_varicella_1dose_coverage_pct': ('Varicella', '1 Dose (or disease history)', 'varicella_1dose_vaccination_coverage'),
    'cdc_svv_kindergarten_varicella_2dose_coverage_pct': ('Varicella', '2 Doses (or disease history)', 'varicella_2dose_vaccination_coverage'),
    'cdc_svv_kindergarten_any_exemption_pct': ('Exemption', 'Any Exemption', 'school_vaccine_exemption'),
    'cdc_svv_kindergarten_nonmedical_exemption_pct': ('Exemption', 'Non-Medical Exemption', 'school_vaccine_nonmedical_exemption'),
    'cdc_svv_kindergarten_medical_exemption_pct': ('Exemption', 'Medical Exemption', 'school_vaccine_medical_exemption'),
}


def school_year_end(label):
    match = re.fullmatch(r'(\d{4})-(\d{2})', label.strip())
    if not match:
        raise ConnectorError('Unexpected school-year label: ' + label)
    start = int(match.group(1))
    if int(match.group(2)) != (start + 1) % 100:
        raise ConnectorError('Malformed school-year label: ' + label)
    return start + 1


def svv():
    with SVV.open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    out = []
    years = sorted({school_year_end(r['year_season']) for r in rows if r['geography_type'] == 'States'})
    for sid, (vaccine, dose, construct) in SVV_SERIES.items():
        values, flags = {}, {}
        for r in rows:
            if r['geography_type'] != 'States' or r['vaccine'] != vaccine or r['dose'] != dose:
                continue
            unit = NAME_TO_UNIT.get(r['geography'].strip().lower())
            if unit is None:
                continue
            key = unit, school_year_end(r['year_season'])
            if key in values or key in flags:
                raise ConnectorError('Duplicate SchoolVaxView cell')
            v = number(r['coverage_estimate'])
            survey = r['survey_type'].strip()
            surveyed = number(r['percent_surveyed'])
            flag = dict(sampling_error=survey != 'Census')
            if survey == 'Voluntary response':
                flag['unreliable'] = True
                flag['source_issue'] = 'voluntary_response_survey'
            elif surveyed is not None and surveyed < 90:
                flag['source_issue'] = 'percent_surveyed_below_90'
            if v is None:
                flag['missing_reason'] = 'CDC_' + (r['coverage_estimate'].strip() or 'blank')
                flag['published_cell_present'] = False
            elif not 0 <= v <= 100:
                raise ConnectorError('SchoolVaxView value outside 0..100')
            else:
                values[key] = v
            flags[key] = flag
        out.append(Series(sid, years, values, 'percent',
            'Kindergarten students in public and private schools, state of the school; ' + (dose or vaccine + ' coverage'),
            SVV_URL, [SVV], construct,
            ages=(4, 7), flags=flags, filters=dict(vaccine=vaccine, dose=dose),
            limitations=['Year is the calendar year in which the school year ends (2009-10 is 2010).',
                         'Census, sample and voluntary-response surveys are mixed by the publisher; each cell carries its survey flag. Cells the CDC marks NA, NR or NReq stay missing.',
                         'Varicella and hepatitis B requirements and the CDC dose definitions change over time; the dose label is part of the series identity.'],
            details=dict(vaccine=vaccine, dose=dose)))
    return out


# ---------------------------------------------------------------- NCES graduation rates
ACGR = ROOT / 'nces_graduation/raw/digest_d23_table219.46_acgr_2012_2022.xlsx'
AFGR = ROOT / 'nces_graduation/raw/digest_d15_table219.35_afgr_1990_2013.html'
ACGR_URL = 'https://nces.ed.gov/programs/digest/d23/tables/dt23_219.46.asp'
AFGR_URL = 'https://nces.ed.gov/programs/digest/d15/tables/dt15_219.35.asp'


def _year_header(text):
    match = re.fullmatch(r'\s*(\d{4})\s*-\s*(\d{2}|\d{4})\s*', str(text).replace('\n', ' '))
    if not match:
        match = re.fullmatch(r'\s*(\d{4})-\s*(\d{2})\s*', str(text).replace('\n', ' '))
    if not match:
        return None
    start = int(match.group(1))
    end = match.group(2)
    if (int(end) % 100) != (start + 1) % 100:
        return None
    return start + 1


def acgr():
    sheet = list(v18_xlsx.read_xlsx(ACGR).values())[0]
    columns = {}
    for (r, c), value in sheet.items():
        if r in (3, 5) and isinstance(value, str):
            year = _year_header(value)
            if year:
                if year in columns:
                    raise ConnectorError('Duplicate ACGR year column')
                columns[year] = c
    if sorted(columns) != list(range(2012, 2023)):
        raise ConnectorError('ACGR year columns changed: %s' % sorted(columns))
    values, flags = {}, {}
    seen = set()
    for r in sorted({r for r, c in sheet if r >= 8}):
        name = sheet.get((r, 1))
        unit = unit_of_name(name) if isinstance(name, str) else None
        if unit is None:
            continue
        if unit in seen:
            raise ConnectorError('Duplicate ACGR state row')
        seen.add(unit)
        for year, c in columns.items():
            raw = sheet.get((r, c))
            key = unit, year
            if isinstance(raw, float):
                values[key] = raw
                flag = {}
                if year == 2012:
                    flag['series_break'] = 'afgr_to_acgr_cohort_definition_2012'
                    flag['source_issue'] = '2011_12_ACGR_published_rounded_to_whole_percent'
                if flag:
                    flags[key] = flag
            else:
                text = str(raw).strip() if raw is not None else ''
                reason = {'---': 'not_available', '‡': 'reporting_standards_not_met'}.get(text, 'topcoded_or_range:' + text if text else 'published_cell_absent')
                flags[key] = dict(missing_reason=reason, suppressed=text.startswith('>') or text.startswith('<'),
                                  series_break='afgr_to_acgr_cohort_definition_2012' if year == 2012 else '')
    if len(seen) != 51:
        raise ConnectorError('ACGR requires 50 states and DC; found %d' % len(seen))
    return Series('nces_acgr_public_hs_4yr_graduation_rate', sorted(columns), values, 'percent',
        'Public high school students in the 9th-grade entering cohort, adjusted for transfers, four-year ACGR, state',
        ACGR_URL, [ACGR], 'high_school_graduation', ages=(14, 19), flags=flags,
        limitations=['Adjusted cohort graduation rate (ACGR), defined from 2010-11; not comparable with the averaged freshman graduation rate (AFGR) series, which ends in 2013.',
                     'Year is the spring in which the cohort graduates (2011-12 is 2012). The 2011-12 state values are published rounded to whole percents.',
                     'States changed diploma and cohort rules during the window; the NCES footnotes (imputed Idaho, Kentucky, Illinois and New Mexico values) are not applied.'],
        details=dict(table='Digest of Education Statistics 2023 Table 219.46', cohort_definition='ACGR'))


def afgr():
    text = AFGR.read_text(encoding='latin1')
    table_rows = re.findall(r'<tr.*?</tr>', text, re.S | re.I)
    header = None
    parsed = []
    for tr in table_rows:
        cells = [re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', c))).strip()
                 for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S | re.I)]
        if cells and cells[0] == 'State or jurisdiction' and len(cells) >= 16:
            header = cells[1:]
        elif header and cells and cells[0]:
            parsed.append(cells)
    if header is None:
        raise ConnectorError('AFGR header not found')
    years = []
    for label in header:
        match = re.fullmatch(r'(\d{4})-(\d{2}|\d{4})', label)
        if not match:
            raise ConnectorError('AFGR header label changed: ' + label)
        start = int(match.group(1))
        years.append(start + 1)
    values, flags, seen = {}, {}, set()
    for cells in parsed:
        unit = unit_of_name(cells[0])
        if unit is None:
            continue
        if unit in seen:
            raise ConnectorError('Duplicate AFGR state row')
        seen.add(unit)
        # Footnote markers are bare integers placed between value cells; values always carry a decimal point.
        data = [c for c in cells[1:] if c != '' and (re.fullmatch(r'\d+\.\d+', c) or c in ('‡', '---', '—', 'N/A'))]
        if len(data) != len(years):
            raise ConnectorError('AFGR row has %d values for %d years: %s' % (len(data), len(years), unit))
        for year, cell in zip(years, data):
            v = number(cell)
            if v is None:
                flags[unit, year] = dict(missing_reason='published_cell_absent')
            else:
                values[unit, year] = v
    if len(seen) != 51:
        raise ConnectorError('AFGR requires 50 states and DC; found %d' % len(seen))
    return Series('nces_afgr_public_hs_graduation_rate', years, values, 'percent',
        'Public high school diplomas awarded in the spring divided by an average of 8th-, 9th- and 10th-grade enrollments in earlier years, state',
        AFGR_URL, [AFGR], 'high_school_graduation_afgr', ages=(14, 19), flags=flags,
        limitations=['Averaged freshman graduation rate (AFGR), an enrollment-based estimate; not the cohort-based ACGR, which starts in 2012. The two are separate series.',
                     'The publisher lists selected years only (1991, 1996, 2000, 2001 and every year 2003-2013); all other years are absent, never interpolated.',
                     'Year is the spring in which the diplomas are awarded (1990-91 is 1991).'],
        details=dict(table='Digest of Education Statistics 2015 Table 219.35', cohort_definition='AFGR', published_years=years))


# ---------------------------------------------------------------- Census state finance, 1996-2011 historical files
ASFIN = ROOT / 'census_asfin/raw'
ASFIN_URL = 'https://www2.census.gov/programs-surveys/state/datasets/'
# Census government IDs are alphabetical by name 01..51 with DC at 09; the 14-digit ID starts with the state code (00 = US total).
# The state files carry no DC lines (DC has no separate state government), so DC cells stay missing.
ASFIN_STATE_CODES = {i + 1: NAME_TO_UNIT[n] for i, n in enumerate(sorted(n.lower() for n in NAMES))}
ASFIN_FILES = {1996: '96data35.txt', 1997: '97data35.txt', 1998: '98data35.txt', 1999: '99state35.txt'}
ASFIN_FILES.update({y: '%02dstate35.txt' % (y % 100) for y in range(2000, 2012)})
# Direct state expenditure by function = current operations (E) + construction (F) + other capital outlay (G)
# + assistance and subsidies (J) on the same two-digit function; intergovernmental (M) and interest (I) are excluded.
# Higher education adds the pre-2005 E19 scholarships line to its 2005+ successor J19 so the aggregate is continuous.
ASFIN_FUNCTIONS = {
    'corrections': ('correction', ['E04', 'E05', 'F04', 'F05', 'G04', 'G05']),
    'police_protection': ('police_protection', ['E62', 'F62', 'G62']),
    'highways': ('highways', ['E44', 'E45', 'F44', 'F45', 'G44', 'G45']),
    'hospitals': ('hospitals', ['E36', 'F36', 'G36']),
    'higher_education': ('higher_education', ['E16', 'E18', 'E19', 'J19', 'F16', 'F18', 'G16', 'G18']),
    'unemployment_compensation_benefits': ('unemployment_insurance_benefits', ['Y05', 'Y06']),
    'workers_compensation_benefits': ('workers_compensation_benefits', ['Y14']),
    'lottery_ticket_sales': ('lottery_sales', ['Z51']),
    'total_salaries_and_wages': ('state_payroll', ['Z00']),
}


def asfin_hist_year(year):
    """One fiscal year of the 35-character state file. The 1999 file uses a different fixed layout (no survey/data
    year columns), so its fiscal year comes from the file name and its totals are checked against neighbours in tests."""
    path = ASFIN / ASFIN_FILES[year]
    items = {}
    for line in path.read_text(encoding='latin1').splitlines():
        if year == 1999:
            match = re.fullmatch(r'(\d{14}) {3}24 {2}(\w{3}) +(-?\d+) *', line)
            if not match:
                if line.strip():
                    raise ConnectorError('Unexpected 1999 state finance line layout')
                continue
            ident, code, amount = match.group(1), match.group(2), float(match.group(3))
        else:
            if len(line) < 33 or not line[:14].isdigit():
                continue
            ident, code, amount = line[:14], line[14:17], number(line[17:29])
            if line[31:33] != '%02d' % (year % 100):
                raise ConnectorError('State finance file %s holds fiscal year %s, expected %d' % (path.name, line[31:33], year))
        state_code, rest = int(ident[0:2]), ident[2:]
        if state_code not in ASFIN_STATE_CODES or rest != '0' * 12:
            continue
        key = ASFIN_STATE_CODES[state_code], code
        if key in items:
            raise ConnectorError('Duplicate state finance item ' + str(key))
        items[key] = amount
    return path, items


def asfin_hist():
    pop = seds_population()
    per_year = {y: asfin_hist_year(y) for y in ASFIN_FILES}
    out = []
    for name, (construct, codes) in ASFIN_FUNCTIONS.items():
        values, flags = {}, {}
        for year, (path, items) in per_year.items():
            for u in UNITS:
                parts = [items.get((u, c)) for c in codes]
                if all(p is None for p in parts):
                    flags[u, year] = dict(missing_reason='DC_not_in_state_files' if u == 'DC' else 'item_codes_absent')
                    continue
                total = sum(p for p in parts if p is not None)
                if (u, year) in pop:
                    values[u, year] = 1000.0 * total / pop[u, year]
                else:
                    flags[u, year] = dict(missing_reason='population_denominator_absent', denominator_available=False)
                if name == 'higher_education' and year in (2004, 2005):
                    flags.setdefault((u, year), {})['series_break'] = 'E19_to_J19_scholarships_2005' if year == 2005 else ''
        if not values:
            raise ConnectorError('No state finance cells for ' + name)
        out.append(Series('census_state_finance_hist_%s_per_capita' % name, sorted(per_year), values, 'USD per resident (nominal)',
            'State governments (not local); %s, fiscal year, divided by resident population' % name.replace('_', ' '),
            ASFIN_URL, [path for path, _ in per_year.values()] + [SEDS], 'state_finance_hist_' + construct,
            flags=flags, filters=dict(item_codes=codes), population_unit='state_fiscal_measure',
            limitations=['State-government direct expenditure (or trust-fund / lottery line) in nominal dollars, thousands of dollars times 1000 divided by SEDS resident population; no inflation adjustment.',
                         'Historical item-code files 1996-2011 are a different compilation from the labeled 2012-2024 ASFIN aggregates already in the catalog (census_asfin_*); the two are never spliced. District of Columbia is not in the state files.',
                         'Function totals add the listed item codes; Census changed several item definitions in 2005 (noted per series), so level breaks near 2005 are possible even though no code is dropped.'],
            details=dict(item_codes=codes, amount_units='thousands USD', time_basis='state fiscal year ending in label year',
                         population='SEDS TPOPP thousands x 1000', years_files={str(y): ASFIN_FILES[y] for y in ASFIN_FILES})))
    return out


# ---------------------------------------------------------------- BEA state personal income and real GDP
BEA = ROOT / 'bea_state_regional/raw'
BEA_URL = 'https://apps.bea.gov/regional/downloadzip.htm'
SAINC1 = 'SAINC1__ALL_AREAS_1929_2025.csv'
SAINC4 = 'SAINC4__ALL_AREAS_1929_2025.csv'
SAGDP9 = 'SAGDP9__ALL_AREAS_1997_2025.csv'
# (series id, zip, member, line code, units, construct, divide by population?, description)
BEA_LINES = [
    ('bea_per_capita_personal_income', 'SAINC.zip', SAINC1, '3', 'USD per resident (nominal)', 'per_capita_income', False,
     'Personal income divided by midyear population, as published by BEA (SAINC1 line 3)'),
    ('bea_wages_salaries_per_capita', 'SAINC.zip', SAINC4, '50', 'USD per resident (nominal)', 'wages_and_salaries', True,
     'Wages and salaries (SAINC4 line 50, place of work) per resident'),
    ('bea_proprietors_income_per_capita', 'SAINC.zip', SAINC4, '70', 'USD per resident (nominal)', 'proprietors_income', True,
     "Proprietors' income (SAINC4 line 70) per resident"),
    ('bea_personal_transfer_receipts_per_capita', 'SAINC.zip', SAINC4, '47', 'USD per resident (nominal)', 'personal_transfer_receipts', True,
     'Personal current transfer receipts (SAINC4 line 47) per resident'),
    ('bea_dividends_interest_rent_per_capita', 'SAINC.zip', SAINC4, '46', 'USD per resident (nominal)', 'property_income', True,
     'Dividends, interest and rent (SAINC4 line 46) per resident'),
    ('bea_real_gdp_per_capita', 'SAGDP.zip', SAGDP9, '1', 'chained 2017 USD per resident', 'real_gdp', True,
     'Real GDP, all industries (SAGDP9 line 1) per resident'),
    ('bea_real_gdp_manufacturing_per_capita', 'SAGDP.zip', SAGDP9, '12', 'chained 2017 USD per resident', 'real_gdp_manufacturing', True,
     'Real GDP of manufacturing (SAGDP9 line 12) per resident'),
    ('bea_real_gdp_construction_per_capita', 'SAGDP.zip', SAGDP9, '11', 'chained 2017 USD per resident', 'real_gdp_construction', True,
     'Real GDP of construction (SAGDP9 line 11) per resident'),
    ('bea_real_gdp_agriculture_per_capita', 'SAGDP.zip', SAGDP9, '3', 'chained 2017 USD per resident', 'real_gdp_agriculture', True,
     'Real GDP of agriculture, forestry, fishing and hunting (SAGDP9 line 3) per resident'),
    ('bea_real_gdp_mining_per_capita', 'SAGDP.zip', SAGDP9, '6', 'chained 2017 USD per resident', 'real_gdp_mining', True,
     'Real GDP of mining, quarrying and oil and gas extraction (SAGDP9 line 6) per resident'),
]


def _bea_table(zip_name, member):
    with zipfile.ZipFile(BEA / zip_name) as z:
        info = z.getinfo(member)
        if info.file_size > 100_000_000:
            raise ConnectorError('BEA member too large')
        text = z.read(member).decode('latin1')
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    year_cols = {int(h): i for i, h in enumerate(header) if re.fullmatch(r'\d{4}', h)}
    table = {}
    for r in reader:
        if len(r) < len(header) or not r[0].strip(' "').isdigit():
            continue
        fips = int(r[0].strip(' "')) // 1000
        if r[0].strip(' "')[-3:] != '000' or fips not in FIPS_TO_UNIT:
            continue
        key = FIPS_TO_UNIT[fips], r[4].strip()
        if key in table:
            raise ConnectorError('Duplicate BEA state/line row')
        table[key] = (r[6].strip(), r[7].strip(), [r[i] for i in sorted(year_cols.values())], sorted(year_cols))
    return table


def bea():
    out = []
    cache = {}
    population = {}
    pop_table = _bea_table('SAINC.zip', SAINC1)
    for (u, line), (_, _, vals, years) in pop_table.items():
        if line == '2':
            for y, v in zip(years, vals):
                n = number(v)
                if n:
                    population[u, y] = n
    for sid, zip_name, member, line, unit, construct, per_capita, description in BEA_LINES:
        table = cache.setdefault((zip_name, member), None) or _bea_table(zip_name, member)
        cache[zip_name, member] = table
        values, flags, years_seen = {}, {}, set()
        for u in UNITS:
            row = table.get((u, line))
            if row is None:
                raise ConnectorError('BEA line %s missing for %s' % (line, u))
            desc, native_unit, vals, years = row
            for y, v in zip(years, vals):
                years_seen.add(y)
                n = number(v)
                if n is None:
                    flags[u, y] = dict(missing_reason='BEA_' + (v.strip() or 'blank'), suppressed=v.strip() == '(D)')
                    continue
                if per_capita:
                    if (u, y) not in population:
                        flags[u, y] = dict(missing_reason='population_denominator_absent', denominator_available=False)
                        continue
                    n = n * 1_000_000 / population[u, y]
                values[u, y] = n
        paths = [BEA / zip_name]
        out.append(Series(sid, sorted(years_seen), values, unit,
            'Resident population of the state; ' + description, BEA_URL, paths, construct, filters=dict(table=member.split('__')[0], line_code=line), flags=flags,
            population_unit='state_measure',
            limitations=['BEA state estimates are revised each release; values are those in the September 2026 files. Personal income measures are nominal dollars (no deflator applied); real GDP is chained 2017 dollars and chained industry values do not add to totals.',
                         'Per-resident division uses BEA midyear population (SAINC1 line 2) from the same release. SAGDP covers 1997 onward only (the SIC-based 1963-1997 series is a different classification and is not spliced).'] if per_capita or sid.startswith('bea_real') else
                        ['BEA state estimates are revised each release; nominal dollars, no deflator applied.'],
            details=dict(source_member=member, line_code=line, description=description, native_unit=native_unit,
                         population_source='BEA SAINC1 line 2' if per_capita else 'published by BEA')))
    return out


# ---------------------------------------------------------------- FHFA house price index
FHFA = ROOT / 'fhfa_hpi_state/raw/hpi_at_state.csv'    # saved with a .csv name; the bytes are an .xlsx workbook
FHFA_URL = 'https://www.fhfa.gov/hpi/download/annual/hpi_at_state.csv'


def fhfa():
    sheet = list(v18_xlsx.read_xlsx(FHFA).values())[0]
    header_row = next(r for r in sorted({r for r, c in sheet}) if sheet.get((r, 1)) == 'State')
    head = {sheet[header_row, c]: c for c in range(1, 12) if (header_row, c) in sheet}
    if not {'Abbreviation', 'Year', 'Annual Change (%)', 'HPI'} <= set(head):
        raise ConnectorError('FHFA header changed')
    index, change, flags_i, flags_c, years = {}, {}, {}, {}, set()
    for r in sorted({r for r, c in sheet if r > header_row}):
        abbr = sheet.get((r, head['Abbreviation']))
        if abbr not in UNITS:
            continue
        year = int(sheet[r, head['Year']])
        years.add(year)
        key = abbr, year
        v, d = sheet.get((r, head['HPI'])), sheet.get((r, head['Annual Change (%)']))
        for table, flag, val in ((index, flags_i, v), (change, flags_c, d)):
            if key in table or key in flag:
                raise ConnectorError('Duplicate FHFA state-year')
            if isinstance(val, float):
                table[key] = val
            else:
                flag[key] = dict(missing_reason='published_cell_absent')
    common = dict(population='Single-family homes with conforming mortgages purchased or securitized by Fannie Mae or Freddie Mac, state (all-transactions index)',
                  url=FHFA_URL, paths=[FHFA], ages=(0, 120), population_unit='housing_measure',
                  limitations=['FHFA all-transactions house price index (purchase and appraisal data), annual; reflects only mortgages bought or guaranteed by the GSEs, so jumbo and cash purchases are absent.',
                               'Index levels are not comparable across states in level terms (all states are set to 100 in 1975); use within-state changes. Revised each quarter.',
                               'The file is an .xlsx workbook saved under a .csv name by the publisher.'])
    return [Series('fhfa_all_transactions_hpi_1975_base', sorted(years), index, 'index (1975=100)', construct='house_prices', flags=flags_i,
                   details=dict(source_column='HPI'), aliases=['house_prices', 'housing_price_index'], **common),
            Series('fhfa_all_transactions_hpi_annual_change_pct', sorted(years), change, 'percent', construct='house_price_growth', flags=flags_c,
                   details=dict(source_column='Annual Change (%)'), aliases=['house_price_growth'], **common)]


# ---------------------------------------------------------------- USDA FNS SNAP monthly state data, fiscal years 1989-2025
SNAP_ZIP = ROOT / 'fns_snap/raw/snap-zip-fy69tocurrent-9.zip'
SNAP_URL = 'https://www.fns.usda.gov/pd/supplemental-nutrition-assistance-program-snap'
MONTHS = ('Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep')


def _snap_member(zf, name):
    info = zf.getinfo(name)
    if info.file_size > 30_000_000:
        raise ConnectorError('SNAP member too large')
    import tempfile
    suffix = Path(name).suffix
    data = zf.read(name)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / ('book' + suffix)
        target.write_bytes(data)
        return v18_xlsx.read_xlsx(target) if suffix == '.xlsx' else v18_xls.read_xls(target)


def snap_year(zf, fy):
    name = 'FY%02d.xls' % (fy % 100)
    names = zf.namelist()
    if name not in names:
        name += 'x'
    sheets = _snap_member(zf, name)
    out = {}
    for sheet_name, grid in sheets.items():
        if sheet_name.strip() == 'US Summary':
            continue
        rows = sorted({r for r, c in grid})
        header_cost_col = 6
        for r in rows[:8]:
            if str(grid.get((r, 4), '')).strip() == 'Cost':
                header_cost_col = 4
        state = None
        months = []
        for r in rows:
            label = grid.get((r, 1))
            if not isinstance(label, str):
                continue
            text = label.strip()
            if (r, 2) not in grid and text and not text.startswith(('Fiscal', 'National', 'SNAP', 'Total', 'Household')):
                state = unit_of_name(text)
                months = []
                continue
            match = re.fullmatch(r'(Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep) (\d{4})( */\d+)?', text)
            if match and state:
                months.append((match.group(1), int(match.group(2)), bool(match.group(3)), grid.get((r, 2)), grid.get((r, 3)), grid.get((r, header_cost_col))))
            elif text == 'Total' and state:
                if state in out:
                    raise ConnectorError('Duplicate SNAP state sheet block %s FY%d' % (state, fy))
                out[state] = months
                state = None
    return name, out


def snap():
    pop = seds_population()
    persons, benefits, avg_benefit = {}, {}, {}
    flags_p, flags_b, flags_a = {}, {}, {}
    years = list(range(1989, 2026))
    with zipfile.ZipFile(SNAP_ZIP) as zf:
        for fy in years:
            _, per_state = snap_year(zf, fy)
            if not set(UNITS) <= set(per_state):
                raise ConnectorError('SNAP FY%d lacks states: %s' % (fy, sorted(set(UNITS) - set(per_state))))
            for u in UNITS:
                months = per_state[u]
                if len(months) != 12 or [m[0] for m in months] != list(MONTHS):
                    raise ConnectorError('SNAP FY%d %s does not hold twelve Oct-Sep months' % (fy, u))
                complete = [m for m in months if isinstance(m[3], float) and isinstance(m[4], float) and isinstance(m[5], float)]
                key = u, fy
                if len(complete) != 12:
                    reason = dict(missing_reason='fewer_than_twelve_months_published')
                    flags_p[key] = flags_b[key] = flags_a[key] = reason
                    continue
                total_cost = sum(m[5] for m in complete)
                usable = [m for m in complete if not m[2]]
                issue = {}
                if len(usable) != 12:
                    # Footnoted months (the January-February 2019 shutdown early issuance) distort monthly counts; the 12-month cost sum is unaffected.
                    issue = dict(source_issue='footnoted_months_excluded_from_participation_mean', unreliable=True)
                avg_persons = sum(m[4] for m in usable) / len(usable)
                if (u, fy) in pop:
                    persons[key] = 100.0 * avg_persons / pop[key]
                    benefits[key] = total_cost / pop[key]
                    if issue:
                        flags_p[key] = issue
                else:
                    flags_p[key] = flags_b[key] = dict(missing_reason='population_denominator_absent', denominator_available=False)
                if len(usable) == 12:
                    person_months = sum(m[4] for m in complete)
                    if person_months > 0:
                        avg_benefit[key] = total_cost / person_months
                else:
                    person_months = sum(m[4] for m in usable)
                    avg_benefit[key] = sum(m[5] for m in usable) / person_months
                    flags_a[key] = issue
    common = dict(population='Persons participating in the Supplemental Nutrition Assistance Program (SNAP) in the state, fiscal year Oct-Sep',
                  url=SNAP_URL, paths=[SNAP_ZIP, SEDS], construct='snap_participation', ages=(0, 120), population_unit='program_measure')
    caveat = ['Federal fiscal year ending September of the label year (FY2000 is Oct 1999-Sep 2000); population is the SEDS July 1 estimate for the label year.',
              'January-February 2019 shutdown early issuance makes those two FY2019 monthly counts unusable; FY2019 participation uses the other ten months and is flagged unreliable. FY2026 is incomplete and not included.',
              'State SNAP files list the program name at the time (Food Stamp Program before October 2008); the Disaster SNAP, D-SNAP, benefits are included in the monthly cost where the state reports them.']
    return [Series('fns_snap_participation_pct_of_population', years, persons, 'percent of resident population',
                   details=dict(numerator='mean of twelve monthly participating persons', denominator='SEDS TPOPP'), flags=flags_p,
                   limitations=caveat, aliases=['snap_participation', 'snap_participation_rate'], **common),
            Series('fns_snap_benefits_per_capita', years, benefits, 'USD per resident (nominal)',
                   details=dict(numerator='sum of twelve monthly SNAP benefit costs', denominator='SEDS TPOPP'), flags=flags_b,
                   limitations=caveat, aliases=['snap_benefits'], **dict(common, construct='snap_benefits')),
            Series('fns_snap_average_monthly_benefit_per_person', years, avg_benefit, 'USD per participant per month (nominal)',
                   details=dict(numerator='sum of monthly benefit cost', denominator='sum of monthly participating persons'), flags=flags_a,
                   limitations=caveat, aliases=['snap_average_benefit'], **dict(common, construct='snap_average_benefit', paths=[SNAP_ZIP]))]


# ---------------------------------------------------------------- BLS QCEW industry slices already on disk
QCEW = ROOT / 'bls_qcew_industry/raw'
QCEW_URL = 'https://www.bls.gov/cew/downloadable-data-files.htm'
QCEW_SLICES = [
    # sid, naics, field, units, construct, population text
    ('bls_qcew_private_agriculture_employment', '11', 'annual_avg_emplvl', 'jobs', 'agriculture_employment',
     'Private-ownership employment in NAICS sector 11 (agriculture, forestry, fishing and hunting), state, annual average of covered employment'),
    ('bls_qcew_private_agriculture_avg_weekly_wage', '11', 'annual_avg_wkly_wage', 'USD per week (nominal)', 'agriculture_wages',
     'Private-ownership average weekly wage in NAICS sector 11, state, covered employment'),
    ('bls_qcew_private_child_day_care_avg_weekly_wage', '6244', 'annual_avg_wkly_wage', 'USD per week (nominal)', 'child_care_wages',
     'Private-ownership average weekly wage in NAICS 6244 (child day care services), state, covered employment'),
    ('bls_qcew_private_child_day_care_establishments', '6244', 'annual_avg_estabs_count', 'establishments (annual average)', 'child_care_establishments',
     'Private-ownership establishments in NAICS 6244 (child day care services), state, annual average'),
]


def qcew_slices():
    out = []
    for sid, naics, field_name, unit, construct, population in QCEW_SLICES:
        values, flags, paths, years = {}, {}, [], []
        for path in sorted(QCEW.glob('*_%s.csv' % naics)):
            year = int(path.name.split('_')[0])
            paths.append(path)
            years.append(year)
            with path.open(newline='', encoding='utf-8') as stream:
                for r in csv.DictReader(stream):
                    # State total, private ownership (own_code 5), sector/industry level 54 (NAICS sector) or 55-56 (NAICS 3-4 digit +): the state row only.
                    if r['own_code'] != '5' or r['industry_code'] != naics or not r['area_fips'].endswith('000') or r['area_fips'][:2] not in STATES or r['size_code'] != '0':
                        continue
                    if r['agglvl_code'] not in ('54', '55', '56', '57', '58'):
                        continue
                    unit_code = STATES[r['area_fips'][:2]]
                    key = unit_code, int(r['year'])
                    if key in values or key in flags:
                        raise ConnectorError('Duplicate QCEW state cell')
                    v = number(r[field_name] if field_name in r else r[field_name.replace('_count', '')])
                    if r['disclosure_code'].strip() == 'N' or v is None:
                        flags[key] = dict(suppressed=True, missing_reason='BLS_disclosure_N', suppression_reason='disclosure')
                    else:
                        values[key] = v
        if not paths:
            raise ConnectorError('No QCEW slices for NAICS ' + naics)
        out.append(Series(sid, sorted(years), values, unit, population, QCEW_URL, paths, construct,
            filters=dict(ownership='private', industry=naics), population_unit='state_measure', aliases=[construct], flags=flags,
            limitations=['BLS QCEW annual state totals for the private ownership code; cells BLS withholds for confidentiality (disclosure code N) stay missing.',
                         'Covered employment under state unemployment insurance laws only (farm operations below the coverage threshold and the self-employed are outside); nominal dollars.',
                         'NAICS vintage changes (2002, 2007, 2012, 2017, 2022) can move establishments between industries; QCEW reclassification is retained, not repaired.']))
    return out


# BEA (bea) and FHFA (fhfa) parsers are written and tested but NOT built into V18: hub ruling 2026-10-20 (panel 4-1) defers them
# until a named policy design needs them. Files, manifests and receipts stay on disk.
BUILDERS = (niaaa, bds, svv, lambda: [acgr()], lambda: [afgr()], asfin_hist, snap, qcew_slices)
DEFERRED_BUILDERS = (bea, fhfa)


def all_series():
    out = []
    for builder in BUILDERS:
        out.extend(builder())
    ids = [s.sid for s in out]
    if len(ids) != len(set(ids)):
        raise ConnectorError('Duplicate V18 series id')
    return out
