#!/usr/bin/env python3
"""Independent recomputation of Q2-V1 (plain loops, standard library only, NO import from build_q2.py or reconcile_q2.py).

Re-derives from the raw files: (1) the reconciliation of the 179 pairs (routes, final classes, agreement statistics); (2) the strict-matched
designs from TARGETS_R2 and the new STRICT pairs; (3) the coverage code of every design of D; (4) the estimate b and the noise SD of the
matched archive designs from l2_set_v2.json (a source the builder does not read) and the live set's point_estimate; (5) G, the noise-scale
variant and the raw gaps; (6) the pair-level class counts and the reading flips.  Then compares with data/l3_meta/q2_v1/RESULTS_Q2_V1.json.

Run:  python3 -I -B scripts/l3_meta/check_q2_independent.py [--results PATH]      (exit 0 = all equal)
"""
import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "l3_meta"
FILES = {
    "live": D / "l2_set_live.json", "v2": D / "l2_set_v2.json", "v3": D / "ex1_v3" / "RESULTS_EX1_V3.json",
    "targets": D / "replication_targets" / "TARGETS_R2.json", "pairs": D / "q2_join" / "q2_pairs_v1.csv",
    "sheet": D / "q2_join" / "q2_coding_sheet_v1.csv", "c1": D / "q2_join" / "q2_codes_coder1_v1.csv",
    "c2": D / "q2_join" / "q2_codes_coder2_v1.csv", "third": D / "q2_join" / "q2_third_reader_v1.csv",
    "recon": D / "q2_join" / "q2_reconciled_v1.csv",
}
DIMS = ["policy_scope_dose", "population", "outcome", "units", "comparison", "clock", "aggregation", "horizon"]
FLIP = ["wherry2016-99815287|proto_medicaid_expansion", "simon2017-1651f1ce|proto_medicaid_expansion",
        "castillocarniglia2021-9d4da005|b1010b_pdmp_mandatory_registration", "mccourt2020-1257d44f|run8_gunshow_background_check",
        "mccourt2020-1257d44f|run8_universal_background_check", "doleac2022-e0686018|r1011_naloxone_access_law",
        "duska2022-9eb9cd62|bulk10_health_naloxone_coprescribing_mandate", "eliason2025-1c2e7a68|b1011tl_chip_waiting_period_elimination",
        "li2018-ea680c66|b1010_parole_abolition"]

problems = []


