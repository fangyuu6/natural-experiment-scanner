import sys, unittest
from pathlib import Path
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_n11 as M


def synth(slope, n=30, seed=1):
    rng = np.random.default_rng(seed)
    D = []
    for i in range(n):
        A = {(f"S{s}", y) for s in range(rng.integers(3, 12)) for y in range(2000 + int(rng.integers(0, 8)), 2020)}
        D.append(dict(id=f"d{i}", family=f"f{i}", w=(2000, 2019), A=A, z=np.zeros(40), sids=[]))
    O, _, N, WIN = M.pair_tables([dict(d, z=np.ones(40)) for d in D])
    # build z so that E[z_i z_j] = slope * O_ij via shared factor model
    S = np.eye(n) + slope * O
    w_, V = np.linalg.eigh(S)
    L = V @ np.diag(np.sqrt(np.clip(w_, 1e-6, None)))
    Z = L @ rng.standard_normal((n, 40))
    for i in range(n):
        D[i]["z"] = Z[i]
    return D


class T(unittest.TestCase):
    def test_wls_exact(self):
        o = np.array([0, .2, .4, .6, .8]); c = 0.1 + 0.5 * o
        a, b = M.wls(c, o, np.ones(5)); self.assertAlmostEqual(a, .1); self.assertAlmostEqual(b, .5)

    def test_degenerate(self):
        self.assertIsNone(M.wls(np.ones(5), np.ones(5), np.ones(5)))

    def test_jaccard(self):
        D = [dict(id="a", family="x", w=(2000, 2019), A={("A", 2005), ("B", 2005)}, z=np.ones(30), sids=[]),
             dict(id="b", family="y", w=(2000, 2019), A={("A", 2005), ("C", 2005)}, z=np.ones(30), sids=[])]
        O, C, N, WIN = M.pair_tables(D)
        self.assertAlmostEqual(O[0, 1], 1 / 3); self.assertEqual(N[0, 1], 30); self.assertEqual(WIN[0, 1], 20)

    def test_not_evaluable(self):
        D = synth(0.0, n=5)
        O, C, N, WIN = M.pair_tables(D)
        self.assertEqual(M.estimate(D, O, C, N, WIN, B=50)["verdict"], "NOT_EVALUABLE")

    def test_null_and_signal(self):
        for slope, want in ((0.0, {"NOT_DETECTED", "INCONCLUSIVE"}), (3.0, {"DETECTED"})):
            D = synth(slope)
            O, C, N, WIN = M.pair_tables(D)
            r = M.estimate(D, O, C, N, WIN, B=499)
            self.assertIn(r["verdict"], want, r)


if __name__ == "__main__":
    unittest.main()
