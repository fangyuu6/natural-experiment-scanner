"""Verify run4 bindings and stage boundaries without dispatch, downloads or fits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import published_study_intake as intake
from .paths import Run4Paths, REPO_ROOT
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.cli import preflight
from scripts.l2_pipeline.connectors.common import ConnectorRequest
from scripts.l2_pipeline.connectors.catalog import CONNECTOR_CATALOG, target_mapped
from scripts.l2_pipeline.connectors.cps import population as cps_population
from scripts.l2_pipeline.connectors.fars import _contract as fars_contract
from scripts.l2_pipeline.connectors.natality import _contract as natality_contract
from scripts.l2_pipeline.connectors.cps_nurse import population as nurse_population
from scripts.l2_pipeline.contracts import StageContext


def connector_contract(card):
    """Validate metadata only; never resolve outcome files or load values."""
    request = ConnectorRequest.from_card(card.payload, card.path.parent)
    name = request.options['connector']
    validators = {'fars_state': fars_contract, 'cdc_natality': natality_contract,
                  'cps_nurse_hours': nurse_population, 'cps_monthly': cps_population,
                  'cps_monthly_aggregates': cps_population}
    entry = CONNECTOR_CATALOG.get(name)
    if not entry or not entry['available']:
        raise ValueError('Unexpected prospective connector: ' + name)
    first_order = card.payload['first_order']
    if not target_mapped(name, first_order['target_outcome'], request.options['measure_id']):
        raise ValueError('Connector target/measure binding missing')
    if any(year < entry['coverage_start'] or year > entry['coverage_end']
           or year in entry['coverage']['excluded_years'] for year in request.years):
        raise ValueError('Outcome years extend beyond connector metadata coverage')
    if name in validators:
        validators[name](request)
    # Remaining catalog routes validate the shared first-order/coverage metadata
    # here. Physical source/population contracts remain the guarded S2 stage.


def run(paths=None, *, output_dir=None, stages=('S0', 'S1'), max_concurrency=4,
        check_optimized=True):
    paths = paths or Run4Paths()
    destination = Path(output_dir).resolve() if output_dir else paths.jobs_root
    normal = intake.verify_all(paths)
    checks = []
    before = set(paths.jobs_root.rglob('DISPATCH_ONCE.json'))
    for path in sorted(paths.cards_dir.glob('*.json')):
        card = load_card(path)
        if card.payload['treatment'].get('source_type') != 'published_did_study':
            continue
        context = StageContext(card, paths.jobs_root / 'work', paths.repo_root)
        preflight(context, 'S1')
        connector_contract(card)
        checked = intake.verify_literature(path, paths)
        if not checked['s1_ready']:
            try:
                preflight(context, 'S2')
            except ValueError as error:
                if 'Missing prior stage receipt: S1' not in str(error):
                    raise
            else:
                raise ValueError('Premature S2 route accepted')
            auditor = paths.jobs_root / 'S1' / card.design_id / 'auditor.md'
            # When an original exists, collection is an explicit later action.
            # A dry run never promotes submitted evidence or writes S1 receipts.
            output = intake.load_job(auditor).output_path
            if not paths.reference(output).is_file():
                parked = intake.collect_audit(auditor, paths)
                if parked.get('status') != 'PARK' or parked.get('s1_ready') is not False:
                    raise ValueError('Missing audit failed open')
        checks.append({'design_id': card.design_id, 's0_prior_receipt_valid': True,
                       'connector_contract_valid_without_loading_data': True,
                       's2_blocked_until_s1': not checked['s1_ready'],
                       's1_ready': checked['s1_ready']})
    try:
        intake.verify_literature(paths.cards_dir.parent / 'outside-card.json', paths)
    except ValueError:
        pass
    else:
        raise ValueError('Outside run4 card path accepted')
    commands = []
    for stage in stages:
        argv = [sys.executable, '-B', '-m', 'scripts.l2_pipeline.agents.dispatch',
                '--jobs-root', str(paths.jobs_root), '--stage', stage,
                '--max-concurrency', str(max_concurrency), '--dry-run']
        result = subprocess.run(argv, cwd=paths.repo_root, text=True,
                                capture_output=True, check=False)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / ('DRY_RUN_' + stage + '.log')).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError('Dispatcher dry-run failed ' + stage + ': ' + result.stderr)
        commands.append({'scope': stage, 'argv': argv, 'exit_code': result.returncode,
                         'printed_job_commands': len(result.stdout.splitlines()),
                         'help_only_cli_preflight': True, 'model_calls': 0})
    if check_optimized:
        argv = [sys.executable, '-B', '-O', '-m',
                'scripts.l2_pipeline.run4_adapters.published_study_intake', '--verify',
                '--repo-root', str(paths.repo_root), '--cards-dir', str(paths.cards_dir),
                '--jobs-root', str(paths.jobs_root)]
        optimized = subprocess.run(argv, cwd=REPO_ROOT, text=True,
                                   capture_output=True, check=False)
        if optimized.returncode or json.loads(optimized.stdout) != normal:
            raise ValueError('Optimized metadata validation mismatch: ' + optimized.stderr)
    if set(paths.jobs_root.rglob('DISPATCH_ONCE.json')) != before:
        raise ValueError('Dry run created an unexpected dispatch reservation')
    report = {'status': 'PASS_FOR_EMITTED_PACKETS_TARGETS_INCOMPLETE',
              'metadata': normal, 'checks': checks, 'dry_runs': commands,
              'optimized_validation_checked': check_optimized,
              'outside_task_path_rejected': True, 'actual_dispatches': 0,
              'pipeline_model_calls': 0, 'outcome_association_calls': 0,
              'outcome_dataset_downloads': 0, 'new_reservations': 0,
              'new_s1_readiness_receipts': 0}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'DRY_RUN_REPORT.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=REPO_ROOT)
    parser.add_argument('--cards-dir', type=Path)
    parser.add_argument('--jobs-root', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--stage', action='append', choices=('S0L', 'S0', 'S1'))
    parser.add_argument('--max-concurrency', type=int, default=4)
    args = parser.parse_args(argv)
    report = run(Run4Paths(args.repo_root, args.cards_dir, args.jobs_root),
                 output_dir=args.output_dir, stages=args.stage or ('S0', 'S1'),
                 max_concurrency=args.max_concurrency)
    print(json.dumps({'dry_run_jobs': sum(item['printed_job_commands'] for item in report['dry_runs']),
                      'status': report['status'], 'model_calls': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
