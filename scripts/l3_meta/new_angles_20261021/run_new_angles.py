#!/usr/bin/env python3
"""T232 runner (EXPLORATORY, EX-1). Plan: docs/plans/l3-meta/PREPLAN_T232_20261021.md. Reads data/l3_meta/new_angles_20261021/inputs/*.csv (made by
extract_inputs.py, map_states.py, policy_keys.py) and, for the Q2 placebo reference, the stored result files of 29 designs (read-only).
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_20261021/run_new_angles.py q1|q2|q5|all
Writes data/l3_meta/new_angles_20261021/{q1,q2,q5}_*.csv and RESULTS_Q?.json (write-once per run name unless --force)."""
import sys, csv, json, math, collections, hashlib, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import numpy as np
import na_core as C

ROOT = HERE.parents[2]
IN = ROOT / "data/l3_meta/new_angles_20261021/inputs"
OUT = ROOT / "data/l3_meta/new_angles_20261021"
LIVE = ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"
SY = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
SEED = 20261021
NPERM_Q1, NPERM_Q2, NPERM_Q5 = 5000, 5000, 2000


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def load():
    ros = {r["design_id"]: r for r in csv.DictReader(open(IN / "design_roster.csv"))}
    keys = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(IN / "policy_keys.csv"))}
    gt = collections.defaultdict(list)
    for r in csv.DictReader(open(IN / "group_time_export.csv")):
        gt[r["design_id"]].append(r)
    es = collections.defaultdict(list)
    for r in csv.DictReader(open(IN / "event_study_export.csv")):
        es[r["design_id"]].append(r)
    cs = collections.defaultdict(dict)
    for r in csv.DictReader(open(IN / "cohort_states.csv")):
        cs[r["design_id"]][int(r["g"])] = r
    D = {}
    for did, r in ros.items():
        rows = [(int(x["g"]), int(float(x["event_time"])), fnum(x["att"]), fnum(x["n_treated"]), x["post"] == "True") for x in gt[did]]
        sd = fnum(r["noise_sd"]); cut = fnum(r["noise_cutoff"])
        pre = [a for g, e, a, n, p in rows if e <= -2 and a is not None]
        s2 = float(np.std(pre, ddof=1)) if len(pre) > 3 else None
        sign = C.design_sign([(e, a, n) for g, e, a, n, p in rows])
        D[did] = dict(id=did, domain=r["domain"], law=keys[did], sd=sd, cut=cut, s2=s2, rows=rows, sign=sign,
                      overall=fnum(r["overall_att"]), es=es[did], cs=cs[did], source_group=r["source_group"],
                      calendar=min(g for g, *_ in rows) >= 1900 and min(g for g, *_ in rows) < 8000,
                      quarterly=min(g for g, *_ in rows) >= 8000)
    return D


def cohorts_of(d, scale_mode="main"):
    sc = d["sd"] if (scale_mode == "main" and d["sd"]) else d["s2"]
    if not sc or d["sign"] == 0 or d["quarterly"]:
        return None
    return C.build_cohorts([(g, e, a, n) for g, e, a, n, p in d["rows"]], sc, d["sign"])


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
        w = csv.DictWriter(f, fieldnames=keys, restval=''); w.writeheader(); w.writerows(rows)


def jclean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): jclean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jclean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return jclean(o.item())
    return o


# ================================================================ Q1
def era_year(d, g_list):
    ys = []
    for g in g_list:
        r = d["cs"].get(g)
        if r and r["onset_years"]:
            v = [int(x) for x in r["onset_years"].split("|") if x not in ("", "None")]
            if v:
                ys.append(float(np.median(v))); continue
        if 1900 <= g < 8000:
            ys.append(float(g - 1))
    return float(np.median(ys)) if ys else None


def q1_setup(D, scale_mode):
    out = {}
    for did, d in D.items():
        coh = cohorts_of(d, scale_mode)
        if not coh:
            continue
        el = C.eligible(coh, range(4))
        gs = sorted(el)
        if len(gs) < 2:
            continue
        y = np.array([C.window_mean(el[g], range(4)) for g in gs])
        n = np.array([el[g]["n"] for g in gs], float)
        out[did] = dict(g=gs, y=y, n=n, era=era_year(d, gs))
    return out


