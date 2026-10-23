#!/usr/bin/env python3
"""Independent recomputation of D4-V1 ... D10-V1 in plain Python (imports nothing from build_d4_d10.py or any other builder or checker).

Written from the amendment files PREREG_AMEND_20261015_D4_D10_QUESTIONS.md and PREREG_AMEND_20261015_D4_D10_V1.md (sections 0 to 7).  Reads the sweep files
(<design>.npz through SWEEP_RUN.json), the two series stores, the three result JSONs it needs (D1-V1 roster, D2-V1 cell codes, CAL-V1 kappa), the inventory CSV, the
catalog, the stored RESULT.json files (group_time only) and the results file written by build_d4_d10.py.  Re-derives with explicit loops: the D1 sample (series used,
common draws, z, line, rho), D4 (pruning, the marginal-mean decomposition, the replicate statistics with the same random numbers, excess, p, shares, labels, the
family view, leave-one-policy-family-out), D5 (values, replicate p, the family-block bootstrap, strata, registered subset, summary), D6 (MDE, scale, medians, classes,
unsupported series), D7 (rates, exact intervals, heterogeneity T and its permutation p), D8 (twin cells, tier estimates, E1 in explicit loops), D9 (break table from the
catalog, exposure from RESULT.json, groups, stratified permutation, bootstrap) and D10 (status of the policy-text rows and the shares).  Prints the largest differences and
exits 1 on any disagreement (1e-9 relative: |x - y| <= 1e-9 max(1, |x|, |y|)).  Writes nothing unless --out is given (a new file).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_d4_d10_independent.py [--results FILE] [--sweep-dir DIR] [--only D4,D5,...] [--out FILE]
(numpy only loads npz files and draws the random numbers; percentiles by numpy.percentile)
"""
import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / "data/l3_meta/sweep_v1"
D1J = ROOT / "data/l3_meta/d1_v1/RESULTS_D1_V1.json"
D2J = ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"
CALJ = ROOT / "data/l3_meta/cal_v1/RESULTS_CAL_V1.json"
INV = ROOT / "docs/plans/l3-meta/OUTCOME_DIMENSION_INVENTORY_20261015.csv"
CATALOG = ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json"
BUILD_DOC = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_D4_D10_V1.md"
DEFAULT_RESULTS = ROOT / "data/l3_meta/d4_d10_v1/RESULTS_D4_D10_V1.json"
R_REP, B_BOOT, R_LOO, MDE_Z = 9999, 9999, 999, 0.8416212335729143

worst = {}
problems = []


def agree(x, y):
    return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))


def compare(label, got, want):
    """Record the difference between the results file (got) and this recomputation (want)."""
    if got is None or want is None:
        if got is not want:
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, bool) or isinstance(got, bool):
        if bool(got) != bool(want):
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, str) or isinstance(got, str):
        if got != want:
            problems.append(f"{label}: {got!r} vs {want!r}")
        return
    if isinstance(want, dict):
        if not isinstance(got, dict):
            problems.append(f"{label}: not a dict")
            return
        for k, v in want.items():
            if k not in got:
                problems.append(f"{label}.{k}: missing in the results file")
            else:
                compare(f"{label}.{k}", got[k], v)
        return
    if isinstance(want, (list, tuple)):
        if len(got) != len(want):
            problems.append(f"{label}: length {len(got)} vs {len(want)}")
            return
        for i, (g, w) in enumerate(zip(got, want)):
            compare(f"{label}[{i}]", g, w)
        return
    d = abs(got - want)
    key = re.sub(r"\[\d+\]", "", label).split(".")[-1]
    worst[key] = max(worst.get(key, 0.0), d / max(1.0, abs(got), abs(want)))
    if not agree(got, want):
        problems.append(f"{label}: {got!r} vs {want!r} (difference {d:.3g})")


def mean_(v):
    return sum(v) / len(v)


def var_(v, ddof=1):
    m = mean_(v)
    return sum((x - m) ** 2 for x in v) / (len(v) - ddof)


def sd_(v):
    return math.sqrt(var_(v))


def ge(a, b):
    return a >= b - 1e-12 * max(1.0, abs(b))


def sgn(x):
    return int(x > 0) - int(x < 0)


def pctl(values, lo=2.5, hi=97.5):
    a = [v for v in values if v == v]
    if not a:
        return None
    q = np.percentile(np.array(a), [lo, hi])
    return [float(q[0]), float(q[1])]


def line95(absvals):
    s = sorted(absvals)
    return s[math.ceil(0.95 * len(s)) - 1]


# ---------------------------------------------------------------------------------------------
# the D1 sample in plain loops (sweep amendment section 6 and build file 0.2)
# ---------------------------------------------------------------------------------------------
def plain_sample(sids, fam, code, att, placebo):
    """Series used by D1-V1 (sorted by id), the common draws C, and the per-series arrays.  None when the design is not EVAL."""
    n_all = len(placebo[0])
    A = []
    for i in range(len(sids)):
        if code[i] != "SUPPORTED":
            continue
        vals = [v for v in placebo[i] if not math.isnan(v)]
        if len(vals) < 2 or not (sd_(vals) > 0):
            continue
        A.append(i)

    def common(idx):
        return [b for b in range(n_all) if all(not math.isnan(placebo[i][b]) for i in idx)]

    C = common(A)
    while A and len(C) < 900 and len({fam[i] for i in A}) >= 5:
        count = {i: sum(1 for v in placebo[i] if not math.isnan(v)) for i in A}
        fewest = min(count.values())
        drop = max((i for i in A if count[i] == fewest), key=lambda i: sids[i])
        A.remove(drop)
        C = common(A)
    if len({fam[i] for i in A}) < 5 or len(C) < 900:
        return None
    A = sorted(A, key=lambda i: sids[i])
    n = len(C)
    out = dict(C=C, n=n, sids=[sids[i] for i in A], fam=[fam[i] for i in A], att=[att[i] for i in A], P=[[placebo[i][b] for b in C] for i in A])
    out["sd"] = [sd_(row) for row in out["P"]]
    if any(not (s > 0) for s in out["sd"]):
        return None
    out["z"] = [a / s for a, s in zip(out["att"], out["sd"])]
    out["c"] = [line95([abs(v) for v in row]) for row in out["P"]]
    out["rho"] = [abs(a) / c for a, c in zip(out["att"], out["c"])]
    out["Zabs"] = [[abs(v) / s for v in row] for row, s in zip(out["P"], out["sd"])]
    return out


def load_npz_plain(path):
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["meta_json"]))
    return dict(sids=[str(x) for x in z["series_ids"]], fam=[str(x) for x in z["family"]], code=[str(x) for x in z["code"]], att=[float(x) for x in z["att"]],
                placebo=[[float(v) for v in row] for row in z["placebo"]], sd=[float(x) for x in z["sd"]], cutoff=[float(x) for x in z["cutoff"]], meta=meta)


