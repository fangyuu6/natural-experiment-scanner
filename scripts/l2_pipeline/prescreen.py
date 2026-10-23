"""C62 cheap, outcome-blind screening; never certifies a legal history.

An inconclusive check proceeds to research. Only all-variant failures park.
The backtest uses initial cards, never audited adoption lists or outcome values.
"""
from copy import deepcopy
from pathlib import Path
import csv,json
from .storage import public_input,sha,ensure_once
REPO=Path(__file__).resolve().parents[2]


def catalog_spec(outcome,repo=REPO):
    path=outcome.get('catalog_path')
    if not path:return None
    path=public_input(repo/path)
    if sha(path)!=outcome.get('catalog_sha256'):raise ValueError('Catalog binding changed')
    return json.loads(path.read_text())['series'].get(outcome.get('series_id'))


def bind_outcome(outcome,repo=REPO):
    """Resolve missing paths against the pinned catalog, never CURRENT."""
    value=deepcopy(outcome)
    spec=catalog_spec(value,repo)
    if not spec:return value
    paths=spec.get('paths',[]);hashes=spec.get('source_sha256',{})
    if not paths and spec.get('exports'):
        paths=[e['raw_path'] for e in spec['exports']]
        hashes={e['raw_path']:e['sha256'] for e in spec['exports']}
    if not paths and spec.get('local_source_path'):
        source=public_input(repo/spec['local_source_path']);receipt=source.with_name(source.name+'.receipt.json')
        if source.is_file() and receipt.is_file():
            proof=json.loads(receipt.read_text())
            if proof.get('sha256')!=sha(source) or proof.get('size_bytes')!=source.stat().st_size:
                raise ValueError('Catalog local-source receipt changed')
            paths=[spec['local_source_path']];hashes={paths[0]:proof['sha256']}
    if not paths and spec.get('artifact'):
        paths=[spec['artifact']['path']];hashes={paths[0]:spec['artifact']['sha256']}
    if value.get('paths'):
        existing=value['paths'];existing=[existing] if isinstance(existing,str) else existing
        # Only exact pinned catalog names may be rebased from a card directory.
        if existing!=paths:return value
        if value.get('source_sha256')!=hashes:raise ValueError('Catalog source binding differs')
    if paths:
        value['paths']=[str(public_input(repo/p)) for p in paths]
        value['source_sha256']={str(public_input(repo/p)):hashes[p] for p in paths}
    return value


def flags(spec,repo=REPO):
    pointer=spec['support_artifact'];path=public_input(repo/pointer['path'])
    if sha(path)!=pointer['sha256']:raise ValueError('Support metadata changed')
    data=json.loads(path.read_text())
    from .connectors.support_schema import SUPPORT_FIELDS
    if data.get('outcome_values_in_sidecar') is not False or data['series_id']!=spec['series_id']:
        raise ValueError('Support metadata identity/value boundary differs')
    rows=data['metadata']
    if any(set(r)-SUPPORT_FIELDS for r in rows):raise ValueError('Non-metadata fields in sidecar')
    keys=[(r['unit'],r['year']) for r in rows]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate support metadata')
    return {k for k,r in zip(keys,rows) if all(str(r.get(f,1)).lower() in {'1','true'} for f in ('available','annual_usable','linkage_certified')) and all(str(r.get(f,0)).lower() in {'0','false',''} for f in ('suppressed','unreliable','suppression_flag'))}


def sidecar_units(spec,repo=REPO):
    """Every unit of the value-free support sidecar (flags() has already validated its binding)."""
    path=public_input(repo/spec['support_artifact']['path'])
    return {r['unit'] for r in json.loads(path.read_text())['metadata']}


