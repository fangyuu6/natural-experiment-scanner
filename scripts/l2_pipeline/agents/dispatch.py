#!/usr/bin/env python3
"""Host-only dispatcher. Pipeline stages emit jobs and never import this runner."""
from __future__ import annotations
import argparse, concurrent.futures, fcntl, json, os, re, shlex, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from scripts.l2_pipeline.agents.jobs import CODEX, load_job
from scripts.l2_pipeline.agents.contract_tests import check_job_types
from scripts.l2_pipeline.storage import write_once, public_input


def command(job, codex=CODEX):
    argv=[codex]+(["--search"] if job.network_access else [])
    argv += ["exec","-C",job.repo,"-s","workspace-write"]
    slot=job.contract.get('forecast_slot')
    if slot:
        from .sonnet_queue import route
        if slot.get('executor')!='codex' or route(job.design_id,job.role,job.contract.get('role_backend_overrides'))!='codex':
            raise ValueError('Forecast slot requires its registered executor override')
        model=slot.get('model_id')
        if not isinstance(model,str) or not model.strip():raise ValueError('Codex forecast slot model identity missing')
        argv += ['--model',model]
    if job.network_access: argv += ["-c","sandbox_workspace_write.network_access=true"]
    return argv+["--color","never","-o",job.result_path,"-"]


def preflight(jobs, codex=CODEX):
    """Check installed flag scopes and parse each argv shape using help only.

    Every invocation includes --help and closed stdin: no agent/model call,
    output reservation, or result write is made by this check.
    """
    def help_text(argv):
        try:
            result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,text=True,timeout=15,check=False)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError("Codex CLI preflight failed: "+str(exc)) from exc
        if result.returncode!=0 or "Usage:" not in result.stdout:
            raise ValueError("Codex CLI preflight rejected "+shlex.join(argv)+": "+result.stderr.strip())
        return result.stdout
    top=help_text([codex,"--help"])
    execution=help_text([codex,"exec","--help"])
    if any(j.network_access for j in jobs) and not re.search(r"(?m)^\s+--search\s*$",top):
        raise ValueError("Codex CLI preflight: top-level --search missing")
    for flag in ("-C","-s","-c","--color","-o"):
        if not re.search(r"(?m)^\s+"+re.escape(flag)+r"(?:,|\s)",execution):
            raise ValueError("Codex CLI preflight: exec flag missing: "+flag)
    # Same flags/values for every job of a shape; validate one of each shape.
    shapes={}
    for job in jobs: shapes.setdefault(job.network_access,job)
    for job in shapes.values():
        argv=command(job,codex)
        help_text(argv[:-1]+["--help",argv[-1]])


def printable(job,codex=CODEX):
    log=Path(job.output_dir)/"dispatch.log"
    return shlex.join(command(job,codex))+" < "+shlex.quote(job.job_path)+" > "+shlex.quote(str(log))+" 2>&1"


def _run(path,codex):
    # Validate bindings again immediately before reserving the one authorized attempt.
    job=load_job(path)
    from .history_packet import check as check_history_packet
    check_history_packet(job)
    out=Path(job.output_dir);out.mkdir(parents=True,exist_ok=True)
    if Path(job.output_path).exists() or Path(job.result_path).exists(): raise FileExistsError("Agent output already exists; no retry: "+job.job_path)
    from . import liveness
    reservation=out/"DISPATCH_ONCE.json"
    started=time.perf_counter();started_at=datetime.now(timezone.utc).isoformat()
    value={"job_sha256":job.job_sha256,"argv":command(job,codex),"retries":0,"host_assertion":True,"started_at":started_at,"host_pid":os.getpid(),"host_start":liveness.identity(os.getpid())}
    reserve_global(job,reservation,value)
    log=out/"dispatch.log"
    try:
        with open(job.job_path,"rb") as prompt,log.open("xb") as stream:
            process=subprocess.run(command(job,codex),stdin=prompt,stdout=stream,stderr=subprocess.STDOUT,check=False)
        if process.returncode==0 and job.contract.get("collector_self_check_required"):
            from .self_check import receipt
            receipt(job.job_path)
        result={"job_path":job.job_path,"role":job.role,"exit_code":process.returncode,"status":"COMPLETED" if process.returncode==0 else "FAILED_NO_RETRY","log":str(log),"retries":0}
    except Exception as exc:
        result={"job_path":job.job_path,"role":job.role,"status":"FAILED_NO_RETRY","error":type(exc).__name__+": "+str(exc),"log":str(log),"retries":0}
    result.update(started_at=started_at,completed_at=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-started)
    write_once(out/"DISPATCH_RESULT.json",result);return result


