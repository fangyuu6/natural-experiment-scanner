"""Deterministic import of UKCPR and author-published Vaghul/Zipperer panels.

Only policy parameters are imported. Participation, economic, demographic and
political variables are deliberately excluded. No network access during import.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import warnings
import zipfile

import openpyxl


RAW_DIR = Path(__file__).resolve().parents[4] / "data/l2_pipeline/sources/research_panel/raw/welfare"
POLICY_INDICES = tuple(range(20, 30)) + tuple(range(31, 35)) + tuple(range(44, 62)) + (74,)
WAGE_OUTCOMES = ["CPS hourly wages", "CPS employment", "QCEW employment"]
EITC_OUTCOMES = ["CPS employment of single mothers", "ACS poverty", "IRS refundable credit claims"]
BENEFIT_OUTCOMES = ["CPS poverty", "CPS food insecurity", "ACF TANF caseload"]


def number(value):
    """Retain observed zero; empty cells and explicit missing codes stay missing."""
    if value is None or (isinstance(value, str) and value.strip() in {"", ".", "NA", "N/A", "-"}):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Unexpected policy cell type: {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError("Non-finite policy value")
    return value


def _verify_receipts(raw_dir):
    receipts = json.loads((raw_dir / "receipts.json").read_text())
    for receipt in receipts:
        path = raw_dir / Path(receipt["path"]).name
        content = path.read_bytes()
        if len(content) != receipt["size_bytes"] or hashlib.sha256(content).hexdigest() != receipt["sha256"]:
            raise ValueError(f"Source receipt mismatch: {path.name}")
    return receipts


def _book(path):
    # Reading never saves a workbook; discarded Excel presentation extensions
    # cannot alter policy cell values.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return openpyxl.load_workbook(path, read_only=True, data_only=True)


def _metadata(book, column, header):
    source_rows = list(book["2024 Data Updates and Sources"].values)[2:]
    match = next((r for r in source_rows if r[0] and str(r[0]).strip().casefold() == header.strip().casefold()), None)
    if column == 74:
        match = next(r for r in source_rows if r[0] == "ACA Expansion")
    if match is None or not match[7]:
        raise ValueError(f"No UKCPR definition for {header}")
    policy_id = "ukcpr:" + match[7]
    definition = match[2]
    if column == 32:
        definition = "Monthly state SSI supplement for individuals living independently"
    elif column == 33:
        definition = "Combined maximum monthly federal and state SSI benefit for an individual living independently"
    elif column == 34:
        definition = "Combined maximum monthly potential SSI and SNAP benefit for aged individuals living independently"
    binary = column == 59
    federal = column in tuple(range(44, 56)) + (31, 60)
    clock = "calendar year as coded by UKCPR; parameter timing varies by source; no inferred enactment date"
    if 23 <= column <= 26:
        clock = "federal fiscal year ending in coded year (Oct 1 previous year–Sep 30); historical source timing must be audited"
    elif column == 74:
        clock = "fraction of calendar year expanded; publisher formula [365 - enactment day-of-year]/365; official effective date requires audit"
    elif 20 <= column <= 22:
        clock = "source's annual maximum monthly benefit snapshot; Urban July tables from 2006"
    if column in (60, 61):
        outcomes = WAGE_OUTCOMES
        unit = "nominal USD/hour"
    elif 44 <= column <= 59:
        outcomes = EITC_OUTCOMES
        unit = "indicator" if binary else ("nominal USD" if 48 <= column <= 51 else "fraction")
    elif column == 74:
        outcomes = ["ACS uninsured rate", "CMS Medicaid enrollment", "BRFSS access to care"]
        unit = "fraction of year"
    else:
        outcomes = BENEFIT_OUTCOMES
        unit = "nominal USD/month"
    notes = str(match[5] or "")
    if column in (20, 21, 22, 27, 28, 29):
        notes += " Definition break: 1980–2005 excludes majority-rule calculation; from 2006 Urban WRD incorporates majority rule."
    if column == 56:
        notes += " Rate is program generosity, not legislative adoption; unfunded Washington EITC historically coded zero."
    if column in (57, 58):
        notes += " Wisconsin-only supplementary rates; blank other jurisdictions are structurally not applicable, never zero."
    if column in (32, 33, 34):
        notes += " Discontinued series; latest nonmissing values 2011, not current SSI rules."
    if column == 61:
        notes += " State statutory/effective-floor coding requires audit: Alabama 1980 is 3.10 in UKCPR while cited DOL table leaves Alabama blank; some statutory rates elsewhere are below the federal floor. A positive value cannot identify adoption of a state minimum-wage law."
    return {
        "policy_id": policy_id, "source_id": "ukcpr", "source_column": header,
        "definition": definition, "kind": "binary_adoption" if binary else "continuous_intensity",
        "unit": unit, "clock": clock, "outcomes": outcomes,
        "jurisdiction_scope": "federal parameter repeated across states" if federal else ("Wisconsin only" if column in (57, 58) else "state/DC"),
        "no_state_variation": federal,
        "source_only": federal or column in (23, 24, 25, 26, 57, 58),
        "source_reference": str(match[3] or "Historical UKCPR Sources/update sheets"),
        "definition_locator": "UKCPR workbook / 2024 Data Updates and Sources; historical sheets and Note on TANF Benefit Levels",
        "official_source_url": str(match[4] or "https://www.ssa.gov/policy/docs/progdesc/ssi_st_asst/"),
        "notes": notes.strip(),
        "missing_notes": "Keep blank cells missing. A complete key grid is not complete observed policy coverage.",
        "repeal_notes": "Preserve observed decreases and 1→0 reversals; never absorb treatment or carry values into missing years.",
        "threshold": None,
        "design_status": "awaiting_v3_official_audit" if binary else "parked_until_predeclared_threshold",
    }


def load(raw_dir=None):
    """Return (canonical rows, policy metadata, all immutable source receipts)."""
    raw_dir = Path(raw_dir) if raw_dir is not None else RAW_DIR
    receipts = _verify_receipts(raw_dir)
    rows, metadata = [], []
    book = _book(raw_dir / "ukcpr_1980_2024.xlsx")
    data = book["Data"].values
    headers = next(data)
    for column in POLICY_INDICES:
        metadata.append(_metadata(book, column, headers[column]))
    for record in data:
        state, year = record[0], record[3]
        if not isinstance(state, str) or len(state) != 2 or not isinstance(year, int):
            raise ValueError("Invalid UKCPR state/year key")
        for column, variable in zip(POLICY_INDICES, metadata):
            value = number(record[column])
            if variable["kind"] == "binary_adoption" and value not in (None, 0, 1):
                raise ValueError("Invalid refundable EITC binary code")
            rows.append({"state": state, "year": year, "policy_id": variable["policy_id"], "value": value})
    book.close()
    with zipfile.ZipFile(raw_dir / "vz_state_excel_v1.4.0.zip") as archive:
        book = _book(io.BytesIO(archive.read("mw_state_annual.xlsx")))
        data = book.active.values
        headers = next(data)
        vz_variables = []
        for column, statistic, scope in [(4, "minimum", "federal"), (5, "minimum", "effective_state"),
                                         (6, "day_weighted_mean", "federal"), (7, "day_weighted_mean", "effective_state"),
                                         (8, "maximum", "federal"), (9, "maximum", "effective_state")]:
            policy_id = "vz:" + scope + "_mw_" + statistic
            definition = f"Annual {statistic.replace('_', ' ')} of daily " + ("federal minimum wage" if scope == "federal" else "effective minimum wage, max(state statutory rate, federal rate)")
            variable = {
                "policy_id": policy_id, "source_id": "vz", "source_column": headers[column],
                "definition": definition, "kind": "continuous_intensity", "unit": "nominal USD/hour",
                "clock": "calendar-year daily aggregation; 1974 is partial (May 1–Dec 31), full years 1975–2022",
                "outcomes": WAGE_OUTCOMES, "threshold": None,
                "jurisdiction_scope": "federal parameter repeated across states" if scope == "federal" else "state/DC effective floor",
                "no_state_variation": scope == "federal", "source_only": scope == "federal",
                "definition_locator": "v1.4.0 code/state_mw.do: mw_adj max rule and yearly collapse",
                "official_source_url": "https://github.com/benzipperer/historicalminwage/releases/tag/v1.4.0",
                "notes": "Publisher effective wage incorporates federal floor; a positive level is not evidence of a state law. Annual minimum/maximum is not a January 1/December 31 stock.",
                "missing_notes": "Keep exported missing values missing; no synthetic extension after 2022.",
                "repeal_notes": "Preserve observed decreases; federal floor may mask state-law reversals. Underlying daily changes needed for enactment/repeal audit.",
                "design_status": "parked_until_predeclared_threshold",
            }
            metadata.append(variable)
            vz_variables.append((column, variable))
        for record in data:
            state, year = record[2], int(record[3])
            if not isinstance(state, str) or len(state) != 2:
                raise ValueError("Invalid VZ state key")
            for column, variable in vz_variables:
                rows.append({"state": state, "year": year, "policy_id": variable["policy_id"], "value": number(record[column])})
        book.close()
    return rows, metadata, receipts
