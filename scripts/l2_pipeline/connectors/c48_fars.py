"""C48 drinking-driver crash deaths: exact ACCIDENT.DRUNK_DR > 0.

Only local inputs. No policy/party/association inputs; no BAC substitution or
reconstruction when DRUNK_DR is absent (2021+ in the pinned archives).
"""
import json,zipfile
from pathlib import Path
from .common import ConnectorError,row,metadata,sha
from .fars import _int,_state
from .c35_fars import historical_rows,HIST
from .c46_fars import FARS_BASE,EXISTING
from .c46_core import REPO,source_record,coverage,construct
from .wonder import write_once

BASE=REPO/'data/l2_pipeline/connectors_v2/c48_20261010'
SID='fars_drinking_driver_crash_fatality_rate'
FILTERS={'population':'all_traffic_fatalities','crash_selection':'ACCIDENT.DRUNK_DR > 0'}
URL='https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars'
UNIT='deaths per 100,000 residents'


def parse_archive(path,year,units):
    counts={u:0 for u in units};totals={u:0 for u in units};seen=set();present=set();unknown_year=set();field_present=None
    with zipfile.ZipFile(path) as archive:
        for cell in historical_rows(archive,'accident.csv',{'STATE','ST_CASE','YEAR','FATALS'}):
            u=_state(cell['STATE']);key=u,_int(cell['ST_CASE'],'ST_CASE');yy=_int(cell['YEAR'],'YEAR')
            if u not in counts or key in seen or (yy not in {year,year%100} and not (year<=1998 and yy==99)):
                raise ConnectorError('C48 duplicate crash / jurisdiction / year')
            seen.add(key);present.add(u)
            if yy==99 and year<=1998:unknown_year.add(u)
            deaths=_int(cell['FATALS'],'FATALS')
            if not 0<=deaths<=99:raise ConnectorError('C48 invalid FATALS')
            totals[u]+=deaths
            available='DRUNK_DR' in cell
            if field_present is not None and available!=field_present:raise ConnectorError('C48 inconsistent DRUNK_DR schema')
            field_present=available
            if available:
                drinking=_int(cell['DRUNK_DR'],'DRUNK_DR')
                if not 0<=drinking<=99:raise ConnectorError('C48 invalid drinking-driver count')
                if drinking>0:counts[u]+=deaths
    if present!=set(units):raise ConnectorError('C48 incomplete national crash census; cannot zero-fill')
    return dict(counts=counts,totals=totals,field_present=field_present,unknown_year=sorted(unknown_year),crashes=len(seen))


