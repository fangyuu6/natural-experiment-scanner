#!/usr/bin/env python3
"""T233 Rank 6: when do states act? (PREPLAN_T233_20261021.md, section 2, binding).

Run only with: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/q6_when_act.py
Reads inputs in data/l3_meta/new_angles_20261021/states_decide/inputs/ (read-only).
Writes data/l3_meta/new_angles_20261021/states_decide/q6/.

Items marked "added after the plan" are sensitivity or reporting rows that the
plan does not define; they do not feed any pattern flag.
"""
import csv
import hashlib
import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = "."
IN = os.path.join(ROOT, "data/l3_meta/new_angles_20261021/states_decide/inputs")
OUT = os.path.join(ROOT, "data/l3_meta/new_angles_20261021/states_decide/q6")
PLAN = os.path.join(ROOT, "docs/plans/l3-meta/PREPLAN_T233_20261021.md")
os.makedirs(OUT, exist_ok=True)

BASE = 20261022
R_NULL = 1000
R_BOOT = 1000
R_A3 = 1000
R_B = 2000
Y0, Y1 = 1962, 2016
CAL_Y0 = 1961  # calendar starts 1961; column index = year - CAL_Y0
EXCL = ["MT", "NV", "ND", "TX"]
MIN_POL, MIN_EV = 20, 500
MIN_B_COH, MIN_B_DES = 30, 10
SHIFT = 8
MAX_REDRAW = 50

F_SPID = "spid_adoption.csv"
F_CAL = "state_year_calendar.csv"
F_DES = "designs.csv"
F_COH = "cohorts.csv"
IN_FILES = [F_SPID, F_CAL, F_DES, F_COH]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def pct(x, q):
    return float(np.percentile(np.asarray(x, dtype=float), q))


def jdump(obj, path):
    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(type(o))

    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, default=conv, sort_keys=True)


# ---------------------------------------------------------------- inputs
spid = pd.read_csv(os.path.join(IN, F_SPID), dtype=str, keep_default_na=False)
spid["adopt_year"] = spid["adopt_year"].astype(int)
spid["date_conflict"] = spid["date_conflict"].astype(int)
cal = pd.read_csv(os.path.join(IN, F_CAL), dtype=str, keep_default_na=False)
cal["year"] = cal["year"].astype(int)
designs = pd.read_csv(os.path.join(IN, F_DES), dtype=str, keep_default_na=False)
cohorts = pd.read_csv(os.path.join(IN, F_COH), dtype=str, keep_default_na=False)

STATES = sorted(cal["state"].unique())
S_IDX = {s: i for i, s in enumerate(STATES)}
NY = 2016 - CAL_Y0 + 1  # 1961..2016
ELEC = np.full((len(STATES), NY), np.nan)
DOWN = np.full((len(STATES), NY), np.nan)
for r in cal.itertuples(index=False):
    si, yi = S_IDX[r.state], r.year - CAL_Y0
    if r.lower_house_election != "":
        ELEC[si, yi] = float(r.lower_house_election)
    if r.downturn != "":
        DOWN[si, yi] = float(r.downturn)

# calendar sanity
assert len(STATES) == 50 and cal["year"].min() == 1961 and cal["year"].max() == 2016
assert set(spid["state"]) <= set(STATES)

# ------------------------------------------------------- event store (numpy)
win = spid[(spid["adopt_year"] >= Y0) & (spid["adopt_year"] <= Y1)].copy()
ev_main = win[win["date_conflict"] == 0].copy()          # primary events
ev_conf = win.copy()                                       # incl. date-conflict rows (sensitivity)


def policy_counts(ev):
    return ev.groupby("policy").size()


def store_for(ev):
    st = {}
    for pol, g in ev.groupby("policy"):
        st[pol] = {
            "st": g["state"].to_numpy(),
            "si": np.array([S_IDX[s] for s in g["state"]], dtype=int),
            "yi": g["adopt_year"].to_numpy(dtype=int) - CAL_Y0,
            "year": g["adopt_year"].to_numpy(dtype=int),
            "topic": g["majortopic_str"].iloc[0],
            "source": g["source"].iloc[0],
        }
    return st


