#!/usr/bin/env python3
"""M7 (rule id EX-1, exploratory): which outcome families carry the common drift of N11, and is it carried by shared states or by shared years?

Rules: docs/plans/l3-meta/PREREG_M7_V1.md (+ amendment and hash records).  Reads the sweep files of refresh 20261018_r1 (random timing, z of SUPPORTED cells of
the 89 original series, as N11-V1) and the N1-V2 sets.   Run: .venv_did/bin/python -I -B scripts/l3_meta/build_m7.py
Set VECLIB_MAXIMUM_THREADS=OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import late_core as L  # noqa: E402

D = ROOT / "data/l3_meta/m7_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_M7_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_M7_V1_HASH.txt"
MIN_RECORDS = 3
MIN_SERIES, MIN_WINDOW = 20, 10          # N11-V1 headline pair floors
MIN_ROWS_FAMILY = 100
B_BOOT, N_PLACEBO = 9999, 999
SEEDS = dict(boot=20261052, years=20261053, states=20261054)
SHIFTS = (3, 4, 5)
CODE = ["scripts/l3_meta/build_m7.py", "scripts/l3_meta/test_m7.py", "scripts/l3_meta/late_core.py", "scripts/l3_meta/build_n11.py", "scripts/l3_meta/selref_core.py"]


# ---------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------
def load_designs(sweep, sets_path, states):
    """List of designs: id, policy family, window, onset by state, z (SUPPORTED, finite), series family; the treated state-year matrix is built by `treated_matrix`."""
    sets = {d["design_id"]: d for d in json.load(open(sets_path))["designs"]}
    out = []
    for f in sorted(Path(sweep).glob("*.npz")):
        if f.name.startswith("series_store"):
            continue
        z = np.load(f, allow_pickle=True)
        m = json.loads(str(z["meta_json"]))
        s = sets.get(m["design_id"])
        if s is None:
            continue
        w0, w1 = int(min(m["years"])), int(max(m["years"]))
        onset = dict(s["T_operative_onset_year"])
        coh = {st: int(y) for y, l in s["T_cohorts"].items() for st in l}
        for st in s["T"]:
            if onset.get(st) is None:
                onset[st] = coh[st]
        zz = np.array(z["z"], dtype=float)
        ok = (np.array(z["code"]) == "SUPPORTED") & np.isfinite(zz)
        out.append(dict(id=m["design_id"], family=s["policy_family"], w=(w0, w1), onset={st: int(onset[st]) for st in s["T"]}, z=np.where(ok, zz, np.nan),
                        sfam=[str(x) for x in z["family"]], sids=[str(x) for x in z["series_ids"]]))
    return out


def treated_matrix(d, states, y0, y1, onset=None, order=None):
    """Boolean (n_states, y1 - y0 + 1) matrix of treated state-years of the design's window (treatment persists to the window end)."""
    on = d["onset"] if onset is None else onset
    w0, w1 = d["w"]
    A = np.zeros((len(states), y1 - y0 + 1), dtype=bool)
    ix = {s: k for k, s in enumerate(states)}
    for st, y in on.items():
        A[ix[st], max(w0, int(y)) - y0: w1 - y0 + 1] = True
    if order is not None:
        A = A[order]
    return A


def overlaps(Ai, Aj, lo, hi, y0):
    """(J_S, J_Y, O): Jaccard of the treated state sets, of the treated year sets and of the treated state-year sets within the common window [lo, hi]."""
    a = Ai[:, lo - y0: hi - y0 + 1]
    b = Aj[:, lo - y0: hi - y0 + 1]
    js = L.jaccard_bool(a.any(axis=1), b.any(axis=1))
    jy = L.jaccard_bool(a.any(axis=0), b.any(axis=0))
    return js, jy, L.jaccard_bool(a, b)


