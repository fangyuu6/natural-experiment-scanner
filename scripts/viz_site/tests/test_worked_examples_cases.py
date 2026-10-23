"""V5 route contract and retained batch-2 adapters, using synthetic sidecars only."""
from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.did_samples.cases.case_sidecar import build_sidecar, pair
from scripts.did_samples.cases.case_validate import period_index
from scripts.did_samples.make_site_assets import adapt_worked_examples


ROOT = Path(__file__).resolve().parents[3]
SCRATCH = Path('/tmp/scratch/'
               '14a6bb1c-5947-4340-9a6e-b478f22d9a02/scratchpad/wp_preview_wp4b/fixtures')
CASE_IDS = ('cigarette_tax', 'adult_use_cannabis', 'primary_seat_belt', 'shall_issue',
            'minimum_wage_step', 'right_to_work', 'renewable_portfolio',
            'death_penalty_abolition', 'salary_history_ban', 'smoke_free_air')
POLICIES = (
    ('Cigarette tax', '卷烟税'), ('Adult-use cannabis', '成人用大麻合法化'),
    ('Primary seat-belt enforcement', '安全带主要执法'), ('Shall-issue carry permits', '符合条件即发放持枪许可'),
    ('Minimum-wage step', '最低工资大幅上调'), ('Right-to-work laws', '工作权法'),
    ('Renewable portfolio standards', '可再生能源配额'), ('Death-penalty abolition', '废除死刑'),
    ('Salary-history bans', '禁止询问薪资历史'), ('Smoke-free air laws', '无烟空气法'),
)