cnt_main = policy_counts(ev_main)
ELIG_MAIN = sorted(cnt_main[cnt_main >= 10].index.tolist())
cnt_conf = policy_counts(ev_conf)
ELIG_CONF = sorted(cnt_conf[cnt_conf >= 10].index.tolist())
ST_MAIN = store_for(ev_main[ev_main["policy"].isin(ELIG_MAIN)])
ST_CONF = store_for(ev_conf[ev_conf["policy"].isin(ELIG_CONF)])


# ------------------------------------------------------ A1 / A2 machinery
def run_share(store, pols, excl, flag, label):
    """Mean over policies of share of events in class (flag==1).

    Null: within each policy permute adoption years among its event states.
    Seeds: null [BASE,6,1]; topic bootstrap [BASE,6,4].
    """
    pp = []
    dropped = 0
    for p in pols:
        d = store[p]
        n_all = len(d["si"])
        m = np.ones(n_all, dtype=bool)
        if excl:
            m &= ~np.isin(d["st"], EXCL)
        fv = flag[d["si"], d["yi"]]
        m &= np.isfinite(fv)
        dropped += int(n_all - m.sum())
        if m.sum() == 0:
            continue
        pp.append({"policy": p, "si": d["si"][m], "yi": d["yi"][m],
                   "topic": d["topic"], "source": d["source"]})
    if len(pp) == 0:
        return {"status": "too few", "n_policies": 0, "pooled_events": 0}
    n_pol = len(pp)
    n_ev = int(sum(len(x["si"]) for x in pp))
    obs_share = np.array([flag[x["si"], x["yi"]].mean() for x in pp])
    out = {"n_policies": n_pol, "pooled_events": n_ev, "dropped_nonfinite_or_excluded": dropped,
           "obs_share_per_policy": obs_share}
    if n_pol < MIN_POL or n_ev < MIN_EV:
        out["status"] = "too few"
        return out
    out["status"] = "ok"
    out["obs_stat"] = float(obs_share.mean())
    out["pooled_event_share"] = float(sum(flag[x["si"], x["yi"]].sum() for x in pp) / n_ev)

    rng = np.random.default_rng([BASE, 6, 1])
    null = np.empty((R_NULL, n_pol))
    for r in range(R_NULL):
        for k, x in enumerate(pp):
            perm = rng.permutation(len(x["yi"]))
            null[r, k] = flag[x["si"], x["yi"][perm]].mean()
    E_p = null.mean(axis=0)
    null_stat = null.mean(axis=1)
    d_p = obs_share - E_p
    out["E_p"] = E_p
    out["null_stat"] = null_stat
    mu = null_stat.mean()
    gap = out["obs_stat"] - mu
    out.update({
        "null_mean": float(mu), "null_sd": float(null_stat.std(ddof=1)),
        "null_p2_5": pct(null_stat, 2.5), "null_p97_5": pct(null_stat, 97.5),
        "gap_pp": float(gap * 100),
        "p_two_sided": float((1 + np.sum(np.abs(null_stat - mu) >= abs(out["obs_stat"] - mu))) / (R_NULL + 1)),
    })
    # topic-block bootstrap of mean over policies of (obs - E_p)
    topics = sorted(set(x["topic"] for x in pp))
    T = len(topics)
    t_index = {t: i for i, t in enumerate(topics)}
    sum_t = np.zeros(T)
    n_t = np.zeros(T)
    for k, x in enumerate(pp):
        sum_t[t_index[x["topic"]]] += d_p[k]
        n_t[t_index[x["topic"]]] += 1
    rb = np.random.default_rng([BASE, 6, 4])
    idx = rb.integers(0, T, size=(R_BOOT, T))
    boot = sum_t[idx].sum(axis=1) / n_t[idx].sum(axis=1)
    blo, bhi = pct(boot, 2.5), pct(boot, 97.5)
    # same-direction rule: topics with >=5 eligible policies
    elig_t = [t for t in topics if n_t[t_index[t]] >= 5]
    same = [t for t in elig_t if np.sign(sum_t[t_index[t]] / n_t[t_index[t]]) == np.sign(gap)]
    frac = len(same) / len(elig_t) if elig_t else float("nan")
    out.update({
        "boot_lo": blo, "boot_hi": bhi,
        "n_topics": T, "n_topics_ge5": len(elig_t), "n_topics_same_dir": len(same),
        "frac_topics_same_dir": frac,
        "topic_table": [(t, int(n_t[t_index[t]]), float(sum_t[t_index[t]] / n_t[t_index[t]] * 100))
                        for t in topics],
    })
    outside = out["obs_stat"] < out["null_p2_5"] or out["obs_stat"] > out["null_p97_5"]
    out["pattern"] = bool(abs(gap) * 100 >= 5 and outside and (blo > 0 or bhi < 0)
                          and frac >= 0.6 and len(elig_t) > 0)
    out["label"] = label
    return out


