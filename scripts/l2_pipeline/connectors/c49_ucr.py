"""C49 outcome-only UCR estimated violent/property rates from pinned local files."""
import csv, math
from pathlib import Path
from .common import ConnectorError, NAMES, STATES, row, metadata
from .c46_core import REPO, source_record, coverage, construct
from .wonder import write_once
from .c35_public import _state
from ..storage import sha

BASE=REPO/'data/l2_pipeline/connectors_v2/c49_20261010/review1'
RAW=REPO/'data/l2_pipeline/connectors_v2/fbi_ucr_state/raw'
UNITS=tuple(sorted(STATES.values()))
SERIES={'fbi_ucr_violent_crime_rate':'violent_crime','fbi_ucr_property_crime_rate':'property_crime'}
UNIT='offenses per 100,000 residents'


def number(value):
    if value is None or str(value).strip().lower() in {'','na','n/a','null','--','*'}:return None
    try:n=float(value)
    except ValueError as exc:raise ConnectorError('UCR invalid numeric cell') from exc
    if not math.isfinite(n) or n<0:raise ConnectorError('UCR negative/nonfinite cell')
    return n


def parse(records,field):
    cells={};years=set()
    for r in records:
        y=int(r['year']);u,corrected,limited=_state(r,year=y)
        if u is None:continue  # National aggregates cannot substitute for state rows.
        if u not in UNITS or y<1979:
            raise ConnectorError('UCR state/year identity differs')
        key=u,y
        if key in cells:raise ConnectorError('UCR duplicate state/year')
        years.add(y);count=number(r[field]);pop=number(r['population']);note=r.get('caveats','')
        available=count is not None and pop is not None and pop>0
        limited=limited or 'limited data' in note.lower()
        cells[key]=dict(count=count,population=pop,value=count/pop*100000 if available else None,
            available=available,limited_data=limited,source_id_corrected=corrected,source_issue=note,
            series_break='NIBRS_transition' if y==2021 else 'revised_rape_definition' if field=='violent_crime' and y==2013 else 'reporting_practice_change' if 'not comparable' in note.lower() else '',
            provisional=False,partial_year=False,
            missing_reason='' if available else 'offense_count_or_population_unavailable')
    if not years or min(years)!=1979 or sorted(years)!=list(range(1979,max(years)+1)):
        raise ConnectorError('UCR calendar must start1979 without gaps')
    return cells,sorted(years)


def build():
    p=RAW/'estimated_crimes_1979_2025.extracted.csv'
    inventory=[source_record(p),source_record(RAW/'estimated_crimes_1979_2025.xlsx')]
    with p.open(encoding='utf-8-sig',newline='') as f:records=list(csv.DictReader(f))
    specs={}
    for sid,field in SERIES.items():
        cells,years=parse(records,field);rows=[];meta=[]
        filters=dict(population='all_residents',measure=field)
        for u in UNITS:
            for y in years:
                c=cells.get((u,y),dict(value=None,count=None,population=None,available=False,limited_data=False,source_issue='',series_break='',provisional=False,partial_year=False,missing_reason='published_state_year_absent'))
                r=row(u,y,c['value'],UNIT,outcome_scale='rate',offense_count=c['count'],population_denominator=c['population']);rows.append(r)
                usable=c['available'] and not any(c[k] for k in ('limited_data','provisional','partial_year'))
                meta.append(metadata(r,usable,linkage_certified=usable,denominator_available=int(c['population'] is not None and c['population']>0),
                    **{k:v for k,v in c.items() if k not in {'value','count','population','available'}}))
        artifact=BASE/'parsed'/(sid+'.json');support=BASE/'support'/(sid+'.json')
        limitations=['Published FBI estimated offenses over the same file resident population; no local imputation.',
            'Voluntary UCR reporting and source caveats retained; rape definition changes affect violent crime in2013; NIBRS transition flagged2021.',
            '2025 Florida and North Dakota limited-data flags close support; no whole-year exclusion is inferred for other states.',
            'License text not verified in inherited source receipt; federal FBI product provenance retained.']
        write_once(artifact,dict(series_id=sid,population_filters=filters,years=years,rows=rows,metadata=meta,inventory=inventory,limitations=limitations,
            details=dict(numerator=field,denominator='population in same estimated-crimes file',outcome_association_calls=0)))
        write_once(support,dict(series_id=sid,population_filters=filters,years=years,metadata=meta,artifact_path=str(artifact.relative_to(REPO)),artifact_sha256=sha(artifact),coverage=coverage(meta),outcome_values_in_sidecar=False,outcome_association_calls=0))
        specs[sid]=dict(series_id=sid,adapter='c49_artifact',adapter_version='c49_offline_v1',years=years,geography='US states and DC',
            population='All-age resident state population; reported UCR estimated '+field,population_filters=filters,units=UNIT,frequency=['annual'],
            source_url=inventory[0]['url'],release_lag='Pinned FBI estimated1979–2025;2025 FL/ND limited-data footnotes',public_available=True,status='receipt_verified_local_artifact',
            license=inventory[0]['license'],registered_connector='outcome_v10_'+sid,artifact=dict(path=str(artifact.relative_to(REPO)),sha256=sha(artifact)),
            support_artifact=dict(path=str(support.relative_to(REPO)),sha256=sha(support)),paths=[str(artifact.relative_to(REPO))],source_sha256={str(artifact.relative_to(REPO)):sha(artifact)},
            observed_coverage=coverage(meta),requested_year_range=[min(years),max(years)],legal_certification_granted=False)
    write_once(BASE/'BUILD_RECEIPT.json',dict(series=specs,inputs=inventory,downloads=[],actual_data_estimation_calls=0,outcome_association_calls=0))
    return specs
