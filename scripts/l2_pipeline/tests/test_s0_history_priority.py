"""Invented S0 availability metadata; no real outcomes or dates are opened."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline.cards import load_card, validate_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.stages.s0_intake import card_from_queue, history_priority, rank_queue_rows, run
from scripts.l2_pipeline.storage import write_once
from scripts.l2_pipeline.tests.test_pipeline import card


def located(**fields):
    return dict(source_ref="invented_official_table", locator="invented table 1", **fields)


def candidate(design_id="invented", **evidence):
    return {"design_id":design_id, "policy_family":"invented", "track":"DiD",
            "priority":{"score":20}, "automation":{"s0_history_evidence":evidence}}


def mature_evidence(n=10, through=2024):
    return {"dated_adopters":[located(unit=f"u{i}", start_year=2010+i%2, official=True) for i in range(n)],
            "outcome_coverage":located(available_through=through)}


class HistoryPriorityTests(unittest.TestCase):
    def test_located_official_history_preferred_and_alternatives_do_not_stack(self):
        source = located(official=True, dated_history=True)
        historical = candidate("historical", official_dated_histories=[source])
        recent_stock = candidate("stock")
        mature = candidate("mature", **mature_evidence())
        both = candidate("both", official_dated_histories=[source], **mature_evidence())
        for row in (historical, mature, both):
            self.assertEqual(history_priority(row)["history_bonus"], 3)
        self.assertEqual(history_priority(recent_stock)["history_bonus"], 0)
        self.assertEqual([r["design_id"] for r in rank_queue_rows([recent_stock, historical])], ["historical", "stock"])

    def test_missing_evidence_and_database_dates_never_gain_credit(self):
        row = candidate("stock", official_dated_histories=[{"official":True, "dated_history":True}])
        row.update(state_count=51, database_date="1995-01-01", year=1995, first_appearance=1995,
                   dated_pre2020_adopter_count=51, outcome_available_through=2024)
        row["outcome"] = {"years":[2000,2024]}
        self.assertEqual(history_priority(row)["history_bonus"], 0)
        for field, value in (("official", 1), ("dated_history", "true"), ("locator", "")):
            source = located(official=True, dated_history=True); source[field] = value
            self.assertEqual(history_priority(candidate(official_dated_histories=[source]))["history_bonus"], 0)

    def test_many_pre2020_requires_ten_unique_dated_units_and_coverage(self):
        self.assertEqual(history_priority(candidate(**mature_evidence(9)))["history_bonus"], 0)
        self.assertEqual(history_priority(candidate(**mature_evidence(10, 2023)))["history_bonus"], 0)
        for invalid_through in (True, "2024", None):
            self.assertEqual(history_priority(candidate(**mature_evidence(10, invalid_through)))["history_bonus"], 0)
        evidence = mature_evidence()
        evidence["dated_adopters"].append(copy.deepcopy(evidence["dated_adopters"][0]))
        self.assertEqual(history_priority(candidate(**evidence))["dated_pre2020_adopter_count"], 10)
        evidence["dated_adopters"].append(located(unit="u0", start_year=2015, official=True))
        scored = history_priority(candidate(**evidence))
        self.assertEqual(scored["dated_pre2020_adopter_count"], 9)
        self.assertEqual(scored["conflicting_dated_units"], ["u0"])
        self.assertEqual(scored["history_bonus"], 0)
        evidence = mature_evidence()
        evidence["dated_adopters"][0]["start_year"] = 2020
        self.assertEqual(history_priority(candidate(**evidence))["history_bonus"], 0)

    def test_ranking_preserves_base_idempotence_inputs_and_ignores_associations(self):
        mature = candidate("mature", **mature_evidence())
        high_base = candidate("higher_base"); high_base["priority"]["score"] = 24
        original = copy.deepcopy([mature, high_base])
        ranked = rank_queue_rows([mature, high_base])
        self.assertEqual(ranked[0]["design_id"], "higher_base")
        self.assertEqual([mature, high_base], original)
        self.assertEqual(rank_queue_rows(ranked), ranked)
        polluted = copy.deepcopy(mature)
        polluted.update(expected_sign="positive", significance=0.001, adoption_by_party=99, effect=1000)
        self.assertEqual(history_priority(polluted), history_priority(mature))
        for invalid in (True, float("nan"), "20"):
            bad = candidate(); bad["priority"]["score"] = invalid
            with self.assertRaises(ValueError): rank_queue_rows([bad])

    def test_other_tracks_keep_own_criteria(self):
        for track in ("RDD", "rdd", "RCT", "rct_lottery"):
            row = candidate(**mature_evidence()); row["track"] = track
            self.assertEqual(history_priority(row)["history_bonus"], 0)

    def test_intake_and_s0_persist_priority_without_readiness_or_mutation(self):
        row = candidate(**mature_evidence())
        row["source_records"] = [{"source_text_id":"text1", "legal_instrument_id":"law1",
                                   "source_record_id":"record1", "source_url":"https://example.invalid"}]
        defaults = card(); defaults["automation"] = {"existing_option":True}
        prior = copy.deepcopy((row, defaults))
        value = card_from_queue(row, defaults, "invented_queue")
        self.assertEqual(validate_card(value), [])
        self.assertEqual(value["automation"]["s0_history_priority"]["history_bonus"], 3)
        self.assertEqual((row, defaults), prior)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = write_once(root/"card.json", value)
            ctx = StageContext(load_card(path), root/"work", root)
            result = run(ctx)
            persisted = json.loads((ctx.design_dir/"S0_PRIORITY.json").read_text())
            self.assertEqual(persisted, history_priority(value))
            self.assertFalse(persisted["readiness_granted"])
            self.assertEqual(persisted["outcome_values_read"], 0)
            self.assertEqual(result.metrics["history_priority_bonus"], 3)
            self.assertEqual(run(ctx), result)


if __name__ == "__main__": unittest.main()
