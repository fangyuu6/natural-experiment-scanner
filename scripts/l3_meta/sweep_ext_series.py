#!/usr/bin/env python3
"""L3 outcome-sweep extension SWEEP-EXT-V1 (rule id EX-1, exploratory): the 62 adapter-ready catalog series -> state-year series store.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_V1.md (hash record PREREG_AMEND_20261016_SWEEP_EXT_HASH.txt).  Inventory and catalog are
NOT edited: the 62 series of OUTCOME_DIMENSION_INVENTORY_20261015.csv with data_state adapter_ready_* (catalog V15) keep their rows there; this file
builds a new, separate, write-once set of files under data/l3_meta/sweep_ext_v1/.  Nothing under scripts/l2_pipeline or data/l2_pipeline is opened for
writing (python -I -B; no bytecode); the L2 adapters (outcome_v2_acs / _cdc / _labor, historical, rates, c56_qcew) are imported READ-ONLY and called
exactly as L2's registered chain calls them (build, then rates.apply_scale for the L2 default rate scale).  No network here (sweep_ext_acquire.py fetches).

Stages (each output file is written once; an existing file is never overwritten):
  plan      the series plan and the source files resolved for each series      -> PLAN_EXT_V1.json
  convert   every series through its L2 adapter to a parsed panel              -> parsed/<series_id>.json, CONVERT_LOG.jsonl
  store     parsed panels -> state-year store (sweep_core layout)               -> series_store_ext_v1.npz
  manifest  provenance, statuses, coverage screen, catalog table, source ledger -> SERIES_EXT_MANIFEST.json, SERIES_EXT_CATALOG_V1.csv, SOURCES_EXT_V1.jsonl

Run:  .venv_did/bin/python -I -B scripts/l3_meta/sweep_ext_series.py --stage plan|convert|store|manifest [--workers N] [--only id,id]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib
import json
import math
import multiprocessing as mp
import os
import re
import sys
import time
import traceback
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import sweep_core as S  # noqa: E402  (read-only library: store layout, eligibility rules)
import sweep_ext_acquire as A  # noqa: E402  (leaf import of the L2 adapters without the package __init__)

VERSION = "SWEEP-EXT-V1"
RULE_ID = "EX-1"
EXT = Path(os.environ.get("L3_SWEEP_EXT_DIR") or (ROOT / "data/l3_meta/sweep_ext_v1"))      # the environment variable is for rehearsals in a scratch folder only
PARSED = EXT / "parsed"
PLAN_FILE = EXT / "PLAN_EXT_V1.json"
STORE_FILE = EXT / "series_store_ext_v1.npz"
MANIFEST_FILE = EXT / "SERIES_EXT_MANIFEST.json"
CATALOG_CSV = EXT / "SERIES_EXT_CATALOG_V1.csv"
SOURCES_FILE = EXT / "SOURCES_EXT_V1.jsonl"
CONVERT_LOG = EXT / "CONVERT_LOG.jsonl"
RUN_LOG = EXT / "RUN_LOG.jsonl"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_HASH.txt"
RATE_CATALOG = ROOT / "scripts/l2_pipeline/connectors/OUTCOME_RATE_CATALOG.json"
SWEEP_V1 = ROOT / "data/l3_meta/sweep_v1"

# --- rules of the amendment (section 3) -----------------------------------------------------------------------------------------------------------
# One published population per catalog series whose catalog entry has no single population (required filter); declared here, before any value is read.
HEADLINE_POPULATION = {
    "cdc_teen_birth_rate": {"age_group": "15-19"},
    "cdc_teen_births": {"age_group": "15-19"},
    "nchs_summary_age_specific_birth_rate": {"age_group": "15-19"},
    "cdc_leading_cause_age_adjusted_rate": {"cause_name": "All causes"},
    "cdc_leading_cause_deaths": {"cause_name": "All causes"},
}
SKIP_PINS = os.environ.get("L3_SWEEP_EXT_SKIP_PINS") == "1"      # rehearsals before the amendment exists only; never set for the registered run

# status codes of a series in the extension
READY = "READY"                                      # parsed, in the rate store
READY_PEER = "DUPLICATE_OF_PEER"                      # count series whose L2 default rate IS the published rate of another catalog series; parsed, not stored twice
PARKED_DENOMINATOR = "PARKED_DENOMINATOR"             # count series whose L2 default rate scale has no admissible denominator in L2; parsed at count scale, not stored
UNAVAILABLE = "UNAVAILABLE_NO_PUBLIC_FILE"            # the public file cannot be obtained under the rules (no login, no key, no block-circumvention)
FAILED = "FAILED"                                     # the L2 adapter refused; the message is recorded


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(path, event, **kw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(utc=utc_now(), event=event, **S.clean(kw)), sort_keys=True) + "\n")


def write_once_json(path, obj):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{S.rel(path)} exists: not overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(S.clean(obj), indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


def parse_record(path=None):
    """The hash record in the format of the sweep's records: 'file:' / 'sha256:' line pairs and '  <path> sha256 <hex>' lines."""
    path = Path(path or HASH_REC)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    files = {}
    for i, line in enumerate(lines):
        m = re.match(r"^file:\s*(\S+)\s*$", line)
        if m and i + 1 < len(lines):
            h = re.match(r"^sha256:\s*([0-9a-f]{64})\s*$", lines[i + 1])
            if h:
                files[m.group(1)] = h.group(1)
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    t = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    return dict(files=files, listed=listed, t_amend_utc=t.group(1) if t else None)