# ---------------------------------------------------------------------------------------------
# D4
# ---------------------------------------------------------------------------------------------
def plain_prune(pairs, min_col=5, min_row=5):
    pairs = set(pairs)
    changed = True
    while changed:
        changed = False
        cc = Counter(c for _, c in pairs)
        bad = {c for c, n in cc.items() if n < min_col}
        if bad:
            pairs = {(r, c) for r, c in pairs if c not in bad}
            changed = True
        rc = Counter(r for r, _ in pairs)
        bad = {r for r, n in rc.items() if n < min_row}
        if bad:
            pairs = {(r, c) for r, c in pairs if r not in bad}
            changed = True
    return sorted(pairs)


def plain_dec(x, row, col, nr, nc):
    N = len(x)
    m = sum(x) / N
    rs, rn, cs, cn = [0.0] * nr, [0] * nr, [0.0] * nc, [0] * nc
    for v, r, c in zip(x, row, col):
        rs[r] += v
        rn[r] += 1
        cs[c] += v
        cn[c] += 1
    rm = [rs[i] / rn[i] for i in range(nr)]
    cm = [cs[j] / cn[j] for j in range(nc)]
    A_row = sum((rm[r] - m) ** 2 for r in row) / N
    A_col = sum((cm[c] - m) ** 2 for c in col) / N
    A_res = sum((v - rm[r] - cm[c] + m) ** 2 for v, r, c in zip(x, row, col)) / N
    V_tot = sum((v - m) ** 2 for v in x) / N
    return {"A_row": A_row, "A_col": A_col, "A_res": A_res, "V_tot": V_tot, "A_x": V_tot - A_row - A_col - A_res}


PARTS = ("A_row", "A_col", "A_res")
NAMES = {"A_row": "POLICY", "A_col": "OUTCOME", "A_res": "PAIR"}


def plain_label(p, e):
    sel = [k for k in PARTS if p[k] <= 0.05]
    if not sel:
        return "NONE BEYOND THE NULL"
    sel.sort(key=lambda k: -e[k])
    return " > ".join(NAMES[k] for k in sel)


def plain_d4_core(pairs, obs_of, rep_of, des_j, idx, kappas, R, label_kappa=None):
    """pairs: sorted (design, col); obs_of[(d,c)] observed size; rep_of[(d,c)] replicate vector; des_j[d] column of the replicate index matrix.
    Returns the observed statistics, the replicate statistics lists and the per-level results."""
    des = sorted({r for r, _ in pairs})
    cols = sorted({c for _, c in pairs})
    row = [des.index(r) for r, _ in pairs]
    col = [cols.index(c) for _, c in pairs]
    obs = plain_dec([obs_of[p] for p in pairs], row, col, len(des), len(cols))
    null = {k: [] for k in obs}
    for r in range(R):
        x = [rep_of[p][idx[r][des_j[p[0]]]] for p in pairs]
        st = plain_dec(x, row, col, len(des), len(cols))
        for k in obs:
            null[k].append(st[k])
    return obs, null, des, cols


def plain_levels(obs, null, kappas, R):
    out = {}
    nm = {k: mean_(v) for k, v in null.items()}
    for lab, kap in kappas.items():
        e = {k: obs[k] - kap ** 2 * nm[k] for k in obs}
        p = {k: (1 + sum(1 for v in null[k] if ge(kap ** 2 * v, obs[k]))) / (R + 1) for k in PARTS}
        pos = {k: max(e[k], 0.0) for k in PARTS}
        tot = sum(pos.values())
        share = {k: (pos[k] / tot if tot > 0 else None) for k in PARTS}
        out[lab] = dict(kappa=kap, excess=e, p_one_sided=p, share_of_positive_excess=share, label=plain_label(p, e))
    return nm, out


# ---------------------------------------------------------------------------------------------
# D5
# ---------------------------------------------------------------------------------------------
def plain_d5_values(a, s, kap):
    m = len(a)
    V = var_(a)
    N = mean_([x * x for x in s])
    t2 = V - kap ** 2 * N
    return dict(m=m, a_bar=mean_(a), V=V, N=N, tau2=t2, tau=math.sqrt(max(t2, 0.0)), H=V / (kap ** 2 * N), rms2=mean_([x * x for x in a]) - kap ** 2 * N)


def plain_d5_boot(a, s, fam_idx, F, rng, B):
    draw = rng.integers(0, F, size=(B, F))
    Vs, Ns, dropped = [], [], 0
    for row in draw:
        cnt = [0] * F
        for f in row:
            cnt[f] += 1
        w = [cnt[f] for f in fam_idx]
        W = sum(w)
        if W < 2:
            dropped += 1
            continue
        ab = sum(wi * ai for wi, ai in zip(w, a)) / W
        Vs.append(sum(wi * (ai - ab) ** 2 for wi, ai in zip(w, a)) / (W - 1))
        Ns.append(sum(wi * si * si for wi, si in zip(w, s)) / W)
    return Vs, Ns, dropped


# ---------------------------------------------------------------------------------------------
# D6
# ---------------------------------------------------------------------------------------------
def plain_scale(val, ok, uidx, yidx):
    cells = []
    for j, y in enumerate(yidx):
        col = [float(val[u][y]) for u in uidx if bool(ok[u][y])]
        cells.append(col)
    N = sum(len(c) for c in cells)
    if N == 0:
        return N, None, None
    allv = [v for c in cells for v in c]
    mean = sum(allv) / N
    ss, Y = 0.0, 0
    for c in cells:
        if c:
            Y += 1
            mc = sum(c) / len(c)
            ss += sum((v - mc) ** 2 for v in c)
    dof = N - Y
    sdw = math.sqrt(ss / dof) if dof >= 2 else None
    if sdw is not None and not sdw > 0:
        sdw = None
    return N, mean, sdw


def plain_class(v):
    if v is None:
        return None
    return "FINE" if v <= 0.25 else "MODERATE" if v <= 0.5 else "COARSE" if v <= 1.0 else "BLIND"


def median_(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---------------------------------------------------------------------------------------------
# D7
# ---------------------------------------------------------------------------------------------
def plain_cdf(x, n, p):
    if x < 0:
        return 0.0
    return sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(0, min(x, n) + 1))


def plain_cp(x, n):
    if n == 0:
        return None

    def root(f):
        a, b = 0.0, 1.0
        fa = f(a)
        for _ in range(200):
            m = (a + b) / 2
            fm = f(m)
            if (fa < 0) == (fm < 0):
                a, fa = m, fm
            else:
                b = m
            if b - a < 1e-13:
                break
        return (a + b) / 2
    lo = 0.0 if x == 0 else root(lambda p: 1.0 - plain_cdf(x - 1, n, p) - 0.025)
    hi = 1.0 if x == n else root(lambda p: plain_cdf(x, n, p) - 0.025)
    return [lo, hi]


def plain_dtype(u):
    first = u.split(";")[0].strip().lower()
    if first.startswith("state"):
        return "STATE"
    for pre in ("county", "city", "institution"):
        if first.startswith(pre):
            return "SUB-STATE"
    for pre in ("individual", "household", "job application", "restaurant", "legislator"):
        if first.startswith(pre):
            return "MICRO"
    raise ValueError(u)


