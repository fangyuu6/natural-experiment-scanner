#!/usr/bin/env python3
"""L3 outcome-sweep extension (SWEEP-EXT-V1, rule id EX-1, exploratory): acquisition of the public files of catalog series that were not on disk.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_V1.md.  This file only fetches bytes; it computes no outcome statistic and reads no
treatment table.  It uses the L2 acquisition functions READ-ONLY (outcome_v2_cdc.acquire, outcome_v2_acs.acquire and the rate-limited, write-once
PublicClient with byte receipts: official credential-free HTTPS hosts only, one request per second per process, no retry around an access block).
Nothing is written under scripts/l2_pipeline or data/l2_pipeline.  Files go to data/replication/shared_data/<source>/<release>/raw/ (the
project's shared download cache, README there), each with its <file>.receipt.json (URL, retrieval time, size, sha256, licence) beside it; one
registry line per batch is appended to data/replication/shared_data/REGISTRY.jsonl (append-only, flock) and one batch receipt is written to
<release>/receipts/.

Jobs (each is one process; jobs for the same host never run concurrently on the same year, so two processes cannot fetch one file):
  cdc_firearm    CDC data.cdc.gov fpsi-y8tj, FA_Deaths by state, 2019-2024            (series cdc_firearm_deaths, cdc_firearm_crude_rate)
  cdc_vsrr       CDC data.cdc.gov xkb8-kh2a, overdose deaths 12-month-ending, 2015-2025 (series cdc_vsrr_overdose_deaths_reported / _predicted)
  acs_tb         Census ACS1 table-based summary files 2021-2024: B17001, B19049, B19126, B27001
  acs_seq_a      Census ACS1 sequence files by state 2010-2014 (the four tables)
  acs_seq_b      Census ACS1 sequence files by state 2015-2019 (the four tables)
  acs_b22003     the sequence years of table B22003 (SNAP receipt) that the L2 yield-fix download did not complete (it stopped at 2011); only missing years

Run:  python -I -B scripts/l3_meta/sweep_ext_acquire.py --job <name> [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import importlib
import json
import os
import sys
import time
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True

SHARED = ROOT / "data/replication/shared_data"
REGISTRY = SHARED / "REGISTRY.jsonl"
OUT = ROOT / "data/l3_meta/sweep_ext_v1"
LOG = OUT / "ACQ_LOG.jsonl"
CONNECTORS = ROOT / "scripts/l2_pipeline/connectors"

# one representative catalog series per ACS table (all series of a table read the same published file); the series' declared years must contain the requested ones
ACS_TABLES = {
    "B17001": "acs1_poverty_rate_age_under5",
    "B19049": "acs1_median_household_income_age_all",
    "B19126": "acs1_single_mother_median_family_income",
    "B27001": "acs1_insurance_coverage_age_under6",
}
ACS_SEQ_DIR = "census-acs1-summary/acs1-sequence-by-state-2010-2019"
ACS_TB_DIR = "census-acs1-summary/acs1-table-based-2021-2024"
CDC_FIREARM_DIR = "cdc-nchs-socrata/fpsi-y8tj-2019-2024"
CDC_VSRR_DIR = "cdc-nchs-socrata/xkb8-kh2a-2015-2025"


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p, chunk=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def rel(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def log(event, **kw):
    OUT.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(utc=utc_now(), event=event, pid=os.getpid(), **kw), sort_keys=True, default=str) + "\n"
    with open(LOG, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------------------------
# L2 import (read-only).  The acquisition functions live in leaf modules that need only common.py and outcome_v2_core.py; the package
# __init__ of scripts.l2_pipeline.connectors registers every catalog version and can fail while another worker is adding one.  The normal
# import is tried first; only if it fails is the package object stubbed (no __init__ executed), which loads the same leaf files.
# ---------------------------------------------------------------------------------------------
def load_leaf_modules(prefer_leaf=True):
    """Load the leaf adapters.  prefer_leaf (default): the package object scripts.l2_pipeline.connectors is stubbed (its __init__ is not executed), so the set of
    L2 files that run is small and fixed (common, outcome_v2_core, _cdc, _acs, _labor, historical, rates, c56_qcew, public_annual, published, prescreen, storage ...)
    and does not depend on catalog versions that other workers add.  Without prefer_leaf the normal package import is tried first."""
    sys.path.insert(0, str(ROOT))
    mode = "leaf_modules_stub_package"
    if not prefer_leaf:
        mode = "package_import"
        try:
            import scripts.l2_pipeline.connectors  # noqa: F401
        except Exception as exc:                      # a half-registered catalog version in the working tree
            for name in [n for n in sys.modules if n.startswith("scripts.l2_pipeline.connectors")]:
                del sys.modules[name]
            prefer_leaf = True
            mode = f"leaf_modules_stub_package (package import failed: {type(exc).__name__}: {str(exc)[:80]})"
    if prefer_leaf:
        for name in [n for n in sys.modules if n.startswith("scripts.l2_pipeline.connectors")]:
            del sys.modules[name]
        stub = types.ModuleType("scripts.l2_pipeline.connectors")
        stub.__path__ = [str(CONNECTORS)]
        stub.__package__ = "scripts.l2_pipeline.connectors"
        sys.modules["scripts.l2_pipeline.connectors"] = stub
    core = importlib.import_module("scripts.l2_pipeline.connectors.outcome_v2_core")
    cdc = importlib.import_module("scripts.l2_pipeline.connectors.outcome_v2_cdc")
    acs = importlib.import_module("scripts.l2_pipeline.connectors.outcome_v2_acs")
    common = importlib.import_module("scripts.l2_pipeline.connectors.common")
    return mode, core, cdc, acs, common


def l2_hashes():
    """{repository-relative path: sha256} of every loaded module under scripts/l2_pipeline and scripts/l2_batch1 (the CPS dictionary audit is imported from there)."""
    bases = [str((ROOT / "scripts" / "l2_pipeline").resolve()), str((ROOT / "scripts" / "l2_batch1").resolve())]
    out = {}
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and f.endswith(".py") and any(str(Path(f).resolve()).startswith(b) for b in bases):
            out[rel(f)] = sha256_file(f)
    return dict(sorted(out.items()))


def shim_acs_lookup(acs):
    """The 2011 ACS1 lookup file has other column names ("seq", "position", "cells") than the 2010 and 2012-2019 files that acs._lookup reads
    ("Sequence Number", "Start Position", "Total Cells in Table").  The shim maps the names and nothing else; every other line of the L2 sequence
    route (geography join, estimate and margin members, derived DAT with source hashes) is unchanged.  Applied only in this process."""
    original = acs._lookup

    def lookup(path, table):
        try:
            return original(path, table)
        except Exception as first:
            matches = []
            with Path(path).open(encoding="latin-1", newline="") as handle:
                reader = csv.DictReader(handle)
                if not {"Table ID", "seq", "position", "cells"} <= set(reader.fieldnames or []):
                    raise first
                for record in reader:
                    if record.get("Table ID") == table and record.get("position", "").strip() not in ("", "."):
                        matches.append(record)
            if len(matches) != 1:
                raise first
            record = matches[0]
            import re
            count = re.fullmatch(r"(\d+) CELLS?", record["cells"].strip())
            if not count:
                raise acs.ConnectorError("Invalid ACS lookup table cell count (2011 layout)")
            seq, start = int(record["seq"]), int(record["position"])
            if not 1 <= seq <= 9999 or start < 7:
                raise acs.ConnectorError("Invalid ACS lookup sequence/start position (2011 layout)")
            return seq, start, int(count[1])

    acs._lookup = lookup
    return original


# ---------------------------------------------------------------------------------------------
# registration in the shared cache
# ---------------------------------------------------------------------------------------------
def file_year(name):
    """Source year in the file names of the ACS routes (acsdt1y2021-b17001.dat, 20121ak0060000.zip, g20121ak.csv, acs_lookup_2012.txt) or None."""
    import re
    m = re.match(r"^(?:g|acsdt1y|acs_lookup_)?(20\d\d)", name)
    return int(m.group(1)) if m else None


def file_records(folder, years=None):
    """[{path, bytes, mtime_ns, url, sha256}] for every file directly in `folder` (the receipts and scan files beside them are not listed);
    with `years`, only files of those source years (a second process may be writing other years into the same folder)."""
    folder = Path(folder)
    out = []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.endswith(".receipt.json") or p.name.endswith(".scan.json") or p.name.startswith("."):
            continue
        if years is not None and file_year(p.name) not in years:
            continue
        r = p.with_name(p.name + ".receipt.json")
        receipt = json.loads(r.read_text(encoding="utf-8")) if r.is_file() else {}
        out.append(dict(path=rel(p), bytes=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns, url=receipt.get("url", "") or "",
                        sha256=sha256_file(p)))
    return out


def registry_append(record):
    """One JSON line, one write, exclusive advisory lock (shared_data/README.md)."""
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    with open(REGISTRY, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def batch_receipt(release_dir, job, files, extra):
    rdir = Path(release_dir) / "receipts"
    rdir.mkdir(parents=True, exist_ok=True)
    path = rdir / f"download_{job}_{utc_now().replace(':', '').replace('-', '')}.json"
    body = dict(job=job, written_utc=utc_now(), n_files=len(files), files=files, **extra)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(body, indent=1, sort_keys=True) + "\n")
    return path


# ---------------------------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------------------------
def run_cdc(job, core, cdc, args):
    if job == "cdc_firearm":
        series, years, sub, ds = "cdc_firearm_deaths", tuple(range(2019, 2025)), CDC_FIREARM_DIR, "fpsi-y8tj"
        dataset = "CDC NCHS firearm injury mortality by state (fpsi-y8tj), FA_Deaths, calendar years"
        geography, unit = "US states and DC, state of residence", "deaths (count) and crude rate per 100,000"
    else:
        series, years, sub, ds = "cdc_vsrr_overdose_deaths_reported", tuple(range(2015, 2026)), CDC_VSRR_DIR, "xkb8-kh2a"
        dataset = "CDC NCHS VSRR provisional drug overdose deaths (xkb8-kh2a), 12-month-ending counts, number of drug overdose deaths"
        geography, unit = "US states, NYC separate, state of occurrence", "deaths in trailing 12 months (reported and predicted)"
    dest = SHARED / sub / "raw"
    plan = [dict(series=series, year=y, file=f"{ds}_{y}.csv") for y in years]
    if args.dry_run:
        print(json.dumps(dict(job=job, destination=rel(dest), plan=plan), indent=1))
        return 0
    client = core.PublicClient()
    failures = []
    for y in years:
        try:
            receipts = cdc.acquire(series, (y,), dest, client=client)
            for r in receipts:
                p = Path(r["path"])
                scan_path = p.with_name(p.name + ".scan.json")
                if not scan_path.exists():
                    from scripts.l2_pipeline.policy_tracker_sources import scan_untrusted
                    scan = scan_untrusted(p.read_bytes(), r["url"])
                    with open(scan_path, "x", encoding="utf-8") as fh:
                        fh.write(json.dumps(scan, indent=1) + "\n")
                    if scan["heuristic_hit_count"]:
                        raise RuntimeError(f"heuristic scan flagged {p.name}: treat as untrusted, stop")
            log("fetched", job=job, year=y, files=[rel(r["path"]) for r in receipts], sha256=[r["sha256"] for r in receipts], urls=[r["url"] for r in receipts])
        except Exception as exc:
            failures.append(dict(year=y, error=f"{type(exc).__name__}: {str(exc)[:300]}"))
            log("failed", job=job, year=y, error=f"{type(exc).__name__}: {str(exc)[:300]}")
    return finish(job, dest.parent, "CDC NCHS (data.cdc.gov Socrata)", dataset, years, geography, unit, failures)


def b22003_missing_years(common):
    """Sequence years 2010-2019 for which the L2 yield-fix download has no complete set of 51 derived whole-state files of B22003."""
    other = ROOT / "data/l2_pipeline/connectors_v2/census_acs1/raw/seq_b22003"
    states = [u.lower() for u in sorted(common.STATES.values())]
    out = []
    for y in range(2010, 2020):
        d = other / str(y)
        ok = d.is_dir() and all((d / f"acsdt1y{y}-b22003-{u}-derived.dat").is_file() and (d / f"acsdt1y{y}-b22003-{u}-derived.dat.receipt.json").is_file() for u in states)
        if not ok:
            out.append(y)
    return out


def run_acs(job, core, acs, args, common=None):
    shim_acs_lookup(acs)
    if job == "acs_b22003":
        sub, source_label = ACS_SEQ_DIR, "ACS 1-year sequence-based summary files by state, table B22003 (years the L2 yield-fix download did not complete)"
        years = tuple(b22003_missing_years(common))
        tables = {"B22003": "acs1_snap_household_receipt_rate"}
    elif job == "acs_tb":
        sub, years, source_label = ACS_TB_DIR, (2021, 2022, 2023, 2024), "ACS 1-year table-based summary files (2021-2024)"
    else:
        sub = ACS_SEQ_DIR
        years = tuple(range(2010, 2015)) if job == "acs_seq_a" else tuple(range(2015, 2020))
        source_label = "ACS 1-year sequence-based summary files by state, whole-state record joined to the derived DAT of the L2 route"
    dest = SHARED / sub / "raw"
    plan = [dict(table=t, series=s, year=y) for y in years for t, s in tables.items()]
    if args.dry_run:
        print(json.dumps(dict(job=job, destination=rel(dest), plan=plan), indent=1))
        return 0
    client = core.PublicClient()
    failures = []
    if not years:
        print(json.dumps(dict(job=job, note="nothing to fetch")))
        return 0
    for y in years:
        for table, sid in tables.items():
            try:
                receipts = acs.acquire(sid, (y,), dest, client=client, source="summary")
                log("fetched", job=job, year=y, table=table, n_receipts=len(receipts))
            except Exception as exc:
                failures.append(dict(year=y, table=table, error=f"{type(exc).__name__}: {str(exc)[:300]}"))
                log("failed", job=job, year=y, table=table, error=f"{type(exc).__name__}: {str(exc)[:300]}")
    return finish(job, dest.parent, "Census ACS 1-year summary files", source_label, years, "US states and DC",
                  "ACS estimates and margins of error of detailed tables " + ", ".join(tables), failures, year_filter=True)


def finish(job, release_dir, source, dataset, years, geography, unit, failures, year_filter=False):
    raw = Path(release_dir) / "raw"
    files = file_records(raw, set(years) if year_filter else None)
    receipt = batch_receipt(release_dir, job, files, dict(failures=failures, l2_modules=l2_hashes()))
    registry_append(dict(source=source, dataset=dataset, years=[min(years), max(years)], geography=geography, unit=unit,
                         files=files, receipt_path=rel(receipt), registered_at=utc_now()))
    log("job_done", job=job, n_files=len(files), n_failures=len(failures), receipt=rel(receipt))
    print(json.dumps(dict(job=job, n_files=len(files), n_failures=len(failures), failures=failures[:10]), indent=1))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True, choices=["cdc_firearm", "cdc_vsrr", "acs_tb", "acs_seq_a", "acs_seq_b", "acs_b22003"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    mode, core, cdc, acs, common = load_leaf_modules()
    log("job_start", job=args.job, import_mode=mode, dry_run=bool(args.dry_run), python=sys.version.split()[0])
    try:
        if args.job.startswith("cdc_"):
            return run_cdc(args.job, core, cdc, args)
        return run_acs(args.job, core, acs, args, common)
    except Exception:
        log("job_error", job=args.job, trace=traceback.format_exc()[-1500:])
        raise


if __name__ == "__main__":
    sys.exit(main())
