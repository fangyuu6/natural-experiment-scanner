#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version SELREF-V1 (rule id EX-1): does a selection-aware reference calibrate the noise lines?

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_SELREF_V1.md (hash record PREREG_AMEND_20261016_SELREF_HASH.txt), hashed before any statistic under a
block-restricted reassignment existed.  The reference is rebuilt by selref_core (the sweep's operators, read-only); D2, D1 and the E1 engine are the hashed
builders applied unchanged to the new design files; Q1 is recomputed from the primary entries of the 33 registered-format designs whose reference is
reproducible.  Every statistic is exploratory: no "significant" label, no multiplicity adjustment, no licensed sentence.

Nothing under scripts/l2_pipeline or data/l2_pipeline is opened for writing (L2 modules are imported read-only, bytecode off).  No network.  Only output:
data/l3_meta/selref_v1/ (each file written once).

Stages (in this order):
  gate     the uniform reference U on five designs must reproduce the SWEEP-V1 files (amendment 5.1) and the Q1 lines
  refs     the design files of the references (default: all seven, one pool)
  q1refs   the Q1 primary reference of the 33 designs under each reference
  analyze  D2, spread, Q1, D1, paired changes, power, verdict (amendment 3 and 4)

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_selref.py --stage gate|refs|q1refs|analyze [--workers N] [--variants ...] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
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
import build_n1 as N1  # noqa: E402  (E1 engine)
import build_d1 as D1  # noqa: E402  (family statistics, D1 rows)
import build_d2 as D2  # noqa: E402  (D2 cells, null width)
import build_d3 as D3  # noqa: E402  (hash record of the D questions)
import build_sweep as B  # noqa: E402  (hash record, pool, roster)
import reconcile_d2 as RC  # noqa: E402  (coding files)
import selref_core as R  # noqa: E402
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "SELREF", "SELREF-V1"
OUT = ROOT / "data/l3_meta/selref_v1"
REFS = OUT / "refs"
Q1DIR = OUT / "q1"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SELREF_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_SELREF_HASH.txt"
EX1_JSON = ROOT / "data/l3_meta/ex1_v3/RESULTS_EX1_V3.json"
D1_JSON = ROOT / "data/l3_meta/d1_v1/RESULTS_D1_V1.json"
D2_JSON = ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"
OUT_NAME = "RESULTS_SELREF_V1.json"
CODE_FILES = ["scripts/l3_meta/selref_core.py", "scripts/l3_meta/build_selref.py", "scripts/l3_meta/sweep_core.py", "scripts/l3_meta/build_sweep.py",
              "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_d2.py", "scripts/l3_meta/build_d3.py", "scripts/l3_meta/build_n1.py", "scripts/l3_meta/reconcile_d2.py"]
GATE_DESIGNS = ["run2_energy_public_benefit_fund", "r1011_speed_limit_65_rural_interstate_1987", "run4_lit_nurse_licensure_compact",
                "b1010b_pdmp_mandatory_query", "run8_large_capacity_magazine_ban"]
SEED_SPREAD = [20261011, 36, 1]
SEED_Q1 = [20261011, 36, 2]
SEED_PAIR = [20261011, 36, 3]
B_BOOT = 9999
MAX_WORKERS = 10
LOW_SUPPORT = 0.75
SPREAD_TOL = 0.005
ALL_IDS = tuple(R.REFERENCES)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def code_hashes():
    return {f: S.sha256_file(ROOT / f) for f in CODE_FILES}


def write_once_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError:
        sys.exit(f"{S.rel(path)} exists: not overwritten (use a new folder for a new version)")


def write_once_json(path, obj):
    write_once_text(path, json.dumps(S.clean(obj), indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


def log(out_dir, **kw):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(utc=utc_now(), **S.clean(kw)), sort_keys=True) + "\n")


# ---------------------------------------------------------------------------------------------
# hash record
# ---------------------------------------------------------------------------------------------
def verify_selref_records(amend=AMEND, hash_rec=HASH_REC):
    """Problems between the SELREF hash record and the files on disk.  Returns (problems, record)."""
    rec = B.parse_hash_record(hash_rec)
    problems = []
    want = rec["files"].get(S.rel(amend))
    if want is None or want != S.sha256_file(amend):
        problems.append(f"{Path(amend).name} differs from its hash record")
    for path, want in rec["listed"].items():
        if path.startswith("scripts/l2_pipeline/") and path.endswith(".py"):
            continue
        p = ROOT / path
        if not p.exists() or S.sha256_file(p) != want:
            problems.append(f"{path} differs from the SELREF hash record")
    return problems, rec


def all_record_problems():
    problems = B.verify_records() + D3.verify_records()
    p2, _ = D2.verify_d2_records()
    p3, rec = verify_selref_records()
    return problems + p2 + p3, rec


# ---------------------------------------------------------------------------------------------
# pure statistics
# ---------------------------------------------------------------------------------------------
def multiplicity(draw, F):
    """c[b, f] = number of times block f is drawn in replicate b (draw: B x F integers in [0, F))."""
    Bn = draw.shape[0]
    flat = (draw + F * np.arange(Bn)[:, None]).ravel()
    return np.bincount(flat, minlength=Bn * F).reshape(Bn, F).astype(float)


def pct(values, lo=2.5, hi=97.5):
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    q = np.percentile(a, [lo, hi])
    return [float(q[0]), float(q[1])]


def spread_design_stats(z, zstar):
    """Per-design quantities of the closed form (amendment 3.2 (ii)): z (k,) observed standardised ATTs, zstar (k, n) standardised placebo draws on the common draws."""
    z = np.asarray(z, float)
    zs = np.asarray(zstar, float)
    S_db = zs.sum(axis=0)
    return dict(n_series=int(z.size), o1=float(z.sum()), o2=float((z ** 2).sum()), a=float((zs ** 2).mean(axis=1).sum()), mu=float(S_db.mean()), nu=float(S_db.var()))


def spread_from_sums(n, o1, o2, a, mu, nu):
    """kappa_V = sqrt(V_obs / V_ref) from the summed per-design quantities (arrays or scalars); NaN when fewer than two series."""
    n = np.asarray(n, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        v_obs = (o2 - o1 ** 2 / n) / (n - 1)
        v_ref = (a - (nu + mu ** 2) / n) / (n - 1)
        out = np.sqrt(v_obs / v_ref)
    return np.where(n > 1, out, np.nan), v_obs, v_ref


def spread_point(stats):
    t = {k: sum(s[k] for s in stats) for k in ("n_series", "o1", "o2", "a", "mu", "nu")}
    k, vo, vr = spread_from_sums(t["n_series"], t["o1"], t["o2"], t["a"], t["mu"], t["nu"])
    return float(k), float(vo), float(vr)


def bootstrap_spread(stats, blocks, B_=B_BOOT, seed=SEED_SPREAD):
    """Percentile bootstrap of kappa_V over the policy families (amendment 3.2 (ii)).  stats: per-design dicts; blocks: policy family of each design."""
    names = sorted(set(blocks))
    pos = {f: i for i, f in enumerate(names)}
    F = len(names)
    cols = {k: np.zeros(F) for k in ("n_series", "o1", "o2", "a", "mu", "nu")}
    for s, f in zip(stats, blocks):
        for k in cols:
            cols[k][pos[f]] += s[k]
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, F, size=(B_, F))
    c = multiplicity(draw, F)
    tot = {k: c @ cols[k] for k in cols}
    ks, _, _ = spread_from_sums(tot["n_series"], tot["o1"], tot["o2"], tot["a"], tot["mu"], tot["nu"])
    return ks, F


def spread_block(blocks_d2, policy_family_of, B_=B_BOOT):
    """Point ratio (D2's own null_width), the closed form, its bootstrap interval, and the centred form, for one set of NC blocks (D2.nc_blocks output)."""
    if not blocks_d2:
        return None
    nw = D2.null_width(blocks_d2)
    stats = [spread_design_stats(b["z"], b["zstar"]) for b in blocks_d2]
    fams = [policy_family_of[b["design_id"]] for b in blocks_d2]
    k_cf, v_obs, v_ref = spread_point(stats)
    ks, F = bootstrap_spread(stats, fams, B_)
    # centred form: u_j = z_j - mean_b z*_jb against z*_jb - mean_b z*_jb
    cstats = []
    for b in blocks_d2:
        zs = np.asarray(b["zstar"], float)
        m = zs.mean(axis=1)
        cstats.append(spread_design_stats(np.asarray(b["z"], float) - m, zs - m[:, None]))
    kc, vo_c, vr_c = spread_point(cstats)
    return dict(point_d2_null_width=nw, kappa_v=nw["ratio_observed_to_reference_mean"], kappa_closed_form=k_cf, V_obs=v_obs, V_ref=v_ref,
                closed_form_vs_simulation_rel_diff=abs(k_cf - nw["ratio_observed_to_reference_mean"]) / nw["ratio_observed_to_reference_mean"],
                interval95=pct(ks), replicates=int(ks.size), defined=int(np.isfinite(ks).sum()), blocks=F, seed=SEED_SPREAD, B=B_,
                centred=dict(kappa_closed_form=kc, V_obs=vo_c, V_ref=vr_c, mean_u=float(np.mean(np.concatenate([np.asarray(b["z"], float) - np.asarray(b["zstar"], float).mean(axis=1) for b in blocks_d2])))))


def level_label(interval):
    lo, hi = interval
    return "OVER-REJECTION" if lo > 0 else "CONSERVATISM" if hi < 0 else "CALIBRATION"


def spread_label(interval):
    lo, hi = interval
    return "WIDER" if lo > 1 else "NARROWER" if hi < 1 else "NOT SEPARATED FROM 1"


def change_label(interval):
    lo, hi = interval
    return "REDUCED" if lo > 0 else "INCREASED" if hi < 0 else "NOT SEPARATED"


def verdict_text(level, change, low_support=False, computable=True):
    """Amendment 4.2, evaluated in the order written."""
    if not computable or level is None or change is None:
        return "NOT COMPUTABLE"
    if level == "CONSERVATISM":
        v = "OVER-CORRECTED"
    elif change == "INCREASED":
        v = "WORSE: THE RESTRICTED REFERENCE IS NARROWER THAN THE DATA"
    elif change == "REDUCED" and level == "CALIBRATION":
        v = "THE SELECTION-AWARE REFERENCE CALIBRATES THE LINES"
    elif change == "REDUCED" and level == "OVER-REJECTION":
        v = "PARTLY CALIBRATED"
    elif change == "NOT SEPARATED" and level == "CALIBRATION":
        v = "CALIBRATED, BUT NOT SEPARATED FROM THE UNIFORM REFERENCE"
    elif change == "NOT SEPARATED" and level == "OVER-REJECTION":
        v = "NO CHANGE: SELECTION ON THESE COVARIATES DOES NOT EXPLAIN THE EXCESS"
    else:
        v = "NOT COMPUTABLE"
    return v + (" (LOW SUPPORT)" if low_support else "")


# ---- Q1 -------------------------------------------------------------------------------------
def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def q1_roster(ex1):
    """The Q1-V3 rule `has_noise_line` (as build_cal.q1_designs): estimated, finite b, positive noise SD and cutoff, support_pass not false."""
    out = []
    for r in ex1["designs"]:
        if (r.get("status") == "estimated" and finite(r.get("b")) and finite(r.get("noise_sd")) and r["noise_sd"] > 0 and finite(r.get("noise_cutoff"))
                and r["noise_cutoff"] > 0 and r.get("support_pass") is not False):
            out.append(dict(design_id=r["design_id"], policy_family=r["policy_family"], group3=r.get("group3"), b=float(r["b"]), cut=float(r["noise_cutoff"])))
    return out


def q1_counts(matched):
    """matched: list of dicts with b_new, cut_new, b_old, cut_old, kappa-free.  Counts beyond the new and the old line."""
    k_new = sum(1 for m in matched if abs(m["b_new"]) > m["cut_new"])
    k_old = sum(1 for m in matched if abs(m["b_old"]) > m["cut_old"])
    return k_new, k_old


def bootstrap_q1(beyond_new, beyond_old, blocks, B_=B_BOOT, seed=SEED_Q1, nominal=0.05):
    """Family-block percentile bootstrap (amendment 3.3): rate, excess over 5% and paired change (k_old - k_new)/n."""
    names = sorted(set(blocks))
    pos = {f: i for i, f in enumerate(names)}
    F = len(names)
    n_f, k_new, k_old = np.zeros(F), np.zeros(F), np.zeros(F)
    for bn, bo, f in zip(beyond_new, beyond_old, blocks):
        n_f[pos[f]] += 1
        k_new[pos[f]] += int(bn)
        k_old[pos[f]] += int(bo)
    rng = np.random.default_rng(seed)
    c = multiplicity(rng.integers(0, F, size=(B_, F)), F)
    den = c @ n_f
    rate = (c @ k_new) / den
    rate_old = (c @ k_old) / den
    return dict(rate=pct(rate), excess=pct(rate - nominal), rate_old=pct(rate_old), change=pct(rate_old - rate), blocks=F, B=B_, seed=seed)


# ---------------------------------------------------------------------------------------------
# worker plumbing
# ---------------------------------------------------------------------------------------------
def ref_npz_path(root, ref_id, did):
    return Path(root) / ref_id / (did.replace(":", "_") + ".npz")


def design_tasks():
    """The 28 swept designs: [(design_id, folder, result_path, store_path, scale_mode)] and the weights (seconds in SWEEP-V1)."""
    report = B.load_gate_report()
    todo, skipped = B.swept_designs(report)
    run = S.read_json(B.OUT / "SWEEP_RUN.json")
    secs = {d["design_id"]: d["seconds"] for d in run["designs"]}
    return todo, skipped, secs, report


def q1_design_list(report):
    """The 33 designs that pass the reproduction gate: [(design_id, folder)]."""
    rows = {r[0]: r for r in B.roster()}
    return [(d["design_id"], rows[d["design_id"]][1]) for d in report["designs"] if d["status"] == "PASS"]


def ex1_by_id():
    ex1 = S.read_json(EX1_JSON)
    return {r["design_id"]: r for r in ex1["designs"]}


def gate_task(task):
    did, folder, scale_mode = task
    t0 = time.time()
    out = dict(design_id=did, ok=False)
    try:
        full_store = B.open_store()
        store = full_store.view(scale_mode)
        table = S.catalog_series_table()
        series_table = {sid: table[sid] for sid in full_store.sids}
        ctx = S.load_design_context(did, folder)
        units, adoption, ystar = R.design_inputs(ctx)
        ref = R.build_reference("U", did, units, adoption, ystar, {})
        res = R.sweep_design_ref(ctx, store, series_table, ref, check_engine_bundle=True)
        arrays = R.design_arrays_from_records(res)
        old = np.load(B.design_npz_path(did), allow_pickle=False)
        cmp = R.compare_with_sweep(arrays, old, full=True)
        q1 = R.q1_primary(ctx, ref)
        ex = ex1_by_id().get(did)
        q1chk = dict(att_scaled_diff=abs(q1["att"] - ex["b"]) / max(1.0, abs(q1["att"]), abs(ex["b"])),
                     cutoff_scaled_diff=abs(q1["cutoff"] - ex["noise_cutoff"]) / max(1.0, abs(q1["cutoff"]), abs(ex["noise_cutoff"])))
        q1chk["ok"] = bool(q1chk["att_scaled_diff"] <= R.LOOSE and q1chk["cutoff_scaled_diff"] <= R.LOOSE)
        out.update(compare=cmp, aligned_equals_engine=res["aligned_equals_engine"], q1_identity=q1chk, ok=bool(cmp["ok"] and q1chk["ok"] and res["aligned_equals_engine"]),
                   n_patterns=len(res["patterns"]), codes=dict(Counter(str(c) for c in arrays["code"])))
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        out["traceback"] = traceback.format_exc()[-1500:]
    out["l2_problems"] = B.verify_l2(B._WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def stage_gate(args):
    problems, rec = all_record_problems()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    todo, skipped, secs, report = design_tasks()
    tasks = [(t[0], t[1], t[4]) for t in todo if t[0] in GATE_DESIGNS]
    if sorted(t[0] for t in tasks) != sorted(GATE_DESIGNS):
        sys.exit("a gate design is not in the swept roster")
    target = OUT / "GATE_UNIFORM.json"
    if target.exists() and not args.dry_run:
        sys.exit(f"{S.rel(target)} exists: not overwritten")
    t0 = time.time()
    results = sorted(B._run_pool(gate_task, tasks, min(args.workers, MAX_WORKERS), "gate"), key=lambda r: r["design_id"])
    ok = all(r["ok"] and not r["l2_problems"] for r in results)
    payload = dict(rule_id=RULE_ID, question=QUESTION, version=VERSION, stage="gate", run_utc=utc_now(), ok=ok, designs=results, code=code_hashes(),
                   amendment_sha256=S.sha256_file(AMEND), sweep_run_sha256=S.sha256_file(B.OUT / "SWEEP_RUN.json"), python=sys.version.split()[0], numpy=np.__version__,
                   seconds=round(time.time() - t0, 1))
    if not args.dry_run:
        write_once_json(target, payload)
        log(OUT, event="gate", ok=ok, designs=GATE_DESIGNS)
    for r in results:
        print(f"{r['design_id'][:56]:56s} {'OK' if r['ok'] else 'FAIL':4s} {r.get('seconds')}s {r.get('error', '')[:200]} {r.get('compare', {}).get('problems', '')}")
    print(json.dumps({"ok": ok, "n": len(results)}))
    return 0 if ok else 1


def ref_task(task):
    ref_id, did, folder, result_path, scale_mode, out_root = task
    t0 = time.time()
    out = dict(reference_id=ref_id, design_id=did, status="ERROR")
    try:
        path = ref_npz_path(out_root, ref_id, did)
        code_sha = S.sha256_file(HERE / "selref_core.py")
        if path.exists():                 # an interrupted earlier run wrote this file atomically; accept it only for the same reference, design and code
            z = np.load(path, allow_pickle=False)
            meta = json.loads(str(z["meta_json"]))
            if meta.get("reference_id") != ref_id or meta.get("design_id") != did or meta.get("code", {}).get("scripts/l3_meta/selref_core.py") != code_sha:
                raise RuntimeError(f"{S.rel(path)} exists from other code or inputs: not overwritten")
            out.update(status="OK", reused=True, file=S.rel(path), sha256=S.sha256_file(path), codes=dict(Counter(str(c) for c in z["code"])), n_patterns=len(meta["patterns"]))
        else:
            full_store = B.open_store()
            store = full_store.view(scale_mode)
            table = S.catalog_series_table()
            series_table = {sid: table[sid] for sid in full_store.sids}
            ctx = S.load_design_context(did, folder)
            units, adoption, ystar = R.design_inputs(ctx)
            cov = R.load_covariate_table() if ref_id != "U" else {}
            ref = R.build_reference(ref_id, did, units, adoption, ystar, cov)
            res = R.sweep_design_ref(ctx, store, series_table, ref, check_engine_bundle=(ref_id == "U"))
            arrays = R.design_arrays_from_records(res)
            old = np.load(B.design_npz_path(did), allow_pickle=False)
            cmp = R.compare_with_sweep(arrays, old, full=(ref_id == "U"))
            if not cmp["ok"]:
                raise RuntimeError("IDENTITY_FAIL: " + "; ".join(cmp["problems"][:5]))
            perms = R.make_perms(ref["blocks"], len(units), R.N_DRAWS, ref["seed"])
            diag = R.perm_diagnostics(perms, adoption)
            meta = dict(rule_id=RULE_ID, question=QUESTION, version=VERSION, reference_id=ref_id, design_id=did, run_folder=S.rel(folder),
                        result_sha256=S.sha256_file(result_path), freeze_sha256=S.sha256_file(folder / "FREEZE_FINAL.json"), scale_mode=scale_mode,
                        units=res["units"], treated_primary=res["treated_primary"], years=res["years"], aligned_equals_engine=res["aligned_equals_engine"],
                        patterns=res["patterns"], reference=R.reference_summary(ref, units, adoption), permutation_diagnostics=diag,
                        compare_with_sweep_v1=cmp, series_store_sha256=S.sha256_file(B.OUT / B.STORE_NAME),
                        series_store_count_sha256=S.sha256_file(B.OUT / B.COUNT_NAME), covariates_sha256=S.sha256_file(R.COV_PATH),
                        amendment_sha256=S.sha256_file(AMEND), code=code_hashes(), built_utc=utc_now())
            problems = B.verify_l2(B._WORKER.get("rec"))
            if problems:
                raise RuntimeError("L2 module changed during the task, nothing written: " + "; ".join(problems[:3]))
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(path.name + ".part")
            with open(part, "xb") as fh:
                np.savez_compressed(fh, meta_json=np.array(json.dumps(S.clean(meta), sort_keys=True)), **arrays)
            if path.exists():
                raise RuntimeError(f"{S.rel(path)} exists: not overwritten")
            os.rename(part, path)
            out.update(status="OK", reused=False, file=S.rel(path), sha256=S.sha256_file(path), codes=dict(Counter(str(c) for c in arrays["code"])),
                       n_patterns=len(res["patterns"]), compare=cmp, diagnostics=diag)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["l2_problems"] = B.verify_l2(B._WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def stage_refs(args):
    problems, rec = all_record_problems()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    if not args.out_root and (not (OUT / "GATE_UNIFORM.json").exists() or not S.read_json(OUT / "GATE_UNIFORM.json").get("ok")):
        sys.exit("GATE_UNIFORM.json missing or failed: the uniform identity gate comes first (amendment 5.1)")
    variants = list(args.variants or ALL_IDS)
    for v in variants:
        if v not in R.REFERENCES:
            sys.exit(f"unknown reference {v}")
    out_root = Path(args.out_root) if args.out_root else REFS
    todo, skipped, secs, report = design_tasks()
    if args.only:
        todo = [t for t in todo if t[0] in args.only]
    tasks = []
    for v in variants:
        if (out_root / v / "REF_RUN.json").exists():
            print(f"{v}: REF_RUN.json exists, skipped")
            continue
        for did, folder, result_path, store_path, scale_mode in todo:
            tasks.append((v, did, folder, result_path, scale_mode, out_root))
    tasks.sort(key=lambda t: -secs.get(t[1], 0.0))
    t0 = time.time()
    if tasks:
        log(OUT if out_root == REFS else out_root, event="refs_start", variants=variants, tasks=len(tasks), workers=min(args.workers, MAX_WORKERS))
    results = B._run_pool(ref_task, tasks, min(args.workers, MAX_WORKERS), "refs") if tasks else []
    bad = 0
    for v in variants:
        rs = sorted([r for r in results if r["reference_id"] == v], key=lambda r: r["design_id"])
        if not rs:
            continue
        n_ok = sum(1 for r in rs if r["status"] == "OK")
        summary = dict(rule_id=RULE_ID, question=QUESTION, version=VERSION, stage="refs", reference_id=v, run_utc=utc_now(), n_ok=n_ok, n_error=len(rs) - n_ok,
                       skipped=skipped, designs=rs, code=code_hashes(), amendment_sha256=S.sha256_file(AMEND), hash_record_sha256=S.sha256_file(HASH_REC),
                       sweep_run_sha256=S.sha256_file(B.OUT / "SWEEP_RUN.json"), gate_report_sha256=S.sha256_file(B.OUT / "GATE_REPORT.json"),
                       covariates_sha256=S.sha256_file(R.COV_PATH), python=sys.version.split()[0], numpy=np.__version__, seconds=round(time.time() - t0, 1))
        write_once_json(out_root / v / "REF_RUN.json", summary)
        bad += len(rs) - n_ok
        for r in rs:
            print(f"{v:4s} {r['design_id'][:52]:52s} {r['status']:5s} {r.get('seconds')}s {r.get('codes')} {r.get('error', '')[:300]}")
    log(OUT if out_root == REFS else out_root, event="refs_done", errors=bad)
    return 0 if bad == 0 else 1


def q1_task(task):
    ref_id, did, folder = task
    t0 = time.time()
    out = dict(reference_id=ref_id, design_id=did, status="ERROR")
    try:
        ctx = S.load_design_context(did, folder)
        units, adoption, ystar = R.design_inputs(ctx)
        cov = R.load_covariate_table() if ref_id != "U" else {}
        ref = R.build_reference(ref_id, did, units, adoption, ystar, cov)
        q = R.q1_primary(ctx, ref)
        placebo = q.pop("placebo")
        out.update(status="OK", q1=q, placebo_finite=[float(x) for x in placebo[np.isfinite(placebo)]], reference=R.reference_summary(ref, units, adoption),
                   adapter=ctx.adapter, mode=ctx.mode)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        out["traceback"] = traceback.format_exc()[-1500:]
    out["l2_problems"] = B.verify_l2(B._WORKER.get("rec"))
    out["seconds"] = round(time.time() - t0, 1)
    return out


def stage_q1refs(args):
    problems, rec = all_record_problems()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    variants = list(args.variants or ALL_IDS)
    report = B.load_gate_report()
    designs = q1_design_list(report)
    if args.only:
        designs = [d for d in designs if d[0] in args.only]
    out_dir = Path(args.out_root) if args.out_root else Q1DIR
    tasks = [(v, did, folder) for v in variants if not (out_dir / f"Q1_REFS_{v}.json").exists() for did, folder in designs]
    t0 = time.time()
    results = B._run_pool(q1_task, tasks, min(args.workers, MAX_WORKERS), "q1refs") if tasks else []
    bad = 0
    ex = ex1_by_id()
    for v in variants:
        rs = sorted([r for r in results if r["reference_id"] == v], key=lambda r: r["design_id"])
        if not rs:
            continue
        for r in rs:
            if r["status"] == "OK":
                e = ex.get(r["design_id"])
                q = r["q1"]
                r["vs_q1_v3"] = dict(att_scaled_diff=abs(q["att"] - e["b"]) / max(1.0, abs(q["att"]), abs(e["b"])),
                                     cutoff_scaled_diff=abs(q["cutoff"] - e["noise_cutoff"]) / max(1.0, abs(q["cutoff"]), abs(e["noise_cutoff"])))
        n_ok = sum(1 for r in rs if r["status"] == "OK")
        if v == "U":
            fails = [r["design_id"] for r in rs if r["status"] == "OK" and (r["vs_q1_v3"]["att_scaled_diff"] > R.LOOSE or r["vs_q1_v3"]["cutoff_scaled_diff"] > R.LOOSE)]
        else:
            fails = []
        payload = dict(rule_id=RULE_ID, question=QUESTION, version=VERSION, stage="q1refs", reference_id=v, run_utc=utc_now(), n_ok=n_ok, n_error=len(rs) - n_ok,
                       uniform_identity_failures=fails, designs=rs, code=code_hashes(), amendment_sha256=S.sha256_file(AMEND), ex1_sha256=S.sha256_file(EX1_JSON),
                       python=sys.version.split()[0], numpy=np.__version__, seconds=round(time.time() - t0, 1))
        write_once_json(out_dir / f"Q1_REFS_{v}.json", payload)
        bad += len(rs) - n_ok + len(fails)
        print(f"{v}: {n_ok} of {len(rs)} designs, uniform identity failures {fails}")
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------------------------
def d2_inputs():
    """Everything build_d2.main prepares before it looks at a sweep folder."""
    roster, live_meta = D1.load_live()
    inv_rows = S.inventory_rows()
    table = S.catalog_series_table()
    reg_fams = D1.registered_families(inv_rows, table)
    groups = D2.design_groups(inv_rows)
    reg_by_group, reg_by_design = D2.registered_maps(D2.registered_rows(inv_rows, table))
    gate = S.read_json(B.OUT / "GATE_REPORT.json")
    receipt = S.read_json(RC.D2DIR / RC.OUT_RECEIPT.name)
    invalid = frozenset(tuple(x) for x in receipt.get("nc_invalid", []))
    pairs = D2.pair_table(RC.load_csv(RC.OUT_CSV, RC.OUT_HEADER))
    if len(pairs) != RC.N_GROUPS * RC.N_FAMILIES:
        sys.exit("the reconciled table is not the 35 x 16 grid")
    return dict(roster=roster, live_meta=live_meta, reg_fams=reg_fams, groups=groups, reg_by_group=reg_by_group, reg_by_design=reg_by_design,
                gate_by={d["design_id"]: d for d in gate["designs"]}, invalid=invalid, pairs=pairs)


def load_reference_folder(v):
    """(folder, swept) of a reference: U is the SWEEP-V1 folder itself; the others are REF_RUN.json folders (every file hash-checked)."""
    if v == "U":
        folder = B.OUT
        run = S.read_json(folder / "SWEEP_RUN.json")
    else:
        folder = REFS / v
        run = S.read_json(folder / "REF_RUN.json")
        if run["n_error"] != 0:
            sys.exit(f"reference {v} has errors in its build: not analysed")
    swept = {d["design_id"]: d for d in run["designs"] if d["status"] == "OK"}
    for did, d in swept.items():
        if S.sha256_file(Path(folder) / Path(d["file"]).name) != d["sha256"]:
            sys.exit(f"{v}: file of {did} differs from its run record")
    return folder, swept, run


def d1_block(ci, folder, swept):
    """D1 exactly as build_d1.main assembles it, for one design folder."""
    gate_by, reg_fams = ci["gate_by"], ci["reg_fams"]
    designs, eval_rows = [], []
    for d in ci["roster"]:
        did = d["design_id"]
        row = dict(design_id=did, policy_family=d["policy_family"] or did, domain=d["domain"], live_status=d["status"], gate=d["gate"], source_group=d["source_group"])
        if d["status"] != "estimated":
            row.update(code="STOP_PRE_RUN")
        elif d["source_group"] in D1.ARCHIVE_GROUPS:
            row.update(code="FIELD_MISSING")
        elif did not in gate_by:
            row.update(code="OTHER")
        elif gate_by[did]["status"] != "PASS":
            row.update(code="REPRO_FAIL")
        elif gate_by[did].get("adapter") == "annual_full_year_v1":
            row.update(code="NO_SECONDARY_ADAPTER")
        elif did not in swept:
            row.update(code="OTHER")
        else:
            f = D1.load_design_file(Path(folder) / Path(swept[did]["file"]).name)
            st = D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], reg_fams.get(did, set()))
            row.update(st)
            row["series_codes"] = dict(Counter(f["code"]))
            if st["code"] == "EVAL":
                eval_rows.append(row)
        designs.append(row)
    n = len(eval_rows)
    e1 = D1.e1_summary(eval_rows)
    out = OrderedDict(n_designs_eval=n, F=len({r["policy_family"] for r in eval_rows}), coverage=dict(sorted(Counter(r["code"] for r in designs).items())), E1=e1,
                      theta=float(np.mean([r["Z"] for r in eval_rows])) if n else None,
                      pattern_counts=D1.pattern_counts(eval_rows) if n else None,
                      series_calibration=dict(series_beyond_line=int(sum(r["series_beyond_line"] for r in eval_rows)), series_beyond_expected=float(sum(r["series_beyond_expected"] for r in eval_rows)),
                                              series_total=int(sum(r["n_series"] for r in eval_rows)), families_beyond_line=int(sum(r["N"] for r in eval_rows)),
                                              families_beyond_expected=float(sum(r["mu"] for r in eval_rows))) if n else None,
                      designs=[dict(design_id=r["design_id"], policy_family=r["policy_family"], N=r["N"], mu=r["mu"], tau=r["tau"], Z=r["Z"], p=r["p"], n_draws=r["n_draws"],
                                    n_series=r["n_series"], families=r["families"]) for r in eval_rows],
                      families_beyond=[dict(design_id=r["design_id"], family=f, M=s["M"], q=s["q"], top_series=s["top_series"], top_z=s["top_z"]) for r in eval_rows
                                       for f, s in r["family_stats"].items() if s["beyond"]])
    return out, eval_rows


def d2_block(ci, folder, swept):
    """D2 for one design folder: cells, NC estimate, E1, null width, series level, strict set, other cells; also returns the arrays for the spread block."""
    designs, arrays = D2.design_table(ci["roster"], ci["gate_by"], swept, ci["reg_fams"], folder)
    cells = D2.build_cells(designs, ci["groups"], ci["pairs"], ci["reg_by_group"], ci["reg_by_design"], ci["invalid"])
    nc = [c for c in cells if c["code"] == "NC"]
    est, e1 = D2.estimate(nc), D2.e1_decision(nc)
    strict = [c for c in cells if c["flags"]["strict"]]
    other = [c for c in cells if c["code"] == "D2_PAIR_NOT_NC"]
    nc_set = {(c["design_id"], c["family"]) for c in nc}
    strict_set = {(c["design_id"], c["family"]) for c in strict}
    blocks, blocks_strict = D2.nc_blocks(arrays, nc_set), D2.nc_blocks(arrays, strict_set)
    pf = {d["design_id"]: d["policy_family"] for d in designs}
    crossing_all = [c for c in cells if c["B"] == 1]
    out = OrderedDict(
        estimate=est, E1=e1, label=D2.reading_label(e1), strict_estimate=D2.estimate(strict), strict_E1=D2.e1_decision(strict),
        other_cells=D2.estimate(other), all_cells=D2.estimate(cells), nc_share_of_crossings=(len([c for c in crossing_all if c["code"] == "NC"]) / len(crossing_all)) if crossing_all else None,
        cell_codes={k: sum(1 for c in cells if c["code"] == k) for k in D2.CELL_CODES}, series_level_nc=D2.series_level(blocks), series_level_strict=D2.series_level(blocks_strict),
        rates_by_policy_group=D2.tabulate(nc, "policy_group"), rates_by_outcome_family=D2.tabulate(nc, "family"), eval_designs=sum(1 for d in designs if d["code"] == "EVAL"),
        designs_with_nc_cell=len({c["design_id"] for c in nc}),
        cells=[dict(design_id=c["design_id"], policy_family=c["policy_family"], policy_group=c["policy_group"], family=c["family"], code=c["code"], strict=bool(c["flags"]["strict"]),
                    M=c["M"], q=c["q"], beyond=c["B"], r=c["r"], n_series=c["n_series"], n_draws=c["n_draws"], top_series=c["top_series"], top_z=c["top_z"]) for c in cells])
    return out, cells, nc, blocks, blocks_strict, pf, designs


def spread_for(blocks, blocks_strict, pf):
    return dict(primary_set=spread_block(blocks, pf), strict_set=spread_block(blocks_strict, pf))


def paired_change(nc_u, nc_v):
    """Amendment 3.5: Delta = mean (y_U - y_v) on the NC cells common to U and v, E1 over policy families with the D2 floor."""
    iu = {(c["design_id"], c["family"]): c for c in nc_u}
    common = [(iu[(c["design_id"], c["family"])], c) for c in nc_v if (c["design_id"], c["family"]) in iu]
    m = len(common)
    fams = [cu["policy_family"] for cu, _ in common]
    F = len(set(fams))
    base = dict(m_common=m, F=F, m_u=len(nc_u), m_v=len(nc_v), share_of_u_nc_cells=len(common) / len(nc_u) if nc_u else None, seed=SEED_PAIR, B=B_BOOT)
    if m < D2.FLOOR_M or F < D2.FLOOR_F:
        return dict(base, run=False, reason=f"E1 floor not met (m = {m}, F = {F})")
    d = [(cu["B"] - cu["r"]) - (cv["B"] - cv["r"]) for cu, cv in common]
    ids, F_ = N1.family_ids(fams)
    res = N1.e1_core(d, ids, F_, B_BOOT, np.random.default_rng(SEED_PAIR))
    return dict(base, run=True, result=res, delta_u_common=float(np.mean([cu["B"] - cu["r"] for cu, _ in common])), delta_v_common=float(np.mean([cv["B"] - cv["r"] for _, cv in common])),
                beyond_u_common=int(sum(cu["B"] for cu, _ in common)), beyond_v_common=int(sum(cv["B"] for _, cv in common)))


def q1_block(q1_roster_rows, q1_refs):
    """Q1 under a reference on the matched designs (amendment 3.3); q1_refs: the Q1_REFS_<v>.json payload."""
    by = {r["design_id"]: r for r in q1_refs["designs"] if r["status"] == "OK"}
    matched = []
    for r in q1_roster_rows:
        x = by.get(r["design_id"])
        if x is None:
            continue
        matched.append(dict(design_id=r["design_id"], policy_family=r["policy_family"], group3=r["group3"], b_old=r["b"], cut_old=r["cut"], b_new=x["q1"]["att"], cut_new=x["q1"]["cutoff"],
                            support_new=bool(x["q1"]["support_pass"]), n_feasible=x["q1"]["n_feasible"]))
    kept = [m for m in matched if m["support_new"]]
    n = len(kept)
    k_new, k_old = q1_counts(kept)
    bn = [abs(m["b_new"]) > m["cut_new"] for m in kept]
    bo = [abs(m["b_old"]) > m["cut_old"] for m in kept]
    out = OrderedDict(n_recomputable=len(matched), n_kept=n, dropped_for_support=[m["design_id"] for m in matched if not m["support_new"]], F=len({m["policy_family"] for m in kept}),
                      k_new=k_new, k_old_same_designs=k_old, expected_nominal=0.05 * n, rate_new=k_new / n if n else None, rate_old=k_old / n if n else None,
                      excess_new=(k_new / n - 0.05) if n else None, designs_beyond_new=[m["design_id"] for m, b in zip(kept, bn) if b],
                      designs_beyond_old=[m["design_id"] for m, b in zip(kept, bo) if b])
    if n:
        out["bootstrap"] = bootstrap_q1(bn, bo, [m["policy_family"] for m in kept])
        lo, hi = out["bootstrap"]["excess"]
        out["label"] = "ABOVE THE NEW LINE" if lo > 0 else "BELOW THE NEW LINE" if hi < 0 else "AT THE NEW LINE"
        out["rho_new"] = {m["design_id"]: abs(m["b_new"]) / m["cut_new"] for m in kept}
        out["att_max_scaled_diff_vs_q1_v3"] = max(abs(m["b_new"] - m["b_old"]) / max(1.0, abs(m["b_new"]), abs(m["b_old"])) for m in kept)
    return out, kept


def residual_widening(kappa, kept, eval_d1, cells):
    """Amendment 3.7 (point values): Q1 count and D1 T_D, theta with every line of v widened by kappa."""
    k = sum(1 for m in kept if abs(m["b_new"]) > kappa * m["cut_new"])
    by_design = Counter()
    for c in cells:
        if c["M"] > kappa * c["q"]:
            by_design[c["design_id"]] += 1
    Zs = []
    for r in eval_d1:
        Zs.append((by_design.get(r["design_id"], 0) - r["mu"]) / r["tau"])
    return dict(kappa=kappa, q1_k=k, q1_n=len(kept), d1_T_D=int(sum(by_design.get(r["design_id"], 0) for r in eval_d1)), d1_sum_mu=float(sum(r["mu"] for r in eval_d1)),
                d1_theta=float(np.mean(Zs)) if Zs else None)


def line_ratios(folder_v, swept_v, folder_u, swept_u):
    """Per supported series common to U and v: the ratio of the new to the old absolute line and SD (descriptive)."""
    rl, rs, shift = [], [], []
    for did, d in swept_v.items():
        zu = np.load(Path(folder_u) / Path(swept_u[did]["file"]).name, allow_pickle=False)
        zv = np.load(Path(folder_v) / Path(d["file"]).name, allow_pickle=False)
        ok = (np.array([str(c) for c in zu["code"]]) == "SUPPORTED") & (np.array([str(c) for c in zv["code"]]) == "SUPPORTED")
        with np.errstate(divide="ignore", invalid="ignore"):
            rl += list((zv["cutoff"][ok] / zu["cutoff"][ok]))
            rs += list((zv["sd"][ok] / zu["sd"][ok]))
            pm = np.nanmean(zv["placebo"][ok], axis=1) / zv["sd"][ok]
            shift += list(pm)
    def q(a):
        a = np.asarray(a, float)
        a = a[np.isfinite(a)]
        return dict(n=int(a.size), median=float(np.median(a)), q25=float(np.percentile(a, 25)), q75=float(np.percentile(a, 75)), min=float(a.min()), max=float(a.max())) if a.size else None
    return dict(line_ratio_new_over_old=q(rl), sd_ratio_new_over_old=q(rs), centre_shift_mean_placebo_over_sd=q(shift),
                centre_shift_sd=float(np.std(np.asarray(shift)[np.isfinite(shift)], ddof=1)) if len(shift) > 1 else None)


def ref_diagnostics(v, run):
    """Per-design permutation diagnostics and block structure from the REF_RUN records (descriptive)."""
    rows = []
    for d in run["designs"]:
        if d["status"] != "OK":
            continue
        z = np.load(ROOT / d["file"], allow_pickle=False) if v != "U" else None
        meta = json.loads(str(z["meta_json"])) if z is not None else None
        if meta is None:
            continue
        ref = meta["reference"]
        rows.append(dict(design_id=d["design_id"], n=ref["n"], n_free=ref["n_free"], K_eff=ref["K_eff"], ystar=ref["ystar"], fixed_units=ref["fixed_units"],
                         blocks=[(len(b["units"]), b["treated"]) for b in ref["blocks"]], **meta["permutation_diagnostics"], codes=d["codes"]))
    return rows


def build_identities(u, ex1, d1j, d2j, q1_ids33):
    """Amendment 5.6: the analysis under U equals D2-V1, D1-V1 and Q1-V3.  Returns an ordered dict name -> (got, want)."""
    d2q = d2j["question"]["D2"]
    d1q = d1j["question"]["D1"]
    ident = OrderedDict()
    ident["d2_nc_cells"] = (u["D2"]["estimate"]["m"], d2q["estimate_nc_cells"]["m"])
    ident["d2_nc_beyond"] = (u["D2"]["estimate"]["n_beyond"], d2q["estimate_nc_cells"]["n_beyond"])
    ident["d2_delta"] = (u["D2"]["estimate"]["delta_D2"], d2q["estimate_nc_cells"]["delta_D2"])
    ident["d2_e1_interval_lo"] = (u["D2"]["E1"]["result"]["interval95"][0], d2q["E1"]["result"]["interval95"][0])
    ident["d2_kappa"] = (u["spread"]["primary_set"]["kappa_v"], d2q["empirical_null_width_nc_series"]["ratio_observed_to_reference_mean"])
    ident["d2_series_beyond"] = (u["D2"]["series_level_nc"]["series_beyond_own_line"], d2q["series_level_nc"]["series_beyond_own_line"])
    ident["d2_series"] = (u["D2"]["series_level_nc"]["series"], d2q["series_level_nc"]["series"])
    ident["d2_strict_cells"] = (u["D2"]["strict_estimate"]["m"], d2q["strict_set_estimate"]["m"])
    ident["d1_T_D"] = (u["D1"]["series_calibration"]["families_beyond_line"], d1q["series_calibration"]["families_beyond_line"])
    ident["d1_theta"] = (u["D1"]["theta"], d1q["theta_D1_plain_mean_Z"])
    ident["d1_n_designs"] = (u["D1"]["n_designs_eval"], d1q["n_designs_eval"])
    ident["d1_e1_lo"] = (u["D1"]["E1"]["result"]["interval95"][0], d1q["E1"]["result"]["interval95"][0])
    d1_rows = {r["design_id"]: r for r in d1j["designs"] if r.get("code") == "EVAL"}
    for r in u["D1"]["designs"]:
        o = d1_rows[r["design_id"]]
        for k in ("N", "mu", "tau", "Z", "p"):
            ident[f"d1_{k}_{r['design_id']}"] = (r[k], o[k])
    nine = set(ex1["questions"]["Q1"]["exceeding_designs"])
    if u["Q1"] is not None:
        ident["q1_exceeders_old_line_in_the_33"] = (sorted(u["Q1"]["designs_beyond_old"]), sorted(nine & set(q1_ids33)))
        ident["q1_exceeders_new_line_equals_old_under_U"] = (sorted(u["Q1"]["designs_beyond_new"]), sorted(nine & set(q1_ids33)))
        ident["q1_n_under_U"] = (u["Q1"]["n_kept"], len(q1_ids33))
    return ident


def check_identities(ident, extra_failures=()):
    bad = list(extra_failures)
    for k, (got, want) in ident.items():
        if isinstance(got, (int, float)) and not isinstance(got, bool) and isinstance(want, (int, float)):
            if abs(got - want) > 1e-9 * max(1.0, abs(got), abs(want)):
                bad.append(f"{k}: {got!r} vs {want!r}")
        elif got != want:
            bad.append(f"{k}: {got!r} vs {want!r}")
    return bad


def stage_analyze(args):
    problems, rec = all_record_problems()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    live_sha = S.sha256_file(S.LIVE)
    if rec["listed"].get(S.rel(S.LIVE)) != live_sha:
        sys.exit("live set differs from the SELREF hash record: not run")
    variants = list(args.variants or ALL_IDS)
    if variants[0] != "U":
        variants = ["U"] + [v for v in variants if v != "U"]
    out_dir = Path(args.out_dir) if args.out_dir else OUT
    ci = d2_inputs()
    ex1, d1j, d2j = S.read_json(EX1_JSON), S.read_json(D1_JSON), S.read_json(D2_JSON)
    q1_rows_all = q1_roster(ex1)
    q1_ids33 = {d[0] for d in q1_design_list(B.load_gate_report())}
    q1_rows33 = [r for r in q1_rows_all if r["design_id"] in q1_ids33]

    results = OrderedDict()
    cache = {}
    for v in variants:
        folder, swept, run = load_reference_folder(v)
        d1b, d1_eval = d1_block(ci, folder, swept)
        d2b, cells, nc, blocks, blocks_strict, pf, designs = d2_block(ci, folder, swept)
        sp = spread_for(blocks, blocks_strict, pf)
        q1_path = Q1DIR / f"Q1_REFS_{v}.json"
        q1_payload = S.read_json(q1_path) if q1_path.exists() else None
        if q1_payload is not None and q1_payload["n_error"] != 0:
            sys.exit(f"Q1 reference {v} has errors in its build: not analysed")
        q1b, kept = q1_block(q1_rows33, q1_payload) if q1_payload is not None else (None, [])
        res = OrderedDict(reference_id=v, D2=d2b, spread=sp, D1=d1b, Q1=q1b)
        if v != "U":
            res["diagnostics"] = ref_diagnostics(v, run)
            res["line_ratios"] = line_ratios(folder, swept, B.OUT, cache["U"]["swept"])
        cache[v] = dict(cells=cells, nc=nc, swept=swept, folder=folder, d1_eval=d1_eval, kept=kept)
        results[v] = res
        print(f"[{v}] D2 {d2b['estimate']['n_beyond']}/{d2b['estimate']['m']} cells, delta {d2b['estimate']['delta_D2']:+.4f}, kappa {sp['primary_set']['kappa_v']:.4f}; "
              f"D1 T_D {d1b['series_calibration']['families_beyond_line']}; Q1 {None if q1b is None else (q1b['k_new'], q1b['n_kept'])}", file=sys.stderr, flush=True)

    # identities (amendment 5.6)
    u = results["U"]
    q1u = S.read_json(Q1DIR / "Q1_REFS_U.json") if (Q1DIR / "Q1_REFS_U.json").exists() else None
    ident = build_identities(u, ex1, d1j, d2j, sorted(q1_ids33))
    bad = check_identities(ident, extra_failures=(["Q1 uniform identity: " + ", ".join(q1u["uniform_identity_failures"])] if q1u and q1u["uniform_identity_failures"] else []))
    if u["Q1"] is not None and u["Q1"]["k_new"] != u["Q1"]["k_old_same_designs"]:
        bad.append("Q1 under U: new and old counts differ")
    if bad:
        print("IDENTITY FAILURES: " + "; ".join(bad[:20]), file=sys.stderr)
        if not args.dry_run:
            sys.exit("identity under U failed: not written")

    # paired changes, labels, residual widening, verdict
    low_support = {}
    for v in variants:
        r = results[v]
        e1 = r["D2"]["E1"]
        level = level_label(e1["result"]["interval95"]) if e1.get("run") and e1["result"].get("status") == "OK" else None
        sp_int = r["spread"]["primary_set"]["interval95"] if r["spread"]["primary_set"] else None
        lab = OrderedDict(LEVEL=level, SPREAD=spread_label(sp_int) if sp_int else None)
        if v != "U":
            pc = paired_change(cache["U"]["nc"], cache[v]["nc"])
            r["paired_change_vs_U"] = pc
            lab["CHANGE"] = change_label(pc["result"]["interval95"]) if pc.get("run") and pc["result"].get("status") == "OK" else None
            share_v = len(cache[v]["nc"]) / len(cache["U"]["nc"])
            lab["SUPPORT"] = "LOW" if (share_v < LOW_SUPPORT) else "OK"
            low_support[v] = share_v < LOW_SUPPORT
        r["labels"] = lab
        if v != "U" and r["spread"]["primary_set"] and r["spread"]["primary_set"]["interval95"][0] > 1 and r["Q1"] is not None:
            r["residual_widening"] = residual_widening(r["spread"]["primary_set"]["kappa_v"], cache[v]["kept"], cache[v]["d1_eval"], cache[v]["cells"])
        # power sanity (descriptive)
        r["power"] = dict(nc_cells=dict(beyond=r["D2"]["estimate"]["n_beyond"], expected=r["D2"]["estimate"]["expected_beyond"], m=r["D2"]["estimate"]["m"]),
                          other_cells=dict(beyond=r["D2"]["other_cells"]["n_beyond"], expected=r["D2"]["other_cells"]["expected_beyond"], m=r["D2"]["other_cells"]["m"]),
                          all_cells=dict(beyond=r["D2"]["all_cells"]["n_beyond"], expected=r["D2"]["all_cells"]["expected_beyond"], m=r["D2"]["all_cells"]["m"]),
                          nc_share_of_crossings=r["D2"]["nc_share_of_crossings"])
    primary = OrderedDict(id=R.PRIMARY)
    if R.PRIMARY in results:
        lab = results[R.PRIMARY]["labels"]
        primary.update(labels=lab, verdict=verdict_text(lab.get("LEVEL"), lab.get("CHANGE"), low_support=low_support.get(R.PRIMARY, False)),
                       leakage_warning=("N5" in results and results["N5"]["labels"].get("CHANGE") in ("REDUCED", "INCREASED")))
    ladder = [OrderedDict(reference_id=v, delta=results[v]["D2"]["estimate"]["delta_D2"], pi_NC=results[v]["D2"]["estimate"]["pi_NC"], beyond=results[v]["D2"]["estimate"]["n_beyond"],
                          m=results[v]["D2"]["estimate"]["m"], kappa=results[v]["spread"]["primary_set"]["kappa_v"], labels=results[v]["labels"]) for v in variants]

    me = Path(__file__).resolve()
    inputs_paths = [S.LIVE, AMEND, HASH_REC, R.COV_PATH, EX1_JSON, D1_JSON, D2_JSON, B.OUT / "SWEEP_RUN.json", B.OUT / "GATE_REPORT.json", B.OUT / "SWEEP_MANIFEST.json"]
    inputs_paths += [REFS / v / "REF_RUN.json" for v in variants if v != "U"] + [Q1DIR / f"Q1_REFS_{v}.json" for v in variants if (Q1DIR / f"Q1_REFS_{v}.json").exists()]
    stamp = OrderedDict(rule_id=RULE_ID, question=QUESTION, version=VERSION, cutoff_utc=ci["live_meta"]["as_of"], run_utc=utc_now(), live_set_sha256=live_sha,
                        covariates_sha256=S.sha256_file(R.COV_PATH), sweep_run_sha256=S.sha256_file(B.OUT / "SWEEP_RUN.json"), code=code_hashes(),
                        amendment_sha256=S.sha256_file(AMEND), hash_record_sha256=S.sha256_file(HASH_REC), D=len(ci["roster"]), references=variants,
                        n_d2_designs=results["U"]["D2"]["eval_designs"], m_u=results["U"]["D2"]["estimate"]["m"], python=sys.version.split()[0], numpy=np.__version__,
                        seeds=OrderedDict(spread=SEED_SPREAD, q1=SEED_Q1, paired=SEED_PAIR, d2_e1=D2.SEED_E1, d2_null=D2.SEED_NULL, d1_e1=D1.SEED_E1), B=B_BOOT, no_significance_label=True)
    payload = S.clean(OrderedDict([
        ("schema_version", "l3-meta-selref-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {S.rel(p): {"sha256": S.sha256_file(p), "bytes": Path(p).stat().st_size} for p in inputs_paths}),
        ("identities_under_U", OrderedDict([(k, dict(got=g, want=w)) for k, (g, w) in ident.items()])), ("identity_failures", bad),
        ("question", OrderedDict([("SELREF", OrderedDict([("primary", primary), ("ladder", ladder), ("references", results)]))])),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        write_once_text(target, text)
        shutil.copy2(S.LIVE, out_dir / "l2_set_live_used.json")
        assert S.sha256_file(out_dir / "l2_set_live_used.json") == live_sha
        log(out_dir, event="analyze", command="build_selref.py " + " ".join(sys.argv[1:]), code_sha256=stamp["code"]["scripts/l3_meta/build_selref.py"],
            results_file=S.rel(target), results_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), exit=0)
        print(f"wrote {S.rel(target)}", file=sys.stderr)
    brief = dict(primary=payload["question"]["SELREF"]["primary"], ladder=payload["question"]["SELREF"]["ladder"], identity_failures=bad)
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", required=True, choices=["gate", "refs", "q1refs", "analyze"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--variants", nargs="*", default=None, help="reference ids (default: all seven)")
    ap.add_argument("--only", nargs="*", default=None, help="restrict gate / refs / q1refs to these design ids (tests)")
    ap.add_argument("--out-root", default=None, help="refs / q1refs: write here instead of the real folder (scratch runs outside the repository)")
    ap.add_argument("--out-dir", default=None, help="analyze: output folder")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing (gate and analyze)")
    args = ap.parse_args(argv)
    if args.out_root and str(Path(args.out_root).resolve()).startswith(str(ROOT.resolve())):
        sys.exit("--out-root inside the repository is only for the real folder; use a scratch folder outside it")
    return dict(gate=stage_gate, refs=stage_refs, q1refs=stage_q1refs, analyze=stage_analyze)[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
