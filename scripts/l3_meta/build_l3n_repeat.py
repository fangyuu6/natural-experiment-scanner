#!/usr/bin/env python3
"""REPEAT (rule id EX-1, exploratory): are the same states the adopters / early adopters across unrelated designs?

Rules: docs/plans/l3-meta/PREREG_AMEND_20261020_REPEAT.md (+ hash record).  Treatment sets and onset years only; no estimate is used.
Run:  VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_l3n_repeat.py [--out-dir DIR] [--quick]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_REPEAT.md"
SETS = ROOT / "data/l3_meta/n1_sets_v2_20261019/n1_sets_v2.json"
TIMELINE = ROOT / "data/l3_meta/party_coding/design_state_timeline_v2.csv"
COV = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
OUT = ROOT / "data/l3_meta/l3n_repeat_20261020"
SEED = 20261020
D_A, D_B, D_C, N_BOOT = 10000, 5000, 5000, 2000
MIN_ROWS = 10      # arms A, B: designs with the state in U
MIN_EARLY = 8      # arm C earliness: designs with the state
COV_NAMES = ["ideology", "ln_income", "urban_share", "dem_share"]
INPUT_SHA = {
    SETS: "5fece9e58453ff5aea2fe0db1dfbd86aa7c91307d7b2e5d9f9f22580713787d7",
    TIMELINE: "8eb2e66635d87569ec7072a0149c484927cdd62c908e092336236d1ec377d0cd",
    COV: "548b6563a0c10a54aa0d6cdc6cc863d68d04073e7a56275c789e6777a028626e",
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------ pure statistics (tested on invented data)
def draw_T(M, tsize, D, rng):
    """D redraws of every design's treated set uniformly inside its U (M: K x S bool), |T| fixed.  Returns (D, K, S) bool."""
    K, S = M.shape
    key = rng.random((D, K, S))
    key[:, ~M] = 2.0                                    # outside U never drawn
    order = np.argsort(key, axis=2)
    rank = np.argsort(order, axis=2)                    # rank of each state in the random order
    return rank < np.asarray(tsize)[None, :, None]


def share_variance(X, M, min_rows):
    """Variance across states (in U of >= min_rows designs) of the treated share.  X: (..., K, S) bool, M: (K, S) bool."""
    n_u = M.sum(0)
    keep = n_u >= min_rows
    share = (X & M).sum(-2)[..., keep] / n_u[keep]
    return share.var(-1), keep


def first_svd(X, M, min_rows):
    """Column-centre over observed entries (missing -> 0), first singular triplet.  X: (..., K, S).  Returns (share of sigma1^2, loading vector over kept states, keep)."""
    n_u = M.sum(0)
    Xf = np.where(M, X.astype(float), 0.0)
    mean = Xf.sum(-2, keepdims=True) / np.maximum(n_u, 1)
    Z = np.where(M, Xf - mean, 0.0)
    U_, s, Vt = np.linalg.svd(Z, full_matrices=False)
    ratio = s[..., 0] ** 2 / np.maximum((s ** 2).sum(-1), 1e-300)
    keep = n_u >= min_rows
    return ratio, Vt[..., 0, :][..., keep], keep


def rank_rows(a):
    """Average ranks along the last axis (ties -> mean rank), 1-based."""
    a = np.asarray(a, float)
    less = (a[..., None, :] < a[..., :, None]).sum(-1)
    eq = (a[..., None, :] == a[..., :, None]).sum(-1)
    return less + (eq + 1) / 2.0


def spearman_rows(a, b):
    """Spearman correlation of every row of a (..., m) with the vector b (m)."""
    ra = rank_rows(a)
    rb = rank_rows(b)
    ra = ra - ra.mean(-1, keepdims=True)
    rb = rb - rb.mean()
    den = np.sqrt((ra ** 2).sum(-1) * (rb ** 2).sum())
    with np.errstate(invalid="ignore", divide="ignore"):
        return (ra * rb).sum(-1) / den


def scaled_midrank(y):
    """(midrank - 1)/(n - 1) along the last axis; ties get the mean."""
    n = y.shape[-1]
    return (rank_rows(y) - 1) / max(n - 1, 1)


