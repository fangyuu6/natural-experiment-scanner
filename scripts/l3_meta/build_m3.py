#!/usr/bin/env python3
"""M3 (rule id EX-1, exploratory): content ladder.  Do effects grow with the plausibility of a causal channel?  On the P5 line; effective n = policy groups.

Rules: docs/plans/l3-meta/PREREG_M3_V1.md (+ amendment and hash records).  Reads the P5 and random-timing union arrays of refresh 20261018_r1 (through the Design
records of build_d4_d10), the D2 reconciled grid and the inventory usage rows.   Run: .venv_did/bin/python -I -B scripts/l3_meta/build_m3.py
Set VECLIB_MAXIMUM_THREADS=OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import late_core as L  # noqa: E402

D = ROOT / "data/l3_meta/m3_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_M3_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_M3_V1_HASH.txt"
MIN_RECORDS = 3
RANK = {"no_channel": 0.0, "unsure": 1.0, "channel": 2.0}
TIER_OF_CODE = {"channel": "T2", "unsure": "T3", "no_channel": "T4"}
R_REP, B_BOOT, N_PERM = 9999, 9999, 9999
SEEDS = dict(rep=20261070, boot=20261071, perm=20261072)
CODE = ["scripts/l3_meta/build_m3.py", "scripts/l3_meta/test_m3.py", "scripts/l3_meta/late_core.py", "scripts/l3_meta/build_r0.py", "scripts/l3_meta/build_m1.py",
        "scripts/l3_meta/m1_core.py", "scripts/l3_meta/coding_tools.py", "scripts/l3_meta/build_d1.py", "scripts/l3_meta/build_d4_d10.py", "scripts/l3_meta/sweep_core.py"]


# ---------------------------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------------------------
def registered_primary(rows):
    """design id -> set of catalogue series ids registered as primary outcome of an estimated design."""
    out = defaultdict(set)
    for r in rows:
        if r["row_type"] == "usage" and r["unit_kind"] == "design" and r["role"] == "primary" and r["status"] == "estimated" and r["catalog_series_id"]:
            out[r["unit_id"]].add(r["catalog_series_id"])
    return out


class Cells:
    """Arrays over the used (design, series) cells of a set of Design records, with tiers and the outcome measures."""

    def __init__(self, designs, groups, grid, primary, scale=1.0, merge_t1=False):
        self.dids = list(designs)
        self.groups = sorted({groups[d] for d in self.dids})
        self.fams = sorted({f for d in designs.values() for f in d.fam})
        rec = []
        for k, did in enumerate(self.dids):
            d = designs[did]
            g = groups[did]
            prim = [s for s in d.sids if s in primary.get(did, ())]
            fam_prim = {d.fam[d.sids.index(s)] for s in prim}
            for j, s in enumerate(d.sids):
                f = d.fam[j]
                code = grid.get((g, f))
                if s in prim:
                    tier = "T0"
                elif f in fam_prim and not merge_t1:
                    tier = "T1"
                elif code:
                    tier = TIER_OF_CODE[code]
                else:
                    tier = "UNCLASSIFIED"
                rec.append((k, self.groups.index(g), self.fams.index(f), j, tier, code, abs(float(d.z[j])) / scale, float(d.rho[j]) / scale))
        self.di = np.array([r[0] for r in rec], dtype=int)
        self.gi = np.array([r[1] for r in rec], dtype=int)
        self.fi = np.array([r[2] for r in rec], dtype=int)
        self.col = np.array([r[3] for r in rec], dtype=int)
        self.tier = np.array([r[4] for r in rec])
        self.code = np.array([r[5] if r[5] else "" for r in rec])
        self.absz = np.array([r[6] for r in rec])
        self.rho = np.array([r[7] for r in rec])
        self.exceed = (self.rho > 1.0).astype(float)
        self.rank = np.array([RANK.get(r[5], np.nan) if r[5] else np.nan for r in rec])
        self.mask = np.isin(self.tier, ("T2", "T3", "T4"))
        if merge_t1:
            self.mask = self.mask | (self.tier == "T1")
        self.nd = len(self.dids)
        self.group_of_design = np.array([self.groups.index(groups[d]) for d in self.dids])
        self.scale = scale


def fe_parts(di, r, a, nd):
    """Within-design slope parts: (numerator, denominator) per design of the regression of a on r with design fixed effects."""
    cnt = np.bincount(di, minlength=nd).astype(float)
    ok = cnt > 0
    mr = np.where(ok, np.bincount(di, weights=r, minlength=nd) / np.maximum(cnt, 1), 0.0)
    rc = r - mr[di]
    return np.bincount(di, weights=rc * a, minlength=nd), np.bincount(di, weights=rc * rc, minlength=nd)


def slope_from_parts(num, den, weights=None):
    w = np.ones_like(num) if weights is None else weights
    d = float((den * w).sum())
    return float((num * w).sum() / d) if d > 0 else float("nan")


def twoway_slope(y, r, di, fi, iters=60):
    """Slope of y on r with design and family fixed effects (alternating demeaning)."""
    def dm(x):
        x = x.astype(float).copy()
        for _ in range(iters):
            for ix in (di, fi):
                s = np.bincount(ix, weights=x)
                c = np.bincount(ix)
                x = x - (s / np.maximum(c, 1))[ix]
        return x
    yd, rd = dm(y), dm(r)
    den = float((rd * rd).sum())
    return float((rd * yd).sum() / den) if den > 1e-12 else float("nan")


def gradient(cs, outcome="absz"):
    m = cs.mask & np.isfinite(cs.rank)
    a = getattr(cs, outcome)
    num, den = fe_parts(cs.di[m], cs.rank[m], a[m], cs.nd)
    return slope_from_parts(num, den), num, den


def group_sums(x, cs):
    return np.bincount(cs.group_of_design, weights=x, minlength=len(cs.groups))


def bootstrap_groups(num_g, den_g, B, rng):
    G = len(num_g)
    out = np.empty(B)
    for b in range(B):
        m = np.bincount(rng.integers(0, G, size=G), minlength=G).astype(float)
        d = (den_g * m).sum()
        out[b] = (num_g * m).sum() / d if d > 0 else np.nan
    return out


def code_table(cs, grid, groups_of):
    """(G, F) rank table of the grid codes for the represented policy groups and the families present."""
    T = np.full((len(cs.groups), len(cs.fams)), np.nan)
    for gi, g in enumerate(cs.groups):
        for fi, f in enumerate(cs.fams):
            c = grid.get((g, f))
            if c:
                T[gi, fi] = RANK[c]
    return T


def permutation_null(cs, table, B, rng, outcome="absz"):
    """Gradient under the code map permuted across policy groups within each outcome family (the marginal distribution of codes in a family is kept)."""
    m = cs.mask
    di, gi, fi = cs.di[m], cs.gi[m], cs.fi[m]
    a = getattr(cs, outcome)[m]
    cnt = np.bincount(di, minlength=cs.nd).astype(float)
    ma = np.bincount(di, weights=a, minlength=cs.nd) / np.maximum(cnt, 1)
    ac = a - ma[di]
    G, F = table.shape
    out = np.empty(B)
    for b in range(B):
        tab = table.copy()
        for f in range(F):
            tab[:, f] = tab[rng.permutation(G), f]
        r = tab[gi, fi]
        if np.isnan(r).any():
            out[b] = np.nan
            continue
        mr = np.bincount(di, weights=r, minlength=cs.nd) / np.maximum(cnt, 1)
        rc = r - mr[di]
        den = (rc * rc).sum()
        out[b] = (rc * ac).sum() / den if den > 0 else np.nan
    return out


def design_stats(cs):
    """Per design: contrast T0 - T4 (NaN if a tier is missing) and the mean percentile of the T0 cells among all the design's cells."""
    con = np.full(cs.nd, np.nan)
    pct = np.full(cs.nd, np.nan)
    for k in range(cs.nd):
        idx = np.where(cs.di == k)[0]
        t0 = idx[cs.tier[idx] == "T0"]
        t4 = idx[cs.tier[idx] == "T4"]
        if len(t0) and len(t4):
            con[k] = cs.absz[t0].mean() - cs.absz[t4].mean()
        if len(t0):
            allz = cs.absz[idx]
            pct[k] = float(np.mean([((allz < cs.absz[t]).sum() + 0.5 * (allz == cs.absz[t]).sum()) / len(allz) for t in t0]))
    return con, pct


