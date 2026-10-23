#!/usr/bin/env python3
"""Q2 step 1: reconcile the two blind codings of the 179 new pairs and add the third reader's decisions.

Spec (PREREG_L3_META.md Q2 + Q2_CODING_SHEET_README_v1.md + INPUTS_B.md section 3): two independent coders fill the eight match
columns, a third reads differences, and the strict match (policy scope/dose, population, outcome, units each `match` or
`arith_convertible`, EXTRACTION_PROTOCOL.md) is locked before our outcome.  "No-paywall-access evidence -> unavailable".

Reads ONLY: the coding sheet, the two coder files, and the third reader's decision file.  No L2 file, no estimate, no live set.
Writes (new, versioned, never overwritten): q2_reconciled_v1.csv and RECEIPT_q2_reconciled_v1.json.

Which pairs go to the third reader (the decision-relevant reading of "differences"; fixed here, before the third reader looks):
  both_yes     both coders code strict_core = yes and all eight cells are identical            -> no third read
  settled_no   each coder has at least one core cell partial/mismatch AND they share a core
               dimension that both code partial/mismatch                                      -> no third read
               (a cell-level difference on a non-core dimension, or on how badly a COMMON failing core dimension fails, cannot
               change a lock)
  third_reader everything else: strict_core differs, a coder left a core cell `unknown` without another failing core cell,
               the coders' failing core dimensions are disjoint, or a both-yes pair differs on any cell.

Final class of a pair (EXTRACTION_PROTOCOL.md class rules):
  STRICT       core four all match/arith_convertible and none of comparison, clock, aggregation, horizon is `mismatch`
  CORE_ONLY    core four pass but a non-core dimension is a documented `mismatch` (protocol: non_strict)
  UNAVAILABLE  third reader could not verify a core cell from public, non-paywalled text (spec: "no-paywall-access -> unavailable")
  NOT_STRICT   at least one core dimension is partial/mismatch

Run:  python3 -I -B scripts/l3_meta/reconcile_q2.py            (standard library only)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ROOT = C.ROOT
Q2DIR = ROOT / "data/l3_meta/q2_join"
SHEET = Q2DIR / "q2_coding_sheet_v1.csv"
CODER1 = Q2DIR / "q2_codes_coder1_v1.csv"
CODER2 = Q2DIR / "q2_codes_coder2_v1.csv"
THIRD = Q2DIR / "q2_third_reader_v1.csv"
OUT_CSV = Q2DIR / "q2_reconciled_v1.csv"
OUT_RECEIPT = Q2DIR / "RECEIPT_q2_reconciled_v1.json"

DIMS = ["policy_scope_dose", "population", "outcome", "units", "comparison", "clock", "aggregation", "horizon"]
CORE = DIMS[:4]
NONCORE = DIMS[4:]
VOCAB = {"match", "arith_convertible", "partial", "mismatch", "unknown"}
PASS = {"match", "arith_convertible"}
FAIL = {"partial", "mismatch"}
CLASSES = {"STRICT", "CORE_ONLY", "UNAVAILABLE", "NOT_STRICT"}


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def check_vocab(rows, label):
    for r in rows:
        for d in DIMS:
            if r[d] not in VOCAB:
                raise ValueError(f"{label}: pair {r['pair_id']} dimension {d} has value {r[d]!r}")
        if r["strict_core_yes_no"] not in ("yes", "no"):
            raise ValueError(f"{label}: pair {r['pair_id']} strict_core_yes_no {r['strict_core_yes_no']!r}")
        own = "yes" if all(r[d] in PASS for d in CORE) else "no"
        if own != r["strict_core_yes_no"]:
            raise ValueError(f"{label}: pair {r['pair_id']} strict_core_yes_no {r['strict_core_yes_no']} contradicts its own core cells")


def core_status(r):
    vals = [r[d] for d in CORE]
    if all(v in PASS for v in vals):
        return "YES"
    if any(v in FAIL for v in vals):
        return "NO_DECIDED"
    return "UNDETERMINED"


def fail_set(r):
    return {d for d in CORE if r[d] in FAIL}


def route(r1, r2):
    s1, s2 = core_status(r1), core_status(r2)
    if s1 == "YES" and s2 == "YES":
        return "both_yes" if all(r1[d] == r2[d] for d in DIMS) else "third_reader"
    if s1 == "NO_DECIDED" and s2 == "NO_DECIDED" and (fail_set(r1) & fail_set(r2)):
        return "settled_no"
    return "third_reader"


def cohen_kappa(a, b):
    """Cohen's kappa for two label lists; None if chance agreement is 1 (both coders used a single label throughout)."""
    n = len(a)
    if n == 0:
        return None
    cats = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((sum(1 for x in a if x == c) / n) * (sum(1 for y in b if y == c) / n) for c in cats)
    if abs(1.0 - pe) < 1e-15:
        return None
    return (po - pe) / (1.0 - pe)


