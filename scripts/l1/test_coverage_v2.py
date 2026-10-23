"""Checks for scripts/l1/coverage_v2.py: rule v2, the recovery tiers, the prepared SQL, and (when present) the
counts of the real run.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/l1 -p 'test_*.py'

The synthetic tests need no data. RealRunCounts reads data/pipeline/coverage_v2/checks_v2.json, which the script
writes (python3 -B scripts/l1/coverage_v2.py), and is skipped when that file is missing. Set L1_TEST_OUT to a
directory to keep the synthetic outputs there instead of a temporary directory.
"""
import contextlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("coverage_v2", HERE / "coverage_v2.py")
cv = importlib.util.module_from_spec(spec)
sys.modules["coverage_v2"] = cv
spec.loader.exec_module(cv)
REPO = HERE.parents[1]
UP = cv.UpRec


def rec(date="2019-03-04", status="enacted", title="A title", bill_id="", number="HB1", session="Regular Session",
        legiscan="", state_link="", state="CA", description="", source="both"):
    return UP(source, state, bill_id, number, session, title, description, status, date, legiscan, state_link)


class HelperTests(unittest.TestCase):
    def test_parse_year_like_v1(self):
        self.assertEqual(cv.parse_year("2019"), 2019)
        self.assertEqual(cv.parse_year("2019.0"), 2019)
        self.assertIsNone(cv.parse_year(""))
        self.assertIsNone(cv.parse_year(None))
        self.assertIsNone(cv.parse_year("n/a"))

    def test_upstream_key_follows_row_to_policy(self):
        self.assertEqual(cv.upstream_key({"bill_id": " 672684 ", "state": "AK", "bill_number": "HB1"}), ("672684", "bill_id"))
        self.assertEqual(cv.upstream_key({"bill_id": "", "state": "ar", "bill_number": " HB1001 ", "session_name": "91st General Assembly"}),
                         ("AR-HB1001-91st General Assembly", "state-number-session"))
        # session_title is the fallback when session_name is blank; blank parts are left out
        self.assertEqual(cv.upstream_key({"state": "AZ", "bill_number": "", "session_name": "", "session_title": "2026"})[0], "AZ-2026")
        self.assertEqual(cv.upstream_key({})[0], "row")

    def test_date_is_first_ten_characters(self):
        self.assertEqual(cv.parsed_date(rec(date="2023-06-12T13:55:00+00:00")), "2023-06-12")
        self.assertEqual(cv.parsed_date(rec(date="2019-03-04")), "2019-03-04")
        self.assertIsNone(cv.parsed_date(rec(date="")))
        self.assertIsNone(cv.parsed_date(rec(date="03/04/2019")))
        self.assertIsNone(cv.parsed_date(rec(date="2019-13-45")))

    def test_title_chain_and_line_breaks(self):
        self.assertEqual(cv.clean_title(rec(title="  Tipping contract.\nLeg. agenda 10/11/16 ")), "Tipping contract. Leg. agenda 10/11/16")
        self.assertEqual(cv.clean_title(rec(title="", description="From the description")), "From the description")
        self.assertEqual(cv.clean_title(rec(title="", description="", number="HB9")), "HB9")
        self.assertEqual(cv.clean_title(rec(title="", description="", number="", bill_id="77")), "77")
        self.assertEqual(cv.clean_title(rec(title="", description="", number="", bill_id="")), "Untitled policy")
        self.assertEqual(cv.clean_title(rec(title="a \r\n\r\nb")), "a     b")

    def test_sql_literal_escapes_quotes(self):
        self.assertEqual(cv.sql_str("O'Brien's act"), "'O''Brien''s act'")
        self.assertEqual(cv.sql_str("a;b -- c"), "'a;b -- c'")

    def test_output_dir_guard(self):
        for bad in ("deploy_output", "deploy_output/x", "data/output", "data/l2_pipeline/y", "src/output", "docs/plans", "scripts/l1"):
            with self.assertRaises(SystemExit):
                cv.assert_safe_out_dir(REPO / bad)
        cv.assert_safe_out_dir(REPO / "data/pipeline/coverage_v2")
        cv.assert_safe_out_dir(Path(tempfile.gettempdir()) / "l1_anything")


