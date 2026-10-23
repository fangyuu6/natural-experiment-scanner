#!/usr/bin/env python3
"""Validate scripts/viz_site/data/curated_adoption.json. Exit 1 with a list of problems on failure.

Checks: schema and types; the 8 policies in display order; every state entry has an int year and 1-3 URLs;
years inside [year_min, year_max] (and the bounds are tight); db_evidence states are a subset of states, within
+-1 year of that state's adoption year, with non-empty titles and no duplicate (policy, state) rows; excluded
states are not adopters; all ZH fields non-empty and contain CJK text; short names <= 20 chars.
When curated_raw/<id>.final.json exists, years are cross-checked against it.

Usage: python3 scripts/viz_site/validate_curated.py [PATH]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = HERE / "data" / "curated_adoption.json"
RAW = HERE / "data" / "curated_raw"

ORDER = [
    "license_recognition", "nurse_licensure_compact", "medical_licensure_compact", "tnc_framework",
    "paid_sick_leave", "paid_family_leave", "salary_history_ban", "min_wage_15",
]
STATES51 = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
    "OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
VERIFICATION = {"verified", "fixed", "single_source"}
# Single-source rows (the second pass could open one secondary page, or none) are drawn on the maps and listed with the year the
# curated data records for them. No single-source year is left open: the Alaska ride-hailing flag was closed on 2026-10-14
# (data/README.md, "Open flag"; docs/plans/site-restructure/ALASKA_YEAR_CHECK_R21.md: 2017, HB 132).
OPEN_YEAR = set()
CONFIDENCE = {"high", "medium", "low", "unknown"}
MATCH = {"enacting", "amendment"}
TEXT_EN = ("name_en", "short_en", "definition_en", "year_basis_en", "labor_topic_en")
TEXT_ZH = ("name_zh", "short_zh", "definition_zh", "year_basis_zh", "labor_topic_zh")
POLICY_KEYS = [
    "id", "name_en", "name_zh", "short_en", "short_zh", "definition_en", "definition_zh",
    "year_basis_en", "year_basis_zh", "labor_topic_en", "labor_topic_zh",
    "n_states", "year_min", "year_max", "states", "excluded", "db_evidence", "summary_sources",
]
TOP_KEYS = ["generated", "as_of", "note", "policies"]
URL_RE = re.compile(r"^https?://[^\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CJK_RE = re.compile(r"[一-鿿]")


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def nonempty_str(x):
    return isinstance(x, str) and x.strip() != ""


def validate(doc, check_raw=True):
    errs = []

    def err(msg):
        errs.append(msg)

    if not isinstance(doc, dict):
        return ["top level is not an object"]
    for k in TOP_KEYS:
        if k not in doc:
            err("top level: missing key %r" % k)
    for k in ("generated", "as_of"):
        if k in doc and not (isinstance(doc[k], str) and DATE_RE.match(doc[k])):
            err("top level: %s must be YYYY-MM-DD, got %r" % (k, doc.get(k)))
    if "note" in doc and not nonempty_str(doc["note"]):
        err("top level: note is empty")
    pols = doc.get("policies")
    if not isinstance(pols, list):
        err("policies is not a list")
        return errs

    ids = [p.get("id") if isinstance(p, dict) else None for p in pols]
    if ids != ORDER:
        err("policies order/ids are %s, expected %s" % (ids, ORDER))

    for idx, p in enumerate(pols):
        if not isinstance(p, dict):
            err("policies[%d] is not an object" % idx)
            continue
        pid = p.get("id", "#%d" % idx)

        def perr(msg, pid=pid):
            err("%s: %s" % (pid, msg))

        missing = [k for k in POLICY_KEYS if k not in p]
        if missing:
            perr("missing keys %s" % missing)
        extra = [k for k in p if k not in POLICY_KEYS and k not in {"display_name_en", "display_name_zh"}]
        if extra:
            perr("unexpected keys %s" % extra)

        if any(k in p for k in ("display_name_en", "display_name_zh")):
            if not all(nonempty_str(p.get(k)) for k in ("display_name_en", "display_name_zh")):
                perr("display names require nonempty EN and ZH strings")
            elif not CJK_RE.search(p["display_name_zh"]):
                perr("display_name_zh has no Chinese characters")
        for k in TEXT_EN:
            if k in p and not nonempty_str(p[k]):
                perr("%s is empty" % k)
        for k in TEXT_ZH:
            if k in p:
                if not nonempty_str(p[k]):
                    perr("%s is empty" % k)
                elif not CJK_RE.search(p[k]):
                    perr("%s has no Chinese characters: %r" % (k, p[k][:40]))
        for k in ("short_en", "short_zh"):
            if nonempty_str(p.get(k)) and len(p[k]) > 20:
                perr("%s longer than 20 chars (%d)" % (k, len(p[k])))

        states = p.get("states")
        if not isinstance(states, dict) or not states:
            perr("states is missing or empty")
            states = {}
        years = []
        for st, e in states.items():
            if st not in STATES51:
                perr("state %r is not a valid code" % st)
            if not isinstance(e, dict):
                perr("%s entry is not an object" % st)
                continue
            if set(e) - {"display_scope_en", "display_scope_zh"} != {"year", "verification", "confidence", "sources"}:
                perr("%s entry keys are %s" % (st, sorted(e)))
            if any(k in e for k in ("display_scope_en", "display_scope_zh")):
                if not all(nonempty_str(e.get(k)) for k in ("display_scope_en", "display_scope_zh")):
                    perr("%s display scopes require nonempty EN and ZH strings" % st)
                elif not CJK_RE.search(e["display_scope_zh"]):
                    perr("%s display_scope_zh has no Chinese characters" % st)
            if not is_int(e.get("year")):
                perr("%s year is not an int: %r" % (st, e.get("year")))
            else:
                years.append(e["year"])
            if e.get("verification") not in VERIFICATION:
                perr("%s verification %r not in %s" % (st, e.get("verification"), sorted(VERIFICATION)))
            if e.get("confidence") not in CONFIDENCE:
                perr("%s confidence %r not in %s" % (st, e.get("confidence"), sorted(CONFIDENCE)))
            src = e.get("sources")
            if not isinstance(src, list) or not 1 <= len(src) <= 3:
                perr("%s needs 1-3 source URLs, got %r" % (st, src))
            else:
                for u in src:
                    if not (isinstance(u, str) and URL_RE.match(u)):
                        perr("%s has a bad source URL %r" % (st, u))
                if len(set(src)) != len(src):
                    perr("%s has duplicate source URLs" % st)

        if p.get("n_states") != len(states):
            perr("n_states=%r but %d states listed" % (p.get("n_states"), len(states)))
        ymin, ymax = p.get("year_min"), p.get("year_max")
        if not (is_int(ymin) and is_int(ymax)):
            perr("year_min/year_max must be ints")
        elif years:
            if ymin > ymax:
                perr("year_min > year_max")
            out = sorted(st for st, e in states.items() if is_int(e.get("year")) and not ymin <= e["year"] <= ymax)
            if out:
                perr("years outside [%d, %d] for %s" % (ymin, ymax, out))
            if ymin != min(years) or ymax != max(years):
                perr("year_min/max (%d, %d) are not tight; states span %d-%d" % (ymin, ymax, min(years), max(years)))

        excluded = p.get("excluded")
        if not isinstance(excluded, dict):
            perr("excluded is not an object")
            excluded = {}
        for st, reason in excluded.items():
            if st not in STATES51:
                perr("excluded state %r is not a valid code" % st)
            if not nonempty_str(reason):
                perr("excluded %s has an empty reason" % st)
            elif len(reason) > 80:
                perr("excluded %s reason longer than 80 chars (%d)" % (st, len(reason)))
            if st in states:
                perr("%s is both adopting and excluded" % st)

        evidence = p.get("db_evidence")
        if not isinstance(evidence, list):
            perr("db_evidence is not a list")
            evidence = []
        seen = set()
        for r in evidence:
            if not isinstance(r, dict) or set(r) != {"state", "year", "policy_id", "title", "match"}:
                perr("db_evidence row has bad keys: %r" % (r,))
                continue
            st = r["state"]
            if st not in states:
                perr("db_evidence state %s is not in states" % st)
            elif is_int(r["year"]) and abs(r["year"] - states[st]["year"]) > 1:
                perr("db_evidence %s year %s is not within +-1 of adoption year %s" % (st, r["year"], states[st]["year"]))
            if not is_int(r["year"]):
                perr("db_evidence %s year is not an int" % st)
            if not nonempty_str(r["title"]):
                perr("db_evidence %s has an empty title" % st)
            elif len(r["title"]) > 90:
                perr("db_evidence %s title longer than 90 chars (%d)" % (st, len(r["title"])))
            if not nonempty_str(r["policy_id"]):
                perr("db_evidence %s has an empty policy_id" % st)
            if r["match"] not in MATCH:
                perr("db_evidence %s match %r not in %s" % (st, r["match"], sorted(MATCH)))
            key = (pid, st)
            if key in seen:
                perr("duplicate db_evidence row for state %s" % st)
            seen.add(key)

        ss = p.get("summary_sources")
        if not isinstance(ss, list) or not ss:
            perr("summary_sources is missing or empty")
        else:
            for u in ss:
                if not (isinstance(u, str) and URL_RE.match(u)):
                    perr("bad summary_sources URL %r" % (u,))

        for open_pid, open_state in sorted(OPEN_YEAR):
            if open_pid == pid and states.get(open_state, {}).get("verification") != "single_source":
                perr("%s is in OPEN_YEAR but is not a single_source row" % open_state)

        if check_raw and pid in ORDER:
            fp = RAW / (pid + ".final.json")
            if fp.exists():
                final = json.loads(fp.read_text(encoding="utf-8"))["states"]
                for st, e in states.items():
                    f = final.get(st)
                    if f is None or f.get("year") != e.get("year"):
                        perr("%s year %r differs from curated_raw final (%r)" % (st, e.get("year"), None if f is None else f.get("year")))
                kept = {st for st, f in final.items() if f.get("year") is not None and f.get("verification") in VERIFICATION}
                if kept != set(states):
                    perr("states differ from curated_raw final: missing %s, extra %s" % (sorted(kept - set(states)), sorted(set(states) - kept)))
    return errs


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("FAIL: cannot read %s: %s" % (path, e))
        return 1
    errs = validate(doc)
    if errs:
        print("FAIL: %d problem(s) in %s" % (len(errs), path))
        for e in errs:
            print("  - " + e)
        return 1
    n = sum(p["n_states"] for p in doc["policies"])
    ev = sum(len(p["db_evidence"]) for p in doc["policies"])
    print("OK: %s  (%d policies, %d state entries, %d db_evidence rows)" % (path.name, len(doc["policies"]), n, ev))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
