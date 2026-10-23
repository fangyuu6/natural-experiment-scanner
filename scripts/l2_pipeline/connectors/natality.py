"""Final NCHS public state teen natality cells, no account/key or query engine.

The CDC y268-sna3 publication supplies counts and age-specific rates separately
for maternal ages15–17,18–19 and15–19 in1990–2019. Broader ages and recent
microdata state identifiers are unavailable here; no proxy is silently chosen.
"""
from __future__ import annotations
import csv
import math
import urllib.parse
from .common import (ConnectorError, ConnectorNotReady, ConnectorRequest,
                     NAMES, PanelData, inventory, metadata, row)

DATASET = 'y268-sna3'
AGE_LABELS = {'15-17':'15-17 years','18-19':'18-19 years','15-19':'15-19 years'}
MEASURES = {'births_count':'state_births','birth_rate':'state_rate'}
SOURCE_YEARS = (1990,2019)
LICENSE = 'Public Domain (CDC NCHS USGOV_WORKS); statistical use only, no reidentification'
MISSING = {'','*','**','...','…','-','--','—','NA','N/A','Suppressed','Unreliable','Not available'}

def _contract(request):
    if request.frequency!='annual': raise ConnectorNotReady('Natality publication requires annual frequency')
    age_group=request.filters.get('age_group')
    if set(request.filters)!={'age_group'} or not isinstance(age_group,str) or age_group not in AGE_LABELS:
        raise ConnectorNotReady('first-order outcome unavailable: published maternal ages15-17,18-19,15-19 only')
    measure=request.options.get('measure_id')
    if measure not in MEASURES: raise ConnectorNotReady('first-order outcome unavailable: natality measure must be births_count or birth_rate')
    expected=MEASURES[measure]
    if request.options.get('value_field',expected)!=expected:
        raise ConnectorError('Natality measure/value_field binding differs; counts and rates cannot be interchanged')
    return measure,expected,AGE_LABELS[request.filters['age_group']]

def natality_download_plan(request):
    """Plan only the actually published age/year cells, never synthesize2023."""
    measure,field,age=_contract(request)
    if min(request.years)<SOURCE_YEARS[0] or max(request.years)>SOURCE_YEARS[1]:
        raise ConnectorNotReady('first-order outcome unavailable: final public teen-birth source covers1990–2019 only')
    where=f"age_years='{age}' AND year >= {min(request.years)} AND year <= {max(request.years)}"
    query=urllib.parse.urlencode({'$where':where,'$limit':10000,'$order':'year,state,age_years'})
    return ({'url':'https://data.cdc.gov/resource/'+DATASET+'.csv?'+query,
        'filename':f'natality_{request.filters["age_group"]}_{min(request.years)}_{max(request.years)}.csv',
        'license':LICENSE,'source_agency':'CDC NCHS final NVSS','dataset_id':DATASET,
        'format':'published state maternal-age birth count/rate CSV','max_rows':10000,
        'measure_id':measure,'value_field':field,'size_bytes':None,'sha256':None,
        'status':'planned_not_downloaded'},)

def _number(raw,*,count):
    raw=str(raw).strip()
    if raw in MISSING:return None
    try:value=float(raw.replace(',',''))
    except ValueError as exc:raise ConnectorError('Invalid published natality numeric cell')from exc
    if not math.isfinite(value) or value<0:raise ConnectorError('Invalid published natality numeric cell')
    if count and not value.is_integer():raise ConnectorError('Published birth count must be an integer')
    return int(value)if count else value

def cdc_natality(request: ConnectorRequest) -> PanelData:
    measure,field,age=_contract(request)
    selected={};inv=[];source_years=set();observed_ages=set()
    paths=request.paths()
    required={'year','state','age_years','state_rate','state_births','unit'}
    for path in paths:
        item=inventory(path,request)
        item['license']=request.options.get('license',LICENSE)
        if not item['url']:
            item['url']='https://data.cdc.gov/resource/'+DATASET+'.csv'
        inv.append(item)
        with path.open(newline='',encoding='utf-8-sig')as handle:
            reader=csv.DictReader(handle)
            if not required<=set(reader.fieldnames or []):
                raise ConnectorError('Not the final NCHS y268-sna3 state/age publication schema')
            for cell in reader:
                try:year=int(cell['year'])
                except ValueError as exc:raise ConnectorError('Invalid natality calendar year')from exc
                if not SOURCE_YEARS[0]<=year<=SOURCE_YEARS[1]:
                    raise ConnectorError('Natality file exceeds certified final publication vintage')
                label=cell['age_years'].strip()
                if label not in AGE_LABELS.values():raise ConnectorError('Unknown published maternal-age bin')
                if cell['unit'].strip()!='per 1,000':raise ConnectorError('Unexpected natality rate denominator unit')
                source_years.add(year);observed_ages.add(label)
                area=cell['state'].strip()
                if area in {'United States','Total U.S.'}:continue # National totals are never state observations.
                if area not in NAMES:raise ConnectorError('Unknown natality residence-state label')
                unit=NAMES[area]
                if label!=age or year not in request.years or unit not in request.units:continue
                key=(unit,year)
                if key in selected:raise ConnectorError('Duplicate published natality state/year/age cell')
                selected[key]=_number(cell[field],count=measure=='births_count')
    outcome_unit='births'if measure=='births_count'else 'births per 1,000 females in declared maternal age group'
    rows=[];meta=[]
    for unit in request.units:
        for year in request.years:
            value=selected.get((unit,year))
            record=row(unit,year,'' if value is None else value,outcome_unit,source_age_group=age,geography='maternal_residence_state',
                       measure_id=measure,source_value_field=field)
            rows.append(record)
            meta.append(metadata(record,value is not None,published_cell_present=int((unit,year)in selected),
                source_age_group=age,missing_reason=None if value is not None else
                    'Published cell absent or suppressed; no zero fill, interpolation or extrapolation'))
    return PanelData(tuple(rows),tuple(meta),tuple(inv),
        ('Only published teen maternal-age bins; no ages18–44 or all-age coverage.',
         'Published rates use revised Census population denominators and are not sums of age-bin rates.',
         'The15–19 total overlaps15–17/18–19; choose exactly one bin, never combine overlapping published totals.',
         'Births are live births, not pregnancies, contraceptive use, unintended pregnancy or policy-beneficiary status.'),
        {'source_dataset':DATASET,'source_vintage':'NCHS final1990–2019, published2021',
         'published_years':sorted(source_years),'requested_years':list(request.years),'observed_age_labels':sorted(observed_ages),
         'geography':'maternal_residence_state','measure_id':measure,'value_field':field,
         'source_definition_url':'https://data.cdc.gov/National-Center-for-Health-Statistics/NCHS-U-S-and-State-Trends-on-Teen-Births/y268-sna3',
         'geography_definition_url':'https://www.cdc.gov/nchs/hus/sources-definitions/nvss.htm',
         'outcome_association_calls':0,'interpolation':False,'rate_aggregation':False})
