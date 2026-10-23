"""Registered annual DiD execution and complete, unscreened result inventory.

Only S6's once-only, approved callback calls the values loader. Metadata plans
and numerical execution share declarations; every registered row has a terminal
disposition, including unsupported alternatives and rows after an exception.
"""
from __future__ import annotations

import json
import math
import traceback
from pathlib import Path
from typing import Callable

from .cards import variant_card
from .execution import DeclaredStop
from .storage import local, sha
from .variants import registered_names, registered_cards, secondary_entries, secondary_name, secondary_support_reason


class RegisteredExecutionError(RuntimeError):
    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


def not_run_result(spec: dict, reason: str) -> dict:
    names=registered_names(spec)
    row=lambda:dict(status='NOT_RUN',result=None,reason=reason)
    support={r['variant']:r for r in spec.get('support',{}).get('variants',[])}
    def terminal(name):
        r=support.get(name)
        if name.startswith('secondary_') and r is not None and r.get('support_pass',r.get('passed')) is not True:
            return dict(status='UNSUPPORTED',result=None,reason=secondary_support_reason(r),support=r)
        return row()
    return dict(schema='l2_registered_result_v1',design_id=spec['design_id'],version=spec['version'],
        status='NOT_RUN',reason=reason,registered_results={n:terminal(n) for n in names if not n.startswith('omit_')},
        omissions={n:row() for n in names if n.startswith('omit_')},actual_data_estimation_calls=0,
        forecast_comparison=dict(status='NOT_ASSESSABLE',rows=[]))


def _index(records: list[dict], expected: list[str], label: str) -> dict:
    result = {r['variant']: r for r in records}
    if len(result) != len(records) or set(result) != set(expected):
        raise ValueError(label+' registered variant inventory changed')
    return result


def numerical_declaration(card: dict, summary: dict, treatment: dict, name: str) -> dict:
    """Card-declared numerical adapter; R3.1 (gate_relax) adds `ri_feasibility_floor_rule` only when the card carries it."""
    from . import gate_relax
    if 'cohort_censor_rule' in card.get('analysis', {}):
        from .cohort_censor_rule import check_card   # R-S3C1 whitelist and floors (T246)
        check_card(card)
    declaration = _numerical_declaration_base(card, summary, treatment, name)
    if gate_relax.ri_floor(card.get('design_id'), card['analysis']) is None:
        return declaration
    return dict(declaration, ri_feasibility_floor_rule=card['analysis']['ri_feasibility_floor'],
                ri_feasibility_floor_design=card['design_id'])


def _numerical_declaration_base(card: dict, summary: dict, treatment: dict, name: str) -> dict:
    if card['analysis'].get('support_adapter') == 'annual_full_year_v1':
        from .full_year import declaration_from_card_support
        return declaration_from_card_support(card, summary)
    # The standardized annual state-panel track uses its registered integers.
    # Dedicated nonannual/RDD/RCT adapters remain explicit preparation stops.
    from .stages.s3_support import variant_treatment
    records = variant_treatment(treatment['records'], card)
    cfg = card['analysis']
    if card['track']=='DiD' and card['outcome'].get('frequency')=='biennial':
        from .biennial import declaration,ADAPTER,adapter
        if cfg.get('support_adapter',ADAPTER)!=adapter(card['outcome']):raise ValueError('Biennial numerical adapter must be registered')
        return declaration(records,cfg,card['outcome'],summary,key=card['design_id'],variant=name,
                           transition_year_drop=card.get('transition_year_drop',False))
    if card['track'] != 'DiD' or card['outcome'].get('frequency', 'annual') != 'annual':
        raise ValueError('No certified numerical adapter for this track/frequency')
    if cfg.get('controls', 'never_treated') not in {'never_treated','not_yet_treated'}:
        raise ValueError('Standardized annual adapter requires never-treated controls')
    from .support import declared_annual_periods, standard_annual_declaration
    return standard_annual_declaration(records,cfg,declared_annual_periods(card['outcome']),
        key=card['design_id'],variant=name,summary=summary,
        transition_year_drop=card.get('transition_year_drop',False))


