#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version D1-V1 (rule id EX-1): D1 "Across state policies, how many different kinds of outcome does one policy move
beyond what random timing produces?"

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_D_QUESTIONS.md (entry D1; hash PREREG_AMEND_20261015_D_HASH.txt) and the dated build amendments
PREREG_AMEND_20261015_SWEEP_V1.md (section 6) and PREREG_AMEND_20261015_SWEEP_V1B.md (hash records PREREG_AMEND_20261015_SWEEP_HASH*.txt), all hashed
before any sweep number was computed; EX-1 (PREREG_AMEND_20261012_EXPLORATORY.md); engine E1 of the addendum 0.5 (scripts/l3_meta/build_n1.py).

Input: the sweep of SWEEP-V1 (data/l3_meta/sweep_v1/<design>.npz: for every swept design and series the ATT and the 999 placebo ATTs of the same label
reassignments).  Per design: family statistic M_if = max |z_ik| over the supported series of family f against the same maximum over the placebo draws,
the line q_if, the number N_i of families beyond the line against its placebo distribution, Z_i = (N_i - mu_i)/tau_i.  theta_D1 = mean Z_i with E1
(family-block bootstrap-t over policy families) only with at least 8 designs in 5 policy families; below that the design-by-family table is the result.
Every statistic is exploratory: no "significant" label, no multiplicity adjustment, no licensed sentence.

