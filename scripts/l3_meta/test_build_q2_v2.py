#!/usr/bin/env python3
"""Unit checks of build_q2_v2.py on SYNTHETIC inputs only (no L2 file, no real estimate).

Run:  python3 -I -B scripts/l3_meta/test_build_q2_v2.py      (standard library only)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_q2 as V1  # noqa: E402
import build_q2_v2 as B  # noqa: E402
import test_build_q2 as T  # noqa: E402  (synthetic fixtures of the V1 tests)


def read(did, n=100, s=0.5, used=True, s1=0.52):
    return {"design_id": did, "n": n, "s_pre_L3": s, "used_in_q2": used, "sensitivity_S1": {"n": n + 5, "s_pre_L3": s1}}


class OverlayTests(unittest.TestCase):
    def table(self, **s_pre):
        return {"designs": [{"design_id": k, "b": 0.05, "noise_sd": 0.02, "s_pre": v} for k, v in s_pre.items()]}

    def test_l2_export_has_precedence_and_is_never_replaced(self):
        out, src = B.overlay_scale(self.table(a=0.3, b=None), [read("a", s=9.0), read("b", s=0.5)])
        by = {d["design_id"]: d for d in out["designs"]}
        self.assertEqual((by["a"]["s_pre"], src["a"]), (0.3, "l2_export"))
        self.assertEqual((by["b"]["s_pre"], src["b"]), (0.5, "l3_read"))

    def test_unused_or_invalid_read_leaves_the_scale_undefined(self):
        out, src = B.overlay_scale(self.table(a=None, b=None, c=None, d=None), [read("a", used=False), read("b", s=0.0), read("c", s=float("nan")), ])
        by = {d["design_id"]: d for d in out["designs"]}
        for k in "abcd":
            self.assertIsNone(by[k]["s_pre"])
            self.assertIsNone(src[k])

    def test_input_table_is_not_mutated(self):
        t = self.table(a=None)
        B.overlay_scale(t, [read("a")])
        self.assertIsNone(t["designs"][0]["s_pre"])

    def test_nonpositive_export_is_not_a_scale(self):
        out, src = B.overlay_scale(self.table(a=0.0), [read("a", s=0.5)])
        self.assertEqual(src["a"], "l3_read")                      # a zero export is not finite-positive, so the L3 read applies


class WorldTests(unittest.TestCase):
    def world(self):
        designs, v3, entries = [], [], []
        # d0: L2 export; d1, d2: no export, L3 read; d3: no scale at all
        for i, s in enumerate([0.2, None, None, None]):
            entries.append(T.entry(f"t{i}", "strict_possible", [T.cand(1, 2013, True, True, point=0.10, direction="+1")]))
            designs.append(T.live_row(f"d{i}", fam=f"fam{i}", tid=f"t{i}", cls="strict_possible", point=0.04))
            v3.append(T.v3rec(f"d{i}", b=0.04, noise=0.02, s_pre=s))
        return {"designs": designs}, {"designs": v3}, {"designs": entries}

    def test_g_with_mixed_sources_and_g_l2_only(self):
        live, v3, targets = self.world()
        reads = [read("d1", s=0.3, s1=0.33), read("d2", s=0.6, s1=0.5)]
        over, src = B.overlay_scale(v3, reads)
        res, rows, _ = V1.assemble(live, over, targets, [], [])
        self.assertEqual((res["n"], res["F"]), (3, 3))
        self.assertEqual(res["coverage_codes"], {"EVAL": 3, "SCALE_UNDEFINED": 1})
        blocks = B.v2_blocks(rows, src, reads)
        gs = [(0.10 - 0.04) / s for s in (0.2, 0.3, 0.6)]
        self.assertAlmostEqual(res["G"]["value"], sum(gs) / 3)
        self.assertEqual((blocks["G_l2_export_only"]["n"], blocks["G_scale_sources"]["n_l3_read"]), (1, 2))
        self.assertAlmostEqual(blocks["G_l2_export_only"]["value"], gs[0])
        s1 = [(0.10 - 0.04) / 0.33, (0.10 - 0.04) / 0.5]
        self.assertAlmostEqual(blocks["G_S1_scale_variant"]["value"], sum(s1) / 2)
        self.assertTrue(blocks["G_S1_scale_variant"]["complete"])
        sources = {r["design_id"]: r["scale_source"] for r in rows}
        self.assertEqual(sources, {"d0": "l2_export", "d1": "l3_read", "d2": "l3_read", "d3": None})

    def test_no_l2_export_means_g_l2_only_is_none(self):
        live, v3, targets = self.world()
        v3["designs"][0]["s_pre"] = None
        reads = [read("d0", s=0.4)]
        over, src = B.overlay_scale(v3, reads)
        res, rows, _ = V1.assemble(live, over, targets, [], [])
        blocks = B.v2_blocks(rows, src, reads)
        self.assertEqual(blocks["G_l2_export_only"], {"definition": blocks["G_l2_export_only"]["definition"], "n": 0, "value": None})
        self.assertEqual(res["n"], 1)
        self.assertEqual(res["decision"]["status"], "DESCRIPTIVE_GAP_ONLY")
        self.assertIsNone(res["decision"]["p_value"])

    def test_gate_failure_means_v2_equals_v1(self):
        live, v3, targets = self.world()
        reads = [read("d1", used=False), read("d2", used=False)]       # the builder sets used_in_q2 false when the gate fails
        over, src = B.overlay_scale(v3, reads)
        res_v2, rows_v2, _ = V1.assemble(live, over, targets, [], [])
        res_v1, rows_v1, _ = V1.assemble(live, v3, targets, [], [])
        self.assertEqual(res_v2["coverage_codes"], res_v1["coverage_codes"])
        self.assertEqual(res_v2["G"]["value"], res_v1["G"]["value"])

    def test_delta_and_other_fields_compare_with_v1(self):
        live, v3, targets = self.world()
        reads = [read("d1", s=0.3)]
        res1, rows1, _ = V1.assemble(live, v3, targets, [], [])
        over, src = B.overlay_scale(v3, reads)
        res2, rows2, _ = V1.assemble(live, over, targets, [], [])
        for r in rows2:                                   # as the builder does, after v2_blocks
            r["scale_source"] = src.get(r["design_id"])
        v1_designs = [V1.clean(dict(r)) for r in rows1]
        delta = B.delta_vs_v1(rows2, v1_designs)
        self.assertEqual(delta, [{"design_id": "d1", "v1": "SCALE_UNDEFINED", "v2": "EVAL"}])
        self.assertEqual(B.other_fields_equal(rows2, v1_designs), [])
        rows2[3]["b"] = 0.99                              # a change outside the allowed fields is reported
        self.assertEqual(B.other_fields_equal(rows2, v1_designs), ["d3:b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
