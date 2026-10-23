"""V5 Estimates page IA, with retained timing-adapter contract tests."""
import copy
import json
import re
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.viz_site.policy_exposure_adapter import render_treatment_timing


HERE = Path(__file__).resolve().parents[1]


def payload():
    return json.loads((HERE / 'data/policy_exposure.json').read_text())


def strings(lang):
    labels = json.loads((HERE / f'i18n/policy_exposure.{lang}.json').read_text())
    labels.update(json.loads((HERE / f'i18n/worked_examples.{lang}.json').read_text()))
    return labels


class EstimatesIATests(unittest.TestCase):
    def test_timing_summary_and_appendix_in_both_languages(self):
        data = payload()
        before = copy.deepcopy(data)
        for lang in ('en', 'zh'):
            html = render_treatment_timing(data, lang, strings(lang))
            self.assertIn('id="treatment-timing"', html)
            for number in (24, 129, 2004, 2006, 1999, 2000):
                self.assertIn(str(number), html)
            self.assertNotIn('policy_exposure_records.html', html)
            self.assertNotIn('id="enforcement-events"', html)
            self.assertIn('<details id="signing-effective-records"', html)
            self.assertNotIn(' open', html)
            self.assertEqual(html.count('<table'), 1)
            body = html.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
            self.assertEqual(body.count('<tr>'), 24)
            self.assertEqual(body.count('<td '), 24 * 4)
            self.assertIn('Effective → Adoption / decision' if lang == 'en' else '生效 → 采纳／裁决', html)
            # Maryland's effective FY2010 becomes signing FY2009.
            self.assertIn('<span>FY2010</span> <span>→ FY2009</span>', html)
            self.assertNotIn('/policy_exposure.html', html)
            self.assertEqual(html.count('<p>'), 2)
            self.assertIn('Hendricks County injunction' if lang == 'en' else '亨德里克斯县禁令', html)
            self.assertIn('statewide reading' if lang == 'en' else '全州解释', html)
            self.assertIn('#signing-effective-records', html)
            trace_label = '24 of 129' if lang == 'en' else '24 条改变'
            self.assertIn(f'#signing-effective-records">{trace_label}</a>', html)
            self.assertIn('concealed-carry law' if lang == 'en' else '隐蔽持枪许可法', html)
            self.assertIn('seat-belt law' if lang == 'en' else '安全带法', html)
            self.assertIn('uninterrupted retroactivity' if lang == 'en' else '无中断追溯效力', html)
        self.assertEqual(data, before)

    def test_timing_numbers_follow_contract_changes(self):
        data = payload()
        clocks = data['descriptive']['three_clocks']
        row = next(r for r in clocks['cases'] if r['case_id'] == 'paid_sick_leave')
        row['signing_effective'].update(changed_records=4, share=4 / 7)
        clocks['signing_effective'].update(changed_records=25, share=25 / 129)
        mn = next(r for r in clocks['enforcement_examples'] if r['state'] == 'MN')
        mn['enforceable_first_period'] = 2007
        with patch('scripts.viz_site.policy_exposure_adapter.Renderer.signing_effective_records', return_value=''):
            html = render_treatment_timing(data, 'en', strings('en'))
        self.assertIn('25 of 129', html)
        self.assertIn('2004 to 2007', html)
        self.assertNotIn('24 of 129', html)

    def test_changed_records_reject_source_contract_disagreement(self):
        data = payload()
        clocks = data['descriptive']['three_clocks']
        row = next(r for r in clocks['cases'] if r['case_id'] == 'paid_sick_leave')
        row['signing_effective'].update(changed_records=4, share=4 / 7)
        clocks['signing_effective'].update(changed_records=25, share=25 / 129)
        with self.assertRaisesRegex(ValueError, 'trace disagrees'):
            render_treatment_timing(data, 'en', strings('en'))

    def test_summary_rejects_inconsistent_contract(self):
        data = payload()
        data['descriptive']['three_clocks']['signing_effective']['changed_records'] += 1
        with self.assertRaisesRegex(ValueError, 'aggregate denominators'):
            render_treatment_timing(data, 'en', strings('en'))

    def test_summary_escapes_numeric_slots(self):
        data = payload()
        data['descriptive']['three_clocks']['enforcement_examples'][0]['enforceable_first_period'] = '<img src=x onerror=alert(1)>'
        html = render_treatment_timing(data, 'en', strings('en'))
        self.assertIn('&lt;img', html)
        self.assertNotIn('<img', html)

    def test_v5_page_order_and_legacy_template_remains_an_adapter(self):
        from scripts.viz_site.restructure_estimates import render
        template = (HERE / 'pages/worked_examples.html').read_text()
        self.assertIn('{{v5_estimates}}', template)
        self.assertIn('{{partial:header}}', template)
        self.assertIn('{{partial:footer}}', template)
        for obsolete in ('{{l2_noise}}', '{{l2_additions}}', '{{worked_examples}}', '{{treatment_timing}}'):
            self.assertNotIn(obsolete, template)
        for lang in ('en', 'zh'):
            html = render(lang)
            order = ('class="v5-progress"', 'class="v5-scope"',
                     'id="v5-gap"', 'id="v5-confirmed"', 'id="method-materials"')
            offsets = [html.index(item) for item in order]
            self.assertEqual(offsets, sorted(offsets))
            self.assertNotIn('/policy_exposure.html', html)
            self.assertNotIn('class="we-cases"', html)
        # The old adapter remains available for its own fixture/data tests.
        self.assertIn('{{example:id}}-heading', (HERE / 'partials/worked_examples_body.html').read_text())

    def test_bilingual_keys_and_titles(self):
        en, zh = strings('en'), strings('zh')
        self.assertEqual(set(en), set(zh))
        self.assertEqual(en['we.title'], 'Estimates')
        self.assertEqual(zh['we.title'], '估计')
        for key in en:
            self.assertEqual(set(re.findall(r'\{(\w+)\}', en[key])),
                             set(re.findall(r'\{(\w+)\}', zh[key])), key)
        for dictionary in (en, zh):
            for key in ('we.intro', 'we.intro_html', 'cases.intro_html'):
                self.assertNotIn('L2', dictionary[key])


if __name__ == '__main__':
    unittest.main()
