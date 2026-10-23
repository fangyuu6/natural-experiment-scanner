#!/usr/bin/env python3
"""Source table (URL, retrieval time, licence, hash, use) for the covariate file v2 inputs.

Joins RECEIPT_covariates_v2.json with data/l4/MANIFEST.csv (older inputs) and
data/l3_meta/covariates/raw/DOWNLOADS_B_20261015.csv (files fetched by package B).
Run:  python3 -I -B scripts/l3_meta/make_sources_table.py   -> data/l3_meta/covariates/SOURCES_covariates_v2.csv
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

COV = C.ROOT / "data/l3_meta/covariates"
rc = json.loads((COV / "RECEIPT_covariates_v2.json").read_text())
man = C.l4_manifest()
dl = {r["file_path"]: r for r in csv.DictReader(open(COV / "raw/DOWNLOADS_B_20261015.csv", newline="", encoding="utf-8"))}
USE = {
    "klarner_main": "dem_unified / rep_unified and chamber control, years <= 2011 (and prior-year governor for the 1 January clock, <= 2010)",
    "collectors_reconciled": "gov_party_jan1, gov_party_dec31, chamber majorities, 2011-2024 (two collectors; NCSL State and Legislative Partisan Composition tables as of Jan 31 or Apr 1 of each year; NGA governor terms)",
    "berry_ideology": "citizen ideology citi6016 1960-2016, carried forward 2017+",
    "bea_sainc1": "per capita personal income, line 3, current dollars",
    "census_county_adjacency_2026": "state land-border pairs (county pairs in different states)",
    "census_popest_2000_2010": "April 1 2000 base population = denominator of the 2000 urban share",
}
rows = []
for role, v in rc["inputs"].items():
    m = man.get(v["path"]) or dl.get(v["path"])
    note = USE.get(role)
    if note is None:
        note = ("CPI-U annual average (M13) for deflation" if role.startswith("bls_cpi") else "urban share by state for the decennial census year(s) of this file")
    rows.append([role, v["path"], (m or {}).get("url", ""), (m or {}).get("retrieved_utc") or (m or {}).get("retrieved_utc_shell_clock", ""),
                 (m or {}).get("licence", "see data/l4/political_control_2011_2024/RECONCILE_REPORT.md" if role == "collectors_reconciled" else ""),
                 v["bytes"], v["sha256"], "manifest sha256 equal" if m and m.get("sha256") == v["sha256"] else ("package B download" if v["path"] in dl else "not in a manifest"), note])
hdr = ["role", "path", "url", "retrieved_utc", "licence", "bytes", "sha256", "hash_check", "used_for"]
print(C.write_new(COV / "SOURCES_covariates_v2.csv", C.csv_bytes(hdr, rows)))
