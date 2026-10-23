"""T227 estimator check, library (EXPLORATORY, offline, read-only on every L2 file).

Independent numpy implementation of group-time ATTs (Callaway-Sant'Anna style) on the pipeline's own frozen
design (units, periods, adoption, exposure, mask) and frozen outcome panel, plus Sun-Abraham, static TWFE and an
HonestDiD-style breakdown.  Nothing is written under scripts/l2_pipeline, data/l2_pipeline.  Bytecode is off.

control='never'  reproduces the pipeline (whole-window-clean never-treated pool).
control='nyt'    not-yet-treated: units with no policy or adoption after max(t, base), clean at t and base.
"""
from __future__ import annotations
import math, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "l3_meta"))
import sweep_core as S  # noqa: E402


# ------------------------------------------------------------------ loading
class Loaded:
    pass


def load_primary(design_id, folder):
    ctx = S.load_design_context(design_id, ROOT / folder)
    e = ctx.primary
    if e is None or e.status != "ready":
        raise RuntimeError(f"{design_id}: primary not ready ({None if e is None else e.reason})")
    rows, outcomes = S.panel_outcomes(e)
    o = Loaded()
    o.design_id, o.folder, o.ctx, o.entry = design_id, folder, ctx, e
    o.D, o.decl, o.y = e.design, e.declaration, outcomes
    o.keys = sorted(o.D["mask"])
    o.index = {k: i for i, k in enumerate(o.keys)}
    o.yvec = np.array([outcomes[k] for k in o.keys], dtype=float)
    case = o.decl["case"]
    o.min_controls = case.get("min_controls", 1) if "min_post_periods" in case else 1
    if "v11_min_controls" in case:
        o.min_controls = case.get("v11_min_controls", 5)
    o.v11 = (dict(min_post=case.get("v11_min_post_periods", 2), min_pre=case.get("v11_min_pre_periods", 3))
             if "v11_min_controls" in case else None)
    o.pipe_control = "nyt" if D_is_nyt(o.D) else "never"
    return o


def D_is_nyt(D):
    return D.get("control_group") == "not_yet_treated"


# ------------------------------------------------------------------ cells
def make_cells(D, control="never", groups=None, min_controls=1, v11=None):
    """Group-time cells with the pipeline's arithmetic.  control in {'never','nyt'}."""
    units, adoption, mask, exposure = D["units"], D["adoption"], D["mask"], D["exposure"]
    observed, periods, wwc = D["observed_periods"], D["periods"], D["whole_window_clean"]
    periods_set, observed_set = set(periods), set(observed)
    clean = lambda u, t: exposure[u].get(t, {}).get("clean", False)
    full = lambda u, t: exposure[u].get(t, {}).get("full", False)
    groups = D["groups"] if groups is None else groups
    bases = {}
    for u, g in adoption.items():
        if g is not None:
            cb = [t for t in observed if (u, t) in mask and t < g and clean(u, t)]
            if cb:
                bases[u] = max(cb)
    cells = []
    for g in groups:
        members = [u for u in units if adoption[u] == g]
        for t in periods:
            post = t >= g
            parts = defaultdict(list)
            for u in members:
                base = bases.get(u) if post else t - 1
                if base is None or base not in (observed_set if post else periods_set) or (u, t) not in mask or (u, base) not in mask:
                    continue
                if not clean(u, base) or (post and not full(u, t)) or ((not post) and not clean(u, t)):
                    continue
                parts[base].append(u)
            for base, treated in sorted(parts.items()):
                tb = max(t, base)
                if control == "never":
                    controls = [u for u in units if adoption[u] != g and (u, t) in mask and (u, base) in mask
                                and clean(u, t) and clean(u, base) and wwc[u]]
                else:
                    controls = [u for u in units if adoption[u] != g and (u, t) in mask and (u, base) in mask
                                and clean(u, t) and clean(u, base) and (adoption[u] is None or tb < adoption[u])]
                if len(controls) < max(1, min_controls):
                    continue
                cells.append(dict(g=g, t=t, event_time=t - g, base_period=base, post=post, n_treated=len(treated),
                                  n_control=len(controls), treated=treated, controls=controls))
    if control == "nyt" and v11:
        # the pipeline's v11 cohort retention for not-yet-treated primaries: enough post cells and enough clean pre periods per unit
        keep = set()
        for g in groups:
            npost = len({c["t"] for c in cells if c["g"] == g and c["post"]})
            if npost >= v11["min_post"] and all(sum((u, t) in mask and exposure[u].get(t, {}).get("clean", False)
                                                   for t in periods if t < g) >= v11["min_pre"]
                                               for u in units if adoption[u] == g):
                keep.add(g)
        cells = [c for c in cells if c["g"] in keep]
    return cells


