"""Synthetic tests of build_r0 helpers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_r0 as B  # noqa: E402


class T(unittest.TestCase):
    def test_rel_diff_and_compare(self):
        a = np.array([1.0, 2.0, np.nan])
        self.assertEqual(B.rel_diff(a, a), 0.0)
        self.assertEqual(B.rel_diff(a, np.array([1.0, 2.0, 3.0])), float("inf"))
        new = dict(att=np.ones(3), sd=np.ones(3), placebo=np.ones((3, 5)), code=np.array(["SUPPORTED"] * 3))
        old = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in new.items()}
        self.assertTrue(B.compare_arrays(new, old)["ok"])
        old["att"] = old["att"] * (1 + 1e-6)
        self.assertFalse(B.compare_arrays(new, old)["ok"])

    def test_cp_interval(self):
        lo, hi = B.cp_interval(5, 100)
        self.assertLess(lo, 0.05)
        self.assertGreater(hi, 0.05)
        self.assertEqual(B.cp_interval(0, 10)[0], 0.0)

    def test_gate_positions(self):
        self.assertEqual(B.GATE_POSITIONS, (0, 5, 10, 15, 20, 25))


if __name__ == "__main__":
    unittest.main()
