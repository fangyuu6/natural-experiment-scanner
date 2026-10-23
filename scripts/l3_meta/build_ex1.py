#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version V3 (rule id EX-1): Q1, Q3, Q5 and N10 on the live L2 set (work package A).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_THREE_SLOT.md (section 5, recorded before any V3 result was read),
PREREG_AMEND_20261012_EXPLORATORY.md (EX-1), and the frozen text of Q1/Q3/Q5 (PREREG_L3_META.md) and N10
(PREREG_L3_META_ADDENDUM_V2.md).  The labelled three-slot variants Q3-3 and N10-3 are in the same amendment.

READ-ONLY on every L2 file (registry, autopilot, jobs, evidence): nothing under scripts/l2_pipeline or
data/l2_pipeline is written, moved or locked.  No network, no download.  Only outputs:
data/l3_meta/ex1_v3/RESULTS_EX1_V3.json and data/l3_meta/ex1_v3/l2_set_live_used.json (a copy of the live set read).
Every statistic is exploratory: no "significant" label, no multiplicity adjustment, no licensed sentence.

Run:  .venv_did/bin/python -B scripts/l3_meta/build_ex1.py [--dry-run] [--out-dir DIR]
(numpy and scipy are needed: scipy only for mid-ranks.)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import statistics
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from build_live_set import load_top_level  # noqa: E402  (streaming reader for 100 MB RESULT.json files)
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
# one eligibility rule for lock writer, L3 reader and the carry sidecar (HUB_RULING_20261023_PARK_REOPEN.md section 4, Q3 ruling 3b)
from scripts.l2_pipeline.forecast_slots import INDEPENDENCE_KEYS, n10_eligible_record, read_carry  # noqa: E402

RULE_ID = "EX-1"
VERSION = "V3"
DATA = ROOT / "data"
LIVE = DATA / "l3_meta" / "l2_set_live.json"
V2 = DATA / "l3_meta" / "l2_set_v2.json"
REG = DATA / "l2_pipeline" / "registry" / "TERMINAL_RUNS.json"
ANNOT = DATA / "l2_pipeline" / "registry" / "FORECAST_SET_ANNOTATIONS.json"
AUTOPILOT = DATA / "l2_pipeline" / "autopilot"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_THREE_SLOT.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_HASH.txt"
RULING = ROOT / "docs/plans/l2-pipeline/HUB_RULING_20261015_CLAUDE_ONLY.md"
OUT_DIR = DATA / "l3_meta" / "ex1_v3"
OUT_NAME = "RESULTS_EX1_V3.json"

