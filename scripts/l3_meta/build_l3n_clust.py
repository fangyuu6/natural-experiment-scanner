#!/usr/bin/env python3
"""CLUST (rule id EX-1, exploratory): spatial clustering of treated states vs random draws, and link to negative-control crossings.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261020_CLUST.md (+ hash record).
Run:  VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_l3n_clust.py [--draws 10000] [--boot 9999]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

SETS = ROOT / "data/l3_meta/n1_sets_v2_20261019/n1_sets_v2.json"
ADJ = ROOT / "data/l3_meta/covariates/state_adjacency_v2.csv"
COV = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
NC = ROOT / "data/l3_meta/refresh/20261019_r2/d2/NC_CELLS_D2_V1.csv"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_CLUST.md"
OUT = ROOT / "data/l3_meta/l3n_clust_20261020"
COVS = ["dem_unified_jan1", "citizen_ideology", "ln_pcpi_real_2024usd", "urban_share"]


def rng_for(tag):
    return np.random.default_rng(int(hashlib.sha256(tag.encode()).hexdigest(), 16) % (2 ** 63))


def rank_avg(a):
    a = np.asarray(a, float)
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a))
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(x, y):
    rx, ry = rank_avg(x), rank_avg(y)
    rx -= rx.mean()
    ry -= ry.mean()
    d = math.sqrt((rx @ rx) * (ry @ ry))
    return float(rx @ ry / d) if d > 0 else float("nan")


def load_adj():
    pairs = []
    with open(ADJ) as f:
        for r in csv.DictReader(f):
            if r["land_border_rule_v1"] == "1":
                pairs.append((r["state_a"], r["state_b"]))
    return pairs


def adj_matrix(units, pairs):
    ix = {u: i for i, u in enumerate(units)}
    A = np.zeros((len(units), len(units)))
    for a, b in pairs:
        if a in ix and b in ix:
            A[ix[a], ix[b]] = A[ix[b], ix[a]] = 1
    return A


def join_counts(X, A):
    """X: (D, n) 0/1 treated indicator; returns number of treated-treated edges per row."""
    return ((X @ A) * X).sum(1) / 2.0


def zscore(obs, draws):
    sd = draws.std(ddof=1)
    if not sd > 0:
        return None, float(draws.mean()), float(sd)
    return float((obs - draws.mean()) / sd), float(draws.mean()), float(sd)


def draws_U(n, k, D, rng):
    r = rng.random((D, n))
    idx = np.argsort(r, axis=1)[:, :k]
    X = np.zeros((D, n))
    np.put_along_axis(X, idx, 1.0, axis=1)
    return X


def make_blocks(index, K=5):
    """index: dict position->value for free units (sorted by (round(v,9), position as code order))."""
    order = sorted(index, key=lambda p: (round(index[p], 9), p))
    nf = len(order)
    Ke = max(1, min(K, nf // 5))
    base, extra = divmod(nf, Ke)
    blocks, s = [], 0
    for b in range(Ke):
        sz = base + (1 if b < extra else 0)
        blocks.append(sorted(order[s:s + sz]))
        s += sz
    return blocks


def p5_index(units, onset, covs, ystar):
    """Return dict position->index for free units, or None if response constant. units sorted codes."""
    free = [i for i, u in enumerate(units) if all(covs.get((u, ystar), {}).get(c) is not None for c in COVS)]
    if len(free) < 5:
        return None, free
    Z = np.array([[covs[(units[i], ystar)][c] for c in COVS] for i in free], float)
    Z = Z - Z.mean(0)
    sd = Z.std(0, ddof=1)
    keep = sd > 0
    Z = Z[:, keep] / sd[keep]
    resp = np.array([onset.get(units[i], np.inf) for i in free], float)
    s = rank_avg(resp)
    if np.ptp(s) == 0 or Z.shape[1] == 0:
        return None, free
    beta = np.linalg.solve(Z.T @ Z + np.eye(Z.shape[1]), Z.T @ (s - s.mean()))
    idx = Z @ beta
    return {free[j]: float(idx[j]) for j in range(len(free))}, free


def draws_P5(lab, blocks, D, rng):
    X = np.tile(lab, (D, 1))
    for blk in blocks:
        blk = np.array(blk)
        perm = np.argsort(rng.random((D, len(blk))), axis=1)
        X[:, blk] = lab[blk][perm]
    return X


def contag_stat(C, A, T_pos_adj):
    """C: (D, m) cohort years per T state per draw; T_pos_adj: (m, m) adjacency among T states.
    Returns share of non-earliest states with a T neighbour of cohort <= own."""
    cmin = C[0].min()
    le = T_pos_adj[None, :, :].astype(bool) & (C[:, None, :] <= C[:, :, None])
    has = le.any(2)
    sel = C > cmin
    return (has & sel).sum(1) / np.maximum(sel.sum(1), 1)


def load_cov():
    covs = {}
    with open(COV) as f:
        for r in csv.DictReader(f):
            d = {}
            for c in COVS:
                v = r[c]
                d[c] = float(v) if v not in ("", "NA") else None
            covs[(r["state"], int(r["year"]))] = d
    return covs


def lower_median(v):
    v = sorted(v)
    return v[(len(v) - 1) // 2]


def analyse_design(x, pairs, covs, D):
    did = x["design_id"]
    U = sorted(x["U"])
    T = sorted(x["T"])
    n, k = len(U), len(T)
    ix = {u: i for i, u in enumerate(U)}
    A = adj_matrix(U, pairs)
    lab = np.zeros(n)
    for t in T:
        lab[ix[t]] = 1
    J = float(lab @ A @ lab / 2)
    res = {"design_id": did, "family": x["policy_family"], "n_U": n, "n_T": k, "J": J, "edges_U": int(A.sum() / 2)}
    X = draws_U(n, k, D, rng_for(f"CLUST-V1|U|{did}"))
    z, m, s = zscore(J, join_counts(X, A))
    Jd = join_counts(X, A)
    res.update(z_U=z, J_mean_U=m, J_sd_U=s, p_U=float((1 + (Jd >= J).sum()) / (D + 1)))
    # P5
    onset = {s_: float(v) for s_, v in x["T_operative_onset_year"].items() if v is not None}
    for yr_, sts_ in x["T_cohorts"].items():  # fallback for treated states with no operative onset year: cohort year
        for s_ in sts_:
            onset.setdefault(s_, float(yr_))
    ystar = int(lower_median([v for v in onset.values()])) if onset else None
    res["ystar"] = ystar
    res.update(z_P5=None, J_mean_P5=None, J_sd_P5=None, p_P5=None, p5_note=None)
    if ystar is None:
        res["p5_note"] = "no onset years"
    else:
        idx, free = p5_index(U, onset, covs, ystar)
        if idx is None:
            res["p5_note"] = "response constant or too few free states"
        else:
            blocks = make_blocks(idx)
            XP = draws_P5(lab, blocks, D, rng_for(f"CLUST-V1|P5|{did}"))
            Jp = join_counts(XP, A)
            z5, m5, s5 = zscore(J, Jp)
            res.update(z_P5=z5, J_mean_P5=m5, J_sd_P5=s5, p_P5=float((1 + (Jp >= J).sum()) / (D + 1)), p5_blocks=len(blocks), n_fixed=n - len(free))
            if z5 is None:
                res["p5_note"] = "draw sd zero"
    # C
    coh = {}
    for yr, sts in x["T_cohorts"].items():
        for s_ in sts:
            coh[s_] = int(yr)
    res.update(excess_C=None, obs_C=None, mean_C=None, n_C=None)
    if len(set(coh.values())) >= 2 and set(coh) == set(T):
        Tl = T
        m_ = len(Tl)
        Tadj = np.array([[A[ix[a], ix[b]] for b in Tl] for a in Tl])
        c0 = np.array([coh[t] for t in Tl], float)
        rng = rng_for(f"CLUST-V1|C|{did}")
        perm = np.argsort(rng.random((D, m_)), axis=1)
        C = c0[perm]
        obs = float(contag_stat(c0[None, :], A, Tadj)[0])
        sh = contag_stat(C, A, Tadj)
        res.update(obs_C=obs, mean_C=float(sh.mean()), excess_C=float(obs - sh.mean()), n_C=int((c0 > c0.min()).sum()))
    return res


def family_boot_mean(vals, fams, B, rng):
    ufam = sorted(set(fams))
    byf = defaultdict(list)
    for v, f in zip(vals, fams):
        byf[f].append(v)
    sums = np.array([sum(byf[f]) for f in ufam], float)
    cnt = np.array([len(byf[f]) for f in ufam], float)
    out = np.empty(B)
    for b in range(B):
        j = rng.integers(0, len(ufam), len(ufam))
        out[b] = sums[j].sum() / cnt[j].sum()
    return out


def pooled(vals, fams, B, seed_tag, chance=None, thr=None):
    vals = np.array(vals, float)
    bm = family_boot_mean(vals, fams, B, rng_for(seed_tag))
    lo, hi = np.percentile(bm, [2.5, 97.5])
    out = {"n_designs": len(vals), "n_families": len(set(fams)), "mean": float(vals.mean()), "ci_lo": float(lo), "ci_hi": float(hi)}
    out["verdict"] = "CLUSTERED" if lo > 0 else ("SPREAD" if hi < 0 else "NOT SEPARATED")
    if thr is not None:
        out["share_above_thr"] = float((vals > thr).mean())
    return out


def link(x, y, fams, B, tag):
    x, y = np.array(x, float), np.array(y, float)
    obs = spearman(x, y)
    ufam = sorted(set(fams))
    byf = defaultdict(list)
    for i, f in enumerate(fams):
        byf[f].append(i)
    rng = rng_for(tag + "|boot")
    bs = []
    for _ in range(B):
        j = rng.integers(0, len(ufam), len(ufam))
        ids = [i for q in j for i in byf[ufam[q]]]
        r = spearman(x[ids], y[ids])
        if r == r:
            bs.append(r)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    fx = np.array([x[byf[f]].mean() for f in ufam])
    fy = np.array([y[byf[f]].mean() for f in ufam])
    ofam = spearman(fx, fy)
    rng2 = rng_for(tag + "|perm")
    cnt = 0
    for _ in range(B):
        if abs(spearman(rng2.permutation(fx), fy)) >= abs(ofam) - 1e-12:
            cnt += 1
    pf = (1 + cnt) / (B + 1)
    nf = len(ufam)
    mdc = math.tanh((1.96 + 0.84) / math.sqrt(nf - 3)) if nf > 3 else None
    verdict = "LINK" if (lo > 0 or hi < 0) and pf < 0.05 and (obs > 0) == (ofam > 0) else "NO LINK DETECTED"
    return {"n_designs": len(x), "n_families": nf, "spearman_design": obs, "boot_lo": float(lo), "boot_hi": float(hi),
            "spearman_family_means": ofam, "perm_p_family": pf, "min_detectable_r_80": mdc, "verdict": verdict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=10000)
    ap.add_argument("--boot", type=int, default=9999)
    ap.add_argument("--out-dir", default=str(OUT))
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sets = json.load(open(SETS))["designs"]
    pairs = load_adj()
    covs = load_cov()
    elig = [x for x in sets if len(x["T"]) >= 4 and set(x["T"]) <= set(x["U"]) and len(x["U"]) > len(x["T"])]
    rows, dropped = [], []
    for x in elig:
        r = analyse_design(x, pairs, covs, a.draws)
        rows.append(r)
        if r["z_U"] is None:
            dropped.append((r["design_id"], "U draw sd zero"))
        if r["z_P5"] is None:
            dropped.append((r["design_id"], "P5: " + str(r["p5_note"])))
    nc = defaultdict(lambda: [0, 0, 0, 0])
    with open(NC) as f:
        for r in csv.DictReader(f):
            c = nc[r["design_id"]]
            c[0] += 1
            c[1] += int(r["beyond"])
            if r["strict_nc"] == "1":
                c[2] += 1
                c[3] += int(r["beyond"])
    for r in rows:
        c = nc.get(r["design_id"])
        r["nc_cells"] = c[0] if c else None
        r["nc_beyond_share"] = c[1] / c[0] if c else None
        r["nc_strict_cells"] = c[2] if c else None
        r["nc_strict_beyond_share"] = (c[3] / c[2]) if c and c[2] else None
    res = {"prereg": str(PREREG.relative_to(ROOT)), "draws": a.draws, "boot": a.boot, "n_eligible": len(rows),
           "dropped": dropped}
    for key, col in (("A_U", "z_U"), ("A_P5", "z_P5")):
        sub = [r for r in rows if r[col] is not None]
        res[key] = pooled([r[col] for r in sub], [r["family"] for r in sub], a.boot, "CLUST-V1|boot|" + key, thr=1.645)
    subc = [r for r in rows if r["excess_C"] is not None]
    res["C"] = pooled([r["excess_C"] for r in subc], [r["family"] for r in subc], a.boot, "CLUST-V1|boot|C")
    # B
    for key, xc, yc in (("B_U", "z_U", "nc_beyond_share"), ("B_P5", "z_P5", "nc_beyond_share"),
                        ("B_U_strict", "z_U", "nc_strict_beyond_share")):
        sub = [r for r in rows if r[xc] is not None and r[yc] is not None]
        if len(sub) >= 5:
            res[key] = link([r[xc] for r in sub], [r[yc] for r in sub], [r["family"] for r in sub], a.boot, "CLUST-V1|" + key)
    (out / "RESULTS_CLUST_V1.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    cols = list(rows[0])
    with open(out / "CLUST_DESIGNS_V1.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(json.dumps(res, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
