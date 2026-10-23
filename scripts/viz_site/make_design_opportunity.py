#!/usr/bin/env python3
"""Build the dated, verified eight-family inventory without touching its inputs.

Only structured dossier dates are copied. Notes are retained as evidence, never
parsed into additional clocks or comparison support. No network is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "scripts/viz_site/data"
CUTOFF = "2025-12-31"
JURISDICTIONS = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming"],
))
# Labels describe the recorded clock, not a study's exposure definition.
CLOCKS = {
    "license_recognition": ("Signed", "签署"),
    "nurse_licensure_compact": ("Signed", "签署"),
    "medical_licensure_compact": ("Signed", "签署"),
    "tnc_framework": ("Signed", "签署"),
    "paid_sick_leave": ("First covered-employer mandate effective", "首个适用雇主强制规定生效"),
    "paid_family_leave": ("Benefits payable", "福利开始支付"),
    "salary_history_ban": ("Private-employer ban effective", "私营雇主禁问规定生效"),
    "min_wage_15": ("Enacted", "制定"),
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_entry(row: dict, cutoff: str = CUTOFF) -> bool:
    year = row.get("year")
    if row.get("verification") not in {"verified", "fixed"}:
        return False
    if type(year) is not int or year < 2004 or year > int(cutoff[:4]):
        return False
    recorded_date = row.get("date")
    return recorded_date is None or date.fromisoformat(recorded_date) <= date.fromisoformat(cutoff)


def make_run_evidence(root: Path) -> list[dict]:
    results = root / "scripts/did_samples/results"
    records = []
    for example_id in ("paid_sick_leave", "medicaid_expansion", "castle_doctrine"):
        result_path = results / f"{example_id}.json"
        sidecar_path = results / f"{example_id}_site.json"
        result, sidecar = read_json(result_path), read_json(sidecar_path)
        result_hash = sha256(result_path)
        if sidecar["result_sha256"] != result_hash:
            raise ValueError(f"{example_id}: frozen sidecar/result binding mismatch")
        session_path = results / f"session_{result['session_id']}.json"
        session = read_json(session_path)
        if session.get("session_id") != result["session_id"] or example_id not in session.get("registered_example_ids", []):
            raise ValueError(f"{example_id}: completed session does not contain example")
        freeze = session.get("freeze_verification", {})
        if freeze.get("verified") is not True or freeze.get("issues") != []:
            raise ValueError(f"{example_id}: completed session freeze verification failed")
        if set(session.get("registered_example_ids", [])) != {"paid_sick_leave", "medicaid_expansion", "castle_doctrine"} or set(session.get("results", {})) != {"paid_sick_leave", "medicaid_expansion", "castle_doctrine"}:
            raise ValueError(f"{example_id}: completed session inventory mismatch")
        original_hash = session["results"][example_id]
        archive_path = results / "full_archive" / f"{example_id}.json.gz"
        if original_hash != result_hash:
            if hashlib.sha256(gzip.decompress(archive_path.read_bytes())).hexdigest() != original_hash:
                raise ValueError(f"{example_id}: original session archive binding mismatch")
        display = sidecar["site"]["public_display"]
        primary_runs = []
        for run in result["primary_estimates"]:
            labels = sidecar["primary_estimates"][run["id"]]
            primary_runs.append({
                "run_id": run["id"], "window_id": run["window"],
                "calendar_years": run["calendar_years"], "window": labels.get("window"),
                "outcome": labels.get("outcome"), "sample": labels.get("sample"),
                "estimator": labels.get("estimator"), "coverage": labels.get("coverage"),
            })
        records.append({
            "example_id": example_id, "label": sidecar["site"]["policy"],
            "status": result["status"], "stop_reason": display.get("stop_reason"),
            "stop_reason_code": display.get("stop_reason_code"),
            "link": {lang: f"/viz/{lang}/worked_examples.html" + (f"#{example_id}-heading" if example_id != "castle_doctrine" else "") for lang in ("en", "zh")},
            "primary_runs": primary_runs, "curated_family_id": example_id if example_id == "paid_sick_leave" else None,
            "result_sha256": result_hash, "site_sha256": sha256(sidecar_path),
            "result_path": result_path.relative_to(root).as_posix(),
            "site_path": sidecar_path.relative_to(root).as_posix(),
            "session_id": result["session_id"], "session_sha256": sha256(session_path),
            "session_path": session_path.relative_to(root).as_posix(),
            "original_session_result_sha256": original_hash,
            "original_archive_path": archive_path.relative_to(root).as_posix() if original_hash != result_hash else None,
        })
    return records


def build_payload(root: Path = ROOT, outcome_inventory: dict | None = None) -> dict:
    data = root / "scripts/viz_site/data"
    curated = read_json(data / "curated_adoption.json")
    if curated["as_of"] != CUTOFF:
        raise ValueError("Curated cutoff does not match the named frame")
    if outcome_inventory is None:
        outcome_inventory = read_json(data / "outcome_inventory.json")
    families, inventory, summaries, provenance = [], [], [], []
    for policy in curated["policies"]:
        family_id = policy["id"]
        final_path = data / "curated_raw" / f"{family_id}.final.json"
        verify_path = data / "curated_raw" / f"{family_id}.verify.json"
        final, verify = read_json(final_path), read_json(verify_path)
        if final["as_of"] != CUTOFF or verify["as_of"] != CUTOFF:
            raise ValueError(f"{family_id}: inconsistent cutoff")
        if set(final["states"]) != set(JURISDICTIONS):
            raise ValueError(f"{family_id}: expected all 51 jurisdictions")
        clock_en, clock_zh = CLOCKS[family_id]
        families.append({
            "id": family_id, "label_en": policy.get("display_name_en", policy["name_en"]),
            "label_zh": policy.get("display_name_zh", policy["name_zh"]),
            "short_en": policy["short_en"], "short_zh": policy["short_zh"],
            "definition_en": policy["definition_en"], "definition_zh": policy["definition_zh"],
            "domain_en": policy.get("labor_topic_en"), "domain_zh": policy.get("labor_topic_zh"),
            "cutoff": CUTOFF, "clock_en": clock_en, "clock_zh": clock_zh,
            "date_basis_en": policy["year_basis_en"], "date_basis_zh": policy["year_basis_zh"],
        })
        groups = defaultdict(list)
        statuses = Counter()
        for jurisdiction in sorted(JURISDICTIONS):
            raw = final["states"][jurisdiction]
            detail = verify.get("details", {}).get(jurisdiction)
            excluded = policy.get("excluded", {}).get(jurisdiction)
            included = strict_entry(raw) and excluded is None
            if excluded:
                status = "removed" if detail and detail.get("status") == "REMOVED" else "disputed"
            elif raw["verification"] == "single_source":
                status = "withheld_single_source"
            elif included:
                status = "strict_dated"
            elif raw["year"] is None:
                status = "undated"
            else:
                status = "outside_cutoff_or_unresolved"
            statuses[status] += 1
            recorded_date = raw["date"]
            inventory.append({
                "family_id": family_id, "jurisdiction": jurisdiction,
                "jurisdiction_name_en": JURISDICTIONS[jurisdiction],
                "recorded_year": raw["year"], "recorded_date": recorded_date,
                "date_precision": "day" if recorded_date is not None else "year" if raw["year"] is not None else None,
                "verification": raw["verification"], "confidence": raw.get("confidence"),
                "included_strict": included, "inventory_status": status,
                "sources": raw["sources"], "citation": None,
                "scope_note": raw.get("note") or None,
                "signing_date": recorded_date if clock_en == "Signed" else None,
                "effective_date": recorded_date if family_id in {"paid_sick_leave", "salary_history_ban"} else None,
                "operative_date": recorded_date if family_id in {"paid_sick_leave", "paid_family_leave"} else None,
                "enacted_date": recorded_date if clock_en == "Enacted" else None,
                "policy_status_basis": final["year_basis"], "exclusion_reason": excluded,
                "verification_detail": detail,
            })
            if included:
                groups[raw["year"]].append(jurisdiction)
        sizes = [{"year": year, "count": len(states), "jurisdictions": states} for year, states in sorted(groups.items())]
        n = sum(len(states) for states in groups.values())
        modal = max((len(states) for states in groups.values()), default=None)
        summaries.append({
            "family_id": family_id, "strict_dated_jurisdictions": n,
            "year_group_count": len(groups), "year_min": min(groups) if groups else None,
            "year_max": max(groups) if groups else None, "year_group_sizes": sizes,
            "modal_count": modal, "modal_share": modal / n if n else None,
            "modal_years": [year for year, states in sorted(groups.items()) if len(states) == modal],
            "status_accounting": dict(sorted(statuses.items())),
            "verified_nonoperative_at_cutoff": None, "eligible_comparison_count": None,
        })
        for path in (final_path, verify_path):
            provenance.append({"path": path.relative_to(root).as_posix(), "sha256": sha256(path)})
    provenance.insert(0, {"path": (data / "curated_adoption.json").relative_to(root).as_posix(), "sha256": sha256(data / "curated_adoption.json")})
    return {"schema_version": 1, "cutoff": CUTOFF, "year_columns": list(range(2004, 2026)),
            "strict_dated_total": sum(s["strict_dated_jurisdictions"] for s in summaries),
            "family": families, "family_jurisdiction": inventory, "opportunity_summary": summaries,
            "panel_support": None, "outcome_inventory": outcome_inventory["records"],
            "run_evidence": make_run_evidence(root), "source_provenance": provenance}


def serialized(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check deterministic bytes without writing")
    args = parser.parse_args()
    destination = DATA / "design_opportunity.json"
    content = serialized(build_payload())
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != content:
            print("design_opportunity.json is stale")
            return 1
        print("design_opportunity.json: deterministic bytes match")
    else:
        destination.write_text(content, encoding="utf-8")
        print(f"Wrote {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
