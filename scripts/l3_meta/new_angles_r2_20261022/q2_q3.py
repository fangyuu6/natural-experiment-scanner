#!/usr/bin/env python3
"""T256 coder B: Q2 (time to first visible effect) and Q3 (same law, different states). EXPLORATORY (EX-1).
Plan: docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md (sha256 a503c461...). Run:
  .venv_did/bin/python -I -B scripts/l3_meta/new_angles_r2_20261022/q2_q3.py q2|q3|all [--force]
Writes data/l3_meta/new_angles_r2_20261022/{q2,q3}_design_level.csv, q3_null_draws.csv, RESULTS_Q2.json, RESULTS_Q3.json."""
import sys, csv, json, math, collections, hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
import numpy as np
from scipy import stats

ROOT = HERE.parents[2]
IN = ROOT / "data/l3_meta/new_angles_20261021/inputs"
OUT = ROOT / "data/l3_meta/new_angles_r2_20261022"
LIVE = ROOT / "data/l3_meta/refresh/20261022_r2/live/l2_set_live.json"
PLAN = ROOT / "docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md"
PLAN_SHA = "a503c461f99954215ced45f789b1c74def7ceab635b5a5c9b33dfcd0f7d5f661"
SEED = 20261022
CENSOR = 6


# ------------------------------------------------------------------ pure functions
def sgn(x):
    return (x > 0) - (x < 0)


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def domain_group(d):
    return "other" if d == "education" else d


def q2_eligible(es, overall):
    """es: dict event_time -> (att, se). Needs >=3 of -4..-1 and >=5 of 0..5 with finite att, and nonzero overall sign."""
    if not overall:
        return False
    pre = sum(1 for e in range(-4, 0) if e in es and es[e][0] is not None)
    post = sum(1 for e in range(0, 6) if e in es and es[e][0] is not None)
    return pre >= 3 and post >= 5


def onset_primary(es, overall, censor=CENSOR):
    """first e in 0..4 where visible at e and e+1; visible = same sign as overall and |att| > pre envelope (max |att| over e in -4..-1)."""
    env = max(abs(es[e][0]) for e in range(-4, 0) if e in es and es[e][0] is not None)
    s = sgn(overall)

    def vis(e):
        if e not in es or es[e][0] is None:
            return False
        a = es[e][0]
        return sgn(a) == s and abs(a) > env

    for e in range(0, 5):
        if vis(e) and vis(e + 1):
            return e
    return censor


def onset_secondary(es, overall, censor=CENSOR):
    s = sgn(overall)

    def vis(e):
        if e not in es or es[e][0] is None or es[e][1] is None or es[e][1] <= 0:
            return False
        a, se = es[e]
        return sgn(a) == s and abs(a / se) >= 1.96

    for e in range(0, 5):
        if vis(e) and vis(e + 1):
            return e
    return censor


def largest_gap(T, grp, qual):
    """T: array of onsets, grp: array of group labels, qual: list of groups to compare. Returns (gap, (hi_group, lo_group)) over pairs of medians."""
    med = {g: float(np.median(T[grp == g])) for g in qual}
    best, pair = -1.0, None
    for i, a in enumerate(qual):
        for b in qual[i + 1:]:
            gp = abs(med[a] - med[b])
            if gp > best:
                best, pair = gp, (a, b) if med[a] >= med[b] else (b, a)
    return best, pair


def shuffle_labels(rng, grp, strata):
    """permute group labels within strata."""
    out = grp.copy()
    for s in np.unique(strata):
        idx = np.where(strata == s)[0]
        out[idx] = grp[rng.permutation(idx)]
    return out


def cohort_values(rows, window=(0, 1, 2, 3)):
    """rows: iterable of (g, e, att, se). Cohort value = mean att over window if all window event times have finite att.
    Returns {g: (mean_att, mean_se (None if any se missing), rss_se (None if any se missing))}."""
    by = collections.defaultdict(dict)
    for g, e, a, se in rows:
        if e in window and a is not None:
            by[g][e] = (a, se)
    out = {}
    for g, d in by.items():
        if all(e in d for e in window):
            atts = [d[e][0] for e in window]
            ses = [d[e][1] for e in window]
            if all(s is not None and s > 0 for s in ses):
                out[g] = (float(np.mean(atts)), float(np.mean(ses)), float(math.sqrt(sum(s * s for s in ses)) / len(window)))
            else:
                out[g] = (float(np.mean(atts)), None, None)
    return out


