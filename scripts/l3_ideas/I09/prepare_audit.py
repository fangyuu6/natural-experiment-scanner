#!/usr/bin/env python3
"""Freeze a metadata-stratified pilot audit draw; writes no coding values."""
import collections
import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/plans/l3-ideas/I09"
SEED = 2026100610


def prepare():
    destination = OUT / "pilot_audit_sample.csv"
    if destination.exists():
        raise SystemExit("Audit draw already exists; preserve frozen bytes")
    strata = collections.defaultdict(list)
    with (OUT / "pilot_sample.csv").open() as f:
        for row in csv.DictReader(f):
            strata[row["stratum"]].append(row)
    selected = []
    for stratum, rows in sorted(strata.items()):
        n = min(len(rows), max(2, math.ceil(0.2 * len(rows))))
        ordered = sorted(rows, key=lambda r: hashlib.sha256(
            f"I09-audit|{SEED}|{r['source_text_id']}".encode()).hexdigest())
        for row in ordered[:n]:
            selected.append({"blind_id": row["blind_id"], "source_text_id": row["source_text_id"],
                             "stratum": stratum, "pilot_stratum_n": len(rows), "audit_stratum_n": n,
                             "conditional_audit_probability": n / len(rows),
                             "joint_probability": float(row["selection_probability"]) * n / len(rows)})
    with destination.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(selected[0]))
        w.writeheader()
        w.writerows(selected)
    (OUT / "AUDIT_MANIFEST.json").write_text(json.dumps({
        "seed": SEED, "audit_n": len(selected), "outcomes_coded": 0,
        "selection": "ceil(20%) per pilot metadata stratum, minimum two; census if smaller",
        "files": [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size,
                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                  for p in [OUT / "pilot_sample.csv", destination, Path(__file__)]],
    }, indent=2) + "\n")
    print(json.dumps({"audit_n": len(selected), "seed": SEED, "outcomes_coded": 0}))


if __name__ == "__main__":
    prepare()