def prune_to_cohorts(cells, min_post=1):
    """Drop cohorts with fewer than min_post post cells (after control-pool restriction)."""
    cnt = defaultdict(int)
    for c in cells:
        if c["post"]:
            cnt[c["g"]] += 1
    return [c for c in cells if cnt[c["g"]] >= min_post]


def cell_vector(c, index, K):
    idx, w = [], []
    nt, nc = len(c["treated"]), len(c["controls"])
    t, b = c["t"], c["base_period"]
    for u in c["treated"]:
        idx += (index[(u, t)], index[(u, b)]); w += (1 / nt, -1 / nt)
    for u in c["controls"]:
        idx += (index[(u, t)], index[(u, b)]); w += (-1 / nc, 1 / nc)
    return np.bincount(np.array(idx, dtype=np.int64), weights=np.array(w), minlength=K)


def agg_weights(cells, index, K, mode="simple"):
    """Linear functional (length K) of the post-cell aggregate.
    simple: cohort-size weighted over supported post cells (the pipeline's overall).
    group : CS 'group' aggregation: mean over each cohort's post cells, cohorts weighted by size."""
    post = [c for c in cells if c["post"]]
    if not post:
        return np.zeros(K)
    if mode == "simple":
        mass = sum(c["n_treated"] for c in post)
        coef = [c["n_treated"] / mass for c in post]
    else:
        per = defaultdict(list)
        for i, c in enumerate(post):
            per[c["g"]].append(i)
        size = {g: max(post[i]["n_treated"] for i in ix) for g, ix in per.items()}
        tot = sum(size.values())
        coef = [0.0] * len(post)
        for g, ix in per.items():
            for i in ix:
                coef[i] = size[g] / tot / len(ix)
    out = np.zeros(K)
    for f, c in zip(coef, post):
        out += f * cell_vector(c, index, K)
    return out


def event_weights(cells, index, K, e):
    sel = [c for c in cells if c["event_time"] == e]
    mass = sum(c["n_treated"] for c in sel)
    out = np.zeros(K)
    for c in sel:
        out += c["n_treated"] / mass * cell_vector(c, index, K)
    return out, mass


# ------------------------------------------------------------------ influence SE (pipeline's formula, analytic)
def overall_se(o, cells, y=None):
    """sqrt(sum psi^2)/n for the cohort-size weighted overall (the multiplier SE of the pipeline, without simulation)."""
    y = o.y if y is None else y
    units = o.D["units"]; n = len(units); ui = {u: i for i, u in enumerate(units)}
    post = [c for c in cells if c["post"]]
    atts, infs, masses = [], [], []
    for c in post:
        ch = {u: y[u, c["t"]] - y[u, c["base_period"]] for u in c["treated"] + c["controls"]}
        mg = sum(ch[u] for u in c["treated"]) / len(c["treated"])
        mc = sum(ch[u] for u in c["controls"]) / len(c["controls"])
        inf = np.zeros(n)
        for u in c["treated"]:
            inf[ui[u]] = n / len(c["treated"]) * (ch[u] - mg)
        for u in c["controls"]:
            inf[ui[u]] = -n / len(c["controls"]) * (ch[u] - mc)
        atts.append(mg - mc); infs.append(inf); masses.append(c["n_treated"])
    total = sum(masses)
    att = sum(m * a for m, a in zip(masses, atts)) / total
    psi = sum(m * i for m, i in zip(masses, infs)) / total
    for m, a, c in zip(masses, atts, post):
        for u in c["treated"]:
            psi[ui[u]] += n / total * (a - att)
    return att, math.sqrt(float(np.sum(psi * psi))) / n


