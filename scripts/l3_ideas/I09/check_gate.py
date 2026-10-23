#!/usr/bin/env python3
"""Fail closed until hub supplies the hash-bound freeze named in PROTOCOL.md.

Does not create approval, code values, or execute any association analysis.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/plans/l3-ideas/I09"
REQUIRED = ["docs/plans/l3-ideas/I09/" + s for s in [
    "PROTOCOL.md", "CODER_SETUP.md", "CODING_SCHEMA.json", "FRAME_MANIFEST.json",
    "AUDIT_MANIFEST.json", "pilot_audit_sample.csv"]] + [
    "scripts/l3_ideas/I09/" + s for s in ["build_frame.py", "prepare_audit.py", "check_gate.py"]]


def check():
    path = OUT / "HUB_FREEZE.json"
    if not path.exists():
        print(json.dumps({"status": "BLOCKED_PENDING_HUB_FREEZE", "coding_allowed": False,
                          "association_allowed": False, "issues": ["HUB_FREEZE.json missing"]}))
        return 2
    freeze = json.loads(path.read_text())
    issues = []
    if freeze.get("status") != "FROZEN" or freeze.get("approved_by") != "hub" or not freeze.get("frozen_at_utc"):
        issues.append("Hub freeze status/identity/timestamp missing")
    hashes = freeze.get("sha256", {})
    for relative in REQUIRED:
        file = ROOT / relative
        if not file.is_file() or hashes.get(relative) != hashlib.sha256(file.read_bytes()).hexdigest():
            issues.append("unbound_or_changed:" + relative)
    # Bind all census/sample inputs and outputs through the frozen frame manifest.
    for item in json.loads((OUT / "FRAME_MANIFEST.json").read_text())["outputs"]:
        file = ROOT / item["path"]
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]:
            issues.append("frame_artifact:" + item["path"])
    for item in json.loads((OUT / "AUDIT_MANIFEST.json").read_text())["files"]:
        file = ROOT / item["path"]
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]:
            issues.append("audit_artifact:" + item["path"])
    print(json.dumps({"status": "BLOCKED" if issues else "PILOT_CODING_ALLOWED",
                      "coding_allowed": not issues, "association_allowed": False,
                      "issues": issues}))
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(check())
