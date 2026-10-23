"""Prediction/prep/final freeze hooks; no automatic signing or calibration run."""
from __future__ import annotations
import json
from .agent_jobs import prepare
from ..contracts import Disposition, StageContext, StageResult
from ..freeze import build_manifest, verify
from ..predictions import lock_predictions
from ..storage import artifact, sha, write_once

def run(ctx: StageContext) -> StageResult:
    if (ctx.design_dir/"REGISTRATION.json").is_file():
        from ..registration import hub_signer
        check=verify(ctx.design_dir,"final",hub_signer(ctx),require_approval=False)
        return StageResult("S5",ctx.card.design_id,Disposition.READY if check["ok"] else Disposition.PARKED,
            "Final candidate frozen" if check["ok"] else "Registration pending",metrics=check)
    from ..forecast_slots import roster
    cfg=ctx.card.payload.get("automation",{}); personas=roster(ctx)
    path,_=prepare(ctx,"S5",roles=personas)
    if not all((ctx.design_dir/"predictions"/p/"submission.json").is_file() for p in personas):
        return StageResult("S5",ctx.card.design_id,Disposition.PARKED,"Blind persona jobs emitted","Host dispatch and collect original filled ledgers before preparatory freeze",(artifact(path,"agent_jobs"),))
    if not (ctx.design_dir/"PREDICTIONS_LOCK.json").exists(): lock_predictions(ctx.design_dir,personas,ctx.card.design_id,sha(ctx.design_dir/"SPEC.json"))
    inventory=cfg.get("freeze_files"); signer=cfg.get("hub_signer")
    if not isinstance(inventory,dict) or not signer: return StageResult("S5",ctx.card.design_id,Disposition.PARKED,"Forecasts locked; freeze inventory/hub identity pending","Declare exact artifact-role inventory and named approval authority")
    inventory=dict(inventory)
    originals=json.loads((ctx.design_dir/"PREDICTIONS_LOCK.json").read_text()).get("original_files",{})
    inventory.update({path:"predictions" for path in originals})
    prep=ctx.design_dir/"FREEZE_PREP.json"
    if not prep.exists(): build_manifest(ctx.design_dir,"prep",inventory,personas,signer)
    check=verify(ctx.design_dir,"prep",signer)
    if not check["ok"]: return StageResult("S5",ctx.card.design_id,Disposition.PARKED,"Preparatory freeze awaits bound hub approval","Supply named hub signature for pre-adoption/synthetic actions only",metrics=check)
    needed=["CALIBRATION_GATES.json","FINAL_READINESS.json","REVIEW.json"]
    if not all((ctx.design_dir/n).exists() for n in needed): return StageResult("S5",ctx.card.design_id,Disposition.PARKED,"Preparatory approved; calibration/review adapter pending","Run frozen pre-adoption CT/fallback and RI feasibility, one complete independent sweep")
    final_inventory=dict(inventory)
    final_inventory.update({"FREEZE_PREP.json":"verification","APPROVAL_PREP.json":"verification","CALIBRATION_GATES.json":"calibration","FINAL_READINESS.json":"verification","REVIEW.json":"verification"})
    if not (ctx.design_dir/"FREEZE_FINAL.json").exists(): build_manifest(ctx.design_dir,"final",final_inventory,personas,signer)
    check=verify(ctx.design_dir,"final",signer,require_approval=False)
    return StageResult("S5",ctx.card.design_id,Disposition.READY if check["ok"] else Disposition.PARKED,"Final candidate frozen" if check["ok"] else "Final candidate invalid","Independent review closure and exact hub final signature",metrics=check)
