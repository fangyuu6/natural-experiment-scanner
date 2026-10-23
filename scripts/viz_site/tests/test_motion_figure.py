"""The Home motion figure: render(lang), its data, its hooks and its placement switch."""
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.viz_site import make_motion_data
from scripts.viz_site import motion_figure as figure
figure.MOTION_VERSION = 'v51'     # these tests cover the V5.1 figure; test_motion_v14.py covers the v14 figure (the Home default)
from scripts.viz_site import restructure_estimates as estimates
from scripts.viz_site.public_page import PublicText, banned_terms

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
PIN_FILE = HERE / 'data/snapshot_pin.json'
FALLBACK_PIN = 'data/replication/snapshot_v5/snapshot_20261018T085028678040Z.json'
CJK = re.compile('[㐀-䶿一-鿿豈-﫿　-〿＀-￯]')


def setUpModule():
    # The figure reads the same pinned snapshot as the Estimates page. A snapshot another test already loaded stays.
    if estimates.load_snapshot.cache_info().currsize == 0:
        pin = json.loads(PIN_FILE.read_text())['snapshot_path'] if PIN_FILE.is_file() else FALLBACK_PIN
        estimates.pin_snapshot(pin)


def visible_text(markup):
    return ' '.join(' '.join(PublicText(markup).parts).split())