L2_BASES = ("scripts/l2_pipeline/", "scripts/l2_batch1/")


def pin_problems(rec=None, files=True):
    """Problems between the hash record and the files now: the amendment, every listed non-L2 input and code file (files=True), and every loaded L2 module,
    which must be listed with the recorded hash.  Empty list = the run may go on."""
    if SKIP_PINS:
        return []
    if not HASH_REC.exists():
        return [f"{S.rel(HASH_REC)} does not exist: the extension runs only after its amendment is hashed"]
    rec = rec or parse_record()
    problems = []
    if files:
        if rec["files"].get(os.path.relpath(AMEND, ROOT)) != (S.sha256_file(AMEND) if AMEND.exists() else None):
            problems.append(f"{AMEND.name} differs from its hash record")
        for path, want in rec["listed"].items():
            if path.startswith(L2_BASES) and path.endswith(".py"):          # L2 modules are checked below, against the modules that are loaded
                continue
            p = ROOT / path
            if not p.exists() or S.sha256_file(p) != want:
                problems.append(f"{path} differs from the hash record")
    for path, h in A.l2_hashes().items():
        want = rec["listed"].get(path)
        if want is None:
            problems.append(f"L2 module {path} is loaded but not in the hash record")
        elif want != h:
            problems.append(f"L2 module {path} differs from the hash record")
    return problems


def enable_pypdf():
    """pypdf >= 5 is the optional dependency of L2's NCHS summary-PDF adapter and of the Census 1990s population table.  The sweep's interpreter has none;
    the one installed for the user (Xcode python 3.9 user site) is appended to sys.path, never prepended, never installed from the network."""
    try:
        import pypdf
        return pypdf.__version__
    except ImportError:
        pass
    import site
    cand = Path(site.getusersitepackages())
    if (cand / "pypdf").is_dir():
        sys.path.append(str(cand))
        import pypdf
        return pypdf.__version__
    return None


# ---------------------------------------------------------------------------------------------
# leaf imports
# ---------------------------------------------------------------------------------------------
class Leaf:
    loaded = False


LEAF = Leaf()


def load_leaf():
    if LEAF.loaded:
        return LEAF
    mode, core, cdc, acs, common = A.load_leaf_modules()
    LEAF.mode, LEAF.core, LEAF.cdc, LEAF.acs, LEAF.common = mode, core, cdc, acs, common
    LEAF.labor = importlib.import_module("scripts.l2_pipeline.connectors.outcome_v2_labor")
    LEAF.hist = importlib.import_module("scripts.l2_pipeline.connectors.historical")
    LEAF.rates = importlib.import_module("scripts.l2_pipeline.connectors.rates")
    LEAF.qcew_c56 = importlib.import_module("scripts.l2_pipeline.connectors.c56_qcew")
    LEAF.pypdf = enable_pypdf()
    LEAF.loaded = True
    return LEAF


def _grid_axes():
    """The 51 units of the store grid = sorted(L2 connectors.common.STATES.values()), the list sweep_core.grid_axes() gets from its full L2 import; read here from the
    leaf module so that this file never needs the whole connector package (whose __init__ other workers keep extending)."""
    return sorted(load_leaf().common.STATES.values())


S.grid_axes = _grid_axes


# ---------------------------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------------------------
def classify(sid, spec):
    ad = spec["adapter"]
    if ad == "acs":
        return "acs"
    if ad == "historical":
        return "historical"
    if ad == "historical_alias":
        return "historical_alias"
    if ad == "c56_qcew":
        return "qcew_c56"
    if ad == "cdc":
        ds = spec.get("dataset_id")
        if ds in ("y268-sna3", "bi63-dtpu"):
            return "historical"
        if ds == "nchs_natality_summary_pdf":
            return "nchs_pdf"
        if ds == "fpsi-y8tj":
            return "socrata_firearm"
        if ds == "xkb8-kh2a":
            return "socrata_vsrr"
    if ad == "labor":
        if sid.startswith("cps_"):
            return "cps"
        if sid.startswith("bls_qcew_"):
            return "qcew_wage"
        if sid in ("bls_laus_unemployment_rate", "bls_laus_employment", "bls_ces_private_employment", "bls_ces_private_weekly_earnings"):
            return "bls_api"
    raise ValueError(f"no group for {sid} ({ad})")


def adapter_ready_ids():
    out = []
    for r in S.inventory_rows():
        if r["row_type"] == "variable" and r["layer"] == "connector_catalog" and r["data_state"].startswith("adapter_ready"):
            out.append(r["outcome_key"])
    return sorted(out)


def population_filters(sid, spec, group):
    if sid in HEADLINE_POPULATION:
        return dict(HEADLINE_POPULATION[sid])
    pf = spec.get("population_filters")
    return dict(pf) if pf else {}


def build_plan():
    cat = S.read_json(S.CATALOG)["series"]
    inv = {r["outcome_key"]: r for r in S.inventory_rows() if r["row_type"] == "variable" and r["layer"] == "connector_catalog"}
    peers = S.read_json(RATE_CATALOG)["published_rate_peers"]
    L = load_leaf()
    plan = OrderedDict()
    for sid in adapter_ready_ids():
        spec = cat[sid]
        group = classify(sid, spec)
        kind = L.rates.count_kind(dict(series_id=sid, outcome_unit=spec["units"], source_outcome_unit=spec["units"]))
        plan[sid] = dict(sid=sid, group=group, family=inv[sid]["family"], adapter=spec["adapter"], units=spec["units"], years=list(spec["years"]),
                         catalog_status=spec.get("status"), population_filters=population_filters(sid, spec, group),
                         population_declared=bool(sid in HEADLINE_POPULATION), required_filters=list(spec.get("required_filters") or []),
                         definition_break=inv[sid]["definition_break"], inventory_state=inv[sid]["data_state"],
                         count_kind=kind, peer=peers.get(sid), alias_of=spec.get("alias_of"), value_field=spec.get("value_field"),
                         table=spec.get("table"))
    return plan


