#!/usr/bin/env python3
"""M1 stage 1 (rule id EX-1, exploratory): how many independent dimensions does the policy-by-outcome effect matrix have?  Real z against the placebo nulls.

Rules: docs/plans/l3-meta/PREREG_M1_V1.md (+ HUB_RULING_20261018_M1_STAGE1.md), after the placebo-only stage X1 (PREREG_X1_V1.md, data/l3_meta/x1_v1).
Reads the sweep files of refresh 20261017_r4 and the SELREF P5 references; the real ATT and sd are opened here for the first time.
Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_m1.py [--out-dir DIR] [--reps 9999] [--check-reps 2999]
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

PREREG = ROOT / "docs/plans/l3-meta/PREREG_M1_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_M1_V1_HASH.txt"
X1 = ROOT / "data/l3_meta/x1_v1/RESULTS_X1_V1.json"
NC_CELLS = M.REFRESH / "d2/NC_CELLS_D2_V1.csv"
LIVE = M.REFRESH / "live/l2_set_live.json"
CAL = M.REFRESH / "cal/RESULTS_CAL_V1.json"
SEED = {"U": 20261018, "P5": 20261028}
B_BOOT = 9999
N_PERM = 200
MIN_JOINT = 5


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def parse_pins(path):
    text = Path(path).read_text(encoding="utf-8")
    pins = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    L = text.splitlines()
    for i, line in enumerate(L):
        m = re.match(r"^file:\s*(\S+)", line)
        if m and i + 1 < len(L):
            s = re.match(r"^sha256:\s*([0-9a-f]{64})", L[i + 1])
            if s:
                pins[m.group(1)] = s.group(1)
    return pins


def verify_records():
    return verify_records_for(REC)


def verify_records_for(rec):
    merged = {}
    recs = [rec] + sorted(rec.parent.glob(rec.stem + "_S*.txt"), key=lambda p: int(re.search(r"_S(\d+)\.txt$", p.name).group(1)))
    for r in recs:
        if r.is_file():
            for p, h in parse_pins(r).items():
                merged[p] = (h, r.name)
    problems = []
    for p, (h, name) in merged.items():
        if not (ROOT / p).is_file():
            problems.append(f"{p} (in {name}) is missing")
        elif sha256_file(ROOT / p) != h:
            problems.append(f"{p} differs from {name}")
    return problems, len(recs)


# ---------------------------------------------------------------------------------------------
def load_real(null):
    """Placebo-side data plus att and sd, per design."""
    data = M.load_null(null)
    real = {}
    for d in data:
        if null == "U":
            parts = [np.load(M.REFRESH / "sweep" / f"{d}.npz", allow_pickle=False), np.load(M.REFRESH / "sweep_ext" / f"{d}.npz", allow_pickle=False)]
        else:
            parts = [np.load(M.P5_DIR / f"{d}.npz", allow_pickle=False)]
        att = np.concatenate([p["att"] for p in parts])
        sd = np.concatenate([p["sd"] for p in parts])
        real[d] = (att, sd)
    return data, real


def real_z(data, real, ds, core):
    """(1, nd, K) real z = att / sd for SUPPORTED cells of the core series, NaN elsewhere."""
    Z = np.full((1, len(ds), len(core)), np.nan)
    for j, d in enumerate(ds):
        dd = data[d]
        att, sd = real[d]
        pos = {s: i for i, s in enumerate(dd["series"])}
        for k, s in enumerate(core):
            i = pos.get(s)
            if i is not None and dd["code"][i] == "SUPPORTED" and np.isfinite(att[i]) and np.isfinite(sd[i]) and sd[i] > 0:
                Z[0, j, k] = att[i] / sd[i]
    return Z


def abs_expect(rows, ds, names_ix):
    """A_dab = mean over the design's common draws of |z_a||z_b| (the null expectation for the |z| variant)."""
    out = np.full((len(ds), len(names_ix), len(names_ix)), np.nan)
    for j, d in enumerate(ds):
        r = rows[d]
        Zc = np.abs(r["Z"][np.ix_(names_ix, r["common"])])
        ok = np.isfinite(Zc).all(axis=1)
        idx = np.where(ok)[0]
        if len(idx):
            out[j][np.ix_(idx, idx)] = (Zc[idx] @ Zc[idx].T) / Zc.shape[1]
    return out


