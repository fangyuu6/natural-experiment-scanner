"""T227 archive part: estimator comparison for the 21 estimated designs that are NOT registry designs
(13 'prototype' + 8 'manual_batch').  EXPLORATORY, offline, read-only on every L2 / did_samples / l2_batch file.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/estimator_check_20261021/archive/run_archive.py --workers 5 [--only id,..] [--no-ri]
Writes data/l3_meta/estimator_check_20261021/PER_DESIGN_ESTIMATORS_ARCHIVE.csv (+ .json).

For every design: rebuild the design with the engine's own structural_design (case-engine copy for prototypes, batch support for
manual batches), require overall ATT == stored ATT (1e-8), then compare with eclib: CS not-yet-treated, Sun-Abraham, static TWFE,
HonestDiD-style breakdown, and RI on the same fixed label shuffles as the pipeline (RI_SEED) for the alternatives.
Columns follow run_designs.py (PER_DESIGN_ESTIMATORS.csv) plus source_group / staggered / ratio / sign-flip columns.
"""
import sys, os, json, csv, time, argparse, math, traceback, random, statistics, copy
for k in ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(k, "1")
from pathlib import Path
import multiprocessing as mp
import numpy as np
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import eclib as E  # noqa: E402

R = E.ROOT
sys.path.insert(0, str(R))
OUT = R / "data/l3_meta/estimator_check_20261021"
LIVE = R / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"
TOL = 1e-8
RI_SEED = 20261002


class H:
    """minimal `o` object for eclib (fields used by agg_weights / overall_se / twfe / sun_abraham / honest_breakdown)."""


def make_o(D, y, min_controls, v11):
    o = H()
    o.D, o.y = D, y
    o.keys = sorted(D["mask"])
    o.index = {k: i for i, k in enumerate(o.keys)}
    o.yvec = np.array([y[k] for k in o.keys], dtype=float)
    o.min_controls, o.v11 = min_controls, v11
    return o


# ------------------------------------------------------------------ loaders
def load_case_type(entry):
    """prototype designs run by did_samples case_run (cases/<id>); returns dict(D, y, relabel_draws, fake_fn, ...)"""
    from scripts.did_samples.cases import case_run as CR, case_engine as eng, case_panel_view as pv
    cid = entry["design_id"].split(":", 1)[1]
    runid = entry["source"]["pointer"].split("/primary_estimates/", 1)[1]
    case = json.load(open(R / f"data/did_samples/{cid}/case.json"))
    reg = {r["id"]: r for r in CR.registry(case)}
    spec = reg[runid]
    loaded = CR.load_panel(case, R)
    tr = CR.load_treatment(case, R)
    vc = pv.effective_case(case, spec.get("variant"))
    sel = pv.analysis_rows(vc, loaded, tr, outcome=spec["outcome"])
    rows = eng.normalize_rows(vc, sel)
    rows = [(u, t, float(y), g) for u, t, y, g in rows]
    D = eng.structural_design(vc, rows, tr, window=spec["window"], control_group=spec["control_group"], normalized=True)
    y = {(u, t): v for u, t, v, g in rows}
    min_never = vc.get("min_controls", 1) if "min_post_periods" in vc else 1
    v11 = (dict(min_post=vc.get("v11_min_post_periods", 2), min_pre=vc.get("v11_min_pre_periods", 3))
           if "v11_min_controls" in vc else None)
    min_nyt = vc.get("v11_min_controls", 5) if v11 else 1
    ctx = dict(kind="case", cid=cid, case=vc, rows=rows, tr=tr, spec=spec, reg=reg, eng=eng)

    def draws(n):
        rng = random.Random(eng.RI_SEED)
        labels = [D["adoption"][u] for u in D["units"]]
        offs = [None if g is None else D["operative"][u] - g for u, g in zip(D["units"], labels)]
        prof = [D["exposure"][u] for u in D["units"]]
        for _ in range(n):
            sh = list(zip(labels, offs, prof))
            rng.shuffle(sh)
            yield sh

    def fake_fn(sh):
        idx = {u: i for i, u in enumerate(D["units"])}
        fr = [(u, t, yy, sh[idx[u]][0]) for u, t, yy, g in rows]
        fo = {u: None if g is None else g + off for u, (g, off, p) in zip(D["units"], sh)}
        return eng.structural_design(vc, fr, tr, window=spec["window"], operative=fo, control_group=spec["control_group"],
                                     normalized=True, exposure_override={u: p for u, (g, off, p) in zip(D["units"], sh)})

    # engine-own not-yet-treated design, as an independent check of eclib's NYT cells (and a stored NYT estimate, if registered)
    nyt_id = runid.rsplit("/", 1)[0] + "/not_yet_treated"
    nyt = dict(id=nyt_id)
    if nyt_id in reg and reg[nyt_id]["window"] == spec["window"]:
        try:
            Dn = eng.structural_design(vc, rows, tr, window=spec["window"], control_group="not_yet_treated", normalized=True)
            nyt["design"] = Dn
            nyt["att_engine"] = float(E.agg_weights(Dn["cells"], {k: i for i, k in enumerate(sorted(D["mask"]))}, len(D["mask"])) @
                                      np.array([y[k] for k in sorted(D["mask"])]))
        except Exception as ex:
            nyt["error"] = f"{type(ex).__name__}: {ex}"[:150]
    engine_att = eng.estimate_design(D, y, B=20)["overall"]["att"]
    return dict(D=D, y=y, min_never=min_never, min_nyt=min_nyt, v11=v11, draws=draws, fake_fn=fake_fn, engine_att=engine_att,
                nyt=nyt, infeasible=lambda ex: isinstance(ex, ValueError), folder=entry["source"]["path"])


