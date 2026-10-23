#!/usr/bin/env python3
"""Independently reconcile the L4 seed against local dossiers and frozen runs.

This validator intentionally does not import the builder or any site totals.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2025-12-31"
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
STATE_NAMES = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"],
))
# Plan section 2 is the independently fixed acceptance table.
EXPECTED = {
    "license_recognition": (26, 8, 6, [2020], "Signed", "签署"),
    "nurse_licensure_compact": (40, 7, 17, [2017], "Signed", "签署"),
    "medical_licensure_compact": (43, 11, 12, [2015], "Signed", "签署"),
    "tnc_framework": (42, 7, 19, [2015], "Signed", "签署"),
    "paid_sick_leave": (18, 12, 4, [2018], "First covered-employer mandate effective", "首个适用雇主强制规定生效"),
    "paid_family_leave": (10, 9, 2, [2020], "Benefits payable", "福利开始支付"),
    "salary_history_ban": (18, 7, 5, [2019], "Private-employer ban effective", "私营雇主禁问规定生效"),
    "min_wage_15": (16, 8, 4, [2019], "Enacted", "制定"),
}
WITHHELD = {("nurse_licensure_compact", "CT")} | {("tnc_framework", s) for s in ("AK", "CT", "MT", "NH", "OH", "OK")}
EXCLUDED = {("license_recognition", "NJ"): "disputed", ("tnc_framework", "CA"): "disputed", ("salary_history_ban", "AL"): "removed"}
# Current compact results are bound by unchanged site sidecars; completed session
# hashes name the unchanged full originals retained in full_archive.
FROZEN_RESULT_HASHES = {
    "paid_sick_leave": "5f5dc1ac90a4d94258aeb44682eb7d7dc70d3f74d6c2d1cc53481525eceec197",
    "medicaid_expansion": "93de8741ed94cc75522b64c6fe06a2a7c7c7e3c3126502fa9e9e2c756dc74111",
    "castle_doctrine": "0ea95917d0d460e3e4680392d0f138d25b70ce6a7e0f5a3623b6a20b24a9e13f",
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


PUBLIC_MARKERS = re.compile(r"\b(?:V[123]|FIX|Researcher|research\s+had|TODO|null)\b", re.IGNORECASE)


def validate_public_fields(value, path="public") -> list[str]:
    """Reject research markers in normalized public string values.

    Source reconciliation retains the local research records. Call this separate
    check on the public projection; keys and absent (None) values are not prose.
    Errors name the field without repeating potentially private research text.
    """
    errors = []

    def visit(item, location):
        if isinstance(item, str):
            if PUBLIC_MARKERS.search(item):
                errors.append(f"{location}: internal research marker in public field")
        elif isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{location}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{location}[{index}]")

    visit(value, path)
    return errors


def validate(payload: dict, root: Path = ROOT, outcome_inventory: dict | None = None) -> list[str]:
    """Return errors; empty means every independent reconciliation passed."""
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    data = root / "scripts/viz_site/data"
    curated = _load(data / "curated_adoption.json")
    if outcome_inventory is None:
        outcome_inventory = _load(data / "outcome_inventory.json")
    check(payload.get("schema_version") == 1, "schema_version must be 1")
    check(payload.get("cutoff") == CUTOFF == curated.get("as_of"), "cutoff must be 2025-12-31")
    check(payload.get("year_columns") == list(range(2004, 2026)), "year columns must be 2004–2025")
    check(payload.get("strict_dated_total") == 213, "strict total must be 213")
    check(payload.get("panel_support") is None, "panel_support must remain null without a validated study panel")
    families = payload.get("family", [])
    summaries = payload.get("opportunity_summary", [])
    check([f.get("id") for f in families] == list(EXPECTED), "family IDs/order differ from named eight-family frame")
    check([s.get("family_id") for s in summaries] == list(EXPECTED), "summary IDs/order differ")
    family_map = {f.get("id"): f for f in families}
    summary_map = {s.get("family_id"): s for s in summaries}
    inventory = payload.get("family_jurisdiction", [])
    check(len(inventory) == 408, "family_jurisdiction must contain 408 rows")
    keys = [(r.get("family_id"), r.get("jurisdiction")) for r in inventory]
    check(len(set(keys)) == len(keys), "duplicate family/jurisdiction rows")
    check(set(keys) == {(fid, state) for fid in EXPECTED for state in STATES}, "all 51 jurisdictions required per family")
    row_map = dict(zip(keys, inventory))
    total, withheld_seen, excluded_seen, included_seen = 0, set(), set(), set()
    source_paths = [data / "curated_adoption.json"]
    for policy in curated["policies"]:
        fid = policy["id"]
        if fid not in EXPECTED:
            errors.append(f"unexpected source family {fid}")
            continue
        n_expected, g_expected, modal_expected, modal_years_expected, clock_en, clock_zh = EXPECTED[fid]
        final_path = data / "curated_raw" / f"{fid}.final.json"
        verify_path = data / "curated_raw" / f"{fid}.verify.json"
        final, verify = _load(final_path), _load(verify_path)
        source_paths += [final_path, verify_path]
        check(final.get("policy_id") == verify.get("policy_id") == fid, f"{fid}: source family join mismatch")
        check(final.get("as_of") == verify.get("as_of") == CUTOFF, f"{fid}: source cutoff mismatch")
        check(set(final["states"]) == STATES, f"{fid}: raw inventory not 51 jurisdictions")
        family = family_map.get(fid, {})
        expected_family = {
            "id": fid, "label_en": policy.get("display_name_en", policy["name_en"]),
            "label_zh": policy.get("display_name_zh", policy["name_zh"]),
            "short_en": policy["short_en"], "short_zh": policy["short_zh"],
            "definition_en": policy["definition_en"], "definition_zh": policy["definition_zh"],
            "domain_en": policy.get("labor_topic_en"), "domain_zh": policy.get("labor_topic_zh"),
            "cutoff": CUTOFF, "clock_en": clock_en, "clock_zh": clock_zh,
            "date_basis_en": policy["year_basis_en"], "date_basis_zh": policy["year_basis_zh"],
        }
        check(family == expected_family, f"{fid}: bilingual family fields or exact clock differ from source/plan")
        groups, accounting = {}, Counter()
        for state, raw in final["states"].items():
            key = (fid, state)
            record = row_map.get(key, {})
            year, recorded_date = raw.get("year"), raw.get("date")
            is_dated = type(year) is int and 2004 <= year <= 2025
            if recorded_date is not None:
                try:
                    d = date.fromisoformat(recorded_date)
                    check(year is not None and d.year == year, f"{fid}/{state}: structured date/year mismatch")
                    is_dated = is_dated and d <= date(2025, 12, 31)
                except (TypeError, ValueError):
                    errors.append(f"{fid}/{state}: invalid structured date")
                    is_dated = False
            excluded_reason = policy.get("excluded", {}).get(state)
            detail = verify.get("details", {}).get(state)
            included = is_dated and raw.get("verification") in ("verified", "fixed") and excluded_reason is None
            if key in EXCLUDED:
                status = EXCLUDED[key]
                excluded_seen.add(key)
                check(excluded_reason is not None, f"{fid}/{state}: missing explicit exclusion")
            elif excluded_reason is not None:
                errors.append(f"{fid}/{state}: unexpected exclusion")
                status = "disputed"
            elif raw.get("verification") == "single_source":
                status = "withheld_single_source"
                withheld_seen.add(key)
            elif included:
                status = "strict_dated"
            elif year is None:
                status = "undated"
            else:
                status = "outside_cutoff_or_unresolved"
            accounting[status] += 1
            expected_row = {
                "family_id": fid, "jurisdiction": state, "recorded_year": year, "recorded_date": recorded_date,
                "date_precision": "day" if recorded_date is not None else "year" if year is not None else None,
                "verification": raw["verification"], "confidence": raw.get("confidence"),
                "included_strict": included, "inventory_status": status, "sources": raw["sources"],
                "citation": None, "scope_note": raw.get("note") or None,
                "signing_date": recorded_date if fid in {"license_recognition", "nurse_licensure_compact", "medical_licensure_compact", "tnc_framework"} else None,
                "effective_date": recorded_date if fid in {"paid_sick_leave", "salary_history_ban"} else None,
                "operative_date": recorded_date if fid in {"paid_sick_leave", "paid_family_leave"} else None,
                "enacted_date": recorded_date if fid == "min_wage_15" else None,
                "policy_status_basis": final["year_basis"], "exclusion_reason": excluded_reason,
                "verification_detail": detail,
            }
            for field, value in expected_row.items():
                check(field in record and record[field] == value, f"{fid}/{state}: {field} source join/null mismatch")
            check(record.get("jurisdiction_name_en") == STATE_NAMES[state], f"{fid}/{state}: jurisdiction name does not match state code")
            check(all(isinstance(url, str) and url.startswith(("https://", "http://")) for url in raw["sources"]), f"{fid}/{state}: invalid source URL")
            if state in policy["states"]:
                curated_row = policy["states"][state]
                check(curated_row["year"] == year and curated_row["verification"] == raw["verification"], f"{fid}/{state}: curated/final disagreement")
            if included:
                check(state in policy["states"], f"{fid}/{state}: strict dated entry absent from curated source")
                groups.setdefault(year, []).append(state)
                included_seen.add(key)
        group_vector = [{"year": year, "count": len(groups[year]), "jurisdictions": sorted(groups[year])} for year in sorted(groups)]
        count = sum(g["count"] for g in group_vector)
        mode = max((g["count"] for g in group_vector), default=None)
        years = [g["year"] for g in group_vector if g["count"] == mode]
        total += count
        check((count, len(groups), mode, years) == (n_expected, g_expected, modal_expected, modal_years_expected), f"{fid}: independently recomputed plan table differs")
        expected_summary = {
            "family_id": fid, "strict_dated_jurisdictions": count, "year_group_count": len(groups),
            "year_min": min(groups) if groups else None, "year_max": max(groups) if groups else None,
            "year_group_sizes": group_vector, "modal_count": mode, "modal_share": mode / count if count else None,
            "modal_years": years, "status_accounting": dict(sorted(accounting.items())),
            "verified_nonoperative_at_cutoff": None, "eligible_comparison_count": None,
        }
        check(sum(accounting.values()) == 51, f"{fid}: status accounting must sum to 51")
        check(summary_map.get(fid) == expected_summary, f"{fid}: summary/group vector/status accounting/nulls differ")
    check(total == 213, "recomputed strict total differs from 213")
    check(withheld_seen == WITHHELD, "seven withheld single-source entries differ")
    check(excluded_seen == set(EXCLUDED), "three distinct exclusions differ")
    check(not (included_seen & withheld_seen or included_seen & excluded_seen or excluded_seen & withheld_seen), "strict/withheld/exclusion sets overlap")
    check(payload.get("source_provenance") == [{"path": p.relative_to(root).as_posix(), "sha256": _hash(p)} for p in source_paths], "source provenance hashes differ")
    _validate_outcomes(payload, outcome_inventory, check)
    _validate_runs(payload, root, check)
    # Presentation checks run only after the independent source reconciliation.
    # Raw notes, provenance and frozen-run metadata are not public prose.
    from scripts.viz_site.design_opportunity_adapter import normalize_payload
    for lang in ("en", "zh"):
        try:
            normalize_payload(payload, lang)
        except ValueError as exc:
            errors.append(f"public.{lang}: {exc}")
        except (KeyError, TypeError):
            errors.append(f"public.{lang}: cannot normalize invalid ledger")
    public_runs = [{"label": row.get("label"), "stop_reason": row.get("stop_reason"),
                    "outcomes": [run.get("outcome") for run in row.get("primary_runs", [])]}
                   for row in payload.get("run_evidence", [])]
    errors.extend(validate_public_fields(public_runs, "public.run_evidence"))
    return errors


def _validate_outcomes(payload, inventory, check):
    records = inventory.get("records", [])
    check(inventory.get("status_vocabulary") == ["survey_candidate", "source_confirmed", "local_panel_validated"], "outcome status vocabulary differs")
    check(payload.get("outcome_inventory") == records, "outcome_inventory source binding differs")
    check(len({r.get("id") for r in records}) == len(records), "duplicate outcome IDs")
    for record in records:
        prefix = f"outcome {record.get('id')}"
        status = record.get("status")
        check(status in {"survey_candidate", "source_confirmed", "local_panel_validated"}, f"{prefix}: invalid status")
        check(set(record.get("family_ids", [])) <= set(EXPECTED), f"{prefix}: unknown family")
        for field in ("series", "provider", "population", "measure", "frequency", "first_observed_year", "last_observed_year", "missing_years", "geography", "access", "breaks", "source_confirmed_metadata", "local_panel_validation"):
            check(field in record, f"{prefix}: missing {field}")
        if status == "survey_candidate":
            check(record.get("survey_evidence", {}).get("sha256") == inventory.get("survey_evidence", {}).get("sha256"), f"{prefix}: survey provenance missing")
            check(all(record.get(f) is None for f in ("first_observed_year", "last_observed_year", "missing_years", "source_confirmed_metadata", "local_panel_validation")), f"{prefix}: unobserved metadata must remain null")
        elif status in {"source_confirmed", "local_panel_validated"}:
            check(bool(record.get("source_confirmed_metadata")), f"{prefix}: source-confirmed metadata required")
        if status == "local_panel_validated":
            check(bool(record.get("local_panel_validation")), f"{prefix}: actual local-panel validation required")


def _validate_runs(payload, root, check):
    records = payload.get("run_evidence", [])
    check([r.get("example_id") for r in records] == list(FROZEN_RESULT_HASHES), "run evidence must contain the three named frozen cases")
    result_dir = root / "scripts/did_samples/results"
    for row in records:
        example_id = row.get("example_id")
        if example_id not in FROZEN_RESULT_HASHES:
            continue
        result_path = result_dir / f"{example_id}.json"
        site_path = result_dir / f"{example_id}_site.json"
        result, site = _load(result_path), _load(site_path)
        check(_hash(result_path) == FROZEN_RESULT_HASHES[example_id] == site.get("result_sha256") == row.get("result_sha256"), f"{example_id}: frozen result hash mismatch")
        session_path = result_dir / f"session_{result['session_id']}.json"
        session = _load(session_path)
        check(session.get("session_id") == result["session_id"] and example_id in session.get("registered_example_ids", []), f"{example_id}: completed session membership mismatch")
        freeze = session.get("freeze_verification", {})
        check(freeze.get("verified") is True and freeze.get("issues") == [], f"{example_id}: completed session freeze verification failed")
        check(set(session.get("registered_example_ids", [])) == set(FROZEN_RESULT_HASHES) and len(session.get("registered_example_ids", [])) == 3 and set(session.get("results", {})) == set(FROZEN_RESULT_HASHES), f"{example_id}: completed session three-case inventory mismatch")
        original_hash = session["results"][example_id]
        archive_path = result_dir / "full_archive" / f"{example_id}.json.gz"
        if original_hash != _hash(result_path):
            check(hashlib.sha256(gzip.decompress(archive_path.read_bytes())).hexdigest() == original_hash, f"{example_id}: original frozen archive hash mismatch")
        display = site["site"]["public_display"]
        expected = {
            "example_id": example_id, "label": site["site"]["policy"], "status": result["status"],
            "stop_reason": display.get("stop_reason"), "stop_reason_code": display.get("stop_reason_code"),
            "link": {lang: f"/viz/{lang}/worked_examples.html" + (f"#{example_id}-heading" if example_id != "castle_doctrine" else "") for lang in ("en", "zh")},
            "curated_family_id": example_id if example_id == "paid_sick_leave" else None,
            "result_sha256": _hash(result_path), "site_sha256": _hash(site_path),
            "result_path": result_path.relative_to(root).as_posix(), "site_path": site_path.relative_to(root).as_posix(),
            "session_id": result["session_id"], "session_sha256": _hash(session_path), "session_path": session_path.relative_to(root).as_posix(),
            "original_session_result_sha256": original_hash,
            "original_archive_path": archive_path.relative_to(root).as_posix() if original_hash != _hash(result_path) else None,
        }
        for field, value in expected.items():
            check(row.get(field) == value, f"{example_id}: {field} differs from frozen run/session")
        primaries = []
        for run in result["primary_estimates"]:
            labels = site["primary_estimates"][run["id"]]
            primaries.append({"run_id": run["id"], "window_id": run["window"], "calendar_years": run["calendar_years"],
                              "window": labels.get("window"), "outcome": labels.get("outcome"), "sample": labels.get("sample"),
                              "estimator": labels.get("estimator"), "coverage": labels.get("coverage")})
        check(row.get("primary_runs") == primaries, f"{example_id}: primary run metadata differs")


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=ROOT / "scripts/viz_site/data/design_opportunity.json")
    args = parser.parse_args()
    payload = _load(args.path)
    errors = validate(payload)
    for s in payload.get("opportunity_summary", []):
        print(f"{s['family_id']}: {s['strict_dated_jurisdictions']} dated; {s['year_group_count']} year groups; largest {s['modal_count']} ({','.join(map(str, s['modal_years']))})")
    print(json.dumps({"valid": not errors, "strict_dated_total": payload.get("strict_dated_total"), "inventory_rows": len(payload.get("family_jurisdiction", [])), "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
