"""Plain-language Explore (V5.1) against frozen evidence; no estimation.

V5.1 release (owner 2026-10-20): the page is Explore. Head (lead + the one exploratory label), the Findings list of l3_findings.json, the
Questions board, then the 35-policy adoption study as one section."""
from copy import deepcopy
from decimal import Decimal
from html import unescape
import csv
import math
import json
import re
import statistics
import unittest
from unittest.mock import patch
from scripts.viz_site import restructure_explore as page
from scripts.viz_site import restructure_home as home

# The words of the page. Sections 2, 3 and 5 of docs/plans/site-restructure/EXPLORE_PLAIN_COPY_V51.md were the copy through R28; R29 applies the
# owner's trim of 2026-10-15 (docs/plans/site-restructure/TRIM_SPEC_V51.md, EX1 to EX17 and its section 4 fallback for EX11), so a string is
# approved when the copy file or the trim spec prints it, or when it is one of R29_DERIVED below.
STUDY = {
    'en': ['Party control and local need',
           'On average across 35 policies (597 adoptions), neither Democratic control nor greater local need (poverty, traffic deaths) clearly predicted which states adopted in a given year.'],
    'zh': ['党派控制与当地需要',
           '对 35 项政策（597 次采纳）取平均，民主党执政和更大的当地需要（贫困、交通事故死亡），均未能明确预测哪些州会在某一年采纳政策。'],
}
FINDINGS = json.loads(page.FINDINGS.read_text(encoding='utf-8'))

# The Methods paragraph without its offset clause (F1: the clause is said nowhere now, Boehmke M4, ZH editor M3; the study section's sentence states the
# two differences between the models and no cause).
EX11_EN = ('From the year after a policy’s first adoption, the model asks each year which states not yet adopting do so, with a baseline for every policy and year. '
           'Party control is +1 when Democrats hold the governorship and both legislative chambers on 1 January, −1 when Republicans do, 0 otherwise, the same way for every policy. '
           'Local need is an earlier measure chosen in advance for each policy type: poverty for welfare, tax-credit '
           'and minimum-wage laws; traffic deaths for drunk-driving and helmet laws. A typical difference is one standard deviation.')
EX11_ZH = ('从政策首次被采纳的下一年起，模型逐年考察尚未采纳的州是否采纳，每项政策、每一年有各自的基准。党派控制：1 月 1 日，民主党同时掌握州长职位和两院，记为 +1；共和党同时掌握，记为 −1；其余记为 0；'
           '所有政策记法相同。当地需要是事先为每类政策选定的较早年份的指标：福利、税收抵免和最低工资类法律用贫困率，酒驾和头盔类法律用交通事故死亡率。'
           '“典型差距”指该指标的一个标准差。')
SAMPLE_EN = ('The State Policy Innovation and Diffusion database lists 724 state policies; the version posted with DellaVigna and Kim’s replication data lists 648; 39 have a local-need measure '
             'and the other data needed, and the model can use 35: 597 adoptions in 11,188 state-year records of the 48 contiguous states.')
SAMPLE_ZH = ('州政策创新与扩散数据库共列有 724 项州政策；随 DellaVigna 与 Kim 复现数据发布的版本列有 648 项；39 项有当地需要指标及所需的其他数据，模型可用 35 项：美国本土 48 州的 11,188 条州—年记录，含 597 次采纳。')
ITEMS = {
    'en': {
        1: ['Results for the 35 policies', '97.5% interval', 'Democratic control', 'Local need', 'Democratic control relative to local need'],
        2: ['Odds ratio of adopting in a year, per typical difference between states (1 = no link)',
            'Odds ratio of adopting in a year when all, rather than none, of the states under the same party control have already adopted (1 = no link)'],
        3: ['Dashed line: the link size the study was planned to detect (1.49).'],
        4: ['The planned claim that party matters more than need is not supported. Every interval ends below the dashed line.'],
        5: ['Same-party states already adopted', '95% interval', '2000–2020', '2000–2020 compared with 1950–1999'],
        6: ['This figure uses all 648 policies of the replication version; the one above uses 35.'],
        7: [SAMPLE_EN],
        8: [EX11_EN],
    },
    'zh': {
        1: ['35 项政策的结果', '97.5% 区间', '民主党执政', '当地需要', '民主党执政相对当地需要'],
        2: ['某年采纳政策的优势比，各州间每相差一个典型差距（1 = 无关联）', '与本州党派控制类型相同的州已全部（而非全无）采纳时，某年采纳政策的优势比（1 = 无关联）'],
        3: ['虚线：研究计划检测的关联大小（1.49）。'],
        4: ['事先设定的“党派比需要更重要”的论断未获支持。每个区间的上端都低于虚线。'],
        5: ['同党派州已采纳', '95% 区间', '2000–2020 年', '2000–2020 年与 1950–1999 年相比'],
        6: ['本图使用复现版本的全部 648 项政策；上图使用 35 项。'],
        7: [SAMPLE_ZH],
        8: [EX11_ZH],
    },
}
# Two strings the trim spec does not print as written: the EX11 text with its offset clause kept (section 4 fallback) and the dashed-line note in
# Chinese, where the spec's wording uses a word that check_v5 bans on this page ("预设").
# V5.1 panel round F1 (2026-10-20): strings changed after the copy file and the trim spec (FIX_ROUND_F1.md lists each with the reviewers behind it).
F1_CHANGED = {STUDY['zh'][1], SAMPLE_EN, SAMPLE_ZH, '97.5% interval', '95% interval',
              'The planned claim that party matters more than need is not supported. Every interval ends below the dashed line.', 'Democratic control relative to local need', '民主党执政相对当地需要', '97.5% 区间', '95% 区间',
              'Odds ratio of adopting in a year, per typical difference between states (1 = no link)',
              'Odds ratio of adopting in a year when all, rather than none, of the states under the same party control have already adopted (1 = no link)',
              '某年采纳政策的优势比，各州间每相差一个典型差距（1 = 无关联）', '与本州党派控制类型相同的州已全部（而非全无）采纳时，某年采纳政策的优势比（1 = 无关联）',
              '事先设定的“党派比需要更重要”的论断未获支持。每个区间的上端都低于虚线。', 'Same-party states already adopted', '同党派州已采纳',
              '2000–2020 年', '2000–2020 年与 1950–1999 年相比'}
# T215 (V5.4 c2): the database total and the replication version are named apart.
C2_CHANGED = {'This figure uses all 648 policies of the replication version; the one above uses 35.', '本图使用复现版本的全部 648 项政策；上图使用 35 项。'}
R29_DERIVED = {EX11_EN, EX11_ZH, '虚线：研究计划检测的关联大小（1.49）。'} | F1_CHANGED | C2_CHANGED
TRIM = page.ROOT / 'docs/plans/site-restructure/TRIM_SPEC_V51.md'
COPY = page.ROOT / 'docs/plans/site-restructure/EXPLORE_PLAIN_COPY_V51.md'


def visible(html):
    """The page's text, without style and script, entities resolved."""
    return unescape(re.sub(r'<[^>]+>', '\n', re.sub(r'<(style|script)\b.*?</\1>', '', html, flags=re.S)))


def copy_questions():
    """Rows 1-17 of the copy file's section 5 table: (question en, question zh, status en, status zh, data en, data zh)."""
    rows = []
    for line in COPY.read_text(encoding='utf-8').split('\n'):
        cells = [cell.strip() for cell in line.strip().strip('|').split(' | ')]
        if len(cells) == 7 and cells[0].isdigit() and cells[3] in ('Finished', 'In progress', 'Waiting for data'):
            rows.append(tuple(cells[1:]))
    return rows


