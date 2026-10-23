"""V7 S0 uses legal/measurement metadata and preserves V6.1 construct gates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.first_order import first_order_gate
from scripts.l2_pipeline.stages.s0_intake import (
    card_from_queue, program_priority, rank_queue_rows, run,
)
from scripts.l2_pipeline.storage import write_once
from scripts.l2_pipeline.tests.test_first_order_gate import card


def candidate(name="broad", broad=True):
    value = card()
    value.update(design_id=name, priority={"score": 10})
    value["automation"] = {"s0_program_evidence": {
        "broad_statutory_program": broad, "source_ref": "invented statute",
        "locator": "program scope section",
    }}
    return value


class V7IntakeTests(unittest.TestCase):
    def test_broad_standard_program_gets_bounded_term_and_stable_rank(self):
        rows = [candidate("a_narrow", False), candidate("z_broad")]
        original = copy.deepcopy(rows)
        ranked = rank_queue_rows(rows)
        self.assertEqual([r["design_id"] for r in ranked], ["z_broad", "a_narrow"])
        self.assertEqual(ranked[0]["priority"]["score"], 13)
        self.assertEqual(ranked[0]["priority"]["program_bonus"], 3)
        self.assertEqual(rank_queue_rows(ranked), ranked)
        self.assertEqual(rank_queue_rows(reversed(rows)), ranked)
        self.assertEqual(rows, original)
        higher = candidate("higher", False)
        higher["priority"]["score"] = 14
        self.assertEqual(rank_queue_rows([rows[1], higher])[0]["design_id"], "higher")

    def test_missing_or_unlocated_broadness_never_inferred_from_title(self):
        for evidence in ({}, {"broad_statutory_program": True},
                         {"broad_statutory_program": 1, "source_ref": "s", "locator": "p"},
                         {"broad_statutory_program": True, "source_ref": "s", "locator": " "}):
            value = candidate()
            value["automation"]["s0_program_evidence"] = evidence
            value.update(mechanism="Broad statutory program", state_count=51)
            self.assertEqual(program_priority(value)["program_bonus"], 0)

    def test_ranking_reads_no_values_and_ignores_associations(self):
        value = candidate()
        original = copy.deepcopy(value)
        value["outcome"]["paths"] = ["must-never-open.csv"]
        value.update(expected_sign="positive", effect=999, p_value=0.001, adoption_by_party=80)
        with patch("builtins.open", side_effect=AssertionError("outcome opened")), \
             patch("pathlib.Path.open", side_effect=AssertionError("outcome opened")):
            term = program_priority(value)
            ranked = rank_queue_rows([value])
        self.assertEqual(term, program_priority(original))
        self.assertEqual(term["outcome_values_read"], 0)
        self.assertEqual(term["outcome_association_calls"], 0)
        self.assertFalse(term["readiness_granted"])
        self.assertEqual(ranked[0]["priority"]["program_bonus"], 3)

    def test_unknown_narrow_construct_stays_parked_even_with_broad_claim(self):
        value = candidate("narrow_gap")
        value["first_order"].update(target_outcome="ems_interstate_practice_count",
                                     primary_outcome="ems_interstate_practice_count")
        value["outcome"].update(measure_id="ems_interstate_practice_count", connector="unavailable")
        self.assertEqual(program_priority(value)["program_bonus"], 0)
        self.assertFalse(first_order_gate(value)["passed"])
        value["priority"]["score"] = 999
        self.assertEqual(rank_queue_rows([value, candidate("mapped")])[0]["design_id"], "mapped")
        value.pop("priority")  # Queue scores are not part of the design-card schema.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = StageContext(load_card(write_once(root / "card.json", value)), root / "work", root)
            result = run(ctx)
            self.assertEqual(result.disposition.value, "parked")
            self.assertEqual(result.metrics["program_priority_bonus"], 0)

    def test_same_construct_proxy_allowed_but_downstream_proxy_has_no_credit(self):
        for target, measure, connector, bonus in (
            ("dependent_health_insurance_coverage_pct", "health_insurance_coverage_pct", "acs1_health_insurance", 3),
            ("ems_interstate_practice_count", "nurse_hours", "cps_nurse_hours", 0),
            ("secondhand_smoke_exposure_pct", "current_smoking_pct", "brfss_state_prevalence", 0),
            ("health_insurance_coverage_pct", "wages", "qcew", 0),
        ):
            with self.subTest(target=target):
                value = candidate()
                value["first_order"].update(target_outcome=target, primary_outcome=measure,
                    relation="closest_proxy", proxy_justification="Same construct in a broader population with intent-to-treat dilution.")
                value["outcome"].update(measure_id=measure, connector=connector,
                                          value_field="annual_avg_wkly_wage")
                self.assertEqual(program_priority(value)["program_bonus"], bonus)
                self.assertEqual(first_order_gate(value)["passed"], bool(bonus))

    def test_invalid_registration_or_purpose_pending_cannot_gain_readiness(self):
        for field, update in (("availability", "unavailable"), ("primary_outcome", "wages"), ("relation", "downstream")):
            value = candidate()
            value["first_order"][field] = update
            self.assertEqual(program_priority(value)["program_bonus"], 0)
        value = candidate()
        value["first_order"]["evidence_status"] = "pending_policy_specific_verification"
        self.assertEqual(program_priority(value)["program_bonus"], 3)
        self.assertFalse(first_order_gate(value)["passed"])

    def test_intake_and_s0_persist_the_identical_term_without_mutation(self):
        row = candidate()
        row["source_records"] = [{"source_text_id": "text1", "legal_instrument_id": "law1",
            "source_record_id": "record1", "source_url": "https://example.invalid"}]
        defaults = card()
        original = copy.deepcopy((row, defaults))
        value = card_from_queue(row, defaults, "invented_queue")
        expected = program_priority(value)
        self.assertEqual(value["automation"]["s0_program_priority"], expected)
        self.assertEqual(expected["program_bonus"], 3)
        self.assertEqual((row, defaults), original)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = StageContext(load_card(write_once(root / "card.json", value)), root / "work", root)
            result = run(ctx)
            persisted = json.loads((ctx.design_dir / "S0_PROGRAM_PRIORITY.json").read_text())
            self.assertEqual(persisted, expected)
            self.assertEqual(result.metrics["program_priority_bonus"], 3)
            self.assertEqual(run(ctx), result)


if __name__ == "__main__":
    unittest.main()
