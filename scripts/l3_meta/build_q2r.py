#!/usr/bin/env python3
"""L3 meta, EXPLORATORY question Q2-R (rule id EX-1), version Q2R-V1: are published effects larger than our independent re-estimates?

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_Q2R_V1.md (hash and T_amend in PREREG_AMEND_20261016_Q2R_V1_HASH.txt), written before this code
existed and before any Q2-R statistic was computed; PREREG_AMEND_20261012_EXPLORATORY.md (EX-1) applies: no confirmatory claim, no
multiplicity adjustment, no "significant" label, no licensed sentence.

Unit: one catalogue paper = the snapshot's `primary` (frozen independent first estimate against a pinned target).  Pair (p, o) = published
number and our number for that cell.  Gap: oriented raw gap d = a (p - o), a = sign(p); bins by the site's 10% rule (the comparator's tolerance);
log ratio L = ln(|p| / (a o)) for sign-agreeing pairs.  Primary D = (n_plus - n_minus) / n; interval = family-block bootstrap (B = 9,999,
seed 20261016) when n >= 20 pairs from F >= 20 families.

Reads: one snapshot file of data/replication/snapshot_v5 (bytes read once, hashed, parsed from those same bytes), the catalogue
data/replication/paper_catalog_v1.jsonl, the earlier immutable snapshots of that folder (trajectory) and the hash record.  Nothing under
data/replication, scripts/replication, scripts/l2_pipeline or data/l2_pipeline is written.  No network.  Standard library only.

Run:  python3 -I -B scripts/l3_meta/build_q2r.py [--dry-run] [--snapshot PATH --version K --out-dir DIR]
      version 1 (default) is pinned to snapshot_20261015T171023010535Z.json; a refresh is  --snapshot <newer snapshot> --version K.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, OrderedDict
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

RULE_ID = "EX-1"
QUESTION = "Q2-R"
DATA = ROOT / "data"
SNAP_DIR = DATA / "replication" / "snapshot_v5"
V1_SNAPSHOT = SNAP_DIR / "snapshot_20261015T171023010535Z.json"
CATALOG = DATA / "replication" / "paper_catalog_v1.jsonl"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2R_V1.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2R_V1_HASH.txt"
LEDGER = DATA / "l3_meta" / "q2r_ledger.jsonl"

SEED = 20261016                       # amendment 6.1
B_RESAMPLES = 9999                    # amendment 6.1
FLOOR_N, FLOOR_F = 20, 20             # amendment 6.2
MULTS = (0, 0.05, 0.10, 0.20, 0.50)   # amendment 5.7 (0.10 is the primary band)
PRIMARY_MULT = 0.10
PROBS = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)   # amendment 5.4
GRADES = ("TIGHT", "DIRECTIONAL", "NOT_CONSISTENT", "NOT_COMPARABLE")
NC_OK_REASON = "interval_unavailable_outside_point_tolerance"   # amendment 3.3
RUNGS = ("R1a", "R1b")                                           # amendment 3.4
TOP5 = ("AER", "QJE", "JPE", "REStud", "Econometrica")           # amendment 7.3
FIELD_BY_BATCH = {"B1": "labor_tax", "B2": "health", "B3": "crime_guns_traffic", "B4": "education_family_elections_other"}
AGE_CUT = 2015                                                   # amendment 7.3: 2015 or earlier | 2016 or later
MIN_ARM = 5
CODES = ["EVAL", "PRIMARY_INELIGIBLE", "RUNG_NOT_COUNTED", "NO_NUMBER", "ZERO_PUBLISHED", "GRADE_MISSING", "NC_OTHER_REASON",
         "CLOSED_TRIAGE", "WAITING_ACCESS", "NOT_STARTED", "IN_PROGRESS", "FINISHED_UNCOUNTED", "NO_PRIMARY_OTHER"]


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, tuples -> lists."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


NEVER_WRITTEN = ("data/replication", "scripts/replication", "scripts/l2_pipeline", "data/l2_pipeline")   # amendment 11


def is_safe_out_dir(out_dir):
    out = Path(out_dir).resolve()
    for t in NEVER_WRITTEN:
        base = (ROOT / t).resolve()
        if out == base or base in out.parents:
            return False
    return True


def parse_hash_record(path=AMEND_HASH):
    """sha256 and T_amend of the amendment and the input hashes listed in the record (amendment 11, 2.3)."""
    text = Path(path).read_text(encoding="utf-8")
    sha = re.search(r"^sha256:\s*([0-9a-f]{64})", text, re.M)
    t_amend = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    return {"sha256": sha.group(1) if sha else None, "t_amend_utc": t_amend.group(1) if t_amend else None, "listed": listed}


# ---------------------------------------------------------------------------------------------
# the gap (amendment 4) -- pure functions, unit-tested in test_build_q2r.py
# ---------------------------------------------------------------------------------------------
def dec(x):
    return Decimal(str(x))


def clean_se(se):
    return se if is_num(se) and se >= 0 else 0


def tolerance_at(p, se, mult):
    """The comparator's tolerance (grade_existing.tolerance): mult * max(|p|, se), Decimal arithmetic, returned as a float."""
    return float(Decimal(str(mult)) * max(abs(dec(p)), dec(clean_se(se))))


