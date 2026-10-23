#!/usr/bin/env python3
"""Independent recomputation of the D3-V1 results in plain Python (does not import build_d3.py).

Written from the build file (docs/plans/l3-meta/PREREG_AMEND_20261015_D3_V1.md, sections 1 to 6) and the entry D3.  Reads the live set
(design ids, statuses, outcome ids, run file and its sha256 only), the RESULT.json of every registered estimated design, the panel files
of the pairs that reach the panel tests, the catalog units, and a results JSON written by build_d3.py.  Re-derives, with explicit loops,
the outcomes of each design, every candidate pair and its code, and for each EVAL pair the numbers a, t, c, the standard deviations,
cutoffs, p, s, mu, tau, Z, p_sub, omega, the flags and the correlation; recomputes the coverage tables and the E1 decision (and E1 itself
when the floor is met).  Prints the largest differences and exits 1 on any disagreement (rule 4.0: |x - y| <= 1e-9 max(1, |x|, |y|)).
Writes nothing.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_d3_independent.py [results.json]
"""
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "data/l3_meta/l2_set_live.json"
CATALOG = ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json"
DEFAULT_RESULTS = ROOT / "data/l3_meta/d3_v1/RESULTS_D3_V1.json"

TABLE = [("P1", "wonder_firearm_homicide_deaths", "wonder_homicide_deaths"),
         ("P2", "wonder_firearm_suicide_deaths", "wonder_suicide_deaths"),
         ("P3", "fars_motor_vehicle_occupant_fatality_rate", "fars_all_road_fatality_rate"),
         ("P4", "fars_drinking_driver_crash_fatality_rate", "fars_all_road_fatality_rate"),
         ("P5", "wonder_opioid_overdose_deaths", "wonder_drug_overdose_deaths"),
         ("P6", "fars_motorcyclist_fatality_rate", "fars_all_road_fatality_rate"),
         ("P7", "fars_pedalcyclist_fatality_rate_all_ages", "fars_all_road_fatality_rate")]
ORDER = ["NEEDS_COMPLEMENT_ESTIMATE", "CELL_SETS_DIFFER", "SETTINGS_DIFFER", "DRAWS_NOT_ALIGNED", "DENOMINATORS_DIFFER", "NOT_NESTED", "OTHER", "SUPPORT"]
DESIGN_ORDER = ["CELL_SETS_DIFFER", "DRAWS_NOT_ALIGNED", "SETTINGS_DIFFER", "DENOMINATORS_DIFFER", "NOT_NESTED", "OTHER", "SUPPORT", "NEEDS_COMPLEMENT_ESTIMATE"]

worst = {}
problems = []


def agree(x, y):
    return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))


def compare(label, got, want):
    """Record the difference between the results file (got) and this recomputation (want)."""
    if got is None or want is None:
        if got is not want:
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, bool) or isinstance(got, bool):
        if bool(got) != bool(want):
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    d = abs(got - want)
    key = label.split(":")[-1]
    worst[key] = max(worst.get(key, 0.0), d)
    if not agree(got, want):
        problems.append(f"{label}: {got!r} vs {want!r} (difference {d:.3g})")


def sign(v):
    return 1 if v > 0 else (-1 if v < 0 else 0)


def feasible(nf):
    f = nf["feasibility"]
    skip = set(x["draw_index"] for x in f["infeasible_draws"])
    return [i for i in range(f["requested_draws"]) if i not in skip]


def stats_of(x, draws):
    n = len(draws)
    srt = sorted(abs(v) for v in draws)
    cut = srt[math.ceil(0.95 * n) - 1]
    cnt = 0
    for v in draws:
        if abs(v) >= abs(x):
            cnt += 1
    return statistics.stdev(draws), cut, (1 + cnt) / (n + 1), abs(x) > cut