def replicate_stats(designs, cs, R, rng, scale=1.0):
    """R replicate matrices (one placebo draw per design, shared by its series, independent across designs): gradient of |z| and of exceedance, the contrast T0 - T4 and the
    mean percentile of the T0 cells.  Tier assignments are those of the observed cells."""
    m = cs.mask & np.isfinite(cs.rank)
    num_a = np.zeros(R)
    num_e = np.zeros(R)
    den_tot = 0.0
    con_sum, con_n = np.zeros(R), np.zeros(R)
    pct_sum, pct_n = np.zeros(R), np.zeros(R)
    for k, did in enumerate(cs.dids):
        d = designs[did]
        idx = np.where(cs.di == k)[0]
        cols = cs.col[idx]
        pick = rng.integers(0, d.P.shape[1], R)
        Pk = d.P[cols][:, pick]
        A = np.abs(Pk) / d.sd[cols][:, None] / scale
        E = (np.abs(Pk) / d.c[cols][:, None] / scale > 1.0).astype(float)
        mm = m[idx]
        if mm.sum() > 1:
            r = cs.rank[idx][mm]
            rc = r - r.mean()
            num_a += rc @ A[mm]
            num_e += rc @ E[mm]
            den_tot += float((rc * rc).sum())
        tr = cs.tier[idx]
        t0, t4 = np.where(tr == "T0")[0], np.where(tr == "T4")[0]
        if len(t0) and len(t4):
            con_sum += A[t0].mean(axis=0) - A[t4].mean(axis=0)
            con_n += 1
        if len(t0):
            ps = []
            for t in t0:
                ps.append(((A < A[t][None, :]).sum(axis=0) + 0.5 * (A == A[t][None, :]).sum(axis=0)) / A.shape[0])
            pct_sum += np.mean(ps, axis=0)
            pct_n += 1
    with np.errstate(all="ignore"):
        return dict(grad=num_a / den_tot, grad_exceed=num_e / den_tot, contrast=con_sum / con_n, percentile=pct_sum / pct_n)