def orient(p):
    return 1 if p > 0 else -1


def bin_at(p, se, o, mult):
    """+1 published larger than ours beyond the band, 0 within the band (inclusive), -1 ours larger beyond the band."""
    d = (dec(p) - dec(o)) * orient(p)
    if abs(d) <= dec(tolerance_at(p, se, mult)):
        return 0
    return 1 if d > 0 else -1


def pair_numbers(p, se, o):
    """Amendment 4.1-4.6 for one pair (p != 0)."""
    a = orient(p)
    se0 = clean_se(se)
    P = abs(p)
    O = a * o
    dd = (dec(p) - dec(o)) * a
    base = max(P, se0)
    tau = tolerance_at(p, se0, PRIMARY_MULT)
    c = bin_at(p, se0, o, PRIMARY_MULT)
    u = 1 if O > 0 else 0
    return OrderedDict([
        ("p", p), ("se_pub", se0), ("o", o), ("a", a), ("P", P), ("O", O), ("d", float(dd)), ("d_sign", 1 if dd > 0 else (-1 if dd < 0 else 0)),
        ("base", base), ("tau", tau), ("c", c), ("g", float(dd) / base), ("u", u), ("L", math.log(P / O) if u else None)])


def grade_numeric(pr):
    return pr.get("grade_numeric") or (pr.get("numeric_checks") or {}).get("grade")


def reason_codes(pr):
    return list((pr.get("numeric_checks") or {}).get("reason_codes") or [])


def classify(paper):
    """Amendment 3.2, 3.3, 3.6: the coverage code of one catalogue paper (EVAL = in E)."""
    pr = paper.get("primary")
    if not pr:
        st = paper.get("stage") or ""
        if st.startswith("closed_"):
            return "CLOSED_TRIAGE"
        if st.startswith("waiting_"):
            return "WAITING_ACCESS"
        return {"not_started": "NOT_STARTED", "in_progress": "IN_PROGRESS", "finished_uncounted": "FINISHED_UNCOUNTED"}.get(st, "NO_PRIMARY_OTHER")
    if not (pr.get("code_origin") == "independent" and pr.get("frozen") is True and pr.get("eligible_for_primary") is True
            and pr.get("target_fixed_before_estimation") is True):
        return "PRIMARY_INELIGIBLE"
    if pr.get("rung") not in RUNGS:
        return "RUNG_NOT_COUNTED"
    o = (pr.get("ours") or {}).get("point")
    p = (pr.get("published") or {}).get("point")
    if not (is_num(o) and is_num(p)):
        return "NO_NUMBER"
    if p == 0:
        return "ZERO_PUBLISHED"
    g = grade_numeric(pr)
    if g not in GRADES:
        return "GRADE_MISSING"
    if g == "NOT_COMPARABLE" and set(reason_codes(pr)) != {NC_OK_REASON}:
        return "NC_OTHER_REASON"
    return "EVAL"


def family_of(paper, cat):
    fam = (cat or {}).get("policy_family")
    return fam if fam else "__own__:" + paper["paper_id"]


def field_of(sources):
    for s in sources or []:
        if s != "GAP":
            return FIELD_BY_BATCH.get(s, "other")
    return "other"


def journal_group(venue_strong):
    if not venue_strong:
        return "not_listed"
    return "top5" if venue_strong in TOP5 else "other_listed"


def age_group(year):
    if not is_num(year):
        return "unknown"
    return "published_2015_or_earlier" if year <= AGE_CUT else "published_2016_or_later"


def build_pair(paper, cat, use_pinned=False):
    """The pair of an EVAL paper with all its descriptive fields.  use_pinned: the original pinned value instead of the comparison value (7.1 f)."""
    pr = paper["primary"]
    pub = pr["published"]
    p, se = pub.get("point"), pub.get("se")
    if use_pinned:
        tg = pr.get("target")
        if isinstance(tg, dict) and is_num(tg.get("point")) and tg.get("point") != 0:
            p, se = tg["point"], tg.get("se")
    o = pr["ours"]["point"]
    row = OrderedDict([
        ("paper_id", paper["paper_id"]), ("slug", paper.get("slug")), ("year", paper.get("year")), ("venue", paper.get("venue")),
        ("stage", paper.get("stage")), ("certified", paper.get("stage") == "confirmed"), ("rung", pr.get("rung")),
        ("spec_completeness", pr.get("spec_completeness")), ("grade_numeric", grade_numeric(pr)), ("grade_gated", pr.get("grade")),
        ("gate_reasons", list(pr.get("gate_reasons") or [])), ("target_verified", pr.get("target_verified") is True),
        ("se_ours", clean_se(pr["ours"].get("se"))), ("recorded_tolerance", (pr.get("numeric_checks") or {}).get("absolute_tolerance")),
        ("family", family_of(paper, cat)), ("sources", list((cat or {}).get("sources") or [])), ("venue_strong", (cat or {}).get("venue_strong")),
        ("catalogue_missing", cat is None)])
    row["field"] = field_of(row["sources"])
    row["journal_group"] = journal_group(row["venue_strong"])
    row["age_group"] = age_group(paper.get("year"))
    row.update(pair_numbers(p, se, o))
    row["z_pub"] = (abs(p) / row["se_pub"]) if row["se_pub"] > 0 else None
    return row


