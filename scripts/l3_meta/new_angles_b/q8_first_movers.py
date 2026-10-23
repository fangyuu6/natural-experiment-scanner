#!/usr/bin/env python3
"""Rank 8 habitual first movers (PREPLAN_T233_20261021 section 4).

Part 1 (L1): state leadership score from the SPID adoption table. Topic split-half
(500 splits), era split, null band, sensitivities, full-sample scores and trait
correlations.
Part 2 (L2): do habitual first movers show larger aligned effects? Per-design
difference, label permutation null, twin-state placebo, leave-one-state-out,
leave-one-block-out, half-habitual cohort rule, block bootstrap for real minus twin.
A plain-python recomputation checks one state score and the Part 2 statistic.

Run:
  .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/q8_first_movers.py
"""
import csv
import hashlib
import json
import math
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = "."
SD = os.path.join(ROOT, "data/l3_meta/new_angles_20261021/states_decide")
INP = os.path.join(SD, "inputs")
MAP_CSV = os.path.join(SD, "mapping/FINAL_design_spid_map.csv")
OUT = os.path.join(SD, "q8")

SEED_SPLIT = [20261022, 8, 1]
SEED_NULL = [20261022, 8, 2]
SEED_PERM = [20261022, 8, 3]
SEED_BOOT = [20261022, 8, 4]
N_SPLITS = 500
N_NULL = 5000
N_PERM = 2000
N_BOOT = 2000
MIN_ADOPT = 10
MIN_TOPIC = 5
MIN_HALF = 20
MIN_POOL = 30
MIN_DESIGNS = 15
MIN_HAB_STATES = 10
MIN_QUAL_STATES = 10
YR0, YR1 = 1962, 2016
ERA_CUT_SENS = 2005
TRAITS = ["ln_inc", "urban", "ideology", "dem_unified"]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pct(a, qs):
    return [float(v) for v in np.percentile(np.asarray(a, float), qs)]


# ---------------------------------------------------------------- Part 1 (L1)

def eligible_rows(spid):
    s = spid[(spid.complete_window == 1) & (spid.date_conflict == 0)].copy()
    cnt = s.groupby("policy")["state"].transform("size")
    s = s[cnt >= MIN_ADOPT].copy()
    s["n"] = s.groupby("policy")["state"].transform("size")
    assert (s["n"] == s["n_adopters"]).all()
    s["rk"] = s.groupby("policy")["adopt_year"].rank(method="average")
    s["o"] = (s["rk"] - 1) / (s["n"] - 1)
    return s


def policy_table(s):
    p = s.groupby("policy").agg(topic=("majortopic_str", "first"),
                                first_year=("first_year", "first"),
                                source=("source", "first"),
                                n=("n", "first")).reset_index()
    cnt = p.topic.value_counts()
    small = set(cnt[cnt < MIN_TOPIC].index)
    p["topic_pool"] = [("other" if t in small else t) for t in p.topic]
    return p


def scores(s, pols):
    r = s[s.policy.isin(list(pols))]
    return r.groupby("state")["o"].agg(["mean", "size"])


def split_rho(s, pa, pb):
    ga = scores(s, pa)
    gb = scores(s, pb)
    qa = set(ga.index[ga["size"] >= MIN_HALF])
    qb = set(gb.index[gb["size"] >= MIN_HALF])
    qual = sorted(qa & qb)
    if len(qual) < 3:
        return len(qual), None, None, None
    x = ga.loc[qual, "mean"].to_numpy(float)
    y = gb.loc[qual, "mean"].to_numpy(float)
    return len(qual), float(spearmanr(x, y)[0]), x, y


def null_band(x, y):
    rng = np.random.default_rng(SEED_NULL)
    draws = np.empty(N_NULL)
    for i in range(N_NULL):
        draws[i] = float(spearmanr(x, y[rng.permutation(y.size)])[0])
    return draws