def q_stat(c, se):
    c, se = np.asarray(c, float), np.asarray(se, float)
    w = 1.0 / se ** 2
    cb = np.sum(w * c) / np.sum(w)
    Q = float(np.sum(w * (c - cb) ** 2))
    return Q, float(stats.chi2.sf(Q, len(c) - 1))


def v_stat(c, ddof=1):
    return float(np.var(np.asarray(c, float), ddof=ddof))


def placebo_p(v_obs, V):
    V = np.asarray(V, float)
    V = V[np.isfinite(V)]
    return float((1 + np.sum(V >= v_obs)) / (1 + len(V)))


def loo_p(V):
    """leave-one-out p for each draw against the other draws: (1 + #others with V >= V_j) / (1 + N - 1)."""
    V = np.asarray(V, float)
    ok = np.isfinite(V)
    v = V[ok]
    n = len(v)
    srt = np.sort(v)
    # number of values >= v_j among all (incl. itself) = n - searchsorted(srt, v_j, 'left'); minus self
    ge = n - np.searchsorted(srt, v, side="left") - 1
    p = np.full(len(V), np.nan)
    p[ok] = (1 + ge) / (1 + (n - 1))
    return p


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 22), b""):
            h.update(ch)
    return h.hexdigest()


def jclean(o):
    if isinstance(o, (np.floating, np.integer)):
        o = o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): jclean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jclean(v) for v in o]
    return o


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval="")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ loading
def load():
    assert sha(PLAN) == PLAN_SHA, "plan hash mismatch"
    live = json.load(open(LIVE))
    est = {x["design_id"]: x for x in live["designs"] if x["status"] == "estimated"}
    ros = {r["design_id"]: r for r in csv.DictReader(open(IN / "design_roster.csv"))}
    assert set(est) == set(ros) and len(ros) == 77, "roster ids differ from live estimated set"
    mism = []
    for k, r in ros.items():
        x = est[k]
        if abs(float(r["overall_att"]) - x["point_estimate"]) > 1e-9:
            mism.append((k, "att"))
        if (r["beyond_noise"] == "True") != bool(x["beyond_noise"]) and r["beyond_noise"] != "":
            mism.append((k, "beyond_noise"))
        if r["noise_sd"] != "" and x.get("benchmark") and False:
            pass
    keys = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(IN / "policy_keys.csv"))}
    es = collections.defaultdict(list)
    for r in csv.DictReader(open(IN / "event_study_export.csv")):
        es[r["design_id"]].append(r)
    gt = collections.defaultdict(list)
    for r in csv.DictReader(open(IN / "group_time_export.csv")):
        gt[r["design_id"]].append(r)
    D = {}
    for did, r in ros.items():
        D[did] = dict(id=did, domain=domain_group(r["domain"]), law=keys[did], source=r["source_group"], overall=fnum(r["overall_att"]),
                      es_rows=es[did], gt_rows=gt[did], live=est[did])
    return D, mism


# ------------------------------------------------------------------ Q2
def es_dict(rows, drop_thin=False):
    d = {}
    for r in rows:
        if drop_thin and r["thin"] == "True":
            continue
        d[int(float(r["event_time"]))] = (fnum(r["att"]), fnum(r["se"]))
    return d


