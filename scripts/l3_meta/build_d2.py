#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version D2-V1 (rule id EX-1): D2 "How often does a policy move an outcome it plausibly cannot affect?" (negative-control outcomes).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_D_QUESTIONS.md (entry D2; hash PREREG_AMEND_20261015_D_HASH.txt) and the dated build amendment
PREREG_AMEND_20261015_D2_V1.md (hash record PREREG_AMEND_20261015_D2_HASH.txt and supplement ..._S1.txt), hashed before any D2 number; the sweep
SWEEP-V1 and D1-V1 (PREREG_AMEND_20261015_SWEEP_V1.md, build_d1.py: the same family statistics, unchanged); EX-1 (PREREG_AMEND_20261012_EXPLORATORY.md);
engine E1 of the addendum 0.5 (scripts/l3_meta/build_n1.py).

Input: the sweep files data/l3_meta/sweep_v1/<design>.npz (through build_d1.design_statistics: for every D1-EVAL design and supported outcome family the
statistic M_if, the line q_if, beyond_if and r_if = the share of the design's common placebo draws beyond the line), the two blind codings of the 35 x 16
grid, the third reader's decisions and their reconciliation (data/l3_meta/d2_plausibility/d2_reconciled_v1.csv), the inventory CSV (policy group of a design,
registered outcome families) and the live set (policy family = the block of E1).  A cell (design i, family f) is a negative control (NC) when the final code
of its (policy group, family) pair is `no_channel` and the pair is not a coding error (a registered primary or secondary outcome of a design of the group
sits in the family).  y_if = B_if - r_if, delta_D2 = mean y_if, E1 (family-block bootstrap-t over policy families) only with at least 40 NC cells in 8
policy families; below that the cell table is the result.  Every statistic is exploratory: no "significant" label, no multiplicity adjustment, no licensed
sentence.

