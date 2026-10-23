#!/usr/bin/env python3
"""NBR (rule id EX-1, exploratory): does adoption hazard rise with the share of land-border neighbours already in force?

Rules: docs/plans/l3-meta/PREREG_AMEND_20261020_NBR.md (+ _HASH.txt).  Pooled over the L2 designs of risk_state_year_v2.csv.
Run: VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_l3n_nbr.py [--n1 1000 --n2 500 --boot 2000 --procs 10]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_NBR.md"
OUT = ROOT / "data/l3_meta/l3n_nbr_20261020"
RISK = ROOT / "data/l3_meta/party_coding/risk_state_year_v2.csv"
TIMELINE = ROOT / "data/l3_meta/party_coding/design_state_timeline_v2.csv"
SUMMARY = ROOT / "data/l3_meta/party_coding/design_summary_v2.csv"
ADJ = ROOT / "data/l3_meta/covariates/state_adjacency_v2.csv"
SEEDS = dict(boot=20261201, n1=20261202, n2=20261203)
MIN_EVENTS = 5
Y0, Y1 = 1965, 2030          # year grid of the rebuilt status table


# ---------------------------------------------------------------------------------------------
# logistic core
# ---------------------------------------------------------------------------------------------
def fit_logit(X, y, ridge, beta0=None, maxit=50, tol=1e-9):
    """Newton for a ridge-penalised logistic model. Returns beta, converged, p."""
    n, k = X.shape
    b = np.zeros(k) if beta0 is None else beta0.copy()
    R = np.diag(ridge)
    pen_old = None
    ok = False
    for _ in range(maxit):
        eta = np.clip(X @ b, -30, 30)
        p = 1 / (1 + np.exp(-eta))
        w = np.maximum(p * (1 - p), 1e-12)
        g = X.T @ (y - p) - ridge * b
        H = (X * w[:, None]).T @ X + R
        step = np.linalg.solve(H, g)
        b = b + step
        if np.max(np.abs(step)) < tol * max(1.0, np.max(np.abs(b))):
            ok = True
            break
    eta = np.clip(X @ b, -30, 30)
    return b, ok, 1 / (1 + np.exp(-eta))


def hessian_inv(X, p, ridge):
    w = np.maximum(p * (1 - p), 1e-12)
    return np.linalg.inv((X * w[:, None]).T @ X + np.diag(ridge))


def cr1_se(X, y, p, Hinv, groups, j):
    """Cluster-robust (CR1) standard error of coefficient j."""
    u = X * (y - p)[:, None]
    order = np.argsort(groups, kind="stable")
    gs = groups[order]
    cuts = np.flatnonzero(np.r_[True, gs[1:] != gs[:-1]])
    S = np.add.reduceat(u[order], cuts, axis=0)
    G = len(cuts)
    n, k = X.shape
    meat = S.T @ S
    V = Hinv @ meat @ Hinv * (G / (G - 1.0)) * ((n - 1.0) / max(n - k, 1.0))
    return float(np.sqrt(V[j, j]))


def design_matrix(des, yr, covs, years):
    """Columns: design dummies | year dummies (first year dropped) | covariates (nb first). Returns X, ridge, index of nb."""
    ud, di = np.unique(des, return_inverse=True)
    uy = np.asarray(years)
    yi = np.searchsorted(uy, yr)
    n = len(des)
    nd, ny = len(ud), len(uy) - 1
    k = nd + ny + covs.shape[1]
    X = np.zeros((n, k))
    X[np.arange(n), di] = 1.0
    m = yi > 0
    X[np.flatnonzero(m), nd + yi[m] - 1] = 1.0
    X[:, nd + ny:] = covs
    ridge = np.r_[np.full(nd, 1e-8), np.full(ny + covs.shape[1], 1e-6)]
    return X, ridge, nd + ny


def fit_m1(des, yr, y, covs, years, groups, se=True, beta0=None):
    X, ridge, j = design_matrix(des, yr, covs, years)
    b, ok, p = fit_logit(X, y, ridge, beta0)
    out = dict(b=float(b[j]), ok=bool(ok), beta=b)
    if se:
        Hinv = hessian_inv(X, p, ridge)
        out["se"] = cr1_se(X, y, p, Hinv, groups, j)
    return out


# ---------------------------------------------------------------------------------------------
# status table and neighbour shares
# ---------------------------------------------------------------------------------------------
def build_status(tl, designs, states):
    """F (in force at Jan1 of year), K (known) arrays of shape (design, state, year grid)."""
    sidx = {s: i for i, s in enumerate(states)}
    didx = {d: i for i, d in enumerate(designs)}
    ny = Y1 - Y0 + 1
    F = np.zeros((len(designs), len(states), ny))
    K = np.zeros_like(F)
    t = np.arange(Y0, Y1 + 1)
    for r in tl.itertuples(index=False):
        if r.design_id not in didx or r.state not in sidx:
            continue
        d, s = didx[r.design_id], sidx[r.state]
        if r.kind == "left_censored":
            F[d, s, :] = 1; K[d, s, :] = 1
        elif r.kind == "adopter":
            F[d, s, :] = (t > r.onset_operative_year); K[d, s, :] = 1
        elif r.kind == "never":
            K[d, s, :] = 1
    return F, K


def rebuilt_share(A, F, K, di, si, yi):
    """Neighbour share for rows (design idx, state idx, year idx) under graph A (state x state 0/1)."""
    nd, ns, ny = F.shape
    Fm = np.transpose(F, (1, 0, 2)).reshape(ns, nd * ny)
    Km = np.transpose(K, (1, 0, 2)).reshape(ns, nd * ny)
    num = (A @ Fm).reshape(ns, nd, ny)
    den = (A @ Km).reshape(ns, nd, ny)
    n = num[si, di, yi]
    d = den[si, di, yi]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(d > 0, n / d, np.nan)


def relabel(A, rng, movable):
    """Permute the state labels of A (only among `movable` indices); keeps degree sequence and shape."""
    pi = np.arange(A.shape[0])
    pi[movable] = rng.permutation(movable)
    B = np.zeros_like(A)
    B[np.ix_(pi, pi)] = A
    return B


def permute_within(y, groups, rng):
    """Permute y within groups (design-year)."""
    r0 = np.argsort(groups, kind="stable")
    r1 = np.lexsort((rng.random(len(y)), groups))
    out = y.copy()
    out[r0] = y[r1]
    return out


# ---------------------------------------------------------------------------------------------
# per-design fits and random effects
# ---------------------------------------------------------------------------------------------
def design_fit(yrs, y, nb, other):
    """Logit: intercept, centred year, nb, other covariates (ridge 1e-4). Returns b_nb, var, ok."""
    t = (yrs - yrs.mean()) / max(yrs.std(), 1.0)
    X = np.column_stack([np.ones(len(y)), t, nb, other])
    ridge = np.r_[0.0, np.full(X.shape[1] - 1, 1e-4)]
    b, ok, p = fit_logit(X, y.astype(float), ridge, maxit=30)
    H = hessian_inv(X, p, ridge)
    return float(b[2]), float(H[2, 2]), bool(ok)


def dersimonian_laird(est, var):
    est, var = np.asarray(est, float), np.asarray(var, float)
    w = 1 / var
    mu0 = np.sum(w * est) / np.sum(w)
    Q = np.sum(w * (est - mu0) ** 2)
    k = len(est)
    c = np.sum(w) - np.sum(w ** 2) / np.sum(w)
    tau2 = max(0.0, (Q - (k - 1)) / c) if c > 0 else 0.0
    ws = 1 / (var + tau2)
    mu = np.sum(ws * est) / np.sum(ws)
    se = np.sqrt(1 / np.sum(ws))
    return dict(mu=float(mu), se=float(se), tau=float(np.sqrt(tau2)), Q=float(Q), k=int(k))


# ---------------------------------------------------------------------------------------------
# workers (fork: globals set by main)
# ---------------------------------------------------------------------------------------------
G = {}


def _fit_nb(nb, yv, beta0=None):
    covs = G["covs"].copy()
    covs[:, 0] = nb
    return fit_m1(G["des"], G["yr"], yv, covs, G["years"], G["fam"], se=False, beta0=beta0)["b"]


def work_n1(args):
    seed, = args
    rng = np.random.default_rng(seed)
    yv = permute_within(G["y"], G["dygroup"], rng)
    return _fit_nb(G["covs"][:, 0], yv, G["beta0"])


def work_n2(args):
    seed, = args
    rng = np.random.default_rng(seed)
    A2 = relabel(G["A"], rng, G["movable"])
    nb = rebuilt_share(A2, G["F"], G["K"], G["di"], G["si"], G["yi"])
    ok = np.isfinite(nb)
    if not ok.all():       # rows whose neighbours are all unknown get share 0 (same handling as S6)
        nb = np.where(ok, nb, 0.0)
    return _fit_nb(nb, G["y"], G["beta0"])


def work_boot(args):
    seed, = args
    rng = np.random.default_rng(seed)
    fams = G["famlist"]
    pick = rng.choice(len(fams), len(fams), replace=True)
    idx = np.concatenate([G["fam_rows"][i] for i in pick])
    r = fit_m1(G["des"][idx], G["yr"][idx], G["y"][idx], G["covs"][idx], G["years"], G["fam"][idx], se=False)
    return r["b"] if r["ok"] else np.nan


def work_lofo(args):
    f, = args
    idx = np.flatnonzero(G["fam"] != f)
    return fit_m1(G["des"][idx], G["yr"][idx], G["y"][idx], G["covs"][idx], G["years"], G["fam"][idx], se=False)["b"]


def run_pool(fn, args, procs):
    ctx = mp.get_context("fork")
    with ctx.Pool(procs) as pool:
        return pool.map(fn, args, chunksize=1)


# ---------------------------------------------------------------------------------------------
def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def pnull(b, null):
    null = np.asarray(null, float)
    null = null[np.isfinite(null)]
    return dict(B=int(len(null)), mean=float(null.mean()), sd=float(null.std(ddof=1)), z=float((b - null.mean()) / null.std(ddof=1)),
                p_two_sided=float((1 + np.sum(np.abs(null) >= abs(b))) / (1 + len(null))), q025=float(np.quantile(null, .025)), q975=float(np.quantile(null, .975)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n1", type=int, default=1000)
    ap.add_argument("--n2", type=int, default=500)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--procs", type=int, default=10)
    ap.add_argument("--out-dir", default=str(OUT))
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    d = pd.read_csv(RISK)
    tl = pd.read_csv(TIMELINE)
    sm = pd.read_csv(SUMMARY)
    adj = pd.read_csv(ADJ)
    adj = adj[adj.land_border_rule_v1 == 1]
    fam_of = sm.set_index("design_id").policy_family.to_dict()

    need = ["nb_in_force_share_jan1", "dem_unified_jan1", "rep_unified_jan1", "citizen_ideology", "ln_pcpi_real_2024usd"]
    c = d.dropna(subset=need)
    ev = c.groupby("design_id").adopt.sum()
    keep = ev[ev >= MIN_EVENTS].index
    c = c[c.design_id.isin(keep)]
    yev = c.groupby("year").adopt.sum()
    years = np.array(sorted(yev[yev > 0].index))
    c = c[c.year.isin(years)].reset_index(drop=True)
    designs = sorted(c.design_id.unique())
    families = np.array([fam_of[x] for x in c.design_id])
    ideo = (c.citizen_ideology - c.citizen_ideology.mean()) / c.citizen_ideology.std()
    linc = (c.ln_pcpi_real_2024usd - c.ln_pcpi_real_2024usd.mean()) / c.ln_pcpi_real_2024usd.std()
    covs = np.column_stack([c.nb_in_force_share_jan1, c.dem_unified_jan1, c.rep_unified_jan1, ideo, linc, c.fcum_jan1_U])
    G.update(des=c.design_id.to_numpy(), yr=c.year.to_numpy(), y=c.adopt.to_numpy(float), covs=covs, years=years, fam=families)
    G["dygroup"] = pd.factorize(c.design_id + "|" + c.year.astype(str))[0]
    G["famlist"] = sorted(set(families))
    G["fam_rows"] = [np.flatnonzero(families == f) for f in G["famlist"]]
    counts = dict(designs=len(designs), families=len(G["famlist"]), rows=len(c), events=int(c.adopt.sum()), years=len(years))
    print("sample", counts, flush=True)

    res = dict(rule="EX-1 exploratory", prereg=str(PREREG.relative_to(ROOT)), prereg_sha256=sha(PREREG), counts=counts, seeds=SEEDS)

    # M1 primary
    m1 = fit_m1(G["des"], G["yr"], G["y"], covs, years, families)
    G["beta0"] = m1["beta"]
    b, se = m1["b"], m1["se"]
    res["M1"] = dict(b=b, se_cluster=se, ci95=[b - 1.96 * se, b + 1.96 * se], odds_ratio_per_quarter=float(np.exp(0.25 * b)),
                     or_ci95=[float(np.exp(0.25 * (b - 1.96 * se))), float(np.exp(0.25 * (b + 1.96 * se)))], converged=m1["ok"],
                     other_coef=dict(zip(["dem", "rep", "ideology_sd", "ln_income_sd", "fcum"], map(float, m1["beta"][-5:]))))
    print("M1", res["M1"], flush=True)

    # S1 without fcum
    s1 = fit_m1(G["des"], G["yr"], G["y"], covs[:, :5], years, families)
    res["S1_no_fcum"] = dict(b=s1["b"], se_cluster=s1["se"], ci95=[s1["b"] - 1.96 * s1["se"], s1["b"] + 1.96 * s1["se"]])

    # status table, S6, S5
    states = sorted(set(adj.state_a) | set(adj.state_b))
    sidx = {s: i for i, s in enumerate(states)}
    A = np.zeros((len(states), len(states)))
    for r in adj.itertuples():
        A[sidx[r.state_a], sidx[r.state_b]] = A[sidx[r.state_b], sidx[r.state_a]] = 1
    didx = {x: i for i, x in enumerate(designs)}
    F, K = build_status(tl, designs, states)
    di = np.array([didx[x] for x in c.design_id])
    si = np.array([sidx.get(s, -1) for s in c.state])
    yi = c.year.to_numpy() - Y0
    assert (si >= 0).all(), "state missing from adjacency"
    G.update(A=A, F=F, K=K, di=di, si=si, yi=yi, movable=np.arange(len(states)))
    nb_true = rebuilt_share(A, F, K, di, si, yi)
    fin = np.isfinite(nb_true)
    match = float(np.mean(np.abs(nb_true[fin] - c.nb_in_force_share_jan1.to_numpy()[fin]) < 1e-6))
    cov6 = covs.copy()
    cov6[:, 0] = np.where(fin, nb_true, 0.0)
    s6 = fit_m1(G["des"], G["yr"], G["y"], cov6, years, families)
    res["S6_rebuilt_share"] = dict(b=s6["b"], se_cluster=s6["se"], share_finite=float(fin.mean()), match_rate_to_file_share=match, ok=s6["ok"])
    G["b_s6"] = s6["b"]
    lead = rebuilt_share(A, F, K, di, si, np.minimum(yi + 1, Y1 - Y0))
    kl = np.isfinite(lead) & (c.year.to_numpy() + 1 <= 2025)
    cov5 = covs[kl].copy()
    cov5[:, 0] = lead[kl]
    s5 = fit_m1(G["des"][kl], G["yr"][kl], G["y"][kl], cov5, years, families[kl])
    res["S5_future_share"] = dict(b=s5["b"], se_cluster=s5["se"], rows=int(kl.sum()), events=int(G["y"][kl].sum()), ok=s5["ok"])
    print("S1/S5/S6", res["S1_no_fcum"]["b"], s5["b"], s6["b"], flush=True)

    # S3 per design -> family -> random effects
    per = []
    for dname in designs:
        m = (G["des"] == dname)
        nbv = covs[m, 0]
        if m.sum() == 0 or c.adopt[m].sum() < MIN_EVENTS or np.ptp(nbv) == 0:
            per.append(dict(design=dname, family=fam_of[dname], used=False))
            continue
        bb, vv, ok = design_fit(G["yr"][m].astype(float), G["y"][m], nbv, covs[m, 1:5])
        per.append(dict(design=dname, family=fam_of[dname], used=bool(ok), b=bb, var=vv, converged=ok, events=int(G["y"][m].sum())))
    pdf = pd.DataFrame(per)
    u = pdf[pdf.used].copy()
    fam_est, fam_var = [], []
    for f, g in u.groupby("family"):
        n = len(g)
        fam_est.append(g.b.mean()); fam_var.append(g["var"].sum() / n ** 2)
    re = dersimonian_laird(fam_est, fam_var)
    re["share_families_positive"] = float(np.mean(np.array(fam_est) > 0))
    re["designs_used"] = int(len(u)); re["designs_not_used"] = [x["design"] for x in per if not x["used"]]
    re["design_mean_b_unweighted"] = float(u.b.mean()); re["design_share_positive"] = float((u.b > 0).mean())
    re["ci95"] = [re["mu"] - 1.96 * re["se"], re["mu"] + 1.96 * re["se"]]
    res["S3_per_design_family_RE"] = re
    pdf.to_csv(out / "per_design_nb_coef.csv", index=False)
    print("S3", re, flush=True)

    # leave one family out
    lo = run_pool(work_lofo, [(f,) for f in G["famlist"]], a.procs)
    res["leave_one_family_out"] = dict(min=float(np.min(lo)), max=float(np.max(lo)), n=len(lo), n_positive=int(np.sum(np.array(lo) > 0)))
    pd.DataFrame(dict(family_left_out=G["famlist"], b=lo)).to_csv(out / "leave_one_family_out.csv", index=False)

    # family bootstrap
    bs = np.array(run_pool(work_boot, [(SEEDS["boot"] + i,) for i in range(a.boot)], a.procs))
    bs = bs[np.isfinite(bs)]
    res["M1"]["bootstrap_ci95_family_block"] = [float(np.quantile(bs, .025)), float(np.quantile(bs, .975))]
    res["M1"]["bootstrap_n"] = int(len(bs))
    print("boot", res["M1"]["bootstrap_ci95_family_block"], time.time() - t0, flush=True)

    # nulls
    n1 = np.array(run_pool(work_n1, [(SEEDS["n1"] + i,) for i in range(a.n1)], a.procs))
    res["N1_adoption_permutation"] = pnull(b, n1)
    print("N1", res["N1_adoption_permutation"], time.time() - t0, flush=True)
    G["beta0"] = s6["beta"]
    n2 = np.array(run_pool(work_n2, [(SEEDS["n2"] + i,) for i in range(a.n2)], a.procs))
    res["N2_graph_relabel"] = pnull(s6["b"], n2)
    res["N2_graph_relabel"]["reference"] = "rebuilt-share coefficient under the true graph (S6)"
    res["N2_graph_relabel"]["z_of_file_share_b"] = float((b - n2.mean()) / n2.std(ddof=1))
    L = max(len(n1), len(n2))
    np.savetxt(out / "null_draws.csv", np.column_stack([np.pad(n1, (0, L - len(n1)), constant_values=np.nan), np.pad(n2, (0, L - len(n2)), constant_values=np.nan)]),
               delimiter=",", header="n1_adoption_permutation,n2_graph_relabel", comments="")
    print("N2", res["N2_graph_relabel"], time.time() - t0, flush=True)

    pos = b > 0
    sig = (b - 1.96 * se > 0) if pos else (b + 1.96 * se < 0)
    pp = res["N1_adoption_permutation"]["p_two_sided"] <= 0.05 and res["N2_graph_relabel"]["p_two_sided"] <= 0.05
    res["decision"] = ("present (positive)" if pos else "present (negative)") if (sig and pp) else "not detected"
    res["decision_parts"] = dict(sign_positive=bool(pos), cluster_ci_excludes_0=bool(sig), both_permutation_p_le_0_05=bool(pp))
    res["code"] = {f: sha(ROOT / f) for f in ("scripts/l3_meta/build_l3n_nbr.py", "scripts/l3_meta/test_l3n_nbr.py")}
    res["inputs_sha256"] = {str(p.relative_to(ROOT)): sha(p) for p in (RISK, TIMELINE, SUMMARY, ADJ)}
    res["seconds"] = time.time() - t0
    (out / "RESULTS_NBR.json").write_text(json.dumps(res, indent=2, default=float))
    print("decision", res["decision"], flush=True)


if __name__ == "__main__":
    main()