def strip(o):
    return {k: v for k, v in o.items() if k not in ("obs_share_per_policy", "E_p", "null_stat", "topic_table")}


pols_main = sorted(ST_MAIN.keys())
# sources of primary eligible policies
src_map = {p: ST_MAIN[p]["source"] for p in pols_main}
ul = [p for p in pols_main if src_map[p] == "Uniform Law"]
other = [p for p in pols_main if src_map[p] != "Uniform Law"]

A1 = run_share(ST_MAIN, pols_main, True, ELEC, "A1 election (primary)")
A2 = run_share(ST_MAIN, pols_main, False, DOWN, "A2 downturn (primary)")

# added after the plan: sensitivity and stratum rows (no pattern flag)
sens = {}
sens["A1_uniform_law"] = strip(run_share(ST_MAIN, ul, True, ELEC, "x"))
sens["A2_uniform_law"] = strip(run_share(ST_MAIN, ul, False, DOWN, "x"))
sens["A1_other_sources"] = strip(run_share(ST_MAIN, other, True, ELEC, "x"))
sens["A2_other_sources"] = strip(run_share(ST_MAIN, other, False, DOWN, "x"))
sens["A1_incl_MT_NV_ND_TX"] = strip(run_share(ST_MAIN, pols_main, False, ELEC, "x"))
pols_conf = sorted(ST_CONF.keys())
sens["A1_incl_date_conflict"] = strip(run_share(ST_CONF, pols_conf, True, ELEC, "x"))
sens["A2_incl_date_conflict"] = strip(run_share(ST_CONF, pols_conf, False, DOWN, "x"))
# event-weighted pooled share (primary sets, added after the plan)
for k, res in (("A1", A1), ("A2", A2)):
    if res["status"] == "ok":
        sens[k + "_pooled_event_share_pct"] = float(res["pooled_event_share"] * 100)


def pub(res):
    """Public view: numbers in percentage points; no numbers when 'too few'."""
    if res["status"] != "ok":
        return {"status": "too few", "n_policies": res["n_policies"], "pooled_events": res["pooled_events"],
                "min_policies": MIN_POL, "min_events": MIN_EV}
    return {
        "status": "ok", "n_policies": res["n_policies"], "pooled_events": res["pooled_events"],
        "dropped_events": res["dropped_nonfinite_or_excluded"],
        "observed_pct": res["obs_stat"] * 100, "null_mean_pct": res["null_mean"] * 100,
        "null_sd_pct": res["null_sd"] * 100, "null_p2_5_pct": res["null_p2_5"] * 100,
        "null_p97_5_pct": res["null_p97_5"] * 100, "gap_pp": res["gap_pp"],
        "p_two_sided": res["p_two_sided"], "boot_lo_pp": res["boot_lo"] * 100,
        "boot_hi_pp": res["boot_hi"] * 100, "n_topics": res["n_topics"],
        "n_topics_ge5": res["n_topics_ge5"], "n_topics_same_dir": res["n_topics_same_dir"],
        "frac_topics_same_dir": res["frac_topics_same_dir"], "pattern": res["pattern"],
    }


# ------------------------------------------------------------ A3 waves
ev_a3 = ev_main[ev_main["policy"].isin(ELIG_MAIN)]
a3_pols = sorted(ELIG_MAIN)
yrs_per_pol = [ST_MAIN[p]["year"] for p in a3_pols]
all_years = np.concatenate(yrs_per_pol)
pol_of_ev = np.concatenate([np.full(len(y), k) for k, y in enumerate(yrs_per_pol)])
minyr = np.array([y.min() for y in yrs_per_pol])
maxyr = np.array([y.max() for y in yrs_per_pol])
NYR = Y1 - Y0 + 1
N_obs = np.bincount(all_years - Y0, minlength=NYR)
S_obs = float(np.sum((N_obs - N_obs.mean()) ** 2))