Reads L2 results only through the sweep files; nothing under scripts/l2_pipeline or data/l2_pipeline is opened for writing.  No network.  Only output:
data/l3_meta/d2_v1/ (each file written once).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_d2.py [--dry-run] [--out-dir DIR] [--sweep-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
import shutil
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_n1 as N1  # noqa: E402  (E1 engine only)
import build_d1 as D1  # noqa: E402  (family statistics of D1-V1, registered families, design file reader)
import build_d3 as D3  # noqa: E402  (hash record of the D questions)
import build_sweep as B  # noqa: E402  (hash record parser and file helpers)
import sweep_core as S  # noqa: E402
import reconcile_d2 as R  # noqa: E402  (coding files, header and vocabulary; standard library only)

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "D2", "D2-V1"
SEED_E1 = [20261011, 32]
SEED_NULL = [20261011, 32, 7]
B_BOOT = 9999
N_REF = 9999
FLOOR_M, FLOOR_F = 40, 8
PLAN_M, PLAN_F = 100, 20
OUT_DIR = ROOT / "data/l3_meta/d2_v1"
OUT_NAME = "RESULTS_D2_V1.json"
CSV_NAME = "NC_CELLS_D2_V1.csv"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D2_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D2_HASH.txt"
HASH_S1 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D2_HASH_S1.txt"
D_QUESTIONS = D1.D_QUESTIONS
D_HASH = D1.D_HASH
CODE_FILES = ["scripts/l3_meta/build_d2.py", "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_n1.py", "scripts/l3_meta/build_d3.py", "scripts/l3_meta/reconcile_d2.py"]
CELL_CODES = ("NC", "D2_PAIR_NOT_NC", "CODING_ERROR", "GROUP_NOT_CODED", "NC_INVALID")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# hash records
# ---------------------------------------------------------------------------------------------
def verify_d2_records():
    """Problems between the D2 hash record (amendment + supplement S1) and the files on disk.  Returns (problems, record)."""
    rec = B.parse_hash_record(HASH_REC)
    problems = []
    want = rec["files"].get(S.rel(AMEND))
    if want is None or want != S.sha256_file(AMEND):
        problems.append(f"{AMEND.name} differs from its hash record")
    if not any(n.startswith(HASH_S1.stem) for n in rec.get("supplements", [])):
        problems.append(f"supplement {HASH_S1.name} is missing")
    for path, want in rec["listed"].items():
        if path.startswith("scripts/l2_pipeline/") and path.endswith(".py"):
            continue
        p = ROOT / path
        if not p.exists() or S.sha256_file(p) != want:
            problems.append(f"{path} differs from the D2 hash record")
    return problems, rec


# ---------------------------------------------------------------------------------------------
# inventory: policy group of a design, registered outcome families
# ---------------------------------------------------------------------------------------------
def design_groups(inv_rows):
    """design_id -> policy_group (the inventory usage rows of unit_kind design carry one group per design)."""
    out = defaultdict(set)
    for r in inv_rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design":
            out[r["unit_id"]].add(r["policy_group"])
    bad = {k: sorted(v) for k, v in out.items() if len(v) != 1}
    if bad:
        raise ValueError(f"designs with more than one policy group: {bad}")
    return {k: next(iter(v)) or None for k, v in out.items()}


def registered_rows(inv_rows, table):
    """Usage rows of role primary or secondary of any design (any status) with their outcome family (build_d1.registered_families rule)."""
    out = []
    for r in inv_rows:
        if r["row_type"] != "usage" or r["unit_kind"] != "design" or r["role"] not in ("primary", "secondary"):
            continue
        fam = r["family"] or (table.get(r["outcome_key"]) or {}).get("family")
        if fam:
            out.append(dict(design_id=r["unit_id"], policy_group=r["policy_group"], outcome_key=r["outcome_key"], role=r["role"], family=fam))
    return out


def registered_maps(reg_rows):
    by_group, by_design = defaultdict(lambda: defaultdict(list)), defaultdict(set)
    for x in reg_rows:
        by_group[x["policy_group"]][x["family"]].append((x["design_id"], x["outcome_key"], x["role"]))
        by_design[x["design_id"]].add(x["family"])
    return by_group, by_design


# ---------------------------------------------------------------------------------------------
# pairs (the reconciled coding) and the variants of the negative-control set
# ---------------------------------------------------------------------------------------------
def pair_table(rows):
    out = {}
    for r in rows:
        k = (r["policy_group"], r["outcome_family"])
        if k in out:
            raise ValueError(f"duplicate pair {k}")
        out[k] = dict(final=r["final_code"], c1=r["coder1_code"], c2=r["coder2_code"], route=r["route"], third=r["third_code"], strict=(r["strict_nc"] == "1"))
    return out


VARIANTS = OrderedDict([
    ("final", lambda p: p["final"] == "no_channel"),
    ("strict", lambda p: bool(p["strict"])),
    ("coder1", lambda p: p["c1"] == "no_channel"),
    ("coder2", lambda p: p["c2"] == "no_channel"),
    ("either", lambda p: p["c1"] == "no_channel" or p["c2"] == "no_channel"),
])
EXTRA_VARIANTS = ("final_narrow_exclusion", "final_no_exclusion")
ALL_VARIANTS = tuple(VARIANTS) + EXTRA_VARIANTS


def cell_variant_flags(did, group, family, pairs, reg_by_group, reg_by_design, invalid=frozenset()):
    """Which variants of the NC set contain the cell (design, family).  Also returns the pair-level facts."""
    p = pairs.get((group, family)) if group is not None else None
    if p is None:
        return {v: False for v in ALL_VARIANTS}, None, False
    bad_pair = (group, family) in invalid
    coding_error = family in reg_by_group.get(group, {})
    narrow_error = family in reg_by_design.get(did, set())
    flags = {}
    for v, fn in VARIANTS.items():
        flags[v] = bool(fn(p)) and not coding_error and not bad_pair
    flags["final_narrow_exclusion"] = bool(VARIANTS["final"](p)) and not narrow_error and not bad_pair
    flags["final_no_exclusion"] = bool(VARIANTS["final"](p)) and not bad_pair
    return flags, p, coding_error


def cell_code(group, family, pairs, reg_by_group, invalid=frozenset()):
    if group is None or (group, family) not in pairs:
        return "GROUP_NOT_CODED"
    if (group, family) in invalid:
        return "NC_INVALID"
    nc = VARIANTS["final"](pairs[(group, family)])
    if nc and family in reg_by_group.get(group, {}):
        return "CODING_ERROR"
    return "NC" if nc else "D2_PAIR_NOT_NC"


# ---------------------------------------------------------------------------------------------
# statistics of a set of cells (pure functions of lists)
# ---------------------------------------------------------------------------------------------
def estimate(cells):
    """pi_NC, rbar, delta_D2 and the counts of a list of cells (each with B in {0,1}, r, policy_family, design_id)."""
    m = len(cells)
    out = {"m": m, "n_designs": len({c["design_id"] for c in cells}), "F": len({c["policy_family"] for c in cells})}
    if m == 0:
        return dict(out, n_beyond=0, pi_NC=None, rbar=None, delta_D2=None, expected_beyond=None)
    b = np.array([c["B"] for c in cells], float)
    r = np.array([c["r"] for c in cells], float)
    return dict(out, n_beyond=int(b.sum()), pi_NC=float(b.mean()), rbar=float(r.mean()), delta_D2=float((b - r).mean()), expected_beyond=float(r.sum()))


def e1_decision(cells):
    """E1 on y_if = B_if - r_if with blocks = policy families; only with m >= 40 cells in F >= 8 policy families."""
    m = len(cells)
    fams = [c["policy_family"] for c in cells]
    F = len(set(fams))
    base = {"m": m, "F": F, "engine_floor": {"m": FLOOR_M, "F": FLOOR_F}, "seed": SEED_E1, "B": B_BOOT, "blocks": "policy_family"}
    if m < FLOOR_M or F < FLOOR_F:
        return dict(base, run=False, reason=f"engine floor not met (m = {m} < {FLOOR_M} or F = {F} < {FLOOR_F}); cell table only; no interval, no p-value")
    y = [c["B"] - c["r"] for c in cells]
    ids, F_ = N1.family_ids(fams)
    res = N1.e1_core(y, ids, F_, B_BOOT, np.random.default_rng(SEED_E1))
    return dict(base, run=True, result=res)


def reading_label(e1):
    if not e1.get("run") or e1["result"].get("status") != "OK":
        return None
    lo, hi = e1["result"]["interval95"]
    if lo > 0:
        return "OVER-REJECTION (unadjusted interval above 0)"
    if hi < 0:
        return "CONSERVATISM (unadjusted interval below 0)"
    return "CALIBRATION (unadjusted interval contains 0)"


def planning(m):
    return {"engine_floor": {"m": FLOOR_M, "F": FLOOR_F}, "planning_minimum": {"m": PLAN_M, "F": PLAN_F},
            "planning_detectable_delta_2.8_sqrt(.05*.95/m)": (2.8 * math.sqrt(0.05 * 0.95 / m)) if m else None,
            "note": "EX-1: printed beside m and F, gate nothing (the engine floor only decides whether E1 runs)"}


def tabulate(cells, key):
    """Descriptive rates by a key (policy group, outcome family or design): cells, cells beyond the line, sum r, mean y."""
    out = OrderedDict()
    for k in sorted({c[key] for c in cells}):
        sel = [c for c in cells if c[key] == k]
        e = estimate(sel)
        out[k] = {"cells": e["m"], "beyond": e["n_beyond"], "expected_beyond": e["expected_beyond"], "mean_y": e["delta_D2"]}
    return out


# ---------------------------------------------------------------------------------------------
# series-level items: width of the empirical null, share of series beyond their own line
# ---------------------------------------------------------------------------------------------
def design_arrays(f, st):
    """Arrays of the series a design used (D1 definition): families, z = a/sd, z* = placebo/sd on the common draws, own-line flags."""
    sids = list(f["sids"])
    pos = {s: i for i, s in enumerate(sids)}
    idx = [pos[s] for s in st["series_used"]]
    placebo = np.asarray(f["placebo"], float)
    common = np.isfinite(placebo[idx]).all(axis=0)
    if int(common.sum()) != st["n_draws"]:
        raise ValueError("common draw set differs from the D1 statistics")
    PC = placebo[np.ix_(idx, np.where(common)[0])]
    n = PC.shape[1]
    sd = PC.std(axis=1, ddof=1)
    att = np.asarray(f["att"], float)[idx]
    rank = math.ceil(0.95 * n) - 1
    own_cut = np.sort(np.abs(PC), axis=1)[:, rank]
    return dict(sids=[sids[i] for i in idx], fam=[f["fam"][i] for i in idx], z=att / sd, zstar=PC / sd[:, None], n=n,
                beyond_own=np.abs(att) > own_cut, exp_own=(np.abs(PC) > own_cut[:, None]).mean(axis=1))


def nc_blocks(arrays_by_design, nc_pairs):
    """Per design (sorted by id) the rows of the NC series: nc_pairs = set of (design_id, family)."""
    blocks = []
    for did in sorted(arrays_by_design):
        a = arrays_by_design[did]
        rows = [i for i, f in enumerate(a["fam"]) if (did, f) in nc_pairs]
        if rows:
            blocks.append(dict(design_id=did, sids=[a["sids"][i] for i in rows], z=a["z"][rows], zstar=a["zstar"][rows], n=a["n"],
                               beyond_own=a["beyond_own"][rows], exp_own=a["exp_own"][rows]))
    return blocks


def null_width(blocks, R_=N_REF, seed=SEED_NULL):
    """Observed SD of z over the NC series against the SD of z* at one random common draw per design (same draw for all of a design's series)."""
    if not blocks:
        return None
    z = np.concatenate([b["z"] for b in blocks])
    obs = float(z.std(ddof=1)) if len(z) > 1 else None
    if obs is None:
        return None
    rng = np.random.default_rng(seed)
    U = rng.random((R_, len(blocks)))
    cols = []
    for j, b in enumerate(blocks):
        n = b["n"]
        idx = np.minimum((U[:, j] * n).astype(int), n - 1)
        cols.append(b["zstar"][:, idx])
    Z = np.concatenate(cols, axis=0)
    sds = Z.std(axis=0, ddof=1)
    p = np.percentile(sds, [2.5, 97.5])
    return {"n_series": int(len(z)), "n_designs": len(blocks), "replicates": R_, "seed": seed, "observed_mean_z": float(z.mean()), "observed_sd_z": obs,
            "reference_mean_sd": float(sds.mean()), "reference_p2.5": float(p[0]), "reference_p97.5": float(p[1]), "share_reference_sd_ge_observed": float(np.mean(sds >= obs)),
            "ratio_observed_to_reference_mean": float(obs / sds.mean())}


def series_level(blocks):
    if not blocks:
        return None
    nb = int(sum(int(b["beyond_own"].sum()) for b in blocks))
    ns = int(sum(len(b["sids"]) for b in blocks))
    exp = float(sum(float(b["exp_own"].sum()) for b in blocks))
    return {"series": ns, "series_beyond_own_line": nb, "expected_beyond": exp, "share_beyond": nb / ns, "share_expected": exp / ns}


# ---------------------------------------------------------------------------------------------
# the design table (the D1-V1 coverage logic) and the cell table
# ---------------------------------------------------------------------------------------------
def design_table(roster, gate_by, swept, reg_fams, sweep_dir):
    """One row per design of the roster with the D1-V1 coverage code; EVAL designs carry the family statistics of build_d1.design_statistics
    and their sweep arrays."""
    designs, arrays = [], {}
    for d in roster:
        did = d["design_id"]
        row = dict(design_id=did, policy_family=d["policy_family"] or did, domain=d["domain"], live_status=d["status"], gate=d["gate"], source_group=d["source_group"])
        if d["status"] != "estimated":
            row.update(code="STOP_PRE_RUN", reason=f"STOP_PRE_RUN: {d['gate']}")
        elif d["source_group"] in D1.ARCHIVE_GROUPS:
            row.update(code="FIELD_MISSING", reason="FIELD_MISSING: archive result carries no frozen treatment table in registered format")
        elif did not in gate_by:
            row.update(code="OTHER", reason="not in the gate report")
        elif gate_by[did]["status"] != "PASS":
            row.update(code="REPRO_FAIL", reason="; ".join(gate_by[did].get("reasons") or ["gate"]))
        elif gate_by[did].get("adapter") == "annual_full_year_v1":
            row.update(code="NO_SECONDARY_ADAPTER", reason="annual full-year adapter: L2 stage S3 has no certified secondary adapter")
        elif did not in swept:
            row.update(code="OTHER", reason="swept file missing or errored")
        else:
            path = Path(sweep_dir) / Path(swept[did]["file"]).name
            f = D1.load_design_file(path)
            st = D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], reg_fams.get(did, set()))
            row.update(code=("EVAL" if st["code"] == "EVAL" else "D1_SUPPORT"), reason=st.get("reason"), n_draws=st.get("n_draws"), families=st.get("families"), sweep_file=swept[did]["file"],
                       sweep_file_sha256=S.sha256_file(path), scale_mode=f["meta"]["scale_mode"])
            if st["code"] == "EVAL":
                row["family_stats"] = st["family_stats"]
                arrays[did] = design_arrays(f, st)
        designs.append(row)
    return designs, arrays


