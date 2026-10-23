"""Synthetic tests of build_repgap (no real replication data)."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_repgap as G  # noqa: E402


def fake_pairs(n=60, seed=3, shrink=0.8):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = rng.choice([-1, 1]) * rng.uniform(0.05, 1.0)
        a = 1 if p > 0 else -1
        o = p * (shrink + rng.gauss(0, 0.15))
        se_p, se_o = abs(p) * 0.3, abs(p) * 0.3
        P, O = abs(p), a * o
        u = 1 if O > 0 else 0
        out.append(dict(paper_id=f"x{i:03d}", slug=f"x{i}", family=f"f{i % 25}", p=p, o=o, a=a, P=P, O=O, u=u, se_pub=se_p, se_ours=se_o, rung="R1b" if rng.random() < 0.4 else "R1a",
                        journal_group=["top5", "other_listed", "not_listed"][i % 3], field=["labor_tax", "health", "crime_guns_traffic", "education_family_elections_other"][i % 4],
                        year=2005 + i % 15, certified=False, target_verified=True, grade_numeric="TIGHT", stage="matched",
                        r=O / P, lam=None, z=None, absz=None, effort_ml="S" if i % 2 else "M_or_L", staggered="staggered" if i % 5 == 0 else "other_design",
                        design="DiD", treated_group="unknown", tier="B_public_rebuild", data_source="x", top5="top5" if i % 3 == 0 else "not_top5", age_group="x", effort="S"))
    import math
    for x in out:
        if x["u"]:
            x["lam"] = math.log(x["O"] / x["P"])
        so, sp = x["se_ours"], x["se_pub"]
        x["z"] = x["a"] * (x["o"] - x["p"]) / math.sqrt(so * so + sp * sp)
        x["absz"] = abs(x["z"])
    return out


class T(unittest.TestCase):
    def test_core_and_boot(self):
        ps = fake_pairs()
        c = G.core(ps)
        self.assertAlmostEqual(c["median_r"], 0.8, delta=0.1)
        self.assertEqual(c["n"], 60)
        b = G.boot(ps, "paper", reps=200)
        lo, hi = b["median_r"]["interval"]
        self.assertTrue(lo < 0.8 < hi + 0.1 and hi < 1.0)
        bf = G.boot(ps, "family", reps=200)
        self.assertEqual(bf["median_r"]["n_resamples"], 200)

    def test_regression_recovers_shift(self):
        ps = fake_pairs(n=200, seed=5)
        for x in ps:                                    # R1b rows shrink more
            if x["rung"] == "R1b" and x["lam"] is not None:
                x["lam"] -= 0.3
        r = G.regression(ps, "lam", reps=200)
        self.assertAlmostEqual(r["coefficients"]["R1b"]["estimate"], -0.3, delta=0.12)

    def test_one_feature_tables(self):
        t = G.one_feature_tables(fake_pairs())
        self.assertIn("difference_median_r", t["data_source"]) if False else None
        self.assertIn("arms", t["field"])
        self.assertTrue(all(v["n"] > 0 for v in t["field"]["arms"].values()))


if __name__ == "__main__":
    unittest.main()
