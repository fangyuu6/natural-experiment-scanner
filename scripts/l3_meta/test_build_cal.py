"""Checks of build_cal.py on INVENTED data (no repo data file is read; files are written only into a temporary folder).

  * kappa on known series, the strict inequality of "beyond the line", counts that never rise with kappa, kappa = 1 equals the random-timing line;
  * the arrays of a design (z, own line, family statistics, placebo family counts N*_ib) against build_d1.design_statistics and against the plain-loop checker;
  * D1 at kappa = 1 equals build_d1 (N, Z, p, the pattern counts and the five-design share) on invented designs, and the plain-loop checker agrees at other kappa;
  * a synthetic null whose ATTs are 1.4 times as wide as the placebo draws: kappa-hat is near 1.4, the uncalibrated theta is clearly positive and the calibrated one near 0;
    a planted effect stays beyond the widened line;
  * the vectorised bootstraps (Q1: JOINT, DATA, KAPPA, uncalibrated, rate rule; D1: the same) against explicit loops on the same integer matrices;
  * percentile intervals, the multiplicity matrix, the D3 counts, the hash-record check (a changed amendment or listed file is caught), write-once.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_cal.py
"""
import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import build_d1 as D1  # noqa: E402
import build_cal as C  # noqa: E402
import check_cal_independent as CHK  # noqa: E402  (the plain-loop checker; compared with the builder on invented data only)

FAMS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]


def synthetic(seed, per_family=2, shift=None, att_scale=1.0, n_draws=999, fams=FAMS):
    """Design-file dict of an invented design: independent N(0, 1) placebo draws per series, ATT ~ N(0, att_scale^2) plus an optional shift per family."""
    rng = np.random.default_rng(seed)
    sids, fam = [], []
    for f in fams:
        for j in range(per_family):
            sids.append(f"{f}_{j}")
            fam.append(f)
    K = len(sids)
    placebo = rng.normal(0, 1.0, (K, n_draws))
    att = rng.normal(0, att_scale, K)
    for i, f in enumerate(fam):
        if shift and f in shift:
            att[i] += shift[f]
    sd = np.array([np.std(placebo[i], ddof=1) for i in range(K)])
    return dict(sids=sids, fam=fam, code=["SUPPORTED"] * K, att=att, sd=sd, placebo=placebo, meta={"scale_mode": "rate"})


def stats_of(f):
    return D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], ())


def make_designs(n_designs, seed0, att_scale=1.0, shift=None, nc_families=None, per_family=2, family_of=None):
    """`designs` dict as sweep_inputs builds it, a fake D2 result with the cell codes, and the plain-loop versions."""
    designs, cells, plain = {}, [], {}
    for k in range(n_designs):
        f = synthetic(seed0 + k, per_family=per_family, att_scale=att_scale, shift=shift)
        st = stats_of(f)
        assert st["code"] == "EVAL"
        did = f"d{k:03d}"
        pf = family_of(k) if family_of else f"pf{k:03d}"
        arr = C.design_arrays(f, st)
        designs[did] = dict(arr=arr, row=dict(mu=st["mu"], tau=st["tau"], N=st["N"], Z=st["Z"], p=st["p"], P_Nstar_ge=st["P_Nstar_ge"], family_stats=st["family_stats"], policy_family=pf),
                            policy_family=pf, n_draws=st["n_draws"])
        for fm in arr["fam_mq"]:
            nc = (nc_families is None) or (fm in nc_families)
            cells.append(dict(design_id=did, family=fm, code="NC" if nc else "D2_PAIR_NOT_NC", policy_group=f"g{k % 3}", flags={"strict": nc}))
        plain[did] = CHK.plain_design(f["sids"], f["fam"], f["code"], f["att"].tolist(), f["placebo"].tolist())
        plain[did]["pf"] = pf
    return designs, {"cells": cells}, plain


