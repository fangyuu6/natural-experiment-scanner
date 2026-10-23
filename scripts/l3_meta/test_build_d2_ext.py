"""Tests of build_d2_ext on invented data only."""
import sys, unittest
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import build_d2_ext as X


class T(unittest.TestCase):
    def test_cell_stats_against_loop(self):
        rng = np.random.default_rng(1)
        zs = rng.standard_normal((3, 200)); z = np.array([0.5, -2.5, 1.0])
        s = X.cell_stats(z, zs)
        Ms = np.array([max(abs(zs[:, b])) for b in range(200)])
        q = sorted(Ms)[int(np.ceil(0.95 * 200)) - 1]
        self.assertAlmostEqual(s["q"], q); self.assertEqual(s["M"], 2.5)
        self.assertEqual(s["B"], int(2.5 > q)); self.assertAlmostEqual(s["r"], float(np.mean(Ms > q)))

    def test_null_cells_cross_about_five_percent(self):
        rng = np.random.default_rng(2); hits = []
        for _ in range(400):
            zs = rng.standard_normal((4, 300)); z = rng.standard_normal(4)
            hits.append(X.cell_stats(z, zs)["B"])
        self.assertTrue(0.02 < np.mean(hits) < 0.09)

    def test_reconcile(self):
        grid = [("g", "a"), ("g", "b"), ("g", "c")]
        mk = lambda codes: {k: dict(code=c) for k, c in zip(grid, codes)}
        c1, c2 = mk(["no_channel", "no_channel", "channel"]), mk(["no_channel", "unsure", "channel"])
        out = X.reconcile(grid, c1, c2, {("g", "b"): dict(code="unsure")})
        self.assertEqual(out[("g", "a")]["final"], "no_channel"); self.assertTrue(out[("g", "a")]["strict"])
        self.assertEqual(out[("g", "b")]["final"], "unsure"); self.assertFalse(out[("g", "b")]["strict"])
        with self.assertRaises(ValueError): X.reconcile(grid, c1, c2, {})
        with self.assertRaises(ValueError): X.reconcile(grid, c1, c2, {("g", "b"): dict(code="x"), ("g", "a"): dict(code="channel")})

    def test_agreement(self):
        a = ["channel", "unsure", "no_channel", "no_channel"]
        r = X.agreement(a, a); self.assertEqual(r["raw"], 1.0); self.assertAlmostEqual(r["kappa"], 1.0)

    def test_write_once(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"; X.write_once(p, "a")
            with self.assertRaises(FileExistsError): X.write_once(p, "b")

    def test_null_width_ratio_near_one_under_null(self):
        rng = np.random.default_rng(3)
        blocks = []
        for i in range(10):
            zs = rng.standard_normal((5, 400)); sd = zs.std(axis=1, ddof=1); zs = zs / sd[:, None]
            blocks.append(dict(design_id=str(i), sids=[str(j) for j in range(5)], z=zs[:, 7] , zstar=zs, n=400, beyond_own=np.zeros(5, bool), exp_own=np.full(5, .05)))
        r = X.D2.null_width(blocks, R_=500, seed=[1, 2, 3]); self.assertTrue(0.7 < r["ratio_observed_to_reference_mean"] < 1.4)


if __name__ == "__main__":
    unittest.main()
