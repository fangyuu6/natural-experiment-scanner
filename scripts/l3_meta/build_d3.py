#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version D3-V1 (rule id EX-1): D3 "When a policy lowers its target outcome, does a close substitute rise?"

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_D_QUESTIONS.md (entry D3) and PREREG_AMEND_20261015_D3_V1.md (build rules); both, and the
entry D3, were hashed in PREREG_AMEND_20261015_D_HASH.txt before any D3 statistic was computed.  Also EX-1 and engine E1 of the addendum 0.5.

A pair is (design, target series A, total series T) with A a part of T (fixed table).  The substitute is C = T - A.  The L2 engine is
linear in the outcome, so on a common estimating structure the ATT of C is t - a and its placebo ATTs (the same 999 label assignments)
are t*_b - a*_b.  Per pair: s = -sgn(a) c / sd_c, placebo s*_b = -sgn(a*_b) c*_b / sd_c, Z = (s - mu)/tau.  E1 over pairs only when
n >= 8 pairs in >= 5 policy families.  D3-V1 uses the L2 outputs that exist and runs NO L2 engine: a pair whose total has no L2 estimate
on the target's structure is listed in NEEDS_ENGINE_RUNS.csv and not computed.

READ-ONLY on every L2 file (RESULT.json and panel.csv of the designs named by the live set); nothing under scripts/l2_pipeline or
data/l2_pipeline is opened for writing.  No network.  Only output: data/l3_meta/d3_v1/{RESULTS_D3_V1.json, RUN_LOG.jsonl,
l2_set_live_used.json, pair_inputs_d3_v1.json, NEEDS_ENGINE_RUNS.csv}.  Every statistic is exploratory: no "significant" label, no
multiplicity adjustment, no licensed sentence.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_d3.py [--dry-run] [--out-dir DIR] [--verify-records]      (numpy for E1 only)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import statistics
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import build_n1 as N1  # noqa: E402  (E1 engine only; importing reads no data file)

RULE_ID = "EX-1"
QUESTION = "D3"
VERSION = "D3-V1"
DATA = ROOT / "data"
LIVE = DATA / "l3_meta" / "l2_set_live.json"
CATALOG = ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json"
AMEND_Q = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D_QUESTIONS.md"
AMEND_B = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D3_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D_HASH.txt"
OUT_DIR = DATA / "l3_meta" / "d3_v1"
OUT_NAME = "RESULTS_D3_V1.json"

# fixed by the entry D3 and the build file
B_BOOT = 9999
SEED_E1 = [20261011, 33]                # E1 stream: k = 30 + 3
FLOOR_N, FLOOR_F = 8, 5                 # engine floor (entry D3)
PLAN_N, PLAN_F = 20, 20                 # printed beside n, gate nothing (EX-1 section 1)
MIN_DRAWS = 900                         # 90% of the 999 requested draws (engine rule)
TOL = 1e-9                              # build file 4.0
LIVE_FIELDS = ("design_id", "status", "gate", "domain", "policy_family", "source_group")
REGISTERED_GROUPS = ("registry_terminal", "autopilot_estimate")
ARCHIVE_GROUPS = ("prototype", "manual_batch")
GATE_ORDER = ["CELL_SETS_DIFFER", "DRAWS_NOT_ALIGNED", "SETTINGS_DIFFER", "DENOMINATORS_DIFFER", "NOT_NESTED", "OTHER", "SUPPORT",
              "NEEDS_COMPLEMENT_ESTIMATE"]

