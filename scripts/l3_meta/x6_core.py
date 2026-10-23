"""X6 core (pure functions): baseline gaps under a labelling and the within-series pooled slope.  PREREG_X6_V1.md."""
from __future__ import annotations

import numpy as np


def cohort_gaps(val, ok, A, lag=1):
    """Standardised baseline gap per labelling.
    val, ok: (n_units, n_years) series values and availability; years index 0.. ; A: (n_label_sets, n_units) adoption YEAR INDEX (np.inf = never adopted).
    A cohort g (year index) uses year index g - lag.  Returns (n_label_sets,) with NaN where no cohort qualifies."""
    R, n = A.shape
    num = np.zeros(R)
    den = np.zeros(R)
    cohorts = np.unique(A[np.isfinite(A)]).astype(int)
    for g in cohorts:
        yb = g - lag
        if yb < 0 or yb >= val.shape[1]:
            continue
        m = ok[:, yb] & np.isfinite(val[:, yb])
        if m.sum() < 3:
            continue
        y = np.where(m, val[:, yb], 0.0)
        sd = y[m].std()
        if not sd > 0:
            continue
        T = ((A == g) & m[None, :]).astype(float)
        C = ((A > g) & m[None, :]).astype(float)
        nT, nC = T.sum(axis=1), C.sum(axis=1)
        valid = (nT >= 1) & (nC >= 1)
        with np.errstate(all="ignore"):
            gap = (T @ y) / nT - (C @ y) / nC
        num += np.where(valid, nT * gap / sd, 0.0)
        den += np.where(valid, nT, 0.0)
    with np.errstate(all="ignore"):
        out = num / den
    out[den == 0] = np.nan
    return out


def within_beta(Z, G, sidx, n_series, W=None):
    """Within-series pooled slope of z on G.  Z, G: (R, nc); sidx: (nc,) series codes; W: optional (R, nc) weights (bootstrap multiplicities).
    Returns (R,) slopes."""
    R, nc = Z.shape
    ok = np.isfinite(Z) & np.isfinite(G)
    w = ok.astype(float) if W is None else np.where(ok, W, 0.0)
    P = np.zeros((nc, n_series))
    P[np.arange(nc), sidx] = 1.0
    Zf, Gf = np.where(ok, Z, 0.0), np.where(ok, G, 0.0)
    ns = w @ P
    with np.errstate(all="ignore"):
        mz = ((w * Zf) @ P) / ns
        mg = ((w * Gf) @ P) / ns
        dz = np.where(ok, Z - mz[:, sidx], 0.0)
        dg = np.where(ok, G - mg[:, sidx], 0.0)
        beta = (w * dz * dg).sum(axis=1) / (w * dg * dg).sum(axis=1)
    return beta
