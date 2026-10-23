"""Registered annual exposure variants using one metadata-only constructor."""
from __future__ import annotations
import copy
from datetime import date
from .support import annual_full_year_support


def effective_full_year_declaration(declaration: dict, variant: str) -> tuple[dict, str]:
    """Return the registered effective clocks/scope and support routing.

    Both metadata support and numerical replay must apply these transformations
    once to the original declaration, including earlier actual exposure.
    """
    d=copy.deepcopy(declaration)
    for field in ('omitted_years','exclusions','disputed_audit_error_units','unresolved_audit_units','competing_mechanism_units','narrow_coverage_units','anchor_availability_exclusions','uncertain_history_units'): d.setdefault(field,[])
    for field in ('scope_ambiguous_starts','actual_clean_onset','initial_step_starts','exposure_end_dates'): d.setdefault(field,{})
    d.setdefault('convention_filled_controls', [])
    if (not isinstance(d['convention_filled_controls'], list)
            or any(not isinstance(u, str) for u in d['convention_filled_controls'])
            or len(d['convention_filled_controls']) != len(set(d['convention_filled_controls']))
            or not set(d['convention_filled_controls']) <= set(d['never_controls'])):
        raise ValueError('Convention-filled controls must be distinct members of the registered never pool')
    if not isinstance(d['exposure_end_dates'], dict):
        raise ValueError('Exposure end dates must be an explicit unit/date mapping')
    for u, end in d['exposure_end_dates'].items():
        if u not in d['operative'] or not isinstance(end, str):
            raise ValueError('Exposure end dates must bind registered treated units')
        try:
            parsed = date.fromisoformat(end)
        except ValueError as exc:
            raise ValueError('Exposure end dates must be ISO calendar dates') from exc
        if parsed.isoformat() != end or parsed <= date(d['operative'][u], 1, 1):
            raise ValueError('Exposure end must follow the operative start')
    handling = d.get('reversal_handling', 'drop_states')
    if handling not in {'drop_states', 'retain_ended_exposure'}:
        raise ValueError('Unregistered reversal handling')
    fixed={'primary','not_yet_controls','drop_disputed_audit_error','drop_unresolved_audit_dates','exclude_convention_controls','start_plus_one','start_minus_one','exclude_2020_2021','include_2020_2021','exclude_competing_mechanism','include_scope_ambiguous','drop_narrow_coverage','initial_step_clock','pre_step_anticipation','drop_uncertain_history_states','constant_definition_window','retain_ended_exposure'}
    if variant not in fixed and not variant.startswith(('omit_state_','omit_cohort_')): raise ValueError('Unregistered annual variant')
    routed=variant
    if variant == 'retain_ended_exposure':
        handling = 'retain_ended_exposure'; routed = 'primary'
    if handling == 'drop_states':
        boundary = date(d['window'][1] + 1, 1, 1)
        reversals = {u for u, end in d['exposure_end_dates'].items()
                     if date.fromisoformat(end) < boundary}
        d['exclusions'] = sorted(set(d['exclusions']) | reversals)
    if variant == 'exclude_convention_controls':
        d['never_controls'] = sorted(set(d['never_controls']) - set(d['convention_filled_controls']))
        routed = 'primary'
    for name,field in [('drop_disputed_audit_error','disputed_audit_error_units'),('drop_unresolved_audit_dates','unresolved_audit_units'),('exclude_competing_mechanism','competing_mechanism_units'),('drop_narrow_coverage','narrow_coverage_units'),('drop_uncertain_history_states','uncertain_history_units')]:
        if variant==name: d['exclusions']=sorted(set(d['exclusions'])|set(d[field]));routed='primary'
    if variant=='constant_definition_window':
        window=d.get('constant_definition_window')
        if (not isinstance(window,list) or len(window)!=2 or any(type(y) is not int for y in window)
                or not d['window'][0]<=window[0]<=window[1]<=d['window'][1]):
            raise ValueError('Constant-definition window must be a registered integer subset of the base window')
        d['window']=list(window)
        d['period_years']=list(range(window[0],window[1]+1))
        routed='primary'
    if variant=='pre_step_anticipation':
        d['actual_clean_onset']={u:min(y,d['actual_clean_onset'].get(u,y),d['initial_step_starts'].get(u,y))-1 for u,y in d['operative'].items()};routed='primary'
    if variant=='initial_step_clock': d['operative'].update(d['initial_step_starts']);routed='primary'
    if variant=='include_scope_ambiguous':
        d['exclusions']=sorted(set(d['exclusions'])-set(d['scope_ambiguous_starts']));d['operative'].update(d['scope_ambiguous_starts']);d['never_controls']=sorted(set(d['never_controls'])-set(d['scope_ambiguous_starts']));routed='primary'
    return d,routed