class RuleTests(unittest.TestCase):
    @staticmethod
    def years(d):
        return Counter({y: n for y, n in d.items()})

    def test_rule_v1_thin_and_first_year(self):
        # 40 rows spread over the window: thin, has 2016
        r, c = cv.rule_v1(self.years({2016: 5, 2018: 18, 2022: 13, 2023: 1, 2026: 1, 2020: 1, 2019: 1}))
        self.assertEqual(c, ["thin"])
        self.assertIn("thin coverage: 40 state-law rows in 2016-2026 (< 300)", r[0])
        # odd-year legislature with 368 rows: not thin, no 2016 rows
        r, c = cv.rule_v1(self.years({2017: 69, 2019: 59, 2021: 73, 2023: 82, 2025: 85}))
        self.assertEqual(c, ["no_first_year"])
        # ND-like: both
        _, c = cv.rule_v1(self.years({2017: 44, 2019: 53, 2021: 49, 2023: 63, 2025: 56}))
        self.assertEqual(c, ["thin", "no_first_year"])
        # 299 rows is thin, 300 is not
        self.assertEqual(cv.rule_v1(self.years({2016: 299}))[1], ["thin"])
        self.assertEqual(cv.rule_v1(self.years({2016: 300}))[1], [])

    def test_rule_v2_threshold_is_per_complete_biennium(self):
        ok = self.years({2017: 10, 2018: 10, 2019: 20, 2021: 12, 2022: 8, 2024: 25})
        by_block, reasons, codes, failing = cv.rule_v2(ok, 20)
        self.assertEqual(by_block, [20, 20, 20, 25])
        self.assertEqual((reasons, codes, failing), ([], [], []))
        # one row fewer in one biennium: that biennium fails, the others do not matter
        short = self.years({2017: 10, 2018: 9, 2019: 20, 2021: 20, 2023: 20})
        by_block, reasons, codes, failing = cv.rule_v2(short, 20)
        self.assertEqual(by_block, [19, 20, 20, 20])
        self.assertEqual(codes, ["sparse_block"])
        self.assertEqual(failing, ["2017-2018"])
        self.assertEqual(reasons, ["2017-2018 has 19 state-law rows (< 20)"])

    def test_rule_v2_ignores_2016_and_2025_26(self):
        c = self.years({2016: 0, 2017: 20, 2019: 20, 2021: 20, 2023: 20, 2025: 0, 2026: 0})
        self.assertEqual(cv.rule_v2(c)[2], [])

    def test_odd_year_legislature_passes_on_the_same_footing(self):
        c = self.years({2017: 20, 2019: 20, 2021: 20, 2023: 20, 2025: 20})
        self.assertEqual(cv.rule_v2(c)[2], [])
        self.assertEqual(cv.calendar_label(c), "odd_years_only")
        self.assertEqual(cv.calendar_label(self.years({2017: 20, 2018: 1})), "all_years")
        self.assertEqual(cv.calendar_label(Counter()), "all_years")

    def test_sensitivity_is_monotone(self):
        by_year = {"AA": self.years({2017: 12, 2018: 12, 2019: 12, 2020: 12, 2021: 12, 2022: 12, 2023: 12, 2024: 12})}
        s = cv.sensitivity(by_year)
        counts = [s[str(m)]["observed"] for m in cv.SENSITIVITY]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(s["20"]["observed"], 0)   # every other jurisdiction has no rows at all


