#!/usr/bin/env python3
"""L3 meta, EXPLORATORY (rule id EX-1): labelled L3 read of the frozen clean-preperiod SD, version S_PRE_L3_V1, for Q2-V2.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2.md (hash and T_amend in PREREG_AMEND_20261016_Q2_V2_HASH.txt), written before this
code existed and before any value below was computed.

What it does (amendment section 2):
  * rule P (pipeline package): s_pre = sample SD (ddof 1) of panels/primary/panel.csv `outcome_value` over the clean pre unit-years of
    SUPPORT.json variant `primary` (analysis_mask pairs whose unit is never treated or whose period is before the unit's cohort).
    It is run on the 7 designs that export l3_export_v2.s_pre as a VALIDATION GATE: n must be reproduced exactly and s_pre within 1e-9
    (relative) for every design whose primary comparison is never_treated; not_yet_treated designs are 'not applicable'.
  * rule A (archive prototype): the same quantity from the frozen panel and treatment files of the two strict-matched estimated
    prototypes (adult-use cannabis, castle doctrine), after the structural checks of section 2.4.
Output (new folder, written once): S_PRE_L3_V1.json, RUN_LOG.jsonl.  Nothing under scripts/l2_pipeline or data/l2_pipeline is written;
the L2 packages and files are opened read-only.  No network.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_s_pre_l3.py [--dry-run] [--out-dir DIR]     (standard library only)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import mmap
import re
import statistics
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

RULE_ID = "EX-1"
VERSION = "S_PRE_L3_V1"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261016_Q2_V2_HASH.txt"
V3 = ROOT / "data/l3_meta/ex1_v3/RESULTS_EX1_V3.json"
OUT_DIR = ROOT / "data/l3_meta/s_pre_l3_v1"
OUT_NAME = "S_PRE_L3_V1.json"
TOL_REL = 1e-9

WORK = "data/l2_pipeline/autopilot/c72/work/"
EXPORTERS = OrderedDict([  # design id -> package folder under WORK (the first certified run of each design that exports s_pre)
    ("b1010_handheld_ban_all", "v6"), ("b1010_permitless_carry", "v5"), ("b1010_primary_seatbelt", "v5"),
    ("b1010b_child_restraint_booster_age5", "v6"), ("b1010b_pdmp_mandatory_query", "v7"),
    ("b1010b_zero_tolerance_under21", "v8"), ("r1011_speed_limit_65_rural_interstate_1987", "v2")])

CANNABIS = {
    "design_id": "prototype:adult_use_cannabis",
    "panel": "data/did_samples/adult_use_cannabis/panel.csv",
    "treatment": "data/did_samples/adult_use_cannabis/treatment_table_normalized.csv",
    "result": "scripts/did_samples/results/cases/adult_use_cannabis.json",
    "expected": {"window": [2000, 2019], "excluded": ["CO"], "units": 50, "unit_years": 1000, "exposed": 41, "partial": 9, "clean": 959, "s1": 968, "treated": 10, "controls": 40},
}
CASTLE = {
    "design_id": "prototype:castle_doctrine",
    "panel": "data/did_samples/castle_doctrine/panel.csv",
    "treatment": "data/did_samples/castle_doctrine/treatment_table.csv",
    "result": "scripts/did_samples/results/castle_doctrine.json",
    "manifest": "scripts/did_samples/results/freeze_manifest.json",
    "expected": {"window": [2000, 2010], "units": 50, "unit_years": 550, "treated": 21, "controls": 29, "clean": 455, "s1": 476},
}


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_csv(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_json(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    return obj


def parse_hash_record(path=AMEND_HASH):
    text = Path(path).read_text(encoding="utf-8")
    sha = re.search(r"^sha256:\s*([0-9a-f]{64})", text, re.M)
    t_amend = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    return {"sha256": sha.group(1) if sha else None, "t_amend_utc": t_amend.group(1) if t_amend else None, "listed": listed}


# ---------------------------------------------------------------------------------------------
# pure rules (unit-tested in test_build_s_pre_l3.py)
# ---------------------------------------------------------------------------------------------
def sample_sd(values):
    """Sample SD (n - 1), the definition of L2's `observed_fields` (statistics.stdev)."""
    values = list(values)
    if len(values) < 2:
        return None
    return statistics.stdev(values)


