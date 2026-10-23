"""Shared helpers of the four 'registered later' L3 questions M3, M6, M7, X5 (rule id EX-1, exploratory).

Pure functions plus thin loaders.  The loaders read the sweep and reference files of refresh 20261018_r1 (identical in content to 20261017_r4) and the P5
reference files of R0 (SELREF P5 for the 89 original series, r0_v1 P5 for the 55 extension series).  Nothing here computes a result statistic.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

REFRESH = ROOT / "data/l3_meta/refresh/20261018_r1"
SETS = ROOT / "data/l3_meta/n1_sets_v2/n1_sets_v2.json"
GRID = ROOT / "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv"
NC_CELLS = REFRESH / "d2/NC_CELLS_D2_V1.csv"
LIVE = REFRESH / "live/l2_set_live.json"
D10 = ROOT / "data/l3_meta/d4_d10_v1/D10_PAIRS_V1.csv"
CAL = REFRESH / "cal/RESULTS_CAL_V1.json"
KAPPA_HAT = 1.3153711626157576
MDE_FACTOR = 2.8
DROP_FROM_CORE = ("fbi_ucr_property_crime_rate", "fbi_ucr_violent_crime_rate",
                  "dol_eta5159_state_ui_weeks_compensated", "dol_eta5159_state_ui_first_payments",
                  "dol_eta5159_state_ui_continued_weeks_claimed")


def sha256_file(p):
    import hashlib
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


def verify_records(rec, min_records=1):
    """(problems, number of records) for a hash record and its numbered supplements; a later record's line for the same path replaces an earlier one."""
    rec = Path(rec)
    merged = {}
    recs = [rec] + sorted(rec.parent.glob(rec.stem + "_S*.txt"), key=lambda p: int(re.search(r"_S(\d+)\.txt$", p.name).group(1)))
    n = 0
    for r in recs:
        if r.is_file():
            n += 1
            for p, h in parse_pins(r).items():
                merged[p] = (h, r.name)
    problems = []
    for p, (h, name) in merged.items():
        if not (ROOT / p).is_file():
            problems.append(f"{p} (in {name}) is missing")
        elif sha256_file(ROOT / p) != h:
            problems.append(f"{p} differs from {name}")
    if n < min_records:
        problems.append(f"only {n} hash records, {min_records} required (the code pinning supplement is missing)")
    return problems, n


def require_records(rec, min_records):
    problems, n = verify_records(rec, min_records)
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    return n


# ---------------------------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------------------------
def load_union(ref, refresh=REFRESH):
    import build_r0 as B0
    return B0.load_union(ref, refresh, True)


def live_families(refresh=REFRESH):
    return {x["design_id"]: x["policy_family"] for x in json.loads((Path(refresh) / "live/l2_set_live.json").read_text())["designs"]}


def design_groups(refresh=REFRESH):
    out = OrderedDict()
    with open(Path(refresh) / "d2/NC_CELLS_D2_V1.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["design_id"]] = r["policy_group"]
    return out


def grid_codes(path=GRID):
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[(r["policy_group"], r["outcome_family"])] = r["final_code"]
    return out


def design_objects(ref, refresh=REFRESH):
    """(designs {id: Design}, skipped {id: reason}, union arrays) for a reference ('P5' or 'U'), union of the 89 original and 55 extension series."""
    import build_d1 as D1M
    import build_d4_d10 as D4M
    import build_r0 as B0
    U = B0.load_union(ref, refresh, True)
    designs, skipped = B0.build_designs(U, live_families(refresh), D1M, D4M)
    return designs, skipped, U


# ---------------------------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------------------------
def clean(o):
    """JSON-safe copy (NaN and inf become None)."""
    if isinstance(o, dict):
        return OrderedDict((str(k), clean(v)) for k, v in o.items())
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if np.isfinite(v) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def pct(a, q=(2.5, 97.5)):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return [float(x) for x in np.percentile(a, q)] if a.size else [None, None]


def one_sided_p_ge(obs, null):
    null = np.asarray(null, float)
    null = null[np.isfinite(null)]
    return float((1 + np.sum(null >= obs)) / (len(null) + 1))


def block_multiplicity(block_of, n_blocks, rng):
    """Bootstrap multiplicity of each unit: blocks resampled with replacement; block_of is an int array (unit -> block)."""
    cnt = np.bincount(rng.integers(0, n_blocks, size=n_blocks), minlength=n_blocks)
    return cnt[np.asarray(block_of)].astype(float)


def wls_fit(X, y, w):
    """Weighted least squares coefficients (None if the normal matrix is singular or the weight is zero)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    w = np.asarray(w, float)
    keep = w > 0
    if keep.sum() < X.shape[1] + 1:
        return None
    Xk, yk, wk = X[keep], y[keep], w[keep]
    A = Xk.T @ (Xk * wk[:, None])
    try:
        if np.linalg.cond(A) > 1e12:
            return None
        return np.linalg.solve(A, Xk.T @ (wk * yk))
    except np.linalg.LinAlgError:
        return None


def jaccard_bool(a, b):
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0
