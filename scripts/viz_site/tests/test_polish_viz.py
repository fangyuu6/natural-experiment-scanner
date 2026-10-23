"""V5 route contracts: minimal retired redirects and the retained curated map."""
from html.parser import HTMLParser
import unittest
from scripts.viz_site import build


TARGETS = {
    'overview': '/viz/{lang}/screening.html',
    'funnel': '{original}/documentation.html#screening',
    'design_opportunity': '/viz/{lang}/adoption.html#hs-map',
    'policy_exposure': '/viz/{lang}/worked_examples.html#method-materials',
    'policy_exposure_records': '/viz/{lang}/worked_examples.html#method-materials',
}


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class RedirectTests(unittest.TestCase):
    def test_all_retired_urls_are_chrome_free_stubs(self):
        for lang in build.LANGS:
            for page, target_pattern in TARGETS.items():
                with self.subTest(lang=lang, page=page):
                    errors = build.Errors()
                    source = build.read_text(build.PAGES_DIR / (page + '.html'))
                    strings = dict(build.load_dict('common', lang, errors))
                    html = build.render(source, dict(lang=lang, page=page, strings=strings), errors, 'stub')
                    build.lint_output(html, lang, 'stub', errors, page)
                    self.assertEqual(errors.errors, [])
                    target = target_pattern.format(lang=lang, original='/zh' if lang == 'zh' else '')
                    tags = Tags(html).tags
                    links = [attrs for tag, attrs in tags if tag == 'a']
                    self.assertEqual(links, [{'href': target}])
                    self.assertIn(('meta', {'name': 'robots', 'content': 'noindex'}), tags)
                    self.assertIn(('meta', {'http-equiv': 'refresh', 'content': '0;url=' + target}), tags)
                    self.assertIn(('link', {'rel': 'canonical', 'href': target}), tags)
                    self.assertFalse(any(tag in ('nav', 'header', 'footer', 'script', 'style') for tag, _ in tags))
                    self.assertLess(len(html.encode()), 700)

    def test_the_adoption_lead_is_one_sentence_and_the_map_caption_keeps_the_source_of_the_dates(self):
        # R27, copy gate O1: "Dates come from policy trackers and statutes." repeated the map caption directly below the lead.
        expected = {'en': 'Eight sourced examples of staggered adoption, through 2025.', 'zh': '8 项有来源记录的交错采纳实例，截至 2025 年底。'}
        for lang in build.LANGS:
            with self.subTest(lang=lang):
                errors = build.Errors()
                strings = build.load_dict('adoption', lang, errors)
                self.assertEqual(errors.errors, [])
                self.assertEqual(strings['ad.curated.sub'], expected[lang])
                self.assertNotIn('trackers', strings['ad.curated.sub'])
                self.assertNotIn('追踪', strings['ad.curated.sub'])

    def test_curated_adoption_is_a_secondary_page_with_accessible_controls(self):
        for lang in build.LANGS:
            with self.subTest(lang=lang):
                errors = build.Errors()
                source = build.read_text(build.PAGES_DIR / 'adoption.html')
                strings = dict(build.load_dict('common', lang, errors))
                for name in build.partials_used(source, errors) + ['adoption']:
                    strings.update(build.load_dict(name, lang, errors) or {})
                html = build.render(source, dict(lang=lang, page='adoption', strings=strings), errors, 'curated-adoption')
                build.lint_output(html, lang, 'curated-adoption', errors, 'adoption')
                self.assertEqual(errors.errors, [])
                tags = Tags(html).tags
                self.assertFalse(any(tag == 'meta' and attrs.get('http-equiv') == 'refresh' for tag, attrs in tags))
                ids = [attrs['id'] for _, attrs in tags if 'id' in attrs]
                self.assertIn('hs-map', ids)
                self.assertIn('sm-root', ids)
                radios = [attrs for tag, attrs in tags if tag == 'button' and attrs.get('role') == 'radio']
                self.assertEqual(len(radios), 8)
                checked = [r for r in radios if r.get('aria-checked') == 'true']
                self.assertEqual(len(checked), 1)
                self.assertEqual(checked[0].get('tabindex'), '0')
                self.assertTrue(all(r.get('tabindex') == '-1' for r in radios if r not in checked))
                self.assertEqual(len({r['data-f'] for r in radios}), 8)
                self.assertTrue(any(tag == 'button' and attrs.get('id') == 'sm-control' and attrs.get('aria-label') for tag, attrs in tags))
                self.assertIn(strings['ad.curated.title'], html)
                self.assertIn(strings['ad.curated.sub'], html)
                # R29 (AD1): no "Back to Screening" link under the map; the navigation bar still reaches Screening.
                self.assertNotIn(('a', {'href': '/viz/' + lang + '/screening.html'}), tags)
                self.assertNotIn(strings.get('ad.curated.back', '\0'), html)
                self.assertTrue(any(tag == 'a' and attrs.get('href') == '/viz/' + lang + '/screening.html' for tag, attrs in tags))
                self.assertNotIn('{{', html)

    def test_live_release_keys_are_updates_including_redirects(self):
        from scripts.release import make_manifest
        # The synthesize pair has been live since 80579e1-20261008, so this release adds no NEW keys.
        self.assertEqual(make_manifest.NEW_IN_RELEASE, frozenset())
        required = dict(make_manifest.ALLOWLIST + make_manifest.DESIGN_OPPORTUNITY + make_manifest.POLICY_EXPOSURE + make_manifest.POLICY_EXPOSURE_RECORDS)
        for lang in build.LANGS:
            for page in (*TARGETS, 'adoption'):
                self.assertIn(f'viz/{lang}/{page}.html', required)


if __name__ == '__main__':
    unittest.main()
