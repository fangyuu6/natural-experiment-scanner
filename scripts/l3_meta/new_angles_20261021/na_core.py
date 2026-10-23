"""T232 core (pure functions, no I/O). EXPLORATORY (EX-1). Spec: docs/plans/l3-meta/PREPLAN_T232_20261021.md (sha256 in the status file).

Cohort arrays: for one design, `coh` is a dict g -> dict(n=int states at event time 0, eff={event_time: aligned standardized ATT}).
"""
from __future__ import annotations

import math
import numpy as np

# ---------------------------------------------------------------- basic helpers


def sgn(x):
    return (x > 0) - (x < 0)


def design_sign(rows):
    """sign of the n_treated-weighted mean ATT over supported post cells (event time >= 0, finite att). rows: iterable of (event_time, att, n_treated)."""
    num = den = 0.0
    for e, a, n in rows:
        if e >= 0 and a is not None and math.isfinite(a) and n and n > 0:
            num += a * n
            den += n
    if den == 0 or num == 0:
        return 0
    return sgn(num)


def build_cohorts(rows, scale, sign):
    """rows: iterable of (g, event_time, att, n_treated). Keep post cells (event_time >= 0) with finite att.
    Returns {g: dict(n=n_treated at event time 0 (None if absent), eff={e: att/scale*sign})}."""
    coh = {}
    for g, e, a, n in rows:
        if e < 0 or a is None or not math.isfinite(a):
            continue
        c = coh.setdefault(g, dict(n=None, eff={}))
        c["eff"][e] = a / scale * sign
        if e == 0:
            c["n"] = n
    return {g: c for g, c in coh.items() if c["n"]}


def eligible(coh, window):
    """cohorts having every event time in `window` (range)."""
    need = list(window)
    return {g: c for g, c in coh.items() if all(e in c["eff"] for e in need)}


def window_mean(c, window):
    return float(np.mean([c["eff"][e] for e in window]))


# ---------------------------------------------------------------- Q1: early vs late groups


def third_groups(order_n, min_states=2):
    """order_n: states per cohort in adoption order. Early group = first cohorts until they hold >= 1/3 of all states; late = last cohorts likewise.
    Returns (early_idx, late_idx) or None when the groups overlap or hold < min_states states."""
    n = np.asarray(order_n, float)
    T = n.sum()
    if len(n) < 2 or T <= 0:
        return None
    cs = np.cumsum(n)
    k = int(np.searchsorted(cs, T / 3.0 - 1e-12) + 1)
    early = list(range(min(k, len(n))))
    rs = np.cumsum(n[::-1])
    m = int(np.searchsorted(rs, T / 3.0 - 1e-12) + 1)
    late = list(range(len(n) - min(m, len(n)), len(n)))
    if set(early) & set(late):
        return None
    if n[early].sum() < min_states or n[late].sum() < min_states:
        return None
    return early, late


def early_late_diff(y, n, order=None):
    """y, n: arrays per eligible cohort in adoption order. order: optional permutation of indices (cohort labels shuffled).
    Returns dict(D=late-early, early=, late=, n_early=, n_late=) or None."""
    y = np.asarray(y, float)
    n = np.asarray(n, float)
    if order is not None:
        y, n = y[order], n[order]
    gr = third_groups(n)
    if gr is None:
        return None
    e, l = gr
    ye = float(np.sum(y[e] * n[e]) / np.sum(n[e]))
    yl = float(np.sum(y[l] * n[l]) / np.sum(n[l]))
    return dict(D=yl - ye, early=ye, late=yl, n_early=float(n[e].sum()), n_late=float(n[l].sum()))


def pooled_share_median(D):
    """D: 1-D array of design differences (nan = invalid). Returns (share of D>0 among valid, median, n)."""
    D = np.asarray(D, float)
    D = D[np.isfinite(D)]
    if len(D) == 0:
        return float("nan"), float("nan"), 0
    return float(np.mean(D > 0)), float(np.median(D)), int(len(D))


# ---------------------------------------------------------------- Q2: fade / hold / grow


def classify(a, b, c):
    """a = early (event times 0..2), b = late (3..5), both aligned standardized; c = noise cutoff in standardized units."""
    if max(abs(a), abs(b)) < c:
        return "unclear"
    if abs(a - b) < c:
        return "hold"
    if sgn(a) * sgn(b) < 0 or abs(b) <= 0.5 * abs(a):
        return "fade"
    if abs(b) >= 1.5 * abs(a) and sgn(a) * sgn(b) >= 0:
        return "grow"
    return "hold"