def tier_table(cs):
    out = OrderedDict()
    for t in ("T0", "T1", "T2", "T3", "T4", "UNCLASSIFIED"):
        s = cs.tier == t
        if not s.any():
            out[t] = dict(n_cells=0)
            continue
        out[t] = dict(n_cells=int(s.sum()), n_designs=int(len(set(cs.di[s]))), n_groups=int(len(set(cs.gi[s]))), mean_absz=float(cs.absz[s].mean()), exceed_share=float(cs.exceed[s].mean()),
                      mean_rho=float(cs.rho[s].mean()))
    return out


def reading(grad, p, boot_lo, mde):
    if not np.isfinite(grad):
        return "not evaluable"
    base = "effects grow with channel plausibility" if (grad > 0 and p <= 0.05 and boot_lo > 0 and grad > mde) else "no gradient established"
    return base + ("" if grad > mde else " (gradient below the minimum detectable size: descriptive)")


# ---------------------------------------------------------------------------------------------
def analyse(designs, groups, grid, primary, label, scale=1.0, merge_t1=False, exclude_groups=(), seeds=SEEDS, R=R_REP, Bb=B_BOOT, Bp=N_PERM, fe_family=False):
    ds = OrderedDict((k, v) for k, v in designs.items() if groups[k] not in exclude_groups)
    cs = Cells(ds, groups, grid, primary, scale=scale, merge_t1=merge_t1)
    g, num, den = gradient(cs)
    ge, nume, dene = gradient(cs, "exceed")
    m = cs.mask & np.isfinite(cs.rank)
    out = OrderedDict(label=label, n_designs=cs.nd, n_groups=len(cs.groups), n_cells=int(len(cs.di)), n_gradient_cells=int(m.sum()), tiers=tier_table(cs))
    pairs = {(cs.gi[i], cs.fi[i]) for i in np.where(m)[0]}
    var_groups = 0
    for gi in range(len(cs.groups)):
        codes = {cs.rank[i] for i in np.where(m & (cs.gi == gi))[0]}
        var_groups += int(len(codes) > 1)
    out["effective_n"] = dict(policy_groups=len(cs.groups), groups_with_code_variation=var_groups, distinct_group_family_codes=len(pairs))
    rep = replicate_stats(designs, cs, R, np.random.default_rng(seeds["rep"]), scale=scale)
    sd = float(np.nanstd(rep["grad"], ddof=1))
    mde = L.MDE_FACTOR * sd
    ng, dg = group_sums(num, cs), group_sums(den, cs)
    boot = bootstrap_groups(ng, dg, Bb, np.random.default_rng(seeds["boot"]))
    table = code_table(cs, grid, groups)
    perm = permutation_null(cs, table, Bp, np.random.default_rng(seeds["perm"]))
    permE = permutation_null(cs, table, Bp, np.random.default_rng(seeds["perm"]), outcome="exceed")
    bo = L.pct(boot)
    p = L.one_sided_p_ge(g, perm)
    out["gradient"] = OrderedDict(slope=g, null_mean=float(np.nanmean(rep["grad"])), null_sd=sd, mde=mde, boot_ci=bo, boot_n=int(np.isfinite(boot).sum()), perm_p_one_sided=p,
                                  perm_null_mean=float(np.nanmean(perm)), perm_null_q=L.pct(perm), reading=reading(g, p, bo[0], mde))
    out["gradient_exceed"] = OrderedDict(slope=ge, null_mean=float(np.nanmean(rep["grad_exceed"])), null_sd=float(np.nanstd(rep["grad_exceed"], ddof=1)),
                                         boot_ci=L.pct(bootstrap_groups(group_sums(nume, cs), group_sums(dene, cs), Bb, np.random.default_rng(seeds["boot"] + 1))),
                                         perm_p_one_sided=L.one_sided_p_ge(ge, permE))
    con, pct = design_stats(cs)
    has = np.isfinite(con)
    hasp = np.isfinite(pct)
    cb = np.empty(Bb)
    pb = np.empty(Bb)
    rng = np.random.default_rng(seeds["boot"] + 2)
    G = len(cs.groups)
    for b in range(Bb):
        mult = np.bincount(rng.integers(0, G, size=G), minlength=G)[cs.group_of_design].astype(float)
        wc, wp = mult * has, mult * hasp
        cb[b] = (np.nan_to_num(con) * wc).sum() / wc.sum() if wc.sum() > 0 else np.nan
        pb[b] = (np.nan_to_num(pct) * wp).sum() / wp.sum() if wp.sum() > 0 else np.nan
    cobs = float(con[has].mean()) if has.any() else float("nan")
    pobs = float(pct[hasp].mean()) if hasp.any() else float("nan")
    out["primary_contrast"] = OrderedDict(n_designs=int(has.sum()), contrast_T0_minus_T4=cobs, null_mean=float(np.nanmean(rep["contrast"])), null_sd=float(np.nanstd(rep["contrast"], ddof=1)),
                                          boot_ci=L.pct(cb))
    out["primary_percentile"] = OrderedDict(n_designs=int(hasp.sum()), mean_percentile=pobs, null_mean=float(np.nanmean(rep["percentile"])), null_sd=float(np.nanstd(rep["percentile"], ddof=1)),
                                            boot_ci=L.pct(pb))
    if fe_family:
        m2 = m
        s2 = twoway_slope(cs.absz[m2], cs.rank[m2], cs.di[m2], cs.fi[m2])
        out["gradient_design_and_family_fe"] = dict(slope=s2)
    return out


