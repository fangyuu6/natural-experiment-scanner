"""Independent outcome registry/CLI, leaving the shared connector registry intact.

Run ``python -m scripts.l2_pipeline.connectors.outcome_v2 list``. Construction
reads declared local public files only; acquisition is a separate explicit step.
No policy, treatment, adoption, estimator, or association inputs are accepted.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData
from .outcome_v2_core import PublicClient
from . import outcome_v2_cdc, outcome_v2_labor, outcome_v2_acs

CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V2.json')
MODULES = {'cdc': outcome_v2_cdc, 'labor': outcome_v2_labor, 'acs': outcome_v2_acs}
REQUIRED = {'geography', 'years', 'units', 'population', 'release_lag', 'frequency', 'source_url'}


def catalog() -> dict:
    data = json.loads(CATALOG_PATH.read_text())
    if data.get('schema_version') != 2:
        raise ConnectorError('Expected outcome catalog schema_version=2')
    for series_id, spec in data['series'].items():
        if spec.get('adapter') not in MODULES or not REQUIRED <= spec.keys():
            raise ConnectorError('Incomplete outcome series metadata: ' + series_id)
    for spec in data['targets'].values():
        if not set(spec.get('series', [])) <= data['series'].keys():
            raise ConnectorError('Target references an unregistered outcome series')
    return data


def _adapter(series_id: str):
    spec = catalog()['series'].get(series_id)
    if spec is None:
        raise ConnectorNotReady('Unknown V2 outcome series: ' + str(series_id))
    return MODULES[spec['adapter']]


def construct(request: ConnectorRequest) -> PanelData:
    series_id = request.options.get('series_id')
    if not isinstance(series_id, str):
        raise ConnectorError('V2 construction requires outcome.series_id')
    if any(k in request.options for k in ('treatment', 'adoption', 'party', 'estimator')):
        raise ConnectorError('Outcome adapters accept outcome-only inputs')
    from .rates import apply_scale
    return apply_scale(request, _adapter(series_id).build(request))


def acquire(series_id: str, years, destination: Path, client=None, **options):
    """Explicit keyless acquisition; returns byte receipts, never panel estimates."""
    years = tuple(years)
    if not years or any(type(y) is not int for y in years) or tuple(sorted(set(years))) != years:
        raise ConnectorError('Acquisition years must be nonempty, unique, increasing integers')
    adapter = _adapter(series_id)
    import inspect
    allowed = inspect.signature(adapter.acquire).parameters
    if set(options) - set(allowed):
        raise ConnectorError('Acquisition options unavailable for this source family: '
                             + ', '.join(sorted(set(options) - set(allowed))))
    return adapter.acquire(series_id, years, Path(destination),
                           client=client or PublicClient(), **options)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='List series, coverage and first-order policy targets')
    fetch = sub.add_parser('acquire', help='Download public bytes with URL/size/license/SHA receipts')
    fetch.add_argument('--series', required=True)
    fetch.add_argument('--years', nargs=2, type=int, metavar=('FIRST', 'LAST'), required=True)
    fetch.add_argument('--destination', type=Path, required=True)
    fetch.add_argument('--source', choices=('flat', 'api_v1', 'summary', 'api'))
    fetch.add_argument('--units', nargs='+', help='Optional state abbreviations for BLS/legacy ACS acquisition')
    build = sub.add_parser('build', help='Construct a local panel without fitting associations')
    build.add_argument('--card', type=Path, required=True)
    build.add_argument('--base-dir', type=Path, default=Path.cwd())
    build.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'list':
            print(json.dumps(catalog(), indent=2))
        elif args.command == 'acquire':
            first, last = args.years
            if first > last:
                raise ConnectorError('FIRST must not exceed LAST')
            options = {k: getattr(args, k) for k in ('source', 'units') if getattr(args, k) is not None}
            print(json.dumps(acquire(args.series, range(first, last + 1), args.destination, **options), indent=2))
        else:
            card = json.loads(args.card.read_text())
            panel = construct(ConnectorRequest.from_card(card, args.base_dir))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x') as stream:
                json.dump(asdict(panel), stream, indent=2, allow_nan=False)
                stream.write('\n')
            print(json.dumps({'panel_path': str(args.output), 'rows': len(panel.rows),
                              'available': sum(int(r.get('available', 0)) for r in panel.metadata),
                              'outcome_association_calls': 0}))
    except (ConnectorError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