rng3 = np.random.default_rng([BASE, 6, 2])
P = len(a3_pols)
N_null = np.empty((R_A3, NYR), dtype=int)
redraws_total = 0
shifted_zero = 0
for r in range(R_A3):
    sh = rng3.integers(-SHIFT, SHIFT + 1, size=P)
    bad = (minyr + sh < Y0) | (maxyr + sh > Y1)
    tries = 0
    while bad.any() and tries < MAX_REDRAW:
        redraws_total += int(bad.sum())
        sh[bad] = rng3.integers(-SHIFT, SHIFT + 1, size=int(bad.sum()))
        bad = (minyr + sh < Y0) | (maxyr + sh > Y1)
        tries += 1
    if bad.any():
        shifted_zero += int(bad.sum())
        sh[bad] = 0
    N_null[r] = np.bincount(all_years + sh[pol_of_ev] - Y0, minlength=NYR)
assert N_null.min() >= 0

S_null = np.sum((N_null - N_null.mean(axis=1, keepdims=True)) ** 2, axis=1)
p_S = float((1 + np.sum(S_null >= S_obs)) / (R_A3 + 1))
S_p975 = pct(S_null, 97.5)
null_mean_y = N_null.mean(axis=0)
null_p975_y = np.percentile(N_null, 97.5, axis=0)
wave_mask = (N_obs > null_p975_y) & (N_obs - null_mean_y >= 3)
WAVE_YEARS = [int(Y0 + i) for i in range(NYR) if wave_mask[i]]
runs = []
for y in WAVE_YEARS:
    if runs and runs[-1][-1] == y - 1:
        runs[-1].append(y)
    else:
        runs.append([y])
A3_PATTERN = bool(S_obs > S_p975)
a3_tab = pd.DataFrame({"year": np.arange(Y0, Y1 + 1), "N_y": N_obs,
                       "null_mean": np.round(null_mean_y, 4), "null_p975": null_p975_y,
                       "wave": wave_mask})
a3_tab.to_csv(os.path.join(OUT, "a3_years.csv"), index=False)

# ----------------------------------------------------- independent recompute
# plain python (csv module, no numpy/pandas helpers) for A1 observed and S
with open(os.path.join(IN, F_CAL), newline="") as fh:
    cal_rows = list(csv.DictReader(fh))
with open(os.path.join(IN, F_SPID), newline="") as fh:
    spid_rows = list(csv.DictReader(fh))
elec_py = {}
for row in cal_rows:
    if row["lower_house_election"] != "":
        elec_py[(row["state"], int(row["year"]))] = float(row["lower_house_election"])
cnt_py = {}
ev_py = {}
for row in spid_rows:
    y = int(row["adopt_year"])
    if 1962 <= y <= 2016 and row["date_conflict"] == "0":
        cnt_py[row["policy"]] = cnt_py.get(row["policy"], 0) + 1
        ev_py.setdefault(row["policy"], []).append((row["state"], y))
elig_py = sorted(p for p, c in cnt_py.items() if c >= 10)
shares_py = []
for p in elig_py:
    evs = [(s, y) for (s, y) in ev_py[p] if s not in EXCL]
    if not evs:
        continue
    shares_py.append(sum(1 for s, y in evs if elec_py.get((s, y)) == 1.0) / len(evs))
obs_py = sum(shares_py) / len(shares_py)
N_py = [0] * (Y1 - Y0 + 1)
for p in elig_py:
    for (s, y) in ev_py[p]:
        N_py[y - Y0] += 1
