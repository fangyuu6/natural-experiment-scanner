"""Presentation contracts; read stored noise data, never run estimation."""
from copy import deepcopy
from pathlib import Path
import json
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

from scripts.viz_site import make_synthesize as synth
from scripts.viz_site.tests.test_polish_shared import NODE_HARNESS
from scripts.viz_site.tests.test_round5_display import Node, Tree, visible

ROOT = Path(__file__).resolve().parents[3]


def motion_harness():
    """Reuse the established clocks, observers and WAAPI mocks, with geometry."""
    setup = NODE_HARNESS.split('// Unsupported, reduce')[0]
    setup = setup.replace('constructor(fn){if(options.observerFail)',
                          'constructor(fn,settings){this.settings=settings;if(options.observerFail)')
    factory = NODE_HARNESS.split('function revealTargets(count=15')[1].split(
        "for(const reason of ['complete'")[0]
    return setup + 'function revealTargets(count=15' + factory + r'''
function flushAll(h){let frames=0;while(h.frames.size){assert.ok(++frames<100,'RAF queue must settle');h.flushFrame()}}
function phone(h,height,width=375){h.window.innerHeight=height;h.window.innerWidth=width;h.root.clientHeight=height;h.root.clientWidth=width}
function rect(el,top,height,width=375){el.rect={top,bottom:top+height,height,left:0,right:width,width}}
function finalOpacity(target){return target.style.getPropertyValue('opacity')||'1'}
'''



def adoption_fixture():
    """Synthetic scores distinguish display preservation from a fitted association."""
    rows = []
    for key, events, scored_events, first, n in [('mw', 69, 48, 1986, 18), ('gt', 44, 23, 1992, 11)]:
        intervals = {gain: dict(available=key == 'gt', lower=-2.123456, upper=.987654,
                                reason='54/999 resamples failed', failed=54, B=999)
                     for gain in ('Delta_P', 'Delta_E')}
        rows.append(dict(key=key, scope=dict(events=events, scored_events=scored_events,
                         decision_years=[first, 2024]), forward=dict(S=list(range(n)),
                         gains=dict(Delta_P=-.123456, Delta_E=-1.234567)),
                         intervals=intervals))
    return rows


def adoption_panel_width(viewport, html):
    """Resolve the emitted layout, including 820 px's two narrow columns."""
    body_padding = float(re.search(r'body\s*\{[^{}]*padding:\s*([\d.]+)rem', html)[1]) * 16
    for breakpoint, padding in re.findall(
            r'@media\s*\(max-width:\s*(\d+)px\)\s*\{\s*body\s*\{\s*padding:\s*([\d.]+)rem', html):
        if viewport <= int(breakpoint):
            body_padding = float(padding) * 16
    shell = float(re.search(r'--nx-shell:([\d.]+)px', html)[1])
    synth_width = float(re.search(r'\.synth\{max-width:([\d.]+)px', html)[1])
    figure = re.search(r'\.synth figure\{([^{}]+)\}', synth.CSS)[1]
    figure_padding = float(re.search(r'padding:([\d.]+)px', figure)[1])
    border = float(re.search(r'border:([\d.]+)px', figure)[1])
    columns = 2
    for media in re.finditer(r'@media\(max-width:(\d+)px\)\{(.*?)(?=@media|\Z)', synth.CSS, re.S):
        if viewport > int(media[1]):
            continue
        padding = re.search(r'\.synth figure\{padding:([\d.]+)px', media[2])
        if padding:
            figure_padding = float(padding[1])
        if re.search(r'\.adoption-panels\{grid-template-columns:1fr', media[2]):
            columns = 1
    grid = re.search(r'\.adoption-panels\{([^{}]+)\}', synth.CSS)[1]
    gap = float(re.search(r'gap:([\d.]+)px', grid)[1])
    inner = min(viewport - 2 * body_padding, shell, synth_width) - 2 * (figure_padding + border)
    return (inner - gap * (columns - 1)) / columns


