import copy
import unittest

from review import apply_reviews
from test_registry import event, queued
from validate import validate_registry


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.previous = queued()
        event(self.previous,"verifying")
        self.review = {"design_id":"d1","status":"verifying","reason":"Still missing operative dates",
                       "readiness":copy.deepcopy(self.previous["designs"][0]["readiness"]),
                       "evidence_refs":["synthetic:third-read"],"reopen_condition":None}

    def test_verification_review_appends_history_preserves_old(self):
        old=copy.deepcopy(self.previous)
        current=apply_reviews(self.previous,[self.review],"2026-10-06T01:00:00Z")
        self.assertEqual(self.previous,old)
        self.assertEqual(current["designs"][0]["history"][:-1],old["designs"][0]["history"])
        self.assertFalse(validate_registry(current,previous=old))

    def test_stop_requires_actionable_reopen_condition(self):
        self.review["status"]="stopped"
        with self.assertRaises(ValueError):
            apply_reviews(self.previous,[self.review])
        self.review["reopen_condition"]="Official missing-record release"
        self.assertEqual(apply_reviews(self.previous,[self.review])["designs"][0]["status"],"stopped")

    def test_review_cannot_promote_missing_readiness(self):
        self.review["status"]="data-ready"
        with self.assertRaises(ValueError):
            apply_reviews(self.previous,[self.review])

    def test_no_missing_evidence_or_duplicate_reviews(self):
        with self.assertRaises(ValueError):
            apply_reviews(self.previous,[self.review,self.review])
        self.review["evidence_refs"]=[]
        with self.assertRaises(ValueError):
            apply_reviews(self.previous,[self.review])

    def test_direct_same_stage_update_still_needs_evidence(self):
        current=copy.deepcopy(self.previous)
        event(current,"verifying")
        current["designs"][0]["history"][-1]["evidence_refs"]=[]
        self.assertTrue(any("verification review requires evidence" in x
                            for x in validate_registry(current,previous=self.previous)))


if __name__ == "__main__":
    unittest.main()