# build file section 2: (pair id, target A, total T, substitute C)
PAIR_TABLE = [
    ("P1", "wonder_firearm_homicide_deaths", "wonder_homicide_deaths", "homicide by other means"),
    ("P2", "wonder_firearm_suicide_deaths", "wonder_suicide_deaths", "suicide by other means"),
    ("P3", "fars_motor_vehicle_occupant_fatality_rate", "fars_all_road_fatality_rate", "road deaths of non-occupants (pedestrians, cyclists, others)"),
    ("P4", "fars_drinking_driver_crash_fatality_rate", "fars_all_road_fatality_rate", "road deaths in crashes with no reported drinking driver"),
    ("P5", "wonder_opioid_overdose_deaths", "wonder_drug_overdose_deaths", "drug overdose deaths without opioid involvement"),
    ("P6", "fars_motorcyclist_fatality_rate", "fars_all_road_fatality_rate", "road deaths other than motorcyclists"),
    ("P7", "fars_pedalcyclist_fatality_rate_all_ages", "fars_all_road_fatality_rate", "road deaths other than cyclists"),
]


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    """Path relative to the repo root; relative inputs are returned as written (no symlink resolution of data/)."""
    p = Path(p)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
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


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, numpy scalars/arrays -> python, tuples -> lists."""
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def agree(x, y, tol=TOL):
    """Build file 4.0: |x - y| <= tol * max(1, |x|, |y|)."""
    return abs(x - y) <= tol * max(1.0, abs(x), abs(y))


def sgn(v):
    return (v > 0) - (v < 0)


def mean(v):
    return math.fsum(v) / len(v)


# ---------------------------------------------------------------------------------------------
# hash record and spec entries
# ---------------------------------------------------------------------------------------------
def entry_text(text, tag):
    """The text of entry `tag` (D1, D2, D3): from its '## Dk. ' heading line to the line before the next '## ' line."""
    m = re.search(r"^## " + re.escape(tag) + r"\. .*?(?=^## |\Z)", text, re.M | re.S)
    return m.group(0) if m else None


def entry_sha256(text, tag):
    t = entry_text(text, tag)
    return hashlib.sha256(t.encode("utf-8")).hexdigest() if t is not None else None


def parse_hash_record(path=HASH_REC):
    """files: {path: sha}, entries: {tag: sha}, listed: {path: sha} (frozen files and inputs), t_amend_utc."""
    text = Path(path).read_text(encoding="utf-8")
    files = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^file:\s*(\S+)\s*$", line)
        if m and i + 1 < len(lines):
            s = re.match(r"^sha256:\s*([0-9a-f]{64})\s*$", lines[i + 1])
            if s:
                files[m.group(1)] = s.group(1)
    entries = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(D[123])\s+([0-9a-f]{64})\s*$", text, re.M)}
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    t_amend = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    return {"files": files, "entries": entries, "listed": listed, "t_amend_utc": t_amend.group(1) if t_amend else None}


def verify_records(rec=None, q_path=AMEND_Q, b_path=AMEND_B):
    """Problems (list of strings) between the hash record and the files on disk; empty list = consistent."""
    rec = rec or parse_hash_record()
    problems = []
    for path in (q_path, b_path):
        want = rec["files"].get(rel(path))
        got = sha256_file(path)
        if want is None:
            problems.append(f"{rel(path)} not in the hash record")
        elif want != got:
            problems.append(f"{rel(path)} differs from the hash record")
    q_text = Path(q_path).read_text(encoding="utf-8")
    for tag in ("D1", "D2", "D3"):
        want, got = rec["entries"].get(tag), entry_sha256(q_text, tag)
        if want is None or got is None:
            problems.append(f"entry {tag} missing in record or file")
        elif want != got:
            problems.append(f"entry {tag} differs from the hash record")
    return problems


# ---------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------
def load_live(path=LIVE):
    """Roster D from the live set.  Only LIVE_FIELDS, the source file name and hash and the outcome ids/statuses are kept:
    no estimate, SE, interval, noise or p field is carried."""
    live = read_json(path)
    designs = []
    for d in live["designs"]:
        row = {k: d.get(k) for k in LIVE_FIELDS}
        src = d.get("source") or {}
        row["source_path"], row["source_sha256"] = src.get("path"), src.get("sha256")
        row["primary_ids"] = [p.get("outcome_id") for p in (d.get("primary_outcomes") or [])]
        row["secondary"] = [(s.get("outcome_id"), s.get("status")) for s in (d.get("secondary_outcomes") or [])]
        designs.append(row)
    meta = {"as_of": live["as_of"], "counts": {k: live["counts"][k] for k in ("processed", "estimated", "not_evaluable")},
            "outside_D": {k: len(v) for k, v in live["excluded_not_counted"].items()}}
    return designs, meta


def load_catalog(path=CATALOG):
    """series id -> {'units': ..., 'artifact': 'path;path'} (units and artifact paths only)."""
    cat = read_json(path)["series"]
    out = {}
    for sid, s in cat.items():
        paths = []
        if isinstance(s.get("artifact"), dict) and s["artifact"].get("path"):
            paths.append(s["artifact"]["path"])
        for e in s.get("exports") or []:
            if isinstance(e, dict) and e.get("raw_path"):
                paths.append(e["raw_path"])
        if not paths:
            paths = list(s.get("paths") or [])
        out[sid] = {"units": s.get("units"), "artifact": ";".join(paths)}
    return out


def read_panel(path):
    """(unit, year) -> (outcome_value or None, population_denominator or None).  Only these columns are used."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["unit"], int(row["year"]))
            vals = []
            for col in ("outcome_value", "population_denominator"):
                raw = row.get(col)
                try:
                    vals.append(float(raw) if raw not in (None, "") else None)
                except ValueError:
                    vals.append(None)
            out[key] = tuple(vals)
    return out