class ExploreV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = page.load_inputs()
        cls.html = {lang: page.render(lang) for lang in ('en', 'zh')}
        cls.text = {lang: visible(html) for lang, html in cls.html.items()}

    def test_odds_ratios_and_ranges_match_the_stored_result(self):
        result = self.inputs[0]
        for lang, html in self.html.items():
            shown = lambda estimate, lower, upper: f'{math.exp(estimate):.2f} [{math.exp(lower):.2f}, {math.exp(upper):.2f}]'
            for row in result['H1'].values():
                self.assertIn(shown(row['estimate'], row['lower'], row['upper']), html)
                for key in ('estimate', 'lower', 'upper'):
                    # Two decimals (content check of R25, M6): the four-decimal values are gone, and so is the log-odds view.
                    self.assertNotIn(f'{math.exp(row[key]):.4f}', html)
                    self.assertNotIn(f'{row[key]:.4f}'.replace('-', '−') + ' [', html)
            for key, intervals in (('late_A', result['H0']['inference']['intervals']['late_A']), ('late_minus_early_A', result['H0']['inference']['intervals']['late_minus_early_A'])):
                self.assertIn(shown(result['H0']['point'][key], *intervals), html)
                for value in (result['H0']['point'][key], *intervals):
                    self.assertNotIn(f'{math.exp(value):.4f}', html)
            # The five rows the content check lists, as they read.
            for row in ('1.07 [0.95, 1.21]', '0.96 [0.78, 1.18]', '1.11 [0.90, 1.38]', '3.01 [1.34, 6.76]', '1.84 [0.87, 3.69]'):
                self.assertIn(row, html)
            self.assertIn('97.5%', html)
            self.assertIn('95%', html)
            self.assertLess(html.index('id="findings"'), html.index('id="questions"'))
            self.assertLess(html.index('id="questions"'), html.index('id="study"'))
            self.assertLess(html.index('id="study"'), html.index('id="h1"'))
            self.assertLess(html.index('id="h1"'), html.index('id="h0"'))
            self.assertLess(html.index('id="h0"'), html.index('id="study-methods"'))

    def test_the_lead_labels_captions_and_methods_are_the_copy_files_words(self):
        for lang in ('en', 'zh'):
            text = self.text[lang]
            for line in STUDY[lang]:
                self.assertIn(line, text)
            for item, strings in ITEMS[lang].items():
                for string in strings:
                    self.assertIn(string, text, (lang, item))
            study = re.search(r'<section id="study"[^>]*><h2[^>]*>(.*?)</h2><p class="ex-lead">(.*?)</p>', self.html[lang], re.S)
            self.assertEqual([unescape(part) for part in study.groups()], STUDY[lang])

    def test_each_string_is_in_the_copy_file_or_the_trim_spec(self):
        approved = COPY.read_text(encoding='utf-8') + TRIM.read_text(encoding='utf-8')
        for lang in ('en', 'zh'):
            for line in STUDY[lang]:
                self.assertTrue(line in approved or line in R29_DERIVED, line)
            for strings in ITEMS[lang].values():
                for string in strings:
                    self.assertTrue(string in approved or string in R29_DERIVED, string)

    def test_numbers_in_the_copy_are_the_pages_data(self):
        fitted, events, rows, supplied, source_events, source_rows = self.inputs[4]
        self.assertEqual((fitted, events, rows, supplied, self.inputs[5]), (35, 597, 11188, 39, 648))
        for lang in ('en', 'zh'):
            text = self.text[lang]
            for value in ('35', '597', '648', '39', '11,188'):
                self.assertIn(value, text)

    def test_lead_has_no_kicker_and_no_source_line_and_the_sample_is_one_paragraph(self):
        # R29 (EX1): the study's heading (5 words) and one sentence (26 words) instead of two sentences.
        self.assertEqual([len(line.split()) for line in STUDY['en']], [5, 26])
        for lang, html in self.html.items():
            self.assertNotIn('ex-kicker', html)
            self.assertEqual(html.count('<header>'), 1)
            methods = re.search(r'<details class="ex-methods" id="study-methods">(.*?)</details>', html, re.S)[1]
            self.assertEqual(len(re.findall(r'<p>', methods)), 2)  # the sample, then the model
            self.assertEqual(methods.count('<h3>'), 0)  # R29 (EX9): no sub-heading

    def test_the_page_is_called_explore_and_its_sections_nest(self):
        # V5.1 release: the heading is the page's name (the navigation label); the study keeps its old heading as a section heading and its
        # figures are titled by what they show (M4 of the content check of R25), one level down.
        for lang, page_name, heading, title in (('en', 'Explore', 'Party control and local need', 'Results for the 35 policies'),
                                                ('zh', '探索', '党派控制与当地需要', '35 项政策的结果')):
            html = self.html[lang]
            self.assertEqual(re.findall(r'<h1>(.*?)</h1>', html), [page_name])
            self.assertEqual(re.findall(r'<h2[^>]*>(.*?)</h2>', html), [{'en': 'When and where policies work', 'zh': '政策何时、何处见效'}[lang], {'en': 'How states make policy', 'zh': '各州如何制定政策'}[lang], {'en': 'Which policies can be evaluated', 'zh': '哪些政策能被评估'}[lang], {'en': 'Technical checks', 'zh': '技术核对'}[lang], {'en': 'Questions', 'zh': '问题'}[lang], heading])
            self.assertEqual(re.findall(r'<figure id="h1"><h3>(.*?)</h3>', html), [title])
            self.assertEqual(re.findall(r'<figure id="h0"><h3>', html), ['<figure id="h0"><h3>'])
            self.assertEqual(sum(h == heading for h in re.findall(r'<h[123][^>]*>(.*?)</h[123]>', html)), 1)
            self.assertIn('<a href="#h1">', html)
            self.assertEqual(json.loads((page.ROOT / f'scripts/viz_site/i18n/common.{lang}.json').read_text())['nav.synthesize'], page_name)

    def test_apostrophes_and_quotation_marks_are_typographic(self):
        # M7 of the content check of R25: Estimates and Method use the curly apostrophe throughout, and no ZH page has an ASCII double quote.
        for lang, html in self.html.items():
            text = self.text[lang]
            self.assertNotIn("'", text)
            self.assertNotIn('"', text)
        self.assertIn('a policy’s first adoption', self.text['en'])
        self.assertIn('earlier states’ policies', self.text['en'])
        self.assertIn('early adopters’ results', self.text['en'])
        self.assertIn('“党派比需要更重要”', self.text['zh'])
        self.assertIn('“典型差距”', self.text['zh'])

    def test_axis_ticks_are_round_odds_ratios_at_their_positions(self):
        # S3 of the content check of R25: round odds ratios, the grey line at a tick labelled 1, the axis domains unchanged.
        expected = {'h1': (['0.8', '1.0', '1.2', '1.4', '1.6'], [8.69, 29.62, 50.55, 71.47, 92.40], (-.3, .5)),
                    'h0': (['1', '2', '4', '6', '8'], [5.82, 18.61, 44.20, 69.79, 95.37], (-.3, 2.1))}
        for lang, html in self.html.items():
            for key, (labels, lefts, (low, high)) in expected.items():
                figure = re.search(r'<figure id="%s">.*?</figure>' % key, html, re.S)[0]
                ticks = re.findall(r'<span style="left:([\d.]+)%">([^<]*)</span>', re.search(r'<div class="ex-ticks">(.*?)</div>', figure, re.S)[1])
                self.assertEqual([label for _, label in ticks], labels, (lang, key))
                for (left, _), want in zip(ticks, lefts):
                    self.assertAlmostEqual(float(left), want, delta=.01, msg=(lang, key))
                # Each tick sits where its odds ratio is drawn on the unchanged axis domain (log-odds -0.3 to 0.5, and -0.3 to 2.1).
                lo, hi = math.exp(low), math.exp(high)
                position = lambda odds: (10 + 380 * (odds - lo) / (hi - lo)) / 4
                for (left, label) in ticks:
                    self.assertAlmostEqual(float(left), position(float(label)), delta=.001)
                grey = float(re.search(r'<path d="M([\d.]+) 2V34" stroke="#94a3b8"', figure)[1]) / 4
                self.assertAlmostEqual(grey, position(1), delta=.001)
                self.assertTrue(any(abs(float(left) - grey) < .01 for left, _ in ticks), 'a tick labelled 1 sits where the grey line is')
            # No tick sits at an axis end, so no label is pinned to an edge any more (the old pin rule would misplace the first and last labels).
            self.assertNotIn('ex-ticks span:first-child', html)
            self.assertNotIn('ex-ticks span:last-child', html)

    def test_the_methods_box_is_labelled_methods_and_has_no_sub_heading(self):
        # R29 (EX14, EX9): the summary says "Methods" and the sample and model paragraphs follow it directly (the id stays for the Method page's link).
        for lang, label in (('en', 'Methods'), ('zh', '方法')):
            methods = re.search(r'<details class="ex-methods" id="study-methods">(.*?)</details>', self.html[lang], re.S)[1]
            self.assertEqual(re.findall(r'<summary>(.*?)</summary>', methods), [label])
            self.assertEqual(re.findall(r'<h3>(.*?)</h3>', methods), [])
        for gone in ('Study sample', 'Study and methods', 'Sample and model', 'Grey line'):
            self.assertNotIn(gone, self.text['en'])
        for gone in ('研究样本', '研究与方法', '样本与模型', '灰线'):
            self.assertNotIn(gone, self.text['zh'])

    def test_dropped_wording_and_the_log_odds_selector_are_gone(self):
        for lang, terms in (('en', ('tilt', 'pooled', 'common sign', 'inconclusive', 'time-stamped', 'private research record', 'og-odds', 'pre-specified',
                                  'alignment', 'of 648', 'exponentiated', 'fixed-effect', 'seat-belt', 'OR 1.4947', 'Standardized', 'Scale', '+.4019',
                                  'null value', 'upper bounds', 'diffusion between states')),
                            ('zh', ('倾向', '合并', '同一符号', '尚无定论', '注明时间', '非公开研究记录', '对数优势', '预设', '对齐', '固定效应', '安全带',
                                    '标准化', '尺度', '+.4019', '无效应值', '上界'))):
            for term in terms:
                self.assertNotIn(term, self.text[lang], term)
            body = re.sub(r'<style>.*?</style>', '', self.html[lang], flags=re.S)
            self.assertNotIn('<select', body)
            self.assertNotIn('data-scale', body)
            self.assertNotRegex(body, r'\shidden[\s>]')
            # Bare "SPID" is only the database's name in the Sources panel; the lead, the labels and the Methods panel do not use it.
            self.assertNotIn('SPID', visible(body.split('id="explore-sources"')[0]))
            self.assertIn('SPID', visible(body.split('id="explore-sources"')[1]))

    def test_sample_flow_counts_and_the_failed_audit_counts_are_not_shown(self):
        # Owner, 2026-10-14 (R21): the lines citing the audit counts are gone, and with them the parser that fed them.
        self.assertEqual(self.inputs[4], [35, 597, 11188, 39, 599, 11381])
        self.assertEqual(self.inputs[5], 648)
        self.assertEqual(len(self.inputs), 6)
        self.assertFalse(hasattr(page, 'parse_audit'))
        for lang, text in self.text.items():
            # V5.1 item 7: three paragraphs become one; the source counts (599, 11,381) and the separation counts (193, 4, 2) leave the page.
            for gone in ('599', '11,381', '193 '):
                self.assertNotIn(gone, text)
            for gone in ('item-level removal map', 'AI source check', 'verifiable policy', 'status errors', 'The audit sampled', 'checked separately',
                         'not an overall error rate', '来源核查', '状态错误', '审计抽查', '另行抽查', '不代表整体错误率'):
                self.assertNotIn(gone, text)
        # R29 (EX12): the update stamp of Sources is gone, as on Home and Estimates.
        self.assertNotIn('adoption study, October 7, 2026', self.text['en'])
        self.assertNotIn('2026 年 10 月 7 日的政策采纳研究', self.text['zh'])

    def test_public_scope_and_sources(self):
        for lang, html in self.html.items():
            for internal in ('id="exploratory"', '5/45', '26/35', '48/58', 'EX-1', 'DEV v2', 'TERMINAL_RUNS', 'DATA_CORRECTION', 'not_licensed', 'C17'):
                self.assertNotIn(internal, html)
            for cited in ('10.1111/psj.12357', '10.1093/restud/rdaf070', 'id="explore-sources"'):
                self.assertIn(cited, html)
            # V5.1 item 7 deletes the authors'-sample sentence (602 = 549 + 53).
            for gone in ('549', '(?<![0-9])602(?![0-9])'):
                self.assertIsNone(re.search(gone, self.text[lang]), gone)
        self.assertNotIn('Separate audit of the policy list supplied by DellaVigna–Kim', self.html['en'])

    def test_planning_marker_and_labels_follow_frozen_scale(self):
        inputs = list(deepcopy(self.inputs))
        inputs[3]['h1_scaling']['party_scale'] = .7
        planning = .64 * .7
        with patch.object(page, 'load_inputs', return_value=inputs):
            html = page.render('en')
        self.assertIn(f'Dashed line: the link size the study was planned to detect ({math.exp(planning):.2f}).', visible(html))
        expected_x = 10 + 380 * (math.exp(planning) - math.exp(-.3)) / (math.exp(.5) - math.exp(-.3))
        self.assertIn(f'M{expected_x:.3f} 2V34" stroke="#b45309"', html)
        self.assertNotIn('(1.49)', visible(html))

    def test_caption_fails_closed_when_a_range_would_end_above_the_dashed_line(self):
        # "Every range ends below the dashed line" is a claim about the data: a planned effect below an upper limit stops the build.
        inputs = list(deepcopy(self.inputs))
        inputs[3]['h1_scaling']['party_scale'] = .2
        with patch.object(page, 'load_inputs', return_value=inputs):
            with self.assertRaisesRegex(ValueError, 'dashed line'):
                page.render('en')

    def test_accessibility_and_public_artifact(self):
        for html in self.html.values():
            self.assertIn('id="study-methods"', html)
            self.assertNotIn('/private/tmp', html)
            self.assertNotIn('/Users/', html)
            self.assertNotIn('github.com', html)
            self.assertNotIn('dv_spid:', html)
            text = re.sub(r'<(style|script)\b.*?</\1>', '', html, flags=re.S)
            self.assertNotRegex(text, r'(?i)\b(?:frozen|hash|receipt|snapshot|optimizer|branch)\b|哈希|冻结|运行记录|分支')
            self.assertNotIn('<dl>', html)
            self.assertIn('min-height:44px', html)
            self.assertEqual(html.count('class="ex-dot"'), 5)
            self.assertNotIn('<circle', html)
            self.assertIn('width:8px;height:8px;border-radius:50%', html)
            self.assertNotIn('<script', html)  # no selector, so no script
        en_without_styles = re.sub(r'<(style|script)\b.*?</\1>', '', self.html['en'], flags=re.S)
        self.assertNotRegex(en_without_styles, r'[㐀-鿿]')

    def test_public_metadata_uses_plain_language(self):
        for lang in ('en', 'zh'):
            metadata = json.loads((page.ROOT / f'scripts/viz_site/i18n/synthesize.{lang}.json').read_text())
            self.assertNotRegex(metadata['page.description'], r'(?i)hash|frozen|internally|哈希|冻结|内部|confirm|确认')
            # M3 of the content check of R25: the snippet and the link preview no longer carry what the page dropped.
            for gone in ('SPID', 'time-stamped', 'private research record', '记录时间', '注明时间', '非公开研究记录'):
                self.assertNotIn(gone, metadata['page.description'])
        # V5.1 release: the page is about L3 as a whole, so the description no longer leads with the 35-policy study.
        self.assertEqual(json.loads((page.ROOT / 'scripts/viz_site/i18n/synthesize.en.json').read_text())['page.description'],
                         'Patterns across many policies, and the questions we are working on. One study asks which states adopt a policy.')
        self.assertEqual(json.loads((page.ROOT / 'scripts/viz_site/i18n/synthesize.zh.json').read_text())['page.description'],
                         '跨多项政策的规律，以及我们正在研究的问题。其中一项研究问哪些州会采纳政策。')


