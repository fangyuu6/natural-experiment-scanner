"""Generic isolated seed/eligibility adapter around the ordinary hub dispatcher.

Preflight is read-only. Fresh launches require bound calibration and purpose.
"""
import argparse
from pathlib import Path
import sys
from scripts.l2_pipeline.intake.config import IntakeConfig
from scripts.l2_pipeline.intake import v8b_calendar
from scripts.l2_pipeline.intake.fresh_gate import fresh_gate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--intake-root', required=True, type=Path)
    options, args = parser.parse_known_args(argv)
    config = IntakeConfig.from_existing(options.intake_root)
    root = config.root
    if not args or args[0] != 'dispatch' or '--jobs-root' not in args:
        raise ValueError('Only dispatch with explicit jobs root is supported')
    jobs_root = Path(args[args.index('--jobs-root')+1]).resolve()
    if jobs_root not in {root/'s0_jobs', root/'calibration_jobs', root/'fresh_audit_jobs'}:
        raise ValueError('Wrapper accepts only the configured intake job roots')
    if any(k in args for k in ('--job', '--attempt-id')):
        raise ValueError('Job paths and attempt are fixed; use --design selectors')
    designs = [args[i+1] for i, value in enumerate(args) if value == '--design']
    v8b_calendar.configure(config)
    # load_job inside fresh_gate must use this intake's V8b sample validator.
    v8b_calendar.install()
    if jobs_root == root/'fresh_audit_jobs' and '--preflight-only' not in args and '--dry-run' not in args:
        fresh_gate(root, designs, validate_audit_clock=v8b_calendar.validate_audit_clock)
    from scripts.l2_pipeline.hub import main as hub_main
    return hub_main(args)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
