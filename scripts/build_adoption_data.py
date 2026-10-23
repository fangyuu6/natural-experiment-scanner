#!/usr/bin/env python3
"""Build deploy_output/adoption_data.json: staggered-adoption data per policy_family.

For a chosen policy_family the site map shows which states adopted it, in what year
(first_year = earliest year a State Legislation row of that family appears in that state),
and which states never did (the controls in a DiD / natural-experiment design).

Stdlib only; streams the 1.5GB policy DB with the csv module (never loads it whole).
Jurisdiction -> postal-code mapping is reused from scripts/build_map_data.py (imported,
that file is not modified).

Row filter: source_family == 'State Legislation', jurisdiction maps to one of the 50
states or DC, non-blank policy_family, parseable year. Dedupe on policy_id (first row wins).

Per family the basis is 'enacted' (status == 'enacted' rows) when that basis passes the
keep thresholds, otherwise 'all' (every row passing the filter); a family that passes on
neither basis is dropped. Keep thresholds: >= MIN_STATES adopting states, >= MIN_YEARS
distinct first-adoption years, >= MIN_POLICIES policies. Junk families (see is_junk) are
excluded. Top TOP_N by number of adopting states are kept.

design / control_quality counts are over the same rows as n_policies (the chosen basis).
did_control_quality is not a CSV column; it lives inside the detail_json blob and is
pulled out with a regex (only for rows that already passed the filter).

Usage: python3 scripts/build_adoption_data.py
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True  # importing build_map_data must not leave __pycache__ behind
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_map_data import STATES, classify, find, DB_NAME  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "deploy_output" / "adoption_data.json"

SOURCE_FAMILY = "State Legislation"
MIN_STATES, MIN_YEARS, MIN_POLICIES = 8, 4, 40
TOP_N = 80
N_TITLES, MAX_TITLE = 3, 90
MAX_FIRST_TITLE = 100  # first_title length cap (chars, ellipsis included)
MIN_TITLE = 8  # skip stub titles like "HB 12"
CQ_KEYS = ("interstate", "within_state", "border_county_possible")

CQ_RE = re.compile(r'"did_control_quality":\s*"([^"]*)"')

# ---- junk policy_family detection -------------------------------------------------------
# Labels that mean "the classifier had nothing to say" or that are not a policy adoption.
JUNK_EXACT = {"", "other", "unknown", "none", "n/a", "na", "null", "misc", "miscellaneous",
              "general", "unclassified", "uncategorized", "unspecified", "various",
              "legislative_procedure"}
# 'unique_*' = one-off fallback labels; the rest are ceremonial / symbolic / unreadable-text
# buckets and recurring annual appropriations (every state "adopts" them every year, so a
# first-adoption year carries no natural-experiment information).
JUNK_PATTERNS = [
    (re.compile(r"^unique_"), "unique_* one-off fallback label"),
    (re.compile(r"ceremonial|commemorat|memorial|congratulat|commendation|symbolic|"
                r"(^|_)honor(_|$)|recognition|awareness_resolution|(^|_)resolution(_|$)"),
     "ceremonial / symbolic resolution"),
    (re.compile(r"no_text|unreadable|corrupted|no_legislative_text"), "no readable text"),
    (re.compile(r"appropriation|(^|_)budget(_|$)"), "recurring annual appropriation/budget"),
]


def junk_reason(fam):
    f = fam.strip().lower()
    if f in JUNK_EXACT:
        return "generic/unknown label" if f != "legislative_procedure" else "intra-legislature procedure"
    for rx, why in JUNK_PATTERNS:
        if rx.search(f):
            return why
    return None


def parse_year(s):
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def norm_title(t):
    return " ".join((t or "").split())


def pid_key(pid):
    """Sort key for policy_id ties: numeric ids numerically, everything else as text, after them."""
    return (0, int(pid), "") if pid.isdigit() else (1, 0, pid)


def clip_title(t, n=MAX_FIRST_TITLE):
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def evaluate(rows):
    """rows: list of dict(state, year, topic, title, did, rd, cq). Returns stats or None if thresholds fail."""
    first = {}
    count = Counter()
    for r in rows:
        s = r["state"]
        count[s] += 1
        if s not in first or r["year"] < first[s]:
            first[s] = r["year"]
    if len(first) < MIN_STATES or len(set(first.values())) < MIN_YEARS or len(rows) < MIN_POLICIES:
        return None
    return first, count


def main():
    csv.field_size_limit(sys.maxsize)
    db_path = find(DB_NAME)
    print("db: %s" % db_path)

    fam_rows = defaultdict(list)  # family -> [row dict] (passes all row filters; deduped)
    seen = set()
    raw = dupes = sl_rows = 0
    drop = Counter()
    with open(db_path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        need = {"policy_id", "title", "jurisdiction", "year", "status", "source_family",
                "policy_topic", "policy_family", "rd", "did", "detail_json"}
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
            fam = row["policy_family"].strip()
            if not fam:
                drop["blank policy_family"] += 1
                continue
            year = parse_year(row["year"])
            if year is None:
                drop["unparseable/blank year"] += 1
                continue
            m = CQ_RE.search(row["detail_json"])
            fam_rows[fam].append({
                "pid": pid,
                "state": state, "year": year, "status": row["status"].strip(),
                "topic": row["policy_topic"].strip(), "title": norm_title(row["title"]),
                "did": row["did"].strip() == "1", "rd": row["rd"].strip() == "1",
                "cq": m.group(1) if m else "",
            })

    kept_rows = sum(len(v) for v in fam_rows.values())
    print("raw rows: %d, duplicates skipped: %d, unique: %d" % (raw, dupes, len(seen)))
    print("State Legislation unique rows: %d; usable (state+family+year): %d in %d families"
          % (sl_rows, kept_rows, len(fam_rows)))
    for k, v in drop.most_common():
        print("  dropped %-32s %d" % (k, v))
    all_years = [r["year"] for v in fam_rows.values() for r in v]
    print("year range among usable rows: %d-%d; years <1900: %d"
          % (min(all_years), max(all_years), sum(1 for y in all_years if y < 1900)))

    candidates, excluded_junk, failed = [], [], 0
    for fam, rows in fam_rows.items():
        basis, res, used = "enacted", None, None
        used = [r for r in rows if r["status"] == "enacted"]
        res = evaluate(used)
        if res is None:
            basis, used = "all", rows
            res = evaluate(used)
        if res is None:
            failed += 1
            continue
        why = junk_reason(fam)
        first, count = res
        if why:
            excluded_junk.append((fam, why, len(first), len(used), basis))
            continue
        candidates.append((fam, basis, used, first, count))

    candidates.sort(key=lambda c: (-len(c[3]), -len(c[2]), c[0]))
    candidates = candidates[:TOP_N]

    families = []
    for fam, basis, used, first, count in candidates:
        topic = Counter(r["topic"] for r in used if r["topic"])
        topic_name = sorted(topic.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if topic else ""
        # first_title / first_id: the earliest-year policy of this family in this state
        # (ties on year -> lowest policy_id; numeric ids compare numerically), so each
        # "event" can be audited against the bill that created it.
        earliest = {}
        for r in used:
            k = pid_key(r["pid"])
            cur = earliest.get(r["state"])
            if cur is None or (r["year"], k) < (cur[0], cur[1]):
                earliest[r["state"]] = (r["year"], k, r)
        states = [{"code": s, "first_year": first[s], "n": count[s],
                   "first_title": clip_title(earliest[s][2]["title"]), "first_id": earliest[s][2]["pid"]}
                  for s in sorted(first, key=lambda s: (first[s], s))]
        never = sorted(set(STATES) - set(first))

        # sample titles: earliest adopters first, one per distinct state, first file-order
        # row of that state in its first year with a usable title
        titles = []
        for st in states:
            for r in used:
                if (r["state"] == st["code"] and r["year"] == st["first_year"]
                        and MIN_TITLE <= len(r["title"]) <= MAX_TITLE):
                    titles.append(r["title"])
                    break
            if len(titles) == N_TITLES:
                break

        cq = Counter(r["cq"] for r in used)
        families.append({
            "family": fam, "topic": topic_name, "basis": basis,
            "n_policies": len(used), "n_states": len(first),
            "states": states, "never": never, "sample_titles": titles,
            "design": {"did": sum(1 for r in used if r["did"]), "rd": sum(1 for r in used if r["rd"])},
            "control_quality": {k: cq.get(k, 0) for k in CQ_KEYS},
        })
        unexpected = set(cq) - set(CQ_KEYS) - {""}
        if unexpected:
            print("WARNING: unexpected did_control_quality values in %s: %s" % (fam, unexpected))

    yrs = [r["year"] for c in candidates for r in c[2]]
    doc = {
        "generated": date.today().isoformat(),
        "years": [min(yrs), max(yrs)] if yrs else [None, None],
        "families": families,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
    size = OUT.stat().st_size
    print("\nwrote %s (%d bytes, %.1f KB)" % (OUT, size, size / 1024))
    if size > 1.5 * 1024 * 1024:
        sys.exit("output exceeds 1.5MB")

    # ---- sanity summary ----
    n_en = sum(1 for x in families if x["basis"] == "enacted")
    print("\nfamilies kept: %d (basis enacted: %d, all: %d); failed thresholds: %d; "
          "passed but junk-excluded: %d"
          % (len(families), n_en, len(families) - n_en, failed, len(excluded_junk)))
    print("\nTop 10 by n_states:")
    for x in families[:10]:
        ys = [s["first_year"] for s in x["states"]]
        print("  %-28s states=%2d policies=%4d basis=%-7s first_years %d-%d (%d distinct) topic=%s"
              % (x["family"], x["n_states"], x["n_policies"], x["basis"], min(ys), max(ys),
                 len(set(ys)), x["topic"]))
    print("\nExcluded as junk (passed thresholds):")
    for fam, why, ns, n, basis in sorted(excluded_junk, key=lambda t: (-t[2], t[0])):
        print("  %-34s states=%2d policies=%4d basis=%-7s  [%s]" % (fam, ns, n, basis, why))
    print("\nSurprises check:")
    print("  empty never lists: %s" % [x["family"] for x in families if not x["never"]])
    print("  families with <3 sample titles: %s" % [x["family"] for x in families if len(x["sample_titles"]) < N_TITLES])
    print("  first_year==min year (left-censored at start of coverage) share per family:")
    for x in families:
        ys = [s["first_year"] for s in x["states"]]
        print("    %-28s %d/%d states first appear in %d" % (x["family"], ys.count(min(ys)), len(ys), min(ys)))


if __name__ == "__main__":
    main()
