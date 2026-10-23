#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version CAL-V1 (rule id EX-1): Q1 and D1 calibrated with the D2 negative-control reference.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_CAL_V1.md (hash record PREREG_AMEND_20261015_CAL_HASH.txt), hashed before any calibrated number; the results it
calibrates (Q1-V3, D1-V1, D2-V1, D3-V2) are read, never changed.  One rule: every random-timing line is widened by kappa-hat = SD of the standardised ATTs of the
negative-control series / the SD random timing gives (the D2 spread ratio).  A design, family or series is beyond the calibrated line when its statistic exceeds
kappa-hat times its line; the random-timing side (5% reference, mu_i, tau_i, the draws' counts) is unchanged.  Uncertainty: two family-block bootstraps that resample
kappa with the data (Q1 over its 47 policy families, D1 over the 24 families of the D1 designs), B = 9,999, percentile intervals.  Every statistic is exploratory: no
"significant" label, no multiplicity adjustment, no licensed sentence.

Reads L2 results only through the sweep files (the 28 design files of SWEEP-V1) and the four result JSONs; nothing under scripts/l2_pipeline or data/l2_pipeline is opened
for writing.  No network.  Only output: data/l3_meta/cal_v1/ (each file written once).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_cal.py [--dry-run] [--out-dir DIR] [--sweep-dir DIR]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_d1 as D1  # noqa: E402  (family statistics of D1-V1, design file reader)
import build_d3 as D3  # noqa: E402  (hash record of the D questions)
import build_sweep as B  # noqa: E402  (hash record parser, sweep records)
import sweep_core as S  # noqa: E402

ROOT = S.ROOT
RULE_ID, QUESTION, VERSION = "EX-1", "CAL", "CAL-V1"
SEED_Q = [20261011, 34, 1]
SEED_D = [20261011, 34, 2]
B_BOOT = 9999
NOMINAL = 0.05                      # the nominal reference rate of Q1-V3 (2.6 of 52)
FIT_TOL = 0.03                      # amendment 6: FITS when the calibrated crossing shares are within 3 points of the random-timing shares
OUT_DIR = ROOT / "data/l3_meta/cal_v1"
OUT_NAME = "RESULTS_CAL_V1.json"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_CAL_V1.md"
HASH_REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_CAL_HASH.txt"
EX1_JSON = ROOT / "data/l3_meta/ex1_v3/RESULTS_EX1_V3.json"
D1_JSON = ROOT / "data/l3_meta/d1_v1/RESULTS_D1_V1.json"
D2_JSON = ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"
D3_JSON = ROOT / "data/l3_meta/d3_v2/RESULTS_D3_V2.json"
CODE_FILES = ["scripts/l3_meta/build_cal.py", "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_d3.py", "scripts/l3_meta/build_d2.py", "scripts/l3_meta/build_n1.py"]
KAPPA_GRID = (1.0, 1.1, 1.2, "strict", "hat", 1.4, 1.5)
TOL = 1e-9


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def agree(x, y, tol=TOL):
    return abs(x - y) <= tol * max(1.0, abs(x), abs(y))


# ---------------------------------------------------------------------------------------------
# hash record
# ---------------------------------------------------------------------------------------------
def verify_cal_records(amend=AMEND, hash_rec=HASH_REC):
    """Problems between the CAL hash record and the files on disk.  Returns (problems, record)."""
    rec = B.parse_hash_record(hash_rec)
    problems = []
    want = rec["files"].get(S.rel(amend))
    if want is None or want != S.sha256_file(amend):
        problems.append(f"{Path(amend).name} differs from its hash record")
    for path, want in rec["listed"].items():
        if path.startswith("scripts/l2_pipeline/") and path.endswith(".py"):
            continue
        p = ROOT / path
        if not p.exists() or S.sha256_file(p) != want:
            problems.append(f"{path} differs from the CAL hash record")
    return problems, rec


# ---------------------------------------------------------------------------------------------
# pure statistics
# ---------------------------------------------------------------------------------------------
def sd_ratio(z, ref):
    """kappa = sample SD (n - 1) of the standardised ATTs / the reference SD of random timing."""
    z = np.asarray(z, float)
    return float(np.std(z, ddof=1) / ref)


def n_beyond(absx, line, kappa):
    """Number of statistics strictly beyond kappa times their line (kappa = 1 is the random-timing line)."""
    return int(np.sum(np.asarray(absx, float) > kappa * np.asarray(line, float)))


def family_ids(names, order):
    pos = {f: i for i, f in enumerate(order)}
    return np.array([pos[x] for x in names], dtype=int)


def d1_design_N(cell_M, cell_q, cell_design, n_designs, kappa):
    """N_i(kappa): families of design i beyond kappa times their line (integer array)."""
    flags = np.asarray(cell_M, float) > kappa * np.asarray(cell_q, float)
    return np.bincount(cell_design, weights=flags.astype(float), minlength=n_designs).astype(int)


def d1_p(N, nstar_list):
    """p_i = (1 + #{b : N*_ib >= N_i}) / (n_i + 1)."""
    return np.array([(1 + int(np.sum(ns >= n_)) ) / (len(ns) + 1) for n_, ns in zip(N, nstar_list)], float)


def d1_summary(N, mu, tau, p, ids, p_ge, kappa):
    """The calibrated D1 numbers of amendment 3.3 for one kappa (N: integer array over the designs)."""
    n = len(N)
    Z = (N - mu) / tau
    E = N - mu
    excess = sorted(((float(E[i]), ids[i]) for i in range(n)))[::-1]
    total = sum(e for e, _ in excess)
    top5 = sum(e for e, _ in excess[:5])
    return OrderedDict([
        ("kappa", float(kappa)), ("n_designs", n), ("theta", float(Z.mean())), ("e_D_mean_excess_per_design", float(E.mean())), ("excess_total", float(total)),
        ("families_beyond_T_D", int(N.sum())), ("families_expected_sum_mu", float(mu.sum())),
        ("designs_N_ge", {str(k): int(np.sum(N >= k)) for k in (1, 2, 3)}),
        ("designs_N_ge_expected", {str(k): float(sum(p_ge[i][k] for i in range(n))) for k in (1, 2, 3)}),
        ("designs_p_le_05", int(np.sum(p <= 0.05))), ("designs_p_le_05_expected", 0.05 * n),
        ("top5_designs", [d for _, d in excess[:5]]), ("excess_top5", float(top5)), ("excess_top5_share", (top5 / total) if total > 0 else None),
        ("Z_min", float(Z.min())), ("Z_max", float(Z.max())), ("Z_median", float(np.median(Z))), ("share_Z_positive", float(np.mean(Z > 0))),
    ])


def multiplicity(draw, F):
    """c[b, f] = number of times block f is drawn in replicate b (draw: B x F integers in [0, F))."""
    Bn = draw.shape[0]
    flat = (draw + F * np.arange(Bn)[:, None]).ravel()
    return np.bincount(flat, minlength=Bn * F).reshape(Bn, F).astype(float)


def one_hot(idx, F):
    M = np.zeros((len(idx), F))
    M[np.arange(len(idx)), idx] = 1.0
    return M


def kappa_replicates(c, nc_blk, nc_z, ref, F):
    """kappa* and the mean z* of every replicate from the block sums of the negative-control series (NaN where fewer than 2 series are drawn)."""
    nf = np.bincount(nc_blk, minlength=F).astype(float)
    s1 = np.bincount(nc_blk, weights=nc_z, minlength=F)
    s2 = np.bincount(nc_blk, weights=nc_z ** 2, minlength=F)
    n = c @ nf
    a = c @ s1
    b2 = c @ s2
    with np.errstate(divide="ignore", invalid="ignore"):
        var = np.where(n > 1, (b2 - a * a / n) / (n - 1), np.nan)
        ks = np.sqrt(np.maximum(var, 0.0)) / ref
        zm = np.where(n > 0, a / n, np.nan)
    return ks, zm


def pct(values, lo=2.5, hi=97.5):
    a = np.asarray(values, float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    q = np.percentile(a, [lo, hi])
    return [float(q[0]), float(q[1])]


def interval_block(values):
    a = np.asarray(values, float)
    return {"interval95": pct(a), "replicates": int(a.size), "defined": int(np.isfinite(a).sum()), "median": float(np.nanmedian(a)) if np.isfinite(a).any() else None}


def bootstrap_q1(absb, cut, blk, F, nc_blk, nc_z, nc_beyond1, ref, kappa_hat, B_=B_BOOT, seed=SEED_Q):
    """Q1 bootstrap (amendment 4.1).  Returns the replicate arrays of the JOINT, DATA and KAPPA rates, the uncalibrated DATA rate and the rate-rule difference."""
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, F, size=(B_, F))
    c = multiplicity(draw, F)
    nQ = np.bincount(blk, minlength=F).astype(float)
    INC = one_hot(blk, F)
    ks, zm = kappa_replicates(c, nc_blk, nc_z, ref, F)
    with np.errstate(invalid="ignore"):
        H = absb[None, :] > ks[:, None] * cut[None, :]
    hits = H.astype(float) @ INC
    den = c @ nQ
    joint = (c * hits).sum(axis=1) / den
    joint = np.where(np.isfinite(ks), joint, np.nan)
    h0 = (absb > kappa_hat * cut).astype(float) @ INC
    h1 = (absb > cut).astype(float) @ INC
    data = (c @ h0) / den
    data1 = (c @ h1) / den
    kap = np.where(np.isfinite(ks), H.mean(axis=1), np.nan)
    ns_f = np.bincount(nc_blk, minlength=F).astype(float)
    bs_f = np.bincount(nc_blk, weights=np.asarray(nc_beyond1, float), minlength=F)
    with np.errstate(divide="ignore", invalid="ignore"):
        ncrate = np.where(c @ ns_f > 0, (c @ bs_f) / (c @ ns_f), np.nan)
    return dict(kappa=ks, mean_z=zm, joint=joint, data=data, kappa_only=kap, data_uncal=data1, nc_series_rate=ncrate, rate_rule=data1 - ncrate)


def bootstrap_d1(cell_M, cell_q, cell_design, des_blk, cell_blk, cell_nc, mu, tau, F, nc_blk, nc_z, ref, kappa_hat, B_=B_BOOT, seed=SEED_D):
    """D1 bootstrap (amendment 4.2): theta and the mean excess per design, JOINT / DATA / KAPPA, the uncalibrated DATA theta and the rate-rule difference."""
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, F, size=(B_, F))
    c = multiplicity(draw, F)
    n_d = len(mu)
    nD = np.bincount(des_blk, minlength=F).astype(float)
    INCD = one_hot(des_blk, F)
    CD = one_hot(cell_design, n_d)
    ks, zm = kappa_replicates(c, nc_blk, nc_z, ref, F)
    with np.errstate(invalid="ignore"):
        H = cell_M[None, :] > ks[:, None] * cell_q[None, :]
    N = H.astype(float) @ CD
    E = N - mu[None, :]
    Z = E / tau[None, :]
    den = c @ nD
    joint_theta = ((Z @ INCD) * c).sum(axis=1) / den
    joint_e = ((E @ INCD) * c).sum(axis=1) / den
    bad = ~np.isfinite(ks)
    joint_theta[bad] = np.nan
    joint_e[bad] = np.nan

    def fixed(kappa):
        N0 = d1_design_N(cell_M, cell_q, cell_design, n_d, kappa).astype(float)
        E0 = N0 - mu
        return (c @ (((E0 / tau)) @ INCD)) / den, (c @ (E0 @ INCD)) / den, float((E0 / tau).mean()), float(E0.mean())

    data_theta, data_e, _, _ = fixed(kappa_hat)
    data1_theta, data1_e, _, _ = fixed(1.0)
    kap_theta = np.where(bad, np.nan, Z.mean(axis=1))
    kap_e = np.where(bad, np.nan, E.mean(axis=1))
    cb = np.bincount(cell_blk, minlength=F).astype(float)
    b1 = np.bincount(cell_blk, weights=(cell_M > cell_q).astype(float), minlength=F)
    ncm = np.bincount(cell_blk[cell_nc], minlength=F).astype(float)
    ncB = np.bincount(cell_blk[cell_nc], weights=(cell_M[cell_nc] > cell_q[cell_nc]).astype(float), minlength=F)
    with np.errstate(divide="ignore", invalid="ignore"):
        ncrate = np.where(c @ ncm > 0, (c @ ncB) / (c @ ncm), np.nan)
        allrate = (c @ b1) / (c @ cb)
    return dict(kappa=ks, mean_z=zm, joint_theta=joint_theta, joint_e=joint_e, data_theta=data_theta, data_e=data_e, kappa_only_theta=kap_theta, kappa_only_e=kap_e,
                data_uncal_theta=data1_theta, data_uncal_e=data1_e, nc_cell_rate=ncrate, all_cell_rate=allrate, rate_rule=allrate - ncrate)


