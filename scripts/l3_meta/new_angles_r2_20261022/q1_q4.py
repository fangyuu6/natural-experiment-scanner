#!/usr/bin/env python3
"""T256 coder A: Q1 (visible mark by domain) and Q4 (moved the right way). EXPLORATORY (EX-1).
Plan (hash-locked): docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md  sha256 a503c461f99954215ced45f789b1c74def7ceab635b5a5c9b33dfcd0f7d5f661
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_r2_20261022/q1_q4.py check|q1|q4|all [--force]
Writes data/l3_meta/new_angles_r2_20261022/{q1,q4}_*.csv and RESULTS_Q1.json / RESULTS_Q4.json (write-once unless --force)."""
import sys, csv, json, math, hashlib, collections
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
import numpy as np
from scipy import stats

ROOT = HERE.parents[2]
IN = ROOT / "data/l3_meta/new_angles_20261021/inputs"
CODES = ROOT / "data/l3_meta/new_angles_20261021/states_decide/mapping/FINAL_codes.csv"
LIVE = ROOT / "data/l3_meta/refresh/20261022_r2/live/l2_set_live.json"
PLAN = ROOT / "docs/plans/l3-meta/PREPLAN_T256_R2_20261022.md"
PLAN_SHA = "a503c461f99954215ced45f789b1c74def7ceab635b5a5c9b33dfcd0f7d5f661"
OUT = ROOT / "data/l3_meta/new_angles_r2_20261022"
SEED = 20261022
NPERM_Q1, NPERM_Q4 = 10000, 10000
DOMAINS = ["health", "labor", "crime", "other", "public_finance", "environment"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def dgroup(dom):
    return "other" if dom == "education" else dom


# ---------------------------------------------------------------- pure functions (unit tested)
def shuffle_within_strata(labels, strata, rng):
    """Permute labels among designs of the same stratum (same group sizes, same stratum composition)."""
    labels = np.asarray(labels); out = labels.copy(); strata = np.asarray(strata)
    for s in np.unique(strata):
        idx = np.where(strata == s)[0]
        out[idx] = labels[idx][rng.permutation(len(idx))]
    return out


def domain_gaps(flag, labels, doms):
    """G_d = share_d - pooled share of all other designs, for each d in doms. flag: float array (0/1 or fractional)."""
    flag = np.asarray(flag, float); labels = np.asarray(labels)
    out = {}
    for d in doms:
        m = labels == d
        if m.sum() == 0 or (~m).sum() == 0:
            out[d] = float("nan"); continue
        out[d] = float(flag[m].mean() - flag[~m].mean())
    return out


def null_gaps(flag, labels, strata, doms, n, rng):
    flag = np.asarray(flag, float)
    mat = np.empty((n, len(doms)))
    for b in range(n):
        lab = shuffle_within_strata(labels, strata, rng)
        g = domain_gaps(flag, lab, doms)
        mat[b] = [g[d] for d in doms]
    return mat


def tercile_label(values):
    """values: dict name->value. lowest tercile / highest tercile by rank (ranks 1..k; low if rank <= k/3, high if rank > 2k/3)."""
    names = sorted(values, key=lambda k: values[k]); k = len(names)
    lab = {}
    for i, nme in enumerate(names):
        r = i + 1
        lab[nme] = "low" if r <= k / 3 else ("high" if r > 2 * k / 3 else "mid")
    return lab


def exact_two_sided(k, n):
    return float(stats.binomtest(int(k), int(n), 0.5, alternative="two-sided").pvalue) if n > 0 else float("nan")


def sign(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


# ---------------------------------------------------------------- loading
def load():
    ros = {r["design_id"]: r for r in csv.DictReader(open(IN / "design_roster.csv"))}
    keys = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(IN / "policy_keys.csv"))}
    dirs = {r["design_id"]: int(r["direction_final"]) for r in csv.DictReader(open(CODES))}
    treated = collections.defaultdict(int)
    for r in csv.DictReader(open(IN / "cohort_states.csv")):
        treated[r["design_id"]] += int(float(r["n_treated_e0"] or 0))
    D = []
    for did, r in ros.items():
        bn = {"True": 1.0, "False": 0.0}.get(r["beyond_noise"])
        D.append(dict(id=did, domain=r["domain"], dg=dgroup(r["domain"]), source=r["source_group"], law=keys[did],
                      sd=fnum(r["noise_sd"]), cut=fnum(r["noise_cutoff"]), att=fnum(r["overall_att"]), bn=bn,
                      n_treated=treated.get(did), dir=dirs[did], family=r["policy_family"]))
    return D