def build_cells(designs, groups, pairs, reg_by_group, reg_by_design, invalid=frozenset()):
    """All cells (EVAL design, supported family) with their pair facts, code and variant flags."""
    cells = []
    for row in sorted((d for d in designs if d["code"] == "EVAL"), key=lambda d: d["design_id"]):
        did = row["design_id"]
        g = groups.get(did)
        for f in sorted(row["family_stats"]):
            st = row["family_stats"][f]
            flags, p, cerr = cell_variant_flags(did, g, f, pairs, reg_by_group, reg_by_design, invalid)
            cells.append(dict(design_id=did, policy_family=row["policy_family"], policy_group=g, family=f, code=cell_code(g, f, pairs, reg_by_group, invalid),
                              M=st["M"], q=st["q"], B=int(bool(st["beyond"])), r=st["share_draws_beyond"], n_series=st["n_series"], top_series=st["top_series"],
                              top_z=st["top_z"], n_draws=row["n_draws"],
                              final_code=(p or {}).get("final"), coder1=(p or {}).get("c1"), coder2=(p or {}).get("c2"), route=(p or {}).get("route"),
                              coding_error_pair=bool(cerr), flags=flags))
    return cells


def coding_errors(pairs, reg_by_group, groups_in_use=None):
    """NC (final) pairs with a registered primary or secondary outcome of a design of the group in the family: the contradicting designs and outcomes."""
    out = []
    for (g, f), p in sorted(pairs.items()):
        if VARIANTS["final"](p) and f in reg_by_group.get(g, {}):
            out.append(dict(policy_group=g, family=f, contradicting=[dict(design_id=d, outcome_key=k, role=r) for d, k, r in sorted(reg_by_group[g][f])]))
    return out


