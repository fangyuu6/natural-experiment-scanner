"""V5.1 (F3): the 'Earlier public draft (June 2026)' link of Screening is alone in its paragraph, so it gets a 44 px tap target (one CSS rule, EN and ZH)."""
import re
import unittest
from pathlib import Path

from scripts.viz_site import restructure_screening as screening

RULE = '.sc-method p a{display:inline-flex;align-items:center;min-height:44px}'


class EarlierDraftLink(unittest.TestCase):
    def test_one_rule_gives_the_link_a_44px_target(self):
        self.assertEqual(screening.CSS.count(RULE), 1)
        # a link in a sentence of this block would get the same box; the block has exactly one link, the earlier draft
        source = Path(screening.__file__).read_text(encoding='utf-8')
        block = re.search(r'<details class="sc-method".*?</details>', source, re.S)[0]
        self.assertEqual(len(re.findall(r'<a ', block)), 1)
        self.assertIn("<p><a href=\"https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6487478\">{p('public_paper')}</a>{stop}</p>", block)


if __name__ == '__main__':
    unittest.main()
