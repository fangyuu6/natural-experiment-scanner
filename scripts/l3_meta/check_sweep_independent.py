#!/usr/bin/env python3
"""Independent recomputation of the outcome sweep (stage 5; amendment SWEEP_V1 8.3).  Plain loops; imports NOTHING from sweep_core, build_sweep, build_d1
or build_d3_v2 (the L2 engine is imported read-only: that is the code the sweep is meant to apply).

For a random tenth of the supported (design, series) pairs of the sweep (random.Random(20261015) over the sorted list; at least one per design) it
rebuilds from the catalog and the frozen design files, without the sweep's operators: the series and its eligible cells, the declaration and the
design of the cell pattern, the 999 aligned label bundles (shuffled index lists), the ATT with engine.runner.fit_primary and every placebo ATT with
engine.ri.one_reference, and compares them with the sweep file (1e-9 relative).  It also recomputes the D1 table from the sweep files and compares it
with RESULTS_D1_V1.json, and the D3-V2 numbers from D3_RUNS.json with RESULTS_D3_V2.json, when those files are given.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_sweep_independent.py [--sweep-dir DIR] [--workers N] [--d1 FILE] [--d3 FILE] [--d3runs FILE]
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import multiprocessing as mp
import random
import statistics
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

RI_SEED = 20261002
N_DRAWS = 999
TOL = 1e-9
SAMPLE_SEED = 20261015
RUN_OVERRIDE = {"run8_extreme_risk_protection_order":
                "data/l2_pipeline/sources/research_panel/v8b_20261009/fresh_audit_jobs/confirmed_c10/c11/work/run8_extreme_risk_protection_order/v12"}


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def scaled_diff(x, y):
    return abs(x - y) / max(1.0, abs(x), abs(y))


# ---------------------------------------------------------------------------------------------
# series (plain re-implementation of the reading rules)
# ---------------------------------------------------------------------------------------------
def csv_text(v):
    return "" if v is None else str(v)


def strict_ok(meta):
    if any(csv_text(meta.get(f, "1")) != "1" for f in ("available", "annual_usable", "linkage_certified")):
        return False
    return all(csv_text(meta.get(f, "0")).strip().lower() in ("0", "false", "") for f in ("suppression_flag", "suppressed", "unreliable"))


def series_cells(sid, scale_mode):
    """{(unit, year): value} of the eligible cells of a catalog series, read again from the files."""
    from scripts.l2_pipeline.connectors import construct, ConnectorRequest
    from scripts.l2_pipeline.connectors.common import STATES, NAMES
    from scripts.l2_pipeline import variants
    from scripts.l2_pipeline.contract_card import registered_execution_card
    cat = load_json(ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json")["series"][sid]
    if cat.get("adapter") == "wonder":
        return wonder_cells(sid, cat, scale_mode)
    live = load_json(ROOT / "data/l3_meta/l2_set_live.json")
    # any registered card serves the request; take the last registered estimated design of the live set
    row = [d for d in live["designs"] if d["status"] == "estimated" and d["source_group"] == "autopilot_estimate"][-1]
    folder = ROOT / Path(row["source"]["path"]).parent
    card = registered_execution_card(folder, load_json(folder / "FREEZE_FINAL.json"))
    base = copy.deepcopy(card)
    base["outcome"] = dict(base["outcome"])
    base["outcome"]["units"] = sorted(STATES.values())
    base["outcome"]["period_years"] = list(cat["years"])
    base["outcome"].pop("years", None)
    sc = variants.secondary_card(base, {"role": "secondary", "outcome": sid, "connector": cat["registered_connector"]})
    pd = construct(ConnectorRequest.from_card(sc, ROOT))
    out = {}
    for r, m in zip(pd.rows, pd.metadata):
        v = r.get("outcome_value")
        if strict_ok(m) and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out[(r["unit"], int(r["year"]))] = float(v)
    return out


def wonder_cells(sid, cat, scale_mode):
    """Plain csv reading of a CDC WONDER TSV export: counts, populations, crude rates, suppression and reliability flags."""
    from scripts.l2_pipeline.connectors.common import NAMES
    out = {}
    count_series = cat["value_field"] == "Deaths" and cat["units"] == "deaths"
    for e in cat["exports"]:
        path = ROOT / e["raw_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == e["sha256"]
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh, delimiter="\t"))
        head = rows[0]
        ix = {name: head.index(name) for name in head if name}
        for r in rows[1:]:
            if len(r) < len(head) or not r[ix["State"]].strip().strip('"') or (r[0].strip().strip('"') == "Total"):
                continue
            state = r[ix["State"]].strip().strip('"')
            if state not in NAMES:
                continue
            year = int(r[ix["Year"]].strip().strip('"'))
            field = cat["value_field"]
            raw = r[ix[field]].strip()
            low = raw.lower()
            unreliable_text = low.endswith("(unreliable)")
            missing = low in ("", "suppressed", "unreliable", "not applicable", "not available", "n/a", "na", "*", "**", "--", "---", "...", "\u2026", "1-9") or unreliable_text
            if missing:
                continue
            value = float(raw.replace(",", ""))
            if count_series and scale_mode == "rate":
                pop = float(r[ix["Population"]].strip().replace(",", ""))
                crude = r[ix["Crude Rate"]].strip().lower()
                if pop <= 0 or "unreliable" in crude:
                    continue
                out[(NAMES[state], year)] = value / pop * 100000.0
            else:
                out[(NAMES[state], year)] = value
    return out


# ---------------------------------------------------------------------------------------------
# design (plain use of the engine)
# ---------------------------------------------------------------------------------------------
def design_setup(design_id, folder):
    from scripts.l2_pipeline import registered, support, variants, storage
    from scripts.l2_pipeline.contract_card import registered_execution_card
    from scripts.l2_pipeline.hygiene import inventory_names
    from scripts.l2_pipeline.stages.s3_support import variant_treatment
    from scripts.l2_pipeline.engine.design import build_design
    folder = Path(folder)
    manifest = load_json(folder / "FREEZE_FINAL.json")
    card = registered_execution_card(folder, manifest)
    spec = load_json(folder / "SPEC.json")
    support_name, panel_name = inventory_names(folder)
    sup = {r["variant"]: r for r in load_json(folder / support_name)["variants"]}
    pan = {r["variant"]: r for r in load_json(folder / panel_name)["variants"]}
    treatment = load_json(folder / "TREATMENT.json")
    cards = variants.registered_cards({k: v for k, v in card.items() if k != "_card_path"})
    pcard = cards["primary"]
    dec = registered.numerical_declaration(pcard, sup["primary"], treatment, "primary")
    meta_path = folder / pan["primary"].get("metadata_relative", "panels/primary/metadata.csv")
    required = registered.registered_panel_keys(dec, meta_path)
    design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(required)])
    legacy = False
    if design["cells"] != sup["primary"]["cell_inventory"]:
        dec = copy.deepcopy(dec)
        dec["case"].pop("min_post_periods", None)
        design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(required)])
        legacy = True
        assert design["cells"] == sup["primary"]["cell_inventory"], "the primary is not reproduced"
    return dict(card=pcard, treatment=treatment, dec=dec, design=design, legacy=legacy, support=support, variant_treatment=variant_treatment)


def entry_setup(folder, name):
    """Declaration and design of one registered entry (primary or secondary), rebuilt from the frozen files with the engine's own functions."""
    from scripts.l2_pipeline import registered, variants
    from scripts.l2_pipeline.contract_card import registered_execution_card
    from scripts.l2_pipeline.hygiene import inventory_names
    from scripts.l2_pipeline.engine.design import build_design
    folder = Path(folder)
    card = registered_execution_card(folder, load_json(folder / "FREEZE_FINAL.json"))
    support_name, panel_name = inventory_names(folder)
    sup = {r["variant"]: r for r in load_json(folder / support_name)["variants"]}
    treatment = load_json(folder / "TREATMENT.json")
    cards = variants.registered_cards({k: v for k, v in card.items() if k != "_card_path"})
    meta = folder / "panels" / name / "metadata.csv"
    if name not in sup and name.startswith("secondary_"):          # earlier preparations kept the secondaries' support in secondary_panels/
        sup[name] = load_json(folder / "secondary_panels" / name[len("secondary_"):] / "SUPPORT.json")
    if not meta.exists():
        meta = folder / "secondary_panels" / name[len("secondary_"):] / "metadata.csv"
    dec = registered.numerical_declaration(cards[name], sup[name], treatment, name)
    required = registered.registered_panel_keys(dec, meta)
    design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(required)])
    if design["cells"] != sup[name]["cell_inventory"]:
        dec = copy.deepcopy(dec)
        dec["case"].pop("min_post_periods", None)
        design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(required)])
        assert design["cells"] == sup[name]["cell_inventory"], "the entry is not reproduced"
    return dec, design


