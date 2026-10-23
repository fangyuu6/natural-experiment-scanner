#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version D2-V2 (rule id EX-1): negative controls among the 55 extension series, and calibrated D1-V2.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261018_D2_V2.md (hash records PREREG_AMEND_20261018_D2_V2_HASH.txt and _S1.txt), ruling HUB_RULING_20261018_D2_V2.md.
Reads the pinned inputs of refresh 20261018_r1 and the blind coding files; reuses build_d1 (design statistics), build_d2 (estimate, E1 decision, null width), build_cal
(bootstrap of the calibrated D1).  Every statistic is exploratory: no "significant" label, no licensed sentence.  Nothing under scripts/l2_pipeline is opened.

Run: VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_d2_ext.py [--dry-run] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_d1 as D1  # noqa: E402
import build_d2 as D2  # noqa: E402
import build_cal as CAL  # noqa: E402
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "D2", "D2-V2"
SEED_E1 = D2.SEED_E1
SEED_NULL = [20261011, 32, 7]
SEED_NULL_POOL = [20261011, 35, 7]
SEED_BOOT = [20261011, 35, 2]
B_BOOT = 9999
N_REF = 9999
KAPPA_V1_FIXED = 1.3154
CODES = ("channel", "unsure", "no_channel")
REFRESH = ROOT / "data/l3_meta/refresh/20261018_r1"
V2DIR = ROOT / "data/l3_meta/d2_plausibility/v2"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261018_D2_V2.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261018_D2_V2_HASH.txt"
HASH_S1 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261018_D2_V2_HASH_S1.txt"
HASH_S2 = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261018_D2_V2_HASH_S2.txt"   # code pin after the first dry-run fix; its lines supersede S1 lines for the same file
GRID = V2DIR / "d2v2_blank_grid.csv"
CODER1, CODER2, THIRD = V2DIR / "d2v2_codes_coder1.csv", V2DIR / "d2v2_codes_coder2.csv", V2DIR / "d2v2_codes_third.csv"
EXT_CATALOG = ROOT / "data/l3_meta/sweep_ext_v1/SERIES_EXT_CATALOG_V1.csv"
OUT_DIR = ROOT / "data/l3_meta/d2_v2"
CODE_FILES = ["scripts/l3_meta/build_d2_ext.py", "scripts/l3_meta/build_d2.py", "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_cal.py"]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------------------------
def verify_records(paths=None, amend=AMEND):
    paths = paths or (HASH_REC, HASH_S1, HASH_S2)
    problems = []
    pat = re.compile(r"^\s+(\S+) sha256 ([0-9a-f]{64})\s*$")
    first = Path(paths[0])
    if not first.exists():
        return ["hash record missing"]
    m = re.search(r"^sha256: ([0-9a-f]{64})", first.read_text(encoding="utf-8"), re.M)
    if not m or sha(amend) != m.group(1):
        problems.append("amendment differs from its record")
    recorded = {}
    for p in paths:
        if not Path(p).exists():
            if Path(p) == Path(HASH_S1):
                problems.append("supplement S1 missing")
            continue
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            m = pat.match(line)
            if m:
                recorded[m.group(1)] = m.group(2)      # later records supersede earlier ones
    for rel, h in recorded.items():
        f = ROOT / rel
        if not f.exists() or sha(f) != h:
            problems.append(f"differs from record: {rel}")
    return problems


# ---------------------------------------------------------------------------------------------
# coding
# ---------------------------------------------------------------------------------------------
def load_codes(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != ["policy_group", "series_id", "code", "reason"]:
            raise ValueError(f"bad header in {path}: {rd.fieldnames}")
        out = {}
        for r in rd:
            k = (r["policy_group"], r["series_id"])
            if k in out:
                raise ValueError(f"duplicate pair {k} in {path}")
            if r["code"] not in CODES:
                raise ValueError(f"bad code {r['code']!r} for {k} in {path}")
            out[k] = r
    return out


def load_grid(path=GRID):
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["policy_group"], r["series_id"]) for r in csv.DictReader(fh)]


