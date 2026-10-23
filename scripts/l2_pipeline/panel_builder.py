"""S2 outcome-only panel construction, shared by primary and sensitivities.

Preparation emits state-period support/counts only. There is no treatment-table,
association, estimator, or calibration import on this construction path.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .connectors import ConnectorError, ConnectorRequest, construct
from .connectors.common import sha


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ConnectorError('Refuse empty panel/support artifact')
    # Stable first-row order preserves the unit/year prefix used by metadata QA.
    fields = list(dict.fromkeys(key for record in rows for key in record))
    with path.open('x', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_panel(card: Mapping[str, Any], base_dir: Path, output: Path) -> dict[str, Any]:
    """Read declared local source files and create write-once panel artifacts.

    ``outcome`` fields: connector, paths, population_filters, years [first,last]
    (or period_years), units, frequency, and adapter-specific options. Aggregated
    CSV adapters require source_population_filters exactly matching the card.
    Returns counts, hashes, and paths only; no outcome summaries are returned.
    ``output`` is a directory, not an existing official result directory.
    """
    output = Path(output).resolve()
    destinations = [output / name for name in ('panel.csv', 'metadata.csv', 'CONSTRUCTION.json')]
    if any(path.exists() for path in destinations):
        raise FileExistsError('Panel artifacts are write-once; create a new spec-version directory')
    if any(part in ('l2_batch1', 'l2_batch2', 'l2_batch3', 'did_samples', 'insight', 'results') for part in output.parts):
        raise ConnectorError('Refuse construction output within a frozen batch or official results tree')
    if card['outcome'].get('connector') == 'opaque_state_year':
        from .opaque import build_opaque_panel
        return build_opaque_panel(dict(card), base_dir, output)
    # Prospective V6 cards must carry the same bounded windows into S2.
    from .coverage import legal_window_issues
    legal_issues=legal_window_issues(card)
    if legal_issues:
        raise ConnectorError('; '.join(legal_issues)+'; revise the card before S2')
    if card.get('automation',{}).get('coverage_window',{}).get('rule')=='coverage_bounded_windows_v6':
        from .coverage import coverage_bounded_card
        from .connectors import ConnectorNotReady
        # Validate the declared variant calendar; the parent card's retained
        # requested window must not expand a sensitivity or secondary here.
        import copy
        declared=copy.deepcopy(dict(card))
        declared['automation'].pop('coverage_window',None)
        bounded=coverage_bounded_card(declared)
        coverage=bounded['automation']['coverage_window']
        if coverage['parked']:
            raise ConnectorNotReady(coverage['reason'])
        if card['outcome'] != bounded['outcome']:
            raise ConnectorError('V6 outcome window changed; revise card before constructing panels')
    request = ConnectorRequest.from_card(card, base_dir)
    data = construct(request)
    from .outcome_transform import apply_log
    data = apply_log(card, data)
    row_keys = [(r['unit'], r['period']) for r in data.rows]
    meta_keys = [(r['unit'], r['period']) for r in data.metadata]
    if len(set(row_keys)) != len(row_keys):
        raise ConnectorError('Duplicate state-period panel key')
    if row_keys != meta_keys:
        raise ConnectorError('Panel and metadata inventories disagree')
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(destinations[0], data.rows)
    _write_csv(destinations[1], data.metadata)
    report: dict[str, Any] = {
        'design_id': card.get('design_id', card.get('id')),
        'connector': request.options['connector'], 'frequency': request.frequency,
        'population_filters': dict(request.filters), 'cells': len(data.rows),
        'available_cells': sum(int(r['available']) for r in data.metadata),
        'units': sorted(request.units), 'years': list(request.years),
        'panel_path': str(destinations[0]), 'panel_sha256': sha(destinations[0]),
        'metadata_path': str(destinations[1]), 'metadata_sha256': sha(destinations[1]),
        'source_files': list(data.inventory), 'limitations': list(data.limitations),
        'construction': dict(data.details), 'treatment_inputs_read': False,
        'outcome_association_calls': 0, 'new_downloads': 0,
        'state': 'constructed' if any(int(r['available']) for r in data.metadata) else 'parked_no_available_cells',
    }
    with destinations[2].open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report