def stats_of(C, core, fam):
    e = M.eigen_desc(C)
    ii = M.family_contrast(C, [fam[s] for s in core])
    iii, nblk = M.block_stat(C, core, M.LABOR_BLOCK, M.PROPERTY_BLOCK)
    K = len(core)
    iu = np.triu_indices(K, 1)
    return dict(eig=e, ii=ii, iii=iii, nblk=nblk, diag=np.mean([C[:, i, i] for i in range(K)], axis=0), off=C[:, iu[0], iu[1]].mean(axis=1))


def run_one(core, null, rows_all, data, real, fam, n_rep, variant="signed", scale=1.0, design_filter=None, cell_mask=None, seed_shift=0, boot=False, design_family=None, perm=False):
    """Observed statistics against the replicate null for one (core, null, variant) combination."""
    universe = None
    ix = {s: i for i, s in enumerate(rows_all["_universe"])}
    names_ix = [ix[s] for s in core]
    rows = {d: r for d, r in rows_all.items() if d != "_universe" and (design_filter is None or design_filter(d))}
    rng = np.random.default_rng(SEED[null] + seed_shift)
    Z, Rd, ds = M.replicate_z(rows, names_ix, n_rep, rng)
    Zr = real_z(data, real, ds, core) / scale
    if variant == "abs":
        Z, Zr = np.abs(Z), np.abs(Zr)
        Rd = abs_expect(rows, ds, names_ix)
    if cell_mask is not None:
        mk = np.array([[cell_mask(d, s) for s in core] for d in ds])
        Z = np.where(mk[None], Z, np.nan)
        Zr = np.where(mk[None], Zr, np.nan)
    Cn = np.concatenate([M.c_matrix(Z[a:a + 250], Rd)[0] for a in range(0, n_rep, 250)])
    Co, cnt = M.c_matrix(Zr, Rd)
    K = len(core)
    if not (np.isfinite(Cn).all() and np.isfinite(Co).all()):
        return dict(feasible=False, reason="a pair has no joint design")
    if cnt[np.triu_indices(K, 1)].min() < MIN_JOINT:
        return dict(feasible=False, reason=f"a pair has fewer than {MIN_JOINT} joint designs")
    sn, so = stats_of(Cn, core, fam), stats_of(Co, core, fam)
    q95 = np.percentile(sn["eig"], 95, axis=0)
    lam = so["eig"][0]
    exceed = lam > q95
    out = OrderedDict(feasible=True, K=K, n_designs=len(ds), joint_designs_min=int(cnt[np.triu_indices(K, 1)].min()),
                      eigen_observed=[float(x) for x in lam[:10]], eigen_null_q95=[float(x) for x in q95[:10]],
                      eigen_rank_p=[float((1 + (sn["eig"][:, j] >= lam[j]).sum()) / (1 + n_rep)) for j in range(min(10, K))],
                      k=int(exceed.sum()), exceed_flags=[bool(x) for x in exceed[:10]])
    for nm in ("diag", "off", "ii", "iii"):
        o, nul = float(so[nm][0]), sn[nm]
        out[nm] = OrderedDict(observed=o, null_mean=float(np.nanmean(nul)), null_sd=float(np.nanstd(nul, ddof=1)), excess=o - float(np.nanmean(nul)),
                              rank_p_one_sided=float((1 + (nul >= o).sum()) / (1 + n_rep)), min_detectable=float(2.8 * np.nanstd(nul, ddof=1)))
    out["iii"]["block_sizes"] = list(so["nblk"])
    if boot and design_family is not None:
        fams = sorted({design_family[d] for d in ds})
        members = [np.array([i for i, d in enumerate(ds) if design_family[d] == f]) for f in fams]
        okd = np.isfinite(Zr[0])
        Mab = np.where(okd[:, :, None] & okd[:, None, :], (Zr[0][:, :, None] * Zr[0][:, None, :]) - np.where(np.isfinite(Rd), Rd, 0.0), 0.0).reshape(len(ds), -1)
        Oab = (okd[:, :, None] & okd[:, None, :]).reshape(len(ds), -1).astype(float)
        brng = np.random.default_rng(SEED[null] + 777 + seed_shift)
        W = np.zeros((B_BOOT, len(ds)))
        pick = brng.integers(0, len(fams), (B_BOOT, len(fams)))
        for b in range(B_BOOT):
            for f in pick[b]:
                W[b, members[f]] += 1
        with np.errstate(all="ignore"):
            Cb = ((W @ Mab) / (W @ Oab)).reshape(B_BOOT, K, K)
        okb = np.isfinite(Cb).all(axis=(1, 2))
        sb = stats_of(Cb[okb], core, fam)
        shift_ii, shift_iii = float(np.nanmean(sn["ii"])), float(np.nanmean(sn["iii"]))
        out["bootstrap_family_block"] = OrderedDict(
            n_resamples=int(okb.sum()), ii_excess_ci95=[float(np.nanpercentile(sb["ii"], 2.5) - shift_ii), float(np.nanpercentile(sb["ii"], 97.5) - shift_ii)],
            iii_excess_ci95=[float(np.nanpercentile(sb["iii"], 2.5) - shift_iii), float(np.nanpercentile(sb["iii"], 97.5) - shift_iii)],
            lambda1_ci95=[float(np.nanpercentile(sb["eig"][:, 0], 2.5)), float(np.nanpercentile(sb["eig"][:, 0], 97.5))])
    if perm:
        prng = np.random.default_rng(SEED[null] + 999)
        kk = []
        for _ in range(N_PERM):
            Zp = Zr.copy()
            for k in range(K):
                col = np.where(np.isfinite(Zr[0, :, k]))[0]
                Zp[0, col, k] = Zr[0, prng.permutation(col), k]
            Cp, _ = M.c_matrix(Zp, Rd)
            ep = M.eigen_desc(Cp)[0]
            kk.append(int((ep > q95).sum()))
        out["row_permutation_check"] = OrderedDict(n_perm=N_PERM, share_with_k_ge_1=float(np.mean([k >= 1 for k in kk])), mean_k=float(np.mean(kk)))
    return out