def registered_panel_keys(declaration: dict, metadata_path: Path) -> set[tuple[str, int]]:
    """Shared S3/calibration/S6 mask; only metadata flags and declaration read."""
    from .engine.design import structural_rows
    from .engine.panels import metadata_eligible_keys
    eligible=metadata_eligible_keys(metadata_path)
    # structural_rows may normalize calendar indices. Return original labels.
    retained=structural_rows(declaration,[(u,t,0.,None) for u,t in sorted(eligible)])
    calendar=declaration.get('period_years')
    keys={(u,calendar[t] if calendar else t) for u,t,_,_ in retained}
    mask=declaration.get('summary',{}).get('analysis_mask')
    if mask is not None and keys!={tuple(k) for k in mask}:
        raise DeclaredStop('Registered metadata eligibility/analysis mask differs')
    return keys


def validate_calibration(record: dict, design: dict | None = None) -> dict:
    """Recompute registered held-out diagnostic gates rather than trust passed."""
    from .engine.estimator import CHECKS, SEED, RI_SEED
    from .engine.simulation import COUNTS, OFFSETS
    from .engine.ri import pre_family, require_feasible
    from .engine.inference import design_binding
    if record.get('counts') != COUNTS or record.get('seeds') != dict(base=SEED, offsets=OFFSETS, RI=RI_SEED):
        raise ValueError('Registered calibration counts/seeds changed')
    if record.get('training_tail_probability') != .01 or record.get('targets_met') is not True or record.get('status') != 'ready':
        raise ValueError('Registered calibration not ready')
    binding=record.get('design_binding',{})
    if set(binding)!={'design_sha256','operator_sha256'} or any(not isinstance(v,str) or len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in binding.values()):
        raise ValueError('Registered calibration design/operator binding missing')
    if design is not None and binding!=design_binding(design):
        raise DeclaredStop('Calibrated design/operator differs from registered design')
    critical = record.get('critical_values', {})
    if set(critical) != set(CHECKS) or any(type(v) not in (int,float) or not math.isfinite(v) for v in critical.values()):
        raise ValueError('Registered calibration critical values incomplete')
    feasibility = record['ri_feasibility']
    require_feasible(feasibility)
    feasible=feasibility.get('feasible_draws');failed=feasibility.get('infeasible_count')
    if feasibility.get('ri_floor_version')=='R3.1':
        # R3.1 record (gate_relax): exact 0.80 floor on the 199 draws; the card-level rule is checked in the declaration.
        from .gate_relax import required_draws, checked_floor
        checked_floor(feasibility.get('minimum_feasible_fraction'))
        floor_ok = type(feasible) is int and feasible >= required_draws(199, .8)
    else:
        floor_ok = type(feasible) is int and 10*feasible>=9*199
    if (type(feasibility.get('requested_draws')) is not int or feasibility['requested_draws']!=199
            or type(feasible) is not int or type(failed) is not int or not 0<=failed<=199
            or feasible!=199-failed or not floor_ok or feasibility.get('redrawn')!=0
            or feasibility.get('support_pass') is not True
            or feasibility.get('feasible_fraction')!=feasible/199
            or len(feasibility.get('infeasible_draws',[]))!=failed):
        raise ValueError('Registered calibration RI feasibility/count changed')
    clean = record['heldout']['clean']
    n = clean['repetitions']; count = clean['full_blocking_set_rejected']
    if type(n) is not int or n != 100 or type(count) is not int or not 0 <= count <= 20 or clean['full_blocking_set_rejection_rate'] != count/n:
        raise ValueError('Registered held-out clean passage failed')
    if set(clean['per_check']) != set(CHECKS):
        raise ValueError('Registered held-out check inventory changed')
    for check in CHECKS:
        r = clean['per_check'][check]; rejected = r['rejected']
        if type(rejected) is not int or not 0 <= rejected <= 5 or r['rejection_rate'] != rejected/n:
            raise ValueError('Registered held-out false rejection failed: '+check)
    if design is not None and record['tested_pre_event_times'] != pre_family(design):
        raise DeclaredStop('Calibrated lead inventory differs from registered design')
    return critical