def consistency_check(D):
    live = json.load(open(LIVE))
    est = {x["design_id"]: x for x in live["designs"] if x["status"] == "estimated"}
    ids = {d["id"] for d in D}
    res = dict(n_live_estimated=len(est), n_roster=len(D), same_id_set=(set(est) == ids), mismatches=[])
    for d in D:
        x = est.get(d["id"])
        if x is None:
            res["mismatches"].append((d["id"], "missing in live")); continue
        po = (x.get("primary_outcomes") or [{}])[0]
        if abs((x["point_estimate"] or 0) - (d["att"] or 0)) > 1e-9:
            res["mismatches"].append((d["id"], "point_estimate", x["point_estimate"], d["att"]))
        lbn = po.get("beyond_noise"); lbn = None if lbn is None else float(bool(lbn))
        if lbn != d["bn"]:
            res["mismatches"].append((d["id"], "beyond_noise", lbn, d["bn"]))
        lsd = po.get("noise_sd")
        if (lsd is None) != (d["sd"] is None) or (lsd is not None and abs(lsd - d["sd"]) > 1e-9):
            res["mismatches"].append((d["id"], "noise_sd", lsd, d["sd"]))
    res["ok"] = res["same_id_set"] and not res["mismatches"]
    return res


def inputs_hash():
    f = {"plan": PLAN, "design_roster": IN / "design_roster.csv", "policy_keys": IN / "policy_keys.csv",
         "cohort_states": IN / "cohort_states.csv", "FINAL_codes": CODES, "l2_set_live_r2": LIVE}
    return {k: sha(v) for k, v in f.items()}


def write_csv(path, rows):
    rows = list(rows)
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval=""); w.writeheader(); w.writerows(rows)


def guard_write(path, force):
    if Path(path).exists() and not force:
        raise SystemExit(f"{path} exists; write-once (use --force)")


def one_vote_per_law(D, valfn):
    """law-level mean of valfn over its designs (None values skipped); returns list of (law, domain group, source, value)."""
    byl = collections.defaultdict(list)
    for d in D:
        v = valfn(d)
        if v is not None:
            byl[d["law"]].append((d, v))
    rows = []
    for law, lst in byl.items():
        dg = collections.Counter(x[0]["dg"] for x in lst).most_common(1)[0][0]
        src = collections.Counter(x[0]["source"] for x in lst).most_common(1)[0][0]
        rows.append((law, dg, src, float(np.mean([x[1] for x in lst]))))
    return rows


# ---------------------------------------------------------------- Q1
def q1_core(sub, rng, n, strat=True, doms=DOMAINS):
    """sub: list of designs with bn and sd. Returns per-domain stats incl. null percentiles."""
    flag = np.array([d["bn"] for d in sub]); lab = np.array([d["dg"] for d in sub])
    strata = np.array([d["source"] if strat else "all" for d in sub])
    obs = domain_gaps(flag, lab, doms)
    null = null_gaps(flag, lab, strata, doms, n, rng)
    res = {}
    for j, d in enumerate(doms):
        col = null[:, j]; col = col[np.isfinite(col)]
        res[d] = dict(G=obs[d], p975=float(np.percentile(col, 97.5)) if len(col) else None,
                      p025=float(np.percentile(col, 2.5)) if len(col) else None,
                      null_mean=float(col.mean()) if len(col) else None)
    return res, null


def q1_labels(res, ranked, strict=True):
    out = {}
    for d in ranked:
        r = res[d]
        up = r["G"] > r["p975"] if strict else r["G"] >= r["p975"] - 1e-12
        dn = r["G"] < r["p025"] if strict else r["G"] <= r["p025"] + 1e-12
        if r["G"] >= 0.20 and up:
            out[d] = "visible"
        elif r["G"] <= -0.20 and dn:
            out[d] = "hard to see"
        else:
            out[d] = "no difference"
    return out


