#!/usr/bin/env python3
"""M6 (rule id EX-1, exploratory): when a policy moves an aggregate, do its parts offset?  Aggregates against components.

Rules: docs/plans/l3-meta/PREREG_M6_V1.md (+ amendment and hash records).  Stages:  check | run
  .venv_did/bin/python -I -B scripts/l3_meta/build_m6.py check     (adding-up check only; writes ADDUP_M6_V1.json)
  .venv_did/bin/python -I -B scripts/l3_meta/build_m6.py run       (offset statistics; reads ADDUP_M6_V1.json; writes RESULTS_M6_V1.json)
Set VECLIB_MAXIMUM_THREADS=OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=1.
"""
from __future__ import annotations

import argparse
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

D = ROOT / "data/l3_meta/m6_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_M6_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_M6_V1_HASH.txt"
MIN_RECORDS = 3                      # base, S1 (amendment), S2 (code pin)
AGG = OrderedDict([
    ("property", ("fbi_ucr_property_crime_rate", ("fbi_ucr_burglary_rate", "fbi_ucr_larceny_rate", "fbi_ucr_motor_vehicle_theft_rate"))),
    ("violent", ("fbi_ucr_violent_crime_rate", ("fbi_ucr_homicide_rate", "fbi_ucr_rape_legacy_rate", "fbi_ucr_robbery_rate", "fbi_ucr_aggravated_assault_rate"))),
])
TOL = 1e-8
MIN_DRAWS = 500
MIN_DESIGNS = 10
R_REP, B_BOOT, N_PSEUDO = 9999, 9999, 1000
SEEDS = dict(null=20261050, boot=20261051, pseudo=20261055)
PSEUDO_MIN_DESIGNS, PSEUDO_MIN_COMMON = 17, 10
INFERENTIAL_MDE = 0.5
CODE = ["scripts/l3_meta/build_m6.py", "scripts/l3_meta/test_m6.py", "scripts/l3_meta/late_core.py", "scripts/l3_meta/build_r0.py", "scripts/l3_meta/build_m1.py",
        "scripts/l3_meta/m1_core.py", "scripts/l3_meta/coding_tools.py"]


# ---------------------------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------------------------
def get_unit(arr, agg, parts):
    """(unit dict, None) or (None, reason).  Row 0 is the aggregate, rows 1.. the parts."""
    ids = [str(s) for s in arr["series_ids"]]
    pos = {s: i for i, s in enumerate(ids)}
    need = [agg, *parts]
    miss = [s for s in need if s not in pos]
    if miss:
        return None, "series missing: " + ",".join(miss)
    ii = [pos[s] for s in need]
    bad = [need[k] for k, i in enumerate(ii) if str(arr["code"][i]) != "SUPPORTED"]
    if bad:
        return None, "not supported: " + ",".join(bad)
    att = np.asarray(arr["att"], float)[ii]
    sd = np.asarray(arr["sd"], float)[ii]
    if not (np.isfinite(att).all() and np.isfinite(sd).all() and (sd > 0).all()):
        return None, "att or sd not finite"
    P = np.asarray(arr["placebo"], float)[ii]
    return dict(ids=need, att=att, sd=sd, P=P, finite=np.isfinite(P).all(axis=0)), None


def addup_gap(unit):
    att, P, fin = unit["att"], unit["P"], unit["finite"]
    obs = float(abs(att[0] - att[1:].sum()) / max(1.0, abs(att[0])))
    Pf = P[:, fin]
    if Pf.shape[1]:
        gap = np.abs(Pf[0] - Pf[1:].sum(axis=0)) / np.maximum(1.0, np.abs(Pf[0]))
        mx = float(gap.max())
    else:
        mx = float("nan")
    return dict(obs_rel_gap=obs, max_draw_rel_gap=mx, n_finite_draws=int(fin.sum()))


def addup_pass(g, tol=TOL, min_draws=MIN_DRAWS):
    return bool(g["obs_rel_gap"] <= tol and g["max_draw_rel_gap"] <= tol and g["n_finite_draws"] >= min_draws)


