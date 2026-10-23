#!/usr/bin/env python3
"""M4 (rule id EX-1, exploratory): outcome fingerprints against legal category, net of treated-state overlap.  PREREG_M4_V1.md.
Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_m4.py [--out-dir DIR] [--boot 9999] [--perm 9999]"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_n11 as N11  # noqa: E402
import build_m1 as B1  # noqa: E402
import m1_core as M  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_M4_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_M4_V1_HASH.txt"
NC_CELLS = M.REFRESH / "d2/NC_CELLS_D2_V1.csv"
GRID = ROOT / "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv"
N1V2 = M.REFRESH / "n1/RESULTS_N1_V2.json"
MIN_SERIES, MIN_WINDOW = 20, 10
SEEDS = dict(null=20261022, boot=20261023, perm=20261024)
N_REP = 1000


def profile_corr(Zt, i, j, mask=None):
    a, b = Zt[i], Zt[j]
    ok = np.isfinite(a) & np.isfinite(b)
    if mask is not None:
        ok &= mask[i] & mask[j]
    k = int(ok.sum())
    if k < 3:
        return np.nan, k
    x, y = a[ok] - a[ok].mean(), b[ok] - b[ok].mean()
    den = np.sqrt((x * x).sum() * (y * y).sum())
    return (float((x * y).sum() / den) if den > 0 else np.nan), k


def center(Z):
    """Subtract each series' mean over the designs where it is finite.  Z: (nd, K)."""
    return Z - np.nanmean(Z, axis=0, keepdims=True)


def wls_fit(y, X, w):
    sw = np.sqrt(w)
    return np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]


def pair_sets(Nn, WIN, n, min_series):
    return [(i, j) for i in range(n) for j in range(i + 1, n) if Nn[i, j] >= min_series and WIN[i, j] >= MIN_WINDOW]


def similarities(Z, pairs, rep_Z, mask=None, min_shared=1):
    """S, shared count, placebo expectation per pair."""
    Zt = center(Z)
    S = np.array([profile_corr(Zt, i, j, mask)[0] for i, j in pairs])
    k = np.array([profile_corr(Zt, i, j, mask)[1] for i, j in pairs])
    acc = np.zeros(len(pairs))
    cnt = np.zeros(len(pairs))
    for Zr in rep_Z:
        Zrt = center(Zr)
        for q, (i, j) in enumerate(pairs):
            v = profile_corr(Zrt, i, j, mask)[0]
            if np.isfinite(v):
                acc[q] += v
                cnt[q] += 1
    with np.errstate(all="ignore"):
        E = acc / cnt
    return S, k, E


def fit_model(St, same, O, Dg, w, with_D=True):
    cols = [np.ones(len(St)), same, O] + ([Dg] if with_D else [])
    X = np.column_stack(cols)
    ok = np.isfinite(St) & np.all(np.isfinite(X), axis=1)
    return wls_fit(St[ok], X[ok], w[ok]), ok