def early_late_windows(coh, early=(0, 1, 2), late=(3, 4, 5), min_states=2):
    """balanced cohorts (all of 0..5), n-weighted. Returns (a, b, n_states, n_cohorts) or None."""
    need = list(early) + list(late)
    bal = {g: c for g, c in coh.items() if all(e in c["eff"] for e in need)}
    if not bal:
        return None
    n = np.array([c["n"] for c in bal.values()], float)
    if n.sum() < min_states:
        return None
    a = float(np.sum([np.mean([c["eff"][e] for e in early]) * c["n"] for c in bal.values()]) / n.sum())
    b = float(np.sum([np.mean([c["eff"][e] for e in late]) * c["n"] for c in bal.values()]) / n.sum())
    return a, b, float(n.sum()), len(bal)


# ---------------------------------------------------------------- Q5: class contrasts inside designs


def halves(values, exclude_middle=True):
    """rank halves: returns array of +1 (top half), -1 (bottom half), 0 (middle if odd). nan -> 0."""
    v = np.asarray(values, float)
    out = np.zeros(len(v), int)
    idx = np.where(np.isfinite(v))[0]
    if len(idx) < 2:
        return out
    order = idx[np.argsort(v[idx], kind="mergesort")]
    h = len(order) // 2
    out[order[:h]] = -1
    out[order[len(order) - h:]] = 1
    return out


def fe_contrast(designs):
    """designs: list of dict(y=array, w=array, lab=array of +1/-1/0). Pooled design-FE difference: de-mean y within design over all cohorts with finite y and
    weight w, then weighted mean of de-meaned y for lab=+1 minus lab=-1, over designs that have both labels. Returns (diff, n_designs, n_pos, n_neg, per_design_diffs)."""
    num_p = den_p = num_m = den_m = 0.0
    per = []
    npos = nneg = 0
    for d in designs:
        y, w, lab = np.asarray(d["y"], float), np.asarray(d["w"], float), np.asarray(d["lab"], int)
        ok = np.isfinite(y) & (w > 0)
        if not ((lab[ok] == 1).any() and (lab[ok] == -1).any()):
            continue
        ym = np.sum(y[ok] * w[ok]) / np.sum(w[ok])
        yy = y - ym
        p = ok & (lab == 1)
        m = ok & (lab == -1)
        num_p += np.sum(yy[p] * w[p]); den_p += np.sum(w[p])
        num_m += np.sum(yy[m] * w[m]); den_m += np.sum(w[m])
        per.append(float(np.sum(y[p] * w[p]) / np.sum(w[p]) - np.sum(y[m] * w[m]) / np.sum(w[m])))
        npos += int(p.sum()); nneg += int(m.sum())
    if not per:
        return float("nan"), 0, 0, 0, []
    return float(num_p / den_p - num_m / den_m), len(per), npos, nneg, per


def band(null, lo=2.5, hi=97.5):
    null = np.asarray(null, float)
    null = null[np.isfinite(null)]
    return float(np.percentile(null, lo)), float(np.percentile(null, hi))


def two_sided_p(obs, null):
    null = np.asarray(null, float)
    null = null[np.isfinite(null)]
    m = np.mean(null)
    return float((1 + np.sum(np.abs(null - m) >= abs(obs - m))) / (1 + len(null)))


def design_terms(y, w, lab):
    """one design's contribution to the pooled design-FE contrast: (num_pos, den_pos, num_neg, den_neg, own_diff) or None if a class is missing."""
    y, w, lab = np.asarray(y, float), np.asarray(w, float), np.asarray(lab, int)
    ok = np.isfinite(y) & (w > 0)
    p = ok & (lab == 1)
    m = ok & (lab == -1)
    if not (p.any() and m.any()):
        return None
    ym = np.sum(y[ok] * w[ok]) / np.sum(w[ok])
    yy = y - ym
    return (float(np.sum(yy[p] * w[p])), float(np.sum(w[p])), float(np.sum(yy[m] * w[m])), float(np.sum(w[m])),
            float(np.sum(y[p] * w[p]) / np.sum(w[p]) - np.sum(y[m] * w[m]) / np.sum(w[m])))


def pooled_from_terms(terms):
    t = [x for x in terms if x is not None]
    if not t:
        return float("nan")
    return sum(x[0] for x in t) / sum(x[1] for x in t) - sum(x[2] for x in t) / sum(x[3] for x in t)
