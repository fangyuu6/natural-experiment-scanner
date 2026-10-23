"""Synthetic tests of m1_core / build_x1 (no real sweep data are read)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import m1_core as M  # noqa: E402


def fake_data(nd=12, K=6, seed=0, rho=0.0):
    rng = np.random.default_rng(seed)
    data = {}
    cov = np.full((K, K), rho) + (1 - rho) * np.eye(K)
    Lc = np.linalg.cholesky(cov)
    for d in range(nd):
        pl = (Lc @ rng.normal(size=(K, 999))) * rng.uniform(0.5, 2.0, (K, 1))
        data[f"d{d}"] = dict(series=[f"s{i}" for i in range(K)], family=["a", "a", "b", "b", "c", "c"][:K], code=["SUPPORTED"] * K, placebo=pl)
    return data


class T(unittest.TestCase):
    def test_reader_refuses_real_keys(self):
        with self.assertRaises(KeyError):
            M.read_placebo_only("/nonexistent", keys=("att",))

    def test_loader_touches_only_placebo_keys(self):
        seen = []

        def reader(path, keys=M.PLACEBO_KEYS):
            seen.append(tuple(keys))
            return dict(series_ids=["s0"], family=["a"], code=["SUPPORTED"], placebo=np.zeros((1, 999)))
        M.load_null("U", reader=reader)
        self.assertTrue(all(set(k) <= {"series_ids", "family", "code", "placebo"} for k in seen))

    def test_meff_bounds(self):
        self.assertAlmostEqual(M.m_eff(np.eye(5)), 5.0)
        self.assertAlmostEqual(M.m_eff(np.ones((5, 5))), 1.0)

    def test_null_c_is_centred_and_R_recovers_rho(self):
        data = fake_data(rho=0.5)
        uni = [f"s{i}" for i in range(6)]
        rows = M.design_arrays(data, uni)
        R, n = M.mean_R(rows, min_designs=5)
        off = R[~np.eye(6, dtype=bool)]
        self.assertAlmostEqual(float(off.mean()), 0.5, delta=0.05)
        Z, Rd, ds = M.replicate_z(rows, list(range(6)), 400, np.random.default_rng(1))
        C, cnt = M.c_matrix(Z, Rd)
        self.assertLess(abs(float(np.nanmean(C[:, ~np.eye(6, dtype=bool)]))), 0.05)
        e = M.eigen_desc(C)
        self.assertEqual(e.shape, (400, 6))

    def test_label_permutation_gives_zero(self):
        data = fake_data(rho=0.6)
        uni = [f"s{i}" for i in range(6)]
        rows = M.design_arrays(data, uni, shuffle_rng=np.random.default_rng(3))
        R, _ = M.mean_R(rows, min_designs=5)
        self.assertLess(float(np.nanmax(np.abs(R[~np.eye(6, dtype=bool)]))), 0.1)



class StageOne(unittest.TestCase):
    def test_run_one_synthetic(self):
        import build_m1 as B
        data = fake_data(nd=26, K=6, rho=0.3, seed=2)
        real = {}
        rng = np.random.default_rng(9)
        for d, v in data.items():
            sd = np.nanstd(v["placebo"], axis=1, ddof=1)
            real[d] = (rng.normal(size=6) * sd, sd)
        uni = [f"s{i}" for i in range(6)]
        rows = M.design_arrays(data, uni)
        rows["_universe"] = uni
        fam = dict(zip(uni, ["a", "a", "b", "b", "c", "c"]))
        fm = {d: f"p{i % 8}" for i, d in enumerate(data)}
        r = B.run_one(uni, "U", rows, data, real, fam, 300, boot=True, design_family=fm, perm=True)
        self.assertTrue(r["feasible"])
        self.assertEqual(len(r["eigen_observed"]), 6)
        self.assertGreaterEqual(r["k"], 0)
        self.assertEqual(r["bootstrap_family_block"]["n_resamples"], B.B_BOOT)
        r2 = B.run_one(uni, "U", rows, data, real, fam, 300, variant="abs")
        self.assertTrue(r2["feasible"])



class M2Parts(unittest.TestCase):
    def test_slopes_and_run(self):
        import build_m2 as B2
        rng = np.random.default_rng(4)
        nd, K = 24, 10
        g = rng.normal(size=nd)
        Z = rng.normal(size=(1, nd, K)) + 0.8 * g[None, :, None] * (np.arange(K) < 3)[None, None, :]
        Zn = rng.normal(size=(300, nd, K))
        b = B2.slopes(Z, g, 15)
        self.assertGreater(b[0, 0], 0.4)
        self.assertLess(abs(b[0, 8]), 0.6)
        masks = {"all": None, "none": rng.random((nd, K)) < 0.5, "channel": rng.random((nd, K)) < 0.3}
        series = [f"s{i}" for i in range(K)]
        fam = {s: ("a" if i < 5 else "b") for i, s in enumerate(series)}
        ds = [f"d{i}" for i in range(nd)]
        res = B2.run(Z, Zn, g, rng.normal(size=nd), masks, series, fam, {d: f"f{i % 6}" for i, d in enumerate(ds)}, [i % 3 for i in range(nd)], ds, 20, np.random.default_rng(1), False)
        self.assertGreater(res["T_all"]["observed"], res["T_all"]["null_mean"])
        self.assertIn("family_mean_slope", res)



class X6Parts(unittest.TestCase):
    def test_gap_and_beta(self):
        import x6_core as X
        rng = np.random.default_rng(2)
        n, ny = 30, 40
        val = rng.normal(size=(n, ny))
        ok = np.ones((n, ny), bool)
        adopt = np.where(np.arange(n) < 10, 20.0, np.inf)
        val[:10, 19] += 2.0                                  # treated units are high the year before adoption
        A = np.vstack([adopt[None, :], adopt[rng.permutation(n)][None, :]])
        G = X.cohort_gaps(val, ok, A, 1)
        self.assertGreater(G[0], 1.0)
        self.assertLess(abs(G[1]), 1.5)
        nc = 200
        sidx = np.repeat(np.arange(10), 20)
        Gc = rng.normal(size=(1, nc))
        Zc = -0.5 * Gc + rng.normal(size=(1, nc))
        b = X.within_beta(Zc, Gc, sidx, 10)
        self.assertLess(b[0], -0.3)
        W = np.ones((1, nc))
        self.assertAlmostEqual(float(X.within_beta(Zc, Gc, sidx, 10, W=W)[0]), float(b[0]), places=9)


if __name__ == "__main__":
    unittest.main()