def tau_gamma(x, y):
    """Kendall tau-b and Goodman-Kruskal gamma of the rows of x with the rows of y.  x, y: (D, n)."""
    sx = np.sign(x[:, :, None] - x[:, None, :])
    sy = np.sign(y[:, :, None] - y[:, None, :])
    num = (sx * sy).sum((1, 2))
    dx, dy = np.abs(sx).sum((1, 2)), np.abs(sy).sum((1, 2))
    both = np.abs(sx * sy).sum((1, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        tau = num / np.sqrt(dx * dy)
        gam = num / both
    return tau, gam


def pair_stats(Y, shared_idx):
    """Y: dict design -> (D, nA) onset years (row 0 = observed order).  shared_idx: list of (p, q, ip, iq) -> (tau (D,P), gamma (D,P))."""
    D = next(iter(Y.values())).shape[0]
    tau = np.full((D, len(shared_idx)), np.nan)
    gam = np.full((D, len(shared_idx)), np.nan)
    for k, (p, q, ip, iq) in enumerate(shared_idx):
        tau[:, k], gam[:, k] = tau_gamma(Y[p][:, ip], Y[q][:, iq])
    return tau, gam


def family_boot_diff(tau_obs, tau_null_mean, fam_of_pair, families, n_boot, rng):
    """Interval for mean(obs - null mean) over pairs when families are resampled.  Pair weight = product of family multiplicities."""
    fi = {f: i for i, f in enumerate(families)}
    a = np.array([fi[f[0]] for f in fam_of_pair])
    b = np.array([fi[f[1]] for f in fam_of_pair])
    d = tau_obs - tau_null_mean
    ok = ~np.isnan(d)
    out = np.empty(n_boot)
    nf = len(families)
    for i in range(n_boot):
        c = np.bincount(rng.integers(0, nf, nf), minlength=nf)
        w = (c[a] * c[b]) * ok
        out[i] = (w * np.where(ok, d, 0)).sum() / w.sum() if w.sum() > 0 else np.nan
    return out


def one_sided_p(obs, null):
    null = np.asarray(null)
    return (1 + int((null >= obs).sum())) / (len(null) + 1)


def holm(ps):
    order = np.argsort(ps)
    m = len(ps)
    out = [0.0] * m
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (m - r) * ps[i]))
        out[i] = run
    return out


def summarize(obs, null):
    null = np.asarray(null)
    sd = null.std(ddof=1)
    return {"observed": float(obs), "null_mean": float(null.mean()), "null_p2.5": float(np.percentile(null, 2.5)),
            "null_p97.5": float(np.percentile(null, 97.5)), "z": float((obs - null.mean()) / sd) if sd > 0 else None,
            "p_one_sided": one_sided_p(obs, null), "n_draws": int(len(null))}


# ------------------------------------------------------------------ inputs
def load_inputs():
    for p, h in INPUT_SHA.items():
        if sha(p) != h:
            raise SystemExit(f"input changed since amendment: {p}")
    designs = json.loads(SETS.read_text())["designs"]
    rows = list(csv.DictReader(open(TIMELINE)))
    cov = defaultdict(lambda: defaultdict(list))
    for r in csv.DictReader(open(COV)):
        y = int(r["year"])
        if 1990 <= y <= 2020:
            for name, col in (("ideology", "citizen_ideology"), ("ln_income", "ln_pcpi_real_2024usd"), ("urban_share", "urban_share"),
                              ("dem_share", "dem_unified_jan1")):
                if r[col] not in ("", "NA"):
                    cov[r["state"]][name].append(float(r[col]))
    covm = {s: {k: float(np.mean(v)) for k, v in d.items() if v} for s, d in cov.items()}
    return designs, rows, covm


def matrices(designs, states):
    sidx = {s: i for i, s in enumerate(states)}
    M = np.zeros((len(designs), len(states)), bool)
    X = np.zeros_like(M)
    for k, d in enumerate(designs):
        for s in d["U"]:
            M[k, sidx[s]] = True
        for s in d["T"]:
            X[k, sidx[s]] = True
    return X, M


def representatives(designs):
    best = {}
    for d in sorted(designs, key=lambda d: d["design_id"]):
        best.setdefault(d["policy_family"], d)
    return list(best.values())


def cov_vector(states_kept, covm, name):
    return np.array([covm.get(s, {}).get(name, np.nan) for s in states_kept])