def interval_plan(root: Path, manifest: dict, design_id: str) -> tuple[dict, Path]:
    plans = [p for p,v in manifest['files'].items() if v['role']=='interval_plan']
    if len(plans) != 1:
        raise ValueError('Exactly one registered interval plan required')
    plan = json.loads(local(root, plans[0]).read_text())
    from .engine.inference import METHODS, validate_certification
    expected = plan.get('expected_designs')
    if (plan.get('schema') != 'l2_interval_plan_v1' or plan.get('candidate_order') != list(METHODS)
            or not isinstance(expected, list) or not expected or len(set(expected))!=len(expected)
            or design_id not in expected):
        raise ValueError('Registered interval plan identity/order missing')
    path = local(root, plan['certification_path'])
    if plan['certification_path'] not in manifest['files'] or sha(path)!=manifest['files'][plan['certification_path']]['sha256']:
        raise ValueError('Interval certification is not frozen')
    validate_certification(json.loads(path.read_text()), expected_designs=expected, design_id=design_id)
    return plan, path


def load_registration(root: Path, card: dict, *, _draft_manifest=None) -> dict:
    from .outcome_transform import export_fields
    """Called inside the reservation; bind all inputs before outcome decoding."""
    from .engine.design import build_design
    from .engine.panels import PanelBinding, audit_binding, projected_keys
    from .engine.runner import registered_multiplier_count
    manifest = _draft_manifest if _draft_manifest is not None else json.loads((root/'FREEZE_FINAL.json').read_text())
    def bound_path(relative):
        path = local(root, relative)
        if relative not in manifest['files'] or sha(path) != manifest['files'][relative]['sha256']:
            raise ValueError('Execution dependency absent/changed in final freeze: '+relative)
        return path
    def read(relative): return json.loads(bound_path(relative).read_text())
    spec=read('SPEC.json')
    from .c64 import SPEC_NAME
    l3_registration = read(SPEC_NAME) if SPEC_NAME in manifest['files'] else None
    from .c70_l3 import SPEC_NAME as AMENDMENT_SPEC, merge_registration
    if l3_registration is not None:
        l3_registration=merge_registration(l3_registration,read(AMENDMENT_SPEC) if AMENDMENT_SPEC in manifest['files'] else None)
    identity = spec.get('card_sha256') in (None, sha(Path(card['_card_path'])))
    if not identity and 'REGISTRATION.json' in manifest['files']:
        registration = read('REGISTRATION.json')
        retained = [n for n, r in manifest['files'].items() if r['role'] == 'card']
        identity = (len(retained) == 1 and Path(card['_card_path']).resolve() == local(root, retained[0]).resolve()
                    and registration.get('card_sha256') == spec.get('card_sha256'))
    if spec.get('design_id')!=card['design_id'] or not identity:
        raise ValueError('Scientific execution identity changed')
    if spec.get('interval_path')!=['Webb','CT_complete_control_path','point_plus_RI_if_uncertified']:
        raise ValueError('Registered interval path differs from Webb then CT then point+RI')
    expected=registered_names(spec)
    from .hygiene import inventory_names
    support_name,panel_name=inventory_names(root)
    support_records=read(support_name)['variants']
    panel_records=read(panel_name)['variants']
    # Earlier prospective preparations stored secondary panels separately.
    # Consume that already frozen inventory without rewriting SPEC or forecasts.
    if secondary_entries(spec) and not all(secondary_name(s) in {p['variant'] for p in panel_records} for s in secondary_entries(spec)):
        secondaries=read('SECONDARY_PANELS.json')['secondaries']
        index={r['outcome']:r for r in secondaries}
        if len(index)!=len(secondaries) or set(index)!={s['outcome'] for s in secondary_entries(spec)}:
            raise ValueError('Secondary panel registered variant inventory changed')
        for entry in secondary_entries(spec):
            name=secondary_name(entry); record=index[entry['outcome']]
            if record.get('role')!='secondary': raise ValueError('Secondary panel role changed')
            p=record['construction'];prefix='secondary_panels/'+entry['outcome']+'/'
            summary=read(prefix+'SUPPORT.json')
            if sha(bound_path(prefix+'SUPPORT.json'))!=record['support_sha256'] or summary.get('support_pass',summary.get('passed'))!=record['support_passed']:
                raise ValueError('Secondary support binding changed: '+name)
            panel_records.append(dict(p,variant=name,panel_relative=prefix+'panel.csv',metadata_relative=prefix+'metadata.csv'))
            support_records.append(dict(summary,variant=name))
    support=_index(support_records,expected,'Support')
    panels=_index(panel_records,expected,'Panel')
    treatment=read('TREATMENT.json')
    if l3_registration is not None:
        from .c64_metadata import geography
        l3_registration['geography'].update(geography(spec,treatment))
        from .c83_l3 import SPEC_NAME as C83_SPEC,overlay
        if C83_SPEC in manifest['files']:
            l3_registration=overlay(l3_registration,read(C83_SPEC))
    scientific_fields=('design_id','version','track','analysis','treatment','sensitivities','outcome')
    if any(spec[k]!=card[k] for k in scientific_fields):
        raise ValueError('Card differs from registered scientific specification')
    if secondary_entries(spec)!=secondary_entries(card):
        raise ValueError('Card differs from registered secondary specification')
    from .engine.panels import transition_year_drop_enabled
    if transition_year_drop_enabled(spec)!=transition_year_drop_enabled(card):
        raise ValueError('Card transition-year drop differs from registered scientific specification')
    scientific_card={k:v for k,v in card.items() if k!='_card_path'}
    cards=registered_cards(scientific_card)
    multiplier_count=registered_multiplier_count(spec)
    if any(registered_multiplier_count(c)!=multiplier_count for c in cards.values()):
        raise ValueError('Registered variants must inherit the same multiplier count')
    calibration_record=None
    if _draft_manifest is None:
        plan,certification=interval_plan(root,manifest,card['design_id'])
        gates=read('CALIBRATION_GATES.json')
        if gates.get('passed') is not True or set(gates.get('cases',{}))!=set(plan['expected_designs']):
            raise ValueError('Registered diagnostic calibration inventory incomplete')
        calibration_record=gates['cases'][card['design_id']]
    bindings={};declarations={}
    for name in expected:
        p=panels[name]
        if name.startswith('secondary_') and p.get('state')=='UNSUPPORTED':
            if support[name].get('support_pass',support[name].get('passed')) is not False or p.get('reason')!=support[name].get('reason'):
                raise ValueError('Unsupported secondary disposition differs: '+name)
            continue
        from .outcome_transform import transform as _transform
        if name in cards and (p.get('construction') or {}).get('outcome_transform','level')!=_transform(cards[name]):
            raise ValueError('Panel outcome transform differs from the registered card: '+name)
        panel=bound_path(p.get('panel_relative','panels/'+name+'/panel.csv')); metadata=bound_path(p.get('metadata_relative','panels/'+name+'/metadata.csv'))
        if name.startswith('secondary_'):
            from .engine.panels import projected_keys, metadata_eligible_keys
            from .stages.s3_support import variant_treatment
            from .support import support_check, declared_annual_periods
            # Independently regenerate the secondary's support from its own
            # metadata. Source construction must bind its own measurement.
            c=cards[name]
            if p.get('connector')!=c['outcome']['connector'] or p.get('population_filters')!=c['outcome']['population_filters']:
                raise ValueError('Secondary measurement binding differs: '+name)
            if (c['analysis'].get('support_adapter')!='annual_full_year_v1' and c['outcome'].get('frequency','annual')=='annual'
                    and c['analysis'].get('controls','never_treated') in {'never_treated','not_yet_treated'}):
                regenerated=support_check(metadata_eligible_keys(metadata),variant_treatment(treatment['records'],c),c['analysis'],
                    period_years=declared_annual_periods(c['outcome']),transition_year_drop=c.get('transition_year_drop',False))
                if any(support[name].get(k)!=regenerated.get(k) for k in ('support_pass','cell_inventory','analysis_mask','support_gap_excluded_units')):
                    raise ValueError('Secondary metadata support differs: '+name)
            if support[name].get('support_pass',support[name].get('passed')) is not True:
                bindings[name]=PanelBinding(panel,panel,metadata,p['panel_sha256'],p['panel_sha256'],p['metadata_sha256'])
                audit_binding(bindings[name])
                continue
        if (support[name].get('report_only') is True and support[name].get('gates_primary') is False
                and support[name].get('support_pass',support[name].get('passed')) is not True):
            bindings[name]=PanelBinding(panel,panel,metadata,p['panel_sha256'],p['panel_sha256'],p['metadata_sha256'])
            audit_binding(bindings[name])
            continue
        declarations[name]=numerical_declaration(cards[name],support[name],treatment,name)
        required=registered_panel_keys(declarations[name],metadata)
        bindings[name]=PanelBinding(panel,panel,metadata,p['panel_sha256'],p['panel_sha256'],p['metadata_sha256'],frozenset(required))
        audit_binding(bindings[name])
        if support[name].get('support_pass',support[name].get('passed')) is True:
            design=build_design(declarations[name],[(u,t,0.,None) for u,t in sorted(required)])
            if 'cell_inventory' not in support[name] or design['cells']!=support[name]['cell_inventory']:
                raise DeclaredStop('Numerical/registered cell inventory differs: '+name)
            if name=='primary' and _draft_manifest is None:
                validate_calibration(calibration_record,design)
                from .engine.inference import validate_certification
                validate_certification(read(plan['certification_path']),expected_designs=plan['expected_designs'],design_id=card['design_id'],design=design)
    if _draft_manifest is not None:
        return dict(l3_export_v2=l3_registration, expected=expected,support=support,declarations=declarations,bindings=bindings,fitting_disabled=True)
    lock=read('PREDICTIONS_LOCK.json');forecasts={}
    if lock['design_id']!=card['design_id'] or lock['spec_sha256']!=sha(bound_path('SPEC.json')):
        raise ValueError('Forecast execution binding changed')
    annotation={}
    if 'FORECAST_SET_ANNOTATION.json' in manifest['files']:
        annotation=read('FORECAST_SET_ANNOTATION.json')
        if (annotation.get('design_id')!=card['design_id'] or annotation.get('forecast_lock',{}).get('sha256')!=sha(bound_path('PREDICTIONS_LOCK.json'))
                or annotation.get('stale_contract_card') is not True or not isinstance(annotation.get('differing_fields'),dict)):
            raise ValueError('Forecast set annotation binding changed')
    author_annotation=read('FORECAST_AUTHOR_ANNOTATION.json') if 'FORECAST_AUTHOR_ANNOTATION.json' in manifest['files'] else {}
    if author_annotation and author_annotation.get('forecast_lock',{}).get('sha256')!=sha(bound_path('PREDICTIONS_LOCK.json')):raise ValueError('Forecast author annotation binding changed')
    for persona in lock['personas']:
        relative='predictions/'+persona+'/submission.json'; value=read(relative)
        if sha(bound_path(relative))!=lock['files'][relative]: raise ValueError('Locked forecast changed')
        collection_relative='predictions/'+persona+'/COLLECTION.json'
        if collection_relative in lock.get('original_files',{}):
            collection=read(collection_relative)
            from .forecast_snapshot import original
            value=dict(value,original=original(root,lock,persona,collection,manifest.get('forecast_snapshot')))
        if annotation:
            value=dict(value,stale_contract_card=True,stale_contract_differing_fields=annotation['differing_fields'])
        if author_annotation:value=dict(value,shared_author_personas=author_annotation['shared_author_personas'])
        forecasts[persona]=value
    return dict(l3_export_v2=l3_registration,expected=expected,support=support,declarations=declarations,bindings=bindings,
        multiplier_count=multiplier_count,
        calibration=calibration_record,forecasts=forecasts,interval_evaluation_path=certification,
        interval_expected_designs=plan['expected_designs'],design_id=card['design_id'],
        version=card['version'],spec_sha256=sha(bound_path('SPEC.json')),manifest_sha256=sha(root/'FREEZE_FINAL.json'),
        **export_fields(card,(panels.get('primary',{}).get('construction') or {}).get('source_unit_before_log')))


