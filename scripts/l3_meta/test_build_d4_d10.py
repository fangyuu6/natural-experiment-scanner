"""Checks of build_d4_d10.py on INVENTED data (the only real data read are the hash record, the registered tables and, for one identity of definitions, one design
file of the sweep; files are written only into a temporary folder).

  * D4: the decomposition against plain loops and the identity on a balanced layout, pruning, the end-to-end result against the plain-loop recomputation on the same
    random numbers, a synthetic null (excess near 0, p not small), planted policy / outcome / pair effects each raising the matching part and the label, a null scaled
    by 1.4 (raw excess positive, calibrated near 0);
  * D5: values, p and bootstrap against plain loops, known spread recovered, a null with no spread;
  * D6: the scale statistics against plain loops, the MDE and class rule, unsupported series;
  * D7: the data-type rule, exact intervals against known values and the plain-loop version, the permutation test (separated and unseparated groups);
  * D8: IDENTICAL twins, the share beyond the line under a null near 5%, a shifted pair beyond the line, the plain-loop version;
  * D9: break years from catalog fields, the exposure and its groups, the stratified permutation and the bootstrap against plain loops;
  * D10: the status and shares of invented rows;
  * the definitions of the cells equal build_cal.design_arrays on one real design; the hash record is consistent and a changed amendment, entry or listed file is caught;
    write-once; no __pycache__ appears under scripts/l2_pipeline.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_d4_d10.py
"""
import math
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import build_cal as CAL  # noqa: E402  (definitions only: design_arrays)
import build_d1 as D1  # noqa: E402
import build_d4_d10 as M  # noqa: E402
import check_d4_d10_independent as CHK  # noqa: E402  (the plain-loop checker; compared with the builder on invented data)

T0 = time.time()
ROOT = M.ROOT


def make_design(did, pf, scale_mode, K, n_draws, rng, att_scale=1.0, att_shift=None, sigma=None, fams=5, sid_fmt="s{:02d}", used=None):
    sigma = np.ones(K) if sigma is None else np.asarray(sigma, float)
    placebo = rng.normal(0, 1, (K, n_draws)) * sigma[:, None]
    att = rng.normal(0, 1, K) * sigma * att_scale
    if att_shift is not None:
        att = att + np.asarray(att_shift, float)
    sids = [sid_fmt.format(k) for k in range(K)]
    fam = [f"fam{k % fams}" for k in range(K)]
    sd_all = placebo.std(axis=1, ddof=1)
    cut = np.array([np.sort(np.abs(placebo[k]))[math.ceil(0.95 * n_draws) - 1] for k in range(K)])
    used = sids if used is None else used
    return M.design_from_arrays(did, pf, scale_mode, list(range(2000, 2012)), ["AA", "BB", "CC"], fam, sids, ["SUPPORTED"] * K, att, placebo, sd_all, cut, used)


def make_designs(n=12, K=10, n_draws=300, seed=1, att_scale=1.0, row_scale=None, col_shift=None, pair=None, scale_modes=None):
    rng = np.random.default_rng(seed)
    designs = {}
    for i in range(n):
        shift = None
        if col_shift is not None:
            shift = np.array(col_shift, float)
        rs = 1.0 if row_scale is None else row_scale[i]
        d = make_design(f"d{i:02d}", f"pf{i % 6}", (scale_modes or {}).get(i, "rate"), K, n_draws, rng, att_scale=att_scale * rs, att_shift=shift)
        if pair:
            for (di, k), eff in pair.items():
                if di == i:
                    d.att[k] += eff
                    d.z[k] = d.att[k] / d.sd[k]
                    d.rho[k] = abs(d.att[k]) / d.c[k]
        designs[d.id] = d
    return designs


def plain_inputs(designs, view):
    """The plain-loop representation of the cell vectors."""
    obs_of, rep_of = {}, {}
    for did, d in designs.items():
        if view == "series":
            for j, s in enumerate(d.sids):
                obs_of[(did, s)] = float(abs(d.z[j]))
                rep_of[(did, s)] = [float(v) for v in d.Zabs[j]]
        else:
            for f in sorted(set(d.fam)):
                rows = [j for j, x in enumerate(d.fam) if x == f]
                obs_of[(did, f)] = CHK.mean_([abs(float(d.z[j])) for j in rows])
                rep_of[(did, f)] = [CHK.mean_([float(d.Zabs[j][b]) for j in rows]) for b in range(d.n)]
    return obs_of, rep_of


