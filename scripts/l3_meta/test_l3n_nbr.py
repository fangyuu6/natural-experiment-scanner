"""Synthetic tests of build_l3n_nbr (statistic on a known null and a known positive; graph relabel; permutation)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_l3n_nbr as B  # noqa: E402


def simulate(b_true, seed, n_states=30, n_designs=8, years=25):
    """Ring-graph states; each design: hazard = base + b_true * share of neighbours already in force."""
    rng = np.random.default_rng(seed)
    A = np.zeros((n_states, n_states))
    for i in range(n_states):
        A[i, (i + 1) % n_states] = A[(i + 1) % n_states, i] = 1
    rows = []
    for d in range(n_designs):
        adopted = np.zeros(n_states, bool)
        for t in range(years):
            share = (A @ adopted) / A.sum(1)
            h = 1 / (1 + np.exp(-(-3.2 + b_true * share)))
            ev = (~adopted) & (rng.random(n_states) < h)
            for s in np.flatnonzero(~adopted):
                rows.append((f"d{d}", f"f{d // 2}", s, 2000 + t, share[s], int(ev[s])))
            adopted |= ev
    des = np.array([r[0] for r in rows]); fam = np.array([r[1] for r in rows])
    return des, fam, np.array([r[2] for r in rows]), np.array([r[3] for r in rows]), np.array([r[4] for r in rows]), np.array([r[5] for r in rows], float), A


def fit(b_true, seed):
    des, fam, st, yr, nb, y, A = simulate(b_true, seed)
    covs = np.column_stack([nb, np.zeros(len(y)), np.zeros(len(y)), np.random.default_rng(1).normal(size=len(y)), np.random.default_rng(2).normal(size=len(y)), np.zeros(len(y))])
    return B.fit_m1(des, yr, y, covs, np.arange(2000, 2025), fam), des, yr, y, covs, fam


class T(unittest.TestCase):
    def test_known_positive(self):
        r, *_ = fit(3.0, 11)
        self.assertGreater(r["b"], 1.5)
        self.assertGreater(r["b"] / r["se"], 3)

    def test_known_null(self):
        bs = [fit(0.0, s)[0] for s in range(21, 27)]
        self.assertLess(abs(np.mean([x["b"] for x in bs])), 0.7)
        self.assertLess(np.mean([abs(x["b"] / x["se"]) for x in bs]), 2.5)

    def test_permutation_null_centred(self):
        r, des, yr, y, covs, fam = fit(3.0, 31)
        g = B.pd.factorize(B.pd.Series(des) + "|" + B.pd.Series(yr).astype(str))[0]
        rng = np.random.default_rng(5)
        yp = B.permute_within(y, g, rng)
        self.assertEqual(int(yp.sum()), int(y.sum()))
        for k in np.unique(g)[:20]:
            self.assertEqual(yp[g == k].sum(), y[g == k].sum())
        nulls = []
        for i in range(6):
            yp = B.permute_within(y, g, np.random.default_rng(100 + i))
            nulls.append(B.fit_m1(des, yr, yp, covs, np.arange(2000, 2025), fam, se=False)["b"])
        self.assertLess(abs(np.mean(nulls)), r["b"] / 2)

    def test_relabel_keeps_degrees(self):
        A = np.zeros((6, 6))
        for i, j in [(0, 1), (1, 2), (2, 3), (3, 4), (0, 2)]:
            A[i, j] = A[j, i] = 1
        B2 = B.relabel(A, np.random.default_rng(3), np.arange(5))
        self.assertEqual(sorted(A.sum(1)), sorted(B2.sum(1)))
        self.assertTrue((B2 == B2.T).all())
        self.assertEqual(B2[5].sum(), 0)

    def test_rebuilt_share(self):
        import pandas as pd
        tl = pd.DataFrame(dict(design_id=["d"] * 4, state=["A", "B", "C", "D"], kind=["adopter", "left_censored", "never", "unknown"],
                               onset_operative_year=[2005, np.nan, np.nan, np.nan]))
        F, K = B.build_status(tl, ["d"], ["A", "B", "C", "D"])
        A = np.zeros((4, 4))
        for i in (0, 1, 3):
            A[2, i] = A[i, 2] = 1                    # C borders A, B, D
        yi = np.array([2005 - B.Y0, 2006 - B.Y0])
        s = B.rebuilt_share(A, F, K, np.zeros(2, int), np.array([2, 2]), yi)
        self.assertAlmostEqual(s[0], 0.5)            # A not yet (onset 2005 not < 2005), B yes, D unknown -> 1/2
        self.assertAlmostEqual(s[1], 1.0)

    def test_dl(self):
        r = B.dersimonian_laird([1.0, 1.0, 1.0], [0.1, 0.1, 0.1])
        self.assertAlmostEqual(r["mu"], 1.0)
        self.assertAlmostEqual(r["tau"], 0.0)
        r = B.dersimonian_laird([0.0, 2.0, -1.0, 3.0], [0.01] * 4)
        self.assertGreater(r["tau"], 1.0)


if __name__ == "__main__":
    unittest.main()
