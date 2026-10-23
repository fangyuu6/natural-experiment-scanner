#!/usr/bin/env python3
"""Independent recomputation of the CAL-V1 results in plain Python (imports nothing from build_cal.py or any other builder or checker).

Written from the amendment PREREG_AMEND_20261015_CAL_V1.md (sections 1 to 6) and the D1-V1 / D2-V1 definitions it refers to.  Reads the four result JSONs of Q1-V3, D1-V1,
D2-V1 and D3-V2 (inputs of the amendment), SWEEP_RUN.json and the sweep files <design>.npz, and the results file written by build_cal.py.  Re-derives with explicit loops:
the D1 family statistics (M_if, q_if, r_if), the placebo family counts N*_ib, mu_i, tau_i, p_i, the negative-control series (z, own line), kappa-hat and kappa_strict, Q1 and D1 at every
kappa of the profile (amendment 5.1), the splits, the fit of the rule (5.4), the group SDs and leave-one-group-out kappa (5.5), the rate rule (5.6), the D3 counts (5.7) and both
family-block bootstraps (4.1, 4.2: the same integer matrices of numpy's generator, everything else in loops).  Prints the largest differences and exits 1 on any disagreement
(1e-9 relative: |x - y| <= 1e-9 max(1, |x|, |y|)).  Writes nothing unless --out is given (a new file).

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_cal_independent.py [--results FILE] [--sweep-dir DIR] [--b N] [--out FILE]   (numpy only loads npz files and draws the random numbers)
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / "data/l3_meta/sweep_v1"
EX1 = ROOT / "data/l3_meta/ex1_v3/RESULTS_EX1_V3.json"
D1J = ROOT / "data/l3_meta/d1_v1/RESULTS_D1_V1.json"
D2J = ROOT / "data/l3_meta/d2_v1/RESULTS_D2_V1.json"
D3J = ROOT / "data/l3_meta/d3_v2/RESULTS_D3_V2.json"
DEFAULT_RESULTS = ROOT / "data/l3_meta/cal_v1/RESULTS_CAL_V1.json"
SEED_Q, SEED_D, B_BOOT, NOMINAL, FIT_TOL = [20261011, 34, 1], [20261011, 34, 2], 9999, 0.05, 0.03

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
    if isinstance(want, (list, tuple)):
        if len(got) != len(want):
            problems.append(f"{label}: length {len(got)} vs {len(want)}")
            return
        for i, (g, w) in enumerate(zip(got, want)):
            compare(f"{label}[{i}]", g, w)
        return
    d = abs(got - want)
    key = label.split(":")[-1].split(".")[-1]
    worst[key] = max(worst.get(key, 0.0), d / max(1.0, abs(got), abs(want)))
    if not agree(got, want):
        problems.append(f"{label}: {got!r} vs {want!r} (difference {d:.3g})")


def mean_(v):
    return sum(v) / len(v)


def sd_(v):
    m = mean_(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


# ---------------------------------------------------------------------------------------------
# D1-V1 statistics in plain loops (sweep amendment section 6), extended with the items the calibration needs
# ---------------------------------------------------------------------------------------------
def plain_design(sids, fam, code, att, placebo):
    n_all = len(placebo[0])
    A = [i for i in range(len(sids)) if code[i] == "SUPPORTED"]
    for i in list(A):
        vals = [v for v in placebo[i] if not math.isnan(v)]
        if len(vals) < 2 or not (sd_(vals) > 0):
            A.remove(i)

    def common(idx):
        return [b for b in range(n_all) if all(not math.isnan(placebo[i][b]) for i in idx)]

    C = common(A)
    while A and len(C) < 900 and len({fam[i] for i in A}) >= 5:
        count = {i: sum(1 for v in placebo[i] if not math.isnan(v)) for i in A}
        fewest = min(count.values())
        drop = max((i for i in A if count[i] == fewest), key=lambda i: sids[i])
        A.remove(drop)
        C = common(A)
    families = sorted({fam[i] for i in A})
    if len(families) < 5 or len(C) < 900:
        return None
    n = len(C)
    sd = {i: sd_([placebo[i][b] for b in C]) for i in A}
    if any(not (v > 0) for v in sd.values()):
        return None
    rank = math.ceil(0.95 * n) - 1
    nstar = [0] * n
    fam_out = {}
    for f in families:
        mem = [i for i in A if fam[i] == f]
        M = max(abs(att[i] / sd[i]) for i in mem)
        Ms = [max(abs(placebo[i][b] / sd[i]) for i in mem) for b in C]
        q = sorted(Ms)[rank]
        for j in range(n):
            nstar[j] += int(Ms[j] > q)
        fam_out[f] = dict(M=M, q=q, r=sum(1 for v in Ms if v > q) / n)
    mu = sum(nstar) / n
    tau = math.sqrt(sum((v - mu) ** 2 for v in nstar) / (n - 1))
    if not tau > 0:
        return None
    series = []
    for i in A:
        row = [placebo[i][b] for b in C]
        cut = sorted(abs(v) for v in row)[rank]
        series.append(dict(sid=sids[i], fam=fam[i], z=att[i] / sd[i], abs_att=abs(att[i]), own_cut=cut, exp_own=sum(1 for v in row if abs(v) > cut) / n))
    return dict(n=n, families=fam_out, series=series, nstar=nstar, mu=mu, tau=tau)


def percentile_pair(values):
    a = [v for v in values if v == v]
    if not a:
        return None
    q = np.percentile(np.array(a), [2.5, 97.5])
    return [float(q[0]), float(q[1])]


def interval_check(label, got, values):
    """got: {'interval95', 'replicates', 'defined', 'median'} of the results file."""
    compare(f"{label}:replicates", got["replicates"], len(values))
    compare(f"{label}:defined", got["defined"], sum(1 for v in values if v == v))
    pp = percentile_pair(values)
    compare(f"{label}:lo", got["interval95"][0], pp[0])
    compare(f"{label}:hi", got["interval95"][1], pp[1])


# ---------------------------------------------------------------------------------------------
def d1_at(designs, ids, kappa):
    """Plain-loop D1 numbers at kappa (amendment 3.3)."""
    n = len(ids)
    Ns, Zs, Es, ps = [], [], [], []
    for did in ids:
        d = designs[did]
        N = sum(1 for f, st in d["families"].items() if st["M"] > kappa * st["q"])
        Ns.append(N)
        Es.append(N - d["mu"])
        Zs.append((N - d["mu"]) / d["tau"])
        ps.append((1 + sum(1 for v in d["nstar"] if v >= N)) / (len(d["nstar"]) + 1))
    p_ge = {k: sum(sum(1 for v in designs[did]["nstar"] if v >= k) / len(designs[did]["nstar"]) for did in ids) for k in (1, 2, 3)}
    excess = sorted(((Es[i], ids[i]) for i in range(n)))[::-1]
    total = sum(e for e, _ in excess)
    top5 = sum(e for e, _ in excess[:5])
    return dict(N=Ns, Z=Zs, E=Es, p=ps, theta=mean_(Zs), e_D=mean_(Es), excess_total=total, T_D=sum(Ns), sum_mu=sum(designs[d]["mu"] for d in ids),
                N_ge={k: sum(1 for v in Ns if v >= k) for k in (1, 2, 3)}, N_ge_expected=p_ge, p_le_05=sum(1 for v in ps if v <= 0.05),
                top5=[d for _, d in excess[:5]], top5_share=(top5 / total) if total > 0 else None, Z_min=min(Zs), Z_max=max(Zs), Z_median=float(np.median(Zs)), share_pos=sum(1 for v in Zs if v > 0) / n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", default=str(DEFAULT_RESULTS))
    ap.add_argument("--sweep-dir", default=str(SWEEP))
    ap.add_argument("--b", type=int, default=B_BOOT, help="bootstrap replicates (the results file must have been made with the same B)")
    ap.add_argument("--out", default=None, help="write the report here (a new file)")
    args = ap.parse_args(argv)
    res = json.load(open(args.results))
    CAL = res["question"]["CAL"]
    sweep_dir = Path(args.sweep_dir)
    ex1, d1j, d2j, d3j = (json.load(open(p)) for p in (EX1, D1J, D2J, D3J))
    d2q = d2j["question"]["D2"]
    d1q = d1j["question"]["D1"]
    Bn = args.b

    # Q1 designs (Q1-V3 rule: estimated, finite b, positive noise SD and cutoff, support_pass not false)
    q1 = []
    for r in ex1["designs"]:
        if (r.get("status") == "estimated" and finite(r.get("b")) and finite(r.get("noise_sd")) and r["noise_sd"] > 0 and finite(r.get("noise_cutoff"))
                and r["noise_cutoff"] > 0 and r.get("support_pass") is not False):
            q1.append(dict(id=r["design_id"], pf=r["policy_family"], g3=r.get("group3"), absb=abs(r["b"]), cut=r["noise_cutoff"]))
    compare("Q1:n", CAL["Q1"]["at_1"]["n"], len(q1))

    # D1 designs from the sweep files
    run = {d["design_id"]: d for d in json.load(open(sweep_dir / "SWEEP_RUN.json"))["designs"] if d["status"] == "OK"}
    d1_rows = {r["design_id"]: r for r in d1j["designs"] if r.get("code") == "EVAL"}
    ids = sorted(d1_rows)
    designs = {}
    for did in ids:
        z = np.load(sweep_dir / Path(run[did]["file"]).name, allow_pickle=False)
        out = plain_design([str(x) for x in z["series_ids"]], [str(x) for x in z["family"]], [str(x) for x in z["code"]], z["att"].tolist(), z["placebo"].tolist())
        if out is None:
            problems.append(f"{did}: not EVAL on plain recomputation")
            continue
        out["pf"] = d1_rows[did]["policy_family"]
        designs[did] = out
        compare(f"D1 {did}:mu", d1_rows[did]["mu"], out["mu"])
        compare(f"D1 {did}:tau", d1_rows[did]["tau"], out["tau"])
        for f, st in out["families"].items():
            s = d1_rows[did]["family_stats"][f]
            compare(f"D1 {did}|{f}:M", s["M"], st["M"])
            compare(f"D1 {did}|{f}:q", s["q"], st["q"])
            compare(f"D1 {did}|{f}:r", s["share_draws_beyond"], st["r"])
    ids = [d for d in ids if d in designs]
    cells2 = {(c["design_id"], c["family"]): c for c in d2j["cells"]}

    # negative-control series
    ser = []
    for did in ids:
        for s in designs[did]["series"]:
            c = cells2[(did, s["fam"])]
            if c["code"] == "NC":
                ser.append(dict(s, design_id=did, pf=designs[did]["pf"], group=c["policy_group"], strict=bool(c["flags"]["strict"])))
    z_nc = [s["z"] for s in ser]
    ref = d2q["empirical_null_width_nc_series"]["reference_mean_sd"]
    ref_s = d2q["empirical_null_width_strict_set"]["reference_mean_sd"]
    kappa_hat = sd_(z_nc) / ref
    z_strict = [s["z"] for s in ser if s["strict"]]
    kappa_strict = sd_(z_strict) / ref_s
    compare("kappa:kappa_hat", CAL["kappa"]["kappa_hat"], kappa_hat)
    compare("kappa:kappa_strict", CAL["kappa"]["kappa_strict"], kappa_strict)
    compare("kappa:kappa_hat_vs_D2", kappa_hat, d2q["empirical_null_width_nc_series"]["ratio_observed_to_reference_mean"])
    compare("kappa:kappa_strict_vs_D2", kappa_strict, d2q["empirical_null_width_strict_set"]["ratio_observed_to_reference_mean"])
    compare("kappa:n_series", CAL["kappa"]["n_series"], len(ser))
    compare("kappa:n_series_strict", CAL["kappa"]["n_series_strict"], len(z_strict))
    compare("kappa:sd_z_nc", CAL["kappa"]["sd_z_nc"], sd_(z_nc))
    compare("kappa:mean_z_nc", CAL["kappa"]["mean_z_nc"], mean_(z_nc))
    compare("kappa:F_blocks_nc", CAL["kappa"]["F_blocks_nc"], len({s["pf"] for s in ser}))

    # identities with the stored results (amendment 3.5)
    d1_at1 = d1_at(designs, ids, 1.0)
    compare("identity:theta_D1", d1_at1["theta"], d1q["theta_D1_plain_mean_Z"])
    compare("identity:T_D", d1_at1["T_D"], d1q["series_calibration"]["families_beyond_line"])
    compare("identity:sum_mu", d1_at1["sum_mu"], d1q["series_calibration"]["families_beyond_expected"])
    compare("identity:p_le_05", d1_at1["p_le_05"], d1q["pattern_counts"]["designs_p_le_05"])
    if d1_at1["top5"] != d1q["pattern_counts"]["top5_designs"]:
        problems.append("identity: top 5 designs differ from D1-V1")
    compare("identity:top5_share", d1_at1["top5_share"], d1q["pattern_counts"]["excess_top5_share"])
    for i, did in enumerate(ids):
        compare(f"identity {did}:Z", d1_at1["Z"][i], d1_rows[did]["Z"])
        compare(f"identity {did}:p", d1_at1["p"][i], d1_rows[did]["p"])
        compare(f"identity {did}:N", d1_at1["N"][i], d1_rows[did]["N"])
    q1_beyond1 = sorted(r["id"] for r in q1 if r["absb"] > r["cut"])
    if q1_beyond1 != sorted(ex1["questions"]["Q1"]["exceeding_designs"]):
        problems.append("identity: Q1 designs beyond at kappa = 1 differ from Q1-V3")
    nc_cells = [(did, f) for did in ids for f in designs[did]["families"] if cells2[(did, f)]["code"] == "NC"]
    nc_cell_beyond1 = sum(1 for did, f in nc_cells if designs[did]["families"][f]["M"] > designs[did]["families"][f]["q"])
    compare("identity:nc_cells", len(nc_cells), d2q["estimate_nc_cells"]["m"])
    compare("identity:nc_cells_beyond", nc_cell_beyond1, d2q["estimate_nc_cells"]["n_beyond"])
    nc_series_beyond1 = [s["abs_att"] > s["own_cut"] for s in ser]
    compare("identity:nc_series_beyond", sum(nc_series_beyond1), d2q["series_level_nc"]["series_beyond_own_line"])

    # Q1 and D1 at the profile of kappa, plus the interval of kappa from BD (below)
    def q1_at(kappa):
        k = sum(1 for r in q1 if r["absb"] > kappa * r["cut"])
        n = len(q1)
        return dict(k=k, n=n, rate=k / n, excess_rate=k / n - NOMINAL, excess_designs=k - NOMINAL * n)

    # blocks
    bq = sorted({r["pf"] for r in q1})
    bd = sorted({designs[d]["pf"] for d in ids})
    if not {s["pf"] for s in ser} <= set(bq):
        problems.append("a negative-control family is not a Q1 family")

    def nc_block_sums(blocks):
        nf = [0] * len(blocks)
        s1 = [0.0] * len(blocks)
        s2 = [0.0] * len(blocks)
        bs = [0] * len(blocks)
        for s, b1 in zip(ser, nc_series_beyond1):
            k = blocks.index(s["pf"])
            nf[k] += 1
            s1[k] += s["z"]
            s2[k] += s["z"] ** 2
            bs[k] += int(b1)
        return nf, s1, s2, bs

    def kappa_star(row, nf, s1, s2):
        n = sum(nf[f] for f in row)
        if n <= 1:
            return float("nan"), (sum(s1[f] for f in row) / n if n > 0 else float("nan"))
        a = sum(s1[f] for f in row)
        b2 = sum(s2[f] for f in row)
        var = (b2 - a * a / n) / (n - 1)
        return math.sqrt(max(var, 0.0)) / ref, a / n

    # ---- BQ ----
    nfq, s1q, s2q, bsq = nc_block_sums(bq)
    members_q = {k: [r for r in q1 if r["pf"] == bq[k]] for k in range(len(bq))}
    nQ = [len(members_q[k]) for k in range(len(bq))]
    h1 = [sum(1 for r in members_q[k] if r["absb"] > r["cut"]) for k in range(len(bq))]
    h0 = [sum(1 for r in members_q[k] if r["absb"] > kappa_hat * r["cut"]) for k in range(len(bq))]
    drawq = np.random.default_rng(SEED_Q).integers(0, len(bq), size=(Bn, len(bq)))
    qj, qd, qk, qd1, qnc, qrr, qks, qzm = [], [], [], [], [], [], [], []
    for row in drawq.tolist():
        ks, zm = kappa_star(row, nfq, s1q, s2q)
        den = sum(nQ[f] for f in row)
        qks.append(ks)
        qzm.append(zm)
        d1v = sum(h1[f] for f in row) / den
        qd1.append(d1v)
        qd.append(sum(h0[f] for f in row) / den)
        nsr = sum(nfq[f] for f in row)
        ncr = (sum(bsq[f] for f in row) / nsr) if nsr > 0 else float("nan")
        qnc.append(ncr)
        qrr.append(d1v - ncr)
        if ks != ks:
            qj.append(float("nan"))
            qk.append(float("nan"))
        else:
            qj.append(sum(sum(1 for r in members_q[f] if r["absb"] > ks * r["cut"]) for f in row) / den)
            qk.append(sum(1 for r in q1 if r["absb"] > ks * r["cut"]) / len(q1))

    # ---- BD ----
    nfd, s1d, s2d, bsd = nc_block_sums(bd)
    members_d = {k: [d for d in ids if designs[d]["pf"] == bd[k]] for k in range(len(bd))}
    nD = [len(members_d[k]) for k in range(len(bd))]
    cell_b1 = [0] * len(bd)
    cell_n = [0] * len(bd)
    ncm = [0] * len(bd)
    ncB = [0] * len(bd)
    for did in ids:
        k = bd.index(designs[did]["pf"])
        for f, st in designs[did]["families"].items():
            cell_n[k] += 1
            cell_b1[k] += int(st["M"] > st["q"])
            if cells2[(did, f)]["code"] == "NC":
                ncm[k] += 1
                ncB[k] += int(st["M"] > st["q"])

    def zE_at(kappa):
        Z, E = {}, {}
        for did in ids:
            d = designs[did]
            N = sum(1 for f, st in d["families"].items() if st["M"] > kappa * st["q"])
            E[did] = N - d["mu"]
            Z[did] = E[did] / d["tau"]
        return Z, E

    Zh, Eh = zE_at(kappa_hat)
    Z1, E1 = zE_at(1.0)
    drawd = np.random.default_rng(SEED_D).integers(0, len(bd), size=(Bn, len(bd)))
    dj, dje, dd, dde, dk, dke, dd1, dd1e, dnc, dall, drr, dks, dzm = [], [], [], [], [], [], [], [], [], [], [], [], []
    for row in drawd.tolist():
        ks, zm = kappa_star(row, nfd, s1d, s2d)
        den = sum(nD[f] for f in row)
        dks.append(ks)
        dzm.append(zm)
        dd.append(sum(Zh[d] for f in row for d in members_d[f]) / den)
        dde.append(sum(Eh[d] for f in row for d in members_d[f]) / den)
        dd1.append(sum(Z1[d] for f in row for d in members_d[f]) / den)
        dd1e.append(sum(E1[d] for f in row for d in members_d[f]) / den)
        nm = sum(ncm[f] for f in row)
        nr = (sum(ncB[f] for f in row) / nm) if nm > 0 else float("nan")
        ar = sum(cell_b1[f] for f in row) / sum(cell_n[f] for f in row)
        dnc.append(nr)
        dall.append(ar)
        drr.append(ar - nr)
        if ks != ks:
            for lst in (dj, dje, dk, dke):
                lst.append(float("nan"))
        else:
            Z, E = zE_at(ks)
            dj.append(sum(Z[d] for f in row for d in members_d[f]) / den)
            dje.append(sum(E[d] for f in row for d in members_d[f]) / den)
            dk.append(mean_(list(Z.values())))
            dke.append(mean_(list(E.values())))
    kint = percentile_pair(dks)
    compare("kappa:interval95_BD:lo", CAL["kappa"]["interval95_BD"][0], kint[0])
    compare("kappa:interval95_BD:hi", CAL["kappa"]["interval95_BD"][1], kint[1])
    qint = percentile_pair(qks)
    compare("kappa:interval95_BQ:lo", CAL["kappa"]["interval95_BQ"][0], qint[0])
    compare("kappa:interval95_BQ:hi", CAL["kappa"]["interval95_BQ"][1], qint[1])
    compare("kappa:mean_z_interval:lo", CAL["kappa"]["mean_z_interval95_BD"][0], percentile_pair(dzm)[0])
    compare("kappa:mean_z_interval:hi", CAL["kappa"]["mean_z_interval95_BD"][1], percentile_pair(dzm)[1])
    compare("kappa:share_below_1", CAL["kappa"]["share_replicates_below_1_BD"], sum(1 for v in dks if v == v and v < 1.0) / sum(1 for v in dks if v == v))

    # intervals
    qi = CAL["Q1"]["intervals"]
    interval_check("Q1 rate joint", qi["rate"]["joint"], qj)
    interval_check("Q1 rate data", qi["rate"]["data_only_kappa_fixed"], qd)
    interval_check("Q1 rate kappa", qi["rate"]["kappa_only_data_fixed"], qk)
    interval_check("Q1 excess joint", qi["excess_rate"]["joint"], [v - NOMINAL for v in qj])
    interval_check("Q1 excess data", qi["excess_rate"]["data_only_kappa_fixed"], [v - NOMINAL for v in qd])
    interval_check("Q1 excess kappa", qi["excess_rate"]["kappa_only_data_fixed"], [v - NOMINAL for v in qk])
    interval_check("Q1 uncalibrated", qi["uncalibrated_rate_same_method"], qd1)
    di = CAL["D1"]["intervals"]
    interval_check("D1 theta joint", di["theta"]["joint"], dj)
    interval_check("D1 theta data", di["theta"]["data_only_kappa_fixed"], dd)
    interval_check("D1 theta kappa", di["theta"]["kappa_only_data_fixed"], dk)
    interval_check("D1 e joint", di["e_D_mean_excess_per_design"]["joint"], dje)
    interval_check("D1 e data", di["e_D_mean_excess_per_design"]["data_only_kappa_fixed"], dde)
    interval_check("D1 e kappa", di["e_D_mean_excess_per_design"]["kappa_only_data_fixed"], dke)
    interval_check("D1 uncalibrated theta", di["uncalibrated_theta_same_method"], dd1)

    # Q1 and D1 point results
    for key, kappa in (("at_1", 1.0), ("at_kappa_hat", kappa_hat), ("at_kappa_strict", kappa_strict)):
        a = q1_at(kappa)
        for k in ("k", "n", "rate", "excess_rate", "excess_designs"):
            compare(f"Q1 {key}:{k}", CAL["Q1"][key][k], a[k])
        d = d1_at(designs, ids, kappa)
        got = CAL["D1"][key]
        for k, v in (("theta", d["theta"]), ("e_D_mean_excess_per_design", d["e_D"]), ("excess_total", d["excess_total"]), ("families_beyond_T_D", d["T_D"]), ("families_expected_sum_mu", d["sum_mu"]),
                     ("designs_p_le_05", d["p_le_05"]), ("excess_top5_share", d["top5_share"]), ("Z_min", d["Z_min"]), ("Z_max", d["Z_max"]), ("Z_median", d["Z_median"]), ("share_Z_positive", d["share_pos"])):
            compare(f"D1 {key}:{k}", got[k], v)
        for k in (1, 2, 3):
            compare(f"D1 {key}:N_ge_{k}", got["designs_N_ge"][str(k)], d["N_ge"][k])
            compare(f"D1 {key}:N_ge_expected_{k}", got["designs_N_ge_expected"][str(k)], d["N_ge_expected"][k])
        if got["top5_designs"] != d["top5"]:
            problems.append(f"D1 {key}: top five designs {got['top5_designs']} vs {d['top5']}")
    # profile
    for g in CAL["profile"]:
        lab = g["label"]
        kappa = {"strict": kappa_strict, "hat": kappa_hat, "kappa_interval_lo": kint[0], "kappa_interval_hi": kint[1]}.get(lab)
        if kappa is None:
            kappa = float(lab)
        a = q1_at(kappa)
        for k in ("k", "rate", "excess_rate"):
            compare(f"profile {lab} Q1:{k}", g["Q1"][k], a[k])
        d = d1_at(designs, ids, kappa)
        for k, v in (("theta", d["theta"]), ("e_D_mean_excess_per_design", d["e_D"]), ("families_beyond_T_D", d["T_D"]), ("designs_p_le_05", d["p_le_05"]), ("designs_N_ge_2", d["N_ge"][2])):
            compare(f"profile {lab} D1:{k}", g["D1"][k], v)

    # designs
    got_q = {r["design_id"]: r for r in res["designs_q1"]}
    for r in q1:
        g = got_q[r["id"]]
        compare(f"design_q1 {r['id']}:rho", g["rho"], r["absb"] / r["cut"])
        for k, kappa in (("beyond_at_1", 1.0), ("beyond_at_kappa_hat", kappa_hat), ("beyond_at_kappa_strict", kappa_strict)):
            compare(f"design_q1 {r['id']}:{k}", g[k], r["absb"] > kappa * r["cut"])
        compare(f"design_q1 {r['id']}:swept", g["swept"], r["id"] in designs)
    for g3 in sorted({r["g3"] for r in q1}):
        sel = [r for r in q1 if r["g3"] == g3]
        sp = CAL["Q1"]["splits"][f"group3={g3}"]
        compare(f"Q1 split {g3}:n", sp["n"], len(sel))
        compare(f"Q1 split {g3}:k_at_kappa_hat", sp["k_at_kappa_hat"], sum(1 for r in sel if r["absb"] > kappa_hat * r["cut"]))
    for nm, sel in (("swept", [r for r in q1 if r["id"] in designs]), ("not_swept", [r for r in q1 if r["id"] not in designs])):
        sp = CAL["Q1"]["splits"][nm]
        compare(f"Q1 split {nm}:n", sp["n"], len(sel))
        compare(f"Q1 split {nm}:k_at_1", sp["k_at_1"], sum(1 for r in sel if r["absb"] > r["cut"]))
        compare(f"Q1 split {nm}:k_at_kappa_hat", sp["k_at_kappa_hat"], sum(1 for r in sel if r["absb"] > kappa_hat * r["cut"]))
        compare(f"Q1 split {nm}:k_at_kappa_strict", sp["k_at_kappa_strict"], sum(1 for r in sel if r["absb"] > kappa_strict * r["cut"]))
    dh = d1_at(designs, ids, kappa_hat)
    got_d = {r["design_id"]: r for r in res["designs_d1"]}
    for i, did in enumerate(ids):
        g = got_d[did]
        compare(f"design_d1 {did}:N_at_kappa_hat", g["N_at_kappa_hat"], dh["N"][i])
        compare(f"design_d1 {did}:Z_at_kappa_hat", g["Z_at_kappa_hat"], dh["Z"][i])
        compare(f"design_d1 {did}:p_at_kappa_hat", g["p_at_kappa_hat"], dh["p"][i])
        compare(f"design_d1 {did}:Z_at_1", g["Z_at_1"], d1_at1["Z"][i])
    beyond_list = [(did, f) for did in ids for f, st in designs[did]["families"].items() if st["M"] > st["q"]]
    got_b = {(x["design_id"], x["family"]): x for x in CAL["D1"]["families_beyond_at_1"]}
    if sorted(got_b) != sorted(beyond_list):
        problems.append("families beyond at kappa = 1: lists differ")
    for did, f in beyond_list:
        rho = designs[did]["families"][f]["M"] / designs[did]["families"][f]["q"]
        compare(f"beyond {did}|{f}:rho", got_b[(did, f)]["rho"], rho)
        compare(f"beyond {did}|{f}:at_kappa_hat", got_b[(did, f)]["beyond_at_kappa_hat"], designs[did]["families"][f]["M"] > kappa_hat * designs[did]["families"][f]["q"])
        compare(f"beyond {did}|{f}:nc", got_b[(did, f)]["nc_cell"], cells2[(did, f)]["code"] == "NC")
    # NC vs other cells
    for label, want_nc in (("nc_cells", True), ("other_cells", False)):
        sel = [(did, f) for did in ids for f in designs[did]["families"] if (cells2[(did, f)]["code"] == "NC") == want_nc]
        sp = CAL["D1"]["split_nc_vs_other_cells"][label]
        compare(f"split {label}:cells", sp["cells"], len(sel))
        compare(f"split {label}:beyond_at_1", sp["beyond_at_1"], sum(1 for d, f in sel if designs[d]["families"][f]["M"] > designs[d]["families"][f]["q"]))
        compare(f"split {label}:beyond_at_kappa_hat", sp["beyond_at_kappa_hat"], sum(1 for d, f in sel if designs[d]["families"][f]["M"] > kappa_hat * designs[d]["families"][f]["q"]))
        compare(f"split {label}:beyond_at_kappa_strict", sp["beyond_at_kappa_strict"], sum(1 for d, f in sel if designs[d]["families"][f]["M"] > kappa_strict * designs[d]["families"][f]["q"]))
        compare(f"split {label}:expected_sum_r", sp["expected_sum_r"], sum(designs[d]["families"][f]["r"] for d, f in sel))

    # fit (5.4)
    fit = CAL["fit_to_negative_controls"]
    cell_share_hat = sum(1 for d, f in nc_cells if designs[d]["families"][f]["M"] > kappa_hat * designs[d]["families"][f]["q"]) / len(nc_cells)
    cell_exp = mean_([designs[d]["families"][f]["r"] for d, f in nc_cells])
    ser_share_hat = sum(1 for s in ser if s["abs_att"] > kappa_hat * s["own_cut"]) / len(ser)
    ser_exp = mean_([s["exp_own"] for s in ser])
    compare("fit:cell_share_hat", fit["primary"]["cell_share_beyond_at_kappa_hat"], cell_share_hat)
    compare("fit:cell_share_expected", fit["primary"]["cell_share_expected"], cell_exp)
    compare("fit:series_share_hat", fit["primary"]["series_share_beyond_at_kappa_hat"], ser_share_hat)
    compare("fit:series_share_expected", fit["primary"]["series_share_expected"], ser_exp)
    compare("fit:series_share_at_1", fit["primary"]["series_share_beyond_at_1"], sum(nc_series_beyond1) / len(ser))
    compare("fit:within_3_points", fit["primary"]["within_3_points"], abs(cell_share_hat - cell_exp) <= FIT_TOL and abs(ser_share_hat - ser_exp) <= FIT_TOL)
    sc = [(d, f) for d, f in nc_cells if cells2[(d, f)]["flags"]["strict"]]
    ss = [s for s in ser if s["strict"]]
    compare("fit strict:cell_share", fit["strict"]["cell_share_beyond_at_kappa_strict"], sum(1 for d, f in sc if designs[d]["families"][f]["M"] > kappa_strict * designs[d]["families"][f]["q"]) / len(sc))
    compare("fit strict:series_share", fit["strict"]["series_share_beyond_at_kappa_strict"], sum(1 for s in ss if s["abs_att"] > kappa_strict * s["own_cut"]) / len(ss))
    compare("fit strict:cells", fit["strict"]["cells"], len(sc))

    # heterogeneity (5.5)
    groups = sorted({s["group"] for s in ser})
    loo = {}
    for g in groups:
        zg = [s["z"] for s in ser if s["group"] == g]
        rest = [s["z"] for s in ser if s["group"] != g]
        h = CAL["heterogeneity_by_policy_group"][g]
        compare(f"het {g}:series", h["series"], len(zg))
        compare(f"het {g}:sd_z", h["sd_z"], sd_(zg) if len(zg) > 1 else None)
        compare(f"het {g}:kappa_without_group", h["kappa_without_group"], sd_(rest) / ref if len(rest) > 1 else None)
        if len(rest) > 1:
            loo[g] = sd_(rest) / ref
    gmin, gmax = min(loo, key=loo.get), max(loo, key=loo.get)
    compare("kappa:loo_min", CAL["kappa"]["leave_one_group_out_min"]["kappa"], loo[gmin])
    compare("kappa:loo_max", CAL["kappa"]["leave_one_group_out_max"]["kappa"], loo[gmax])
    if (CAL["kappa"]["leave_one_group_out_min"]["group"], CAL["kappa"]["leave_one_group_out_max"]["group"]) != (gmin, gmax):
        problems.append("leave-one-group-out min or max group differs")

    # rate rule (5.6)
    rr = CAL["rate_rule_check"]
    pi_ncs = sum(nc_series_beyond1) / len(ser)
    pi_ncc = nc_cell_beyond1 / len(nc_cells)
    n_cells_all = sum(len(designs[d]["families"]) for d in ids)
    beyond_all = len(beyond_list)
    compare("rate Q1:rate", rr["Q1"]["rate"], q1_at(1.0)["rate"])
    compare("rate Q1:nc_series_rate", rr["Q1"]["nc_series_rate"], pi_ncs)
    compare("rate Q1:difference", rr["Q1"]["difference"], q1_at(1.0)["rate"] - pi_ncs)
    compare("rate Q1:expected", rr["Q1"]["expected_designs_from_nc_rate"], pi_ncs * len(q1))
    pp = percentile_pair(qrr)
    compare("rate Q1:interval:lo", rr["Q1"]["joint_interval95_of_difference"][0], pp[0])
    compare("rate Q1:interval:hi", rr["Q1"]["joint_interval95_of_difference"][1], pp[1])
    pp = percentile_pair(qnc)
    compare("rate Q1:nc_interval:lo", rr["Q1"]["nc_series_rate_interval95"][0], pp[0])
    compare("rate Q1:nc_interval:hi", rr["Q1"]["nc_series_rate_interval95"][1], pp[1])
    compare("rate D1:rate", rr["D1"]["rate"], beyond_all / n_cells_all)
    compare("rate D1:families_beyond", rr["D1"]["families_beyond"], beyond_all)
    compare("rate D1:nc_cell_rate", rr["D1"]["nc_cell_rate"], pi_ncc)
    compare("rate D1:difference", rr["D1"]["difference"], beyond_all / n_cells_all - pi_ncc)
    compare("rate D1:expected", rr["D1"]["expected_families_from_nc_rate"], pi_ncc * n_cells_all)
    pp = percentile_pair(drr)
    compare("rate D1:interval:lo", rr["D1"]["joint_interval95_of_difference"][0], pp[0])
    compare("rate D1:interval:hi", rr["D1"]["joint_interval95_of_difference"][1], pp[1])
    pp = percentile_pair(dnc)
    compare("rate D1:nc_interval:lo", rr["D1"]["nc_cell_rate_interval95"][0], pp[0])
    compare("rate D1:nc_interval:hi", rr["D1"]["nc_cell_rate_interval95"][1], pp[1])

    # D3 (5.7)
    d3_pairs = [p for p in d3j["pairs"] if p.get("code") == "EVAL"]
    for label, kappa in (("at_1", 1.0), ("at_kappa_hat", kappa_hat), ("at_kappa_strict", kappa_strict)):
        ta = sum(1 for p in d3_pairs if abs(p["numbers"]["a"]) > kappa * p["numbers"]["cutoff_a"])
        sg = sum(1 for p in d3_pairs if abs(p["numbers"]["a"]) > kappa * p["numbers"]["cutoff_a"] and p["numbers"]["opposite"] and abs(p["numbers"]["c"]) > kappa * p["numbers"]["cutoff_c"])
        compare(f"D3 {label}:targets_beyond", CAL["D3"][label]["targets_beyond"], ta)
        compare(f"D3 {label}:signals", CAL["D3"][label]["signals"], sg)
        compare(f"D3 {label}:pairs", CAL["D3"][label]["pairs"], len(d3_pairs))

    # labels of the reading guide
    lo, hi = percentile_pair([v - NOMINAL for v in qj])
    want_q = "ABOVE THE WIDENED LINE" if lo > 0 else "BELOW THE WIDENED LINE" if hi < 0 else "AT THE WIDENED LINE"
    compare("reading:Q1", CAL["reading_guide"]["Q1"], want_q)
    compare("reading:kappa", CAL["reading_guide"]["kappa"], "WIDER" if kint[0] > 1.0 else "NOT SEPARATED FROM 1")
    compare("reading:fit", CAL["reading_guide"]["fit"], "FITS" if (abs(cell_share_hat - cell_exp) <= FIT_TOL and abs(ser_share_hat - ser_exp) <= FIT_TOL) else "FITS POORLY")

    report = dict(results=str(args.results), B=Bn, designs=len(ids), q1_n=len(q1), nc_series=len(ser), kappa_hat=kappa_hat, kappa_strict=kappa_strict, kappa_interval_BD=kint,
                  q1_at_kappa_hat=q1_at(kappa_hat), d1_theta_at_kappa_hat=dh["theta"], largest_scaled_differences={k: float(f"{v:.2e}") for k, v in sorted(worst.items())}, problems=problems)
    print(json.dumps(report, indent=1, default=str))
    if args.out:
        with open(args.out, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=1, default=str) + "\n")
    if problems:
        print("MISMATCH", len(problems), file=sys.stderr)
        for p in problems[:40]:
            print("  ", p, file=sys.stderr)
        return 1
    print("OK: the independent recomputation agrees with the results file", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