# ---------------------------------------------------------------------------------------------
# provenance readers for files that were obtained before this work
# ---------------------------------------------------------------------------------------------
def _sidecar(path):
    r = Path(str(path) + ".receipt.json")
    return json.loads(r.read_text(encoding="utf-8")) if r.is_file() else None


_SHA_CACHE = {}


def cached_sha(p):
    st = os.stat(p)
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        _SHA_CACHE[key] = S.sha256_file(p)
    return _SHA_CACHE[key]


def file_entry(rel_path, origin, url=None, retrieved=None, expect_sha=None, note=None, receipt=None):
    p = ROOT / rel_path
    sha = cached_sha(p)
    if expect_sha is not None and expect_sha != sha:
        raise RuntimeError(f"{rel_path}: sha256 differs from its recorded value")
    rc = receipt if receipt is not None else _sidecar(p)
    derived = []
    if rc:
        if rc.get("sha256") and rc["sha256"] != sha:
            raise RuntimeError(f"{rel_path}: sha256 differs from its byte receipt")
        url = url or rc.get("url") or rc.get("final_url")
        retrieved = retrieved or rc.get("downloaded_at_utc") or rc.get("retrieved_utc") or rc.get("downloaded_at")
        for sr in rc.get("source_receipts") or []:
            derived.append(dict(path=S.rel(sr["path"]), sha256=sr.get("sha256", ""), bytes=sr.get("size_bytes"), url=sr.get("url", ""),
                                retrieved_utc=sr.get("downloaded_at_utc", "")))
    out = dict(path=rel_path, sha256=sha, bytes=p.stat().st_size, url=url or "", retrieved_utc=retrieved or "", origin=origin, note=note or "")
    if derived:
        out["derived_from"] = derived
    return out


