#!/usr/bin/env python3
"""X6 (rule id EX-1, exploratory) on refresh 20261021_r1: copy of scripts/l3_meta/build_x6.py with the refresh changed.

Statistics, seeds, thresholds, hash-record checks and replicate counts are those of build_x6.py (PREREG_X6_V1).  Additive extension after the
registered outputs: X6_DESIGN_LEVEL.csv, X6_BY_DOMAIN.json, X6_LOO.json.
Run:  VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/new_angles_20261021/build_x6_r7.py [--out-dir DIR] [--reps 9999] [--boot 9999]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]                # repo root (this file sits three folders below it)
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE.parent))                       # scripts/l3_meta (m1_core, build_m1, x6_core, l3_pins, build_sweep, sweep_core)
import numpy as np  # noqa: E402
import m1_core as M  # noqa: E402

REFRESH = ROOT / "data/l3_meta/refresh/20261021_r1"
M.REFRESH = REFRESH                                       # must be set before build_m1 is imported (it binds paths at import)
import build_m1 as B1  # noqa: E402
import x6_core as X  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_X6_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_X6_V1_HASH.txt"
SWEEP_DIR = REFRESH / "sweep"
GRID = ROOT / "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv"
NC_CELLS = REFRESH / "d2/NC_CELLS_D2_V1.csv"
LIVE = REFRESH / "live/l2_set_live.json"
GATE = SWEEP_DIR / "GATE_REPORT.json"
CAL = REFRESH / "cal/RESULTS_CAL_V1.json"
EXT_STORE = ROOT / "data/l3_meta/sweep_ext_v1/series_store_ext_v1.npz"   # not a refresh file; covers the same 55 extension series as r1 (checked)
SEED_NULL, SEED_BOOT = 20261020, 20261021
MIN_SERIES_DESIGNS = 5
MIN_CELLS_SERIES = 5
N_DRAWS = 999
MIN_CELLS_DESIGN = 8
MIN_DESIGNS_DOMAIN = 6
DEFAULT_OUT = ROOT / "data/l3_meta/new_angles_20261021/x6_r7"


def perms_for(n_units, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(N_DRAWS):
        idx = list(range(n_units))
        rng.shuffle(idx)
        out.append(idx)
    return np.array(out)


def collect(designs, rows_by_design, stores, gate_mode, S, Bsw):
    """For every design: per supported cell the real z, the 999 draw z, and the gaps (base-year and lag-5) under the real and the 999 drawn labellings."""
    cells = []
    for d in designs:
        r = rows_by_design[d]
        ctx = S.load_design_context(d, r[1])
        design = ctx.primary.design
        units0 = list(design["units"])
        adopt0 = np.array([np.inf if design["adoption"][u] is None else float(design["adoption"][u]) for u in units0])
        perms = perms_for(len(units0), S.RI_SEED)
        a_files = [np.load(REFRESH / "sweep" / f"{d}.npz", allow_pickle=False), np.load(REFRESH / "sweep_ext" / f"{d}.npz", allow_pickle=False)]
        for z_file, which in zip(a_files, ("v1", "ext")):
            sids = [str(s) for s in z_file["series_ids"]]
            for i, sid in enumerate(sids):
                if str(z_file["code"][i]) != "SUPPORTED":
                    continue
                st = stores[which]
                mode = gate_mode.get(d, "rate") if which == "v1" else "rate"
                val = st.arr(sid, "val", mode)
                ok = st.arr(sid, "ok", mode).astype(bool)
                uidx = [st.ui.get(u) for u in units0]
                if any(x is None for x in uidx):
                    continue
                v = val[uidx]
                o = ok[uidx]
                y0 = st.years[0]
                yi_adopt = adopt0 - y0                           # year index of the adoption year
                A = np.vstack([yi_adopt[None, :], yi_adopt[perms]])    # row 0 real, rows 1..999 draws
                G1 = X.cohort_gaps(v, o, A, 1)
                G5 = X.cohort_gaps(v, o, A, 6)
                sd = float(z_file["sd"][i])
                cells.append(dict(design=d, series=sid, family=str(z_file["family"][i]), z_real=float(z_file["att"][i]) / sd, z_draw=z_file["placebo"][i] / sd, G1=G1, G5=G5))
    return cells


def avg_rank(x):
    """Average ranks (ties share the mean rank) of a 1-D finite array."""
    n = len(x)
    o = np.argsort(x, kind="mergesort")
    xs = x[o]
    _, start, counts = np.unique(xs, return_index=True, return_counts=True)
    r = np.empty(n)
    r[o] = np.repeat(start + (counts + 1) / 2.0, counts)
    return r


def spearman(a, b):
    ra, rb = avg_rank(np.asarray(a, float)), avg_rank(np.asarray(b, float))
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def stats_from(cells, idx, use="G1", scale=1.0):
    """Arrays for a subset of cell indices."""
    Z = np.array([cells[i]["z_real"] for i in idx])[None, :] / scale
    G = np.array([cells[i][use][0] for i in idx])[None, :]
    return Z, G


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--reps", type=int, default=9999)
    ap.add_argument("--boot", type=int, default=9999)
    args = ap.parse_args(argv)
    if not (REC.is_file() and PREREG.is_file()):
        sys.exit("hash record or prereg missing: " + str(REC))
    problems, n_rec = B1.verify_records_for(REC)
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    out_dir = Path(args.out_dir)
    if (out_dir / "RESULTS_X6_V1.json").exists():
        sys.exit("results exist: not overwritten")
    import l3_pins
    import build_sweep as Bsw
    import sweep_core as S
    l3_pins.install(Bsw)
    S.LIVE = LIVE
    S.load_l2()
    pins = Bsw.verify_l2()
    if pins:
        sys.exit("L2 modules differ from the sweep hash record: " + "; ".join(pins[:3]))
    t0 = time.time()
    rows_by = {r[0]: r for r in Bsw.roster()}
    gate = json.loads(GATE.read_text())
    gate_mode = {d["design_id"]: d.get("scale_mode") for d in gate["designs"]}
    stores = {"v1": S.SeriesStore(SWEEP_DIR / "series_store_v1.npz", SWEEP_DIR / "series_store_v1_count.npz"), "ext": S.SeriesStore(EXT_STORE)}
    designs = M.design_ids(REFRESH)
    cells = collect(designs, rows_by, stores, gate_mode, S, Bsw)
    # series with enough designs and cells
    by_series = defaultdict(list)
    for i, c in enumerate(cells):
        by_series[c["series"]].append(i)
    keep_series = sorted(s for s, ii in by_series.items() if len({cells[i]["design"] for i in ii}) >= MIN_SERIES_DESIGNS)
    keep = [i for s in keep_series for i in by_series[s]]
    keep = [i for i in keep if np.isfinite(cells[i]["z_real"]) and np.isfinite(cells[i]["G1"][0])]
    sid_map = {s: k for k, s in enumerate(keep_series)}
    sidx = np.array([sid_map[cells[i]["series"]] for i in keep])
    dnames = sorted({cells[i]["design"] for i in keep})
    dcode = np.array([dnames.index(cells[i]["design"]) for i in keep])
    live_rows = {x["design_id"]: x for x in json.loads(LIVE.read_text())["designs"]}
    live = {k: v["policy_family"] for k, v in live_rows.items()}
    pgroup = {}
    with open(NC_CELLS, newline="") as fh:
        for r in csv.DictReader(fh):
            pgroup[r["design_id"]] = r["policy_group"]
    grid = {}
    with open(GRID, newline="") as fh:
        for r in csv.DictReader(fh):
            grid[(r["policy_group"], r["outcome_family"])] = r["final_code"]
    kappa = json.loads(CAL.read_text())["question"]["CAL"]["kappa"]["kappa_hat"]
    nc = len(keep)
    ZD = np.array([cells[i]["z_draw"] for i in keep])                       # (nc, 999)
    GD = {k: np.array([cells[i][k][1:] for i in keep]) for k in ("G1", "G5")}  # (nc, 999)
    G0 = {k: np.array([cells[i][k][0] for i in keep]) for k in ("G1", "G5")}
    Z0 = np.array([cells[i]["z_real"] for i in keep])
    # common draws per design: finite z and finite G1 for all of its cells
    common = {}
    for j, d in enumerate(dnames):
        ii = np.where(dcode == j)[0]
        fin = np.isfinite(ZD[ii]).all(axis=0) & np.isfinite(GD["G1"][ii]).all(axis=0)
        common[j] = np.where(fin)[0]
    rng = np.random.default_rng(SEED_NULL)
    bsel = np.stack([common[j][rng.integers(0, len(common[j]), args.reps)] for j in range(len(dnames))], axis=1)   # (reps, nd)
    ar = np.arange(nc)[None, :]
    res = OrderedDict()

    def one(label, use, scale, cellmask=None, nser=None):
        m = np.ones(nc, bool) if cellmask is None else cellmask
        ii = np.where(m)[0]
        # series with at least MIN_CELLS_SERIES cells in the subset
        cnt = np.bincount(sidx[ii], minlength=len(keep_series))
        ii = ii[cnt[sidx[ii]] >= MIN_CELLS_SERIES]
        if len(ii) < 30:
            return dict(feasible=False, n_cells=int(len(ii)))
        Zr = (Z0[ii] / scale)[None, :]
        Gr = G0[use][ii][None, :]
        bo = float(X.within_beta(Zr, Gr, sidx[ii], len(keep_series))[0])
        bn = []
        for a in range(0, args.reps, 500):
            bs = bsel[a:a + 500]
            Zn = ZD[ii][np.arange(len(ii))[None, :], bs[:, dcode[ii]]]
            Gn = GD[use][ii][np.arange(len(ii))[None, :], bs[:, dcode[ii]]]
            bn.append(X.within_beta(Zn, Gn, sidx[ii], len(keep_series)))
        bn = np.concatenate(bn)
        out = OrderedDict(feasible=True, n_cells=int(len(ii)), n_series=int(len(set(sidx[ii]))), n_designs=int(len(set(dcode[ii]))), beta_observed=bo,
                          null_mean=float(np.nanmean(bn)), null_sd=float(np.nanstd(bn, ddof=1)), null_q05_q95=[float(np.nanpercentile(bn, 5)), float(np.nanpercentile(bn, 95))])
        out["excess"] = bo - out["null_mean"]
        out["excess_in_null_sd"] = out["excess"] / out["null_sd"]
        out["rank_p_two_sided"] = float((1 + (np.abs(bn - out["null_mean"]) >= abs(bo - out["null_mean"])).sum()) / (1 + np.isfinite(bn).sum()))
        out["pooled_correlation_z_G"] = float(np.corrcoef(Z0[ii] / scale, G0[use][ii])[0, 1])
        return out

    res["primary_G1"] = one("primary", "G1", 1.0)
    # family-block bootstrap of the observed slope
    fams = sorted({live[d] for d in dnames})
    mem = [np.array([j for j, d in enumerate(dnames) if live[d] == f]) for f in fams]
    brng = np.random.default_rng(SEED_BOOT)
    W = np.zeros((args.boot, nc))
    cnt_d = np.zeros((args.boot, len(dnames)))
    for b in range(args.boot):
        for f in brng.integers(0, len(fams), len(fams)):
            cnt_d[b, mem[f]] += 1
    W = cnt_d[:, dcode]
    bb = X.within_beta(np.repeat(Z0[None, :], args.boot, axis=0), np.repeat(G0["G1"][None, :], args.boot, axis=0), sidx, len(keep_series), W=W)
    res["primary_G1"]["family_block_bootstrap_ci95"] = [float(np.nanpercentile(bb, 2.5)), float(np.nanpercentile(bb, 97.5))]
    res["primary_G1"]["family_block_interval_excludes_null_mean"] = bool(res["primary_G1"]["null_mean"] < np.nanpercentile(bb, 2.5) or res["primary_G1"]["null_mean"] > np.nanpercentile(bb, 97.5))
    res["lag5_G5"] = one("lag5", "G5", 1.0)
    res["kappa_scaled_G1"] = one("kappa", "G1", kappa)
    cls = np.array([grid.get((pgroup.get(cells[i]["design"]), cells[i]["family"])) for i in keep])   # designs absent from NC_CELLS (13 of 50 in r1) are unclassified
    res["class_none"] = one("none", "G1", 1.0, cls == "no_channel")
    res["class_channel"] = one("channel", "G1", 1.0, cls == "channel")
    fam_of = np.array([cells[i]["family"] for i in keep])
    res["by_family"] = OrderedDict()
    for f in sorted(set(fam_of)):
        if (fam_of == f).sum() >= 30:
            res["by_family"][f] = one(f, "G1", 1.0, fam_of == f)
    stamp = OrderedDict(rule_id="EX-1", question="X6", version="X6-V1", refresh="data/l3_meta/refresh/20261021_r1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        reps=args.reps, boot=args.boot, seeds=dict(null=SEED_NULL, boot=SEED_BOOT), kappa=kappa, prereg_sha256=B1.sha256_file(PREREG), hash_records=n_rec,
                        n_designs=len(dnames), n_series=len(keep_series), n_cells=nc, n_cells_G_real_finite=int(np.isfinite(G0["G1"]).sum()), policy_families=len(fams),
                        seconds=round(time.time() - t0, 1),
                        code={f: B1.sha256_file(ROOT / f) for f in ("scripts/l3_meta/new_angles_20261021/build_x6_r7.py", "scripts/l3_meta/build_x6.py", "scripts/l3_meta/x6_core.py",
                                                                    "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py")})
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "X6_CELLS_V1.csv", "x", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["design", "series", "family", "z_real", "G1_real", "G5_real", "G1_placebo_mean", "z_placebo_sd"])
        for k, i in enumerate(keep):
            c = cells[i]
            w.writerow([c["design"], c["series"], c["family"], f"{c['z_real']:.6f}", f"{c['G1'][0]:.6f}", f"{c['G5'][0]:.6f}", f"{np.nanmean(c['G1'][1:]):.6f}", f"{np.nanstd(c['z_draw']):.6f}"])
    (out_dir / "RESULTS_X6_V1.json").write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: {a: v.get(a) for a in ("n_cells", "beta_observed", "null_mean", "null_sd", "rank_p_two_sided")} for k, v in res.items() if isinstance(v, dict) and "beta_observed" in v}, default=float))

    # ---- additive extension (does not touch the registered outputs above) ----
    dom_of = {d: live_rows[d].get("domain") for d in dnames}
    fam_design = {d: live[d] for d in dnames}
    rows_dl = []
    for j, d in enumerate(dnames):
        ii = np.where(dcode == j)[0]
        if len(ii) < MIN_CELLS_DESIGN:
            continue
        cm = common[j]
        sp_real = spearman(Z0[ii], G0["G1"][ii])
        sp_null = np.array([spearman(ZD[ii, k], GD["G1"][ii, k]) for k in cm])
        rows_dl.append(dict(design=d, n_cells=int(len(ii)), spearman_real=sp_real, spearman_null_mean=float(np.nanmean(sp_null)) if len(sp_null) else float("nan"),
                            spearman_null_sd=float(np.nanstd(sp_null, ddof=1)) if len(sp_null) > 1 else float("nan"), n_common_draws=int(len(cm)),
                            domain=dom_of[d], policy_family=fam_design[d]))
    with open(out_dir / "X6_DESIGN_LEVEL.csv", "x", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["design", "n_cells", "spearman_real", "spearman_null_mean", "spearman_null_sd", "n_common_draws", "domain", "policy_family"])
        for r in rows_dl:
            w.writerow([r["design"], r["n_cells"], f"{r['spearman_real']:.6f}", f"{r['spearman_null_mean']:.6f}", f"{r['spearman_null_sd']:.6f}", r["n_common_draws"],
                        r["domain"], r["policy_family"]])

    dom_counts = defaultdict(int)
    for d in dnames:
        dom_counts[dom_of[d]] += 1
    by_domain = OrderedDict()
    for dom in sorted(k for k in dom_counts if k is not None):
        if dom_counts[dom] >= MIN_DESIGNS_DOMAIN:
            m = np.array([dom_of[dnames[j]] == dom for j in dcode])
            by_domain[dom] = one(f"domain:{dom}", "G1", 1.0, m)
    by_domain_out = OrderedDict(stamp=OrderedDict(refresh=stamp["refresh"], seeds=stamp["seeds"], reps=args.reps, design_domain_counts={k: v for k, v in dom_counts.items()},
                                                  min_designs=MIN_DESIGNS_DOMAIN), results=by_domain)
    (out_dir / "X6_BY_DOMAIN.json").write_text(json.dumps(B1.clean(by_domain_out), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")

    loo = OrderedDict()
    for f in fams:
        m = np.array([fam_design[dnames[j]] != f for j in dcode])
        loo[f] = one(f"drop:{f}", "G1", 1.0, m)
    loo_out = OrderedDict(stamp=OrderedDict(refresh=stamp["refresh"], seeds=stamp["seeds"], reps=args.reps, families=fams), full_primary_G1=res["primary_G1"], results=loo)
    (out_dir / "X6_LOO.json").write_text(json.dumps(B1.clean(loo_out), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"design_level_rows": len(rows_dl), "domains": list(by_domain.keys()), "loo_families": len(loo)}, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