def run_q2_variant(D, rule, censor=CENSOR, drop_thin=False, strat=True, rng_seed=SEED + 2, nperm=5000, min_total=30, min_group=8):
    items = []
    for did, d in D.items():
        es = es_dict(d["es_rows"], drop_thin)
        if not q2_eligible(es, d["overall"]):
            continue
        if rule == "primary":
            T = onset_primary(es, d["overall"], censor)
        else:
            if not any(v[1] is not None for v in es.values()):
                continue
            T = onset_secondary(es, d["overall"], censor)
        items.append(dict(id=did, T=T, dom=d["domain"], law=d["law"], src=d["source"]))
    out = dict(n=len(items))
    if not items:
        return out, items
    T = np.array([i["T"] for i in items], float)
    grp = np.array([i["dom"] for i in items])
    src = np.array([i["src"] for i in items])
    cnt = collections.Counter(grp)
    qual = sorted(g for g, c in cnt.items() if c >= min_group)
    by = {}
    for g in sorted(cnt):
        t = T[grp == g]
        by[g] = dict(n=int(len(t)), median_T=float(np.median(t)), share_visible_by_year2=float(np.mean(t <= 2)), share_never_visible=float(np.mean(t >= censor)),
                     mostly_not_visible_within_5_years=bool(np.mean(t >= censor) > 0.5))
    out["by_domain"] = by
    out["qualifying_groups"] = qual
    out["min_check"] = dict(total_ok=len(items) >= min_total, groups_ok=len(qual) >= 2, total=len(items), min_total=min_total, min_group=min_group,
                            group_sizes=dict(cnt))
    if len(items) < min_total or len(qual) < 2:
        out["status"] = "not enough data"
        out["gap_to_minimum"] = dict(total_short_by=max(0, min_total - len(items)), qualifying_groups=len(qual))
        return out, items
    gap, pair = largest_gap(T, grp, qual)
    rng = np.random.default_rng(rng_seed)
    strata = src if strat else np.zeros(len(src), int)
    null = np.empty(nperm)
    for b in range(nperm):
        null[b] = largest_gap(T, shuffle_labels(rng, grp, strata), qual)[0]
    p95 = float(np.percentile(null, 95))
    out.update(status="ok", gap=gap, pair_hi_lo=pair, null_p95=p95, null_mean=float(null.mean()), null_p50=float(np.percentile(null, 50)),
               null_p5=float(np.percentile(null, 5)), null_p99=float(np.percentile(null, 99)),
               p_gap=float((1 + np.sum(null >= gap)) / (1 + nperm)), rule_gap_ge_2=bool(gap >= 2), rule_above_p95=bool(gap > p95),
               pattern=bool(gap >= 2 and gap > p95))
    out["_null"] = null
    return out, items


def law_level(items, qual, nperm=5000, seed=SEED + 22):
    L = collections.defaultdict(list)
    for i in items:
        L[i["law"]].append(i)
    laws = sorted(L)
    T = np.array([float(np.median([i["T"] for i in L[l]])) for l in laws])
    grp = np.array([collections.Counter(i["dom"] for i in L[l]).most_common(1)[0][0] for l in laws])
    src = np.array([collections.Counter(i["src"] for i in L[l]).most_common(1)[0][0] for l in laws])
    cnt = collections.Counter(grp)
    q = [g for g in qual if cnt.get(g, 0) >= 1]
    if len(q) < 2:
        return dict(n_laws=len(laws), status="not computable")
    gap, pair = largest_gap(T, grp, q)
    rng = np.random.default_rng(seed)
    null = np.array([largest_gap(T, shuffle_labels(rng, grp, src), q)[0] for _ in range(nperm)])
    p95 = float(np.percentile(null, 95))
    return dict(n_laws=len(laws), gap=gap, pair_hi_lo=pair, null_p95=p95, rule_gap_ge_2=bool(gap >= 2), rule_above_p95=bool(gap > p95),
                pattern=bool(gap >= 2 and gap > p95), group_sizes=dict(cnt), medians={g: float(np.median(T[grp == g])) for g in q})


def leave_one_law_out(items, qual):
    laws = sorted(set(i["law"] for i in items))
    res = {}
    for l in laws:
        sub = [i for i in items if i["law"] != l]
        T = np.array([i["T"] for i in sub], float)
        grp = np.array([i["dom"] for i in sub])
        q = [g for g in qual if (grp == g).sum() >= 1]
        if len(q) < 2:
            continue
        res[l] = largest_gap(T, grp, q)[0]
    v = np.array(list(res.values()))
    return dict(n_drops=len(res), min_gap=float(v.min()), max_gap=float(v.max()), n_drops_gap_below_2=int((v < 2).sum()),
                laws_dropping_below_2=sorted(l for l, g in res.items() if g < 2), gap_ge_2_in_all=bool((v >= 2).all()))