mean_py = sum(N_py) / len(N_py)
S_py = sum((n - mean_py) ** 2 for n in N_py)
checks = {
    "A1_obs_numpy": A1["obs_stat"], "A1_obs_plain": obs_py,
    "A1_diff": abs(A1["obs_stat"] - obs_py),
    "A1_policies_numpy": A1["n_policies"], "A1_policies_plain": len(shares_py),
    "S_numpy": S_obs, "S_plain": S_py, "S_diff": abs(S_obs - S_py),
    "eligible_policies_numpy": len(pols_main), "eligible_policies_plain": len(elig_py),
}
checks["pass_1e9"] = bool(checks["A1_diff"] < 1e-9 and checks["S_diff"] < 1e-9
                          and checks["A1_policies_numpy"] == checks["A1_policies_plain"]
                          and checks["eligible_policies_numpy"] == checks["eligible_policies_plain"])
assert checks["pass_1e9"], checks

# --------------------------------------------------------- A1/A2 table files
# per-policy table rebuilt in run_share's order (pols_main order, policies with analysed events)
def policy_rows(res, qname, store, exclude_flag, flag):
    out = []
    kept = []
    for p in pols_main:
        d = store[p]
        m = np.ones(len(d["si"]), dtype=bool)
        if exclude_flag:
            m &= ~np.isin(d["st"], EXCL)
        m &= np.isfinite(flag[d["si"], d["yi"]])
        if m.sum() > 0:
            kept.append((p, int(m.sum())))
    for k, (p, n) in enumerate(kept):
        out.append({"question": qname, "policy": p, "topic": store[p]["topic"],
                    "source": store[p]["source"], "n_events_analysed": n,
                    "obs_share_pct": res["obs_share_per_policy"][k] * 100,
                    "E_p_pct": res["E_p"][k] * 100,
                    "obs_minus_E_pp": (res["obs_share_per_policy"][k] - res["E_p"][k]) * 100})
    return out


pt_rows = policy_rows(A1, "A1", ST_MAIN, True, ELEC) + policy_rows(A2, "A2", ST_MAIN, False, DOWN)
pd.DataFrame(pt_rows).to_csv(os.path.join(OUT, "a1_a2_policy_shares.csv"), index=False)

topic_rows = []
for res, q in ((A1, "A1"), (A2, "A2")):
    for t, n, m in res["topic_table"]:
        topic_rows.append({"question": q, "topic": t, "n_policies": n, "mean_obs_minus_E_pp": m})
pd.DataFrame(topic_rows).to_csv(os.path.join(OUT, "a1_by_topic.csv"), index=False)

# ------------------------------------------------------------- Part B
cd = cohorts.merge(designs, on="design_id", how="left", validate="many_to_one")
assert cd["estimate"].notna().all() and (cd["estimate"] != "").all()
cd["est"] = cd["estimate"].astype(float)
cd["noise"] = pd.to_numeric(cd["noise_sd"], errors="coerce")
cd["att"] = pd.to_numeric(cd["att_ev0_3"], errors="coerce")
cd["ncell"] = pd.to_numeric(cd["n_cells_ev0_3"], errors="coerce")
cd["gc"] = np.floor(pd.to_numeric(cd["g_cal"], errors="coerce")).astype("Int64")
valid = (cd["noise"] > 0) & np.isfinite(cd["att"]) & (cd["ncell"] >= 2) & (cd["est"] != 0)
cd["valid"] = valid
cd["aligned_a"] = np.where(valid, np.sign(cd["est"]) * cd["att"] / cd["noise"], np.nan)
cd["delivers"] = np.where(valid, cd["aligned_a"] >= 1, False)
cd["wave"] = cd["gc"].apply(lambda y: (not pd.isna(y)) and int(y) in WAVE_YEARS)

dn_n, dn_d, dn_cls, in_dn_univ = [], [], [], []
for r in cd.itertuples(index=False):
    st_list = [s for s in r.states.split("|") if s] if r.states else []
    if st_list and not pd.isna(r.gc) and Y0 <= int(r.gc) <= Y1:
        yr = int(r.gc) - 1 - CAL_Y0
        known = [DOWN[S_IDX[s], yr] for s in st_list if s in S_IDX and np.isfinite(DOWN[S_IDX[s], yr])]
        n_k = len(known)
        d_k = int(sum(1 for v in known if v == 1.0))
        dn_n.append(n_k)
        dn_d.append(d_k)
        dn_cls.append(bool(n_k > 0 and d_k / n_k >= 0.5))
        in_dn_univ.append(True)
    else:
        dn_n.append(0)
        dn_d.append(0)
        dn_cls.append(False)
        in_dn_univ.append(False)