class ResolveTests(unittest.TestCase):
    def test_tier1_unique_key(self):
        r = cv.resolve("http://x", [rec(date="2019-05-31T10:00:00+00:00", title="  Human trafficking\ntraining ")])
        self.assertEqual((r["tier"], r["evidence"], r["date"], r["year"], r["status"]), (1, "unique_key", "2019-05-31", 2019, "enacted"))
        self.assertEqual(r["title"], "Human trafficking training")

    def test_tier1_agreeing_records(self):
        r = cv.resolve("", [rec(date="2019-05-31", source="both"), rec(date="2019-05-31T00:00:00+00:00", source="openstates")])
        self.assertEqual((r["tier"], r["evidence"]), (1, "agreeing_records"))

    def test_ambiguous_key_needs_the_rows_own_url(self):
        recs = [rec(date="2016-03-23", legiscan="https://legiscan.com/CO/bill/HB1051/2016"),
                rec(date="2019-05-31", legiscan="https://legiscan.com/CO/bill/HB1051/2019", state_link="https://leg.colorado.gov/bills/hb19-1051")]
        self.assertIsNone(cv.resolve("", recs))
        self.assertIsNone(cv.resolve("https://example.org/other", recs))
        r = cv.resolve("https://legiscan.com/CO/bill/HB1051/2016", recs)
        self.assertEqual((r["tier"], r["evidence"], r["date"]), (2, "key+url", "2016-03-23"))
        r = cv.resolve("https://leg.colorado.gov/bills/hb19-1051", recs)
        self.assertEqual((r["tier"], r["date"]), (2, "2019-05-31"))

    def test_same_policy_id_different_urls_are_different_bills(self):
        recs = [rec(date="2016-03-23", legiscan="u16"), rec(date="2019-05-31", legiscan="u19")]
        self.assertEqual(cv.resolve("u16", recs)["year"], 2016)
        self.assertEqual(cv.resolve("u19", recs)["year"], 2019)

    def test_url_that_matches_records_with_different_dates_is_not_resolved(self):
        recs = [rec(date="2016-03-23", legiscan="same"), rec(date="2019-05-31", legiscan="same")]
        self.assertIsNone(cv.resolve("same", recs))

    def test_url_matching_records_that_agree_is_tier2(self):
        recs = [rec(date="2016-03-23", legiscan="same"), rec(date="2016-03-23", state_link="same"), rec(date="2019-05-31", legiscan="other")]
        r = cv.resolve("same", recs)
        self.assertEqual((r["tier"], r["n_selected"], r["n_upstream"]), (2, 2, 3))

    def test_loose_url_match(self):
        recs = [rec(date="2016-03-23", legiscan="https://www.legiscan.com/CO/bill/HB1/2016/"), rec(date="2019-05-31", legiscan="u19")]
        r = cv.resolve("http://legiscan.com/CO/bill/HB1/2016", recs)
        self.assertEqual((r["tier"], r["evidence"], r["year"]), (2, "key+url_loose", 2016))

    def test_unusable_records_stay_unresolved(self):
        self.assertIsNone(cv.resolve("u", []))
        self.assertIsNone(cv.resolve("u", [rec(date="")]))
        self.assertIsNone(cv.resolve("u", [rec(date="2019-05-31", status="")]))
        self.assertIsNone(cv.resolve("u", [rec(date="1850-01-01")]))
        # same date, different status under the key
        self.assertIsNone(cv.resolve("", [rec(date="2019-05-31", status="enacted"), rec(date="2019-05-31", status="proposed")]))
        # one dated record and one undated record under the key: not certain
        self.assertIsNone(cv.resolve("", [rec(date="2019-05-31"), rec(date="")]))

    def test_recover_rows_is_per_policy_id_and_url(self):
        up = {"X": [rec(date="2016-03-23", legiscan="u16"), rec(date="2019-05-31", legiscan="u19")]}
        rows = [{"pid": "X", "url": "u16"}, {"pid": "X", "url": "u19"}, {"pid": "X", "url": "u16"}, {"pid": "Y", "url": "u"}]
        out = cv.recover_rows(rows, up)
        self.assertEqual(sorted(out), [("X", "u16"), ("X", "u19"), ("Y", "u")])
        self.assertEqual((out[("X", "u16")]["year"], out[("X", "u19")]["year"], out[("Y", "u")]), (2016, 2019, None))


class CountTests(unittest.TestCase):
    @staticmethod
    def row(pid, jur, year, fam="f", url="u"):
        return {"pid": pid, "jurisdiction": jur, "year": year, "policy_family": fam, "url": url}

    def test_filters_in_v1_order_and_recovery(self):
        first = {
            "a": self.row("a", "AL State", "2018"),
            "b": self.row("b", "AL State", ""),                       # orphan, tier 1
            "c": self.row("c", "AL State", "", url="uc"),            # orphan, tier 2
            "d": self.row("d", "AL State", "", url="ud"),            # orphan, unresolved
            "e": self.row("e", "", ""),                              # orphan without jurisdiction -> state from the source bill
            "f": self.row("f", "US Federal", "2018"),                # not a state
            "g": self.row("g", "AL State", "2018", fam=" "),         # blank family
            "h": self.row("h", "AL State", "2031"),                  # outside the window
            "i": self.row("i", "AL State", "", url="ui"),            # recovered year outside the window
        }
        mk = lambda tier, y, st="AL": {"tier": tier, "year": y, "state": st}  # noqa: E731
        res = {("b", "u"): mk(1, 2019), ("c", "uc"): mk(2, 2020), ("d", "ud"): None, ("e", "u"): mk(1, 2021, "AK"),
               ("i", "ui"): mk(1, 2030)}
        by, out, drop, rec_ = cv.count_rows(first, {}, ())
        self.assertEqual(dict(by["AL"]), {2018: 1})
        self.assertEqual(drop["unparseable/blank year"], 4)          # b, c, d, i
        self.assertEqual(drop["not a state/DC jurisdiction"], 2)     # e (blank jurisdiction), f
        self.assertEqual(drop["blank policy_family"], 1)
        self.assertEqual(out["AL"], 1)
        by, out, drop, rec_ = cv.count_rows(first, res, (1, 2))
        self.assertEqual(dict(by["AL"]), {2018: 1, 2019: 1, 2020: 1})
        self.assertEqual(dict(by["AK"]), {2021: 1})
        self.assertEqual(out["AL"], 2)                               # h and the recovered 2030 row
        self.assertEqual(drop["unparseable/blank year"], 1)          # d stays dropped
        self.assertEqual({s: dict(c) for s, c in rec_.items()}, {"AL": {1: 1, 2: 1}, "AK": {1: 1}})
        by, _, _, rec_ = cv.count_rows(first, res, (1,))
        self.assertEqual(dict(by["AL"]), {2018: 1, 2019: 1})
        self.assertNotIn(2, rec_["AL"])


