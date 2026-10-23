#!/usr/bin/env python3
"""X6 (rule id EX-1, exploratory): does the adopters-minus-free baseline gap in an outcome predict its effect?  Slope tested against the same designs' placebo draws.

Rules: docs/plans/l3-meta/PREREG_X6_V1.md (+ hash record and supplements).  Needs the L2 engine read-only (design labels) and the series stores; no ATT is used to build G.
Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_x6.py [--out-dir DIR] [--reps 9999] [--boot 9999]
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
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import m1_core as M  # noqa: E402
import build_m1 as B1  # noqa: E402
import x6_core as X  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_X6_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_X6_V1_HASH.txt"
SWEEP_DIR = M.REFRESH / "sweep"
GRID = ROOT / "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv"
NC_CELLS = M.REFRESH / "d2/NC_CELLS_D2_V1.csv"
LIVE = M.REFRESH / "live/l2_set_live.json"
GATE = SWEEP_DIR / "GATE_REPORT.json"
CAL = M.REFRESH / "cal/RESULTS_CAL_V1.json"
EXT_STORE = ROOT / "data/l3_meta/sweep_ext_v1/series_store_ext_v1.npz"
SEED_NULL, SEED_BOOT = 20261020, 20261021
MIN_SERIES_DESIGNS = 5
MIN_CELLS_SERIES = 5
N_DRAWS = 999


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
        a_files = [np.load(M.REFRESH / "sweep" / f"{d}.npz", allow_pickle=False), np.load(M.REFRESH / "sweep_ext" / f"{d}.npz", allow_pickle=False)]
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


def stats_from(cells, idx, use="G1", scale=1.0):
    """Arrays for a subset of cell indices."""
    Z = np.array([cells[i]["z_real"] for i in idx])[None, :] / scale
    G = np.array([cells[i][use][0] for i in idx])[None, :]
    return Z, G


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/x6_v1"))
    ap.add_argument("--reps", type=int, default=9999)
    ap.add_argument("--boot", type=int, default=9999)
    args = ap.parse_args(argv)
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
    designs = M.design_ids()
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
    live = {x["design_id"]: x["policy_family"] for x in json.loads(LIVE.read_text())["designs"]}
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
    cls = np.array([grid.get((pgroup[cells[i]["design"]], cells[i]["family"])) for i in keep])
    res["class_none"] = one("none", "G1", 1.0, cls == "no_channel")
    res["class_channel"] = one("channel", "G1", 1.0, cls == "channel")
    fam_of = np.array([cells[i]["family"] for i in keep])
    res["by_family"] = OrderedDict()
    for f in sorted(set(fam_of)):
        if (fam_of == f).sum() >= 30:
            res["by_family"][f] = one(f, "G1", 1.0, fam_of == f)
    stamp = OrderedDict(rule_id="EX-1", question="X6", version="X6-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reps=args.reps, boot=args.boot,
                        seeds=dict(null=SEED_NULL, boot=SEED_BOOT), kappa=kappa, prereg_sha256=B1.sha256_file(PREREG), hash_records=n_rec, n_designs=len(dnames), n_series=len(keep_series),
                        n_cells=nc, n_cells_G_real_finite=int(np.isfinite(G0["G1"]).sum()), policy_families=len(fams), seconds=round(time.time() - t0, 1),
                        code={f: B1.sha256_file(ROOT / f) for f in ("scripts/l3_meta/build_x6.py", "scripts/l3_meta/x6_core.py", "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py")})
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "X6_CELLS_V1.csv", "x", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["design", "series", "family", "z_real", "G1_real", "G5_real", "G1_placebo_mean", "z_placebo_sd"])
        for k, i in enumerate(keep):
            c = cells[i]
            w.writerow([c["design"], c["series"], c["family"], f"{c['z_real']:.6f}", f"{c['G1'][0]:.6f}", f"{c['G5'][0]:.6f}", f"{np.nanmean(c['G1'][1:]):.6f}", f"{np.nanstd(c['z_draw']):.6f}"])
    (out_dir / "RESULTS_X6_V1.json").write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: {a: v.get(a) for a in ("n_cells", "beta_observed", "null_mean", "null_sd", "rank_p_two_sided")} for k, v in res.items() if isinstance(v, dict) and "beta_observed" in v}, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
