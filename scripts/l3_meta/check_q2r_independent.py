#!/usr/bin/env python3
"""Independent recomputation of Q2-R (plain loops, standard library only, NO import from build_q2r.py).

Re-derives from the raw files named in the results stamp: (1) the primary estimate of every one of the 503 papers from `runs[]` (the V3.2
selection rule written again here: eligible, frozen, independent, not an audit; one candidate, else the earliest by first-estimate time, ties
unresolved) and compares it with the snapshot's `primary`; (2) the coverage code of every paper; (3) every pair (orientation, raw gap,
tolerance band in EXACT rational arithmetic, relative gap, sign agreement, log ratio); (4) bins, D, shares, the distribution, the strict
direction, the band curve; (5) the family-block bootstrap with its own implementation of amendment 6.1 and the exact (Clopper-Pearson)
intervals by the regularised incomplete beta function (the builder bisects on binomial sums); (6) the sets a-g, leave-one-family-out, the splits,
the certified pairs and the planning widths; (7) the trajectory over the retained snapshots.  Then compares with
data/l3_meta/q2r_v1/RESULTS_Q2R_V1.json (and the pairs and coverage CSV files).

Run:  python3 -I -B scripts/l3_meta/check_q2r_independent.py [--results PATH]      (exit 0 = all equal)
"""
import argparse
import csv
import datetime
import glob
import hashlib
import json
import math
import random
import re
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
problems = []
n_checked = 0


def isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def eq(a, b, tol=1e-12):
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is None and b is None
        return abs(a - b) <= tol * max(1.0, abs(b))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(eq(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(eq(a[k], b[k], tol) for k in a)
    return a == b


def same(label, got, want, tol=1e-12):
    global n_checked
    n_checked += 1
    ok = eq(got, want, tol)
    if not ok:
        print("DIFF " + label + f"  checker={got!r}  results={want!r}")
        problems.append(label)
    return ok


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------------------------
# small numerics, written differently from the builder
# ---------------------------------------------------------------------------------------------
def q7(xs, q):
    xs = sorted(xs)
    m = len(xs)
    if m == 0:
        return None
    pos = q * (m - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= m:
        return xs[-1]
    return xs[lo] * (1.0 - frac) + xs[lo + 1] * frac


def med(xs):
    xs = sorted(xs)
    m = len(xs)
    if m == 0:
        return None
    return xs[m // 2] if m % 2 else (xs[m // 2 - 1] + xs[m // 2]) / 2


def avg(xs):
    xs = list(xs)
    return None if not xs else math.fsum(xs) / len(xs)


def betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def ibeta(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * betacf(a, b, x) / a
    return 1.0 - bt * betacf(b, a, 1.0 - x) / b


def beta_quantile(prob, a, b):
    lo, hi = 0.0, 1.0
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if ibeta(a, b, mid) < prob:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def cp(k, n, alpha=0.05):
    if n <= 0:
        return None
    low = 0.0 if k == 0 else beta_quantile(alpha / 2, k, n - k + 1)
    high = 1.0 if k == n else beta_quantile(1 - alpha / 2, k + 1, n - k)
    return [low, high]


def exact_sign_p(k, n):
    if n <= 0:
        return None
    big = max(k, n - k)
    top = sum(math.comb(n, i) for i in range(big, n + 1))
    return min(1.0, float(Fraction(2 * top, 2 ** n)))


def rank_interval(vals):
    vals = sorted(vals)
    m = len(vals)
    if m == 0:
        return None
    lo = math.floor(Fraction(25 * (m + 1), 1000))
    hi = math.ceil(Fraction(975 * (m + 1), 1000))
    lo = min(max(lo, 1), m)
    hi = min(max(hi, 1), m)
    return [vals[lo - 1], vals[hi - 1]]


# ---------------------------------------------------------------------------------------------
# the primary estimate from runs[] (V3.2 section 2), the coverage code and the pair
# ---------------------------------------------------------------------------------------------
def when(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def pick_primary(runs):
    cands = []
    for r in runs:
        if r.get("eligible_for_primary") and r.get("frozen") and r.get("code_origin") == "independent" and not r.get("is_audit") \
                and "/audit2/" not in r["run_id"] and isnum((r.get("ours") or {}).get("point")):
            cands.append(r)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if any(not c.get("freeze_utc") for c in cands):
        return None
    cands.sort(key=lambda c: (when(c.get("first_estimate_utc") or c["freeze_utc"]), c["run_id"]))
    if when(cands[0].get("first_estimate_utc") or cands[0]["freeze_utc"]) == when(cands[1].get("first_estimate_utc") or cands[1]["freeze_utc"]):
        return None
    return cands[0]


def code_of(paper, run):
    if run is None:
        st = paper.get("stage") or ""
        if st[:7] == "closed_":
            return "CLOSED_TRIAGE"
        if st[:8] == "waiting_":
            return "WAITING_ACCESS"
        return {"not_started": "NOT_STARTED", "in_progress": "IN_PROGRESS", "finished_uncounted": "FINISHED_UNCOUNTED"}.get(st, "NO_PRIMARY_OTHER")
    if run.get("code_origin") != "independent" or run.get("frozen") is not True or run.get("eligible_for_primary") is not True \
            or run.get("target_fixed_before_estimation") is not True:
        return "PRIMARY_INELIGIBLE"
    if run.get("rung") not in ("R1a", "R1b"):
        return "RUNG_NOT_COUNTED"
    o = (run.get("ours") or {}).get("point")
    p = (run.get("published") or {}).get("point")
    if not (isnum(o) and isnum(p)):
        return "NO_NUMBER"
    if p == 0:
        return "ZERO_PUBLISHED"
    grade = run.get("grade_numeric") or (run.get("numeric_checks") or {}).get("grade")
    if grade not in ("TIGHT", "DIRECTIONAL", "NOT_CONSISTENT", "NOT_COMPARABLE"):
        return "GRADE_MISSING"
    reasons = (run.get("numeric_checks") or {}).get("reason_codes") or []
    if grade == "NOT_COMPARABLE" and sorted(set(reasons)) != ["interval_unavailable_outside_point_tolerance"]:
        return "NC_OTHER_REASON"
    return "EVAL"


def fr(x):
    return Fraction(str(x))


def band(p, se, o, mult):
    """Exact rational version of amendment 4.3: -1/0/+1."""
    s = se if (isnum(se) and se >= 0) else 0
    a = 1 if p > 0 else -1
    d = (fr(p) - fr(o)) * a
    t = fr(mult) * max(abs(fr(p)), fr(s))
    if abs(d) <= t:
        return 0
    return 1 if d > 0 else -1


def make_pair(paper, run, cat, pinned=False):
    p, se = run["published"].get("point"), run["published"].get("se")
    if pinned:
        tg = run.get("target")
        if isinstance(tg, dict) and isnum(tg.get("point")) and tg.get("point") != 0:
            p, se = tg["point"], tg.get("se")
    o = run["ours"]["point"]
    s = se if (isnum(se) and se >= 0) else 0
    a = 1 if p > 0 else -1
    Fp, Fo = fr(p), fr(o)
    d_exact = (Fp - Fo) * a
    base_exact = max(abs(Fp), fr(s))
    O = a * Fo
    u = 1 if O > 0 else 0
    fam = (cat or {}).get("policy_family") or ("__own__:" + paper["paper_id"])
    sources = (cat or {}).get("sources") or []
    field = "other"
    for src in sources:
        if src != "GAP":
            field = {"B1": "labor_tax", "B2": "health", "B3": "crime_guns_traffic", "B4": "education_family_elections_other"}.get(src, "other")
            break
    vs = (cat or {}).get("venue_strong")
    jg = "not_listed" if not vs else ("top5" if vs in ("AER", "QJE", "JPE", "REStud", "Econometrica") else "other_listed")
    yr = paper.get("year")
    ag = "unknown" if not isnum(yr) else ("published_2015_or_earlier" if yr <= 2015 else "published_2016_or_later")
    return {
        "paper_id": paper["paper_id"], "family": fam, "stage": paper.get("stage"), "p": p, "se": s, "o": o, "a": a,
        "d": float(d_exact), "d_sign": (d_exact > 0) - (d_exact < 0), "base": float(base_exact), "g": float(d_exact / base_exact),
        "c": band(p, se, o, Fraction(1, 10)), "u": u, "L": (math.log(float(abs(Fp) / O)) if u else None),
        "grade": run.get("grade_numeric") or (run.get("numeric_checks") or {}).get("grade"), "gates": list(run.get("gate_reasons") or []),
        "verified": run.get("target_verified") is True, "spec": run.get("spec_completeness"), "rung": run.get("rung"), "field": field,
        "journal": jg, "age": ag, "year": yr, "certified": paper.get("stage") == "confirmed", "slug": paper.get("slug")}


def evaluate(snap, cats, use_runs=True):
    rows, pairs = [], []
    for paper in sorted(snap["papers"], key=lambda x: x["paper_id"]):
        run = pick_primary(paper["runs"]) if use_runs else paper.get("primary")
        code = code_of(paper, run)
        rows.append((paper["paper_id"], code))
        if code == "EVAL":
            pairs.append(make_pair(paper, run, cats.get(paper["paper_id"])))
    return rows, pairs


def fingerprint(pairs):
    items = sorted([x["paper_id"], x["p"], x["se"], x["o"], x["stage"], sorted(x["gates"])] for x in pairs)
    return sha_bytes(json.dumps(items, sort_keys=True, separators=(",", ":")).encode("utf-8"))


# ---------------------------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------------------------
def counts(pairs, mult=Fraction(1, 10), pre=True):
    if pre and mult == Fraction(1, 10):
        cs = [x["c"] for x in pairs]
    else:
        cs = [band(x["p"], x["se"], x["o"], mult) for x in pairs]
    return cs.count(1), cs.count(0), cs.count(-1)


def stats(pairs):
    n = len(pairs)
    plus, zero, minus = counts(pairs)
    agree = [x for x in pairs if x["u"]]
    Ls = [x["L"] for x in agree]
    out = {"n": n, "F": len({x["family"] for x in pairs}), "n_plus": plus, "n_zero": zero, "n_minus": minus,
           "D": (plus - minus) / n if n else None, "n_dec": plus + minus, "P_dec": plus / (plus + minus) if plus + minus else None,
           "n_pub_larger_strict": sum(1 for x in pairs if x["d_sign"] > 0), "n_ours_larger_strict": sum(1 for x in pairs if x["d_sign"] < 0),
           "n_ties": sum(1 for x in pairs if x["d_sign"] == 0), "n_agree": len(agree), "n_flip": n - len(agree),
           "L_n": len(Ls), "L_mean": avg(Ls), "L_median": med(Ls) if Ls else None, "ratio": math.exp(-avg(Ls)) if Ls else None,
           "L_q": [q7(Ls, q) for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)] if Ls else None,
           "g_q": [q7([x["g"] for x in pairs], q) for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)] if pairs else None}
    return out


def bootstrap(pairs, B=9999, seed=20261016):
    ordered = sorted(pairs, key=lambda x: x["paper_id"])
    fams = sorted({x["family"] for x in ordered})
    F = len(fams)
    blocks = [[(x["c"], x["d_sign"], x["u"], x["L"]) for x in ordered if x["family"] == f] for f in fams]
    rng = random.Random(seed)
    res = {k: [] for k in ("D", "share_plus", "share_zero", "share_minus", "strict_share_published_larger", "sign_agreement", "mean_L", "median_L", "ratio_ours_to_published")}
    for _ in range(B):
        pool = []
        for _j in range(F):
            pool += blocks[int(rng.random() * F)]
        n = len(pool)
        plus = sum(1 for t in pool if t[0] == 1)
        minus = sum(1 for t in pool if t[0] == -1)
        res["D"].append((plus - minus) / n)
        res["share_plus"].append(plus / n)
        res["share_minus"].append(minus / n)
        res["share_zero"].append((n - plus - minus) / n)
        res["strict_share_published_larger"].append(sum(1 for t in pool if t[1] > 0) / n)
        Ls = [t[3] for t in pool if t[2]]
        res["sign_agreement"].append(len(Ls) / n)
        if Ls:
            m = sum(Ls) / len(Ls)
            res["mean_L"].append(m)
            res["median_L"].append(med(Ls))
            res["ratio_ours_to_published"].append(math.exp(-m))
    return {k: rank_interval(v) for k, v in res.items()}, {k: len(v) for k, v in res.items()}


def floor_ok(pairs):
    return len(pairs) >= 20 and len({x["family"] for x in pairs}) >= 20


def reading_of(iv, pairs):
    if not floor_ok(pairs) or iv is None:
        return "descriptive only"
    return "published larger" if iv[0] > 0 else ("ours larger" if iv[1] < 0 else "no asymmetry seen")


def check_set(label, pairs, want, engine=True):
    s = stats(pairs)
    same(label + " n", s["n"], want["n"])
    same(label + " F", s["F"], want["F"])
    same(label + " bins", [s["n_plus"], s["n_zero"], s["n_minus"]], [want["bins"]["n_plus"], want["bins"]["n_zero"], want["bins"]["n_minus"]])
    same(label + " D", s["D"], want["D"])
    same(label + " P_dec", s["P_dec"], want["P_dec"])
    same(label + " strict", [s["n_pub_larger_strict"], s["n_ours_larger_strict"], s["n_ties"]],
         [want["strict"]["n_published_larger"], want["strict"]["n_ours_larger"], want["strict"]["n_exact_ties"]])
    same(label + " sign agreement", [s["n_agree"], s["n_flip"]], [want["sign_agreement"]["n_agree"], want["sign_agreement"]["n_flip_or_zero"]])
    same(label + " L n/mean/median/ratio", [s["L_n"], s["L_mean"], s["L_median"], s["ratio"]],
         [want["log_ratio"]["n"], want["log_ratio"]["mean"], want["log_ratio"]["median"], want["log_ratio"]["ratio_ours_to_published_geometric"]])
    if s["L_q"] is not None:
        same(label + " L quantiles", s["L_q"], list(want["log_ratio"]["quantiles"].values()))
    else:
        same(label + " L quantiles", None, want["log_ratio"]["quantiles"])
    if s["g_q"] is not None:
        same(label + " g quantiles", s["g_q"], list(want["relative_gap_g_quantiles"].values()))
    same(label + " floor", floor_ok(pairs), want["floor"]["met"])
    if floor_ok(pairs) and engine:
        iv, used = bootstrap(pairs)
        for k, v in iv.items():
            same(label + " bootstrap " + k, v, want["bootstrap"][k]["interval"])
            same(label + " bootstrap used " + k, used[k], want["bootstrap"][k]["m_resamples_used"])
        da = want["direction_among_departures"]
        same(label + " CP P_dec", cp(s["n_plus"], s["n_dec"]), da["cp95"], 1e-9)
        same(label + " sign test p", exact_sign_p(s["n_plus"], s["n_dec"]), da["sign_test_p_two_sided"])
        same(label + " CP sign agreement", cp(s["n_agree"], s["n"]), want["sign_agreement"]["cp95"], 1e-9)
        same(label + " reading", reading_of(iv["D"], pairs), want["reading"])
    else:
        same(label + " no engine below the floor", "bootstrap" in want, False)
        same(label + " reading", "descriptive only", want["reading"])
    return s


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=str(ROOT / "data/l3_meta/q2r_v1/RESULTS_Q2R_V1.json"))
    args = ap.parse_args()
    res_path = Path(args.results)
    R = json.loads(res_path.read_text(encoding="utf-8"))
    run_dir = res_path.parent
    st = R["stamp"]

    # --- files and hashes
    snap_orig = ROOT / st["snapshot_file"]
    snap_copy = run_dir / "snapshot_used.json"
    raw_copy = snap_copy.read_bytes()
    same("archived snapshot copy sha256 = stamp", sha_bytes(raw_copy), st["snapshot_sha256"])
    if snap_orig.exists():
        same("original snapshot sha256 = stamp", sha_bytes(snap_orig.read_bytes()), st["snapshot_sha256"])
    cat_copy = (run_dir / "paper_catalog_used.jsonl").read_bytes()
    same("archived catalogue sha256 = stamp", sha_bytes(cat_copy), st["catalogue_sha256"])
    same("original catalogue sha256 = stamp", sha_bytes((ROOT / "data/replication/paper_catalog_v1.jsonl").read_bytes()), st["catalogue_sha256"])
    amend = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2R_V1.md"
    rec = (ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2R_V1_HASH.txt").read_text(encoding="utf-8")
    same("amendment sha256 = stamp", sha_bytes(amend.read_bytes()), st["amendment_sha256"])
    same("amendment sha256 = hash record", re.search(r"^sha256:\s*([0-9a-f]{64})", rec, re.M).group(1), st["amendment_sha256"])
    same("T_amend = hash record", re.search(r"^amended_utc \(T_amend\):\s*(\S+)", rec, re.M).group(1), st["t_amend_utc"])
    same("builder sha256 = stamp (the code that ran is the code on disk)", sha_bytes((ROOT / st["code"]).read_bytes()), st["code_sha256"])
    snap = json.loads(raw_copy)
    cats = {}
    for line in cat_copy.decode("utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            cats[r["paper_id"]] = r
    same("roster D", len(snap["papers"]), st["D"])

    # --- 1. the primary of every paper from runs[]
    bad_primary = []
    for paper in snap["papers"]:
        run = pick_primary(paper["runs"])
        if (run is None) != (paper.get("primary") is None):
            bad_primary.append(paper["paper_id"])
        elif run is not None and (paper["primary_run_id"] != run["run_id"] or paper["primary"] != run):
            bad_primary.append(paper["paper_id"])
    same("primary re-derived from runs[] equals the snapshot's primary, all 503 papers", bad_primary, [])

    # --- 2. coverage
    rows, pairs = evaluate(snap, cats, use_runs=True)
    cov = {}
    for _, c in rows:
        cov[c] = cov.get(c, 0) + 1
    for code, cnt in R["coverage"]["codes"].items():
        same("coverage code " + code, cov.get(code, 0), cnt)
    same("coverage covers all papers", sum(cov.values()), 503)
    with open(run_dir / "Q2R_COVERAGE_V1.csv", newline="", encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    same("coverage csv rows", [(r["paper_id"], r["code"]) for r in csv_rows], rows)

    # --- 3. pairs
    same("n", len(pairs), st["n"])
    same("F", len({x["family"] for x in pairs}), st["F"])
    same("fingerprint", fingerprint(pairs), st["pairs_fingerprint_sha256"])
    by = {x["paper_id"]: x for x in pairs}
    same("pair ids", sorted(by), [x["paper_id"] for x in R["pairs"]])
    for w in R["pairs"]:
        x = by[w["paper_id"]]
        for mine, theirs in (("p", "p"), ("se", "se_pub"), ("o", "o"), ("a", "a"), ("d", "d"), ("d_sign", "d_sign"), ("base", "base"), ("g", "g"), ("c", "c"), ("u", "u"),
                             ("L", "L"), ("family", "family"), ("stage", "stage"), ("grade", "grade_numeric"), ("gates", "gate_reasons"), ("verified", "target_verified"),
                             ("rung", "rung"), ("field", "field"), ("journal", "journal_group"), ("age", "age_group"), ("certified", "certified")):
            same(f"pair {w['paper_id']} {theirs}", x[mine], w[theirs])
        # exact tolerance versus the recorded and the builder's float tolerance (informative: expect no band difference)
        same(f"pair {w['paper_id']} tolerance ~ recorded", float(Fraction(1, 10) * fr(x["base"])), w["tau"], 1e-12)
    with open(run_dir / "Q2R_PAIRS_V1.csv", newline="", encoding="utf-8") as fh:
        prow = list(csv.DictReader(fh))
    same("pairs csv ids", [r["paper_id"] for r in prow], [w["paper_id"] for w in R["pairs"]])
    same("pairs csv c", [int(r["c"]) for r in prow], [w["c"] for w in R["pairs"]])
    same("pairs csv d", [float(r["d"]) for r in prow], [w["d"] for w in R["pairs"]])

    # --- 4-5. primary set
    P = R["question"]["Q2R"]["primary"]
    check_set("primary", pairs, P)
    for rowc in R["question"]["Q2R"]["band_curve"]:
        m = fr(rowc["band_multiplier"])
        plus, zero, minus = counts(pairs, m, pre=False)
        same(f"band curve m={rowc['band_multiplier']}", [plus, zero, minus, (plus - minus) / len(pairs)], [rowc["n_plus"], rowc["n_zero"], rowc["n_minus"], rowc["D"]])
    pl = R["question"]["Q2R"]["planning_half_width_cp95_at_half"]
    for k, v in pl.items():
        lo, hi = cp(int(k) // 2, int(k))
        same("planning half-width n_dec=" + k, (hi - lo) / 2, v, 1e-9)
    dec_mine = sorted([x for x in pairs if x["c"] != 0], key=lambda x: -x["g"])
    same("decisive pairs in order", [x["paper_id"] for x in dec_mine], [w["paper_id"] for w in R["question"]["Q2R"]["decisive_pairs_by_g_desc"]])

    # --- 6. sets, leave-one-family-out, splits, certified
    sets = R["beside"]["sets"]
    preds = {"a_without_not_comparable": lambda x: x["grade"] != "NOT_COMPARABLE", "b_target_verified": lambda x: x["verified"],
             "c_gate_clean": lambda x: not x["gates"], "d_stage_matched_or_confirmed": lambda x: x["stage"] in ("matched", "confirmed"),
             "e_confirmed_only": lambda x: x["stage"] == "confirmed"}
    for name, pred in preds.items():
        check_set("set " + name, [x for x in pairs if pred(x)], sets[name])
    pinned = []
    n_diff = 0
    for paper in sorted(snap["papers"], key=lambda x: x["paper_id"]):
        run = pick_primary(paper["runs"])
        if code_of(paper, run) == "EVAL":
            x = make_pair(paper, run, cats.get(paper["paper_id"]), pinned=True)
            pinned.append(x)
            if (x["p"], x["se"]) != (by[x["paper_id"]]["p"], by[x["paper_id"]]["se"]):
                n_diff += 1
    check_set("set f_original_pinned_value", pinned, sets["f_original_pinned_value"])
    same("set f pairs whose value differs", n_diff, sets["f_original_pinned_value"]["n_pairs_whose_value_differs_from_comparison_value"])
    fam_groups = {}
    for x in pairs:
        fam_groups.setdefault(x["family"], []).append(x)
    fw_D = avg([avg([x["c"] for x in v]) for v in fam_groups.values()])
    fw_L = avg([avg([x["L"] for x in v if x["u"]]) for v in fam_groups.values() if any(x["u"] for x in v)])
    same("set g family-weighted D / mean L", [fw_D, fw_L], [sets["g_family_weighted"]["D_family_weighted"], sets["g_family_weighted"]["mean_L_family_weighted"]])
    loo = R["beside"]["leave_one_family_out"]
    for row in loo["rows"]:
        rest = [x for x in pairs if x["family"] != row["dropped_family"]]
        s = stats(rest)
        same("leave-out " + row["dropped_family"], [s["n"], s["D"], s["L_mean"]], [row["n"], row["D"], row["mean_L"]])
    same("leave-out families", sorted(fam_groups), sorted(r["dropped_family"] for r in loo["rows"]))
    same("leave-out D range", [min(r["D"] for r in loo["rows"]), max(r["D"] for r in loo["rows"])], [loo["D_range"]["min"], loo["D_range"]["max"]])
    keyfn = {"rung": lambda x: x["rung"], "specification_completeness": lambda x: "complete" if x["spec"] == "complete" else "not_complete",
             "field_catalogue_batch": lambda x: x["field"], "paper_age": lambda x: x["age"], "journal_group": lambda x: x["journal"]}
    for sname, table in R["beside"]["splits"].items():
        for arm, w in table.items():
            if arm.startswith("__"):
                same(f"split {sname} stray arms", w, sorted({keyfn[sname](x) for x in pairs} - {a for a in table if not a.startswith('__')}))
                continue
            sub = [x for x in pairs if keyfn[sname](x) == arm]
            s = stats(sub)
            same(f"split {sname}/{arm}", [s["n"], s["n_plus"], s["n_zero"], s["n_minus"], s["D"], s["L_n"], s["L_mean"], s["L_median"], s["n"] < 5],
                 [w["n"], w["n_plus"], w["n_zero"], w["n_minus"], w["D"], w["n_sign_agree"], w["mean_L"], w["median_L"], w["too_few"]])
    cert = [x for x in pairs if x["certified"]]
    same("certified pairs", [(x["paper_id"], x["p"], x["o"], x["c"]) for x in cert], [(w["paper_id"], w["p"], w["o"], w["c"]) for w in R["beside"]["certified_pairs"]])

    # --- 7. trajectory over the retained snapshots
    files = sorted(glob.glob(str(ROOT / "data/replication/snapshot_v5/snapshot_*.json")))
    prev = None
    mine = []
    for f in files:
        raw = open(f, "rb").read()
        sn = json.loads(raw)
        if sn.get("schema_version") != 5 or sn["as_of"] > st["snapshot_as_of"]:
            continue
        _, pp = evaluate(sn, cats, use_runs=False)
        s = stats(pp)
        fp = fingerprint(pp)
        mine.append({"sha256": sha_bytes(raw), "n": s["n"], "F": s["F"], "n_plus": s["n_plus"], "n_zero": s["n_zero"], "n_minus": s["n_minus"], "D": s["D"],
                     "P_dec": s["P_dec"], "mean_L": s["L_mean"], "median_L": s["L_median"], "sign_agreement": s["n_agree"] / s["n"] if s["n"] else None,
                     "fingerprint": fp, "new_state": fp != prev})
        prev = fp
    theirs = [{k: t[k] for k in mine[0]} for t in R["trajectory"]]
    same("trajectory rows", len(mine), len(theirs))
    for i, (a, b) in enumerate(zip(mine, theirs)):
        same(f"trajectory row {i} ({a['sha256'][:8]})", a, b)

    # --- audit block and stamp
    same("audit: nothing written under data/replication", R["audit"]["files_written_under_data_replication"], 0)
    same("stamp has no significance label", R["stamp"]["no_significance_label"], True)
    same("data checks all clean", [R["checks"]["data_checks"]["pairs_where_band_and_numeric_grade_disagree"], R["checks"]["data_checks"]["pairs_where_recomputed_tolerance_differs_from_recorded"],
                                   R["checks"]["data_checks"]["pairs_where_primary_run_missing_or_not_independent"]], [[], [], []])

    print(f"{n_checked} comparisons, {len(problems)} differences")
    if problems:
        print("DIFFERENCES: " + "; ".join(problems[:20]))
        return 1
    print("ALL EQUAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
