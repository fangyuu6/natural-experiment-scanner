#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version Q2-V2 (rule id EX-1): Q2, do matched published effects shrink in our estimates?  Scale = L2 export or labelled L3 read.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2.md (hash and T_amend in PREREG_AMEND_20261016_Q2_V2_HASH.txt), written before this code
existed; the V1 build rules (PREREG_AMEND_20261015_Q2_V1.md) are kept except section 4.3, which section 3 of the V2 amendment replaces.

Q2-V2 = Q2-V1 with one change: the scale s_i is the L2 export `s_pre` when there is one, otherwise `s_pre_L3` from data/l3_meta/s_pre_l3_v1/
S_PRE_L3_V1.json (labelled L3 read; validated and structurally checked there), otherwise undefined.  The pure functions of build_q2.py (sha256
recorded in the amendment, unchanged) do the rest: this script overlays the scale into the first-certified-run table and calls V1.assemble.
Nothing under scripts/l2_pipeline or data/l2_pipeline is opened; no network.  The frozen test is NOT implemented (amendment 3.3).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_q2_v2.py [--dry-run] [--out-dir DIR]     (standard library only)
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_q2 as V1  # noqa: E402  (pure functions of Q2-V1, unchanged)

RULE_ID = "EX-1"
QUESTION = "Q2"
VERSION = "Q2-V2"
DATA = ROOT / "data"
LIVE, V3, TARGETS, PAIRS, RECON, RECEIPT = V1.LIVE, V1.V3, V1.TARGETS, V1.PAIRS, V1.RECON, V1.RECEIPT
SPRE = DATA / "l3_meta" / "s_pre_l3_v1" / "S_PRE_L3_V1.json"
V1_RESULTS = DATA / "l3_meta" / "q2_v1" / "RESULTS_Q2_V1.json"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2_HASH.txt"
OUT_DIR = DATA / "l3_meta" / "q2_v2"
OUT_NAME = "RESULTS_Q2_V2.json"

finite, mean, rel, sha256_file, read_json, clean = V1.finite, V1.mean, V1.rel, V1.sha256_file, V1.read_json, V1.clean


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_hash_record(path=AMEND_HASH):
    return V1.parse_hash_record(path)


# ---------------------------------------------------------------------------------------------
# pure rules (unit-tested in test_build_q2_v2.py)
# ---------------------------------------------------------------------------------------------
def overlay_scale(v3, spre_reads):
    """Amendment 3.1.  Returns (copy of the first-certified-run table with the scale overlaid, source by design).
    L2 export first; else the L3 read when it is used_in_q2 and finite positive; else undefined.  An L3 value never replaces an L2 export."""
    by = {r["design_id"]: r for r in spre_reads}
    out = copy.deepcopy(v3)
    source = {}
    for d in out["designs"]:
        l2 = d.get("s_pre")
        if finite(l2) and l2 > 0:
            source[d["design_id"]] = "l2_export"
            continue
        r = by.get(d["design_id"])
        if r and r.get("used_in_q2") and finite(r.get("s_pre_L3")) and r["s_pre_L3"] > 0:
            d["s_pre"] = r["s_pre_L3"]
            source[d["design_id"]] = "l3_read"
        else:
            d["s_pre"] = l2 if finite(l2) else None
            source[d["design_id"]] = None
    return out, source


def g_by_scale(row, scale):
    return V1.gap(row["a_i"], row["b_lit"], row["b"], scale)


