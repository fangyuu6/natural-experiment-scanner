#!/usr/bin/env python3
"""Independent recomputation of SELREF-V1 (amendment 5.4).  Plain loops; imports NOTHING from selref_core, build_selref, sweep_core, build_d1, build_d2, build_n1 or
the other builders (the L2 engine is imported read-only: that is the code the sweep applies; check_sweep_independent.py, itself an independent checker, supplies the
series, design and pattern readers).

Written from the amendment PREREG_AMEND_20261016_SELREF_V1.md (sections 2 to 4).  It
  (a) rebuilds the blocks and the permutations of every design and reference (U excepted) from the unit list, the adoption periods and the covariate file, and
      compares the block structure with the one stored in the design files (the block ORDER of PC5 is the sign of an eigenvector; the partition is checked and the
      stored order, which must equal the order or its exact reverse, is used for the draws);
  (b) recomputes the placebo ATTs of sampled supported (design, series) cells with the engine (series re-read, declaration and design rebuilt, ATT with fit_primary,
      every placebo ATT with ri.one_reference) under the permutations of (a), and of the primary entry of each of the 33 designs under P5 and N5;
  (c) recomputes every statistic of sections 3 and 4 from the stored arrays with plain loops: family statistics, D2 cells and estimate, E1, null width, the closed form
      of the spread and its bootstrap, D1 per design and theta, Q1 and its bootstrap, the paired change, the labels and the verdict;
  (d) rejects a copy of the results file with planted changes.
Any difference above 1e-9 relative (|x - y| <= 1e-9 max(1, |x|, |y|)) is a failure.  Writes nothing unless --out is given (a new file).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_selref_independent.py [--workers N] [--results FILE] [--skip-engine] [--out FILE]
"""
import argparse
import copy
import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
import random
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import check_sweep_independent as CS  # noqa: E402  (a checker: series, design and pattern readers for the engine part)

# The engine part imports the L2 modules.  SWEEP-V1 pins 82 of them by hash and keeps byte copies (data/l3_meta/sweep_v1/l2_files/).  The L2 loop keeps changing
# the working tree, so by default the checker imports the pinned copies through an overlay package tree that lives outside the repository (built by main(), found
# by the worker processes through the environment variable); everything that is not pinned is a symlink to the working-tree file.
if os.environ.get("SELREF_L2_OVERLAY"):
    sys.path.insert(0, os.environ["SELREF_L2_OVERLAY"])

OUT = ROOT / "data/l3_meta/selref_v1"
SWEEP = ROOT / "data/l3_meta/sweep_v1"
D2DIR = ROOT / "data/l3_meta/d2_plausibility"
LIVE = ROOT / "data/l3_meta/l2_set_live.json"
INVENTORY = ROOT / "docs/plans/l3-meta/OUTCOME_DIMENSION_INVENTORY_20261015.csv"
COV = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
EX1 = ROOT / "data/l3_meta/ex1_v3/RESULTS_EX1_V3.json"
RI_SEED = 20261002
N_DRAWS = 999
MIN_BLOCK = 5
RIDGE = 1.0
B_BOOT = 9999
N_REF = 9999
SEED_E1_D2, SEED_NULL, SEED_E1_D1 = [20261011, 32], [20261011, 32, 7], [20261011, 31]
SEED_SPREAD, SEED_Q1, SEED_PAIR = [20261011, 36, 1], [20261011, 36, 2], [20261011, 36, 3]
FLOOR_M, FLOOR_F = 40, 8
COLS = ("dem_unified_jan1", "citizen_ideology", "ln_pcpi_real_2024usd", "urban_share")
REFS = {"P5": ("timing", 5), "P3": ("timing", 3), "P8": ("timing", 8), "PC5": ("pc1", 5), "REG": ("region", 4), "N5": ("noise", 5)}
ALL_IDS = ["U", "P5", "P3", "P8", "PC5", "REG", "N5"]
REGION_ORDER = ["Northeast", "Midwest", "South", "West"]
REGION = {}
for _r, _s in (("Northeast", "CT ME MA NH RI VT NJ NY PA"), ("Midwest", "IL IN MI OH WI IA KS MN MO NE ND SD"),
               ("South", "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX"), ("West", "AZ CO ID MT NV NM UT WY AK CA HI OR WA")):
    for _st in _s.split():
        REGION[_st] = _r
ARCHIVE_GROUPS = ("prototype", "manual_batch")
GATE_PASS_FULL_YEAR = "annual_full_year_v1"

worst = {}
problems = []


# ---------------------------------------------------------------------------------------------
# comparison helpers
# ---------------------------------------------------------------------------------------------
def agree(x, y):
    return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))


def compare(label, got, want):
    """Record the difference between the results file (got) and this recomputation (want)."""
    if got is None or want is None:
        if got is not want:
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, bool) or isinstance(got, bool):
        if bool(got) != bool(want):
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, str) or isinstance(got, str):
        if got != want:
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, (list, tuple)) or isinstance(got, (list, tuple)):
        if len(got) != len(want):
            problems.append(f"{label}: lengths {len(got)} vs {len(want)}")
            return
        for i, (a, b) in enumerate(zip(got, want)):
            compare(f"{label}[{i}]", a, b)
        return
    d = abs(got - want)
    key = label.split(":")[-1].split(".")[-1]
    worst[key] = max(worst.get(key, 0.0), d / max(1.0, abs(got), abs(want)))
    if not agree(got, want):
        problems.append(f"{label}: {got!r} vs {want!r} (difference {d:.3g})")


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def mean_(v):
    return sum(v) / len(v)