def run_q1(D, force=False):
    rng = np.random.default_rng(SEED + 1)
    res = {"question": "Q1 pioneers vs copycats", "n_perm": NPERM_Q1, "seed": SEED + 1}
    S = {m: q1_setup(D, m) for m in ("main", "s2")}
    perm_orders = {}
    base = S["main"]
    # real
    rows = []
    for did, s in base.items():
        r = C.early_late_diff(s["y"], s["n"])
        d = D[did]
        row = dict(design_id=did, domain=d["domain"], law=d["law"], n_cohorts=len(s["g"]), era_year=s["era"])
        if r:
            row.update(D=r["D"], early=r["early"], late=r["late"], n_early=r["n_early"], n_late=r["n_late"], same_sign=int(r["early"] * r["late"] > 0))
        rows.append(row)
    valid = [r for r in rows if "D" in r]
    res["n_designs_with_eligible_cohorts"] = len(rows)
    res["n_designs_valid"] = len(valid)
    if len(valid) < 15:
        res["result"] = f"too few ({len(valid)})"
        return res, rows
    ids = [r["design_id"] for r in valid]
    laws = [D[i]["law"] for i in ids]
    Dreal = np.array([r["D"] for r in valid])
    # permutations (same shuffles for the main scale reused for every variant of that design)
    NB = NPERM_Q1
    Dperm = np.full((NB, len(ids)), np.nan)
    for b in range(NB):
        for j, did in enumerate(ids):
            s = base[did]
            order = rng.permutation(len(s["g"]))
            r = C.early_late_diff(s["y"], s["n"], order)
            if r:
                Dperm[b, j] = r["D"]

    def stats(mask_idx, scale="D"):
        sh, md, n = C.pooled_share_median(Dreal[mask_idx])
        nsh = np.array([C.pooled_share_median(Dperm[b, mask_idx])[0] for b in range(NB)])
        nmd = np.array([C.pooled_share_median(Dperm[b, mask_idx])[1] for b in range(NB)])
        return dict(n=n, share_late_better=sh, median_D=md, null_share_band=C.band(nsh), null_median_band=C.band(nmd),
                    null_share_mean=float(np.nanmean(nsh)), null_median_mean=float(np.nanmean(nmd)),
                    p_share=C.two_sided_p(sh, nsh), p_median=C.two_sided_p(md, nmd))

    allidx = np.arange(len(ids))
    res["_null"] = dict(share=[C.pooled_share_median(Dperm[b])[0] for b in range(NB)], median=[C.pooled_share_median(Dperm[b])[1] for b in range(NB)])
    res["primary"] = stats(allidx)
    p = res["primary"]
    res["primary"]["outside_both_bands"] = bool((p["share_late_better"] < p["null_share_band"][0] or p["share_late_better"] > p["null_share_band"][1]) and
                                                 (p["median_D"] < p["null_median_band"][0] or p["median_D"] > p["null_median_band"][1]))
    dpos = float(np.mean(Dreal > 0)); dneg = float(np.mean(Dreal < 0))
    res["same_sign_share_of_designs_in_majority_direction"] = max(dpos, dneg)
    res["direction"] = "late_better" if dpos > dneg else ("early_better" if dneg > dpos else "tie")
    res["share_both_groups_same_sign"] = float(np.mean([r["same_sign"] for r in valid]))
    res["median_n_early_states"] = float(np.median([r["n_early"] for r in valid])); res["median_n_late_states"] = float(np.median([r["n_late"] for r in valid]))
    # >= 3 eligible cohorts
    big = np.array([len(base[i]["g"]) >= 3 for i in ids])
    res["ge3_cohorts"] = stats(np.where(big)[0]) if big.sum() >= 15 else f"too few ({int(big.sum())})"
    # one vote per law
    ul = sorted(set(laws))
    L = np.array([[np.nanmean(Dperm[b, [k for k, l in enumerate(laws) if l == u]]) if np.isfinite(Dperm[b, [k for k, l in enumerate(laws) if l == u]]).any() else np.nan for u in ul] for b in range(NB)])
    Lreal = np.array([np.mean([Dreal[k] for k, l in enumerate(laws) if l == u]) for u in ul])
    sh, md, n = C.pooled_share_median(Lreal)
    nsh = np.array([C.pooled_share_median(L[b])[0] for b in range(NB)]); nmd = np.array([C.pooled_share_median(L[b])[1] for b in range(NB)])
    res["one_vote_per_law"] = dict(n=n, share_late_better=sh, median_D=md, null_share_band=C.band(nsh), null_median_band=C.band(nmd), p_share=C.two_sided_p(sh, nsh), p_median=C.two_sided_p(md, nmd))
    # leave-one-law-out
    lofo = []
    for u in ul:
        keep = np.array([l != u for l in laws])
        if keep.sum() < 10:
            continue
        st = stats(np.where(keep)[0])
        lofo.append(dict(left_out=u, n=st["n"], share=st["share_late_better"], median=st["median_D"], p_share=st["p_share"], p_median=st["p_median"]))
    res["leave_one_law_out"] = dict(
        n_replicates=len(lofo),
        share_min=min(x["share"] for x in lofo), share_max=max(x["share"] for x in lofo),
        median_min=min(x["median"] for x in lofo), median_max=max(x["median"] for x in lofo),
        max_p_share=max(x["p_share"] for x in lofo), max_p_median=max(x["p_median"] for x in lofo),
        direction_kept_all=all((x["share"] > 0.5) == (p["share_late_better"] > 0.5) for x in lofo))
    # leave one domain out
    dom = [D[i]["domain"] for i in ids]
    lodo = []
    for u in sorted(set(dom)):
        keep = np.array([x != u for x in dom])
        if keep.sum() < 10:
            continue
        st = stats(np.where(keep)[0])
        lodo.append(dict(left_out=u, n=st["n"], share=st["share_late_better"], median=st["median_D"], p_share=st["p_share"], p_median=st["p_median"]))
    res["leave_one_domain_out"] = lodo
    # by domain (descriptive, min 5)
    bydom = {}
    for u in sorted(set(dom)):
        idx = np.where(np.array([x == u for x in dom]))[0]
        if len(idx) >= 5:
            bydom[u] = dict(n=len(idx), share_late_better=float(np.mean(Dreal[idx] > 0)), median_D=float(np.median(Dreal[idx])))
        else:
            bydom[u] = f"too few ({len(idx)})"
    res["by_domain"] = bydom
    # EXTRA (not in the plan): is the spread of domain medians larger than label shuffling gives? (domains with >= 5 designs)
    big_dom = [u for u in sorted(set(dom)) if dom.count(u) >= 5]
    sel = np.array([x in big_dom for x in dom])
    if len(big_dom) >= 3:
        Dsel = Dreal[sel]; dsel = np.array(dom)[sel]
        spread = lambda dv, lab: float(np.std([np.median(dv[lab == u]) for u in big_dom], ddof=0))
        obs_sp = spread(Dsel, dsel)
        rng_x = np.random.default_rng(SEED + 11)
        nl = np.array([spread(Dsel, rng_x.permutation(dsel)) for _ in range(5000)])
        res["extra_domain_spread"] = dict(domains=big_dom, observed_sd_of_domain_medians=obs_sp, shuffle_p_upper=float((1 + np.sum(nl >= obs_sp)) / (1 + len(nl))), note="not in PREPLAN; post hoc check")
    # era
    era = np.array([base[i]["era"] if base[i]["era"] is not None else np.nan for i in ids])
    for lab, m in (("era_before_2005", era < 2005), ("era_2005_or_later", era >= 2005)):
        idx = np.where(m)[0]
        res[lab] = dict(n=len(idx), share_late_better=float(np.mean(Dreal[idx] > 0)), median_D=float(np.median(Dreal[idx]))) if len(idx) >= 8 else f"too few ({len(idx)})"
    # S2 scale sensitivity
    s2 = S["s2"]
    D2 = []
    for did in ids:
        if did in s2:
            r = C.early_late_diff(s2[did]["y"], s2[did]["n"])
            D2.append(r["D"] if r else np.nan)
        else:
            D2.append(np.nan)
    D2 = np.array(D2)
    sh2, md2, n2 = C.pooled_share_median(D2)
    res["scale_S2"] = dict(n=n2, share_late_better=sh2, median_D=md2)
    for r in valid:
        j = ids.index(r["design_id"]); r["D_S2"] = float(D2[j]) if np.isfinite(D2[j]) else None
        r["perm_p"] = float((1 + np.sum(np.abs(Dperm[:, j][np.isfinite(Dperm[:, j])] - np.nanmean(Dperm[:, j])) >= abs(r["D"] - np.nanmean(Dperm[:, j])))) / (1 + np.isfinite(Dperm[:, j]).sum()))
    res["designs_with_perm_p_le_05"] = int(sum(1 for r in valid if r["perm_p"] <= 0.05))
    res["pattern_rule"] = dict(
        outside_both_bands=res["primary"]["outside_both_bands"],
        majority_share_ge_60=res["same_sign_share_of_designs_in_majority_direction"] >= 0.60,
        lofo_direction_kept=res["leave_one_law_out"]["direction_kept_all"],
        lofo_max_p_below_10=max(res["leave_one_law_out"]["max_p_share"], res["leave_one_law_out"]["max_p_median"]) < 0.10)
    res["pattern"] = bool(all(res["pattern_rule"].values()))
    return res, rows