def reserve_global(job,reservation,value):
    """Cooperating host dispatchers share the autopilot's GLOBAL reservation cap.

    Legacy waves without an autopilot budget retain their original behavior.
    Waiting here consumes no Codex worker/model call and retries no job.
    """
    registry=Path(job.repo)/'data/l2_pipeline/registry'
    budget_path=registry/'AUTOPILOT_WORKER_BUDGET.json'
    if not budget_path.exists():
        write_once(reservation,value);return
    while True:
        with (registry/'AUTOPILOT_WORKERS.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            budget=json.loads(budget_path.read_text())['max_workers']
            if type(budget) is not int or budget<1:raise ValueError('Invalid global worker budget')
            from .liveness import running as live_running
            running=live_running(job.repo)
            if running<budget:
                write_once(reservation,value);return
        time.sleep(1)


def require_host(assertion):
    if not assertion:
        raise ValueError('Execution requires --hub-outside-sandbox')
    if os.environ.get('CODEX_SANDBOX') or os.environ.get('CODEX_SANDBOX_NETWORK_DISABLED') or os.environ.get('L2_PIPELINE_SANDBOX'):
        raise ValueError('Detected sandbox environment; run from the hub outside the sandbox')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-root",type=Path,default=Path("data/l2_pipeline/jobs"));parser.add_argument("--job",action="append",type=Path)
    parser.add_argument("--stage",action="append",choices=("S0L","S0","S1","S5","S6"));parser.add_argument("--design",action="append")
    parser.add_argument("--attempt-id",help="Select only jobs bound to this explicit attempt")
    parser.add_argument("--max-concurrency",type=int,default=4,help="Maximum simultaneous jobs: 1..4 (default 4)");parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--preflight-only",action="store_true",help="Validate CLI flags using help only; never launch agents")
    parser.add_argument("--hub-outside-sandbox",action="store_true",help="Hub confirms this process is running outside the pipeline/agent sandbox")
    parser.add_argument('--scheduled-worker',action='store_true',help='One assigned worker; no scheduling or pool')
    parser.add_argument("--codex",default=CODEX);args=parser.parse_args(argv)
    if not 1<=args.max_concurrency<=4: parser.error("max concurrency must be 1..4")
    paths=args.job or sorted(args.jobs_root.glob("*/*/*.md"))
    if args.attempt_id:
        paths=[p for p in paths if p.name.endswith("."+args.attempt_id+".md")]
    jobs=[];failures=[]
    for p in paths:
        try:
            # Discovery can include completed/stale jobs from other waves. Apply
            # requested selectors before their strict, potentially obsolete bindings.
            sidecar=p.with_suffix(".job.json") if p.suffix==".md" else p
            metadata=json.loads(sidecar.read_text())
            if isinstance(metadata,dict) and (
                    (args.stage and metadata.get("stage") not in args.stage) or
                    (args.design and metadata.get("design_id") not in args.design)):
                continue
            j=load_job(p)
            if ((not args.stage or j.stage in args.stage) and
                    (not args.design or j.design_id in args.design) and
                    (not args.attempt_id or j.contract.get("attempt_id")==args.attempt_id)):
                jobs.append((p,j))
        except Exception as exc:
            failures.append({"job_path":str(p),"status":"FAILED_VALIDATION_NO_RETRY",
                             "error":type(exc).__name__+": "+str(exc),"retries":0})
    if not jobs:
        if failures:
            print(json.dumps({"jobs":failures,"retries":0},indent=2));return 1
        parser.error("no matching agent jobs")
    # Contract failures are wave-level failures: no selected job may launch.
    try: check_job_types([j for _,j in jobs])
    except Exception as exc: parser.error("Job contract preflight failed: "+str(exc))
    counts={}
    for _,j in jobs:
        for key in (("job",j.job_path),("output",j.output_dir)):
            counts[key]=counts.get(key,0)+1
    independent=[]
    for p,j in jobs:
        if counts[("job",j.job_path)]>1 or counts[("output",j.output_dir)]>1:
            failures.append({"job_path":str(p),"design_id":j.design_id,
                             "status":"FAILED_VALIDATION_NO_RETRY",
                             "error":"Duplicate jobs/output directories refuse; roles must stay independent","retries":0})
        else: independent.append((p,j))
    jobs=independent
    if not jobs:
        print(json.dumps({"jobs":failures,"retries":0},indent=2));return 1
    if args.dry_run or args.preflight_only:
        try: preflight([j for _,j in jobs],args.codex)
        except ValueError as exc: parser.error(str(exc))
    if args.preflight_only:
        payload={"preflight":"PASS","jobs":len(jobs),"model_calls":0}
        if failures: payload["failures"]=failures
        print(json.dumps(payload));return int(bool(failures))
    if args.dry_run:
        for _,job in jobs: print(printable(job,args.codex))
        if failures: print(json.dumps({"failures":failures}),file=sys.stderr)
        return int(bool(failures))
    if not args.hub_outside_sandbox: parser.error("Execution requires --hub-outside-sandbox; pipeline/agent stages must never dispatch")
    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") or os.environ.get("L2_PIPELINE_SANDBOX"):
        parser.error("Detected sandbox environment; run dispatcher from the hub outside the sandbox")
    # Preflight all jobs before any launch; existing reservation means no repeat attempts.
    ready=[]
    for p,j in jobs:
        if any((Path(j.output_dir)/n).exists() for n in ("DISPATCH_ONCE.json","dispatch.log","DISPATCH_RESULT.json")) or Path(j.output_path).exists() or Path(j.result_path).exists():
            failures.append({"job_path":j.job_path,"design_id":j.design_id,
                             "status":"FAILED_VALIDATION_NO_RETRY",
                             "error":"Reserved/existing output; retries=0","retries":0})
        else: ready.append((p,j))
    jobs=ready
    if not jobs:
        print(json.dumps({"jobs":failures,"retries":0},indent=2));return 1
    try: preflight([j for _,j in jobs],args.codex)
    except ValueError as exc: parser.error(str(exc))
    if args.scheduled_worker:
        if len(jobs)!=1 or failures or args.max_concurrency!=1:
            parser.error('Scheduled worker requires exactly one fresh valid job')
        result=_run(jobs[0][0],args.codex)
        print(json.dumps({'jobs':[result],'retries':0},indent=2))
        return int(result['status']!='COMPLETED')
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrency) as pool:
        futures=[(j,pool.submit(_run,p,args.codex)) for p,j in jobs]
        results=[]
        for job,future in futures:
            try: results.append(future.result())
            except Exception as exc:
                results.append({"job_path":job.job_path,"design_id":job.design_id,"role":job.role,
                                "status":"FAILED_NO_RETRY","error":type(exc).__name__+": "+str(exc),"retries":0})
    results=failures+results
    print(json.dumps({"jobs":results,"retries":0},indent=2))
    return int(any(r["status"]!="COMPLETED" for r in results))

if __name__=="__main__": raise SystemExit(main())