def load_batch_type(entry):
    path = entry["source"]["path"]
    key = Path(path).stem
    if "l2_batch1" in path:
        from scripts.l2_batch1 import runner as RN, runner_support as SP
        decl = json.load(open(R / "scripts/l2_batch1/case_declarations.json"))["cases"][key]
        rows, _ = RN.load_panel(R, key)
    else:
        b = "l2_batch2" if "l2_batch2" in path else "l2_batch3"
        import importlib
        SP = importlib.import_module(f"scripts.{b}.runner_support")
        PL = importlib.import_module(f"scripts.{b}.panel_loader")
        pkg = SP.load_package(R)
        decl = SP.variant_declaration(key, "primary", pkg)
        rows, _ = PL.load_panel(R, key, "primary")
    D = SP.build_design(decl, rows)
    y = {(u, t): v for u, t, v, g in SP.structural_rows(decl, rows)}
    from scripts.did_samples.cases import case_engine as eng
    engine_att = eng.estimate_design(D, y, B=20)["overall"]["att"]
    family = SP.pre_family(D)

    def draws(n):
        return iter(SP.predraw_labels(D, n))

    def fake_fn(labels):
        f = SP.relabeled_design(decl, D, labels)
        if SP.pre_family(f) != family:
            raise ValueError("RI lead inventory changed under relabeling")
        return f

    return dict(D=D, y=y, min_never=1, min_nyt=1, v11=None, draws=draws, fake_fn=fake_fn, engine_att=engine_att,
                nyt=dict(), infeasible=lambda ex: SP.support_failure(ex) or str(ex).startswith("RI lead inventory"),
                folder=path)


def pseudo_design(rows, g_of, T, ant):
    """Design dict for a balanced rectangle estimated by did_samples.cs (first-generation examples).
    clean: t < g - ant ; full: t >= g (anticipation year is neither clean nor full, so base = g-1-ant and post starts at g)."""
    units = sorted({u for u, t, y, g in rows})
    periods = list(range(T))
    adoption = {u: g_of.get(u) for u in units}
    exposure = {u: {t: dict(clean=(adoption[u] is None or t < adoption[u] - ant), full=(adoption[u] is not None and t >= adoption[u]))
                    for t in periods} for u in units}
    D = dict(units=units, periods=periods, observed_periods=periods, adoption=adoption,
             mask={(u, t) for u, t, y, g in rows}, exposure=exposure,
             whole_window_clean={u: adoption[u] is None for u in units},
             groups=sorted({g for g in adoption.values() if g is not None and 0 <= g <= T - 1}),
             window=dict(start=0, end=T - 1), control_group="never_treated")
    D["cells"] = E.make_cells(D, "never")
    return D


