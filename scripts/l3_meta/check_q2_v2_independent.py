#!/usr/bin/env python3
"""Independent recomputation of Q2-V2 and of the L3 read S_PRE_L3_V1 (plain loops, standard library only, NO import from build_s_pre_l3.py,
build_q2.py or build_q2_v2.py).

Re-derives from the raw files: (1) rule P on the 7 designs that export s_pre (clean set from SUPPORT.json and the primary panel, a two-pass SD
written out by hand, the L2 export read from the bytes of L3_EXPORT_DATA.json), the applicability and the validation gate; (2) rule A for the two
prototypes from their panel and treatment files (no use of the builder's structural constants), with the S1 variant; (3) the matched
designs' gap pieces from TARGETS_R2.json (chosen candidate, direction, published number), the live set (estimate b, outcome unit, status) and
the first-run table (noise SD): raw gaps, g, G, the S1 variant, the noise-scale variant; (4) the coverage code of every design of D against
the Q2-V1 table (only the two scale-undefined prototypes may change); (5) n, F, D and the decision fields.
Then compares with data/l3_meta/s_pre_l3_v1/S_PRE_L3_V1.json and data/l3_meta/q2_v2/RESULTS_Q2_V2.json.

Run:  python3 -I -B scripts/l3_meta/check_q2_v2_independent.py [--results PATH] [--scale PATH]      (exit 0 = all equal)
"""
import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "l3_meta"
WORK = ROOT / "data/l2_pipeline/autopilot/c72/work"
EXPORTERS = [("b1010_handheld_ban_all", "v6"), ("b1010_permitless_carry", "v5"), ("b1010_primary_seatbelt", "v5"),
             ("b1010b_child_restraint_booster_age5", "v6"), ("b1010b_pdmp_mandatory_query", "v7"), ("b1010b_zero_tolerance_under21", "v8"),
             ("r1011_speed_limit_65_rural_interstate_1987", "v2")]

problems = []