# ================================================================ Q2
def q2_label(d, scale_mode="main"):
    coh = cohorts_of(d, scale_mode)
    if coh is None or not d["sd"] or not d["cut"]:
        return None
    w = C.early_late_windows(coh)
    if w is None:
        return None
    a, b, n, k = w
    c = d["cut"] / d["sd"] if scale_mode == "main" else None
    return a, b, n, k, c


def placebo_labels(d, scale):
    """label per placebo draw (list of str or None) from l3_export_v2.placebo_profiles of the stored result."""
    live = json.load(open(LIVE))
    x = next(x for x in live["designs"] if x["design_id"] == d["id"])
    j = json.load(open(ROOT / x["source"]["path"]))
    pp = (j.get("l3_export_v2") or {}).get("placebo_profiles")
    if not pp:
        return None
    c = d["cut"] / d["sd"]
    labs = []
    for pr in pp:
        rows = [(r["g"], r["event_time"], r["att"], r["n_treated"]) for r in pr["group_time"]]
        sg = C.design_sign([(e, a, n) for g, e, a, n in rows])
        if sg == 0:
            labs.append(None); continue
        coh = C.build_cohorts(rows, d["sd"], sg)
        w = C.early_late_windows(coh)
        labs.append(C.classify(w[0], w[1], c) if w else None)
    return labs