def load_example_type(entry):
    """first-generation prototypes run by did_samples/estimate.py (own CS on a balanced rectangle)."""
    cid = entry["design_id"].split(":", 1)[1]
    if cid == "medicaid_expansion":
        raise NotImplementedError("medicaid_expansion primary is a population-weighted (fixed 2013 denominator), universal-base, "
                                  "not-yet-treated estimator with Mammen multiplier; eclib has no weighted estimator, so the "
                                  "comparison would not be on the same estimand")
    from scripts.did_samples import estimate as ES, cs
    config = json.load(open(R / f"scripts/did_samples/examples/{cid}/config.json"))
    rec, policy, issues = ES.verify_amendments(config, R)
    if issues:
        raise RuntimeError(f"amendment verification issues: {issues[:1]}")
    base = dict(config, parameters=policy["parameters"])
    records = list(csv.DictReader(open(R / ES.parameter(base, "panel_path"))))
    castle = cid == "castle_doctrine"
    win, out = (None, "y") if castle else ("E1", "y_employment")
    rows, sel, years, adopt = ES.prepare_panel(base, records, window=win, outcome=out)
    ant = 1 if castle else 0
    rules = ES.parameter(base, "validation_rules", {})
    kw = dict(anticipation=ant, control_group="never_treated", B=19, seed=ES.parameter(base, "seed", 20261001),
              pre_min_treated=rules.get("pre_min_treated", 5), pre_event_min=rules.get("pre_event_min"), pre_event_max=rules.get("pre_event_max"))
    if castle:
        kw["overall_min_event_time"] = 0
    engine_att = ES._overall(cs.estimate(rows, **kw))["att"]
    g_obs = {}
    for u, t, yy, g in rows:
        g_obs[u] = g
    D = pseudo_design(rows, g_obs, len(years), ant)
    y = {(u, t): float(v) for u, t, v, g in rows}
    T = len(years)
    draws = None; fake_fn = None
    if castle:
        d = ES.parameter(base, "randomization_inference")
        units = sorted(g_obs); labels = [g for g in g_obs.values() if g is not None]

        def draws(n):
            rng = random.Random(d["seed"])
            for _ in range(n):
                yield dict(zip(rng.sample(units, len(labels)), labels))

        def fake_fn(fake):
            return pseudo_design(rows, fake, T, ant)
        stored_p = None
        for r in json.load(open(R / "scripts/did_samples/results/castle_doctrine.json"))["registered_results"]:
            if r.get("id") == "P1/permutation":
                stored_p = r["result"]["p_two_sided"]
        n_ri = d["draws"]
    return dict(D=D, y=y, min_never=1, min_nyt=1, v11=None, draws=draws, fake_fn=fake_fn, engine_att=engine_att, nyt={},
                infeasible=lambda ex: isinstance(ex, ValueError), folder=entry["source"]["path"], n_ri=(n_ri if castle else None),
                stored_p=(stored_p if castle else None), pseudo=True)


# ------------------------------------------------------------------ RI over fixed label draws (pipeline, CS-NYT, TWFE)
def ri_all(o, L, n=999, do_alt=True):
    pipe = np.full(n, np.nan); alt = np.full(n, np.nan); tw = np.full(n, np.nan)
    K = len(o.keys)
    for b, lab in enumerate(L["draws"](n)):
        try:
            fake = L["fake_fn"](lab)
        except ValueError as ex:
            if L["infeasible"](ex):
                continue
            raise
        post = [c for c in fake["cells"] if c["post"]]
        if not post:
            continue
        pipe[b] = float(E.agg_weights(fake["cells"], o.index, K) @ o.yvec)
        if not do_alt:
            continue
        cy = E.make_cells(fake, "nyt", groups=fake["groups"], min_controls=L["min_nyt"], v11=L["v11"])
        if any(c["post"] for c in cy):
            alt[b] = float(E.agg_weights(cy, o.index, K) @ o.yvec)
        try:
            tw[b] = E.twfe(o, fake["cells"], D=fake, se=False)[0]
        except Exception:
            pass
    return pipe, alt, tw


