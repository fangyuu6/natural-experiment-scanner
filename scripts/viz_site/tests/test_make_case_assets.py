"""Synthetic display-only tests: no executor, panels or outcome files."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.viz_site.make_case_assets import (
    CASE_IDS, CaseAssetsError, benchmark_tier, load_case_selection,
    merge_case_selection,
)


SCRATCH = Path("/tmp/scratch/"
               "bdface0a-3d58-4f5e-aef6-12ae959fa031/scratchpad/page_ext")


def pair(en="Synthetic", zh="合成数据"):
    return dict(en=en, zh=zh)


def sidecar(identity, *, shown=True):
    row = dict(id="primary", label=pair(), outcome=pair("Invented outcome", "虚构结果指标"),
               population=pair(), unit=pair("units", "单位"), calendar_years=[2000, 2001],
               estimate=0.2, interval=[-0.1, 0.5],
               comparison_line=pair("No comparable published point estimate for this window.",
                                    "本窗口无可比的已发表点估计。"))
    return dict(version=1, case_id=identity, data_kind="synthetic",
                status="PUBLISHABLE_EXAMPLE" if shown else "INTERNAL_ONLY", shown=shown,
                policy=pair(), treatment=pair(), estimand_line=pair(), source_caption=pair(),
                design=dict(treated_jurisdictions=6, adoption_groups=2, never_treated=5,
                            not_yet_treated_or_future=6, periods=2, first_period=pair('2000', '2000'),
                            last_period=pair('2001', '2001'), excluded_by_rule_count=0,
                            year_corroborated_count=0, time_basis={'kind': 'calendar_year'}),
                checks=dict(pretrend=dict(joint_p_value=.8, joint_limit=.01, joint_passed=True,
                                         economic_size=.01, economic_limit=.05, economic_passed=True),
                            placebo=dict(reading=.02, limit=.08, passed=True), calibration=None,
                            thin_support_note=None),
                primary_estimates={"primary": row} if shown else {}, other_estimates=[],
                figures={"primary": dict(caption=pair(), points=[
                    dict(event_time=-1, estimate=0.0, treated_support=6, hollow=False,
                         band=[-0.1, 0.1], band_family="pre"),
                    dict(event_time=0, estimate=0.2, treated_support=4, hollow=True,
                         band=None, band_family="post")])} if shown else {},
                stop_reasons=[] if shown else [pair("Execution was interrupted.", "执行被中断。")])


class CaseSiteAssetsTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write(self, identity, record=None):
        path = self.directory / (identity + "_site.json")
        path.write_text(json.dumps(record if record is not None else sidecar(identity)), encoding="utf-8")
        return path

    def test_frozen_order_and_counts_include_stopped(self):
        for identity in reversed(CASE_IDS):
            self.write(identity, sidecar(identity, shown=identity != "right_to_work"))
        selected = load_case_selection(self.directory)
        self.assertEqual(selected["present_case_ids"], list(CASE_IDS))
        self.assertEqual((selected["specified_count"], selected["present_count"],
                          selected["shown_count"], selected["stopped_count"]), (10, 10, 9, 1))
        stopped = next(row for row in selected["cases"] if row["case_id"] == "right_to_work")
        self.assertFalse(stopped["shown"])
        self.assertEqual(stopped["stop_reasons"][0]["en"], "Execution was interrupted.")

    def test_partial_inventory_never_manufactures_results(self):
        self.write("smoke_free_air")
        selected = load_case_selection(self.directory)
        self.assertEqual(selected["specified_count"], 10)
        self.assertEqual(selected["present_count"], 1)
        self.assertEqual(selected["stopped_count"], 0)
        self.assertEqual(selected["present_case_ids"], ["smoke_free_air"])

    def test_missing_optional_fields_preserves_objective_rows(self):
        record = sidecar("cigarette_tax")
        alternate = copy.deepcopy(record["primary_estimates"]["primary"])
        alternate.update(id="alternative", ids=["alternative", "exact_alias"], estimate=-0.3,
                         interval=[-0.5, -0.1], variant_kind="outcome")
        diagnostic = dict(kind="range", headline_id="primary", ids=["omit_a", "omit_b"],
                          point_range=[-0.1, 0.4], label=pair(), outcome=pair(),
                          population=pair(), unit=pair(), calendar_years=[2000, 2001])
        record["other_estimates"] = [alternate, diagnostic]
        self.write("cigarette_tax", record)
        adapted = load_case_selection(self.directory)["cases"][0]
        self.assertEqual(adapted, record)
        self.assertEqual(adapted["other_estimates"][0]["ids"], ["alternative", "exact_alias"])

    def test_quarter_and_fiscal_labels_are_retained(self):
        for identity, basis, periods, labels in (
                ("cigarette_tax", {"kind": "quarter"}, [20001, 20002],
                 {"en": ["2000 Q1", "2000 Q2"], "zh": ["2000 年第 1 季度", "2000 年第 2 季度"]}),
                ("adult_use_cannabis", {"kind": "fiscal_year", "fiscal_start_month": 7}, [2000, 2001],
                 {"en": ["FY 2000", "FY 2001"], "zh": ["2000 财年", "2001 财年"]})):
            record = sidecar(identity)
            record["primary_estimates"]["primary"].update(
                time_basis=basis, periods=periods, calendar_years=periods, period_labels=labels)
            self.write(identity, record)
        records = load_case_selection(self.directory)["cases"]
        self.assertEqual(records[0]["primary_estimates"]["primary"]["period_labels"]["en"],
                         ["2000 Q1", "2000 Q2"])
        self.assertEqual(records[1]["primary_estimates"]["primary"]["time_basis"]["kind"], "fiscal_year")

    def test_schema_and_identity_mismatches_are_loud(self):
        for field, value, message in (("version", 2, "schema version"),
                                      ("version", True, "schema version"),
                                      ("case_id", "wrong_identity", "identity mismatch"),
                                      ("shown", False, "status/shown mismatch")):
            with self.subTest(field=field, value=value):
                record = sidecar("cigarette_tax")
                record[field] = value
                self.write("cigarette_tax", record)
                with self.assertRaisesRegex(CaseAssetsError, message):
                    load_case_selection(self.directory)

    def test_unknown_filename_is_not_silently_hidden(self):
        self.write("unknown_policy")
        with self.assertRaisesRegex(CaseAssetsError, "Unknown case sidecar identity"):
            load_case_selection(self.directory)

    def test_stopped_requires_plain_reason(self):
        record = sidecar("cigarette_tax", shown=False)
        record["stop_reasons"] = []
        self.write("cigarette_tax", record)
        with self.assertRaisesRegex(CaseAssetsError, "Stopped case lacks a reason"):
            load_case_selection(self.directory)

    def test_null_required_design_counts_refused(self):
        for key in ('treated_jurisdictions', 'adoption_groups', 'never_treated',
                    'not_yet_treated_or_future', 'periods', 'excluded_by_rule_count', 'year_corroborated_count'):
            with self.subTest(key=key):
                record = sidecar('cigarette_tax')
                record['design'][key] = None
                self.write('cigarette_tax', record)
                with self.assertRaisesRegex(CaseAssetsError, 'required design count: ' + key):
                    load_case_selection(self.directory)

    def test_stopped_needs_no_design_or_checks(self):
        record = sidecar('cigarette_tax', shown=False)
        record.pop('design')
        record.pop('checks')
        self.write('cigarette_tax', record)
        self.assertEqual(load_case_selection(self.directory)['cases'], [record])

    def test_stopped_with_available_scalars_is_still_stopped(self):
        record = sidecar("cigarette_tax")
        record.update(status="INTERNAL_ONLY", shown=False,
                      stop_reasons=[pair("Pre-treatment trend test rejected.", "处理前趋势检验拒绝。")])
        self.write("cigarette_tax", record)
        selected = load_case_selection(self.directory)
        self.assertEqual(selected["shown_count"], 0)
        self.assertEqual(selected["cases"][0], record)

    def test_merge_batch1_first_and_selection_counts(self):
        batch1 = dict(prepared=3, shown=2, examples=[dict(registered_id="paid_sick_leave"),
                                                  dict(registered_id="medicaid_expansion")],
                      omitted=[dict(registered_id="castle_doctrine")],
                      contract=dict(specified_example_ids=["medicaid_expansion", "paid_sick_leave", "castle_doctrine"]))
        self.write("smoke_free_air")
        self.write("cigarette_tax", sidecar("cigarette_tax", shown=False))
        merged = merge_case_selection(batch1, load_case_selection(self.directory))
        self.assertEqual((merged["prepared"], merged["shown"]), (13, 3))
        self.assertEqual([entry["id"] for entry in merged["entries"]],
                         ["medicaid_expansion", "paid_sick_leave", "castle_doctrine", "cigarette_tax", "smoke_free_air"])
        self.assertIs(merged["examples"], batch1["examples"])
        self.assertEqual(batch1["prepared"], 3)

    def test_empty_inventory_leaves_batch1_selection_exactly_equal(self):
        batch1 = dict(prepared=3, shown=2, examples=[], omitted=[], contract=None)
        self.assertEqual(merge_case_selection(batch1, load_case_selection(self.directory)), batch1)
        self.assertEqual(load_case_selection(self.directory / "missing")["cases"], [])

    def test_tiers_use_only_known_generator_copy(self):
        lines = {
            "Published 0.2 units; within the preset tolerance of ±0.1 units.": "TIGHT",
            "Published 0.2 units; same sign and overlapping intervals, outside the preset tolerance.": "DIRECTIONAL",
            "Published 0.2 units; this estimate is not consistent with the published estimate.": "NOT_CONSISTENT",
            "Published 0.2 units; this estimate is not consistent with the published estimate. Sign differs; the difference is not statistically distinguishable.": "NOT_CONSISTENT",
            "No comparable published point estimate for this window.": "NOT_COMPARABLE",
            "Context: published estimate 0.2 units.": "NOT_COMPARABLE",
            "Published study reports exclusion bounds; no comparable point estimate.\nBound verdict: synthetic bound.": "NOT_COMPARABLE",
            "Published exclusion bound: 0.2 units. No comparable point estimate.": "NOT_COMPARABLE",
            "Unknown comparison prose": None,
        }
        for line, expected in lines.items():
            with self.subTest(line=line):
                self.assertEqual(benchmark_tier(dict(comparison_line=pair(line))), expected)


if __name__ == "__main__":
    unittest.main()