class SqlTests(unittest.TestCase):
    def db(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE policies (id INTEGER PRIMARY KEY, policy_id TEXT, title TEXT, jurisdiction TEXT, date TEXT, "
                    "year TEXT, status TEXT, url TEXT, source_family TEXT)")
        return con

    R16 = {"tier": 2, "date": "2016-03-23", "year": 2016, "status": "enacted", "title": "O'Brien's act", "state": "CO"}
    R19 = {"tier": 2, "date": "2019-05-31", "year": 2019, "status": "enacted", "title": "Second", "state": "CO"}

    def test_update_touches_only_the_matching_blank_row(self):
        con = self.db()
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "X", "Good", "CO State", "2019-05-31", "2019", "enacted", "u19", "State Legislation"),   # same id, has a year
            (2, "X", "", "CO State", "", "", "unknown", "u16", "State Legislation"),                      # to be recovered
            (3, "X", "", "CO State", "", None, "unknown", "u16", "State Legislation"),                    # NULL year, same key
            (4, "X", "", "CO State", "", "", "unknown", "u-else", "State Legislation"),                  # other url: untouched
            (5, "X", "", "CO State", "", "", "unknown", "u16", "Federal Legislation"),                    # other family: untouched
            (6, "Y", "", "CO State", "", "", "unknown", "u16", "State Legislation"),                      # other id: untouched
        ])
        before = {r[0]: r for r in con.execute("SELECT * FROM policies")}
        con.executescript(cv.update_statement("X", "u16", self.R16, False))
        after = {r[0]: r for r in con.execute("SELECT * FROM policies")}
        changed = {i for i in before if before[i] != after[i]}
        self.assertEqual(changed, {2, 3})
        self.assertEqual(after[2][2:7], ("O'Brien's act", "CO State", "2016-03-23", "2016", "enacted"))
        con.executescript(cv.update_statement("X", "u16", self.R16, False))   # idempotent
        self.assertEqual({r[0]: r for r in con.execute("SELECT * FROM policies")}, after)

    def test_two_rows_with_the_same_policy_id_get_their_own_values(self):
        con = self.db()
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "X", "", "CO State", "", "", "unknown", "u16", "State Legislation"),
            (2, "X", "", "CO State", "", "", "unknown", "u19", "State Legislation")])
        con.executescript(cv.update_statement("X", "u16", self.R16, False) + cv.update_statement("X", "u19", self.R19, False))
        self.assertEqual(con.execute("SELECT id, year FROM policies ORDER BY id").fetchall(), [(1, "2016"), (2, "2019")])

    def test_title_and_jurisdiction_are_only_filled_when_blank(self):
        con = self.db()
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "X", "Kept", "AK State", "", "", "unknown", "u", "State Legislation"),
            (2, "Z", "", "", "", "", "unknown", "u", "State Legislation")])
        r = dict(self.R16, state="AK")
        con.executescript(cv.update_statement("X", "u", r, True) + cv.update_statement("Z", "u", r, True))
        self.assertEqual(con.execute("SELECT title, jurisdiction FROM policies ORDER BY id").fetchall(),
                         [("Kept", "AK State"), ("O'Brien's act", "AK State")])

    def test_blank_url_key(self):
        con = self.db()
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "X", "", "AK State", "", "", "unknown", "", "State Legislation"),
            (2, "X", "", "AK State", "", "", "unknown", None, "State Legislation"),
            (3, "X", "", "AK State", "", "", "unknown", "u", "State Legislation")])
        con.executescript(cv.update_statement("X", "", self.R16, False))
        self.assertEqual(con.execute("SELECT id FROM policies WHERE year = '2016' ORDER BY id").fetchall(), [(1,), (2,)])

    def test_rollback_restores_exactly(self):
        con = self.db()
        rows = [(1, "X", "", "", "", "", "unknown", "u16", "State Legislation"),
                (2, "X", "", "CO State", "", "", "proposed", "u16", "State Legislation"),
                (3, "X", "Good", "CO State", "2016-03-23", "2016", "enacted", "u16", "State Legislation")]   # already had a year
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?)", rows)
        blank = [dict(id="1", status="unknown", jurisdiction=""), dict(id="2", status="proposed", jurisdiction="CO State")]
        con.executescript(cv.update_statement("X", "u16", self.R16, True))
        self.assertEqual(con.execute("SELECT year FROM policies ORDER BY id").fetchall(), [("2016",), ("2016",), ("2016",)])
        for stmt in cv.rollback_statements("X", "u16", self.R16, blank, has_year_row=True):
            self.assertIn(" WHERE id = ", stmt)
            con.executescript(stmt)
        self.assertEqual(con.execute("SELECT * FROM policies ORDER BY id").fetchall(), [tuple(r) for r in rows])
        # homogeneous rows without a good twin: one statement keyed by policy_id, url, year and date
        stmts = cv.rollback_statements("X", "u16", self.R16, [dict(id="1", status="unknown", jurisdiction="CO State"),
                                                              dict(id="2", status="unknown", jurisdiction="CO State")])
        self.assertEqual(len(stmts), 1)
        self.assertNotIn(" WHERE id = ", stmts[0])

    def test_group_propagation_uses_policy_id_and_url(self):
        con = self.db()
        con.execute("CREATE TABLE policy_groups (id INTEGER PRIMARY KEY, family TEXT, policy_id TEXT, title TEXT, jurisdiction TEXT, date TEXT, url TEXT)")
        con.executemany("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?)", [
            (1, "X", "T16", "CO State", "2016-03-23", "2016", "enacted", "u16", "State Legislation"),
            (2, "X", "T19", "CO State", "2019-05-31", "2019", "enacted", "u19", "State Legislation")])
        con.executemany("INSERT INTO policy_groups (family, policy_id, title, jurisdiction, date, url) VALUES (?,?,?,?,?,?)", [
            ("f", "X", "", "", "", "u16"), ("f", "X", "", "", "", "u19"), ("f", "X", "Own", "J", "2020-01-01", "u-else"),
            ("f", "X", "", "", "", "u-none")])
        con.executescript(cv.GROUPS_PROPAGATE)
        self.assertEqual(con.execute("SELECT title, jurisdiction, date, url FROM policy_groups ORDER BY id").fetchall(),
                         [("T16", "CO State", "2016-03-23", "u16"), ("T19", "CO State", "2019-05-31", "u19"),
                          ("Own", "J", "2020-01-01", "u-else"), ("", "", "", "u-none")])

    def test_build_and_rehearse_on_a_synthetic_table(self):
        first = {
            "1": dict(id="10", pid="1", jurisdiction="AL State", date="", year="", status="unknown", policy_family="f", title="", url="a1"),
            "X": dict(id="11", pid="X", jurisdiction="CO State", date="2019-05-31", year="2019", status="enacted", policy_family="f", title="Good", url="u19"),
            "Z": dict(id="12", pid="Z", jurisdiction="", date="", year="", status="enacted", policy_family="f", title="", url="z"),
        }
        blank = [dict(first["1"], is_first=True), dict(first["Z"], is_first=True),
                 dict(id="13", pid="X", jurisdiction="CO State", date="", year="", status="unknown", policy_family="f", title="", url="u16", is_first=False)]
        res = {("1", "a1"): {"tier": 1, "evidence": "unique_key", "n_upstream": 1, "date": "2018-01-02", "year": 2018, "status": "enacted",
                             "title": "One's title", "state": "AL", "source": "both"},
               ("Z", "z"): {"tier": 1, "evidence": "unique_key", "n_upstream": 1, "date": "2021-02-03", "year": 2021, "status": "enacted",
                            "title": "Zed", "state": "AK", "source": "both"},
               ("X", "u16"): {"tier": 2, "evidence": "key+url", "n_upstream": 2, "date": "2016-03-23", "year": 2016, "status": "enacted",
                              "title": "Sixteen", "state": "CO", "source": "both"}}
        good = {("X", "u19")}
        base = os.environ.get("L1_TEST_OUT")
        with (contextlib.nullcontext(base) if base else tempfile.TemporaryDirectory()) as tmp:
            out = Path(tmp)
            out.mkdir(parents=True, exist_ok=True)
            m = cv.build_live_update(res, blank, out, good, {"1", "Z"}, 100, 4)
            self.assertEqual((m["update_statements"], m["raw_rows_expected"], m["rollback_statements"]), (3, 3, 3))
            self.assertEqual((m["tiers"]["tier1"]["statements"], m["tiers"]["tier2"]["statements"]), (2, 1))
            self.assertEqual(len(m["bundle_sha256"]), 64)
            names = sorted(f["file"].split("/")[-1] for f in m["files"])
            self.assertIn("01_policies_update_t1_001.sql", names)
            self.assertIn("01_policies_update_t2_001.sql", names)
            self.assertIn("rollback_policies_t2_001.sql", names)
            self.assertNotIn("01_policies_update_t3_001.sql", names)
            reh = cv.rehearse(first, blank, res, out)
        self.assertTrue(reh["changed_ids_match"] and reh["values_match_recovery"])
        self.assertEqual((reh["rows_changed"], reh["blank_after_update"]), (3, 0))
        self.assertTrue(reh["second_run_is_noop"] and reh["rollback_restores_original"] and reh["federal_guard_row_unchanged"])
        self.assertTrue(reh["groups_each_row_got_its_own_date"] and reh["groups_keep_row_unchanged"])
        self.assertTrue(reh["tier1_files_alone_change_exactly_tier1_rows"])


