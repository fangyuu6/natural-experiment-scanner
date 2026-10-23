"""Deterministic import of Tufts/Siegel's public firearm-law annual panel.

Source coding is a policy stock, not an absorbing first-adoption indicator. Raw
future/current-year snapshots are retained and explicitly flagged for the caller.
No annual reference day, effective date, or legislative decision date is invented.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import openpyxl

SOURCE_ID = "siegel_firearms"
DEFAULT_RAW = Path(__file__).resolve().parents[4] / "data/l2_pipeline/sources/research_panel/raw/firearms"
SNAPSHOT_YEAR = 2026
CLOCK = "source_annual_policy_status_reference_day_unverified"
STATE_NAMES = (
    ("Alabama", "AL"), ("Alaska", "AK"), ("Arizona", "AZ"), ("Arkansas", "AR"),
    ("California", "CA"), ("Colorado", "CO"), ("Connecticut", "CT"), ("Delaware", "DE"),
    ("Florida", "FL"), ("Georgia", "GA"), ("Hawaii", "HI"), ("Idaho", "ID"),
    ("Illinois", "IL"), ("Indiana", "IN"), ("Iowa", "IA"), ("Kansas", "KS"),
    ("Kentucky", "KY"), ("Louisiana", "LA"), ("Maine", "ME"), ("Maryland", "MD"),
    ("Massachusetts", "MA"), ("Michigan", "MI"), ("Minnesota", "MN"), ("Mississippi", "MS"),
    ("Missouri", "MO"), ("Montana", "MT"), ("Nebraska", "NE"), ("Nevada", "NV"),
    ("New Hampshire", "NH"), ("New Jersey", "NJ"), ("New Mexico", "NM"), ("New York", "NY"),
    ("North Carolina", "NC"), ("North Dakota", "ND"), ("Ohio", "OH"), ("Oklahoma", "OK"),
    ("Oregon", "OR"), ("Pennsylvania", "PA"), ("Rhode Island", "RI"), ("South Carolina", "SC"),
    ("South Dakota", "SD"), ("Tennessee", "TN"), ("Texas", "TX"), ("Utah", "UT"),
    ("Vermont", "VT"), ("Virginia", "VA"), ("Washington", "WA"), ("West Virginia", "WV"),
    ("Wisconsin", "WI"), ("Wyoming", "WY"),
)
STATES = dict(STATE_NAMES)


def _outcomes(category: str) -> list[str]:
    """Candidate first-order outcomes, declared without outcome-data inspection."""
    if category == "Child access prevention":
        return ["child firearm injuries", "child unintentional firearm deaths", "youth firearm suicides"]
    if category == "Domestic violence":
        return ["intimate-partner firearm homicides", "domestic-violence firearm injuries"]
    if category == "Stand your ground":
        return ["firearm homicides", "justifiable homicides", "firearm assaults"]
    if category == "Concealed carry permitting":
        return ["firearm homicides", "firearm assaults", "concealed-carry permits"]
    if category in {"Dealer regulations", "Buyer regulations", "Background checks", "Ammunition regulations"}:
        return ["firearm transfers", "firearm acquisition", "firearm homicides", "firearm suicides"]
    return ["firearm homicides", "firearm suicides", "nonfatal firearm injuries"]


def _number(value, binary: bool):
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "na", "nan", "."}):
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"Non-numeric firearm policy cell: {value!r}")
    if not math.isfinite(value):
        return None
    if binary and value not in (0, 1):
        raise ValueError(f"Nonbinary provision cell: {value!r}")
    return int(value) if float(value).is_integer() else float(value)


def load(raw_dir: str | Path | None = None):
    """Return (canonical rows, all usable variables, download receipts), offline.

    Complete historical candidate coverage is 1976–2025, pending the V3 official
    audit. 2026 is a partial July snapshot; the file's 2027 rows are unverified
    future snapshots and cannot constitute observed complete historical exposure.
    """
    raw = Path(raw_dir) if raw_dir is not None else DEFAULT_RAW
    receipts = json.loads((raw / "receipts.json").read_text())
    for receipt in receipts:
        name = Path(receipt.get("path", receipt["file"])).name
        body = (raw / name).read_bytes()
        if len(body) != receipt["size_bytes"] or hashlib.sha256(body).hexdigest() != receipt["sha256"]:
            raise ValueError(f"Download receipt mismatch: {name}")

    codebook = openpyxl.load_workbook(raw / "siegel_codebook.xlsx", read_only=True, data_only=True)
    try:
        definitions = {r[3]: r for r in list(codebook["Sheet1"].values)[1:] if r[3]}
    finally:
        codebook.close()
    database = openpyxl.load_workbook(raw / "siegel_database.xlsx", read_only=True, data_only=True)
    try:
        values = list(database["results"].values)
    finally:
        database.close()
    header = values[0]
    if header[1] != "year" or len(set(header[2:])) != len(header[2:]):
        raise ValueError("Unexpected/duplicate firearm panel headers")
    if set(header[2:]) != set(definitions) | {"lawtotal"}:
        raise ValueError("Policy columns and codebook definitions disagree")
    years = sorted({int(row[1]) for row in values[1:]})
    variables = []
    common_notes = (
        "Preserve annual zeros, ones, missing cells, repeal and reversal; no cumulative maximum. "
        "Source annual reference day is undocumented; enactment, decision and effective years require official audit. "
        "Source generally retains challenged laws despite temporary injunctions; Oregon Measure 114 is an exception. "
        "2026 is a partial July snapshot; 2027 future rows are unverified. No outcome association examined."
    )
    for name in header[2:]:
        aggregate = name == "lawtotal"
        entry = definitions.get(name)
        category = entry[1] if entry else "Aggregate count"
        variables.append({
            "policy_id": f"siegel:{name}", "source_id": SOURCE_ID, "source_variable": name,
            "definition": entry[5] or entry[4] if entry else "Source-provided sum of firearm provision indicators (not an individual policy).",
            "label": entry[4] if entry else "Number of firearm-law provisions",
            "kind": "continuous_intensity" if aggregate else "binary_adoption",
            "unit": "number of provision indicators" if aggregate else "0/1 source-defined policy presence",
            "clock": CLOCK, "category": category, "outcomes": _outcomes(category),
            "raw_coverage": {"start_year": min(years), "end_year": max(years), "states": 50},
            "complete_year_candidate_coverage": {"start_year": min(years), "end_year": 2025, "states": 50},
            "threshold": None,
            "disposition": "park_continuous_no_preregistered_threshold" if aggregate else "pending_ten_state_official_audit",
            "notes": common_notes + (" Continuous aggregate is parked; no threshold selected." if aggregate else ""),
            "coding_notes": entry[6] if entry else None,
            "coding_instructions": entry[7] if entry else None,
            "additional_definition_notes": entry[8] if entry else None,
            "definition_locator": f"siegel_codebook.xlsx:Sheet1:variable={name}" if entry else "siegel_database.xlsx:results:lawtotal",
        })
    rows = []
    seen = set()
    for row in values[1:]:
        state = STATES.get(str(row[0]).strip())
        year = int(row[1])
        if state is None or row[1] != year or (state, year) in seen:
            raise ValueError(f"Unknown state / invalid year / duplicate row: {row[:2]!r}")
        seen.add((state, year))
        year_status = "future_snapshot_unverified" if year > SNAPSHOT_YEAR else "current_year_partial" if year == SNAPSHOT_YEAR else "historical_candidate"
        for index, name in enumerate(header[2:], start=2):
            rows.append({"state": state, "year": year, "policy_id": f"siegel:{name}",
                         "value": _number(row[index], name != "lawtotal"),
                         "source_id": SOURCE_ID, "year_status": year_status,
                         "source_locator": f"siegel_database.xlsx:results:{row[0]}:{year}:{name}"})
    expected = {(state, year) for state in STATES.values() for year in years}
    if seen != expected or years != list(range(min(years), max(years) + 1)):
        raise ValueError("Firearm raw panel is not rectangular across all 50 states and years")
    return sorted(rows, key=lambda row: (row["policy_id"], row["state"], row["year"])), variables, receipts