# ------------------------------------------------------------------ RI draws for an arbitrary cell builder
def ri_draws(o, control="never", mode="simple", n_draws=999, min_controls=None, return_ops=False):
    """Placebo ATTs of the pipeline's fixed label reassignments, estimator = group-time ATT with the given control pool.
    Infeasible draws (the pipeline's rule) are NaN.  Returns (att_vector_op, draws[n_draws], n_cells_obs_draws_info)."""
    L = S.load_l2()
    mc = o.min_controls if min_controls is None else min_controls
    K = len(o.keys)
    family = S.family_of(o.D["cells"])
    out = np.full(n_draws, np.nan)
    rows, idxs = [], []
    for b, labels in enumerate(S.own_bundles(o.D, n_draws)):
        try:
            fake = L.ri.relabeled_design(o.decl, o.D, labels)
            if S.family_of(fake["cells"]) != family:
                raise ValueError("RI lead inventory changed under relabeling")
        except ValueError as exc:
            if not L.ri.support_failure(exc):
                raise
            continue
        cells = make_cells(fake, control, groups=fake["groups"], min_controls=mc, v11=o.v11)
        if not any(c["post"] for c in cells):
            continue
        w = agg_weights(cells, o.index, K, mode)
        rows.append(w); idxs.append(b)
    if rows:
        R = np.array(rows)
        out[idxs] = R @ o.yvec
        if return_ops:
            return R, idxs, out
    return out


def noise_stats(att, draws):
    d = np.asarray(draws, dtype=float)
    d = d[np.isfinite(d)]
    n = d.size
    if n < 2:
        return dict(n=int(n), p2=float("nan"), cutoff=float("nan"), beyond=None)
    cutoff = float(np.sort(np.abs(d))[math.ceil(0.95 * n) - 1])
    p2 = (1 + int(np.sum(np.abs(d) >= abs(att)))) / (n + 1)
    return dict(n=int(n), p2=p2, cutoff=cutoff, beyond=bool(abs(att) > cutoff), sd=float(np.std(d, ddof=1)))


# ------------------------------------------------------------------ regression sample, TWFE, Sun-Abraham
def reg_sample(o, cells, D=None):
    D = o.D if D is None else D
    exposure, adoption, mask = D["exposure"], D["adoption"], D["mask"]
    sup = sorted({c["g"] for c in cells})
    treated_units = {u for u in D["units"] if adoption[u] in sup}
    never = {u for u in D["units"] if D["whole_window_clean"][u] and u not in treated_units}
    rows = []
    for u in sorted(treated_units | never):
        g = adoption[u] if u in treated_units else None
        for t in D["periods"]:
            if (u, t) not in mask:
                continue
            ex = exposure[u].get(t, {})
            if g is not None:
                if t < g and not ex.get("clean", False):
                    continue
                if t >= g and not ex.get("full", False):
                    continue
            else:
                if not ex.get("clean", False):
                    continue
            rows.append((u, t, g))
    return rows


def _fe_design(rows):
    us = sorted({r[0] for r in rows}); ts = sorted({r[1] for r in rows})
    ui = {u: i for i, u in enumerate(us)}; ti = {t: i for i, t in enumerate(ts)}
    N = len(rows)
    F = np.zeros((N, len(us) + len(ts) - 1))
    for k, (u, t, g) in enumerate(rows):
        F[k, ui[u]] = 1
        if ti[t] > 0:
            F[k, len(us) + ti[t] - 1] = 1
    return F, ui


