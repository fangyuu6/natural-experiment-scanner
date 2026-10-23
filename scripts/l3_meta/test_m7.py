"""Synthetic tests of build_m7."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_m7 as B  # noqa: E402
import build_n11 as N  # noqa: E402
import late_core as L  # noqa: E402

STATES = ["AA", "BB", "CC", "DD", "EE", "FF"]


def design(i, fam, onset, w=(2000, 2019), z=None, sfam=None):
    z = np.arange(1.0, 31.0) if z is None else z
    sfam = (["f1"] * 10 + ["f2"] * 10 + ["f3"] * 10) if sfam is None else sfam
    return dict(id=f"d{i}", family=fam, w=w, onset=onset, z=np.array(z, float), sfam=sfam, sids=[f"s{k}" for k in range(len(z))])


class T(unittest.TestCase):
    def test_treated_matrix_and_overlaps(self):
        d1 = design(1, "a", {"AA": 2005, "BB": 2010})
        d2 = design(2, "b", {"BB": 2005, "CC": 2010})
        A1 = B.treated_matrix(d1, STATES, 2000, 2019)
        A2 = B.treated_matrix(d2, STATES, 2000, 2019)
        self.assertEqual(int(A1.sum()), 15 + 10)
        js, jy, o = B.overlaps(A1, A2, 2000, 2019, 2000)
        self.assertAlmostEqual(js, 1 / 3)                            # {AA,BB} vs {BB,CC}
        self.assertAlmostEqual(jy, 1.0)                              # years 2005..2019 both
        both = int((A1 & A2).sum())
        self.assertAlmostEqual(o, both / int((A1 | A2).sum()))
        js2, jy2, _ = B.overlaps(A1, A2, 2000, 2004, 2000)
        self.assertEqual((js2, jy2), (0.0, 0.0))

    def test_overlap_equals_n11(self):
        d1 = design(1, "a", {"AA": 2005, "BB": 2010})
        d2 = design(2, "b", {"BB": 2005, "CC": 2012}, w=(2003, 2019))
        A1 = B.treated_matrix(d1, STATES, 2000, 2019)
        A2 = B.treated_matrix(d2, STATES, 2000, 2019)
        lo, hi = 2003, 2019
        _, _, o = B.overlaps(A1, A2, lo, hi, 2000)
        def n11(d):
            return dict(id=d["id"], family=d["family"], w=d["w"], z=d["z"], A={(st, y) for st, on in d["onset"].items() for y in range(max(d["w"][0], on), d["w"][1] + 1)})
        O, C, Nn, WIN = N.pair_tables([n11(d1), n11(d2)])
        self.assertAlmostEqual(o, O[0, 1])
        self.assertAlmostEqual(float(np.mean(d1["z"] * d2["z"])), C[0, 1])

    def test_pair_table_criteria(self):
        ds = [design(1, "a", {"AA": 2005}), design(2, "a", {"BB": 2005}), design(3, "b", {"CC": 2005}, w=(2012, 2019)), design(4, "c", {"DD": 2005})]
        pairs, rows, fams = B.pair_table(ds)
        got = {(i, j) for i, j, lo, hi in pairs}
        self.assertNotIn((0, 1), got)                                  # same policy family
        self.assertNotIn((0, 2), got)                                  # window of 8 years
        self.assertIn((0, 3), got)
        self.assertEqual(sorted({r[1] for r in rows}), ["f1", "f2", "f3"])
        self.assertTrue(all(r[2] == 10 for r in rows))

    def test_fit_recovers_state_overlap(self):
        rng = np.random.default_rng(1)
        n = 600
        pi = np.arange(n)
        ov = np.column_stack([rng.uniform(0, 1, n), rng.uniform(0, 1, n), rng.uniform(0, 1, n)])
        y = 0.5 + 2.0 * ov[:, 0] + 0.0 * ov[:, 1] + 0.2 * rng.normal(size=n)
        R = B.Rows.__new__(B.Rows)
        R.pi, R.fi, R.n, R.y = pi, np.zeros(n, int), np.ones(n), y
        R.I, R.J = rng.integers(0, 10, n), rng.integers(0, 10, n)
        R.fams = ["f1"]
        r = B.fit_family(R, 0, ov)
        self.assertAlmostEqual(r[0], 2.0, delta=0.2)
        self.assertAlmostEqual(r[1], 0.0, delta=0.2)
        p = B.fit_pooled(R, ov)
        self.assertAlmostEqual(p[0], 2.0, delta=0.2)
        mult = np.ones(10)
        self.assertAlmostEqual(B.fit_family(R, 0, ov, mult)[0], B.fit_family(R, 0, ov)[0])

    def test_reading(self):
        self.assertEqual(B.reading(0.1, 0.5, -0.1, 0.2), "carried by states")
        self.assertEqual(B.reading(-0.1, 0.5, 0.1, 0.2), "carried by years")
        self.assertEqual(B.reading(0.1, 0.5, 0.1, 0.2), "both")
        self.assertEqual(B.reading(-0.1, 0.5, -0.1, 0.2), "neither")

    def test_run_end_to_end_synthetic(self):
        import tempfile
        from selref_core import REGION
        sts = sorted(REGION)
        rng = np.random.default_rng(7)
        ds = []
        for i in range(14):
            on = {st: int(rng.integers(2002, 2012)) for st in rng.choice(sts, size=int(rng.integers(3, 9)), replace=False)}
            ds.append(design(i, f"p{i % 7}", on, z=rng.normal(size=60), sfam=[f"f{k % 3}" for k in range(60)]))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "d2").mkdir()
            Path(tmp, "d2/NC_CELLS_D2_V1.csv").write_text("design_id,policy_family,policy_group,outcome_family,final_code\nd0,p0,g,f0,no_channel\nd1,p1,g,f0,no_channel\n")
            orig = B.load_designs
            B.load_designs = lambda sweep, sets_path, states: ds
            try:
                out, designs, pairs, ov, R = B.run(tmp, None, seeds=B.SEEDS, B=40, n_placebo=8)
            finally:
                B.load_designs = orig
        self.assertEqual(out["n_designs"], 14)
        self.assertGreater(out["n_headline_pairs"], 20)
        self.assertIn("placebo_years", out)
        self.assertIn("placebo_states", out)
        self.assertIn("pooled", out)
        self.assertEqual(sorted(out["observed"]), ["f0", "f1", "f2"])

    def test_verifier_needs_supplements(self):
        problems, n = L.verify_records(L.ROOT / "docs/plans/l3-meta/PREREG_M7_V1_HASH.txt", 99)
        self.assertTrue(any("hash records" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
