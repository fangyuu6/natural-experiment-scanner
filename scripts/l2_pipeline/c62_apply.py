"""Explicit C62 stopped-host repairs. No dispatch, fit, download, or lease change."""
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc
from .storage import ensure_once,sha
from .population_resolution import bind
from .maintenance import require_match,FIXES
BASE=Path('data/l2_pipeline/autopilot/c62')


def inactive(p,row):
    from .agents.jobs import load_job
    from .agents.sonnet_queue import state
    jobs=[]
    for original in read(p.case_dir(row)/'jobs/history.json',dict(jobs=[]))['jobs']:
        path=p.effective_job(row,original);job=load_job(Path(path));out=Path(job.output_dir)
        if state(path,p.repo)=='running' or ((out/'DISPATCH_ONCE.json').exists() and not (out/'DISPATCH_RESULT.json').exists()):
            raise ValueError('Running/claimed worker must remain untouched')
        jobs.append(job)
    return jobs


def supersede(p,row,evidence,tags,park=None,*,suffix="C62"):
    if suffix not in {"C62","C84"}:raise ValueError("Unknown supersession task")
    stop=p.stop_record(row);require_match(stop['reason'],tags)
    matches=[x for x in p.case_dir(row).glob('STOP*.json') if read(x)==stop]
    if len(matches)!=1:raise ValueError('Unique retained stop required')
    ensure_once(p.case_dir(row)/('STOP_SUPERSEDED_'+suffix+'.json'),dict(stop=bind(matches[0]),evidence=[bind(evidence)],impact_tags=tags))
    if park:ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],action='history',status='PARKED',reason=park,diagnosis=bind(evidence)))
    queue=read(p.registry/'HUB_QUEUE.json')
    for e in queue['entries']:
        if (e['design_id'],e['attempt_id'],e.get('status'))==(row['design_id'],row['attempt_id'],'NEEDS_RULING'):
            e.update(status='SUPERSEDED_'+suffix,prior_status='NEEDS_RULING',superseded_at=utc(),evidence=bind(evidence),impact_tags=tags)
    atomic(p.registry/'HUB_QUEUE.json',queue)
    p.ledger(row,'resume','SUPERSEDED_'+suffix,'record',evidence=bind(evidence),impact_tags=tags,disposition='PARK' if park else 'RESUME')


