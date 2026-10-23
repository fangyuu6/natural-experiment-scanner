"""R28: the Screening candidate panel is read from the original page on every build, not copied (scripts/viz_site/legacy_detail.py).

This file is not called test_legacy_detail.py because the repository's usual command, unittest discover -p 'test_[!l]*.py', skips every test file whose
name starts with "test_l" (it is how the L2 registry tests are left out), and these tests must run in it."""
import contextlib
import io
import re
import unittest
from unittest.mock import patch

from scripts.viz_site import build_v5, legacy_detail as ld
from scripts.zh_pages import build as zh


class SourceReaders(unittest.TestCase):
    def test_a_block_ends_at_its_own_brace_whatever_strings_comments_and_patterns_hold(self):
        text = "x{ const a = '}'; const b = \"{\"; /* } */ // }\n const r = /[}]\\//g; const c = a.replace(/_/g, '}'); return a + b; }TAIL"
        self.assertEqual(text[ld._past_block(text, text.index('{')):], 'TAIL')
        with self.assertRaises(ld.SourceChanged):
            ld._past_block('x{ if (a) { }', 1)

    def test_stylesheet_rules_keep_their_media_conditions_and_quoted_braces(self):
        rules = ld._rules('a, b{c: d} @media (max-width: 1px){e{f: g} h{i: j}} k{content: "}"} l:not(.m), n{o: p}')
        self.assertEqual(rules, [(None, 'a, b', 'c: d'), ('@media (max-width: 1px)', 'e', 'f: g'), ('@media (max-width: 1px)', 'h', 'i: j'),
                                 (None, 'k', 'content: "}"'), (None, 'l:not(.m), n', 'o: p')])
        self.assertEqual(ld._split_selectors(':lang(zh) .a, .b:not(.c, .d), .e'), [':lang(zh) .a', '.b:not(.c, .d)', '.e'])

    def test_every_function_of_the_panel_is_found_in_the_original_page(self):
        for lang in ('en', 'zh'):
            code = ld.code(lang)
            for name in ld.FUNCTIONS:
                with self.subTest(lang=lang, function=name):
                    self.assertIn('function ' + ('buildDetailHtmlLegacy' if name == 'buildDetailHtml' else name) + '(', code)
            self.assertIn(ld.WINDOW_HOOK, code)

    def test_the_chinese_text_is_the_original_pages_own_dictionary(self):
        en, cn = ld.code('en'), ld.code('zh')
        for title in ('<h4>Overview</h4>', '<h4>Bill Info</h4>', '<h4>Text Provenance</h4>', '<h4>What & Who</h4>', '<h4>Literature</h4>', '<h4>Research Angles</h4>',
                      '<h4>RD Assessment</h4>', '<h4>DiD Assessment</h4>', '§ CHAPTERS (', 'Red flags:'):
            self.assertIn(title, en)
            self.assertNotIn(title, cn)
        for title in ('<h4>概览</h4>', '<h4>法案信息</h4>', '<h4>文本来源</h4>', '<h4>政策内容与对象</h4>', '<h4>文献</h4>', '<h4>研究角度</h4>', '<h4>RD 评估</h4>',
                      '<h4>DiD 评估</h4>', '§ 章节（', '风险提示：', 'const ZH_VOCAB', 'function zhLabel', 'labor_market:"劳动力市场"'):
            self.assertIn(title, cn)
            self.assertNotIn(title, en)
        self.assertNotRegex(en, r'[一-鿿]')

    def test_screening_changes_only_what_the_edits_list_and_each_must_fit_exactly_once(self):
        # Applied in front of the page's own text: the score label, the RD assignment rule and the wrapper name; nothing else differs.
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                plain = ld.absolute_links(ld.original(lang), lang)
                changed = ld.code(lang)
                self.assertNotEqual(plain, changed)
                restored = changed
                for _, english, chinese, replacement in ld.EDITS:
                    restored = restored.replace(replacement, chinese if lang == 'zh' else english)
                self.assertEqual(restored, plain)
        original = ld.original('en')
        for broken in (original.replace('escape(cleanLLM(r.rd_info))', 'escape(r.rd_info)'), original + original[original.index('async function buildDetailHtml'):][:80]):
            with patch.object(ld, 'original', return_value=broken), self.assertRaisesRegex(ld.SourceChanged, 'no longer fits'):
                ld.code('en')

    def test_a_source_without_a_function_stops_the_build(self):
        text = ld.source_text().replace('async function buildDetailHtml(', 'async function buildDetails(')
        with self.assertRaisesRegex(ld.SourceChanged, 'no function buildDetailHtml'):
            ld._function(text, 'buildDetailHtml')

    def test_css_is_the_original_rules_of_the_panel_without_the_tables_own(self):
        rules = ld.css_rules()
        self.assertGreaterEqual(len(rules), 40)
        selectors = ' | '.join(selector for _, selector, _ in rules)
        for needed in ('.detail-content', '.detail-section', '.ww-grid', '.lit-note', '.jur-badge', '.chapter-strip', '.chapter-pill.active', '.studies div'):
            self.assertIn(needed, selectors)
        for table_only in ('#tableView', '.detail-row', '.expand-icon', '.main-row', '.chapter-badge'):
            self.assertNotIn(table_only, selectors)
        by_selector = {selector: body for condition, selector, body in rules if not condition}
        self.assertIn('border-left: 3px solid #8b5cf6', by_selector['.chapter-strip'])
        self.assertIn('background: #faf5ff', by_selector['.chapter-strip'])
        self.assertIn('background: #7c3aed', by_selector['.chapter-pill.active'])
        self.assertIn('grid-template-columns: minmax(0, 2fr) minmax(0, 1fr)', by_selector['.detail-content'])
        # The two Chinese rules that undo inline italics and 12 px are aimed at Screening's panel instead of the table.
        self.assertIn(':lang(zh) .sc-detail [style*="font-style:italic"]', selectors)
        css = ld.css()
        self.assertEqual(css.count('{'), css.count('}'))
        self.assertIn('@media (max-width: 1100px){', css)

    def test_the_panel_equals_the_one_on_all_records_function_by_function_in_both_languages(self):
        # All records is built from the same page by scripts/zh_pages/build.py and build_v5.legacy_records; every function of Screening's panel
        # must equal its counterpart there, apart from the edits listed in legacy_detail.EDITS.
        originals = {}
        with contextlib.redirect_stdout(io.StringIO()), patch.object(zh, '_viz_build', build_v5.viz):
            self.assertEqual(zh.main(['--check'], rendered=originals), 0)
        for lang in ('en', 'zh'):
            records = build_v5.legacy_records(originals[lang]['policy_db'], lang)
            mine = ld.code(lang)
            for _, english, chinese, replacement in ld.EDITS:
                mine = mine.replace(replacement, chinese if lang == 'zh' else english)
            for name in ld.FUNCTIONS:
                with self.subTest(lang=lang, function=name):
                    self.assertEqual(ld._function(mine, name), ld._function(records, name))
            self.assertEqual(ld._statement(mine, '    const topicDisplayMap = {'), ld._statement(records, '    const topicDisplayMap = {'))
            if lang == 'zh':
                self.assertEqual(ld._statement(mine, '    const ZH_VOCAB = {'), ld._statement(records, '    const ZH_VOCAB = {'))
                self.assertEqual(ld._function(mine, 'zhLabel'), ld._function(records, 'zhLabel'))
            # The group link is the root-absolute one All records uses.
            self.assertIn('href="' + ('/zh' if lang == 'zh' else '') + '/policy_groups.html#grp-', mine)


if __name__ == '__main__':
    unittest.main()