def check_total(task):
    """The total of a D3 pair on the target's mask, again with fit_primary and permutation_reference (the target's own label bundle)."""
    design_id, folder, pair = task
    res = dict(design_id=design_id, pair=pair["pair"], ok=False)
    try:
        from scripts.l2_pipeline.engine import ri
        from scripts.l2_pipeline.engine.runner import fit_primary
        dec, design = entry_setup(folder, pair["target_entry"])
        cells = series_cells(pair["total_series"], pair["scale_mode"])
        keys = [tuple(k) for k in pair["keys"]]
        assert sorted(design["mask"]) == sorted(keys)
        rows = [(u, t, cells[(u, t)], None) for u, t in keys]
        payload, design2, outcomes = fit_primary(dict(dec, variant="check"), rows, B=2)
        t = payload["result"]["overall"]["att"]
        ref = ri.permutation_reference(dec, design, outcomes, count=999)
        bad = {x["draw_index"] for x in ref["feasibility"]["infeasible_draws"]}
        res["t_diff"] = scaled_diff(t, pair["t"])
        res["draw_index_equal"] = [i for i in range(999) if i not in bad] == pair["draw_index"]
        res["draw_max_diff"] = max([scaled_diff(a, b) for a, b in zip(ref["overall"], pair["dt"])] or [0.0]) if len(ref["overall"]) == len(pair["dt"]) else None
        res["ok"] = bool(res["t_diff"] <= TOL and res["draw_index_equal"] and res["draw_max_diff"] is not None and res["draw_max_diff"] <= TOL)
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    return res


