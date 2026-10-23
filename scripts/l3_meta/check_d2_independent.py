#!/usr/bin/env python3
"""Independent recomputation of the D2-V1 results in plain Python (imports nothing from build_d2.py, reconcile_d2.py, build_d1.py or any other builder).

Written from the amendment PREREG_AMEND_20261015_D2_V1.md (sections 1 to 6), the entry D2 and the D1-V1 rules (PREREG_AMEND_20261015_SWEEP_V1.md section 6).
Reads the two coder files and the third reader's file (rebuilds the final codes, the negative-control pairs and the strict set itself), the inventory CSV
(policy group of a design, registered outcome families), the live set (status, source group, policy family), SWEEP_RUN.json, GATE_REPORT.json and the sweep
files <design>.npz, and a results JSON written by build_d2.py.  Re-derives with explicit loops the family statistics M_if, q_if, beyond_if, r_if of every D1-EVAL
design, the cell codes, the estimate (pi_NC, rbar, delta_D2), E1 (explicit bootstrap loop), the variants of the NC set, the width of the empirical null (the same
random numbers), the series-level share, the rates, the coding-error list and the design coverage; with --d1 it also compares the family statistics with
RESULTS_D1_V1.json (the consistency check of amendment 7.3, made after the first D2 run).  Prints the largest differences and exits 1 on any disagreement
(1e-9 relative: |x - y| <= 1e-9 max(1, |x|, |y|)).  Writes nothing unless --out is given (a new file).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_d2_independent.py [--results FILE] [--d1 FILE] [--out FILE]   (numpy only loads npz files and draws the random numbers)
"""
import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
D2DIR = ROOT / "data/l3_meta/d2_plausibility"
LIVE = ROOT / "data/l3_meta/l2_set_live.json"
INVENTORY = ROOT / "docs/plans/l3-meta/OUTCOME_DIMENSION_INVENTORY_20261015.csv"
SWEEP = ROOT / "data/l3_meta/sweep_v1"
DEFAULT_RESULTS = ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"
ARCHIVE_GROUPS = ("prototype", "manual_batch")
SEED_E1, SEED_NULL, B_BOOT, N_REF = [20261011, 32], [20261011, 32, 7], 9999, 9999
VARIANTS = ["final", "strict", "coder1", "coder2", "either", "final_narrow_exclusion", "final_no_exclusion"]

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
    if isinstance(want, str) or isinstance(got, str):
        if got != want:
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    d = abs(got - want)
    key = label.split(":")[-1].split(".")[-1]
    worst[key] = max(worst.get(key, 0.0), d / max(1.0, abs(got), abs(want)))
    if not agree(got, want):
        problems.append(f"{label}: {got!r} vs {want!r} (difference {d:.3g})")


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------------------------
# the coding: final codes, NC pairs, strict set
# ---------------------------------------------------------------------------------------------
def coding():
    c1 = {(r["policy_group"], r["outcome_family"]): r["code"] for r in load_csv(D2DIR / "d2_codes_coder1_v1.csv")}
    c2 = {(r["policy_group"], r["outcome_family"]): r["code"] for r in load_csv(D2DIR / "d2_codes_coder2_v1.csv")}
    third = {(r["policy_group"], r["outcome_family"]): r["third_code"] for r in load_csv(D2DIR / "d2_codes_third_v1.csv")}
    assert set(c1) == set(c2) and len(c1) == 560
    differ = {k for k in c1 if c1[k] != c2[k]}
    assert set(third) == differ, "third reader file does not cover exactly the differing pairs"
    pairs = {}
    for k in c1:
        final = c1[k] if c1[k] == c2[k] else third[k]
        pairs[k] = dict(final=final, c1=c1[k], c2=c2[k], strict=(c1[k] == c2[k] == "no_channel"))
    # the reconciled table written by reconcile_d2.py must say the same
    for r in load_csv(D2DIR / "d2_reconciled_v1.csv"):
        k = (r["policy_group"], r["outcome_family"])
        if r["final_code"] != pairs[k]["final"] or r["nc"] != str(int(pairs[k]["final"] == "no_channel")) or r["strict_nc"] != str(int(pairs[k]["strict"])):
            problems.append(f"reconciled table differs for pair {k}")
    return pairs


