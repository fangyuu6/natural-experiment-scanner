"""WP4 presentation contracts against stored evidence, without estimation/builds."""
from copy import deepcopy
from pathlib import Path
import re
import json
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

from scripts.did_samples.make_site_assets import adapt_worked_examples, render_event_study
from scripts.viz_site import build
from scripts.viz_site.make_case_assets import load_case_selection, merge_case_selection
from scripts.viz_site.tests.test_round5_display import Tree, visible
from scripts.viz_site.worked_examples_polish import pad_event_svg, numeric_punctuation

ROOT = Path(__file__).resolve().parents[3]
NS = {'s': 'http://www.w3.org/2000/svg'}


class PolishExamplesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        results = ROOT / 'scripts/did_samples/results'
        cls.selection = merge_case_selection(adapt_worked_examples(results),
                                             load_case_selection(results / 'cases'))
        cls.original = deepcopy(cls.selection)
        cls.html, cls.base = {}, {}
        template = (ROOT / 'scripts/viz_site/partials/worked_examples_body.html').read_text()
        for lang in ('en', 'zh'):
            errors = build.Errors()
            strings = build.load_dict('common', lang, errors)
            strings.update(build.load_dict('worked_examples', lang, errors))
            ctx = dict(lang=lang, page='worked_examples', strings=strings)
            cls.base[lang] = build._render_case_page(cls.selection, ctx, errors, template)
            cls.html[lang] = build.render_case_page(cls.selection, ctx, errors, template)
            if errors.errors:
                raise AssertionError(errors.errors)

    def test_each_readout_stays_with_its_plot_and_full_interval(self):
        for lang, html in self.html.items():
            self.assertNotIn('<p class="we-selection">', html)
            tree = Tree(html).root
            articles = [n for n in tree.nodes() if n.tag == 'article']
            self.assertEqual(len(articles), self.selection['shown'])
            for article in articles:
                blocks = [n for n in article.nodes() if n.has_class('we-outcome-block')]
                self.assertTrue(blocks)
                for block in blocks:
                    readouts = [n for n in block.nodes() if n.has_class('we-readout')]
                    plots = [n for n in block.nodes() if n.has_class('we-plot')]
                    self.assertEqual((len(readouts), len(plots)), (1, 1))
                    outcome = next(n for n in readouts[0].nodes() if n.has_class('we-outcome'))
                    title = next(n for n in plots[0].nodes() if n.tag == 'title')
                    self.assertEqual(visible(outcome), visible(title))
                    values = [n for n in readouts[0].nodes() if n.has_class('we-value')]
                    self.assertEqual(len(values), 2)
                    self.assertRegex(visible(values[1]), r'^\[[^]]+, [^]]+\]$')
                    self.assertFalse(any(n.tag == 'p' for n in plots[0].children if hasattr(n, 'tag')))
            self.assertEqual(html.count('we-coverage'), self.base[lang].count('we-coverage'))
        self.assertEqual(self.selection, self.original)

    def test_research_geometry_y_domain_marks_caps_and_copy_preserved(self):
        for lang in ('en', 'zh'):
            old = re.findall(r'<svg\b.*?</svg>', self.base[lang], re.S)
            new = re.findall(r'<svg\b.*?</svg>', self.html[lang], re.S)
            self.assertEqual(len(old), len(new))
            for source, output in zip(old, new):
                a, b = ET.fromstring(source), ET.fromstring(output)
                self.assertEqual(len(list(a.iter())), len(list(b.iter())))
                for before, after in zip(a.iter(), b.iter()):
                    self.assertEqual(before.tag, after.tag)
                    self.assertEqual(before.text, after.text)
                    classes = lambda n: set(n.get('class', '').split()) - {'we-x-narrow'}
                    self.assertEqual(classes(before), classes(after))
                    for key in ('cy', 'y', 'y1', 'y2', 'r', 'viewBox', 'role', 'aria-labelledby'):
                        self.assertEqual(before.get(key), after.get(key))
                    if 'points' in before.attrib:
                        ys = lambda n: [float(pair.split(',')[1]) for pair in n.get('points').split()]
                        self.assertEqual(ys(before), ys(after))
                    if 'd' in before.attrib:
                        # Vertical commands and every cap remain; only M/H x changes.
                        strip_x = lambda d: re.sub(r'([MH])-?\d+(?:\.\d+)?', r'\1X', d)
                        self.assertEqual(strip_x(before.get('d')), strip_x(after.get('d')))
                if a.get('class') == 'we-event-study':
                    for point in b.findall('s:circle', NS):
                        percent = float(re.search(r'calc\(([-\d.]+)%', point.get('style'))[1])
                        self.assertGreater(percent, 0)
                        self.assertLess(percent, 100)

    def test_phone_ticks_retain_zero_and_clearance_after_padding(self):
        for svg in re.findall(r'<svg id=.*?</svg>', self.html['en'], re.S):
            root = ET.fromstring(svg)
            ticks = [n for n in root.findall('s:text', NS) if 'we-x-narrow' in n.get('class', '').split()]
            self.assertEqual(sum(n.text == '0' for n in ticks), 1)
            self.assertGreaterEqual(len(ticks), 3)
            for a, b in zip(ticks, ticks[1:]):
                x = lambda n: float(n.get('x')[:-1])/100*297.6+float(n.get('dx'))
                self.assertGreaterEqual(x(b)-x(a)-(len(a.text)+len(b.text))*7.25/2, 8)

    def test_phone_restores_minus_ten_when_labels_fit(self):
        for svg in re.findall(r'<svg id=.*?</svg>', self.html['en'], re.S):
            root = ET.fromstring(svg)
            all_ticks = [n for n in root.findall('s:text', NS) if 'we-x-tick' in n.get('class', '').split()]
            values = [n.text for n in all_ticks]
            if values == ['−15', '−10', '−5', '0', '5', '10']:
                self.assertTrue(all('we-x-narrow' in n.get('class', '').split() for n in all_ticks))
                return
        self.fail('Stored example with the reviewed tick domain must remain present.')

    def test_reviewed_phone_domains_keep_their_regular_ticks(self):
        domains = {('−30', '−20', '−10', '0', '10', '20', '30'),
                   ('−20', '−15', '−10', '−5', '0', '5', '10'),
                   ('−25', '−20', '−15', '−10', '−5', '0', '5')}
        found = set()
        for svg in re.findall(r'<svg id=.*?</svg>', self.html['en'], re.S):
            ticks = [n for n in ET.fromstring(svg).findall('s:text', NS)
                     if 'we-x-tick' in n.get('class', '').split()]
            domain = tuple(n.text for n in ticks)
            if domain in domains:
                found.add(domain)
                self.assertTrue(all('we-x-narrow' in n.get('class', '').split() for n in ticks))
        self.assertEqual(found, domains)

    def test_han_latin_spacing_crosses_presentation_span_boundaries(self):
        samples = {
            '<span>-0.233 [-0.307, -0.158]</span>对数差': '<span>-0.233 [-0.307, -0.158]</span> 对数差',
            '<span>+0.2%</span><span>[−0.9%, 1.3%]</span>': '<span>+0.2% </span><span>[−0.9%, 1.3%]</span>',
            '<span>+0.5</span><span>[−0.4, 1.3]</span>百分点': '<span>+0.5 </span><span>[−0.4, 1.3]</span> 百分点',
        }
        for source, expected in samples.items():
            self.assertEqual(numeric_punctuation(source), expected)
            self.assertEqual(numeric_punctuation(expected), expected)
        for cell in (n for n in Tree(self.html['zh']).root.nodes() if n.tag == 'td'):
            self.assertNotRegex(visible(cell), r'\][\u3400-\u9fff]')

    def test_half_unit_inset_is_time_based_not_pixel_based(self):
        for first, last in ((-2, 3), (-34, 30), (-6, 2)):
            events = [dict(event_time=t, att=.1*t, simultaneous95=[.1*t-.2, .1*t+.2])
                      for t in range(first, last+1)]
            source = render_event_study(events, title='Outcome', description='All recorded points.',
                event_label='Event time', estimate_label='Estimate', figure_id='padding', plot_width=368)
            fragment = pad_event_svg(build.lean_event_svg(source), [p['event_time'] for p in events])
            root = ET.fromstring('<div>' + fragment + '</div>')[1]
            for time, point in zip(range(first, last+1), root.findall('s:circle', NS)):
                percent, offset = map(float, re.search(r'calc\(([-\d.]+)% \+ ([-\d.]+)px', point.get('style')).groups())
                for width in (297.6, 520, 1100):
                    actual = width*percent/100+offset
                    expected = 48+(time-first+.5)/(last-first+1)*(width-60)
                    self.assertAlmostEqual(actual, expected, delta=.01)

    def test_stopped_findings_reasons_values_and_variants_unchanged(self):
        for lang in ('en', 'zh'):
            old_tree, new_tree = Tree(self.base[lang]).root, Tree(self.html[lang]).root
            for cls in ('we-stopped', 'we-case-summary', 'we-case-variants', 'we-design', 'we-comparison-line'):
                old = [visible(numeric_node) for numeric_node in old_tree.nodes() if numeric_node.has_class(cls)]
                new = [visible(numeric_node) for numeric_node in new_tree.nodes() if numeric_node.has_class(cls)]
                self.assertEqual([re.sub(r'\s+', '', numeric_punctuation(t)) for t in old],
                                 [re.sub(r'\s+', '', t) for t in new], (lang, cls))
            for stopped in (n for n in new_tree.nodes() if n.has_class('we-stopped')):
                self.assertTrue(any(n.tag == 'strong' for n in stopped.nodes()))

    def test_punctuation_changes_numeric_intervals_only(self):
        self.assertEqual(numeric_punctuation('中文：［−2.1%，6.4%］；（95%）。'),
                         '中文：[−2.1%, 6.4%]；（95%）。')
        self.assertEqual(numeric_punctuation('［正文，标题］'), '［正文，标题］')
        self.assertEqual(numeric_punctuation('+2.1%［−2.1%，6.4%］'), '+2.1% [−2.1%, 6.4%]')
        self.assertEqual(numeric_punctuation('−4.4[−7.2, −1.7]百分点'), '−4.4 [−7.2, −1.7] 百分点')
        self.assertEqual(numeric_punctuation('<a href="#case">正文</a>'), '<a href="#case">正文</a>')


if __name__ == '__main__':
    unittest.main()