def d3_plain(a, da, t, dt):
    """D3 arithmetic of build file 4.1 to 4.5 with plain loops: returns the numbers of the pair."""
    n = len(da)
    c = t - a
    dc = [y - x for x, y in zip(da, dt)]
    sd_c = statistics.stdev(dc)
    sgn = lambda v: (v > 0) - (v < 0)
    s = -sgn(a) * c / sd_c
    s_star = [-sgn(x) * y / sd_c for x, y in zip(da, dc)]
    mu = sum(s_star) / n
    tau = statistics.stdev(s_star)
    cutoff_c = sorted(abs(v) for v in dc)[math.ceil(0.95 * n) - 1]
    return dict(a=a, t=t, c=c, sd_c=sd_c, cutoff_c=cutoff_c, s=s, mu=mu, tau=tau, Z=(s - mu) / tau, p_sub=(1 + sum(1 for v in s_star if v >= s)) / (n + 1),
                beyond_c=abs(c) > cutoff_c)


def check_d3(sweep_dir, d3runs_path, d3_path, workers):
    runs = load_json(d3runs_path)
    res = load_json(d3_path)
    live = load_json(ROOT / "data/l3_meta/l2_set_live.json")
    folders = {d["design_id"]: RUN_OVERRIDE.get(d["design_id"]) or str(Path(d["source"]["path"]).parent) for d in live["designs"]
               if d["status"] == "estimated" and d["source_group"] in ("registry_terminal", "autopilot_estimate")}
    tasks = []
    for d in runs["designs"]:
        for p in d.get("pairs", []):
            if p.get("t") is not None:
                tasks.append((d["design_id"], str(ROOT / folders[d["design_id"]]), dict(p, scale_mode=d["scale_mode"])))
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=min(workers, max(1, len(tasks))), maxtasksperchild=4) as pool:
        total_checks = pool.map(check_total, tasks, chunksize=1)
    problems = [r for r in total_checks if not r["ok"]]
    # the arithmetic from the stored target and the checked total
    pairs_by = {(p["design_id"], p["pair"]): p for p in res["pairs"]}
    worst = 0.0
    arithmetic = []
    for design_id, folder, p in tasks:
        row = pairs_by.get((design_id, p["pair"]))
        if row is None or row.get("code") != "EVAL":
            continue
        stored = load_json(Path(folder) / "RESULT.json")["registered_results"][p["target_entry"]]
        a = stored["result"]["overall"]["att"]
        da = stored["noise_floor"]["permutation_draws"]
        got = d3_plain(a, da, p["t"], p["dt"])
        mine = row["numbers"]
        for k, v in got.items():
            if isinstance(v, bool):
                if mine[k] != v:
                    arithmetic.append(f"{design_id}:{p['pair']} {k} differs")
                continue
            dif = scaled_diff(v, mine[k])
            worst = max(worst, dif)
            if dif > 1e-9:
                arithmetic.append(f"{design_id}:{p['pair']} {k} differs by {dif:.2e}")
    return dict(n_totals_checked=len(total_checks), total_failures=problems, arithmetic_problems=arithmetic, worst_scaled_difference=worst)