def run_q2():
    D, mism = load()
    if mism:
        raise SystemExit(f"consistency mismatch: {mism}")
    res = dict(question="Q2 time to first visible effect", plan_sha256=PLAN_SHA, seed=SEED + 2, n_perm=5000, consistency_check="roster overall_att and beyond_noise equal live point_estimate / beyond_noise: OK (77 designs)")
    prim, items = run_q2_variant(D, "primary")
    sec, items_s = run_q2_variant(D, "secondary")
    res["n_eligible_primary"] = prim["n"]
    res["n_eligible_secondary"] = sec["n"]
    res["n_expected_primary"] = 69
    # CSV
    sec_map = {i["id"]: i["T"] for i in items_s}
    rows = []
    for i in items:
        d = D[i["id"]]
        rows.append(dict(design_id=i["id"], domain=i["dom"], source_group=i["src"], law=i["law"], overall_att=d["overall"], T_primary=i["T"],
                         visible_by_year2=int(i["T"] <= 2), never_visible=int(i["T"] >= CENSOR), T_secondary=sec_map.get(i["id"], "")))
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "q2_design_level.csv", rows)
    nullp = prim.pop("_null", None)
    nulls = sec.pop("_null", None)
    if nullp is not None:
        write_csv(OUT / "q2_null_draws.csv", [dict(draw=k, largest_gap_primary=float(v)) for k, v in enumerate(nullp)])
    res["primary"] = prim
    res["secondary"] = sec
    # guards
    if prim.get("status") == "ok":
        res["guards"] = dict(one_vote_per_law=law_level(items, prim["qualifying_groups"]), leave_one_law_out=leave_one_law_out(items, prim["qualifying_groups"]))
    # sensitivities
    sens = {}
    for name, kw in [("censor5", dict(censor=5)), ("drop_thin", dict(drop_thin=True)), ("unstratified_shuffle", dict(strat=False))]:
        r, _ = run_q2_variant(D, "primary", **kw)
        r.pop("_null", None)
        sens[name] = {k: r.get(k) for k in ("n", "status", "gap", "pair_hi_lo", "null_p95", "pattern", "by_domain")}
    res["sensitivities"] = sens
    # secondary rule requirement: stricter reading (documented): pattern claimed only if secondary also meets min AND its own rule
    sec_ok = sec.get("status") == "ok"
    res["secondary_pattern"] = bool(sec.get("pattern")) if sec_ok else False
    prim_pat = bool(prim.get("pattern"))
    guards_ok = bool(prim.get("status") == "ok" and res["guards"]["one_vote_per_law"].get("pattern") and res["guards"]["leave_one_law_out"]["gap_ge_2_in_all"])
    res["pattern_rule_check"] = dict(
        primary_minimum_met=bool(prim.get("min_check", {}).get("total_ok") and prim.get("min_check", {}).get("groups_ok")),
        primary_gap_ge_2_years=prim.get("rule_gap_ge_2"), primary_gap_above_shuffle_p95=prim.get("rule_above_p95"),
        secondary_minimum_met=bool(sec.get("min_check", {}).get("total_ok") and sec.get("min_check", {}).get("groups_ok")),
        secondary_gap_ge_2_years=sec.get("rule_gap_ge_2"), secondary_gap_above_shuffle_p95=sec.get("rule_above_p95"),
        primary_pattern=prim_pat, secondary_pattern_required_too_stricter_reading=res["secondary_pattern"],
        guards_one_vote_per_law_and_leave_one_law_out_hold=guards_ok)
    final = prim_pat and res["secondary_pattern"]
    res["PATTERN"] = bool(final)
    if prim.get("status") != "ok":
        verdict = "not enough data"
    else:
        verdict = "pattern" if final else "no difference"
    res["verdict"] = verdict
    res["guards_note"] = "guards are reported; the pattern decision follows the plan rule (primary gap and shuffle, plus the secondary rule on its own minimum, stricter reading); guards_hold is listed beside it"
    p = prim
    if p.get("status") == "ok":
        meds = ", ".join(f"{g} {v['median_T']:.1f}" for g, v in p["by_domain"].items())
        line2 = (f"Largest gap between medians of two domain groups (>= 8 designs each) {p['gap']:.1f} years ({p['pair_hi_lo'][0]} later than {p['pair_hi_lo'][1]}); "
                 f"shuffle 95th percentile {p['null_p95']:.1f}; median onset by domain (6 = not yet visible): {meds}.")
        line3 = f"n = {p['n']} designs (primary), {sec['n']} (secondary); secondary gap {sec.get('gap')}; verdict follows the plan rule only."
    else:
        line2 = f"Minimum not met: {prim.get('gap_to_minimum')}."
        line3 = f"n = {prim['n']}."
    res["plain"] = dict(line1=f"Result: {verdict}.", line2=line2, line3=line3)
    res["inputs_sha256"] = {"plan": PLAN_SHA, "live_json": sha(LIVE), "design_roster.csv": sha(IN / "design_roster.csv"),
                            "event_study_export.csv": sha(IN / "event_study_export.csv"), "policy_keys.csv": sha(IN / "policy_keys.csv")}
    return res


