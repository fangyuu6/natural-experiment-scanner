#!/usr/bin/env python3
"""Add explicit unverified target-boundary hints without changing the frozen sample."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/plans/l3-ideas/I13"
SOURCE = OUT / "pilot_sample.jsonl"
PACKET = OUT / "pilot_target_packet.jsonl"
LOCK = OUT / "CODER_PACKET_FREEZE.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def payload():
    rows = []
    for line in SOURCE.read_text().splitlines():
        r = json.loads(line)
        rows.append({k: r[k] for k in ("frame_id", "source_text_id", "title_hint", "source_url_hint")}
                    | {"unverified_target_hint": r["threshold_hint"],
                       "address_value_required": r["stratum"] == "state_mapped__population_hint"})
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def verify():
    lock = json.loads(LOCK.read_text())
    for name, digest in lock["sha256"].items():
        p = Path(__file__).resolve() if name == "prepare_coder_packet.py" else OUT / name
        assert sha(p) == digest, f"Changed coder packet input/artifact: {name}"
    assert PACKET.read_text() == payload(), "Packet reproduction mismatch"
    print("60 target packets verified; no sampling change or coding values")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--check-prereg", action="store_true")
    args = ap.parse_args()
    frame_cmd = [sys.executable, "-B", str(Path(__file__).with_name("build_frame.py")),
                 "--check-prereg" if args.check_prereg else "--verify"]
    subprocess.run(frame_cmd, check=True)
    if args.build:
        assert not LOCK.exists() and not PACKET.exists(), "Refuse frozen packet overwrite"
        PACKET.write_text(payload())
        lock = {"version": "I13-coder-target-v1", "sampling_changed": False,
                "sha256": {p.name: sha(p) for p in (SOURCE, PACKET, OUT / "FRAME_FREEZE.json", Path(__file__).resolve())}}
        LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    else:
        verify()
        if args.check_prereg:
            approval = json.loads((OUT / "HUB_FREEZE.json").read_text())
            for p in (LOCK, Path(__file__).resolve()):
                assert approval["sha256"][p.name] == sha(p), f"Hub packet hash mismatch: {p.name}"
            print("Hub frame/protocol/target-packet gate passed; no analysis executor exists here")
