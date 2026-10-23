#!/usr/bin/env python3
"""Rank 7 co-adoption map (PREPLAN_T233_20261021 section 3).

Pair statistic: share_pq = share of states adopting both policies whose adoption
years differ by <= 3. Null: joint draws in which every policy's adoption years are
permuted among its own adopting states. Family (topic, source) statistics, a
leave-one-policy-out check, a policy bootstrap for topic families, and a
close-partner flag per policy.

Run:
  .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/q7_coadoption.py [--force]
  .venv_did/bin/python -I -B scripts/l3_meta/new_angles_b/q7_coadoption.py --caution
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = "."
SD = os.path.join(ROOT, "data/l3_meta/new_angles_20261021/states_decide")
IN_CSV = os.path.join(SD, "inputs/spid_adoption.csv")
OUT = os.path.join(SD, "q7")
NODUP = False
MAP_CSV = os.path.join(SD, "mapping/FINAL_design_spid_map.csv")

R = 1000
TOL = 3
MIN_ADOPT = 10
MIN_OVERLAP = 5
MIN_FAMILY = 3
B_BOOT = 1000
SEED_NULL = [20261022, 7, 1]
SEED_BOOT = [20261022, 7, 2]
SEED_CHECK = [20261022, 7, 3]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def match_counts(Z):
    """Integer count of states with |Zp - Zq| <= TOL for all policy pairs. NaN = not adopted."""
    with np.errstate(invalid="ignore"):
        d = np.abs(Z[:, None, :] - Z[None, :, :])
        return np.sum(d <= TOL, axis=2, dtype=np.int32)


def label_masks(labels, valid):
    cnt = pd.Series(labels).value_counts()
    names = sorted(cnt[cnt >= MIN_FAMILY].index.tolist())
    if not names:
        return names, np.zeros((0,) + valid.shape, dtype=bool)
    masks = np.stack([np.outer(labels == L, labels == L) & valid for L in names])
    return names, masks


def boot_family(members, excess, valid, rng):
    """Policy bootstrap inside one family. Pair weights = count_i * count_j; self-pairs excluded."""
    nf = len(members)
    E = excess[np.ix_(members, members)]
    V = valid[np.ix_(members, members)]
    E0 = np.where(V, E, 0.0)
    Vf = V.astype(np.float64)
    idx = rng.integers(0, nf, size=(B_BOOT, nf))
    M = np.zeros((B_BOOT, nf))
    np.add.at(M, (np.repeat(np.arange(B_BOOT), nf), idx.ravel()), 1.0)
    num = 0.5 * np.einsum("bi,ij,bj->b", M, E0, M)
    den = 0.5 * np.einsum("bi,ij,bj->b", M, Vf, M)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def to_py(o):
    if isinstance(o, dict):
        return {str(k): to_py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_py(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    return o


def main(force):
    t0 = time.perf_counter()
    results_path = os.path.join(OUT, "RESULTS_Q7.json")
    if os.path.exists(results_path) and not force:
        sys.exit(f"{results_path} exists; rerun with --force to overwrite")
    os.makedirs(OUT, exist_ok=True)

    sha = sha256(IN_CSV)
    raw = pd.read_csv(IN_CSV, dtype={"policy": str, "state": str, "majortopic_str": str,
                                     "source": str, "description": str})
    if raw.duplicated(["policy", "state"]).any():
        sys.exit("duplicate policy-state rows in input")
    n_csv_policies = int(raw.policy.nunique())

    el = raw[(raw.complete_window == 1) & (raw.date_conflict == 0)].copy()
    cnt_all = el.groupby("policy").size()
    elig = sorted(cnt_all[cnt_all >= MIN_ADOPT].index.tolist())
    d = el[el.policy.isin(elig)].copy()
    P = len(elig)
    states = sorted(d.state.unique().tolist())
    S = len(states)
    pidx = {p: i for i, p in enumerate(elig)}
    sidx = {s: i for i, s in enumerate(states)}
    Y = np.full((P, S), np.nan, dtype=np.float32)
    Y[d.policy.map(pidx).values, d.state.map(sidx).values] = d.adopt_year.values.astype(np.float32)

    first = d.groupby("policy").first().loc[elig]
    topic = first.majortopic_str.fillna("(missing)").values.astype(object)
    source = first.source.fillna("(missing)").values.astype(object)
    desc = first.description.fillna("").values.astype(object)
    n_adopt_col = d.groupby("policy").n_adopters.first().loc[elig].values
    n_adopt_mismatch = int((n_adopt_col != cnt_all.loc[elig].values).sum())
    print(f"eligible policies {P}, states {S}, n_adopters column mismatches {n_adopt_mismatch}")

    A = ~np.isnan(Y)
    Cobs_pre = match_counts(Y)
    Af = A.astype(np.int32)
    Nov = Af @ Af.T
    Nsafe = np.where(Nov > 0, Nov, 1).astype(np.float64)
    valid = (Nov >= MIN_OVERLAP) & ~np.eye(P, dtype=bool)
    if NODUP:  # added after the plan: likely duplicate codings of one law
        import re
        stop = {"the", "a", "an", "of", "to", "and", "or", "for", "in", "on", "by", "is", "does", "state", "have", "has", "law", "laws", "that", "with", "any"}
        toks = [set(w for w in re.findall(r"[a-z0-9]+", str(t).lower()) if w not in stop) for t in desc]
        dup = np.zeros((P, P), dtype=bool)
        for i in range(P):
            for j in range(i + 1, P):
                u = toks[i] | toks[j]
                jac = len(toks[i] & toks[j]) / len(u) if u else 0.0
                near = (source[i] != source[j]) and Nov[i, j] >= 10 and (Cobs_pre[i, j] / max(Nov[i, j], 1) >= 0.9)
                if jac >= 0.35 or near:
                    dup[i, j] = dup[j, i] = True
        print("likely duplicate pairs dropped:", int(np.triu(dup & valid, 1).sum()))
        valid = valid & ~dup
    n_pairs = int(np.triu(valid, 1).sum())
    print(f"pairs with overlap>=5: {n_pairs}")

    # Sorted adopter years per policy, padded with NaN: used for the within-policy permutation.
    Vpad = np.full((P, S), np.nan, dtype=np.float32)
    for p in range(P):
        v = np.sort(Y[p, A[p]])
        Vpad[p, : v.size] = v

    Cobs = match_counts(Y)
    share_obs = np.where(valid, Cobs / Nsafe, np.nan)

    topic_names, TM = label_masks(topic, valid)
    source_names, SM = label_masks(source, valid)
    names = topic_names + source_names
    kind = ["topic"] * len(topic_names) + ["source"] * len(source_names)
    MK = np.concatenate([TM, SM]) if names else np.zeros((0, P, P), dtype=bool)
    K = len(names)
    MKf = MK.reshape(K, -1).astype(np.float64)
    Nfull = MK.reshape(K, -1).sum(1).astype(np.float64)
    print(f"families: {len(topic_names)} topic, {len(source_names)} source")

    # Null: R joint draws.
    t_null = time.perf_counter()
    rng = np.random.default_rng(SEED_NULL)
    rowsS = np.broadcast_to(np.arange(S), (P, S))
    sumC = np.zeros((P, P), dtype=np.int64)
    Cge = np.zeros((P, P), dtype=np.int32)
    G = np.zeros((R, K))
    for r in range(R):
        keys = rng.random((P, S))
        keys[~A] = 2.0
        order = np.argsort(keys, axis=1)
        rank = np.empty_like(order)
        np.put_along_axis(rank, order, rowsS, axis=1)
        Z = np.take_along_axis(Vpad, rank, axis=1)
        if r == 0 and not np.array_equal(np.isnan(Z), ~A):
            sys.exit("permutation changed the adopting-state sets")
        Cd = match_counts(Z)
        sumC += Cd
        Cge += (Cd >= Cobs).astype(np.int32)
        if K:
            G[r] = MKf @ np.where(valid, Cd / Nsafe, 0.0).ravel()
    t_null = time.perf_counter() - t_null
    print(f"null draws done in {t_null:.1f}s")

    null_mean = np.where(valid, sumC / (R * Nsafe), np.nan)
    excess = share_obs - null_mean
    p_upper = np.where(valid, (Cge + 1) / (R + 1), np.nan)
    Ex0 = np.where(valid, excess, 0.0)
    NM0 = np.where(valid, null_mean, 0.0)

    Nf_safe = np.where(Nfull > 0, Nfull, np.nan)
    Fobs = (MKf @ Ex0.ravel()) / Nf_safe if K else np.zeros(0)
    H = MKf @ NM0.ravel() if K else np.zeros(0)
    Fnull = (G - H[None, :]) / Nf_safe[None, :] if K else np.zeros((R, 0))

    # Family table with leave-one-policy-out.
    fam = {}
    for k, (name, kd) in enumerate(zip(names, kind)):
        mem = np.where(topic == name)[0] if kd == "topic" else np.where(source == name)[0]
        Mk = MK[k]
        tot = float(Ex0[Mk].sum())
        rs = (Ex0 * Mk).sum(axis=1)
        rc = Mk.sum(axis=1)
        loo = {}
        for i in mem:
            dn = Nfull[k] - 2 * rc[i]
            if dn > 0:
                loo[int(i)] = (tot - 2 * rs[i]) / dn
        if loo:
            imin = min(loo, key=loo.get)
            loo_min, loo_pol = float(loo[imin]), elig[imin]
        else:
            loo_min, loo_pol = float("nan"), ""
        f_obs = float(Fobs[k])
        q = np.nanpercentile(Fnull[:, k], [5, 50, 95, 97.5]) if np.isfinite(f_obs) else [np.nan] * 4
        p_up = (np.sum(Fnull[:, k] >= f_obs) + 1) / (R + 1) if np.isfinite(f_obs) else np.nan
        pattern = bool(np.isfinite(f_obs) and np.isfinite(loo_min)
                       and f_obs > q[2] and loo_min > 0 and loo_min > q[2])
        fam[(kd, name)] = dict(
            kind=kd, label=name, n_policies=int(mem.size), n_pairs=int(Nfull[k] // 2),
            mean_excess_obs=f_obs, null_p05=float(q[0]), null_p50=float(q[1]),
            null_p95=float(q[2]), null_p975=float(q[3]), p_upper=float(p_up),
            loo_min_excess=loo_min, loo_min_policy=loo_pol, pattern=pattern,
            members=mem,
        )

    # Bootstrap for topic families (fixed seed, families in sorted order).
    rng_b = np.random.default_rng(SEED_BOOT)
    excess_nan = np.where(valid, excess, np.nan)
    for key, f in fam.items():
        if f["kind"] != "topic":
            continue
        b = boot_family(f["members"], excess_nan, valid, rng_b)
        f["boot_point_unweighted_check"] = float(np.nanmean(
            excess_nan[np.ix_(f["members"], f["members"])]))
        f["boot_lo95"] = float(np.nanpercentile(b, 2.5)) if np.isfinite(b).any() else np.nan
        f["boot_med"] = float(np.nanpercentile(b, 50)) if np.isfinite(b).any() else np.nan
        f["boot_hi95"] = float(np.nanpercentile(b, 97.5)) if np.isfinite(b).any() else np.nan

    # Topic x topic mean excess map.
    tk = [n for n, kd in zip(names, kind) if kd == "topic"]
    tmat = np.full((len(tk), len(tk)), np.nan)
    for a, La in enumerate(tk):
        ia = topic == La
        for b_, Lb in enumerate(tk):
            m = np.outer(ia, topic == Lb) & valid
            if m.any():
                tmat[a, b_] = excess[m].mean()

    # Pair table (upper triangle, overlap>=5).
    iu, ju = np.where(np.triu(valid, 1))
    pairs = pd.DataFrame({
        "policy_p": np.array(elig)[iu], "policy_q": np.array(elig)[ju],
        "topic_p": topic[iu], "topic_q": topic[ju],
        "source_p": source[iu], "source_q": source[ju],
        "overlap_n": Nov[iu, ju], "share_obs": share_obs[iu, ju],
        "null_mean": null_mean[iu, ju], "excess": excess[iu, ju],
        "p_upper": p_upper[iu, ju],
    })
    pairs.to_csv(os.path.join(OUT, "pairs.csv"), index=False)

    # Family CSVs.
    rows_t, rows_s = [], []
    for key, f in fam.items():
        row = {k: v for k, v in f.items() if k not in ("kind", "members")}
        (rows_t if f["kind"] == "topic" else rows_s).append(row)
    pd.DataFrame(rows_t).to_csv(os.path.join(OUT, "family_topic.csv"), index=False)
    pd.DataFrame(rows_s).drop(columns=[c for c in rows_s[0] if c.startswith("boot")] if rows_s else [],
                              errors="ignore").to_csv(os.path.join(OUT, "family_source.csv"), index=False)
    pd.DataFrame(tmat, index=tk, columns=tk).to_csv(os.path.join(OUT, "topic_matrix.csv"),
                                                   float_format="%.6g")

    # Top 25 pairs.
    tp = pairs[(pairs.p_upper <= 0.01) & (pairs.overlap_n >= 8)].sort_values("excess", ascending=False).head(25).copy()
    tp.insert(0, "rank", range(1, len(tp) + 1))
    tp["description_p"] = [desc[pidx[p]] for p in tp.policy_p]
    tp["description_q"] = [desc[pidx[q]] for q in tp.policy_q]
    tp[["rank", "policy_p", "policy_q", "description_p", "description_q", "topic_p", "topic_q",
        "overlap_n", "share_obs", "null_mean", "excess", "p_upper"]].to_csv(
        os.path.join(OUT, "top_pairs.csv"), index=False)

    # Close partners.
    qual = valid & (share_obs >= 0.5) & (p_upper <= 0.01)
    cp_rows = []
    for i in range(P):
        js = np.where(qual[i])[0]
        row = dict(policy=elig[i], topic=topic[i], source=source[i],
                   close_partner=int(js.size > 0), n_close_partners=int(js.size),
                   best_partner="", best_partner_excess=np.nan, best_partner_share=np.nan,
                   best_partner_p_upper=np.nan)
        if js.size:
            j = js[np.argmax(excess[i, js])]
            row.update(best_partner=elig[j], best_partner_excess=float(excess[i, j]),
                       best_partner_share=float(share_obs[i, j]), best_partner_p_upper=float(p_upper[i, j]))
        cp_rows.append(row)
    pd.DataFrame(cp_rows).to_csv(os.path.join(OUT, "close_partner.csv"), index=False, float_format="%.6g")

    # Self-checks.
    trivial_gap = float(np.max(np.abs((sumC[valid] / R) / Nsafe[valid] - null_mean[valid])))
    ex_u = excess[iu, ju]
    so_u = share_obs[iu, ju]
    nm_u = null_mean[iu, ju]
    pu = p_upper[iu, ju]
    frac05 = float(np.mean(pu <= 0.05))

    # Independent plain-Python recomputation for 20 random pairs, from the raw CSV rows.
    raw_by = {}
    with open(IN_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["complete_window"] == "1" and row["date_conflict"] == "0" and row["policy"] in pidx:
                raw_by.setdefault(row["policy"], {})[row["state"]] = int(row["adopt_year"])
    rng_c = np.random.default_rng(SEED_CHECK)
    pick = rng_c.choice(iu.size, size=20, replace=False)
    max_diff = 0.0
    for k in pick:
        p, q = elig[iu[k]], elig[ju[k]]
        sp, sq = raw_by[p], raw_by[q]
        common = [s for s in sp if s in sq]
        cnt_py = sum(1 for s in common if abs(sp[s] - sq[s]) <= TOL)
        share_py = cnt_py / len(common)
        assert len(common) == Nov[iu[k], ju[k]], (p, q)
        diff = abs(share_py - share_obs[iu[k], ju[k]])
        max_diff = max(max_diff, diff)
        assert diff < 1e-12, (p, q, share_py, share_obs[iu[k], ju[k]])

    runtime = time.perf_counter() - t0
    n_pattern_t = sum(1 for f in fam.values() if f["kind"] == "topic" and f["pattern"])
    n_pattern_s = sum(1 for f in fam.values() if f["kind"] == "source" and f["pattern"])

    def fam_json(kd):
        return {f["label"]: {k: v for k, v in f.items() if k not in ("kind", "label", "members")}
                for f in fam.values() if f["kind"] == kd}

    results = dict(
        input=IN_CSV, input_sha256=sha, n_csv_policies=n_csv_policies, n_eligible=P,
        n_states=S, n_pairs=n_pairs, R=R, tolerance_years=TOL,
        min_adopters=MIN_ADOPT, min_overlap=MIN_OVERLAP, min_family=MIN_FAMILY,
        seeds=dict(null=SEED_NULL, bootstrap=SEED_BOOT, check=SEED_CHECK),
        bootstrap_draws=B_BOOT, runtime_seconds=round(runtime, 1), null_seconds=round(t_null, 1),
        n_adopt_column_mismatch=n_adopt_mismatch,
        topic_families=fam_json("topic"), source_families=fam_json("source"),
        n_pattern_topic=n_pattern_t, n_pattern_source=n_pattern_s,
        pattern_topic_labels=[f["label"] for f in fam.values() if f["kind"] == "topic" and f["pattern"]],
        pattern_source_labels=[f["label"] for f in fam.values() if f["kind"] == "source" and f["pattern"]],
        share_pairs_p_upper_le_005=frac05, share_pairs_expected=0.05,
        overall_mean_excess=float(np.mean(ex_u)),
        overall_mean_share_obs=float(np.mean(so_u)),
        overall_mean_null_share=float(np.mean(nm_u)),
        n_close_partner_policies=int(sum(r["close_partner"] for r in cp_rows)),
        self_checks=dict(
            a_null_mean_recomputed_max_abs_gap=trivial_gap,
            a_note="trivial: null_mean is the mean of the same draws",
            b_frac_p_le_005=frac05,
            b_note="near 0.05 means no pair-level signal beyond chance",
            c_overall_share_obs_minus_null_mean=float(np.mean(so_u) - np.mean(nm_u)),
            d_python_loop_pairs_checked=20, d_python_loop_max_abs_diff=float(max_diff),
        ),
    )
    with open(results_path, "w") as f:
        json.dump(to_py(results), f, indent=2)
    print(f"pattern families: topic {n_pattern_t}, source {n_pattern_s}")
    print(f"overall mean excess {np.mean(ex_u):.5f}; share_obs {np.mean(so_u):.5f} vs null {np.mean(nm_u):.5f}")
    print(f"share of pairs p<=0.05: {frac05:.4f} (expected 0.05)")
    print(f"python check max abs diff {max_diff:.2e}")
    print(f"runtime {runtime:.1f}s")


def caution_line(map_csv=MAP_CSV):
    cp_path = os.path.join(OUT, "close_partner.csv")
    if not os.path.exists(cp_path):
        sys.exit("close_partner.csv missing; run the main analysis first")
    cp = pd.read_csv(cp_path, dtype={"policy": str})
    cpd = cp.set_index("policy")
    if not os.path.exists(map_csv):
        print(f"mapping file not found: {map_csv}; nothing written")
        return
    mp = pd.read_csv(map_csv, dtype=str)
    mapped = mp[mp.spid_policy.notna() & (mp.spid_policy.str.strip() != "")]
    n_mapped = int(mapped.design_id.nunique())
    k = 0
    for pol in mapped.spid_policy.str.strip():
        if pol in cpd.index and int(cpd.loc[pol, "close_partner"]) == 1:
            k += 1
    out = dict(n_designs_mapped=n_mapped, k_designs_mapped_policy_has_close_partner=k,
               line=f"at least {k} of {n_mapped} mapped designs have a close partner law (floor)")
    print(out["line"])
    with open(os.path.join(OUT, "caution.json"), "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--caution", action="store_true")
    ap.add_argument("--nodup", action="store_true", help="added after the plan: drop likely duplicate codings of one law; writes to q7/nodup/")
    a = ap.parse_args()
    if a.nodup:
        NODUP = True
        OUT = os.path.join(SD, "q7/nodup")
    if a.caution:
        caution_line(MAP_CSV)
    else:
        main(a.force)
