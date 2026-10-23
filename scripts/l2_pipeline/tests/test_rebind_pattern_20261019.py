"""B-group rebind (hub approval 2026-10-19): the C57 rebind route (requeue + require_match) must recognise the retained STOP text
'Single review resolution signer differs' as a preparation-binding defect. Invented inputs only."""
import unittest
from scripts.l2_pipeline.maintenance import reason_tags,require_match

SIGNER='ValueError: Single review resolution signer differs'
C57_TAGS=['preparation_binding','draft_load','review_should']   # c57.migrate impact_tags


class SignerStopIsAPreparationBindingDefect(unittest.TestCase):
    def test_reason_carries_the_preparation_binding_tag(self):
        self.assertIn('preparation_binding',reason_tags(SIGNER))

    def test_c57_impact_tags_match_the_signer_stop(self):
        self.assertEqual(require_match(SIGNER,C57_TAGS),['preparation_binding'])

    def test_unrelated_stops_still_do_not_match(self):
        for reason in ('ValueError: NEEDS_RULING: something else entirely','ValueError: Run failure before terminal: x','ValueError: signer'):
            self.assertNotIn('preparation_binding',reason_tags(reason))
            with self.assertRaises(ValueError):require_match(reason,C57_TAGS)
