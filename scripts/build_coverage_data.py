#!/usr/bin/env python3
"""Build deploy_output/coverage_data.json: how well each state is covered by the policy DB.

For every state / DC: the number of State Legislation rows per year 2016-2026 and in total,
using the SAME row filters as scripts/build_adoption_data.py (dedupe on policy_id with the
first row winning; source_family == 'State Legislation'; jurisdiction maps to a state or DC;
non-blank policy_family; parseable year). Rows outside the window (year < 2016 or > 2026)
are counted separately (`outside_window`) and are NOT part of `total`.

A state is flagged `not_observed` when the database cannot tell "never adopts" from
"not covered":
  - total State Legislation rows in the window < MIN_TOTAL (300), or
  - zero rows in the window's first year (2016; typically biennial legislatures), so the
    state's "first appearance" would be an artefact of the data window, not an adoption.
`not_observed_reason` holds the reason(s); `reason_codes` holds machine-readable tags
("thin" / "no_first_year").

Stdlib only; streams the 1.5GB policy DB. Helpers are imported from build_map_data.py
(that file is not modified). Usage: python3 scripts/build_coverage_data.py
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_map_data import STATES, classify, find, DB_NAME  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "deploy_output" / "coverage_data.json"
ADOPTION = REPO / "deploy_output" / "adoption_data.json"

SOURCE_FAMILY = "State Legislation"
Y0, Y1 = 2016, 2026
MIN_TOTAL = 300


def parse_year(s):
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def main():
    csv.field_size_limit(sys.maxsize)
    db_path = find(DB_NAME)
    print("db: %s" % db_path)
    if ADOPTION.exists():  # keep the window aligned with the adoption data
        yrs = json.load(open(ADOPTION, encoding="utf-8")).get("years")
        if yrs and [Y0, Y1] != [int(yrs[0]), int(yrs[1])]:
            print("WARNING: adoption_data.json years %s differ from coverage window [%d, %d]" % (yrs, Y0, Y1))

    by_year = defaultdict(Counter)   # state -> year -> rows (in window)
    outside = Counter()              # state -> rows outside the window
    seen = set()
    raw = dupes = sl_rows = 0
    drop = Counter()
    with open(db_path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        need = {"policy_id", "jurisdiction", "year", "source_family", "policy_family"}
        missing = need - set(rd.fieldnames or [])
        if missing:
            sys.exit("policy db missing columns: %s" % sorted(missing))
        for row in rd:
            raw += 1
            pid = row["policy_id"]
            if pid in seen:
                dupes += 1
                continue
            seen.add(pid)
            if row["source_family"] != SOURCE_FAMILY:
                continue
            sl_rows += 1
            state, _ = classify(row["jurisdiction"])
            if state not in STATES:
                drop["not a state/DC jurisdiction"] += 1
                continue
            if not row["policy_family"].strip():
                drop["blank policy_family"] += 1
                continue
            year = parse_year(row["year"])
            if year is None:
                drop["unparseable/blank year"] += 1
                continue
            if Y0 <= year <= Y1:
                by_year[state][year] += 1
            else:
                outside[state] += 1

    print("raw rows: %d, duplicates skipped: %d, unique: %d" % (raw, dupes, len(seen)))
    print("State Legislation unique rows: %d; usable (state+family+year): %d in window, %d outside window"
          % (sl_rows, sum(sum(c.values()) for c in by_year.values()), sum(outside.values())))
    for k, v in drop.most_common():
        print("  dropped %-32s %d" % (k, v))

    years = list(range(Y0, Y1 + 1))
    states = {}
    for code in sorted(STATES):
        c = by_year.get(code, Counter())
        total = sum(c.values())
        reasons, codes = [], []
        if total < MIN_TOTAL:
            reasons.append("thin coverage: %d state-law rows in %d-%d (< %d)" % (total, Y0, Y1, MIN_TOTAL))
            codes.append("thin")
        if c.get(Y0, 0) == 0:
            reasons.append("no %d rows (first year of the window)" % Y0)
            codes.append("no_first_year")
        states[code] = {
            "by_year": [c.get(y, 0) for y in years],
            "total": total,
            "outside_window": outside.get(code, 0),
            "not_observed": bool(codes),
            "not_observed_reason": "; ".join(reasons),
            "reason_codes": codes,
        }

    doc = {
        "generated": date.today().isoformat(),
        "years": [Y0, Y1],
        "min_total": MIN_TOTAL,
        "rule": "not_observed = total State Legislation rows in window < %d OR zero rows in %d" % (MIN_TOTAL, Y0),
        "states": states,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
    print("\nwrote %s (%d bytes)" % (OUT, OUT.stat().st_size))

    no = [c for c in sorted(states) if states[c]["not_observed"]]
    print("\nnot_observed states (%d of %d):" % (len(no), len(states)))
    for c in sorted(no, key=lambda c: states[c]["total"]):
        print("  %s total=%5d %s" % (c, states[c]["total"], states[c]["not_observed_reason"]))
    print("observed states: %d" % (len(states) - len(no)))
    print("zero-2016 states: %s" % [c for c in sorted(states) if states[c]["by_year"][0] == 0])
    print("total rows range: min %s, max %s" % (
        min((states[c]["total"], c) for c in states), max((states[c]["total"], c) for c in states)))


if __name__ == "__main__":
    main()
