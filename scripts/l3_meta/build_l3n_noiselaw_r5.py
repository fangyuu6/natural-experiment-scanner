#!/usr/bin/env python3
"""NOISELAW (rule id EX-1, exploratory): placebo SD vs design geometry, and size of |z| vs geometry beyond the placebo slope.
PREREG_AMEND_20261020_NOISELAW.md.  Run:
  VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_l3n_noiselaw_r5.py
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

R = ROOT / "data/l3_meta/refresh/20261020_r2"
SETS = ROOT / "data/l3_meta/n1_sets_v2_20261019/n1_sets_v2.json"
X6 = ROOT / "data/l3_meta/x6_v1/X6_CELLS_V1.csv"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_NOISELAW.md"
OUT = ROOT / "data/l3_meta/l3n_noiselaw_20261020_r5"
SEED = 20261020
N_PERM = 10000
N_BOOT = 2000
MIN_DESIGNS = 8


# ---------- loading ----------
def load_geometry(path=SETS):
    j = json.load(open(path))
    g = {}
    for d in j["designs"]:
        py = d["panel_years"]
        g[d["design_id"]] = dict(
            family=d["policy_family"], T=len(d["T"]), C=len(d["T_cohorts"]), U=len(d["U"]),
            L=int(py[1]) - int(py[0]) + 1, era=(float(d["weighted_lower_median_adoption_year_T"]) if d["weighted_lower_median_adoption_year_T"] is not None else float("nan")))
    return g


def load_cells(geom, root=R):
    """cells: list of dict; placebo z matrix kept per cell."""
    cells = []
    for sub in ("sweep", "sweep_ext"):
        for f in sorted(glob.glob(str(root / sub / "*.npz"))):
            name = os.path.basename(f)[:-4]
            if name not in geom:
                continue
            z = np.load(f, allow_pickle=True)
            for i in range(len(z["series_ids"])):
                if z["code"][i] != "SUPPORTED":
                    continue
                sd, zz = float(z["sd"][i]), float(z["z"][i])
                if not (np.isfinite(sd) and sd > 0 and np.isfinite(zz)):
                    continue
                cells.append(dict(design=name, series=str(z["series_ids"][i]), src=sub, sd=sd, cutoff=float(z["cutoff"][i]),
                                  z=zz, n_units=int(z["n_units"][i]), pz=np.asarray(z["placebo"][i], float) / sd))
    return cells


# ---------- part A ----------
def design_matrix(geom, cells):
    return np.array([[np.log(geom[c["design"]]["T"]), np.log(geom[c["design"]]["U"]), np.log(geom[c["design"]]["C"]),
                      np.log(geom[c["design"]]["L"])] for c in cells])


def mech_term(geom, cells):
    out = []
    for c in cells:
        g = geom[c["design"]]
        out.append(0.5 * np.log(1.0 / g["T"] + 1.0 / (g["U"] - g["T"])))
    return np.array(out)


class FE:
    """series fixed-effects projector; cells must be sorted by group."""

    def __init__(self, gid):
        _, self.starts, self.gid, self.cnt = np.unique(np.asarray(gid), return_index=True, return_inverse=True, return_counts=True)

    def demean(self, A):
        A = np.asarray(A, float)
        m = np.add.reduceat(A, self.starts, axis=0) / (self.cnt if A.ndim == 1 else self.cnt[:, None])
        return A - m[self.gid]


def ols(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def fe_ols(fe, X, y):
    return ols(fe.demean(X), fe.demean(y))


def sort_by_series(cells):
    return sorted(cells, key=lambda c: (c["series"], c["design"]))


def part_a(geom, cells, rng, n_perm=N_PERM, n_boot=N_BOOT):
    cells = sort_by_series(cells)
    sers = [c["series"] for c in cells]
    _, gid = np.unique(sers, return_inverse=True)
    fe = FE(gid)
    X = design_matrix(geom, cells)
    y_sd = np.log([c["sd"] for c in cells])
    y_ln = np.log([c["cutoff"] / c["sd"] for c in cells])
    res = {"n_cells": len(cells), "n_designs": len({c["design"] for c in cells}),
           "n_series": int(gid.max() + 1)}
    names = ["lnT", "lnU", "lnC", "lnL"]
    b = fe_ols(fe, X, y_sd)
    res["four_var_sd"] = dict(zip(names, b.tolist()))
    res["four_var_cutoff_over_sd"] = dict(zip(names, fe_ols(fe, X, y_ln).tolist()))
    res["lnT_only_sd"] = float(fe_ols(fe, X[:, :1], y_sd)[0])
    mt = mech_term(geom, cells)
    res["mech_term_sd"] = float(fe_ols(fe, mt[:, None], y_sd)[0])
    # permutation: geometry rows among cells of the same series
    Xd_y = fe.demean(y_sd)
    mat = np.column_stack([X, mt])
    def perm_stats(idx):
        Xp = fe.demean(X[idx])
        bb = ols(Xp, Xd_y)
        return bb[0]
    obs = b[0]
    dist = np.empty(n_perm)
    for k in range(n_perm):
        idx = np.lexsort((rng.random(len(cells)), gid))
        dist[k] = perm_stats(idx)
    res["perm_b_T"] = dict(mean=float(dist.mean()), sd=float(dist.std(ddof=1)),
                           p_two_sided=float((1 + np.sum(np.abs(dist - dist.mean()) >= abs(obs - dist.mean()))) / (n_perm + 1)),
                           p_vs_zero=float((1 + np.sum(np.abs(dist) >= abs(obs))) / (n_perm + 1)))
    # family cluster bootstrap
    fam_of = {d: geom[d]["family"] for d in {c["design"] for c in cells}}
    fams = sorted(set(fam_of.values()))
    cells_by_fam = {f: [i for i, c in enumerate(cells) if fam_of[c["design"]] == f] for f in fams}
    boots = {k: [] for k in ("lnT", "lnU", "lnC", "lnL", "lnT_only", "mech", "cut_lnT", "cut_lnU", "cut_lnC", "cut_lnL")}
    dropped = 0
    for _ in range(n_boot):
        pick = rng.integers(0, len(fams), len(fams))
        idx = np.concatenate([cells_by_fam[fams[p]] for p in pick]).astype(int)
        gb = gid[idx]
        order = np.argsort(gb, kind="stable")
        idx = idx[order]
        gb = gb[order]
        feb = FE(gb)
        try:
            Xb = X[idx]
            if np.linalg.matrix_rank(feb.demean(Xb)) < Xb.shape[1]:
                dropped += 1
                continue
            bb = fe_ols(feb, Xb, y_sd[idx])
            cc = fe_ols(feb, Xb, y_ln[idx])
            for n_, v in zip(("lnT", "lnU", "lnC", "lnL"), bb):
                boots[n_].append(v)
            for n_, v in zip(("cut_lnT", "cut_lnU", "cut_lnC", "cut_lnL"), cc):
                boots[n_].append(v)
            boots["lnT_only"].append(fe_ols(feb, Xb[:, :1], y_sd[idx])[0])
            boots["mech"].append(fe_ols(feb, mt[idx][:, None], y_sd[idx])[0])
        except np.linalg.LinAlgError:
            dropped += 1
    res["bootstrap_ci95"] = {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in boots.items() if v}
    res["bootstrap_dropped"] = dropped
    res["bootstrap_n_families"] = len(fams)
    lo, hi = res["bootstrap_ci95"]["lnT"]
    res["verdict_sqrt_law"] = ("consistent with the square-root law" if lo <= -0.5 <= hi else
                               "steeper than sqrt" if hi < -0.5 else "flatter than sqrt")
    res["matters_beyond_T"] = [k for k in ("lnU", "lnC", "lnL")
                               if not (res["bootstrap_ci95"][k][0] <= 0 <= res["bootstrap_ci95"][k][1])]
    return res


# ---------- part B ----------
def breadth_tables(cells):
    designs = sorted({c["design"] for c in cells})
    real = np.empty(len(designs))
    plc = np.empty((len(designs), cells[0]["pz"].shape[0]))
    for i, d in enumerate(designs):
        cs = [c for c in cells if c["design"] == d]
        real[i] = np.mean([abs(c["z"]) for c in cs])
        with np.errstate(all="ignore"):
            plc[i] = np.nanmean(np.abs(np.array([c["pz"] for c in cs])), axis=0)
    return designs, real, plc


def slopes(Xg, Y):
    """OLS slopes (without intercept entry) of each column of Y (n x m) on Xg (n x p); returns p x m."""
    A = np.column_stack([np.ones(len(Xg)), Xg])
    return (np.linalg.pinv(A) @ Y)[1:]


def rank(a):
    a = np.asarray(a, float)
    order = a.argsort()
    r = np.empty(len(a))
    r[order] = np.arange(len(a))
    for v in np.unique(a):  # average ties
        m = a == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman_cols(x, Y):
    """Spearman of x (ties averaged) with each column of Y (continuous, ranks by double argsort)."""
    rx = rank(x)
    rx = (rx - rx.mean()) / rx.std()
    ry = Y.argsort(axis=0).argsort(axis=0).astype(float)
    ry = (ry - ry.mean(axis=0)) / ry.std(axis=0)
    return (rx[:, None] * ry).mean(axis=0)


def specs(geom, designs):
    g = [geom[d] for d in designs]
    lnT = np.log([x["T"] for x in g])
    lnC = np.log([x["C"] for x in g])
    share = np.array([x["T"] / x["U"] for x in g])
    era = np.array([x["era"] for x in g])
    era = era - era.mean()
    return dict(joint=np.column_stack([lnT, lnC]), lnT=lnT[:, None], lnC=lnC[:, None],
                four=np.column_stack([lnT, lnC, share, era])), lnT, np.array([x["C"] for x in g], float)


def excess_stats(SP, lnT, Craw, real, plc):
    """returns dict name -> (real slope, mean placebo slope, excess)"""
    Y = np.column_stack([real, plc])
    out = {}
    for key, labels in (("joint", ["lnT", "lnC"]), ("lnT", ["lnT"]), ("lnC", ["lnC"]), ("four", ["lnT", "lnC", "share", "era"])):
        s = slopes(SP[key], Y)
        for i, lab in enumerate(labels):
            out[f"{key}:{lab}"] = (s[i, 0], float(np.nanmean(s[i, 1:])), s[i, 0] - float(np.nanmean(s[i, 1:])))
    for lab, x in (("lnT", lnT), ("C", Craw)):
        sp = spearman_cols(x, Y)
        out[f"spearman:{lab}"] = (sp[0], float(np.nanmean(sp[1:])), sp[0] - float(np.nanmean(sp[1:])))
    out["mean_breadth"] = (real.mean(), float(np.nanmean(plc.mean(axis=0))), real.mean() - float(np.nanmean(plc.mean(axis=0))))
    return out


def part_b(geom, cells, rng, n_boot=N_BOOT):
    designs, real, plc = breadth_tables(cells)
    SP, lnT, Craw = specs(geom, designs)
    est = excess_stats(SP, lnT, Craw, real, plc)
    res = {"n_designs": len(designs), "n_cells": len(cells),
           "estimates": {k: dict(real=float(v[0]), placebo_mean=float(v[1]), excess=float(v[2])) for k, v in est.items()}}
    fams = sorted({geom[d]["family"] for d in designs})
    by_fam = {f: [i for i, d in enumerate(designs) if geom[d]["family"] == f] for f in fams}
    boots = {k: [] for k in est}
    dropped = 0
    for _ in range(n_boot):
        pick = rng.integers(0, len(fams), len(fams))
        idx = np.concatenate([by_fam[fams[p]] for p in pick]).astype(int)
        if len(set(idx.tolist())) < MIN_DESIGNS:
            dropped += 1
            continue
        sub = {k: v[idx] for k, v in SP.items()}
        if np.linalg.matrix_rank(np.column_stack([np.ones(len(idx)), sub["joint"]])) < 3 or np.std(lnT[idx]) == 0 or np.std(Craw[idx]) == 0:
            dropped += 1
            continue
        if np.linalg.matrix_rank(np.column_stack([np.ones(len(idx)), sub["four"]])) < 5:
            dropped += 1
            continue
        e = excess_stats(sub, lnT[idx], Craw[idx], real[idx], plc[idx])
        for k, v in e.items():
            boots[k].append(v[2])
    res["bootstrap_dropped"] = dropped
    res["bootstrap_n_families"] = len(fams)
    for k, v in boots.items():
        res["estimates"][k]["excess_ci95"] = [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    ver = {}
    for lab in ("joint:lnT", "joint:lnC"):
        lo, hi = res["estimates"][lab]["excess_ci95"]
        ver[lab] = "geometry explains detection beyond the mechanical slope" if not (lo <= 0 <= hi) else "no detectable excess"
    res["verdict"] = ver
    return res


def check_x6(cells):
    mp = {(c["design"], c["series"]): c["z"] for c in cells}
    n = bad = 0
    for r in csv.DictReader(open(X6)):
        k = (r["design"], r["series"])
        if k in mp:
            n += 1
            bad += abs(mp[k] - float(r["z_real"])) > 1e-4
    return dict(matched=n, mismatched=int(bad))


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    geom = load_geometry()
    cells = load_cells(geom)
    rng = np.random.default_rng(SEED)
    a = part_a(geom, cells, rng)
    b = part_b(geom, cells, rng)
    out = dict(rule="EX-1", prereg=str(PREREG.relative_to(ROOT)), prereg_sha256=sha(PREREG), seed=SEED,
               n_perm=N_PERM, n_boot=N_BOOT, x6_check=check_x6(cells), part_a=a, part_b=b,
               geometry={d: geom[d] for d in sorted({c["design"] for c in cells})})
    json.dump(out, open(OUT / "RESULTS_L3N_NOISELAW.json", "w"), indent=1)
    with open(OUT / "CELLS_NOISELAW.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["design", "series", "src", "ln_sd", "ln_cutoff_over_sd", "z"])
        for c in sort_by_series(cells):
            w.writerow([c["design"], c["series"], c["src"], f"{np.log(c['sd']):.6f}", f"{np.log(c['cutoff']/c['sd']):.6f}", f"{c['z']:.6f}"])
    print(json.dumps({"A": {k: a[k] for k in ("n_cells", "n_designs", "four_var_sd", "lnT_only_sd", "mech_term_sd", "perm_b_T", "bootstrap_ci95", "verdict_sqrt_law", "matters_beyond_T")},
                      "B": b["estimates"], "Bverdict": b["verdict"], "x6": out["x6_check"]}, indent=1))


if __name__ == "__main__":
    main()
