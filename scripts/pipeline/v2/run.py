#!/usr/bin/env python3
"""Rebuild lean pilot comparisons and descriptive adoption counts offline.

Run from the repository root with PYTHONDONTWRITEBYTECODE=1 python3 -m
scripts.pipeline.v2.run. No outcome association or estimator is invoked.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "data/pipeline/v2"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    from . import l2, l3

    OUTPUT.mkdir(parents=True, exist_ok=True)
    summaries = {"l2": l2.run(ROOT, OUTPUT), "l3": l3.run(ROOT, OUTPUT)}
    (OUTPUT / "summary.json").write_text(
        json.dumps(summaries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    files = sorted(p for p in OUTPUT.glob("*")
                   if p.is_file() and p.name != "output_manifest.json")
    code = sorted(p for p in Path(__file__).parent.glob("*.py") if p.is_file())
    documents = [ROOT / "docs/plans/PROTOCOL_V2_LEAN.md",
                 ROOT / "docs/plans/PROTOCOL_V2_RESULTS.md"]
    manifest = {
        "schema_version": 2,
        "network_requests": 0,
        "new_public_downloads": [],
        "outcome_association_calls": 0,
        "estimation_calls": 0,
        "source_handling": "Existing structured pilot data only; no PDF/web page reopened or separate scan claimed.",
        "outputs": [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size,
                     "sha256": digest(p)} for p in files],
        "code": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)} for p in code],
        "documents": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p),
                       "lines": len(p.read_text().splitlines())} for p in documents],
    }
    (OUTPUT / "output_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"output": str(OUTPUT.relative_to(ROOT)),
                      "files": len(files), "estimation_calls": 0}))


if __name__ == "__main__":
    main()