# ---------------------------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------------------------
def csv_text(header, rows):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def write_once(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError:
        sys.exit(f"{S.rel(path)} exists: not overwritten (use --out-dir for a new version folder)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--sweep-dir", default=str(B.OUT), help="folder with the sweep files (tests only)")
    args = ap.parse_args(argv)
    out_dir, sweep_dir = Path(args.out_dir), Path(args.sweep_dir)

    problems = B.verify_records() + D3.verify_records()
    p2, rec2 = verify_d2_records()
    problems += p2
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    live_sha = S.sha256_file(S.LIVE)
    if rec2["listed"].get(S.rel(S.LIVE)) != live_sha:
        sys.exit("live set differs from the D2 hash record: not run")
    entry_d2 = re.search(r"^\s+D2\s+([0-9a-f]{64})", D_HASH.read_text(encoding="utf-8"), re.M).group(1)
    gate = S.read_json(sweep_dir / "GATE_REPORT.json")
    if gate["live_set_sha256"] != live_sha:
        sys.exit("the sweep was made with another live set")
    sweep_run = S.read_json(sweep_dir / "SWEEP_RUN.json")
    swept = {d["design_id"]: d for d in sweep_run["designs"] if d["status"] == "OK"}
    for did, d in swept.items():
        if S.sha256_file(Path(sweep_dir) / Path(d["file"]).name) != d["sha256"]:
            sys.exit(f"sweep file of {did} differs from SWEEP_RUN.json")

    roster, live_meta = D1.load_live()
    inv_rows = S.inventory_rows()
    table = S.catalog_series_table()
    reg_fams = D1.registered_families(inv_rows, table)
    groups = design_groups(inv_rows)
    reg_rows = registered_rows(inv_rows, table)
    reg_by_group, reg_by_design = registered_maps(reg_rows)
    gate_by = {d["design_id"]: d for d in gate["designs"]}

    receipt = S.read_json(R.D2DIR / R.OUT_RECEIPT.name)
    invalid = frozenset(tuple(x) for x in receipt.get("nc_invalid", []))
    rec_rows = R.load_csv(R.OUT_CSV, R.OUT_HEADER)
    pairs = pair_table(rec_rows)
    if len(pairs) != R.N_GROUPS * R.N_FAMILIES:
        sys.exit("the reconciled table is not the 35 x 16 grid")

    designs, arrays = design_table(roster, gate_by, swept, reg_fams, sweep_dir)
    cells = build_cells(designs, groups, pairs, reg_by_group, reg_by_design, invalid)
    nc_cells = [c for c in cells if c["code"] == "NC"]
    primary = estimate(nc_cells)
    e1 = e1_decision(nc_cells)

    # secondary items
    variants = OrderedDict()
    for v in ALL_VARIANTS:
        sel = [c for c in cells if c["flags"][v]]
        variants[v] = estimate(sel)
    nc_set = {(c["design_id"], c["family"]) for c in nc_cells}
    strict_set = {(c["design_id"], c["family"]) for c in cells if c["flags"]["strict"]}
    blocks_final, blocks_strict = nc_blocks(arrays, nc_set), nc_blocks(arrays, strict_set)
    design_codes = Counter(d["code"] for d in designs)
    eval_designs = [d for d in designs if d["code"] == "EVAL"]
    n_with_nc = len({c["design_id"] for c in nc_cells})
    groups_in_use = sorted({groups.get(d["design_id"]) for d in eval_designs if groups.get(d["design_id"])})
    r1, r2 = R.load_csv(R.CODER1, R.HEADER_CODER), R.load_csv(R.CODER2, R.HEADER_CODER)
    keys, a1, a2 = R.align(r1, r2)
    sub = [i for i, k in enumerate(keys) if k[0] in groups_in_use]
    agree_sub = R.agreement_stats([a1[i] for i in sub], [a2[i] for i in sub])

    me = Path(__file__).resolve()
    inputs_paths = [S.LIVE, D_QUESTIONS, D_HASH, AMEND, HASH_REC, HASH_S1, S.INVENTORY_CSV, R.CODER1, R.CODER2, R.THIRD, R.GRID, R.OUT_CSV, R.D2DIR / R.OUT_RECEIPT.name,
                    sweep_dir / "GATE_REPORT.json", sweep_dir / "SWEEP_RUN.json"]
    if (sweep_dir / "SWEEP_MANIFEST.json").exists():
        inputs_paths.append(sweep_dir / "SWEEP_MANIFEST.json")
    stamp = {"rule_id": RULE_ID, "question": QUESTION, "version": VERSION, "cutoff_utc": live_meta["as_of"], "run_utc": utc_now(), "live_set_sha256": live_sha,
             "sweep_run_sha256": S.sha256_file(sweep_dir / "SWEEP_RUN.json"), "gate_report_sha256": S.sha256_file(sweep_dir / "GATE_REPORT.json"),
             "sweep_manifest_sha256": S.sha256_file(sweep_dir / "SWEEP_MANIFEST.json") if (sweep_dir / "SWEEP_MANIFEST.json").exists() else None,
             "code": {f: S.sha256_file(ROOT / f) for f in CODE_FILES}, "sweep_code": B.code_hashes(), "amendment_sha256": S.sha256_file(AMEND),
             "hash_record_sha256": S.sha256_file(HASH_REC), "hash_record_s1_sha256": S.sha256_file(HASH_S1), "question_file_sha256": S.sha256_file(D_QUESTIONS), "entry_D2_sha256": entry_d2,
             "coding_files_sha256": {"coder1": S.sha256_file(R.CODER1), "coder2": S.sha256_file(R.CODER2), "third_reader": S.sha256_file(R.THIRD), "blank_grid": S.sha256_file(R.GRID),
                                     "reconciled": S.sha256_file(R.OUT_CSV)},
             "D": len(roster), "estimated": live_meta["counts"]["estimated"], "not_evaluable": live_meta["counts"]["not_evaluable"], "outside_D": live_meta["outside_D"],
             "m": primary["m"], "F": primary["F"], "n_designs_d1_eval": len(eval_designs), "python": sys.version.split()[0], "numpy": np.__version__,
             "seeds": {"E1": SEED_E1, "null_width": SEED_NULL}, "B": B_BOOT, "replicates": N_REF, "no_significance_label": True}
    result = OrderedDict([
        ("D", len(roster)), ("designs_d1_eval", len(eval_designs)), ("designs_with_nc_cell", n_with_nc), ("designs_without_nc_cell", len(eval_designs) - n_with_nc),
        ("coverage_codes_over_D", dict(sorted(design_codes.items()))),
        ("cell_codes", {c: sum(1 for x in cells if x["code"] == c) for c in CELL_CODES}), ("cells_total", len(cells)),
        ("minimums_printed_beside_m", dict(planning(primary["m"]), m_ge_floor=primary["m"] >= FLOOR_M, F_ge_floor=primary["F"] >= FLOOR_F,
                                           m_ge_planning=primary["m"] >= PLAN_M, F_ge_planning=primary["F"] >= PLAN_F)),
        ("estimate_nc_cells", primary), ("E1", e1), ("reading_guide_label", reading_label(e1)),
        ("variants_of_the_nc_set", variants),
        ("strict_set_estimate", variants["strict"]),
        ("empirical_null_width_nc_series", null_width(blocks_final)), ("empirical_null_width_strict_set", null_width(blocks_strict)),
        ("series_level_nc", series_level(blocks_final)), ("series_level_strict_set", series_level(nc_blocks(arrays, strict_set))),
        ("rates_by_policy_group", tabulate(nc_cells, "policy_group")), ("rates_by_outcome_family", tabulate(nc_cells, "family")), ("rates_by_design", tabulate(nc_cells, "design_id")),
        ("nc_pairs", sorted(f"{g}|{f}" for (g, f), p in pairs.items() if VARIANTS["final"](p))),
        ("coding_errors", coding_errors(pairs, reg_by_group)),
        ("pairs_by_final_code", dict(Counter(p["final"] for p in pairs.values()))),
        ("coding", {"counts_route": receipt["counts_route"], "counts_final_code": receipt["counts_final_code"], "n_nc_pairs": receipt["n_nc"], "n_strict_nc_pairs": receipt["n_strict_nc"],
                    "agreement_before_third_reader": receipt["agreement_before_third_reader"], "agreement_in_groups_with_a_d1_eval_design": agree_sub, "groups_with_a_d1_eval_design": groups_in_use,
                    "limitations": ["no code book and no blank grid were hashed before the coding", "both coders saw the inventory's policy-group by outcome-family count matrix (no estimate)",
                                    "the sweep and D1/D3 result files existed on disk during coding; the coder logs state that none was opened",
                                    "coders and third reader are Claude instances; no human coder; the third reader is the analysis session"]}),
    ])
    payload = S.clean(OrderedDict([
        ("schema_version", "l3-meta-d2-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {S.rel(p): {"sha256": S.sha256_file(p), "bytes": Path(p).stat().st_size} for p in inputs_paths}),
        ("question", OrderedDict([("D2", result)])), ("cells", cells),
        ("designs", [{k: v for k, v in d.items() if k != "family_stats"} for d in designs]),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    nc_header = ["design_id", "policy_family", "policy_group", "outcome_family", "final_code", "coder1", "coder2", "strict_nc", "M", "q", "beyond", "r", "y", "n_series", "n_draws", "top_series", "top_z"]
    nc_rows = [[c["design_id"], c["policy_family"], c["policy_group"], c["family"], c["final_code"], c["coder1"], c["coder2"], int(c["flags"]["strict"]), repr(c["M"]), repr(c["q"]),
                c["B"], repr(c["r"]), repr(c["B"] - c["r"]), c["n_series"], c["n_draws"], c["top_series"], repr(c["top_z"])] for c in nc_cells]
    if not args.dry_run:
        target = out_dir / OUT_NAME
        if target.exists():
            sys.exit(f"{S.rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        write_once(target, text)
        write_once(out_dir / CSV_NAME, csv_text(nc_header, nc_rows))
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(S.LIVE, copy)
        assert S.sha256_file(copy) == live_sha
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_d2.py " + " ".join(argv if argv is not None else sys.argv[1:]), "code_sha256": stamp["code"]["scripts/l3_meta/build_d2.py"],
                                 "live_set_sha256": live_sha, "sweep_run_sha256": stamp["sweep_run_sha256"], "results_file": S.rel(target),
                                 "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": {k: stamp[k] for k in ("rule_id", "question", "version", "run_utc", "m", "F", "D", "n_designs_d1_eval")}, "D2": {k: result[k] for k in (
        "cell_codes", "cells_total", "designs_with_nc_cell", "estimate_nc_cells", "E1", "reading_guide_label", "variants_of_the_nc_set", "empirical_null_width_nc_series", "series_level_nc")}}
    print(json.dumps(S.clean(brief), ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {S.rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