def question_text(n, lang):
    """Row n's question as the page prints it: the copy file's, or the F1 rewording."""
    qen, qzh = copy_questions()[n - 1][:2]
    en, zh = V51_QUESTION.get(n, (None, None))
    return (en or qen) if lang == 'en' else (zh or qzh)


def question_groups(html):
    """[(heading, [(question html, data line)])] of the Questions list: the status headings, each printed once, and the rows under them."""
    section = re.search(r'<section id="questions".*?</section>', html, re.S)[0]
    groups = []
    for heading, items in re.findall(r'<h3>([^<]*)</h3><ol class="ex-questions">(.*?)</ol>', section, re.S):
        rows = [(match[0], unescape(match[1])) for match in re.findall(r'<li><span class="ex-q">(.*?)</span>(?:<span class="ex-data">([^<]*)</span>)?</li>', items, re.S)]
        groups.append((heading, rows))
    return groups


# R29 (EX3): the rows whose data line only restated the question print none. V5.1 release (hub, 2026-10-20): row 15 prints the count of the
# designs the refresh of 2026-10-18 attempted again, so it leaves this list; rows 2, 6 and 11 keep no line.
NO_DATA_LINE = (2, 6, 7, 8, 10, 11)
# V5.1 release (hub, 2026-10-20) on top of the copy file's table: row 2 is in progress (the registered strict-match version is still
# blocked), rows 6 and 11 are finished; rows 9, 15 and 16 count the L2 designs of the refresh of 2026-10-18 (RESULTS_EX1_V3.json).
V51_STATUS = {2: 'in_progress', 5: 'in_progress', 6: 'in_progress', 7: 'finished', 10: 'finished', 11: 'finished', 12: 'in_progress', 13: 'in_progress', 17: 'in_progress'}
# F6 (Liebman M2): rows 6, 12 and 13 have a result file but no answer on the page, so they are in progress; Finished holds rows 1 and 11.
# V5.1 panel round F1 (2026-10-20): row 17 is in progress (Stan2 produced no estimate: zero comparable cases in 317 texts, docs/plans/l3-ideas/Stan2/RESULT.md);
# the wording of rows 3, 4, 10, 11, 12 and 13 is made exact: (English, Chinese), None where only the Chinese changes.
V51_QUESTION = {3: (None, '先行州的政策见效后，后续的州更可能采纳吗？'), 4: (None, '失败对后续采纳的拖累，比成功的推动更大吗？'),
                10: (None, '后来采纳州是重复早期采纳州的结果，还是更好？'),
                # F1b (panel round 2): row 11 names the comparison group exactly (states without the policy); row 12 is the R30 English question again
                # (the study measures the share of clauses whose treatment depends on a past trajectory) and the Chinese gets its missing noun.
                11: ('Do adopting states differ from states without the policy?', '采纳州与没有该政策的州有差别吗？'),
                12: (None, '法律多常取决于过去的情况，而不只是当前的事实？'),
                13: (None, '企业规则审查资源的回溯期，比家庭规则更长吗？')}
