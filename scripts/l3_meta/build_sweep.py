#!/usr/bin/env python3
"""L3 outcome sweep, stages 1 to 3 (rule id EX-1, exploratory; SWEEP-V1).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_SWEEP_V1.md (hash record PREREG_AMEND_20261015_SWEEP_HASH.txt) and
OUTCOME_SWEEP_DESIGN_20261015.md.  READ-ONLY on every L2 file; nothing under scripts/l2_pipeline or data/l2_pipeline is opened for writing; no network.
Only outputs: data/l3_meta/sweep_v1/ (each file written once).

Stages (run in this order; the later stages refuse to start without the earlier outputs):
  series   the series store (89 annual catalog series on disk) and its manifest
  gate     the reproduction gate on the 34 registered estimated designs (operator path against the stored RESULT.json)
  sweep    every swept series on every design that passed the gate (aligned label bundles), one npz per design
  d3runs   the totals of the D3 pairs on the target's own mask (operators of the target's entry)

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_sweep.py --stage <series|gate|sweep|d3runs> [--workers N] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import sys
import time
import traceback
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
OUT = ROOT / "data/l3_meta/sweep_v1"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_SWEEP_V1.md"          # first amendment (rules of SWEEP-V1, D1-V1, D3-V2)
AMEND_B = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_SWEEP_V1B.md"        # amendment B (scale of the WONDER death-count series)
AMENDS = [AMEND, AMEND_B]
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_SWEEP_HASH.txt"
STORE_NAME = "series_store_v1.npz"
COUNT_NAME = "series_store_v1_count.npz"
LOG = OUT / "RUN_LOG.jsonl"
MAX_WORKERS = 6
CODE_FILES = ["scripts/l3_meta/sweep_core.py", "scripts/l3_meta/build_sweep.py", "scripts/l3_meta/sweep_d3.py"]


# ---------------------------------------------------------------------------------------------
# hash record, pinning, logging
# ---------------------------------------------------------------------------------------------
def parse_hash_record(path=HASH_REC):
    """The record of the first amendment, its numbered supplements (..._HASH_S*.txt) and the records of later amendments (..._HASH_B*.txt), merged."""
    rec = _parse_one(path)
    for extra in sorted(Path(path).parent.glob(Path(path).stem + "_[SB]*.txt")):
        e = _parse_one(extra)
        for k, v in e["listed"].items():
            if k in rec["listed"] and rec["listed"][k] != v:
                raise RuntimeError(f"{extra.name} contradicts the record for {k}")
            rec["listed"][k] = v
        for k, v in e["files"].items():
            rec["files"][k] = v
        rec.setdefault("supplements", []).append(extra.name)
    return rec


def _parse_one(path):
    text = Path(path).read_text(encoding="utf-8")
    files = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^file:\s*(\S+)\s*$", line)
        if m and i + 1 < len(lines):
            s = re.match(r"^sha256:\s*([0-9a-f]{64})\s*$", lines[i + 1])
            if s:
                files[m.group(1)] = s.group(1)
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    t = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    return {"files": files, "listed": listed, "t_amend_utc": t.group(1) if t else None}


def verify_records(rec=None):
    """Problems between the hash record and the files on disk (amendment, frozen files, inputs); L2 modules are checked by verify_l2."""
    rec = rec or parse_hash_record()
    problems = []
    for a in AMENDS:
        want = rec["files"].get(S.rel(a))
        if want is None or want != S.sha256_file(a):
            problems.append(f"{a.name} differs from its hash record")
    for path, want in rec["listed"].items():
        if path.startswith("scripts/l2_pipeline/") and path.endswith(".py"):
            continue
        p = ROOT / path
        if not p.exists() or S.sha256_file(p) != want:
            problems.append(f"{path} differs from the hash record")
    return problems


def verify_l2(rec=None):
    """Every loaded L2 module must be listed in the record with the recorded hash.  Returns problems."""
    rec = rec or parse_hash_record()
    problems = []
    for path, h in S.l2_files().items():
        want = rec["listed"].get(path)
        if want is None:
            problems.append(f"L2 module {path} is loaded but not in the hash record")
        elif want != h:
            problems.append(f"L2 module {path} differs from the hash record")
    return problems


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(event, **kw):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(utc=utc_now(), event=event, **S.clean(kw)), sort_keys=True) + "\n")


def amend_hashes():
    return {a.name: S.sha256_file(a) for a in AMENDS}


def code_hashes():
    return {f: S.sha256_file(ROOT / f) for f in CODE_FILES}


def write_once_json(path, obj):
    path = Path(path)
    if path.exists():
        sys.exit(f"{S.rel(path)} exists: not overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(S.clean(obj), indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


# ---------------------------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------------------------
def roster():
    """[(design_id, run folder (absolute), RESULT.json path, live row)] for the 34 registered estimated designs (EX-1 run rule)."""
    live = S.read_json(S.LIVE)
    out = []
    for d in live["designs"]:
        if d["status"] != "estimated" or d["source_group"] not in S.REGISTERED_GROUPS:
            continue
        did = d["design_id"]
        if did in S.RUN_OVERRIDE:
            folder = ROOT / S.RUN_OVERRIDE[did]
        else:
            folder = ROOT / Path(d["source"]["path"]).parent
            if S.sha256_file(ROOT / d["source"]["path"]) != d["source"]["sha256"]:
                raise RuntimeError(f"RESULT.json of {did} differs from the live set")
        out.append((did, folder, folder / "RESULT.json", d))
    return out


def policy_family(row):
    return row.get("policy_family") or row["design_id"]


# ---------------------------------------------------------------------------------------------
# worker plumbing
# ---------------------------------------------------------------------------------------------
_WORKER = {}


def _init_worker():
    sys.dont_write_bytecode = True
    S.load_l2()
    _WORKER["rec"] = parse_hash_record()
    problems = verify_l2(_WORKER["rec"])
    if problems:
        raise RuntimeError("; ".join(problems[:5]))


def _run_pool(func, tasks, workers, label):
    """Largest tasks first; results in completion order."""
    ctx = mp.get_context("spawn")
    results = []
    t0 = time.time()
    if workers <= 1:
        _init_worker()
        for t in tasks:
            results.append(func(t))
            print(f"[{label}] {len(results)}/{len(tasks)} done ({time.time() - t0:.0f}s)", file=sys.stderr, flush=True)
        return results
    with ctx.Pool(processes=workers, initializer=_init_worker) as pool:
        for r in pool.imap_unordered(func, tasks, chunksize=1):
            results.append(r)
            print(f"[{label}] {len(results)}/{len(tasks)} done ({time.time() - t0:.0f}s)", file=sys.stderr, flush=True)
    return results


# ---------------------------------------------------------------------------------------------
# stage 1: series store
# ---------------------------------------------------------------------------------------------
NOT_SWEPT = {"biennial": "BIENNIAL", "maternal_age": "POPULATION_UNSPECIFIED"}


def stage_series(args):
    table = S.catalog_series_table()
    L = S.load_l2()
    problems = verify_l2()
    if problems:
        sys.exit("L2 modules differ from the hash record: " + "; ".join(problems[:5]))
    # any registered card serves the constructor request; the result must not depend on it (checked below with a second card)
    design_rows = roster()
    def card_of(did, folder):
        manifest = S.read_json(folder / "FREEZE_FINAL.json")
        return L.registered_execution_card(folder, manifest)
    card_a = card_of(design_rows[0][0], design_rows[0][1])
    card_b = card_of(design_rows[-1][0], design_rows[-1][1])
    store, manifest_rows, skipped = OrderedDict(), OrderedDict(), OrderedDict()
    cat = S.read_json(S.CATALOG)["series"]
    t0 = time.time()
    for sid, s in table.items():
        if s["frequency"] != "annual":
            skipped[sid] = dict(code=NOT_SWEPT["biennial"], family=s["family"], frequency=s["frequency"])
            continue
        if s["adapter"] == "wonder" and s["required_filters"] == ["age_group"]:
            skipped[sid] = dict(code=NOT_SWEPT["maternal_age"], family=s["family"], frequency=s["frequency"])
            continue
        if s["adapter"] == "wonder":
            ser = S.build_wonder_series(sid, s)
            sources = [dict(path=e["raw_path"], sha256=S.sha256_file(ROOT / e["raw_path"])) for e in s["exports"]]
        else:
            ser = S.build_artifact_series(sid, s, card_a)
            sources = [dict(path=s["artifact"], sha256=S.sha256_file(ROOT / s["artifact"]))]
        store[sid] = ser
        n_ok = sum(1 for v in ser["ok"].values() if v)
        n_sem = sum(1 for v in ser["sem"].values() if v)
        years = sorted({y for (u, y), v in ser["ok"].items() if v})
        manifest_rows[sid] = dict(family=s["family"], units=ser["units_string"], source=ser["source"], sources=sources, cells=len(ser["ok"]), eligible=n_ok,
                                  eligible_semantic=n_sem, strict_dropped=n_sem - n_ok, first_year=years[0] if years else None,
                                  last_year=years[-1] if years else None, definition_break=s["definition_break"][:200], catalog_units=s["units"])
    # independence of the card: three artifact series rebuilt with a second card must be identical
    check = []
    for sid in [k for k, v in store.items() if v["source"] == "l2_construct"][:3] + [k for k in store if store[k]["source"] == "l2_construct"][-3:]:
        again = S.build_artifact_series(sid, table[sid], card_b)
        same = again["val"].keys() == store[sid]["val"].keys() and all(
            (again["ok"][k] == store[sid]["ok"][k]) and (np.isnan(again["val"][k]) == np.isnan(store[sid]["val"][k]))
            and (np.isnan(again["val"][k]) or again["val"][k] == store[sid]["val"][k]) for k in again["val"])
        check.append((sid, bool(same)))
    if not all(ok for _, ok in check):
        sys.exit(f"series store depends on the request card: {check}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / STORE_NAME
    meta = dict(series=list(store), catalog_sha256=S.sha256_file(S.CATALOG), inventory_sha256=S.sha256_file(S.INVENTORY_CSV))
    arrays = S.store_arrays(store, meta)
    resumed = False
    if path.exists():       # a first run wrote the store and stopped at the module check before the manifest: accept the file only if it holds exactly these arrays
        if not S.arrays_equal(arrays, path):
            sys.exit(f"{S.rel(path)} exists and differs from the rebuilt store: not overwritten")
        resumed = True
    else:
        S.save_series_store(path, store, meta)
    # the two WONDER series whose V15 constructor has a stale population contract are checked cell by cell elsewhere (tests); the others here
    wonder_check = {}
    for sid, s in table.items():
        if s["adapter"] == "wonder" and sid in store:
            wonder_check[sid] = wonder_vs_l2(sid, s, store[sid], card_a, cat[sid])
    manifest = dict(rule_id=S.RULE_ID, version="SWEEP-V1", stage="series", built_utc=utc_now(), store_file_reused_after_identical_rebuild=resumed, n_series=len(store), n_skipped=len(skipped),
                    store_file=S.rel(path), store_sha256=S.sha256_file(path), series=manifest_rows, not_swept=skipped, card_independence_check=check,
                    wonder_vs_l2_constructor=wonder_check, code=code_hashes(), amendment_sha256=amend_hashes(),
                    python=sys.version.split()[0], numpy=np.__version__, seconds=round(time.time() - t0, 1))
    problems = verify_l2()
    if problems:
        sys.exit("L2 modules differ from the hash record after the series build: " + "; ".join(problems[:5]))
    write_once_json(OUT / "SERIES_MANIFEST.json", manifest)
    log("series_store", n=len(store), sha256=manifest["store_sha256"])
    print(json.dumps({k: manifest[k] for k in ("n_series", "n_skipped", "store_sha256", "seconds")}, indent=1))
    bad = {k: v for k, v in wonder_check.items() if v.get("compared") and not v.get("equal")}
    if bad:
        sys.exit(f"WONDER reader differs from the L2 constructor: {bad}")
    return 0


def wonder_vs_l2(sid, s, mine, card, spec):
    """Cell-by-cell comparison of the WONDER reader with L2's constructor, where the constructor yields any available cell."""
    L = S.load_l2()
    import copy
    entry = {"role": "secondary", "outcome": sid, "connector": spec["registered_connector"],
             "paths": [str(ROOT / e["raw_path"]) for e in spec["exports"]], "source_sha256": {str(ROOT / e["raw_path"]): e["sha256"] for e in spec["exports"]}}
    base = copy.deepcopy(card)
    base["outcome"] = dict(base["outcome"])
    base["outcome"]["units"] = S.grid_axes()
    base["outcome"]["period_years"] = list(spec["years"])
    base["outcome"].pop("years", None)
    try:
        pd = L.construct(L.ConnectorRequest.from_card(L.variants.secondary_card(base, entry), ROOT))
    except Exception as exc:
        return dict(compared=False, reason=f"{type(exc).__name__}: {str(exc)[:120]}")
    ok, val = {}, {}
    for r, m in zip(pd.rows, pd.metadata):
        key = (r["unit"], int(r["year"]))
        v = r.get("outcome_value")
        has = S._finite(v)
        ok[key] = bool(S.strict_eligible(m) and has)
        val[key] = float(v) if has else float("nan")
    if not any(ok.values()):
        return dict(compared=False, reason="the L2 constructor yields no available cell for this series (stale contract)")
    keys = set(ok) | set(mine["ok"])
    diff_ok = sum(1 for k in keys if ok.get(k, False) != mine["ok"].get(k, False))
    diff_val = max([abs(val[k] - mine["val"][k]) for k in keys if ok.get(k, False) and mine["ok"].get(k, False)] or [0.0])
    return dict(compared=True, equal=bool(diff_ok == 0 and diff_val == 0.0), eligibility_differences=diff_ok, max_value_difference=diff_val,
                eligible_l2=int(sum(ok.values())), eligible_reader=int(sum(mine["ok"].values())))


