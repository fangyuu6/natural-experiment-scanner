#!/usr/bin/env python3
"""REPGAP-V1 (rule id EX-1, exploratory): replication gap across policies and what moves it.  Extension of Q2-R.

Rules: docs/plans/l3-meta/PREREG_REPGAP_V1.md (hash record PREREG_REPGAP_V1_HASH.txt and its supplements).  The pairs are those of build_q2r.py
(imported, unchanged).  Reads: the snapshot file given, the catalogue, the effort letters of the replication queue.  Writes only the new output folder
and the ledger.  No network; nothing under data/replication, scripts/replication or any L2 tree is written.

  python3 -I -B scripts/l3_meta/build_repgap.py --snapshot data/replication/snapshot_v5/snapshot_<UTC>.json --version 1 --out-dir data/l3_meta/repgap_v1
(.venv_did/bin/python for the regression: numpy)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_q2r as Q  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_REPGAP_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_REPGAP_V1_HASH.txt"
V1_SNAPSHOT = ROOT / "data/replication/snapshot_v5/snapshot_20261017T022909852263Z.json"
JOBS = ROOT / "data/replication/queue/jobs.jsonl"
LEDGER = ROOT / "data/l3_meta/repgap_ledger.jsonl"
SEED_P = 20261017
SEED_R = 20261018
B = 9999
FLOOR_N, FLOOR_F = 20, 20
MIN_ARM = 5
CODE = ["scripts/l3_meta/build_repgap.py", "scripts/l3_meta/build_q2r.py"]


def sha256_file(p):
    return Q.sha256_file(p)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_pins(path):
    text = Path(path).read_text(encoding="utf-8")
    pins = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    files, L = {}, text.splitlines()
    for i, line in enumerate(L):
        m = re.match(r"^file:\s*(\S+)", line)
        if m and i + 1 < len(L):
            s = re.match(r"^sha256:\s*([0-9a-f]{64})", L[i + 1])
            if s:
                files[m.group(1)] = s.group(1)
    return {**pins, **files}


def verify_records():
    problems, merged = [], {}
    recs = [REC] + sorted(REC.parent.glob(REC.stem + "_S*.txt"), key=lambda p: int(re.search(r"_S(\d+)\.txt$", p.name).group(1)))
    for r in recs:
        if r.is_file():
            for p, h in parse_pins(r).items():
                merged[p] = (h, r.name)
    for p, (h, name) in merged.items():
        if not (ROOT / p).is_file():
            problems.append(f"{p} (in {name}) is missing")
        elif sha256_file(ROOT / p) != h:
            problems.append(f"{p} differs from {name}")
    return problems, len(recs)


# ---------------------------------------------------------------------------------------------
# pairs with features
# ---------------------------------------------------------------------------------------------
def add_features(pairs, cat, effort):
    out = []
    for x in pairs:
        c = cat.get(x["paper_id"]) or {}
        y = OrderedDict(x)
        P, O = x["P"], x["O"]
        y["r"] = O / P
        y["lam"] = math.log(O / P) if x["u"] == 1 else None
        so, sp = x["se_ours"], x["se_pub"]
        if so > 0 and sp > 0:
            z = x["a"] * (x["o"] - x["p"]) / math.sqrt(so * so + sp * sp)
            y["z"], y["absz"] = z, abs(z)
        else:
            y["z"] = y["absz"] = None
        y["effort"] = effort.get(x["paper_id"])
        y["effort_ml"] = None if y["effort"] not in ("S", "M", "L") else ("S" if y["effort"] == "S" else "M_or_L")
        y["design"] = c.get("design")
        y["staggered"] = None if not y["design"] else ("staggered" if y["design"] == "staggered_DiD" else "other_design")
        n_t = c.get("n_treated_states")
        y["n_treated"] = n_t
        y["treated_group"] = "unknown" if not isinstance(n_t, int) else ("1" if n_t == 1 else ("2_to_5" if n_t <= 5 else "6_or_more"))
        y["tier"] = c.get("tier")
        y["data_source"] = {"R1a": "authors_cleaned_data", "R1b": "public_rebuild"}[x["rung"]]
        y["top5"] = "top5" if x["journal_group"] == "top5" else "not_top5"
        out.append(y)
    return out


# ---------------------------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------------------------
def med(xs):
    s = sorted(xs)
    return Q.quantile7(s, 0.5) if s else None


def core(pairs):
    r = sorted(x["r"] for x in pairs)
    lam = [x["lam"] for x in pairs if x["lam"] is not None]
    az = [x["absz"] for x in pairs if x["absz"] is not None]
    n = len(pairs)
    return OrderedDict([
        ("n", n), ("F", len({x["family"] for x in pairs})),
        ("median_r", Q.quantile7(r, 0.5) if r else None), ("mean_r", sum(r) / n if n else None),
        ("quartiles_r", [Q.quantile7(r, q) for q in (0.25, 0.75)] if r else None), ("min_max_r", [r[0], r[-1]] if r else None),
        ("n_r_gt_1", sum(1 for v in r if v > 1)), ("n_sign_flip", sum(1 for x in pairs if x["u"] == 0)),
        ("share_sign_agree", sum(x["u"] for x in pairs) / n if n else None),
        ("geo_mean_r_sign_agreeing", math.exp(sum(lam) / len(lam)) if lam else None),
        ("n_z", len(az)), ("median_abs_z", med(az)), ("share_abs_z_gt_1", sum(1 for v in az if v > 1) / len(az) if az else None),
        ("share_abs_z_gt_1_96", sum(1 for v in az if v > 1.96) / len(az) if az else None)])


BOOT_KEYS = ("median_r", "share_sign_agree", "geo_mean_r_sign_agreeing", "median_abs_z")


def boot(pairs, mode, seed=SEED_P, reps=B):
    """Percentile bootstrap of BOOT_KEYS; mode 'paper' or 'family'."""
    if mode == "family":
        fams = sorted({x["family"] for x in pairs})
        units = [[x for x in pairs if x["family"] == f] for f in fams]
    else:
        units = [[x] for x in sorted(pairs, key=lambda x: x["paper_id"])]
    rng = random.Random(seed)
    k = len(units)
    vals = {key: [] for key in BOOT_KEYS}
    for _ in range(reps):
        pool = []
        for _j in range(k):
            pool += units[int(rng.random() * k)]
        c = core(pool)
        for key in BOOT_KEYS:
            if c[key] is not None:
                vals[key].append(c[key])
    return OrderedDict((key, dict(interval=Q.percentile_interval(sorted(v)), n_resamples=len(v))) for key, v in vals.items())


def analyse(pairs, with_engine=True):
    c = core(pairs)
    fl = Q.floor_met(c["n"], c["F"])
    out = OrderedDict([("stats", c), ("floor", dict(met=bool(fl), n=c["n"], F=c["F"], required=dict(n=FLOOR_N, F=FLOOR_F)))])
    if fl and with_engine:
        out["bootstrap_paper"] = boot(pairs, "paper")
        out["bootstrap_family"] = boot(pairs, "family")
        k = sum(x["u"] for x in pairs)
        out["share_sign_agree_exact_ci95"] = Q.cp_interval(k, c["n"])
        iv = out["bootstrap_paper"]["median_r"]["interval"]
        out["reading"] = "ours smaller" if iv[1] < 1 else ("ours larger" if iv[0] > 1 else "no shrinkage seen")
    else:
        out["reading"] = "descriptive only"
    return out


def leave_one_family_out(pairs):
    fams = sorted({x["family"] for x in pairs})
    rows = []
    for f in fams:
        sub = [x for x in pairs if x["family"] != f]
        c = core(sub)
        rows.append((f, c["median_r"], c["share_sign_agree"]))
    lo, hi = min(rows, key=lambda t: t[1]), max(rows, key=lambda t: t[1])
    so, sh = min(rows, key=lambda t: t[2]), max(rows, key=lambda t: t[2])
    return OrderedDict([("median_r", dict(min=lo[1], family_at_min=lo[0], max=hi[1], family_at_max=hi[0])),
                        ("share_sign_agree", dict(min=so[2], family_at_min=so[0], max=sh[2], family_at_max=sh[0]))])


FEATURES = OrderedDict([
    ("data_source", "data_source"), ("tier", "tier"), ("effort", "effort"), ("effort_S_vs_M_or_L", "effort_ml"), ("treated_states", "treated_group"),
    ("staggered", "staggered"), ("design", "design"), ("journal_group", "journal_group"), ("top5", "top5"), ("field", "field"), ("age_group", "age_group")])


def one_feature_tables(pairs):
    out = OrderedDict()
    for name, key in FEATURES.items():
        arms = OrderedDict()
        missing = sum(1 for x in pairs if x.get(key) is None)
        for a in sorted({x[key] for x in pairs if x.get(key) is not None}):
            sub = [x for x in pairs if x.get(key) == a]
            c = core(sub)
            arms[a] = OrderedDict([("n", c["n"]), ("F", c["F"]), ("too_few", c["n"] < MIN_ARM), ("median_r", c["median_r"]), ("share_sign_agree", c["share_sign_agree"]),
                                   ("median_abs_z", c["median_abs_z"]), ("n_z", c["n_z"])])
        ent = OrderedDict([("arms", arms), ("n_missing", missing)])
        big = [a for a, v in arms.items() if not v["too_few"]]
        if len(big) == 2:
            a0, a1 = big
            s0 = [x for x in pairs if x.get(key) == a0]
            s1 = [x for x in pairs if x.get(key) == a1]
            ent["difference_median_r"] = OrderedDict([("arm_a", a0), ("arm_b", a1), ("b_minus_a", med([x["r"] for x in s1]) - med([x["r"] for x in s0])),
                                                      ("family_bootstrap_ci95", boot_diff(s0, s1))])
        out[name] = ent
    return out


def boot_diff(s0, s1, reps=B, seed=SEED_P):
    fams = sorted({x["family"] for x in s0 + s1})
    by0 = {f: [x for x in s0 if x["family"] == f] for f in fams}
    by1 = {f: [x for x in s1 if x["family"] == f] for f in fams}
    rng = random.Random(seed)
    k = len(fams)
    vals = []
    for _ in range(reps):
        p0, p1 = [], []
        for _j in range(k):
            f = fams[int(rng.random() * k)]
            p0 += by0[f]
            p1 += by1[f]
        if p0 and p1:
            vals.append(med([x["r"] for x in p1]) - med([x["r"] for x in p0]))
    return dict(interval=Q.percentile_interval(sorted(vals)), n_resamples=len(vals))


# ---------------------------------------------------------------------------------------------
# joint model
# ---------------------------------------------------------------------------------------------
FIELDS = ("labor_tax", "health", "crime_guns_traffic")        # reference: education_family_elections_other
COLS = ["intercept", "R1b", "effort_M_or_L", "staggered", "top5", "year_since_2015_over_10"] + [f"field_{f}" for f in FIELDS]


def design_row(x):
    return [1.0, 1.0 if x["rung"] == "R1b" else 0.0, 1.0 if x["effort_ml"] == "M_or_L" else 0.0, 1.0 if x["staggered"] == "staggered" else 0.0,
            1.0 if x["top5"] == "top5" else 0.0, (x["year"] - 2015) / 10.0] + [1.0 if x["field"] == f else 0.0 for f in FIELDS]


def complete_for_model(x):
    return x["effort_ml"] is not None and x["staggered"] is not None and isinstance(x["year"], (int, float))


def ols(rows, key):
    X = np.array([design_row(x) for x in rows])
    y = np.array([x[key] for x in rows], float)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return beta


def regression(pairs, key, reps=B, seed=SEED_R):
    rows = [x for x in pairs if complete_for_model(x) and x.get(key) is not None]
    fams = sorted({x["family"] for x in rows})
    out = OrderedDict([("outcome", key), ("n", len(rows)), ("F", len(fams)), ("n_parameters", len(COLS)), ("n_dropped_missing_feature", sum(1 for x in pairs if x.get(key) is not None) - len(rows))])
    if len(rows) < len(COLS) + 5:
        out["note"] = "too few rows for the joint model: not fitted"
        return out
    beta = ols(rows, key)
    by = {f: [x for x in rows if x["family"] == f] for f in fams}
    rng = random.Random(seed)
    draws = []
    k = len(fams)
    for _ in range(reps):
        pool = []
        for _j in range(k):
            pool += by[fams[int(rng.random() * k)]]
        if len(pool) >= len(COLS):
            draws.append(ols(pool, key))
    D = np.array(draws)
    out["coefficients"] = OrderedDict((c, OrderedDict([("estimate", float(beta[i])), ("family_bootstrap_ci95", Q.percentile_interval(sorted(D[:, i].tolist())))])) for i, c in enumerate(COLS))
    out["n_resamples"] = len(draws)
    return out


# ---------------------------------------------------------------------------------------------
def write_csv(path, fields, rows):
    with open(path, "x", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("|".join(v) if isinstance(v, list) else v) for k, v in r.items()})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--version", type=int, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--reps", type=int, default=B, help="bootstrap resamples (tests only; the registered value is 9,999)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    problems, n_rec = verify_records()
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    snap_path, out_dir, version = Path(args.snapshot), Path(args.out_dir), args.version
    if not snap_path.is_absolute():
        snap_path = ROOT / snap_path
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    snap_bytes = snap_path.read_bytes()
    snap_sha = hashlib.sha256(snap_bytes).hexdigest()
    if version == 1 and snap_path.resolve() != V1_SNAPSHOT.resolve():
        sys.exit("version 1 is pinned to the snapshot of the registration: not run")
    snap = json.loads(snap_bytes)
    if snap.get("schema_version") != 5 or len(snap.get("papers") or []) != 503:
        sys.exit("snapshot schema or roster differs: not run")
    cat_bytes = Q.CATALOG.read_bytes()
    cat = {}
    for line in cat_bytes.decode("utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            cat[r["paper_id"]] = r
    effort = {}
    with open(JOBS, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                j = json.loads(line)
                effort[j["paper_id"]] = j.get("effort")
    rows, pairs0 = Q.evaluate_snapshot(snap, cat)
    pairs = add_features(pairs0, cat, effort)
    codes = OrderedDict((c, sum(1 for r in rows if r["code"] == c)) for c in Q.CODES)
    # author code and ports: reported, never in E
    by_id = {p["paper_id"]: p for p in snap["papers"]}
    non_indep = Counter()
    for p in snap["papers"]:
        origins = {r.get("code_origin") for r in p["runs"]}
        if "port_of_author_code" in origins:
            non_indep["papers_with_a_port_of_author_code_run"] += 1
        if any(r.get("rung") == "R0" for r in p["runs"]):
            non_indep["papers_with_an_R0_run"] += 1
        if origins - {"independent"}:
            non_indep["papers_with_any_non_independent_or_unknown_origin_run"] += 1
    n_in_E_with_non_indep = sum(1 for x in pairs if (by_id[x["paper_id"]]["primary"] or {}).get("code_origin") != "independent")
    primary = analyse(pairs)
    sets = OrderedDict()
    sets["a_without_not_comparable"] = analyse([x for x in pairs if x["grade_numeric"] != "NOT_COMPARABLE"])
    sets["b_target_verified"] = analyse([x for x in pairs if x["target_verified"]])
    sets["c_confirmed_pairs"] = analyse([x for x in pairs if x["certified"]], with_engine=False)
    sets["c_confirmed_pairs"]["pairs"] = [OrderedDict((k, x[k]) for k in ("paper_id", "slug", "p", "o", "r", "absz")) for x in pairs if x["certified"]]
    tables = one_feature_tables(pairs)
    reg_lam = regression(pairs, "lam")
    reg_u = regression(pairs, "u")
    me = Path(__file__).resolve()
    stamp = OrderedDict([("rule_id", "EX-1"), ("question", "REPGAP"), ("version", f"REPGAP-V{version}"), ("run_utc", utc_now()), ("snapshot_as_of", snap["as_of"]), ("snapshot_sha256", snap_sha),
                         ("catalogue_sha256", hashlib.sha256(cat_bytes).hexdigest()), ("code", {c: sha256_file(ROOT / c) for c in CODE}), ("prereg_sha256", sha256_file(PREREG)),
                         ("hash_records", n_rec), ("D", len(rows)), ("n", len(pairs)), ("F", primary["stats"]["F"]), ("coverage_codes", codes),
                         ("pairs_fingerprint_sha256", Q.fingerprint(pairs0)), ("B", args.reps), ("seeds", dict(paper_family=SEED_P, regression=SEED_R)), ("python", sys.version.split()[0]),
                         ("numpy", np.__version__), ("no_significance_label", True)])
    payload = Q.clean(OrderedDict([
        ("schema_version", "l3-meta-repgap-v1"), ("evidence_status", "EXPLORATORY (EX-1); E is the accessible finished part of the 503 papers"), ("stamp", stamp),
        ("coverage", OrderedDict([("D", len(rows)), ("codes", codes), ("author_code_and_ports_not_in_E", dict(non_indep)), ("papers_in_E_whose_primary_is_not_independent", n_in_E_with_non_indep),
                                  ("n_z_defined", primary["stats"]["n_z"])])),
        ("primary", primary), ("sets", sets), ("leave_one_family_out", leave_one_family_out(pairs)), ("one_feature_tables", tables),
        ("regression_log_ratio_sign_agreeing", reg_lam), ("regression_sign_agreement_lpm", reg_u), ("pairs", pairs),
        ("audit", dict(files_written_under_data_replication=0, l2_trees_opened=0, network=False))]))
    if not args.dry_run:
        if (out_dir / f"RESULTS_REPGAP_V{version}.json").exists():
            sys.exit("results exist: not overwritten")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "snapshot_used.json").write_bytes(snap_bytes)
        (out_dir / "paper_catalog_used.jsonl").write_bytes(cat_bytes)
        with open(out_dir / f"effort_map_V{version}.csv", "x", encoding="utf-8") as fh:
            fh.write("paper_id,effort\n" + "".join(f"{k},{v}\n" for k, v in sorted(effort.items())))
        write_csv(out_dir / f"REPGAP_PAIRS_V{version}.csv", ["paper_id", "slug", "year", "family", "rung", "data_source", "effort", "design", "staggered", "n_treated", "tier", "journal_group", "field",
                                                              "grade_numeric", "stage", "p", "se_pub", "o", "se_ours", "r", "u", "lam", "z", "absz"], pairs)
        write_csv(out_dir / f"REPGAP_COVERAGE_V{version}.csv", ["paper_id", "slug", "stage", "code", "family"], rows)
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        (out_dir / f"RESULTS_REPGAP_V{version}.json").write_text(text, encoding="utf-8")
        h = hashlib.sha256(text.encode()).hexdigest()
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(run_utc=stamp["run_utc"], snapshot_sha256=snap_sha, code_sha256=sha256_file(me), results_sha256=h)) + "\n")
        with open(LEDGER, "a", encoding="utf-8") as fh:
            st = primary["stats"]
            fh.write(json.dumps(Q.clean(OrderedDict([("question", "REPGAP"), ("version", stamp["version"]), ("run_utc", stamp["run_utc"]), ("as_of", snap["as_of"]), ("snapshot_sha256", snap_sha),
                                                    ("code_sha256", sha256_file(me)), ("n", st["n"]), ("F", st["F"]), ("median_r", st["median_r"]),
                                                    ("median_r_ci_paper", (primary.get("bootstrap_paper") or {}).get("median_r", {}).get("interval")),
                                                    ("share_sign_agree", st["share_sign_agree"]), ("reading", primary["reading"]), ("results_sha256", h)])), ensure_ascii=False) + "\n")
    print(json.dumps(OrderedDict([("stamp", payload["stamp"]), ("coverage", payload["coverage"]), ("primary", payload["primary"])]), ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