class TestD4(unittest.TestCase):
    def test_dec_stats_against_plain_and_balanced_identity(self):
        rng = np.random.default_rng(7)
        nr, nc = 6, 7
        keep = [(r, c) for r in range(nr) for c in range(nc) if rng.random() < 0.7]
        row = np.array([r for r, _ in keep])
        col = np.array([c for _, c in keep])
        x = rng.normal(0, 1, len(keep))
        got = M.dec_stats(x, row, col, nr, nc)
        want = CHK.plain_dec(list(x), list(row), list(col), nr, nc)
        for k in want:
            self.assertAlmostEqual(float(got[k][0]), want[k], places=12)
        # balanced: the four parts add up exactly
        row = np.array([r for r in range(nr) for c in range(nc)])
        col = np.array([c for r in range(nr) for c in range(nc)])
        x = rng.normal(0, 1, nr * nc)
        g = M.dec_stats(x, row, col, nr, nc)
        self.assertAlmostEqual(float(g["A_x"][0]), 0.0, places=12)
        self.assertAlmostEqual(float(g["A_row"][0] + g["A_col"][0] + g["A_res"][0]), float(g["V_tot"][0]), places=12)
        # a pure row effect is all A_row
        x = np.array([1.0 * r for r in row])
        g = M.dec_stats(x, row, col, nr, nc)
        self.assertAlmostEqual(float(g["A_res"][0]), 0.0, places=12)
        self.assertAlmostEqual(float(g["A_col"][0]), 0.0, places=12)
        self.assertGreater(float(g["A_row"][0]), 1.0)

    def test_prune(self):
        pairs = [(r, c) for r in range(6) for c in range(6)] + [("lonely", 0)] + [(0, "thin")]
        self.assertEqual(M.prune(pairs), M.prune(CHK.plain_prune(pairs)))
        self.assertEqual(M.prune(pairs), CHK.plain_prune(pairs))
        self.assertNotIn(("lonely", 0), M.prune(pairs))
        self.assertNotIn((0, "thin"), M.prune(pairs))
        # a cascade: removing a thin column makes a row thin
        cascade = [(0, c) for c in range(5)] + [(r, c) for r in range(1, 7) for c in range(4)] + [(7, 4)]
        out = M.prune(cascade, 5, 5)
        self.assertEqual(out, CHK.plain_prune(cascade, 5, 5))

    def run_d4(self, designs, R=300, view="series", seed=3, loo=True):
        ids, idx = M.replicate_index(designs, [20261011, 44, seed], R=R)
        kap = M.OrderedDict([("raw", 1.0), ("calibrated", 1.3), ("kappa_lo", 1.1), ("kappa_hi", 1.5), ("kappa_strict", 1.2)])
        res = M.d4_run(designs, M.build_cellvec(designs, view), ids, idx, kap, R=R, loo=loo)
        return res, ids, idx, kap

    def test_end_to_end_against_plain_loops(self):
        designs = make_designs(n=10, K=8, n_draws=200, seed=5, att_scale=1.5)
        res, ids, idx, kap = self.run_d4(designs, R=120)
        self.assertEqual(res["code"], "EVAL")
        obs_of, rep_of = plain_inputs(designs, "series")
        pairs = CHK.plain_prune(list(obs_of))
        des_j = {d: j for j, d in enumerate(ids)}
        obs, null, des, cols = CHK.plain_d4_core(pairs, obs_of, rep_of, des_j, idx.tolist(), dict(kap), 120)
        nm, levels = CHK.plain_levels(obs, null, dict(kap), 120)
        for k in obs:
            self.assertAlmostEqual(res["observed"][k], obs[k], places=11)
            self.assertAlmostEqual(res["null_mean"][k], nm[k], places=11)
        for lab in kap:
            for k in M.PARTS:
                self.assertAlmostEqual(res["levels"][lab]["excess"][k], levels[lab]["excess"][k], places=11)
                self.assertAlmostEqual(res["levels"][lab]["p_one_sided"][k], levels[lab]["p_one_sided"][k], places=12)
            self.assertEqual(res["levels"][lab]["label"], levels[lab]["label"])
        # leave-one-policy-family-out at the calibrated kappa against plain loops
        loo = {b["policy_family_removed"]: b for b in res["leave_one_policy_family_out"]["blocks"]}
        fam_of = {d: designs[d].policy_family for d in designs}
        for f in sorted(set(fam_of.values())):
            keep = CHK.plain_prune([p for p in pairs if fam_of[p[0]] != f])
            if not keep:
                self.assertEqual(loo[f]["code"], "EMPTY")
                continue
            des2, cols2 = sorted({r for r, _ in keep}), sorted({c for _, c in keep})
            row, col = [des2.index(r) for r, _ in keep], [cols2.index(c) for _, c in keep]
            o2 = CHK.plain_dec([obs_of[p] for p in keep], row, col, len(des2), len(cols2))
            null2 = {k: [] for k in o2}
            for r in range(120):
                st = CHK.plain_dec([rep_of[p][idx[r][des_j[p[0]]]] for p in keep], row, col, len(des2), len(cols2))
                for k in o2:
                    null2[k].append(st[k])
            for k in M.PARTS:
                e = o2[k] - 1.3 ** 2 * CHK.mean_(null2[k])
                self.assertAlmostEqual(loo[f]["excess"][k], e, places=11)

    def test_synthetic_null_has_no_excess(self):
        ps, ex = [], []
        for seed in (11, 12, 13, 14):
            designs = make_designs(n=14, K=10, n_draws=300, seed=seed)
            res, *_ = self.run_d4(designs, R=300, seed=seed, loo=False)
            lv = res["levels"]["raw"]
            ps.append(min(lv["p_one_sided"].values()))
            tot = res["levels"]["raw"]["excess"]["V_tot"] / res["null_mean"]["V_tot"]
            ex.append(abs(tot))
        self.assertGreater(min(ps), 0.003)
        self.assertLess(max(ex), 0.3)

    def test_planted_parts(self):
        n, K = 14, 10
        # policy part: half of the designs have effects three times as wide
        row_scale = [3.0 if i % 2 == 0 else 1.0 for i in range(n)]
        res, *_ = self.run_d4(make_designs(n=n, K=K, seed=21, row_scale=row_scale), loo=False)
        self.assertIn("POLICY", res["levels"]["raw"]["label"])
        self.assertLess(res["levels"]["raw"]["p_one_sided"]["A_row"], 0.01)
        # outcome part: two series have a common shift in every design
        shift = np.zeros(K)
        shift[[1, 6]] = [4.0, -4.0]
        res, *_ = self.run_d4(make_designs(n=n, K=K, seed=22, col_shift=shift), loo=False)
        self.assertIn("OUTCOME", res["levels"]["raw"]["label"])
        self.assertLess(res["levels"]["raw"]["p_one_sided"]["A_col"], 0.01)
        # pair part: sparse large effects on single cells
        pair = {(i, (3 * i) % K): 6.0 for i in range(n)}
        res, *_ = self.run_d4(make_designs(n=n, K=K, seed=23, pair=pair), loo=False)
        self.assertIn("PAIR", res["levels"]["raw"]["label"])
        self.assertLess(res["levels"]["raw"]["p_one_sided"]["A_res"], 0.01)

    def test_calibration_removes_a_pure_scale_excess(self):
        designs = make_designs(n=14, K=10, n_draws=400, seed=31, att_scale=1.4)
        res, *_ = self.run_d4(designs, R=400, loo=False)
        raw, cal = res["levels"]["raw"], res["levels"]["kappa_hi"]
        tot_raw = res["observed"]["V_tot"] - res["null_mean"]["V_tot"]
        tot_cal = res["observed"]["V_tot"] - 1.5 ** 2 * res["null_mean"]["V_tot"]
        self.assertGreater(tot_raw, 0.05)
        self.assertLess(abs(tot_cal), abs(tot_raw))
        self.assertGreater(raw["excess"]["A_res"], 0.0)

    def test_family_view_runs(self):
        designs = make_designs(n=10, K=10, seed=41, n_draws=200)
        res, *_ = self.run_d4(designs, R=100, view="family", loo=False)
        self.assertEqual(res["code"], "EVAL")
        self.assertEqual(res["columns"], 5)
        self.assertEqual(res["cells"], 50)

    def test_below_floor(self):
        designs = make_designs(n=4, K=10, seed=51, n_draws=200)
        res, *_ = self.run_d4(designs, R=50, loo=False)
        self.assertEqual(res["code"], "BELOW_FLOOR")


