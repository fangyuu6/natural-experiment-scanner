#!/usr/bin/env python3
"""T64-SIGNED-V1 analysis stage (rule id EX-1, exploratory).  Rules: docs/plans/l3-meta/PREREG_T64_SIGNED_PARTY_V1.md.

  pin    write the supplement PREREG_T64_SIGNED_PARTY_V1_HASH_S1.txt (direction table, coder files, adjudicator file, code) -- before `run`
  run    verify the records, merge the frozen direction table with the DK panel and the state-year politics, fit everything of sections 4 to 6,
         write data/l3_meta/t64_signed_v1/RESULTS_T64_SIGNED_PARTY_V1.json (write-once)

Run:  .venv_did/bin/python -I -B scripts/l3_meta/t64_run.py pin|run [--workers N] [--quick]
`--quick` (tests only) shrinks the bootstrap/placebo counts and writes nowhere inside the repository.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import multiprocessing as mp
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import t64_core as C  # noqa: E402

OUT = ROOT / "data/l3_meta/t64_signed_v1"
DOCS = ROOT / "docs/plans/l3-meta"
REC = DOCS / "PREREG_T64_SIGNED_PARTY_V1_HASH.txt"
REC_S1 = DOCS / "PREREG_T64_SIGNED_PARTY_V1_HASH_S1.txt"
PREREG = DOCS / "PREREG_T64_SIGNED_PARTY_V1.md"
DK = ROOT / "data/l3_pooled/downloads/DVKim_SPID_iqt.dta"
NATIVE = ROOT / "data/l3_pooled/downloads/SPID_v1.2_policies.tab"
POLITICS = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
ADJ = ROOT / "data/l3_meta/covariates/state_adjacency_v2.csv"
RECON = ROOT / "data/l3_pooled/adoption_year_reconciliation.csv"
CODE_FILES = ["scripts/l3_meta/t64_core.py", "scripts/l3_meta/t64_run.py", "scripts/l3_meta/t64_signed.py"]
CODER_FILES = [f"data/l3_meta/t64_signed_v1/coding/{d}/labels_chunk_{k}.csv" for d in ("coder1", "coder2") for k in range(1, 7)] + \
              ["data/l3_meta/t64_signed_v1/coding/adjudicator/labels.csv", "data/l3_meta/t64_signed_v1/coding/adjudication_packets.csv"]
MIN_EVENTS = 10
SEEDS = dict(boot=20261016, shuffle=20261017, shuffle_topic=20261018, tau_placebo=20261019, crossfit=20261020)
FULL = dict(B_boot=499, B_shuffle=1000, B_tau=100, B_cross=100)
QUICK = dict(B_boot=6, B_shuffle=6, B_tau=3, B_cross=3)
_G = {}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_pins(path):
    text = Path(path).read_text(encoding="utf-8")
    pins = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    files = {}
    L = text.splitlines()
    for i, line in enumerate(L):
        m = re.match(r"^file:\s*(\S+)", line)
        if m and i + 1 < len(L):
            s = re.match(r"^sha256:\s*([0-9a-f]{64})", L[i + 1])
            if s:
                files[m.group(1)] = s.group(1)
    return pins, files


def verify_records(require_s1):
    """Pins of the main record and of its supplements; a later record replaces the line of the same path in an earlier one."""
    problems = []
    recs = [REC] + (sorted(REC.parent.glob(REC.stem + "_S*.txt"), key=lambda p: int(re.search(r"_S(\d+)\.txt$", p.name).group(1))) if require_s1 else [])
    if require_s1 and len(recs) < 2:
        problems.append("supplement S1 is missing")
    merged = {}
    for r in recs:
        if not r.is_file():
            problems.append(f"{r.name} is missing")
            continue
        pins, files = parse_pins(r)
        for p, h in {**pins, **files}.items():
            merged[p] = (h, r.name)
    for p, (h, name) in merged.items():
        if not (ROOT / p).is_file():
            problems.append(f"{p} (in {name}) is missing")
        elif sha256_file(ROOT / p) != h:
            problems.append(f"{p} differs from {name}")
    return problems


def stage_pin():
    if REC_S1.exists():
        sys.exit(f"{REC_S1.name} exists: not overwritten")
    table = OUT / "direction_table_v1.csv"
    report = OUT / "CODING_REPORT_V1.json"
    if not table.is_file():
        sys.exit("direction_table_v1.csv missing: run t64_signed.py freeze first")
    rep = json.loads(report.read_text())
    if not rep["anchors"]["passed"]:
        sys.exit(f"anchor check failed ({rep['anchors']['final_match']} of {rep['anchors']['n']}): the analysis is not run (pre-registration 3.4)")
    files = ["data/l3_meta/t64_signed_v1/direction_table_v1.csv", "data/l3_meta/t64_signed_v1/CODING_REPORT_V1.json"] + CODER_FILES
    lines = ["Supplement 1 to PREREG_T64_SIGNED_PARTY_V1_HASH.txt (frozen direction table, coder files and analysis code; rule id EX-1)",
             "reason: section 3.6 of the pre-registration; written after the coding and before the stage that merges adoption events with party data is run.",
             f"file: docs/plans/l3-meta/PREREG_T64_SIGNED_PARTY_V1.md", f"sha256: {sha256_file(PREREG)}", "(unchanged)",
             f"supplement_utc: {utc_now()}",
             "frozen files, coding outputs (pinned):"]
    lines += [f"  {p} sha256 {sha256_file(ROOT / p)}" for p in files]
    lines.append("frozen files, analysis code (pinned):")
    lines += [f"  {p} sha256 {sha256_file(ROOT / p)}" for p in CODE_FILES]
    lines.append("note: at this supplement no model of this analysis had been fitted on the merged panel; the code was tested on synthetic panels only (scripts/l3_meta/test_t64_core.py).")
    with open(REC_S1, "x", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"wrote {REC_S1.name} sha256 {sha256_file(REC_S1)}")


# ---------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------
def load_inputs():
    import pandas as pd
    panel = pd.read_stata(DK, columns=["policy", "state", "year", "adopt_year"])
    panel["policy"] = panel["policy"].astype(str)
    politics = pd.read_csv(POLITICS, dtype={"fips": str})
    adj = pd.read_csv(ADJ)
    adj = adj[adj["land_border_rule_v1"] == 1]
    pairs = list(zip(adj["state_a"], adj["state_b"]))
    nat = pd.read_csv(NATIVE, sep="\t")[["policy", "majortopic_str"]]
    dkt = pd.read_stata(DK, columns=["policy", "policyarea"]).drop_duplicates("policy")
    topic = dict(zip(dkt["policy"].astype(str), dkt["policyarea"].astype(str)))
    topic.update({str(a): str(b) for a, b in zip(nat["policy"], nat["majortopic_str"]) if isinstance(b, str)})
    conflicts = set()
    with open(RECON, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["status"] == "conflicting_year":
                conflicts.add(r["spid_policy"])
    direction = {}
    first_pass = {}
    # the table was written without quoting and 24 policy keys (uniform laws) contain commas: split from the right (6 fixed fields at the end)
    with open(OUT / "direction_table_v1.csv", encoding="utf-8") as fh:
        for line in fh.read().splitlines()[1:]:
            head, _a, _b, _third, final, agree = line.rsplit(",", 5)
            key = head.split(",", 1)[1]
            direction[key] = int(final)
            first_pass[key] = int(agree)
    return panel, politics, pairs, topic, conflicts, direction, first_pass


def prepare(inputs):
    """-> dict of arrays (cache) and counts."""
    panel, politics, pairs, topic, conflicts, direction, first_pass = inputs
    rows, counts = C.build_rows(panel, politics, pairs)
    ev = rows.groupby("policy")["y"].sum()
    elig = sorted(p for p in ev.index if ev[p] >= MIN_EVENTS)
    counts["policies_with_rows"] = int(rows["policy"].nunique())
    counts["policies_ge10_events"] = len(elig)
    s_of = {p: direction.get(p) for p in elig}
    counts["policies_ge10_without_label"] = sum(1 for p in elig if s_of[p] is None)
    elig = [p for p in elig if s_of[p] is not None]
    prim = [p for p in elig if s_of[p] != 0]
    counts["policies_sample_signed"] = len(prim)
    counts["policies_zero_coded_ge10"] = len(elig) - len(prim)
    counts["signed_plus"] = sum(1 for p in prim if s_of[p] == 1)
    counts["signed_minus"] = sum(1 for p in prim if s_of[p] == -1)
    rows = rows[rows["policy"].isin(elig)].copy()
    mu_sd = {}
    base = rows[rows["policy"].isin(prim)]
    for c in ("ideo", "lninc", "urban"):
        mu_sd[c] = (float(base[c].mean()), float(base[c].std(ddof=0)))
        rows[c] = (rows[c] - mu_sd[c][0]) / mu_sd[c][1]
    rows = rows.sort_values(["policy", "state", "year"], kind="stable").reset_index(drop=True)
    pols = sorted(elig)
    pidx = {p: i for i, p in enumerate(pols)}
    states = sorted(rows["state"].unique())
    sidx = {s: i for i, s in enumerate(states)}
    topics = sorted({topic.get(p, "?") for p in pols})
    cache = dict(
        pols=np.array(pols), s=np.array([s_of[p] for p in pols]), conflict=np.array([int(p in conflicts) for p in pols]),
        first_pass=np.array([first_pass.get(p, 0) for p in pols]), topic=np.array([topics.index(topic.get(p, "?")) for p in pols]),
        topics=np.array(topics), pc=rows["policy"].map(pidx).to_numpy(), sc=rows["state"].map(sidx).to_numpy(), states=np.array(states),
        year=rows["year"].to_numpy(), y=rows["y"].to_numpy(float), dem=rows["dem"].to_numpy(float), ideo=rows["ideo"].to_numpy(float),
        lninc=rows["lninc"].to_numpy(float), urban=rows["urban"].to_numpy(float), nbr=rows["nbr"].to_numpy(float),
        dur1=rows["dur1"].to_numpy(float), dur2=rows["dur2"].to_numpy(float), dur3=rows["dur3"].to_numpy(float))
    counts["rows_signed_sample"] = int(np.isin(cache["pc"], [i for i, p in enumerate(pols) if p in set(prim)]).sum())
    counts["events_signed_sample"] = int(cache["y"][np.isin(cache["pc"], [i for i, p in enumerate(pols) if p in set(prim)])].sum())
    counts["standardisation"] = mu_sd
    return cache, counts


def init_worker(path):
    z = np.load(path, allow_pickle=False)
    _G.clear()
    _G.update({k: z[k] for k in z.files})
    pc = _G["pc"]
    n_pol = int(pc.max()) + 1
    starts = np.searchsorted(pc, np.arange(n_pol), side="left")
    ends = np.searchsorted(pc, np.arange(n_pol), side="right")
    _G["starts"], _G["ends"] = starts, ends


# ---------------------------------------------------------------------------------------------
# pooled fits
# ---------------------------------------------------------------------------------------------
def pooled_fit(picks, s_vec, kind="M1", beta0=None, mask=None, clusters=False):
    """picks: list of policy indices (repeats allowed = bootstrap copies); s_vec: direction per policy index; mask: optional boolean row filter.
    Returns dict(coef {name: value}, beta, n_rows, n_events, n_policies[, cov])."""
    G = _G
    idx_parts, pcode = [], []
    for j, p in enumerate(picks):
        a, b = G["starts"][p], G["ends"][p]
        ii = np.arange(a, b)
        if mask is not None:
            ii = ii[mask[ii]]
        idx_parts.append(ii)
        pcode.append(np.full(len(ii), j))
    idx = np.concatenate(idx_parts)
    pc = np.concatenate(pcode)
    # drop copies without an event (separation) in this fit
    y = G["y"][idx]
    ev = np.bincount(pc, weights=y, minlength=len(picks))
    keep_pol = ev > 0
    row_keep = keep_pol[pc]
    idx, pc, y = idx[row_keep], pc[row_keep], y[row_keep]
    remap = np.cumsum(keep_pol) - 1
    pc = remap[pc]
    n_pol = int(keep_pol.sum())
    pick_arr = np.array(picks)[keep_pol]
    s = np.asarray(s_vec)[pick_arr][pc].astype(float)
    dem, ideo = G["dem"][idx], G["ideo"][idx]
    base = [ideo, G["lninc"][idx], G["urban"][idx], G["nbr"][idx], G["dur1"][idx], G["dur2"][idx], G["dur3"][idx]]
    if kind == "M1":
        cols, names = [s * dem, s * ideo] + base, ["signed_dem", "signed_ideo", "ideo", "lninc", "urban", "nbr", "dur1", "dur2", "dur3"]
    elif kind == "M1S":
        cols, names = [dem * (s > 0), dem * (s < 0), s * ideo] + base, ["dem_x_plus", "dem_x_minus", "signed_ideo", "ideo", "lninc", "urban", "nbr", "dur1", "dur2", "dur3"]
    elif kind == "M0":
        cols, names = [dem] + base, ["dem", "ideo", "lninc", "urban", "nbr", "dur1", "dur2", "dur3"]
    else:
        raise ValueError(kind)
    yr = G["year"][idx]
    ycode = np.unique(yr, return_inverse=True)[1]
    k = len(cols)
    X = C.design(np.column_stack(cols), pc, ycode, n_pol, int(ycode.max()) + 1)
    b0 = None
    if beta0 is not None:
        b0 = np.zeros(X.shape[1])
        b0[:min(k, len(beta0))] = beta0[:k]
    f = C.fit_logit(X, y, beta0=b0)
    out = dict(coef=dict(zip(names, f["beta"][:k].tolist())), beta=f["beta"][:k], n_rows=int(len(y)), n_events=int(y.sum()), n_policies=n_pol, converged=f["converged"])
    if clusters:
        V = C.cluster_cov(X, y, f, G["sc"][idx])
        out["se"] = dict(zip(names, np.sqrt(np.diag(V))[:k].tolist()))
    return out


# ---------------------------------------------------------------------------------------------
# per-policy Firth fits
# ---------------------------------------------------------------------------------------------
def policy_matrix(p, dem=None, rows=None):
    G = _G
    a, b = G["starts"][p], G["ends"][p]
    ii = np.arange(a, b) if rows is None else np.arange(a, b)[rows]
    d = G["dem"][ii] if dem is None else dem[ii]
    X = np.column_stack([np.ones(len(ii)), d, G["ideo"][ii], G["lninc"][ii], G["urban"][ii], G["nbr"][ii], (G["year"][ii] - 1990) / 10.0])
    return X, G["y"][ii], ii


def policy_theta(p, dem=None, rows=None):
    X, y, _ = policy_matrix(p, dem, rows)
    if y.sum() < 1 or y.sum() == len(y):
        return np.nan, np.nan
    b, cov, ok = C.firth_logit(X, y)
    if not ok or not np.isfinite(cov[1, 1]) or cov[1, 1] <= 0:
        return np.nan, np.nan
    return float(b[1]), float(np.sqrt(cov[1, 1]))


def all_thetas(policies, dem=None):
    out = np.array([policy_theta(p, dem) for p in policies])
    return out[:, 0], out[:, 1]


def tau_stats(theta, se, s=None, boot=0, seed=0):
    ok = np.isfinite(theta) & np.isfinite(se)
    th, sd = theta[ok], se[ok]
    r = C.reml(th, sd)
    r["share_positive"] = float(np.mean(th > 0))
    r["n_policies"] = int(ok.sum())
    r["n_failed"] = int((~ok).sum())
    if s is not None:
        phi = th * np.asarray(s)[ok]
        rs = C.reml(phi, sd)
        r["signed"] = dict(m_RE=rs["mu"], tau_signed=rs["tau"], share_positive=float(np.mean(phi > 0)), fixed_mean=C.fixed_mean(phi, sd))
    if boot:
        rng = np.random.default_rng(seed)
        mus, taus = [], []
        for _ in range(boot):
            j = rng.integers(0, len(th), len(th))
            rr = C.reml(th[j], sd[j])
            mus.append(rr["mu"]), taus.append(rr["tau"])
        r["mu_ci95"] = [float(np.percentile(mus, 2.5)), float(np.percentile(mus, 97.5))]
        r["tau_ci95"] = [float(np.percentile(taus, 2.5)), float(np.percentile(taus, 97.5))]
    return r


# ---------------------------------------------------------------------------------------------
# tasks for the pool
# ---------------------------------------------------------------------------------------------
def task_boot(args):
    seed, prim, s_vec, b0 = args
    _G["_b0"] = b0
    rng = np.random.default_rng(seed)
    picks = list(rng.choice(prim, size=len(prim), replace=True))
    f = pooled_fit(picks, s_vec, "M1", beta0=_G["_b0"])
    f0 = pooled_fit(picks, s_vec, "M0")
    return f["coef"]["signed_dem"], f0["coef"]["dem"]


def task_shuffle(args):
    seed, prim, s_vec, topic_strata, b0 = args
    _G["_b0"] = b0
    rng = np.random.default_rng(seed)
    s_new = np.array(s_vec).copy()
    if topic_strata:
        tp = _G["topic"]
        for t in np.unique(tp[prim]):
            members = [p for p in prim if tp[p] == t]
            s_new[members] = rng.permutation(np.array(s_vec)[members])
    else:
        s_new[prim] = rng.permutation(np.array(s_vec)[prim])
    f = pooled_fit(list(prim), s_new, "M1", beta0=_G["_b0"])
    # two-step estimator under the shuffled signs
    return f["coef"]["signed_dem"], s_new


def task_tau_placebo(args):
    seed, prim = args
    rng = np.random.default_rng(seed)
    G = _G
    ns, ny = len(G["states"]), int(G["year"].max() - G["year"].min() + 1)
    perm = np.array([rng.permutation(ns) for _ in range(ny)])
    yc = (G["year"] - G["year"].min()).astype(int)
    # table of dem by (state, year) from the rows (every state-year of the panel appears in some row)
    table = np.full((ns, ny), np.nan)
    table[G["sc"], yc] = G["dem"]
    dem_new = table[perm[yc, G["sc"]], yc]
    bad = np.isnan(dem_new)
    dem_new[bad] = G["dem"][bad]            # a state-year with no row keeps its own value (rare; no row there uses it for a policy at risk)
    th, se = all_thetas(prim, dem_new)
    r = tau_stats(th, se)
    return r["tau"], r["mu"]


def task_cross(args):
    seed, prim, s_vec = args
    rng = np.random.default_rng(seed)
    G = _G
    ns = len(G["states"])
    est = []
    for rev in (0, 1):
        num = den = 0.0
        numc = denc = 0.0
        for p in prim:
            a, b = G["starts"][p], G["ends"][p]
            st = G["sc"][a:b]
            present = np.unique(st)
            half = set(rng.permutation(present)[: len(present) // 2].tolist()) if rev == 0 else half_prev[p]
            if rev == 0:
                half_prev = globals().setdefault("_half_cache", {})
                half_prev[p] = set(present.tolist()) - half
            inA = np.isin(st, list(half))
            tA, sA = policy_theta(p, rows=inA)
            tB, sB = policy_theta(p, rows=~inA)
            if not (np.isfinite(tA) and np.isfinite(tB) and np.isfinite(sB)):
                continue
            w = 1.0 / sB ** 2
            num += np.sign(tA) * tB * w
            den += w
        est.append(num / den if den > 0 else np.nan)
        if rev == 0:
            pass
    globals().pop("_half_cache", None)
    return float(np.nanmean(est))


def run_pool(workers, cache_path, func, tasks, label):
    if workers <= 1:
        init_worker(cache_path)
        return [func(t) for t in tasks]
    with mp.get_context("spawn").Pool(workers, initializer=init_worker, initargs=(str(cache_path),)) as pool:
        out = pool.map(func, tasks, chunksize=1)
    return out


def interval(x, level=0.95):
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    return [float(np.percentile(x, lo)), float(np.percentile(x, hi))]


def stage_run(workers, quick):
    cfg = QUICK if quick else FULL
    problems = verify_records(True)
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:6]))
    t0 = time.time()
    inputs = load_inputs()
    cache, counts = prepare(inputs)
    out_dir = OUT if not quick else Path(sys.argv[sys.argv.index("--out-dir") + 1])
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "rows_cache_v1.npz"
    if cache_path.exists():           # a first attempt wrote the cache and stopped before any result: accept it only if it holds exactly these arrays
        z = np.load(cache_path, allow_pickle=False)
        if set(z.files) != set(cache) or any(not np.array_equal(z[k], cache[k], equal_nan=True) if cache[k].dtype.kind == "f" else not np.array_equal(z[k], cache[k]) for k in cache):
            sys.exit(f"{cache_path} exists and differs from the rebuilt rows: not overwritten")
    else:
        np.savez_compressed(cache_path, **cache)
    init_worker(cache_path)
    s_vec = _G["s"].astype(int)
    pols = _G["pols"]
    prim = [i for i in range(len(pols)) if s_vec[i] != 0]
    zero = [i for i in range(len(pols)) if s_vec[i] == 0]
    res = dict(counts=counts, config=cfg, seeds=SEEDS)
    # ---- pooled models
    m1 = pooled_fit(prim, s_vec, "M1", clusters=True)
    m0 = pooled_fit(prim, s_vec, "M0", clusters=True)
    m1s = pooled_fit(prim, s_vec, "M1S", clusters=True)
    _G["_b0"] = m1["beta"]
    res["M1"] = dict(b1=m1["coef"]["signed_dem"], se_state_cluster=m1["se"]["signed_dem"], coef=m1["coef"], n_rows=m1["n_rows"], n_events=m1["n_events"], n_policies=m1["n_policies"], converged=m1["converged"])
    res["M0"] = dict(b0=m0["coef"]["dem"], se_state_cluster=m0["se"]["dem"], n_rows=m0["n_rows"], n_events=m0["n_events"], n_policies=m0["n_policies"], converged=m0["converged"])
    res["M1S"] = dict(beta_D=m1s["coef"]["dem_x_plus"], beta_D_se=m1s["se"]["dem_x_plus"], beta_R=-m1s["coef"]["dem_x_minus"], beta_R_se=m1s["se"]["dem_x_minus"], converged=m1s["converged"])
    # ---- bootstrap over policies
    boots = run_pool(workers, cache_path, task_boot, [(SEEDS["boot"] + i, prim, s_vec, m1["beta"]) for i in range(cfg["B_boot"])], "boot")
    bb1 = np.array([b[0] for b in boots])
    bb0 = np.array([b[1] for b in boots])
    res["M1"]["b1_policy_bootstrap_ci95"] = interval(bb1)
    res["M0"]["b0_policy_bootstrap_ci95"] = interval(bb0)
    res["b1_minus_b0"] = dict(estimate=res["M1"]["b1"] - res["M0"]["b0"], bootstrap_ci95=interval(bb1 - bb0))
    # ---- heterogeneity
    th, se = all_thetas(prim)
    res["tau"] = tau_stats(th, se, s=s_vec[prim], boot=499 if not quick else 5, seed=SEEDS["boot"])
    res["tau"]["by_policy"] = [dict(policy=str(pols[p]), s=int(s_vec[p]), theta=float(a) if np.isfinite(a) else None, se=float(b) if np.isfinite(b) else None) for p, a, b in zip(prim, th, se)]
    tp = run_pool(workers, cache_path, task_tau_placebo, [(SEEDS["tau_placebo"] + i, prim) for i in range(cfg["B_tau"])], "tau")
    taus = np.array([t[0] for t in tp])
    res["tau_placebo"] = dict(draws=int(len(taus)), mean=float(taus.mean()), p95=float(np.percentile(taus, 95)), observed=res["tau"]["tau"],
                              share_ge_observed=float(np.mean(taus >= res["tau"]["tau"])), mu_mean=float(np.mean([t[1] for t in tp])))
    # ---- shuffled directions
    for name, strata, seed in (("all", False, SEEDS["shuffle"]), ("within_topic", True, SEEDS["shuffle_topic"])):
        sh = run_pool(workers, cache_path, task_shuffle, [(seed + i, prim, s_vec, strata, m1["beta"]) for i in range(cfg["B_shuffle"])], "shuffle")
        b = np.array([x[0] for x in sh])
        fit_ok = np.ones(len(prim), bool)
        okm = np.isfinite(th)
        mre = []
        for x in sh:
            phi = th[okm] * np.asarray(x[1])[prim][okm]
            mre.append(C.reml(phi, se[okm])["mu"])
        res[f"shuffle_{name}"] = dict(draws=int(len(b)), mean=float(b.mean()), sd=float(b.std(ddof=1)) if len(b) > 1 else None, p5_p95=interval(b, 0.90),
                                      observed=res["M1"]["b1"], share_ge_observed=float(np.mean(b >= res["M1"]["b1"])),
                                      share_abs_ge_observed=float(np.mean(np.abs(b) >= abs(res["M1"]["b1"]))),
                                      m_RE_placebo_mean=float(np.mean(mre)), m_RE_share_abs_ge_observed=float(np.mean(np.abs(mre) >= abs(res["tau"]["signed"]["m_RE"]))))
    # ---- cross-fit
    cf = np.array(run_pool(workers, cache_path, task_cross, [(SEEDS["crossfit"] + i, prim, s_vec) for i in range(cfg["B_cross"])], "cross"))
    res["cross_fit"] = dict(draws=int(len(cf)), median=float(np.nanmedian(cf)), p5_p95=interval(cf[np.isfinite(cf)], 0.90),
                            coded_fixed_mean=res["tau"]["signed"]["fixed_mean"])
    # ---- checks
    conflict = _G["conflict"]
    keep = [p for p in prim if not conflict[p]]
    c1 = pooled_fit(keep, s_vec, "M1", clusters=False)
    c0 = pooled_fit(keep, s_vec, "M0")
    thk, sek = all_thetas(keep)
    res["check_dates"] = dict(n_conflict_policies_in_sample=int(sum(conflict[p] for p in prim)), n_policies=len(keep), b1=c1["coef"]["signed_dem"], b0=c0["coef"]["dem"],
                              tau=tau_stats(thk, sek, s=s_vec[keep])["tau"])
    fp = _G["first_pass"]
    keep = [p for p in prim if fp[p]]
    c1 = pooled_fit(keep, s_vec, "M1")
    res["check_clean_labels"] = dict(n_policies=len(keep), b1=c1["coef"]["signed_dem"])
    if zero:
        z0 = pooled_fit(zero, s_vec, "M0", clusters=True)
        thz, sez = all_thetas(zero)
        rz = tau_stats(thz, sez)
        res["check_zero_coded"] = dict(n_policies=len(zero), b0=z0["coef"]["dem"], se=z0["se"]["dem"], mu_RE=rz["mu"], tau=rz["tau"])
    else:
        res["check_zero_coded"] = None
    era = {}
    for name, m in (("1961_1989", _G["year"] <= 1989), ("1990_2016", _G["year"] >= 1990)):
        e1 = pooled_fit(prim, s_vec, "M1", mask=m, clusters=True)
        era[name] = dict(b1=e1["coef"]["signed_dem"], se=e1["se"]["signed_dem"], n_policies=e1["n_policies"], n_events=e1["n_events"])
    res["check_era"] = era
    lo = {}
    for t in np.unique(_G["topic"][prim]):
        keep = [p for p in prim if _G["topic"][p] != t]
        lo[str(_G["topics"][t])] = pooled_fit(keep, s_vec, "M1")["coef"]["signed_dem"]
    res["check_leave_one_topic_out"] = dict(min=min(lo.values()), max=max(lo.values()), by_topic=lo)
    res["stamp"] = dict(rule_id="EX-1", version="T64-SIGNED-V1", run_utc=utc_now(), seconds=round(time.time() - t0, 1), prereg_sha256=sha256_file(PREREG),
                        hash_record_sha256=sha256_file(REC), hash_record_supplements={q.name: sha256_file(q) for q in sorted(REC.parent.glob(REC.stem + "_S*.txt"))}, direction_table_sha256=sha256_file(OUT / "direction_table_v1.csv"),
                        code={f: sha256_file(ROOT / f) for f in CODE_FILES}, inputs={str(p.relative_to(ROOT)): sha256_file(p) for p in (DK, NATIVE, POLITICS, ADJ, RECON)},
                        python=sys.version.split()[0], numpy=np.__version__, quick=quick)
    target = out_dir / "RESULTS_T64_SIGNED_PARTY_V1.json"
    with open(target, "x", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, allow_nan=False, default=float)
    print(f"wrote {target}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["pin", "run"])
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out-dir")
    a = ap.parse_args()
    if a.stage == "pin":
        stage_pin()
    else:
        stage_run(a.workers, a.quick)
