"""Annual ETA5159 state UI claims and payment activities, requiring 12 observed months."""
import csv
from collections import defaultdict
from .c59_core import REPO, BASE, UNITS, save
from .c56_eia import number
from .common import ConnectorError
from .wonder import write_once
RAW=REPO/'data/l2_pipeline/connectors_v2/dol_eta_claims/raw/ar5159.csv'
MAP=BASE/'docs/eta5159_map.pdf'
DEFS={
 'initial_claims':(['c1'],'ui_initial_claims','claims'),
 'continued_weeks_claimed':(['c21','c24'],'ui_continued_weeks_claimed','claimed weeks'),
 'weeks_compensated':(['c38'],'ui_weeks_compensated','compensated weeks'),
 'first_payments':(['c51'],'ui_first_payments','first payments')}


def annual_values(path,columns):
    monthly=defaultdict(dict)
    with path.open() as f:
        for r in csv.DictReader(f):
            if r['st'] not in UNITS:continue
            year,month=map(int,r['rptdate'][:7].split('-'));k=r['st'],year
            if month in monthly[k]:raise ConnectorError('Duplicate ETA5159 state-month')
            ns=[number(r[c]) for c in columns]
            if any(n is not None and n<0 for n in ns):raise ConnectorError('Negative ETA5159 count')
            monthly[k][month]=None if any(n is None for n in ns) else sum(ns)
    out={};flags={}
    for k,months in monthly.items():
        if set(months)==set(range(1,13)) and all(n is not None for n in months.values()):out[k]=sum(months.values())
        else:flags[k]={'partial_year':True,'missing_reason':'requires_12_nonmissing_months','annual_usable':False}
    return out,flags


def build():
    out={}
    for label,(columns,construct,units) in DEFS.items():
        values,flags=annual_values(RAW,columns);sid='dol_eta5159_state_ui_'+label
        spec=save(sid,range(1971,2027),values,[RAW,MAP],
            'Regular state unemployment insurance program; excludes standalone UCFE/UCX, EB, EUC/PEUC and workshare tables',
            'https://oui.doleta.gov/unemploy/DataDownloads.asp',units,filters={'program':'state_UI_regular','measure':label},flags=flags,
            details={'official_dictionary':'ETA5159 data map, State UI lines 101/201/301/303','columns':columns,'aggregation':'sum of 12 complete months'},
            limitations=['Counts are claims, weeks or payment events, not unique people. Claims need not become paid benefits.',
                         'Continued weeks uses intrastate plus interstate received as liable state (c21+c24); agent-state claims excluded to avoid geographic double counting.',
                         'Incomplete years are missing; no extrapolation. Emergency extensions and workshare are distinct tables and not included.'])
        spec.update(construct=construct,population_metadata={'ages':[0,120],'restrictions':{},'population_unit':'ui_program_event','aliases':[construct]});out[sid]=spec
    write_once(BASE/'UI_RECIPIENCY_STATUS.json',dict(status='SKIPPED_MISSING_DENOMINATOR',
        reason='ETA5159 counts alone cannot identify share of all unemployed receiving UI; exact total-unemployed denominator absent in inspected local files. Existing LAUS rate is rounded and is not a denominator. Official Chartbook CSV export returned empty bytes; requested year ranges returned default 1976 only (retained probes in docs). No claim-count ratio substituted.',
        reopen_requires='Official annual total unemployed (LAUS) plus declared recipiency numerator/time basis, or published DOL recipiency rate series.',outcome_association_calls=0))
    return out