# ---------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------
def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def q1_designs(ex1):
    """The Q1-V3 rule `has_noise_line`: estimated, finite b, positive noise SD and cutoff, support_pass not false."""
    out = []
    for r in ex1["designs"]:
        if (r.get("status") == "estimated" and finite(r.get("b")) and finite(r.get("noise_sd")) and r["noise_sd"] > 0 and finite(r.get("noise_cutoff"))
                and r["noise_cutoff"] > 0 and r.get("support_pass") is not False):
            out.append(dict(design_id=r["design_id"], policy_family=r["policy_family"], group3=r.get("group3"), absb=abs(float(r["b"])), cut=float(r["noise_cutoff"]),
                            recorded_exceed=r.get("recorded_exceed")))
    return out


def design_arrays(f, st):
    """Arrays of the series a D1-EVAL design used (D1 definition) and its placebo family counts N*_ib: z = a/sd, own line (rank ceil(.95 n) of |placebo|),
    the family statistics M_if and lines q_if recomputed from the draws, nstar."""
    sids = list(f["sids"])
    pos = {s: i for i, s in enumerate(sids)}
    idx = [pos[s] for s in st["series_used"]]
    placebo = np.asarray(f["placebo"], float)
    common = np.isfinite(placebo[idx]).all(axis=0)
    n = int(common.sum())
    if n != st["n_draws"]:
        raise ValueError("common draw set differs from the D1 statistics")
    PC = placebo[np.ix_(idx, np.where(common)[0])]
    sd = PC.std(axis=1, ddof=1)
    att = np.asarray(f["att"], float)[idx]
    rank = math.ceil(0.95 * n) - 1
    own_cut = np.sort(np.abs(PC), axis=1)[:, rank]
    fam = [f["fam"][i] for i in idx]
    z = att / sd
    zstar = PC / sd[:, None]
    nstar = np.zeros(n, dtype=int)
    fam_mq = OrderedDict()
    for fm in sorted(set(fam)):
        rows = [j for j, x in enumerate(fam) if x == fm]
        Mstar = np.max(np.abs(zstar[rows]), axis=0)
        q = float(np.sort(Mstar)[rank])
        nstar += (Mstar > q).astype(int)
        fam_mq[fm] = (float(np.max(np.abs(z[rows]))), q, float(np.mean(Mstar > q)))
    return dict(sids=[sids[i] for i in idx], fam=fam, z=z, abs_att=np.abs(att), own_cut=own_cut, exp_own=(np.abs(PC) > own_cut[:, None]).mean(axis=1), n=n, nstar=nstar, fam_mq=fam_mq)