def run_q1(D, force):
    guard_write(OUT / "RESULTS_Q1.json", force)
    rng = np.random.default_rng(SEED)
    sub = [d for d in D if d["sd"] is not None and d["bn"] is not None]
    n_all, n_sd = len(D), len(sub)
    cnt = collections.Counter(d["dg"] for d in sub)
    ranked = [d for d in DOMAINS if cnt[d] >= 5]
    unranked = {d: cnt[d] for d in DOMAINS if cnt[d] < 5}
    enough = len(ranked) >= 4
    flag = np.array([d["bn"] for d in sub])
    dom_tab = {}
    for dm in DOMAINS:
        m = [d for d in sub if d["dg"] == dm]
        if not m:
            continue
        dom_tab[dm] = dict(n=len(m), n_beyond=int(sum(d["bn"] for d in m)), share=float(np.mean([d["bn"] for d in m])),
                           median_noise_sd=float(np.median([d["sd"] for d in m])),
                           median_abs_att_over_noise=float(np.median([abs(d["att"]) / d["sd"] for d in m])),
                           median_n_treated=float(np.median([d["n_treated"] for d in m if d["n_treated"] is not None])),
                           ranked=dm in ranked)
    out = dict(question="Q1", plan_sha256=PLAN_SHA, inputs_sha256=inputs_hash(), seed=SEED, n_perm=NPERM_Q1,
               n_designs_roster=n_all, n_designs_with_noise_sd=n_sd,
               excluded_no_noise_sd=[d["id"] for d in D if d["sd"] is None or d["bn"] is None],
               pooled_share=float(flag.mean()), domain_table=dom_tab, ranked=ranked, unranked_below_5=unranked)
    if not enough:
        out["verdict"] = "not enough data"
        out["minimum_check"] = dict(min_designs_per_domain=5, min_ranked_domains=4, ranked_domains=len(ranked),
                                    gap=4 - len(ranked), result="not enough data")
        out["plain"] = ["not enough data", f"Only {len(ranked)} domain groups have 5+ designs (need 4).", f"n={n_sd} designs"]
        json.dump(out, open(OUT / "RESULTS_Q1.json", "w"), indent=1); return out
    out["minimum_check"] = dict(min_designs_per_domain=5, min_ranked_domains=4, ranked_domains=len(ranked), result="met")
    res, null = q1_core(sub, rng, NPERM_Q1, True)
    res_un, _ = q1_core(sub, np.random.default_rng(SEED + 1), NPERM_Q1, False)
    lab = q1_labels(res, ranked); lab_un = q1_labels(res_un, ranked)
    # power labels (ranked domains)
    mn = {d: dom_tab[d]["median_noise_sd"] for d in ranked}
    terc = tercile_label(mn)
    power = {d: ("easy to detect" if terc[d] == "low" else ("low power, not no effect" if terc[d] == "high" else "")) for d in ranked}
    shares = [dom_tab[d]["share"] for d in ranked]; noises = [mn[d] for d in ranked]
    rho = float(stats.spearmanr(shares, noises).statistic)
    power_blocked_literal = bool(rho >= 0.8)
    power_blocked_abs = bool(abs(rho) >= 0.8)       # stricter reading (primary): either direction blocks
    blocked = power_blocked_abs
    out["power"] = dict(terciles_over_ranked_domains=terc, labels=power, spearman_share_vs_median_noise=rho,
                        rule_literal_rho_ge_0_8=power_blocked_literal, rule_abs_rho_ge_0_8_primary=power_blocked_abs,
                        note="median noise_sd is in each outcome's own unit; stricter reading (|rho|>=0.8 blocks) is primary, literal (rho>=0.8) reported")
    final = {d: ("no difference (power, not effect)" if (blocked and lab[d] != "no difference") else lab[d]) for d in ranked}
    # law-level guard
    law_rows = one_vote_per_law(sub, lambda d: d["bn"])
    law_flag = np.array([r[3] for r in law_rows]); law_dom = np.array([r[1] for r in law_rows]); law_src = np.array([r[2] for r in law_rows])
    law_obs = domain_gaps(law_flag, law_dom, ranked)
    law_null = null_gaps(law_flag, law_dom, law_src, ranked, NPERM_Q1, np.random.default_rng(SEED + 2))
    law_guard = {}
    for j, d in enumerate(ranked):
        c = law_null[:, j]; c = c[np.isfinite(c)]
        p975, p025 = float(np.percentile(c, 97.5)), float(np.percentile(c, 2.5))
        g = law_obs[d]
        law_guard[d] = dict(G=g, p975=p975, p025=p025, still_visible=bool(g >= .2 and g > p975), still_hard=bool(g <= -.2 and g < p025))
    # leave-one-law-out (rerun with 2000 draws each)
    lolo = {}
    laws = sorted({d["law"] for d in sub})
    for k, law in enumerate(laws):
        s2 = [d for d in sub if d["law"] != law]
        rs, _ = q1_core(s2, np.random.default_rng(SEED + 100 + k), 2000, True, ranked)
        c2 = collections.Counter(d["dg"] for d in s2)
        lb = q1_labels(rs, [d for d in ranked if c2[d] >= 5])
        lolo[law] = {d: lb.get(d, "unranked") for d in ranked}
    stable_lolo = {d: sorted({v[d] for v in lolo.values()}) for d in ranked}
    # leave-one-domain-out of the pooled share
    lodo = {}
    for k, o in enumerate(ranked):
        s2 = [d for d in sub if d["dg"] != o]
        rs, _ = q1_core(s2, np.random.default_rng(SEED + 500 + k), 5000, True, [d for d in ranked if d != o])
        lodo[o] = q1_labels(rs, [d for d in ranked if d != o])
    # pattern per rule item by item
    items = {}
    for d in ranked:
        r = res[d]
        items[d] = dict(G_ge_plus20=bool(r["G"] >= .2), G_above_p975=bool(r["G"] > r["p975"]),
                        G_le_minus20=bool(r["G"] <= -.2), G_below_p025=bool(r["G"] < r["p025"]),
                        label_stratified=lab[d], label_unstratified_sensitivity=lab_un[d], final_label=final[d],
                        survives_one_vote_per_law=(law_guard[d]["still_visible"] if lab[d] == "visible" else law_guard[d]["still_hard"] if lab[d] == "hard to see" else None),
                        labels_across_leave_one_law_out=stable_lolo[d],
                        labels_across_leave_one_domain_out=sorted({v[d] for o, v in lodo.items() if d in v}))
    out["sensitivity_non_strict_boundary"] = q1_labels(res, ranked, strict=False)
    out.update(stratified=res, unstratified=res_un, law_level_guard=law_guard, leave_one_law_out_label_sets=stable_lolo,
               leave_one_domain_out=lodo, rule_items=items, labels_primary=final)
    claimed = []
    for d in ranked:
        if final[d] in ("visible", "hard to see"):
            it = items[d]
            if it["survives_one_vote_per_law"] is False:
                continue
            claimed.append((d, final[d]))
    out["pattern_domains_before_guards_note"] = "guards are reported; a domain is named in plain only if the rule is met and the one-vote-per-law check does not reverse it"
    out["verdict"] = "pattern" if claimed else "no difference"
    # design level csv / null draws
    rows = []
    for d in sub:
        rows.append(dict(design_id=d["id"], domain_group=d["dg"], source_group=d["source"], law=d["law"], beyond_noise=int(d["bn"]),
                         noise_sd=d["sd"], overall_att=d["att"], abs_att_over_noise=abs(d["att"]) / d["sd"], n_treated=d["n_treated"]))
    write_csv(OUT / "q1_design_level.csv", rows)
    nd = [dict(draw=i, **{f"G_{dm}": float(null[i, j]) for j, dm in enumerate(DOMAINS)}) for i in range(null.shape[0])]
    write_csv(OUT / "q1_null_draws.csv", nd)
    rs = ", ".join(f"{d} {dom_tab[d]['share']*100:.0f}% (n={dom_tab[d]['n']}, gap {res[d]['G']*100:+.0f} pts)" for d in ranked)
    if claimed:
        f1 = "Pattern: " + "; ".join(f"{d} {w}" for d, w in claimed) + " per the plan rule."
    else:
        f1 = ("No domain meets the full rule (gap of 20 points and beyond the shuffle band" +
              ("; power rule blocks naming" if blocked else "") + ").")
    out["plain"] = [out["verdict"], f1, f"Share beyond noise by domain: {rs}; pooled {out['pooled_share']*100:.0f}%; n={n_sd} designs ({n_all - n_sd} without noise_sd)."]
    json.dump(out, open(OUT / "RESULTS_Q1.json", "w"), indent=1, default=str)
    return out


