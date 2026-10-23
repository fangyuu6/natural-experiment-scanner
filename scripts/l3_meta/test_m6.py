"""Synthetic tests of build_m6 (and late_core helpers)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_m6 as B  # noqa: E402
import late_core as L  # noqa: E402


def make_arr(rng, parts_att, parts_pl, agg_att=None, agg_pl=None, code="SUPPORTED", extra=2):
    ids = [B.AGG["property"][0], *B.AGG["property"][1]] + [f"other{k}" for k in range(extra)]
    na = len(parts_att)
    att = np.concatenate([[parts_att.sum() if agg_att is None else agg_att], parts_att, rng.normal(size=extra)])
    pl = np.vstack([parts_pl.sum(axis=0)[None] if agg_pl is None else agg_pl, parts_pl, rng.normal(size=(extra, parts_pl.shape[1]))])
    return dict(series_ids=np.array(ids), code=np.array([code] * len(ids)), att=att, sd=np.nanstd(pl, axis=1, ddof=1), placebo=pl, family=np.array(["crime_justice"] * len(ids)))


class T(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(3)

    def unit(self, parts_att, parts_pl):
        arr = make_arr(self.rng, parts_att, parts_pl)
        u, why = B.get_unit(arr, *B.AGG["property"])
        self.assertIsNone(why)
        return u

    def test_addup_pass_and_fail(self):
        u = self.unit(self.rng.normal(size=3), self.rng.normal(size=(3, 700)))
        self.assertTrue(B.addup_pass(B.addup_gap(u)))
        arr = make_arr(self.rng, self.rng.normal(size=3), self.rng.normal(size=(3, 700)), agg_att=5.0)
        u2, _ = B.get_unit(arr, *B.AGG["property"])
        self.assertFalse(B.addup_pass(B.addup_gap(u2)))
        u3 = self.unit(self.rng.normal(size=3), self.rng.normal(size=(3, 300)))
        self.assertFalse(B.addup_pass(B.addup_gap(u3)))          # fewer than 500 draws

    def test_get_unit_refusals(self):
        arr = make_arr(self.rng, self.rng.normal(size=3), self.rng.normal(size=(3, 600)), code="KEPT_LT_80")
        self.assertIsNotNone(B.get_unit(arr, *B.AGG["property"])[1])
        arr = make_arr(self.rng, self.rng.normal(size=3), self.rng.normal(size=(3, 600)))
        self.assertIsNotNone(B.get_unit(arr, *B.AGG["violent"])[1])

    def test_offset_index(self):
        nd = 400
        a = self.rng.normal(size=(1, nd, 3))
        O, med, opp = B.stats_batch(a.sum(-1), a)
        self.assertLess(abs(O[0]), 0.15)                         # independent parts: about 0
        x = self.rng.normal(size=(1, nd, 1))
        parts = np.concatenate([x, -x + 0.1 * self.rng.normal(size=(1, nd, 1)), 0.1 * self.rng.normal(size=(1, nd, 1))], axis=-1)
        O2, med2, opp2 = B.stats_batch(parts.sum(-1), parts)
        self.assertGreater(O2[0], 0.9)                           # offsetting parts
        self.assertGreater(med2[0], 3)
        self.assertGreater(opp2[0], 0.2)
        same = np.abs(x) + 0.2 * np.abs(a)
        O3, med3, opp3 = B.stats_batch(same.sum(-1), same)
        self.assertLess(O3[0], 0)                                # co-moving parts
        self.assertAlmostEqual(med3[0], 1.0)
        self.assertEqual(opp3[0], 0.0)

    def test_null_and_bootstrap_shapes(self):
        units = [self.unit(self.rng.normal(size=3), self.rng.normal(size=(3, 700))) for _ in range(12)]
        O, med, opp = B.null_replicates(units, 200, np.random.default_rng(1))
        self.assertEqual(O.shape, (200,))
        self.assertLess(abs(np.nanmean(O)), 0.5)
        bt = B.bootstrap(units, list(range(12)), 50, np.random.default_rng(2))
        self.assertEqual(bt.shape, (50, 3))
        self.assertTrue(np.isfinite(bt[:, 0]).sum() > 40)

    def test_reading(self):
        self.assertEqual(B.reading(0.6, 0.0, -0.3, 0.3, 0.2, 0.9, 0.4)[0], "offsets beyond random")
        self.assertEqual(B.reading(-0.6, 0.0, -0.3, 0.3, -0.9, -0.2, 0.4)[0], "parts co-move")
        self.assertEqual(B.reading(0.1, 0.0, -0.3, 0.3, -0.2, 0.4, 0.4)[0], "no excess offset")
        self.assertEqual(B.reading(0.6, 0.0, -0.3, 0.3, 0.2, 0.9, 0.7)[1], "descriptive")

    def test_pseudo_offsets(self):
        Z = self.rng.normal(size=(20, 30))
        fam = [f"f{k % 6}" for k in range(30)]
        ps, tries = B.pseudo_offsets(Z, fam, 3, 100, np.random.default_rng(5))
        self.assertEqual(len(ps), 100)
        self.assertLess(abs(ps.mean()), 0.3)

    def test_records_verifier_requires_supplements(self):
        problems, n = L.verify_records(ROOT_REC, 99)
        self.assertTrue(any("hash records" in p for p in problems))


    def test_stage_run_end_to_end_synthetic(self):
        import tempfile
        import json
        rng = np.random.default_rng(11)
        uni = {}
        names_p = [B.AGG["property"][0], *B.AGG["property"][1]]
        names_v = [B.AGG["violent"][0], *B.AGG["violent"][1]]
        fams = ["crime_justice"] * 9 + [f"f{k % 6}" for k in range(20)]
        ids = names_p + names_v + [f"o{k}" for k in range(20)]
        for i in range(20):
            pa = rng.normal(size=(3, 700))
            pv = rng.normal(size=(4, 700))
            pl = np.vstack([pa.sum(0), pa, pv.sum(0), pv, rng.normal(size=(20, 700))])
            aa, av = rng.normal(size=3), rng.normal(size=4)
            att = np.concatenate([[aa.sum()], aa, [av.sum()], av, rng.normal(size=20)])
            uni[f"d{i}"] = dict(arr=dict(series_ids=np.array(ids), family=np.array(fams), code=np.array(["SUPPORTED"] * len(ids)), att=att, sd=pl.std(axis=1, ddof=1), placebo=pl,
                                         cutoff=np.ones(len(ids))), meta={})
        uni["d19"]["arr"]["att"][0] += 1.0                       # one design fails the property adding-up check
        saved = (B.load_refs, B.D, L.require_records, L.live_families)
        with tempfile.TemporaryDirectory() as tmp:
            B.load_refs = lambda: {"P5": uni, "U": uni}
            B.D = Path(tmp)
            L.require_records = lambda rec, n: n
            L.live_families = lambda refresh=L.REFRESH: {f"d{i}": f"p{i % 5}" for i in range(20)}
            try:
                B.R_REP, B.B_BOOT, B.N_PSEUDO = 200, 50, 40
                B.stage_check()
                rep = json.loads((Path(tmp) / "ADDUP_M6_V1.json").read_text())["units"]
                self.assertEqual(rep["P5:property"]["n_pass"], 19)
                self.assertEqual(rep["P5:violent"]["n_pass"], 20)
                B.stage_run()
                res = json.loads((Path(tmp) / "RESULTS_M6_V1.json").read_text())["results"]
            finally:
                B.load_refs, B.D, L.require_records, L.live_families = saved
                B.R_REP, B.B_BOOT, B.N_PSEUDO = 9999, 9999, 1000
        self.assertEqual(res["P5:property"]["n_designs"], 19)
        self.assertEqual(res["P5:violent"]["n_designs"], 20)
        self.assertIn("pseudo_z", res["P5:violent"])
        self.assertIn(res["P5:violent"]["reading"], ("no excess offset", "offsets beyond random", "parts co-move"))


ROOT_REC = L.ROOT / "docs/plans/l3-meta/PREREG_M6_V1_HASH.txt"

if __name__ == "__main__":
    unittest.main()
