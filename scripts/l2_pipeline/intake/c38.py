"""C38 answer-free V9 routing of contradicted/inconclusive C35 histories.

Use a new entry point: archived intake and sealed code bytes remain untouched.
Preparation never reads outcome panels, dispatches models, or certifies S1.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import REPO
from . import history_routing as generic
from scripts.l2_pipeline.agents.history import TEMPLATE
from scripts.l2_pipeline.storage import ensure_once, sha

C35 = REPO / 'data/l2_pipeline/sources/research_panel/c35_20261010'
ROOT = C35 / 'c38'
JOBS = ROOT / 'official_history_jobs'
ATTEMPT = 'c38'
SEATBELT = 'Does the state require the usage of seat belts (either primary or secondary enforcement)? (0 = no, 1 = yes)'
CLOCK = generic.CLOCK
ENFORCEMENT = generic.ENFORCEMENT
read = generic.read
binding = generic.binding

# Legacy compatibility only: approved C38 ledger owns this wave's roster.
# New designs enter history_routing, which has no policy allowlist.
ROUTES = {r['design_id']: (r['outcome_series_id'], r['calibration'], r['trigger'])
          for r in read(ROOT / 'C37_LEDGER_C38.json')['rows']
          if r['disposition'] == 'OFFICIAL_HISTORY_AB_PREFLIGHT_ONLY'}


def planning(card, universe):
    """Legacy C38 validation; generic planning preserves its exact packet bytes."""
    if card['design_id'] not in ROUTES or card['outcome']['series_id'] != ROUTES[card['design_id']][0]:
        raise ValueError('C38 route/outcome differs')
    if card['design_id'].endswith('mandatory_seatbelts') and card['mechanism'] != SEATBELT:
        raise ValueError('Seatbelt predicate cannot be changed to primary-only')
    return generic.planning(card, universe, attempt_id=ATTEMPT,
        enforcement_rule=ENFORCEMENT if card['design_id'].endswith('mandatory_seatbelts') else None)


def emit_pair(card, universe, *, root=ROOT):
    return generic.emit_pair(card, universe, root=root, attempt_id=ATTEMPT,
                             plan=planning(card, universe),legacy_contract=True)


def prepare():
    collection = read(C35 / 'c37_collection/COLLECT_REPORT.json')
    rows = {r['design_id']: r for r in collection['rows']}
    jobs, ledger = [], []
    for did, (outcome, verdict, trigger) in ROUTES.items():
        if rows[did]['calibration']['verdict'] != verdict or rows[did]['fresh_eligible']:
            raise ValueError('C37 verdict changed; routing requires review')
        card_path = C35 / 'cards' / (did + '.json')
        card = read(card_path)
        # Extract only the registered roster; no panel-derived answers enter jobs.
        roster_path = C35 / 'imports' / did / 'PANEL_IMPORT.json'
        universe = read(roster_path)['units']
        emitted = emit_pair(card, universe)
        jobs.extend(emitted)
        ledger.append(dict(design_id=did, disposition='OFFICIAL_HISTORY_AB_PREFLIGHT_ONLY',
            calibration=verdict, trigger=trigger, original_card=binding(card_path),
            mechanism=card['mechanism'], outcome_series_id=outcome, roster_source=binding(roster_path),
            job_paths=[j.job_path for j in emitted], s1_ready=False, execution_authorized=False))
    terminal = REPO / 'data/l2_pipeline/sources/research_panel/v8b_20261009/fresh_audit_jobs/confirmed_c10/c12/work/run8_gunshow_background_check/v13/TERMINAL.json'
    if read(terminal)['status'] != 'STOPPED':
        raise ValueError('Hub terminal duplicate binding changed')
    ledger.append(dict(design_id='c35_siegel_gunshowh', disposition='EXCLUDED_DUPLICATE_TERMINAL',
        duplicate_of='run8_gunshow_background_check', terminal=binding(terminal), terminal_status='STOPPED',
        hub_ruling='Same policy construct; run8 is terminal STOPPED (placebo). Changing to firearm homicide after the terminal result is a forking path; no replacement outcome or jobs.',
        emitted_jobs=0, execution_authorized=False))
    from scripts.l2_pipeline.agents.contract_tests import check_job_types
    contracts = check_job_types(jobs)
    ensure_once(ROOT / 'C37_LEDGER_C38.json', dict(previous_collection=binding(C35 / 'c37_collection/COLLECT_REPORT.json'),
        rows=ledger, contracts=contracts, s1_ready=False, execution_authorized=False, model_calls=0))
    report = dict(attempt_id=ATTEMPT, designs=len(ROUTES), jobs=len(jobs), job_paths=[j.job_path for j in jobs],
        roles=['A', 'B'], template=binding(TEMPLATE), model_calls=0, dispatched=False,
        s1_ready=False, execution_authorized=False, new_downloads=[])
    ensure_once(JOBS / 'EMISSION_REPORT.json', report)
    print(json.dumps(report, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare'])
    parser.parse_args(argv)
    return prepare()


if __name__ == '__main__':
    raise SystemExit(main())