def collapse(v):
    return "pass" if v in PASS else ("fail" if v in FAIL else "unknown")


def agreement_stats(rows1, rows2):
    n = len(rows1)
    out = {"n_pairs": n, "dimensions": {}}
    for d in DIMS:
        a = [r[d] for r in rows1]
        b = [r[d] for r in rows2]
        entry = {"raw_agreement": sum(1 for x, y in zip(a, b) if x == y) / n,
                 "n_agree": sum(1 for x, y in zip(a, b) if x == y),
                 "kappa_5level": cohen_kappa(a, b)}
        ac, bc = [collapse(x) for x in a], [collapse(y) for y in b]
        entry["raw_agreement_pass_fail_unknown"] = sum(1 for x, y in zip(ac, bc) if x == y) / n
        entry["kappa_pass_fail_unknown"] = cohen_kappa(ac, bc)
        out["dimensions"][d] = entry
    a = [r["strict_core_yes_no"] for r in rows1]
    b = [r["strict_core_yes_no"] for r in rows2]
    out["strict_core"] = {
        "coder1_yes": a.count("yes"), "coder2_yes": b.count("yes"),
        "both_yes": sum(1 for x, y in zip(a, b) if x == "yes" and y == "yes"),
        "coder1_yes_coder2_no": sum(1 for x, y in zip(a, b) if x == "yes" and y == "no"),
        "coder1_no_coder2_yes": sum(1 for x, y in zip(a, b) if x == "no" and y == "yes"),
        "both_no": sum(1 for x, y in zip(a, b) if x == "no" and y == "no"),
        "raw_agreement": sum(1 for x, y in zip(a, b) if x == y) / n,
        "n_agree": sum(1 for x, y in zip(a, b) if x == y),
        "kappa": cohen_kappa(a, b),
    }
    s1 = [core_status(r) for r in rows1]
    s2 = [core_status(r) for r in rows2]
    out["core_status_crosstab"] = {f"{x}|{y}": sum(1 for p, q in zip(s1, s2) if (p, q) == (x, y))
                                   for x in ("YES", "NO_DECIDED", "UNDETERMINED") for y in ("YES", "NO_DECIDED", "UNDETERMINED")}
    out["core_status_coder1"] = {k: s1.count(k) for k in ("YES", "NO_DECIDED", "UNDETERMINED")}
    out["core_status_coder2"] = {k: s2.count(k) for k in ("YES", "NO_DECIDED", "UNDETERMINED")}
    out["any_core_cell_unknown"] = {"coder1": sum(1 for r in rows1 if any(r[d] == "unknown" for d in CORE)),
                                    "coder2": sum(1 for r in rows2 if any(r[d] == "unknown" for d in CORE))}
    return out


def final_from_cells(cells):
    """cells: dict with the eight dimension values.  Returns STRICT / CORE_ONLY / NOT_STRICT (UNAVAILABLE is set by the third reader)."""
    if all(cells[d] in PASS for d in CORE):
        return "CORE_ONLY" if any(cells[d] == "mismatch" for d in NONCORE) else "STRICT"
    return "NOT_STRICT"


def reconcile(sheet, rows1, rows2, third_rows):
    ids = [r["pair_id"] for r in sheet]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate pair_id in the coding sheet")
    for lab, rows in (("coder1", rows1), ("coder2", rows2)):
        if [r["pair_id"] for r in rows] != ids:
            raise ValueError(f"{lab} pair ids/order differ from the coding sheet")
        for r, s in zip(rows, sheet):
            if r["design_id"] != s["design_id"] or r["paper_id"] != s["paper_id"]:
                raise ValueError(f"{lab}: design/paper mismatch for {r['pair_id']}")
        check_vocab(rows, lab)
    by1 = {r["pair_id"]: r for r in rows1}
    by2 = {r["pair_id"]: r for r in rows2}
    routes = {pid: route(by1[pid], by2[pid]) for pid in ids}
    need = sorted(pid for pid, rt in routes.items() if rt == "third_reader")
    third = {}
    for t in third_rows:
        if t["pair_id"] in third:
            raise ValueError(f"third reader: duplicate pair {t['pair_id']}")
        third[t["pair_id"]] = t
    if sorted(third) != need:
        raise ValueError(f"third reader file covers {sorted(third)} but the pairs needing a third read are {need}")
    final = {}
    for pid in ids:
        r1, r2, rt = by1[pid], by2[pid], routes[pid]
        rec = {"pair_id": pid, "design_id": r1["design_id"], "paper_id": r1["paper_id"], "route": rt,
               "coder1_core_status": core_status(r1), "coder2_core_status": core_status(r2),
               "coder1_strict_core": r1["strict_core_yes_no"], "coder2_strict_core": r2["strict_core_yes_no"]}
        if rt == "both_yes":
            cells = {d: r1[d] for d in DIMS}
            rec.update({"final_class": final_from_cells(cells), "common_failing_core_dims": "", "basis": "both coders yes, eight cells identical"})
            rec.update(cells)
        elif rt == "settled_no":
            common = sorted(fail_set(r1) & fail_set(r2), key=CORE.index)
            rec.update({"final_class": "NOT_STRICT", "common_failing_core_dims": ";".join(common),
                        "basis": "both coders code the common core dimension(s) partial/mismatch"})
            rec.update({d: "" for d in DIMS})
        else:
            t = third[pid]
            cells = {d: t["tr_" + d] for d in DIMS}
            for d in DIMS:
                if cells[d] not in VOCAB:
                    raise ValueError(f"third reader: {pid} dimension {d} value {cells[d]!r}")
            fc = t["tr_final_class"]
            if fc not in CLASSES:
                raise ValueError(f"third reader: {pid} final class {fc!r}")
            if fc == "UNAVAILABLE":
                if not any(cells[d] == "unknown" for d in CORE):
                    raise ValueError(f"third reader: {pid} UNAVAILABLE needs an unverifiable (unknown) core cell")
            else:
                expect = final_from_cells(cells)
                if fc != expect:
                    raise ValueError(f"third reader: {pid} class {fc} contradicts its cells (rule gives {expect})")
            if not t["tr_evidence_locator"].strip():
                raise ValueError(f"third reader: {pid} has no evidence locator")
            rec.update({"final_class": fc, "common_failing_core_dims": ";".join(sorted(fail_set(cells), key=CORE.index)),
                        "basis": "third reader: " + t["tr_basis"]})
            rec.update(cells)
        final[pid] = rec
    return ids, routes, final


