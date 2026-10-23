"""Synthetic tests of build_m4 (no real data)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_m4 as B  # noqa: E402


class T(unittest.TestCase):
    def test_profile_corr_and_center(self):
        rng = np.random.default_rng(1)
        Z = rng.normal(size=(6, 40))
        Zt = B.center(Z)
        self.assertLess(abs(np.nanmean(Zt, axis=0)).max(), 1e-12)
        r, k = B.profile_corr(Zt, 0, 0)
        self.assertAlmostEqual(r, 1.0)
        self.assertEqual(k, 40)

    def test_infer_recovers_same_group_effect(self):
        rng = np.random.default_rng(2)
        n = 20
        groups = [f"g{i % 5}" for i in range(n)]
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        I = np.array([p[0] for p in pairs])
        J = np.array([p[1] for p in pairs])
        same = np.array([groups[a] == groups[b] for a, b in pairs], float)
        St = 0.3 * same + rng.normal(0, 0.1, len(pairs))
        O = rng.random(len(pairs))
        Dg = rng.random(len(pairs))
        w = np.full(len(pairs), 30.0)
        out = B.infer(pairs, St, None, O, Dg, w, groups, n, np.random.default_rng(3), np.random.default_rng(4), 200, 200, True)
        self.assertAlmostEqual(out["coef"][1], 0.3, delta=0.05)
        self.assertGreater(out["bootstrap_same_group_ci95"][0], 0.0)
        self.assertLess(out["permutation_p_one_sided"], 0.05)


if __name__ == "__main__":
    unittest.main()