class Basics(unittest.TestCase):
    def test_sd_ratio_known(self):
        z = [1.0, -1.0] * 10
        self.assertAlmostEqual(C.sd_ratio(z, 1.0), math.sqrt(20 / 19), places=12)
        self.assertAlmostEqual(C.sd_ratio(z, 2.0), math.sqrt(20 / 19) / 2, places=12)

    def test_n_beyond_is_strict_and_monotone(self):
        absx = np.array([1.0, 2.0, 3.0, 3.0])
        line = np.array([1.0, 1.0, 1.0, 1.5])
        self.assertEqual(C.n_beyond(absx, line, 1.0), 3)           # 1.0 is not beyond 1.0 (strict)
        self.assertEqual(C.n_beyond(absx, line, 2.0), 1)           # 3.0 > 2.0 x 1.0 and 3.0 > 3.0 is false
        ks = np.linspace(0.5, 4.0, 60)
        counts = [C.n_beyond(absx, line, k) for k in ks]
        self.assertTrue(all(a >= b for a, b in zip(counts, counts[1:])))

    def test_pct_and_interval_block(self):
        v = np.arange(1.0, 101.0)
        self.assertEqual(C.pct(v), [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))])
        v2 = np.array([1.0, 2.0, np.nan, 3.0])
        b = C.interval_block(v2)
        self.assertEqual((b["replicates"], b["defined"]), (4, 3))
        self.assertIsNone(C.pct([np.nan]))

    def test_multiplicity_rows_sum_to_F_and_count_draws(self):
        draw = np.array([[0, 0, 2], [1, 2, 2]])
        c = C.multiplicity(draw, 3)
        self.assertEqual(c.tolist(), [[2.0, 0.0, 1.0], [0.0, 1.0, 2.0]])
        rng = np.random.default_rng(3)
        d = rng.integers(0, 7, size=(50, 7))
        self.assertTrue(np.all(C.multiplicity(d, 7).sum(axis=1) == 7))

    def test_family_ids(self):
        self.assertEqual(C.family_ids(["b", "a", "b"], ["a", "b"]).tolist(), [1, 0, 1])

    def test_d3_counts(self):
        pairs = [dict(numbers=dict(a=-1.0, cutoff_a=0.9, c=0.5, cutoff_c=0.4, opposite=True)),
                 dict(numbers=dict(a=1.0, cutoff_a=0.9, c=-0.5, cutoff_c=0.4, opposite=False)),
                 dict(numbers=dict(a=0.5, cutoff_a=0.9, c=0.5, cutoff_c=0.4, opposite=True))]
        self.assertEqual(dict(C.d3_counts(pairs, 1.0)), dict(pairs=3, targets_beyond=2, signals=1))
        self.assertEqual(dict(C.d3_counts(pairs, 1.2)), dict(pairs=3, targets_beyond=0, signals=0))


