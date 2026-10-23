"""V5.1 (F3): two changes of the All records source (scripts/zh_pages/policy_db.en.html), checked in EN and ZH as the V5 build renders them.

1. A link built from an API field is not rendered when its address carries a credential query parameter. The guard is one function, written twice:
   at the top of the page script (the table's View link) and inside buildDetailHtml (the panel; Screening reads that function on its own).
   Behaviour in node is tested in scripts/viz_site/tests/test_detail_link_guard.py; this suite checks the source pair and the Chinese dictionary.
2. With the keyboard, a row, a link or a button never lands under the sticky header row: scroll-margin-top is the header height plus 8 px, only where the
   header sticks (above 700 px). The inset focus ring rules are not touched.
"""
from __future__ import annotations

import re
import unittest

from scripts.zh_pages.tests.test_focus_rings import BLUE, css_rules, render

NAMES = {"api[_-]?key", "key", "token", "access_token", "auth", "authorization", "password", "secret", "signature"}
LABELS = {"en": (">Open source document</a>", ">Open source page</a>", ">Open fetched text</a>", ">View</a>"),
          "zh": (">打开源文件</a>", ">打开来源页面</a>", ">打开已获取文本</a>", ">查看</a>")}
MARGIN_SELECTORS = ["#tableView tr.main-row", "#tableView td a", "#tableView td button", "#tableView td summary"]


def definitions(page):
    """Every definition of hasCredentialParam in the page, source as written."""
    out = []
    for found in re.finditer(r"function hasCredentialParam\(", page):
        depth, i = 0, page.index("{", found.end())
        start = i
        while True:
            depth += (page[i] == "{") - (page[i] == "}")
            i += 1
            if not depth:
                break
        out.append(page[found.start():i])
    return out


class LinkGuardSource(unittest.TestCase):
    def test_the_guard_is_one_function_in_the_table_and_in_the_panel(self):
        for lang, page in render("policy_db").items():
            with self.subTest(lang=lang):
                copies = definitions(page)
                self.assertEqual(len(copies), 2)
                self.assertEqual(len({re.sub(r"\s+", " ", copy) for copy in copies}), 1)
                names = re.search(r"return /\^\(\?:([^/]*)\)\$/i\.test\(name\.trim\(\)\)", copies[0])
                self.assertEqual(set(names[1].split("|")), NAMES)
                # the nested copy sits in the panel, the other before the table; both are used where the links are built
                self.assertIn("async function buildDetailHtml(r, containerId) {", page)
                panel = page[page.index("async function buildDetailHtml(r, containerId) {"):]
                self.assertIn("function hasCredentialParam(", panel[:1200])
                for call in ("hasCredentialParam(r.document_url)", "hasCredentialParam(r.fulltext_url)", "hasCredentialParam(viewUrl)"):
                    self.assertEqual(page.count(call), 1, call)
                self.assertNotRegex(page, r"escape\(\s*r\.(?:url|document_url|fulltext_url)\s*\)")

    def test_every_link_label_still_has_its_translation_once(self):
        # The dictionary pairs match the tail of each link; the guard keeps those tails whole, once each.
        for lang, page in render("policy_db").items():
            with self.subTest(lang=lang):
                for label in LABELS[lang]:
                    self.assertEqual(page.count(label), 1, label)
                if lang == "zh":
                    for label in LABELS["en"]:
                        self.assertNotIn(label, page)

    def test_a_row_a_link_or_a_button_stays_below_the_sticky_header_when_focus_moves(self):
        pages = render("policy_db")
        found = {}
        for lang, page in pages.items():
            rules = css_rules(page)
            found[lang] = sorted((sel, decl, context) for sel, decl, context in rules if "scroll-margin-top" in decl or "--thead-h" in decl)
            for selector in MARGIN_SELECTORS:
                margin = [(decl, context) for sel, decl, context in rules if sel == selector and "scroll-margin-top" in decl]
                self.assertEqual(margin, [("scroll-margin-top:calc(var(--thead-h)+8px);", "@media (min-width: 701px)")], (lang, selector))
            heights = sorted((decl, context) for sel, decl, context in rules if sel == "#tableView" and "--thead-h" in decl)
            self.assertEqual(heights, [("--thead-h:42px;", "@media (min-width: 701px)"), ("--thead-h:44px;", "@media (min-width: 701px) and (max-width: 900px)")], lang)
            # The header sticks above 700 px and is static below it, so the margin applies exactly where it sticks.
            sticky = [decl for sel, decl, context in rules if sel == "#tableView th" and not context]
            self.assertTrue(any("position:sticky;top:0;" in decl for decl in sticky), lang)
            static = [decl for sel, decl, context in rules if sel == "#tableView thead th" and "max-width: 700px" in context]
            self.assertTrue(any("position:static;" in decl for decl in static), lang)
            # The inset focus ring of a row is as it was.
            ring = [decl for sel, decl, context in rules if sel == "#tableView tr.main-row:focus-visible" and not context]
            self.assertEqual(ring, ["outline:2pxsolid" + BLUE + ";outline-offset:-2px;"], lang)
        self.assertEqual(found["en"], found["zh"], "the margin rules are the same in both languages")


if __name__ == "__main__":
    unittest.main()