def rule_p_keys(mask, cells):
    """Clean pre unit-years of a pipeline package (amendment 2.2): mask pairs whose unit is in no cell's `treated` list, or whose period
    is earlier than the unit's cohort.  Returns (sorted keys, cohort by treated unit)."""
    cohort = {}
    for c in cells:
        for u in c["treated"]:
            if u in cohort and cohort[u] != c["cohort"]:
                raise ValueError(f"unit {u} has two cohorts: {cohort[u]} and {c['cohort']}")
            cohort[u] = c["cohort"]
    keys = sorted((u, int(t)) for u, t in mask if u not in cohort or int(t) < cohort[u])
    return keys, cohort


def rel_diff(a, b):
    return abs(a - b) / abs(b) if b else (0.0 if a == b else float("inf"))


def validation_verdict(rows, tol=TOL_REL):
    """Gate of amendment 2.5.  rows: dicts with `applicable`, `n_equal`, `rel_diff`.  Gate passes when at least one design is applicable
    and every applicable design reproduces n exactly and s_pre within `tol`."""
    app = [r for r in rows if r["applicable"]]
    ok = [r for r in app if r["n_equal"] and r["rel_diff"] is not None and r["rel_diff"] <= tol]
    return {"applicable_n": len(app), "passed_n": len(ok), "not_applicable_n": len(rows) - len(app), "tolerance_rel": tol,
            "gate_passed": bool(app) and len(ok) == len(app)}


def clean_pre_cannabis(rows, window, excluded_units):
    """Rule A, cannabis (amendment 2.3): retained units, registered window, law not in force at all (law_share_of_year == 0)."""
    window = set(window)
    retained = [r for r in rows if r["unit"] not in excluded_units and int(r["period"]) in window]
    clean = [r for r in retained if float(r["law_share_of_year"] or 0) == 0]
    return retained, clean


def clean_pre_castle(rows, window, adoption_year):
    """Rule A, castle doctrine (amendment 2.3): registered window, a treated state before the calendar year of its effective date, every
    year of a control state.  adoption_year: state -> calendar year of the effective date (None for a control)."""
    window = set(window)
    inwin = [r for r in rows if int(r["year"]) in window]
    clean = [r for r in inwin if adoption_year.get(r["state_abbr"]) is None or int(r["year"]) < adoption_year[r["state_abbr"]]]
    return inwin, clean


# ---------------------------------------------------------------------------------------------
# rule P on a pipeline package, and the L2 export it is validated against
# ---------------------------------------------------------------------------------------------
def read_panel_values(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["unit"], int(r["period"]))] = float(r["outcome_value"])
    return out