class TestD5(unittest.TestCase):
    def test_values_and_p_against_plain(self):
        rng = np.random.default_rng(3)
        a, s = rng.normal(0, 2, 9), rng.uniform(0.5, 1.5, 9)
        for kap in (1.0, 1.3):
            got = M.d5_values(a, s, kap)
            want = CHK.plain_d5_values(list(a), list(s), kap)
            for k in ("V", "N", "tau2", "tau", "H", "rms2", "a_bar"):
                self.assertAlmostEqual(got[k], want[k], places=12)
        Vstar = rng.uniform(0, 5, 99)
        self.assertAlmostEqual(M.d5_pvalue(2.5, Vstar, 1.0), (1 + sum(1 for v in Vstar if v >= 2.5 - 1e-12)) / 100)

    def test_bootstrap_against_plain(self):
        rng = np.random.default_rng(4)
        a, s = rng.normal(0, 2, 10), rng.uniform(0.5, 1.5, 10)
        fam_idx = np.array([0, 0, 1, 1, 1, 2, 2, 3, 3, 3])
        V, N, dropped = M.d5_bootstrap(a, s, fam_idx, 4, np.random.default_rng(9), 500)
        Vp, Np, dropped_p = CHK.plain_d5_boot(list(a), list(s), list(fam_idx), 4, np.random.default_rng(9), 500)
        self.assertEqual(dropped, dropped_p)
        np.testing.assert_allclose(V, Vp, rtol=1e-11)
        np.testing.assert_allclose(N, Np, rtol=1e-11)

    def make_keys(self, true_sd, m=14, seed=0, n_draws=300):
        rng = np.random.default_rng(seed)
        designs = {}
        for i in range(m):
            d = make_design(f"d{i:02d}", f"pf{i % 5}", "rate", 1, n_draws, rng, sid_fmt="only")
            d.att = np.array([rng.normal(0, true_sd) + d.att[0]])
            designs[d.id] = d
        return designs

    def run_d5(self, designs, R=400):
        ids, idx = M.replicate_index(designs, [20261011, 45, 7], R=R)
        kap = M.OrderedDict([("raw", 1.0), ("calibrated", 1.3), ("kappa_lo", 1.1), ("kappa_hi", 1.5)])
        return M.d5_run(designs, ids, idx, kap, set(), {}, {}, R=R, B_=300), kap

    def test_known_spread_is_recovered(self):
        res, _ = self.run_d5(self.make_keys(true_sd=3.0, seed=61))
        row = res["series"][0]
        self.assertEqual(row["code"], "EVAL")
        self.assertTrue(row["levels"]["raw"]["beyond_noise"])
        self.assertTrue(row["levels"]["calibrated"]["beyond_noise"])
        self.assertGreater(row["levels"]["raw"]["tau"], 2.0)
        self.assertLess(row["levels"]["raw"]["tau"], 4.0)

    def test_null_has_no_spread(self):
        flags = 0
        for seed in range(6):
            res, _ = self.run_d5(self.make_keys(true_sd=0.0, seed=70 + seed))
            flags += int(res["series"][0]["levels"]["raw"]["beyond_noise"])
        self.assertLessEqual(flags, 2)

    def test_end_to_end_against_plain(self):
        designs = self.make_keys(true_sd=1.0, seed=81)
        res, kap = self.run_d5(designs, R=200)
        row = res["series"][0]
        members = [(d, 0) for d in sorted(designs)]
        a = [float(designs[d].att[0]) for d, _ in members]
        s = [float(designs[d].sd[0]) for d, _ in members]
        for lab, k in kap.items():
            want = CHK.plain_d5_values(a, s, k)
            for fld in ("V", "N", "tau2", "tau", "H"):
                self.assertAlmostEqual(row["levels"][lab][fld], want[fld], places=11)

    def test_below_floor_and_strata(self):
        designs = self.make_keys(true_sd=1.0, m=4, seed=91)
        res, _ = self.run_d5(designs, R=50)
        self.assertEqual(res["below_floor"][0]["code"], "BELOW_FLOOR")
        self.assertEqual(res["series"], [])
        designs = self.make_keys(true_sd=1.0, m=14, seed=92)
        ids, idx = M.replicate_index(designs, [20261011, 45, 7], R=60)
        kap = M.OrderedDict([("raw", 1.0), ("calibrated", 1.3)])
        reg = {d: {"only"} for d in designs}
        res = M.d5_run(designs, ids, idx, kap, set(), reg, {}, R=60, B_=100)
        self.assertEqual(res["series"][0]["stratum"], "REGISTERED")
        self.assertIn("registered_subset", res["series"][0])
        nc = {(d, "fam0"): True for d in designs}
        res = M.d5_run(designs, ids, idx, kap, set(), {}, nc, R=60, B_=100)
        self.assertEqual(res["series"][0]["stratum"], "NC-DOMINATED")

    def test_series_key_scale_version(self):
        d_count, d_rate = make_design("a", "pf", "count", 1, 50, np.random.default_rng(1), sid_fmt="x"), make_design("b", "pf", "rate", 1, 50, np.random.default_rng(1), sid_fmt="x")
        self.assertEqual(M.series_key(d_count, "x", {"x"}), "x[count]")
        self.assertEqual(M.series_key(d_count, "x", set()), "x")
        self.assertEqual(M.series_key(d_rate, "x", {"x"}), "x")


