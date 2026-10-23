#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version N1-V1 (rule id EX-1): N1 "Which states do state DiD designs actually identify?"

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_N1_V1.md (recorded before any N1 statistic was computed),
PREREG_AMEND_20261012_EXPLORATORY.md (EX-1) and the frozen text of N1 (PREREG_L3_META_ADDENDUM_V2.md, with engine E1 of section 0.5).

N1 compares, per design, the states that identify the ATT (treated states T, read in their own adoption year) with the states still free
to adopt (at-risk states R, read in the design's median adoption year) on four standardised covariates (Democratic unified control on
1 January, citizen ideology, ln real per-capita income, urban share).  D_i is the root-mean-square standardised gap; mu_i and tau_i are
the mean and SD of D over 999 random reassignments of the same adoption cohorts; Z_i = (D_i - mu_i)/tau_i; theta1 = mean Z_i with E1
(family-block bootstrap-t).  Metadata only: no estimate, SE, interval or noise value is read.  The live set is parsed, but only the
fields in LIVE_FIELDS are kept.

READ-ONLY on every L2 file; nothing under scripts/l2_pipeline or data/l2_pipeline is opened.  No network.  Only output:
data/l3_meta/n1_v1/{RESULTS_N1_V1.json, RUN_LOG.jsonl, l2_set_live_used.json}.  Every statistic is exploratory: no "significant"
label, no multiplicity adjustment, no licensed sentence.

Run:  .venv_did/bin/python -B scripts/l3_meta/build_n1.py [--dry-run] [--out-dir DIR]      (numpy only)
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
import warnings
import zlib
from collections import Counter, OrderedDict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

RULE_ID = "EX-1"
QUESTION = "N1"
VERSION = "N1-V1"
DATA = ROOT / "data"
LIVE = DATA / "l3_meta" / "l2_set_live.json"
SETS = DATA / "l3_meta" / "n1_sets" / "n1_sets_v1.json"
COV_PATH = DATA / "l3_meta" / "covariates" / "state_year_v2.csv"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_N1_V1.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_N1_HASH.txt"
OUT_DIR = DATA / "l3_meta" / "n1_v1"
OUT_NAME = "RESULTS_N1_V1.json"

# fixed by the frozen text (addendum 0.5, N1) and the amendment
M_REASSIGN = 999
B_BOOT = 9999
SEED_E1 = [20261011, 1]                      # E1 stream (k = 1)
SEED_CAL = [20261011, 1, 3]                  # calibration bootstrap stream (amendment 5.2)
B_CAL = 999
P_CAL = 1000
MIN_U, MIN_T, MIN_R = 20, 3, 3               # inclusion (N1 text)
MIN_N, MIN_F = 20, 20                        # printed beside n, gate nothing (EX-1 section 1)
N1B_MIN_N = 30
DC = "DC"
COVARIATES = [("dem_unified_jan1", "Democratic unified control, 1 Jan"),
              ("citizen_ideology", "citizen ideology (Berry et al.)"),
              ("ln_pcpi_real_2024usd", "ln real per-capita personal income"),
              ("urban_share", "urban population share")]
LIVE_FIELDS = ("design_id", "status", "gate", "domain", "policy_family", "source_group")
ARCHIVE_GROUPS = ("prototype", "manual_batch")


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def finite(x):
    return isinstance(x, (int, float, np.floating, np.integer)) and not isinstance(x, bool) and math.isfinite(float(x))


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, numpy scalars/arrays -> python, tuples -> lists."""
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def crc(design_id):
    return zlib.crc32(design_id.encode("utf-8"))


def frac(k, n):
    return k / n if n else None


def weighted_lower_median(values):
    """Median of values, every value weight 1: the smallest y with at least half of the values <= y (the lower of two middle values)."""
    v = sorted(values)
    if not v:
        return None
    return v[(len(v) - 1) // 2]


# ---------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------
def load_covariates(path=COV_PATH):
    """Dense array V[c, state, year]; blank cells are NaN (unknown cells are dropped, never filled)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    states = sorted({r["state"] for r in rows})
    years = sorted({int(r["year"]) for r in rows})
    y0 = years[0]
    V = np.full((len(COVARIATES), len(states), years[-1] - y0 + 1), np.nan)
    si = {s: i for i, s in enumerate(states)}
    for r in rows:
        for c, (col, _) in enumerate(COVARIATES):
            v = r[col]
            if v not in ("", None):
                V[c, si[r["state"]], int(r["year"]) - y0] = float(v)
    return {"V": V, "states": states, "state_index": si, "year0": y0, "year_last": years[-1]}


