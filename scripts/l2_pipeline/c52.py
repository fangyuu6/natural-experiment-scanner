"""Stopped-host C52 queue recovery. No dispatch, calibration or fitting."""
from copy import deepcopy
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc
from .storage import ensure_once,sha
from .population_resolution import bind,rebase_sources
from .preparation_recovery import replay_public_panel,FROZEN
from .requeue import requeue
from .c42 import Park

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c52'


def advance(p,row):
    stages=[]
    for action in ('S3','S4'):
        if p.done(row,action):continue
        try:
            result=p.backend.perform(row,action,p)
            ensure_once(p.checkpoint(row,action),result);stages.append(dict(action=action,result=result))
        except Park as exc:
            ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],
                action=action,status='PARKED',reason=str(exc),metrics=exc.metrics))
            stages.append(dict(action=action,park_reason=str(exc)));break
    return stages


def apply():
    p=Autopilot()
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C52 requires stopped scheduler')
        baseline=read(OUT/'OPEN_QUEUE_BASELINE.json')['entries']
        source_entries=[e for e in baseline if not e['status'].startswith('SUPERSEDED_') and e['action']=='S2' and 'C35 source path differs' in e['reason']]
        auth=ensure_once(OUT/'AUTHORIZATION.json',dict(schema='l2_public_panel_replay_authorization_v1',stage='S2',
            designs=[e['design_id'] for e in source_entries],failure_reason='C35 source path differs from catalog',
            source='User Task C52 §2/3(a): mechanical rerun of stale pre-C50 public-panel failures; no estimation slot',execution_authorized=False))
        authority=ensure_once(OUT/'AUTHORITY.json',dict(source='User Task C52 §2/3(a,b)',
            allowed=['replay stale pre-C50 S2 public-panel failures','mechanically reprepare EPCS with new support and draft'],
            science_changes_authorized=False,execution_authorized=False))
        handled=[];applications=[]
        for e in source_entries:
            row=next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==e['design_id'])
            slot=OUT/'resolutions'/row['design_id'];existing=read(slot/'APPLICATION.json')
            if existing:applications.append(existing);handled.append((e['design_id'],e['attempt_id']));continue
            ctx=p.backend.ctx(row,strict=True)
            failed=next(path for path in sorted((ctx.design_dir/'receipts/S2').glob('*.json')) if read(path)['result']['disposition']=='failed_no_retry')
            result=replay_public_panel(ctx,bind(failed),auth)
            if result['disposition']!='ready':raise ValueError('Mechanical S2 replay did not pass: '+str(result))
            ensure_once(p.checkpoint(row,'S2'),result)
            proof=ensure_once(slot/'S2_RECOVERY.json',dict(failed_receipt=bind(failed),
                ready_receipt=bind(sorted((ctx.design_dir/'receipts/S2').glob('*.json'))[-1]),authorization=bind(auth),
                cause='Pre-C50 compact-secondary failure was reused by Backend.perform; current pinned adapter succeeds',
                code=[bind(ROOT/'scripts/l2_pipeline/connectors/c35_core.py'),bind(ROOT/'scripts/l2_pipeline/preparation_recovery.py')],
                actual_data_estimation_calls=0,dispatches=0))
            stop=p.stop_record(row);paths=[path for path in p.case_dir(row).glob('STOP*.json') if read(path)==stop]
            if len(paths)!=1:raise ValueError('Unique retained stop required')
            ensure_once(p.case_dir(row)/'STOP_SUPERSEDED_C52.json',dict(stop=bind(paths[0]),evidence=[bind(proof),bind(auth)]))
            stages=advance(p,row)
            value=dict(design_id=row['design_id'],attempt_id=row['attempt_id'],recovery=bind(proof),stages=stages,next_action=p.next_action(row))
            ensure_once(slot/'APPLICATION.json',value);applications.append(value);handled.append((e['design_id'],e['attempt_id']))
            p.ledger(row,'S2','SUPERSEDED_C52','record',receipt=str(slot/'APPLICATION.json'),next_action=value['next_action'])
        epcs=next(e for e in baseline if e['design_id']=='b1010b_epcs_mandate' and not e['status'].startswith('SUPERSEDED_'))
        slot=OUT/'resolutions'/epcs['design_id'];value=read(slot/'APPLICATION.json')
        if not value:
            inputs=read(slot/'PREPARATION_INPUTS.json')
            old=inputs['prior_row'] if inputs else next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==epcs['design_id'])
            oldroot=p.backend.root(old)
            if any((oldroot/n).exists() for n in FROZEN):raise ValueError('Frozen EPCS cannot reprepare')
            card=rebase_sources(read(old['card']),old['card']);card['version']='v'+str(int(card['version'][1:])+1)
            card.setdefault('automation',{})['c52_preparation_revision']=dict(prior_card=bind(old['card']),
                prior_support=bind(oldroot/'SUPPORT.json'),reason='S0 effective CARD versus raw intake conflict; new current-code draft required',execution_authorized=False)
            path=ensure_once(slot/'CARD.json',card)
            ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=old['design_id'],
                card_sha256=sha(path),prior_card_sha256=sha(old['card']),decision='accept',purpose_rule='V6.2',
                reason='C52 mechanical preparation reset; unchanged science, retained legal gates; current-code draft required',
                authority=bind(authority),superseded_status='SUPERSEDED_C52',execution_authorized=False))
            ensure_once(slot/'PREPARATION_INPUTS.json',dict(prior_row=old,card=bind(path),ruling=bind(ruling)))
            # Release the global lock before the public requeue API.
        queue=read(p.registry/'HUB_QUEUE.json')
        for e in queue['entries']:
            if (e['design_id'],e['attempt_id']) in handled and not e['status'].startswith('SUPERSEDED_'):
                e.update(status='SUPERSEDED_C52',prior_status=e['status'],superseded_at=utc(),authority=bind(auth))
        atomic(p.registry/'HUB_QUEUE.json',queue)
    if not value:
        receipt=requeue(epcs['design_id'],path,ruling,p)
        row=receipt['new_row'];stages=[]
        for action in ('S0','S1','S2'):
            if p.done(row,action):continue
            result=p.backend.perform(row,action,p);ensure_once(p.checkpoint(row,action),result);stages.append(dict(action=action,result=result))
        stages+=advance(p,row)
        if p.next_action(row)!='PARK':
            jobs=p.backend.jobs(row,'draft',p);ensure_once(p.case_dir(row)/'jobs/draft.json',dict(jobs=jobs))
        else:jobs=[]
        value=dict(design_id=row['design_id'],new_row=row,requeue=receipt,stages=stages,
            new_support=bind(p.backend.root(row)/'SUPPORT.json'),draft_jobs=[bind(j) for j in jobs],
            next_action=p.next_action(row),prep_freeze_pending='RI, forecasts and independent current-code draft PASS required',
            science_changed=False,actual_data_estimation_calls=0,dispatches=0)
        ensure_once(slot/'APPLICATION.json',value)
    applications.append(value)
    return ensure_once(OUT/'QUEUE_APPLICATION.json',dict(applications=applications,
        remaining_open=[e for e in read(p.registry/'HUB_QUEUE.json')['entries'] if not e['status'].startswith('SUPERSEDED_')],
        downloads=[],dispatches=0,actual_data_estimation_calls=0,outcome_association_calls=0))


if __name__=='__main__':print(apply())