def records(card,shift):
    screen=card['treatment'].get('screening_adoption',{})
    adopters=screen.get('adopters',{})
    never=screen.get('never_through_coverage_end') or []
    if not adopters:
        legacy=card['treatment'].get('screening_adoption_list_not_for_estimation',{})
        adopters=legacy.get('adoptions',{})
        never=card['treatment'].get('screening_support',{}).get('never_treated_states') or []
    if not adopters and card['treatment'].get('source_type')=='research_panel':
        # Reuse the outcome-free, hash-locked source import. Never read an
        # audited TREATMENT file or infer unknown source years as controls.
        from .sources.research_panel.s1 import load_inputs
        _,imported,_=load_inputs(card['treatment'])
        if imported is None:return None
        return [dict(r,start_period=r['start_period']+shift if r['start_period'] is not None else None)
                for r in imported['records'] if r['status'] in {'treated','never_treated'} and not r.get('primary_excluded')]
    if not adopters and card['analysis'].get('support_adapter')=='annual_full_year_v1':
        declaration=card['treatment'].get('full_year_declaration',{})
        adopters=declaration.get('operative',{})
        never=declaration.get('never_controls',[])
    if not adopters:return None
    if not all(type(y) is int for y in adopters.values()):return None
    # Unknown screening entries are excluded from both arms, never filled as zero.
    excluded=set(card['treatment'].get('excluded_units',[]))
    censor=card['treatment'].get('censor_after_period',{})
    universe=card['treatment'].get('universe',sorted(set(adopters)|set(never)))
    return [dict(unit=u,status='treated' if u in adopters else 'never_treated',
        start_period=adopters[u]+shift if u in adopters else None,
        **({'censor_after_period':censor[u]} if u in censor else {}))
        for u in universe if u not in excluded and (u in adopters or u in never)]


def certified_records(card,reuse):
    """The certified source design's TREATMENT.json records (shift 0, no year tolerance) minus the card's own exclusions."""
    gone=set(card['treatment'].get('excluded_units',[]))
    return [dict(r) for r in reuse['records'] if r['unit'] not in gone]


