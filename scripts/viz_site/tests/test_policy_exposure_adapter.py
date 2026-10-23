"""Policy exposure rendering invariants, independent of the numeric snapshot."""
import copy
import json
import re
import unittest
from html import escape, unescape
from pathlib import Path
from unittest.mock import patch

from scripts.viz_site.policy_exposure_adapter import Renderer, render_policy_exposure, render_policy_exposure_records, validate

HERE = Path(__file__).resolve().parents[1]


def payload():
    return json.loads((HERE / 'data/policy_exposure.json').read_text())


def strings(lang='en'):
    result = json.loads((HERE / f'i18n/policy_exposure.{lang}.json').read_text())
    result.update(json.loads((HERE / f'i18n/policy_exposure_records.{lang}.json').read_text()))
    return result



class PolicyExposureAdapterTests(unittest.TestCase):
    def test_signing_effective_trace_exposes_the_denominator_and_changed_records(self):
        data = payload()
        before = copy.deepcopy(data)
        for lang in ('en', 'zh'):
            renderer = Renderer(data, lang, strings(lang))
            html = renderer.signing_effective_records()
            rows = re.findall(r'<tr>(.*?)</tr>', html.split('<tbody>', 1)[1], re.S)
            self.assertEqual(len(rows), 129)
            self.assertEqual(html.count('class="lm-changed"'), 24)
            self.assertTrue(all('class="lm-changed"' in row for row in rows[:24]))
            self.assertTrue(all('class="lm-changed"' not in row for row in rows[24:]))
            for row in rows:
                self.assertEqual(len(re.findall(r'<td\b', row)), 6)
                self.assertRegex(row, r'\d{4}-\d{2}-\d{2}')
            self.assertIn('2014-11-04', html)  # Alaska cannabis signing/vote date.
            self.assertIn('2015-02-24', html)  # Its effective date, a changed record.
        self.assertEqual(data, before)
        clocks = data['descriptive']['three_clocks']
        row = next(r for r in clocks['cases'] if r['case_id'] == 'paid_sick_leave')
        row['signing_effective'].update(changed_records=4, share=4 / 7)
        clocks['signing_effective'].update(changed_records=25, share=25 / 129)
        with self.assertRaisesRegex(ValueError, 'trace disagrees'):
            Renderer(data, 'en', strings()).signing_effective_records()

    def test_trace_requires_the_contract_source_snapshot(self):
        data = payload()
        data['provenance']['input_sha256']['data/l4/clock_audit/unit_clocks.csv'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'source snapshot changed'):
            Renderer(data, 'en', strings()).signing_effective_records()

    def test_record_sources_open_new_tabs_and_use_domains(self):
        html = Renderer(payload(), 'en', strings()).source(['https://www.revisor.mn.gov/laws/2005/0/83/', 'https://iga.in.gov/'])
        self.assertIn('target="_blank" rel="noopener"', html)
        self.assertIn('aria-label="Source: revisor.mn.gov"', html)
        self.assertIn('>revisor.mn.gov</a>', html)
        self.assertIn('</a><span class="lm-source-sep" aria-hidden="true"> · </span><a', html)

    def test_empty_event_scopes_have_no_responsive_label_content(self):
        for lang in ('en', 'zh'):
            renderer = Renderer(payload(), lang, strings(lang))
            html = renderer.enforcement_events()
            empty = re.findall(r'<td[^>]+class="lm-empty"[^>]*>(.*?)</td>', html, re.S)
            self.assertTrue(empty)
            self.assertTrue(all(value == '<span class="lm-value"></span>' for value in empty))
            self.assertEqual(html.count('scope="rowgroup" colspan="4"'), 2)

    def test_production_projection_is_renderable(self):
        # Fail when source-reviewed display facts are missing; never fall back
        # to another jurisdiction's threshold or silently alter the data file.
        for lang in ('en', 'zh'):
            render_policy_exposure(payload(), lang, strings(lang))

    def test_record_stacks_reuse_headers_and_keep_source_and_count_meanings(self):
        for lang in ('en', 'zh'):
            labels = strings(lang)
            html = render_policy_exposure_records(payload(), lang, labels)
            def table(anchor):
                return re.search(r'<table id="' + anchor + r'".*?</table>', html, re.S)[0]
            corridor = table('nurse-corridors')
            self.assertIn('<span class="lm-mobile-heading">' + labels['lm.records'] + '</span>', corridor)
            self.assertNotIn('data-label="' + labels['lm.records'] + '"', corridor)
            # Funding categories and event counts need their meaning on phones.
            self.assertIn('data-label="' + labels['lm.funding'] + '"', table('agency-responsibility'))
            self.assertIn('data-label="' + labels['lm.events'] + '"', table('legal-status'))
            for anchor in ('agency-responsibility', 'legal-status', 'policy-lifecycle', 'frozen-predictions'):
                self.assertIn('data-label="' + labels['lm.source'] + '"', table(anchor))

    def test_null_zero_and_stopped_rows(self):
        html = render_policy_exposure(payload(), 'en', strings())
        rows = dict(re.findall(r'data-case="([^"]+)"[^>]*>(.*?)</a>', html, re.S))
        self.assertEqual(len(rows), 13)
        for cid in ('medicaid_expansion', 'primary_seat_belt'):
            self.assertNotIn('lm-track', rows[cid])
            self.assertEqual(rows[cid].count('—'), 2)
        for cid in ('castle_doctrine', 'death_penalty_abolition'):
            self.assertIn('lm-track', rows[cid])
            self.assertNotIn('lm-fill', rows[cid])
            self.assertIn('0%', rows[cid])
        for cid in ('castle_doctrine', 'adult_use_cannabis', 'primary_seat_belt'):
            self.assertIn('Design stopped', rows[cid])
            self.assertIn(f'worked_examples.html#{cid}-heading', html)

    def test_examples_windows_and_period_units(self):
        html = render_policy_exposure(payload(), 'en', strings())
        self.assertIn('2011 Q4–2019 Q3', html)
        self.assertNotIn('20114–20193', html)
        self.assertIn('Fiscal year, July start', html)
        self.assertNotIn('E1', html)
        self.assertNotIn('Pplus', html)
        self.assertNotIn('w1996_2019', html)
        self.assertIn('2001–2019', html)
        self.assertIn('3/7', html)
        self.assertIn('24/129', html)
        self.assertIn('18.6%', html)
        self.assertNotIn('primary windows', html)
        self.assertNotIn('23/126', html)

    def test_changes_come_from_data(self):
        data = payload()
        cases = data['descriptive']['three_clocks']['cases']
        row = next(r for r in cases if r['case_id'] == 'paid_sick_leave')
        row['signing_effective'].update(changed_records=4, share=4/7)
        total = data['descriptive']['three_clocks']['signing_effective']
        total.update(changed_records=25, share=25/129)
        html = render_policy_exposure(data, 'en', strings())
        self.assertIn('25/129', html)
        self.assertIn('19.4%', html)
        self.assertIn('4/7', html)
        self.assertNotIn('24/129', html)

    def test_denominator_inconsistency_fails(self):
        data = payload()
        data['descriptive']['three_clocks']['denominators']['comparable']['records'] += 1
        with self.assertRaisesRegex(ValueError, 'denominators'):
            validate(data)
        data = payload()
        next(r for r in data['descriptive']['three_clocks']['cases'] if r['case_id'] == 'medicaid_expansion')['signing_effective']['share'] = 0
        with self.assertRaisesRegex(ValueError, 'null'):
            validate(data)

    def test_conditional_interpretations_and_dc_missed_match(self):
        html = render_policy_exposure(payload(), 'en', strings())
        self.assertIn('County orders keep 1999', html)
        self.assertIn('Statewide reading', html)
        self.assertIn('2005 law’s uninterrupted retroactivity keeps 2004', html)
        self.assertIn('San Francisco ranks 8', html)
        self.assertIn('0.035', html)
        self.assertIn('records staff contact', html)
        self.assertNotIn('We took a cue', html)
        self.assertNotIn('model_or_source', html)
        data = payload()
        data['descriptive']['three_clocks']['enforcement_examples'][1]['scope_conditional'] = False
        with self.assertRaisesRegex(ValueError, 'conditional'):
            validate(data)

    def test_escaping_and_source_urls(self):
        data = payload()
        data['descriptive']['paid_sick_leave']['lineage']['rows'][0]['closest_earlier_text']['name'] = '<script>bad</script>'
        html = render_policy_exposure(data, 'en', strings())
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', html)
        self.assertNotIn('<script>', html)
        data['descriptive']['paid_sick_leave']['clause_rows'][0]['source_urls'] = ['javascript:alert(1)']
        with self.assertRaisesRegex(ValueError, 'unsafe source'):
            render_policy_exposure(data, 'en', strings())

    def test_public_allowlist_and_static_markup(self):
        data = payload()
        data['provenance']['private_secret'] = 'DO_NOT_RENDER_PRIVATE'
        data['descriptive']['paid_sick_leave']['external_projection_agreement']['row_level_data'] = 'DO_NOT_RENDER_RESTRICTED'
        html = render_policy_exposure(data, 'en', strings())
        for value in ('DO_NOT_RENDER_PRIVATE', 'DO_NOT_RENDER_RESTRICTED', 'input_sha256', '/Users/', 'fetch(', 'pre-registered', 'borrowed', 'blind'):
            self.assertNotIn(value, html)
        self.assertEqual(html.count('<details '), 1)
        self.assertNotIn('<details open', html)
        self.assertEqual(html.count('<h1 '), 1)
        self.assertEqual(html.count('<h2 '), 2)
        self.assertEqual(html.count('<h3 '), 2)

    def test_audited_clause_wording_and_lineage(self):
        html = render_policy_exposure(payload(), 'en', strings())
        for text in ('2014 original', 'Employer may limit use to 24 hours or 3 days', 'Workers in the listed occupations at employers with 50+', 'printed 2015 edition', '0.621', '0.164'):
            self.assertIn(text, html)
        self.assertNotIn('printed 2016', html)
        self.assertIn('2014-09-10', json.dumps(payload()))

    def test_source_event_dates_precision_and_scope(self):
        data = payload()
        html = render_policy_exposure_records(data, 'en', strings())
        for date in ('2003-04-28', '2003-05-28', '2003-06-06', '2004-07-13', '2005-04-12', '2005-05-24', '2005-05-25', '1998-07-01', '1998-12', '1999-07-06'):
            self.assertIn(date, html)
        self.assertIn('Year recorded', html)
        self.assertIn('By 1998-12', html)
        self.assertIn('Inferred bound', html)
        self.assertIn('Religious organizations', html)
        self.assertIn('permitting scheme continued', html)
        self.assertIn('Hendricks County prosecutor and sheriff', html)
        self.assertNotIn('1998-12-01', html)
        self.assertNotIn('1998-12-31', html)
        self.assertNotIn('Effective-clock cohort', html)
        mn, indiana = data['descriptive']['three_clocks']['enforcement_examples']
        self.assertIsNone(indiana['exact_injunction_date'])
        self.assertIsNone(indiana['signing_date'])
        event = next(e for e in mn['events'] if e['kind'] == 'struck')
        event['date'] = '2004-07-14'
        changed = render_policy_exposure_records(data, 'en', strings())
        self.assertIn('2004-07-14', changed)
        self.assertNotIn('2004-07-13', unescape(re.sub('<[^>]+>', '', changed)))
        event['date'] = None
        missing = render_policy_exposure(data, 'en', strings())
        timeline = missing.split('<ol class="lm-milestones">', 1)[1].split('</ol>', 1)[0]
        self.assertNotIn('Struck down', timeline)
        self.assertNotIn('None', missing)

    def test_desktop_date_positions_and_uncertain_order_span(self):
        data = payload()
        html = render_policy_exposure(data, 'en', strings())
        timelines = re.findall(r'<div class="lm-timeline"[^>]*>(.*?)</div>', html, re.S)
        self.assertEqual(len(timelines), 2)
        self.assertIn('Signed · effective', timelines[0])
        self.assertIn('Apr / May 2003', timelines[0])
        expected = 100 * (18 + 12 / 31) / 36
        self.assertIn(f'left:{expected:.6f}%', timelines[0])
        self.assertIn('lm-off', timelines[0])
        self.assertIn('lm-unknown', timelines[1])
        self.assertIn('1998 order', timelines[1])
        self.assertIn('Day unknown', timelines[1])
        self.assertNotIn('1998-12', timelines[1])
        self.assertNotIn('Inferred month', timelines[1])
        self.assertIn('Jul 1999', timelines[1])
        bounds = data['descriptive']['three_clocks']['enforcement_examples'][1]['injunction_date_bounds']
        self.assertTrue(bounds['bounds_are_not_observed_days'])
        bounds['bounds_are_not_observed_days'] = False
        with self.assertRaisesRegex(ValueError, 'not be observed days'):
            render_policy_exposure(data, 'en', strings())

    def test_three_year_axes_equal_month_spacing_and_original_span_end(self):
        data = payload()
        before = copy.deepcopy(data)
        for lang in ('en', 'zh'):
            renderer = Renderer(data, lang, strings(lang))
            html = renderer.render()
            axes = re.findall(r'data-axis-start="([^"]+)" data-axis-end="([^"]+)"', html)
            self.assertEqual(axes, [('2003-01-01', '2006-01-01'), ('1998-01-01', '2001-01-01')])
            timelines = re.findall(r'<div class="lm-timeline"[^>]*>(.*?)</div>', html, re.S)
            for timeline in timelines:
                ticks = re.findall(r'class="lm-tick[^"]*" style="left:([\d.]+)%"', timeline)
                self.assertEqual(ticks, ['0.000000', '33.333333', '66.666667', '100.000000'])
            # Indiana's final legal span still ends at the exported 2000 cohort,
            # rather than silently adding another year of enforcement.
            spans = re.findall(r'class="lm-span " style="left:([\d.]+)%;width:([\d.]+)%"', timelines[1])
            self.assertAlmostEqual(sum(map(float, spans[-1])), 200 / 3, places=5)
            render_policy_exposure_records(data, lang, strings(lang))
        self.assertEqual(data, before)
        renderer = Renderer(data, 'en', strings())
        gap = copy.deepcopy(data['descriptive']['three_clocks']['enforcement_examples'][0])
        path = 'descriptive.three_clocks.enforcement_examples.0'
        positions = []
        for effective in ('2003-02-01', '2003-03-01', '2003-04-01'):
            gap['effective_date'] = effective
            timeline = renderer.timeline(gap, path)
            positions.append(float(re.search(r'class="lm-mark" style="left:([\d.]+)%"', timeline).group(1)))
        for previous, current in zip(positions, positions[1:]):
            self.assertAlmostEqual(current - previous, 100 / 36, places=5)

    def test_column_heading_words_are_relocated_without_ratio_changes(self):
        for lang in ('en', 'zh'):
            dictionary = strings(lang)
            html = Renderer(payload(), lang, dictionary).main_figure()
            self.assertIn('<span class="lm-share-heading">' + dictionary['lm.share.changed'] + '</span>', html)
            self.assertIn('<span class="lm-count-heading">' + dictionary['lm.share.comparable'] + '</span>', html)
            self.assertIn('style="width:42.857143%"', html)
            self.assertIn('3/7', html)

    def test_audited_scope_consumed_without_changing_dispute(self):
        data = payload()
        ca = next(r for r in data['descriptive']['paid_sick_leave']['clause_rows'] if r['statute_id'] == 'CA_enacted')
        ct = next(r for r in data['descriptive']['paid_sick_leave']['clause_rows'] if r['statute_id'] == 'CT_enacted')
        self.assertEqual(ct['clauses']['employer_coverage']['status'], 'unresolved')
        ca['audited_scope'].update(use_limit_days=4, use_limit_hours=32)
        ct['audited_scope']['min_employees'] = 60
        html = render_policy_exposure(data, 'en', strings())
        self.assertIn('limit use to 32 hours or 4 days', html)
        self.assertIn('Workers in the listed occupations at employers with 60+', html)
        self.assertEqual(ct['clauses']['employer_coverage']['status'], 'unresolved')
        ca['audited_scope']['source_urls'] = ['javascript:alert(1)']
        with self.assertRaisesRegex(ValueError, 'unsafe source'):
            render_policy_exposure(data, 'en', strings())

    def test_bilingual_key_slots_and_copy_budget(self):
        en, zh = strings(), strings('zh')
        self.assertEqual(set(en), set(zh))
        for key in en:
            self.assertEqual(set(re.findall(r'\{(\w+)\}', en[key])), set(re.findall(r'\{(\w+)\}', zh[key])), key)
        for lang, dictionary in (('en', en), ('zh', zh)):
            html = render_policy_exposure(payload(), lang, dictionary)
            prose = ' '.join(unescape(re.sub('<[^>]+>', '', p)) for p in re.findall(r'data-prose="[^"]+">(.*?)</(?:h1|h2|h3|p|figcaption)>', html, re.S))
            if lang == 'en':
                count = len(prose.split())
                self.assertTrue(130 <= count <= 170, count)
                self.assertNotRegex(html, r'[\u3400-\u9fff]')
            else:
                count = len(re.sub(r'\s', '', prose))
                # The single L3 definition replaces the old subtitle (+6 characters).
                self.assertTrue(220 <= count <= 306, count)

    def test_records_migration_and_direct_links(self):
        expected = {'enforcement-events': 14, 'nurse-corridors': 5,
                    'agency-responsibility': 18, 'policy-lifecycle': 3,
                    'frozen-predictions': 12, 'legal-status': 31}
        for lang in ('en', 'zh'):
            main = render_policy_exposure(payload(), lang, strings(lang))
            records = render_policy_exposure_records(payload(), lang, strings(lang))
            details = main.split('<details ', 1)[1]
            self.assertEqual(details.count('<table'), 3)
            self.assertEqual(main.count('<dt>'), 8)
            self.assertEqual(records.count('<table'), 7)
            self.assertNotIn('<details', records)
            self.assertNotIn('fetch(', records)
            self.assertNotIn('lm-records-back', records)
            for anchor, rows in expected.items():
                self.assertIn(f'href="/viz/{lang}/policy_exposure_records.html#{anchor}"', main)
                body = records.split(f'<table id="{anchor}"', 1)[1].split('</table>', 1)[0]
                self.assertEqual(body.count('<tbody>'), 2 if anchor == 'enforcement-events' else 1)
                self.assertEqual(body.split('<tbody>', 1)[1].count('<tr>'), rows)
            self.assertEqual(records.count(strings(lang)['lm.awaiting.data']), 1)

    def test_csv_event_date_injection_through_exporter_and_both_renderers(self):
        from scripts.l4.page import export_policy_exposure as export
        attack = '<script>alert(1)</script><img src=x onerror="alert(2)">'
        original_csv = export.Inputs.csv
        def contaminated(inputs, path):
            rows = original_csv(inputs, path)
            if path == export.L4 + 's3_verify/events_verified.csv':
                for row in rows:
                    if row['jurisdiction'] == 'Arkansas' and row['design_use'] == 'stock_flow_case_candidate':
                        row['event_date'] = attack
            return rows
        with patch.object(export.Inputs, 'csv', contaminated):
            data = export.build_contract()
        dates = [event['date'] for card in data['descriptive']['policy_lifecycle']['cards']
                 for event in card['ending_events']]
        self.assertIn(attack, dates)  # Proves the real CSV -> contract path was exercised.
        for lang in ('en', 'zh'):
            for render_page in (render_policy_exposure, render_policy_exposure_records):
                html = render_page(data, lang, strings(lang))
                self.assertNotIn('<script>', html)
                self.assertNotIn('<img', html)
                self.assertNotIn('onerror="alert(2)"', html)
                if render_page is render_policy_exposure_records:
                    self.assertIn(escape(attack), html)
            # Migration intentionally removes this date from the main page;
            # its result link must still resolve to the renderer that escaped it.
            self.assertIn(f'/viz/{lang}/policy_exposure_records.html#policy-lifecycle',
                          render_policy_exposure(data, lang, strings(lang)))

    def test_contextual_clause_units_and_obligations(self):
        data = payload()
        main = render_policy_exposure(data, 'en', strings())
        self.assertIn('7 days at 100+ employees; 5 days at 25–99; 3 days at 0–24', main)
        self.assertIn('Paid sick leave not required', main)
        self.assertIn('Carryover required', main)
        self.assertIn('Business location in a city with more than 500,000 residents', main)
        self.assertNotIn('1.3333333333333333', main)
        self.assertIn('2018 amended baseline (PA369)', main)
        self.assertIn('San Francisco ranks 8 of 9, score 0.035', main)
        self.assertEqual(main.count('Template-level text overlap'), data['descriptive']['paid_sick_leave']['lineage']['tier_counts']['D'])  # Full matrix tiers remain.

    def test_event_rows_bind_sources_to_dates(self):
        renderer = Renderer(payload(), 'en', strings())
        events = renderer.enforcement_events()
        rows = re.findall(r'<tr>(.*?)</tr>', events, re.S)
        mn_first = [row for row in rows if '2003-04-28' in row]
        self.assertEqual(len(mn_first), 1)
        self.assertEqual(re.findall(r'href="([^"]+)"', mn_first[0]),
                         ['https://www.revisor.mn.gov/laws/2003/0/28/'])
        self.assertIn('By 1998-12', events)
        self.assertIn('Inferred bound', events)
        data = payload()
        data['descriptive']['three_clocks']['enforcement_examples'][0]['events'] = [
            event for event in data['descriptive']['three_clocks']['enforcement_examples'][0]['events']
            if event['event_id'] != 'W2V-036']
        fallback = Renderer(data, 'en', strings()).enforcement_events()
        self.assertEqual(fallback.count('2003-04-28'), 1)

    def test_status_law_count_is_separate_and_data_bound(self):
        data = payload()
        html = render_policy_exposure(data, 'en', strings())
        self.assertIn('102 events across 31 laws; 21 have primary-document', html)
        data['descriptive']['selected_legal_status']['law_event_groups'].pop()
        self.assertIn('102 events across 30 laws; 21 have primary-document',
                      render_policy_exposure(data, 'en', strings()))

    def test_oregon_unpaid_general_scope_has_city_complement(self):
        main = render_policy_exposure(payload(), 'en', strings())
        self.assertIn('0–9 employees · No business location in a city with more than 500,000 residents', main)
        self.assertIn('Coders differ', main)
        self.assertIn('Two independent coders agree exactly on 308/470 stated paired fields.', main)
        self.assertIn('两位独立编码员在 470 个已陈述配对字段中，有 308 个完全一致。',
                      render_policy_exposure(payload(), 'zh', strings('zh')))

    def test_lifecycle_clock_types_are_displayed_and_escaped(self):
        html = render_policy_exposure_records(payload(), 'en', strings())
        for text in ('2014-09-12 · Effective · Freeze',
                     '2019-10-22 · Effective · Weakened and sunset',
                     '2017-03-30 · Signed · State preemption',
                     '2019-03-27 · Court vacatur'):
            self.assertIn(text, html)
        self.assertNotIn('Court · Court', html)

    def test_recomputed_candidates_and_old_later_comparisons_remain_visible(self):
        data = payload()
        for lang in ('en', 'zh'):
            html = render_policy_exposure(data, lang, strings(lang))
            for score in ('0.267', '0.350', '0.302', '0.424'):
                self.assertIn(score, html)
            self.assertNotIn(strings(lang)['lm.lineage.date.disputed'], html)
            self.assertIn('Later-text comparison' if lang == 'en' else '较晚文本比较', html)
            self.assertNotIn('Agency coding is paused.' if lang == 'en' else '机构字段编码暂停。', render_policy_exposure_records(data, lang, strings(lang)))
            table = html.split('<table id="clause-matrix"', 1)[1].split('</table>', 1)[0]
            ct = next(row for row in re.findall(r'<tr>(.*?)</tr>', table, re.S) if '>CT<' in row)
            cells = re.findall(r'<td[^>]*>(.*?)</td>', ct, re.S)
            self.assertIn('40', cells[2])
            self.assertNotIn(strings(lang)['lm.ct.scope'].split('{threshold}')[0], cells[2])
            self.assertIn(strings(lang)['lm.ct.scope'].split('{threshold}')[0], cells[3])
            self.assertIn('Coders differ' if lang == 'en' else '编码分歧', cells[3])

    def test_missing_candidate_rows_use_dashes_and_separate_index_counts(self):
        for status in ('date_order_unresolved', 'no_eligible_candidates'):
            data = payload()
            lineage = data['descriptive']['paid_sick_leave']['lineage']
            # Use a featured row as well, so the closed summary must tolerate absence.
            row = next(r for r in lineage['rows'] if r['statute_id'] == 'AZ_enacted')
            row.update(closest_earlier_text=None, temporal_status=status)
            row['target_text_date']['status'] = 'missing'
            lineage['tier_counts']['D'] -= 1
            lineage['temporal_counts']['resolved'] -= 1
            lineage['temporal_counts'][status] += 1
            for lang in ('en', 'zh'):
                html = render_policy_exposure(data, lang, strings(lang))
                self.assertIn(strings(lang)['lm.lineage.' + status], html)
                self.assertIn(strings(lang)['lm.index.lineage.' + status].format(count=1), html)
                table = re.search(r'<table[^>]*lm-lineage[^>]*>(.*?)</table>', html, re.S).group(1)
                cells = [re.findall(r'<td[^>]*>(.*?)</td>', r, re.S) for r in re.findall(r'<tr>(.*?)</tr>', table, re.S)]
                self.assertEqual(sum(strings(lang)['lm.tier.none'] in c[3] for c in cells if c), lineage['tier_counts']['none'])
                az_cells = next(c for c in cells if c and '>AZ<' in c[0])
                self.assertEqual([unescape(re.sub('<[^>]+>', '', c)) for c in az_cells[2:4]], ['—', '—'])
                self.assertNotIn('None', html)

    def test_unresolved_text_date_dispute_remains_visible(self):
        data = payload()
        lineage = data['descriptive']['paid_sick_leave']['lineage']
        row = next(r for r in lineage['rows'] if r['statute_id'] == 'AK_enacted')
        self.assertEqual(row['target_text_date']['status'], 'disputed')
        tier = row['closest_earlier_text']['evidence_tier']
        row.update(closest_earlier_text=None, temporal_status='date_order_unresolved')
        lineage['tier_counts'][tier] -= 1
        lineage['temporal_counts']['resolved'] -= 1
        lineage['temporal_counts']['date_order_unresolved'] += 1
        for lang in ('en', 'zh'):
            html = render_policy_exposure(data, lang, strings(lang))
            self.assertIn(strings(lang)['lm.lineage.date.disputed'], html)
            self.assertIn(strings(lang)['lm.lineage.date_order_unresolved'], html)

    def test_summary_numbers_and_comparison_object_follow_data(self):
        data = payload()
        az = next(r for r in data['descriptive']['paid_sick_leave']['lineage']['rows'] if r['statute_id'] == 'AZ_enacted')
        az['closest_earlier_text'].update(id='NEW_earlier_ordinance', name='Collected city ordinance', similarity_score=.55)
        html = render_policy_exposure(data, 'en', strings())
        summary = html.split('data-prose="clauses.summary">', 1)[1].split('</p>', 1)[0]
        self.assertIn('Collected city ordinance', summary)
        self.assertIn('0.550', summary)
        self.assertNotIn('a model act', summary)

    def test_numeric_ledger_has_resolvable_json_paths(self):
        from scripts.viz_site.policy_exposure_adapter import get_path
        data = payload()
        renderer = Renderer(data, 'en', strings())
        renderer.render()
        self.assertGreater(len(renderer.numbers), 100)
        for item in renderer.numbers:
            self.assertIsNotNone(get_path(data, item['json_path']), item)


if __name__ == '__main__':
    unittest.main()
