"""C63 production intake pause, then one or two canaries before batch release."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from ..storage import public_input, sha

OWNER_DECISION='docs/plans/l2-pipeline/OWNER_DECISIONS_20261011_EVENING.md'


def early_canary_authority(pilot,now=None):
    """Only a completed restart receipt starts the owner's six-hour clock."""
    from ..autopilot import read
    now=now or datetime.now(timezone.utc)
    receipts=[]
    for path in (pilot.repo/'data/l2_pipeline/autopilot').glob('*/RESTART_RECEIPT.json'):
        try:
            value=read(path)
            if value.get('status')!='STARTED':continue
            stamp=datetime.fromisoformat(value['time'])
            if stamp.tzinfo is None:continue
            receipts.append((stamp,path))
        except (OSError,ValueError,KeyError,TypeError):continue
    if not receipts:return None
    stamp,path=max(receipts,key=lambda item:(item[0],str(item[1])))
    if now<stamp+timedelta(hours=6):return None
    return dict(owner_decision_path=OWNER_DECISION,restart_receipt_path=str(path),
                restart_receipt_sha256=sha(path),restart_time=stamp.isoformat(),
                eligible_at=(stamp+timedelta(hours=6)).isoformat(),admitted_at=now.isoformat())


def estimated_runs(pilot):
    from ..autopilot import read
    records=read(pilot.registry/'TERMINAL_RUNS.json',{'runs':[]})['runs'];valid=[]
    for r in records:
        root=Path(r.get('package_path','')).resolve()
        if not root.is_relative_to(pilot.registry.parent/'autopilot') or r.get('study_estimated') is not True:continue
        if r.get('status') not in {'COMPLETED','STOPPED'} or r.get('actual_data_estimation_calls',0)<=0:continue
        if all(r.get(k+'_path') and Path(r[k+'_path']).is_file() and sha(r[k+'_path'])==r.get(k+'_sha256') for k in ('terminal','result')):
            valid.append(r)
    return valid


def prescreen_parked(pilot, canaries):
    """Use the current attempt's structured receipt or ledger state only."""
    import json
    from ..autopilot import read
    rows={r['design_id']:r for r in read(pilot.registry/'ACTIVE_DESIGNS.json',{'designs':[]})['designs']}
    decisions={}
    ledger=pilot.registry/'AUTOPILOT_LEDGER.jsonl'
    if ledger.is_file():
        with ledger.open() as stream:
            for line in stream:
                event=json.loads(line);did=event.get('design_id')
                if did not in canaries:continue
                row=rows.get(did)
                if row and event.get('attempt_id')!=row.get('attempt_id'):continue
                if event.get('action') in {'resume','requeue'} or event.get('decision')=='MECHANICAL_REQUEUE':
                    decisions.pop(did,None)
                if event.get('action')=='prescreen':decisions[did]=event.get('decision')
                elif event.get('decision')=='OBSERVED_PRESCREEN_PARK':decisions[did]='PRESCREEN_PARK'
    parked={did for did,status in decisions.items() if status=='PRESCREEN_PARK'}
    for did in canaries:
        row=rows.get(did)
        if row and hasattr(pilot,'case_dir'):
            receipt=read(pilot.case_dir(row)/'PRESCREEN.json',{})
            if (receipt.get('status')=='PRESCREEN_PARK' and
                receipt.get('design_id',did)==did and row.get('card_sha256') and
                receipt.get('card_sha256')==row['card_sha256']):parked.add(did)
    for did in canaries:
        row=rows.get(did)
        if not row or not hasattr(pilot,'case_dir'):continue
        receipt=read(pilot.case_dir(row)/'PARKED.json',{})
        if (receipt.get('design_id')==did and receipt.get('attempt_id')==row.get('attempt_id')
                and receipt.get('status')=='PARKED' and receipt.get('reason') and receipt.get('action')):
            parked.add(did)
        from ..gate0 import current
        gate0=current(pilot,row) or {}
        if (gate0.get('status')=='GATE0_PARK' and gate0.get('card_sha256')==row.get('card_sha256')
                and gate0.get('design_id')==did):parked.add(did)
    return parked


