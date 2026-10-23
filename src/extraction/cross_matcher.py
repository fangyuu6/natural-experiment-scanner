"""
Cross-Match Engine — post-hoc audit of policy_db.csv.

Two matching modes:
  1. Threshold confounds: same jurisdiction + similar RV type + fuzzy cutoff + time overlap
  2. Family groups: same policy_family across jurisdictions → DID potential

Used by scripts/cross_match_audit.py.  Does NOT modify the LLM pipeline.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from extraction.confound_detector import (
    FEDERAL_THRESHOLDS,
    STATE_THRESHOLDS,
    _parse_cutoff,
    _worst_severity,
)

# ──────────────────────────────────────────────────────────────────────
# Running-variable canonical categories
# ──────────────────────────────────────────────────────────────────────
_RV_CATEGORIES: List[Tuple[str, List[str], List[str]]] = [
    # (canonical, keywords, exclude)
    ("employees", [
        "employee", "firm_size", "firm size", "headcount",
        "workers", "fte", "full time equivalent", "staff size",
    ], []),
    ("age", [
        "age", "years old", "year of birth",
    ], ["acreage", "stage", "mileage", "usage", "storage", "dosage",
        "passage", "postage", "voltage", "shortage"]),
    ("income", [
        "income", "earnings", "wage", "salary", "compensation",
        "poverty", "fpl", "fpg", "agi", "taxable_income", "gross_receipts",
    ], []),
    ("population", [
        "population", "enrollment", "number of students", "residents",
        "student count", "headcount enrollment",
    ], []),
    ("monetary_value", [
        "property value", "contract value", "revenue", "assets",
        "sales", "assessed value", "market value", "receipts",
        "expenditure", "budget",
    ], []),
    ("percentage", [
        "percent", "rate", "share", "proportion", "ratio",
    ], []),
    ("time_duration", [
        "days", "months", "weeks", "hours", "years of service",
        "years of experience", "tenure",
    ], []),
    ("distance", [
        "distance", "miles", "feet", "meters", "yards", "radius",
    ], []),
    ("score", [
        "score", "grade", "proficiency", "rating", "gpa", "index",
    ], []),
]

# Families to skip in cross-jurisdiction grouping (ceremonial / noise)
_SKIP_FAMILIES = frozenset({
    "ceremonial_resolution", "ceremonial_commendation",
    "symbolic_resolution", "commemorative_resolution",
    "symbolic_commendation", "symbolic_recognition",
    "non_binding_resolution", "placeholder_title",
    "technical_correction", "technical_amendment",
    "administrative_reporting", "congratulatory_resolution",
})

# Employee-count "gravity" thresholds — snap nearby values
_EMPLOYEE_GRAVITY = [15, 20, 25, 50, 100, 250, 500]

# Known permanent federal thresholds (always temporally relevant)
PERMANENT_FEDERAL = {
    ("employees", 50): "ACA Employer Mandate / FMLA",
    ("employees", 20): "COBRA / ADEA",
    ("employees", 15): "Title VII / ADA / GINA",
    ("employees", 100): "WARN Act",
    ("age", 65): "Medicare eligibility",
    ("age", 62): "Social Security early retirement",
}

# State code extraction regex — "CA State" → "CA", "New York State" → None (handled below)
_STATE_CODE_RE = re.compile(r"^([A-Z]{2})\s", re.IGNORECASE)

# Full state-name → code mapping (subset of most common)
_STATE_NAMES: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
}


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────
@dataclass
class ThresholdMatch:
    idx_a: int
    idx_b: int
    policy_id_a: str
    policy_id_b: str
    jurisdiction: str
    rv_canonical: str
    cutoff_a: float
    cutoff_b: float
    similarity: float
    severity: str


@dataclass
class FamilyGroup:
    family: str
    topic: str
    jurisdictions: Dict[str, List[int]]  # jur → row indices
    n_jurisdictions: int = 0
    earliest_date: str = ""
    latest_date: str = ""
    staggered: bool = False

    def __post_init__(self):
        self.n_jurisdictions = len(self.jurisdictions)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def extract_state_code(jurisdiction: str) -> str:
    """Extract 2-letter state code from jurisdiction string like 'CA State'."""
    jur = (jurisdiction or "").strip()
    if not jur:
        return ""
    # "CA State" pattern
    m = _STATE_CODE_RE.match(jur)
    if m:
        return m.group(1).upper()
    # "US Federal" / "US State"
    if jur.upper().startswith("US"):
        return "US"
    # Full state name
    low = jur.lower().replace(" state", "").strip()
    return _STATE_NAMES.get(low, "")


def normalize_rv_type(raw: str, fallback_unit: str = "") -> str:
    """Classify LLM-generated running_variable_type into a canonical category."""
    if not raw and not fallback_unit:
        return ""
    for text in [raw, fallback_unit]:
        if not text:
            continue
        low = text.lower().replace("-", " ").replace("_", " ")
        for canonical, keywords, excludes in _RV_CATEGORIES:
            if any(ex in low for ex in excludes):
                continue
            if any(kw in low for kw in keywords):
                return canonical
    return "other" if (raw or fallback_unit) else ""


def parse_cutoff_numeric(val: Any) -> Optional[float]:
    """Parse cutoff to a float, returning None on failure."""
    try:
        return float(str(val).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _snap_employee_cutoff(val: float) -> float:
    """Snap employee cutoff to nearest gravity threshold if within ±3."""
    for gt in _EMPLOYEE_GRAVITY:
        if abs(val - gt) <= 3:
            return float(gt)
    return val


def compute_cutoff_similarity(val_a: float, val_b: float,
                              rv_canonical: str = "") -> float:
    """Return 0.0–1.0 similarity between two cutoff values."""
    # For employee counts, snap to gravity thresholds first
    if rv_canonical == "employees":
        val_a = _snap_employee_cutoff(val_a)
        val_b = _snap_employee_cutoff(val_b)

    if val_a == val_b:
        return 1.0
    max_val = max(abs(val_a), abs(val_b))
    if max_val == 0:
        return 1.0
    rel_diff = abs(val_a - val_b) / max_val
    if rel_diff <= 0.02:
        return 1.0
    if rel_diff <= 0.10:
        return 0.8
    if rel_diff <= 0.20:
        return 0.5
    return 0.0


def _parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError, IndexError):
        return None


def check_time_window(row_a: Dict, row_b: Dict,
                      max_gap_years: int = 10) -> bool:
    """True if two policies are within a relevant time window."""
    da = _parse_date(row_a.get("introduced_date", ""))
    db = _parse_date(row_b.get("introduced_date", ""))
    if da is None or db is None:
        return True  # conservative: assume relevant
    return abs((da - db).days) <= max_gap_years * 365


def infer_severity(row_a: Dict, row_b: Dict, similarity: float) -> str:
    """Infer confound severity for a matched pair."""
    fam_a = (row_a.get("policy_family") or "").strip().lower()
    fam_b = (row_b.get("policy_family") or "").strip().lower()
    # Same policy family = same intervention, not a confound
    if fam_a and fam_b and fam_a == fam_b:
        return "none"
    if similarity >= 1.0:
        return "severe"
    return "manageable"


# ──────────────────────────────────────────────────────────────────────
# Index building — O(N) single pass
# ──────────────────────────────────────────────────────────────────────
def build_indices(rows: List[Dict]) -> Tuple[
    Dict[Tuple[str, str], List[int]],
    Dict[str, List[int]],
]:
    """Build threshold index and family index from policy rows.

    Returns:
        threshold_index: (state_code, rv_canonical) → [row indices]
        family_index:    policy_family → [row indices]
    """
    threshold_index: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    family_index: Dict[str, List[int]] = defaultdict(list)

    for i, row in enumerate(rows):
        # --- threshold index ---
        state = extract_state_code(row.get("jurisdiction", ""))
        rv_raw = row.get("running_variable_type", "")
        rv_unit = row.get("cutoff_unit", "")
        rv_canon = normalize_rv_type(rv_raw, rv_unit)
        cutoff = parse_cutoff_numeric(row.get("cutoff_value", ""))
        if state and rv_canon and rv_canon != "other" and cutoff is not None:
            threshold_index[(state, rv_canon)].append(i)

        # --- family index ---
        fam = (row.get("policy_family") or "").strip().lower()
        if fam and not fam.startswith("unique_") and fam not in _SKIP_FAMILIES:
            family_index[fam].append(i)

    return dict(threshold_index), dict(family_index)


# ──────────────────────────────────────────────────────────────────────
# Matching: threshold confounds
# ──────────────────────────────────────────────────────────────────────
def find_threshold_confounds(
    rows: List[Dict],
    threshold_index: Dict[Tuple[str, str], List[int]],
    *,
    min_similarity: float = 0.5,
    max_gap_years: int = 10,
) -> List[ThresholdMatch]:
    """Find confound pairs within each (jurisdiction, rv_type) group."""
    matches: List[ThresholdMatch] = []
    for (state, rv_canon), indices in threshold_index.items():
        if len(indices) < 2:
            continue
        for ai, idx_a in enumerate(indices):
            row_a = rows[idx_a]
            cut_a = parse_cutoff_numeric(row_a.get("cutoff_value", ""))
            if cut_a is None:
                continue
            for idx_b in indices[ai + 1:]:
                row_b = rows[idx_b]
                cut_b = parse_cutoff_numeric(row_b.get("cutoff_value", ""))
                if cut_b is None:
                    continue
                sim = compute_cutoff_similarity(cut_a, cut_b, rv_canon)
                if sim < min_similarity:
                    continue
                if not check_time_window(row_a, row_b, max_gap_years):
                    continue
                sev = infer_severity(row_a, row_b, sim)
                if sev == "none":
                    continue
                matches.append(ThresholdMatch(
                    idx_a=idx_a, idx_b=idx_b,
                    policy_id_a=row_a.get("policy_id", ""),
                    policy_id_b=row_b.get("policy_id", ""),
                    jurisdiction=f"{state} State",
                    rv_canonical=rv_canon,
                    cutoff_a=cut_a, cutoff_b=cut_b,
                    similarity=sim, severity=sev,
                ))
    return matches


# ──────────────────────────────────────────────────────────────────────
# Matching: cross-jurisdiction family groups
# ──────────────────────────────────────────────────────────────────────
def find_family_groups(
    rows: List[Dict],
    family_index: Dict[str, List[int]],
    *,
    min_jurisdictions: int = 2,
) -> List[FamilyGroup]:
    """Find policy families spanning multiple jurisdictions."""
    groups: List[FamilyGroup] = []
    for fam, indices in family_index.items():
        jur_map: Dict[str, List[int]] = defaultdict(list)
        for i in indices:
            jur = rows[i].get("jurisdiction", "") or "unknown"
            jur_map[jur].append(i)
        if len(jur_map) < min_jurisdictions:
            continue

        # Collect dates for stagger detection
        dates: List[str] = []
        for i in indices:
            d = (rows[i].get("introduced_date") or "").strip()
            if d:
                dates.append(d)
        dates.sort()
        earliest = dates[0] if dates else ""
        latest = dates[-1] if dates else ""

        # Staggered = enacted dates span > 1 year across jurisdictions
        staggered = False
        if len(dates) >= 2:
            d0 = _parse_date(dates[0])
            d1 = _parse_date(dates[-1])
            if d0 and d1 and abs((d1 - d0).days) > 365:
                staggered = True

        topic = ""
        for i in indices:
            t = (rows[i].get("policy_topic") or "").strip()
            if t and t != "other":
                topic = t
                break

        groups.append(FamilyGroup(
            family=fam,
            topic=topic,
            jurisdictions=dict(jur_map),
            earliest_date=earliest,
            latest_date=latest,
            staggered=staggered,
        ))

    groups.sort(key=lambda g: g.n_jurisdictions, reverse=True)
    return groups


# ──────────────────────────────────────────────────────────────────────
# Write-back: update confounding_policies in rows (in memory)
# ──────────────────────────────────────────────────────────────────────
def apply_confound_updates(
    rows: List[Dict],
    matches: List[ThresholdMatch],
) -> int:
    """Update confounding_policies and confound_severity fields in-place.

    Returns number of rows updated.
    """
    # Build: row_index → [(confound_text, severity)]
    updates: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
    for m in matches:
        title_b = (rows[m.idx_b].get("title") or "")[:60]
        jur_b = rows[m.idx_b].get("jurisdiction", "")
        text_ab = f"[DB-match] {title_b} ({jur_b}, cutoff={m.cutoff_b})"

        title_a = (rows[m.idx_a].get("title") or "")[:60]
        jur_a = rows[m.idx_a].get("jurisdiction", "")
        text_ba = f"[DB-match] {title_a} ({jur_a}, cutoff={m.cutoff_a})"

        updates[m.idx_a].append((text_ab, m.severity))
        updates[m.idx_b].append((text_ba, m.severity))

    updated = 0
    for idx, entries in updates.items():
        existing = (rows[idx].get("confounding_policies") or "").strip()
        existing_lower = existing.lower()
        new_parts = []
        for text, _ in entries:
            if text.lower() not in existing_lower:
                new_parts.append(text)
        if not new_parts:
            continue
        combined = existing + (" | " if existing else "") + " | ".join(new_parts)
        rows[idx]["confounding_policies"] = combined

        all_sevs = [s for _, s in entries]
        cur_sev = (rows[idx].get("confound_severity") or "none").strip().lower()
        all_sevs.append(cur_sev)
        rows[idx]["confound_severity"] = _worst_severity(all_sevs)
        updated += 1

    return updated