def panel_path(folder, name, series):
    """build file 1.3: panels/<name>/panel.csv, else secondary_panels/<series>/panel.csv."""
    folder = Path(folder)
    for cand in (folder / "panels" / name / "panel.csv", folder / "secondary_panels" / series / "panel.csv"):
        if cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------------------------
# outcomes, candidate pairs, tests (build file sections 3 and 4)
# ---------------------------------------------------------------------------------------------
def outcomes_of(result, primary_ids):
    """series id -> (entry name, entry) for the registered outcomes that are ESTIMATED and hold placebo draws (3.1)."""
    out = OrderedDict()
    for name, ent in ((result or {}).get("registered_results") or {}).items():
        if name == "primary":
            sid = primary_ids[0] if primary_ids else None
        elif name.startswith("secondary_"):
            sid = name[len("secondary_"):]
        else:
            continue
        if sid is None or not isinstance(ent, dict) or ent.get("status") != "ESTIMATED":
            continue
        if not isinstance((ent.get("noise_floor") or {}).get("permutation_draws"), list):
            continue
        out[sid] = (name, ent)
    return out


def feasible_indices(nf):
    feas = (nf or {}).get("feasibility") or {}
    requested = feas.get("requested_draws")
    if not isinstance(requested, int):
        return None
    bad = {x.get("draw_index") for x in feas.get("infeasible_draws") or []}
    return [i for i in range(requested) if i not in bad]


def cell_key(ent):
    return [(c.get("g"), c.get("t"), c.get("base_period"), c.get("n_treated"), c.get("n_control"), c.get("post"))
            for c in ent["result"]["group_time"]]


def structure_failures(A, T):
    """Tests 2 to 4 of build file 3.2; returns (code, reason) list in test order."""
    f = []
    ia, it = A.get("inventory") or {}, T.get("inventory") or {}
    if (cell_key(A) != cell_key(T) or ia.get("cells") != it.get("cells") or ia.get("construction_cells") != it.get("construction_cells")
            or A["result"]["overall"].get("n_cells") != T["result"]["overall"].get("n_cells")):
        f.append(("CELL_SETS_DIFFER", "group_time lists or inventory cells differ"))
    if A["result"].get("settings") != T["result"].get("settings"):
        f.append(("SETTINGS_DIFFER", "result.settings differ"))
    fa, ft = feasible_indices(A["noise_floor"]), feasible_indices(T["noise_floor"])
    na, nt = len(A["noise_floor"]["permutation_draws"]), len(T["noise_floor"]["permutation_draws"])
    if fa is None or ft is None or fa != ft or na != len(fa) or nt != len(ft) or len(fa) < MIN_DRAWS:
        f.append(("DRAWS_NOT_ALIGNED", "feasible draw indices differ, counts disagree or fewer than %d draws" % MIN_DRAWS))
    return f


def draw_stats(x, draws):
    """Build file 4.3."""
    n = len(draws)
    cutoff = sorted(abs(v) for v in draws)[math.ceil(.95 * n) - 1]
    return {"sd": statistics.stdev(draws), "cutoff": cutoff, "p2": (1 + sum(abs(v) >= abs(x) for v in draws)) / (n + 1),
            "beyond": abs(x) > cutoff}