def reconcile(grid, c1, c2, third):
    """Equal codes stand; any different pair takes the third reader's code.  Returns {pair: dict(final, c1, c2, route, strict)}."""
    out = {}
    for k in grid:
        a, b = c1[k]["code"], c2[k]["code"]
        if a == b:
            out[k] = dict(final=a, c1=a, c2=b, route="agreed", third=None, strict=(a == "no_channel"))
        else:
            if k not in third:
                raise ValueError(f"disagreement without third-reader code: {k}")
            out[k] = dict(final=third[k]["code"], c1=a, c2=b, route="third_reader", third=third[k]["code"], strict=False)
    extra = set(third) - {k for k, v in out.items() if v["route"] == "third_reader"}
    if extra:
        raise ValueError(f"third-reader codes for pairs that were not in dispute: {sorted(extra)[:3]}")
    return out


def agreement(a, b):
    n = len(a)
    agree = sum(1 for x, y in zip(a, b) if x == y)
    p0 = agree / n
    pe = sum((sum(1 for x in a if x == c) / n) * (sum(1 for y in b if y == c) / n) for c in CODES)
    return dict(n=n, equal=agree, raw=p0, kappa=(p0 - pe) / (1 - pe) if pe < 1 else None, table={f"{x}|{y}": int(sum(1 for u, v in zip(a, b) if u == x and v == y)) for x in CODES for y in CODES})


# ---------------------------------------------------------------------------------------------
# cell statistics (pure)
# ---------------------------------------------------------------------------------------------
def cell_stats(z, zstar):
    """z: (k,), zstar: (k, n) of the NC series of one cell.  M = max |z|; q = rank ceil(.95 n) value of max_k |z*|; B = M > q; r = share of draws with max |z*| > q."""
    z, zstar = np.asarray(z, float), np.asarray(zstar, float)
    n = zstar.shape[1]
    M = float(np.max(np.abs(z)))
    Ms = np.max(np.abs(zstar), axis=0)
    q = float(np.sort(Ms)[math.ceil(0.95 * n) - 1])
    return dict(M=M, q=q, B=int(M > q), r=float(np.mean(Ms > q)), n_series=int(len(z)), n_draws=int(n))


def sd_ratio_block(blocks, seed):
    return D2.null_width(blocks, R_=N_REF, seed=seed)


def design_blocks(arrays, rowsets):
    """rowsets: {design_id: set of series ids}.  One block per design (sorted) with those series' arrays."""
    out = []
    for did in sorted(arrays):
        a = arrays[did]
        rows = [i for i, s in enumerate(a["sids"]) if s in rowsets.get(did, ())]
        if rows:
            out.append(dict(design_id=did, sids=[a["sids"][i] for i in rows], z=a["z"][rows], zstar=a["zstar"][rows], n=a["n"], beyond_own=a["beyond_own"][rows],
                            exp_own=a["exp_own"][rows]))
    return out


