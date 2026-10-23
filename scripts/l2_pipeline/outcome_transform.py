"""R3 (hub ruling 2026-10-18): optional card field ``outcome_transform`` = ``level`` (default) | ``log``.

Registered at S0 with the outcome, before any outcome value is read. One transform per card; no co-primary,
no later switch (sensitivities and requeues cannot change it). ``log`` is the natural log of the registered catalog
series as S2 builds it, in "log points". No log(1+y), no inverse hyperbolic sine, no zero replacement: a series that
is not strictly positive in every state-year used keeps the card at ``level`` (S0) or STOPs the design (S2).
Cards without the field are byte-identical to before and count as ``level``.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any, Mapping

FIELD = "outcome_transform"
TRANSFORMS = ("level", "log")
LOG_UNITS = "log points"
# Series cleared for ``log``: strictly positive in every state-year (census of the series, no treatment/association).
ELIGIBLE = Path(__file__).with_name("LOG_POSITIVE_SERIES.json")
FORBIDDEN_KEYS = ("log1p", "ihs", "asinh", "inverse_hyperbolic_sine", "zero_replacement", "zero_replace", "offset")
# Estimate scale classes and the benchmark scales each may be compared with (ruling item 5).
COMPARABLE = {"level": {"level"}, "log": {"log", "percent"}}
BASES = ("rate", "count")


def transform(card: Mapping[str, Any]) -> str:
    value = card.get(FIELD, "level")
    if value not in TRANSFORMS:
        raise ValueError("outcome_transform must be level or log")
    return value


def declared(card: Mapping[str, Any]) -> bool:
    return FIELD in card


def eligible_series() -> dict:
    if not ELIGIBLE.is_file():
        return {}
    return json.loads(ELIGIBLE.read_text()).get("series", {})


def series_id(card: Mapping[str, Any]) -> str:
    outcome = card.get("outcome", {}) if isinstance(card.get("outcome"), Mapping) else {}
    return outcome.get("series_id") or outcome.get("measure_id") or ""


def s0_issues(card: Mapping[str, Any]) -> list[str]:
    """S0 gate; reads no outcome value. Empty for level cards and for cards without the field."""
    if not declared(card):
        return []
    if card[FIELD] not in TRANSFORMS:
        return ["outcome_transform must be level or log"]
    if card[FIELD] == "level":
        return []
    issues = []
    outcome = card.get("outcome", {}) if isinstance(card.get("outcome"), Mapping) else {}
    bad = sorted(k for k in outcome if any(t in str(k).lower() for t in FORBIDDEN_KEYS))
    if bad:
        issues.append("log outcome allows no offset, log1p, IHS or zero replacement: " + ", ".join(bad))
    sid = series_id(card)
    if not sid or sid not in eligible_series():
        issues.append("log needs a registered catalog series proven strictly positive in every state-year; this card stays level")
    return issues


def apply_log(card: Mapping[str, Any], data):
    """S2: natural log of every available cell of the built panel; a cell that is not strictly positive STOPs."""
    from dataclasses import replace
    from .connectors.common import ConnectorError
    if transform(card) != "log":
        return data
    available = {(m["unit"], m["period"]) for m in data.metadata if int(m.get("available", 1))}
    rows, source_unit = [], None
    for row in data.rows:
        value = row.get("outcome_value")
        if (row["unit"], row["period"]) in available and value not in (None, ""):
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                raise ConnectorError("Log outcome needs a strictly positive value in every state-year used (%s %s); "
                                     "no zero replacement or offset" % (row["unit"], row["period"]))
            source_unit = source_unit or row.get("outcome_unit")
            row = dict(row, outcome_value=repr(math.log(number)), outcome_unit=LOG_UNITS)
        elif value not in (None, ""):
            row = dict(row, outcome_unit=LOG_UNITS)
        rows.append(row)
    details = dict(data.details, outcome_transform="log", transformed_unit=LOG_UNITS, source_unit_before_log=source_unit)
    return replace(data, rows=tuple(rows), details=details)


def units(card: Mapping[str, Any]) -> dict:
    t = transform(card)
    label = LOG_UNITS if t == "log" else str(card.get("outcome", {}).get("outcome_unit") or "")
    return {"outcome_transform": t, "units": label}


def export_fields(card: Mapping[str, Any], source_unit_before_log: Any = None) -> dict:
    """Export carries the scale only when the card declares it (old exports are byte-identical).

    Log cards also carry ``source_unit_before_log`` (e.g. "per 1,000 residents") when S2 recorded it;
    level cards and cards without the field never get it."""
    if not declared(card):
        return {}
    fields = units(card)
    if fields["outcome_transform"] == "log" and source_unit_before_log is not None:
        fields["source_unit_before_log"] = source_unit_before_log
    return fields


def scale_class(entry: Mapping[str, Any]) -> str:
    """Scale of an exported estimate: ``log`` only when it says so; absent field = level."""
    t = entry.get(FIELD, "level")
    if t not in TRANSFORMS:
        raise ValueError("Unknown outcome_transform in export")
    if t == "log" and entry.get("units") != LOG_UNITS:
        raise ValueError("log export without log points units")
    return t


def comparable(estimate_scale: str, benchmark_scale: str) -> bool:
    """log estimates meet log or percent benchmarks; level estimates meet level benchmarks only."""
    return benchmark_scale in COMPARABLE[estimate_scale]


def assert_poolable(entries) -> str:
    """Mechanical pool guard: every entry in one pool has the same scale class."""
    classes = {scale_class(e) for e in entries}
    if len(classes) > 1:
        raise ValueError("Pool mixes log and level estimates; never pooled across scales")
    return classes.pop() if classes else "level"


def check_benchmark(entry: Mapping[str, Any], benchmark_scale: str, basis: str, benchmark_basis: str) -> None:
    """Ruling item 3: scale must be comparable AND the basis (rate | count) must match.

    ``basis`` is the estimate's basis, ``benchmark_basis`` the benchmark's; a log of a raw count never meets a log-rate."""
    for value in (basis, benchmark_basis):
        if value not in BASES:
            raise ValueError("basis must be rate or count")
    if not comparable(scale_class(entry), benchmark_scale):
        raise ValueError("Estimate scale %s cannot be compared with a %s benchmark" % (scale_class(entry), benchmark_scale))
    if basis != benchmark_basis:
        raise ValueError("Estimate basis %s cannot be compared with a %s-basis benchmark" % (basis, benchmark_basis))