def exported_values(export_path):
    """s_pre and s_pre_n as exported by L2 (every numeric occurrence in the file must agree)."""
    with open(export_path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            vals = {m.group(1) for m in re.finditer(rb'"s_pre":\s*(-?[0-9][0-9.eE+\-]*)', mm)}
            ns = {m.group(1) for m in re.finditer(rb'"s_pre_n":\s*([0-9]+)', mm)}
        finally:
            mm.close()
    if len(vals) != 1 or len(ns) != 1:
        raise ValueError(f"export {rel(export_path)}: s_pre values {sorted(vals)}, s_pre_n values {sorted(ns)}")
    return float(next(iter(vals))), int(next(iter(ns)))


def rule_p(design_id, folder, v3_by):
    pkg = ROOT / WORK / design_id / folder
    support = read_json(pkg / "SUPPORT.json")
    var = support["variants"][0]
    row = OrderedDict([("design_id", design_id), ("package", rel(pkg)), ("variant", var.get("variant")), ("comparison_group", var.get("comparison_group"))])
    exp_sd, exp_n = exported_values(pkg / "L3_EXPORT_DATA.json")
    v3_sd = (v3_by.get(design_id) or {}).get("s_pre")
    row.update(n_export=exp_n, sd_export_file=exp_sd, sd_export_v3_table=v3_sd,
               export_file_equals_v3_table=bool(finite(v3_sd) and exp_sd == v3_sd))
    applicable = var.get("variant") == "primary" and var.get("comparison_group") == "never_treated"
    row["applicable"] = applicable
    keys, cohort = rule_p_keys(var["analysis_mask"], var["cells"])
    panel = read_panel_values(pkg / "panels/primary/panel.csv")
    missing = [k for k in keys if k not in panel]
    if missing:
        raise ValueError(f"{design_id}: {len(missing)} mask keys missing from the panel, first {missing[:3]}")
    vals = [panel[k] for k in keys]
    if not all(finite(v) for v in vals):
        raise ValueError(f"{design_id}: non-finite outcome in the clean set")
    sd = sample_sd(vals)
    read = dict(n_L3=len(vals), sd_L3=sd, treated_units=len(cohort), mask_unit_years=len(var["analysis_mask"]), rel_diff=rel_diff(sd, exp_sd), n_equal=len(vals) == exp_n)
    if applicable:
        row.update(read)
    else:
        # amendment 2.2/2.5: not applicable, so nothing enters the gate; the same arithmetic is shown for information only
        row.update(reason="primary comparison is not never_treated: not applicable, not in the gate; the L2 export is used unchanged",
                   n_L3=None, sd_L3=None, rel_diff=None, n_equal=None,
                   informational_not_gating={k: read[k] for k in ("n_L3", "sd_L3", "rel_diff", "n_equal")})
    return row


# ---------------------------------------------------------------------------------------------
# rule A on the two prototypes
# ---------------------------------------------------------------------------------------------
def check(name, ok, **detail):
    return OrderedDict([("check", name), ("passed", bool(ok))] + list(detail.items()))


def read_cannabis(cfg=CANNABIS):
    panel_p, treat_p, res_p = (ROOT / cfg[k] for k in ("panel", "treatment", "result"))
    res = read_json(res_p)
    est = res["primary_estimates"]["primary/y/never_treated"]
    window = list(est["calendar_years"])
    excluded = sorted({x["unit"] for x in est["unit_exclusions"]})
    reg_hash = {x["path"]: x["expected"] for x in res["source_hashes"] if isinstance(x, dict) and "path" in x}
    rows = read_csv(panel_p)
    treat = {r["abbr"]: r for r in read_csv(treat_p)}
    retained, clean = clean_pre_cannabis(rows, window, set(excluded))
    exp = cfg["expected"]
    checks = []
    for key in ("panel", "treatment"):
        got = sha256_file(ROOT / cfg[key])
        checks.append(check(f"sha256 of {cfg[key]} equals the registered hash", reg_hash.get(cfg[key]) == got, sha256=got, registered=reg_hash.get(cfg[key])))
    units = sorted({r["unit"] for r in retained})
    checks.append(check("window and units", window == list(range(exp["window"][0], exp["window"][1] + 1)) and len(units) == exp["units"] and len(retained) == exp["unit_years"] and excluded == exp["excluded"],
                        window=[window[0], window[-1]], excluded_units=excluded, units=len(units), unit_years=len(retained)))
    flag_years = sorted({int(r["period"]) for r in retained if r["in_primary_window"] == "1"})
    checks.append(check("in_primary_window equals the registered calendar years", flag_years == window and all(r["in_primary_window"] == "1" for r in retained)))
    yv = [r["y"] for r in retained]
    checks.append(check("y present and finite in the window", all(v not in ("", "NA") and finite(float(v)) for v in yv)))
    # law share against the effective-date year
    viol, treated_units = 0, set()
    for u in units:
        eff = (treat.get(u) or {}).get("effective_date") or ""
        eyear = int(eff[:4]) if re.match(r"\d{4}-", eff) else None
        if eyear is not None and eyear <= window[-1]:
            treated_units.add(u)
        for r in (x for x in retained if x["unit"] == u):
            t, share = int(r["period"]), float(r["law_share_of_year"] or 0)
            want_exposed = eyear is not None and t >= eyear
            if want_exposed != (share > 0):
                viol += 1
    checks.append(check("law_share_of_year is 0 before the effective-date year and positive from it through the window end", viol == 0, violations=viol))
    exposed = [r for r in retained if float(r["law_share_of_year"] or 0) > 0]
    partial = [r for r in exposed if float(r["law_share_of_year"]) < 1]
    ft = lambda r: int(r["first_treated"]) if r["first_treated"] else 10 ** 9  # noqa: E731
    s1 = [r for r in retained if int(r["period"]) < ft(r)]
    counts = {"treated": len(treated_units), "controls": len(units) - len(treated_units), "exposed": len(exposed), "partial": len(partial), "clean": len(clean), "s1": len(s1)}
    checks.append(check("counts equal those of the amendment (2.3, 2.6)", all(counts[k] == exp[k] for k in ("treated", "controls", "exposed", "partial", "clean", "s1")), counts=counts))
    sd, sd1 = sample_sd([float(r["y"]) for r in clean]), sample_sd([float(r["y"]) for r in s1])
    ok = all(c["passed"] for c in checks)
    return OrderedDict([("design_id", cfg["design_id"]), ("rule", "A"), ("outcome", "y = natural log nominal personal income per capita (log points)"),
                        ("inputs", {cfg[k]: sha256_file(ROOT / cfg[k]) for k in ("panel", "treatment", "result")}),
                        ("window", [window[0], window[-1]]), ("excluded_units", excluded), ("n", len(clean)), ("s_pre_L3", sd),
                        ("sensitivity_S1", {"definition": "every unit-year earlier than first_treated (partial year kept)", "n": len(s1), "s_pre_L3": sd1}),
                        ("structural_checks", checks), ("checks_passed", ok)])


def read_castle(cfg=CASTLE):
    panel_p, treat_p, res_p, man_p = (ROOT / cfg[k] for k in ("panel", "treatment", "result", "manifest"))
    res = read_json(res_p)
    pe = res["primary_estimates"][0]
    window = list(pe["calendar_years"])
    cal_adopt = pe["calendar_adoptions"]
    manifest = read_json(man_p)["files"]
    rows = read_csv(panel_p)
    tt = {r["state_abbr"]: r for r in read_csv(treat_p)}
    adoption = {s: (int(r["adoption_year"]) if r["group"] == "treated" else None) for s, r in tt.items()}
    inwin, clean = clean_pre_castle(rows, window, adoption)
    exp = cfg["expected"]
    checks = []
    for key in ("panel", "treatment"):
        got = sha256_file(ROOT / cfg[key])
        want = manifest.get(cfg[key])
        want = want.get("sha256") if isinstance(want, dict) else want
        checks.append(check(f"sha256 of {cfg[key]} equals the freeze-manifest hash", want == got, sha256=got, registered=want))
    units = sorted({r["state_abbr"] for r in inwin})
    checks.append(check("window and units", window == list(range(exp["window"][0], exp["window"][1] + 1)) and len(units) == exp["units"] and len(inwin) == exp["unit_years"] and len(rows) == len(inwin),
                        window=[window[0], window[-1]], units=len(units), unit_years=len(inwin)))
    checks.append(check("y present and finite in the window", all(r["y"] not in ("", "NA") and finite(float(r["y"])) for r in inwin)))
    treated = sorted(s for s, y in adoption.items() if y is not None)
    bad_ft, bad_gap, bad_cal = 0, 0, 0
    last = {r["state_abbr"]: r for r in inwin if int(r["year"]) == window[-1]}
    for s in treated:
        ruleA = int(tt[s]["exposure_year_ruleA_first_full_year"])
        if last[s]["first_treated"] != str(ruleA):
            bad_ft += 1
        if ruleA - adoption[s] not in (0, 1):
            bad_gap += 1
        if cal_adopt.get(s) != ruleA:
            bad_cal += 1
    for s in units:
        if adoption.get(s) is None and (last[s]["first_treated"] != "" or cal_adopt.get(s) is not None):
            bad_ft += 1
    checks.append(check("panel first_treated equals ruleA year, ruleA minus adoption_year in {0,1}, equals the registered calendar_adoptions cohort",
                        bad_ft == 0 and bad_gap == 0 and bad_cal == 0, violations={"first_treated": bad_ft, "gap": bad_gap, "calendar_adoptions": bad_cal}))
    ft = lambda r: int(r["first_treated"]) if r["first_treated"] else 10 ** 9  # noqa: E731
    s1 = [r for r in inwin if int(r["year"]) < ft(r)]
    counts = {"treated": len(treated), "controls": len(units) - len(treated), "clean": len(clean), "s1": len(s1)}
    checks.append(check("counts equal those of the amendment (2.3, 2.6)", all(counts[k] == exp[k] for k in ("treated", "controls", "clean", "s1")), counts=counts))
    sd, sd1 = sample_sd([float(r["y"]) for r in clean]), sample_sd([float(r["y"]) for r in s1])
    ok = all(c["passed"] for c in checks)
    return OrderedDict([("design_id", cfg["design_id"]), ("rule", "A"), ("outcome", "y = natural log homicide rate per 100,000 (log points)"),
                        ("inputs", {cfg[k]: sha256_file(ROOT / cfg[k]) for k in ("panel", "treatment", "result", "manifest")}),
                        ("window", [window[0], window[-1]]), ("excluded_units", []), ("n", len(clean)), ("s_pre_L3", sd),
                        ("sensitivity_S1", {"definition": "every unit-year earlier than first_treated (partial year kept)", "n": len(s1), "s_pre_L3": sd1}),
                        ("structural_checks", checks), ("checks_passed", ok)])


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def verify_inputs(rec):
    """Every file this run reads is listed in the amendment's hash record; a difference stops the run."""
    to_check = [V3, ROOT / CANNABIS["panel"], ROOT / CANNABIS["treatment"], ROOT / CANNABIS["result"], ROOT / CASTLE["panel"], ROOT / CASTLE["treatment"],
                ROOT / CASTLE["result"], ROOT / CASTLE["manifest"]]
    for d, v in EXPORTERS.items():
        b = ROOT / WORK / d / v
        to_check += [b / "SUPPORT.json", b / "panels/primary/panel.csv", b / "L3_EXPORT_DATA.json"]
    out = {}
    for p in to_check:
        h = sha256_file(p)
        if rec["listed"].get(rel(p)) != h:
            sys.exit(f"{rel(p)} differs from the amendment record (or is not listed): not run")
        out[rel(p)] = h
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    rec = parse_hash_record()
    amend_sha = sha256_file(AMEND)
    if rec["sha256"] != amend_sha:
        sys.exit("amendment file differs from its hash record: not run")
    input_hashes = verify_inputs(rec)

    v3 = read_json(V3)
    v3_by = {d["design_id"]: d for d in v3["designs"]}
    val_rows = [rule_p(d, v, v3_by) for d, v in EXPORTERS.items()]
    verdict = validation_verdict(val_rows)
    reads = [read_cannabis(), read_castle()]
    for r in reads:
        r["gate_passed"] = verdict["gate_passed"]
        r["used_in_q2"] = bool(verdict["gate_passed"] and r["checks_passed"] and finite(r["s_pre_L3"]) and r["s_pre_L3"] > 0)
    me = Path(__file__).resolve()
    stamp = OrderedDict([("rule_id", RULE_ID), ("version", VERSION), ("run_utc", utc_now()), ("code", rel(me)), ("code_sha256", sha256_file(me)),
                         ("amendment_sha256", amend_sha), ("t_amend_utc", rec["t_amend_utc"]), ("python", sys.version.split()[0])])
    payload = clean_json(OrderedDict([
        ("schema_version", "l3-meta-s-pre-l3-v1"), ("evidence_status", "EXPLORATORY (EX-1); labelled L3 read, not an L2 export; no confirmatory claim"),
        ("stamp", stamp),
        ("definition", "sample SD (ddof 1) of the registered outcome over the clean pre-period unit-years of the primary analysis, in outcome units (addendum F2; l3_addendum.observed_fields)"),
        ("inputs", input_hashes),
        ("validation", OrderedDict([("rule", "P"), ("gate", verdict), ("designs", val_rows)])),
        ("reads", reads),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    brief = {"stamp": payload["stamp"], "validation_gate": verdict,
             "validation": [{k: r.get(k) for k in ("design_id", "comparison_group", "applicable", "n_L3", "n_export", "sd_L3", "sd_export_file", "rel_diff", "n_equal", "informational_not_gating")} for r in val_rows],
             "reads": [{k: r.get(k) for k in ("design_id", "n", "s_pre_L3", "sensitivity_S1", "checks_passed", "used_in_q2")} for r in reads]}
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():
            sys.exit(f"{rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_s_pre_l3.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code_sha256"], "amendment_sha256": amend_sha, "results_file": rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
