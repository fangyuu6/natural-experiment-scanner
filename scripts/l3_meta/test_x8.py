"""Synthetic tests of build_x8 pieces (no real data)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_x8 as B  # noqa: E402


class T(unittest.TestCase):
    def test_politics_names(self):
        self.assertEqual(len(B.POL), 3)

    def test_store_builder_shapes(self):
        class FakeS:
            @staticmethod
            def grid_axes():
                return ["AL", "AK"]
        import tempfile
        orig = B.COV
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.csv"
            p.write_text("state,year,dem_unified_jan1,rep_unified_jan1,citizen_ideology,ideology_carried_forward,ln_pcpi_real_2024usd\nAL,2000,1,0,40,0,10\nAL,2020,0,1,41,1,10.5\nDC,2000,,,,,\n")
            B.COV = p
            try:
                st = B.build_store(FakeS)
            finally:
                B.COV = orig
        self.assertEqual(st[B.POL[0]]["val"][("AL", 2000)], 1.0)
        self.assertEqual(st[B.POL[0]]["val"][("AL", 2020)], -1.0)
        self.assertNotIn(("AL", 2020), st[B.POL[1]]["val"])        # carried-forward ideology is not eligible
        self.assertIn(("AL", 2020), st[B.POL[2]]["val"])


if __name__ == "__main__":
    unittest.main()