Reads L2 results only through the sweep files; nothing under scripts/l2_pipeline or data/l2_pipeline is opened.  No network.  Only output: data/l3_meta/d1_v1/.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_d1.py [--dry-run] [--out-dir DIR] [--sweep-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_n1 as N1  # noqa: E402  (E1 engine only)
import build_d3 as D3  # noqa: E402  (hash record of the D questions)
import build_sweep as B  # noqa: E402  (hash record and file helpers)
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "D1", "D1-V1"
SEED_E1 = [20261011, 31]
B_BOOT = 9999
FLOOR_N, FLOOR_F = 8, 5
PLAN_N, PLAN_F = 20, 20
MIN_FAMILIES = 5
MIN_COMMON = S.MIN_COMMON_DRAWS
LIVE_FIELDS = ("design_id", "status", "gate", "domain", "policy_family", "source_group")
ARCHIVE_GROUPS = ("prototype", "manual_batch")
OUT_DIR = ROOT / "data/l3_meta/d1_v1"
OUT_NAME = "RESULTS_D1_V1.json"
D_QUESTIONS = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D_QUESTIONS.md"
D_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D_HASH.txt"


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# the statistics of one design (amendment SWEEP_V1 section 6; pure function of arrays)
# ---------------------------------------------------------------------------------------------
def design_statistics(sids, fam, code, att, sd_own, placebo, registered_families=()):
    """sids, fam, code: sequences over series; att, sd_own: float arrays; placebo: (K, 999) with NaN at infeasible draws or unsupported series.

    Returns a dict with code ('EVAL' or 'SUPPORT') and, when EVAL, all statistics of section 6.3 and the descriptive items of 6.6."""
    sids, fam, code = list(sids), list(fam), list(code)
    att, sd_own, placebo = np.asarray(att, float), np.asarray(sd_own, float), np.asarray(placebo, float)
    K = len(sids)
    removed = OrderedDict()
    A = []
    for i in range(K):
        if code[i] != "SUPPORTED":
            continue
        if not (np.isfinite(sd_own[i]) and sd_own[i] > 0):
            removed[sids[i]] = "SD_ZERO"
            continue
        A.append(i)
    feas = np.isfinite(placebo)
    n_requested = placebo.shape[1]

    def common(idx):
        return feas[idx].all(axis=0) if idx else np.zeros(n_requested, dtype=bool)

    C = common(A)
    while A and int(C.sum()) < MIN_COMMON and len({fam[i] for i in A}) >= MIN_FAMILIES:
        fewest = min(int(feas[i].sum()) for i in A)
        drop = max((i for i in A if int(feas[i].sum()) == fewest), key=lambda i: sids[i])
        A.remove(drop)
        removed[sids[drop]] = "DRAWS_COMMON"
        C = common(A)
    out = dict(n_supported_series_before_common=int(sum(1 for c in code if c == "SUPPORTED")), removed_series=dict(removed), n_draws=int(C.sum()))
    families = sorted({fam[i] for i in A})
    out["families"] = families
    if len(families) < MIN_FAMILIES:
        return dict(out, code="SUPPORT", reason=f"FAMILIES_LT_{MIN_FAMILIES}: {len(families)} supported families")
    n = int(C.sum())
    if n < MIN_COMMON:
        return dict(out, code="SUPPORT", reason=f"COMMON_DRAWS: {n} draws feasible for every supported series")
    PC = placebo[np.ix_(A, np.where(C)[0])]
    sd = PC.std(axis=1, ddof=1)
    if not np.all(np.isfinite(sd)) or np.any(sd <= 0):
        return dict(out, code="SUPPORT", reason="SD_ZERO_COMMON")
    z = att[A] / sd
    zstar = PC / sd[:, None]
    rank = math.ceil(0.95 * n) - 1
    fam_stats = OrderedDict()
    nstar = np.zeros(n)
    nstar_off = np.zeros(n)
    N = 0
    N_off = 0
    reg = set(registered_families)
    Mmat = {}
    for f in families:
        idx = [j for j, i in enumerate(A) if fam[i] == f]
        M = float(np.max(np.abs(z[idx])))
        Mstar = np.max(np.abs(zstar[idx]), axis=0)
        q = float(np.sort(Mstar)[rank])
        beyond = bool(M > q)
        arg = int(np.argmax(np.abs(z[idx])))
        fam_stats[f] = dict(M=M, q=q, beyond=beyond, n_series=len(idx), top_series=sids[A[idx[arg]]], top_z=float(z[idx[arg]]),
                            share_draws_beyond=float(np.mean(Mstar > q)))
        flags = Mstar > q
        nstar += flags
        N += int(beyond)
        if f not in reg:
            nstar_off += flags
            N_off += int(beyond)
    mu, tau = float(nstar.mean()), float(nstar.std(ddof=1))
    out.update(family_stats=fam_stats, N=int(N), mu=mu, tau=tau, N_off=int(N_off), mu_off=float(nstar_off.mean()),
               families_registered=sorted(f for f in families if f in reg), n_series=len(A), series_used=[sids[i] for i in A])
    if not tau > 0:
        return dict(out, code="SUPPORT", reason="tau = 0")
    out["Z"] = (N - mu) / tau
    out["p"] = (1 + int(np.sum(nstar >= N))) / (n + 1)
    out["P_Nstar_ge"] = {str(k): float(np.mean(nstar >= k)) for k in (1, 2, 3)}
    # self-calibration of the series: share of placebo draws beyond the series' own line (5% by construction) and observed series beyond
    own_cut = np.sort(np.abs(PC), axis=1)[:, rank]
    beyond_series = np.abs(att[A]) > own_cut
    out["series_beyond_line"] = int(beyond_series.sum())
    out["series_beyond_expected"] = 0.05 * len(A)
    out["series_beyond_ids"] = [sids[A[j]] for j in np.where(beyond_series)[0]]
    out["placebo_share_beyond_own_line_mean"] = float(np.mean(np.abs(PC) > own_cut[:, None]))
    out["code"] = "EVAL"
    return out


# ---------------------------------------------------------------------------------------------
# registered families, roster, coverage
# ---------------------------------------------------------------------------------------------
def registered_families(rows, table):
    """design_id -> set of outcome families of its registered primary and secondary outcomes (inventory usage rows, else the catalog family)."""
    out = {}
    for r in rows:
        if r["row_type"] != "usage" or r["unit_kind"] != "design" or r["role"] not in ("primary", "secondary"):
            continue
        fam = r["family"] or (table.get(r["outcome_key"]) or {}).get("family")
        if fam:
            out.setdefault(r["unit_id"], set()).add(fam)
    return out


def load_live(path=S.LIVE):
    live = S.read_json(path)
    designs = []
    for d in live["designs"]:
        row = {k: d.get(k) for k in LIVE_FIELDS}
        designs.append(row)
    meta = {"as_of": live["as_of"], "counts": {k: live["counts"][k] for k in ("processed", "estimated", "not_evaluable")},
            "outside_D": {k: len(v) for k, v in live["excluded_not_counted"].items()}}
    return designs, meta


def load_design_file(path):
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["meta_json"]))
    return dict(sids=[str(x) for x in z["series_ids"]], fam=[str(x) for x in z["family"]], code=[str(x) for x in z["code"]], att=z["att"], sd=z["sd"],
                placebo=z["placebo"], meta=meta, z=z)


def e1_summary(rows):
    n = len(rows)
    fams = [r["policy_family"] for r in rows]
    F = len(set(fams))
    base = {"n": n, "F": F, "engine_floor": {"n": FLOOR_N, "F": FLOOR_F}, "seed": SEED_E1, "B": B_BOOT, "blocks": "policy_family"}
    if n < FLOOR_N or F < FLOOR_F:
        return dict(base, run=False, reason=f"engine floor not met (n = {n} < {FLOOR_N} or F = {F} < {FLOOR_F}); design-by-family table only; no interval, no p-value, no theta_D1")
    ids, F = N1.family_ids(fams)
    res = N1.e1_core([r["Z"] for r in rows], ids, F, B_BOOT, np.random.default_rng(SEED_E1))
    return dict(base, run=True, result=res)


