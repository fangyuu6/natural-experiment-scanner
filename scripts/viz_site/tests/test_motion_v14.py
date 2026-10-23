"""The Home figure of the fourteenth round: render(lang), its data, its gate and the Home-only brand icon."""
from html import unescape
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.viz_site import check_v5, motion_figure, motion_figure_v14 as figure, motion_v14_check as gate
from scripts.viz_site import restructure_estimates as estimates, restructure_home
from scripts.viz_site.public_page import PublicText

HERE = Path(__file__).resolve().parents[1]
PIN_FILE = HERE / 'data/snapshot_pin.json'
CJK = re.compile('[㐀-䶿一-鿿豈-﫿　-〿＀-￯]')
GIVEN = {'texts': 483414, 'candidates': 7781}


def setUpModule():
    if estimates.load_snapshot.cache_info().currsize == 0:
        estimates.pin_snapshot(json.loads(PIN_FILE.read_text())['snapshot_path'])


class RenderTests(unittest.TestCase):
    def test_both_languages_render_one_figure_with_two_44px_buttons(self):
        for lang in ('en', 'zh'):
            markup = figure.render(lang, GIVEN)
            self.assertEqual(markup.count('id="motion-figure"'), 1)
            self.assertEqual(markup.count('class="mf-btn"'), 2)
            self.assertIn('width:44px;height:44px', markup)
            self.assertIn('prefers-reduced-motion', markup)

    def test_the_english_page_holds_no_chinese_and_the_chinese_page_ships_no_english_strings(self):
        en, zh = figure.render('en', GIVEN), figure.render('zh', GIVEN)
        self.assertFalse(CJK.search(en))
        self.assertIn('筛查', zh)
        self.assertNotIn('Screening: policy texts land', zh)
        self.assertNotIn('筛查：政策文本落到', en)

    def test_the_numbers_are_the_loaders(self):
        snapshot = estimates.load_snapshot()
        markup = figure.render('en', GIVEN)
        data = json.loads(re.search(r'id="mf-data">(.*?)</script>', markup, re.S)[1])
        public = estimates.public_counts(snapshot)
        self.assertEqual(data['counts']['papers'], estimates.attempted(snapshot))
        self.assertEqual([data['counts'][k] for k in ('match', 'dir', 'nc', 'os')], [public[k] for k in figure.GROUPS])
        self.assertEqual((data['counts']['texts'], data['counts']['cands']), (483414, 7781))

    def test_the_visible_text_names_no_dataset_and_no_candidate_becomes_the_design(self):
        for lang in ('en', 'zh'):
            markup = figure.render(lang, GIVEN)
            shown = ' '.join(PublicText(markup).parts)
            for word in ('SPID', 'DellaVigna'):
                self.assertNotIn(word, shown)
            self.assertNotRegex(markup, r'one candidate becomes|一个候选成为')

    def test_the_scripts_parse(self):
        for lang in ('en', 'zh'):
            markup = figure.render(lang, GIVEN)
            code = markup[markup.rindex('<script>') + 8:markup.rindex('</script>')]
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'figure.js'
                path.write_text(code, encoding='utf-8')
                run = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_screening_totals_are_required(self):
        with self.assertRaises(ValueError):
            figure.render('en', {})


class GateTests(unittest.TestCase):
    def home(self, lang):
        with patch.object(motion_figure, 'MOTION_VERSION', 'v14'):
            return '<div id="sm-root"></div>' + restructure_home.render(lang)

    def issues(self, home, lang):
        return gate.issues(home, lang, ('483,414', '7,781'), estimates.load_snapshot(), check_v5.nav_labels(lang))

    def test_the_real_home_passes(self):
        for lang in ('en', 'zh'):
            self.assertEqual(self.issues(self.home(lang), lang), [], lang)

    def test_the_gate_finds_a_changed_count_a_missing_label_and_an_external_load(self):
        home = self.home('en')
        self.assertTrue(self.issues(home.replace('"papers":53', '"papers":54', 1), 'en'))
        self.assertTrue(self.issues(home.replace('aria-label="Replay"', 'aria-label=""', 1), 'en'))
        self.assertTrue(self.issues(home.replace('<canvas', '<img src="x"><canvas', 1), 'en'))
        self.assertTrue(self.issues(home.replace('id="motion-figure"', 'id="motion-figure"></figure><figure id="motion-figure"', 1), 'en'))

    def test_the_gate_finds_chinese_on_the_english_page(self):
        home = self.home('en')
        self.assertTrue(self.issues(home.replace('<p class="mf-nojs">', '<p class="mf-nojs">筛查', 1), 'en'))


class BrandIconTests(unittest.TestCase):
    def test_the_icon_is_one_shared_mark_in_the_brand_blue_on_every_page(self):
        # V5.7: the mark lives in the shared header style (partials/head_css.html), not in the Home block, and is painted
        # with the site's accent token through a mask, so its blue is exactly --nx-accent.
        for lang in ('en', 'zh'):
            home = restructure_home.render(lang)
            self.assertNotIn('.site-brand-link::before', home)
            self.assertNotIn('opacity:.5', home)
        css = (HERE / 'partials/head_css.html').read_text()
        self.assertEqual(css.count('.site-brand-link::before'), 1)
        rule = re.search(r'\.site-brand-link::before \{([^}]*)\}', css)[1]
        self.assertIn('background: var(--nx-accent)', rule)
        self.assertNotIn('opacity', rule)
        header = (HERE / 'partials/header.html').read_text()
        self.assertNotIn('brand-icon', header)


if __name__ == '__main__':
    unittest.main()