def build():
    from . import outcome_v8
    previous=outcome_v8.catalog(verify_sources=False);spec=previous['series']['fars_all_road_fatality_rate']
    p=REPO/spec['artifact']['path']
    if sha(p)!=spec['artifact']['sha256']:raise ConnectorError('C48 inherited denominator artifact changed')
    allroad=json.loads(p.read_text());den={(r['unit'],r['year']):r['population_denominator'] for r in allroad['rows']}
    expected={(r['unit'],r['year']):r['fatality_count'] for r in allroad['rows']}
    years=spec['years'];units=sorted({u for u,y in den});rows=[];meta=[];inv=[source_record(p)]+allroad['inventory'];checks={}
    for y in years:
        path=(HIST if y<=1998 else FARS_BASE/'raw' if y==2024 else EXISTING)/f'FARS{y}NationalCSV.zip'
        parsed=parse_archive(path,y,units);inv.append(source_record(path))
        if any(parsed['totals'][u]!=expected[u,y] for u in units):raise ConnectorError('C48 all-road census cross-check failed')
        checks[str(y)]=dict(crashes=parsed['crashes'],all_road_reproduced=True,drunk_dr_field_present=parsed['field_present'])
        for u in units:
            denominator=den[u,y];available=parsed['field_present'] and denominator is not None and denominator>0
            count=parsed['counts'][u] if parsed['field_present'] else None
            r=row(u,y,count/denominator*100000 if available else None,UNIT,outcome_scale='rate',fatality_count=count,population_denominator=denominator);rows.append(r)
            early=y<=1981
            meta.append(metadata(r,available,linkage_certified=available,denominator_available=int(denominator is not None),
                unknown_year_present=u in parsed['unknown_year'],reporting_coverage_unknown=early,
                source_issue='early_police_reported_drinking_underascertainment' if early else 'DRUNK_DR_field_absent' if not parsed['field_present'] else '',
                missing_reason='' if available else 'ACCIDENT_DRUNK_DR_field_absent' if not parsed['field_present'] else 'resident_denominator_unavailable',
                series_break='ACCIDENT_DRUNK_DR_removed' if y==2021 else 'Census_population_vintage_boundary' if y in {1999,2000,2010,2020} else ''))
    # Stable state/year ordering, identical to the other public artifact adapters.
    rows.sort(key=lambda r:(r['unit'],r['year']));meta.sort(key=lambda r:(r['unit'],r['year']))
    inv=list({r['path']:r for r in inv}.values())
    for r in inv:
        if sha(REPO/r['path'])!=r['sha256']:raise ConnectorError('C48 upstream input changed')
    limitations=[
        'Deaths of ALL persons in a crash with ACCIDENT.DRUNK_DR > 0; not drinking-driver deaths alone and not BAC >=0.08 alcohol-impaired deaths.',
        'DRUNK_DR is a reported drinking-driver count, with weaker early ascertainment and variable police reporting/testing; zero is not proof of no alcohol. Early1975–1981 reporting quality is flagged; no imputation, threshold recoding or retrospective exclusion.',
        '2021–2024 ACCIDENT archives lack DRUNK_DR. Retain missing state-years and close support; do not substitute DR_DRINK, BAC imputation or all-road deaths.',
        'Crash-state numerator / Census resident denominator; denominator vintages inherited byte-bound from the all-road series. No driver/rider mileage exposure denominator.',
        'All-road sums reproduce every pinned inherited annual state count; historical YEAR99 assigned only by annual archive membership and flagged.']
    artifact=BASE/'parsed'/(SID+'.json');support=BASE/'support'/(SID+'.json')
    payload=dict(series_id=SID,population_filters=FILTERS,years=years,rows=rows,metadata=meta,inventory=inv,limitations=limitations,
        details=dict(selection='ACCIDENT.DRUNK_DR > 0',numerator='sum ACCIDENT.FATALS over qualifying crashes',denominator='resident population',raw_years=years,
            numeric_years=[int(y) for y,v in checks.items() if v['drunk_dr_field_present']],quality_basis='Observed archive schema and reported-drinking construct; no outcome associations',outcome_association_calls=0))
    write_once(artifact,payload)
    write_once(support,dict(series_id=SID,population_filters=FILTERS,years=years,metadata=meta,artifact_path=str(artifact.relative_to(REPO)),artifact_sha256=sha(artifact),coverage=coverage(meta),outcome_values_in_sidecar=False,outcome_association_calls=0))
    write_once(BASE/'checks/FARS_BUILD.json',dict(years=checks,downloads=[],actual_data_estimation_calls=0,outcome_association_calls=0))
    return dict(series_id=SID,adapter='c48_artifact',adapter_version='c48_offline_v1',years=years,geography='US states and DC',
        population='All road users killed in crashes with at least one reported drinking driver; crash state; all-age resident denominator',population_filters=FILTERS,
        units=UNIT,frequency=['annual'],source_url=URL,release_lag='Pinned local FARS1975–2024; DRUNK_DR observed1975–2020',public_available=True,
        status='receipt_verified_local_artifact',license='; '.join(sorted({r['license'] for r in inv})),registered_connector='outcome_v9_'+SID,
        artifact=dict(path=str(artifact.relative_to(REPO)),sha256=sha(artifact)),support_artifact=dict(path=str(support.relative_to(REPO)),sha256=sha(support)),
        paths=[str(artifact.relative_to(REPO))],source_sha256={str(artifact.relative_to(REPO)):sha(artifact)},observed_coverage=coverage(meta),
        requested_year_range=[min(years),max(years)],legal_certification_granted=False,reporting_quality=limitations[:3])
