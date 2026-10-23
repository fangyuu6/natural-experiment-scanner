"""Outcome-blind V6 windows bounded by declared legal and connector coverage.

Declared source coverage is an audit scope, not an S1 legal certification.
This module reads no source files or outcome values and never extends coverage.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
from datetime import date
import re
from typing import Any

RULE = "coverage_bounded_windows_v6"


def _year(value: Any) -> int | None:
    return value if type(value) is int and 1800 <= value <= 9999 else None


def _date_year(value: Any) -> int | None:
    if _year(value) is not None:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}(?:-\d{2}-\d{2})?", value):
        if len(value) == 10:
            try:
                date.fromisoformat(value)
            except ValueError:
                return None
        return _year(int(value[:4]))
    return None


def _catalog_default() -> Mapping:
    from . import connectors
    catalog = getattr(connectors, "CONNECTOR_CATALOG", None)
    if isinstance(catalog, Mapping):
        return catalog
    for name in ("connector_catalog", "first_order_catalog"):
        factory = getattr(connectors, name, None)
        if callable(factory):
            value = factory()
            if isinstance(value, Mapping):
                return value
    return {}


def _connector_bounds(outcome: Mapping, catalog: Mapping) -> tuple[dict, list[str]]:
    from .connectors import ALIASES
    name = outcome.get("connector")
    name = ALIASES.get(name.lower(), name.lower()) if isinstance(name, str) else ""
    entry = catalog.get(name, {})
    entry = entry if isinstance(entry, Mapping) else {}
    coverage = entry.get("coverage", {})
    coverage = coverage if isinstance(coverage, Mapping) else {}
    start = _year(coverage.get("start", coverage.get("start_year", entry.get("coverage_start"))))
    end = _year(coverage.get("end", coverage.get("end_year", entry.get("coverage_end"))))
    pair = entry.get("coverage_years")
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        start = start if start is not None else _year(pair[0])
        end = end if end is not None else _year(pair[1])
    excluded = coverage.get("excluded_years", entry.get("excluded_years", []))
    issues = []
    if start is None or end is None:
        issues.append("connector coverage bounds unknown")
    elif start > end:
        issues.append("connector coverage bounds reversed")
    if not isinstance(excluded, (list, tuple)) or any(_year(y) is None for y in excluded):
        issues.append("connector excluded years invalid")
        excluded = []
    from .connectors.series_windows import definition_metadata
    definitions=dict(definition_metadata(entry.get('series_id',outcome.get('series_id'))))
    definitions.update({k:copy.deepcopy(entry[k]) for k in ('series_breaks','definition_segments','consistent_definition_window','window_edge_rule') if k in entry})
    window=definitions.get('consistent_definition_window')
    if window:
        if len(window)!=2 or any(_year(y) is None for y in window) or window[0]>window[1]:
            issues.append('invalid consistent definition window')
        else:
            start=max(start,window[0]) if start is not None else None
            end=min(end,window[1]) if end is not None else None
    return {"connector": name, "start_year": start, "end_year": end,
            "excluded_years": sorted(set(excluded)),**definitions}, issues


def _legal_bounds(treatment: Mapping) -> tuple[dict, list[str]]:
    window = treatment.get("source_window", {})
    window = window if isinstance(window, Mapping) else {}
    start = _year(window.get("start_year", window.get("start")))
    source_end = _year(window.get("end_year", window.get("end")))
    declared_end = _date_year(treatment.get("coverage_end"))
    ends = [end for end in (source_end, declared_end) if end is not None]
    end = min(ends) if ends else None
    issues = []
    if end is None:
        issues.append("legal coverage end unknown")
    if window and start is None:
        issues.append("legal source-window start unknown")
    if window and source_end is None:
        issues.append("legal source-window end unknown")
    if start is not None and end is not None and start > end:
        issues.append("legal coverage bounds reversed")
    return {"start_year": start, "end_year": end,
            "source_window_end_year": source_end, "declared_coverage_end_year": declared_end,
            "status": "declared_audit_scope_pending_S1_certification"}, issues


def _desired(outcome: Mapping, retained: Mapping | None = None) -> dict:
    retained = retained or {}
    return {"original_requested_years": copy.deepcopy(retained.get("original_requested_years", outcome.get("years"))),
            "original_period_years": copy.deepcopy(retained.get("original_period_years", outcome.get("period_years"))),
            "period_years_declared": retained.get("period_years_declared", "period_years" in outcome)}


def normalize_series_breaks(breaks: Any) -> list[dict]:
    """Return catalog ``series_breaks`` entries in the dict shape ``_bound`` reads.

    Entries are dicts (``year``, optional ``definition_change``) or the
    ``[year, note]`` pairs written by ``connectors/c70_build.py``. A dict is
    returned unchanged. A pair becomes ``dict(year=year, note=note)``: it keeps
    the recorded break but carries no ``definition_change`` flag, so it is not a
    window edge (same as every unflagged break). Yearless strings become structured
    non-window notes. Other shapes raise; no
    entry is skipped, so break information is never silently dropped.
    """
    if breaks is None:
        return []
    if not isinstance(breaks, (list, tuple)):
        raise ValueError("series_breaks must be a list of dict or [year, note] entries, got "
                         + type(breaks).__name__)
    normalized = []
    for index, entry in enumerate(breaks):
        if isinstance(entry, Mapping):
            normalized.append(entry)
        elif (isinstance(entry, (list, tuple)) and len(entry) == 2 and
              _year(entry[0]) is not None and isinstance(entry[1], str)):
            normalized.append({"year": entry[0], "note": entry[1]})
        elif isinstance(entry,str) and entry.strip() and not re.search(r"\b(?:18|19|20|21)\d{2}\b",entry):
            normalized.append({"note":entry,"definition_change":False,"window_edge":False,"year_unknown":True})
        else:
            raise ValueError("series_breaks entry %d must be a dict or a [year, note] pair, got %s: %.80r"
                             % (index, type(entry).__name__, entry))
    return normalized


def _bound(outcome: dict, treatment: Mapping, catalog: Mapping, desired: Mapping) -> dict:
    legal, issues = _legal_bounds(treatment)
    connector, connector_issues = _connector_bounds(outcome, catalog)
    if 'series_breaks' in connector:
        connector['series_breaks']=[normalize_series_breaks([v])[0] if isinstance(v,str) else v for v in connector['series_breaks']]
    issues.extend(connector_issues)
    years = desired["original_requested_years"]
    if (not isinstance(years, (list, tuple)) or len(years) != 2 or
            any(_year(y) is None for y in years) or years[0] > years[1]):
        issues.append("requested outcome window unknown or invalid")
    periods = desired["original_period_years"]
    if desired["period_years_declared"] and (
            not isinstance(periods, (list, tuple)) or not periods or
            any(_year(y) is None for y in periods) or list(periods) != sorted(set(periods))):
        issues.append("requested period_years unknown or invalid")
    report = dict(desired, rule=RULE, legal_bounds=legal, connector_bounds=connector,
                  no_extrapolation=True, legal_certification_granted=False,
                  outcome_values_read=0, outcome_association_calls=0,
                  effective_years=None, effective_period_years=[], excluded_years=[],
                  parked=bool(issues), status="unknown", issues=issues)
    if issues:
        report["reason"] = "; ".join(issues)
        return report
    first = max(years[0], connector["start_year"], legal["start_year"] or years[0])
    last = min(years[1], connector["end_year"], legal["end_year"])
    breaks=sorted(b['year'] for b in normalize_series_breaks(connector.get('series_breaks')) if b.get('definition_change') is True)
    if not connector.get('consistent_definition_window'):
        # Anchor on the first requested calendar year, independent of legal
        # support or outcome values; never slide into a better supported segment.
        lower=max((y for y in breaks if y<=years[0]),default=first)
        upper=min((y-1 for y in breaks if y>years[0]),default=last)
        first=max(first,lower);last=min(last,upper)
    requested_periods = list(periods) if desired["period_years_declared"] else list(range(years[0], years[1] + 1))
    available = [y for y in requested_periods if first <= y <= last and y not in connector["excluded_years"]]
    report["excluded_years"] = [y for y in requested_periods if y not in available]
    if not available:
        outcome["years"] = []
        if desired["period_years_declared"] or connector["excluded_years"]:
            outcome["period_years"] = []
        report.update(status="empty", parked=True, effective_years=[],
                      reason="No requested outcome periods overlap declared legal and connector coverage")
        return report
    outcome["years"] = [available[0], available[-1]]
    # An explicit period inventory must survive bounds, including biennial CPS
    # periods and holes such as the ACS1 2020 vintage.
    if desired["period_years_declared"] or connector["excluded_years"]:
        outcome["period_years"] = available
    else:
        outcome.pop("period_years", None)
    report.update(status="bounded", parked=False, effective_years=copy.deepcopy(outcome["years"]),
                  effective_period_years=available,
                  reason="Requested outcome periods intersected with declared legal audit scope and connector coverage")
    return report


def _merge(base: dict, patch: Mapping) -> None:
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def coverage_bounded_card(card: Mapping[str, Any], *, catalog: Mapping | None = None) -> dict:
    """Return a copy with V6 primary and sensitivity windows, without readiness.

    ``catalog`` accepts connector entries with ``coverage_start/coverage_end``,
    ``coverage={start,end,excluded_years}``, ``coverage={start_year,end_year}``,
    or ``coverage_years=[first,last]``. Unknown or empty coverage parks in
    ``automation.coverage_window``; callers must honor that disposition.
    """
    result = copy.deepcopy(dict(card))
    catalog = _catalog_default() if catalog is None else catalog
    automation = result.setdefault("automation", {})
    previous = automation.get("coverage_window", {})
    previous = previous if isinstance(previous, Mapping) and previous.get("rule") == RULE else {}
    outcome = result.setdefault("outcome", {})
    treatment = result.get("treatment", {})
    primary_desired = _desired(outcome, previous)
    report = _bound(outcome, treatment, catalog, primary_desired)
    sensitivity_reports = {}
    prior_variants = previous.get("sensitivities", {})
    for sensitivity in result.get("sensitivities", []):
        if not isinstance(sensitivity, dict) or not isinstance(sensitivity.get("changes", {}), Mapping):
            continue  # General card validation owns malformed inventory.
        name = sensitivity.get("name", "unnamed")
        variant = {"outcome": copy.deepcopy(dict(card.get("outcome", {}))),
                   "treatment": copy.deepcopy(dict(treatment))}
        variant["outcome"]["years"] = copy.deepcopy(primary_desired["original_requested_years"])
        if primary_desired["period_years_declared"]:
            variant["outcome"]["period_years"] = copy.deepcopy(primary_desired["original_period_years"])
        else:
            variant["outcome"].pop("period_years", None)
        _merge(variant, sensitivity.get("changes", {}))
        desired = _desired(variant["outcome"], prior_variants.get(name))
        variant_report = _bound(variant["outcome"], variant["treatment"], catalog, desired)
        sensitivity_reports[name] = variant_report
        if variant_report["status"] != "unknown":
            changes = sensitivity.setdefault("changes", {})
            patched_outcome = changes.setdefault("outcome", {})
            patched_outcome["years"] = copy.deepcopy(variant["outcome"]["years"])
            # Sensitivity patches merge into the bounded primary. Always bind
            # their own periods so primary-specific holes cannot leak into a
            # different connector or window through that merge.
            patched_outcome["period_years"] = copy.deepcopy(variant_report["effective_period_years"])
        if variant_report["parked"]:
            report["parked"] = True
            report["reason"] += "; sensitivity " + str(name) + ": " + variant_report["reason"]
    report["sensitivities"] = sensitivity_reports
    report["ready_for_audit"] = not report["parked"]
    report["readiness_granted"] = False
    automation["coverage_window"] = report
    return result


def legal_window_issues(card: Mapping[str, Any]) -> list[str]:
    """Enforce the legal end invariant for existing declared local-panel cards.

    Catalog certification is handled by full V6 intake. This compatibility
    guard never authorizes missing legal coverage or alters any old card bytes.
    """
    treatment=card.get('treatment',{})
    bounds, _=_legal_bounds(treatment)
    outcome=card.get('outcome',{})
    years=outcome.get('years')
    if not isinstance(years,(list,tuple)) or len(years)!=2 or any(_year(y) is None for y in years):
        return []
    issues=[]
    if bounds['end_year'] is not None and years[-1]>bounds['end_year']:
        issues.append('Outcome window exceeds declared legal coverage end')
    if bounds['start_year'] is not None and years[0]<bounds['start_year']:
        issues.append('Outcome window begins before declared legal source coverage')
    return issues
