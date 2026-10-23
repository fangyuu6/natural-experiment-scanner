"""Stopped C58 remediation; preserve submitted, in-progress and sealed bytes."""
import json, os
from pathlib import Path
from .storage import ensure_once, sha
from .autopilot import atomic, utc
from .agents.history_packet import answer_bearing, clean_pending

RULE='HUB_RULING_C58_ANSWER_BEARING.md §2'


def read(path): return json.loads(Path(path).read_text())


def jobs(repo):
    ids={r['design_id'] for r in read(repo/'data/l2_pipeline/registry/ACTIVE_DESIGNS.json')['designs']}
    ledger=repo/'data/l2_pipeline/registry/AUTOPILOT_LEDGER.jsonl'
    ids.update(read_line.get('design_id') for line in ledger.read_text().splitlines()
               if line.strip() for read_line in [json.loads(line)])
    for directory, dirs, files in os.walk(repo/'data/l2_pipeline'):
        dirs[:]=[d for d in dirs if d not in {'code_snapshots','results','objects','tree','c58'}]
        for name in files:
            if not name.endswith('.job.json'): continue
            path=Path(directory)/name;v=read(path)
            if (v.get('role') in {'A','B'} and v.get('contract',{}).get('jobs_kind')=='official_history_collection'
                    and (v.get('design_id') in ids or path.is_relative_to(repo/'data/l2_pipeline/autopilot'))):
                yield path,v


def apply(repo):
    from .agents.sonnet_queue import locked, _reroute_pending_locked
    repo=Path(repo).resolve();base=repo/'data/l2_pipeline/autopilot/c58';registry=repo/'data/l2_pipeline/registry'
    if not (registry/'AUTOPILOT_STOP').exists():raise ValueError('C58 requires stopped scheduler')
    if (base/'APPLICATION.json').exists():return read(base/'APPLICATION.json')
    inventory=read(base/'AUDIT_INVENTORY.json');entries=inventory['submissions']
    if inventory['replacement_cases'] or inventory['unresolved_cases']:
        raise ValueError('C58 exact-copy cases require explicit §7 preparation before application')
    flagged=[r for r in entries if r['answer_bearing_packet']]
    annotations={r['output_path']:dict(answer_bearing_packet=True,design_id=r['design_id'],role=r['role'],
        job_path=r['job_path'],job_sha256=sha(r['job_path']),submission_sha256=sha(r['output_path']),rule=RULE,
        acceptance_guard='A/B reconciliation and fresh ten-state official V3 audit remain required') for r in flagged}
    ensure_once(base/'HISTORY_SUBMISSION_ANNOTATIONS.json',dict(schema='l2_c58_history_annotations_v1',submissions=annotations))
    lock_annotations=[]
    # Companions bind immutable lock bytes; never rewrite lock hashes used in sealed designs.
    for directory, dirs, files in os.walk(repo/'data/l2_pipeline/autopilot'):
        dirs[:]=[d for d in dirs if d not in {'code_snapshots','results','objects','tree','c58'}]
        if 'CODING_LOCK.json' not in files:continue
        path=Path(directory)/'CODING_LOCK.json';v=read(path)
        affected={p:annotations[p] for p in v.get('files',{}) if p in annotations}
        if affected:lock_annotations.append(dict(path=str(path),sha256=sha(path),answer_bearing_packet=True,submissions=affected))
    ensure_once(base/'HISTORY_LOCK_ANNOTATIONS.json',dict(schema='l2_c58_history_lock_annotations_v1',locks=lock_annotations,
        submissions=annotations,rule=RULE,original_locks_unchanged=True))
    if not (base/'LEDGER_RECEIPT.json').exists():
        ledger=registry/'AUTOPILOT_LEDGER.jsonl'
        existing={json.loads(line).get('output_path') for line in ledger.read_text().splitlines()
                  if line.strip() and json.loads(line).get('decision')=='ANSWER_BEARING_PACKET_C58'}
        with ledger.open('a') as stream:
            for r in flagged:
                if r['output_path'] in existing:continue
                stream.write(json.dumps(dict(time=utc(),design_id=r['design_id'],attempt_id=r['attempt_id'],
                    action='history',decision='ANSWER_BEARING_PACKET_C58',answer_bearing_packet=True,
                    output_path=r['output_path'],job=r['job_path'],rule=RULE,status_unchanged=True),sort_keys=True)+'\n')
        ensure_once(base/'LEDGER_RECEIPT.json',dict(flagged_submissions=len(flagged),sent_to_section7=0,statuses_unchanged=True))
    queue=registry/'SONNET_QUEUE';running=set()
    for p in (queue/'in_progress').glob('*/PACKET.json'):running.add(read(p)['job_path'])
    generated=[];retained=[]
    for _,v in jobs(repo):
        p=v['job_path'];out=Path(v['output_dir'])
        from .agents.sonnet_queue import identity
        claim=repo/'data/l2_pipeline/autopilot/worker_claims'/(identity(p)+'.json')
        if claim.exists() or claim.with_suffix('.started.json').exists() or p in running or Path(v['output_path']).exists() or Path(v['result_path']).exists() or any((out/n).exists() for n in
                ('DISPATCH_ONCE.json','dispatch.log','DISPATCH_RESULT.json')):
            retained.append(p);continue
        clean=clean_pending(p,base/'clean_jobs')
        if clean!=p:generated.append(dict(original_job=p,original_sha256=sha(p),clean_job=clean,clean_sha256=sha(clean)))
    with locked(queue):events=_reroute_pending_locked(queue)
    ensure_once(base/'PACKET_REGENERATION.json',dict(generated=generated,retained=retained,
        queue_reroutes=events,in_progress_unchanged=sorted(running),dispatches=0))
    ensure_once(base/'DOWNLOADS.json',dict(files=0,bytes=0,entries=[]))
    result=dict(submissions=len(entries),flagged=len(flagged),sent_to_section7=0,clean_packets=len(generated),
        lock_annotations=len(lock_annotations),in_progress=len(running),queue_reroutes=len(events))
    ensure_once(base/'APPLICATION.json',result)
    return result
