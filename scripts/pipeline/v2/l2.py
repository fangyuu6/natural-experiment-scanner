"""Retroactive L2 lean projections. Reads locked pilot submissions; never fetches data.

Comparisons are named statutory exposure components, not certified first-adoption
histories. Explicit JSON paths keep benefit, tax-applicability and signing clocks
apart. A/B projections are computed before existing third-read evidence is added.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

LICENSE = "did-12289964c5c16b8620de"
FAMILY = "did-3ee1dcd04a7f41715f72"
WAGE = "did-88990ee6c1033c449c9c"
EITC = "did-c2e2f141c074e2a2eab4"
RDD = "rdd-88c354e13eddfb55a29c"
LOTTERY = "rct-f4f160db1eb3f29d5af0"
NAMES = {LICENSE: "License recognition", FAMILY: "Paid family leave",
         WAGE: "Prevailing wage", EITC: "EITC", RDD: "WIA population RDD",
         LOTTERY: "H-1B lottery"}
END = "2025-12-31"


def decoded(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def get_path(value, path):
    """A path addresses only its named clock; never searches surrounding prose."""
    for key in path:
        try:
            value = value[key]
        except (KeyError, IndexError, TypeError):
            return None
    return value


def annual(value):
    """Accept a date/year token at the start of an explicitly selected field."""
    if isinstance(value, int) and 1800 <= value <= 2100:
        return value
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        year = int(value[:4])
        if 1800 <= year <= 2100:
            return year
    return None


def full_negative(value):
    """Partial negatives/snapshot omissions do not certify the requested window."""
    return (isinstance(value, dict) and not value.get("partial_certificate", False)
            and value.get("certified_through") == END
            and value.get("complete_window", False) is True)


def evidence(row):
    items = decoded(row.get("evidence_json", "[]"))
    if not isinstance(items, list):
        items = [items]
    for item in items:
        if isinstance(item, dict) and item.get("url") and item.get("locator"):
            return {"url": item["url"], "locator": item["locator"],
                    "source_id": item.get("source_id"),
                    "source_type": item.get("source_type")}
    return None


def cell(row, path=(), transform=annual, reason=None):
    if row is None:
        return {"value": None, "availability": "missing", "reason": reason or "no aligned record"}
    raw = get_path(decoded(row["value"]), path)
    value = transform(raw) if transform else raw
    return {"value": value, "availability": "known" if value is not None else "unknown",
            "row_ref": row["_ref"], "record_id": row["record_id"],
            "version_id": row["version_id"], "field_id": row["field_id"],
            "json_path": list(path), "original_field_status": row["value_status"],
            "selected_raw": raw,
            "extraction_basis": "explicit positive scoped component; whole-field status retained" if value is not None and row["value_status"] != "known" else "original known component" if value is not None else "unknown analysis value",
            "source": evidence(row), "reason": reason}


def compare(a, b):
    if a["value"] is not None and b["value"] is not None:
        return "agree" if a["value"] == b["value"] else "dispute"
    if "missing" in (a["availability"], b["availability"]):
        return "missing_counterpart"
    if a["value"] is None and b["value"] is None:
        return "both_unknown"
    return "one_unknown"


class Inputs:
    def __init__(self, root):
        self.root = root
        self.paths = []
        self.records = {}
        csv.field_size_limit(10**7)
        for design in NAMES:
            for coder in "AB":
                path = root / f"data/pipeline/coding/{coder}/{design}.csv"
                self.paths.append(path)
                with path.open() as f:
                    rows = list(csv.DictReader(f))
                for line, row in enumerate(rows, 2):
                    row["_ref"] = f"{path.relative_to(root)}:{line}"
                self.records[design, coder] = rows
        cross = root / "data/pipeline/coding/reconciled/crosswalk.csv"
        self.paths.append(cross)
        with cross.open() as f:
            self.crosswalk = list(csv.DictReader(f))
        path = root / "data/pipeline/coding/reconciled/adjudication_batch1.csv"
        self.paths.append(path)
        self.rulings = {}
        with path.open() as f:
            for line, row in enumerate(csv.DictReader(f), 2):
                row["_ref"] = f"{path.relative_to(root)}:{line}"
                self.rulings[row["ruling_id"]] = row

    def find(self, design, coder, record=None, unit=None, field=None):
        matches = [r for r in self.records[design, coder]
                   if (record is None or r["record_id"] == record)
                   and (unit is None or r["unit_id"] == unit)
                   and (field is None or r["field_id"] == field)]
        return matches[0] if matches else None

    def baseline(self, design, coder, unit, field):
        refs = [x[f"{coder}_row_ref"] for x in self.crosswalk
                if x["design_id"] == design and x["unit_id"] == unit
                and x["field_id"] == field and x["alignment_basis"] == "assigned_baseline"]
        return next((r for r in self.records[design, coder] if r["_ref"] in refs), None)

    def linked(self, a, b):
        refs = {v.get("row_ref") for v in (a, b)}
        return [x["comparison_id"] for x in self.crosswalk
                if x["A_row_ref"] in refs or x["B_row_ref"] in refs]


def projected_row(inputs, design, unit, component, a, b, diagnostic=False):
    return {"design_id": design, "unit": unit, "component": component,
            "analysis_granularity": "year" if design.startswith("did") else "design",
            "A": a, "B": b, "initial_result": compare(a, b),
            "component_only": True, "diagnostic_only": diagnostic,
            "crosswalk_ids": inputs.linked(a, b), "third_read": None}


def did_projections(inputs):
    rows = []
    # Scope conflicts are substantive. They are retained even if annual dates match.
    scope_conflicts = {"NV": ("2023 EO oversight/recommendations", "2015 AB89 military reciprocity"),
                       "NJ": ("2018 endorsement amendment", "2013 endorsement law"),
                       "FL": ("2024 endorsement law", "2020 deregulation/endorsement candidate"),
                       "SD": ("2025 chapter150 amendment", "2021 HB1077 recognition law")}
    for design in (LICENSE, FAMILY, EITC):
        units = sorted({r["unit_id"] for r in inputs.records[design, "A"]
                        if len(r["unit_id"]) == 2})
        for unit in units:
            field = "effective_date" if design == LICENSE else "treatment_start_date"
            ar = inputs.baseline(design, "A", unit, field)
            br = inputs.baseline(design, "B", unit, field)
            if design == LICENSE:
                if unit == "MO":
                    # Existing baseline alignment mixes2025 with2018; align the named SB840.
                    ar = inputs.find(design, "A", record="A-LIC-MO-R1-effective_date-MO-324009-2018")
                av = decoded(ar["value"]) if ar else None
                # CO core sections are explicit; multi-version histories require scope selection.
                ap = (0, "date") if isinstance(av, list) and (len(av) == 1 or unit == "CO") else ()
                a = cell(ar, ap, reason="core statutory clock; multi-component fields need selection" if not ap else None)
                b = cell(br, ("effective_date",))
                if unit in scope_conflicts:
                    for c, scope in zip((a, b), scope_conflicts[unit]):
                        if c["value"] is not None:
                            c["value"] = {"year": c["value"], "scope": scope}
                component = "named statutory recognition exposure"
            elif design == FAMILY:
                keys = {"CA": "mandatory_private_family_benefits", "CO": "family_and_ownmedical_benefits",
                        "CT": "family_and_ownmedical_benefits", "DC": "family_and_ownmedical_benefits",
                        "MA": "family_bonding_medical_military", "NJ": "mandatory_family_benefits",
                        "NY": "private_family_benefits", "OR": "mandatory_family_medical_safe_benefit_program",
                        "RI": "family_benefits", "WA": "family_medical_benefits"}
                a = cell(ar, (keys.get(unit, "primary_family_benefit_not_recorded"), "start"))
                bp = ("benefit_claim_start_bonding_and_own_serious_health", "date") if unit == "MA" else ("benefit_claim_start", "date")
                b = cell(br, bp)
                # A future schedule is not an observed start inside the 2025 sample.
                for c in (a, b):
                    if c["value"] is not None and c["value"] > 2025:
                        c["scheduled_year"] = c.pop("value")
                        c["value"] = None
                        c["availability"] = "unknown"
                        c["reason"] = "future schedule; no observed benefit exposure through sample end"
                component = "primary private-worker family benefit start"
            else:
                # These baseline rows refer to FIRST credit; reform components below remain separate.
                a = cell(ar, ("first_tax_year",))
                bv = decoded(br["value"]) if br else {}
                key = next((k for k in ("first_tax_year", "first_tax_year_begins", "first_taxable_year", "first_calendar_year") if isinstance(bv, dict) and k in bv), "first_tax_year")
                b = cell(br, (key,))
                component = "first credit tax applicability (compiled table; unaudited)"
            row = projected_row(inputs, design, unit, component, a, b)
            if design == LICENSE and unit in scope_conflicts:
                row["dispute_type"] = "candidate exposure scope/version differs; not opposing claims about one legal date"
            if design == LICENSE and unit == "MO":
                row["alignment_override"] = "same SB840/2018 component; A baseline2025 is separate amendment"
            rows.append(row)

    for aid, bid, unit, component in [
        ("A-LIC-MO-R1-effective_date-MO-324009-2020", "B-MO-2", "MO", "2020 recognition amendment"),
        ("A-LIC-MO-R1-effective_date", None, "MO", "2025 recognition amendment"),
        (None, "B-NV-2", "NV", "2017 SB69 recognition component"),
    ]:
        ar = inputs.find(LICENSE, "A", record=aid) if aid else None
        br = inputs.find(LICENSE, "B", record=bid, field="effective_date") if bid else None
        rows.append(projected_row(inputs, LICENSE, unit, component,
                                  cell(ar, (0, "date")), cell(br, ("effective_date",))))

    # Named prevailing-wage components preserve local/state/funding and phase differences.
    # No first date is taken from a union of legal, solicitation and wage-order clocks.
    wage_specs = [
        ("AR", "AR-Act1068-2017", "date", "I-AR-1", "date"),
        ("KY", "KY-ch3-2017", "date", "I-KY-1", "date"),
        ("CO", "CO-SB19-196-2019", ("clock_components", "solicitation_application", "date"), None, None),
        ("CO", "CO-HB21-1319-2021", ("clock_components", "direct_USDOL_phase_start", "date"), None, None),
        ("WI", "WI-Act55-local-2017", "date", "I-WI-1", "date"),
        ("WI", "WI-Act59-highway-2017", "date", "I-WI-2", "date"),
        ("WV", "WV-HB4005-2016", "exact_calendar_effect", "I-WV-2", "computed_from_2016-02-04"),
        ("IL", "IL-PA100-0002-2017", "date", None, None),
        ("NJ", "NJ-PL2019-ch44", "date", None, None),
        ("NJ", "NJ-PL2023-ch133", ("clock_components", "retroactive_application_start", "date"), None, None),
        ("WA", "WA-ch248-2018", "date", None, None),
        ("MD", "MD-ch58-2021", "date", None, None),
        ("NV", "NV-ch174-2019", "date", "I-NV-1", "date"),
        ("IN", "IN-PL252-2015", "date", "I-IN-1", "year"),
        ("MI", "MI-PA10-2023-implementation2024", "agency_reported", "I-MI-3", "date"),
        ("MO", "MO-HB1729-2018", "agency_record", None, None),
        ("VA", "VA-2021-agency-operation-framework", "agency_announced", "I-VA-1", None),
        ("OH", "OH-411504-effective2011", "date", "I-OH-1", "date"),
        ("OR", "OR-ch104-2021", ("operative_phase", "date"), None, None),
    ]
    for unit, version, ap, bi, bk in wage_specs:
        ar = inputs.find(WAGE, "A", record=f"A-{version}-effective_date")
        br = inputs.baseline(WAGE, "B", unit, "effective_date") if bi else None
        ap = (ap,) if isinstance(ap, str) else ap
        bp = (bi, bk) if bk else (bi,)
        a, b = cell(ar, ap), cell(br, bp)
        if unit == "MO":
            # Field starts with the already-dated agency exposure, not wage-order release.
            a = cell(ar, ("primary_coded_date", "date"))
        if version == "WI-Act59-highway-2017":
            # Highway agency versus state-building source: annual match is only diagnostic.
            row = projected_row(inputs, WAGE, unit, version, a, b, diagnostic=True)
            row["scope_gap"] = "A highway agency scope; B state-building scope; choose target funding/project universe"
        else:
            row = projected_row(inputs, WAGE, unit, version, a, b)
        rows.append(row)

    # EITC statutory reform targets, not the first-adoption table or payment year.
    eitc_specs = [
        ("NJ", "NJ__NJ_2018_CH45_EITC", ("EITC_applicability", "start"), "B-NJ-2", ("37%_rate_tax_year_begins",), "37pct"),
        ("NJ", "NJ__NJ_2018_CH45_EITC", ("schedule", "2019"), "B-NJ-2", ("39%_rate_tax_year_begins",), "39pct"),
        ("NJ", "NJ__NJ_2018_CH45_EITC", ("schedule", "2020_onward"), "B-NJ-2", ("40%_rate_tax_year_begins",), "40pct"),
        ("MD", "MD__MD_2018_CH612_EITC", ("tax_applicability", "start"), "B-MD-2", ("first_tax_year_for_age_change",), "minimum-age eligibility"),
        ("DE", "DE__DE_2021_CH118_DATE_CONFLICT", ("unresolved_applicability",), "B-DE-2", ("first_tax_year_with_refundable_option",), "refundable option"),
        ("WA", "WA__WA_2021_CH195_WFTC", ("remittance_taxes",), "B-WA-1", ("unresolved_tax_applicability",), "WFTC eligible tax year"),
        ("CO", "CO__CO_2024_HB1084_TY2023", ("TY_scope", "lower"), None, (), "50pct TY2023"),
        ("MA", "MA__MA_2015_CH52_EITC", ("EITC_section1", "date"), None, (), "23pct"),
        ("SC", "SC__SC_2017_ACT40_EITC", ("tax_applicability",), "B-SC-1", ("first_tax_year",), "initial phase"),
        ("MI", "MI__MI_2023_PA4_AGENCY_VERIFIED", ("applicability", "tax_year"), "B-MI-3", ("applicability",), "30pct plus TY2022 supplement"),
    ]
    for unit, aid, ap, bid, bp, component in eitc_specs:
        ar = inputs.find(EITC, "A", record=aid, field="effective_date")
        bf = "effective_date" if unit == "MI" else "treatment_start_date"
        br = inputs.find(EITC, "B", record=bid, field=bf) if bid else None
        a, b = cell(ar, ap), cell(br, bp)
        if component in ("39pct", "40pct"):
            # The selected schedule key is the applicability year; its value is a rate.
            expected_rate = 39 if component == "39pct" else 40
            a["value"] = (2019 if component == "39pct" else 2020) if a["selected_raw"] == expected_rate else None
            a["availability"] = "known" if a["value"] is not None else "unknown"
            a["transform"] = "year encoded by selected schedule key; rate is not a date"
        if unit == "MD":
            a["value"] = 2018 if get_path(decoded(ar["value"]), ap) == "after2017-12-31" else None
            a["availability"] = "known" if a["value"] else "unknown"
            a["transform"] = "tax years after 2017-12-31 -> TY2018"
        if unit in ("WA", "SC"):
            expected = "after2022-01-01" if unit == "WA" else "After2017"
            a["value"] = (2022 if unit == "WA" else 2018) if get_path(decoded(ar["value"]), ap) == expected else None
            a["availability"] = "known" if a["value"] else "unknown"
            a["transform"] = "explicit tax applicability boundary"
        if unit == "MI":
            # B records TY2022 supplemental24% separately; total entitlement is30%.
            sr = inputs.find(EITC, "B", record="B-MI-3", field="scope_target_population")
            text = get_path(decoded(sr["value"]), ("generosity",)) if sr else None
            b = cell(sr, ("generosity",), transform=lambda v: 2022 if isinstance(v, str) and "2022 only: additional 24% credit" in v else None)
            b["transform"] = "TY2022 supplemental24% + baseline6% -> full30% entitlement"
        rows.append(projected_row(inputs, EITC, unit, component, a, b))
    return rows


def other_projections(inputs):
    rows = []
    a = cell(inputs.find(RDD, "A", record="FEDERAL_WIA__WIA_1998_ENACTED", field="threshold"), ("value",), transform=lambda v: v if isinstance(v, int) else None)
    b = cell(inputs.find(RDD, "B", record="THR-WIA-a2A", field="threshold"), ("cutoff",), transform=lambda v: v if isinstance(v, int) else None)
    rows.append(projected_row(inputs, RDD, "DESIGN", "requested designation population cutoff >=", a, b))
    ar = inputs.find(RDD, "A", record="FEDERAL_WIA__WIA_1998_ENACTED", field="assignment_versions")
    br = inputs.find(RDD, "B", record="R-DESIGN", field="assignment_versions")
    a = cell(ar, ("version",), transform=lambda v: "WIA1998 section116" if v == "1998enacted§116" else None)
    b = cell(br, (1, "version_id"), transform=lambda v: "WIA1998 section116" if v == "WIA-1998-s116" else None)
    rows.append(projected_row(inputs, RDD, "DESIGN", "assignment rule version (request eligibility)", a, b))
    for field, name in (("running_variable", "operative governor-selected population source/vintage"), ("local_unit_roster", "complete analysis unit list on both cutoff sides")):
        a = cell(inputs.find(RDD, "A", unit="DESIGN", field=field), ("verified_analysis_value",))
        b = cell(inputs.find(RDD, "B", unit="DESIGN", field=field), ("verified_analysis_value",))
        rows.append(projected_row(inputs, RDD, "DESIGN", name, a, b))
    for field, component in (("assignment_records_availability", "FY2025 individual assignment records available"), ("outcome_linkage_availability", "FY2025 actual winner and loser outcomes linkable")):
        # Neither submission proves yes/no; not located does not establish absence.
        a = cell(inputs.find(LOTTERY, "A", unit="FY2025_INITIAL_DISCOVERY", field=field), ("verified_analysis_value",))
        b = cell(inputs.find(LOTTERY, "B", unit="DESIGN", field=field), ("verified_analysis_value",))
        rows.append(projected_row(inputs, LOTTERY, "FY2025", component, a, b))
    return rows


def risk_set_projections(inputs):
    """Carry estimator-required status gaps alongside positive component years."""
    rows = []
    for design in (LICENSE, FAMILY, WAGE, EITC):
        units = sorted({r["unit_id"] for r in inputs.records[design, "A"]
                        if len(r["unit_id"]) == 2})
        for unit in units:
            pair = []
            for coder in "AB":
                row = inputs.baseline(design, coder, unit, "never_treated_certification")
                c = cell(row, transform=lambda v: "never-treated through2025" if full_negative(v) else None)
                # Positive starts do not by themselves prove maintained treatment after repeal.
                c["reason"] = "through-end treated/not-yet/never status is not supplied by a complete analysis table; partial negative rejected"
                pair.append(c)
            result = projected_row(inputs, design, unit, "treated/not-yet/never status through sample end", *pair)
            result["component_only"] = False
            rows.append(result)
    return rows


def add_existing_third_reads(inputs, rows):
    selectors = {
        (LICENSE, "CO"): ("DID-R3-001", ("core_sections_4_5_effective",)),
        (LICENSE, "IA"): ("DID-R3-002", ("recognition_section_26_effective",)),
        (FAMILY, "MA"): ("DID-R3-005", ("benefit_bonding_ownmedical",)),
        (FAMILY, "RI"): ("DID-R3-006", ("TCI_operational_effect",)),
        (FAMILY, "WA"): ("DID-R3-007", ("applications_open",)),
        (FAMILY, "NY"): ("DID-R3-008", ("private_family_benefits_available",)),
        (FAMILY, "CT"): ("DID-R3-009", ("benefits_granted",)),
        (FAMILY, "DC"): ("DID-R3-010", ("benefits_administered",)),
        (FAMILY, "CO"): ("DID-R3-011", ("benefits_available",)),
        (WAGE, "NV"): ("DID-R3-012", ("agency_rate_application",)),
        (WAGE, "MO"): ("DID-R3-013", ("listed_coverage_changes_took_place",)),
        (WAGE, "NJ"): ("DID-R3-014", ("approved_effective",)),
        (EITC, "MD"): ("DID-R3-016", ("applicability",)),
        (EITC, "WA"): ("DID-R3-018", ("eligible_tax_year",)),
    }
    for row in rows:
        if not row["component_only"]:
            continue
        key = row["design_id"], row["unit"]
        if key not in selectors:
            continue
        # Rulings apply only to the named matching component, not another reform.
        if key == (WAGE, "NJ") and row["component"] != "NJ-PL2019-ch44":
            continue
        if row["component"].startswith("first credit"):
            continue
        rid, path = selectors[key]
        ruling = inputs.rulings[rid]
        raw = get_path(decoded(ruling["ruling_value_json"]), path)
        value = annual(raw)
        if key == (EITC, "MD") and raw == "tax years beginning after2017-12-31":
            value = 2018
        row["third_read"] = {"value": value, "ruling_id": rid,
                             "row_ref": ruling["_ref"], "json_path": list(path),
                             "evidence": decoded(ruling["evidence_json"]),
                             "whole_field_resolved": False,
                             "changes_blind_agreement": False}


def run(root: Path, out: Path) -> dict:
    inputs = Inputs(root)
    rows = did_projections(inputs) + other_projections(inputs) + risk_set_projections(inputs)
    add_existing_third_reads(inputs, rows)
    blockers = {
        LICENSE: ["Choose one recognition scope/version; NV/NJ/FL/SD original targets differ", "Complete estimator-specific treatment/status panel through2025; existing negatives stop2023", "Link the chosen outcome and establish cohort/control support"],
        FAMILY: ["Fill primary benefit statuses for the sample window; partial snapshots do not reach2025 year-end", "Define workplace/residence linkage for chosen outcome", "Fetch/link outcome panel and freeze spec"],
        WAGE: ["Choose project/funding universe; WI highway vs state-building evidence is diagnostic only", "Fill application/repeal status panel for that universe", "Link project/worker outcome data and freeze spec"],
        EITC: ["Choose first adoption versus named generosity/eligibility reform", "Resolve DE refundable-option TY2021/2022 and audit compiled first-credit years/controls", "Build chosen tax-year status panel and link outcome data"],
        RDD: ["Obtain governor-selected population source/vintage and boundary values", "Complete requested/approved/refused eligible unit list on both cutoff sides", "Link outcomes; choose eligibility/fuzzy assignment estimand"],
        LOTTERY: ["FY2025 beneficiary winner/loser roster and actual outcomes are unlinked", "FY2021–2024 release metadata exists; archives/round coverage require data acquisition audit", "Any cohort change requires its own frozen estimand"],
    }
    designs = []
    for design, name in NAMES.items():
        selected = [r for r in rows if r["design_id"] == design]
        actual = [r for r in selected if not r["diagnostic_only"]]
        counts = Counter(r["initial_result"] for r in actual)
        disputes = sorted({r["unit"] for r in actual if r["initial_result"] == "dispute"})
        sources = sum(r["A"]["value"] is not None and r["A"].get("source") is not None for r in actual)
        known_cells = [r[c] for r in actual for c in "AB" if r[c]["value"] is not None]
        universe = 51 if design.startswith("did") else 1
        designs.append({"design_id": design, "name": name,
                        "initial_known_agree": counts["agree"],
                        "initial_known_pair_denominator": counts["agree"] + counts["dispute"],
                        "initial_known_disputes": counts["dispute"],
                        "same_component_annual_date_disputes": sum(r["initial_result"] == "dispute" and not r.get("dispute_type") for r in actual),
                        "scope_version_disputes": sum(r["initial_result"] == "dispute" and bool(r.get("dispute_type")) for r in actual),
                        "disputed_units": disputes, "unknown_missing": dict(counts),
                        "diagnostic_only_rows": len(selected) - len(actual),
                        "known_A_components_with_source_locator": sources,
                        "known_analysis_cells": len(known_cells),
                        "known_cells_with_source_locator": sum(c.get("source") is not None for c in known_cells),
                        "whole_field_unknown_but_recorded_positive_components": sum(c.get("original_field_status") != "known" for c in known_cells),
                        "through_end_status_known_pairs": sum(r["initial_result"] in ("agree", "dispute") for r in actual if not r["component_only"]),
                        "third_read_known_components": sum(r["third_read"] is not None and r["third_read"]["value"] is not None for r in actual),
                        "source_ready": False, "full_data_ready": False,
                        "small_conflict_read_by_count": bool(disputes) and len(disputes) <= 5 and len(disputes) <= .1 * universe,
                        "ready_after_small_third_read": False,
                        "small_read_minutes_verified": None,
                        "readiness_blockers": blockers[design]})
    totals = {"known_agree": sum(d["initial_known_agree"] for d in designs),
              "known_pair_denominator": sum(d["initial_known_pair_denominator"] for d in designs),
              "known_disputes": sum(d["initial_known_disputes"] for d in designs),
              "source_ready_designs": 0, "full_data_ready_designs": 0,
              "ready_after_small_third_read_designs": 0}
    for outcome in ("both_unknown", "one_unknown", "missing_counterpart"):
        totals[outcome] = sum(d["unknown_missing"].get(outcome, 0) for d in designs)
    totals["analysis_comparisons"] = (totals["known_pair_denominator"]
        + sum(totals[k] for k in ("both_unknown", "one_unknown", "missing_counterpart")))
    totals["raw_agree_including_unknown"] = totals["known_agree"] + totals["both_unknown"]
    summary = {"schema_version": 2, "sample_end": END,
               "comparison_scope": "original blind A/B named analysis components; annual statutory exposure, family primary benefit, EITC tax applicability; diagnostic scope mismatch excluded",
               "denominator_note": "Known pairs exclude shared unknowns, missing counterparts and diagnostic-only year matches; component counts are not six complete panels.",
               "scoped_component_note": "Explicit positive date/version components with a source+locator can be compared while their v1 whole-history field remains unknown; retained original statuses and selected_raw make this distinction inspectable. Agreement measures recorded values, not new official-source validation.",
               "risk_set_note": "No accepted sampled-zero convention/audit receipt covers the L2 window; partial negatives are retained unknown, including2023/Nov2025 snapshots.",
               "small_third_read_definition": "<=5 conflicting known units AND <=10% universe AND <=30min verified; no major missing data. Count-only candidates do not confer readiness.",
               "blindness_note": "Retrospective revealed recoding cannot establish new blinded reliability; original A/B lineage preserved; third reads never enter initial agreement.",
               "downloads": [], "new_web_coding": False, "outcome_association_calls": 0,
               "lottery_available_metadata": {"cohorts": ["FY2021", "FY2022", "FY2023", "FY2024"], "source": "existing H1B-R4-PUBLIC-FOIA-RELEASE adjudication; public USCIS release located", "actual_archives_audited": False, "linkable_realized_winner_loser_outcomes": "unknown", "original_FY2025_roster": "unknown"},
               "totals": totals, "designs": designs,
               "existing_design_adjudications": {design: [{"ruling_id": r["ruling_id"], "row_ref": r["_ref"], "value": decoded(r["ruling_value_json"]), "whole_field_resolved": r["whole_field_resolved"], "changes_blind_agreement": False} for r in inputs.rulings.values() if r["design_id"] == design] for design in (RDD, LOTTERY)},
               "input_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.paths}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "l2_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    (out / "l2_analysis_values.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    with (out / "l2_comparisons.csv").open("w", newline="") as f:
        cols = ["design_id", "unit", "component", "initial_result", "A_value", "B_value", "A_row_ref", "B_row_ref", "diagnostic_only", "third_read_value"]
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({**{k: r[k] for k in cols if k in r},
                             "A_value": json.dumps(r["A"]["value"], ensure_ascii=False),
                             "B_value": json.dumps(r["B"]["value"], ensure_ascii=False),
                             "A_row_ref": r["A"].get("row_ref", ""), "B_row_ref": r["B"].get("row_ref", ""),
                             "third_read_value": r["third_read"]["value"] if r["third_read"] else ""})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = run(args.root, args.out or args.root / "data/pipeline/v2")
    print(json.dumps(result["totals"], indent=2))
