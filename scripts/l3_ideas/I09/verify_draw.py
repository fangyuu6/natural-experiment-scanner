#!/usr/bin/env python3
"""Independent streaming reconstruction of selection and descriptive coverage."""
import collections
import csv
import hashlib
import heapq
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/plans/l3-ideas/I09"


def verify():
    manifest = json.loads((OUT / "FRAME_MANIFEST.json").read_text())
    allocation = {r["stratum"]: r["pilot_n"] for r in manifest["strata"]}
    heaps = collections.defaultdict(list)
    sizes = collections.Counter()
    coverage = collections.Counter()
    years = collections.defaultdict(collections.Counter)
    seen = set()
    issues = []
    with (OUT / "frame.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            pid, key = row["source_text_id"], row["stratum"]
            if pid in seen:
                issues.append("duplicate_frame_id")
            seen.add(pid)
            sizes[key] += 1
            coverage[(row["sampling_lane"], row["authority"], row["year"], row["domain"])] += 1
            years[row["authority"]][row["year"]] += 1
            # Negative numeric hash makes a bounded max-heap independent of builder's sort.
            rank = int(hashlib.sha256(f"I09|{manifest['seed']}|draw|{pid}".encode()).hexdigest(), 16)
            heap = heaps[key]
            heapq.heappush(heap, (-rank, pid))
            if len(heap) > allocation[key]:
                heapq.heappop(heap)
    expected = {pid for heap in heaps.values() for _, pid in heap}
    with (OUT / "pilot_sample.csv").open() as f:
        pilot = list(csv.DictReader(f))
    if expected != {r["source_text_id"] for r in pilot}:
        issues.append("draw_does_not_reproduce")
    if len(seen) != manifest["frame_n"]:
        issues.append("frame_size")
    if dict(sizes) != {r["stratum"]: r["frame_n"] for r in manifest["strata"]}:
        issues.append("stratum_counts")
    with (OUT / "pilot_audit_sample.csv").open() as f:
        audit = list(csv.DictReader(f))
    if not {r["source_text_id"] for r in audit} <= expected or len(audit) != 72:
        issues.append("audit_membership_or_size")
    groups = collections.defaultdict(list)
    for row in pilot:
        groups[row["stratum"]].append(row)
    audit_expected = set()
    import math
    for key, rows in groups.items():
        n = min(len(rows), max(2, math.ceil(0.2 * len(rows))))
        ordered = sorted(rows, key=lambda r: hashlib.sha256(
            f"I09-audit|2026100610|{r['source_text_id']}".encode()).hexdigest())
        audit_expected.update(r["source_text_id"] for r in ordered[:n])
    if audit_expected != {r["source_text_id"] for r in audit}:
        issues.append("audit_draw_does_not_reproduce")
    result = {"issues": issues, "frame_n": len(seen), "pilot_n": len(pilot), "audit_n": len(audit),
              "strata_n": len(sizes), "outcomes_coded": 0, "association_calls": 0,
              "authority_years": {a: {"known_min": min(int(y) for y in c if y != "unknown"),
                                      "known_max": max(int(y) for y in c if y != "unknown"),
                                      "unknown_n": c["unknown"]} for a, c in years.items()}}
    destination = OUT / "coverage_by_lane_authority_year_domain.csv"
    if not destination.exists():
        with destination.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["sampling_lane", "authority", "metadata_year", "domain", "frame_n"])
            w.writerows((*key, count) for key, count in sorted(coverage.items()))
    else:
        with destination.open() as f:
            existing = {(r["sampling_lane"], r["authority"], r["metadata_year"], r["domain"]): int(r["frame_n"])
                        for r in csv.DictReader(f)}
        if existing != dict(coverage):
            issues.append("coverage_file_changed")
    print(json.dumps(result))
    return bool(issues)


if __name__ == "__main__":
    raise SystemExit(verify())