def evaluate_snapshot(snap, catalog_by_id):
    """Coverage rows over all papers of a snapshot and the pairs of E (sorted by paper_id)."""
    rows, pairs = [], []
    for paper in sorted(snap["papers"], key=lambda x: x["paper_id"]):
        code = classify(paper)
        cat = catalog_by_id.get(paper["paper_id"])
        rows.append(OrderedDict([("paper_id", paper["paper_id"]), ("slug", paper.get("slug")), ("stage", paper.get("stage")), ("code", code),
                                 ("family", family_of(paper, cat)), ("grade_numeric", grade_numeric(paper["primary"]) if paper.get("primary") else None),
                                 ("reason_codes", "|".join(reason_codes(paper["primary"])) if paper.get("primary") else "")]))
        if code == "EVAL":
            pairs.append(build_pair(paper, cat))
    return rows, pairs


def fingerprint(pairs):
    """Amendment 8.2: sha256 over the fields whose change triggers a new version."""
    items = sorted([x["paper_id"], x["p"], x["se_pub"], x["o"], x["stage"], sorted(x["gate_reasons"])] for x in pairs)
    return sha256_bytes(json.dumps(items, sort_keys=True, separators=(",", ":")).encode("utf-8"))


# ---------------------------------------------------------------------------------------------
# statistics (amendment 5)
# ---------------------------------------------------------------------------------------------
def quantile7(xs_sorted, q):
    """Linear interpolation, type 7."""
    m = len(xs_sorted)
    if m == 0:
        return None
    h = (m - 1) * q
    j = int(math.floor(h))
    if j >= m - 1:
        return xs_sorted[-1]
    return xs_sorted[j] + (h - j) * (xs_sorted[j + 1] - xs_sorted[j])


def quantile_table(xs):
    xs = sorted(xs)
    return OrderedDict((str(int(round(q * 100))), quantile7(xs, q)) for q in PROBS)


def median(xs):
    return quantile7(sorted(xs), 0.5)


def _pmf(k, n, p):
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.exp(math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * math.log(p) + (n - k) * math.log1p(-p))


def _sf_ge(k, n, p):
    return sum(_pmf(j, n, p) for j in range(max(k, 0), n + 1))


def _cdf_le(k, n, p):
    return sum(_pmf(j, n, p) for j in range(0, min(k, n) + 1))


def _bisect(f, increasing):
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if (f(mid) < 0) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def cp_interval(k, n, alpha=0.05):
    """Exact Clopper-Pearson two-sided interval for k successes in n trials."""
    if n <= 0:
        return None
    lo = 0.0 if k == 0 else _bisect(lambda p: _sf_ge(k, n, p) - alpha / 2, True)
    hi = 1.0 if k == n else _bisect(lambda p: _cdf_le(k, n, p) - alpha / 2, False)
    return [lo, hi]