def run_q2(D, alt=False):
    rng = np.random.default_rng(SEED + 2)
    res = {"question": "Q2 fade/hold/grow", "n_perm": NPERM_Q2, "seed": SEED + 2}
    rows = []
    for did, d in D.items():
        r = q2_label(d)
        row = dict(design_id=did, domain=d["domain"], law=d["law"])
        if r is None:
            row.update(label="", reason="no_noise_scale_or_no_balanced_cohort_0to5")
        else:
            a, b, n, k, c = r
            row.update(early=a, late=b, n_states=n, n_cohorts=k, cutoff=c, label=C.classify(a, b, c), ratio=(b / a if abs(a) > 1e-9 else None))
        rows.append(row)
    el = [r for r in rows if r["label"]]
    res["n_designs"] = len(D); res["n_eligible"] = len(el)
    if len(el) < 20:
        res["overall"] = f"too few ({len(el)})"
        return res, rows
    labels = ["fade", "hold", "grow", "unclear"]
    res["overall_counts"] = {l: sum(1 for r in el if r["label"] == l) for l in labels}
    doms = sorted({r["domain"] for r in el})
    res["by_domain_counts"] = {u: {l: sum(1 for r in el if r["domain"] == u and r["label"] == l) for l in labels} | {"n": sum(1 for r in el if r["domain"] == u)} for u in doms}
    # shuffle of domain labels
    lab = np.array([r["label"] for r in el]); dom = np.array([r["domain"] for r in el])
    reported = [u for u in doms if (dom == u).sum() >= 5]
    res["domains_reported"] = reported
    res["domains_too_few"] = {u: int((dom == u).sum()) for u in doms if (dom == u).sum() < 5}
    obs = {u: {l: float(np.mean(lab[dom == u] == l)) for l in ("fade", "grow", "hold", "unclear")} for u in reported}
    nullc = {u: {l: [] for l in ("fade", "grow")} for u in reported}
    sizes = {u: int((dom == u).sum()) for u in reported}
    for b in range(NPERM_Q2):
        pd = rng.permutation(dom)
        for u in reported:
            m = pd == u
            for l in ("fade", "grow"):
                nullc[u][l].append(float(np.mean(lab[m] == l)))
    res["by_domain"] = {}
    for u in reported:
        e = {"n": sizes[u], "shares": obs[u]}
        for l in ("fade", "grow"):
            nl = np.array(nullc[u][l])
            e[f"{l}_shuffle_p_upper"] = float((1 + np.sum(nl >= obs[u][l] - 1e-12)) / (1 + len(nl)))
            e[f"{l}_shuffle_p_lower"] = float((1 + np.sum(nl <= obs[u][l] + 1e-12)) / (1 + len(nl)))
        res["by_domain"][u] = e
    # placebo reference (29 designs with stored placebo group_time draws)
    pl = {}
    for did, d in D.items():
        if not alt and d["sd"] and d["cut"] and d["source_group"] == "autopilot_estimate":
            labs = placebo_labels(d, d["sd"])
            if labs:
                pl[did] = labs
    res["n_designs_with_placebo_draws"] = len(pl)
    if pl:
        ids = [i for i in pl if next((r for r in el if r["design_id"] == i), None)]
        real = {r["design_id"]: r["label"] for r in el}
        nd = len(pl[ids[0]])
        res["placebo"] = {}
        for scope, sel in [("all_with_draws", ids)] + [(u, [i for i in ids if D[i]["domain"] == u]) for u in sorted({D[i]["domain"] for i in ids})]:
            if len(sel) < 5:
                res["placebo"][scope] = f"too few ({len(sel)})"
                continue
            nd = min(len(pl[i]) for i in sel)
            e = {"n": len(sel), "n_draws": nd, "draws_per_design": {i: len(pl[i]) for i in sel} if scope == 'all_with_draws' else None}
            for l in labels:
                rs = float(np.mean([real[i] == l for i in sel]))
                null = []
                for b in range(nd):
                    v = [pl[i][b] for i in sel if pl[i][b] is not None]
                    null.append(float(np.mean([x == l for x in v])) if v else float("nan"))
                null = np.array(null)
                e[l] = dict(real=rs, placebo_mean=float(np.nanmean(null)), placebo_band_5_95=C.band(null, 5, 95), p_two_sided=C.two_sided_p(rs, null))
            res["placebo"][scope] = e
    # one vote per law: average label shares by law -> law-level modal label counts
    byl = collections.defaultdict(list)
    for r in el:
        byl[r["law"]].append(r["label"])
    lawcount = collections.Counter()
    for l, v in byl.items():
        c = collections.Counter(v).most_common()
        top = [x for x, n in c if n == c[0][1]]
        for t in top:
            lawcount[t] += 1 / len(top)
    res["one_vote_per_law_counts"] = dict(n_laws=len(byl), **{k: float(v) for k, v in lawcount.items()})
    # pattern evaluation per domain (plan section 2): shuffle p < .05, beyond placebo band (needs >= 5 designs with draws), one vote per law (law-level shuffle),
    # same direction (grow > fade, or fade > grow) in >= 60 percent of reported domains, leave-one-law-out keeps domain share above the rest of the sample
    law_of = {r["design_id"]: r["law"] for r in el}
    laws_u = sorted({r["law"] for r in el})
    law_lab = {}   # law -> (domain of majority, label share dict)
    pat = {}
    for l in (("fade", "grow") if not alt else ()):
        dir_dom = [u for u in reported if res["by_domain"][u]["shares"][l] > res["by_domain"][u]["shares"]["grow" if l == "fade" else "fade"]]
        share_dirs = len(dir_dom) / len(reported)
        for u in reported:
            e = res["by_domain"][u]
            sh = e["shares"][l]
            ok_shuffle = e[f"{l}_shuffle_p_upper"] < 0.05
            pb = res.get("placebo", {}).get(u)
            if isinstance(pb, dict):
                ok_placebo = sh > pb[l]["placebo_band_5_95"][1]
                placebo_note = f"real {pb[l]['real']:.3f} vs band {pb[l]['placebo_band_5_95']}"
            else:
                ok_placebo = False
                placebo_note = f"not testable ({pb if pb else 'no draws'})"
            # one vote per law inside domain: a law counts with the share of its in-domain designs carrying the label
            in_dom = [r for r in el if r["domain"] == u]
            lawsd = sorted({r["law"] for r in in_dom})
            law_share = np.mean([np.mean([r["label"] == l for r in in_dom if r["law"] == q]) for q in lawsd])
            # law-level shuffle: all laws, each with label share vector over its designs (domain of the law = its first design's domain)
            lawtab = {}
            for r in el:
                lawtab.setdefault(r["law"], []).append(r)
            lw = [(q, v[0]["domain"], float(np.mean([x["label"] == l for x in v]))) for q, v in lawtab.items()]
            ld = np.array([x[1] for x in lw]); lv = np.array([x[2] for x in lw])
            obs_l = float(np.mean(lv[ld == u]))
            nulls = np.array([np.mean(lv[rng.permutation(ld) == u]) for _ in range(2000)])
            p_law = float((1 + np.sum(nulls >= obs_l - 1e-12)) / (1 + len(nulls)))
            ok_law = p_law < 0.05
            # leave-one-law-out
            rest_ok = True
            for q in lawsd:
                keep = [r for r in el if r["law"] != q]
                du = [r for r in keep if r["domain"] == u]
                ot = [r for r in keep if r["domain"] != u]
                if not du or not ot:
                    continue
                if np.mean([r["label"] == l for r in du]) <= np.mean([r["label"] == l for r in ot]):
                    rest_ok = False
            pat[f"{u}:{l}"] = dict(n=e["n"], share=sh, shuffle_p_upper=e[f"{l}_shuffle_p_upper"], placebo=placebo_note, beyond_placebo=bool(ok_placebo),
                                   law_level_share=float(obs_l), law_level_shuffle_p=p_law, directional_share_of_domains=share_dirs,
                                   leave_one_law_out_keeps_above_rest=bool(rest_ok),
                                   PATTERN=bool(ok_shuffle and ok_placebo and ok_law and share_dirs >= 0.6 and rest_ok))
    res["pattern_by_domain"] = pat
    # ratio among designs with effect
    rat = [r["ratio"] for r in el if r["label"] != "unclear" and r.get("ratio") is not None]
    res["median_late_over_early_ratio_among_effect_designs"] = float(np.median(rat)) if rat else None
    res["n_effect_designs"] = len(rat)
    # S2 sensitivity not defined for the cutoff (c) -> reported as not run
    # leave-one-law-out of the overall fade share
    allf = np.mean(lab == "fade")
    lof = []
    for u in sorted(byl):
        keep = np.array([r["law"] != u for r in el])
        lof.append(float(np.mean(lab[keep] == "fade")))
    res["overall_fade_share"] = float(allf); res["overall_grow_share"] = float(np.mean(lab == "grow")); res["overall_hold_share"] = float(np.mean(lab == "hold"))
    res["leave_one_law_out_fade_share_range"] = [min(lof), max(lof)]
    return res, rows


