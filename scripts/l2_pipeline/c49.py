"""C49 queue resolution, transport-only replacements; no dispatch or fitting."""
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc
from .storage import ensure_once,sha
from .population_resolution import apply as resolve,bind
from .agents.replacement import ContractRejection
from .c42 import Park

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c49'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULINGS_C49.md'


def row_for(p,did):return next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==did)


def reopen(p,row,evidence):
    stop=p.stop_record(row)
    if not stop:return
    paths=[q for q in p.case_dir(row).glob('STOP*.json') if read(q)==stop]
    if len(paths)!=1:raise ValueError('Unique retained stop required')
    ensure_once(p.case_dir(row)/'STOP_SUPERSEDED_C49.json',dict(stop=bind(paths[0]),evidence=list(evidence)+[bind(AUTH)],authority=bind(AUTH),execution_authorized=False))


def history(p,row):
    from .agents.jobs import load_job
    inventory=read(p.case_dir(row)/'jobs/history.json',{})
    jobs=inventory.get('jobs',row.get('history_jobs',[]))
    if not jobs:raise ValueError('History job inventory missing')
    ensure_once(p.case_dir(row)/'jobs/history.json',dict(jobs=jobs))
    resolutions=[];exhausted=[]
    for original in jobs:
        job_path=p.effective_job(row,original);job=load_job(Path(job_path))
        try:
            receipt=p.backend.collect(job_path)
            resolutions.append(dict(role=job.role,action='retained_validated',collection=bind(Path(job.output_dir)/'COLLECTION.json')))
        except ContractRejection as exc:
            if exc.kind not in {'V9_SOURCE_ID_URL','V9_RESEARCH_LOG'}:raise
            slot=p.replacement_slot(row,job_path)
            if (slot/'REPLACEMENT.json').exists():
                exhausted.append(dict(role=job.role,job=job_path,kind=exc.kind,reason=str(exc)))
                resolutions.append(dict(role=job.role,action='PARK',reason='§7 replacement already used and defect repeated: '+str(exc)))
            else:
                replacement=p.replace_submission(row,'history',exc)
                resolutions.append(dict(role=job.role,action='§7 replacement prepared',reason=str(exc),job=bind(replacement),sidecar=bind(Path(replacement).with_suffix('.job.json')),replacement=bind(slot/'REPLACEMENT.json')))
    path=ensure_once(OUT/'resolutions'/row['design_id']/'CONTRACT.json',dict(design_id=row['design_id'],roles=resolutions,authority=bind(AUTH),dispatches=0,exhausted=exhausted))
    reopen(p,row,[bind(path)])
    if exhausted:
        ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],action='history',status='PARKED',reason='§7 replacement exhausted: '+'; '.join(e['role']+': '+e['reason'] for e in exhausted),evidence=bind(path)))
    p.ledger(row,'history','CONTRACT_ROUTED_C49','record',receipt=bind(path),dispatches=0)
    return path


def apply():
    p=Autopilot();q=read(p.registry/'HUB_QUEUE.json');prior=read(OUT/'OPEN_QUEUE_BASELINE.json')
    entries=prior['entries'] if prior else [e for e in q['entries'] if not e['status'].startswith('SUPERSEDED_')]
    if len(entries)!=16:raise ValueError('Expected16 original open C49 entries')
    ensure_once(OUT/'OPEN_QUEUE_BASELINE.json',dict(entries=entries))
    results=[]
    for e in entries:
        did=e['design_id'];slot=OUT/'resolutions'/did;application=slot/'APPLICATION.json'
        if application.exists():results.append(bind(application));continue
        row=row_for(p,did)
        if e['action']=='purpose':
            value=resolve(p,row,output=slot/'population')
            path=ensure_once(application,dict(action='V6.3 versioned resolution',receipt=value['receipt'],dispatches=0))
        else:
            with scheduler_lock(p.registry):
                if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('Stopped scheduler required')
                if e['action']=='history':
                    receipt=history(p,row)
                    value=dict(action='§7 contract routing',receipt=bind(receipt),next_action=p.next_action(row),dispatches=0)
                elif e['action']=='S1':
                    if p.done(row,'S1') is None:
                        result=p.backend.perform(row,'S1',p);ensure_once(p.checkpoint(row,'S1'),result)
                    overlay=p.backend.root(row)/'C48_AUDIT_PREPARATION.json'
                    reopen(p,row,[bind(overlay)])
                    value=dict(action='V3.1 S1 registered',overlay=bind(overlay),unresolved_units=read(overlay)['unresolved_units'],next_action=p.next_action(row),dispatches=0)
                else:raise ValueError('Uncovered action: '+e['action'])
                path=ensure_once(application,value)
            results.append(bind(path));continue
        results.append(bind(path))
    with scheduler_lock(p.registry):
        q=read(p.registry/'HUB_QUEUE.json')
        for e in q['entries']:
            if not e['status'].startswith('SUPERSEDED_') and e['design_id'] in {v['design_id'] for v in entries}:
                e.update(status='SUPERSEDED_C49',prior_status=e['status'],superseded_at=utc(),authority=bind(AUTH))
        atomic(p.registry/'HUB_QUEUE.json',q)
    reasons=[]
    for e in entries:
        row=row_for(p,e['design_id']);action=p.next_action(row)
        if action not in {'FINISHED'}:
            parked=read(p.case_dir(row)/'PARKED.json',{})
            reason=parked.get('reason') or ('Await unchanged-contract §7 replacement submission; '+e['reason'] if e['action']=='history' else 'C49 resolved '+e['action']+'; awaits '+action+' and remaining frozen-study gates')
            reasons.append(dict(design_id=row['design_id'],next_action=action,reason=reason))
    return ensure_once(OUT/'QUEUE_APPLICATION.json',dict(resolutions=results,remaining_designs=reasons,superseded_entries=len(entries),downloads=[],dispatches=0,outcome_association_calls=0,actual_data_estimation_calls=0))

if __name__=='__main__':print(apply())