# ---------------------------------------------------------------- Q4
def plac_sign_rate(D):
    """Noise-only reference: for the autopilot designs, the share of random-timing permutation draws (stored noise_floor.permutation_draws)
    whose sign equals direction_final; one result file at a time (read-only)."""
    live = json.load(open(LIVE))
    src = {x["design_id"]: x for x in live["designs"]}
    out = {}
    for d in D:
        x = src.get(d["id"])
        if not x or x["source_group"] != "autopilot_estimate" or d["dir"] == 0:
            continue
        j = json.load(open(ROOT / x["source"]["path"]))
        draws = np.array(j["noise_floor"]["permutation_draws"], float)
        sdraw = float(draws.std(ddof=1))
        out[d["id"]] = dict(n_draws=int(len(draws)), share_aligned=float(np.mean(np.sign(draws) == d["dir"])),
                            draw_sd=sdraw, noise_sd=d["sd"], sd_ratio=(sdraw / d["sd"] if d["sd"] else None))
        del j, draws
    return out


def q4_stats(S):
    """S: list of designs with dir!=0. Returns hit counts."""
    hit = [int(sign(d["att"]) == d["dir"]) for d in S]
    bn = [d for d in S if d["bn"] is not None]
    rb = sum(1 for d in bn if d["bn"] == 1 and sign(d["att"]) == d["dir"])
    wb = sum(1 for d in bn if d["bn"] == 1 and sign(d["att"]) != d["dir"])
    nb = sum(1 for d in bn if d["bn"] == 0)
    return dict(n=len(S), hits=int(sum(hit)), H_all=(sum(hit) / len(S) if S else None), right_bn=rb, wrong_bn=wb, not_distinguishable=nb,
                no_flag=len(S) - len(bn), H_bn=(rb / (rb + wb) if rb + wb else None), n_bn=rb + wb)


