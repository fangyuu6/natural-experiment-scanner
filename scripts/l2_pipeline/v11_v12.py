"""Prospective V11 comparison selection and V12 first-spell certification.

Only structured dates and evidence are used; narratives cannot supply dates.
"""
from copy import deepcopy
from datetime import date
import re


def comparison_group(records):
    return 'never_treated' if sum(r['status']=='never_treated' for r in records)>=5 else 'not_yet_treated'


def declared_group(analysis, summary=None):
    return (summary or {}).get('comparison_group', analysis.get('comparison_group', analysis.get('controls','never_treated')))


def register_card(card, group):
    value = deepcopy(dict(card))
    prior = value.get('comparison_group')
    if prior is not None and prior != group:
        raise ValueError('Registered comparison group differs from metadata counts')
    value['comparison_group'] = group
    value['analysis']['controls'] = group
    if value['analysis'].get('support_adapter')=='annual_full_year_v1':
        value['treatment']['full_year_declaration']['comparison_group']=group
    return value


def _date(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}(-\d{2})?(-\d{2})?',value):
        return None
    try:
        date.fromisoformat(value + ('-01-01' if len(value)==4 else '-01' if len(value)==7 else ''))
    except ValueError:
        return None
    return value


def interruption_dates(row):
    """Canonicalize aliases; preserve precision and every recorded endpoint.

    A repeal predicate's abolition_interval ends at reinstatement, so that end
    is the interruption of its treated spell. No predicate is inferred here.
    """
    events = row.get('observed_interruptions', [])
    result=[]
    for item in events:
        if not isinstance(item,dict) or not (item.get('source_ids') or item.get('evidence_refs') or item.get('evidence_ref')):
            return None
        interval = item.get('abolition_interval')
        if interval is not None:
            start = _date(interval.get('end'))
            end = None
        else:
            start = next((_date(item[k]) for k in ('start','start_date','date','start_year','year','suspended_date') if _date(item.get(k))),None)
            end = next((_date(item[k]) for k in ('end','end_date','end_year') if _date(item.get(k))),None)
        if start is None:
            return None
        result.append((start,end))
    return sorted(set(result),key=lambda x:(x[0],x[1] or ''))


def certified_interruptions(left, right):
    a,b=interruption_dates(left),interruption_dates(right)
    if not left.get('observed_interruptions') and not right.get('observed_interruptions'):
        return None, None
    if a is None or b is None or not a or not b or a!=b:
        return None, 'interruption_conflict'
    onset=left.get('best_evidence_start_year',left.get('start_period'))
    if type(onset) is not int or left.get('status') not in ('treated','not_yet_treated'):
        return None, 'interruption_without_treated_spell'
    relevant=[int(start[:4]) for start,_ in a if int(start[:4])>=onset]
    return (min(relevant)-1 if relevant else None), None


def placebo_controls(design, members, endpoints):
    if design['control_group']=='never_treated' or not design.get('v11_comparison'):
        return [u for u in design['units'] if design['whole_window_clean'][u]
                and all((u,t) in design['mask'] and design['exposure'][u][t]['clean'] for t in endpoints)]
    return [u for u in design['units'] if u not in members
            and (design['adoption'][u] is None or max(endpoints)<design['adoption'][u])
            and all((u,t) in design['mask'] and design['exposure'][u].get(t,{}).get('clean',False) for t in endpoints)]
