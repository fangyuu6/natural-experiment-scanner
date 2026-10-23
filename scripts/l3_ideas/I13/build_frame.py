#!/usr/bin/env python3
"""Descriptive I13 frame and probability samples only; no coding or estimator."""
import argparse
import csv
import hashlib
import json
import math
import random
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/plans/l3-ideas/I13"
CODE = ROOT / "scripts/l3_ideas/I13"
INPUT = ROOT / "data/pipeline/queue_v2/rdd_candidates.jsonl"
SEED = 2026100613
PILOT_N = 60
MAIN_N = 600
GEO_RE = re.compile(r"population|inhabitant|resident", re.I)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def source_group(s):
    jurisdiction = s.get("jurisdiction", "")
    if s.get("state") or jurisdiction.endswith(" State"):
        return "state_mapped"
    if jurisdiction == "US Federal":
        if "-plaw-" in s["source_text_id"] or "/PLAW-" in s.get("source_url", ""):
            return "federal_public_law"
        return "federal_other"
    return "unmapped_other"


def assemble():
    """Collapse identical parent/variable/cutoff/direction/quote signatures only."""
    grouped = defaultdict(list)
    with INPUT.open() as f:
        for line in f:
            r = json.loads(line)
            assert len(r["source_records"]) == 1, "New source layout needs new frame version"
            s = r["source_records"][0]
            t = r["threshold"]
            key = (s["source_text_id"], *(norm(t.get(k)) for k in
                    ("running_variable", "cutoff", "direction", "quote")))
            grouped[key].append(r)
    frame = []
    for key, records in sorted(grouped.items()):
        # Deterministic metadata representative; no validity/data score selection.
        records.sort(key=lambda r: (
            r["source_records"][0].get("source") != "legacy_csv", r["design_id"]))
        r = records[0]
        s, t = r["source_records"][0], r["threshold"]
        group = source_group(s)
        hint = "population_hint" if GEO_RE.search(str(t.get("running_variable", ""))) else "other_hint"
        fid = "i13-" + hashlib.sha256(dumps(key).encode()).hexdigest()[:24]
        frame.append({
            "frame_id": fid, "stratum": group + "__" + hint,
            "source_group": group, "variable_hint_group": hint,
            "source_text_id": s["source_text_id"], "title_hint": s.get("title", ""),
            "source_url_hint": s.get("source_url", ""),
            "jurisdiction_hint": s.get("jurisdiction", ""), "state_hint": s.get("state", ""),
            "year_hint": s.get("year"), "domain_hint": r.get("domain", "other"),
            "family_hint": r.get("policy_family", ""),
            "threshold_hint": {k: t.get(k, "") for k in
                               ("running_variable", "cutoff", "direction", "quote")},
            "queue_design_ids": sorted(x["design_id"] for x in records),
            "extraction_records": len(records),
            "source_record_ids": sorted({x["source_records"][0]["source_record_id"] for x in records}),
            "metadata_years": sorted({str(x["source_records"][0].get("year")) for x in records}),
            "metadata_domains": sorted({x.get("domain", "other") for x in records}),
        })
    assert len(frame) == len({r["frame_id"] for r in frame})
    return frame


def allocate(counts, total, minimum, overrides=None):
    """Capped minimum, then proportional largest remainders; no response inputs."""
    overrides = overrides or {}
    allocation = {h: min(overrides.get(h, minimum), n) for h, n in counts.items()}
    if sum(allocation.values()) > total or total > sum(counts.values()):
        raise ValueError("Sample size incompatible with strata")
    left = total - sum(allocation.values())
    capacity = {h: counts[h] - allocation[h] for h in counts}
    if left:
        denom = sum(capacity.values())
        quotas = {h: left * capacity[h] / denom for h in counts}
        for h in counts:
            allocation[h] += math.floor(quotas[h])
        remaining = total - sum(allocation.values())
        order = sorted(counts, key=lambda h: (-(quotas[h] - math.floor(quotas[h])), h))
        for h in order[:remaining]:
            allocation[h] += 1
    assert sum(allocation.values()) == total
    return allocation


def samples(frame):
    by_h = defaultdict(list)
    for r in frame:
        by_h[r["stratum"]].append(r)
    counts = {h: len(rr) for h, rr in sorted(by_h.items())}
    main_n = allocate(counts, MAIN_N, 10, {
        "state_mapped__population_hint": 100, "state_mapped__other_hint": 100})
    pilot_n = allocate(main_n, PILOT_N, 4, {
        "state_mapped__population_hint": 12, "state_mapped__other_hint": 12})
    main, pilot = [], []
    for h, rr in sorted(by_h.items()):
        # Stable seed per stratum, independent of dict traversal and upstream order.
        rng = random.Random(int(hashlib.sha256(f"{SEED}|{h}".encode()).hexdigest(), 16))
        selected = rng.sample(sorted(rr, key=lambda r: r["frame_id"]), main_n[h])
        for position, r in enumerate(selected):
            row = {**r, "stratum_frame_n": counts[h], "stratum_main_n": main_n[h],
                   "stratum_pilot_n": pilot_n[h], "main_pi": main_n[h] / counts[h],
                   "pilot_pi": pilot_n[h] / counts[h], "pilot_selected": position < pilot_n[h]}
            main.append(row)
            if row["pilot_selected"]:
                pilot.append(row)
    return counts, main_n, pilot_n, sorted(main, key=lambda r: r["frame_id"]), sorted(pilot, key=lambda r: r["frame_id"])


