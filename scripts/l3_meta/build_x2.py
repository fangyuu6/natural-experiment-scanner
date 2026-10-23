#!/usr/bin/env python3
"""X2 (rule id EX-1, exploratory): welfare valence of the outcome matrix (halo, concordance, alignment of the leading factor).  PREREG_X2_V1.md.
Stages: adjud | freeze | pin | analyze.   Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_x2.py <stage>"""
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
import coding_tools as CT  # noqa: E402
import m1_core as M  # noqa: E402
import build_m1 as B1  # noqa: E402

D = ROOT / "data/l3_meta/x2_v1"
PREREG = ROOT / "docs/plans/l3-meta/PREREG_X2_V1.md"
REC = ROOT / "docs/plans/l3-meta/PREREG_X2_V1_HASH.txt"
X1 = ROOT / "data/l3_meta/x1_v1/RESULTS_X1_V1.json"
M2 = ROOT / "data/l3_meta/m2_v1/RESULTS_M2_V1.json"
LIVE = M.REFRESH / "live/l2_set_live.json"
CAL = M.REFRESH / "cal/RESULTS_CAL_V1.json"
SEEDS = dict(null=20261028, perm=20261029, boot=20261030, align=20261031)
CODE = ["scripts/l3_meta/build_x2.py", "scripts/l3_meta/coding_tools.py", "scripts/l3_meta/m1_core.py", "scripts/l3_meta/build_m1.py", "scripts/l3_meta/test_x2.py"]
CODING_FILES = ["data/l3_meta/x2_v1/coding/coder1/labels.csv", "data/l3_meta/x2_v1/coding/coder2/labels.csv", "data/l3_meta/x2_v1/coding/adjudication_packets.csv",
                "data/l3_meta/x2_v1/valence_table_v1.csv", "data/l3_meta/x2_v1/CODING_REPORT_V1.json"]
MIN_CODED_CELLS = 10


def halo_stats(Z, v):
    """Z: (reps, nd, K).  Returns H (reps,), W_d (reps, nd), concordance share (reps,), n concordant cells counted (reps,)."""
    coded = v != 0
    w = np.where(coded[None, None, :], v[None, None, :] * Z, np.nan)
    fin = np.isfinite(w)
    cnt = fin.sum(axis=2)
    with np.errstate(all="ignore"):
        Wd = np.where(cnt >= MIN_CODED_CELLS, np.nanmean(w, axis=2), np.nan)
        H = np.nanmean(Wd, axis=1)
    big = fin & (np.abs(Z) > 1)
    pos = big & (w > 0)
    with np.errstate(all="ignore"):
        conc = pos.sum(axis=(1, 2)) / big.sum(axis=(1, 2))
    return H, Wd, conc, big.sum(axis=(1, 2))


