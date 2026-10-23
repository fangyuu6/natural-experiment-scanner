#!/usr/bin/env python3
"""X5 (rule id EX-1, exploratory): do outcome families that statutes mention together also move together in the effect matrix?  Spearman and Mantel.

Rules: docs/plans/l3-meta/PREREG_X5_V1.md (+ amendment and hash records).  Reads the P5 and random-timing union arrays of refresh 20261018_r1 and D10_PAIRS_V1.csv.
Run: .venv_did/bin/python -I -B scripts/l3_meta/build_x5.py      Set VECLIB_MAXIMUM_THREADS=OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1.
"""
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
from scipy.stats import rankdata  # noqa: E402
import late_core as L  # noqa: E402

D = ROOT / "data/l3_meta/x5_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_X5_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_X5_V1_HASH.txt"
MIN_RECORDS = 3
MIN_COMMON = 10
MIN_PAIRS_INFERENCE = 30
R_REP, N_MANTEL, B_BOOT = 9999, 10000, 9999
SEEDS = dict(rep=20261060, mantel=20261061, boot=20261062)
MDC_LIMIT = 0.3
CODE = ["scripts/l3_meta/build_x5.py", "scripts/l3_meta/test_x5.py", "scripts/l3_meta/late_core.py", "scripts/l3_meta/build_r0.py", "scripts/l3_meta/build_m1.py",
        "scripts/l3_meta/m1_core.py", "scripts/l3_meta/coding_tools.py", "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_d4_d10.py"]


# ---------------------------------------------------------------------------------------------
def load_d10(path=L.D10):
    """(groups, families, counts (G, F)) from the state-law text mention counts."""
    rows = list(csv.DictReader(open(path, newline="")))
    groups = sorted({r["policy_group"] for r in rows})
    fams = sorted({r["family"] for r in rows})
    C = np.zeros((len(groups), len(fams)))
    for r in rows:
        v = r["n_texts_db_state"]
        C[groups.index(r["policy_group"]), fams.index(r["family"])] = float(v) if v not in ("", None) else 0.0
    return groups, fams, C


def cosine_weights(C):
    """Cosine similarity of the family columns of the group-share matrix (each group's mentions scaled to sum to 1)."""
    tot = C.sum(axis=1, keepdims=True)
    S = np.divide(C, tot, out=np.zeros_like(C), where=tot > 0)
    nrm = np.linalg.norm(S, axis=0)
    with np.errstate(all="ignore"):
        W = (S.T @ S) / np.outer(nrm, nrm)
    return np.nan_to_num(W)


def jaccard_weights(C):
    """Jaccard similarity of the sets of policy groups whose texts mention each family at all."""
    I = (C > 0).astype(float)
    inter = I.T @ I
    cnt = I.sum(axis=0)
    union = cnt[:, None] + cnt[None, :] - inter
    with np.errstate(all="ignore"):
        return np.nan_to_num(inter / union)


def logcount_weights(C):
    """Cosine similarity of the family columns of log(1 + count)."""
    S = np.log1p(C)
    nrm = np.linalg.norm(S, axis=0)
    with np.errstate(all="ignore"):
        return np.nan_to_num((S.T @ S) / np.outer(nrm, nrm))


def family_means(d, fams, exclude=L.DROP_FROM_CORE, nc=None):
    """For a Design: (zbar (F,), ZB (F, n_draws)): mean z of the used series of each family (NaN if none) and the same for each common placebo draw."""
    zs = d.P / d.sd[:, None]
    zbar = np.full(len(fams), np.nan)
    ZB = np.full((len(fams), zs.shape[1]), np.nan)
    for k, f in enumerate(fams):
        idx = [j for j, (s, fm) in enumerate(zip(d.sids, d.fam)) if fm == f and s not in exclude]
        if not idx or (nc is not None and (d.id, f) not in nc):
            continue
        zbar[k] = d.z[idx].mean()
        ZB[k] = zs[idx].mean(axis=0)
    return zbar, ZB


def comovement(Zbar):
    """(CM (F, F), N (F, F)): mean over designs having both families of the product of the family means."""
    ok = np.isfinite(Zbar)
    Z0 = np.where(ok, Zbar, 0.0)
    N = ok.T.astype(float) @ ok.astype(float)
    with np.errstate(all="ignore"):
        CM = (Z0.T @ Z0) / N
    return CM, N


def exact_null(ZBs):
    """Per design (F, F): mean over its common draws of the product of the family means; NaN where a family is missing."""
    out = []
    for ZB in ZBs:
        ok = np.isfinite(ZB).all(axis=1)
        E = np.full((ZB.shape[0], ZB.shape[0]), np.nan)
        ix = np.where(ok)[0]
        if len(ix):
            E[np.ix_(ix, ix)] = (ZB[ix] @ ZB[ix].T) / ZB.shape[1]
        out.append(E)
    return np.stack(out)