def infer(pairs, St, same_fn, O, Dg, w, groups, n, rng_b, rng_p, B, P, with_D=True):
    I = np.array([p[0] for p in pairs])
    J = np.array([p[1] for p in pairs])
    gl = sorted(set(groups))
    gidx = np.array([gl.index(g) for g in groups])
    same = (gidx[I] == gidx[J]).astype(float)
    beta, ok = fit_model(St, same, O, Dg, w, with_D)
    out = OrderedDict(n_pairs=int(ok.sum()), n_same=int(same[ok].sum()), n_groups=len(gl), coef=[float(x) for x in beta],
                      coef_names=["intercept", "same_group", "overlap"] + (["political_distance"] if with_D else []))
    bs = []
    for _ in range(B):
        cnt = np.bincount(rng_b.integers(0, len(gl), len(gl)), minlength=len(gl))
        m = cnt[gidx].astype(float)
        ww = w * m[I] * m[J]
        keep = ww > 0
        if keep.sum() < 8:
            continue
        b, _ = fit_model(St[keep], same[keep], O[keep], Dg[keep], ww[keep], with_D)
        bs.append(b[1])
    out["bootstrap_same_group_ci95"] = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    ge = 0
    for _ in range(P):
        perm = rng_p.permutation(len(gl))
        g2 = gidx[rng_p.permutation(n)]   # permute design labels (group sizes kept)
        sm = (g2[I] == g2[J]).astype(float)
        b, _ = fit_model(St, sm, O, Dg, w, with_D)
        ge += b[1] >= beta[1]
    out["permutation_p_one_sided"] = float((1 + ge) / (P + 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/m4_v1"))
    ap.add_argument("--boot", type=int, default=9999)
    ap.add_argument("--perm", type=int, default=9999)
    args = ap.parse_args(argv)
    problems, n_rec = B1.verify_records_for(REC)
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    out_dir = Path(args.out_dir)
    if (out_dir / "RESULTS_M4_V1.json").exists():
        sys.exit("results exist: not overwritten")
    t0 = time.time()
    D = N11.load_designs(N11.SWEEP, N11.SETS)
    O, C, Nn, WIN = N11.pair_tables(D)
    n = len(D)
    ids = [d["id"] for d in D]
    pg = {}
    with open(NC_CELLS, newline="") as fh:
        for r in csv.DictReader(fh):
            pg[r["design_id"]] = r["policy_group"]
    grid = {}
    with open(GRID, newline="") as fh:
        for r in csv.DictReader(fh):
            grid[(r["policy_group"], r["outcome_family"])] = r["final_code"]
    n1 = {x["design_id"]: x for x in json.loads(N1V2.read_text())["designs"] if x.get("code") == "EVAL"}
    groups = [pg[i] for i in ids]
    pairs = pair_sets(Nn, WIN, n, MIN_SERIES)
    Z = np.array([d["z"] for d in D])                                         # (nd, 89) real z, NaN unsupported
    sids = D[0]["sids"]
    famc = []
    # catalogue family of each V1 series from the sweep file
    zf = np.load(M.REFRESH / "sweep" / f"{ids[0]}.npz", allow_pickle=False)
    fam = [str(x) for x in zf["family"]]
    none_mask = np.array([[grid.get((pg[i], fam[k])) == "no_channel" for k in range(len(sids))] for i in ids])
    # replicate placebo z (one draw per design), V1 series only
    rng = np.random.default_rng(SEEDS["null"])
    pl = []
    for d in D:
        f = np.load(M.REFRESH / "sweep" / f"{d['id']}.npz", allow_pickle=False)
        code = np.array(f["code"]) == "SUPPORTED"
        P = np.array(f["placebo"], float)
        sd = np.array(f["sd"], float)
        pl.append((code, P / sd[:, None]))
    rep_Z = []
    for _ in range(N_REP):
        Zr = np.full_like(Z, np.nan)
        for q, (code, Pz) in enumerate(pl):
            fin = np.where(np.isfinite(Pz[code]).all(axis=0))[0]
            b = fin[rng.integers(0, len(fin))]
            Zr[q, code] = Pz[code, b]
        rep_Z.append(Zr)
    S, k, E = similarities(Z, pairs, rep_Z)
    St = S - E
    I = np.array([p[0] for p in pairs])
    J = np.array([p[1] for p in pairs])
    Ov = O[I, J]
    w = k.astype(float)
    def gap_vec(i):
        x = n1.get(ids[i])
        return None if x is None else np.array(x["gaps"], float)
    Dg = np.array([np.linalg.norm(gap_vec(a) - gap_vec(b)) if gap_vec(a) is not None and gap_vec(b) is not None else np.nan for a, b in pairs])
    res = OrderedDict()
    res["descriptives"] = OrderedDict(n_pairs=len(pairs), mean_S=float(np.nanmean(S)), mean_placebo_expectation=float(np.nanmean(E)), mean_S_tilde=float(np.nanmean(St)),
                                      same_group_pairs=int(sum(groups[a] == groups[b] for a, b in pairs)),
                                      mean_S_tilde_same=float(np.nanmean([x for x, (a, b) in zip(St, pairs) if groups[a] == groups[b]])),
                                      mean_S_tilde_diff=float(np.nanmean([x for x, (a, b) in zip(St, pairs) if groups[a] != groups[b]])),
                                      n_pairs_with_D=int(np.isfinite(Dg).sum()))
    rb, rp = np.random.default_rng(SEEDS["boot"]), np.random.default_rng(SEEDS["perm"])
    res["headline_with_D"] = infer(pairs, St, None, Ov, Dg, w, groups, n, rb, rp, args.boot, args.perm, True) if np.isfinite(Dg).sum() >= 30 else dict(feasible=False)
    res["all_pairs_without_D"] = infer(pairs, St, None, Ov, Dg, w, groups, n, np.random.default_rng(SEEDS["boot"] + 1), np.random.default_rng(SEEDS["perm"] + 1), args.boot, args.perm, False)
    # firearms out
    keep_d = [q for q, g in enumerate(groups) if g != "firearm_laws"]
    sub = [(a, b) for (a, b) in pairs if a in keep_d and b in keep_d]
    if sub:
        idx = [pairs.index(p_) for p_ in sub]
        Dg_s = Dg[idx]
        res["firearms_out_without_D"] = infer(sub, St[idx], None, Ov[idx], Dg_s, w[idx], groups, n, np.random.default_rng(SEEDS["boot"] + 2), np.random.default_rng(SEEDS["perm"] + 2),
                                              min(args.boot, 2999), min(args.perm, 2999), False)
    # none-coded series only
    S2, k2, E2 = similarities(Z, pairs, rep_Z[:200], mask=none_mask)
    ok2 = k2 >= 10
    pairs2 = [p_ for p_, o in zip(pairs, ok2) if o]
    if len(pairs2) >= 30:
        St2 = (S2 - E2)[ok2]
        res["none_coded_series_only_without_D"] = infer(pairs2, St2, None, Ov[ok2], Dg[ok2], k2[ok2].astype(float), groups, n, np.random.default_rng(SEEDS["boot"] + 3),
                                                        np.random.default_rng(SEEDS["perm"] + 3), min(args.boot, 2999), min(args.perm, 2999), False)
    else:
        res["none_coded_series_only_without_D"] = dict(feasible=False, n_pairs=len(pairs2))
    stamp = OrderedDict(rule_id="EX-1", question="M4", version="M4-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seeds=SEEDS, n_rep=N_REP, boot=args.boot, perm=args.perm,
                        prereg_sha256=B1.sha256_file(PREREG), hash_records=n_rec, n_designs=n, seconds=round(time.time() - t0, 1),
                        code={f: B1.sha256_file(ROOT / f) for f in ("scripts/l3_meta/build_m4.py", "scripts/l3_meta/build_n11.py", "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py")})
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "M4_PAIRS_V1.csv", "x", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["design_i", "design_j", "group_i", "group_j", "n_shared", "S", "placebo_expectation", "S_tilde", "overlap", "political_distance"])
        for q, (a, b) in enumerate(pairs):
            wr.writerow([ids[a], ids[b], groups[a], groups[b], int(k[q]), f"{S[q]:.5f}", f"{E[q]:.5f}", f"{St[q]:.5f}", f"{Ov[q]:.5f}", "" if not np.isfinite(Dg[q]) else f"{Dg[q]:.5f}"])
    (out_dir / "RESULTS_M4_V1.json").write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(B1.clean(res), default=float)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
