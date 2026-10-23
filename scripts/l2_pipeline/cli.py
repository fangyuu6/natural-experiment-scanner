"""Stage CLI. Preparation failures retain per-design receipts and batch dispositions."""
from __future__ import annotations
import argparse, importlib, json, sys, time, traceback
from pathlib import Path
from .cards import load_card
from .contracts import Disposition, StageContext, StageResult
from .storage import encoded, output_root, public_input, sha, write_once, ensure_once

STAGES={"S0":"s0_intake","S1":"s1_treatment","S2":"s2_outcomes","S3":"s3_support","S4":"s4_specs","S5":"s5_registration","S6":"s6_execute","S7":"s7_export"}
REQUIRED={"S1":("S0",),"S2":("S1",),"S3":("S1","S2"),"S4":("S3",),"S5":("S4",),"S6":("S5",),"S7":("S6",)}

def preflight(ctx: StageContext, stage: str) -> None:
    for previous in REQUIRED.get(stage,()):
        receipts=sorted((ctx.design_dir/"receipts"/previous).glob("*.json"))
        if not receipts: raise ValueError("Missing prior stage receipt: "+previous)
        receipt=json.loads(receipts[-1].read_text())
        if receipt["card_sha256"]!=sha(ctx.card.path): raise ValueError("Card changed; use a new version")
        result=receipt["result"]
        terminal_export=(stage=="S7" and previous=="S6" and
                         any((ctx.design_dir/n).is_file() for n in ("TERMINAL.json","FAILURE.json")))
        if result["disposition"]!="ready" and not terminal_export: raise ValueError("Prior stage not ready: "+previous)
        for artifact in result["artifacts"]:
            path=Path(artifact["path"])
            if not path.resolve().is_relative_to(ctx.design_dir.resolve()) or sha(path)!=artifact["sha256"]: raise ValueError("Prior artifact changed/outside design root")
    imported=ctx.design_dir/"S1_IMPORT_PROVENANCE.json"
    if imported.is_file():
        proof=json.loads(imported.read_text())
        for evidence in proof["evidence"]:
            path=public_input(ctx.repo/evidence["path"])
            matches=path.is_relative_to(ctx.repo.resolve()) and sha(path)==evidence["sha256"]
            if not matches and path.is_relative_to((ctx.repo/"scripts/l2_pipeline").resolve()) and path.suffix==".py":
                archive_path=ctx.design_dir/"S1_CODE_ARCHIVE.json"
                archived=json.loads(archive_path.read_text()).get("files",{}).get(str(path),{}) if archive_path.is_file() else {}
                archived_path=public_input(Path(archived.get("path","")))
                matches=(archived.get("sha256")==evidence["sha256"] and archived_path.is_file()
                         and archived_path.is_relative_to(ctx.repo.resolve()) and sha(archived_path)==evidence["sha256"])
            if not matches:
                raise ValueError("Archival S1 source/coder evidence changed: "+evidence["path"])

def run_stage(ctx: StageContext, stage: str) -> StageResult:
    try:
        if stage == 'S6':
            from . import snapshots
            frozen = snapshots.manifest_for(ctx.design_dir)
            if frozen and snapshots.active() is None:
                value = snapshots.call(ctx.design_dir, frozen, 'run_stage', dict(context=snapshots.context_payload(ctx)))
                from .contracts import Artifact
                return StageResult(value['stage'], value['design_id'], Disposition(value['disposition']),
                                   value['reason'], value.get('reopen_condition'),
                                   tuple(Artifact(**a) for a in value.get('artifacts', [])), value.get('metrics', {}))
        if stage not in ("S0","S1"):
            from .run2_preparation import effective_context
            ctx=effective_context(ctx)
        preflight(ctx,stage)
        result=importlib.import_module("scripts.l2_pipeline.stages."+STAGES[stage]).run(ctx)
    except Exception as exc:
        result=StageResult(stage,ctx.card.design_id,Disposition.FAILED,str(exc),"Repair preparation in a new version; consumed official runs remain nonretryable",metrics={"exception_type":type(exc).__name__,"traceback":traceback.format_exc()})
    write_once(ctx.design_dir/"receipts"/stage/(str(time.time_ns())+".json"),{"card_sha256":sha(ctx.card.path),"result":result.as_dict()})
    return result