def stage_run():
    n = L.require_records(REC, MIN_RECORDS)
    out_path = D / "RESULTS_M3_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    t0 = time.time()
    import sweep_core as S
    primary = registered_primary(S.inventory_rows())
    grid = L.grid_codes()
    groups = L.design_groups()
    res = OrderedDict()
    des_p5, skipped_p5, _ = L.design_objects("P5")
    res["P5_primary"] = analyse(des_p5, groups, grid, primary, "P5, T0 T1 T2 T3 T4", fe_family=True)
    res["P5_primary"]["skipped_designs"] = skipped_p5
    print("P5", res["P5_primary"]["gradient"], round(time.time() - t0), "s", flush=True)
    res["P5_no_firearm"] = analyse(des_p5, groups, grid, primary, "P5, firearm_laws designs removed", exclude_groups=("firearm_laws",))
    res["P5_t1_merged"] = analyse(des_p5, groups, grid, primary, "P5, T1 merged into T2 to T4 by its code", merge_t1=True)
    des_u, skipped_u, _ = L.design_objects("U")
    res["U_calibrated"] = analyse(des_u, groups, grid, primary, "random timing, z / kappa-hat", scale=L.KAPPA_HAT)
    res["U_calibrated"]["skipped_designs"] = skipped_u
    stamp = OrderedDict(rule_id="EX-1", question="M3", version="M3-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seeds=SEEDS, hash_records=n,
                        prereg_sha256=L.sha256_file(PREREG), seconds=round(time.time() - t0, 1), code={f: L.sha256_file(ROOT / f) for f in CODE if (ROOT / f).exists()},
                        registered_primary_designs={d: sorted(s) for d, s in primary.items() if d in des_p5})
    D.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(L.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return 0


def main(argv=None):
    argparse.ArgumentParser().parse_args(argv)
    return stage_run()


if __name__ == "__main__":
    sys.exit(main())
