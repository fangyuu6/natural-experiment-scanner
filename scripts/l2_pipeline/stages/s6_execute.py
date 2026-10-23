"""Candidate-bound review, final approval and once-only registered execution."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .agent_jobs import prepare
from ..contracts import Artifact, Disposition, StageContext, StageResult
from ..freeze import verify
from ..storage import artifact, encoded, local, sha

def review_passed(review: object, candidate_sha256: str, design_id: str, *, closed_should=()) -> bool:
    should=review.get("should") if isinstance(review,dict) else None
    if not isinstance(should,list): return False
    if any(not isinstance(f,dict) or any(not isinstance(f.get(k),str) or not f[k].strip()
            for k in ("id","description","evidence","impact")) for f in should): return False
    if len({f["id"] for f in should})!=len(should): return False
    hashes=[hashlib.sha256(encoded(f)).hexdigest() for f in should]
    if len(set(hashes))!=len(hashes) or set(hashes)!=set(closed_should): return False
    return (isinstance(review,dict) and review.get("candidate_sha256")==candidate_sha256
            and review.get("design_id")==design_id and review.get("review_scope")=="sealed_candidate"
            and review.get("independence_attestation")=={"wrote_implementation":False,"wrote_fixes":False,"real_outcome_associations_read":False}
            and all(v is False for v in review.get("independence_attestation",{}).values())
            and type(review.get("complete_sweeps")) is int and review["complete_sweeps"]==1
            and review.get("must")==[] and review.get("verdict")=="PASS"
            and type(review.get("fix_cycles")) is int and 0<=review["fix_cycles"]<=2)

def single_review_mode(root):
    """True when the design's frozen rules snapshot (RULES_SNAPSHOT.json, written at lock) says one pre-run review (R2)."""
    path=local(root,"RULES_SNAPSHOT.json")
    return path.is_file() and json.loads(path.read_text()).get("review")=={"mode":"single"}

def single_review_evidence(root, candidate_sha256, design_id, hub_signer):
    """REDESIGN R2: the one independent review is the draft-readiness review in REVIEW.json, bound to the frozen SPEC.

    PASS (with every SHOULD closed through the same rule as a sealed review) or an evidence-backed hub resolution of its
    findings (status RESOLVED_SINGLE_REVIEW, validated when REVIEW.json was written). No EXECUTION_REVIEW.json exists or is needed."""
    manifest_path=local(root,"FREEZE_FINAL.json")
    if sha(manifest_path)!=candidate_sha256: raise ValueError("Single review candidate changed")
    files=json.loads(manifest_path.read_text())["files"]
    review_path=local(root,"REVIEW.json")
    if files.get("REVIEW.json",{}).get("sha256")!=sha(review_path): raise ValueError("REVIEW.json differs from the frozen candidate")
    spec_sha=files.get("SPEC.json",{}).get("sha256")
    if not spec_sha or sha(local(root,"SPEC.json"))!=spec_sha: raise ValueError("SPEC.json differs from the frozen candidate")
    review=json.loads(review_path.read_text())
    if not isinstance(review,dict) or review.get("candidate_sha256")!=spec_sha or review.get("design_id")!=design_id:
        raise ValueError("Single review is not bound to the frozen specification")
    result={"review_sha256":sha(review_path),"review_mode":"single"}
    if review.get("status")=="RESOLVED_SINGLE_REVIEW":
        resolution=local(root,Path(review.get("resolution_path","")).name)
        if (review.get("review_scope")!="draft_readiness" or review.get("independent_verdict") not in {"PASS","STOP"}
                or review.get("execution_authorized") is not False or review.get("resolution_sha256")!=sha(resolution)
                or files.get(resolution.name,{}).get("sha256")!=sha(resolution)):
            raise ValueError("Single review hub resolution is not frozen with the candidate")
        value=json.loads(resolution.read_text())
        if not hub_signer or value.get("hub_signer")!=hub_signer: raise ValueError("Single review resolution signer differs")
        result["review_resolution_sha256"]=sha(resolution)
        return result
    candidate=dict(review,review_scope="sealed_candidate")
    if review.get("review_scope")!="draft_readiness" or not review_passed(candidate,spec_sha,design_id):
        raise ValueError("Single independent PASS or hub resolution required before final signature")
    return result