def year_idx(cov, y):
    i = int(y) - cov["year0"]
    if not 0 <= i < cov["V"].shape[2]:
        raise KeyError(f"year {y} outside the covariate file")
    return i


def load_live(path=LIVE):
    """Roster D from the live set.  Only LIVE_FIELDS are kept: no estimate, SE, interval or noise field is carried."""
    live = read_json(path)
    designs = [{k: d.get(k) for k in LIVE_FIELDS} for d in live["designs"]]
    meta = {"as_of": live["as_of"], "counts": {k: live["counts"][k] for k in ("processed", "estimated", "not_evaluable")},
            "outside_D": {k: len(v) for k, v in live["excluded_not_counted"].items()}}
    return designs, meta


def parse_hash_record(path=AMEND_HASH):
    """sha256 and T_amend of the amendment, and the input hashes listed in the record."""
    text = Path(path).read_text(encoding="utf-8")
    sha = re.search(r"^sha256:\s*([0-9a-f]{64})", text, re.M)
    t_amend = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    return {"sha256": sha.group(1) if sha else None, "t_amend_utc": t_amend.group(1) if t_amend else None, "listed": listed}


# ---------------------------------------------------------------------------------------------
# design preparation (DC rule, inclusion rule, code checks)
# ---------------------------------------------------------------------------------------------
def prepare_design(rec):
    """Amendment sections 1.3, 2 and 6 on one record of the sets file.

    Returns a dict with 'reasons' (list of (class, name), first = the coverage code) and, when the sets pass,
    the DC-free U, R, E, the ordered T slots (state, cohort g, onset year) and the reference year."""
    did = rec["design_id"]
    reasons = []
    U = sorted(s for s in rec["U"] if s != DC)
    T = sorted(s for s in rec["T"] if s != DC)
    R = None if rec.get("R") is None else sorted(s for s in rec["R"] if s != DC)
    if not rec.get("treatment_audit_passed"):
        reasons.append(("FIELD_MISSING", "treatment_audit_not_passed"))
    if R is None or not rec.get("R_resolved"):
        reasons.append(("FIELD_MISSING", "R_unresolved"))
    if len(U) < MIN_U:
        reasons.append(("SUPPORT", "U_lt20"))
    if len(T) < MIN_T:
        reasons.append(("SUPPORT", "T_lt3"))
    if R is not None and len(R) < MIN_R:
        reasons.append(("SUPPORT", "R_lt3"))
    out = {"design_id": did, "reasons": reasons, "U": U, "T": T, "R": R, "n_U_with_dc": len(rec["U"]), "dc_in_U": DC in rec["U"],
           "dc_in_T": DC in rec["T"], "dc_in_R": R is not None and DC in (rec.get("R") or [])}
    if reasons:
        return out
    onset = rec.get("T_operative_onset_year") or {}
    cohorts = {}
    for g, members in (rec.get("T_cohorts") or {}).items():
        ms = sorted(m for m in members if m != DC)
        if ms:
            cohorts[int(g)] = ms
    slots = [(s, g, onset.get(s)) for g in sorted(cohorts) for s in cohorts[g]]
    inforce = set(rec.get("in_force_last_panel_year") or []) - {DC}
    uset, tset, rset = set(U), set(T), set(R)
    checks = []
    if not tset <= uset:
        checks.append("T_outside_U")
    if not rset <= uset:
        checks.append("R_outside_U")
    if tset & rset:
        checks.append("T_R_overlap")
    if sorted(s for s, _, _ in slots) != T:
        checks.append("T_not_equal_to_cohort_members")
    if any(not isinstance(y, int) for _, _, y in slots):
        checks.append("T_state_without_onset_year")
    if not inforce <= uset or rset != uset - inforce:
        checks.append("R_not_U_minus_in_force_at_end")
    if checks:
        reasons.extend(("OTHER", c) for c in checks)
        out["checks"] = checks
        return out
    ystar = weighted_lower_median([y for _, _, y in slots])
    out.update({"slots": slots, "cohorts": {g: ms for g, ms in sorted(cohorts.items())}, "E": sorted(inforce - tset), "ystar": ystar,
                "ystar_sets_file": rec.get("weighted_lower_median_adoption_year_T"),
                "mixed_cohorts": sorted(g for g, ms in cohorts.items() if len({onset[m] for m in ms}) > 1),
                "support_passed": rec.get("support_passed"), "panel_years": rec.get("panel_years")})
    return out