def pair_table(designs, nc=None):
    """Headline pairs (different policy families, at least MIN_SERIES common series, at least MIN_WINDOW common window years) and their family rows.
    Returns (pairs [(i, j, lo, hi)], rows [(pair index, family, n, C)], fams)."""
    fams = sorted({f for d in designs for f in d["sfam"]})
    pairs, rows = [], []
    for i in range(len(designs)):
        for j in range(i + 1, len(designs)):
            di, dj = designs[i], designs[j]
            lo, hi = max(di["w"][0], dj["w"][0]), min(di["w"][1], dj["w"][1])
            win = max(0, hi - lo + 1)
            both = np.isfinite(di["z"]) & np.isfinite(dj["z"])
            if win < MIN_WINDOW or int(both.sum()) < MIN_SERIES or di["family"] == dj["family"]:
                continue
            p = len(pairs)
            pairs.append((i, j, lo, hi))
            prod = di["z"] * dj["z"]
            for f in fams:
                sel = both & np.array([x == f for x in di["sfam"]])
                if nc is not None:
                    if (di["id"], f) not in nc or (dj["id"], f) not in nc:
                        continue
                if sel.any():
                    rows.append((p, f, int(sel.sum()), float(prod[sel].mean())))
    return pairs, rows, fams


def pair_stats(designs, pairs, As, y0):
    """J_S, J_Y, O arrays over the pairs for a list of treated matrices."""
    out = np.zeros((len(pairs), 3))
    for p, (i, j, lo, hi) in enumerate(pairs):
        out[p] = overlaps(As[i], As[j], lo, hi, y0)
    return out


# ---------------------------------------------------------------------------------------------
# estimators
# ---------------------------------------------------------------------------------------------
class Rows:
    """Arrays of the family rows: pair index, family index, weight n, y = C, design indices i, j."""

    def __init__(self, pairs, rows, fams):
        self.pi = np.array([r[0] for r in rows], dtype=int)
        self.fi = np.array([fams.index(r[1]) for r in rows], dtype=int)
        self.n = np.array([r[2] for r in rows], dtype=float)
        self.y = np.array([r[3] for r in rows], dtype=float)
        self.I = np.array([pairs[p][0] for p in self.pi], dtype=int)
        self.J = np.array([pairs[p][1] for p in self.pi], dtype=int)
        self.fams = fams


def fit_family(R, f, ov, mult=None, cols=(0, 1)):
    """WLS of C on [1, J_S, J_Y] for family index f; mult = design multiplicities for the bootstrap.  Returns (beta_S, beta_Y, n_rows) or None."""
    sel = np.where(R.fi == f)[0]
    if sel.size == 0:
        return None
    w = R.n[sel] if mult is None else R.n[sel] * mult[R.I[sel]] * mult[R.J[sel]]
    X = np.column_stack([np.ones(sel.size), ov[R.pi[sel]][:, list(cols)]])
    b = L.wls_fit(X, R.y[sel], w)
    return None if b is None else (float(b[1]), float(b[2]), int(sel.size))


def fit_single(R, ov, mult=None):
    """The N11 slope on O for all family rows pooled by pair (weights n): used as a consistency reading; family-demeaned."""
    return fit_pooled(R, ov, mult, cols=(2,))


def fit_pooled(R, ov, mult=None, cols=(0, 1)):
    """Family fixed effects (weighted demeaning within family), slopes on the chosen overlap columns."""
    w = R.n if mult is None else R.n * mult[R.I] * mult[R.J]
    keep = w > 0
    if keep.sum() < 5:
        return None
    fi, wk = R.fi[keep], w[keep]
    X = ov[R.pi[keep]][:, list(cols)]
    y = R.y[keep]
    nf = len(R.fams)
    sw = np.bincount(fi, weights=wk, minlength=nf)
    ok = sw[fi] > 0
    ym = np.bincount(fi, weights=wk * y, minlength=nf) / np.maximum(sw, 1e-300)
    Xd = X.copy()
    for c in range(X.shape[1]):
        Xd[:, c] = X[:, c] - (np.bincount(fi, weights=wk * X[:, c], minlength=nf) / np.maximum(sw, 1e-300))[fi]
    yd = y - ym[fi]
    b = L.wls_fit(Xd[ok], yd[ok], wk[ok])
    return None if b is None else tuple(float(x) for x in b)


def reading(blo_s, bhi_s, blo_y, bhi_y):
    """Section 3 of the pre-registration: bootstrap intervals of beta_S and beta_Y against 0."""
    s_pos, y_pos = blo_s > 0, blo_y > 0
    if s_pos and y_pos:
        return "both"
    if s_pos:
        return "carried by states" if bhi_y >= 0 else "carried by states (years negative)"
    if y_pos:
        return "carried by years" if bhi_s >= 0 else "carried by years (states negative)"
    return "neither"