B_BOOT = 9999
SEED_Q1 = 20261007
SEED_N10 = [20261011, 10]
SEED_N10_3 = [20261011, 10, 3]
DOMAINS = ["labor", "public_finance", "health", "crime", "education", "environment", "other"]
GATES = ["support", "history", "connector", "ri", "fresh", "v3", "pre_run_stop", "other"]
# Registry deviation records -> reason code of a later certified run (EX-1 section 1; amendment 20261015 5.2).
LATER_RUN_CODES = {"erpo_20261009_deviation002": ("TIMING_CORRECTION", "docs/plans/l2-scale/DEVIATION_002_ERPO_CLOCK.md")}
FIVE = 5


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def sign(x):
    return (x > 0) - (x < 0)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, numpy scalars -> python, tuples -> lists."""
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def frac(k, n):
    return k / n if n else None


# ---------------------------------------------------------------------------------------------
# statistics (pure functions; unit-tested in test_build_ex1.py)
# ---------------------------------------------------------------------------------------------
def family_bootstrap_proportion(flags, fam, B=B_BOOT, seed=SEED_Q1):
    """Family-block bootstrap of an equal-weight proportion: F blocks drawn with replacement, pi* = hits / designs.

    Blocks are the sorted distinct values of `fam`.  Returns the array of B replicates (all defined).
    """
    flags = np.asarray(flags, dtype=float)
    fams = sorted(set(fam))
    pos = {f: i for i, f in enumerate(fams)}
    hits = np.zeros(len(fams))
    size = np.zeros(len(fams))
    for x, f in zip(flags, fam):
        hits[pos[f]] += x
        size[pos[f]] += 1
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(fams), size=(B, len(fams)))
    return hits[draws].sum(axis=1) / size[draws].sum(axis=1)


def percentile_pair(values, lo, hi):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    q = np.percentile(a, [lo, hi])
    return [float(q[0]), float(q[1])]


def _residual(a, c):
    X = np.column_stack([np.ones_like(c), c])
    beta, *_ = np.linalg.lstsq(X, a, rcond=None)
    return a - X @ beta


def partial_spearman(x, y, z):
    """N10 r10: mid-ranks of x, y, z; residuals of rank(x), rank(y) on an intercept and rank(z); Pearson r. NaN if undefined."""
    x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z))
    if len(x) < 3:
        return float("nan")
    rx, ry, rz = rankdata(x), rankdata(y), rankdata(z)
    ex, ey = _residual(rx, rz), _residual(ry, rz)
    if float(np.sum(ex ** 2)) < 1e-12 or float(np.sum(ey ** 2)) < 1e-12:
        return float("nan")
    return float(np.corrcoef(ex, ey)[0, 1])


def e4_pairs_bootstrap(disp, err, g, fam, B=B_BOOT, seed=SEED_N10):
    """E4: B replicates, each drawing F families with replacement (all designs of a drawn family, duplicates kept)."""
    disp, err, g = (np.asarray(v, dtype=float) for v in (disp, err, g))
    fams = sorted(set(fam))
    members = {f: [i for i, x in enumerate(fam) if x == f] for f in fams}
    rng = np.random.default_rng(seed)
    out = np.empty(B)
    for b in range(B):
        draw = rng.integers(0, len(fams), size=len(fams))
        idx = [i for k in draw for i in members[fams[k]]]
        out[b] = partial_spearman(disp[idx], err[idx], g[idx])
    return out


def e4_summary(r_star, point):
    defined = r_star[np.isfinite(r_star)]
    b_def = int(defined.size)
    out = {"B": int(r_star.size), "B_defined": b_def, "share_defined": b_def / r_star.size if r_star.size else None,
           "point": point, "p_value": None, "interval95": None}
    if b_def and b_def / r_star.size >= 0.9:
        le = (1 + int(np.sum(defined <= 0))) / (b_def + 1)
        ge = (1 + int(np.sum(defined >= 0))) / (b_def + 1)
        out["p_value"] = float(min(1.0, 2 * min(le, ge)))
        out["interval95"] = percentile_pair(defined, 2.5, 97.5)
    else:
        out["note"] = "fewer than 90% of replicates defined: no p-value or interval shown"
    return out


def brier_skill(slots, b):
    y = 1.0 if b > 0 else 0.0
    return 0.25 - statistics.mean((s["p_positive"] - y) ** 2 for s in slots)


def design_scores(b, slots, scale):
    """Q3 secondary scores of one design (five or three slots); zero point = miss; sign(0) = 0 in O."""
    centers = [s["center"] for s in slots]
    hits = [1.0 if (m != 0 and (m > 0) == (b > 0)) else 0.0 for m in centers]
    med = statistics.median(centers)
    cov = [1.0 if s["interval"][0] <= b <= s["interval"][1] else 0.0 for s in slots if s.get("interval_level") == 0.8]
    over = [sign(m) * (m - b) / scale for m in centers] if scale and scale > 0 else None
    return {"slot_hit_rate": statistics.mean(hits), "median": med, "median_hit": 1.0 if (med != 0 and (med > 0) == (b > 0)) else 0.0,
            "coverage80": statistics.mean(cov) if cov else None, "n_cov_slots": len(cov),
            "overstatement": statistics.mean(over) if over else None, "zero_centers": sum(1 for m in centers if m == 0)}


def summarize_scores(rows):
    """rows: list of dicts with design_scores keys plus b. Equal design weights."""
    if not rows:
        return {"design_n": 0}
    ov = [r["overstatement"] for r in rows if r["overstatement"] is not None]
    cov = [r["coverage80"] for r in rows if r["coverage80"] is not None]
    bs = [r["b"] for r in rows]
    tab = {"median_pos_b_pos": 0, "median_neg_b_neg": 0, "median_pos_b_neg": 0, "median_neg_b_pos": 0, "median_zero": 0}
    for r in rows:
        m, b = r["median"], r["b"]
        if m == 0:
            tab["median_zero"] += 1
        elif m > 0 and b > 0:
            tab["median_pos_b_pos"] += 1
        elif m < 0 and b < 0:
            tab["median_neg_b_neg"] += 1
        elif m > 0:
            tab["median_pos_b_neg"] += 1
        else:
            tab["median_neg_b_pos"] += 1
    return {"design_n": len(rows), "slots": None,
            "slot_hit_rate_equal_design": statistics.mean(r["slot_hit_rate"] for r in rows),
            "median_hits": int(sum(r["median_hit"] for r in rows)),
            "median_hit_rate": statistics.mean(r["median_hit"] for r in rows),
            "coverage80_equal_design": statistics.mean(cov) if cov else None, "coverage80_designs": len(cov),
            "overstatement_mean": statistics.mean(ov) if ov else None,
            "overstatement_median": statistics.median(ov) if ov else None,
            "overstatement_designs": len(ov), "overstatement_positive_designs": sum(1 for o in ov if o > 0),
            "zero_centers": int(sum(r["zero_centers"] for r in rows)),
            "median_vs_b_sign_table": tab,
            "naive_always_negative_hits": sum(1 for b in bs if b < 0), "naive_always_positive_hits": sum(1 for b in bs if b > 0)}


# ---------------------------------------------------------------------------------------------
# registry: first certified run per design
# ---------------------------------------------------------------------------------------------
def summarize_run(run):
    p = Path(run["result_path"])
    sha = sha256_file(p)
    top = load_top_level(p, {"design_id", "status", "noise_floor", "registered_results", "forecast_comparison", "stop_issues"})
    rr = top.get("registered_results") or {}
    prim = rr.get("primary") or {}
    ov = (prim.get("result") or {}).get("overall") or {}
    nf = top.get("noise_floor") or prim.get("noise_floor") or {}
    feas = nf.get("feasibility") or {}
    return {"result_path": rel(p), "result_sha256": sha, "sha_matches_registry": sha == run.get("result_sha256"),
            "result_status": top.get("status"), "att": ov.get("att"), "se": ov.get("se"),
            "noise_sd": nf.get("sample_sd"), "noise_cutoff": nf.get("absolute_cutoff"),
            "recorded_exceed": nf.get("strictly_exceeds_cutoff"), "ri_p": nf.get("p_two_sided"),
            "support_pass": feas.get("support_pass"), "feasible_draws": feas.get("feasible_draws"),
            "forecast_rows": (top.get("forecast_comparison") or {}).get("rows") or []}


def is_certified(run, summ):
    """EX-1 section 1 / amendment 20261015 5.2: a fitted, registered result."""
    return (run.get("status") in ("COMPLETED", "STOPPED") and run.get("study_estimated") is not False
            and finite(summ.get("att")))


def run_chains(reg):
    by = OrderedDict()
    for r in reg["runs"]:
        by.setdefault(r["design_id"], []).append(r)
    out = {}
    for did, runs in by.items():
        chain = []
        for r in runs:
            summ = summarize_run(r)
            chain.append({"run": r, "summ": summ, "certified": is_certified(r, summ)})
        first = next((c for c in chain if c["certified"]), None)
        entry = {"first": first, "all_runs": chain, "not_certified_before": [], "later_certified": []}
        if first is not None:
            pos = chain.index(first)
            entry["not_certified_before"] = [c for c in chain[:pos] if not c["certified"]]
            entry["later_certified"] = [c for c in chain[pos + 1:] if c["certified"]]
        out[did] = entry
    return out


def run_listing(c):
    r, s = c["run"], c["summ"]
    code = LATER_RUN_CODES.get(r["run_id"], ("UNCODED", None))
    return {"run_id": r["run_id"], "status": r["status"], "study_estimated": r.get("study_estimated"),
            "att": s["att"], "recorded_exceed": s["recorded_exceed"], "result_sha256": s["result_sha256"],
            "reason_code": code[0] if c["certified"] else "NOT_CERTIFIED", "reason_record": code[1] if c["certified"] else r.get("reason")}


# ---------------------------------------------------------------------------------------------
# forecast ledgers
# ---------------------------------------------------------------------------------------------
def read_lock_slots(pk, rows):
    """Five- or three-slot ledger of a registry package: lock verified = file hash and scored-row equality."""
    lock_path = Path(pk) / "PREDICTIONS_LOCK.json"
    if not lock_path.exists():
        return None
    lock = read_json(lock_path)
    personas = lock.get("personas") or []
    by_row = {x.get("persona"): x for x in rows}
    slots = []
    for persona in personas:
        relp = f"predictions/{persona}/submission.json"
        recorded = (lock.get("files") or {}).get(relp)
        fp = Path(pk) / relp
        hash_ok = bool(fp.exists() and recorded and sha256_file(fp) == recorded)
        sub = read_json(fp) if fp.exists() else {}
        row = by_row.get(persona) or {}
        matches = bool(row) and sub.get("point") == row.get("point") and list(sub.get("interval") or []) == list(row.get("interval") or [])
        interval = sub.get("interval")
        ok_vals = finite(sub.get("point")) and isinstance(interval, list) and len(interval) == 2 and all(finite(v) for v in interval)
        slots.append({"persona": persona, "center": sub.get("point"), "interval": interval, "interval_level": sub.get("interval_level"),
                      "hash_ok": hash_ok, "matches_scored_row": matches, "finite": bool(ok_vals),
                      "author_model": (sub.get("author_provenance") or {}).get("model_id")})
    # Independence record: the lock's own keys win; a lock with none may be completed by the additive sidecar INDEPENDENCE_CARRY.json (read-only, hash-checked).
    own = {k: lock[k] for k in INDEPENDENCE_KEYS + ("n_workers", "independence_source") if k in lock}
    sidecar, sidecar_problems = (None, []) if own else read_carry(pk)
    record = own or (sidecar or {})
    old_rule = (own.get("SINGLE_CONSENSUS") is False and own.get("n10_eligible") is True
                and isinstance(own.get("n_eff"), (int, float)) and own["n_eff"] > 1)   # the reader rule before the Q3 ruling (lock keys only)
    return {"lock_path": rel(lock_path), "lock_sha256": sha256_file(lock_path), "personas": personas,
            # a keyless three-persona lock (a rebound whose keys were lost) is a three-slot set too
            "three_slot_key": bool(lock.get("forecast_slots")) or len(personas) == 3,
            "independence": {k: record[k] for k in ("n_eff", "SINGLE_CONSENSUS", "n10_eligible") if k in record},
            "independence_from": "lock" if own else ("sidecar" if sidecar else None), "sidecar_problems": sidecar_problems,
            "n10_old_rule": bool(old_rule), "n10_three_slot_lock_only": n10_eligible_record(own), "n10_three_slot_eligible": n10_eligible_record(record),
            "slots": slots, "verified": bool(slots) and all(s["hash_ok"] and s["matches_scored_row"] and s["finite"] for s in slots)}


def find_ledger(chain_entry):
    """Ledger from the first certified run's package; fall back to any run's package (listed in `source`)."""
    first = chain_entry["first"]
    order = [first] + [c for c in chain_entry["all_runs"] if c is not first]
    for i, c in enumerate(order):
        led = read_lock_slots(c["run"]["package_path"], c["summ"]["forecast_rows"] if c["summ"]["forecast_rows"] else first["summ"]["forecast_rows"])
        if led:
            led["source"] = "first certified run package" if i == 0 else f"package of run {c['run']['run_id']}"
            return led
    return None


def receipt_times(pk, stage):
    d = Path(pk) / "receipts" / stage
    if not d.is_dir():
        return []
    out = []
    for f in sorted(os.listdir(d)):
        stem = f.split(".")[0]
        if stem.isdigit():
            out.append(int(stem) / 1e9)
    return out


def utc_from(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def n10_evidence(run, ledger):
    """s_pre and the lock-before-outcome evidence of a registry package (amendment 5.6)."""
    pk = Path(run["package_path"])
    out = {"s_pre": None, "s_pre_n": None, "export_ok": False, "lock_bound": False, "reasons": []}
    l3x = pk / "L3_EXPORT_DATA.json"
    if not l3x.exists():
        out["reasons"].append("no L3_EXPORT_DATA.json")
        return out
    out["l3_export_sha256"] = sha256_file(l3x)
    expected = (run.get("s7_exports") or {}).get("L3_EXPORT_DATA.json")
    out["l3_export_matches_registry"] = (expected == out["l3_export_sha256"]) if expected else None
    blk = load_top_level(l3x, {"l3_export_v2"}).get("l3_export_v2")
    if not blk:
        out["reasons"].append("no l3_export_v2 block (legacy export)")
        return out
    out["s_pre"] = blk.get("s_pre")
    out["s_pre_n"] = blk.get("s_pre_n")
    out["H"] = blk.get("H")
    out["export_ok"] = bool(blk.get("calibration_outcome_computation") is False and blk.get("outcome_compute_stage") == "S6")
    if not out["export_ok"]:
        out["reasons"].append("export does not state calibration_outcome_computation false and outcome_compute_stage S6")
    freeze = pk / "FREEZE_FINAL.json"
    listed = bool(freeze.exists() and ledger and ledger["lock_sha256"] in freeze.read_text())
    s5, s6 = receipt_times(pk, "S5"), receipt_times(pk, "S6")
    ordered = bool(s5 and s6 and max(s5) < min(s6))
    out.update({"lock_listed_in_final_freeze": listed, "s5_receipt_utc": utc_from(max(s5)) if s5 else None,
                "s6_receipt_utc": utc_from(min(s6)) if s6 else None, "s5_before_s6": ordered})
    out["lock_bound"] = bool(listed and ordered and out["export_ok"])
    if not listed:
        out["reasons"].append("lock hash not listed in FREEZE_FINAL.json")
    if not ordered:
        out["reasons"].append("S5 receipt not earlier than S6 receipt, or receipts missing")
    return out


def find_three_slot_sets(root=AUTOPILOT):
    """FORECAST_SLOTS.json files of the autopilot work folders (bounded walk, no code snapshots)."""
    skip = {"code_snapshots", "rehearsal", "panels", "predictions", "forecast_packs", "forecast_snapshots", "receipts", "MERGE_BACKUP",
            "reviewer", "agents"}
    found = []
    base_depth = len(Path(root).parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        dirnames[:] = [d for d in dirnames if d not in skip] if depth < 6 else []
        if "FORECAST_SLOTS.json" in filenames:
            p = Path(dirpath) / "FORECAST_SLOTS.json"
            v = read_json(p)
            slots = v.get("slots") or {}
            s3 = next((s for s in slots.values() if s.get("slot") == 3), {})
            found.append({"path": rel(p), "design_id": Path(dirpath).parent.name, "version": Path(dirpath).name,
                          "personas": v.get("personas"), "slot3_model_id": s3.get("model_id"), "slot3_executor": s3.get("executor"),
                          "sha256": sha256_file(p)})
    return sorted(found, key=lambda x: x["path"])


# ---------------------------------------------------------------------------------------------
# per-design records
# ---------------------------------------------------------------------------------------------
def build_records(live, v2, chains, stale):
    v2_designs = {d["design_id"]: d for d in v2["designs"]}
    v2_rows = {}
    for r in v2["rows"]:
        v2_rows.setdefault(r["design_id"], []).append(r)
    recs = []
    diffs = []
    for d in live["designs"]:
        base = {"design_id": d["design_id"], "domain": d["domain"], "policy_family": d.get("policy_family") or d["design_id"],
                "source_group": d["source_group"], "status": d["status"], "gate": d.get("gate"), "reportable": bool(d.get("reportable")),
                "live_point_estimate": d.get("point_estimate"), "stale_contract_card": d["design_id"] in stale}
        if d["status"] != "estimated":
            recs.append(base)
            continue
        did = d["design_id"]
        if d["source_group"] in ("prototype", "manual_batch"):
            rows = v2_rows[did]
            row = rows[0]
            nz = row.get("noise") or {}
            base.update({"group3": "archive", "run": {"kind": "archived result", "source": d["source"]["path"],
                                                      "source_sha256": d["source"]["sha256"]},
                         "b": row["b"], "noise_sd": nz.get("sample_sd"), "noise_cutoff": nz.get("absolute_cutoff"),
                         "support_pass": None, "recorded_exceed": row.get("noise_exceedance"),
                         "outcome_order": v2_designs[did]["outcome_order"], "later_runs": [], "not_certified_runs": []})
            sp = ROOT / d["source"]["path"]
            base["run"]["source_sha256_recomputed_ok"] = bool(sp.exists() and sha256_file(sp) == d["source"]["sha256"])
            slots = row.get("forecast_slots") or []
            base["ledger"] = None
            if len(slots) == FIVE:
                base["ledger"] = {"kind": "archive", "personas": [s["persona"] for s in slots], "three_slot_key": False,
                                  "verified": all(s.get("current_digest_matches") is True and finite(s.get("center"))
                                                  and isinstance(s.get("interval80"), list) and len(s["interval80"]) == 2 for s in slots),
                                  "slots": [{"persona": s["persona"], "center": s["center"], "interval": s["interval80"], "interval_level": 0.8,
                                             "p_positive": s.get("p_positive"), "exact_positive_event": bool(s.get("exact_positive_event")),
                                             "hash_ok": s.get("current_digest_matches") is True} for s in slots]}
            base["s_pre"] = None
            base["n10_evidence"] = None
        else:
            ch = chains[did]
            first = ch["first"]
            if first is None:
                base.update({"group3": "registry", "b": None, "run": None})
                recs.append(base)
                continue
            s = first["summ"]
            r = first["run"]
            autopilot = "/autopilot/" in str(r["package_path"])
            base.update({"group3": "autopilot" if autopilot else "registry",
                         "run": {"kind": "registry first certified run", "run_id": r["run_id"], "status": r["status"],
                                 "result_path": s["result_path"], "result_sha256": s["result_sha256"],
                                 "sha_matches_registry": s["sha_matches_registry"], "spec_version": r.get("spec_version")},
                         "b": s["att"], "noise_sd": s["noise_sd"], "noise_cutoff": s["noise_cutoff"], "support_pass": s["support_pass"],
                         "recorded_exceed": s["recorded_exceed"], "ri_p": s["ri_p"],
                         "later_runs": [run_listing(c) for c in ch["later_certified"]],
                         "not_certified_runs": [run_listing(c) for c in ch["not_certified_before"]]})
            base["reportable"] = r["status"] == "COMPLETED"
            if d.get("point_estimate") is None or abs(d["point_estimate"] - s["att"]) > 1e-12 * max(1.0, abs(s["att"])):
                diffs.append({"design_id": did, "live_point_estimate": d.get("point_estimate"), "first_run_att": s["att"],
                              "first_run": r["run_id"], "later_runs": base["later_runs"]})
            if did in v2_designs:
                base["outcome_order"] = v2_designs[did]["outcome_order"]
            else:
                card = read_json(Path(r["package_path"]) / "CARD.json")
                rel_ = (card.get("first_order") or {}).get("relation")
                base["outcome_order"] = "first_order" if rel_ in ("direct", "closest_proxy") else "unknown"
            led = find_ledger(ch)
            base["ledger"] = led
            ev = n10_evidence(r, led) if led else None
            base["n10_evidence"] = ev
            base["s_pre"] = ev["s_pre"] if ev else None
        recs.append(base)
    return recs, diffs


def has_noise_line(r):
    return (r.get("status") == "estimated" and finite(r.get("b")) and finite(r.get("noise_sd")) and r["noise_sd"] > 0
            and finite(r.get("noise_cutoff")) and r["noise_cutoff"] > 0 and r.get("support_pass") is not False)


# ---------------------------------------------------------------------------------------------
# questions
# ---------------------------------------------------------------------------------------------
def code_table(recs, eval_fn, field_missing_code):
    """Coverage codes over D: EVAL / field-missing code for estimated designs / STOP_PRE_RUN:<gate> for not-evaluable designs."""
    c = Counter()
    for r in recs:
        if r["status"] != "estimated":
            c[f"STOP_PRE_RUN:{r['gate']}"] += 1
        elif eval_fn(r):
            c["EVAL"] += 1
        else:
            c[field_missing_code(r)] += 1
    return dict(sorted(c.items()))


def question_1(recs, D):
    elig = [r for r in recs if has_noise_line(r)]
    for r in elig:
        r["exceed"] = abs(r["b"]) > r["noise_cutoff"]
        r["z"] = r["b"] / r["noise_sd"]
    n, k = len(elig), sum(1 for r in elig if r["exceed"])
    u = D - n
    fam = [r["policy_family"] for r in elig]
    boot = family_bootstrap_proportion([1.0 if r["exceed"] else 0.0 for r in elig], fam)
    zs = sorted(r["z"] for r in elig)
    q = np.percentile(zs, [25, 50, 75])
    mismatches = [{"design_id": r["design_id"], "recomputed": r["exceed"], "recorded": r["recorded_exceed"]}
                  for r in elig if r["recorded_exceed"] is not None and bool(r["recorded_exceed"]) != r["exceed"]]

    def sub(pred):
        """designs = designs of D in the subset (not-evaluable designs carry no group, so group splits count estimated designs only)."""
        e = [r for r in elig if pred(r)]
        allr = [r for r in recs if pred(r)]
        kk = sum(1 for r in e if r["exceed"])
        return {"designs": len(allr), "n": len(e), "k": kk, "rate_k_over_n": frac(kk, len(e)), "families": len({r["policy_family"] for r in e})}

    est = [r for r in recs if r["status"] == "estimated"]
    result = {
        "D": D, "n": n, "k": k, "u": u, "F": len(set(fam)), "rate_k_over_n": frac(k, n), "coverage_n_over_D": frac(n, D),
        "full_roster_bounds": [k / D, (k + u) / D], "expected_exceedances_under_5pct_reference": 0.05 * n,
        "family_bootstrap": {"B": B_BOOT, "seed": SEED_Q1, "blocks": "policy_family", "F_blocks": len(set(fam)),
                             "interval95": percentile_pair(boot, 2.5, 97.5), "interval99": percentile_pair(boot, 0.5, 99.5),
                             "note": "unadjusted; registry families treated as independent blocks; shared controls and calendar years not modelled"},
        "p_value": None, "p_value_note": "joint calibrated null replays not run (no observational reassignment model)",
        "latent_prevalence": "NOT_IDENTIFIED (not run)",
        "z": {"median": float(q[1]), "quartiles": [float(q[0]), float(q[2])], "min": zs[0], "max": zs[-1],
              "n_abs_gt_1_96": sum(1 for z in zs if abs(z) > 1.96)},
        "exceeding_designs": [r["design_id"] for r in elig if r["exceed"]],
        "recorded_flag_mismatches": mismatches,
        "coverage_codes": code_table(recs, has_noise_line, lambda r: "NO_NOISE_REF"),
        "no_noise_line_designs": [r["design_id"] for r in est if not has_noise_line(r)],
        "splits": {
            "archive_hand_picked": sub(lambda r: r.get("group3") == "archive"),
            "pipeline": sub(lambda r: r.get("group3") in ("registry", "autopilot")),
            "registry_terminal": sub(lambda r: r.get("group3") == "registry"),
            "autopilot_estimates": sub(lambda r: r.get("group3") == "autopilot"),
            "completed_runs_only": sub(lambda r: r.get("status") == "estimated" and r.get("reportable")),
            "by_domain": {dom: sub(lambda r, dom=dom: r["domain"] == dom) for dom in DOMAINS},
            "by_outcome_order": {o: sub(lambda r, o=o: r.get("outcome_order") == o) for o in ("first_order", "second_order", "unknown")},
        },
    }
    return result


def annotate_sets(sets_found, recs):
    """Add membership of D and live status to each registered three-slot set found on disk."""
    by_id = {r["design_id"]: r for r in recs}
    out = []
    for x in sets_found:
        r = by_id.get(x["design_id"])
        out.append(dict(x, in_D=r is not None, live_status=(r["status"] if r else None),
                        label="slot 3: %s / %s" % (x["slot3_model_id"], x["slot3_executor"])))
    return out


def three_slot_blank(label, recs, D, sets_outside_D):
    """Q3-3 / N10-3 at a cutoff with no eligible set (amendment 20261015 sections 3, 4)."""
    fm = Counter()
    for r in recs:
        fm["STOP_PRE_RUN:" + str(r["gate"]) if r["status"] != "estimated" else "FIELD_MISSING (no three-slot set)"] += 1
    return {"variant": label, "D": D, "n": 0, "F": 0, "coverage_codes": dict(sorted(fm.items())),
            "registered_three_slot_sets_found_under_autopilot": sets_outside_D,
            "by_slot3_label": {"gpt-6.1-sol": {"n": 0}, "claude-sonnet-5.5": {"n": 0}},
            "note": "No estimated design in D carries a three-slot set at this cutoff."}


def question_3(recs, D, sets_found):
    led_ok = [r for r in recs if r.get("status") == "estimated" and finite(r.get("b")) and r.get("ledger")
              and len(r["ledger"]["slots"]) == FIVE and not r["ledger"]["three_slot_key"] and r["ledger"]["verified"]]
    three = [r for r in recs if r.get("status") == "estimated" and r.get("ledger") and r["ledger"]["three_slot_key"]]
    unverified = [r["design_id"] for r in recs if r.get("status") == "estimated" and r.get("ledger") and not r["ledger"]["verified"]]
    scored = []
    for r in led_ok:
        sd = r["noise_sd"] if finite(r.get("noise_sd")) and r["noise_sd"] > 0 else None
        sc = design_scores(r["b"], r["ledger"]["slots"], sd)
        sp = r.get("s_pre") if finite(r.get("s_pre")) and r["s_pre"] > 0 else None
        sc_sp = design_scores(r["b"], r["ledger"]["slots"], sp) if sp else None
        scored.append(dict(sc, design_id=r["design_id"], b=r["b"], group3=r["group3"], stale=r["stale_contract_card"],
                           policy_family=r["policy_family"], overstatement_spre=sc_sp["overstatement"] if sc_sp else None))

    def block(pred):
        rows = [s for s in scored if pred(s)]
        out = summarize_scores(rows)
        sp_rows = [s["overstatement_spre"] for s in rows if s["overstatement_spre"] is not None]
        out["families"] = len({s["policy_family"] for s in rows})
        out["overstatement_spre"] = {"designs": len(sp_rows), "mean": statistics.mean(sp_rows) if sp_rows else None,
                                     "positive_designs": sum(1 for o in sp_rows if o > 0)}
        out["slots"] = FIVE * len(rows)
        return out

    # registered primary S and the available-slot version (archive ledgers with explicit P(b>0))
    full, avail = [], []
    for r in led_ok:
        ex = [s for s in r["ledger"]["slots"] if s.get("exact_positive_event") and finite(s.get("p_positive"))]
        if len(ex) == FIVE:
            full.append({"design_id": r["design_id"], "skill": brier_skill(ex, r["b"])})
        if ex:
            avail.append({"design_id": r["design_id"], "exact_slots": len(ex), "skill": brier_skill(ex, r["b"])})
    ok_ids = {r["design_id"] for r in led_ok}
    codes = code_table(recs, lambda r: r["design_id"] in ok_ids, lambda r: "FIELD_MISSING (no ledger)" if not r.get("ledger") else
                       ("FIELD_MISSING (three-slot set)" if r["ledger"]["three_slot_key"] else "FIELD_MISSING (lock not verified)"))
    out = {"D": D, "n_descriptive": len(led_ok), "F": len({r["policy_family"] for r in led_ok}),
           "primary_S_complete_five_P_gt0": {"n": len(full), "S": statistics.mean(x["skill"] for x in full) if full else None,
                                              "mean_brier": (0.25 - statistics.mean(x["skill"] for x in full)) if full else None,
                                              "designs": full},
           "available_slot_S": {"design_n": len(avail), "S": statistics.mean(x["skill"] for x in avail) if avail else None, "designs": avail},
           "tests": "none: the frozen score-resampling engine replays L2 estimators and is not run; no interval, no p-value",
           "scores": {"all": block(lambda s: True),
                      "without_stale_contract_sets": block(lambda s: not s["stale"]),
                      "archive_hand_batches": block(lambda s: s["group3"] == "archive"),
                      "registry_terminal": block(lambda s: s["group3"] == "registry"),
                      "autopilot": block(lambda s: s["group3"] == "autopilot"),
                      "stale_contract_sets_only": block(lambda s: s["stale"])},
           "stale_contract_designs": sorted(s["design_id"] for s in scored if s["stale"]),
           "designs_with_unverified_ledger": unverified,
           "coverage_codes": codes,
           "per_design": [{k: s[k] for k in ("design_id", "group3", "b", "median", "median_hit", "slot_hit_rate", "coverage80", "overstatement",
                                             "overstatement_spre", "zero_centers", "stale")} for s in scored],
           "three_slot_variant": three_slot_blank("Q3-3", recs, D, sets_found)}
    if three:
        out["three_slot_variant"]["WARNING"] = "estimated designs with a three-slot lock exist; the variant must be implemented before use: " + ", ".join(r["design_id"] for r in three)
    return out


def n10_three_slot_membership(recs):
    """Who is independent enough for N10-3, by lock keys and with the sidecar (Q3 ruling 3b/3c); N10-3 itself is not computed here.

    before = the reader rule before the ruling (stored flags only); after = ONE rule `forecast_slots.n10_eligible_record` (>= 3 personas, >= 2 distinct
    worker ids); with sidecar = rows whose lock lost its keys, completed by a hash-checked INDEPENDENCE_CARRY.json. Design ids only, no value.
    """
    est = [r for r in recs if r.get("status") == "estimated" and r.get("ledger") and r["ledger"].get("three_slot_key")]

    def names(key):
        return sorted(r["design_id"] for r in est if r["ledger"].get(key))
    sidecar = sorted(r["design_id"] for r in est if r["ledger"].get("independence_from") == "sidecar")
    return OrderedDict([
        ("estimated_three_slot_designs", len(est)),
        ("before_ruling_reader_rule", OrderedDict(n=len(names("n10_old_rule")), designs=names("n10_old_rule"))),
        ("after_ruling_lock_keys_only", OrderedDict(n=len(names("n10_three_slot_lock_only")), designs=names("n10_three_slot_lock_only"))),
        ("after_ruling_with_sidecar", OrderedDict(n=len(names("n10_three_slot_eligible")), designs=names("n10_three_slot_eligible"))),
        ("sidecar_designs", sidecar),
        ("sidecar_problems", {r["design_id"]: r["ledger"]["sidecar_problems"] for r in est if r["ledger"].get("sidecar_problems")}),
        ("rule", "not SINGLE_CONSENSUS, at least 3 personas, at least 2 distinct worker ids (HUB_RULING_20261023_PARK_REOPEN.md section 4, Q3 3b); "
                 "a missing worker id is ineligible; both counts printed, the rule rests on authorship facts"),
        ("note", "membership only; the N10-3 statistic is not implemented"),
    ])


def n10_rows(recs, require_five=True):
    """Eligible N10 designs (amendment 5.6) and the codes of the others."""
    elig, codes, why = [], {}, {}
    for r in recs:
        if r.get("status") != "estimated":
            codes[r["design_id"]] = "STOP_PRE_RUN:" + str(r["gate"])
            continue
        led, ev = r.get("ledger"), r.get("n10_evidence")
        if not led or len(led["slots"]) != FIVE or led["three_slot_key"]:
            codes[r["design_id"]] = "FIELD_MISSING (no five-slot ledger)"
        elif not led["verified"]:
            codes[r["design_id"]] = "FIELD_MISSING (lock not verified)"
        elif not ev or not finite(ev.get("s_pre")) or ev["s_pre"] <= 0:
            codes[r["design_id"]] = "FIELD_MISSING (no s_pre)"
        elif not ev["lock_bound"]:
            codes[r["design_id"]] = "FIELD_MISSING (lock not shown to precede outcome access)"
            why[r["design_id"]] = ev["reasons"]
        elif not (finite(r.get("b")) and has_noise_line(r)):
            codes[r["design_id"]] = "NO_NOISE_REF"
        else:
            elig.append(r)
            codes[r["design_id"]] = "EVAL"
    return elig, codes, why


def n10_stats(elig):
    rows = []
    for r in elig:
        m = [s["center"] for s in r["ledger"]["slots"]]
        sd = r["noise_sd"]
        rows.append({"design_id": r["design_id"], "policy_family": r["policy_family"], "stale": r["stale_contract_card"],
                     "disp": statistics.stdev(m) / sd, "err": abs(statistics.median(m) - r["b"]) / sd, "g": sd / r["s_pre"],
                     "median": statistics.median(m), "b": r["b"], "noise_sd": sd, "s_pre": r["s_pre"]})
    return rows


def n10_result(rows, seed):
    if len(rows) < 4:
        return {"n": len(rows), "F": len({r["policy_family"] for r in rows}), "r10": None, "note": "not computable: fewer than 4 designs"}
    disp = [r["disp"] for r in rows]
    err = [r["err"] for r in rows]
    g = [r["g"] for r in rows]
    fam = [r["policy_family"] for r in rows]
    r10 = partial_spearman(disp, err, g)
    star = e4_pairs_bootstrap(disp, err, g, fam, B_BOOT, seed)
    out = {"n": len(rows), "F": len(set(fam)), "r10": None if math.isnan(r10) else r10,
           "E4": dict(e4_summary(star, None if math.isnan(r10) else r10), seed=seed, blocks="policy_family")}
    return out


def question_10(recs, D):
    elig, codes, why = n10_rows(recs)
    rows = n10_stats(elig)
    nonstale = [r for r in rows if not r["stale"]]
    res_all = n10_result(rows, SEED_N10)
    res_ns = n10_result(nonstale, SEED_N10)
    return {"D": D, "n": len(rows), "F": len({r["policy_family"] for r in rows}), "all_eligible": res_all,
            "without_stale_contract_sets": res_ns,
            "coverage_codes": dict(sorted(Counter(codes.values()).items())),
            "ineligibility_reasons": why,
            "per_design": rows,
            "plain_note": "min N 40 and F 20 are printed for context and gate nothing (EX-1)"}


def question_5(recs, D):
    def dom_table(pred):
        out = {}
        for dom in DOMAINS:
            ds = [r for r in recs if r["domain"] == dom and pred(r)]
            est = [r for r in ds if r["status"] == "estimated"]
            rep = [r for r in est if r.get("reportable")]
            gates = Counter(r["gate"] for r in ds if r["status"] != "estimated")
            out[dom] = {"D": len(ds), "estimated": len(est), "estimated_rate": frac(len(est), len(ds)),
                        "completed_reportable": len(rep), "reportable_rate": frac(len(rep), len(ds)),
                        "not_evaluable_by_gate": {g: gates.get(g, 0) for g in GATES if gates.get(g, 0)}}
        ds = [r for r in recs if pred(r)]
        est = [r for r in ds if r["status"] == "estimated"]
        rep = [r for r in est if r.get("reportable")]
        gates = Counter(r["gate"] for r in ds if r["status"] != "estimated")
        out["ALL"] = {"D": len(ds), "estimated": len(est), "estimated_rate": frac(len(est), len(ds)),
                      "completed_reportable": len(rep), "reportable_rate": frac(len(rep), len(ds)),
                      "not_evaluable_by_gate": {g: gates.get(g, 0) for g in GATES if gates.get(g, 0)}}
        return out

    archive = ("prototype", "manual_batch")
    return {"D": D, "F": len({r["policy_family"] for r in recs}),
            "all_D": dom_table(lambda r: True),
            "archive_hand_picked": dom_table(lambda r: r["source_group"] in archive),
            "pipeline_processed": dom_table(lambda r: r["source_group"] not in archive),
            "autopilot_loop": dom_table(lambda r: r["source_group"] in ("autopilot_estimate", "autopilot_park")),
            "designs_by_source_group": dict(Counter(r["source_group"] for r in recs)),
            "not_applied": ["age60 closure (no intake date in the live set)", "enrolment waves (no freeze time)",
                            "wave-adjusted wild-score omnibus test", "outcome-domain strata (L3-D not built)"],
            "tests": "none run; no interval, no p-value"}


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    live = read_json(LIVE)
    v2 = read_json(V2)
    reg = read_json(REG)
    annot = read_json(ANNOT)
    stale = {s["design_id"] for s in annot["sets"] if s.get("stale_contract_card")}
    D = len(live["designs"])
    outside = {k: len(v) for k, v in live["excluded_not_counted"].items()}
    chains = run_chains(reg)
    recs, diffs = build_records(live, v2, chains, stale)
    sets_found = annotate_sets(find_three_slot_sets(), recs)

    q1 = question_1(recs, D)
    q3 = question_3(recs, D, sets_found)
    dg = (v2["questions"]["Q3"].get("diagnostic_event_score") or {}).get("descriptive_defined_archive_subset") or {}
    briers = [x["design_mean_brier"] for x in dg.get("records", [])]
    q3["diagnostic_event_brier_carried_from_v2"] = {
        "designs": len(briers), "mean_brier": statistics.mean(briers) if briers else None,
        "note": "archive batch 2-3 designs only; unchanged by V3 (pipeline ledgers carry no diagnostic-pass probability); not recomputed"}
    v2q3 = v2["questions"]["Q3"]
    regression = {"primary_S_v2": v2q3["complete_five_P_gt0"]["skill_S"], "primary_S_v3": q3["primary_S_complete_five_P_gt0"]["S"],
                  "available_S_v2": v2q3["available_slot_P_gt0"]["skill_S"], "available_S_v3": q3["available_slot_S"]["S"]}
    regression["agree"] = (regression["primary_S_v3"] is not None and abs(regression["primary_S_v2"] - regression["primary_S_v3"]) < 1e-9
                           and abs(regression["available_S_v2"] - regression["available_S_v3"]) < 1e-9)
    q5 = question_5(recs, D)
    q10 = question_10(recs, D)
    q10_3 = three_slot_blank("N10-3", recs, D, sets_found)
    q10_3["independence_membership"] = n10_three_slot_membership(recs)

    me = Path(__file__).resolve()
    inputs = {}
    for p in (LIVE, V2, REG, ANNOT, AMEND, AMEND_HASH, RULING, HERE / "build_live_set.py"):
        inputs[rel(p)] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
    first_run_checks = {
        "live_vs_first_run_differences": diffs,
        "registry_sha_mismatches": [r["design_id"] for r in recs if r.get("run") and r["run"].get("sha_matches_registry") is False],
        "archive_source_hash_mismatches": [r["design_id"] for r in recs if r.get("run") and r["run"].get("source_sha256_recomputed_ok") is False],
        "designs_with_later_certified_runs": {r["design_id"]: r["later_runs"] for r in recs if r.get("later_runs")},
        "designs_with_not_certified_earlier_runs": {r["design_id"]: r["not_certified_runs"] for r in recs if r.get("not_certified_runs")},
        "q3_archive_regression_vs_v2": regression,
        "registry_designs_with_first_certified_run": sum(1 for r in recs if r.get("group3") in ("registry", "autopilot")),
    }
    stamp = {"rule_id": RULE_ID, "version": VERSION, "cutoff_utc": live["as_of"], "run_utc": utc_now(),
             "live_set_sha256": inputs[rel(LIVE)]["sha256"], "code_sha256": sha256_file(me), "code": rel(me),
             "amendment_sha256": inputs[rel(AMEND)]["sha256"],
             "D": D, "estimated": live["counts"]["estimated"], "not_evaluable": live["counts"]["not_evaluable"],
             "outside_D": outside, "python": sys.version.split()[0], "numpy": np.__version__,
             "scipy": __import__("scipy").__version__,
             "run_rule": "first certified run (EX-1 section 1)", "no_significance_label": True}
    payload = clean(OrderedDict([
        ("schema_version", "l3-meta-ex1-v3"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", inputs), ("first_run_checks", first_run_checks),
        ("questions", OrderedDict([("Q1", q1), ("Q3", q3), ("Q5", q5), ("N10", q10), ("N10-3", q10_3)])),
        ("designs", [{k: v for k, v in r.items() if k not in ("ledger", "n10_evidence")} | {
            "ledger": ({"source": r["ledger"].get("source", r["ledger"].get("kind")), "lock_sha256": r["ledger"].get("lock_sha256"),
                        "verified": r["ledger"]["verified"], "three_slot_key": r["ledger"]["three_slot_key"],
                        "slots": [{k2: v2_ for k2, v2_ in sl.items() if k2 in ("persona", "center", "interval", "interval_level", "p_positive",
                                                                        "exact_positive_event", "hash_ok", "matches_scored_row")}
                                  for sl in r["ledger"]["slots"]]} if r.get("ledger") else None),
            "n10_evidence": r.get("n10_evidence")} for r in recs]),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False}),
    ]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():  # a version folder is written once; a new live set or new code makes a new folder
            sys.exit(f"{rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(LIVE, copy)
        assert sha256_file(copy) == stamp["live_set_sha256"]
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:  # append-only record of every run
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_ex1.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code_sha256"], "live_set_sha256": stamp["live_set_sha256"],
                                 "amendment_sha256": stamp["amendment_sha256"], "results_file": rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": stamp, "first_run_checks": {k: (v if k != "designs_with_later_certified_runs" else list(v)) for k, v in first_run_checks.items()},
             "Q1": {k: v for k, v in q1.items() if k not in ("splits",)}, "Q1_splits": q1["splits"],
             "Q3": {k: v for k, v in q3.items() if k != "per_design"}, "Q5": q5,
             "N10": {k: v for k, v in q10.items()}, "N10-3": q10_3}
    print(json.dumps(clean(brief), ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
