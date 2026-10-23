#!/usr/bin/env python3
"""T233 prep (rule id EX-1, exploratory): L1 adoption table (SPID v1.2 native, 724 policies) + state calendar tables.

Writes data/l3_meta/new_angles_20261021/states_decide/inputs/:
  spid_adoption.csv    policy, state (USPS), adopt_year, majortopic_str, source, first_year, last_year, n_adopters, description, date_conflict (0/1),
                       complete_window (first_year >= 1961 and last_year <= 2016)
  state_year_calendar.csv   state, year (1961-2016), lower_house_election (0/1), annual_session (0/1), income_growth_lag (ln real pcpi y-1 minus y-2),
                       downturn (bottom tercile of the state's own growth distribution 1962-2016, 1/0), region (Census), dem_unified, ideology, ln_inc, urban
  PREP_L1_RECEIPT.json

Election-year table rule (fixed before any statistic): a year is an election year for a state when the state's LOWER HOUSE holds a general election.
  two-year terms: even years, except NJ and VA (odd years);
  four-year lower-house terms: AL and MD (years = 2 mod 4: 1962, 1966, ...), LA and MS (years = 3 mod 4: 1963, 1967, ...).
annual_session = 0 for MT, NV, ND, TX (legislatures that meet in odd years only for most of 1961-2016), else 1.
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/prep_l1.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = ROOT / "data/l3_meta/new_angles_20261021/states_decide/inputs"
D = ROOT / "data/l3_pooled/downloads"
Y0, Y1 = 1961, 2016

ABBR = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
        "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
        "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
        "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
        "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
        "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"}
REGION = {  # Census regions
    **{s: "Northeast" for s in "CT ME MA NH RI VT NJ NY PA".split()},
    **{s: "Midwest" for s in "IL IN MI OH WI IA KS MN MO NE ND SD".split()},
    **{s: "South" for s in "DE FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX".split()},
    **{s: "West" for s in "AZ CO ID MT NV NM UT WY AK CA HI OR WA".split()}}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def election_year(state, y):
    if state in ("NJ", "VA"):
        return int(y % 2 == 1)
    if state in ("AL", "MD"):
        return int(y % 4 == 2)
    if state in ("LA", "MS"):
        return int(y % 4 == 3)
    return int(y % 2 == 0)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    s = pd.read_csv(D / "SPID_v1.2.tab", sep="\t")
    p = pd.read_csv(D / "SPID_v1.2_policies.tab", sep="\t")
    rec = pd.read_csv(ROOT / "data/l3_pooled/adoption_year_reconciliation.csv", usecols=["spid_policy", "status"])
    conflict = set(rec.loc[rec.status == "conflicting_year", "spid_policy"])
    s["state"] = s["statenam"].map(ABBR)
    assert s["state"].notna().all()
    s = s.merge(p[["policy", "first_year", "last_year", "majortopic_str", "source", "description"]], on="policy", how="left")
    s["n_adopters"] = s.groupby("policy")["state"].transform("count")
    s["date_conflict"] = s["policy"].isin(conflict).astype(int)
    s["complete_window"] = ((s["first_year"] >= Y0) & (s["last_year"] <= Y1)).astype(int)
    cols = ["policy", "state", "adopt_year", "majortopic_str", "source", "first_year", "last_year", "n_adopters", "description", "date_conflict", "complete_window"]
    s[cols].sort_values(["policy", "adopt_year", "state"]).to_csv(OUT / "spid_adoption.csv", index=False)

    cov = pd.read_csv(ROOT / "data/l3_meta/covariates/state_year_v2.csv", dtype={"fips": str})
    cov = cov[cov["state"].isin(ABBR.values())].copy()
    rows = []
    for st, g in cov.groupby("state"):
        g = g.set_index("year").sort_index()
        inc = g["ln_pcpi_real_2024usd"]
        gr = inc.shift(1) - inc.shift(2)  # growth between y-2 and y-1, known in year y
        own = gr.loc[Y0 + 1:Y1].dropna()
        cut = float(np.quantile(own, 1 / 3))
        for y in range(Y0, Y1 + 1):
            gv = gr.get(y, np.nan)
            rows.append(dict(state=st, year=y, lower_house_election=election_year(st, y), annual_session=int(st not in ("MT", "NV", "ND", "TX")),
                             income_growth_lag=gv, downturn=(int(gv <= cut) if gv == gv else ""), region=REGION[st],
                             dem_unified=g.loc[y, "dem_unified_jan1"] if y in g.index else "", ideology=g.loc[y, "citizen_ideology"] if y in g.index else "",
                             ln_inc=inc.get(y, np.nan), urban=g.loc[y, "urban_share"] if y in g.index else ""))
    pd.DataFrame(rows).to_csv(OUT / "state_year_calendar.csv", index=False)
    receipt = dict(inputs={str(x.relative_to(ROOT)): sha(x) for x in (D / "SPID_v1.2.tab", D / "SPID_v1.2_policies.tab",
                                                                     ROOT / "data/l3_pooled/adoption_year_reconciliation.csv",
                                                                     ROOT / "data/l3_meta/covariates/state_year_v2.csv")},
                   counts=dict(policies=int(s.policy.nunique()), rows=len(s), complete_window_policies=int(s[s.complete_window == 1].policy.nunique()),
                               complete_window_ge10=int((s[s.complete_window == 1].groupby("policy").size() >= 10).sum()),
                               conflict_policies=len(conflict & set(s.policy)), state_years=len(rows)))
    (OUT / "PREP_L1_RECEIPT.json").write_text(json.dumps(receipt, indent=1))
    print(receipt["counts"])


if __name__ == "__main__":
    main()