def _sd(values):
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    return float(v.std(ddof=1)) if v.size >= 2 else float("nan")


def design_arrays(prep, cov):
    """Index arrays for the vectorised computation (states in code order, slots in cohort order)."""
    si, V = cov["state_index"], cov["V"]
    U_idx = np.array([si[s] for s in prep["U"]], dtype=int)
    T_idx = np.array([si[s] for s, _, _ in prep["slots"]], dtype=int)
    slot_year_idx = np.array([year_idx(cov, y) for _, _, y in prep["slots"]], dtype=int)
    R_idx = np.array([si[s] for s in prep["R"]], dtype=int)
    Eset = set(prep["E"])
    pool_s1 = np.array([si[s] for s in prep["U"] if s not in Eset], dtype=int)
    ystar_idx = year_idx(cov, prep["ystar"])
    sd = np.array([_sd(V[c, U_idx, ystar_idx]) for c in range(len(COVARIATES))])
    return {"U_idx": U_idx, "T_idx": T_idx, "slot_year_idx": slot_year_idx, "R_idx": R_idx, "pool_s1_idx": pool_s1,
            "n_E": len(prep["E"]), "ystar_idx": ystar_idx, "sd": sd}


# ---------------------------------------------------------------------------------------------
# D, reassignment, Z
# ---------------------------------------------------------------------------------------------
def gaps_and_D(V, T_idx, slot_year_idx, R_idx, ystar_idx, sd):
    """Standardised gaps (treated mean minus at-risk mean, per covariate) and D = root-mean-square of the four gaps.

    T_idx (M, k) / R_idx (M, r): covariate-file state indices (1-D = one assignment); slot_year_idx (k,): the year each treated slot is read
    in; at-risk states are read in ystar_idx.  Means skip NaN cells; a group with no value gives NaN (an undefined draw)."""
    T_idx = np.atleast_2d(T_idx)
    R_idx = np.atleast_2d(R_idx)
    gaps = np.empty((T_idx.shape[0], V.shape[0]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        for c in range(V.shape[0]):
            mt = np.nanmean(V[c][T_idx, slot_year_idx[None, :]], axis=1)
            mr = np.nanmean(V[c][R_idx, ystar_idx], axis=1)
            gaps[:, c] = (mt - mr) / sd[c]
        D = np.sqrt(np.mean(gaps ** 2, axis=1))
    return gaps, D


def draw_permutations(n_pool, M, rng):
    """M uniform random permutations of range(n_pool) (row-wise argsort of uniform numbers).

    Cohorts taking consecutive blocks of a uniform permutation is the same as successive uniform draws without replacement
    from the states left (the N1 reassignment)."""
    return np.argsort(rng.random((M, n_pool)), axis=1, kind="stable")


def reassigned_D(arr, V, mode, M, rng):
    """D* over M reassignments.  mode 'primary': cohorts drawn from all of U, R* = U minus the drawn set (the text).
    mode 'S1': cohorts drawn from U minus the other adopters E, R* = that pool minus the drawn set (the size of R)."""
    pool = arr["U_idx"] if mode == "primary" else arr["pool_s1_idx"]
    k = len(arr["T_idx"])
    perms = draw_permutations(len(pool), M, rng)
    return gaps_and_D(V, pool[perms[:, :k]], arr["slot_year_idx"], pool[perms[:, k:]], arr["ystar_idx"], arr["sd"])


def null_moments(Dstar):
    d = np.asarray(Dstar, dtype=float)
    ok = d[np.isfinite(d)]
    if ok.size < 2:
        return {"mu": float("nan"), "tau": float("nan"), "M_defined": int(ok.size), "M_undefined": int(d.size - ok.size)}
    return {"mu": float(ok.mean()), "tau": float(ok.std(ddof=1)), "M_defined": int(ok.size), "M_undefined": int(d.size - ok.size)}


def z_and_p(D_obs, Dstar, mom):
    """Z = (D - mu)/tau and the permutation p = (1 + #{D* >= D})/(M_def + 1)."""
    ok = np.asarray(Dstar, dtype=float)
    ok = ok[np.isfinite(ok)]
    z = (D_obs - mom["mu"]) / mom["tau"] if mom["tau"] > 0 else float("nan")
    p = (1 + int(np.sum(ok >= D_obs))) / (ok.size + 1)
    return float(z), float(p)


def evaluate_design(prep, cov, M=M_REASSIGN):
    """Observed D, the primary null and S1 (+ a second stream for stability).  Adds covariate reasons to prep['reasons']."""
    arr = design_arrays(prep, cov)
    V = cov["V"]
    did = prep["design_id"]
    if not np.all(np.isfinite(arr["sd"])) or np.any(arr["sd"] <= 0):
        prep["reasons"].append(("SUPPORT", "covariate_constant"))
        return None
    gaps, D = gaps_and_D(V, arr["T_idx"], arr["slot_year_idx"], arr["R_idx"], arr["ystar_idx"], arr["sd"])
    if not np.isfinite(D[0]):
        prep["reasons"].append(("FIELD_MISSING", "covariate_without_value_in_group"))
        return None
    D_obs = float(D[0])
    res = {"arr": arr, "gaps": gaps[0], "D": D_obs}
    for mode in ("primary", "S1"):
        rng = np.random.default_rng([20261011, 1, crc(did)])
        _, Dstar = reassigned_D(arr, V, mode, M, rng)
        mom = null_moments(Dstar)
        if not (mom["tau"] > 0):
            prep["reasons"].append(("SUPPORT", "tau_zero"))
            return None
        z, p = z_and_p(D_obs, Dstar, mom)
        res[mode] = dict(mom, Z=z, p=p)
    rng2 = np.random.default_rng([20261011, 1, crc(did), 1])
    _, Dstar2 = reassigned_D(arr, V, "primary", M, rng2)
    mom2 = null_moments(Dstar2)
    res["stability"] = dict(mom2, Z=z_and_p(D_obs, Dstar2, mom2)[0])
    return res


# ---------------------------------------------------------------------------------------------
# E1: equal-weight mean, family-block bootstrap-t (addendum 0.5)
# ---------------------------------------------------------------------------------------------
def family_ids(fam):
    names = sorted(set(fam))
    pos = {f: i for i, f in enumerate(names)}
    return np.array([pos[f] for f in fam], dtype=int), len(names)


def e1_core(y, ids, F, B, rng, return_t=False):
    """theta = mean(y); SE = sqrt(F/(F-1) sum_f (sum_{i in f}(y_i - theta))^2)/n; t = theta/SE.
    Replicate: F families drawn with replacement (all their designs, duplicates kept, each draw its own cluster), theta*, SE*,
    t* = (theta* - theta)/SE* (0 if SE* = 0).  p = (1 + #{|t*| >= |t|})/(B + 1); interval = theta +/- c SE with c = 95th percentile of |t*|."""
    y = np.asarray(y, dtype=float)
    n = int(y.size)
    if F < 2 or n < 2:
        return {"status": "NOT_COMPUTABLE", "n": n, "F": int(F)}
    S = np.bincount(ids, weights=y, minlength=F)
    m = np.bincount(ids, minlength=F).astype(float)
    theta = float(y.mean())
    tiny = 1e-9 * max(float(np.abs(y).max()), 1e-300)          # an SE below this is a floating-point zero (e.g. one family drawn F times)
    se = math.sqrt(F / (F - 1) * float(np.sum((S - m * theta) ** 2))) / n
    t = theta / se if se > tiny else float("nan")
    draw = rng.integers(0, F, size=(B, F))
    Sb, mb = S[draw], m[draw]
    nb = mb.sum(axis=1)
    thb = Sb.sum(axis=1) / nb
    seb = np.sqrt(F / (F - 1) * np.sum((Sb - mb * thb[:, None]) ** 2, axis=1)) / nb
    with np.errstate(divide="ignore", invalid="ignore"):
        tb = np.where(seb > tiny, (thb - theta) / seb, 0.0)
    a = np.abs(tb)
    c = float(np.percentile(a, 95))
    out = {"status": "OK", "n": n, "F": int(F), "B": int(B), "theta": theta, "se": se, "t": t,
           "p_two_sided": ((1 + int(np.sum(a >= abs(t)))) / (B + 1)) if math.isfinite(t) else None,
           "c95": c, "interval95": [theta - c * se, theta + c * se]}
    if return_t:
        out["t_star"] = tb
    return out


def e1_family_bootstrap_t(y, fam, B=B_BOOT, seed=SEED_E1, return_t=False):
    ids, F = family_ids(fam)
    return e1_core(y, ids, F, B, np.random.default_rng(seed), return_t=return_t)


# ---------------------------------------------------------------------------------------------
# calibration on pseudo-assignments (amendment 5.2): a diagnostic, not a gate
# ---------------------------------------------------------------------------------------------
def pseudo_Z(ev, V, did, P):
    """P pseudo-assignments of one design: cohorts, then |E| further adopters, then R, from one uniform permutation of U.
    Returns Z against the primary's and S1's (mu, tau)."""
    arr = ev["arr"]
    rng = np.random.default_rng([20261011, 1, crc(did), 2])
    k, nE = len(arr["T_idx"]), arr["n_E"]
    perms = draw_permutations(len(arr["U_idx"]), P, rng)
    pool = arr["U_idx"]
    _, Dp = gaps_and_D(V, pool[perms[:, :k]], arr["slot_year_idx"], pool[perms[:, k + nE:]], arr["ystar_idx"], arr["sd"])
    out = {}
    for mode in ("primary", "S1"):
        out[mode] = (Dp - ev[mode]["mu"]) / ev[mode]["tau"]
    return out


def calibration(evals, V, fams, P=P_CAL, B=B_CAL, seed=SEED_CAL):
    """Mean Z and E1 rejection rate (two-sided p <= .05) over P pseudo-datasets, for the primary null and for S1."""
    n = len(evals)
    Z = {"primary": np.empty((P, n)), "S1": np.empty((P, n))}
    for j, (did, ev) in enumerate(evals):
        z = pseudo_Z(ev, V, did, P)
        for mode in Z:
            Z[mode][:, j] = z[mode]
    ids, F = family_ids(fams)
    rng = np.random.default_rng(seed)
    out = {"P": P, "B": B, "seed": seed, "n": n, "F": F,
           "note": "designs independent in this simulation; real designs share states and calendar years, so real size can be worse"}
    for mode in Z:
        rej, rej_pos, rej_neg, thetas = 0, 0, 0, []
        for r in range(P):
            res = e1_core(Z[mode][r], ids, F, B, rng)
            if res["status"] != "OK" or res["p_two_sided"] is None:
                continue
            thetas.append(res["theta"])
            if res["p_two_sided"] <= 0.05:
                rej += 1
                if res["theta"] > 0:
                    rej_pos += 1
                else:
                    rej_neg += 1
        zz = Z[mode]
        out[mode] = {"mean_Z_per_design_average": float(np.nanmean(zz)),
                     "mean_theta1": float(np.mean(thetas)) if thetas else None,
                     "sd_of_pseudo_Z_pooled": float(np.nanstd(zz, ddof=1)),
                     "rejection_rate_at_05": rej / len(thetas) if thetas else None, "rejects_positive": rej_pos, "rejects_negative": rej_neg,
                     "datasets_used": len(thetas),
                     "design_mean_pseudo_Z": {did: float(np.nanmean(zz[:, j])) for j, (did, _) in enumerate(evals)}}
    return out


# ---------------------------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------------------------
def z_block(rows, key):
    z = [r[key]["Z"] for r in rows]
    if not z:
        return {"n": 0}
    q = np.percentile(z, [25, 50, 75])
    return {"n": len(z), "F": len({r["family"] for r in rows}), "mean_Z": float(np.mean(z)), "median_Z": float(q[1]),
            "quartiles": [float(q[0]), float(q[2])], "min": float(min(z)), "max": float(max(z)), "share_positive": sum(1 for x in z if x > 0) / len(z)}


def p_counts(rows, key):
    n = len(rows)
    ps = [r[key]["p"] for r in rows]
    return {"n": n, "p_le_05": sum(1 for p in ps if p <= 0.05), "p_le_10": sum(1 for p in ps if p <= 0.10),
            "expected_p_le_05": 0.05 * n, "expected_p_le_10": 0.10 * n}


def covariate_gaps(rows):
    out = {}
    for c, (col, label) in enumerate(COVARIATES):
        g = [r["gaps"][c] for r in rows]
        out[col] = {"label": label, "mean_signed_gap": float(np.mean(g)) if g else None, "median_signed_gap": float(np.median(g)) if g else None,
                    "share_above_0": sum(1 for x in g if x > 0) / len(g) if g else None,
                    "mean_abs_gap": float(np.mean(np.abs(g))) if g else None}
    return out


def coverage_code(reasons):
    return "EVAL" if not reasons else f"{reasons[0][0]}:{reasons[0][1]}"


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--live-set-changed-ok", action="store_true", help="accept a live set whose hash differs from the amendment record")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    # integrity of the recorded rules and inputs
    rec = parse_hash_record()
    amend_sha = sha256_file(AMEND)
    if rec["sha256"] != amend_sha:
        sys.exit("amendment file differs from its hash record: not run")
    live_sha, sets_sha, cov_sha = sha256_file(LIVE), sha256_file(SETS), sha256_file(COV_PATH)
    for path, got in ((rel(SETS), sets_sha), (rel(COV_PATH), cov_sha)):
        if rec["listed"].get(path) != got:
            sys.exit(f"{path} differs from the amendment record: not run")
    live_same = rec["listed"].get(rel(LIVE)) == live_sha
    if not live_same and not args.live_set_changed_ok:
        sys.exit("live set differs from the amendment record: not run (use --live-set-changed-ok for a new roster)")

    roster, live_meta = load_live()
    sets = read_json(SETS)
    sets_by_id = {d["design_id"]: d for d in sets["designs"]}
    skipped_by_id = {s["design_id"]: s for s in sets["skipped"]}
    cov = load_covariates()
    V = cov["V"]

    rows, evals, family_mismatch, status_mismatch, ystar_differs = [], [], [], [], []
    for d in roster:
        did = d["design_id"]
        fam = d["policy_family"] or did
        row = {"design_id": did, "family": fam, "domain": d["domain"], "live_status": d["status"], "gate": d["gate"], "source_group": d["source_group"]}
        reasons = []
        if did in sets_by_id:
            rec_ = sets_by_id[did]
            if (rec_.get("policy_family") or did) != fam:
                family_mismatch.append({"design_id": did, "live": fam, "sets_file": rec_.get("policy_family")})
            if rec_.get("live_status") != d["status"]:
                status_mismatch.append({"design_id": did, "live": d["status"], "sets_file": rec_.get("live_status")})
            prep = prepare_design(rec_)
            reasons = prep["reasons"]
            row.update({"U_n": len(prep["U"]), "T_n": len(prep["T"]), "R_n": None if prep["R"] is None else len(prep["R"]),
                        "dc_in_U": prep["dc_in_U"], "dc_in_T": prep["dc_in_T"], "dc_in_R": prep["dc_in_R"],
                        "support_passed": rec_.get("support_passed"), "panel_years": rec_.get("panel_years")})
            if not reasons:
                ev = evaluate_design(prep, cov)
                reasons = prep["reasons"]
                if not reasons:
                    row.update({"E_n": len(prep["E"]), "cohorts_n": len(prep["cohorts"]), "ystar": prep["ystar"], "ystar_sets_file": prep["ystar_sets_file"],
                                "mixed_cohorts": prep["mixed_cohorts"], "onset_year_range": [min(y for _, _, y in prep["slots"]), max(y for _, _, y in prep["slots"])],
                                "sd_at_ystar": ev["arr"]["sd"], "gaps": ev["gaps"], "D": ev["D"], "primary": ev["primary"], "S1": ev["S1"],
                                "stability": ev["stability"]})
                    if prep["ystar"] != prep["ystar_sets_file"]:
                        ystar_differs.append({"design_id": did, "used": prep["ystar"], "sets_file": prep["ystar_sets_file"], "dc_in_T": prep["dc_in_T"]})
                    evals.append((did, ev, row))
        elif did in skipped_by_id:
            if d["source_group"] in ARCHIVE_GROUPS:
                reasons = [("FIELD_MISSING", "archive_design_has_no_treatment_file")]
            else:
                reasons = [("STOP_PRE_RUN", str(d["gate"]))]
        else:
            reasons = [("OTHER", "design_not_in_sets_file")]
        row["code"] = coverage_code(reasons)
        row["reasons"] = [f"{a}:{b}" for a, b in reasons]
        rows.append(row)

    D_n = len(roster)
    ev_rows = [r for r in rows if r["code"] == "EVAL"]
    n = len(ev_rows)
    fams = [r["family"] for r in ev_rows]
    F = len(set(fams))
    ids, _ = family_ids(fams)

    def inference(key):
        z = [r[key]["Z"] for r in ev_rows]
        res = e1_core(z, ids, F, B_BOOT, np.random.default_rng(SEED_E1))
        res["seed"] = SEED_E1
        res["blocks"] = "policy_family"
        return res

    primary = inference("primary")
    s1 = inference("S1")
    codes = dict(sorted(Counter(r["code"] for r in rows).items()))
    est = [r for r in ev_rows if r["live_status"] == "estimated"]
    non = [r for r in ev_rows if r["live_status"] != "estimated"]
    sup_pass = [r for r in ev_rows if r["support_passed"] is True]
    sup_stop = [r for r in ev_rows if r["support_passed"] is False]
    n1b_candidates = [r["design_id"] for r in est if r["cohorts_n"] >= 3]

    stability = {"seed_note": "second independent stream [20261011, 1, crc32(design_id), 1], M = 999",
                 "theta1_main_stream": float(np.mean([r["primary"]["Z"] for r in ev_rows])),
                 "theta1_second_stream": float(np.mean([r["stability"]["Z"] for r in ev_rows])),
                 "max_abs_change_in_Z": float(max(abs(r["stability"]["Z"] - r["primary"]["Z"]) for r in ev_rows)),
                 "undefined_draws_primary": int(sum(r["primary"]["M_undefined"] for r in ev_rows)),
                 "undefined_draws_S1": int(sum(r["S1"]["M_undefined"] for r in ev_rows))}
    cal = calibration([(did, ev) for did, ev, _ in evals], V, fams)

    me = Path(__file__).resolve()
    stamp = {"rule_id": RULE_ID, "question": QUESTION, "version": VERSION, "cutoff_utc": live_meta["as_of"], "run_utc": utc_now(),
             "live_set_sha256": live_sha, "live_set_matches_amendment_record": live_same, "sets_sha256": sets_sha, "covariates_sha256": cov_sha,
             "code_sha256": sha256_file(me), "code": rel(me), "amendment_sha256": amend_sha, "t_amend_utc": rec["t_amend_utc"],
             "D": D_n, "estimated": live_meta["counts"]["estimated"], "not_evaluable": live_meta["counts"]["not_evaluable"],
             "outside_D": live_meta["outside_D"], "n": n, "F": F, "python": sys.version.split()[0], "numpy": np.__version__,
             "seeds": {"E1": SEED_E1, "within_design": "[20261011, 1, zlib.crc32(design_id)]", "calibration_bootstrap": SEED_CAL},
             "M": M_REASSIGN, "B": B_BOOT, "no_significance_label": True}
    result = OrderedDict([
        ("D", D_n), ("n", n), ("F", F), ("coverage_codes", codes),
        ("minimums_printed_beside_n", {"min_n": MIN_N, "min_F": MIN_F, "n_ge_min": n >= MIN_N, "F_ge_min": F >= MIN_F,
                                       "note": "EX-1: printed beside n, gate nothing",
                                       "planning_detectable_mean_Z_2.8_over_sqrt_n": 2.8 / math.sqrt(n) if n else None}),
        ("primary_literal_null", dict(primary, Z_distribution=z_block(ev_rows, "primary"), permutation_p=p_counts(ev_rows, "primary"))),
        ("S1_size_matched_null", dict(s1, Z_distribution=z_block(ev_rows, "S1"), permutation_p=p_counts(ev_rows, "S1"),
                                      designs_with_E_nonempty=sum(1 for r in ev_rows if r["E_n"] > 0))),
        ("calibration_pseudo_assignments", cal),
        ("monte_carlo_stability", stability),
        ("descriptive", {"by_live_status": {"estimated": z_block(est, "primary"), "not_evaluable": z_block(non, "primary")},
                         "by_live_status_S1": {"estimated": z_block(est, "S1"), "not_evaluable": z_block(non, "S1")},
                         "by_s3_support": {"passed": z_block(sup_pass, "primary"), "stopped": z_block(sup_stop, "primary")},
                         "signed_standardised_gaps": covariate_gaps(ev_rows),
                         "note": "signed gap = (mean over T states - mean over R states) / SD over U at the reference year; no interval"}),
        ("N1b", {"run": False, "estimated_eval_designs_with_ge3_cohorts": len(n1b_candidates), "minimum_n": N1B_MIN_N,
                 "reason": "frozen minimum (n >= 30 with >= 3 supported cohorts) not reached; cohort ATTs not read"}),
    ])
    checks = {"family_mismatch_live_vs_sets_file": family_mismatch, "live_status_mismatch_vs_sets_file": status_mismatch,
              "reference_year_differs_from_sets_file": ystar_differs,
              "dc_rule": {"eval_designs_with_dc_in_U": sum(1 for r in ev_rows if r["dc_in_U"]),
                          "in_T": sum(1 for r in ev_rows if r["dc_in_T"]), "in_R": sum(1 for r in ev_rows if r["dc_in_R"]),
                          "sets_file_eval_count": sum(1 for d in sets["designs"] if d.get("eval_n1_spec")), "eval_count_after_dc_rule": n},
              "designs_with_mixed_cohorts": {r["design_id"]: r["mixed_cohorts"] for r in ev_rows if r["mixed_cohorts"]}}
    payload = clean(OrderedDict([
        ("schema_version", "l3-meta-n1-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {rel(p): {"sha256": s, "bytes": p.stat().st_size} for p, s in ((LIVE, live_sha), (SETS, sets_sha), (COV_PATH, cov_sha), (AMEND, amend_sha))}),
        ("question", OrderedDict([("N1", result)])), ("checks", checks), ("designs", rows),
        ("audit", {"l2_files_written": 0, "estimate_fields_read": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():  # a version folder is written once
            sys.exit(f"{rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(LIVE, copy)
        assert sha256_file(copy) == live_sha
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_n1.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code_sha256"], "live_set_sha256": live_sha, "amendment_sha256": amend_sha,
                                 "results_file": rel(target), "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": stamp, "N1": payload["question"]["N1"], "checks": payload["checks"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