cd["dn_known_n"] = dn_n
cd["dn_known_d"] = dn_d
cd["downturn"] = dn_cls
cd["in_downturn_universe"] = in_dn_univ


def b_class(mask_class, universe_mask, label):
    u = cd[universe_mask & cd["valid"]].copy()
    u["cls"] = mask_class[u.index]
    per = []
    for dsg, g in u.groupby("design_id"):
        n_in = int(g["cls"].sum())
        n_out = int(len(g) - n_in)
        if n_in >= 2 and n_out >= 2:
            per.append((dsg, g["domain"].iloc[0], g["cls"].to_numpy(bool), g["delivers"].to_numpy(bool)))
    n_class = int(u["cls"].sum())
    res = {"label": label, "cohorts_valid_universe": int(len(u)), "cohorts_in_class": n_class,
           "designs_eligible": len(per)}
    if n_class < MIN_B_COH or len(per) < MIN_B_DES:
        res["status"] = "too few"
        return res
    res["status"] = "ok"
    diffs = np.array([dl[g].mean() - dl[~g].mean() for _, _, g, dl in per])
    obs = float(diffs.mean())
    rng = np.random.default_rng([BASE, 6, 3])
    null = np.empty(R_B)
    for r in range(R_B):
        acc = np.empty(len(per))
        for k, (_, _, g, dl) in enumerate(per):
            gp = rng.permutation(g)
            acc[k] = dl[gp].mean() - dl[~gp].mean()
        null[r] = acc.mean()
    mu = null.mean()
    lo, hi = pct(null, 2.5), pct(null, 97.5)
    p = float((1 + np.sum(np.abs(null - mu) >= abs(obs - mu))) / (R_B + 1))
    sgn = np.sign(obs)
    frac_same = float(np.mean(np.sign(diffs) == sgn))
    dom = {}
    for (_, d, _, _), dv in zip(per, diffs):
        dom.setdefault(d, []).append(dv)
    dom_rows = {d: {"n_designs": len(v), "mean_diff_pp": float(np.mean(v) * 100), "sign": int(np.sign(np.mean(v)))}
                for d, v in dom.items() if len(v) >= 3}
    dom_opp_all = bool(dom_rows) and all(v["sign"] == -sgn for v in dom_rows.values())
    dom_ok = bool(dom_rows) and not dom_opp_all
    res.update({
        "observed_pp": obs * 100, "null_mean_pp": float(mu * 100), "null_p2_5_pp": lo * 100,
        "null_p97_5_pp": hi * 100, "p_two_sided": p, "frac_designs_same_sign": frac_same,
        "frac_designs_positive_diff": float(np.mean(diffs > 0)),
        "domains_ge3_designs": dom_rows, "domain_signs_not_all_opposite": dom_ok,
        "pattern": bool((obs < lo or obs > hi) and frac_same >= 0.6 and dom_ok),
        "wave_or_downturn_cohorts_in_class_among_eligible": int(sum(g.sum() for _, _, g, _ in per)),
        "cohorts_outside_among_eligible": int(sum((~g).sum() for _, _, g, _ in per)),
    })
    return res


B_wave = b_class(cd["wave"], pd.Series(True, index=cd.index), "wave cohort (g_cal in Part A wave year)")
B_down = b_class(cd["downturn"], cd["in_downturn_universe"], "downturn cohort (>=half known states in downturn year g_cal-1)")

bout = cd[["design_id", "domain", "block", "est", "noise", "gc", "states", "att", "ncell",
           "aligned_a", "valid", "delivers", "wave", "dn_known_n", "dn_known_d", "downturn",
           "in_downturn_universe"]].copy()
bout.columns = ["design_id", "domain", "block", "headline_estimate", "noise_sd", "g_cal_floor", "states",
                "att_ev0_3", "n_cells_ev0_3", "aligned_a", "valid", "delivers", "wave_class",
                "downturn_known_states", "downturn_states_in_downturn", "downturn_class",
                "in_downturn_universe"]
