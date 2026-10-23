"""Offline membership, evidence and safe-render checks for Screening V5."""
import json
from pathlib import Path
import re
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restructure_screening as screening


class ScreeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = screening.load_data()

    def test_one_member_per_policy_and_count_binding(self):
        data = self.data
        ids = [r['id'] for r in data['members']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), data['count'])
        audit = json.loads((screening.ROOT / 'paper/aej_latest/COUNT_AUDIT.json').read_text())
        self.assertEqual(len(ids), audit['derived']['unique_first_viable_strict_1'])
        self.assertEqual(data['total'], audit['main']['unique_policy_ids'])
        self.assertEqual(data['federal'], audit['shortlist_composition']['source_family=Federal Regulations'])

    def test_raw_recall_and_intervals_match_bound_table(self):
        for row in self.data['recall']:
            self.assertEqual(round(100 * row['k'] / row['n'], 1), row['rate'])
            self.assertLess(row['low'], row['rate'])
            self.assertLess(row['rate'], row['high'])
        self.assertEqual([r['design'] for r in self.data['recall']], ['DiD', 'RD'])

    def test_recall_comes_from_the_run_behind_the_abstract_for_did_and_rd(self):
        # Owner, 2026-10-14 (option C): both rows from the abstract's run (87.6 / 70.6), none from Table 1 (76.1 / 88.2).
        self.assertEqual([(r['design'], r['k'], r['n'], r['rate'], r['low'], r['high']) for r in self.data['recall']],
                         [('DiD', 99, 113, 87.6, 80.3, 92.5), ('RD', 12, 17, 70.6, 46.9, 86.7)])
        self.assertEqual(self.data['model'], 'glm-5.3-flash')
        self.assertEqual(self.data['draft'], 'September 30, 2026')

    def test_wilson_reproduces_the_intervals_the_site_showed_for_table_1(self):
        shown = lambda k, n: tuple(float(f'{value:.1f}') for value in screening.wilson(k, n))
        self.assertEqual(shown(86, 113), (67.5, 83.0))
        self.assertEqual(shown(15, 17), (65.7, 96.7))

    TABLE = '''
**Benchmark (positive-only rediscovery recall; DID /113, RD /17; paper level)**

| metric | model-a | model-b |
|---|---|---|
| DID recall /113 | 99 = 87.6% (106) | 86 = 76.1% |
| RD recall /17 | 12 = 70.6% (13) | 15 = 88.2% |

**Validity**
'''

    def test_pilot_recall_takes_the_column_that_equals_the_abstract_and_fails_closed(self):
        rows, model = screening.pilot_recall(self.TABLE, [87.6, 70.6], [113, 17])
        self.assertEqual((model, rows[0]['k'], rows[1]['k']), ('model-a', 99, 12))
        self.assertEqual(screening.pilot_recall(self.TABLE, [76.1, 88.2], [113, 17])[1], 'model-b')
        for table, abstract, positives, message in (
                (self.TABLE, [87.6, 88.2], [113, 17], 'no single model column'),
                (self.TABLE, [87.6, 70.6], [113, 18], 'benchmark size'),
                (self.TABLE.replace('99 = 87.6%', '98 = 87.6%'), [87.6, 70.6], [113, 17], 'percentage'),
                (self.TABLE.replace('Benchmark (positive-only', 'Benchmark (other'), [87.6, 70.6], [113, 17], 'format changed')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                screening.pilot_recall(table, abstract, positives)

    PRECISION_TABLE = '''
**fresh158 vs final adjudicated label, majority (>=2 of 3 runs)**

| metric | model-a | model-b |
|---|---|---|
| DID precision % | 58.7 [49,68] | 62.1 [49,73] |
| DID recall % | 90.1 [81,95] | 50.7 [39,62] |
| DID TP/FP/FN/TN (n pos=71/155) | 64/45/7/39 | 36/22/35/62 |
| RD precision % | 42.2 [29,57] | 44.1 [33,56] |
| RD TP/FP/FN/TN (n pos=32/156) | 19/26/13/98 | 30/38/2/86 |

**fresh158 vs final adjudicated label, any-of-3**

| metric | model-a | model-b |
|---|---|---|
| DID precision % | 53.2 [44,62] | n/a |
| DID TP/FP/FN/TN (n pos=71/155) | 66/58/5/26 | n/a |
| RD precision % | 36.7 [26,49] | n/a |
| RD TP/FP/FN/TN (n pos=32/156) | 22/38/10/86 | n/a |
'''

    def test_pilot_precision_takes_the_named_column_of_the_first_block_and_fails_closed(self):
        self.assertEqual(screening.pilot_precision(self.PRECISION_TABLE, 'model-a'), [58.7, 42.2])
        self.assertEqual(screening.pilot_precision(self.PRECISION_TABLE, 'model-b'), [62.1, 44.1])
        for table, model, message in (
                (self.PRECISION_TABLE, 'model-c', 'format changed'),
                (self.PRECISION_TABLE.replace('58.7 [49,68]', '58.8 [49,68]'), 'model-a', 'its counts disagree'),
                (self.PRECISION_TABLE.replace('64/45/7/39', '0/0/7/39').replace('58.7 [49,68]', '0.0 [0,0]'), 'model-a', 'its counts disagree'),
                (self.PRECISION_TABLE.replace('| DID precision % | 58.7', '| DID precision (%) | 58.7'), 'model-a', 'format changed'),
                (self.PRECISION_TABLE.replace('final adjudicated label, majority', 'other label, majority'), 'model-a', 'format changed')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                screening.pilot_precision(table, model)

    def test_rendered_rows_and_version_date(self):
        # R29 (SC8, SC11): "Revised paper". F6 (Liebman S7): the source line no longer says "not yet public".
        for lang, caption, source in (('en', 'Revised paper · September 30, 2026; the candidate list',
                                       'Source: revised paper (abstract, Appendix D) and model-comparison table, dated September 30, 2026.'),
                                      ('zh', '修订稿 · 2026 年 9 月 30 日；', '来源：修订稿（摘要、附录 D）及模型比较表，日期：2026 年 9 月 30 日。')):
            page = screening.render(lang)
            visible = re.sub(r'<(style|script)\b.*?</\1>', '', page, flags=re.S)
            for text in ('99/113 · 87.6% <small>[80.3, 92.5]</small>', '12/17 · 70.6% <small>[46.9, 86.7]</small>',
                         'style="left:80.3%;width:12.2%"', 'style="left:87.6%"', 'style="left:46.9%;width:39.8%"', 'style="left:70.6%"',
                         caption, source):
                self.assertIn(text, visible)
            for old in ('86/113', '15/17', '76.1', '88.2', '67.5', '83.0', '65.7', '96.7', 'September 22', '9月22日', 'Tables 2–3', '表 2–3'):
                self.assertNotIn(old, visible)

    def test_precision_is_the_recall_runs_and_the_coverage_clause_is_gone(self):
        self.assertEqual(self.data['precision'], [58.7, 42.2])
        self.assertNotIn('coverage', self.data)
        note = (screening.ROOT / 'paper/aej_latest/SOURCE.txt').read_text()
        source = Path(re.search(r'^Source folder: (.+)$', note, re.M)[1])
        first = (source.parent / screening.PILOT_TABLE).read_text().split('**fresh158 vs final adjudicated label, majority', 1)[1].split('\n**', 1)[0]
        self.assertIn('| metric | glm-5.3-flash |', first)
        for printed in ('| DID precision % | 58.7 [49,68] |', '| RD precision % | 42.2 [29,57] |', '| DID TP/FP/FN/TN (n pos=71/155) | 64/45/', '| RD TP/FP/FN/TN (n pos=32/156) | 19/26/'):
            self.assertIn(printed, first)
        self.assertRegex((source / 'main.tex').read_text(), r'and\s+58\.7\s+percent\s+of\s+those\s+agree')

    def test_precision_sentence_and_score_order(self):
        scores = [row['score'] for row in self.data['members']]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for lang, sentence in (('en', 'Precision in the research sample: DiD 58.7%; RD 42.2%.'), ('zh', '研究样本中的精确率：DiD 58.7%；RD 42.2%。')):
            page = screening.render(lang)
            visible = re.sub(r'<(style|script)\b.*?</\1>', '', page, flags=re.S)
            self.assertNotIn('Qwen 3.6 Plus', page)
            self.assertNotIn('GLM 5.2', page)
            self.assertIn('<p class="sc-deployment">' + sentence + '</p>', visible)
            for old in ('62.1', '44.1', '70/155', '150/156', 'Classification coverage', '分类覆盖率'):
                self.assertNotIn(old, visible)
            self.assertIn('abstract_id=6487478', page)

    def test_paper_title_sits_on_the_benchmark_source_line_unlinked(self):
        # Owner, 2026-10-13/14: the paper title moves from Home to Screening.
        self.assertEqual(self.data['title'], 'Finding Causal Variation in Policy Text with Large Language Models')
        for lang, title in (('en', self.data['title']), ('zh', '用大语言模型从政策文本中寻找因果变异')):
            page = screening.render(lang)
            visible = re.sub(r'<(style|script)\b.*?</\1>', '', page, flags=re.S)
            self.assertEqual(visible.count(title), 1)
            line = re.search(r'<p class="sc-muted">' + re.escape(title) + r'<br>(.*?)</p>', visible, re.S)
            self.assertIsNotNone(line)
            self.assertNotIn('<a', line[1])
            self.assertLess(visible.index('id="sc-benchmark-title"'), visible.index(title))
            self.assertLess(visible.index(title), visible.index('class="sc-recall"'))
            for added in ('AEJ', 'University of Southern California', '南加州大学'):
                self.assertNotIn(added, visible)

    def test_method_box_says_each_thing_once_and_the_results_label_has_no_classifier(self):
        # R27, arbiter O2: "AI proposes the research design and scores." is the third statement of one fact and the source line's
        # "one entry per policy" repeats the first sentence of the box. Copy gate O4: the Chinese results label has no number to count,
        # and the link to the Adoption page names its destination. R29 (SC10 fallback, SC12): the box holds the access limit, the paper
        # sources and one line for the database and its update date; the rules paragraph is gone (the rules are on Method).
        count = f'{self.data["count"]:,}'
        for lang, expected in (('en', {'box': ('Source: revised paper', 'Policy database, last updated '), 'gone': ('AI proposes', 'September 30, 2026; one entry', 'We keep one entry per policy'),
                                       'label': 'candidates', 'count': f'{count} candidates', 'link': 'Documented state-policy adoptions →'}),
                               ('zh', {'box': ('来源：修订稿', '政策数据库，更新日期：'), 'gone': ('研究设计与评分由 AI 提出', '日；每项政策一条记录', '每项政策只保留一条记录'),
                                       'label': '候选记录', 'count': f'{count} 条候选记录', 'link': '有公开记录的州政策采纳 →'})):
            page = screening.render(lang)
            visible = re.sub(r'<(style|script)\b.*?</\1>', '', page, flags=re.S)
            box = re.search(r'<details class="sc-method".*?</details>', visible, re.S)[0]
            for text in expected['box']:
                self.assertIn(text, box)
            for text in expected['gone']:
                self.assertNotIn(text, visible)
            self.assertNotIn('<summary>Screening method</summary>' if lang == 'en' else '<summary>筛查方法</summary>', visible)
            self.assertIn(f'<section aria-label="{expected["label"]}">', visible)
            self.assertIn(expected['count'], visible)
            self.assertIn(expected['link'], visible)
        self.assertNotIn('有文献记录', screening.render('zh'))

    def test_r29_trim_header_composition_reset_and_method_box(self):
        # R29 (TRIM_SPEC_V51.md): SC1 no kicker line; SC2 and SC3 are kept by the section 4 fallbacks (the subtitle, "coverage varies by state");
        # SC7 "Reset"; SC9 one composition line with both percentages and both fractions, none in the closed box; SC10 fallback: the box keeps
        # only the access limit; SC11 and SC12 two source lines and one line for the database and its update date.
        federal, count, bills, total = (f'{self.data["federal"]:,}', f'{self.data["count"]:,}', self.data['bills'], self.data['benchmark_total'])
        pct = (round(100 * self.data['federal'] / self.data['count']), round(100 * bills / total))
        expected = {'en': {'subtitle': 'A candidate list for manual review.', 'sampling': 'coverage varies by state.', 'reset': '>Reset</button>',
                           'line': f'Federal regulations are {pct[0]}% of the candidate list ({federal}/{count}); bills are {pct[1]}% of the published-design sample ({bills}/{total}).',
                           'access': 'Source: revised paper', 'summary': '<summary>Sources</summary>',
                           'update': 'Policy database, last updated <time datetime="2026-09-30">September 30, 2026</time>.'},
                    'zh': {'subtitle': '待人工核查的候选清单。', 'sampling': '各州覆盖不均。', 'reset': '>重置</button>',
                           'line': f'联邦法规占候选清单的 {pct[0]}%（{federal}/{count}）；法案占已发表研究基准样本的 {pct[1]}%（{bills}/{total}）。',
                           'access': '来源：修订稿', 'summary': '<summary>来源</summary>',
                           'update': '政策数据库，更新日期：<time datetime="2026-09-30">2026 年 9 月 30 日</time>。'}}
        for lang, want in expected.items():
            visible = re.sub(r'<(style|script)\b.*?</\1>', '', screening.render(lang), flags=re.S)
            box = re.search(r'<details class="sc-method".*?</details>', visible, re.S)[0]
            self.assertNotIn('sc-kicker', visible)
            for key in ('subtitle', 'sampling', 'reset', 'line'):
                self.assertIn(want[key], visible, (lang, key))
            for key in ('access', 'summary', 'update'):
                self.assertIn(want[key], box, (lang, key))
            self.assertEqual(visible.count(f'{federal}/{count}'), 1)  # said once, not again in the closed box
            self.assertNotIn(f'{bills}/{total} bills' if lang == 'en' else f'{bills}/{total} 项法案', visible)
            self.assertEqual(box.count('<p'), 3)  # source, earlier draft, database and update date (F6: the access line is gone)
            self.assertNotIn('has not been verified', box)
            self.assertNotIn('尚未核实', box)
            self.assertNotIn('not yet public', box)
            self.assertNotIn('尚未公开', box)
        self.assertNotIn('Last updated:', screening.render('en'))

    def test_paper_title_must_match_the_paper_source(self):
        changed = dict(self.data, title='Another title')
        with patch.object(screening, 'load_data', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'paper title'):
                screening.render('en')

    def test_public_url_is_not_executable_or_credential_bearing(self):
        self.assertEqual(screening.safe_url('javascript:alert(1)'), '')
        self.assertEqual(screening.safe_url('https://user:secret@example.org/a'), '')
        self.assertEqual(screening.safe_url('https://example.org/policy?api_key=secret#part'), 'https://example.org/policy')
        self.assertEqual(screening.safe_url('https://example.org/policy?docId=15&api_key=secret'), 'https://example.org/policy?docId=15')
        for row in self.data['members']:
            self.assertNotRegex(row['url'].lower(), r'[?&](?:api_key|token|password|auth)=')
            self.assertNotIn('fulltext_url', row)

    def test_safe_url_keeps_the_address_byte_for_byte_and_drops_only_credential_parameters(self):
        keep = screening.safe_url
        # legislative session parameters stay, whatever their spelling
        self.assertEqual(keep('https://example.gov/bill?Session=2019&Bill=HB1'), 'https://example.gov/bill?Session=2019&Bill=HB1')
        self.assertEqual(keep('https://example.gov/bill?txtSessionYear=2019&sessiontype=R'), 'https://example.gov/bill?txtSessionYear=2019&sessiontype=R')
        self.assertEqual(keep('https://www.ilga.gov/legislation/BillStatus.asp?DocNum=1&GAID=14&DocTypeID=HB&SessionID=85'),
                         'https://www.ilga.gov/legislation/BillStatus.asp?DocNum=1&GAID=14&DocTypeID=HB&SessionID=85')
        self.assertEqual(keep('https://www.azleg.gov/ars/x?session_id=115'), 'https://www.azleg.gov/ars/x?session_id=115')
        # a blank-valued parameter gets no '=', order and percent-encoding are as written
        self.assertEqual(keep('https://example.gov/doc.nsf?OpenDocument'), 'https://example.gov/doc.nsf?OpenDocument')
        self.assertEqual(keep('https://example.gov/doc?a=&b&c=1'), 'https://example.gov/doc?a=&b&c=1')
        self.assertEqual(keep('https://example.gov/doc?z=1&a=%26x+y&Committee%26nbspVotes=1'), 'https://example.gov/doc?z=1&a=%26x+y&Committee%26nbspVotes=1')
        self.assertEqual(keep('https://example.gov/doc?&a=1&&b=2'), 'https://example.gov/doc?&a=1&&b=2')
        # api.govinfo.gov package links need a key: the public address of the same document is shown
        self.assertEqual(keep('https://api.govinfo.gov/packages/FR-2011-06-07/granules/2011-14007/htm'),
                         'https://www.govinfo.gov/content/pkg/FR-2011-06-07/html/2011-14007.htm')
        self.assertEqual(keep('https://api.govinfo.gov/packages/FR-2011-06-07/granules/2011-14007/htm?api_key=SECRETSECRET'),
                         'https://www.govinfo.gov/content/pkg/FR-2011-06-07/html/2011-14007.htm')
        # credential names, whole name and any case, are removed together with one separator
        self.assertEqual(keep('https://example.gov/doc?docId=15&api_key=SECRETSECRET'), 'https://example.gov/doc?docId=15')
        self.assertEqual(keep('https://example.gov/doc?api_key=SECRETSECRET&docId=15'), 'https://example.gov/doc?docId=15')
        self.assertEqual(keep('https://example.gov/doc?a=1&API_KEY=SECRETSECRET&b=2'), 'https://example.gov/doc?a=1&b=2')
        self.assertEqual(keep('https://example.gov/doc?api%5Fkey=SECRETSECRET&b=2'), 'https://example.gov/doc?b=2')
        self.assertEqual(keep('https://example.gov/doc?api_key=SECRETSECRET'), 'https://example.gov/doc')
        self.assertEqual(keep('https://example.gov/doc?token=a&access_token=b&auth=c&password=d&signature=e&JSESSIONID=f'), 'https://example.gov/doc')
        # a name that only contains such a word is not a credential
        self.assertEqual(keep('https://example.gov/doc?keyword=tax&author=lee&monkey=1'), 'https://example.gov/doc?keyword=tax&author=lee&monkey=1')
        # a long opaque SessionID is a login session; a short one is the legislative session
        self.assertEqual(keep('https://example.gov/doc?SessionID=0123456789abcdef0123&b=1'), 'https://example.gov/doc?b=1')
        self.assertEqual(keep('https://example.gov/doc;jsessionid=0123456789ABCDEF?b=1'), 'https://example.gov/doc?b=1')
        # the fragment is dropped; userinfo, other schemes and empty values give ''
        self.assertEqual(keep('https://example.gov/doc?b=1#part'), 'https://example.gov/doc?b=1')
        self.assertEqual(keep('https://example.gov/doc?api_key=SECRETSECRET#part'), 'https://example.gov/doc')
        for refused in ('https://user:secret@example.gov/a', 'https://user@example.gov/a', 'https://@example.gov/a', 'javascript:alert(1)',
                        'ftp://example.gov/a', 'mailto:a@example.gov', '//example.gov/a', '', None):
            self.assertEqual(keep(refused), '', refused)
        for row in self.data['members']:
            self.assertEqual(keep(row['url']), row['url'])  # nothing left for a second pass to remove

    def test_member_text_carries_no_api_key_parameter(self):
        from scripts.viz_site.public_page import credential_leaks
        self.assertEqual(credential_leaks(json.dumps(self.data['members'])), 0)

    # R28 (owner, 2026-10-14): opening a candidate shows the original panel, which asks the API for that candidate's record and, in its Policy
    # Family block, for the family's list. These two calls, made only when a card is opened, are the only requests the page may make.
    FETCHES = ['"/api/policies/" + id + "/detail"', '"/api/policy-groups/" + encodeURIComponent(family) + "?page=1&size=100"']

    def test_en_zh_embed_identical_safe_members_and_only_the_two_calls_of_an_open_candidate(self):
        from scripts.viz_site import legacy_detail
        dictionaries = [json.loads((screening.HERE / f'i18n/screening.{lang}.json').read_text()) for lang in ['en', 'zh']]
        self.assertEqual(set(dictionaries[0]), set(dictionaries[1]))
        for lang in ['en', 'zh']:
            page = screening.render(lang)
            blob = re.search(r'<script type="application/json" id="sc-members">(.*?)</script>', page, re.S)[1]
            self.assertNotIn('<', blob)
            self.assertEqual(json.loads(blob), self.data['members'])
            self.assertIn(f'/viz/{lang}/all_records.html', page)
            self.assertIn('id="screening-method"', page)
            self.assertIn('id="sc-prev"', page)
            self.assertEqual(re.findall(r'\bfetch\s*\(((?:[^()]|\([^()]*\))*)\)', page), self.FETCHES)
            for other in ('XMLHttpRequest', 'sendBeacon', 'WebSocket', 'EventSource', 'importScripts'):
                self.assertNotIn(other, page)
            # No request is made while the page loads or the list is drawn: the pure display block has none, and no timer can repeat one.
            display = re.search(r'// SCREENING DISPLAY START(.*?)// SCREENING DISPLAY END', page, re.S)[1]
            self.assertNotIn('fetch', display)
            for code in (screening.JS, legacy_detail.script(lang)):
                self.assertNotRegex(code, r'setInterval|setTimeout|requestAnimationFrame')

    # Checked against the live API on 2026-10-14 (GET /api/policies/{api_id}/detail, twelve candidates spread over the list and over the
    # four source families; each answer had this policy_id and this title): the CSV `id` is the API id.
    VERIFIED_API_IDS = {
        '1031268': 2082947, 'govinfo-plaw-plaw-117publ151': 2484950, 'NM-SB15-Regular Session': 2083386, 'fedreg-97-29117': 2224659,
        'fedreg-99-8850': 2394607, 'state-eo-il-69d5eba2d99ab88b': 2359657, '1053922': 2078369, 'KY-SB199-Regular Session': 2079110,
        'fedreg-2010-22298': 2441592, 'regulations.gov-regulations-EBSA-2023-0020-0060': 2100861, 'TN-SB2926-113th General Assembly': 2090191,
        'fedreg-97-31678': 2235108}

    def test_every_member_carries_the_api_id_of_its_csv_row(self):
        ids = [row['api_id'] for row in self.data['members']]
        self.assertTrue(all(type(value) is int and value > 0 for value in ids))
        self.assertEqual(len(set(ids)), len(ids))
        by_policy = {row['id']: row['api_id'] for row in self.data['members']}
        for policy_id, api_id in self.VERIFIED_API_IDS.items():
            with self.subTest(policy_id=policy_id):
                self.assertEqual(by_policy[policy_id], api_id)
        # The policy_id stays the member's id; it is not the API id (494 candidates have a purely numeric policy_id).
        self.assertNotEqual(by_policy['1031268'], 1031268)

    def test_card_has_its_api_id_the_compact_fallback_and_a_place_for_the_panel(self):
        program = screening.DISPLAY_JS + r"""
const fs=require('fs'), input=JSON.parse(fs.readFileSync(0,'utf8'));
const scText=input.translations;
console.log(JSON.stringify(scDisplayRecord(input.row)));
"""
        row = next(r for r in self.data['members'] if r['family'])
        for lang in ('en', 'zh'):
            translations = json.loads((screening.HERE / f'i18n/screening.{lang}.json').read_text())
            result = subprocess.run(['node', '-e', program], input=json.dumps({'row': row, 'translations': translations}), text=True, capture_output=True, check=True)
            card = json.loads(result.stdout)
            self.assertIn(f'<details class="sc-record" data-api-id="{row["api_id"]}">', card)
            # In this order inside the body: the AI line, the status line, the panel's place (both empty and hidden), the compact card, the family button.
            order = [card.index(text) for text in (translations['model_labels'], 'class="sc-detail-note sc-muted" role="status" hidden>', 'class="sc-detail" hidden>',
                                                   'class="sc-compact"', 'data-family=')]
            self.assertEqual(order, sorted(order))
            self.assertIn('<dl>', card[card.index('class="sc-compact"'):card.index('data-family=')])

    def test_the_panel_comes_from_the_original_page_and_its_css_is_in_the_page(self):
        from scripts.viz_site import legacy_detail
        for lang in ('en', 'zh'):
            page = screening.render(lang)
            self.assertIn(legacy_detail.script(lang), page)
            self.assertIn(legacy_detail.css(), page)
            # The original's own colours are in the page's stylesheet: the strip, the grid and the section frame.
            for rule in ('.chapter-strip{', 'background: #faf5ff', 'border-left: 3px solid #8b5cf6', '.detail-section{', '.ww-grid{', '.detail-content{'):
                self.assertIn(rule, page)
            # The page-wide button and link rules do not restyle the panel: its bridge rules come before the original's own.
            self.assertLess(page.index('.sc-detail{'), page.index('.detail-section{'))

    OPEN = r"""
const vm=require('vm'), fs=require('fs'), input=JSON.parse(fs.readFileSync(0,'utf8'));
const element=(extra={})=>Object.assign({hidden:false,dataset:{},className:'',textContent:'',innerHTML:'',children:{},classList:{contains:name=>false},
  querySelector(selector){return this.children[selector]||null},querySelectorAll(){return []},addEventListener(){}},extra);
const card=(apiId,open=false)=>{const c=element({open,dataset:{apiId:String(apiId)},classList:{contains:name=>name==='sc-record'}});
  c.children={'.sc-detail':element({hidden:true}),'.sc-compact':element(),'.sc-detail-note':element({hidden:true})};return c};
const rows=input.rows,cards=input.rows.map(r=>card(r.api_id)),listeners={},calls=[];let answers={};
const list=element({addEventListener(type,handler,capture){listeners[type]=handler},querySelectorAll(selector){return selector.includes('[open]')?cards.filter(c=>c.open):[]}});
const ids={'sc-members':element({textContent:JSON.stringify(rows)}),'sc-query':element({value:'',addEventListener(){}}),'sc-records':list,'sc-result-count':element({scrollIntoView(){}}),
  'sc-page-number':element(),'sc-prev':element({addEventListener(){}}),'sc-next':element({addEventListener(){}}),'sc-reset':element({addEventListener(){}})};
const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
const context={window:{},document:{getElementById:id=>ids[id],querySelectorAll:()=>[],documentElement:{lang:'en'}},
  fetch:async url=>{calls.push(url);const answer=answers[url];if(!answer)return reply(404,{error:'not found'});return reply(answer[0],answer[1])}};
vm.createContext(context);vm.runInContext(input.code,context);
const settle=()=>new Promise(resolve=>setTimeout(resolve,30));
const open=async(c)=>{c.open=true;listeners.toggle({target:c});await settle()};
const state=c=>({state:c.children['.sc-detail'].dataset.state||'',panel:!c.children['.sc-detail'].hidden,compact:!c.children['.sc-compact'].hidden,note:c.children['.sc-detail-note'].hidden?'':c.children['.sc-detail-note'].textContent});
const detailUrl=id=>'/api/policies/'+id+'/detail';
(async()=>{
  const out={callsAtLoad:calls.length};
  const record=input.record,other=Object.assign({},record,{policy_id:'SOMEONE-ELSE'});
  answers={[detailUrl(rows[0].api_id)]:[200,record],['/api/policy-groups/'+record.policy_family+'?page=1&size=100']:[200,{rows:input.group,total:input.group.length}],
           [detailUrl(rows[1].api_id)]:[429,{error:'rate limit exceeded'}],[detailUrl(rows[2].api_id)]:[200,other]};
  await open(cards[0]);out.first=state(cards[0]);out.firstCalls=calls.slice();
  cards[0].open=false;listeners.toggle({target:cards[0]});await open(cards[0]);out.reopenCalls=calls.length;out.reopen=state(cards[0]);
  // one panel at a time; a refusal shows the compact card and the failure line, nothing is retried, opening again is the retry
  await open(cards[1]);out.refused=state(cards[1]);out.openAfterSecond=cards.filter(c=>c.open).length;out.callsAfterRefusal=calls.length;
  await settle();await settle();out.callsLater=calls.length;
  cards[1].open=false;listeners.toggle({target:cards[1]});await open(cards[1]);out.callsAfterRetry=calls.length;
  answers[detailUrl(rows[1].api_id)]=[200,Object.assign({},record,{policy_id:rows[1].id})];
  cards[1].open=false;listeners.toggle({target:cards[1]});await open(cards[1]);out.recovered=state(cards[1]);
  // a record of another policy behind the id is not shown
  await open(cards[2]);out.mismatch=state(cards[2]);
  process.stdout.write(JSON.stringify(out));
})();
"""

    def test_a_candidate_is_fetched_only_when_its_card_opens_once_and_a_refusal_is_not_retried(self):
        from scripts.viz_site import legacy_detail
        from scripts.viz_site.check_v5 import PANEL_FIXTURE, PANEL_GROUP
        for lang in ('en', 'zh'):
            strings = json.loads((screening.HERE / f'i18n/screening.{lang}.json').read_text())
            rows = [dict(self.data['members'][i], api_id=900001 + i) for i in range(3)]
            rows[0]['id'] = PANEL_FIXTURE['policy_id']
            record = dict(PANEL_FIXTURE, id=900001)
            group = PANEL_GROUP
            code = 'const scText=' + json.dumps(strings) + ';' + screening.DISPLAY_JS + legacy_detail.script(lang) + screening.JS
            result = subprocess.run(['node', '-e', self.OPEN], input=json.dumps({'code': code, 'strings': strings, 'rows': rows, 'record': record, 'group': group}),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout)
            self.assertEqual(out['callsAtLoad'], 0)
            # Opening the card: the detail and the family list, once; the panel replaces the compact card.
            self.assertEqual(out['firstCalls'], ['/api/policies/900001/detail', '/api/policy-groups/minimum_marriage_age?page=1&size=100'])
            self.assertEqual(out['first'], {'state': 'ready', 'panel': True, 'compact': False, 'note': ''})
            self.assertEqual(out['reopenCalls'], 2)
            self.assertEqual(out['reopen'], out['first'])
            # A refusal (HTTP 429): the compact card stays, the page's short retry line shows, only one request was made and no timer repeats it.
            failed = {'state': '', 'panel': False, 'compact': True, 'note': strings['detail_failed']}
            self.assertEqual(out['refused'], failed)
            self.assertEqual(out['openAfterSecond'], 1)
            self.assertEqual((out['callsAfterRefusal'], out['callsLater']), (3, 3))
            self.assertEqual(out['callsAfterRetry'], 4)
            self.assertEqual(out['recovered'], {'state': 'ready', 'panel': True, 'compact': False, 'note': ''})
            self.assertEqual(out['mismatch'], failed)

    def test_plain_filters_and_no_internal_record_ids(self):
        jurisdictions = {row['jurisdiction'] for row in self.data['members']}
        self.assertIn('Illinois', jurisdictions)
        self.assertNotIn('IL State', jurisdictions)
        self.assertNotIn('Illinois State', jurisdictions)
        self.assertEqual(screening.public_jurisdiction('IL State'), screening.public_jurisdiction('Illinois State'))
        for lang in ('en', 'zh'):
            page = screening.render(lang)
            text = re.sub(r'<(style|script)\b.*?</\1>', '', page, flags=re.S)
            self.assertNotRegex(text, r'(?i)\b(?:snapshot|gate|hash|pipeline|Qwen|GLM)\b|快照|五关|政策 ID')
            self.assertNotIn('<ol class="sc-stages">', page)
            self.assertNotIn('${esc(r.id)}', page)
            self.assertIn("scDisplayFamily(family)", page)
            labels = json.loads((screening.HERE / f'i18n/screening.{lang}.json').read_text())
            self.assertEqual(labels['minimum_marriage_age'], '最低结婚年龄' if lang == 'zh' else 'Minimum marriage age')
            if lang == 'en':
                for topic in {row['topic'] for row in self.data['members']}:
                    self.assertTrue(labels[topic][0].isupper())


    def test_display_mapping_preserves_members_and_cleans_every_card(self):
        # Exercise the actual browser formatter on every record, including cards
        # not shown on the first page and their collapsed contents.
        program = screening.DISPLAY_JS + r"""
const fs=require('fs'), input=JSON.parse(fs.readFileSync(0,'utf8'));
const scText=input.translations;
const before=JSON.stringify(input.rows);
const visible=input.rows.map(scDisplayRecord).map(html=>html.replace(/<[^>]*>/g,' '));
const bad=visible.filter(text=>/[A-Za-z0-9]+_[A-Za-z0-9_]+|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|date_--|·\s*·/.test(text));
console.log(JSON.stringify({drafting:visible.filter(text=>/number from legislative text|provide the specific numeric cutoff|should be inserted here/i.test(text)),bad:bad.slice(0,3),unchanged:before===JSON.stringify(input.rows),
  direction:scDisplayText('below_cutoff'),variant:scDisplayText('sharp_standard'),
  outcomes:scDisplayText('crime_rates, gun_ownership'),
  unit:scDisplayElement('rd_info','Variable: date | Cutoff: 2022-05-02 | Unit: date_--'),
  date:scDisplayText('2026-09-30T20:10:54+00:00'),
  title:scDisplayTitle({id:'fedreg-2011-14010',title:'fedreg-2011-14010',topic:'agriculture_food',source:'Federal Regulations'}),
  label:scDisplayLabel('agriculture_food'),family:scDisplayFamily('concealed_carry_licensing')}));
"""
        for lang in ('en', 'zh'):
            translations = json.loads((screening.HERE / f'i18n/screening.{lang}.json').read_text())
            result = subprocess.run(['node', '-e', program], input=json.dumps({'rows': self.data['members'], 'translations': translations}), text=True, capture_output=True, check=True)
            output = json.loads(result.stdout)
            self.assertEqual(output['bad'], [])
            self.assertEqual(output['drafting'], [])
            self.assertTrue(output['unchanged'])
            self.assertEqual(output['direction'], translations['below_cutoff'])
            self.assertEqual(output['variant'], translations['sharp_standard'])
            self.assertEqual(output['outcomes'], 'crime rates, gun ownership')
            self.assertNotIn('Unit:', output['unit'])
            self.assertNotIn('单位', output['unit'])
            self.assertNotIn('T20:', output['date'])
            # F1d (ZH editor): the run-time date has the year, month and day each followed by its unit and a space, as the page text does
            self.assertEqual(output['date'], '2026 年 9 月 30 日' if lang == 'zh' else 'September 30, 2026')
            self.assertNotIn('fedreg-', output['title'])
            self.assertEqual(output['label'], '农业与食品' if lang == 'zh' else 'Agriculture and food')
            self.assertEqual(output['family'], translations['concealed_carry_licensing'])
            page = screening.render(lang)
            self.assertIn('2026 年 9 月 30 日' if lang == 'zh' else 'September 30, 2026', page)
            # R30 (owner, 2026-10-15): the evidence block is above the candidate list.
            self.assertLess(page.index('class="sc-benchmark"'), page.index('id="sc-records"'))
            self.assertNotIn('2026-09-30T20:10:54+00:00', page)

    def test_score_label_is_ai_design_score_everywhere_and_the_language_flag_does_not_depend_on_it(self):
        # Owner, 2026-10-14 (R21): the card label and the sort sentence name the score the same way; the language
        # flag the display code uses for dates and colons comes from the page title, not from the label.
        program = screening.DISPLAY_JS + r"""
const fs=require('fs'), input=JSON.parse(fs.readFileSync(0,'utf8'));
const scText=input.translations;
const row={title:'T',id:'x',design:'DiD',source:'State Legislation',jurisdiction:'Illinois',year:'2024',score:3.45,elements:{},scores:{},url:'',family:''};
console.log(JSON.stringify({meta:/<span class="sc-record-meta">(.*?)<\/span>/.exec(scDisplayRecord(row))[1],zh:scIsZh()}));
"""
        for lang, label, colon, sentence, old in (('en', 'AI design score', ': ', 'Sorted by AI design score.', 'overall score'),
                                                  ('zh', 'AI 设计评分', '：', '按 AI 设计评分从高到低排列。', '综合评分')):
            translations = json.loads((screening.HERE / f'i18n/screening.{lang}.json').read_text())
            result = subprocess.run(['node', '-e', program], input=json.dumps({'translations': translations}), text=True, capture_output=True, check=True)
            output = json.loads(result.stdout)
            self.assertTrue(output['meta'].endswith(label + colon + '3.45'), output['meta'])
            self.assertEqual(output['zh'], lang == 'zh')
            page = re.sub(r'<(style|script)\b.*?</\1>', '', screening.render(lang), flags=re.S)
            self.assertIn(sentence, page)
            self.assertNotIn(old, page)


    def test_converging_flow_sits_between_the_intro_and_the_candidate_list(self):
        # Owner, 2026-10-14 (V5.1): "the Screening page could add the pipeline the previous website made".
        for lang in ('en', 'zh'):
            page = screening.render(lang)
            self.assertEqual(page.count('data-component="story-flow"'), 1)
            self.assertEqual(page.count('<div class="sc-flow">'), 1)
            self.assertLess(page.index('class="sc-intro"'), page.index('<div class="sc-flow">'))
            self.assertLess(page.index('<div class="sc-flow">'), page.index('class="sc-search"'))
            # The figure and its frame come from the generated partial, once, with its own motion script and static fallbacks.
            figure = screening.flow(lang)
            self.assertEqual(page.count(figure), 1)
            self.assertIn('@media (prefers-reduced-motion: reduce)', figure)
            self.assertIn('@media print', figure)
            self.assertNotIn('{{', figure)
            # No link, no tooltip text outside the figure's own list; one list of six steps for screen readers.
            self.assertEqual(figure.count('<li tabindex="0" data-k='), 5)  # R29 (SC5): the five steps; the end box has no hover text

    def test_evidence_block_sits_between_the_flow_figure_and_the_candidate_list(self):
        # Owner, 2026-10-15 (R30): "Recall of published designs", the paper title, the DiD and RD recall with their intervals, the precision
        # line, the composition line and the Sources lines come before the candidate list, directly after the intro and the flow figure.
        # The words are unchanged: only the position moved.
        for lang in ('en', 'zh'):
            page = screening.render(lang)
            visible = re.sub(r'<(style|script)\b.*?</\1>', '', page, flags=re.S)
            self.assertEqual(visible.count('class="sc-benchmark"'), 1)
            self.assertEqual(visible.count('<details class="sc-method" id="screening-method">'), 1)
            deployment = [m.start() for m in re.finditer(r'<p class="sc-deployment">', visible)]
            self.assertEqual(len(deployment), 2)  # the precision line and the composition line
            sources = visible.index('<details class="sc-method" id="screening-method">')
            chain = [visible.index('class="sc-intro"'), visible.index('<div class="sc-flow">'), visible.index('class="sc-benchmark"'),
                     visible.index('id="sc-benchmark-title"'), visible.index('class="sc-recall"'), *deployment, sources,
                     visible.index('</details>', sources), visible.index('class="sc-search"'), visible.index('id="sc-records"'),
                     visible.index('class="sc-secondary"')]
            self.assertEqual(chain, sorted(chain), lang)
            # Nothing sits between the figure, the evidence section, the Sources box and the candidate list.
            self.assertRegex(visible, r'</div>\n<section class="sc-benchmark" aria-labelledby="sc-benchmark-title">')
            self.assertRegex(visible, r'</section>\n<details class="sc-method" id="screening-method">')
            self.assertRegex(visible, r'</details>\n<section class="sc-search" aria-labelledby="sc-browse-heading">')
            # Both recall rows (DiD and RD) are above the first candidate control, and the Sources box still holds its three paragraphs (F6).
            self.assertEqual(visible.count('class="sc-recall-row"'), 2)
            self.assertLess(visible.rindex('class="sc-recall-row"'), visible.index('id="sc-query"'))
            self.assertEqual(re.search(r'<details class="sc-method".*?</details>', visible, re.S)[0].count('<p'), 3)

    def test_converging_flow_prints_only_counts_the_v5_pages_print(self):
        funnel = json.loads((screening.ROOT / 'deploy_output/funnel_data.json').read_text())
        families = [funnel['by_source_family'][name]['n'][0] for name in ('Federal Regulations', 'Federal Legislation', 'State Legislation', 'State/Local Executive')]
        for lang in ('en', 'zh'):
            figure = screening.flow(lang)
            text = re.sub(r'<(style|script)\b.*?</\1>', '', figure, flags=re.S)
            text = ' '.join(re.sub(r'<[^>]+>', ' ', text).split())
            numbers = set(re.findall(r'\d[\d,.]*\d|\d', text))
            # The texts and candidates are the count line's; the four families are Method's; 2020-21 (2020-2021 in Chinese, F1d) and 3 are the rules'.
            allowed = {f'{self.data["total"]:,}', f'{self.data["count"]:,}', *(f'{n:,}' for n in families), '2020', '2021', '3'}
            self.assertEqual(numbers, allowed)
            # The five gate counts of the old figure (and any rounded "k" or "万" count) are not drawn.
            for stage in funnel['stages'][1:-1]:
                self.assertNotIn(f'{stage["n_out"]:,}', text)
            self.assertNotRegex(text, r'\d\s?k\b|万')
            self.assertEqual(sum(families), self.data['total'])

    def test_converging_flow_fits_the_page_column_and_the_phone(self):
        # The frame is full-bleed on a phone, so its negative margin must equal the body padding (0.7rem at 900 px and below).
        self.assertIn('.sc-flow{margin:0 0 32px;--nx-gutter:.7rem;overflow-wrap:normal}', screening.CSS)
        chrome = (screening.HERE / 'partials/head_css.html').read_text()
        self.assertIn('@media (max-width: 900px) {\n    body { padding: .7rem; }', chrome)
        figure = screening.flow('en')
        self.assertIn('container-type: inline-size;', figure)
        self.assertIn('margin-inline: calc(var(--nx-gutter, 16px) * -1)', figure)
        self.assertIn('@container (min-width: 1000px)', figure)


if __name__ == '__main__':
    unittest.main()
