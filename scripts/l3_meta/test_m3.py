"""Synthetic tests of build_m3."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import build_m3 as B  # noqa: E402
import late_core as L  # noqa: E402

FAMS = ["fa", "fb", "fc", "fd", "fe"]
CODES = ["channel", "unsure", "no_channel"]


def synth(n_groups=12, per_group=2, effect=0.0, seed=1, n_draws=700):
    rng = np.random.default_rng(seed)
    groups_list = [f"g{k}" for k in range(n_groups)]
    grid = {(g, f): CODES[int(rng.integers(0, 3))] for g in groups_list for f in FAMS}
    designs, groups, primary = {}, {}, {}
    for gi, g in enumerate(groups_list):
        for k in range(per_group):
            did = f"{g}_d{k}"
            sids, fam = [], []
            for f in FAMS:
                for t in range(3):
                    sids.append(f"{f}_s{t}")
                    fam.append(f)
            n = len(sids)
            P = rng.normal(size=(n, n_draws))
            sd = P.std(axis=1, ddof=1)
            c = np.sort(np.abs(P), axis=1)[:, int(np.ceil(0.95 * n_draws)) - 1]
            lift = np.array([effect * RANKV[grid[(g, f)]] for f in fam])
            att = (rng.normal(size=n) + lift * rng.choice([-1, 1], size=n)) * sd
            z = att / sd
            designs[did] = SimpleNamespace(id=did, sids=sids, fam=fam, z=z, att=att, P=P, sd=sd, c=c, rho=np.abs(att) / c, policy_family=f"p{gi}")
            groups[did] = g
            primary[did] = {"fa_s0"}
    return designs, groups, grid, primary


RANKV = {"channel": 2.0, "unsure": 1.0, "no_channel": 0.0}


class T(unittest.TestCase):
    def test_tiers(self):
        designs, groups, grid, primary = synth(3, 1)
        cs = B.Cells(designs, groups, grid, primary)
        for k, did in enumerate(cs.dids):
            idx = np.where(cs.di == k)[0]
            sid = [designs[did].sids[j] for j in cs.col[idx]]
            t = dict(zip(sid, cs.tier[idx]))
            self.assertEqual(t["fa_s0"], "T0")
            self.assertEqual(t["fa_s1"], "T1")
            self.assertEqual(t["fa_s2"], "T1")
            for f in FAMS[1:]:
                self.assertEqual(t[f"{f}_s0"], B.TIER_OF_CODE[grid[(groups[did], f)]])
        cs2 = B.Cells(designs, groups, grid, primary, merge_t1=True)
        self.assertFalse((cs2.tier == "T1").any())
        self.assertEqual(int(cs2.mask.sum()), int(cs.mask.sum()) + int((cs.tier == "T1").sum()))

    def test_fe_slope_matches_ols(self):
        rng = np.random.default_rng(2)
        di = np.repeat(np.arange(6), 10)
        r = rng.integers(0, 3, 60).astype(float)
        a = 0.7 * r + rng.normal(size=60) + di
        num, den = B.fe_parts(di, r, a, 6)
        s = B.slope_from_parts(num, den)
        X = np.column_stack([r] + [(di == k).astype(float) for k in range(6)])
        b = np.linalg.lstsq(X, a, rcond=None)[0]
        self.assertAlmostEqual(s, b[0])
        fi = np.tile(np.arange(10), 6)
        s2 = B.twoway_slope(a, r, di, fi)
        X2 = np.column_stack([r] + [(di == k).astype(float) for k in range(6)] + [(fi == k).astype(float) for k in range(1, 10)])
        b2 = np.linalg.lstsq(X2, a, rcond=None)[0]
        self.assertAlmostEqual(s2, b2[0], places=5)

    def test_gradient_detects_planted_effect(self):
        designs, groups, grid, primary = synth(12, 2, effect=1.0, seed=3)
        out = B.analyse(designs, groups, grid, primary, "t", R=400, Bb=300, Bp=300)
        self.assertGreater(out["gradient"]["slope"], 0.3)
        self.assertLess(out["gradient"]["perm_p_one_sided"], 0.05)
        self.assertGreater(out["gradient"]["boot_ci"][0], 0.0)
        self.assertEqual(out["effective_n"]["policy_groups"], 12)
        self.assertGreater(out["gradient"]["mde"], 0)

    def test_gradient_null_is_near_zero(self):
        designs, groups, grid, primary = synth(12, 2, effect=0.0, seed=4)
        out = B.analyse(designs, groups, grid, primary, "t", R=400, Bb=300, Bp=300)
        self.assertLess(abs(out["gradient"]["slope"]), 0.25)
        self.assertGreater(out["gradient"]["perm_p_one_sided"], 0.02)
        self.assertLess(abs(out["gradient"]["null_mean"]), 0.05)

    def test_exclude_group_and_scale(self):
        designs, groups, grid, primary = synth(12, 2, effect=0.5, seed=5)
        out = B.analyse(designs, groups, grid, primary, "t", exclude_groups=("g0",), R=100, Bb=100, Bp=100)
        self.assertEqual(out["n_groups"], 11)
        a = B.analyse(designs, groups, grid, primary, "t", R=100, Bb=100, Bp=100)
        b = B.analyse(designs, groups, grid, primary, "t", scale=2.0, R=100, Bb=100, Bp=100)
        self.assertAlmostEqual(a["gradient"]["slope"] / 2.0, b["gradient"]["slope"], places=9)

    def test_reading(self):
        self.assertTrue(B.reading(0.3, 0.01, 0.1, 0.2).startswith("effects grow"))
        self.assertTrue(B.reading(0.1, 0.01, 0.05, 0.2).endswith("descriptive)"))
        self.assertTrue(B.reading(0.3, 0.2, 0.1, 0.2).startswith("no gradient"))

    def test_registered_primary(self):
        rows = [dict(row_type="usage", unit_kind="design", role="primary", status="estimated", catalog_series_id="s1", unit_id="d1"),
                dict(row_type="usage", unit_kind="design", role="secondary", status="estimated", catalog_series_id="s2", unit_id="d1"),
                dict(row_type="usage", unit_kind="design", role="primary", status="not_evaluable", catalog_series_id="s3", unit_id="d2")]
        self.assertEqual(dict(B.registered_primary(rows)), {"d1": {"s1"}})

    def test_stage_run_end_to_end_synthetic(self):
        import tempfile
        import json
        import sweep_core as S
        designs, groups, grid, primary = synth(12, 2, effect=0.7, seed=8)
        prim_rows = [dict(row_type="usage", unit_kind="design", role="primary", status="estimated", catalog_series_id="fa_s0", unit_id=d) for d in designs]
        saved = (B.D, L.require_records, L.design_objects, L.grid_codes, L.design_groups, S.inventory_rows, B.R_REP, B.B_BOOT, B.N_PERM)
        with tempfile.TemporaryDirectory() as tmp:
            B.D = Path(tmp)
            L.require_records = lambda rec, n: n
            L.design_objects = lambda ref, refresh=L.REFRESH: (designs, {}, None)
            L.grid_codes = lambda path=L.GRID: grid
            L.design_groups = lambda refresh=L.REFRESH: groups
            S.inventory_rows = lambda: prim_rows
            B.R_REP, B.B_BOOT, B.N_PERM = 100, 50, 50
            try:
                B.stage_run()
                res = json.loads((Path(tmp) / "RESULTS_M3_V1.json").read_text())["results"]
            finally:
                B.D, L.require_records, L.design_objects, L.grid_codes, L.design_groups, S.inventory_rows, B.R_REP, B.B_BOOT, B.N_PERM = saved
        self.assertEqual(sorted(res), ["P5_no_firearm", "P5_primary", "P5_t1_merged", "U_calibrated"])
        self.assertIn("gradient_design_and_family_fe", res["P5_primary"])

    def test_verifier(self):
        problems, n = L.verify_records(L.ROOT / "docs/plans/l3-meta/PREREG_M3_V1_HASH.txt", 99)
        self.assertTrue(any("hash records" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
