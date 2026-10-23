"""Checks on INVENTED data that the L2 engine is linear in the outcome and that the placebo draws of two outcomes of one design are the same
label assignments when their estimating structure is the same.  D3 (PREREG_AMEND_20261015_D_QUESTIONS.md, entry D3, and build file 3.3)
relies on both: ATT(C) = ATT(T) - ATT(A) cell by cell and draw by draw.

This test imports the L2 engine CODE (scripts/l2_pipeline/engine/*.py) read-only, with bytecode writing off, and runs it on invented panels.
No repo data file is read, no L2 result is read, nothing is written; it asserts that no __pycache__ directory appears under scripts/l2_pipeline.

Run:  .venv_did/bin/python -B scripts/l3_meta/test_d3_engine_identity.py
"""
import hashlib
import json
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import build_d3 as D  # noqa: E402
from scripts.l2_pipeline.engine import noise, ri  # noqa: E402
from scripts.l2_pipeline.engine.runner import fit_primary  # noqa: E402

ENGINE_FILES = ["estimator.py", "ri.py", "noise.py", "runner.py", "design.py", "structure.py", "exposure.py", "types.py", "calendar.py"]


def pycache_dirs():
    return sorted(str(p) for p in (ROOT / "scripts" / "l2_pipeline").rglob("__pycache__"))


def declaration(control_group="never_treated"):
    units = list("ABCDEFGHIJKL")
    cohorts = {u: 6 for u in "ABC"} | {u: 9 for u in "DEF"}
    d = dict(key="invented", variant="primary", cohorts=cohorts, operative_years=dict(cohorts), never_controls=list("GHIJKL"),
             case=dict(panel=dict(frequency="annual", time_basis=dict(kind="calendar_year")),
                       windows=dict(primary=dict(start=0, end=13, excluded_periods=[])),
                       sample_mask=dict(include_units=units), comparison=dict(exclusions=[]),
                       exposure_clock=dict(date_column="operative_date")))
    if control_group != "never_treated":
        d["control_group"] = control_group
    return d


def panel(seed, shift=0.0, noise=0.15):
    """Invented outcome rows (unit, period, value, cohort): random walks per unit plus a treatment shift."""
    d = declaration()
    rng = random.Random(seed)
    rows = []
    for j, u in enumerate("ABCDEFGHIJKL"):
        level = 40 + 3 * j
        for t in range(14):
            level += rng.gauss(0, noise)
            rows.append((u, t, level + (shift if u in d["cohorts"] and t >= d["cohorts"][u] else 0.0), d["cohorts"].get(u)))
    return rows


def add(rows1, rows2):
    return [(u, t, y1 + y2, g) for (u, t, y1, g), (_, _, y2, _) in zip(rows1, rows2)]


def run(d, rows):
    payload, design, outcomes = fit_primary(d, rows, B=9)
    ref = ri.permutation_reference(d, design, outcomes, count=999)
    nf = noise.noise_summary(ref, payload["result"]["overall"]["att"])
    return payload, ref, nf


def as_entry(payload, nf, n_cells=12 * 14):
    return {"status": "ESTIMATED", "inventory": {"cells": n_cells, "construction_cells": n_cells, "metadata_sha256": "invented"},
            "result": payload["result"], "noise_floor": nf}


class EngineIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = pycache_dirs()
        cls.hashes = {f: hashlib.sha256((ROOT / "scripts/l2_pipeline/engine" / f).read_bytes()).hexdigest() for f in ENGINE_FILES}
        print("ENGINE_FILE_HASHES " + json.dumps(cls.hashes, sort_keys=True))

    def scenario(self, control_group):
        d = declaration(control_group)
        ya, yc = panel(1, shift=-3.0), panel(2, shift=+2.0)       # target falls, substitute rises (invented)
        yt = add(ya, yc)
        pa, ra, na = run(d, ya)
        pc, rc, nc = run(d, yc)
        pt, rt, nt = run(d, yt)
        return d, (pa, ra, na), (pc, rc, nc), (pt, rt, nt)

    def check_identity(self, control_group):
        d, (pa, ra, na), (pc, rc, nc), (pt, rt, nt) = self.scenario(control_group)
        # same estimating structure for the three outcomes
        key = lambda p: [(c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], c["post"]) for c in p["result"]["group_time"]]
        self.assertEqual(key(pa), key(pt))
        self.assertEqual(key(pa), key(pc))
        self.assertEqual(ra["feasibility"]["infeasible_draws"], rt["feasibility"]["infeasible_draws"])
        self.assertEqual(ra["feasibility"]["infeasible_draws"], rc["feasibility"]["infeasible_draws"])
        # linearity in the outcome: cells, overall ATT, and the 999 placebo ATTs
        for ca, cc, ct in zip(pa["result"]["group_time"], pc["result"]["group_time"], pt["result"]["group_time"]):
            self.assertAlmostEqual(ct["att"], ca["att"] + cc["att"], places=9)
        self.assertAlmostEqual(pt["result"]["overall"]["att"], pa["result"]["overall"]["att"] + pc["result"]["overall"]["att"], places=9)
        self.assertEqual(len(ra["overall"]), len(rt["overall"]))
        self.assertGreaterEqual(len(ra["overall"]), 900)
        for x, y, z in zip(ra["overall"], rc["overall"], rt["overall"]):
            self.assertAlmostEqual(z, x + y, places=9)
        # D3's own arithmetic on engine-shaped entries reproduces the engine run on the complement
        A, T = as_entry(pa, na), as_entry(pt, nt)
        self.assertEqual(D.structure_failures(A, T), [])
        nums, problems = D.pair_numbers(A, T)
        self.assertEqual(problems, [])
        self.assertAlmostEqual(nums["c"], pc["result"]["overall"]["att"], places=9)
        self.assertAlmostEqual(nums["sd_c"], nc["sample_sd"], places=9)
        self.assertAlmostEqual(nums["cutoff_c"], nc["absolute_cutoff"], places=9)
        self.assertAlmostEqual(nums["p2_c"], nc["p_two_sided"], places=12)
        self.assertEqual(nums["beyond_c"], nc["strictly_exceeds_cutoff"])
        self.assertEqual(nums["n_draws"], nc["draws"])
        # the engine's own noise statistics of A and T are the ones D3 recomputes from the draws (the builder's cross-check)
        self.assertEqual(nums["sd_a"], na["sample_sd"])
        self.assertEqual(nums["cutoff_t"], nt["absolute_cutoff"])
        self.assertGreater(nums["corr_a_t_draws"], 0.3)             # same label draws: the placebo ATTs of A and T move together
        # by the invented design the substitute rises when the target falls
        self.assertLess(nums["a"], 0)
        self.assertGreater(nums["c"], 0)
        self.assertTrue(nums["opposite"])
        return nums

    def test_identity_never_treated_controls(self):
        nums = self.check_identity("never_treated")
        print("IDENTITY never_treated a=%.4f t=%.4f c=%.4f Z=%.3f n_draws=%d" % (nums["a"], nums["t"], nums["c"], nums["Z"], nums["n_draws"]))

    def test_identity_not_yet_treated_controls(self):
        nums = self.check_identity("not_yet_treated")
        print("IDENTITY not_yet_treated a=%.4f t=%.4f c=%.4f Z=%.3f n_draws=%d" % (nums["a"], nums["t"], nums["c"], nums["Z"], nums["n_draws"]))

    def test_different_cell_sets_break_the_identity_and_are_flagged(self):
        d = declaration()
        ya, yc = panel(3, shift=-1.0), panel(4, shift=+0.5)
        yt = add(ya, yc)
        # the target is observed on fewer cells (as with WONDER suppression): drop a few early cells of treated and control units from A only
        dropped = {("G", 0), ("K", 13), ("L", 6), ("A", 13)}
        ya_masked = [r for r in ya if (r[0], r[1]) not in dropped]
        pa, ra, na = run(d, ya_masked)
        pt, rt, nt = run(d, yt)
        # complement on A's mask, estimated directly
        mask = {(u, t) for u, t, _, _ in ya_masked}
        yc_common = [r for r in yc if (r[0], r[1]) in mask]
        pc, rc, nc = run(d, yc_common)
        naive = pt["result"]["overall"]["att"] - pa["result"]["overall"]["att"]
        direct = pc["result"]["overall"]["att"]
        self.assertGreater(abs(naive - direct), 1e-6)              # t - a is not the complement's ATT when the cell sets differ
        A, T = as_entry(pa, na, n_cells=len(ya_masked)), as_entry(pt, nt)
        codes = [c for c, _ in D.structure_failures(A, T)]
        self.assertIn("CELL_SETS_DIFFER", codes)                   # and the builder codes it instead of using it
        # the identity does hold if the total is re-estimated on A's mask
        pt2, rt2, nt2 = run(d, [r for r in yt if (r[0], r[1]) in mask])
        self.assertAlmostEqual(pt2["result"]["overall"]["att"] - pa["result"]["overall"]["att"], direct, places=9)

    @classmethod
    def tearDownClass(cls):
        after = pycache_dirs()
        assert after == cls.before, f"__pycache__ appeared under scripts/l2_pipeline: {sorted(set(after) - set(cls.before))}"


if __name__ == "__main__":
    unittest.main(verbosity=1)