def plain_T(y, g, ng):
    n = len(y)
    pbar = sum(y) / n
    if not 0 < pbar < 1:
        return None
    cnt, suc = [0] * ng, [0.0] * ng
    for yi, gi in zip(y, g):
        cnt[gi] += 1
        suc[gi] += yi
    return sum(cnt[k] * ((suc[k] / cnt[k] if cnt[k] else 0.0) - pbar) ** 2 for k in range(ng)) / (pbar * (1 - pbar))


# ---------------------------------------------------------------------------------------------
# D8 / E1
# ---------------------------------------------------------------------------------------------
def plain_e1(y, fam, seed, B):
    fams = sorted(set(fam))
    F = len(fams)
    members = {f: [i for i, x in enumerate(fam) if x == f] for f in fams}
    n = len(y)
    theta = sum(y) / n
    se = math.sqrt(F / (F - 1) * sum(sum(y[i] - theta for i in members[f]) ** 2 for f in fams)) / n
    tiny = 1e-9 * max(max(abs(v) for v in y), 1e-300)
    t = theta / se if se > tiny else float("nan")
    draw = np.random.default_rng(seed).integers(0, F, size=(B, F))
    S_ = [sum(y[i] for i in members[f]) for f in fams]
    m_ = [len(members[f]) for f in fams]
    ts = []
    for row in draw:
        nb = sum(m_[k] for k in row)
        th = sum(S_[k] for k in row) / nb
        ss = sum((S_[k] - m_[k] * th) ** 2 for k in row)
        seb = math.sqrt(F / (F - 1) * ss) / nb
        ts.append((th - theta) / seb if seb > tiny else 0.0)
    a = [abs(v) for v in ts]
    c = float(np.percentile(a, 95))
    return dict(theta=theta, se=se, t=t, c95=c, interval95=[theta - c * se, theta + c * se], n=n, F=F,
                p_two_sided=((1 + sum(1 for v in a if v >= abs(t))) / (B + 1)) if math.isfinite(t) else None)


def plain_twin(z1, z2, P1, P2, s1, s2, rho1, rho2, kappas):
    u = [a / s1 - b / s2 for a, b in zip(P1, P2)]
    sd_u = sd_(u)
    base = dict(z1=z1, z2=z2, sd_u=sd_u, sign_agree=sgn(z1) == sgn(z2), d_abs=abs(z1 - z2), rho1=rho1, rho2=rho2)
    if sd_u <= 1e-9:
        return dict(base, code="IDENTICAL")
    U = (z1 - z2) / sd_u
    w = [abs(v) / sd_u for v in u]
    ln = line95(w)
    r = sum(1 for v in w if v > ln) / len(w)
    return dict(base, code="EVAL", U=U, line=ln, r=r, beyond={lab: abs(U) > k * ln for lab, k in kappas.items()}, flip={lab: (rho1 > k) != (rho2 > k) for lab, k in kappas.items()})


# ---------------------------------------------------------------------------------------------
# D9
# ---------------------------------------------------------------------------------------------
def plain_break_years(c, sid):
    ys = set()
    for b in c.get("series_breaks") or []:
        ys.add(int(b[0]) if isinstance(b, (list, tuple)) else int(b["year"]))
    sb = c.get("series_break")
    if isinstance(sb, dict) and "year" in sb:
        ys.add(int(sb["year"]))
    dc = c.get("definition_changes")
    dc = " ".join(dc) if isinstance(dc, list) else str(dc or "")
    if "single-race" in dc and "2018" in dc:
        ys.add(2018)
    if sid == "wonder_infant_mortality_rate":
        ys.add(2003)
        ys.add(2007)
    return sorted(ys)


def plain_exposure(post, years, br):
    keep = [(g, t, b0) for g, t, b0 in post if t in years and b0 in years]
    if not keep:
        return None
    k = 0
    for g, t, b0 in keep:
        if any(b0 < b <= t for b in br):
            k += 1
    return k / len(keep)


def plain_group(phi):
    return "SPANNED" if phi >= 0.5 else "NOT SPANNED" if phi == 0 else "BETWEEN"


def plain_delta(y, lab):
    a = [v for v, s in zip(y, lab) if s]
    b = [v for v, s in zip(y, lab) if not s]
    if not a or not b:
        return None
    return sum(a) / len(a) - sum(b) / len(b)


def plain_exact(k, ps):
    """Supplement S1.1.3 in plain loops: the Poisson-binomial tails of the number of beyond cells."""
    dist = [1.0]
    for p in ps:
        new = [0.0] * (len(dist) + 1)
        for j, v in enumerate(dist):
            new[j] += v * (1.0 - p)
            new[j + 1] += v * p
        dist = new
    up, lo = math.fsum(dist[k:]), math.fsum(dist[:k + 1])
    label = "TWINS DIFFER" if up <= 0.05 else "TWINS CLOSER THAN RANDOM TIMING" if lo <= 0.05 else "AT THE LINE"
    return dict(k=k, n=len(ps), expected=math.fsum(ps), p_upper=up, p_lower=lo, label=label)


def plain_resid(v, d_id, s_id, tol=1e-12, max_sweeps=10000):
    """Supplement S1.2.2: alternately subtract design means and series means."""
    nd, ns = max(d_id) + 1, max(s_id) + 1
    v = list(v)
    for _ in range(max_sweeps):
        old = v
        sums, cnt = [0.0] * nd, [0] * nd
        for x, i in zip(v, d_id):
            sums[i] += x
            cnt[i] += 1
        v = [x - sums[i] / cnt[i] for x, i in zip(v, d_id)]
        sums, cnt = [0.0] * ns, [0] * ns
        for x, k in zip(v, s_id):
            sums[k] += x
            cnt[k] += 1
        v = [x - sums[k] / cnt[k] for x, k in zip(v, s_id)]
        if max(abs(a - b) for a, b in zip(v, old)) < tol:
            break
    return v


def plain_delta_fe(ytil, stil):
    den = sum(s * s for s in stil)
    if den <= 1e-9:
        return None
    return sum(s * y for s, y in zip(stil, ytil)) / den


