"""Reusable batch2/3 two-stage guards. Signatures are supplied by the hub.
These guards validate approval identity/binding, not cryptographic signatures.
There is no automatic signer and no self-authorizing real execution path.
"""
from __future__ import annotations
import importlib.metadata, json, platform, sys
from pathlib import Path
from typing import Mapping
from .storage import local, sha, write_once
from .dependency_closure import POLICY as CODE_INVENTORY_POLICY, dependency_inventory

EXECUTION_ENTRY_POINT=Path(__file__).parent/"stages"/"s6_execute.py"

def code_inventory() -> dict[str,str]:
    """Bind execution, guard, pipeline and transitive repo-local dependencies."""
    from .snapshots import active
    if active() is not None: return active()["code_inventory"]
    inventory = dependency_inventory(Path(__file__).resolve().parents[2],
        [EXECUTION_ENTRY_POINT],pipeline_root=Path(__file__).parent)
    inventory.update({str(p.resolve()):sha(p) for p in (Path(__file__).parent/"agents/templates").rglob("*.md")})
    return dict(sorted(inventory.items()))

ACTIONS={"prep":["pre_adoption_anchor","synthetic_calibration"],"final":["real_estimation","noise_floor"]}
REQUIRED_READINESS=("synthetic_tests_pass","independent_review_pass","independent_reference_pass","parallel_parity_pass","interval_reporting_valid")
ROLES={"card","spec","treatment","outcome","support","code","environment","predictions","interval_plan"}

def readiness_passed(value: object) -> bool:
    return isinstance(value,dict) and set(value)==set(REQUIRED_READINESS) and all(value[k] is True for k in REQUIRED_READINESS)

def environment() -> dict:
    packages={}
    for name in ("numpy","scipy"):
        try: packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: packages[name]=None
    return {"python":sys.version,"executable":sys.executable,"platform":platform.platform(),"packages":packages}

def manifest_path(stage: str) -> str:
    if stage not in ACTIONS: raise ValueError("Unknown freeze stage")
    return "FREEZE_"+stage.upper()+".json"

