#!/usr/bin/env python3
"""Convert queue-v2 designs into a provisional registry; no readiness is inferred."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from .validate import COMMON_CHECKS, TRACK_CHECKS, validate_registry
except ImportError:
    from validate import COMMON_CHECKS, TRACK_CHECKS, validate_registry

FILES = {"did": "did_designs.jsonl", "rdd": "rdd_candidates.jsonl", "rct_lottery": "rct_mentions.jsonl"}


def provisional_family(source):
    """Missing family labels stay unique to a source; no invented common intervention."""
    return "family-unresolved-" + hashlib.sha256(source["source_text_id"].encode()).hexdigest()[:20]


def import_queue(queue_dir, design_ids=None, generated_at=None):
    queue_dir = Path(queue_dir)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    summary = json.loads((queue_dir / "summary.json").read_text())
    queue_version = summary["version"]
    sources, families, instruments, designs = {}, {}, {}, []
    found_ids = set()
    for track, filename in FILES.items():
        with (queue_dir / filename).open() as stream:
            for line_number, line in enumerate(stream, 1):
                row = json.loads(line)
                design_id = row["design_id"]
                if design_ids is not None and design_id not in design_ids:
                    continue
                if design_id in found_ids:
                    raise ValueError(f"duplicate queue design ID: {design_id}")
                found_ids.add(design_id)
                source_records = row["source_records"]
                if not source_records:
                    raise ValueError(f"{design_id}: queue record has no source provenance")
                family_id = source_records[0].get("policy_family_id") or provisional_family(source_records[0])
                domain = row["domain"]
                family_label = row.get("policy_family") or "Unresolved family: " + source_records[0]["source_text_id"]
                family = families.setdefault(family_id, {"policy_family_id": family_id, "label": family_label, "domains": []})
                if domain not in family["domains"]:
                    family["domains"].append(domain)
                text_ids, instrument_ids = set(), set()
                for s in source_records:
                    text_id = s["source_text_id"]
                    text_ids.add(text_id)
                    ins_id = s["legal_instrument_id"]
                    instrument_ids.add(ins_id)
                    source = sources.setdefault(text_id, {
                        "source_text_id": text_id, "source_family": "existing_l1_extraction",
                        "extraction_version": "existing_l1:" + queue_version,
                        "locator": str(queue_dir / "sources.jsonl") + "#" + text_id,
                        "text_sha256": None, "extraction_records": [],
                    })
                    record = {"source_record_id": s["source_record_id"], "chapter_id": s["chapter_id"],
                              "extraction_source": s["source"],
                              "payload_sha256": s["payload_sha256"],
                              "locator": str(queue_dir / "sources.jsonl") + "#" + s["source_record_id"],
                              "source_url": s.get("source_url") or None}
                    matching = [x for x in source["extraction_records"] if x["source_record_id"] == record["source_record_id"]]
                    if matching and matching[0] != record:
                        raise ValueError(f"{design_id}: conflicting source_record provenance {s['source_record_id']}")
                    if not matching:
                        source["extraction_records"].append(record)
                    instrument = instruments.setdefault(ins_id, {
                        "legal_instrument_id": ins_id, "policy_family_ids": [],
                        "jurisdiction": s.get("jurisdiction") or "unresolved",
                        "legal_citation": None, "source_text_ids": [], "identity_status": "provisional",
                    })
                    if family_id not in instrument["policy_family_ids"]:
                        instrument["policy_family_ids"].append(family_id)
                    if text_id not in instrument["source_text_ids"]:
                        instrument["source_text_ids"].append(text_id)
                reason = row.get("evidence_status") or "Existing L1 extraction; legal identity, assignment and data require verification."
                queue_ref = str(queue_dir / filename) + f":{line_number}#{design_id}"
                raw_strength = row.get("evidence_strength", max(s.get("evidence_strength", 0) for s in source_records))
                priority = {"evidence_strength": min(5, max(0, int(raw_strength))),
                            "free_data_availability": 1 if row.get("data_alias_verdict") in {"public_ok", "mixed"} else 0,
                            "domain_priority": 5 if domain in {"labor_market", "taxation_fiscal", "appropriations_budget"} else 1,
                            "rationale": "Existing quoted-field completeness; data alias only (availability unverified); labor/public finance first. No expected sign, significance or L1 policy-significance score."}
                history = [{"event_id": design_id + ":queued", "at": timestamp,
                            "from_status": None, "to_status": "queued", "actor": "queue_v2_import",
                            "reason": reason, "reopen_condition": None, "evidence_refs": [queue_ref],
                            "outcome_spec_id": None, "run_id": None, "readiness_snapshot": None}]
                designs.append({
                    "design_id": design_id, "policy_family_id": family_id,
                    "legal_instrument_ids": sorted(instrument_ids), "source_text_ids": sorted(text_ids),
                    "design_type": track, "domain": domain, "label": family_label,
                    "screen_version": "existing_l1", "queue_version": queue_version, "priority": priority,
                    "readiness": {"checks": {k: False for k in sorted(COMMON_CHECKS | TRACK_CHECKS[track])},
                                  "evidence_refs": [queue_ref],
                                  "missing": ["Official legal/assignment verification", "Independent coding/reconciliation", "Verified free outcome linkage and design-specific support"]},
                    "status": "queued", "reason": reason, "reopen_condition": None,
                    "active_outcome_spec_id": None, "active_run_id": None, "history": history,
                })
    if design_ids is not None and design_ids - found_ids:
        raise ValueError(f"queue does not contain requested design IDs: {sorted(design_ids - found_ids)}")
    registry = {"registry_version": "1.0.0", "generated_at": timestamp,
                "source_texts": sorted(sources.values(), key=lambda s: s["source_text_id"]),
                "policy_families": sorted(families.values(), key=lambda f: f["policy_family_id"]),
                "legal_instruments": sorted(instruments.values(), key=lambda i: i["legal_instrument_id"]),
                "designs": sorted(designs, key=lambda d: d["design_id"]), "outcome_specs": [], "runs": []}
    errors = validate_registry(registry)
    if errors:
        raise ValueError("Queue conversion failed: " + "; ".join(errors))
    return registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-dir", type=Path, default=Path("data/pipeline/queue_v2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--design-id", action="append", help="Optional whitelist; repeat for each selected ID")
    parser.add_argument("--generated-at", help="UTC timestamp, e.g. 2026-10-06T12:00:00Z")
    args = parser.parse_args()
    try:
        registry = import_queue(args.queue_dir, set(args.design_id) if args.design_id else None, args.generated_at)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "designs": len(registry["designs"]),
                      "source_texts": len(registry["source_texts"]), "legal_instruments": len(registry["legal_instruments"])}))


if __name__ == "__main__":
    main()