def null_from_exact(E, weights=None):
    """m_fg = weighted mean over designs having both of the per-design exact expectations."""
    ok = np.isfinite(E)
    w = np.ones(E.shape[0]) if weights is None else weights
    num = np.where(ok, E, 0.0) * w[:, None, None]
    den = ok * w[:, None, None]
    with np.errstate(all="ignore"):
        return num.sum(axis=0) / den.sum(axis=0)


def replicate_null(ZBs, R, rng, chunk=500):
    """Monte Carlo null mean of the co-movement: R replicates, each design one common draw shared by its families, independent across designs."""
    nd, F = len(ZBs), ZBs[0].shape[0]
    num = np.zeros((F, F))
    cnt = np.zeros((F, F))
    done = 0
    while done < R:
        r = min(chunk, R - done)
        Zr = np.stack([ZB[:, rng.integers(0, ZB.shape[1], r)].T for ZB in ZBs], axis=1)     # (r, nd, F)
        ok = np.isfinite(Zr)
        Z0 = np.where(ok, Zr, 0.0)
        num += np.einsum("rdf,rdg->fg", Z0, Z0)
        cnt += np.einsum("rdf,rdg->fg", ok.astype(float), ok.astype(float))
        done += r
    with np.errstate(all="ignore"):
        return num / cnt


def pair_index(N, min_common=MIN_COMMON):
    F = N.shape[0]
    return [(f, g) for f in range(F) for g in range(f + 1, F) if N[f, g] >= min_common]


def spearman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3:
        return float("nan")
    rx, ry = rankdata(x), rankdata(y)
    sx, sy = rx.std(), ry.std()
    return float(np.mean((rx - rx.mean()) * (ry - ry.mean())) / (sx * sy)) if sx > 0 and sy > 0 else float("nan")


def mantel(W, E_pairs, pairs, B, rng):
    """Permute the family labels of W jointly on rows and columns; returns the permutation distribution of the Spearman correlation."""
    F = W.shape[0]
    f_ix = np.array([p[0] for p in pairs])
    g_ix = np.array([p[1] for p in pairs])
    out = np.empty(B)
    ry = rankdata(E_pairs)
    sy = ry.std()
    for b in range(B):
        pi = rng.permutation(F)
        x = W[pi[f_ix], pi[g_ix]]
        rx = rankdata(x)
        sx = rx.std()
        out[b] = np.mean((rx - rx.mean()) * (ry - ry.mean())) / (sx * sy) if sx > 0 and sy > 0 else np.nan
    return out


def bootstrap_rho(Zbar, E_exact, W, pairs, block_of, B, rng):
    """Policy-family block bootstrap of the Spearman correlation, pair set fixed; the null mean is the exact per-design expectation."""
    nb = int(max(block_of)) + 1
    block_of = np.asarray(block_of)
    out = np.full(B, np.nan)
    f_ix = np.array([p[0] for p in pairs])
    g_ix = np.array([p[1] for p in pairs])
    wv = W[f_ix, g_ix]
    for b in range(B):
        m = L.block_multiplicity(block_of, nb, rng)
        CM, N = comovement_weighted(Zbar, m)
        mm = null_from_exact(E_exact, m)
        e = (CM - mm)[f_ix, g_ix]
        ok = np.isfinite(e)
        if ok.sum() >= 5:
            out[b] = spearman(wv[ok], e[ok])
    return out


def comovement_weighted(Zbar, m):
    ok = np.isfinite(Zbar)
    Z0 = np.where(ok, Zbar, 0.0)
    wf = ok * m[:, None]
    N = wf.T @ ok.astype(float)
    with np.errstate(all="ignore"):
        CM = ((Z0 * m[:, None]).T @ Z0) / N
    return CM, N


def verdict(rho, p, boot_lo, mdc, n_pairs):
    if n_pairs < MIN_PAIRS_INFERENCE:
        return "descriptive (fewer than 30 pairs)"
    if rho > 0 and p <= 0.05 and boot_lo > 0:
        return "statutes and effects draw the same outcome map"
    if rho <= 0 or boot_lo <= 0:
        return "disagree" if mdc < MDC_LIMIT else "inconclusive (low power)"
    return "inconclusive"