def pearson(x, y):
    mx, my = mean(x), mean(y)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = math.fsum((a - mx) ** 2 for a in x), math.fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def pair_numbers(A, T):
    """Build file 4.1 to 4.5 on a pair that passed the structure tests.  Returns (numbers, problems)."""
    problems = []
    a, t = float(A["result"]["overall"]["att"]), float(T["result"]["overall"]["att"])
    da = [float(v) for v in A["noise_floor"]["permutation_draws"]]
    dt_ = [float(v) for v in T["noise_floor"]["permutation_draws"]]
    if not all(math.isfinite(v) for v in [a, t] + da + dt_):
        return None, [("OTHER", "non-finite value")]
    c = t - a
    # 4.1 cross-checks: the engine's overall ATT is the n_treated-weighted mean of its post cells
    for label, ent, val in (("A", A, a), ("T", T, t)):
        post = [(c_["n_treated"], c_["att"]) for c_ in ent["result"]["group_time"] if c_["post"]]
        mass = sum(w for w, _ in post)
        if not mass or not agree(math.fsum(w * v for w, v in post) / mass, val):
            problems.append(("OTHER", f"overall.att of {label} disagrees with its post cells"))
    postA = [(c_["n_treated"], c_["att"]) for c_ in A["result"]["group_time"] if c_["post"]]
    postT = [(c_["n_treated"], c_["att"]) for c_ in T["result"]["group_time"] if c_["post"]]
    mass = sum(w for w, _ in postA)
    c_cells = math.fsum(w * (vt - va) for (w, va), (_, vt) in zip(postA, postT)) / mass if mass else float("nan")
    if not agree(c_cells, c):
        problems.append(("OTHER", "c = t - a disagrees with the difference of the post cells"))
    n = len(da)
    dc = [y - x for x, y in zip(da, dt_)]
    st_a, st_t, st_c = draw_stats(a, da), draw_stats(t, dt_), draw_stats(c, dc)
    # 4.3: the stored noise statistics of A and T must equal their recomputation
    for label, ent, st, val in (("A", A, st_a, a), ("T", T, st_t, t)):
        nf = ent["noise_floor"]
        for key, mine in (("sample_sd", st["sd"]), ("absolute_cutoff", st["cutoff"]), ("p_two_sided", st["p2"])):
            if not finite(nf.get(key)) or not agree(float(nf[key]), mine):
                problems.append(("OTHER", f"stored {key} of {label} disagrees with its recomputation"))
    sd_c = st_c["sd"]
    out = {"n_draws": n, "a": a, "t": t, "c": c, "sd_a": st_a["sd"], "sd_t": st_t["sd"], "sd_c": sd_c,
           "cutoff_a": st_a["cutoff"], "cutoff_t": st_t["cutoff"], "cutoff_c": st_c["cutoff"],
           "p2_a": st_a["p2"], "p2_t": st_t["p2"], "p2_c": st_c["p2"],
           "beyond_a": st_a["beyond"], "beyond_t": st_t["beyond"], "beyond_c": st_c["beyond"],
           "corr_a_t_draws": pearson(da, dt_)}
    if sd_c == 0:
        problems.append(("SUPPORT", "sd_c = 0"))
        return out, problems
    s = -sgn(a) * c / sd_c
    s_star = [-sgn(x) * y / sd_c for x, y in zip(da, dc)]
    mu, tau = mean(s_star), statistics.stdev(s_star)
    out.update({"s": s, "mu": mu, "tau": tau})
    if tau == 0:
        problems.append(("SUPPORT", "tau = 0"))
        return out, problems
    out["Z"] = (s - mu) / tau
    out["p_sub"] = (1 + sum(v >= s for v in s_star)) / (n + 1)
    out["omega"] = -c / a if a != 0 else None
    out["net_ratio"] = t / a if a != 0 else None
    out["opposite"] = bool(a != 0 and c != 0 and sgn(c) == -sgn(a))
    out["signal"] = bool(out["beyond_a"] and out["opposite"] and out["beyond_c"])
    return out, problems


