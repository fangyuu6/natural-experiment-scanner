#!/usr/bin/env python3
"""Export validated registry snapshots for later static-site inlining; never publish."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .validate import validate_registry
except ImportError:
    from validate import validate_registry


def export_static(registry, artifact_root=None, previous=None):
    errors = validate_registry(registry, artifact_root, previous)
    if errors:
        raise ValueError("Invalid registry: " + "; ".join(errors))
    specs = {s["outcome_spec_id"]: s for s in registry["outcome_specs"]}
    runs = {r["run_id"]: r for r in registry["runs"]}
    records = []
    for design in sorted(registry["designs"], key=lambda d: d["design_id"]):
        record = dict(design)
        record["outcome_specs"] = [
            {"outcome_spec_id": s["outcome_spec_id"], "outcome_id": s["outcome_id"],
             "spec_version": s["spec_version"], "outcome_label": s["outcome_label"], "unit": s["unit"],
             "supersedes": s["supersedes"],
             "freeze_bundle_sha256": s["freeze_bundle"]["sha256"] if s["freeze_bundle"] else None}
            for s in specs.values() if s["design_id"] == design["design_id"]
        ]
        record["runs"] = [r for r in runs.values() if r["design_id"] == design["design_id"]]
        records.append(record)
    counts = {"source_texts": len(registry["source_texts"]), "policy_families": len(registry["policy_families"]),
              "legal_instruments": len(registry["legal_instruments"]), "designs": len(records),
              "outcome_specs": len(specs), "runs": len(runs)}
    for key in ("status", "design_type", "domain"):
        counts[key] = dict(sorted(Counter(d[key] for d in registry["designs"]).items()))
    return {"export_version": "1.0.0", "registry_version": registry["registry_version"],
            "generated_at": registry["generated_at"], "counts": counts,
            "source_texts": registry["source_texts"], "policy_families": registry["policy_families"],
            "legal_instruments": registry["legal_instruments"], "records": records}


def inline_safe_json(value):
    """JSON safe to embed as application/json in static HTML (no closing script tag)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).replace(
        "<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text())
    previous = json.loads(args.previous.read_text()) if args.previous else None
    try:
        value = export_static(registry, args.artifact_root, previous)
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(inline_safe_json(value) + "\n")
    print(json.dumps({"output": str(args.output), "counts": value["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