def verify(root: Path, stage: str, hub_signer: str, *, require_approval: bool=True) -> dict:
    from . import snapshots
    frozen = snapshots.manifest_for(root, stage)
    if frozen and snapshots.active() is None and any(p.endswith("/scripts/l2_pipeline/freeze.py") for p in frozen["code_inventory"]):
        try:
            return snapshots.call(root, frozen, "verify", dict(stage=stage, hub_signer=hub_signer, require_approval=require_approval))
        except (OSError, ValueError, RuntimeError) as exc:
            return dict(ok=False, issues=[str(exc)], stage=stage, manifest_sha256=sha(Path(root)/manifest_path(stage)), actual_data_estimation_calls=0)
    issues=[]; digest=None; root=Path(root)
    try:
        path=local(root,manifest_path(stage)); m=json.loads(path.read_text()); digest=sha(path)
        if m.get("stage")!=stage or m.get("schema")!="l2_pipeline_freeze_v1" or not m.get("files"): raise ValueError("Invalid freeze inventory")
        if m.get("canonical_root")!=str(root.resolve()): issues.append("Canonical study root mismatch")
        if m.get("environment")!=environment(): issues.append("Environment mismatch")
        roles=set()
        for relative,item in m["files"].items():
            p=local(root,relative); roles.add(item["role"])
            if sha(p)!=item["sha256"]: issues.append("Hash mismatch: "+relative)
        if not ROLES<=roles: issues.append("Required artifact role missing")
        # Recompute the same complete closure at seal and execution guard time.
        if "code_snapshot" in m:
            snapshots.validate(root, m["code_snapshot"], m["code_inventory"])
            if m.get("code_inventory_policy") != CODE_INVENTORY_POLICY:
                issues.append("Unbound or changed pipeline dependency")
        else:
            actual=code_inventory()
            if (m.get("code_inventory_policy")!=CODE_INVENTORY_POLICY
                    or m.get("execution_entry_point")!=str(EXECUTION_ENTRY_POINT.resolve())
                    or m.get("code_inventory")!=actual):
                issues.append("Unbound or changed pipeline dependency")
        lock_path=m["prediction_lock"]
        if lock_path not in m["files"]: issues.append("Prediction lock itself is unbound")
        lock=json.loads(local(root,lock_path).read_text())
        if 'forecast_snapshot' in m:
            from .forecast_snapshot import validate
            validate(root,m['forecast_snapshot'],lock)
        expected_predictions={"predictions/"+persona+"/submission.json" for persona in m["personas"]}
        if set(lock["files"])!=expected_predictions or not expected_predictions: issues.append("Missing/extra prediction submissions")
        specs=[relative for relative,item in m["files"].items() if item["role"]=="spec"]
        cards=[relative for relative,item in m["files"].items() if item["role"]=="card"]
        if len(specs)!=1 or len(cards)!=1: raise ValueError("Exactly one card and scientific spec required")
        card=json.loads(local(root,cards[0]).read_text())
        design_id=card["design_id"]
        from .engine.panels import transition_year_drop_enabled
        if transition_year_drop_enabled(m)!=transition_year_drop_enabled(card):
            issues.append('Freeze transition-year drop differs from card')
        if lock.get("spec_sha256")!=sha(local(root,specs[0])) or lock.get("design_id")!=design_id: issues.append("Prediction scientific binding mismatch")
        if set(lock["personas"])!=set(m["personas"]) or len(lock["personas"])!=len(m["personas"]): issues.append("Prediction persona inventory mismatch")
        for relative,h in lock["files"].items():
            prediction=json.loads(local(root,relative).read_text())
            persona=relative.split("/")[1]
            if prediction.get("persona")!=persona or prediction.get("design_id")!=design_id or prediction.get("spec_sha256")!=lock.get("spec_sha256"): issues.append("Forecast identity mismatch")
            if m["files"].get(relative,{}).get("sha256")!=h or sha(local(root,relative))!=h: issues.append("Prediction lock mismatch")
        for relative,h in lock.get("original_files",{}).items():
            if m["files"].get(relative,{}).get("sha256")!=h or sha(local(root,relative))!=h:
                issues.append("Original forecast/collection lock mismatch: "+relative)
        if stage=="final":
            if m.get('dry_load_required') is True:
                from .dry_load import inventory
                if m.get('dry_load_inventory') != inventory(root,m['files']):
                    issues.append('DRY-LOAD final input inventory mismatch')
            pre=verify(root,"prep",hub_signer); issues += ["prep: "+s for s in pre["issues"]]
            if m.get("prep_sha256")!=pre["manifest_sha256"]: issues.append("Preparatory linkage mismatch")
            prep_manifest=json.loads(local(root,manifest_path("prep")).read_text())
            prior=prep_manifest["files"]
            if m.get('code_snapshot') != prep_manifest.get('code_snapshot') or m.get('code_inventory') != prep_manifest.get('code_inventory'):
                issues.append('Final code differs from preparatory snapshot')
            if any(m["files"].get(k)!=v for k,v in prior.items()): issues.append("Preparatory bytes not preserved")
            readiness=json.loads(local(root,"FINAL_READINESS.json").read_text())
            gates=json.loads(local(root,"CALIBRATION_GATES.json").read_text())
            if not readiness_passed(readiness) or gates.get("passed") is not True: issues.append("Malformed/failed readiness or calibration")
            from .registered import interval_plan, validate_calibration
            plan,_=interval_plan(root,m,design_id)
            if set(gates.get("cases",{}))!=set(plan["expected_designs"]): issues.append("Calibration design inventory incomplete")
            else:
                for key,record in gates["cases"].items():
                    if record.get("case_id")!=key: issues.append("Calibration design identity mismatch")
                    validate_calibration(record)
            if m.get("execution_ready") is not True: issues.append("Final freeze not execution ready")
            if not {"FINAL_READINESS.json","CALIBRATION_GATES.json",manifest_path("prep"),"APPROVAL_PREP.json","REVIEW.json"}<=set(m["files"]): issues.append("Final prerequisite missing")
        if require_approval:
            a=json.loads(local(root,"APPROVAL_"+stage.upper()+".json").read_text())
            from datetime import datetime
            timestamp=datetime.fromisoformat(a["signed_at_utc"].replace("Z","+00:00"))
            if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds()!=0: issues.append("Invalid approval UTC")
            if a.get("signed_by")!=hub_signer or not hub_signer or a.get("status")!="APPROVED": issues.append("Missing/incorrect named hub signature")
            if a.get("manifest_sha256")!=digest or a.get("manifest_path")!=manifest_path(stage): issues.append("Approval byte binding mismatch")
            if stage=="final":
                from .stages.s6_execute import sealed_review_evidence
                evidence=sealed_review_evidence(root,digest,design_id,hub_signer)
                if any(a.get(key)!=value for key,value in evidence.items()):
                    issues.append("Approval does not bind exact reviewer closure bytes")
                if m.get('dry_load_required') is True:
                    from .dry_load import require_pass
                    if a.get('dry_load_sha256') != require_pass(root,digest,design_id):
                        issues.append('Approval does not bind DRY-LOAD PASS')
            if a.get("approved_actions")!=ACTIONS[stage] or a.get("execution_authorized") is not (stage=="final"): issues.append("Approval scope mismatch")
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc: issues.append(str(exc))
    return {"ok":not issues,"issues":issues,"stage":stage,"manifest_sha256":digest,"actual_data_estimation_calls":0}