class DesignArrays(unittest.TestCase):
    def test_arrays_equal_design_statistics_and_plain_checker(self):
        f = synthetic(11, per_family=3, att_scale=1.3)
        st = stats_of(f)
        arr = C.design_arrays(f, st)
        pl = CHK.plain_design(f["sids"], f["fam"], f["code"], f["att"].tolist(), f["placebo"].tolist())
        self.assertAlmostEqual(arr["nstar"].mean(), st["mu"], places=12)
        self.assertAlmostEqual(arr["nstar"].std(ddof=1), st["tau"], places=12)
        self.assertAlmostEqual(pl["mu"], st["mu"], places=12)
        self.assertAlmostEqual(pl["tau"], st["tau"], places=12)
        self.assertEqual(list(arr["nstar"]), pl["nstar"])
        for fm, (M, q, r) in arr["fam_mq"].items():
            s = st["family_stats"][fm]
            for a, b in ((M, s["M"]), (q, s["q"]), (r, s["share_draws_beyond"]), (pl["families"][fm]["M"], M), (pl["families"][fm]["q"], q), (pl["families"][fm]["r"], r)):
                self.assertAlmostEqual(a, b, places=12)
        for sa, sp in zip(sorted(pl["series"], key=lambda x: x["sid"]), sorted(zip(arr["sids"], arr["fam"], arr["z"], arr["abs_att"], arr["own_cut"], arr["exp_own"]))):
            self.assertEqual(sa["sid"], sp[0])
            for a, b in ((sa["z"], sp[2]), (sa["abs_att"], sp[3]), (sa["own_cut"], sp[4]), (sa["exp_own"], sp[5])):
                self.assertAlmostEqual(a, b, places=12)

    def test_d1_at_one_equals_build_d1(self):
        designs, d2, plain = make_designs(12, 100, att_scale=1.2)
        data = C.build_arrays(designs, d2)
        out, N, p = C.d1_eval_at(data, designs, 1.0)
        rows = []
        for i, did in enumerate(data["ids"]):
            row = designs[did]["row"]
            self.assertEqual(int(N[i]), row["N"])
            self.assertAlmostEqual((N[i] - row["mu"]) / row["tau"], row["Z"], places=12)
            self.assertAlmostEqual(p[i], row["p"], places=12)
            rows.append(dict(design_id=did, N=row["N"], mu=row["mu"], p=row["p"], Z=row["Z"], P_Nstar_ge=row["P_Nstar_ge"]))
        pc = D1.pattern_counts(rows)
        self.assertEqual(out["designs_p_le_05"], pc["designs_p_le_05"])
        self.assertEqual(out["designs_N_ge"], {str(k): v for k, v in pc["designs_N_ge"].items()})
        self.assertEqual(out["top5_designs"], pc["top5_designs"])
        self.assertAlmostEqual(out["excess_top5_share"], pc["excess_top5_share"], places=12)
        self.assertAlmostEqual(out["theta"], np.mean([r["Z"] for r in rows]), places=12)

    def test_builder_and_plain_checker_agree_at_other_kappa(self):
        designs, d2, plain = make_designs(10, 300, att_scale=1.5)
        data = C.build_arrays(designs, d2)
        ids = data["ids"]
        for kappa in (1.0, 1.15, 1.3154, 1.6, 2.5):
            out, N, p = C.d1_eval_at(data, designs, kappa)
            ref = CHK.d1_at(plain, ids, kappa)
            self.assertEqual(N.tolist(), ref["N"])
            for a, b in ((out["theta"], ref["theta"]), (out["e_D_mean_excess_per_design"], ref["e_D"]), (out["families_beyond_T_D"], ref["T_D"]), (out["designs_p_le_05"], ref["p_le_05"])):
                self.assertAlmostEqual(a, b, places=10)
            self.assertEqual(out["top5_designs"], ref["top5"])
            for a, b in zip(p, ref["p"]):
                self.assertAlmostEqual(a, b, places=12)

    def test_counts_do_not_rise_with_kappa(self):
        designs, d2, _ = make_designs(8, 500, att_scale=1.8)
        data = C.build_arrays(designs, d2)
        last = None
        for kappa in np.linspace(0.8, 3.0, 25):
            _, N, _ = C.d1_eval_at(data, designs, float(kappa))
            if last is not None:
                self.assertTrue(np.all(N <= last))
            last = N


class SyntheticNullAndEffect(unittest.TestCase):
    def test_scaled_null_is_calibrated_by_kappa_hat(self):
        designs, d2, _ = make_designs(150, 1000, att_scale=1.4)
        data = C.build_arrays(designs, d2)
        z = np.array([s["z"] for s in data["series"]])
        kappa = C.sd_ratio(z, 1.0)
        self.assertGreater(kappa, 1.3)
        self.assertLess(kappa, 1.5)
        th1 = C.d1_eval_at(data, designs, 1.0)[0]["theta"]
        thk = C.d1_eval_at(data, designs, kappa)[0]["theta"]
        self.assertGreater(th1, 0.8)
        self.assertLess(abs(thk), 0.35)
        # series level: about 5% beyond the calibrated own line, more than 12% beyond the random-timing line
        abs_att = np.array([s["abs_att"] for s in data["series"]])
        cut = np.array([s["own_cut"] for s in data["series"]])
        self.assertGreater(np.mean(abs_att > cut), 0.12)
        self.assertLess(abs(np.mean(abs_att > kappa * cut) - 0.05), 0.02)

    def test_a_null_at_the_random_timing_scale_is_untouched(self):
        designs, d2, _ = make_designs(150, 3000, att_scale=1.0)
        data = C.build_arrays(designs, d2)
        z = np.array([s["z"] for s in data["series"]])
        self.assertLess(abs(C.sd_ratio(z, 1.0) - 1.0), 0.06)
        self.assertLess(abs(C.d1_eval_at(data, designs, 1.0)[0]["theta"]), 0.35)

    def test_planted_effect_stays_beyond_the_widened_line(self):
        designs, d2, _ = make_designs(20, 4000, att_scale=1.0, shift={"alpha": 14.0}, nc_families=["beta", "gamma", "delta", "epsilon", "zeta"])
        data = C.build_arrays(designs, d2)
        # NC series exclude the shifted family, so kappa stays near 1
        z = np.array([s["z"] for s in data["series"]])
        kappa = C.sd_ratio(z, 1.0)
        self.assertLess(kappa, 1.15)
        _, N, _ = C.d1_eval_at(data, designs, max(kappa, 1.5))
        self.assertTrue(np.all(N >= 1))
        k = [i for i, (_, d, f) in enumerate(data["fam_cells"]) if f == "alpha"]
        self.assertTrue(np.all(data["cell_M"][k] > 1.5 * data["cell_q"][k]))


