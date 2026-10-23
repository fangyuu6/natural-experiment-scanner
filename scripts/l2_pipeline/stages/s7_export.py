"""Local immutable export fragments for registry/site/L3; no site build or upload."""
from __future__ import annotations
import copy
from ..contracts import Disposition, StageContext, StageResult
from ..storage import artifact, sha, write_once, public_input

def exports(card: dict, terminal: dict, result: dict | None, provenance: dict, l3_registration=None, export_annotations=None, result_reference=None, forecast_independence=None, cohort_censor=None) -> dict:
    status=terminal["status"]
    if status not in {"COMPLETED","STOPPED","FAILED_NO_RETRY","NOT_RUN"}: raise ValueError("Nonterminal export")
    provenance=copy.deepcopy(provenance)
    for key in ("source_certification", "annual_clock_disposition"):
        if key in card.get("analysis", {}):
            provenance[key]=copy.deepcopy(card["analysis"][key])
    base={"design_id":card["design_id"],"spec_version":card["version"],"policy_family":card["policy_family"],
          "track":card["track"],"lineage":card["lineage"],"outcome_family":card["outcome_family"],"status":status,"reason":terminal.get("reason",terminal.get("message")),"provenance":provenance}
    from ..outcome_transform import export_fields
    base.update(export_fields(card,(result or {}).get('source_unit_before_log')))
    if export_annotations is not None:
        base["export_annotations"] = copy.deepcopy(export_annotations)
    from ..c64 import export_block
    l3 = export_block(card, result, l3_registration,
        dict(result_reference,pointer='/l3_export_v2') if result_reference and (result or {}).get('l3_export_v2') else None)
    if forecast_independence is not None:base['forecast_independence']=copy.deepcopy(forecast_independence)
    if cohort_censor is not None:base['cohort_censor']=copy.deepcopy(cohort_censor)   # R-S3C1 tag, bound cell list, share, runs, verdict
    if result_reference:result={'$ref':result_reference}
    return {"registry_event":{**base,"status":"run" if status=="COMPLETED" else "stopped"},
            "estimates_page":{**base,"result":result},"l3_meta":{**base,"result":result,"l3_export_v2":l3,"selection_denominator":"all_queued_designs_including_stops"}}

def run(ctx: StageContext) -> StageResult:
    terminal_path=ctx.design_dir/"TERMINAL.json"
    if not terminal_path.exists(): terminal_path=ctx.design_dir/"FAILURE.json"
    if not terminal_path.exists(): return StageResult("S7",ctx.card.design_id,Disposition.PARKED,"No terminal execution record","Finish registered execution or retain an explicit stopped disposition")
    result_path=ctx.design_dir/"RESULT.json"
    result=ctx.read("RESULT.json") if result_path.exists() else None
    from ..c64 import SPEC_NAME
    registration=ctx.read(SPEC_NAME) if (ctx.design_dir/SPEC_NAME).exists() else None
    from ..c70_l3 import frozen_contract, merge_registration
    if registration is not None:
        registration=merge_registration(registration,frozen_contract(ctx.design_dir))
    if registration is not None and (ctx.design_dir/'TREATMENT.json').exists():
        from ..c64_metadata import geography
        registration['geography'].update(geography(dict(ctx.card.payload),ctx.read('TREATMENT.json')))
    from ..c83_l3 import frozen,overlay
    registration=overlay(registration,frozen(ctx.design_dir))
    from ..f1_coding import bind
    registration=bind(registration,ctx.design_dir)
    from ..class_table import enabled, export_annotations
    from ..c64 import reference
    prospective=enabled(ctx.design_dir)
    annotations=ctx.read('C65_EXPORT_ANNOTATIONS.json') if (ctx.design_dir/'C65_EXPORT_ANNOTATIONS.json').exists() else None
    if prospective:annotations=export_annotations(ctx.design_dir,annotations)
    lock=ctx.read('PREDICTIONS_LOCK.json') if (ctx.design_dir/'PREDICTIONS_LOCK.json').exists() else {}
    independence={k:lock[k] for k in ('n_eff','SINGLE_CONSENSUS','n10_eligible','forecast_slots','n_workers','independence_source') if k in lock}
    censor=None
    if "cohort_censor_rule" in ctx.card.payload.get("analysis",{}):
        from ..cohort_censor_rule import export_block as censor_block
        censor=censor_block(ctx.design_dir,dict(ctx.card.payload),result)
    value=exports(dict(ctx.card.payload),ctx.read(terminal_path.name),result,{"terminal_sha256":sha(terminal_path),"result_sha256":sha(result_path) if result_path.exists() else None}, l3_registration=registration, export_annotations=annotations,
        result_reference=reference(result_path) if prospective and result_path.exists() else None,
        forecast_independence=independence or None,cohort_censor=censor)
    path=write_once(ctx.design_dir/"EXPORT.json",value)
    artifacts=[artifact(path,"local_export")]
    registry_input=ctx.card.payload.get("automation",{}).get("registry_update_input")
    if registry_input:
        import json
        from ..registry import update_snapshot
        envelope=json.loads(public_input(ctx.card.path.parent/registry_input).read_text())
        snapshot,site=update_snapshot(**envelope)
        p=write_once(ctx.design_dir/"REGISTRY_SNAPSHOT.json",snapshot);artifacts.append(artifact(p,"registry"))
        p=write_once(ctx.design_dir/"REGISTRY_SITE_DATA.json",site);artifacts.append(artifact(p,"registry_site_data"))
    return StageResult("S7",ctx.card.design_id,Disposition.READY,"Registry event, Estimates data and L3 input fragments exported","Merge via existing validated registry/site builders before release",tuple(artifacts))
