"""Fixed tracker selection respects legal scope and existing registrations."""
import unittest
from scripts.l2_pipeline.run5 import select_policies


def provision(tracker, pid, parent=None):
    return dict(tracker_id=tracker, policy_id=pid, title='Located legal provision '+pid,
                definition='Original question', conditional_parent_question_id=parent)


class Run5SelectionTests(unittest.TestCase):
    def test_duplicate_and_conditional_criteria_do_not_inflate_designs(self):
        rows = [provision('pdaps_naloxone', 'naloxone-thirdparty'),
                provision('pdaps_naloxone', 'pharmacist-dispensing-method__standing_order', 'parent'),
                provision('pdaps_naloxone', 'reasonable-care-condition', 'parent'),
                provision('pdaps_good_samaritan', 'goodsam-law'),
                provision('pdaps_good_samaritan', 'goodsam-paroleyn__unclear'),
                provision('pdaps_good_samaritan', 'goodsam-cs__arrest')]
        selected, excluded = select_policies(rows)
        self.assertEqual([p['policy_id'] for p in selected], ['naloxone-thirdparty', 'goodsam-cs__arrest'])
        self.assertEqual(len(excluded), 4)
        self.assertTrue(all(p['outcome']['target_population_gap'] for p in selected))
        self.assertTrue(all('dilution' in p['outcome']['proxy_justification'] for p in selected))
        self.assertEqual(rows[0]['definition'], 'Original question')

    def test_category_enters_predicate_and_sign_does_not_enter_selection(self):
        pid = 'pharmacist-dispensing-method__protocol_order'
        rows = [provision('pdaps_naloxone', pid, 'parent')]
        first, _ = select_policies(rows)
        rows[0]['untrusted_expected_sign'] = -1
        second, _ = select_policies(rows)
        self.assertEqual(first[0]['policy_id'], second[0]['policy_id'])
        self.assertIn('protocol_order', first[0]['definition'])
        self.assertEqual(first[0]['outcome']['years'], [1999, 2017])


if __name__ == '__main__':
    unittest.main()
