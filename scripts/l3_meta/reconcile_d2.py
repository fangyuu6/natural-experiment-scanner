#!/usr/bin/env python3
"""D2 step 1: reconcile the two blind codings of the 560-cell grid (35 policy groups x 16 outcome families) and add the third reader's decisions.

Spec: docs/plans/l3-meta/PREREG_AMEND_20261015_D_QUESTIONS.md, entry D2, paragraph "Coding": two coders code every cell as `channel`, `no_channel` or
`unsure` ("unsure counts as a channel"); "a third reader decides the cells where the coders differ"; "a cell is a negative control when the final code is
`no_channel`"; secondary: "the stricter set of cells that both first coders marked `no_channel`".  Build rules: PREREG_AMEND_20261015_D2_V1.md section 3
(hash record PREREG_AMEND_20261015_D2_HASH.txt).

Routing (fixed in this file and in the amendment before the third read; the spec's words, "the cells where the coders differ"):
  agreed          both coders gave the same code                               -> final = that code, no third read
  third_reader    the two codes differ (any pair of the three codes)           -> final = the third reader's code
Negative control (nc) = final code `no_channel`; strict set (strict_nc) = both first coders `no_channel`.

Reads ONLY: the two coder files and (stage reconcile) the third reader's file.  No L2 file, no sweep file, no estimate, no live set.
Writes (new files, never overwritten): d2_blank_grid_v1.csv (stage grid); d2_reconciled_v1.csv and RECEIPT_d2_reconciled_v1.json (stage reconcile).

Run:  python3 -I -B scripts/l3_meta/reconcile_d2.py --stage dry|grid|reconcile      (standard library only)
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ROOT = C.ROOT
D2DIR = ROOT / "data/l3_meta/d2_plausibility"
CODER1 = D2DIR / "d2_codes_coder1_v1.csv"
CODER2 = D2DIR / "d2_codes_coder2_v1.csv"
THIRD = D2DIR / "d2_codes_third_v1.csv"
GRID = D2DIR / "d2_blank_grid_v1.csv"
OUT_CSV = D2DIR / "d2_reconciled_v1.csv"
OUT_RECEIPT = D2DIR / "RECEIPT_d2_reconciled_v1.json"

CODES = ("channel", "unsure", "no_channel")
ORDER = {"no_channel": 0, "unsure": 1, "channel": 2}          # ordinal scale for the weighted kappa: distance = number of steps
N_GROUPS, N_FAMILIES = 35, 16
HEADER_CODER = ["policy_group", "outcome_family", "code", "reason"]
HEADER_THIRD = ["policy_group", "outcome_family", "third_code", "third_reason"]
OUT_HEADER = ["policy_group", "outcome_family", "coder1_code", "coder2_code", "route", "third_code", "final_code", "nc", "strict_nc", "basis"]
FINAL_CODES = CODES


def load_csv(path, header):
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != header:
            raise ValueError(f"{C.rel(path)}: header {rd.fieldnames} differs from {header}")
        return list(rd)


def check_coder(rows, label):
    keys = [(r["policy_group"], r["outcome_family"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label}: duplicate (policy_group, outcome_family) cells")
    groups, fams = {k[0] for k in keys}, {k[1] for k in keys}
    if len(rows) != N_GROUPS * N_FAMILIES or len(groups) != N_GROUPS or len(fams) != N_FAMILIES or len(set(keys)) != len(groups) * len(fams):
        raise ValueError(f"{label}: {len(rows)} rows, {len(groups)} groups, {len(fams)} families: not the complete 35 x 16 grid")
    for r in rows:
        if r["code"] not in CODES:
            raise ValueError(f"{label}: {r['policy_group']} x {r['outcome_family']}: code {r['code']!r} is not one of {CODES}")
        if not r["reason"].strip():
            raise ValueError(f"{label}: {r['policy_group']} x {r['outcome_family']}: empty reason")
    return sorted(keys)


# ---------------------------------------------------------------------------------------------
# agreement statistics
# ---------------------------------------------------------------------------------------------
def cohen_kappa(a, b, weights=None):
    """Cohen's kappa of two label lists (weights: None = identity; or a function w(x, y) in [0, 1], 1 on the diagonal).
    Returns dict(kappa, se, po, pe); kappa is None when chance agreement is 1.  se = sqrt(po (1 - po) / (n (1 - pe)^2)) for the unweighted kappa
    (the usual large-sample approximation, descriptive); None for the weighted kappa."""
    n = len(a)
    cats = sorted(set(a) | set(b))
    w = weights or (lambda x, y: 1.0 if x == y else 0.0)
    pa = {c: sum(1 for x in a if x == c) / n for c in cats}
    pb = {c: sum(1 for y in b if y == c) / n for c in cats}
    po = sum(w(x, y) for x, y in zip(a, b)) / n
    pe = sum(w(c, d) * pa[c] * pb[d] for c in cats for d in cats)
    if abs(1.0 - pe) < 1e-15:
        return {"kappa": None, "se": None, "po": po, "pe": pe}
    k = (po - pe) / (1.0 - pe)
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2)) if weights is None else None
    return {"kappa": k, "se": se, "po": po, "pe": pe}


def confusion(a, b, cats=CODES):
    cm = Counter(zip(a, b))
    return {x: {y: cm[(x, y)] for y in cats} for x in cats}


def agreement_stats(rows1, rows2):
    """rows1, rows2 aligned on the same keys.  Descriptive statistics of the two first codings (before the third reader)."""
    a, b = [r["code"] for r in rows1], [r["code"] for r in rows2]
    n = len(a)
    out = OrderedDict()
    out["n_cells"] = n
    out["coder1_counts"] = {c: a.count(c) for c in CODES}
    out["coder2_counts"] = {c: b.count(c) for c in CODES}
    out["confusion_rows_coder1_cols_coder2"] = confusion(a, b)
    out["n_agree"] = sum(1 for x, y in zip(a, b) if x == y)
    out["raw_agreement"] = out["n_agree"] / n
    out["kappa_3_codes"] = cohen_kappa(a, b)
    out["kappa_3_codes_linear_weights_no_channel_unsure_channel"] = cohen_kappa(a, b, weights=lambda x, y: 1.0 - abs(ORDER[x] - ORDER[y]) / 2.0)
    # the decision-relevant binary view: no_channel (candidate negative control) versus channel-or-unsure
    ba = ["no_channel" if x == "no_channel" else "channel_or_unsure" for x in a]
    bb = ["no_channel" if y == "no_channel" else "channel_or_unsure" for y in b]
    both = sum(1 for x, y in zip(ba, bb) if x == y == "no_channel")
    only1 = sum(1 for x, y in zip(ba, bb) if x == "no_channel" and y != "no_channel")
    only2 = sum(1 for x, y in zip(ba, bb) if x != "no_channel" and y == "no_channel")
    neither = n - both - only1 - only2
    out["binary_no_channel_vs_other"] = {
        "both_no_channel": both, "only_coder1_no_channel": only1, "only_coder2_no_channel": only2, "neither": neither,
        "raw_agreement": (both + neither) / n, "kappa": cohen_kappa(ba, bb),
        "positive_specific_agreement_no_channel": 2 * both / (2 * both + only1 + only2) if (both + only1 + only2) else None,
        "negative_specific_agreement": 2 * neither / (2 * neither + only1 + only2) if (neither + only1 + only2) else None,
        "jaccard_of_no_channel_sets": both / (both + only1 + only2) if (both + only1 + only2) else None}
    by_family, by_group = OrderedDict(), OrderedDict()
    for key, store in (("outcome_family", by_family), ("policy_group", by_group)):
        for v in sorted({r[key] for r in rows1}):
            idx = [i for i, r in enumerate(rows1) if r[key] == v]
            store[v] = {"n": len(idx), "n_agree": sum(1 for i in idx if a[i] == b[i]),
                        "both_no_channel": sum(1 for i in idx if a[i] == b[i] == "no_channel"),
                        "exactly_one_no_channel": sum(1 for i in idx if (a[i] == "no_channel") != (b[i] == "no_channel"))}
    out["by_family"], out["by_policy_group"] = by_family, by_group
    return out


# ---------------------------------------------------------------------------------------------
# routing and reconciliation
# ---------------------------------------------------------------------------------------------
def route(c1, c2):
    return "agreed" if c1 == c2 else "third_reader"


def align(rows1, rows2):
    """Both coder files as lists in the sorted key order."""
    k1, k2 = check_coder(rows1, "coder1"), check_coder(rows2, "coder2")
    if k1 != k2:
        raise ValueError("coder 1 and coder 2 cover different cells")
    by1 = {(r["policy_group"], r["outcome_family"]): r for r in rows1}
    by2 = {(r["policy_group"], r["outcome_family"]): r for r in rows2}
    return k1, [by1[k] for k in k1], [by2[k] for k in k1]


def reconcile(keys, r1, r2, third_rows):
    routes = {k: route(x["code"], y["code"]) for k, x, y in zip(keys, r1, r2)}
    need = sorted(k for k, v in routes.items() if v == "third_reader")
    third = {}
    for t in third_rows or []:
        k = (t["policy_group"], t["outcome_family"])
        if k in third:
            raise ValueError(f"third reader: duplicate cell {k}")
        if t["third_code"] not in CODES:
            raise ValueError(f"third reader: {k} code {t['third_code']!r}")
        if not t["third_reason"].strip():
            raise ValueError(f"third reader: {k} has no reason")
        third[k] = t
    if sorted(third) != need:
        extra, missing = sorted(set(third) - set(need)), sorted(set(need) - set(third))
        raise ValueError(f"third reader file does not cover exactly the {len(need)} routed cells (extra {extra[:3]}, missing {missing[:3]})")
    final = OrderedDict()
    for k, x, y in zip(keys, r1, r2):
        rt = routes[k]
        c1, c2 = x["code"], y["code"]
        if rt == "agreed":
            code, tcode, basis = c1, "", f"both coders {c1}"
        else:
            tcode = third[k]["third_code"]
            code, basis = tcode, f"third reader (coder 1 {c1}, coder 2 {c2}): " + third[k]["third_reason"].strip()
        final[k] = {"policy_group": k[0], "outcome_family": k[1], "coder1_code": c1, "coder2_code": c2, "route": rt, "third_code": tcode,
                    "final_code": code, "nc": int(code == "no_channel"), "strict_nc": int(c1 == c2 == "no_channel"), "basis": basis}
    return routes, final


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", choices=("dry", "grid", "reconcile"), default="dry")
    ap.add_argument("--out-dir", default=str(D2DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    rows1, rows2 = load_csv(CODER1, HEADER_CODER), load_csv(CODER2, HEADER_CODER)
    keys, r1, r2 = align(rows1, rows2)
    stats = agreement_stats(r1, r2)
    if args.stage == "dry":
        routes = {k: route(x["code"], y["code"]) for k, x, y in zip(keys, r1, r2)}
        print("routes:", dict(Counter(routes.values())))
        print("agreement:", {k: stats[k] for k in ("n_agree", "raw_agreement", "kappa_3_codes", "binary_no_channel_vs_other")})
        return 0
    if args.stage == "grid":
        res = C.write_new(out_dir / GRID.name, C.csv_bytes(["policy_group", "outcome_family"], keys))
        print(res)
        return 0
    third_rows = load_csv(THIRD, HEADER_THIRD)
    routes, final = reconcile(keys, r1, r2, third_rows)
    counts_route = dict(sorted(Counter(routes.values()).items()))
    counts_final = {c: sum(1 for v in final.values() if v["final_code"] == c) for c in FINAL_CODES}
    tc = Counter(t["third_code"] for t in third_rows)
    rows_out = [[final[k][h] for h in OUT_HEADER] for k in keys]
    wrote_csv = C.write_new(out_dir / OUT_CSV.name, C.csv_bytes(OUT_HEADER, rows_out))
    nc_groups = Counter(k[0] for k, v in final.items() if v["nc"])
    receipt = {
        "version": "d2-reconciled-v1", "created_utc": C.utc_now(), "script": C.rel(Path(__file__)), "script_sha256": C.sha256_file(Path(__file__)),
        "inputs": {C.rel(p): {"sha256": C.sha256_file(p), "bytes": p.stat().st_size} for p in (CODER1, CODER2, THIRD)},
        "rule": {"agreed": "both coders gave the same code: final = that code", "third_reader": "the two codes differ: final = the third reader's code",
                 "nc": "final code no_channel", "strict_nc": "both first coders no_channel"},
        "counts_route": counts_route, "counts_final_code": counts_final, "n_nc": sum(v["nc"] for v in final.values()), "n_strict_nc": sum(v["strict_nc"] for v in final.values()),
        "third_reader_codes": dict(tc), "n_nc_from_third_reader": sum(1 for v in final.values() if v["route"] == "third_reader" and v["nc"]),
        "nc_cells_by_policy_group": dict(sorted(nc_groups.items())), "groups_without_nc_cell": sorted({k[0] for k in keys} - set(nc_groups)),
        "agreement_before_third_reader": stats, "nc_invalid": [],
        "output": wrote_csv,
        "blindness": "reads the two coder files and the third reader file only; no L2 file, sweep file, estimate or live set"}
    wrote_receipt = C.write_new(out_dir / OUT_RECEIPT.name, C.json_bytes(receipt))
    print(wrote_csv)
    print(wrote_receipt)
    print("route counts:", counts_route, "| final codes:", counts_final, "| NC:", receipt["n_nc"], "| strict NC:", receipt["n_strict_nc"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
