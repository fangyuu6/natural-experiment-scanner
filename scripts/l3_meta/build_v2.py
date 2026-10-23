#!/usr/bin/env python3
"""L3 meta-analysis, DEVELOPMENT v2 (not confirmatory).

Joins the frozen v1 analysis set (21 archive designs + carried pipeline stops) with every
terminal L2 pipeline run in data/l2_pipeline/registry/TERMINAL_RUNS.json, then tabulates the
five pre-registered questions (PREREG_L3_META.md, Q1-Q5) descriptively.

Read-only on every L2 file. No estimation, no replay, no bootstrap, no download.
Only output: data/l3_meta/l2_set_v2.json.

Run:  .venv_did/bin/python -B scripts/l3_meta/build_v2.py     (scipy only used for Q5 planning power)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from scripts.l3_meta.reader import read_export, n10_eligible, forecast_summary
import cli as l3cli  # noqa: E402  (stdlib power helpers from the proposal-only CLI; read-only use)

try:
    from scipy.stats import chi2, ncx2
except Exception:  # planning power for Q5 is optional
    chi2 = ncx2 = None

AS_OF = "2026-10-10"
V1_PATH = ROOT / "data/l3_meta/l2_set_v1.json"
REG_PATH = ROOT / "data/l2_pipeline/registry/TERMINAL_RUNS.json"
SCOREBOARD = ROOT / "docs/plans/l2-pipeline/L2_PIPELINE_SCOREBOARD.md"
LIT_REG = ROOT / "data/l2_pipeline/literature/registry.json"
OUT = ROOT / "data/l3_meta/l2_set_v2.json"
STATUS_DOCS = ["C15_STATUS.md", "C16A_STATUS.md", "C28_STATUS.md", "C31_STATUS.md", "C34_STATUS.md"]
DOMAINS = ["labor", "public_finance", "health", "crime", "education", "environment", "other"]
SIX = DOMAINS[:6]

PROV = {}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def rel(p):
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def note(p):
    """Record a read source (path, bytes, sha256)."""
    p = Path(p)
    if p.is_file():
        PROV[rel(p)] = {"sha256": sha(p), "bytes": p.stat().st_size}


def jload(p):
    note(p)
    return json.loads(Path(p).read_text())


# ---------------------------------------------------------------------------------------------
# domain / family / order maps for designs that are new since v1 (policy field, not outcome sector)
# ---------------------------------------------------------------------------------------------
NEW_DOMAIN = {
    "run4_lit_family_planning_income_waiver": ("health", "family-planning eligibility expansion: health policy"),
    "run4_lit_nurse_licensure_compact": ("labor", "occupational licensing: labor policy (matches v1 domain)"),
    "run7_refundable_eitc": ("public_finance", "state EITC: tax policy (matches archive EITC domain)"),
    "run7_abortion_informed_consent": ("health", "abortion informed-consent law: health policy"),
}
for _d in ("run8_extreme_risk_protection_order", "run8_large_capacity_magazine_ban", "run8_gunshow_background_check",
           "run8_universal_background_check", "run8_domestic_violence_restraining_order_firearm_restriction",
           "c18_siegel_mcdvsurrender", "c18_siegel_mcdv", "c18_cspp_guncontrol_stand_your_ground",
           "c18_siegel_dvrosurrender", "c18_siegel_loststolen"):
    NEW_DOMAIN[_d] = ("crime", "firearm / criminal-justice law: crime policy")

# Development-only grouping of obviously related designs, used solely to count policy families against the
# prereg's ">=20 eligible policy families" gate. Everything not listed is its own family.
FAMILY_CLUSTER = {
    "firearm_laws": ["prototype:shall_issue", "prototype:castle_doctrine", "run8_extreme_risk_protection_order",
                     "run8_large_capacity_magazine_ban", "run8_gunshow_background_check",
                     "run8_universal_background_check", "run8_domestic_violence_restraining_order_firearm_restriction",
                     "c18_siegel_mcdvsurrender", "c18_siegel_mcdv", "c18_cspp_guncontrol_stand_your_ground",
                     "c18_siegel_dvrosurrender", "c18_siegel_loststolen"],
    "eitc": ["did-c2e2f141c074e2a2eab4", "run7_refundable_eitc"],
    "ban_the_box": ["did-a0c08d8d5a891ede06b1", "did-58827a36a4167bcf0ad1"],
    "minimum_wage": ["prototype:minimum_wage_step", "did-0facf57d0164de6ca875"],
    "cannabis": ["prototype:adult_use_cannabis", "did-82cc3e884d0ca4f8ce01"],
    "licensure_compacts": ["run1_ems_compact", "run1_pt_compact", "run4_lit_nurse_licensure_compact"],
    "reproductive_health": ["run7_abortion_informed_consent", "run2_contraception_insurance",
                            "run4_lit_family_planning_income_waiver"],
    "paid_leave": ["did-3ee1dcd04a7f41715f72", "prototype:paid_sick_leave"],
    "college_policy": ["run2_community_college_bachelor", "run2_decentralized_tuition", "run2_statewide_college_transfer"],
}
CLUSTER_OF = {d: k for k, v in FAMILY_CLUSTER.items() for d in v}


def queue_group(d):
    """How the design entered the L2 queue (workflow route), from its registered lineage."""
    if d.get("source_group") != "pipeline":
        return "archive_hand_picked"
    qv = ((d.get("lineage") or {}).get("queue_version") or "")
    if "literature" in qv or "published_did" in qv:
        return "literature_intake"
    if "research_panel" in qv or qv.startswith("c21"):
        return "research_panel"
    return "early_L1_queue"

# Chinese scoreboard row label -> design id (ERPO original row is the superseded run, mapped to None)
SCOREBOARD_NAMES = OrderedDict([
    ("薪酬保密禁令", "pay_secrecy"), ("孕期工作便利", "pregnancy_accommodation"), ("EMS执照互认", "run1_ems_compact"),
    ("PT执照互认", "run1_pt_compact"), ("护士强制加班限制", "run1_nurse_overtime"),
    ("乳腺密度通知", "run2_breast_density_notification"), ("结直肠筛查覆盖", "run2_colorectal_screening_coverage"),
    ("社区学院本科", "run2_community_college_bachelor"), ("避孕保险覆盖", "run2_contraception_insurance"),
    ("学费决策分权", "run2_decentralized_tuition"), ("电子废物回收", "run2_electronic_waste_recycling"),
    ("能源公益基金", "run2_energy_public_benefit_fund"), ("性别认同就业保护", "run2_gender_identity_employment"),
    ("甲基苯丙胺前体限制", "run2_meth_precursor_restrictions"), ("青年家属医保", "run2_young_adult_dependent_coverage"),
    ("计划生育收入豁免", "run4_lit_family_planning_income_waiver"), ("护士执照互认", "run4_lit_nurse_licensure_compact"),
    ("可退还EITC", "run7_refundable_eitc"), ("ERPO（原版）", None),
    ("大容量弹匣禁令", "run8_large_capacity_magazine_ban"), ("枪展背景核查", "run8_gunshow_background_check"),
    ("通用背景核查", "run8_universal_background_check"), ("ERPO（偏差002）", "run8_extreme_risk_protection_order"),
    ("MCDV交枪", "c18_siegel_mcdvsurrender"), ("流产知情同意", "run7_abortion_informed_consent"),
    ("DVRO禁枪", "run8_domestic_violence_restraining_order_firearm_restriction"),
    ("DVRO交枪", "c18_siegel_dvrosurrender"), ("枪支遗失／被盗报告", "c18_siegel_loststolen"),
    ("SYG", "c18_cspp_guncontrol_stand_your_ground"), ("MCDV禁枪", "c18_siegel_mcdv"),
])


# ---------------------------------------------------------------------------------------------
# archive part: carried from v1 (frozen results do not change), slimmed
# ---------------------------------------------------------------------------------------------
def slim_archive_slot(f):
    keep = ("persona", "center", "interval80", "p_positive", "p_negative", "p_near_zero", "event_definition",
            "units", "diagnostic_pass_p", "timestamp", "locked_file_sha256", "current_digest_matches",
            "exact_positive_event", "historical_event_compatible")
    return {k: f.get(k) for k in keep}


def archive_part(v1):
    designs, rows = [], []
    arch_ids = [d["design_id"] for d in v1["designs"] if d.get("source_group") != "pipeline"]
    for d in v1["designs"]:
        if d["design_id"] in arch_ids:
            z = {k: d[k] for k in ("design_id", "case_key", "domain", "domain_source", "batch", "status",
                                   "technical_point_available", "project_reportable", "current_project_reportable",
                                   "stop_reasons", "source", "registry_design_ids", "crosswalk_status",
                                   "policy_family_id", "outcome_order") if k in d}
            if d.get("amendment_display"):
                z["amendment_display"] = d["amendment_display"]
            z["source_group"] = d.get("source_group") or d["batch"]
            z["origin"] = "archive (carried unchanged from v1)"
            z["added_in_v2"] = False
            designs.append(z)
    for r in v1["rows"]:
        if r["design_id"] in arch_ids:
            nz = r.get("noise") or {}
            z = {
                "row_id": r["row_id"], "design_id": r["design_id"], "outcome_id": r["outcome_id"],
                "source_group": r["batch"], "outcome_label": r["outcome_label"], "outcome_unit": r.get("outcome_unit"),
                "domain": r["domain"], "outcome_order": r["outcome_order"],
                "designated_l3_primary": r["designated_l3_primary"], "status": r["status"],
                "technical_point_available": r["technical_point_available"],
                "project_reportable": r["project_reportable"], "stop_reasons": r["stop_reasons"],
                "b": r["b"], "se": r["se"], "se_method": r.get("sampling_se_method"), "ci95": r["ci95"],
                "interval_method": r.get("interval_method"),
                "noise": {k: nz.get(k) for k in ("sample_sd", "absolute_cutoff", "p_two_sided")} if nz else None,
                "noise_exceedance": r["noise_exceedance"],
                "signed_noise_standardized_estimate": r["signed_noise_standardized_estimate"],
                "Q1_descriptive_eligible": r["Q1_descriptive_eligible"],
                "forecast_slots": [slim_archive_slot(f) for f in r.get("forecasts") or []],
                "forecast_missing_reason": r.get("forecast_missing_reason"),
                "s_pre": r.get("s_pre"), "H": r.get("H"),
                "source": r["source"], "sampling_variance_valid_for_Q4": False,
                "meta_primary_unavailable_reasons": r["meta_primary_unavailable_reasons"],
                "meta_primary_test_status": "UNAVAILABLE_DEVELOPMENT_ONLY",
            }
            if r.get("amendment_display"):
                z["amendment_display"] = r["amendment_display"]
            rows.append(z)
    return designs, rows


# ---------------------------------------------------------------------------------------------
# pipeline part: latest valid run per design from TERMINAL_RUNS.json
# ---------------------------------------------------------------------------------------------
def latest_runs(reg):
    """Latest registered run per design (append-only order). A deviation replaces its failed/erroneous original."""
    latest, history = OrderedDict(), defaultdict(list)
    for r in reg["runs"]:
        history[r["design_id"]].append(r)
        latest[r["design_id"]] = r
    return latest, history


def find_package(runs, name):
    for r in reversed(runs):
        p = Path(r["package_path"]) / name
        if p.exists():
            return Path(r["package_path"])
    return None


def lock_slots(did, runs, result):
    """Locked three/five-slot roster, cross-checked against submission bytes."""
    pk = None
    for r in runs:
        if (Path(r["package_path"]) / "PREDICTIONS_LOCK.json").exists():
            pk = Path(r["package_path"])
    rows = (result.get("forecast_comparison") or {}).get("rows") or []
    lock_files, subs, lock = {}, {}, None
    if pk:
        lock = jload(pk / "PREDICTIONS_LOCK.json")
        lock_files = lock.get("files", {})
        for fpath, h in lock_files.items():
            fp = pk / fpath
            ok = fp.exists() and sha(fp) == h
            if fp.exists():
                s = json.loads(fp.read_text())
                subs[s["persona"]] = {"point": s["point"], "interval": s["interval"], "hash_ok": ok, "sha256": h}
    slots = []
    for x in rows:
        s = subs.get(x["persona"])
        slots.append({
            "persona": x["persona"], "center": x["point"], "interval80": x["interval"], "p_positive": None,
            "exact_positive_event": False, "event_definition": "point + central 80% interval; no explicit P(b>0)",
            "lock_file_sha256": s["sha256"] if s else None,
            "lock_verified": bool(s and s["hash_ok"] and s["point"] == x["point"] and list(s["interval"]) == list(x["interval"])),
        })
    return slots, (str(pk) if pk else None), lock


def build_pipeline(v1, reg):
    latest, history = latest_runs(reg)
    v1p = {d["design_id"]: d for d in v1["designs"] if d.get("source_group") == "pipeline"}
    v1rows = {r["design_id"]: r for r in v1["rows"] if r.get("source_group") == "pipeline"}
    designs, rows, secondary, superseded = [], [], [], []

    for did, r in latest.items():
        res = jload(r["result_path"])
        assert sha(r["result_path"]) == r["result_sha256"], did
        runs = history[did]
        l3x = Path(r["package_path"]) / "L3_EXPORT_DATA.json"
        l3_export = None
        if l3x.exists():
            note(l3x)
            l3_export = read_export(l3x)
        card_pk = find_package(runs, "CARD.json")
        card = jload(card_pk / "CARD.json")
        prim = res["registered_results"]["primary"]
        ov = prim["result"]["overall"]
        nf = res["noise_floor"]
        feas = (nf.get("feasibility") or {})
        gates = {g["gate"]: g["passed"] for g in res["gate_table"] if g.get("check_class") == "B"}
        status = res["status"]
        v1d, v1r = v1p.get(did), v1rows.get(did)
        fo = (card.get("first_order") or {})
        if v1r and v1r.get("outcome_order") in ("first_order", "second_order", "unknown"):
            order, order_src = v1r["outcome_order"], "v1 retrospective first-order card classification"
        else:
            order = "first_order" if fo.get("relation") in ("direct", "closest_proxy") else "unknown"
            order_src = f"CARD.first_order.relation={fo.get('relation')}"
        if v1d:
            domain, dsrc = v1d["domain"], "v1 roster domain"
        else:
            domain, dsrc = NEW_DOMAIN[did]
        outc = card.get("outcome") or {}
        if v1r:
            ocl, ounit, oid = v1r["outcome_label"], v1r.get("outcome_unit"), v1r.get("outcome_id")
        else:
            ocl = (f"{outc.get('measure_id')}; " if isinstance(outc, dict) else "") + str((card.get("population") or {}).get("description"))
            ounit = outc.get("outcome_unit") if isinstance(outc, dict) else None
            oid = outc.get("measure_id") if isinstance(outc, dict) else None
        slots, lock_pkg, forecast_lock = lock_slots(did, runs, res)
        forecast_fields = forecast_summary(slots, forecast_lock)
        b = ov["att"]
        cutoff, sd = nf["absolute_cutoff"], nf["sample_sd"]
        comparable = bool(feas.get("support_pass", True))
        exceed = (abs(b) > cutoff) if comparable else None
        assert exceed == nf.get("strictly_exceeds_cutoff"), (did, exceed, nf.get("strictly_exceeds_cutoff"))
        supers = [x for x in runs if x["run_id"] != r["run_id"]]
        for x in supers:
            superseded.append({"design_id": did, "run_id": x["run_id"], "status": x["status"],
                               "spec_version": x.get("spec_version"), "result_sha256": x.get("result_sha256"),
                               "replaced_by": r["run_id"],
                               "why_not_counted": ("original attempt failed before fitting (FAILED_NO_RETRY); rerun under deviation"
                                                   if x["status"] == "FAILED_NO_RETRY" else
                                                   "original run used a wrong operative clock; corrected under deviation 002 (first record kept)"
                                                   if did == "run8_extreme_risk_protection_order" else "superseded"),
                               "original_b": (jload(x["result_path"])["registered_results"]["primary"]["result"]["overall"]["att"]
                                              if x["status"] == "COMPLETED" else None)})
        reportable = status == "COMPLETED"
        d = {
            "design_id": did, "source_group": "pipeline", "origin": "terminal L2 pipeline run (latest valid)",
            "domain": domain, "domain_source": dsrc, "status": status,
            "technical_point_available": True, "project_reportable": reportable,
            "current_project_reportable": reportable,
            "stop_reasons": res.get("stop_issues") or None, "outcome_order": order,
            "policy_family": card.get("policy_family"), "family_cluster_dev": CLUSTER_OF.get(did, did),
            "lineage": card.get("lineage"), "latest_run_id": r["run_id"], "latest_run_is_deviation": r["is_deviation"],
            "attempts_total": len(runs), "added_in_v2": did not in v1p or v1p[did]["status"] != status,
            "v1_status": v1p[did]["status"] if did in v1p else None,
            "treatment_source_type": (jload(card_pk / "TREATMENT.json").get("source_type")
                                      if (card_pk / "TREATMENT.json").exists() else None),
            "certified_full_state_year_history_file": (card_pk / "CERTIFIED_HISTORY.json").exists(),
            "party_risk_certified": False,
        }
        designs.append(d)
        row = {
            "row_id": f"{did}::primary", "design_id": did, "outcome_id": oid, "source_group": "pipeline",
            "outcome_label": ocl, "outcome_unit": ounit, "domain": domain, "outcome_order": order,
            "outcome_order_source": order_src, "designated_l3_primary": True, "status": status,
            "technical_point_available": True, "project_reportable": reportable, "stop_reasons": d["stop_reasons"],
            "b": b, "se": ov["se"], "se_method": (prim["result"].get("inference") or {}).get("method"),
            "ci95": ov.get("ci95"), "interval_method": (ov.get("interval") or {}).get("method"),
            "noise": {"sample_sd": sd, "absolute_cutoff": cutoff, "p_two_sided": nf.get("p_two_sided"),
                      "feasible_fraction": feas.get("feasible_fraction"), "draws": nf.get("draws"),
                      "support_pass": comparable},
            "noise_exceedance": exceed,
            "signed_noise_standardized_estimate": (b / sd) if sd and sd > 0 else None,
            "Q1_descriptive_eligible": exceed is not None,
            "gates_class_B": gates,
            "forecast_slots": slots, "forecast_lock_package": rel(lock_pkg) if lock_pkg else None,
            "forecast_independence": (l3_export or {}).get("forecast_independence", {}),
            "N10_eligible": n10_eligible(l3_export or {}),
            **forecast_fields,
            "s_pre": None, "s_pre_missing_reason": "Registered raw clean-preperiod outcome SD not stored as a frozen scalar; two-way-residual ANCHOR SD is a different quantity",
            "H": None, "H_missing_reason": "No singular frozen supported-horizon-in-years scalar located",
            "source": {"path": rel(r["result_path"]), "sha256": r["result_sha256"], "run_id": r["run_id"],
                       "pointer": "/registered_results/primary/result/overall",
                       "l3_export_sha256": sha(l3x) if l3x.exists() else None},
            "sampling_variance_valid_for_Q4": False,
            "meta_primary_unavailable_reasons": {
                "Q2": "No certified strict published match; standardized scale/covariance unavailable",
                "Q4": "Certified Jan-1 party at-risk archive, valid variance, raw outcome SD and shared covariance unavailable",
                "Q5": "Historical status only; signed age60/wave endpoint unavailable"},
            "meta_primary_test_status": "UNAVAILABLE_DEVELOPMENT_ONLY",
        }
        rows.append(row)
        for k, x in res["registered_results"].items():
            if not k.startswith("secondary"):
                continue
            xo = ((x.get("result") or {}).get("overall")) if isinstance(x.get("result"), dict) else None
            xn = x.get("noise_floor") or {}
            secondary.append({"design_id": did, "outcome_id": k.replace("secondary_", "", 1), "status": x.get("status"),
                              "estimate": xo["att"] if xo else None,
                              "noise_cutoff": xn.get("absolute_cutoff"),
                              "strictly_exceeds_cutoff": xn.get("strictly_exceeds_cutoff"),
                              "reason": x.get("reason")})

    # pre-run stops that never produced a later run (terminal without estimate)
    seen = set(latest)
    for p in reg["pre_run_stops"]:
        did = p["design_id"]
        if did in seen:
            superseded.append({"design_id": did, "run_id": p["run_id"], "status": "STOPPED (pre-run)",
                               "spec_version": p.get("spec_version"), "result_sha256": None,
                               "replaced_by": latest[did]["run_id"],
                               "why_not_counted": "sealed pre-run stop later repaired and run; first record kept", "original_b": None})
            continue
        spk = Path(p["spec_path"]).parent
        card = jload(spk / "CARD.json")
        fo = card.get("first_order") or {}
        domain, dsrc = NEW_DOMAIN[did]
        slots = []; lock = None
        if (spk / "PREDICTIONS_LOCK.json").exists():
            lock = jload(spk / "PREDICTIONS_LOCK.json")
            for fpath, h in lock["files"].items():
                s = json.loads((spk / fpath).read_text())
                slots.append({"persona": s["persona"], "center": s["point"], "interval80": s["interval"],
                              "p_positive": None, "exact_positive_event": False,
                              "event_definition": "point + central 80% interval; no explicit P(b>0)",
                              "lock_file_sha256": h, "lock_verified": sha(spk / fpath) == h})
        d = {"design_id": did, "source_group": "pipeline", "origin": "pre-run terminal stop (never executed)",
             "domain": domain, "domain_source": dsrc, "status": "PRE_RUN_STOP", "technical_point_available": False,
             "project_reportable": False, "current_project_reportable": False, "stop_reasons": [p["reason"]],
             "outcome_order": "first_order" if fo.get("relation") in ("direct", "closest_proxy") else "unknown",
             "policy_family": card.get("policy_family"), "family_cluster_dev": CLUSTER_OF.get(did, did),
             "lineage": card.get("lineage"), "latest_run_id": p["run_id"], "latest_run_is_deviation": False,
             "attempts_total": 1, "added_in_v2": True, "v1_status": None, "treatment_source_type": None,
             "certified_full_state_year_history_file": (spk / "CERTIFIED_HISTORY.json").exists(), "party_risk_certified": False}
        designs.append(d)
        rows.append({
            "row_id": f"{did}::UNAVAILABLE_PRIMARY", "design_id": did, "outcome_id": (card.get("outcome") or {}).get("measure_id"),
            "source_group": "pipeline", "outcome_label": str((card.get("population") or {}).get("description")),
            "outcome_unit": (card.get("outcome") or {}).get("outcome_unit"), "domain": domain,
            "outcome_order": d["outcome_order"], "designated_l3_primary": True, "status": "PRE_RUN_STOP",
            "technical_point_available": False, "project_reportable": False, "stop_reasons": d["stop_reasons"],
            "b": None, "se": None, "ci95": None, "noise": None, "noise_exceedance": None,
            "signed_noise_standardized_estimate": None, "Q1_descriptive_eligible": False,
            "point_missing_reason": f"{p['reason']} ({p['stop_rule']}; feasible {p['feasible_draws']}/{p['requested_draws']} draws); study not executed",
            "forecast_slots": slots, **forecast_summary(slots, lock),
            "s_pre": None, "H": None,
            "source": {"path": rel(p["terminal_record_path"]), "sha256": p["terminal_record_sha256"], "run_id": p["run_id"]},
            "sampling_variance_valid_for_Q4": False,
            "meta_primary_unavailable_reasons": {"Q1": "Pre-run stop: no estimate", "Q2": "No estimate / no strict match",
                                                 "Q4": "No estimate", "Q5": "Terminal stop recorded; age60 endpoint unavailable"},
            "meta_primary_test_status": "UNAVAILABLE_DEVELOPMENT_ONLY"})

    # v1 pipeline terminal S3 support stops that are not in the registry stay in D
    carried = []
    for did, d in v1p.items():
        if did in latest or did in {p["design_id"] for p in reg["pre_run_stops"]}:
            continue
        assert d["status"] == "SUPPORT_STOPPED", did
        z = {k: d[k] for k in ("design_id", "domain", "domain_source", "status", "technical_point_available",
                               "project_reportable", "current_project_reportable", "stop_reasons", "lineage",
                               "outcome_order") if k in d}
        z.update({"source_group": "pipeline", "origin": "terminal S3 support stop (carried from v1; not in registry)",
                  "policy_family": d.get("policy_family_id"), "family_cluster_dev": CLUSTER_OF.get(did, did),
                  "latest_run_id": None, "attempts_total": 1, "added_in_v2": False, "v1_status": d["status"],
                  "certified_full_state_year_history_file": False, "party_risk_certified": False})
        designs.append(z)
        r1 = v1rows[did]
        rows.append({
            "row_id": r1["row_id"], "design_id": did, "outcome_id": r1.get("outcome_id"), "source_group": "pipeline",
            "outcome_label": r1.get("outcome_label"), "outcome_unit": r1.get("outcome_unit"), "domain": d["domain"],
            "outcome_order": d["outcome_order"], "designated_l3_primary": True, "status": "SUPPORT_STOPPED",
            "technical_point_available": False, "project_reportable": False, "stop_reasons": d["stop_reasons"],
            "b": None, "se": None, "ci95": None, "noise": None, "noise_exceedance": None,
            "signed_noise_standardized_estimate": None, "Q1_descriptive_eligible": False,
            "point_missing_reason": "Terminal S3 support stop; policy effect never technically estimated",
            "forecast_slots": [], "forecast_missing_reason": "No locked persona ledger for this unsupported design",
            "s_pre": None, "H": None, "source": {"path": (r1.get("provenance") or [{}])[0].get("path"),
                                                "sha256": (r1.get("provenance") or [{}])[0].get("sha256")},
            "sampling_variance_valid_for_Q4": False,
            "meta_primary_unavailable_reasons": {"Q1": "S3 support stop: no estimate", "Q2": "No estimate",
                                                 "Q4": "No estimate", "Q5": "Terminal stop recorded; age60 endpoint unavailable"},
            "meta_primary_test_status": "UNAVAILABLE_DEVELOPMENT_ONLY"})
        carried.append(did)
    return designs, rows, secondary, superseded, carried, latest


# ---------------------------------------------------------------------------------------------
# cross-checks against the human-readable scoreboard and status docs
# ---------------------------------------------------------------------------------------------
NUM = re.compile(r"[-+−]?\d+(?:\.\d+)?")


def scoreboard_check(rows):
    note(SCOREBOARD)
    byid = {r["design_id"]: r for r in rows}
    out, bad = [], []
    superseded_erpo = None
    for line in SCOREBOARD.read_text().splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("|政策"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        name = cells[0]
        if name not in SCOREBOARD_NAMES:
            bad.append(("unknown scoreboard row", name))
            continue
        did = SCOREBOARD_NAMES[name]
        m = NUM.search(cells[2].replace("−", "-"))
        sb_b = float(m.group(0).replace("+", "")) if cells[2][0] not in "—" and m else None
        flag = cells[3]
        if did is None:
            superseded_erpo = sb_b
            continue
        r = byid[did]
        if sb_b is None:
            ok = r["b"] is None and flag == "—"
        else:
            ok = r["b"] is not None and abs(round(r["b"], 3) - sb_b) < 1.5e-3 + 1e-9
            ok = ok and ((flag == "Y") == bool(r["noise_exceedance"])) and flag in ("Y", "N")
        out.append({"design_id": did, "scoreboard_b": sb_b, "build_b": r["b"], "scoreboard_flag": flag, "match": ok})
        if not ok:
            bad.append((did, sb_b, r["b"], flag))
    assert not bad, bad
    return {"rows_compared": len(out), "mismatches": 0, "superseded_erpo_row_value": superseded_erpo}


def status_doc_medians(rows):
    """Locked forecast medians quoted in C15/C16A/C28/C31/C34 vs the locked ledgers."""
    quoted = {  # design -> median quoted in the status doc (see doc column)
        "run8_gunshow_background_check": (-10.0, "C15"), "run8_universal_background_check": (-15.0, "C15"),
        "run7_refundable_eitc": (1.0, "C15"), "run8_extreme_risk_protection_order": (-15.0, "C16A"),
        "run8_large_capacity_magazine_ban": (-5.0, "C16A"), "c18_cspp_guncontrol_stand_your_ground": (1.0, "C28/C31/C34"),
        "run8_domestic_violence_restraining_order_firearm_restriction": (-0.1, "C28/C31/C34"),
        "run7_abortion_informed_consent": (1.0, "C28/C31/C34"), "c18_siegel_mcdv": (-0.08, "C28/C31/C34"),
        "c18_siegel_mcdvsurrender": (-0.08, "C28/C31/C34"),
    }
    byid = {r["design_id"]: r for r in rows}
    out = []
    for did, (q, doc) in quoted.items():
        m = byid[did]["forecast_median_of_five"]
        assert abs(m - q) < 1e-9, (did, m, q)
        out.append({"design_id": did, "doc": doc, "quoted_median": q, "ledger_median": m})
    for doc in STATUS_DOCS:
        note(ROOT / "docs/plans/l2-pipeline" / doc)
    return out


# ---------------------------------------------------------------------------------------------
# question tabulations
# ---------------------------------------------------------------------------------------------
def sign(x):
    return (x > 0) - (x < 0)


def q1_counts(designs, rows, pred=None):
    ids = {d["design_id"] for d in designs if (pred is None or pred(d))}
    es = [r for r in rows if r["design_id"] in ids and r["Q1_descriptive_eligible"]]
    k, n, D = sum(bool(r["noise_exceedance"]) for r in es), len(es), len(ids)
    return {"D": D, "n": n, "k": k, "u": D - n, "rate_k_over_n": k / n if n else None,
            "coverage_n_over_D": n / D if D else None,
            "full_roster_bounds": [k / D, (k + D - n) / D] if D else None}


def pct(x):
    return None if x is None else round(100 * x, 2)


def score_forecasts(rows):
    per = []
    for r in rows:
        b, fs = r["b"], r.get("forecast_slots") or []
        if b is None or len(fs) != 5:
            continue
        sd = (r.get("noise") or {}).get("sample_sd")
        cs = [f["center"] for f in fs]
        hits = [float(m != 0 and (m > 0) == (b > 0)) for m in cs]
        cov = [float(f["interval80"][0] <= b <= f["interval80"][1]) for f in fs if f.get("interval80")]
        med = statistics.median(cs)
        ov = [sign(m) * (m - b) / sd for m in cs] if sd else None
        per.append({"design_id": r["design_id"], "source_group": r["source_group"], "domain": r["domain"],
                    "b": b, "forecast_median": med, "median_direction_hit": float(med != 0 and (med > 0) == (b > 0)),
                    "slot_direction_hit_rate": statistics.mean(hits), "interval80_coverage": statistics.mean(cov),
                    "overstatement_noise_sd": statistics.mean(ov) if ov else None,
                    "zero_centers": sum(m == 0 for m in cs)})
    return per


def summarize_scores(per):
    if not per:
        return None
    ov = [p["overstatement_noise_sd"] for p in per if p["overstatement_noise_sd"] is not None]
    return {"design_n": len(per), "slots": 5 * len(per),
            "slot_direction_hit_rate_equal_design": statistics.mean(p["slot_direction_hit_rate"] for p in per),
            "median_direction_hit_rate": statistics.mean(p["median_direction_hit"] for p in per),
            "median_direction_hits": int(sum(p["median_direction_hit"] for p in per)),
            "interval80_coverage_equal_design": statistics.mean(p["interval80_coverage"] for p in per),
            "overstatement_noise_sd_mean": statistics.mean(ov) if ov else None,
            "overstatement_noise_sd_median": statistics.median(ov) if ov else None,
            "overstatement_positive_designs": sum(o > 0 for o in ov), "overstatement_n": len(ov)}


def brier_skill(rows, full_five):
    sk, used = [], []
    for r in rows:
        # Three-slot sets are retained separately from the legacy Q3 contract.
        if r.get("forecast_roster_kind")=="prospective_three":
            continue
        b = r["b"]
        if b is None:
            continue
        sl = [f for f in r.get("forecast_slots") or [] if f.get("exact_positive_event") and f.get("p_positive") is not None]
        if not sl or (full_five and len(sl) != 5):
            continue
        y = 1.0 if b > 0 else 0.0
        sk.append(0.25 - statistics.mean((f["p_positive"] - y) ** 2 for f in sl))
        used.append({"design_id": r["design_id"], "exact_slots": len(sl), "skill": sk[-1]})
    return (statistics.mean(sk) if sk else None), used


def power_row(n):
    if n < 4:
        return {"n": n, "status": "NOT_ESTIMABLE"}
    nd = l3cli.NormalDist()
    zc, zp = nd.inv_cdf(0.995), nd.inv_cdf(0.80)
    ex = l3cli.exact_binomial_design(n)
    return {"n": n, "q1_reject_k_independent_calibrated": ex["reject_at_or_above"],
            "q1_rate_80power_independent_calibrated": ex["rate_for_80_percent_power"],
            "q2_q3_paired_mde_sd": (zc + zp) / math.sqrt(n),
            "q4_partial_r_mde": math.tanh((zc + zp) / math.sqrt(n - 3)),
            "proportion99_halfwidth_normal": zc * 0.5 / math.sqrt(n)}


def cohen_w_df5(n):
    if ncx2 is None or n < 1:
        return None
    crit = chi2.ppf(0.99, 5)
    lo, hi = 0.0, 400.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if ncx2.sf(crit, 5, mid) < 0.8:
            lo = mid
        else:
            hi = mid
    return math.sqrt(hi / n)


def main():
    v1 = jload(V1_PATH)
    reg = jload(REG_PATH)
    lit = jload(LIT_REG)
    assert reg["total_terminal_design_count_including_pre_run"] == 29

    a_designs, a_rows = archive_part(v1)
    p_designs, p_rows, secondary_p, superseded, carried, latest = build_pipeline(v1, reg)
    designs = a_designs + p_designs
    rows = a_rows + p_rows
    secondary = v1["secondary_outcomes"] + [dict(s, source_group="pipeline") for s in secondary_p]

    for d in designs:
        d["queue_group"] = queue_group(d)
    ids = [d["design_id"] for d in designs]
    assert len(ids) == len(set(ids)), "duplicate design"
    rid = [r["row_id"] for r in rows]
    assert len(rid) == len(set(rid))
    D = len(designs)
    byid = {d["design_id"]: d for d in designs}
    reg_designs = [d for d in p_designs if d["design_id"] not in carried]
    assert len(reg_designs) == 29 and len(a_designs) == 21 and len(carried) == 8

    # registry summary check (scoreboard header counts)
    st = Counter(d["status"] for d in reg_designs)
    assert st == Counter({"COMPLETED": 24, "STOPPED": 3, "PRE_RUN_STOP": 2}), st
    sb = scoreboard_check(rows)
    med_check = status_doc_medians(rows)
    note(ROOT / "docs/plans/l3-meta/PREREG_L3_META.md")
    note(ROOT / "docs/plans/l3-meta/RESULTS_DEV_V1.md")
    note(ROOT / "data/l3_meta/CODEBOOK_V1.md")

    # ---------------- Q1 ----------------
    E = [r for r in rows if r["Q1_descriptive_eligible"]]
    zs = sorted(r["signed_noise_standardized_estimate"] for r in E if r["signed_noise_standardized_estimate"] is not None)
    q1 = q1_counts(designs, rows)
    q1.update({
        "z_median": statistics.median(zs), "z_distribution": zs,
        "z_quartiles": [statistics.quantiles(zs, n=4)[i] for i in range(3)], "z_min": zs[0], "z_max": zs[-1],
        "share_abs_z_gt_1_96": sum(abs(z) > 1.96 for z in zs) / len(zs), "n_abs_z_gt_1_96": sum(abs(z) > 1.96 for z in zs),
        "exceeding_designs": [r["design_id"] for r in E if r["noise_exceedance"]],
        "by_source": {"archive": q1_counts(designs, rows, lambda d: d["source_group"] != "pipeline"),
                      "pipeline": q1_counts(designs, rows, lambda d: d["source_group"] == "pipeline"),
                      "pipeline_registry_29": q1_counts(designs, rows, lambda d: d["source_group"] == "pipeline" and d["design_id"] not in carried)},
        "by_domain": {dom: q1_counts(designs, rows, lambda d, dom=dom: d["domain"] == dom) for dom in DOMAINS},
        "sensitivity_gate_passing_only": {
            "note": "Descriptive only; prereg keeps designs that fail outcome-based diagnostic gates in the primary.",
            **{"n": sum(1 for r in E if byid[r["design_id"]]["current_project_reportable"]),
               "k": sum(1 for r in E if byid[r["design_id"]]["current_project_reportable"] and r["noise_exceedance"])}},
        "expected_exceedances_under_reference_5pct": 0.05 * len(E),
        "eligible_policy_families_dev_grouping": len({CLUSTER_OF.get(r["design_id"], r["design_id"]) for r in E}),
        "family_gate_min": 20,
        "test_status": "UNAVAILABLE: no validated joint calibrated observational null replays; family-block inference not run",
        "p_value": None, "interval99": None,
        "latent_estimand": "NOT_IDENTIFIED: valid sampling variance, joint covariance and synthetic recovery unavailable",
    })
    q1["by_source"]["pipeline_registry_29"]["note"] = "the 29 designs in TERMINAL_RUNS.json (24 COMPLETED, 3 post-run STOPPED, 2 pre-run STOPPED)"

    # outcome-order split (description only; prereg v0.1 has no explicit split)
    order_split = {}
    for o in ("first_order", "second_order", "unknown"):
        order_split[o] = q1_counts(designs, rows, lambda d, o=o: d["outcome_order"] == o)
    # ---------------- Q2 ----------------
    q2_audit = []
    v1q2 = v1["questions"]["Q2"]
    prev = {x["design_id"]: x for x in v1q2["archive_designs"] + v1q2["pipeline_completed_designs"]}
    lit_ids = {s["study_id"]: s for s in lit["studies"]}
    special = {
        "run4_lit_family_planning_income_waiver": ("family_planning_income_waivers",
            "Published Kearney-Levine effect is on the log birth rate (conception-year clock); ours is births per 1,000 (birth-year clock). Registry gap note itself disables the replication gap for clock/units/population."),
        "run4_lit_nurse_licensure_compact": ("depasquale_stange_2016",
            "Published hours are usual weekly hours last year for RN+LPN in Census/ACS (all settings); ours is actual reference-week hours of hospital RNs at work in CPS. Population, measure and source all differ."),
    }
    for d in designs:
        did = d["design_id"]
        if did in special:
            sid, why = special[did]
            q2_audit.append({"design_id": did, "q2_strict_match": False, "candidate_study_ids": [sid], "reason": why})
        elif did in prev:
            q2_audit.append({"design_id": did, "q2_strict_match": False,
                             "candidate_study_ids": prev[did].get("registry_candidate_study_ids", []), "reason": prev[did]["q2_reason"]})
        else:
            q2_audit.append({"design_id": did, "q2_strict_match": False, "candidate_study_ids": [],
                             "reason": "No policy/outcome matching published estimate among the 74 literature-registry studies; support stops and pre-run stops have no estimate to compare."})
    assert not any(x["q2_strict_match"] for x in q2_audit)
    q2 = {"strict_match_requirements": v1q2["strict_match_requirements"], "D": D, "eligible_n": 0,
          "matched_families": 0, "family_gate_min": 20, "published_gap_G": None, "test": None,
          "true_sentence_supported": False, "false_sentence_is_untested_not_a_null_claim": True,
          "no_blind_two_extractor_lock_for_new_registry_matches": True,
          "literature_registry": {"path": rel(LIT_REG), "sha256": sha(LIT_REG), "studies_retained": len(lit["studies"]),
                                  "counts": lit["counts"]},
          "new_since_v1": "Two literature-built designs finished (family-planning waiver, nurse licensure compact); neither passes the strict match. The other new designs (firearm laws, EITC, abortion consent) have no study in the registry.",
          "audit": q2_audit}

    # ---------------- Q3 ----------------
    full_skill, full_used = brier_skill(rows, True)
    avail_skill, avail_used = brier_skill(rows, False)
    per = score_forecasts(rows)
    arch_per = [p for p in per if p["source_group"] != "pipeline"]
    pipe_per = [p for p in per if p["source_group"] == "pipeline"]
    v1q3 = v1["questions"]["Q3"]
    assert abs(full_skill - v1q3["full5_skill"]) < 1e-9 and abs(avail_skill - v1q3["available_slots_skill_equal_design"]) < 1e-9
    q3 = {"D": D, "primary_contract": "five explicit P(b>0) slots per design",
          "prospective_three_slot_note": "Three-slot sets are retained as metadata and excluded from both legacy Q3 forecast denominators; N10 additionally excludes SINGLE_CONSENSUS.",
          "eligible_primary_contract_n": 0, "prospective_primary_n": 0,
          "complete_five_P_gt0": {"n": len(full_used), "skill_S": full_skill, "designs": full_used,
                                  "mean_brier": 0.25 - full_skill},
          "available_slot_P_gt0": {"design_n": len(avail_used), "skill_S": avail_skill, "designs": avail_used},
          "locked_median_and_interval_scores": {
              "all": summarize_scores(per), "archive": summarize_scores(arch_per), "pipeline": summarize_scores(pipe_per),
              "note": "Descriptive. Centers are medians of five internal AI persona forecasts per design; 80% intervals scored against the frozen primary b. Zero centers count as misses. Overstatement uses each design's own noise SD as scale because the raw clean-preperiod SD s_i is not stored (development proxy for O).",
              "designs": per},
          "designs_with_scored_ledger_but_no_P_gt0": sum(1 for p in per) - len(avail_used),
          "pre_run_stop_ledgers_unscored": [r["design_id"] for r in rows if r["status"] == "PRE_RUN_STOP" and r.get("forecast_slots")],
          "diagnostic_event_score": v1q3["diagnostic_event_score"],
          "test_status": "UNAVAILABLE: no design beyond the two batch1 ledgers carries five explicit P(b>0); calibrated joint score resampling not run",
          "p_value": None, "interval99": None}
    ratios = [(p["forecast_median"], p["b"]) for p in per]
    q3["locked_median_and_interval_scores"]["median_vs_b_sign_table"] = {
        "both_positive": sum(m > 0 and b > 0 for m, b in ratios), "both_negative": sum(m < 0 and b < 0 for m, b in ratios),
        "median_pos_b_neg": sum(m > 0 and b < 0 for m, b in ratios), "median_neg_b_pos": sum(m < 0 and b > 0 for m, b in ratios),
        "median_zero": sum(m == 0 for m, b in ratios)}

    # ---------------- Q4 ----------------
    v1q4 = v1["questions"]["Q4"]
    q4 = {"D": D, "eligible_n": 0, "eligible_families": 0, "family_gate_min": 20,
          "coefficient_beta_A": None, "A_not_computed": True, "new_adoption_associations_not_fit": True,
          "readiness_counts_no_association": {
              "designs_with_frozen_treatment_clock_and_point": sum(1 for d in designs if d.get("technical_point_available") and d["source_group"] == "pipeline"),
              "pipeline_designs_with_certified_full_state_year_history_file": sum(1 for d in designs if d.get("certified_full_state_year_history_file")),
              "designs_with_certified_jan1_party_at_risk_archive": 0,
              "designs_with_valid_sampling_variance_for_Q4": 0},
          "reason": "No design has the separately frozen Jan-1 party at-risk first-adoption index (>=20 risk-years per party group) with valid variance, raw preperiod SD and horizon. Certified policy histories supply adoption years and at-risk state-years only; party coding is a separate gate. Computing A after the effects are visible would break the prereg, so none is computed.",
          "pooled_adoption_reference": "separate frozen study C17 (docs/plans/l3-pooled/RESULTS.md); not a Q4 substitute",
          "true_sentence_supported": False, "false_sentence_is_untested_not_a_null_claim": True}

    # ---------------- Q5 ----------------
    q5dom = {}
    for dom in DOMAINS:
        ds = [d for d in designs if d["domain"] == dom]
        tech = sum(d["technical_point_available"] for d in ds)
        rep = sum(d["current_project_reportable"] for d in ds)
        reg_ds = [d for d in ds if d["source_group"] == "pipeline"]
        pass_s3 = sum(1 for d in reg_ds if d["status"] != "SUPPORT_STOPPED")
        q5dom[dom] = {"D": len(ds), "technical_point_n": tech, "technical_rate": tech / len(ds) if ds else None,
                      "current_reportable_n": rep, "reportable_rate": rep / len(ds) if ds else None,
                      "pipeline_D": len(reg_ds), "pipeline_passed_S3": pass_s3,
                      "pipeline_estimable": sum(d["technical_point_available"] for d in reg_ds),
                      "pipeline_reportable": sum(d["current_project_reportable"] for d in reg_ds),
                      "age60_eligible_n": 0}
    pipe = [d for d in designs if d["source_group"] == "pipeline"]
    by_queue = {}
    for qg in ("archive_hand_picked", "early_L1_queue", "literature_intake", "research_panel"):
        ds = [d for d in designs if d["queue_group"] == qg]
        by_queue[qg] = {"D": len(ds), "passed_S3": sum(d["status"] != "SUPPORT_STOPPED" for d in ds),
                        "S3_support_stops": sum(d["status"] == "SUPPORT_STOPPED" for d in ds),
                        "pre_run_stops": sum(d["status"] == "PRE_RUN_STOP" for d in ds),
                        "post_run_stops": sum(d["status"] == "STOPPED" for d in ds),
                        "estimable": sum(d["technical_point_available"] for d in ds),
                        "reportable": sum(d["current_project_reportable"] for d in ds),
                        "domains": dict(Counter(d["domain"] for d in ds))}
    q5 = {"D": D, "age60_eligible_n": 0, "by_queue_group": by_queue,
          "families_in_D_dev_grouping": len({CLUSTER_OF.get(d["design_id"], d["design_id"]) for d in designs}),
          "technical_point_n": sum(d["technical_point_available"] for d in designs),
          "current_reportable_n": sum(d["current_project_reportable"] for d in designs),
          "domain_counts": q5dom,
          "domains_with_at_least_5_designs": [k for k in SIX if q5dom[k]["D"] >= 5],
          "domain_gate": "each of 6 domains needs >=5 designs and >=20 independent families",
          "pipeline_funnel_retention": {
              "intake_terminal_D": len(pipe),
              "passed_data_support_S3": sum(d["status"] != "SUPPORT_STOPPED" for d in pipe),
              "frozen_and_attempted": sum(d["status"] != "SUPPORT_STOPPED" for d in pipe),
              "estimable_point": sum(d["technical_point_available"] for d in pipe),
              "reportable_all_gates_pass": sum(d["current_project_reportable"] for d in pipe),
              "stage_stops": Counter(d["status"] for d in pipe),
              "note": "legal-verification and linkage stages are not separately recorded for terminal designs; infrastructure failures (3 FAILED_NO_RETRY loads, 2 sealed pre-run stops) were repaired and re-run and are not counted as stops"},
          "test_status": "UNAVAILABLE: no frozen enrollment waves, age60 endpoint, full six-domain support or validated wild-score null",
          "p_value": None, "interval99": None}

    # ---------------- thresholds / distance ----------------
    n_terminal_cert = 21 + 29
    thresholds = {
        "total_terminal_designs_certified_registry_basis": n_terminal_cert,
        "total_terminal_designs_incl_carried_S3_stops": D,
        "milestone_50": {"reached": n_terminal_cert >= 50, "meaning": "metadata and throughput review only; no interim primary estimates"},
        "checkpoint_100": {"target": 100, "remaining_registry_basis": 100 - n_terminal_cert, "remaining_incl_carried": 100 - D},
        "pristine_prospective_designs": {"n": 0, "of": 79,
                                         "reason": "no signed L3 enrollment freeze; every design above was exposed before any freeze (development evidence)"},
        "confirmatory_analysis": "NOT RUN: waits for the signed freeze and the 100-design checkpoint (or freeze + 6 months), then age60 closure",
        "Q1_to_Q4_need_>=20_eligible_families_and_validated_shared_shock_inference": {
            "Q1_eligible_families_dev_grouping": q1["eligible_policy_families_dev_grouping"], "Q2_matched_families": 0,
            "Q3_complete_five_P_gt0_designs": len(full_used), "Q4_eligible_families": 0},
        "Q5_domain_support_designs_per_domain": {k: q5dom[k]["D"] for k in SIX},
    }

    # ---------------- power / changes from v1 ----------------
    power = {"assumptions": v1["power_current_N"]["assumptions"],
             "roster": power_row(D), "Q1_eligible_n": power_row(q1["n"]),
             "Q3_complete_five_n": power_row(len(full_used)), "Q2_Q4_Q5_eligible_n": power_row(0),
             "Q5_ideal_D_roster_cohen_w_df5": cohen_w_df5(D)}
    v1q1 = v1["questions"]["Q1"]
    changes = {
        "Q1": {"v1": {"k": v1q1["k"], "n": v1q1["n"], "D": v1q1["D"], "rate": v1q1["eligible_rate"],
                      "coverage": v1q1["coverage"], "bounds": v1q1["full_roster_bounds"], "z_median": v1q1["z_median"]},
               "v2": {"k": q1["k"], "n": q1["n"], "D": q1["D"], "rate": q1["rate_k_over_n"], "coverage": q1["coverage_n_over_D"],
                      "bounds": q1["full_roster_bounds"], "z_median": q1["z_median"]}},
        "Q2": {"v1": {"matched_n": 0, "D": 45}, "v2": {"matched_n": 0, "D": D}},
        "Q3": {"v1": {"complete_five_n": v1q3["full5_n"], "S": v1q3["full5_skill"], "scored_designs": v1q3["all_ledger_secondary"]["design_n"],
                      "center_sign": v1q3["all_ledger_secondary"]["center_sign_rate_equal_design"],
                      "coverage80": v1q3["all_ledger_secondary"]["interval80_coverage_equal_design"]},
               "v2": {"complete_five_n": len(full_used), "S": full_skill, "scored_designs": q3["locked_median_and_interval_scores"]["all"]["design_n"],
                      "center_sign": q3["locked_median_and_interval_scores"]["all"]["slot_direction_hit_rate_equal_design"],
                      "coverage80": q3["locked_median_and_interval_scores"]["all"]["interval80_coverage_equal_design"]}},
        "Q4": {"v1": {"eligible_n": 0, "D": 45}, "v2": {"eligible_n": 0, "D": D}},
        "Q5": {"v1": {"D": 45, "technical_point_n": v1["questions"]["Q5"]["technical_point_n"],
                      "reportable_n": v1["questions"]["Q5"]["current_reportable_n"]},
               "v2": {"D": D, "technical_point_n": q5["technical_point_n"], "reportable_n": q5["current_reportable_n"]}},
    }

    sentence_decisions = {q: {"true_template_licensed": False, "false_template_licensed": False,
                              "EN": "Pending registered analysis", "ZH": "等待预注册分析",
                              "reason": "Registered primary test unavailable; descriptive direction alone cannot select a headline"}
                          for q in ("Q1", "Q2", "Q3", "Q4", "Q5")}

    out = OrderedDict([
        ("schema_version", "l3-meta-l2-development-v2"), ("as_of", AS_OF), ("evidence_status", "DEVELOPMENT"),
        ("label", "development, not confirmatory"),
        ("supersedes", {"path": rel(V1_PATH), "sha256": sha(V1_PATH)}),
        ("confirmatory_design_n", 0), ("scientific_primary_tests_computed", 0),
        ("new_policy_effect_estimation_calls", 0), ("bootstrap_or_L2_replay_calls", 0),
        ("alpha_each", 0.01), ("familywise_alpha", 0.05),
        ("source_roster_rule",
         "21 archive designs (13 prototypes + 8 batch1-3, carried from v1) + 29 terminal designs in TERMINAL_RUNS.json "
         "(latest valid run each; deviation runs replace failed/erroneous originals; originals retained in superseded_runs) "
         "+ 8 terminal S3 support stops carried from v1 that are not in the registry. Exclude nonterminal intake cards and synthetic/reviewer fixtures."),
        ("counts", {"design_D": D, "primary_outcome_rows": len(rows), "archive_designs": 21, "pipeline_designs": len(pipe),
                    "registry_terminal_designs": 29, "carried_S3_support_stops": 8,
                    "registry_run_attempts": reg["run_count"], "superseded_or_repaired_records": len(superseded),
                    "secondary_outcome_rows": len(secondary),
                    "pipeline_secondary_exceedances": sum(1 for s in secondary_p if s.get("strictly_exceeds_cutoff"))}),
        ("registry_snapshot", v1["registry_snapshot"]),
        ("terminal_registry_summary", reg["terminal_summary"]),
        ("thresholds", thresholds),
        ("roster_delta_vs_v1", {
            "v1_D": v1["counts"]["design_D"], "v2_D": D,
            "added_designs": [d["design_id"] for d in designs if d.get("added_in_v2") and d.get("v1_status") is None],
            "status_changed": [{"design_id": d["design_id"], "v1": d["v1_status"], "v2": d["status"]}
                               for d in designs if d.get("added_in_v2") and d.get("v1_status") is not None],
            "carried_unchanged": sum(1 for d in designs if not d.get("added_in_v2"))}),
        ("schema_notes", {
            "same_as_v1": "row fields b, se, ci95, noise, noise_exceedance, signed_noise_standardized_estimate, Q1_descriptive_eligible, designated_l3_primary, outcome_order keep their v1 meaning (see data/l3_meta/CODEBOOK_V1.md)",
            "new_in_v2": {
                "queue_group": "workflow route into the L2 queue: archive_hand_picked / early_L1_queue / literature_intake / research_panel",
                "family_cluster_dev": "development grouping of related designs; only used for the >=20-family gate",
                "forecast_slots[].center": "one persona's locked median; point + central 80% interval, no P(b>0) for pipeline ledgers",
                "forecast_median": "median of the verified locked roster; forecast_slot_count identifies three or five",
                "forecast_median_of_five": "five-slot sets only (null for three); median of the five persona centers (the 'locked forecast median' quoted in C15/C16A/C28/C31/C34)",
                "forecast_slots[].lock_verified": "file hash equals PREDICTIONS_LOCK and equals the scored result row",
                "superseded_runs": "retained original attempts that are not counted (failed, erroneous or sealed pre-run stops later repaired)",
                "status PRE_RUN_STOP": "registered stop before execution; no estimate, counted in D, not in n"},
            "slimmed_vs_v1": "archive forecast records keep persona, center, interval, probabilities, event definition, timestamp and lock hash; full raw ledgers stay in v1 JSON and the source files"}),
        ("designs", designs), ("rows", rows), ("secondary_outcomes", secondary), ("superseded_runs", superseded),
        ("pipeline_excluded_results", v1["pipeline_excluded_results"]),
        ("family_cluster_dev", FAMILY_CLUSTER),
        ("questions", OrderedDict([("Q1", q1), ("Q2", q2), ("Q3", q3), ("Q4", q4), ("Q5", q5)])),
        ("outcome_order_sensitivity", {"evidence_status": "DEVELOPMENT_DESCRIPTIVE",
                                       "note": "Requested split, not in prereg v0.1; retrospective classifications kept; no confirmatory order test.",
                                       **order_split}),
        ("power_current_N", power), ("changes_from_v1", changes), ("sentence_decisions", sentence_decisions),
        ("pooled_adoption_reference", v1["pooled_adoption_reference"]),
        ("checks", {"scoreboard_cross_check": sb, "status_doc_forecast_medians": med_check,
                    "result_sha256_matches_registry": True, "forecast_locks_verified_slots": sum(
                        1 for r in rows for f in r.get("forecast_slots") or [] if f.get("lock_verified")),
                    "design_ids_unique": True}),
        ("provenance", PROV),
        ("audit", {"downloads": [], "download_files": 0, "download_bytes": 0, "external_web_or_pdf_ingested": False,
                   "builder": "scripts/l3_meta/build_v2.py", "builder_sha256": sha(HERE / "build_v2.py"),
                   "writes": [rel(OUT)], "l2_pipeline_files_modified": 0,
                   "notes": ["Forecast lock times are not independently authenticated; locks are checked by file hash and by equality with scored result rows.",
                             "Zero-center forecasts count as direction misses (v1 convention).",
                             "Q1 family count uses a development grouping (family_cluster_dev); it only serves the >=20-family gate."]}),
    ])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    brief = {
        "D": D, "rows": len(rows), "Q1": {k: v for k, v in q1.items() if k not in ("z_distribution", "by_domain")},
        "order": order_split, "Q3": {k: v for k, v in q3.items() if k not in ("locked_median_and_interval_scores", "diagnostic_event_score")},
        "Q3_scores": {k: q3["locked_median_and_interval_scores"][k] for k in ("all", "archive", "pipeline", "median_vs_b_sign_table")},
        "Q5": {k: v for k, v in q5.items() if k != "domain_counts"}, "Q5_domains": q5dom, "thresholds": thresholds,
        "power": power, "checks": out["checks"]["scoreboard_cross_check"], "secondary_exceed": out["counts"]["pipeline_secondary_exceedances"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
