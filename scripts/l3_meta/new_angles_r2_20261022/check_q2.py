"""T256 checker B, Q2 (independent, from plan only)."""
import csv, json, math, collections, itertools
import numpy as np
ROOT = ""
I = ROOT + "data/l3_meta/new_angles_20261021/inputs/"
OUT = "/path/to/scratch/scratch/T256/check/"
ro = {r["design_id"]: r for r in csv.DictReader(open(I + "design_roster.csv"))}
pk = {r["design_id"]: r for r in csv.DictReader(open(I + "policy_keys.csv"))}
print("policy_keys cols", list(next(iter(pk.values())).keys()))
es = collections.defaultdict(dict)
for r in csv.DictReader(open(I + "event_study_export.csv")):
    e = int(r["event_time"]); a = r["att"]; s = r["se"]
    es[r["design_id"]][e] = (float(a) if a not in ("", "nan") else None, float(s) if s not in ("", "nan") else None, r["thin"])
dom = lambda d: "other" if d in ("other", "education") else d
def sgn(x): return (x > 0) - (x < 0)
def onset(d, rule):
    ov = float(ro[d]["overall_att"]); so = sgn(ov); E = es[d]
    pre = [abs(E[e][0]) for e in range(-4, 0) if e in E and E[e][0] is not None]
    Ed = max(pre)
    def vis(e):
        if e not in E or E[e][0] is None: return False
        a, s, _ = E[e]
        if sgn(a) != so: return False
        if rule == "env": return abs(a) > Ed
        return s is not None and s > 0 and abs(a / s) >= 1.96
    for e in range(0, 5):
        if vis(e) and vis(e + 1): return e
    return 6
elig = []
for d in ro:
    if d not in es: continue
    E = es[d]
    npre = sum(1 for e in range(-4, 0) if e in E and E[e][0] is not None)
    npost = sum(1 for e in range(0, 6) if e in E and E[e][0] is not None)
    if npre >= 3 and npost >= 5: elig.append(d)
print("eligible", len(elig))
sec = [d for d in elig if all(E_ok for E_ok in [all(e in es[d] and es[d][e][1] is not None for e in range(0, 6))])]
print("with se in 0..5 (all)", len(sec))
sec2 = [d for d in elig if any(es[d][e][1] is not None for e in es[d])]
print("with any se", len(sec2))
rng = np.random.default_rng(20261022 + 7)
def gap_stat(T, labs, minn=8):
    g = collections.defaultdict(list)
    for t, l in zip(T, labs): g[l].append(t)
    ks = [k for k in g if len(g[k]) >= minn]
    med = {k: float(np.median(g[k])) for k in ks}
    best = 0.0; pair = None
    for a, b in itertools.combinations(ks, 2):
        v = abs(med[a] - med[b])
        if v > best: best, pair = v, (a, b)
    return best, pair, med
def run(ds, rule, label, onevote=False, B=2000):
    T = np.array([onset(d, rule) for d in ds]); labs = [dom(ro[d]["domain"]) for d in ds]; src = [ro[d]["source_group"] for d in ds]
    res = dict(label=label, n=len(ds))
    if len(ds) < 30: res["verdict"] = "not enough data"; return res
    gap, pair, med = gap_stat(T, labs)
    byd = {}
    for k in sorted(set(labs)):
        t = T[[i for i, l in enumerate(labs) if l == k]]
        byd[k] = dict(n=len(t), median=float(np.median(t)), share_by2=float(np.mean(t <= 2)), never=float(np.mean(t == 6)))
    def null(strat):
        out = []
        for _ in range(B):
            L = np.array(labs, dtype=object)
            if strat:
                for s in set(src):
                    idx = [i for i, x in enumerate(src) if x == s]
                    L[idx] = rng.permutation(L[idx])
            else: L = rng.permutation(L)
            out.append(gap_stat(T, list(L))[0])
        return np.array(out)
    ns, nu = null(True), null(False)
    res.update(gap=gap, pair=pair, domains=byd, med=med,
               p95_strat=float(np.percentile(ns, 95)), pct_strat=float(np.mean(ns < gap)), p_ge_strat=float((np.sum(ns >= gap) + 1) / (B + 1)),
               p95_unstrat=float(np.percentile(nu, 95)), p_ge_unstrat=float((np.sum(nu >= gap) + 1) / (B + 1)),
               overall_median=float(np.median(T)), share_by2=float(np.mean(T <= 2)), never=float(np.mean(T == 6)))
    res["pattern"] = bool(gap >= 2 and gap > res["p95_strat"])
    return res
R = {}
R["primary"] = run(elig, "env", "primary envelope")
R["secondary"] = run(sec, "se", "secondary se rule (all se present 0..5)")
R["secondary_any"] = run(sec2, "se", "secondary se rule (any se)")
# one vote per law: median T within law
law = lambda d: pk[d].get("policy_key", d) if d in pk else d
laws = collections.defaultdict(list)
for d in elig: laws[law(d)].append(d)
print("laws among eligible", len(laws))
# law-level: build pseudo-designs
Tl = np.array([np.median([onset(d, "env") for d in v]) for v in laws.values()])
labl = [dom(ro[v[0]]["domain"]) for v in laws.values()]; srcl = [ro[v[0]]["source_group"] for v in laws.values()]
gl, pl, ml = gap_stat(Tl, labl)
nl = []
for _ in range(2000):
    L = np.array(labl, dtype=object)
    for s in set(srcl):
        idx = [i for i, x in enumerate(srcl) if x == s]; L[idx] = rng.permutation(L[idx])
    nl.append(gap_stat(Tl, list(L))[0])
R["one_vote"] = dict(n=len(Tl), gap=gl, pair=pl, med=ml, p95=float(np.percentile(nl, 95)))
# leave-one-law-out
lol = []
for k in laws:
    ds = [d for d in elig if law(d) != k]
    T = np.array([onset(d, "env") for d in ds]); labs = [dom(ro[d]["domain"]) for d in ds]
    lol.append(gap_stat(T, labs)[0])
R["leave_one_law_out_gap_range"] = [min(lol), max(lol)]
# sensitivities: censor=5, drop thin
Tb = np.array([onset(d, "env") for d in elig]); labs = [dom(ro[d]["domain"]) for d in elig]
R["sens_censor5_gap"] = gap_stat(np.where(Tb == 6, 5, Tb), labs)[0]
def onset_nothin(d):
    saved = es[d]; es[d] = {e: v for e, v in saved.items() if v[2] not in ("True", "true", "1")}
    try:
        E = es[d]; ok = sum(1 for e in range(-4, 0) if e in E) >= 3
        return onset(d, "env") if ok else None
    finally: es[d] = saved
Tn = [(onset_nothin(d), l) for d, l in zip(elig, labs)]
Tn = [(t, l) for t, l in Tn if t is not None]
R["sens_dropthin_gap"] = gap_stat([t for t, l in Tn], [l for t, l in Tn])[0]; R["sens_dropthin_n"] = len(Tn)
R["per_design_T"] = {d: onset(d, "env") for d in elig}
json.dump(R, open(OUT + "CHECK_Q2.json", "w"), indent=1, default=str)
for k in ("primary", "secondary", "secondary_any"):
    r = R[k]; print(k, {x: r[x] for x in r if x != "domains"}); print(r.get("domains"))
print({k: R[k] for k in R if k not in ("primary", "secondary", "secondary_any", "per_design_T")})
