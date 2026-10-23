import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402
import build_l3n_noiselaw as B  # noqa: E402


def fake(rng, law=-0.5, effect=0.0, n_des=24, n_ser=6, ndraw=300):
    geom, cells = {}, []
    for d in range(n_des):
        T = int(rng.integers(5, 40))
        geom[f"d{d}"] = dict(family=f"f{d % 12}", T=T, C=int(rng.integers(2, 10)), U=int(rng.integers(40, 51)),
                             L=int(rng.integers(35, 50)), era=2000.0 + d)
        for s in range(n_ser):
            sd = np.exp(s * 0.3 + law * np.log(T) + 0.01 * rng.normal())
            pl = rng.normal(0, 1, ndraw)
            z = rng.normal(0, 1) + (effect / np.sqrt(T) * 6 if effect else 0.0)
            cells.append(dict(design=f"d{d}", series=f"s{s}", src="sweep", sd=sd, cutoff=1.96 * sd, z=float(z), n_units=45, pz=pl))
    return geom, cells


class T(unittest.TestCase):
    def test_noise_law_recovered(self):
        rng = np.random.default_rng(1)
        g, c = fake(rng, law=-0.5)
        a = B.part_a(g, c, rng, n_perm=200, n_boot=200)
        self.assertAlmostEqual(a["four_var_sd"]["lnT"], -0.5, delta=0.05)
        self.assertEqual(a["verdict_sqrt_law"], "consistent with the square-root law")

    def test_other_law_detected(self):
        rng = np.random.default_rng(2)
        g, c = fake(rng, law=-1.0)
        a = B.part_a(g, c, rng, n_perm=200, n_boot=200)
        self.assertEqual(a["verdict_sqrt_law"], "steeper than sqrt")
        self.assertLess(a["perm_b_T"]["p_vs_zero"], 0.05)

    def test_b_null(self):
        rng = np.random.default_rng(103)
        g, c = fake(rng, effect=0.0)
        # null: real z drawn like placebo, so excess slopes should straddle 0
        b = B.part_b(g, c, rng, n_boot=300)
        lo, hi = b["estimates"]["joint:lnT"]["excess_ci95"]
        self.assertTrue(lo <= 0 <= hi)
        self.assertEqual(b["verdict"]["joint:lnT"], "no detectable excess")

    def test_b_positive(self):
        rng = np.random.default_rng(4)
        g, c = fake(rng, effect=3.0, n_des=40)
        # real |z| larger for small T: strong geometry-linked excess
        for cell in c:
            T = g[cell["design"]]["T"]
            cell["z"] = float(3.0 * 12.0 / T + abs(rng.normal()))
        b = B.part_b(g, c, rng, n_boot=300)
        e = b["estimates"]["joint:lnT"]
        self.assertLess(e["excess"], 0)
        self.assertLess(e["excess_ci95"][1], 0)
        self.assertEqual(b["verdict"]["joint:lnT"], "geometry explains detection beyond the mechanical slope")

    def test_fe_demean(self):
        fe = B.FE(np.array([0, 0, 1, 1, 1]))
        d = fe.demean(np.array([1.0, 3.0, 2.0, 2.0, 5.0]))
        self.assertAlmostEqual(d[0], -1.0)
        self.assertAlmostEqual(d[4], 2.0)

    def test_rank_ties(self):
        self.assertEqual(B.rank([1, 2, 2, 3]).tolist(), [0.0, 1.5, 1.5, 3.0])


if __name__ == "__main__":
    unittest.main()
