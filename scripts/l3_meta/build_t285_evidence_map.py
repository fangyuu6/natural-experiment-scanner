"""T285 evidence map (prereg: docs/plans/l3-meta/PREREG_T285_EVIDENCE_MAP_20261023.md).
Reads only design cards / state lists and Census population. Run: .venv_did/bin/python -I -B scripts/l3_meta/build_t285_evidence_map.py
"""
import csv, json, os, sys, collections
import numpy as np

ROOT = os.getcwd()
LIVE = "data/l3_meta/refresh/20261023_r2/live/l2_set_live.json"
COH = "data/l3_meta/new_angles_20261021/inputs/cohort_states.csv"
POP = "data/l4/covariates_public/raw/census_popest/NST-EST2025-ALLDATA.csv"
OUT = "data/l3_meta/t285_evidence_map_20261023"
SEED, NDRAW = 285285, 5000
os.makedirs(OUT, exist_ok=True)

# universe + population + region
pop, region = {}, {}
ABBR = {}
import re
FIPS_ABBR = {"01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT","10":"DE","11":"DC","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL","18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD","25":"MA","26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE","32":"NV","33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND","39":"OH","40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD","47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV","55":"WI","56":"WY"}
REG = {"1": "Northeast", "2": "Midwest", "3": "South", "4": "West"}
for r in csv.DictReader(open(POP, encoding="utf-8", errors="replace")):
    if r["SUMLEV"] == "040" and r["STATE"] in FIPS_ABBR:
        a = FIPS_ABBR[r["STATE"]]
        pop[a] = float(r["ESTIMATESBASE2020"]); region[a] = REG[r["REGION"]]
U = sorted(pop)
assert len(U) == 51, len(U)
lpop = np.log([pop[s] for s in U])

live = json.load(open(LIVE))
designs = live["designs"]

cs = collections.defaultdict(set)
for r in csv.DictReader(open(COH)):
    if r["matched"] == "1" and r["states"]:
        cs[r["design_id"]] |= set(re.split(r"[;,| ]+", r["states"].strip()))

def jload(p):
    try:
        return json.load(open(p))
    except Exception:
        return None

def card_states(c):
    """return (source label, set) from a card dict or None"""
    try:
        v = c["analysis"]["screen_expected_support"]["treated_states"]
        if v: return "card.screen_expected_support", set(v)
    except Exception: pass
    try:
        v = c["treatment"]["screening_adoption"]["adopters"]
        if v: return "card.screening_adoption", set(v)
    except Exception: pass
    try:
        v = c["treatment"]["screening_support"]["treated_states"]
        if v: return "card.screening_support", set(v)
    except Exception: pass
    for k, a in (c.get("automation") or {}).items():
        if isinstance(a, dict):
            try:
                v = a["support"]["treated_states"]
                if v: return "card.automation_support", set(v)
            except Exception: pass
    return None

def legacy_states(c):
    try:
        a = c["calibration"]["structure"]["adoption"]
        s = {k for k, v in a.items() if v is not None}
        if s: return "legacy.calibration_adoption", s
    except Exception: pass
    return None

rows = []
for x in designs:
    did, src = x["design_id"], x["source"]
    got = None; agree = ""
    if did in cs:
        got = ("cohort_states", set(cs[did]))
    cands = []
    cp = src.get("card_path")
    if cp: cands.append(cp)
    p = src.get("path", "")
    if p.endswith("CARD.json"): cands.append(p)
    elif p.endswith("RESULT.json"): cands.append(os.path.join(os.path.dirname(p), "CARD.json"))
    card_got = None
    for cpth in cands:
        if os.path.exists(cpth):
            c = jload(cpth)
            if c:
                card_got = card_states(c)
                if card_got: break
    if card_got and got:
        agree = "equal" if card_got[1] == got[1] else "differ"
    if got is None:
        got = card_got
    if got is None and p.endswith(".json") and os.path.exists(p):
        c = jload(p)
        if c: got = legacy_states(c)
    st = {s for s in (got[1] if got else set()) if s in pop}
    rows.append(dict(design_id=did, status=x["status"], policy_family=x.get("policy_family") or did,
                     domain=x.get("domain"), source=(got[0] if got else "none"), n_treated=len(st),
                     card_vs_cohort=agree, states=";".join(sorted(st))))

