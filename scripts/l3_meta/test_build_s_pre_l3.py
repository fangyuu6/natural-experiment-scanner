#!/usr/bin/env python3
"""Unit checks of build_s_pre_l3.py on SYNTHETIC inputs only (no repository data).

Run:  python3 -I -B scripts/l3_meta/test_build_s_pre_l3.py
"""
import csv
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_s_pre_l3 as B  # noqa: E402


def manual_sd(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


class Pure(unittest.TestCase):
    def test_sample_sd_matches_two_pass_formula(self):
        xs = [1.0, 2.0, 4.0, 8.0, 16.5]
        self.assertAlmostEqual(B.sample_sd(xs), manual_sd(xs), places=12)

    def test_sample_sd_needs_two_values(self):
        self.assertIsNone(B.sample_sd([3.0]))
        self.assertIsNone(B.sample_sd([]))

    def test_rule_p_never_treated_contribute_all_years_treated_only_before_cohort(self):
        mask = [["A", 2000], ["A", 2001], ["A", 2002], ["A", 2003], ["B", 2000], ["B", 2001], ["B", 2003], ["N", 2000], ["N", 2001], ["N", 2002]]
        cells = [{"cohort": 2002, "treated": ["A"]}, {"cohort": 2003, "treated": ["B"]}, {"cohort": 2002, "treated": ["A"]}]
        keys, cohort = B.rule_p_keys(mask, cells)
        self.assertEqual(cohort, {"A": 2002, "B": 2003})
        # A: 2000, 2001 (not 2002, 2003); B: 2000, 2001 (2003 is its cohort, excluded); N: all three
        self.assertEqual(keys, [("A", 2000), ("A", 2001), ("B", 2000), ("B", 2001), ("N", 2000), ("N", 2001), ("N", 2002)])

    def test_rule_p_conflicting_cohorts_raise(self):
        with self.assertRaises(ValueError):
            B.rule_p_keys([["A", 2000]], [{"cohort": 2002, "treated": ["A"]}, {"cohort": 2003, "treated": ["A"]}])

    def test_rule_p_mask_period_strings_are_ints(self):
        keys, _ = B.rule_p_keys([["A", "2000"], ["A", "2005"]], [{"cohort": 2003, "treated": ["A"]}])
        self.assertEqual(keys, [("A", 2000)])

    def test_rel_diff(self):
        self.assertEqual(B.rel_diff(2.0, 2.0), 0.0)
        self.assertAlmostEqual(B.rel_diff(2.2, 2.0), 0.1)
        self.assertEqual(B.rel_diff(1.0, 0.0), float("inf"))

    def test_validation_gate_passes_when_every_applicable_design_reproduces(self):
        rows = [dict(applicable=True, n_equal=True, rel_diff=0.0), dict(applicable=True, n_equal=True, rel_diff=5e-10),
                dict(applicable=False, n_equal=None, rel_diff=None)]
        v = B.validation_verdict(rows)
        self.assertTrue(v["gate_passed"])
        self.assertEqual((v["applicable_n"], v["passed_n"], v["not_applicable_n"]), (2, 2, 1))

    def test_validation_gate_fails_on_one_n_mismatch_or_one_large_difference(self):
        self.assertFalse(B.validation_verdict([dict(applicable=True, n_equal=True, rel_diff=0.0), dict(applicable=True, n_equal=False, rel_diff=0.0)])["gate_passed"])
        self.assertFalse(B.validation_verdict([dict(applicable=True, n_equal=True, rel_diff=2e-9)])["gate_passed"])

    def test_validation_gate_fails_when_nothing_is_applicable(self):
        self.assertFalse(B.validation_verdict([dict(applicable=False, n_equal=None, rel_diff=None)])["gate_passed"])
        self.assertFalse(B.validation_verdict([])["gate_passed"])

    def test_clean_pre_cannabis_window_exclusion_and_law_share(self):
        rows = [dict(unit=u, period=str(t), law_share_of_year=s, y="1.0")
                for u, t, s in [("A", 2000, "0"), ("A", 2001, "0.4"), ("A", 2002, "1"), ("B", 2000, "0"), ("B", 1999, "0"), ("C", 2000, "0"), ("C", 2001, "")]]
        retained, clean = B.clean_pre_cannabis(rows, [2000, 2001, 2002], {"C"})
        self.assertEqual(sorted((r["unit"], r["period"]) for r in retained), [("A", "2000"), ("A", "2001"), ("A", "2002"), ("B", "2000")])
        self.assertEqual(sorted((r["unit"], r["period"]) for r in clean), [("A", "2000"), ("B", "2000")])

    def test_clean_pre_castle_treated_before_adoption_year_controls_always(self):
        rows = [dict(state_abbr=s, year=str(y), y="1") for s in ("T", "K") for y in (2000, 2001, 2002, 2003)] + [dict(state_abbr="T", year="1999", y="1")]
        inwin, clean = B.clean_pre_castle(rows, [2000, 2001, 2002, 2003], {"T": 2002, "K": None})
        self.assertEqual(len(inwin), 8)
        self.assertEqual(sorted((r["state_abbr"], r["year"]) for r in clean),
                         [("K", "2000"), ("K", "2001"), ("K", "2002"), ("K", "2003"), ("T", "2000"), ("T", "2001")])


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Prototypes(unittest.TestCase):
    """read_cannabis / read_castle on a tiny synthetic prototype with absolute paths."""

    def cannabis_fixture(self, tmp, tamper=False):
        years = list(range(2000, 2004))
        units = {"CO": None, "AA": 2002, "BB": None, "CC": 2010}   # CO excluded; AA treated (law from 2002, partial 2002 -> share .5); CC future adopter
        panel, ytxt = [], 0
        for u, e in units.items():
            for t in years:
                share = 0 if (e is None or t < e) else (0.5 if t == e else 1)
                ytxt += 1
                panel.append(dict(unit=u, period=t, y=str(1.0 + 0.1 * ytxt), first_treated=("" if e is None else str(e + 1 if e <= 2003 else e)), law_share_of_year=share, in_primary_window=1))
        pp, tp, rp = tmp / "panel.csv", tmp / "treat.csv", tmp / "res.json"
        write_csv(pp, panel, ["unit", "period", "y", "first_treated", "law_share_of_year", "in_primary_window"])
        write_csv(tp, [dict(abbr=u, effective_date=("" if e is None else f"{e}-03-01")) for u, e in units.items()], ["abbr", "effective_date"])
        res = {"primary_estimates": {"primary/y/never_treated": {"calendar_years": years, "unit_exclusions": [{"unit": "CO"}]}},
               "source_hashes": [{"path": str(pp), "expected": ("0" * 64 if tamper else sha(pp))}, {"path": str(tp), "expected": sha(tp)}]}
        rp.write_text(json.dumps(res))
        return {"design_id": "toy:cannabis", "panel": str(pp), "treatment": str(tp), "result": str(rp),
                "expected": {"window": [2000, 2003], "excluded": ["CO"], "units": 3, "unit_years": 12, "exposed": 2, "partial": 1, "clean": 10, "s1": 11, "treated": 1, "controls": 2}}

    def test_cannabis_read_counts_and_sd(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cannabis_fixture(Path(d))
            out = B.read_cannabis(cfg)
        self.assertTrue(out["checks_passed"], [c for c in out["structural_checks"] if not c["passed"]])
        self.assertEqual(out["n"], 10)
        self.assertEqual(out["sensitivity_S1"]["n"], 11)
        # y of clean rows: units AA,BB,CC (CO excluded), rows with law_share 0
        vals = [1.0 + 0.1 * k for k in range(1, 17)]
        # order of generation: CO (1-4), AA (5-8), BB (9-12), CC (13-16); AA exposed from 2002 (index 7, 8 -> k = 7, 8)
        clean = [vals[k - 1] for k in range(5, 17) if k not in (7, 8)]
        self.assertAlmostEqual(out["s_pre_L3"], manual_sd(clean), places=12)

    def test_cannabis_hash_mismatch_fails_the_checks(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cannabis_fixture(Path(d), tamper=True)
            out = B.read_cannabis(cfg)
        self.assertFalse(out["checks_passed"])

    def test_cannabis_law_share_inconsistent_with_effective_date_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.cannabis_fixture(Path(d))
            with open(cfg["panel"], newline="") as fh:
                rows = list(csv.DictReader(fh))
            for r in rows:
                if r["unit"] == "BB" and r["period"] == "2001":
                    r["law_share_of_year"] = "0.3"           # exposure with no effective date
            write_csv(cfg["panel"], rows, list(rows[0].keys()))
            res = json.loads(Path(cfg["result"]).read_text())
            res["source_hashes"][0]["expected"] = sha(cfg["panel"])   # keep the hash check honest, isolate the law-share check
            Path(cfg["result"]).write_text(json.dumps(res))
            out = B.read_cannabis(cfg)
        bad = [c["check"] for c in out["structural_checks"] if not c["passed"]]
        self.assertTrue(any("law_share_of_year" in c for c in bad), bad)

    def castle_fixture(self, tmp):
        years = list(range(2000, 2004))
        states = {"T1": (2001, 2002), "K1": (None, None), "K2": (None, None)}   # adoption_year, ruleA year
        panel = []
        k = 0
        for s, (a, f) in states.items():
            for t in years:
                k += 1
                panel.append(dict(state_abbr=s, year=t, y=str(2.0 + 0.05 * k), first_treated=("" if f is None else str(f))))
        pp, tp, rp, mp = tmp / "panel.csv", tmp / "treat.csv", tmp / "res.json", tmp / "manifest.json"
        write_csv(pp, panel, ["state_abbr", "year", "y", "first_treated"])
        write_csv(tp, [dict(state_abbr=s, group=("treated" if a else "control"), adoption_year=(a or ""), exposure_year_ruleA_first_full_year=(f or "")) for s, (a, f) in states.items()],
                  ["state_abbr", "group", "adoption_year", "exposure_year_ruleA_first_full_year"])
        rp.write_text(json.dumps({"primary_estimates": [{"calendar_years": years, "calendar_adoptions": {"T1": 2002, "K1": None, "K2": None}}]}))
        mp.write_text(json.dumps({"files": {str(pp): sha(pp), str(tp): sha(tp)}}))
        return {"design_id": "toy:castle", "panel": str(pp), "treatment": str(tp), "result": str(rp), "manifest": str(mp),
                "expected": {"window": [2000, 2003], "units": 3, "unit_years": 12, "treated": 1, "controls": 2, "clean": 9, "s1": 10}}

    def test_castle_read_counts_and_sd(self):
        with tempfile.TemporaryDirectory() as d:
            out = B.read_castle(self.castle_fixture(Path(d)))
        self.assertTrue(out["checks_passed"], [c for c in out["structural_checks"] if not c["passed"]])
        self.assertEqual((out["n"], out["sensitivity_S1"]["n"]), (9, 10))
        vals = [2.0 + 0.05 * k for k in range(1, 13)]
        clean = [vals[i] for i in range(12) if not (i in (1, 2, 3))]       # T1 = first four rows; adoption 2001 -> only 2000 clean
        self.assertAlmostEqual(out["s_pre_L3"], manual_sd(clean), places=12)

    def test_castle_wrong_cohort_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self.castle_fixture(Path(d))
            Path(cfg["result"]).write_text(json.dumps({"primary_estimates": [{"calendar_years": list(range(2000, 2004)),
                                                                              "calendar_adoptions": {"T1": 2003, "K1": None, "K2": None}}]}))
            out = B.read_castle(cfg)
        self.assertFalse(out["checks_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