# ---------------------------------------------------------------------------------------------
def write_once(path, text):
    p = Path(path)
    if p.exists():
        raise FileExistsError(f"refusing to overwrite {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def nums(x):
    if isinstance(x, dict):
        return {str(k): nums(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [nums(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    problems = verify_records()
    if problems:
        sys.exit("record check failed, not run: " + "; ".join(problems))
    sweep_dir, ext_dir = REFRESH / "sweep", REFRESH / "sweep_ext"
    live_path = REFRESH / "live/l2_set_live.json"
    ext_run = S.read_json(ext_dir / "SWEEP_EXT_RUN.json")
    d1v2 = S.read_json(REFRESH / "d1_ext/RESULTS_D1_V2.json")
    roster, live_meta = D1.load_live(live_path)
    fam_of = {d["design_id"]: (d["policy_family"] or d["design_id"]) for d in roster}
    inv_rows = S.inventory_rows()
    table = S.catalog_series_table()
    reg_fams = D1.registered_families(inv_rows, table)
    groups = D2.design_groups(inv_rows)
    reg_by_group, _ = D2.registered_maps(D2.registered_rows(inv_rows, table))
    ext_cat = {r["outcome_key"]: r for r in csv.DictReader(open(EXT_CATALOG, newline="", encoding="utf-8"))}

    # ---- coding
    grid = load_grid()
    c1, c2, third = load_codes(CODER1), load_codes(CODER2), (load_codes(THIRD) if THIRD.exists() else {})
    if set(c1) != set(grid) or set(c2) != set(grid):
        sys.exit("coding files do not match the blank grid")
    pairs = reconcile(grid, c1, c2, third)
    agree = agreement([c1[k]["code"] for k in grid], [c2[k]["code"] for k in grid])
    final_counts = Counter(v["final"] for v in pairs.values())

    # ---- arrays on the D1-V2 universe
    arrays, stats, v1_sids, ext_sids = {}, {}, {}, {}
    for e in sorted(ext_run["designs"], key=lambda d: d["design_id"]):
        if e.get("status") != "OK":
            continue
        did = e["design_id"]
        name = Path(e["file"]).name if "file" in e else f"{did}.npz"
        f1, f2 = D1.load_design_file(sweep_dir / name), D1.load_design_file(ext_dir / name)
        if set(f1["sids"]) & set(f2["sids"]):
            sys.exit(f"series ids repeat for {did}")
        u = dict(sids=f1["sids"] + f2["sids"], fam=f1["fam"] + f2["fam"], code=f1["code"] + f2["code"], att=np.concatenate([f1["att"], f2["att"]]),
                 sd=np.concatenate([f1["sd"], f2["sd"]]), placebo=np.vstack([f1["placebo"], f2["placebo"]]))
        st = D1.design_statistics(u["sids"], u["fam"], u["code"], u["att"], u["sd"], u["placebo"], reg_fams.get(did, set()))
        if st["code"] != "EVAL":
            continue
        arrays[did] = D2.design_arrays(u, st)
        stats[did] = st
        v1_sids[did], ext_sids[did] = set(f1["sids"]), set(f2["sids"])
    if len(arrays) != d1v2["question"]["D1"]["n_designs_eval"]:
        sys.exit("number of EVAL designs differs from RESULTS_D1_V2.json")
    # identity at kappa = 1: family statistics equal RESULTS_D1_V2
    ref_rows = {d["design_id"]: d for d in d1v2["designs"] if d.get("code") == "EVAL"}
    maxdiff = 0.0
    for did, st in stats.items():
        r = ref_rows[did]
        for f, v in st["family_stats"].items():
            for k in ("M", "q"):
                a, b = v[k], r["family_stats"][f][k]
                maxdiff = max(maxdiff, abs(a - b) / max(1e-12, abs(b)))
        maxdiff = max(maxdiff, abs(st["Z"] - r["Z"]) / max(1e-12, abs(r["Z"])) if r["Z"] else abs(st["Z"]))
    if maxdiff > 1e-9:
        sys.exit(f"D1-V2 statistics differ from RESULTS_D1_V2.json (max relative diff {maxdiff:.2e}); stopped")

    # ---- V2 cells
    series_family = {s: ext_cat[s]["family"] for s in ext_cat}
    cells, codes_by_series = [], Counter()
    nc_rows = defaultdict(set)         # design -> NC extension series ids (primary set)
    strict_rows = defaultdict(set)
    for did, a in arrays.items():
        g = groups.get(did)
        by_fam = defaultdict(list)
        for i, s in enumerate(a["sids"]):
            if s not in ext_sids[did]:
                continue
            p = pairs.get((g, s))
            if p is None:
                codes_by_series["GROUP_OR_SERIES_NOT_CODED"] += 1
                continue
            fam = a["fam"][i]
            if p["final"] != "no_channel":
                codes_by_series["PAIR_NOT_NC"] += 1
                continue
            if fam in reg_by_group.get(g, {}):
                codes_by_series["CODING_ERROR"] += 1
                continue
            codes_by_series["NC"] += 1
            by_fam[fam].append(i)
            nc_rows[did].add(s)
            if p["strict"]:
                strict_rows[did].add(s)
        for fam, rows in sorted(by_fam.items()):
            cs = cell_stats(a["z"][rows], a["zstar"][rows])
            top = int(np.argmax(np.abs(a["z"][rows])))
            cells.append(dict(design_id=did, policy_family=fam_of[did], policy_group=g, family=fam, **cs, top_series=a["sids"][rows[top]], top_z=float(a["z"][rows][top]),
                              strict=all(s in strict_rows[did] for s in [a["sids"][i] for i in rows])))
    est = D2.estimate(cells)
    e1 = D2.e1_decision(cells)
    strict_cells = [c for c in cells if c["strict"]]
    strict_est = D2.estimate(strict_cells)
    blocks_v2 = design_blocks(arrays, nc_rows)
    blocks_v2_strict = design_blocks(arrays, strict_rows)
    series_lvl = D2.series_level(blocks_v2)

    # ---- V1 negative-control cells on the D1-V2 universe
    v1cells = list(csv.DictReader(open(REFRESH / "d2/NC_CELLS_D2_V1.csv", newline="", encoding="utf-8")))
    v1_nc, v1_strict = defaultdict(set), defaultdict(set)
    for r in v1cells:
        did = r["design_id"]
        if did not in arrays:
            continue
        a = arrays[did]
        for i, s in enumerate(a["sids"]):
            if s in v1_sids[did] and a["fam"][i] == r["outcome_family"]:
                v1_nc[did].add(s)
                if r["strict_nc"] == "1":
                    v1_strict[did].add(s)
    pooled_rows = {d: v1_nc.get(d, set()) | nc_rows.get(d, set()) for d in arrays}
    pooled_strict = {d: v1_strict.get(d, set()) | strict_rows.get(d, set()) for d in arrays}
    sets = OrderedDict([("pooled", pooled_rows), ("V1only", dict(v1_nc)), ("V2only", dict(nc_rows)), ("pooled_strict", pooled_strict)])
    kappa = OrderedDict()
    for name, rows in sets.items():
        bl = design_blocks(arrays, rows)
        nw = D2.null_width(bl, R_=N_REF, seed=SEED_NULL_POOL) if bl else None
        kappa[name] = dict(nw, kappa=nw["ratio_observed_to_reference_mean"]) if nw else None
    d2v1 = S.read_json(REFRESH / "d2/RESULTS_D2_V1.json")

    # V1 + V2 cells (second row)
    v1_cell_objs = [dict(design_id=r["design_id"], policy_family=r["policy_family"], B=int(r["beyond"]), r=float(r["r"])) for r in v1cells]
    pooled_cells = D2.estimate(v1_cell_objs + [dict(design_id=c["design_id"], policy_family=c["policy_family"], B=c["B"], r=c["r"]) for c in cells])
    pooled_e1 = D2.e1_decision(v1_cell_objs + [dict(design_id=c["design_id"], policy_family=c["policy_family"], B=c["B"], r=c["r"]) for c in cells])

    # ---- calibrated D1-V2
    dids = sorted(arrays)
    didx = {d: i for i, d in enumerate(dids)}
    fams = sorted({fam_of[d] for d in dids})
    fpos = {f: i for i, f in enumerate(fams)}
    cell_M, cell_q, cell_design, cell_blk, cell_fam = [], [], [], [], []
    mu, tau, nstar_list, p_ge = [], [], [], []
    for did in dids:
        st, a = stats[did], arrays[did]
        mu.append(st["mu"]); tau.append(st["tau"]); p_ge.append({int(k): v for k, v in st["P_Nstar_ge"].items()})
        ns = np.zeros(a["n"])
        for f, v in st["family_stats"].items():
            rows = [i for i, x in enumerate(a["fam"]) if x == f]
            Ms = np.max(np.abs(a["zstar"][rows]), axis=0)
            ns += (Ms > v["q"])
            cell_M.append(v["M"]); cell_q.append(v["q"]); cell_design.append(didx[did]); cell_blk.append(fpos[fam_of[did]]); cell_fam.append((did, f))
        nstar_list.append(ns)
    cell_M, cell_q = np.array(cell_M), np.array(cell_q)
    cell_design, cell_blk = np.array(cell_design), np.array(cell_blk)
    mu, tau = np.array(mu), np.array(tau)
    nc_cellset = {(c["design_id"], c["family"]) for c in cells} | {(r["design_id"], r["outcome_family"]) for r in v1cells if r["design_id"] in arrays}
    cell_nc = np.array([k in nc_cellset for k in cell_fam])
    des_blk = np.array([fpos[fam_of[d]] for d in dids])
    F = len(fams)

    def calibrated(kp):
        N = CAL.d1_design_N(cell_M, cell_q, cell_design, len(dids), kp)
        p = CAL.d1_p(N, nstar_list)
        return CAL.d1_summary(N, mu, tau, p, dids, p_ge, kp)

    kv = {n: (v["kappa"] if v else None) for n, v in kappa.items()}
    grid_k = OrderedDict([("kappa=1", 1.0), ("pooled", kv["pooled"]), ("V1only", kv["V1only"]), ("V2only", kv["V2only"]), ("V1_fixed_1.3154", KAPPA_V1_FIXED),
                          ("pooled_strict", kv["pooled_strict"])])
    cal_tab = OrderedDict((n, calibrated(k)) for n, k in grid_k.items() if k is not None)
    # identity at kappa = 1 with D1-V2
    d1v2_theta = d1v2["question"]["D1"]["theta_D1_plain_mean_Z"]
    if abs(cal_tab["kappa=1"]["theta"] - d1v2_theta) > 1e-9 * max(1, abs(d1v2_theta)):
        sys.exit("calibrated D1 at kappa = 1 differs from D1-V2 theta")

    # bootstrap (pooled set, ref fixed at the pooled reference)
    pooled_blocks = design_blocks(arrays, pooled_rows)
    nc_z = np.concatenate([b["z"] for b in pooled_blocks])
    nc_blk = np.concatenate([[fpos[fam_of[b["design_id"]]]] * len(b["z"]) for b in pooled_blocks]).astype(int)
    ref_pool = kappa["pooled"]["reference_mean_sd"]
    bs = CAL.bootstrap_d1(cell_M, cell_q, cell_design, des_blk, cell_blk, cell_nc, mu, tau, F, nc_blk, nc_z, ref_pool, kv["pooled"], B_=B_BOOT, seed=SEED_BOOT)
    boot = OrderedDict((k, CAL.interval_block(bs[k])) for k in ("kappa", "joint_theta", "data_theta", "kappa_only_theta", "joint_e", "data_e", "data_uncal_theta", "mean_z"))
    kappa_lo = boot["kappa"]["interval95"][0]
    reading = dict(
        kappa="WIDER (lower end above 1)" if kappa_lo > 1 else "NOT SEPARATED FROM 1",
        d1="NOT SEPARATED FROM THE WIDENED LINE" if boot["joint_theta"]["interval95"][0] <= 0 <= boot["joint_theta"]["interval95"][1] else ("BREADTH REMAINS (interval above 0)" if boot["joint_theta"]["interval95"][0] > 0 else "BELOW (interval below 0)"))

    # ---- tables
    def tab(key):
        d = defaultdict(list)
        for c in cells:
            d[c[key]].append(c)
        return {k: dict(cells=len(v), beyond=int(sum(x["B"] for x in v)), sum_r=float(sum(x["r"] for x in v)), mean_y=float(np.mean([x["B"] - x["r"] for x in v]))) for k, v in sorted(d.items())}

    d2_v1_summary = d2v1["estimate_nc_cells"] if "estimate_nc_cells" in d2v1 else d2v1.get("question", {}).get("D2", {}).get("estimate_nc_cells")
    stamp = dict(rule_id=RULE_ID, question=QUESTION, version=VERSION, run_utc=utc_now(), live_set_sha256=sha(live_path), live_as_of=live_meta["as_of"],
                 amendment_sha256=sha(AMEND), hash_record_sha256=sha(HASH_REC), hash_record_s1_sha256=sha(HASH_S1),
                 coding_files_sha256={p.name: sha(p) for p in (GRID, CODER1, CODER2, THIRD)}, code={f: sha(ROOT / f) for f in CODE_FILES},
                 d1_v2_results_sha256=sha(REFRESH / "d1_ext/RESULTS_D1_V2.json"), seeds=dict(E1=SEED_E1, null=SEED_NULL, null_pool=SEED_NULL_POOL, boot=SEED_BOOT), B=B_BOOT, replicates=N_REF,
                 python=sys.version.split()[0], numpy=np.__version__, m=est["m"], F_policy_families=est["F"], no_significance_label=True)
    result = OrderedDict([
        ("stamp", stamp),
        ("coding", dict(pairs=len(grid), agreement=agree, final_counts=dict(final_counts), routed_to_third=sum(1 for v in pairs.values() if v["route"] == "third_reader"),
                        strict_nc_pairs=sum(1 for v in pairs.values() if v["strict"]))),
        ("series_codes_over_supported_extension_series_of_eval_designs", dict(codes_by_series)),
        ("designs_eval", len(arrays)), ("max_rel_diff_vs_RESULTS_D1_V2", maxdiff),
        ("estimate_V2_cells_headline", est), ("E1", e1), ("reading_label", D2.reading_label(e1)), ("minimums", D2.planning(est["m"])),
        ("estimate_V2_strict_cells", strict_est), ("series_level_V2", series_lvl),
        ("null_width_V2_series", D2.null_width(blocks_v2, R_=N_REF, seed=SEED_NULL)), ("null_width_V2_strict", D2.null_width(blocks_v2_strict, R_=N_REF, seed=SEED_NULL)),
        ("estimate_V1_cells_from_D2_V1", d2_v1_summary), ("estimate_V1_plus_V2_cells", pooled_cells), ("E1_V1_plus_V2", pooled_e1),
        ("by_policy_group", tab("policy_group")), ("by_outcome_family", tab("family")), ("by_design", tab("design_id")),
        ("kappa", kappa), ("calibrated_D1_V2", cal_tab), ("bootstrap_pooled", boot), ("reading_guide", reading),
        ("nc_series_counts", {n: int(sum(len(v) for v in rows.values())) for n, rows in sets.items()}),
    ])
    csv_head = ["design_id", "policy_family", "policy_group", "outcome_family", "M", "q", "B", "r", "n_series", "n_draws", "top_series", "top_z", "strict"]
    csv_lines = [",".join(csv_head)] + [",".join(str(c[k]) for k in ("design_id", "policy_family", "policy_group", "family", "M", "q", "B", "r", "n_series", "n_draws", "top_series", "top_z", "strict")) for c in cells]
    if args.dry_run:
        print(json.dumps(nums(result), indent=1)[:6000])
        return 0
    write_once(out_dir / "RESULTS_D2_V2.json", json.dumps(nums(result), indent=1, sort_keys=False))
    write_once(out_dir / "NC_CELLS_D2_V2.csv", "\n".join(csv_lines) + "\n")
    write_once(out_dir / "RUN_LOG.jsonl", json.dumps(dict(run_utc=stamp["run_utc"], m=est["m"], F=est["F"], delta=est["delta_D2"])) + "\n")
    print(json.dumps(nums(dict(m=est["m"], F=est["F"], est=est, kappa={k: (v or {}).get("kappa") for k, v in kappa.items()})), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