def stage_series_count(args):
    """Amendment B 1.1: the count version of the 12 WONDER death-count series, in a second file."""
    problems = verify_records()
    if problems:
        sys.exit("hash record check failed: " + "; ".join(problems))
    table = S.catalog_series_table()
    S.load_l2()
    store_path = OUT / STORE_NAME
    if not store_path.exists():
        sys.exit("series store missing: run --stage series first")
    t0 = time.time()
    store, rows = OrderedDict(), OrderedDict()
    for sid, s in table.items():
        if s["adapter"] == "wonder" and s["frequency"] == "annual" and s["required_filters"] != ["age_group"] and S.is_death_count_series(sid, s):
            ser = S.build_wonder_series(sid, s, scale="count")
            store[sid] = ser
            rows[sid] = dict(family=s["family"], source=ser["source"], units=ser["units_string"], eligible=int(sum(1 for v in ser["ok"].values() if v)),
                             cells=len(ser["ok"]), sources=[dict(path=e["raw_path"], sha256=S.sha256_file(ROOT / e["raw_path"])) for e in s["exports"]])
    if len(store) != 12:
        sys.exit(f"expected 12 death-count series, found {len(store)}")
    # the count version must agree with the rate version on which cells have a published count and a positive population
    rate = S.SeriesStore(store_path)
    consistency = {}
    for sid, ser in store.items():
        more = [k for k, ok in ser["ok"].items() if rate.arr(sid, "ok")[rate.ui[k[0]], rate.yi[k[1]]] and not ok]
        consistency[sid] = dict(rate_eligible_but_count_not=len(more))
        if more:
            sys.exit(f"{sid}: {len(more)} cells are eligible as rates but not as counts")
    meta = dict(series=list(store), kind="count", rate_store_sha256=S.sha256_file(store_path))
    arrays = S.store_arrays(store, meta)
    path = OUT / COUNT_NAME
    reused = False
    if path.exists():
        if not S.arrays_equal(arrays, path):
            sys.exit(f"{S.rel(path)} exists and differs from the rebuilt store: not overwritten")
        reused = True
    else:
        S.save_series_store(path, store, meta)
    problems = verify_l2()
    if problems:
        sys.exit("L2 modules differ from the hash record: " + "; ".join(problems[:5]))
    manifest = dict(rule_id=S.RULE_ID, version="SWEEP-V1", stage="series_count", built_utc=utc_now(), store_file=S.rel(path), store_sha256=S.sha256_file(path),
                    n_series=len(store), series=rows, count_vs_rate_consistency=consistency, store_file_reused_after_identical_rebuild=reused,
                    code=code_hashes(), amendment_sha256=amend_hashes(), seconds=round(time.time() - t0, 1))
    write_once_json(OUT / "SERIES_MANIFEST_COUNT.json", manifest)
    log("series_count", n=len(store), sha256=manifest["store_sha256"])
    print(json.dumps({k: manifest[k] for k in ("n_series", "store_sha256", "seconds")}, indent=1))
    return 0