@unittest.skipUnless((REPO / "data/pipeline/coverage_v2/checks_v2.json").exists(), "run scripts/l1/coverage_v2.py first")
class RealRunCounts(unittest.TestCase):
    """The counts the report quotes, as the script recorded them."""

    @classmethod
    def setUpClass(cls):
        d = REPO / "data/pipeline/coverage_v2"
        cls.checks = {c["name"]: c for c in json.loads((d / "checks_v2.json").read_text(encoding="utf-8"))["checks"]}
        cls.cov = json.loads((d / "coverage_data_v2.json").read_text(encoding="utf-8"))
        cls.summary = json.loads((d / "recovery_summary_v2.json").read_text(encoding="utf-8"))

    def test_every_check_passed(self):
        self.assertEqual([n for n, c in self.checks.items() if not c["ok"]], [])
        self.assertGreaterEqual(len(self.checks), 40)

    def test_headline_counts(self):
        v = {n: c["value"] for n, c in self.checks.items()}
        self.assertEqual((v["v1_not_observed"], v["v2_observed"], v["v2_observed_after"]), (22, 46, 46))
        self.assertEqual(v["v2_not_observed"], ["KS", "MN", "MO", "NM", "WI"])
        self.assertEqual((v["orphans"], v["tier1"], v["tier2"], v["unrecovered"]), (14071, 11463, 2608, 0))
        self.assertEqual((v["v1_in_window_rows"], v["after_in_window_rows"]), (27269, 41340))
        self.assertEqual(v["blank_year_raw_rows"], 14227)

    def test_old_output_reproduced_before_anything_else(self):
        self.assertTrue(self.checks["v1_output_reproduced_exactly"]["value"])

    def test_json_totals_are_consistent(self):
        states = self.cov["states"]
        self.assertEqual(len(states), 51)
        self.assertEqual(sum(s["total"] for s in states.values()), self.cov["recovery"]["rows_in_window_after"])
        self.assertEqual(sum(s["total_before"] for s in states.values()), self.cov["recovery"]["rows_in_window_before"])
        for s in states.values():
            self.assertEqual(s["total"], sum(s["by_year"]))
            self.assertEqual(s["total"] - s["total_before"], s["recovered_rows"]["tier1"] + s["recovered_rows"]["tier2"])
        self.assertEqual(sorted(c for c, s in states.items() if s["calendar"] == "odd_years_only"), ["MT", "ND", "NV", "TX"])

    def test_back_fill_cannot_reach_the_orphans(self):
        reach = self.summary["backfill_reach"]
        self.assertEqual(reach["filtered_2016_csv"]["reachable_by_backfill"], 0)
        self.assertEqual(reach["checkpoint"]["hits"], 0)
        self.assertEqual(reach["enacted_trials_raw_policies"]["hits"], 0)


if __name__ == "__main__":
    unittest.main()
