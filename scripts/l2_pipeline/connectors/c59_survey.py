"""C59 offline ASEC and legacy BRFSS: descriptive outcomes, no policy inputs.

ASEC income/coverage refers to the preceding calendar year; demographic groups
are measured at interview. No replicate-weight variance estimates are produced.
"""
from __future__ import annotations
import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from .common import ConnectorError, STATES
from .c59_core import REPO, save
from .c46_core import source_record
from .wonder import write_once

BASE = REPO / 'data/l2_pipeline/connectors_v2/c59/surveys'
RAW = REPO / 'data/l2_pipeline/connectors_v2'
PENPLAN_MEASUREMENT = 'PENPLAN: employer or union offers a retirement plan to any employees; does not establish respondent eligibility. PENINCL separately measures own participation.'
FIELDS = 'MARSUPWT A_AGE A_SEX PRCITSHP PEHSPNON PERLIS POV_UNIV PEARNVAL WECLW CLWK PENPLAN PENINCL PEDISREM PEDISEAR PEDISEYE PEDISPHY PEDISDRS PEDISOUT WKSWORK HRSWK'.split()


def dictionary_fields(text):
    """Read one-based fixed-width offsets, retaining duplicate-name safeguards."""
    result = {}
    for name, width, position in re.findall(r'\b([A-Z][A-Z0-9_]+)\s+(\d+)\s+(\d+)\s*\(', text):
        if name not in set(FIELDS + ['GESTFIPS','COV']):
            continue
        value = (int(position) - 1, int(width))
        if name in result and result[name] != value:
            raise ConnectorError('ASEC ambiguous dictionary field: ' + name)
        result[name] = value
    return result


def field(record, definition):
    start, width = definition
    raw = record[start:start + width]
    if len(raw) != width or not raw.strip():
        raise ConnectorError('ASEC truncated/blank fixed-width value')
    try:
        return int(raw)
    except ValueError as exc:
        raise ConnectorError('ASEC nonnumeric fixed-width value') from exc


def iter_people(lines, definitions):
    """Household state is inherited only until the next type-1 record."""
    state = None
    for line in lines:
        if line[:1] == b'1':
            state = STATES.get(str(field(line, definitions['GESTFIPS'])).zfill(2))
        elif line[:1] == b'3':
            if state is None:
                raise ConnectorError('ASEC person without valid household state')
            person = {}
            for name in FIELDS:
                try:
                    person[name] = field(line, definitions[name])
                except ConnectorError as exc:
                    raise ConnectorError('ASEC invalid field ' + name) from exc
            if 'COV' in definitions:
                person['COV'] = field(line, definitions['COV'])
            if not 0 <= person['A_AGE'] <= 85 or person['A_SEX'] not in (1, 2):
                raise ConnectorError('ASEC demographic field outside dictionary range')
            yield state, person
        elif line[:1] not in (b'2', b'', b'\n', b'\r'):
            raise ConnectorError('ASEC unknown record type')


def weighted_median(values):
    pairs = sorted((float(v), float(w)) for v, w in values if w > 0)
    total = sum(w for _, w in pairs)
    if not total:
        return None
    cumulative = 0.
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= total / 2:
            return value


def measures(p):
    """Explicit survey populations; no proxy silently relabelled as hourly pay."""
    age, income = p['A_AGE'], p['PEARNVAL']
    adult = 18 <= age <= 64
    noncitizen = p['PRCITSHP'] == 5
    cognitive = adult and p['PEDISREM'] == 1
    disability = adult and any(p[k] == 1 for k in ('PEDISEAR','PEDISEYE','PEDISREM','PEDISPHY','PEDISDRS','PEDISOUT'))
    result = {}
    if p['POV_UNIV'] == 1 and p['PERLIS'] in (1, 2, 3, 4):
        for group, eligible in [('all', True), ('children', age < 18), ('noncitizen_children', age < 18 and noncitizen), ('disability_age18_64', disability)]:
            if eligible:
                result['poverty_pct_' + group] = float(p['PERLIS'] == 1)
    if p.get('COV') in (1, 2):
        for group, eligible in [('all', True), ('noncitizen_children', age < 18 and noncitizen), ('hispanic_noncitizen_age18_64', adult and noncitizen and p['PEHSPNON'] == 1)]:
            if eligible:
                result['insured_pct_' + group] = float(p['COV'] == 1)
    if adult and income > 0:
        if disability:
            result['mean_annual_earnings_disability_age18_64'] = income
        if cognitive:
            result['mean_annual_earnings_cognitive_difficulty_age18_64'] = income
        if p['WECLW'] == 4:
            result['mean_annual_earnings_private_household_workers_age18_64'] = income
        if p['WKSWORK'] >= 50 and p['HRSWK'] >= 35:
            result['median_annual_earnings_ftyr_' + ('female' if p['A_SEX'] == 2 else 'male')] = income
    if adult and p['CLWK'] == 1 and p['WECLW'] in (1, 4, 5) and p['PENPLAN'] in (1, 2):
        result['retirement_plan_access_pct_private_workers_age18_64'] = float(p['PENPLAN'] == 1)
        if p['PENPLAN'] == 2 or p['PENINCL'] in (1, 2):
            result['retirement_plan_participation_pct_private_workers_age18_64'] = float(p['PENPLAN'] == 1 and p['PENINCL'] == 1)
    return result