# ---------------------------------------------------------------------------------------------
# stage 2: gate
# ---------------------------------------------------------------------------------------------
def open_store():
    return S.SeriesStore(OUT / STORE_NAME, OUT / COUNT_NAME)


def gate_task(task):
    did, folder, result_path, store_path = task
    t0 = time.time()
    out = dict(design_id=did, folder=S.rel(folder), status="ERROR")
    try:
        store = open_store()
        ctx = S.load_design_context(did, folder)
        out["adapter"], out["mode"], out["context_errors"] = ctx.adapter, ctx.mode, ctx.errors
        stored_all = S.read_json(result_path)
        out["result_sha256"] = S.sha256_file(result_path)
        out["freeze_sha256"] = S.sha256_file(folder / "FREEZE_FINAL.json")
        entries = OrderedDict()
        for name, e in ctx.entries.items():
            stored = stored_all["registered_results"].get(name)
            if stored is None or stored.get("status") != "ESTIMATED":
                entries[name] = dict(gated=False, stored_status=(stored or {}).get("status"), entry_status=e.status, reason=e.reason)
                continue
            rec = S.gate_entry(ctx, name, stored)
            rec["gated"] = True
            rec["entry_status"], rec["mode"] = e.status, e.mode
            if ctx.adapter != "annual_full_year_v1":
                rec["builder"] = S.gate_builder_entry(ctx, name)
                sid = S.entry_series_id(ctx, name)
                if sid in store.sids:
                    rec["store"] = S.gate_store_entry(ctx, name, sid, store)
            entries[name] = rec
        out["entries"] = entries
        # scale mode of the design (amendment B 1.2): the version of the WONDER death-count series that its own registered entries match
        matches = {r["store"]["scale_match"] for r in entries.values() if r.get("gated") and r.get("store", {}).get("has_count_version")}
        out["scale_matches"] = sorted(str(m) for m in matches)
        out["scale_mode"] = (sorted(matches)[0] if len(matches) == 1 and None not in matches else "rate" if not matches else None)
        if ctx.adapter != "annual_full_year_v1" and ctx.primary is not None and ctx.primary.status == "ready":
            al = S.AlignedLabels(ctx.primary.declaration, ctx.primary.design, S.Builder(ctx).years)
            out["aligned_equals_engine_bundle"] = bool(S.aligned_equals_engine(al, ctx.primary.design))
        gated = [r for r in entries.values() if r.get("gated")]
        reasons = []
        if ctx.errors:
            reasons.append("context: " + "; ".join(ctx.errors))
        if not gated or "primary" not in entries or not entries["primary"].get("gated"):
            reasons.append("the primary is not an ESTIMATED entry that can be gated")
        for name, r in entries.items():
            if not r.get("gated"):
                continue
            if not r.get("ok"):
                reasons.append(f"{name}: reproduction failed ({r.get('error') or 'difference'})")
            b = r.get("builder")
            if b is not None and not b.get("ok") and "skipped" not in b:
                reasons.append(f"{name}: builder check failed ({b.get('error') or 'difference'})")
            st = r.get("store")
            if st is not None and not st.get("ok"):
                reasons.append(f"{name}: series-store check failed ({st.get('error') or 'difference'})")
        if out.get("aligned_equals_engine_bundle") is False:
            reasons.append("aligned bundle differs from the engine bundle")
        if out.get("scale_mode") is None:
            reasons.append("scale mode: entries of the design match different versions of the WONDER death-count series, or none")
        out["reasons"] = reasons
        out["status"] = "PASS" if not reasons else "REPRO_FAIL"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        out["traceback"] = traceback.format_exc()[-1500:]
    out["l2_problems"] = verify_l2(_WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def stage_gate(args):
    problems = verify_records()
    if problems:
        sys.exit("hash record check failed: " + "; ".join(problems))
    store_path = OUT / STORE_NAME
    if not store_path.exists() or not (OUT / COUNT_NAME).exists():
        sys.exit("series store missing: run --stage series and --stage series_count first")
    target = OUT / "GATE_REPORT.json"
    if target.exists():
        sys.exit(f"{S.rel(target)} exists: not overwritten")
    rows = roster()
    # largest designs first (cells of the primary result)
    def weight(r):
        try:
            return -os.path.getsize(r[2])
        except OSError:
            return 0
    tasks = [(did, folder, rp, store_path) for did, folder, rp, d in sorted(rows, key=weight)]
    if args.only:
        tasks = [t for t in tasks if t[0] in args.only]
    t0 = time.time()
    results = _run_pool(gate_task, tasks, min(args.workers, MAX_WORKERS), "gate")
    results = sorted(results, key=lambda r: r["design_id"])
    summary = Counter(r["status"] for r in results)
    report = dict(rule_id=S.RULE_ID, version="SWEEP-V1", stage="gate", run_utc=utc_now(), amendment_sha256=amend_hashes(),
                  live_set_sha256=S.sha256_file(S.LIVE), code=code_hashes(), summary=dict(summary), n_designs=len(results),
                  tolerance=S.TOL, designs=results,
                  python=sys.version.split()[0], numpy=np.__version__, seconds=round(time.time() - t0, 1), only=args.only or None)
    if args.dry_run:
        print(json.dumps(S.clean(dict(summary=dict(summary), designs=[{k: r.get(k) for k in ("design_id", "status", "reasons", "error", "seconds")} for r in results])), indent=1))
        return 0
    write_once_json(target, report)
    log("gate", summary=dict(summary), n=len(results))
    for r in results:
        print(f"{r['design_id'][:60]:60s} {r['status']:11s} {r.get('seconds')}s {'; '.join(r.get('reasons') or [])[:200]} {r.get('error', '')[:200]}")
    print(dict(summary))
    return 0


# ---------------------------------------------------------------------------------------------
# stage 3: sweep
# ---------------------------------------------------------------------------------------------
def load_gate_report():
    path = OUT / "GATE_REPORT.json"
    if not path.exists():
        sys.exit("GATE_REPORT.json missing: the sweep starts only after the reproduction gate (amendment 2.3)")
    return S.read_json(path)


def design_npz_path(did, sweep_dir=None):
    return Path(sweep_dir or OUT) / (did.replace(":", "_") + ".npz")


def sweep_task(task):
    did, folder, result_path, store_path, scale_mode, sweep_dir = task
    t0 = time.time()
    out = dict(design_id=did, status="ERROR")
    try:
        full_store = open_store()
        store = full_store.view(scale_mode)
        table = S.catalog_series_table()
        series_table = {sid: table[sid] for sid in full_store.sids}
        ctx = S.load_design_context(did, folder)
        res = S.sweep_design(ctx, store, series_table)
        recs = res["records"]
        sids = list(recs)
        K = len(sids)
        placebo = np.vstack([recs[k]["placebo"] for k in sids])
        stats = [S.draw_statistics(recs[k]["att"], recs[k]["placebo"]) if recs[k]["code"] in ("SUPPORTED", "RI_FEASIBLE_LT_90") else
                 dict(sd=float("nan"), cutoff=float("nan"), p2=float("nan"), z=float("nan")) for k in sids]
        # amendment 5.8: the sweep equals the gate for the primary's own series
        consistency = dict(checked=False)
        stored = S.read_json(result_path)["registered_results"]["primary"]
        sid_p = S.entry_series_id(ctx, "primary")
        if sid_p in recs and recs[sid_p]["fingerprint"] and recs[sid_p]["fingerprint"] == S.mask_fingerprint(ctx.primary.design):
            r = recs[sid_p]
            att_ok = S.agree(r["att"], stored["result"]["overall"]["att"])
            sd_ = stored["noise_floor"]["permutation_draws"]
            fin = r["placebo"][np.isfinite(r["placebo"])]
            dr_ok = len(fin) == len(sd_) and S.max_scaled_diff(fin, sd_) <= S.TOL
            consistency = dict(checked=True, series=sid_p, att_equal=bool(att_ok), draws_equal=bool(dr_ok))
            if not (att_ok and dr_ok):
                raise RuntimeError("SWEEP_GATE_MISMATCH: the sweep of the primary's own series differs from the stored result")
        meta = dict(rule_id=S.RULE_ID, version="SWEEP-V1", design_id=did, run_folder=S.rel(folder), result_sha256=S.sha256_file(result_path),
                    freeze_sha256=S.sha256_file(folder / "FREEZE_FINAL.json"), mode=ctx.mode, adapter=ctx.adapter, units=res["units"],
                    treated_primary=res["treated_primary"], years=res["years"], aligned_equals_engine=res["aligned_equals_engine"],
                    patterns=res["patterns"], consistency_with_gate=consistency, scale_mode=scale_mode,
                    series_store_sha256=S.sha256_file(store_path), series_store_count_sha256=S.sha256_file(OUT / COUNT_NAME),
                    amendment_sha256=amend_hashes(), code=code_hashes(), built_utc=utc_now())
        path = design_npz_path(did, sweep_dir)
        if path.exists():
            raise RuntimeError(f"{S.rel(path)} exists: not overwritten")
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
        problems = verify_l2(_WORKER.get("rec"))
        if problems:
            raise RuntimeError("L2 module changed during the task, nothing written: " + "; ".join(problems[:3]))
        part = path.with_name(path.name + ".part")
        with open(part, "xb") as fh:
            np.savez_compressed(fh, **arr)
        if path.exists():
            raise RuntimeError(f"{S.rel(path)} exists: not overwritten")
        os.rename(part, path)
        out.update(status="OK", file=str(path) if Path(sweep_dir) != OUT else S.rel(path), codes=dict(Counter(recs[k]["code"] for k in sids)), n_patterns=len(res["patterns"]),
                   consistency=consistency, sha256=S.sha256_file(path))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["l2_problems"] = verify_l2(_WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def swept_designs(report):
    """[(design_id, folder, result_path, store_path)] for the designs that passed the gate and have a certified secondary adapter, and the skipped ones."""
    rows = {r[0]: r for r in roster()}
    todo, skipped = [], {}
    n_bad = sum(1 for d in report["designs"] if d["status"] != "PASS")
    if n_bad > len(report["designs"]) / 3:
        sys.exit(f"{n_bad} of {len(report['designs'])} designs did not pass the gate: systematic failure (amendment 2.3), stopped")
    for d in report["designs"]:
        did = d["design_id"]
        if d["status"] != "PASS":
            skipped[did] = "REPRO_FAIL" if d["status"] == "REPRO_FAIL" else d["status"]
        elif d.get("adapter") == "annual_full_year_v1":
            skipped[did] = "NO_SECONDARY_ADAPTER"
        else:
            r = rows[did]
            todo.append((did, r[1], r[2], OUT / STORE_NAME, d.get("scale_mode") or "rate"))
    return todo, skipped



def stage_sweep(args):
    problems = verify_records()
    if problems:
        sys.exit("hash record check failed: " + "; ".join(problems))
    report = load_gate_report()
    if amend_hashes() != report["amendment_sha256"] or S.sha256_file(S.LIVE) != report["live_set_sha256"]:
        sys.exit("the gate report was made with another amendment or live set")
    todo, skipped = swept_designs(report)
    sweep_dir = Path(args.sweep_out) if args.sweep_out else OUT
    real = sweep_dir.resolve() == OUT.resolve()
    if not real and str(sweep_dir.resolve()).startswith(str(ROOT.resolve())):
        sys.exit("--sweep-out inside the repository is only for the real folder")
    todo = [t + (sweep_dir,) for t in todo]
    if args.only:
        todo = [t for t in todo if t[0] in args.only]
    def weight(t):
        try:
            return -os.path.getsize(t[2])
        except OSError:
            return 0
    todo = sorted(todo, key=weight)
    if real:
        log("sweep_start", designs=len(todo), skipped=skipped, workers=min(args.workers, MAX_WORKERS))
    t0 = time.time()
    results = _run_pool(sweep_task, todo, min(args.workers, MAX_WORKERS), "sweep")
    results = sorted(results, key=lambda r: r["design_id"])
    summary = dict(rule_id=S.RULE_ID, version="SWEEP-V1", stage="sweep", run_utc=utc_now(), n_swept=sum(1 for r in results if r["status"] == "OK"),
                   n_error=sum(1 for r in results if r["status"] != "OK"), skipped=skipped, designs=results, code=code_hashes(),
                   amendment_sha256=amend_hashes(), gate_report_sha256=S.sha256_file(OUT / "GATE_REPORT.json"), seconds=round(time.time() - t0, 1))
    write_once_json(sweep_dir / "SWEEP_RUN.json", summary)
    if real:
        log("sweep_done", n_swept=summary["n_swept"], n_error=summary["n_error"])
    for r in results:
        print(f"{r['design_id'][:60]:60s} {r['status']:6s} {r.get('seconds')}s patterns={r.get('n_patterns')} {r.get('codes')} {r.get('error', '')[:300]}")
    return 0 if summary["n_error"] == 0 else 1


# ---------------------------------------------------------------------------------------------
# stage 3b: the totals of the D3 pairs on the target's own mask (code in sweep_d3.py)
# ---------------------------------------------------------------------------------------------
def d3_task(task):
    did, folder, result_path, store_path, scale_mode, primary_ids = task
    t0 = time.time()
    try:
        import sweep_d3
        store = open_store().view(scale_mode)
        out = sweep_d3.run_design(did, folder, result_path, store, primary_ids, scale_mode)
        out["status"] = "OK"
    except Exception as exc:
        out = dict(design_id=did, status="ERROR", error=f"{type(exc).__name__}: {str(exc)[:500]}", traceback=traceback.format_exc()[-2000:], pairs=[])
    out["l2_problems"] = verify_l2(_WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def stage_d3runs(args):
    problems = verify_records()
    if problems:
        sys.exit("hash record check failed: " + "; ".join(problems))
    report = load_gate_report()
    run_path = OUT / "SWEEP_RUN.json"
    if not run_path.exists():
        sys.exit("SWEEP_RUN.json missing: run --stage sweep first")
    target = OUT / "D3_RUNS.json"
    if target.exists():
        sys.exit(f"{S.rel(target)} exists: not overwritten")
    live = {d["design_id"]: d for d in S.read_json(S.LIVE)["designs"]}
    rows = {r[0]: r for r in roster()}
    tasks = []
    for d in report["designs"]:
        if d["status"] != "PASS":
            continue
        r = rows[d["design_id"]]
        primary_ids = [p.get("outcome_id") for p in (live[d["design_id"]].get("primary_outcomes") or [])]
        tasks.append((d["design_id"], r[1], r[2], OUT / STORE_NAME, d.get("scale_mode") or "rate", primary_ids))
    if args.only:
        tasks = [t for t in tasks if t[0] in args.only]
    t0 = time.time()
    results = sorted(_run_pool(d3_task, tasks, min(args.workers, MAX_WORKERS), "d3runs"), key=lambda r: r["design_id"])
    errors = [r for r in results if r["status"] != "OK"]
    summary = dict(rule_id=S.RULE_ID, version="SWEEP-V1", stage="d3runs", run_utc=utc_now(), n_designs=len(results), n_error=len(errors),
                   n_pairs=sum(len(r["pairs"]) for r in results), n_pairs_with_total=sum(1 for r in results for p in r["pairs"] if p.get("t") is not None),
                   code=code_hashes(), amendment_sha256=amend_hashes(), gate_report_sha256=S.sha256_file(OUT / "GATE_REPORT.json"),
                   sweep_run_sha256=S.sha256_file(run_path), designs=[r for r in results if r["pairs"] or r["status"] != "OK"], seconds=round(time.time() - t0, 1))
    write_once_json(target, summary)
    log("d3runs", n_pairs=summary["n_pairs"], n_with_total=summary["n_pairs_with_total"], n_error=summary["n_error"])
    for r in results:
        for p in r["pairs"]:
            print(f"{r['design_id'][:56]:56s} {p['pair']} {p['target_entry'][:40]:40s} {p.get('code') or 'computed':18s} mask={p.get('n_mask_cells')} feasible={p.get('n_feasible')}")
        if r["status"] != "OK":
            print(f"{r['design_id']} ERROR {r.get('error')}")
    return 0 if not errors else 1


# ---------------------------------------------------------------------------------------------
# manifest of the sweep folder
# ---------------------------------------------------------------------------------------------
def stage_manifest(args):
    problems = verify_records()
    if problems:
        sys.exit("hash record check failed: " + "; ".join(problems))
    target = OUT / "SWEEP_MANIFEST.json"
    if target.exists():
        sys.exit(f"{S.rel(target)} exists: not overwritten")
    rec = parse_hash_record()
    l2_listed = {k: v for k, v in rec["listed"].items() if k.startswith("scripts/l2_pipeline/") and k.endswith(".py")}
    copies = OUT / "l2_files"
    for path, h in l2_listed.items():
        if S.sha256_file(ROOT / path) != h:
            sys.exit(f"L2 module {path} differs from the hash record: manifest not written")
        dest = copies / path
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "xb") as fh:
                fh.write((ROOT / path).read_bytes())
        if S.sha256_file(dest) != h:
            sys.exit(f"copy of {path} differs")
    files = {p.name: S.sha256_file(p) for p in sorted(OUT.glob("*")) if p.is_file() and p.name not in ("RUN_LOG.jsonl", "SWEEP_MANIFEST.json")}
    run = S.read_json(OUT / "SWEEP_RUN.json")
    gate = S.read_json(OUT / "GATE_REPORT.json")
    manifest = dict(rule_id=S.RULE_ID, version="SWEEP-V1", stage="manifest", built_utc=utc_now(), live_set_sha256=S.sha256_file(S.LIVE),
                    amendments=amend_hashes(), hash_records={n: S.sha256_file(HASH_REC.parent / n) for n in ["PREREG_AMEND_20261015_SWEEP_HASH.txt"] + rec.get("supplements", [])},
                    code={f: S.sha256_file(ROOT / f) for f in CODE_FILES + ["scripts/l3_meta/sweep_d3.py"]},
                    l2_modules_pinned=l2_listed, l2_copies=S.rel(copies), files=files,
                    designs={d["design_id"]: dict(gate=d["status"], scale_mode=d.get("scale_mode"), adapter=d.get("adapter"), mode=d.get("mode"),
                                                  result_sha256=d.get("result_sha256"), freeze_sha256=d.get("freeze_sha256"), folder=d.get("folder"),
                                                  reasons=d.get("reasons"), sweep_file=next((x.get("file") for x in run["designs"] if x["design_id"] == d["design_id"] and x["status"] == "OK"), None))
                             for d in gate["designs"]},
                    skipped_in_sweep=run.get("skipped"), python=sys.version.split()[0], numpy=np.__version__)
    write_once_json(target, manifest)
    log("manifest", sha256=S.sha256_file(target))
    print(json.dumps({"files": len(files), "l2_copies": len(l2_listed), "sha256": S.sha256_file(target)}, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", required=True, choices=["series", "series_count", "gate", "sweep", "d3runs", "manifest"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing (gate only)")
    ap.add_argument("--only", nargs="*", help="restrict a stage to these design ids")
    ap.add_argument("--sweep-out", default=None, help="sweep stage: write the design files here instead of the real folder (tests; must be outside the repository)")
    args = ap.parse_args(argv)
    if args.stage == "series":
        return stage_series(args)
    if args.stage == "series_count":
        return stage_series_count(args)
    if args.stage == "gate":
        return stage_gate(args)
    if args.stage == "sweep":
        return stage_sweep(args)
    if args.stage == "d3runs":
        return stage_d3runs(args)
    if args.stage == "manifest":
        return stage_manifest(args)
    sys.exit("stage not available in this file version")


if __name__ == "__main__":
    sys.exit(main())
