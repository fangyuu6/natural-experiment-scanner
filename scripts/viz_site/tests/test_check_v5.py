"""Catch display regressions in static and dynamically rendered Screening text."""
import json
import os
import shutil
import subprocess
import sys
import unittest
import re
import tempfile
from html import escape
from pathlib import Path
from unittest.mock import patch
from scripts.viz_site import check_v5, legacy_detail, make_motion_data, motion_figure, restructure_estimates, restructure_explore, restructure_home, restructure_screening
motion_figure.MOTION_VERSION = 'v51'     # these tests cover the V5.1 figure; test_motion_v14.py covers the v14 figure (the Home default)
from scripts.viz_site.check_v5 import (confirmed_card_issues, definitions_issues, pin_issues, strip_issues, OLD_PRECISION_FIGURES, PANEL_BLOCKS, PANEL_FIXTURE, QUESTION_COUNTS, REMOVED_CATEGORY, SECOND_CODER_INDEPENDENT, STATUS_WORDS,
                                       credential_issues, empty_disclosures, scroll_margin_issues, explore_counts, explore_issues, explore_question_counts, explore_question_groups, flow_issues, focus_ring_issues, head_texts, home_staircase,
                                       home_staircase_issues, motion_data_issues, motion_figure_issues, motion_region, original_link_issues, screening_counts, screening_display_issues, screening_fetch_issues,
                                       screening_member_issues, screening_panel_issues, standalone)


class ScreeningArtifactChecks(unittest.TestCase):
    def source(self, displayed, static=''):
        return static + '<script type="application/json" id="sc-members">[{"raw":"below_cutoff"}]</script><script>const scText={};\n// SCREENING DISPLAY START\nfunction scDisplayRecord(r){return '+json.dumps(displayed)+';}\n// SCREENING DISPLAY END\n</script>'

    def test_rejects_raw_dynamic_values_and_static_timestamp(self):
        for bad in ('below_cutoff', 'sharp_standard', 'crime_rates', 'date_--', '2026-09-30T20:10:54+00:00'):
            with self.subTest(bad=bad):
                self.assertTrue(screening_display_issues(self.source('<p>'+bad+'</p>')))
                self.assertTrue(screening_display_issues(self.source('plain', '<p>'+bad+'</p>')))

    def test_raw_data_keys_urls_and_attributes_are_not_public_copy(self):
        self.assertEqual(screening_display_issues(self.source('<p title="below the cutoff">Below the cutoff</p><a href="/crime_rates">Source</a><button data-family="minimum_marriage_age">Same family</button>')), [])

    def test_missing_display_renderer_fails_closed(self):
        self.assertTrue(screening_display_issues('<p>September 30, 2026</p>'))


class ScreeningPanelChecks(unittest.TestCase):
    """R28: the candidate panel, the member ids and the two calls are checked on the page's own script, so a change that loses a block, a
    colour, a label, an id or adds a request is found by check_v5 on the built page."""
    def page(self, lang, members=None, panel=lambda code: code, script=lambda code: code):
        strings = json.loads((restructure_screening.HERE / f'i18n/screening.{lang}.json').read_text())
        members = [{'id': 'a', 'api_id': 11}, {'id': 'b', 'api_id': 12}] if members is None else members
        code = script('const scText=' + json.dumps(strings) + ';' + restructure_screening.DISPLAY_JS + panel(legacy_detail.script(lang)) + restructure_screening.JS)
        return '<script type="application/json" id="sc-members">' + json.dumps(members) + '</script><script>' + code + '</script>'

    def test_the_built_scripts_pass_in_both_languages_and_the_fixture_reaches_every_block(self):
        for lang in ('en', 'zh'):
            with self.subTest(lang=lang):
                page = self.page(lang)
                self.assertEqual(screening_panel_issues(page, lang), [])
                self.assertEqual(screening_fetch_issues(page), [])
                self.assertEqual(screening_member_issues(page), [])
        self.assertEqual([block[0] for block in PANEL_BLOCKS][:3], ['Overview', 'Bill Info', 'Text Provenance'])
        self.assertTrue(PANEL_FIXTURE['rd'] and PANEL_FIXTURE['did'] and len(PANEL_FIXTURE['siblings']) > 1)

    def test_a_lost_colour_a_missing_block_a_raw_token_or_the_old_label_is_found(self):
        mutations = {
            'colour': lambda code: code.replace('border-left:3px solid #ea580c', 'border-left:3px solid #000000'),
            'block': lambda code: code.replace('<h4>Literature</h4>', '<h4>Studies</h4>').replace('<h4>文献</h4>', '<h4>研究</h4>'),
            'label': lambda code: code.replace("scText.score+scColon+scoreDisplay", "'Score: '+scoreDisplay"),
            'cleaner': lambda code: code.replace('return scCleanHtml(await buildDetailHtmlLegacy(r, containerId));', 'return await buildDetailHtmlLegacy(r, containerId);'),
            'order': lambda code: code.replace('+provenanceHtml+wwHtml+', '+wwHtml+provenanceHtml+'),
            'strip': lambda code: code.replace("(chapterStripHtml ? '<div style=\"grid-column:1/-1;\">'+chapterStripHtml+'</div>' : '')", "''"),
        }
        for name, mutate in mutations.items():
            for lang in ('en', 'zh'):
                with self.subTest(mutation=name, lang=lang):
                    self.assertTrue(screening_panel_issues(self.page(lang, panel=mutate), lang), name)

    def test_the_two_calls_are_pinned_and_a_third_or_another_kind_of_request_is_found(self):
        self.assertTrue(screening_fetch_issues(self.page('en', script=lambda code: code + 'fetch("/api/policies?page=1");')))
        self.assertTrue(screening_fetch_issues(self.page('en', script=lambda code: code + 'navigator.sendBeacon("/x");')))
        self.assertTrue(screening_fetch_issues(self.page('en', panel=lambda code: code.replace('"/detail"', '"/detail?x=1"'))))
        self.assertTrue(screening_fetch_issues(self.page('en', script=lambda code: code.replace('function scDisplayRecord(r){', 'function scDisplayRecord(r){fetch("/api/policies/"+r.api_id+"/detail");'))))
        self.assertTrue(screening_fetch_issues('<script>const x=1;</script>'))

    def test_every_member_needs_its_own_integer_api_id(self):
        for members in ([{'id': 'a'}], [{'id': 'a', 'api_id': '11'}], [{'id': 'a', 'api_id': 0}], [{'id': 'a', 'api_id': 11}, {'id': 'b', 'api_id': 11}], [], [{'id': 'a', 'api_id': True}]):
            with self.subTest(members=members):
                self.assertTrue(screening_member_issues(self.page('en', members=members)))

    def test_the_rendered_page_passes_all_three(self):
        for lang in ('en', 'zh'):
            page = restructure_screening.render(lang)
            self.assertEqual(screening_member_issues(page) + screening_fetch_issues(page) + screening_panel_issues(page, lang), [])