class TestD6(unittest.TestCase):
    def test_scale_stats_against_plain(self):
        rng = np.random.default_rng(5)
        val = rng.normal(10, 3, (6, 8))
        ok = rng.random((6, 8)) < 0.8
        uidx, yidx = [0, 2, 3, 5], [1, 2, 4, 5, 7]
        N, mean, sdw = M.scale_stats(val, ok, uidx, yidx)
        N2, mean2, sdw2 = CHK.plain_scale(val, ok, uidx, yidx)
        self.assertEqual(N, N2)
        self.assertAlmostEqual(mean, mean2, places=12)
        self.assertAlmostEqual(sdw, sdw2, places=12)
        self.assertEqual(M.scale_stats(val, np.zeros((6, 8), bool), uidx, yidx), (0, None, None))
        val_c = np.ones((6, 8)) * 5
        self.assertIsNone(M.scale_stats(val_c, np.ones((6, 8), bool), uidx, yidx)[2])

    def test_class_rule(self):
        self.assertEqual([M.mde_class(v) for v in (0.25, 0.26, 0.5, 0.51, 1.0, 1.01, None)], ["FINE", "MODERATE", "MODERATE", "COARSE", "COARSE", "BLIND", None])

    def test_run_on_invented_stores(self):
        rng = np.random.default_rng(6)
        units, years = ["AA", "BB", "CC"], list(range(2000, 2012))
        sids = [f"s{k:02d}" for k in range(4)]
        store = {"units": np.array(units), "years": np.array(years)}
        for sid in sids:
            store[sid + "__val"] = rng.normal(20, 4, (3, 12))
            store[sid + "__ok"] = np.ones((3, 12), bool)
        designs = {}
        for i in range(4):
            d = make_design(f"d{i}", f"pf{i}", "rate", 4, 200, rng)
            d.years, d.units = years, units
            d.all_codes = ["SUPPORTED", "SUPPORTED", "SUPPORTED", "NO_DESIGN"] if i < 2 else ["SUPPORTED", "NO_DESIGN", "SUPPORTED", "NO_DESIGN"]
            designs[d.id] = d
        kap = M.OrderedDict([("raw", 1.0), ("calibrated", 1.3)])
        fam = {sid: f"fam{k % 5}" for k, sid in enumerate(sids)}
        res = M.d6_run(designs, (store, store), set(), kap, sids, fam)
        keys = {r["key"]: r for r in res["series"]}
        self.assertEqual(sorted(keys), ["s00", "s02"])         # s01 supported twice only (below the floor of 3), s03 never
        self.assertEqual([b["key"] for b in res["keys_below_floor"]], ["s01"])
        self.assertEqual([u["series_id"] for u in res["unsupported_series"]], ["s03"])
        r = keys["s00"]
        mde = [d.all_cutoff[0] + M.MDE_Z * d.all_sd[0] for d in designs.values()]
        self.assertAlmostEqual(r["mde"]["median"], float(np.median(mde)), places=12)
        self.assertAlmostEqual(r["levels"]["calibrated"]["mde_median"], 1.3 * r["mde"]["median"], places=12)
        want_class = M.mde_class(1.3 * r["mde_sd"]["median"])
        self.assertEqual(r["levels"]["calibrated"]["class"], want_class)


class TestD7(unittest.TestCase):
    def test_data_type_rule_on_the_real_strings(self):
        rows = M.S.inventory_rows()
        types = [M.data_type(r["unit_of_observation"]) for r in rows if r["row_type"] == "usage" and r["layer"] == "replication_paper" and r["unit_kind"] == "paper"]
        self.assertEqual(len(types), 42)
        self.assertEqual({t: types.count(t) for t in set(types)}, {"STATE": 23, "MICRO": 12, "SUB-STATE": 7})
        self.assertEqual([CHK.plain_dtype(r["unit_of_observation"]) for r in rows if r["row_type"] == "usage" and r["layer"] == "replication_paper" and r["unit_kind"] == "paper"], types)
        with self.assertRaises(ValueError):
            M.data_type("galaxy")

    def test_clopper_pearson(self):
        lo, hi = M.clopper_pearson(3, 10)
        self.assertAlmostEqual(lo, 0.06673951, places=6)
        self.assertAlmostEqual(hi, 0.65245285, places=6)
        lo, hi = M.clopper_pearson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 1 - 0.025 ** 0.1, places=8)
        self.assertEqual(M.clopper_pearson(10, 10)[1], 1.0)
        self.assertIsNone(M.clopper_pearson(0, 0))
        for x, n in ((1, 7), (5, 36), (7, 36)):
            a, b = M.clopper_pearson(x, n), CHK.plain_cp(x, n)
            self.assertAlmostEqual(a[0], b[0], places=10)
            self.assertAlmostEqual(a[1], b[1], places=10)

    def papers(self, outcome_of_group):
        out = []
        for g, n in (("fa", 12), ("fb", 12), ("fc", 12)):
            for i in range(n):
                out.append(dict(unit_id=f"{g}{i:02d}", family=g, unit_of_observation="state-year" if g == "fa" else "county-year" if g == "fb" else "individual",
                                status=outcome_of_group(g, i), policy_group="p"))
        return out

    def test_separated_groups_are_found(self):
        res = M.d7_run(self.papers(lambda g, i: "matched" if (g == "fa" and i < 10) else "not_consistent"), R=999)
        self.assertEqual(res["family"]["tests"]["strict"]["label"], "DIFFERS")
        self.assertEqual(res["family"]["tests"]["strict"]["highest"], "fa")
        self.assertLess(res["data_type"]["tests"]["strict"]["p"], 0.01)

    def test_unseparated_groups_and_floor(self):
        res = M.d7_run(self.papers(lambda g, i: "matched" if i % 4 == 0 else "not_consistent"), R=999)
        self.assertEqual(res["family"]["tests"]["strict"]["label"], "NOT SEPARATED")
        res = M.d7_run(self.papers(lambda g, i: "not_comparable" if i < 10 else "matched")[:30], R=99)
        self.assertEqual(res["family"]["tests"]["strict"]["code"], "BELOW_FLOOR")

    def test_permutation_p_against_plain_loop(self):
        papers = self.papers(lambda g, i: "matched" if ((ord(g[-1]) * 7 + i * 3) % 5 == 0) else "directional" if i % 7 == 0 else "not_consistent")
        res = M.d7_run(papers, R=199)
        comp = sorted([p for p in papers if p["status"] != "not_comparable"], key=lambda p: p["unit_id"])
        names = sorted({p["family"] for p in comp})
        gid = [names.index(p["family"]) for p in comp]
        y = [1.0 if p["status"] in ("matched", "confirmed") else 0.0 for p in comp]
        T = CHK.plain_T(y, gid, len(names))
        rng = np.random.default_rng([20261011, 47, 1])
        cnt = 0
        for _ in range(199):
            Ts = CHK.plain_T([float(v) for v in rng.permutation(np.array(y))], gid, len(names))
            cnt += int(Ts is not None and CHK.ge(Ts, T))
        self.assertAlmostEqual(res["family"]["tests"]["strict"]["T"], T, places=12)
        self.assertAlmostEqual(res["family"]["tests"]["strict"]["p"], (1 + cnt) / 200, places=12)