# ------------------------------------------------------------------ arms
def arm_a(designs, states, rng, D):
    out = {}
    for label, ds in (("primary_family_representative", representatives(designs)), ("secondary_all_designs", designs)):
        X, M = matrices(ds, states)
        obs, keep = share_variance(X, M, MIN_ROWS)
        null = share_variance(draw_T(M, X.sum(1), D, rng), M, MIN_ROWS)[0]
        s = summarize(obs, null)
        s.update({"n_designs": len(ds), "n_families": len({d["policy_family"] for d in ds}), "n_states": int(keep.sum())})
        out[label] = s
    return out


def arm_b(designs, states, covm, rng, D):
    ds_all = [d for d in designs if len(d["T"]) >= 8]
    out = {}
    for label, ds in (("primary_family_representative", representatives(ds_all)), ("secondary_all_designs", ds_all)):
        X, M = matrices(ds, states)
        r_obs, v_obs, keep = first_svd(X, M, MIN_ROWS)
        kept_states = [s for s, k in zip(states, keep) if k]
        Xn = draw_T(M, X.sum(1), D, rng)
        r_null, v_null, _ = first_svd(Xn, M, MIN_ROWS)
        res = {"n_designs": len(ds), "n_families": len({d["policy_family"] for d in ds}), "n_states": int(keep.sum()),
               "sigma1_share": summarize(r_obs, r_null), "covariate_abs_spearman": {}}
        for name in COV_NAMES:
            c = cov_vector(kept_states, covm, name)
            ok = ~np.isnan(c)
            o = abs(float(spearman_rows(v_obs[ok][None, :], c[ok])[0]))
            nl = np.abs(spearman_rows(v_null[:, ok], c[ok]))
            res["covariate_abs_spearman"][name] = summarize(o, nl) | {"n_states": int(ok.sum())}
        out[label] = res
    return out


