"""Offline housing authorizations and disclosed NAICS6244 payroll employment."""
import csv
from .c59_core import REPO, UNITS, save
from .c59_fiscal import population, POP, per_capita
from .c56_eia import number
from .common import STATES, ConnectorError
ROOT=REPO/'data/l2_pipeline/connectors_v2'


def permit_values(paths):
    values={}
    for path in paths:
        year=int(path.stem[2:6])
        with path.open() as f:
            for r in csv.reader(f):
                if len(r)<17 or r[1].strip() not in STATES or not r[0].strip().isdigit():continue
                state=STATES[r[1].strip()];k=state,year
                components=[number(r[i]) for i in (6,9,12,15)] # units, including imputation; not buildings or reported-only units.
                if any(v is None for v in components):continue
                if k in values:raise ConnectorError('Duplicate BPS state-year')
                values[k]=sum(components)
    return values


def qcew_values(paths):
    values={};flags={}
    for path in paths:
        with path.open() as f:
            for r in csv.DictReader(f):
                area=r['area_fips'];state=STATES.get(area[:2])
                if not state or area[2:]!='000' or r['own_code']!='5' or r['industry_code']!='6244' or r['size_code']!='0' or r['qtr']!='A':continue
                k=state,int(r['year'])
                if k in values or k in flags:raise ConnectorError('Duplicate QCEW state-year')
                if r['disclosure_code'].strip():flags[k]={'suppressed':True,'missing_reason':'publisher_disclosure_suppression'};continue
                n=number(r['annual_avg_emplvl'])
                if n is not None:values[k]=n
    return values,flags


def build():
    out={};paths=sorted((ROOT/'census_bps/raw').glob('st*a.txt'));values=permit_values(paths);pop=population()
    sid='census_bps_housing_units_authorized_per_1000'
    flags={k:{'missing_reason':'population_denominator_absent','denominator_available':False} for k in values if k not in pop}
    spec=save(sid,range(1980,2026),per_capita(values,pop,1000),paths+[POP],
        'New privately owned residential housing units authorized by building permits per 1,000 residents',
        'https://www.census.gov/construction/bps/','housing units per 1,000 residents',flags=flags,
        filters={'measure':'total_units_authorized','coverage':'including_imputation'},
        details={'source_columns_0based':[6,9,12,15],'population_source':'SEDS TPOPP','time_basis':'calendar year'},
        limitations=['Permits authorize units; they do not measure completions or net housing supply. Areas without permit requirements are outside BPS.',
                     '2025 population denominator absent; no forward fill. Permit universe updates can change coverage.'])
    spec.update(construct='housing_units_authorized',population_metadata={'ages':[0,120],'restrictions':{},'population_unit':'housing_unit','aliases':['housing_units_authorized','building_permits','building_permits_per_capita']});out[sid]=spec
    paths=sorted((ROOT/'bls_qcew_industry/raw').glob('*_6244.csv'));values,flags=qcew_values(paths)
    sid='bls_qcew_child_day_care_employment_local'
    spec=save(sid,range(2001,2026),values,paths,'Private UI-covered payroll jobs in NAICS6244 Child Day Care Services; workplace state',
        'https://www.bls.gov/cew/','payroll jobs',filters={'ownership':'private','industry':'6244'},flags=flags,
        details={'source_field':'annual_avg_emplvl','ownership':'5','time_basis':'annual average'},
        limitations=['Jobs, not people or children; private UI-covered establishments only. Suppressed values remain missing; no worker demographics.'])
    spec.update(construct='child_day_care_payroll_employment',population_metadata={'ages':[0,120],'restrictions':{},'population_unit':'payroll_job','aliases':['child_day_care_payroll_employment']});out[sid]=spec
    return out