OUT_HEADER = ["pair_id", "design_id", "paper_id", "route", "coder1_core_status", "coder2_core_status", "coder1_strict_core",
              "coder2_strict_core", "final_class", "common_failing_core_dims"] + DIMS + ["basis"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print statistics and the pairs for the third reader; write nothing")
    ap.add_argument("--out-dir", default=str(Q2DIR), help="folder for q2_reconciled_v1.csv and its receipt (default: the q2_join folder)")
    args = ap.parse_args(argv)
    out_csv = Path(args.out_dir) / OUT_CSV.name
    out_receipt = Path(args.out_dir) / OUT_RECEIPT.name
    sheet = load_csv(SHEET)
    rows1 = load_csv(CODER1)
    rows2 = load_csv(CODER2)
    stats = agreement_stats(rows1, rows2)
    if args.dry_run or not THIRD.exists():
        ids = [r["pair_id"] for r in sheet]
        by1 = {r["pair_id"]: r for r in rows1}
        by2 = {r["pair_id"]: r for r in rows2}
        rts = {pid: route(by1[pid], by2[pid]) for pid in ids}
        print("routes:", {k: list(rts.values()).count(k) for k in ("both_yes", "settled_no", "third_reader")})
        for pid in ids:
            if rts[pid] == "third_reader":
                print("  third reader:", pid)
        print("strict_core:", stats["strict_core"])
        return 0
    third_rows = load_csv(THIRD)
    ids, routes, final = reconcile(sheet, rows1, rows2, third_rows)
    counts_route = {k: sum(1 for v in routes.values() if v == k) for k in ("both_yes", "settled_no", "third_reader")}
    counts_class = {k: sum(1 for r in final.values() if r["final_class"] == k) for k in sorted(CLASSES)}
    rows_out = [[final[pid].get(h, "") for h in OUT_HEADER] for pid in ids]
    wrote_csv = C.write_new(out_csv, C.csv_bytes(OUT_HEADER, rows_out))
    receipt = {
        "version": "v1", "created_utc": C.utc_now(), "script": C.rel(Path(__file__)), "script_sha256": C.sha256_file(Path(__file__)),
        "inputs": {C.rel(p): {"sha256": C.sha256_file(p), "bytes": p.stat().st_size} for p in (SHEET, CODER1, CODER2, THIRD)},
        "counts_route": counts_route, "counts_final_class": counts_class,
        "strict_pairs": [pid for pid in ids if final[pid]["final_class"] == "STRICT"],
        "core_only_pairs": [pid for pid in ids if final[pid]["final_class"] == "CORE_ONLY"],
        "unavailable_pairs": [pid for pid in ids if final[pid]["final_class"] == "UNAVAILABLE"],
        "third_reader_pairs": [pid for pid in ids if routes[pid] == "third_reader"],
        "agreement_before_third_reader": stats,
        "output": wrote_csv,
        "blindness": "reads the coding sheet, two coder files and the third reader file only; no L2 file, estimate, live set or snapshot",
    }
    wrote_receipt = C.write_new(out_receipt, C.json_bytes(receipt))
    print(wrote_csv)
    print(wrote_receipt)
    print("route counts:", counts_route, "| final classes:", counts_class)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
