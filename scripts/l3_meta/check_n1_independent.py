#!/usr/bin/env python3
"""Independent recomputation of the N1-V1 numbers in plain Python (does not import build_n1.py).

Reads the sets file, the covariate file, the live set (design_id, status and policy_family only) and a results JSON written by
build_n1.py; recomputes, design by design with explicit loops, the eligible set, the reference year, D, the 999 reassignments
(cohorts take consecutive blocks of one uniform permutation, as the amendment writes), mu, tau, Z and the permutation p for the
primary null and for S1, and then E1 (theta1, SE, t, p, interval) with an explicit bootstrap loop.  Prints the largest differences
and exits 1 on any difference above 1e-9.  Writes nothing.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_n1_independent.py [results.json]   (numpy is used only for the random numbers
and a percentile)
"""
import csv
import json
import math
import statistics
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SETS = ROOT / "data/l3_meta/n1_sets/n1_sets_v1.json"
COV = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
LIVE = ROOT / "data/l3_meta/l2_set_live.json"
COLS = ["dem_unified_jan1", "citizen_ideology", "ln_pcpi_real_2024usd", "urban_share"]
M, B = 999, 9999


def load_cov():
    cov = {}
    with open(COV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            cov[(r["state"], int(r["year"]))] = [float(r[c]) if r[c] != "" else None for c in COLS]
    return cov


def d_of(cov, sd, ystar, t_pairs, r_states):
    gaps = []
    for c in range(4):
        tv = [cov[(s, y)][c] for s, y in t_pairs if cov[(s, y)][c] is not None]
        rv = [cov[(s, ystar)][c] for s in r_states if cov[(s, ystar)][c] is not None]
        gaps.append((sum(tv) / len(tv) - sum(rv) / len(rv)) / sd[c])
    return math.sqrt(sum(g * g for g in gaps) / 4), gaps


def e1(y, fam, seed):
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


def _live_for(path):
    """The live set the run copied beside its results (l2_set_live_used.json) when present, else the data/l3_meta snapshot."""
    used = Path(path).parent / "l2_set_live_used.json"
    return used if used.exists() else LIVE


def main(path):
    res = json.load(open(path))
    sets = {d["design_id"]: d for d in json.load(open(sys.argv[2] if len(sys.argv) > 2 else SETS))["designs"]}
    live = {d["design_id"]: d.get("policy_family") or d["design_id"] for d in json.load(open(_live_for(path)))["designs"]}
    cov = load_cov()
    got = {r["design_id"]: r for r in res["designs"]}
    worst = {"D": 0.0, "mu": 0.0, "tau": 0.0, "Z": 0.0, "p": 0.0, "mu_S1": 0.0, "tau_S1": 0.0, "Z_S1": 0.0, "p_S1": 0.0}
    eval_ids, z_primary, z_s1, fams, problems = [], [], [], [], []
    for did in sorted(sets):
        if did not in got:   # a sets-file design outside the run's roster D (e.g. the two V1 designs absent from the r3/r4 live set)
            continue
        rec = sets[did]
        U = sorted(s for s in rec["U"] if s != "DC")
        T = [s for s in rec["T"] if s != "DC"]
        R = None if rec["R"] is None else sorted(s for s in rec["R"] if s != "DC")
        ok = bool(rec["treatment_audit_passed"] and R is not None and rec["R_resolved"] and len(U) >= 20 and len(T) >= 3 and len(R) >= 3)
        if ok != (got[did]["code"] == "EVAL"):
            problems.append(f"{did}: eligibility differs ({ok} vs {got[did]['code']})")
        if not ok:
            continue
        eval_ids.append(did)
        onset = rec["T_operative_onset_year"]
        cohorts = {}
        for g, ms in rec["T_cohorts"].items():
            ms = sorted(m for m in ms if m != "DC")
            if ms:
                cohorts[int(g)] = ms
        slots = [(s, onset[s]) for g in sorted(cohorts) for s in cohorts[g]]
        ys = sorted(y for _, y in slots)
        ystar = ys[(len(ys) - 1) // 2]
        inforce = set(rec["in_force_last_panel_year"]) - {"DC"}
        tset = {s for s, _ in slots}
        E = [s for s in U if s in inforce and s not in tset]
        sd = [statistics.stdev([cov[(s, ystar)][c] for s in U if cov[(s, ystar)][c] is not None]) for c in range(4)]
        d_obs, gaps = d_of(cov, sd, ystar, slots, R)
        row = got[did]
        worst["D"] = max(worst["D"], abs(d_obs - row["D"]))
        for c in range(4):
            worst["D"] = max(worst["D"], abs(gaps[c] - row["gaps"][c]))
        for mode, key in (("primary", ""), ("S1", "_S1")):
            pool = U if mode == "primary" else [s for s in U if s not in set(E)]
            rng = np.random.default_rng([20261011, 1, zlib.crc32(did.encode("utf-8"))])
            perms = np.argsort(rng.random((M, len(pool))), axis=1, kind="stable")
            ds = []
            for prow in perms:
                order = [pool[i] for i in prow]
                pos, t_pairs = 0, []
                for g in sorted(cohorts):
                    block = order[pos:pos + len(cohorts[g])]
                    pos += len(cohorts[g])
                    t_pairs += list(zip(block, [onset[s] for s in cohorts[g]]))      # k-th drawn state takes the k-th member year
                ds.append(d_of(cov, sd, ystar, t_pairs, order[pos:])[0])
            mu, tau = statistics.mean(ds), statistics.stdev(ds)
            z = (d_obs - mu) / tau
            p = (1 + sum(1 for v in ds if v >= d_obs)) / (len(ds) + 1)
            g = row["primary" if mode == "primary" else "S1"]
            worst["mu" + key] = max(worst["mu" + key], abs(mu - g["mu"]))
            worst["tau" + key] = max(worst["tau" + key], abs(tau - g["tau"]))
            worst["Z" + key] = max(worst["Z" + key], abs(z - g["Z"]))
            worst["p" + key] = max(worst["p" + key], abs(p - g["p"]))
            (z_primary if mode == "primary" else z_s1).append(z)
        fams.append(live[did])
    n1 = res["question"]["N1"]
    for name, z, block in (("primary", z_primary, n1["primary_literal_null"]), ("S1", z_s1, n1["S1_size_matched_null"])):
        theta, se, t, p, lo, hi = e1(z, fams, [20261011, 1])
        diffs = {"theta": abs(theta - block["theta"]), "se": abs(se - block["se"]), "t": abs(t - block["t"]), "p": abs(p - block["p_two_sided"]),
                 "lo": abs(lo - block["interval95"][0]), "hi": abs(hi - block["interval95"][1])}
        print(f"E1 {name}: theta {theta:.6f} se {se:.6f} t {t:.4f} p {p:.4f} interval [{lo:.4f}, {hi:.4f}]  max diff {max(diffs.values()):.2e}")
        worst["E1_" + name] = max(diffs.values())
    print("EVAL designs:", len(eval_ids), "F:", len(set(fams)))
    print("largest differences:", {k: float(f"{v:.2e}") for k, v in worst.items()})
    bad = [k for k, v in worst.items() if v > 1e-9]
    if problems or bad or len(eval_ids) != res["question"]["N1"]["n"]:
        print("MISMATCH", problems, bad)
        return 1
    print("OK: the independent recomputation agrees with the results file")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data/l3_meta/n1_v1/RESULTS_N1_V1.json"))