class TestD8(unittest.TestCase):
    KAP = M.OrderedDict([("raw", 1.0), ("calibrated", 1.3)])

    def cell(self, rng, shift=0.0, noise=1.0, n=600):
        """n placebo draws and one observed pair drawn from the same joint distribution (the null of random timing), plus an optional shift of series 2."""
        base = rng.normal(0, 1, n + 1)
        Q1 = base + rng.normal(0, noise, n + 1)
        Q2 = base + rng.normal(0, noise, n + 1)
        P1, P2 = Q1[:n], Q2[:n]
        sd1, sd2 = P1.std(ddof=1), P2.std(ddof=1)
        a1, a2 = Q1[n], Q2[n] + shift * sd2
        c1, c2 = np.sort(np.abs(P1))[math.ceil(0.95 * n) - 1], np.sort(np.abs(P2))[math.ceil(0.95 * n) - 1]
        return (a1 / sd1, a2 / sd2, P1, P2, sd1, sd2, abs(a1) / c1, abs(a2) / c2)

    def test_identical_twins(self):
        P = np.random.default_rng(1).normal(0, 1, 100)
        out = M.twin_cell(0.7, 0.7, P, P.copy(), P.std(ddof=1), P.std(ddof=1), 0.5, 0.5, self.KAP)
        self.assertEqual(out["code"], "IDENTICAL")

    def test_null_rate_and_shift(self):
        rng = np.random.default_rng(2)
        beyond, r = [], []
        for _ in range(300):
            c = M.twin_cell(*self.cell(rng), self.KAP)
            self.assertEqual(c["code"], "EVAL")
            beyond.append(c["beyond"]["raw"])
            r.append(c["r"])
        self.assertAlmostEqual(sum(r) / len(r), 0.05, delta=0.005)
        self.assertLess(abs(sum(beyond) / len(beyond) - 0.05), 0.04)
        big = M.twin_cell(*self.cell(rng, shift=8.0), self.KAP)
        self.assertTrue(big["beyond"]["raw"] and big["beyond"]["calibrated"])

    def test_against_plain_loops(self):
        rng = np.random.default_rng(3)
        args = self.cell(rng, shift=1.5, n=400)
        a = M.twin_cell(*args, self.KAP)
        b = CHK.plain_twin(args[0], args[1], list(args[2]), list(args[3]), args[4], args[5], args[6], args[7], dict(self.KAP))
        for k in ("sd_u", "U", "line", "r", "d_abs"):
            self.assertAlmostEqual(a[k], b[k], places=11)
        self.assertEqual(a["beyond"], b["beyond"])
        self.assertEqual(a["flip"], b["flip"])

    def test_run_tiers_on_invented_designs(self):
        rng = np.random.default_rng(4)
        designs = {}
        s1, s2 = "wonder_homicide_rate", "fbi_ucr_homicide_rate"
        for i in range(10):
            placebo = rng.normal(0, 1, (2, 400))
            placebo[1] = placebo[0] + rng.normal(0, 0.8, 400)
            att = rng.normal(0, 1, 2)
            sd_all = placebo.std(axis=1, ddof=1)
            cut = np.array([np.sort(np.abs(placebo[k]))[math.ceil(0.95 * 400) - 1] for k in range(2)])
            d = M.design_from_arrays(f"d{i:02d}", f"pf{i % 6}", "rate", [2000, 2001], ["AA"], ["crime_justice"] * 2, [s1, s2], ["SUPPORTED"] * 2, att, placebo, sd_all, cut, [s1, s2])
            designs[d.id] = d
        res = M.d8_run(designs, self.KAP)
        self.assertEqual(res["pairs"]["A1"]["cells"], 10)
        self.assertEqual(res["pairs"]["A3"]["code"], "NO_PAIR_CELL")
        tier = res["tiers"]["A"]
        self.assertEqual(tier["n"], 10)
        self.assertTrue(tier["engine_floor"]["met"])
        self.assertIn("E1", tier["levels"]["raw"])
        self.assertIn(tier["levels"]["calibrated"]["label"], ("TWINS DIFFER", "AT THE LINE", "TWINS CLOSER THAN RANDOM TIMING"))
        # E1 against the plain loop on the same y
        ev = [c for c in res["cells"] if c["pair"] == "A1" and c["code"] == "EVAL"]
        y = [float(c["beyond"]["raw"]) - c["r"] for c in ev]
        plain = CHK.plain_e1(y, [c["policy_family"] for c in ev], [20261011, 48], 9999)
        self.assertAlmostEqual(tier["levels"]["raw"]["E1"]["se"], plain["se"], places=11)
        self.assertAlmostEqual(tier["levels"]["raw"]["E1"]["interval95"][0], plain["interval95"][0], places=10)


