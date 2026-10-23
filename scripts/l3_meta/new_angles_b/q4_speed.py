#!/usr/bin/env python3
"""Rank 4 of PREPLAN_T233_20261021 (section 1): do fast and slow spreaders work differently?

Exploratory (EX-1). No significance labels; intervals are unadjusted.
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/q4_speed.py
"""
import collections
import hashlib
import json
import math
import os

import numpy as np
import pandas as pd
from scipy import stats

ROOT = "."
SD = os.path.join(ROOT, "data", "l3_meta", "new_angles_20261021", "states_decide")
IN = os.path.join(SD, "inputs")
MAPD = os.path.join(SD, "mapping")
SWEEP = os.path.join(ROOT, "data", "l3_meta", "refresh", "20261021_r1", "sweep")
OUT = os.path.join(SD, "q4")

SEED_PERM = [20261022, 4, 1]
SEED_BOOT = [20261022, 4, 2]
NPERM = 2000
NBOOT = 2000
MIN_BLOCKS = 20
MIN_DOM_BLOCKS = 4
MIN_SPID_N = 12
MIN_TOPIC = 8
NAN = float("nan")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fnum(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def rel(p):
    return os.path.relpath(p, ROOT)


def wmedian_lower(vals, w):
    # added after the plan: lower weighted median (first value whose cumulative weight reaches half)
    o = np.argsort(vals, kind="stable")
    v = vals[o]
    cw = np.cumsum(w[o])
    return v[int(np.searchsorted(cw, cw[-1] / 2.0, side="left"))]


def ranks_centred(vals, doms):
    out = np.zeros(len(vals))
    for d in np.unique(doms):
        m = doms == d
        rk = stats.rankdata(vals[m], method="average")
        out[m] = rk - rk.mean()
    return out


def pearson(a, b):
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum()) / den if den > 0 else NAN


def rho_of(s, z, doms):
    if len(s) < 3:
        return NAN
    return pearson(ranks_centred(s, doms), ranks_centred(z, doms))


