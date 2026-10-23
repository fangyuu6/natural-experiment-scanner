#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version D1-V2 (rule id EX-1): D1 on the series of SWEEP-V1 plus the 55 series of SWEEP-EXT-V1.

Rules: the D1 entry and engine of build_d1.py (PREREG_AMEND_20261015_D_QUESTIONS.md, entry D1; hash record PREREG_AMEND_20261015_D_HASH.txt) are used UNCHANGED;
only the series universe differs (the per-design arrays of the V1 sweep files and of the extension files are concatenated, series ids must not repeat).
Wiring amendment: PREREG_AMEND_20261016_SWEEP_EXT_WIRE_V1.md section 4 (hash record PREREG_AMEND_20261016_SWEEP_EXT_WIRE_HASH.txt).

Consequences of the D1 rule that the reader should know before opening the result: D1 keeps only the placebo draws that are feasible for EVERY supported
series of a design, so adding series can shrink a design's common-draw set and move the lines of the old families a little (build_d1.design_statistics,
DRAWS_COMMON); D1-V2 is therefore a recomputation, not an addition to D1-V1.  Gate built in: the same code with the extension files left out must give
exactly the D1-V1 result of the same run (--v1-results), else nothing is written.

Run (normally through refresh_all.py, step d1_ext):
  .venv_did/bin/python -I -B scripts/l3_meta/build_d1_ext.py --sweep-dir <V1 sweep folder> --ext-dir <extension sweep folder> --out-dir <new folder> [--v1-results <RESULTS_D1_V1.json>] [--dry-run]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_d1 as D1  # noqa: E402
import build_sweep as B  # noqa: E402
import build_sweep_ext as X  # noqa: E402
import sweep_core as S  # noqa: E402

VERSION = "D1-V2"
OUT_NAME = "RESULTS_D1_V2.json"
PROV_NAME = "D1_V2_PROVENANCE.json"
_STATE = {"ext_dir": None}          # None = extension files left out (the regression pass)


def union_loader(orig):
    def load(path):
        f = orig(path)
        ext_dir = _STATE["ext_dir"]
        if ext_dir is None:
            return f
        ep = Path(ext_dir) / Path(path).name
        if not ep.is_file():
            raise RuntimeError(f"extension sweep file missing for {Path(path).name}")
        g = orig(ep)
        if set(f["sids"]) & set(g["sids"]):
            raise RuntimeError(f"series ids repeat between the V1 file and the extension file of {Path(path).name}")
        return dict(sids=f["sids"] + g["sids"], fam=f["fam"] + g["fam"], code=f["code"] + g["code"], att=np.concatenate([f["att"], g["att"]]),
                    sd=np.concatenate([f["sd"], g["sd"]]), placebo=np.vstack([f["placebo"], g["placebo"]]), meta=f["meta"], z=f["z"])
    return load


def run_d1(argv, ext_dir, capture=False):
    """Call build_d1.main with the extension files in (ext_dir) or out (None).  Returns (rc, stdout text)."""
    _STATE["ext_dir"] = ext_dir
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = D1.main([str(a) for a in argv])
    return rc, buf.getvalue(), err.getvalue()


def main(argv=None):
    """Runs D1-V2.  build_d1 is patched only for the duration of the call (refresh_all runs later steps, e.g. CAL, in the same process)."""
    saved = (D1.load_design_file, D1.VERSION, D1.OUT_NAME)
    try:
        return _main(argv)
    finally:
        D1.load_design_file, D1.VERSION, D1.OUT_NAME = saved
        _STATE["ext_dir"] = None


def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sweep-dir", default=str(B.OUT))
    ap.add_argument("--ext-dir", required=True, help="folder with SWEEP_EXT_RUN.json and the extension sweep files")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--v1-results", default=None, help="RESULTS_D1_V1.json of the same run: the regression gate compares the extension-free pass with it")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    sweep_dir, ext_dir, out_dir = Path(args.sweep_dir), Path(args.ext_dir), Path(args.out_dir)
    problems = X.verify_pins()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    ext_run = S.read_json(ext_dir / X.RUN_NAME)
    if ext_run["gate_report_sha256"] != S.sha256_file(sweep_dir / "GATE_REPORT.json") or ext_run.get("live_set_sha256") != S.sha256_file(S.LIVE):
        sys.exit("the extension sweep was made with another gate report or live set")
    if ext_run["n_error"]:
        sys.exit(f"the extension sweep has {ext_run['n_error']} errored designs")
    D1.load_design_file = union_loader(D1.load_design_file)
    D1.VERSION, D1.OUT_NAME = VERSION, OUT_NAME
    common = ["--sweep-dir", sweep_dir]
    # regression gate: extension files left out must reproduce D1-V1 exactly
    reg = dict(checked=False)
    if args.v1_results:
        rc0, out0, err0 = run_d1(common + ["--dry-run"], None)
        if rc0 != 0:
            sys.exit(f"extension-free pass failed: {err0[-300:]}")
        got = json.loads(out0)["D1"]
        want = S.read_json(Path(args.v1_results))["question"]["D1"]
        same = json.dumps(got, sort_keys=True) == json.dumps(want, sort_keys=True)
        reg = dict(checked=True, equal=bool(same), v1_results_sha256=S.sha256_file(Path(args.v1_results)))
        if not same:
            sys.exit("REGRESSION_FAIL: D1 with the extension files left out differs from D1-V1 of the same run; nothing written")
    rc, out, err = run_d1(common + ["--out-dir", out_dir] + (["--dry-run"] if args.dry_run else []), ext_dir)
    sys.stdout.write(out)
    sys.stderr.write(err)
    if rc != 0 or args.dry_run:
        return rc
    prov = dict(rule_id=S.RULE_ID, version=VERSION, built_utc=B.utc_now(), regression_gate=reg, ext_dir=S.rel(ext_dir) if str(ext_dir).startswith(str(S.ROOT)) else str(ext_dir),
                ext_run_sha256=S.sha256_file(ext_dir / X.RUN_NAME), ext_store_sha256=ext_run["ext_store_sha256"], gate_report_sha256=ext_run["gate_report_sha256"],
                n_ext_series=ext_run["n_ext_series"], code={f: S.sha256_file(S.ROOT / f) for f in X.CODE_FILES + ["scripts/l3_meta/build_d1_ext.py", "scripts/l3_meta/build_d1.py"]},
                results_file=OUT_NAME, results_sha256=S.sha256_file(out_dir / OUT_NAME))
    X.B.write_once_json(out_dir / PROV_NAME, prov)
    return 0


if __name__ == "__main__":
    sys.exit(main())
