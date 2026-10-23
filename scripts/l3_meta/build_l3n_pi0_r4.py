#!/usr/bin/env python3
"""PI0 (rule id EX-1, exploratory): share of cross-policy results with real movement; empirical-null mixture.  PREREG_AMEND_20261020_PI0.md.

Run:  VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_l3n_pi0_r4.py [--quick]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

REFRESH = ROOT / "data/l3_meta/refresh/20261020_r1"
LIVE = REFRESH / "live/l2_set_live.json"
CAL = REFRESH / "cal/RESULTS_CAL_V1.json"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_PI0.md"
OUT = ROOT / "data/l3_meta/l3n_pi0_20261020_r4"
KAPPA_FALLBACK = 1.3105319977534653
K_LO, K_HI = 1.0702811230675329, 1.5119603041873089
SEED = 20261030
GRID = np.round(np.arange(-10.0, 10.0 + 1e-9, 0.1), 10)
BIN = 0.1
EM_ITERS = 3000
Q = 0.682689


# ---------------------------------------------------------------- statistics
def storey_pi1(z, s):
    z = np.asarray(z, float)
    pi0 = min(1.0, float(np.mean(np.abs(z) <= s)) / Q)
    return 1.0 - pi0


def npmle(z, s, iters=EM_ITERS):
    """Binned NPMLE of G for z ~ N(theta, s^2); returns weights on GRID."""
    z = np.asarray(z, float)
    z = np.clip(z, -14.95, 14.95)
    edges = np.arange(-15.0, 15.0 + 1e-9, BIN)
    cnt, _ = np.histogram(z, bins=edges)
    mid = 0.5 * (edges[:-1] + edges[1:])
    keep = cnt > 0
    mid, cnt = mid[keep], cnt[keep].astype(float)
    L = np.exp(-0.5 * ((mid[:, None] - GRID[None, :]) / s) ** 2)
    L /= L.sum(axis=0, keepdims=True).clip(1e-300)
    w = np.full(GRID.size, 1.0 / GRID.size)
    n = cnt.sum()
    for _ in range(iters):
        mix = L @ w
        w = w * (L.T @ (cnt / mix.clip(1e-300))) / n
    return w / w.sum()


def g_masses(w):
    a = np.abs(GRID)
    return dict(g_null=float(w[a <= 0.25 + 1e-9].sum()), g_gt05=float(w[a > 0.5 + 1e-9].sum()), g_gt1=float(w[a > 1.0 + 1e-9].sum()))


def central(z):
    z = np.asarray(z, float)
    d0 = float(np.median(z))
    q25, q75 = np.percentile(z, [25, 75])
    sc = float((q75 - q25) / 1.34898)
    T = int(np.sum(np.abs(z - d0) > 1.96 * sc))
    return d0, sc, T


def tau2(z, s):
    return max(0.0, float(np.mean(np.square(z))) - s * s)


def all_stats(z, s, with_npmle=True):
    z = np.asarray(z, float)
    d0, sc, T = central(z)
    out = OrderedDict(n=int(z.size), pi1=storey_pi1(z, s), tau2=tau2(z, s), tau=math.sqrt(tau2(z, s)), delta0=d0, s_c=sc, T=T, T_expected=0.05 * z.size,
                      V=float(np.mean(z ** 2) - 1.0), I=max(0.0, sc * sc - 1.0))
    out["tail_part"] = out["V"] - out["I"]
    if with_npmle:
        out.update(g_masses(npmle(z, s)))
    return out


def pctl(a, p):
    return [float(x) for x in np.percentile(np.asarray(a, float), p)]


# ---------------------------------------------------------------- data
def load_cells():
    live = json.loads(LIVE.read_text())
    fam_of = {d["design_id"]: d["policy_family"] for d in live["designs"]}
    cells = []
    for which in ("sweep", "sweep_ext"):
        for f in sorted((REFRESH / which).glob("*.npz")):
            if "series_store" in f.name:
                continue
            did = f.stem
            a = np.load(f, allow_pickle=True)
            ok = (a["code"].astype(str) == "SUPPORTED") & np.isfinite(a["z"])
            for i in np.where(ok)[0]:
                cells.append(dict(design=did, pfam=fam_of[did], ofam=str(a["family"][i]), z=float(a["z"][i]), pl=np.asarray(a["placebo"][i], float) / float(a["sd"][i]), src=which))
    return cells


def load_headline():
    live = json.loads(LIVE.read_text())
    rows = []
    for d in live["designs"]:
        if d["status"] != "estimated":
            continue
        for o in d["primary_outcomes"]:
            if all(o.get(k) is not None for k in ("estimate", "noise_sd", "noise_cutoff", "ri_p")):
                rows.append(dict(design=d["design_id"], pfam=d["policy_family"], z=float(o["estimate"]) / float(o["noise_sd"]), beyond=bool(o.get("beyond_noise"))))
    return rows


def kappa_hat():
    try:
        return float(json.loads(CAL.read_text())["question"]["CAL"]["kappa"]["kappa_hat"])
    except Exception:
        return KAPPA_FALLBACK


# ---------------------------------------------------------------- bootstrap / replicates
def boot(z, groups, s, nboot, rng, with_npmle, nnp):
    """Cluster bootstrap over `groups` (array of labels)."""
    z = np.asarray(z, float)
    labs = sorted(set(groups))
    idx = {g: np.where(np.asarray(groups) == g)[0] for g in labs}
    res = defaultdict(list)
    for b in range(nboot):
        pick = rng.integers(0, len(labs), len(labs))
        zz = z[np.concatenate([idx[labs[j]] for j in pick])]
        st = all_stats(zz, s, with_npmle=(with_npmle and b < nnp))
        for k, v in st.items():
            if k not in ("n", "T_expected"):
                res[k].append(v)
    out = {}
    for k, v in res.items():
        lo, hi = pctl(v, [2.5, 97.5])
        if k == "pi1":
            lo, hi = max(0.0, lo), min(1.0, hi)
        out[k] = dict(lo=lo, hi=hi, n=len(v))
    return out


def replicate_stats(cols, scale, s, with_npmle):
    res = defaultdict(list)
    for z in cols:
        st = all_stats(z * scale, s, with_npmle=with_npmle)
        for k, v in st.items():
            res[k].append(v)
    return res


def summarize_ref(real, res, keys):
    out = {}
    for k in keys:
        v = np.asarray(res[k], float)
        out[k] = dict(real=float(real[k]), ref_mean=float(v.mean()), ref_p2_5=float(np.percentile(v, 2.5)), ref_p97_5=float(np.percentile(v, 97.5)),
                      share_ref_ge_real=float(np.mean(v >= real[k] - 1e-12)))
    return out


def pl_columns(cells, c0, c1):
    """Replicate vectors: one per placebo column; cells with NaN in that column dropped."""
    cols, des = [], []
    P = np.vstack([c["pl"] for c in cells])
    for c in range(c0, c1):
        ok = np.isfinite(P[:, c])
        cols.append(P[ok, c])
    return cols, P


def positive_control(base_cols, kappa, scen, rng, with_npmle, spike_pool=None):
    rows = []
    for pi_true, T in scen:
        pis, g1 = [], []
        for z0 in base_cols:
            z = z0 * kappa
            n = z.size
            if pi_true > 0:
                m = int(round(pi_true * n))
                idx = rng.choice(n, m, replace=False)
                z = z.copy()
                z[idx] += T * rng.choice([-1.0, 1.0], m)
            pis.append(storey_pi1(z, kappa))
            if with_npmle:
                g1.append(g_masses(npmle(z, kappa))["g_gt1"])
        row = dict(pi_true=pi_true, theta=T, pi1_mean=float(np.mean(pis)), pi1_sd=float(np.std(pis)), passed=bool(abs(np.mean(pis) - pi_true) <= 0.10))
        if with_npmle:
            row.update(g_gt1_mean=float(np.mean(g1)), g_gt1_sd=float(np.std(g1)))
        rows.append(row)
    return rows


SCEN = [(0.0, 0.0), (0.10, 2.0), (0.10, 3.0), (0.25, 2.0), (0.25, 3.0)]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out-dir", default=str(OUT))
    a = ap.parse_args()
    nrep, nboot, nnp, nctl_a, nctl_b, nsimb = (500, 2000, 300, 200, 1000, 5000)
    if a.quick:
        nrep, nboot, nnp, nctl_a, nctl_b, nsimb = (20, 40, 5, 5, 20, 100)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    kap = kappa_hat()
    rng = np.random.default_rng(SEED)
    cells = load_cells()
    head = load_headline()
    zA = np.array([c["z"] for c in cells])
    gP = [c["pfam"] for c in cells]
    gD = [c["design"] for c in cells]
    zB = np.array([h["z"] for h in head])
    res = OrderedDict(rule_id="EX-1", question="PI0", kappa_hat=kap, counts=dict(
        A_cells=len(cells), A_designs=len(set(gD)), A_policy_families=len(set(gP)), A_outcome_families=len(set(c["ofam"] for c in cells)),
        B_results=len(head), B_designs=len(set(h["design"] for h in head)), B_policy_families=len(set(h["pfam"] for h in head))))
    print(res["counts"], flush=True)
    keys = ["pi1", "tau", "g_gt05", "g_gt1", "s_c", "T"]

    # ---- (A) sweep cells
    A = OrderedDict()
    A["real"] = {str(s): all_stats(zA, s) for s in (kap, K_LO, K_HI, 1.0)}
    A["real"]["empirical_s_c"] = all_stats(zA, A["real"][str(kap)]["s_c"])
    realK = A["real"][str(kap)]
    cols, _ = pl_columns(cells, 0, nrep)
    resK = replicate_stats(cols, kap, kap, True)
    A["ref_K_placebo_times_kappa"] = summarize_ref(realK, resK, keys + ["tau2"])
    resR = replicate_stats(cols, 1.0, kap, False)
    A["ref_R_placebo_unscaled"] = summarize_ref(realK, resR, ["s_c", "T", "pi1"])
    sc = realK["s_c"]
    resC = replicate_stats(cols, sc, kap, False)
    A["ref_C_placebo_times_real_s_c"] = summarize_ref(realK, resC, ["T"])
    print("A refs", time.time() - t0, flush=True)
    A["boot_by_policy_family"] = boot(zA, gP, kap, nboot, rng, True, nnp)
    A["boot_by_design"] = boot(zA, gD, kap, nboot, rng, True, nnp)
    print("A boot", time.time() - t0, flush=True)
    bf = A["boot_by_policy_family"]["pi1"]
    rk = A["ref_K_placebo_times_kappa"]["pi1"]
    A["decision"] = OrderedDict(
        movement=bool(realK["pi1"] > rk["ref_p97_5"] and bf["lo"] > 0),
        inflation=bool(realK["s_c"] > A["ref_R_placebo_unscaled"]["s_c"]["ref_p97_5"]),
        tail=bool(realK["T"] > A["ref_C_placebo_times_real_s_c"]["T"]["ref_p97_5"]))
    A["decision"]["shape"] = "tail" if A["decision"]["tail"] else ("inflation only" if A["decision"]["inflation"] else "neither")
    byf = OrderedDict()
    for f in sorted(set(c["ofam"] for c in cells)):
        zz = np.array([c["z"] for c in cells if c["ofam"] == f])
        byf[f] = dict(n=int(zz.size), pi1=storey_pi1(zz, kap), s_c=central(zz)[1], share_abs_gt_kappa196=float(np.mean(np.abs(zz) > 1.96 * kap)))
    A["by_outcome_family_descriptive"] = byf
    ccols, _ = pl_columns(cells, 500, 500 + nctl_a)
    A["positive_control"] = positive_control(ccols, kap, SCEN, rng, True)
    res["A_sweep_cells"] = A
    print("A done", time.time() - t0, flush=True)

    # ---- (B) headline results
    B = OrderedDict()
    B["real"] = {str(s): all_stats(zB, s) for s in (kap, K_LO, K_HI, 1.0)}
    B["real"]["empirical_s_c"] = all_stats(zB, B["real"][str(kap)]["s_c"])
    B["n_beyond_noise_flag"] = int(sum(h["beyond"] for h in head))
    realB = B["real"][str(kap)]
    B["ref_draws"] = OrderedDict()
    for lab, k2 in (("kappa_hat", kap), ("kappa_lo", K_LO), ("kappa_hi", K_HI)):
        d = [rng.standard_normal(zB.size) * k2 for _ in range(nsimb)]
        rs = defaultdict(list)
        for z in d[:nsimb if lab == "kappa_hat" else min(nsimb, 1000)]:
            st = all_stats(z, k2, with_npmle=(lab == "kappa_hat" and len(rs["pi1"]) < 1000))
            for kk, v in st.items():
                rs[kk].append(v)
        stat_k = all_stats(zB, k2)
        B["ref_draws"][lab] = summarize_ref(stat_k, {k: rs[k] for k in rs}, ["pi1", "tau", "s_c", "T"] + (["g_gt05", "g_gt1"] if lab == "kappa_hat" else []))
    d1 = [rng.standard_normal(zB.size) for _ in range(nsimb)]
    B["ref_s_c_unit_normal"] = dict(real=realB["s_c"], p97_5=float(np.percentile([central(z)[1] for z in d1], 97.5)))
    dc = [rng.standard_normal(zB.size) * realB["s_c"] for _ in range(nsimb)]
    B["ref_T_given_real_s_c"] = dict(real=realB["T"], p97_5=float(np.percentile([central(z)[2] for z in dc], 97.5)))
    B["boot_by_policy_family"] = boot(zB, [h["pfam"] for h in head], kap, nboot, rng, True, nnp)
    B["boot_by_design"] = boot(zB, [h["design"] for h in head], kap, nboot, rng, True, nnp)
    bfB = B["boot_by_policy_family"]["pi1"]
    B["decision"] = OrderedDict(
        movement=bool(realB["pi1"] > B["ref_draws"]["kappa_hat"]["pi1"]["ref_p97_5"] and bfB["lo"] > 0),
        inflation=bool(realB["s_c"] > B["ref_s_c_unit_normal"]["p97_5"]),
        tail=bool(realB["T"] > B["ref_T_given_real_s_c"]["p97_5"]))
    B["decision"]["shape"] = "tail" if B["decision"]["tail"] else ("inflation only" if B["decision"]["inflation"] else "neither")
    bdraws = [rng.standard_normal(zB.size) for _ in range(nctl_b)]
    B["positive_control"] = positive_control(bdraws, kap, SCEN, rng, True)
    res["B_headline"] = B
    res["elapsed_s"] = time.time() - t0
    res["inputs_sha256"] = {str(p.relative_to(ROOT)): sha(p) for p in (LIVE, CAL, PREREG)}
    res["settings"] = dict(seed=SEED, n_replicates=nrep, n_boot=nboot, n_boot_npmle=nnp, n_control_A=nctl_a, n_control_B=nctl_b, n_ref_draws_B=nsimb, quick=a.quick, em_iters=EM_ITERS, bin=BIN)
    p = out_dir / ("RESULTS_PI0_QUICK.json" if a.quick else "RESULTS_PI0.json")
    p.write_text(json.dumps(res, indent=1, default=float))
    print("wrote", p, flush=True)


if __name__ == "__main__":
    main()
