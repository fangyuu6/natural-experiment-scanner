import sys, unittest
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import n6_trend_blind_spot as N


class T(unittest.TestCase):
    def test_reference(self):
        self.assertEqual(N.reference([-4, -3, -2]), -1)
        self.assertEqual(N.reference([-3, -2, -1]), 0)

    def test_select_nearest(self):
        idx, r = N.select_leads([-6, -5, -4, -3, -2], 4)
        self.assertEqual([[-6, -5, -4, -3, -2][i] for i in idx], [-2, -3, -4, -5]); self.assertEqual(r, -1)

    def test_wald_one_lead_matches_closed_form(self):
        # df=1, h=-1, V=1: detectable gamma at 50% power: |gamma| = 1.96 (normal two-sided, ignoring tiny tail)
        g = N.wald_gamma([-1.0], [[1.0]], 0.5)
        self.assertAlmostEqual(g["gamma"], 1.96, delta=0.01)
        g80 = N.wald_gamma([-1.0], [[1.0]], 0.8)
        self.assertAlmostEqual(g80["gamma"], 1.96 + 0.8416, delta=0.01)

    def test_scaling_with_se(self):
        g1 = N.wald_gamma([-1, -2], np.eye(2), 0.5)["gamma"]
        g2 = N.wald_gamma([-1, -2], 4 * np.eye(2), 0.5)["gamma"]
        self.assertAlmostEqual(g2 / g1, 2.0, places=6)

    def test_saturation(self):
        V = np.array([[1.0, 0.0], [0.0, 0.0]])
        self.assertTrue(N.wald_gamma([-1.0, -2.0], V, 0.5)["saturated"])

    def test_anyt_one_lead_close_to_wald(self):
        g = N.anyt_gamma([-1.0], [[1.0]], 0.5, ndraw=100000)
        self.assertAlmostEqual(g, 1.96, delta=0.03)

    def test_bias_formula(self):
        lead = dict(event_times=[-4, -3, -2], cov=np.eye(3).tolist(), coef=[0, 0, 0], n_treated=[1, 1, 1])
        row = N.design_row("x", lead, dict(H=5, v_mult=1.0, policy_family="f", n_treated_overall=3), 2.0, with_anyt=False)
        self.assertAlmostEqual(row["bias_avg50"], row["gamma50"] * 3.0)
        self.assertAlmostEqual(row["R_cut50"], row["bias_avg50"] / 2.0)

    def test_output_write_once(self):
        if N.OUT.exists():
            self.assertEqual(N.main([]), 2)


if __name__ == "__main__":
    unittest.main()