def pattern_design(setup, eligible, label):
    from scripts.l2_pipeline import registered
    from scripts.l2_pipeline.engine.design import build_design, structural_rows
    card, treatment = setup["card"], setup["treatment"]
    records = setup["variant_treatment"](treatment["records"], card)
    summ = setup["support"].support_check(eligible, records, card["analysis"], period_years=setup["support"].declared_annual_periods(card["outcome"]),
                                          transition_year_drop=card.get("transition_year_drop", False))
    if setup["legacy"]:
        summ = dict(summ, post_support_excluded_units=[])
    dec = registered.numerical_declaration(card, summ, treatment, label)
    if setup["legacy"]:
        dec = copy.deepcopy(dec)
        dec["case"].pop("min_post_periods", None)
    retained = structural_rows(dec, [(u, t, 0.0, None) for u, t in sorted(eligible)])
    cal = dec.get("period_years")
    keys = {(u, cal[t] if cal else t) for u, t, _, _ in retained}
    design = build_design(dec, [(u, t, 0.0, None) for u, t in sorted(keys)])
    return dec, keys, design


def aligned_bundles(setup, design_k, years):
    """Labels of draw b for the units of design_k: unit j of the primary receives the label of primary unit perm_b[j] (plain loops)."""
    from scripts.l2_pipeline.engine.design import build_design
    d0, dec0 = setup["design"], setup["dec"]
    units0 = list(d0["units"])
    full = build_design(dec0, [(u, t, 0.0, None) for u in units0 for t in years])["exposure"]
    labels0 = {}
    for u in units0:
        g = d0["adoption"][u]
        labels0[u] = (g, None if g is None else d0["operative"][u] - g, full[u], d0["whole_window_clean"][u])
    pos0 = {u: i for i, u in enumerate(units0)}
    rng = random.Random(RI_SEED)
    out = []
    for _ in range(N_DRAWS):
        idx = list(range(len(units0)))
        rng.shuffle(idx)
        out.append([labels0[units0[idx[pos0[u]]]] for u in design_k["units"]])
    return out


def check_cell(task):
    design_id, folder, sid, scale_mode, npz_path = task
    t0 = time.time()
    res = dict(design_id=design_id, series=sid, ok=False)
    try:
        from scripts.l2_pipeline.engine import ri
        from scripts.l2_pipeline.engine.design import structural_rows
        from scripts.l2_pipeline.engine.runner import fit_primary
        z = np.load(npz_path, allow_pickle=False)
        sids = [str(x) for x in z["series_ids"]]
        k = sids.index(sid)
        setup = design_setup(design_id, folder)
        years = setup["support"].declared_annual_periods(setup["card"]["outcome"])
        U0 = set(setup["design"]["units"])
        cells = series_cells(sid, scale_mode)
        eligible = {(u, y) for (u, y) in cells if u in U0 and y in set(years)}
        dec, keys, design, = pattern_design(setup, eligible, "check")
        assert z["code"][k] == "SUPPORTED"
        res["n_cells_equal"] = int(z["n_cells"][k]) == len(design["mask"]) and int(z["n_units"][k]) == len(design["units"])
        rows = [(u, t, cells[(u, t)], None) for u, t in sorted(keys)]
        dec_chk = dict(dec, variant="check")
        payload, design2, outcomes = fit_primary(dec_chk, rows, B=2)
        att = payload["result"]["overall"]["att"]
        res["att_diff"] = scaled_diff(att, float(z["att"][k]))
        family = ri.pre_family(design)
        bundles = aligned_bundles(setup, design, years)
        mine = []
        for labels in bundles:
            r = ri.one_reference(labels, (dec, design, outcomes, family))
            mine.append(float("nan") if r.get("infeasible") else float(r["overall"]))
        stored = z["placebo"][k]
        res["nan_pattern_equal"] = bool(all((math.isnan(a) == math.isnan(b)) for a, b in zip(mine, stored)))
        res["draw_max_diff"] = max([scaled_diff(a, float(b)) for a, b in zip(mine, stored) if not math.isnan(a) and not math.isnan(b)] or [0.0])
        fin = [v for v in mine if not math.isnan(v)]
        sd = statistics.stdev(fin)
        cutoff = sorted(abs(v) for v in fin)[math.ceil(0.95 * len(fin)) - 1]
        p2 = (1 + sum(1 for v in fin if abs(v) >= abs(att))) / (len(fin) + 1)
        res["stats_diff"] = max(scaled_diff(sd, float(z["sd"][k])), scaled_diff(cutoff, float(z["cutoff"][k])), scaled_diff(p2, float(z["p2"][k])))
        res["n_feasible_equal"] = len(fin) == int(z["n_feasible"][k])
        res["ok"] = bool(res["n_cells_equal"] and res["att_diff"] <= TOL and res["nan_pattern_equal"] and res["draw_max_diff"] <= TOL
                         and res["stats_diff"] <= TOL and res["n_feasible_equal"])
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    res["seconds"] = round(time.time() - t0, 1)
    return res


