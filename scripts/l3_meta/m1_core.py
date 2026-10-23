"""M1 / X1 library (rule id EX-1): outcome-matrix structure under placebo draws.  Pure functions plus the placebo-only loader.

Registered in docs/plans/l3-meta/PREREG_X1_V1.md (stage 0, placebo draws only) and PREREG_M1_V1.md (stage 1).  The X1 loader reads ONLY the keys
`series_ids`, `family`, `code` and `placebo` of the sweep files: the real ATT, z and sd never enter stage 0 (test_m1_core.py asserts it).
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
import numpy as np  # noqa: E402

REFRESH = ROOT / "data/l3_meta/refresh/20261017_r4"
P5_DIR = ROOT / "data/l3_meta/selref_v1/refs/P5"
PLACEBO_KEYS = ("series_ids", "family", "code", "placebo")
MIN_COMMON = 500                     # X1: a design needs this many draws finite for all its supported series
MIN_DESIGNS_R = 10                   # X1: R_ab needs this many designs
CORE_MIN_DESIGNS = 24                # M1 core set: supported in at least this many of the 28 designs
DENSE_MIN_DESIGNS = 20               # X1 dense block for m_eff
UNIVERSE_MIN_DESIGNS = 10
DROP_FROM_CORE = ("fbi_ucr_property_crime_rate", "fbi_ucr_violent_crime_rate",                      # aggregates whose parts are in the core
                  "dol_eta5159_state_ui_weeks_compensated", "dol_eta5159_state_ui_first_payments",  # near-duplicate UI series: initial claims is kept
                  "dol_eta5159_state_ui_continued_weeks_claimed")
LABOR_BLOCK = ("bls_laus_unemployment_rate", "dol_eta5159_state_ui_initial_claims", "cps_teen_employment", "cps_young_adult_employment")
PROPERTY_BLOCK = ("fbi_ucr_burglary_rate", "fbi_ucr_larceny_rate", "fbi_ucr_motor_vehicle_theft_rate")


def design_ids(refresh=REFRESH):
    return [os.path.basename(f)[:-4] for f in sorted(glob.glob(str(Path(refresh) / "sweep/*.npz"))) if not os.path.basename(f).startswith("series_store")]


def read_placebo_only(path, keys=PLACEBO_KEYS):
    """Dict of the placebo-side keys of one sweep file; any other key is refused."""
    for k in keys:
        if k not in PLACEBO_KEYS:
            raise KeyError(f"stage 0 may not read {k}")
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in keys}


def load_null(null, refresh=REFRESH, p5_dir=P5_DIR, reader=read_placebo_only):
    """{design: dict(series, family, code, placebo)} for null 'U' (random timing: SWEEP-V1 plus extension files of the refresh) or 'P5' (SELREF references of
    the 89 original series)."""
    out = {}
    for d in design_ids(refresh):
        if null == "U":
            a = reader(Path(refresh) / "sweep" / f"{d}.npz")
            b = reader(Path(refresh) / "sweep_ext" / f"{d}.npz")
            ser = list(a["series_ids"]) + list(b["series_ids"])
            fam = list(a["family"]) + list(b["family"])
            code = list(a["code"]) + list(b["code"])
            pl = np.vstack([a["placebo"], b["placebo"]])
        elif null == "P5":
            a = reader(Path(p5_dir) / f"{d}.npz")
            ser, fam, code, pl = list(a["series_ids"]), list(a["family"]), list(a["code"]), a["placebo"]
        else:
            raise ValueError(null)
        out[d] = dict(series=[str(s) for s in ser], family=[str(f) for f in fam], code=[str(c) for c in code], placebo=np.asarray(pl, float))
    return out


def supported_counts(data):
    cnt = {}
    for d in data.values():
        for s, c in zip(d["series"], d["code"]):
            if c == "SUPPORTED":
                cnt[s] = cnt.get(s, 0) + 1
    return cnt


def series_sets(data):
    """(universe, dense, core, family) under one null: sorted lists of series ids."""
    cnt = supported_counts(data)
    fam = {}
    for d in data.values():
        fam.update(dict(zip(d["series"], d["family"])))
    universe = sorted(s for s, c in cnt.items() if c >= UNIVERSE_MIN_DESIGNS)
    dense = sorted(s for s, c in cnt.items() if c >= DENSE_MIN_DESIGNS)
    core = sorted(s for s, c in cnt.items() if c >= CORE_MIN_DESIGNS and s not in DROP_FROM_CORE)
    return universe, dense, core, fam


def design_arrays(data, universe, draw_mask=None, shuffle_rng=None):
    """Per design: Z (len(universe), 999) placebo z (NaN where the series is not supported in the design or the draw is not common), R (U x U, NaN outside),
    the list of common draw indices.  z = placebo / (std, ddof 1, of the series' finite placebo draws)."""
    U = len(universe)
    ix = {s: i for i, s in enumerate(universe)}
    rows = {}
    for dname, d in data.items():
        sel = [(ix[s], j) for j, (s, c) in enumerate(zip(d["series"], d["code"])) if c == "SUPPORTED" and s in ix]
        if not sel:
            rows[dname] = None
            continue
        idx = np.array([a for a, _ in sel])
        P = d["placebo"][[j for _, j in sel]]
        common = np.where(np.isfinite(P).all(axis=0))[0]
        sd = np.nanstd(P, axis=1, ddof=1)
        corr_draws = common if draw_mask is None else np.array([c for c in common if draw_mask[c]], dtype=int)
        Z = np.full((U, P.shape[1]), np.nan)
        Zs = P / sd[:, None]
        Z[np.ix_(idx, common)] = Zs[:, common]
        R = np.full((U, U), np.nan)
        if len(corr_draws) >= (MIN_COMMON if draw_mask is None else MIN_COMMON // 2) and len(idx) > 1:
            Zc = Zs[:, corr_draws]
            if shuffle_rng is not None:                 # label-permutation check: each series' draws are shuffled independently
                Zc = np.stack([shuffle_rng.permutation(row) for row in Zc])
            R[np.ix_(idx, idx)] = np.corrcoef(Zc)
        elif len(common) >= MIN_COMMON and len(idx) == 1:
            R[idx[0], idx[0]] = 1.0
        rows[dname] = dict(Z=Z, R=R, common=common, n_common=int(len(common)), series_index=idx)
    return rows


def mean_R(rows, min_designs=MIN_DESIGNS_R):
    stack = np.stack([r["R"] for r in rows.values() if r is not None and r["n_common"] >= MIN_COMMON])
    n = np.sum(np.isfinite(stack), axis=0)
    with np.errstate(all="ignore"):
        m = np.nanmean(stack, axis=0)
    m[n < min_designs] = np.nan
    return m, n


def m_eff(R):
    """(sum of eigenvalues)^2 / sum of squared eigenvalues of a symmetric correlation block (negative eigenvalues clipped at 0)."""
    lam = np.clip(np.linalg.eigvalsh(R), 0, None)
    return float(lam.sum() ** 2 / (lam ** 2).sum())


def c_matrix(Z, Rd, mask_pairs=None):
    """C_ab = mean over designs having both series of (z_da z_db - R_d,ab).  Z: (reps, nd, K) with NaN for missing; Rd: (nd, K, K) with NaN outside the
    design's supported pairs.  Returns (C (reps, K, K), counts (K, K))."""
    ok = np.isfinite(Z)
    Zc = np.where(ok, Z, 0.0)
    prod = np.einsum("rda,rdb->rdab", Zc, Zc)
    okab = ok[:, :, :, None] & ok[:, :, None, :]
    Rz = np.where(np.isfinite(Rd), Rd, 0.0)[None]
    num = np.where(okab, prod - Rz, 0.0).sum(axis=1)
    cnt = okab.sum(axis=1)
    with np.errstate(all="ignore"):
        C = num / cnt
    return C, cnt[0]


def eigen_desc(C):
    """Eigenvalues in descending order for a batch of symmetric matrices (reps, K, K); NaN rows give NaN."""
    out = np.full(C.shape[:2], np.nan)
    for i in range(C.shape[0]):
        if np.isfinite(C[i]).all():
            out[i] = np.linalg.eigvalsh((C[i] + C[i].T) / 2)[::-1]
    return out


def family_contrast(C, fam):
    """Mean off-diagonal C within family minus across family, per matrix of the batch."""
    K = len(fam)
    same = np.array([[fam[a] == fam[b] and a != b for b in range(K)] for a in range(K)])
    diff = np.array([[fam[a] != fam[b] for b in range(K)] for a in range(K)])
    return C[:, same].mean(axis=1) - C[:, diff].mean(axis=1) if same.any() and diff.any() else np.full(C.shape[0], np.nan)


def block_stat(C, names, block_a, block_b):
    ia = [names.index(s) for s in block_a if s in names]
    ib = [names.index(s) for s in block_b if s in names]
    if not ia or not ib:
        return np.full(C.shape[0], np.nan), (len(ia), len(ib))
    return C[:, ia][:, :, ib].reshape(C.shape[0], -1).mean(axis=1), (len(ia), len(ib))


def replicate_z(rows, names_ix, n_rep, rng):
    """Replicate placebo z matrices: for each rep and design one draw from the design's common draws, shared by all its series.  Returns (reps, nd, K)."""
    ds = [d for d, r in rows.items() if r is not None and r["n_common"] >= MIN_COMMON]
    K = len(names_ix)
    Z = np.full((n_rep, len(ds), K), np.nan)
    for j, d in enumerate(ds):
        r = rows[d]
        pick = r["common"][rng.integers(0, len(r["common"]), n_rep)]
        Z[:, j, :] = r["Z"][names_ix][:, pick].T
    Rd = np.stack([rows[d]["R"][np.ix_(names_ix, names_ix)] for d in ds])
    return Z, Rd, ds