def read_panel(path):
    """(unit, year) -> (outcome_value or None, denominator or None) with csv.reader and column positions."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        head = next(rd)
        iu, iy, iv, idn = head.index("unit"), head.index("year"), head.index("outcome_value"), head.index("population_denominator")
        for row in rd:
            def num(i):
                try:
                    return float(row[i]) if row[i] != "" else None
                except ValueError:
                    return None
            out[(row[iu], int(row[iy]))] = (num(iv), num(idn))
    return out


def find_panel(folder, name, series):
    for cand in (folder / "panels" / name / "panel.csv", folder / "secondary_panels" / series / "panel.csv"):
        if cand.exists():
            return cand
    return None


def derive_pair(folder, outs, table_row, catalog):
    """Returns (code, numbers or None) for one candidate pair, following build file 3.2 and section 4 with explicit loops."""
    pid, a_id, t_id = table_row
    a_name, A = outs[a_id]
    if t_id not in outs:
        return "NEEDS_COMPLEMENT_ESTIMATE", None
    t_name, T = outs[t_id]
    fails = []
    gt_a, gt_t = A["result"]["group_time"], T["result"]["group_time"]
    sig = lambda gt: [(c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], c["post"]) for c in gt]
    if (sig(gt_a) != sig(gt_t) or A["inventory"]["cells"] != T["inventory"]["cells"] or A["inventory"]["construction_cells"] != T["inventory"]["construction_cells"]
            or A["result"]["overall"]["n_cells"] != T["result"]["overall"]["n_cells"]):
        fails.append("CELL_SETS_DIFFER")
    if A["result"]["settings"] != T["result"]["settings"]:
        fails.append("SETTINGS_DIFFER")
    fa, ft = feasible(A["noise_floor"]), feasible(T["noise_floor"])
    da = [float(v) for v in A["noise_floor"]["permutation_draws"]]
    dt = [float(v) for v in T["noise_floor"]["permutation_draws"]]
    if fa != ft or len(da) != len(fa) or len(dt) != len(ft) or len(fa) < 900:
        fails.append("DRAWS_NOT_ALIGNED")
    if fails:
        return sorted(fails, key=ORDER.index)[0], None
    pa_path, pt_path = find_panel(folder, a_name, a_id), find_panel(folder, t_name, t_id)
    if pa_path is None or pt_path is None:
        return "OTHER", None
    pa, pt = read_panel(pa_path), read_panel(pt_path)
    for k in set(pa) & set(pt):
        if pa[k][1] is not None and pt[k][1] is not None and not agree(pa[k][1], pt[k][1]):
            fails.append("DENOMINATORS_DIFFER")
            break
    ua, ut = catalog[a_id]["units"], catalog[t_id]["units"]
    nested_bad = ua != ut
    for k in set(pa) & set(pt):
        if pa[k][0] is not None and pt[k][0] is not None and pt[k][0] < pa[k][0] - 1e-9 * max(1.0, abs(pa[k][0])):
            nested_bad = True
            break
    if nested_bad:
        fails.append("NOT_NESTED")
    a, t = A["result"]["overall"]["att"], T["result"]["overall"]["att"]
    c = t - a
    # weighted post cells
    mass = sum(x["n_treated"] for x in gt_a if x["post"])
    wa = sum(x["n_treated"] * x["att"] for x in gt_a if x["post"]) / mass
    wt = sum(x["n_treated"] * x["att"] for x in gt_t if x["post"]) / mass
    wc = sum(x["n_treated"] * (y["att"] - x["att"]) for x, y in zip(gt_a, gt_t) if x["post"]) / mass
    if not (agree(wa, a) and agree(wt, t) and agree(wc, c)):
        fails.append("OTHER")
    dc = [y - x for x, y in zip(da, dt)]
    sd_a, cut_a, p2_a, _ = stats_of(a, da)
    sd_t, cut_t, p2_t, _ = stats_of(t, dt)
    for ent, sd, cut, p2 in ((A, sd_a, cut_a, p2_a), (T, sd_t, cut_t, p2_t)):
        nf = ent["noise_floor"]
        if not (agree(nf["sample_sd"], sd) and agree(nf["absolute_cutoff"], cut) and agree(nf["p_two_sided"], p2)):
            fails.append("OTHER")
    sd_c, cut_c, p2_c, beyond_c = stats_of(c, dc)
    if fails:
        return sorted(fails, key=ORDER.index)[0], None
    if sd_c == 0:
        return "SUPPORT", None
    s = -sign(a) * c / sd_c
    star = [-sign(x) * y / sd_c for x, y in zip(da, dc)]
    mu = sum(star) / len(star)
    tau = statistics.stdev(star)
    if tau == 0:
        return "SUPPORT", None
    n = len(star)
    beyond_a = abs(a) > cut_a
    opposite = (a != 0 and c != 0 and sign(c) == -sign(a))
    mx, my = sum(da) / n, sum(dt) / n
    corr = sum((x - mx) * (y - my) for x, y in zip(da, dt)) / math.sqrt(sum((x - mx) ** 2 for x in da) * sum((y - my) ** 2 for y in dt))
    return "EVAL", {"n_draws": n, "a": a, "t": t, "c": c, "sd_a": sd_a, "sd_t": sd_t, "sd_c": sd_c, "cutoff_a": cut_a, "cutoff_t": cut_t,
                    "cutoff_c": cut_c, "p2_a": p2_a, "p2_t": p2_t, "p2_c": p2_c, "beyond_a": beyond_a, "beyond_c": beyond_c,
                    "s": s, "mu": mu, "tau": tau, "Z": (s - mu) / tau, "p_sub": (1 + sum(1 for v in star if v >= s)) / (n + 1),
                    "omega": (-c / a) if a != 0 else None, "net_ratio": (t / a) if a != 0 else None, "opposite": opposite,
                    "signal": bool(beyond_a and opposite and beyond_c), "corr_a_t_draws": corr}


def e1(y, fam, seed, B=9999):
    import numpy as np
    fams = sorted(set(fam))
    F = len(fams)
    members = {f: [i for i, x in enumerate(fam) if x == f] for f in fams}
    n = len(y)
    theta = sum(y) / n
    se = math.sqrt(F / (F - 1) * sum(sum(y[i] - theta for i in members[f]) ** 2 for f in fams)) / n
    t = theta / se
    tiny = 1e-9 * max(abs(v) for v in y)
    draw = np.random.default_rng(seed).integers(0, F, size=(B, F))
    ts = []
    for row in draw:
        idx = [i for k in row for i in members[fams[k]]]
        th = sum(y[i] for i in idx) / len(idx)
        ss = sum(sum(y[i] - th for i in members[fams[k]]) ** 2 for k in row)
        seb = math.sqrt(F / (F - 1) * ss) / len(idx)
        ts.append((th - theta) / seb if seb > tiny else 0.0)
    a = [abs(v) for v in ts]
    c = float(np.percentile(a, 95))
    p = (1 + sum(1 for v in a if v >= abs(t))) / (B + 1)
    return theta, se, t, p, theta - c * se, theta + c * se


def main(path):
    res = json.load(open(path))
    live = json.load(open(LIVE))
    catalog = {k: {"units": v.get("units")} for k, v in json.load(open(CATALOG))["series"].items()}
    got_pairs = {(p["design_id"], p["pair"]): p for p in res["pairs"]}
    got_designs = {r["design_id"]: r for r in res["designs"]}
    mine_pairs, design_codes, mine_needs = {}, {}, set()
    for d in live["designs"]:
        did = d["design_id"]
        grp = d["source_group"]
        if d["status"] != "estimated":
            design_codes[did] = "STOP_PRE_RUN"
            continue
        if grp in ("prototype", "manual_batch"):
            design_codes[did] = "FIELD_MISSING"
            continue
        if grp not in ("registry_terminal", "autopilot_estimate"):
            design_codes[did] = "OTHER"
            continue
        src = ROOT / d["source"]["path"]
        result = json.load(open(src))
        outs = {}
        prim = [p["outcome_id"] for p in d["primary_outcomes"]]
        for name, ent in result["registered_results"].items():
            if name == "primary":
                sid = prim[0] if prim else None
            elif name.startswith("secondary_"):
                sid = name[len("secondary_"):]
            else:
                continue
            if sid and ent.get("status") == "ESTIMATED" and isinstance(ent.get("noise_floor", {}).get("permutation_draws"), list):
                outs[sid] = (name, ent)
        codes = []
        for row in TABLE:
            if row[1] in outs:
                code, nums = derive_pair(src.parent, outs, row, catalog)
                mine_pairs[(did, row[0])] = (code, nums)
                codes.append(code)
                if code in ("NEEDS_COMPLEMENT_ESTIMATE", "CELL_SETS_DIFFER"):
                    inv = outs[row[1]][1]["inventory"]
                    tstate = ("registered on another mask: cells=%s" % outs[row[2]][1]["inventory"]["cells"]) if row[2] in outs else None
                    if tstate is None:
                        other = result["registered_results"].get("secondary_" + row[2])
                        tstate = ("registered but not estimated (status %s)" % other.get("status")) if isinstance(other, dict) else "not registered"
                    mine_needs.add((did, row[0], outs[row[1]][0], row[1], row[2], str(inv["cells"]), str(inv["construction_cells"]), inv["metadata_sha256"], tstate))
        if not codes:
            design_codes[did] = "NO_PAIR"
        elif "EVAL" in codes:
            design_codes[did] = "EVAL"
        else:
            design_codes[did] = next(c for c in DESIGN_ORDER if c in codes)
    # 1. same pairs, same codes
    if set(mine_pairs) != set(got_pairs):
        problems.append(f"candidate pairs differ: only mine {sorted(set(mine_pairs) - set(got_pairs))}, only results {sorted(set(got_pairs) - set(mine_pairs))}")
    for k in sorted(set(mine_pairs) & set(got_pairs)):
        if mine_pairs[k][0] != got_pairs[k]["code"]:
            problems.append(f"pair {k}: code {got_pairs[k]['code']} vs {mine_pairs[k][0]}")
    # 2. design codes
    for did, code in design_codes.items():
        if got_designs[did]["code"] != code:
            problems.append(f"design {did}: code {got_designs[did]['code']} vs {code}")
    # 3. numbers of EVAL pairs
    n_eval = 0
    for k, (code, nums) in sorted(mine_pairs.items()):
        if code != "EVAL":
            continue
        n_eval += 1
        got = got_pairs[k]["numbers"]
        for field, want in nums.items():
            compare(f"{k[0]}/{k[1]}:{field}", got.get(field), want)
    # 4. tables and E1
    from collections import Counter
    if res["question"]["D3"]["coverage_codes_over_D"] != dict(sorted(Counter(design_codes.values()).items())):
        problems.append("coverage table over D differs")
    pair_counter = dict(sorted(Counter(c for c, _ in mine_pairs.values()).items()))
    if res["question"]["D3"]["pair_codes"] != pair_counter:
        problems.append("pair code table differs")
    evals = [(k, nums) for k, (code, nums) in sorted(mine_pairs.items()) if code == "EVAL"]
    fam_of = {d["design_id"]: (d.get("policy_family") or d["design_id"]) for d in live["designs"]}
    fams = [fam_of[k[0]] for k, _ in evals]
    e1_res = res["question"]["D3"]["E1"]
    should_run = len(evals) >= 8 and len(set(fams)) >= 5
    if bool(e1_res["run"]) != should_run:
        problems.append(f"E1 run flag {e1_res['run']} vs {should_run}")
    if should_run and e1_res["run"]:
        theta, se, t, p, lo, hi = e1([n["Z"] for _, n in evals], fams, [20261011, 33])
        for label, got, want in (("theta", e1_res["result"]["theta"], theta), ("se", e1_res["result"]["se"], se), ("t", e1_res["result"]["t"], t),
                                 ("p", e1_res["result"]["p_two_sided"], p), ("lo", e1_res["result"]["interval95"][0], lo), ("hi", e1_res["result"]["interval95"][1], hi)):
            compare("E1:" + label, got, want)
    # 4b. the list of engine runs needed
    csv_path = Path(path).parent / "NEEDS_ENGINE_RUNS.csv"
    with open(csv_path, newline="", encoding="utf-8") as fh:
        got_needs = {(r["design_id"], r["pair"], r["target_entry"], r["target_series"], r["total_series"], r["mask_cells"], r["mask_construction_cells"],
                      r["mask_metadata_sha256"], r["state_of_total"]) for r in csv.DictReader(fh)}
    if got_needs != mine_needs:
        problems.append(f"NEEDS_ENGINE_RUNS.csv differs: only csv {sorted(got_needs - mine_needs)}, only recomputed {sorted(mine_needs - got_needs)}")
    # 5. pair_inputs file against the source files
    pi_path = Path(path).parent / "pair_inputs_d3_v1.json"
    if evals:
        pin = json.load(open(pi_path))
        for rec in pin["pairs"]:
            d = next(x for x in live["designs"] if x["design_id"] == rec["design_id"])
            src = json.load(open(ROOT / d["source"]["path"]))["registered_results"]
            for side in ("target", "total"):
                ent = src[rec[side]["entry"]]
                compare(f"{rec['design_id']}:pair_inputs_{side}_att", rec[side]["overall_att"], ent["result"]["overall"]["att"])
                if rec[side]["permutation_draws"] != ent["noise_floor"]["permutation_draws"]:
                    problems.append(f"pair_inputs {rec['design_id']} {side} draws differ from the source")
    print(json.dumps({"results_file": str(path), "designs_checked": len(design_codes), "candidate_pairs": len(mine_pairs), "eval_pairs": n_eval,
                      "engine_runs_needed_rows_checked": len(mine_needs),
                      "design_codes": dict(sorted(Counter(design_codes.values()).items())), "pair_codes": pair_counter,
                      "largest_differences": {k: float(f"{v:.3g}") for k, v in sorted(worst.items())}, "problems": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RESULTS))
