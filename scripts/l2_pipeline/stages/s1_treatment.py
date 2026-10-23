"""Blind tuple reconciliation and seeded V3 treated-start audit (no absence audit)."""
from __future__ import annotations
import json, math, random
from collections import Counter
from pathlib import Path
from typing import Mapping, Any
from ..contracts import Disposition, StageContext, StageResult
from ..storage import artifact, sha, write_once, ensure_once, public_input
from .agent_jobs import prepare
from .. import prewindow

KNOWN = {"treated", "never_treated", "not_yet_treated"}

def reconcile(a: list[dict], b: list[dict], universe: list[str]) -> dict:
    def keyed(rows):
        result = {}
        for row in rows:
            unit = row["unit"]
            if unit in result or unit not in universe: raise ValueError("Duplicate/outside-universe coding unit")
            if row.get("status") not in KNOWN | {"unknown"}: raise ValueError("Invalid coding status")
            if row["status"] in {"treated", "not_yet_treated"} and (not isinstance(row.get("start_period"), int) or isinstance(row["start_period"], bool)):
                raise ValueError("Known adoption needs integer analysis period")
            if row["status"] == "never_treated" and row.get("start_period") is not None: raise ValueError("Never-treated cannot carry a start")
            if row["status"] in KNOWN and (not row.get("mechanism") or not row.get("locator")): raise ValueError("Known analysis tuple lacks scope/locator")
            prewindow.validate_row(row)
            result[unit] = row
        return result
    left, right = keyed(a), keyed(b); counts = Counter(); accepted = []; gaps = []
    for u in universe:
        x, y = left.get(u, {"status":"unknown"}), right.get(u, {"status":"unknown"})
        x_known, y_known = x["status"] in KNOWN, y["status"] in KNOWN
        if x_known and y_known:
            pw = prewindow.pair(x, y)   # T192: pre_window_bound pairs follow the panel rules, never the year equality
            if pw is not None:
                accepted.append(pw); counts["known_match"] += 1; continue
            same = all(x.get(k) == y.get(k) for k in ("status", "start_period", "mechanism"))
            label = "known_match" if same else "known_conflict"
            if same:
                from ..v11_v12 import certified_interruptions
                end, conflict = certified_interruptions(x,y)
                if conflict:
                    label=conflict
                    gaps.append({'unit':u,'kind':label,'a':x,'b':y})
                else:
                    accepted.append(dict(x,censor_after_period=end,certified_interruption=True)
                                    if x.get('observed_interruptions') or y.get('observed_interruptions') else x)
            else: gaps.append({"unit":u,"kind":label,"a":x,"b":y})
        else:
            label = "one_sided_missing" if x_known != y_known else "both_unknown"
            gaps.append({"unit":u,"kind":label})
        counts[label] += 1
    paired = counts["known_match"] + counts["known_conflict"]
    return {"counts":dict(counts),"agreement":counts["known_match"] / paired if paired else None,
            "paired_known":paired,"accepted":accepted,"gaps":gaps}

def audit_sample(records: list[dict], seed: int) -> list[str]:
    treated = sorted(r["unit"] for r in records if r["status"] == "treated" and not prewindow.is_bound(r))
    return random.Random(seed).sample(treated, min(10, len(treated)))

def evaluate_v3(sample: list[str], checks: list[dict], *, remainder_census=False, limits=None) -> dict:
    if len(sample) != 10 and not (remainder_census is True and 0 < len(sample) < 10): return {"passed":False,"reason":"V3 ten-treated audit unavailable; small-universe hub ruling required"}
    keyed = {r["unit"]:r for r in checks}
    if len(keyed) != len(checks) or set(keyed) != set(sample): raise ValueError("Audit does not match prelocked seeded sample")
    if any(r.get("status") not in {"match","known_error","unresolved"} or not r.get("official_locator") for r in checks): raise ValueError("Invalid official audit record")
    errors = sum(r["status"] == "known_error" for r in checks)
    unresolved = sum(r["status"] == "unresolved" for r in checks)
    corrected = all(r.get("corrected_start_period") is not None for r in checks if r["status"] == "known_error")
    max_errors, max_unresolved = (2, 5) if limits is None else (limits["max_known_errors"], limits["max_unresolved"])
    return {"passed":errors <= max_errors and unresolved <= max_unresolved and corrected,"known_start_errors":errors,"unresolved":unresolved,"sample_size":len(sample),"known_errors_corrected":corrected}