def cosine(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else np.nan


def perm_within(v, fam, rng):
    out = v.copy()
    for f in sorted(set(fam)):
        ii = [i for i, x in enumerate(fam) if x == f]
        out[ii] = v[rng.permutation(ii)]
    return out


def verify(require_pin=True):
    problems, n = B1.verify_records_for(REC)
    if require_pin and n < 2:
        problems.append("the pinning supplement S1 is missing")
    if problems:
        sys.exit("hash record check failed, not run: " + "; ".join(problems[:5]))
    return n


def stage_pin():
    rep = json.loads((D / "CODING_REPORT_V1.json").read_text())
    if not rep["anchors"]["passed"]:
        sys.exit("anchor check failed: X2 is not run")
    t = CT.write_supplement(REC, 1, "coding outputs and analysis code pinned before any z is read for X2 (section 4 of the pre-registration)", "docs/plans/l3-meta/PREREG_X2_V1.md", PREREG,
                            CODING_FILES + CODE, "no X2 statistic existed at this supplement; code tested on synthetic data only (test_x2.py)", ROOT)
    print("wrote", t.name)


def stage_analyze():
    n_rec = verify()
    out_path = D / "RESULTS_X2_V1.json"
    if out_path.exists():
        sys.exit("results exist")
    t0 = time.time()
    table = CT.read_table(D / "valence_table_v1.csv")
    universe = sorted(table)
    v = np.array([table[s] for s in universe], float)
    data, real = B1.load_real("U")
    fam_map = {}
    for d in data.values():
        fam_map.update(dict(zip(d["series"], d["family"])))
    fam = [fam_map[s] for s in universe]
    rows = M.design_arrays(data, universe)
    ix = {s: i for i, s in enumerate(universe)}
    n_rep = 9999
    rng = np.random.default_rng(SEEDS["null"])
    Zn, Rd, ds = M.replicate_z(rows, list(range(len(universe))), n_rep, rng)
    Zr = B1.real_z(data, real, ds, universe)
    kappa = json.loads(CAL.read_text())["question"]["CAL"]["kappa"]["kappa_hat"]
    live = {x["design_id"]: x["policy_family"] for x in json.loads(LIVE.read_text())["designs"]}
    res = OrderedDict()
    res["valence_counts"] = OrderedDict((k, int((v == k).sum())) for k in (1, -1, 0))

    def halo_block(Z1, Znull, label):
        Ho, Wo, co, no = halo_stats(Z1, v)
        Hn, _, cn, _ = halo_stats(Znull, v)
        prng = np.random.default_rng(SEEDS["perm"])
        perm_H = np.array([halo_stats(Z1, perm_within(v, fam, prng))[0][0] for _ in range(1000)])
        fams = sorted({live[d] for d in ds})
        mem = [np.array([i for i, d in enumerate(ds) if live[d] == f]) for f in fams]
        brng = np.random.default_rng(SEEDS["boot"])
        bH = []
        for _ in range(9999):
            pick = np.concatenate([mem[i] for i in brng.integers(0, len(fams), len(fams))])
            bH.append(np.nanmean(Wo[0, pick]))
        o, nl = float(Ho[0]), Hn
        out = OrderedDict(H_observed=o, n_designs_with_coded_cells=int(np.isfinite(Wo[0]).sum()), null_mean=float(np.nanmean(nl)), null_sd=float(np.nanstd(nl, ddof=1)),
                          null_q025_q975=[float(np.nanpercentile(nl, 2.5)), float(np.nanpercentile(nl, 97.5))],
                          rank_p_two_sided=float((1 + (np.abs(nl - np.nanmean(nl)) >= abs(o - np.nanmean(nl))).sum()) / (1 + np.isfinite(nl).sum())),
                          valence_permutation_share_ge=float((1 + (np.abs(perm_H) >= abs(o)).sum()) / (1 + len(perm_H))), valence_permutation_mean=float(np.mean(perm_H)),
                          family_block_ci95=[float(np.nanpercentile(bH, 2.5)), float(np.nanpercentile(bH, 97.5))],
                          concordance_share=float(co[0]), concordance_cells=int(no[0]), concordance_null_mean=float(np.nanmean(cn)),
                          concordance_null_q025_q975=[float(np.nanpercentile(cn, 2.5)), float(np.nanpercentile(cn, 97.5))])
        return out
    res["halo_raw"] = halo_block(Zr, Zn, "raw")
    res["halo_kappa_scaled"] = halo_block(Zr / kappa, Zn, "kappa")
    # halo by family
    byfam = OrderedDict()
    for f in sorted(set(fam)):
        ii = [i for i, x in enumerate(fam) if x == f and v[i] != 0]
        if len(ii) < 2:
            continue
        w = v[ii][None, None, :] * Zr[:, :, ii]
        wn = v[ii][None, None, :] * Zn[:, :, ii]
        o = float(np.nanmean(w))
        nl = np.nanmean(wn, axis=(1, 2))
        byfam[f] = OrderedDict(n_series=len(ii), mean_welfare_signed_z=o, null_sd=float(np.nanstd(nl, ddof=1)), z_vs_null=float((o - np.nanmean(nl)) / np.nanstd(nl, ddof=1)))
    res["halo_by_family"] = byfam
    # alignment
    core = json.loads(X1.read_text())["results"]["U"]["core"]
    ci = [ix[s] for s in core]
    Rc = Rd[:, ci][:, :, ci]
    Co, cnt = M.c_matrix(Zr[:, :, ci] / kappa, Rc)
    w_, V_ = np.linalg.eigh((Co[0] + Co[0].T) / 2)
    u1 = V_[:, -1]
    vc = v[ci]
    coded = vc != 0
    cos_v = cosine(u1[coded], vc[coded])
    cos_all = cosine(u1, vc)
    nullcos = []
    for a in range(0, n_rep, 250):
        Cb, _ = M.c_matrix(Zn[a:a + 250][:, :, ci], Rc)
        for C in Cb:
            if np.isfinite(C).all():
                ww, VV = np.linalg.eigh((C + C.T) / 2)
                nullcos.append(abs(cosine(VV[:, -1][coded], vc[coded])))
    prng = np.random.default_rng(SEEDS["align"])
    famc = [fam[i] for i in ci]
    permcos = []
    for _ in range(1000):
        vp = perm_within(vc, famc, prng)
        cd = vp != 0
        permcos.append(abs(cosine(u1[cd], vp[cd])))
    m2 = json.loads(M2.read_text())["results"]["raw"]["series_slopes_all"]
    have = [k for k, s in enumerate(core) if m2.get(s) is not None]
    b = np.array([m2[core[k]] for k in have], float)
    cos_b = float(np.corrcoef(u1[have], b)[0, 1]) if len(have) > 5 else None
    bperm = []
    if cos_b is not None:
        famh = [famc[k] for k in have]
        for _ in range(1000):
            bp = perm_within(b, famh, prng)
            bperm.append(abs(float(np.corrcoef(u1[have], bp)[0, 1])))
    res["alignment"] = OrderedDict(
        lambda1_kappa_scaled=float(w_[-1]), n_core=len(core), n_core_coded=int(coded.sum()), abs_cos_v_coded=abs(cos_v), cos_v_coded=cos_v, cos_all_series=cos_all,
        null_abs_cos_q95_replicate_eigenvector=float(np.percentile(nullcos, 95)), null_abs_cos_median=float(np.median(nullcos)), n_null=len(nullcos),
        valence_permutation_q95=float(np.percentile(permcos, 95)), valence_permutation_share_ge=float((1 + (np.array(permcos) >= abs(cos_v)).sum()) / (1 + len(permcos))),
        n_core_with_M2_slope=len(have), corr_u1_with_M2_slope=cos_b, M2_permutation_q95=(float(np.percentile(bperm, 95)) if bperm else None),
        M2_permutation_share_ge=(float((1 + (np.array(bperm) >= abs(cos_b)).sum()) / (1 + len(bperm))) if bperm else None),
        loadings=OrderedDict((s, OrderedDict(u1=float(u1[i]), valence=int(vc[i]))) for i, s in enumerate(core)))
    stamp = OrderedDict(rule_id="EX-1", question="X2", version="X2-V1", run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), n_rep=n_rep, seeds=SEEDS, kappa=kappa,
                        prereg_sha256=B1.sha256_file(PREREG), hash_records=n_rec, n_designs=len(ds), n_series=len(universe), seconds=round(time.time() - t0, 1),
                        code={f: CT.sha256_file(ROOT / f) for f in CODE if (ROOT / f).exists()})
    out_path.write_text(json.dumps(B1.clean(OrderedDict(stamp=stamp, results=res)), indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(B1.clean({k: res[k] for k in ("valence_counts", "halo_raw", "halo_kappa_scaled")}), default=float)[:2500])
    a = res["alignment"]
    print({k: a[k] for k in a if k != "loadings"})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["adjud", "freeze", "pin", "analyze"])
    a = ap.parse_args(argv)
    if a.stage == "adjud":
        print(CT.write_adjudication(D, "series_id", ["series_id", "family", "units"]))
    elif a.stage == "freeze":
        print(json.dumps(CT.freeze(D, "series_id", "series_id", 9, "valence_table_v1.csv")))
    elif a.stage == "pin":
        stage_pin()
    else:
        return stage_analyze()
    return 0


if __name__ == "__main__":
    sys.exit(main())