def jl(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def cl(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def same(label, got, want, tol=1e-12):
    if isinstance(got, float) or isinstance(want, float):
        ok = (got is None and want is None) or (got is not None and want is not None and abs(got - want) <= tol * max(1.0, abs(want)))
    else:
        ok = got == want
    print(("OK   " if ok else "DIFF ") + label + ("" if ok else f"  checker={got!r}  results={want!r}"))
    if not ok:
        problems.append(label)


def sd_two_pass(xs):
    n = len(xs)
    m = sum(xs) / n
    acc = 0.0
    for x in xs:
        acc += (x - m) * (x - m)
    return math.sqrt(acc / (n - 1))


# ---------------------------------------------------------------------------------------------
# 1. rule P on the exporting designs
# ---------------------------------------------------------------------------------------------
def exported(path):
    data = open(path, "rb").read()
    vals = set(re.findall(rb'"s_pre":\s*(-?[0-9][0-9.eE+\-]*)', data))
    ns = set(re.findall(rb'"s_pre_n":\s*([0-9]+)', data))
    assert len(vals) == 1 and len(ns) == 1
    return float(vals.pop()), int(ns.pop())


def rule_p_again():
    out = {}
    for did, ver in EXPORTERS:
        pkg = WORK / did / ver
        var = jl(pkg / "SUPPORT.json")["variants"][0]
        cohort = {}
        for cell in var["cells"]:
            for u in cell["treated"]:
                cohort[u] = cell["cohort"]
        panel = {}
        for r in cl(pkg / "panels/primary/panel.csv"):
            panel[(r["unit"], int(r["period"]))] = float(r["outcome_value"])
        vals = []
        for pair in var["analysis_mask"]:
            u, t = pair[0], int(pair[1])
            if (u not in cohort) or (t < cohort[u]):
                vals.append(panel[(u, t)])
        e_sd, e_n = exported(pkg / "L3_EXPORT_DATA.json")
        out[did] = {"group": var["comparison_group"], "variant": var["variant"], "n": len(vals), "sd": sd_two_pass(vals), "e_n": e_n, "e_sd": e_sd}
    return out


# ---------------------------------------------------------------------------------------------
# 2. rule A on the two prototypes
# ---------------------------------------------------------------------------------------------
def cannabis_again():
    base = ROOT / "data/did_samples/adult_use_cannabis"
    eff_year = {}
    for r in cl(base / "treatment_table_normalized.csv"):
        eff_year[r["abbr"]] = int(r["effective_date"][:4]) if r["effective_date"] else None
    clean, s1 = [], []
    for r in cl(base / "panel.csv"):
        t = int(r["period"])
        if r["unit"] == "CO" or t < 2000 or t > 2019:
            continue
        share = float(r["law_share_of_year"]) if r["law_share_of_year"] != "" else 0.0
        # cross-check of the exposure rule against the effective-date year (independent of law_share)
        e = eff_year[r["unit"]]
        exposed_by_date = e is not None and t >= e
        assert exposed_by_date == (share > 0), (r["unit"], t)
        if share == 0:
            clean.append(float(r["y"]))
        ft = int(r["first_treated"]) if r["first_treated"] else 10 ** 9
        if t < ft:
            s1.append(float(r["y"]))
    return {"n": len(clean), "sd": sd_two_pass(clean), "n1": len(s1), "sd1": sd_two_pass(s1)}


def castle_again():
    base = ROOT / "data/did_samples/castle_doctrine"
    adopt = {}
    for r in cl(base / "treatment_table.csv"):
        adopt[r["state_abbr"]] = int(r["adoption_year"]) if r["group"] == "treated" else None
    clean, s1 = [], []
    for r in cl(base / "panel.csv"):
        t = int(r["year"])
        if t < 2000 or t > 2010:
            continue
        a = adopt[r["state_abbr"]]
        if a is None or t < a:
            clean.append(float(r["y"]))
        ft = int(r["first_treated"]) if r["first_treated"] else 10 ** 9
        if t < ft:
            s1.append(float(r["y"]))
    return {"n": len(clean), "sd": sd_two_pass(clean), "n1": len(s1), "sd1": sd_two_pass(s1)}


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(D / "q2_v2" / "RESULTS_Q2_V2.json"))
    ap.add_argument("--scale", default=str(D / "s_pre_l3_v1" / "S_PRE_L3_V1.json"))
    args = ap.parse_args()
    res = jl(args.results)
    scale = jl(args.scale)
    q = res["question"]["Q2"]
    v1 = jl(D / "q2_v1" / "RESULTS_Q2_V1.json")

    # ---- 1. validation
    P = rule_p_again()
    sv = {r["design_id"]: r for r in scale["validation"]["designs"]}
    app = [d for d in P if P[d]["group"] == "never_treated" and P[d]["variant"] == "primary"]
    ok_all = True
    for did, x in P.items():
        row = sv[did]
        same(f"validation applicable {did}", did in app, row["applicable"])
        same(f"export n {did}", x["e_n"], row["n_export"])
        same(f"export s_pre {did}", x["e_sd"], row["sd_export_file"], tol=0)
        if did in app:
            same(f"rule P n {did}", x["n"], row["n_L3"])
            same(f"rule P sd {did}", x["sd"], row["sd_L3"], tol=1e-12)
            rd = abs(x["sd"] - x["e_sd"]) / x["e_sd"]
            ok_all = ok_all and x["n"] == x["e_n"] and rd <= 1e-9
            same(f"reproduces the export {did}", x["n"] == x["e_n"] and rd <= 1e-9, True)
        else:
            info = row.get("informational_not_gating") or {}
            same(f"informational n {did}", x["n"], info.get("n_L3"))
            same(f"informational sd {did}", x["sd"], info.get("sd_L3"), tol=1e-12)
    same("validation gate", bool(app) and ok_all, scale["validation"]["gate"]["gate_passed"])
    same("applicable designs", len(app), scale["validation"]["gate"]["applicable_n"])

    # ---- 2. prototypes
    reads = {r["design_id"]: r for r in scale["reads"]}
    for did, x in (("prototype:adult_use_cannabis", cannabis_again()), ("prototype:castle_doctrine", castle_again())):
        r = reads[did]
        same(f"{did} n", x["n"], r["n"])
        same(f"{did} s_pre_L3", x["sd"], r["s_pre_L3"], tol=1e-12)
        same(f"{did} S1 n", x["n1"], r["sensitivity_S1"]["n"])
        same(f"{did} S1 s_pre", x["sd1"], r["sensitivity_S1"]["s_pre_L3"], tol=1e-12)
        same(f"{did} used in Q2", True, r["used_in_q2"])
        r["_chk"] = x

    # ---- 3. gap pieces from the raw sources
    live = {d["design_id"]: d for d in jl(D / "l2_set_live.json")["designs"]}
    targets = {e["design_id"]: e for e in jl(D / "replication_targets" / "TARGETS_R2.json")["designs"]}
    v3 = {d["design_id"]: d for d in jl(D / "ex1_v3" / "RESULTS_EX1_V3.json")["designs"]}
    gaps, gaps1, gnoise = [], [], []
    per = {}
    for did in ("prototype:adult_use_cannabis", "prototype:castle_doctrine"):
        L = live[did]
        e = targets[L["benchmark"]["target_id"]]
        ch = [c for c in e["candidates"] if c["rank"] == e["chosen_candidate_rank"]][0]
        a = {"+1": 1, "-1": -1}[ch["direction_a_i"]]
        b_lit = ch["estimate"]["point"]
        b = L["point_estimate"]
        raw = a * (b_lit - b)
        s = reads[did]["_chk"]["sd"]
        s1 = reads[did]["_chk"]["sd1"]
        per[did] = {"raw": raw, "g": raw / s, "g1": raw / s1, "noise": v3[did].get("noise_sd")}
        gaps.append(raw / s)
        gaps1.append(raw / s1)
        if isnum(per[did]["noise"]) and per[did]["noise"] > 0:
            gnoise.append(raw / per[did]["noise"])
    G = sum(gaps) / len(gaps)
    same("n", 2, q["n"])
    same("F", 2, q["F"])
    same("D", len(live), q["D"])
    same("G", G, q["G"]["value"])
    same("G families", sorted(["adult_use_cannabis", "castle_doctrine"]), sorted(q["G"]["families"]))
    for e in q["G"]["per_design"]:
        same(f"g {e['design_id']}", per[e["design_id"]]["g"], e["g"])
        same(f"scale source {e['design_id']}", "l3_read", e["scale_source"])
    same("G S1 variant", sum(gaps1) / len(gaps1), q["G_S1_scale_variant"]["value"])
    same("G noise-scale variant", sum(gnoise) / len(gnoise) if gnoise else None, q["G_noise_variant"]["value"])
    same("G noise-scale n", len(gnoise), q["G_noise_variant"]["n"])
    same("G (L2 export only) n", 0, q["G_l2_export_only"]["n"])
    same("G (L2 export only) value", None, q["G_l2_export_only"]["value"])
    for g in q["descriptive_raw_gaps"]:
        same(f"raw gap {g['design_id']}", per[g["design_id"]]["raw"], g["raw_gap_a_times_blit_minus_b"])
    # sign of g against the sign of the raw gap (the scale is positive)
    same("sign of every g equals the sign of its raw gap", all((per[d]["g"] < 0) == (per[d]["raw"] < 0) for d in per), True)

    # ---- 4. coverage codes against Q2-V1
    codes_v1 = {d["design_id"]: d["code"] for d in v1["designs"]}
    codes_v2 = {d["design_id"]: d["code"] for d in res["designs"]}
    changed = sorted(k for k in codes_v2 if codes_v1.get(k) != codes_v2[k])
    same("designs whose code changed", changed, ["prototype:adult_use_cannabis", "prototype:castle_doctrine"])
    same("code of the changed designs", sorted(set(codes_v2[k] for k in changed)), ["EVAL"])
    same("their V1 code", sorted(set(codes_v1[k] for k in changed)), ["SCALE_UNDEFINED"])
    same("design count equal V1", len(codes_v1), len(codes_v2))
    exp_counts = dict(v1["question"]["Q2"]["coverage_codes"])
    exp_counts["SCALE_UNDEFINED"] -= 2
    if exp_counts["SCALE_UNDEFINED"] == 0:
        del exp_counts["SCALE_UNDEFINED"]
    exp_counts["EVAL"] = 2
    same("coverage code table", dict(sorted(exp_counts.items())), dict(sorted(q["coverage_codes"].items())))

    # ---- 5. decision and sensitivities
    same("decision status", "DESCRIPTIVE_GAP_ONLY", q["decision"]["status"])
    same("no interval, p-value or sentence", [None, None, None], [q["decision"]["interval"], q["decision"]["p_value"], q["decision"]["sentence"]])
    same("families below the minimum", False, q["decision"]["families_ge_min"])
    same("gate-passing sensitivity n", sum(1 for d in per if live[d]["reportable"]), q["sensitivities"]["gate_passing_EVAL"]["n"])
    same("core-only estimated with a scale", 2, q["sensitivities"]["core_only_rule"]["estimated_with_s_pre"])
    same("reading flips any_would_enter_G (unchanged from V1)", v1["question"]["Q2"]["sensitivities"]["reading_flips"]["any_would_enter_G"],
         q["sensitivities"]["reading_flips"]["any_would_enter_G"])

    # ---- 6. stamp
    import hashlib
    h = hashlib.sha256(open(ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2.md", "rb").read()).hexdigest()
    same("amendment sha256 in the stamp", h, res["stamp"]["amendment_sha256"])
    same("amendment sha256 in the scale file", h, scale["stamp"]["amendment_sha256"])
    same("scale file sha256 in the stamp", hashlib.sha256(open(args.scale, "rb").read()).hexdigest(), res["stamp"]["scale_file_sha256"])

    print()
    if problems:
        print(f"{len(problems)} DIFFERENCES: " + "; ".join(problems))
        return 1
    print("all checks equal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
