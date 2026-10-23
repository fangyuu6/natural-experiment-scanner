"""Pure numerical runner; stage orchestration owns approvals and write-once I/O.

This module never opens panel/result paths. Frozen interval evaluation is the
only optional file read. Existing-study archival verification can call it
without creating an official run; new studies must use the guarded S6 stage.
"""
from __future__ import annotations

from pathlib import Path
import math
from typing import Callable, Sequence

from . import estimator, inference
from .design import build_design, structural_rows
from .reference import independent_reference, reference_agreement
from .types import Declaration, Design, FitPayload, PanelRow
from ..execution import DeclaredStop


def registered_multiplier_count(spec: dict) -> int:
    """Resolve the one registered count; old registrations inherit 4999."""
    count = spec.get('analysis', {}).get('multiplier_count', inference.INTERVAL_COUNTS['B'])
    if type(count) is not int or count < 2:
        raise ValueError('Registered multiplier count must be an integer at least two')
    return count


def fit_primary(declaration: Declaration, rows: Sequence[PanelRow], *, B: int = inference.INTERVAL_COUNTS['B'],
                seed: int = estimator.SEED,
                interval_evaluation_path: str | Path | None = None,
                interval_expected_designs: Sequence[str] | None = None,
                on_estimate: Callable[[], None] | None = None) -> tuple[FitPayload, Design, dict]:
    if type(B) is not int or B < 2 or type(seed) is not int:
        raise ValueError('Registered bootstrap count/seed invalid')
    keys = [(u, t) for u, t, _, _ in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate outcome key')
    if any(not math.isfinite(y) for _, _, y, _ in rows):
        raise ValueError('Nonfinite outcome input')
    design = build_design(declaration, rows)
    outcomes = {(u, t): y for u, t, y, _ in structural_rows(declaration, rows)}
    if on_estimate is not None:
        on_estimate()
    result = estimator.estimate_design(design, outcomes, B=B, seed=seed)
    result = inference.apply_reporting(result, design, outcomes,
             B=B,
             evaluation_path=interval_evaluation_path,
             design_id=declaration['key'], expected_designs=interval_expected_designs,
             primary=declaration.get('variant', 'primary') == 'primary')
    reference = reference_agreement(result, independent_reference(design, outcomes))
    if not reference['passed']:
        raise DeclaredStop('Independent contrast agreement failed at absolute1e-8')
    return dict(status='ESTIMATED', result=result, reference=reference), design, outcomes


# Sensitivities and primary use one path. An alternate estimand changes its
# declaration; outcome merge and numerical implementation are identical.
fit_row = fit_primary