# ---------------------------------------------------------------------------------------------
# D1 and D3-V2 plain recomputation
# ---------------------------------------------------------------------------------------------
def d1_plain(sids, fam, code, att, placebo):
    """Section 6 of the amendment on arrays, with plain loops.  Returns None when the design is not EVAL."""
    n_all = len(placebo[0])
    A = [i for i in range(len(sids)) if code[i] == "SUPPORTED"]
    own_sd = {}
    for i in list(A):
        vals = [v for v in placebo[i] if not math.isnan(v)]
        s = statistics.stdev(vals) if len(vals) > 1 else float("nan")
        if not (s > 0):
            A.remove(i)
        else:
            own_sd[i] = s

    def common(idx):
        return [b for b in range(n_all) if all(not math.isnan(placebo[i][b]) for i in idx)]

    C = common(A)
    while A and len(C) < 900 and len({fam[i] for i in A}) >= 5:
        count = {i: sum(1 for v in placebo[i] if not math.isnan(v)) for i in A}
        fewest = min(count.values())
        drop = max((i for i in A if count[i] == fewest), key=lambda i: sids[i])
        A.remove(drop)
        C = common(A)
    families = sorted({fam[i] for i in A})
    if len(families) < 5 or len(C) < 900:
        return None
    n = len(C)
    sd = {i: statistics.stdev([placebo[i][b] for b in C]) for i in A}
    if any(not (v > 0) for v in sd.values()):
        return None
    nstar = [0] * n
    N = 0
    fam_out = {}
    for f in families:
        mem = [i for i in A if fam[i] == f]
        M = max(abs(att[i] / sd[i]) for i in mem)
        Ms = [max(abs(placebo[i][b] / sd[i]) for i in mem) for b in C]
        q = sorted(Ms)[math.ceil(0.95 * n) - 1]
        fam_out[f] = (M, q, M > q)
        N += int(M > q)
        for j in range(n):
            nstar[j] += int(Ms[j] > q)
    mu = sum(nstar) / n
    tau = math.sqrt(sum((v - mu) ** 2 for v in nstar) / (n - 1))
    if not tau > 0:
        return None
    return dict(N=N, mu=mu, tau=tau, Z=(N - mu) / tau, p=(1 + sum(1 for v in nstar if v >= N)) / (n + 1), n=n, families=fam_out)


