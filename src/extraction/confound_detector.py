"""
Confounding Policy Detector — Phase B rule engine.
Maintains a knowledge base of federal and state employer-size thresholds
and cross-matches them against a policy's running variable + cutoff.
Used by llm_analyzer._map_aer_to_legacy() as a post-LLM rule layer.
"""
from typing import Any, Dict, List, Tuple

# ─────────────────────────────────────────────────────────────
# Federal threshold knowledge base
# key: employee count threshold (int)
# value: list of (policy_name, severity_hint)
# severity_hint: "fatal" | "severe" | "manageable"
# ─────────────────────────────────────────────────────────────
FEDERAL_THRESHOLDS: Dict[int, List[Tuple[str, str]]] = {
    100: [
        ("WARN Act (100 employees, 60-day layoff notice)", "manageable"),
    ],
    50: [
        ("ACA Employer Mandate (50 FTE, $2,000-$3,000/employee penalty — 10-20x typical PFL cost)", "fatal"),
        ("FMLA (50 employees, unpaid job-protected leave)", "severe"),
    ],
    20: [
        ("COBRA (20 employees, mandatory health continuation coverage)", "manageable"),
        ("ADEA (20 employees, age discrimination protections)", "manageable"),
    ],
    15: [
        ("Title VII (15 employees, race/sex/religion discrimination)", "manageable"),
        ("ADA (15 employees, disability accommodation)", "manageable"),
        ("GINA (15 employees, genetic information non-discrimination)", "manageable"),
    ],
}

# ─────────────────────────────────────────────────────────────
# State-level threshold knowledge base
# key: jurisdiction code (e.g. "OR", "WA")
# value: dict of {threshold: [(policy_name, severity_hint)]}
# ─────────────────────────────────────────────────────────────
STATE_THRESHOLDS: Dict[str, Dict[int, List[Tuple[str, str]]]] = {
    "OR": {
        25: [
            ("Oregon Family Leave Act (OFLA, since 1995, 25-employee job protection)", "manageable"),
        ],
    },
    "MA": {
        25: [
            ("Massachusetts PFML (25 employees, enacted 2018, effective 2021)", "manageable"),
            ("Massachusetts Pay Transparency Act (25 employees, effective 2025)", "manageable"),
        ],
        6: [
            ("Massachusetts small employer definition (6 employees)", "manageable"),
        ],
    },
    "WA": {
        50: [
            ("Washington PFML (50 employees, enacted 2017)", "manageable"),
        ],
    },
    "CO": {
        10: [
            ("Colorado FAMLI (10 employees, effective 2024)", "manageable"),
        ],
    },
    "NY": {
        50: [
            ("New York Paid Family Leave (50 employees threshold for certain provisions)", "manageable"),
        ],
    },
    "CT": {
        1: [
            ("Connecticut Paid Leave (all employers, no size threshold)", "manageable"),
        ],
    },
    "NJ": {
        30: [
            ("New Jersey Family Leave Act (30 employees)", "manageable"),
        ],
    },
    "CA": {
        5: [
            ("California CFRA (5 employees, effective 2021)", "manageable"),
        ],
        50: [
            ("California CFRA original threshold (50 employees, pre-2021)", "manageable"),
        ],
    },
    "IL": {
        50: [
            ("Illinois Paid Leave for All Workers — small employer definition (50 employees)", "manageable"),
        ],
    },
}

# Running variable keywords that trigger employer-size confound checks
_EMPLOYER_SIZE_KEYWORDS = (
    "employee", "employees", "firm_size", "firm size", "headcount",
    "workers", "full.time equivalent", "fte",
)


def _is_employer_size_var(running_variable_type: str) -> bool:
    """Return True if the running variable is employer size / headcount."""
    rv = (running_variable_type or "").lower().replace("-", " ")
    return any(kw in rv for kw in _EMPLOYER_SIZE_KEYWORDS)


def _parse_cutoff(cutoff_value: Any) -> int | None:
    """Try to parse cutoff_value as an integer."""
    try:
        return int(float(str(cutoff_value).strip()))
    except (TypeError, ValueError):
        return None


def _worst_severity(severities: List[str]) -> str:
    """Return the most severe level from a list."""
    order = ("none", "manageable", "severe", "fatal")
    best_idx = 0
    for s in severities:
        try:
            idx = order.index(s.lower())
            best_idx = max(best_idx, idx)
        except ValueError:
            pass
    return order[best_idx]


def detect_confounds(
    running_variable_type: str,
    cutoff_value: Any,
    jurisdiction: str,
) -> List[Dict[str, Any]]:
    """
    Cross-match a policy's running variable + cutoff against the federal and
    state threshold knowledge bases.

    Returns list of confound dicts, each with:
        {"policy": "...", "severity": "...", "type": "threshold_overlap"}
    All entries from the knowledge base are threshold_overlap type
    (employer-size cutoff matches). Empty list if no confounds found.
    """
    # Only applies to employer-size running variables
    if not _is_employer_size_var(running_variable_type):
        return []

    cutoff = _parse_cutoff(cutoff_value)
    if cutoff is None:
        return []

    confounds: List[Dict[str, Any]] = []

    # Check federal thresholds (exact match only)
    for threshold, policies in FEDERAL_THRESHOLDS.items():
        if threshold == cutoff:
            for name, sev in policies:
                confounds.append({
                    "policy": f"[Federal] {name}",
                    "severity": sev,
                    "type": "threshold_overlap",
                })

    # Check state thresholds
    jur = (jurisdiction or "").strip().upper()
    state_key = None
    for key in STATE_THRESHOLDS:
        if jur.startswith(key) or jur.endswith(key):
            state_key = key
            break
    if state_key and state_key in STATE_THRESHOLDS:
        state_db = STATE_THRESHOLDS[state_key]
        for threshold, policies in state_db.items():
            if threshold == cutoff:
                for name, sev in policies:
                    confounds.append({
                        "policy": f"[{state_key}] {name}",
                        "severity": sev,
                        "type": "threshold_overlap",
                    })

    return confounds