def one(task):
    entry, do_ri = task
    did = entry["design_id"]; grp = entry["source_group"]
    t0 = time.time()
    rec = dict(design_id=did, folder=entry["source"]["path"], source_group=grp, status="ok")
    try:
        if grp == "manual_batch":
            L = load_batch_type(entry)
        elif entry["design_id"].split(":", 1)[1] in ("castle_doctrine", "medicaid_expansion", "paid_sick_leave"):
            L = load_example_type(entry)
        else:
            L = load_case_type(entry)
        D, y = L["D"], L["y"]
        stored = float(entry["point_estimate"])
        rec["pipe_att_stored"] = stored
        rec["engine_att_rebuilt"] = L["engine_att"]
        rec["rebuild_absdiff"] = abs(L["engine_att"] - stored)
        if not rec["rebuild_absdiff"] <= TOL:
            rec["status"] = "skipped"
            rec["reason"] = f"engine rebuild does not reproduce stored ATT (|diff|={rec['rebuild_absdiff']:.3g})"
            return rec
        o = make_o(D, y, L["min_never"], L["v11"])
        K = len(o.keys)
        rec["rebuild_kind"] = "pseudo-design on the first-generation cs rectangle" if L.get("pseudo") else "engine structural_design"
        rec["pipe_control"] = "never"
        c0 = E.make_cells(D, "never", min_controls=L["min_never"])
        key = lambda c: (c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], bool(c["post"]))
        rec["cells_reproduce_pipeline"] = [key(c) for c in c0] == [key(c) for c in D["cells"]]
        att0 = float(E.agg_weights(c0, o.index, K) @ o.yvec)
        a_se = E.overall_se(o, c0)[1]
        post0 = [c for c in c0 if c["post"]]
        rec.update(n_units=len(D["units"]), n_cohorts=len({c["g"] for c in post0}), n_post_cells=len(post0),
                   n_treated_mass=sum(c["n_treated"] for c in post0), n_pre_cells=sum(1 for c in c0 if not c["post"]),
                   window_start=D["window"]["start"], window_end=D["window"]["end"],
                   pipe_att_recomputed=att0, pipe_recompute_absdiff=abs(att0 - stored),
                   pipe_ci95_lo=(entry.get("ci95") or [None, None])[0], pipe_ci95_hi=(entry.get("ci95") or [None, None])[1],
                   pipe_se_analytic=a_se, pipe_ri_p2_stored=entry.get("ri_p"), pipe_ri_cutoff_stored=entry["primary_outcomes"][0].get("noise_cutoff"),
                   pipe_ri_beyond_stored=entry.get("beyond_noise"))
        rec["staggered"] = rec["n_cohorts"] >= 2
        if not rec["pipe_recompute_absdiff"] <= TOL:
            rec["status"] = "skipped"
            rec["reason"] = f"eclib never-treated cells do not reproduce stored ATT (|diff|={rec['pipe_recompute_absdiff']:.3g})"
            return rec
        # CS not-yet-treated
        cy = E.make_cells(D, "nyt", min_controls=L["min_nyt"], v11=L["v11"])
        posy = [c for c in cy if c["post"]]
        rec["alt_supported"] = bool(posy)
        atty = None
        if posy:
            atty = float(E.agg_weights(cy, o.index, K) @ o.yvec)
            rec.update(alt_att=atty, alt_se=E.overall_se(o, cy)[1], alt_n_post_cells=len(posy),
                       alt_n_cohorts=len({c["g"] for c in posy}), alt_treated_mass=sum(c["n_treated"] for c in posy),
                       alt_mean_controls=float(np.mean([c["n_control"] for c in posy])), pipe_mean_controls=float(np.mean([c["n_control"] for c in post0])),
                       alt_att_group_agg=float(E.agg_weights(cy, o.index, K, "group") @ o.yvec))
        rec["pipe_att_group_agg"] = float(E.agg_weights(c0, o.index, K, "group") @ o.yvec)
        nyt = L.get("nyt") or {}
        if "design" in nyt:
            ke = sorted((c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], bool(c["post"])) for c in nyt["design"]["cells"])
            km = sorted(key(c) for c in cy)
            rec["alt_cells_match_engine_nyt"] = ke == km
            rec["engine_nyt_att"] = nyt.get("att_engine")
        elif "error" in nyt:
            rec["engine_nyt_error"] = nyt["error"]
        # Sun-Abraham and TWFE
        try:
            sa, sase, nrow, miss = E.sun_abraham(o, c0)
            rec.update(sa_att=sa, sa_se_cluster=sase, sa_rows=nrow, sa_missing_cells=miss)
        except Exception as ex:
            rec["sa_error"] = f"{type(ex).__name__}: {ex}"[:200]
        try:
            tw, twse, nrow = E.twfe(o, c0)
            rec.update(twfe_att=tw, twfe_se_cluster=twse, twfe_rows=nrow)
        except Exception as ex:
            rec["twfe_error"] = f"{type(ex).__name__}: {ex}"[:200]
        # RI on the pipeline's fixed label shuffles
        if do_ri and L["draws"] is None:
            rec["ri_note"] = "no registered RI line for this design (stored noise fields null); alternatives' RI left blank"
        if do_ri and L["draws"] is not None:
            pipe, alt, twd = ri_all(o, L, n=L.get("n_ri") or 999)
            if L.get("stored_p") is not None:
                rec["pipe_ri_p2_stored"] = L["stored_p"]
            for name, att, d in (("pipe", att0, pipe), ("alt", atty, alt), ("twfe", rec.get("twfe_att"), twd)):
                if att is None:
                    continue
                ns = E.noise_stats(att, d)
                if name == "pipe":
                    rec.update(pipe_ri_p2_recomputed=ns["p2"], pipe_ri_cutoff_recomputed=ns["cutoff"], pipe_ri_beyond_recomputed=ns["beyond"],
                               pipe_ri_n=ns["n"])
                else:
                    rec.update({f"{name}_ri_p2": ns["p2"], f"{name}_ri_cutoff": ns["cutoff"], f"{name}_ri_beyond": ns["beyond"], f"{name}_ri_n": ns["n"]})
        rec["honest"] = E.honest_breakdown(o, c0, att0, a_se)
        # ratios and sign flips
        for nm, v in (("alt", rec.get("alt_att")), ("sa", rec.get("sa_att")), ("twfe", rec.get("twfe_att"))):
            if v is not None and stored != 0:
                rec[f"ratio_{nm}"] = v / stored
                rec[f"signflip_{nm}"] = bool(np.sign(v) != np.sign(stored) and v != 0)
        if rec.get("alt_ri_beyond") is not None and rec.get("pipe_ri_beyond_stored") is not None:
            rec["alt_beyond_changed"] = bool(rec["alt_ri_beyond"]) != bool(rec["pipe_ri_beyond_stored"])
        if rec.get("twfe_ri_beyond") is not None and rec.get("pipe_ri_beyond_stored") is not None:
            rec["twfe_beyond_changed"] = bool(rec["twfe_ri_beyond"]) != bool(rec["pipe_ri_beyond_stored"])
    except NotImplementedError as ex:
        rec["status"] = "skipped"; rec["reason"] = str(ex)[:400]
    except Exception as ex:
        rec["status"] = "error"; rec["reason"] = f"{type(ex).__name__}: {ex}"[:300]; rec["trace"] = traceback.format_exc()[-700:]
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5); ap.add_argument("--only", default=None); ap.add_argument("--no-ri", action="store_true")
    ap.add_argument("--out", default=str(OUT / "PER_DESIGN_ESTIMATORS_ARCHIVE"))
    a = ap.parse_args()
    live = json.load(open(LIVE))
    ents = [d for d in live["designs"] if d.get("status") == "estimated" and d.get("source_group") in ("prototype", "manual_batch")]
    if a.only:
        keep = set(a.only.split(",")); ents = [d for d in ents if d["design_id"] in keep]
    tasks = [(d, not a.no_ri) for d in ents]
    ctx = mp.get_context("spawn")
    recs = []; t0 = time.time()
    with ctx.Pool(a.workers) as pool:
        for r in pool.imap_unordered(one, tasks, chunksize=1):
            recs.append(r); print(f"[{len(recs)}/{len(tasks)}] {r['design_id']} {r['status']} {r.get('reason','')[:80]} {r['seconds']}s ({time.time()-t0:.0f}s)", file=sys.stderr, flush=True)
    recs.sort(key=lambda r: (r["source_group"], r["design_id"]))
    json.dump(recs, open(a.out + ".json", "w"), indent=1, default=str)
    flat = []
    for r in recs:
        f = {k: v for k, v in r.items() if k not in ("honest", "trace")}
        for k, v in (r.get("honest") or {}).items():
            if not isinstance(v, (list, dict)):
                f["h_" + k] = v
        flat.append(f)
    cols = []
    for f in flat:
        for k in f:
            if k not in cols:
                cols.append(k)
    with open(a.out + ".csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(flat)


if __name__ == "__main__":
    main()