def analyze_pair(design, pair, A_name, A, T_outcome, panels, catalog):
    """One candidate pair -> record.  T_outcome = (name, entry) or None; panels = callable(name_a, series_a, name_t, series_t)
    returning (panel_a, panel_t) dicts or raising OSError/ValueError/KeyError."""
    pid, a_id, t_id, label = pair
    rec = {"design_id": design["design_id"], "policy_family": design["policy_family"] or design["design_id"], "pair": pid,
           "target_series": a_id, "total_series": t_id, "substitute": label, "target_entry": A_name, "reasons": [], "code": None}
    if T_outcome is None:
        rec["code"] = "NEEDS_COMPLEMENT_ESTIMATE"
        rec["reasons"] = ["NEEDS_COMPLEMENT_ESTIMATE: total series is not an ESTIMATED registered outcome"]
        return rec
    T_name, T = T_outcome
    rec["total_entry"] = T_name
    fails = structure_failures(A, T)
    if not fails:
        try:
            pa, pt = panels(A_name, a_id, T_name, t_id)
        except (OSError, ValueError, KeyError) as exc:
            fails.append(("OTHER", f"PANEL_UNREADABLE: {type(exc).__name__}"))
        else:
            bad_den = [k for k in pa.keys() & pt.keys() if pa[k][1] is not None and pt[k][1] is not None and not agree(pa[k][1], pt[k][1])]
            if bad_den:
                fails.append(("DENOMINATORS_DIFFER", f"{len(bad_den)} keys with different population_denominator"))
            ua, ut = (catalog.get(a_id) or {}).get("units"), (catalog.get(t_id) or {}).get("units")
            both = [k for k in pa.keys() & pt.keys() if pa[k][0] is not None and pt[k][0] is not None]
            viol = [k for k in both if pt[k][0] < pa[k][0] - 1e-9 * max(1.0, abs(pa[k][0]))]
            if ua is None or ut is None or ua != ut:
                fails.append(("NOT_NESTED", f"catalog units differ or missing ({ua!r}, {ut!r})"))
            elif viol:
                fails.append(("NOT_NESTED", f"outcome_value of the total is below the target at {len(viol)} keys"))
            rec["panel_keys_checked"] = len(both)
    if not fails:
        nums, probs = pair_numbers(A, T)
        fails.extend(probs)
        if nums is not None:
            rec["numbers"] = nums
    # first failing reason in the order of build file 3.2 is the code; all failing reasons are listed
    order = ["NEEDS_COMPLEMENT_ESTIMATE", "CELL_SETS_DIFFER", "SETTINGS_DIFFER", "DRAWS_NOT_ALIGNED", "DENOMINATORS_DIFFER", "NOT_NESTED",
             "OTHER", "SUPPORT"]
    fails.sort(key=lambda x: order.index(x[0]))
    rec["reasons"] = [f"{a}: {b}" for a, b in fails]
    rec["code"] = fails[0][0] if fails else "EVAL"
    return rec


def design_code(pair_codes):
    """Build file 3.4."""
    if not pair_codes:
        return "NO_PAIR"
    if "EVAL" in pair_codes:
        return "EVAL"
    for code in GATE_ORDER:
        if code in pair_codes:
            return code
    return "OTHER"


def needs_rows(design, pair_rec, A, T_outcome, result, catalog, folder):
    """Build file 6: one engine run needed for a pair coded NEEDS_COMPLEMENT_ESTIMATE or CELL_SETS_DIFFER."""
    inv = A.get("inventory") or {}
    t_id = pair_rec["total_series"]
    if T_outcome is not None:
        state = "registered on another mask: cells=%s" % ((T_outcome[1].get("inventory") or {}).get("cells"))
    else:
        other = ((result or {}).get("registered_results") or {}).get("secondary_" + t_id)
        state = "registered but not estimated (status %s)" % other.get("status") if isinstance(other, dict) else "not registered"
    return OrderedDict([
        ("design_id", design["design_id"]), ("policy_family", pair_rec["policy_family"]), ("live_set_run_folder", rel(folder)),
        ("pair", pair_rec["pair"]), ("target_entry", pair_rec["target_entry"]), ("target_series", pair_rec["target_series"]),
        ("total_series", t_id), ("total_catalog_artifact", (catalog.get(t_id) or {}).get("artifact")),
        ("mask_cells", inv.get("cells")), ("mask_construction_cells", inv.get("construction_cells")),
        ("mask_metadata_sha256", inv.get("metadata_sha256")), ("state_of_total", state),
        ("call", "estimate %s on the registered declaration of %s over exactly its %s mask cells, 999 fixed draws on the same unit list; then c = t - a (D3-V2)"
                 % (t_id, pair_rec["target_entry"], inv.get("cells")))])