# ---------------------------------------------------------------------------------------------
def analyse(designs, W, fams_all, d10_fams, nc=None, label="", seeds=SEEDS, R=R_REP, Bm=N_MANTEL, Bb=B_BOOT):
    fams = [f for f in d10_fams if f in fams_all]
    pol = sorted({d.policy_family for d in designs.values()})
    block_of = [pol.index(d.policy_family) for d in designs.values()]
    Zbar, ZBs = [], []
    for d in designs.values():
        zb, ZB = family_means(d, fams, nc=nc)
        Zbar.append(zb)
        ZBs.append(ZB)
    Zbar = np.array(Zbar)
    CM, N = comovement(Zbar)
    pairs = pair_index(N)
    out = OrderedDict(label=label, n_designs=len(designs), n_policy_families=len(pol), families=fams, n_pairs=len(pairs))
    if len(pairs) < 3:
        out["verdict"] = "NOT_EVALUABLE"
        return out
    Wf = W[np.ix_([d10_fams.index(f) for f in fams], [d10_fams.index(f) for f in fams])]
    E_exact = exact_null(ZBs)
    m_exact = null_from_exact(E_exact)
    m_rep = replicate_null(ZBs, R, np.random.default_rng(seeds["rep"]))
    f_ix = np.array([p[0] for p in pairs])
    g_ix = np.array([p[1] for p in pairs])
    E = (CM - m_rep)[f_ix, g_ix]
    wv = Wf[f_ix, g_ix]
    rho = spearman(wv, E)
    pear = float(np.corrcoef(wv, E)[0, 1])
    perm = mantel(Wf, E, pairs, Bm, np.random.default_rng(seeds["mantel"]))
    p = L.one_sided_p_ge(rho, perm)
    boot = bootstrap_rho(Zbar, E_exact, Wf, pairs, block_of, Bb, np.random.default_rng(seeds["boot"]))
    bo = L.pct(boot)
    mdc = L.MDE_FACTOR * float(np.nanstd(perm, ddof=1))
    out.update(rho=rho, pearson=pear, mantel_p_one_sided=p, mantel_null_mean=float(np.nanmean(perm)), mantel_null_sd=float(np.nanstd(perm, ddof=1)), mdc=mdc, boot_ci=bo,
               boot_n=int(np.isfinite(boot).sum()), max_abs_diff_null_rep_vs_exact=float(np.nanmax(np.abs((m_rep - m_exact)[f_ix, g_ix]))),
               verdict=verdict(rho, p, bo[0], mdc, len(pairs)),
               pairs=[dict(f=fams[a], g=fams[b], W=float(Wf[a, b]), CM=float(CM[a, b]), null=float(m_rep[a, b]), E=float(CM[a, b] - m_rep[a, b]), n_designs=int(N[a, b])) for a, b in pairs])
    return out


def stage_run():
    n = L.require_records(REC, MIN_RECORDS)
    out_path = D / "RESULTS_X5_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    t0 = time.time()
    groups, d10_fams, Cnt = load_d10()
    W = cosine_weights(Cnt)
    nc = set()
    import csv as _csv
    with open(L.NC_CELLS, newline="") as fh:
        for r in _csv.DictReader(fh):
            nc.add((r["design_id"], r["outcome_family"]))
    res = OrderedDict()
    shown = {}
    for ref in ("P5", "U"):
        designs, skipped, U = L.design_objects(ref)
        fams_all = sorted({f for d in designs.values() for f in d.fam})
        res[ref] = analyse(designs, W, fams_all, d10_fams, label=ref)
        res[ref]["skipped_designs"] = skipped
        res[ref + "_no_channel_cells"] = analyse(designs, W, fams_all, d10_fams, nc=nc, label=ref + " no_channel cells only")
        print(ref, res[ref]["n_designs"], "designs", res[ref].get("rho"), res[ref].get("verdict"), round(time.time() - t0), "s", flush=True)
        shown[ref] = len(designs)
    designs, skipped, U = L.design_objects("P5")
    fams_all = sorted({f for d in designs.values() for f in d.fam})
    res["P5_W_jaccard"] = analyse(designs, jaccard_weights(Cnt), fams_all, d10_fams, label="P5, co-mention = Jaccard of mentioning groups")
    res["P5_W_logcount"] = analyse(designs, logcount_weights(Cnt), fams_all, d10_fams, label="P5, co-mention = cosine of log(1 + count)")
    info = OrderedDict(groups=len(groups), families_d10=d10_fams, mention_total=float(Cnt.sum()))
    stamp = OrderedDict(rule_id="EX-1", question="X5", version="X5-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seeds=SEEDS, hash_records=n,
                        prereg_sha256=L.sha256_file(PREREG), seconds=round(time.time() - t0, 1), code={f: L.sha256_file(ROOT / f) for f in CODE if (ROOT / f).exists()})
    D.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(L.clean(OrderedDict(stamp=stamp, d10=info, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return 0


def main(argv=None):
    argparse.ArgumentParser().parse_args(argv)
    return stage_run()


if __name__ == "__main__":
    sys.exit(main())