def clean(o):
    if isinstance(o, dict):
        return OrderedDict((k, clean(v)) for k, v in o.items())
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/m1_v1"))
    ap.add_argument("--reps", type=int, default=9999)
    ap.add_argument("--check-reps", type=int, default=2999)
    args = ap.parse_args(argv)
    problems, n_rec = verify_records()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    out_dir = Path(args.out_dir)
    if (out_dir / "RESULTS_M1_V1.json").exists():
        sys.exit("results exist: not overwritten")
    x1 = json.loads(X1.read_text())["results"]
    core18, core23 = x1["P5"]["core"], x1["U"]["core"]
    live = {d["design_id"]: d["policy_family"] for d in json.loads(LIVE.read_text())["designs"]}
    firearm = set()
    with open(NC_CELLS, newline="") as fh:
        nc = {}
        for r in csv.DictReader(fh):
            nc[(r["design_id"], r["outcome_family"])] = r["strict_nc"] == "1"
            if r["policy_group"] == "firearm_laws":
                firearm.add(r["design_id"])
    kappa = json.loads(CAL.read_text())["question"]["CAL"]["kappa"]["kappa_hat"]
    res = OrderedDict()
    t0 = time.time()
    prep = {}
    for null in ("U", "P5"):
        data, real = load_real(null)
        universe, dense, core, fam = M.series_sets(data)
        rows = M.design_arrays(data, universe)
        rows["_universe"] = universe
        prep[null] = (data, real, rows, fam)
    # --- primary: the 18 original core series under both nulls
    runs = OrderedDict()
    for null in ("U", "P5"):
        data, real, rows, fam = prep[null]
        runs[f"S18_{null}_signed"] = run_one(core18, null, rows, data, real, fam, args.reps, boot=True, design_family=live, perm=True)
    data, real, rows, fam = prep["U"]
    runs["S23_U_signed"] = run_one(core23, "U", rows, data, real, fam, args.reps, boot=True, design_family=live, perm=True)
    # --- variants (random timing)
    for tag, core in (("S18", core18), ("S23", core23)):
        runs[f"{tag}_U_abs"] = run_one(core, "U", rows, data, real, fam, args.reps, variant="abs")
        runs[f"{tag}_U_kappa_scaled"] = run_one(core, "U", rows, data, real, fam, args.reps, scale=kappa)
    # --- checks
    chk = OrderedDict()
    for tag, core, null in (("S18_U", core18, "U"), ("S18_P5", core18, "P5"), ("S23_U", core23, "U")):
        data, real, rows, fam = prep[null]
        c = OrderedDict()
        c["firearm_designs_dropped"] = run_one(core, null, rows, data, real, fam, args.check_reps, design_filter=lambda d: d not in firearm, seed_shift=1)
        fams_in_core = sorted({fam[s] for s in core})
        c["family_left_out"] = OrderedDict()
        for f in fams_in_core:
            sub = [s for s in core if fam[s] != f]
            c["family_left_out"][f] = run_one(sub, null, rows, data, real, fam, args.check_reps, seed_shift=2) if len(sub) >= 6 else dict(feasible=False, reason="fewer than 6 series left")
        c["negative_control_cells_only"] = run_one(core, null, rows, data, real, fam, args.check_reps, seed_shift=3,
                                                   cell_mask=lambda d, s, fam=fam: bool(nc.get((d, fam[s]), False)))
        chk[tag] = c
    # --- decision label (PREREG_M1_V1.md section 6)
    p, u = runs["S18_P5_signed"], runs["S18_U_signed"]
    boot_ok = lambda r: r.get("bootstrap_family_block", {}).get("ii_excess_ci95", [0, 0])[0] > 0
    if p["k"] == 0:
        label = "no structure detectable under P5"
    elif u["k"] >= 1 and p["k"] >= 1 and boot_ok(u) and boot_ok(p):
        label = "structure (provisional until R0 completes P5 for the extension series)"
    else:
        label = "timing-driven or not confirmed under both nulls"
    stamp = OrderedDict(rule_id="EX-1", question="M1", version="M1-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reps=args.reps, check_reps=args.check_reps,
                        seeds=SEED, bootstrap=B_BOOT, kappa=kappa, prereg_sha256=sha256_file(PREREG), x1_results_sha256=sha256_file(X1), hash_records=n_rec,
                        code={f: sha256_file(ROOT / f) for f in ("scripts/l3_meta/build_m1.py", "scripts/l3_meta/m1_core.py")}, refresh="data/l3_meta/refresh/20261017_r4",
                        core18=core18, core23=core23, firearm_designs=sorted(firearm), seconds=round(time.time() - t0, 1), python=sys.version.split()[0], numpy=np.__version__)
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(OrderedDict(stamp=stamp, label=label, runs=runs, checks=chk)), indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    (out_dir / "RESULTS_M1_V1.json").write_text(text, encoding="utf-8")
    print(label)
    print(json.dumps({k: {a: v.get(a) for a in ("k", "eigen_observed", "eigen_null_q95")} for k, v in runs.items() if v.get("feasible")}, default=float)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
