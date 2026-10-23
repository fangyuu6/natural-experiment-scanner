"""Synthetic tests of build_x2 pieces."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_x2 as B  # noqa: E402


class T(unittest.TestCase):
    def test_halo_and_cosine(self):
        rng = np.random.default_rng(1)
        v = np.array([1, 1, -1, -1, 0, 0] * 4, float)
        Z = rng.normal(size=(1, 20, 24)) + 0.7 * v[None, None, :]
        H, Wd, conc, n = B.halo_stats(Z, v)
        self.assertGreater(H[0], 0.5)
        self.assertGreater(conc[0], 0.7)
        Zn = rng.normal(size=(200, 20, 24))
        Hn = B.halo_stats(Zn, v)[0]
        self.assertLess(abs(np.mean(Hn)), 0.05)
        self.assertAlmostEqual(B.cosine(np.array([1.0, 0, 0]), np.array([2.0, 0, 0])), 1.0)

    def test_perm_within(self):
        v = np.array([1, 1, -1, 0, 1, -1], float)
        fam = ["a", "a", "a", "b", "b", "b"]
        p = B.perm_within(v, fam, np.random.default_rng(2))
        self.assertEqual(sorted(p[:3]), sorted(v[:3]))
        self.assertEqual(sorted(p[3:]), sorted(v[3:]))


if __name__ == "__main__":
    unittest.main()
