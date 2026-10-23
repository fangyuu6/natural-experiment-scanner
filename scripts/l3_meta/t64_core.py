"""T64-SIGNED-V1 numerical core (pure functions; no file access).  See docs/plans/l3-meta/PREREG_T64_SIGNED_PARTY_V1.md sections 2, 4, 5.

  build_rows        risk rows of the DK panel merged with state-year politics
  design / fit_logit  discrete-time logit with policy and year effects (sparse Newton), state-clustered covariance
  firth_logit       Firth-penalised logit for one policy
  reml              random-effects mean and tau by REML
"""
from __future__ import annotations

import math

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import minimize_scalar

Y0, Y1 = 1961, 2016
DUR_BINS = ((0, 4), (5, 14), (15, 10 ** 6))
DENSE_BASE = ["ideo", "lninc", "urban", "nbr", "dur1", "dur2", "dur3"]


# ---------------------------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------------------------
def build_rows(panel, politics, adjacency, y0=Y0, y1=Y1):
    """panel: DataFrame(policy, state, year, adopt_year) of the DK panel.  politics: DataFrame(state, year, dem_unified_jan1, rep_unified_jan1,
    citizen_ideology, ln_pcpi_real_2024usd, urban_share).  adjacency: iterable of (state_a, state_b) land-border pairs.
    Returns (rows DataFrame, counts dict).  Columns: policy, state, year, y, dem, ideo, lninc, urban, nbr, dur1..dur3 (unstandardised)."""
    import pandas as pd
    states = sorted(panel["state"].unique())
    sidx = {s: i for i, s in enumerate(states)}
    W = np.zeros((len(states), len(states)))
    for a, b in adjacency:
        if a in sidx and b in sidx:
            W[sidx[a], sidx[b]] = W[sidx[b], sidx[a]] = 1.0
    deg = W.sum(axis=1)
    Wn = np.divide(W, deg[:, None], out=np.zeros_like(W), where=deg[:, None] > 0)
    counts = {"panel_rows": int(len(panel))}
    p = panel[(panel["year"] >= y0) & (panel["year"] <= y1)]
    risk = p[p["adopt_year"].isna() | (p["year"] <= p["adopt_year"])].copy()
    counts["risk_rows_window"] = int(len(risk))
    risk["y"] = ((risk["year"] == risk["adopt_year"])).astype(int)
    # earliest adoption of the policy in any state (whole panel, also before the window)
    first = panel.groupby("policy")["adopt_year"].min()
    risk["first_adopt"] = risk["policy"].map(first)
    # neighbour share, per policy
    A = {}
    for pol, g in panel.groupby("policy"):
        a = np.full(len(states), np.inf)
        gg = g.drop_duplicates("state")
        for s, ay in zip(gg["state"], gg["adopt_year"]):
            if ay == ay:
                a[sidx[s]] = ay
        A[pol] = a
    nbr = np.zeros(len(risk))
    pol_arr, st_arr, yr_arr = risk["policy"].to_numpy(), risk["state"].to_numpy(), risk["year"].to_numpy()
    order = np.argsort(pol_arr, kind="stable")
    start = 0
    while start < len(order):
        pol = pol_arr[order[start]]
        end = start
        while end < len(order) and pol_arr[order[end]] == pol:
            end += 1
        idx = order[start:end]
        before = (A[pol][None, :] < yr_arr[idx][:, None]).astype(float)
        srow = np.array([sidx[s] for s in st_arr[idx]])
        nbr[idx] = (before * Wn[srow]).sum(axis=1)
        start = end
    risk["nbr"] = nbr
    # duration bins at t-1
    k = (risk["year"] - 1 - risk["first_adopt"]).to_numpy()
    for j, (lo, hi) in enumerate(DUR_BINS, start=1):
        risk[f"dur{j}"] = ((k >= lo) & (k <= hi) & np.isfinite(k)).astype(float)
    # politics
    pol_ = politics.copy()
    pol_["dem"] = pol_["dem_unified_jan1"] - pol_["rep_unified_jan1"]
    cur = pol_[["state", "year", "dem", "urban_share"]].rename(columns={"urban_share": "urban"})
    lag = pol_[["state", "year", "citizen_ideology", "ln_pcpi_real_2024usd"]].copy()
    lag["year"] = lag["year"] + 1
    lag = lag.rename(columns={"citizen_ideology": "ideo", "ln_pcpi_real_2024usd": "lninc"})
    r = risk.merge(cur, on=["state", "year"], how="left").merge(lag, on=["state", "year"], how="left")
    keep = ["dem", "urban", "ideo", "lninc"]
    counts["missing_covariate_rows"] = int(r[keep].isna().any(axis=1).sum())
    r = r.dropna(subset=keep).reset_index(drop=True)
    counts["rows_complete"] = int(len(r))
    counts["events_complete"] = int(r["y"].sum())
    return r[["policy", "state", "year", "y", "dem", "ideo", "lninc", "urban", "nbr", "dur1", "dur2", "dur3"]], counts


def standardise(rows, cols=("ideo", "lninc", "urban")):
    out = rows.copy()
    for c in cols:
        sd = out[c].std(ddof=0)
        out[c] = (out[c] - out[c].mean()) / (sd if sd > 0 else 1.0)
    return out