def evaluate(card,base_dir,*,excluded_units=(),repo=REPO,certified=None,roster=None,shifts=(0,-1,1),pool_rule=True):
    from .connectors.common import ConnectorRequest
    from .population_resolution import choose
    from .support import support_check,declared_annual_periods
    from .variants import registered_cards
    from .stages.s3_support import variant_treatment
    from .registered import numerical_declaration
    from .engine.design import build_design,structural_rows
    from .hub import ri_geometry_feasibility
    result=dict(schema='l2_prescreen_c62_v1',design_id=card['design_id'],status='PROCEED',
        processed=False,counting_rule='C63: PRESCREEN_PARK excluded from L2 100; reported separately',variants=[],reasons=[],
        outcome_values_read=0,actual_data_estimation_calls=0,adoption_basis='screening_only',year_tolerance=[-1,0,1])
    if card.get('track')!='DiD':
        result.update(status='PRESCREEN_PARK',reasons=['S3_TRACK: Track-specific support adapter pending']);return result
    if card.get('unit','state')!='state':
        result.update(status='PRESCREEN_PARK',reasons=['S2_PATH: Non-state panel adapter pending']);return result
    card=deepcopy(card)
    try:
        card['outcome']=bind_outcome(card['outcome'],repo)
        ConnectorRequest.from_card(card,base_dir).paths()
    except (ValueError,OSError) as exc:
        result.update(status='PRESCREEN_PARK',reasons=['S2_PATH: '+str(exc)]);return result
    first=card.get('first_order',{})
    try:
        resolution=choose(card,dict(status='VERIFIED',target_outcome=first.get('target_outcome',''),
            target_construct=first.get('population_resolution',{}).get('construct')))
        result['same_construct']=resolution['construct']
        if not resolution['selected']:
            result.update(status='PRESCREEN_PARK',reasons=['S0_CONSTRUCT: '+resolution['reason']]);return result
    except ValueError as exc:
        # A new interpretation is a purpose job, not proof of unavailable data.
        result['construct_inconclusive']=str(exc)
    # Intake S3 replay (YIELD_DIAGNOSTIC fix 7): a card that reuses a certified history is screened on that history's own
    # roster, with no year tolerance, instead of on the screening map.
    reuse=certified if certified and card['analysis'].get('support_adapter')!='annual_full_year_v1' else None
    if reuse:
        result.update(adoption_basis='certified_reused_history',year_tolerance=[0],source_design_id=reuse['source_design_id'])
        card['treatment']['excluded_units']=sorted(set(card['treatment'].get('excluded_units',[]))|set(reuse['excluded_units']))
    if reuse:
        # REDESIGN W3 (rules R3): the outcome-blind replay of S3 and of the card's structural list on the certified roster.
        # Not evaluable parks here like any other S3_SIBLING stop (Gate 0 records it at ingest); a card that still lacks a structural
        # unit is refused by the ingest hook, so it never reaches this point.
        try:
            from . import intake_replay,rules as rules_module
            rules=rules_module.of_payload(card,repo)
            if rules_module.at_least(rules,3):
                replayed=intake_replay.replay_certified(card,reuse,repo,rules)
                result['intake_replay']={k:replayed[k] for k in ('status','excluded','structural','failures','issues','figures')}
                if replayed['status']=='NOT_EVALUABLE':
                    result.update(status='PRESCREEN_PARK',reasons=[replayed['reason']]);return result
        except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
            result['intake_replay_inconclusive']=type(exc).__name__+': '+str(exc)
    def roster_of(variant,shift):
        if roster is not None:
            # REDESIGN screen: the A/B-reconciled roster, shifted like the screening map would be.
            gone=set(variant['treatment'].get('excluded_units',[]))
            return [dict(r,start_period=r['start_period']+shift) if shift and r.get('start_period') is not None else dict(r) for r in roster if r['unit'] not in gone]
        if reuse is None:return records(variant,shift)
        if shift:return None
        return certified_records(variant,reuse)
    try:screen_records=roster_of(card,0)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        result['inconclusive']='Screening source unavailable: '+str(exc);return result
    if screen_records is None:
        result['inconclusive']='No complete screening adoption map';return result
    cache={}
    for name,variant in registered_cards(card).items():
        if name.startswith('secondary_'):continue  # A secondary cannot rescue the primary estimand.
        for shift in ((0,) if reuse else tuple(shifts)):
            trial=dict(variant=name,year_shift=shift,passed=None)
            trial_card=variant
            try:
                spec=catalog_spec(variant['outcome'],repo)
                if not spec:spec={}
                if not spec.get('support_artifact') and variant['outcome'].get('support_path'):
                    spec=dict(spec,series_id=variant['outcome']['series_id'],support_artifact=dict(
                        path=variant['outcome']['support_path'],sha256=variant['outcome']['support_sha256']))
                optimistic=not spec.get('support_artifact')
                years=declared_annual_periods(variant['outcome'])
                # Without a sidecar only an optimistic complete rectangle can
                # prove insufficient S3 support. Passing cannot certify support
                # or RI and therefore remains inconclusive.
                keys={(u,y) for u in variant['treatment'].get('universe',[]) for y in years} if optimistic else flags(spec,repo)
                tr=roster_of(variant,shift)
                if tr is None:raise ValueError('Screening adoption missing')
                tr=variant_treatment(tr,variant)
                fresh=sorted(r['unit'] for r in tr if r['status']=='treated' and r['unit'] not in excluded_units)
                # YIELD_FIXES ruling (ii): a design that reuses a certified history runs no fresh audit, so it needs no fresh pool.
                reused=bool((card.get('automation') or {}).get('history_reuse'))
                trial['fresh_pool_n']=len(fresh);trial['fresh_pass']=len(fresh)>0 or reused or not pool_rule  # Owner D8: census of nonempty remainder; the REDESIGN screen runs before any audit and skips the pool rule
                cfg=variant['analysis'];years=declared_annual_periods(variant['outcome'])
                if variant['outcome'].get('frequency')=='biennial':
                    from .biennial import support
                    summary=support(keys,tr,cfg,variant['outcome'],variant.get('transition_year_drop',False))
                elif cfg.get('support_adapter')=='annual_full_year_v1':
                    from .full_year import construct_full_year
                    vc=deepcopy(variant);decl=vc['treatment']['full_year_declaration']
                    for field in ('operative','actual_clean_onset','initial_step_starts','scope_ambiguous_starts'):
                        if field in decl:decl[field]={u:y+shift for u,y in decl[field].items()}
                    decl['min_post_periods']=cfg.get('min_post_periods',2)
                    if vc.get('transition_year_drop'):decl['transition_year_drop']=True
                    summary,_=construct_full_year(decl,cfg['full_year_variant'],
                        [dict(unit=u,year=y,annual_usable='1',linkage_certified='1',suppression_flag='0') for u,y in sorted(keys)],{})
                    summary=dict(summary,passed=summary['support_pass'],reason='Annual full-year metadata support')
                    trial_card=vc
                else:summary=support_check(keys,tr,cfg,period_years=years,transition_year_drop=variant.get('transition_year_drop',False))
                trial.update(s3_pass=summary['passed'],s3_reason=summary['reason'],support_counts=summary.get('counts',{}))
                if optimistic and summary['passed'] and trial['fresh_pass']:
                    trial['inconclusive']='No certified value-free support sidecar; optimistic support can pass'
                    result['variants'].append(trial);continue
                trial['support_basis']='optimistic_complete_rectangle' if optimistic else 'certified_sidecar'
                trial['ri_pass']=False
                if summary['passed'] and trial['fresh_pass']:
                    # tr already carries variant shifts; avoid applying them twice.
                    vc=deepcopy(trial_card);vc['treatment'].pop('start_shift',None)
                    declaration=numerical_declaration(vc,summary,dict(records=tr),name)
                    design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(keys)])
                    import hashlib
                    key=hashlib.sha256(json.dumps(dict(declaration=declaration,mask=design['mask']),sort_keys=True,default=str).encode()).hexdigest()
                    if key not in cache:cache[key]=screen_ri(declaration,design)
                    ri=cache[key]
                    trial.update(ri_pass=ri['passed'],ri_checks=ri['checks'])
                trial['passed']=trial['fresh_pass'] and trial['s3_pass'] and trial['ri_pass']
                trial['reasons']=[k for k in ('fresh','s3','ri') if not trial[k+'_pass']]
            except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
                trial['inconclusive']=type(exc).__name__+': '+str(exc)
            result['variants'].append(trial)
            if trial['passed'] is True:
                result['remaining_variants']='Not required: a tolerated variant passes all checks'
                return result
    if result['variants'] and all(r['passed'] is False for r in result['variants']):
        reasons=sorted({x for v in result['variants'] for x in v['reasons']})
        if reuse and all('s3' in v['reasons'] for v in result['variants']):
            try:rescue=structural_rescue(card,reuse,repo)
            except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
                result['inconclusive']='Structural replay failed: '+type(exc).__name__+': '+str(exc);return result
            result['structural_rescue']=rescue
            if rescue['rescuable']:return result  # the Q1 rule would pass it: proceed; the card is not changed here
            reasons=['S3_SIBLING: '+'; '.join(rescue['why'])]+[x for x in reasons if x!='s3']
        result.update(status='PRESCREEN_PARK',reasons=reasons)
    return result


