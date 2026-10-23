"""T256 checker B, Q3 (independent, from plan only)."""
import csv, json, math, collections, gc, os
import numpy as np
from scipy import stats
ROOT = ""
I = ROOT + "data/l3_meta/new_angles_20261021/inputs/"
OUT = "/path/to/scratch/scratch/T256/check/"
ro = {r["design_id"]: r for r in csv.DictReader(open(I + "design_roster.csv"))}
pk = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(I + "policy_keys.csv"))}
live = json.load(open(ROOT + "data/l3_meta/refresh/20261022_r2/live/l2_set_live.json"))
src = {x["design_id"]: x for x in live["designs"] if x["status"] == "estimated"}
dom = lambda d: "other" if d in ("other", "education") else d
def fin(x):
    try: return x is not None and x != "" and math.isfinite(float(x))
    except: return False
gt = collections.defaultdict(lambda: collections.defaultdict(dict))
for r in csv.DictReader(open(I + "group_time_export.csv")):
    e = int(r["event_time"])
    if 0 <= e <= 3: gt[r["design_id"]][r["g"]][e] = (float(r["att"]) if fin(r["att"]) else None, float(r["se"]) if fin(r["se"]) else None)
def cohorts(dct, needse):
    out = []
    for g, c in dct.items():
        if all(e in c and c[e][0] is not None and (not needse or (c[e][1] is not None and c[e][1] > 0)) for e in range(4)):
            out.append((np.mean([c[e][0] for e in range(4)]),
                        np.mean([c[e][1] for e in range(4)]) if needse else None,
                        math.sqrt(sum(c[e][1] ** 2 for e in range(4))) / 4 if needse else None))
    return out
A = {}; Bd = {}; excl = {}
for d in ro:
    if d not in gt: excl[d] = "no group_time"; continue
    hasse = ro[d]["source_group"] != "autopilot_estimate"
    # source by se availability
    allse = all(v[1] is not None for g in gt[d].values() for v in g.values())
    if allse: A[d] = cohorts(gt[d], True)
    elif src[d]["source_group"] == "autopilot_estimate": Bd[d] = None
    else: excl[d] = "no se, not autopilot"
print("A candidates", len(A), "B candidates", len(Bd), "excl", excl)
resA = {}
for d, cs in list(A.items()):
    if len(cs) < 3: excl[d] = f"<3 cohorts ({len(cs)})"; del A[d]; continue
    c = np.array([x[0] for x in cs]); se1 = np.array([x[1] for x in cs]); se2 = np.array([x[2] for x in cs])
    def q(se):
        w = 1 / se ** 2; cb = (w * c).sum() / w.sum(); Q = (w * (c - cb) ** 2).sum()
        return Q, float(stats.chi2.sf(Q, len(c) - 1))
    resA[d] = dict(k=len(c), Q=q(se1)[0], p=q(se1)[1], p_indep=q(se2)[1])
print("A eligible", len(resA))
# source B
resB = {}
def V_of(rows):
    by = collections.defaultdict(dict)
    for r in rows:
        e = r["event_time"]
        if 0 <= e <= 3 and fin(r["att"]): by[r["g"]][e] = float(r["att"])
    vals = [np.mean([c[e] for e in range(4)]) for c in by.values() if all(e in c for e in range(4))]
    return vals
for d in list(Bd):
    obs = cohorts(gt[d], False)
    if len(obs) < 3: excl[d] = f"<3 cohorts ({len(obs)})"; continue
    ov = np.array([x[0] for x in obs])
    j = json.load(open(ROOT + src[d]["source"]["path"]))
    pp = j["l3_export_v2"]["placebo_profiles"]
    V1, V0, nb = [], [], len(pp)
    for pr in pp:
        v = V_of(pr["group_time"])
        if len(v) >= 3: V1.append(np.var(v, ddof=1)); V0.append(np.var(v, ddof=0))
    del j, pp; gc.collect()
    V1 = np.array(V1); V0 = np.array(V0)
    vo1, vo0 = np.var(ov, ddof=1), np.var(ov, ddof=0)
    def pv(V, vo): return float((1 + np.sum(V >= vo)) / (1 + len(V)))
    def loo(V):
        # leave-one-out p of each draw against the other draws
        srt = np.sort(V); n = len(V)
        ge = n - np.searchsorted(srt, V, side="left")  # count >= incl self
        return (1 + (ge - 1)) / (1 + (n - 1))
    pl1 = loo(V1); pl0 = loo(V0)
    resB[d] = dict(k=len(ov), ndraw=nb, nelig=len(V1), V=float(vo1), p=pv(V1, vo1), p_ddof0=pv(V0, vo0),
                   loo_rate=float(np.mean(pl1 < 0.10)), loo_rate0=float(np.mean(pl0 < 0.10)))
    print(d, resB[d], flush=True)
json.dump(dict(A=resA, B=resB, excl=excl), open(OUT + "CHECK_Q3_raw.json", "w"), indent=1)