def v2_blocks(rows, source, spre_reads):
    """Everything V2 adds to the V1 result: scale sources, G by source, the S1 sensitivity."""
    by = {r["design_id"]: r for r in spre_reads}
    ev = [r for r in rows if r["code"] == "EVAL"]
    for r in rows:
        r["scale_source"] = source.get(r["design_id"]) if r["live_status"] == "estimated" else None
    ev_l2 = [r for r in ev if r["scale_source"] == "l2_export"]
    ev_l3 = [r for r in ev if r["scale_source"] == "l3_read"]
    s1 = []
    for r in ev_l3:
        s1_sd = ((by.get(r["design_id"]) or {}).get("sensitivity_S1") or {}).get("s_pre_L3")
        if finite(s1_sd) and s1_sd > 0:
            s1.append({"design_id": r["design_id"], "s_pre_S1": s1_sd, "g": g_by_scale(r, s1_sd)})
    return {
        "G_scale_sources": {"definition": "every EVAL design with its scale source: l2_export (exported by L2) or l3_read (S_PRE_L3_V1, amendment 2)",
                            "n_l2_export": len(ev_l2), "n_l3_read": len(ev_l3),
                            "per_design": [{"design_id": r["design_id"], "scale_source": r["scale_source"], "s": r["s_pre"], "g": r["g"]} for r in ev]},
        "G_l2_export_only": {"definition": "mean g over EVAL designs whose scale is an L2 export (this is the Q2-V1 primary)", "n": len(ev_l2),
                             "value": mean(r["g"] for r in ev_l2) if ev_l2 else None},
        "G_S1_scale_variant": {"definition": "G with the S1 scale (partial year kept in the clean set, amendment 2.6) for the l3_read designs; labelled sensitivity, never selecting",
                               "n": len(s1), "value": mean(x["g"] for x in s1) if s1 else None, "per_design": s1,
                               "complete": len(s1) == len(ev_l3)},
    }


def delta_vs_v1(rows_v2, v1_designs):
    """Designs whose coverage code differs from the Q2-V1 table (expected: only the strict-matched estimated designs that got an L3 scale)."""
    v1 = {d["design_id"]: d for d in v1_designs}
    out = []
    for r in rows_v2:
        o = v1.get(r["design_id"])
        if o is None:
            out.append({"design_id": r["design_id"], "v1": None, "v2": r["code"]})
        elif o["code"] != r["code"]:
            out.append({"design_id": r["design_id"], "v1": o["code"], "v2": r["code"]})
    return out