def part1_subset(s, p, name):
    topics = sorted(p.topic_pool.unique())
    K = len(topics)
    rng = np.random.default_rng(SEED_SPLIT)
    rows, kept = [], []
    for k in range(N_SPLITS):
        perm = rng.permutation(K)
        A = [topics[i] for i in perm[: K // 2]]
        pa = set(p.policy[p.topic_pool.isin(A)])
        pb = set(p.policy) - pa
        nq, rho, x, y = split_rho(s, pa, pb)
        rows.append(dict(subset=name, split=k, topics_A=len(A), topics_B=K - len(A),
                         policies_A=len(pa), policies_B=len(pb), n_qual_states=nq,
                         rho=(np.nan if rho is None else rho)))
        kept.append((x, y))
    rhos = np.array([r["rho"] for r in rows], float)
    nq = np.array([r["n_qual_states"] for r in rows], float)
    valid = ~np.isnan(rhos)
    med_nq = float(np.median(nq[valid]))
    out = dict(subset=name, n_topics=K, n_valid_splits=int(valid.sum()),
               median_qual_states=med_nq)
    if med_nq < MIN_QUAL_STATES:
        out["status"] = "too few"
        out["counts"] = dict(median_qual_states=med_nq)
        return out, rows
    cand = [i for i in range(N_SPLITS) if valid[i] and nq[i] == med_nq]
    if cand:
        ref = cand[0]
    else:
        vi = np.where(valid)[0]
        ref = int(vi[np.argmin(np.abs(nq[vi] - med_nq))])
    xr, yr = kept[ref]
    draws = null_band(xr, yr)
    nb = pct(draws, [2.5, 95, 97.5])
    rv = rhos[valid]
    med_rho = float(np.median(rv))
    share95 = float(np.mean(rv > nb[1]))

    # era split
    med_fy = float(p.first_year.median())
    pa = set(p.policy[p.first_year < med_fy])
    pb = set(p.policy[p.first_year >= med_fy])
    nq_e, rho_e, _, _ = split_rho(s, pa, pb)

    pattern = bool(med_rho > nb[2] and rho_e is not None and rho_e > nb[2] and share95 >= 0.9)
    out.update(
        status="ok",
        ref_split=int(ref), ref_qual_states=int(xr.size), ref_size_matches_median=bool(nq[ref] == med_nq),
        null=dict(draws=N_NULL, seed=SEED_NULL, p2_5=nb[0], p95=nb[1], p97_5=nb[2]),
        topic_split=dict(n_splits=N_SPLITS, seed=SEED_SPLIT, median_rho=med_rho,
                         p5=pct(rv, [5])[0], p95=pct(rv, [95])[0],
                         share_above_null95=share95),
        era=dict(cut_first_year=med_fy, policies_before=len(pa), policies_after=len(pb),
                 n_qual_states=nq_e, rho=rho_e),
        pattern_flag=pattern,
    )
    return out, rows


def part1(spid):
    s = eligible_rows(spid)
    p = policy_table(s)
    res = {"n_eligible_policies": int(p.policy.nunique()),
           "n_eligible_policies_uniform_law": int((p.source == "Uniform Law").sum()),
           "n_state_policy_rows": int(len(s)),
           "topics": {t: int(c) for t, c in p.topic_pool.value_counts().items()}}
    subsets = {
        "primary": p,
        "first_year_le_2005": p[p.first_year <= ERA_CUT_SENS],
        "uniform_law_only": p[p.source == "Uniform Law"],
        "excluding_uniform_law": p[p.source != "Uniform Law"],
    }
    sub_out, split_rows = {}, []
    for name, pp in subsets.items():
        pp = pp.copy()
        # topic pooling is recomputed inside each subset
        cnt = pp.topic.value_counts()
        small = set(cnt[cnt < MIN_TOPIC].index)
        pp["topic_pool"] = [("other" if t in small else t) for t in pp.topic]
        ss = s[s.policy.isin(list(pp.policy))]
        o, rows = part1_subset(ss, pp, name)
        o["n_policies"] = int(pp.shape[0])
        sub_out[name] = o
        split_rows += rows
    res["subsets"] = sub_out

    # full-sample scores (primary eligible set)
    full = scores(s, set(p.policy))
    full = full.rename(columns={"mean": "score", "size": "n_policies"})
    res["full_sample_states"] = int(len(full))
    return res, s, p, full, split_rows


def traits_table(cal):
    c = cal[(cal.year >= YR0) & (cal.year <= YR1)]
    return c.groupby("state")[TRAITS].mean()


# ---------------------------------------------------------------- Part 2 (L2)

def habitual_from(s, pols_all, pool, k, excl=None):
    pols = pols_all if excl is None else (pols_all - {excl})
    g = scores(s, pols)["mean"].reindex(pool)
    order = sorted(pool, key=lambda st: (float(g[st]), st))
    return set(order[:k])


def twin_map(H, pool, Z):
    cand = [t for t in pool if t not in H]
    pairs = []
    for h in sorted(H):
        for t in cand:
            dist = float(np.sqrt(((Z.loc[h].to_numpy() - Z.loc[t].to_numpy()) ** 2).sum()))
            pairs.append((dist, h, t))
    pairs.sort()
    uh, ut, T = set(), set(), {}
    for dist, h, t in pairs:
        if h in uh or t in ut:
            continue
        T[h] = t
        uh.add(h)
        ut.add(t)
    return set(T.values()), T


def design_diffs(df, lab):
    diffs = {}
    for d, g in df.groupby("design_id", sort=True):
        L = g[lab].to_numpy(bool)
        a = g["a"].to_numpy(float)
        if L.any() and (~L).any():
            diffs[d] = float(a[L].mean() - a[~L].mean())
    return diffs


def summarise(diffs):
    v = np.array(list(diffs.values()), float)
    if v.size == 0:
        return dict(n_designs=0, stat=None, sign_share=None, median=None)
    return dict(n_designs=int(v.size), stat=float(v.mean()),
                sign_share=float((v > 0).mean()), median=float(np.median(v)))


def perm_band(df, lab, seed):
    rng = np.random.default_rng(seed)
    groups = []
    for d, g in df.groupby("design_id", sort=True):
        L = g[lab].to_numpy(bool)
        a = g["a"].to_numpy(float)
        if L.any() and (~L).any():
            groups.append((a, L))
    st = np.empty(N_PERM)
    sh = np.empty(N_PERM)
    for b in range(N_PERM):
        ds = np.empty(len(groups))
        for j, (a, L) in enumerate(groups):
            pl = L[rng.permutation(L.size)]
            ds[j] = a[pl].mean() - a[~pl].mean()
        st[b] = ds.mean()
        sh[b] = (ds > 0).mean()
    return st, sh


def p_two(obs, null):
    m = null.mean()
    return float((1 + np.sum(np.abs(null - m) >= abs(obs - m))) / (null.size + 1))


def part2(s, p, des, coh, mp, cal):
    pols_all = set(p.policy)
    full = scores(s, pols_all)
    pool = sorted(full.index[full["size"] >= MIN_POOL])
    k = math.ceil(0.25 * len(pool))
    H_full = habitual_from(s, pols_all, pool, k)

    cache = {}

    def H_pol(q):
        if q not in cache:
            cache[q] = habitual_from(s, pols_all, pool, k, excl=q)
        return cache[q]

    pol_map = dict(zip(mp.design_id, mp.spid_policy))
    H_d = {}
    for d in des.design_id:
        q = pol_map.get(d)
        H_d[d] = H_pol(q) if (q is not None and q in pols_all) else H_full

    # covariates for twins: state means 1962-2016, standardised over the pool
    tm = traits_table(cal).loc[pool, ["ln_inc", "urban", "ideology"]]
    Z = (tm - tm.mean()) / tm.std(ddof=0)

    # units
    d_ok = des[des.noise_sd.notna() & (des.estimate != 0)].copy()
    sign = dict(zip(d_ok.design_id, np.sign(d_ok.estimate)))
    noise = dict(zip(d_ok.design_id, d_ok.noise_sd))
    block = dict(zip(des.design_id, des.block))
    u = coh[coh.design_id.isin(list(d_ok.design_id)) & (coh.n_cells_ev0_3 >= 2)
            & coh.att_ev0_3.notna()].copy()
    u["states_list"] = u.states.fillna("").map(lambda x: [t for t in x.split("|") if t])
    u["nst"] = u.states_list.map(len)
    u["a"] = [sign[d] * att / noise[d] for d, att in zip(u.design_id, u.att_ev0_3)]
    u["block"] = u.design_id.map(block)
    u["cohort_id"] = u.index
    u["H_size"] = u.design_id.map(lambda d: len(H_d[d]))

    single = u[(u.n_treated == 1) & (u.nst == 1)].copy()
    single["state"] = single.states_list.map(lambda x: x[0])
    single["hab"] = [st in H_d[d] for d, st in zip(single.design_id, single.state)]

    # real statistic
    diffs_real = design_diffs(single, "hab")
    S_real = summarise(diffs_real)
    n_hab_states = len({st for d, st, h in zip(single.design_id, single.state, single.hab)
                        if h and d in diffs_real})
    counts = dict(designs_with_units=int(single.design_id.nunique()),
                  single_state_cohorts=int(len(single)),
                  designs_both_kinds=int(len(diffs_real)),
                  distinct_habitual_states_in_those=int(n_hab_states),
                  habitual_full_sample=sorted(H_full), pool_states=len(pool), k=k)
    out = {"counts": counts}
    if len(diffs_real) < MIN_DESIGNS or n_hab_states < MIN_HAB_STATES:
        out["status"] = "too few"
        return out, dict(single=single, H_d=H_d, pool=pool, Z=Z, H_full=H_full,
                         full=full, S_real=None, diffs_real=diffs_real)

    # permutation null on the real labels
    st_null, sh_null = perm_band(single, "hab", SEED_PERM)
    sb = pct(st_null, [2.5, 97.5])
    hb = pct(sh_null, [2.5, 97.5])
    real_stat = S_real["stat"]
    real_share = S_real["sign_share"]

    # leave-one-state-out (drop the state's units and remove it from habitual sets)
    loso = {}
    for st0 in sorted(H_full):
        sub = single[single.state != st0].copy()
        Hm = {d: (H_d[d] - {st0}) for d in sub.design_id.unique()}
        sub["hab"] = [stt in Hm[d] for d, stt in zip(sub.design_id, sub.state)]
        loso[st0] = summarise(design_diffs(sub, "hab"))["stat"]
    loso_vals = [v for v in loso.values() if v is not None]
    loso_sign_ok = all(np.sign(v) == np.sign(real_stat) for v in loso_vals)

    # leave-one-block-out (drop the block's designs)
    lobo = {}
    for b in sorted(set(single.block)):
        sub = single[single.block != b]
        lobo[int(b)] = summarise(design_diffs(sub, "hab"))["stat"]
    lobo_vals = [v for v in lobo.values() if v is not None]
    lobo_sign_ok = all(np.sign(v) == np.sign(real_stat) for v in lobo_vals)

    # twin placebo
    twin_T, twin_pairs = {}, {}
    single["twin"] = False
    twin_rows = []
    for d in sorted(single.design_id.unique()):
        Hd = H_d[d]
        T, mapping = twin_map(Hd, pool, Z)
        twin_T[d] = sorted(T)
        twin_pairs[d] = mapping
    tw = single[~single.hab].copy()
    tw["twin_lab"] = [stt in set(twin_T[d]) for d, stt in zip(tw.design_id, tw.state)]
    diffs_twin = design_diffs(tw, "twin_lab")
    S_twin = summarise(diffs_twin)
    st_tw, sh_tw = perm_band(tw, "twin_lab", SEED_PERM)
    tb = pct(st_tw, [2.5, 97.5])
    twin_in_band = bool(tb[0] <= S_twin["stat"] <= tb[1])

    # block bootstrap of real minus twin (designs included in both)
    both = sorted(set(diffs_real) & set(diffs_twin))
    e_blk = {}
    for d in both:
        e_blk.setdefault(int(block[d]), []).append(diffs_real[d] - diffs_twin[d])
    e_obs = float(np.mean([diffs_real[d] - diffs_twin[d] for d in both]))
    rng = np.random.default_rng(SEED_BOOT)
    keys = sorted(e_blk)
    bs = np.empty(N_BOOT)
    for b in range(N_BOOT):
        pick = rng.integers(0, len(keys), size=len(keys))
        vals = []
        for i in pick:
            vals += e_blk[keys[i]]
        bs[b] = np.mean(vals)
    diff_ci = pct(bs, [2.5, 97.5])
    diff_ci_excl0 = bool(diff_ci[0] > 0 or diff_ci[1] < 0)

    # half-habitual cohort rule: all known-state cohorts, share of states habitual >= 0.5
    allk = u[u.nst >= 1].copy()
    allk["hab_share"] = [float(np.mean([stt in H_d[d] for stt in lst]))
                         for d, lst in zip(allk.design_id, allk.states_list)]
    allk["hab"] = allk.hab_share >= 0.5
    S_half = summarise(design_diffs(allk, "hab"))

    # sign and band checks
    stat_out = (real_stat < sb[0]) or (real_stat > sb[1])
    share_out = (real_share < hb[0]) or (real_share > hb[1])
    twin_ok = twin_in_band or (diff_ci_excl0 and e_obs > 0)
    pattern = bool(stat_out and share_out and loso_sign_ok and lobo_sign_ok and twin_ok)

    out.update(
        status="ok",
        null=dict(draws=N_PERM, seed=SEED_PERM, stat_p2_5=sb[0], stat_p97_5=sb[1],
                  sign_share_p2_5=hb[0], sign_share_p97_5=hb[1],
                  p_two_sided_stat=p_two(real_stat, st_null),
                  p_two_sided_sign_share=p_two(real_share, sh_null)),
        real=S_real,
        leave_one_state_out=dict(range=[min(loso_vals), max(loso_vals)],
                                 n_states=len(loso_vals), sign_kept=loso_sign_ok,
                                 values=loso),
        leave_one_block_out=dict(range=[min(lobo_vals), max(lobo_vals)],
                                 n_blocks=len(lobo_vals), sign_kept=lobo_sign_ok),
        twin=dict(stat=S_twin["stat"], sign_share=S_twin["sign_share"],
                  n_designs=S_twin["n_designs"], median=S_twin["median"],
                  null_p2_5=tb[0], null_p97_5=tb[1], inside_own_band=twin_in_band,
                  seed=SEED_PERM,
                  real_minus_twin=e_obs, real_minus_twin_ci=diff_ci,
                  bootstrap=dict(draws=N_BOOT, seed=SEED_BOOT, n_blocks=len(keys),
                                 n_designs_both=len(both), excludes_zero=diff_ci_excl0)),
        sensitivity_half_habitual=S_half,
        pattern_flag=pattern,
    )
    return out, dict(single=single, H_d=H_d, pool=pool, Z=Z, H_full=H_full, full=full,
                     S_real=S_real, diffs_real=diffs_real, diffs_twin=diffs_twin,
                     twin_T=twin_T, allk=allk, tw=tw, twin_pairs=twin_pairs)


# ---------------------------------------------------------------- plain-python checks

def plain_scores(spid_path, excl=None):
    with open(spid_path, newline="") as f:
        rows = list(csv.DictReader(f))
    el = [r for r in rows if r["complete_window"] == "1" and r["date_conflict"] == "0"]
    by = {}
    for r in el:
        by.setdefault(r["policy"], []).append(r)
    acc = {}
    for pol, rs in by.items():
        if len(rs) < MIN_ADOPT or pol == excl:
            continue
        n = len(rs)
        yrs = [float(r["adopt_year"]) for r in rs]
        for r in rs:
            y = float(r["adopt_year"])
            lo = sum(1 for v in yrs if v < y)
            eq = sum(1 for v in yrs if v == y)
            avg = lo + (eq + 1) / 2.0
            o = (avg - 1.0) / (n - 1.0)
            acc.setdefault(r["state"], []).append(o)
    return {st: (len(v), sum(v) / len(v)) for st, v in acc.items()}


def plain_checks(spid_path, coh_path, des_path, mp_path, main):
    base = plain_scores(spid_path)
    pool = sorted(st for st, (n, _) in base.items() if n >= MIN_POOL)
    k = math.ceil(0.25 * len(pool))
    # one state: most policies in pool, first alphabetically on ties
    chk_state = sorted(pool, key=lambda st: (-base[st][0], st))[0]
    diff_score = abs(base[chk_state][1] - float(main["full"].loc[chk_state, "score"]))
    assert diff_score < 1e-9, diff_score

    def H_of(excl=None):
        sc = plain_scores(spid_path, excl=excl)
        return set(sorted(pool, key=lambda st: (sc[st][1], st))[:k])

    Hfull = H_of()
    assert Hfull == main["H_full"], (Hfull, main["H_full"])
    with open(mp_path, newline="") as f:
        mapping = {r["design_id"]: r["spid_policy"] for r in csv.DictReader(f)}
    with open(des_path, newline="") as f:
        designs = list(csv.DictReader(f))
    cache = {}
    sign, noise, blk = {}, {}, {}
    for r in designs:
        if r["noise_sd"] == "" or float(r["estimate"]) == 0:
            continue
        est = float(r["estimate"])
        sign[r["design_id"]] = 1.0 if est > 0 else -1.0
        noise[r["design_id"]] = float(r["noise_sd"])
        blk[r["design_id"]] = r["block"]
    Hd = {}
    pols_all = set()
    with open(spid_path, newline="") as f:
        for r in csv.DictReader(f):
            if r["complete_window"] == "1" and r["date_conflict"] == "0":
                pols_all.add(r["policy"])
    for d in [r["design_id"] for r in designs]:
        q = mapping.get(d)
        if q is not None and q in pols_all:
            if q not in cache:
                cache[q] = H_of(excl=q)
            Hd[d] = cache[q]
        else:
            Hd[d] = Hfull
    with open(coh_path, newline="") as f:
        cohorts = list(csv.DictReader(f))
    dsum = {}
    for r in cohorts:
        d = r["design_id"]
        if d not in sign or int(r["n_cells_ev0_3"]) < 2 or r["att_ev0_3"] == "":
            continue
        if int(r["n_treated"]) != 1 or r["states"] == "" or "|" in r["states"]:
            continue
        st = r["states"]
        a = sign[d] * float(r["att_ev0_3"]) / noise[d]
        dsum.setdefault(d, {"h": [], "o": []})
        (dsum[d]["h"] if st in Hd[d] else dsum[d]["o"]).append(a)
    ds = []
    for d, v in dsum.items():
        if v["h"] and v["o"]:
            ds.append(sum(v["h"]) / len(v["h"]) - sum(v["o"]) / len(v["o"]))
    stat = sum(ds) / len(ds)
    if main["S_real"]["stat"] is None:
        return dict(state=chk_state, abs_diff_score=diff_score, stat_plain=stat, note="part 2 too few")
    diff_stat = abs(stat - main["S_real"]["stat"])
    assert diff_stat < 1e-9, diff_stat
    return dict(state=chk_state, score_plain=base[chk_state][1],
                score_main=float(main["full"].loc[chk_state, "score"]),
                abs_diff_score=diff_score, H_full_match=True,
                stat_plain=stat, stat_main=main["S_real"]["stat"],
                abs_diff_stat=diff_stat, n_designs_plain=len(ds))


# ---------------------------------------------------------------- main

def main():
    os.makedirs(OUT, exist_ok=True)
    inputs = {
        "spid_adoption.csv": os.path.join(INP, "spid_adoption.csv"),
        "state_year_calendar.csv": os.path.join(INP, "state_year_calendar.csv"),
        "designs.csv": os.path.join(INP, "designs.csv"),
        "cohorts.csv": os.path.join(INP, "cohorts.csv"),
        "FINAL_design_spid_map.csv": MAP_CSV,
    }
    hashes = {k: sha256(v) for k, v in inputs.items()}

    spid = pd.read_csv(inputs["spid_adoption.csv"])
    cal = pd.read_csv(inputs["state_year_calendar.csv"])
    des = pd.read_csv(inputs["designs.csv"])
    coh = pd.read_csv(inputs["cohorts.csv"])
    mp = pd.read_csv(MAP_CSV)

    p1, s, p, full, split_rows = part1(spid)
    res2, aux = part2(s, p, des, coh, mp, cal)

    # state table, leaders, trait correlations (primary)
    tab = full.join(traits_table(cal), how="left")
    tab["qual20"] = tab["n_policies"] >= MIN_HALF
    tab["in_pool30"] = tab.index.isin(list(aux["pool"]))
    tab["habitual_full"] = tab.index.isin(list(aux["H_full"]))
    lead = tab[tab.qual20].sort_values(["score", "n_policies"]).copy()
    lead["rank_early"] = np.arange(1, len(lead) + 1)
    tab = tab.join(lead[["rank_early"]], how="left")
    tab.index.name = "state"
    tab.reset_index().to_csv(os.path.join(OUT, "state_scores.csv"), index=False)

    corr = {}
    for t in TRAITS:
        sub = tab[tab.qual20 & tab[t].notna()]
        corr[t] = dict(rho=float(spearmanr(sub["score"], sub[t])[0]), n_states=int(len(sub)))
    p1["leaders_early_12"] = [dict(state=st, n_policies=int(r.n_policies), score=float(r.score))
                              for st, r in lead.head(12).iterrows()]
    p1["latest_12"] = [dict(state=st, n_policies=int(r.n_policies), score=float(r.score))
                       for st, r in lead.tail(12).iterrows()]
    p1["trait_spearman_states_ge20"] = corr
    p1["trait_window"] = [YR0, YR1]
    pd.DataFrame(split_rows).to_csv(os.path.join(OUT, "splits_topic.csv"), index=False)

    # part 2 tables
    single = aux["single"]
    cohort_out = single[["cohort_id", "design_id", "block", "g_cal", "states", "state",
                         "n_cells_ev0_3", "att_ev0_3", "a", "hab"]].rename(
        columns={"hab": "habitual_label"})
    if "twin_T" in aux:
        cohort_out["in_twin_set"] = [st in set(aux["twin_T"][d]) for d, st in
                                     zip(cohort_out.design_id, cohort_out.state)]
    cohort_out.to_csv(os.path.join(OUT, "part2_cohorts.csv"), index=False)

    mpd = dict(zip(mp.design_id, mp.spid_policy))
    dsum = []
    for _, r in des.iterrows():
        d = r.design_id
        dsum.append(dict(design_id=d, block=int(r.block), spid_policy=mpd.get(d),
                         n_habitual_states=len(aux["H_d"][d]),
                         d_real=aux.get("diffs_real", {}).get(d),
                         d_twin=aux.get("diffs_twin", {}).get(d)))
    pd.DataFrame(dsum).to_csv(os.path.join(OUT, "part2_designs.csv"), index=False)

    real = aux.get("S_real")
    chk = plain_checks(inputs["spid_adoption.csv"], inputs["cohorts.csv"], inputs["designs.csv"],
                       MAP_CSV, dict(full=full, H_full=aux["H_full"],
                                     S_real=real if real else dict(stat=None)))

    result = dict(
        task="T233 Q8 rank 8 habitual first movers",
        plan="docs/plans/l3-meta/PREPLAN_T233_20261021.md sections 0 and 4",
        deviation_note="mapping file read from states_decide/mapping/ (not inputs/mapping/)",
        inputs_sha256=hashes,
        seeds=dict(split=SEED_SPLIT, null=SEED_NULL, perm=SEED_PERM, boot=SEED_BOOT),
        draws=dict(splits=N_SPLITS, null=N_NULL, perm=N_PERM, boot=N_BOOT),
        part1=p1,
        part2=res2,
        plain_check=chk,
        added_after_the_plan=[
            "Leaders list uses states with >=20 eligible policies; scores for all states in state_scores.csv.",
            "Part 1 null band from one reference split whose qualifying-state count equals the median; the shuffled pair is that split's half-A and half-B scores. Same band used for the era split.",
            "Topic split: topics split into balanced halves (floor/ceil) on each draw.",
            "Leave-one-state-out drops the state's cohorts and removes it from habitual sets; leave-one-block-out drops the block's designs.",
            "Twin placebo: habitual states matched to non-habitual pool states (standardised ln_inc, urban, ideology, pool SD); cohorts in real-habitual states are excluded from the twin comparison.",
            "Designs with missing noise_sd (3) cannot be standardised and are excluded from Part 2 only.",
        ],
    )
    with open(os.path.join(OUT, "RESULTS_Q8.json"), "w") as f:
        json.dump(result, f, indent=2, default=_jsonable)
    print(json.dumps(dict(part1_subsets={k: {kk: v.get(kk) for kk in ("status", "median_qual_states", "pattern_flag")}
                                         for k, v in p1["subsets"].items()},
                          part1_primary=p1["subsets"]["primary"],
                          part2_status=res2.get("status"), part2_counts=res2.get("counts"),
                          plain_check=chk), indent=1, default=_jsonable))


def _jsonable(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


if __name__ == "__main__":
    main()
