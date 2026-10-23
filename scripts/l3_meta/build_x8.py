#!/usr/bin/env python3
"""X8 (rule id EX-1, exploratory): politics as an outcome series (policy feedback and a selection thermometer; random-timing draws only).  PREREG_X8_V1.md.

Stages:  adjud | freeze | pin | store | sweep | analyze   (pin writes the supplement; store/sweep/analyze refuse to run without it)
Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_x8.py <stage> [--workers N]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import coding_tools as CT  # noqa: E402
import m1_core as M  # noqa: E402
import build_m1 as B1  # noqa: E402

D = ROOT / "data/l3_meta/x8_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_X8_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_X8_V1_HASH.txt"
COV = ROOT / "data/l3_meta/covariates/state_year_v2.csv"
STORE = D / "series_store_x8_v1.npz"
X1 = ROOT / "data/l3_meta/x1_v1/RESULTS_X1_V1.json"
LIVE = M.REFRESH / "live/l2_set_live.json"
CAL = M.REFRESH / "cal/RESULTS_CAL_V1.json"
POL = ["pol_dem_minus_rep_unified_jan1", "pol_citizen_ideology", "pol_ln_real_income"]
SEEDS = dict(null=20261025, perm=20261026, boot=20261027)
CODE = ["scripts/l3_meta/build_x8.py", "scripts/l3_meta/coding_tools.py", "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py", "scripts/l3_meta/test_x8.py"]
CODING_FILES = ["data/l3_meta/x8_v1/coding/coder1/labels.csv", "data/l3_meta/x8_v1/coding/coder2/labels.csv", "data/l3_meta/x8_v1/coding/adjudication_packets.csv",
                "data/l3_meta/x8_v1/policy_side_table_v1.csv", "data/l3_meta/x8_v1/CODING_REPORT_V1.json"]


def build_store(S):
    import csv as _csv
    units = S.grid_axes()
    store = {s: dict(val={}, ok={}, sem={}, den={}, source="x8", units_string="") for s in POL}
    with open(COV, newline="", encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            st, y = r["state"], int(r["year"])
            if st not in units or y > 2025:
                continue
            def num(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            dm, rp = num(r["dem_unified_jan1"]), num(r["rep_unified_jan1"])
            vals = {POL[0]: (dm - rp) if dm is not None and rp is not None else None,
                    POL[1]: num(r["citizen_ideology"]) if r.get("ideology_carried_forward") not in ("1", "1.0") else None,
                    POL[2]: num(r["ln_pcpi_real_2024usd"])}
            for s, v in vals.items():
                if v is not None:
                    store[s]["val"][(st, y)] = v
                    store[s]["ok"][(st, y)] = True
                    store[s]["sem"][(st, y)] = True
                    store[s]["den"][(st, y)] = float("nan")
    return store


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


def sweep_task(task):
    import build_sweep as Bsw
    import sweep_core as S
    did, folder, result_path, out_dir = task
    t0 = time.time()
    out = dict(design_id=did, status="ERROR")
    try:
        store = S.SeriesStore(STORE).view("rate")
        table = OrderedDict((s, dict(family="politics_x8")) for s in POL)
        ctx = S.load_design_context(did, folder)
        res = S.sweep_design(ctx, store, table)
        recs = res["records"]
        sids = list(recs)
        placebo = np.vstack([recs[k]["placebo"] for k in sids])
        stats = [S.draw_statistics(recs[k]["att"], recs[k]["placebo"]) if recs[k]["code"] in ("SUPPORTED", "RI_FEASIBLE_LT_90") else dict(sd=np.nan, cutoff=np.nan, p2=np.nan, z=np.nan) for k in sids]
        arr = dict(series_ids=np.array(sids), family=np.array([recs[k]["family"] for k in sids]), code=np.array([recs[k]["code"] for k in sids]),
                   att=np.array([recs[k]["att"] for k in sids], float), sd=np.array([x["sd"] for x in stats], float), placebo=placebo,
                   n_cells=np.array([recs[k]["n_cells"] for k in sids]), kept=np.array([recs[k]["kept"] for k in sids], float))
        path = Path(out_dir) / (did + ".npz")
        if path.exists():
            raise RuntimeError(f"{path} exists")
        pr = Bsw.verify_l2(Bsw._WORKER.get("rec"))
        if pr:
            raise RuntimeError("L2 module changed: " + "; ".join(pr[:2]))
        with open(path, "xb") as fh:
            np.savez_compressed(fh, **arr)
        out.update(status="OK", codes=dict(Counter(recs[k]["code"] for k in sids)), patterns=len(res["patterns"]))
    except Exception as exc:
        import traceback
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        out["traceback"] = traceback.format_exc()[-1200:]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def verify(require_pin=True):
    problems, n = B1.verify_records_for(REC)
    if require_pin and n < 2:
        problems.append("the pinning supplement S1 is missing")
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    return n


def stage_pin():
    rep = json.loads((D / "CODING_REPORT_V1.json").read_text())
    if not rep["anchors"]["passed"]:
        sys.exit("anchor check failed: the feedback arm is not run")
    t = CT.write_supplement(REC, 1, "coding outputs and analysis code pinned before the stage that reads the politics ATT (section 4 of the pre-registration)", "docs/plans/l3-meta/PREREG_X8_V1.md", PREREG,
                            CODING_FILES + CODE, "no politics series had been swept and no X8 statistic existed at this supplement; code tested on synthetic data only (test_x8.py)", ROOT)
    print("wrote", t.name)


def stage_store():
    n = verify()
    import sweep_core as S
    S.load_l2()
    store = build_store(S)
    if STORE.exists():
        sys.exit("store exists")
    S.save_series_store(STORE, store, dict(series=POL, source=str(COV.relative_to(ROOT)), sha256=CT.sha256_file(COV)))
    print({s: len(v["val"]) for s, v in store.items()})


def stage_sweep(workers):
    verify()
    import l3_pins
    import build_sweep as Bsw
    import sweep_core as S
    l3_pins.install(Bsw)
    S.LIVE = LIVE
    S.load_l2()
    pr = Bsw.verify_l2()
    if pr:
        sys.exit("L2 modules differ from the sweep record: " + "; ".join(pr[:3]))
    out_dir = D / "sweep"
    out_dir.mkdir(exist_ok=True)
    rows = {r[0]: r for r in Bsw.roster()}
    gate = json.loads((M.REFRESH / "sweep/GATE_REPORT.json").read_text())
    todo, skipped = Bsw.swept_designs(gate)
    tasks = [(did, folder, rp, out_dir) for did, folder, rp, _s, _m in todo]
    Bsw._init_worker = init_worker
    res = sorted(Bsw._run_pool(sweep_task, tasks, workers, "x8"), key=lambda r: r["design_id"])
    (D / "X8_SWEEP_RUN.json").write_text(json.dumps(dict(n_ok=sum(r["status"] == "OK" for r in res), n_error=sum(r["status"] != "OK" for r in res), designs=res, skipped=skipped,
                                                      store_sha256=CT.sha256_file(STORE)), indent=1, default=str))
    print(Counter(r["status"] for r in res))
    return 0


# ---------------------------------------------------------------------------------------------
def analysis_inputs(sides):
    """combined data per design (politics + core series), real z and replicate machinery."""
    x1 = json.loads(X1.read_text())["results"]["U"]
    core = x1["core"]
    data, real = B1.load_real("U")
    comb, creal = {}, {}
    for d in data:
        px = np.load(D / "sweep" / f"{d}.npz", allow_pickle=False)
        ser = [str(s) for s in px["series_ids"]] + data[d]["series"]
        comb[d] = dict(series=ser, family=[str(f) for f in px["family"]] + data[d]["family"], code=[str(c) for c in px["code"]] + data[d]["code"],
                       placebo=np.vstack([px["placebo"], data[d]["placebo"]]))
        creal[d] = (np.concatenate([px["att"], real[d][0]]), np.concatenate([px["sd"], real[d][1]]))
    return comb, creal, core


def stage_analyze():
    n_rec = verify()
    out_path = D / "RESULTS_X8_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    t0 = time.time()
    sides_by_policy = CT.read_table(D / "policy_side_table_v1.csv")
    live = {x["design_id"]: x for x in json.loads(LIVE.read_text())["designs"]}
    key = {r["packet_id"]: r["design_id"] for r in csv.DictReader(open(D / "PACKET_KEY_V1.csv"))}
    pk = {r["packet_id"]: r["policy_name"] for r in CT.read_packet(D)}
    side = {key[p]: sides_by_policy[pk[p]] for p in pk}
    comb, creal, core = analysis_inputs(side)
    universe = POL + list(core)
    rows = M.design_arrays(comb, universe)
    ix = {s: i for i, s in enumerate(universe)}
    names_ix = [ix[s] for s in universe]
    ds_all = [d for d, r in rows.items() if r is not None and r["n_common"] >= M.MIN_COMMON]
    n_rep = 9999
    rng = np.random.default_rng(SEEDS["null"])
    Zn, Rd, ds = M.replicate_z(rows, names_ix, n_rep, rng)
    Zr = B1.real_z(comb, creal, ds, universe)           # (1, nd, 26)
    kappa = json.loads(CAL.read_text())["question"]["CAL"]["kappa"]["kappa_hat"]
    s_vec = np.array([side[d] for d in ds], float)
    directional = s_vec != 0
    fam_of = {d: live[d]["policy_family"] for d in ds}
    res = OrderedDict()
    # --- feedback
    def feedback(Z, svec):
        w = (svec != 0)
        v = svec[None, :, None] * Z[:, :, :3]
        v = np.where(w[None, :, None], v, np.nan)
        return np.nanmean(v, axis=1)                       # (reps, 3)
    Fo = feedback(Zr, s_vec)[0]
    Fn = feedback(Zn, s_vec)
    fb = OrderedDict()
    prng = np.random.default_rng(SEEDS["perm"])
    perm = np.array([feedback(Zr, prng.permutation(s_vec))[0] for _ in range(9999)])
    fams = sorted(set(fam_of.values()))
    mem = [np.array([i for i, d in enumerate(ds) if fam_of[d] == f]) for f in fams]
    brng = np.random.default_rng(SEEDS["boot"])
    boot = []
    for _ in range(9999):
        pick = np.concatenate([mem[i] for i in brng.integers(0, len(fams), len(fams))])
        boot.append(feedback(Zr[:, pick, :], s_vec[pick])[0])
    boot = np.array(boot)
    Fk = feedback(Zr / kappa, s_vec)[0]
    for j, p in enumerate(POL):
        nl = Fn[:, j]
        o = float(Fo[j])
        cell_ok = np.isfinite(Zr[0, directional, j])
        fb[p] = OrderedDict(observed=o, n_directional_cells=int(cell_ok.sum()), null_mean=float(np.nanmean(nl)), null_sd=float(np.nanstd(nl, ddof=1)),
                            null_q025_q975=[float(np.nanpercentile(nl, 2.5)), float(np.nanpercentile(nl, 97.5))],
                            rank_p_two_sided=float((1 + (np.abs(nl - np.nanmean(nl)) >= abs(o - np.nanmean(nl))).sum()) / (1 + np.isfinite(nl).sum())),
                            sides_permutation_share_ge=float((1 + (np.abs(perm[:, j]) >= abs(o)).sum()) / (1 + len(perm))),
                            family_block_ci95=[float(np.nanpercentile(boot[:, j], 2.5)), float(np.nanpercentile(boot[:, j], 97.5))],
                            kappa_scaled_observed=float(Fk[j]),
                            share_directional_designs_positive=float(np.mean((s_vec[directional] * Zr[0, directional, j])[np.isfinite(Zr[0, directional, j])] > 0)))
    res["policy_feedback"] = fb
    res["sides"] = OrderedDict(n_designs=len(ds), n_plus=int((s_vec > 0).sum()), n_minus=int((s_vec < 0).sum()), n_zero=int((s_vec == 0).sum()))
    # --- thermometer
    def cmat(Z, Rdd, scale_outcome=1.0):
        Zs = Z.copy()
        Zs[:, :, 3:] = Zs[:, :, 3:] / scale_outcome
        return np.concatenate([M.c_matrix(Zs[a:a + 250], Rdd)[0][:, :3, 3:] for a in range(0, Zs.shape[0], 250)])
    Co = cmat(Zr, Rd)[0]                                    # (3, 23)
    Cn = cmat(Zn, Rd)                                       # (reps, 3, 23)
    Cok = cmat(Zr, Rd, kappa)[0]
    th = OrderedDict()
    famc = [M.series_sets(M.load_null("U"))[3][s] for s in core] if False else None
    fam_map = {}
    for d in comb.values():
        fam_map.update(dict(zip(d["series"], d["family"])))
    flag_cut = 0.05 / 15
    for j, p in enumerate(POL):
        T_o = float(np.nanmean(Co[j] ** 2))
        T_n = np.nanmean(Cn[:, j, :] ** 2, axis=1)
        per = []
        for a, s in enumerate(core):
            nl = Cn[:, j, a]
            pv = float((1 + (np.abs(nl - np.nanmean(nl)) >= abs(Co[j, a] - np.nanmean(nl))).sum()) / (1 + np.isfinite(nl).sum()))
            per.append(OrderedDict(series=s, family=fam_map.get(s), C=float(Co[j, a]), null_sd=float(np.nanstd(nl, ddof=1)), z_vs_null=float((Co[j, a] - np.nanmean(nl)) / np.nanstd(nl, ddof=1)),
                                   rank_p=pv, flagged=bool(pv < flag_cut), C_kappa_scaled=float(Cok[j, a])))
        famm = OrderedDict()
        for f in sorted({x["family"] for x in per}):
            vals = [x["C"] for x in per if x["family"] == f]
            ii = [a for a, s in enumerate(core) if fam_map.get(s) == f]
            nl = np.nanmean(Cn[:, j, ii], axis=1)
            famm[f] = OrderedDict(n_series=len(vals), mean_C=float(np.mean(vals)), z_vs_null=float((np.mean(vals) - np.nanmean(nl)) / np.nanstd(nl, ddof=1)))
        th[p] = OrderedDict(T_observed=T_o, T_null_mean=float(np.nanmean(T_n)), T_null_sd=float(np.nanstd(T_n, ddof=1)), T_null_q95=float(np.nanpercentile(T_n, 95)),
                            T_rank_p=float((1 + (T_n >= T_o).sum()) / (1 + len(T_n))), T_kappa_scaled=float(np.nanmean(Cok[j] ** 2)),
                            n_flagged=sum(x["flagged"] for x in per), per_series=per, by_family=famm)
    res["thermometer"] = th
    stamp = OrderedDict(rule_id="EX-1", question="X8", version="X8-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), n_rep=n_rep, seeds=SEEDS, kappa=kappa, prereg_sha256=B1.sha256_file(PREREG),
                        hash_records=n_rec, n_designs=len(ds), core=list(core), politics_series=POL, seconds=round(time.time() - t0, 1), x8_store_sha256=CT.sha256_file(STORE),
                        code={f: CT.sha256_file(ROOT / f) for f in CODE if (ROOT / f).exists()})
    out_path.write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({p: {a: v[a] for a in ("observed", "null_mean", "null_sd", "rank_p_two_sided", "sides_permutation_share_ge", "family_block_ci95")} for p, v in fb.items()}, default=float))
    print({p: (round(v["T_observed"], 3), round(v["T_null_mean"], 3), v["T_rank_p"], v["n_flagged"]) for p, v in th.items()})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["adjud", "freeze", "pin", "store", "sweep", "analyze"])
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.stage == "adjud":
        print(CT.write_adjudication(D, "policy_name", ["policy_name", "policy_family"]))
    elif a.stage == "freeze":
        print(json.dumps(CT.freeze(D, "policy_name", "policy_name", 7, "policy_side_table_v1.csv")))
    elif a.stage == "pin":
        stage_pin()
    elif a.stage == "store":
        stage_store()
    elif a.stage == "sweep":
        return stage_sweep(a.workers)
    else:
        return stage_analyze()
    return 0


if __name__ == "__main__":
    sys.exit(main())