def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    validate=sub.add_parser("validate"); validate.add_argument("card",type=Path)
    run=sub.add_parser("run"); run.add_argument("--stage",choices=STAGES,required=True)
    selection=run.add_mutually_exclusive_group(required=True); selection.add_argument("--design",type=Path); selection.add_argument("--batch",type=Path)
    run.add_argument("--work-dir",type=Path,default=Path("scripts/l2_pipeline/.work"))
    intake=sub.add_parser("intake"); intake.add_argument("--queue",type=Path,required=True); intake.add_argument("--defaults",type=Path,required=True); intake.add_argument("--output",type=Path,required=True)
    sub.add_parser("connectors")
    jobs=sub.add_parser("jobs",help="Emit agent jobs only; never launch agents")
    jobs.add_argument("--stage",choices=("S0","S1","S5","S6"),required=True)
    jobs.add_argument("--design",type=Path,required=True)
    jobs.add_argument("--work-dir",type=Path,default=Path("data/l2_pipeline/acceptance/work"))
    jobs.add_argument("--allow-draft",action="store_true",help="S6 preparation review, without execution approval")
    jobs.add_argument("--jobs-root",type=Path)
    jobs.add_argument("--attempt-id")
    jobs.add_argument("--source-pack",type=Path)
    packs=sub.add_parser("source-packs",help="Non-agent fetch/archive stage; never launches a coder")
    packs.add_argument("--batch",type=Path,required=True)
    packs.add_argument("--design-id",action="append",help="Prepare only named designs; preserve prior immutable packets")
    packs.add_argument("--candidates",type=Path,help="Reviewed per-design source certifications")
    packs.add_argument("--work-dir",type=Path,required=True)
    packs.add_argument("--jobs-root",type=Path,required=True)
    packs.add_argument("--attempt-id",default="attempt3")
    packs.add_argument("--output",type=Path,required=True)
    check=sub.add_parser("check",help="Record normal and optimized pipeline regression evidence")
    check.add_argument("--output",type=Path,required=True)
    check.add_argument("--no-archival-fits",action="store_true",help="Prospective checks exclude the real archival fit; invented fixtures still run")
    registration=sub.add_parser("registration",help="Non-agent lock, approval, calibration and seal steps")
    registration.add_argument("--action",choices=("lock","resolve-draft","prep","approve-prep","calibrate","seal","dry-load","approve-final","report"),required=True)
    selection=registration.add_mutually_exclusive_group(required=True)
    selection.add_argument("--design",type=Path); selection.add_argument("--batch",type=Path)
    registration.add_argument("--work-dir",type=Path,required=True)
    registration.add_argument("--signer")
    registration.add_argument("--signed-at")
    registration.add_argument("--review-attempt")
    registration.add_argument("--resolution",type=Path,help="Evidence-bound per-finding hub draft closure; grants no execution authority")
    registration.add_argument("--test-report",type=Path)
    registration.add_argument("--output",type=Path)
    collect=sub.add_parser("collect",help="Validate job outputs and promote stage contracts")
    collect.add_argument("--job",type=Path,required=True)
    collect.add_argument("--allow-historical-draft",action="store_true",help="Retain completed S6 draft findings after code changes; requires fresh sealed review")
    reconcile_s1=sub.add_parser("reconcile-s1",help="Non-agent reconciliation and scoped follow-up jobs; never dispatch")
    reconcile_s1.add_argument("--jobs-root",type=Path,required=True)
    reconcile_s1.add_argument("--work-dir",type=Path,required=True)
    reconcile_s1.add_argument("--ruling",type=Path,required=True)
    reconcile_s1.add_argument("--attempt-id",required=True)
    args=parser.parse_args(argv); repo=Path(__file__).resolve().parents[2]
    try:
        if args.command=="reconcile-s1":
            from .stages.s1_reconciliation import reconcile_batch
            print(json.dumps(reconcile_batch(args,repo),indent=2));return 0
        if args.command=="check":
            from .registration import check_tests
            print(json.dumps(check_tests(output_root(args.output,repo),no_archival_fits=args.no_archival_fits),indent=2)); return 0
        if args.command=="registration":
            from .registration import command
            print(json.dumps(command(args,repo),indent=2)); return 0
        if args.command=="validate":
            card=load_card(args.card); print(encoded({"valid":True,"design_id":card.design_id}).decode()); return 0
        if args.command=="connectors":
            from .connectors import connector_inventory
            print(json.dumps(connector_inventory(),indent=2)); return 0
        if args.command=="jobs":
            started=time.perf_counter()
            from .agents import emit_stage_jobs
            from dataclasses import asdict
            workspace=output_root(args.work_dir,repo)
            ctx=StageContext(load_card(args.design),workspace,repo)
            if args.stage!="S1":
                from .run2_preparation import effective_context
                ctx=effective_context(ctx)
            # A draft reviewer receives the prepared S4 package, not a final gate bypass.
            if args.allow_draft:
                if args.stage!="S6": raise ValueError("Draft mode is limited to S6 preparation review")
                preflight(ctx,"S5")
            else:
                preflight(ctx,args.stage)
            emitted=emit_stage_jobs(ctx,args.stage,allow_draft=args.allow_draft,
                jobs_root=output_root(args.jobs_root,repo) if args.jobs_root else None,attempt_id=args.attempt_id,
                source_pack_path=args.source_pack)
            if args.jobs_root:
                write_once(workspace/"throughput"/("jobs-"+str(time.time_ns())+".json"),
                    {"action":"jobs-"+ctx.card.design_id,"wall_seconds":time.perf_counter()-started,
                     "dispatched":False,"jobs":[asdict(j) for j in emitted]})
            print(json.dumps({"dispatched":False,"jobs":[asdict(j) for j in emitted]},indent=2)); return 0
        if args.command=="source-packs":
            from .source_packs import build_source_pack
            from .agents.jobs import _safe_component
            _safe_component(args.attempt_id)
            workspace=output_root(args.work_dir,repo);jobs_root=output_root(args.jobs_root,repo)
            candidates=json.loads(public_input(args.candidates).read_text()) if args.candidates else {}
            results=[];downloads=[];emitted_count=0
            for path in sorted(args.batch.glob("*.json")):
                card=load_card(path);ctx=StageContext(card,workspace,repo)
                if args.design_id and card.design_id not in args.design_id: continue
                s0=run_stage(ctx,"S0")
                if s0.disposition!=Disposition.READY: raise ValueError("S0 intake failed: "+s0.reason)
                pack_dir=ctx.design_dir/"source_packs"/args.attempt_id
                manifest=build_source_pack(card,repo=repo,pack_dir=pack_dir,candidates=candidates.get(card.design_id))
                pointer={"manifest_path":str((pack_dir/"manifest.json").resolve()),"manifest_sha256":sha(pack_dir/"manifest.json"),"attempt_id":args.attempt_id,"jobs_root":str(jobs_root)}
                ensure_once(ctx.design_dir/"SOURCE_PACK.json",pointer)
                s1=run_stage(ctx,"S1")
                job_manifest=ctx.design_dir/"S1_AGENT_JOBS.json"
                if job_manifest.is_file(): emitted_count+=len(json.loads(job_manifest.read_text())["job_paths"])
                results.append({"design_id":card.design_id,"source_pack":pointer,"pack_status":manifest["status"],"reason":manifest["reason"],"s0":s0.as_dict(),"s1":s1.as_dict()})
                downloads.extend(manifest.get("downloads",[]))
            ready=sum(r["pack_status"]=="READY" for r in results)
            report={"schema_version":"1.0","attempt_id":args.attempt_id,"cards":len(results),"packs_built":ready,"parked":len(results)-ready,"coder_jobs":emitted_count,"dispatched":False,"model_calls":0,"results":results,"downloads":downloads}
            ensure_once(output_root(args.output,repo),report)
            print(json.dumps(report,indent=2));return 1 if any(r["s1"]["disposition"]=="failed_no_retry" for r in results) else 0
        if args.command=="collect":
            from .agents import collect_job
            result=collect_job(args.job,allow_historical_draft=args.allow_historical_draft);print(json.dumps(result,indent=2)); return 0 if result.get("validated") else 1
        if args.command=="intake":
            from .stages.s0_intake import card_from_queue
            output=output_root(args.output,repo); defaults=json.loads(args.defaults.read_text()); version=json.loads((args.queue/"summary.json").read_text())["version"]
            count=0; seen=set()
            for name in ("did_designs.jsonl","rdd_candidates.jsonl","rct_mentions.jsonl"):
                with (args.queue/name).open() as handle:
                    for line in handle:
                        value=card_from_queue(json.loads(line),defaults,version)
                        if value["design_id"] in seen: raise ValueError("Duplicate queue design")
                        seen.add(value["design_id"]); write_once(output/(value["design_id"]+".json"),value); count+=1
            print(encoded({"cards":count,"output":str(output)}).decode()); return 0
        workspace=output_root(args.work_dir,repo)
        paths=[args.design] if args.design else sorted(p for p in args.batch.iterdir() if p.is_file() and p.suffix.lower() in {".json",".yaml",".yml"})
        if not paths: raise ValueError("No design cards in batch directory")
        seen=set(); results=[]; aborted_after=None
        for path in paths:
            try:
                card=load_card(path)
                identity=(card.design_id,card.version)
                if identity in seen: raise ValueError("Duplicate design/version in batch")
                seen.add(identity)
                ctx=StageContext(card,workspace,repo)
                if args.stage=="S6" and aborted_after is not None:
                    from .registered import not_run_result
                    reason="Batch aborted after unexpected execution failure: "+aborted_after
                    result=not_run_result(ctx.read("SPEC.json"),reason)
                    write_once(ctx.design_dir/"RESULT.json",result)
                    write_once(ctx.design_dir/"TERMINAL.json",{"status":"NOT_RUN","reason":reason})
                    stage_result=StageResult("S6",card.design_id,Disposition.PARKED,reason)
                    write_once(ctx.design_dir/"receipts"/"S6"/(str(time.time_ns())+".json"),{"card_sha256":sha(card.path),"result":stage_result.as_dict()})
                else:
                    stage_result=run_stage(ctx,args.stage)
                    if args.stage=="S6" and stage_result.disposition==Disposition.FAILED and (ctx.design_dir/"FAILURE.json").is_file():
                        aborted_after=card.design_id
                results.append(stage_result.as_dict())
            except (OSError,ValueError) as exc:
                results.append({"stage":args.stage,"card":str(path),"disposition":"failed_no_retry","reason":str(exc)})
        print(json.dumps({"stage":args.stage,"results":results},indent=2))
        return 1 if any(r["disposition"]=="failed_no_retry" for r in results) else 2 if any(r["disposition"]!="ready" for r in results) else 0
    except (OSError,ValueError) as exc:
        parser.exit(1,str(exc)+"\n")

if __name__=="__main__": raise SystemExit(main())
