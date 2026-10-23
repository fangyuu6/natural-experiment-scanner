#!/usr/bin/env python3
"""Manifest (path, bytes, sha256) of every file written for the Q2-V2 / S_PRE_L3_V1 / blockers work of 2026-10-16.  Written once, last.

Run:  python3 -I -B scripts/l3_meta/make_q2_v2_manifest.py
"""
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/l3_meta/q2_v2/Q2_V2_MANIFEST_20261016.csv"
FILES = [
    "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2.md", "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2_HASH.txt",
    "docs/plans/l3-meta/RESULTS_Q2_V2.md", "docs/plans/l3-meta/Q2_L2_REQUESTS_20261016.md",
    "scripts/l3_meta/build_s_pre_l3.py", "scripts/l3_meta/test_build_s_pre_l3.py", "scripts/l3_meta/build_q2_v2.py", "scripts/l3_meta/test_build_q2_v2.py",
    "scripts/l3_meta/check_q2_v2_independent.py", "scripts/l3_meta/build_q2_blockers.py", "scripts/l3_meta/build_q2_new_design_candidates.py",
    "scripts/l3_meta/yrbs_window_grid.py", "scripts/l3_meta/make_q2_v2_manifest.py",
    "data/l3_meta/s_pre_l3_v1/S_PRE_L3_V1.json", "data/l3_meta/s_pre_l3_v1/RUN_LOG.jsonl",
    "data/l3_meta/q2_v2/RESULTS_Q2_V2.json", "data/l3_meta/q2_v2/RUN_LOG.jsonl", "data/l3_meta/q2_v2/l2_set_live_used.json",
    "data/l3_meta/q2_v2/Q2_BLOCKERS_20261016.csv", "data/l3_meta/q2_v2/Q2_CATALOGUE_FUNNEL_20261016.json",
    "data/l3_meta/q2_v2/Q2_BLOCKERS_20261016.draft1_superseded.csv", "data/l3_meta/q2_v2/Q2_CATALOGUE_FUNNEL_20261016.draft1_superseded.json",
    "data/l3_meta/q2_v2/Q2_BLOCKERS_20261016.draft2_superseded.csv", "data/l3_meta/q2_v2/Q2_CATALOGUE_FUNNEL_20261016.draft2_superseded.json",
    "data/l3_meta/q2_v2/Q2_V2_MANIFEST_20261016.r1_superseded.csv", "data/l3_meta/q2_v2/Q2_V2_MANIFEST_20261016.r2_superseded.csv",
    "data/l3_meta/q2_v2/Q2_YRBS_WINDOW_GRID_20261016.csv", "data/l3_meta/q2_v2/Q2_NEW_DESIGN_BENCHMARK_CANDIDATES_20261016.csv",
]
for sub in ("cards", "pending_unbound_copy"):
    FILES += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "data/l3_meta/q2_v2/prescreen_dry_run" / sub).glob("*.json"))
FILES += ["data/l3_meta/q2_v2/prescreen_dry_run/dry_run_report.json", "data/l3_meta/q2_v2/prescreen_dry_run/dry_run_report_first_pass_seatbelt_unbound.json"]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


if OUT.exists():
    sys.exit("manifest exists: not overwritten")
with open(OUT, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["path", "bytes", "sha256"])
    for f in FILES:
        p = ROOT / f
        w.writerow([f, p.stat().st_size, sha(p)])
print(len(FILES), "files listed")