def sweep_inputs(d1j, d2j, sweep_dir):
    """Re-derive the 28 D1 designs from the sweep files, check them against D1-V1, and return the arrays the calibration needs."""
    sweep_run = S.read_json(Path(sweep_dir) / "SWEEP_RUN.json")
    swept = {d["design_id"]: d for d in sweep_run["designs"] if d["status"] == "OK"}
    d1_eval = {r["design_id"]: r for r in d1j["designs"] if r.get("code") == "EVAL"}
    cells2 = {(c["design_id"], c["family"]): c for c in d2j["cells"]}
    problems = []
    designs = OrderedDict()
    for did in sorted(d1_eval):
        d = swept.get(did)
        if d is None:
            problems.append(f"{did}: not in SWEEP_RUN.json")
            continue
        path = Path(sweep_dir) / Path(d["file"]).name
        if S.sha256_file(path) != d["sha256"]:
            problems.append(f"sweep file of {did} differs from SWEEP_RUN.json")
            continue
        f = D1.load_design_file(path)
        st = D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], ())
        if st["code"] != "EVAL":
            problems.append(f"{did}: D1 code {st['code']} on recomputation")
            continue
        arr = design_arrays(f, st)
        row = d1_eval[did]
        # identities with D1-V1
        for k in ("N", "mu", "tau", "Z", "p"):
            if not agree(float(st[k]), float(row[k])):
                problems.append(f"{did}: {k} {st[k]!r} differs from D1-V1 {row[k]!r}")
        if not agree(float(st["tau"]), float(np.std(arr["nstar"], ddof=1))) or not agree(float(st["mu"]), float(arr["nstar"].mean())):
            problems.append(f"{did}: mu/tau from the recomputed placebo family counts differ from design_statistics")
        for k in (1, 2, 3):
            if not agree(float(np.mean(arr["nstar"] >= k)), float(row["P_Nstar_ge"][str(k)])):
                problems.append(f"{did}: P(N* >= {k}) differs from D1-V1")
        for fm, (M, q, r) in arr["fam_mq"].items():
            s = row["family_stats"][fm]
            if not (agree(M, s["M"]) and agree(q, s["q"]) and agree(r, s["share_draws_beyond"])):
                problems.append(f"{did}|{fm}: M, q or r differs from D1-V1")
            c2 = cells2.get((did, fm))
            if c2 is None or not (agree(M, c2["M"]) and agree(q, c2["q"]) and agree(r, c2["r"])):
                problems.append(f"{did}|{fm}: differs from the D2-V1 cell")
        if sorted(arr["fam_mq"]) != sorted(row["family_stats"]):
            problems.append(f"{did}: families differ from D1-V1")
        designs[did] = dict(arr=arr, row=row, policy_family=row["policy_family"], n_draws=st["n_draws"])
    return designs, problems, sweep_run


