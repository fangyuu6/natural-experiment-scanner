"""Checks of build_d3_v2.py on INVENTED entries (no repo data file is read, nothing is written).

  * the entry made for the total from operator estimates gives exactly the numbers of D3-V1's arithmetic on the full entry;
  * the pair codes of D3-V1 (cell sets, nesting, denominators, support) apply to it;
  * the design code order of D3-V2 and the engine floor of E1.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_build_d3_v2.py
"""
import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import build_d3 as D3  # noqa: E402
import build_d3_v2 as V2  # noqa: E402
import test_build_d3 as H  # noqa: E402  (helpers that make invented L2-like entries)


def rec_of(T):
    return dict(t=T["result"]["overall"]["att"], t_cell_atts=[c["att"] for c in T["result"]["group_time"]], dt=list(T["noise_floor"]["permutation_draws"]))


class TotalEntry(unittest.TestCase):
    def test_roundtrip_equals_full_entry(self):
        A, T, c_overall, dc = H.make_pair(5, a_post=-1.0, c_post=0.6)
        T2 = V2.total_entry(A, rec_of(T))
        self.assertEqual(D3.structure_failures(A, T2), [])
        n1, p1 = D3.pair_numbers(A, T)
        n2, p2 = D3.pair_numbers(A, T2)
        self.assertEqual((p1, p2), ([], []))
        for k in ("a", "t", "c", "sd_c", "cutoff_c", "s", "mu", "tau", "Z", "p_sub", "omega", "net_ratio"):
            self.assertAlmostEqual(n1[k], n2[k], places=12, msg=k)
        self.assertAlmostEqual(n2["c"], c_overall, places=12)

    def test_target_is_not_altered(self):
        A, T, _, _ = H.make_pair(6)
        before = copy.deepcopy(A)
        V2.total_entry(A, rec_of(T))
        self.assertEqual(A, before)

    def test_analyze_pair_codes_follow_d3_v1(self):
        A, T, _, _ = H.make_pair(7, a_post=-1.0, c_post=0.6)
        T2 = V2.total_entry(A, rec_of(T))
        design = H.design_row()
        pa, pt = H.panels_ok()
        ok = D3.analyze_pair(design, H.P3, "primary", A, ("total_from_operators", T2), lambda *a: (pa, pt), H.CATALOG)
        self.assertEqual(ok["code"], "EVAL")
        # the total below the target at a common cell
        pa2, pt2 = H.panels_ok(t_vals=lambda i: 0.5)
        bad = D3.analyze_pair(design, H.P3, "primary", A, ("total_from_operators", T2), lambda *a: (pa2, pt2), H.CATALOG)
        self.assertEqual(bad["code"], "NOT_NESTED")
        # different denominators
        pa3, pt3 = H.panels_ok()
        pt3 = {k: (v[0], v[1] + 10.0) for k, v in pt3.items()}
        den = D3.analyze_pair(design, H.P3, "primary", A, ("total_from_operators", T2), lambda *a: (pa3, pt3), H.CATALOG)
        self.assertEqual(den["code"], "DENOMINATORS_DIFFER")
        # count scale: no denominators on either side, the identity still holds
        pa4 = {k: (v[0], None) for k, v in pa.items()}
        pt4 = {k: (v[0], None) for k, v in pt.items()}
        cnt = D3.analyze_pair(design, H.P3, "primary", A, ("total_from_operators", T2), lambda *a: (pa4, pt4), H.CATALOG)
        self.assertEqual(cnt["code"], "EVAL")

    def test_design_code_order(self):
        self.assertEqual(V2.design_code([]), "NO_PAIR")
        self.assertEqual(V2.design_code(["REPRO_FAIL", "EVAL"]), "EVAL")
        self.assertEqual(V2.design_code(["CELL_SETS_DIFFER", "NO_TOTAL_SERIES"]), "NO_TOTAL_SERIES")
        self.assertEqual(V2.design_code(["SUPPORT", "REPRO_FAIL"]), "REPRO_FAIL")
        self.assertEqual(V2.design_code(["OTHER"]), "OTHER")


class EngineFloor(unittest.TestCase):
    def test_e1_runs_only_from_eight_pairs_in_five_families(self):
        pairs = []
        for i in range(8):
            A, T, _, _ = H.make_pair(100 + i, a_post=-1.0, c_post=0.3)
            nums, probs = D3.pair_numbers(A, T)
            self.assertEqual(probs, [])
            pairs.append(dict(policy_family=f"f{i}", numbers=nums, design_id=f"d{i}", pair="P1"))
        e = D3.e1_summary(pairs)
        self.assertTrue(e["run"])
        self.assertEqual(e["seed"], [20261011, 33])
        self.assertFalse(D3.e1_summary(pairs[:7])["run"])
        self.assertFalse(D3.e1_summary([dict(p, policy_family="same" if j % 2 else "other") for j, p in enumerate(pairs)])["run"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
