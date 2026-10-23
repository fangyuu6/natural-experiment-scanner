"""T289 paper vs real (prereg: docs/plans/l3-meta/PREREG_T289_PAPER_VS_REAL_20261023.md).
Run: .venv_did/bin/python -I -B scripts/l3_meta/build_t289_paper_vs_real.py"""
import json, os, re, collections, hashlib
LIVE = "data/l3_meta/refresh/20261023_r4/live/l2_set_live.json"
PRE = "docs/plans/l3-meta/PREREG_T289_PAPER_VS_REAL_20261023.md"
HASH = "docs/plans/l3-meta/PREREG_T289_PAPER_VS_REAL_20261023_HASH.txt"
OUT = "data/l3_meta/t289_paper_vs_real_20261023"
os.makedirs(OUT, exist_ok=True)
assert hashlib.sha256(open(PRE, "rb").read()).hexdigest() == open(HASH).read().strip(), "prereg hash mismatch"
PROC = ["dispens", "first payments", "weeks compensated", "continued weeks", "claims", "enroll", "take-up", "takeup", "filing", "application", "compliance", "uninsured", "insured", "coverage rate"]
END = ["employment", "wage", "earnings", "income", "mortality", "death", "fatalit", "homicide", "crime", "robbery", "assault", "birth", "fertility", "smoking", "binge", "overdose", "turnout", "hours", "emissions", "price", "sales", "taxes"]
def tag(o, x):
    t = ((o.get("outcome_id") or "") + " " + (o.get("label") or "")).strip()
    if not t: t = x.get("outcome_family") or ""
    t = t.lower().replace("_", " ")
    p = any(k in t for k in PROC); e = any(k in t for k in END)
    return "ambiguous" if p and e else "process" if p else "end" if e else "untagged"
d = json.load(open(LIVE))
est = [x for x in d["designs"] if x["status"] == "estimated"]
laws = collections.defaultdict(list); rows = []
for x in est:
    for o in x["primary_outcomes"] + x["secondary_outcomes"]:
        t = tag(o, x)
        r = dict(law=x["policy_family"], design_id=x["design_id"], outcome_id=o.get("outcome_id"), label=o.get("label"), tag=t, detected=bool(o.get("beyond_noise")))
        rows.append(r); laws[x["policy_family"]].append(r)
res = []
for l, rs in sorted(laws.items()):
    P = [r for r in rs if r["tag"] == "process"]; E = [r for r in rs if r["tag"] == "end"]
    res.append(dict(law=l, n_process=len(P), n_end=len(E), process_detected=any(r["detected"] for r in P), end_detected=any(r["detected"] for r in E),
                    designs=sorted({r["design_id"] for r in P + E})))
use = [r for r in res if r["n_process"] and r["n_end"]]
cell = collections.Counter(("both" if r["process_detected"] and r["end_detected"] else "process only" if r["process_detected"] else "end only" if r["end_detected"] else "neither") for r in use)
out = dict(prereg_sha256=open(HASH).read().strip(), live_as_of=d["as_of"], n_designs=len(d["designs"]), n_est_designs=len(est), N_laws=len(laws),
           tag_counts=dict(collections.Counter(r["tag"] for r in rows)), laws_with_process=sum(1 for r in res if r["n_process"]), laws_with_end=sum(1 for r in res if r["n_end"]),
           k_usable=len(use), usable_designs=len({x for r in use for x in r["designs"]}), stop=len(use) < 10, cells=dict(cell), usable=use,
           process_outcomes=[(r["law"], r["outcome_id"], r["detected"]) for r in rows if r["tag"] == "process"])
json.dump(out, open(f"{OUT}/RESULTS_T289.json", "w"), indent=1)
json.dump(rows, open(f"{OUT}/OUTCOME_TAGS.json", "w"), indent=1)
print(json.dumps(out, indent=1))