def pattern_counts(rows):
    n = len(rows)
    p05 = sum(1 for r in rows if r["p"] <= 0.05)
    excess = sorted(((r["N"] - r["mu"]), r["design_id"]) for r in rows)[::-1]
    total_excess = sum(e for e, _ in excess)
    top5 = sum(e for e, _ in excess[:5])
    return dict(designs_p_le_05=p05, designs_p_le_05_expected=0.05 * n, designs_N_ge_2=sum(1 for r in rows if r["N"] >= 2),
                designs_N_ge_2_expected=sum(r["P_Nstar_ge"]["2"] for r in rows),
                designs_N_ge={k: sum(1 for r in rows if r["N"] >= k) for k in (1, 2, 3)},
                designs_N_ge_expected={k: sum(r["P_Nstar_ge"][str(k)] for r in rows) for k in (1, 2, 3)},
                excess_total=total_excess, excess_top5=top5, excess_top5_share=(top5 / total_excess) if total_excess > 0 else None,
                top5_designs=[d for _, d in excess[:5]])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--sweep-dir", default=str(B.OUT), help="folder with the sweep files (tests only)")
    args = ap.parse_args(argv)
    out_dir, sweep_dir = Path(args.out_dir), Path(args.sweep_dir)

    problems = B.verify_records() + D3.verify_records()          # sweep amendments and records; question file, D3-V1 build file and the entries D1 to D3
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    entry_d1 = re.search(r"^\s+D1\s+([0-9a-f]{64})", D_HASH.read_text(encoding="utf-8"), re.M).group(1)
    live_sha = S.sha256_file(S.LIVE)
    rec = B.parse_hash_record()
    if rec["listed"].get(S.rel(S.LIVE)) != live_sha:
        sys.exit("live set differs from the sweep hash record: not run")

    gate = S.read_json(sweep_dir / "GATE_REPORT.json")
    if gate["live_set_sha256"] != live_sha:
        sys.exit("the sweep was made with another live set")
    roster, live_meta = load_live()
    inv_rows = S.inventory_rows()
    table = S.catalog_series_table()
    reg_fams = registered_families(inv_rows, table)
    gate_by = {d["design_id"]: d for d in gate["designs"]}
    sweep_run = S.read_json(sweep_dir / "SWEEP_RUN.json")
    swept = {d["design_id"]: d for d in sweep_run["designs"] if d["status"] == "OK"}

    designs, eval_rows = [], []
    for d in roster:
        did = d["design_id"]
        row = dict(design_id=did, policy_family=d["policy_family"] or did, domain=d["domain"], live_status=d["status"], gate=d["gate"], source_group=d["source_group"])
        if d["status"] != "estimated":
            row.update(code="STOP_PRE_RUN", reason=f"STOP_PRE_RUN: {d['gate']}")
        elif d["source_group"] in ARCHIVE_GROUPS:
            row.update(code="FIELD_MISSING", reason="FIELD_MISSING: archive result carries no frozen treatment table in registered format")
        elif did not in gate_by:
            row.update(code="OTHER", reason="not in the gate report")
        elif gate_by[did]["status"] != "PASS":
            row.update(code="REPRO_FAIL", reason="; ".join(gate_by[did].get("reasons") or ["gate"]))
        elif gate_by[did].get("adapter") == "annual_full_year_v1":
            row.update(code="NO_SECONDARY_ADAPTER", reason="annual full-year adapter: L2 stage S3 has no certified secondary adapter")
        elif did not in swept:
            row.update(code="OTHER", reason="swept file missing or errored")
        else:
            f = load_design_file(sweep_dir / Path(swept[did]["file"]).name)
            st = design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], reg_fams.get(did, set()))
            row.update(st)
            row["scale_mode"] = f["meta"]["scale_mode"]
            row["n_series_total"] = len(f["sids"])
            row["series_codes"] = dict(Counter(f["code"]))
            row["sweep_file"] = swept[did]["file"]
            row["sweep_file_sha256"] = S.sha256_file(sweep_dir / Path(swept[did]["file"]).name)
            row["registered_families"] = sorted(reg_fams.get(did, set()))
            if st["code"] == "EVAL":
                eval_rows.append(row)
        designs.append(row)

    D_n = len(roster)
    n = len(eval_rows)
    F = len({r["policy_family"] for r in eval_rows})
    e1 = e1_summary(eval_rows)
    me = Path(__file__).resolve()
    stamp = {"rule_id": RULE_ID, "question": QUESTION, "version": VERSION, "cutoff_utc": live_meta["as_of"], "run_utc": utc_now(), "live_set_sha256": live_sha,
             "sweep_run_sha256": S.sha256_file(sweep_dir / "SWEEP_RUN.json"), "gate_report_sha256": S.sha256_file(sweep_dir / "GATE_REPORT.json"),
             "sweep_manifest_sha256": S.sha256_file(sweep_dir / "SWEEP_MANIFEST.json") if (sweep_dir / "SWEEP_MANIFEST.json").exists() else None,
             "sweep_run_code": sweep_run.get("code"),
             "code_sha256": S.sha256_file(me), "code": S.rel(me), "e1_engine_code_sha256": S.sha256_file(HERE / "build_n1.py"),
             "sweep_code": B.code_hashes(), "amendment_sha256": B.amend_hashes(), "question_file_sha256": S.sha256_file(D_QUESTIONS),
             "entry_D1_sha256": entry_d1,
             "D": D_n, "estimated": live_meta["counts"]["estimated"], "not_evaluable": live_meta["counts"]["not_evaluable"], "outside_D": live_meta["outside_D"],
             "n": n, "F": F, "python": sys.version.split()[0], "numpy": np.__version__, "seeds": {"E1": SEED_E1}, "B": B_BOOT, "no_significance_label": True}
    result = OrderedDict([
        ("D", D_n), ("n_designs_eval", n), ("F", F), ("coverage_codes_over_D", dict(sorted(Counter(r["code"] for r in designs).items()))),
        ("minimums_printed_beside_n", {"engine_floor": {"n": FLOOR_N, "F": FLOOR_F, "n_ge": n >= FLOOR_N, "F_ge": F >= FLOOR_F},
                                       "planning_minimum": {"n": PLAN_N, "F": PLAN_F, "n_ge": n >= PLAN_N, "F_ge": F >= PLAN_F},
                                       "planning_detectable_mean_Z_2.8_over_sqrt_n": 2.8 / math.sqrt(n) if n else None,
                                       "note": "EX-1: printed beside n, gate nothing (the engine floor only decides whether E1 runs)"}),
        ("E1", e1), ("theta_D1_plain_mean_Z", float(np.mean([r["Z"] for r in eval_rows])) if n else None),
        ("Z_distribution", {"mean": float(np.mean([r["Z"] for r in eval_rows])), "sd": float(np.std([r["Z"] for r in eval_rows], ddof=1)) if n > 1 else None,
                            "median": float(np.median([r["Z"] for r in eval_rows])), "min": float(min(r["Z"] for r in eval_rows)), "max": float(max(r["Z"] for r in eval_rows)),
                            "share_positive": float(np.mean([r["Z"] > 0 for r in eval_rows]))} if n else None),
        ("pattern_counts", pattern_counts(eval_rows) if n else None),
        ("series_calibration", {"series_beyond_line": int(sum(r["series_beyond_line"] for r in eval_rows)), "series_beyond_expected": float(sum(r["series_beyond_expected"] for r in eval_rows)),
                                "series_total": int(sum(r["n_series"] for r in eval_rows)),
                                "families_beyond_line": int(sum(r["N"] for r in eval_rows)), "families_beyond_expected": float(sum(r["mu"] for r in eval_rows))} if n else None),
    ])
    payload = S.clean(OrderedDict([
        ("schema_version", "l3-meta-d1-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {S.rel(p): {"sha256": S.sha256_file(p), "bytes": p.stat().st_size} for p in (S.LIVE, D_QUESTIONS, S.INVENTORY_CSV, sweep_dir / "GATE_REPORT.json", sweep_dir / "SWEEP_RUN.json")}),
        ("question", OrderedDict([("D1", result)])), ("designs", designs),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():
            sys.exit(f"{S.rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(S.LIVE, copy)
        assert S.sha256_file(copy) == live_sha
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_d1.py " + " ".join(argv if argv is not None else sys.argv[1:]), "code_sha256": stamp["code_sha256"],
                                 "live_set_sha256": live_sha, "sweep_run_sha256": stamp["sweep_run_sha256"], "results_file": S.rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": stamp, "D1": payload["question"]["D1"], "designs_eval": [{k: r.get(k) for k in ("design_id", "policy_family", "N", "mu", "tau", "Z", "p", "N_off", "n_series", "n_draws", "families")} for r in payload["designs"] if r.get("code") == "EVAL"],
             "designs_other": [{k: r.get(k) for k in ("design_id", "code", "reason")} for r in payload["designs"] if r.get("code") not in ("EVAL", "STOP_PRE_RUN", "FIELD_MISSING")]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {S.rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