class TestD9(unittest.TestCase):
    def test_break_years_from_catalog_fields(self):
        c1 = {"series_breaks": [[2011, "a"], [2012, "b"]]}
        c2 = {"series_breaks": [{"note": "x", "year": 2005}, {"note": "y", "year": 2008}]}
        c3 = {"series_break": {"year": 1999, "type": "t"}}
        c4 = {"definition_changes": ["1999-2017 bridged-race, 2018-2024 single-race D158"]}
        c5 = {"definition_changes": ["1999–2017 bridged-race D76 and 2018–2024 single-race D158 use disjoint years"]}
        c6 = {"definition_changes": ["no seam here"]}
        for c, sid, want in ((c1, "x", [2011, 2012]), (c2, "x", [2005, 2008]), (c3, "x", [1999]), (c4, "x", [2018]), (c5, "x", [2018]), (c6, "x", []), ({}, "wonder_infant_mortality_rate", [2003, 2007])):
            self.assertEqual(M.break_years(c, sid), want)
            self.assertEqual(CHK.plain_break_years(c, sid), want)

    def test_exposure_and_groups(self):
        post = [(2010, 2010, 2009), (2010, 2011, 2009), (2010, 2012, 2009), (2012, 2012, 2011), (2012, 2013, 2011)]
        years = set(range(2005, 2020))
        phi, n = M.exposure(post, years, [2012])
        self.assertEqual(n, 5)
        self.assertAlmostEqual(phi, 3 / 5)          # cells (2010, 2012, base 2009), (2012, 2012, base 2011), (2012, 2013, base 2011) have base < 2012 <= t
        self.assertAlmostEqual(M.exposure(post, years, [2011])[0], 2 / 5)
        self.assertEqual(M.exposure(post, years, [2025])[0], 0.0)
        self.assertEqual(M.exposure(post, set(), [2012]), (None, 0))
        self.assertEqual(M.exposure(post, {2009, 2010, 2012, 2013}, [2012]), (0.5, 2))             # cells with t or base period outside the years leave
        self.assertEqual([M.exposure_group(v) for v in (0.0, 0.2, 0.5, 1.0)], ["NOT SPANNED", "BETWEEN", "SPANNED", "SPANNED"])
        self.assertAlmostEqual(CHK.plain_exposure(post, years, [2011]), 2 / 5)

    def strata(self, effect, seed, n_series=6, n_each=12):
        rng = np.random.default_rng(seed)
        cells, k0, strata = [], 0, []
        for s in range(n_series):
            idx = []
            for i in range(n_each):
                spanned = bool(i % 2 == 0)
                p = 0.05 + (effect if spanned else 0.0)
                cells.append((spanned, float(rng.random() < p)))
                idx.append(k0)
                k0 += 1
            strata.append(np.array(idx))
        labels = np.array([c[0] for c in cells])
        y = np.array([c[1] for c in cells])
        return labels, y, strata

    def test_permutation_detects_an_effect_and_matches_plain(self):
        labels, y, strata = self.strata(0.5, 1)
        obs, p = M.d9_permutation({"raw": y}, labels, strata, ["raw"], R=999)
        self.assertGreater(obs["raw"], 0.2)
        self.assertLess(p["raw"], 0.02)
        rng = np.random.default_rng([20261011, 49, 1])
        cnt = 0
        for _ in range(999):
            lab = labels.copy()
            for s in strata:
                perm = rng.permutation(len(s))
                lab[s] = labels[s][perm]
            ds = CHK.plain_delta(list(y), list(lab))
            cnt += int(CHK.ge(abs(ds), abs(obs["raw"])))
        self.assertAlmostEqual(p["raw"], (1 + cnt) / 1000, places=12)

    def test_permutation_null_is_not_small(self):
        ps = []
        for seed in range(8):
            labels, y, strata = self.strata(0.0, 100 + seed)
            ps.append(M.d9_permutation({"raw": y}, labels, strata, ["raw"], R=299)[1]["raw"])
        self.assertLess(sum(1 for p in ps if p <= 0.05), 3)

    def test_bootstrap_against_plain(self):
        labels, y, strata = self.strata(0.4, 2)
        blocks = [f"pf{i % 5}" for i in range(len(y))]
        out = M.d9_bootstrap({"raw": y}, labels, blocks, ["raw"], B_=300)
        names = sorted(set(blocks))
        draw = np.random.default_rng([20261011, 49, 2]).integers(0, len(names), size=(300, len(names)))
        deltas = []
        for row in draw:
            cnt = [0] * len(names)
            for f in row:
                cnt[f] += 1
            nS = sum(cnt[names.index(b)] for b, l in zip(blocks, labels) if l)
            nN = sum(cnt[names.index(b)] for b, l in zip(blocks, labels) if not l)
            sS = sum(cnt[names.index(b)] * v for b, l, v in zip(blocks, labels, y) if l)
            sN = sum(cnt[names.index(b)] * v for b, l, v in zip(blocks, labels, y) if not l)
            if nS and nN:
                deltas.append(sS / nS - sN / nN)
        lo, hi = np.percentile(deltas, [2.5, 97.5])
        self.assertAlmostEqual(out["raw"]["interval95"][0], float(lo), places=12)
        self.assertAlmostEqual(out["raw"]["interval95"][1], float(hi), places=12)
        self.assertEqual(out["raw"]["defined"], len(deltas))


