"""Checks of build_l3n_repeat.py on INVENTED data (no repo data file is read).
Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_l3n_repeat.py   (or -m unittest)
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_l3n_repeat as R  # noqa: E402


class T(unittest.TestCase):
    def test_draw_T_inside_U_and_size(self):
        rng = np.random.default_rng(1)
        M = rng.random((6, 20)) < 0.7
        ts = np.minimum(M.sum(1), [3, 4, 5, 2, 1, 6])
        X = R.draw_T(M, ts, 50, rng)
        self.assertFalse((X & ~M).any())
        self.assertTrue((X.sum(2) == ts[None, :]).all())

    def test_tau_known(self):
        x = np.array([[1., 2, 3, 4]])
        t, g = R.tau_gamma(x, x)
        self.assertAlmostEqual(t[0], 1.0)
        t, g = R.tau_gamma(x, -x)
        self.assertAlmostEqual(t[0], -1.0)
        t, g = R.tau_gamma(np.array([[1., 1, 2, 3]]), np.array([[1., 2, 3, 4]]))
        self.assertLess(t[0], 1.0)
        self.assertAlmostEqual(g[0], 1.0)

    def test_midrank(self):
        self.assertEqual(R.scaled_midrank(np.array([5., 5, 9])).tolist(), [0.25, 0.25, 1.0])

    def test_share_variance_null_vs_positive(self):
        rng = np.random.default_rng(2)
        K, S = 40, 30
        M = np.ones((K, S), bool)
        # null: random sets
        X0 = R.draw_T(M, np.full(K, 8), 1, rng)[0]
        null = R.share_variance(R.draw_T(M, np.full(K, 8), 2000, rng), M, 10)[0]
        self.assertGreater(R.one_sided_p(R.share_variance(X0, M, 10)[0], null), 0.05)
        # positive: the same 8 states always treated
        X1 = np.zeros((K, S), bool)
        X1[:, :8] = True
        self.assertLess(R.one_sided_p(R.share_variance(X1, M, 10)[0], null), 0.001)

    def test_svd_null_vs_positive(self):
        rng = np.random.default_rng(3)
        K, S = 30, 25
        M = np.ones((K, S), bool)
        null = R.first_svd(R.draw_T(M, np.full(K, 8), 1000, rng), M, 10)[0]
        X0 = R.draw_T(M, np.full(K, 8), 1, rng)[0]
        self.assertGreater(R.one_sided_p(R.first_svd(X0, M, 10)[0], null), 0.05)
        X1 = np.zeros((K, S), bool)
        for k in range(K):                       # two design types adopt in opposite state blocks (a latent axis; column centring removes pure propensity)
            hi = (np.arange(S) < 10) if k % 2 == 0 else (np.arange(S) >= 10)
            X1[k] = rng.random(S) < np.where(hi, 0.8, 0.1)
        r, v, keep = R.first_svd(X1, M, 10)
        null1 = R.first_svd(R.draw_T(M, X1.sum(1), 1000, rng), M, 10)[0]
        self.assertLess(R.one_sided_p(r, null1), 0.01)
        sign = np.where(np.arange(S) < 10, 1.0, 0.0)  # loading sign splits the two blocks
        self.assertGreater(abs(R.spearman_rows(v[None, :], sign)[0]), 0.7)

    def test_concordance_null_and_positive(self):
        rng = np.random.default_rng(4)
        n = 12
        base = rng.permutation(n).astype(float)
        # positive: designs share the order up to small noise
        Ys = {k: np.tile(base + rng.normal(0, 0.5, n), (1, 1)) for k in "abcd"}
        idx = [(p, q, list(range(n)), list(range(n))) for p, q in [("a", "b"), ("a", "c"), ("b", "d")]]
        tau, _ = R.pair_stats(Ys, idx)
        self.assertGreater(np.nanmean(tau), 0.7)
        # null: independent orders
        taus = []
        for _ in range(300):
            Y2 = {k: rng.permutation(n).astype(float)[None, :] for k in "ab"}
            taus.append(R.pair_stats(Y2, [("a", "b", list(range(n)), list(range(n)))])[0][0, 0])
        self.assertLess(abs(np.mean(taus)), 0.05)

    def test_family_boot_and_holm(self):
        rng = np.random.default_rng(5)
        fams = [("f1", "f2"), ("f1", "f3"), ("f2", "f3")] * 5
        b = R.family_boot_diff(np.full(15, 0.3), np.zeros(15), fams, ["f1", "f2", "f3"], 200, rng)
        self.assertTrue(np.allclose(b[~np.isnan(b)], 0.3))
        self.assertEqual(R.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])


if __name__ == "__main__":
    unittest.main()