def pair_in(variant, p):
    if variant in ("final", "final_narrow_exclusion", "final_no_exclusion"):
        return p["final"] == "no_channel"
    if variant == "strict":
        return p["strict"]
    if variant == "coder1":
        return p["c1"] == "no_channel"
    if variant == "coder2":
        return p["c2"] == "no_channel"
    return p["c1"] == "no_channel" or p["c2"] == "no_channel"


# ---------------------------------------------------------------------------------------------
# inventory and live set
# ---------------------------------------------------------------------------------------------
def inventory():
    rows = load_csv(INVENTORY)
    group = {}
    for r in rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design":
            group.setdefault(r["unit_id"], set()).add(r["policy_group"])
    assert all(len(v) == 1 for v in group.values())
    group = {k: (next(iter(v)) or None) for k, v in group.items()}
    fam_of_series = {r["outcome_key"]: r["family"] for r in rows
                     if r["row_type"] == "variable" and r["layer"] == "connector_catalog" and r["data_state"] in ("on_disk_parsed", "on_disk_exports")}
    reg = []
    for r in rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design" and r["role"] in ("primary", "secondary"):
            fam = r["family"] or fam_of_series.get(r["outcome_key"])
            if fam:
                reg.append((r["unit_id"], r["policy_group"], r["outcome_key"], r["role"], fam))
    by_group, by_design = {}, {}
    for did, g, key, role, fam in reg:
        by_group.setdefault(g, {}).setdefault(fam, []).append((did, key, role))
        by_design.setdefault(did, set()).add(fam)
    return group, by_group, by_design


# ---------------------------------------------------------------------------------------------
# D1-V1 family statistics in plain loops
# ---------------------------------------------------------------------------------------------
def mean_(v):
    return sum(v) / len(v)