class TestD10(unittest.TestCase):
    def rows(self):
        def usage(layer, unit_kind, **kw):
            base = {k: "" for k in ("row_type", "layer", "unit_kind", "status", "role", "policy_group", "family", "outcome_key", "catalog_series_id", "data_state",
                                    "n_texts_db", "n_texts_db_state", "n_texts_db_shortlist")}
            base.update(row_type="usage", layer=layer, unit_kind=unit_kind)
            base.update(kw)
            return base
        rows = [dict(usage("x", ""), row_type="variable", layer="connector_catalog", outcome_key="s1", family="crime_justice", data_state="on_disk_parsed"),
                dict(usage("x", ""), row_type="variable", layer="connector_catalog", outcome_key="s2", family="mortality", data_state="adapter_ready_public_download"),
                usage("l2_design", "design", status="estimated", role="primary", policy_group="g1", family="crime_justice", outcome_key="s1"),
                usage("l2_design", "design", status="queued_card", role="primary", policy_group="g2", family="mortality", outcome_key="s2"),
                usage("replication_paper", "paper", policy_group="g2", family="education", status="matched")]
        for g, f, a, st, sh in (("g1", "crime_justice", 10, 8, 2), ("g1", "mortality", 20, 15, 3), ("g1", "other", 30, 20, 5), ("g2", "education", 40, 30, 4), ("g2", "business_innovation", 50, 25, 1)):
            rows.append(usage("policy_db_text", "db_text_group", policy_group=g, family=f, n_texts_db=str(a), n_texts_db_state=str(st), n_texts_db_shortlist=str(sh)))
        return rows

    def test_status_and_shares(self):
        res, out = M.d10_run(self.rows())
        status = {(r["policy_group"], r["family"]): r["status"] for r in out}
        self.assertEqual(status[("g1", "crime_justice")], "RESULT")
        self.assertEqual(status[("g1", "mortality")], "NEITHER")          # mortality has no series on disk (its catalog row is adapter-ready only)
        self.assertEqual(status[("g2", "education")], "RESULT")           # a re-estimated paper counts as a result
        self.assertEqual(status[("g2", "business_innovation")], "NEITHER")
        sh = res["overall"]["state_law_texts"]
        self.assertEqual(sh["mentions"], 8 + 15 + 20 + 30 + 25)
        self.assertAlmostEqual(sh["RESULT"]["share"], (8 + 30) / 98)
        self.assertAlmostEqual(sh["NEITHER"]["share"], (15 + 20 + 25) / 98)
        self.assertEqual(res["label"], "MOSTLY OUTSIDE OUR DATA")
        wo = res["overall_without_other"]["state_law_texts"]
        self.assertEqual(wo["mentions"], 78)
        self.assertEqual(res["calibration"], "NOT_APPLICABLE")


