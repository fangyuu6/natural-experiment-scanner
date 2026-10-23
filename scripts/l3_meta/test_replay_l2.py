"""Tests for replay_l2.py (synthetic designs; no L2 data needed except the optional pilot package)."""
import math
import random
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import replay_l2 as R  # noqa: E402


def toy():
    rng = random.Random(7)
    units = ["A", "B", "C", "D", "E", "F", "G", "H"]
    out = {(u, t): rng.gauss(0, 1) + 0.3 * t for u in units for t in range(7)}
    cells = []
    for g, members in ((3, ["A", "B"]), (4, ["C"])):
        controls = ["D", "E", "F", "G", "H"]
        for t in range(g - 2, 7):
            if t == g - 3 or t == g - 1 and False:
                continue
            cells.append(dict(g=g, t=t, base_period=g - 3, treated=members, controls=controls, n_treated=len(members),
                              event_time=t - g, post=t >= g))
    return units, cells, out


class Tests(unittest.TestCase):
    def test_att_matches_l2_estimator_and_variance_matches_multiplier(self):
        from scripts.l2_pipeline.engine import estimator
        units, cells, out = toy()
        design = dict(units=units, cells=[dict(c) for c in cells], groups=[3, 4], periods=list(range(7)),
                      control_group="never_treated")
        l2 = estimator.estimate_design(design, out, B=20000, seed=11)
        mine = R.influence_table(units, cells, out)
        self.assertAlmostEqual(mine["overall"][0], l2["overall"]["att"], places=12)
        for r in l2["event_study"]:
            self.assertAlmostEqual(mine["events"][r["event_time"]]["att"], r["att"], places=12)
        se_mult = l2["overall"]["se"]
        se_mine = math.sqrt(R.variance_from_psi(mine["overall"][1]))
        self.assertLess(abs(se_mult / se_mine - 1), 0.03)
        e = l2["event_study"][0]
        self.assertLess(abs(e["se"] / math.sqrt(R.variance_from_psi(mine["events"][e["event_time"]]["psi"])) - 1), 0.03)

    def test_gate(self):
        units, cells, out = toy()
        mine = R.influence_table(units, cells, out)
        stored = [dict(event_time=e, att=v["att"]) for e, v in mine["events"].items()]
        g = R.gate_against_stored(mine, mine["overall"][0], stored)
        self.assertTrue(g["passed"])
        stored[0]["att"] += 1e-6
        self.assertFalse(R.gate_against_stored(mine, mine["overall"][0], stored)["passed"])
        self.assertFalse(R.gate_against_stored(mine, mine["overall"][0] + 1e-3, [])["passed"])
        g = R.gate_against_stored(mine, mine["overall"][0], stored[1:])
        self.assertFalse(g["passed"])  # event-time sets differ

    def test_cov_is_psd_symmetric_and_diag_is_variance(self):
        units, cells, out = toy()
        mine = R.influence_table(units, cells, out)
        lead = R.lead_block(mine["events"])
        c = np.array(lead["cov"])
        self.assertTrue(np.allclose(c, c.T))
        self.assertGreaterEqual(np.linalg.eigvalsh(c).min(), -1e-12)
        for k, e in enumerate(lead["event_times"]):
            self.assertAlmostEqual(c[k, k], R.variance_from_psi(mine["events"][e]["psi"]), places=14)
        self.assertTrue(all(e < 0 for e in lead["event_times"]))

    def test_variance_from_ci(self):
        self.assertAlmostEqual(R.variance_from_ci([-1.96, 1.96]), 1.0)
        self.assertIsNone(R.variance_from_ci(None))
        self.assertIsNone(R.variance_from_ci([None, 1.0]))

    def test_joint_covariance(self):
        a = np.array([1.0, 2.0, 3.0]); b = np.array([2.0, 1.0])
        ids, c = R.joint_covariance({"x": a, "y": b}, {"x": ["S1", "S2", "S3"], "y": ["S2", "S3"]})
        self.assertAlmostEqual(c[0, 0], 14 / 9)
        self.assertAlmostEqual(c[0, 1], (2 * 2.0 + 3 * 1.0) / (3 * 2))
        self.assertAlmostEqual(c[0, 1], c[1, 0])

    def test_pilot_package_if_present(self):
        pkg = ROOT / "data/l2_pipeline/autopilot/c72/work/b1010_handheld_ban_all/v6"
        if not (pkg / "FREEZE_FINAL.json").exists():
            self.skipTest("pilot package not on disk")
        loaded = R.load_package(pkg)
        design = loaded["design"]
        t = R.influence_table(design["units"], design["cells"], loaded["outcomes"])
        self.assertAlmostEqual(t["overall"][0], 1.0053983321260451, places=9)
        sh = R.observed_s_pre_h(loaded)
        self.assertAlmostEqual(sh["s_pre"], 6.927874625165487, places=9)
        self.assertEqual(sh["H"], 4)


if __name__ == "__main__":
    unittest.main()