def _population(key):
    ages = [0, 120]
    restrictions = {}
    if 'children' in key:
        ages = [0, 17]
    elif any(s in key for s in ('age18_64', 'ftyr')):
        ages = [18, 64]
    if 'noncitizen' in key:
        restrictions['citizenship'] = 'noncitizen'
    if 'hispanic' in key:
        restrictions['ethnicity'] = 'Hispanic'
    if 'cognitive' in key:
        restrictions['disability'] = 'cognitive_difficulty'
    elif 'disability' in key:
        restrictions['disability'] = 'any_of_six_difficulties'
    if 'private_household' in key:
        restrictions['longest_job_class'] = 'private_household'
    elif 'private_workers' in key:
        restrictions['longest_job_class'] = 'private_wage_salary'
    if 'earnings' in key:
        restrictions['earnings'] = 'positive_annual_earnings'
    if 'ftyr' in key:
        restrictions.update(weeks_worked='50_or_more', usual_hours='35_or_more')
    if 'plan_access' in key:
        restrictions['plan_measure'] = 'employer_offers_plan_to_any_employees'
    if key.startswith('poverty'):
        restrictions['universe'] = 'official_poverty_universe'
    return ages, restrictions


def asec():
    values = defaultdict(dict)
    stats = defaultdict(dict)
    sources = []
    definitions_by_year = {}
    raw = RAW / 'census_cps_asec/raw'
    for survey_year in range(2016, 2025):
        if survey_year <= 2018:
            dictionary = raw / {2016:'Asec2016_Data_Dict_Full.txt',2017:'08ASEC2017_Data_Dict_Full.txt',2018:'08ASEC2018_Data_Dict_Full.txt'}[survey_year]
        else:
            stem = {2019:'06_ASEC_2019-Data_Dictionary_Full',2020:'ASEC2020ddl_pub_full'}.get(survey_year, f'asec{survey_year}_ddl_pub_full')
            dictionary = BASE / 'dictionaries' / (stem + '.txt')
            sources.append(raw / (stem + '.pdf'))
        definitions = dictionary_fields(dictionary.read_text(errors='replace'))
        # 2019 PDF wraps COV after its offset; this is printed at page 6C-38.
        if survey_year == 2019:
            if not re.search(r'1\s+916\s+\(0:2\)\s+Values[^\n]*\nCOV', dictionary.read_text()):
                raise ConnectorError('2019 wrapped COV dictionary locator changed')
            definitions['COV'] = (915, 1)
        if not set(FIELDS + ['GESTFIPS']) <= definitions.keys():
            raise ConnectorError('ASEC dictionary lacks required fields for ' + str(survey_year))
        definitions_by_year[str(survey_year)] = {k:definitions[k] for k in FIELDS + ['GESTFIPS'] + (['COV'] if 'COV' in definitions else [])}
        zpath = next(raw.glob(f'asec{survey_year}_pubuse*.zip'))
        sources.extend([zpath, dictionary])
        aggregates = defaultdict(lambda: [0., 0., 0, 0.])
        medians = defaultdict(list)
        with zipfile.ZipFile(zpath) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith('.dat')]
            if len(names) != 1:
                raise ConnectorError('ASEC expected one ASCII member')
            with archive.open(names[0]) as handle:
                for state, p in iter_people(handle, definitions):
                    weight = p['MARSUPWT'] / 100
                    if weight <= 0:
                        continue
                    for key, value in measures(p).items():
                        a = aggregates[key, state]
                        a[0] += value * weight; a[1] += weight; a[2] += 1; a[3] += weight * weight
                        if key.startswith('median_'):
                            medians[key, state].append((value, weight))
        for (key, state), (total, denominator, n, weights_squared) in aggregates.items():
            value = weighted_median(medians[key, state]) if key.startswith('median_') else total / denominator
            if '_pct_' in key:
                value *= 100
            cell = (state, survey_year - 1)
            values[key][cell] = value
            stats[key][cell] = dict(unweighted_n=n, weighted_denominator=denominator, kish_effective_n=denominator**2/weights_squared)
    # Ratio is of the two weighted medians, never the median of individual ratios.
    key = 'female_male_median_annual_earnings_ratio_ftyr'
    for cell, female in values['median_annual_earnings_ftyr_female'].items():
        male = values['median_annual_earnings_ftyr_male'].get(cell)
        if male and male > 0:
            values[key][cell] = female / male
            a=stats['median_annual_earnings_ftyr_female'][cell];b=stats['median_annual_earnings_ftyr_male'][cell]
            stats[key][cell] = dict(unweighted_n=min(a['unweighted_n'],b['unweighted_n']),kish_effective_n=min(a['kish_effective_n'],b['kish_effective_n']))
    del values['median_annual_earnings_ftyr_male'], values['median_annual_earnings_ftyr_female']
    write_once(BASE/'ASEC_FIELD_MAP.json',dict(zero_based_offsets=True,survey_years=definitions_by_year))
    specifications={}
    for key, cells in sorted(values.items()):
        sid = 'cps_asec_' + key
        ages, restrictions = _population(key)
        construct = ('poverty' if key.startswith('poverty') else 'insurance_coverage' if key.startswith('insured') else 'employer_retirement_plan_offering' if 'plan_access' in key else 'retirement_plan_participation' if 'plan_participation' in key else 'gender_annual_earnings_ratio' if key.startswith('female_male') else 'annual_earnings')
        filters = dict(age_min=ages[0],age_max=ages[1],measure=key,**restrictions)
        flags={cell:dict(sampling_error=True,limited_data=s['unweighted_n']<30 or s['kish_effective_n']<30,
                         series_break='2019_ASEC_processing_system_redesign' if cell[1]==2018 else '') for cell,s in stats[key].items()}
        years = range(2018 if key.startswith('insured') else 2015, 2024)
        population = ('CPS ASEC private-sector longest-job workers age18–64; share whose employer offers a retirement plan to any employees; respondent eligibility is not measured' if 'plan_access' in key else 'CPS ASEC; '+key.replace('_',' '))
        spec=save(sid,years,cells,sources,population,
            'https://www.census.gov/data/datasets/time-series/demo/cps/cps-asec.html',
            'percent' if '_pct_' in key else 'ratio' if key.startswith('female_male') else 'nominal_USD_per_year',
            filters=filters,flags=flags,output_base=BASE/'penplan_offering_correction' if 'plan_access' in key else BASE,
            details=dict(survey_weight='MARSUPWT / 100',reference_year='survey_year - 1',
                cell_diagnostics=[dict(unit=u,year=y,**s) for (u,y),s in sorted(stats[key].items())],
                replicate_weights_used=False,variance_estimated=False,
                demographic_reference='age/citizenship/disability at interview; earnings, poverty and insurance refer to prior year',
                analysis_segments=[[2015,2017],[2018,2023]],
                **({'measurement_definition':PENPLAN_MEASUREMENT} if 'plan_access' in key else {})),
            limitations=['Small state/subgroup samples: limited_data flags N<30 or Kish effective N<30; this threshold is descriptive and is not a variance estimate.',
                'ASEC complex survey weights give point estimates; replicate weights are required if survey sampling variance is used in a subsequent frozen design.',
                '2019 survey processing redesign is marked in reference year 2018. Pandemic 2020 survey nonresponse affects reference year 2019.',
                'Earnings are positive nominal annual earnings, include self-employment where population allows, and are topcoded; no hourly-wage interpretation or inflation adjustment.',
                'Private-household class is narrower than all domestic occupations; noncitizen does not identify unauthorized status.',
                'No policy or adoption outcomes joined; no preregistered association tested.'])
        spec.update(construct=construct,population_metadata=dict(ages=ages,restrictions=restrictions,population_unit='person',aliases=[sid]))
        specifications[sid]=spec
    return specifications