# ================================================================ T227 robustness (alternative comparison pool)
def load_alt(D):
    rows = collections.defaultdict(list)
    for r in csv.DictReader(open(IN / "alt_group_time_export.csv")):
        rows[r["design_id"]].append((int(r["g"]), int(float(r["event_time"])), fnum(r["att"]), fnum(r["n_treated"]), r["post"] == "True"))
    DA = {}
    for did, rs in rows.items():
        if did not in D:
            continue
        d = dict(D[did]); d["rows"] = rs
        d["sign"] = C.design_sign([(e, a, n) for g, e, a, n, p in rs])
        DA[did] = d
    return DA


def run_alt(D):
    DA = load_alt(D)
    out = {"n_designs_with_alt_cells": len(DA), "note": "T227 Callaway-Sant'Anna-style cells with the opposite comparison pool, same scale, same classifier, no placebo reference"}
    r1, rows1 = run_q1(DA)
    r1.pop("_null", None)
    keep = ("n_designs_valid", "primary", "same_sign_share_of_designs_in_majority_direction", "direction", "pattern")
    out["q1"] = {k: r1.get(k) for k in keep}
    r2, rows2 = run_q2(DA, alt=True)
    out["q2"] = {k: r2.get(k) for k in ("n_eligible", "overall_counts", "by_domain_counts", "overall_fade_share", "overall_grow_share", "median_late_over_early_ratio_among_effect_designs")}
    # agreement with the pipeline labels
    base = {r["design_id"]: r["label"] for r in csv.DictReader(open(OUT / "q2_design_level.csv"))}
    alt = {r["design_id"]: r["label"] for r in rows2 if r["label"]}
    both = [i for i in alt if base.get(i)]
    out["q2"]["label_agreement"] = dict(n_both=len(both), same_label=int(sum(base[i] == alt[i] for i in both)),
                                        crosstab={f"{base[i]}->{alt[i]}": 0 for i in both})
    for i in both:
        out["q2"]["label_agreement"]["crosstab"][f"{base[i]}->{alt[i]}"] += 1
    return out, rows1, rows2


