#!/usr/bin/env python3
"""L3 package B, input 4: N1 geography sets (U_i, T_i by cohort, R_i) for every live design that carries a treatment file.

N1 (PREREG_L3_META_ADDENDUM_V2.md): U_i = CARD.treatment.universe - excluded_units - excluded_conflict_states - always-treated;
T_i = states in cohorts with supported post cells in the frozen primary; R_i = U_i minus every state with the policy in force in the
last panel year.  EVAL if |U_i| >= 20, |T_i| >= 3, |R_i| >= 3 and the treatment audit passed.  Metadata only: nothing from RESULT.json is
read (the S3 SUPPORT.json cell inventory, which holds no outcome value, defines the supported cohorts), so STOP_PRE_RUN, S3-support-stopped and
estimated designs are all covered by the same rule.

L2 writes these sets into the export block (`geography`) only when every legal status is resolved; here they are rebuilt from the archive
fields that the N1 text lists (CARD, TREATMENT, SUPPORT) with the same status logic as scripts/l2_pipeline/c64_metadata.py (read-only), applied
to the panel years [outcome.years[0], outcome.years[1]]: always-treated = in force at the first panel year; R = not in force at the last.

Output (new files under data/l3_meta/n1_sets/):  n1_sets_v1.json (lists), n1_sets_v1.csv (one row per design), RECEIPT_n1_sets_v1.json.
Run:  python3 -I -B scripts/l3_meta/build_n1_sets.py [--out-dir DIR]     (standard library only)
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402
from build_party_histories import annual_path, first_certified_packages, park_package  # noqa: E402

ROOT = C.ROOT
L2 = ROOT / "data/l2_pipeline"
VERSION = "v1"
INPUTS = {}


def note(role, path, **extra):
    p = Path(path)
    INPUTS[role] = {"path": C.rel(p), "bytes": p.stat().st_size, "sha256": C.sha256_file(p), **extra}
    return p


def jload(p):
    return json.loads(Path(p).read_text())


def weighted_lower_median(values):
    v = sorted(values)
    return v[(len(v) - 1) // 2] if v else None


def package_for(d, pkgs):
    did, sg = d["design_id"], d["source_group"]
    if did in pkgs:
        return pkgs[did][0], pkgs[did][1], "registry_first_certified_run_or_pre_run_stop"
    if sg == "autopilot_estimate":
        return ROOT / Path(d["source"]["path"]).parent, d["source"].get("run_id"), "autopilot_estimate_package"
    if sg == "autopilot_park":
        return park_package(did, d["source"]["path"]), None, "latest_park_attempt_package"
    if sg == "early_s3_stop":
        return ROOT / Path(d["source"]["path"]).parent, None, "early_s3_stop_card_dir"
    return None, None, "no_package(archive design)"


def primary_variant(support):
    vs = support.get("variants") or []
    for v in vs:
        if v.get("variant") == "primary":
            return v
    return vs[0] if vs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/n1_sets"))
    OUT = Path(ap.parse_args().out_dir)
    live = jload(note("l2_set_live", ROOT / "data/l3_meta/l2_set_live.json"))
    pkgs = first_certified_packages()
    note("terminal_runs", L2 / "registry/TERMINAL_RUNS.json")

    designs, rows, skipped = [], [], []
    for d in live["designs"]:
        pkg, run_id, how = package_for(d, pkgs)
        if pkg is None or not all((Path(pkg) / f).exists() for f in ("CARD.json", "TREATMENT.json", "SUPPORT.json")):
            skipped.append({"design_id": d["design_id"], "source_group": d["source_group"], "status": d["status"], "reason": how if pkg is None else "missing CARD/TREATMENT/SUPPORT"})
            continue
        pkg = Path(pkg)
        card, treat, support = jload(pkg / "CARD.json"), jload(pkg / "TREATMENT.json"), jload(pkg / "SUPPORT.json")
        did = d["design_id"]
        assert card.get("design_id") == did, (did, card.get("design_id"))
        cfg = card.get("treatment", {})
        years = card.get("outcome", {}).get("years")
        if not (isinstance(years, list) and len(years) == 2 and all(type(y) is int for y in years)):
            skipped.append({"design_id": did, "source_group": d["source_group"], "status": d["status"], "reason": "no exact annual panel years"})
            continue
        P0, P1 = years
        universe = cfg.get("universe")
        usrc = "card.treatment.universe"
        if not (isinstance(universe, list) and universe):
            universe, usrc = card.get("outcome", {}).get("units") or [], "card.outcome.units (fallback)"
        conflict = set(cfg.get("excluded_conflict_states") or []) | set(treat.get("excluded_conflict_states") or [])
        excluded = set(cfg.get("excluded_units") or []) | conflict
        records = {r["unit"]: r for r in treat.get("records", []) if isinstance(r, dict) and isinstance(r.get("unit"), str)}
        paths, evid = {}, {}
        for u in set(universe) | set(records):
            paths[u], evid[u], _ = annual_path(records.get(u), P0, P1)
        always = sorted(u for u in universe if u not in excluded and paths[u][P0] is True)
        U = sorted(u for u in universe if u not in excluded and u not in always)
        unk_first = sorted(u for u in U if paths[u][P0] is None)
        unk_last = sorted(u for u in U if paths[u][P1] is None)
        in_force_last = sorted(u for u in U if paths[u][P1] is True)
        R_known = sorted(u for u in U if paths[u][P1] is False)
        R_resolved = not unk_last
        # T from the S3 cell inventory of the primary variant (metadata only)
        pv = primary_variant(support)
        T, cohorts, support_passed = [], {}, None
        if pv:
            support_passed = bool(pv.get("passed"))
            retained = set(pv.get("retained_treated") or [])
            ug = {}
            for cell in pv.get("cell_inventory") or []:
                if cell.get("post"):
                    for u in cell.get("treated") or []:
                        ug.setdefault(u, cell["g"])
            T = sorted(u for u in ug if (not retained or u in retained) and u in U)
            for u in T:
                cohorts.setdefault(ug[u], []).append(u)
        # operative adoption years of T (first year with the policy in force within the panel)
        def onset(u):
            p = paths[u]
            for y in range(P0 + 1, P1 + 1):
                if p[y - 1] is False and p[y] is True:
                    return y
            return None
        T_onset = {u: onset(u) for u in T}
        med = weighted_lower_median([y for y in T_onset.values() if y is not None])
        audit_ok = (treat.get("audit") or {}).get("passed") is True
        n_dc = int("DC" in U)
        eval_spec = bool(audit_ok and len(U) >= 20 and len(T) >= 3 and R_resolved and len(R_known) >= 3)
        rec = {"design_id": did, "policy_family": card.get("policy_family"), "live_status": d["status"], "gate": d.get("gate"),
               "source_group": d["source_group"], "package": C.rel(pkg), "package_kind": how, "run_id": run_id,
               "panel_years": [P0, P1], "universe_source": usrc, "universe_n": len(universe), "excluded_n": len(set(universe) & excluded),
               "always_treated": always, "U": U, "unknown_baseline_in_U": unk_first, "unknown_last_year_in_U": unk_last,
               "in_force_last_panel_year": in_force_last, "R": R_known if R_resolved else None, "R_known_part": R_known, "R_resolved": R_resolved,
               "T": T, "T_cohorts": {str(g): us for g, us in sorted(cohorts.items())}, "T_operative_onset_year": T_onset,
               "weighted_lower_median_adoption_year_T": med, "treatment_audit_passed": audit_ok, "support_passed": support_passed,
               "support_disposition": (pv or {}).get("disposition"), "U_includes_DC": bool(n_dc), "eval_n1_spec": eval_spec,
               "evidence_classes": dict(collections.Counter(evid[u] for u in universe))}
        designs.append(rec)

    hdr = ["design_id", "policy_family", "live_status", "gate", "source_group", "panel_years", "U_n", "T_n", "T_cohorts_n", "R_n_known_part", "R_resolved",
           "always_treated_n", "unknown_baseline_n", "unknown_last_year_n", "treatment_audit_passed", "support_passed", "U_includes_DC", "eval_n1_spec",
           "weighted_lower_median_adoption_year_T"]
    for r in designs:
        rows.append([r["design_id"], r["policy_family"], r["live_status"], r["gate"], r["source_group"], f"{r['panel_years'][0]}-{r['panel_years'][1]}",
                     len(r["U"]), len(r["T"]), len(r["T_cohorts"]), len(r["R_known_part"]), int(r["R_resolved"]), len(r["always_treated"]),
                     len(r["unknown_baseline_in_U"]), len(r["unknown_last_year_in_U"]), int(r["treatment_audit_passed"]),
                     "" if r["support_passed"] is None else int(r["support_passed"]), int(r["U_includes_DC"]), int(r["eval_n1_spec"]),
                     r["weighted_lower_median_adoption_year_T"]])
    outs = {}
    outs["sets_json"] = C.write_new(OUT / f"n1_sets_{VERSION}.json", C.json_bytes({"designs": designs, "skipped": skipped}))
    outs["summary_csv"] = C.write_new(OUT / f"n1_sets_{VERSION}.csv", C.csv_bytes(hdr, rows))
    ev = [r for r in designs if r["eval_n1_spec"]]
    def blk(rs):
        return {"designs": len(rs), "families": len({r["policy_family"] for r in rs})}
    counts = {"live_designs": len(live["designs"]), "with_sets": len(designs), "skipped": len(skipped),
              "skipped_by_group": dict(collections.Counter(s["source_group"] for s in skipped)),
              "eval_n1_spec": blk(ev), "eval_estimated": blk([r for r in ev if r["live_status"] == "estimated"]),
              "eval_not_evaluable": blk([r for r in ev if r["live_status"] != "estimated"]),
              "with_sets_estimated": blk([r for r in designs if r["live_status"] == "estimated"]),
              "R_unresolved": sum(1 for r in designs if not r["R_resolved"]),
              "support_failed_but_eval": sum(1 for r in ev if r["support_passed"] is False),
              "U_includes_DC_among_eval": sum(1 for r in ev if r["U_includes_DC"]),
              "fail_reasons": {"audit": sum(1 for r in designs if not r["treatment_audit_passed"]),
                               "U_lt20": sum(1 for r in designs if len(r["U"]) < 20), "T_lt3": sum(1 for r in designs if len(r["T"]) < 3),
                               "R_unresolved": sum(1 for r in designs if not r["R_resolved"]),
                               "R_lt3_known_part": sum(1 for r in designs if len(r["R_known_part"]) < 3)}}
    receipt = {"schema": "l3_inputs_b_n1_sets_receipt_v1", "version": VERSION, "built_utc": C.utc_now(),
               "builder": {"path": C.rel(__file__), "sha256": C.sha256_file(Path(__file__)), "python": sys.version.split()[0]},
               "live_set_as_of": live.get("as_of"),
               "rules": {"U": "universe - excluded_units - excluded_conflict_states - always-treated (in force at the first panel year)",
                         "T": "units with a post cell in the S3 cell inventory of the primary variant, restricted to retained_treated and U (metadata only; no RESULT.json value read)",
                         "R": "U units known NOT in force at the last panel year; unresolved (R = null) if any U unit has unknown status at the last panel year",
                         "status_logic": "annual_path() of build_party_histories.py (dated onset / explicit exposure spells), same intent as c64_metadata.active()",
                         "median": "weighted lower median of the operative onset years of T (every state weight 1 = cohort-size weighted); the run version fixes the convention before any result",
                         "eval_n1_spec": "treatment audit passed, |U| >= 20, |T| >= 3, R resolved and |R| >= 3"},
               "inputs": INPUTS, "outputs": outs, "counts": counts,
               "limitations": ["Archive designs (13 prototypes, 8 hand batches) have no CARD/TREATMENT/SUPPORT and are listed as skipped.",
                               "Cohort label g is the engine's registered cohort (first fully exposed year); T_operative_onset_year is the state's operative year. The run version names which one is the 'adoption year'.",
                               "DC has no party or ideology value in the covariate file; designs whose U contains DC need a rule for it.",
                               "S3-support-stopped and pre-run-stopped designs are treated as EVAL when the audit passed (N1 text); support_passed is reported beside."]}
    outs["receipt"] = C.write_new(OUT / f"RECEIPT_n1_sets_{VERSION}.json", C.json_bytes(receipt))
    for k, v in outs.items():
        print(k, v["status"], v["path"], v["bytes"])
    print(json.dumps(counts, indent=1))


if __name__ == "__main__":
    main()
