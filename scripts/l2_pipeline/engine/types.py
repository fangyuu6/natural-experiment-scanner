"""Numerical contracts independent of design-card and queue storage schemas."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, TypedDict

CellKey = tuple[str, int]
PanelRow = tuple[str, int, float, Optional[int]]
Outcomes = Mapping[CellKey, float]
Design = dict[str, Any]


class Declaration(TypedDict, total=False):
    key: str
    variant: str
    case: dict[str, Any]
    cohorts: dict[str, int]
    operative_years: dict[str, int]
    never_controls: Sequence[str]
    exposure: dict[str, dict[int, dict[str, bool]]]
    period_years: Sequence[int]
    control_group: str
    partial_periods_dropped: Sequence[dict[str, Any]]
    CT_complete_path_reference_controls: Optional[Sequence[str]]
    summary: dict[str, Any]
    allow_partial_start: bool
    transition_year_drop: bool
    transition_effective_years: dict[str, int]


class FitPayload(TypedDict):
    status: str
    result: dict[str, Any]
    reference: dict[str, Any]
