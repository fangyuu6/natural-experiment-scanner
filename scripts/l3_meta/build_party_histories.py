#!/usr/bin/env python3
"""L3 package B, input 2: party-coded certified at-risk state-year archive for the 12 certified adoption histories
(+ an extension block for the other live designs that carry a passed-audit history).

What it builds (all metadata; no L2 estimate, SE, forecast or noise value is read):
  design_state_timeline_v1.csv   one row per design x state: kind (adopter / never / left_censored / unknown), operative onset year, exclusion class
  risk_state_year_v1.csv         one row per design x state x year at risk of FIRST adoption, with the adoption indicator,
                                 Jan-1 party (covariate file v2), the N7 control vector x_st and the diffusion stage
  design_summary_v1.json/.csv    sets, counts, hazards by party group and A_i (|h_D - h_R|) for the primary and 3 one-at-a-time variants
  RECEIPT_party_coding_v1.json   inputs, hashes, rules, checks

Definitions (PREREG_L3_META.md Q4; PREREG_L3_META_ADDENDUM_V2.md N1/N7; scripts/l2_pipeline/c64_metadata.py read-only):
  * Rows are state-years (s, t) with t in [H0+1, H1] where the policy was NOT in force on 1 January of t (first-adoption risk set):
    the state's operative onset year is >= t, or it never adopts inside the window.  The adoption indicator is 1 when onset == t.
    Rows after the first adoption are not at risk.  Unknown cells are dropped, never coded as non-adoption.
  * Onset = first operative year (first interval start; else operative_period / best_evidence_start_year / effective_year / start_period,
    the order of c64_metadata.active()).  A state in force at H0 is left-censored (L2 "always treated") and has no rows.
  * U_i (N1) = universe - excluded_units - excluded_conflict_states - always-treated; rows carry in_U.  Primary hazards use in_U = 1.
  * Window [H0, H1]: TREATMENT coverage, else CARD.treatment.source_window, else CARD.treatment.window, else CARD.outcome.years.
  * Primary party clock: Jan-1 governor (covariate file v2 column party_cat_jan1); primary opportunity rule: t > t0 where t0 = first
    adoption year in the certified history (SPEC_POOLED_V1: opportunity starts 1 January after the first recorded adoption).
  * Hazard of a party group = adoptions / risk-years in the group; A_i = |h_D_unified - h_R_unified|; a group needs >= 20 risk-years (Q4).

Run:  python3 -I -B scripts/l3_meta/build_party_histories.py [--out-dir DIR]     (standard library only)
Outputs are NEW files under data/l3_meta/party_coding/ and are never overwritten.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ROOT = C.ROOT
L2 = ROOT / "data/l2_pipeline"
OUT = ROOT / "data/l3_meta/party_coding"
COV = ROOT / "data/l3_meta/covariates"
VERSION = "v2"
INPUTS = {}


def note(role, path, **extra):
    p = Path(path)
    INPUTS[role] = {"path": C.rel(p), "bytes": p.stat().st_size, "sha256": C.sha256_file(p), **extra}
    return p


def jload(p):
    return json.loads(Path(p).read_text())


# ---------------------------------------------------------------------------------------------
# package resolution
# ---------------------------------------------------------------------------------------------
def first_certified_packages():
    """design_id -> (package_dir, run_id, kind) from the registry (first COMPLETED/STOPPED run), plus pre-run stops."""
    reg_path = note("terminal_runs", L2 / "registry/TERMINAL_RUNS.json")
    reg = jload(reg_path)
    out = {}
    for r in reg["runs"]:
        if r["status"] in ("COMPLETED", "STOPPED") and r["design_id"] not in out:
            out[r["design_id"]] = (Path(r["package_path"]), r["run_id"], "registry_first_certified_run")
    for p in reg["pre_run_stops"]:
        out.setdefault(p["design_id"], (Path(p["spec_path"]).parent, p["run_id"], "pre_run_stop"))
    return out


def park_package(design_id, park_path):
    """Latest attempt folder (autopilot/work/<id>/vN) holding a TREATMENT.json."""
    base = L2 / "autopilot/work" / design_id
    if not base.is_dir():
        return None
    cands = []
    for v in base.iterdir():
        if v.is_dir() and v.name.startswith("v") and v.name[1:].isdigit() and (v / "TREATMENT.json").exists():
            cands.append((int(v.name[1:]), v))
    return max(cands)[1] if cands else None


# ---------------------------------------------------------------------------------------------
# unit timelines
# ---------------------------------------------------------------------------------------------
def parse_year(v):
    """int | '2004-10-24' | '2002' -> year int, else None."""
    if type(v) is int:
        return v
    if isinstance(v, str) and len(v) >= 4 and v[:4].isdigit():
        return int(v[:4])
    return None


_SPELL_STATUS = {"exposed": True, "unexposed": False, "unknown": None}


def annual_path(row, H0, H1):
    """Policy in force during year y (True / False / None = unknown) for y in [H0, H1].

    Returns (path, evidence, why).  evidence = 'status_spells' (explicit exposed/unexposed/unknown spells, run7-EITC style),
    'dated_onset' (first operative year known; earlier years absent, later years in force: only the FIRST adoption is used),
    'never_certified', or 'unknown' (no usable path)."""
    years = range(H0, H1 + 1)
    if row is None:
        return {y: None for y in years}, "unknown", "no_record"
    st = row.get("status")
    if st in (None, "unknown"):
        return {y: None for y in years}, "unknown", "status_unknown"
    ivs = row.get("operative_intervals", row.get("intervals"))
    # (1) explicit status spells: annual path exactly as documented, unknown spells stay unknown
    if isinstance(ivs, list) and any(isinstance(sp, dict) and sp.get("status") in _SPELL_STATUS for sp in ivs):
        path = {y: None for y in years}
        for sp in ivs:
            if not isinstance(sp, dict) or sp.get("status") not in _SPELL_STATUS:
                continue
            a = parse_year(sp.get("start", sp.get("start_period", sp.get("start_year"))))
            b = parse_year(sp.get("end", sp.get("end_period", sp.get("end_year"))))
            if a is None:
                continue
            val = _SPELL_STATUS[sp["status"]]
            for y in range(max(a, H0), min(b if b is not None else H1, H1) + 1):
                if path[y] is None or val is None:
                    path[y] = path[y] if val is None else val
                elif path[y] != val:
                    path[y] = None                                           # conflicting overlap: unknown
        return path, "status_spells", ""
    if row.get("history_uncertain"):
        return {y: None for y in years}, "unknown", "history_uncertain_without_status_spells"
    if st in ("always_treated", "baseline_treated") or row.get("left_censored") is True:
        return {y: True for y in years}, "dated_onset", "left_censored_flag"
    # (2) dated onset: first operative year (c64_metadata.active order, plus the dated-start fields of the autopilot histories)
    onset = None
    for k in ("operative_period", "best_evidence_start_year", "effective_year"):
        if type(row.get(k)) is int:
            onset = row[k]
            break
    if onset is None and isinstance(ivs, list) and ivs:
        starts = [parse_year(sp.get("start_period", sp.get("start_year", sp.get("start")))) for sp in ivs if isinstance(sp, dict)]
        if starts and all(x is not None for x in starts):
            onset = min(starts)
    if onset is None and type(row.get("start_period")) is int:
        onset = row["start_period"]
    unk = {y for y in (row.get("unknown_years") or []) if type(y) is int}
    if onset is None:
        if st == "never_treated" and row.get("start_period") is None:
            return {y: (None if y in unk else False) for y in years}, "never_certified", ""
        return {y: None for y in years}, "unknown", "no_onset_not_never"
    path = {y: (False if y < onset else True) for y in years}
    for y in unk:
        if y in path and y < onset:
            path[y] = None
    return path, "dated_onset", ""


def window_for(card, treat):
    cfg = card.get("treatment", {})
    for src, sc in (("treatment.coverage", treat.get("coverage")), ("card.treatment.source_window", cfg.get("source_window"))):
        if isinstance(sc, dict) and type(sc.get("start_year")) is int and type(sc.get("end_year")) is int:
            return sc["start_year"], sc["end_year"], src
    w = cfg.get("window")
    if isinstance(w, list) and len(w) == 2 and all(type(x) is int for x in w):
        return w[0], w[1], "card.treatment.window"
    y = card.get("outcome", {}).get("years")
    if isinstance(y, list) and len(y) == 2 and all(type(x) is int for x in y):
        return y[0], y[1], "card.outcome.years"
    return None, None, "none"


# ---------------------------------------------------------------------------------------------
# covariates and adjacency
# ---------------------------------------------------------------------------------------------
def load_covariates():
    p = note("covariates_state_year_v2", COV / "state_year_v2.csv")
    cov = {}
    with open(p, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            cov[(r["state"], int(r["year"]))] = r
    pa = note("state_adjacency_v2", COV / "state_adjacency_v2.csv")
    nb = collections.defaultdict(set)
    with open(pa, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["land_border_rule_v1"] == "1":
                nb[r["state_a"]].add(r["state_b"])
                nb[r["state_b"]].add(r["state_a"])
    return cov, nb


# ---------------------------------------------------------------------------------------------
# one design
# ---------------------------------------------------------------------------------------------
def code_design(did, pkg, run_id, how, set_label, cov, nbrs, live_row):
    card = jload(pkg / "CARD.json")
    treat = jload(pkg / "TREATMENT.json")
    assert card.get("design_id") == did, (did, card.get("design_id"))
    ch_path = pkg / "CERTIFIED_HISTORY.json"
    ch = jload(ch_path) if ch_path.exists() else None
    H0, H1, wsrc = window_for(card, treat)
    if H0 is None:
        return None, {"design_id": did, "problem": "no_window"}
    cfg = card.get("treatment", {})
    universe = cfg.get("universe")
    usrc = "card.treatment.universe"
    if not (isinstance(universe, list) and universe):
        universe = card.get("outcome", {}).get("units") or []
        usrc = "card.outcome.units (fallback)"
    universe = [u for u in universe if u in C.USPS_TO_FIPS and u != "DC"] or list(universe)
    conflict = set(cfg.get("excluded_conflict_states") or []) | set(treat.get("excluded_conflict_states") or [])
    primary_excl = set(treat.get("excluded_primary_units") or []) | set((ch or {}).get("excluded_primary_units") or [])
    excluded = set(cfg.get("excluded_units") or []) | conflict
    records = {}
    dup = set()
    for rec in treat.get("records", []):
        if isinstance(rec, dict) and isinstance(rec.get("unit"), str):
            if rec["unit"] in records:
                dup.add(rec["unit"])
            records[rec["unit"]] = rec
    # annual paths for every unit that has a record or is in the universe
    units = sorted(set(universe) | set(records))
    paths, evid, whys = {}, {}, {}
    for u in units:
        paths[u], evid[u], whys[u] = annual_path(None if u in dup else records.get(u), H0, H1)

    def first_onset(path):
        for y in range(H0 + 1, H1 + 1):
            if path[y - 1] is False and path[y] is True:
                return y
        return None

    kind, onset = {}, {}
    for u in units:
        p = paths[u]
        if all(v is None for v in p.values()):
            kind[u], onset[u] = "unknown", None
        elif p[H0] is True:
            kind[u], onset[u] = "left_censored", None
        else:
            o = first_onset(p)
            if o is not None:
                kind[u], onset[u] = "adopter", o
            elif any(v is True for v in p.values()):
                kind[u], onset[u] = "adopter_onset_in_unknown_gap", None
            else:
                kind[u], onset[u] = "never", None
    always = {u for u in units if kind[u] == "left_censored"}
    inU = {u for u in universe if u not in excluded and u not in always and kind[u] != "unknown"}
    onsets = [onset[u] for u in units if kind[u] == "adopter"]
    t0 = min(onsets) if onsets else None
    if always:
        t0 = H0 if t0 is None else min(t0, H0)

    def in_force_jan1(u, t):
        return paths[u][t - 1] if u in paths else None

    timeline_rows = []
    for u in units:
        if u not in universe:
            cls = "not_in_universe"
        elif u in conflict:
            cls = "excluded_conflict"
        elif u in primary_excl:
            cls = "excluded_primary"
        elif u in excluded:
            cls = "excluded_other"
        elif kind[u] == "left_censored":
            cls = "always_treated_left_censored"
        elif kind[u] == "unknown":
            cls = "unknown_history"
        else:
            cls = "in_U"
        rec = records.get(u) or {}
        nk = sum(1 for v in paths[u].values() if v is not None)
        timeline_rows.append([did, u, int(u in inU), cls, kind[u], onset[u], evid[u], whys[u], nk, rec.get("status"), rec.get("start_period")])
    rows = []
    for u in units:
        if kind[u] in ("unknown", "left_censored"):
            continue
        for t in range(H0 + 1, H1 + 1):
            prev, cur = paths[u][t - 1], paths[u][t]
            if prev is True:
                break
            if prev is None or cur is None:
                continue
            adopt = int(cur is True)
            uk = [in_force_jan1(v, t) for v in inU]
            u_known = [x for x in uk if x is not None]
            fcum = sum(u_known) / len(u_known) if u_known else None
            nn = [in_force_jan1(v, t) for v in nbrs.get(u, ()) if v in paths]
            nn_known = [x for x in nn if x is not None]
            nbs = sum(nn_known) / len(nn_known) if nn_known else None
            cv = cov.get((u, t), {})
            rows.append([did, u, t, adopt, int(u in inU), int(t > t0) if t0 is not None else 0, onset[u] if adopt else "",
                         "" if fcum is None else f"{fcum:.6f}", len(u_known), "" if nbs is None else f"{nbs:.6f}", len(nn_known), len(nbrs.get(u, ())),
                         cv.get("dem_unified_jan1", ""), cv.get("rep_unified_jan1", ""), cv.get("party_cat_jan1", ""),
                         cv.get("party_cat_session", ""), cv.get("citizen_ideology", ""), cv.get("ln_pcpi_real_2024usd", "")])
            if adopt:
                break
    # summary
    def hz(sel, clock):
        idx = {"jan1": 14, "session": 15}[clock]
        g = collections.defaultdict(lambda: [0, 0])
        for r in rows:
            if not sel(r):
                continue
            g[r[idx] or "unknown"][0] += 1
            g[r[idx] or "unknown"][1] += r[3]
        d, rr = g.get("D_unified", [0, 0]), g.get("R_unified", [0, 0])
        hd = d[1] / d[0] if d[0] else None
        hr = rr[1] / rr[0] if rr[0] else None
        a = abs(hd - hr) if (hd is not None and hr is not None) else None
        return {"risk_years": {k: v[0] for k, v in sorted(g.items())}, "events": {k: v[1] for k, v in sorted(g.items())},
                "h_D": hd, "h_R": hr, "A_raw": a, "both_groups_ge20_risk_years": bool(d[0] >= 20 and rr[0] >= 20)}
    prim = lambda r: r[4] == 1 and r[5] == 1
    summ = {
        "design_id": did, "set": set_label, "package": C.rel(pkg), "package_kind": how, "run_id": run_id,
        "policy_family": card.get("policy_family"), "live_status": (live_row or {}).get("status"),
        "live_source_group": (live_row or {}).get("source_group"),
        "history_schema": (ch or {}).get("schema") or (ch or {}).get("schema_version"),
        "history_file_audit_passed": ((ch or {}).get("audit") or {}).get("passed"),
        "treatment_audit_passed": (treat.get("audit") or {}).get("passed"),
        "window": [H0, H1], "window_source": wsrc, "universe_source": usrc, "universe_n": len(universe),
        "excluded_conflict_n": len(conflict & set(universe)), "excluded_primary_n": len(primary_excl & set(universe)),
        "excluded_n": len(excluded & set(universe)), "always_treated_left_censored_n": len(always & set(universe)),
        "unknown_history_n": sum(1 for u in universe if kind[u] == "unknown"),
        "U_n": len(inU), "adopters_in_window_U": sum(1 for u in inU if kind[u] == "adopter"),
        "adopters_in_window_all_known": sum(1 for u in units if kind[u] == "adopter"),
        "evidence_classes": dict(collections.Counter(evid[u] for u in universe)),
        "t0_first_adoption_year": t0, "rows_total": len(rows), "rows_in_U": sum(1 for r in rows if r[4]),
        "rows_primary": sum(1 for r in rows if prim(r)), "events_primary": sum(r[3] for r in rows if prim(r)),
        "events_all_rows": sum(r[3] for r in rows),
        "primary_jan1": hz(prim, "jan1"),
        "variant_session_clock": hz(prim, "session"),
        "variant_full_window": hz(lambda r: r[4] == 1, "jan1"),
        "variant_all_known_states": hz(lambda r: r[5] == 1, "jan1"),
    }
    return (timeline_rows, rows, summ), None


# ---------------------------------------------------------------------------------------------
def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OUT), help="default data/l3_meta/party_coding (new versioned files only)")
    OUT = Path(ap.parse_args().out_dir)
    cov, nbrs = load_covariates()
    v2 = jload(note("l2_set_v2", ROOT / "data/l3_meta/l2_set_v2.json"))
    twelve = [d["design_id"] for d in v2["designs"] if d.get("certified_full_state_year_history_file")]
    assert len(twelve) == 12, twelve
    live_path = note("l2_set_live", ROOT / "data/l3_meta/l2_set_live.json")
    live = jload(live_path)
    live_by = {d["design_id"]: d for d in live["designs"]}
    pkgs = first_certified_packages()

    plan = []                                    # (design_id, package, run_id, how, set_label)
    for did in twelve:
        pkg, run_id, how = pkgs[did]
        plan.append((did, pkg, run_id, how, "twelve_v2"))
    seen = set(twelve)
    for d in live["designs"]:
        did = d["design_id"]
        if did in seen:
            continue
        pkg = run_id = how = None
        if did in pkgs:
            pkg, run_id, how = pkgs[did]
        elif d["source_group"] == "autopilot_estimate":
            pkg = ROOT / Path(d["source"]["path"]).parent; run_id = d["source"].get("run_id"); how = "autopilot_estimate_package"
        elif d["source_group"] == "autopilot_park":
            pkg = park_package(did, d["source"]["path"]); run_id = None; how = "latest_park_attempt_package"
        if pkg is None or not (Path(pkg) / "TREATMENT.json").exists() or not (Path(pkg) / "CERTIFIED_HISTORY.json").exists():
            continue
        plan.append((did, Path(pkg), run_id, how, "extension_live_" + live["as_of"][:10].replace("-", "")))
        seen.add(did)

    all_tl, all_rows, summaries, problems = [], [], [], []
    for did, pkg, run_id, how, label in plan:
        res, prob = code_design(did, pkg, run_id, how, label, cov, nbrs, live_by.get(did))
        if res is None:
            problems.append(prob)
            continue
        tl_rows, rows, summ = res
        if summ["treatment_audit_passed"] is not True:
            summ["note"] = "treatment audit not passed: kept in the summary, rows withheld from the risk file"
            summaries.append(summ)
            continue
        all_tl += tl_rows; all_rows += rows; summaries.append(summ)

    hdr_tl = ["design_id", "state", "in_U", "class", "kind", "onset_operative_year", "evidence", "why", "known_years_n", "record_status", "record_start_period"]
    hdr_rows = ["design_id", "state", "year", "adopt", "in_U", "after_first_adoption", "onset_operative_year_if_adopter",
                "fcum_jan1_U", "fcum_known_n", "nb_in_force_share_jan1", "nb_known_n", "nb_n",
                "dem_unified_jan1", "rep_unified_jan1", "party_cat_jan1", "party_cat_session", "citizen_ideology", "ln_pcpi_real_2024usd"]
    outs = {}
    outs["timeline"] = C.write_new(OUT / f"design_state_timeline_{VERSION}.csv", C.csv_bytes(hdr_tl, all_tl))
    outs["risk_rows"] = C.write_new(OUT / f"risk_state_year_{VERSION}.csv", C.csv_bytes(hdr_rows, all_rows))
    outs["summary_json"] = C.write_new(OUT / f"design_summary_{VERSION}.json", C.json_bytes(summaries))
    flat_hdr = ["design_id", "set", "live_status", "policy_family", "window", "U_n", "adopters_in_window_U", "t0_first_adoption_year",
                "rows_primary", "events_primary", "D_risk_years", "R_risk_years", "divided_risk_years", "h_D", "h_R", "A_raw",
                "both_groups_ge20", "A_raw_session_clock", "A_raw_full_window", "A_raw_all_known_states", "treatment_audit_passed"]
    flat = []
    for s in summaries:
        p = s["primary_jan1"]
        flat.append([s["design_id"], s["set"], s["live_status"], s["policy_family"], f"{s['window'][0]}-{s['window'][1]}", s["U_n"],
                     s["adopters_in_window_U"], s["t0_first_adoption_year"], s["rows_primary"], s["events_primary"],
                     p["risk_years"].get("D_unified", 0), p["risk_years"].get("R_unified", 0), p["risk_years"].get("divided", 0),
                     None if p["h_D"] is None else f"{p['h_D']:.6f}", None if p["h_R"] is None else f"{p['h_R']:.6f}",
                     None if p["A_raw"] is None else f"{p['A_raw']:.6f}", int(p["both_groups_ge20_risk_years"]),
                     None if s["variant_session_clock"]["A_raw"] is None else f"{s['variant_session_clock']['A_raw']:.6f}",
                     None if s["variant_full_window"]["A_raw"] is None else f"{s['variant_full_window']['A_raw']:.6f}",
                     None if s["variant_all_known_states"]["A_raw"] is None else f"{s['variant_all_known_states']['A_raw']:.6f}",
                     s["treatment_audit_passed"]])
    outs["summary_csv"] = C.write_new(OUT / f"design_summary_{VERSION}.csv", C.csv_bytes(flat_hdr, flat))

    tw = [s for s in summaries if s["set"] == "twelve_v2"]
    ext = [s for s in summaries if s["set"] != "twelve_v2"]
    def block(ss):
        ok = [s for s in ss if s["primary_jan1"]["both_groups_ge20_risk_years"]]
        return {"designs": len(ss), "with_rows": sum(1 for s in ss if s["rows_total"] > 0),
                "families": len({s["policy_family"] for s in ss}),
                "both_groups_ge20_risk_years": len(ok), "families_among_ge20": len({s["policy_family"] for s in ok}),
                "events_primary_total": sum(s["events_primary"] for s in ss),
                "estimated_in_live_set": sum(1 for s in ss if s["live_status"] == "estimated"),
                "estimated_and_ge20": sum(1 for s in ok if s["live_status"] == "estimated"),
                "families_estimated_and_ge20": len({s["policy_family"] for s in ok if s["live_status"] == "estimated"}),
                "events_primary_estimated_and_ge20": sum(s["events_primary"] for s in ok if s["live_status"] == "estimated")}
    receipt = {
        "schema": "l3_inputs_b_party_coding_receipt_v1", "version": VERSION, "built_utc": C.utc_now(),
        "builder": {"path": C.rel(__file__), "sha256": C.sha256_file(Path(__file__)), "python": sys.version.split()[0]},
        "rules": {
            "risk_set": "state-years (s,t), t in [H0+1,H1], policy not in force on 1 January of t (operative onset >= t or never in window); rows end at first adoption; unknown cells dropped",
            "onset": "first interval start; else operative_period / best_evidence_start_year / effective_year / start_period (c64_metadata.active order); onset <= H0 = left-censored (no rows)",
            "U": "universe - excluded_units - excluded_conflict_states - left-censored (N1); rows carry in_U; primary hazards use in_U = 1",
            "primary": "Jan-1 party clock (covariate v2 party_cat_jan1), opportunity from t > t0 (first adoption year), states in U; variants: session clock, full window, all known states",
            "A_i": "|h_D_unified - h_R_unified|, hazard = adoptions / risk-years of the group; a group needs >= 20 risk-years (Q4); standardisation across the cohort is left to the run",
            "no_L2_values": "only CARD/TREATMENT/CERTIFIED_HISTORY metadata and registry paths are read; no RESULT, estimate, SE, noise or forecast value",
        },
        "inputs": INPUTS, "outputs": outs, "problems": problems,
        "counts": {"designs_planned": len(plan), "designs_in_summary": len(summaries), "risk_rows": len(all_rows),
                   "timeline_rows": len(all_tl), "twelve": block(tw), "extension": block(ext)},
        "limitations": [
            "Party values are the covariate file v2 values (Klarner session-year chambers <=2011, collectors' reconciled files 2011-2024); no certified 1 January reading; 2025 rows have no party and are dropped from group counts.",
            "Each history is read from the first certified run's package (ERPO: the original v12 clock; the later timing-corrected run is not coded).",
            "The party-coded archive is a single-pass mechanical build, not independently coded; 'party_risk_certified' is left to the hub.",
            "Ever-reversed or support-excluded states with known onset are kept as rows with in_U = 0; unknown or conflict states have no rows.",
        ],
    }
    outs["receipt"] = C.write_new(OUT / f"RECEIPT_party_coding_{VERSION}.json", C.json_bytes(receipt))
    for k, v in outs.items():
        print(k, v["status"], v["path"], v["bytes"])
    print(json.dumps(receipt["counts"], indent=1))
    print("problems", problems)


if __name__ == "__main__":
    main()
