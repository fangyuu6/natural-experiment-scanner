#!/usr/bin/env python3
"""T233 independent checks written by the coordinator (not the script authors): Q4 rho, Q6 A1/A2 observed shares, Q8 era split and
no-duplicate sensitivity (added after the plan), Q4 like-for-like excess (added after the plan). Writes states_decide/checks/CHECKS_T233.json."""
import json, re, sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import rankdata, spearmanr
sys.dont_write_bytecode = True
SD = Path(__file__).resolve().parents[3] / "data/l3_meta/new_angles_20261021/states_decide"
out = {}
b = pd.read_csv(SD / "q4/blocks_q4.csv")
def rho(df, zc):
    df = df[df.complete & df[zc].notna()]
    rs, rz = [], []
    for _, g in df.groupby("domain"):
        r1, r2 = rankdata(g.S5), rankdata(g[zc]); rs += list(r1 - r1.mean()); rz += list(r2 - r2.mean())
    return float(np.corrcoef(rs, rz)[0, 1]), len(df)
out["q4_rho"] = rho(b, "Z")
both = b[b.refZ.notna()]
out["q4_same_blocks_real_vs_ref"] = [rho(both, "Z"), rho(both, "refZ")]
s = pd.read_csv(SD / "inputs/spid_adoption.csv"); c = pd.read_csv(SD / "inputs/state_year_calendar.csv")
e = s[s.adopt_year.between(1962, 2016) & (s.date_conflict == 0)]; n = e.groupby("policy").size(); e = e[e.policy.isin(n[n >= 10].index)]
m = e.merge(c, left_on=["state", "adopt_year"], right_on=["state", "year"])
a1 = m[~m.state.isin(["MT", "NV", "ND", "TX"])]
out["q6_A1_share"] = float(a1.groupby("policy").lower_house_election.mean().mean()); out["q6_A1_events"] = len(a1)
a2 = m[m.downturn.notna()]; out["q6_A2_share"] = float(a2.groupby("policy").downturn.mean().mean()); out["q6_A2_events"] = len(a2)
w = s[(s.complete_window == 1) & (s.date_conflict == 0)]; n = w.groupby("policy").size(); w = w[w.policy.isin(n[n >= 10].index)].copy()
w["o"] = w.groupby("policy").adopt_year.transform(lambda x: (rankdata(x) - 1) / (len(x) - 1))
pol = w.groupby("policy").agg(fy=("first_year", "first"))
med = pol.fy.median(); pa = pol[pol.fy < med].index; pb = pol[pol.fy >= med].index
sa = w[w.policy.isin(pa)].groupby("state").o.mean(); sb = w[w.policy.isin(pb)].groupby("state").o.mean()
out["q8_era_rho"] = float(spearmanr(sa, sb.reindex(sa.index))[0]); out["q8_policies"] = len(pol)
(SD / "checks").mkdir(exist_ok=True)
(SD / "checks/CHECKS_T233.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out))