def sd_(v):
    m = mean_(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


# ---------------------------------------------------------------------------------------------
# blocks and permutations, written from amendment 2.4 and 2.5
# ---------------------------------------------------------------------------------------------
def read_cov():
    out = {}
    for r in load_csv(COV):
        out[(r["state"], int(r["year"]))] = tuple(None if r[c] == "" else float(r[c]) for c in COLS)
    return out


def lower_median_(v):
    v = sorted(v)
    return v[(len(v) - 1) // 2]


def avg_ranks_(keys):
    order = sorted(range(len(keys)), key=lambda i: keys[i])
    ranks = [0.0] * len(keys)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and keys[order[j + 1]] == keys[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def standardise_rows(rows):
    n = len(rows)
    cols = []
    for c in range(len(rows[0])):
        col = [r[c] for r in rows]
        s = sd_(col)
        if s > 0:
            m = mean_(col)
            cols.append([(x - m) / s for x in col])
    return [[cols[k][i] for k in range(len(cols))] for i in range(n)]


def solve_gj(A, b):
    n = len(A)
    M = [list(map(float, A[i])) + [float(b[i])] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def ridge_plain(Z, s):
    n, p = len(Z), len(Z[0])
    sm = mean_(s)
    sc = [x - sm for x in s]
    A = [[sum(Z[i][a] * Z[i][b] for i in range(n)) + (RIDGE if a == b else 0.0) for b in range(p)] for a in range(p)]
    b = [sum(Z[i][a] * sc[i] for i in range(n)) for a in range(p)]
    beta = solve_gj(A, b)
    return [sum(Z[i][a] * beta[a] for a in range(p)) for i in range(n)]


def pc1_plain(Z):
    n, p = len(Z), len(Z[0])
    C = [[sum(Z[i][a] * Z[i][b] for i in range(n)) / (n - 1) for b in range(p)] for a in range(p)]
    v = [1.0 + 0.1 * k for k in range(p)]
    nv = math.sqrt(sum(x * x for x in v))
    v = [x / nv for x in v]
    for _ in range(20000):
        w = [sum(C[a][b] * v[b] for b in range(p)) for a in range(p)]
        nw = math.sqrt(sum(x * x for x in w))
        w = [x / nw for x in w]
        if max(abs(a - b) for a, b in zip(w, v)) < 1e-15:
            v = w
            break
        v = w
    return [sum(Z[i][a] * v[a] for a in range(p)) for i in range(n)]


def blocks_plain(ref_id, design_id, units, adoption, ystar, cov, negate=False):
    """Blocks (ascending position lists, in block order) and fixed positions of a reference, from the amendment text.  negate flips the sign of a PC1 index
    (the amendment leaves the sign of an eigenvector open; it decides which end of the index receives the larger blocks when n_free is not a multiple of K)."""
    n = len(units)
    rows = [cov.get((u, ystar)) for u in units]
    free = [i for i, r in enumerate(rows) if r is not None and all(v is not None for v in r)]
    fixed = [i for i in range(n) if i not in set(free)]
    kind, K = REFS[ref_id]
    if kind == "region":
        groups = {r: [] for r in REGION_ORDER}
        for i in free:
            groups[REGION[units[i]]].append(i)
        return [groups[r] for r in REGION_ORDER if groups[r]], fixed
    nf = len(free)
    Z = standardise_rows([rows[i] for i in free])
    Keff = max(1, min(K, nf // MIN_BLOCK))
    if kind in ("timing", "noise"):
        keys = [math.inf if adoption[i] is None else adoption[i] for i in free]
        s = avg_ranks_(keys)
        if kind == "noise":
            rng = random.Random(f"SELREF-V1|NOISE-X|{design_id}")
            Zuse = standardise_rows([[rng.gauss(0.0, 1.0) for _ in range(4)] for _ in range(nf)])
        else:
            Zuse = Z
        idx = pc1_plain(Zuse) if max(s) == min(s) else ridge_plain(Zuse, s)
    else:
        idx = pc1_plain(Z)
    if negate:
        idx = [-x for x in idx]
    names = [units[i] for i in free]
    order = sorted(range(nf), key=lambda j: (round(idx[j], 9), names[j]))
    base, extra = divmod(nf, Keff)
    blocks, pos = [], 0
    for k in range(Keff):
        size = base + (1 if k < extra else 0)
        blocks.append(sorted(free[j] for j in order[pos:pos + size]))
        pos += size
    return blocks, fixed


def perms_plain(blocks, n, n_draws, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(n_draws):
        perm = list(range(n))
        for block in blocks:
            sh = list(block)
            rng.shuffle(sh)
            for i in range(len(block)):
                perm[block[i]] = sh[i]
        out.append(perm)
    return out


def partition(blocks):
    return sorted(tuple(b) for b in blocks)


# ---------------------------------------------------------------------------------------------
# D1-V1 / D2-V1 family statistics in plain loops (extended with N, mu, tau, Z, p)
# ---------------------------------------------------------------------------------------------
def plain_design(sids, fam, code, att, placebo):
    n_all = len(placebo[0])
    A = [i for i in range(len(sids)) if code[i] == "SUPPORTED"]
    for i in list(A):
        vals = [v for v in placebo[i] if not math.isnan(v)]
        if len(vals) < 2 or not (sd_(vals) > 0):
            A.remove(i)

    def common(idx):
        return [b for b in range(n_all) if all(not math.isnan(placebo[i][b]) for i in idx)]

    C = common(A)
    while A and len(C) < 900 and len({fam[i] for i in A}) >= 5:
        count = {i: sum(1 for v in placebo[i] if not math.isnan(v)) for i in A}
        fewest = min(count.values())
        drop = max((i for i in A if count[i] == fewest), key=lambda i: sids[i])
        A.remove(drop)
        C = common(A)
    families = sorted({fam[i] for i in A})
    if len(families) < 5 or len(C) < 900:
        return None
    n = len(C)
    sd = {i: sd_([placebo[i][b] for b in C]) for i in A}
    if any(not (v > 0) for v in sd.values()):
        return None
    rank = math.ceil(0.95 * n) - 1
    nstar = [0] * n
    fam_out = {}
    N = 0
    for f in families:
        mem = [i for i in A if fam[i] == f]
        absz = [abs(att[i] / sd[i]) for i in mem]
        M = max(absz)
        top = mem[absz.index(M)]
        Ms = [max(abs(placebo[i][b] / sd[i]) for i in mem) for b in C]
        q = sorted(Ms)[rank]
        for j in range(n):
            nstar[j] += int(Ms[j] > q)
        ties = [sids[i] for i, a in zip(mem, absz) if a >= M * (1 - 1e-9)]
        fam_out[f] = dict(M=M, q=q, beyond=bool(M > q), r=sum(1 for v in Ms if v > q) / n, n_series=len(mem), top_series=sids[top], top_z=att[top] / sd[top], ties=ties)
        N += int(M > q)
    mu = sum(nstar) / n
    tau = math.sqrt(sum((v - mu) ** 2 for v in nstar) / (n - 1))
    if not tau > 0:
        return None
    series = {}
    series_beyond = 0
    for i in A:
        row = [placebo[i][b] for b in C]
        cut = sorted(abs(v) for v in row)[rank]
        beyond_own = abs(att[i]) > cut
        series_beyond += int(beyond_own)
        series[sids[i]] = dict(fam=fam[i], z=att[i] / sd[i], zstar=[v / sd[i] for v in row], beyond_own=beyond_own, exp_own=sum(1 for v in row if abs(v) > cut) / n)
    return dict(n=n, families=fam_out, series=series, N=N, mu=mu, tau=tau, Z=(N - mu) / tau, p=(1 + sum(1 for v in nstar if v >= N)) / (n + 1),
                P_ge={k: sum(1 for v in nstar if v >= k) / n for k in (1, 2, 3)}, series_beyond=series_beyond, n_series=len(A))


def e1_plain(y, fam, seed, B=B_BOOT):
    fams = sorted(set(fam))
    F = len(fams)
    members = {f: [i for i, x in enumerate(fam) if x == f] for f in fams}
    n = len(y)
    theta = sum(y) / n
    se = math.sqrt(F / (F - 1) * sum(sum(y[i] - theta for i in members[f]) ** 2 for f in fams)) / n
    tiny = 1e-9 * max(max(abs(v) for v in y), 1e-300)
    t = theta / se if se > tiny else float("nan")
    draw = np.random.default_rng(seed).integers(0, F, size=(B, F))
    S_ = [sum(y[i] for i in members[f]) for f in fams]
    m_ = [len(members[f]) for f in fams]
    ts = []
    for row in draw:
        nb = sum(m_[k] for k in row)
        th = sum(S_[k] for k in row) / nb
        ss = sum((S_[k] - m_[k] * th) ** 2 for k in row)
        seb = math.sqrt(F / (F - 1) * ss) / nb
        ts.append((th - theta) / seb if seb > tiny else 0.0)
    a = [abs(v) for v in ts]
    c = float(np.percentile(a, 95))
    p = (1 + sum(1 for v in a if v >= abs(t))) / (B + 1) if math.isfinite(t) else None
    return dict(theta=theta, se=se, t=t, p=p, c95=c, lo=theta - c * se, hi=theta + c * se, n=n, F=F)


def null_width_plain(blocks, R=N_REF, seed=SEED_NULL):
    if not blocks:
        return None
    z = [v for b in blocks for v in b["z"]]
    if len(z) < 2:
        return None
    obs = sd_(z)
    U = np.random.default_rng(seed).random((R, len(blocks)))
    sds = []
    for r in range(R):
        vals = []
        for j, b in enumerate(blocks):
            idx = min(int(U[r, j] * b["n"]), b["n"] - 1)
            vals += [row[idx] for row in b["zstar"]]
        sds.append(sd_(vals))
    lo, hi = np.percentile(sds, [2.5, 97.5])
    return dict(n_series=len(z), n_designs=len(blocks), observed_mean_z=mean_(z), observed_sd_z=obs, reference_mean_sd=mean_(sds), reference_p2_5=float(lo), reference_p97_5=float(hi),
                share_reference_sd_ge_observed=sum(1 for v in sds if v >= obs) / R, ratio_observed_to_reference_mean=obs / mean_(sds))


def estimate_plain(cells):
    m = len(cells)
    if m == 0:
        return dict(m=0, n_beyond=0, pi_NC=None, rbar=None, delta_D2=None, expected_beyond=None, n_designs=0, F=0)
    b = [int(c["B"]) for c in cells]
    r = [c["r"] for c in cells]
    return dict(m=m, n_beyond=sum(b), pi_NC=sum(b) / m, rbar=sum(r) / m, delta_D2=sum(bi - ri for bi, ri in zip(b, r)) / m, expected_beyond=sum(r),
                n_designs=len({c["design_id"] for c in cells}), F=len({c["policy_family"] for c in cells}))


# ---- spread: closed form (amendment 3.2) in plain loops --------------------------------------
def design_spread(z, zstar):
    k = len(z)
    n = len(zstar[0])
    S_db = [sum(zstar[j][b] for j in range(k)) for b in range(n)]
    mu = sum(S_db) / n
    nu = sum((s - mu) ** 2 for s in S_db) / n
    a = sum(sum(v * v for v in zstar[j]) / n for j in range(k))
    return dict(n=k, o1=sum(z), o2=sum(v * v for v in z), a=a, mu=mu, nu=nu)


def kappa_closed(stats):
    n = sum(s["n"] for s in stats)
    o1, o2 = sum(s["o1"] for s in stats), sum(s["o2"] for s in stats)
    a, mu, nu = sum(s["a"] for s in stats), sum(s["mu"] for s in stats), sum(s["nu"] for s in stats)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    v_obs = (o2 - o1 * o1 / n) / (n - 1)
    v_ref = (a - (nu + mu * mu) / n) / (n - 1)
    return math.sqrt(v_obs / v_ref), v_obs, v_ref


def spread_plain(blocks, fam_of, B=B_BOOT, seed=SEED_SPREAD):
    nw = null_width_plain(blocks)
    stats = [design_spread(b["z"], b["zstar"]) for b in blocks]
    fams = [fam_of[b["design_id"]] for b in blocks]
    k_cf, v_obs, v_ref = kappa_closed(stats)
    names = sorted(set(fams))
    F = len(names)
    draw = np.random.default_rng(seed).integers(0, F, size=(B, F))
    ks = []
    for row in draw:
        chosen = [s for f in row for s, g in zip(stats, fams) if g == names[f]]
        ks.append(kappa_closed(chosen)[0])
    fin = [k for k in ks if math.isfinite(k)]
    cst = []
    mean_u = []
    for b in blocks:
        m = [mean_(r) for r in b["zstar"]]
        cst.append(design_spread([zi - mi for zi, mi in zip(b["z"], m)], [[v - m[j] for v in row] for j, row in enumerate(b["zstar"])]))
        mean_u += [zi - mi for zi, mi in zip(b["z"], m)]
    kc, vo_c, vr_c = kappa_closed(cst)
    return dict(nw=nw, kappa_closed_form=k_cf, V_obs=v_obs, V_ref=v_ref, interval95=[float(x) for x in np.percentile(fin, [2.5, 97.5])], replicates=len(ks), defined=len(fin), blocks=F,
                centred=dict(kappa_closed_form=kc, V_obs=vo_c, V_ref=vr_c, mean_u=mean_(mean_u)))


# ---- Q1 ---------------------------------------------------------------------------------------
def bootstrap_q1_plain(bn, bo, blocks, B=B_BOOT, seed=SEED_Q1):
    names = sorted(set(blocks))
    F = len(names)
    draw = np.random.default_rng(seed).integers(0, F, size=(B, F))
    rate, rate_old, change = [], [], []
    for row in draw:
        idx = [i for f in row for i in range(len(blocks)) if blocks[i] == names[f]]
        kn = sum(1 for i in idx if bn[i])
        ko = sum(1 for i in idx if bo[i])
        rate.append(kn / len(idx))
        rate_old.append(ko / len(idx))
        change.append((ko - kn) / len(idx))
    pc = lambda v: [float(x) for x in np.percentile(v, [2.5, 97.5])]
    return dict(rate=pc(rate), excess=pc([r - 0.05 for r in rate]), rate_old=pc(rate_old), change=pc(change), blocks=F)


def level_label(iv):
    return "OVER-REJECTION" if iv[0] > 0 else "CONSERVATISM" if iv[1] < 0 else "CALIBRATION"


def spread_label(iv):
    return "WIDER" if iv[0] > 1 else "NARROWER" if iv[1] < 1 else "NOT SEPARATED FROM 1"


def change_label(iv):
    return "REDUCED" if iv[0] > 0 else "INCREASED" if iv[1] < 0 else "NOT SEPARATED"


def verdict_plain(level, change, low):
    if level is None or change is None:
        return "NOT COMPUTABLE"
    if level == "CONSERVATISM":
        v = "OVER-CORRECTED"
    elif change == "INCREASED":
        v = "WORSE: THE RESTRICTED REFERENCE IS NARROWER THAN THE DATA"
    elif change == "REDUCED" and level == "CALIBRATION":
        v = "THE SELECTION-AWARE REFERENCE CALIBRATES THE LINES"
    elif change == "REDUCED":
        v = "PARTLY CALIBRATED"
    elif level == "CALIBRATION":
        v = "CALIBRATED, BUT NOT SEPARATED FROM THE UNIFORM REFERENCE"
    else:
        v = "NO CHANGE: SELECTION ON THESE COVARIATES DOES NOT EXPLAIN THE EXCESS"
    return v + (" (LOW SUPPORT)" if low else "")


# ---------------------------------------------------------------------------------------------
# coding and inventory (as the D2 checker reads them)
# ---------------------------------------------------------------------------------------------
def coding():
    c1 = {(r["policy_group"], r["outcome_family"]): r["code"] for r in load_csv(D2DIR / "d2_codes_coder1_v1.csv")}
    c2 = {(r["policy_group"], r["outcome_family"]): r["code"] for r in load_csv(D2DIR / "d2_codes_coder2_v1.csv")}
    third = {(r["policy_group"], r["outcome_family"]): r["third_code"] for r in load_csv(D2DIR / "d2_codes_third_v1.csv")}
    pairs = {}
    for k in c1:
        final = c1[k] if c1[k] == c2[k] else third[k]
        pairs[k] = dict(final=final, strict=(c1[k] == c2[k] == "no_channel"))
    return pairs


def inventory():
    rows = load_csv(INVENTORY)
    group = {}
    for r in rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design":
            group.setdefault(r["unit_id"], set()).add(r["policy_group"])
    group = {k: (next(iter(v)) or None) for k, v in group.items()}
    fam_of_series = {r["outcome_key"]: r["family"] for r in rows if r["row_type"] == "variable" and r["layer"] == "connector_catalog" and r["data_state"] in ("on_disk_parsed", "on_disk_exports")}
    by_group = {}
    reg_fams = {}
    for r in rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design" and r["role"] in ("primary", "secondary"):
            fam = r["family"] or fam_of_series.get(r["outcome_key"])
            if fam:
                by_group.setdefault(r["policy_group"], set()).add(fam)
                reg_fams.setdefault(r["unit_id"], set()).add(fam)
    return group, by_group, reg_fams


def design_folders():
    live = load_json(LIVE)["designs"]
    return {d["design_id"]: (CS.RUN_OVERRIDE.get(d["design_id"]) or str(Path(d["source"]["path"]).parent)) for d in live
            if d["status"] == "estimated" and d["source_group"] in ("registry_terminal", "autopilot_estimate")}


# ---------------------------------------------------------------------------------------------
# recomputation of one reference
# ---------------------------------------------------------------------------------------------
def load_run(v):
    if v == "U":
        folder = SWEEP
        run = load_json(SWEEP / "SWEEP_RUN.json")
    else:
        folder = OUT / "refs" / v
        run = load_json(folder / "REF_RUN.json")
    return folder, {d["design_id"]: d for d in run["designs"] if d["status"] == "OK"}


def recompute_reference(v, ctx):
    """Everything of sections 3 and 4 for reference v from the stored arrays (plain loops).  Returns a dict shaped like the results file's entry."""
    folder, run = load_run(v)
    live, gate, group_of, reg_by_group, reg_fams, pairs = ctx["live"], ctx["gate"], ctx["group_of"], ctx["reg_by_group"], ctx["reg_fams"], ctx["pairs"]
    plain, pf_of = {}, {}
    for d in live:
        did = d["design_id"]
        if d["status"] != "estimated" or d["source_group"] in ARCHIVE_GROUPS or did not in gate or gate[did]["status"] != "PASS" or gate[did].get("adapter") == GATE_PASS_FULL_YEAR or did not in run:
            continue
        z = np.load(folder / Path(run[did]["file"]).name, allow_pickle=False)
        out = plain_design([str(x) for x in z["series_ids"]], [str(x) for x in z["family"]], [str(x) for x in z["code"]], z["att"].tolist(), z["placebo"].tolist())
        if out is not None:
            plain[did] = out
            pf_of[did] = d.get("policy_family") or did
    cells = []
    for did in sorted(plain):
        g = group_of.get(did)
        for f in sorted(plain[did]["families"]):
            st = plain[did]["families"][f]
            p = pairs.get((g, f)) if g is not None else None
            if p is None:
                code = "GROUP_NOT_CODED"
            elif p["final"] == "no_channel":
                code = "CODING_ERROR" if f in reg_by_group.get(g, set()) else "NC"
            else:
                code = "D2_PAIR_NOT_NC"
            cells.append(dict(design_id=did, policy_family=pf_of[did], policy_group=g, family=f, code=code, B=int(st["beyond"]), r=st["r"], M=st["M"], q=st["q"],
                              strict=bool(p and p["strict"] and code == "NC")))
    nc = [c for c in cells if c["code"] == "NC"]
    strict = [c for c in cells if c["strict"]]
    other = [c for c in cells if c["code"] == "D2_PAIR_NOT_NC"]
    out = dict(cells=cells, nc=nc, plain=plain)
    d2 = dict(estimate=estimate_plain(nc), strict_estimate=estimate_plain(strict), other_cells=estimate_plain(other), all_cells=estimate_plain(cells))
    m, F = d2["estimate"]["m"], d2["estimate"]["F"]
    if m >= FLOOR_M and F >= FLOOR_F:
        d2["E1"] = e1_plain([c["B"] - c["r"] for c in nc], [c["policy_family"] for c in nc], SEED_E1_D2)
    crossing = [c for c in cells if c["B"] == 1]
    d2["nc_share_of_crossings"] = (sum(1 for c in crossing if c["code"] == "NC") / len(crossing)) if crossing else None
    d2["cell_codes"] = dict(Counter(c["code"] for c in cells))
    d2["eval_designs"] = len(plain)
    d2["designs_with_nc_cell"] = len({c["design_id"] for c in nc})

    def blocks_for(sel):
        res = []
        for did in sorted(plain):
            fams = {f for (d_, f) in sel if d_ == did}
            names = sorted(s for s, x in plain[did]["series"].items() if x["fam"] in fams)
            if names:
                res.append(dict(design_id=did, z=[plain[did]["series"][s]["z"] for s in names], zstar=[plain[did]["series"][s]["zstar"] for s in names], n=plain[did]["n"],
                                beyond=[plain[did]["series"][s]["beyond_own"] for s in names], exp=[plain[did]["series"][s]["exp_own"] for s in names]))
        return res

    blocks = blocks_for({(c["design_id"], c["family"]) for c in nc})
    blocks_s = blocks_for({(c["design_id"], c["family"]) for c in strict})
    ns = sum(len(b["z"]) for b in blocks)
    nb = sum(sum(1 for x in b["beyond"] if x) for b in blocks)
    d2["series_level_nc"] = dict(series=ns, series_beyond_own_line=nb, expected_beyond=sum(sum(b["exp"]) for b in blocks))
    out["d2"] = d2
    out["spread"] = dict(primary_set=spread_plain(blocks, pf_of), strict_set=spread_plain(blocks_s, pf_of) if blocks_s else None)
    # D1
    rows = [dict(design_id=did, policy_family=pf_of[did], N=plain[did]["N"], mu=plain[did]["mu"], tau=plain[did]["tau"], Z=plain[did]["Z"], p=plain[did]["p"], n_draws=plain[did]["n"],
                 P_ge=plain[did]["P_ge"], series_beyond=plain[did]["series_beyond"], n_series=plain[did]["n_series"]) for did in sorted(plain)]
    d1 = dict(n_designs_eval=len(rows), theta=mean_([r["Z"] for r in rows]) if rows else None, rows=rows,
              families_beyond_line=sum(r["N"] for r in rows), families_beyond_expected=sum(r["mu"] for r in rows), series_beyond_line=sum(r["series_beyond"] for r in rows),
              series_total=sum(r["n_series"] for r in rows), designs_p_le_05=sum(1 for r in rows if r["p"] <= 0.05),
              designs_N_ge={k: sum(1 for r in rows if r["N"] >= k) for k in (1, 2, 3)}, designs_N_ge_expected={k: sum(r["P_ge"][k] for r in rows) for k in (1, 2, 3)})
    if len(rows) >= 8 and len({r["policy_family"] for r in rows}) >= 5:
        d1["E1"] = e1_plain([r["Z"] for r in rows], [r["policy_family"] for r in rows], SEED_E1_D1)
    out["d1"] = d1
    out["pf_of"] = pf_of
    return out


def compare_reference(v, res, mine):
    """Compare the results-file entry `res` with the recomputation `mine`."""
    d2g, d2m = res["D2"], mine["d2"]
    for blk in ("estimate", "strict_estimate", "other_cells", "all_cells"):
        for k in ("m", "n_designs", "F", "n_beyond", "pi_NC", "rbar", "delta_D2", "expected_beyond"):
            compare(f"{v} D2 {blk}:{k}", d2g[blk][k], d2m[blk][k])
    run_should = "E1" in d2m
    compare(f"{v} D2 E1:run", d2g["E1"]["run"], run_should)
    if run_should:
        r = d2g["E1"]["result"]
        e = d2m["E1"]
        for k, kk in (("theta", "theta"), ("se", "se"), ("t", "t"), ("c95", "c95"), ("p_two_sided", "p")):
            compare(f"{v} D2 E1:{k}", r[k], e[kk])
        compare(f"{v} D2 E1:lo", r["interval95"][0], e["lo"])
        compare(f"{v} D2 E1:hi", r["interval95"][1], e["hi"])
    compare(f"{v} D2:nc_share_of_crossings", d2g["nc_share_of_crossings"], d2m["nc_share_of_crossings"])
    for code in set(d2g["cell_codes"]) | set(d2m["cell_codes"]):
        compare(f"{v} D2 cell_codes:{code}", d2g["cell_codes"].get(code, 0), d2m["cell_codes"].get(code, 0))
    compare(f"{v} D2:eval_designs", d2g["eval_designs"], d2m["eval_designs"])
    compare(f"{v} D2:designs_with_nc_cell", d2g["designs_with_nc_cell"], d2m["designs_with_nc_cell"])
    for k in ("series", "series_beyond_own_line", "expected_beyond"):
        compare(f"{v} D2 series_level_nc:{k}", d2g["series_level_nc"][k], d2m["series_level_nc"][k])
    got_cells = {(c["design_id"], c["family"]): c for c in d2g["cells"]}
    mine_cells = {(c["design_id"], c["family"]): c for c in mine["cells"]}
    if set(got_cells) != set(mine_cells):
        problems.append(f"{v}: the set of (design, family) cells differs")
    for key, c in mine_cells.items():
        g = got_cells.get(key)
        if g is None:
            continue
        lab = f"{v} cell {key[0]}|{key[1]}"
        for k in ("M", "q", "r", "beyond"):
            compare(f"{lab}:{k}", g[k], c[{"beyond": "B"}.get(k, k)])
        for k in ("code", "policy_group", "policy_family"):
            if g[k] != c[k]:
                problems.append(f"{lab}: {k} {g[k]!r} vs {c[k]!r}")
        compare(f"{lab}:strict", bool(g["strict"]), bool(c["strict"]))
    # spread
    for setname in ("primary_set", "strict_set"):
        sg, sm = res["spread"][setname], mine["spread"][setname]
        if sg is None or sm is None:
            compare(f"{v} spread {setname}:presence", sg is None, sm is None)
            continue
        nw = sm["nw"]
        pg = sg["point_d2_null_width"]
        for k, kk in (("n_series", "n_series"), ("n_designs", "n_designs"), ("observed_mean_z", "observed_mean_z"), ("observed_sd_z", "observed_sd_z"), ("reference_mean_sd", "reference_mean_sd"),
                      ("reference_p2.5", "reference_p2_5"), ("reference_p97.5", "reference_p97_5"), ("share_reference_sd_ge_observed", "share_reference_sd_ge_observed"),
                      ("ratio_observed_to_reference_mean", "ratio_observed_to_reference_mean")):
            compare(f"{v} spread {setname}:{k}", pg[k], nw[kk])
        compare(f"{v} spread {setname}:kappa_v", sg["kappa_v"], nw["ratio_observed_to_reference_mean"])
        for k, kk in (("kappa_closed_form", "kappa_closed_form"), ("V_obs", "V_obs"), ("V_ref", "V_ref")):
            compare(f"{v} spread {setname}:{k}", sg[k], sm[kk])
        compare(f"{v} spread {setname}:interval95", sg["interval95"], sm["interval95"])
        compare(f"{v} spread {setname}:defined", sg["defined"], sm["defined"])
        for k in ("kappa_closed_form", "V_obs", "V_ref", "mean_u"):
            compare(f"{v} spread {setname} centred:{k}", sg["centred"][k], sm["centred"][k])
    # D1
    d1g, d1m = res["D1"], mine["d1"]
    compare(f"{v} D1:n_designs_eval", d1g["n_designs_eval"], d1m["n_designs_eval"])
    compare(f"{v} D1:theta", d1g["theta"], d1m["theta"])
    got_rows = {r["design_id"]: r for r in d1g["designs"]}
    if sorted(got_rows) != sorted(r["design_id"] for r in d1m["rows"]):
        problems.append(f"{v} D1: EVAL designs differ")
    for r in d1m["rows"]:
        g = got_rows.get(r["design_id"])
        if g is None:
            continue
        for k in ("N", "mu", "tau", "Z", "p", "n_draws", "n_series"):
            compare(f"{v} D1 {r['design_id']}:{k}", g[k], r[k])
    compare(f"{v} D1:E1 run", d1g["E1"]["run"], "E1" in d1m)
    if "E1" in d1m:
        e, rr = d1m["E1"], d1g["E1"]["result"]
        for k, kk in (("theta", "theta"), ("se", "se"), ("t", "t"), ("c95", "c95"), ("p_two_sided", "p")):
            compare(f"{v} D1 E1:{k}", rr[k], e[kk])
        compare(f"{v} D1 E1:interval95", rr["interval95"], [e["lo"], e["hi"]])
    sc = d1g["series_calibration"]
    for k, kk in (("families_beyond_line", "families_beyond_line"), ("families_beyond_expected", "families_beyond_expected"), ("series_beyond_line", "series_beyond_line"), ("series_total", "series_total")):
        compare(f"{v} D1 series_calibration:{k}", sc[k], d1m[kk])
    pc = d1g["pattern_counts"]
    compare(f"{v} D1:designs_p_le_05", pc["designs_p_le_05"], d1m["designs_p_le_05"])
    for k in (1, 2, 3):
        compare(f"{v} D1:designs_N_ge {k}", pc["designs_N_ge"][str(k)], d1m["designs_N_ge"][k])
        compare(f"{v} D1:designs_N_ge_expected {k}", pc["designs_N_ge_expected"][str(k)], d1m["designs_N_ge_expected"][k])
    compare(f"{v} D1:n families beyond", len(d1g["families_beyond"]), d1m["families_beyond_line"])


# ---------------------------------------------------------------------------------------------
# Q1 and the paired change, labels, verdict
# ---------------------------------------------------------------------------------------------
def recompute_q1(v, ex1_rows33, q1_payload):
    by = {r["design_id"]: r for r in q1_payload["designs"] if r["status"] == "OK"}
    matched = []
    for r in ex1_rows33:
        x = by.get(r["design_id"])
        if x is not None:
            matched.append(dict(r, b_new=x["q1"]["att"], cut_new=x["q1"]["cutoff"], support=bool(x["q1"]["support_pass"])))
    kept = [m for m in matched if m["support"]]
    n = len(kept)
    bn = [abs(m["b_new"]) > m["cut_new"] for m in kept]
    bo = [abs(m["b"]) > m["cut"] for m in kept]
    out = dict(n_recomputable=len(matched), n_kept=n, k_new=sum(bn), k_old_same_designs=sum(bo), F=len({m["policy_family"] for m in kept}),
               designs_beyond_new=[m["design_id"] for m, x in zip(kept, bn) if x], designs_beyond_old=[m["design_id"] for m, x in zip(kept, bo) if x])
    if n:
        out["rate_new"], out["rate_old"], out["excess_new"], out["expected_nominal"] = sum(bn) / n, sum(bo) / n, sum(bn) / n - 0.05, 0.05 * n
        out["bootstrap"] = bootstrap_q1_plain(bn, bo, [m["policy_family"] for m in kept])
        out["kept"] = kept
    return out


def compare_q1(v, res, mine):
    g = res["Q1"]
    if g is None or not mine:
        compare(f"{v} Q1:presence", g is None, not mine)
        return
    for k in ("n_recomputable", "n_kept", "k_new", "k_old_same_designs", "F"):
        compare(f"{v} Q1:{k}", g[k], mine[k])
    for k in ("rate_new", "rate_old", "excess_new", "expected_nominal"):
        compare(f"{v} Q1:{k}", g[k], mine[k])
    if g["designs_beyond_new"] != mine["designs_beyond_new"] or g["designs_beyond_old"] != mine["designs_beyond_old"]:
        problems.append(f"{v} Q1: the lists of designs beyond the line differ")
    for k in ("rate", "excess", "rate_old", "change"):
        compare(f"{v} Q1 bootstrap:{k}", g["bootstrap"][k], mine["bootstrap"][k])


def paired_plain(nc_u, nc_v):
    iu = {(c["design_id"], c["family"]): c for c in nc_u}
    common = [(iu[(c["design_id"], c["family"])], c) for c in nc_v if (c["design_id"], c["family"]) in iu]
    m = len(common)
    fams = [cu["policy_family"] for cu, _ in common]
    out = dict(m_common=m, F=len(set(fams)), m_u=len(nc_u), m_v=len(nc_v), share=len(common) / len(nc_u) if nc_u else None)
    if m >= FLOOR_M and out["F"] >= FLOOR_F:
        d = [(cu["B"] - cu["r"]) - (cv["B"] - cv["r"]) for cu, cv in common]
        out["e1"] = e1_plain(d, fams, SEED_PAIR)
        out["delta_u_common"] = mean_([cu["B"] - cu["r"] for cu, _ in common])
        out["delta_v_common"] = mean_([cv["B"] - cv["r"] for _, cv in common])
    return out


def compare_paired_and_labels(res_all, mine_all, primary_got):
    u_nc = mine_all["U"]["nc"]
    labels = {}
    low_support = {}
    for v in ALL_IDS:
        mine = mine_all[v]
        lev = level_label([mine["d2"]["E1"]["lo"], mine["d2"]["E1"]["hi"]]) if "E1" in mine["d2"] else None
        sp = mine["spread"]["primary_set"]
        lab = dict(LEVEL=lev, SPREAD=spread_label(sp["interval95"]))
        if v != "U":
            pc = paired_plain(u_nc, mine["nc"])
            g = res_all[v]["paired_change_vs_U"]
            compare(f"{v} paired:m_common", g["m_common"], pc["m_common"])
            compare(f"{v} paired:run", g["run"], "e1" in pc)
            if "e1" in pc:
                for k, kk in (("theta", "theta"), ("se", "se"), ("t", "t"), ("c95", "c95"), ("p_two_sided", "p")):
                    compare(f"{v} paired:{k}", g["result"][k], pc["e1"][kk])
                compare(f"{v} paired:interval95", g["result"]["interval95"], [pc["e1"]["lo"], pc["e1"]["hi"]])
                compare(f"{v} paired:delta_u_common", g["delta_u_common"], pc["delta_u_common"])
                compare(f"{v} paired:delta_v_common", g["delta_v_common"], pc["delta_v_common"])
                lab["CHANGE"] = change_label([pc["e1"]["lo"], pc["e1"]["hi"]])
            else:
                lab["CHANGE"] = None
            share_v = len(mine["nc"]) / len(u_nc)
            lab["SUPPORT"] = "LOW" if share_v < 0.75 else "OK"
            low_support[v] = share_v < 0.75
        labels[v] = lab
        got = res_all[v]["labels"]
        for k, val in lab.items():
            if got.get(k) != val:
                problems.append(f"{v} label {k}: {got.get(k)!r} vs {val!r}")
    lab = labels["P5"]
    verdict = verdict_plain(lab.get("LEVEL"), lab.get("CHANGE"), low_support.get("P5", False))
    if primary_got["verdict"] != verdict:
        problems.append(f"primary verdict: {primary_got['verdict']!r} vs {verdict!r}")
    leak = labels["N5"].get("CHANGE") in ("REDUCED", "INCREASED")
    if bool(primary_got["leakage_warning"]) != leak:
        problems.append("leakage warning differs")
    return labels, verdict


# ---------------------------------------------------------------------------------------------
# blocks and permutations against the stored structure, and engine cell checks
# ---------------------------------------------------------------------------------------------
def design_basics(did, folder):
    setup = CS.design_setup(did, folder)
    d, dec = setup["design"], setup["dec"]
    py = dec.get("period_years")
    units = list(d["units"])
    adoption = [d["adoption"][u] for u in units]
    cal = (lambda t: py[t]) if py is not None else (lambda t: t)
    ystar = lower_median_([cal(d["operative"][u]) for u in units if d["adoption"][u] is not None])
    return setup, units, adoption, ystar


def stored_blocks(meta, units):
    pos = {u: i for i, u in enumerate(units)}
    return [sorted(pos[u] for u in b["units"]) for b in meta["reference"]["blocks"]], sorted(pos[u] for u in meta["reference"]["fixed_units"])


def check_structure(task):
    """Blocks of every swept design under one reference: the independent construction against the stored block structure, and the permutation diagnostics."""
    v, did, folder, npz_path = task
    res = dict(reference=v, design_id=did, ok=False)
    try:
        cov = read_cov()
        setup, units, adoption, ystar = design_basics(did, folder)
        z = np.load(npz_path, allow_pickle=False)
        meta = json.loads(str(z["meta_json"]))
        sblocks, sfixed = stored_blocks(meta, units)
        blocks, fixed = blocks_plain(v, did, units, adoption, ystar, cov)
        res["orientation"] = "as written"
        if v == "PC5" and blocks != sblocks:                                 # the sign of the eigenvector is open in the amendment: try the other one
            blocks_neg, _ = blocks_plain(v, did, units, adoption, ystar, cov, negate=True)
            if blocks_neg == sblocks:
                blocks, res["orientation"] = blocks_neg, "negated index"
        res["fixed_equal"] = fixed == sfixed
        res["ystar_equal"] = ystar == meta["reference"]["ystar"]
        same_order = blocks == sblocks
        res["partition_equal"] = partition(blocks) == partition(sblocks)
        res["order"] = "same" if same_order else "different"
        res["order_ok"] = same_order
        n = len(units)
        perms = perms_plain(sblocks, n, N_DRAWS, f"SELREF-V1|{v}|{did}")
        moved = swapped = same = 0
        for perm in perms:
            new = [adoption[perm[j]] for j in range(n)]
            moved += sum(1 for j in range(n) if new[j] != adoption[j])
            swapped += sum(1 for j in range(n) if adoption[j] is not None and new[j] is None)
            same += int(all(new[j] == adoption[j] for j in range(n)))
        dg = meta["permutation_diagnostics"]
        res["diag_diff"] = max(abs(moved / N_DRAWS - dg["mean_moved"]), abs(swapped / N_DRAWS - dg["mean_swapped_out"]), abs(same / N_DRAWS - dg["share_identical_to_observed"]))
        res["ok"] = bool(res["fixed_equal"] and res["ystar_equal"] and res["partition_equal"] and res["order_ok"] and res["diag_diff"] <= 1e-12)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    res["l2_audit"] = l2_loaded_audit(pinned_l2())
    return res


def bundles_for(setup, design_k, years, perms):
    from scripts.l2_pipeline.engine.design import build_design
    d0, dec0 = setup["design"], setup["dec"]
    units0 = list(d0["units"])
    full = build_design(dec0, [(u, t, 0.0, None) for u in units0 for t in years])["exposure"]
    labels0 = {}
    for u in units0:
        g = d0["adoption"][u]
        labels0[u] = (g, None if g is None else d0["operative"][u] - g, full[u], d0["whole_window_clean"][u])
    pos0 = {u: i for i, u in enumerate(units0)}
    return [[labels0[units0[perm[pos0[u]]]] for u in design_k["units"]] for perm in perms]


def series_cells_any(sid, scale_mode):
    """CS.series_cells, with a local reader for the WONDER exports whose columns are 'Year of Death' / 'State of Residence' (check_sweep_independent reads 'Year' only)."""
    try:
        return CS.series_cells(sid, scale_mode)
    except KeyError:
        from scripts.l2_pipeline.connectors.common import NAMES
        cat = load_json(ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json")["series"][sid]
        if cat.get("adapter") != "wonder":
            raise
        out = {}
        for e in cat["exports"]:
            path = ROOT / e["raw_path"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == e["sha256"]
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh, delimiter="\t"))
            head = rows[0]
            ix = {name: head.index(name) for name in head if name}
            ycol = "Year" if "Year" in ix else "Year of Death"
            scol = "State" if "State" in ix else "State of Residence"
            for r in rows[1:]:
                if len(r) < len(head) or not r[ix[scol]].strip().strip('"') or (r[0].strip().strip('"') == "Total"):
                    continue
                state = r[ix[scol]].strip().strip('"')
                if state not in NAMES:
                    continue
                raw = r[ix[cat["value_field"]]].strip()
                low = raw.lower()
                if low in ("", "suppressed", "unreliable", "not applicable", "not available", "n/a", "na", "*", "**", "--", "---", "...", "\u2026", "1-9") or low.endswith("(unreliable)"):
                    continue
                out[(NAMES[state], int(r[ix[ycol]].strip().strip('"')))] = float(raw.replace(",", ""))
        return out


def check_cell_ref(task):
    v, did, folder, sid, scale_mode, npz_path = task
    t0 = time.time()
    res = dict(reference=v, design_id=did, series=sid, ok=False)
    try:
        from scripts.l2_pipeline.engine import ri
        from scripts.l2_pipeline.engine.runner import fit_primary
        z = np.load(npz_path, allow_pickle=False)
        sids = [str(x) for x in z["series_ids"]]
        k = sids.index(sid)
        meta = json.loads(str(z["meta_json"]))
        setup = CS.design_setup(did, folder)
        years = setup["support"].declared_annual_periods(setup["card"]["outcome"])
        U0 = set(setup["design"]["units"])
        cells = series_cells_any(sid, scale_mode)
        eligible = {(u, y) for (u, y) in cells if u in U0 and y in set(years)}
        dec, keys, design = CS.pattern_design(setup, eligible, "check")
        assert str(z["code"][k]) in ("SUPPORTED", "RI_FEASIBLE_LT_90")
        res["n_cells_equal"] = int(z["n_cells"][k]) == len(design["mask"]) and int(z["n_units"][k]) == len(design["units"])
        rows = [(u, t, cells[(u, t)], None) for u, t in sorted(keys)]
        payload, design2, outcomes = fit_primary(dict(dec, variant="check"), rows, B=2)
        att = payload["result"]["overall"]["att"]
        res["att_diff"] = CS.scaled_diff(att, float(z["att"][k]))
        family = ri.pre_family(design)
        units0 = list(setup["design"]["units"])
        sblocks, _ = stored_blocks(meta, units0)
        perms = perms_plain(sblocks, len(units0), N_DRAWS, f"SELREF-V1|{v}|{did}")
        mine = []
        for labels in bundles_for(setup, design, years, perms):
            r = ri.one_reference(labels, (dec, design, outcomes, family))
            mine.append(float("nan") if r.get("infeasible") else float(r["overall"]))
        stored = z["placebo"][k]
        res["nan_pattern_equal"] = bool(all((math.isnan(a) == math.isnan(b)) for a, b in zip(mine, stored)))
        res["draw_max_diff"] = max([CS.scaled_diff(a, float(b)) for a, b in zip(mine, stored) if not math.isnan(a) and not math.isnan(b)] or [0.0])
        fin = [x for x in mine if not math.isnan(x)]
        sdv = sd_(fin)
        cutoff = sorted(abs(x) for x in fin)[math.ceil(0.95 * len(fin)) - 1]
        # p = (1 + #{|draw| >= |ATT|}) / (n + 1); a draw that reproduces the observed labelling ties with the ATT up to rounding, so the stored p must lie between the count
        # with strict and with non-strict comparison at 1e-9 relative
        n_strict = sum(1 for x in fin if abs(x) > abs(att) * (1 + 1e-9))
        n_loose = sum(1 for x in fin if abs(x) >= abs(att) * (1 - 1e-9))
        p_lo, p_hi = (1 + n_strict) / (len(fin) + 1), (1 + n_loose) / (len(fin) + 1)
        res["sd_cutoff_diff"] = max(CS.scaled_diff(sdv, float(z["sd"][k])), CS.scaled_diff(cutoff, float(z["cutoff"][k])))
        res["p_between_tie_bounds"] = bool(p_lo - 1e-12 <= float(z["p2"][k]) <= p_hi + 1e-12)
        res["p_tie_width"] = p_hi - p_lo
        res["stats_diff"] = res["sd_cutoff_diff"]
        res["code_equal"] = (("SUPPORTED" if 10 * len(fin) >= 9 * N_DRAWS else "RI_FEASIBLE_LT_90") == str(z["code"][k])) and len(fin) == int(z["n_feasible"][k])
        res["ok"] = bool(res["n_cells_equal"] and res["att_diff"] <= 1e-9 and res["nan_pattern_equal"] and res["draw_max_diff"] <= 1e-9 and res["stats_diff"] <= 1e-9
                         and res["p_between_tie_bounds"] and res["code_equal"])
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    res["seconds"] = round(time.time() - t0, 1)
    res["l2_audit"] = l2_loaded_audit(pinned_l2())
    return res


def entry_setup_with_keys(folder):
    """Declaration, design and calendar mask keys of the primary entry (the sweep checker's entry_setup, returning the keys as well)."""
    from scripts.l2_pipeline import registered, variants
    from scripts.l2_pipeline.contract_card import registered_execution_card
    from scripts.l2_pipeline.hygiene import inventory_names
    from scripts.l2_pipeline.engine.design import build_design
    folder = Path(folder)
    card = registered_execution_card(folder, load_json(folder / "FREEZE_FINAL.json"))
    support_name, panel_name = inventory_names(folder)
    sup = {r["variant"]: r for r in load_json(folder / support_name)["variants"]}
    treatment = load_json(folder / "TREATMENT.json")
    cards = variants.registered_cards({k: v for k, v in card.items() if k != "_card_path"})
    meta = folder / "panels" / "primary" / "metadata.csv"
    dec = registered.numerical_declaration(cards["primary"], sup["primary"], treatment, "primary")
    required = registered.registered_panel_keys(dec, meta)
    design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(required)])
    if design["cells"] != sup["primary"]["cell_inventory"]:
        dec = copy.deepcopy(dec)
        dec["case"].pop("min_post_periods", None)
        design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(required)])
        assert design["cells"] == sup["primary"]["cell_inventory"], "the primary is not reproduced"
    return dec, design, required


def check_primary(task):
    v, did, folder, q1_entry = task
    t0 = time.time()
    res = dict(reference=v, design_id=did, ok=False)
    try:
        from scripts.l2_pipeline.engine import ri
        from scripts.l2_pipeline.engine.runner import fit_primary
        folder = Path(folder)
        dec, design, required = entry_setup_with_keys(folder)
        from scripts.l2_pipeline.engine import panels as engine_panels
        panel_path, meta_path = folder / "panels" / "primary" / "panel.csv", folder / "panels" / "primary" / "metadata.csv"
        sha = lambda q: hashlib.sha256(Path(q).read_bytes()).hexdigest()
        binding = engine_panels.PanelBinding(panel_path, panel_path, meta_path, sha(panel_path), sha(panel_path), sha(meta_path), frozenset(required))
        prows, _ = engine_panels.load_bound_panel(binding)                  # the engine's own reader of the stored panel (blank cells, adapter columns)
        rows = [(u, y, val, None) for (u, y, val, _) in prows]
        payload, design2, outcomes = fit_primary(dict(dec, variant="check"), rows, B=2)
        att = payload["result"]["overall"]["att"]
        res["att_diff"] = CS.scaled_diff(att, q1_entry["q1"]["att"])
        units = list(design["units"])
        adoption = [design["adoption"][u] for u in units]
        cov = read_cov()
        py = dec.get("period_years")
        cal = (lambda t: py[t]) if py is not None else (lambda t: t)
        ystar = lower_median_([cal(design["operative"][u]) for u in units if design["adoption"][u] is not None])
        blocks, fixed = blocks_plain(v, did, units, adoption, ystar, cov)
        sb = [sorted(units.index(u) for u in b["units"]) for b in q1_entry["reference"]["blocks"]]
        res["partition_equal"] = blocks == sb
        perms = perms_plain(sb, len(units), N_DRAWS, f"SELREF-V1|{v}|{did}")
        labels = [(design["adoption"][u], None if design["adoption"][u] is None else design["operative"][u] - design["adoption"][u], design["exposure"][u], design["whole_window_clean"][u])
                  for u in units]
        family = ri.pre_family(design)
        mine = []
        for perm in perms:
            r = ri.one_reference([labels[perm[p]] for p in range(len(units))], (dec, design, outcomes, family))
            if not r.get("infeasible"):
                mine.append(float(r["overall"]))
        stored = q1_entry["placebo_finite"]
        res["n_equal"] = len(mine) == len(stored) == q1_entry["q1"]["n_feasible"]
        res["draw_max_diff"] = max([CS.scaled_diff(a, b) for a, b in zip(mine, stored)] or [0.0]) if res["n_equal"] else None
        cutoff = sorted(abs(x) for x in mine)[math.ceil(0.95 * len(mine)) - 1]
        res["cutoff_diff"] = CS.scaled_diff(cutoff, q1_entry["q1"]["cutoff"])
        res["ok"] = bool(res["att_diff"] <= 1e-9 and res["partition_equal"] and res["n_equal"] and res["draw_max_diff"] is not None and res["draw_max_diff"] <= 1e-9 and res["cutoff_diff"] <= 1e-9)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    res["seconds"] = round(time.time() - t0, 1)
    res["l2_audit"] = l2_loaded_audit(pinned_l2())
    return res


# ---------------------------------------------------------------------------------------------
def cross_check_55():
    """Amendment 5.5: for the designs whose primary's own series sits in the series store (SWEEP-V1 recorded `consistency_with_gate.checked`), the placebo draws of that
    series in the design file of reference v equal the Q1-stage draws of the primary entry under v (finite draws, in order), and the ATTs agree."""
    run_u = load_run("U")[1]
    series_of = {}
    for did, d in sorted(run_u.items()):
        z = np.load(SWEEP / Path(d["file"]).name, allow_pickle=False)
        cons = json.loads(str(z["meta_json"])).get("consistency_with_gate", {})
        if cons.get("checked"):
            series_of[did] = cons["series"]
    out = dict(designs=sorted(series_of), comparisons=0, max_scaled_draw_diff=0.0, max_scaled_att_diff=0.0, failures=[])
    for v in ALL_IDS:
        folder, run = load_run(v)
        q1 = load_json(OUT / "q1" / f"Q1_REFS_{v}.json")
        by = {r["design_id"]: r for r in q1["designs"] if r["status"] == "OK"}
        for did, sid in sorted(series_of.items()):
            z = np.load(folder / Path(run[did]["file"]).name, allow_pickle=False)
            k = [str(s) for s in z["series_ids"]].index(sid)
            if str(z["code"][k]) not in ("SUPPORTED", "RI_FEASIBLE_LT_90"):
                out["failures"].append(f"{v} {did}: series {sid} has code {z['code'][k]}")
                continue
            mine = [float(x) for x in z["placebo"][k] if not math.isnan(float(x))]
            theirs = by[did]["placebo_finite"]
            out["comparisons"] += 1
            if len(mine) != len(theirs):
                out["failures"].append(f"{v} {did}: {len(mine)} finite draws in the design file, {len(theirs)} in the Q1 stage")
                continue
            dd = max([CS.scaled_diff(a, b) for a, b in zip(mine, theirs)] or [0.0])
            da = CS.scaled_diff(float(z["att"][k]), by[did]["q1"]["att"])
            out["max_scaled_draw_diff"] = max(out["max_scaled_draw_diff"], dd)
            out["max_scaled_att_diff"] = max(out["max_scaled_att_diff"], da)
            if dd > 1e-9 or da > 1e-9:
                out["failures"].append(f"{v} {did}: draws differ by {dd:.2e}, ATT by {da:.2e}")
    return out


def planted_changes_are_rejected(results_path, ctx, mine_all):
    """A copy of the results with three planted changes must produce at least three mismatches in the comparison (amendment 5.4)."""
    global problems
    res = load_json(results_path)
    refs = res["question"]["SELREF"]["references"]
    refs["P5"]["D2"]["estimate"]["n_beyond"] += 1
    refs["P3"]["spread"]["primary_set"]["interval95"][1] += 1e-6
    refs["PC5"]["D1"]["theta"] *= 1.0001
    saved, saved_worst = problems, dict(worst)
    problems = []
    for v in ("P5", "P3", "PC5"):
        compare_reference(v, refs[v], mine_all[v])
    found = len(problems)
    problems = saved
    worst.clear()
    worst.update(saved_worst)                                                # the planted differences must not enter the report of the real ones
    return found


PIN_RECORDS = ["PREREG_AMEND_20261015_SWEEP_HASH.txt", "PREREG_AMEND_20261015_SWEEP_HASH_S1.txt", "PREREG_AMEND_20261015_SWEEP_HASH_B1.txt"]


def pinned_l2():
    """{repo-relative path: sha256} of the L2 modules pinned by the SWEEP-V1 hash records (read by regular expression)."""
    out = {}
    for name in PIN_RECORDS:
        text = (ROOT / "docs/plans/l3-meta" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"^\s+(scripts/l2_pipeline/\S+\.py)\s+sha256\s+([0-9a-f]{64})", text, re.M):
            out[m.group(1)] = m.group(2)
    return out


def build_overlay(dest):
    """A package tree at `dest` in which scripts/l2_pipeline holds the pinned byte copies of the pinned modules and symlinks to the working-tree files for the rest;
    the other top-level entries of the repository are symlinked so that paths computed from a module's own location still find the data."""
    pins = pinned_l2()
    copies = SWEEP / "l2_files"
    dest = Path(dest)
    src = ROOT / "scripts" / "l2_pipeline"
    n_copy = n_link = 0
    for dirpath, dirnames, filenames in os.walk(src):
        rel_dir = Path(dirpath).relative_to(ROOT)
        (dest / rel_dir).mkdir(parents=True, exist_ok=True)
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            rel = str(rel_dir / fn)
            target = dest / rel
            if rel in pins:
                data = (copies / rel).read_bytes()
                if hashlib.sha256(data).hexdigest() != pins[rel]:
                    raise RuntimeError(f"the pinned copy of {rel} differs from the hash record")
                target.write_bytes(data)
                n_copy += 1
            else:
                os.symlink(Path(dirpath) / fn, target)
                n_link += 1
    missing = [r for r in pins if not (dest / r).exists()]
    if missing:
        raise RuntimeError(f"pinned modules absent from the working tree layout: {missing[:3]}")
    for entry in sorted(os.listdir(ROOT)):
        if entry == "scripts" or entry.startswith(".") or (dest / entry).exists():       # hidden entries (credentials, environments, caches) are never linked
            continue
        os.symlink(ROOT / entry, dest / entry)
    for entry in sorted(os.listdir(ROOT / "scripts")):
        if entry != "l2_pipeline" and not (dest / "scripts" / entry).exists():
            os.symlink(ROOT / "scripts" / entry, dest / "scripts" / entry)
    return dict(pinned_copied=n_copy, linked=n_link)


def l2_loaded_audit(pins):
    """Loaded L2 modules of this process: pinned ones must hash to the record; the others are listed with the hash of the file they were read from."""
    out = {}
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and f.endswith(".py") and "/scripts/l2_pipeline/" in f:
            rel = "scripts/l2_pipeline/" + f.split("/scripts/l2_pipeline/", 1)[1]
            out[rel] = hashlib.sha256(Path(f).read_bytes()).hexdigest()
    pinned_bad = sorted(r for r, h in out.items() if r in pins and pins[r] != h)
    unpinned = {r: h for r, h in sorted(out.items()) if r not in pins}
    return dict(loaded=len(out), pinned_loaded=sum(1 for r in out if r in pins), pinned_mismatch=pinned_bad, unpinned=unpinned)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=str(OUT / "RESULTS_SELREF_V1.json"))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--skip-engine", action="store_true", help="skip the engine recomputation of cells and primary entries (done in an earlier run)")
    ap.add_argument("--working-tree-l2", action="store_true", help="import the L2 modules from the working tree instead of the SWEEP-V1 pinned copies")
    ap.add_argument("--out", default=None, help="write the report here (a new file)")
    args = ap.parse_args(argv)
    t_start = time.time()
    overlay_info = None
    if not args.skip_engine and not args.working_tree_l2:
        overlay_dir = tempfile.mkdtemp(prefix="selref_l2_overlay_")
        overlay_info = dict(path=overlay_dir, **build_overlay(overlay_dir))
        os.environ["SELREF_L2_OVERLAY"] = overlay_dir
        sys.path.insert(0, overlay_dir)
        print(f"[overlay] pinned L2 modules copied: {overlay_info['pinned_copied']}, others linked: {overlay_info['linked']}", file=sys.stderr, flush=True)
    resfile = load_json(args.results)
    sel = resfile["question"]["SELREF"]
    res_all = sel["references"]
    live = load_json(LIVE)["designs"]
    gate = {d["design_id"]: d for d in load_json(SWEEP / "GATE_REPORT.json")["designs"]}
    group_of, reg_by_group, reg_fams = inventory()
    ctx = dict(live=live, gate=gate, group_of=group_of, reg_by_group=reg_by_group, reg_fams=reg_fams, pairs=coding())

    # (c) statistics from the stored arrays
    mine_all = {}
    for v in ALL_IDS:
        mine_all[v] = recompute_reference(v, ctx)
        compare_reference(v, res_all[v], mine_all[v])
        print(f"[{v}] recomputed: D2 {mine_all[v]['d2']['estimate']['n_beyond']}/{mine_all[v]['d2']['estimate']['m']}, kappa {mine_all[v]['spread']['primary_set']['nw']['ratio_observed_to_reference_mean']:.4f}",
              file=sys.stderr, flush=True)
    # Q1
    ex1 = load_json(EX1)
    ids33 = [d["design_id"] for d in gate.values() if d["status"] == "PASS"]
    rows33 = []
    for r in ex1["designs"]:
        if (r["design_id"] in ids33 and r.get("status") == "estimated" and isinstance(r.get("b"), (int, float)) and isinstance(r.get("noise_sd"), (int, float)) and r["noise_sd"] > 0
                and isinstance(r.get("noise_cutoff"), (int, float)) and r["noise_cutoff"] > 0 and r.get("support_pass") is not False):
            rows33.append(dict(design_id=r["design_id"], policy_family=r["policy_family"], b=float(r["b"]), cut=float(r["noise_cutoff"])))
    q1_mine = {}
    for v in ALL_IDS:
        q1_payload = load_json(OUT / "q1" / f"Q1_REFS_{v}.json")
        q1_mine[v] = recompute_q1(v, rows33, q1_payload)
        compare_q1(v, res_all[v], q1_mine[v])
        for r in q1_payload["designs"]:
            e = next(x for x in ex1["designs"] if x["design_id"] == r["design_id"])
            compare(f"{v} Q1 att vs Q1-V3 {r['design_id']}", r["q1"]["att"], float(e["b"]))
            if v == "U":
                compare(f"U Q1 cutoff vs Q1-V3 {r['design_id']}", r["q1"]["cutoff"], float(e["noise_cutoff"]))
    labels, verdict = compare_paired_and_labels(res_all, mine_all, sel["primary"])
    # residual widening (3.7)
    for v in ALL_IDS:
        rw = res_all[v].get("residual_widening")
        sp = mine_all[v]["spread"]["primary_set"]
        should = v != "U" and sp["interval95"][0] > 1.0 and q1_mine[v].get("kept") is not None
        compare(f"{v} residual widening present", rw is not None, should)
        if rw is not None and should:
            kappa = sp["nw"]["ratio_observed_to_reference_mean"]
            kept = q1_mine[v]["kept"]
            compare(f"{v} residual:q1_k", rw["q1_k"], sum(1 for m in kept if abs(m["b_new"]) > kappa * m["cut_new"]))
            by = Counter()
            for c in mine_all[v]["cells"]:
                if c["M"] > kappa * c["q"]:
                    by[c["design_id"]] += 1
            rows = mine_all[v]["d1"]["rows"]
            compare(f"{v} residual:d1_T_D", rw["d1_T_D"], sum(by.get(r["design_id"], 0) for r in rows))
            compare(f"{v} residual:d1_theta", rw["d1_theta"], mean_([(by.get(r["design_id"], 0) - r["mu"]) / r["tau"] for r in rows]))
    # identities under U (5.6)
    d2v1, d1v1 = load_json(ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"), load_json(ROOT / "data/l3_meta/d1_v1/RESULTS_D1_V1.json")
    q2 = d2v1["question"]["D2"]
    u = mine_all["U"]
    compare("U vs D2-V1 m", u["d2"]["estimate"]["m"], q2["estimate_nc_cells"]["m"])
    compare("U vs D2-V1 n_beyond", u["d2"]["estimate"]["n_beyond"], q2["estimate_nc_cells"]["n_beyond"])
    compare("U vs D2-V1 delta", u["d2"]["estimate"]["delta_D2"], q2["estimate_nc_cells"]["delta_D2"])
    compare("U vs D2-V1 kappa", u["spread"]["primary_set"]["nw"]["ratio_observed_to_reference_mean"], q2["empirical_null_width_nc_series"]["ratio_observed_to_reference_mean"])
    compare("U vs D1-V1 theta", u["d1"]["theta"], d1v1["question"]["D1"]["theta_D1_plain_mean_Z"])
    compare("U vs D1-V1 T_D", u["d1"]["families_beyond_line"], d1v1["question"]["D1"]["series_calibration"]["families_beyond_line"])

    # (a) and (b): blocks, permutations, engine cells
    structure, cell_results, primary_results = [], [], []
    if not args.skip_engine:
        folders = design_folders()
        scale = {d["design_id"]: d.get("scale_mode") or "rate" for d in gate.values()}
        tasks_struct, tasks_cells, tasks_primary = [], [], []
        for v in REFS:
            folder, run = load_run(v)
            for did, d in sorted(run.items()):
                tasks_struct.append((v, did, str(ROOT / folders[did]), str(folder / Path(d["file"]).name)))
        # sampled cells
        p5_folder, p5_run = load_run("P5")
        pairs_p5 = []
        for did, d in sorted(p5_run.items()):
            z = np.load(p5_folder / Path(d["file"]).name, allow_pickle=False)
            pairs_p5 += [(did, str(s)) for s, c in zip(z["series_ids"], z["code"]) if str(c) == "SUPPORTED"]
        rng = random.Random(20261016)
        chosen = set(rng.sample(pairs_p5, max(1, round(len(pairs_p5) / 10))))
        for did in sorted({p[0] for p in pairs_p5}):
            if not any(c[0] == did for c in chosen):
                chosen.add(rng.choice([p for p in pairs_p5 if p[0] == did]))
        for did, sid in sorted(chosen):
            tasks_cells.append(("P5", did, str(ROOT / folders[did]), sid, scale[did], str(p5_folder / Path(p5_run[did]["file"]).name)))
        for v in ("P3", "P8", "PC5", "REG", "N5"):
            folder, run = load_run(v)
            rng_v = random.Random(f"SELREF-V1-CHECK|{v}")
            for did, d in sorted(run.items()):
                z = np.load(folder / Path(d["file"]).name, allow_pickle=False)
                cand = [str(s) for s, c in zip(z["series_ids"], z["code"]) if str(c) == "SUPPORTED"]
                tasks_cells.append((v, did, str(ROOT / folders[did]), rng_v.choice(cand), scale[did], str(folder / Path(d["file"]).name)))
        for v in ("P5", "N5"):
            q1_payload = load_json(OUT / "q1" / f"Q1_REFS_{v}.json")
            for r in q1_payload["designs"]:
                if r["status"] == "OK":
                    tasks_primary.append((v, r["design_id"], str(ROOT / folders[r["design_id"]]), r))
        ctx_mp = mp.get_context("spawn")
        with ctx_mp.Pool(processes=args.workers, maxtasksperchild=20) as pool:
            structure = pool.map(check_structure, tasks_struct, chunksize=1)
            print(f"[structure] {len(structure)} done ({time.time() - t_start:.0f}s)", file=sys.stderr, flush=True)
            cell_results = pool.map(check_cell_ref, tasks_cells, chunksize=1)
            print(f"[cells] {len(cell_results)} done ({time.time() - t_start:.0f}s)", file=sys.stderr, flush=True)
            primary_results = pool.map(check_primary, tasks_primary, chunksize=1)
            print(f"[primary entries] {len(primary_results)} done ({time.time() - t_start:.0f}s)", file=sys.stderr, flush=True)
        audits = [r["l2_audit"] for r in structure + cell_results + primary_results if "l2_audit" in r]
        l2_audit_total = dict(tasks_audited=len(audits), pinned_modules_loaded_max=max([a["pinned_loaded"] for a in audits] or [0]),
                              pinned_mismatches=sorted({m for a in audits for m in a["pinned_mismatch"]}),
                              unpinned_modules_loaded={r: h for a in audits for r, h in a["unpinned"].items()}, l2_source="working tree" if args.working_tree_l2 else "SWEEP-V1 pinned copies (overlay)")
        for m in l2_audit_total["pinned_mismatches"]:
            problems.append(f"a loaded pinned L2 module differs from the SWEEP-V1 record: {m}")
        for r in structure:
            if not r["ok"]:
                problems.append(f"structure {r['reference']} {r['design_id']}: {r}")
        for r in cell_results + primary_results:
            if not r["ok"]:
                problems.append(f"engine check {r['reference']} {r['design_id']} {r.get('series', 'primary')}: {r}")

    # 5.5 cross-check of the sweep files against the Q1 stage
    cross = cross_check_55()
    for f_ in cross["failures"]:
        problems.append("cross-check 5.5: " + f_)
    if len(cross["designs"]) != 14:
        problems.append(f"cross-check 5.5: {len(cross['designs'])} designs have their primary's own series in the store (14 expected)")
    l2_audit_total = locals().get("l2_audit_total")
    # (d) planted changes
    planted = planted_changes_are_rejected(args.results, ctx, mine_all)
    if planted < 3:
        problems.append(f"planted changes: only {planted} mismatches found (3 expected)")

    report = dict(results=str(args.results), references=ALL_IDS, labels=labels, verdict=verdict,
                  structure=dict(n=len(structure), failures=[r for r in structure if not r["ok"]], pc5_orientation=dict(Counter(r.get("orientation") for r in structure if r["reference"] == "PC5")),
                                 other_order=dict(Counter(r["order"] for r in structure if r["reference"] != "PC5"))),
                  cells=dict(n=len(cell_results), failures=[r for r in cell_results if not r["ok"]], max_att_diff=max([r.get("att_diff", 0.0) for r in cell_results] or [0.0]),
                             max_draw_diff=max([r.get("draw_max_diff", 0.0) for r in cell_results] or [0.0]), by_reference=dict(Counter(r["reference"] for r in cell_results)),
                             cells_with_a_p_tie=[(r["reference"], r["design_id"], r["series"]) for r in cell_results if r.get("p_tie_width", 0) > 0]),
                  primary_entries=dict(n=len(primary_results), failures=[r for r in primary_results if not r["ok"]], max_att_diff=max([r.get("att_diff", 0.0) for r in primary_results] or [0.0]),
                                       max_draw_diff=max([r.get("draw_max_diff") or 0.0 for r in primary_results] or [0.0])),
                  l2_modules=l2_audit_total, overlay=overlay_info, cross_check_5_5=cross, planted_changes_found=planted, largest_scaled_differences={k: float(f"{x:.2e}") for k, x in sorted(worst.items())}, problems=problems,
                  seconds=round(time.time() - t_start, 1))
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        with open(args.out, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(report, cells_detail=cell_results, primary_detail=primary_results, structure_detail=structure), indent=1, default=str) + "\n")
    if problems:
        print("MISMATCH", len(problems), file=sys.stderr)
        return 1
    print("OK: the independent recomputation agrees with the results file", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
