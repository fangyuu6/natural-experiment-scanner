#!/usr/bin/env python3
"""Q9 neighbour copy (PREPLAN_T233_20261021 section 5, rank 9; rule id EX-1, exploratory).

Question: does a state's adoption hazard rise with the visible outcome change of its land-border
neighbours that already adopted (f_bar), net of design and year effects?

Everything below follows the pre-plan section 5 and section 6. Anything not in the plan is labelled
"added after the plan" in the output JSON.

Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/q9_neighbour_copy.py
Outputs: data/l3_meta/new_angles_20261021/states_decide/q9/ (RESULTS_Q9.json, rows_summary.csv, per_design_coef.csv)
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SD = ROOT / "data/l3_meta/new_angles_20261021/states_decide"
IN = SD / "inputs"
MAP = SD / "mapping"
OUT = SD / "q9"
ADJ = ROOT / "data/l3_meta/covariates/state_adjacency_v2.csv"
PLAN = ROOT / "docs/plans/l3-meta/PREPLAN_T233_20261021.md"

SEED_NULL_A = [20261022, 9, 1]
SEED_NULL_B = [20261022, 9, 2]
SEED_CHECK = 20261022 * 10 + 9          # python random.Random seed for the independent check (added after the plan)
N_DRAW = 1000
F_CLIP = 3.0
MIN_DESIGNS = 15
MIN_STATES = 5
MIN_ROWS = 150
MIN_EVENTS = 5
PRE_YEARS = 3
MIN_PRE = 2
MIN_DIFFS = 4
RIDGE_FIX = 1e-8       # design dummies
RIDGE_OTHER = 1e-6     # f_bar, nb_share, year dummies
RIDGE_DESIGN_FIT = 1e-4


# ---------------------------------------------------------------------------------------------
# logistic core (copied from scripts/l3_meta/build_l3n_nbr.py; that file is not imported or changed)
# ---------------------------------------------------------------------------------------------
def fit_logit(X, y, ridge, beta0=None, maxit=50, tol=1e-9):
    """Newton for a ridge-penalised logistic model. Returns beta, converged, p."""
    n, k = X.shape
    b = np.zeros(k) if beta0 is None else beta0.copy()
    R = np.diag(ridge)
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


def permute_within(v, groups, rng):
    """Permute v within groups (same routine as build_l3n_nbr.py)."""
    r0 = np.argsort(groups, kind="stable")
    r1 = np.lexsort((rng.random(len(v)), groups))
    out = v.copy()
    out[r0] = v[r1]
    return out


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def J(x):
    """JSON-safe conversion of numpy scalars and non-finite floats."""
    if isinstance(x, dict):
        return {k: J(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [J(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        xf = float(x)
        return xf if math.isfinite(xf) else None
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


# ---------------------------------------------------------------------------------------------
# feature construction
# ---------------------------------------------------------------------------------------------
def nb_feature(n, end, tr, vals, sy, pre_diffs, dirn):
    """Visible-outcome z-change of neighbour n over post window a_n..end (f_n = direction * z)."""
    a = tr[n]
    pre = [vals[(n, t)] for t in (a - 3, a - 2, a - 1) if t in sy[n]]
    if len(pre) < MIN_PRE:
        return None
    post = [vals[(n, t)] for t in range(a, end + 1) if t in sy[n]]
    if not post:
        return None
    dd = pre_diffs[n]
    if len(dd) < MIN_DIFFS:
        return None
    sd = float(np.std(dd, ddof=1))
    if sd == 0.0:
        return None
    D = float(np.mean(post)) - float(np.mean(pre))
    return dirn * D / sd


def build_design(d, pan, coh, dirn, nbrs, region):
    vals = {(s, int(y)): float(v) for s, y, v in zip(pan.state, pan.year, pan.value)}
    years_all = sorted({y for _, y in vals})
    y0, y1 = years_all[0], years_all[-1]
    sy = defaultdict(set)
    for s, y in vals:
        sy[s].add(y)
    S = sorted(sy)
    Sset = set(S)

    tr_all = {}
    dup = 0
    for r in coh.itertuples(index=False):
        a = int(math.floor(float(r.g_cal)))
        for s in str(r.states).split("|"):
            s = s.strip()
            if not s:
                continue
            if s in tr_all and tr_all[s] != a:
                dup += 1
            tr_all[s] = a
    tr = {s: a for s, a in tr_all.items() if s in Sset}
    treated_no_rows = sorted(s for s in tr_all if s not in Sset)
    out_window = sorted(s for s, a in tr.items() if a < y0 or a > y1)
    year_missing = sorted(s for s, a in tr.items() if y0 <= a <= y1 and a not in sy[s])

    pre_diffs = {}
    for n in tr:
        a = tr[n]
        pre_diffs[n] = [vals[(n, t)] - vals[(n, t - 1)] for t in sorted(sy[n]) if t < a and (t - 1) in sy[n]]

    nbS = {s: sorted(n for n in nbrs.get(s, ()) if n in Sset) for s in S}
    nb_outside = {s: len(nbrs.get(s, ())) - len(nbS[s]) for s in S}

    rows, nbf_store = [], {}
    n_risk_states = set()
    n_risk = 0
    for s in S:
        yhi = y1 if s not in tr else min(tr[s], y1)
        for y in range(y0 + 3, yhi + 1):
            if y not in sy[s]:
                continue
            n_risk += 1
            n_risk_states.add(s)
            Ns = nbS[s]
            q = []
            for n in Ns:
                if n in tr and tr[n] <= y - 1:
                    f = nb_feature(n, y - 1, tr, vals, sy, pre_diffs, dirn)
                    if f is not None:
                        q.append((n, f))
                        nbf_store[(d, s, y, n)] = f
            if not q:
                continue
            fbar = float(np.clip(np.mean([f for _, f in q]), -F_CLIP, F_CLIP))
            share = sum(1 for n in Ns if n in tr and tr[n] <= y - 1) / len(Ns)
            ev = int(s in tr and tr[s] == y)
            placebo = np.nan
            if y + 2 <= y1:
                fp = [nb_feature(n, y + 2, tr, vals, sy, pre_diffs, dirn) for n, _ in q]
                fp = [f for f in fp if f is not None]
                if fp:
                    placebo = float(np.clip(np.mean(fp), -F_CLIP, F_CLIP))
            rows.append(dict(design=d, state=s, year=y, event=ev, fbar=fbar, nbshare=share,
                             n_q=len(q), placebo=placebo, region=region[s]))

    summ = dict(
        design=d, first_year=y0, last_year=y1, n_states_panel=len(S), n_treated_states_in_panel=len(tr),
        n_treated_states_no_rows=len(treated_no_rows), treated_out_of_window=len(out_window),
        treated_year_missing_row=len(year_missing), cohort_state_dupes=dup,
        n_risk_rows=n_risk, n_risk_states=len(n_risk_states),
    )
    return rows, nbf_store, summ


# ---------------------------------------------------------------------------------------------
# pooled model
# ---------------------------------------------------------------------------------------------
def pooled_design(df):
    dcodes, did = np.unique(df.design.values, return_inverse=True)
    ycodes, yidx = np.unique(df.year.values, return_inverse=True)
    scodes, sidx = np.unique(df.state.values, return_inverse=True)
    nd, ny = len(dcodes), len(ycodes) - 1
    n = len(df)
    k = nd + ny + 2
    X = np.zeros((n, k))
    X[np.arange(n), did] = 1.0
    m = yidx > 0
    X[np.flatnonzero(m), nd + yidx[m] - 1] = 1.0
    X[:, nd + ny] = df.fbar.values
    X[:, nd + ny + 1] = df.nbshare.values
    ridge = np.r_[np.full(nd, RIDGE_FIX), np.full(ny + 2, RIDGE_OTHER)]
    return dict(X=X, ridge=ridge, j=nd + ny, did=did, yidx=yidx, sidx=sidx, nd=nd, ny=ny,
                dnames=list(dcodes), ynames=list(ycodes), snames=list(scodes), n=n)


def pooled_fit(df, beta0=None):
    M = pooled_design(df)
    y = df.event.values.astype(float)
    b, ok, p = fit_logit(M["X"], y, M["ridge"], beta0)
    Hinv = hessian_inv(M["X"], p, M["ridge"])
    se = cr1_se(M["X"], y, p, Hinv, M["sidx"], M["j"])
    return dict(b=float(b[M["j"]]), se=se, ok=bool(ok), beta=b, n=len(df), events=int(y.sum()),
                n_states=int(len(M["snames"])), n_designs=int(M["nd"]), n_years=int(M["ny"] + 1))


def design_logit(df_d):
    """Per-design logit: event ~ f_bar, nb_share, intercept, centred year trend (ridge 1e-4 except intercept)."""
    yrs = df_d.year.values.astype(float)
    t = (yrs - yrs.mean()) / max(yrs.std(), 1.0)
    X = np.column_stack([df_d.fbar.values, df_d.nbshare.values, np.ones(len(df_d)), t])
    ridge = np.array([RIDGE_DESIGN_FIT, RIDGE_DESIGN_FIT, 0.0, RIDGE_DESIGN_FIT])
    y = df_d.event.values.astype(float)
    b, ok, p = fit_logit(X, y, ridge, maxit=30)
    H = hessian_inv(X, p, ridge)
    return dict(b=float(b[0]), se=float(np.sqrt(max(H[0, 0], 0.0))), ok=bool(ok))


# ---------------------------------------------------------------------------------------------
# independent check (plain python, csv module; no pandas, no shared helper)
# ---------------------------------------------------------------------------------------------
def plain_nb_f(d, s, y, n):
    """Recompute f_n for one (design, s, y, neighbour n) from the raw files."""
    fdir = {}
    with open(MAP / "FINAL_codes.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            fdir[r["design_id"]] = int(float(r["direction_final"]))
    a_of = {}
    with open(IN / "cohorts.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["design_id"] != d:
                continue
            for st in r["states"].split("|"):
                if st:
                    a_of[st] = int(math.floor(float(r["g_cal"])))
    vals = {}
    with gzip.open(IN / "panel_outcomes.csv.gz", "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["design_id"] == d and r["state"] == n:
                vals[int(r["year"])] = float(r["value"])
    a = a_of[n]
    pre = [vals[t] for t in (a - 3, a - 2, a - 1) if t in vals]
    post = [vals[t] for t in range(a, y) if t in vals]
    assert len(pre) >= 2 and post, "triple does not qualify"
    yrs = sorted(vals)
    diffs = [vals[t] - vals[t - 1] for t in yrs if t < a and (t - 1) in vals]
    assert len(diffs) >= 4
    sd = statistics.stdev(diffs)
    D = sum(post) / len(post) - sum(pre) / len(pre)
    return fdir[d] * D / sd


# ---------------------------------------------------------------------------------------------
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "designs.csv": IN / "designs.csv",
        "cohorts.csv": IN / "cohorts.csv",
        "panel_outcomes.csv.gz": IN / "panel_outcomes.csv.gz",
        "state_year_calendar.csv": IN / "state_year_calendar.csv",
        "FINAL_codes.csv": MAP / "FINAL_codes.csv",
        "state_adjacency_v2.csv": ADJ,
    }
    hashes = {k: sha(v) for k, v in files.items()}

    designs = pd.read_csv(IN / "designs.csv")
    cohorts = pd.read_csv(IN / "cohorts.csv")
    panel = pd.read_csv(IN / "panel_outcomes.csv.gz")
    cal = pd.read_csv(IN / "state_year_calendar.csv")
    fc = pd.read_csv(MAP / "FINAL_codes.csv")
    adj = pd.read_csv(ADJ)
    assert not panel.duplicated(["design_id", "state", "year"]).any()

    nbrs = defaultdict(set)
    for a, b in zip(adj.loc[adj.land_border_rule_v1 == 1, "state_a"], adj.loc[adj.land_border_rule_v1 == 1, "state_b"]):
        nbrs[a].add(b)
        nbrs[b].add(a)
    region = cal.groupby("state").region.first().to_dict()
    # DC is in the panel but not in the calendar; Census places DC in the South region (added after the plan)
    region.setdefault("DC", "South")
    dirmap = dict(zip(fc.design_id, fc.direction_final))
    blockmap = dict(zip(designs.design_id, designs.block))

    # ---- design sample -----------------------------------------------------------------------
    acct = dict(n_designs_total=int(len(designs)), n_panel_rows_gt0=int((designs.panel_rows > 0).sum()))
    cand = designs[designs.panel_rows > 0].copy()
    cand["direction_final"] = cand.design_id.map(dirmap)
    cand = cand[cand.direction_final.notna() & (cand.direction_final != 0)]
    acct["n_after_direction_nonzero"] = int(len(cand))
    coh_by = {d: g for d, g in cohorts.groupby("design_id")}
    sample, excluded = [], []
    for d in cand.design_id:
        g = coh_by.get(d)
        if g is None or g.states.isna().any() or g.g_cal.isna().any():
            excluded.append(dict(design=d, reason="treated-state list unknown for some cohort"))
            continue
        sample.append(d)
    acct["n_design_sample"] = len(sample)
    acct["excluded"] = excluded

    # ---- build rows -------------------------------------------------------------------------
    all_rows, nbf_all, summaries = [], {}, []
    for d in sample:
        pan = panel[panel.design_id == d]
        dirn = int(dirmap[d])
        rws, nbf, summ = build_design(d, pan, coh_by[d], dirn, nbrs, region)
        all_rows.extend(rws)
        nbf_all.update(nbf)
        summ["direction_final"] = dirn
        summ["block"] = int(blockmap[d])
        summaries.append(summ)
    R = pd.DataFrame(all_rows)
    per = pd.DataFrame(summaries)
    if len(R):
        g = R.groupby("design")
        cnt = pd.DataFrame({
            "n_used_rows": g.size(),
            "n_used_states": g.state.nunique(),
            "n_events": g.event.sum(),
            "n_placebo_rows": g.placebo.apply(lambda s: int(s.notna().sum())),
            "n_placebo_events": g.apply(lambda x: int(x.loc[x.placebo.notna(), "event"].sum()), include_groups=False),
        })
        per = per.merge(cnt, left_on="design", right_index=True, how="left")
    per = per.fillna({"n_used_rows": 0, "n_used_states": 0, "n_events": 0, "n_placebo_rows": 0, "n_placebo_events": 0})
    for c in ["n_used_rows", "n_used_states", "n_events", "n_placebo_rows", "n_placebo_events"]:
        per[c] = per[c].astype(int)
    per.to_csv(OUT / "rows_summary.csv", index=False)

    n_rows = int(len(R))
    n_events = int(R.event.sum()) if n_rows else 0
    states_used = sorted(set(R.state)) if n_rows else []
    n_qual_designs = int((per.n_used_states >= MIN_STATES).sum())
    counts = dict(
        n_designs_in_sample=len(sample), n_designs_with_used_rows=int((per.n_used_rows > 0).sum()),
        n_used_rows=n_rows, n_events=n_events, n_states=len(states_used),
        n_designs_ge5_states=n_qual_designs, n_designs_with_ge5_events=int((per.n_events >= MIN_EVENTS).sum()),
        n_placebo_rows=int(R.placebo.notna().sum()) if n_rows else 0,
        n_placebo_events=int(R.loc[R.placebo.notna(), "event"].sum()) if n_rows else 0,
        n_risk_rows_total=int(per.n_risk_rows.sum()),
        per_design_used_rows={r.design: int(r.n_used_rows) for r in per.itertuples()},
    )
    passes = (n_qual_designs >= MIN_DESIGNS) or (n_rows >= MIN_ROWS)
    res = dict(
        question="Q9 neighbour copy (PREPLAN_T233_20261021 section 5)",
        rule_id="EX-1 exploratory",
        plan=str(PLAN.relative_to(ROOT)),
        plan_sha256=sha(PLAN),
        input_sha256=hashes,
        seeds=dict(null_a=SEED_NULL_A, null_b=SEED_NULL_B, check_python_random=SEED_CHECK, n_draw=N_DRAW),
        design_sample=acct,
        counts=counts,
        minimum_rule=dict(need="15 designs with >=5 states having a qualifying neighbour, OR >=150 used rows",
                          passes=bool(passes)),
    )

    if not passes:
        res["verdict"] = "too few"
        with open(OUT / "RESULTS_Q9.json", "w") as fh:
            json.dump(J(res), fh, indent=2)
        print("too few", counts)
        return

    # ---- independent check of f_n (5 triples, plain python) ----------------------------------
    rng_chk = random.Random(SEED_CHECK)
    keys = sorted(nbf_all.keys())
    picks = rng_chk.sample(keys, 5)
    checks = []
    for (d, s, y, n) in picks:
        mine = nbf_all[(d, s, y, n)]
        plain = plain_nb_f(d, s, y, n)
        checks.append(dict(design=d, state=s, year=int(y), neighbour=n, main=float(mine), plain=float(plain),
                           abs_diff=float(abs(mine - plain))))
    chk_ok = all(c["abs_diff"] <= 1e-9 for c in checks)
    res["independent_check"] = dict(triples=checks, all_within_1e9=bool(chk_ok),
                                    note="added after the plan: plain-python recomputation from csv files")

    # ---- pooled model ----------------------------------------------------------------------
    obs = pooled_fit(R)
    j = pooled_design(R)["j"]
    z = obs["b"] / obs["se"] if obs["se"] > 0 else float("nan")
    from math import erfc, sqrt
    p_norm = erfc(abs(z) / sqrt(2)) if math.isfinite(z) else float("nan")
    pooled = dict(b_f=obs["b"], se_cr1=obs["se"], z=z, p_normal=p_norm,
                  ci95=[obs["b"] - 1.96 * obs["se"], obs["b"] + 1.96 * obs["se"]],
                  converged=obs["ok"], n_rows=obs["n"], n_events=obs["events"], n_states=obs["n_states"],
                  n_designs=obs["n_designs"], n_years=obs["n_years"])
    b_obs = obs["beta"]
    print("observed b_f", obs["b"], "se", obs["se"], "converged", obs["ok"])

    # ---- nulls -----------------------------------------------------------------------------
    M = pooled_design(R)
    X0 = M["X"].copy()
    y = R.event.values.astype(float)
    yidx = M["yidx"]
    did = M["did"]
    grp_a = did * 10000 + yidx
    reg_codes = pd.factorize(R.region.values)[0]
    grp_b = grp_a * 10 + reg_codes
    fb0 = R.fbar.values.astype(float)

    def null_draws(groups, seed):
        rng = np.random.default_rng(seed)
        out, nonconv = [], 0
        X = X0.copy()
        for _ in range(N_DRAW):
            X[:, j] = permute_within(fb0, groups, rng)
            b, ok, _ = fit_logit(X, y, M["ridge"], beta0=b_obs)
            out.append(float(b[j]))
            nonconv += (not ok)
        return np.array(out), nonconv

    def pnull(b, null):
        null = null[np.isfinite(null)]
        return dict(B=int(len(null)), mean=float(null.mean()), sd=float(null.std(ddof=1)),
                    q025=float(np.quantile(null, 0.025)), q975=float(np.quantile(null, 0.975)),
                    p_two_sided=float((1 + np.sum(np.abs(null) >= abs(b))) / (1 + len(null))))

    nullA, ncA = null_draws(grp_a, SEED_NULL_A)
    nullB, ncB = null_draws(grp_b, SEED_NULL_B)
    resA = pnull(obs["b"], nullA)
    resA["nonconverged_draws"] = int(ncA)
    resB = pnull(obs["b"], nullB)
    resB["nonconverged_draws"] = int(ncB)
    print("nullA", resA)
    print("nullB", resB)

    # ---- future placebo (observed only) ----------------------------------------------------
    Rp = R[R.placebo.notna()].copy()
    Rp["fbar"] = Rp["placebo"]
    if len(Rp) and Rp.event.sum() > 0:
        pl = pooled_fit(Rp)
        placebo = dict(b=pl["b"], se_cr1=pl["se"], converged=pl["ok"], n_rows=pl["n"], n_events=pl["events"])
    else:
        placebo = dict(b=None, note="no rows or events")

    # ---- per-design logit ------------------------------------------------------------------
    pdrows = []
    for d, g in R.groupby("design"):
        ev = int(g.event.sum())
        if ev < MIN_EVENTS:
            pdrows.append(dict(design=d, n_rows=len(g), n_events=ev, fitted=False, b_f=None, se_model=None,
                               sign=None, same_sign_as_pooled=None))
            continue
        fit = design_logit(g)
        if not fit["ok"] or not math.isfinite(fit["b"]):
            pdrows.append(dict(design=d, n_rows=len(g), n_events=ev, fitted=False, b_f=fit["b"],
                               se_model=fit["se"], sign=None, same_sign_as_pooled=None))
            continue
        sg = int(np.sign(fit["b"]))
        pdrows.append(dict(design=d, n_rows=len(g), n_events=ev, fitted=True, b_f=fit["b"],
                           se_model=fit["se"], sign=sg, same_sign_as_pooled=bool(sg == np.sign(obs["b"]))))
    PD = pd.DataFrame(pdrows)
    PD.to_csv(OUT / "per_design_coef.csv", index=False)
    fitted = PD[PD.fitted]
    n_same = int(fitted.same_sign_as_pooled.sum()) if len(fitted) else 0
    sign_share = (n_same / len(fitted)) if len(fitted) else float("nan")

    # ---- leave-one-block-out (added after the plan) ----------------------------------------
    lobo = []
    for blk in sorted(R.design.map(blockmap).unique()):
        dl = [d for d in R.design.unique() if blockmap[d] == blk]
        sub = R[~R.design.isin(dl)]
        if sub.event.sum() == 0 or len(sub) == 0:
            continue
        f = pooled_fit(sub)
        lobo.append(dict(block_dropped=int(blk), designs_dropped=dl, b_f=f["b"], converged=f["ok"]))
    lb = [x["b_f"] for x in lobo if x["converged"]]

    # ---- pattern decision ------------------------------------------------------------------
    inside_a = resA["q025"] <= obs["b"] <= resA["q975"]
    inside_b = resB["q025"] <= obs["b"] <= resB["q975"]
    outside_both = (not inside_a) and (not inside_b)
    sign_ok = (len(fitted) > 0) and (sign_share >= 0.6)
    placebo_ok = placebo.get("b") is not None and abs(placebo["b"]) < abs(obs["b"])
    pattern = bool(outside_both and sign_ok and placebo_ok)

    res.update(dict(
        verdict="estimated",
        pooled=pooled,
        null_a_design_year=resA,
        null_b_design_year_region=resB,
        future_placebo=placebo,
        per_design=dict(n_designs_fitted=int(len(fitted)), n_designs_failed_or_lt5=int(len(PD) - len(fitted)),
                        n_same_sign=n_same, same_sign_share=sign_share,
                        n_designs_ge5_events=int((PD.n_events >= MIN_EVENTS).sum())),
        odds_ratio_per_unit_f_bar=float(np.exp(obs["b"])),
        leave_one_block_out_added_after_plan=dict(
            n_blocks=len(lobo), min=float(min(lb)) if lb else None, max=float(max(lb)) if lb else None,
            blocks=lobo),
        pattern_checks=dict(outside_null_a_band=bool(not inside_a), outside_null_b_band=bool(not inside_b),
                            sign_share_ge_0_6=bool(sign_ok), placebo_smaller_than_b_f=bool(placebo_ok)),
        pattern_flag=pattern,
        note_p_convention="p_two_sided = (1 + #|null| >= |b_f|)/(1 + B), null centred at 0 as in build_l3n_nbr.py",
        note_per_design_ridge="intercept unpenalised, f_bar/nb_share/trend ridge 1e-4 (as design_fit in build_l3n_nbr.py)",
        note_placebo_sample="added after the plan: placebo rows = main used rows with y+2 <= last panel year",
    ))
    with open(OUT / "RESULTS_Q9.json", "w") as fh:
        json.dump(J(res), fh, indent=2)
    print(json.dumps(J({k: res[k] for k in ["counts", "pooled", "null_a_design_year", "null_b_design_year_region",
                                              "future_placebo", "per_design", "pattern_flag"]}), indent=2))


if __name__ == "__main__":
    main()
