#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version D3-V2 (rule id EX-1): D3 "When a policy lowers its target outcome, does a close substitute rise?", with the firearm pairs
whose total series needed a new estimate in D3-V1.

Rules: PREREG_AMEND_20261015_D_QUESTIONS.md (entry D3), PREREG_AMEND_20261015_D3_V1.md (pair table and arithmetic, unchanged), PREREG_AMEND_20261015_SWEEP_V1.md
section 7 and PREREG_AMEND_20261015_SWEEP_V1B.md (the total estimated on the target's own mask and scale), all hashed before any sweep or D3-V2 number
existed; EX-1; engine E1 of the addendum 0.5.

Input: data/l3_meta/sweep_v1/D3_RUNS.json (stage d3runs: for every candidate pair the total's ATT, cells and placebo ATTs on the target's mask, estimated with
the operators of the target's own registered entry) and the L2 results of the targets.  The arithmetic is build_d3.py of D3-V1 (pair_numbers, analyze_pair,
E1), applied to an entry for the total made from the operator estimates; the cross-checks of D3-V1 (overall ATT against the cells, stored statistics against
their recomputation, cell sets, settings, draws, denominators, nesting) are the same.

READ-ONLY on every L2 file.  No network.  Only output: data/l3_meta/d3_v2/.  Every statistic is exploratory: no "significant" label, no multiplicity
adjustment, no licensed sentence.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_d3_v2.py [--dry-run] [--out-dir DIR] [--sweep-dir DIR]
"""
from __future__ import annotations

import argparse
import copy
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
import build_d3 as D3  # noqa: E402
import build_sweep as B  # noqa: E402
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "D3", "D3-V2"
OUT_DIR = ROOT / "data/l3_meta/d3_v2"
OUT_NAME = "RESULTS_D3_V2.json"
D3V1_RESULTS = ROOT / "data/l3_meta/d3_v1/RESULTS_D3_V1.json"
GATE_ORDER_V2 = ["REPRO_FAIL", "NO_TOTAL_SERIES", "CELL_SETS_DIFFER", "DRAWS_NOT_ALIGNED", "SETTINGS_DIFFER", "DENOMINATORS_DIFFER", "NOT_NESTED", "OTHER", "SUPPORT",
                 "NEEDS_COMPLEMENT_ESTIMATE"]
PAIR_ORDER_V2 = ["NO_TOTAL_SERIES", "REPRO_FAIL", "CELL_SETS_DIFFER", "SETTINGS_DIFFER", "DRAWS_NOT_ALIGNED", "DENOMINATORS_DIFFER", "NOT_NESTED", "OTHER", "SUPPORT"]


def utc_now():
    return B.utc_now()


def total_entry(A, rec):
    """An entry in RESULT.json shape for the total, from the operator estimate on the target's cells (cells, overall ATT, placebo ATTs, statistics)."""
    T = copy.deepcopy(A)
    gt = T["result"]["group_time"]
    if len(gt) != len(rec["t_cell_atts"]):
        raise ValueError("cell count of the total differs from the target's")
    for c, att in zip(gt, rec["t_cell_atts"]):
        c["att"] = att
    T["result"]["overall"] = dict(T["result"]["overall"], att=rec["t"])
    st = D3.draw_stats(rec["t"], rec["dt"])
    nf = dict(T["noise_floor"])
    nf.update(permutation_draws=list(rec["dt"]), sample_sd=st["sd"], absolute_cutoff=st["cutoff"], p_two_sided=st["p2"], strictly_exceeds_cutoff=st["beyond"])
    T["noise_floor"] = nf
    return T


def second_code(codes):
    for c in PAIR_ORDER_V2:
        if c in codes:
            return c
    return "OTHER"


def design_code(pair_codes):
    if not pair_codes:
        return "NO_PAIR"
    if "EVAL" in pair_codes:
        return "EVAL"
    for c in GATE_ORDER_V2:
        if c in pair_codes:
            return c
    return "OTHER"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--sweep-dir", default=str(B.OUT))
    args = ap.parse_args(argv)
    out_dir, sweep_dir = Path(args.out_dir), Path(args.sweep_dir)

    problems = B.verify_records() + D3.verify_records()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    live_sha = S.sha256_file(S.LIVE)
    rec_all = B.parse_hash_record()
    if rec_all["listed"].get(S.rel(S.LIVE)) != live_sha:
        sys.exit("live set differs from the sweep hash record: not run")
    entry_d3 = re.search(r"^\s+D3\s+([0-9a-f]{64})", D3.HASH_REC.read_text(encoding="utf-8"), re.M).group(1)
    gate = S.read_json(sweep_dir / "GATE_REPORT.json")
    runs = S.read_json(sweep_dir / "D3_RUNS.json")
    if gate["live_set_sha256"] != live_sha:
        sys.exit("the sweep was made with another live set")
    gate_by = {d["design_id"]: d for d in gate["designs"]}
    runs_by = {r["design_id"]: r for r in runs["designs"]}
    store = S.SeriesStore(sweep_dir / B.STORE_NAME, sweep_dir / B.COUNT_NAME)
    catalog = D3.load_catalog()
    roster, live_meta = D3.load_live()
    live_rows = {d["design_id"]: d for d in S.read_json(S.LIVE)["designs"]}
    reg_rows = {r[0]: r for r in B.roster()}

    rows, pairs = [], []
    for design in roster:
        did = design["design_id"]
        row = {"design_id": did, "family": design["policy_family"] or did, "domain": design["domain"], "live_status": design["status"], "gate": design["gate"],
               "source_group": design["source_group"], "pairs": [], "reasons": []}
        if design["status"] != "estimated":
            row["code"], row["reasons"] = "STOP_PRE_RUN", [f"STOP_PRE_RUN: {design['gate']}"]
            rows.append(row)
            continue
        if design["source_group"] in D3.ARCHIVE_GROUPS:
            row["code"], row["reasons"] = "FIELD_MISSING", ["FIELD_MISSING: archive result carries no registered results with placebo draws"]
            rows.append(row)
            continue
        if did not in reg_rows:
            row["code"], row["reasons"] = "OTHER", ["OTHER: source group"]
            rows.append(row)
            continue
        _, folder, result_path, _ = reg_rows[did]
        stored = S.read_json(result_path)
        primary_ids = [p.get("outcome_id") for p in (live_rows[did].get("primary_outcomes") or [])]
        outs = D3.outcomes_of(stored, primary_ids)
        pair_recs = []
        for pid, a_id, t_id, label in D3.PAIR_TABLE:
            if a_id not in outs:
                continue
            a_name, A = outs[a_id]
            prec = {"design_id": did, "policy_family": design["policy_family"] or did, "pair": pid, "target_series": a_id, "total_series": t_id, "substitute": label,
                    "target_entry": a_name, "reasons": [], "code": None}
            g = gate_by.get(did, {})
            run = (runs_by.get(did) or {})
            run_pair = next((p for p in run.get("pairs", []) if p["pair"] == pid), None)
            if g.get("status") != "PASS":
                prec.update(code="REPRO_FAIL", reasons=["REPRO_FAIL: " + "; ".join(g.get("reasons") or ["the design did not pass the reproduction gate"])])
            elif run_pair is None:
                prec.update(code="OTHER", reasons=["OTHER: no D3 run record"])
            elif run_pair.get("code") in ("NO_TOTAL_SERIES", "CELL_SETS_DIFFER", "OTHER"):
                prec.update(code=run_pair["code"], reasons=run_pair.get("reasons") or [run_pair["code"]], run=dict(n_mask_cells=run_pair.get("n_mask_cells"),
                            n_total_missing_cells=run_pair.get("n_total_missing_cells")))
            else:
                prec["scale_mode"] = run["scale_mode"]
                T = total_entry(A, run_pair)
                keys = [tuple(k) for k in run_pair["keys"]]
                view = store.view(run["scale_mode"])
                vt = view.values(t_id, keys, "val")
                dn = view.values(t_id, keys, "den")
                pt = {k: (float(v), (None if not math.isfinite(d) else float(d))) for k, v, d in zip(keys, vt, dn)}
                panel_a_path = D3.panel_path(folder, a_name, a_id)
                def panels(an, asid, tn, tsid, _pa=panel_a_path, _pt=pt):
                    return D3.read_panel(_pa), _pt
                one = D3.analyze_pair(dict(design_id=did, policy_family=design["policy_family"]), (pid, a_id, t_id, label), a_name, A, ("total_from_operators", T), panels, catalog)
                one["total_entry"] = "operator estimate on the target's mask"
                one["target_entry"] = a_name
                one["scale_mode"] = run["scale_mode"]
                one["n_mask_cells"] = run_pair["n_mask_cells"]
                one["operator_checks"] = dict(a_vs_stored=run_pair["a"] - run_pair["a_stored"], a_draws_vs_stored_scaled=run_pair["a_draws_vs_stored"],
                                              stored_total=run_pair.get("stored_total"))
                st_ = run_pair.get("stored_total")
                if abs(run_pair["a"] - run_pair["a_stored"]) > 1e-8 * max(1.0, abs(run_pair["a"])) or (run_pair["a_draws_vs_stored"] or 0) > 1e-8:
                    one["code"], one["reasons"] = "OTHER", one["reasons"] + ["OTHER: the target's operator estimate differs from its stored result"]
                if st_ and st_["same_cells"] and ((st_["att_diff"] or 0) > 1e-8 or (st_["draws_max_diff"] or 0) > 1e-8):
                    one["code"], one["reasons"] = "OTHER", one["reasons"] + ["OTHER: the total's operator estimate differs from its stored result"]
                prec = {**prec, **one}
            pair_recs.append(prec)
        pairs.extend(pair_recs)
        row["pairs"] = [(r["pair"], r["code"]) for r in pair_recs]
        row["code"] = design_code([r["code"] for r in pair_recs])
        row["reasons"] = [f"{r['pair']}:{r['code']}" for r in pair_recs] if pair_recs else ["NO_PAIR: no registered ESTIMATED outcome is a target of the pair table"]
        rows.append(row)

    D_n = len(roster)
    eval_pairs = [p for p in pairs if p["code"] == "EVAL"]
    n = len(eval_pairs)
    F = len({p["policy_family"] for p in eval_pairs})
    e1 = D3.e1_summary(eval_pairs)
    # D3-V1 comparison: the pairs that were EVAL in D3-V1 must give the same numbers
    v1 = S.read_json(D3V1_RESULTS)
    v1_eval = {(p["design_id"], p["pair"]): p for p in v1["pairs"] if p["code"] == "EVAL"}
    comparison = []
    for key, p1 in v1_eval.items():
        p2 = next((p for p in eval_pairs if (p["design_id"], p["pair"]) == key), None)
        if p2 is None:
            comparison.append(dict(pair=list(key), v2_code=next((p["code"] for p in pairs if (p["design_id"], p["pair"]) == key), None)))
            continue
        diffs = {k: S.max_scaled_diff([p1["numbers"][k]], [p2["numbers"][k]]) for k in ("a", "t", "c", "sd_c", "cutoff_c", "s", "mu", "tau", "Z", "p_sub") if p1["numbers"].get(k) is not None}
        comparison.append(dict(pair=list(key), max_scaled_difference=max(diffs.values()), per_number=diffs))
    runs_needed = [dict(design_id=p["design_id"], pair=p["pair"], total_series=p["total_series"], target_entry=p["target_entry"], mask_cells=p.get("n_mask_cells"), scale_mode=p.get("scale_mode"), code=p["code"])
                   for p in pairs]
    me = Path(__file__).resolve()
    stamp = {"rule_id": RULE_ID, "question": QUESTION, "version": VERSION, "cutoff_utc": live_meta["as_of"], "run_utc": utc_now(), "live_set_sha256": live_sha,
             "sweep_gate_report_sha256": S.sha256_file(sweep_dir / "GATE_REPORT.json"), "d3_runs_sha256": S.sha256_file(sweep_dir / "D3_RUNS.json"),
             "sweep_manifest_sha256": S.sha256_file(sweep_dir / "SWEEP_MANIFEST.json") if (sweep_dir / "SWEEP_MANIFEST.json").exists() else None,
             "sweep_run_sha256": S.sha256_file(sweep_dir / "SWEEP_RUN.json"), "d3_runs_code": runs.get("code"),
             "code_sha256": S.sha256_file(me), "code": S.rel(me), "d3_v1_code_sha256": S.sha256_file(HERE / "build_d3.py"), "e1_engine_code_sha256": S.sha256_file(HERE / "build_n1.py"),
             "sweep_code": B.code_hashes(), "amendment_sha256": B.amend_hashes(), "question_file_sha256": S.sha256_file(D3.AMEND_Q), "build_file_sha256": S.sha256_file(D3.AMEND_B),
             "entry_D3_sha256": entry_d3, "D": D_n, "estimated": live_meta["counts"]["estimated"], "not_evaluable": live_meta["counts"]["not_evaluable"],
             "outside_D": live_meta["outside_D"], "n": n, "F": F, "python": sys.version.split()[0], "numpy": np.__version__, "seeds": {"E1": D3.SEED_E1}, "B": D3.B_BOOT,
             "no_significance_label": True}
    result = OrderedDict([
        ("D", D_n), ("n_pairs_eval", n), ("F", F), ("coverage_codes_over_D", D3.code_table(rows)), ("candidate_pairs", len(pairs)), ("pair_codes", D3.code_table(pairs)),
        ("minimums_printed_beside_n", {"engine_floor": {"n": D3.FLOOR_N, "F": D3.FLOOR_F, "n_ge": n >= D3.FLOOR_N, "F_ge": F >= D3.FLOOR_F},
                                       "planning_minimum": {"n": D3.PLAN_N, "F": D3.PLAN_F, "n_ge": n >= D3.PLAN_N, "F_ge": F >= D3.PLAN_F},
                                       "planning_detectable_mean_Z_2.8_over_sqrt_n": 2.8 / math.sqrt(n) if n else None,
                                       "note": "EX-1: printed beside n, gate nothing (the engine floor only decides whether E1 runs)"}),
        ("E1", e1), ("theta_D3_plain_mean_Z", float(np.mean([p["numbers"]["Z"] for p in eval_pairs])) if n else None),
        ("pattern_counts", D3.pattern_counts(eval_pairs) if eval_pairs else None),
        ("comparison_with_D3_V1", comparison), ("pairs_and_runs", runs_needed)])
    checks = {"eval_pairs_corr_of_target_and_total_draws": {f"{p['design_id']}:{p['pair']}": p["numbers"]["corr_a_t_draws"] for p in eval_pairs},
              "operator_checks": {f"{p['design_id']}:{p['pair']}": p.get("operator_checks") for p in pairs if p.get("operator_checks")}}
    payload = S.clean(OrderedDict([
        ("schema_version", "l3-meta-d3-v2"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {S.rel(p): {"sha256": S.sha256_file(p), "bytes": p.stat().st_size} for p in (S.LIVE, D3.AMEND_Q, D3.AMEND_B, D3.HASH_REC, sweep_dir / "GATE_REPORT.json", sweep_dir / "D3_RUNS.json", D3V1_RESULTS)}),
        ("question", OrderedDict([("D3", result)])), ("checks", checks), ("designs", rows), ("pairs", pairs),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():
            sys.exit(f"{S.rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy_ = out_dir / "l2_set_live_used.json"
        shutil.copy2(S.LIVE, copy_)
        assert S.sha256_file(copy_) == live_sha
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_d3_v2.py " + " ".join(argv if argv is not None else sys.argv[1:]), "code_sha256": stamp["code_sha256"],
                                 "live_set_sha256": live_sha, "d3_runs_sha256": stamp["d3_runs_sha256"], "results_file": S.rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": stamp, "D3": payload["question"]["D3"], "pairs": [{k: p.get(k) for k in ("design_id", "pair", "code", "reasons", "scale_mode", "numbers")} for p in payload["pairs"]]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {S.rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