V51_DATA = {5: ('16 laws', '16 项法律'), 9: ('210 forecasts for 42 estimates', '42 项估计的 210 条预测'), 15: ('155 attempted designs', '155 个已尝试的设计'),
            16: ('35 forecasts for 7 estimates', '7 项估计的 35 条预测')}
# V5.5 (T237): rows 7 (grow or fade) and 10 (later adopters) are answered by the new stories and are finished.
FINISHED, IN_PROGRESS, WAITING = [1, 7, 10, 11], [2, 5, 6, 9, 12, 13, 15, 16, 17], [3, 4, 8, 14]


class QuestionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = {lang: page.render(lang) for lang in ('en', 'zh')}
        cls.groups = {lang: question_groups(html) for lang, html in cls.html.items()}
        # (question, status heading, data line) in the order printed: grouped by status, the copy file's order inside a group.
        cls.rows = {lang: [(question, heading, data) for heading, rows in groups for question, data in rows] for lang, groups in cls.groups.items()}

    def by_copy_number(self, lang):
        """{row number of the copy file: (question, status heading, data line)}, matched on the question text (with the F1 rewordings)."""
        shown = {unescape(re.sub(r'<[^>]+>', '', question)): row for row in self.rows[lang] for question in [row[0]]}
        return {n: shown[question_text(n, lang)] for n in range(1, 18)}

    def test_seventeen_questions_four_finished_nine_in_progress_and_four_waiting_for_data(self):
        # M9 (hub default, third status), R29 (EX2): three headings, each printed once, with the 4, 8 and 5 questions under them (V5.5: rows 7 and 10 finished) (V5.1 release, F1: row 17 in progress; F6: rows 6, 12, 13 in progress).
        for lang, words in (('en', ('Finished', 'In progress', 'Waiting for data')), ('zh', ('已完成', '进行中', '等待数据'))):
            groups = self.groups[lang]
            self.assertEqual(tuple(heading for heading, _ in groups), words)
            self.assertEqual(tuple(len(rows) for _, rows in groups), (4, 9, 4))
            self.assertNotIn('ex-status', self.html[lang])
            numbered = self.by_copy_number(lang)
            statuses = {n: row[1] for n, row in numbered.items()}
            self.assertEqual(len(statuses), 17)
            self.assertEqual([n for n, status in statuses.items() if status == words[0]], FINISHED)
            self.assertEqual([n for n, status in statuses.items() if status == words[1]], IN_PROGRESS)
            self.assertEqual([n for n, status in statuses.items() if status == words[2]], WAITING)
            self.assertEqual(re.findall(r'<h2 id="ex-questions-h">(.*?)</h2>', self.html[lang]), ['Questions' if lang == 'en' else '问题'])

    def test_questions_are_the_copy_files_in_its_order(self):
        copy = copy_questions()
        self.assertEqual(len(copy), 17)
        for lang in ('en', 'zh'):
            numbered = self.by_copy_number(lang)
            for n, (qen, qzh, sen, szh, den, dzh) in enumerate(copy, 1):
                question, status, data = numbered[n]
                self.assertEqual(unescape(re.sub(r'<[^>]+>', '', question)), question_text(n, lang))
                # The status and the data line are the copy file's, with the V5.1 release changes (V51_STATUS, V51_DATA) and the rows that
                # print no data line after R29 (EX3).
                self.assertEqual(status, page.STATUS[V51_STATUS[n]][lang == 'zh'] if n in V51_STATUS else (sen if lang == 'en' else szh))
                want = '' if n in NO_DATA_LINE else V51_DATA[n][lang == 'zh'] if n in V51_DATA else unescape(den if lang == 'en' else dzh)
                self.assertEqual(data, want, (lang, n))
            # Inside a group the rows keep the copy file's order.
            order = [n for heading, _ in self.groups[lang] for n in sorted(n for n, row in numbered.items() if row[1] == heading)]
            printed = [unescape(re.sub(r'<[^>]+>', '', row[0])) for row in self.rows[lang]]
            self.assertEqual(printed, [question_text(n, lang) for n in order])
        # The data file carries the same rows, with the adoption study's numbers as placeholders.
        stored = json.loads(page.QUESTIONS.read_text(encoding='utf-8'))['rows']
        self.assertEqual([row['question']['en'] for row in stored], [question_text(n, 'en') for n in range(1, 18)])
        self.assertEqual([row['question']['zh'] for row in stored], [question_text(n, 'zh') for n in range(1, 18)])
        self.assertEqual([row['n'] for row in stored if not row['data']['en'] and not row['data']['zh']], list(NO_DATA_LINE))

    def test_first_question_links_to_the_adoption_study_and_carries_its_numbers(self):
        fitted, events = page.load_inputs()[4][:2]
        for lang, data in (('en', f'{fitted} policies, {events} adoptions'), ('zh', f'{fitted} 项政策，{events} 次采纳')):
            question, status, shown = self.rows[lang][0]
            self.assertIn('<a href="#h1">', question)
            self.assertEqual(unescape(shown), data)
            self.assertIn('id="h1"', self.html[lang])
            # Only that one row links; every other row is plain text.
            self.assertEqual(sum('<a ' in row[0] for row in self.rows[lang]), 1)

    def test_questions_are_short_and_show_no_result(self):
        for lang in ('en', 'zh'):
            for question, status, data in self.rows[lang]:
                words = unescape(re.sub(r'<[^>]+>', '', question))
                if lang == 'en':
                    self.assertLessEqual(len(words.split()), 12, words)
                else:
                    self.assertLessEqual(len(words), 23, words)
                # An unfinished question shows its status and the data it uses, never an estimate or a range.
                self.assertNotRegex(unescape(data), r'[\[\]%]|odds|\bOR\b|几率|范围')
        self.assertEqual(page.STATUS, {'finished': ('Finished', '已完成'), 'in_progress': ('In progress', '进行中'), 'waiting': ('Waiting for data', '等待数据')})

    def test_the_data_lines_carry_no_count_that_belongs_to_another_set(self):
        # M5 of the content check of R25: 257, 52 and 137 count designs of other sets than the 42 papers on Estimates, and none is dated on the page.
        for lang in ('en', 'zh'):
            data = ' | '.join(shown for _, _, shown in self.rows[lang][1:])
            for gone in ('257', '52 ', '137', '52', 'checked against the literature', 'coded law purposes', '文献比对', '编码的立法目的'):
                self.assertNotIn(gone, data, (lang, gone))
        # R29 (EX3): the lines that only restated the question are gone; the rows print no data line at all.
        for lang in ('en', 'zh'):
            numbered = self.by_copy_number(lang)
            self.assertEqual({n: numbered[n][2] for n in (2, 5, 6)}, {2: '', 5: '16 laws' if lang == 'en' else '16 项法律', 6: ''})
            self.assertEqual(numbered[15][2], V51_DATA[15][lang == 'zh'])
            self.assertTrue(numbered[3][2] and numbered[9][2] and numbered[12][2])  # rows with a number keep their line
            self.assertNotIn('published estimates sought for each analysis', self.html['en'])
            self.assertNotIn('为每项分析查找的已发表估计', self.html['zh'])
        self.assertNotIn('live set', json.loads(page.QUESTIONS.read_text(encoding='utf-8'))['basis'])

    def test_a_malformed_question_row_stops_the_build(self):
        rows = json.loads(page.QUESTIONS.read_text(encoding='utf-8'))
        for change, message in ((lambda r: r['rows'].pop(3), 'numbered'),
                                (lambda r: r['rows'][2].update(status='done'), 'malformed'),
                                (lambda r: r['rows'][4]['question'].update(en='one two three four five six seven eight nine ten eleven twelve thirteen'), '12 words')):
            broken = deepcopy(rows)
            change(broken)
            page.load_questions.cache_clear()
            try:
                with patch.object(page, '_read', return_value=broken):
                    with self.assertRaisesRegex(ValueError, message):
                        page.load_questions()
            finally:
                page.load_questions.cache_clear()


NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?|\.\d+')
SNAPSHOT_TIME = '2026-10-17T02:29:09'


def numbers_in(text):
    """The numbers a sentence prints, as Decimals without sign or thousands separator, in the order printed."""
    return [Decimal(token.replace(',', '')) for token in NUMBER.findall(text)]


def audit_numbers(rows, rounded=False):
    """The numbers the audit data of l3_findings.json accounts for: each value and range end, the digits of its label and sample, and the
    percent change of an odds ratio. `rounded` also accepts a number rounded to a whole one (the "not seen" line says 48% for 48.1)."""
    known = set()
    for row in rows:
        for entry in row['numbers']:
            for value in (entry['value'], entry['low'], entry['high']):
                if value is not None:
                    known.add(abs(Decimal(str(value))))
                    if entry['unit'] == 'odds ratio':
                        known.add((abs(Decimal(str(value))) - 1) * 100)
            known.update(numbers_in(entry['label'] + ' ' + entry['n']))
    if rounded:
        known.update(x.quantize(Decimal(1)) for x in list(known))
    return {x.quantize(Decimal('0.0001')) for x in known}


class FindingsTests(unittest.TestCase):
    """The Findings list, the exploratory label and the sentence that reconciles the 35-policy study (V5.1 release)."""
    @classmethod
    def setUpClass(cls):
        cls.html = {lang: page.render(lang) for lang in ('en', 'zh')}
        cls.text = {lang: visible(html) for lang, html in cls.html.items()}
        cls.rows = sorted(FINDINGS['findings'], key=lambda row: row['order'])

    def known_rows(self):
        return {row['id']: row for row in self.rows} | {part['id']: part for part in FINDINGS['not_seen']['parts']}

    def test_four_plain_findings_are_listed_in_both_languages_word_for_word(self):
        # T211 (owner 2026-10-21, "听不懂"): the page prints the plain version of the findings file (`explore`): a headline, one sentence on why it
        # matters and a muted numbers line. The audit rows (`findings`, seven, whose short labels Home reads) are unchanged; three findings (P5, D4,
        # X6) and two of the three "not seen" parts (D1-P5, M3), and the null X2 (48% against 50%) are method notes or depend on a doubtful benchmark and are not printed.
        self.assertEqual([row['id'] for row in self.rows], ['N1-V2', 'T64', 'P5', 'D4', 'N11-M7', 'X6', 'REPGAP'])
        view = FINDINGS['explore']
        self.assertEqual([item['id'] for item in view['items']], ['REPGAP', 'N1-V2', 'T64', 'N11-M7'])
        self.assertEqual(view['removed'], ['P5', 'D4', 'X6', 'D1-P5', 'M3', 'X2'])
        known = self.known_rows()
        for lang, html in self.html.items():
            section = re.search(r'<section id="findings".*?</section>', html, re.S)[0]
            items = re.findall(r'<li><h3>([^<]*)</h3><div><p>([^<]*)</p><p class="ex-numbers">([^<]*)</p></div></li>', section)
            self.assertEqual(len(items), 4)
            self.assertIn('<p class="ex-how">' + escape_text(view['how'][lang]) + '</p></section>', section)
            self.assertEqual([tuple(unescape(x) for x in item) for item in items],
                             [tuple(page.explore_text(it, f, lang, known[it['id']].get('computed')) for f in ('head', 'why', 'numbers')) for it in view['items']])
            self.assertNotIn('ex-notseen', section)
            self.assertEqual(unescape(re.search(r'<header>.*?<p class="ex-lead">([^<]*)</p>', html, re.S)[1]), view['lead'][lang])
            # The lead does not print a count: Home counts the audit rows, Explore lists the plain version.
            self.assertNotRegex(unescape(re.search(r'<p class="ex-lead">([^<]*)</p>', html)[1]), r'\d')

    def test_headlines_are_plain_and_short(self):
        jargon = re.compile(r'noise line|reshuffle|random timing|excess|variance|false alarm|p\s*=|标准差|噪声线|重排|随机时点|误报|方差', re.I)
        for item in FINDINGS['explore']['items']:
            self.assertLessEqual(len(item['en']['head'].split()), 15, item['id'])
            self.assertLessEqual(len(item['zh']['head']), 36, item['id'])
            for lang in ('en', 'zh'):
                self.assertIsNone(jargon.search(item[lang]['head']), item[lang]['head'])
                self.assertNotRegex((item[lang]['head'] + item[lang]['why']).replace('10%', '').replace('1990', ''), r'\d')      # the numbers live in the muted line (10% is the rule Estimates prints, 1990 the year the party link starts)
        # Chinese says 州 for states, never 状态.
        for lang, html in self.html.items():
            if lang == 'zh':
                self.assertNotIn('状态', html)
        # A headline with jargon, or too long, stops the build.
        for change in (lambda d: d['explore']['items'][0]['en'].update(head='The noise line is crossed'),
                       lambda d: d['explore']['items'][1]['zh'].update(head='随机时点' * 3),
                       lambda d: d['explore']['items'][0]['en'].update(head=' '.join(['word'] * 16)),
                       lambda d: d['explore']['items'][0].update(id='P5'),
                       lambda d: d['explore']['items'].append(dict(d['explore']['items'][0]))):
            broken = deepcopy(FINDINGS)
            change(broken)
            page.load_findings.cache_clear()
            try:
                with patch.object(page, '_read', return_value=broken):
                    with self.assertRaises(ValueError):
                        page.load_findings()
            finally:
                page.load_findings.cache_clear()

    def test_every_number_of_a_finding_line_is_one_of_its_audit_numbers(self):
        known_rows = self.known_rows()
        for item in FINDINGS['explore']['items']:
            row = known_rows[item['id']]
            known = audit_numbers([row])
            for lang in ('en', 'zh'):
                text = page.explore_text(item, 'numbers', lang, row.get('computed')).replace('95% interval', '').replace('95% 区间', '')
                for number in numbers_in(text):
                    self.assertIn(number.quantize(Decimal('0.0001')), known, (item['id'], lang, number))
        self.assertEqual(numbers_in(FINDINGS['reconcile_35']['en']), [Decimal(166), Decimal(35)])

    def test_the_numbers_are_the_same_in_english_and_chinese(self):
        for item in FINDINGS['explore']['items']:
            computed = self.known_rows()[item['id']].get('computed')
            self.assertEqual(sorted(numbers_in(page.explore_text(item, 'numbers', 'en', computed))), sorted(numbers_in(page.explore_text(item, 'numbers', 'zh', computed))), item['id'])
        self.assertEqual(sorted(numbers_in(FINDINGS['reconcile_35']['en'])), sorted(numbers_in(FINDINGS['reconcile_35']['zh'])))

    def test_the_page_prints_exactly_the_numbers_of_the_findings_file(self):
        known = self.known_rows()
        for lang, html in self.html.items():
            section = re.search(r'<section id="findings".*?</section>', html, re.S)[0]
            printed = numbers_in(' '.join(unescape(text) for text in re.findall(r'<p[^>]*>([^<]*)</p>', section) if text != FINDINGS['explore']['how'][lang]))
            wanted = [n for it in FINDINGS['explore']['items'] for field in ('why', 'numbers') for n in numbers_in(page.explore_text(it, field, lang, known[it['id']].get('computed')))]
            self.assertEqual(sorted(printed), sorted(wanted))

    def test_the_exploratory_label_is_printed_once_and_no_result_is_called_confirmed(self):
        for lang in ('en', 'zh'):
            label = FINDINGS['label'][lang]
            # T211 (owner 2026-10-21): the label is the short one; the 95% intervals are named where they are printed (each numbers line), the
            # 35-policy study keeps its own registered 97.5% level. Home prints the same short label.
            self.assertEqual(label, {'en': 'All findings here are exploratory.', 'zh': '此处所有发现均为探索性。'}[lang])
            self.assertEqual(FINDINGS['label_home'][lang], label)
            self.assertIn('97.5% interval' if lang == 'en' else '97.5% 区间', self.text[lang])   # the study keeps its own registered level, in the same word
            self.assertEqual(self.text[lang].count(label), 1)
            self.assertEqual(self.html[lang].count('class="ex-label"'), 1)
            # The label follows the lead inside the page head, before the first finding.
            self.assertLess(self.html[lang].index(escape_text(label)), self.html[lang].index('id="findings"'))
            self.assertNotRegex(self.text[lang], r'(?i)confirm|确认|证实|已证明')
            self.assertNotIn('exploratory' if lang == 'en' else '探索性', visible(re.search(r'<section id="findings".*?</section>', self.html[lang], re.S)[0]))
        # A finding that is not exploratory, or text that says confirmed, stops the build.
        for change in (lambda d: d['findings'][0].update(status='confirmed'), lambda d: d['explore']['items'][2]['en'].update(why='A confirmed result.'),
                       lambda d: d['explore']['items'][3]['zh'].update(numbers='已确认的结果。'), lambda d: d['explore']['items'][1]['en'].update(head='')):
            broken = deepcopy(FINDINGS)
            change(broken)
            page.load_findings.cache_clear()
            try:
                with patch.object(page, '_read', return_value=broken):
                    with self.assertRaises(ValueError):
                        page.load_findings()
            finally:
                page.load_findings.cache_clear()

    def test_home_short_labels_are_whole_and_short(self):
        # F1c (art review M5): the Home labels are read alone on the canvas: at most 5 words in English and 7 characters in Chinese.
        for row in self.rows:
            self.assertLessEqual(len(row['short']['en'].split()), 5, row['id'])
            # F6 (hub, carried item): the ZH label of finding 6 may have 10 characters ("基期高，估计超出随机"); the others keep 7.
            self.assertLessEqual(len(row['short']['zh']), 10 if row['id'] == 'X6' else 7, row['id'])
        self.assertEqual(next(r for r in self.rows if r['id'] == 'D4')['short'], {'en': 'Estimates vary by policy', 'zh': '估计因政策而异'})

    def test_no_id_of_a_result_file_is_on_the_page(self):
        for lang, html in self.html.items():
            for term in ('N1-V2', 'N11-M7', 'T64', 'REPGAP', 'SELREF', 'EX-1', 'X8', 'X6', 'D4', 'P5', 'M7', 'N11'):
                self.assertNotRegex(html, r'\b' + re.escape(term) + r'\b')

    def test_lines_are_short_and_plain(self):
        known = self.known_rows()
        for item in FINDINGS['explore']['items']:
            self.assertLessEqual(len(page.explore_text(item, 'numbers', 'en', known[item['id']].get('computed')).split()), 60, item['id'])
            self.assertLessEqual(len(item['en']['why'].split()), 16, item['id'])
        # The head, the findings and the "not seen" line (the question board and the study keep their approved wording): no stock AI word, no
        # em dash, no "not X but Y" contrast.
        for lang, html in self.html.items():
            fresh = visible(re.search(r'<header>.*?</header>', html, re.S)[0] + re.search(r'<section id="findings".*?</section>', html, re.S)[0])
            for word in ('delve', 'pivotal', 'landscape', 'robust', 'crucial', 'showcase', 'testament', 'honest', 'transparent'):
                self.assertNotIn(word, fresh)
            self.assertNotIn('—', fresh if lang == 'en' else '')
            self.assertNotRegex(fresh, r'(?i)\bnot just\b|\bnot only\b|\bit’s not\b|\bisn’t\b|\bnot [a-z ]{1,20}, but\b|不是[^。；]{1,12}，而是')

    def test_the_party_control_sentence_sits_under_the_study_heading(self):
        for lang, html in self.html.items():
            reconcile = FINDINGS['reconcile_35'][lang]
            study = re.search(r'<section id="study"[^>]*><h2[^>]*>([^<]*)</h2><p class="ex-lead">([^<]*)</p><p>([^<]*)</p>', html)
            self.assertEqual(unescape(study[3]), reconcile)
            self.assertEqual(unescape(study[1]), STUDY[lang][0])
            self.assertEqual(html.count(escape_text(reconcile)), 1)
            self.assertLess(html.index('id="questions"'), html.index(escape_text(reconcile)))
            self.assertLess(html.index(escape_text(reconcile)), html.index('id="h1"'))
        self.assertEqual(FINDINGS['reconcile_35']['placement'], 'under_study_heading')

    def test_finding_7_is_computed_from_the_pinned_snapshot(self):
        """F1 (hub decision 2): the line on re-estimates agrees with Estimates (53 papers, 36 within 10%) because it is formatted at build time from the
        pinned snapshot, never typed. The test recomputes the four counts from the raw snapshot file with the page's own rule and compares them with the
        loader and with the page."""
        row = next(r for r in self.rows if r['id'] == 'REPGAP')
        for lang in ('en', 'zh'):
            self.assertRegex(row[lang]['line'], r'\{papers\}.*\{within\}.*\{other\}.*\{closer\}')
            self.assertNotRegex(row[lang]['line'].replace('10%', ''), r'\d')   # no count is typed into the file (10% is the rule Estimates prints)
        self.assertEqual(row['computed']['fields'], ['papers', 'within', 'other', 'closer'])
        self.assertEqual(row['en']['head'], 'Most re-estimates land within 10%')     # F6 (Finkelstein SF1)
        self.assertEqual(row['zh']['head'], '多数重估差距不超过 10%')
        self.assertEqual(row['short'], {'en': 'Most within 10%', 'zh': '多数在10%内'})      # G7 (Finkelstein): the Home label agrees with the Explore head (ZH 7 characters)
        # No median ratio, no "smaller", no "first 49": the earlier wording is gone.
        self.assertNotRegex(row['en']['line'] + row['zh']['line'], r'(?i)median|smaller|first \d|ratio|49|中位数|我们更小|最先')
        # F1b: the 36 are counted by the rule Estimates states (10% of the published value or of its standard error, whichever is larger), and the line says so
        # in the words of the Estimates lead, in both languages.
        # F1d (Liebman): the rule is 10% of the LARGER of the two bases; "or its standard error, when larger" could be read as the looser rule.
        self.assertIn('within 10% of the larger of the published estimate’s absolute value and its standard error', row['en']['line'])
        self.assertIn('不超过发表值绝对值的 10%（若发表标准误更大，则以标准误为基数）', row['zh']['line'])
        self.assertIn('within 10% of the larger of the published estimate’s absolute value and its standard error', estimates_lead_en())
        pin = json.loads((page.ROOT / 'scripts/viz_site/data/snapshot_pin.json').read_text(encoding='utf-8'))
        snapshot = json.loads((page.ROOT / pin['snapshot_path']).read_text(encoding='utf-8'))
        compared = [p for p in snapshot['papers'] if p.get('primary')]
        def inside(run):      # the Estimates rule: |ours - published| <= 10% of the larger of |published| and the published standard error
            ours, published = Decimal(str(run['ours']['point'])), Decimal(str(run['published']['point']))
            se = abs(Decimal(str(run['published'].get('se') or 0)))
            return abs(ours - published) <= Decimal('0.10') * max(abs(published), se)
        others = [p for p in compared if not inside(p['primary'])]
        closer = [p for p in others if Decimal(str(p['primary']['ours']['point'])) * Decimal(str(p['primary']['published']['point'])) > 0
                  and abs(Decimal(str(p['primary']['ours']['point']))) < abs(Decimal(str(p['primary']['published']['point'])))]
        counts = {'papers': len(compared), 'within': len(compared) - len(others), 'other': len(others), 'closer': len(closer)}
        self.assertEqual(counts, {'papers': 53, 'within': 36, 'other': 17, 'closer': 13})
        from scripts.viz_site import restructure_estimates as estimates
        self.assertEqual(estimates.reestimate_summary(), counts)
        self.assertEqual(counts['papers'], estimates.attempted(estimates.load_snapshot()))
        self.assertEqual(counts['within'], estimates.public_counts(estimates.load_snapshot())['number_matches'])
        self.assertEqual({e['value'] for e in row['numbers']}, set(counts.values()))
        self.assertEqual(page.finding_line(row, 'en'), 'Of the 53 papers re-estimated (easiest-to-access data first), 36 are within 10% of the larger of the published estimate’s absolute value and its standard error; of the other 17, our estimate is closer to zero in 13.')
        self.assertEqual(page.finding_line(row, 'zh'), '在已重新估计的 53 篇论文（优先处理数据最易取得者）中，36 篇与发表值之差不超过发表值绝对值的 10%（若发表标准误更大，则以标准误为基数）；其余 17 篇中有 13 篇的重估值更接近零。')
        item = next(i for i in FINDINGS['explore']['items'] if i['id'] == 'REPGAP')
        for lang in ('en', 'zh'):
            self.assertIn(escape_text(page.explore_text(item, 'numbers', lang, row['computed'])), self.html[lang])
        # The line follows the loader: other counts give another line, and a mismatch between the fields and the loader stops the build.
        moved = {'papers': 60, 'within': 40, 'other': 20, 'closer': 15}
        with patch.object(estimates, 'reestimate_summary', return_value=moved):
            self.assertEqual(page.finding_line(row, 'en'), 'Of the 60 papers re-estimated (easiest-to-access data first), 40 are within 10% of the larger of the published estimate’s absolute value and its standard error; of the other 20, our estimate is closer to zero in 15.')
            self.assertIn('40 of 60 papers, starting with the easiest data to get', visible(page.render('en')))
        with patch.object(estimates, 'reestimate_summary', return_value={'papers': 60}):
            with self.assertRaises(ValueError):
                page.finding_line(row, 'en')
        # The 17 papers beyond the allowance: 13 closer to zero, 3 with a larger absolute value, 1 with the opposite sign (the numbers behind "13").
        larger = [p for p in others if p not in closer and Decimal(str(p['primary']['ours']['point'])) * Decimal(str(p['primary']['published']['point'])) > 0]
        opposite = [p for p in others if Decimal(str(p['primary']['ours']['point'])) * Decimal(str(p['primary']['published']['point'])) < 0]
        self.assertEqual((len(closer), len(larger), len(opposite)), (13, 3, 1))

    def test_a_placeholder_without_a_loader_entry_stops_the_build(self):
        row = deepcopy(next(r for r in self.rows if r['id'] == 'REPGAP'))
        del row['computed']
        with self.assertRaises(ValueError):
            page.finding_line(row, 'en')

    def test_home_counts_the_finished_rows_of_the_board(self):
        finished = sum(row['status'] == 'finished' for row in page.load_questions())
        self.assertEqual(finished, 4)
        self.assertEqual(home.figures()['finished'], finished)
        self.assertEqual(home.figures()['questions'], 17)
        for lang in ('en', 'zh'):
            section = re.search(r'<section id="questions".*?</section>', self.html[lang], re.S)[0]
            rows = {heading: items.count('<li>') for heading, items in re.findall(r'<h3>([^<]*)</h3><ol class="ex-questions">(.*?)</ol>', section, re.S)}
            self.assertEqual(rows[page.STATUS['finished'][lang == 'zh']], finished)
            self.assertEqual(sum(rows.values()), home.figures()['questions'])

    def test_the_board_file_and_the_findings_file_agree_on_every_row(self):
        stored = json.loads(page.QUESTIONS.read_text(encoding='utf-8'))['rows']
        proposed = FINDINGS['questions']['rows']
        self.assertEqual([(r['n'], r['status'], r['data']) for r in stored], [(r['n'], r['status'], r['data']) for r in proposed])
        counts = {key: sum(r['status'] == key for r in stored) for key in ('finished', 'in_progress', 'waiting')}
        self.assertEqual({key: FINDINGS['questions']['counts'][key] for key in counts}, counts)
        self.assertEqual(tuple(counts.values()), (4, 9, 4))

    def test_layout_rules_of_the_findings_list(self):
        css = page.CSS
        # One column on a phone, two columns (head, sentence) from 700 px, no text under 12 px, no external resource, ZH line height 1.75.
        self.assertIn('.explore-v5 .ex-findings li{display:grid;grid-template-columns:minmax(0,17rem) minmax(0,1fr)', css)
        self.assertIn('@media(max-width:700px){.explore-v5 .ex-questions li{grid-template-columns:minmax(0,1fr);gap:2px 16px}.explore-v5 .ex-findings li{grid-template-columns:minmax(0,1fr)', css)
        self.assertIn(':lang(zh) .explore-v5{line-height:1.75}', css)
        self.assertTrue(all(int(size) >= 12 for size in re.findall(r'font-size:(\d+)px', css)))
        self.assertNotRegex(css, r'@import|url\(|https?:')
        self.assertNotIn('min-width:max-content', css)
        for html in self.html.values():
            self.assertNotIn('<script', html)
            self.assertNotRegex(html, r'(?i)<(?:link|img|iframe)\b')