if __name__ == "__main__" and False:
    pass


# ================================================================ Q5
def load_sy():
    party, ideo = {}, {}
    for r in csv.DictReader(open(SY)):
        k = (r["state"], int(r["year"]))
        party[k] = r["party_cat_session"]
        v = fnum(r["citizen_ideology"])
        if v is not None:
            ideo[k] = v
    return party, ideo


def q5_setup(D, party, ideo, scale_mode="main", party_lag=0):
    out = []
    for did, d in D.items():
        coh = cohorts_of(d, scale_mode)
        if not coh or not d["cs"]:
            continue
        el = C.eligible(coh, range(4))
        gs = [g for g in sorted(el) if d["cs"].get(g) and d["cs"][g]["matched"] == "1" and d["cs"][g]["states"]]
        if len(gs) < 3:
            continue
        y, w, score, ide, onset_max = [], [], [], [], []
        for g in gs:
            r = d["cs"][g]
            sts = r["states"].split("|"); ons = [int(x) if x not in ("", "None") else None for x in r["onset_years"].split("|")]
            pv, iv = [], []
            for st, on in zip(sts, ons):
                if on is None:
                    continue
                pc = party.get((st, on - party_lag))
                pv.append(1 if pc == "D_unified" else (-1 if pc == "R_unified" else 0))
                if (st, on) in ideo:
                    iv.append(ideo[(st, on)])
            if len(pv) < 1:
                continue
            y.append(C.window_mean(el[g], range(4))); w.append(el[g]["n"])
            score.append(float(np.mean(pv))); ide.append(float(np.mean(iv)) if iv else np.nan)
            onset_max.append(max(o for o in ons if o is not None))
        if len(y) < 3:
            continue
        y, w, score, ide, onset_max = map(np.array, (y, w, score, ide, onset_max))
        labA = np.where(score >= 0.5, 1, np.where(score <= -0.5, -1, 0))
        labB = C.halves(ide)
        # C: distance from early-third cohorts' ideology; early-third cohorts themselves excluded from the comparison
        gr = C.third_groups(w)
        labC = np.zeros(len(y), int)
        if gr is not None and np.isfinite(ide).sum() >= 3:
            e, _ = gr
            ref = np.nansum(ide[e] * w[e]) / np.sum(w[e][np.isfinite(ide[e])]) if np.isfinite(ide[e]).any() else np.nan
            if np.isfinite(ref):
                dist = np.abs(ide - ref)
                dist[e] = np.nan
                labC = -C.halves(dist)          # +1 = farther? halves gives +1 for top (farther) -> keep: farther=+1
                labC = C.halves(dist)
        out.append(dict(id=did, law=d["law"], domain=d["domain"], y=y, w=w, labA=labA, labB=labB, labC=labC, onset_max=onset_max, n_cohorts=len(y)))
    return out


