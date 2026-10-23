#!/usr/bin/env python3
"""M2 (rule id EX-1, exploratory): do the adopters' politics predict which outcomes load?  Raw (random-timing) reference.

Rules: docs/plans/l3-meta/PREREG_M2_V1.md (hash record PREREG_M2_V1_HASH.txt + supplements).  Reads the sweep files of refresh 20261017_r4 (real z = att / sd of the
random-timing placebo), the N1-V2 gaps of the same refresh and the D2 plausibility grid.  Writes data/l3_meta/m2_v1/ only.
Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_m2.py [--out-dir DIR] [--reps 9999]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import m1_core as M  # noqa: E402
import build_m1 as B1  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_M2_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_M2_V1_HASH.txt"
N1V2 = M.REFRESH / "n1/RESULTS_N1_V2.json"
GRID = ROOT / "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv"
NC_CELLS = M.REFRESH / "d2/NC_CELLS_D2_V1.csv"
LIVE = M.REFRESH / "live/l2_set_live.json"
CAL = M.REFRESH / "cal/RESULTS_CAL_V1.json"
SEED = 20261019
MIN_DESIGNS_SERIES = 15
MIN_CLASS_DESIGNS = 8
N_PERM = 1000
B_BOOT = 9999


def verify_records():
    return B1.verify_records_for(REC)


def slopes(Z, g, min_n):
    """OLS slope of z on g (with intercept) per series, over the designs where z is finite.  Z: (reps, nd, K); g: (nd,).  Returns (reps, K) with NaN below min_n."""
    ok = np.isfinite(Z)
    n = ok.sum(axis=1)
    Zc = np.where(ok, Z, 0.0)
    gg = np.where(ok, g[None, :, None], 0.0)
    with np.errstate(all="ignore"):
        gm = gg.sum(axis=1) / n
        zm = Zc.sum(axis=1) / n
        dg = np.where(ok, g[None, :, None] - gm[:, None, :], 0.0)
        dz = np.where(ok, Z - zm[:, None, :], 0.0)
        b = (dg * dz).sum(axis=1) / (dg ** 2).sum(axis=1)
    b[n < min_n] = np.nan
    return b


def design_logrms(Z):
    """0.5 * log(mean z^2) per design over its finite cells; (reps, nd)."""
    ok = np.isfinite(Z)
    with np.errstate(all="ignore"):
        m = (np.where(ok, Z, 0.0) ** 2).sum(axis=2) / ok.sum(axis=2)
        out = 0.5 * np.log(m)
    out[ok.sum(axis=2) == 0] = np.nan
    return out


def ols_beta(Y, X):
    """Coefficients of Y (reps, nd) on X (nd, p) with rows of Y that are finite; returns (reps, p)."""
    out = np.full((Y.shape[0], X.shape[1]), np.nan)
    for i in range(Y.shape[0]):
        ok = np.isfinite(Y[i])
        if ok.sum() > X.shape[1]:
            out[i] = np.linalg.lstsq(X[ok], Y[i, ok], rcond=None)[0]
    return out


def statistics(Z, g, logTn, cls_masks, min_n):
    """All M2 statistics on a (reps, nd, K) array.  cls_masks: {'all': None, 'none': (nd,K) bool, 'channel': (nd,K) bool}."""
    out = {}
    X = np.column_stack([np.ones(len(g)), g, logTn])
    for name, mk in cls_masks.items():
        Zc = Z if mk is None else np.where(mk[None], Z, np.nan)
        b = slopes(Zc, g, min_n if name == "all" else MIN_CLASS_DESIGNS)
        out[f"T_{name}"] = np.nanmean(b ** 2, axis=1)
        out[f"mean_slope_{name}"] = np.nanmean(b, axis=1)
        beta = ols_beta(design_logrms(Zc), X)
        out[f"beta_logrms_{name}"] = beta[:, 1]
        out[f"slopes_{name}"] = b
    out["T_channel_minus_none"] = out["T_channel"] - out["T_none"]
    out["beta_channel_minus_none"] = out["beta_logrms_channel"] - out["beta_logrms_none"]
    return out


def run(Zr, Zn, g, logTn, masks, series, fam, design_family, groups, ds, n_perm, rng, boot):
    obs = statistics(Zr, g, logTn, masks, MIN_DESIGNS_SERIES)
    nul = statistics(Zn, g, logTn, masks, MIN_DESIGNS_SERIES)
    res = OrderedDict()
    for k in obs:
        if k.startswith("slopes_"):
            continue
        o = float(obs[k][0])
        nl = nul[k]
        res[k] = OrderedDict(observed=o, null_mean=float(np.nanmean(nl)), null_sd=float(np.nanstd(nl, ddof=1)), null_q95=float(np.nanpercentile(nl, 95)), null_q05=float(np.nanpercentile(nl, 5)),
                             rank_p_two_sided=float((1 + (np.abs(nl - np.nanmean(nl)) >= abs(o - np.nanmean(nl))).sum()) / (1 + np.isfinite(nl).sum())))
    # slopes by series
    res["series_slopes_all"] = OrderedDict((s, (float(v) if np.isfinite(v) else None)) for s, v in zip(series, obs["slopes_all"][0]))
    res["series_slope_null_sd"] = OrderedDict((s, float(v)) for s, v in zip(series, np.nanstd(nul["slopes_all"], axis=0, ddof=1)))
    # family-mean slope
    fm = OrderedDict()
    for f in sorted({fam[s] for s in series}):
        idx = [i for i, s in enumerate(series) if fam[s] == f]
        vals = obs["slopes_all"][0][idx]
        nl = np.nanmean(nul["slopes_all"][:, idx], axis=1)
        if np.isfinite(vals).any():
            m = float(np.nanmean(vals))
            fm[f] = OrderedDict(n_series=len(idx), mean_slope=m, null_sd=float(np.nanstd(nl, ddof=1)), z_vs_null=float((m - np.nanmean(nl)) / np.nanstd(nl, ddof=1)))
    res["family_mean_slope"] = fm
    # permutation of the gaps within policy groups (placebo for the covariate)
    permstats = {k: [] for k in ("T_all", "mean_slope_all", "beta_logrms_all", "T_none", "T_channel", "beta_logrms_none", "beta_logrms_channel")}
    gr = np.array(groups)
    for _ in range(n_perm):
        gp = g.copy()
        for grp in np.unique(gr):
            ii = np.where(gr == grp)[0]
            gp[ii] = g[rng.permutation(ii)]
        st = statistics(Zr, gp, logTn, masks, MIN_DESIGNS_SERIES)
        for k in permstats:
            permstats[k].append(float(st[k][0]))
    res["gap_permutation_within_policy_group"] = OrderedDict(
        (k, OrderedDict(n=len(v), mean=float(np.nanmean(v)), q95=float(np.nanpercentile(v, 95)), share_ge_observed=float((1 + (np.abs(np.array(v)) >= abs(res[k]["observed"])).sum()) / (1 + len(v))))) for k, v in permstats.items())
    if boot:
        fams = sorted({design_family[d] for d in ds})
        mem = [np.array([i for i, d in enumerate(ds) if design_family[d] == f]) for f in fams]
        bs = {k: [] for k in ("T_all", "mean_slope_all", "beta_logrms_all", "beta_channel_minus_none", "T_channel_minus_none")}
        for _ in range(B_BOOT):
            pick = np.concatenate([mem[i] for i in rng.integers(0, len(fams), len(fams))])
            st = statistics(Zr[:, pick, :], g[pick], logTn[pick], {k: (None if v is None else v[pick]) for k, v in masks.items()}, MIN_DESIGNS_SERIES)
            for k in bs:
                bs[k].append(float(st[k][0]))
        res["family_block_bootstrap_ci95"] = OrderedDict((k, [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))]) for k, v in bs.items())
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/m2_v1"))
    ap.add_argument("--reps", type=int, default=9999)
    ap.add_argument("--perm", type=int, default=N_PERM)
    ap.add_argument("--boot", type=int, default=B_BOOT)
    args = ap.parse_args(argv)
    globals()["B_BOOT"] = args.boot
    problems, n_rec = verify_records()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    out_dir = Path(args.out_dir)
    if (out_dir / "RESULTS_M2_V1.json").exists():
        sys.exit("results exist: not overwritten")
    t0 = time.time()
    n1 = json.loads(N1V2.read_text())
    ev = {x["design_id"]: x for x in n1["designs"] if x.get("code") == "EVAL"}
    live = {d["design_id"]: d["policy_family"] for d in json.loads(LIVE.read_text())["designs"]}
    pgroup = {}
    with open(NC_CELLS, newline="") as fh:
        for r in csv.DictReader(fh):
            pgroup[r["design_id"]] = r["policy_group"]
    grid = {}
    with open(GRID, newline="") as fh:
        for r in csv.DictReader(fh):
            grid[(r["policy_group"], r["outcome_family"])] = r["final_code"]
    data, real = B1.load_real("U")
    d24 = [d for d in data if d in ev]
    data24 = {d: data[d] for d in d24}
    cnt = M.supported_counts(data24)
    series = sorted(s for s, c in cnt.items() if c >= MIN_DESIGNS_SERIES)
    universe, dense, core, fam = M.series_sets(data)
    rows = M.design_arrays(data, universe)
    ix = {s: i for i, s in enumerate(universe)}
    names_ix = [ix[s] for s in series]
    rows24 = {d: rows[d] for d in d24}
    rng = np.random.default_rng(SEED)
    Zn, Rd, ds = M.replicate_z(rows24, names_ix, args.reps, rng)
    Zr = B1.real_z(data, real, ds, series)
    g = np.array([np.mean(ev[d]["gaps"]) for d in ds])
    logTn = np.log(np.array([ev[d]["T_n"] for d in ds], float))
    groups = [pgroup[d] for d in ds]
    masks = {"all": None,
             "none": np.array([[grid.get((pgroup[d], fam[s])) == "no_channel" for s in series] for d in ds]),
             "channel": np.array([[grid.get((pgroup[d], fam[s])) == "channel" for s in series] for d in ds])}
    kappa = json.loads(CAL.read_text())["question"]["CAL"]["kappa"]["kappa_hat"]
    res = OrderedDict()
    res["raw"] = run(Zr, Zn, g, logTn, masks, series, fam, live, groups, ds, args.perm, np.random.default_rng(SEED + 1), True)
    res["kappa_scaled"] = run(Zr / kappa, Zn, g, logTn, masks, series, fam, live, groups, ds, 200, np.random.default_rng(SEED + 2), False)
    # secondary: the four gaps one at a time on the design arm (log RMS), placebo null from the same replicates
    sec = OrderedDict()
    X0 = np.column_stack([np.ones(len(ds)), logTn])
    for j, nm in enumerate(("dem_unified", "ideology", "log_income", "urban")):
        gj = np.array([ev[d]["gaps"][j] for d in ds])
        Xj = np.column_stack([np.ones(len(ds)), gj, logTn])
        bo = ols_beta(design_logrms(Zr), Xj)[0, 1]
        bn = ols_beta(design_logrms(Zn[:2000]), Xj)[:, 1]
        sec[nm] = OrderedDict(beta_logrms=float(bo), null_mean=float(np.nanmean(bn)), null_sd=float(np.nanstd(bn, ddof=1)))
    stamp = OrderedDict(rule_id="EX-1", question="M2", version="M2-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reps=args.reps, perm=args.perm, boot=B_BOOT, seed=SEED,
                        kappa=kappa, prereg_sha256=B1.sha256_file(PREREG), hash_records=n_rec, n_designs=len(ds), designs=ds, n_series=len(series), series=series,
                        n_cells_all=int(np.isfinite(Zr).sum()), n_cells_none=int((np.isfinite(Zr[0]) & masks["none"]).sum()), n_cells_channel=int((np.isfinite(Zr[0]) & masks["channel"]).sum()),
                        gaps_mean_by_design={d: float(x) for d, x in zip(ds, g)}, policy_families=len({live[d] for d in ds}), seconds=round(time.time() - t0, 1),
                        code={f: B1.sha256_file(ROOT / f) for f in ("scripts/l3_meta/build_m2.py", "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py")})
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "RESULTS_M2_V1.json").write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, results=res, secondary_single_gaps=sec)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: {a: res["raw"][k][a] for a in ("observed", "null_mean", "null_sd", "rank_p_two_sided")} for k in res["raw"] if isinstance(res["raw"][k], dict) and "observed" in res["raw"][k]}, default=float)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