def structural_rescue(card,reuse,repo=REPO):
    """The NAMED_PANEL Q1 rule replayed on the certified roster at intake: no data value, no card change.

    {rescuable, why, excluded, figures}. `why` lists what still fails (the stored-S3 reproduction check of Q1 does not apply
    because no S3 has run); with an empty exclusion list the support stop does not come from the roster."""
    from .variants import registered_cards
    from .stages.s3_support import variant_treatment
    from .support import support_check,declared_annual_periods
    from . import named_panel
    primary=registered_cards(deepcopy(card))['primary']
    spec=catalog_spec(primary['outcome'],repo) or {}
    if not spec.get('support_artifact') and primary['outcome'].get('support_path'):
        spec=dict(spec,series_id=primary['outcome']['series_id'],support_artifact=dict(
            path=primary['outcome']['support_path'],sha256=primary['outcome']['support_sha256']))
    years=declared_annual_periods(primary['outcome'])
    if spec.get('support_artifact'):
        keys=flags(spec,repo);present=sidecar_units(spec,repo)
    else:
        present=set(primary['treatment'].get('universe',[]));keys={(u,y) for u in present for y in years}
    records=reuse['records']
    excluded,details=named_panel.structural_exclusions(card,records,sorted(present))
    out=dict(rescuable=False,why=[],excluded={u:excluded[u] for u in sorted(excluded)},figures=None,details=details)
    if not excluded:
        out['why'].append('no structural exclusion applies; the support stop does not come from the roster');return out
    second=deepcopy(card);second['treatment']['excluded_units']=sorted(set(second['treatment'].get('excluded_units',[]))|set(excluded))
    primary=registered_cards(second)['primary'];treatment=variant_treatment(records,primary)
    tyd=primary.get('transition_year_drop',False)
    if primary['outcome'].get('frequency')=='biennial':
        from .biennial import support as biennial_support
        replay=biennial_support(keys,treatment,primary['analysis'],primary['outcome'],tyd)
    else:replay=support_check(keys,treatment,primary['analysis'],period_years=years,transition_year_drop=tyd)
    out['figures'],failures=named_panel.check_floors(records,excluded,reuse['sample'],replay.get('comparison_controls',[]))
    out['why'].extend(failures)
    if not replay.get('passed'):
        out['why'].append('the unchanged S3 check still fails with the structural list: '+', '.join(sorted(replay.get('issues',{}))))
    out['rescuable']=not out['why']
    return out


