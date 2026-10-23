"""Public, outcome-only first-order adapters; no treatment or association inputs.

Official source downloads are explicit and separate in first_order_downloads.
Absent/suppressed cells remain missing. No longitudinal interpolation is used.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, NAMES, STATES, exact_filters
from .public_annual import _csv_rows, _licensed, acs1_state
from .published import _finalize, _numeric

FIRST_ORDER_MEASURES = {
    'acs1_health_insurance': {'health_insurance_coverage_pct'},
    'brfss_state_prevalence': {'colorectal_screening_pct', 'mammography_pct',
        'cervical_screening_pct', 'current_smoking_pct', 'binge_drinking_pct',
        'obesity_pct', 'health_insurance_coverage_pct', 'drinking_driving_pct'},
    'ipeds_completions': {'bachelors_completions_count'},
    'ipeds_enrollment': {'undergraduate_enrollment_count'},
    'nchs_mortality': {'drug_overdose_deaths_count', 'drug_overdose_age_adjusted_rate',
        'mortality_deaths_count', 'mortality_age_adjusted_rate'},
    'fbi_ucr_state': {'violent_crime_count', 'homicide_count', 'rape_count', 'robbery_count',
        'aggravated_assault_count', 'property_crime_count', 'burglary_count', 'larceny_count',
        'motor_vehicle_theft_count'},
}
NCHS_SUPPORTED_CAUSES = frozenset({'All causes', "Alzheimer's disease", 'CLRD',
    'Cancer', 'Diabetes', 'Heart disease', 'Influenza and pneumonia',
    'Kidney disease', 'Stroke', 'Suicide', 'Unintentional injuries'})


def _measure(request, connector):
    measure = request.options.get('measure_id')
    if measure not in FIRST_ORDER_MEASURES[connector]:
        raise ConnectorNotReady('Unsupported first-order measure for ' + connector)
    return measure


def _put(values, key, value, path, **extra):
    if key in values:
        raise ConnectorError('Duplicate published state/year cell: ' + repr(key))
    values[key] = dict(value=value, source=str(path), **extra)


def _number(value, *, maximum=None):
    if value is None or str(value).strip().lower() in ('', 'null', 'na', 'n/a',
            'suppressed', 'unreliable', '*', '**', '---', '—'):
        return ''
    try:
        number = float(str(value).replace(',', ''))
    except ValueError as exc:
        raise ConnectorError('Invalid published number') from exc
    if number < 0:  # Federal sentinel/suppression codes never become outcomes.
        return ''
    return _numeric(str(number), 0, maximum)


def acs1_health_insurance(request: ConnectorRequest):
    _measure(request, 'acs1_health_insurance')
    age = request.filters.get('age_group', '19-25')
    sex = request.filters.get('sex', 'all')
    exact_filters(request, {'age_group': age, 'sex': sex})
    bins = {'under6': 3, '6-18': 6, '19-25': 9, '26-34': 12,
            '35-44': 15, '45-54': 18, '55-64': 21, '65-74': 24, '75+': 27}
    if age not in bins or sex not in ('all', 'male', 'female'):
        raise ConnectorNotReady('ACS B27001 age bin/sex unavailable; do not approximate silently')
    offsets = [0, 28] if sex == 'all' else [0 if sex == 'male' else 28]
    denominator = [f'B27001_{bins[age]+offset:03d}E' for offset in offsets]
    numerator = [f'B27001_{bins[age]+offset+1:03d}E' for offset in offsets]
    options = dict(request.options, numerator_variables=numerator,
        denominator_variables=denominator, scale=100, outcome_unit='percent insured',
        source_population_filters=dict(request.filters))
    options.pop('variables', None)
    panel = acs1_state(replace(request, options=options))
    if any(r['outcome_value'] != '' and not 0 <= float(r['outcome_value']) <= 100 for r in panel.rows):
        raise ConnectorError('ACS insurance covered numerator exceeds population denominator')
    return replace(panel, limitations=panel.limitations + (
        'B27001 covers the civilian noninstitutionalized population; age 19–25 excludes age 18.',),
        details=dict(panel.details, measure_id='health_insurance_coverage_pct',
            numerator_variables=numerator, denominator_variables=denominator))


# Labels are checked as well as caller-selected published IDs, to prevent
# a generic prevalence connector from relabeling an unrelated question.
_BRFSS_LABEL = {
    'colorectal_screening_pct': r'colorectal|colonoscop|sigmoidoscop|blood stool|blood.*stool|USPSTF recommendation',
    'mammography_pct': r'mammogram|mammograph',
    'cervical_screening_pct': r'pap test|cervical',
    'current_smoking_pct': r'current smok|smoke cigarettes|smoking status',
    'binge_drinking_pct': r'binge drink',
    'obesity_pct': r'obes|bmi categor|body mass index',
    'drinking_driving_pct': r'driven after drinking|drinking.*driv',
    'health_insurance_coverage_pct': r'health care coverage|health.*insurance',
}
_BRFSS_POSITIVE = {
    'colorectal_screening_pct': {'_COL10YR':{'RESP207'},'_FS5YR':{'RESP211'},
        '_RFSIGM2':{'RESP046'},'_CRCREC':{'RESP192'},'_CRCREC1':{'RESP227'},
        '_CRCREC2':{'RESP227'},'_CRCREC3':{'RESP227'},'_RFBLDS2':{'RESP046'},
        '_RFBLDS3':{'RESP190'},'_RFBLDS4':{'RESP190'},'_RFBLDS5':{'RESP190'},'_RFBLDS6':{'RESP190'}},
    'mammography_pct': {k:{'RESP186'} for k in ('_MAM402Y','_MAM5021','_MAM5022','_MAM5023')},
    'cervical_screening_pct': {'_RFPAP32':{'RESP046'},'_RFPAP33':{'RESP188'},
        '_RFPAP34':{'RESP188'},'_RFPAP35':{'RESP188'}},
    'current_smoking_pct': {'_RFSMOK3':{'RESP046'}},
    'binge_drinking_pct': {'_RFBING5':{'RESP046'},'_RFBING6':{'RESP046'}},
    'obesity_pct': {'_BMI5CAT':{'RESP039'}},
    'drinking_driving_pct': {'_DRNKDRV':{'RESP201'}},
    'health_insurance_coverage_pct': {'HLTHPLN1':{'RESP046'},'_HCVU651':{'RESP046'},
        '_HCVU652':{'RESP233'},'_HCVU653':{'RESP233'},'_HCVU654':{'RESP233'}},
}
_BRFSS_POSITIVE['mammography_pct'].update({k:{'RESP046'} for k in
    ('_MAM502Y','_RFMAM21','_RFMAM22','_RFMAM23','_RFMAM2Y')})


def brfss_state_prevalence(request: ConnectorRequest):
    measure = _measure(request, 'brfss_state_prevalence')
    required = {'questionid', 'responseid', 'breakoutid', 'data_value_type'}
    if set(request.filters) != required or any(not request.filters[k] for k in required):
        raise ConnectorNotReady('BRFSS requires exact published questionid/responseid/breakoutid/data_value_type')
    if request.filters['responseid'] not in _BRFSS_POSITIVE[measure].get(request.filters['questionid'],set()):
        raise ConnectorError('BRFSS published question/positive-response IDs do not bind declared measure')
    if min(request.years) < 2011:
        raise ConnectorNotReady('Post-2011 BRFSS design must not be spliced to pre-2011 estimates')
    request = _licensed(request, 'CDC BRFSS')
    paths, values = request.paths(), {}
    for path in paths:
        for source in _csv_rows(path):
            if any(source.get(k) != str(v) for k, v in request.filters.items()):
                continue
            unit, year = source['locationabbr'], int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            if not re.search(_BRFSS_LABEL[measure], source.get('question', ''), re.I):
                raise ConnectorError('BRFSS published question does not measure declared outcome')
            # A registered response label binds the positive/negative orientation.
            expected_response = request.options.get('response_label')
            if not expected_response or source.get('response', '').casefold() != expected_response.casefold():
                raise ConnectorError('BRFSS response_label must bind the published response orientation')
            if source.get('data_value_unit') != '%':
                raise ConnectorError('BRFSS prevalence must use published percent units')
            suppressed = bool(source.get('data_value_footnote_symbol'))
            value = '' if suppressed else _number(source.get('data_value'), maximum=100)
            _put(values, (unit, year), value, path, counts={
                'suppression_flag': int(suppressed), 'sample_size': source.get('sample_size', ''),
                'confidence_limit_low': source.get('confidence_limit_low', ''),
                'confidence_limit_high': source.get('confidence_limit_high', '')})
    return _finalize(request, values, paths, 'percent', (
        'Published BRFSS weighted prevalence, not raw respondent means; rotating questions remain missing between waves.',
        'Question, denominator age eligibility and response wording must be reviewed across vintages; 2011 method break is excluded.'))


def _ipeds_year(path, request):
    year = request.options.get('source_years', {}).get(str(path.relative_to(request.base_dir)))
    match = re.search(r'(?:hd|c|ef)(\d{4})', path.name, re.I)
    if year is None and not match:
        raise ConnectorError('IPEDS filename/source_years must bind reporting year')
    return int(year or match.group(1))


def _ipeds_rows(path, request):
    if path.suffix.lower() != '.zip':
        yield from _csv_rows(path)
        return
    # HD is the annual geography/header component and has one original member;
    # outcome-file vintage selection applies only to C/EF releases.
    vintage = 'original' if path.name.lower().startswith('hd') else request.options.get('ipeds_vintage', 'original')
    if vintage not in ('original', 'revised'):
        raise ConnectorError('IPEDS ipeds_vintage must bind original/revised')
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith('.csv')
            and ('_rv.' in n.lower()) == (vintage == 'revised')]
        if len(members) != 1:
            raise ConnectorNotReady('IPEDS requested vintage lacks exactly one CSV member')
        with archive.open(members[0]) as raw, io.TextIOWrapper(raw,encoding='utf-8-sig',newline='') as handle:
            for record in csv.DictReader(handle):
                yield {str(k).strip(): str(v).strip() for k,v in record.items()}


def _ipeds(request, completion):
    name = 'ipeds_completions' if completion else 'ipeds_enrollment'
    _measure(request, name)
    required = {'sex': 'all', 'institution_control': 'public'}
    if completion:
        required.update(award_level=5, cipcode='99', major_number=1)
    else:
        required.update(level='undergraduate')
    if dict(request.filters) != required:
        raise ConnectorNotReady('IPEDS published population requires ' + repr(required))
    request = _licensed(request, 'NCES IPEDS')
    paths = request.paths()
    hd_paths = tuple(p for p in paths if re.match('hd', p.name, re.I))
    source_paths = tuple(p for p in paths if p not in hd_paths)
    if not hd_paths or not source_paths:
        raise ConnectorNotReady('IPEDS requires matched HD and C/EF public files for every year')
    institutions, observations, seen_source = {}, {}, set()
    selected_ids = request.options.get('institution_ids')
    if selected_ids is not None and (not isinstance(selected_ids, list) or not selected_ids
            or len(set(map(str, selected_ids))) != len(selected_ids)):
        raise ConnectorError('IPEDS institution_ids must be a unique explicit roster')
    selected_set = set(map(str,selected_ids)) if selected_ids is not None else None
    for path in hd_paths:
        year = _ipeds_year(path, request)
        for raw in _ipeds_rows(path, request):
            s = {k.upper(): v for k, v in raw.items()}
            uid = s['UNITID']
            if (year, uid) in institutions:
                raise ConnectorError('Duplicate IPEDS HD institution/year')
            state = s.get('STABBR') or STATES.get(s.get('FIPS', '').zfill(2))
            # Central/system administrative units do not enroll or award degrees.
            institutions[year, uid] = (state, s.get('CONTROL'), s.get('SECTOR', '1'))
    if selected_ids is not None:
        for year in request.years:
            for uid in map(str,selected_ids):
                if (year,uid) not in institutions:
                    raise ConnectorNotReady('Declared IPEDS roster institution missing in HD vintage')
                _,control,sector=institutions[year,uid]
                if control!='1' or sector=='0':
                    raise ConnectorError('Declared IPEDS roster includes nonpublic/administrative institution')
    for path in source_paths:
        year = _ipeds_year(path, request)
        expected_prefix = 'c' if completion else 'ef'
        if not path.name.lower().startswith(expected_prefix):
            raise ConnectorError('IPEDS unexpected source file type')
        for raw in _ipeds_rows(path, request):
            s = {k.upper(): v for k, v in raw.items()}
            uid = s['UNITID']
            if (year, uid) not in institutions:
                raise ConnectorError('IPEDS outcome institution missing matched HD geography')
            seen_source.add((year, uid))
            if completion:
                if (s.get('CIPCODE') not in ('99', '99.0000') or s.get('AWLEVEL') != '5'
                        or s.get('MAJORNUM') != '1'):
                    continue
                field = 'CTOTALT'
            else:
                if s.get('EFALEVEL') != '2':
                    continue
                field = 'EFTOTLT'
            key = year, uid
            if key in observations:
                raise ConnectorError('Duplicate IPEDS institution/total outcome cell')
            observations[key] = (_number(s.get(field)), str(path))
    values = {}
    for unit in request.units:
        for year in request.years:
            roster = [uid for (y, uid), (state, control, sector) in institutions.items()
                if y == year and state == unit and control == '1' and sector != '0'
                and (uid in selected_set if selected_set is not None else True)]
            # A source-wide institution row with no bachelor award level is a
            # structural zero; an absent institution file record is missing.
            cells = [observations.get((year, uid), (0, '') if completion and
                (year, uid) in seen_source else ('', '')) for uid in roster]
            complete = bool(roster) and all(value != '' for value, _ in cells)
            value = sum(v for v, _ in cells) if complete else ''
            _put(values, (unit, year), value, ';'.join(sorted({p for _, p in cells if p})),
                linkage_certified=complete, counts={'institutions_expected': len(roster),
                    'institutions_present': sum((year, uid) in seen_source for uid in roster)})
    return _finalize(request, values, paths, 'degrees awarded' if completion else 'students', (
        'IPEDS state refers to institution location, not student residence; central/system administrative units are excluded. Original CSV vintage is used unless revised is explicitly declared.',
        'Completions reporting year covers prior July–June; enrollment is fall census headcount. Institution changes and imputation require vintage review.',))


def ipeds_completions(request):
    return _ipeds(request, True)


def ipeds_enrollment(request):
    return _ipeds(request, False)


def nchs_mortality(request: ConnectorRequest):
    measure = _measure(request, 'nchs_mortality')
    overdose = measure.startswith('drug_overdose')
    if overdose:
        exact_filters(request, {'sex': 'Both Sexes', 'age': 'All Ages', 'race': 'All Races-All Origins'})
    elif set(request.filters) != {'cause_name'}:
        raise ConnectorNotReady('Final NCHS leading-causes source requires exact cause_name')
    elif request.filters['cause_name'] not in NCHS_SUPPORTED_CAUSES:
        raise ConnectorNotReady('first-order outcome unavailable: cause not published in final NCHS leading-causes source')
    request = _licensed(request, 'CDC NCHS NVSS')
    paths, values = request.paths(), {}
    for path in paths:
        for source in _csv_rows(path):
            if any(source.get(k) != str(v) for k, v in request.filters.items()):
                continue
            unit = NAMES.get(source.get('state', ''))
            year = int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            if overdose and not {'age', 'race', 'sex', 'deaths', 'ageadjrate'} <= source.keys():
                raise ConnectorError('Drug poisoning source schema mismatch')
            field = ('ageadjrate' if overdose else 'aadr') if measure.endswith('rate') else 'deaths'
            value = _number(source.get(field))
            _put(values, (unit, year), value, path)
    return _finalize(request, values, paths,
        'deaths per 100,000, age adjusted' if measure.endswith('rate') else 'deaths', (
        'Final published state mortality cells only; provisional rolling 12-month counts are not annual final outcomes.',
        'Age-adjusted rates use the source standard population; small/suppressed cells remain missing.',))


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.cells=None; self.text=None
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.cells=[]
        if tag in ('td', 'th') and self.cells is not None: self.text=[]
    def handle_data(self, data):
        if self.text is not None: self.text.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.text is not None:
            self.cells.append(' '.join(''.join(self.text).split())); self.text=None
        if tag == 'tr' and self.cells is not None:
            self.rows.append(self.cells); self.cells=None


def fbi_ucr_state(request: ConnectorRequest):
    measure = _measure(request, 'fbi_ucr_state')
    exact_filters(request, {'series': 'estimated_state_total'})
    if max(request.years) > 2019 or min(request.years) < 2010:
        raise ConnectorNotReady('Legacy UCR table 5 adapter supports 2010–2019 only; CDE/NIBRS transition needs separate certification')
    request = _licensed(request, 'FBI UCR')
    paths, values = request.paths(), {}
    fields = ['violent_crime_count','homicide_count','rape_count','robbery_count',
        'aggravated_assault_count','property_crime_count','burglary_count','larceny_count',
        'motor_vehicle_theft_count']
    for path in paths:
        match = re.search(r'(20\d{2})', path.name)
        if not match: raise ConnectorError('UCR filename must bind publication year')
        year = int(match.group(1))
        if path.suffix.lower() in ('.html', '.htm'):
            parser = _TableParser(); parser.feed(path.read_text()); table = parser.rows
        elif path.suffix.lower() == '.xls':
            try: import xlrd
            except ImportError as exc:
                raise ConnectorNotReady('XLS source needs optional xlrd; official HTML table 5 is also supported') from exc
            book = xlrd.open_workbook(str(path)); sheet = book.sheet_by_index(0)
            table = [[str(v).strip() for v in sheet.row_values(i)] for i in range(sheet.nrows)]
        else: raise ConnectorError('UCR adapter requires official table 5 HTML/XLS')
        state = None
        for cells in table:
            for cell in cells[:2]:
                clean = re.sub(r'[\d,]+$', '', cell).strip().title()
                if clean in NAMES: state = NAMES[clean]
            if not any(re.fullmatch(r'State Total\d*', cell, re.I) for cell in cells):
                continue
            if state is None: raise ConnectorError('UCR total row has no state heading')
            # The final 10 columns are population followed by 9 offense counts;
            # reporting-area rows and rate rows are never summed or substituted.
            numbers = [v for v in cells if re.fullmatch(r'[\d,]+(?:\.0)?', v)]
            if len(numbers) != 10: raise ConnectorError('Unexpected UCR state-total columns')
            if state in request.units and year in request.years:
                _put(values,(state,year),_number(numbers[fields.index(measure)+1]),path,
                    counts={'population': _number(numbers[0]), 'estimated_total': 1})
    return _finalize(request, values, paths, 'offenses known to police, estimated state total', (
        'UCR totals include FBI estimation for incomplete reporting and measure police-reported offenses, not victimization.',
        'Legacy SRS ends at 2019 here; revised rape definition in 2013 must be declared in any rape window.',))