# ---------------------------------------------------------------------------------------------
# main: recompute the results file
# ---------------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--sweep-dir", default=str(SWEEP))
    ap.add_argument("--only", default="D4,D5,D6,D7,D8,D9,D10")
    ap.add_argument("--out", default=None, help="write the report here (a new file)")
    args = ap.parse_args(argv)
    only = set(args.only.split(","))
    sweep = Path(args.sweep_dir)
    res = json.load(open(args.results))
    Q = res["questions_results"]
    d1 = json.load(open(D1J))
    d2 = json.load(open(D2J))
    cal = json.load(open(CALJ))
    kap = cal["question"]["CAL"]["kappa"]
    kappas = {"raw": 1.0, "calibrated": kap["kappa_hat"], "kappa_lo": kap["interval95_BD"][0], "kappa_hi": kap["interval95_BD"][1]}
    k4 = dict(kappas)
    k4["kappa_strict"] = kap["kappa_strict"]
    compare("stamp.kappa", res["stamp"]["kappa"], {"calibrated": kap["kappa_hat"], "interval95_BD": kap["interval95_BD"], "strict": kap["kappa_strict"]})
    run = json.load(open(sweep / "SWEEP_RUN.json"))
    swept = {d["design_id"]: d for d in run["designs"] if d["status"] == "OK"}
    eval_ids = sorted(r["design_id"] for r in d1["designs"] if r.get("code") == "EVAL")
    compare("roster.designs", res["roster"]["designs"], eval_ids)

    S = {}          # design -> plain sample
    RAW = {}
    for did in eval_ids:
        f = load_npz_plain(sweep / Path(swept[did]["file"]).name)
        smp = plain_sample(f["sids"], f["fam"], f["code"], f["att"], f["placebo"])
        if smp is None:
            problems.append(f"{did}: not EVAL on plain recomputation")
            continue
        row = next(r for r in d1["designs"] if r["design_id"] == did)
        compare(f"{did}:series_used", smp["sids"], row["series_used"])
        compare(f"{did}:n_draws", smp["n"], row["n_draws"])
        smp["policy_family"] = row["policy_family"]
        smp["scale_mode"] = f["meta"]["scale_mode"]
        smp["years"] = f["meta"]["years"]
        smp["units"] = f["meta"]["units"]
        S[did] = smp
        RAW[did] = f
    compare("stamp.n_cells", res["stamp"]["n_cells"], sum(len(s["sids"]) for s in S.values()))
    cells_csv = Path(args.results).parent / "CELLS_D4_D10_V1.csv"
    if cells_csv.exists():
        rows_csv = {(r["design_id"], r["series_id"]): r for r in csv.DictReader(open(cells_csv, encoding="utf-8"))}
        if len(rows_csv) != sum(len(s["sids"]) for s in S.values()):
            problems.append("CELLS_D4_D10_V1.csv: row count differs from the cells of the sample")
        for did in eval_ids:
            s = S[did]
            for j, sid in enumerate(s["sids"]):
                r = rows_csv.get((did, sid))
                if r is None:
                    problems.append(f"CELLS_D4_D10_V1.csv: {did}/{sid} missing")
                    continue
                for fld, v in (("att", s["att"][j]), ("sd", s["sd"][j]), ("z", s["z"][j]), ("line_c", s["c"][j]), ("rho", s["rho"][j])):
                    compare(f"cells[{did}/{sid}].{fld}", float(r[fld]), v)
    U7 = lambda q: np.random.default_rng([20261011, 40 + int(q[1:]), 7]).random((R_REP, len(eval_ids)))

    def rep_index(q):
        U = U7(q)
        idx = []
        for r in range(R_REP):
            idx.append([min(int(U[r][j] * S[d]["n"]), S[d]["n"] - 1) for j, d in enumerate(eval_ids)])
        return idx

    des_j = {d: j for j, d in enumerate(eval_ids)}

    # ----------------------------------------------------------------- D4
    if "D4" in only:
        idx = rep_index("D4")
        for view in ("series_view", "family_view"):
            obs_of, rep_of = {}, {}
            for did in eval_ids:
                s = S[did]
                if view == "series_view":
                    for j, sid in enumerate(s["sids"]):
                        obs_of[(did, sid)] = abs(s["z"][j])
                        rep_of[(did, sid)] = s["Zabs"][j]
                else:
                    for f in sorted(set(s["fam"])):
                        rows = [j for j, x in enumerate(s["fam"]) if x == f]
                        obs_of[(did, f)] = mean_([abs(s["z"][j]) for j in rows])
                        rep_of[(did, f)] = [mean_([s["Zabs"][j][b] for j in rows]) for b in range(s["n"])]
            pairs = plain_prune(list(obs_of))
            R = R_REP
            obs, null, des, cols = plain_d4_core(pairs, obs_of, rep_of, des_j, idx, k4, R)
            nm, levels = plain_levels(obs, null, k4, R)
            want = dict(cells_before_pruning=len(obs_of), designs=len(des), columns=len(cols), cells=len(pairs), code="EVAL", observed=obs, null_mean=nm, levels=levels)
            compare(f"D4.{view}", Q["D4"][view], want)
            if view == "series_view":
                fam_of = {d: S[d]["policy_family"] for d in eval_ids}
                blocks = sorted({fam_of[d] for d, _ in pairs})
                rows_out = []
                posn = {p: i for i, p in enumerate(pairs)}
                for f in blocks:
                    keep = plain_prune([p for p in pairs if fam_of[p[0]] != f])
                    if not keep:
                        rows_out.append((f, None))
                        continue
                    des2 = sorted({r for r, _ in keep})
                    cols2 = sorted({c for _, c in keep})
                    row = [des2.index(r) for r, _ in keep]
                    col = [cols2.index(c) for _, c in keep]
                    o2 = plain_dec([obs_of[p] for p in keep], row, col, len(des2), len(cols2))
                    null2 = {k: [] for k in o2}
                    for r in range(R_LOO):
                        x = [rep_of[p][idx[r][des_j[p[0]]]] for p in keep]
                        st = plain_dec(x, row, col, len(des2), len(cols2))
                        for k in o2:
                            null2[k].append(st[k])
                    nm2 = {k: mean_(v) for k, v in null2.items()}
                    kk = kappas["calibrated"]
                    e2 = {k: o2[k] - kk ** 2 * nm2[k] for k in o2}
                    p2 = {k: (1 + sum(1 for v in null2[k] if ge(kk ** 2 * v, o2[k]))) / (R_LOO + 1) for k in PARTS}
                    rows_out.append((f, dict(designs=len(des2), columns=len(cols2), cells=len(keep), excess={k: e2[k] for k in PARTS}, p_one_sided=p2, label=plain_label(p2, e2))))
                loo = res["questions_results"]["D4"]["series_view"]["leave_one_policy_family_out"]
                got_blocks = {b["policy_family_removed"]: b for b in loo["blocks"]}
                full_label = levels["calibrated"]["label"]
                for f, rr in rows_out:
                    if rr is None:
                        compare(f"D4.loo[{f}].code", got_blocks[f]["code"], "EMPTY")
                    else:
                        compare(f"D4.loo[{f}]", got_blocks[f], dict(rr, code="EVAL", policy_family_removed=f))
                ev = [rr for _, rr in rows_out if rr]
                summ = dict(R=R_LOO, blocks=len(blocks), blocks_evaluated=len(ev), full_sample_label=full_label)
                for k in PARTS:
                    summ[f"excess_{NAMES[k].lower()}_min_max"] = [min(r["excess"][k] for r in ev), max(r["excess"][k] for r in ev)]
                summ["n_blocks_changing_label"] = sum(1 for r in ev if r["label"] != full_label)
                compare("D4.loo.summary", loo["summary"], summ)

    # ----------------------------------------------------------------- D5
    count_series = set(res["roster"]["count_series"])
    inv = list(csv.DictReader(open(INV, encoding="utf-8")))
    if "D5" in only:
        idx = rep_index("D5")
        registered = defaultdict(set)
        for r in inv:
            if r["row_type"] == "usage" and r["unit_kind"] == "design" and r["role"] in ("primary", "secondary") and r["status"] == "estimated" and r["catalog_series_id"]:
                registered[r["unit_id"]].add(r["catalog_series_id"])
        nc = {(c["design_id"], c["family"]): c["code"] == "NC" for c in d2["cells"]}
        by_key = defaultdict(list)
        for did in eval_ids:
            s = S[did]
            for j, sid in enumerate(s["sids"]):
                key = sid + "[count]" if (s["scale_mode"] == "count" and sid in count_series) else sid
                by_key[key].append((did, j))
        rng = np.random.default_rng([20261011, 45, 1])
        rows_out, below, labels = [], [], defaultdict(list)
        for key in sorted(by_key):
            mem = by_key[key]
            fams = sorted({S[d]["policy_family"] for d, _ in mem})
            sid = key.split("[")[0]
            if len(mem) < 5 or len(fams) < 3:
                below.append(dict(key=key, m=len(mem), F=len(fams), code="BELOW_FLOOR"))
                continue
            a = [S[d]["att"][j] for d, j in mem]
            sdv = [S[d]["sd"][j] for d, j in mem]
            fam_idx = [fams.index(S[d]["policy_family"]) for d, _ in mem]
            Vstar = []
            for r in range(R_REP):
                Vstar.append(var_([S[d]["P"][j][idx[r][des_j[d]]] for d, j in mem]))
            Vb, Nb, dropped = plain_d5_boot(a, sdv, fam_idx, len(fams), rng, B_BOOT)
            nreg = sum(1 for d, _ in mem if sid in registered.get(d, ()))
            share_nc = sum(1 for d, j in mem if nc.get((d, S[d]["fam"][j]))) / len(mem)
            stratum = "REGISTERED" if nreg >= 5 else "NC-DOMINATED" if share_nc >= 0.8 else "OTHER"
            lv = {}
            for lab, k in kappas.items():
                vals = plain_d5_values(a, sdv, k)
                p = (1 + sum(1 for v in Vstar if ge(k ** 2 * v, vals["V"]))) / (R_REP + 1)
                t2b = [v - k ** 2 * n for v, n in zip(Vb, Nb)]
                lv[lab] = dict(vals, kappa=k, p_one_sided=p, beyond_noise=bool(p <= 0.05 and vals["tau2"] > 0), tau2_interval95=pctl(t2b), bootstrap_defined=len(t2b))
            want = dict(key=key, m=len(mem), F=len(fams), stratum=stratum, n_registered=nreg, share_nc_cells=share_nc, bootstrap_dropped=dropped, levels=lv)
            reg = [(q, d, j) for q, (d, j) in enumerate(mem) if sid in registered.get(d, ())]
            if len(reg) >= 5 and len({S[d]["policy_family"] for _, d, _ in reg}) >= 3:
                qs = [q for q, _, _ in reg]
                sub = {}
                for lab, k in kappas.items():
                    vals = plain_d5_values([a[q] for q in qs], [sdv[q] for q in qs], k)
                    Vs = [var_([S[d]["P"][j][idx[r][des_j[d]]] for _, d, j in reg]) for r in range(R_REP)]
                    sub[lab] = dict(vals, kappa=k, p_one_sided=(1 + sum(1 for v in Vs if ge(k ** 2 * v, vals["V"]))) / (R_REP + 1))
                want["registered_subset"] = dict(F=len({S[d]["policy_family"] for _, d, _ in reg}), levels=sub)
            rows_out.append(want)
            for lab in kappas:
                if lv[lab]["beyond_noise"]:
                    labels[lab].append(key)
        got = {r["key"]: r for r in Q["D5"]["series"]}
        compare("D5.n_series", len(Q["D5"]["series"]), len(rows_out))
        for w in rows_out:
            if w["key"] not in got:
                problems.append(f"D5: key {w['key']} missing in the results file")
            else:
                compare(f"D5[{w['key']}]", got[w["key"]], w)
        compare("D5.below_floor", [dict(key=b["key"], m=b["m"], F=b["F"], code=b["code"]) for b in Q["D5"]["below_floor"]], below)
        summ = dict(keys_at_or_above_floor=len(rows_out), keys_listed_below_floor=len(below), expected_by_random_timing_5pct_of_keys=0.05 * len(rows_out))
        compare("D5.summary", {k: Q["D5"]["summary"][k] for k in summ}, summ)
        for lab in kappas:
            cnt = len(labels[lab])
            compare(f"D5.summary.{lab}.keys_with_label", Q["D5"]["summary"]["levels"][lab]["keys_with_label"], cnt)
            compare(f"D5.summary.{lab}.median_H", Q["D5"]["summary"]["levels"][lab]["median_H"], median_([r["levels"][lab]["H"] for r in rows_out]) if rows_out else None)
            for st in ("REGISTERED", "NC-DOMINATED", "OTHER"):
                w = dict(keys=sum(1 for r in rows_out if r["stratum"] == st), keys_with_label=sum(1 for r in rows_out if r["stratum"] == st and r["levels"][lab]["beyond_noise"]))
                compare(f"D5.summary.{lab}.{st}", Q["D5"]["summary"]["levels"][lab]["by_stratum"][st], w)
        compare("D5.summary.keys_with_label_at_calibrated", Q["D5"]["summary"]["keys_with_label_at_calibrated"], labels["calibrated"])

    # ----------------------------------------------------------------- D6
    if "D6" in only:
        rate = np.load(sweep / "series_store_v1.npz", allow_pickle=False)
        cnts = np.load(sweep / "series_store_v1_count.npz", allow_pickle=False)
        s_units = [str(u) for u in rate["units"]]
        s_years = [int(y) for y in rate["years"]]
        by_key = defaultdict(list)
        codes = defaultdict(Counter)
        for did in eval_ids:
            f = RAW[did]
            uidx = [s_units.index(u) for u in f["meta"]["units"]]
            yidx = [s_years.index(y) for y in f["meta"]["years"]]
            for i, sid in enumerate(f["sids"]):
                codes[sid][f["code"][i]] += 1
                if f["code"][i] != "SUPPORTED":
                    continue
                fin = [v for v in f["placebo"][i] if v == v]
                sd, cut = (sd_(fin), line95([abs(v) for v in fin])) if len(fin) >= 2 else (float("nan"), float("nan"))
                if not (f["sd"][i] == f["sd"][i] and agree(sd, f["sd"][i]) and agree(cut, f["cutoff"][i])):
                    problems.append(f"D6: stored sd or cutoff of {did}/{sid} differs from the placebo draws")
                if not (sd == sd and sd > 0 and cut == cut and cut > 0):
                    continue
                key = sid + "[count]" if (f["meta"]["scale_mode"] == "count" and sid in count_series) else sid
                st = cnts if key.endswith("[count]") else rate
                N, mean, sdw = plain_scale(st[sid + "__val"], st[sid + "__ok"], uidx, yidx)
                mde = cut + MDE_Z * sd
                by_key[key].append(dict(mde=mde, rel=(mde / abs(mean)) if (mean is not None and mean != 0) else None, sds=(mde / sdw) if sdw else None))
        fam_of = {sid: f for sid, f in zip(RAW[eval_ids[0]]["sids"], RAW[eval_ids[0]]["fam"])}
        tab, below = [], []
        for key in sorted(by_key):
            cells = by_key[key]
            if len(cells) < 3:
                below.append(dict(key=key, supported_designs=len(cells), code="BELOW_FLOOR"))
                continue
            row = dict(key=key, supported_designs=len(cells), family=fam_of[key.split("[")[0]])
            for fld, nm in (("mde", "mde"), ("rel", "mde_rel"), ("sds", "mde_sd")):
                v = [c[fld] for c in cells if c[fld] is not None]
                row[nm] = dict(n=len(v), median=median_(v) if v else None, min=min(v) if v else None, max=max(v) if v else None)
            row["levels"] = {}
            for lab, k in kappas.items():
                md = row["mde_sd"]["median"]
                row["levels"][lab] = dict(kappa=k, mde_median=k * row["mde"]["median"], mde_rel_median=(k * row["mde_rel"]["median"]) if row["mde_rel"]["median"] is not None else None,
                                          mde_sd_median=(k * md) if md is not None else None, **{"class": plain_class(k * md if md is not None else None)})
            tab.append(row)
        got = {r["key"]: r for r in Q["D6"]["series"]}
        compare("D6.keys", sorted(got), [r["key"] for r in tab])
        for w in tab:
            if w["key"] in got:
                compare(f"D6[{w['key']}]", got[w["key"]], w)
        compare("D6.below_floor", [dict(key=b["key"], supported_designs=b["supported_designs"], code=b["code"]) for b in Q["D6"]["keys_below_floor"]], below)
        compare("D6.supported_cells", Q["D6"]["supported_cells"], sum(len(v) for v in by_key.values()))
        uns = sorted(sid for sid in fam_of if not any(k.split("[")[0] == sid for k in by_key))
        compare("D6.unsupported", [u["series_id"] for u in Q["D6"]["unsupported_series"]], uns)
        for u in Q["D6"]["unsupported_series"]:
            compare(f"D6.unsupported[{u['series_id']}].codes", u["codes"], dict(codes[u["series_id"]]))
        for lab in ("raw", "calibrated"):
            compare(f"D6.classes.{lab}", Q["D6"]["classes"][lab], dict(Counter(r["levels"][lab]["class"] for r in tab if r["levels"][lab]["class"])))

    # ----------------------------------------------------------------- D7
    if "D7" in only:
        papers = []
        for r in inv:
            if r["row_type"] == "usage" and r["layer"] == "replication_paper" and r["unit_kind"] == "paper":
                papers.append(dict(id=r["unit_id"], family=r["family"], type=plain_dtype(r["unit_of_observation"]), status=r["status"]))
        for p in papers:
            p["comp"] = p["status"] != "not_comparable"
            p["strict"] = p["status"] in ("matched", "confirmed")
            p["broad"] = p["strict"] or p["status"] == "directional"
        comp = sorted([p for p in papers if p["comp"]], key=lambda p: p["id"])
        want = dict(papers=len(papers), comparable=len(comp))
        want["rates"] = {}
        for rate in ("strict", "broad"):
            x = sum(1 for p in comp if p[rate])
            want["rates"][rate] = dict(successes=x, n=len(comp), rate=x / len(comp), interval95_exact=plain_cp(x, len(comp)))
        compare("D7.top", {k: Q["D7"][k] for k in ("papers", "comparable")}, dict(papers=len(papers), comparable=len(comp)))
        compare("D7.rates", Q["D7"]["rates"], want["rates"])
        for gname, gkey, j0 in (("family", "family", 1), ("data_type", "type", 3)):
            names = sorted({p[gkey] for p in comp})
            gid = [names.index(p[gkey]) for p in comp]
            tab = {}
            for nm in sorted({p[gkey] for p in papers}):
                mem = [p for p in papers if p[gkey] == nm]
                cm = [p for p in mem if p["comp"]]
                tab[nm] = dict(papers=len(mem), comparable=len(cm), strict=dict(successes=sum(1 for p in cm if p["strict"]), n=len(cm), interval95_exact=plain_cp(sum(1 for p in cm if p["strict"]), len(cm))),
                               broad=dict(successes=sum(1 for p in cm if p["broad"]), n=len(cm), interval95_exact=plain_cp(sum(1 for p in cm if p["broad"]), len(cm))))
            compare(f"D7.{gname}.groups", Q["D7"][gname]["groups"], tab)
            n_big = sum(1 for nm in names if sum(1 for g in gid if g == names.index(nm)) >= 3)
            met = len(comp) >= 20 and n_big >= 3
            compare(f"D7.{gname}.floor.met", Q["D7"][gname]["floor"]["met"], met)
            for k, rate in enumerate(("strict", "broad")):
                y = [1.0 if p[rate] else 0.0 for p in comp]
                T = plain_T(y, gid, len(names))
                got = Q["D7"][gname]["tests"][rate]
                compare(f"D7.{gname}.{rate}.T", got["T"], T)
                if met and T is not None:
                    rng = np.random.default_rng([20261011, 47, j0 + k])
                    cnt = 0
                    for _ in range(R_REP):
                        ys = [float(v) for v in rng.permutation(np.array(y))]
                        Ts = plain_T(ys, gid, len(names))
                        cnt += int(Ts is not None and ge(Ts, T))
                    compare(f"D7.{gname}.{rate}.p", got["p"], (1 + cnt) / (R_REP + 1))
                    compare(f"D7.{gname}.{rate}.label", got["label"], "DIFFERS" if (1 + cnt) / (R_REP + 1) <= 0.05 else "NOT SEPARATED")

    # ----------------------------------------------------------------- D8
    if "D8" in only:
        pairs_doc = re.findall(r"^\| ([ABC]\d) \| ([^|]+) \| (\S+) \| (\S+) \|$", BUILD_DOC.read_text(encoding="utf-8"), re.M)
        cells = []
        for pid, tier, s1, s2 in pairs_doc:
            for did in eval_ids:
                s = S[did]
                if s1 in s["sids"] and s2 in s["sids"]:
                    a, b = s["sids"].index(s1), s["sids"].index(s2)
                    rec = plain_twin(s["z"][a], s["z"][b], s["P"][a], s["P"][b], s["sd"][a], s["sd"][b], s["rho"][a], s["rho"][b], kappas)
                    rec.update(pair=pid, tier=tier.strip()[0], design_id=did, policy_family=s["policy_family"])
                    cells.append(rec)
        compare("D8.n_cells", Q["D8"]["n_cells"], len(cells))
        got = {(c["pair"], c["design_id"]): c for c in Q["D8"]["cells"]}
        for c in cells:
            g = got.get((c["pair"], c["design_id"]))
            if g is None:
                problems.append(f"D8: cell {c['pair']}/{c['design_id']} missing")
                continue
            compare(f"D8[{c['pair']}/{c['design_id']}]", g, c)
        tiers = {"A": ("A1", "A3"), "B": ("B1", "B2", "B3", "B4", "B5", "B6"), "C": ("C1", "C2")}
        for t, pids in tiers.items():
            ev = [c for c in cells if c["tier"] == t and c["pair"] in pids and c["code"] == "EVAL"]
            allc = [c for c in cells if c["tier"] == t and c["pair"] in pids]
            fams = [c["policy_family"] for c in ev]
            want = dict(cells=len(allc), identical=len(allc) - len(ev), n=len(ev), F=len(set(fams)))
            lv = {}
            for lab, k in kappas.items():
                d = dict(kappa=k)
                if ev:
                    d["share_beyond"] = mean_([float(c["beyond"][lab]) for c in ev])
                    d["mean_r"] = mean_([c["r"] for c in ev])
                    y = [float(c["beyond"][lab]) - c["r"] for c in ev]
                    d["delta_D8"] = mean_(y)
                    d["flips"] = sum(1 for c in ev if c["flip"][lab])
                    events = sum(1 for c in ev if c["beyond"][lab])
                    d["exact"] = plain_exact(events, [c["r"] for c in ev])
                    d["E1_degenerate"] = events < 5 or len(ev) - events < 5
                    if lab in ("raw", "calibrated") and len(ev) >= 8 and len(set(fams)) >= 5:
                        e1 = plain_e1(y, fams, [20261011, 48], B_BOOT)
                        d["E1"] = dict(theta=e1["theta"], se=e1["se"], t=e1["t"], c95=e1["c95"], interval95=e1["interval95"], p_two_sided=e1["p_two_sided"], n=e1["n"], F=e1["F"])
                        d["label_E1"] = "TWINS DIFFER" if e1["interval95"][0] > 0 else "TWINS CLOSER THAN RANDOM TIMING" if e1["interval95"][1] < 0 else "AT THE LINE"
                        d["label"] = d["exact"]["label"] if d["E1_degenerate"] else d["label_E1"]
                    elif lab in ("raw", "calibrated"):
                        d["label"] = "BELOW_FLOOR"
                lv[lab] = d
            want["levels"] = lv
            compare(f"D8.tiers.{t}", Q["D8"]["tiers"][t], want)

    # ----------------------------------------------------------------- D9
    if "D9" in only:
        cat = json.load(open(CATALOG))["series"]
        man = json.load(open(sweep / "SWEEP_MANIFEST.json"))
        gt = {}
        for did in eval_ids:
            r = json.load(open(ROOT / man["designs"][did]["folder"] / "RESULT.json"))
            gt[did] = [(int(c["g"]), int(c["t"]), int(c["base_period"])) for c in r["registered_results"]["primary"]["result"]["group_time"] if c.get("post")]
            del r
        doc = {}
        for m in re.finditer(r"^\| (\S+) \| ((?:\d{4})(?:, \d{4})*) \|$", BUILD_DOC.read_text(encoding="utf-8"), re.M):
            doc[m.group(1)] = [int(v) for v in m.group(2).split(", ")]
        cells = []
        for did in eval_ids:
            s = S[did]
            for j, sid in enumerate(s["sids"]):
                br = plain_break_years(cat[sid], sid)
                if br:
                    if doc.get(sid) != br:
                        problems.append(f"D9: break years of {sid} differ from the registered table")
                rec = dict(design_id=did, policy_family=s["policy_family"], series_id=sid, rho=s["rho"][j], z=s["z"][j])
                if not br:
                    rec["group"] = "NO_BREAK"
                else:
                    phi = plain_exposure(gt[did], set(int(y) for y in cat[sid]["years"]), br)
                    if phi is None:
                        rec["group"] = "NO_POST"
                    else:
                        rec["phi"], rec["group"] = phi, plain_group(phi)
                cells.append(rec)
        got = {(c["design_id"], c["series_id"]): c for c in Q["D9"]["cells"]} if "cells" in Q["D9"] else {}
        csv_path = Path(args.results).parent / "D9_CELLS_V1.csv"
        rows = list(csv.DictReader(open(csv_path))) if csv_path.exists() else []
        csv_group = {(r["design_id"], r["series_id"]): r for r in rows}
        for c in (cells if rows else []):
            r = csv_group.get((c["design_id"], c["series_id"]))
            if r is None or r["group"] != c["group"] or (c.get("phi") is not None and not agree(float(r["phi"]), c["phi"])):
                problems.append(f"D9: cell {c['design_id']}/{c['series_id']} differs from D9_CELLS_V1.csv")
        if not rows:
            print("note: D9_CELLS_V1.csv not found next to the results file; the CSV comparison of the D9 cells is skipped", file=sys.stderr)
        compare("D9.cells_total", Q["D9"]["cells_total"], len(cells))
        compare("D9.groups", Q["D9"]["groups"], dict(Counter(c["group"] for c in cells)))
        lev = {}
        sel = [c for c in cells if c["group"] in ("SPANNED", "NOT SPANNED")]
        for lab, k in kappas.items():
            d = dict(kappa=k)
            for g in ("SPANNED", "NOT SPANNED", "BETWEEN", "NO_BREAK"):
                m = [c for c in cells if c["group"] == g]
                d["share_beyond_" + g.replace(" ", "_")] = mean_([float(c["rho"] > k) for c in m]) if m else None
            d["delta_pooled"] = plain_delta([float(c["rho"] > k) for c in sel], [c["group"] == "SPANNED" for c in sel]) if sel else None
            lev[lab] = d
        nS = sum(1 for c in sel if c["group"] == "SPANNED")
        nN = sum(1 for c in sel if c["group"] == "NOT SPANNED")
        fams = {c["policy_family"] for c in sel}
        met = nS >= 20 and nN >= 20 and len(fams) >= 5
        compare("D9.floor.met", Q["D9"]["floor"]["met"], met)
        if met:
            strata = []
            for sid in sorted({c["series_id"] for c in sel}):
                mem = sorted([c for c in sel if c["series_id"] == sid], key=lambda c: c["design_id"])
                if any(c["group"] == "SPANNED" for c in mem) and any(c["group"] == "NOT SPANNED" for c in mem):
                    strata.append(mem)
            flat = [c for mem in strata for c in mem]
            labels = [c["group"] == "SPANNED" for c in flat]
            bounds, k0 = [], 0
            for mem in strata:
                bounds.append((k0, k0 + len(mem)))
                k0 += len(mem)
            Y = {lab: [float(c["rho"] > k) for c in flat] for lab, k in kappas.items()}
            obs = {lab: plain_delta(Y[lab], labels) for lab in kappas}
            rng = np.random.default_rng([20261011, 49, 1])
            cnt = {lab: 0 for lab in kappas}
            for _ in range(R_REP):
                lab_s = list(labels)
                for a0, a1 in bounds:
                    perm = rng.permutation(a1 - a0)
                    seg = labels[a0:a1]
                    lab_s[a0:a1] = [seg[int(q)] for q in perm]
                for lab in kappas:
                    ds = plain_delta(Y[lab], lab_s)
                    cnt[lab] += int(ds is not None and ge(abs(ds), abs(obs[lab])))
            names = sorted({c["policy_family"] for c in sel})
            F = len(names)
            draw = np.random.default_rng([20261011, 49, 2]).integers(0, F, size=(B_BOOT, F))
            bid = [names.index(c["policy_family"]) for c in sel]
            slab = [c["group"] == "SPANNED" for c in sel]
            boot = {}
            for lab, k in kappas.items():
                y = [float(c["rho"] > k) for c in sel]
                deltas, dropped = [], 0
                for row in draw:
                    cnts = [0] * F
                    for f in row:
                        cnts[f] += 1
                    nSb = nNb = sSb = sNb = 0.0
                    for i in range(len(sel)):
                        w = cnts[bid[i]]
                        if slab[i]:
                            nSb += w
                            sSb += w * y[i]
                        else:
                            nNb += w
                            sNb += w * y[i]
                    if nSb > 0 and nNb > 0:
                        deltas.append(sSb / nSb - sNb / nNb)
                    else:
                        dropped += 1
                boot[lab] = dict(interval95=pctl(deltas), defined=len(deltas), dropped=dropped)
            for lab in kappas:
                p = (1 + cnt[lab]) / (R_REP + 1)
                lev[lab].update(delta_stratified=obs[lab], p_permutation_two_sided=p, bootstrap=boot[lab],
                                label="BREAK EXPOSURE ADDS" if (p <= 0.05 and boot[lab]["interval95"] is not None and boot[lab]["interval95"][0] > 0) else "NOT SEPARATED")
        if met:
            dn, sn = sorted({c["design_id"] for c in sel}), sorted({c["series_id"] for c in sel})
            d_id = [dn.index(c["design_id"]) for c in sel]
            s_id = [sn.index(c["series_id"]) for c in sel]
            Sv = [1.0 if c["group"] == "SPANNED" else 0.0 for c in sel]
            names = sorted({c["policy_family"] for c in sel})
            Fn = len(names)
            members = [[i for i, c in enumerate(sel) if c["policy_family"] == f] for f in names]
            Ys = {lab: [float(c["rho"] > k) for c in sel] for lab, k in kappas.items()}
            St = plain_resid(Sv, d_id, s_id)
            fe_obs = {lab: plain_delta_fe(plain_resid(Ys[lab], d_id, s_id), St) for lab in kappas}
            draw3 = np.random.default_rng([20261011, 49, 3]).integers(0, Fn, size=(999, Fn))
            boots, dropped = {lab: [] for lab in kappas}, {lab: 0 for lab in kappas}
            for row in draw3:
                ix = [i for f in row for i in members[f]]
                dd, ss, sv = [d_id[i] for i in ix], [s_id[i] for i in ix], [Sv[i] for i in ix]
                stl = plain_resid(sv, dd, ss)
                for lab in kappas:
                    v = plain_delta_fe(plain_resid([Ys[lab][i] for i in ix], dd, ss), stl)
                    if v is None:
                        dropped[lab] += 1
                    else:
                        boots[lab].append(v)
            for lab in kappas:
                lev[lab]["fe_sensitivity"] = dict(delta_FE=fe_obs[lab], interval95=pctl(boots[lab]), defined=len(boots[lab]), dropped=dropped[lab], B=999, cells=len(sel), designs=len(dn),
                                                  series=len(sn), F=Fn)
        compare("D9.levels", Q["D9"]["levels"], lev)

    # ----------------------------------------------------------------- D10
    if "D10" in only:
        cat_family, series_fam = {}, set()
        for r in inv:
            if r["row_type"] == "variable" and r["layer"] == "connector_catalog":
                cat_family[r["outcome_key"]] = r["family"]
                if r["data_state"] in ("on_disk_parsed", "on_disk_exports"):
                    series_fam.add(r["family"])
        results = set()
        for r in inv:
            if r["row_type"] != "usage":
                continue
            if r["layer"] == "l2_design" and r["unit_kind"] == "design" and r["status"] == "estimated" and r["role"] in ("primary", "secondary"):
                fam = r["family"] or cat_family.get(r["outcome_key"])
                if fam and r["policy_group"]:
                    results.add((r["policy_group"], fam))
            elif r["layer"] == "replication_paper" and r["unit_kind"] == "paper" and r["family"] and r["policy_group"]:
                results.add((r["policy_group"], r["family"]))
        rows = []
        for r in inv:
            if r["row_type"] == "usage" and r["layer"] == "policy_db_text" and r["unit_kind"] == "db_text_group":
                st = "RESULT" if (r["policy_group"], r["family"]) in results else "SERIES" if r["family"] in series_fam else "NEITHER"
                rows.append((r["policy_group"], r["family"], st, int(r["n_texts_db"] or 0), int(r["n_texts_db_state"] or 0), int(r["n_texts_db_shortlist"] or 0)))
        compare("D10.rows", Q["D10"]["rows"], len(rows))
        compare("D10.pairs_with_result", Q["D10"]["pairs_with_result"], len(results))
        compare("D10.families_with_series_on_disk", Q["D10"]["families_with_series_on_disk"], sorted(series_fam))

        def sh(sub, c):
            tot = sum(r[c] for r in sub)
            d = {"mentions": tot}
            for st in ("RESULT", "SERIES", "NEITHER"):
                m = sum(r[c] for r in sub if r[2] == st)
                d[st] = {"mentions": m, "share": (m / tot) if tot else None}
            return d
        for nm, c in (("state_law_texts", 4), ("all_texts", 3), ("shortlist", 5)):
            compare(f"D10.overall.{nm}", Q["D10"]["overall"][nm], sh(rows, c))
            compare(f"D10.overall_without_other.{nm}", Q["D10"]["overall_without_other"][nm], sh([r for r in rows if r[1] != "other"], c))
        for g in sorted({r[0] for r in rows}):
            compare(f"D10.by_policy_group.{g}", Q["D10"]["by_policy_group"][g], sh([r for r in rows if r[0] == g], 4))
        for f in sorted({r[1] for r in rows}):
            compare(f"D10.by_family.{f}", Q["D10"]["by_family"][f], sh([r for r in rows if r[1] == f], 4))
        s = sh(rows, 4)
        compare("D10.label", Q["D10"]["label"], "MEASURED" if s["RESULT"]["share"] >= 0.5 else "MEASURABLE, NOT MEASURED" if s["RESULT"]["share"] + s["SERIES"]["share"] >= 0.5 else "MOSTLY OUTSIDE OUR DATA")

    report = dict(results_file=str(args.results), questions_checked=sorted(only), failures=len(problems), problems=problems[:200],
                  largest_relative_difference_by_field={k: v for k, v in sorted(worst.items(), key=lambda kv: -kv[1])[:30]},
                  largest_relative_difference=max(worst.values()) if worst else 0.0)
    print(json.dumps(report, indent=1))
    if args.out:
        with open(args.out, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=1) + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