class Bootstraps(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(77)
        self.F = 6
        # Q1: 14 designs in 6 blocks
        self.blk = np.array([0, 0, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5])
        self.absb = np.abs(rng.normal(0, 1.7, 14))
        self.cut = np.abs(rng.normal(1.8, 0.2, 14))
        # negative-control series: 40 series in blocks 0..4 (block 5 holds none)
        self.nc_blk = rng.integers(0, 5, size=40)
        self.nc_z = rng.normal(0, 1.3, 40)
        self.nc_own = np.abs(rng.normal(0, 1.9, 40)) > np.abs(rng.normal(0, 1, 40)) * 2
        self.ref = 0.997

    def test_q1_bootstrap_matches_explicit_loops(self):
        B_ = 40
        out = C.bootstrap_q1(self.absb, self.cut, self.blk, self.F, self.nc_blk, self.nc_z, self.nc_own, self.ref, 1.31, B_=B_, seed=[1, 2, 3])
        draw = np.random.default_rng([1, 2, 3]).integers(0, self.F, size=(B_, self.F))
        for b in range(B_):
            row = draw[b].tolist()
            ns = [(self.nc_z[j], self.nc_own[j]) for f in row for j in range(40) if self.nc_blk[j] == f]
            n = len(ns)
            if n > 1:
                m = sum(v for v, _ in ns) / n
                a = sum(v for v, _ in ns)
                b2 = sum(v * v for v, _ in ns)
                ks = math.sqrt(max((b2 - a * a / n) / (n - 1), 0.0)) / self.ref
            else:
                ks = float("nan")
            designs = [i for f in row for i in range(14) if self.blk[i] == f]
            den = len(designs)
            self.assertAlmostEqual(out["kappa"][b], ks, places=12)
            self.assertAlmostEqual(out["data"][b], sum(1 for i in designs if self.absb[i] > 1.31 * self.cut[i]) / den, places=12)
            self.assertAlmostEqual(out["data_uncal"][b], sum(1 for i in designs if self.absb[i] > self.cut[i]) / den, places=12)
            if ks == ks:
                self.assertAlmostEqual(out["joint"][b], sum(1 for i in designs if self.absb[i] > ks * self.cut[i]) / den, places=12)
                self.assertAlmostEqual(out["kappa_only"][b], sum(1 for i in range(14) if self.absb[i] > ks * self.cut[i]) / 14, places=12)
            nsr = sum(1 for _, o in ns)
            if nsr:
                self.assertAlmostEqual(out["nc_series_rate"][b], sum(1 for _, o in ns if o) / nsr, places=12)

    def test_d1_bootstrap_matches_explicit_loops(self):
        rng = np.random.default_rng(5)
        n_d, F = 9, 4
        des_blk = np.array([0, 0, 1, 1, 1, 2, 2, 3, 3])
        cells_per = [4, 5, 3, 4, 5, 4, 3, 5, 4]
        cell_design = np.repeat(np.arange(n_d), cells_per)
        cell_M = np.abs(rng.normal(1.8, 0.8, cell_design.size))
        cell_q = np.abs(rng.normal(2.0, 0.2, cell_design.size))
        cell_blk = des_blk[cell_design]
        cell_nc = rng.random(cell_design.size) < 0.5
        mu = rng.uniform(0.3, 0.7, n_d)
        tau = rng.uniform(0.5, 0.9, n_d)
        nc_blk = rng.integers(0, F, size=30)
        nc_z = rng.normal(0, 1.25, 30)
        B_ = 40
        out = C.bootstrap_d1(cell_M, cell_q, cell_design, des_blk, cell_blk, cell_nc, mu, tau, F, nc_blk, nc_z, self.ref, 1.28, B_=B_, seed=[4, 5])
        draw = np.random.default_rng([4, 5]).integers(0, F, size=(B_, F))

        def ZE(kappa):
            Z, E = [], []
            for i in range(n_d):
                N = sum(1 for k in range(cell_design.size) if cell_design[k] == i and cell_M[k] > kappa * cell_q[k])
                E.append(N - mu[i])
                Z.append((N - mu[i]) / tau[i])
            return Z, E

        Zh, Eh = ZE(1.28)
        Z1, E1 = ZE(1.0)
        for b in range(B_):
            row = draw[b].tolist()
            ds = [i for f in row for i in range(n_d) if des_blk[i] == f]
            ns = [nc_z[j] for f in row for j in range(30) if nc_blk[j] == f]
            n = len(ns)
            ks = (math.sqrt(max((sum(v * v for v in ns) - sum(ns) ** 2 / n) / (n - 1), 0.0)) / self.ref) if n > 1 else float("nan")
            self.assertAlmostEqual(out["kappa"][b], ks, places=12)
            self.assertAlmostEqual(out["data_theta"][b], sum(Zh[i] for i in ds) / len(ds), places=12)
            self.assertAlmostEqual(out["data_e"][b], sum(Eh[i] for i in ds) / len(ds), places=12)
            self.assertAlmostEqual(out["data_uncal_theta"][b], sum(Z1[i] for i in ds) / len(ds), places=12)
            if ks == ks:
                Z, E = ZE(ks)
                self.assertAlmostEqual(out["joint_theta"][b], sum(Z[i] for i in ds) / len(ds), places=12)
                self.assertAlmostEqual(out["joint_e"][b], sum(E[i] for i in ds) / len(ds), places=12)
                self.assertAlmostEqual(out["kappa_only_theta"][b], sum(Z) / n_d, places=12)
            all_b = sum(int(cell_M[k] > cell_q[k]) * row.count(cell_blk[k]) for k in range(cell_design.size)) / sum(row.count(cell_blk[k]) for k in range(cell_design.size))
            self.assertAlmostEqual(out["all_cell_rate"][b], all_b, places=12)

    def test_kappa_only_equals_joint_when_the_data_do_not_depend_on_the_draw(self):
        # one Q1 block only: every replicate resamples that block, so JOINT = KAPPA
        out = C.bootstrap_q1(self.absb, self.cut, np.zeros(14, dtype=int), 1, np.zeros(40, dtype=int), self.nc_z, self.nc_own, self.ref, 1.3, B_=30, seed=[9])
        self.assertTrue(np.allclose(out["joint"], out["kappa_only"]))
        self.assertTrue(np.allclose(out["kappa"], out["kappa"][0]))     # the same 40 series are drawn every time: kappa is constant

    def test_data_interval_is_degenerate_when_kappa_is_the_only_noise(self):
        # all Q1 designs in one block: the DATA rate does not move
        out = C.bootstrap_q1(self.absb, self.cut, np.zeros(14, dtype=int), 1, np.zeros(40, dtype=int), self.nc_z, self.nc_own, self.ref, 1.3, B_=30, seed=[9])
        self.assertTrue(np.allclose(out["data"], out["data"][0]))


class Records(unittest.TestCase):
    def test_hash_record_catches_changes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            amend = td / "A.md"
            amend.write_text("rules\n")
            listed = td / "input.json"
            listed.write_text("{}\n")
            sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            rec = td / "REC.txt"
            rec.write_text(f"file: {C.S.rel(amend)}\nsha256: {sha(amend)}\nbytes: 6\namended_utc (T_amend): 2026-10-16T00:00:00Z\ninputs:\n  {C.S.rel(listed)} sha256 {sha(listed)}\n")
            probs, _ = C.verify_cal_records(amend, rec)
            self.assertEqual(probs, [])
            amend.write_text("rules changed\n")
            probs, _ = C.verify_cal_records(amend, rec)
            self.assertTrue(any("differs from its hash record" in p for p in probs))
            amend.write_text("rules\n")
            listed.write_text('{"x": 1}\n')
            probs, _ = C.verify_cal_records(amend, rec)
            self.assertTrue(any("differs from the CAL hash record" in p for p in probs))

    def test_write_once(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "R.json"
            C.write_once(p, "a")
            with self.assertRaises(SystemExit):
                C.write_once(p, "b")
            self.assertEqual(p.read_text(), "a")


if __name__ == "__main__":
    unittest.main(verbosity=1)