def forecast_comparison(forecasts: dict, att: float | None, diagnostics_passed: bool | None) -> dict:
    """Score every locked ledger on the same realized primary, in original units."""
    rows=[]
    for persona,p in forecasts.items():
        row=dict(persona=persona,point=p['point'],interval=p['interval'],interval_level=p['interval_level'],
            original_sha256=p.get('original_sha256'),status='NOT_ASSESSABLE' if att is None else 'SCORED')
        if 'shared_author_personas' in p:row['shared_author_personas']=p['shared_author_personas']
        if p.get('stale_contract_card') is True:
            row.update(stale_contract_card=True,stale_contract_differing_fields=p['stale_contract_differing_fields'])
        if att is not None:
            lo,hi=p['interval'];alpha=1-p['interval_level'];error=p['point']-att
            row.update(realized_ATT=att,error=error,absolute_error=abs(error),squared_error=error*error,
                interval_contains_ATT=lo<=att<=hi,interval_score=hi-lo+2/alpha*(max(lo-att,0)+max(att-hi,0)))
            original=p.get('original',{})
            if all(k in original for k in ('near_zero_threshold','prob_positive','prob_negative','prob_near_zero')):
                threshold=original['near_zero_threshold']
                direction='positive' if att>threshold else 'negative' if att < -threshold else 'near_zero'
                row.update(realized_direction=direction,direction_correct=original['direction']==direction,
                    direction_brier=sum((original['prob_'+d]-int(direction==d))**2 for d in ('positive','negative','near_zero')))
            if diagnostics_passed is not None and 'prob_diagnostics_pass' in original:
                row['diagnostics_brier']=(original['prob_diagnostics_pass']-int(diagnostics_passed))**2
        rows.append(row)
    return dict(status='NOT_ASSESSABLE' if att is None else 'SCORED',rows=rows,
        estimand='registered primary overall ATT',selection_affected_by_scores=False)