# ---------------------------------------------------------------------------------------------
def build_arrays(designs, d2j):
    """Cell arrays of the 28 designs, the negative-control series (primary and strict sets) and the policy groups."""
    ids = list(designs)
    fam_cells = []
    for i, did in enumerate(ids):
        for fm in designs[did]["arr"]["fam_mq"]:
            fam_cells.append((i, did, fm))
    cells2 = {(c["design_id"], c["family"]): c for c in d2j["cells"]}
    cell_design = np.array([i for i, _, _ in fam_cells], dtype=int)
    cell_M = np.array([designs[d]["arr"]["fam_mq"][f][0] for _, d, f in fam_cells])
    cell_q = np.array([designs[d]["arr"]["fam_mq"][f][1] for _, d, f in fam_cells])
    cell_r = np.array([designs[d]["arr"]["fam_mq"][f][2] for _, d, f in fam_cells])
    cell_code = [cells2[(d, f)]["code"] for _, d, f in fam_cells]
    cell_nc = np.array([c == "NC" for c in cell_code])
    cell_strict = np.array([bool(cells2[(d, f)]["flags"]["strict"]) and cells2[(d, f)]["code"] == "NC" for _, d, f in fam_cells])
    cell_group = [cells2[(d, f)]["policy_group"] for _, d, f in fam_cells]
    # NC series
    ser = []
    for i, did in enumerate(ids):
        a = designs[did]["arr"]
        for j, (sid, fm) in enumerate(zip(a["sids"], a["fam"])):
            c = cells2[(did, fm)]
            if c["code"] == "NC":
                ser.append(dict(design_id=did, sid=sid, family=fm, policy_family=designs[did]["policy_family"], policy_group=c["policy_group"], strict=bool(c["flags"]["strict"]),
                                z=float(a["z"][j]), abs_att=float(a["abs_att"][j]), own_cut=float(a["own_cut"][j]), exp_own=float(a["exp_own"][j])))
    return dict(ids=ids, fam_cells=fam_cells, cell_design=cell_design, cell_M=cell_M, cell_q=cell_q, cell_r=cell_r, cell_code=cell_code, cell_nc=cell_nc, cell_strict=cell_strict,
                cell_group=cell_group, series=ser)


def d1_eval_at(data, designs, kappa):
    ids = data["ids"]
    n_d = len(ids)
    mu = np.array([designs[d]["row"]["mu"] for d in ids])
    tau = np.array([designs[d]["row"]["tau"] for d in ids])
    nstar = [designs[d]["arr"]["nstar"] for d in ids]
    p_ge = [{k: float(np.mean(ns >= k)) for k in (1, 2, 3)} for ns in nstar]
    N = d1_design_N(data["cell_M"], data["cell_q"], data["cell_design"], n_d, kappa)
    p = d1_p(N, nstar)
    out = d1_summary(N, mu, tau, p, ids, p_ge, kappa)
    return out, N, p


def q1_eval_at(q1, kappa):
    absb = np.array([r["absb"] for r in q1])
    cut = np.array([r["cut"] for r in q1])
    flags = absb > kappa * cut
    k = int(flags.sum())
    n = len(q1)
    return OrderedDict([("kappa", float(kappa)), ("n", n), ("k", k), ("rate", k / n), ("expected_nominal", NOMINAL * n), ("excess_rate", k / n - NOMINAL), ("excess_designs", k - NOMINAL * n)]), flags