def jl(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def cl(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def same(label, got, want, tol=1e-12):
    if isinstance(got, float) or isinstance(want, float):
        ok = got is not None and want is not None and abs(got - want) <= tol * max(1.0, abs(want))
        ok = ok or (got is None and want is None)
    else:
        ok = got == want
    print(("OK   " if ok else "DIFF ") + label + ("" if ok else f"  checker={got!r}  results={want!r}"))
    if not ok:
        problems.append(label)


def kappa(a, b):
    n = len(a)
    labs = sorted(set(a) | set(b))
    po = sum(1 for i in range(n) if a[i] == b[i]) / n
    pe = 0.0
    for L in labs:
        pe += (sum(1 for x in a if x == L) / n) * (sum(1 for x in b if x == L) / n)
    return None if abs(1 - pe) < 1e-15 else (po - pe) / (1 - pe)


# ---------------------------------------------------------------------------------------------
# 1. reconciliation, from the raw coder files
# ---------------------------------------------------------------------------------------------
def reconcile_again():
    sheet, c1, c2, third = cl(FILES["sheet"]), cl(FILES["c1"]), cl(FILES["c2"]), cl(FILES["third"])
    core = DIMS[:4]
    passing = ("match", "arith_convertible")
    failing = ("partial", "mismatch")
    out = {}
    t_by = {t["pair_id"]: t for t in third}
    n_route = {"both_yes": 0, "settled_no": 0, "third_reader": 0}
    for i, s in enumerate(sheet):
        pid = s["pair_id"]
        assert c1[i]["pair_id"] == pid == c2[i]["pair_id"], "order"
        a, b = c1[i], c2[i]
        a_yes = all(a[d] in passing for d in core)
        b_yes = all(b[d] in passing for d in core)
        a_fail = [d for d in core if a[d] in failing]
        b_fail = [d for d in core if b[d] in failing]
        if a_yes and b_yes and all(a[d] == b[d] for d in DIMS):
            route = "both_yes"
            cells = {d: a[d] for d in DIMS}
        elif a_fail and b_fail and set(a_fail) & set(b_fail):
            route = "settled_no"
            cells = None
        else:
            route = "third_reader"
            cells = {d: t_by[pid]["tr_" + d] for d in DIMS}
        n_route[route] += 1
        if route == "settled_no":
            cls = "NOT_STRICT"
        elif route == "third_reader" and t_by[pid]["tr_final_class"] == "UNAVAILABLE":
            cls = "UNAVAILABLE"
        else:
            if all(cells[d] in passing for d in core):
                cls = "CORE_ONLY" if any(cells[d] == "mismatch" for d in DIMS[4:]) else "STRICT"
            else:
                cls = "NOT_STRICT"
        out[pid] = (route, cls, cells)
    stats = {"strict_agree": sum(1 for i in range(len(sheet)) if c1[i]["strict_core_yes_no"] == c2[i]["strict_core_yes_no"]),
             "kappa": kappa([r["strict_core_yes_no"] for r in c1], [r["strict_core_yes_no"] for r in c2]),
             "dim_kappa": {d: kappa([r[d] for r in c1], [r[d] for r in c2]) for d in DIMS}}
    return out, n_route, stats


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(D / "q2_v1" / "RESULTS_Q2_V1.json"))
    args = ap.parse_args()
    R = jl(args.results)
    Q = R["question"]["Q2"]

    # hashes recorded in the results equal the files on disk
    st = R["stamp"]
    same("live set sha256", sha(FILES["live"]), st["live_set_sha256"])
    same("first-run table sha256", sha(FILES["v3"]), st["first_run_table_sha256"])
    same("TARGETS_R2 sha256", sha(FILES["targets"]), st["targets_r2_sha256"])
    same("pairs sha256", sha(FILES["pairs"]), st["pairs_sha256"])
    same("reconciled sha256", sha(FILES["recon"]), st["reconciled_sha256"])

    # 1. reconciliation
    rec, n_route, stats = reconcile_again()
    recon_file = {r["pair_id"]: r for r in cl(FILES["recon"])}
    same("reconciled file: pairs", len(recon_file), len(rec))
    bad = [pid for pid, (route, cls, cells) in rec.items() if recon_file[pid]["route"] != route or recon_file[pid]["final_class"] != cls]
    same("reconciled file: route and class of all 179 pairs", bad, [])
    same("route counts", n_route, {"both_yes": 1, "settled_no": 171, "third_reader": 7})
    same("strict-core raw agreement count", stats["strict_agree"], 178)
    same("strict-core kappa", stats["kappa"], 0.6641651031894934, 1e-9)

    # 2. strict-matched designs
    live = jl(FILES["live"])
    v3 = {d["design_id"]: d for d in jl(FILES["v3"])["designs"]}
    targets = {e["design_id"]: e for e in jl(FILES["targets"])["designs"]}
    pairs = {p["pair_id"]: p for p in cl(FILES["pairs"])}
    live_ids = {d["design_id"] for d in live["designs"]}
    # b and the noise SD of archive designs from l2_set_v2.json (independent source)
    v2 = jl(FILES["v2"])
    v2_rows = {}
    for r in v2["rows"]:
        v2_rows.setdefault(r["design_id"], r)

    codes = {}
    per_design = {}
    for d in live["designs"]:
        did = d["design_id"]
        bm = d.get("benchmark") or {}
        entry = targets.get(bm.get("target_id")) if bm.get("target_id") else None
        strict_cands = []
        unavailable = False
        if entry is not None:
            if entry["q2_class"] == "strict_possible":
                for c in entry["candidates"]:
                    if c["rank"] == entry["chosen_candidate_rank"] and c.get("strict_core"):
                        strict_cands.append(("t", c["year"], bool(c.get("peer_reviewed")), c))
            if entry["q2_class"] == "unavailable":
                unavailable = True
        for pid, (route, cls, cells) in rec.items():
            p = pairs.get(pid)
            if p and p["live_design_id"] == did and not p["fo_variant_base_design"]:
                if cls == "STRICT":
                    venue = p["paper_venue"]
                    pr = re.search(r"\d+\(\d+\):\d+", venue) is not None
                    strict_cands.append(("n", int(p["paper_year"]), pr, None))
                if cls == "UNAVAILABLE":
                    unavailable = True
        if strict_cands:
            strict_cands.sort(key=lambda t: (0 if t[2] else 1, t[1], 0 if t[0] == "t" else 1))
            top = strict_cands[0]
            if d["status"] != "estimated":
                codes[did] = f"STRICT_NOT_ESTIMATED:{d['gate']}"
            elif top[0] == "n":
                codes[did] = "STRICT_NO_NUMBER"
            else:
                c = top[3]
                conv = c.get("conversion") or {}
                est = c.get("estimate") or {}
                if isnum(conv.get("converted_point")):
                    val, lab = conv["converted_point"], conv.get("to_units") or est.get("units") or ""
                elif (c.get("match") or {}).get("units") == "match" and isnum(est.get("point")):
                    val, lab = est["point"], est.get("units") or ""
                else:
                    val, lab = None, None
                a = {"+1": 1, "-1": -1}.get(c.get("direction_a_i"))
                v3r = v3.get(did) or {}
                b, s_pre, noise = v3r.get("b"), v3r.get("s_pre"), v3r.get("noise_sd")
                if a is None:
                    codes[did] = "ORIENTATION_MISSING"
                elif val is None:
                    codes[did] = "NO_COMPARABLE_NUMBER"
                elif not (d.get("outcome_unit") and lab and d["outcome_unit"].strip().lower() in lab.lower()):
                    codes[did] = "UNIT_MISMATCH"
                elif not isnum(b):
                    codes[did] = "ESTIMATE_MISSING"
                else:
                    codes[did] = "EVAL" if (isnum(s_pre) and s_pre > 0) else "SCALE_UNDEFINED"
                    per_design[did] = {"a": a, "b_lit": val, "b": b, "s_pre": s_pre, "noise": noise, "reportable": bool(d.get("reportable")),
                                       "family": d.get("policy_family") or did, "group": d.get("source_group")}
        elif unavailable:
            codes[did] = "UNAVAILABLE"
        elif entry is None:
            codes[did] = "NO_TARGETS_ENTRY"
        elif entry["q2_class"] == "none_found":
            codes[did] = "NONE_FOUND"
        else:
            codes[did] = "NON_STRICT"

    tally = {}
    for c in codes.values():
        tally[c] = tally.get(c, 0) + 1
    same("D", len(codes), Q["D"])
    same("coverage codes", dict(sorted(tally.items())), Q["coverage_codes"])
    builder_codes = {r["design_id"]: r["code"] for r in R["designs"]}
    same("per-design codes", {k: v for k, v in codes.items() if builder_codes.get(k) != v}, {})

    # 3. independent b and noise for matched archive designs, G and variants
    for did, x in per_design.items():
        row = next(d for d in live["designs"] if d["design_id"] == did)
        if row["source_group"] in ("prototype", "manual_batch"):
            v2r = v2_rows.get(did) or v2_rows.get(did.replace("prototype:", "proto_"))
            if v2r is None:
                for key, r in v2_rows.items():
                    if r.get("source", {}).get("path") == row["source"]["path"]:
                        v2r = r
                        break
            same(f"b of {did} from l2_set_v2.json equals the first-run table", v2r["b"], x["b"])
            same(f"b of {did} equals the live set point estimate", row["point_estimate"], x["b"])
            nz = v2r.get("noise") or {}
            same(f"noise SD of {did} from l2_set_v2.json equals the first-run table", nz.get("sample_sd"), x["noise"])
    ev = [(did, x) for did, x in per_design.items() if codes[did] == "EVAL"]
    g_list = [x["a"] * (x["b_lit"] - x["b"]) / x["s_pre"] for _, x in ev]
    G = sum(g_list) / len(g_list) if g_list else None
    same("n", len(ev), Q["n"])
    same("F", len({x["family"] for _, x in ev}), Q["F"])
    same("G", G, Q["G"]["value"])
    noise_set = [(did, x) for did, x in per_design.items() if isnum(x["noise"]) and x["noise"] > 0]
    gn = [x["a"] * (x["b_lit"] - x["b"]) / x["noise"] for _, x in noise_set]
    same("G_noise n", len(noise_set), Q["G_noise_variant"]["n"])
    same("G_noise", (sum(gn) / len(gn)) if gn else None, Q["G_noise_variant"]["value"])
    raw = {r["design_id"]: r for r in Q["descriptive_raw_gaps"]}
    same("raw-gap designs", sorted(per_design), sorted(raw))
    for did, x in per_design.items():
        same(f"raw gap of {did}", x["a"] * (x["b_lit"] - x["b"]), raw[did]["raw_gap_a_times_blit_minus_b"])
    same("gate-passing EVAL n", sum(1 for _, x in ev if x["reportable"]), Q["sensitivities"]["gate_passing_EVAL"]["n"])
    same("decision status", "NOT_COMPUTABLE" if not ev else "DESCRIPTIVE_GAP_ONLY", Q["decision"]["status"])
    same("no p-value or interval", (Q["decision"]["p_value"], Q["decision"]["interval"]), (None, None))

    # 4. pair-level counts over the 302 pairs of the join
    counts = {}
    for pid, p in pairs.items():
        if p["known_in_targets"] == "1":
            ec, sc = p["targets_q2_class"], p["targets_strict_core"] == "1"
            cls = "UNAVAILABLE" if ec == "unavailable" else ("STRICT" if (sc and ec == "strict_possible") else ("CORE_ONLY" if (sc and ec == "non_strict") else "NOT_STRICT"))
            key = f"known:{cls}"
        else:
            key = f"new:{rec[pid][1]}"
        counts[key] = counts.get(key, 0) + 1
    same("pair classes (302 pairs)", dict(sorted(counts.items())), Q["pairs"]["known_pairs_and_new_pairs_by_class"])

    # 5. reading flips
    flips = {f["pair_id"]: f for f in Q["sensitivities"]["reading_flips"]["pairs"]}
    for pid in FLIP:
        p = pairs[pid]
        ld = p["live_design_id"]
        row = next((d for d in live["designs"] if d["design_id"] == ld), None)
        r3 = v3.get(ld) or {}
        est = bool(row and row["status"] == "estimated")
        enter = bool(est and isnum(r3.get("b")) and isnum(r3.get("s_pre")) and r3["s_pre"] > 0)
        enter_n = bool(est and isnum(r3.get("b")) and isnum(r3.get("noise_sd")) and r3["noise_sd"] > 0)
        same(f"flip would_enter_G {pid}", enter, flips[pid]["would_enter_G"])
        same(f"flip would_enter_G_noise {pid}", enter_n, flips[pid]["would_enter_G_noise"])

    print()
    if problems:
        print(f"{len(problems)} DIFFERENCES: {problems}")
        return 1
    print("all independent recomputations equal the results file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