def run_q4(D, force):
    guard_write(OUT / "RESULTS_Q4.json", force)
    rng = np.random.default_rng(SEED + 7)
    S = [d for d in D if d["dir"] != 0 and d["att"] is not None]
    zero = [d["id"] for d in D if d["dir"] == 0]
    st = q4_stats(S)
    out = dict(question="Q4", plan_sha256=PLAN_SHA, inputs_sha256=inputs_hash(), seed=SEED + 7, n_sign_flips=NPERM_Q4,
               n_roster=len(D), n_nonzero=len(S), excluded_zero_coded=zero, n_zero_coded=len(zero),
               counts=st)
    mins = dict(min_nonzero=40, min_bn_nonzero=20, nonzero=len(S), bn_nonzero=st["n_bn"],
                nonzero_ok=len(S) >= 40, bn_ok=st["n_bn"] >= 20)
    out["minimum_check"] = mins
    if not mins["nonzero_ok"]:
        out["verdict"] = "not enough data"
        out["plain"] = ["not enough data", f"Only {len(S)} designs have a clear favourable direction (need 40).", f"n={len(S)}"]
        json.dump(out, open(OUT / "RESULTS_Q4.json", "w"), indent=1); return out
    p_all = exact_two_sided(st["hits"], st["n"])
    p_bn = exact_two_sided(st["right_bn"], st["n_bn"]) if mins["bn_ok"] else None
    # sign flips of direction_final: independent fair flip per design
    n = len(S); hits = st["hits"]; obs = abs(hits / n - 0.5)
    k = rng.binomial(1, 0.5, size=(NPERM_Q4, n)).sum(axis=1) if False else None
    att_sign = np.array([sign(d["att"]) for d in S]); dirs = np.array([d["dir"] for d in S])
    cnt = 0; hv = np.empty(NPERM_Q4)
    for b in range(NPERM_Q4):
        flip = rng.choice([-1, 1], size=n)
        hv[b] = np.mean(att_sign == dirs * flip)
    p_flip = float((1 + np.sum(np.abs(hv - 0.5) >= obs - 1e-12)) / (NPERM_Q4 + 1))
    out["tests"] = dict(H_all=st["H_all"], p_binom_two_sided_H_all=p_all, p_signflip_two_sided_H_all=p_flip,
                        signflip_H_mean=float(hv.mean()), signflip_H_p2_5=float(np.percentile(hv, 2.5)), signflip_H_p97_5=float(np.percentile(hv, 97.5)),
                        H_bn=st["H_bn"], p_binom_two_sided_H_bn=p_bn)
    # placebo reference
    pl = plac_sign_rate(D)
    out["placebo_noise_only_reference"] = dict(n_designs=len(pl),
                                               mean_share_aligned=float(np.mean([v["share_aligned"] for v in pl.values()])) if pl else None,
                                               median_sd_ratio_draws_to_noise_sd=float(np.median([v["sd_ratio"] for v in pl.values() if v["sd_ratio"]])) if pl else None,
                                               per_design=pl)
    # precision terciles (noise_sd/|att|), descriptive
    P = [d for d in S if d["sd"] is not None and d["att"] != 0]
    P.sort(key=lambda d: d["sd"] / abs(d["att"]))
    k3 = len(P) / 3; terc = {}
    for nm, lo, hi in (("most_precise", 0, round(k3)), ("middle", round(k3), round(2 * k3)), ("least_precise", round(2 * k3), len(P))):
        g = P[lo:hi]
        terc[nm] = dict(n=len(g), H_all=float(np.mean([sign(d["att"]) == d["dir"] for d in g])) if g else None,
                        noise_over_abs_att_range=[g[0]["sd"] / abs(g[0]["att"]), g[-1]["sd"] / abs(g[-1]["att"])] if g else None)
    out["precision_terciles_descriptive"] = terc
    # domain splits (>=8) and leave-one-domain-out
    dom = {}
    for dm in DOMAINS:
        g = [d for d in S if d["dg"] == dm]
        if len(g) >= 8:
            dom[dm] = q4_stats(g)
    out["domain_splits_ge8"] = dom
    out["domains_below_8_not_split"] = {dm: sum(1 for d in S if d["dg"] == dm) for dm in DOMAINS if 0 < sum(1 for d in S if d["dg"] == dm) < 8}
    lodo = {}
    for o in DOMAINS:
        s2 = [d for d in S if d["dg"] != o]
        if len(s2) == len(S):
            continue
        q = q4_stats(s2)
        lodo[o] = dict(n=q["n"], H_all=q["H_all"], H_bn=q["H_bn"], n_bn=q["n_bn"], sign_H_all_minus_half=sign(q["H_all"] - 0.5),
                       p_binom_H_all=exact_two_sided(q["hits"], q["n"]))
    out["leave_one_domain_out"] = lodo
    same_sign = all(v["sign_H_all_minus_half"] == sign(st["H_all"] - 0.5) for v in lodo.values())
    # leave-one-law-out
    lolo = []
    for law in sorted({d["law"] for d in S}):
        q = q4_stats([d for d in S if d["law"] != law])
        lolo.append((law, q["H_all"], q["H_bn"]))
    out["leave_one_law_out"] = dict(H_all_min=min(x[1] for x in lolo), H_all_max=max(x[1] for x in lolo),
                                    H_bn_min=min(x[2] for x in lolo if x[2] is not None), H_bn_max=max(x[2] for x in lolo if x[2] is not None),
                                    n_laws=len(lolo))
    # one vote per law
    lr = one_vote_per_law(S, lambda d: float(sign(d["att"]) == d["dir"]))
    lh = [r[3] for r in lr]; clear = [x for x in lh if x != 0.5]
    lawbn = one_vote_per_law([d for d in S if d["bn"] == 1], lambda d: float(sign(d["att"]) == d["dir"]))
    lbh = [r[3] for r in lawbn if r[3] != 0.5]
    out["one_vote_per_law"] = dict(n_laws=len(lh), ties_dropped=len(lh) - len(clear), H_all_law=float(np.mean([x > 0.5 for x in clear])) if clear else None,
                                   p_binom=exact_two_sided(sum(1 for x in clear if x > 0.5), len(clear)),
                                   n_laws_bn=len(lbh), H_bn_law=float(np.mean([x > 0.5 for x in lbh])) if lbh else None,
                                   p_binom_bn=exact_two_sided(sum(1 for x in lbh if x > 0.5), len(lbh)) if lbh else None)
    miss = [dict(design_id=d["id"], domain=d["dg"], law=d["law"], overall_att=d["att"], direction_final=d["dir"], noise_sd=d["sd"])
            for d in S if d["bn"] == 1 and sign(d["att"]) != d["dir"]]
    out["miss_list_wrong_way_beyond_noise"] = miss
    # pattern items
    items = dict(H_all_ge_0_60=bool(st["H_all"] >= 0.60), p_H_all_lt_0_05=bool(p_all < 0.05),
                 H_bn_ge_0_65=bool(st["H_bn"] is not None and st["H_bn"] >= 0.65), p_H_bn_lt_0_05=bool(p_bn is not None and p_bn < 0.05),
                 same_sign_in_all_leave_one_domain_out=bool(same_sign),
                 minimum_met=bool(mins["nonzero_ok"] and mins["bn_ok"]))
    out["rule_items"] = items
    if not mins["bn_ok"]:
        out["verdict"] = "not enough data"
        out["minimum_check"]["note"] = f"not enough data for H_bn: {st['n_bn']} beyond-noise non-zero designs, need 20 (gap {20 - st['n_bn']})"
    else:
        out["verdict"] = "pattern" if all(items.values()) else "no difference"
    rows = []
    for d in D:
        c = "zero-coded (excluded)" if d["dir"] == 0 else ("no flag" if d["bn"] is None else ("not distinguishable from noise" if d["bn"] == 0 else ("right and beyond noise" if sign(d["att"]) == d["dir"] else "wrong and beyond noise")))
        rows.append(dict(design_id=d["id"], domain_group=d["dg"], source_group=d["source"], law=d["law"], direction_final=d["dir"], overall_att=d["att"],
                         noise_sd=d["sd"], beyond_noise=d["bn"], hit=(int(sign(d["att"]) == d["dir"]) if d["dir"] != 0 else ""), class_=c))
    write_csv(OUT / "q4_design_level.csv", rows)
    write_csv(OUT / "q4_null_draws.csv", [dict(draw=i, H_all_signflip=float(hv[i])) for i in range(NPERM_Q4)])
    pr = out["placebo_noise_only_reference"]
    out["plain"] = [out["verdict"],
                    ("Pattern: outcomes moved the favourable way more often than not by the plan rule." if out["verdict"] == "pattern" else
                     "Not enough data for the beyond-noise rate." if out["verdict"] == "not enough data" else
                     "The plan rule is not met (all items listed in rule_items)."),
                    f"H_all {st['H_all']*100:.0f}% ({st['hits']}/{st['n']}, p={p_all:.3g}); H_bn " +
                    (f"{st['H_bn']*100:.0f}% ({st['right_bn']}/{st['n_bn']}, p={p_bn:.3g})" if p_bn is not None else f"not enough data ({st['right_bn']}/{st['n_bn']} beyond noise, need 20)") +
                    f"; placebo aligned share {pr['mean_share_aligned']:.2f}; n={st['n']} designs ({len(zero)} zero-coded excluded)"]
    json.dump(out, open(OUT / "RESULTS_Q4.json", "w"), indent=1, default=str)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    what = args[0] if args else "all"
    OUT.mkdir(parents=True, exist_ok=True)
    assert sha(PLAN) == PLAN_SHA, "plan hash mismatch"
    D = load()
    cc = consistency_check(D)
    print("consistency:", json.dumps(cc, default=str))
    json.dump(dict(plan_sha256=sha(PLAN), consistency=cc), open(OUT / "CHECK_SECTION0_coderA.json", "w"), indent=1, default=str)
    if not cc["ok"]:
        raise SystemExit("section 0 consistency check FAILED; stopping")
    if what == "check":
        return
    if what in ("q1", "all"):
        r = run_q1(D, force); print("Q1", r["verdict"], r["plain"])
    if what in ("q4", "all"):
        r = run_q4(D, force); print("Q4", r["verdict"], r["plain"])


if __name__ == "__main__":
    main()
