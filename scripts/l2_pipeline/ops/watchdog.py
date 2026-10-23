"""Host-only restart gate. No start on import; fail closed on stale evidence."""
import argparse, contextlib, fcntl, json, os, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO=Path(__file__).resolve().parents[3]

def read(path,default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default

def atomic(path,value):
    from ..autopilot import atomic as save
    save(Path(path),value)

@contextlib.contextmanager
def guard(registry):
    registry=Path(registry);registry.mkdir(parents=True,exist_ok=True)
    with (registry/'OPS.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        yield


def clean_stop(repo):
    """Require a clean-stop event newer than every retained restart receipt."""
    registry=Path(repo)/'data/l2_pipeline/registry'
    events=[json.loads(s) for s in (registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
    stops=[e for e in events if e.get('decision')=='CLEAN_STOP']
    if not stops:raise ValueError('No CLEAN_STOP')
    stop=max(stops,key=lambda e:e['time']);ended=datetime.fromisoformat(stop['time'])
    starts=[e['time'] for e in events if e.get('decision')=='LOOP_START']
    for p in (Path(repo)/'data/l2_pipeline/autopilot').glob('*/RESTART_RECEIPT.json'):
        starts.append(read(p)['time'])
    receipt=read(registry/'WATCHDOG_RESTART.json',{})
    if receipt.get('time'):starts.append(receipt['time'])
    if any(datetime.fromisoformat(s)>=ended for s in starts):raise ValueError('Latest loop has no CLEAN_STOP')
    return stop


def crashed_loop(repo=REPO,now=None,grace_seconds=120):
    """The latest loop start, if that loop is gone without a CLEAN_STOP; else None.

    A start is a LOOP_START event, a restart receipt or WATCHDOG_RESTART.json.
    Crashed = newest start is later than the newest CLEAN_STOP, older than the
    launch grace, no code task owns the registry and the scheduler flock is free.
    Detection only: an abnormal exit is never restarted automatically (runbook 17).
    """
    from ..autopilot import scheduler_lock
    repo=Path(repo);registry=repo/'data/l2_pipeline/registry'
    now=now or datetime.now(timezone.utc)
    if (registry/'CODE_TASK.lock').exists():return None
    events=[json.loads(s) for s in (registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
    starts=[e['time'] for e in events if e.get('decision')=='LOOP_START']
    for path in (repo/'data/l2_pipeline/autopilot').glob('*/RESTART_RECEIPT.json'):starts.append(read(path)['time'])
    receipt=read(registry/'WATCHDOG_RESTART.json',{})
    if receipt.get('time'):starts.append(receipt['time'])
    if not starts:return None
    latest=max(starts,key=datetime.fromisoformat)
    stops=[e['time'] for e in events if e.get('decision')=='CLEAN_STOP']
    if stops and datetime.fromisoformat(max(stops,key=datetime.fromisoformat))>datetime.fromisoformat(latest):return None
    if (now-datetime.fromisoformat(latest)).total_seconds()<grace_seconds:return None
    try:
        with scheduler_lock(registry):pass
    except ValueError:return None  # a scheduler is alive
    return dict(latest_start=latest,loop_state=read(registry/'LOOP_STATE.json',{}),crash_marker=read(registry/'LOOP_CRASH.json'))


def crash_alert(repo=REPO,now=None):
    """Write LOOP_CRASH_ALERT.json once per crashed start; returns it when new."""
    repo=Path(repo);registry=repo/'data/l2_pipeline/registry'
    crashed=crashed_loop(repo,now)
    if crashed is None:return None
    alert_path=registry/'LOOP_CRASH_ALERT.json'
    if read(alert_path,{}).get('latest_start')==crashed['latest_start']:return None
    alert=dict(schema='l2_loop_crash_alert_v1',detected_at=(now or datetime.now(timezone.utc)).isoformat(),
               action='loop is gone without CLEAN_STOP; not restarted automatically; hub: set AUTOPILOT_STOP, run one loop pass to record CLEAN_STOP, then stop_and_begin_code_task or once',
               **crashed)
    atomic(alert_path,alert)
    return alert


def newest_report(repo):
    reports=list((Path(repo)/'data/l2_pipeline/autopilot').glob('*/TEST_REPORT*.json'))
    p=Path(repo)/'scripts/l2_pipeline/TEST_REPORT.json'
    if p.exists():reports.append(p)
    if not reports:raise ValueError('No TEST_REPORT')
    return max(reports,key=lambda p:(p.stat().st_mtime_ns,str(p)))


def eligible(repo=REPO,report=None):
    from ..registration import _check_evidence
    repo=Path(repo);registry=repo/'data/l2_pipeline/registry'
    if (registry/'CODE_TASK.lock').exists():raise ValueError('CODE_TASK.lock held')
    stop=clean_stop(repo)
    latest=newest_report(repo)
    if report is not None and Path(report).resolve()!=latest.resolve():raise ValueError('Not newest TEST_REPORT')
    evidence=_check_evidence(latest)
    runs=evidence['runs']
    if (not evidence.get('no_archival_fits') or
        any('--no-archival-fits' not in r.get('command',[]) for r in runs) or
        sorted('-O' in r.get('command',[]) for r in runs)!=[False,True]):
        raise ValueError('Both no-archival-fits modes required')
    return latest,stop


# Tick dispositions that cannot start work; any other count is runnable.
NON_RUNNABLE=frozenset({'PARK','STOP','FINISHED','PRESCREEN_PARK','GATE0_PARK'})


def idle_proof(registry,stop):
    """Latest completed tick before STOP, bound to the unchanged active registry.

    IDLE ledger notes are deduplicated; the tick snapshot prevents an older IDLE
    note from exempting a later busy window. Missing/stale evidence counts busy.
    """
    from ..storage import sha
    registry=Path(registry)
    try:
        path=registry/'LOOP_STATE.json';state=read(path,{})
        active=registry/'ACTIVE_DESIGNS.json'
        counts=state['next_counts'];tick=datetime.fromisoformat(state['time'])
        stopped=datetime.fromisoformat(stop['time'])
        requested=datetime.fromtimestamp((registry/'AUTOPILOT_STOP').stat().st_mtime,timezone.utc)
        if (state.get('schema')!='l2_loop_state_v1' or state.get('queued_jobs')!=0 or
            not isinstance(counts,dict) or not set(counts)<=NON_RUNNABLE or
            any(type(n) is not int or n<0 for n in counts.values()) or
            sum(counts.values())!=len(read(active)['designs']) or
            state.get('active_designs_sha256')!=sha(active) or
            not tick<=requested<=stopped or not 0<=(stopped-tick).total_seconds()<=600):return None
        note=None
        with (registry/'AUTOPILOT_LEDGER.jsonl').open() as stream:
            for line in stream:
                event=json.loads(line)
                stamp=datetime.fromisoformat(event['time'])
                if event.get('decision')=='LOOP_START' and tick<stamp<=stopped:return None
                if event.get('decision')=='IDLE' and stamp<=tick:note=event
        if note is None or note.get('blockers')!=counts:return None
        return dict(loop_state_sha256=sha(path),tick_time=state['time'],
                    active_designs_sha256=sha(active),next_counts=counts,queued_jobs=0,
                    stop_requested_at=requested.isoformat())
    except (OSError,ValueError,KeyError,TypeError):return None


def begin_code_task(task,repo=REPO,now=None,*,_guard_held=False):
    from ..autopilot import scheduler_lock
    registry=Path(repo)/'data/l2_pipeline/registry'
    now=now or datetime.now(timezone.utc)
    with (contextlib.nullcontext() if _guard_held else guard(registry)),scheduler_lock(registry):
        if not (registry/'AUTOPILOT_STOP').exists():raise ValueError('Set STOP and wait for CLEAN_STOP first')
        stop=clean_stop(repo)
        if (registry/'CODE_TASK.lock').exists():raise ValueError('Another code task owns CODE_TASK.lock')
        day=now.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
        history=read(registry/'MAINTENANCE_WINDOWS.json',{'windows':[]})
        proof=idle_proof(registry,stop);idle=proof is not None
        used={w['task'] for w in history['windows'] if w['day']==day}
        for receipt in (Path(repo)/'data/l2_pipeline/autopilot').glob('c*/RESTART_RECEIPT.json'):
            if datetime.fromisoformat(read(receipt)['time']).astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()==day:used.add(receipt.parent.name.upper())
        count=sum(w['day']==day and w.get('idle') is not True for w in history['windows'])+len(used-{w['task'] for w in history['windows']})
        override = read(registry/'MAINTENANCE_WINDOW_OVERRIDE.json', {})
        if isinstance(override, dict) and override.get('schema') == 'maintenance_window_override_v1':
            override = next((dict(entry, schema=override['schema'])
                for entry in [override, *override.get('additional', [])]
                if isinstance(entry, dict) and entry.get('day') == day and entry.get('task') == task), {})
        valid_override = (isinstance(override, dict) and
            override.get('schema') == 'maintenance_window_override_v1' and
            override.get('day') == day and override.get('task') == task and
            type(override.get('extra_windows')) is int and override['extra_windows'] > 0)
        consumed = sum(w.get('override_consumed', False) and w['day'] == day and
                       w['task'] == task for w in history['windows'])
        use_override = count >= 4 and not idle
        if use_override and (not valid_override or consumed >= override['extra_windows']):
            raise ValueError('Daily maintenance limit: 4 (no unused matching override)')
        token=uuid.uuid4().hex
        value=dict(task=task,token=token,time=now.isoformat(),day=day,clean_stop=stop,idle=idle)
        if idle:value['idle_proof']=proof
        if use_override:value['override_consumed']=True;value['override']=override
        with (registry/'CODE_TASK.lock').open('x') as f:json.dump(value,f)
        history['windows'].append(value);atomic(registry/'MAINTENANCE_WINDOWS.json',history)
        return value


def stop_and_begin_code_task(task,repo=REPO,timeout=600,poll_seconds=.25):
    """One command owns OPS.lock across STOP -> CLEAN_STOP -> code lock.

    No shared code is written here. A failed timeout keeps STOP intact; a
    maintenance limit rejection creates no code lock and grants no authority.
    """
    registry=Path(repo)/'data/l2_pipeline/registry'
    if not task or timeout<=0 or not 0<poll_seconds<=1:raise ValueError('Invalid stop/begin request')
    deadline=time.monotonic()+timeout
    with guard(registry):
        if (registry/'CODE_TASK.lock').exists():raise ValueError('Another code task owns CODE_TASK.lock')
        try:(registry/'AUTOPILOT_STOP').touch(exist_ok=False)
        except FileExistsError:pass  # Preserve idle proof's original request time.
        while True:
            try:
                clean_stop(repo)
                return begin_code_task(task,repo,_guard_held=True)
            except (BlockingIOError,ValueError) as exc:
                if isinstance(exc,ValueError) and str(exc) not in {'No CLEAN_STOP','Latest loop has no CLEAN_STOP','Another autopilot scheduler/ingest owns the global lock'}:raise
                if time.monotonic()>=deadline:raise TimeoutError('CLEAN_STOP/code lock wait timed out') from exc
                time.sleep(poll_seconds)


def end_code_task(token,repo=REPO):
    from ..registration import _check_evidence
    registry=Path(repo)/'data/l2_pipeline/registry'
    with guard(registry):
        lock=read(registry/'CODE_TASK.lock',{})
        if lock.get('token')!=token:raise ValueError('Code task ownership differs')
        report=newest_report(repo);_check_evidence(report)
        atomic(registry/'CODE_TASK_LAST_END.json',dict(task=lock['task'],report=str(report),time=datetime.now(timezone.utc).isoformat()))
        (registry/'CODE_TASK.lock').unlink()


def restart_once(repo=REPO,report=None,output=None):
    from ..agents.dispatch import require_host
    from ..autopilot import scheduler_lock
    from ..storage import sha
    require_host(True)
    repo=Path(repo);registry=repo/'data/l2_pipeline/registry'
    output=Path(output or repo/'data/l2_pipeline/autopilot/c63').resolve()
    if not output.is_relative_to(repo/'data/l2_pipeline/autopilot'):raise ValueError('Durable restart output required')
    with guard(registry),scheduler_lock(registry):
        report,stop=eligible(repo,report)
        # Record launch intent before spawning: an ambiguous/crashed launch is
        # never treated as another clean stop or silently retried.
        stamp=datetime.now(timezone.utc).isoformat();output.mkdir(parents=True,exist_ok=True)
        log=registry/'AUTOPILOT_C63.log'
        command=[str(repo/'.venv_did/bin/python'),'-B','-m','scripts.l2_pipeline.hub','autopilot','loop',
            '--max-workers','12','--agent-backends','sonnet_queue','--max-local-workers','2','--hub-outside-sandbox',
            '--test-report',str(report),'--time-limit','28800','--poll-seconds','15','--stop-file',str(registry/'AUTOPILOT_STOP')]
        receipt=dict(time=stamp,status='LAUNCH_INTENT',test_report=str(report),test_report_sha256=sha(report),
            clean_stop=stop,command=command,log=str(log),codex_max_workers=12,agent_backends='sonnet_queue',sonnet_worker_cap=None)
        atomic(registry/'WATCHDOG_RESTART.json',receipt)
        (registry/'AUTOPILOT_STOP').unlink(missing_ok=True)
    # Child takes scheduler lock itself. CODE_TASK cannot start without a newer
    # clean stop, even after the short parent lock handoff.
    with log.open('ab') as f:
        env=dict(os.environ)
        env['L2_CLAUDE_ONLY']='1'
        extra=Path.home()/'Library/Python/3.9/lib/python/site-packages'
        if extra.is_dir():env['PYTHONPATH']=str(extra)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        child=subprocess.Popen(command,cwd=repo,stdout=f,stderr=subprocess.STDOUT,start_new_session=True,env=env)
    receipt.update(status='STARTED',scheduler_pid=child.pid)
    atomic(registry/'WATCHDOG_RESTART.json',receipt);atomic(output/'RESTART_RECEIPT.json',receipt)
    return receipt


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['check','once','watch','begin_code_task','stop_and_begin_code_task','end_code_task'])
    p.add_argument('--task');p.add_argument('--token');p.add_argument('--report',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--poll-seconds',type=float,default=15);a=p.parse_args()
    if not 0<a.poll_seconds<=60:p.error('poll 0..60 seconds')
    if a.action=='stop_and_begin_code_task':print(json.dumps(stop_and_begin_code_task(a.task or 'unnamed')));return
    if a.action=='begin_code_task':print(json.dumps(begin_code_task(a.task or 'unnamed')));return
    if a.action=='end_code_task':end_code_task(a.token);return
    if a.action=='check':print(eligible(report=a.report)[0]);return
    while True:
        if a.action=='watch':
            try:
                alert=crash_alert()
                if alert:print('ALERT: loop crashed (started '+alert['latest_start']+', no CLEAN_STOP); see LOOP_CRASH_ALERT.json',flush=True)
            except Exception as exc:print('ALERT-CHECK-FAILED: '+type(exc).__name__+': '+str(exc),flush=True)
        try:print(json.dumps(restart_once(report=a.report,output=a.output)),flush=True)
        except (OSError,ValueError) as exc:
            if a.action=='once':raise
            print('WAIT: '+str(exc),flush=True)
        if a.action=='once':return
        time.sleep(a.poll_seconds)

if __name__=='__main__':main()