class StoriesTests(unittest.TestCase):
    """V5.5 (T237): the exploratory stories (l3_stories.json) lead the Explore page; the findings list is now the technical checks."""
    @classmethod
    def setUpClass(cls):
        cls.html = {lang: page.render(lang) for lang in ('en', 'zh')}
        cls.groups, cls.items = page.load_stories()

    def test_eleven_stories_in_three_groups_are_printed_word_for_word_before_the_checks(self):
        self.assertEqual([item['id'] for item in self.items],
                         ['NEED', 'FADE', 'PIONEERS', 'PARTY', 'PROMISE', 'LEADERS', 'TIMING', 'SPEED', 'NEIGHBORS', 'BUNDLES', 'EVALUABLE'])
        for lang, html in self.html.items():
            for key in self.groups:
                section = re.search(r'<section id="stories-%s".*?</section>' % key, html, re.S)[0]
                got = [tuple(unescape(x) for x in row) for row in re.findall(r'<li><h3>([^<]*)</h3><div><p>([^<]*)</p><p class="ex-numbers">([^<]*)</p></div></li>', section)]
                self.assertEqual(got, [(i[lang]['head'], i[lang]['why'], i[lang]['numbers']) for i in self.items if i['group'] == key])
            self.assertLess(html.index('id="stories-when"'), html.index('id="stories-how"'))
            self.assertLess(html.index('id="stories-how"'), html.index('id="stories-which"'))
            self.assertLess(html.index('id="stories-which"'), html.index('id="findings"'))
        self.assertEqual(sum(1 for i in self.items if i['group'] == 'when'), 5)

    def test_every_printed_number_is_audited_and_the_languages_agree(self):
        for item in self.items:
            audit = set(item['audit'])
            self.assertTrue(audit, item['id'])
            self.assertLessEqual(set(page.story_numbers(item['en']['numbers'])), audit, item['id'])
            self.assertEqual(sorted(page.story_numbers(item['en']['numbers'])), sorted(page.story_numbers(item['zh']['numbers'])), item['id'])

    def test_the_exact_numbers_of_the_status_files(self):
        # Spot checks against NEW_ANGLES_A (T232) and NEW_ANGLES_B (T233).
        by_id = {item['id']: item['en']['numbers'] for item in self.items}
        for key, wanted in {'NEED': ('+0.33', '0.20', '3.9 standard deviations', '3.0 and 4.2', '50 studies', '106'),
                            'FADE': ('64 studies', '46 show', '18 that do', '0 faded, 12 held and 6 grew'),
                            'PIONEERS': ('31 of 60', '52%', '+0.03', '0.53 to +0.54'),
                            'PARTY': ('0.75', '1.33 to +1.26', '23 studies', '46 studies'),
                            'LEADERS': ('0.46', '0.29', '375 policies', '34 studies', '1.03 to +1.14'),
                            'TIMING': ('45.2%', '46.1%', '36.3%', '36.0%', '418 policies'),
                            'SPEED': ('0.14', '0.36 to +0.35', '51 groups'),
                            'NEIGHBORS': ('0.05', '0.08 to 0.18', '37 studies', '304 adoptions', '5,562'),
                            'BUNDLES': ('53%', '375', '44%', '8 of 12'),
                            # V5.6 (T255): T249 (n = 15 on pin r2) and the r2 live set / T229.
                            'PROMISE': ('16 laws', '2 moved as promised', '12 showed no detectable change', '2 moved the opposite way'),
                            'EVALUABLE': ('155 designs', '81 ended with an estimate', '74 did not', '48 were run by hand', '97 were run automatically, 33 estimated', '10 more stopped early', 'policy field and adoption era show no clear link to getting an estimate', '24 with a positive sign', '54 of the 74', '35 of the 81')}.items():
            for text in wanted:
                self.assertIn(text, by_id[key], (key, text))

    def test_the_exploratory_label_stays_single_and_nothing_is_called_confirmed(self):
        for lang, html in self.html.items():
            self.assertEqual(unescape(html).count(FINDINGS['label'][lang]), 1)
            self.assertNotRegex(html, page.CONFIRM_WORDS)

    def test_a_story_with_an_unaudited_number_or_a_numbered_headline_stops_the_build(self):
        import copy
        doc = json.loads(page.STORIES.read_text(encoding='utf-8'))
        for mutate in (lambda d: d['items'][0]['en'].update(numbers='Slope 9.99.'), lambda d: d['items'][1]['en'].update(head='64 studies hold'),
                       lambda d: d['items'][2]['zh'].update(numbers='31 项'), lambda d: d['items'][3]['en'].update(why='Confirmed.'),
                       lambda d: d['items'][4]['en'].update(head='')):
            broken = copy.deepcopy(doc)
            mutate(broken)
            with patch.object(page, '_read', return_value=broken):
                page.load_stories.cache_clear()
                try:
                    with self.assertRaises(ValueError):
                        page.load_stories()
                finally:
                    page.load_stories.cache_clear()

    def test_the_re_estimates_headline_says_what_it_shows(self):
        # T227/hub: the published numbers reproduce when re-run with each paper's own method.
        view = {item['id']: item for item in FINDINGS['explore']['items']}['REPGAP']
        self.assertEqual(view['en']['head'], 'With each paper\u2019s own method, most published results reproduce within 10%')
        self.assertIn('自己的方法', view['zh']['head'])