# ------------------------------------------------------------------ Q3
def draw_cohort_values(group_time, window=(0, 1, 2, 3)):
    by = collections.defaultdict(dict)
    for r in group_time:
        e = r["event_time"]
        if e in window and r.get("att") is not None and math.isfinite(r["att"]):
            by[r["g"]][e] = r["att"]
    return [float(np.mean([d[e] for e in window])) for g, d in by.items() if all(e in d for e in window)]


def binom_up(k, n, p):
    return float(stats.binomtest(int(k), int(n), p, alternative="greater").pvalue)


def pattern_stats(flags, ref, n_src):
    """flags: list of (bool flag, source)."""
    n = len(flags)
    k = sum(f for f, _ in flags)
    share = k / n if n else float("nan")
    return n, k, share


def evaluate(items, ref, key="flag"):
    """items: list of dict(flag, source). Returns the rule check dict."""
    n = len(items)
    if n == 0:
        return {}
    k = sum(bool(i[key]) for i in items)
    share = k / n
    pb = binom_up(k, n, ref)
    by_src = {}
    for s in sorted(set(i["source"] for i in items)):
        sub = [i for i in items if i["source"] == s]
        by_src[s] = dict(n=len(sub), k=int(sum(bool(i[key]) for i in sub)), share=sum(bool(i[key]) for i in sub) / len(sub))
    src_ok = all(v["share"] >= 0.15 for v in by_src.values() if v["n"] >= 15)
    return dict(n=n, k=int(k), share=share, binom_p_one_sided=pb, share_ge_20pct=bool(share >= 0.20), binom_p_lt_01=bool(pb < 0.01),
                share_ge_15pct_in_each_source_with_ge_15=bool(src_ok), by_source=by_src,
                pattern=bool(share >= 0.20 and pb < 0.01 and src_ok))