def apply():
    from .c59_queue import require_complete_history
    from .c42 import Park
    p=Autopilot();results=[]
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('STOP required')
        rows={r['design_id']:r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs']}
        for suffix in ('pay_range_default_disclosure','state_ice_detainer_limit_violent_crime','reinsurance_1332_waiver'):
            row=rows['b1011tl_'+suffix];dest=BASE/row['design_id']/'APPLICATION.json'
            if dest.exists():results.append(read(dest));continue
            gaps=[]
            for job in inactive(p,row):
                try:require_complete_history(job,read(job.output_path,{}))
                except Park as exc:gaps.append(dict(role=job.role,reason=str(exc),metrics=exc.metrics,original=bind(job.output_path)))
            if not gaps:raise ValueError('Expected incomplete-history proof missing')
            diagnosis=ensure_once(BASE/row['design_id']/'DIAGNOSIS.json',dict(
                cause='Retained STOP predates C59 queue migration; C59 apply_queue enumerated only complete_streets and snap_call_center, leaving these three old STOP entries active. Current generic checker detects explicit incomplete/null statuses.',
                old_stop=p.stop_record(row),current_code=bind(p.repo/'scripts/l2_pipeline/c59_queue.py'),gaps=gaps,impact_tags=['history_status'],dispatches=0))
            supersede(p,row,diagnosis,['history_status'],'INSUFFICIENT_WORK: complete independent state history required')
            value=dict(design_id=row['design_id'],next_action=p.next_action(row),diagnosis=bind(diagnosis),rechecked_current_code=True,dispatches=0)
            ensure_once(dest,value);results.append(value)
        row=rows['b1011tl_snap_phone_interview_waiver'];dest=BASE/row['design_id']/'APPLICATION.json'
        if dest.exists():results.append(read(dest))
        else:
            inactive(p,row)
            replacement=read(p.registry.parent/'autopilot/replacements'/row['design_id']/'A/REPLACEMENT.json')
            from .agents.jobs import load_job
            evidence=[];both_sonnet=True
            from .agents.sonnet_queue import identity
            for key in ('original_job','replacement_job'):
                job=load_job(Path(replacement[key]));completion=Path(job.output_dir)/'DISPATCH_RESULT.json'
                packets=[x for x in (p.registry/'SONNET_QUEUE').rglob('PACKET.json') if read(x).get('job_id')==identity(job.job_path)]
                sonnet=any(x.parent.parent.name=='done' and (x.parent/'COMPLETION.json').exists() for x in packets)
                both_sonnet &= sonnet and not completion.exists()
                evidence.append(dict(attempt=key,job=bind(job.job_path),backend='codex' if completion.exists() else 'sonnet' if sonnet else 'unknown',completion=bind(completion) if completion.exists() else None))
            if both_sonnet:raise ValueError('Sonnet-only exception requires explicit final replacement path; not this observed case')
            diagnosis=ensure_once(BASE/row['design_id']/'DIAGNOSIS.json',dict(evidence=evidence,both_failed_attempts_sonnet=False,
                decision='PARK',reason='Both observed attempts ran on Codex; C62 Sonnet web-cap exception does not apply. Replacement completion arrived after retained STOP; no third attempt authorized.',impact_tags=['history_universe'],extra_replacements=0))
            supersede(p,row,diagnosis,['history_universe'],'CONTRACT_EXHAUSTED: history universe; both attempts Codex, no C62 Sonnet exception')
            value=dict(design_id=row['design_id'],next_action=p.next_action(row),diagnosis=bind(diagnosis),dispatches=0);ensure_once(dest,value);results.append(value)
        row=rows['r1011_speed_limit_65_rural_interstate_1987'];dest=BASE/row['design_id']/'APPLICATION.json'
        if dest.exists():results.append(read(dest))
        else:
            from .population_resolution import apply as resolve
            require_match(p.stop_record(row)['reason'],['s0_construct'])
            value=resolve(p,row,scheduler_owned=True,output=BASE/row['design_id'],superseded_status='SUPERSEDED_C62')
            value.update(impact_tags=['s0_construct'],diagnosis='Stale pre-C59 V6.2 STOP: intake selection already carried catalog hashes but pending purpose status; current C59 precheck/structured target_construct resolves all-road traffic fatalities. Broader population dilution is recorded in new card.')
            ensure_once(dest,value);results.append(value)
        row=rows['r1011_good_samaritan_911'];dest=BASE/row['design_id']/'APPLICATION.json'
        if dest.exists():results.append(read(dest))
        else:
            from .agents.collect import collect_job
            from .manual_evidence import collection_path
            jobs=inactive(p,row);receipts=[]
            for job in jobs:
                receipt=collect_job(job.job_path)
                if not receipt['validated']:raise ValueError('History recollection failed')
                receipts.append(bind(collection_path(job)))
            proof=ensure_once(BASE/row['design_id']/'REPREP.json',dict(schema='l2_c62_history_reprep_v1',
                card=bind(row['card']),history=receipts,prior_stop=p.stop_record(row),impact_tags=['preparation_binding'],
                reason='C61 IN adjudication changed derived normalization; additive manual_revision receipts bind current ruling without overwriting C59 history. No existing SUPPORT/SPEC: reconciliation and fresh audit precede their construction.',execution_authorized=False,dispatches=0))
            supersede(p,row,proof,['preparation_binding'])
            ensure_once(p.checkpoint(row,'history'),dict(jobs=[j.job_path for j in jobs],current_collections=receipts))
            try:
                value=p.backend.perform(row,'reconcile',p)
                ensure_once(p.checkpoint(row,'reconcile'),value)
            except Park as exc:
                ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],action='reconcile',status='PARKED',reason=str(exc),metrics=exc.metrics))
            value=dict(design_id=row['design_id'],next_action=p.next_action(row),reprep=bind(proof),dispatches=0)
            ensure_once(dest,value);results.append(value)
    ensure_once(BASE/'APPLICATION.json',dict(records=results,dispatches=0,actual_data_estimation_calls=0));return results

if __name__=='__main__':print(apply())