BRFSS_SELECTORS = {
    'brfss_current_smoking_pct_legacy_spliced': ({'_RFSMOK2','_RFSMOK3'},'adult_cigarette_smoking',[18,120]),
    'brfss_binge_drinking_pct_legacy_spliced': ({'_RFBINGE','_RFBING2','_RFBING3','_RFBING4','_RFBING5','_RFBING6'},'adult_binge_drinking',[18,120]),
    'brfss_mammogram_past2years_women40plus_legacy': ({'_RFMAM2Y'},'mammography_screening',[40,120]),
    'brfss_mammogram_past2years_women50plus_legacy': ({'_MAM502Y'},'mammography_screening',[50,120]),
    'brfss_ever_colorectal_endoscopy_age50plus_legacy': ({'_RFSIGMD','_RFSIGM2'},'colorectal_screening',[50,120]),
}


def brfss_rows(records, allowed_questions):
    values, flags, questions = {}, {}, {}
    for original in records:
        r = {k.lower():v for k,v in original.items()}
        if r['questionid'] not in allowed_questions or r['response'] != 'Yes' or r['locationabbr'] not in STATES.values():
            continue
        if r['break_out'] != 'Overall':
            continue
        if r['data_value_type'] != 'Crude Prevalence' or r['data_value_unit'] != '%':
            raise ConnectorError('BRFSS incompatible scale/standardization')
        key = r['locationabbr'], int(r['year'])
        if key in values:
            raise ConnectorError('BRFSS duplicate overall cell')
        v = float(r['data_value']) if r['data_value'].strip() else None
        if v is not None and not 0 <= v <= 100:
            raise ConnectorError('BRFSS prevalence outside 0..100')
        values[key] = v
        questions[key] = r['questionid']
        note = r['data_value_footnote']
        flags[key] = dict(suppressed=bool(note and v is None),suppression_reason=note,
                          missing_reason=note if v is None else '',sampling_error=True)
    return values, flags, questions