def run_registered(bundle: dict, *, load_panel: Callable | None = None) -> dict:
    if bundle.get('fitting_disabled'):raise ValueError('Draft DRY-LOAD bundle cannot execute')
    from .engine.runner import fit_primary, registered_multiplier_count
    from .engine.panels import load_bound_panel
    from .engine import ri, noise
    load_panel=load_bound_panel if load_panel is None else load_panel
    multiplier_count=registered_multiplier_count(dict(analysis={
        'multiplier_count':bundle.get('multiplier_count',registered_multiplier_count({}))}))
    names=bundle['expected']; expected=['primary']+[n for n in names if n!='primary']
    record=dict(schema='l2_registered_result_v1',design_id=bundle['design_id'],version=bundle['version'],
        status='COMPLETED',spec_sha256=bundle['spec_sha256'],manifest_sha256=bundle['manifest_sha256'],
        registered_results={},omissions={},gate_table=[],stop_issues=[],actual_data_estimation_calls=0,
        expected_registry=[n for n in names if not n.startswith('omit_')],expected_omissions=[n for n in names if n.startswith('omit_')])
    record.update({k:bundle[k] for k in ('outcome_transform','units','source_unit_before_log') if k in bundle})
    def bucket(name):return record['omissions'] if name.startswith('omit_') else record['registered_results']
    def invoked():record['actual_data_estimation_calls']+=1
    def complete_remaining(reason):
        for name in expected:
            support=bundle['support'][name]
            if name.startswith('secondary_') and support.get('support_pass',support.get('passed')) is not True:
                bucket(name).setdefault(name,dict(status='UNSUPPORTED',result=None,reason=secondary_support_reason(support),support=support))
            else:
                bucket(name).setdefault(name,dict(status='NOT_RUN',result=None,reason=reason))
    primary_att=None;diagnostics_passed=None
    try:
        for name in expected:
            support=bundle['support'][name]
            if support.get('support_pass',support.get('passed')) is not True:
                reason=secondary_support_reason(support) if name.startswith('secondary_') else 'Registered metadata support failed'
                bucket(name)[name]=dict(status='UNSUPPORTED',result=None,reason=reason,support=support)
                if name=='primary': raise DeclaredStop('Registered primary support failed')
                continue
            declaration=bundle['declarations'][name]
            if name=='primary' and bundle.get('l3_export_v2'):
                geo=bundle['l3_export_v2']['geography']
                declaration=dict(declaration,l3_universe=geo.get('U') if geo.get('U') is not None else [])
            rows,identity=load_panel(bundle['bindings'][name])
            row,design,outcomes=fit_primary(declaration,rows,B=multiplier_count,
                interval_evaluation_path=bundle['interval_evaluation_path'] if name=='primary' else None,
                interval_expected_designs=bundle['interval_expected_designs'],on_estimate=invoked)
            row['inventory']=identity;bucket(name)[name]=row
            if name=='primary':
                primary_att=row['result']['overall']['att']
                if bundle.get('l3_export_v2'):
                    from .l3_addendum import observed_fields
                    direction=bundle['l3_export_v2'].get('first_order',{}).get('purpose_direction')
                    record['l3_export_v2']=observed_fields(declaration,design,outcomes,direction)
                    record['l3_export_v2']['geography']=bundle['l3_export_v2']['geography']
                    from .l3_addendum import amendment_fields
                    record['l3_export_v2'].update(amendment_fields(declaration,design,outcomes,bundle['l3_export_v2'],on_estimate=invoked))
            if not name.startswith('omit_'):
                try:
                    reference=ri.permutation_reference(declaration,design,outcomes,count=999, **({'l3_export':dict(purpose_direction=direction,amendment_v2=bundle['l3_export_v2'].get('amendment_v2'))} if name=='primary' and bundle.get('l3_export_v2') else {}))
                    if name=='primary' and bundle.get('l3_export_v2'):
                        if 'placebo_group' in reference:
                            record['l3_export_v2']['placebo_group']=reference['placebo_group']
                            record['l3_export_v2']['amendment_field_status']['F11']='AVAILABLE'
                        record['l3_export_v2'].update(placebo_profiles=reference['placebo_profiles'],placebo_labels=reference['placebo_labels'],feasibility=reference['feasibility'],draw_sha256=reference['draw_sha256'])
                    # The blocking diagnostic must use the same fixed RI count
                    # as its registered calibration. Noise retains its separate
                    # 999-draw reference; neither count is tuned to outcomes.
                    diagnostic_reference = (ri.permutation_reference(declaration,design,outcomes,
                        count=bundle['calibration']['counts']['RI']) if name=='primary' else reference)
                    diagnostic=ri.diagnostics(design,outcomes,row['result'],diagnostic_reference,B=multiplier_count,on_estimate=invoked)
                    row['diagnostics']=diagnostic;row['noise_floor']=noise.noise_summary(reference,row['result']['overall']['att'])
                except ValueError as exc:
                    if str(exc).startswith('NOT_ASSESSABLE:'):
                        if name=='primary': raise DeclaredStop(str(exc)) from exc
                        row['diagnostics']=dict(status='NOT_ASSESSABLE',reason=str(exc))
                        row['noise_floor']=dict(status='NOT_ASSESSABLE',reason=str(exc))
                    else: raise
                if name=='primary':
                    critical=validate_calibration(bundle['calibration'],design)
                    record['gate_table']=ri.gate_checks(diagnostic['statistics'],critical)
                    diagnostics_passed=all(g['passed'] for g in record['gate_table'])
                    record['diagnostics']=diagnostic;record['noise_floor']=row['noise_floor']
                elif 'statistics' in row['diagnostics']:row['diagnostics']['reported_only']=True
        points=[r['result']['overall']['att'] for r in record['omissions'].values() if r['status']=='ESTIMATED']
        record['omission_range']=[min(points),max(points)] if points else None
        record['omission_maximum_absolute_change']=max((abs(p-primary_att) for p in points),default=None)
        if diagnostics_passed is False:
            record.update(status='STOPPED',stop_class='STATISTICAL_CALIBRATION',reason='Registered diagnostic gate failed')
            record['stop_issues']=[g['gate'] for g in record['gate_table'] if not g['passed']]
    except DeclaredStop as exc:
        record.update(status='STOPPED',stop_class='STRUCTURAL_OR_INTEGRITY',reason=str(exc))
        record['stop_issues'].append(str(exc))
        bucket(name).setdefault(name,dict(status='STOPPED',result=None,reason=str(exc)))
        complete_remaining(str(exc))
    except Exception as exc:
        record.update(status='FAILED_NO_RETRY',reason=str(exc),execution_error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()))
        if name not in bucket(name): bucket(name)[name]=dict(status='FAILED_NO_RETRY',result=None,reason=str(exc),execution_error=record['execution_error'])
        complete_remaining('Execution aborted after exception: '+str(exc))
        record['forecast_comparison']=forecast_comparison(bundle['forecasts'],primary_att,diagnostics_passed)
        raise RegisteredExecutionError(str(exc),record) from exc
    record['forecast_comparison']=forecast_comparison(bundle['forecasts'],primary_att,diagnostics_passed)
    return record