def other_fields_equal(rows_v2, v1_designs, ignore=("scale_source", "g", "s_pre", "code", "reasons")):
    """Fields of the designs other than the ones allowed to differ must be identical to V1 (the version changes the scale source only)."""
    v1 = {d["design_id"]: d for d in v1_designs}
    bad = []
    for r in rows_v2:
        o = v1.get(r["design_id"])
        if o is None:
            bad.append(r["design_id"])
            continue
        for k, v in r.items():
            if k in ignore:
                continue
            if k in o and clean(o[k]) != clean(v):
                bad.append(f"{r['design_id']}:{k}")
    return bad


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    rec = parse_hash_record()
    amend_sha = sha256_file(AMEND)
    if rec["sha256"] != amend_sha:
        sys.exit("amendment file differs from its hash record: not run")
    v1_code = ROOT / "scripts/l3_meta/build_q2.py"
    v1_code_sha = sha256_file(v1_code)
    if rec["listed"].get(V1.rel(v1_code)) != v1_code_sha:
        sys.exit("build_q2.py (the V1 code reused here) differs from the amendment record: not run")
    shas = {V1.rel(p): sha256_file(p) for p in (LIVE, V3, TARGETS, PAIRS, RECON, RECEIPT, V1_RESULTS)}
    for p in (LIVE, V3, TARGETS, PAIRS, RECON, RECEIPT, V1_RESULTS):
        if rec["listed"].get(V1.rel(p)) != shas[V1.rel(p)]:
            sys.exit(f"{V1.rel(p)} differs from the amendment record: not run")

    spre = read_json(SPRE)
    spre_sha = sha256_file(SPRE)
    if spre["stamp"]["amendment_sha256"] != amend_sha:
        sys.exit("S_PRE_L3_V1.json was built under another amendment: not run")
    gate = spre["validation"]["gate"]

    live, v3, targets, pairs, recon = V1.load_inputs()
    v3_over, source = overlay_scale(v3, spre["reads"])
    result, rows, checks = V1.assemble(live, v3_over, targets, pairs, recon, read_json(RECEIPT))
    blocks = v2_blocks(rows, source, spre["reads"])
    v1_designs = read_json(V1_RESULTS)["designs"]
    delta = delta_vs_v1(rows, v1_designs)
    other_bad = other_fields_equal(rows, v1_designs)

    # V2 wording of the pieces V1.assemble labels for the L2 export
    result["G"]["definition"] = "mean_i a_i (b_lit,i - b_i) / s_i over EVAL designs, equal design weights; s_i = L2 export s_pre, else the labelled L3 read s_pre_L3 (amendment 3.1)"
    for e in result["G"]["per_design"]:
        e["scale_source"] = source.get(e["design_id"])
    for g in result["descriptive_raw_gaps"]:
        g["scale_source"] = source.get(g["design_id"])
        g["s_pre_S1"] = next((x["s_pre_S1"] for x in blocks["G_S1_scale_variant"]["per_design"] if x["design_id"] == g["design_id"]), None)
    result.update(blocks)
    result["delta_vs_v1"] = {"codes_changed": delta, "counts_v1": read_json(V1_RESULTS)["question"]["Q2"]["coverage_codes"], "counts_v2": result["coverage_codes"],
                             "other_fields_differing": other_bad}
    result["scale_file"] = {"path": V1.rel(SPRE), "sha256": spre_sha, "code_sha256": spre["stamp"]["code_sha256"], "validation_gate": gate,
                            "used": [{"design_id": r["design_id"], "n": r["n"], "s_pre_L3": r["s_pre_L3"], "used_in_q2": r["used_in_q2"]} for r in spre["reads"]]}
    if result["n"]:
        result["decision"]["reading"] += ". Scale of the EVAL designs: " + ", ".join(f"{e['design_id']} = {e['scale_source']}" for e in result["G"]["per_design"])

    me = Path(__file__).resolve()
    stamp = OrderedDict([
        ("rule_id", RULE_ID), ("question", QUESTION), ("version", VERSION), ("cutoff_utc", live["as_of"]), ("run_utc", utc_now()),
        ("live_set_sha256", shas[V1.rel(LIVE)]), ("first_run_table_sha256", shas[V1.rel(V3)]), ("targets_r2_sha256", shas[V1.rel(TARGETS)]),
        ("pairs_sha256", shas[V1.rel(PAIRS)]), ("reconciled_sha256", shas[V1.rel(RECON)]), ("scale_file_sha256", spre_sha),
        ("scale_code_sha256", spre["stamp"]["code_sha256"]), ("v1_functions_code_sha256", v1_code_sha),
        ("code_sha256", sha256_file(me)), ("code", V1.rel(me)), ("amendment_sha256", amend_sha), ("t_amend_utc", rec["t_amend_utc"]),
        ("D", result["D"]), ("estimated", live["counts"]["estimated"]), ("not_evaluable", live["counts"]["not_evaluable"]),
        ("outside_D", {k: len(v) for k, v in live["excluded_not_counted"].items()}),
        ("n", result["n"]), ("F", result["F"]), ("python", sys.version.split()[0]), ("no_significance_label", True)])
    payload = clean(OrderedDict([
        ("schema_version", "l3-meta-q2-v2"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim; scale partly a labelled L3 read"), ("stamp", stamp),
        ("inputs", {**{V1.rel(p): {"sha256": shas[V1.rel(p)], "bytes": p.stat().st_size} for p in (LIVE, V3, TARGETS, PAIRS, RECON, RECEIPT, V1_RESULTS)},
                    V1.rel(SPRE): {"sha256": spre_sha, "bytes": SPRE.stat().st_size}, V1.rel(AMEND): {"sha256": amend_sha, "bytes": AMEND.stat().st_size}}),
        ("question", OrderedDict([("Q2", result)])), ("checks", checks), ("designs", rows),
        ("audit", {"l2_files_written": 0, "l2_trees_opened_by_this_script": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():
            sys.exit(f"{V1.rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy_p = out_dir / "l2_set_live_used.json"
        shutil.copy2(LIVE, copy_p)
        assert sha256_file(copy_p) == shas[V1.rel(LIVE)]
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_q2_v2.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code_sha256"], "live_set_sha256": stamp["live_set_sha256"], "amendment_sha256": amend_sha,
                                 "scale_file_sha256": spre_sha, "results_file": V1.rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": payload["stamp"], "Q2": payload["question"]["Q2"], "checks": payload["checks"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {V1.rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
