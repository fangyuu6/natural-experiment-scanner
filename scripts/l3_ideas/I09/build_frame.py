#!/usr/bin/env python3
"""Metadata-only census and probability sample. Never reads/codes study outcomes.

Run once; outputs are immutable by default. --verify checks existing artifact hashes.
No network, SDK, graph construction, inference, or source-readiness classification.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/plans/l3-ideas/I09"
QUEUE = ROOT / "data/pipeline/queue_v2"
SEED = 2026100609
QUOTAS = {"DiD": 50, "RDD": 60, "RCT": 30, "nonqueued": 40}
FIELDS = ["source_text_id", "sampling_lane", "stratum", "domain", "domain_group",
          "authority", "year", "year_bin", "jurisdiction", "cohort",
          "source_record_id", "source", "title", "source_url", "extraction_versions",
          "metadata_conflict", "in_DiD", "in_RDD", "in_RCT"]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def records(path, ledger):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for line in f:
            h.update(line)
            yield json.loads(line)
    ledger.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                   "sha256": h.hexdigest()})


def safe_url(value):
    # Source exports are untrusted; do not propagate credentials or arbitrary queries.
    allowed = {"id", "i", "b", "s", "n", "h", "l", "sn", "pn", "year", "bill",
               "billid", "bill_id", "billnumber", "billtype", "bill_number", "bill_type",
               "number", "type", "doc", "docname", "docnum", "leg", "session",
               "sessionyear", "sessionname", "sa", "sln", "nav", "file", "filename",
               "bs", "ses", "page"}
    try:
        u = urlsplit(str(value or ""))
        if u.scheme not in ("http", "https") or u.username or u.password:
            return ""
        return urlunsplit((u.scheme, u.netloc, u.path,
                           urlencode([(k, v) for k, v in parse_qsl(u.query)
                                      if k.lower() in allowed and len(v) < 160]), ""))
    except ValueError:
        return ""


def group(domain):
    if domain == "labor_market":
        return "labor"
    if domain in {"taxation_fiscal", "appropriations_budget", "finance_monetary"}:
        return "fiscal"
    if domain in {"public_health", "social_insurance"}:
        return "health_insurance"
    return "other"


def year_bin(year):
    if year == "unknown":
        return "unknown"
    y = int(year)
    return "pre2016" if y < 2016 else "2016_2020" if y <= 2020 else "2021plus"


def allocate(counts, target):
    """One per nonempty stratum, then Hamilton allocation proportional to remainder."""
    if len(counts) > target or sum(counts.values()) < target:
        raise ValueError("Pilot quota cannot support all nonempty strata; amend before coding")
    n = {k: 1 for k in counts}
    left = target - len(n)
    capacities = {k: counts[k] - 1 for k in counts}
    total = sum(capacities.values())
    if not left:
        return n
    floors = {k: left * capacities[k] // total for k in counts}
    for k in n:
        n[k] += floors[k]
    extra = left - sum(floors.values())
    order = sorted(counts, key=lambda k: (-(left * capacities[k] % total), k))
    for k in order[:extra]:
        n[k] += 1
    return n


def rank(label, value):
    return hashlib.sha256(f"I09|{SEED}|{label}|{value}".encode()).hexdigest()


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def verify():
    manifest = json.loads((OUT / "FRAME_MANIFEST.json").read_text())
    issues = []
    for item in manifest["inputs"] + manifest["outputs"]:
        path = ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha(path) != item["sha256"]:
            issues.append(item["path"])
    with (OUT / "pilot_sample.csv").open() as f:
        sample = list(csv.DictReader(f))
    if len(sample) != 180 or len({r["source_text_id"] for r in sample}) != 180:
        issues.append("pilot_size_or_duplicates")
    counts = {r["stratum"]: r for r in manifest["strata"]}
    observed = collections.Counter(r["stratum"] for r in sample)
    for key, item in counts.items():
        if observed[key] != item["pilot_n"]:
            issues.append("allocation:" + key)
    for r in sample:
        item = counts[r["stratum"]]
        if abs(float(r["selection_probability"]) - item["pilot_n"] / item["frame_n"]) > 1e-14:
            issues.append("probability:" + r["source_text_id"])
    print(json.dumps({"issues": issues, "pilot_n": len(sample), "association_calls": 0}))
    return bool(issues)


def build():
    if (OUT / "FRAME_MANIFEST.json").exists() or (OUT / "frame.csv").exists():
        raise SystemExit("Existing frame: use --verify; preserve it and create a new version for amendments")
    ledger = []
    membership = {k: set() for k in ("DiD", "RDD", "RCT")}
    raw_counts = {}
    for lane, name in [("DiD", "did_designs"), ("RDD", "rdd_candidates"), ("RCT", "rct_mentions")]:
        total = eligible = 0
        for r in records(QUEUE / (name + ".jsonl"), ledger):
            total += 1
            if lane == "RCT" and r.get("disposition") == "government_gambling_lottery":
                continue
            eligible += 1
            membership[lane].update(str(s["source_text_id"]) for s in r["source_records"])
        raw_counts[lane] = {"raw_records": total, "eligible_records": eligible,
                            "unique_parent_texts": len(membership[lane])}
    units = {}
    versions = collections.Counter()
    conflicts = set()
    source_rows = 0
    for r in records(QUEUE / "sources.jsonl", ledger):
        source_rows += 1
        pid = str(r["source_text_id"])
        versions[pid] += 1
        # Legacy metadata first, then whole-DB, then chapter; input order is frozen.
        year = str(r["year"]) if r.get("year") is not None else "unknown"
        authority = "state" if r.get("state") else "federal" if r.get("jurisdiction") == "US Federal" else "other_unknown"
        meta = (r.get("domain") or "other", authority, year, r.get("jurisdiction") or "unknown")
        if pid in units:
            prior = units[pid]
            if meta != (prior["domain"], prior["authority"], prior["year"], prior["jurisdiction"]):
                conflicts.add(pid)
            continue
        lane = next((k for k in ("RCT", "RDD", "DiD") if pid in membership[k]), "nonqueued")
        dg = group(meta[0])
        units[pid] = dict(zip(FIELDS[:10], [pid, lane, "|".join((lane, authority, dg)),
                                          meta[0], dg, authority, year, year_bin(year),
                                          meta[3], r.get("cohort") or "unknown"]))
        units[pid].update(source_record_id=r["source_record_id"], source=r["source"],
                          title=r.get("title") or "", source_url=safe_url(r.get("source_url")),
                          extraction_versions=0, metadata_conflict=0,
                          **{"in_" + k: int(pid in membership[k]) for k in membership})
    unknown_ids = set.union(*membership.values()) - units.keys()
    if unknown_ids:
        raise ValueError("Queue parent IDs missing from source census")
    census = collections.defaultdict(collections.Counter)
    strata = collections.defaultdict(list)
    for pid in sorted(units):
        r = units[pid]
        r["extraction_versions"] = versions[pid]
        r["metadata_conflict"] = int(pid in conflicts)
        strata[r["stratum"]].append(pid)
        for field in ["sampling_lane", "domain", "authority", "year", "year_bin", "cohort"]:
            census[field][r[field]] += 1
    allocation = {}
    for lane, target in QUOTAS.items():
        allocation.update(allocate({k: len(v) for k, v in strata.items() if k.split("|")[0] == lane}, target))
    selected = []
    stratum_rows = []
    for key in sorted(strata):
        ids = strata[key]
        n = allocation[key]
        for pid in sorted(ids, key=lambda x: (rank("draw", x), x))[:n]:
            selected.append({**units[pid], "selection_probability": n / len(ids),
                             "sampling_weight": len(ids) / n})
        stratum_rows.append({"stratum": key, "frame_n": len(ids), "pilot_n": n,
                             "selection_probability": n / len(ids)})
    selected.sort(key=lambda r: rank("blind_order", r["source_text_id"]))
    for i, r in enumerate(selected, 1):
        r["blind_id"] = f"I09-P{i:03d}"
    write_csv(OUT / "frame.csv", (units[k] for k in sorted(units)), FIELDS)
    write_csv(OUT / "pilot_sample.csv", selected, FIELDS + ["selection_probability", "sampling_weight", "blind_id"])
    write_csv(OUT / "counts_by_stratum.csv", stratum_rows, list(stratum_rows[0]))
    write_csv(OUT / "coverage_counts.csv",
              ({"dimension": dim, "value": value, "frame_n": n}
               for dim in sorted(census) for value, n in sorted(census[dim].items())),
              ["dimension", "value", "frame_n"])
    # Answer-free identity lookup only. Cadence/manual must be frozen before launch.
    packet_fields = ["blind_id", "source_text_id", "title", "source_url", "jurisdiction", "year"]
    write_csv(OUT / "pilot_lookup.csv", ({k: r[k] for k in packet_fields} for r in selected), packet_fields)
    artifacts = ["frame.csv", "pilot_sample.csv", "counts_by_stratum.csv", "coverage_counts.csv", "pilot_lookup.csv"]
    ledger.append({"path": str(Path(__file__).relative_to(ROOT)), "bytes": Path(__file__).stat().st_size,
                   "sha256": sha(Path(__file__))})
    manifest = {"status": "FRAME_LOCKED_PROTOCOL_PENDING_HUB_FREEZE", "seed": SEED,
                "draw": "SHA256 deterministic pseudorandom rank; SRS without replacement per stratum",
                "unit": "unique parent source_text_id, provisional instrument; not independent designs",
                "frame_n": len(units), "source_records": source_rows, "pilot_n": len(selected),
                "lane_quotas": QUOTAS, "raw_queue_counts": raw_counts,
                "counts": {k: dict(v) for k, v in census.items()}, "strata": stratum_rows,
                "metadata_conflict_parent_ids": len(conflicts), "inputs": ledger,
                "outputs": [{"path": str((OUT / name).relative_to(ROOT)),
                             "bytes": (OUT / name).stat().st_size, "sha256": sha(OUT / name)} for name in artifacts],
                "outcomes_coded": 0, "association_calls": 0, "downloads": []}
    (OUT / "FRAME_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ["frame_n", "source_records", "pilot_n", "raw_queue_counts", "counts"]}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verify", action="store_true")
    args = p.parse_args()
    raise SystemExit(verify() if args.verify else build())