def stats_batch(XA, XP):
    """XA (R, nd), XP (R, nd, na) -> offset index O, median offset ratio, share of (design, part) pairs with a sign opposite to the aggregate."""
    vA = XA.var(axis=1, ddof=1)
    vP = XP.var(axis=1, ddof=1).sum(axis=-1)
    with np.errstate(all="ignore"):
        O = 1.0 - vA / vP
        den = np.abs(XA)
        ok = den > 1e-12
        ratio = np.where(ok, np.abs(XP).sum(-1) / np.where(ok, den, 1.0), np.nan)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            med = np.nanmedian(ratio, axis=1)
    sA = np.sign(XA)[:, :, None]
    sP = np.sign(XP)
    valid = (sA != 0) & (sP != 0)
    opp = ((sA != sP) & valid).sum(axis=(1, 2)) / np.maximum(valid.sum(axis=(1, 2)), 1)
    return O, med, opp


def observed_arrays(units):
    """(XA (1, nd), XP (1, nd, na)) of the observed ATT of the units of a list."""
    XA = np.array([[u["att"][0] for u in units]])
    XP = np.array([[u["att"][1:] for u in units]])
    return XA, XP


def null_replicates(units, R, rng):
    """R replicates: each design uses one placebo draw (shared by the aggregate and its parts, finite for all of them), independent across designs."""
    nd, na = len(units), len(units[0]["ids"]) - 1
    XA = np.empty((R, nd))
    XP = np.empty((R, nd, na))
    for j, u in enumerate(units):
        el = np.where(u["finite"])[0]
        pick = el[rng.integers(0, len(el), R)]
        XA[:, j] = u["P"][0, pick]
        XP[:, j, :] = u["P"][1:, pick].T
    return stats_batch(XA, XP)


def bootstrap(units, block_of, B, rng):
    XA, XP = observed_arrays(units)
    XA, XP = XA[0], XP[0]
    nb = int(max(block_of)) + 1
    block_of = np.asarray(block_of)
    out = np.full((B, 3), np.nan)
    for b in range(B):
        cnt = np.bincount(rng.integers(0, nb, size=nb), minlength=nb)
        m = cnt[block_of]
        idx = np.repeat(np.arange(len(units)), m)
        if len(idx) < 3:
            continue
        O, med, opp = stats_batch(XA[idx][None], XP[idx][None])
        out[b] = (O[0], med[0], opp[0])
    return out


def reading(obs, null_mean, q025, q975, boot_lo, boot_hi, mde):
    if obs > q975 and boot_lo > null_mean:
        r = "offsets beyond random"
    elif obs < q025 and boot_hi < null_mean:
        r = "parts co-move"
    else:
        r = "no excess offset"
    return r, ("inferential" if mde <= INFERENTIAL_MDE else "descriptive")


def offset_z(Zsel):
    """O in the z scale for a (nd, m) matrix of z (complete rows): 1 - Var_i(sum_a z) / sum_a Var_i(z_a)."""
    v = Zsel.var(axis=0, ddof=1).sum()
    return float(1.0 - Zsel.sum(axis=1).var(ddof=1) / v) if v > 0 else float("nan")


def pseudo_offsets(Z, fam, m, n_sets, rng, min_common=PSEUDO_MIN_COMMON, max_tries=200):
    """Offset index (z scale) of n_sets random sets of m series from m different families.  Z (nd, S) with NaN where unsupported."""
    fams = sorted(set(fam))
    by = {f: [k for k, x in enumerate(fam) if x == f] for f in fams}
    out, tries = [], 0
    if len(fams) < m:
        return np.array(out), tries
    while len(out) < n_sets and tries < n_sets * max_tries:
        tries += 1
        fs = rng.choice(len(fams), size=m, replace=False)
        cols = [by[fams[f]][rng.integers(0, len(by[fams[f]]))] for f in fs]
        rows = np.isfinite(Z[:, cols]).all(axis=1)
        if rows.sum() < min_common:
            continue
        out.append(offset_z(Z[rows][:, cols]))
    return np.array(out), tries


