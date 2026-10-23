import json, csv, collections
import numpy as np
from scipy import stats
ROOT = ""
OUT = "/path/to/scratch/scratch/T256/check/"
I = ROOT + "data/l3_meta/new_angles_20261021/inputs/"
R = json.load(open(OUT + "CHECK_Q3_raw.json")); A, B, ex = R["A"], R["B"], R["excl"]
ro = {r["design_id"]: r for r in csv.DictReader(open(I + "design_roster.csv"))}
pk = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(I + "policy_keys.csv"))}
dom = lambda d: "other" if d in ("other", "education") else d
print("excluded", len(ex), ex)
print("A", len(A), "B", len(B))
def binom1(k, n, p=0.10): return float(stats.binom.sf(k - 1, n, p))
rows = {}
for d, v in A.items(): rows[d] = dict(src="A", p=v["p"], p_ind=v["p_indep"])
for d, v in B.items(): rows[d] = dict(src="B", p=v["p"], p_ind=v["p"])
def summ(ds, key="p", ref=0.10):
    n = len(ds); k = sum(rows[d][key] < 0.10 for d in ds)
    return dict(n=n, k=k, share=k / n if n else None, p_binom=binom1(k, n, ref) if n else None)
allds = list(rows)
out = dict(pooled=summ(allds), A=summ([d for d in allds if rows[d]["src"] == "A"]), B=summ([d for d in allds if rows[d]["src"] == "B"]),
           A_indep=summ([d for d in allds if rows[d]["src"] == "A"], "p_ind"), pooled_with_indepA=summ(allds, "p_ind"))
out["B_ddof0"] = dict(k=sum(v["p_ddof0"] < 0.10 for v in B.values()), n=len(B))
out["B_loo_rate_mean"] = float(np.mean([v["loo_rate"] for v in B.values()])); out["B_loo_rate_max"] = float(max(v["loo_rate"] for v in B.values()))
out["B_loo_rate_pooled_drawweighted"] = float(np.average([v["loo_rate"] for v in B.values()], weights=[v["nelig"] for v in B.values()]))
bd = collections.defaultdict(list)
for d in allds: bd[dom(ro[d]["domain"])].append(d)
out["by_domain"] = {k: summ(v) for k, v in bd.items() if len(v) >= 8}
out["by_domain_all"] = {k: len(v) for k, v in bd.items()}
# one vote per law: share of p<.10 among designs within law, law counted as share>=0.5 / mean
laws = collections.defaultdict(list)
for d in allds: laws[pk[d]].append(d)
lv = [np.mean([rows[d]["p"] < 0.10 for d in v]) for v in laws.values()]
out["one_vote_law_mean_share"] = float(np.mean(lv)); out["one_vote_law_majority_share"] = float(np.mean([x > 0.5 for x in lv])); out["n_laws"] = len(laws)
kk = sum(x >= 0.5 for x in lv); out["one_vote_ge_half_share"] = float(kk / len(lv))
# leave-one-law-out
lol = []
for l in laws:
    ds = [d for d in allds if pk[d] != l]; lol.append(summ(ds)["share"])
out["lol_share_range"] = [min(lol), max(lol)]
lod = {k: summ([d for d in allds if dom(ro[d]["domain"]) != k]) for k in bd}
out["leave_one_domain_out"] = lod
out["pvals_sorted_A"] = sorted(round(rows[d]["p"], 4) for d in A)
out["design_p"] = {d: (r["src"], r["p"]) for d, r in rows.items()}
json.dump(out, open(OUT + "CHECK_Q3.json", "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "design_p"}, indent=1))
