"""Round-5 copy and wrapping contracts from stored records; no build or executor."""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

from scripts.did_samples.make_site_assets import adapt_worked_examples
from scripts.viz_site import build
from scripts.viz_site.make_case_assets import load_case_selection, merge_case_selection

ROOT = Path(__file__).resolve().parents[3]


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def has_class(self, name):
        return name in self.attrs.get('class', '').split()

    def nodes(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.nodes()

    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in ('br', 'wbr', 'img', 'input', 'meta', 'link', 'hr'):
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def visible(node):
    return re.sub(r'\s+', ' ', node.text().replace('\u2060', '')).strip()


class Round5DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = ROOT / 'scripts/did_samples/results'
        cls.selection = merge_case_selection(adapt_worked_examples(directory),
                                            load_case_selection(directory / 'cases'))
        cls.original = deepcopy(cls.selection)
        cls.html, cls.trees = {}, {}
        template = (ROOT / 'scripts/viz_site/partials/worked_examples_body.html').read_text()
        for lang in ('en', 'zh'):
            errors = build.Errors()
            strings = dict(build.load_dict('common', lang, errors))
            strings.update(build.load_dict('worked_examples', lang, errors))
            ctx = dict(lang=lang, page='worked_examples', strings=strings)
            cls.html[lang] = build.render_case_page(cls.selection, ctx, errors, template)
            if errors.errors:
                raise AssertionError(errors.errors)
            cls.trees[lang] = Tree(cls.html[lang]).root

    def article(self, identifier, lang='en'):
        return next(n for n in self.trees[lang].nodes()
                    if n.tag == 'article' and n.attrs.get('aria-labelledby') == identifier + '-heading')

    def summary(self, lang='en'):
        return next(n for n in self.trees[lang].nodes() if n.has_class('we-case-summary'))

    def test_m1_complete_design_period_nouns_have_no_duplicate_or_fiscal_comma(self):
        for identifier in ('shall_issue', 'minimum_wage_step', 'right_to_work',
                           'renewable_portfolio', 'death_penalty_abolition', 'smoke_free_air'):
            article = self.article(identifier)
            design = next(n for n in article.nodes() if n.has_class('we-design'))
            case = next(c for c in self.selection['cases'] if c['case_id'] == identifier)
            self.assertIn(str(case['design']['periods']) + ' annual periods.', visible(design))
        self.assertIn('31 fiscal years.', visible(self.article('cigarette_tax')))
        self.assertNotRegex(self.html['en'], r'periods periods|years, periods|fiscal years,\.')

    def test_s1_rps_full_revision_date_is_one_nowrap_token(self):
        benchmark = next(n for n in self.article('renewable_portfolio').nodes()
                         if n.has_class('we-comparison-line'))
        self.assertTrue(any(n.has_class('we-nowrap') and n.text() == 'November 12, 2021'
                            for n in benchmark.nodes()))
        benchmark = next(n for n in self.article('renewable_portfolio', 'zh').nodes()
                         if n.has_class('we-comparison-line'))
        self.assertTrue(any(n.has_class('we-nowrap') and visible(n) == '2021 年 11 月 12 日'
                            for n in benchmark.nodes()))

    def test_s2_all_five_simulation_notes_use_the_readouts_slot(self):
        for lang in ('en', 'zh'):
            for identifier in ('paid_sick_leave', 'right_to_work', 'renewable_portfolio',
                               'death_penalty_abolition', 'salary_history_ban'):
                article = self.article(identifier, lang)
                notes = [n for n in article.nodes() if n.has_class('we-interval-note')]
                self.assertEqual(len(notes), 1, (lang, identifier))
                self.assertTrue(notes[0].parent.has_class('we-readouts'), (lang, identifier))
                self.assertTrue(notes[0].parent.parent.has_class('we-figure'), (lang, identifier))
                direct = [n for n in article.children if isinstance(n, Node)]
                figure = next(n for n in direct if n.has_class('we-figure'))
                self.assertTrue(any(n.has_class('we-outcome-block') for n in figure.nodes()))
            self.assertEqual(sum(n.has_class('we-coverage') for n in self.trees[lang].nodes()), 4)
        for identifier, rate in (('right_to_work', '75.5%'), ('renewable_portfolio', '89.5%'),
                                 ('death_penalty_abolition', '85%'), ('salary_history_ban', '86%')):
            self.assertIn(rate + ' (nominal 95%)', visible(self.article(identifier)))

    def test_s3_zh_table_and_stop_pretty_wrap_and_log_word_bound(self):
        css = (ROOT / 'scripts/viz_site/design/worked_examples_cases.css').read_text()
        self.assertRegex(css, r'\.we-page:lang\(zh\)\s+\.we-cases td\s*\{[^}]*text-wrap:pretty')
        # The existing paragraph rule already reaches the stopped paragraphs.
        self.assertRegex(css, r'\.we-page:lang\(zh\)\s+\.we-cases :is\(p,figcaption,caption\)\s*\{[^}]*text-wrap:pretty')
        cannabis = next(n for n in self.trees['zh'].nodes()
                        if n.attrs.get('id') == 'adult_use_cannabis-heading')
        tokens = [n for n in cannabis.nodes() if n.has_class('we-nowrap') and n.text() == '对数']
        self.assertGreaterEqual(len(tokens), 2)
        self.assertTrue(any(n.has_class('we-nowrap') and n.text() == '名义'
                            for n in cannabis.nodes()))
        for identifier in ('death_penalty_abolition', 'salary_history_ban'):
            note = next(n for n in self.article(identifier, 'zh').nodes() if n.has_class('we-coverage'))
            self.assertTrue(any(n.has_class('we-nowrap') and visible(n) == '（名义 95%）'
                                for n in note.nodes()))

    def test_s4_s5_stopped_suffix_is_in_link_and_summary_units_can_wrap(self):
        for lang in ('en', 'zh'):
            stopped = [n for n in self.summary(lang).nodes() if n.has_class('we-stopped-row')]
            self.assertEqual(len(stopped), 4)
            for row in stopped:
                headings = [n for n in row.children if isinstance(n, Node) and n.tag == 'th']
                if headings:
                    heading = headings[0]
                    anchor = next(n for n in heading.nodes() if n.tag == 'a')
                    self.assertIn('Stopped' if lang == 'en' else '已停止', anchor.text())
                    self.assertEqual(heading.text(), anchor.text())
        for group in self.summary().nodes():
            if group.attrs.get('data-policy') in ('cigarette_tax', 'adult_use_cannabis'):
                cells = [n for n in group.nodes() if n.tag == 'td' and n.attrs.get('data-label') == 'Estimate']
                for cell in cells:
                    spans = [n for n in cell.nodes() if n.has_class('we-nowrap')]
                    self.assertTrue(spans)
                    self.assertNotIn('log difference', spans[0].text())
                    self.assertIn('log difference', cell.text())
                    self.assertTrue(any(visible(n) == 'log difference' for n in spans[1:]))

    def test_s6_variants_name_the_same_series_despite_legacy_diagnostic_labels(self):
        cases = {
            'salary_history_ban': {'primary/y/never_treated/placebo': ('Ages 25–54', '25–54 岁'),
                                   'primary/y_age3554/never_treated/placebo': ('Ages 35–54', '35–54 岁')},
            'renewable_portfolio': {'primary_1990_2015/y/never_treated/placebo': ('Retail electricity price', '零售电价')}}
        for lang in ('en', 'zh'):
            for identifier, expected in cases.items():
                article = self.article(identifier, lang)
                for identity, labels in expected.items():
                    row = next(n for n in article.nodes()
                               if identity in n.attrs.get('data-record-ids', '').split())
                    label = next(n for n in row.children if isinstance(n, Node) and n.tag == 'td')
                    self.assertEqual(visible(label), ('Placebo · ' if lang == 'en' else '安慰剂 · ') + labels[lang == 'zh'])
            for identifier in ('paid_sick_leave', 'medicaid_expansion'):
                variants = next(n for n in self.article(identifier, lang).nodes() if n.has_class('we-case-variants'))
                self.assertNotIn('state/adoption group' if lang == 'en' else '剔除州／采纳组', visible(variants))
                self.assertIn('jurisdiction/adoption group' if lang == 'en' else '剔除辖区／采纳组', visible(variants))

    def test_s7_zh_benchmark_prefix_and_fiscal_spacing_have_no_echo(self):
        for identifier in ('shall_issue', 'minimum_wage_step', 'right_to_work', 'renewable_portfolio'):
            benchmark = next(n for n in self.article(identifier, 'zh').nodes() if n.has_class('we-comparison-line'))
            self.assertFalse(visible(benchmark).startswith('文献对照：文献'))
        self.assertNotRegex(visible(self.article('cigarette_tax', 'zh')), r'2019\s*财年\s+全窗口')

    def test_s8_stop_sentences_bind_estimator_and_retain_computed_results(self):
        for lang in ('en', 'zh'):
            asides = [n for n in self.trees[lang].nodes() if n.has_class('we-stopped')]
            self.assertEqual(len(asides), 3)
            for aside in asides:
                self.assertTrue(any(n.has_class('we-nowrap') and n.text() == "Callaway–Sant'Anna"
                                    for n in aside.nodes()))
            cannabis = next(n for n in asides if n.attrs['id'] == 'adult_use_cannabis-heading')
            self.assertIn('0.0317', cannabis.text())
            self.assertIn('−0.0000609', cannabis.text())
            self.assertIn('0.0637', cannabis.text())
            if lang == 'en':
                self.assertIn("limit. Computed", visible(cannabis))
                self.assertIn('estimates: log nominal', visible(cannabis))
                self.assertIn('; log annual', visible(cannabis))

    def test_s9_only_combined_lede_drops_duplicate_sample_window(self):
        for lang in ('en', 'zh'):
            for identifier, window in (('paid_sick_leave', '2001–2019'),
                                       ('medicaid_expansion', '2010–2016')):
                article = self.article(identifier, lang)
                lede = next(n for n in article.nodes() if n.has_class('we-lede'))
                self.assertNotIn(window, visible(lede))
                design = next(n for n in article.nodes() if n.has_class('we-design'))
                self.assertIn(window, visible(design))
        # The appendix uses the same native scale and three significant digits
        # as the unified noise chart, including the stopped castle estimate.
        for identifier, values in {
                'paid_sick_leave': ('0.0203 log difference', '−0.00552 log difference'),
                'medicaid_expansion': ('−4.44 pp', '[−7.16, −1.71]')}.items():
            rendered = visible(self.article(identifier))
            for value in values:
                self.assertIn(value, rendered)
        castle = next(n for n in self.trees['en'].nodes()
                      if n.attrs.get('id') == 'castle_doctrine-heading')
        self.assertIn('0.114 [0.0318, 0.196] log difference', visible(castle))
        self.assertEqual(self.selection, self.original, 'Page rendering changed stored display evidence')
        note = next(n for n in self.article('paid_sick_leave', 'zh').nodes()
                    if n.has_class('we-interval-note'))
        self.assertIn('2001–2019 年：区间为近似区间；模拟覆盖率 86%。', visible(note))


if __name__ == '__main__':
    unittest.main()