# ---------------------------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------------------------
def load_refs():
    return {ref: L.load_union(ref) for ref in ("P5", "U")}


def stage_check():
    n = L.require_records(REC, MIN_RECORDS)
    out_path = D / "ADDUP_M6_V1.json"
    if out_path.exists():
        sys.exit("adding-up report exists")
    U = load_refs()
    rep = OrderedDict()
    for ref, uni in U.items():
        for name, (agg, parts) in AGG.items():
            rows = OrderedDict()
            for did, u in uni.items():
                unit, why = get_unit(u["arr"], agg, parts)
                if unit is None:
                    rows[did] = dict(status="NOT_UNIT", reason=why)
                    continue
                g = addup_gap(unit)
                rows[did] = dict(status=("PASS" if addup_pass(g) else "FAIL"), **g)
            rep[f"{ref}:{name}"] = dict(n_pass=sum(r["status"] == "PASS" for r in rows.values()), n_fail=sum(r["status"] == "FAIL" for r in rows.values()),
                                       n_not_unit=sum(r["status"] == "NOT_UNIT" for r in rows.values()), designs=rows)
    D.mkdir(parents=True, exist_ok=True)
    stamp = dict(rule_id="EX-1", question="M6", stage="check", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), hash_records=n, prereg_sha256=L.sha256_file(PREREG),
                 tolerance=TOL, min_draws=MIN_DRAWS)
    out_path.write_text(json.dumps(L.clean(OrderedDict(stamp=stamp, units=rep)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print({k: (v["n_pass"], v["n_fail"], v["n_not_unit"]) for k, v in rep.items()})
    return 0


def z_matrix(uni, exclude):
    """(designs, series, Z (nd, S), family list) of the z = att/sd of SUPPORTED cells with series supported in at least PSEUDO_MIN_DESIGNS designs."""
    designs = list(uni)
    cells, fam = {}, {}
    for j, did in enumerate(designs):
        a = uni[did]["arr"]
        for k, s in enumerate(a["series_ids"]):
            s = str(s)
            if str(a["code"][k]) == "SUPPORTED" and np.isfinite(a["att"][k]) and np.isfinite(a["sd"][k]) and a["sd"][k] > 0:
                cells.setdefault(s, {})[j] = float(a["att"][k] / a["sd"][k])
                fam[s] = str(a["family"][k])
    series = sorted(s for s, c in cells.items() if len(c) >= PSEUDO_MIN_DESIGNS and s not in exclude)
    Z = np.full((len(designs), len(series)), np.nan)
    for k, s in enumerate(series):
        for j, v in cells[s].items():
            Z[j, k] = v
    Zall = {s: cells[s] for s in cells}
    return designs, series, Z, [fam[s] for s in series], Zall


def stage_run():
    n = L.require_records(REC, MIN_RECORDS)
    out_path = D / "RESULTS_M6_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    addup_path = D / "ADDUP_M6_V1.json"
    if not addup_path.exists():
        sys.exit("run the check stage first")
    addup = json.loads(addup_path.read_text())["units"]
    t0 = time.time()
    U = load_refs()
    live = L.live_families()
    exclude = {agg for agg, _ in AGG.values()} | {p for _, parts in AGG.values() for p in parts}
    res = OrderedDict()
    for ref, uni in U.items():
        designs_all, series, Zm, famm, Zall = z_matrix(uni, exclude)
        for name, (agg, parts) in AGG.items():
            key = f"{ref}:{name}"
            rows = addup[key]["designs"]
            units, ids, dropped = [], [], OrderedDict()
            for did, u in uni.items():
                if rows[did]["status"] == "PASS":
                    unit, _ = get_unit(u["arr"], agg, parts)
                    units.append(unit)
                    ids.append(did)
                else:
                    dropped[did] = rows[did]["status"]
            entry = OrderedDict(reference=ref, aggregate=name, n_designs=len(ids), designs=ids, dropped=dropped)
            if len(ids) < MIN_DESIGNS:
                entry["verdict"] = "NOT_EVALUABLE"
                entry["reason"] = f"{len(ids)} designs pass the adding-up check, {MIN_DESIGNS} required"
                res[key] = entry
                continue
            fams = sorted({live[d] for d in ids})
            block_of = [fams.index(live[d]) for d in ids]
            entry["n_policy_families"] = len(fams)
            XA, XP = observed_arrays(units)
            O, med, opp = [float(x[0]) for x in stats_batch(XA, XP)]
            rng = np.random.default_rng(SEEDS["null"])
            nO, nmed, nopp = null_replicates(units, R_REP, rng)
            rng = np.random.default_rng(SEEDS["boot"])
            bt = bootstrap(units, block_of, B_BOOT, rng)
            sdO = float(np.nanstd(nO, ddof=1))
            mde = L.MDE_FACTOR * sdO
            q = L.pct(nO)
            bo = L.pct(bt[:, 0])
            rd, infer = reading(O, float(np.nanmean(nO)), q[0], q[1], bo[0], bo[1], mde)
            entry.update(
                O=dict(obs=O, null_mean=float(np.nanmean(nO)), null_sd=sdO, null_q=q, excess=O - float(np.nanmean(nO)), mde=mde, boot_ci=bo, boot_n=int(np.isfinite(bt[:, 0]).sum())),
                ratio_median=dict(obs=med, null_mean=float(np.nanmean(nmed)), null_sd=float(np.nanstd(nmed, ddof=1)), null_q=L.pct(nmed), boot_ci=L.pct(bt[:, 1])),
                share_opposite=dict(obs=opp, null_mean=float(np.nanmean(nopp)), null_sd=float(np.nanstd(nopp, ddof=1)), null_q=L.pct(nopp), boot_ci=L.pct(bt[:, 2])),
                reading=rd, strength=infer,
                per_design=OrderedDict((d, dict(att_aggregate=float(u["att"][0]), att_parts=[float(x) for x in u["att"][1:]],
                                                ratio=(float(np.abs(u["att"][1:]).sum() / abs(u["att"][0])) if abs(u["att"][0]) > 1e-12 else None))) for d, u in zip(ids, units)),
            )
            # pseudo-aggregates (z scale)
            cols = [s for s in parts]
            Zr = np.full((len(designs_all), len(cols)), np.nan)
            for k, s in enumerate(cols):
                for j, v in Zall.get(s, {}).items():
                    Zr[j, k] = v
            keep = np.array([d in set(ids) for d in designs_all])
            Zr = Zr[keep]
            real_oz = offset_z(Zr[np.isfinite(Zr).all(axis=1)])
            ps, tries = pseudo_offsets(Zm, famm, len(parts), N_PSEUDO, np.random.default_rng(SEEDS["pseudo"]))
            entry["pseudo_z"] = OrderedDict(real=real_oz, n_sets=0, tries=int(tries), note="fewer than %d families in the universe" % len(parts)) if len(ps) == 0 else \
                OrderedDict(real=real_oz, n_sets=int(len(ps)), tries=int(tries), mean=float(ps.mean()), sd=float(ps.std(ddof=1)), q=L.pct(ps),
                            share_pseudo_ge_real=float(np.mean(ps >= real_oz)), share_pseudo_le_real=float(np.mean(ps <= real_oz)), universe_series=len(series))
            res[key] = entry
            print(key, entry["n_designs"], "designs", f"O={O:.3f}", rd, round(time.time() - t0), "s", flush=True)
    stamp = OrderedDict(rule_id="EX-1", question="M6", version="M6-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), seeds=SEEDS, hash_records=n,
                        prereg_sha256=L.sha256_file(PREREG), addup_sha256=L.sha256_file(addup_path), seconds=round(time.time() - t0, 1),
                        code={f: L.sha256_file(ROOT / f) for f in CODE if (ROOT / f).exists()})
    out_path.write_text(json.dumps(L.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["check", "run"])
    a = ap.parse_args(argv)
    return stage_check() if a.stage == "check" else stage_run()


if __name__ == "__main__":
    sys.exit(main())
