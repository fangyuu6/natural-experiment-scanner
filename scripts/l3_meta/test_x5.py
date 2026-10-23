"""Synthetic tests of build_x5."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_x5 as B  # noqa: E402
import late_core as L  # noqa: E402

FAMS = ["fa", "fb", "fc", "fd", "fe", "ff"]


def make_design(i, rng, loading, n_draws=600):
    """Design with 12 series (two per family); z has a design-level factor with the family loadings."""
    sids = [f"s{k}" for k in range(12)]
    fam = [FAMS[k // 2] for k in range(12)]
    load = np.array([loading[k // 2] for k in range(12)])
    f_obs = rng.normal()
    z = load * f_obs + 0.5 * rng.normal(size=12)
    P = (load[:, None] * rng.normal(size=(1, n_draws)) + 0.5 * rng.normal(size=(12, n_draws)))
    return SimpleNamespace(id=f"d{i}", policy_family=f"p{i % 8}", sids=sids, fam=fam, z=z, P=P, sd=np.ones(12))


class T(unittest.TestCase):
    def test_cosine(self):
        C = np.array([[5, 5, 0], [10, 10, 0], [0, 0, 7]], float)
        W = B.cosine_weights(C)
        self.assertAlmostEqual(W[0, 1], 1.0)
        self.assertAlmostEqual(W[0, 2], 0.0)
        self.assertAlmostEqual(W[0, 0], 1.0)

    def test_alternative_weights(self):
        C = np.array([[5, 5, 0], [10, 10, 0], [0, 3, 7]], float)
        J = B.jaccard_weights(C)
        self.assertAlmostEqual(J[0, 1], 2 / 3)                         # groups {0,1} vs {0,1,2}
        self.assertAlmostEqual(J[0, 2], 0.0)
        Wl = B.logcount_weights(C)
        self.assertAlmostEqual(Wl[0, 0], 1.0)
        self.assertGreater(Wl[0, 1], Wl[0, 2])

    def test_comovement_and_null(self):
        rng = np.random.default_rng(1)
        designs = {f"d{i}": make_design(i, rng, [2, 2, 2, 0, 0, 0]) for i in range(30)}
        Zbar, ZBs = [], []
        for d in designs.values():
            zb, ZB = B.family_means(d, FAMS)
            Zbar.append(zb)
            ZBs.append(ZB)
        Zbar = np.array(Zbar)
        CM, N = B.comovement(Zbar)
        self.assertEqual(N[0, 1], 30)
        self.assertGreater(CM[0, 1], CM[0, 4])                            # families a, b share a factor, a and e do not
        E = B.exact_null(ZBs)
        m_exact = B.null_from_exact(E)
        m_rep = B.replicate_null(ZBs, 3000, np.random.default_rng(2))
        self.assertLess(np.nanmax(np.abs(m_exact - m_rep)), 0.25)
        self.assertGreater(m_exact[0, 1], m_exact[0, 4])

    def test_mantel_detects_planted_map(self):
        rng = np.random.default_rng(3)
        F = 9
        W = np.eye(F)
        W[0, 1] = W[1, 0] = W[0, 2] = W[2, 0] = W[1, 2] = W[2, 1] = 0.9
        pairs = [(a, b) for a in range(F) for b in range(a + 1, F)]
        E = np.array([W[a, b] + 0.05 * rng.normal() for a, b in pairs])
        rho = B.spearman(np.array([W[a, b] for a, b in pairs]), E)
        perm = B.mantel(W, E, pairs, 2000, np.random.default_rng(4))
        self.assertGreater(rho, 0.4)
        self.assertLess(L.one_sided_p_ge(rho, perm), 0.05)
        Eflat = rng.normal(size=len(pairs))
        rho2 = B.spearman(np.array([W[a, b] for a, b in pairs]), Eflat)
        self.assertGreater(L.one_sided_p_ge(rho2, B.mantel(W, Eflat, pairs, 2000, np.random.default_rng(5))), 0.05) if abs(rho2) < 0.5 else None

    def test_analyse_end_to_end(self):
        rng = np.random.default_rng(6)
        designs = {f"d{i}": make_design(i, rng, [2, 2, 2, 0, 0, 0]) for i in range(30)}
        W = np.eye(6) * 0.2 + 0.2
        W[:3, :3] = 0.9
        np.fill_diagonal(W, 1.0)
        out = B.analyse(designs, W, FAMS, FAMS, R=600, Bm=300, Bb=200)
        self.assertEqual(out["n_pairs"], 15)
        self.assertIn("rho", out)
        self.assertGreater(out["rho"], 0.3)
        self.assertEqual(out["verdict"][:11], "descriptive")                # 15 pairs < 30
        nc = {(f"d{i}", FAMS[k]) for i in range(30) for k in (0, 1, 3)}
        out2 = B.analyse(designs, W, FAMS, FAMS, nc=nc, R=300, Bm=100, Bb=50)
        self.assertEqual(out2["n_pairs"], 3)

    def test_stage_run_end_to_end_synthetic(self):
        import tempfile
        import json
        rng = np.random.default_rng(9)
        designs = {f"d{i}": make_design(i, rng, [2, 2, 2, 0, 0, 0]) for i in range(30)}
        Cnt = rng.integers(0, 20, size=(10, 6)).astype(float)
        with tempfile.TemporaryDirectory() as tmp:
            nc = Path(tmp) / "nc.csv"
            nc.write_text("design_id,outcome_family\n" + "\n".join(f"d{i},{FAMS[k]}" for i in range(30) for k in (0, 1, 3)) + "\n")
            saved = (B.D, L.require_records, L.design_objects, B.load_d10, L.NC_CELLS, B.R_REP, B.N_MANTEL, B.B_BOOT)
            B.D = Path(tmp)
            L.require_records = lambda rec, n: n
            L.design_objects = lambda ref, refresh=L.REFRESH: (designs, {}, None)
            B.load_d10 = lambda: ([f"g{k}" for k in range(10)], FAMS, Cnt)
            L.NC_CELLS = nc
            B.R_REP, B.N_MANTEL, B.B_BOOT = 200, 100, 40
            try:
                orig = B.analyse
                B.analyse = lambda *a, **k: orig(*a, **dict(k, R=150, Bm=80, Bb=30))
                B.stage_run()
                res = json.loads((Path(tmp) / "RESULTS_X5_V1.json").read_text())["results"]
            finally:
                B.analyse = orig
                B.D, L.require_records, L.design_objects, B.load_d10, L.NC_CELLS, B.R_REP, B.N_MANTEL, B.B_BOOT = saved
        self.assertEqual(sorted(res), ["P5", "P5_W_jaccard", "P5_W_logcount", "P5_no_channel_cells", "U", "U_no_channel_cells"])
        self.assertEqual(res["P5"]["n_pairs"], 15)

    def test_verdict(self):
        self.assertEqual(B.verdict(0.5, 0.01, 0.2, 0.2, 40), "statutes and effects draw the same outcome map")
        self.assertEqual(B.verdict(-0.1, 0.8, -0.3, 0.2, 40), "disagree")
        self.assertEqual(B.verdict(0.1, 0.3, -0.3, 0.4, 40), "inconclusive (low power)")

    def test_verifier(self):
        problems, n = L.verify_records(L.ROOT / "docs/plans/l3-meta/PREREG_X5_V1_HASH.txt", 99)
        self.assertTrue(any("hash records" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
