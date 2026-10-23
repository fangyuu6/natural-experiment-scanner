#!/usr/bin/env python3
"""D2-V2 helper: writes the coder input file and the blank grid (write-once). No estimate, support flag per design, or sweep number is written.
The 12 policy groups are those of the 28 swept designs; the 46 series are the extension series with at least one SUPPORTED cell in the SWEEP-EXT files."""
import sys, csv, json, glob, os, collections
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import build_d2 as D2, sweep_core as S
ROOT = S.ROOT
OUT = ROOT / "data/l3_meta/d2_plausibility/v2"
R = ROOT / "data/l3_meta/refresh/20261018_r1"
inv = S.inventory_rows()
g = D2.design_groups(inv)
swept = {}
sers = set()
for p in sorted(glob.glob(str(R / "sweep_ext/*.npz"))):
    z = np.load(p, allow_pickle=False)
    meta = json.loads(str(z["meta_json"]))
    did = meta["design_id"]
    for s, c in zip(z["series_ids"], z["code"]):
        if str(c) == "SUPPORTED":
            sers.add(str(s)); swept[did] = g[did]
groups = sorted(set(swept.values()))
titles = collections.defaultdict(list)
for r in inv:
    if r.get("unit_kind") == "design" and r.get("policy_group") in groups:
        t = (r.get("unit_id"), r.get("policy"))
        if t not in titles[r["policy_group"]]:
            titles[r["policy_group"]].append(t)
cat = {r["outcome_key"]: r for r in csv.DictReader(open(ROOT / "data/l3_meta/sweep_ext_v1/SERIES_EXT_CATALOG_V1.csv"))}
sers = sorted(sers)
for path in (OUT / "d2v2_coder_input.md", OUT / "d2v2_blank_grid.csv"):
    if path.exists():
        sys.exit(f"exists: {path}")
lines = ["# D2-V2 coder input (blind: policy description and outcome names only)", "", "## Policy groups (12), with the policy titles of their designs", ""]
for gr in groups:
    lines.append(f"### {gr}")
    for uid, pol in sorted(titles[gr]):
        lines.append(f"- {pol}")
    lines.append("")
lines += ["## Outcome series (46)", "", "| series id | family | unit | population |", "|---|---|---|---|"]
for s in sers:
    c = cat[s]
    lines.append(f"| {s} | {c['family']} | {c['unit']} | {c['population']} |")
(OUT / "d2v2_coder_input.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
with open(OUT / "d2v2_blank_grid.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["policy_group", "series_id"])
    for gr in groups:
        for s in sers:
            w.writerow([gr, s])
print(len(groups), len(sers), len(groups) * len(sers))