class TestSupplement1(unittest.TestCase):
    def test_poisson_binomial_against_enumeration(self):
        import itertools
        ps = [0.05, 0.1, 0.02, 0.3, 0.07, 0.05]
        dist = M.poisson_binomial(ps)
        want = [0.0] * (len(ps) + 1)
        for bits in itertools.product([0, 1], repeat=len(ps)):
            pr = 1.0
            for b, p in zip(bits, ps):
                pr *= p if b else 1 - p
            want[sum(bits)] += pr
        np.testing.assert_allclose(dist, want, rtol=1e-12)
        self.assertAlmostEqual(sum(dist), 1.0, places=12)
        r = M.exact_reading(3, ps)
        self.assertAlmostEqual(r["p_upper"], sum(want[3:]), places=12)
        self.assertAlmostEqual(r["p_lower"], sum(want[:4]), places=12)
        self.assertAlmostEqual(r["expected"], sum(ps), places=12)
        self.assertEqual(M.exact_reading(0, [0.05] * 10)["label"], "AT THE LINE")
        self.assertEqual(M.exact_reading(5, [0.05] * 10)["label"], "TWINS DIFFER")
        self.assertEqual(M.exact_reading(0, [0.3] * 12)["label"], "TWINS CLOSER THAN RANDOM TIMING")

    def test_d8_degenerate_flag_and_label_rule(self):
        rng = np.random.default_rng(14)
        designs = {}
        s1, s2 = "wonder_homicide_rate", "fbi_ucr_homicide_rate"
        for i in range(10):
            placebo = rng.normal(0, 1, (2, 400))
            placebo[1] = placebo[0] + rng.normal(0, 0.8, 400)
            att = rng.normal(0, 0.2, 2)               # small estimates: no twin difference beyond the line
            sd_all = placebo.std(axis=1, ddof=1)
            cut = np.array([np.sort(np.abs(placebo[k]))[math.ceil(0.95 * 400) - 1] for k in range(2)])
            d = M.design_from_arrays(f"d{i:02d}", f"pf{i % 6}", "rate", [2000, 2001], ["AA"], ["crime_justice"] * 2, [s1, s2], ["SUPPORTED"] * 2, att, placebo, sd_all, cut, [s1, s2])
            designs[d.id] = d
        res = M.d8_run(designs, TestD8.KAP)
        lv = res["tiers"]["A"]["levels"]["raw"]
        self.assertTrue(lv["E1_degenerate"])
        self.assertEqual(lv["label"], lv["exact"]["label"])
        self.assertIn("label_E1", lv)
        self.assertEqual(lv["exact"]["n"], 10)

    def test_fe_residualize_and_delta_against_least_squares(self):
        rng = np.random.default_rng(15)
        nd, ns = 8, 6
        cells = [(i, k) for i in range(nd) for k in range(ns) if rng.random() < 0.75]
        d_id = np.array([i for i, _ in cells])
        s_id = np.array([k for _, k in cells])
        alpha, beta = rng.normal(0, 1, nd), rng.normal(0, 1, ns)
        S_ = (rng.random(len(cells)) < 0.4).astype(float)
        y = alpha[d_id] + beta[s_id] + 0.7 * S_ + rng.normal(0, 0.3, len(cells))
        X = np.zeros((len(cells), nd + ns - 1 + 1))
        X[np.arange(len(cells)), d_id] = 1.0
        for r, k in enumerate(s_id):
            if k > 0:
                X[r, nd + k - 1] = 1.0
        X[:, -1] = S_
        beta_hat = np.linalg.lstsq(X, y, rcond=None)[0]
        self.assertAlmostEqual(M.delta_fe(y, S_, d_id, s_id), float(beta_hat[-1]), places=8)
        # S absorbed by the design effects: undefined
        S_abs = (d_id % 2 == 0).astype(float)
        self.assertIsNone(M.delta_fe(y, S_abs, d_id, s_id))
        # the residuals are orthogonal to both sets of dummies
        r = M.fe_residualize(y, d_id, s_id, nd, ns)
        for i in range(nd):
            self.assertAlmostEqual(float(r[d_id == i].sum()), 0.0, places=8)
        for k in range(ns):
            self.assertAlmostEqual(float(r[s_id == k].sum()), 0.0, places=8)

    def test_record_merges_the_supplement(self):
        rec = M.parse_record()
        self.assertIn("PREREG_AMEND_20261015_D4_D10_HASH_S1.txt", rec["supplements"])
        self.assertIn(M.S.rel(M.AMEND_S1), rec["files"])
        with tempfile.TemporaryDirectory() as td:
            s1 = Path(td) / "s1.md"
            s1.write_text(M.AMEND_S1.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            probs = M.verify_records(rec, s1_path=s1)
            self.assertTrue(any("differs from the hash record" in p or "not in the hash record" in p for p in probs))


class TestRecordsAndDefinitions(unittest.TestCase):
    def test_hash_record_is_consistent_and_catches_changes(self):
        self.assertEqual(M.verify_records(), [])
        rec = M.parse_record()
        self.assertEqual(sorted(rec["entries"]), sorted(M.QUESTIONS))
        with tempfile.TemporaryDirectory() as td:
            q = Path(td) / "q.md"
            text = M.AMEND_Q.read_text(encoding="utf-8")
            q.write_text(text.replace("Is the size of an estimated policy effect", "Is the size of an estimated policy effect XX"), encoding="utf-8")
            probs = M.verify_records(rec, q_path=q)
            self.assertTrue(any("entry D4 differs" in p for p in probs))
            self.assertTrue(any("differs from the hash record" in p for p in probs))
            q.write_text(text.replace("**Question.** For an outcome that many policies", "**Question.** (changed) For an outcome that many policies"), encoding="utf-8")
            self.assertTrue(any("entry D5 differs" in p for p in M.verify_records(rec, q_path=q)))

    def test_entry_extraction(self):
        text = "## D4. a\nbody\n## D5. b\nmore\n## 2. What stays\nx\n"
        self.assertEqual(M.entry_text(text, "D4"), "## D4. a\nbody\n")
        self.assertEqual(M.entry_text(text, "D5"), "## D5. b\nmore\n")
        self.assertIsNone(M.entry_text(text, "D6"))

    def test_registered_tables_equal_the_code_tables(self):
        breaks, pairs = M.parse_doc_tables()
        self.assertEqual([tuple(p) for p in pairs], [tuple(p) for p in M.PAIRS])
        self.assertEqual(len(breaks), 29)
        cat = M.S.read_json(M.S.CATALOG)["series"]
        for sid, years in breaks.items():
            self.assertEqual(M.break_years(cat[sid], sid), years)

    def test_cell_definitions_equal_cal_design_arrays_on_one_real_design(self):
        path = ROOT / "data/l3_meta/sweep_v1/c18_siegel_mcdv.npz"
        f = D1.load_design_file(path)
        st = D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], ())
        arr = CAL.design_arrays(f, st)
        z = f["z"]
        d = M.design_from_arrays("c18_siegel_mcdv", "x", "rate", f["meta"]["years"], f["meta"]["units"], f["fam"], f["sids"], f["code"], f["att"], f["placebo"],
                                 np.asarray(z["sd"], float), np.asarray(z["cutoff"], float), st["series_used"])
        self.assertEqual(d.sids, arr["sids"])
        np.testing.assert_allclose(d.z, arr["z"], rtol=1e-13)
        np.testing.assert_allclose(d.c, arr["own_cut"], rtol=1e-13)
        np.testing.assert_allclose(np.abs(d.att), arr["abs_att"], rtol=1e-13)
        self.assertEqual(d.n, arr["n"])

    def test_write_once(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "R.json"
            M.write_once(p, "a")
            with self.assertRaises(SystemExit):
                M.write_once(p, "b")
            self.assertEqual(p.read_text(), "a")
            c = Path(td) / "sub" / "t.csv"
            M.write_csv_once(c, ["a", "b"], [{"a": 1, "b": None}])
            with self.assertRaises(SystemExit):
                M.write_csv_once(c, ["a", "b"], [{"a": 1, "b": 2}])
            self.assertEqual(c.read_text().splitlines(), ["a,b", "1,"])

    def test_replicate_index_rule(self):
        designs = make_designs(n=3, K=2, n_draws=50, seed=2)
        ids, idx = M.replicate_index(designs, [1, 2, 3], R=500)
        U = np.random.default_rng([1, 2, 3]).random((500, 3))
        for j, d in enumerate(ids):
            n = designs[d].n
            np.testing.assert_array_equal(idx[:, j], np.minimum(np.floor(U[:, j] * n).astype(int), n - 1))
        self.assertTrue((idx >= 0).all() and (idx < 50).all())

    def test_no_pycache_under_the_l2_scripts(self):
        newer = [p for p in (ROOT / "scripts/l2_pipeline").rglob("__pycache__") if p.stat().st_mtime > T0]
        self.assertEqual(newer, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