def brfss():
    legacy = RAW/'cdc_brfss_legacy/raw/brfss_prevalence_2010_and_prior.csv'
    modern = RAW/'cdc_brfss/raw/brfss_smoking_ecig_binge_overall_2011_2025.csv'
    allowed = set().union(*(entry[0] for entry in BRFSS_SELECTORS.values()))
    # Keep only eligible published rows in memory (the legacy file is 535MB).
    selected=[]
    for path in (legacy,modern):
        with path.open(newline='',encoding='utf-8-sig') as handle:
            for original in csv.DictReader(handle):
                r = {k.lower():v for k,v in original.items()}
                if r['questionid'] in allowed and r['response']=='Yes' and r['break_out']=='Overall':
                    selected.append(r)
    specs={}
    for sid,(questions,construct,ages) in BRFSS_SELECTORS.items():
        values, flags, question_cells = brfss_rows(selected,questions)
        spliced = sid.endswith('_spliced')
        years=range(1995,2026 if spliced else 2011)
        if not spliced:
            values={k:v for k,v in values.items() if k[1]<=2010}
        for u in STATES.values():
            if spliced:
                flags.setdefault((u,2011),{})['series_break']='2011_BRFSS_raking_and_cellphone_sampling'
            if 'binge' in sid:
                flags.setdefault((u,2006),{})['series_break']='2006_female_binge_threshold_5_to_4_drinks'
                flags.setdefault((u,2022),{})['series_break']='2022_binge_variable_RFBING5_to_RFBING6'
        restrictions={'sex':'female'} if 'mammogram' in sid else {}
        sources=[legacy,modern] if spliced else [legacy]
        spec=save(sid,years,values,sources,'BRFSS weighted crude prevalence; '+sid,
            'https://data.cdc.gov/resource/y4ft-s73h.csv','percent',
            filters=dict(age_min=ages[0],age_max=ages[1],breakout='Overall',measure=sid,**restrictions),
            flags=flags,output_base=BASE,
            details=dict(question_ids=sorted(questions),
                question_ids_by_year={str(y):sorted({q for (u,year),q in question_cells.items() if year==y}) for y in years},
                analysis_segments=([[1995,2005],[2006,2010],[2011,2021],[2022,2025]] if 'binge' in sid else [[1995,2010],[2011,2025]] if spliced else [[1995,2010]])),
            limitations=['2011 adds cellphone sampling and raking: the splice preserves levels and marks the break; it is not a harmonized continuous trend.',
                'CDC published weighted crude prevalence, with published suppression preserved. Missing states/years are not interpolated.',
                'Binge drinking changes from five drinks for everyone to five for men/four for women in 2006; a further variable change is marked in 2022.',
                'Mammography is within two years, not the past-year outcome in some needs_connector requests; colorectal endoscopy is ever-use, not guideline-concordant screening.'])
        spec.update(construct=construct,population_metadata=dict(ages=ages,restrictions=restrictions,population_unit='person',aliases=[sid]))
        specs[sid]=spec
    return specs


def build():
    specs = {**asec(), **brfss()}
    write_once(BASE/'SERIES_SPECS.json',specs)
    inventory={}
    for spec in specs.values():
        artifact=json.loads((REPO/spec['artifact']['path']).read_text())
        for item in artifact['inventory']:
            inventory[item['path']]=item
    write_once(BASE/'SOURCE_INVENTORY.json',dict(new_downloads=[],files=list(inventory.values()),outcome_association_calls=0))
    return specs


if __name__ == '__main__':
    print(json.dumps({sid:s['observed_coverage']['available_cells'] for sid,s in build().items()},indent=2))