def persist(pilot,row):
    from .autopilot import read
    path=pilot.case_dir(row)/'PRESCREEN.json'
    value=read(path)
    if value is None:
        from .history_reuse import certified_roster
        value=evaluate(read(row['card']),Path(row['card']).parent,
            excluded_units=row.get('fresh_policy',{}).get('excluded_units',[]),repo=pilot.repo,certified=certified_roster(pilot,row))
        value['card_sha256']=sha(row['card']);ensure_once(path,value)
        pilot.ledger(row,'prescreen',value['status'],'record',processed=False,reasons=value['reasons'],receipt=str(path))
    if value['card_sha256']!=sha(row['card']):raise ValueError('Prescreen card binding changed')
    return value


def backtest(output):
    from .autopilot import Autopilot,read
    pilot=Autopilot();active={r['design_id']:r for r in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs']}
    initial={}
    for line in (pilot.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines():
        v=json.loads(line)
        if v.get('action')=='ingest':initial.setdefault(v.get('design_id'),v)
    rows=[]
    with (REPO/'data/process_insights/design_table_20261011.csv').open() as stream:actual=list(csv.DictReader(stream))
    for a in actual:
        did=a['design'];r=active[did];path=Path(r['card'])
        # Find the earliest retained card through immutable requeue ancestry.
        while True:
            receipts=list((pilot.registry.parent/'autopilot/requeues'/did).glob('*/REQUEUE.json'))
            old=next((read(p)['prior_row']['card'] for p in receipts if read(p)['new_card_sha256']==sha(path)),None)
            if not old:break
            path=Path(old)
        checkpoint=Path(output).parent/'backtest_rows'/Path(output).stem/(did+'.json')
        value=read(checkpoint)
        if value is None:
            value=evaluate(read(path),path.parent,excluded_units=r.get('fresh_policy',{}).get('excluded_units',[]))
            ensure_once(checkpoint,value)
        rows.append(dict(design_id=did,card=str(path),card_sha256=sha(path),actual_class=a['final_class'],actual_state=a['final_state'],
            actual_park=a['final_state']=='PARK',**{k:v for k,v in value.items() if k!='design_id'}))
        print(did,value['status'],flush=True)
    tp=sum(r['status']=='PRESCREEN_PARK' and r['actual_park'] for r in rows)
    fp=sum(r['status']=='PRESCREEN_PARK' and not r['actual_park'] for r in rows)
    fn=sum(r['status']!='PRESCREEN_PARK' and r['actual_park'] for r in rows)
    tn=len(rows)-tp-fp-fn
    value=dict(schema='l2_prescreen_backtest_v1',n=len(rows),positive='observed gate PARK in fixed 80-design audit; STOP/unresolved is non-PARK, not proven success',
        TP=tp,FP=fp,FN=fn,TN=tn,precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
        limitations='Current code/catalog semantics, original cards; unresolved actual designs are censored; uniform ±1-year shifts, not independent state-wise perturbations; path auto-binding repairs historical path PARKs',
        rows=rows,retroactive_changes=0,actual_data_estimation_calls=0)
    ensure_once(output,value);return value


def screen_ri(declaration,design):
    """Exact deterministic decision, stopping once the floor (0.90, or R3.1 0.80) is impossible."""
    from .engine.ri import predraw_labels,pre_family,relabeled_design,support_failure,inventory,RI_SEED
    from . import gate_relax
    floor=gate_relax.floor_from_declaration(declaration)
    # Largest failure count that still reaches the floor: n - ceil(floor*n) (exact integers).
    max_failed={n:n-gate_relax.required_draws(n,floor or gate_relax.DEFAULT_FLOOR) for n in (199,999)}
    family=pre_family(design);records=[];failed=0
    if not family:return dict(passed=False,checks={'reason':'empty pre family'},outcome_values_read=0)
    for i,labels in enumerate(predraw_labels(design,999),1):
        try:
            if pre_family(relabeled_design(declaration,design,labels))!=family:
                raise ValueError('RI lead inventory changed under relabeling')
        except ValueError as exc:
            if not support_failure(exc):raise
            records.append(dict(infeasible=True,reason=str(exc)));failed+=1
        else:records.append(dict(infeasible=False))
        if (i<=199 and failed>max_failed[199]) or failed>max_failed[999]:
            n=199 if i<=199 and failed>max_failed[199] else 999
            return dict(passed=False,checks={f'RI{n}':dict(evaluated=i,infeasible_count=failed,
                maximum_possible_feasible_fraction=(n-failed)/n,support_pass=False,early_failure_proved=True)},seed=RI_SEED)
    checks={f'RI{n}':inventory(records[:n],floor=floor) for n in (199,999)}
    return dict(passed=all(r['support_pass'] for r in checks.values()),checks=checks,seed=RI_SEED)
