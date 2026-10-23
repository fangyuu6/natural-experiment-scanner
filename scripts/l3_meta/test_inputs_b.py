"""Tests for the L3 package B input builders (standard library; reads the produced files under data/l3_meta).

Run:  python3 -B -m unittest scripts.l3_meta.test_inputs_b -v     (from the repo root; or python3 -B scripts/l3_meta/test_inputs_b.py)
"""
import collections
import csv
import json
import math
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inputs_b_common as C  # noqa: E402
import build_covariates as BC  # noqa: E402
import build_party_histories as BP  # noqa: E402
import build_n1_sets as BN  # noqa: E402

ROOT = C.ROOT
COV = ROOT / "data/l3_meta/covariates"
PARTY = ROOT / "data/l3_meta/party_coding"
Q2 = ROOT / "data/l3_meta/q2_join"
N1 = ROOT / "data/l3_meta/n1_sets"


def rows(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Units(unittest.TestCase):
    def test_nearest_census_ties_take_earlier(self):
        cs = [1960, 1970, 1980, 1990, 2000, 2010, 2020]
        self.assertEqual([BC.nearest_census(y, cs) for y in (1960, 1964, 1965, 1966, 1995, 2005, 2015, 2020, 2025)],
                         [1960, 1960, 1960, 1970, 1990, 2000, 2010, 2020, 2020])

    def test_unified_category(self):
        self.assertEqual(BC._cat("D", "D", "D")[2], "D_unified")
        self.assertEqual(BC._cat("R", "R", "R")[:2], (0, 1))
        self.assertEqual(BC._cat("I", "D", "D")[2], "divided")
        self.assertEqual(BC._cat("D", "split", "D")[2], "divided")
        self.assertEqual(BC._cat(None, "D", "D")[2], "unknown")

    def test_annual_path_dated_onset(self):
        rec = {"status": "treated", "best_evidence_start_year": 2005, "start_period": 2006,
               "operative_intervals": [{"start": "2005-07-01", "end": None}]}
        path, ev, _ = BP.annual_path(rec, 2000, 2010)
        self.assertEqual(ev, "dated_onset")
        self.assertTrue(all(path[y] is False for y in range(2000, 2005)))
        self.assertTrue(all(path[y] is True for y in range(2005, 2011)))

    def test_annual_path_status_spells_keep_unknown(self):
        rec = {"status": "treated", "history_uncertain": True, "operative_intervals": [
            {"start": "2000-01-01", "end": "2004-12-31", "status": "unknown"},
            {"start": "2005-01-01", "end": "2005-12-31", "status": "exposed"},
            {"start": "2006-01-01", "end": "2010-12-31", "status": "unknown"}]}
        path, ev, _ = BP.annual_path(rec, 2000, 2010)
        self.assertEqual(ev, "status_spells")
        self.assertEqual([path[y] for y in (2000, 2004, 2005, 2006, 2010)], [None, None, True, None, None])

    def test_annual_path_never_unknown_and_left_censored(self):
        self.assertEqual(BP.annual_path({"status": "never_treated", "start_period": None, "operative_intervals": []}, 2000, 2003)[1], "never_certified")
        self.assertEqual(BP.annual_path(None, 2000, 2003)[1], "unknown")
        self.assertEqual(BP.annual_path({"status": "treated", "history_uncertain": True, "operative_intervals": []}, 2000, 2003)[1], "unknown")
        path, _, _ = BP.annual_path({"status": "treated", "best_evidence_start_year": 1990}, 2000, 2003)
        self.assertTrue(all(v is True for v in path.values()))

    def test_lower_median(self):
        self.assertEqual(BN.weighted_lower_median([2001, 2009, 2005, 2003]), 2003)
        self.assertIsNone(BN.weighted_lower_median([]))


class CovariateFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = rows(COV / "state_year_v2.csv")

    def test_shape_and_keys(self):
        self.assertEqual(len(self.r), 51 * 66)
        self.assertEqual(len({(x["state"], x["year"]) for x in self.r}), len(self.r))
        self.assertEqual({int(x["year"]) for x in self.r}, set(range(1960, 2026)))

    def test_party_coding(self):
        for x in self.r:
            if x["dem_unified_jan1"] == "1":
                self.assertEqual(x["party_cat_jan1"], "D_unified")
                self.assertEqual(x["rep_unified_jan1"], "0")
            if x["state"] == "NE" and int(x["year"]) <= 2024:
                self.assertEqual((x["dem_unified_jan1"], x["rep_unified_jan1"], x["party_cat_jan1"]), ("0", "0", "nonpartisan_NE"))
            if x["state"] == "DC" or int(x["year"]) == 2025:
                self.assertEqual(x["dem_unified_jan1"], "")

    def test_income_urban_ideology(self):
        for x in self.r:
            real = float(x["pcpi_real_2024usd"])
            self.assertAlmostEqual(math.log(real), float(x["ln_pcpi_real_2024usd"]), places=5)
            self.assertTrue(0 < float(x["urban_share"]) <= 1)
            if x["state"] != "DC":
                self.assertTrue(0 < float(x["citizen_ideology"]) < 100)
                self.assertEqual(int(x["ideology_source_year"]), min(int(x["year"]), 2016))

    def test_adjacency(self):
        a = rows(COV / "state_adjacency_v2.csv")
        self.assertEqual(len(a), 112)
        self.assertEqual(sum(int(x["land_border_rule_v1"]) for x in a), 107)
        deg = collections.Counter()
        for x in a:
            if x["land_border_rule_v1"] == "1":
                deg[x["state_a"]] += 1; deg[x["state_b"]] += 1
        self.assertNotIn("AK", deg); self.assertNotIn("HI", deg)
        self.assertEqual(deg["TN"], 8); self.assertEqual(deg["MO"], 8)


class PartyArchive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = rows(PARTY / "risk_state_year_v2.csv")
        cls.s = json.loads((PARTY / "design_summary_v2.json").read_text())

    def test_rows_end_at_first_adoption(self):
        g = collections.defaultdict(list)
        for x in self.r:
            g[(x["design_id"], x["state"])].append(x)
        for key, rs in g.items():
            years = [int(x["year"]) for x in rs]
            self.assertEqual(years, list(range(years[0], years[0] + len(years))), key)
            adopt = [int(x["adopt"]) for x in rs]
            self.assertLessEqual(sum(adopt), 1, key)
            if sum(adopt):
                self.assertEqual(adopt[-1], 1, key)

    def test_twelve_and_extension(self):
        self.assertEqual(sum(1 for s in self.s if s["set"] == "twelve_v2"), 12)
        self.assertTrue(all(s["treatment_audit_passed"] is True for s in self.s))

    def test_counts_match_rows(self):
        by = collections.Counter()
        for x in self.r:
            if x["in_U"] == "1" and x["after_first_adoption"] == "1":
                by[x["design_id"]] += 1
        for s in self.s:
            self.assertEqual(by[s["design_id"]], s["rows_primary"], s["design_id"])


class Q2Join(unittest.TestCase):
    def test_pairs_and_sheet(self):
        p = rows(Q2 / "q2_pairs_v1.csv")
        sh = rows(Q2 / "q2_coding_sheet_v1.csv")
        self.assertEqual(len({x["pair_id"] for x in p}), len(p))
        new = {x["pair_id"] for x in p if x["known_in_targets"] == "0"}
        self.assertEqual({x["pair_id"] for x in sh}, new)
        self.assertNotIn("l2_state", sh[0])
        self.assertNotIn("has_s_pre", sh[0])
        self.assertTrue(all(x["coder_id"] == "" and x["policy_scope_dose"] == "" for x in sh))


class N1Sets(unittest.TestCase):
    def test_sets_are_consistent(self):
        d = json.loads((N1 / "n1_sets_v1.json").read_text())["designs"]
        self.assertGreater(len(d), 50)
        for r in d:
            U, T = set(r["U"]), set(r["T"])
            self.assertTrue(T <= U, r["design_id"])
            self.assertEqual(sorted(u for us in r["T_cohorts"].values() for u in us), sorted(T), r["design_id"])
            self.assertFalse(set(r["R_known_part"]) & set(r["in_force_last_panel_year"]), r["design_id"])
            self.assertTrue(set(r["R_known_part"]) <= U, r["design_id"])
            if r["eval_n1_spec"]:
                self.assertTrue(len(U) >= 20 and len(T) >= 3 and r["R_resolved"] and len(r["R_known_part"]) >= 3, r["design_id"])


if __name__ == "__main__":
    unittest.main()
