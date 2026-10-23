#!/usr/bin/env python3
"""Coverage rule v2 and orphan-row recovery for the policy DB (L1 work package A).

LOCAL OUTPUTS ONLY. This script never touches the live database, the Worker, R2 or any page.
It does not run scripts/build_coverage_data.py (which overwrites deploy_output/coverage_data.json); it imports only
the helpers of scripts/build_map_data.py.

Reads (never writes):
  data/output/policy_db_20260415_483k.csv              the 483k policy table; first row per policy_id wins
  src/data/inputs/raw/legiscan_openstates_merged.csv   source bills (LegiScan + Open States merged)
  src/output/llm_results.db                            LLM records (opened read-only, immutable)
  deploy_output/coverage_data.json                     rule v1 output, only compared against
  diagnosis only: src/data/inputs/processed/legiscan_openstates_merged_filtered*.csv, the expanded CSV and
  checkpoint.jsonl under src/data/source_expansion/full_collection/, src/output/enacted_trials/*/raw_policies.csv

Writes (new files only, default data/pipeline/coverage_v2/):
  coverage_data_v2.json      rule v2 coverage; keeps the keys of coverage_data.json and adds the v2 fields
  orphan_recovery_v2.csv     one row per (policy_id, url) with a blank year: recovered date/year/status/title + evidence
  recovery_summary_v2.json   counts by tier, state, status, year; holdout test; back-fill reach; sampling pool
  checks_v2.json             every count the report quotes, with expected value and pass/fail, plus the SQL rehearsal
  tables_v2.md               markdown tables for docs/plans/l1/COVERAGE_V2_20261015.md
  live_update/               SQL for the live D1 table `policies` (PREPARED, NOT EXECUTED), manifest, rollback

Rule v1 (scripts/build_coverage_data.py): not_observed = rows 2016-2026 < 300, or no rows in 2016.
Rule v2: not_observed = any complete biennium 2017-18, 2019-20, 2021-22, 2023-24 has fewer than MIN_BLOCK (20) rows.
Recovery: a table row with a blank year is matched to its source bill by the key run_enacted_trial.row_to_policy
builds (bill_id, else STATE-bill_number-session). Tier 1: every source record under the key agrees on date and status.
Tier 2: the key repeats across sessions; the row's own URL selects source records that agree. The unit is the pair
(policy_id, url), because policy_id is not unique in the table (156 ids appear twice, always with different URLs).

Usage:  python3 -B scripts/l1/coverage_v2.py [--out-dir DIR] [--min-block 20] [--generated YYYY-MM-DD]
                                              [--no-expected] [--skip-llm-db] [--skip-reach]
Stdlib only, Python 3.9+. Exit code 1 when a check fails.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict, namedtuple
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_map_data import STATES, classify, find, DB_NAME  # noqa: E402  (imported, not modified)

SOURCE_FAMILY = "State Legislation"
Y0, Y1 = 2016, 2026
V1_MIN_TOTAL = 300
BLOCKS = ((2017, 2018), (2019, 2020), (2021, 2022), (2023, 2024))
V2_MIN_BLOCK = 20
SENSITIVITY = (10, 15, 20, 25)
TRIAL_TAGS = ("both", "openstates", "legiscan", "merged_csv")

UPSTREAM = REPO / "src/data/inputs/raw/legiscan_openstates_merged.csv"
LLM_DB = REPO / "src/output/llm_results.db"
V1_JSON = REPO / "deploy_output/coverage_data.json"
PRE_FILTER = REPO / "src/extraction/pre_filter.py"
REACH_SOURCES = {
    "filtered_2016_csv": REPO / "src/data/inputs/processed/legiscan_openstates_merged_filtered_2016.csv",
    "filtered_csv": REPO / "src/data/inputs/processed/legiscan_openstates_merged_filtered.csv",
    "expanded_csv": REPO / "src/data/source_expansion/full_collection/expanded_policies_merged.csv",
    "checkpoint": REPO / "src/data/source_expansion/full_collection/checkpoint.jsonl",
}
ENACTED_DIR = REPO / "src/output/enacted_trials"
DEFAULT_OUT = REPO / "data/pipeline/coverage_v2"
FORBIDDEN_OUT = [REPO / p for p in ("deploy_output", "src", "data/output", "data/l2_pipeline", "docs", "scripts", "worker")]

# Snapshot-specific numbers the report quotes; checks_v2.json compares every run against them.
EXPECTED = {
    "raw_rows": 483570, "unique_policy_ids": 483414, "duplicate_rows": 156, "duplicate_rows_other_url": 156,
    "state_rows": 41652, "state_rows_with_year": 27425, "state_unique_ids": 41496, "v1_in_window_rows": 27269, "v1_not_observed": 22,
    "dropped_not_state": 113, "dropped_blank_family": 52, "dropped_blank_year": 14062,
    "orphans": 14071, "orphans_blank_jurisdiction": 9, "blank_year_raw_rows": 14227,
    "tier1": 11463, "tier2": 2608, "unrecovered": 0,
    "after_in_window_rows": 41340, "v2_observed": 46, "v2_observed_after": 46,
    "v2_not_observed": ["KS", "MN", "MO", "NM", "WI"],
}

UpRec = namedtuple("UpRec", "source state bill_id bill_number session bill_title description status status_date "
                            "legiscan_url state_link")


# ----------------------------------------------------------------------------- small helpers

def parse_year(s):
    """Same as scripts/build_coverage_data.py: int(float(s)) or None."""
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def upstream_key(row):
    """Key of a source bill, as src/scripts/run_enacted_trial.py::row_to_policy builds the policy_id suffix."""
    bill_id = str(row.get("bill_id") or "").strip()
    if bill_id:
        return bill_id, "bill_id"
    state = str(row.get("state") or "").strip().upper()
    bill_number = str(row.get("bill_number") or "").strip()
    session = str(row.get("session_name") or row.get("session_title") or "").strip()
    return "-".join(p for p in (state, bill_number, session) if p) or "row", "state-number-session"


def parsed_date(rec):
    """run_enacted_trial._parse_date: date.fromisoformat(status_date[:10]); None when blank or invalid."""
    text = (rec.status_date or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def norm_url(u):
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower()).rstrip("/")


def clean_title(rec):
    """Title as row_to_policy builds it (bill_title, else description, else bill_number, else bill_id); each line
    break becomes one space, as in the stored titles."""
    t = ((rec.bill_title or "").strip() or (rec.description or "").strip() or rec.bill_number.strip()
         or rec.bill_id.strip() or "Untitled policy")
    return re.sub(r"[\r\n]", " ", t).replace("\x00", "")


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def assert_safe_out_dir(path):
    p = Path(path).resolve()
    for bad in FORBIDDEN_OUT:
        b = bad.resolve()
        if p == b or b in p.parents:
            sys.exit("refusing to write under %s (outputs must be new files outside the published tree)" % b)
    return p


# ----------------------------------------------------------------------------- readers

def read_policy_rows(db_csv):
    """Stream the policy table once. First row per policy_id wins (all families), as every site builder does.

    Returns dict(first = pid -> first State Legislation row, blank_rows = every raw State Legislation row with an
    unparseable year (id, pid, url, ...), good_keys = {(pid, url)} of State Legislation rows that have a year,
    counts)."""
    csv.field_size_limit(sys.maxsize)
    first, blank_rows, good_keys, seen, counts = {}, [], set(), {}, Counter()
    with open(db_csv, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        need = {"id", "policy_id", "jurisdiction", "date", "year", "status", "source_family", "policy_family",
                "title", "url"}
        missing = need - set(rd.fieldnames or [])
        if missing:
            sys.exit("policy db missing columns: %s" % sorted(missing))
        for row in rd:
            counts["raw_rows"] += 1
            pid = row["policy_id"]
            is_dup = pid in seen
            if is_dup:
                counts["duplicate_rows"] += 1
                counts["duplicate_rows_same_url" if seen[pid] == row["url"] else "duplicate_rows_other_url"] += 1
            else:
                seen[pid] = row["url"]
            if row["source_family"] != SOURCE_FAMILY:
                continue
            counts["state_rows"] += 1
            rec = {k: row[k] for k in ("id", "jurisdiction", "date", "year", "status", "policy_family", "title", "url")}
            rec["pid"] = pid
            if parse_year(row["year"]) is None:
                blank_rows.append(dict(rec, is_first=not is_dup))
            else:
                good_keys.add((pid, row["url"]))
            if not is_dup:
                first[pid] = rec
    counts["unique_policy_ids"] = len(seen)
    return {"first": first, "blank_rows": blank_rows, "good_keys": good_keys, "counts": counts}


def load_skip_fn():
    """src/extraction/pre_filter.should_skip_bill, loaded by path (the package __init__ needs httpx)."""
    if not PRE_FILTER.exists():
        return None
    spec = importlib.util.spec_from_file_location("l1_pre_filter", PRE_FILTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.should_skip_bill


def read_upstream(path, wanted, skip_fn=None):
    """One pass over the merged source-bill CSV. Returns (key -> [UpRec], rows, pool_by_state).

    pool_by_state = enacted, status_date >= 2016, not ceremonial: the pool the 10% sample was drawn from."""
    csv.field_size_limit(sys.maxsize)
    out, n, pool = defaultdict(list), 0, Counter()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            n += 1
            key, _ = upstream_key(row)
            sd = (row.get("status_date") or "").strip()
            if skip_fn is not None and (row.get("status") or "").strip().lower() == "enacted" and sd[:4].isdigit() \
                    and int(sd[:4]) >= Y0:
                if not skip_fn(str(row.get("bill_title") or row.get("description") or ""))[0]:
                    pool[str(row.get("state") or "").strip().upper()] += 1
            if key in wanted:
                out[key].append(UpRec(
                    row.get("source") or "", str(row.get("state") or "").strip().upper(),
                    (row.get("bill_id") or "").strip(), (row.get("bill_number") or "").strip(),
                    str(row.get("session_name") or row.get("session_title") or "").strip(),
                    row.get("bill_title") or "", row.get("description") or "",
                    (row.get("status") or "").strip().lower(), sd,
                    (row.get("legiscan_bill_url") or "").strip(), (row.get("state_bill_link") or "").strip()))
    return out, n, pool


def read_llm_records(db_path, pids):
    """policy_id (table form) -> list of (llm_policy_id, chapter_id, title, jurisdiction, introduced_date, status)."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    out, pids = defaultdict(list), sorted(pids)
    try:
        for i in range(0, len(pids), 150):
            cands = {}
            for p in pids[i:i + 150]:
                for tag in TRIAL_TAGS:
                    cands["trial-%s-%s" % (tag, p)] = p
                cands[p] = p
            ph = ",".join("?" * len(cands))
            for lid, chap, rj in con.execute(
                    "SELECT policy_id, chapter_id, result_json FROM results WHERE policy_id IN (%s)" % ph, list(cands)):
                d = json.loads(rj) if rj else {}
                out[cands[lid]].append((lid, chap, d.get("title"), d.get("jurisdiction"), d.get("introduced_date"),
                                        d.get("policy_status")))
    finally:
        con.close()
    return out


