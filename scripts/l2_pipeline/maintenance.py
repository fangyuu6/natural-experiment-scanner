"""C62 reason-scoped maintenance and daily, immutable hub-class batches."""
from pathlib import Path
import re
from .storage import ensure_once,sha

FIXES={
 'prescreen': ['s2_path','s0_construct','s3_support','ri_feasibility','fresh_pool'],
 'draft_dry_load': ['draft_load','preparation_binding'],
 'history_contract': ['history_status','history_universe'],
 'population_metadata': ['s0_construct'],
 'mechanical_reprep': ['preparation_binding'],
 'evidence_should': ['review_should'],
 'collector_self_check': ['history_status','history_universe','agent_contract'],
 'history_packet_ci': ['history_blindness'],
 'scoped_requeue': ['requeue_scope'],
 'daily_hub_batch': ['hub_queue'],
 'packet_released_rebind': ['packet_released'],
 'code_defect_fixed': ['code_fix_c78','code_fix_c84_f1','code_fix_c84_purpose','code_fix_named_panel_c42','code_fix_named_panel_c64','code_fix_forecast_model_id','code_fix_inference_tolerance'],
}
PATTERNS={
 's2_path':('no local','missing declared public file','outcome.paths','source path'),
 's0_construct':('v6.2 target/population','same-construct','construct question','v6.3'),
 's3_support':('support insufficient','s3 '),
 'ri_feasibility':('ri199','ri999','ri feasibility'),
 'fresh_pool':('fresh v3','ten-treated','fresh pool'),
 'history_status':('invalid coding status','insufficient_work'),
 'history_universe':('complete universe once',),
 'preparation_binding':('existing preparation artifact changed','preparatory snapshot','preparation binding','single review resolution signer differs'),
 'packet_released':('sonnet packet released by workers',),
 'review_should':('review should','should requires','novel/science finding requires explicit validated hub closure'),
 'draft_load':('dry-load','scientific card','metadata csv','metadata-selected comparison group','metadata named','comparison group','registered loader'),
 'agent_contract':('contract_rejected','schema_format'),
 'history_blindness':('history packet','screening adoption'),
 'code_fix_c84_f1':('purpose direction requires public source locators',),
 'code_fix_c84_purpose':('purpose citation must identify supported fields',),
 'code_fix_c78':("'list' object has no attribute 'get'",),
 'code_fix_named_panel_c42':('c42 overlay removed existing exclusions',),
 'code_fix_named_panel_c64':('purpose direction requires s0 evidence bound to the target',),
 'code_fix_forecast_model_id':('forecast model differs from registered slot',),
 'code_fix_inference_tolerance':('stacked wls differs from registered weighted att',),  # T264: engine/inference.py relative tolerance (T258); frozen snapshots need a C57 rebind
}
# Code defects fixed by a named code task: tag -> the task and the one stop action
# it can supersede. Matching is by exact error text, once per design attempt.
CODE_FIXES={'code_fix_c78':dict(task='C78',action='purpose',
    fix='coverage.normalize_series_breaks: [year, note] series_breaks pairs'),
 'code_fix_c84_f1':dict(task='C84',action='purpose-direction',fix='Empty citations record F1 FIELD_MISSING; EX-1 coding never gates L2'),
 'code_fix_c84_purpose':dict(task='C84',action='purpose',fix='Queue self-check and supported-fields CONTRACT replacement')}


def reason_tags(reason):
    text=str(reason).lower()
    return sorted(tag for tag,patterns in PATTERNS.items() if any(p in text for p in patterns))


def require_match(reason,tags):
    if not isinstance(tags,list) or not tags or any(t not in {x for v in FIXES.values() for x in v} for t in tags):
        raise ValueError('Fix must declare known impact_tags')
    matched=sorted(set(reason_tags(reason))&set(tags))
    if not matched:raise ValueError('Fix impact tags do not match retained PARK/STOP reason')
    return matched


def daily_batch(pilot,day=None):
    from .autopilot import read,utc
    day=day or utc()[:10]
    base=pilot.registry.parent/'autopilot/hub_daily';dest=base/(day+'.json')
    if dest.exists():return read(dest)
    prior=set()
    for p in base.glob('*.json'):
        prior.update(c['class'] for c in read(p).get('classes',[]))
    grouped={}
    for e in read(pilot.registry/'HUB_QUEUE.json',dict(entries=[]))['entries']:
        if e.get('status')!='NEEDS_RULING':continue
        tags=reason_tags(e.get('reason',''))
        # New unknown exceptions group by a bounded normalized prefix.
        reason=re.sub(r'(?:/[^\s:]+|(?:data|scripts)/[^\s:]+)','<path>',str(e.get('reason','')))
        reason=re.sub(r'\b[0-9a-f]{16,}\b','<hash>',reason)
        reason=re.sub(r'\b[0-9]+\b','#',reason)
        key='|'.join(tags) or reason.split('[{')[0][:240]
        if key not in prior:grouped.setdefault(key,[]).append(e)
    value=dict(schema='l2_hub_daily_classes_v1',day=day,classes=[dict(**{'class':k},entries=v) for k,v in sorted(grouped.items())],execution_authorized=False)
    ensure_once(dest,value);return value


