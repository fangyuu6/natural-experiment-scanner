import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import na_core as C


class T(unittest.TestCase):
    def test_third_groups(self):
        self.assertEqual(C.third_groups([1, 1, 1, 1, 1, 1]), ([0, 1], [4, 5]))
        self.assertEqual(C.third_groups([3, 1, 1, 1, 4]), ([0, 1], [4]))
        self.assertIsNone(C.third_groups([1, 1]))            # groups hold < 2 states
        self.assertEqual(C.third_groups([2, 2]), ([0], [1]))
        self.assertIsNone(C.third_groups([10]))               # single cohort
        self.assertIsNone(C.third_groups([1, 6, 1]))          # overlap / small

    def test_diff(self):
        r = C.early_late_diff([1.0, 0.0, 0.0, 3.0], [2, 1, 1, 2])
        self.assertAlmostEqual(r["early"], 1.0); self.assertAlmostEqual(r["late"], 3.0); self.assertAlmostEqual(r["D"], 2.0)
        r2 = C.early_late_diff([1.0, 0.0, 0.0, 3.0], [2, 1, 1, 2], order=[3, 1, 2, 0])
        self.assertAlmostEqual(r2["D"], -2.0)

    def test_classify(self):
        c = 2.0
        self.assertEqual(C.classify(1.0, 1.5, c), "unclear")
        self.assertEqual(C.classify(4.0, 1.0, c), "fade")
        self.assertEqual(C.classify(4.0, -1.0, c), "fade")
        self.assertEqual(C.classify(2.5, 5.0, c), "grow")
        self.assertEqual(C.classify(3.0, 3.5, c), "hold")
        self.assertEqual(C.classify(3.0, 1.9, c), "hold")      # change below cutoff
        self.assertEqual(C.classify(0.0, 4.0, c), "grow")

    def test_windows(self):
        coh = {2000: dict(n=2, eff={e: 4.0 if e < 3 else 1.0 for e in range(6)}), 2001: dict(n=1, eff={e: 4.0 for e in range(3)})}
        a, b, n, k = C.early_late_windows(coh)
        self.assertEqual((a, b, n, k), (4.0, 1.0, 2.0, 1))
        self.assertIsNone(C.early_late_windows(coh, min_states=3))

    def test_sign_and_cohorts(self):
        self.assertEqual(C.design_sign([(0, -1.0, 2), (1, -1.0, 1), (-1, 5.0, 3)]), -1)
        coh = C.build_cohorts([(2000, 0, 2.0, 3), (2000, 1, 4.0, 3), (2000, -1, 9.0, 3)], 2.0, -1)
        self.assertEqual(coh[2000]["eff"], {0: -1.0, 1: -2.0}); self.assertEqual(coh[2000]["n"], 3)

    def test_fe_contrast(self):
        d = [dict(y=[1.0, 3.0, 2.0], w=[1, 1, 1], lab=[1, -1, 0]), dict(y=[5.0, 5.0], w=[1, 1], lab=[1, 1])]
        diff, nd, npos, nneg, per = C.fe_contrast(d)
        self.assertEqual(nd, 1); self.assertAlmostEqual(diff, -2.0); self.assertEqual(per, [-2.0])

    def test_terms_match(self):
        d = [dict(y=[1.0, 3.0, 2.0, 0.5], w=[1, 2, 1, 1], lab=[1, -1, 0, 1]), dict(y=[2.0, 0.0], w=[3, 1], lab=[-1, 1])]
        diff = C.fe_contrast(d)[0]
        self.assertAlmostEqual(diff, C.pooled_from_terms([C.design_terms(**x) for x in d]))

    def test_halves(self):
        self.assertEqual(list(C.halves([3, 1, 2, 4])), [1, -1, -1, 1])
        self.assertEqual(list(C.halves([3, 1, 2])), [1, -1, 0])


if __name__ == "__main__":
    unittest.main()