def backfill_reach(orphan_pids, llm_ids):
    """Can scripts/rebuild_policy_db.py back-fill reach the orphans? Mirrors its three lookups.

    1. checkpoint.jsonl entries by full llm policy_id.  2. enacted_trials raw_policies.csv by id/policy_id.
    3. csv_lookup keyed by NON-BLANK bill_id from expanded_policies_merged.csv and the 2016-filtered merged CSV,
       consulted by numeric_id (last '-' segment of a trial- id) or by the full id."""
    csv.field_size_limit(sys.maxsize)
    full_ids = {x[0] for p in orphan_pids for x in llm_ids.get(p, [])}
    numeric = {lid.split("-")[-1] for lid in full_ids if lid.startswith("trial-")}
    orphan_set = set(orphan_pids)
    res = {"orphans": len(orphan_pids), "llm_ids": len(full_ids)}

    def count_csv(path):
        """reachable_by_backfill = orphans the lookup can return (non-blank bill_id the back-fill asks for);
        present_* = orphans found at all, by bill_id (numeric ids) or by STATE-bill_number-session (text ids)."""
        reach_ids, present_num, present_txt, rows = set(), set(), set(), 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows += 1
                b = (row.get("bill_id") or "").strip()
                if b and (b in numeric or b in full_ids):
                    reach_ids.add(b)
                key, _ = upstream_key(row)
                if key in orphan_set:
                    (present_num if key.isdigit() else present_txt).add(key)
        return {"rows": rows, "reachable_by_backfill": len(reach_ids), "present_numeric_ids": len(present_num),
                "present_text_ids": len(present_txt)}

    for name in ("filtered_2016_csv", "filtered_csv", "expanded_csv"):
        res[name] = count_csv(REACH_SOURCES[name]) if REACH_SOURCES[name].exists() else None
    ck = {"entries": 0, "hits": 0}
    if REACH_SOURCES["checkpoint"].exists():
        with open(REACH_SOURCES["checkpoint"], encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    i = json.loads(line).get("id", "")
                except json.JSONDecodeError:
                    continue
                ck["entries"] += 1
                ck["hits"] += i in full_ids
    res["checkpoint"] = ck
    et = {"files": 0, "rows": 0, "hits": 0}
    for p in sorted(ENACTED_DIR.glob("**/raw_policies.csv")):
        with open(p, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            col = "id" if "id" in (rd.fieldnames or []) else ("policy_id" if "policy_id" in (rd.fieldnames or []) else None)
            if not col:
                continue
            et["files"] += 1
            for row in rd:
                et["rows"] += 1
                et["hits"] += (row.get(col) or "").strip() in full_ids
    res["enacted_trials_raw_policies"] = et
    return res


# ----------------------------------------------------------------------------- recovery

def resolve(url, recs):
    """Match one table row (its own url) to its source bill. Returns a dict or None.

    tier 1: every source record under the key has the same (date, status) and a valid date.
    tier 2: the key is ambiguous; the records whose legiscan_bill_url or state_bill_link equals the row's url
            (exact, else scheme/www/slash-insensitive) all share one (date, status). Anything else: None."""
    if not recs:
        return None

    def pick(selected, tier, evidence):
        pairs = {(parsed_date(r), r.status) for r in selected}
        if len(pairs) != 1:
            return None
        d, st = next(iter(pairs))
        if d is None or not (1900 <= int(d[:4]) <= 2100) or not st:
            return None
        r = next((x for x in selected if (x.bill_title or "").strip()), selected[0])
        return {"tier": tier, "evidence": evidence, "n_upstream": len(recs), "n_selected": len(selected),
                "date": d, "year": int(d[:4]), "status": st, "title": clean_title(r), "state": r.state,
                "source": r.source}

    out = pick(recs, 1, "unique_key" if len(recs) == 1 else "agreeing_records")
    if out:
        return out
    u = (url or "").strip()
    if not u:
        return None
    exact = [r for r in recs if u in (r.legiscan_url, r.state_link)]
    if exact:
        out = pick(exact, 2, "key+url")
        if out:
            return out
    loose = [r for r in recs if norm_url(u) and norm_url(u) in (norm_url(r.legiscan_url), norm_url(r.state_link))]
    return pick(loose, 2, "key+url_loose") if loose else None


def recover_rows(blank_rows, upstream):
    """Resolve every distinct (policy_id, url) that has a blank-year raw row. Returns {(pid, url): resolution or None}."""
    out = {}
    for b in blank_rows:
        k = (b["pid"], b["url"])
        if k not in out:
            out[k] = resolve(b["url"], upstream.get(b["pid"]))
    return out


def holdout(first_rows, upstream):
    """Hide-and-recover test on rows whose year is known: does the recovery return the stored values?"""
    c = Counter()
    for pid, row in first_rows.items():
        if parse_year(row["year"]) is None:
            continue
        r = resolve(row["url"], upstream.get(pid))
        if not r:
            c["unresolved"] += 1
            continue
        t = "tier%d" % r["tier"]
        c[t + "_n"] += 1
        c[t + "_year_match"] += r["year"] == parse_year(row["year"])
        c[t + "_date_match"] += r["date"] == row["date"]
        c[t + "_title_match"] += r["title"] == row["title"]
        if row["status"] in ("enacted", "proposed"):
            c[t + "_status_n"] += 1
            c[t + "_status_match"] += r["status"] == row["status"]
            c[t + "_status_proposed_vs_enacted"] += (row["status"], r["status"]) == ("proposed", "enacted")
        else:
            c[t + "_status_unknown_in_table"] += 1
    return dict(c)


# ----------------------------------------------------------------------------- coverage rules

def window_total(by_year):
    return sum(by_year.get(y, 0) for y in range(Y0, Y1 + 1))


def rule_v1(by_year):
    """scripts/build_coverage_data.py, same logic: (reasons, codes)."""
    total, reasons, codes = window_total(by_year), [], []
    if total < V1_MIN_TOTAL:
        reasons.append("thin coverage: %d state-law rows in %d-%d (< %d)" % (total, Y0, Y1, V1_MIN_TOTAL))
        codes.append("thin")
    if by_year.get(Y0, 0) == 0:
        reasons.append("no %d rows (first year of the window)" % Y0)
        codes.append("no_first_year")
    return reasons, codes


def rule_v2(by_year, min_block=V2_MIN_BLOCK, blocks=BLOCKS):
    """Rule v2: every complete biennium needs >= min_block rows. Returns (by_block, reasons, codes, failing)."""
    by_block = [sum(by_year.get(y, 0) for y in b) for b in blocks]
    failing = ["%d-%d" % b for b, n in zip(blocks, by_block) if n < min_block]
    reasons = ["%d-%d has %d state-law rows (< %d)" % (b[0], b[1], n, min_block)
               for b, n in zip(blocks, by_block) if n < min_block]
    return by_block, reasons, (["sparse_block"] if failing else []), failing


def calendar_label(by_year):
    """Data-derived: odd_years_only when every in-window row sits in an odd year."""
    even = sum(n for y, n in by_year.items() if y % 2 == 0)
    odd = sum(n for y, n in by_year.items() if y % 2 == 1)
    return "odd_years_only" if odd > 0 and even == 0 else "all_years"


def count_rows(first_rows, resolutions, tiers):
    """By state and year, with the v1 filters in the v1 order (state, policy_family, year).

    A blank-year first row takes its year (and, if its jurisdiction is blank, its state) from the resolution of
    (policy_id, url) when the tier is allowed. Returns (by_year, outside, dropped, recovered)."""
    by_year, outside, dropped = defaultdict(Counter), Counter(), Counter()
    recovered = defaultdict(Counter)   # state -> tier -> rows inside the window
    for pid, row in first_rows.items():
        state, _ = classify(row["jurisdiction"])
        year = parse_year(row["year"])
        tier = None
        res = resolutions.get((pid, row["url"])) if year is None else None
        if res and res["tier"] in tiers:
            year, tier = res["year"], res["tier"]
            if state not in STATES:
                state, _ = classify(("%s State" % res["state"]) if res["state"] else "")
        if state not in STATES:
            dropped["not a state/DC jurisdiction"] += 1
        elif not row["policy_family"].strip():
            dropped["blank policy_family"] += 1
        elif year is None:
            dropped["unparseable/blank year"] += 1
        elif Y0 <= year <= Y1:
            by_year[state][year] += 1
            if tier:
                recovered[state][tier] += 1
        else:
            outside[state] += 1
    return by_year, outside, dropped, recovered


def state_entry(code, by_year, outside, min_block, v1_style=False):
    c = by_year.get(code, Counter())
    d = {"by_year": [c.get(y, 0) for y in range(Y0, Y1 + 1)], "total": window_total(c), "outside_window": outside.get(code, 0)}
    if v1_style:
        reasons, codes = rule_v1(c)
        d.update(not_observed=bool(codes), not_observed_reason="; ".join(reasons), reason_codes=codes)
        return d
    by_block, reasons, codes, failing = rule_v2(c, min_block)
    d.update(by_block=by_block, calendar=calendar_label(c), not_observed=bool(codes),
             not_observed_reason="; ".join(reasons), reason_codes=codes, failing_blocks=failing)
    return d


def sensitivity(by_year, blocks=BLOCKS):
    out = {}
    for m in SENSITIVITY:
        no = sorted(c for c in STATES if rule_v2(by_year.get(c, Counter()), m, blocks)[2])
        out[str(m)] = {"observed": len(STATES) - len(no), "not_observed": no}
    return out


# ----------------------------------------------------------------------------- live-update SQL (prepared, not run)

def url_cond(url):
    return "url = %s" % sql_str(url) if url else "(url IS NULL OR url = '')"


def row_where(pid, url):
    return "policy_id = %s AND %s AND source_family = 'State Legislation'" % (sql_str(pid), url_cond(url))


def update_statement(pid, url, r, blank_jurisdiction):
    """One idempotent UPDATE for the live `policies` table. Only rows that still have a blank year change."""
    sets = ["title = CASE WHEN title IS NULL OR title = '' THEN %s ELSE title END" % sql_str(r["title"]),
            "date = %s" % sql_str(r["date"]), "year = %s" % sql_str(str(r["year"])),
            "status = %s" % sql_str(r["status"])]
    if blank_jurisdiction and r["state"]:
        sets.append("jurisdiction = CASE WHEN jurisdiction IS NULL OR jurisdiction = '' THEN %s ELSE jurisdiction END"
                    % sql_str("%s State" % r["state"]))
    return "UPDATE policies SET %s WHERE %s AND (year IS NULL OR year = '');" % (", ".join(sets), row_where(pid, url))


def rollback_statements(pid, url, r, rows, has_year_row=False):
    """Exact inverse for the blank-year rows of one (policy_id, url), restoring the values read from the local table.

    Keyed by policy_id, url and the date and year this update wrote. Restored one by one by id when the old status
    or jurisdiction differ between the rows, or when a row with the same policy_id and url already had a year
    (the update never touched it, so a key-wide rollback would blank it)."""
    sig = {(x["status"], x["jurisdiction"] == "") for x in rows}
    guard = "%s AND year = %s AND date = %s" % (row_where(pid, url), sql_str(str(r["year"])), sql_str(r["date"]))

    def one(where, old_status, blank_jur):
        s = "UPDATE policies SET title = '', date = '', year = '', status = %s" % sql_str(old_status)
        if blank_jur:
            s += ", jurisdiction = ''"
        return "%s WHERE %s;" % (s, where)

    if len(sig) == 1 and not has_year_row:
        (st, bj), = sig
        return [one(guard, st, bj)]
    return [one("id = %d AND %s" % (int(x["id"]), guard), x["status"], x["jurisdiction"] == "") for x in rows]


GROUPS_PROPAGATE = (
    "UPDATE policy_groups SET\n"
    "  date = (SELECT p.date FROM policies p WHERE p.policy_id = policy_groups.policy_id AND p.url = policy_groups.url\n"
    "          AND p.source_family = 'State Legislation' AND p.date <> '' ORDER BY p.id LIMIT 1),\n"
    "  title = CASE WHEN title IS NULL OR title = '' THEN (SELECT p.title FROM policies p WHERE p.policy_id = "
    "policy_groups.policy_id AND p.url = policy_groups.url AND p.source_family = 'State Legislation' AND p.date <> '' "
    "ORDER BY p.id LIMIT 1) ELSE title END,\n"
    "  jurisdiction = CASE WHEN jurisdiction IS NULL OR jurisdiction = '' THEN (SELECT p.jurisdiction FROM policies p "
    "WHERE p.policy_id = policy_groups.policy_id AND p.url = policy_groups.url AND p.source_family = 'State Legislation' "
    "AND p.date <> '' ORDER BY p.id LIMIT 1) ELSE jurisdiction END\n"
    "WHERE (date IS NULL OR date = '')\n"
    "  AND EXISTS (SELECT 1 FROM policies p WHERE p.policy_id = policy_groups.policy_id AND p.url = policy_groups.url "
    "AND p.source_family = 'State Legislation' AND p.date <> '');"
)

PRECHECK_SQL = """-- Read-only. Expected values hold when the live table equals data/output/policy_db_20260415_483k.csv.
SELECT COUNT(*) AS blank_year_state_rows FROM policies
  WHERE source_family = 'State Legislation' AND (year IS NULL OR year = '');                   -- expect {rows}
SELECT COUNT(*) AS total_rows FROM policies;                                                    -- expect {total}
SELECT status, COUNT(*) FROM policies WHERE source_family = 'State Legislation'
  AND (year IS NULL OR year = '') GROUP BY status ORDER BY status;                             -- expect {status}
SELECT id, policy_id FROM policies WHERE policy_id IN ({sample}) ORDER BY policy_id;          -- expect id:policy_id = {ids}
SELECT COUNT(*) AS state_rows_with_year FROM policies WHERE source_family = 'State Legislation'
  AND year BETWEEN '2016' AND '2026';                                                            -- expect {inwin_before}
"""

POSTCHECK_SQL = """-- Read-only. Run after every update chunk has succeeded.
SELECT COUNT(*) AS blank_year_state_rows FROM policies
  WHERE source_family = 'State Legislation' AND (year IS NULL OR year = '');                   -- expect {left} ({left_t1} if only the tier 1 files ran)
SELECT COUNT(*) AS recovered_rows_in_sample FROM policies
  WHERE source_family = 'State Legislation' AND year <> '' AND date <> '' AND status = 'enacted'
  AND policy_id IN ({sample});                                                                   -- expect {nsample}
SELECT COUNT(*) AS total_rows FROM policies;                                                    -- expect {total}, unchanged
SELECT COUNT(*) AS state_rows_with_year FROM policies WHERE source_family = 'State Legislation'
  AND year BETWEEN '2016' AND '2026';                                                            -- expect {inwin} ({inwin_t1} if only the tier 1 files ran)
"""


def build_live_update(resolutions, blank_rows, out_dir, good_keys=frozenset(), orphans=frozenset(), total_rows=0,
                      state_rows=0, chunk=500):
    """Write the prepared SQL under out_dir/live_update. Returns the manifest dict (also written).

    The update and rollback statements are split by tier (files 01_policies_update_t1_* and _t2_*), so that tier 1
    alone can be applied. Every statement is one line."""
    d = Path(out_dir) / "live_update"
    d.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for b in blank_rows:
        groups[(b["pid"], b["url"])].append(b)
    keys = sorted(k for k in groups if resolutions.get(k))
    files = []

    def write(name, lines, kind):
        p = d / name
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files.append({"file": "live_update/" + name, "kind": kind,
                      "statements": sum(1 for l in lines if l.startswith("UPDATE")),
                      "sha256": sha256_file(p), "bytes": p.stat().st_size})

    ups, rbs, rows_by_tier = {1: [], 2: []}, {1: [], 2: []}, Counter()
    for k in keys:
        r, rows = resolutions[k], groups[k]
        t = r["tier"]
        ups[t].append(update_statement(k[0], k[1], r, any(x["jurisdiction"] == "" for x in rows)))
        rbs[t].extend(rollback_statements(k[0], k[1], r, rows, k in good_keys))
        rows_by_tier[t] += len(rows)
    n_rows = sum(rows_by_tier.values())
    unresolved = sum(1 for b in blank_rows if not resolutions.get((b["pid"], b["url"])))
    # sample of ids whose every table row is blank, so the post-check counts are exact
    pure = [k for k in keys if k[0] in orphans and len(groups[k]) == 1 and resolutions[k]["tier"] == 1]
    sample_keys = pure[:: max(1, len(pure) // 5)][:5]
    sample = [k[0] for k in sample_keys]
    sample_sql = ", ".join(sql_str(p) for p in sample)
    sample_ids = ", ".join("%s:%s" % (groups[k][0]["id"], k[0]) for k in sorted(sample_keys))
    status = Counter(x["status"] for k in keys for x in groups[k])
    n_files = {t: (len(ups[t]) + chunk - 1) // chunk for t in (1, 2)}
    expected_names = {"00_precheck.sql", "02_policy_groups_propagate.sql", "03_postcheck.sql"}
    for t in (1, 2):
        expected_names |= {"01_policies_update_t%d_%03d.sql" % (t, i + 1) for i in range(n_files[t])}
        expected_names |= {"rollback_policies_t%d_%03d.sql" % (t, i + 1) for i in range(n_files[t])}
    stale = sorted(f.name for f in d.glob("*.sql") if f.name not in expected_names)
    if stale:
        sys.exit("stale SQL files in %s (this script never deletes; move them away first): %s%s" % (
            d, ", ".join(stale[:5]), " ..." if len(stale) > 5 else ""))
    write("00_precheck.sql", PRECHECK_SQL.format(
        rows=n_rows, total=total_rows, sample=sample_sql, status=", ".join("%s %d" % kv for kv in sorted(status.items())),
        ids=sample_ids, inwin_before=state_rows - n_rows - unresolved).rstrip("\n").split("\n"), "read-only")
    for t in (1, 2):
        for i in range(n_files[t]):
            write("01_policies_update_t%d_%03d.sql" % (t, i + 1), ups[t][i * chunk:(i + 1) * chunk], "update-tier%d" % t)
    write("02_policy_groups_propagate.sql",
          ["-- Optional. Copies date, title and jurisdiction from the updated `policies` rows into the `policy_groups` rows "
           "(same policy_id and url) whose date is blank.", "-- Not reversible by SQL; use D1 Time Travel.", GROUPS_PROPAGATE],
          "update-optional")
    write("03_postcheck.sql", POSTCHECK_SQL.format(
        left=unresolved, left_t1=unresolved + rows_by_tier[2], sample=sample_sql, nsample=len(sample), total=total_rows,
        inwin=state_rows - unresolved, inwin_t1=state_rows - unresolved - rows_by_tier[2]).rstrip("\n").split("\n"), "read-only")
    for t in (1, 2):
        for i in range(n_files[t]):
            write("rollback_policies_t%d_%03d.sql" % (t, i + 1), rbs[t][i * chunk:(i + 1) * chunk], "rollback-tier%d" % t)
    all_ups = ups[1] + ups[2]
    manifest = {
        "purpose": "Re-attach date, year, status and title (and jurisdiction where blank) to the State Legislation rows of the "
                   "live D1 table `policies` whose year is blank. PREPARED ONLY: nothing here has been executed.",
        "target": "Cloudflare D1 database policy-db: table policies (step 01); table policy_groups (optional step 02)",
        "row_key": "policy_id + url + source_family, only where year is blank (idempotent)",
        "policy_id_url_pairs": len(keys), "raw_rows_expected": n_rows, "update_statements": len(all_ups),
        "tiers": {"tier1": {"statements": len(ups[1]), "rows": rows_by_tier[1], "files": n_files[1]},
                  "tier2": {"statements": len(ups[2]), "rows": rows_by_tier[2], "files": n_files[2]}},
        "rollback_statements": len(rbs[1]) + len(rbs[2]), "statements_per_file": chunk, "files": files,
        "status_before_in_these_rows": dict(sorted(status.items())),
        "max_statement_bytes": max(len(x.encode("utf-8")) for x in all_ups + rbs[1] + rbs[2]),
        "apply": ["cd worker", "npx wrangler d1 time-travel info policy-db   # write down the bookmark first",
                  "npx wrangler d1 execute policy-db --remote --file=../data/pipeline/coverage_v2/live_update/00_precheck.sql",
                  "for each 01_policies_update_t1_NNN.sql, then each 01_policies_update_t2_NNN.sql, in order: "
                  "npx wrangler d1 execute policy-db --remote --file=<file>   (tier 1 alone is a valid stopping point)",
                  "npx wrangler d1 execute policy-db --remote --file=../data/pipeline/coverage_v2/live_update/03_postcheck.sql",
                  "optional: 02_policy_groups_propagate.sql"],
        "rollback": "npx wrangler d1 time-travel restore policy-db --bookmark=<bookmark written down before> "
                    "(exact), or run rollback_policies_t*_NNN.sql (keyed by the same row key)",
    }
    manifest["bundle_sha256"] = hashlib.sha256(
        "".join("%s %s\n" % (f["file"], f["sha256"]) for f in sorted(files, key=lambda f: f["file"])).encode()).hexdigest()
    (d / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def rehearse(first_rows, blank_rows, resolutions, out_dir):
    """Replay the prepared SQL on in-memory SQLite copies of the State Legislation rows (no files, no network).

    Checks: the update changes exactly the blank-year rows with a resolution, to their recovered values; rows with a
    year, other families and policy_groups rows with a date stay as they are; a second run is a no-op; the rollback
    restores every original value; and the tier 1 files alone change exactly the tier 1 rows."""
    live = Path(out_dir) / "live_update"
    cols = "id, policy_id, title, jurisdiction, date, year, status, url"
    rows = {}
    for pid, r in first_rows.items():
        rows[int(r["id"])] = (pid, r["title"], r["jurisdiction"], r["date"], r["year"], r["status"], r["url"])
    for b in blank_rows:
        rows[int(b["id"])] = (b["pid"], b["title"], b["jurisdiction"], b["date"], b["year"], b["status"], b["url"])
    keys = sorted({(b["pid"], b["url"]) for b in blank_rows if resolutions.get((b["pid"], b["url"]))})

    def populate():
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE policies (id INTEGER PRIMARY KEY, policy_id TEXT, title TEXT, jurisdiction TEXT, date TEXT, "
                    "year TEXT, status TEXT, url TEXT, source_family TEXT)")
        con.execute("CREATE INDEX idx_policy_id ON policies(policy_id)")
        con.execute("CREATE TABLE policy_groups (id INTEGER PRIMARY KEY, family TEXT, policy_id TEXT, title TEXT, "
                    "jurisdiction TEXT, date TEXT, url TEXT)")
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,'State Legislation')", [(i,) + v for i, v in sorted(rows.items())])
        con.execute("INSERT INTO policies VALUES (-1,'US-HB1-114th Congress','','US Federal','','','unknown','u','Federal Legislation')")
        con.executemany("INSERT INTO policy_groups (family, policy_id, title, jurisdiction, date, url) VALUES ('f',?,'','','',?)", keys)
        con.execute("INSERT INTO policy_groups (family, policy_id, title, jurisdiction, date, url) VALUES ('f','keep','T','J','2020-01-01','k')")
        return con

    def run(con, pattern):
        for f in sorted(live.glob(pattern)):
            con.executescript(f.read_text(encoding="utf-8"))

    def snapshot(con):
        return {r[0]: r for r in con.execute("SELECT %s FROM policies" % cols)}

    def blank_count(con):
        return con.execute("SELECT COUNT(*) FROM policies WHERE (year IS NULL OR year='') AND source_family='State Legislation'").fetchone()[0]

    exp = {int(b["id"]): resolutions[(b["pid"], b["url"])] for b in blank_rows if resolutions.get((b["pid"], b["url"]))}
    exp_t1 = {i for i, r in exp.items() if r["tier"] == 1}

    # tier 1 files alone
    c1 = populate()
    before1 = snapshot(c1)
    run(c1, "01_policies_update_t1_*.sql")
    after1 = snapshot(c1)
    changed1 = {i for i in before1 if after1[i] != before1[i]}

    # everything
    con = populate()
    before = snapshot(con)
    n_blank = blank_count(con)
    run(con, "01_policies_update_t*.sql")
    after = snapshot(con)
    changed = {i for i in before if after[i] != before[i]}
    had_title = {i: bool(b[2]) for i, b in before.items()}
    ok_values = all(after[i][4] == r["date"] and after[i][5] == str(r["year"]) and after[i][6] == r["status"]
                    and (after[i][2] == r["title"] or had_title[i]) for i, r in exp.items())
    blank_left = blank_count(con)
    run(con, "01_policies_update_t*.sql")
    again = snapshot(con)
    con.executescript(live.joinpath("02_policy_groups_propagate.sql").read_text(encoding="utf-8"))
    g_blank = con.execute("SELECT COUNT(*) FROM policy_groups WHERE date = ''").fetchone()[0]
    g_keep = con.execute("SELECT title, date FROM policy_groups WHERE policy_id='keep'").fetchone()
    g_ok = all(con.execute("SELECT date FROM policy_groups WHERE policy_id=? AND url=?", k).fetchone()[0]
               == resolutions[k]["date"] for k in keys)
    run(con, "rollback_policies_t*.sql")
    restored = snapshot(con)
    return {"rows_blank_before": n_blank, "rows_changed": len(changed), "expected_rows_changed": len(exp),
            "changed_ids_match": changed == set(exp), "values_match_recovery": ok_values,
            "blank_after_update": blank_left, "second_run_is_noop": again == after,
            "federal_guard_row_unchanged": after[-1] == before[-1], "groups_blank_after_propagate": g_blank,
            "groups_each_row_got_its_own_date": g_ok, "groups_keep_row_unchanged": g_keep == ("T", "2020-01-01"),
            "rollback_restores_original": restored == before,
            "tier1_files_alone_change_exactly_tier1_rows": changed1 == exp_t1}


# ----------------------------------------------------------------------------- outputs

def write_recovery_csv(path, resolutions, blank_rows, llm):
    groups = defaultdict(list)
    for b in blank_rows:
        groups[(b["pid"], b["url"])].append(b)
    cols = ["policy_id", "url", "raw_ids", "blank_rows", "first_row_blank", "llm_policy_id", "tier", "evidence",
            "n_upstream_records", "state", "jurisdiction_old", "jurisdiction_new", "date_new", "year_new", "status_old",
            "status_new", "title_new"]
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for k in sorted(groups):
            r = resolutions.get(k)
            if not r:
                continue
            rows = groups[k]
            jur_old = rows[0]["jurisdiction"]
            w.writerow([k[0], k[1], "|".join(sorted(x["id"] for x in rows)), len(rows), int(any(x["is_first"] for x in rows)),
                        "|".join(sorted({l[0] for l in llm.get(k[0], [])})), r["tier"], r["evidence"], r["n_upstream"],
                        r["state"], jur_old, ("%s State" % r["state"]) if jur_old == "" and r["state"] else "", r["date"],
                        r["year"], "|".join(sorted({x["status"] for x in rows})), r["status"], r["title"]])
            n += 1
    return n


def md_tables(states_after, states_before, states_v1, rec_state, pool, sampled, min_block, sens_before, sens_after):
    """Markdown for the report, generated from the JSON values so the report cannot drift from the data."""
    codes = sorted(STATES)
    f = "{:,}".format

    def flag(e):
        return "not observed" if e["not_observed"] else "observed"
    n_flag = sum(1 for c in codes if states_v1[c]["not_observed"])
    out = ["### Table 1. The %d jurisdictions rule v1 flags" % n_flag, "",
           "| State | rows before | raw pool | sampled / pool | orphan rows | rule v1 reason | cause | rule v2 after |",
           "|---|---:|---:|---:|---:|---|---|---|"]
    for c in sorted((c for c in codes if states_v1[c]["not_observed"]), key=lambda c: (states_v1[c]["total"], c)):
        v, a = states_v1[c], states_after[c]
        p = pool.get(c)
        orph = sum(rec_state.get(c, {}).values())
        if a["calendar"] == "odd_years_only":
            cause = "odd-year legislature"
        elif orph and v["total"] + orph >= V1_MIN_TOTAL and "thin" in v["reason_codes"]:
            cause = "years lost to orphan rows"
        elif a["not_observed"]:
            cause = "sample too small"
        else:
            cause = "fixed bar of 300 on a ~10% sample"
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            c, f(v["total"]), f(p) if p else "n/a", ("%.1f%%" % (100.0 * sampled.get(c, 0) / p)) if p else "n/a",
            f(orph), "+".join(v["reason_codes"]), cause,
            "not observed (%s)" % ", ".join(a["failing_blocks"]) if a["not_observed"] else "observed"))
    out += ["", "### Table 2. Sensitivity to the biennium threshold (observed jurisdictions out of 51)", "",
            "| MIN_BLOCK | observed before recovery | not observed before | observed after recovery | not observed after |",
            "|---:|---:|---|---:|---|"]
    for m in SENSITIVITY:
        sb, sa = sens_before[str(m)], sens_after[str(m)]
        out.append("| %d%s | %d | %s | %d | %s |" % (m, " (rule v2)" if m == min_block else "", sb["observed"],
                                                      ", ".join(sb["not_observed"]), sa["observed"], ", ".join(sa["not_observed"])))
    out += ["", "### Table 3. Rows 2016-2026 per jurisdiction, before and after, with the flags", "",
            "| State | rows before | recovered tier 1 | recovered tier 2 | rows after | rule v1 | rule v2 before | rule v2 after | rows per biennium after (17-18 / 19-20 / 21-22 / 23-24) |",
            "|---|---:|---:|---:|---:|---|---|---|---|"]
    for c in sorted(codes, key=lambda c: (states_v1[c]["total"], c)):
        a, b, v = states_after[c], states_before[c], states_v1[c]
        rs = rec_state.get(c, {})
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            c, f(v["total"]), f(rs.get(1, 0)), f(rs.get(2, 0)), f(a["total"]), flag(v), flag(b), flag(a),
            " / ".join(f(x) for x in a["by_block"])))
    return "\n".join(out) + "\n"


def run_checks(results, expected, use_expected):
    """results: name -> value. Booleans must be True; other values are compared with `expected` when listed."""
    out = []
    for name, val in results.items():
        if isinstance(val, bool):
            out.append({"name": name, "value": val, "expected": True, "ok": val})
        elif use_expected and name in expected:
            out.append({"name": name, "value": val, "expected": expected[name], "ok": val == expected[name]})
        else:
            out.append({"name": name, "value": val, "expected": None, "ok": True})
    return out


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--min-block", type=int, default=V2_MIN_BLOCK)
    ap.add_argument("--generated", default=date.today().isoformat())
    ap.add_argument("--no-expected", action="store_true", help="skip the comparison with the snapshot-specific expected counts")
    ap.add_argument("--skip-llm-db", action="store_true")
    ap.add_argument("--skip-reach", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    out_dir = assert_safe_out_dir(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_csv = find(DB_NAME)
    print("policy table: %s" % db_csv)
    P = read_policy_rows(db_csv)
    first, blank_rows, good_keys, pc = P["first"], P["blank_rows"], P["good_keys"], P["counts"]
    orphan_pids = sorted(p for p, r in first.items() if parse_year(r["year"]) is None)
    print("raw rows %d, duplicate rows %d, unique ids %d, State Legislation ids %d, blank-year rows %d, orphans %d (%.0fs)" % (
        pc["raw_rows"], pc["duplicate_rows"], pc["unique_policy_ids"], len(first), len(blank_rows), len(orphan_pids), time.time() - t0))

    wanted = set(first) | {b["pid"] for b in blank_rows}
    upstream, up_rows, pool = read_upstream(UPSTREAM, wanted, load_skip_fn())
    missing_key = sorted(p for p in wanted if p not in upstream)
    print("source bills: %d rows, %d keys matched (%.0fs)" % (up_rows, len(upstream), time.time() - t0))

    resolutions = recover_rows(blank_rows, upstream)
    ho = holdout(first, upstream)
    orphan_res = {p: resolutions.get((p, first[p]["url"])) for p in orphan_pids}
    t1 = [p for p, r in orphan_res.items() if r and r["tier"] == 1]
    t2 = [p for p, r in orphan_res.items() if r and r["tier"] == 2]
    unrec = [p for p, r in orphan_res.items() if not r]
    dup_blank = [b for b in blank_rows if not b["is_first"]]
    dup_res = Counter(("tier%d" % resolutions[(b["pid"], b["url"])]["tier"]) if resolutions.get((b["pid"], b["url"])) else "unresolved"
                      for b in dup_blank)
    print("orphans %d: tier 1 %d, tier 2 %d, unrecovered %d; later rows with a blank year %d: %s" % (
        len(orphan_pids), len(t1), len(t2), len(unrec), len(dup_blank), dict(dup_res)))

    pids_for_llm = set(orphan_pids) | {b["pid"] for b in blank_rows}
    llm = {} if args.skip_llm_db or not LLM_DB.exists() else read_llm_records(LLM_DB, pids_for_llm)
    reach = None if args.skip_reach else backfill_reach(orphan_pids, llm)
    print("llm records and back-fill reach done (%.0fs)" % (time.time() - t0))

    # ---- coverage: before (rule v1 on the table as it is) and after (recovered years)
    by0, out0, drop0, _ = count_rows(first, {}, ())
    by1, out1, drop1, rec_state = count_rows(first, resolutions, (1, 2))
    by_t1, out_t1, _, _ = count_rows(first, resolutions, (1,))
    states_v1 = {c: state_entry(c, by0, out0, args.min_block, v1_style=True) for c in sorted(STATES)}
    states_before = {c: state_entry(c, by0, out0, args.min_block) for c in sorted(STATES)}
    states_after = {c: state_entry(c, by1, out1, args.min_block) for c in sorted(STATES)}
    states_v1_after = {c: state_entry(c, by1, out1, args.min_block, v1_style=True) for c in sorted(STATES)}
    states_t1 = {c: state_entry(c, by_t1, out_t1, args.min_block) for c in sorted(STATES)}
    sens_b, sens_a = sensitivity(by0), sensitivity(by1)
    for c in states_after:
        states_after[c]["total_before"] = states_v1[c]["total"]
        states_after[c]["recovered_rows"] = {"tier1": rec_state.get(c, {}).get(1, 0), "tier2": rec_state.get(c, {}).get(2, 0)}
        states_after[c]["not_observed_v1_before"] = states_v1[c]["not_observed"]
    nos = lambda st: sorted(c for c in st if st[c]["not_observed"])  # noqa: E731
    no_after, no_before_v2, no_v1, no_v1_after, no_t1 = (nos(states_after), nos(states_before), nos(states_v1),
                                                         nos(states_v1_after), nos(states_t1))

    sampled = Counter()
    for pid, r in first.items():
        s, _ = classify(r["jurisdiction"])
        res = orphan_res.get(pid)
        if s not in STATES and res and res["state"]:
            s, _ = classify("%s State" % res["state"])
        if s in STATES:
            sampled[s] += 1

    inputs = {
        "policy_csv": {"path": str(db_csv.relative_to(REPO)), "bytes": db_csv.stat().st_size, "sha256": sha256_file(db_csv)},
        "source_bills_csv": {"path": str(UPSTREAM.relative_to(REPO)), "bytes": UPSTREAM.stat().st_size,
                             "sha256": sha256_file(UPSTREAM), "rows": up_rows},
        "llm_results_db": {"path": str(LLM_DB.relative_to(REPO)), "bytes": LLM_DB.stat().st_size,
                           "opened": "read-only, immutable"} if LLM_DB.exists() else None,
    }
    in_before = sum(s["total"] for s in states_v1.values())
    in_after = sum(s["total"] for s in states_after.values())
    doc = {
        "generated": args.generated, "rule_version": 2, "years": [Y0, Y1],
        "rule": "not_observed = any complete biennium %s has fewer than %d State Legislation rows" % (
            ", ".join("%d-%d" % b for b in BLOCKS), args.min_block),
        "blocks": [list(b) for b in BLOCKS], "min_block_rows": args.min_block,
        "not_tested": "%d (tail of the 2015-16 session) and 2025-26 (2026 is a partial year); both are still plotted" % Y0,
        "summary": {"observed": len(STATES) - len(no_after), "not_observed": no_after,
                    "rule_v1_before": {"observed": len(STATES) - len(no_v1), "not_observed": no_v1},
                    "rule_v2_before_recovery": {"observed": len(STATES) - len(no_before_v2), "not_observed": no_before_v2},
                    "rule_v1_after_recovery": {"observed": len(STATES) - len(no_v1_after), "not_observed": no_v1_after},
                    "rule_v2_tier1_only": {"observed": len(STATES) - len(no_t1), "not_observed": no_t1},
                    "odd_years_only": sorted(c for c in states_after if states_after[c]["calendar"] == "odd_years_only"),
                    "sensitivity_min_block": {"before_recovery": sens_b, "after_recovery": sens_a}},
        "recovery": {"orphan_policy_ids": len(orphan_pids), "tier1": len(t1), "tier2": len(t2), "unrecovered": len(unrec),
                     "rows_in_window_before": in_before, "rows_in_window_after": in_after,
                     "rows_in_window_tier1_only": sum(by_t1[c][y] for c in by_t1 for y in by_t1[c])},
        "inputs": inputs, "states": states_after,
    }
    (out_dir / "coverage_data_v2.json").write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    n_csv = write_recovery_csv(out_dir / "orphan_recovery_v2.csv", resolutions, blank_rows, llm)

    # ---- summary json
    st_counter = Counter((orphan_res[p]["tier"], first[p]["status"], orphan_res[p]["status"]) for p in orphan_pids if orphan_res[p])
    yr_counter = Counter(orphan_res[p]["year"] for p in orphan_pids if orphan_res[p])
    state_counter = defaultdict(Counter)
    for p in orphan_pids:
        r = orphan_res[p]
        if r:
            s, _ = classify(first[p]["jurisdiction"])
            if s not in STATES:
                s, _ = classify("%s State" % r["state"])
            state_counter[s][r["tier"]] += 1
    llm_meta = Counter()
    for p in orphan_pids:
        rec = llm.get(p, [])
        if rec:
            _lid, _chap, t, j, d, _st = rec[0]
            llm_meta[("title_blank" if not t else "title_set", "jurisdiction_blank" if not j else "jurisdiction_set",
                      "introduced_date=%s" % d)] += 1
    prefix_counter = Counter(x[0].split("-")[1] if x[0].startswith("trial-") else "other"
                             for p in orphan_pids for x in llm.get(p, []))
    frac = {c: sampled.get(c, 0) / pool[c] for c in STATES if pool.get(c)}
    topup = [c for c in sorted(STATES) if c <= "GA"]          # AK..GA: the block with an extra pass
    rest = [c for c in sorted(STATES) if c > "GA"]            # HI..WY: the uniform ~10% sample

    def spread(codes):
        v = sorted(frac[c] for c in codes if c in frac)
        return {"n": len(v), "min": round(v[0], 4), "median": round(v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2, 4),
                "max": round(v[-1], 4)} if v else None
    pool_total = sum(pool.values())
    url_year, url_year_far = Counter(), Counter()
    for p in orphan_pids:
        r = orphan_res[p]
        m = re.search(r"legiscan\.com/[A-Za-z]{2}/bill/[^/]+/(\d{4})", first[p]["url"])
        if r and m:
            gap = r["year"] - int(m.group(1))
            url_year[gap] += 1
            if gap >= 2 or gap < 0:
                url_year_far["%s|gap=%d|tier%d" % (r["state"], gap, r["tier"])] += 1
    summary = {
        "orphan_definition": "State Legislation policy_id whose first table row has an unparseable (blank) year",
        "orphans": len(orphan_pids), "blank_year_raw_rows": len(blank_rows),
        "blank_year_rows_after_first": len(dup_blank), "blank_year_rows_after_first_resolution": dict(dup_res),
        "policy_id_pairs_with_blank_year": len({(b["pid"], b["url"]) for b in blank_rows}),
        "orphans_blank_jurisdiction": sum(1 for p in orphan_pids if classify(first[p]["jurisdiction"])[0] not in STATES),
        "orphan_key_type": dict(Counter("numeric bill_id" if p.isdigit() else "state-number-session" for p in orphan_pids)),
        "tiers": {"tier1": len(t1), "tier2": len(t2)},
        "tier2_url_match": dict(Counter(orphan_res[p]["evidence"] for p in t2)),
        "status_old_to_new": {"%d|%s|%s" % k: v for k, v in sorted(st_counter.items())},
        "by_state_tier": {s: dict(c) for s, c in sorted(state_counter.items())},
        "recovered_year_counts": dict(sorted(yr_counter.items())),
        "recovered_year_minus_legiscan_url_year": dict(sorted(url_year.items())),
        "recovered_year_minus_url_year_gap_2_or_more_or_negative": dict(url_year_far),
        "llm_results_records": {"policy_ids_found": sum(1 for p in orphan_pids if llm.get(p)), "prefix": dict(prefix_counter),
                                "metadata_in_llm_record": {"|".join(k): v for k, v in llm_meta.items()}},
        "backfill_reach": reach, "holdout_known_year_rows": ho,
        "table_policy_id_not_unique": {"duplicate_rows": pc["duplicate_rows"], "same_url": pc["duplicate_rows_same_url"],
                                       "other_url": pc["duplicate_rows_other_url"]},
        "keys_missing_in_source_bills": len(missing_key),
        "pool_and_sampling": {c: {"raw_pool": pool.get(c), "sampled_rows": sampled.get(c, 0)} for c in sorted(STATES)},
        "sampling_fraction": {"pool_total_all_states_and_US": pool_total, "pool_total_51": sum(pool.get(c, 0) for c in STATES),
                              "block_HI_to_WY": dict(spread(rest), codes=len(rest)), "block_AK_to_GA": dict(spread(topup), codes=len(topup))},
        "dropped_v1": dict(drop0), "dropped_after": dict(drop1),
        "unresolved_orphans": unrec[:50],
    }
    (out_dir / "recovery_summary_v2.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "tables_v2.md").write_text(
        md_tables(states_after, states_before, states_v1, rec_state, pool, sampled, args.min_block, sens_b, sens_a), encoding="utf-8")

    manifest = build_live_update(resolutions, blank_rows, out_dir, good_keys, set(orphan_pids), pc["raw_rows"], pc["state_rows"])
    reh = rehearse(first, blank_rows, resolutions, out_dir)

    # ---- checks
    old = json.loads(V1_JSON.read_text(encoding="utf-8-sig")) if V1_JSON.exists() else None
    v1_same = bool(old) and all(
        all(old["states"][c][k] == states_v1[c][k] for k in ("by_year", "total", "outside_window", "not_observed",
                                                              "not_observed_reason", "reason_codes")) for c in STATES)
    with open(out_dir / "orphan_recovery_v2.csv", newline="", encoding="utf-8") as fh:
        csv_back = list(csv.DictReader(fh))
    resolved_pairs = sum(1 for v in resolutions.values() if v)
    results = {
        "raw_rows": pc["raw_rows"], "unique_policy_ids": pc["unique_policy_ids"], "duplicate_rows": pc["duplicate_rows"],
        "duplicate_rows_other_url": pc["duplicate_rows_other_url"], "state_rows": pc["state_rows"],
        "state_rows_with_year": pc["state_rows"] - len(blank_rows), "state_unique_ids": len(first), "v1_in_window_rows": in_before, "v1_not_observed": len(no_v1),
        "dropped_not_state": drop0["not a state/DC jurisdiction"], "dropped_blank_family": drop0["blank policy_family"],
        "dropped_blank_year": drop0["unparseable/blank year"],
        "orphans": len(orphan_pids), "orphans_blank_jurisdiction": summary["orphans_blank_jurisdiction"],
        "blank_year_raw_rows": len(blank_rows), "tier1": len(t1), "tier2": len(t2), "unrecovered": len(unrec),
        "after_in_window_rows": in_after, "v2_observed": len(STATES) - len(no_before_v2),
        "v2_observed_after": len(STATES) - len(no_after), "v2_not_observed": no_after,
        "v1_output_reproduced_exactly": v1_same,
        "every_policy_id_found_in_source_bills": not missing_key,
        "every_blank_year_row_resolved": all(resolutions.values()),
        "no_recovery_outside_window": all(Y0 <= r["year"] <= Y1 for r in resolutions.values() if r),
        "recovery_changes_no_v2_flag": no_before_v2 == no_after,
        "holdout_tier1_year_exact": ho.get("tier1_n", 0) > 0 and ho.get("tier1_year_match") == ho.get("tier1_n"),
        "holdout_tier1_date_exact": ho.get("tier1_n", 0) > 0 and ho.get("tier1_date_match") == ho.get("tier1_n"),
        "holdout_tier2_year_exact": ho.get("tier2_n", 0) > 0 and ho.get("tier2_year_match") == ho.get("tier2_n"),
        "holdout_tier2_date_exact": ho.get("tier2_n", 0) > 0 and ho.get("tier2_date_match") == ho.get("tier2_n"),
        "holdout_status_differs_only_proposed_vs_enacted":
            sum(ho.get("tier%d_status_n" % t, 0) - ho.get("tier%d_status_match" % t, 0) for t in (1, 2))
            == sum(ho.get("tier%d_status_proposed_vs_enacted" % t, 0) for t in (1, 2)),
        "recovery_csv_rows_equal_resolved_pairs": len(csv_back) == resolved_pairs == n_csv,
        "sum_of_blocks_matches_years": all(
            states_after[c]["by_block"] == [sum(states_after[c]["by_year"][y - Y0] for y in b) for b in BLOCKS] for c in STATES),
        "live_update_covers_every_blank_year_row": manifest["raw_rows_expected"] == len(blank_rows),
        "rehearsal_changes_exactly_the_blank_rows": reh["changed_ids_match"] and reh["rows_changed"] == reh["expected_rows_changed"],
        "rehearsal_values_equal_recovery": reh["values_match_recovery"],
        "rehearsal_no_blank_year_left": reh["blank_after_update"] == 0,
        "rehearsal_second_run_is_noop": reh["second_run_is_noop"],
        "rehearsal_guard_rows_unchanged": reh["federal_guard_row_unchanged"] and reh["groups_keep_row_unchanged"],
        "rehearsal_groups_filled_with_own_dates": reh["groups_blank_after_propagate"] == 0 and reh["groups_each_row_got_its_own_date"],
        "rehearsal_rollback_restores_original": reh["rollback_restores_original"],
        "rehearsal_tier1_files_alone_change_exactly_tier1_rows": reh["tier1_files_alone_change_exactly_tier1_rows"],
    }
    if reach:
        results["reach_filtered_2016_csv_hits"] = (reach["filtered_2016_csv"] or {}).get("reachable_by_backfill")
        results["reach_checkpoint_hits"] = reach["checkpoint"]["hits"]
        results["reach_enacted_trials_hits"] = reach["enacted_trials_raw_policies"]["hits"]
    exp = dict(EXPECTED, reach_filtered_2016_csv_hits=0, reach_checkpoint_hits=0, reach_enacted_trials_hits=0)
    checks = run_checks(results, exp, not args.no_expected)
    (out_dir / "checks_v2.json").write_text(json.dumps(
        {"generated": args.generated, "checks": checks, "rehearsal": reh, "live_update_files": manifest["files"]},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    failed = [c for c in checks if not c["ok"]]
    print("\nrule v1 before:          %d observed, %d not observed" % (len(STATES) - len(no_v1), len(no_v1)))
    print("rule v2 before recovery: %d observed, not observed %s" % (len(STATES) - len(no_before_v2), no_before_v2))
    print("rule v2 after recovery:  %d observed, not observed %s" % (len(STATES) - len(no_after), no_after))
    print("rows in window: %d -> %d (tier 1 only: %d)" % (in_before, in_after, doc["recovery"]["rows_in_window_tier1_only"]))
    print("live update: %d update statements for %d rows; rehearsal %s" % (
        manifest["update_statements"], manifest["raw_rows_expected"], "OK" if all(
            c["ok"] for c in checks if c["name"].startswith("rehearsal")) else "FAILED"))
    print("checks: %d, failed: %d (%.0fs)" % (len(checks), len(failed), time.time() - t0))
    for c in failed:
        print("  FAIL %s value=%r expected=%r" % (c["name"], c["value"], c["expected"]))
    print("outputs in %s" % out_dir)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
