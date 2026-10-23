import unittest

from scripts.l2_pipeline.v9_rescore import rescore_submission


class V9RescoreTests(unittest.TestCase):
    def fixture(self):
        return {
            "design_id": "synthetic", "role": "A",
            "sources": [{"source_id": "official", "sha256": "abc"}],
            "records": [{"unit": "CA", "status": "unknown", "start_period": None,
                         "effective_date": "2002-01-01", "reason": "Prior absence not proven",
                         "evidence_refs": [{"source_id": "official", "source_sha256": "abc",
                                            "quote": "The law takes effect January 1, 2002"}]}],
        }

    def test_dated_start_replayed_without_certifying_full_year_or_readiness(self):
        data = self.fixture()
        report = rescore_submission(data, {"CA": "2002-01-01"})
        self.assertEqual(report["summary"]["states_becoming_known"], 1)
        self.assertEqual(report["records"][0]["best_evidence_start_year"], 2002)
        self.assertIsNone(report["records"][0]["start_period"])
        self.assertFalse(report["s1_ready"])
        self.assertEqual(data["records"][0]["status"], "unknown")

    def test_cannot_invent_date_or_use_unbound_reference(self):
        with self.assertRaises(ValueError):
            rescore_submission(self.fixture(), {"CA": "2001-01-01"})
        data = self.fixture()
        data["sources"][0]["sha256"] = "changed"
        with self.assertRaises(ValueError):
            rescore_submission(data, {"CA": "2002-01-01"})

    def test_absence_is_not_inferred_from_unknown_or_current_stock(self):
        data = self.fixture()
        data["records"][0]["current_stock"] = {"status": "never_treated"}
        report = rescore_submission(data, {})
        self.assertEqual(report["records"][0]["evidence_status"], "needs_recollection")
        self.assertEqual(report["summary"]["known_after"], 0)

    def test_found_interruptions_survive_known_start(self):
        data = self.fixture()
        events = [{"date": "2006-04-12", "kind": "rule_expiration"}]
        data["records"][0]["authority_events"] = events
        report = rescore_submission(data, {"CA": "2002-01-01"})
        self.assertEqual(report["records"][0]["original_events"]["authority_events"], events)

    def test_shared_evidence_ids_are_resolved_without_changing_original_quotes(self):
        data = self.fixture()
        ref = data["records"][0]["evidence_refs"][0]
        ref["id"] = "date-evidence"
        data["evidence_refs"] = [ref]
        data["records"][0]["evidence_refs"] = ["date-evidence"]
        replay = rescore_submission(data, {"CA": "2002-01-01"})
        self.assertEqual(replay["records"][0]["source_id"], "official")
        self.assertEqual(replay["records"][0]["start_evidence_refs"][0]["quote"], ref["quote"])

    def test_unselected_encoded_dates_have_concrete_review_reason(self):
        data = self.fixture()
        data["design_id"] = "run7_minor_abortion_parental_involvement"
        data["records"][0]["unit"] = "CO"
        data["records"][0]["effective_date"] = "1998-12-30"
        replay = rescore_submission(data, {})
        candidate = replay["records"][0]["encoded_date_candidate_reviews"][0]
        self.assertFalse(candidate["selected_for_start_year"])
        self.assertIn("original injunction", candidate["decision_reason"])


if __name__ == "__main__":
    unittest.main()
