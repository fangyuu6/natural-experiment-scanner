"""Tests of t64_core on synthetic data (no real adoption data, no real direction labels)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import scipy.sparse as sp  # noqa: E402
import t64_core as C  # noqa: E402


def synth_panel(P=30, S=20, y0=1961, y1=1990, seed=1, b_signed=0.0, tau=0.0):
    rng = np.random.default_rng(seed)
    states = [f"S{i:02d}" for i in range(S)]
    years = list(range(y0, y1 + 1))
    dem = {(s, t): int(rng.integers(-1, 2)) for s in states for t in range(y0 - 2, y1 + 1)}
    sign = rng.choice([-1, 1], size=P)
    rows = []
    for p in range(P):
        base = rng.normal(-3.2, 0.3)
        slope = b_signed * sign[p] + rng.normal(0, tau)
        for s in states:
            ad = np.nan
            for t in years:
                h = 1 / (1 + np.exp(-(base + slope * dem[(s, t)] + 0.02 * (t - y0))))
                if rng.random() < h:
                    ad = t
                    break
            for t in years:
                if np.isnan(ad) or t <= ad:
                    rows.append((f"p{p:02d}", s, t, ad))
    panel = pd.DataFrame(rows, columns=["policy", "state", "year", "adopt_year"])
    pol = pd.DataFrame([dict(state=s, year=t, dem_unified_jan1=int(dem[(s, t)] == 1), rep_unified_jan1=int(dem[(s, t)] == -1),
                             citizen_ideology=50 + rng.normal(), ln_pcpi_real_2024usd=9 + rng.normal(0, .1), urban_share=.6 + rng.normal(0, .05)) for s in states for t in range(y0 - 2, y1 + 1)])
    adj = [(states[i], states[i + 1]) for i in range(S - 1)]
    return panel, pol, adj, {f"p{p:02d}": int(sign[p]) for p in range(P)}


class Core(unittest.TestCase):
    def test_build_rows_basic(self):
        panel, pol, adj, _ = synth_panel(P=4, S=6, y1=1975)
        rows, counts = C.build_rows(panel, pol, adj, y0=1961, y1=1975)
        self.assertEqual(counts["rows_complete"], len(rows))
        self.assertTrue(set(rows["y"]) <= {0, 1})
        # an adopter has exactly one event row and no later rows
        g = rows.groupby(["policy", "state"])["y"].sum()
        self.assertTrue((g <= 1).all())
        self.assertTrue(((rows["nbr"] >= 0) & (rows["nbr"] <= 1)).all())
        self.assertTrue(((rows[["dur1", "dur2", "dur3"]].sum(axis=1)) <= 1).all())

    def test_logit_matches_statsmodels(self):
        import statsmodels.api as sm
        rng = np.random.default_rng(3)
        n, P = 3000, 8
        pc = rng.integers(0, P, n)
        yc = rng.integers(0, 6, n)
        x = rng.normal(size=(n, 2))
        eta = -1 + 0.5 * x[:, 0] - 0.3 * x[:, 1] + 0.2 * pc / P
        y = (rng.random(n) < 1 / (1 + np.exp(-eta))).astype(float)
        X = C.design(x, pc, yc, P, 6)
        f = C.fit_logit(X, y)
        self.assertTrue(f["converged"])
        ref = sm.Logit(y, X.toarray()).fit(disp=0, method="newton", maxiter=100)
        self.assertLess(np.max(np.abs(f["beta"][:2] - ref.params[:2])), 1e-5)
        V = C.cluster_cov(X, y, f, rng.integers(0, 10, n))
        self.assertTrue(np.all(np.isfinite(np.diag(V))))

    def test_firth_close_to_mle_when_easy(self):
        import statsmodels.api as sm
        rng = np.random.default_rng(5)
        n = 4000
        X = np.column_stack([np.ones(n), rng.normal(size=(n, 2))])
        y = (rng.random(n) < 1 / (1 + np.exp(-(X @ np.array([-1.0, 0.4, 0.2]))))).astype(float)
        b, cov, ok = C.firth_logit(X, y)
        ref = sm.Logit(y, X).fit(disp=0)
        self.assertTrue(ok)
        self.assertLess(np.max(np.abs(b - ref.params)), 0.02)
        self.assertLess(abs(np.sqrt(cov[1, 1]) / ref.bse[1] - 1), 0.02)
        # separation: finite estimate
        x = rng.normal(size=60)
        y2 = (x > 0).astype(float)
        b2, _, _ = C.firth_logit(np.column_stack([np.ones(60), x]), y2)
        self.assertTrue(np.all(np.isfinite(b2)))

    def test_reml_recovers_tau(self):
        rng = np.random.default_rng(7)
        se = rng.uniform(0.1, 0.2, 400)
        theta = 0.3 + rng.normal(0, 0.25, 400) + rng.normal(0, 1, 400) * se
        r = C.reml(theta, se)
        self.assertAlmostEqual(r["mu"], 0.3, delta=0.05)
        self.assertAlmostEqual(r["tau"], 0.25, delta=0.04)
        r0 = C.reml(rng.normal(0, 1, 300) * se[:300], se[:300])
        self.assertLess(r0["tau"], 0.06)

    def test_signed_model_recovers_effect_on_synthetic_panel(self):
        panel, pol, adj, sign = synth_panel(P=40, S=25, y1=1995, seed=11, b_signed=0.6)
        rows, _ = C.build_rows(panel, pol, adj, y0=1961, y1=1995)
        rows = C.standardise(rows)
        pols = sorted(rows["policy"].unique())
        pcode = rows["policy"].map({p: i for i, p in enumerate(pols)}).to_numpy()
        ycode = (rows["year"] - rows["year"].min()).to_numpy()
        s = rows["policy"].map(sign).to_numpy()
        dense = np.column_stack([s * rows["dem"], s * rows["ideo"]] + [rows[c] for c in C.DENSE_BASE])
        X = C.design(dense, pcode, ycode, len(pols), int(ycode.max()) + 1)
        f = C.fit_logit(X, rows["y"].to_numpy(float))
        self.assertTrue(f["converged"])
        self.assertAlmostEqual(f["beta"][0], 0.6, delta=0.15)
        # the unsigned coefficient is near 0 because the signs cancel
        dense0 = np.column_stack([rows["dem"]] + [rows[c] for c in C.DENSE_BASE])
        f0 = C.fit_logit(C.design(dense0, pcode, ycode, len(pols), int(ycode.max()) + 1), rows["y"].to_numpy(float))
        self.assertLess(abs(f0["beta"][0]), f["beta"][0] - 0.2)      # signs cancel in the pool


if __name__ == "__main__":
    unittest.main()