class ExploreCheckTests(unittest.TestCase):
    """check_v5 must fail on a wrong Explore page (V5.1 release): the head, the Findings list, the label, the board and the study."""
    @classmethod
    def setUpClass(cls):
        from scripts.viz_site import check_v5
        cls.check = check_v5
        cls.html = {lang: page.render(lang) for lang in ('en', 'zh')}
        cls.counts = page.load_inputs()[4][:2]

    def issues(self, html, lang='en'):
        return self.check.explore_issues(html, lang, *self.counts)

    def test_the_real_page_passes_and_the_pins_are_the_new_state(self):
        self.assertEqual(self.check.QUESTION_COUNTS, (4, 9, 4))
        for lang, html in self.html.items():
            self.assertEqual(self.issues(html, lang), [])
            self.assertEqual(self.check.explore_counts(html, lang), tuple(str(n) for n in self.counts))
            self.assertEqual(self.check.explore_question_counts(html, lang), ('17', '4'))

    def test_a_wrong_page_is_found(self):
        en, zh = self.html['en'], self.html['zh']
        label = FINDINGS['label']['en']
        study = re.search(r'<section id="study".*?</section>', en, re.S)[0]
        wrong = {
            'old heading': en.replace('<h1>Explore</h1>', '<h1>Party control and local need</h1>'),
            'label twice': en.replace('</header>', '</header><p>%s</p>' % label, 1),
            'label missing': en.replace(label, '', 1),
            'a finding gone': re.sub(r'<li><h3>Estimates for policies that reach many[^<]*</h3>.*?</li>', '', en, flags=re.S),
            'a finding reworded': en.replace('2.84 standard deviations', '2.48 standard deviations'),
            'the re-estimates line typed with other counts': en.replace('36 of 53 papers, starting', '36 of 49 papers, starting'),
            'a deleted finding is back': en.replace('<li><h3>Adopting states', '<li><h3>All-states random timing gives too many false alarms</h3><div><p>x</p><p class="ex-numbers">y</p></div></li><li><h3>Adopting states', 1),
            'finding order': en.replace('<li><h3>With each paper’s own method', '<li><h3>Xx</h3><div><p>y</p><p class="ex-numbers">z</p></div></li><li><h3>Adopting states', 1),
            'confirmed': en.replace('(slope +2.31', '(slope confirmed +2.31'),
            'an id on the page': en.replace('reproduce within 10%', 'reproduce within 10% (REPGAP)'),
            'jargon in a headline': en.replace('States adopt a policy faster when', 'Random timing: states adopt a policy faster when'),
            'a not seen line is back': en.replace('</p></section><section id="questions"', '</p><p class="ex-notseen">Not seen: x</p></section><section id="questions"', 1),
            'how line gone': en.replace(FINDINGS['explore']['how']['en'], '', 1),
            'lead changed': en.replace(FINDINGS['explore']['lead']['en'], 'Four findings so far.', 1),
            'sentence gone': en.replace(FINDINGS['reconcile_35']['en'], 'x'),
            'the study before the findings': en.replace(study, '').replace('<section id="findings"', study + '<section id="findings"', 1),
            'a story reworded': en.replace('(52%), a coin flip', '(53%), a coin flip'),
            'a story gone': re.sub(r'<li><h3>Early and late adopters[^<]*</h3>.*?</li>', '', en, flags=re.S),
            'stories after the checks': en.replace('id="stories-when"', 'id="stories-wh"').replace('<section id="findings"', '<section id="stories-when" class="ex-stories"><h2>x</h2><ol class="ex-findings"></ol></section><section id="findings"', 1),
            'a question moved to waiting': en.replace('<h3>Finished</h3><ol class="ex-questions"><li>', '<h3>Waiting for data</h3><ol class="ex-questions"><li>', 1),
        }
        for name, html in wrong.items():
            self.assertNotEqual(html, en, name)
            self.assertTrue(self.issues(html), name)
        self.assertTrue(self.issues(zh.replace('<h1>探索</h1>', '<h1>党派控制与当地需要</h1>'), 'zh'))
        self.assertTrue(self.issues(zh.replace(FINDINGS['label']['zh'], '', 1), 'zh'))
        self.assertTrue(self.issues(zh.replace('斜率 +2.31', '斜率 +2.31，已确认', 1), 'zh'))
        self.assertTrue(self.issues(en.replace('<li><span class="ex-q">Do adopting states', '<li class="x"><span class="ex-q">Do adopting states', 1)))

    def test_counts_of_the_study_are_read_from_its_own_lead_not_the_page_head(self):
        for lang, html in self.html.items():
            self.assertIsNone(self.check.explore_counts(html.replace('class="ex-lead">On average', 'class="x">On average').replace('class="ex-lead">对 35', 'class="x">对 35'), lang))


def estimates_lead_en():
    """The Estimates page's own statement of the rule the 36 were counted by."""
    from scripts.viz_site import restructure_estimates as estimates
    return estimates.progress_line('en', include_date=False)


def escape_text(text):
    """The text as the page writes it into markup."""
    from html import escape
    return escape(text)


if __name__ == '__main__':
    unittest.main()
