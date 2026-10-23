#!/usr/bin/env python3
"""SERPROP (rule id EX-1, exploratory): do series properties and data provider predict excess crossing of the calibrated P5 line?

Rules: docs/plans/l3-meta/PREREG_AMEND_20261020_SERPROP.md (+ HASH record).  Read-only inputs; output small JSON/CSV only.
Run:  VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/build_l3n_serprop_r6.py [--out-dir DIR] [--reps 999] [--perms 2000] [--boot 999]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

PREREG = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_SERPROP.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_SERPROP_HASH.txt"
R2 = ROOT / "data/l3_meta/refresh/20261020_r3"
CELLS = ROOT / "data/l3_meta/d4_d10_v1/CELLS_D4_D10_V1.csv"
D6 = ROOT / "data/l3_meta/d4_d10_v1/D6_SERIES_V1.csv"
SWEEP = R2 / "sweep"
MANIFEST = SWEEP / "SERIES_MANIFEST.json"
MANIFEST_COUNT = SWEEP / "SERIES_MANIFEST_COUNT.json"
CAL = R2 / "cal/RESULTS_CAL_V1.json"
INVENTORY = ROOT / "docs/plans/l3-meta/OUTCOME_DIMENSION_INVENTORY_20261015.csv"
NC = R2 / "d2/NC_CELLS_D2_V1.csv"
OUT_DIR = ROOT / "data/l3_meta/l3n_serprop_20261020_r6"
SEED_PLACEBO, SEED_PERM, SEED_BOOT = 20261020, 20261021, 20261022

PROVIDERS = ["CDC_NCHS", "FBI", "NHTSA", "CENSUS", "ACS", "OTHER"]
PROVIDER_MAP = {"CDC WONDER (NCHS)": "CDC_NCHS", "CDC / NCHS": "CDC_NCHS", "CDC": "CDC_NCHS", "FBI (UCR / CDE)": "FBI", "NHTSA (FARS)": "NHTSA",
                "Census Bureau": "CENSUS", "Census Bureau (ACS 1-year)": "ACS", "EIA": "OTHER", "DOL ETA": "OTHER", "NCES (IPEDS)": "OTHER", "FTC": "OTHER", "BLS": "OTHER"}
NAMES = ["log_cells", "span", "break", "count"] + ["prov_" + p for p in PROVIDERS[1:]]
BLOCK_ALL = list(range(9))
BLOCK_PROV = [4, 5, 6, 7, 8]
BLOCK_MECH = [0, 1, 2, 3]


# ------------------------------------------------------------------ core statistics (tested on synthetic data)
def demean(M, groups):
    """Subtract group means from rows of M (N x k or N)."""
    M = np.asarray(M, float)
    out = M.copy()
    for g in np.unique(groups):
        i = groups == g
        out[i] = M[i] - M[i].mean(axis=0)
    return out


def fe_cluster(Y, X, design, cluster, Xdm=None):
    """Linear model with design fixed effects.  Y: N x R (columns = outcomes), X: N x K.
    Returns coef (K x R), V (R x K x K cluster-robust, G/(G-1) factor)."""
    Y = np.asarray(Y, float)
    if Y.ndim == 1:
        Y = Y[:, None]
    Xd = demean(X, design) if Xdm is None else Xdm
    Yd = demean(Y, design)
    A = Xd.T @ Xd
    Ai = np.linalg.pinv(A)
    B = Ai @ (Xd.T @ Yd)
    E = Yd - Xd @ B
    cl = np.unique(cluster)
    G = len(cl)
    K = Xd.shape[1]
    R = Y.shape[1]
    meat = np.zeros((R, K, K))
    for g in cl:
        i = cluster == g
        S = Xd[i].T @ E[i]            # K x R
        meat += np.einsum("kr,lr->rkl", S, S)
    V = (G / max(G - 1, 1)) * np.einsum("ab,rbc,cd->rad", Ai, meat, Ai)
    return B, V


def wald(B, V, idx):
    """Wald statistic per column for coefficients idx (pseudo-inverse)."""
    idx = list(idx)
    out = np.empty(B.shape[1])
    for r in range(B.shape[1]):
        b = B[idx, r]
        Vi = np.linalg.pinv(V[r][np.ix_(idx, idx)])
        out[r] = float(b @ Vi @ b)
    return out


def p_upper(real, reps):
    reps = np.asarray(reps, float)
    return float((1 + np.sum(reps >= real - 1e-12 * max(1.0, abs(real)))) / (len(reps) + 1))


def perm_bundles(keys_by_family, bundle, rng):
    """Permute whole key-level bundles across keys within family.  bundle: dict key -> vector.  Returns dict key -> permuted bundle."""
    out = {}
    for fam, keys in keys_by_family.items():
        ks = list(keys)
        order = rng.permutation(len(ks))
        for a, b in zip(ks, order):
            out[a] = bundle[ks[b]]
    return out


# ------------------------------------------------------------------ data
def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for ch in iter(lambda: fh.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()


def read_csv(p):
    with open(p, newline="") as fh:
        return list(csv.DictReader(fh))


def load_cells():
    """Cells table with the re-derived placebo arrays.  Returns cells list, per-design dict."""
    cells = read_csv(CELLS)
    run = json.load(open(SWEEP / "SWEEP_RUN.json"))
    files = {d["design_id"]: d for d in run["designs"] if d["status"] == "OK"}
    by_design = defaultdict(list)
    for i, c in enumerate(cells):
        by_design[c["design_id"]].append(i)
    designs, problems = OrderedDict(), []
    for did in sorted(by_design):
        f = files.get(did)
        if f is None:
            problems.append(f"{did}: not in SWEEP_RUN.json")
            continue
        path = SWEEP / Path(f["file"]).name
        if sha256_file(path) != f["sha256"]:
            problems.append(f"{did}: npz sha256 differs from SWEEP_RUN.json")
            continue
        z = np.load(path, allow_pickle=False)
        ids = [str(s) for s in z["series_ids"]]
        pos = {s: j for j, s in enumerate(ids)}
        rows = by_design[did]
        sids = [cells[i]["series_id"] for i in rows]
        if any(s not in pos for s in sids):
            problems.append(f"{did}: series missing in npz")
            continue
        P = np.asarray(z["placebo"], float)[[pos[s] for s in sids]]
        common = np.isfinite(P).all(axis=0)
        P = P[:, common]
        n = P.shape[1]
        att = np.asarray(z["att"], float)[[pos[s] for s in sids]]
        sd = P.std(axis=1, ddof=1)
        c = np.sort(np.abs(P), axis=1)[:, math.ceil(0.95 * n) - 1]
        designs[did] = dict(rows=rows, P=P, att=att, sd=sd, c=c, n=n)
        for k, i in enumerate(rows):
            row = cells[i]
            for name, val in (("z", att[k] / sd[k]), ("line_c", c[k]), ("rho", abs(att[k]) / c[k])):
                if abs(float(row[name]) - val) > 1e-8 * max(1.0, abs(val)):
                    problems.append(f"{did}/{row['series_id']}: {name} differs ({row[name]} vs {val})")
    return cells, designs, problems


def series_properties(cells):
    man = json.load(open(MANIFEST))["series"]
    manc = json.load(open(MANIFEST_COUNT))["series"]
    inv = {r["outcome_key"]: r for r in read_csv(INVENTORY) if r["row_type"] == "variable"}
    d6 = {r["key"]: r for r in read_csv(D6)}
    props, missing = {}, []
    for c in cells:
        key, sid = c["key"], c["series_id"]
        if key in props:
            continue
        m = man.get(sid)
        if m is None or sid not in inv or inv[sid]["source"] not in PROVIDER_MAP:
            missing.append(key)
            continue
        ncell = manc[sid]["cells"] if key.endswith("[count]") and sid in manc else m["cells"]
        props[key] = dict(log_cells=math.log(ncell), span=float(m["last_year"] - m["first_year"] + 1), brk=float(bool(m["definition_break"])),
                          count=float(c["scale_mode"] == "count"), provider=PROVIDER_MAP[inv[sid]["source"]], frequency=inv[sid]["frequency"],
                          coarse=(None if key not in d6 or d6[key]["calibrated_class"] == "BLIND" else float(d6[key]["calibrated_class"] == "COARSE")), family=c["family"])
    return props, missing


def bundle_matrix(props, keys, mu, sd):
    """Key-level standardised regressor bundle (9 columns)."""
    out = {}
    for k in keys:
        p = props[k]
        v = [(p["log_cells"] - mu[0]) / sd[0], (p["span"] - mu[1]) / sd[1], p["brk"], p["count"]] + [float(p["provider"] == q) for q in PROVIDERS[1:]]
        out[k] = np.array(v)
    return out


# ------------------------------------------------------------------ main
def pct(a, lo=2.5, hi=97.5):
    return [float(np.percentile(a, lo)), float(np.percentile(a, hi))]


def provider_gap(beyond, prov, min_series, series_of):
    """Largest provider gap (share beyond of provider minus share of others), providers with >= min_series series."""
    best = None
    for q in PROVIDERS:
        i = prov == q
        if len({series_of[j] for j in np.where(i)[0]}) < min_series or i.all() or (~i).all():
            continue
        g = beyond[i].mean() - beyond[~i].mean()
        if best is None or g > best[1]:
            best = (q, float(g))
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--reps", type=int, default=999)
    ap.add_argument("--perms", type=int, default=2000)
    ap.add_argument("--boot", type=int, default=999)
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # hash record check
    txt = REC.read_text()
    want = [l.split()[1] for l in txt.splitlines() if l.startswith("sha256:")][0]
    if sha256_file(PREREG) != want:
        sys.exit("prereg differs from hash record: not run")
    cells, designs, problems = load_cells()
    result = OrderedDict(rule_id="EX-1", question="SERPROP", prereg_sha256=want, rederivation_problems=problems)
    if problems:
        result["status"] = "NOT_RUN: re-derivation of c, rho, z differs from the cells file"
        (out / "RESULTS_SERPROP.json").write_text(json.dumps(result, indent=1))
        print(result["status"], problems[:5])
        return 1
    kappa = float(json.load(open(CAL))["question"]["CAL"]["kappa"]["kappa_hat"])
    props, missing = series_properties(cells)
    keep = [i for i, c in enumerate(cells) if c["key"] in props]
    result.update(kappa=kappa, n_cells_file=len(cells), n_cells_used=len(keep), keys_missing_properties=sorted(set(missing)))
    N = len(keep)
    design = np.array([cells[i]["design_id"] for i in keep])
    pfam = np.array([cells[i]["policy_family"] for i in keep])
    key = np.array([cells[i]["key"] for i in keep])
    ofam = np.array([cells[i]["family"] for i in keep])
    sidarr = np.array([cells[i]["series_id"] for i in keep])
    lc = np.array([props[k]["log_cells"] for k in key]); sp = np.array([props[k]["span"] for k in key])
    mu = (lc.mean(), sp.mean()); sdv = (lc.std(), sp.std())
    ukeys = sorted(set(key))
    bund = bundle_matrix(props, ukeys, mu, sdv)
    X = np.array([bund[k] for k in key])
    prov = np.array([props[k]["provider"] for k in key])
    # real and placebo outcome matrices
    pos = {i: j for j, i in enumerate(keep)}
    rho = np.zeros(N); rho_mean = np.zeros(N)
    rng = np.random.default_rng(SEED_PLACEBO)
    R = a.reps
    rho_rep = np.zeros((N, R))
    for did, d in designs.items():
        idx = rng.integers(0, d["n"], size=R)
        absP = np.abs(d["P"]) / d["c"][:, None]
        for k_, ci in enumerate(d["rows"]):
            if ci not in pos:
                continue
            j = pos[ci]
            rho[j] = abs(d["att"][k_]) / d["c"][k_]
            rho_mean[j] = absP[k_].mean()
            rho_rep[j] = absP[k_, idx]
    A_real = (rho > kappa).astype(float)
    A_rep = (rho_rep > kappa).astype(float)
    B_real = rho - rho_mean
    B_rep = rho_rep - rho_mean[:, None]
    result["placebo_beyond_rate_at_kappa"] = float(A_rep.mean())
    result["real_beyond_rate"] = float(A_real.mean())
    result["n_designs"] = int(len(set(design))); result["n_series"] = int(len(set(sidarr))); result["n_keys"] = len(ukeys)
    result["frequency_values"] = sorted({props[k]["frequency"] for k in ukeys})
    Xdm = demean(X, design)

    def arm(Yreal, Yrep, label, cluster=design):
        B0, V0 = fe_cluster(Yreal, X, design, cluster, Xdm)
        Br, Vr = fe_cluster(Yrep, X, design, cluster, Xdm)
        o = OrderedDict()
        for blk, idx in (("joint", BLOCK_ALL), ("provider_X7", BLOCK_PROV), ("mechanical_M8", BLOCK_MECH)):
            w0 = wald(B0, V0, idx)[0]
            wr = wald(Br, Vr, idx)
            o[blk] = OrderedDict(df=len(idx), wald=float(w0), p_placebo=p_upper(w0, wr), placebo_wald_mean=float(wr.mean()))
        coefs = OrderedDict()
        for k_, nm in enumerate(NAMES):
            se = float(np.sqrt(V0[0][k_, k_]))
            lo_hi = pct(Br[k_])
            coefs[nm] = OrderedDict(coef=float(B0[k_, 0]), se_cluster=se, ci95=[float(B0[k_, 0] - 1.96 * se), float(B0[k_, 0] + 1.96 * se)], placebo_mean=float(Br[k_].mean()),
                                    placebo_range95=lo_hi, outside_placebo_range=bool(B0[k_, 0] < lo_hi[0] or B0[k_, 0] > lo_hi[1]))
        o["coefficients"] = coefs
        o["wald_real"] = {b: o[b]["wald"] for b in ("joint", "provider_X7", "mechanical_M8")}
        return o

    result["arm_A_beyond"] = arm(A_real, A_rep, "A")
    result["arm_B_excess_rho"] = arm(B_real, B_rep, "B")
    # family-clustered sensitivity (arm A joint)
    B0, V0 = fe_cluster(A_real, X, design, pfam, Xdm); Br, Vr = fe_cluster(A_rep, X, design, pfam, Xdm)
    w0 = wald(B0, V0, BLOCK_ALL)[0]; wr = wald(Br, Vr, BLOCK_ALL)
    result["sensitivity_family_cluster_A_joint"] = OrderedDict(wald=float(w0), p_placebo=p_upper(w0, wr), n_clusters=int(len(set(pfam))))

    # bundle permutation reference (arms A, B) and provider max-excess
    keys_by_family = defaultdict(list)
    for k in ukeys:
        keys_by_family[props[k]["family"]].append(k)
    prng = np.random.default_rng(SEED_PERM)
    kidx = {k: i for i, k in enumerate(key)}
    rows_of_key = defaultdict(list)
    for i, k in enumerate(key):
        rows_of_key[k].append(i)
    wA = {b: [] for b in ("joint", "provider_X7", "mechanical_M8")}; wB = {b: [] for b in wA}
    gap_perm = []
    Ys = np.column_stack([A_real, B_real])
    prov_of = {k: props[k]["provider"] for k in ukeys}
    for _ in range(a.perms):
        pm = perm_bundles(keys_by_family, bund, prng)
        Xp = np.array([pm[k] for k in key])
        Bp, Vp = fe_cluster(Ys, Xp, design, design)
        for b, idx in (("joint", BLOCK_ALL), ("provider_X7", BLOCK_PROV), ("mechanical_M8", BLOCK_MECH)):
            w = wald(Bp, Vp, idx)
            wA[b].append(w[0]); wB[b].append(w[1])
        # provider labels follow the permuted bundle: provider index from the dummies
        pp = np.array(["CDC_NCHS" if not Xp[i, 4:].any() else PROVIDERS[1 + int(np.argmax(Xp[i, 4:]))] for i in range(N)])
        g = provider_gap(A_real, pp, 3, sidarr)
        gap_perm.append(g[1] if g else -1.0)
    for lab, w_real, ws in (("arm_A_beyond", result["arm_A_beyond"], wA), ("arm_B_excess_rho", result["arm_B_excess_rho"], wB)):
        for b in ws:
            w_real[b]["p_bundle_permutation"] = p_upper(w_real[b]["wald"], ws[b])
            pp_, pb_ = w_real[b]["p_placebo"], w_real[b]["p_bundle_permutation"]
            w_real[b]["reading"] = "predicts" if (pp_ <= 0.05 and pb_ <= 0.05) else ("mixed" if (pp_ <= 0.05 or pb_ <= 0.05) else "no detectable relation")
    g_real = provider_gap(A_real, prov, 3, sidarr)
    gap_rep = []
    for r_ in range(R):
        g = provider_gap(A_rep[:, r_], prov, 3, sidarr)
        gap_rep.append(g[1] if g else -1.0)
    result["provider_max_gap"] = OrderedDict(provider=g_real[0], gap=g_real[1], p_label_permutation=p_upper(g_real[1], gap_perm), p_placebo=p_upper(g_real[1], gap_rep),
                                             placebo_gap_mean=float(np.mean(gap_rep)))
    # provider table
    tab = []
    for q in PROVIDERS:
        i = prov == q
        tab.append(OrderedDict(provider=q, cells=int(i.sum()), series=int(len(set(sidarr[i]))), designs=int(len(set(design[i]))), share_beyond=float(A_real[i].mean()),
                               placebo_share_beyond=float(A_rep[i].mean()), mean_rho=float(rho[i].mean()), placebo_mean_rho=float(rho_mean[i].mean())))
    result["provider_table"] = tab

    # arm C
    coarse = np.array([np.nan if props[k]["coarse"] is None else props[k]["coarse"] for k in key])
    ci = ~np.isnan(coarse)
    Xc = coarse[ci][:, None]; dc = design[ci]
    B0, V0 = fe_cluster(A_real[ci], Xc, dc, dc); Br, Vr = fe_cluster(A_rep[ci], Xc, dc, dc)
    brng = np.random.default_rng(SEED_BOOT)
    ud = sorted(set(dc)); bs = []
    for _ in range(a.boot):
        pick = brng.integers(0, len(ud), len(ud))
        rows_ = np.concatenate([np.where(dc == ud[p_])[0] for p_ in pick])
        gid = np.concatenate([np.full((dc == ud[p_]).sum(), j) for j, p_ in enumerate(pick)])
        Bb, _ = fe_cluster(A_real[ci][rows_], Xc[rows_], gid, gid)
        bs.append(float(Bb[0, 0]))
    se = float(np.sqrt(V0[0][0, 0]))
    result["arm_C_coarse"] = OrderedDict(n_cells=int(ci.sum()), n_designs=int(len(ud)), n_series=int(len(set(sidarr[ci]))), coef=float(B0[0, 0]), se_cluster=se,
                                         boot_ci95=pct(bs), placebo_mean=float(Br[0].mean()), placebo_range95=pct(Br[0]),
                                         p_placebo_two_sided=float((1 + np.sum(np.abs(Br[0]) >= abs(B0[0, 0]) - 1e-12)) / (R + 1)),
                                         share_beyond_coarse=float(A_real[ci][coarse[ci] == 1].mean()), share_beyond_other=float(A_real[ci][coarse[ci] == 0].mean()),
                                         placebo_share_coarse=float(A_rep[ci][coarse[ci] == 1].mean()), placebo_share_other=float(A_rep[ci][coarse[ci] == 0].mean()))

    # arm D (strict NC cells)
    nc = [r for r in read_csv(NC) if r["strict_nc"] == "1"]
    cnt_keys = {c["series_id"] for c in cells if c["key"].endswith("[count]")}
    dscale = {c["design_id"]: c["scale_mode"] for c in cells}
    rowsD = []
    for r in nc:
        s = r["top_series"]
        kk = s + "[count]" if (dscale.get(r["design_id"]) == "count" and s in cnt_keys) else s
        if kk in props:
            rowsD.append((r, kk))
        else:
            # property keys for series not in the cells file: build from manifest if possible
            pass
    result["arm_D"] = OrderedDict(n_nc_strict=len(nc), n_with_properties=len(rowsD))
    if len(rowsD) >= 20:
        XD = np.array([bund[kk] for _, kk in rowsD]); yD = np.array([float(r["beyond"]) for r, _ in rowsD])
        dD = np.array([r["design_id"] for r, _ in rowsD]); fD = np.array([r["outcome_family"] for r, _ in rowsD])
        B0, V0 = fe_cluster(yD, XD, dD, dD)
        w0 = wald(B0, V0, BLOCK_ALL)[0]
        wps = []
        for _ in range(a.perms):
            Xp = XD.copy()
            for f_ in set(fD):
                ii = np.where(fD == f_)[0]
                Xp[ii] = XD[ii[prng.permutation(len(ii))]]
            Bp, Vp = fe_cluster(yD, Xp, dD, dD)
            wps.append(wald(Bp, Vp, BLOCK_ALL)[0])
        result["arm_D"].update(share_beyond=float(yD.mean()), n_designs=int(len(set(dD))), wald_joint=float(w0), p_bundle_permutation=p_upper(w0, wps),
                               coefs={nm: float(B0[k_, 0]) for k_, nm in enumerate(NAMES)})
    result["status"] = "RUN"
    result["inputs_sha256"] = {str(p.relative_to(ROOT)): sha256_file(p) for p in (CELLS, D6, SWEEP / "SWEEP_RUN.json", MANIFEST, MANIFEST_COUNT, CAL, INVENTORY, NC)}
    with open(out / "RESULTS_SERPROP.json", "w") as fh:
        json.dump(result, fh, indent=1)
    with open(out / "SERPROP_CELLS.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["design_id", "key", "provider", "rho", "beyond", "excess_rho"] + NAMES)
        for i in range(N):
            w.writerow([design[i], key[i], prov[i], rho[i], int(A_real[i]), B_real[i]] + list(X[i]))
    with open(out / "SERPROP_PROVIDER_TABLE.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(tab[0])); w.writeheader(); w.writerows(tab)
    print(json.dumps({k: result[k] for k in ("status", "n_cells_used", "n_designs", "placebo_beyond_rate_at_kappa", "real_beyond_rate")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