def require_verified(root: Path, stage: str, hub_signer: str) -> dict:
    result=verify(root,stage,hub_signer)
    if not result["ok"]: raise RuntimeError("Freeze refused: "+json.dumps(result["issues"]))
    return result

def build_manifest(root: Path, stage: str, files: Mapping[str,str], personas: list[str], hub_signer: str) -> Path:
    root=Path(root)
    if not personas or len(set(personas))!=len(personas): raise ValueError("Persona inventory missing or duplicated")
    if "PREDICTIONS_LOCK.json" not in files: raise ValueError("Prediction lock must be bound")
    if not ROLES<=set(files.values()): raise ValueError("Mandatory dependency role missing")
    lock=json.loads(local(root,"PREDICTIONS_LOCK.json").read_text())
    if not set(lock.get("original_files",{}))<=set(files): raise ValueError("Original forecast and collection bytes must be bound")
    records={relative:{"role":role,"sha256":sha(local(root,relative))} for relative,role in sorted(files.items())}
    value={"schema":"l2_pipeline_freeze_v1","stage":stage,"environment":environment(),"files":records,
           "canonical_root":str(root.resolve()),
           "personas":personas,"prediction_lock":"PREDICTIONS_LOCK.json",
           "code_inventory_policy":CODE_INVENTORY_POLICY,
           "execution_entry_point":str(EXECUTION_ENTRY_POINT.resolve()),
           "code_inventory":code_inventory()}
    from .snapshots import create
    value["code_snapshot"] = create(root, value["code_inventory"])
    from .forecast_snapshot import create as create_forecasts
    value['forecast_snapshot'] = create_forecasts(root,lock)
    if stage=='final':
        from .dry_load import inventory
        value.update(dry_load_required=True,dry_load_inventory=inventory(root,records))
    cards=[name for name,role in files.items() if role=='card']
    if len(cards)!=1: raise ValueError('Exactly one execution card required')
    from .engine.panels import transition_year_drop_enabled
    value['transition_year_drop']=transition_year_drop_enabled(json.loads(local(root,cards[0]).read_text()))
    if stage=="final":
        pre=require_verified(root,"prep",hub_signer)
        prior=json.loads(local(root,manifest_path("prep")).read_text())
        if value["code_inventory"] != prior["code_inventory"]:
            raise ValueError("Final code differs from preparatory snapshot")
        required={"FINAL_READINESS.json","CALIBRATION_GATES.json",manifest_path("prep"),"APPROVAL_PREP.json","REVIEW.json"}
        if not required<=set(records): raise ValueError("Missing final prerequisites")
        readiness=json.loads(local(root,"FINAL_READINESS.json").read_text()); gates=json.loads(local(root,"CALIBRATION_GATES.json").read_text())
        if not readiness_passed(readiness) or gates.get("passed") is not True: raise ValueError("Final not ready: exact literal predicates required")
        cards=[name for name,role in files.items() if role=="card"]
        if len(cards)!=1: raise ValueError("Exactly one execution card required")
        from .registered import interval_plan, validate_calibration
        design_id=json.loads(local(root,cards[0]).read_text())["design_id"]
        plan,_=interval_plan(root,value,design_id)
        if set(gates.get("cases",{}))!=set(plan["expected_designs"]): raise ValueError("Calibration design inventory incomplete")
        for key,record in gates["cases"].items():
            if record.get("case_id")!=key: raise ValueError("Calibration design identity mismatch")
            validate_calibration(record)
        value.update(prep_sha256=pre["manifest_sha256"],execution_ready=True)
    return write_once(local(root,manifest_path(stage)),value)