def declaration_from_card_support(card: dict, summary: dict) -> dict:
    """Adapt an annual variant card and its metadata support for numerical S6."""
    from .engine.design import declaration_from_support
    if card['analysis'].get('support_adapter')!='annual_full_year_v1':
        raise ValueError('Card does not register annual full-year support')
    variant=card['analysis']['full_year_variant']
    if summary['variant']!=variant or summary['key']!=card['design_id']:
        raise ValueError('Annual card/support design or variant mismatch')
    from .engine.panels import transition_year_drop_enabled
    enabled=transition_year_drop_enabled(card)
    if transition_year_drop_enabled(summary)!=enabled:
        raise ValueError('Registered transition-year drop differs from metadata summary')
    source=dict(card['treatment']['full_year_declaration'])
    from .support import minimum_post_periods
    if 'min_post_periods' in card['analysis']:
        source['min_post_periods']=minimum_post_periods(card['analysis'])
    if enabled: source['transition_year_drop']=True
    return declaration_from_support(source,summary,
                                    key=card['design_id'],variant=variant)


def construct_full_year(declaration: dict, variant: str, metadata_rows: list[dict], source_checks: dict) -> tuple[dict,set]:
    d=copy.deepcopy(declaration)
    years=d['period_years']
    if not years or any(type(y) is not int for y in years) or years!=list(range(years[0],years[-1]+1)) or d['window']!=[years[0],years[-1]]: raise ValueError('Annual full-year adapter requires a complete integer calendar axis')
    if any(type(g) is not int for g in d['operative'].values()): raise ValueError('Annual operative years must be known integers')
    if set(d['never_controls'])&set(d['operative']) or len(d['never_controls'])!=len(set(d['never_controls'])): raise ValueError('Annual never-control pool overlaps adoption or duplicates units')
    keys=[(r['unit'],int(r['year'])) for r in metadata_rows]
    if len(keys)!=len(set(keys)): raise ValueError('Duplicate annual source metadata key')
    if any(r['annual_usable'] not in {'0','1'} or r.get('linkage_certified','1') not in {'0','1'} or r.get('suppression_flag','0') not in {'0','1'} for r in metadata_rows): raise ValueError('Invalid annual source metadata flag')
    d,routed=effective_full_year_declaration(d,variant)
    if d['period_years']!=list(range(d['window'][0],d['window'][1]+1)):
        raise ValueError('Effective annual variant calendar differs from its window')
    summary,required=annual_full_year_support(d,routed,metadata_rows)
    from .engine.panels import transition_year_drop_enabled, transition_year_keys
    if transition_year_drop_enabled(d):
        summary.update(transition_year_drop=True,
            transition_years_dropped=[list(k) for k in sorted(transition_year_keys(d['operative'],True))
                if k[0] in set(summary['retained_treated']) and k[1] in summary['period_years']])
    summary['variant']=variant;summary['checks'].update(source_checks)
    if variant=='include_scope_ambiguous' and d['scope_ambiguous_starts']: summary['checks']['mechanism_scope_certified']=False
    summary['support_pass']=all(summary['checks'].values());summary['source_ready']=d['source_ready']
    return summary,required
