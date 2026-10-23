"""Emit immutable run2 forecast/review packets; never dispatch or estimate.

Downstream packets retain canonical stage collection locations. Nurse outputs and
collection promotions live in its separate job root, with a one-design allowlist.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import time

from .agents.jobs import AgentJob, emit_stage_jobs, load_job
from .cards import load_card
from .contracts import StageContext
from .storage import ensure_once, public_input, sha

REPO = Path(__file__).resolve().parents[2]
RUN2 = REPO / 'data/l2_pipeline/jobs/run2'
DOWNSTREAM_ROOT = RUN2 / 'downstream_jobs'
NURSE_ROOT = RUN2 / 'nurse_sealed'
NURSE_ID = 'run1_nurse_overtime'
NURSE_V5 = REPO / 'data/l2_pipeline/jobs/run1/_v5'


def _text_once(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != text:
            raise ValueError('Existing packet changed: ' + str(path))
    else:
        with path.open('x') as stream:
            stream.write(text)


def _root(path: Path, repo: Path) -> Path:
    path = public_input(path)
    allowed = (repo / 'data/l2_pipeline/jobs/run2', Path(tempfile.gettempdir()).resolve(), Path('/private/tmp'))
    if not any(path.is_relative_to(p.resolve()) for p in allowed):
        raise ValueError('Run2 job outputs must remain in run2 or scratch')
    return path


def _retarget(job: AgentJob, root: Path, attempt_id: str | None) -> AgentJob:
    """Reissue a packet with every output reference and sidecar binding updated."""
    target = root / job.stage / job.design_id / Path(job.job_path).name
    work = root / 'work' / job.design_id / job.design_version
    if job.stage == 'S5':
        canonical = work / 'predictions' / job.role
        out = canonical / 'attempts' / attempt_id if attempt_id else canonical
        promote = canonical / 'submission.json'
    else:
        out = work / 'reviewer'
        if attempt_id:
            out = out / 'attempts' / attempt_id
        promote = work / ('EXECUTION_REVIEW.json' if job.contract['review_scope'] == 'sealed_candidate'
                          else 'REVIEW_DRAFT' + ('_' + attempt_id if attempt_id else '') + '.json')
    output = out / Path(job.output_path).name
    contract = dict(job.contract)
    if job.stage == 'S5' and attempt_id:
        contract.update(canonical_collection_path=str(canonical / 'COLLECTION.json'),
                        canonical_normalization_path=str(canonical / 'NORMALIZATION.json'))
    contract['task_owned_output_root'] = str(root)
    rebound = replace(job, job_path=str(target), job_sha256='', output_dir=str(out),
                      output_path=str(output), result_path=str(out / 'codex_result.txt'),
                      promote_path=str(promote), contract=contract)
    text = Path(job.job_path).read_text()
    binding_marker = '## Execution metadata binding (data)\n```json\n'
    prior_binding=json.loads(text.split(binding_marker,1)[1].split('\n```',1)[0])
    if prior_binding.get('projection')=='review_transport_reference_v1':
        # Large S6 packets bind a separate transport; retarget both immutable
        # resources together instead of assuming an inline contract block.
        old_transport=Path(prior_binding['path']);new_transport=target.with_suffix('.review-inputs.json')
        contract['permitted_metadata_paths']=[str(new_transport) if p==str(old_transport) else p
                                              for p in contract['permitted_metadata_paths']]
        rebound=replace(rebound,contract=contract)
        transport=json.loads(old_transport.read_text())
        binding=asdict(rebound);binding.pop('job_sha256')
        transport['binding']=binding
        ensure_once(new_transport,transport)
        text=(text.replace(job.output_path,str(output)).replace(job.output_dir,str(out))
              .replace(job.job_path,str(target)).replace(str(old_transport),str(new_transport))
              .replace(prior_binding['sha256'],sha(new_transport)))
        _text_once(target,text)
        rebound=replace(rebound,job_sha256=sha(target))
        ensure_once(target.with_suffix('.job.json'),asdict(rebound))
        return load_job(target)
    body = text.split(binding_marker, 1)[0]
    body = body.replace(job.output_path, str(output)).replace(job.output_dir, str(out))
    contract_marker = '## Binding contract (data)\n```json\n'
    before, after = body.split(contract_marker, 1)
    _, tail = after.split('\n```', 1)
    body = before + contract_marker + json.dumps(contract, indent=2, sort_keys=True) + '\n```' + tail
    binding = asdict(rebound)
    binding.pop('job_sha256')
    text = body + binding_marker + json.dumps(binding, indent=2, sort_keys=True) + '\n```\n'
    _text_once(target, text)
    rebound = replace(rebound, job_sha256=sha(target))
    ensure_once(target.with_suffix('.job.json'), asdict(rebound))
    return load_job(target)


def _emit(ctx: StageContext, stage: str, root: Path, *, allow_draft=False, attempt_id=None,
          isolate_outputs=False) -> list[AgentJob]:
    """Use the established scientific contracts without emitting canonical outputs."""
    root = _root(root, ctx.repo)
    if not isolate_outputs:
        return emit_stage_jobs(ctx, stage, jobs_root=root, allow_draft=allow_draft, attempt_id=attempt_id)
    with tempfile.TemporaryDirectory(prefix='run2-packets-') as scratch:
        jobs = emit_stage_jobs(ctx, stage, jobs_root=Path(scratch).resolve(),
                               allow_draft=allow_draft, attempt_id=attempt_id)
        return [_retarget(job, root, attempt_id) for job in jobs]


def s4_ready(ctx: StageContext) -> tuple[bool, str]:
    receipts = sorted((ctx.design_dir / 'receipts/S4').glob('*.json'))
    if not receipts:
        return False, 'No S4 receipt; forecasts require a completed registered draft'
    receipt = json.loads(receipts[-1].read_text())
    if receipt.get('card_sha256') != sha(ctx.card.path):
        return False, 'S4 receipt does not bind the current source card'
    result = receipt.get('result', {})
    if result.get('disposition') != 'ready':
        return False, 'S4 is ' + str(result.get('disposition')) + ': ' + str(result.get('reason'))
    spec_path = ctx.design_dir / 'SPEC.json'
    if not spec_path.is_file():
        return False, 'S4 receipt has no registered SPEC.json'
    spec = json.loads(spec_path.read_text())
    if (spec.get('design_id'), spec.get('version'), spec.get('card_sha256')) != (ctx.card.design_id, ctx.card.version, sha(ctx.card.path)):
        return False, 'S4 specification identity/card binding differs'
    if spec.get('support', {}).get('primary_passed') is not True:
        return False, 'S4 primary metadata support did not pass'
    if (ctx.design_dir / 'FREEZE_FINAL.json').exists():
        return False, 'Design already has a final freeze; run2 emission is draft readiness only'
    return True, 'S4 registered draft and primary support passed'


def emit_downstream_jobs(ctx: StageContext, *, jobs_root: Path | None = None) -> dict:
    root = _root(jobs_root or DOWNSTREAM_ROOT, ctx.repo)
    ready, reason = s4_ready(ctx)
    if not ready:
        return dict(design_id=ctx.card.design_id, version=ctx.card.version, disposition='PARKED', reason=reason, jobs=[])
    from .forecast_slots import roster
    expected_personas=roster(ctx)
    stage_times = {}
    start = time.perf_counter()
    forecast = _emit(ctx, 'S5', root)
    stage_times['S5_job_emission'] = time.perf_counter() - start
    start = time.perf_counter()
    reviews = _emit(ctx, 'S6', root, allow_draft=True, attempt_id='run2-readiness')
    stage_times['S6_draft_job_emission'] = time.perf_counter() - start
    if [job.role for job in forecast] != expected_personas:
        raise ValueError('Forecast packets differ from the retained or prospective persona roster')
    jobs = forecast + reviews
    stable = dict(schema='run2_downstream_jobs_v1', design_id=ctx.card.design_id, version=ctx.card.version,
                  disposition='READY', reason=reason, spec_path=str(ctx.design_dir / 'SPEC.json'),
                  spec_sha256=sha(ctx.design_dir / 'SPEC.json'), forecasts=len(expected_personas), draft_reviewers=1,
                  execution_authorized=False, dispatched=False,
                  jobs=[dict(stage=j.stage, role=j.role, job_path=j.job_path, job_sha256=j.job_sha256,
                             output_dir=j.output_dir, promote_path=j.promote_path) for j in jobs])
    ensure_once(root / 'manifests' / ctx.card.design_id / ctx.card.version / 'JOBS.json', stable)
    return dict(stable, stage_timings_seconds=stage_times)


def emit_ready_designs(*, workspace: Path = RUN2 / 'work', jobs_root: Path = DOWNSTREAM_ROOT,
                       design_ids: list[str] | None = None, repo: Path = REPO) -> dict:
    results = []
    cards = sorted(workspace.glob('*/*/CARD.json'))
    selected = set(design_ids or [])
    found = set()
    for path in cards:
        identity = path.parent.parent.name
        version = path.parent.name
        if selected and identity not in selected:
            continue
        found.add(identity)
        start = time.perf_counter()
        try:
            lock = path.parent / 'LITERATURE_IMPORT_LOCK.json'
            source_path = Path(json.loads(lock.read_text())['card_path']) if lock.is_file() else path
            card = load_card(source_path)
            ctx = StageContext(card, workspace, repo)
            ready, reason = s4_ready(ctx)
            if not ready:
                results.append(dict(design_id=identity, version=version, disposition='PARKED', reason=reason,
                                    jobs=[], elapsed_seconds=time.perf_counter() - start))
                continue
            # The literature source card is the S0/S4 hash authority. Rehydrate
            # source-specific prospective preparation without changing its bytes.
            from .run2_preparation import prepare_context
            ctx = prepare_context(ctx)
            results.append(emit_downstream_jobs(ctx, jobs_root=jobs_root))
        except Exception as exc:
            results.append(dict(design_id=identity, version=version, disposition='FAILED',
                                reason=type(exc).__name__ + ': ' + str(exc), jobs=[]))
        results[-1]['elapsed_seconds'] = time.perf_counter() - start
    if selected - found:
        raise ValueError('Unknown design id(s): ' + ', '.join(sorted(selected - found)))
    return dict(results=results, counts={state:sum(r['disposition'] == state for r in results)
                                       for state in ('READY', 'PARKED', 'FAILED')},
                model_calls=0, dispatch_calls=0, actual_data_estimation_calls=0)


def verify_root_seal(root: Path) -> dict:
    root = public_input(root)
    seal = json.loads((root / 'JOB_ROOT_SEAL.json').read_text())
    paths = sorted(root.glob('*/*/*.md'))
    if (seal.get('schema') != 'run2_nurse_job_root_seal_v1'
            or seal.get('design_allowlist') != [NURSE_ID] or seal.get('stages') != ['S6']
            or seal.get('execution_authorized') is not False or len(paths) != 1):
        raise ValueError('Nurse sealed root must contain exactly one unauthorized S6 review')
    if [str(p.relative_to(root)) for p in paths] != sorted(seal['jobs']):
        raise ValueError('Sealed root contains unlisted or missing jobs')
    for rel, record in seal['jobs'].items():
        path = root / rel
        job = load_job(path)
        if job.design_id != NURSE_ID or job.stage != 'S6' or job.contract.get('review_scope') != 'sealed_candidate':
            raise ValueError('Nurse-only sealed root identity differs')
        if job.contract.get('candidate_sha256') != seal['sealed_candidate_sha256']:
            raise ValueError('Sealed root candidate binding differs')
        if sha(path) != record['job_sha256'] or sha(path.with_suffix('.job.json')) != record['sidecar_sha256']:
            raise ValueError('Sealed root job bytes changed')
        for field in ('output_dir', 'promote_path'):
            if not Path(getattr(job, field)).is_relative_to(root):
                raise ValueError('Sealed root output escapes task root')
    for path, digest in seal['originals'].items():
        if sha(public_input(Path(path))) != digest:
            raise ValueError('Original nurse input/job changed')
    return dict(status='PASS', jobs=len(paths), designs=[NURSE_ID], execution_authorized=False)


def emit_nurse_sealed(*, jobs_root: Path = NURSE_ROOT, nurse_v5: Path = NURSE_V5, repo: Path = REPO,
                      design_id: str = NURSE_ID) -> dict:
    if design_id != NURSE_ID:
        raise ValueError('Nurse sealed root accepts run1_nurse_overtime only')
    root = _root(jobs_root, repo)
    expected = Path('S6') / NURSE_ID / 'reviewer.run2-nurse-sealed.md'
    if any(path.relative_to(root) != expected for path in root.glob('*/*/*.md')):
        raise ValueError('Nurse sealed root already contains an unrelated job')
    ctx = StageContext(load_card(nurse_v5 / 'cards' / (NURSE_ID + '.json')), nurse_v5 / 'work', repo)
    if ctx.card.design_id != NURSE_ID:
        raise ValueError('Nurse input card identity differs')
    candidate = ctx.design_dir / 'FREEZE_FINAL.json'
    if not candidate.is_file() or not (ctx.design_dir / 'PREDICTIONS_LOCK.json').is_file():
        raise ValueError('Nurse final candidate and predictions lock are required')
    original_job = nurse_v5 / 'sealed_jobs/S6' / NURSE_ID / 'reviewer.20261007-sealed.md'
    originals = {str(p):sha(p) for p in [candidate, ctx.card.path, original_job, original_job.with_suffix('.job.json')]}
    jobs = _emit(ctx, 'S6', root, attempt_id='run2-nurse-sealed', isolate_outputs=True)
    for job in jobs:
        if job.contract['review_scope'] != 'sealed_candidate' or job.contract['candidate_sha256'] != originals[str(candidate)]:
            raise ValueError('Nurse reissue changed the frozen candidate')
    seal = dict(schema='run2_nurse_job_root_seal_v1', design_allowlist=[NURSE_ID], stages=['S6'],
                sealed_candidate_sha256=originals[str(candidate)], originals=originals,
                jobs={str(Path(j.job_path).relative_to(root)):dict(job_sha256=j.job_sha256,
                       sidecar_sha256=sha(Path(j.job_path).with_suffix('.job.json'))) for j in jobs},
                execution_authorized=False, final_execution_approval='UNSIGNED', dispatched=False,
                notes='Fresh nurse-only review execution bindings; original V51 candidate and shared jobs preserved')
    ensure_once(root / 'JOB_ROOT_SEAL.json', seal)
    bridge = dict(schema='run2_nurse_review_handoff_v1', design_id=NURSE_ID,
                  candidate_sha256=originals[str(candidate)],
                  collect_job_path=jobs[0].job_path, reviewed_artifact_path=jobs[0].promote_path,
                  canonical_execution_review_path=str(ctx.design_dir / 'EXECUTION_REVIEW.json'),
                  canonical_candidate_path=str(candidate), canonical_candidate_sha256=originals[str(candidate)],
                  action='After independent sealed review is collected, hub may import the validated review into the original V51 registration workflow. No original review is written by this emitter.',
                  final_execution_approval='UNSIGNED', execution_authorized=False)
    ensure_once(root / 'REVIEW_HANDOFF.json', bridge)
    return dict(verify_root_seal(root), job_paths=[j.job_path for j in jobs],
                candidate_sha256=originals[str(candidate)], originals_preserved=True, dispatched=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('downstream', 'nurse-sealed', 'verify-nurse-seal'), required=True)
    parser.add_argument('--design-id', action='append')
    parser.add_argument('--work-dir', type=Path, default=RUN2 / 'work')
    parser.add_argument('--jobs-root', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'downstream':
        result = emit_ready_designs(workspace=args.work_dir, jobs_root=args.jobs_root or DOWNSTREAM_ROOT, design_ids=args.design_id)
    else:
        if args.design_id and args.design_id != [NURSE_ID]:
            parser.error('Nurse actions accept --design-id run1_nurse_overtime only')
        root = args.jobs_root or NURSE_ROOT
        result = emit_nurse_sealed(jobs_root=root) if args.action == 'nurse-sealed' else verify_root_seal(root)
    print(json.dumps(result, indent=2))
    return int(bool(result.get('counts', {}).get('FAILED')))


if __name__ == '__main__':
    raise SystemExit(main())