def contrast_run(data, lab_key, rng, nperm, drop_late_onset=False, min_designs=12):
    dd = []
    for x in data:
        lab = x[lab_key].copy()
        if drop_late_onset:
            lab = np.where(x["onset_max"] > 2016, 0, lab)
        dd.append(dict(id=x["id"], law=x["law"], domain=x["domain"], y=x["y"], w=x["w"], lab=lab))
    terms = [C.design_terms(d["y"], d["w"], d["lab"]) for d in dd]
    have = [i for i, t in enumerate(terms) if t is not None]
    npos = sum(int((dd[i]["lab"] == 1).sum()) for i in have); nneg = sum(int((dd[i]["lab"] == -1).sum()) for i in have)
    res = dict(n_designs=len(have), n_cohorts_class_plus=npos, n_cohorts_class_minus=nneg)
    if len(have) < min_designs or npos < 4 or nneg < 4:
        res["result"] = f"too few ({len(have)} designs, {npos}/{nneg} cohorts)"
        return res
    obs = C.pooled_from_terms(terms)
    per = np.array([terms[i][4] for i in have])
    laws = [dd[i]["law"] for i in have]
    ul = sorted(set(laws))
    def law_mean(pv):
        return float(np.mean([np.mean([pv[k] for k, l in enumerate(laws) if l == u]) for u in ul]))
    obs_law = law_mean(per)
    null = np.empty(nperm); null_law = np.empty(nperm); null_per_sign = np.empty(nperm)
    NP = np.empty((nperm, len(have)))
    for b in range(nperm):
        tt = []
        for i in have:
            lab = rng.permutation(dd[i]["lab"])
            tt.append(C.design_terms(dd[i]["y"], dd[i]["w"], lab))
        null[b] = C.pooled_from_terms(tt)
        pr = [t[4] for t in tt]
        NP[b] = pr
        null_law[b] = law_mean(pr)
    pos = float(np.mean(per > 0)); neg = float(np.mean(per < 0))
    res.update(pooled_diff=obs, null_mean=float(null.mean()), null_band=C.band(null), p_two_sided=C.two_sided_p(obs, null),
               outside_band=bool(obs < C.band(null)[0] or obs > C.band(null)[1]),
               design_level_median=float(np.median(per)), share_positive=pos, share_negative=neg,
               majority_share=max(pos, neg), one_vote_per_law=dict(n_laws=len(ul), mean_diff=obs_law, null_band=C.band(null_law), p_two_sided=C.two_sided_p(obs_law, null_law)))
    # leave-one-law-out (pooled estimate recomputed; same permutation structure, null from the stored per-design draws is not additive, so recompute with fresh draws)
    lofo = []
    rng2 = np.random.default_rng(int(rng.integers(1, 2**31)))
    for u in ul:
        idx = [i for i in have if dd[i]["law"] != u]
        if len(idx) < 8:
            continue
        o = C.pooled_from_terms([terms[i] for i in idx])
        nl = np.array([C.pooled_from_terms([C.design_terms(dd[i]["y"], dd[i]["w"], rng2.permutation(dd[i]["lab"])) for i in idx]) for _ in range(300)])
        lofo.append(dict(left_out=u, diff=float(o), p=C.two_sided_p(o, nl)))
    res["leave_one_law_out"] = dict(n=len(lofo), min=min(x["diff"] for x in lofo), max=max(x["diff"] for x in lofo), max_p=max(x["p"] for x in lofo),
                                    direction_kept=all(np.sign(x["diff"]) == np.sign(obs) for x in lofo))
    res["pattern_rule"] = dict(outside_band=res["outside_band"], majority_ge_two_thirds=res["majority_share"] >= 2 / 3,
                               lofo_direction_kept=res["leave_one_law_out"]["direction_kept"], lofo_max_p_below_10=res["leave_one_law_out"]["max_p"] < 0.10)
    res["pattern"] = bool(all(res["pattern_rule"].values()))
    res["per_design"] = {dd[i]["id"]: float(terms[i][4]) for i in have}
    return res