def sd_(v):
    m = mean_(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def plain_design(sids, fam, code, att, placebo):
    """Section 6.2 and 6.3 of the sweep amendment with plain loops.  None when the design is not EVAL (D1 code SUPPORT)."""
    n_all = len(placebo[0])
    A = [i for i in range(len(sids)) if code[i] == "SUPPORTED"]
    for i in list(A):
        vals = [v for v in placebo[i] if not math.isnan(v)]
        if len(vals) < 2 or not (sd_(vals) > 0):
            A.remove(i)

    def common(idx):
        return [b for b in range(n_all) if all(not math.isnan(placebo[i][b]) for i in idx)]

    C = common(A)
    while A and len(C) < 900 and len({fam[i] for i in A}) >= 5:
        count = {i: sum(1 for v in placebo[i] if not math.isnan(v)) for i in A}
        fewest = min(count.values())
        drop = max((i for i in A if count[i] == fewest), key=lambda i: sids[i])
        A.remove(drop)
        C = common(A)
    families = sorted({fam[i] for i in A})
    if len(families) < 5 or len(C) < 900:
        return None
    n = len(C)
    sd = {i: sd_([placebo[i][b] for b in C]) for i in A}
    if any(not (v > 0) for v in sd.values()):
        return None
    rank = math.ceil(0.95 * n) - 1
    nstar = [0] * n
    fam_out = {}
    for f in families:
        mem = [i for i in A if fam[i] == f]
        absz = [abs(att[i] / sd[i]) for i in mem]
        M = max(absz)
        top = mem[absz.index(M)]
        Ms = [max(abs(placebo[i][b] / sd[i]) for i in mem) for b in C]
        q = sorted(Ms)[rank]
        for j in range(n):
            nstar[j] += int(Ms[j] > q)
        ties = [sids[i] for i, a in zip(mem, absz) if a >= M * (1 - 1e-9)]             # series tied for the largest |z| (identical series give equal values up to rounding)
        fam_out[f] = dict(M=M, q=q, beyond=bool(M > q), r=sum(1 for v in Ms if v > q) / n, n_series=len(mem), top_series=sids[top], top_z=att[top] / sd[top], ties=ties)
    mu = sum(nstar) / n
    tau = math.sqrt(sum((v - mu) ** 2 for v in nstar) / (n - 1))
    if not tau > 0:
        return None
    series = {}
    for i in A:
        row = [placebo[i][b] for b in C]
        cut = sorted(abs(v) for v in row)[rank]
        series[sids[i]] = dict(fam=fam[i], z=att[i] / sd[i], zstar=[v / sd[i] for v in row], beyond_own=abs(att[i]) > cut, exp_own=sum(1 for v in row if abs(v) > cut) / n)
    return dict(n=n, families=fam_out, series=series)


# ---------------------------------------------------------------------------------------------
# E1 in plain loops
# ---------------------------------------------------------------------------------------------
def e1(y, fam, seed=SEED_E1, B=B_BOOT):
    fams = sorted(set(fam))
    F = len(fams)
    members = {f: [i for i, x in enumerate(fam) if x == f] for f in fams}
    n = len(y)
    theta = sum(y) / n
    se = math.sqrt(F / (F - 1) * sum(sum(y[i] - theta for i in members[f]) ** 2 for f in fams)) / n
    tiny = 1e-9 * max(max(abs(v) for v in y), 1e-300)
    t = theta / se if se > tiny else float("nan")
    draw = np.random.default_rng(seed).integers(0, F, size=(B, F))
    S_ = [sum(y[i] for i in members[f]) for f in fams]
    m_ = [len(members[f]) for f in fams]
    ts = []
    for row in draw:
        nb = sum(m_[k] for k in row)
        th = sum(S_[k] for k in row) / nb
        ss = sum((S_[k] - m_[k] * th) ** 2 for k in row)
        seb = math.sqrt(F / (F - 1) * ss) / nb
        ts.append((th - theta) / seb if seb > tiny else 0.0)
    a = [abs(v) for v in ts]
    c = float(np.percentile(a, 95))
    p = (1 + sum(1 for v in a if v >= abs(t))) / (B + 1) if math.isfinite(t) else None
    return dict(theta=theta, se=se, t=t, p=p, c95=c, lo=theta - c * se, hi=theta + c * se, n=n, F=F)


def null_width(blocks, R=N_REF, seed=SEED_NULL):
    if not blocks:
        return None
    z = [v for b in blocks for v in b["z"]]
    if len(z) < 2:
        return None
    obs = sd_(z)
    U = np.random.default_rng(seed).random((R, len(blocks)))
    sds = []
    for r in range(R):
        vals = []
        for j, b in enumerate(blocks):
            idx = min(int(U[r, j] * b["n"]), b["n"] - 1)
            vals += [row[idx] for row in b["zstar"]]
        sds.append(sd_(vals))
    lo, hi = np.percentile(sds, [2.5, 97.5])
    return dict(n_series=len(z), n_designs=len(blocks), observed_mean_z=mean_(z), observed_sd_z=obs, reference_mean_sd=mean_(sds), reference_p25=float(lo), reference_p975=float(hi),
                share_reference_sd_ge_observed=sum(1 for v in sds if v >= obs) / R, ratio_observed_to_reference_mean=obs / mean_(sds))


def estimate(cells):
    m = len(cells)
    if m == 0:
        return dict(m=0, n_beyond=0, pi_NC=None, rbar=None, delta_D2=None, expected_beyond=None, n_designs=0, F=0)
    b = [int(c["B"]) for c in cells]
    r = [c["r"] for c in cells]
    return dict(m=m, n_beyond=sum(b), pi_NC=sum(b) / m, rbar=sum(r) / m, delta_D2=sum(bi - ri for bi, ri in zip(b, r)) / m, expected_beyond=sum(r),
                n_designs=len({c["design_id"] for c in cells}), F=len({c["policy_family"] for c in cells}))


# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--d1", default=None, help="RESULTS_D1_V1.json: consistency of the family statistics (amendment 7.3)")
    ap.add_argument("--out", default=None, help="write the report here (a new file)")
    args = ap.parse_args(argv)
    res = json.load(open(args.results))
    D2 = res["question"]["D2"]

    pairs = coding()
    group_of, reg_by_group, reg_by_design = inventory()
    live = json.load(open(LIVE))["designs"]
    gate = {d["design_id"]: d for d in json.load(open(SWEEP / "GATE_REPORT.json"))["designs"]}
    run = {d["design_id"]: d for d in json.load(open(SWEEP / "SWEEP_RUN.json"))["designs"] if d["status"] == "OK"}

    cover, plain, eval_policy_family = Counter(), {}, {}
    for d in live:
        did = d["design_id"]
        pf = d.get("policy_family") or did
        if d["status"] != "estimated":
            cover["STOP_PRE_RUN"] += 1
        elif d["source_group"] in ARCHIVE_GROUPS:
            cover["FIELD_MISSING"] += 1
        elif did not in gate:
            cover["OTHER"] += 1
        elif gate[did]["status"] != "PASS":
            cover["REPRO_FAIL"] += 1
        elif gate[did].get("adapter") == "annual_full_year_v1":
            cover["NO_SECONDARY_ADAPTER"] += 1
        elif did not in run:
            cover["OTHER"] += 1
        else:
            z = np.load(SWEEP / Path(run[did]["file"]).name, allow_pickle=False)
            out = plain_design([str(x) for x in z["series_ids"]], [str(x) for x in z["family"]], [str(x) for x in z["code"]], z["att"].tolist(), z["placebo"].tolist())
            if out is None:
                cover["D1_SUPPORT"] += 1
            else:
                cover["EVAL"] += 1
                plain[did] = out
                eval_policy_family[did] = pf
    for code in set(cover) | set(D2["coverage_codes_over_D"]):
        compare(f"coverage:{code}", D2["coverage_codes_over_D"].get(code, 0), cover.get(code, 0))

    # cells
    cells = []
    for did in sorted(plain):
        g = group_of.get(did)
        for f in sorted(plain[did]["families"]):
            st = plain[did]["families"][f]
            p = pairs.get((g, f)) if g is not None else None
            coding_error = p is not None and f in reg_by_group.get(g, {})
            narrow_error = f in reg_by_design.get(did, set())
            if p is None:
                code = "GROUP_NOT_CODED"
            elif p["final"] == "no_channel":
                code = "CODING_ERROR" if coding_error else "NC"
            else:
                code = "D2_PAIR_NOT_NC"
            flags = {}
            for v in VARIANTS:
                if p is None:
                    flags[v] = False
                elif v == "final_narrow_exclusion":
                    flags[v] = pair_in(v, p) and not narrow_error
                elif v == "final_no_exclusion":
                    flags[v] = pair_in(v, p)
                else:
                    flags[v] = pair_in(v, p) and not coding_error
            cells.append(dict(design_id=did, policy_family=eval_policy_family[did], policy_group=g, family=f, code=code, B=int(st["beyond"]), r=st["r"], M=st["M"], q=st["q"],
                              n_series=st["n_series"], top_series=st["top_series"], top_z=st["top_z"], ties=st["ties"], flags=flags))
    got_cells = {(c["design_id"], c["family"]): c for c in res["cells"]}
    if set(got_cells) != {(c["design_id"], c["family"]) for c in cells}:
        problems.append("the set of (design, family) cells differs")
    for c in cells:
        g_ = got_cells.get((c["design_id"], c["family"]))
        if g_ is None:
            continue
        lab = f"cell {c['design_id']}|{c['family']}"
        for k in ("M", "q", "r"):
            compare(f"{lab}:{k}", g_[k], c[k])
        compare(f"{lab}:abs_top_z", abs(g_["top_z"]), abs(c["top_z"]))
        if g_["top_series"] not in c["ties"]:
            problems.append(f"{lab}: top series {g_['top_series']!r} is not among the series tied for the largest |z| {c['ties']!r}")
        for k in ("B", "n_series"):
            compare(f"{lab}:{k}", g_[k], c[k])
        for k in ("code", "policy_group", "policy_family"):
            if g_[k] != c[k]:
                problems.append(f"{lab}: {k} {g_[k]!r} vs {c[k]!r}")
        for v in VARIANTS:
            if bool(g_["flags"][v]) != bool(c["flags"][v]):
                problems.append(f"{lab}: variant {v}")
    for code in set(D2["cell_codes"]) | {c["code"] for c in cells}:
        compare(f"cell_codes:{code}", D2["cell_codes"].get(code, 0), sum(1 for c in cells if c["code"] == code))

    # estimate and E1
    nc = [c for c in cells if c["code"] == "NC"]
    est = estimate(nc)
    got = D2["estimate_nc_cells"]
    for k in ("m", "n_designs", "F", "n_beyond", "pi_NC", "rbar", "delta_D2", "expected_beyond"):
        compare(f"estimate:{k}", got[k], est[k])
    e1_got = D2["E1"]
    should_run = est["m"] >= 40 and est["F"] >= 8
    compare("E1:run", e1_got["run"], should_run)
    e1_plain = None
    if should_run and nc:
        e1_plain = e1([c["B"] - c["r"] for c in nc], [c["policy_family"] for c in nc])
        r = e1_got["result"]
        for k, kk in (("theta", "theta"), ("se", "se"), ("t", "t"), ("c95", "c95"), ("p_two_sided", "p")):
            compare(f"E1:{k}", r[k], e1_plain[kk])
        compare("E1:lo", r["interval95"][0], e1_plain["lo"])
        compare("E1:hi", r["interval95"][1], e1_plain["hi"])
    # variants
    for v in VARIANTS:
        e = estimate([c for c in cells if c["flags"][v]])
        got_v = D2["variants_of_the_nc_set"][v]
        for k in ("m", "n_designs", "F", "n_beyond", "pi_NC", "rbar", "delta_D2", "expected_beyond"):
            compare(f"variant {v}:{k}", got_v[k], e[k])
    # empirical null and series level
    def blocks_for(sel):
        out = []
        for did in sorted(plain):
            fams = {f for (d, f) in sel if d == did}
            names = sorted(s for s, x in plain[did]["series"].items() if x["fam"] in fams)
            if names:
                out.append(dict(z=[plain[did]["series"][s]["z"] for s in names], zstar=[plain[did]["series"][s]["zstar"] for s in names], n=plain[did]["n"],
                                beyond=[plain[did]["series"][s]["beyond_own"] for s in names], exp=[plain[did]["series"][s]["exp_own"] for s in names]))
        return out

    for label, key, sel in (("final", ("empirical_null_width_nc_series", "series_level_nc"), {(c["design_id"], c["family"]) for c in nc}),
                            ("strict", ("empirical_null_width_strict_set", "series_level_strict_set"), {(c["design_id"], c["family"]) for c in cells if c["flags"]["strict"]})):
        blocks = blocks_for(sel)
        w = null_width(blocks)
        gw = D2[key[0]]
        if w is None or gw is None:
            if (w is None) != (gw is None):
                problems.append(f"null width {label}: presence differs")
        else:
            for k, kk in (("n_series", "n_series"), ("n_designs", "n_designs"), ("observed_mean_z", "observed_mean_z"), ("observed_sd_z", "observed_sd_z"), ("reference_mean_sd", "reference_mean_sd"),
                          ("reference_p2.5", "reference_p25"), ("reference_p97.5", "reference_p975"), ("share_reference_sd_ge_observed", "share_reference_sd_ge_observed"),
                          ("ratio_observed_to_reference_mean", "ratio_observed_to_reference_mean")):
                compare(f"null width {label}:{k}", gw[k], w[kk])
        gs = D2[key[1]]
        ns = sum(len(b["z"]) for b in blocks)
        if ns:
            nb = sum(sum(1 for x in b["beyond"] if x) for b in blocks)
            ex = sum(sum(b["exp"]) for b in blocks)
            for k, v in (("series", ns), ("series_beyond_own_line", nb), ("expected_beyond", ex), ("share_beyond", nb / ns), ("share_expected", ex / ns)):
                compare(f"series level {label}:{k}", gs[k], v)
    # rates
    for key, field in (("rates_by_policy_group", "policy_group"), ("rates_by_outcome_family", "family"), ("rates_by_design", "design_id")):
        got_t = D2[key]
        vals = sorted({c[field] for c in nc})
        if sorted(got_t) != vals:
            problems.append(f"{key}: keys differ")
        for v in vals:
            sel = [c for c in nc if c[field] == v]
            e = estimate(sel)
            compare(f"{key} {v}:cells", got_t[v]["cells"], e["m"])
            compare(f"{key} {v}:beyond", got_t[v]["beyond"], e["n_beyond"])
            compare(f"{key} {v}:expected", got_t[v]["expected_beyond"], e["expected_beyond"])
            compare(f"{key} {v}:mean_y", got_t[v]["mean_y"], e["delta_D2"])
    # pairs and coding errors
    nc_pairs = sorted(f"{g}|{f}" for (g, f), p in pairs.items() if p["final"] == "no_channel")
    if D2["nc_pairs"] != nc_pairs:
        problems.append("nc_pairs differ")
    errs = sorted((g, f) for (g, f), p in pairs.items() if p["final"] == "no_channel" and f in reg_by_group.get(g, {}))
    if sorted((e["policy_group"], e["family"]) for e in D2["coding_errors"]) != errs:
        problems.append("coding_errors differ")
    for e in D2["coding_errors"]:
        want = sorted([list(x) for x in reg_by_group[e["policy_group"]][e["family"]]])
        if sorted([[x["design_id"], x["outcome_key"], x["role"]] for x in e["contradicting"]]) != want:
            problems.append(f"coding error {e['policy_group']}|{e['family']}: contradicting outcomes differ")
    # D1 consistency
    d1_report = None
    if args.d1:
        d1 = json.load(open(args.d1))
        d1_eval = {r["design_id"]: r for r in d1["designs"] if r.get("code") == "EVAL"}
        if set(d1_eval) != set(plain):
            problems.append(f"D1-V1 EVAL designs differ: {sorted(set(d1_eval) ^ set(plain))}")
        n_f = 0
        for did in sorted(set(d1_eval) & set(plain)):
            fs = d1_eval[did]["family_stats"]
            if sorted(fs) != sorted(plain[did]["families"]):
                problems.append(f"D1-V1 {did}: families differ")
                continue
            for f, st in plain[did]["families"].items():
                n_f += 1
                for k, kk in (("M", "M"), ("q", "q"), ("share_draws_beyond", "r")):
                    compare(f"D1 {did}|{f}:{k}", fs[f][k], st[kk])
                compare(f"D1 {did}|{f}:abs_top_z", abs(fs[f]["top_z"]), abs(st["top_z"]))
                if bool(fs[f]["beyond"]) != st["beyond"] or fs[f]["n_series"] != st["n_series"] or fs[f]["top_series"] not in st["ties"]:
                    problems.append(f"D1 {did}|{f}: beyond, number of series or top series (not among the tied series) differs")
        d1_report = dict(designs=len(d1_eval), families_compared=n_f)

    report = dict(results=str(args.results), designs_eval=len(plain), cells=len(cells), nc_cells=est["m"], F=est["F"], delta_D2=est["delta_D2"], pi_NC=est["pi_NC"],
                  e1=e1_plain, d1_consistency=d1_report, largest_scaled_differences={k: float(f"{v:.2e}") for k, v in sorted(worst.items())}, problems=problems)
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        with open(args.out, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=1, default=str) + "\n")
    if problems:
        print("MISMATCH", len(problems), file=sys.stderr)
        return 1
    print("OK: the independent recomputation agrees with the results file", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
