"""One host entry point for isolated L2 waves, collection and final signatures.

Examples (repository root; dispatcher on the host):
  python -B -m scripts.l2_pipeline.hub status --batch CARDS --work-dir WORK
  python -B -m scripts.l2_pipeline.hub prepare S1 --batch CARDS --work-dir WORK --jobs-root JOBS
  python -B -m scripts.l2_pipeline.hub dispatch S1 --jobs-root JOBS --max-concurrency 4 --hub-outside-sandbox
  python -B -m scripts.l2_pipeline.hub collect --jobs-root JOBS --stage S1
  python -B -m scripts.l2_pipeline.hub approve-run --only-pass --batch CARDS --work-dir WORK --signer HUB --signed-at UTC
  python -B -m scripts.l2_pipeline.hub run S6 --batch CARDS --work-dir WORK

--max-concurrency is always 1..4. prepare emits jobs without model calls;
approve-run signs only, and run is a separate explicit execution command.
Each design/job retains its own failure; failed neighbours never cancel it.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.l2_pipeline import registration
from scripts.l2_pipeline.agents import collect_job, emit_stage_jobs, load_job
from scripts.l2_pipeline.agents import dispatch as dispatcher
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.cli import STAGES, preflight, run_stage
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.run2_preparation import effective_context
from scripts.l2_pipeline.stages.s6_execute import sealed_review_evidence
from scripts.l2_pipeline.storage import ensure_once, output_root, public_input, sha

AGENT_STAGES = ("S0L", "S0", "S1", "S5", "S6")
REPO = Path(__file__).resolve().parents[2]


def failure(path, exc, **identity):
    return dict(path=str(path), status="FAILED_NO_RETRY", validated=False,
                reason=str(exc), exception_type=type(exc).__name__, **identity)


def card_paths(args):
    if args.design:
        return [public_input(p) for p in args.design]
    root = public_input(args.batch)
    paths = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".yaml", ".yml"})
    if not paths:
        raise ValueError("No design cards")
    return paths


def contexts(args, repo=REPO):
    """Return contexts OR failure records, without discarding a valid neighbour."""
    workspace = output_root(args.work_dir, repo)
    seen = set()
    for path in card_paths(args):
        try:
            card = load_card(path)
            key = (card.design_id, card.version)
            if key in seen:
                raise ValueError("Duplicate design/version")
            seen.add(key)
            yield StageContext(card, workspace, repo), None
        except Exception as exc:
            yield None, failure(path, exc)


def selected_jobs(args):
    paths = [public_input(p) for p in args.job] if args.job else sorted(public_input(args.jobs_root).glob("*/*/*.md"))
    for path in paths:
        # Filter directory identity before opening unrelated broken packets.
        if args.stage and path.parent.parent.name != args.stage:
            continue
        if args.design_id and path.parent.name not in args.design_id:
            continue
        if args.attempt_id and not path.name.endswith("." + args.attempt_id + ".md"):
            continue
        try:
            job = load_job(path)
            if args.stage and job.stage != args.stage:
                continue
            if args.design_id and job.design_id not in args.design_id:
                continue
            if args.attempt_id and job.contract.get("attempt_id") != args.attempt_id:
                continue
            yield path, job, None
        except Exception as exc:
            yield path, None, failure(path, exc)


def collect(args):
    results = []
    for path, job, error in selected_jobs(args):
        if error:
            results.append(error)
            continue
        try:
            results.append(collect_job(path, allow_historical_draft=args.allow_historical_draft))
        except Exception as exc:
            results.append(failure(path, exc, design_id=job.design_id, stage=job.stage, role=job.role))
    if not results:
        raise ValueError("No matching agent jobs")
    return dict(action="collect", results=results, execution_authorized=False)


def approve_run(args, repo=REPO):
    """Do not close findings, fabricate a reviewer, sign a STOP, or execute fits."""
    if not args.only_pass:
        raise ValueError("approve-run requires --only-pass")
    results = []
    for ctx, error in contexts(args, repo):
        if error:
            results.append(error)
            continue
        try:
            ctx = effective_context(ctx)
            review = ctx.read("EXECUTION_REVIEW.json")
            if (not isinstance(review, dict) or review.get("verdict") != "PASS"
                    or review.get("must") != [] or review.get("review_scope") != "sealed_candidate"):
                results.append(dict(design_id=ctx.card.design_id, status="SKIPPED_NOT_PASS",
                                    reason="Sealed review must be PASS with MUST=0", signed=False))
                continue
            # Validate candidate binding, reviewer independence, and SHOULD closure
            # before invoking the existing freeze/authority/signature verifier.
            candidate = sha(ctx.design_dir / "FREEZE_FINAL.json")
            from .snapshots import manifest_for
            if not manifest_for(ctx.design_dir):
                sealed_review_evidence(ctx.design_dir, candidate, ctx.card.design_id, args.signer)
            check = registration.approve(ctx, args, "final")
            results.append(dict(design_id=ctx.card.design_id, status="APPROVED", signed=True, verification=check))
        except Exception as exc:
            results.append(failure(ctx.card.path, exc, design_id=ctx.card.design_id, signed=False))
    return dict(action="approve-run", results=results, actual_data_estimation_calls=0)


def run(args, repo=REPO):
    results = []
    for ctx, error in contexts(args, repo):
        if error:
            results.append(error)
            continue
        try:
            # Call the single-design runner; the legacy batch abort branch is
            # deliberately never entered, even following an official failure.
            results.append(run_stage(ctx, args.stage).as_dict())
        except Exception as exc:
            results.append(failure(ctx.card.path, exc, design_id=ctx.card.design_id, stage=args.stage))
    return dict(action="run", stage=args.stage, results=results)


def ri_geometry_feasibility(declaration, design):
    """Check the exact RI199 prefix and RI999 labels without outcome operators."""
    from scripts.l2_pipeline.engine.ri import (
        RI_SEED, inventory, predraw_labels, pre_family, relabeled_design, support_failure,
    )
    family = pre_family(design)
    records = []
    for labels in predraw_labels(design, 999):
        try:
            if not family:
                raise ValueError('NOT_ASSESSABLE: empty supported pre family')
            fake = relabeled_design(declaration, design, labels)
            if pre_family(fake) != family:
                raise ValueError('RI lead inventory changed under relabeling')
        except ValueError as exc:
            if not support_failure(exc):
                raise
            records.append(dict(infeasible=True, reason=str(exc)))
        else:
            records.append(dict(infeasible=False))
    from scripts.l2_pipeline.gate_relax import floor_from_declaration
    floor = floor_from_declaration(declaration)
    checks = {f'RI{n}': inventory(records[:n], floor=floor) for n in (199, 999)}
    return dict(checks=checks, passed=all(v['support_pass'] for v in checks.values()),
                seed=RI_SEED, event_times=family, outcome_values_read=0,
                actual_data_estimation_calls=0, redrawn=0)


def precheck_s5(ctx):
    """Persist a hash-bound check on S4 geometry before spending on forecasts."""
    from scripts.l2_pipeline.registered import numerical_declaration, registered_panel_keys
    from scripts.l2_pipeline.engine.design import build_design
    from scripts.l2_pipeline.engine.inference import design_binding
    root = ctx.design_dir
    support = next(r for r in ctx.read('SUPPORT.json')['variants'] if r['variant'] == 'primary')
    if not support.get('support_pass', support.get('passed')):
        raise ValueError('Supported primary required for prospective RI check')
    declaration = numerical_declaration(ctx.card.payload, support, ctx.read('TREATMENT.json'), 'primary')
    metadata = root/'panels/primary/metadata.csv'
    rows = [(u, t, 0., None) for u, t in sorted(registered_panel_keys(declaration, metadata))]
    design = build_design(declaration, rows)
    if design['cells'] != support['cell_inventory']:
        raise ValueError('Prospective RI/metadata cell inventory differs')
    value = dict(schema='l2_before_s5_ri_geometry_v1', design_id=ctx.card.design_id,
                 version=ctx.card.version, design_binding=design_binding(design),
                 input_bindings=[dict(path=str(p), sha256=sha(p)) for p in
                                 (ctx.card.path, root/'SPEC.json', root/'SUPPORT.json',
                                  root/'TREATMENT.json', metadata)],
                 **ri_geometry_feasibility(declaration, design))
    ensure_once(root/'RI_PRE_S5.json', value)
    return value


def prepare(args, repo=REPO):
    if args.allow_draft and args.stage != "S6":
        raise ValueError("Draft mode is limited to S6")
    results = []
    for ctx, error in contexts(args, repo):
        if error:
            results.append(error)
            continue
        try:
            if args.stage != "S1":
                ctx = effective_context(ctx)
            # A source-panel audit packet is preparation, not S1 passage.
            # Purpose review and official treatment audits may be queued together;
            # run_stage still requires a ready S0 receipt before S1 can pass.
            audit_only = (args.stage == "S1" and
                          ctx.card.payload.get("treatment", {}).get("source_type") in {"research_panel", "policy_tracker"})
            if not audit_only:
                preflight(ctx, "S5" if args.allow_draft else args.stage)
            if args.stage == 'S5':
                check = precheck_s5(ctx)
                override = getattr(args, 'hub_override_ri_feasibility', None)
                if override is not None and not override.strip():
                    raise ValueError('Hub RI override requires an explicit reason')
                if not check['passed'] and not override:
                    results.append(dict(design_id=ctx.card.design_id, status='STOPPED_PRE_RUN',
                        reason='RI199/RI999 feasibility below the registered 90%',
                        ri_precheck_path=str(ctx.design_dir/'RI_PRE_S5.json'),
                        ri_precheck_sha256=sha(ctx.design_dir/'RI_PRE_S5.json'),
                        checks=check['checks'], jobs=[], execution_authorized=False))
                    continue
                if override:
                    ensure_once(ctx.design_dir/'RI_PRE_S5_HUB_OVERRIDE.json', dict(
                        reason=override, precheck_sha256=sha(ctx.design_dir/'RI_PRE_S5.json'),
                        execution_authorized=False, changes_registered_stop_rule=False))
            jobs = emit_stage_jobs(ctx, args.stage, allow_draft=args.allow_draft,
                                   jobs_root=output_root(args.jobs_root, repo),
                                   attempt_id=args.attempt_id, source_pack_path=args.source_pack,
                                   previous_jobs_manifest=getattr(args, "previous_jobs_manifest", None))
            results.append(dict(design_id=ctx.card.design_id, status="PREPARED", jobs=[asdict(j) for j in jobs]))
        except Exception as exc:
            results.append(failure(ctx.card.path, exc, design_id=ctx.card.design_id))
    return dict(action="prepare", results=results, dispatched=False, model_calls=0,
                max_concurrency="1..4", hub_dispatch="python -B -m scripts.l2_pipeline.hub dispatch " + args.stage)


def advance(args, repo=REPO):
    """Prepare S2–S4 only after actual passing upstream receipts; never estimate."""
    if not args.only_pass:
        raise ValueError("advance requires --only-pass")
    results = []
    for ctx, error in contexts(args, repo):
        if error:
            results.append(error)
            continue
        stages = []
        try:
            for stage in ("S2", "S3", "S4"):
                current = effective_context(ctx)
                try:
                    preflight(current, stage)
                except ValueError as exc:
                    stages.append(dict(stage=stage, status="SKIPPED_NOT_PASS", reason=str(exc)))
                    break
                result = run_stage(ctx, stage).as_dict()
                stages.append(result)
                if result["disposition"] != "ready":
                    break
            status = ("FAILED_NO_RETRY" if any(r.get("disposition") == "failed_no_retry" for r in stages)
                      else "PREPARED" if len(stages) == 3 and all(r.get("disposition") == "ready" for r in stages)
                      else "SKIPPED_NOT_PASS")
            results.append(dict(design_id=ctx.card.design_id, status=status, stages=stages))
        except Exception as exc:
            results.append(failure(ctx.card.path, exc, design_id=ctx.card.design_id, stages=stages))
    return dict(action="advance", results=results, execution_authorized=False,
                actual_data_estimation_calls=0)


def status(args, repo=REPO):
    results = []
    for ctx, error in contexts(args, repo):
        if error:
            results.append(error)
            continue
        try:
            root = ctx.design_dir
            row = dict(design_id=ctx.card.design_id, version=ctx.card.version, work_dir=str(root), receipts={})
            for stage in STAGES:
                receipts = sorted((root / "receipts" / stage).glob("*.json"))
                if receipts:
                    row["receipts"][stage] = json.loads(public_input(receipts[-1]).read_text())["result"]
            for name in ("TERMINAL.json", "FAILURE.json", "EXECUTION_REVIEW.json", "APPROVAL_FINAL.json"):
                path = root / name
                if path.is_file():
                    value = json.loads(public_input(path).read_text())
                    row[name] = {k: value[k] for k in ("status", "verdict", "review_scope", "reason", "execution_authorized") if k in value}
                    if name == "EXECUTION_REVIEW.json":
                        row[name]["MUST"] = len(value["must"]) if isinstance(value.get("must"), list) else None
            results.append(row)
        except Exception as exc:
            results.append(failure(ctx.card.path, exc, design_id=ctx.card.design_id))
    return dict(action="status", results=results)


def _cards(parser):
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--design", type=Path, action="append")
    selection.add_argument("--batch", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == 'autopilot':
        from .autopilot import main as autopilot_main
        return autopilot_main(raw[1:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser('autopilot', help='Durable global scheduler: tick, loop, ingest (use autopilot --help)')
    _cards(sub.add_parser("status", help="Read per-design receipts; no outcome decoding"))
    approve = sub.add_parser("approve-run", help="Sign only sealed PASS designs with MUST=0; no execution")
    _cards(approve)
    approve.add_argument("--only-pass", required=True, action="store_true")
    approve.add_argument("--signer", required=True)
    approve.add_argument("--signed-at", required=True, help="Explicit UTC timestamp")
    execute = sub.add_parser("run", help="Run one pipeline stage independently for each design")
    _cards(execute)
    execute.add_argument("stage", choices=STAGES)
    automatic = sub.add_parser("advance", help="Prepare S2–S4 automatically for passing S1 designs; no estimation")
    _cards(automatic)
    automatic.add_argument("--only-pass", required=True, action="store_true")
    prep = sub.add_parser("prepare", help="Emit answer-free jobs independently; never launch agents")
    _cards(prep)
    prep.add_argument("stage", choices=("S0", "S1", "S5", "S6"))
    prep.add_argument("--jobs-root", type=Path, required=True)
    prep.add_argument("--attempt-id")
    prep.add_argument("--previous-jobs-manifest", type=Path, help="Hash-bound archived packets for template regeneration")
    prep.add_argument("--source-pack", type=Path)
    prep.add_argument("--allow-draft", action="store_true")
    prep.add_argument("--hub-override-ri-feasibility", metavar="REASON",
                      help="Explicit hub exception to forecast preparation only; retains failed RI check and grants no execution authority")
    coll = sub.add_parser("collect", help="Collect each job independently")
    selection = coll.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job", type=Path, action="append")
    selection.add_argument("--jobs-root", type=Path)
    coll.add_argument("--stage", choices=AGENT_STAGES)
    coll.add_argument("--design-id", action="append")
    coll.add_argument("--attempt-id")
    coll.add_argument("--allow-historical-draft", action="store_true")
    host = sub.add_parser("dispatch", help="Host agent wave; --max-concurrency 1..4")
    host.add_argument("stage", choices=AGENT_STAGES)
    host.add_argument("--jobs-root", type=Path, default=Path("data/l2_pipeline/jobs"))
    host.add_argument("--job", type=Path, action="append")
    host.add_argument("--design", action="append")
    host.add_argument("--attempt-id")
    host.add_argument("--max-concurrency", type=int, choices=range(1, 5), default=4, help="1..4 (default 4)")
    host.add_argument("--dry-run", action="store_true")
    host.add_argument("--preflight-only", action="store_true")
    host.add_argument("--hub-outside-sandbox", action="store_true")
    host.add_argument('--scheduled-worker', action='store_true', help='Execute one scheduler-assigned job; no worker pool')
    host.add_argument("--codex", default=dispatcher.CODEX)
    args = parser.parse_args(argv)
    if args.command == "dispatch":
        forward = ["--stage", args.stage, "--jobs-root", str(args.jobs_root), "--max-concurrency", str(args.max_concurrency), "--codex", args.codex]
        for option in ("dry_run", "preflight_only", "hub_outside_sandbox", "scheduled_worker"):
            if getattr(args, option):
                forward.append("--" + option.replace("_", "-"))
        for option in ("job", "design"):
            for value in getattr(args, option) or []:
                forward.extend(["--" + option, str(value)])
        if args.attempt_id:
            forward.extend(["--attempt-id", args.attempt_id])
        return dispatcher.main(forward)
    try:
        handlers = {"status": status, "approve-run": approve_run, "collect": collect, "run": run, "prepare": prepare, "advance": advance}
        report = handlers[args.command](args)
        print(json.dumps(report, indent=2))
        rows = report["results"]
        if any(r.get("status") == "FAILED_NO_RETRY" or r.get("disposition") == "failed_no_retry" or r.get("validated") is False for r in rows):
            return 1
        return 2 if any(r.get("status") in {"SKIPPED_NOT_PASS", "STOPPED_PRE_RUN"} or r.get("disposition") in {"parked", "stopped"} for r in rows) else 0
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