def sign_test_p(k, n):
    """Exact two-sided binomial p for p0 = 0.5 (amendment 5.3)."""
    if n <= 0:
        return None
    m = max(k, n - k)
    tail = sum(math.comb(n, j) for j in range(m, n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def planning_half_widths(sizes=(10, 20, 40, 80)):
    out = OrderedDict()
    for n in sizes:
        lo, hi = cp_interval(n // 2, n)
        out[str(n)] = (hi - lo) / 2
    return out


def floor_met(n, F):
    return n >= FLOOR_N and F >= FLOOR_F


def bins_of(pairs, mult=PRIMARY_MULT):
    cs = [x["c"] if mult == PRIMARY_MULT else bin_at(x["p"], x["se_pub"], x["o"], mult) for x in pairs]
    n_plus, n_minus = cs.count(1), cs.count(-1)
    n = len(cs)
    return OrderedDict([("n_plus", n_plus), ("n_zero", n - n_plus - n_minus), ("n_minus", n_minus),
                        ("D", (n_plus - n_minus) / n if n else None)])


def summarize(pairs):
    """Descriptive statistics of a set (amendment 5.1-5.7); no interval."""
    n = len(pairs)
    fams = sorted({x["family"] for x in pairs})
    b = bins_of(pairs)
    n_dec = b["n_plus"] + b["n_minus"]
    agree = [x for x in pairs if x["u"]]
    Ls = [x["L"] for x in agree]
    out = OrderedDict()
    out["n"], out["F"] = n, len(fams)
    out["bins"] = OrderedDict([("n_plus", b["n_plus"]), ("n_zero", b["n_zero"]), ("n_minus", b["n_minus"])])
    out["shares"] = OrderedDict([("share_plus", b["n_plus"] / n if n else None), ("share_zero", b["n_zero"] / n if n else None),
                                 ("share_minus", b["n_minus"] / n if n else None)])
    out["D"] = b["D"]
    out["n_dec"] = n_dec
    out["P_dec"] = b["n_plus"] / n_dec if n_dec else None
    out["strict"] = OrderedDict([("n_published_larger", sum(1 for x in pairs if x["d_sign"] > 0)), ("n_ours_larger", sum(1 for x in pairs if x["d_sign"] < 0)),
                                 ("n_exact_ties", sum(1 for x in pairs if x["d_sign"] == 0))])
    out["strict"]["share_published_larger"] = out["strict"]["n_published_larger"] / n if n else None
    out["strict"]["share_ours_larger"] = out["strict"]["n_ours_larger"] / n if n else None
    out["sign_agreement"] = OrderedDict([("n_agree", len(agree)), ("n_flip_or_zero", n - len(agree)), ("share", len(agree) / n if n else None)])
    out["log_ratio"] = OrderedDict([
        ("n", len(Ls)), ("n_flip_or_zero", n - len(Ls)), ("quantiles", quantile_table(Ls) if Ls else None), ("mean", mean(Ls)), ("median", median(Ls) if Ls else None),
        ("ratio_ours_to_published_geometric", math.exp(-mean(Ls)) if Ls else None)])
    out["relative_gap_g_quantiles"] = quantile_table([x["g"] for x in pairs]) if pairs else None
    return out


def band_curve(pairs):
    rows = []
    for m in MULTS:
        b = bins_of(pairs, m)
        rows.append(OrderedDict([("band_multiplier", m), ("n_plus", b["n_plus"]), ("n_zero", b["n_zero"]), ("n_minus", b["n_minus"]), ("D", b["D"])]))
    return rows


STAT_KEYS = ("D", "share_plus", "share_zero", "share_minus", "strict_share_published_larger", "sign_agreement", "mean_L", "median_L", "ratio_ours_to_published")


def percentile_interval(sorted_vals):
    m = len(sorted_vals)
    if m == 0:
        return None
    lo = max(1, min(m, (25 * (m + 1)) // 1000))
    hi = max(1, min(m, -((-975 * (m + 1)) // 1000)))
    return [sorted_vals[lo - 1], sorted_vals[hi - 1]]


def family_bootstrap(pairs, B=B_RESAMPLES, seed=SEED):
    """Amendment 6.1: family-block percentile bootstrap.  Families sorted by name, pairs by paper_id; each resample draws F families with
    replacement as int(r F), r = random.Random(seed).random(); the pooled pairs are re-summarised."""
    order = sorted(range(len(pairs)), key=lambda i: pairs[i]["paper_id"])
    fams = sorted({pairs[i]["family"] for i in order})
    F = len(fams)
    members = {f: [i for i in order if pairs[i]["family"] == f] for f in fams}
    c = [x["c"] for x in pairs]
    ds = [x["d_sign"] for x in pairs]
    u = [x["u"] for x in pairs]
    L = [x["L"] for x in pairs]
    rng = random.Random(seed)
    draws = {k: [] for k in STAT_KEYS}
    for _ in range(B):
        idx = []
        for _j in range(F):
            idx.extend(members[fams[int(rng.random() * F)]])
        n = len(idx)
        n_plus = sum(1 for i in idx if c[i] == 1)
        n_minus = sum(1 for i in idx if c[i] == -1)
        draws["D"].append((n_plus - n_minus) / n)
        draws["share_plus"].append(n_plus / n)
        draws["share_minus"].append(n_minus / n)
        draws["share_zero"].append((n - n_plus - n_minus) / n)
        draws["strict_share_published_larger"].append(sum(1 for i in idx if ds[i] > 0) / n)
        Ls = [L[i] for i in idx if u[i]]
        draws["sign_agreement"].append(len(Ls) / n)
        if Ls:
            mu = sum(Ls) / len(Ls)
            draws["mean_L"].append(mu)
            draws["median_L"].append(median(Ls))
            draws["ratio_ours_to_published"].append(math.exp(-mu))
    out = OrderedDict()
    for k in STAT_KEYS:
        out[k] = OrderedDict([("interval", percentile_interval(sorted(draws[k]))), ("m_resamples_used", len(draws[k]))])
    return out


def reading(D_interval, n, F):
    """Amendment 6.3."""
    if not floor_met(n, F) or D_interval is None:
        return "descriptive only"
    if D_interval[0] > 0:
        return "published larger"
    if D_interval[1] < 0:
        return "ours larger"
    return "no asymmetry seen"


def analyze_set(pairs, with_engine=True):
    """summarize + (when the floor is met) the intervals, the exact interval and p of 5.3/5.5 (amendment 6.2)."""
    out = summarize(pairs)
    met = floor_met(out["n"], out["F"])
    out["floor"] = OrderedDict([("n_min", FLOOR_N), ("F_min", FLOOR_F), ("met", met)])
    if with_engine and met:
        out["bootstrap"] = family_bootstrap(pairs)
        k, n = out["bins"]["n_plus"], out["n_dec"]
        out["direction_among_departures"] = OrderedDict([
            ("n_published_larger", k), ("n_dec", n), ("P_dec", out["P_dec"]), ("cp95", cp_interval(k, n) if n else None),
            ("sign_test_p_two_sided", sign_test_p(k, n) if n else None)])
        out["sign_agreement"]["cp95"] = cp_interval(out["sign_agreement"]["n_agree"], out["n"])
        out["reading"] = reading(out["bootstrap"]["D"]["interval"], out["n"], out["F"])
    else:
        out["reading"] = "descriptive only"
    return out


def family_weighted(pairs):
    """Amendment 7.1 (g): each family's mean counts once."""
    by = {}
    for x in pairs:
        by.setdefault(x["family"], []).append(x)
    ds = [mean(x["c"] for x in v) for v in by.values()]
    ml = [mean(x["L"] for x in v if x["u"]) for v in by.values() if any(x["u"] for x in v)]
    return OrderedDict([("F", len(by)), ("D_family_weighted", mean(ds)), ("mean_L_family_weighted", mean(ml) if ml else None), ("families_with_L", len(ml))])


def leave_one_family_out(pairs):
    """Amendment 7.2."""
    fams = sorted({x["family"] for x in pairs})
    rows = []
    if len(fams) < 2:
        return OrderedDict([("rows", rows), ("D_range", None), ("mean_L_range", None)])
    for f in fams:
        rest = [x for x in pairs if x["family"] != f]
        s = summarize(rest)
        rows.append(OrderedDict([("dropped_family", f), ("n_dropped", len(pairs) - len(rest)), ("n", s["n"]), ("D", s["D"]), ("mean_L", s["log_ratio"]["mean"])]))
    dmin, dmax = min(rows, key=lambda r: r["D"]), max(rows, key=lambda r: r["D"])
    withL = [r for r in rows if r["mean_L"] is not None]
    lmin = min(withL, key=lambda r: r["mean_L"]) if withL else None
    lmax = max(withL, key=lambda r: r["mean_L"]) if withL else None
    return OrderedDict([("rows", rows), ("D_range", OrderedDict([("min", dmin["D"]), ("min_when_dropped", dmin["dropped_family"]), ("max", dmax["D"]), ("max_when_dropped", dmax["dropped_family"])])),
                        ("mean_L_range", OrderedDict([("min", lmin["mean_L"]), ("min_when_dropped", lmin["dropped_family"]), ("max", lmax["mean_L"]), ("max_when_dropped", lmax["dropped_family"])]) if withL else None)])


def split_table(pairs, key, arms):
    """Amendment 7.3: per arm n, bins, mean L over sign-agreeing pairs, flagged too few when n < 5.  No test."""
    out = OrderedDict()
    for arm in arms:
        sub = [x for x in pairs if key(x) == arm]
        b = bins_of(sub)
        Ls = [x["L"] for x in sub if x["u"]]
        out[arm] = OrderedDict([("n", len(sub)), ("n_plus", b["n_plus"]), ("n_zero", b["n_zero"]), ("n_minus", b["n_minus"]), ("D", b["D"]),
                                ("n_sign_agree", len(Ls)), ("mean_L", mean(Ls)), ("median_L", median(Ls) if Ls else None), ("too_few", len(sub) < MIN_ARM)])
    extra = sorted({key(x) for x in pairs} - set(arms))
    if extra:
        out["__not_in_listed_arms__"] = extra
    return out


SPLITS = OrderedDict([
    ("rung", (lambda x: x["rung"], ["R1a", "R1b"])),
    ("specification_completeness", (lambda x: "complete" if x["spec_completeness"] == "complete" else "not_complete", ["complete", "not_complete"])),
    ("field_catalogue_batch", (lambda x: x["field"], ["labor_tax", "health", "crime_guns_traffic", "education_family_elections_other", "other"])),
    ("paper_age", (lambda x: x["age_group"], ["published_2015_or_earlier", "published_2016_or_later"])),
    ("journal_group", (lambda x: x["journal_group"], ["top5", "other_listed", "not_listed"])),
])

SETS = OrderedDict([
    ("a_without_not_comparable", lambda x: x["grade_numeric"] != "NOT_COMPARABLE"),
    ("b_target_verified", lambda x: x["target_verified"]),
    ("c_gate_clean", lambda x: not x["gate_reasons"]),
    ("d_stage_matched_or_confirmed", lambda x: x["stage"] in ("matched", "confirmed")),
    ("e_confirmed_only", lambda x: x["stage"] == "confirmed"),
])


def core_row(pairs):
    s = summarize(pairs)
    return OrderedDict([("n", s["n"]), ("F", s["F"]), ("n_plus", s["bins"]["n_plus"]), ("n_zero", s["bins"]["n_zero"]), ("n_minus", s["bins"]["n_minus"]),
                        ("D", s["D"]), ("P_dec", s["P_dec"]), ("mean_L", s["log_ratio"]["mean"]), ("median_L", s["log_ratio"]["median"]),
                        ("sign_agreement", s["sign_agreement"]["share"]), ("fingerprint", fingerprint(pairs))])


def trajectory(paths, catalog_by_id, main_as_of, record_listed):
    """Amendment 7.5: the pure functions without the engine on each retained immutable snapshot with as_of up to the main one."""
    rows, prev = [], None
    for path in paths:
        raw = Path(path).read_bytes()
        sha = sha256_bytes(raw)
        snap = json.loads(raw)
        if snap.get("schema_version") != 5 or not snap.get("as_of") or snap["as_of"] > main_as_of:
            continue
        listed = record_listed.get(rel(path))
        _, pairs = evaluate_snapshot(snap, catalog_by_id)
        r = core_row(pairs)
        r.update(OrderedDict([("file", rel(path)), ("sha256", sha), ("as_of", snap["as_of"]), ("papers_with_primary", sum(1 for p in snap["papers"] if p.get("primary"))),
                              ("in_hash_record", listed is not None), ("record_matches", (listed == sha) if listed is not None else None),
                              ("new_state", r["fingerprint"] != prev)]))
        prev = r["fingerprint"]
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------------------------
def run_checks(snap, rows, pairs, catalog_by_id):
    issues_bin, issues_tol, issues_run = [], [], []
    for x in pairs:
        if (x["c"] == 0) != (x["grade_numeric"] == "TIGHT"):
            issues_bin.append(x["paper_id"])
        rt = x.get("recorded_tolerance")
        if is_num(rt) and abs(rt - x["tau"]) > 1e-12 * max(1.0, abs(rt)):
            issues_tol.append(x["paper_id"])
    by_id = {p["paper_id"]: p for p in snap["papers"]}
    for x in pairs:
        paper = by_id[x["paper_id"]]
        runs = {r.get("run_id"): r for r in paper.get("runs") or []}
        pr = paper["primary"]
        r = runs.get(paper.get("primary_run_id"))
        if r is None or (r.get("ours") or {}).get("point") != pr["ours"]["point"] or r.get("code_origin") != "independent":
            issues_run.append(x["paper_id"])
    ids = [p["paper_id"] for p in snap["papers"]]
    return OrderedDict([
        ("papers", len(ids)), ("unique_paper_ids", len(set(ids))), ("sum_stage_counts", sum((snap.get("stage_counts") or {}).values())),
        ("catalogue_join_complete", all(i in catalog_by_id for i in ids)),
        ("pairs_where_band_and_numeric_grade_disagree", issues_bin), ("pairs_where_recomputed_tolerance_differs_from_recorded", issues_tol),
        ("pairs_where_primary_run_missing_or_not_independent", issues_run),
        ("all_pairs_independent_frozen_rung_ok", all(classify(by_id[x["paper_id"]]) == "EVAL" for x in pairs)),
        ("coverage_codes_sum_to_papers", sum(Counter(r["code"] for r in rows).values()) == len(ids))])


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("|".join(v) if isinstance(v, list) else v) for k, v in r.items()})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--snapshot", default=None, help="snapshot file to read (version 1: only the hashed one)")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--dump", default=None, help="with --dry-run only: write the full payload to this file (outside the guarded trees)")
    args = ap.parse_args(argv)
    if args.dump and (not args.dry_run or not is_safe_out_dir(Path(args.dump).resolve().parent)):
        sys.exit("--dump needs --dry-run and a path outside the guarded trees")
    version = args.version
    version_label = f"Q2R-V{version}"
    snap_path = Path(args.snapshot) if args.snapshot else V1_SNAPSHOT
    if args.snapshot and not snap_path.is_absolute() and not snap_path.exists():
        snap_path = ROOT / snap_path
    if version > 1 and not args.snapshot:
        sys.exit("version > 1 needs --snapshot <newer immutable snapshot file>")
    out_dir = Path(args.out_dir) if args.out_dir else DATA / "l3_meta" / f"q2r_v{version}"
    if not is_safe_out_dir(out_dir):
        sys.exit("--out-dir is inside a tree this question never writes (data/replication, scripts/replication, scripts/l2_pipeline, data/l2_pipeline): not run")

    # --- the hash record: the amendment, the frozen files, the catalogue and the registered snapshots must be as recorded
    rec = parse_hash_record()
    amend_sha = sha256_file(AMEND)
    if rec["sha256"] != amend_sha:
        sys.exit("amendment file differs from its hash record: not run")
    informational = {}
    for path, want in rec["listed"].items():
        p = ROOT / path
        if not p.exists():
            sys.exit(f"{path} listed in the hash record is missing: not run")
        got = sha256_file(p)
        enforced = path.startswith("docs/plans/l3-meta/PREREG_") or path == rel(CATALOG) or re.search(r"snapshot_v5/snapshot_[0-9TZ]+\.json$", path)
        if got != want:
            if enforced:
                sys.exit(f"{path} differs from the amendment record: not run")
            informational[path] = {"record": want, "now": got}
    # --- the snapshot: bytes read once, hashed, parsed from the same bytes
    snap_bytes = snap_path.read_bytes()
    snap_sha = sha256_bytes(snap_bytes)
    if version == 1 and (snap_path.resolve() != V1_SNAPSHOT.resolve() or snap_sha != rec["listed"].get(rel(V1_SNAPSHOT))):
        sys.exit("version 1 is pinned to the hashed snapshot file: not run")
    snap = json.loads(snap_bytes)
    if snap.get("schema_version") != 5 or snap.get("policy_id") != "independent_v5" or len(snap.get("papers") or []) != 503:
        sys.exit("snapshot schema, policy or roster differs from the amendment (schema 5, independent_v5, 503 papers): not run")
    cat_bytes = CATALOG.read_bytes()
    cat_sha = sha256_bytes(cat_bytes)
    if cat_sha != rec["listed"].get(rel(CATALOG)):
        sys.exit("catalogue differs from the amendment record: not run")
    catalog_by_id = {}
    for line in cat_bytes.decode("utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            catalog_by_id[r["paper_id"]] = r

    # --- coverage, pairs
    rows, pairs = evaluate_snapshot(snap, catalog_by_id)
    n, F = len(pairs), len({x["family"] for x in pairs})
    codes = OrderedDict((c, sum(1 for r in rows if r["code"] == c)) for c in CODES)
    primary = analyze_set(pairs)
    checks = run_checks(snap, rows, pairs, catalog_by_id)
    fp = fingerprint(pairs)

    # --- beside the primary
    sets = OrderedDict()
    for name, pred in SETS.items():
        sub = [x for x in pairs if pred(x)]
        sets[name] = analyze_set(sub)
    pinned_pairs = [build_pair(next(p for p in snap["papers"] if p["paper_id"] == x["paper_id"]), catalog_by_id.get(x["paper_id"]), use_pinned=True) for x in pairs]
    pinned = analyze_set(pinned_pairs)
    pinned["n_pairs_whose_value_differs_from_comparison_value"] = sum(1 for a, b in zip(pairs, pinned_pairs) if (a["p"], a["se_pub"]) != (b["p"], b["se_pub"]))
    sets["f_original_pinned_value"] = pinned
    sets["g_family_weighted"] = family_weighted(pairs)
    splits = OrderedDict((name, split_table(pairs, key, arms)) for name, (key, arms) in SPLITS.items())
    decisive = sorted([x for x in pairs if x["c"] != 0], key=lambda x: -x["g"])
    certified = [x for x in pairs if x["certified"]]

    # --- trajectory (7.5)
    snap_files = sorted(glob.glob(str(SNAP_DIR / "snapshot_*.json")))
    traj = trajectory(snap_files, catalog_by_id, snap["as_of"], rec["listed"])
    if snap_path.resolve().parent != SNAP_DIR.resolve() and not any(t["sha256"] == snap_sha for t in traj):
        r = core_row(pairs)
        r.update(OrderedDict([("file", rel(snap_path)), ("sha256", snap_sha), ("as_of", snap["as_of"]), ("papers_with_primary", sum(1 for p in snap["papers"] if p.get("primary"))),
                              ("in_hash_record", False), ("record_matches", None), ("new_state", True)]))
        traj.append(r)

    me = Path(__file__).resolve()
    stamp = OrderedDict([
        ("rule_id", RULE_ID), ("question", QUESTION), ("version", version_label), ("run_utc", utc_now()), ("snapshot_as_of", snap["as_of"]),
        ("snapshot_file", rel(snap_path)), ("snapshot_sha256", snap_sha), ("catalogue_sha256", cat_sha), ("code_sha256", sha256_file(me)), ("code", rel(me)),
        ("amendment_sha256", amend_sha), ("t_amend_utc", rec["t_amend_utc"]), ("D", len(rows)), ("n", n), ("F", F), ("coverage_codes", codes),
        ("pairs_fingerprint_sha256", fp), ("seed", SEED), ("B", B_RESAMPLES), ("floor", {"n": FLOOR_N, "F": FLOOR_F}), ("python", sys.version.split()[0]),
        ("no_significance_label", True)])
    payload = clean(OrderedDict([
        ("schema_version", "l3-meta-q2r-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim; the set E is the accessible, finished part of the 503 papers"),
        ("stamp", stamp),
        ("inputs", {rel(snap_path): {"sha256": snap_sha, "bytes": len(snap_bytes)}, rel(CATALOG): {"sha256": cat_sha, "bytes": len(cat_bytes)},
                    rel(AMEND): {"sha256": amend_sha, "bytes": AMEND.stat().st_size}}),
        ("coverage", OrderedDict([("D", len(rows)), ("codes", codes), ("stage_counts_in_snapshot", snap.get("stage_counts"))])),
        ("question", OrderedDict([("Q2R", OrderedDict([("primary", primary), ("band_curve", band_curve(pairs)), ("planning_half_width_cp95_at_half", planning_half_widths()),
                                                         ("decisive_pairs_by_g_desc", [OrderedDict((k, x[k]) for k in ("paper_id", "slug", "year", "rung", "grade_numeric", "stage", "p", "o", "se_pub", "d", "g", "c", "u", "L", "family")) for x in decisive])]))])),
        ("beside", OrderedDict([("sets", sets), ("leave_one_family_out", leave_one_family_out(pairs)), ("splits", splits),
                                ("certified_pairs", [OrderedDict((k, x[k]) for k in ("paper_id", "slug", "year", "rung", "p", "o", "d", "g", "c", "L")) for x in certified])])),
        ("trajectory", traj),
        ("checks", OrderedDict([("data_checks", checks), ("listed_inputs_changed_but_not_enforced", informational)])),
        ("pairs", pairs),
        ("audit", {"files_written_under_data_replication": 0, "files_written_under_scripts_replication": 0, "l2_trees_opened": 0, "network": False})]))

    if not args.dry_run:
        results = out_dir / f"RESULTS_Q2R_V{version}.json"
        if results.exists():
            sys.exit(f"{rel(results)} exists: not overwritten (use --version/--out-dir for a new version)")
        out_dir.mkdir(parents=True, exist_ok=True)
        snap_copy, cat_copy = out_dir / "snapshot_used.json", out_dir / "paper_catalog_used.jsonl"
        snap_copy.write_bytes(snap_bytes)
        cat_copy.write_bytes(cat_bytes)
        assert sha256_file(snap_copy) == snap_sha and sha256_file(cat_copy) == cat_sha
        write_csv(out_dir / f"Q2R_PAIRS_V{version}.csv",
                  ["paper_id", "slug", "year", "venue", "venue_strong", "journal_group", "field", "age_group", "family", "rung", "spec_completeness", "stage", "certified",
                   "grade_numeric", "grade_gated", "gate_reasons", "target_verified", "p", "se_pub", "o", "se_ours", "a", "P", "O", "d", "base", "tau", "c", "g", "u", "L", "z_pub"], pairs)
        write_csv(out_dir / f"Q2R_COVERAGE_V{version}.csv", ["paper_id", "slug", "stage", "code", "family", "grade_numeric", "reason_codes"], rows)
        write_csv(out_dir / f"Q2R_TRAJECTORY_V{version}.csv",
                  ["as_of", "file", "sha256", "papers_with_primary", "n", "F", "n_plus", "n_zero", "n_minus", "D", "P_dec", "mean_L", "median_L", "sign_agreement", "fingerprint", "new_state", "in_hash_record", "record_matches"], traj)
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        results.write_text(text, encoding="utf-8")
        results_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_q2r.py " + " ".join(argv if argv is not None else sys.argv[1:]), "code_sha256": stamp["code_sha256"],
                                 "snapshot_sha256": snap_sha, "amendment_sha256": amend_sha, "results_file": rel(results), "results_sha256": results_sha, "exit": 0}) + "\n")
        bs = (primary.get("bootstrap") or {})
        ledger_line = clean(OrderedDict([
            ("question", QUESTION), ("version", version_label), ("run_utc", stamp["run_utc"]), ("as_of", snap["as_of"]), ("snapshot_sha256", snap_sha), ("code_sha256", stamp["code_sha256"]),
            ("amendment_sha256", amend_sha), ("n", n), ("F", F), ("floor_met", primary["floor"]["met"]), ("bins", primary["bins"]), ("D", primary["D"]),
            ("D_interval", (bs.get("D") or {}).get("interval")), ("mean_L", primary["log_ratio"]["mean"]), ("mean_L_interval", (bs.get("mean_L") or {}).get("interval")),
            ("reading", primary["reading"]), ("pairs_fingerprint_sha256", fp), ("results_file", rel(results)), ("results_sha256", results_sha)]))
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ledger_line, ensure_ascii=False) + "\n")
    if args.dump:
        Path(args.dump).write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    brief = OrderedDict([("stamp", payload["stamp"]), ("primary", payload["question"]["Q2R"]["primary"]), ("checks", payload["checks"])])
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {rel(out_dir)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
