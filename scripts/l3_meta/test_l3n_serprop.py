"""Checks of build_l3n_serprop.py on INVENTED data (no repo data is read).
Run:  .venv_did/bin/python -I -B -m unittest scripts/l3_meta/test_l3n_serprop.py  (or run this file)"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_l3n_serprop as S  # noqa: E402


def make(seed, effect=0.0, G=20, per=40, K=3):
    rng = np.random.default_rng(seed)
    N = G * per
    design = np.repeat(np.arange(G), per)
    X = rng.normal(size=(N, K))
    fe = rng.normal(size=G)[design] * 3
    y = fe + effect * X[:, 0] + rng.normal(size=N)
    return design, X, y


class T(unittest.TestCase):
    def test_demean(self):
        d = np.array([0, 0, 1, 1]); M = np.array([1., 3., 10., 20.])
        self.assertTrue(np.allclose(S.demean(M, d), [-1, 1, -5, 5]))

    def test_known_coefficient(self):
        d, X, y = make(1, effect=2.0)
        B, V = S.fe_cluster(y, X, d, d)
        self.assertAlmostEqual(B[0, 0], 2.0, delta=0.15)
        w = S.wald(B, V, [0, 1, 2])[0]
        self.assertGreater(w, 50)

    def test_known_null_wald_rate(self):
        rej = 0
        crit = 7.81  # chi2(3) 5%
        reps = 200
        for s in range(reps):
            d, X, y = make(100 + s)
            B, V = S.fe_cluster(y, X, d, d)
            rej += S.wald(B, V, [0, 1, 2])[0] > crit
        self.assertLess(rej / reps, 0.25)  # 20 clusters: cluster Wald over-rejects a little; the placebo reference corrects it

    def test_p_upper(self):
        self.assertAlmostEqual(S.p_upper(5.0, [1, 2, 3, 4]), 1 / 5)
        self.assertAlmostEqual(S.p_upper(0.0, [1, 2, 3, 4]), 1.0)

    def test_placebo_reference_null_vs_positive(self):
        d, X, y = make(7, effect=0.0)
        rng = np.random.default_rng(3)
        Yrep = np.column_stack([rng.normal(size=len(y)) + (S.demean(np.zeros(len(y)), d)) for _ in range(300)])
        B, V = S.fe_cluster(np.column_stack([y, Yrep]), X, d, d)
        w = S.wald(B, V, [0, 1, 2])
        p_null = S.p_upper(w[0], w[1:])
        d2, X2, y2 = make(7, effect=1.0)
        B2, V2 = S.fe_cluster(np.column_stack([y2, Yrep]), X2, d2, d2)
        w2 = S.wald(B2, V2, [0, 1, 2])
        p_pos = S.p_upper(w2[0], w2[1:])
        self.assertGreater(p_null, 0.01)
        self.assertLessEqual(p_pos, 1 / 301 + 1e-9)

    def test_perm_bundles_within_family(self):
        kb = {"f1": ["a", "b", "c"], "f2": ["d", "e"]}
        bundle = {k: np.array([i]) for i, k in enumerate("abcde")}
        out = S.perm_bundles(kb, bundle, np.random.default_rng(0))
        self.assertEqual(sorted(float(out[k][0]) for k in "abc"), [0, 1, 2])
        self.assertEqual(sorted(float(out[k][0]) for k in "de"), [3, 4])

    def test_provider_gap(self):
        prov = np.array(["A"] * 6 + ["B"] * 6)
        beyond = np.array([1, 1, 1, 0, 0, 0] + [0] * 6, float)
        series = np.array(list("abcdef") + list("ghijkl"))
        g = S.provider_gap(beyond, prov, 3, series) if False else None
        S.PROVIDERS_BAK = S.PROVIDERS
        S.PROVIDERS = ["A", "B"]
        try:
            g = S.provider_gap(beyond, prov, 3, series)
        finally:
            S.PROVIDERS = S.PROVIDERS_BAK
        self.assertEqual(g[0], "A"); self.assertAlmostEqual(g[1], 0.5)


if __name__ == "__main__":
    unittest.main()