def sign(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0
    return int(np.sign(x))


# ---------- plain-python independent re-computation (no pandas / scipy ranks) ----------
def midrank_py(vals):
    out = []
    for v in vals:
        less = sum(1 for u in vals if u < v)
        eq = sum(1 for u in vals if u == v)
        out.append(less + (eq + 1) / 2.0)
    return out


def centred_py(vals, doms):
    n = len(vals)
    out = [0.0] * n
    for d in sorted(set(doms)):
        idx = [i for i in range(n) if doms[i] == d]
        rk = midrank_py([vals[i] for i in idx])
        mu = sum(rk) / len(rk)
        for k, i in enumerate(idx):
            out[i] = rk[k] - mu
    return out


def pearson_py(a, b):
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    sab = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    saa = sum((x - ma) ** 2 for x in a)
    sbb = sum((x - mb) ** 2 for x in b)
    return sab / math.sqrt(saa * sbb)


def rho_py(s, z, doms):
    return pearson_py(centred_py(list(s), list(doms)), centred_py(list(z), list(doms)))


def main():
    res_path = os.path.join(OUT, "RESULTS_Q4.json")
    if os.path.exists(res_path):
        raise SystemExit("RESULTS_Q4.json exists; write-once folder, version the name before rerunning")
    os.makedirs(OUT, exist_ok=True)

    p_des = os.path.join(IN, "designs.csv")
    p_coh = os.path.join(IN, "cohorts.csv")
    p_spid = os.path.join(IN, "spid_adoption.csv")
    p_map = os.path.join(MAPD, "FINAL_design_spid_map.csv")
    hashes = {rel(p): sha256(p) for p in (p_des, p_coh, p_spid, p_map)}

    des = pd.read_csv(p_des)
    coh = pd.read_csv(p_coh)
    assert des.design_id.is_unique
    excl = collections.Counter()
    ref_hashes = {}

    # ---------- step 1: design level ----------
    dsn = []
    for r in des.itertuples(index=False):
        did = r.design_id
        c = coh[coh.design_id == did]
        rec = dict(design_id=did, block=int(r.block), domain=str(r.domain),
                   n_treated_total=float(r.n_treated_total))
        if len(c) == 0:
            excl["design_no_cohorts"] += 1
            first = total = last = medyrs = S5 = NAN
            complete = False
        else:
            g = c.g_cal.to_numpy(float)
            w = c.n_treated.to_numpy(float)
            first = float(g.min())
            total = float(w.sum())
            S5 = float(w[g <= first + 5].sum() / total) if total > 0 else NAN
            last = float((g + c.max_event_time.to_numpy(float)).max())
            complete = bool(last >= first + 5 and total >= 3)
            medyrs = float(wmedian_lower(g, w) - first)
            if not complete:
                excl["design_incomplete"] += 1
                if last < first + 5:
                    excl["design_incomplete_reason_last_obs_before_first_plus5"] += 1
                if total < 3:
                    excl["design_incomplete_reason_total_lt_3"] += 1
        noise = float(r.noise_sd) if pd.notna(r.noise_sd) else NAN
        est = float(r.estimate)
        Z = abs(est) / noise if (np.isfinite(noise) and noise > 0) else NAN
        if not np.isfinite(Z):
            excl["design_no_Z_noise_sd_missing"] += 1
        refZ = NAN
        ncell = 0
        rp = os.path.join(SWEEP, did + ".npz")
        if ":" in did:
            excl["design_no_reference_id_has_colon"] += 1
        elif not os.path.exists(rp):
            excl["design_no_reference_file"] += 1
        else:
            with np.load(rp, allow_pickle=True) as f:
                k = f["kept"].astype(float)
                zz = f["z"].astype(float)
            ref_hashes[rel(rp)] = sha256(rp)
            m = (k == 1) & np.isfinite(zz)
            ncell = int(m.sum())
            if m.any():
                refZ = float(np.mean(np.abs(zz[m])))
            else:
                excl["design_reference_file_no_valid_cells"] += 1
        rec.update(first=first, total_treated=total, last_obs=last, complete=complete,
                   S5=S5, medyrs=medyrs, estimate=est, noise_sd=noise, Z=Z,
                   refZ=refZ, n_ref_cells=ncell)
        dsn.append(rec)
    dd = pd.DataFrame(dsn)
    dd["complete"] = dd["complete"].astype(bool)

    # ---------- step 2: blocks ----------
    bl = []
    for b, sub in dd.groupby("block", sort=True):
        comp = sub[sub.complete]
        Zs = sub.Z.dropna()
        refs = sub.refZ.dropna()
        if len(Zs) < len(sub):
            excl["block_some_designs_without_Z_skipped_in_mean"] += 1
        cnt = collections.Counter(sub.domain)
        dom = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if len(cnt) > 1:
            excl["block_mixed_domains_info"] += 1
        bl.append(dict(
            block=int(b), domain=dom, n_designs=int(len(sub)),
            n_complete_designs=int(len(comp)), complete=bool(len(comp) > 0),
            S5=float(comp.S5.mean()) if len(comp) else NAN,
            medyrs=float(comp.medyrs.mean()) if len(comp) else NAN,
            Z=float(Zs.mean()) if len(Zs) else NAN,
            refZ=float(refs.mean()) if len(refs) else NAN,
            ntreat=float(sub.n_treated_total.mean()),
            design_ids=";".join(sub.design_id)))
    bd = pd.DataFrame(bl)
    bd["in_U"] = bd.complete & np.isfinite(bd.Z)
    bd["in_Uref"] = bd.in_U & np.isfinite(bd.refZ)
    excl["blocks_total"] = int(len(bd))
    excl["blocks_not_complete"] = int((~bd.complete).sum())
    excl["blocks_complete_but_Z_missing"] = int((bd.complete & ~np.isfinite(bd.Z)).sum())
    excl["blocks_complete_with_Z_but_no_refZ"] = int((bd.in_U & ~np.isfinite(bd.refZ)).sum())

    U = bd[bd.in_U].reset_index(drop=True)
    n = len(U)
    counts = dict(
        n_designs=int(len(dd)), n_designs_complete=int(dd.complete.sum()),
        n_blocks=int(len(bd)), n_blocks_complete=int(bd.complete.sum()),
        n_blocks_in_U=int(n), n_blocks_in_Uref=int(bd.in_Uref.sum()),
        n_designs_with_refZ=int(np.isfinite(dd.refZ).sum()),
        n_designs_with_Z=int(np.isfinite(dd.Z).sum()))
    counts["exclusions"] = dict(excl)

    out = dict(
        script=rel(os.path.abspath(__file__)),
        plan="docs/plans/l3-meta/PREPLAN_T233_20261021.md section 0 and section 1 (Rank 4)",
        exploratory_rule="EX-1; no significance labels; unadjusted intervals",
        seeds=dict(permutation=SEED_PERM, bootstrap=SEED_BOOT),
        input_sha256=hashes, reference_sha256=ref_hashes,
        counts=counts,
        added_after_plan=[
            "medyrs weighted median is the lower weighted median (first cohort year whose cumulative treated weight reaches half the total)",
            "pattern reading of 'outside the 2.5-97.5 band' taken as rho < p2.5 or rho > p97.5 of the signed permutation draws",
            "null sd uses ddof=1; percentiles use numpy linear interpolation",
            "reference rho is computed on blocks that are in U and have refZ (U is complete blocks with finite Z); bootstrap uses the same universe",
            "domain sign values use scipy.stats.spearmanr within each domain with at least 4 blocks",
            "R-M block Z correlation uses mapped blocks with finite Z; S5 correlation uses mapped complete blocks; both plain Spearman",
            "kept is fractional in these sweep files; the brief's kept==1 rule is applied literally",
        ],
    )

    if n < MIN_BLOCKS:
        out["status"] = "too few"
        out["n_blocks_in_U"] = int(n)
        with open(res_path, "w") as fh:
            json.dump(out, fh, indent=2)
        print("too few complete blocks:", n)
        return

    S5 = U.S5.to_numpy(float)
    Z = U.Z.to_numpy(float)
    doms = U.domain.to_numpy(dtype=str)
    refZ = U.refZ.to_numpy(float)
    ntr = U.ntreat.to_numpy(float)
    med = U.medyrs.to_numpy(float)
    mref = np.isfinite(refZ)

    # ---------- step 3: observed rho and permutation null ----------
    rho = rho_of(S5, Z, doms)
    dom_idx = {d: np.where(doms == d)[0] for d in np.unique(doms)}
    rng = np.random.default_rng(SEED_PERM)
    null = np.empty(NPERM)
    for i in range(NPERM):
        s = S5.copy()
        for d in sorted(dom_idx):
            idx = dom_idx[d]
            s[idx] = S5[rng.permutation(idx)]
        null[i] = rho_of(s, Z, doms)
    lo, hi = np.percentile(null, [2.5, 97.5])
    p_two = (1 + int(np.sum(np.abs(null) >= abs(rho)))) / (1 + NPERM)
    observed = dict(rho=rho, null_mean=float(null.mean()), null_sd=float(null.std(ddof=1)),
                    null_p2_5=float(lo), null_p97_5=float(hi), p_two_sided=p_two, n_perm=NPERM,
                    n_blocks=n, domains=sorted(dom_idx))

    # ---------- step 4: reference and bootstrap ----------
    rho_ref = rho_of(S5[mref], refZ[mref], doms[mref])
    excess = rho - rho_ref
    rng2 = np.random.default_rng(SEED_BOOT)
    b_rho, b_ref, b_exc = [], [], []
    for _ in range(NBOOT):
        j = rng2.integers(0, n, n)
        r1 = rho_of(S5[j], Z[j], doms[j])
        mm = mref[j]
        r2 = rho_of(S5[j][mm], refZ[j][mm], doms[j][mm]) if mm.sum() >= 3 else NAN
        b_rho.append(r1)
        b_ref.append(r2)
        b_exc.append(r1 - r2)
    b_rho = np.array(b_rho)
    b_ref = np.array(b_ref)
    b_exc = np.array(b_exc)
    ok = np.isfinite(b_exc)
    ci_exc = np.percentile(b_exc[ok], [2.5, 97.5])
    ci_rho = np.percentile(b_rho[np.isfinite(b_rho)], [2.5, 97.5])
    reference = dict(n_blocks_ref=int(mref.sum()), rho_ref=rho_ref, excess=excess,
                     excess_ci95=[float(ci_exc[0]), float(ci_exc[1])],
                     rho_ci95=[float(ci_rho[0]), float(ci_rho[1])],
                     boot_valid_excess=int(ok.sum()), n_boot=NBOOT,
                     boot_valid_rho=int(np.isfinite(b_rho).sum()))

    # ---------- step 5: robustness ----------
    rS = ranks_centred(S5, doms)
    rZ = ranks_centred(Z, doms)
    X = np.column_stack([np.ones(n), np.log(ntr)])
    resid = lambda v: v - X @ np.linalg.lstsq(X, v, rcond=None)[0]
    partial = pearson(resid(rS), resid(rZ))
    rho_negmed = rho_of(-med, Z, doms)
    lobo = []
    for k in range(n):
        keep = np.arange(n) != k
        lobo.append(rho_of(S5[keep], Z[keep], doms[keep]))
    lobo = np.array(lobo)
    lodo = {}
    for d in sorted(dom_idx):
        keep = doms != d
        lodo[d] = rho_of(S5[keep], Z[keep], doms[keep])
    lodo_vals = np.array(list(lodo.values()))
    dom_rows = []
    for d in sorted(dom_idx):
        m = doms == d
        nd = int(m.sum())
        if nd >= MIN_DOM_BLOCKS:
            rd, _ = stats.spearmanr(S5[m], Z[m])
            dom_rows.append(dict(domain=d, n_blocks=nd, spearman=fnum(rd),
                                 sign=sign(rd), same_sign_as_pooled=bool(sign(rd) == sign(rho))))
        else:
            dom_rows.append(dict(domain=d, n_blocks=nd, spearman=None, sign=None,
                                 same_sign_as_pooled=None, note="not scored (fewer than 4 blocks)"))
    scored = [r for r in dom_rows if r["spearman"] is not None]
    share_same = (sum(1 for r in scored if r["same_sign_as_pooled"]) / len(scored)) if scored else NAN
    robustness = dict(
        a_partial_on_log_ntreat=partial,
        b_speed_neg_medyrs=rho_negmed,
        c_lobo_min=float(np.nanmin(lobo)), c_lobo_max=float(np.nanmax(lobo)),
        d_lodo_min=float(np.nanmin(lodo_vals)), d_lodo_max=float(np.nanmax(lodo_vals)),
        d_lodo_by_domain={d: fnum(v) for d, v in lodo.items()},
        e_domain_spearman=dom_rows,
        f_share_domains_same_sign_as_pooled=fnum(share_same),
        n_domains_scored=len(scored))

    # ---------- step 6: pattern flag ----------
    c1 = bool(rho < lo or rho > hi)
    c2 = bool(sign(rho) == sign(excess) and sign(rho) != 0)
    c3 = bool(len(scored) > 0 and share_same >= 2 / 3)
    c4 = bool(sign(np.nanmin(lobo)) == sign(rho) and sign(np.nanmax(lobo)) == sign(rho))
    pattern = dict(criteria=dict(outside_null_band=c1, rho_and_excess_same_sign=c2,
                                 two_thirds_domains_same_sign=c3, lobo_keeps_sign=c4),
                   flag=bool(c1 and c2 and c3 and c4))

    # ---------- step 7: R-M SPID speed robustness ----------
    spid = pd.read_csv(p_spid)
    spid["hit"] = (spid.adopt_year <= spid.first_year + 5).astype(float)
    pol = spid.groupby("policy").agg(
        first_year=("first_year", "first"), n_rows=("state", "size"),
        n_adopters=("n_adopters", "first"), topic=("majortopic_str", "first"),
        source=("source", "first"), complete_window=("complete_window", "first"),
        date_conflict=("date_conflict", "first"), S5_spid=("hit", "mean"))
    mp = pd.read_csv(p_map)
    mp_map = dict(zip(mp.design_id, mp.spid_policy))
    missing_pol = [p for p in mp_map.values() if p not in pol.index]
    dd["S5_spid"] = [float(pol.S5_spid[mp_map[x]]) if (x in mp_map and mp_map[x] in pol.index) else NAN
                     for x in dd.design_id]
    bsp = dd[dd.design_id.isin(list(mp_map))].groupby("block").S5_spid.mean()
    bd["S5_spid"] = bd.block.map(bsp)
    bd["n_mapped_designs"] = bd.block.map(dd[dd.design_id.isin(list(mp_map))].groupby("block").size()).fillna(0).astype(int)
    mz = bd[np.isfinite(bd.S5_spid) & np.isfinite(bd.Z)]
    ms = bd[np.isfinite(bd.S5_spid) & bd.complete & np.isfinite(bd.S5)]
    rm = dict(n_mapped_designs=int(len(mp_map)), n_mapped_designs_policy_missing=len(missing_pol))
    if len(mz) < MIN_SPID_N:
        rm["spid_vs_block_Z"] = dict(status="too few", n=int(len(mz)))
    else:
        rm["spid_vs_block_Z"] = dict(n=int(len(mz)), spearman=fnum(stats.spearmanr(mz.S5_spid, mz.Z)[0]))
    if len(ms) < MIN_SPID_N:
        rm["spid_vs_design_block_S5"] = dict(status="too few", n=int(len(ms)))
    else:
        rm["spid_vs_design_block_S5"] = dict(n=int(len(ms)), spearman=fnum(stats.spearmanr(ms.S5_spid, ms.S5)[0]))

    # ---------- step 8: A0 descriptive ----------
    el = pol[(pol.complete_window == 1) & (pol.date_conflict == 0) & (pol.n_adopters >= 10)].copy()
    eligible_set = set(el.index)

    def summ(key):
        rows = []
        for k, cnt in el.groupby(key).size().items():
            if cnt >= MIN_TOPIC:
                x = el.loc[el[key] == k, "S5_spid"].to_numpy(float)
                p25, p75 = np.percentile(x, [25, 75])
                rows.append(dict(group=str(k), n_policies=int(cnt), median=float(np.median(x)),
                                 p25=float(p25), p75=float(p75), iqr=float(p75 - p25)))
        return rows

    a0 = dict(n_policies=int(len(el)), overall_median=float(el.S5_spid.median()),
              by_topic=summ("topic"), by_source=summ("source"),
              n_rows_mismatch_adopters=int((pol.n_rows != pol.n_adopters).sum()))
    pol_out = pol.reset_index()
    pol_out["eligible_A0"] = pol_out.policy.isin(eligible_set)
    pol_out.to_csv(os.path.join(OUT, "a0_spid_speed.csv"), index=False)

    # ---------- independent check of the observed rho (plain python loops) ----------
    rho_chk = rho_py(S5.tolist(), Z.tolist(), doms.tolist())
    rho_ref_chk = rho_py(S5[mref].tolist(), refZ[mref].tolist(), doms[mref].tolist())
    d1 = abs(rho - rho_chk)
    d2 = abs(rho_ref - rho_ref_chk)
    assert d1 < 1e-9 and d2 < 1e-9, (d1, d2)
    check = dict(rho_main=rho, rho_python_loops=rho_chk, abs_diff_rho=d1,
                 rho_ref_main=rho_ref, rho_ref_python_loops=rho_ref_chk, abs_diff_ref=d2,
                 tolerance=1e-9, pass_=True)

    # ---------- block / design tables ----------
    bd.drop(columns=["design_ids"]).to_csv(os.path.join(OUT, "blocks_q4.csv"), index=False)
    dd.to_csv(os.path.join(OUT, "designs_q4.csv"), index=False)

    out.update(status="computed", observed=observed, reference=reference,
               robustness=robustness, pattern=pattern, R_M_spid=rm, A0_spid_speed=a0,
               check=check)
    with open(res_path, "w") as fh:
        json.dump(out, fh, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps(dict(n_blocks_U=n, rho=rho, p=p_two, rho_ref=rho_ref, excess=excess,
                          flag=pattern["flag"]), indent=1))


if __name__ == "__main__":
    main()