def generated():
    frame = assemble()
    counts, main_n, pilot_n, main, pilot = samples(frame)
    artifacts = {}
    def jsonl(name, rows):
        artifacts[name] = "".join(dumps(r) + "\n" for r in rows).encode()
    jsonl("frame.jsonl", frame)
    jsonl("confirmatory_sample.jsonl", main)
    jsonl("pilot_sample.jsonl", pilot)
    # Independent coders receive identical, answer-free lookup hints; no model judgments.
    packets = [{k: r[k] for k in ("frame_id", "source_text_id", "title_hint", "source_url_hint")}
               | {"target_quote_hint": r["threshold_hint"]["quote"]} for r in pilot]
    jsonl("pilot_lookup_packet.jsonl", packets)
    for dimension in ("stratum", "year_hint", "domain_hint", "source_group", "jurisdiction_hint"):
        counts_dim = Counter(str(r[dimension]) if r[dimension] is not None else "unknown" for r in frame)
        import io
        buf = io.StringIO(newline="")
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow([dimension, "frame_n", "confirmatory_n", "pilot_n"])
        mc = Counter(str(r[dimension]) if r[dimension] is not None else "unknown" for r in main)
        pc = Counter(str(r[dimension]) if r[dimension] is not None else "unknown" for r in pilot)
        for label, n in sorted(counts_dim.items()):
            writer.writerow([label, n, mc[label], pc[label]])
        artifacts[f"counts_by_{dimension}.csv"] = buf.getvalue().encode()
    return artifacts, frame, counts, main_n, pilot_n


def verify():
    manifest = json.loads((OUT / "FRAME_FREEZE.json").read_text())
    for entry in manifest["inputs"] + manifest["artifacts"] + manifest["builder"]:
        p = ROOT / entry["path"]
        assert p.stat().st_size == entry["bytes"] and sha(p) == entry["sha256"], f"Changed: {p}"
    artifacts, frame, counts, main_n, pilot_n = generated()
    for name, payload in artifacts.items():
        assert (OUT / name).read_bytes() == payload, f"Reproduction failed: {name}"
    assert counts == manifest["stratum_counts"]
    assert len(frame) == manifest["frame_n"]
    assert main_n == manifest["main_allocation"] and pilot_n == manifest["pilot_allocation"]
    print(dumps({"frame_n": len(frame), "pilot_n": PILOT_N, "main_n": MAIN_N,
                 "reproducible": True, "outcomes_read_or_coded": 0, "associations_computed": 0}))


def prereg_gate():
    verify()
    approval = OUT / "HUB_FREEZE.json"
    if not approval.exists():
        raise SystemExit("BLOCKED: hub protocol freeze absent; coding/analysis not authorized")
    a = json.loads(approval.read_text())
    assert a["status"] == "frozen" and a["approved_by"] == "hub" and a["approved_at_utc"]
    for name in ("PROTOCOL.md", "PILOT_PLAN.md", "CODING_SCHEMA.json", "FRAME_FREEZE.json"):
        assert a["sha256"][name] == sha(OUT / name), f"Hub hash mismatch: {name}"
    assert a["sha256"]["build_frame.py"] == sha(CODE / "build_frame.py")
    print("Hub hash gate passed. This tool has no coding or association execution path.")


def build():
    if (OUT / "FRAME_FREEZE.json").exists():
        raise SystemExit("Existing frame frozen; use --verify. Amend in a new version, never overwrite.")
    artifacts, frame, counts, main_n, pilot_n = generated()
    for name, payload in artifacts.items():
        assert not (OUT / name).exists(), f"Refuse overwrite: {name}"
        (OUT / name).write_bytes(payload)
    def entry(path):
        return {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha(path)}
    manifest = {
        "version": "I13-frame-v1", "frame_frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_status": "draft_pending_hub", "seed": SEED,
        "population_hint_regex": GEO_RE.pattern, "raw_queue_n": sum(r["extraction_records"] for r in frame),
        "frame_n": len(frame), "parent_text_n": len({r["source_text_id"] for r in frame}),
        "main_n": MAIN_N, "pilot_n": PILOT_N, "stratum_counts": counts,
        "main_allocation": main_n, "pilot_allocation": pilot_n,
        "inputs": [entry(INPUT)], "builder": [entry(Path(__file__).resolve())],
        "artifacts": [entry(OUT / name) for name in sorted(artifacts)],
        "downloads": [], "coding_started": False, "associations_computed": 0,
    }
    (OUT / "FRAME_FREEZE.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(dumps({k: manifest[k] for k in ("raw_queue_n", "frame_n", "parent_text_n", "main_n", "pilot_n", "stratum_counts", "main_allocation", "pilot_allocation")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--check-prereg", action="store_true")
    args = parser.parse_args()
    if args.build:
        build()
    elif args.verify:
        verify()
    else:
        prereg_gate()