def analyze_design(design, result_loader, panel_loader, catalog):
    """Returns (design_row, pair_records, needs_rows)."""
    row = {"design_id": design["design_id"], "family": design["policy_family"] or design["design_id"], "domain": design["domain"],
           "live_status": design["status"], "gate": design["gate"], "source_group": design["source_group"], "pairs": [], "reasons": []}
    if design["status"] != "estimated":
        row["code"], row["reasons"] = "STOP_PRE_RUN", [f"STOP_PRE_RUN: {design['gate']}"]
        return row, [], []
    if design["source_group"] in ARCHIVE_GROUPS:
        row["code"], row["reasons"] = "FIELD_MISSING", ["FIELD_MISSING: archive result carries no registered results with placebo draws"]
        return row, [], []
    if design["source_group"] not in REGISTERED_GROUPS:
        row["code"], row["reasons"] = "OTHER", [f"OTHER: source group {design['source_group']}"]
        return row, [], []
    try:
        result, sha_ok = result_loader(design)
    except (OSError, ValueError, KeyError) as exc:
        row["code"], row["reasons"] = "OTHER", [f"OTHER: RESULT.json unreadable ({type(exc).__name__})"]
        return row, [], []
    if not sha_ok:
        row["code"], row["reasons"] = "OTHER", ["OTHER: RESULT.json sha256 differs from the live set"]
        return row, [], []
    outs = outcomes_of(result, design["primary_ids"])
    folder = Path(design["source_path"]).parent
    pair_recs, needs = [], []
    for pair in PAIR_TABLE:
        if pair[1] not in outs:
            continue
        a_name, A = outs[pair[1]]
        T_outcome = outs.get(pair[2])
        rec = analyze_pair(design, pair, a_name, A, T_outcome,
                           lambda an, asid, tn, tsid, _f=folder: panel_loader(_f, an, asid, tn, tsid), catalog)
        pair_recs.append(rec)
        if rec["code"] in ("NEEDS_COMPLEMENT_ESTIMATE", "CELL_SETS_DIFFER"):
            needs.append(needs_rows(design, rec, A, T_outcome, result, catalog, folder))
    row["pairs"] = [(r["pair"], r["code"]) for r in pair_recs]
    row["code"] = design_code([r["code"] for r in pair_recs])
    row["reasons"] = [f"{r['pair']}:{r['code']}" for r in pair_recs] if pair_recs else ["NO_PAIR: no registered ESTIMATED outcome is a target of the pair table"]
    return row, pair_recs, needs


# ---------------------------------------------------------------------------------------------
# E1 and the printed summary
# ---------------------------------------------------------------------------------------------
def pattern_counts(eval_pairs):
    beyond_a = [p for p in eval_pairs if p["numbers"]["beyond_a"]]
    opp = [p for p in beyond_a if p["numbers"]["opposite"]]
    both = [p for p in opp if p["numbers"]["beyond_c"]]
    om = [p["numbers"]["omega"] for p in beyond_a if p["numbers"]["omega"] is not None]
    return {"pairs_with_target_beyond_line": len(beyond_a), "of_those_substitute_opposite": len(opp),
            "of_those_substitute_also_beyond_its_line": len(both), "median_offset_share_of_pairs_with_target_beyond_line":
                statistics.median(om) if om else None,
            "pairs_with_substitution_signal": sum(1 for p in eval_pairs if p["numbers"]["signal"])}