class RemovedCategoryChecks(unittest.TestCase):
    def test_a_displayed_count_is_found_but_anonymous_labels_and_ids_are_not(self):
        shown = ['Not yet replicated', '461', 'Number matches: 32; Not yet replicated: 461', '461 papers', '全部论文 · 461']
        for part in shown:
            with self.subTest(part=part):
                self.assertEqual(standalone(461, [part]), [part] if '461' in part else [])
        quiet = ['Paper 461', '论文 461 · 数字一致 · 0.2%', 'Paper 461 · Same direction · 3%', 'p461', 'NV-SB461', '1,461', '193.461', '4610', '461,000', '461.5']
        self.assertEqual(standalone(461, quiet), [])
        # R21: the anonymous labels are 1..N, and are still not a count.
        self.assertEqual(standalone(32, ['Paper 32', '论文 32 · 数字一致 · 0.2%', 'Paper 7 · Same direction · 3%']), [])
        self.assertEqual(standalone(32, ['32 selected estimates']), ['32 selected estimates'])

    def test_removed_category_label_is_found_in_either_language(self):
        for text in ('Not yet replicated 461', 'not_yet_replicated', '尚未复现 461', 'NOT YET REPLICATED'):
            self.assertTrue(REMOVED_CATEGORY.search(text), text)
        for text in ('Number matches', 'Same direction', 'Not comparable', '数字一致', 'Figures here come from the revised draft, not yet public.'):
            self.assertFalse(REMOVED_CATEGORY.search(text), text)


class SecondCoderChecks(unittest.TestCase):
    def test_independent_describing_the_second_coder_is_found_in_either_language(self):
        for text in ('a second, independent AI agent agrees', 'A second, independent AI agent', 'a second independent coder', '第二个独立 AI 智能体结果一致'):
            with self.subTest(text=text):
                self.assertTrue(SECOND_CODER_INDEPENDENT.search(text))

    def test_independent_code_and_the_plain_second_coder_are_quiet(self):
        for text in ('AI agents wrote and ran code independently of the authors’ code.', 'reproduce published estimates with independently written code',
                     'a second AI agent agrees', 'A second AI agent', '第二个 AI 智能体结果一致', 'AI 智能体使用作者公开的数据，独立于原作者代码编写并运行分析代码。'):
            with self.subTest(text=text):
                self.assertIsNone(SECOND_CODER_INDEPENDENT.search(text))


class OldPrecisionChecks(unittest.TestCase):
    def test_the_older_runs_precision_and_coverage_are_found_in_either_language(self):
        for text in ('Precision in the research sample: DiD 62.1%; RD 44.1%. Classification coverage: DiD 70/155; RD 150/156.',
                     '研究样本中的精确率：DiD 62.1%；RD 44.1%。分类覆盖率：DiD 70/155；RD 150/156。', 'RD 44.1 %', '70/155', '150/156'):
            with self.subTest(text=text):
                self.assertTrue(OLD_PRECISION_FIGURES.search(text))

    def test_the_new_figures_signed_gaps_and_longer_numbers_are_quiet(self):
        for text in ('Precision in the research sample: DiD 58.7%; RD 42.2%.', '研究样本中的精确率：DiD 58.7%；RD 42.2%。',
                     'Paper 012 · Number matches · −62.1%', '44.18928%', '62.100%', '162.1%', '170/155', '1150/1560',
                     'section 62.1-02-01', '99/113 · 87.6%'):
            with self.subTest(text=text):
                self.assertIsNone(OLD_PRECISION_FIGURES.search(text))


class EmptyDisclosureChecks(unittest.TestCase):
    def test_a_disclosure_with_only_a_summary_is_found_by_id(self):
        # The R17 Estimates shell: a title that opens to nothing.
        self.assertEqual(empty_disclosures('<details class="v5-method" id="method-materials"><summary>Method &amp; materials</summary></details>'),
                         ['method-materials'])
        self.assertEqual(empty_disclosures('<details><summary>One</summary>\n  </details>'), ['(no id)'])
        self.assertEqual(empty_disclosures('<details id="a"><summary><span>Marked</span> up <b>title</b></summary></details>'), ['a'])

    def test_text_an_element_or_a_nested_disclosure_after_the_summary_is_content(self):
        for body in ('words', '<p>one paragraph</p>', '<img src="x.png" alt="">', '<details id="inner"><summary>Inner</summary><p>text</p></details>'):
            with self.subTest(body=body):
                self.assertEqual(empty_disclosures('<details id="outer"><summary>Title</summary>' + body + '</details>'), [])

    def test_an_empty_inner_disclosure_is_reported_and_the_outer_one_is_not(self):
        html = '<details id="outer"><summary>Outer</summary><details id="inner"><summary>Inner</summary></details></details>'
        self.assertEqual(empty_disclosures(html), ['inner'])

    def test_pages_without_disclosures_and_a_bare_anchor_div_are_quiet(self):
        self.assertEqual(empty_disclosures('<article><div id="method-materials"></div></article>'), [])
        self.assertEqual(empty_disclosures(''), [])


class HomeStaircaseChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        figures = restructure_home.figures()
        cls.figures = ((f'{figures["texts"]:,}', f'{figures["candidates"]:,}'), (str(figures['papers']), str(figures['matched'])), (str(figures['questions']), str(figures['finished'])))
        cls.home = {lang: '<main class="home-v5">' + restructure_home.render(lang) + '</main>' for lang in ('en', 'zh')}

    def test_the_rendered_staircase_passes_in_both_languages(self):
        for lang in ('en', 'zh'):
            self.assertEqual(len(home_staircase(self.home[lang])), 3)
            self.assertEqual(home_staircase_issues(self.home[lang], lang, self.figures), [])

    def test_a_changed_figure_a_wrong_link_a_long_line_or_a_missing_step_is_found(self):
        home = self.home['en']
        papers = self.figures[1][0]  # the Estimate step's first figure, read from the snapshot (53 under the V5.1 pin)
        self.assertIn(papers + '</b>', home)
        self.assertTrue(home_staircase_issues(home.replace(papers + '</b>', str(int(papers) + 1) + '</b>'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(home.replace('/viz/en/synthesize.html', '/viz/en/adoption.html'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(home.replace('>Explore</a>', '>Synthesize</a>'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(home.replace('finished</span>', 'finished across all the policies we studied so far</span>'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(home.replace('<li class="nx-st" data-nx-intro="stair-step">', '<li class="other">', 1), 'en', self.figures))

    def test_the_ten_percent_rule_is_allowed_on_the_estimate_step_only(self):
        # M1 of the content check of R25: "within 10%" carries the literal 10 of Estimates' rule; no other number may be added.
        for lang, home in self.home.items():
            self.assertEqual(home_staircase_issues(home, lang, self.figures), [])
        en = self.home['en']
        self.assertIn('within 10%', en)
        self.assertTrue(home_staircase_issues(en.replace('within 10%', 'within 12%'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(en.replace('within 10%', 'within'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(en.replace('17</b> questions', '17</b> questions in 10 fields'), 'en', self.figures))
        self.assertTrue(home_staircase_issues(self.home['zh'].replace('差距不超过 10%', '差距不超过 12%'), 'zh', self.figures))

    def test_the_explore_step_shows_the_questions_counts_and_no_single_study(self):
        # Hub default (O1): no single study stands for L3, so the step shows the list's counts, not the adoption study's 35 and 597.
        self.assertEqual([line for _, _, line in home_staircase(self.home['en'])][2], '17 questions · 4 finished')
        self.assertEqual([line for _, _, line in home_staircase(self.home['zh'])][2], '17 个问题 · 4 个已完成')
        self.assertTrue(home_staircase_issues(self.home['en'].replace('<b>17</b> questions ·', '<b>35</b> policies,').replace('<b>2</b> finished', '<b>597</b> adoptions'), 'en', self.figures))

    def test_a_returning_door_or_feature_card_is_found(self):
        for markup in ('<section class="v5-door" id="home-screen"></section>', '<section class="home-feature"><p>−47.617</p></section>', '<p>SPID</p>'):
            self.assertTrue(home_staircase_issues(self.home['en'] + markup, 'en', self.figures), markup)
        self.assertTrue(home_staircase_issues(self.home['zh'] + '<p>民主党倾向</p>', 'zh', self.figures))

    def test_the_counted_up_copy_of_a_figure_is_not_read_twice(self):
        (_, _, line), = home_staircase(self.home['en'])[:1]
        self.assertEqual(line, '483,414 unique policy texts → 7,781 candidates')

    def test_the_pages_count_lines_are_read_back(self):
        self.assertEqual(screening_counts('<p class="sc-countline">483,414 US policy texts · 7,781 pass five screening rules</p>', 'en'), ('483,414', '7,781'))
        self.assertEqual(screening_counts('<p class="sc-countline">483,414 份美国政策文本 · 7,781 条通过五项筛查规则</p>', 'zh'), ('483,414', '7,781'))
        self.assertIsNone(screening_counts('<p class="sc-countline">changed</p>', 'en'))
        self.assertEqual(explore_counts('<header><h1>x</h1><p>On average across 35 policies (597 adoptions), neither predicted.</p></header>', 'en'), ('35', '597'))
        # The Questions list's counts: all rows, and the rows under the first status heading (Finished / 已完成); each heading is printed once.
        section = ('<section id="questions"><h3>Finished</h3><ol class="ex-questions"><li>a</li><li>b</li></ol>'
                   '<h3>Waiting for data</h3><ol class="ex-questions"><li>c</li></ol><h3>In progress</h3><ol class="ex-questions"><li>d</li></ol></section>')
        self.assertEqual(explore_question_counts(section, 'en'), ('4', '2'))
        self.assertEqual(explore_question_counts(section.replace('Finished', '已完成'), 'zh'), ('4', '2'))
        self.assertEqual(explore_question_groups(section)[1], ('Waiting for data', ['<li>c</li>']))
        self.assertIsNone(explore_question_counts('<main class="explore-v5"></main>', 'en'))
        # A no-break span around the unit does not hide the figure.
        self.assertEqual(explore_counts('<header><h1>x</h1><p>对 35 <span class="nx-seg">项政策</span>（597 次采纳）取平均……</p></header>', 'zh'), ('35', '597'))
        self.assertIsNone(explore_counts('<header><h1>x</h1><p>35 of 648 SPID policies</p></header>', 'en'))


class FlowFigureChecks(unittest.TestCase):
    KNOWN = '483,414 US policy texts · 7,781 pass five screening rules 315,727 125,919 41,496 272 2020–21 at least 3'

    def page(self, figure_text, count=1):
        figure = '<div class="story-flow" data-component="story-flow"><svg><text>' + figure_text + '</text></svg></div><script>var x=1;</script>'
        return '<header class="sc-intro"></header><div class="sc-flow">' + figure * count + '</div><section class="sc-search"></section>'

    def test_a_figure_with_published_counts_only_passes(self):
        self.assertEqual(flow_issues(self.page('483,414 policy texts 315,727 125,919 41,496 272 7,781 candidates five rules 2020–21 every score at least 3'), self.KNOWN), [])

    def test_an_unpublished_gate_count_or_a_rounded_count_is_found(self):
        self.assertTrue(flow_issues(self.page('100,311 94,505 7,781'), self.KNOWN))
        self.assertTrue(flow_issues(self.page('483k policy texts 7.8k candidates'), self.KNOWN))
        self.assertTrue(flow_issues(self.page('48.3万 份政策文本'), self.KNOWN))

    def test_zero_or_two_figures_or_a_figure_outside_the_intro_and_list_are_found(self):
        self.assertTrue(flow_issues('<header class="sc-intro"></header><section class="sc-search"></section>', self.KNOWN))
        self.assertTrue(flow_issues(self.page('7,781', count=2), self.KNOWN))
        misplaced = '<section class="sc-search"></section>' + self.page('7,781')
        self.assertTrue(flow_issues(misplaced, self.KNOWN))


class ExploreChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = {lang: restructure_explore.render(lang) for lang in ('en', 'zh')}
        cls.counts = restructure_explore.load_inputs()[4][:2]

    def test_the_rendered_page_passes_in_both_languages(self):
        for lang in ('en', 'zh'):
            self.assertEqual(explore_issues(self.page[lang], lang, *self.counts), [])
            self.assertEqual(explore_counts(self.page[lang], lang), tuple(str(n) for n in self.counts))

    def test_the_list_holds_three_status_words_in_the_stated_counts(self):
        # R26 (content check M9), V5.1 release: 2 Finished, 8 In progress, 7 Waiting for data (F1: row 17, Stan2, is in progress; F6, Liebman M2: rows 6, 12 and 13 have no answer on the page).
        self.assertEqual(QUESTION_COUNTS, (4, 9, 4))
        self.assertEqual(STATUS_WORDS, {'en': ('Finished', 'In progress', 'Waiting for data'), 'zh': ('已完成', '进行中', '等待数据')})
        for lang in ('en', 'zh'):
            self.assertEqual(explore_question_counts(self.page[lang], lang), ('17', '4'))
        page = self.page['en']
        self.assertTrue(explore_issues(page.replace('<h3>Waiting for data</h3>', '<h3>In progress</h3>', 1), 'en', *self.counts))
        self.assertTrue(explore_issues(page.replace('<h3>In progress</h3>', '<h3>Waiting for data</h3>', 1), 'en', *self.counts))
        self.assertTrue(explore_issues(self.page['zh'].replace('<h3>等待数据</h3>', '<h3>进行中</h3>', 1), 'zh', *self.counts))
        # Each status is a heading, printed once: a status word on a row is found.
        self.assertTrue(explore_issues(page.replace('<li><span class="ex-q">Do published', '<li><span class="ex-status">Waiting for data</span><span class="ex-q">Do published', 1), 'en', *self.counts))

    def test_the_title_and_the_description_are_read_for_dropped_wording(self):
        # M3 of the content check of R25: the search snippet and the link preview must not bring back what the page dropped.
        head = '<head><title>Explore · Natural Experiment Scanner</title><meta name="description" content="%s"></head>'
        clean = 'Does party control or local need predict which states adopt a policy? Results for 35 policies, and the questions we are working on.'
        self.assertEqual(explore_issues(head % clean + self.page['en'], 'en', *self.counts), [])
        for bad in ('Patterns in policy adoption: a study of 35 SPID policies, with analysis decided in advance and time-stamped.', 'A study of 35 policies, time-stamped.', 'Odds of 35 policies, pooled.'):
            self.assertTrue(explore_issues(head % bad + self.page['en'], 'en', *self.counts), bad)
        zh_head = '<head><title>探索</title><meta name="description" content="%s"></head>'
        self.assertEqual(explore_issues(zh_head % '党派控制或当地需要，能预测哪些州采纳政策吗？35 项政策的结果，以及我们正在研究的问题。' + self.page['zh'], 'zh', *self.counts), [])
        for bad in ('政策采纳规律：35 项 SPID 政策研究，分析方法事先确定并记录时间。', '分析方法事先确定并注明时间。'):
            self.assertTrue(explore_issues(zh_head % bad + self.page['zh'], 'zh', *self.counts), bad)
        self.assertEqual(head_texts('<title>A</title><meta property="og:description" content="B &amp; C"><meta content="D" name="description"><meta name="viewport" content="E">'), ['A', 'B & C', 'D'])

    def test_dropped_jargon_the_selector_a_missing_question_or_a_result_in_the_list_is_found(self):
        page = self.page['en']
        self.assertTrue(explore_issues(page.replace('Local need', 'Local need (tilt)', 1), 'en', *self.counts))
        self.assertTrue(explore_issues(page.replace('<details class="ex-methods" id="study-methods">', '<select></select><details class="ex-methods" id="study-methods">'), 'en', *self.counts))
        self.assertTrue(explore_issues(page.replace('<li><span class="ex-q">Do published', '<li class="x"><span class="ex-q">Do published', 1), 'en', *self.counts))
        self.assertTrue(explore_issues(page.replace('12 checked adoption histories', '257 analyses: odds ratio 1.2 [0.9, 1.5]', 1), 'en', *self.counts))
        self.assertTrue(explore_issues(page.replace('<a href="#h1">', '<a href="#h0">'), 'en', *self.counts))
        self.assertTrue(explore_issues(page.replace('<h3>Finished</h3>', '<h3>In progress</h3>', 1), 'en', *self.counts))
        self.assertTrue(explore_issues(self.page['zh'].replace('当地需要', '当地需要（对数优势）', 1), 'zh', *self.counts))


class EstimatesPageChecks(unittest.TestCase):
    """V5.1: the confirmed cards, the gap strip, the definitions line and the pin are checked on the built Estimates page."""
    @classmethod
    def setUpClass(cls):
        cls.snapshot = restructure_estimates.load_snapshot()
        cls.page = {lang: restructure_estimates.render(lang) for lang in ('en', 'zh')}

    def issues(self, lang, page=None):
        page = self.page[lang] if page is None else page
        return confirmed_card_issues(page, self.snapshot, lang) + strip_issues(page, self.snapshot) + definitions_issues(page, self.snapshot, lang)

    def test_the_rendered_page_passes_in_both_languages(self):
        for lang in ('en', 'zh'):
            self.assertEqual(self.issues(lang), [])

    def test_a_changed_card_row_a_lost_card_a_lost_doi_or_a_note_that_does_not_belong_is_found(self):
        en = self.page['en']
        self.assertTrue(confirmed_card_issues(en.replace('Table 3, panel B (Selective), column (5)', 'Table 6a, panel B, column (5)'), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(en.replace('<dt>Sample</dt><dd>ACT states', '<dt>Sample</dt><dd>All states', 1), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(en.replace('10.1162/rest_a_00600', '10.1162/rest_a_00601'), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(en.replace('<summary>Learning from the Test: Raising Selective College Enrollment by Providing Information <span>', '<summary>Learning from the Test <span>'), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(en.replace(' (calculated from reported SE)', ''), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(en.replace('2.0 / 47.6<', '2.0 / 47.6 (calculated from reported SE)<'), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(en.replace('<details class="v5-result" id="v5-result-3">', '<details class="v5-other" id="v5-result-3">'), self.snapshot, 'en'))
        self.assertTrue(confirmed_card_issues(self.page['zh'].replace('第 (5) 列', '第 (6) 列'), self.snapshot, 'zh'))
        self.assertTrue(confirmed_card_issues(self.page['zh'].replace('（根据报告的标准误计算）', ''), self.snapshot, 'zh'))
        # A card whose description file entry is gone is found as well.
        entries = {k: v for k, v in restructure_estimates.reviewed_descriptions().items() if k != 'goodman2016-c6f7ffbe'}
        with patch.object(restructure_estimates, 'reviewed_descriptions', return_value=entries):
            self.assertTrue(confirmed_card_issues(en, self.snapshot, 'en'))

    def test_a_strip_shorter_than_its_lanes_or_with_a_lost_lane_is_found(self):
        en = self.page['en']
        height = re.search(r'<div class="v5-strip"[^>]*style="height:(\d+)px"', en)[1]
        self.assertEqual(int(height), 10 * 45 + 16)
        self.assertTrue(strip_issues(en.replace('style="height:' + height + 'px"', 'style="height:304px"'), self.snapshot))
        self.assertTrue(strip_issues(en.replace(' style="height:' + height + 'px"', ''), self.snapshot))
        self.assertTrue(strip_issues(en.replace('<span class="v5-strip-lane"', '<span class="v5-lane"', 1), self.snapshot))
        self.assertTrue(strip_issues(en.replace('(45 plotted)', '(36 plotted)'), self.snapshot))
        self.assertTrue(strip_issues(self.page['zh'].replace('（图示 45 项）', '（图示 36 项）'), self.snapshot))

    def test_the_definitions_line_must_define_the_legends_groups_beyond_10_percent(self):
        en = self.page['en']
        self.assertEqual(definitions_issues(en, self.snapshot, 'en'), [])
        self.assertEqual(definitions_issues(self.page['zh'], self.snapshot, 'zh'), [])
        # F6 (Finkelstein MF1): the old name of the third group, or a name that is not in the legend, is found.
        self.assertTrue(definitions_issues(en.replace("Opposite sign: beyond", "Doesn’t match: beyond"), self.snapshot, 'en'))
        self.assertTrue(definitions_issues(en.replace('Not comparable: no published', 'Cannot compare: no published'), self.snapshot, 'en'))
        self.assertTrue(definitions_issues(self.page['zh'].replace('符号相反：', '对不上：'), self.snapshot, 'zh'))
        self.assertTrue(definitions_issues('<p class="v5-small">other</p>', self.snapshot, 'en'))

    def test_the_definitions_line_gives_the_two_groups_beyond_10_percent(self):
        # F6 (Finkelstein MF1): Not comparable = 8 without a published standard error (not plotted); Opposite sign = 1; counted from the snapshot.
        en, zh = self.page['en'], self.page['zh']
        self.assertEqual(restructure_estimates.third_group_split(self.snapshot), (8, 1, 0))
        self.assertIn("Not comparable: no published standard error to compare intervals (not plotted). Opposite sign: beyond 10%, opposite sign.", en)
        self.assertIn('不可比：发表值无标准误、无法比较区间（不绘制）。符号相反：差距超出 10%，符号相反。', zh)
        counts = restructure_estimates.public_counts(self.snapshot)
        self.assertEqual(restructure_estimates.third_group_split(self.snapshot)[:2], (counts['not_comparable'], counts['opposite_sign']))
        # A line with a lost clause or the old wording is found.
        self.assertTrue(definitions_issues(en.replace(' Opposite sign: beyond 10%, opposite sign.', ''), self.snapshot, 'en'))
        self.assertTrue(definitions_issues(en.replace('(not plotted).', '(not plotted), or not shown to overlap in the same direction.'), self.snapshot, 'en'))
        self.assertTrue(definitions_issues(zh.replace('符号相反：差距超出 10%，符号相反。', ''), self.snapshot, 'zh'))
        self.assertTrue(definitions_issues(zh.replace('不可比：发表值无标准误', '不可比：差距超出 10%，发表值有标准误'), self.snapshot, 'zh'))

    def test_a_build_from_another_snapshot_than_the_pin_is_found(self):
        pin = restructure_estimates.read_pin_file()
        receipt = {'snapshot_path': pin['snapshot_path'], 'snapshot_sha256': pin['snapshot_sha256']}
        self.assertEqual(pin_issues(receipt), [])
        self.assertEqual(len(pin_issues(dict(receipt, snapshot_sha256='0' * 64))), 1)
        self.assertEqual(len(pin_issues(dict(receipt, snapshot_path='data/replication/snapshot_v5/snapshot_20261013T102801529434Z.json'))), 1)
        self.assertEqual(len(pin_issues({})), 2)
        # Without a pin file there is nothing to compare with (the earlier behaviour).
        with patch.object(restructure_estimates, 'PIN_FILE', Path(tempfile.gettempdir()) / 'no-such-snapshot-pin.json'):
            self.assertEqual(pin_issues({}), [])



class HomeFigureChecks(unittest.TestCase):
    """V5.1: the animated figure on Home is checked on the built Home page; a stripped finding label, a changed strip figure, a printed count of
    maps or policies, a lost control or an external reference in a copy of the page is found."""
    @classmethod
    def setUpClass(cls):
        cls.snapshot = restructure_estimates.load_snapshot()
        cls.doc = json.loads(check_v5.FINDINGS_FILE.read_text(encoding='utf-8'))
        cls.rows = sorted(cls.doc['findings'], key=lambda row: row['order'])
        # The adoption map partial sits above the figure on the real page; the figure only needs its anchor here.
        cls.home = {lang: '<div class="home-thesis"></div><div id="sm-root"></div>' + restructure_home.render(lang) for lang in ('en', 'zh')}
        figures = restructure_home.figures()
        cls.counts = {lang: (f'{figures["texts"]:,}', f'{figures["candidates"]:,}') for lang in ('en', 'zh')}

    def issues(self, lang, home=None, counts=None):
        return motion_figure_issues(self.home[lang] if home is None else home, lang, self.counts[lang] if counts is None else counts, self.snapshot)

    def test_the_rendered_page_passes_in_both_languages(self):
        for lang in ('en', 'zh'):
            self.assertEqual(self.issues(lang), [], lang)
            self.assertEqual(self.home[lang].count('id="motion-figure"'), 1)
            self.assertEqual(self.home[lang].count('data-home-block='), 1)      # the whole page has two blocks; the figure is neither
        self.assertGreaterEqual(len(self.rows), 6)

    def test_a_stripped_reworded_or_reordered_finding_label_or_a_wrong_label_count_is_found(self):
        for lang in ('en', 'zh'):
            shorts = [row['short'][lang] for row in self.rows]
            home = self.home[lang]
            self.assertIn('<li>' + escape(shorts[0]) + '</li>', home)
            self.assertTrue(self.issues(lang, home.replace('<li>' + escape(shorts[0]) + '</li>', '', 1)), lang + ': label stripped')
            self.assertTrue(self.issues(lang, home.replace('<li>' + escape(shorts[1]) + '</li>', '<li>' + escape(shorts[1]) + ' (confirmed)</li>', 1)), lang + ': reworded')
            swapped = home.replace('<li>' + escape(shorts[0]) + '</li><li>' + escape(shorts[1]) + '</li>', '<li>' + escape(shorts[1]) + '</li><li>' + escape(shorts[0]) + '</li>', 1)
            self.assertNotEqual(swapped, home)
            self.assertTrue(self.issues(lang, swapped), lang + ': reordered')
            label = self.doc.get('label_home', self.doc['label'])[lang]      # Home prints no ranges: label_home, else the shared label
            self.assertTrue(self.issues(lang, home.replace('<p class="mf-tag">' + escape(label) + '</p>', '<p class="mf-tag"></p>', 1)), lang + ': exploratory label gone')
            self.assertTrue(self.issues(lang, home.replace('<p class="mf-tag">' + escape(label) + '</p>', '<p class="mf-tag">' + escape(label) + '</p><p>' + escape(label) + '</p>', 1)), lang + ': label twice')
            self.assertTrue(self.issues(lang, home.replace('<ol class="mf-strip">', '<ol class="mf-strip"><li class="mf-cell" data-lv="9">x</li>', 1)), lang + ': extra strip cell')
            if 'label_home' in self.doc and self.doc['label_home'][lang] != self.doc['label'][lang]:      # Home must not carry Explore's label about ranges
                self.assertTrue(self.issues(lang, home.replace('<p class="mf-tag">' + escape(label) + '</p>', '<p class="mf-tag">' + escape(self.doc['label'][lang]) + '</p>', 1)), lang + ': the Explore label on Home')
        # Fewer than six findings in the file, or one that is not exploratory.
        with tempfile.TemporaryDirectory() as folder:
            five = dict(self.doc, findings=self.doc['findings'][:5])
            confirmed = dict(self.doc, findings=[dict(self.doc['findings'][0], status='confirmed')] + self.doc['findings'][1:])
            for name, doc in (('five', five), ('confirmed', confirmed)):
                path = Path(folder) / (name + '.json')
                path.write_text(json.dumps(doc), encoding='utf-8')
                with patch.object(check_v5, 'FINDINGS_FILE', path):
                    self.assertTrue(self.issues('en'), name)

    def test_a_number_in_the_names_only_strip_and_canvas_data_that_is_not_the_loaders_are_found(self):
        en = self.home['en']
        papers = str(restructure_estimates.attempted(self.snapshot))
        # Art gate (Q2a): STRIP_NUMBERS is off, the strip names the levels, the staircase below carries the numbers.
        self.assertIs(motion_figure.STRIP_NUMBERS, False)
        self.assertNotIn('mf-count', en.split('<script')[0].split('</style>')[1])
        self.assertTrue(self.issues('en', en.replace('<p class="mf-lab">Estimates</p>', '<p class="mf-lab">Estimates</p><p class="mf-line">%s papers re-estimated</p>' % papers, 1)))
        self.assertTrue(self.issues('zh', self.home['zh'].replace('<p class="mf-lab">估计</p>', '<p class="mf-lab">估计 53</p>', 1)))
        # The canvas data and the shipped map data are the loaders' and the committed file's.
        self.assertTrue(self.issues('en', en.replace('"papers":%s,' % papers, '"papers":%d,' % (int(papers) + 1), 1)))
        data = re.search(r'<script type="application/json" id="mf-data">(.*?)</script>', en, re.S)
        changed = json.loads(data[1])
        changed['counts']['texts'] += 1
        self.assertTrue(self.issues('en', en.replace(data[1], motion_figure._json_text(changed), 1)))

    def test_a_strip_figure_that_is_not_the_loaders_is_found_when_the_numbers_are_switched_on(self):
        with patch.object(motion_figure, 'STRIP_NUMBERS', True):
            home = {lang: '<div class="home-thesis"></div><div id="sm-root"></div>' + restructure_home.render(lang) for lang in ('en', 'zh')}
            en = home['en']
            self.assertEqual([self.issues(lang, home[lang]) for lang in ('en', 'zh')], [[], []])
            papers = str(restructure_estimates.attempted(self.snapshot))
            self.assertIn('<span class="mf-count">' + papers + '</span>', en)
            self.assertTrue(self.issues('en', en.replace('<span class="mf-count">' + papers + '</span>', '<span class="mf-count">' + str(int(papers) + 1) + '</span>')))
            public = restructure_estimates.public_counts(self.snapshot)
            within = '<b class="mf-val">%d</b> within 10%%' % public['number_matches']
            self.assertIn(within, en)
            self.assertTrue(self.issues('en', en.replace(within, '<b class="mf-val">%d</b> within 10%%' % (public['number_matches'] - 1), 1)))
            # No bare count of a result group in the strip, each number next to its unit in the staircase's words.
            self.assertTrue(self.issues('en', en.replace(within + '</span>', within + ', <b class="mf-val">%d</b></span>' % public['same_direction'], 1)))
            self.assertTrue(self.issues('en', en.replace('<span class="mf-seg"><b class="mf-val">483,414</b> unique policy texts</span> → <span class="mf-seg"><b class="mf-val">7,781</b> candidates</span>',
                                                         '<b class="mf-val">483,414 → 7,781</b> policy texts → candidates', 1)))
            self.assertTrue(self.issues('zh', home['zh'].replace('篇差距不超过 10%', '篇数字一致', 1)))
            self.assertTrue(self.issues('en', en.replace('<b class="mf-val">%d</b> findings' % len(self.rows), '<b class="mf-val">%d</b> findings' % (len(self.rows) + 1), 1)))
            self.assertTrue(self.issues('en', en.replace('findings</p></li></ol>', 'maps</p></li></ol>', 1)))
            self.assertTrue(self.issues('zh', home['zh'].replace('项发现', '项结果')))
            # The strip must print the figures Screening prints.
            self.assertTrue(self.issues('en', home=en, counts=('483,414', '7,780')))
            self.assertTrue(self.issues('zh', home=home['zh'], counts=('483,415', '7,781')))

    def test_the_nurse_map_must_use_the_home_cards_own_bands_labels_and_colours(self):
        en = self.home['en']
        card = ('<script type="application/json" id="sm-d">{"p":{"nurse_licensure_compact":{"b":[[2016,2019],[2020,2023],[2024,2024]]}}}</script>'
                '<span class="sm-rk" aria-hidden="true"><i>2016–19</i><i>2020–23</i><i>2024</i></span>'
                '<style>.sm .r1{background:#a8c5f9}.sm .r2{background:#5283e0}.sm .r3{background:#1e4798}</style>')
        self.assertEqual(self.issues('en', card + en), [])
        self.assertTrue(self.issues('en', card.replace('[2016,2019],[2020,2023]', '[2016,2017],[2018,2023]') + en))
        self.assertTrue(self.issues('en', card.replace('<i>2016–19</i>', '<i>2016–17</i>') + en))
        self.assertTrue(self.issues('en', card.replace('#5283e0', '#7ba4f0') + en))

    def test_the_strip_names_the_levels_as_the_navigation_does_and_nothing_says_patterns(self):
        # Round two: Screening / Estimates / Explore (中文: 筛查 / 估计 / 探索), as the navigation and the staircase say.
        en, zh = self.home['en'], self.home['zh']
        self.assertIn('<p class="mf-lab">Explore</p>', en)
        self.assertIn('<p class="mf-lab">探索</p>', zh)
        for name, markup in (('third label', en.replace('<p class="mf-lab">Explore</p>', '<p class="mf-lab">Patterns</p>', 1)),
                             ('second label', en.replace('<p class="mf-lab">Estimates</p>', '<p class="mf-lab">Estimate</p>', 1)),
                             ('canvas text', en.replace('Explore: a wall of state policy adoption maps', 'Patterns: a wall of state policy adoption maps', 1)),
                             ('section label', en.replace('aria-label="Screening, Estimates and Explore"', 'aria-label="Screening, estimates and patterns"', 1)),
                             ('canvas string', en.replace('"wallCap":["State policy adoption maps"', '"wallCap":["Patterns of state policy adoption maps"', 1))):
            with self.subTest(change=name):
                self.assertNotEqual(markup, en)
                self.assertTrue(self.issues('en', markup))
        for name, markup in (('third label', zh.replace('<p class="mf-lab">探索</p>', '<p class="mf-lab">跨政策规律</p>', 1)),
                             ('canvas text', zh.replace('探索：一面州政策采纳地图墙', '跨政策规律：一面州政策采纳地图墙', 1))):
            with self.subTest(change='zh ' + name):
                self.assertNotEqual(markup, zh)
                self.assertTrue(self.issues('zh', markup))

    def test_a_printed_count_of_maps_or_policies_is_found(self):
        motion = json.loads(check_v5.MOTION_DATA_FILE.read_text(encoding='utf-8'))
        section_end = '</div></figure></section>'
        for lang, extras in (('en', ('<p>732 maps</p>', '<p>724 state policies</p>', '<p>lists 648 state policies</p>', '<p>12 policies</p>')),
                             ('zh', ('<p>732 张地图</p>', '<p>648 项州政策</p>', '<p>12 项政策</p>'))):
            home = self.home[lang]
            for extra in extras:
                markup = home.replace('<ol class="mf-strip">', extra + '<ol class="mf-strip">', 1)
                self.assertTrue(self.issues(lang, markup), lang + ' ' + extra)
        self.assertEqual((motion['tiles']['n'], motion['tiles']['spid']), (732, 724))
        # A canvas string with a count is found as well; "1,234 policy texts" is Screening's own wording and stays quiet.
        en = self.home['en']
        self.assertTrue(self.issues('en', en.replace('"wallCap":["State policy adoption maps"', '"wallCap":["732 maps"', 1)))
        self.assertEqual(self.issues('en', en.replace('<ol class="mf-strip">', '<p>483,414 policy texts</p><ol class="mf-strip">', 1)), [])

    def test_a_lost_control_text_alternative_or_external_reference_is_found(self):
        en = self.home['en']
        replay = re.search(r'<button type="button" class="mf-btn" data-act="replay".*?</button>', en, re.S)[0]
        self.assertTrue(self.issues('en', en.replace(replay, '', 1)))
        self.assertTrue(self.issues('en', en.replace(' aria-pressed="false"', '', 1)))
        self.assertTrue(self.issues('en', en.replace('data-act="toggle"', 'data-act="pause"', 1)))
        self.assertTrue(self.issues('en', en.replace('aria-label="Replay" ', '', 1)))
        self.assertTrue(self.issues('en', en.replace('width:44px;height:44px', 'width:32px;height:32px')))
        self.assertTrue(self.issues('en', en.replace('<canvas class="mf-canvas" role="img"', '<canvas class="mf-canvas"', 1)))
        self.assertTrue(self.issues('en', en.replace(' aria-describedby="mf-findings"', '', 1)))
        self.assertTrue(self.issues('en', re.sub(r'(<canvas [^>]*aria-label=")[^"]*"', r'\1Animation"', en, count=1)))
        for extra in ('<img src="a.png" alt="">', '<link rel="stylesheet" href="a.css">', '<a href="https://example.org/x">x</a>'):
            self.assertTrue(self.issues('en', en.replace('<ol class="mf-strip">', extra + '<ol class="mf-strip">', 1)), extra)
        self.assertTrue(self.issues('en', en.replace('</script><section class="nx-sec nx-ladder"', 'fetch("/x");</script><section class="nx-sec nx-ladder"', 1)))
        self.assertTrue(self.issues('en', en.replace('<ol class="mf-strip">', '<p>中文</p><ol class="mf-strip">', 1)))
        self.assertEqual([issue for issue in self.issues('zh') if 'Chinese' in issue], [])

    def test_the_place_of_the_figure_is_the_gap_between_the_map_and_the_staircase(self):
        en = self.home['en']
        self.assertTrue(motion_region(en))
        self.assertTrue(self.issues('en', en.replace('<div id="sm-root"></div>', '', 1)))
        self.assertTrue(self.issues('en', en.replace('<div id="sm-root"></div>', '', 1) + '<div id="sm-root"></div>'))
        self.assertTrue(self.issues('en', en.replace('<section class="mf-sec"', '<section data-home-block="3" class="mf-sec"', 1)))
        self.assertTrue(self.issues('en', en.replace('id="motion-figure"', 'id="motion-figure" data-x="1"', 1).replace('<ol class="mf-strip">', '<i id="motion-figure"></i><ol class="mf-strip">', 1)))
        self.assertTrue(self.issues('en', en.replace('class="nx-sec nx-ladder"', 'class="nx-sec"', 1)))
        with patch.object(motion_figure, 'MOTION_PLACEMENT', 'none'):
            without = '<div id="sm-root"></div>' + restructure_home.render('en')
        self.assertEqual(self.issues('en', without)[:1], ['the Home page needs exactly one animated figure (id="motion-figure"), it has 0'])

    def test_the_committed_motion_data_must_be_current(self):
        self.assertEqual(motion_data_issues(), [])
        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / 'motion_data.json'
            stale.write_text(check_v5.MOTION_DATA_FILE.read_text(encoding='utf-8').replace('"texts":', '"texts": ', 1), encoding='utf-8')
            with patch.object(make_motion_data, 'OUT', stale):
                self.assertTrue(motion_data_issues())
            with patch.object(make_motion_data, 'OUT', Path(folder) / 'missing.json'):
                self.assertTrue(motion_data_issues())


class OriginalTextLinkChecks(unittest.TestCase):
    """V5.1: the Original text link of a Screening candidate is its document's own address minus credential parameters and the fragment. The
    page's own display code is run on every embedded member; an altered address or display code in a copy of the page is found."""
    @classmethod
    def setUpClass(cls):
        cls.page = {lang: restructure_screening.render(lang) for lang in ('en', 'zh')}

    @staticmethod
    def members(page):
        return json.loads(re.search(r'<script type="application/json" id="sc-members">(.*?)</script>', page, re.S)[1])

    @staticmethod
    def embedded(page, rows):
        payload = json.dumps(rows, ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        return re.sub(r'(<script type="application/json" id="sc-members">).*?(</script>)', lambda m: m[1] + payload + m[2], page, count=1, flags=re.S)

    def altered(self, lang, change, index=0):
        rows = self.members(self.page[lang])
        rows[index]['url'] = change(rows[index]['url'])
        return self.embedded(self.page[lang], rows)

    def test_the_rendered_pages_pass_and_their_links_are_the_members_addresses(self):
        for lang in ('en', 'zh'):
            self.assertEqual(original_link_issues(self.page[lang]), [], lang)
            rows = self.members(self.page[lang])
            self.assertGreater(sum(bool(row['url']) for row in rows), 7000)
            self.assertEqual(self.embedded(self.page[lang], rows), self.page[lang], 'the test re-embeds members exactly as the page does')

    def test_a_credential_parameter_a_fragment_a_userinfo_part_or_another_scheme_in_one_link_is_found(self):
        base = next(row['url'] for row in self.members(self.page['en']) if row['url'])
        index = next(i for i, row in enumerate(self.members(self.page['en'])) if row['url'])
        for name, change in (('api_key', lambda url: url + ('&' if '?' in url else '?') + 'api_key=0123456789abcdef'),
                             ('short api_key', lambda url: url + ('&' if '?' in url else '?') + 'api_key=abc'),
                             ('token', lambda url: url + ('&' if '?' in url else '?') + 'token=abc'),
                             ('fragment', lambda url: url + '#section-2'),
                             ('userinfo', lambda url: url.replace('://', '://user:pw@', 1)),
                             ('ftp', lambda url: 'ftp' + url[url.index('://'):]),
                             ('script', lambda url: 'javascript:alert(1)')):
            for lang in ('en', 'zh'):
                with self.subTest(change=name, lang=lang):
                    issues = original_link_issues(self.altered(lang, change, index))
                    self.assertTrue(issues)
                    self.assertNotIn('0123456789abcdef', ' '.join(issues))
                    self.assertNotIn(base, ' '.join(issues))      # a count, never an address

    def test_a_display_code_that_shows_another_address_or_no_link_is_found(self):
        page = self.page['en']
        link = '<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(scText.original)} ↗</a>'
        self.assertEqual(page.count(link), 1)
        for name, mutated in (('fragment added', page.replace(link, link.replace('${esc(r.url)}', '${esc(r.url)}#top'))),
                              ('key added', page.replace(link, link.replace('${esc(r.url)}', '${esc(r.url)}&amp;api_key=0123456789abcdef'))),
                              ('link dropped', page.replace('${r.url?`' + link + '`:\'\'}', '')),
                              ('link twice', page.replace(link, link + link)),
                              ('label changed', page.replace(link, link.replace('${esc(scText.original)}', 'Source')))):
            with self.subTest(change=name):
                self.assertNotEqual(mutated, page)
                self.assertTrue(original_link_issues(mutated))
        self.assertTrue(original_link_issues('<p>no members</p>'))


class ApiKeyAndFocusRingChecks(unittest.TestCase):
    """V5.1: no API key parameter in any file of the output, and the inset focus rings of the Groups, Method and All records pages in both
    languages. Each check passes on pages as the build renders them and fails on a corrupted copy."""
    @classmethod
    def setUpClass(cls):
        from scripts.zh_pages.tests import test_focus_rings
        cls.focus = test_focus_rings
        cls.shipped = {}
        for page, (en_key, zh_key) in check_v5.SHIPPED_FOCUS_PAGES.items():
            built = test_focus_rings.render(page)
            cls.shipped[en_key], cls.shipped[zh_key] = built['en'], built['zh']

    def test_no_api_key_in_any_file_of_a_folder(self):
        key = 'api_key=0123456789abcdef'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'viz' / 'en').mkdir(parents=True)
            page = restructure_home.render('en')
            (root / 'policy_db.html').write_text(page, encoding='utf-8')
            (root / 'build_v5.json').write_text('{"progress_en": "53 papers"}', encoding='utf-8')
            self.assertEqual(credential_issues(root), [])
            # Short values, an empty value and the words "API key" are text, not a key parameter.
            (root / 'notes.json').write_text('{"a": "api_key=abc", "b": "set the api_key= value", "c": "API key"}', encoding='utf-8')
            self.assertEqual(credential_issues(root), [])
            (root / 'policy_db.html').write_text(page.replace('</body>', '<a href="https://x.org/a?' + key + '">x</a></body>') if '</body>' in page else page + '<a href="https://x.org/a?' + key + '">x</a>', encoding='utf-8')
            issues = credential_issues(root)
            self.assertEqual(len(issues), 1)
            self.assertTrue(issues[0].startswith('policy_db.html: 1 API key'))
            self.assertNotIn('0123456789abcdef', issues[0])
            (root / 'policy_db.html').write_text(page, encoding='utf-8')
            for spelling in ('api_key%3D0123456789abcdef', 'api-key=0123456789abcdef', 'API_KEY=0123456789abcdef', 'api_key&#61;0123456789abcdef', 'api_key\\u003d0123456789abcdef'):
                (root / 'viz' / 'en' / 'other.json').write_text('{"u": "' + spelling + '"}', encoding='utf-8')
                self.assertEqual([issue.split(':')[0] for issue in credential_issues(root)], ['viz/en/other.json'], spelling)
            # A file that is not a page counts too.
            (root / 'viz' / 'en' / 'other.json').write_text('[]', encoding='utf-8')
            (root / 'build_v5.json').write_text('{"url": "https://x.org/?' + key + '"}', encoding='utf-8')
            self.assertEqual([issue.split(':')[0] for issue in credential_issues(root)], ['build_v5.json'])

    def test_the_built_pages_carry_the_three_inset_rings_in_both_languages(self):
        self.assertEqual(focus_ring_issues(self.shipped), [])
        self.assertEqual(len(self.shipped), 6)

    def in_rule(self, text, selector, change):
        """The page with the declarations of the rule for `selector` changed by `change` (the rule itself stays)."""
        pattern = re.compile(r'(' + re.escape(selector) + r'\s*\{)([^}]*)(\})')
        self.assertEqual(len(pattern.findall(text)), 1, selector)
        return pattern.sub(lambda m: m[1] + change(m[2]) + m[3], text, count=1)

    def test_a_removed_moved_or_turned_off_ring_is_found_in_either_language(self):
        # page -> (the rule's selector as shipped, a more general selector a later rule could switch the ring off with)
        cases = {'policy_groups': ('.group-header:focus-visible', 'button:focus-visible'),
                 'documentation': ('.sidebar a:focus-visible,.sidebar-overlay a:focus-visible', 'a:focus-visible'),
                 'policy_db': ('#tableView tr.main-row:focus-visible', 'tr:focus-visible')}
        for page, (selector, general) in cases.items():
            for key in check_v5.SHIPPED_FOCUS_PAGES[page]:
                text = self.shipped[key]
                last_style = text.rindex('</style>')
                for name, broken in (('ring removed', self.in_rule(text, selector, lambda decl: '')),
                                     ('ring outside the box', self.in_rule(text, selector, lambda decl: re.sub(r'outline-offset:\s*-2px', 'outline-offset:2px', decl))),
                                     ('another colour', self.in_rule(text, selector, lambda decl: decl.replace('#1d4ed8', '#000000'))),
                                     ('thinner ring', self.in_rule(text, selector, lambda decl: re.sub(r'outline:\s*2px', 'outline: 1px', decl))),
                                     ('turned off by a later rule', text[:last_style] + general + '{outline:none}' + text[last_style:]),
                                     ('moved out by a later rule', text[:last_style] + general + '{outline-offset:3px}' + text[last_style:])):
                    with self.subTest(page=key, change=name):
                        self.assertNotEqual(broken, text)
                        issues = focus_ring_issues(dict(self.shipped, **{key: broken}))
                        self.assertTrue(issues)
                        self.assertTrue(all(key in issue for issue in issues), issues)
        # The page that carries the rule missing, and a ring that differs between the languages.
        self.assertTrue(focus_ring_issues({k: v for k, v in self.shipped.items() if k != '/zh/documentation.html'}))
        differ = dict(self.shipped, **{'/zh/policy_groups.html': self.in_rule(self.shipped['/zh/policy_groups.html'], '.group-header:focus-visible', lambda decl: decl.replace('border-radius:7px;', 'border-radius:5px;'))})
        self.assertTrue(focus_ring_issues(differ))


class ScrollMarginChecks(unittest.TestCase):
    """Fix round F3: the keyboard targets of All records carry a scroll margin below the sticky header, only from 701 px up."""
    @classmethod
    def setUpClass(cls):
        from scripts.zh_pages.tests import test_focus_rings
        built = test_focus_rings.render('policy_db')
        cls.pages = {'/viz/en/all_records.html': built['en'], '/viz/zh/all_records.html': built['zh']}

    def test_the_built_pages_pass_and_a_lost_wrong_or_unbounded_margin_is_found(self):
        self.assertEqual(scroll_margin_issues(self.pages), [])
        for key in self.pages:
            text = self.pages[key]
            for name, broken in (('margin removed from the rows', text.replace('scroll-margin-top: calc(var(--thead-h) + 8px);', '', 1)),
                                 ('header height lost', re.sub(r'--thead-h:\s*42px;', '', text, count=1)),
                                 ('header height too small', text.replace('--thead-h: 42px', '--thead-h: 12px', 1)),
                                 ('smaller margin', text.replace('calc(var(--thead-h) + 8px)', 'calc(var(--thead-h) + 0px)', 1)),
                                 ('for every width', re.sub(r'@media \(min-width: 701px\) \{(\s*#tableView \{ --thead-h: 42px;)', r'@media all {\1', text, count=1)),
                                 ('a margin outside the media query', text.replace('</style>', '#tableView tr.main-row{scroll-margin-top:50px}</style>', 1))):
                with self.subTest(page=key, change=name):
                    self.assertTrue(broken != text)
                    issues = scroll_margin_issues(dict(self.pages, **{key: broken}))
                    self.assertTrue(issues)
                    self.assertTrue(all(key in issue or 'differs' in issue for issue in issues), issues)
        self.assertTrue(scroll_margin_issues({k: v for k, v in self.pages.items() if 'zh' not in k}))
        self.assertTrue(scroll_margin_issues(dict(self.pages, **{'/viz/zh/all_records.html': self.pages['/viz/zh/all_records.html'].replace('+ 8px)', '+ 9px)')})))


class CandidateCopyChecks(unittest.TestCase):
    """End to end, on a copy of a built candidate (set NES_V51_CANDIDATE to its folder; skipped otherwise): check_v5 passes on the copy and
    exits 1 when one page of the copy is corrupted in one of the four ways the V5.1 release closes. The copy goes under
    NES_V51_COPY_DIR (a folder on the big disk), not the system temp folder."""
    folder = os.environ.get('NES_V51_CANDIDATE', '')
    copy_dir = os.environ.get('NES_V51_COPY_DIR', '')

    @classmethod
    def setUpClass(cls):
        if not cls.folder or not (Path(cls.folder) / 'build_v5.json').is_file() or not Path(cls.copy_dir).is_dir():
            raise unittest.SkipTest('NES_V51_CANDIDATE (a built candidate) and NES_V51_COPY_DIR (a folder for its copy) are not both set')
        cls.scratch = tempfile.mkdtemp(prefix='check-v5-copy-', dir=cls.copy_dir)
        cls.copy = Path(cls.scratch) / 'c'
        shutil.copytree(cls.folder, cls.copy)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, 'scratch', ''), ignore_errors=True)

    def run_check(self):
        # One process per run: the checker pins the snapshot of the receipt once per process.
        run = subprocess.run([sys.executable, str(Path(check_v5.__file__)), '--output-dir', str(self.copy)], cwd=str(check_v5.ROOT), capture_output=True, text=True)
        return run.returncode, run.stderr

    def corrupt(self, name, change):
        path = self.copy / name
        original = path.read_text(encoding='utf-8')
        changed = change(original)
        self.assertNotEqual(changed, original)
        path.write_text(changed, encoding='utf-8')
        try:
            return self.run_check()
        finally:
            path.write_text(original, encoding='utf-8')

    def test_the_copy_passes_and_four_corruptions_fail(self):
        self.assertEqual(self.run_check()[0], 0)
        label = json.loads(check_v5.FINDINGS_FILE.read_text(encoding='utf-8'))['findings'][0]['short']['en']
        code, err = self.corrupt('policy_db.html', lambda text: text.replace('<li>' + escape(label) + '</li>', '', 1))
        self.assertEqual(code, 1)
        self.assertIn('/policy_db.html: the figure\'s findings list', err)
        code, err = self.corrupt('viz/en/all_records.html', lambda text: text.replace('</body>', '<a href="https://x.org/a?api_key=0123456789abcdef">x</a></body>', 1))
        self.assertEqual(code, 1)
        self.assertIn('viz/en/all_records.html: 1 API key', err)
        self.assertNotIn('0123456789abcdef', err)

        def link(text):
            url = re.search(r'"url":"(https?:[^"]*)"', text)[1]
            return text.replace('"url":"' + url + '"', '"url":"' + url + '#frag"', 1)
        code, err = self.corrupt('viz/en/screening.html', link)
        self.assertEqual(code, 1)
        self.assertIn('Original-text addresses carry', err)
        code, err = self.corrupt('zh/documentation.html', lambda text: re.sub(r'(\.sidebar a:focus-visible,\.sidebar-overlay a:focus-visible\s*\{)[^}]*\}', r'\1}', text, count=1))
        self.assertEqual(code, 1)
        self.assertIn('/zh/documentation.html: the Method sidebar link needs one rule', err)
        # Round two: the third level named Patterns, the credential guard on a link built from an API field, the All records scroll margin.
        code, err = self.corrupt('policy_db.html', lambda text: text.replace('<p class="mf-lab">Explore</p>', '<p class="mf-lab">Patterns</p>', 1))
        self.assertEqual(code, 1)
        self.assertIn('/policy_db.html: the figure', err)
        code, err = self.corrupt('viz/en/all_records.html', lambda text: text.replace('hasCredentialParam(r.fulltext_url)', 'false'))
        self.assertEqual(code, 1)
        self.assertIn('viz/en/all_records.html: the guard call hasCredentialParam(r.fulltext_url) is missing', err)
        self.assertNotIn('INVENTEDkey', err)
        code, err = self.corrupt('viz/zh/all_records.html', lambda text: text.replace('scroll-margin-top: calc(var(--thead-h) + 8px);', ''))
        self.assertEqual(code, 1)
        self.assertIn('/viz/zh/all_records.html: "#tableView tr.main-row" needs scroll-margin-top', err)