def run_q5(D):
    party, ideo = load_sy()
    res = {"question": "Q5 party/ideology vs delivery within design", "n_perm": NPERM_Q5, "seed": SEED + 5}
    rng = np.random.default_rng(SEED + 5)
    data = q5_setup(D, party, ideo)
    res["n_designs_with_state_map_and_ge3_cohorts"] = len(data)
    res["n_cohorts_total"] = int(sum(x["n_cohorts"] for x in data))
    res["class_counts_A"] = {"D": int(sum((x["labA"] == 1).sum() for x in data)), "R": int(sum((x["labA"] == -1).sum() for x in data)), "mixed": int(sum((x["labA"] == 0).sum() for x in data))}
    names = {"A": ("labA", "unified D (+) vs unified R (-)"), "B": ("labB", "more liberal half (+) vs less liberal half (-)"), "C": ("labC", "farther from early adopters' ideology (+) vs closer (-)")}
    for k, (lk, desc) in names.items():
        res[f"cut_{k}"] = dict(description=desc, primary=contrast_run(data, lk, rng, NPERM_Q5))
    # sensitivities
    res["cut_A"]["party_lag1"] = contrast_run(q5_setup(D, party, ideo, party_lag=1), "labA", rng, NPERM_Q5)
    res["cut_A"]["scale_S2"] = contrast_run(q5_setup(D, party, ideo, scale_mode="s2"), "labA", rng, NPERM_Q5)
    for k in ("B", "C"):
        res[f"cut_{k}"]["drop_onset_after_2016"] = contrast_run(data, names[k][0], rng, NPERM_Q5, drop_late_onset=True)
        res[f"cut_{k}"]["scale_S2"] = contrast_run(q5_setup(D, party, ideo, scale_mode="s2"), names[k][0], rng, NPERM_Q5)
    rows = [dict(design_id=x["id"], law=x["law"], domain=x["domain"], n_cohorts=x["n_cohorts"], D_cohorts=int((x["labA"] == 1).sum()), R_cohorts=int((x["labA"] == -1).sum()),
                 per_design_diff_A=res["cut_A"]["primary"].get("per_design", {}).get(x["id"]), per_design_diff_B=res["cut_B"]["primary"].get("per_design", {}).get(x["id"]),
                 per_design_diff_C=res["cut_C"]["primary"].get("per_design", {}).get(x["id"])) for x in data]
    return res, rows


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    D = load()
    t0 = time.time()
    OUT.mkdir(exist_ok=True, parents=True)
    stamp = dict(rule_id="EX-1", plan="docs/plans/l3-meta/PREPLAN_T232_20261021.md", plan_sha256=sha(ROOT / "docs/plans/l3-meta/PREPLAN_T232_20261021.md"),
                 code_sha256={f: sha(HERE / f) for f in ("na_core.py", "run_new_angles.py")}, run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    if what in ("q1", "all"):
        r, rows = run_q1(D)
        nul = r.pop("_null", None)
        if nul:
            write_csv(OUT / "q1_null_draws.csv", [dict(draw=i, share=a, median=b) for i, (a, b) in enumerate(zip(nul["share"], nul["median"]))])
        write_csv(OUT / "q1_design_level.csv", rows)
        (OUT / "RESULTS_Q1.json").write_text(json.dumps(jclean(dict(stamp=stamp, result=r)), indent=1, allow_nan=False))
        print("Q1 done", round(time.time() - t0, 1), "s")
    if what in ("q2", "all"):
        r, rows = run_q2(D)
        write_csv(OUT / "q2_design_level.csv", rows)
        (OUT / "RESULTS_Q2.json").write_text(json.dumps(jclean(dict(stamp=stamp, result=r)), indent=1, allow_nan=False))
        print("Q2 done", round(time.time() - t0, 1), "s")
    if what in ("alt", "all"):
        r, rows1, rows2 = run_alt(D)
        write_csv(OUT / "alt_q1_design_level.csv", rows1)
        write_csv(OUT / "alt_q2_design_level.csv", rows2)
        (OUT / "RESULTS_ALT_T227.json").write_text(json.dumps(jclean(dict(stamp=stamp, result=r)), indent=1, allow_nan=False))
        print("ALT done", round(time.time() - t0, 1), "s")
    if what in ("q5", "all"):
        r, rows = run_q5(D)
        write_csv(OUT / "q5_design_level.csv", rows)
        (OUT / "RESULTS_Q5.json").write_text(json.dumps(jclean(dict(stamp=stamp, result=r)), indent=1, allow_nan=False))
        print("Q5 done", round(time.time() - t0, 1), "s")
