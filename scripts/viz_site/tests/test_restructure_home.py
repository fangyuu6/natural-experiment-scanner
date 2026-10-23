"""The Home staircase: three steps whose figures are the ones the linked pages print, read from their sources."""
from html import unescape
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from scripts.viz_site import motion_figure
from scripts.viz_site import restructure_estimates as estimates
from scripts.viz_site import restructure_explore as explore
from scripts.viz_site import restructure_home as home
from scripts.viz_site import restructure_screening as screening
from scripts.viz_site.public_page import PublicText

ROUTES = ('screening', 'worked_examples', 'synthesize')
FIGURES = (('texts', 'candidates'), ('papers', 'matched'), ('questions', 'finished'))
# The Estimate step also says "within 10%": the rule Estimates prints (content check of R25, M1).
LITERALS = ((), ('10',), ())


def steps(markup):
    """(href, big word, line text) of each step; the aria-hidden counted-up copy of a figure is left out."""
    found = []
    for item in re.findall(r'<li class="nx-st"[^>]*>(.*?)</li>', markup, re.S):
        href, word = re.search(r'<h3 class="nx-lv-word"><a href="([^"]+)">([^<]*)</a></h3>', item).groups()
        line = re.search(r'<p class="nx-nd[^"]*"[^>]*>(.*?)</p>', item, re.S)[1]
        text = re.sub(r'<[^>]+>', '', re.sub(r'<span class="nx-tally"[^>]*>.*?</span>', '', line))
        found.append((href, word, ' '.join(unescape(text).split())))
    return found


class HomeStaircaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = estimates.load_snapshot()
        cls.printed = screening.load_data()
        cls.asked = explore.load_questions()
        cls.words = {lang: json.loads((home.HERE / f'i18n/home_story.{lang}.json').read_text()) for lang in ('en', 'zh')}
        cls.nav = {lang: json.loads((home.HERE / f'i18n/common.{lang}.json').read_text()) for lang in ('en', 'zh')}

    def test_figures_are_the_numbers_the_linked_pages_print(self):
        shown = home.figures()
        self.assertEqual((shown['texts'], shown['candidates']), (self.printed['total'], self.printed['count']))
        self.assertEqual((shown['papers'], shown['matched']),
                         (estimates.attempted(self.snapshot), estimates.public_counts(self.snapshot)['number_matches']))
        # R26 (hub default): the Explore step shows the Questions list's counts, not one study's numbers.
        self.assertEqual((shown['questions'], shown['finished']), (len(self.asked), sum(row['status'] == 'finished' for row in self.asked)))
        self.assertEqual((shown['questions'], shown['finished']), (17, 4))
        self.assertNotIn('policies', shown)
        self.assertNotIn('events', shown)
        # The same values as those pages print them: Screening's count line, Estimates' sentence, Explore's Questions list.
        count_line = f'{shown["texts"]:,} US policy texts · {shown["candidates"]:,} pass five screening rules'
        self.assertIn(count_line, re.sub(r'<(style|script)\b.*?</\1>', '', screening.render('en'), flags=re.S))
        sentence = estimates.progress_line('en', include_date=False)
        self.assertIn(f'has re-estimated {shown["papers"]} published', sentence)
        self.assertIn(f'{shown["matched"]} selected point estimates', sentence)
        # R29: the Questions are grouped under their status headings, so the counts are the rows under each heading.
        section = re.search(r'<section id="questions".*?</section>', explore.render('en'), re.S)[0]
        rows = {heading: items.count('<li>') for heading, items in re.findall(r'<h3>([^<]*)</h3><ol class="ex-questions">(.*?)</ol>', section, re.S)}
        self.assertEqual((sum(rows.values()), rows['Finished']), (shown['questions'], shown['finished']))

    def test_r29_trim_lead_and_map_wording(self):
        # R29 (TRIM_SPEC_V51.md, HM1 and HM4 to HM7): one verb for the second activity, and the card's Chinese wording and first header.
        # V5.2 (owner decision 2026-10-21): new headline subtitle.
        self.assertEqual(self.words['en']['story.intro_html'], 'Screening US policy text for natural experiments, re-estimating published state-policy studies, and reading patterns across policies.')
        self.assertEqual(self.words['zh']['story.intro_html'].replace('<wbr>', ''), '筛查美国政策文本中的自然实验，重新估计已发表的州政策研究，再看跨政策的规律。')
        card = {lang: json.loads((home.HERE / f'i18n/story_map.{lang}.json').read_text()) for lang in ('en', 'zh')}
        self.assertEqual((card['zh']['map.lg_db'], card['zh']['map.ro_default']), ('抽样数据库中的颁布法案', '悬停或点按州'))
        self.assertEqual((card['en']['map.p.license_recognition.name'], card['zh']['map.p.license_recognition.name']), ('Recognition of other states’ licenses', '他州执照认可'))
        self.assertEqual((card['zh']['map.scope.paid_sick_leave.CT'], card['zh']['map.scope.paid_sick_leave.MI']),
                         ('康涅狄格州：服务业工人，雇主员工 50 人以上', '密歇根州：2019 年法律，雇主员工 50 人以上'))
        # The captions are gone; the provenance line stays (section 4 fallback for HM3) as the only text under the card.
        for lang in ('en', 'zh'):
            self.assertFalse([key for key in card[lang] if key.startswith('map.cap.')])
            self.assertTrue(card[lang]['map.provenance'])
        partial = (home.HERE / 'partials/story_map.html').read_text()
        self.assertEqual(partial.count('id="sm-cap"'), 1)
        self.assertNotIn('sm-definition', partial)

    def test_three_steps_each_a_big_word_linking_to_its_page(self):
        for lang in ('en', 'zh'):
            markup = home.render(lang)
            self.assertEqual(markup.count('class="nx-st"'), 3)
            self.assertEqual(markup.count('<ol class="nx-lv nx-stair"'), 1)
            self.assertEqual([href for href, _, _ in steps(markup)], [f'/viz/{lang}/{route}.html' for route in ROUTES])
            self.assertEqual(re.findall(r'<span class="nx-lv-no">(L\d)</span>', markup), ['L1', 'L2', 'L3'])
            self.assertEqual(re.findall(r'<h2 class="nx-h2" id="hs-levels">(.*?)</h2>', markup), [self.words[lang]['story.levels.h']])
            self.assertEqual(markup.count('data-nx-intro="stair-step"'), 3)
            self.assertEqual(markup.count('data-home-block="2"'), 1)

    def test_words_are_the_staircases_where_they_fit_else_the_navigation_label(self):
        # Screen and Estimate are the staircase's words and fit Screening and Estimates; Synthesize does not fit Explore.
        for lang, words in (('en', ('Screen', 'Estimate', 'Explore')), ('zh', ('筛查', '估计', '探索'))):
            shown = [word for _, word, _ in steps(home.render(lang))]
            self.assertEqual(shown, list(words))
            labels = [self.nav[lang]['nav.' + key] for key in ('screening', 'worked_examples', 'synthesize')]
            for word, label in zip(shown, labels):
                self.assertTrue(label.startswith(word), (word, label))
            self.assertNotIn('Synthesize', ' '.join(shown))
            self.assertNotIn('综合', ' '.join(shown))

    def test_each_line_is_short_and_shows_only_its_two_figures(self):
        shown = home.figures()
        for lang in ('en', 'zh'):
            for (_, _, line), names, literals in zip(steps(home.render(lang)), FIGURES, LITERALS):
                self.assertEqual(re.findall(r'\d[\d,]*', line), [f'{shown[name]:,}' for name in names] + list(literals), line)
                if lang == 'en':
                    self.assertLessEqual(len(line.split()), 10, line)
                else:
                    self.assertLessEqual(len(re.sub(r'[\s\d,→，]', '', line)), 20, line)

    def test_the_lines_are_the_content_check_wording(self):
        # Content check of R25: M1 "within 10%" (not "match"), M2 "unique", and (hub default) the Questions list's counts on the Explore step.
        shown = home.figures()
        expected = {
            'en': (f'{shown["texts"]:,} unique policy texts → {shown["candidates"]:,} candidates',
                   f'{shown["papers"]} papers re-estimated, {shown["matched"]} within 10%',
                   f'{shown["questions"]} questions · {shown["finished"]} finished'),
            'zh': (f'{shown["texts"]:,} 份不重复政策文本 → {shown["candidates"]:,} 条候选记录',
                   f'已重新估计 {shown["papers"]} 篇论文，{shown["matched"]} 篇差距不超过 10%',
                   f'{shown["questions"]} 个问题 · {shown["finished"]} 个已完成'),
        }
        for lang in ('en', 'zh'):
            self.assertEqual([line for _, _, line in steps(home.render(lang))], list(expected[lang]))
        self.assertEqual(steps(home.render('en'))[2][2], '17 questions · 4 finished')
        self.assertEqual(steps(home.render('zh'))[2][2], '17 个问题 · 4 个已完成')
        for lang, words in (('en', ('match', 'adoptions', 'policies,')), ('zh', ('数字一致', '次采纳', '项政策'))):
            visible = ' '.join(line for _, _, line in steps(home.render(lang)))
            for word in words:
                self.assertNotIn(word, visible)

    def test_the_heading_is_plain(self):
        # Hub default (O3): no DiD or RD in the Home heading, which the Home never explains.
        plain = {lang: re.sub(r'<[^>]+>', '', self.words[lang]['story.thesis_html']) for lang in ('en', 'zh')}
        self.assertEqual(plain['en'], 'From Policy Text to Insights')
        self.assertEqual(plain['zh'], '从政策文本到洞见')
        for lang in ('en', 'zh'):
            for term in ('DiD', 'RD'):
                self.assertNotIn(term, plain[lang])

    def test_the_heading_keeps_its_phrase_on_one_line_and_marks_one_key_word_in_the_brand_blue(self):
        # V5.7 (owner, 2026-10-23): "Policy Text" never splits; the key word takes the accent token.
        for lang, phrase, key in (('en', 'Policy Text', 'Insights'), ('zh', '政策文本', '洞见')):
            html = self.words[lang]['story.thesis_html']
            self.assertIn(f'<span class="home-nb">{phrase}</span>', html)
            self.assertIn(f'<span class="home-key">{key}</span>', html)
        story = (Path(__file__).resolve().parents[1] / 'partials/home_story.html').read_text()
        self.assertIn('.home-v5 .home-nb{white-space:nowrap}', story)
        self.assertIn('.home-v5 .home-key{color:var(--nx-accent)}', story)

    def test_a_figure_and_its_noun_never_split_across_lines(self):
        # Every number-and-noun pair is a no-break segment, so a narrow step wraps between pairs, not inside one.
        for lang in ('en', 'zh'):
            for item in re.findall(r'<li class="nx-st"[^>]*>(.*?)</li>', home.render(lang), re.S):
                line = re.search(r'<p class="nx-nd[^"]*"[^>]*>(.*?)</p>', item, re.S)[1]
                self.assertGreaterEqual(line.count('class="nx-seg"') + line.count('class="nx-candidates"'), 2, line)
        self.assertIn('<span class="nx-seg">', home.render('en'))

    def test_only_the_two_screen_figures_count_up_and_each_is_read_once(self):
        for lang in ('en', 'zh'):
            markup = home.render(lang)
            figures = home.figures()
            self.assertEqual(re.findall(r'<span class="nx-tally" aria-hidden="true">([^<]*)</span>', markup),
                             [f'{figures["texts"]:,}', f'{figures["candidates"]:,}'])
            self.assertEqual(re.findall(r'<span class="sr-only">([^<]*)</span>', markup), [f'{figures["texts"]:,}', f'{figures["candidates"]:,}'])

    def test_staircase_figures_follow_their_sources(self):
        # No figure is written into a template: change a source and the Home changes with it.
        with patch.object(home.screening, 'load_data', return_value=dict(self.printed, total=1234567, count=8901)):
            line = steps(home.render('en'))[0][2]
        self.assertEqual(line, '1,234,567 unique policy texts → 8,901 candidates')
        rows = [{'status': 'finished'}, {'status': 'finished'}, {'status': 'waiting'}]
        with patch.object(home.explore, 'load_questions', return_value=rows):
            self.assertEqual(steps(home.render('en'))[2][2], '3 questions · 2 finished')
            self.assertEqual(steps(home.render('zh'))[2][2], '3 个问题 · 2 个已完成')

    def test_nothing_from_the_removed_blocks_is_left(self):
        # Owner, 2026-10-14 (V5.1): the doors, the feature card and the Explore text are gone from Home; the example stays on Estimates.
        removed = ('−47.617', '−47.6167', '20.140', 'SPID', 'DellaVigna', 'Democratic', '民主党', 'absolute value', '绝对值',
                   'of the published', '发表值', 'easiest-to-access', '最易取得', '648', 'second AI agent', '第二个 AI', 'Browse candidates', 'Compare estimates',
                   '35 policies', '597')
        for lang in ('en', 'zh'):
            # The motion figure carries its own script, data and styles (builder D); it is switched off here so that this test
            # keeps guarding the staircase section, and its visible text is held to the same list below.
            with patch.object(motion_figure, 'MOTION_PLACEMENT', 'none'):
                markup = unescape(home.render(lang))
            visible = re.sub(r'<[^>]+>', ' ', markup)
            for block in ('v5-door', 'home-evidence', 'home-feature', 'home-comparison', 'home-result', 'home-tolerance'):
                self.assertNotIn(block, markup)
            # (The Estimate step says "within 10%": M1 of the content check of R25. The Estimates headline sentence stays off Home.)
            for text in removed:
                self.assertNotIn(text, visible)
            self.assertNotIn('<script', markup)  # the staircase script lives in the partial
            # No catalogue size and no update stamp.
            self.assertNotIn(str(self.snapshot['total']), visible)
            self.assertNotIn(self.snapshot['as_of'], markup)
            # The figure, with script and style left out, shows none of the removed text and no catalogue size either.
            with patch.object(motion_figure, 'MOTION_PLACEMENT', 'home'):
                with_figure = home.render(lang)
            self.assertIn('id="motion-figure"', with_figure)
            shown = unescape(' '.join(PublicText(with_figure).parts))
            for text in removed:
                self.assertNotIn(text, shown)
            self.assertNotIn(str(self.snapshot['total']), shown)
            self.assertNotIn(self.snapshot['as_of'], shown)


if __name__ == '__main__':
    unittest.main()