def close_should(p,row,job):
    """C54 §1: bounded evidence rules, never prose alone as execution authority."""
    from .autopilot import read
    from .registration import _validate_resolution,_review,finding_sha256,hub_signer
    from .population_resolution import bind
    from .closure_evidence import snapshot
    from .c54 import draft_identity,AUTH_REL
    meta=read(Path(job).with_suffix('.job.json'));review=read(meta['promote_path'])
    if review.get('should') and any(not isinstance(f,dict) or not f.get('description') or not f.get('impact') or not f.get('evidence') for f in review['should']):return False
    ctx=p.backend.ctx(row);root=ctx.design_dir
    if review.get('must') or review.get('verdict')!='PASS' or (root/'FREEZE_PREP.json').exists():return False
    attempt,path=draft_identity(p,row,[job]);resolutions=[]
    for f in review.get('should',[]):
        impact=f.get('impact','').lower();description=f.get('description','').lower()
        # Keep legal timing, measurement breaks and potential gate changes open.
        if any(w in description for w in ('legal-timing','recoding','series break','payer-limited')):return False
        no_change=any(w in impact for w in ('no registered number is incorrect','no number is affected','no registered number or result is affected','no registered number or gate is affected','primary result, its gates and every reported att are unaffected'))
        kind=any(w in description for w in ('sensitivity','sensitivities','interval candidate','interval candidates','limitations','documentation','confidence interval'))
        if not (no_change and kind):return False
        evidence=[]
        for token in re.findall(r'(?:data|scripts|docs)/[^\s`\"\';:,()]+',f.get('evidence','')):
            candidate=(p.repo/token).resolve()
            if candidate.is_relative_to(p.repo) and candidate.is_file():evidence.append(snapshot(candidate,root))
        if not evidence:return False
        resolutions.append(dict(bucket='should',id=f['id'],finding_sha256=finding_sha256(f),finding_verbatim=f,status='declined',
            reason='C54 §1(b): defer non-science improvement to next version; located repo evidence and review impact affirm unchanged registered estimate/sample/gates.',
            evidence=[snapshot(path,root),snapshot(root/'SPEC.json',root),snapshot(p.repo/AUTH_REL,root),*evidence]))
    if not resolutions:return False
    value=dict(schema='l2_draft_resolution_v1',design_id=row['design_id'],review_attempt=attempt,
        review_sha256=sha(path),source_spec=bind(root/'SPEC.json'),target_spec_sha256=sha(root/'SPEC.json'),target_unchanged=True,
        hub_signer=hub_signer(ctx) or 'hub autopilot (standing authorization 2026-10-10)',execution_authorized=False,outcome_associations_read=False,resolutions=resolutions)
    _validate_resolution(ctx,attempt,value)
    closure=ensure_once(root/('DRAFT_RESOLUTION_'+attempt+'.json'),value);_review(ctx,attempt)
    p.ledger(row,'draft','SHOULD_DEFERRED_C62','c54',closure=bind(closure),findings=review['should'],impact_tags=['review_should'])
    return True


def resume_should(p,row):
    from .autopilot import read
    stop=p.stop_record(row)
    if not stop or 'review_should' not in reason_tags(stop['reason']):return False
    root=p.backend.root(row)
    if any((root/n).exists() for n in ('FREEZE_PREP.json','FREEZE_FINAL.json','EXECUTION_ONCE.json','TERMINAL.json')):return False
    inventory=read(p.case_dir(row)/'jobs/draft.json')
    if not inventory or len(inventory.get('jobs',[]))!=1:return False
    job=inventory['jobs'][0]
    meta=read(Path(job).with_suffix('.job.json'))
    if not meta or not Path(meta['promote_path']).exists():return False
    from .c54 import close_should as close_known,draft_identity,valid_hub_closure
    from .class_table import enabled,accept
    if enabled(root):
        if not (valid_hub_closure(p,row,job) or accept(p,row,job,'draft_readiness')):return False
    elif not (valid_hub_closure(p,row,job) or close_known(p,row,job) or close_should(p,row,job)):return False
    attempt,_=draft_identity(p,row,[job]);evidence=root/('DRAFT_RESOLUTION_'+attempt+'.json')
    from .c62_apply import supersede
    supersede(p,row,evidence,['review_should'])
    ensure_once(p.checkpoint(row,'draft'),inventory)
    return True


