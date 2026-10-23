#!/usr/bin/env python3
"""T232: Q2 descriptive event-time profiles by domain (aligned standardized event_study ATT, median over designs). EXPLORATORY.
Writes data/l3_meta/new_angles_20261021/q2_domain_profiles.csv"""
import sys, csv, collections
from pathlib import Path
import numpy as np
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_new_angles as R
D = R.load()
OUT = R.OUT
prof = collections.defaultdict(lambda: collections.defaultdict(list))
ndes = collections.Counter()
for did, d in D.items():
    if d["quarterly"] or not d["sd"] or d["sign"] == 0:
        continue
    ndes[d["domain"]] += 1
    for r in d["es"]:
        e = int(float(r["event_time"])); a = R.fnum(r["att"])
        if a is None or e < 0 or e > 8:
            continue
        prof[d["domain"]][e].append(a / d["sd"] * d["sign"])
rows = []
for u in sorted(prof):
    for e in range(0, 9):
        v = prof[u][e]
        rows.append(dict(domain=u, event_time=e, n_designs=len(v), n_designs_domain=ndes[u], shown=int(len(v) >= 0.5 * ndes[u] and ndes[u] >= 5),
                         median=float(np.median(v)) if v else None, q25=float(np.percentile(v, 25)) if v else None, q75=float(np.percentile(v, 75)) if v else None))
R.write_csv(OUT / "q2_domain_profiles.csv", rows)
for r in rows:
    if r["shown"] and r["event_time"] in (0, 2, 4, 6, 8):
        print(r["domain"], r["event_time"], r["n_designs"], round(r["median"], 2))

# ---- guard: the same median profile under random-timing placebo draws (29 designs with stored placebo group_time) ----
import json
live = json.load(open(R.LIVE))
src = {x["design_id"]: x for x in live["designs"]}
def agg_profile(rows, sd, sign):
    num = collections.defaultdict(float); den = collections.defaultdict(float)
    for g, e, a, n in rows:
        if e < 0 or e > 8 or a is None or not n:
            continue
        num[e] += a * n; den[e] += n
    return {e: num[e] / den[e] / sd * sign for e in num}
real_p, plac_p = {}, {}
for did, d in D.items():
    if d["source_group"] != "autopilot_estimate" or not d["sd"] or d["sign"] == 0:
        continue
    real_p[did] = agg_profile([(g, e, a, n) for g, e, a, n, p in d["rows"]], d["sd"], d["sign"])
    j = json.load(open(R.ROOT / src[did]["source"]["path"]))
    pp = (j.get("l3_export_v2") or {}).get("placebo_profiles") or []
    lst = []
    for pr in pp:
        rr = [(r["g"], r["event_time"], r["att"], r["n_treated"]) for r in pr["group_time"]]
        sg = R.C.design_sign([(e, a, n) for g, e, a, n in rr])
        lst.append(agg_profile(rr, d["sd"], sg) if sg else {})
    plac_p[did] = lst
ids = [i for i in real_p if plac_p[i]]
nd = min(len(plac_p[i]) for i in ids)
out = []
for e in range(0, 9):
    rv = [real_p[i][e] for i in ids if e in real_p[i]]
    draws = []
    for b in range(nd):
        v = [plac_p[i][b][e] for i in ids if e in plac_p[i][b]]
        draws.append(np.median(v) if len(v) >= 5 else np.nan)
    draws = np.array(draws)
    out.append(dict(event_time=e, n_designs_real=len(rv), real_median=float(np.median(rv)), placebo_mean_of_medians=float(np.nanmean(draws)),
                    placebo_p05=float(np.nanpercentile(draws, 5)), placebo_p95=float(np.nanpercentile(draws, 95))))
R.write_csv(OUT / "q2_profile_vs_placebo.csv", out)
for o in out:
    print({k: (round(v, 2) if isinstance(v, float) else v) for k, v in o.items()})