def run_q3():
    D, mism = load()
    if mism:
        raise SystemExit(f"consistency mismatch: {mism}")
    res = dict(question="Q3 same law, different states", plan_sha256=PLAN_SHA, seed=SEED + 3)
    live = json.load(open(LIVE))
    items = []
    excluded = collections.Counter()
    null_rows = []
    cal_n = cal_k = 0
    cal_by = {}
    result_hashes = {}
    for did, d in D.items():
        rows = [(int(r["g"]), int(float(r["event_time"])), fnum(r["att"]), fnum(r["se"])) for r in d["gt_rows"]]
        cv = cohort_values(rows)
        is_B = d["source"] == "autopilot_estimate"
        if len(cv) < 3:
            excluded["fewer_than_3_eligible_cohorts"] += 1
            continue
        c = np.array([v[0] for v in cv.values()])
        rec = dict(design_id=did, domain=d["domain"], law=d["law"], source="B_placebo" if is_B else "A_se", k=len(cv), V=v_stat(c))
        if not is_B:
            if any(v[1] is None for v in cv.values()):
                excluded["no_se_and_no_placebo_draws"] += 1
                continue
            se_c = np.array([v[1] for v in cv.values()])
            se_i = np.array([v[2] for v in cv.values()])
            Q, p = q_stat(c, se_c)
            Qi, pi = q_stat(c, se_i)
            rec.update(stat=Q, p=p, p_independence_se=pi, flag=int(p < 0.10), flag_independence=int(pi < 0.10))
        else:
            x = live and next(z for z in live["designs"] if z["design_id"] == did)
            path = ROOT / x["source"]["path"]
            result_hashes[did] = sha(path)
            j = json.load(open(path))
            pp = (j.get("l3_export_v2") or {}).get("placebo_profiles")
            if not pp:
                excluded["no_se_and_no_placebo_draws"] += 1
                del j
                continue
            CC = [draw_cohort_values(pr["group_time"]) for pr in pp]
            V1 = np.array([(v_stat(cc, 1) if len(cc) >= 3 else np.nan) for cc in CC])
            V0 = np.array([(v_stat(cc, 0) if len(cc) >= 3 else np.nan) for cc in CC])
            del j, pp, CC
            p = placebo_p(rec["V"], V1)
            p0 = placebo_p(v_stat(c, 0), V0)
            lp = loo_p(V1)
            nv = int(np.isfinite(V1).sum())
            kl = int(np.sum(lp[np.isfinite(lp)] < 0.10))
            cal_n += nv
            cal_k += kl
            cal_by[did] = dict(n_valid_draws=nv, share_p_lt_10=kl / nv if nv else None)
            rec.update(stat=rec["V"], p=p, p_ddof0=p0, flag=int(p < 0.10), flag_ddof0=int(p0 < 0.10), n_valid_draws=nv, loo_rate=kl / nv if nv else float("nan"))
            for k, (v, q) in enumerate(zip(V1, lp)):
                null_rows.append(dict(design_id=did, draw=k, V=("" if not np.isfinite(v) else float(v)), p_loo=("" if not np.isfinite(q) else float(q))))
        items.append(rec)
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "q3_design_level.csv", items)
    write_csv(OUT / "q3_null_draws.csv", null_rows)
    res["n_included"] = len(items)
    res["n_by_source"] = dict(collections.Counter(i["source"] for i in items))
    res["n_expected"] = dict(A=48, B=29)
    res["excluded"] = dict(excluded)
    # calibration
    cal_rate = cal_k / cal_n if cal_n else float("nan")
    ref = 0.10
    res["calibration"] = dict(pooled_loo_rate_p_lt_10=cal_rate, n_draws=cal_n, exceeds_15pct=bool(cal_n and cal_rate > 0.15), design_max=max((v["share_p_lt_10"] for v in cal_by.values() if v["share_p_lt_10"] is not None), default=None))
    if cal_n and cal_rate > 0.15:
        ref = cal_rate
        res["calibration"]["reference_replaced_by"] = cal_rate
    res["reference_rate"] = ref
    nA = res["n_by_source"].get("A_se", 0)
    nB = res["n_by_source"].get("B_placebo", 0)
    N = len(items)
    res["minimum"] = dict(total_designs=N, required=40, met=bool(N >= 40), gap=max(0, 40 - N), source_A_n=nA, source_A_below_25_reported_separately=bool(nA < 25))
    def share_of(sub, key="flag"):
        return dict(n=len(sub), k=int(sum(bool(i[key]) for i in sub)), share=(sum(bool(i[key]) for i in sub) / len(sub) if sub else None))
    res["descriptive"] = dict(pooled=share_of(items), A=share_of([i for i in items if i["source"] == "A_se"]), B=share_of([i for i in items if i["source"] == "B_placebo"]),
                              A_independence_se=share_of([i for i in items if i["source"] == "A_se"], "flag_independence"),
                              B_ddof0=share_of([i for i in items if i["source"] == "B_placebo"], "flag_ddof0"),
                              by_domain={dm: share_of([i for i in items if i["domain"] == dm]) for dm in sorted(set(i["domain"] for i in items)) if sum(1 for i in items if i["domain"] == dm) >= 8})
    for k in ("pooled", "A", "B"):
        pass
    if N < 40:
        res["verdict"] = "not enough data"
        res["PATTERN"] = False
        res["plain"] = dict(line1="Result: not enough data.", line2=f"{N} designs usable, 40 required (short by {40 - N}).", line3=f"n = {N}.")
    else:
        ev = evaluate(items, ref)
        res["pattern_rule_check"] = ev
        # sensitivities
        sens = {}
        itA = [dict(source=i["source"], flag=i["flag_independence"] if i["source"] == "A_se" else i["flag"]) for i in items]
        e1 = evaluate(itA, ref); sens["independence_se_for_A"] = e1
        itB = [dict(source=i["source"], flag=i["flag_ddof0"] if i["source"] == "B_placebo" else i["flag"]) for i in items]
        sens["variance_ddof0_for_B"] = evaluate(itB, ref)
        sens["reference_rate_10pct_fixed"] = evaluate(items, 0.10)
        res["sensitivities"] = sens
        # guards
        laws = collections.defaultdict(list)
        for i in items:
            laws[i["law"]].append(i)
        lawitems = []
        for l, v in laws.items():
            kf = sum(i["flag"] for i in v)
            lawitems.append(dict(flag=int(kf > len(v) / 2), source=collections.Counter(i["source"] for i in v).most_common(1)[0][0]))
        g1 = evaluate(lawitems, ref)
        lolo = {l: evaluate([i for i in items if i["law"] != l], ref) for l in laws}
        lodo = {dm: evaluate([i for i in items if i["domain"] != dm], ref) for dm in sorted(set(i["domain"] for i in items))}
        def summ(dd):
            return dict(min_share=min(v["share"] for v in dd.values()), max_binom_p=max(v["binom_p_one_sided"] for v in dd.values()),
                        n_drops=len(dd), all_patterns_hold=bool(all(v["pattern"] for v in dd.values())),
                        drops_failing=sorted(k for k, v in dd.items() if not v["pattern"]))
        res["guards"] = dict(one_vote_per_law=g1, leave_one_law_out=summ(lolo), leave_one_domain_out=summ(lodo))
        res["guards"]["one_vote_ties_counted_as_no_hit"] = True
        pat = bool(ev["pattern"])
        res["PATTERN"] = pat
        res["verdict"] = "pattern" if pat else "no difference"
        d = res["descriptive"]
        res["plain"] = dict(line1=f"Result: {res['verdict']}.",
                            line2=f"Share of laws with more cohort-to-cohort spread than expected from noise (p < 0.10; 10% expected): pooled {ev['share']:.0%} ({ev['k']}/{ev['n']}), one-sided exact binomial p {ev['binom_p_one_sided']:.3g}.",
                            line3="By source: " + ", ".join("%s %.0f%% (n=%d)" % (k, 100 * v["share"], v["n"]) for k, v in ev["by_source"].items()) + "; n = %d." % N)
    res["inputs_sha256"] = {"plan": PLAN_SHA, "live_json": sha(LIVE), "design_roster.csv": sha(IN / "design_roster.csv"), "group_time_export.csv": sha(IN / "group_time_export.csv"),
                            "policy_keys.csv": sha(IN / "policy_keys.csv"), "autopilot_result_files": result_hashes}
    return res


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    what = args[0] if args else "all"
    OUT.mkdir(parents=True, exist_ok=True)
    for q, fn in (("q2", run_q2), ("q3", run_q3)):
        if what in (q, "all"):
            tgt = OUT / f"RESULTS_{q.upper()}.json"
            if tgt.exists() and not force:
                raise SystemExit(f"{tgt} exists; use --force")
            r = fn()
            json.dump(jclean(r), open(tgt, "w"), indent=1)
            print(q, r["verdict"], json.dumps(jclean(r["plain"]), indent=1))