def check_d1(sweep_dir, d1_path):
    d1 = load_json(d1_path)
    rows = {r["design_id"]: r for r in d1["designs"] if r.get("code") == "EVAL"}
    problems, worst = [], 0.0
    run = load_json(Path(sweep_dir) / "SWEEP_RUN.json")
    for d in run["designs"]:
        if d["status"] != "OK" or d["design_id"] not in rows:
            continue
        z = np.load(Path(sweep_dir) / Path(d["file"]).name, allow_pickle=False)
        got = d1_plain([str(x) for x in z["series_ids"]], [str(x) for x in z["family"]], [str(x) for x in z["code"]], z["att"].tolist(), z["placebo"].tolist())
        mine = rows[d["design_id"]]
        if got is None:
            problems.append(f"{d['design_id']}: plain recomputation is not EVAL")
            continue
        for key in ("N", "n"):
            if got[key] != (mine["N"] if key == "N" else mine["n_draws"]):
                problems.append(f"{d['design_id']}: {key} differs")
        for key, other in (("mu", mine["mu"]), ("tau", mine["tau"]), ("Z", mine["Z"]), ("p", mine["p"])):
            dif = scaled_diff(got[key], other)
            worst = max(worst, dif)
            if dif > 1e-9:
                problems.append(f"{d['design_id']}: {key} differs by {dif:.2e}")
        for f, (M, q, beyond) in got["families"].items():
            o = mine["family_stats"][f]
            if scaled_diff(M, o["M"]) > 1e-9 or scaled_diff(q, o["q"]) > 1e-9 or beyond != o["beyond"]:
                problems.append(f"{d['design_id']}: family {f} differs")
    return dict(n_designs=len(rows), problems=problems, worst_scaled_difference=worst)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sweep-dir", default=str(ROOT / "data/l3_meta/sweep_v1"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--fraction", type=float, default=0.1)
    ap.add_argument("--d1", default=None)
    ap.add_argument("--d3runs", default=None, help="D3_RUNS.json; with --d3 the totals and the D3-V2 arithmetic are checked")
    ap.add_argument("--d3", default=None, help="RESULTS_D3_V2.json")
    ap.add_argument("--extra", default=None, help="json file with extra (design, series) pairs to check (D3 pairs)")
    ap.add_argument("--out", default=None, help="write the report here (new file)")
    ap.add_argument("--skip-cells", action="store_true", help="skip the cell recomputation (done in an earlier run); check only the D1 / D3 parts")
    args = ap.parse_args(argv)
    sweep_dir = Path(args.sweep_dir)
    run = load_json(sweep_dir / "SWEEP_RUN.json")
    gate = load_json(sweep_dir / "GATE_REPORT.json")
    scale = {d["design_id"]: d.get("scale_mode") or "rate" for d in gate["designs"]}
    live = load_json(ROOT / "data/l3_meta/l2_set_live.json")
    folders = {}
    for d in live["designs"]:
        if d["status"] == "estimated" and d["source_group"] in ("registry_terminal", "autopilot_estimate"):
            folders[d["design_id"]] = RUN_OVERRIDE.get(d["design_id"]) or str(Path(d["source"]["path"]).parent)
    pairs = []
    for d in sorted(run["designs"], key=lambda r: r["design_id"]):
        if d["status"] != "OK":
            continue
        z = np.load(sweep_dir / Path(d["file"]).name, allow_pickle=False)
        pairs += [(d["design_id"], str(s)) for s, c in zip(z["series_ids"], z["code"]) if str(c) == "SUPPORTED"]
    rng = random.Random(SAMPLE_SEED)
    k = max(1, round(len(pairs) * args.fraction))
    chosen = set(rng.sample(pairs, k))
    for did in sorted({p[0] for p in pairs}):
        if not any(c[0] == did for c in chosen):
            chosen.add(rng.choice([p for p in pairs if p[0] == did]))
    if args.extra:
        for did, sid in load_json(args.extra):
            chosen.add((did, sid))
    if args.skip_cells:
        chosen = set()
    tasks = []
    for did, sid in sorted(chosen):
        f = [d["file"] for d in run["designs"] if d["design_id"] == did][0]
        tasks.append((did, str(ROOT / folders[did]), sid, scale[did], str(sweep_dir / Path(f).name)))
    t0 = time.time()
    ctx = mp.get_context("spawn")
    if tasks:
        with ctx.Pool(processes=args.workers, maxtasksperchild=20) as pool:
            results = pool.map(check_cell, tasks, chunksize=1)
    else:
        results = []
    report = dict(n_pairs_supported=len(pairs), n_checked=len(results), fraction=args.fraction, sample_seed=SAMPLE_SEED,
                  failures=[r for r in results if not r["ok"]], max_att_diff=max([r.get("att_diff", 0.0) for r in results] or [0.0]),
                  max_draw_diff=max([r.get("draw_max_diff", 0.0) for r in results] or [0.0]), seconds=round(time.time() - t0, 1))
    if args.d1:
        report["d1"] = check_d1(sweep_dir, args.d1)
    if args.d3runs and args.d3:
        report["d3"] = check_d3(sweep_dir, args.d3runs, args.d3, args.workers)
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        with open(args.out, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(report, cells=results), indent=1, default=str) + "\n")
    bad = report["failures"] or (args.d1 and report["d1"]["problems"]) or (args.d3runs and args.d3 and (report["d3"]["total_failures"] or report["d3"]["arithmetic_problems"]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
