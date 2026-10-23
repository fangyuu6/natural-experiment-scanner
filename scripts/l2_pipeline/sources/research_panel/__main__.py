"""Additive command entry point: build, prepare a source S1, or route a stage."""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--no-jobs', action='store_true')
    prepare = sub.add_parser('prepare')
    prepare.add_argument('card', type=Path)
    prepare.add_argument('--workspace', type=Path, required=True)
    stage = sub.add_parser('run-stage')
    stage.add_argument('card', type=Path)
    stage.add_argument('--stage', required=True, choices=['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7'])
    stage.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        from .build import build
        report = build(jobs=not args.no_jobs)
        print(json.dumps({k: v for k, v in report.items() if k != 'dispositions'}, indent=2))
        return 0
    from ...contracts import DesignCard, StageContext
    from .core import REPO
    value = json.loads(args.card.read_text())
    ctx = StageContext(DesignCard.from_mapping(value, args.card.resolve()), args.workspace.resolve(), REPO)
    if args.command == 'prepare':
        from .s1 import run
        result = run(ctx)
    else:
        from ...cards import load_card
        from .registration import run_stage
        ctx = StageContext(load_card(args.card), ctx.workspace, REPO)
        result = run_stage(ctx, args.stage)
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.disposition.value in ('ready', 'parked') else 1


raise SystemExit(main())
