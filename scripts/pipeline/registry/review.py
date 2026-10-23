#!/usr/bin/env python3
"""Append an evidenced verification review to a registry, validating old history."""
import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from .validate import validate_registry
except ImportError:
    from validate import validate_registry


def apply_reviews(previous, reviews, timestamp=None):
    timestamp = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
    current = copy.deepcopy(previous)
    current["generated_at"] = timestamp
    byid = {d["design_id"]:d for d in current["designs"]}
    seen = set()
    for review in reviews:
        id_ = review["design_id"]
        if id_ in seen:
            raise ValueError(f"Duplicate design review {id_}")
        seen.add(id_)
        design = byid[id_]
        if design["status"] != "verifying" or review["status"] not in {"verifying","stopped","data-ready"}:
            raise ValueError("Review tool handles only verification dispositions")
        if not review["reason"] or not review["evidence_refs"]:
            raise ValueError("Review needs a reason and evidence")
        readiness = review["readiness"]
        event = {"event_id":id_+":reconcile:"+timestamp,
                 "at":timestamp,"from_status":design["status"],"to_status":review["status"],
                 "actor":"l2_blind_reconciliation","reason":review["reason"],
                 "reopen_condition":review.get("reopen_condition"),
                 "evidence_refs":review["evidence_refs"],"outcome_spec_id":None,"run_id":None,
                 "readiness_snapshot":copy.deepcopy(readiness) if review["status"] == "data-ready" else None}
        design["history"].append(event)
        design.update(status=review["status"],reason=review["reason"],
                      reopen_condition=review.get("reopen_condition"),readiness=copy.deepcopy(readiness))
    errors = validate_registry(current,previous=previous)
    if errors:
        raise ValueError("Invalid review: "+"; ".join(errors))
    return current


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous",type=Path,required=True)
    parser.add_argument("--reviews",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--at")
    args=parser.parse_args()
    current=apply_reviews(json.loads(args.previous.read_text()),json.loads(args.reviews.read_text()),args.at)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(current,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"valid":True,"output":str(args.output),"reviewed":len(json.loads(args.reviews.read_text()))}))


if __name__ == "__main__":
    main()
