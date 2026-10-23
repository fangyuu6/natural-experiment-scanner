#!/usr/bin/env python3
"""X1 (stage 0 of M1, rule id EX-1, exploratory): the noise network and the power map of the outcome matrix, from PLACEBO DRAWS ONLY.

Rules: docs/plans/l3-meta/PREREG_X1_V1.md (hash record PREREG_X1_V1_HASH.txt and supplements).  Reads the keys series_ids, family, code and placebo of the sweep
files of refresh 20261017_r4 (random timing: SWEEP-V1 + SWEEP-EXT) and of the SELREF P5 references (89 original series); never att, z or sd.
Writes only the new output folder (default data/l3_meta/x1_v1).
Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_x1.py [--out-dir DIR] [--reps 9999]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import m1_core as M  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_X1_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_X1_V1_HASH.txt"
SEED = 20261018
MD_FACTOR = 2.8
MD_LIMIT = 0.5


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
    merged = {}
    recs = [REC] + sorted(REC.parent.glob(REC.stem + "_S*.txt"), key=lambda p: int(re.search(r"_S(\d+)\.txt$", p.name).group(1)))
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


def fam_vec(names, fam):
    return [fam[s] for s in names]


def one_null(null, n_rep, rng):
    t0 = time.time()
    data = M.load_null(null)
    universe, dense, core, fam = M.series_sets(data)
    rows = M.design_arrays(data, universe)
    Rbar, ncount = M.mean_R(rows)
    ix = {s: i for i, s in enumerate(universe)}
    out = dict(null=null, designs=len(data), universe_n=len(universe), dense_n=len(dense), core_n=len(core), core=core, dense=dense,
               designs_below_common_floor=[d for d, r in rows.items() if r is None or r["n_common"] < M.MIN_COMMON],
               n_common_min_median=[min(r["n_common"] for r in rows.values() if r), float(np.median([r["n_common"] for r in rows.values() if r]))])
    # --- m_eff, halves, label-permutation check
    def block(Rm, names):
        sub = Rm[np.ix_([ix[s] for s in names], [ix[s] for s in names])]
        return sub
    dense_R = block(Rbar, dense)
    out["m_eff_dense"] = M.m_eff(dense_R) if np.isfinite(dense_R).all() else None
    core_R = block(Rbar, core)
    out["m_eff_core"] = M.m_eff(core_R) if np.isfinite(core_R).all() else None
    halves = []
    for par in (0, 1):
        mask = np.arange(999) % 2 == par
        rh = M.design_arrays(data, universe, draw_mask=mask)
        Rh, _ = M.mean_R(rh)
        sub = block(Rh, dense)
        halves.append(M.m_eff(sub) if np.isfinite(sub).all() else None)
    out["m_eff_dense_halves"] = halves
    out["m_eff_halves_agree_within_10pct"] = bool(all(h is not None for h in halves) and abs(halves[0] - halves[1]) <= 0.10 * max(halves))
    rs = M.design_arrays(data, universe, shuffle_rng=np.random.default_rng(SEED + 1))
    Rsh, _ = M.mean_R(rs)
    sub = block(Rsh, dense)
    off = sub[~np.eye(len(dense), dtype=bool)]
    out["label_permutation_check"] = dict(mean_abs_offdiag_R=float(np.nanmean(np.abs(off))), max_abs_offdiag_R=float(np.nanmax(np.abs(off))),
                                          mean_abs_offdiag_R_real=float(np.nanmean(np.abs(dense_R[~np.eye(len(dense), dtype=bool)]))))
    offd = dense_R[~np.eye(len(dense), dtype=bool)]
    out["R_dense_offdiag"] = dict(mean=float(np.nanmean(offd)), mean_abs=float(np.nanmean(np.abs(offd))), q05_q95=[float(np.nanpercentile(offd, 5)), float(np.nanpercentile(offd, 95))],
                                  max=float(np.nanmax(offd)), min=float(np.nanmin(offd)))
    # --- replicate matrices over the core set
    names_ix = [ix[s] for s in core]
    Z, Rd, ds = M.replicate_z(rows, names_ix, n_rep, rng)
    K = len(core)
    Cs, eig = [], []
    for a in range(0, n_rep, 250):
        C, cnt = M.c_matrix(Z[a:a + 250], Rd)
        Cs.append(C)
    C = np.concatenate(Cs)
    eig = M.eigen_desc(C)
    cf = [fam[s] for s in core]
    ii = M.family_contrast(C, cf)
    iii, nblk = M.block_stat(C, core, M.LABOR_BLOCK, M.PROPERTY_BLOCK)
    iu = np.triu_indices(K, 1)
    sd_pair = C.std(axis=0, ddof=1)
    pair_md = (MD_FACTOR * sd_pair)[iu]
    out["joint_designs_per_pair_min"] = int(cnt[iu].min())
    out["null"] = dict(
        n_rep=n_rep, C_offdiag_mean=float(C[:, iu[0], iu[1]].mean()), C_diag_mean=float(np.mean([C[:, i, i].mean() for i in range(K)])),
        pair_null_sd=dict(min=float(sd_pair[iu].min()), median=float(np.median(sd_pair[iu])), max=float(sd_pair[iu].max())),
        pair_min_detectable=dict(min=float(pair_md.min()), median=float(np.median(pair_md)), max=float(pair_md.max()), n_pairs=int(len(pair_md)), n_above_0_5=int((pair_md > MD_LIMIT).sum())),
        diag_min_detectable=dict(min=float(MD_FACTOR * min(sd_pair[i, i] for i in range(K))), max=float(MD_FACTOR * max(sd_pair[i, i] for i in range(K)))),
        eigen_q95=[float(x) for x in np.nanpercentile(eig, 95, axis=0)], eigen_mean=[float(x) for x in np.nanmean(eig, axis=0)], eigen_sd=[float(x) for x in np.nanstd(eig, axis=0, ddof=1)],
        eigen_top_min_detectable=float(MD_FACTOR * np.nanstd(eig[:, 0], ddof=1)),
        stat_ii_family_contrast=dict(mean=float(np.nanmean(ii)), sd=float(np.nanstd(ii, ddof=1)), min_detectable=float(MD_FACTOR * np.nanstd(ii, ddof=1)),
                                     q95=float(np.nanpercentile(ii, 95))),
        stat_iii_labor_vs_property=dict(block_sizes=list(nblk), mean=float(np.nanmean(iii)), sd=float(np.nanstd(iii, ddof=1)),
                                        min_detectable=float(MD_FACTOR * np.nanstd(iii, ddof=1)), q95=float(np.nanpercentile(iii, 95))))
    out["sidak"] = dict(m=int(math.ceil(out["m_eff_dense"])) if out["m_eff_dense"] else None)
    out["seconds"] = round(time.time() - t0, 1)
    out["_R_dense"] = (dense, dense_R, ncount, ix)
    out["_pairs"] = (core, sd_pair, cnt)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/x1_v1"))
    ap.add_argument("--reps", type=int, default=9999)
    args = ap.parse_args(argv)
    problems, n_rec = verify_records()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    out_dir = Path(args.out_dir)
    if (out_dir / "RESULTS_X1_V1.json").exists():
        sys.exit("results exist: not overwritten")
    res = {}
    for k, null in enumerate(("U", "P5")):
        res[null] = one_null(null, args.reps, np.random.default_rng(SEED + 10 * k))
    out_dir.mkdir(parents=True, exist_ok=True)
    for null in res:
        dense, Rm, nc, ix = res[null].pop("_R_dense")
        with open(out_dir / f"X1_R_dense_{null}.csv", "x", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["series_a", "series_b", "R_mean", "n_designs"])
            for a, sa in enumerate(dense):
                for b, sb in enumerate(dense):
                    if a < b:
                        w.writerow([sa, sb, f"{Rm[a, b]:.6f}", int(nc[ix[sa], ix[sb]])])
        core, sd_pair, cnt = res[null].pop("_pairs")
        with open(out_dir / f"X1_core_pairs_{null}.csv", "x", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["series_a", "series_b", "null_sd_C", "min_detectable_C", "joint_designs", "dropped_stage1_md_gt_0_5"])
            for a, sa in enumerate(core):
                for b, sb in enumerate(core):
                    if a < b:
                        md = MD_FACTOR * sd_pair[a, b]
                        w.writerow([sa, sb, f"{sd_pair[a, b]:.6f}", f"{md:.6f}", int(cnt[a, b]), int(md > MD_LIMIT)])
    stamp = dict(rule_id="EX-1", question="X1", version="X1-V1", reps=args.reps, seed=SEED, prereg_sha256=sha256_file(PREREG), hash_records=n_rec,
                 code={f: sha256_file(ROOT / f) for f in ("scripts/l3_meta/build_x1.py", "scripts/l3_meta/m1_core.py")}, reads="series_ids, family, code, placebo only",
                 refresh="data/l3_meta/refresh/20261017_r4", python=sys.version.split()[0], numpy=np.__version__)
    text = json.dumps(dict(stamp=stamp, results=res), indent=1, ensure_ascii=False, default=float, allow_nan=False) + "\n"
    (out_dir / "RESULTS_X1_V1.json").write_text(text, encoding="utf-8")
    print(text[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