def sealed_review_evidence(root, candidate_sha256, design_id, hub_signer):
    """Require independent PASS and exact, evidence-backed closure of any SHOULD.

    The review stays immutable. Closure evidence must already be in the frozen
    candidate, and the later hub signature binds this separate closure's bytes.
    """
    if single_review_mode(root): return single_review_evidence(root,candidate_sha256,design_id,hub_signer)
    review_path=local(root,"EXECUTION_REVIEW.json")
    review=json.loads(review_path.read_text())
    result={"review_sha256":sha(review_path)}
    if review_passed(review,candidate_sha256,design_id): return result
    should=review.get("should") if isinstance(review,dict) else None
    if not isinstance(should,list) or not should:
        raise ValueError("Sealed candidate independent PASS required before final signature")
    hashes=[hashlib.sha256(encoded(f)).hexdigest() for f in should]
    if not review_passed(review,candidate_sha256,design_id,closed_should=hashes):
        raise ValueError("Sealed candidate independent PASS required before final signature")
    path=local(root,"EXECUTION_REVIEW_RESOLUTION.json")
    value=json.loads(path.read_text())
    if (value.get("schema")!="l2_sealed_review_resolution_v1" or value.get("design_id")!=design_id
            or value.get("candidate_sha256")!=candidate_sha256
            or value.get("review_sha256")!=result["review_sha256"]
            or not hub_signer or value.get("hub_signer")!=hub_signer
            or value.get("outcome_associations_read") is not False
            or value.get("execution_authorized") is not False):
        raise ValueError("Sealed SHOULD closure identity/candidate binding missing")
    manifest_path=local(root,"FREEZE_FINAL.json")
    if sha(manifest_path)!=candidate_sha256: raise ValueError("Sealed closure candidate changed")
    files=json.loads(manifest_path.read_text())["files"]
    records=value.get("resolutions")
    if not isinstance(records,list) or len(records)!=len(should):
        raise ValueError("Every sealed SHOULD needs an explicit closure")
    seen=set()
    for record in records:
        digest=record.get("finding_sha256")
        matches=[f for f,h in zip(should,hashes) if h==digest]
        if (digest in seen or len(matches)!=1 or record.get("id")!=matches[0].get("id")
                or record.get("bucket")!="should" or record.get("status") not in ("resolved","declined")
                or not isinstance(record.get("reason"),str) or not record["reason"].strip()
                or not isinstance(record.get("evidence"),list) or not record["evidence"]):
            raise ValueError("Invalid/unclosed sealed SHOULD finding")
        seen.add(digest)
        for evidence in record["evidence"]:
            relative=evidence.get("path","")
            evidence_path=local(root,relative)
            from ..closure_evidence import validate
            validate(evidence,root,frozen_files=files,root=root)
            if (relative not in files or evidence.get("sha256")!=files[relative]["sha256"]
                    or not evidence_path.is_file() or sha(evidence_path)!=evidence["sha256"]):
                raise ValueError("Sealed SHOULD evidence absent/changed outside frozen candidate")
    result["review_resolution_sha256"]=sha(path)
    return result

def run(ctx: StageContext) -> StageResult:
    from ..run2_preparation import effective_context
    ctx=effective_context(ctx)
    from .. import snapshots
    frozen = snapshots.manifest_for(ctx.design_dir)
    if frozen and snapshots.active() is None:
        value = snapshots.call(ctx.design_dir, frozen, 'run', dict(context=snapshots.context_payload(ctx)))
        return StageResult(value['stage'], value['design_id'], Disposition(value['disposition']),
                           value['reason'], value.get('reopen_condition'),
                           tuple(Artifact(**a) for a in value.get('artifacts', [])),
                           value.get('metrics', {}))
    candidate=ctx.design_dir/"FREEZE_FINAL.json"
    if not candidate.exists(): return StageResult("S6",ctx.card.design_id,Disposition.PARKED,"No final candidate","Finish calibration and frozen candidate first")
    from ..registration import hub_signer
    signer=hub_signer(ctx)
    try:
        sealed_review_evidence(ctx.design_dir,sha(candidate),ctx.card.design_id,signer)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        path,_=prepare(ctx,"S6")
        return StageResult("S6",ctx.card.design_id,Disposition.PARKED,"One complete independent sweep/closure missing","Supply candidate-bound review; fix only enumerated MUSTs in at most two cycles",(artifact(path,"review_plan"),))
    if "cohort_censor_rule" in ctx.card.payload.get("analysis",{}):
        from .. import cohort_censor_rule   # R-S3C1: the cell list must still be the bound export of the supported primary geometry
        cohort_censor_rule.check_card(dict(ctx.card.payload))
        cohort_censor_rule.verify_bound(ctx.design_dir,ctx.card.path,ctx.read("SUPPORT.json"))
    check=verify(ctx.design_dir,"final",signer)
    if not check["ok"]: return StageResult("S6",ctx.card.design_id,Disposition.PARKED,"Hub final approval hook blocked","Supply exact candidate-bound named hub approval",metrics=check)
    from ..execution import execute_once, DeclaredStop
    from ..registered import load_registration, run_registered, not_run_result, RegisteredExecutionError
    from ..contract_card import registered_execution_card
    card=registered_execution_card(ctx.design_dir,json.loads(candidate.read_text()))
    def load():
        try: return load_registration(ctx.design_dir,card)
        except Exception as exc:
            partial=not_run_result(ctx.read("SPEC.json"),str(exc))
            partial["status"]="STOPPED" if isinstance(exc,DeclaredStop) else "FAILED_NO_RETRY"
            if isinstance(exc,DeclaredStop):
                exc.result=partial
                raise
            raise RegisteredExecutionError(str(exc),partial) from exc
    result=execute_once(ctx.design_dir,signer,load,run_registered)
    status=result.get("status")
    return StageResult("S6",ctx.card.design_id,Disposition.STOPPED if status=="STOPPED" else Disposition.READY,
        result.get("reason") or "Registered results, diagnostics, sensitivities, RI/noise and forecast comparisons completed",
        artifacts=tuple(artifact(ctx.design_dir/n,"execution_result") for n in ("RESULT.json","TERMINAL.json")),
        metrics={"status":status,"actual_data_estimation_calls":result.get("actual_data_estimation_calls",0)})
