"""Accountless public legal-history adapters; never infer adoption from missing rows.

PDAPS exports request identifying information. This adapter reads only the ordinary
public viewer page and its embedded JSON, never export/contact/login routes.
The original bytes, exact URL, rights, scan limitations and digest are retained.
No outcome data or outcome associations are read by this module.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unicodedata
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

PDAPS_TERMS = "https://pdaps.org/terms"
PDAPS_LICENSE = "CC BY-NC 3.0; https://creativecommons.org/licenses/by-nc/3.0/"
CDC_LICENSE = "ODC-BY 1.0; https://opendatacommons.org/licenses/by/1.0/"
PDAPS = {
    "pdaps_naloxone": "laws-regulating-administration-of-naloxone-1501695139",
    "pdaps_good_samaritan": "good-samaritan-overdose-laws-1501695153",
    "pdaps_pain_clinic": "pain-management-clinic-laws",
    "pdmp_mandates": "pdmp-mandates",
}
CDC = {
    "cdc_tobacco_tax": "2dwv-vfam",
    "cdc_tobacco_youth_access": "hgv5-3wrn",
    "cdc_tobacco_licensure": "eb4y-d4ic",
    "cdc_tobacco_smokefree": "32fd-hyzc",
    "cdc_tobacco_campus": "yhkp-cczf",
    "cdc_tobacco_preemption": "xsta-sbh5",
    "cdc_ecig_tax": "kwbr-syv2",
    "cdc_ecig_youth_access": "8zea-kwnt",
    "cdc_ecig_licensure": "ne52-uraz",
    "cdc_ecig_smokefree": "wan8-w4er",
    "cdc_ecig_campus": "itia-u6fu",
    "cdc_ecig_preemption": "piju-vf3p",
}
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


def get_catalog():
    result = []
    for tracker_id, slug in PDAPS.items():
        result.append({"tracker_id": tracker_id, "source_type": "policy_tracker",
                       "source_url": "https://pdaps.org/datasets/" + slug,
                       "adapter": "pdaps_public_viewer", "license": PDAPS_LICENSE,
                       "terms_url": PDAPS_TERMS,
                       "access": "public page; identifying-information export gate not used",
                       "history_complete": None, "repeals_included": "value reversals retained; official audit required"})
    for tracker_id, view in CDC.items():
        result.append({"tracker_id": tracker_id, "source_type": "policy_tracker",
                       "source_url": "https://data.cdc.gov/resource/" + view + ".csv",
                       "metadata_url": "https://data.cdc.gov/api/views/" + view + ".json",
                       "adapter": "cdc_state_quarterly", "license": CDC_LICENSE,
                       "terms_url": "https://data.cdc.gov/",
                       "access": "public Socrata without account or token",
                       "coverage_claim": "1995–2024; each provision's observed bounds validated separately",
                       "history_complete": None, "repeals_included": "quarterly changes retained, including decreases"})
    result.extend([
        {"tracker_id": "niaaa_apis", "source_url": "https://alcoholpolicy.niaaa.nih.gov/",
         "adapter": None, "access": "blocked", "reason": "403 in ordinary public request; no bypass", "history_complete": None, "license": "unverified"},
        {"tracker_id": "iihs_ghsa", "source_url": "https://www.iihs.org/topics",
         "adapter": None, "access": "pending", "reason": "current law tables do not certify repeals or full annual histories", "history_complete": None, "license": "unverified"},
        {"tracker_id": "kff", "source_url": "https://www.kff.org/state-health-facts/",
         "adapter": None, "access": "pending", "reason": "implementation dates need explicit nonadoption/repeal certification", "history_complete": None, "license": "unverified"},
        {"tracker_id": "ncsl", "source_url": "https://www.ncsl.org/",
         "adapter": None, "access": "pending", "reason": "historical state-year tables not yet acquired", "history_complete": None, "license": "unverified"},
        {"tracker_id": "dol_whd", "source_url": "https://www.dol.gov/agencies/whd/state/minimum-wage/history",
         "adapter": None, "access": "pending", "reason": "annual history adapter pending; magnitude treatment requires separate specification", "history_complete": None, "license": "unverified"},
        {"tracker_id": "tpc_eitc", "source_url": "https://www.taxpolicycenter.org/",
         "adapter": None, "access": "pending", "reason": "public history not yet acquired", "history_complete": None, "license": "unverified"},
        {"tracker_id": "nces_policy", "source_url": "https://nces.ed.gov/",
         "adapter": None, "access": "pending", "reason": "education policy dates and annual completeness not yet verified", "history_complete": None, "license": "unverified"},
    ])
    return result


def scan_untrusted(raw: bytes, source_url: str) -> dict:
    """Initial local skill check; never represent heuristics as an ML clean scan."""
    value = unicodedata.normalize("NFKC", raw.decode("utf-8", "replace"))
    value = value.translate(dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None))
    value = "".join(c for c in value if not 0xE0000 <= ord(c) <= 0xE007F)
    # HTML comments/hidden nodes/script data are deliberately included.
    decoded = []
    for token in re.findall(r"[A-Za-z0-9+/=]{24,}", value):
        if len(token) > 8192:
            continue
        try:
            text = base64.b64decode(token, validate=True).decode("utf-8")
            if text.isprintable():
                decoded.append(text)
        except (ValueError, UnicodeDecodeError):
            pass
    probe = value + "\n" + "\n".join(decoded)
    pattern = r"ignore\s+(?:all\s+|previous\s+)?instructions|system\s+prompt|reveal\s+(?:secrets|credentials)|assistant\s+must|send\s+(?:api\s+keys|credentials)"
    hits = re.findall(pattern, probe + "\n" + codecs.decode(probe, "rot_13"), re.I)
    installed = {n: importlib.util.find_spec(n) is not None for n in ("llm_guard", "transformers")}
    return {"source_url": source_url, "sha256": hashlib.sha256(raw).hexdigest(),
            "heuristic_hit_count": len(hits), "heuristic_patterns": sorted(set(hits)),
            "ml_scanner": "not run", "ml_packages_installed": installed,
            "limitation": "Normalized heuristic check only; no ML model installed/cached was used. A clean check is not proof of safety.",
            "authority": "untrusted data"}


def download_public(url: str, path: Path, license_text: str) -> dict:
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("Only explicit HTTPS public downloads permitted")
    with urllib.request.urlopen(url, timeout=120) as response:
        raw = response.read()
        resolved = response.url
    scan = scan_untrusted(raw, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.with_suffix(path.suffix + ".scan.json").write_text(json.dumps(scan, indent=2) + "\n")
    return {"url": url, "resolved_url": resolved, "path": str(path.resolve()),
            "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "license": license_text, "retrieved_at": datetime.now(timezone.utc).isoformat(), "scan": scan}


def extract_pdaps_viewer(html: str) -> dict:
    marker = re.search(r"\bvar\s+full_preview\s*=\s*", html)
    if not marker:
        raise ValueError("Public viewer has no full_preview JSON")
    # Decode JSON data only; do not execute third-party JavaScript.
    value, _ = json.JSONDecoder().raw_decode(html[marker.end():])
    if "mappable_data" not in value.get("snapshot", {}):
        raise ValueError("Viewer lacks longitudinal snapshot")
    return value


def _pdaps_date(value) -> date:
    # Viewer itself converts these Unix dates in America/New_York.
    return datetime.fromtimestamp(int(value["sec"]), ZoneInfo("America/New_York")).date()


def _binary(value):
    if value in ("1", 1, "Yes", "yes", True):
        return 1
    if value in ("0", 0, "No", "no", False):
        return 0
    return None


def annualize_intervals(rows: list, start_year: int, end_year: int) -> list:
    """Certify full calendar years; retain reversals, gaps and conflict days as unknown."""
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["unit"]].append(r)
    output = []
    for unit, intervals in sorted(grouped.items()):
        for year in range(start_year, end_year + 1):
            start, end = date(year, 1, 1), date(year, 12, 31)
            bounds = {start, end + timedelta(days=1)}
            selected = []
            for r in intervals:
                a, b = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
                if b < start or a > end:
                    continue
                selected.append((max(a, start), min(b, end), r))
                bounds.update((max(a, start), min(b, end) + timedelta(days=1)))
            missing = conflicting = unknown = 0
            statuses = set()
            boundaries = sorted(bounds)
            for a, b in zip(boundaries, boundaries[1:]):
                values = {r["active"] for x, z, r in selected if x <= a <= z}
                days = (b - a).days
                if not values:
                    missing += days
                elif None in values:
                    unknown += days
                elif len(values) > 1:
                    conflicting += days
                else:
                    statuses.update(values)
            active = next(iter(statuses)) if len(statuses) == 1 and not (missing or conflicting or unknown) else None
            output.append({"unit": unit, "year": year, "active": active,
                           "transition_year": len(statuses) > 1 and not (missing or conflicting or unknown),
                           "missing_days": missing, "unknown_days": unknown, "conflict_days": conflicting,
                           "locator": ";".join(sorted({r["locator"] for _, _, r in selected}))})
    return output


def parse_pdaps_viewer(path, tracker_id, raw_path=None) -> list:
    path = Path(path)
    data = extract_pdaps_viewer(path.read_text()) if path.suffix == ".html" else json.loads(path.read_text())
    raw_path = Path(raw_path) if raw_path else path
    raw = raw_path.read_bytes()
    snapshot = data["snapshot"]
    questions = snapshot["questions_answers"]
    records = [r for r in snapshot["mappable_data"] if r.get("state_code") in STATES and not r.get("_deleted") and not r.get("_hidden")]
    if not records:
        raise ValueError("No public state history records")
    first = min(_pdaps_date(r["effective"]) for r in records)
    last = max(_pdaps_date(r["through"]) for r in records)
    root_id = questions[0]["id"]
    parents = {child: q["id"] for q in questions for child in (q.get("children") or [])}
    output = []
    for question in questions:
        qid = question["id"]
        labels = question.get("answers") or []
        multi = "check all" in question["type"]
        is_binary = "Binary" in question["type"] or set(labels) == {"Yes", "No"}
        selections = [None] if is_binary else [x for x in labels if x.strip().lower() not in {"none", "other", "no time specified", "no specified time", "no maximum specified"}]
        if not selections:
            continue
        for option in selections:
            intervals = []
            for r in records:
                answers = r.get("answers", r)
                value = answers.get(qid)
                active = _binary(value) if option is None else (int(option in value) if isinstance(value, list) else (int(value == option) if value not in (None, "") else None))
                reason = "explicit answer"
                if active is None and _binary(answers.get(root_id)) == 0:
                    active, reason = 0, "explicit absence of parent law"
                elif active is None and qid in parents and _binary(answers.get(parents[qid])) == 0:
                    active, reason = 0, "explicit absence of parent provision"
                intervals.append({"unit": r["state_code"], "start_date": _pdaps_date(r["effective"]).isoformat(),
                                  "end_date": _pdaps_date(r["through"]).isoformat(), "end_inclusive": True,
                                  "active": active, "raw_value": value, "coding_basis": reason,
                                  "locator": "snapshot.mappable_data/" + str(r.get("record_id", r["id"])) + "/answers/" + qid,
                                  "citation": [{k: c[k] for k in ("id", "path", "url", "title") if k in c}
                                               for c in r.get("citations", {}).get(qid, [])],
                                  "law_ids": r.get("laws", []), "caution": r.get("cautions", {}).get(qid, "")})
            annual = annualize_intervals(intervals, first.year, last.year)
            fully_covered = [x for x in annual if not (x["missing_days"] or x["unknown_days"] or x["conflict_days"])]
            suffix = "__" + re.sub(r"[^a-z0-9]+", "_", option.lower()).strip("_") if option else ""
            output.append({"tracker_id": tracker_id, "policy_id": (question.get("note") or question.get("slug") or qid) + suffix,
                           "question_id": qid, "title": question["question"].strip() + (" — " + option if option else ""),
                           "definition": question.get("description") or question["question"].strip(),
                           "answer_option": option, "conditional_parent_question_id": parents.get(qid),
                           "source_url": "https://pdaps.org/datasets/" + data["slug"], "source_path": str(raw_path.resolve()),
                           "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                           "license": PDAPS_LICENSE, "terms_url": PDAPS_TERMS,
                           "as_of": _pdaps_date(data["updated_through"]).isoformat(),
                           "declared_valid_from": _pdaps_date(data["valid_from"]).isoformat(),
                           "coverage": {"start_year": first.year, "end_year": last.year, "start_date": first.isoformat(), "end_date": last.isoformat()},
                           "history_complete": len(fully_covered) == len(annual),
                           "history_completeness_rule": "All calendar days and explicit component answers, within observed intervals; no interpolation across gaps",
                           "repeals_included": True, "repeal_scope": "observed reversals preserved; no claim outside source coverage",
                           "states": sorted({r["unit"] for r in intervals}), "interval_rows": intervals,
                           "annual_rows": annual, "events": [], "source_attribution": data.get("created_by"),
                           "dataset_description": data.get("description", "")})
    return output


def parse_cdc_csv(path, tracker_id, source_url=None, metadata=None) -> list:
    path = Path(path)
    groups = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            r = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in raw.items()}
            if r.get("locationabbr") not in STATES:
                continue
            key = r["measureid"], r["provisionid"]
            value = r["provisionvalue"]
            active = _binary(value)
            if value in {"No Provision", "None"}:
                active = 0
            groups[key].append({"unit": r["locationabbr"], "year": int(r["year"]), "quarter": int(r["quarter"]),
                                "active": active, "raw_value": value, "numeric_value": r.get("provisionaltvalue"),
                                "measure": r["measuredesc"], "provision": r["provisiondesc"],
                                "effective_date": r.get("effectivedate"), "enacted_date": r.get("enacteddate"),
                                "citation": r.get("citation"), "locator": f"{key[0]}/{key[1]}/{r['locationabbr']}/{r['year']}Q{r['quarter']}"})
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    output = []
    for (measure, provision), rows in sorted(groups.items()):
        years = [r["year"] for r in rows]
        output.append({"tracker_id": tracker_id, "policy_id": measure + "_" + provision,
                       "title": rows[0]["measure"] + ": " + rows[0]["provision"],
                       "definition": rows[0]["provision"], "source_url": source_url or "https://data.cdc.gov/resource/" + CDC[tracker_id] + ".csv",
                       "source_path": str(path.resolve()), "sha256": digest, "size_bytes": path.stat().st_size,
                       "license": CDC_LICENSE, "as_of": (metadata or {}).get("rowsUpdatedAt"),
                       "coverage": {"start_year": min(years), "end_year": max(years)},
                       "history_complete": None, "repeals_included": True,
                       "repeal_scope": "quarterly stock changes retained; provision coding and coverage require validation",
                       "states": sorted({r["unit"] for r in rows}), "quarterly_rows": rows,
                       "events": [], "numeric_coding_requires_definition": any(r["active"] is None for r in rows)})
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["catalog", "download", "parse"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tracker", choices=list(PDAPS) + list(CDC))
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "catalog":
        (args.out / "tracker_catalog.json").write_text(json.dumps(get_catalog(), indent=2) + "\n")
    elif args.command == "download":
        if not args.tracker:
            parser.error("--tracker required")
        entry = next(x for x in get_catalog() if x["tracker_id"] == args.tracker)
        url = entry["source_url"]
        if args.tracker in CDC:
            # Explicit limit plus count certification prevents Socrata default 1000-row truncation.
            url += "?" + urllib.parse.urlencode({"$limit": 2000000, "$order": "year,quarter,locationabbr,measureid,provisionid"})
        target = args.out / (args.tracker + (".html" if args.tracker in PDAPS else ".csv"))
        receipt = download_public(url, target, entry["license"])
        (args.out / (args.tracker + ".download.json")).write_text(json.dumps(receipt, indent=2) + "\n")
        if args.tracker in CDC:
            metadata = download_public(entry["metadata_url"], args.out / (args.tracker + ".metadata.json"), entry["license"])
            count_url = "https://data.cdc.gov/resource/" + CDC[args.tracker] + ".json?" + urllib.parse.urlencode({"$select": "count(*) as n"})
            count_receipt = download_public(count_url, args.out / (args.tracker + ".count.json"), entry["license"])
            expected = int(json.loads(Path(count_receipt["path"]).read_text())[0]["n"])
            with target.open(newline="", encoding="utf-8-sig") as f:
                actual = sum(1 for _ in csv.DictReader(f))
            if actual != expected:
                raise ValueError(f"Truncated CDC history: {actual} != {expected}")
            receipt["metadata_download"] = metadata
            receipt["count_download"] = count_receipt
            receipt["row_count_verified"] = actual
            (args.out / (args.tracker + ".download.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    else:
        if not args.source or not args.tracker:
            parser.error("--source and --tracker required")
        policies = parse_pdaps_viewer(args.source, args.tracker) if args.tracker in PDAPS else parse_cdc_csv(args.source, args.tracker)
        (args.out / (args.tracker + ".policies.json")).write_text(json.dumps(policies, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
