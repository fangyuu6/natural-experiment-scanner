#!/usr/bin/env python3
"""L3 meta, EXPLORATORY versions D4-V1 ... D10-V1 (rule id EX-1): the outcome-dimension questions D4 to D10.

  D4  policy, outcome or pair        (marginal-mean decomposition of |z| against random-timing replicate matrices)
  D5  spread on a shared outcome     (between-design variance of the ATT minus the noise variance, per outcome series)
  D6  detectability by outcome       (minimum detectable effect per series from the sweep)
  D7  replication by outcome and data type (grades of the 42 re-estimated papers)
  D8  source choice                  (twin series: is the difference of their standardised ATTs beyond the random-timing line?)
  D9  definition breaks              (effects of cells whose base-to-evaluation span crosses a break year)
  D10 what laws invite and what we can measure (text-family mentions against series on disk and results)

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_QUESTIONS.md (entries) and PREREG_AMEND_20261015_D4_D10_V1.md (build rules), hash record
PREREG_AMEND_20261015_D4_D10_HASH.txt, hashed before any D4-D10 statistic was computed.  Every raw result is printed beside the CAL-V1 calibrated reading
(random-timing lines or SDs widened by kappa-hat; D7 and D10 carry no noise line: NOT_APPLICABLE).  Everything is exploratory: no "significant" label, no
multiplicity adjustment, no licensed sentence.

Reads L2 results only through the sweep files (the 28 D1-V1 design files) and, for D9, the group-time cell structure (g, t, base_period, post) of the 28 stored
RESULT.json files; nothing under scripts/l2_pipeline or data/l2_pipeline is opened for writing.  No network.  Only output: data/l3_meta/d4_d10_v1/ (each file written once).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_d4_d10.py [--dry-run] [--out-dir DIR] [--sweep-dir DIR] [--verify-records]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_d1 as D1  # noqa: E402  (D1-V1 statistics and design-file reader)
import build_d3 as D3  # noqa: E402  (hash record of the D questions)
import build_n1 as N1  # noqa: E402  (E1 engine)
import build_sweep as B  # noqa: E402  (sweep hash record)
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID = "EX-1"
QUESTIONS = ["D4", "D5", "D6", "D7", "D8", "D9", "D10"]
Q_NUM = {q: int(q[1:]) for q in QUESTIONS}
R_REP = 9999                       # placebo replicates (D4, D5) and permutations (D7, D9)
B_BOOT = 9999
R_LOO = 999                        # replicates used by the leave-one-policy-family-out passes of D4
SEED_BASE = 20261011
CHUNK = 1000
MDE_Z = 0.8416212335729143         # the 0.8 quantile of the standard normal (build file 3.2)
PRUNE_MIN = 5                      # D4 pruning (build file 1.2)
IDENTICAL_TOL = 1e-9               # D8: sd of the standardised difference at or below which twins are IDENTICAL
TOL = 1e-9
OUT_DIR = ROOT / "data/l3_meta/d4_d10_v1"
OUT_NAME = "RESULTS_D4_D10_V1.json"
AMEND_Q = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_QUESTIONS.md"
AMEND_B = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_HASH.txt"
AMEND_S1 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_S1.md"
HASH_REC_S1 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_HASH_S1.txt"
B_FE = 999                         # supplement S1.2.3
D1_JSON = ROOT / "data/l3_meta/d1_v1/RESULTS_D1_V1.json"
D2_JSON = ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"
CAL_JSON = ROOT / "data/l3_meta/cal_v1/RESULTS_CAL_V1.json"
STORE_RATE = "series_store_v1.npz"
STORE_COUNT = "series_store_v1_count.npz"
CODE_FILES = ["scripts/l3_meta/build_d4_d10.py", "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_n1.py", "scripts/l3_meta/build_d3.py",
              "scripts/l3_meta/build_sweep.py", "scripts/l3_meta/sweep_core.py"]

# D8 pair table (build file 5.1)
PAIRS = [
    ("A1", "A", "wonder_homicide_rate", "fbi_ucr_homicide_rate"),
    ("A2", "A variant, printed, not pooled (shares the UCR series with A1)", "wonder_homicide_deaths", "fbi_ucr_homicide_rate"),
    ("A3", "A", "fars_all_road_fatality_rate", "wonder_motor_vehicle_traffic_deaths"),
    ("B1", "B", "brfss_current_smoking_pct", "brfss_current_smoking_pct_legacy_spliced"),
    ("B2", "B", "brfss_binge_drinking_pct", "brfss_binge_drinking_pct_legacy_spliced"),
    ("B3", "B", "wonder_homicide_rate", "wonder_homicide_deaths"),
    ("B4", "B", "wonder_firearm_homicide_rate", "wonder_firearm_homicide_deaths"),
    ("B5", "B", "wonder_infant_mortality_rate", "wonder_infant_deaths"),
    ("B6", "B", "fbi_ucr_rape_legacy_rate", "fbi_ucr_rape_revised_rate"),
    ("C1", "C", "brfss_health_coverage_pct_age18_64", "cps_asec_insured_pct_all"),
    ("C2", "C", "acs1_college_enrollment_share_age18_24", "ipeds_enrollment_per_population"),
]
TIER_OF = {"A": ("A1", "A3"), "B": ("B1", "B2", "B3", "B4", "B5", "B6"), "C": ("C1", "C2")}
INFANT_RATE_BREAKS = (2003, 2007)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def agree(x, y, tol=TOL):
    return abs(x - y) <= tol * max(1.0, abs(x), abs(y))


def seed_of(q, r=None):
    return [SEED_BASE, 40 + Q_NUM[q]] + ([r] if r is not None else [])


# ---------------------------------------------------------------------------------------------
# hash record
# ---------------------------------------------------------------------------------------------
def entry_text(text, tag):
    m = re.search(r"^## " + re.escape(tag) + r"\. .*?(?=^## |\Z)", text, re.M | re.S)
    return m.group(0) if m else None


def entry_sha256(text, tag):
    t = entry_text(text, tag)
    return hashlib.sha256(t.encode("utf-8")).hexdigest() if t is not None else None


def parse_record(path=HASH_REC):
    """The main record merged with its supplements (..._HASH_S*.txt)."""
    rec = _parse_one(path)
    for extra in sorted(Path(path).parent.glob(Path(path).stem + "_[SB]*.txt")):
        e = _parse_one(extra)
        for k, v in e["listed"].items():
            if k in rec["listed"] and rec["listed"][k] != v:
                raise RuntimeError(f"{extra.name} contradicts the record for {k}")
            rec["listed"][k] = v
        rec["files"].update(e["files"])
        rec.setdefault("supplements", []).append(extra.name)
    return rec


def _parse_one(path):
    text = Path(path).read_text(encoding="utf-8")
    files = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^file:\s*(\S+)\s*$", line)
        if m and i + 1 < len(lines):
            s = re.match(r"^sha256:\s*([0-9a-f]{64})\s*$", lines[i + 1])
            if s:
                files[m.group(1)] = s.group(1)
    entries = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(D(?:[4-9]|10))\s+([0-9a-f]{64})\s*$", text, re.M)}
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    t = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    return {"files": files, "entries": entries, "listed": listed, "t_amend_utc": t.group(1) if t else None}


def verify_records(rec=None, q_path=AMEND_Q, b_path=AMEND_B, hash_path=HASH_REC, s1_path=AMEND_S1):
    """Problems between the hash record and the files on disk (empty list = consistent)."""
    rec = rec or parse_record(hash_path)
    problems = []
    for p in (q_path, b_path, s1_path):
        want, got = rec["files"].get(S.rel(p)), S.sha256_file(p)
        if want is None:
            problems.append(f"{S.rel(p)} not in the hash record")
        elif want != got:
            problems.append(f"{S.rel(p)} differs from the hash record")
    q_text = Path(q_path).read_text(encoding="utf-8")
    for q in QUESTIONS:
        want, got = rec["entries"].get(q), entry_sha256(q_text, q)
        if want is None or got is None:
            problems.append(f"entry {q} missing in record or file")
        elif want != got:
            problems.append(f"entry {q} differs from the hash record")
    for path, want in rec["listed"].items():
        if path.startswith("scripts/l2_pipeline/") and path.endswith(".py"):
            continue
        p = ROOT / path
        if not p.exists() or S.sha256_file(p) != want:
            problems.append(f"{path} differs from the hash record")
    return problems


# ---------------------------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------------------------
def one_hot(ids, n):
    M = np.zeros((len(ids), n))
    M[np.arange(len(ids)), ids] = 1.0
    return M


def pct(values, lo=2.5, hi=97.5):
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    q = np.percentile(a, [lo, hi])
    return [float(q[0]), float(q[1])]


def rank_line(absvals, n=None):
    """The ceil(.95 n)-th smallest value (1-based): the engine rule for the 95% line."""
    a = np.sort(np.asarray(absvals, float))
    n = a.size if n is None else n
    return float(a[math.ceil(0.95 * n) - 1])


def sign(x):
    return int(x > 0) - int(x < 0)


def ge(a, b):
    """a >= b up to floating-point noise (1e-12 relative): the comparison of every permutation or replicate count."""
    return np.asarray(a) >= b - 1e-12 * max(1.0, abs(b))


# ---------------------------------------------------------------------------------------------
# sample: the 28 D1-V1 EVAL designs
# ---------------------------------------------------------------------------------------------
class Design:
    pass


def design_from_arrays(did, policy_family, scale_mode, years, units, fam_all, sids_all, code_all, att_all, placebo_all, sd_stored, cutoff_stored, used):
    """Build the Design record from the sweep arrays and the D1 `series_used` list (build file 0.2)."""
    d = Design()
    d.id, d.policy_family, d.scale_mode, d.years, d.units = did, policy_family, scale_mode, list(years), list(units)
    pos = {s: i for i, s in enumerate(sids_all)}
    used = sorted(used)
    idx = [pos[s] for s in used]
    placebo_all = np.asarray(placebo_all, float)
    common = np.isfinite(placebo_all[idx]).all(axis=0)
    d.C = np.where(common)[0]
    d.n = int(d.C.size)
    d.sids = used
    d.fam = [fam_all[i] for i in idx]
    d.att = np.asarray(att_all, float)[idx]
    d.P = placebo_all[np.ix_(idx, d.C)]
    d.sd = d.P.std(axis=1, ddof=1)
    d.z = d.att / d.sd
    rank = math.ceil(0.95 * d.n) - 1
    d.c = np.sort(np.abs(d.P), axis=1)[:, rank]
    d.rho = np.abs(d.att) / d.c
    d.Zabs = np.abs(d.P) / d.sd[:, None]
    d.col = {s: j for j, s in enumerate(d.sids)}
    d.all_sids = list(sids_all)
    d.all_codes = list(code_all)
    d.all_fam = list(fam_all)
    d.all_sd = np.asarray(sd_stored, float)
    d.all_cutoff = np.asarray(cutoff_stored, float)
    return d


def load_sample(sweep_dir, d1j):
    """The 28 EVAL designs of D1-V1, re-derived from their sweep files and checked against RESULTS_D1_V1.json (build file 0.2)."""
    sweep_dir = Path(sweep_dir)
    sweep_run = S.read_json(sweep_dir / "SWEEP_RUN.json")
    swept = {x["design_id"]: x for x in sweep_run["designs"] if x["status"] == "OK"}
    d1_eval = {r["design_id"]: r for r in d1j["designs"] if r.get("code") == "EVAL"}
    designs, problems = OrderedDict(), []
    for did in sorted(d1_eval):
        row = d1_eval[did]
        x = swept.get(did)
        if x is None:
            problems.append(f"{did}: not in SWEEP_RUN.json")
            continue
        path = sweep_dir / Path(x["file"]).name
        if S.sha256_file(path) != x["sha256"]:
            problems.append(f"sweep file of {did} differs from SWEEP_RUN.json")
            continue
        f = D1.load_design_file(path)
        st = D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], ())
        if st["code"] != "EVAL":
            problems.append(f"{did}: D1 code {st['code']} on recomputation")
            continue
        for k in ("N", "mu", "tau", "Z", "p"):
            if not agree(float(st[k]), float(row[k])):
                problems.append(f"{did}: {k} differs from D1-V1")
        if st["n_draws"] != row["n_draws"] or list(st["series_used"]) != list(row["series_used"]):
            problems.append(f"{did}: n_draws or series_used differ from D1-V1")
        z = f["z"]
        d = design_from_arrays(did, row["policy_family"], f["meta"]["scale_mode"], f["meta"]["years"], f["meta"]["units"], f["fam"], f["sids"], f["code"], f["att"], f["placebo"],
                               np.asarray(z["sd"], float), np.asarray(z["cutoff"], float), st["series_used"])
        if d.n != st["n_draws"]:
            problems.append(f"{did}: common draw set differs from design_statistics")
        d.sweep_file_sha256 = x["sha256"]
        designs[did] = d
    return designs, problems


# ---------------------------------------------------------------------------------------------
# replicate indices (build file 0.6)
# ---------------------------------------------------------------------------------------------
def replicate_index(designs, seed, R=R_REP):
    ids = sorted(designs)
    U = np.random.default_rng(seed).random((R, len(ids)))
    n = np.array([designs[d].n for d in ids], dtype=float)
    idx = np.minimum(np.floor(U * n[None, :]).astype(int), n.astype(int)[None, :] - 1)
    return ids, idx


# ---------------------------------------------------------------------------------------------
# D4: marginal-mean decomposition
# ---------------------------------------------------------------------------------------------
def prune(pairs, min_col=PRUNE_MIN, min_row=PRUNE_MIN):
    """Build file 1.2: remove columns used by fewer than min_col rows and rows holding fewer than min_row columns until nothing changes."""
    pairs = set(pairs)
    while True:
        cc = Counter(c for _, c in pairs)
        drop_c = {c for c, n in cc.items() if n < min_col}
        if drop_c:
            pairs = {(r, c) for r, c in pairs if c not in drop_c}
        rc = Counter(r for r, _ in pairs)
        drop_r = {r for r, n in rc.items() if n < min_row}
        if drop_r:
            pairs = {(r, c) for r, c in pairs if r not in drop_r}
        if not drop_c and not drop_r:
            break
    return sorted(pairs)


def dec_stats(X, row, col, n_row, n_col):
    """Build file 1.3 on the rows of X (R x N): A_row, A_col, A_res, V_tot and A_x (arrays of length R)."""
    X = np.atleast_2d(np.asarray(X, float))
    m = X.mean(axis=1, keepdims=True)
    rc = np.bincount(row, minlength=n_row).astype(float)
    cc = np.bincount(col, minlength=n_col).astype(float)
    r = (X @ one_hot(row, n_row)) / rc[None, :]
    s = (X @ one_hot(col, n_col)) / cc[None, :]
    ri, sk = r[:, row], s[:, col]
    A_row = np.mean((ri - m) ** 2, axis=1)
    A_col = np.mean((sk - m) ** 2, axis=1)
    A_res = np.mean((X - ri - sk + m) ** 2, axis=1)
    V_tot = np.mean((X - m) ** 2, axis=1)
    return {"A_row": A_row, "A_col": A_col, "A_res": A_res, "V_tot": V_tot, "A_x": V_tot - A_row - A_col - A_res}


PARTS = ("A_row", "A_col", "A_res")
PART_NAME = {"A_row": "POLICY", "A_col": "OUTCOME", "A_res": "PAIR"}


def d4_excess(obs, null_mean, kappa):
    return {k: float(obs[k] - kappa ** 2 * null_mean[k]) for k in obs}


def d4_pvalues(obs, null, kappa, R):
    """One-sided randomization p of the three parts; null[k] is the array of replicate values."""
    return {k: float((1 + np.sum(ge(kappa ** 2 * null[k], obs[k]))) / (R + 1)) for k in PARTS}


def d4_label(p, e):
    sel = [k for k in PARTS if p[k] <= 0.05]
    if not sel:
        return "NONE BEYOND THE NULL"
    sel.sort(key=lambda k: -e[k])
    return " > ".join(PART_NAME[k] for k in sel)


def d4_shares(e):
    pos = {k: max(e[k], 0.0) for k in PARTS}
    tot = sum(pos.values())
    return {k: (pos[k] / tot if tot > 0 else None) for k in PARTS}


def build_cellvec(designs, view):
    """{(design id, column key): (observed size, replicate vector over the design's common draws)} for the series view or the family view."""
    out = OrderedDict()
    for did in sorted(designs):
        d = designs[did]
        if view == "series":
            for j, s in enumerate(d.sids):
                out[(did, s)] = (float(abs(d.z[j])), d.Zabs[j])
        else:
            fams = sorted(set(d.fam))
            for f in fams:
                rows = [j for j, x in enumerate(d.fam) if x == f]
                out[(did, f)] = (float(np.mean(np.abs(d.z[rows]))), d.Zabs[rows].mean(axis=0))
    return out


def d4_run(designs, cellvec, ids, idx, kappas, R=R_REP, loo=True, chunk=CHUNK):
    """Decomposition, excess, p, label at each kappa; leave-one-policy-family-out.  `kappas`: OrderedDict label -> value."""
    pairs = prune(list(cellvec))
    des = sorted({r for r, _ in pairs})
    cols = sorted({c for _, c in pairs})
    n_designs, n_cols = len(des), len(cols)
    families = {designs[r].policy_family for r in des}
    base = {"cells_before_pruning": len(cellvec), "designs": n_designs, "columns": n_cols, "policy_families": len(families), "cells": len(pairs)}
    if n_designs < 8 or len(families) < 5 or n_cols < 5:
        return dict(base, code="BELOW_FLOOR", reason="engine floor: 8 designs, 5 policy families, 5 columns")
    row = np.array([des.index(r) for r, _ in pairs], dtype=int)
    col = np.array([cols.index(c) for _, c in pairs], dtype=int)
    xobs = np.array([cellvec[p][0] for p in pairs])
    jpos = {did: j for j, did in enumerate(ids)}
    cell_j = np.array([jpos[r] for r, _ in pairs], dtype=int)
    reps = [cellvec[p][1] for p in pairs]
    obs = {k: float(v[0]) for k, v in dec_stats(xobs, row, col, n_designs, n_cols).items()}
    null = {k: np.empty(R) for k in obs}
    X_first = None
    for r0 in range(0, R, chunk):
        r1 = min(R, r0 + chunk)
        X = np.empty((r1 - r0, len(pairs)))
        for c in range(len(pairs)):
            X[:, c] = reps[c][idx[r0:r1, cell_j[c]]]
        st = dec_stats(X, row, col, n_designs, n_cols)
        for k in obs:
            null[k][r0:r1] = st[k]
        if r0 == 0:
            X_first = X[:min(R_LOO, r1 - r0)].copy()
    null_mean = {k: float(null[k].mean()) for k in obs}
    result = OrderedDict(base)
    result["code"] = "EVAL"
    result["observed"] = obs
    result["null_mean"] = null_mean
    result["null_percentiles_kappa1"] = {k: pct(null[k]) for k in obs}
    result["R"] = R
    levels = OrderedDict()
    for lab, kap in kappas.items():
        e = d4_excess(obs, null_mean, kap)
        p = d4_pvalues(obs, null, kap, R)
        levels[lab] = OrderedDict([("kappa", float(kap)), ("excess", e), ("p_one_sided", p), ("share_of_positive_excess", d4_shares(e)), ("label", d4_label(p, e)),
                                   ("null_percentiles_kappa_squared_scaled", {k: pct(kap ** 2 * null[k]) for k in PARTS})])
    result["levels"] = levels
    if loo:
        result["leave_one_policy_family_out"] = d4_loo(designs, pairs, xobs, X_first, kappas["calibrated"], R_LOO_used(X_first), levels["calibrated"]["label"])
    return result


def R_LOO_used(X_first):
    return int(X_first.shape[0])


def d4_loo(designs, pairs, xobs, X_first, kappa, R, full_label):
    """Build file 1.8."""
    fams = sorted({designs[r].policy_family for r, _ in pairs})
    pos = {p: i for i, p in enumerate(pairs)}
    rows_out = []
    for f in fams:
        keep = [p for p in pairs if designs[p[0]].policy_family != f]
        keep = prune(keep)
        if not keep:
            rows_out.append(OrderedDict([("policy_family_removed", f), ("code", "EMPTY")]))
            continue
        des = sorted({r for r, _ in keep})
        cols = sorted({c for _, c in keep})
        if len(des) < 2 or len(cols) < 2:
            rows_out.append(OrderedDict([("policy_family_removed", f), ("code", "EMPTY")]))
            continue
        row = np.array([des.index(r) for r, _ in keep], dtype=int)
        col = np.array([cols.index(c) for _, c in keep], dtype=int)
        sel = np.array([pos[p] for p in keep], dtype=int)
        obs = {k: float(v[0]) for k, v in dec_stats(xobs[sel], row, col, len(des), len(cols)).items()}
        st = dec_stats(X_first[:, sel], row, col, len(des), len(cols))
        nm = {k: float(st[k].mean()) for k in obs}
        e = d4_excess(obs, nm, kappa)
        p = d4_pvalues(obs, st, kappa, R)
        rows_out.append(OrderedDict([("policy_family_removed", f), ("code", "EVAL"), ("designs", len(des)), ("columns", len(cols)), ("cells", len(keep)),
                                     ("excess", {k: e[k] for k in PARTS}), ("p_one_sided", p), ("label", d4_label(p, e))]))
    ev = [r for r in rows_out if r["code"] == "EVAL"]
    summ = OrderedDict([("R", R), ("kappa", float(kappa)), ("blocks", len(fams)), ("blocks_evaluated", len(ev)), ("full_sample_label", full_label)])
    for k in PARTS:
        vals = [r["excess"][k] for r in ev]
        summ[f"excess_{PART_NAME[k].lower()}_min_max"] = [min(vals), max(vals)] if vals else None
    summ["blocks_changing_label"] = [r["policy_family_removed"] for r in ev if r["label"] != full_label]
    summ["n_blocks_changing_label"] = len(summ["blocks_changing_label"])
    return OrderedDict([("summary", summ), ("blocks", rows_out)])


# ---------------------------------------------------------------------------------------------
# D5: spread on a shared outcome
# ---------------------------------------------------------------------------------------------
def d5_values(a, s, kappa):
    a, s = np.asarray(a, float), np.asarray(s, float)
    m = a.size
    V = float(a.var(ddof=1))
    N = float(np.mean(s ** 2))
    t2 = V - kappa ** 2 * N
    return OrderedDict([("m", int(m)), ("a_bar", float(a.mean())), ("V", V), ("N", N), ("tau2", t2), ("tau", math.sqrt(max(t2, 0.0))), ("H", V / (kappa ** 2 * N) if N > 0 else None),
                        ("rms2", float(np.mean(a ** 2)) - kappa ** 2 * N)])


def d5_pvalue(V, Vstar, kappa):
    return float((1 + np.sum(ge(kappa ** 2 * np.asarray(Vstar), V))) / (len(Vstar) + 1))


def d5_bootstrap(a, s, fam_idx, F, rng, B_=B_BOOT):
    """Build file 2.4: policy-family block bootstrap of V and N.  Returns (V*, N*, dropped) over the defined replicates."""
    a, s = np.asarray(a, float), np.asarray(s, float)
    draw = rng.integers(0, F, size=(B_, F))
    c = np.zeros((B_, F))
    for f in range(F):
        c[:, f] = (draw == f).sum(axis=1)
    w = c[:, fam_idx]
    W = w.sum(axis=1)
    ok = W >= 2
    Wd = np.where(ok, W, 1.0)
    abar = (w * a[None, :]).sum(axis=1) / Wd
    V = (w * (a[None, :] - abar[:, None]) ** 2).sum(axis=1) / np.where(ok, W - 1, 1.0)
    Nn = (w * (s[None, :] ** 2)).sum(axis=1) / Wd
    return V[ok], Nn[ok], int((~ok).sum())


def series_key(design, sid, count_series):
    return sid + "[count]" if (design.scale_mode == "count" and sid in count_series) else sid


def d5_run(designs, ids, idx, kappas, count_series, registered, nc_cells, R=R_REP, B_=B_BOOT, seed_boot=None):
    seed_boot = seed_boot or seed_of("D5", 1)
    jpos = {did: j for j, did in enumerate(ids)}
    by_key = defaultdict(list)
    for did in sorted(designs):
        d = designs[did]
        for j, s in enumerate(d.sids):
            by_key[series_key(d, s, count_series)].append((did, j))
    rng = np.random.default_rng(seed_boot)
    table, listed = [], []
    for key in sorted(by_key):
        members = by_key[key]
        m = len(members)
        fams = sorted({designs[did].policy_family for did, _ in members})
        sid = key.split("[")[0]
        if m < 5 or len(fams) < 3:
            listed.append(OrderedDict([("key", key), ("m", m), ("F", len(fams)), ("code", "BELOW_FLOOR")]))
            continue
        a = np.array([designs[did].att[j] for did, j in members])
        s = np.array([designs[did].sd[j] for did, j in members])
        fam_idx = np.array([fams.index(designs[did].policy_family) for did, _ in members], dtype=int)
        Pm = np.empty((R, m))
        for q, (did, j) in enumerate(members):
            Pm[:, q] = designs[did].P[j][idx[:, jpos[did]]]
        Vstar = Pm.var(axis=1, ddof=1)
        Vb, Nb, dropped = d5_bootstrap(a, s, fam_idx, len(fams), rng, B_)
        # strata
        nreg = sum(1 for did, _ in members if sid in registered.get(did, ()))
        cells_nc = [nc_cells.get((did, designs[did].fam[j])) for did, j in members]
        share_nc = sum(1 for x in cells_nc if x) / m
        stratum = "REGISTERED" if nreg >= 5 else "NC-DOMINATED" if share_nc >= 0.8 else "OTHER"
        row = OrderedDict([("key", key), ("series_id", sid), ("family", designs[members[0][0]].fam[members[0][1]]), ("m", m), ("F", len(fams)), ("stratum", stratum),
                           ("n_registered", nreg), ("share_nc_cells", share_nc), ("code", "EVAL"), ("bootstrap_dropped", dropped), ("designs", [did for did, _ in members])])
        row["levels"] = OrderedDict()
        for lab, kap in kappas.items():
            vals = d5_values(a, s, kap)
            p = d5_pvalue(vals["V"], Vstar, kap)
            t2b = Vb - kap ** 2 * Nb
            row["levels"][lab] = OrderedDict(list(vals.items()) + [("kappa", float(kap)), ("p_one_sided", p), ("beyond_noise", bool(p <= 0.05 and vals["tau2"] > 0)),
                                                                   ("tau2_interval95", pct(t2b)), ("bootstrap_defined", int(t2b.size))])
        # registered subset
        reg_members = [(q, did, j) for q, (did, j) in enumerate(members) if sid in registered.get(did, ())]
        reg_fams = sorted({designs[did].policy_family for _, did, _ in reg_members})
        if len(reg_members) >= 5 and len(reg_fams) >= 3:
            qs = [q for q, _, _ in reg_members]
            sub = OrderedDict()
            for lab, kap in kappas.items():
                vals = d5_values(a[qs], s[qs], kap)
                Vs = Pm[:, qs].var(axis=1, ddof=1)
                sub[lab] = OrderedDict(list(vals.items()) + [("kappa", float(kap)), ("p_one_sided", d5_pvalue(vals["V"], Vs, kap))])
            row["registered_subset"] = OrderedDict([("designs", [did for _, did, _ in reg_members]), ("F", len(reg_fams)), ("levels", sub)])
        table.append(row)
    summary = OrderedDict()
    summary["keys_at_or_above_floor"] = len(table)
    summary["keys_listed_below_floor"] = len(listed)
    summary["engine_floor_summary"] = {"keys": 8, "met": len(table) >= 8}
    summary["expected_by_random_timing_5pct_of_keys"] = 0.05 * len(table)
    lv = OrderedDict()
    for lab in kappas:
        lv[lab] = OrderedDict([("kappa", float(kappas[lab])), ("keys_with_label", int(sum(r["levels"][lab]["beyond_noise"] for r in table))),
                               ("median_H", float(np.median([r["levels"][lab]["H"] for r in table])) if table else None),
                               ("by_stratum", {st: OrderedDict([("keys", sum(1 for r in table if r["stratum"] == st)),
                                                                ("keys_with_label", sum(1 for r in table if r["stratum"] == st and r["levels"][lab]["beyond_noise"]))])
                                               for st in ("REGISTERED", "NC-DOMINATED", "OTHER")})])
    summary["levels"] = lv
    summary["keys_with_label_at_calibrated"] = [r["key"] for r in table if r["levels"]["calibrated"]["beyond_noise"]]
    return OrderedDict([("summary", summary), ("series", table), ("below_floor", listed), ("R", R), ("B", B_)])


# ---------------------------------------------------------------------------------------------
# D6: detectability
# ---------------------------------------------------------------------------------------------
def scale_stats(val, ok, uidx, yidx):
    """Build file 3.3: (N, mean, sdw) over the ok cells of the unit x year rectangle (sdw None when undefined)."""
    V = np.asarray(val)[np.ix_(uidx, yidx)]
    O = np.asarray(ok)[np.ix_(uidx, yidx)]
    N = int(O.sum())
    if N == 0:
        return N, None, None
    mean = float(V[O].mean())
    ss, Y = 0.0, 0
    for j in range(len(yidx)):
        c = V[:, j][O[:, j]]
        if c.size:
            Y += 1
            ss += float(((c - c.mean()) ** 2).sum())
    dof = N - Y
    sdw = math.sqrt(ss / dof) if dof >= 2 else None
    if sdw is not None and not sdw > 0:
        sdw = None
    return N, mean, sdw


def mde_class(v):
    if v is None:
        return None
    return "FINE" if v <= 0.25 else "MODERATE" if v <= 0.5 else "COARSE" if v <= 1.0 else "BLIND"


def d6_run(designs, stores, count_series, kappas, swept_ids, series_family):
    """stores: (rate npz, count npz).  Cells = SUPPORTED with finite positive stored sd and cutoff."""
    rate, cnt = stores
    s_units, s_years = [str(u) for u in rate["units"]], [int(y) for y in rate["years"]]
    upos, ypos = {u: i for i, u in enumerate(s_units)}, {y: i for i, y in enumerate(s_years)}
    by_key = defaultdict(list)
    codes_by_sid = defaultdict(Counter)
    for did in sorted(designs):
        d = designs[did]
        uidx = [upos[u] for u in d.units]
        yidx = [ypos[y] for y in d.years]
        for i, sid in enumerate(d.all_sids):
            codes_by_sid[sid][d.all_codes[i]] += 1
            if d.all_codes[i] != "SUPPORTED":
                continue
            sd, cut = d.all_sd[i], d.all_cutoff[i]
            if not (np.isfinite(sd) and sd > 0 and np.isfinite(cut) and cut > 0):
                continue
            key = series_key(d, sid, count_series)
            S_ = cnt if key.endswith("[count]") else rate
            N, mean, sdw = scale_stats(S_[sid + "__val"], S_[sid + "__ok"], uidx, yidx)
            mde = float(cut + MDE_Z * sd)
            by_key[key].append(OrderedDict([("design_id", did), ("sd", float(sd)), ("cutoff", float(cut)), ("mde", mde), ("n_cells_scale", N), ("mean", mean), ("sdw", sdw),
                                            ("mde_rel", (mde / abs(mean)) if (mean is not None and mean != 0) else None), ("mde_sd", (mde / sdw) if sdw else None)]))
    table, listed = [], []
    for key in sorted(by_key):
        cells = by_key[key]
        sid = key.split("[")[0]
        if len(cells) < 3:
            listed.append(OrderedDict([("key", key), ("supported_designs", len(cells)), ("code", "BELOW_FLOOR")]))
            continue
        row = OrderedDict([("key", key), ("series_id", sid), ("family", series_family[sid]), ("supported_designs", len(cells)), ("code", "EVAL")])
        for fld in ("mde", "mde_rel", "mde_sd"):
            v = [c[fld] for c in cells if c[fld] is not None]
            row[fld] = OrderedDict([("n", len(v)), ("median", float(np.median(v)) if v else None), ("min", min(v) if v else None), ("max", max(v) if v else None)])
        row["levels"] = OrderedDict()
        for lab, kap in kappas.items():
            med = row["mde_sd"]["median"]
            row["levels"][lab] = OrderedDict([("kappa", float(kap)), ("mde_median", kap * row["mde"]["median"]),
                                              ("mde_rel_median", kap * row["mde_rel"]["median"] if row["mde_rel"]["median"] is not None else None),
                                              ("mde_sd_median", kap * med if med is not None else None), ("class", mde_class(kap * med if med is not None else None))])
        table.append(row)
    unsupported = []
    for sid in sorted(set(series_family)):
        if sid in codes_by_sid and not any(k.split("[")[0] == sid for k in by_key):
            unsupported.append(OrderedDict([("series_id", sid), ("family", series_family[sid]), ("codes", dict(codes_by_sid[sid]))]))
    fam_tab = OrderedDict()
    for fam in sorted({r["family"] for r in table} | {u["family"] for u in unsupported}):
        rows = [r for r in table if r["family"] == fam]
        entry = OrderedDict([("keys", len(rows)), ("unsupported_series", sum(1 for u in unsupported if u["family"] == fam))])
        for lab in ("raw", "calibrated"):
            entry[f"classes_{lab}"] = dict(Counter(r["levels"][lab]["class"] for r in rows if r["levels"][lab]["class"]))
        meds = [r["levels"]["calibrated"]["mde_sd_median"] for r in rows if r["levels"]["calibrated"]["mde_sd_median"] is not None]
        entry["median_calibrated_mde_sd"] = float(np.median(meds)) if meds else None
        fam_tab[fam] = entry
    classes = OrderedDict((lab, dict(Counter(r["levels"][lab]["class"] for r in table if r["levels"][lab]["class"]))) for lab in kappas)
    return OrderedDict([("supported_cells", sum(len(v) for v in by_key.values())), ("keys_at_or_above_floor", len(table)), ("keys_below_floor", listed), ("series", table),
                        ("unsupported_series", unsupported), ("by_family", fam_tab), ("classes", classes), ("swept_series", len(swept_ids)),
                        ("mde_z", MDE_Z)])


# ---------------------------------------------------------------------------------------------
# D7: replication grades
# ---------------------------------------------------------------------------------------------
def data_type(unit_of_observation):
    first = unit_of_observation.split(";")[0].strip().lower()
    if first.startswith("state"):
        return "STATE"
    if first.startswith(("county", "city", "institution")):
        return "SUB-STATE"
    if first.startswith(("individual", "household", "job application", "restaurant", "legislator")):
        return "MICRO"
    raise ValueError(f"unit of observation not classified: {unit_of_observation!r}")


def binom_cdf(x, n, p):
    """P(X <= x) for X ~ Binomial(n, p) by exact summation."""
    if x < 0:
        return 0.0
    return float(sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(0, min(x, n) + 1)))


def clopper_pearson(x, n, alpha=0.05):
    """Exact two-sided interval by bisection (build file 4.3)."""
    if n == 0:
        return None
    lo = 0.0 if x == 0 else _bisect(lambda p: 1.0 - binom_cdf(x - 1, n, p) - alpha / 2, 0.0, 1.0)
    hi = 1.0 if x == n else _bisect(lambda p: binom_cdf(x, n, p) - alpha / 2, 0.0, 1.0)
    return [lo, hi]


def _bisect(f, a, b, it=200):
    """Root of a monotone function f on [a, b] with f(a) and f(b) of opposite sign; tolerance 1e-12."""
    fa, fb = f(a), f(b)
    if fa == 0:
        return a
    if fb == 0:
        return b
    for _ in range(it):
        m = (a + b) / 2
        fm = f(m)
        if fm == 0 or (b - a) / 2 < 1e-13:
            return m
        if (fa < 0) == (fm < 0):
            a, fa = m, fm
        else:
            b, fb = m, fm
    return (a + b) / 2


def hetero_T(y, groups, n_groups):
    y = np.asarray(y, float)
    n_g = np.bincount(groups, minlength=n_groups).astype(float)
    s_g = np.bincount(groups, weights=y, minlength=n_groups)
    pbar = float(y.mean())
    if not 0 < pbar < 1:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        pg = np.where(n_g > 0, s_g / n_g, 0.0)
    return float(np.sum(n_g * (pg - pbar) ** 2) / (pbar * (1 - pbar)))


def d7_run(papers, R=R_REP):
    """papers: list of dict(unit_id, family, unit_of_observation, status)."""
    for p in papers:
        p["data_type"] = data_type(p["unit_of_observation"])
        p["comparable"] = p["status"] != "not_comparable"
        p["strict"] = p["status"] in ("matched", "confirmed")
        p["broad"] = p["strict"] or p["status"] == "directional"
    comp = sorted([p for p in papers if p["comparable"]], key=lambda p: p["unit_id"])
    out = OrderedDict([("papers", len(papers)), ("comparable", len(comp)), ("grades", dict(Counter(p["status"] for p in papers))), ("calibration", "NOT_APPLICABLE")])
    out["rates"] = OrderedDict()
    for rate in ("strict", "broad"):
        x = sum(p[rate] for p in comp)
        out["rates"][rate] = OrderedDict([("successes", int(x)), ("n", len(comp)), ("rate", x / len(comp) if comp else None), ("interval95_exact", clopper_pearson(x, len(comp)))])
    for gname, gkey, j0 in (("family", "family", 1), ("data_type", "data_type", 3)):
        names = sorted({p[gkey] for p in comp})
        gid = np.array([names.index(p[gkey]) for p in comp], dtype=int)
        block = OrderedDict()
        tab = OrderedDict()
        for nm in sorted({p[gkey] for p in papers}):
            members = [p for p in papers if p[gkey] == nm]
            cm = [p for p in members if p["comparable"]]
            tab[nm] = OrderedDict([("papers", len(members)), ("comparable", len(cm)),
                                   ("strict", OrderedDict([("successes", sum(p["strict"] for p in cm)), ("n", len(cm)), ("rate", (sum(p["strict"] for p in cm) / len(cm)) if cm else None),
                                                           ("interval95_exact", clopper_pearson(sum(p["strict"] for p in cm), len(cm)))])),
                                   ("broad", OrderedDict([("successes", sum(p["broad"] for p in cm)), ("n", len(cm)), ("rate", (sum(p["broad"] for p in cm) / len(cm)) if cm else None),
                                                          ("interval95_exact", clopper_pearson(sum(p["broad"] for p in cm), len(cm)))])),
                                   ("grades", dict(Counter(p["status"] for p in members)))])
        block["groups"] = tab
        n_big = sum(1 for nm in names if int(np.sum(gid == names.index(nm))) >= 3)
        block["floor"] = {"comparable_papers": 20, "groups_with_3": 3, "met": bool(len(comp) >= 20 and n_big >= 3), "groups_with_3_comparable": n_big}
        block["tests"] = OrderedDict()
        for k, rate in enumerate(("strict", "broad")):
            y = np.array([p[rate] for p in comp], dtype=float)
            T = hetero_T(y, gid, len(names))
            res = OrderedDict([("T", T), ("R", R)])
            if not block["floor"]["met"]:
                res.update(code="BELOW_FLOOR", p=None, label="BELOW_FLOOR")
            elif T is None:
                res.update(code="UNDEFINED", p=None, label="UNDEFINED")
            else:
                rng = np.random.default_rng(seed_of("D7", j0 + k))
                cnt = 0
                for _ in range(R):
                    Ts = hetero_T(rng.permutation(y), gid, len(names))
                    cnt += int(Ts is not None and bool(ge(Ts, T)))
                p = (1 + cnt) / (R + 1)
                big = [nm for nm in names if tab[nm]["comparable"] >= 3]
                res.update(code="EVAL", p=p, label="DIFFERS" if p <= 0.05 else "NOT SEPARATED")
                if res["label"] == "DIFFERS" and big:
                    res["highest"] = max(big, key=lambda nm: tab[nm][rate]["rate"])
                    res["lowest"] = min(big, key=lambda nm: tab[nm][rate]["rate"])
            block["tests"][rate] = res
        out[gname] = block
    return out


# ---------------------------------------------------------------------------------------------
# D8: twin series
# ---------------------------------------------------------------------------------------------
def twin_cell(z1, z2, P1, P2, sd1, sd2, rho1, rho2, kappas):
    """Build file 5.2.  P1, P2: placebo ATTs over the design's common draws.  Returns the cell record (code IDENTICAL when sd_u is at most 1e-9)."""
    u_star = np.asarray(P1, float) / sd1 - np.asarray(P2, float) / sd2
    n = u_star.size
    sd_u = float(u_star.std(ddof=1))
    base = OrderedDict([("z1", float(z1)), ("z2", float(z2)), ("sd_u", sd_u), ("sign_agree", bool(sign(z1) == sign(z2))), ("d_abs", float(abs(z1 - z2))),
                        ("rho1", float(rho1)), ("rho2", float(rho2))])
    if sd_u <= IDENTICAL_TOL:
        return dict(base, code="IDENTICAL")
    U = (z1 - z2) / sd_u
    w = np.abs(u_star) / sd_u
    line = rank_line(w, n)
    r = float(np.mean(w > line))
    out = dict(base, code="EVAL", U=float(U), line=line, r=r, n_draws=int(n))
    out["beyond"] = OrderedDict((lab, bool(abs(U) > kap * line)) for lab, kap in kappas.items())
    out["flip"] = OrderedDict((lab, bool((rho1 > kap) != (rho2 > kap))) for lab, kap in kappas.items())
    return out


def pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.size < 2:
        return None
    sx, sy = x.std(), y.std()
    if not (sx > 0 and sy > 0):
        return None
    return float(np.mean((x - x.mean()) * (y - y.mean())) / (sx * sy))


def poisson_binomial(ps):
    """Exact distribution of the number of successes of independent trials with probabilities ps (supplement S1.2)."""
    dist = [1.0]
    for p in ps:
        new = [0.0] * (len(dist) + 1)
        for j, v in enumerate(dist):
            new[j] += v * (1.0 - p)
            new[j + 1] += v * p
        dist = new
    return dist


def exact_reading(k, ps):
    """Supplement S1.2: upper and lower tail of the number of beyond cells against the random-timing probabilities, and the label."""
    dist = poisson_binomial(ps)
    up, lo = math.fsum(dist[k:]), math.fsum(dist[:k + 1])
    label = "TWINS DIFFER" if up <= 0.05 else "TWINS CLOSER THAN RANDOM TIMING" if lo <= 0.05 else "AT THE LINE"
    return OrderedDict([("k", int(k)), ("n", len(ps)), ("expected", math.fsum(ps)), ("p_upper", up), ("p_lower", lo), ("label", label)])


def e1_block(y, fams, B_=B_BOOT, seed=None):
    seed = seed or seed_of("D8")
    ids, F = N1.family_ids(list(fams))
    res = N1.e1_core(np.asarray(y, float), ids, F, B_, np.random.default_rng(seed))
    return {k: res.get(k) for k in ("status", "n", "F", "B", "theta", "se", "t", "p_two_sided", "c95", "interval95")}


def d8_run(designs, kappas):
    cells = []
    for pid, tier, s1, s2 in PAIRS:
        for did in sorted(designs):
            d = designs[did]
            if s1 in d.col and s2 in d.col:
                a, b = d.col[s1], d.col[s2]
                rec = twin_cell(d.z[a], d.z[b], d.P[a], d.P[b], d.sd[a], d.sd[b], d.rho[a], d.rho[b], kappas)
                rec.update(pair=pid, tier=tier[0], design_id=did, policy_family=d.policy_family)
                cells.append(rec)
    pairs_out = OrderedDict()
    for pid, tier, s1, s2 in PAIRS:
        cs = [c for c in cells if c["pair"] == pid]
        ev = [c for c in cs if c["code"] == "EVAL"]
        row = OrderedDict([("tier", tier), ("series_1", s1), ("series_2", s2), ("cells", len(cs)), ("identical", len(cs) - len(ev)), ("eval", len(ev)),
                           ("F", len({c["policy_family"] for c in ev}))])
        if len(cs) == 0:
            row["code"] = "NO_PAIR_CELL"
        elif not ev:
            row["code"] = "IDENTICAL"
        else:
            row["code"] = "EVAL"
            row["sign_agreement_share"] = float(np.mean([c["sign_agree"] for c in ev]))
            row["median_d_abs"] = float(np.median([c["d_abs"] for c in ev]))
            row["share_beyond"] = OrderedDict((lab, float(np.mean([c["beyond"][lab] for c in ev]))) for lab in kappas)
            row["flips"] = OrderedDict((lab, int(sum(c["flip"][lab] for c in ev))) for lab in kappas)
            row["corr_z1_z2"] = pearson([c["z1"] for c in ev], [c["z2"] for c in ev]) if len(ev) >= 8 else None
        pairs_out[pid] = row
    tiers = OrderedDict()
    for t, pids in TIER_OF.items():
        ev = [c for c in cells if c["tier"] == t and c["pair"] in pids and c["code"] == "EVAL"]
        allc = [c for c in cells if c["tier"] == t and c["pair"] in pids]
        fams = [c["policy_family"] for c in ev]
        row = OrderedDict([("pairs", list(pids)), ("cells", len(allc)), ("identical", len(allc) - len(ev)), ("n", len(ev)), ("F", len(set(fams)))])
        row["engine_floor"] = {"n": 8, "F": 5, "met": bool(len(ev) >= 8 and len(set(fams)) >= 5)}
        row["levels"] = OrderedDict()
        for lab in kappas:
            lv = OrderedDict([("kappa", float(kappas[lab]))])
            if ev:
                lv["share_beyond"] = float(np.mean([c["beyond"][lab] for c in ev]))
                lv["mean_r"] = float(np.mean([c["r"] for c in ev]))
                y = [float(c["beyond"][lab]) - c["r"] for c in ev]
                lv["delta_D8"] = float(np.mean(y))
                lv["flips"] = int(sum(c["flip"][lab] for c in ev))
                events = int(sum(c["beyond"][lab] for c in ev))
                lv["exact"] = exact_reading(events, [c["r"] for c in ev])                                   # supplement S1.2
                lv["E1_degenerate"] = bool(events < 5 or len(ev) - events < 5)                               # supplement S1.1
                if lab in ("raw", "calibrated") and row["engine_floor"]["met"]:
                    e1 = e1_block(y, fams)
                    lv["E1"] = e1
                    iv = e1.get("interval95")
                    lv["label_E1"] = (("TWINS DIFFER" if iv[0] > 0 else "TWINS CLOSER THAN RANDOM TIMING" if iv[1] < 0 else "AT THE LINE") if iv else "BELOW_FLOOR")
                    lv["label"] = lv["exact"]["label"] if lv["E1_degenerate"] else lv["label_E1"]
                else:
                    lv["label"] = "BELOW_FLOOR" if lab in ("raw", "calibrated") else None
            row["levels"][lab] = lv
        if ev:
            row["sign_agreement_share"] = float(np.mean([c["sign_agree"] for c in ev]))
            row["median_d_abs"] = float(np.median([c["d_abs"] for c in ev]))
        tiers[t] = row
    return OrderedDict([("pairs", pairs_out), ("tiers", tiers), ("cells", cells), ("n_cells", len(cells))])


# ---------------------------------------------------------------------------------------------
# D9: definition breaks
# ---------------------------------------------------------------------------------------------
def break_years(cat_entry, sid):
    """Build file 6.1."""
    ys = set()
    for b in cat_entry.get("series_breaks") or []:
        ys.add(int(b[0]) if isinstance(b, (list, tuple)) else int(b["year"]))
    sb = cat_entry.get("series_break")
    if isinstance(sb, dict) and "year" in sb:
        ys.add(int(sb["year"]))
    dc = cat_entry.get("definition_changes")
    dc = " ".join(dc) if isinstance(dc, list) else str(dc or "")
    if "single-race" in dc and "2018" in dc:
        ys.add(2018)
    if sid == "wonder_infant_mortality_rate":
        ys.update(INFANT_RATE_BREAKS)
    return sorted(ys)


def exposure(post_cells, years_set, breaks):
    """Build file 6.3.  post_cells: (g, t, base_period).  Returns (phi or None, n_cells)."""
    pc = [(g, t, b0) for g, t, b0 in post_cells if t in years_set and b0 in years_set]
    if not pc:
        return None, 0
    sp = sum(1 for _, t, b0 in pc if any(b0 < b <= t for b in breaks))
    return sp / len(pc), len(pc)


def exposure_group(phi):
    return "SPANNED" if phi >= 0.5 else "NOT SPANNED" if phi == 0 else "BETWEEN"


def d9_delta(y, lab):
    """mean y over SPANNED minus mean y over NOT SPANNED; lab: boolean array True for SPANNED, False for NOT SPANNED."""
    y, lab = np.asarray(y, float), np.asarray(lab, bool)
    if lab.sum() == 0 or (~lab).sum() == 0:
        return None
    return float(y[lab].mean() - y[~lab].mean())


def d9_permutation(Y, labels, strata, kappa_labels, R=R_REP, seed=None):
    """Build file 6.6.  Y: dict kappa label -> 0/1 array over the cells; labels: bool array; strata: list of index arrays (sorted order)."""
    seed = seed or seed_of("D9", 1)
    rng = np.random.default_rng(seed)
    pos = np.concatenate(strata) if strata else np.array([], dtype=int)
    obs = {k: d9_delta(Y[k][pos], labels[pos]) for k in kappa_labels}
    cnt = {k: 0 for k in kappa_labels}
    for _ in range(R):
        lab = labels.copy()
        for s in strata:
            perm = rng.permutation(len(s))
            lab[s] = labels[s][perm]
        for k in kappa_labels:
            ds = d9_delta(Y[k][pos], lab[pos])
            cnt[k] += int(ds is not None and obs[k] is not None and bool(ge(abs(ds), abs(obs[k]))))
    return obs, {k: (1 + cnt[k]) / (R + 1) for k in kappa_labels}


def d9_bootstrap(Y, labels, blocks, kappa_labels, B_=B_BOOT, seed=None):
    """Build file 6.7: policy-family block bootstrap of the difference of shares."""
    seed = seed or seed_of("D9", 2)
    rng = np.random.default_rng(seed)
    names = sorted(set(blocks))
    F = len(names)
    bid = np.array([names.index(b) for b in blocks], dtype=int)
    draw = rng.integers(0, F, size=(B_, F))
    c = np.zeros((B_, F))
    for f in range(F):
        c[:, f] = (draw == f).sum(axis=1)
    w = c[:, bid]
    lab = np.asarray(labels, bool)
    out = {}
    for k in kappa_labels:
        y = np.asarray(Y[k], float)
        nS = (w * lab[None, :]).sum(axis=1)
        nN = (w * (~lab)[None, :]).sum(axis=1)
        sS = (w * (lab * y)[None, :]).sum(axis=1)
        sN = (w * ((~lab) * y)[None, :]).sum(axis=1)
        ok = (nS > 0) & (nN > 0)
        delta = (sS[ok] / nS[ok]) - (sN[ok] / nN[ok])
        out[k] = OrderedDict([("interval95", pct(delta)), ("defined", int(ok.sum())), ("dropped", int((~ok).sum())), ("B", B_), ("F", F)])
    return out


def fe_residualize(v, d_id, s_id, n_d, n_s, tol=1e-12, max_sweeps=10000):
    """Supplement S1.2.2: subtract the design means, then the series means, until the largest change of a value in a sweep is below tol."""
    v = np.array(v, float)
    cd = np.maximum(np.bincount(d_id, minlength=n_d), 1)
    cs = np.maximum(np.bincount(s_id, minlength=n_s), 1)
    for _ in range(max_sweeps):
        old = v
        v = v - (np.bincount(d_id, weights=v, minlength=n_d) / cd)[d_id]
        v = v - (np.bincount(s_id, weights=v, minlength=n_s) / cs)[s_id]
        if float(np.max(np.abs(v - old))) < tol:
            break
    return v


def delta_fe(y, S, d_id, s_id):
    """Supplement S1.2.2: coefficient of S in y = alpha_design + beta_series + delta S + e by the Frisch-Waugh route (None when S is absorbed)."""
    d_id, s_id = np.asarray(d_id, int), np.asarray(s_id, int)
    n_d, n_s = int(d_id.max()) + 1, int(s_id.max()) + 1
    Yt = fe_residualize(y, d_id, s_id, n_d, n_s)
    St = fe_residualize(S, d_id, s_id, n_d, n_s)
    den = float(np.sum(St * St))
    if den <= 1e-9:
        return None
    return float(np.sum(St * Yt) / den)


def d9_fe(sel, kappas, B_=B_FE, seed=None):
    """Supplement S1.2: delta_FE and its policy-family block bootstrap interval at every kappa level.  sel: the SPANNED / NOT SPANNED cells."""
    seed = seed or seed_of("D9", 3)
    dn, sn = sorted({c["design_id"] for c in sel}), sorted({c["series_id"] for c in sel})
    d_id = np.array([dn.index(c["design_id"]) for c in sel], dtype=int)
    s_id = np.array([sn.index(c["series_id"]) for c in sel], dtype=int)
    S_ = np.array([1.0 if c["group"] == "SPANNED" else 0.0 for c in sel])
    names = sorted({c["policy_family"] for c in sel})
    F = len(names)
    members = [np.array([i for i, c in enumerate(sel) if c["policy_family"] == f], dtype=int) for f in names]
    draw = np.random.default_rng(seed).integers(0, F, size=(B_, F))
    out = OrderedDict()
    Ys = {lab: np.array([float(c["rho"] > k) for c in sel]) for lab, k in kappas.items()}
    boots = {lab: [] for lab in kappas}
    dropped = {lab: 0 for lab in kappas}
    for row in draw:
        ix = np.concatenate([members[f] for f in row])
        dd, ss = d_id[ix], s_id[ix]
        for lab in kappas:
            v = delta_fe(Ys[lab][ix], S_[ix], dd, ss)
            if v is None:
                dropped[lab] += 1
            else:
                boots[lab].append(v)
    for lab in kappas:
        out[lab] = OrderedDict([("delta_FE", delta_fe(Ys[lab], S_, d_id, s_id)), ("interval95", pct(boots[lab])), ("defined", len(boots[lab])), ("dropped", dropped[lab]),
                                ("B", B_), ("cells", len(sel)), ("designs", len(dn)), ("series", len(sn)), ("F", F)])
    return out


def d9_run(designs, catalog, group_time, kappas, R=R_REP, B_=B_BOOT):
    breaks = {sid: break_years(catalog[sid], sid) for sid in sorted({s for d in designs.values() for s in d.all_sids})}
    cells = []
    for did in sorted(designs):
        d = designs[did]
        post = group_time[did]
        for j, sid in enumerate(d.sids):
            br = breaks[sid]
            rec = OrderedDict([("design_id", did), ("policy_family", d.policy_family), ("series_id", sid), ("rho", float(d.rho[j])), ("z", float(d.z[j])), ("break_years", br)])
            if not br:
                rec["group"] = "NO_BREAK"
            else:
                phi, npost = exposure(post, set(int(y) for y in catalog[sid]["years"]), br)
                if phi is None:
                    rec["group"] = "NO_POST"
                else:
                    rec["phi"], rec["n_post_cells"], rec["group"] = float(phi), npost, exposure_group(phi)
            cells.append(rec)
    groups = Counter(c["group"] for c in cells)
    res = OrderedDict([("cells_total", len(cells)), ("groups", dict(groups)), ("break_series_in_sample", len({c["series_id"] for c in cells if c["break_years"]})), ("calibration_levels", list(kappas))])
    sel = [c for c in cells if c["group"] in ("SPANNED", "NOT SPANNED")]
    lev = OrderedDict()
    for lab, kap in kappas.items():
        entry = OrderedDict([("kappa", float(kap))])
        for g in ("SPANNED", "NOT SPANNED", "BETWEEN", "NO_BREAK"):
            m = [c for c in cells if c["group"] == g]
            entry[f"share_beyond_{g.replace(' ', '_')}"] = float(np.mean([c["rho"] > kap for c in m])) if m else None
        yy = [float(c["rho"] > kap) for c in sel]
        entry["delta_pooled"] = d9_delta(yy, [c["group"] == "SPANNED" for c in sel]) if sel else None
        lev[lab] = entry
    res["levels"] = lev
    nS, nN = groups.get("SPANNED", 0), groups.get("NOT SPANNED", 0)
    fams = {c["policy_family"] for c in sel}
    res["floor"] = {"cells_per_group": 20, "policy_families": 5, "met": bool(nS >= 20 and nN >= 20 and len(fams) >= 5), "spanned": nS, "not_spanned": nN, "F": len(fams)}
    if res["floor"]["met"]:
        sers = sorted({c["series_id"] for c in sel})
        strata, order, cell_pos = [], [], {}
        for sid in sers:
            members = [c for c in sorted(sel, key=lambda c: c["design_id"]) if c["series_id"] == sid]
            if any(c["group"] == "SPANNED" for c in members) and any(c["group"] == "NOT SPANNED" for c in members):
                strata.append(members)
        flat = [c for members in strata for c in members]
        labels = np.array([c["group"] == "SPANNED" for c in flat], dtype=bool)
        pos, k0 = [], 0
        for members in strata:
            pos.append(np.arange(k0, k0 + len(members)))
            k0 += len(members)
        Ystrat = {lab: np.array([float(c["rho"] > kap) for c in flat]) for lab, kap in kappas.items()}
        obs, p = d9_permutation(Ystrat, labels, pos, list(kappas), R=R)
        blocks = [c["policy_family"] for c in sel]
        Yall = {lab: np.array([float(c["rho"] > kap) for c in sel]) for lab, kap in kappas.items()}
        boot = d9_bootstrap(Yall, np.array([c["group"] == "SPANNED" for c in sel], dtype=bool), blocks, list(kappas), B_=B_)
        res["stratified"] = OrderedDict([("strata", len(strata)), ("cells", len(flat)), ("R", R)])
        for lab in kappas:
            iv = boot[lab]["interval95"]
            lv = res["levels"][lab]
            lv["delta_stratified"] = obs[lab]
            lv["p_permutation_two_sided"] = p[lab]
            lv["bootstrap"] = boot[lab]
            lv["label"] = "BREAK EXPOSURE ADDS" if (p[lab] <= 0.05 and iv is not None and iv[0] > 0) else "NOT SEPARATED"
        fe = d9_fe(sel, kappas)
        for lab in kappas:
            res["levels"][lab]["fe_sensitivity"] = fe[lab]
    else:
        for lab in kappas:
            res["levels"][lab]["label"] = "BELOW_FLOOR"
    by_series = OrderedDict()
    for sid in sorted({c["series_id"] for c in cells if c["break_years"]}):
        m = [c for c in cells if c["series_id"] == sid]
        by_series[sid] = OrderedDict([("break_years", m[0]["break_years"]), ("groups", dict(Counter(c["group"] for c in m))),
                                      ("share_beyond_raw", float(np.mean([c["rho"] > 1 for c in m])))])
    res["by_series"] = by_series
    return res, cells, breaks


# ---------------------------------------------------------------------------------------------
# D10: what laws invite
# ---------------------------------------------------------------------------------------------
def d10_run(rows):
    """rows: inventory CSV dict rows.  Build file 7."""
    cat_family = {}
    series_families = set()
    for r in rows:
        if r["row_type"] == "variable" and r["layer"] == "connector_catalog":
            cat_family[r["outcome_key"]] = r["family"]
            if r["data_state"] in ("on_disk_parsed", "on_disk_exports"):
                series_families.add(r["family"])
    results = set()
    for r in rows:
        if r["row_type"] != "usage":
            continue
        if r["layer"] == "l2_design" and r["unit_kind"] == "design" and r["status"] == "estimated" and r["role"] in ("primary", "secondary"):
            fam = r["family"] or cat_family.get(r["outcome_key"])
            if fam and r["policy_group"]:
                results.add((r["policy_group"], fam))
        elif r["layer"] == "replication_paper" and r["unit_kind"] == "paper":
            if r["family"] and r["policy_group"]:
                results.add((r["policy_group"], r["family"]))
    usage = [r for r in rows if r["row_type"] == "usage" and r["layer"] == "policy_db_text" and r["unit_kind"] == "db_text_group"]
    out_rows = []
    for r in usage:
        pair = (r["policy_group"], r["family"])
        status = "RESULT" if pair in results else "SERIES" if r["family"] in series_families else "NEITHER"
        out_rows.append(OrderedDict([("policy_group", r["policy_group"]), ("family", r["family"]), ("status", status), ("n_texts_db", int(r["n_texts_db"] or 0)),
                                     ("n_texts_db_state", int(r["n_texts_db_state"] or 0)), ("n_texts_db_shortlist", int(r["n_texts_db_shortlist"] or 0))]))
    res = OrderedDict([("rows", len(out_rows)), ("pairs_with_result", len(results)), ("families_with_series_on_disk", sorted(series_families)), ("calibration", "NOT_APPLICABLE")])

    def shares(sub, col):
        tot = sum(r[col] for r in sub)
        d = OrderedDict([("mentions", tot)])
        for st in ("RESULT", "SERIES", "NEITHER"):
            m = sum(r[col] for r in sub if r["status"] == st)
            d[st] = OrderedDict([("mentions", m), ("share", (m / tot) if tot else None)])
        return d
    cols = (("state_law_texts", "n_texts_db_state"), ("all_texts", "n_texts_db"), ("shortlist", "n_texts_db_shortlist"))
    res["overall"] = OrderedDict((name, shares(out_rows, col)) for name, col in cols)
    res["overall_without_other"] = OrderedDict((name, shares([r for r in out_rows if r["family"] != "other"], col)) for name, col in cols)
    res["by_policy_group"] = OrderedDict((g, shares([r for r in out_rows if r["policy_group"] == g], "n_texts_db_state")) for g in sorted({r["policy_group"] for r in out_rows}))
    res["by_family"] = OrderedDict((f, shares([r for r in out_rows if r["family"] == f], "n_texts_db_state")) for f in sorted({r["family"] for r in out_rows}))
    sh = res["overall"]["state_law_texts"]
    res["label"] = ("MEASURED" if sh["RESULT"]["share"] >= 0.5 else "MEASURABLE, NOT MEASURED" if sh["RESULT"]["share"] + sh["SERIES"]["share"] >= 0.5 else "MOSTLY OUTSIDE OUR DATA")
    sh2 = res["overall_without_other"]["state_law_texts"]
    res["label_without_other"] = ("MEASURED" if sh2["RESULT"]["share"] >= 0.5 else "MEASURABLE, NOT MEASURED" if sh2["RESULT"]["share"] + sh2["SERIES"]["share"] >= 0.5
                                  else "MOSTLY OUTSIDE OUR DATA")
    res["blocked"] = {"first_listed_suggestion_variant": "needs the 94-pattern keyword mapping of the inventory build (not kept) or a new registered mapping over data/pipeline/queue_v2/sources.jsonl"}
    return res, out_rows


# ---------------------------------------------------------------------------------------------
# inputs for D5 strata, D7, D9
# ---------------------------------------------------------------------------------------------
def registered_series(rows):
    """design id -> set of catalog series ids registered as primary or secondary outcome of an estimated design (build file 2.5)."""
    out = defaultdict(set)
    for r in rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design" and r["role"] in ("primary", "secondary") and r["status"] == "estimated" and r["catalog_series_id"]:
            out[r["unit_id"]].add(r["catalog_series_id"])
    return out


def papers_from_rows(rows):
    out = []
    for r in rows:
        if r["row_type"] == "usage" and r["layer"] == "replication_paper" and r["unit_kind"] == "paper":
            out.append(dict(unit_id=r["unit_id"], family=r["family"], unit_of_observation=r["unit_of_observation"], status=r["status"], policy_group=r["policy_group"]))
    return out


def read_group_time(manifest, designs, root=ROOT):
    """Build file 6.2: post cells (g, t, base_period) of the primary of each design from the stored RESULT.json (sha256 checked against the manifest)."""
    out, meta, problems = {}, {}, []
    for did in sorted(designs):
        m = manifest["designs"][did]
        path = root / m["folder"] / "RESULT.json"
        sha = S.sha256_file(path)
        if sha != m["result_sha256"]:
            problems.append(f"{did}: RESULT.json differs from SWEEP_MANIFEST.json")
            continue
        r = json.loads(path.read_text(encoding="utf-8"))
        gt = r["registered_results"]["primary"]["result"]["group_time"]
        out[did] = [(int(c["g"]), int(c["t"]), int(c["base_period"])) for c in gt if c.get("post")]
        meta[did] = {"folder": m["folder"], "result_sha256": sha, "post_cells": len(out[did]), "cohorts": sorted({int(c["g"]) for c in gt})}
        del r
    return out, meta, problems


def parse_doc_tables(path=AMEND_B):
    """The registered break table (6.1) and pair table (5.1) of the build file, for the identity checks."""
    text = Path(path).read_text(encoding="utf-8")
    breaks, pairs = {}, []
    for m in re.finditer(r"^\| (\S+) \| ((?:\d{4})(?:, \d{4})*) \|$", text, re.M):
        breaks[m.group(1)] = [int(x) for x in m.group(2).split(", ")]
    for m in re.finditer(r"^\| ([ABC]\d) \| ([^|]+) \| (\S+) \| (\S+) \|$", text, re.M):
        pairs.append((m.group(1), m.group(2).strip(), m.group(3), m.group(4)))
    return breaks, pairs


def write_once(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError:
        sys.exit(f"{S.rel(path)} exists: not overwritten (use --out-dir for a new version folder)")


def write_csv_once(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})
    except FileExistsError:
        sys.exit(f"{S.rel(path)} exists: not overwritten (use --out-dir for a new version folder)")


# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--sweep-dir", default=str(B.OUT), help="folder with the sweep files (tests only)")
    ap.add_argument("--verify-records", action="store_true", help="check the hash record and exit")
    ap.add_argument("--payload-to", default=None, help="also write the full payload JSON here (reading aid for dry runs; a new file; no effect on any number)")
    args = ap.parse_args(argv)
    out_dir, sweep_dir = Path(args.out_dir), Path(args.sweep_dir)

    problems = verify_records()
    if args.verify_records:
        print(json.dumps({"problems": problems, "ok": not problems, "t_amend_utc": parse_record()["t_amend_utc"]}, indent=1))
        return 0 if not problems else 1
    problems += B.verify_records() + D3.verify_records()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    live_sha = S.sha256_file(S.LIVE)
    rec = parse_record()
    if rec["listed"].get(S.rel(S.LIVE)) != live_sha:
        sys.exit("live set differs from the hash record: not run")

    d1j, d2j, calj = (S.read_json(p) for p in (D1_JSON, D2_JSON, CAL_JSON))
    for name, j in (("D1", d1j), ("D2", d2j), ("CAL", calj)):
        if j["stamp"]["live_set_sha256"] != live_sha:
            sys.exit(f"{name} result was made with another live set: not run")
    kap = calj["question"]["CAL"]["kappa"]
    kappas = OrderedDict([("raw", 1.0), ("calibrated", float(kap["kappa_hat"])), ("kappa_lo", float(kap["interval95_BD"][0])), ("kappa_hi", float(kap["interval95_BD"][1]))])
    kappa_strict = float(kap["kappa_strict"])

    designs, prob = load_sample(sweep_dir, d1j)
    if prob:
        sys.exit("identity with D1-V1 failed, not run: " + "; ".join(prob[:12]))
    ids = sorted(designs)
    n_cells = sum(len(d.sids) for d in designs.values())
    if n_cells != d1j["question"]["D1"]["series_calibration"]["series_total"]:
        sys.exit(f"cell count {n_cells} differs from D1-V1")
    inv_rows = S.inventory_rows()
    catalog = S.read_json(S.CATALOG)["series"]
    series_family = {sid: tab["family"] for sid, tab in S.catalog_series_table().items()}
    stores = (np.load(sweep_dir / STORE_RATE, allow_pickle=False), np.load(sweep_dir / STORE_COUNT, allow_pickle=False))
    for st, name in zip(stores, (STORE_RATE, STORE_COUNT)):
        want = rec["listed"].get(S.rel(sweep_dir / name))
        if want is not None and S.sha256_file(sweep_dir / name) != want:
            sys.exit(f"{name} differs from the hash record")
    count_series = sorted({k.rsplit("__", 1)[0] for k in stores[1].files if k.endswith("__val")})
    doc_breaks, doc_pairs = parse_doc_tables()
    if [tuple(p) for p in doc_pairs] != [tuple(p) for p in PAIRS]:
        sys.exit("the pair table of the code differs from the registered table")
    swept_all = sorted(designs[ids[0]].all_sids)
    code_breaks = {}
    for sid in swept_all:
        by = break_years(catalog[sid], sid)
        if by:
            code_breaks[sid] = by
    if code_breaks != doc_breaks:
        sys.exit("the break-year table generated from the catalog differs from the registered table")

    print("D4 ...", file=sys.stderr)
    ids_rep4, idx4 = replicate_index(designs, seed_of("D4", 7))
    k4 = OrderedDict(kappas)
    k4["kappa_strict"] = kappa_strict
    d4 = OrderedDict()
    d4["series_view"] = d4_run(designs, build_cellvec(designs, "series"), ids_rep4, idx4, k4)
    d4["family_view"] = d4_run(designs, build_cellvec(designs, "family"), ids_rep4, idx4, k4, loo=False)

    print("D5 ...", file=sys.stderr)
    ids_rep5, idx5 = replicate_index(designs, seed_of("D5", 7))
    nc_cells = {(c["design_id"], c["family"]): (c["code"] == "NC") for c in d2j["cells"]}
    d5 = d5_run(designs, ids_rep5, idx5, kappas, set(count_series), registered_series(inv_rows), nc_cells)

    print("D6 ...", file=sys.stderr)
    sm = S.read_json(sweep_dir / "SERIES_MANIFEST.json")
    swept_ids = sorted(sm["series"])
    d6 = d6_run(designs, stores, set(count_series), kappas, swept_ids, {sid: series_family[sid] for sid in swept_ids})
    d6["not_swept"] = OrderedDict()
    for r in inv_rows:
        if r["row_type"] == "variable" and r["layer"] == "connector_catalog" and r["outcome_key"] not in sm["series"]:
            ns = sm["not_swept"].get(r["outcome_key"], {})
            d6["not_swept"][r["outcome_key"]] = OrderedDict([("family", r["family"]), ("data_state", r["data_state"]), ("code", ns.get("code") or "NOT_ON_DISK_OR_UNAVAILABLE")])
    d6["not_swept_counts"] = dict(Counter(v["data_state"] for v in d6["not_swept"].values()))

    print("D7 ...", file=sys.stderr)
    d7 = d7_run(papers_from_rows(inv_rows))

    print("D8 ...", file=sys.stderr)
    d8 = d8_run(designs, kappas)

    print("D9 (reading the stored group-time structure) ...", file=sys.stderr)
    manifest = S.read_json(sweep_dir / "SWEEP_MANIFEST.json")
    group_time, gt_meta, prob = read_group_time(manifest, designs)
    if prob:
        sys.exit("; ".join(prob))
    d9, d9_cells, d9_breaks = d9_run(designs, catalog, group_time, kappas)

    print("D10 ...", file=sys.stderr)
    d10, d10_rows = d10_run(inv_rows)

    me = Path(__file__).resolve()
    inputs_paths = [S.LIVE, AMEND_Q, AMEND_B, HASH_REC, AMEND_S1, HASH_REC_S1, D1_JSON, D2_JSON, CAL_JSON, sweep_dir / "SWEEP_RUN.json", sweep_dir / "GATE_REPORT.json", sweep_dir / "SWEEP_MANIFEST.json",
                    sweep_dir / "SERIES_MANIFEST.json", sweep_dir / STORE_RATE, sweep_dir / STORE_COUNT, S.INVENTORY_CSV, S.CATALOG]
    stamp = OrderedDict([
        ("rule_id", RULE_ID), ("questions", QUESTIONS), ("versions", {q: f"{q}-V1" for q in QUESTIONS}), ("cutoff_utc", d1j["stamp"]["cutoff_utc"]), ("run_utc", utc_now()),
        ("t_amend_utc", rec["t_amend_utc"]), ("live_set_sha256", live_sha), ("d1_v1_sha256", S.sha256_file(D1_JSON)), ("d2_v1_sha256", S.sha256_file(D2_JSON)),
        ("cal_v1_sha256", S.sha256_file(CAL_JSON)), ("sweep_run_sha256", S.sha256_file(sweep_dir / "SWEEP_RUN.json")),
        ("code", {f: S.sha256_file(ROOT / f) for f in CODE_FILES}), ("questions_file_sha256", S.sha256_file(AMEND_Q)), ("build_file_sha256", S.sha256_file(AMEND_B)),
        ("hash_record_sha256", S.sha256_file(HASH_REC)), ("supplement_s1_sha256", S.sha256_file(AMEND_S1)), ("hash_record_s1_sha256", S.sha256_file(HASH_REC_S1)),
        ("entries_sha256", rec["entries"]),
        ("D", d1j["stamp"]["D"]), ("n_designs", len(designs)), ("F_policy_families", len({d.policy_family for d in designs.values()})), ("n_cells", n_cells),
        ("kappa", {"calibrated": kappas["calibrated"], "interval95_BD": [kappas["kappa_lo"], kappas["kappa_hi"]], "strict": kappa_strict, "source": S.rel(CAL_JSON)}),
        ("R_replicates", R_REP), ("B_bootstrap", B_BOOT), ("seeds", {"D4": seed_of("D4", 7), "D5": [seed_of("D5", 7), seed_of("D5", 1)], "D7": [seed_of("D7", j) for j in (1, 2, 3, 4)],
                                                                 "D8": seed_of("D8"), "D9": [seed_of("D9", 1), seed_of("D9", 2), seed_of("D9", 3)]}),
        ("python", sys.version.split()[0]), ("numpy", np.__version__), ("no_significance_label", True)])
    payload = S.clean(OrderedDict([
        ("schema_version", "l3-meta-d4-d10-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {S.rel(p): {"sha256": S.sha256_file(p), "bytes": Path(p).stat().st_size} for p in inputs_paths}),
        ("roster", OrderedDict([("designs", ids), ("policy_family", {d: designs[d].policy_family for d in ids}), ("scale_mode", {d: designs[d].scale_mode for d in ids}),
                                ("n_draws", {d: designs[d].n for d in ids}), ("count_series", count_series)])),
        ("questions_results", OrderedDict([("D4", d4), ("D5", d5), ("D6", d6), ("D7", d7), ("D8", d8), ("D9", d9), ("D10", d10)])),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    brief = OrderedDict([("stamp", {k: stamp[k] for k in ("run_utc", "n_designs", "F_policy_families", "n_cells", "kappa")}),
                         ("D4_series", {k: v for k, v in d4["series_view"].items() if k in ("designs", "columns", "cells", "code", "levels")}),
                         ("D5_summary", d5["summary"]), ("D6_classes", d6["classes"]), ("D7", {k: d7[k] for k in ("papers", "comparable", "rates")}),
                         ("D8_tiers", d8["tiers"]), ("D9", {k: d9[k] for k in ("cells_total", "groups", "floor", "levels")}), ("D10", {k: d10[k] for k in ("rows", "overall", "label")})])
    if args.payload_to:
        write_once(args.payload_to, json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    if not args.dry_run:
        target = out_dir / OUT_NAME
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        write_once(target, text)
        cell_rows = []
        for did in ids:
            d = designs[did]
            for j, s in enumerate(d.sids):
                cell_rows.append(OrderedDict([("design_id", did), ("policy_family", d.policy_family), ("scale_mode", d.scale_mode), ("series_id", s), ("key", series_key(d, s, set(count_series))),
                                              ("family", d.fam[j]), ("att", float(d.att[j])), ("sd", float(d.sd[j])), ("z", float(d.z[j])), ("line_c", float(d.c[j])), ("rho", float(d.rho[j])),
                                              ("n_draws", d.n)]))
        write_csv_once(out_dir / "CELLS_D4_D10_V1.csv", ["design_id", "policy_family", "scale_mode", "series_id", "key", "family", "att", "sd", "z", "line_c", "rho", "n_draws"], cell_rows)
        d5_rows = [OrderedDict([("key", r["key"]), ("family", r["family"]), ("m", r["m"]), ("F", r["F"]), ("stratum", r["stratum"])] +
                               [(f"{lab}_{fld}", r["levels"][lab][fld]) for lab in kappas for fld in ("tau", "H", "p_one_sided", "beyond_noise")]) for r in d5["series"]]
        write_csv_once(out_dir / "D5_SERIES_V1.csv", list(d5_rows[0].keys()) if d5_rows else ["key"], d5_rows)
        d6_rows = [OrderedDict([("key", r["key"]), ("family", r["family"]), ("supported_designs", r["supported_designs"]), ("mde_median", r["mde"]["median"]),
                                ("mde_rel_median", r["mde_rel"]["median"]), ("mde_sd_median", r["mde_sd"]["median"])] +
                               [(f"{lab}_{fld}", r["levels"][lab][fld]) for lab in kappas for fld in ("mde_sd_median", "class")]) for r in d6["series"]]
        write_csv_once(out_dir / "D6_SERIES_V1.csv", list(d6_rows[0].keys()) if d6_rows else ["key"], d6_rows)
        d8_rows = [OrderedDict([("pair", c["pair"]), ("tier", c["tier"]), ("design_id", c["design_id"]), ("policy_family", c["policy_family"]), ("code", c["code"]), ("z1", c["z1"]), ("z2", c["z2"]),
                                ("sd_u", c["sd_u"]), ("U", c.get("U")), ("line", c.get("line")), ("r", c.get("r")), ("sign_agree", c["sign_agree"])] +
                               [(f"beyond_{lab}", (c.get("beyond") or {}).get(lab)) for lab in kappas]) for c in d8["cells"]]
        write_csv_once(out_dir / "D8_CELLS_V1.csv", list(d8_rows[0].keys()) if d8_rows else ["pair"], d8_rows)
        write_csv_once(out_dir / "D9_CELLS_V1.csv", ["design_id", "policy_family", "series_id", "group", "phi", "n_post_cells", "rho", "z"], d9_cells)
        write_csv_once(out_dir / "D10_PAIRS_V1.csv", ["policy_group", "family", "status", "n_texts_db", "n_texts_db_state", "n_texts_db_shortlist"], d10_rows)
        write_csv_once(out_dir / "BREAK_YEARS_D9_V1.csv", ["series_id", "break_years"], [{"series_id": s, "break_years": ";".join(map(str, v))} for s, v in sorted(d9_breaks.items()) if v])
        write_once(out_dir / "COHORT_STRUCTURE_D9_V1.json", json.dumps(S.clean(OrderedDict([("note", "post group-time cells (g, t, base_period) of the primary of each design, from the stored RESULT.json"),
                                                                                              ("designs", OrderedDict((d, OrderedDict([("meta", gt_meta[d]), ("post_cells", group_time[d])])) for d in ids))])),
                                                      ensure_ascii=False) + "\n")
        shutil.copy2(S.LIVE, out_dir / "l2_set_live_used.json")
        assert S.sha256_file(out_dir / "l2_set_live_used.json") == live_sha
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_d4_d10.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code"]["scripts/l3_meta/build_d4_d10.py"], "live_set_sha256": live_sha, "results_file": S.rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    print(json.dumps(S.clean(brief), ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {S.rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