def synthetic_case_sidecars():
    """Produce exact display schema with invented results and declared words only.

    Calling build_sidecar formats in-memory scalar records; it does not estimate,
    open a panel, acquire data or execute any case.
    """
    records = []
    for index, case_id in enumerate(CASE_IDS):
        periods = [2000, 2001, 2002, 2003]
        basis = {'kind': 'calendar_year'}
        frequency = 'annual'
        if case_id == 'adult_use_cannabis':
            periods, basis, frequency = [20001, 20002, 20003, 20004], {'kind': 'quarter'}, 'quarterly'
        elif case_id == 'renewable_portfolio':
            basis = {'kind': 'fiscal_year', 'fiscal_start_month': 7}
        case = dict(case_id=case_id, data_kind='synthetic',
                    panel=dict(frequency=frequency, time_basis=basis),
                    outcome=dict(column='invented_y', units='fixture units'),
                    exposure_clock=dict(rule='first_full_period'),
                    benchmark=dict(author='Invented Study', year=2005, outlet='Synthetic fixture'))
        labels = dict(version=1, case_id=case_id, policy=pair(*POLICIES[index]),
                      treatment=pair('Invented first fully exposed period', '虚构的首个完整暴露时期'),
                      outcomes={'invented_y': dict(outcome=pair('Invented outcome', '虚构结果'),
                                                  population=pair('Invented residents', '虚构居民'),
                                                  unit=pair('fixture units', '虚构单位'))},
                      rows={'main': pair('Primary estimate', '主估计')})
        value = round(.2 + index * .05, 3)
        tier = ('TIGHT', 'NOT_COMPARABLE', 'NOT_CONSISTENT', 'DIRECTIONAL')[index % 4]
        comparison = dict(tier=tier)
        if tier != 'NOT_COMPARABLE':
            comparison.update(target=.3, absolute_tolerance=.1, ours={'att': value})
        main = dict(outcome_column='invented_y', population='invented residents',
                    calendar_years=periods, window_id='main', control_group='never_treated',
                    role='primary', overall=dict(att=value, ci95=[value-.2, value+.2]),
                    interval_metadata=dict(method='unit Gaussian multiplier', nominal_level=.95),
                    comparison=comparison, event_study=[
                        dict(event_time=t, att=round(.04*t, 3), n_treated=6 if t < 2 else 4,
                             pre_simultaneous95=[round(.04*t-.15, 3), round(.04*t+.15, 3)],
                             post_simultaneous95=[round(.04*t-.15, 3), round(.04*t+.15, 3)])
                        for t in (-2, -1, 0, 1, 2)])
        registry = {'main': main}
        if index % 2 == 0:
            variant = deepcopy(main)
            variant.update(role='primary_alternative', variant_id='alternative_control',
                           variant_kind='control', control_group='not_yet_treated',
                           overall=dict(att=value+.05, ci95=[value-.15, value+.25]))
            registry['alternate'] = variant
            labels['rows']['alternate'] = pair('Registered alternative control', '预设替代对照组')
            registry['main']['evidence'] = dict(
                omit_one={'jurisdiction': {'rows': [dict(id='omit/A', att=value-.03),
                                                    dict(id='omit/B', att=value+.04)]}},
                placebo=dict(att=.01, ci95=[-.05, .07]))
        primary = {'main': main}
        if case_id == 'adult_use_cannabis':
            second = deepcopy(main)
            second.update(outcome_column='invented_house_price', variant_id='house_price',
                          variant_kind='outcome', overall=dict(att=1.2, ci95=[.7, 1.7]),
                          comparison=dict(tier='NOT_COMPARABLE', comparison_role='context_only'))
            registry['second'] = second
            primary['second'] = second
            labels['outcomes']['invented_house_price'] = dict(
                outcome=pair('Invented house-price index', '虚构房价指数'),
                population=pair('Invented homes', '虚构住房'), unit=pair('index points', '指数点'))
            labels['rows']['second'] = pair('Registered second outcome', '预设第二结果')
        stopped = case_id in ('shall_issue', 'smoke_free_air')
        result = dict(case_id=case_id, status='INTERNAL_ONLY' if stopped else 'PUBLISHABLE_EXAMPLE',
                      primary_estimates=primary, registered_results=registry,
                      stop_reasons=['pretrend_joint'] if stopped else [])
        indices = [period_index(t, basis) for t in periods]
        adoption = {**{'T'+str(i): indices[1+i//3] for i in range(6)},
                    **{'N'+str(i): None for i in range(5)}, 'F': indices[-1]+4}
        result['calibration'] = dict(clean_pass_rate=.9375,
            structure=dict(units=list(adoption), periods=indices, adoption=adoption),
            mdd90={'anticipation_jump': {'full_blocking_set': dict(value=.125, status='grid_detected', units='fixture units')}})
        result['gate_table'] = [dict(gate=key, numbers=dict(measured_value=value, critical_value=limit), **{'pass': not stopped})
            for key, value, limit in (('pretrend_joint', .75, .01),
                                     ('pretrend_economic', .012, .05), ('placebo', .02, .08))]
        records.append(build_sidecar(case, result, labels))
    return records


def write_case_fixtures(directory):
    """Write ten explicitly synthetic sidecars to an isolated fixture directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    records = synthetic_case_sidecars()
    for record in records:
        (directory / (record['case_id'] + '_site.json')).write_text(
            json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    return records


class ClosedTags(HTMLParser):
    """Require explicit, correctly nested closure, including embedded SVG."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
            'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.ids, self.hrefs = [], [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if attrs.get('href', '').startswith('#'):
            self.hrefs.append(attrs['href'][1:])
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append('Unexpected closing tag: ' + tag)
        else:
            self.stack.pop()


class WorkedExamplesCasePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='page_tests_', dir=SCRATCH)
        cls.fixture_dir = Path(cls.temp.name) / 'cases'
        cls.records = write_case_fixtures(cls.fixture_dir)
        cls.batch1 = adapt_worked_examples(ROOT / 'scripts/did_samples/fixtures', fixtures=True)
        cls.output = Path(cls.temp.name) / 'output'
        cls.args = ['--fixtures', '--case-fixtures', str(cls.fixture_dir),
                    '--output-dir', str(cls.output), '--page', 'worked_examples']
        cls.build = cls.run_build(cls.args)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def run_build(args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/viz_site/build.py'), *args],
                              cwd=ROOT, text=True, capture_output=True)

    def page(self, lang):
        self.assertEqual(self.build.returncode, 0, self.build.stdout + self.build.stderr)
        return (self.output / 'viz' / lang / 'worked_examples.html').read_text(encoding='utf-8')

    def legacy_fragment(self, lang, directory=None, batch1=None):
        """Exercise the retained adapter independently of the replaced V5 page."""
        from scripts.viz_site import build
        from scripts.viz_site.make_case_assets import load_case_selection, merge_case_selection
        cases = load_case_selection(directory or self.fixture_dir)
        selection = merge_case_selection(batch1 or self.batch1, cases)
        errors = build.Errors()
        labels = build.load_dict('worked_examples', lang, errors)
        context = {'lang': lang, 'page': 'worked_examples', 'strings': labels}
        with patch.object(build, '_examples_cache', selection), patch.object(build, 'FIXTURE_MODE', True):
            html = build.render_worked_examples(context, errors)
        self.assertEqual(errors.errors, [])
        return html

    def test_v5_route_has_snapshot_results_and_excludes_case_fixtures(self):
        from scripts.viz_site.restructure_estimates import attempted, load_snapshot, progress_html
        snapshot = load_snapshot()
        for lang in ('en', 'zh'):
            html = self.page(lang)
            parser = ClosedTags(); parser.feed(html); parser.close()
            self.assertEqual(parser.errors, [])
            self.assertEqual(parser.stack, [])
            self.assertEqual(len(parser.ids), len(set(parser.ids)))
            self.assertTrue(set(parser.hrefs).issubset(set(parser.ids)))
            self.assertIn('name="robots" content="noindex"', html)
            self.assertIn(progress_html(lang), html)
            self.assertNotIn('class="v5-cell"', html)
            self.assertEqual(html.count('class="v5-row" data-display-key='), attempted(snapshot))
            self.assertEqual(html.count('class="v5-result"'), snapshot['confirmed_independent'])
            self.assertIn('id="method-materials"', html)
            self.assertNotIn('class="we-cases"', html)
            self.assertNotIn('class="we-event-study"', html)
            self.assertNotIn('Invented outcome', html)
            self.assertNotIn('虚构结果', html)
            self.assertNotIn('{{', html)
            self.assertLess(len(html.encode('utf-8')), 450_000)

    def test_synthetic_inventory_covers_display_branches(self):
        self.assertEqual([r['case_id'] for r in self.records], list(CASE_IDS))
        self.assertTrue(all(r['data_kind'] == 'synthetic' for r in self.records))
        self.assertEqual(sum(r['shown'] for r in self.records), 8)
        self.assertTrue(any(r['other_estimates'] for r in self.records))
        self.assertEqual(len(self.records[1]['primary_estimates']), 2)
        self.assertEqual(self.records[1]['primary_estimates']['main']['period_labels']['en'][0], '2000 Q1')
        self.assertTrue(self.records[6]['primary_estimates']['main']['period_labels']['en'][0].startswith('FY 2000'))
        lines = ' '.join(r['primary_estimates']['main']['comparison_line']['en'] for r in self.records)
        self.assertIn('No comparable published', lines)
        self.assertIn('not consistent with the published', lines)

    def test_bilingual_html_closure_anchors_and_flat_layout(self):
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang)
            parser = ClosedTags()
            parser.feed(html)
            parser.close()
            self.assertEqual(parser.errors, [])
            self.assertEqual(parser.stack, [], 'Unclosed tags')
            self.assertEqual(len(parser.ids), len(set(parser.ids)), 'Duplicate HTML ids')
            self.assertTrue(set(parser.hrefs).issubset(set(parser.ids)), 'Missing anchor targets')
            self.assertNotRegex(html, r'role=["\']tab(?:list)?["\']')
            self.assertIn('class="we-cases"', html)
            summary = re.search(r'<table class="we-summary we-case-summary">(.*?)</table>', html, re.S)
            self.assertIsNotNone(summary)
            groups = re.findall(r'<tbody\b[^>]*>(.*?)</tbody>', summary.group(1), re.S)
            expected_specified = self.batch1['prepared'] + len(CASE_IDS)
            expected_shown = self.batch1['shown'] + sum(r['shown'] for r in self.records)
            self.assertEqual(len(groups), expected_specified)
            self.assertEqual(len(re.findall(r'<figure\b', html)), expected_shown)
            expected_plots = sum(r['headline'] for e in self.batch1['examples'] for r in e['runs']) + sum(
                len(r['primary_estimates']) for r in self.records if r['shown'])
            self.assertEqual(len(re.findall(r'<svg\b[^>]*class="we-event-study"', html)), expected_plots)
            self.assertNotRegex(html, r'<svg\b[^>]*data-native-width=')
            self.assertLess(len(html.encode('utf-8')), 450_000)
            selection = re.search(r'<p class="we-selection">(.*?)</p>', html, re.S)
            expected_stopped = len(self.batch1['omitted']) + sum(not r['shown'] for r in self.records)
            self.assertEqual(re.findall(r'\d+', selection.group(1)),
                             [str(expected_specified), str(expected_shown), str(expected_stopped)])
            for record in self.records:
                self.assertIn(record['policy'][lang], html)
                self.assertIn('href="#' + record['case_id'] + '-heading"', summary.group(1))
                for row in record['other_estimates'] if record['shown'] else []:
                    self.assertIn(row['label'][lang], html)
                    self.assertIn('data-record-ids="' + ' '.join(row['ids']) + '"', html)
            self.assertIn(self.records[1]['primary_estimates']['second']['outcome'][lang], html)
            plain = re.sub(r'[ \xa0]+', '', html)
            self.assertIn('2000Q1' if lang == 'en' else '2000年第1季度', plain)
            self.assertIn('FY2000' if lang == 'en' else '2000财年', plain)

    def test_summary_is_compact_and_stopped_rows_follow_full_results(self):
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang)
            table = re.search(r'<table class="we-summary we-case-summary">(.*?)</table>', html, re.S).group(1)
            head, body = table.split('</thead>', 1)
            self.assertEqual(len(re.findall(r'<th\b', head)), 3)
            groups = re.findall(r'<tbody\b[^>]*>.*?</tbody>', body, re.S)
            is_stopped = ['we-stopped-row' in group for group in groups]
            self.assertEqual(is_stopped, sorted(is_stopped))
            for group in groups:
                rows = re.findall(r'<tr\b[^>]*>.*?</tr>', group, re.S)
                self.assertEqual(len(re.findall(r'<th\b', group)), 1)
                self.assertEqual(len(re.findall(r'<(?:th|td)\b', rows[0])), 3)
                self.assertTrue(all(len(re.findall(r'<td\b', row)) == 2 for row in rows[1:]))
                if len(rows) > 1:
                    self.assertIn('rowspan="' + str(len(rows)) + '"', rows[0])
                # Each numerical line stays in the same row as its outcome.
                self.assertTrue(all(len(re.findall(r'<td\b', row)) == 2 for row in rows))
                self.assertNotRegex(group, r'<br\s*/?>')
            for key in ('cases.design_size', 'cases.benchmark'):
                dictionary = json.loads((ROOT / 'scripts/viz_site/i18n' / ('worked_examples.' + lang + '.json')).read_text())
                self.assertNotIn(dictionary[key], head)
            # Number rounding must happen in display only, without emitting
            # long decimals in text or removing the visible estimate intervals.
            visible = re.sub(r'<style\b.*?</style>|<[^>]+>', '', html, flags=re.S)
            self.assertNotRegex(visible, r'0\.0*[1-9]\d{3,}')
            self.assertIn('class="we-nowrap"', table)
            self.assertNotIn('log points', visible)
            self.assertNotIn('对数点', visible)
            self.assertNotIn('对数单位', visible)

    def test_stopped_cases_show_computed_primary_results_without_full_cards(self):
        directory = Path(self.temp.name) / 'stopped_cases'
        directory.mkdir()
        records = deepcopy(self.records)
        for record in records:
            if record['case_id'] in ('adult_use_cannabis', 'primary_seat_belt'):
                record.update(status='INTERNAL_ONLY', shown=False,
                              stop_reasons=[pair('Required check failed.', '必需检查未通过。')])
            if record['case_id'] == 'adult_use_cannabis':
                # A four-decimal income interval preserves its negative lower
                # bound. All three values share that display precision.
                main = record['primary_estimates']['main']
                main.update(estimate=.0317, interval=[-.0000609, .0635],
                            unit=pair('log points', '对数点'))
                second = record['primary_estimates']['second']
                second.update(estimate=.064, interval=[.007, .120],
                              unit=pair('log points', '对数点'))
            (directory / (record['case_id'] + '_site.json')).write_text(json.dumps(record))
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang, directory=directory)
            dictionary = json.loads((ROOT / 'scripts/viz_site/i18n' /
                                     ('worked_examples.' + lang + '.json')).read_text())
            status = dictionary['cases.status.stopped']
            for record in records:
                if not record['shown']:
                    identifier = record['case_id']
                    aside = re.search(r'<aside class="we-stopped" id="' + identifier +
                                      r'-heading">(.*?)</aside>', html, re.S).group(1)
                    self.assertIn(status, aside)
                    reason = record['stop_reasons'][0][lang].rstrip('.。')
                    self.assertIn(reason, aside)
                    visible_aside = re.sub(r'<[^>]+>', '', aside)
                    self.assertIn(('。' if lang == 'zh' else '. ') +
                                  escape(dictionary['cases.computed_cs_estimates' if len(record['primary_estimates']) > 1 else 'cases.computed_cs_estimate']) +
                                  ('：' if lang == 'zh' else ': '), visible_aside)
                    self.assertNotIn(dictionary['cases.no_estimate'], aside)
                    self.assertNotRegex(aside, r'<(?:svg|figure|article)\b')
                    self.assertNotIn('<h3 id="' + identifier + '-heading">', html)
                    for row in record['primary_estimates'].values():
                        outcome = row['outcome'][lang]
                        self.assertIn(outcome[:1].lower() + outcome[1:] if lang == 'en' else outcome, visible_aside)
                    group = next(group for group in re.findall(r'<tbody\b[^>]*>.*?</tbody>', html, re.S)
                                 if 'href="#' + identifier + '-heading"' in group)
                    self.assertIn('we-stopped-row', group)
                    self.assertEqual(len(re.findall(r'<tr\b', group)), len(record['primary_estimates']))
            cannabis = re.search(r'<aside class="we-stopped" id="adult_use_cannabis-heading">(.*?)</aside>', html, re.S).group(1)
            visible = re.sub(r'<[^>]+>', '', cannabis).replace('\xa0', ' ')
            self.assertIn('0.0317', visible)
            self.assertIn('−0.0000609', visible)
            self.assertIn('0.0635', visible)
            self.assertIn('0.064', visible)
            self.assertIn('0.007', visible)
            self.assertIn('0.120', visible)

    def test_number_units_convert_only_shares_and_keep_log_differences(self):
        from scripts.viz_site.build import case_display_scale, case_display_number
        log = {'en': 'log points', 'zh': '对数点'}
        for lang, label in (('en', 'log difference'), ('zh', '对数差')):
            self.assertEqual(case_display_scale(log, lang), (1, 3, label))
            factor, decimals, _ = case_display_scale(log, lang)
            self.assertEqual(case_display_number(-.232925 * factor, decimals), '−0.233')
            self.assertEqual([case_display_number(v * factor, decimals) for v in [-.307456, -.158394]],
                             ['−0.307', '−0.158'])
        share = {'en': 'proportion', 'zh': '比例'}
        before = deepcopy(share)
        for lang, label in (('en', 'pp'), ('zh', '百分点')):
            factor, decimals, unit = case_display_scale(share, lang)
            self.assertEqual((factor, decimals, unit), (100, 3, label))
            self.assertEqual(case_display_number(-.00323525 * factor, decimals), '−0.324')
            self.assertEqual(case_display_number(.000471003 * factor, decimals), '0.047')
            self.assertEqual([case_display_number(v * factor, decimals) for v in [-.0226245, .0235665]],
                             ['−2.262', '2.357'])
        self.assertEqual(share, before, 'A display conversion mutated source units')
        self.assertEqual(case_display_number(-.000001, 3), '0.000')

    def test_ranges_and_chinese_punctuation_are_protected_in_prose_only(self):
        from scripts.viz_site.build import case_page_typography
        for lang, fragment in (
                ('en', '<p>Aged 25–54; 2010-2019, WP 2019-62, 1988-07-01.</p>'),
                ('zh', '<p>35–54岁；12 个辖区。0.1 ［−0.2, 0.3］ 对数差。</p>')):
            projected = case_page_typography(fragment, lang)
            self.assertRegex(projected, r'<span class="we-nowrap">(?:25|35)–54')
            if lang == 'zh':
                self.assertIn('\u2060；', projected)
                self.assertIn('0.1［−0.2，0.3］对数差', projected)
                self.assertNotRegex(projected, r'\s［|］\s')
            else:
                for value in ('2010-2019', '2019-62', '1988-07-01'):
                    self.assertIn('<span class="we-nowrap">' + value + '</span>', projected)
        svg = '<svg><path d="M25–54"></path></svg>'
        self.assertEqual(case_page_typography(svg, 'en'), svg)

    def test_zero_tick_and_phone_spacing_across_the_reviewed_event_windows(self):
        from scripts.did_samples.make_site_assets import render_event_study
        from scripts.viz_site.build import lean_event_svg
        ns = {'s': 'http://www.w3.org/2000/svg'}
        for first, last in ((-28, 12), (-34, 30), (-34, 14), (-34, 17), (-18, 14),
                            (-22, 10), (-16, 10), (-2, 3)):
            events = [dict(event_time=t, att=.1, simultaneous95=[0, .2])
                      for t in range(first, last + 1)]
            source = render_event_study(events, title='Fixture', description='All points.',
                event_label='Event time', estimate_label='Estimate', figure_id='ticks', plot_width=368)
            projected = ET.fromstring('<div>' + lean_event_svg(source) + '</div>')[1]
            labels = projected.findall('s:text[@y="194"]', ns)
            self.assertEqual(len(projected.findall('s:circle', ns)), len(events))
            for display_class in ('we-x-wide', 'we-x-narrow'):
                ticks = [label for label in labels if display_class in label.attrib['class']]
                self.assertEqual(sum(label.text == '0' for label in ticks), 1)
                # 320px viewport minus two 11.2px body paddings. An 8.5px glyph
                # upper bound tests the chooser; browser QA must measure
                # the actual text boxes. Keep zero and three readable labels.
                width = 297.6 if display_class == 'we-x-narrow' else 760
                self.assertGreaterEqual(len(ticks), 3)
                for a, b in zip(ticks, ticks[1:]):
                    def x(label):
                        return float(label.attrib['x'].rstrip('%')) / 100 * width + float(label.attrib['dx'])
                    gap = x(b) - x(a) - (len(a.text) + len(b.text)) * 8.5 / 2
                    self.assertGreaterEqual(gap, 28)

    def test_combined_cases_share_the_same_note_order(self):
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang)
            articles = re.findall(r'<article class="we-example".*?</article>', html, re.S)
            self.assertTrue(articles)
            for article in articles:
                figure = article.index('</figure>')
                design = article.index('<p class="we-design">', figure)
                method = article.index('<p class="we-design">', design + 1)
                benchmark = article.index('<p class="we-comparison-line">', method)
                self.assertLess(figure, design)
                self.assertLess(design, method)
                self.assertLess(method, benchmark)
                self.assertNotIn('<p class="we-checks">', article)
                variants = article.find('<table class="we-other-table', benchmark)
                if variants >= 0:
                    self.assertLess(benchmark, variants)

    def test_check_validates_only_isolated_output(self):
        self.page('en')
        checked = self.run_build([*self.args, '--check'])
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('check ok', checked.stdout)

    def test_display_missing_optional_fields_has_no_placeholders(self):
        directory = Path(self.temp.name) / 'minimal_case'
        directory.mkdir(exist_ok=True)
        record = deepcopy(self.records[0])
        record['checks']['calibration'] = None
        # A supported computed point can lack a band; retain its solid mark.
        record['figures']['main']['points'][0]['band'] = None
        for key in ('registered_results', 'visible_disagreements', 'other_estimates_caption'):
            record.pop(key, None)
        for row in list(record['primary_estimates'].values()) + record['other_estimates']:
            for key in ('interval_label', 'comparison_line', 'period_labels', 'time_basis'):
                row.pop(key, None)
            if row.get('kind') != 'range':
                row.pop('calendar_years', None)  # The actual periods inventory is sufficient.
            row['variant_id'] = 'invented_future_variant'
        (directory / (record['case_id'] + '_site.json')).write_text(json.dumps(record), encoding='utf-8')
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang, directory=directory)
            dictionary = json.loads((ROOT / 'scripts/viz_site/i18n' / ('worked_examples.' + lang + '.json')).read_text())
            self.assertIn('data-record-ids="alternate"', html)
            self.assertIn('2000', html)
            # Batch-1 also has event time -2, so scope to the batch-2 article.
            article = html[html.index('<h3 id="cigarette_tax-heading">'):].split('</article>', 1)[0]
            self.assertNotIn(dictionary['cases.checks_unavailable'], article)
            self.assertNotIn(dictionary['cases.unavailable'], article)
            self.assertNotIn('93.75%', article)
            fragment = ET.fromstring('<div>' + ''.join(re.findall(r'<svg\b.*?</svg>', article, re.S)[:2]) + '</div>')
            geometry, svg = list(fragment)
            ns = {'s': 'http://www.w3.org/2000/svg'}
            point = svg.find('s:circle', ns)
            self.assertIsNotNone(point)
            self.assertNotIn('we-hollow', point.attrib['class'])
            # A missing band retains the first point but supplies no errorbar
            # at its x=48 position. The remaining bands are still rendered.
            bars = geometry.findall('.//s:path', ns)
            self.assertTrue(bars)
            self.assertFalse(any(re.search(r'M48 ', bar.attrib['d']) for bar in bars))

    def test_design_and_stopping_records_keep_facts_without_checks_paragraphs(self):
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang)
            self.assertNotIn('<p class="we-checks">', html)
            for record in self.records:
                if not record['shown']:
                    stopped = re.search(r'<aside class="we-stopped" id="' + record['case_id'] +
                                        r'-heading">(.*?)</aside>', html, re.S).group(1)
                    self.assertNotRegex(stopped, r'we-design|we-checks|93\.75|0\.125')
                    self.assertIn(record['stop_reasons'][0][lang].rstrip('.。'), stopped)
                    continue
                article = html[html.index('<h3 id="' + record['case_id'] + '-heading">'):].split('</article>', 1)[0].replace('\xa0', ' ')
                self.assertNotIn('class="we-checks"', article)
                self.assertNotIn('93.75%', article)
                self.assertNotIn('0.125', article)
                self.assertIn('6 jurisdictions adopted in 2 groups' if lang == 'en' else '6 个辖区分 2 组采纳政策', article)
                self.assertIn('5 never-treated comparison jurisdictions' if lang == 'en' else '5 个始终未处理辖区作为对照', article)
                self.assertNotIn('preset limit' if lang == 'en' else '预设上限', article)
                self.assertNotIn('pre-trend size' if lang == 'en' else '处理前趋势幅度', article)
                self.assertNotIn('Not supplied' if lang == 'en' else '未提供', article)

    def test_no_case_sidecars_preserve_legacy_body_and_results(self):
        """Chrome/type may change; batch-1 evidence and body remain compatible."""
        from scripts.did_samples.make_site_assets import (render_policy_fields,
            render_selection_fields, render_closing_fields)
        from scripts.viz_site import build
        empty = Path(self.temp.name) / 'empty_cases'
        empty.mkdir(exist_ok=True)
        selection = adapt_worked_examples(ROOT / 'scripts/did_samples/results')
        original = deepcopy(selection)
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang, directory=empty, batch1=selection)
            errors = build.Errors()
            strings = build.load_dict('worked_examples', lang, errors)
            content = html
            self.assertIn(render_selection_fields(selection, lang=lang, strings=strings), content)
            self.assertIn(render_closing_fields(selection, lang=lang, strings=strings), content)
            self.assertEqual(content.count('<article class="we-example"'), len(selection['examples']))
            for example in selection['examples']:
                fields = render_policy_fields(example, lang=lang, strings=strings)
                self.assertIn('<h2 id="' + fields['id'] + '-heading">' + fields['policy'] + '</h2>', content)
                self.assertIn('<p class="we-lede">' + fields['lede'] + '</p>', content)
                self.assertIn(fields['runs'], content)
                for run in (r for r in example['runs'] if r['headline']):
                    for event in run['events']:
                        self.assertIn('data-event-time="' + str(event['event_time']) + '"', content)
            self.assertNotIn('class="we-cases"', content)
            self.assertEqual(errors.errors, [])
        self.assertEqual(selection, original)

    def test_review_r4_copy_and_wrapping_remain_in_combined_page(self):
        for lang in ('en', 'zh'):
            html = self.legacy_fragment(lang)
            dictionary = json.loads((ROOT / 'scripts/viz_site/i18n' /
                                     ('worked_examples.' + lang + '.json')).read_text())
            selection = re.search(r'<p class="we-selection">(.*?)</p>', html, re.S)[1]
            self.assertNotIn('<br>', selection)
            self.assertNotIn('cases.first_look', dictionary)
            self.assertIn('frozen before estimation' if lang == 'en' else '估计前冻结', selection)
            self.assertNotRegex(html, r'Benchmark: Literature context:|文献对照：文献背景：|计算估计为 ')
            if lang == 'en':
                combined = html.split('<div class="we-cases">', 1)[1].split('</section>', 1)[0]
                self.assertNotIn(' · ', combined)
                self.assertIn('\xa0· ', combined)
        css = (ROOT / 'scripts/viz_site/design/worked_examples_cases.css').read_text()
        self.assertRegex(css, r':is\(p,figcaption,caption\)\{text-wrap:wrap;text-wrap:pretty\}')

    def test_320px_static_stacked_table_containment(self):
        """Fallback contract when no local browser viewport is available."""
        css = (ROOT / 'scripts/viz_site/design/worked_examples_cases.css').read_text()
        self.assertRegex(css, r'@media\s*\(max-width:\s*720px\)')
        self.assertRegex(css, r'\.we-cases[^{}]*\btr\s*\{[^}]*display:\s*(?:grid|flex|block)')
        self.assertRegex(css, r'min-width:\s*0')
        self.assertRegex(css, r'overflow-wrap:\s*break-word')
        self.assertRegex(css, r'\.we-event-study\{[^}]*(?:max-width|width):\s*100%')
        self.assertNotRegex(css, r'\{[^}]*min-width:\s*(?:3[2-9]\d|[4-9]\d\d)px')
        # Desktop column widths must not shrink policy headers in stacked rows.
        self.assertNotRegex(css, r'\.we-cases\s+\.we-case-summary\s+th:nth-child\([^)]*\)\s*\{[^}]*width:')
        self.assertRegex(css, r'\.we-page:lang\(zh\)\s+\.we-cases[^{}]*\{[^}]*overflow-wrap:\s*break-word')
        self.assertRegex(css, r'\.we-cases\s+\.we-nowrap[^{}]*\{[^}]*white-space:\s*nowrap')
        self.assertRegex(css, r'\.we-cases\s+\.we-case-summary\s+a\s*\{[^}]*min-width:\s*44px')

    def test_responsive_geometry_retains_points_bands_and_accessible_copy(self):
        """Check the projection against the source renderer at small/wide widths."""
        from scripts.did_samples.make_site_assets import render_event_study
        from scripts.viz_site.build import lean_event_svg
        events = [dict(event_time=t, att=v, simultaneous95=band,
                       thin_support=thin, band_unavailable=band is None)
                  for t, v, band, thin in [(-2, -.1, [-.2, .1], False),
                                         (-1, 0, [-.1, .1], False),
                                         (0, .2, [.1, .3], False),
                                         (1, .3, [.2, .4], False),
                                         (2, .4, None, True),
                                         (3, .2, None, False)]]
        source = render_event_study(events, title='A < B', description='All recorded points.',
            event_label='Periods relative to adoption', estimate_label='Estimate',
            figure_id='geometry', plot_width=368)
        original = ET.fromstring(source)
        fragment = ET.fromstring('<div>' + lean_event_svg(source) + '</div>')
        self.assertEqual(len(fragment), 2)
        geometry, compact = list(fragment)
        self.assertEqual(geometry.attrib['class'], 'we-chart-geometry')
        self.assertEqual(geometry.attrib['aria-hidden'], 'true')
        self.assertEqual(compact.attrib['class'], 'we-event-study')
        ns = {'s': 'http://www.w3.org/2000/svg'}
        for tag in ('title', 'desc'):
            self.assertEqual(original.find('s:'+tag, ns).text, compact.find('s:'+tag, ns).text)
        old_points = original.findall('.//s:g[@class="we-observation"]', ns)
        new_points = compact.findall('s:circle', ns)
        self.assertEqual(len(old_points), len(new_points))
        for old, new in zip(old_points, new_points):
            before, after = old.find('s:circle', ns), new
            self.assertEqual(float(after.attrib['cy']), round(float(before.attrib['cy']), 1))
            hollow = before.attrib['fill'] == '#ffffff'
            self.assertEqual('we-hollow' in after.attrib['class'].split(), hollow)
            match = re.fullmatch(r'cx:calc\(([-\d.]+)% \+ ([-\d.]+)px\)', after.attrib['style'])
            self.assertIsNotNone(match)
            percent, offset = map(float, match.groups())
            for width in (296, 320, 760):
                actual = width*percent/100 + offset
                expected = 48+(float(before.attrib['cx'])-48)/308*(width-60)
                self.assertAlmostEqual(actual, expected, delta=.06)
                self.assertGreaterEqual(actual-2.7, 0)
                self.assertLessEqual(actual+2.7, width)
        for tag in ('polygon', 'polyline'):
            old_marks = original.findall('.//s:'+tag, ns)
            new_marks = geometry.findall('.//s:'+tag, ns)
            self.assertEqual(len(old_marks), len(new_marks))
            for before, after in zip(old_marks, new_marks):
                for key in ('points', 'x1', 'x2', 'y1', 'y2'):
                    if key in before.attrib:
                        actual = list(map(float, re.findall(r'-?\d+(?:\.\d+)?', after.attrib[key])))
                        expected = [round(float(v), 1) for v in re.findall(r'-?\d+(?:\.\d+)?', before.attrib[key])]
                        self.assertEqual(actual, expected)
        # A thin-support point remains visible and has no newly introduced
        # band vertex, line vertex, or errorbar in the geometry layer.
        for point in old_points:
            mark = point.find('s:circle', ns)
            if mark.attrib['fill'] != '#ffffff':
                continue
            thin_x = round(float(mark.attrib['cx']), 1)
            for tag in ('polygon', 'polyline'):
                for shape in geometry.findall('.//s:' + tag, ns):
                    xs = [float(pair.split(',')[0]) for pair in shape.attrib['points'].split()]
                    self.assertNotIn(thin_x, xs)
            for path in geometry.findall('.//s:path', ns):
                starts = [float(x) for x in re.findall(r'M(-?[\d.]+) ', path.attrib['d'])]
                self.assertNotIn(thin_x, starts)
        # Combining all errorbars by color must keep every original line.
        expected_commands = {'we-pre': [], 'we-post': []}
        for point in old_points:
            for mark in point.findall('s:line', ns):
                values = {key: format(round(float(value), 1), '.1f').rstrip('0').rstrip('.')
                          for key, value in mark.attrib.items() if key in ('x1','x2','y1','y2')}
                color = 'we-pre' if mark.attrib['stroke'] == '#8793a6' else 'we-post'
                expected_commands[color].append(
                    'M{0[x1]} {0[y1]}H{0[x2]}'.format(values) if values['y1'] == values['y2']
                    else 'M{0[x1]} {0[y1]}V{0[y2]}'.format(values))
        for path in geometry.findall('.//s:path', ns):
            color = path.attrib['class'].split()[-1]
            self.assertEqual(path.attrib['d'], ''.join(expected_commands[color]))
        self.assertNotIn('data-event-time', lean_event_svg(source))
        self.assertEqual(len(fragment.findall('.//s:svg', ns)), 2)
        self.assertIsNone(compact.find('s:use', ns))
        use = geometry.find('s:use', ns)
        self.assertIsNotNone(use)
        self.assertNotIn('style', use.attrib)
        self.assertNotIn('x', use.attrib)
        self.assertEqual(use.attrib['width'], '100%')
        self.assertEqual(use.attrib['height'], '100%')
        self.assertEqual(compact.attrib['width'], '100%')
        for mark in geometry.findall('.//s:line', ns) + geometry.findall('.//s:path', ns) + geometry.findall('.//s:polyline', ns):
            self.assertEqual(mark.attrib['vector-effect'], 'non-scaling-stroke')
        css = (ROOT / 'scripts/viz_site/design/worked_examples_cases.css').read_text()
        for cls in ('we-grid', 'we-adopt', 'we-band', 'we-estimate-line', 'we-errorbars'):
            self.assertRegex(css, r'(?m)^\.' + cls + r'(?:[,\{])')
        self.assertRegex(css, r'\.we-band\{[^}]*fill-opacity:\s*\.12')
        self.assertRegex(css, r'svg\.we-chart-geometry\{[^}]*left:48px;right:12px;')


if __name__ == '__main__':
    unittest.main()
