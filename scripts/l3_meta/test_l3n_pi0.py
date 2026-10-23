"""Synthetic tests of build_l3n_pi0 statistics."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_l3n_pi0 as B  # noqa: E402


class T(unittest.TestCase):
    def test_known_null(self):
        rng = np.random.default_rng(1)
        k = 1.31
        pis = [B.storey_pi1(rng.standard_normal(1000) * k, k) for _ in range(50)]
        self.assertLess(np.mean(pis), 0.05)          # boundary-capped null
        z = rng.standard_normal(5000) * k
        self.assertLess(abs(B.central(z)[1] - k), 0.06)
        self.assertLess(B.tau2(z, k), 0.15)
        w = B.npmle(z, k, iters=800)
        self.assertLess(B.g_masses(w)["g_gt1"], 0.15)

    def test_known_positive(self):
        rng = np.random.default_rng(2)
        k = 1.31
        n = 2000
        z = rng.standard_normal(n) * k
        idx = rng.choice(n, 400, replace=False)
        z[idx] += 3.0 * rng.choice([-1, 1], 400)
        self.assertLess(abs(B.storey_pi1(z, k) - 0.20), 0.08)
        g = B.g_masses(B.npmle(z, k, iters=1500))
        self.assertGreater(g["g_gt1"], 0.12)
        self.assertGreater(B.tau2(z, k), 1.0)
        d0, sc, T = B.central(z)
        self.assertGreater(T, 0.05 * n)

    def test_variance_inflation_is_not_a_tail(self):
        rng = np.random.default_rng(3)
        z = rng.standard_normal(4000) * 1.6          # wider body, no movers
        st = B.all_stats(z, 1.0, with_npmle=False)
        self.assertAlmostEqual(st["s_c"], 1.6, delta=0.08)
        self.assertLess(abs(st["tail_part"]), 0.4)   # V mostly inflation
        self.assertLess(st["T"], 0.05 * 4000 * 1.3)

    def test_npmle_weights_sum(self):
        w = B.npmle(np.random.default_rng(4).standard_normal(300), 1.0, iters=200)
        self.assertAlmostEqual(float(w.sum()), 1.0, places=9)

    def test_boot_runs(self):
        rng = np.random.default_rng(5)
        z = rng.standard_normal(60)
        r = B.boot(z, list(np.repeat(np.arange(12), 5)), 1.0, 30, rng, False, 0)
        self.assertLessEqual(r["pi1"]["hi"], 1.0)
        self.assertGreaterEqual(r["pi1"]["lo"], 0.0)


if __name__ == "__main__":
    unittest.main()