def resume_code_fix(p,row):
    """Supersede a STOP once after a named code task fixed its defect.

    No dispatch, fit or download: the design's existing job inventory for the
    stopped action is reused, so the next tick only re-collects it.
    """
    from .autopilot import read
    stop=p.stop_record(row)
    if not stop:return False
    tags=[t for t in reason_tags(stop.get('reason','')) if t in CODE_FIXES]
    if len(tags)!=1:return False
    fix=CODE_FIXES[tags[0]];case=p.case_dir(row)
    if stop.get('action')!=fix['action']:return False
    if fix['task']=='C84':
        from .c84_apply import resume
        return resume(p,row,stop,tags,fix)
    # One supersession per design attempt; a later stop is never auto-superseded.
    if any((case/n).exists() for n in ('STOP_SUPERSEDED_C62.json','STOP_SUPERSEDED_C70.json')):return False
    root=p.backend.root(row)
    if any((root/n).exists() for n in ('FREEZE_PREP.json','FREEZE_FINAL.json','EXECUTION_ONCE.json','TERMINAL.json')):return False
    inventory=read(case/('jobs/'+fix['action']+'.json'))
    if not inventory or not inventory.get('jobs'):return False
    evidence=ensure_once(case/('CODE_FIX_'+fix['task']+'.json'),dict(schema='l2_code_fix_supersession_v1',
        code_task=fix['task'],fix=fix['fix'],impact_tags=tags,stop=stop,reused_jobs=inventory['jobs'],
        dispatches=0,execution_authorized=False,outcome_values_read=0))
    from .c62_apply import supersede
    supersede(p,row,evidence,tags)
    return True


def requeue_fix(design,card,ruling,pilot,*,scheduler_owned=False):
    """All engineering fixes enter here; scientific owner rulings stay separate."""
    from .autopilot import read
    from .requeue import requeue
    decision=read(ruling);tags=decision.get('impact_tags')
    row=next(r for r in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==design)
    stop=pilot.stop_record(row) or read(pilot.case_dir(row)/'PARKED.json',{})
    require_match(stop.get('reason',''),tags)
    return requeue(design,card,ruling,pilot,scheduler_owned=scheduler_owned)


MODEL_ID_STATUS='SUPERSEDED_FORECAST_MODEL_ID'
MODEL_ID_AUTHORITY='docs/plans/l2-pipeline/INTAKE_AND_PARK_MAP_20261016.md'


def requeue_model_id_stops(pilot=None,window_task='LOOP_EPERM'):
    """Window step after the model-id fix: reopen designs STOPped at forecasts only by the id mismatch.

    Each goes through requeue_fix as a new attempt (card = previous card with the version bumped, nothing else;
    history, reconcile and audit retained, restart at purpose-direction). Exact-reason STOPs only. Nothing is
    dispatched, fitted or deleted; idempotent (a reopened design no longer has the STOP).
    """
    from .autopilot import Autopilot,read
    from .ops.watchdog import clean_stop,guard
    p=pilot or Autopilot()
    out=p.repo/'data/l2_pipeline/autopilot/loop_eperm/forecast_model_id'
    authority=p.repo/MODEL_ID_AUTHORITY
    done=[]
    with guard(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists() or read(p.registry/'CODE_TASK.lock',{}).get('task')!=window_task:
            raise ValueError(window_task+' stopped code window required')
        clean_stop(p.repo)
        for row in read(p.registry/'ACTIVE_DESIGNS.json')['designs']:
            stop=p.stop_record(row)
            if not stop or stop.get('action')!='forecasts' or 'code_fix_forecast_model_id' not in reason_tags(stop.get('reason','')):continue
            payload=read(row['card']);payload['version']='v'+str(int(payload['version'][1:])+1)
            card=ensure_once(Path(row['card']).with_name(Path(row['card']).stem+'_'+payload['version']+'.json'),payload)
            ruling=ensure_once(out/'rulings'/(row['design_id']+'.json'),dict(
                schema='l2_requeue_ruling_v1',design_id=row['design_id'],prior_card_sha256=sha(row['card']),card_sha256=sha(card),
                decision='accept',purpose_rule='V6.2',superseded_status=MODEL_ID_STATUS,impact_tags=['code_fix_forecast_model_id'],
                authority=dict(path=str(authority.resolve()),sha256=sha(authority)),prior_stop=stop,
                reason='Forecast model-id comparison fixed (reported claude-sonnet-5-5 equals registered claude-sonnet-5.5); fresh attempt, card unchanged except its version',
                execution_authorized=False))
            done.append(requeue_fix(row['design_id'],card,ruling,p))
    return done
