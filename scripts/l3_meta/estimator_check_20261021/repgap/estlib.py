"""EXPLORATORY (T227). Estimator library for the REPGAP estimator check. Own code, numpy/pandas only.

Estimators (binary, absorbing-treatment panel unless noted):
  twfe(...)       weighted two-way FE with optional covariates and unit-specific linear trends, CR1 cluster SE
  imputation(...) Borusyak-Jaravel-Spiess style: fit on untreated obs (same FE/covariates/trends/weights), impute, average
  cs(...)         Callaway-Sant'Anna group-time ATT, not-yet-treated (plus never-treated) controls, no covariates;
                  'simple' = weights proportional to cohort size over all post cells; 'group' = cohort-average then cohort-size weights
  sa(...)         Sun-Abraham interaction-weighted (never-treated, else last-treated cohort, as control), unit+time FE
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- helpers
def _dummies(codes, drop_first=True):
    k = int(codes.max()) + 1
    M = np.zeros((len(codes), k))
    M[np.arange(len(codes)), codes] = 1.0
    return M[:, 1:] if drop_first else M


def _codes(s):
    return pd.factorize(s)[0]


def design(df, unit, time, controls=(), trend=False, extra=None):
    """Return X (no constant column besides intercept via unit dummies), names."""
    u = _codes(df[unit]); t = _codes(df[time])
    parts = [_dummies(u, drop_first=False), _dummies(t, drop_first=True)]
    if trend:
        tt = (df[time].astype(float) - df[time].astype(float).min()).to_numpy()
        # unit-specific linear trends for every unit (min-norm lstsq handles collinearity with unit FE of 1-obs units)
        tu = u if trend is True else _codes(df[trend])
        parts.append(_dummies(tu, drop_first=False) * tt[:, None])
    if len(controls):
        parts.append(df[list(controls)].to_numpy(float))
    if extra is not None:
        parts.append(extra)
    return np.column_stack(parts)


def wls(y, X, w=None):
    if w is None:
        w = np.ones(len(y))
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    return beta


def cluster_vcov(y, X, w, beta, cl, rank=None):
    if w is None:
        w = np.ones(len(y))
    sw = np.sqrt(w)
    Xw = X * sw[:, None]
    res = (y - X @ beta) * sw
    bread = np.linalg.pinv(Xw.T @ Xw)
    cl = np.asarray(cl)
    G = len(np.unique(cl))
    S = np.zeros((G, X.shape[1]))
    np.add.at(S, pd.factorize(cl)[0], Xw * res[:, None])
    meat = S.T @ S
    n = len(y); r = np.linalg.matrix_rank(Xw) if rank is None else rank
    f = G / (G - 1) * (n - 1) / (n - r)
    return f * bread @ meat @ bread


# ---------------------------------------------------------------- TWFE
def twfe(df, y, unit, time, d, w=None, controls=(), trend=False, cluster=None, return_all=False):
    X = np.column_stack([df[d].to_numpy(float), design(df, unit, time, controls, trend)])
    yy = df[y].to_numpy(float)
    ww = None if w is None else df[w].to_numpy(float)
    beta = wls(yy, X, ww)
    out = {"est": float(beta[0])}
    cl = df[cluster if cluster else unit]
    V = cluster_vcov(yy, X, ww, beta, cl)
    out["se"] = float(np.sqrt(V[0, 0]))
    out["n"] = int(len(df))
    return out


# ---------------------------------------------------------------- cohorts
def add_cohort(df, unit, time, d):
    """first treated period G (np.inf if never); flag non-absorbing."""
    df = df.copy()
    first = df[df[d] > 0].groupby(unit)[time].min()
    df["G"] = df[unit].map(first).fillna(np.inf)
    # absorbing check
    bad = 0
    for _, g in df.sort_values(time).groupby(unit):
        v = (g[d].to_numpy() > 0).astype(int)
        if len(v) and np.any(np.diff(v) < 0):
            bad += 1
    return df, bad


# ---------------------------------------------------------------- imputation
def imputation(df, y, unit, time, d, w=None, controls=(), trend=False, tol=1e-7):
    X = design(df, unit, time, controls, trend)
    yy = df[y].to_numpy(float)
    ww = np.ones(len(df)) if w is None else df[w].to_numpy(float)
    treated = (df[d].to_numpy(float) > 0)
    Xu, yu, wu = X[~treated], yy[~treated], ww[~treated]
    beta = wls(yu, Xu, wu)
    # estimability: x in rowspace(Xu)
    U, s, Vt = np.linalg.svd(Xu * np.sqrt(wu)[:, None], full_matrices=False)
    r = int((s > s.max() * 1e-9).sum())
    Vr = Vt[:r].T
    Xt = X[treated]
    resid = Xt - (Xt @ Vr) @ Vr.T
    ok = np.linalg.norm(resid, axis=1) <= tol * max(1.0, np.linalg.norm(X, axis=1).max())
    tau = yy[treated] - Xt @ beta
    wt = ww[treated]
    if ok.sum() == 0:
        return {"est": np.nan, "n_treated": int(treated.sum()), "n_used": 0}
    est = float((tau[ok] * wt[ok]).sum() / wt[ok].sum())
    return {"est": est, "n_treated": int(treated.sum()), "n_used": int(ok.sum())}


# ---------------------------------------------------------------- Callaway-Sant'Anna (no covariates)
def cs(df, y, unit, time, d, w=None, agg="simple"):
    """Group-time ATT with not-yet-treated (G > t) and never-treated controls. Unit weight = mean of w over unit."""
    df, _ = add_cohort(df, unit, time, d)
    periods = np.sort(df[time].unique())
    Y = df.pivot(index=unit, columns=time, values=y)
    G = df.groupby(unit)["G"].first().reindex(Y.index)
    if w is None:
        W = pd.Series(1.0, index=Y.index)
    else:
        W = df.groupby(unit)[w].mean().reindex(Y.index)
    cells = []
    cohorts = sorted([g for g in G.unique() if np.isfinite(g)])
    for g in cohorts:
        if g <= periods[0]:
            continue  # no pre-period
        tr = (G == g).to_numpy()
        cand = [p for p in periods[periods < g] if np.isfinite(Y[p].to_numpy()[tr]).all()]
        if not cand:
            continue
        base = max(cand)  # latest pre-period observed for every unit of the cohort (handles dropped transition years)
        for t in periods[periods >= g]:
            ctrl = (G > t).to_numpy()  # not yet treated at t (includes never)
            if ctrl.sum() == 0:
                continue
            dy = (Y[t] - Y[base]).to_numpy()
            m_t = np.isfinite(dy) & tr
            m_c = np.isfinite(dy) & ctrl
            if m_t.sum() == 0 or m_c.sum() == 0:
                continue
            wt, wc = W.to_numpy()[m_t], W.to_numpy()[m_c]
            att = np.average(dy[m_t], weights=wt) - np.average(dy[m_c], weights=wc)
            cells.append((g, t, att, W.to_numpy()[tr].sum()))
    if not cells:
        return {"est": np.nan, "n_cells": 0}
    c = pd.DataFrame(cells, columns=["g", "t", "att", "wg"])
    if agg == "simple":
        est = float((c.att * c.wg).sum() / c.wg.sum())
    else:  # group
        gm = c.groupby("g").apply(lambda z: pd.Series({"a": z.att.mean(), "wg": z.wg.iloc[0]}))
        est = float((gm.a * gm.wg).sum() / gm.wg.sum())
    return {"est": est, "n_cells": int(len(c)), "n_cohorts": int(c.g.nunique())}


# ---------------------------------------------------------------- Sun-Abraham
def sa(df, y, unit, time, d, w=None, controls=(), cluster=None):
    df, _ = add_cohort(df, unit, time, d)
    df = df[~(df["G"] <= df[time].min())].copy()  # always-treated units carry no pre-period: drop
    never = np.isinf(df["G"]).any()
    periods = np.sort(df[time].unique())
    if not never:
        gl = df["G"].max()
        df = df[df[time] < gl]  # last-treated cohort acts as control; drop its treated periods
        df = df.assign(G=df["G"].where(df["G"] < gl, np.inf))
    cohorts = sorted([g for g in df.G.unique() if np.isfinite(g) and g > df[time].min()])
    cols, names, meta = [], [], []
    allt = sorted(df[time].unique())
    for g in cohorts:
        coh = (df.G == g).to_numpy()
        nco = df.loc[coh, unit].nunique()
        obs_by_t = df.loc[coh].groupby(time)[unit].nunique()
        pre = [p for p in allt if p < g and obs_by_t.get(p, 0) == nco]
        base = max(pre) if pre else None  # latest pre-period observed for the whole cohort
        for t in allt:
            if base is None or t == base:
                continue
            m = ((df.G == g) & (df[time] == t)).to_numpy().astype(float)
            if m.sum() == 0:
                continue
            cols.append(m); names.append((g, t)); meta.append(m.sum())
    D = np.column_stack(cols)
    X = np.column_stack([D, design(df, unit, time, controls)])
    yy = df[y].to_numpy(float)
    ww = None if w is None else df[w].to_numpy(float)
    beta = wls(yy, X, ww)
    V = cluster_vcov(yy, X, ww, beta, df[cluster if cluster else unit])
    k = len(names)
    wts = np.zeros(k)
    wunit = np.ones(len(df)) if ww is None else ww
    for j, (g, t) in enumerate(names):
        if t >= g:
            wts[j] = wunit[((df.G == g) & (df[time] == t)).to_numpy()].sum()
    wts = wts / wts.sum()
    est = float(wts @ beta[:k])
    se = float(np.sqrt(wts @ V[:k, :k] @ wts))
    return {"est": est, "se": se, "control": "never" if never else "last-treated"}


# ---------------------------------------------------------------- cluster bootstrap
def boot_se(fn, df, unit, cluster, B=100, seed=1):
    """fn(df)->float. Resample clusters with replacement; relabel unit and cluster so duplicates are distinct."""
    rng = np.random.default_rng(seed)
    cl = df[cluster].to_numpy()
    uc = np.unique(cl)
    groups = {c: df[cl == c] for c in uc}
    vals = []
    for b in range(B):
        pick = rng.choice(uc, size=len(uc), replace=True)
        parts = []
        for j, c in enumerate(pick):
            z = groups[c].copy()
            z[unit] = str(j) + "_" + z[unit].astype(str)
            z[cluster] = j
            parts.append(z)
        bd = pd.concat(parts, ignore_index=True)
        try:
            v = fn(bd)
        except Exception:
            continue
        if v is not None and np.isfinite(v):
            vals.append(v)
    return float(np.std(vals, ddof=1)) if len(vals) > 10 else np.nan
