"""Even-election-year support and RI geometry; no outcomes or associations."""
from copy import deepcopy
from .support import declared_annual_periods, support_check, standard_annual_declaration

ADAPTER='biennial_even_year_v1'
ODD_ADAPTER='biennial_odd_year_v1'

def adapter(outcome):
    return ODD_ADAPTER if declared_annual_periods(outcome)[0]%2 else ADAPTER


def periods(outcome):
    years=declared_annual_periods(outcome)
    if outcome.get('frequency')!='biennial' or any(y%2!=years[0]%2 for y in years) or years!=list(range(years[0],years[-1]+1,2)):
        raise ValueError('Biennial adapter requires a complete declared same-parity grid')
    return years


def normalize(treatment, analysis, years, transition_year_drop):
    from .prewindow import forbid
    forbid(treatment, 'biennial clock code (T192)')
    rows=deepcopy(treatment);cfg=deepcopy(dict(analysis));axis={y:i for i,y in enumerate(years)}
    def at_or_after(year):return next((i for i,y in enumerate(years) if y>=year),len(years))
    # Events before the window must retain negative labels, never manufacture a
    # within-window cohort with a clean baseline.
    def clock(year):
        if type(year) is not int:raise ValueError('Known integer biennial clock required')
        if year<years[0]:return (year-years[0])//2
        return at_or_after(year)
    for row in rows:
        start=row.get('start_period')
        if row['status'] in {'treated','not_yet_treated'}:
            op=row.get('operative_period',start)
            row['start_period']=clock(start+int(transition_year_drop))
            row['operative_period']=clock(op)
        if row.get('censor_after_period') is not None:
            end=row['censor_after_period'];row['censor_after_period']=max((i for i,y in enumerate(years) if y<=end),default=-1)
    cfg['excluded_periods']=[axis[y] for y in cfg.get('excluded_periods',[]) if y in axis]
    return rows,cfg


def support(keys,treatment,analysis,outcome,transition_year_drop=False):
    years=periods(outcome);axis={y:i for i,y in enumerate(years)}
    rows,cfg=normalize(treatment,analysis,years,transition_year_drop)
    partial={(r['unit'],r.get('operative_period',r.get('start_period'))) for r in treatment
             if transition_year_drop and r['status'] in {'treated','not_yet_treated'}}
    compressed={(u,axis[y]) for u,y in keys if y in axis and (u,y) not in partial}
    result=support_check(compressed,rows,cfg,period_years=list(range(len(years))),
                         omitted_cells={(u,axis[y]) for u,y in partial if y in axis})
    result.update(adapter=adapter(outcome),period_years=years,event_time_unit='survey_period' if years[0]%2 else 'election_period',
        clock_rule='first declared biennial wave at/after operation; operation-year drop uses next wave',
        transition_year_drop=transition_year_drop,outcome_values_parsed=0)
    result['analysis_mask']=[[u,years[i]] for u,i in result.get('analysis_mask',[])]
    result['anchor_years']=[years[i] for i in result.get('anchor_years',[])]
    result['period_index_to_year']={str(i):y for y,i in axis.items()}
    return result


def declaration(treatment,analysis,outcome,summary,*,key,variant,transition_year_drop=False):
    if summary.get('adapter')!=adapter(outcome) or summary.get('transition_year_drop',False)!=transition_year_drop:
        raise ValueError('Biennial support/card adapter or clock mismatch')
    years=periods(outcome)
    if summary['period_years']!=years:raise ValueError('Biennial support/card grid mismatch')
    rows,cfg=normalize(treatment,analysis,years,transition_year_drop)
    normalized_summary=dict(summary,transition_year_drop=False)
    result=standard_annual_declaration(rows,cfg,list(range(len(years))),key=key,variant=variant,summary=normalized_summary)
    result.update(period_years=years,summary=summary,support_adapter=adapter(outcome))
    # structural_rows censors raw calendar keys before normalizing the axis.
    ends={r['unit']:r['censor_after_period'] for r in treatment if r.get('censor_after_period') is not None}
    if ends:result['censor_after_periods']=ends
    if transition_year_drop:
        result.update(transition_year_drop=True,transition_effective_years={r['unit']:r.get('operative_period',r['start_period'])
            for r in treatment if r['status'] in {'treated','not_yet_treated'}})
    return result
