#!/usr/bin/env python3
"""Build deploy_output/map_data.json: per-state policy counts for the site map.

Stdlib only; streams the 1.5GB policy DB with the csv module.

Inputs (repo-relative ./data/output/... preferred, else absolute /data/output/...):
  policy_db_20260415_483k.csv   policy_id, jurisdiction, year, status, policy_topic, ...
  derived/derived_views.csv     policy_id, strict_enacted, verify_level, viable_strict, ...

Both files contain duplicate policy_id rows; the first row per policy_id is kept.
Usage: python3 scripts/build_map_data.py
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "deploy_output" / "map_data.json"

DB_NAME = "policy_db_20260415_483k.csv"
DERIVED_NAME = "derived/derived_views.csv"

METRICS = ["total", "enacted", "strict_enacted", "viable_strict", "id_verified"]
YEAR_MIN, YEAR_MAX = 1990, 2026
TOP_TOPICS = 6

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
NAME_TO_CODE = {v.lower(): k for k, v in STATES.items()}
NAME_TO_CODE["washington dc"] = "DC"
NAME_TO_CODE["washington d.c."] = "DC"

FEDERAL = "FEDERAL"
UNASSIGNED = "UNASSIGNED"


def find(rel):
    for base in (REPO / "data" / "output", Path("/data/output")):
        p = base / rel
        if p.exists():
            return p
    sys.exit("input not found: %s (looked in %s/data/output and /data/output)" % (rel, REPO))


def classify(jur):
    """Return (bucket, reason). bucket is a state code, FEDERAL or UNASSIGNED."""
    j = (jur or "").strip()
    if not j:
        return UNASSIGNED, "blank"
    if j == "US Federal":
        return FEDERAL, None
    name = j[:-len(" State")].strip() if j.endswith(" State") else j
    if name.upper() in STATES:
        return name.upper(), None
    code = NAME_TO_CODE.get(name.lower())
    if code:
        return code, None
    return UNASSIGNED, j


def load_derived(path):
    """policy_id -> (strict_enacted, viable_strict, id_verified) as 0/1 ints; first row wins."""
    d = {}
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        need = {"policy_id", "strict_enacted", "verify_level", "viable_strict"}
        missing = need - set(r.fieldnames or [])
        if missing:
            sys.exit("derived_views missing columns: %s (has %s)" % (sorted(missing), r.fieldnames))
        for row in r:
            pid = row["policy_id"]
            if pid in d:
                continue
            d[pid] = (
                1 if row["strict_enacted"].strip() == "1" else 0,
                1 if row["viable_strict"].strip() == "1" else 0,
                1 if row["verify_level"].strip() == "id_verified" else 0,
            )
    return d


def new_bucket():
    return {"counts": Counter(), "topics": Counter(), "years": Counter()}


def main():
    csv.field_size_limit(sys.maxsize)
    db_path = find(DB_NAME)
    derived_path = find(DERIVED_NAME)
    print("db:      %s" % db_path)
    print("derived: %s" % derived_path)

    derived = load_derived(derived_path)
    print("derived unique policy_ids: %d" % len(derived))

    buckets = defaultdict(new_bucket)
    seen = set()
    raw_rows = dupes = no_derived = 0
    glob = Counter()  # independent global tallies for the sanity check
    unmapped = Counter()

    with open(db_path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        need = {"policy_id", "jurisdiction", "year", "status", "policy_topic"}
        missing = need - set(r.fieldnames or [])
        if missing:
            sys.exit("policy db missing columns: %s" % sorted(missing))
        for row in r:
            raw_rows += 1
            pid = row["policy_id"]
            if pid in seen:
                dupes += 1
                continue
            seen.add(pid)

            key, reason = classify(row["jurisdiction"])
            if key == UNASSIGNED and reason and reason != "blank":
                unmapped[reason] += 1
            b = buckets[key]
            c = b["counts"]
            c["total"] += 1
            if row["status"].strip() == "enacted":
                c["enacted"] += 1
                glob["enacted"] += 1
            dv = derived.get(pid)
            if dv is None:
                no_derived += 1
            else:
                c["strict_enacted"] += dv[0]
                c["viable_strict"] += dv[1]
                c["id_verified"] += dv[2]
                glob["strict_enacted"] += dv[0]
                glob["viable_strict"] += dv[1]
                glob["id_verified"] += dv[2]

            if key != UNASSIGNED:
                topic = row["policy_topic"].strip()
                if topic:
                    b["topics"][topic] += 1
                try:
                    y = int(float(row["year"]))
                except (ValueError, TypeError):
                    y = None
                if y is not None and YEAR_MIN <= y <= YEAR_MAX:
                    b["years"][y] += 1

    total = len(seen)
    print("raw rows: %d, duplicate rows skipped: %d, unique: %d, no derived match: %d"
          % (raw_rows, dupes, total, no_derived))

    def nums(key):
        c = buckets[key]["counts"] if key in buckets else Counter()
        return {m: int(c.get(m, 0)) for m in METRICS}

    def full(key, code, name):
        b = buckets.get(key)
        # stable tie-break: count desc, then name asc
        topics = sorted(b["topics"].items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_TOPICS] if b else []
        years = sorted(b["years"].items()) if b else []
        out = {}
        if code is not None:
            out["code"] = code
        out["name"] = name
        out.update(nums(key))
        out["topics"] = [[t, n] for t, n in topics]
        out["by_year"] = [[y, n] for y, n in years if n > 0]
        return out

    states = [full(code, code, name) for code, name in sorted(STATES.items(), key=lambda kv: kv[1])]
    federal = full(FEDERAL, None, "US Federal")
    unassigned = nums(UNASSIGNED)

    doc = {
        "generated": date.today().isoformat(),
        "total": total,
        "metrics": METRICS,
        "states": states,
        "federal": federal,
        "unassigned": unassigned,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
    size = OUT.stat().st_size
    print("wrote %s (%d bytes, %.1f KB)" % (OUT, size, size / 1024))

    # ---- sanity checks ----
    print("\nSanity: sum(states)+federal+unassigned vs expected")
    print("%-16s %10s %10s %10s %10s  %s" % ("metric", "states", "federal", "unassigned", "sum", "ok"))
    all_ok = True
    for m in METRICS:
        s = sum(st[m] for st in states)
        fed = federal[m]
        un = unassigned[m]
        tot = s + fed + un
        expected = total if m == "total" else glob[m]
        ok = tot == expected
        all_ok &= ok
        print("%-16s %10d %10d %10d %10d  %s" % (m, s, fed, un, tot, "OK" if ok else "MISMATCH (expected %d)" % expected))
    print("ALL OK" if all_ok else "SANITY FAILED")

    print("\nTop 8 states by total:")
    for st in sorted(states, key=lambda s: -s["total"])[:8]:
        print("  %s %-20s total=%6d enacted=%5d strict=%4d viable=%3d id_verified=%6d"
              % (st["code"], st["name"], st["total"], st["enacted"], st["strict_enacted"],
                 st["viable_strict"], st["id_verified"]))
    print("  US Federal: total=%d" % federal["total"])
    print("  unassigned: total=%d" % unassigned["total"])

    if unmapped:
        print("\nUnmapped non-blank jurisdiction strings -> unassigned:")
        for k, v in unmapped.most_common():
            print("  %r: %d" % (k, v))
    else:
        print("\nNo unmapped non-blank jurisdiction strings.")
    print("Zero-total states: %s" % [s["code"] for s in states if s["total"] == 0])
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