class SegmentParser(HTMLParser):
    """The text of each <span class="mf-seg"> (nested tags included), in order."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.depth, self.parts, self.found = 0, [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == 'span':
            if self.depth or dict(attrs).get('class') == 'mf-seg':
                self.depth += 1

    def handle_endtag(self, tag):
        if tag == 'span' and self.depth:
            self.depth -= 1
            if not self.depth:
                self.found.append(' '.join(''.join(self.parts).split()))
                self.parts = []

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def segments_of(markup):
    return SegmentParser(markup).found


def squash(text):
    return re.sub(r'\s+', '', text)


def strip_text(markup):
    strip = re.search(r'<ol class="mf-strip">(.*?)</ol>', markup, re.S)[1]
    return ' '.join(re.sub(r'<[^>]+>', ' ', strip).split())


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = {lang: figure.render(lang) for lang in ('en', 'zh')}
        cls.findings = json.loads(figure.FINDINGS_FILE.read_text())
        cls.data = json.loads(figure.DATA_FILE.read_text())
        cls.snapshot = estimates.load_snapshot()
        cls.public = estimates.public_counts(cls.snapshot)

    def test_findings_are_the_real_ones_with_their_short_labels(self):
        rows = sorted(self.findings['findings'], key=lambda row: row['order'])
        self.assertGreaterEqual(len(rows), figure.MIN_FINDINGS)
        for lang in ('en', 'zh'):
            items = re.findall(r'<li>(.*?)</li>', re.search(r'<div class="mf-find".*?</ol>', self.html[lang], re.S)[0])
            self.assertEqual([unescape(re.sub(r'<[^>]+>', '', item)) for item in items], [row['short'][lang] for row in rows])
            for row in rows:
                self.assertEqual(row['status'], 'exploratory')
        # The schema the figure relies on: at most five words in English, at most eight characters in Chinese (F6: ten for finding 6, "基期高，估计超出随机").
        for row in rows:
            self.assertLessEqual(len(row['short']['en'].split()), 5, row['id'])
            self.assertLessEqual(len(row['short']['zh']), 10 if row['id'] == 'X6' else 8, row['id'])

    def test_the_exploratory_label_appears_once(self):
        # Home prints label_home (no ranges on Home); a file without it gives Home the shared label.
        for lang in ('en', 'zh'):
            label = self.findings.get('label_home', self.findings['label'])[lang]
            self.assertEqual(visible_text(self.html[lang]).count(label), 1)
            self.assertEqual(self.html[lang].count(label), 1)
            if label != self.findings['label'][lang]:
                self.assertNotIn(self.findings['label'][lang], self.html[lang])

    def test_label_home_wins_and_the_shared_label_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            for with_home in (True, False):
                doc = json.loads(json.dumps(self.findings))
                doc.pop('label_home', None)
                doc['label'] = {'en': 'All findings here are exploratory; ranges are 95% intervals.', 'zh': '此处所有发现均为探索性；区间均为 95% 区间。'}   # T211: the shipped Explore label is now the short one, so the test names a distinct one
                if with_home:
                    doc['label_home'] = {'en': 'All findings here are exploratory.', 'zh': '此处所有发现均为探索性。'}
                path = Path(folder) / ('home.json' if with_home else 'plain.json')
                path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
                with patch.object(figure, 'FINDINGS_FILE', path):
                    for lang in ('en', 'zh'):
                        markup = figure.render(lang)
                        shown = doc['label_home'][lang] if with_home else doc['label'][lang]
                        other = doc['label'][lang]
                        self.assertEqual(markup.count(shown), 1)
                        if with_home:
                            self.assertNotIn(other, markup)           # Explore's label speaks of ranges; Home prints none

    def staircase(self, lang, key, **numbers):
        """The Home staircase's own line (home_story.{lang}.json) with these numbers, as plain text."""
        raw = json.loads((HERE / f'i18n/home_story.{lang}.json').read_text(encoding='utf-8'))[f'story.{key}.line_html']
        return ' '.join(unescape(re.sub(r'<[^>]+>', '', raw.format(**numbers))).split())

    def numbered(self, lang, given=None):
        with patch.object(figure, 'STRIP_NUMBERS', True):
            return figure.render(lang, given)

    def cell(self, lang, level, markup=None):
        body = re.search(r'<li class="mf-cell" data-lv="%d">(.*?)</li>' % level, markup or self.html[lang], re.S)[1]
        return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', body)).split())

    def test_the_strip_names_the_levels_and_the_numbers_stay_in_the_staircase(self):
        # Hub decision (art gate): STRIP_NUMBERS is False; nothing is said twice, and no DOM text reads "0 papers".
        self.assertIs(figure.STRIP_NUMBERS, False)
        for lang, names in (('en', ['Screening', 'Estimates', 'Explore']), ('zh', ['筛查', '估计', '探索'])):
            for level, name in enumerate(names, 1):
                self.assertEqual(self.cell(lang, level), name)
            body = re.search(r'<ol class="mf-strip">(.*?)</ol>', self.html[lang], re.S)[1]
            self.assertNotIn('mf-line', body)
            self.assertNotIn('mf-count', self.html[lang].split('<script')[0].split('</style>')[1])
            self.assertNotRegex(strip_text(self.html[lang]), r'\d')
            # The text alternative keeps the final numbers.
            label = unescape(re.search(r'<canvas [^>]*aria-label="([^"]*)"', self.html[lang])[1])
            self.assertIn(f"{self.data['counts']['texts']:,}", label)
            self.assertIn(f"{self.data['counts']['cands']:,}", label)

    def test_the_strip_carries_the_numbers_of_the_loaders(self):
        funnel = ROOT / 'deploy_output/funnel_data.json'
        papers, matched = estimates.attempted(self.snapshot), self.public['number_matches']
        texts, cands = self.data['counts']['texts'], self.data['counts']['cands']
        if funnel.is_file():          # the number Screening prints
            shown = json.loads(funnel.read_text())
            self.assertEqual((texts, cands), (shown['total'], shown['stages'][-1]['n_out']))
        for lang, label in (('en', 'Screening'), ('zh', '筛查')):
            # Each number stands next to its own unit, in the staircase's words; no bare count of a result group.
            numbers = self.numbered(lang)
            self.assertEqual(squash(self.cell(lang, 1, numbers)), squash(label + ' ' + self.staircase(lang, 'screen', texts=f'{texts:,}', candidates=f'{cands:,}')))
            second = self.cell(lang, 2, numbers)
            self.assertTrue(squash(second).endswith(squash(self.staircase(lang, 'estimate', papers=papers, matched=matched))), second)
            allowed = {str(papers), str(matched), '10'}
            self.assertEqual([n for n in re.findall(r'\d[\d,.]*\d|\d', second) if n not in allowed], [])      # no bare 8 or 9
        for lang, unit in (('en', 'findings'), ('zh', '项发现')):
            third = self.cell(lang, 3, self.numbered(lang)).split(' ', 1)[1]
            self.assertEqual(third, f"{len(self.findings['findings'])} {unit}")
        self.assertNotIn('mf-glyphs', self.html['en'])
        # Home passes the staircase figures; they win over the stored ones.
        passed = self.numbered('en', {'texts': 1234567, 'candidates': 8901, 'papers': 77, 'matched': 55})
        self.assertEqual(squash(self.cell('en', 1, passed)), squash('Screening ' + self.staircase('en', 'screen', texts='1,234,567', candidates='8,901')))
        self.assertTrue(squash(self.cell('en', 2, passed)).endswith(squash('77 papers re-estimated, 55 within 10%')))

    def test_the_three_levels_carry_the_names_of_the_site_navigation(self):
        # Round two (hub item 2): the strip says Screening / Estimates / Explore, as the navigation and the staircase do; nothing says Patterns.
        for lang in ('en', 'zh'):
            words = json.loads((HERE / 'i18n' / f'common.{lang}.json').read_text())
            names = tuple(words['nav.' + key] for key in ('screening', 'worked_examples', 'synthesize'))
            self.assertEqual(names, {'en': ('Screening', 'Estimates', 'Explore'), 'zh': ('筛查', '估计', '探索')}[lang])
            self.assertEqual(figure.STRINGS[lang]['levels'], names)
            markup = self.html[lang]
            self.assertEqual(re.findall(r'<p class="mf-lab">([^<]*)</p>', markup), list(names))
            cfg = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', markup, re.S)[1])
            printed = [visible_text(markup), markup.split('<script')[0]] + [' '.join(v) if isinstance(v, list) else str(v) for v in cfg['s'].values()]
            for text in printed:
                self.assertNotRegex(text, r'(?i)patterns?|跨政策规律')
            label = re.search(r'<canvas [^>]*aria-label="([^"]*)"', markup)[1]
            self.assertEqual(unescape(label).count(names[2] + ('：' if lang == 'zh' else ':')), 1)
            self.assertIn(names[2], re.search(r'<section class="mf-sec" aria-label="([^"]*)"', markup)[1])

    def test_the_canvas_text_alternative_names_each_number_and_the_captions_are_exact(self):
        papers = estimates.attempted(self.snapshot)
        names = estimates.category_labels(self.snapshot, 'en')
        zh = estimates.category_labels(self.snapshot, 'zh')
        keys = ('number_matches', 'same_direction', 'not_comparable', 'opposite_sign')
        label = {lang: unescape(re.search(r'aria-label="([^"]*)"', re.search(r'<canvas [^>]*>', self.html[lang])[0])[1]) for lang in ('en', 'zh')}
        # Every count of a result group is followed by its unit (papers / 篇) and its group name: no bare numbers.
        self.assertIn('By result, ' + ', '.join(f'{names[k]}: {self.public[k]} ' + ('paper' if self.public[k] == 1 else 'papers') for k in keys) + '.', label['en'])
        self.assertIn('，'.join(f'{self.public[k]} 篇{zh[k]}' for k in keys) + '。', label['zh'])
        self.assertTrue(label['zh'].startswith('分三幕的动画。'))
        self.assertIn('unique policy texts are screened to', label['en'])
        self.assertIn(f"{self.data['counts']['texts']:,} 份不重复政策文本筛出 {self.data['counts']['cands']:,} 条候选记录", label['zh'])
        for text in label.values():                                    # the nurse compact is not one of the screened candidates
            self.assertNotRegex(text, r'one candidate becomes|一个候选成为')
        self.assertIn('the nurse licensure compact', label['en'])
        self.assertIn('护士执照协定', label['zh'])
        # Captions: the wall caption does not claim the whole universe; the clock caption is a full phrase; the key says what grey means.
        for lang in ('en', 'zh'):
            cfg = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', self.html[lang], re.S)[1])['s']
            for wording in cfg['wallCap']:
                self.assertNotRegex(wording, r'\b(each|every|all)\b|每项|所有|全部')
        en = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', self.html['en'], re.S)[1])['s']
        zhs = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', self.html['zh'], re.S)[1])['s']
        self.assertEqual(en['wallCap'], ['State policy adoption maps', 'Policy adoption maps'])
        self.assertEqual(zhs['wallCap'], ['州政策采纳地图', '政策采纳地图'])
        self.assertEqual(en['clockCap'][0], 'Descriptive: median number of states with the policy 5 years after the first state')
        self.assertEqual(zhs['clockCap'][0], '描述性：首个州之后第 5 年已采纳的州数（中位数）')
        for wording in en['clockCap'] + zhs['clockCap']:
            self.assertNotIn('within 5 years of the first, median', wording)
        self.assertEqual((en['none'], zhs['none']), ('none recorded', '无记录'))
        self.assertEqual((zhs['key'], zhs['unobs'], zhs['bands'][4], figure.STRINGS['zh']['estLab']), ('晚于首个州的年数', '观察期不足 5 年', '2000 年起', '估计值与置信区间'))

    def test_each_number_stays_with_its_unit_in_a_no_wrap_segment(self):
        # Panel round 2, ZH editor S1: the strip breaks only between segments, never inside "7,781 条候选记录" or "36 within 10%".
        expected = {
            'en': {1: ['483,414 unique policy texts', '7,781 candidates'], 2: ['53 papers re-estimated', '36 within 10%']},
            'zh': {1: ['483,414 份不重复政策文本', '7,781 条候选记录'], 2: ['已重新估计 53 篇论文', '36 篇差距不超过 10%']}}
        for lang, levels in expected.items():
            for level, segments in levels.items():
                body = re.search(r'<li class="mf-cell" data-lv="%d">(.*?)</li>' % level, self.numbered(lang), re.S)[1]
                self.assertEqual(segments_of(body), segments, (lang, level))
        css = (figure.ASSETS / 'motion_figure.css').read_text()
        self.assertRegex(css, r'\.mf-seg,\.mf-nb\{white-space:nowrap\}')

    def test_act_two_draws_the_home_cards_own_bands_labels_and_colours(self):
        # Art gate M1: the nurse map has the card's three year bands, its labels and its three colours, read from the card itself.
        from scripts.viz_site import public_page
        card = public_page.simplify_adoption((HERE / 'partials/story_map.html').read_text(encoding='utf-8') + '</head>')
        bands = json.loads(re.search(r'<script type="application/json" id="sm-d">(.*?)</script>', card, re.S)[1])['p']['nurse_licensure_compact']['b']
        labels = re.findall(r'<i>([^<]*)</i>', re.search(r'<span class="sm-rk" aria-hidden="true">(.*?)</span>', card, re.S)[1])
        nurse = self.data['nurse']
        self.assertEqual(nurse['bins'], [[2016, 2019], [2020, 2023], [2024, 2024]])
        self.assertEqual(nurse['bins'], bands)
        self.assertEqual(nurse['binLabels'], labels)
        self.assertEqual(nurse['binLabels'], ['2016–19', '2020–23', '2024'])
        tokens = (HERE / 'design/design_tokens.css').read_text(encoding='utf-8')
        site = {name: re.search(r'--nx-%s:(#[0-9a-fA-F]{6})' % name, tokens)[1].lower() for name in ('b1', 'b3', 'b5')}
        self.assertEqual(nurse['binColors'], [site['b1'], site['b3'], site['b5']])          # the card's .r1 .r2 .r3
        self.assertEqual(nurse['binColors'], [c.lower() for c in re.findall(r'\.sm \.r\d\{background:(#[0-9a-fA-F]{6})\}', card)])
        # every state of the compact falls in a band
        years = {year for _, year in nurse['a']}
        self.assertTrue(all(any(lo <= y <= hi for lo, hi in bands) for y in years))

    def test_owner_taste_switches_and_the_timeline(self):
        self.assertEqual((figure.STRIP_NUMBERS, figure.ESTIMATE_LANE, figure.TALL_BELOW, figure.AUTOPLAY_VISIBLE, figure.POSTER_SECONDS),
                         (False, False, 900, 0.6, 1.5))
        for lang in ('en', 'zh'):
            cfg = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', self.html[lang], re.S)[1])
            self.assertEqual((cfg['lane'], cfg['tallBelow'], cfg['autoplay'], cfg['poster']), (False, 900, 0.6, 1.5))
            self.assertNotIn('estLab', cfg['s'])                      # no label for a lane that is not drawn
            self.assertNotRegex(self.html[lang], r'Estimate and interval|估计值与置信区间')
        with patch.object(figure, 'ESTIMATE_LANE', True):
            en = figure.render('en')
        cfg = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', en, re.S)[1])
        self.assertTrue(cfg['lane'])
        self.assertEqual(cfg['s']['estLab'], 'Estimate and interval')
        js = (figure.ASSETS / 'motion_figure.js').read_text()
        self.assertIn('TALL_BELOW', js)
        self.assertIn('AUTOPLAY', js)
        self.assertIn('T.still', js)                                  # the still before play
        self.assertIn('function seq(', js)                            # fade out, then fade in: never two texts on one line

    def test_the_wall_and_ticker_say_jurisdictions_as_the_home_card_does(self):
        words = {lang: json.loads((HERE / 'i18n' / f'story_map.{lang}.json').read_text(encoding='utf-8'))['map.stats'] for lang in ('en', 'zh')}
        self.assertIn('jurisdictions', words['en'])
        self.assertEqual(figure.STRINGS['en']['ofStates'], '{a} of {n} jurisdictions')
        self.assertIn('个辖区', words['zh'])
        self.assertEqual(figure.STRINGS['zh']['ofStates'], '{a} / {n} 个辖区')

    def test_a_finding_label_never_breaks_inside_a_hyphenated_word_and_the_list_is_one_column_when_narrow(self):
        # Gate B3, MF1: "re-estimates" is one no-wrap piece (the text is unchanged); the list is one column below 480 px of figure width.
        self.assertEqual(figure.label_html('Most re-estimates match'), '<span class="mf-t">Most <span class="mf-nb">re-estimates</span> match</span>')     # one flex item
        self.assertEqual(figure.label_html("Adopters' gap"), 'Adopters&#x27; gap')
        self.assertEqual(figure.label_html('多数重估相符'), '多数重估相符')
        css = (figure.ASSETS / 'motion_figure.css').read_text()
        self.assertRegex(css, r'\.mf-find li\{[^}]*hyphens:manual')
        self.assertIn('.mf-js .mf-find.mf-one li{', css)
        js = (figure.ASSETS / 'motion_figure.js').read_text()
        self.assertIn("toggle('mf-one', L.portrait && W < 480)", js)
        self.assertIn("toggle('mf-two', L.portrait && W >= 480)", js)
        for lang in ('en', 'zh'):
            for item in re.findall(r'<li>(.*?)</li>', re.search(r'<div class="mf-find".*?</ol>', self.html[lang], re.S)[0]):
                for word in re.findall(r'[A-Za-z]+(?:-[A-Za-z]+)+', re.sub(r'<[^>]+>', '', item)):
                    self.assertIn('<span class="mf-nb">' + word + '</span>', item)

    def test_no_public_count_of_maps_or_policies(self):
        # The Explore page names the database's own policy count; the wall shows many maps but prints no count of them.
        tiles = self.data['tiles']
        for lang, markup in self.html.items():
            cfg = json.loads(re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', markup, re.S)[1])
            printed = [visible_text(markup)] + [' '.join(value) if isinstance(value, list) else str(value) for key, value in cfg['s'].items() if key != 'dot']
            for text in printed:
                for number in (tiles['n'], tiles['spid'], 648):
                    self.assertNotRegex(text, r'(?<![\d,.])%d(?![\d,])' % number, text[:80])
                self.assertNotRegex(text, r'\d+ (state )?polic(ies|y)\b(?! texts)' if lang == 'en' else r'\d+ 项(州)?政策')
            self.assertNotRegex(self.html[lang].split('<script')[0], r'(?<![\d,.])%d(?![\d,])' % tiles['n'])

    def test_the_wall_has_one_map_per_policy(self):
        tiles = self.data['tiles']
        self.assertEqual(tiles['n'], tiles['spid'] + tiles['verified'])
        self.assertEqual(sum(tiles['bands']), tiles['n'])
        self.assertEqual((tiles['spid'], tiles['verified']), (724, 8))
        self.assertEqual(len(tiles['packed']), tiles['n'])
        self.assertEqual(len(tiles['src']), tiles['n'])

    def test_nothing_external(self):
        for lang, markup in self.html.items():
            self.assertNotRegex(markup, r'https?://|//[a-z0-9.-]+\.[a-z]{2,}/|@import|url\(')
            self.assertNotRegex(markup, r'<(link|iframe|img|video|audio|embed|object)\b')
            self.assertNotRegex(markup, r'<script[^>]+src=')
            self.assertNotRegex(markup, r'fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|import\(')
            self.assertNotIn('xmlns', markup)

    def test_language_is_chosen_by_the_page(self):
        self.assertIsNone(CJK.search(self.html['en']))
        self.assertIsNotNone(CJK.search(self.html['zh']))
        self.assertNotIn('data-lang', self.html['en'])          # the Chinese copy is generated and compared attribute by attribute
        for lang in ('en', 'zh'):
            self.assertNotIn('EN</button>', self.html[lang])     # no in-figure language buttons
            self.assertEqual(banned_terms(self.html[lang]), [])

    def test_light_only_and_site_tokens(self):
        css = (figure.ASSETS / 'motion_figure.css').read_text()
        js = (figure.ASSETS / 'motion_figure.js').read_text()
        self.assertNotIn('prefers-color-scheme', css + js)
        self.assertNotRegex(css, r'#0e1320|#1a2336|data-theme')
        for token in ('--nx-ink', '--nx-line', '--nx-accent', '--nx-plate', '--nx-sans'):
            self.assertIn(token, css)
        for token in ('--nx-b1', '--nx-b5', '--nx-land', '--nx-slate'):
            self.assertIn(token, js)

    def test_controls_and_hooks(self):
        js = (figure.ASSETS / 'motion_figure.js').read_text()
        css = (figure.ASSETS / 'motion_figure.css').read_text()
        for lang, markup in self.html.items():
            self.assertEqual(len(re.findall(r'<button type="button" class="mf-btn"', markup)), 2)
            self.assertIn('data-act="replay"', markup)
            self.assertIn('data-act="toggle" aria-pressed="false"', markup)
        self.assertIn('prefers-reduced-motion: reduce', js)           # static final frame
        self.assertIn('prefers-reduced-motion:reduce', css)
        self.assertIn('visibilitychange', js)                         # pause when the page is hidden
        self.assertIn('document.hidden', js)
        self.assertIn('IntersectionObserver', js)                     # start once, pause when out of view
        self.assertIn('0.4', js)
        self.assertIn('aria-pressed', js)
        self.assertIn('requestAnimationFrame', js)
        self.assertRegex(css, r'\.mf-btn\{[^}]*width:44px;height:44px')
        self.assertIn('.mf-btn:focus-visible', css)
        self.assertIn('devicePixelRatio', js)                         # density-aware canvas
        self.assertIn('ResizeObserver', js)
        self.assertRegex(css, r'\.mf:not\(\.mf-js\)')                 # the list and the strip without script
        self.assertNotIn('NXMotion', js)

    def test_text_alternative_and_dom_findings(self):
        for lang, markup in self.html.items():
            canvas = re.search(r'<canvas [^>]*>', markup)[0]
            self.assertIn('role="img"', canvas)
            label = re.search(r'aria-label="([^"]*)"', canvas)[1]
            for number in (f"{self.data['counts']['texts']:,}", f"{self.data['counts']['cands']:,}",
                           str(estimates.attempted(self.snapshot)), str(len(self.findings['findings']))):
                self.assertIn(number, label)
            self.assertIn('aria-describedby="mf-findings"', canvas)
            self.assertIn('id="mf-findings"', markup)
            self.assertRegex(markup, r'<ol>(<li>.*?</li>){%d,}</ol>' % figure.MIN_FINDINGS)

    def test_no_text_under_12px(self):
        css = (figure.ASSETS / 'motion_figure.css').read_text()
        for size in re.findall(r'font-size:\s*(\d+(?:\.\d+)?)px', css):
            self.assertGreaterEqual(float(size), 12, size)
        js = (figure.ASSETS / 'motion_figure.js').read_text()
        for size in re.findall(r'\bsize2? ?[:=] ?(\d+(?:\.\d+)?)\b', js):
            self.assertGreaterEqual(float(size), 12, size)
        for size in re.findall(r'KEY_SIZE = (\d+)', js) + re.findall(r'Math\.max\((\d+), Math\.floor', js):
            self.assertGreaterEqual(float(size), 12)

    def test_script_is_valid_and_closed(self):
        js = (figure.ASSETS / 'motion_figure.js').read_text()
        self.assertNotIn('</script', js.lower())
        if shutil.which('node'):
            result = subprocess.run(['node', '--check', str(figure.ASSETS / 'motion_figure.js')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class PlacementTests(unittest.TestCase):
    def test_one_switch(self):
        for placement, expected in (('home', (True, False)), ('explore', (False, True)), ('none', (False, False))):
            with patch.object(figure, 'MOTION_PLACEMENT', placement):
                home, explore = figure.slot('home', 'en'), figure.slot('explore', 'en')
            self.assertEqual((bool(home), bool(explore)), expected, placement)
            for markup in (home, explore):
                if markup:
                    self.assertEqual(markup.count('id="motion-figure"'), 1)
        with self.assertRaises(ValueError):
            figure.slot('footer', 'en')

    def test_home_puts_it_between_the_map_card_and_the_levels(self):
        from scripts.viz_site import restructure_home as home
        shown = {'texts': 483414, 'candidates': 7781, 'papers': 53, 'matched': 36, 'questions': 17, 'finished': 4}
        for placement in ('home', 'none', 'explore'):
            with patch.object(figure, 'MOTION_PLACEMENT', placement), patch.object(home, 'figures', lambda: shown):
                markup = home.render('en')
            ladder = markup.index('class="nx-sec nx-ladder"')
            if placement == 'home':
                self.assertEqual(markup.count('id="motion-figure"'), 1)
                self.assertLess(markup.index('id="motion-figure"'), ladder)
                self.assertEqual(markup.count('data-home-block='), 1)    # check_v5 counts two blocks on the whole page
                self.assertEqual(markup.count('class="nx-st"'), 3)
                self.assertEqual(markup.count('nx-stair"'), 1)
            else:
                self.assertNotIn('motion-figure', markup, placement)
                self.assertTrue(markup.startswith('<section class="nx-sec nx-ladder"'))


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(figure.DATA_FILE.read_text())

    def test_the_committed_file_is_current(self):
        needed = [ROOT / 'data/l3_pooled/downloads/SPID_v1.2.tab', HERE / 'partials/story_map.html', HERE / 'data/curated_adoption.json']
        missing = [str(path.relative_to(ROOT)) for path in needed if not path.is_file()]
        if missing:
            self.skipTest('inputs missing: ' + ', '.join(missing))
        self.assertEqual(make_motion_data.main(['--check']), 0)

    def test_inputs_are_recorded(self):
        for name, entry in self.data['inputs'].items():
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$', name)
        self.assertIn('data/l3_pooled/downloads/SPID_v1.2.tab', self.data['inputs'])

    def test_the_clock_medians_follow_from_the_maps(self):
        tiles, clock = self.data['tiles'], self.data['clock']
        bands, start, found = tiles['bands'], 0, []
        for size in bands:
            within = []
            for row, src in zip(tiles['packed'][start:start + size], tiles['src'][start:start + size]):
                if row[0] + clock['k'] > clock['obsEnd'][src]:
                    continue
                within.append(sum(1 for d in row[2::2] if d <= clock['k']))
            found.append((len(within), make_motion_data.median(within)))
            start += size
        self.assertEqual(found, [(b['n'], b['median']) for b in clock['bands']])
        self.assertEqual(clock['unobserved'], sum(bands) - sum(n for n, _ in found))

    def test_every_map_starts_with_a_first_state_and_the_nurse_tile_is_the_site_map(self):
        tiles = self.data['tiles']
        for row in tiles['packed']:
            self.assertEqual(row[2], 0)                       # the first state adopts 0 years after the first
            self.assertLessEqual(max(row[1::2]), 50)
        nurse = self.data['nurse']
        self.assertEqual(self.data['tiles']['src'][tiles['open']], 'c')
        self.assertEqual(tiles['packed'][tiles['open']][0], nurse['y0'])
        self.assertEqual(len(nurse['a']), nurse['n'])
        self.assertEqual(self.data['map']['codes'], sorted(self.data['map']['codes']))
        self.assertEqual(len(self.data['map']['d']), 51)

    def test_screening_counts_end_at_the_shortlist(self):
        counts = self.data['counts']
        self.assertEqual(counts['gates'][-1], counts['cands'])
        self.assertEqual(counts['gates'], sorted(counts['gates'], reverse=True))
        self.assertLess(counts['cands'], counts['texts'])


if __name__ == '__main__':
    unittest.main()