def owner_batch_open(pilot,state):
    """C82: a repo-relative owner decision file, bound by sha256 in the gate state, opens the batch."""
    record=state.get('owner_batch_open') if isinstance(state,dict) else None
    if not isinstance(record,dict):return False
    relative,digest=record.get('decision_path'),record.get('sha256')
    if not isinstance(relative,str) or not isinstance(digest,str) or Path(relative).is_absolute():return False
    try:
        repo=Path(pilot.repo).resolve();path=public_input(repo/relative)
        return path.is_relative_to(repo) and path.is_file() and sha(path)==digest
    except (OSError,ValueError):return False


def check(pilot,paths,*,now=None):
    from ..autopilot import read,REPO
    marker=pilot.registry/'INGEST_GATE.json'
    # Isolated test registries opt into the same production policy explicitly.
    if pilot.repo!=REPO and not marker.exists():return None
    state=read(marker,{'schema':'l2_c63_ingest_gate_v1','canary_designs':[]})
    if owner_batch_open(pilot,state):return dict(state,phase='BATCH_OPEN')
    estimated=estimated_runs(pilot)
    ids=[read(p)['design_id'] for p in paths]
    removed=prescreen_parked(pilot,state['canary_designs'])
    canaries=[did for did in state['canary_designs'] if did not in removed]
    state=dict(state,canary_designs=canaries)
    if removed:state['prescreen_removed_canaries']=sorted(set(state.get('prescreen_removed_canaries',[]))|removed)
    if set(state['canary_designs']) & {r['design_id'] for r in estimated}:
        return dict(state,phase='BATCH_OPEN')
    if not estimated:
        authority=early_canary_authority(pilot,now)
        if canaries or authority is None:
            raise ValueError('C63 INGEST_PAUSED: first autopilot estimate required')
        if not 1<=len(ids)<=2 or len(set(ids))!=len(ids):
            raise ValueError('C70 early intake must contain 1-2 distinct canaries')
        return dict(state,canary_designs=ids,phase='EARLY_CANARIES',early_canary=authority)
    if not canaries:
        if not 1<=len(ids)<=2 or len(set(ids))!=len(ids):raise ValueError('C63 first intake must contain 1-2 distinct canaries')
        return dict(state,canary_designs=ids,phase='CANARIES',first_estimate_run_id=estimated[0]['run_id'])
    if not set(canaries)&{r['design_id'] for r in estimated}:
        raise ValueError('C63 canaries must finish successfully before full batch')
    return dict(state,phase='BATCH_OPEN')


def waiting_before_screen(pilot):
    """Active designs that have not yet reached `screen` or an S3 terminal: next action is history, reconcile or screen."""
    from ..autopilot import read
    count=0
    for row in read(pilot.registry/'ACTIVE_DESIGNS.json',{'designs':[]})['designs']:
        try:action=pilot.next_action(row)
        except Exception:continue   # an unreadable row is not counted as waiting
        if action in {'history','reconcile','screen'}:count+=1
    return count


def throttle(pilot,paths):
    """REDESIGN W3 (rules.intake.max_open_before_screen): a batch of cards that pin R3 or later is accepted only while the number of
    designs waiting before the screen, plus the new cards, stays within the limit. Cards of earlier rules versions are not counted
    or limited; the canary rule of `check` is unchanged. Returns None, or raises ValueError('INGEST_THROTTLED ...')."""
    from ..autopilot import read
    from .. import rules as rules_module
    new=0;limit=None
    for path in paths:
        rules=rules_module.of_payload(read(path) or {},pilot.repo)
        if rules_module.at_least(rules,3):
            new+=1;limit=rules['intake']['max_open_before_screen'] if limit is None else min(limit,rules['intake']['max_open_before_screen'])
    if not new:return None
    waiting=waiting_before_screen(pilot)
    if waiting+new>limit:
        raise ValueError('INGEST_THROTTLED: %d designs wait before screen and %d new cards would exceed max_open_before_screen %d'%(waiting,new,limit))
    return None