bout.to_csv(os.path.join(OUT, "b_cohorts.csv"), index=False)

# -------------------------------------------------------------- results
results = {
    "task": "T233 rank 6 when do states act",
    "plan": os.path.relpath(PLAN, ROOT),
    "plan_sha256": sha256(PLAN),
    "script": "scripts/l3_meta/new_angles_b/q6_when_act.py",
    "script_sha256": sha256(os.path.abspath(__file__)),
    "python": sys.version.split()[0],
    "numpy": np.__version__, "pandas": pd.__version__,
    "inputs": {f: {"path": os.path.join("data/l3_meta/new_angles_20261021/states_decide/inputs", f),
                   "sha256": sha256(os.path.join(IN, f))} for f in IN_FILES},
    "seeds": {"A1_null": [BASE, 6, 1], "A2_null_same_seed_separate_run": [BASE, 6, 1],
              "A1_A2_bootstrap": [BASE, 6, 4], "A3_null": [BASE, 6, 2], "B_null": [BASE, 6, 3]},
    "draws": {"A1_A2_null": R_NULL, "A1_A2_bootstrap": R_BOOT, "A3_null": R_A3, "B_null": R_B},
    "window": [Y0, Y1],
    "excluded_states_A1": EXCL,
    "counts": {
        "spid_rows": int(len(spid)), "rows_in_window": int(len(win)),
        "rows_in_window_date_conflict0": int(len(ev_main)),
        "eligible_policies_primary": len(ELIG_MAIN),
        "eligible_policies_incl_conflict": len(ELIG_CONF),
        "uniform_law_policies_eligible": len(ul), "other_source_policies_eligible": len(other),
        "designs": int(len(designs)), "cohorts": int(len(cohorts)),
        "cohorts_valid": int(cd["valid"].sum()),
        "cohorts_invalid_noise_or_att_or_cells_or_zero_estimate": int((~cd["valid"]).sum()),
        "designs_blank_noise_sd": int((designs["noise_sd"] == "").sum()),
    },
    "A1_election": pub(A1),
    "A2_downturn": pub(A2),
    "A3_waves": {
        "S_observed": S_obs, "S_null_mean": float(S_null.mean()), "S_null_p2_5": pct(S_null, 2.5),
        "S_null_p97_5": S_p975, "p_one_sided_ge": p_S, "pattern": A3_PATTERN,
        "wave_years": WAVE_YEARS, "wave_runs": runs,
        "shift_redraws_total": redraws_total, "policies_fallback_shift0_total": shifted_zero,
        "note": "p = (1+#(S_null>=S_obs))/(R+1)",
    },
    "B_cohorts": {"wave": B_wave, "downturn": B_down},
    "added_after_plan": {
        "description": "sensitivity and stratum rows, no pattern flags; same statistic code, "
                       "A1/A2 null re-seeded with [BASE,6,1] inside each sub-run",
        "rows": {k: {kk: vv for kk, vv in v.items()} if isinstance(v, dict) else v
                 for k, v in sens.items()},
        "A1_obs_plain_check": checks,
        "policy_source_rule": "source of the policy's first event row",
        "b_class_definitions": "wave class = floor(g_cal) in Part A wave years (all valid cohorts); "
                               "downturn universe = valid cohorts with states and 1962<=g_cal<=2016",
        "b_domain_rule": "domain sign = sign of mean diff over eligible designs in domain (>=3 designs); "
                         "'not all opposite' is false when no domain has >=3 designs",
        "a1_null_sharing": "A1 and A2 use separate runs with the same seed list",
        "p_convention": "two-sided p = (1+#(|null-mean|>=|obs-mean|))/(R+1)",
    },
}
jdump(results, os.path.join(OUT, "RESULTS_Q6.json"))

print(json.dumps({"A1": pub(A1), "A2": pub(A2), "A3": {"S": S_obs, "p": p_S, "wave_years": WAVE_YEARS,
                                                     "pattern": A3_PATTERN},
                  "B_wave": {k: v for k, v in B_wave.items() if k != "domains_ge3_designs"},
                  "B_down": {k: v for k, v in B_down.items() if k != "domains_ge3_designs"},
                  "checks": checks, "counts": results["counts"]}, indent=1, default=float))
