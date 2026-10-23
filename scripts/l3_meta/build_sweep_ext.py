#!/usr/bin/env python3
"""L3 outcome sweep extension SWEEP-EXT-V1, sweep stage (rule id EX-1, exploratory).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_V1.md (section 6) and the wiring amendment PREREG_AMEND_20261016_SWEEP_EXT_WIRE_V1.md
(hash record PREREG_AMEND_20261016_SWEEP_EXT_WIRE_HASH.txt).  The 55 READY series of data/l3_meta/sweep_ext_v1/series_store_ext_v1.npz are run through the
SAME engine as SWEEP-V1 (sweep_core.sweep_design, unchanged) on the SAME designs (the designs that passed the reproduction gate of the run's own
sweep folder, minus the 5 annual-adapter designs), one npz per design with the arrays of the V1 files.  The V1 files are not touched and not read here
except for their gate report.  READ-ONLY on every L2 file; no network.

Why a separate file set: the SWEEP-V1 hash record pins the inventory CSV and the catalog V15, so the 55 series cannot enter the V1 series store (amendment
6.2); a design's V1 results do not depend on which other series exist (operators and label bundles are per design and cell pattern), so a separate pass over
the same designs equals a pass over the union.  D1-V2 (build_d1_ext.py) reads both file sets.

Run (normally through refresh_all.py, step sweep_ext):
  .venv_did/bin/python -I -B scripts/l3_meta/build_sweep_ext.py --sweep-dir <folder of a finished sweep> --out-dir <new folder> [--workers N] [--only id ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import traceback
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_sweep as B  # noqa: E402
import l3_pins  # noqa: E402
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
VERSION = "SWEEP-EXT-V1"
EXT_DIR = ROOT / "data/l3_meta/sweep_ext_v1"
EXT_STORE = EXT_DIR / "series_store_ext_v1.npz"
EXT_MANIFEST = EXT_DIR / "SERIES_EXT_MANIFEST.json"
EXT_CATALOG = EXT_DIR / "SERIES_EXT_CATALOG_V1.csv"
EXT_AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_V1.md"
EXT_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_HASH.txt"
WIRE_AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_WIRE_V1.md"
WIRE_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_WIRE_HASH.txt"
RUN_NAME = "SWEEP_EXT_RUN.json"
CODE_FILES = ["scripts/l3_meta/build_sweep_ext.py", "scripts/l3_meta/l3_pins.py", "scripts/l3_meta/sweep_core.py", "scripts/l3_meta/build_sweep.py",
              "scripts/l3_meta/sweep_ext_series.py"]
MAX_WORKERS = 6


def utc_now():
    return B.utc_now()


def parse_wire_record(path=WIRE_REC):
    """{'files': {path: sha} (the `file:` pairs), 'listed': {path: sha} (every `<path> sha256 <hex>` line)}"""
    return B._parse_one(path)


def verify_pins():
    """Problems between the wiring record, the SWEEP-EXT-V1 record and the files on disk (L2 modules are checked by B.verify_l2)."""
    problems = []
    for rec_path, amend in ((WIRE_REC, WIRE_AMEND), (EXT_REC, EXT_AMEND)):
        if not rec_path.is_file():
            problems.append(f"{S.rel(rec_path)} is missing")
            continue
        rec = l3_pins.merged_record(B._parse_one, l3_pins.record_paths(rec_path))
        if rec["files"].get(S.rel(amend)) != S.sha256_file(amend):
            problems.append(f"{amend.name} differs from {rec_path.name}")
        for path, want in rec["listed"].items():
            if path.startswith(("scripts/l2_pipeline/", "scripts/l2_batch1/")):
                continue
            if re.match(r"^data/l3_meta/(refresh|l2_set_live)", path):
                continue
            p = ROOT / path
            if not p.is_file():
                problems.append(f"{path} (listed in {rec_path.name}) is missing")
            elif S.sha256_file(p) != want:
                # the files that the SWEEP-EXT-V1 record lists as "referenced, not pinned" are not in `listed`; everything in `listed` is pinned
                problems.append(f"{path} differs from {rec_path.name}")
    return problems


def ext_series_table(catalog=None):
    """{series id: dict(family=...)} of the series in the extension store (rows with ext_in_store = 1)."""
    out = OrderedDict()
    with open(catalog or EXT_CATALOG, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("ext_in_store")) in ("1", "True", "true"):
                out[r["outcome_key"]] = dict(family=r["family"], ext_status=r.get("ext_status"), frequency=r.get("frequency"))
    return out


def ext_task(task):
    did, folder, result_path, out_dir = task
    t0 = time.time()
    out = dict(design_id=did, status="ERROR")
    try:
        store_obj = S.SeriesStore(EXT_STORE)
        store = store_obj.view("rate")
        table = ext_series_table()
        if set(table) != set(store_obj.sids):
            raise RuntimeError("the extension catalog table and the extension store hold different series")
        ctx = S.load_design_context(did, folder)
        res = S.sweep_design(ctx, store, table)
        recs = res["records"]
        sids = list(recs)
        placebo = np.vstack([recs[k]["placebo"] for k in sids])
        stats = [S.draw_statistics(recs[k]["att"], recs[k]["placebo"]) if recs[k]["code"] in ("SUPPORTED", "RI_FEASIBLE_LT_90") else
                 dict(sd=float("nan"), cutoff=float("nan"), p2=float("nan"), z=float("nan")) for k in sids]
        meta = dict(rule_id=S.RULE_ID, version=VERSION, design_id=did, run_folder=S.rel(folder), result_sha256=S.sha256_file(result_path),
                    freeze_sha256=S.sha256_file(folder / "FREEZE_FINAL.json"), mode=ctx.mode, adapter=ctx.adapter, units=res["units"],
                    treated_primary=res["treated_primary"], years=res["years"], aligned_equals_engine=res["aligned_equals_engine"],
                    patterns=res["patterns"], scale_mode="rate", ext_store_sha256=S.sha256_file(EXT_STORE),
                    ext_manifest_sha256=S.sha256_file(EXT_MANIFEST), ext_catalog_sha256=S.sha256_file(EXT_CATALOG),
                    amendment_sha256={p.name: S.sha256_file(p) for p in (EXT_AMEND, WIRE_AMEND)},
                    code={f: S.sha256_file(ROOT / f) for f in CODE_FILES}, built_utc=utc_now())
        path = Path(out_dir) / (did.replace(":", "_") + ".npz")
        if path.exists():
            raise RuntimeError(f"{path} exists: not overwritten")
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = dict(
            series_ids=np.array(sids), family=np.array([recs[k]["family"] for k in sids]), code=np.array([recs[k]["code"] for k in sids]),
            detail=np.array([recs[k]["detail"] for k in sids]), fingerprint=np.array([recs[k]["fingerprint"] for k in sids]),
            n_cells=np.array([recs[k]["n_cells"] for k in sids], dtype=np.int64), n_units=np.array([recs[k]["n_units"] for k in sids], dtype=np.int64),
            kept=np.array([recs[k]["kept"] for k in sids], dtype=float), n_feasible=np.array([recs[k]["n_feasible"] for k in sids], dtype=np.int64),
            support_pass_l2=np.array([-1 if recs[k]["support_pass_l2"] is None else int(recs[k]["support_pass_l2"]) for k in sids], dtype=np.int64),
            strict_dropped=np.array([recs[k]["strict_dropped"] for k in sids], dtype=np.int64),
            att=np.array([recs[k]["att"] for k in sids], dtype=float), sd=np.array([x["sd"] for x in stats], dtype=float),
            cutoff=np.array([x["cutoff"] for x in stats], dtype=float), p2=np.array([x["p2"] for x in stats], dtype=float),
            z=np.array([x["z"] for x in stats], dtype=float), placebo=placebo, meta_json=np.array(json.dumps(S.clean(meta), sort_keys=True)))
        problems = B.verify_l2(B._WORKER.get("rec"))
        if problems:
            raise RuntimeError("L2 module changed during the task, nothing written: " + "; ".join(problems[:3]))
        part = path.with_name(path.name + ".part")
        with open(part, "xb") as fh:
            np.savez_compressed(fh, **arr)
        if path.exists():
            raise RuntimeError(f"{path} exists: not overwritten")
        os.rename(part, path)
        out.update(status="OK", file=path.name, codes=dict(Counter(recs[k]["code"] for k in sids)), n_patterns=len(res["patterns"]), n_series=len(sids),
                   sha256=S.sha256_file(path))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["l2_problems"] = B.verify_l2(B._WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sweep-dir", default=str(B.OUT), help="folder of a finished SWEEP-V1 run (GATE_REPORT.json, SWEEP_RUN.json)")
    ap.add_argument("--out-dir", required=True, help="new folder for the extension sweep files (must not hold a SWEEP_EXT_RUN.json)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", nargs="*", help="restrict to these design ids (tests only)")
    args = ap.parse_args(argv)
    sweep_dir, out_dir = Path(args.sweep_dir), Path(args.out_dir)
    problems = B.verify_records() + verify_pins()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    S.load_l2()
    problems = B.verify_l2()
    if problems:
        sys.exit("L2 modules differ from the hash record, not run: " + "; ".join(problems[:5]))
    target = out_dir / RUN_NAME
    if target.exists():
        sys.exit(f"{S.rel(target)} exists: not overwritten")
    report = S.read_json(sweep_dir / "GATE_REPORT.json")
    if S.sha256_file(S.LIVE) != report["live_set_sha256"]:
        sys.exit("the gate report was made with another live set")
    if B.amend_hashes() != report["amendment_sha256"]:
        sys.exit("the gate report was made with another SWEEP-V1 amendment")
    todo, skipped = B.swept_designs(report)
    tasks = [(did, folder, rp, out_dir) for did, folder, rp, _store, _mode in todo]
    if args.only:
        tasks = [t for t in tasks if t[0] in args.only]
    tasks.sort(key=lambda t: -os.path.getsize(t[2]) if os.path.exists(t[2]) else 0)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results = sorted(B._run_pool(ext_task, tasks, min(args.workers, MAX_WORKERS), "sweep_ext"), key=lambda r: r["design_id"])
    table = ext_series_table()
    summary = dict(rule_id=S.RULE_ID, version=VERSION, stage="sweep_ext", run_utc=utc_now(), n_swept=sum(1 for r in results if r["status"] == "OK"),
                   n_error=sum(1 for r in results if r["status"] != "OK"), n_ext_series=len(table), skipped=skipped, designs=results,
                   code={f: S.sha256_file(ROOT / f) for f in CODE_FILES}, amendment_sha256={p.name: S.sha256_file(p) for p in (EXT_AMEND, WIRE_AMEND)},
                   ext_store_sha256=S.sha256_file(EXT_STORE), ext_manifest_sha256=S.sha256_file(EXT_MANIFEST), ext_catalog_sha256=S.sha256_file(EXT_CATALOG),
                   gate_report_sha256=S.sha256_file(sweep_dir / "GATE_REPORT.json"),
                   sweep_run_sha256=S.sha256_file(sweep_dir / "SWEEP_RUN.json") if (sweep_dir / "SWEEP_RUN.json").exists() else None,
                   live_set_sha256=report["live_set_sha256"], seconds=round(time.time() - t0, 1), python=sys.version.split()[0], numpy=np.__version__)
    B.write_once_json(target, summary)
    for r in results:
        print(f"{r['design_id'][:60]:60s} {r['status']:6s} {r.get('seconds')}s patterns={r.get('n_patterns')} {r.get('codes')} {r.get('error', '')[:300]}")
    return 0 if summary["n_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