def run(ctx: StageContext) -> StageResult:
    if ctx.card.payload.get("treatment", {}).get("source_type") == "research_panel":
        from ..sources.research_panel.s1 import run as run_research_panel
        return run_research_panel(ctx)
    if ctx.card.payload.get("treatment", {}).get("source_type") == "policy_tracker":
        from ..policy_tracker_intake import run as run_tracker
        return run_tracker(ctx)
    if (ctx.design_dir / "S1_RECONCILIATION_INPUTS.json").is_file():
        from .s1_reconciliation import run_reconciliation
        return run_reconciliation(ctx)
    cfg = ctx.card.payload["treatment"]
    pack_pointer=ctx.design_dir/"SOURCE_PACK.json"
    if pack_pointer.is_file():
        from ..source_packs import load_source_pack
        from ..agents import emit_stage_jobs, collect_job
        pointer=json.loads(pack_pointer.read_text());mp=public_input(Path(pointer["manifest_path"]))
        if sha(mp)!=pointer["manifest_sha256"]: raise ValueError("Source pack pointer changed")
        pack=load_source_pack(mp,card=ctx.card)
        if pack["status"]!="READY" or pack["complete_list_complete"] is not True:
            return StageResult("S1",ctx.card.design_id,Disposition.PARKED,pack["reason"],"Fetch a dated complete-list source before coding",(artifact(mp,"source_pack"),))
        jobs=emit_stage_jobs(ctx,"S1",jobs_root=Path(pointer["jobs_root"]),attempt_id=pointer["attempt_id"],source_pack_path=mp)
        manifest=ensure_once(ctx.design_dir/"S1_AGENT_JOBS.json",{"job_paths":[j.job_path for j in jobs],"source_pack_sha256":sha(mp),"execution":"external_host_only","retries":0})
        if not all(Path(j.output_path).is_file() for j in jobs):
            return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Source-backed independent coder jobs emitted","Host dispatch attempt and collect two blind originals",(artifact(manifest,"agent_jobs"),artifact(mp,"source_pack")))
        collected=[collect_job(j.job_path) for j in jobs]
        if not all(r.get("validated") for r in collected):
            return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"INSUFFICIENT_WORK","Complete unit-specific research before reconciliation",metrics={"quality_status":"INSUFFICIENT_WORK"})
        # Current stock is eligible under V4. A hash-bound hub declaration
        # supplies the roster/withdrawal judgement; source type alone is no gate.
        if not pointer.get("ruling_path") or not pointer.get("ruling_sha256"):
            return StageResult("S1",ctx.card.design_id,Disposition.PARKED,
                "Coding collected; V4 absence/withdrawal declaration required",
                "Bind the pipeline-wide V4 ruling and complete-list roster; dated official starts do not require a historical pack",
                (artifact(mp,"source_pack"),))
        ruling_path=public_input(Path(pointer["ruling_path"]))
        if sha(ruling_path)!=pointer["ruling_sha256"]: raise ValueError("V4 ruling pointer changed")
        from .s1_reconciliation import run_reconciliation, RULING
        if json.loads(ruling_path.read_text()).get("ruling_id")!=RULING: raise ValueError("Source-backed route requires V4 hub ruling")
        ensure_once(ctx.design_dir/"S1_RECONCILIATION_INPUTS.json", {
            "coder_jobs": {j.role:j.job_path for j in jobs},
            "ruling_path":str(ruling_path), "ruling_sha256":sha(ruling_path),
            "followup_jobs_root":pointer["jobs_root"],
            "followup_jobs":pointer.get("followup_jobs", {}),
            "outcome_inventory_window":pointer.get("outcome_inventory_window"),
            "emit_missing_followups":pointer.get("emit_missing_followups", True)})
        return run_reconciliation(ctx)
    required = {"universe","seed","complete_list_source_id","predicate","coverage_end","historical_convention"}
    if not required <= set(cfg):
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Complete-list source contract incomplete","Supply predicate, universe, clock/coverage and source version")
    if type(cfg["seed"]) is not int or len(set(cfg["universe"])) != len(cfg["universe"]): raise ValueError("Invalid seeded unit universe")
    source = next((s for s in ctx.card.payload["sources"] if s["source_id"] == cfg["complete_list_source_id"]), None)
    if pack_pointer.is_file():
        source={"source_id":"source_pack","complete_list":True,"sha256":sha(mp),"coverage_end":cfg["coverage_end"],"path":str(mp)}
    if not source or source.get("complete_list") is not True or not source.get("sha256") or source.get("coverage_end") != cfg["coverage_end"]:
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Complete-list coverage not bound","Archive and hash an eligible historical complete list")
    if not source.get("path") or sha(public_input(ctx.card.path.parent / source["path"])) != source["sha256"]: raise ValueError("Complete-list source hash mismatch")
    end = ctx.card.payload["outcome"].get("years", ctx.card.payload["outcome"].get("period_years",[None]))[-1]
    if end is not None and str(cfg["coverage_end"]) < str(end) + "-12-31":
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Complete list does not cover sample end","Supply a later cumulative history or prospectively revise window")
    # Imported/manual originals retain the legacy path; newly emitted jobs use
    # the strict identity and blindness contract in the collector.
    paths = [ctx.design_dir / "agents" / role / "submission.json" for role in ("A","B")]
    manifest, _ = prepare(ctx,"S1") if not all(p.is_file() for p in paths) else (None, [])
    if not all(p.is_file() for p in paths):
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Independent coder jobs emitted","Host dispatch then collect the two blind originals",(artifact(manifest,"agent_jobs"),))
    records = [json.loads(p.read_text())["records"] for p in paths]
    if not pack_pointer.is_file() and any(sum(r.get("status") in KNOWN for r in rows)<.5*len(cfg["universe"]) for rows in records):
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"INSUFFICIENT_WORK","Build a substantive source pack and complete independent unit-level research",metrics={"quality_status":"INSUFFICIENT_WORK"})
    ensure_once(ctx.design_dir / "CODING_LOCK.json", {"files":{str(p):sha(p) for p in paths}})
    comparison = reconcile(*records, cfg["universe"])
    if comparison["gaps"]:
        output=write_once(ctx.design_dir / "RECONCILIATION.json", comparison)
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Analysis tuple completion/conflicts pending","Third-read conflicting known units; complete missing units",(artifact(output,"reconciliation"),))
    prewindow.forbid(comparison["accepted"], "the legacy S1 route (no exclusion path)")
    if any(r["mechanism"] != ctx.card.mechanism for r in comparison["accepted"]):
        return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Mixed policy mechanisms","Split card or pre-register ambiguous-member sensitivity")
    sample = audit_sample(comparison["accepted"], cfg["seed"])
    audit_path=ctx.design_dir / "AUDIT_SELECTION.json"
    if audit_path.exists():
        if json.loads(audit_path.read_text()) != {"seed":cfg["seed"],"sample":sample,"source_sha256":source["sha256"]}: raise ValueError("Audit selection changed")
    else: write_once(audit_path,{"seed":cfg["seed"],"sample":sample,"source_sha256":source["sha256"]})
    checks_path=ctx.design_dir / "OFFICIAL_AUDIT.json"
    if not checks_path.exists():
        if len(sample)!=10:
            return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"V3 ten-treated audit unavailable","Explicit small-universe hub disposition required")
        manifest,_=prepare(ctx,"S1",roles=["auditor"])
        if not checks_path.exists():
            return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"Seeded treated-start auditor job emitted","Host dispatch then collect official checks",(artifact(manifest,"agent_jobs"),))
    audit=evaluate_v3(sample,json.loads(checks_path.read_text())["checks"])
    if not audit["passed"]: return StageResult("S1",ctx.card.design_id,Disposition.PARKED,"V3 audit failed","Full coding or scoped hub ruling",metrics=audit)
    corrections={r["unit"]:r["corrected_start_period"] for r in json.loads(checks_path.read_text())["checks"] if r["status"]=="known_error"}
    accepted=[dict(r,start_period=corrections.get(r["unit"],r.get("start_period"))) for r in comparison["accepted"]]
    path=write_once(ctx.design_dir / "TREATMENT.json",{"records":accepted,"audit":audit,"source_sha256":source["sha256"],"comparison":comparison["counts"],"sample":sample})
    return StageResult("S1",ctx.card.design_id,Disposition.READY,"V3 treatment source-ready",artifacts=(artifact(path,"treatment"),),metrics=audit)
