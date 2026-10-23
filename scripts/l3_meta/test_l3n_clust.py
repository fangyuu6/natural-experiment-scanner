import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import build_l3n_clust as C


def grid_adj(n):
    # path graph 0-1-2-...-(n-1)
    A = np.zeros((n, n))
    for i in range(n - 1):
        A[i, i + 1] = A[i + 1, i] = 1
    return A


class T(unittest.TestCase):
    def test_join_count(self):
        A = grid_adj(5)
        X = np.array([[1, 1, 1, 0, 0], [1, 0, 1, 0, 1]], float)
        self.assertEqual(list(C.join_counts(X, A)), [2, 0])

    def test_known_positive(self):
        n, k = 40, 10
        A = grid_adj(n)
        lab = np.zeros(n); lab[:k] = 1
        J = lab @ A @ lab / 2
        z, _, _ = C.zscore(J, C.join_counts(C.draws_U(n, k, 4000, np.random.default_rng(1)), A))
        self.assertGreater(z, 5)

    def test_known_null(self):
        n, k, zs = 40, 10, []
        A = grid_adj(n)
        rng = np.random.default_rng(2)
        for _ in range(200):
            lab = np.zeros(n); lab[rng.choice(n, k, replace=False)] = 1
            J = lab @ A @ lab / 2
            zs.append(C.zscore(J, C.join_counts(C.draws_U(n, k, 1000, rng), A))[0])
        self.assertLess(abs(np.mean(zs)), 0.25)
        self.assertAlmostEqual(np.std(zs), 1.0, delta=0.25)

    def test_p5_blocks_preserve_counts(self):
        idx = {i: float(i % 7) + 0.01 * i for i in range(23)}
        blocks = C.make_blocks(idx)
        self.assertEqual(sum(len(b) for b in blocks), 23)
        self.assertTrue(all(len(b) >= 4 for b in blocks))
        lab = np.zeros(23); lab[[0, 3, 9, 14]] = 1
        X = C.draws_P5(lab, blocks, 50, np.random.default_rng(3))
        for blk in blocks:
            self.assertTrue(np.all(X[:, blk].sum(1) == lab[blk].sum()))

    def test_spearman(self):
        self.assertAlmostEqual(C.spearman([1, 2, 3, 4], [2, 4, 6, 9]), 1.0)
        self.assertAlmostEqual(C.spearman([1, 2, 3, 4], [9, 6, 4, 2]), -1.0)

    def test_contag_positive_and_null(self):
        m = 12
        Tadj = grid_adj(m)
        c0 = np.arange(m, dtype=float)  # cohorts spread along path: every later state has earlier neighbour
        obs = C.contag_stat(c0[None, :], None, Tadj)[0]
        self.assertEqual(obs, 1.0)
        perm = np.argsort(np.random.default_rng(4).random((3000, m)), axis=1)
        self.assertLess(C.contag_stat(c0[perm], None, Tadj).mean(), 0.95)

    def test_link_detects_and_null(self):
        rng = np.random.default_rng(5)
        fams = [f"f{i}" for i in range(30)]
        x = rng.normal(size=30)
        r = C.link(x, x + 0.1 * rng.normal(size=30), fams, 499, "t1")
        self.assertEqual(r["verdict"], "LINK")
        r = C.link(x, rng.normal(size=30), fams, 499, "t2")
        self.assertEqual(r["verdict"], "NO LINK DETECTED")


if __name__ == "__main__":
    unittest.main()