def d3_counts(pairs, kappa):
    """Amendment 3.4: targets and signals among the EVAL pairs of D3-V2 at kappa (pairs: dicts with the `numbers` block of the results file)."""
    ta = [abs(p["numbers"]["a"]) > kappa * p["numbers"]["cutoff_a"] for p in pairs]
    sg = [t and bool(p["numbers"]["opposite"]) and abs(p["numbers"]["c"]) > kappa * p["numbers"]["cutoff_c"] for t, p in zip(ta, pairs)]
    return OrderedDict([("pairs", len(pairs)), ("targets_beyond", int(sum(ta))), ("signals", int(sum(sg)))])


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
    p2, rec = verify_cal_records()
    problems += p2
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems))
    live_sha = S.sha256_file(S.LIVE)
    if rec["listed"].get(S.rel(S.LIVE)) != live_sha:
        sys.exit("live set differs from the CAL hash record: not run")

    ex1, d1j, d2j, d3j = (S.read_json(p) for p in (EX1_JSON, D1_JSON, D2_JSON, D3_JSON))
    for name, j in (("EX1", ex1), ("D1", d1j), ("D2", d2j), ("D3", d3j)):
        if j["stamp"]["live_set_sha256"] != live_sha:
            sys.exit(f"{name} result was made with another live set: not run")
    d2q = d2j["question"]["D2"]
    if d2q["cell_codes"].get("CODING_ERROR", 0) != 0:
        sys.exit("D2 has coding errors: the NC membership rule of the amendment assumed none")
    if not (d2q["E1"]["run"] and d2q["estimate_nc_cells"]["m"] >= 40 and d2q["estimate_nc_cells"]["F"] >= 8):
        sys.exit("D2-V1 floors not met: CAL-V1 not run")

    q1 = q1_designs(ex1)
    q1x = ex1["questions"]["Q1"]
    designs, prob, sweep_run = sweep_inputs(d1j, d2j, sweep_dir)
    if prob:
        sys.exit("identity with D1-V1 / D2-V1 failed, not run: " + "; ".join(prob[:12]))
    data = build_arrays(designs, d2j)
    ids = data["ids"]
    ser = data["series"]
    n_d, n_cells = len(ids), len(data["cell_M"])
    ref = float(d2q["empirical_null_width_nc_series"]["reference_mean_sd"])
    ref_strict = float(d2q["empirical_null_width_strict_set"]["reference_mean_sd"])
    z_nc = np.array([s["z"] for s in ser])
    strict_mask = np.array([s["strict"] for s in ser])
    kappa_hat = sd_ratio(z_nc, ref)
    kappa_strict = sd_ratio(z_nc[strict_mask], ref_strict)
    # identities with D2-V1 and Q1-V3 (amendment 3.1 and 3.5)
    ident = OrderedDict()
    ident["nc_series"] = (len(ser), d2q["empirical_null_width_nc_series"]["n_series"])
    ident["strict_series"] = (int(strict_mask.sum()), d2q["empirical_null_width_strict_set"]["n_series"])
    ident["kappa_hat_vs_D2"] = (kappa_hat, d2q["empirical_null_width_nc_series"]["ratio_observed_to_reference_mean"])
    ident["kappa_strict_vs_D2"] = (kappa_strict, d2q["empirical_null_width_strict_set"]["ratio_observed_to_reference_mean"])
    ident["mean_z_vs_D2"] = (float(z_nc.mean()), d2q["empirical_null_width_nc_series"]["observed_mean_z"])
    q1_at1, q1_flags1 = q1_eval_at(q1, 1.0)
    ident["q1_k_at_1"] = (q1_at1["k"], q1x["k"])
    ident["q1_n"] = (len(q1), q1x["n"])
    ident["q1_designs_at_1"] = (sorted(r["design_id"] for r, f in zip(q1, q1_flags1) if f), sorted(q1x["exceeding_designs"]))
    d1_at1, N1, p1 = d1_eval_at(data, designs, 1.0)
    d1q = d1j["question"]["D1"]
    ident["d1_theta_at_1"] = (d1_at1["theta"], d1q["theta_D1_plain_mean_Z"])
    ident["d1_T_D_at_1"] = (d1_at1["families_beyond_T_D"], d1q["series_calibration"]["families_beyond_line"])
    ident["d1_sum_mu"] = (d1_at1["families_expected_sum_mu"], d1q["series_calibration"]["families_beyond_expected"])
    ident["d1_p_le_05_at_1"] = (d1_at1["designs_p_le_05"], d1q["pattern_counts"]["designs_p_le_05"])
    ident["d1_N_ge_at_1"] = (d1_at1["designs_N_ge"], {str(k): v for k, v in d1q["pattern_counts"]["designs_N_ge"].items()})
    ident["d1_top5_at_1"] = (d1_at1["top5_designs"], d1q["pattern_counts"]["top5_designs"])
    ident["d1_top5_share_at_1"] = (d1_at1["excess_top5_share"], d1q["pattern_counts"]["excess_top5_share"])
    for i, did in enumerate(ids):
        ident[f"d1_Z_{did}"] = (float((N1[i] - designs[did]["row"]["mu"]) / designs[did]["row"]["tau"]), designs[did]["row"]["Z"])
        ident[f"d1_p_{did}"] = (float(p1[i]), designs[did]["row"]["p"])
    nc_cells = data["cell_nc"]
    ident["nc_cells_beyond_at_1"] = (int(np.sum(data["cell_M"][nc_cells] > data["cell_q"][nc_cells])), d2q["estimate_nc_cells"]["n_beyond"])
    ident["nc_cells"] = (int(nc_cells.sum()), d2q["estimate_nc_cells"]["m"])
    nc_beyond1 = np.array([s["abs_att"] > s["own_cut"] for s in ser])
    ident["nc_series_beyond_at_1"] = (int(nc_beyond1.sum()), d2q["series_level_nc"]["series_beyond_own_line"])
    bad = []
    for k, (got, want) in ident.items():
        if isinstance(got, (int, float)) and not isinstance(got, bool):
            ok = agree(float(got), float(want))
        else:
            ok = got == want if not (got is None or want is None) else got is want
        if not ok:
            bad.append(f"{k}: {got!r} vs {want!r}")
    if bad:
        sys.exit("identity at kappa = 1 failed, not run: " + "; ".join(bad[:12]))

    # blocks
    blk_names_q = sorted({r["policy_family"] for r in q1})
    Fq = len(blk_names_q)
    q_blk = family_ids([r["policy_family"] for r in q1], blk_names_q)
    absb = np.array([r["absb"] for r in q1])
    cut = np.array([r["cut"] for r in q1])
    nc_pf = [s["policy_family"] for s in ser]
    if not set(nc_pf) <= set(blk_names_q):
        sys.exit("a negative-control policy family is not a Q1 policy family")
    ncq_blk = family_ids(nc_pf, blk_names_q)
    blk_names_d = sorted({designs[d]["policy_family"] for d in ids})
    Fd = len(blk_names_d)
    des_blk = family_ids([designs[d]["policy_family"] for d in ids], blk_names_d)
    cell_blk = des_blk[data["cell_design"]]
    ncd_blk = family_ids(nc_pf, blk_names_d)
    mu = np.array([designs[d]["row"]["mu"] for d in ids])
    tau = np.array([designs[d]["row"]["tau"] for d in ids])

    # point results
    kq = bootstrap_q1(absb, cut, q_blk, Fq, ncq_blk, z_nc, nc_beyond1, ref, kappa_hat)
    kd = bootstrap_d1(data["cell_M"], data["cell_q"], data["cell_design"], des_blk, cell_blk, nc_cells, mu, tau, Fd, ncd_blk, z_nc, ref, kappa_hat)
    kappa_int = pct(kd["kappa"])
    lo_hi = {"lo": kappa_int[0], "hi": kappa_int[1]}
    q1_hat, q1_flags_hat = q1_eval_at(q1, kappa_hat)
    d1_hat, N_hat, p_hat = d1_eval_at(data, designs, kappa_hat)

    def q1_block(kappa):
        r, fl = q1_eval_at(q1, kappa)
        return r

    def d1_short(kappa):
        r, N, p = d1_eval_at(data, designs, kappa)
        return OrderedDict([(k, r[k]) for k in ("kappa", "theta", "e_D_mean_excess_per_design", "families_beyond_T_D", "designs_p_le_05")] + [("designs_N_ge_2", r["designs_N_ge"]["2"])])

    grid = []
    for g in KAPPA_GRID:
        kk = kappa_strict if g == "strict" else kappa_hat if g == "hat" else g
        grid.append(OrderedDict([("label", g if isinstance(g, str) else f"{g:g}"), ("Q1", q1_block(kk)), ("D1", d1_short(kk))]))
    grid.append(OrderedDict([("label", "kappa_interval_lo"), ("Q1", q1_block(lo_hi["lo"])), ("D1", d1_short(lo_hi["lo"]))]))
    grid.append(OrderedDict([("label", "kappa_interval_hi"), ("Q1", q1_block(lo_hi["hi"])), ("D1", d1_short(lo_hi["hi"]))]))

    q1_rows = []
    for r, f1, fh in zip(q1, q1_flags1, q1_flags_hat):
        q1_rows.append(OrderedDict([("design_id", r["design_id"]), ("policy_family", r["policy_family"]), ("group3", r["group3"]), ("swept", r["design_id"] in designs),
                                    ("rho", r["absb"] / r["cut"]), ("beyond_at_1", bool(f1)), ("beyond_at_kappa_hat", bool(fh)), ("beyond_at_kappa_strict", bool(r["absb"] > kappa_strict * r["cut"]))]))

    def split(pred):
        sel = [x for x in q1_rows if pred(x)]
        return OrderedDict([("n", len(sel)), ("k_at_1", sum(x["beyond_at_1"] for x in sel)), ("k_at_kappa_hat", sum(x["beyond_at_kappa_hat"] for x in sel)),
                            ("k_at_kappa_strict", sum(x["beyond_at_kappa_strict"] for x in sel))])

    q1_splits = OrderedDict([(f"group3={g}", split(lambda x, g=g: x["group3"] == g)) for g in sorted({x["group3"] for x in q1_rows})])
    q1_splits["swept"] = split(lambda x: x["swept"])
    q1_splits["not_swept"] = split(lambda x: not x["swept"])

    d1_rows = []
    for i, did in enumerate(ids):
        d1_rows.append(OrderedDict([("design_id", did), ("policy_family", designs[did]["policy_family"]), ("n_draws", designs[did]["n_draws"]), ("n_families", len(designs[did]["arr"]["fam_mq"])),
                                    ("mu", float(mu[i])), ("tau", float(tau[i])), ("N_at_1", int(N1[i])), ("Z_at_1", float((N1[i] - mu[i]) / tau[i])), ("p_at_1", float(p1[i])),
                                    ("N_at_kappa_hat", int(N_hat[i])), ("Z_at_kappa_hat", float((N_hat[i] - mu[i]) / tau[i])), ("p_at_kappa_hat", float(p_hat[i]))]))
    beyond_cells = []
    for k_, (i, did, fm) in enumerate(data["fam_cells"]):
        rho = float(data["cell_M"][k_] / data["cell_q"][k_])
        if rho > 1.0:
            beyond_cells.append(OrderedDict([("design_id", did), ("family", fm), ("rho", rho), ("nc_cell", bool(data["cell_nc"][k_])), ("beyond_at_kappa_hat", bool(rho > kappa_hat)),
                                             ("beyond_at_kappa_strict", bool(rho > kappa_strict))]))
    nc_split = OrderedDict()
    for label, mask in (("nc_cells", data["cell_nc"]), ("other_cells", ~data["cell_nc"])):
        M_, q_, r_ = data["cell_M"][mask], data["cell_q"][mask], data["cell_r"][mask]
        nc_split[label] = OrderedDict([("cells", int(mask.sum())), ("beyond_at_1", int(np.sum(M_ > q_))), ("beyond_at_kappa_hat", int(np.sum(M_ > kappa_hat * q_))),
                                       ("beyond_at_kappa_strict", int(np.sum(M_ > kappa_strict * q_))), ("expected_sum_r", float(r_.sum()))])

    # fit of the rule to the negative controls (amendment 5.4)
    abs_att = np.array([s["abs_att"] for s in ser])
    own_cut = np.array([s["own_cut"] for s in ser])
    exp_own = np.array([s["exp_own"] for s in ser])
    fit = OrderedDict()
    cell_share_hat = float(np.mean(data["cell_M"][nc_cells] > kappa_hat * data["cell_q"][nc_cells]))
    cell_share_1 = float(np.mean(data["cell_M"][nc_cells] > data["cell_q"][nc_cells]))
    cell_exp = float(data["cell_r"][nc_cells].mean())
    ser_share_hat = float(np.mean(abs_att > kappa_hat * own_cut))
    ser_share_1 = float(np.mean(abs_att > own_cut))
    ser_exp = float(exp_own.mean())
    sm = data["cell_strict"]
    cell_share_strict = float(np.mean(data["cell_M"][sm] > kappa_strict * data["cell_q"][sm]))
    cell_exp_strict = float(data["cell_r"][sm].mean())
    ser_share_strict = float(np.mean(abs_att[strict_mask] > kappa_strict * own_cut[strict_mask]))
    ser_exp_strict = float(exp_own[strict_mask].mean())
    fit["primary"] = OrderedDict([("cells", int(nc_cells.sum())), ("cell_share_beyond_at_1", cell_share_1), ("cell_share_beyond_at_kappa_hat", cell_share_hat), ("cell_share_expected", cell_exp),
                                  ("series", len(ser)), ("series_share_beyond_at_1", ser_share_1), ("series_share_beyond_at_kappa_hat", ser_share_hat), ("series_share_expected", ser_exp),
                                  ("within_3_points", bool(abs(cell_share_hat - cell_exp) <= FIT_TOL and abs(ser_share_hat - ser_exp) <= FIT_TOL))])
    fit["strict"] = OrderedDict([("cells", int(sm.sum())), ("cell_share_beyond_at_kappa_strict", cell_share_strict), ("cell_share_expected", cell_exp_strict),
                                 ("series", int(strict_mask.sum())), ("series_share_beyond_at_kappa_strict", ser_share_strict), ("series_share_expected", ser_exp_strict),
                                 ("within_3_points", bool(abs(cell_share_strict - cell_exp_strict) <= FIT_TOL and abs(ser_share_strict - ser_exp_strict) <= FIT_TOL))])
    fit["label"] = "FITS" if fit["primary"]["within_3_points"] else "FITS POORLY"

    # heterogeneity (amendment 5.5)
    groups = sorted({s["policy_group"] for s in ser})
    het = OrderedDict()
    loo = {}
    for g in groups:
        zg = np.array([s["z"] for s in ser if s["policy_group"] == g])
        rest = np.array([s["z"] for s in ser if s["policy_group"] != g])
        het[g] = OrderedDict([("series", int(zg.size)), ("sd_z", float(np.std(zg, ddof=1)) if zg.size > 1 else None), ("kappa_without_group", sd_ratio(rest, ref) if rest.size > 1 else None)])
        loo[g] = het[g]["kappa_without_group"]
    loo_valid = {g: v for g, v in loo.items() if v is not None}
    gmin, gmax = min(loo_valid, key=loo_valid.get), max(loo_valid, key=loo_valid.get)

    # D3 (amendment 3.4)
    d3_pairs = [p for p in d3j["pairs"] if p.get("code") == "EVAL"]
    d3 = OrderedDict()
    for label, kk in (("at_1", 1.0), ("at_kappa_hat", kappa_hat), ("at_kappa_strict", kappa_strict)):
        d3[label] = d3_counts(d3_pairs, kk)
    d3["stored_at_1"] = OrderedDict([("targets_beyond", int(sum(1 for p in d3_pairs if p["numbers"]["beyond_a"]))), ("signals", int(sum(1 for p in d3_pairs if p["numbers"]["signal"])))])
    if (d3["at_1"]["targets_beyond"], d3["at_1"]["signals"]) != (d3["stored_at_1"]["targets_beyond"], d3["stored_at_1"]["signals"]):
        sys.exit("D3 counts at kappa = 1 differ from D3-V2: not run")

    # rate rule (amendment 5.6)
    pi_ncs = float(nc_beyond1.mean())
    pi_ncc = float(np.mean(data["cell_M"][nc_cells] > data["cell_q"][nc_cells]))
    allcells_beyond = int(np.sum(data["cell_M"] > data["cell_q"]))
    rate_rule = OrderedDict([
        ("Q1", OrderedDict([("designs_beyond_k", q1_at1["k"]), ("n", q1_at1["n"]), ("rate", q1_at1["rate"]), ("nc_series_rate", pi_ncs), ("difference", q1_at1["rate"] - pi_ncs),
                            ("expected_designs_from_nc_rate", pi_ncs * q1_at1["n"]), ("joint_interval95_of_difference", pct(kq["rate_rule"])), ("nc_series_rate_interval95", pct(kq["nc_series_rate"]))])),
        ("D1", OrderedDict([("families_beyond", allcells_beyond), ("cells", n_cells), ("rate", allcells_beyond / n_cells), ("nc_cell_rate", pi_ncc), ("difference", allcells_beyond / n_cells - pi_ncc),
                            ("expected_families_from_nc_rate", pi_ncc * n_cells), ("joint_interval95_of_difference", pct(kd["rate_rule"])), ("nc_cell_rate_interval95", pct(kd["nc_cell_rate"]))])),
    ])

    # intervals
    def trio(joint, data_, kap):
        return OrderedDict([("joint", interval_block(joint)), ("data_only_kappa_fixed", interval_block(data_)), ("kappa_only_data_fixed", interval_block(kap))])

    q1_int = OrderedDict([
        ("rate", trio(kq["joint"], kq["data"], kq["kappa_only"])), ("excess_rate", trio(kq["joint"] - NOMINAL, kq["data"] - NOMINAL, kq["kappa_only"] - NOMINAL)),
        ("uncalibrated_rate_same_method", interval_block(kq["data_uncal"])), ("uncalibrated_rate_V3_percentile_interval", q1x["family_bootstrap"]["interval95"]),
    ])
    d1_int = OrderedDict([
        ("theta", trio(kd["joint_theta"], kd["data_theta"], kd["kappa_only_theta"])), ("e_D_mean_excess_per_design", trio(kd["joint_e"], kd["data_e"], kd["kappa_only_e"])),
        ("uncalibrated_theta_same_method", interval_block(kd["data_uncal_theta"])), ("uncalibrated_theta_D1V1_E1_interval", d1q["E1"]["result"]["interval95"]),
    ])
    kappa_block = OrderedDict([
        ("kappa_hat", kappa_hat), ("kappa_strict", kappa_strict), ("sd_z_nc", float(np.std(z_nc, ddof=1))), ("ref", ref), ("ref_strict", ref_strict), ("n_series", len(ser)), ("n_series_strict", int(strict_mask.sum())),
        ("F_blocks_nc", len({s["policy_family"] for s in ser})), ("interval95_BD", kappa_int), ("interval95_BQ", pct(kq["kappa"])), ("share_replicates_below_1_BD", float(np.mean(kd["kappa"][np.isfinite(kd["kappa"])] < 1.0))),
        ("mean_z_nc", float(z_nc.mean())), ("mean_z_interval95_BD", pct(kd["mean_z"])), ("leave_one_group_out_min", OrderedDict([("group", gmin), ("kappa", loo_valid[gmin])])),
        ("leave_one_group_out_max", OrderedDict([("group", gmax), ("kappa", loo_valid[gmax])])),
        ("reading_guide", "WIDER" if kappa_int[0] > 1.0 else "NOT SEPARATED FROM 1"),
    ])

    def lab_q(iv):
        lo, hi = iv["interval95"]
        return "ABOVE THE WIDENED LINE" if lo > 0 else "BELOW THE WIDENED LINE" if hi < 0 else "AT THE WIDENED LINE"

    def lab_d():
        lo, hi = d1_int["theta"]["joint"]["interval95"]
        if lo > 0:
            if d1_hat["designs_p_le_05"] > d1_hat["designs_p_le_05_expected"] and d1_hat["designs_N_ge"]["2"] > d1_hat["designs_N_ge_expected"]["2"]:
                return "BREADTH REMAINS"
            return "THETA ABOVE 0, BUT THE DESIGN COUNTS ARE NOT BOTH ABOVE THE DRAWS"
        return "THETA BELOW 0" if hi < 0 else "NOT SEPARATED FROM THE WIDENED LINE"

    reading = OrderedDict([("kappa", kappa_block["reading_guide"]), ("Q1", lab_q(q1_int["excess_rate"]["joint"])), ("D1", lab_d()),
                           ("D1_concentration", "CONCENTRATED" if (d1_hat["excess_top5_share"] is not None and d1_hat["excess_top5_share"] > 0.5) else "NOT CONCENTRATED (or no positive excess)"),
                           ("fit", fit["label"])])

    me = Path(__file__).resolve()
    inputs_paths = [S.LIVE, AMEND, HASH_REC, EX1_JSON, D1_JSON, D2_JSON, D3_JSON, Path(sweep_dir) / "SWEEP_RUN.json", Path(sweep_dir) / "GATE_REPORT.json"]
    if (Path(sweep_dir) / "SWEEP_MANIFEST.json").exists():
        inputs_paths.append(Path(sweep_dir) / "SWEEP_MANIFEST.json")
    stamp = OrderedDict([
        ("rule_id", RULE_ID), ("question", QUESTION), ("version", VERSION), ("cutoff_utc", d2j["stamp"]["cutoff_utc"]), ("run_utc", utc_now()), ("live_set_sha256", live_sha),
        ("ex1_v3_sha256", S.sha256_file(EX1_JSON)), ("d1_v1_sha256", S.sha256_file(D1_JSON)), ("d2_v1_sha256", S.sha256_file(D2_JSON)), ("d3_v2_sha256", S.sha256_file(D3_JSON)),
        ("sweep_run_sha256", S.sha256_file(Path(sweep_dir) / "SWEEP_RUN.json")), ("code", {f: S.sha256_file(ROOT / f) for f in CODE_FILES}),
        ("amendment_sha256", S.sha256_file(AMEND)), ("hash_record_sha256", S.sha256_file(HASH_REC)),
        ("D", d2j["stamp"]["D"]), ("n_q1", len(q1)), ("F_q1", Fq), ("n_d1_designs", n_d), ("n_d1_cells", n_cells), ("F_d1", Fd), ("n_nc_series", len(ser)),
        ("python", sys.version.split()[0]), ("numpy", np.__version__), ("seeds", {"BQ": SEED_Q, "BD": SEED_D}), ("B", B_BOOT), ("no_significance_label", True)])
    result = OrderedDict([
        ("kappa", kappa_block), ("reading_guide", reading),
        ("Q1", OrderedDict([("at_1", q1_at1), ("at_kappa_hat", q1_hat), ("at_kappa_strict", q1_block(kappa_strict)), ("intervals", q1_int), ("splits", q1_splits),
                            ("designs_beyond_at_kappa_hat", [x["design_id"] for x in q1_rows if x["beyond_at_kappa_hat"]]),
                            ("designs_beyond_at_kappa_strict", [x["design_id"] for x in q1_rows if x["beyond_at_kappa_strict"]]),
                            ("designs_beyond_at_1", [x["design_id"] for x in q1_rows if x["beyond_at_1"]])])),
        ("D1", OrderedDict([("at_1", d1_at1), ("at_kappa_hat", d1_hat), ("at_kappa_strict", d1_eval_at(data, designs, kappa_strict)[0]), ("intervals", d1_int), ("split_nc_vs_other_cells", nc_split),
                            ("families_beyond_at_1", beyond_cells)])),
        ("D3", d3), ("profile", grid), ("fit_to_negative_controls", fit), ("rate_rule_check", rate_rule), ("heterogeneity_by_policy_group", het),
        ("identities_at_kappa_1", OrderedDict([(k, True) for k in ident if not k.startswith("d1_Z_") and not k.startswith("d1_p_")] + [("d1_Z_and_p_of_28_designs", True)])),
        ("bootstrap", OrderedDict([("BQ", OrderedDict([("blocks", Fq), ("seed", SEED_Q), ("B", B_BOOT), ("defined_joint", int(np.isfinite(kq["joint"]).sum()))])),
                                   ("BD", OrderedDict([("blocks", Fd), ("seed", SEED_D), ("B", B_BOOT), ("defined_joint", int(np.isfinite(kd["joint_theta"]).sum()))]))])),
    ])
    payload = S.clean(OrderedDict([
        ("schema_version", "l3-meta-cal-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {S.rel(p): {"sha256": S.sha256_file(p), "bytes": Path(p).stat().st_size} for p in inputs_paths}),
        ("question", OrderedDict([("CAL", result)])), ("designs_q1", q1_rows), ("designs_d1", d1_rows),
        ("audit", {"l2_files_written": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        target = out_dir / OUT_NAME
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        write_once(target, text)
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(S.LIVE, copy)
        assert S.sha256_file(copy) == live_sha
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_cal.py " + " ".join(argv if argv is not None else sys.argv[1:]), "code_sha256": stamp["code"]["scripts/l3_meta/build_cal.py"],
                                 "live_set_sha256": live_sha, "results_file": S.rel(target), "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": {k: stamp[k] for k in ("rule_id", "question", "version", "run_utc", "n_q1", "F_q1", "n_d1_designs", "n_d1_cells", "F_d1", "n_nc_series")},
             "kappa": payload["question"]["CAL"]["kappa"], "reading_guide": payload["question"]["CAL"]["reading_guide"], "Q1": {k: payload["question"]["CAL"]["Q1"][k] for k in ("at_1", "at_kappa_hat", "at_kappa_strict", "intervals", "splits")},
             "D1": {k: payload["question"]["CAL"]["D1"][k] for k in ("at_1", "at_kappa_hat", "at_kappa_strict", "intervals", "split_nc_vs_other_cells")}, "D3": payload["question"]["CAL"]["D3"],
             "fit": payload["question"]["CAL"]["fit_to_negative_controls"], "rate_rule": payload["question"]["CAL"]["rate_rule_check"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {S.rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
