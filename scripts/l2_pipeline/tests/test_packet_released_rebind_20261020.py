"""T179 (hub approval 2026-10-20, R2): a sealed design whose reviewer packet was released by workers 3 times carries a STOP text
that no rebind impact tag matched. The new tag 'packet_released' recognises exactly that text. Invented inputs only."""
import unittest
from scripts.l2_pipeline.maintenance import reason_tags,require_match

STOP='ValueError: Sonnet packet released by workers 3 times; HUB_QUEUE required'


class PacketReleasedTag(unittest.TestCase):
    def test_tag_on_the_stop_text(self):
        self.assertEqual(reason_tags(STOP),['packet_released'])
        self.assertEqual(require_match(STOP,['packet_released']),['packet_released'])

    def test_other_stops_do_not_match(self):
        for reason in ('ValueError: NEEDS_RULING: something else','ValueError: Single review resolution signer differs','ValueError: packet'):
            self.assertNotIn('packet_released',reason_tags(reason))
            with self.assertRaises(ValueError):require_match(reason,['packet_released'])

    def test_c57_tags_do_not_match_this_stop(self):
        with self.assertRaises(ValueError):require_match(STOP,['preparation_binding','draft_load','review_should'])