def _cps_ledger():
    out = {}
    with open(ROOT / "scripts/l2_batch1/DOWNLOADS.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["path"]] = r
    return out


def _laus_rate_sources_md():
    """Rows of the SOURCES.md table of the BLS API v1 download of the LAUS state unemployment rate (retrieved 2026-10-04, data/l4/insight_inputs)."""
    base = "data/l4/insight_inputs/downloads/bls_laus_state_unemployment"
    out = {}
    for line in (ROOT / base / "SOURCES.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(raw/\S+)\s*\|\s*(https://\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*([0-9a-f]{64})\s*\|", line)
        if m:
            out[m.group(1)] = dict(url=m.group(2), retrieved=m.group(3), bytes=int(m.group(4)), sha256=m.group(5))
    return base, out


def resolve_sources(sid, spec, group):
    """[file entries] for a series, from files that exist now; missing -> entry list plus the list of reasons.  Paths are repository-relative."""
    files, missing = [], []
    if group in ("historical", "historical_alias"):
        cat = S.read_json(S.CATALOG)["series"][sid]
        lp = cat["local_source_path"]
        files.append(file_entry(lp, "reused_local_l2", note="CDC data.cdc.gov Socrata export acquired by L2 (historical.acquire)"))
    elif group == "nchs_pdf":
        for y in range(2019, 2025):
            p = f"data/l2_pipeline/connectors_v2/nchs_natality_state/raw/nchs_natality_{y}.pdf"
            files.append(file_entry(p, "reused_local_l2", note="NCHS National Vital Statistics Reports, final births, summary PDF"))
    elif group in ("socrata_firearm", "socrata_vsrr"):
        sub = A.CDC_FIREARM_DIR if group == "socrata_firearm" else A.CDC_VSRR_DIR
        ds, years = ("fpsi-y8tj", range(2019, 2025)) if group == "socrata_firearm" else ("xkb8-kh2a", range(2015, 2026))
        for y in years:
            p = f"data/replication/shared_data/{sub}/raw/{ds}_{y}.csv"
            if (ROOT / p).is_file():
                files.append(file_entry(p, "downloaded_this_task"))
            else:
                missing.append(f"{p} not on disk")
    elif group == "cps":
        led = _cps_ledger()
        for r_path, r in sorted(led.items()):
            if not r_path.startswith("raw/monthly/") or r.get("status") != "ok":
                continue
            m = re.match(r"raw/monthly/(\d{4})_([a-z]{3})\.zip$", r_path)
            if not m:
                continue
            first = 2000 if sid.startswith("cps_single_mother") else 1994
            if first <= int(m.group(1)) <= 2025:
                files.append(file_entry("scripts/l2_batch1/" + r_path, "reused_local_l2", url=r["url"], retrieved=r["downloaded_at_utc"], expect_sha=r["sha256"],
                                        note="Census CPS Basic Monthly public-use file (L2 batch 1 download ledger)"))
    elif group == "qcew_c56":
        for y in range(2001, 2025):
            p = f"data/l2_pipeline/connectors_v2/bls_qcew_industry/raw/{y}_6244.csv"
            if (ROOT / p).is_file():
                files.append(file_entry(p, "reused_local_l2", note="BLS QCEW annual averages, NAICS 6244, extracted unchanged from the annual-by-industry archive"))
            else:
                missing.append(f"{p} not on disk")
    elif group == "qcew_wage":
        man = S.read_json(ROOT / "data/did_samples/paid_sick_leave/manifest.json")["files"]
        for y in range(2014, 2026):
            name = f"qcew_{y}_a_industry_10.csv"
            rec = man[name]
            files.append(file_entry(f"data/did_samples/paid_sick_leave/raw/{name}", "reused_local_other", url=f"https://data.bls.gov/cew/data/api/{y}/a/industry/10.csv",
                                    retrieved=rec["retrieved_utc"], expect_sha=rec["sha256"],
                                    note="BLS QCEW annual industry slice, all industries (10); URL is the catalog template, the manifest records access='BLS QCEW industry slice (annual)'"))
    elif group == "bls_api":
        if sid == "bls_laus_unemployment_rate":
            base, md = _laus_rate_sources_md()
            for name, rec in sorted(md.items()):
                files.append(file_entry(f"{base}/{name}", "reused_local_other", url=rec["url"], retrieved=rec["retrieved"], expect_sha=rec["sha256"],
                                        note="BLS Public Data API v1, series LAUST<fips>0000000000003, annual average M13 included; request body not kept"))
        elif sid in ("bls_laus_employment", "bls_ces_private_weekly_earnings"):
            pat = f"bls_v1_{sid}_"
            raw = ROOT / "data/l2_pipeline/connectors_v2/bls_api_v1/raw"
            names = sorted(p.name for p in raw.glob(pat + "*.json") if not p.name.endswith(".receipt.json"))
            for n in names:
                files.append(file_entry(f"data/l2_pipeline/connectors_v2/bls_api_v1/raw/{n}", "reused_local_l2_this_session",
                                        note="BLS Public Data API v1 (POST), downloaded by the L2 yield-fix step on 2026-10-16 with the L2 connector"))
            if not names:
                missing.append("no bls_api_v1 file on disk")
        else:                                                    # bls_ces_private_employment
            missing.append("BLS flat file route is blocked (HTTP 403 for the project's client); BLS API v1 daily quota of 25 queries was used up on 2026-10-16 by the L2 yield-fix step")
    elif group == "acs":
        files, missing = acs_sources(sid, spec)
    return files, missing


def acs_sources(sid, spec):
    """Derived whole-state DATs (2010-2019, L2 sequence route) and table-based DATs (2021-2024) of the series' table."""
    table = spec["table"].lower()
    files, missing = [], []
    years_wanted = [y for y in spec["years"] if y != 2020]
    seq_dirs = [ROOT / "data/replication/shared_data" / A.ACS_SEQ_DIR / "raw"]
    other = ROOT / "data/l2_pipeline/connectors_v2/census_acs1/raw/seq_b22003"
    tb_dirs = [ROOT / "data/replication/shared_data" / A.ACS_TB_DIR / "raw", ROOT / "data/l2_pipeline/connectors_v2/census_acs1/raw/tablebased"]
    states = [u.lower() for u in sorted(S_STATES())]
    for y in years_wanted:
        if y < 2010:
            missing.append(f"{y}: pre-2010 ACS1 needs the keyed Census API or a legacy-layout parser (not in L2)")
            continue
        if y <= 2019:
            hit = None
            dirs = list(seq_dirs) + ([other / str(y)] if table == "b22003" else [])
            for d in dirs:
                names = [d / f"acsdt1y{y}-{table}-{u}-derived.dat" for u in states]
                if all(n.is_file() and Path(str(n) + ".receipt.json").is_file() for n in names):
                    hit = names
                    break
            if hit is None:
                missing.append(f"{y}: derived whole-state files for {table} not complete on disk")
            else:
                for n in hit:
                    files.append(file_entry(S.rel(n), "downloaded_this_task" if "shared_data" in str(n) else "reused_local_l2_this_session",
                                            note="derived whole-state DAT (L2 sequence route); raw sources listed in its receipt"))
        else:
            hit = None
            for d in tb_dirs:
                p = d / f"acsdt1y{y}-{table}.dat"
                if p.is_file() and Path(str(p) + ".receipt.json").is_file():
                    hit = p
                    break
            if hit is None:
                missing.append(f"{y}: table-based file for {table} not on disk")
            else:
                files.append(file_entry(S.rel(hit), "downloaded_this_task" if "shared_data" in str(hit) else "reused_local_l2_this_session"))
    return files, missing


def S_STATES():
    L = load_leaf()
    return sorted(L.common.STATES.values())


# ---------------------------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------------------------
def make_request(L, p, files):
    """The ConnectorRequest the L2 adapters read: outcome-only options, the series' own population filters and the files found for it."""
    sid = p["sid"]
    group = p["group"]
    paths = [f["path"] for f in files]
    opts = dict(series_id=sid, measure_id=sid, connector="outcome_v15_" + sid, paths=paths, source_sha256={f["path"]: f["sha256"] for f in files},
                source_urls={f["path"]: f["url"] for f in files if f["url"]}, outcome_unit=p["units"], source_outcome_unit=p["units"],
                license="US federal government public data (see the byte receipts); public domain, cite the agency")
    if group == "historical_alias":
        opts.update(series_id=p["alias_of"], measure_id=p["alias_of"], connector="outcome_v4_" + p["alias_of"])
    if p["count_kind"] and p["peer"] is None:
        opts["outcome_scale"] = "rate"                       # L2's default for count outcomes (rates.resolved_scale on a design card)
    if group == "historical" and sid.startswith("nchs_historical_drug"):
        opts["population_filters"] = dict(p["population_filters"])
    years = tuple(p["years"])
    return L.common.ConnectorRequest(opts, dict(p["population_filters"]), years, tuple(sorted(L.common.STATES.values())), "annual", ROOT)


def build_panel(L, p, req):
    g = p["group"]
    if g in ("historical", "historical_alias"):
        panel = L.hist.build(req)
    elif g in ("nchs_pdf", "socrata_firearm", "socrata_vsrr"):
        panel = L.cdc.build(req)
    elif g in ("bls_api", "qcew_wage", "cps"):
        panel = L.labor.build(req)
    elif g == "qcew_c56":
        panel = L.qcew_c56.construct(req)
    elif g == "acs":
        panel = L.acs.build(req)
    else:
        raise ValueError(g)
    if g == "historical_alias":
        sid = p["sid"]
        from dataclasses import replace
        panel = replace(panel, rows=tuple(dict(r, series_id=sid) for r in panel.rows), metadata=tuple(dict(m, series_id=sid) for m in panel.metadata),
                        details=dict(panel.details, series_id=sid, alias_of=p["alias_of"]))
    return L.rates.apply_scale(req, panel)


def panel_obj(panel):
    return dict(rows=[dict(r) for r in panel.rows], metadata=[dict(m) for m in panel.metadata], inventory=[dict(i) for i in panel.inventory],
                limitations=list(panel.limitations), details=dict(panel.details))


def convert_one(task):
    """Worker: one series.  Returns a result record; writes parsed/<sid>.json once."""
    p, files, missing = task
    sid = p["sid"]
    t0 = time.time()
    rec = dict(sid=sid, group=p["group"], status=None, scale=None, seconds=None, n_rows=0, n_available=0, error=None, parked_reason=None, parsed=None)
    try:
        L = load_leaf()
        bad = pin_problems(files=False)
        if bad:
            raise RuntimeError("L2 module differs from the hash record before the task: " + "; ".join(bad[:3]))
        if missing and not files:
            rec.update(status=UNAVAILABLE, error="; ".join(missing)[:600])
            return rec
        if missing and p["group"] not in ("acs",):
            rec.update(status=UNAVAILABLE, error="; ".join(missing)[:600])
            return rec
        req = make_request(L, p, files)
        scale_rate = req.options.get("outcome_scale") == "rate"
        try:
            panel = build_panel(L, p, req)
            rec["scale"] = "rate" if scale_rate else ("count" if p["count_kind"] else "source")
        except L.common.ConnectorNotReady as exc:
            if not scale_rate:
                raise
            rec["parked_reason"] = str(exc)[:300]
            req0 = make_request(L, dict(p, count_kind=None), files)
            panel = build_panel(L, dict(p, count_kind=None), req0)
            rec["scale"] = "count"
        n_ok = sum(1 for m in panel.metadata if str(m.get("available")) in ("1", "True"))
        rec.update(n_rows=len(panel.rows), n_available=n_ok)
        obj = panel_obj(panel)
        obj["request"] = dict(series_id=sid, group=p["group"], filters=p["population_filters"], years=list(req.years), scale=rec["scale"],
                              paths=[f["path"] for f in files], n_paths=len(files), l2_import_mode=L.mode[:80], pypdf=LEAF.pypdf)
        path = PARSED / f"{sid}.json"
        text = json.dumps(S.clean(obj), sort_keys=True, ensure_ascii=False, allow_nan=False)
        PARSED.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if S.sha256_bytes(path.read_text(encoding="utf-8").encode()) != S.sha256_bytes(text.encode()):
                raise FileExistsError(f"{S.rel(path)} exists with other content: not overwritten")
        else:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(text)
        rec["parsed"] = S.rel(path)
        rec["parsed_sha256"] = S.sha256_file(path)
        bad = pin_problems(files=False)
        if bad:
            raise RuntimeError("L2 module differs from the hash record after the task: " + "; ".join(bad[:3]))
        if p["count_kind"] and p["peer"] is not None:
            rec["status"] = READY_PEER
        elif rec["parked_reason"]:
            rec["status"] = PARKED_DENOMINATOR
        else:
            rec["status"] = READY
        rec["l2_modules"] = A.l2_hashes()
    except Exception as exc:                                    # recorded, never worked around
        rec.update(status=FAILED, error=f"{type(exc).__name__}: {str(exc)[:500]}", trace=traceback.format_exc()[-800:])
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


def stage_plan(args):
    plan = build_plan()
    out = OrderedDict()
    for sid, p in plan.items():
        files, missing = resolve_sources(sid, p, p["group"])
        out[sid] = dict(p, n_source_files=len(files), source_bytes=sum(f["bytes"] for f in files), missing=missing,
                        origins=dict(Counter(f["origin"] for f in files)))
    obj = dict(rule_id=RULE_ID, version=VERSION, built_utc=utc_now(), n_series=len(out), series=out,
               groups=dict(Counter(p["group"] for p in out.values())), code=code_hashes())
    write_once_json(PLAN_FILE, obj)
    log(RUN_LOG, "plan", n=len(out))
    print(json.dumps(dict(n_series=len(out), groups=obj["groups"]), indent=1))
    return 0


def code_hashes():
    return {f: S.sha256_file(ROOT / f) for f in ["scripts/l3_meta/sweep_ext_series.py", "scripts/l3_meta/sweep_ext_acquire.py", "scripts/l3_meta/sweep_core.py"]}


def _init_worker():
    sys.dont_write_bytecode = True
    load_leaf()


def check_plan_identity(plan, resolved):
    """The files found now must be the files of the registered plan (same count and bytes per series)."""
    if not PLAN_FILE.exists():
        return [f"{S.rel(PLAN_FILE)} does not exist"]
    reg = S.read_json(PLAN_FILE)["series"]
    bad = []
    for sid, (files, missing) in resolved.items():
        r = reg.get(sid)
        if r is None or r["n_source_files"] != len(files) or r["source_bytes"] != sum(f["bytes"] for f in files) or r["missing"] != missing:
            bad.append(f"{sid}: source files differ from the plan")
    return bad


def stage_convert(args):
    load_leaf()
    bad = pin_problems()
    if bad:
        sys.exit("pins differ from the hash record, nothing run: " + "; ".join(bad[:6]))
    plan = build_plan()
    only = set(args.only.split(",")) if args.only else None
    resolved = {sid: resolve_sources(sid, p, p["group"]) for sid, p in plan.items()}
    if not SKIP_PINS:
        bad = check_plan_identity(plan, resolved)
        if bad:
            sys.exit("; ".join(bad[:6]))
    tasks = []
    for sid, p in plan.items():
        if only and sid not in only:
            continue
        files, missing = resolved[sid]
        tasks.append((p, files, missing))
    heavy = {"cps": 0, "acs": 1}
    tasks.sort(key=lambda t: (heavy.get(t[0]["group"], 2), t[0]["sid"]))
    t0 = time.time()
    results = []
    ctx = mp.get_context("spawn")
    workers = max(1, min(args.workers, len(tasks)))
    if workers == 1:
        _init_worker()
        for t in tasks:
            results.append(convert_one(t))
            print(f"[convert] {len(results)}/{len(tasks)} {results[-1]['sid']} {results[-1]['status']} ({time.time() - t0:.0f}s)", file=sys.stderr, flush=True)
    else:
        with ctx.Pool(processes=workers, initializer=_init_worker) as pool:
            for r in pool.imap_unordered(convert_one, tasks, chunksize=1):
                results.append(r)
                print(f"[convert] {len(results)}/{len(tasks)} {r['sid']} {r['status']} ({time.time() - t0:.0f}s)", file=sys.stderr, flush=True)
    for r in sorted(results, key=lambda r: r["sid"]):
        log(CONVERT_LOG, "converted", **{k: v for k, v in r.items() if k != "l2_modules"})
    mods = {}
    changed = set()
    for r in results:
        for k, v in (r.get("l2_modules") or {}).items():
            if k in mods and mods[k] != v:
                changed.add(k)
            mods.setdefault(k, v)
    log(CONVERT_LOG, "convert_done", n=len(results), counts=dict(Counter(r["status"] for r in results)), l2_modules=mods, l2_modules_changed_during_run=sorted(changed),
        seconds=round(time.time() - t0, 1), python=sys.version.split()[0], numpy=np.__version__, pypdf=LEAF.pypdf if LEAF.loaded else None)
    print(json.dumps(dict(counts=dict(Counter(r["status"] for r in results)), changed=sorted(changed)), indent=1))
    bad = [r for r in results if r["status"] == FAILED]
    for r in bad:
        print("FAILED", r["sid"], r["error"], file=sys.stderr)
    return 1 if changed else 0


# ---------------------------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------------------------
def value_of(v):
    """The number of a panel value as the L2 engine reads it (engine.panels._value: '' is missing, anything else goes through float()).  Adapters that preserve the
    source text (QCEW) hand over numeric strings.  Text that is not a number is a data error, not a missing cell."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    if isinstance(v, bool):
        raise ValueError("Outcome value is not numeric")
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            raise ValueError("Outcome value is not numeric: " + repr(v)[:40]) from None
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return float(v)
    return None


def entry_from_panel(obj, units_string=None):
    """{val, ok, sem, den, source, units_string} for sweep_core.store_arrays, from a parsed panel (rows and metadata in step)."""
    val, ok, sem, den = {}, {}, {}, {}
    for r, m in zip(obj["rows"], obj["metadata"]):
        key = (r["unit"], int(r["year"]))
        v = value_of(r.get("outcome_value"))
        has = v is not None
        val[key] = v if has else float("nan")
        ok[key] = bool(S.strict_eligible(m) and has)
        sem[key] = bool(S.semantic_eligible(m) and has)
        d = r.get("population_denominator")
        den[key] = float(d) if S._finite(d) else float("nan")
    unit = units_string or (obj["rows"][0].get("outcome_unit") if obj["rows"] else "")
    return dict(val=val, ok=ok, sem=sem, den=den, source="ext_l2_adapter", units_string=unit)


def stage_store(args):
    load_leaf()
    bad = pin_problems()
    if bad:
        sys.exit("pins differ from the hash record, nothing run: " + "; ".join(bad[:6]))
    log_rows = [json.loads(l) for l in open(CONVERT_LOG, encoding="utf-8")] if CONVERT_LOG.exists() else []
    status = {}
    for r in log_rows:
        if r["event"] == "converted":
            status[r["sid"]] = r
    store = OrderedDict()
    for sid in sorted(status):
        if status[sid]["status"] != READY:
            continue
        obj = json.loads((ROOT / status[sid]["parsed"]).read_text(encoding="utf-8"))
        if S.sha256_file(ROOT / status[sid]["parsed"]) != status[sid]["parsed_sha256"]:
            raise RuntimeError(f"{sid}: parsed file differs from its log line")
        store[sid] = entry_from_panel(obj)
    meta = dict(series=list(store), kind="ext", version=VERSION, convert_log_sha256=S.sha256_file(CONVERT_LOG))
    arrays = S.store_arrays(store, meta)
    if STORE_FILE.exists():
        if not S.arrays_equal(arrays, STORE_FILE):
            sys.exit(f"{S.rel(STORE_FILE)} exists and differs from the rebuilt store: not overwritten")
        print("store file exists and equals the rebuilt arrays")
    else:
        S.save_series_store(STORE_FILE, store, meta)
    log(RUN_LOG, "store", n=len(store), sha256=S.sha256_file(STORE_FILE))
    print(json.dumps(dict(n_series=len(store), sha256=S.sha256_file(STORE_FILE)), indent=1))
    return 0


# ---------------------------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------------------------
def design_screen():
    """Metadata-only screen against the 28 swept designs of SWEEP-V1: (id -> units, window, treated).  No outcome value is used."""
    out = OrderedDict()
    for f in sorted(SWEEP_V1.glob("*.npz")):
        if f.name.startswith("series_store"):
            continue
        z = np.load(f, allow_pickle=False)
        m = json.loads(str(z["meta_json"]))
        out[f.stem] = dict(units=m["units"], years=m["years"], treated=m["treated_primary"], scale_mode=m.get("scale_mode"))
    return out


def screen_series(store, sid, designs):
    """Per design: states of the design's unit list that are eligible in every year of the design's window, and the share of the treated states among them."""
    res = {}
    for did, d in designs.items():
        years = [y for y in d["years"] if y in store.yi]
        if len(years) < len(d["years"]):
            kept_units = []
        else:
            a = store.arr(sid, "ok")
            kept_units = [u for u in d["units"] if u in store.ui and all(a[store.ui[u], store.yi[y]] for y in d["years"])]
        treated = set(d["treated"])
        share = (len(treated & set(kept_units)) / len(treated)) if treated else 0.0
        res[did] = dict(complete_units=len(kept_units), treated_kept=round(share, 4))
    return res


def copy_l2_files(mods):
    """Byte copies of the pinned L2 modules next to the outputs (write once), as the sweep keeps them."""
    out = EXT / "l2_files"
    for path, h in mods.items():
        dst = out / path
        if dst.exists():
            if S.sha256_file(dst) != h:
                raise RuntimeError(f"{S.rel(dst)} exists with other content")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "xb") as fh:
            fh.write((ROOT / path).read_bytes())
        if S.sha256_file(dst) != h:
            raise RuntimeError(f"copy of {path} differs")
    return S.rel(out)


def stage_manifest(args):
    load_leaf()
    bad = pin_problems()
    if bad:
        sys.exit("pins differ from the hash record, nothing run: " + "; ".join(bad[:6]))
    plan = build_plan()
    rows = [json.loads(l) for l in open(CONVERT_LOG, encoding="utf-8")]
    conv = {r["sid"]: r for r in rows if r["event"] == "converted"}
    done = [r for r in rows if r["event"] == "convert_done"]
    if not done:
        sys.exit("convert stage not finished")
    store = S.SeriesStore(STORE_FILE)
    designs = design_screen()
    peers = {sid: p["peer"] for sid, p in plan.items() if p["peer"]}
    series, ledger = OrderedDict(), OrderedDict()
    for sid, p in plan.items():
        files, missing = resolve_sources(sid, p, p["group"])
        c = conv.get(sid, {})
        st = c.get("status") or UNAVAILABLE
        entry = dict(family=p["family"], group=p["group"], adapter=p["adapter"], status=st, scale=c.get("scale"), units=p["units"], population_filters=p["population_filters"],
                     population_declared_by_amendment=p["population_declared"], years_declared=[p["years"][0], p["years"][-1]], n_years_declared=len(p["years"]),
                     definition_break=p["definition_break"], peer=p["peer"], alias_of=p["alias_of"], parked_reason=c.get("parked_reason"), error=c.get("error"),
                     n_source_files=len(files), source_bytes=sum(f["bytes"] for f in files), origins=dict(Counter(f["origin"] for f in files)),
                     missing=missing, parsed=c.get("parsed"), parsed_sha256=c.get("parsed_sha256"), n_rows=c.get("n_rows"), n_available_cells=c.get("n_available"))
        for f in files:
            ledger.setdefault(f["path"], dict(f, series=[]))["series"].append(sid)
            for d in f.get("derived_from", []):                      # the raw inputs of a derived whole-state ACS file are ledger lines of their own
                ledger.setdefault(d["path"], dict(path=d["path"], sha256=d["sha256"], bytes=d["bytes"], url=d["url"], retrieved_utc=d["retrieved_utc"],
                                                  origin=f["origin"], note="raw input of a derived whole-state file (L2 sequence route)", series=[]))["series"].append(sid)
        parsed_inv = []
        if c.get("parsed"):
            parsed_obj = json.loads((ROOT / c["parsed"]).read_text(encoding="utf-8"))
            parsed_inv = parsed_obj.get("inventory", [])
            entry["units_store"] = parsed_obj["rows"][0].get("outcome_unit") if parsed_obj["rows"] else None
            for it in parsed_inv:                                    # files the adapter read besides the declared paths (CPS vintage dictionaries)
                rp = S.rel(it["path"])
                if rp not in ledger and (ROOT / rp).is_file():
                    led = _cps_ledger() if p["group"] == "cps" else {}
                    key = rp.replace("scripts/l2_batch1/", "")
                    row = led.get(key, {})
                    ledger[rp] = dict(path=rp, sha256=S.sha256_file(ROOT / rp), bytes=(ROOT / rp).stat().st_size, url=row.get("url") or it.get("url", ""),
                                      retrieved_utc=row.get("downloaded_at_utc", ""), origin="reused_local_l2", note=it.get("role", "adapter inventory"), series=[sid])
                elif rp in ledger:
                    ledger[rp]["series"].append(sid)
        if sid in store.sids:
            a = store.arr(sid, "ok")
            sem = store.arr(sid, "sem")
            yrs = [y for y in store.years if a[:, store.yi[y]].any()]
            screen = screen_series(store, sid, designs)
            n_ge80 = sum(1 for s in screen.values() if s["treated_kept"] >= 0.8)
            n_any = sum(1 for s in screen.values() if s["complete_units"] > 0)
            entry.update(in_store=True, eligible_cells=int(a.sum()), eligible_semantic=int(sem.sum()), strict_dropped=int(sem.sum() - a.sum()),
                         first_year=yrs[0] if yrs else None, last_year=yrs[-1] if yrs else None,
                         gap_years_inside=[y for y in range(yrs[0], yrs[-1] + 1) if y not in yrs] if yrs else [],
                         states_with_any_cell=int(a.any(axis=1).sum()), designs_with_complete_state=n_any, designs_treated_kept_ge_80=n_ge80,
                         design_screen=screen)
        else:
            entry.update(in_store=False)
        series[sid] = entry
    # source ledger: one line per file, the series that read it
    SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SOURCES_FILE.exists():
        sys.exit(f"{S.rel(SOURCES_FILE)} exists: not overwritten")
    with open(SOURCES_FILE, "x", encoding="utf-8") as fh:
        for path, f in ledger.items():
            fh.write(json.dumps(dict(f, series=sorted(set(f["series"]))), sort_keys=True, ensure_ascii=False) + "\n")
    # catalog table: the inventory columns of the 62 rows plus the extension columns
    inv = {r["outcome_key"]: r for r in S.inventory_rows() if r["row_type"] == "variable" and r["layer"] == "connector_catalog"}
    cols = list(next(iter(inv.values())).keys()) + ["ext_status", "ext_in_store", "ext_scale", "ext_population", "ext_first_year", "ext_last_year", "ext_peer", "ext_note"]
    if CATALOG_CSV.exists():
        sys.exit(f"{S.rel(CATALOG_CSV)} exists: not overwritten")
    with open(CATALOG_CSV, "x", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for sid, e in series.items():
            row = dict(inv[sid])
            row["data_state"] = "ext_parsed_in_store" if e["in_store"] else "ext_" + e["status"].lower()
            row.update(ext_status=e["status"], ext_in_store=int(e["in_store"]), ext_scale=e["scale"] or "", ext_population=json.dumps(e["population_filters"], sort_keys=True),
                       ext_first_year=e.get("first_year") or "", ext_last_year=e.get("last_year") or "", ext_peer=e["peer"] or "",
                       ext_note=(e["parked_reason"] or e["error"] or "; ".join(e["missing"][:2]))[:300])
            w.writerow(row)
    problems = pin_problems()
    mods = (done[-1].get("l2_modules") or {})
    for path, h in mods.items():
        if S.sha256_file(ROOT / path) != h:
            problems.append(f"{path} changed after the convert stage")
    l2_copy = copy_l2_files(mods)
    manifest = dict(rule_id=RULE_ID, version=VERSION, stage="manifest", built_utc=utc_now(), n_series=len(series),
                    counts=dict(Counter(e["status"] for e in series.values())), in_store=sum(1 for e in series.values() if e["in_store"]),
                    store_file=S.rel(STORE_FILE), store_sha256=S.sha256_file(STORE_FILE), catalog_csv=S.rel(CATALOG_CSV), catalog_csv_sha256=S.sha256_file(CATALOG_CSV),
                    sources_file=S.rel(SOURCES_FILE), sources_sha256=S.sha256_file(SOURCES_FILE), n_source_files=len(ledger),
                    series=series, code=code_hashes(), amendment_sha256=S.sha256_file(AMEND) if AMEND.exists() else None,
                    l2_modules_sha256=mods, l2_copies=l2_copy, l2_modules_changed_during_run=done[-1].get("l2_modules_changed_during_run"),
                    sweep_v1_designs_screened=list(designs), python=sys.version.split()[0], numpy=np.__version__, pypdf=done[-1].get("pypdf"), problems=problems)
    write_once_json(MANIFEST_FILE, manifest)
    log(RUN_LOG, "manifest", n=len(series), counts=manifest["counts"])
    print(json.dumps(dict(counts=manifest["counts"], in_store=manifest["in_store"], n_source_files=len(ledger)), indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", required=True, choices=["plan", "convert", "store", "manifest"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-pins", action="store_true", help="rehearsal before the amendment is hashed; never for the registered run")
    args = ap.parse_args(argv)
    global SKIP_PINS
    if args.skip_pins:
        SKIP_PINS = True
        os.environ["L3_SWEEP_EXT_SKIP_PINS"] = "1"             # spawned workers read it at import
    return dict(plan=stage_plan, convert=stage_convert, store=stage_store, manifest=stage_manifest)[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