def _cluster_comb(X, e, clusters, a):
    """Cluster-robust SE of a'beta using the pseudo-inverse of X'X."""
    XtX = X.T @ X
    b = np.linalg.pinv(XtX) @ a
    s = defaultdict(lambda: np.zeros(X.shape[1]))
    for k, cl in enumerate(clusters):
        s[cl] += X[k] * e[k]
    G = len(s)
    var = sum(float(b @ v) ** 2 for v in s.values()) * G / max(G - 1, 1)
    return math.sqrt(max(var, 0.0))


def twfe(o, cells, D=None, se=True):
    rows = reg_sample(o, cells, D)
    y = np.array([o.y[(u, t)] for u, t, g in rows])
    d = np.array([1.0 if (g is not None and t >= g) else 0.0 for u, t, g in rows])
    F, _ = _fe_design(rows)
    X = np.column_stack([d, F])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    if not se:
        return float(beta[0]), None, len(rows)
    e = y - X @ beta
    a = np.zeros(X.shape[1]); a[0] = 1
    sev = _cluster_comb(X, e, [r[0] for r in rows], a)
    return float(beta[0]), sev, len(rows)


def twfe_ri(o, n_draws=999):
    """Placebo TWFE coefficients under the pipeline's fixed label reassignments (same feasibility rule)."""
    L = S.load_l2()
    family = S.family_of(o.D["cells"])
    out = np.full(n_draws, np.nan)
    for b, labels in enumerate(S.own_bundles(o.D, n_draws)):
        try:
            fake = L.ri.relabeled_design(o.decl, o.D, labels)
            if S.family_of(fake["cells"]) != family:
                raise ValueError("RI lead inventory changed under relabeling")
        except ValueError as exc:
            if not L.ri.support_failure(exc):
                raise
            continue
        out[b] = twfe(o, fake["cells"], D=fake, se=False)[0]
    return out


def sun_abraham(o, cells):
    """Interaction-weighted estimator.  Cohort-specific reference period = the cohort's pipeline base (usually g-1).
    Overall = cohort-size weighted mean of CATT(g,e) over the pipeline's post cells (same aggregation set as CS)."""
    rows = reg_sample(o, cells)
    post = [c for c in cells if c["post"]]
    if not post:
        raise ValueError("no post cells")
    ref = {}
    for g in sorted({c["g"] for c in post}):
        bs = [c["base_period"] - g for c in post if c["g"] == g]
        ref[g] = max(set(bs), key=bs.count)
    y = np.array([o.y[(u, t)] for u, t, g in rows])
    cols, colkey = [], {}
    for k, (u, t, g) in enumerate(rows):
        if g is None or g not in ref:
            continue
        e = t - g
        if e == ref[g]:
            continue
        colkey.setdefault((g, e), len(colkey))
    D = np.zeros((len(rows), len(colkey)))
    for k, (u, t, g) in enumerate(rows):
        if g is None or g not in ref:
            continue
        key = (g, t - g)
        if key in colkey:
            D[k, colkey[key]] = 1
    F, _ = _fe_design(rows)
    X = np.column_stack([D, F])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    mass = sum(c["n_treated"] for c in post)
    a = np.zeros(X.shape[1])
    att = 0.0
    miss = 0
    for c in post:
        key = (c["g"], c["event_time"])
        if key not in colkey:
            miss += 1
            continue
        wgt = c["n_treated"] / mass
        a[colkey[key]] += wgt
        att += wgt * beta[colkey[key]]
    se = _cluster_comb(X, resid, [r[0] for r in rows], a)
    return float(att), se, len(rows), miss


