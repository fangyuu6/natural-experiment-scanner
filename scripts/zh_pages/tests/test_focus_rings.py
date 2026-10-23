"""V5.1 keyboard focus: the three controls whose ring an ancestor can clip draw it inside themselves, in EN and ZH.

The Groups header sits in `.group-card` (overflow hidden), the Method sidebar links in `.sidebar` / `.sidebar-overlay` (overflow-y auto) and the
All records rows in `#tableView table` (overflow hidden / clip). A default ring (offset +2px) would be cut off at the edge of each, so each gets
an inset ring: 2px, the site's focus blue, offset -2px, with nothing after it that turns the outline off. Pixels are checked in a browser; this
suite checks the shipped CSS of both languages as the V5 build renders it (no files written).
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest

sys.dont_write_bytecode = True
from scripts.zh_pages import build

BLUE = "#1d4ed8"
# page -> (control, selectors that must each carry the inset ring, selector of the clipping ancestor and the clip that justifies the inset ring,
#          the element names a later, more general focus rule would be written for)
CONTROLS = {
    "policy_groups": ("Groups header", [".group-header:focus-visible"], (".group-card", "overflow:hidden"), (".group-header", "button")),
    "documentation": ("Method sidebar link", [".sidebar a:focus-visible", ".sidebar-overlay a:focus-visible"], (".sidebar", "overflow-y:auto"), ("a",)),
    "policy_db": ("All records row", ["#tableView tr.main-row:focus-visible"], ("#tableView table", "overflow:hidden"), ("tr.main-row", "tr", ".main-row")),
}


def css_rules(page):
    """[(selector, declarations without whitespace, at-rule context)] of every <style> block, in source order."""
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []

    def walk(text, context):
        i = 0
        while i < len(text):
            open_at = text.find("{", i)
            if open_at < 0:
                break
            depth, j = 1, open_at + 1
            while j < len(text) and depth:
                depth += (text[j] == "{") - (text[j] == "}")
                j += 1
            head, body = text[i:open_at].strip(), text[open_at + 1:j - 1]
            if head.startswith("@"):
                walk(body, (context + " " + head).strip())
            else:
                for selector in head.split(","):
                    out.append((re.sub(r"\s+", " ", selector.strip()), re.sub(r"\s+", "", body), context))
            i = j

    walk(css, "")
    return out


_RENDERED = {}
# The dictionary check wants every shared pair matched by some page of the same run, so Method and All records are built beside the Groups page.
BUILD_WITH = {"policy_groups": "policy_groups,documentation", "documentation": "policy_groups,documentation", "policy_db": "policy_groups,policy_db"}


def render(page):
    """{'en': html, 'zh': html} of one page as the V5 build renders it (the same call as `zh_pages/build.py --check`; nothing is written)."""
    pages = BUILD_WITH[page]
    if pages not in _RENDERED:
        rendered = {}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = build.main(["--check", "--pages", pages], rendered=rendered)
        assert code == 0, f"zh_pages build of {pages} failed with code {code}: {stderr.getvalue().strip()}"
        _RENDERED[pages] = rendered
    return {lang: _RENDERED[pages][lang][page] for lang in ("en", "zh")}


class FocusRings(unittest.TestCase):
    def check_ring(self, page):
        name, selectors, (ancestor, clip), heads = CONTROLS[page]
        pages = render(page)
        for lang in ("en", "zh"):
            rules = css_rules(pages[lang])
            for selector in selectors:
                with self.subTest(lang=lang, control=name, selector=selector):
                    found = [decl for sel, decl, context in rules if sel == selector and "print" not in context]
                    self.assertEqual(len(found), 1, "one rule for the selector")
                    self.assertIn(f"outline:2pxsolid{BLUE}", found[0])
                    offset = re.search(r"outline-offset:(-?\d+)px", found[0])
                    self.assertTrue(offset and int(offset[1]) <= -2, "offset of -2px or more, so the whole ring lies inside the box")
                    # Nothing after the ring turns the outline off or moves it outside the box again.
                    at = max(i for i, (sel, _, _) in enumerate(rules) if sel == selector)
                    base = selector.split(":focus-visible")[0]
                    for sel, decl, context in rules[at + 1:]:
                        element = sel.split(" ")[-1].split(":")[0]
                        if ":focus" in sel and (sel.startswith(base) or element in heads + ("", "*", "[tabindex]")):
                            self.assertNotRegex(decl, r"outline(?:-style|-width)?:(?:none|0)", (sel, context))
                            self.assertNotRegex(decl, r"outline-offset:(?:0|[1-9])", (sel, context))
            with self.subTest(lang=lang, control=name, clip=ancestor):
                # The clip that makes an inset ring necessary; if it goes, the ring is still fine and this tells whoever removed it.
                self.assertTrue(any(sel == ancestor and clip in decl and "print" not in context for sel, decl, context in rules), ancestor + " " + clip)
        for selector in selectors:  # EN and ZH carry the same rule
            same = [sorted((sel, decl) for sel, decl, _ in css_rules(pages[lang]) if sel == selector) for lang in ("en", "zh")]
            self.assertEqual(same[0], same[1], selector)

    def test_groups_header_ring_is_inset_in_both_languages(self):
        self.check_ring("policy_groups")
        for lang, text in render("policy_groups").items():
            self.assertRegex(text, r"""<button type="button" class="group-header"[^>]*aria-expanded""", lang)

    def test_method_sidebar_link_ring_is_inset_in_both_languages(self):
        self.check_ring("documentation")
        for lang, text in render("documentation").items():
            self.assertRegex(text, r'(?s)<nav class="sidebar" id="sidebar">.*?<a href="#', lang)

    def test_all_records_row_ring_is_inset_in_both_languages(self):
        self.check_ring("policy_db")
        for lang, text in render("policy_db").items():
            self.assertIn('<tr class="main-row" tabindex="0"', text, lang)
            self.assertRegex(text, r'tr\.addEventListener\("keydown"', lang)


if __name__ == "__main__":
    unittest.main()
