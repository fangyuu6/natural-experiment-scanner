"""Checks of sweep_ext_series.py and sweep_ext_acquire.py on INVENTED files (no repository data file is read except the catalogs for the plan test; nothing
is written outside temporary folders):

  eligibility and the store layout (strict flags, finite values, denominators, round trip through sweep_core.SeriesStore),
  the metadata-only design screen, the acquisition bookkeeping (file records by year, registry line, batch receipt, 2011 ACS lookup shim),
  the plan (62 series, groups, the declared populations, the count-kind / peer / parked classification), write-once behaviour.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_sweep_ext_series.py
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import sweep_core as S  # noqa: E402
import sweep_ext_acquire as A  # noqa: E402
import sweep_ext_series as M  # noqa: E402

PYCACHE_BEFORE = set(map(str, (M.ROOT / "scripts/l2_pipeline").rglob("__pycache__")))


def panel(values, flags=None, year0=2010):
    """values: {(unit, year): value or None}; flags: {(unit, year): dict of metadata overrides}."""
    rows, meta = [], []
    for (u, y), v in values.items():
        rows.append(dict(unit=u, year=y, period=str(y), frequency="annual", outcome_value="" if v is None else v, outcome_unit="u", population_denominator=(100.0 if v is not None else None)))
        m = dict(unit=u, year=y, available=int(v is not None), annual_usable=int(v is not None), linkage_certified=1)
        m.update((flags or {}).get((u, y), {}))
        meta.append(m)
    return dict(rows=rows, metadata=meta)


class Eligibility(unittest.TestCase):
    def test_strict_and_semantic(self):
        vals = {("AL", 2010): 1.0, ("AL", 2011): 2.0, ("AL", 2012): None, ("AK", 2010): 4.0, ("AK", 2011): float("nan"), ("AK", 2012): 6.0}
        flags = {("AK", 2010): dict(suppressed=True), ("AL", 2011): dict(unreliable=False), ("AK", 2012): dict(annual_usable=0)}
        e = M.entry_from_panel(panel(vals, flags))
        self.assertTrue(e["ok"][("AL", 2010)])
        self.assertTrue(e["ok"][("AL", 2011)])          # unreliable False is not a flag
        self.assertFalse(e["ok"][("AL", 2012)])         # no value
        self.assertFalse(e["ok"][("AK", 2010)])         # suppressed True
        self.assertFalse(e["ok"][("AK", 2011)])         # NaN value written as a number is refused by the finite test
        self.assertFalse(e["ok"][("AK", 2012)])         # annual_usable 0
        self.assertTrue(np.isnan(e["val"][("AL", 2012)]))
        self.assertEqual(e["den"][("AL", 2010)], 100.0)
        self.assertTrue(np.isnan(e["den"][("AL", 2012)]))
        self.assertEqual(e["units_string"], "u")

    def test_numeric_text_values_like_the_engine(self):
        self.assertEqual(M.value_of("1487"), 1487.0)
        self.assertEqual(M.value_of(" 12.5 "), 12.5)
        self.assertIsNone(M.value_of(""))
        self.assertIsNone(M.value_of(None))
        self.assertIsNone(M.value_of(float("nan")))
        self.assertIsNone(M.value_of("nan"))
        with self.assertRaises(ValueError):
            M.value_of("N")
        with self.assertRaises(ValueError):
            M.value_of(True)
        p = panel({("AL", 2010): 1.0, ("AL", 2011): 2.0})
        p["rows"][0]["outcome_value"] = "1487"
        p["rows"][1]["outcome_value"] = ""
        p["metadata"][1]["available"] = 0
        e = M.entry_from_panel(p)
        self.assertTrue(e["ok"][("AL", 2010)] and e["val"][("AL", 2010)] == 1487.0)
        self.assertFalse(e["ok"][("AL", 2011)])

    def test_store_roundtrip_with_sweep_core(self):
        e1 = M.entry_from_panel(panel({("AL", 2010): 1.0, ("AL", 2011): 2.0, ("TX", 2010): 3.0, ("TX", 2011): None}))
        e2 = M.entry_from_panel(panel({("AL", 2010): 10.0, ("DC", 2011): 20.0}))
        store = {"s_one": e1, "s_two": e2}
        arrays = S.store_arrays(store, dict(series=list(store), kind="ext"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.npz"
            S.save_series_store(path, store, dict(series=list(store), kind="ext"))
            self.assertTrue(S.arrays_equal(arrays, path))
            st = S.SeriesStore(path)
            self.assertEqual(st.sids, ["s_one", "s_two"])
            self.assertEqual(st.eligible_keys("s_one", ["AL", "TX"], [2010, 2011]), {("AL", 2010), ("AL", 2011), ("TX", 2010)})
            self.assertEqual(st.value("s_two", ("DC", 2011)), 20.0)
            self.assertEqual(len(st.units), 51)


class Screen(unittest.TestCase):
    def test_complete_units_and_treated_share(self):
        e = M.entry_from_panel(panel({(u, y): 1.0 for u in ("AL", "AK", "AZ") for y in (2010, 2011, 2012)} | {("AZ", 2011): None}))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.npz"
            S.save_series_store(path, {"s": e}, dict(series=["s"]))
            st = S.SeriesStore(path)
            designs = {"d1": dict(units=["AL", "AK", "AZ"], years=[2010, 2011, 2012], treated=["AL", "AZ"], scale_mode="rate"),
                       "d2": dict(units=["AL", "AK", "AZ"], years=[2010, 2011], treated=["AL", "AK"], scale_mode="rate"),
                       "d3": dict(units=["AL"], years=[2009, 2010], treated=["AL"], scale_mode="rate")}
            res = M.screen_series(st, "s", designs)
            self.assertEqual(res["d1"], dict(complete_units=2, treated_kept=0.5))        # AZ lost its 2011 cell
            self.assertEqual(res["d2"], dict(complete_units=2, treated_kept=1.0))        # window 2010-2011: AL, AK complete; AZ lost
            self.assertEqual(res["d3"], dict(complete_units=0, treated_kept=0.0))        # 2009 is not in the series


class Acquisition(unittest.TestCase):
    def test_file_year_and_records(self):
        self.assertEqual(A.file_year("acsdt1y2021-b17001.dat"), 2021)
        self.assertEqual(A.file_year("20121ak0060000.zip"), 2012)
        self.assertEqual(A.file_year("g20151ca.csv"), 2015)
        self.assertEqual(A.file_year("acs_lookup_2013.txt"), 2013)
        self.assertEqual(A.file_year("acsdt1y2016-b27001-tx-derived.dat"), 2016)
        self.assertIsNone(A.file_year("README"))
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            for name in ("acsdt1y2021-b17001.dat", "acsdt1y2022-b17001.dat", "20121ak0060000.zip"):
                (raw / name).write_text("x")
                (raw / (name + ".receipt.json")).write_text(json.dumps({"url": "https://example.gov/" + name}))
            (raw / "acsdt1y2021-b17001.dat.scan.json").write_text("{}")
            (raw / ".hidden").write_text("x")
            rec = A.file_records(raw)
            self.assertEqual([Path(r["path"]).name for r in rec], ["20121ak0060000.zip", "acsdt1y2021-b17001.dat", "acsdt1y2022-b17001.dat"])
            self.assertTrue(all(r["url"].startswith("https://example.gov/") for r in rec))
            self.assertEqual(len(rec[0]["sha256"]), 64)
            only = A.file_records(raw, {2021})
            self.assertEqual([Path(r["path"]).name for r in only], ["acsdt1y2021-b17001.dat"])

    def test_registry_append_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Path(tmp) / "REGISTRY.jsonl"
            reg.write_text('{"old": 1}\n')
            old = A.REGISTRY
            A.REGISTRY = reg
            try:
                A.registry_append({"source": "s", "dataset": "d", "years": [2010, 2011], "files": [], "receipt_path": "r", "registered_at": "t"})
                A.registry_append({"source": "s2"})
            finally:
                A.REGISTRY = old
            lines = reg.read_text().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[0]), {"old": 1})          # the earlier line is untouched
            self.assertEqual(json.loads(lines[1])["dataset"], "d")
            rel = Path(tmp) / "rel"
            path = A.batch_receipt(rel, "job", [{"path": "p"}], {"failures": []})
            self.assertTrue(path.exists() and path.parent.name == "receipts")
            self.assertEqual(json.loads(path.read_text())["n_files"], 1)
            with self.assertRaises(FileExistsError):
                with open(path, "x"):
                    pass

    def test_acs_2011_lookup_shim(self):
        mode, core, cdc, acs, common = A.load_leaf_modules()
        with tempfile.TemporaryDirectory() as tmp:
            f2011 = Path(tmp) / "l2011.txt"
            f2011.write_text('"fileid","Table ID","seq","Line Number Decimal M Lines","position","cells","total","Long Table Title","subject_area"\n'
                             '"ACSSF","B17001","0069",".","7","59 CELLS",".","POVERTY","Poverty"\n'
                             '"ACSSF","B17001","0069",".",".","",".","Universe",""\n'
                             '"ACSSF","B17001","0069","1",".","",".","Total:",""\n'
                             '"ACSSF","B99999","0070",".","7","3 CELLS",".","OTHER",""\n')
            f2012 = Path(tmp) / "l2012.txt"
            f2012.write_text('"File ID","Table ID","Sequence Number","Line Number","Start Position","Total Cells in Table","Total Cells in Sequence","Table Title","Subject Area"\n'
                             'ACSSF,B17001,0069, ,7,59 CELLS, ,POVERTY,Poverty\n'
                             'ACSSF,B17001,0069, , ,, ,Universe,\n')
            original = acs._lookup
            try:
                self.assertEqual(A.shim_acs_lookup(acs) is original, True)
                self.assertEqual(acs._lookup(f2011, "B17001"), (69, 7, 59))
                self.assertEqual(acs._lookup(f2012, "B17001"), (69, 7, 59))      # the original layout still goes through the original function
                with self.assertRaises(Exception):
                    acs._lookup(f2011, "B00000")
            finally:
                acs._lookup = original


class Plan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = M.build_plan()

    def test_62_series_and_groups(self):
        self.assertEqual(len(self.plan), 62)
        groups = {}
        for p in self.plan.values():
            groups[p["group"]] = groups.get(p["group"], 0) + 1
        self.assertEqual(groups, {"acs": 32, "bls_api": 4, "cps": 9, "historical": 7, "historical_alias": 2, "nchs_pdf": 2, "qcew_c56": 1, "qcew_wage": 1,
                                  "socrata_firearm": 2, "socrata_vsrr": 2})

    def test_declared_populations(self):
        self.assertEqual(self.plan["cdc_teen_birth_rate"]["population_filters"], {"age_group": "15-19"})
        self.assertEqual(self.plan["nchs_summary_age_specific_birth_rate"]["population_filters"], {"age_group": "15-19"})
        self.assertEqual(self.plan["cdc_leading_cause_deaths"]["population_filters"], {"cause_name": "All causes"})
        self.assertEqual(self.plan["nchs_historical_suicide_deaths"]["population_filters"], {"cause_name": "Suicide"})
        self.assertEqual(self.plan["acs1_poverty_rate_age_under5"]["population_filters"], {"age_group": "under5", "sex": "all"})
        self.assertEqual(self.plan["cps_teen_employment"]["population_filters"], {})
        declared = {s for s, p in self.plan.items() if p["population_declared"]}
        self.assertEqual(declared, set(M.HEADLINE_POPULATION))

    def test_count_kind_peer_and_parking_inputs(self):
        ck = {s: (p["count_kind"], p["peer"]) for s, p in self.plan.items() if p["count_kind"]}
        self.assertEqual(ck, {"cdc_teen_births": ("births", "cdc_teen_birth_rate"), "nchs_summary_births": ("births", "nchs_summary_fertility_rate"),
                              "cdc_firearm_deaths": ("deaths", "cdc_firearm_crude_rate"), "bls_ces_private_employment": ("events", None),
                              "bls_laus_employment": ("events", None), "cdc_leading_cause_deaths": ("deaths", None),
                              "cdc_vsrr_overdose_deaths_predicted": ("deaths", None), "cdc_vsrr_overdose_deaths_reported": ("deaths", None),
                              "nchs_historical_drug_overdose_deaths": ("deaths", None), "nchs_historical_suicide_deaths": ("deaths", None)})

    def test_families_follow_the_inventory(self):
        fam = {}
        for p in self.plan.values():
            fam[p["family"]] = fam.get(p["family"], 0) + 1
        self.assertEqual(fam, {"births_fertility": 4, "earnings_income": 11, "employment": 9, "health_coverage": 12, "mortality": 11, "poverty_safety_net": 14,
                               "unemployment_ui": 1})


H1, H2 = "a" * 64, "b" * 64


class Pins(unittest.TestCase):
    def test_record_parse_and_pin_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            amend = tmp / "docs/AMEND.md"
            code = tmp / "scripts/l3_meta/x.py"
            amend.parent.mkdir(parents=True)
            code.parent.mkdir(parents=True)
            amend.write_text("amendment text")
            code.write_text("code")
            cat = tmp / "scripts/l2_pipeline/connectors/CAT.json"            # a non-code file under the L2 tree is an input and is checked as a file
            cat.parent.mkdir(parents=True)
            cat.write_text("{}")
            rec_path = tmp / "docs/REC.txt"
            lines = ["header", "file: docs/AMEND.md", f"sha256: {S.sha256_file(amend)}", "bytes: 14", "amended_utc (T_amend): 2026-10-16T00:00:00Z", "inputs:",
                     f"  scripts/l3_meta/x.py sha256 {S.sha256_file(code)}", f"  scripts/l2_pipeline/connectors/common.py sha256 {H1}",
                     f"  scripts/l2_pipeline/connectors/CAT.json sha256 {S.sha256_file(cat)}"]
            rec_path.write_text("\n".join(lines) + "\n")
            rec = M.parse_record(rec_path)
            self.assertEqual(rec["t_amend_utc"], "2026-10-16T00:00:00Z")
            self.assertEqual(rec["files"], {"docs/AMEND.md": S.sha256_file(amend)})
            old = (M.ROOT, M.HASH_REC, M.AMEND, M.SKIP_PINS, A.l2_hashes)
            try:
                M.ROOT, M.HASH_REC, M.AMEND, M.SKIP_PINS = tmp, rec_path, amend, False
                A.l2_hashes = lambda: {"scripts/l2_pipeline/connectors/common.py": H1}
                self.assertEqual(M.pin_problems(), [])
                A.l2_hashes = lambda: {"scripts/l2_pipeline/connectors/common.py": H2}
                self.assertEqual(len(M.pin_problems()), 1)                                  # a changed L2 module
                A.l2_hashes = lambda: {"scripts/l2_pipeline/connectors/common.py": H1, "scripts/l2_pipeline/connectors/new.py": H2}
                self.assertIn("loaded but not in the hash record", M.pin_problems()[0])      # an unlisted L2 module
                A.l2_hashes = lambda: {"scripts/l2_pipeline/connectors/common.py": H1}
                code.write_text("code changed")
                self.assertIn("scripts/l3_meta/x.py differs", M.pin_problems()[0])           # a changed code file
                self.assertEqual(M.pin_problems(files=False), [])                           # the loaded-module check alone
                code.write_text("code")
                cat.write_text('{"x": 1}')
                self.assertIn("CAT.json differs", M.pin_problems()[0])                        # a changed catalog file under the L2 tree
                cat.write_text("{}")
                amend.write_text("amendment text, edited")
                self.assertIn("differs from its hash record", M.pin_problems()[0])           # an edited amendment
                amend.write_text("amendment text")
                rec_path.rename(tmp / "docs/MOVED.txt")
                self.assertIn("does not exist", M.pin_problems()[0])                          # no record, no run
            finally:
                M.ROOT, M.HASH_REC, M.AMEND, M.SKIP_PINS, A.l2_hashes = old


class WriteOnce(unittest.TestCase):
    def test_write_once_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a" / "x.json"
            M.write_once_json(p, {"a": float("nan"), "b": np.float64(1.5), "c": {1, 2}})
            self.assertEqual(json.loads(p.read_text()), {"a": None, "b": 1.5, "c": [1, 2]})
            with self.assertRaises(FileExistsError):
                M.write_once_json(p, {})

    def test_no_new_pycache_under_l2(self):
        M.load_leaf()                                   # imports the L2 adapters
        after = set(map(str, (M.ROOT / "scripts/l2_pipeline").rglob("__pycache__")))
        self.assertEqual(after - PYCACHE_BEFORE, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
