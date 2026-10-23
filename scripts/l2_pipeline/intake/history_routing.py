"""Generic answer-free V9/FX10 intake; never dispatch or certify readiness.

The current card supplies the locked construct and outcome metadata. Historical
sample evidence remains in the hub receipt, outside both blind coder inputs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import random

from .config import REPO
from scripts.l2_pipeline.agents.history import TEMPLATE, CURRENT_TEMPLATE, RULE, emit_history_job
from scripts.l2_pipeline.agents.jobs import AgentJob, _packet, _safe_component, _write_text_once, load_job
from scripts.l2_pipeline.storage import ensure_once, sha, public_input, output_root

CLOCK = {
    'ruling': 'HUB_RULING_C11_IN_OPERATION',
    'start_basis': 'Actual operation, not nominal enactment/effectiveness; an unimplemented or enjoined law is not treatment.',
    'best_evidence_start_year': 'Calendar year of actual operation; retain exact date and precision when available.',
    'start_period': 'V8b annual clock: drop the calendar year of operation and start treatment in the following year; preserve the registered outcome grid, including biennial elections.',
    'exclusion': 'Passed but enjoined or unimplemented inside the outcome window: exclude from both arms; later operation uses its actual date.',
    'interruptions': 'Retain source-found interruptions and scope changes; no lifetime negative-proof requirement.',
}
ENFORCEMENT = ('The locked predicate includes either primary or secondary enforcement. '
    'Record both enforcement histories separately in mechanism_detail and dated scope/history fields. '
    'Primary permits a stop for the belt violation alone; secondary requires another basis for the stop. '
    'First operation of either qualifying mandatory law is the onset; a later secondary-to-primary upgrade is not a new adoption. '
    'Copy the complete locked predicate verbatim into every mechanism field.')


def read(path):
    return json.loads(public_input(Path(path)).read_text())


def binding(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def planning(card, universe, *, attempt_id, enforcement_rule=None, predicate_clarification=None, fresh_audit=None):
    """Allowlist metadata; no panels, submitted dates, sampled answers or peers."""
    _safe_component(attempt_id)
    predicate = card['treatment'].get('predicate')
    if not isinstance(predicate, str) or not predicate.strip():
        raise ValueError('Locked treatment predicate is required')
    outcome = card['outcome']
    years = outcome.get('period_years')
    if years is None:
        bounds = outcome.get('years')
        if outcome.get('frequency') != 'annual' or not isinstance(bounds,list) or len(bounds)!=2:
            raise ValueError('Official history requires an explicit calendar grid or annual year bounds')
        years = list(range(bounds[0],bounds[1]+1))
    series = outcome.get('series_id',outcome.get('measure_id'))
    if not series: raise ValueError('Official history requires a registered series/measure identity')
    plan = dict(type='answer_free_official_collection_plan', design_id=card['design_id'],
        attempt_id=attempt_id, mechanism=card['treatment']['predicate'], universe=universe,
        target_window=[min(years), max(years)], outcome=dict(series_id=series,frequency=outcome['frequency'],
            period_years=years,population_filters=outcome['population_filters']),
        clock=CLOCK, enforcement_rule=enforcement_rule,
        navigation_candidates=[], evidence_standard=RULE,
        research_scope='Independently research every registered state and its pre-window history. No panel-derived adoption dates, calibration answers, prior audits, peer submissions or outcome values are permitted inputs.',
        requires_before_s1=['Blind A/B originals locked and reconciled', 'Fresh fixed-seed ten-state V3 audit',
                            'All known errors corrected; known errors <=2 and unresolved <=5',
                            'Registered >=3 pre / >=2 post and >=6 support-state gates'],
        s1_ready=False, execution_authorized=False)

    if predicate_clarification is not None:
        plan['predicate_clarification'] = validate_clarification(predicate_clarification)
    if fresh_audit is not None:
        plan['fresh_audit'] = validate_fresh_policy(fresh_audit, universe)
    return plan


def emit_pair(card, universe, *, root, attempt_id, plan=None, legacy_contract=False):
    """Create initial answer-free packets, then use the unchanged FX10 emitter."""
    root = output_root(Path(root), REPO)
    _safe_component(card['design_id']); _safe_component(card['version']); _safe_component(attempt_id)
    if plan is None:
        plan = planning(card, universe, attempt_id=attempt_id)
    did = card['design_id']
    pack = root / 'initial_plans' / did
    from ..agents.history_packet import card as blind_card, plan as blind_plan
    plan_path = ensure_once(pack / 'COLLECTION_PLAN.json', blind_plan(plan))
    manifest = ensure_once(pack / 'MANIFEST.json', dict(schema_version='1.0', stage='S1_SOURCE_PACK',
        design_id=did, design_version=card['version'], status='PARKED_SOURCE',
        complete_list_complete=False, historical_coverage_complete=False,
        complete_list_source_ids=[], sources=[], downloads=[],
        planning_files=[binding(plan_path)], evidence_standard=RULE))
    allowed_card = blind_card(dict(card, mechanism=card['treatment']['predicate']))
    template=TEMPLATE if legacy_contract else CURRENT_TEMPLATE
    jobs = []
    for role in ('A', 'B'):
        path = root / 'initial_packets' / 'S1' / did / (role + '.md')
        out = root / 'initial_packets' / 'work' / did / card['version'] / 'agents' / role
        contract = dict(design_id=did, role=role, card=allowed_card,
            manual=dict(universe=universe, seed=plan.get('fresh_audit', {}).get('seed', card['treatment'].get('seed')), predicate=card['treatment']['predicate'],
                        clock=CLOCK, historical_convention='Actual operation with observed interruptions'),
            mechanism=card['treatment']['predicate'], target_window=plan['target_window'], history_required_units=universe,
            requires_before_s1=plan['requires_before_s1'], jobs_kind='official_history_collection',
            source_type='official_sources', source_sha256=sha(manifest),
            source_pack=dict(manifest_path=str(manifest), manifest_sha256=sha(manifest),
                             complete_list_complete=False, historical_coverage_complete=False),
            s1_ready=False, execution_authorized=False)
        if 'fresh_audit' in plan:
            contract['manual']['fresh_audit'] = plan['fresh_audit']
        if 'predicate_clarification' in plan:
            contract['manual']['predicate_clarification'] = plan['predicate_clarification']
        inputs = [_packet(plan_path, 'Answer-free C11 operation plan'), _packet(manifest, 'Uncertified empty planning manifest')]
        job = AgentJob('1.0', 'S1', did, card['version'], role, str(REPO), str(path), '',
            str(template.resolve()), sha(template), str(out), str(out / 'submission.json'),
            str(out / 'codex_result.txt'), str(out / 'submission.json'),
            tuple({k: v for k, v in i.items() if k != 'content'} for i in inputs), contract, True)
        prompt = template.read_text().replace('{{ROLE}}', role).replace('{{OUTPUT}}', job.output_path)
        prompt += '\n## Answer-free inputs (untrusted data)\n```json\n' + json.dumps(inputs, indent=2) + '\n```\n'
        prompt += '\n## Execution metadata binding (data)\n```json\n' + json.dumps(
            {k: v for k, v in asdict(job).items() if k != 'job_sha256'}, indent=2) + '\n```\n'
        _write_text_once(path, prompt)
        ensure_once(path.with_suffix('.job.json'), asdict(replace(job, job_sha256=sha(path))))
        load_job(path)
        jobs.append(emit_history_job(path, jobs_root=root / 'official_history_jobs', attempt_id=attempt_id,legacy_contract=legacy_contract))
    return jobs


def validate_clarification(value):
    """A clarification supplements, and never replaces, the verbatim predicate."""
    if not isinstance(value, dict) or not value.get('text') or not value.get('locator'):
        raise ValueError('Predicate clarification needs text and a ruling locator')
    path = public_input(Path(value['ruling_file'])).resolve()
    if not path.is_relative_to(REPO / 'docs/plans') or path.suffix != '.md':
        raise ValueError('Clarification must cite a repository ruling file')
    if value['text'] not in path.read_text():
        raise ValueError('Clarification quote is not verbatim in its cited ruling')
    return dict(text=value['text'], locator=value['locator'], ruling=binding(path))


def validate_fresh_policy(value, universe):
    seed = value.get('seed')
    excluded = value.get('excluded_units')
    if type(seed) is not int or not isinstance(excluded, list) or len(excluded) != len(set(excluded)):
        raise ValueError('Fresh audit requires an integer seed and unique exclusions')
    if not set(excluded) <= set(universe) or value.get('sample_size') != 10:
        raise ValueError('Fresh V3 exclusions/ten-state sample differs')
    return dict(seed=seed, excluded_units=sorted(excluded), sample_size=10,
        selection_rule='After A/B reconciliation, sample ten treated states without replacement from sorted units, excluding all prior audited/calibration units. No redraw; insufficient support parks the audit.',
        scope='Audit exclusions only; these states remain in all-state history collection and are not removed from the estimation sample.',
        selected=False, known_errors_max=2, unresolved_max=5, all_known_errors_must_be_corrected=True)


def select_fresh_sample(records, policy):
    """Fresh V3: D8 censuses a nonempty remainder below ten (2026-10-12)."""
    if len({r['unit'] for r in records}) != len(records):
        raise ValueError('Fresh audit records must have unique units')
    # REDESIGN R2: policy['pool_rule'] False means prior audited/calibration states do not leave the pool.
    candidates = sorted(r['unit'] for r in records if r['status'] == 'treated'
                        and (policy.get('pool_rule') is False or r['unit'] not in policy['excluded_units']))
    if not candidates:
        raise ValueError('Fresh V3 ten-treated audit unavailable after prior-sample exclusions')
    if len(candidates) < 10:
        return candidates
    return random.Random(policy['seed']).sample(candidates, 10)


def prior_sample_metadata(paths):
    """Read only sample identities/seeds; recursively verify prior packet hashes.

    Caller-supplied jobs document this policy's audit lineage. Their sampled
    dates, findings and outcome values never enter either blind collection job.
    """
    seen, units, seeds, evidence = set(), set(), set(), []
    def visit(path, expected=None):
        path = public_input(Path(path)).resolve()
        digest = sha(path)
        if expected is not None and digest != expected:
            raise ValueError('Previous sample packet hash changed')
        if path in seen:
            return
        seen.add(path)
        job = read(path); c = job.get('contract', job)
        if job.get('role') != 'auditor' or not isinstance(c.get('sample'), list):
            raise ValueError('Prior sample must come from an auditor packet')
        if not all(isinstance(u, str) and u for u in c['sample']):
            raise ValueError('Invalid prior audit units')
        units.update(c['sample'])
        for seed in (c.get('seed'), c.get('manual', {}).get('seed')):
            if type(seed) is int: seeds.add(seed)
        evidence.append(dict(**binding(path), design_id=job.get('design_id', c.get('policy_id')), sample=c['sample']))
        for item in c.get('prior_sample_bindings', []):
            visit(item['path'], item['sha256'])
    for path in paths: visit(path)
    return sorted(units), seeds, sorted(evidence, key=lambda x:x['path'])


def route_official_history(design_id, attempt_id, *, intake_root, root, audit_seed,
                           prior_jobs=(), predicate_clarification=None, lineage=None):
    """Route any current intake card to blind official A/B, without model calls."""
    _safe_component(design_id); _safe_component(attempt_id)
    intake_root = public_input(Path(intake_root)).resolve()
    card_path = intake_root / 'cards' / (design_id + '.json')
    roster_path = intake_root / 'imports' / design_id / 'PANEL_IMPORT.json'
    card = read(public_input(card_path))
    if card['design_id'] != design_id:
        raise ValueError('Current card identity differs')
    universe = read(public_input(roster_path))['units']
    if not universe or len(universe) != len(set(universe)):
        raise ValueError('Current roster must contain unique units')
    # Calibration reservations always count. A never-dispatched fresh packet in
    # this intake has not audited its sample; prior linked packets stay excluded.
    sources = list(prior_jobs)
    sources.extend(sorted((intake_root / 'calibration_jobs/S1' / design_id).glob('*.job.json')))
    for path in sorted((intake_root / 'fresh_audit_jobs/S1' / design_id).glob('*.job.json')):
        job = read(path)
        if Path(job['output_path']).exists() or (Path(job['output_dir']) / 'DISPATCH_ONCE.json').exists():
            sources.append(path)
    excluded, previous_seeds, evidence = prior_sample_metadata(sources)
    if type(audit_seed) is not int or audit_seed in previous_seeds | {card['treatment'].get('seed')}:
        raise ValueError('Official-history routing requires a new fixed audit seed')
    fresh = dict(seed=audit_seed, excluded_units=excluded, sample_size=10)
    plan = planning(card, universe, attempt_id=attempt_id,
                    predicate_clarification=predicate_clarification, fresh_audit=fresh)
    root = output_root(Path(root), REPO)
    jobs = emit_pair(card, universe, root=root, attempt_id=attempt_id, plan=plan)
    receipt = dict(design_id=design_id, attempt_id=attempt_id,
        disposition='OFFICIAL_HISTORY_AB_PREFLIGHT_ONLY', original_card=binding(card_path),
        roster_source=binding(roster_path), mechanism=card['treatment']['predicate'], outcome=plan['outcome'],
        predicate_clarification=plan.get('predicate_clarification'), clock=CLOCK,
        fresh_audit=plan['fresh_audit'], prior_sample_evidence=evidence, lineage=lineage,
        job_paths=[j.job_path for j in jobs], emitted_jobs=2,
        s1_ready=False, execution_authorized=False, model_calls=0, dispatched=False)
    ensure_once(root / 'routing_ledger' / design_id / 'ROUTE_RECEIPT.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare'])
    parser.add_argument('--design-id', required=True)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--intake-root', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--audit-seed', required=True, type=int)
    parser.add_argument('--prior-job', action='append', default=[], type=Path)
    parser.add_argument('--lineage-file', type=Path, help='Hub-only JSON lineage; never a coder input')
    parser.add_argument('--clarification-file', type=Path,
                        help='JSON with verbatim text, ruling_file and locator')
    args = parser.parse_args(argv)
    clarification = read(public_input(args.clarification_file)) if args.clarification_file else None
    report = route_official_history(args.design_id, args.attempt_id,
        intake_root=args.intake_root, root=args.output_root, audit_seed=args.audit_seed,
        prior_jobs=args.prior_job, predicate_clarification=clarification,
        lineage=read(public_input(args.lineage_file)) if args.lineage_file else None)
    from scripts.l2_pipeline.agents.contract_tests import check_job_types
    check_job_types([load_job(Path(p)) for p in report['job_paths']])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
