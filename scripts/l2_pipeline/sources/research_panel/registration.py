"""Additive S1 registration; existing teams' dispatch modules remain untouched.

Call this run_stage instead of cli.run_stage for research_panel S1. All other
stages and source types delegate to the existing dispatcher and prerequisites.
"""
from __future__ import annotations
import time
from ...contracts import Disposition, StageResult
from ...storage import sha, write_once
from .core import owned

SOURCE_TYPES = {'research_panel': 'scripts.l2_pipeline.sources.research_panel.s1:run'}


def register(mapping):
    """Host-owned registries can consume this plugin without implicit patching."""
    if 'research_panel' in mapping and mapping['research_panel'] != SOURCE_TYPES['research_panel']:
        raise ValueError('research_panel already registered differently')
    mapping.update(SOURCE_TYPES)
    return mapping


def run_stage(ctx, stage):
    if stage != 'S1' or ctx.card.payload.get('treatment', {}).get('source_type') != 'research_panel':
        from ...cli import run_stage as existing
        return existing(ctx, stage)
    from ...cli import preflight
    from .s1 import run
    owned(ctx.design_dir)
    try:
        preflight(ctx, stage)
        result = run(ctx)
    except Exception as exc:
        result = StageResult('S1', ctx.card.design_id, Disposition.FAILED, str(exc),
            'Repair inputs in a fresh version', metrics={'exception_type': type(exc).__name__})
    write_once(ctx.design_dir / 'receipts/S1' / (str(time.time_ns()) + '.json'),
        {'card_sha256': sha(ctx.card.path), 'result': result.as_dict()})
    return result