# ---------------------------------------------------------------------------------------------
def run(refresh, out_path, seeds=SEEDS, B=B_BOOT, n_placebo=N_PLACEBO):
    from selref_core import REGION
    states = sorted(REGION)
    region_of = np.array([REGION[s] for s in states])
    designs = load_designs(Path(refresh) / "sweep", L.SETS, states)
    y0 = min(d["w"][0] for d in designs)
    y1 = max(d["w"][1] for d in designs)
    As = [treated_matrix(d, states, y0, y1) for d in designs]
    pairs, rows, fams = pair_table(designs)
    R = Rows(pairs, rows, fams)
    ov = pair_stats(designs, pairs, As, y0)
    nd = len(designs)
    pol = sorted({d["family"] for d in designs})
    block_of = np.array([pol.index(d["family"]) for d in designs])
    cnt_by_family = {f: int((R.fi == k).sum()) for k, f in enumerate(fams)}
    evalf = [k for k, f in enumerate(fams) if cnt_by_family[f] >= MIN_ROWS_FAMILY]
    out = OrderedDict(n_designs=nd, n_policy_families=len(pol), n_headline_pairs=len(pairs), n_family_rows=int(len(rows)), families=fams, rows_by_family=cnt_by_family,
                      families_evaluable=[fams[k] for k in evalf], overlap_summary={k: [float(np.percentile(ov[:, c], q)) for q in (10, 50, 90)] for c, k in enumerate(("J_S", "J_Y", "O"))},
                      corr_JS_JY=float(np.corrcoef(ov[:, 0], ov[:, 1])[0, 1]))
    # observed
    obs = OrderedDict()
    for k, f in enumerate(fams):
        r = fit_family(R, k, ov)
        obs[f] = None if r is None else dict(beta_S=r[0], beta_Y=r[1], n_rows=r[2], evaluable=bool(k in evalf))
    pooled = fit_pooled(R, ov)
    single = fit_single(R, ov)
    out["observed"] = obs
    out["pooled"] = dict(beta_S=pooled[0], beta_Y=pooled[1]) if pooled else None
    out["pooled_single_O_slope"] = single[0] if single else None
    # bootstrap by policy family
    rng = np.random.default_rng(seeds["boot"])
    bs = {k: [] for k in range(len(fams))}
    bp = []
    for _ in range(B):
        mult = L.block_multiplicity(block_of, len(pol), rng)
        for k in range(len(fams)):
            r = fit_family(R, k, ov, mult)
            if r is not None:
                bs[k].append(r[:2])
        r = fit_pooled(R, ov, mult)
        if r is not None:
            bp.append(r)
    for k, f in enumerate(fams):
        if obs[f] is None:
            continue
        a = np.array(bs[k]) if bs[k] else np.zeros((0, 2))
        if len(a) > 0.5 * B:
            obs[f]["boot_S"], obs[f]["boot_Y"] = L.pct(a[:, 0]), L.pct(a[:, 1])
            obs[f]["boot_n"] = int(len(a))
            if obs[f]["evaluable"]:
                obs[f]["reading"] = reading(obs[f]["boot_S"][0], obs[f]["boot_S"][1], obs[f]["boot_Y"][0], obs[f]["boot_Y"][1])
    a = np.array(bp)
    out["pooled"]["boot_S"], out["pooled"]["boot_Y"] = L.pct(a[:, 0]), L.pct(a[:, 1])
    out["pooled"]["reading"] = reading(out["pooled"]["boot_S"][0], out["pooled"]["boot_S"][1], out["pooled"]["boot_Y"][0], out["pooled"]["boot_Y"][1])
    # placebo A (years) and B (states)
    for name, seed in (("years", seeds["years"]), ("states", seeds["states"])):
        rng = np.random.default_rng(seed)
        per = {k: [] for k in range(len(fams))}
        pp = []
        for _ in range(n_placebo):
            if name == "years":
                As2 = []
                for d in designs:
                    s = int(rng.choice(SHIFTS)) * int(rng.choice([-1, 1]))
                    w0, w1 = d["w"]
                    on2 = {st: min(max(y + s, w0), w1 + 1) for st, y in d["onset"].items()}
                    As2.append(treated_matrix(d, states, y0, y1, onset=on2))
            else:
                As2 = []
                for k, d in enumerate(designs):
                    order = np.arange(len(states))
                    for reg in sorted(set(region_of)):
                        idx = np.where(region_of == reg)[0]
                        order[idx] = rng.permutation(idx)
                    As2.append(As[k][order])
            ov2 = pair_stats(designs, pairs, As2, y0)
            for k in range(len(fams)):
                r = fit_family(R, k, ov2)
                if r is not None:
                    per[k].append(r[:2])
            r = fit_pooled(R, ov2)
            if r is not None:
                pp.append(r)
        plc = OrderedDict()
        for k, f in enumerate(fams):
            if obs[f] is None or not per[k]:
                continue
            a = np.array(per[k])
            plc[f] = dict(beta_S=dict(mean=float(a[:, 0].mean()), q=L.pct(a[:, 0])), beta_Y=dict(mean=float(a[:, 1].mean()), q=L.pct(a[:, 1])), n=int(len(a)))
        a = np.array(pp)
        plc["__pooled__"] = dict(beta_S=dict(mean=float(a[:, 0].mean()), q=L.pct(a[:, 0])), beta_Y=dict(mean=float(a[:, 1].mean()), q=L.pct(a[:, 1])), n=int(len(a)))
        out[f"placebo_{name}"] = plc
    # variant: no_channel cells only
    nc = set()
    with open(Path(refresh) / "d2/NC_CELLS_D2_V1.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            nc.add((r["design_id"], r["outcome_family"]))
    pairs_n, rows_n, _ = pair_table(designs, nc=nc)
    var = OrderedDict(n_pairs=len(pairs_n), n_rows=len(rows_n))
    if rows_n:
        Rn = Rows(pairs_n, rows_n, fams)
        ovn = pair_stats(designs, pairs_n, As, y0)
        pooled_n = fit_pooled(Rn, ovn)
        var["pooled"] = dict(beta_S=pooled_n[0], beta_Y=pooled_n[1]) if pooled_n else None
        rng = np.random.default_rng(seeds["boot"] + 1000)
        bq = []
        for _ in range(B):
            mult = L.block_multiplicity(block_of, len(pol), rng)
            r = fit_pooled(Rn, ovn, mult)
            if r is not None:
                bq.append(r)
        if bq and var["pooled"]:
            a = np.array(bq)
            var["pooled"]["boot_S"], var["pooled"]["boot_Y"] = L.pct(a[:, 0]), L.pct(a[:, 1])
    out["variant_no_channel_cells"] = var
    out["ids"] = [d["id"] for d in designs]
    return out, designs, pairs, ov, R


def stage_run():
    n = L.require_records(REC, MIN_RECORDS)
    out_path = D / "RESULTS_M7_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    t0 = time.time()
    out, designs, pairs, ov, R = run(L.REFRESH, out_path)
    # consistency with N11-V1: O and the pair statistic of build_n11 on the same files
    import build_n11 as N
    DN = N.load_designs(L.REFRESH / "sweep", L.SETS)
    O, C, Nn, WIN = N.pair_tables(DN)
    pos = {d["id"]: k for k, d in enumerate(DN)}
    dev = max(abs(O[pos[designs[i]["id"]], pos[designs[j]["id"]]] - ov[p, 2]) for p, (i, j, lo, hi) in enumerate(pairs))
    out["consistency_O_vs_n11"] = float(dev)
    stamp = OrderedDict(rule_id="EX-1", question="M7", version="M7-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seeds=SEEDS, hash_records=n,
                        prereg_sha256=L.sha256_file(PREREG), seconds=round(time.time() - t0, 1), code={f: L.sha256_file(ROOT / f) for f in CODE if (ROOT / f).exists()})
    D.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(L.clean(OrderedDict(stamp=stamp, results=out)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("pooled", out["pooled"], "consistency", dev, round(time.time() - t0), "s")
    return 0


def main(argv=None):
    argparse.ArgumentParser().parse_args(argv)
    return stage_run()


if __name__ == "__main__":
    sys.exit(main())