def arm_c(designs, rows, covm, rng, D, n_boot):
    fam = {d["design_id"]: d["policy_family"] for d in designs}
    ad = defaultdict(dict)
    for r in rows:
        if r["kind"] == "adopter" and r["onset_operative_year"] not in ("", "NA") and r["design_id"] in fam:
            ad[r["design_id"]][r["state"]] = float(r["onset_operative_year"])
    ids = sorted(k for k, v in ad.items() if len(v) >= 5)
    states_all = sorted({s for k in ids for s in ad[k]})
    sidx = {s: i for i, s in enumerate(states_all)}
    # permuted years: row 0 observed, rows 1..D shuffles across the design's adopter states
    Y, A = {}, {}
    for k in ids:
        st = sorted(ad[k])
        A[k] = st
        y = np.array([ad[k][s] for s in st])
        perm = np.argsort(rng.random((D, len(y))), axis=1)
        Y[k] = np.vstack([y[None, :], y[perm]])
    pairs, same_fam = [], 0
    for p, q in itertools.combinations(ids, 2):
        sh = sorted(set(A[p]) & set(A[q]))
        if len(sh) < 4:
            continue
        if fam[p] == fam[q]:
            same_fam += 1
            continue
        pairs.append((p, q, [A[p].index(s) for s in sh], [A[q].index(s) for s in sh], len(sh)))
    tau, gam = pair_stats(Y, [(p, q, ip, iq) for p, q, ip, iq, _ in pairs])
    mean_tau = np.nanmean(tau, 1)
    mean_gam = np.nanmean(gam, 1)
    res = {"n_designs": len(ids), "n_families": len({fam[k] for k in ids}), "n_states": len(states_all), "n_pairs_used": len(pairs),
           "n_pairs_ge6": sum(1 for p in pairs if p[4] >= 6), "n_same_family_pairs_excluded": same_fam,
           "mean_tau": summarize(mean_tau[0], mean_tau[1:]), "mean_gamma": summarize(mean_gam[0], mean_gam[1:])}
    families = sorted({fam[k] for k in ids})
    boot = family_boot_diff(tau[0], np.nanmean(tau[1:], 0), [(fam[p[0]], fam[p[1]]) for p in pairs], families, n_boot, rng)
    res["mean_tau"]["obs_minus_null_family_boot_95"] = [float(np.nanpercentile(boot, 2.5)), float(np.nanpercentile(boot, 97.5))]
    # earliness: mean scaled rank per state over designs; draws share the permutation rows
    R = np.full((D + 1, len(ids), len(states_all)), np.nan)
    for j, k in enumerate(ids):
        sr = scaled_midrank(Y[k])
        for c, s in enumerate(A[k]):
            R[:, j, sidx[s]] = sr[:, c]
    cnt = (~np.isnan(R[0])).sum(0)
    keep = cnt >= MIN_EARLY
    E = np.nanmean(R[:, :, keep], 1)
    kept_states = [s for s, kp in zip(states_all, keep) if kp]
    ev = E.var(1)
    res["earliness"] = {"n_states": int(keep.sum()), "variance": summarize(ev[0], ev[1:])}
    res["earliness"]["profile_abs_spearman"] = {}
    for name in COV_NAMES:
        c = cov_vector(kept_states, covm, name)
        ok = ~np.isnan(c)
        r_ = spearman_rows(E[:, ok], c[ok])
        res["earliness"]["profile_abs_spearman"][name] = summarize(abs(r_[0]), np.abs(r_[1:])) | {"signed_observed": float(r_[0]), "n_states": int(ok.sum())}
    order = np.argsort(E[0])
    res["earliness"]["earliest_5"] = [[kept_states[i], round(float(E[0][i]), 3), int(cnt[keep][i])] for i in order[:5]]
    res["earliness"]["latest_5"] = [[kept_states[i], round(float(E[0][i]), 3), int(cnt[keep][i])] for i in order[::-1][:5]]
    return res, pairs, tau[0], gam[0], list(zip(kept_states, E[0], cnt[keep]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OUT))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    designs, rows, covm = load_inputs()
    ev = [d for d in designs if d["eval_n1_spec"]]
    states = sorted({s for d in ev for s in d["U"]} | {s for d in ev for s in d["T"]})
    da, db, dc, nb = (200, 200, 200, 100) if a.quick else (D_A, D_B, D_C, N_BOOT)
    rng = np.random.default_rng(SEED)
    A = arm_a(ev, states, rng, da)
    B = arm_b(ev, states, covm, rng, db)
    C, pairs, tau0, gam0, early = arm_c(designs, rows, covm, rng, dc, nb)
    p_prim = [A["primary_family_representative"]["p_one_sided"], B["primary_family_representative"]["sigma1_share"]["p_one_sided"],
              C["mean_tau"]["p_one_sided"]]
    ci = C["mean_tau"]["obs_minus_null_family_boot_95"]
    verdict = {"A_variance": p_prim[0] < 0.05, "B_sigma1": p_prim[1] < 0.05, "C_tau": bool(p_prim[2] < 0.05 and ci[0] > 0)}
    res = {"rule": "EX-1 exploratory", "prereg": str(PREREG.relative_to(ROOT)), "prereg_sha256": sha(PREREG), "seed": SEED,
           "inputs_sha256": {str(p.relative_to(ROOT)): h for p, h in INPUT_SHA.items()}, "quick": a.quick,
           "A_overdispersion_of_treated_share": A, "B_first_singular_vector": B, "C_adoption_order_concordance": C,
           "primary_p": dict(zip(["A", "B", "C"], p_prim)), "primary_p_holm": dict(zip(["A", "B", "C"], holm(p_prim))), "verdict_finds_repetition": verdict,
           "seconds": round(time.time() - t0, 1)}
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "RESULTS_REPEAT.json").write_text(json.dumps(res, indent=1))
    with open(out / "pairs_tau.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["design_p", "design_q", "n_shared", "tau", "gamma"])
        for pr, t_, g_ in zip(pairs, tau0, gam0):
            w.writerow([pr[0], pr[1], pr[4], "" if np.isnan(t_) else round(float(t_), 4), "" if np.isnan(g_) else round(float(g_), 4)])
    with open(out / "state_earliness.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["state", "earliness_mean_scaled_rank", "n_designs"])
        for s, e, c in early:
            w.writerow([s, round(float(e), 4), int(c)])
    print(json.dumps({k: res[k] for k in ("primary_p", "primary_p_holm", "verdict_finds_repetition", "seconds")}, indent=1))


if __name__ == "__main__":
    main()
