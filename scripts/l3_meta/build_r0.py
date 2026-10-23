#!/usr/bin/env python3
"""R0 (rule id EX-1, exploratory): selection-aware reference P5 for the 55 SWEEP-EXT series; D4, D5, D6 rerun under P5.  PREREG_R0_V1.md.

Stages:  pin | gate | refs | analyze      Run (after the W3 pin check):
  .venv_did/bin/python -I -B scripts/l3_meta/build_r0.py gate --workers 5 [--refresh DIR]
  .venv_did/bin/python -I -B scripts/l3_meta/build_r0.py refs --workers 5
  .venv_did/bin/python -I -B scripts/l3_meta/build_r0.py analyze
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import traceback
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import coding_tools as CT  # noqa: E402
import m1_core as M  # noqa: E402
import build_m1 as B1  # noqa: E402

D = ROOT / "data/l3_meta/r0_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_R0_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_R0_V1_HASH.txt"
SELREF = ROOT / "data/l3_meta/selref_v1/refs"
GRID = ROOT / "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv"
REFS_NEW = ("P5", "P3", "P8", "N5")
GATE_POSITIONS = (0, 5, 10, 15, 20, 25)
TOL = 1e-9
SEEDS = dict(d4=20261040, d5=20261041, boot=20261042)
CODE = ["scripts/l3_meta/build_r0.py", "scripts/l3_meta/test_r0.py", "scripts/l3_meta/coding_tools.py", "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py"]
STATE = {"refresh": M.REFRESH}


def sha(p):
    return CT.sha256_file(p)


def verify(stage):
    problems, n = B1.verify_records_for(REC)
    if n < 2:
        problems.append("the pinning supplement S1 is missing")
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    return n


def stage_pin():
    t = CT.write_supplement(REC, 1, "analysis code pinned before the first real stage (section 6 of the pre-registration)", "docs/plans/l3-meta/PREREG_R0_V1.md", PREREG, CODE,
                            "code tested on synthetic data and on a random-valued copy of the extension store (scratch) only; no extension series had been swept under any reference", ROOT)
    print("wrote", t.name)


def init_worker():
    import l3_pins
    import build_sweep as Bsw
    l3_pins.install(Bsw)
    sys.dont_write_bytecode = True
    Bsw.S.load_l2()
    Bsw._WORKER["rec"] = Bsw.parse_hash_record()
    problems = Bsw.verify_l2(Bsw._WORKER["rec"])
    if problems:
        raise RuntimeError("; ".join(problems[:3]))


def rel_diff(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.shape != b.shape:
        return float("inf")
    both = np.isfinite(a) & np.isfinite(b)
    if (np.isfinite(a) != np.isfinite(b)).any():
        return float("inf")
    return float(np.max(np.abs(a[both] - b[both]) / (1e-12 + np.abs(b[both])))) if both.any() else 0.0


def compare_arrays(new, old):
    out = OrderedDict()
    for k in ("att", "sd", "placebo"):
        out[k] = rel_diff(new[k], old[k])
    out["code_equal"] = bool(list(map(str, new["code"])) == list(map(str, old["code"])))
    out["ok"] = bool(all(out[k] <= TOL for k in ("att", "sd", "placebo")) and out["code_equal"])
    return out


def ref_task(task):
    kind, ref_id, did, folder, scale_mode, out_root, refresh = task
    import sweep_core as S
    import selref_core as R
    import build_sweep_ext as X
    t0 = time.time()
    out = dict(kind=kind, reference_id=ref_id, design_id=did, status="ERROR")
    try:
        if kind in ("refs_ext", "gate_u_ext"):
            store = S.SeriesStore(X.EXT_STORE).view("rate")
            table = X.ext_series_table()
        else:                                                       # gate_p5_orig: the 89 original series
            full = S.SeriesStore(Path(refresh) / "sweep/series_store_v1.npz", Path(refresh) / "sweep/series_store_v1_count.npz")
            store = full.view(scale_mode)
            cat = S.catalog_series_table()
            table = {sid: cat[sid] for sid in full.sids}
        ctx = S.load_design_context(did, folder)
        units, adoption, ystar = R.design_inputs(ctx)
        cov = R.load_covariate_table() if ref_id != "U" else {}
        ref = R.build_reference(ref_id, did, units, adoption, ystar, cov)
        res = R.sweep_design_ref(ctx, store, table, ref, check_engine_bundle=(ref_id == "U"))
        arrays = R.design_arrays_from_records(res)
        if kind == "refs_ext":
            meta = dict(rule_id="EX-1", question="R0", version="R0-V1", reference_id=ref_id, design_id=did, scale_mode="rate", units=res["units"], treated_primary=res["treated_primary"],
                        years=res["years"], patterns=res["patterns"], reference=R.reference_summary(ref, units, adoption), ext_store_sha256=S.sha256_file(X.EXT_STORE),
                        selref_core_sha256=S.sha256_file(HERE / "selref_core.py"), built_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            path = Path(out_root) / ref_id / f"{did}.npz"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise RuntimeError(f"{path} exists")
            import build_sweep as Bsw
            pr = Bsw.verify_l2(Bsw._WORKER.get("rec"))
            if pr:
                raise RuntimeError("L2 changed: " + "; ".join(pr[:2]))
            with open(path, "xb") as fh:
                np.savez_compressed(fh, meta_json=np.array(json.dumps(S.clean(meta), sort_keys=True)), **arrays)
            out.update(status="OK", codes=dict(Counter(str(c) for c in arrays["code"])), file=(str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path)), sha256=sha(path))
        else:
            if kind == "gate_p5_orig":
                old = np.load(SELREF / "P5" / f"{did}.npz", allow_pickle=False)
            else:
                old = np.load(Path(refresh) / "sweep_ext" / f"{did}.npz", allow_pickle=False)
            cmp = compare_arrays(arrays, old)
            out.update(status="OK" if cmp["ok"] else "GATE_FAIL", compare=cmp)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        out["traceback"] = traceback.format_exc()[-1500:]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def design_list(refresh):
    gate = json.loads((Path(refresh) / "sweep/GATE_REPORT.json").read_text())
    return gate


def prepare_pool(refresh):
    import l3_pins
    import build_sweep as Bsw
    import sweep_core as S
    l3_pins.install(Bsw)
    S.LIVE = Path(refresh) / "live/l2_set_live.json"
    S.load_l2()
    pr = Bsw.verify_l2()
    if pr:
        sys.exit("L2 modules differ from the sweep hash record (run repin_l2.py --group all, then refresh_all.py --force first): " + "; ".join(pr[:3]))
    Bsw._init_worker = init_worker
    return Bsw, S


def tasks_for(Bsw, refresh, kind, refs, positions=None):
    rows = {r[0]: r for r in Bsw.roster()}
    gate = design_list(refresh)
    todo, _ = Bsw.swept_designs(gate)
    todo = sorted(todo, key=lambda t: t[0])
    if positions is not None:
        todo = [todo[i] for i in positions if i < len(todo)]
    return [(kind, ref, did, folder, mode, D / "refs", str(refresh)) for ref in refs for did, folder, rp, _s, mode in todo]


def stage_gate(workers, refresh):
    n = verify("gate")
    out = D / "GATE_R0_V1.json"
    if out.exists():
        sys.exit("gate report exists")
    # 2.1 file gate
    file_gate = OrderedDict()
    for ref in REFS_NEW:
        rr = json.loads((SELREF / ref / "REF_RUN.json").read_text())
        bad = []
        for d in rr["designs"]:
            f = ROOT / d["file"]
            if sha(f) != d["sha256"]:
                bad.append(d["design_id"])
        file_gate[ref] = dict(n=len(rr["designs"]), mismatched=bad)
    Bsw, S = prepare_pool(refresh)
    tasks = tasks_for(Bsw, refresh, "gate_p5_orig", ["P5"], GATE_POSITIONS) + tasks_for(Bsw, refresh, "gate_u_ext", ["U"], GATE_POSITIONS)
    res = sorted(Bsw._run_pool(ref_task, tasks, workers, "r0gate"), key=lambda r: (r["kind"], r["design_id"]))
    ok = all(not v["mismatched"] for v in file_gate.values()) and all(r["status"] == "OK" for r in res)
    D.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(ok=ok, file_gate=file_gate, rebuild_gate=res, refresh=str(refresh), prereg_sha256=sha(PREREG), run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), indent=1, default=str))
    print("GATE", "PASS" if ok else "FAIL", Counter(r["status"] for r in res))
    return 0 if ok else 1


def stage_refs(workers, refresh):
    verify("refs")
    g = D / "GATE_R0_V1.json"
    if not g.exists() or not json.loads(g.read_text())["ok"]:
        sys.exit("GATE_R0_V1.json missing or failed: the gates come first")
    Bsw, S = prepare_pool(refresh)
    tasks = []
    for ref in REFS_NEW:
        if (D / "refs" / ref / "REF_RUN.json").exists():
            print(ref, "REF_RUN.json exists, skipped")
            continue
        tasks += tasks_for(Bsw, refresh, "refs_ext", [ref])
    t0 = time.time()
    res = sorted(Bsw._run_pool(ref_task, tasks, workers, "r0refs"), key=lambda r: (r["reference_id"], r["design_id"]))
    for ref in REFS_NEW:
        rs = [r for r in res if r["reference_id"] == ref]
        if rs:
            (D / "refs" / ref / "REF_RUN.json").write_text(json.dumps(dict(reference_id=ref, n_ok=sum(r["status"] == "OK" for r in rs), n_error=sum(r["status"] != "OK" for r in rs), designs=rs,
                                                                           seconds=round(time.time() - t0, 1)), indent=1, default=str))
    print(Counter((r["reference_id"], r["status"]) for r in res))
    return 0 if all(r["status"] == "OK" for r in res) else 1


# ---------------------------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------------------------
KEYS = ("series_ids", "family", "code", "att", "sd", "cutoff", "placebo")


def load_union(ref, refresh, union=True):
    out = OrderedDict()
    for did in M.design_ids(refresh):
        if ref == "U":
            a = np.load(Path(refresh) / "sweep" / f"{did}.npz", allow_pickle=False)
            b = np.load(Path(refresh) / "sweep_ext" / f"{did}.npz", allow_pickle=False) if union else None
        else:
            a = np.load(SELREF / ref / f"{did}.npz", allow_pickle=False)
            b = np.load(D / "refs" / ref / f"{did}.npz", allow_pickle=False) if union else None
        arr = {k: (np.concatenate([a[k], b[k]]) if b is not None and k != "placebo" else (np.vstack([a[k], b[k]]) if b is not None else a[k])) for k in KEYS}
        meta = json.loads(str(a["meta_json"]))
        out[did] = dict(arr=arr, meta=meta)
    return out


def build_designs(U, live_family, D1M, D4M):
    designs, skipped = OrderedDict(), {}
    for did, u in U.items():
        a = u["arr"]
        st = D1M.design_statistics([str(s) for s in a["series_ids"]], [str(f) for f in a["family"]], [str(c) for c in a["code"]], a["att"], a["sd"], a["placebo"], ())
        if st["code"] != "EVAL":
            skipped[did] = st.get("reason", st["code"])
            continue
        m = u["meta"]
        designs[did] = D4M.design_from_arrays(did, live_family[did], m.get("scale_mode", "rate"), m["years"], m["units"], [str(f) for f in a["family"]], [str(s) for s in a["series_ids"]],
                                              [str(c) for c in a["code"]], a["att"], a["placebo"], a["sd"], a["cutoff"], st["series_used"])
    return designs, skipped


def nc_cell_exceedance(designs, nc):
    rows = []
    for did, d in designs.items():
        fams = sorted(set(d.fam))
        for f in fams:
            if not nc.get((did, f)):
                continue
            ii = [j for j, x in enumerate(d.fam) if x == f]
            M_obs = float(np.max(np.abs(d.z[ii])))
            Ms = np.max(d.Zabs[ii], axis=0)
            rank = math.ceil(0.95 * d.n) - 1
            q = float(np.sort(Ms)[rank])
            rows.append((did, f, M_obs, q, bool(M_obs > q)))
    n = len(rows)
    k = sum(r[4] for r in rows)
    return dict(n_cells=n, n_beyond=k, share=(k / n if n else None), cp95=cp_interval(k, n) if n else None)


def cp_interval(k, n, alpha=0.05):
    from scipy.stats import beta
    lo = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return [lo, hi]


def run_combo(tag, designs, kappas, D4M, nc, registered, count_series, stores, series_family, do_d56=True, loo=False):
    ids, idx4 = D4M.replicate_index(designs, SEEDS["d4"])
    out = OrderedDict(tag=tag, n_designs=len(designs), n_policy_families=len({d.policy_family for d in designs.values()}))
    cv_s = D4M.build_cellvec(designs, "series")
    out["d4_series"] = D4M.d4_run(designs, cv_s, ids, idx4, kappas, loo=loo)
    cv_f = D4M.build_cellvec(designs, "family")
    out["d4_family"] = D4M.d4_run(designs, cv_f, ids, idx4, kappas, loo=False)
    if do_d56:
        ids5, idx5 = D4M.replicate_index(designs, SEEDS["d5"])
        out["d5"] = D4M.d5_run(designs, ids5, idx5, kappas, count_series, registered, nc, B_=D4M.B_BOOT, seed_boot=SEEDS["boot"])
        swept = sorted({s for d in designs.values() for s in d.all_sids})
        out["d6"] = D4M.d6_run(designs, stores, count_series, kappas, swept, series_family)
    out["nc_cell_exceedance"] = nc_cell_exceedance(designs, nc)
    return out


def stage_analyze(refresh):
    n_rec = verify("analyze")
    out_path = D / "RESULTS_R0_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    for ref in REFS_NEW:
        if not (D / "refs" / ref / "REF_RUN.json").exists():
            sys.exit(f"extension references for {ref} are not built")
    t0 = time.time()
    import build_d1 as D1M
    import build_d4_d10 as D4M
    import sweep_core as S
    live = {x["design_id"]: x["policy_family"] for x in json.loads((Path(refresh) / "live/l2_set_live.json").read_text())["designs"]}
    pgroup = {}
    with open(Path(refresh) / "d2/NC_CELLS_D2_V1.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            pgroup[r["design_id"]] = r["policy_group"]
    grid = {}
    with open(GRID, newline="") as fh:
        for r in csv.DictReader(fh):
            grid[(r["policy_group"], r["outcome_family"])] = r["final_code"]
    nc = defaultdict(bool)
    fams_all = {(d, f) for d in pgroup for (g, f) in grid if g == pgroup[d]}
    for (d, f) in fams_all:
        nc[(d, f)] = grid.get((pgroup[d], f)) == "no_channel"
    rate = {k: v for k, v in np.load(Path(refresh) / "sweep/series_store_v1.npz", allow_pickle=False).items()}
    rate.update({k: v for k, v in np.load(ROOT / "data/l3_meta/sweep_ext_v1/series_store_ext_v1.npz", allow_pickle=False).items() if k not in ("units", "years", "meta_json")})
    cntz = np.load(Path(refresh) / "sweep/series_store_v1_count.npz", allow_pickle=False)
    count_series = sorted({k.rsplit("__", 1)[0] for k in cntz.files if k.endswith("__val")})
    inv_rows = S.inventory_rows()
    registered = D4M.registered_series(inv_rows)
    kap = json.loads((Path(refresh) / "cal/RESULTS_CAL_V1.json").read_text())["question"]["CAL"]["kappa"]
    k_cal = OrderedDict([("raw", 1.0), ("calibrated", float(kap["kappa_hat"]))])
    k_ref = OrderedDict([("raw", 1.0), ("calibrated", 1.0)])           # under a reference the kappa is 1 (the reference replaces it)
    series_family = {}
    res = OrderedDict()
    for ref, union in (("U", False), ("U", True), ("P5", False), ("P5", True), ("P3", True), ("P8", True), ("N5", True)):
        U = load_union(ref, refresh, union)
        for u in U.values():
            series_family.update(dict(zip([str(s) for s in u["arr"]["series_ids"]], [str(f) for f in u["arr"]["family"]])))
        designs, skipped = build_designs(U, live, D1M, D4M)
        tag = f"{ref}_{'union' if union else 'v1'}"
        full = ref in ("U", "P5") and True
        res[tag] = run_combo(tag, designs, k_cal if ref == "U" else k_ref, D4M, nc, registered, set(count_series), (rate, cntz), series_family, do_d56=full, loo=(tag in ("P5_union", "U_union")))
        res[tag]["skipped_designs"] = skipped
        print(tag, len(designs), "designs", round(time.time() - t0), "s", flush=True)
    # decision rule 4.1 and 4.3 readouts
    def policy_p(tag):
        r = res[tag]["d4_series"]
        return r["levels"]["raw"]["p_one_sided"]["A_row"] if r.get("code") == "EVAL" else None
    def policy_ex(tag):
        r = res[tag]["d4_series"]
        return r["levels"]["raw"]["excess"]["A_row"] if r.get("code") == "EVAL" else None
    rules = OrderedDict()
    pp = {t: policy_p(t) for t in ("P3_union", "P5_union", "P8_union")}
    rules["d4_policy_p_by_blocks"] = pp
    rules["d4_policy_label_stands"] = bool(all(v is not None and v <= 0.05 for v in pp.values()))
    eu, en = policy_ex("U_union"), policy_ex("N5_union")
    rules["d4_n5_vs_u_policy_excess"] = dict(U=eu, N5=en, within_25pct=bool(eu is not None and en is not None and abs(en - eu) <= 0.25 * abs(eu)))
    d5 = res["P5_union"].get("d5")
    if d5:
        ns = [r for r in d5["series"] if r["stratum"] == "NC-DOMINATED" and r["levels"]["raw"]["beyond_noise"]]
        rules["d5_nc_dominated_beyond_noise"] = len(ns)
        rules["d5_keys_beyond_noise_p5"] = d5["summary"]["levels"]["raw"]["keys_with_label"]
        rules["d5_keys_at_or_above_floor"] = d5["summary"]["keys_at_or_above_floor"]
    nce = res["P5_union"]["nc_cell_exceedance"]
    rules["p5_line_of_record"] = bool(nce["cp95"] and nce["cp95"][0] <= 0.05 <= nce["cp95"][1]) if nce["n_cells"] else None
    rules["nc_share_gt_9pct"] = bool(nce["share"] is not None and nce["share"] > 0.09)
    stamp = OrderedDict(rule_id="EX-1", question="R0", version="R0-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), refresh=str(refresh), seeds=SEEDS, prereg_sha256=sha(PREREG),
                        hash_records=n_rec, seconds=round(time.time() - t0, 1), gate_report_sha256=sha(D / "GATE_R0_V1.json"),
                        code={f: sha(ROOT / f) for f in CODE if (ROOT / f).exists()})
    out_path.write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, rules=rules, results=res)), indent=1, ensure_ascii=False, allow_nan=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps(B1.clean(rules), default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["pin", "gate", "refs", "analyze"])
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--refresh", default=str(M.REFRESH))
    a = ap.parse_args(argv)
    refresh = Path(a.refresh)
    if a.stage == "pin":
        stage_pin()
    elif a.stage == "gate":
        return stage_gate(a.workers, refresh)
    elif a.stage == "refs":
        return stage_refs(a.workers, refresh)
    else:
        return stage_analyze(refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
