"""Offline V9 start-evidence replay; never replaces submissions or certifies S1.

The reviewed decisions below identify dates already encoded in the v2 rows.
They are deliberately a partial replay, not a replacement 50-state submission.
Earlier proven paths and binary-relevant definition conflicts still need review.
No current-stock observation is converted to absence or continuous exposure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


BASE = Path("data/l2_pipeline/runs/run7/v62_official_history")
OUTPUT = BASE / "jobs/v9_20261009/rescore"

# Exact existing dates, reviewed against each row's evidence_refs and narrative.
# Legal opening dates are V9 start evidence, not full-year exposure certification.
REVIEWED_STARTS = {
    ("run7_abortion_informed_consent", "A"): {
        "GA": "2005-07-01", "LA": "1995-06-20", "MI": "1994-04-01",
        "MS": "1992-08-05", "TX": "2004-01-01",
        "WV": "2003-05-25",
    },
    ("run7_abortion_informed_consent", "B"): {
        "AR": "2001-05-01", "GA": "2005-07-01", "MN": "2003-07-01",
        "TX": "2004-01-01", "WV": "2003-05-25",
    },
    ("run7_minor_abortion_parental_involvement", "A"): {
        "AL": "1987-09-23", "MI": "1991-03-28",
        "MN": "1981-08-01", "NC": "1995-10-01", "ND": "1981-03-31",
        "SC": "1990-05-29", "TX": "2000-01-01", "VA": "1997-07-01",
        "WV": "1984-05-23", "NH": "2012-01-01",
    },
    ("run7_minor_abortion_parental_involvement", "B"): {
        "AL": "1987-09-23", "MI": "1991-03-28", "NH": "2012-01-01",
        "NC": "1995-10-01", "TX": "2000-01-01",
    },
    ("run7_pharmacy_ec_access", "A"): {
        "CA": "2002-01-01", "HI": "2003-06-24", "ME": "2004-07-30",
        "NH": "2005-10-14", "NM": "2002-12-15",
    },
    ("run7_pharmacy_ec_access", "B"): {
        "CA": "2002-01-01", "HI": "2003-06-24", "ME": "2004-07-30",
        "MA": "2005-12-14", "NH": "2005-10-14", "NM": "2002-12-15",
    },
    ("run7_refundable_eitc", "A"): {},
    ("run7_refundable_eitc", "B"): {"WA": "2022-01-01"},
}

# Prefer the specific dated proposition instead of a row's first citation, which
# may concern signing, repeal, or a later stock observation.
PRIMARY_START_SOURCES = {
    ("run7_abortion_informed_consent", "A", "GA"): "GA_implementation_history_web0",
    ("run7_abortion_informed_consent", "A", "TX"): "TX_enrolled2003",
    ("run7_abortion_informed_consent", "A", "WV"): "WV_signed2003",
    ("run7_abortion_informed_consent", "B", "AR"): "B_AR_0ccec9f3fac63c4b_66",
    ("run7_abortion_informed_consent", "B", "MN"): "B_MN_2eb392583a9a1d83_27",
    ("run7_abortion_informed_consent", "B", "WV"): "B_WV_16c4200102ff2c86_141",
    ("run7_minor_abortion_parental_involvement", "A", "NH"): "NH_ab9ab6f36feb",
    ("run7_pharmacy_ec_access", "A", "CA"): "CA_2002script",
    ("run7_pharmacy_ec_access", "A", "NM"): "NM_2002register",
    ("run7_pharmacy_ec_access", "B", "CA"): "CA_script2002",
    ("run7_pharmacy_ec_access", "B", "MA"): "MA_board2006",
    ("run7_refundable_eitc", "B", "WA"): "WA_history",
}

EXCLUDED_START_REASONS = {
    "run7_abortion_informed_consent": {
        "AK": "AS18.16.060/SB30 opening versus specifically identified earlier 12AAC40.070 abortion-disclosure obligation; registered generic-consent boundary requires reconciliation.",
        "KY": "311.725/1998 opening versus 311.726 predecessor created in 1982; predecessor text and predicate are unresolved.",
        "NC": "2011 SL405 opening versus specifically identified earlier 10ANCAC14E.0305 clinic counseling; whether older duty matches registered counseling predicate is unresolved.",
        "HI": "Act35/2006 changes residency/location restrictions; it does not document a qualifying counseling opening.",
        "DE": "Former1794/1979 explanation and24-hour clause versus registered generic-consent distinction; predicate/enforceability unresolved.",
        "NV": "1981 detailed disclosure law and1985 replacement are documented only as approval dates; legal applicability/effective clock is not supplied.",
    },
    "run7_minor_abortion_parental_involvement": {
        "AK": "2010 notice initiative versus identified 1997 consent law/injunction path; earlier qualifying operative spell remains unresolved.",
        "CO": "1998 initiative is paired with an identified original injunction and2003 bypass changes; actual initial operative boundary unresolved.",
        "DE": "Chapter238 covers under16 and permits professional/nonparent route; registered ages15–17 parental requirement definition is ambiguous.",
        "IA": "Chapter1011 allows notice to grandparent/aunt/uncle; exact inclusion under parental notification/consent predicate needs ruling.",
        "IL": "2013 Illinois Supreme Court expressly says1995 Act had never been enforced;1983/1984 legal dates and2009 administrative instruction cannot settle actual first operative year.",
        "KY": "Section311.732/1982 opening versus identified predecessor311.740/1974 repeal andEubanks litigation; earlier qualifying operative spell unresolved (A/B hub review).",
        "LA": "1973 blanket consent and1978 minor-specific provision precede1981 amendment; a later amendment is not initial opening.",
        "MT": "2013 consent law was specifically enjoined;January2013 notice and earlier1995 law are distinct paths, not a July2013 qualifying start.",
        "PA": "1994 stay-of-mandate denial versus earlier1974/1982 consent paths; denial is not a dated initial statutory opening.",
        "SC": "B specifically raises under17/nonparent alternatives andearly challenge; approval alone does not provide applicability clock or settle predicate.",
        "MS": "1986 law and1993 appellate opinion are distinct from original injunction/mandate start; appellate date is not an operative opening.",
        "NJ": "2000 order rescinds bypass rules afterFarmer invalidation; rescission does not date a qualifying operative start.",
    },
    "run7_pharmacy_ec_access": {
        "MD": "2018/2019 COMAR hormonal-contraception route is not expressly EC-specific in the coded event; applicability to registered EC predicate unresolved.",
        "VA": "2021 protocol is outside1974–2014 window andcovers18+ only; it cannot settle registered ages15–19 EC authority definition.",
        "WA": "1979/1980 broad CDTA prescriptive authority versus EC-specific program and2007 prescription-delivery rule are different routes; whether broad authority meets EC predicate requires ruling.",
        "VT": "2015 repeal/2023 protocol are later boundaries; original EC authorization is not dated by those events.",
        "OK": "January2014 judicial invalidation concerns17+/OTC-linked restriction; no qualifying EC-prescriptive opening is dated.",
    },
    "run7_refundable_eitc": {
        "VT": "A explicitly conflicts on applicableTY1988 versus1989 andJFO1980 percentage narrative; legal effectivenessJune16,1988 is not an applicable-tax-year clause. B1988 retained separately.",
        "ME": "B identifies earlier refundable1996/2000 predecessor versus2016 revival; original A2016 is retained with its uncertainty, not newly endorsed.",
    },
}


def candidate_dates(row: dict) -> list[dict]:
    """Inventory all encoded law/event dates; exclude requested coverage bounds."""
    candidates = []

    def walk(value: Any, pointer: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{pointer}/{key}"
                if key in {"operative_date", "effective_date", "implementation_date", "enactment_date", "date"} and isinstance(child, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", child):
                    candidates.append({"pointer": path, "date": child,
                                       "kind": value.get("kind", value.get("event", key)),
                                       "description": value.get("description", value.get("law"))})
                elif key == "start_period" and isinstance(child, int):
                    candidates.append({"pointer": path, "year": child, "kind": "existing_start_period"})
                elif key not in {"operative_intervals", "unknown_intervals", "history_coverage", "current_stock", "evidence_refs", "date_bounds"}:
                    walk(child, path)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{pointer}/{i}")

    walk(row)
    return candidates


def occurrences(value: Any, expected: Any, pointer: str = "") -> list[str]:
    """Return exact JSON pointers; narrative years cannot manufacture dates."""
    if value == expected:
        return [pointer or "/"]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in occurrences(v, expected, f"{pointer}/{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in occurrences(v, expected, f"{pointer}/{i}")]
    return []


def bound_refs(row: dict, sources: list[dict], shared_refs: list[dict] = ()) -> list[dict]:
    """Require a nonempty quoted reference bound to the original source hash."""
    registry = {s.get("source_id", s.get("id")): s for s in sources}
    shared = {r.get("id", r.get("evidence_ref_id")): r for r in shared_refs}
    refs = []
    for ref in row.get("evidence_refs", []):
        if isinstance(ref, str):
            ref = shared.get(ref, {})
        source = registry.get(ref.get("source_id"))
        quoted = ref.get("quote") or ref.get("quoted_text") or ref.get("quoted_spans")
        digest = ref.get("source_sha256", ref.get("sha256"))
        if not source or not quoted or not digest or digest != source.get("sha256"):
            continue
        refs.append(ref)
    return refs


def rescore_submission(data: dict, reviewed_starts: dict[str, str]) -> dict:
    records = []
    before = sum(r.get("status") in {"treated", "not_yet_treated", "never_treated"}
                 for r in data["records"])
    for row in data["records"]:
        unit = row["unit"]
        refs = bound_refs(row, data.get("sources", []), data.get("evidence_refs", []))
        old_known = row.get("status") in {"treated", "not_yet_treated", "never_treated"}
        date = reviewed_starts.get(unit)
        pointers = occurrences(row, date) if date else []
        if date and (not pointers or not refs):
            raise ValueError(f"{unit}: reviewed date has no original literal or hash-bound quoted source")
        if unit == "WA" and data["design_id"] == "run7_refundable_eitc" and date:
            quoted = json.dumps(refs, ensure_ascii=False)
            if "tax year 2022" not in quoted:
                raise ValueError("WA: original official quote must confirm applicable tax year 2022")
        record = {
            "unit": unit,
            "original_status": row.get("status"),
            "original_start_period": row.get("start_period"),
            "start_period": row.get("start_period"),
            "evidence_status": "retained_existing_known" if old_known else "needs_recollection",
            "best_evidence_start_year": row.get("start_period") if old_known else None,
            "confidence": "high" if old_known and row.get("date_certified") else "medium" if old_known else None,
            "source_id": refs[0]["source_id"] if refs else None,
            "source_ids": sorted({ref["source_id"] for ref in refs}),
            "quoted_evidence_refs": refs,
            "original_reason": row.get("reason"),
            "original_blocker": row.get("blocker"),
            "original_scope_conflicts": row.get("scope_conflicts", []),
            "original_clock_conflicts": row.get("clock_conflicts", row.get("timing_conflicts", [])),
            "original_events": {k: row[k] for k in (
                "historical_events", "judicial_events", "authority_events", "documented_legal_events",
                "documented_dates", "policy_changes", "operative_intervals", "unknown_intervals"
            ) if k in row},
            "s1_ready": False,
        }
        if date:
            primary = PRIMARY_START_SOURCES.get((data["design_id"], data["role"], unit))
            if primary and primary not in record["source_ids"]:
                raise ValueError(f"{unit}: specific dated primary reference is not hash-bound")
            record.update({
                "evidence_status": "known_start",
                "best_evidence_start_year": int(date[:4]),
                "original_selected_date": date,
                "original_date_pointers": pointers,
                "source_id": primary or record["source_id"],
                "confidence": "medium",
                "review_basis": "V9 accepts the official dated opening already documented in this row; missing negative proof and speculative interruptions do not defeat start evidence. Applicability supersedes an earlier legal effective date when explicitly dated. Original found events are retained separately.",
            })
            if unit == "WA" and data["design_id"] == "run7_refundable_eitc":
                record["start_period"] = 2022
                record["confidence"] = "high"
                record["review_basis"] = "Binding C9 WA ruling: refundable WFTC linked to federal EITC, including ITIN filers, qualifies; first applicable tax year is TY2022, explicitly confirmed by original official quote. Operational launch in 2023 is separate."
        review = []
        for candidate in candidate_dates(row):
            selected = candidate.get("date") == date or old_known and candidate.get("year") == row.get("start_period")
            if selected:
                reason = "Existing reviewed official opening/date; accepted for start-year evidence only."
            elif date:
                reason = "Separate signing, amendment, interruption or later implementation clock; selected legal/applicability opening is preserved above."
            elif old_known:
                reason = "Existing accepted start retained; this separate date does not replace its original clock."
            else:
                reason = EXCLUDED_START_REASONS.get(data["design_id"], {}).get(unit)
                if not reason:
                    reason = ("Original date is signing/approval or a named later judicial/amendment event; no encoded qualifying opening/applicability date with bound quoted evidence is established. Event: "
                              + str(candidate.get("kind")) + "; original source finding retained in original_reason.")
            review.append({**candidate, "selected_for_start_year": selected, "decision_reason": reason})
        record["encoded_date_candidate_reviews"] = review
        record["start_evidence_refs"] = [ref for ref in refs if ref.get("source_id") == record["source_id"]] if date else []
        if not date and not old_known and review and unit in EXCLUDED_START_REASONS.get(data["design_id"], {}):
            detail = EXCLUDED_START_REASONS[data["design_id"]][unit]
            record["named_candidate_conflict"] = {
                "kind": "policy_definition_ambiguous",
                "detail": detail,
                "binary_relevant_fact": "Does the specifically identified earlier/different route satisfy the registered binary policy predicate, changing its first start year?",
                "source_ids": record["source_ids"],
            }
            record["disposition"] = "NEEDS_RULING"
            record["question"] = "Does the specifically identified route/version in the candidate decision_reason satisfy this card's locked policy definition, and what is the earliest qualifying operative year?"
            if data["design_id"] == "run7_abortion_informed_consent" and unit == "KY":
                record["question"] = "Does former KRS311.726, created in1982 and repealed in1998, mandate the same abortion-specific information as the locked counseling predicate, or is KRS311.725's1998 opening the first qualifying route?"
                record["named_candidate_conflict"]["binary_relevant_fact"] = "Earlier KRS311.726 qualifies versus first qualifying KRS311.725 start in1998."
            if data["design_id"] == "run7_abortion_informed_consent" and unit == "NC":
                record["question"] = "Does earlier10ANCAC14E.0305 clinic counseling satisfy the locked abortion-specific counseling predicate, or is it excluded generic consent so SL2011-405 is the first qualifying opening?"
                record["named_candidate_conflict"]["binary_relevant_fact"] = "Earlier clinic obligation qualifies versus first qualifying2011 statutory start."
        records.append(record)
    after = sum(r["evidence_status"] in {"known_start", "retained_existing_known"} for r in records)
    return {
        "schema": "v9_start_evidence_replay_v1",
        "design_id": data["design_id"], "role": data["role"],
        "partial_diagnostic": True, "valid_replacement_submission": False,
        "s1_ready": False, "execution_authorized": False,
        "summary": {"states": len(records), "known_before": before, "known_after": after,
                    "states_becoming_known": after - before,
                    "evidence_status_counts": dict(Counter(r["evidence_status"] for r in records))},
        "notes": [
            "This replay preserves source rows and does not certify uninterrupted exposure or any new first_wholly_exposed_year.",
            "Remaining needs_recollection is an offline diagnostic, not a V9 unknown assertion. New A/B jobs must supply starts, absent(V2_LIST), or named binary-relevant conflicts.",
            "No unknown row is recoded as absence; all three V2_LIST checks would be required.",
            "Existing treated rows are retained, including their original conflict notes; retention is not a new reconciliation or S1 endorsement.",
            "Blind original submissions remain unchanged; this replay is a hub review that sees both A/B inputs.",
        ],
        "records": records,
    }


def run(input_root: Path, output_root: Path) -> dict:
    if input_root.resolve() == output_root.resolve() or input_root.resolve() in output_root.resolve().parents:
        raise ValueError("Replay output must not be inside original work inputs")
    output_root.mkdir(parents=True, exist_ok=True)
    reports = []
    original_hashes = {}
    inventory = {}
    for path in sorted(input_root.glob("*/v2/agents/*/submission.json")):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        key = (data["design_id"], data["role"])
        if key not in REVIEWED_STARTS:
            continue
        original_hashes[str(path)] = digest
        report = rescore_submission(data, REVIEWED_STARTS[key])
        report["original_submission"] = str(path)
        report["original_submission_sha256"] = digest
        destination = output_root / data["design_id"] / data["role"] / "START_EVIDENCE_REPLAY.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        reports.append({"design_id": key[0], "role": key[1], "path": str(destination), **report["summary"]})
        used_ids = {sid for row in report["records"] if row["evidence_status"] == "known_start" for sid in row["source_ids"]}
        for source in data.get("sources", []):
            sid = source.get("source_id", source.get("id"))
            if sid in used_ids:
                inventory[(str(path), sid)] = {
                    "original_submission": str(path), "source_id": sid,
                    "url": source.get("url"), "size_bytes": source.get("size_bytes"),
                    "license": source.get("license", "unverified"), "sha256": source.get("sha256"),
                    "new_download": False,
                }
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in original_hashes.items()):
        raise ValueError("An original submission changed during the replay")
    summary = {
        "schema": "v9_start_evidence_replay_summary_v1", "reports": reports,
        "states_becoming_known_across_submissions": sum(r["states_becoming_known"] for r in reports),
        "new_downloads": [], "new_download_bytes": 0,
        "used_inherited_source_inventory": list(inventory.values()),
        "original_submission_hashes_verified": original_hashes,
        "s1_ready": False, "dispatch_count": 0,
    }
    pairs = {}
    for item in reports:
        replay = json.loads(Path(item["path"]).read_text())
        known = {r["unit"]: r["best_evidence_start_year"] for r in replay["records"]
                 if r["evidence_status"] in {"known_start", "retained_existing_known"}}
        pairs.setdefault(item["design_id"], {})[item["role"]] = known
    summary["paired_start_evidence"] = {
        design: {"same_year_known_in_both": sorted(u for u in pair.get("A", {})
                                                if pair.get("B", {}).get(u) == pair["A"][u]),
                 "known_in_either": sorted(set(pair.get("A", {})) | set(pair.get("B", {})))}
        for design, pair in pairs.items()
    }
    new_starts = {}
    for item in reports:
        replay = json.loads(Path(item["path"]).read_text())
        new_starts.setdefault(item["design_id"], {})[item["role"]] = {
            r["unit"]: r["best_evidence_start_year"] for r in replay["records"] if r["evidence_status"] == "known_start"
        }
    summary["paired_new_start_counts"] = {
        design: {"A": len(pair.get("A", {})), "B": len(pair.get("B", {})),
                 "union": len(set(pair.get("A", {})) | set(pair.get("B", {}))),
                 "same_year_intersection": sum(pair.get("B", {}).get(u) == year for u, year in pair.get("A", {}).items())}
        for design, pair in new_starts.items()
    }
    wa_rows = []
    for item in reports:
        if item["design_id"] != "run7_refundable_eitc":
            continue
        replay = json.loads(Path(item["path"]).read_text())
        wa = next(r for r in replay["records"] if r["unit"] == "WA")
        wa_rows.append({"role": item["role"], "original_status": wa["original_status"],
                        "original_start_period": wa["original_start_period"],
                        "replay_evidence_status": wa["evidence_status"],
                        "best_evidence_start_year": wa["best_evidence_start_year"],
                        "quoted_evidence_refs": wa["quoted_evidence_refs"],
                        "original_submission": replay["original_submission"],
                        "original_submission_sha256": replay["original_submission_sha256"]})
    (output_root / "WA_RULING.json").write_text(json.dumps({
        "ruling_id": "C9_WA_20261009", "authority": "User-supplied binding hub ruling",
        "decision": "Working Families Tax Credit counts as a state refundable EITC: federal-EITC-linked eligibility, including ITIN filers.",
        "start_period": 2022, "clock": "first_applicable_tax_year", "confidence": "high",
        "implementation_launch_separate": "2023-02-01", "source_id": "WA_history",
        "rows": wa_rows, "s1_ready": False,
        "scope": "Definition ruling plus existing official applicable-year evidence; other S1 checks still required.",
    }, ensure_ascii=False, indent=2) + "\n")
    (output_root / "RESCORE_REPORT.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=BASE / "work")
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = run(args.input_root, args.output_root)
    print(json.dumps({"reports": report["reports"], "states_becoming_known_across_submissions": report["states_becoming_known_across_submissions"]}, indent=2))


if __name__ == "__main__":
    main()