def e1_summary(eval_pairs):
    n = len(eval_pairs)
    fams = [p["policy_family"] for p in eval_pairs]
    F = len(set(fams))
    base = {"n": n, "F": F, "engine_floor": {"n": FLOOR_N, "F": FLOOR_F}, "seed": SEED_E1, "B": B_BOOT, "blocks": "policy_family"}
    if n < FLOOR_N or F < FLOOR_F:
        return dict(base, run=False, reason=f"engine floor not met (n = {n} < {FLOOR_N} or F = {F} < {FLOOR_F}); per-pair table only; "
                                            "no interval, no p-value, no theta_D3")
    ids, F = N1.family_ids(fams)
    res = N1.e1_core([p["numbers"]["Z"] for p in eval_pairs], ids, F, B_BOOT, np.random.default_rng(SEED_E1))
    return dict(base, run=True, result=res)


def code_table(rows, key="code"):
    return dict(sorted(Counter(r[key] for r in rows).items()))


def write_needs_csv(path, needs):
    cols = ["design_id", "policy_family", "live_set_run_folder", "pair", "target_entry", "target_series", "total_series",
            "total_catalog_artifact", "mask_cells", "mask_construction_cells", "mask_metadata_sha256", "state_of_total", "call"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in needs:
            w.writerow({k: r.get(k) for k in cols})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--verify-records", action="store_true", help="check the hash record against the files and exit")
    ap.add_argument("--live-set-changed-ok", action="store_true", help="accept a live set whose hash differs from the record")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    rec = parse_hash_record()
    problems = verify_records(rec)
    if args.verify_records:
        print(json.dumps({"problems": problems, "t_amend_utc": rec["t_amend_utc"], "entries": rec["entries"]}, indent=1))
        return 1 if problems else 0
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    live_sha = sha256_file(LIVE)
    live_same = rec["listed"].get(rel(LIVE)) == live_sha
    if not live_same and not args.live_set_changed_ok:
        sys.exit("live set differs from the hash record: not run (use --live-set-changed-ok for a new roster)")
    for p in (CATALOG, ROOT / "scripts/l3_meta/build_n1.py"):
        if rec["listed"].get(rel(p)) != sha256_file(p):
            sys.exit(f"{rel(p)} differs from the hash record: not run")

    roster, live_meta = load_live()
    catalog = load_catalog()

    def result_loader(design):
        p = ROOT / design["source_path"]
        return read_json(p), sha256_file(p) == design["source_sha256"]

    def panel_loader(folder, a_name, a_id, t_name, t_id):
        folder = ROOT / folder
        pa, pt = panel_path(folder, a_name, a_id), panel_path(folder, t_name, t_id)
        if pa is None or pt is None:
            raise OSError("panel file not found")
        return read_panel(pa), read_panel(pt)

    rows, pairs, needs = [], [], []
    for d in roster:
        r, ps, nd = analyze_design(d, result_loader, panel_loader, catalog)
        rows.append(r)
        pairs.extend(ps)
        needs.extend(nd)

    D_n = len(roster)
    eval_pairs = [p for p in pairs if p["code"] == "EVAL"]
    n = len(eval_pairs)
    F = len({p["policy_family"] for p in eval_pairs})
    e1 = e1_summary(eval_pairs)
    runs_needed = sorted({(r["design_id"], r["total_series"], r["mask_cells"]) for r in needs})
    me = Path(__file__).resolve()
    stamp = {"rule_id": RULE_ID, "question": QUESTION, "version": VERSION, "cutoff_utc": live_meta["as_of"], "run_utc": utc_now(),
             "live_set_sha256": live_sha, "live_set_matches_record": live_same, "catalog_sha256": sha256_file(CATALOG),
             "code_sha256": sha256_file(me), "code": rel(me), "e1_engine_code_sha256": sha256_file(ROOT / "scripts/l3_meta/build_n1.py"),
             "question_file_sha256": sha256_file(AMEND_Q), "build_file_sha256": sha256_file(AMEND_B),
             "entry_D3_sha256": rec["entries"]["D3"], "t_amend_utc": rec["t_amend_utc"],
             "D": D_n, "estimated": live_meta["counts"]["estimated"], "not_evaluable": live_meta["counts"]["not_evaluable"],
             "outside_D": live_meta["outside_D"], "n": n, "F": F, "python": sys.version.split()[0], "numpy": np.__version__,
             "seeds": {"E1": SEED_E1}, "B": B_BOOT, "no_significance_label": True}
    result = OrderedDict([
        ("D", D_n), ("n_pairs_eval", n), ("F", F),
        ("coverage_codes_over_D", code_table(rows)), ("candidate_pairs", len(pairs)), ("pair_codes", code_table(pairs)),
        ("minimums_printed_beside_n", {"engine_floor": {"n": FLOOR_N, "F": FLOOR_F, "n_ge": n >= FLOOR_N, "F_ge": F >= FLOOR_F},
                                       "planning_minimum": {"n": PLAN_N, "F": PLAN_F, "n_ge": n >= PLAN_N, "F_ge": F >= PLAN_F},
                                       "planning_detectable_mean_Z_2.8_over_sqrt_n": 2.8 / math.sqrt(n) if n else None,
                                       "note": "EX-1: printed beside n, gate nothing (the engine floor only decides whether E1 runs)"}),
        ("E1", e1), ("pattern_counts", pattern_counts(eval_pairs) if eval_pairs else None),
        ("engine_runs_needed", {"count": len(runs_needed), "rows": len(needs),
                                "by_series": dict(sorted(Counter(r["total_series"] for r in needs).items())),
                                "note": "D3-V1 runs no engine; NEEDS_ENGINE_RUNS.csv lists each run (build file 6)"}),
    ])
    checks = {"eval_pairs_corr_of_target_and_total_draws": {f"{p['design_id']}:{p['pair']}": p["numbers"]["corr_a_t_draws"] for p in eval_pairs},
              "designs_whose_result_sha256_differs_from_live_set": [r["design_id"] for r in rows if any("sha256 differs" in x for x in r["reasons"])]}
    payload = clean(OrderedDict([
        ("schema_version", "l3-meta-d3-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {rel(p): {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in (LIVE, CATALOG, AMEND_Q, AMEND_B, HASH_REC)}),
        ("question", OrderedDict([("D3", result)])), ("checks", checks), ("designs", rows), ("pairs", pairs),
        ("audit", {"l2_files_written": 0, "engine_calls": 0, "downloads": 0, "network": False})]))

    pair_inputs = None
    if eval_pairs:
        pair_inputs = {"note": "numbers read from the L2 RESULT.json of each EVAL pair (build file 7.2)", "pairs": []}
        by_id = {d["design_id"]: d for d in roster}
        for p in eval_pairs:
            d = by_id[p["design_id"]]
            res = read_json(ROOT / d["source_path"])
            ents = {k: v for k, v in (res.get("registered_results") or {}).items()}
            A, T = ents[p["target_entry"]], ents[p["total_entry"]]
            feas = feasible_indices(A["noise_floor"])
            pair_inputs["pairs"].append({
                "design_id": p["design_id"], "pair": p["pair"], "source": d["source_path"], "source_sha256": d["source_sha256"],
                "feasible_draw_indices": feas,
                "target": {"entry": p["target_entry"], "series": p["target_series"], "overall_att": A["result"]["overall"]["att"],
                           "group_time": [{k: c[k] for k in ("g", "t", "base_period", "n_treated", "n_control", "post", "att")} for c in A["result"]["group_time"]],
                           "permutation_draws": A["noise_floor"]["permutation_draws"]},
                "total": {"entry": p["total_entry"], "series": p["total_series"], "overall_att": T["result"]["overall"]["att"],
                          "group_time": [{k: c[k] for k in ("g", "t", "base_period", "n_treated", "n_control", "post", "att")} for c in T["result"]["group_time"]],
                          "permutation_draws": T["noise_floor"]["permutation_draws"]}})

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():  # a version folder is written once
            sys.exit(f"{rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(LIVE, copy)
        assert sha256_file(copy) == live_sha
        if pair_inputs is not None:
            (out_dir / "pair_inputs_d3_v1.json").write_text(json.dumps(clean(pair_inputs), ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        write_needs_csv(out_dir / "NEEDS_ENGINE_RUNS.csv", needs)
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_d3.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code_sha256"], "live_set_sha256": live_sha, "question_file_sha256": stamp["question_file_sha256"],
                                 "build_file_sha256": stamp["build_file_sha256"], "results_file": rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": stamp, "D3": payload["question"]["D3"], "designs_with_pairs": [r for r in payload["designs"] if r["pairs"]],
             "eval_pairs": [p for p in payload["pairs"] if p["code"] == "EVAL"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