def adoption_label_boxes(axis, svg, panel_width, lang):
    """Screen-space line boxes from actual SVG anchors and emitted HTML styles.

    Use conservative advances at the axis's 12 px font (0.7 em for Latin,
    1 em for CJK), so this test needs no browser or platform font package.
    """
    tokens = (ROOT / 'scripts/viz_site/design/design_tokens.css').read_text()
    body_leading = float(re.search(r':root\{[^{}]*--nx-lh-body:([\d.]+)', tokens)[1])
    if lang == 'zh':
        body_leading = float(re.search(r':root:lang\(zh\)\{[^{}]*--nx-lh-body:([\d.]+)', tokens)[1])
    font_size = float(re.search(r'\.plot-axis\{[^{}]*font-size:([\d.]+)px', synth.CSS)[1])
    caption_css = re.search(r'\.plot-axis \.zero-label\{([^{}]+)\}', synth.CSS)[1]
    caption_top = float(re.search(r'top:([-\d.]+)px', caption_css)[1])
    caption_height = float(re.search(r'line-height:([\d.]+)px', caption_css)[1])
    viewbox = list(map(float, svg.attrs['viewbox'].split()))
    grid_x = [float(n.attrs['x1']) for n in svg.nodes() if n.has_class('grid-line')]
    low, high = min(grid_x), max(grid_x)
    axis_width = panel_width * (high - low) / viewbox[2]
    inset = panel_width * low / viewbox[2]
    boxes = []
    for node in (n for n in axis.children if hasattr(n, 'tag')):
        if node.has_class('tick-secondary') and panel_width <= float(node.attrs['data-min-panel-width']):
            continue
        text = visible(node)
        width = font_size * sum(1 if '\u3400' <= ch <= '\u9fff' or '\uff00' <= ch <= '\uffef' else .7 for ch in text)
        style = node.attrs['style']
        if 'right:0;left:auto' in style:
            x = axis_width
            left = x - width
        else:
            x = float(re.search(r'left:([\d.]+)%', style)[1]) / 100 * axis_width
            left = x if node.has_class('tick') and float(node.attrs['data-svg-x']) == low else x - width / 2
        if node.has_class('tick'):
            # Check the HTML anchor against the SVG, rather than trusting metadata.
            svg_x = float(node.attrs['data-svg-x'])
            if abs(x - (svg_x - low) / viewbox[2] * panel_width) > .001:
                raise AssertionError('HTML tick is detached from its SVG anchor')
        y = caption_top if node.has_class('zero-label') else 0
        height = caption_height if node.has_class('zero-label') else font_size * body_leading
        boxes.append((text, inset + left, y, inset + left + width, y + height))
    return boxes


class SynthesizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noise = json.loads((ROOT / 'docs/plans/l3-results/NOISE_FIGURE_DATA.json').read_text())
        cls.adoption = adoption_fixture()
        cls.original = deepcopy((cls.adoption, cls.noise))
        cls.html = {lang: synth.page(lang, cls.adoption, cls.noise) for lang in ('en', 'zh')}
        cls.tree = {lang: Tree(html).root for lang, html in cls.html.items()}

    def figure(self, lang, identifier):
        return next(n for n in self.tree[lang].nodes() if n.attrs.get('id') == identifier)

    def test_short_hero_scope_and_three_linked_levels(self):
        for lang in ('en', 'zh'):
            tree = self.tree[lang]
            hero = next(n for n in tree.nodes() if n.has_class('synth-hero'))
            headings = [n for n in hero.nodes() if n.tag == 'h1']
            self.assertEqual(len(headings), 1)
            self.assertEqual(visible(headings[0]), synth.COPY[lang]['headline'].replace('<wbr>', ''))
            intro = [n for n in hero.children if hasattr(n, 'tag') and n.tag == 'p' and not n.has_class('synth-kicker')]
            self.assertEqual(len(intro), 1)
            self.assertEqual(visible(intro[0]), 'Minimum wage and gas tax: the simplest model scored best.' if lang == 'en' else '最低工资与汽油税：最简模型得分最高。')
            levels = next(n for n in tree.nodes() if n.has_class('synth-levels'))
            rows = [n for n in levels.nodes() if n.has_class('level')]
            self.assertEqual(len(rows), 3)
            links = [n for n in rows if n.tag == 'a']
            prefix = '/zh' if lang == 'zh' else ''
            self.assertEqual([n.attrs['href'] for n in links], [prefix + '/policy_db.html#explore', '/viz/' + lang + '/worked_examples.html'])
            self.assertTrue(rows[2].has_class('level-current'))
            self.assertFalse(any('data-nx-reveal' in n.attrs or 'data-nx-intro' in n.attrs for n in rows[2].nodes()))
            self.assertLess(self.html[lang].index('synth-levels'), self.html[lang].index('<figure'))
            text = visible(tree)
            for forbidden in ('Fixed in advance', 'reported as it came out', 'stacking L1', '逐层积累'):
                self.assertNotIn(forbidden, text)
        self.assertIn('data-nx-wrap="phrase"', self.html['zh'])
        self.assertIn('党派控制没有帮助<wbr>这些预测。', self.html['zh'])

    def test_adoption_preserves_scores_intervals_and_distinct_sample_windows(self):
        for lang in ('en', 'zh'):
            figure = self.figure(lang, 'adoption-test')
            panels = [n for n in figure.nodes() if n.has_class('policy-panel')]
            self.assertEqual(len(panels), 2)
            self.assertEqual(visible(figure).count(synth.COPY[lang]['training']), 1)
            children = [n for n in figure.children if hasattr(n, 'tag')]
            self.assertEqual(children[0].tag, 'h2')
            self.assertEqual(visible(children[1]), synth.COPY[lang]['training'])
            for panel, source in zip(panels, self.adoption):
                text = visible(panel)
                scope = source['scope']
                self.assertIn(('in sample' if lang == 'en' else '样本内'), text)
                self.assertIn(str(scope['events']), text)
                self.assertIn(f'{min(scope["decision_years"])}–2024', text)
                self.assertIn(str(scope['scored_events']), text)
                ranges = [n for n in panel.nodes() if n.has_class('gain-range')]
                self.assertEqual(len(ranges), 2)
                if source['key'] == 'mw':
                    self.assertTrue(all(n.attrs.get('aria-hidden') == 'true' and not visible(n) for n in ranges))
            table = next(n for n in figure.nodes() if n.tag == 'tbody')
            rows = [n for n in table.children if hasattr(n, 'tag') and n.tag == 'tr']
            self.assertEqual(len(rows), 4)
            for row, key in zip(rows, ('Delta_P', 'Delta_E') * 2):
                self.assertIn(synth.value(self.adoption[0]['forward']['gains'][key], 6), visible(row))
            self.assertEqual(sum(n.has_class('zero-label') for n in figure.nodes()), 2)
            axes = [n for n in figure.nodes() if n.has_class('plot-axis')]
            for axis in axes:
                ticks = [visible(n) for n in axis.children if hasattr(n, 'tag') and not n.has_class('zero-label')]
                self.assertEqual(ticks, ['−6', '−3', '0', '1.5'])
                tick_nodes = [n for n in axis.children if hasattr(n, 'tag') and n.has_class('tick')]
                for node, expected in zip(tick_nodes, (0, 40, 80, 100)):
                    position = 100 if 'right:0;left:auto' in node.attrs['style'] else float(re.search(r'left:([\d.]+)%', node.attrs['style'])[1])
                    self.assertAlmostEqual(position, expected, delta=.001)
            details = next(n for n in figure.nodes() if n.tag == 'details')
            self.assertIn('54/999', visible(details))
            descriptions = [visible(n) for n in panels[0].nodes() if n.tag == 'desc']
            self.assertEqual(len(descriptions), 2)
            self.assertTrue(all('54/999' in text for text in descriptions))
            interval_cells = [n for n in rows[0].children if hasattr(n, 'tag') and n.tag == 'td']
            self.assertEqual(visible(interval_cells[-1]), 'Unavailable' if lang == 'en' else '不可用')
            self.assertEqual(sum('54/999' in visible(n) for n in panels[0].nodes() if n.tag == 'p'), 1)

    def test_adoption_label_boxes_never_overlap_at_reviewed_viewports(self):
        self.assertNotRegex(synth.CSS, r'\.policy-panel\{[^{}]*container-type:')
        self.assertRegex(synth.CSS, r'\.adoption-axis\{[^{}]*container-type:inline-size')
        density = re.search(r'@container\(max-width:([\d.]+)px\)\{\.adoption-axis \.tick-secondary\{display:none\}', synth.CSS)
        self.assertIsNotNone(density)
        self.assertRegex(synth.CSS, r'\.plot-axis \.zero-label\{[^{}]*white-space:nowrap')
        for lang in ('en', 'zh'):
            panels = [n for n in self.figure(lang, 'adoption-test').nodes() if n.has_class('policy-panel')]
            for viewport in (320, 390, 820, 1440):
                width = adoption_panel_width(viewport, self.html[lang])
                for index, panel in enumerate(panels):
                    with self.subTest(lang=lang, viewport=viewport, panel=index):
                        axis = next(n for n in panel.nodes() if n.has_class('adoption-axis'))
                        svg = next(n for n in panel.nodes() if n.tag == 'svg')
                        secondary = next(n for n in axis.nodes() if n.has_class('tick-secondary'))
                        self.assertAlmostEqual(float(secondary.attrs['data-min-panel-width']) * .96, float(density[1]))
                        boxes = adoption_label_boxes(axis, svg, width, lang)
                        self.assertEqual(len(boxes), 4 if width * .96 <= float(density[1]) else 5)
                        for label, left, _, right, _ in boxes:
                            self.assertGreaterEqual(left, 0, label)
                            self.assertLessEqual(right, width, label)
                        for i, (label, left, top, right, bottom) in enumerate(boxes):
                            for other, x0, y0, x1, y1 in boxes[i + 1:]:
                                self.assertFalse(left < x1 and x0 < right and top < y1 and y0 < bottom,
                                                 f'{label!r} overlaps {other!r}: {boxes}')

    def test_label_box_check_detects_the_reported_legacy_one_tick_collision(self):
        for lang in ('en', 'zh'):
            panel = next(n for n in self.figure(lang, 'adoption-test').nodes() if n.has_class('policy-panel'))
            svg = next(n for n in panel.nodes() if n.tag == 'svg')
            axis = next(n for n in Tree(synth.axis(-6, 1.5, zero_label=synth.COPY[lang]['zero'])).root.nodes()
                        if n.has_class('adoption-axis'))
            legacy = Node('span', [('class', 'tick'), ('style', 'left:93.333%'), ('data-svg-x', '916')])
            legacy.children = ['1']
            endpoint = next(n for n in axis.children if hasattr(n, 'tag') and visible(n) == '1.5')
            axis.children.insert(axis.children.index(endpoint), legacy)
            boxes = adoption_label_boxes(axis, svg, adoption_panel_width(320, self.html[lang]), lang)
            one = next(box for box in boxes if box[0] == '1')
            high = next(box for box in boxes if box[0] == '1.5')
            self.assertLess(one[1], high[3])
            self.assertLess(high[1], one[3])

    def test_level_highlight_is_immediately_visible_and_does_not_queue_figures(self):
        for lang in ('en', 'zh'):
            intros = [n for n in self.tree[lang].nodes() if 'data-nx-intro' in n.attrs]
            self.assertEqual([n.attrs.get('id') for n in intros], ['adoption-test', 'noise-floor'])
            first_target = next(n for n in intros[0].nodes() if 'data-nx-reveal' in n.attrs)
            self.assertEqual(first_target.tag, 'svg')
        self.assertNotIn('targets.push', synth.MOTION)
        self.assertNotIn('level-current', synth.MOTION)
        self.assertNotIn('setTimeout', synth.MOTION)

    def test_first_plot_starts_within_300ms_with_the_level_highlight_in_view(self):
        self.assertIsNotNone(shutil.which('node'), 'Node is required for motion callback verification')
        # Reuse the shared runtime's existing observer/RAF and animation mocks.
        harness = motion_harness()
        page_hook = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', synth.MOTION, re.S))
        harness += '''
const h=setup(),figure=h.element(),noise=h.element(),plots=revealTargets(4),level=revealTargets(1),noisePlots=revealTargets(12);
figure.id='adoption-test';noise.id='noise-floor';
figure.querySelectorAll=()=>plots.targets;noise.querySelectorAll=()=>noisePlots.targets;
const document={querySelectorAll:()=>[figure,noise],querySelector:()=>level.targets[0]};
vm.runInNewContext(PAGE_HOOK,{window:h.window,document});
assert.equal(h.observers.length,2,'The level must not register a queued intro before Figure 1.');
h.enter(figure,1,true);h.advance(16);flushAll(h);
assert.equal(plots.animations.length,4,'All four plots must start in the first scheduling frame.');
assert.equal(level.animations.length,0,'The visible level uses its static final state.');
assert.ok(16+plots.animations[0].settings.delay<=300);
assert.equal(plots.animations[0].settings.delay,0);
h.api.finishAll();console.log('synthesize motion behavior OK');
'''.replace('PAGE_HOOK', json.dumps(page_hook))
        runtime = (ROOT / 'scripts/viz_site/partials/motion.html').read_text()
        script = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', runtime, re.S))
        result = subprocess.run(['node', '-e', harness], input=json.dumps(script),
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('synthesize motion behavior OK', result.stdout)

    def test_desktop_panels_share_seven_rows_and_reserve_missing_range_height(self):
        self.assertRegex(synth.CSS, r'@media\(min-width:820px\)\{\.adoption-panels\{grid-template-rows:repeat\(7,auto\);row-gap:0\}\.policy-panel\{display:grid;grid-row:span 7;grid-template-rows:subgrid\}')
        self.assertRegex(synth.CSS, r'\.gain-range\{[^{}]*min-height:20px')
        for lang in ('en', 'zh'):
            panels = [n for n in self.figure(lang, 'adoption-test').nodes() if n.has_class('policy-panel')]
            for index, panel in enumerate(panels):
                children = [n for n in panel.children if hasattr(n, 'tag')]
                self.assertEqual(len(children), 7)
                self.assertEqual(children[0].tag, 'h3')
                self.assertTrue(children[1].has_class('policy-scope'))
                self.assertTrue(children[2].has_class('interval-reason'))
                self.assertTrue(all(n.has_class('gain-row') for n in children[3:5]))
                self.assertTrue(children[5].has_class('adoption-axis'))
                self.assertTrue(children[6].has_class('axis-caption'))
                if index == 1:
                    self.assertEqual(visible(children[2]), '')
                    self.assertEqual(children[2].attrs.get('aria-hidden'), 'true')

    def test_zh_paragraphs_keep_two_to_four_final_cjk_characters_together(self):
        self.assertRegex(synth.CSS, r'\.zh-tail\{white-space:nowrap\}')
        main = next(n for n in self.tree['zh'].nodes() if n.tag == 'main')
        paragraphs = [n for n in main.nodes() if n.tag == 'p' and re.search(r'[\u3400-\u9fff]{2,}[。！？]?$', visible(n))]
        self.assertGreater(len(paragraphs), 5)
        for paragraph in paragraphs:
            tails = [n for n in paragraph.nodes() if n.has_class('zh-tail')]
            self.assertEqual(len(tails), 1, visible(paragraph))
            self.assertRegex(visible(tails[0]), r'^[\u3400-\u9fff]{2,4}[。！？]?$')
            self.assertTrue(visible(paragraph).endswith(visible(tails[0])))
        linked = next(n for n in paragraphs if any(child.tag == 'a' for child in n.nodes()))
        self.assertIn(synth.CITATION, next(n.attrs['href'] for n in linked.nodes() if n.tag == 'a'))

    def test_noise_edges_have_one_short_label_outside_data_rows(self):
        for lang in ('en', 'zh'):
            figure = self.figure(lang, 'noise-floor')
            keys = [n for n in figure.nodes() if n.has_class('band-key')]
            self.assertEqual(len(keys), 1)
            self.assertEqual(visible(figure).count(synth.COPY[lang]['band_label']), 1)
            self.assertEqual(visible(keys[0]), synth.COPY[lang]['band_label'])
            self.assertEqual(keys[0].parent.tag, 'p')
            self.assertIs(keys[0].parent.parent, figure)
            band = next(n for n in keys[0].nodes() if n.has_class('band'))
            edge = next(n for n in keys[0].nodes() if n.has_class('band-edge'))
            edges = list(map(float, re.findall(r'M([\d.]+) [\d.]+V[\d.]+', edge.attrs['d'])))
            self.assertEqual(edges, [float(band.attrs['x']), float(band.attrs['x']) + float(band.attrs['width'])])
            for row in (n for n in figure.nodes() if n.has_class('noise-row')):
                self.assertNotIn(synth.COPY[lang]['band_label'], visible(row))

    def test_noise_assessed_plot_and_complete_table_keep_original_order_and_values(self):
        assessed = [r for r in self.noise['rows'] if r['reference_available']]
        for lang in ('en', 'zh'):
            figure = self.figure(lang, 'noise-floor')
            plots = [n for n in figure.nodes() if n.has_class('noise-row')]
            self.assertEqual([int(n.attrs['data-outcome-order']) for n in plots], [r['order'] for r in assessed])
            self.assertEqual(len(plots), 12)
            self.assertEqual(sum(n.attrs.get('data-reference') == 'false' for n in plots), 0)
            statuses = [visible(n) for n in figure.nodes() if n.has_class('noise-value')]
            self.assertEqual(sum(('Outside band' if lang == 'en' else '带外') in s for s in statuses), 4)
            self.assertIn('4 of 12' if lang == 'en' else '12 个估计中，4 个', visible(figure))
            table = next(n for n in figure.nodes() if n.tag == 'tbody')
            rows = [n for n in table.children if hasattr(n, 'tag') and n.tag == 'tr']
            self.assertEqual(len(rows), 15)
            for row, source in zip(rows, self.noise['rows']):
                text = visible(row)
                self.assertIn(synth.UNIT_ZH[source['original_units']] if lang == 'zh' else source['original_units'], text)
                for number in [source['estimate'], *source['ci95']]:
                    self.assertIn(synth.value(number, 6), text)
                if source['reference_available']:
                    self.assertIn(synth.value(source['reference_threshold'], 6), text)
                else:
                    self.assertEqual(text.count('Not yet assessed' if lang == 'en' else '尚未评估'), 1)
            renewable = next(n for n in plots if int(n.attrs['data-outcome-order']) == 10)
            readout = next(n for n in renewable.nodes() if n.has_class('noise-value'))
            self.assertIn('0.00047', visible(readout))
            self.assertIn('Restaurant weekly wages' if lang == 'en' else '餐饮业周工资', visible(figure))
            self.assertIn('Log average restaurant weekly wage' if lang == 'en' else '餐饮业平均周工资的对数', visible(table))
            self.assertIn('Log average restaurant weekly wage' if lang == 'en' else '餐饮业平均周工资的对数', next(visible(n) for n in next(p for p in plots if int(p.attrs['data-outcome-order']) == 7).nodes() if n.tag == 'title'))
        self.assertEqual((self.adoption, self.noise), self.original)

    def test_all_tables_retain_header_associations_and_translated_mobile_labels(self):
        for lang, tree in self.tree.items():
            for table in (n for n in tree.nodes() if n.tag == 'table'):
                head = next(n for n in table.nodes() if n.tag == 'thead')
                columns = [n for n in head.nodes() if n.tag == 'th']
                self.assertTrue(all(n.attrs.get('scope') == 'col' for n in columns))
                body = next(n for n in table.nodes() if n.tag == 'tbody')
                for row in (n for n in body.children if hasattr(n, 'tag') and n.tag == 'tr'):
                    cells = [n for n in row.children if hasattr(n, 'tag') and n.tag in ('td', 'th')]
                    self.assertEqual(cells[0].attrs.get('scope'), 'row')
                    self.assertEqual([c.attrs.get('data-label') for c in cells[1:]], [visible(c) for c in columns[1:]])
            if lang == 'zh':
                for n in tree.nodes():
                    if n.tag in ('td', 'desc', 'figcaption'):
                        self.assertNotRegex(visible(n), r'\bto\b|须线')
            for figure in (n for n in tree.nodes() if n.tag == 'figure'):
                children = [n for n in figure.children if hasattr(n, 'tag')]
                key = next(i for i, n in enumerate(children) if 'Dot:' in visible(n) or 'Line:' in visible(n) or '点：' in visible(n) or '区间线：' in visible(n))
                first_plot = next(i for i, n in enumerate(children) if n.has_class('adoption-panels') or n.has_class('noise-row'))
                self.assertLess(key, first_plot)
                self.assertFalse(any(n.tag == 'figcaption' for n in children))

    def test_nonuniform_point_plots_have_screen_constant_paths_and_full_whiskers(self):
        for lang, html in self.html.items():
            for fragment in re.findall(r'<svg\b[^>]*class="nx-plot".*?</svg>', html, re.S):
                svg = ET.fromstring(fragment.replace(' data-nx-reveal ', ' '))
                self.assertIn('nx-plot', svg.get('class', '').split())
                self.assertEqual(svg.get('preserveAspectRatio'), 'none')
                self.assertEqual(len(svg.findall('.//ellipse')), 0)
                points = [n for n in svg.iter('path') if n.get('class') == 'mark']
                self.assertEqual(len(points), 1)
                self.assertRegex(points[0].get('d'), r'^M[-\d.]+ 18h\.01$')
                holes = [n for n in svg.iter('path') if n.get('class') == 'mark-hole']
                self.assertLessEqual(len(holes), 1)
                if holes:
                    self.assertEqual(holes[0].get('d'), points[0].get('d'))
                marks = next(n for n in svg.iter('g') if 'plot-marks' in n.get('class', '').split())
                if 'within' in marks.get('class', '').split():
                    self.assertEqual(len(holes), 1)
                if 'crossing' in marks.get('class', '').split():
                    self.assertEqual(len(holes), 0)
                whiskers = [n for n in svg.iter('path') if n.get('class') == 'whisker']
                if 'mw-' not in fragment:
                    self.assertEqual(len(whiskers), 1)
                    self.assertEqual(whiskers[0].get('d').count('V24'), 2)

    def test_method_retains_scaling_scope_without_obsolete_roster(self):
        for lang in ('en', 'zh'):
            method = visible(Tree(synth.method_block(lang)).root)
            for count in ('35', '597', '95%'):
                self.assertIn(count, method)
            for obsolete in ('18 designs', '23 outcomes', '18 个设计', '23 个结局'):
                self.assertNotIn(obsolete, method)
            for design in ('DiD', 'RDD', 'RCT'):
                self.assertIn(design, method)
            self.assertIn('terminal states' if lang == 'en' else '终态', method)
            self.assertIn('independent adoption panels' if lang == 'en' else '独立采纳面板', method)
            self.assertIn('Pipeline designs' if lang == 'en' else '流水线设计', method)
            self.assertIn('completed frozen pooled study' if lang == 'en' else '已完成的冻结合并研究', method)
            for name in ('Webb', 'Conley–Taber', '93%', '12', '11'):
                self.assertIn(name, method)
            self.assertNotIn('unit Gaussian-multiplier intervals', method)


class AggregateSynthesizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta, cls.counts = synth.load_aggregate_inputs()
        cls.html = {lang: synth.page_v3(lang, cls.meta, cls.counts) for lang in ('en', 'zh')}
        cls.trees = {lang: Tree(html).root for lang, html in cls.html.items()}

    def test_adoption_finding_leads_and_aggregates_remain(self):
        for lang, tree in self.trees.items():
            figures = [n for n in tree.nodes() if n.tag == 'figure']
            self.assertEqual([n.attrs['id'] for n in figures], ['adoption-test', 'q1', 'q3', 'q5'])
            self.assertEqual(len([n for n in tree.nodes() if n.has_class('aggregate-chart')]), 3)
            text = visible(tree)
            self.assertEqual(text.count('等待预注册分析' if lang == 'zh' else 'Pending registered analysis'), 0)
            for forbidden in ('Medicaid', 'data-case='):
                self.assertNotIn(forbidden, self.html[lang])
            self.assertIn('/viz/' + lang + '/worked_examples.html', self.html[lang])
            self.assertIn('2026-10-10', text)
            self.assertIn('2026-10-11', text)
            self.assertIn('开发样本' if lang == 'zh' else 'Development sample', text)
            hero = next(n for n in tree.nodes() if n.has_class('synth-hero'))
            self.assertIn('区间均包含零' if lang == 'zh' else 'intervals that include zero', visible(hero))
            self.assertNotIn('仅含基线的模型得分最高' if lang == 'zh' else 'baseline-only model scored best', text)
        body = next(n for n in self.trees['en'].nodes() if n.tag == 'main')
        self.assertNotRegex(visible(body), r'[\u3400-\u9fff]')

    def test_chart_values_keep_the_correct_denominators(self):
        for lang, tree in self.trees.items():
            rows = {n.attrs['data-series']: n for n in tree.nodes() if 'data-series' in n.attrs}
            for key, amount, denominator in [('all-forecast', 26, 35), ('all-baseline', 24, 35),
                                             ('pipeline-forecast', 19, 27), ('pipeline-baseline', 21, 27),
                                             ('estimated', 48, 125),
                                             ('not_evaluable', 77, 125)]:
                self.assertEqual(float(rows[key].attrs['data-value']), amount)
                self.assertEqual(float(rows[key].attrs['data-denominator']), denominator)
            self.assertAlmostEqual(float(rows['exceedance'].attrs['data-value']), 5/45)
            self.assertEqual(float(rows['reference'].attrs['data-value']), .05)
            self.assertNotIn('archive-baseline', rows)
            self.assertEqual(sum(int(rows[key].attrs['data-value']) for key in synth.GATE_LABELS), 77)
            text = visible(tree)
            for value in ('5/45', '11.1%', '5%', '26/35', '24/35', '19/27', '21/27', '8.6%', '31.0%', '104', '27'):
                self.assertIn(value, text)
            self.assertNotIn('131 designs processed', text)
            self.assertNotIn('INSUFFICIENT_WORK', text)

    def test_counts_and_forecast_values_are_read_from_inputs(self):
        counts = deepcopy(self.counts); counts['processed'] = 126; counts['estimated'] = 49
        meta = deepcopy(self.meta)
        q = meta['questions']['Q3']['locked_median_and_interval_scores']['all']
        q['median_direction_hits'] = 25
        text = visible(Tree(synth.page_v3('en', meta, counts)).root)
        self.assertIn('126 designs: all attempts', text)
        self.assertIn('49', text)
        self.assertIn('25/35', text)
        self.assertNotIn('26/35', text)
        self.assertIn('all have intervals that include zero', text)

    def test_restored_adoption_panel_preserves_frozen_intervals(self):
        reference = self.meta['pooled_adoption_reference']
        for lang, tree in self.trees.items():
            panel = next(n for n in tree.nodes() if n.attrs.get('id') == 'adoption-test')
            text = visible(panel)
            for row in reference['historical_H1'].values():
                for key in ('estimate', 'lower', 'upper'):
                    self.assertIn(synth.value(row[key], 6), text)
            self.assertIn('597', text)
            self.assertIn('DellaVigna', text)
            self.assertIn('二者之差的区间均包含零' if lang == 'zh' else 'all have intervals that include zero', visible(tree))
            self.assertNotIn('All three intervals include zero', text)

    def test_development_definition_reference_and_visual_restraint(self):
        for lang, tree in self.trees.items():
            text = visible(tree)
            self.assertIn('含诊断后停止设计' if lang == 'zh' else 'including diagnostic stops', text)
            self.assertIn('按 95% 截点构造的 5%' if lang == 'zh' else '5% by construction of the 95% cutoff', text)
            self.assertIn('在符合条件的州之间随机重排采纳日期' if lang == 'zh' else 'randomly reassigning adoption dates across eligible states', text)
            adoption = next(n for n in tree.nodes() if n.attrs.get('id') == 'adoption-test')
            for svg in (n for n in adoption.nodes() if n.tag == 'svg'):
                self.assertFalse(any(n.has_class('grid-line') for n in svg.nodes()))
                whisker = next(n for n in svg.nodes() if n.has_class('whisker'))
                self.assertNotIn('V24', whisker.attrs['d'])
                marks = next(n for n in svg.nodes() if n.has_class('plot-marks'))
                self.assertTrue(marks.has_class('within'))
            for figure in (n for n in tree.nodes() if n.attrs.get('id') in ('q1', 'q3')):
                for svg in (n for n in figure.nodes() if n.tag == 'svg'):
                    self.assertEqual(svg.attrs['preserveaspectratio'], 'none')
            self.assertNotIn('prefers-color-scheme:dark', self.html[lang])
            self.assertNotIn('<p class="synth-kicker">', self.html[lang])
            self.assertNotIn('joint calibration is pending', text)

    def test_no_transient_input_dependencies(self):
        source = Path(synth.__file__).read_text()
        self.assertNotIn('/private/tmp', source)
        self.assertNotIn('runfrozen_report.md', source)
        self.assertNotIn('load_adoption', source)
        self.assertNotIn('load_pooled', source)
        self.assertTrue(synth.META.is_relative_to(ROOT))
        self.assertTrue(synth.COUNTS.is_relative_to(ROOT))


class MotionViewportTests(unittest.TestCase):
    def run_motion(self, checks):
        self.assertIsNotNone(shutil.which('node'), 'Node is required for motion verification')
        runtime = (ROOT / 'scripts/viz_site/partials/motion.html').read_text()
        script = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', runtime, re.S))
        result = subprocess.run(['node', '-e', motion_harness() + checks], input=json.dumps(script),
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_figure_two_phone_plots_finish_at_full_opacity(self):
        noise_data = json.loads((ROOT / 'docs/plans/l3-results/NOISE_FIGURE_DATA.json').read_text())
        for lang, width, viewport, figure_height in [('en', 375, 667, 2055), ('zh', 393, 659, 1891)]:
            tree = Tree(synth.page(lang, adoption_fixture(), noise_data)).root
            intros = [n for n in tree.nodes() if 'data-nx-intro' in n.attrs]
            targets = [[n for n in intro.nodes() if 'data-nx-reveal' in n.attrs] for intro in intros]
            self.assertEqual([n.attrs['id'] for n in intros], ['adoption-test', 'noise-floor'])
            self.assertEqual(len(targets[1]), 12)
            page_hook = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', synth.MOTION, re.S))
            with self.subTest(lang=lang, viewport=(width, viewport)):
                self.run_motion(r'''
const h=setup(),figure=h.element(),noise=h.element(),plots=revealTargets(COUNT),noisePlots=revealTargets(12);
phone(h,VIEWPORT,WIDTH);rect(figure,VIEWPORT+20,800,WIDTH);rect(noise,VIEWPORT+900,HEIGHT,WIDTH);
figure.id='adoption-test';noise.id='noise-floor';
noisePlots.targets.forEach(target=>target.style.removeProperty('opacity'));
figure.querySelectorAll=()=>plots.targets;noise.querySelectorAll=()=>noisePlots.targets;
const document={querySelectorAll:()=>[figure,noise]};
vm.runInNewContext(PAGE_HOOK,{window:h.window,document});
assert(noisePlots.targets.every(target=>finalOpacity(target)==='0'));
while([...h.timers.values()].some(t=>t.ms===0))h.fire(0);flushAll(h);
rect(noise,0,HEIGHT,WIDTH);h.enter(noise,VIEWPORT/HEIGHT,true);flushAll(h);
assert.equal(noisePlots.animations.length,12,'Every stored outcome plot starts on a short phone.');
h.fire(1200);assert(noisePlots.targets.every(target=>finalOpacity(target)==='1'));
h.api.finishAll();assert.equal(h.timers.size,0);assert.equal(h.frames.size,0);
'''.replace('COUNT', str(len(targets[0]))).replace('VIEWPORT', str(viewport))
                                .replace('WIDTH', str(width)).replace('HEIGHT', str(figure_height))
                                .replace('PAGE_HOOK', json.dumps(page_hook)))

    def test_every_oversized_reveal_has_a_reachable_phone_threshold(self):
        self.run_motion(r'''
// .35 was unreachable for 2,055 px EN and 1,891 px ZH Figure 2.
// Exercise default and explicit consumer thresholds, including future tall rows.
for(const viewport of [667,659])for(const height of [668,800,1891,2055,4000])for(const threshold of [undefined,.35,.8,1]){
 const h=setup(),el=h.element();phone(h,viewport);rect(el,viewport+20,height);
 let runs=0;const handle=h.api.once(el,()=>runs++,{threshold,finish(){}});
 flushAll(h);assert.equal(runs,0,'An offscreen target waits.');
 rect(el,0,height);const maximum=viewport/height;
 h.enter(el,maximum,true);flushAll(h);
 assert.equal(runs,1,`Unreachable threshold: viewport=${viewport}, target=${height}, requested=${threshold}`);
 assert.equal(handle.state,'running');h.api.finishAll();
}
''')

    def test_first_viewport_reveals_on_load_without_observer_delivery(self):
        self.run_motion(r'''
for(const height of [667,659])for(const loading of [false,true]){
 const h=setup({loading}),level=h.element(),r=revealTargets(1);phone(h,height);
 rect(level,height-25,100);
 const handle=h.api.revealOnce(level,r.targets);
 assert.equal(finalOpacity(r.targets[0]),'0','Preparation occurs before paint.');
 if(loading)h.events['document:DOMContentLoaded']();else h.fire(0);flushAll(h);
 assert.equal(r.animations.length,1,'A partly visible target starts on load.');
 assert.equal(handle.state,'running');h.fire(1200);
 assert.equal(finalOpacity(r.targets[0]),'.7');assert.equal(handle.state,'finished');
}
''')

    def test_low_ratio_intersection_commits_pending_reveal_after_600ms(self):
        self.run_motion(r'''
for(const height of [667,659]){
 const h=setup(),el=h.element(),r=revealTargets(12);phone(h,height);rect(el,height+10,2055);
 const handle=h.api.revealOnce(el,r.targets);flushAll(h);
 rect(el,height-1,2055);h.enter(el,1/2055,true);flushAll(h);
 assert.equal(handle.state,'pending');assert.equal(r.animations.length,0);
 h.advance(600);h.fire(600);
 assert.equal(handle.state,'finished','Continuous intersection cannot hide plots indefinitely.');
 r.checkOriginal();assert.equal(h.timers.size,0);assert.equal(h.frames.size,0);
}
''')

    def test_leaving_viewport_cancels_continuous_intersection_fallback(self):
        self.run_motion(r'''
const h=setup(),el=h.element(),r=revealTargets(12);phone(h,667);rect(el,700,2055);
const handle=h.api.revealOnce(el,r.targets);flushAll(h);
rect(el,666,2055);h.enter(el,1/2055,true);flushAll(h);
assert([...h.timers.values()].some(t=>t.ms===600));
rect(el,700,2055);h.enter(el,0,true);flushAll(h);
assert(![...h.timers.values()].some(t=>t.ms===600),'Only continuous intersection counts.');
assert.equal(handle.state,'pending');h.api.finishAll();r.checkOriginal();
''')

    def test_scroll_end_and_debounce_commit_intersecting_pending_and_queued_reveals(self):
        self.run_motion(r'''
for(const trigger of ['scrollend','scroll']){
 const h=setup(),active=h.element(),pending=h.element(),queued=h.element();phone(h,667);
 rect(active,700,80);rect(pending,700,2055);rect(queued,700,2055);
 const p=revealTargets(12),q=revealTargets(12);
 h.api.once(active,()=>{},{finish(){}});
 const pendingHandle=h.api.revealOnce(pending,p.targets),queuedHandle=h.api.revealOnce(queued,q.targets);flushAll(h);
 rect(active,0,80);h.enter(active,1,true);flushAll(h);
 rect(pending,666,2055);h.enter(pending,1/2055,true);
 rect(queued,0,2055);h.enter(queued,667/2055,true);flushAll(h);
 assert.equal(pendingHandle.state,'pending');assert.equal(queuedHandle.state,'queued');
 assert.equal(typeof h.events[trigger],'function');h.events[trigger]();
 if(trigger==='scroll'){
  const timer=[...h.timers.values()].find(t=>t.ms>0&&t.ms!==120&&t.ms!==600&&t.ms!==5000);
  assert(timer,'Scroll must schedule a bounded end-of-scroll fallback.');h.advance(timer.ms);h.fire(timer.ms);
 }
 assert.equal(pendingHandle.state,'finished');assert.equal(queuedHandle.state,'finished');
 p.checkOriginal();q.checkOriginal();h.api.finishAll();assert.equal(h.timers.size,0);
}
''')



if __name__ == '__main__':
    unittest.main()