# ------------------------------------------------------------------ HonestDiD-style breakdown
def pre_level_betas(o, min_treated=5):
    """Event-study pre coefficients relative to the cohort's own base period (universal base), never-treated pool,
    cohort-size weighted by event time e = t - g; n_treated >= min_treated per event time."""
    D = o.D
    units, adoption, mask, exposure = D["units"], D["adoption"], D["mask"], D["exposure"]
    clean = lambda u, t: exposure[u].get(t, {}).get("clean", False)
    full = lambda u, t: exposure[u].get(t, {}).get("full", False)
    wwc = D["whole_window_clean"]
    per_e = defaultdict(list)           # e -> list of (n_treated, att)
    for g in D["groups"]:
        members = [u for u in units if adoption[u] == g]
        bmap = defaultdict(list)
        for u in members:
            cb = [t for t in D["observed_periods"] if (u, t) in mask and t < g and clean(u, t)]
            if cb:
                bmap[max(cb)].append(u)
        for base, mem in bmap.items():
            for t in D["periods"]:
                if t >= base:
                    continue
                tr = [u for u in mem if (u, t) in mask and clean(u, t)]
                if not tr:
                    continue
                ct = [u for u in units if adoption[u] != g and wwc[u] and (u, t) in mask and (u, base) in mask and clean(u, t) and clean(u, base)]
                if len(ct) < max(1, o.min_controls):
                    continue
                att = sum(o.y[u, t] - o.y[u, base] for u in tr) / len(tr) - sum(o.y[u, t] - o.y[u, base] for u in ct) / len(ct)
                per_e[t - g].append((len(tr), att, base - g))
    out = {}
    for e, lst in per_e.items():
        m = sum(x[0] for x in lst)
        if m >= min_treated:
            out[e] = sum(x[0] * x[1] for x in lst) / m
    return out


def honest_breakdown(o, cells_never, att, se, last_k=None):
    """Relative-magnitudes breakdown of the overall post ATT, analytic bound (NOT the HonestDiD package).
    level-RM: |post trend deviation| <= M * max_e |pre coefficient relative to the cohort base| (pre_level_betas, n_treated>=5).
    slope-RM: per-period post drift <= M * max |pre one-period change| (the pipeline's own pre event study, t vs t-1);
              overall bias <= M * s_max * sum_c w_c k_c with k_c = t - base.
    Breakdown M*: |att| / unit bias (point) and max(0, |att| - 1.96 se) / unit bias (conservative 95%).
    Needs >= 3 pre event times.  Maxima of noisy coefficients overstate the true pre violation, so M* is a lower-leaning guide."""
    K = len(o.keys)
    pre_cells = [c for c in cells_never if not c["post"]]
    mass = defaultdict(int)
    for c in pre_cells:
        mass[c["event_time"]] += c["n_treated"]
    fam = sorted(e for e, m in mass.items() if e < 0 and m >= 5)
    res = dict(n_pre=len(fam))
    if len(fam) < 3:
        res.update(status="FEWER_THAN_3_PRE")
        return res
    beta = {}
    for e in fam:
        w, _ = event_weights(cells_never, o.index, K, e)
        beta[e] = float(w @ o.yvec)
    lev = pre_level_betas(o)
    if last_k:                      # only the last_k pre event times closest to the adoption (less noise-maximum inflation)
        keep = sorted(beta, reverse=True)[:last_k]
        beta = {e: beta[e] for e in keep}
        lev = {e: lev[e] for e in sorted(lev, reverse=True)[:last_k]}
    smax = max(abs(v) for v in beta.values())
    lmax = max((abs(v) for v in lev.values()), default=float("nan"))
    post = [c for c in cells_never if c["post"]]
    m = sum(c["n_treated"] for c in post)
    kbar = sum(c["n_treated"] / m * (c["t"] - c["base_period"]) for c in post)
    def bd(unit):
        if not unit or not math.isfinite(unit) or unit <= 0:
            return float("inf"), float("inf")
        return abs(att) / unit, max(0.0, abs(att) - 1.96 * se) / unit
    ps, cs = bd(smax * kbar)
    pl, cl = bd(lmax)
    res.update(status="OK", s_max=smax, level_max=lmax, kbar=kbar, n_pre_level=len(lev), bd_slope_point=ps, bd_slope_ci=cs,
               bd_level_point=pl, bd_level_ci=cl)
    return res