cov = collections.Counter((r["status"], r["source"] != "none") for r in rows)
listed = [r for r in rows if r["n_treated"] > 0]
with open(f"{OUT}/DESIGN_STATES.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

idx = {s: i for i, s in enumerate(U)}
def rank(a):
    o = np.argsort(a, kind="mergesort"); r = np.empty(len(a)); 
    # average ranks
    a = np.asarray(a); r = np.zeros(len(a)); i = 0; so = a[o]
    while i < len(a):
        j = i
        while j + 1 < len(a) and so[j + 1] == so[i]: j += 1
        r[o[i:j + 1]] = (i + j) / 2 + 1; i = j + 1
    return r
def spear(x, y):
    rx, ry = rank(x), rank(y)
    rx -= rx.mean(); ry -= ry.mean()
    d = np.sqrt((rx**2).sum() * (ry**2).sum())
    return float((rx * ry).sum() / d) if d else float("nan")
tert = np.zeros(51, int)
order = np.argsort(lpop); 
tert[order[:17]] = 0; tert[order[17:34]] = 1; tert[order[34:]] = 2   # small, mid, large

def counts(sets):
    c = np.zeros(51)
    for s in sets:
        for a in s: c[idx[a]] += 1
    return c
def top10(c): return float(np.sort(c)[-10:].sum() / c.sum())

def study(units, tag):
    """units: list of (set of states, is_estimated)"""
    rng = np.random.default_rng(SEED)
    sets = [u[0] for u in units]; est = np.array([u[1] for u in units])
    c = counts(sets)
    obs = dict(n_units=len(units), n_est=int(est.sum()), appearances=int(c.sum()),
               p1_top10_share=top10(c), p2_rho_logpop=spear(c, lpop))
    # P3 observed
    def p3(estv):
        a_e = np.zeros(51); a_t = np.zeros(51)
        for s, e in zip(sets, estv):
            for a in s:
                (a_e if e else a_t)[idx[a]] += 1
        tot = a_e + a_t
        share = lambda m: a_e[m].sum() / tot[m].sum() if tot[m].sum() else float("nan")
        return share(tert == 2) - share(tert == 0), share(tert == 0), share(tert == 1), share(tert == 2)
    o3 = p3(est)
    obs.update(p3_est_share_large_minus_small=o3[0], est_share_small=o3[1], est_share_mid=o3[2], est_share_large=o3[3])
    sizes = [len(s) for s in sets]
    d1 = np.empty(NDRAW); d2 = np.empty(NDRAW); d3 = np.empty(NDRAW)
    for b in range(NDRAW):
        cc = np.zeros(51)
        for k in sizes:
            cc[rng.choice(51, k, replace=False)] += 1
        d1[b] = top10(cc); d2[b] = spear(cc, lpop)
        d3[b] = p3(rng.permutation(est))[0]
    def band(d, o):
        lo, hi = np.nanpercentile(d, [2.5, 97.5])
        p = float((np.sum(np.abs(d - np.nanmean(d)) >= abs(o - np.nanmean(d))) + 1) / (NDRAW + 1))
        return dict(lo=float(lo), hi=float(hi), mean=float(np.nanmean(d)), p_two_sided=p,
                    verdict=("tilted" if (o < lo or o > hi) else "no detectable tilt"))
    res = dict(tag=tag, observed=obs, P1=band(d1, obs["p1_top10_share"]), P2=band(d2, obs["p2_rho_logpop"]),
               P3=band(d3, o3[0]))
    return res, c

units = [(set(r["states"].split(";")), r["status"] == "estimated") for r in listed]
res_design, c_all = study(units, "design (primary)")
# one vote per law
fam = collections.defaultdict(lambda: [set(), []])
for r in listed:
    fam[r["policy_family"]][0] |= set(r["states"].split(";")); fam[r["policy_family"]][1].append(r["status"] == "estimated")
units_l = [(v[0], any(v[1])) for v in fam.values()]
res_law, c_law = study(units_l, "one vote per law (any design estimated)")

# descriptive
est_sets = [u[0] for u in units if u[1]]; ne_sets = [u[0] for u in units if not u[1]]
c_est, c_ne = counts(est_sets), counts(ne_sets)
tot_pop = sum(pop.values())
desc_states = [dict(state=s, region=region[s], pop_2020=int(pop[s]), pop_share=pop[s] / tot_pop,
                    designs_all=int(c_all[idx[s]]), designs_estimated=int(c_est[idx[s]]), designs_not_evaluable=int(c_ne[idx[s]]),
                    appearance_share=c_all[idx[s]] / c_all.sum()) for s in U]
with open(f"{OUT}/STATE_COUNTS.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(desc_states[0])); w.writeheader(); w.writerows(desc_states)
regs = {}
for g in REG.values():
    m = [s for s in U if region[s] == g]
    regs[g] = dict(pop_share=sum(pop[s] for s in m) / tot_pop, appearance_share=sum(c_all[idx[s]] for s in m) / c_all.sum(),
                   estimated_appearance_share=sum(c_est[idx[s]] for s in m) / c_est.sum())
pop_in_est = sum(pop[s] for s in U if c_est[idx[s]] > 0) / tot_pop
pop_in_all = sum(pop[s] for s in U if c_all[idx[s]] > 0) / tot_pop
never_est = [s for s in U if c_est[idx[s]] == 0]
top = sorted(desc_states, key=lambda d: -d["designs_all"])
out = dict(prereg_sha256=open("docs/plans/l3-meta/PREREG_T285_EVIDENCE_MAP_20261023_HASH.txt").read().strip(),
           live_as_of=live["as_of"], n_designs=len(designs),
           coverage={f"{k[0]}|listed={k[1]}": v for k, v in cov.items()},
           n_listed=len(listed), n_listed_estimated=sum(1 for u in units if u[1]),
           source_counts=dict(collections.Counter(r["source"] for r in rows)),
           card_vs_cohort=dict(collections.Counter(r["card_vs_cohort"] for r in rows)),
           design_level=res_design, law_level=res_law, n_laws=len(units_l),
           regions=regs, pop_share_in_states_treated_ge1_estimated=pop_in_est, pop_share_in_states_treated_ge1_any=pop_in_all,
           states_never_treated_in_estimated=never_est, median_designs_per_state=float(np.median(c_all)),
           top10_states=[(d["state"], d["designs_all"], round(d["pop_share"], 4)) for d in top[:10]],
           bottom10_states=[(d["state"], d["designs_all"], round(d["pop_share"], 4)) for d in top[-10:]],
           code="scripts/l3_meta/build_t285_evidence_map.py", seed=SEED, ndraw=NDRAW)
json.dump(out, open(f"{OUT}/RESULTS_T285.json", "w"), indent=1)
print(json.dumps(out, indent=1))