# ---------------------------------------------------------------------------------------------
# pooled logit with policy and year effects
# ---------------------------------------------------------------------------------------------
def design(dense, policy_codes, year_codes, n_policy, n_year):
    """dense: (n, k) array; policy_codes in 0..n_policy-1; year_codes in 0..n_year-1 (the first year is the reference).  Returns a CSR matrix."""
    n = dense.shape[0]
    rows = np.arange(n)
    P = sp.csr_matrix((np.ones(n), (rows, policy_codes)), shape=(n, n_policy))
    m = year_codes > 0
    Yd = sp.csr_matrix((np.ones(int(m.sum())), (rows[m], year_codes[m] - 1)), shape=(n, max(n_year - 1, 0)))
    return sp.hstack([sp.csr_matrix(dense), P, Yd], format="csr")


def fit_logit(X, y, beta0=None, max_iter=50, tol=1e-9, ridge=1e-8):
    """Unpenalised logit by Newton with step halving.  Returns dict(beta, p, bread_inv, loglik, iters, converged)."""
    n, k = X.shape
    beta = np.zeros(k) if beta0 is None else np.array(beta0, float)
    if beta0 is None:
        beta[:] = 0.0
    Xt = X.T.tocsr()

    def ll(b):
        eta = X @ b
        return float(np.sum(y * eta - np.logaddexp(0.0, eta)))

    cur = ll(beta)
    converged = False
    for it in range(1, max_iter + 1):
        eta = X @ beta
        p = 1.0 / (1.0 + np.exp(-eta))
        w = p * (1 - p)
        g = Xt @ (y - p)
        H = (Xt @ sp.diags(w) @ X).toarray()
        H[np.diag_indices(k)] += ridge
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        t = 1.0
        while t > 1e-6:
            new = ll(beta + t * step)
            if new >= cur - 1e-12:
                break
            t /= 2
        beta = beta + t * step
        if abs(new - cur) < tol * (1 + abs(cur)) and np.max(np.abs(t * step)) < 1e-7:
            cur, converged = new, True
            break
        cur = new
    eta = X @ beta
    p = 1.0 / (1.0 + np.exp(-eta))
    w = p * (1 - p)
    H = (Xt @ sp.diags(w) @ X).toarray()
    H[np.diag_indices(k)] += ridge
    return dict(beta=beta, p=p, H=H, loglik=cur, iters=it, converged=converged)


def cluster_cov(X, y, fit, clusters):
    """CR1 state-cluster covariance of the full coefficient vector."""
    n, k = X.shape
    r = y - fit["p"]
    codes = np.unique(clusters, return_inverse=True)[1]
    G = codes.max() + 1
    S = sp.csr_matrix((np.ones(n), (codes, np.arange(n))), shape=(G, n)) @ X.multiply(r[:, None]).tocsr()
    S = S.toarray()
    meat = S.T @ S
    bread = np.linalg.pinv(fit["H"])
    V = bread @ meat @ bread
    return V * (G / (G - 1)) * ((n - 1) / (n - k))


# ---------------------------------------------------------------------------------------------
# Firth logit for one policy
# ---------------------------------------------------------------------------------------------
def firth_logit(X, y, max_iter=60, tol=1e-8):
    """Firth-penalised logit (Jeffreys prior).  X dense with an intercept column.  Returns (beta, cov, converged)."""
    n, k = X.shape
    beta = np.zeros(k)
    beta[0] = math.log((y.sum() + 0.5) / (n - y.sum() + 0.5))
    converged = False
    for _ in range(max_iter):
        eta = X @ beta
        p = 1 / (1 + np.exp(-np.clip(eta, -30, 30)))
        w = np.clip(p * (1 - p), 1e-12, None)
        XtWX = X.T @ (X * w[:, None])
        try:
            inv = np.linalg.inv(XtWX)
        except np.linalg.LinAlgError:
            return beta, np.full((k, k), np.nan), False
        h = w * np.einsum("ij,jk,ik->i", X, inv, X)
        U = X.T @ (y - p + h * (0.5 - p))
        step = inv @ U
        m = np.max(np.abs(step))
        if m > 5:
            step *= 5 / m
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            converged = True
            break
    eta = X @ beta
    p = 1 / (1 + np.exp(-np.clip(eta, -30, 30)))
    w = np.clip(p * (1 - p), 1e-12, None)
    try:
        cov = np.linalg.inv(X.T @ (X * w[:, None]))
    except np.linalg.LinAlgError:
        return beta, np.full((k, k), np.nan), False
    return beta, cov, converged


# ---------------------------------------------------------------------------------------------
# random effects
# ---------------------------------------------------------------------------------------------
def reml(theta, se):
    """Random-effects mean and tau by REML.  Returns dict(mu, tau, mu_se, Q, df, n, boundary)."""
    th, v = np.asarray(theta, float), np.asarray(se, float) ** 2
    n = len(th)

    def nll(tau):
        t2 = tau * tau
        w = 1.0 / (v + t2)
        mu = np.sum(w * th) / np.sum(w)
        return 0.5 * (np.sum(np.log(v + t2)) + math.log(np.sum(w)) + np.sum(w * (th - mu) ** 2))

    res = minimize_scalar(nll, bounds=(0.0, 5.0), method="bounded", options=dict(xatol=1e-8))
    tau = float(res.x)
    if nll(0.0) <= nll(tau) + 1e-12:
        tau = 0.0
    w = 1.0 / (v + tau * tau)
    mu = float(np.sum(w * th) / np.sum(w))
    w0 = 1.0 / v
    mu0 = np.sum(w0 * th) / np.sum(w0)
    Q = float(np.sum(w0 * (th - mu0) ** 2))
    return dict(mu=mu, tau=tau, mu_se=float(math.sqrt(1.0 / np.sum(w))), Q=Q, df=n - 1, n=n, boundary=bool(tau == 0.0))


def fixed_mean(theta, se):
    th, w = np.asarray(theta, float), 1.0 / np.asarray(se, float) ** 2
    return float(np.sum(w * th) / np.sum(w))
