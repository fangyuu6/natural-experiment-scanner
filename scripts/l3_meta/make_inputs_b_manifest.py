#!/usr/bin/env python3
"""Hash manifest of every file written by L3 package B (path, bytes, sha256).

Run:  python3 -I -B scripts/l3_meta/make_inputs_b_manifest.py [--out-name NAME]
      -> data/l3_meta/INPUTS_B_MANIFEST_20261015_r2.csv (not listed in itself; r1 predates the last doc and script edits)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out-name", default="INPUTS_B_MANIFEST_20261015_r2.csv")
OUT_NAME = ap.parse_args().out_name
R = C.ROOT
files = []
for d in ("covariates", "party_coding", "q2_join", "n1_sets"):
    files += sorted(p for p in (R / "data/l3_meta" / d).rglob("*") if p.is_file())
files += [R / "scripts/l3_meta" / n for n in (
    "inputs_b_common.py", "build_covariates.py", "build_party_histories.py", "build_q2_join.py", "build_n1_sets.py",
    "hash_party_packages.py", "make_sources_table.py", "check_n1_cohorts.py", "make_inputs_b_manifest.py", "test_inputs_b.py")]
files.append(R / "docs/plans/l3-meta/INPUTS_B.md")
rows = [[C.rel(p), p.stat().st_size, C.sha256_file(p)] for p in files]
print(C.write_new(R / "data/l3_meta" / OUT_NAME, C.csv_bytes(["path", "bytes", "sha256"], rows)))
print(len(rows), "files")
