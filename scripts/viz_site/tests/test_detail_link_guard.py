"""V5.1 (F3): a link built from an API field is not rendered when its address carries a credential query parameter.

The live API can still return a govinfo key inside fulltext_url; Screening's panel and All records (table and panel) build links from the answer.
The page script is run in node on invented records (the key below is made up, it is not a real credential), once per page and language:
  - Screening: the panel (legacy_detail.script, the code the Screening page ships) and its display helpers;
  - All records: the functions of the V5 page as build_v5.legacy_records ships it (the table row and the panel).
Groups builds no link from an API field. check_v5.detail_link_guard_issues does the same job on a built output; the last tests show it fails on a
copy of a page whose guard has been taken out.
"""
import contextlib
import io
import json
import re
import subprocess
import unittest
from html import unescape
from pathlib import Path
from unittest.mock import patch

from scripts.viz_site import build_v5, check_v5, legacy_detail as ld, restructure_screening as screening
from scripts.zh_pages import build as zh

INVENTED = 'INVENTEDkey0123456789abcdef'
LABELS = {'en': {'page': 'Open source page', 'text': 'Open fetched text', 'document': 'Open source document'},
          'zh': {'page': '打开来源页面', 'text': '打开已获取文本', 'document': '打开源文件'}}
PLAIN_PAGE, PLAIN_TEXT = 'https://example.org/bill?id=3&session=2019', 'https://example.org/bill/text.pdf'
KEYED = 'https://api.example.test/package/htm?api_key=' + INVENTED
BASE = {'id': 1, 'policy_id': 'XX-1', 'title': 'Example law', 'composite_score': 4.8, 'fulltext_source': 'fetched', 'fulltext_chars': 120}

OMIT = ['https://h.test/p?api_key=K', 'https://h.test/p?API_KEY=K', 'https://h.test/p?Api-Key=K', 'https://h.test/p?apikey=K', 'https://h.test/p?key=K',
        'https://h.test/p?token=K', 'https://h.test/p?access_token=K', 'https://h.test/p?auth=K', 'https://h.test/p?authorization=K',
        'https://h.test/p?password=K', 'https://h.test/p?secret=K', 'https://h.test/p?signature=K', 'https://h.test/p?a=1&api_key=K',
        'https://h.test/p?a=1;api_key=K', 'https://h.test/p?a=1&amp;api_key=K', 'https://h.test/p?%61pi_key=K', 'https://h.test/p?api%5Fkey=K',
        'https://h.test/p#access_token=K', 'https://h.test/p?a=1#token=K', 'https://h.test/p?key', 'https://h.test/p?+api_key+=K']
KEEP = ['https://example.org/bill', 'https://h.test/p?Session=2019', 'https://h.test/p?txtSessionYear=2019&txtBillNumber=1', 'https://h.test/p?keyword=wage',
        'https://h.test/p?monkey=1', 'https://h.test/p?tokens=2', 'https://h.test/p?authors=1', 'https://h.test/p?DocNum=12&GAID=17&SessionID=112',
        'https://h.test/p?x=api_key', 'https://h.test/p?y=1&z=token', 'https://h.test/api_key/doc', '/relative/path', '', None]

PREDICATE = """const vm=require('vm');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
const p=JSON.parse(input);const c={};vm.createContext(c);vm.runInContext(p.code+'\\nglobalThis.test=hasCredentialParam;',c);
process.stdout.write(JSON.stringify(p.urls.map(u=>c.test(u))));});"""

PANELS = """const vm=require('vm');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
const p=JSON.parse(input),els={};
const el=id=>els[id]||(els[id]={textContent:'',innerHTML:'',disabled:false});
const c={scText:p.strings,window:{},document:{getElementById:el,querySelectorAll:()=>[]},fetch:async()=>{throw new Error('no network')}};
vm.createContext(c);vm.runInContext(p.code,c);
const out={panels:{},rows:{}};
for(const [name,record] of Object.entries(p.records))out.panels[name]=await c.panel.buildDetailHtml(record,'dc-1');
if(c.panel.renderTable)for(const [name,row] of Object.entries(p.rows)){c.panel.renderTable({total:1,rows:[row]});out.rows[name]=els.tbody.innerHTML}
process.stdout.write(JSON.stringify(out));});"""


def node(script, payload):
    run = subprocess.run(['node', '-e', script], text=True, capture_output=True, input=json.dumps(payload))
    assert run.returncode == 0, run.stderr[:300]
    return run.stdout


def functions_named(text, name):
    """Every definition of `name` in the text (a top-level one and one nested in another function are both found), source as written."""
    found, start = [], text.find('function ' + name + '(')
    while start >= 0:
        found.append(text[start:ld._past_block(text, text.index('{', start))])
        start = text.find('function ' + name + '(', start + 1)
    return found


def anchors(html):
    return [(unescape(href), label) for href, label in re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', html)]


class LinkGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        originals = {}
        with contextlib.redirect_stdout(io.StringIO()), patch.object(zh, '_viz_build', build_v5.viz):
            assert zh.main(['--check'], rendered=originals) == 0
        cls.pages = {}
        cls.code = {}
        for lang in ('en', 'zh'):
            records = build_v5.legacy_records(originals[lang]['policy_db'], lang)
            strings = json.loads((Path(screening.HERE) / 'i18n' / ('screening.%s.json' % lang)).read_text())
            # What the Screening page ships: the display block, the panel and the page's strings (the member data and the search script are not needed here).
            cls.pages['/viz/%s/screening.html' % lang] = '<script>const scText=' + json.dumps(strings, ensure_ascii=False) + ';' + screening.DISPLAY_JS + ld.script(lang) + '</script>'
            cls.pages['/viz/%s/all_records.html' % lang] = records
            cls.pages[('/zh' if lang == 'zh' else '') + '/policy_groups.html'] = originals[lang]['policy_groups']
            cls.code[lang, 'screening'] = (screening.DISPLAY_JS + '\n' + ld.script(lang) + '\nglobalThis.panel=scDetail;', strings)
            parts = [ld._statement(records, '    const topicDisplayMap = {')]
            if lang == 'zh':
                parts += [ld._statement(records, '    const ZH_VOCAB = {'), ld._function(records, 'zhLabel')]
            parts += [ld._function(records, name) for name in ld.FUNCTIONS + ('statusBadge', 'srcBadgeClass', 'renderTable', 'hasCredentialParam')]
            cls.code[lang, 'records'] = ("const state={ignore_data:false,page:1,size:20},detailCache=new Map(),groupCache=new Map();let lastData=null;function updatePagination(){}"
                                         "function toggleDetail(){}\n" + '\n'.join(parts) + '\nglobalThis.panel={buildDetailHtml,renderTable};', {})

    def run_panels(self, lang, kind, records, rows=None):
        code, strings = self.code[lang, kind]
        out = json.loads(node(PANELS, dict(code=code, strings=strings, records=records, rows=rows or {})))
        return out['panels'], out['rows']

    # -- the predicate: the copies are one function and agree with the list of names ------------------------------------------------------------

    def test_every_copy_of_the_predicate_agrees_on_credential_and_ordinary_addresses(self):
        for lang in ('en', 'zh'):
            copies = functions_named(self.pages['/viz/%s/all_records.html' % lang], 'hasCredentialParam') + functions_named(ld.script(lang), 'hasCredentialParam')
            self.assertEqual(len(copies), 3, 'one top-level copy and one nested copy on All records, one nested copy in Screening')
            self.assertEqual(len({re.sub(r'\s+', ' ', copy) for copy in copies}), 1, 'the copies must be the same function')
            for copy in copies:
                shown = json.loads(node(PREDICATE, dict(code=copy, urls=OMIT + KEEP)))
                self.assertEqual(shown, [True] * len(OMIT) + [False] * len(KEEP), copy[:60])

    # -- the panels -----------------------------------------------------------------------------------------------------------------------------

    def test_a_panel_leaves_out_a_credential_link_keeps_a_normal_one_and_never_prints_the_key(self):
        cases = {'plain': (PLAIN_PAGE, PLAIN_TEXT), 'key in the text link': (PLAIN_PAGE, KEYED), 'key in the second link field': (KEYED, PLAIN_TEXT),
                 'one address twice, with the key': (KEYED, KEYED), 'one address twice': (PLAIN_TEXT, PLAIN_TEXT)}
        for lang in ('en', 'zh'):
            word = LABELS[lang]
            for kind in ('screening', 'records'):
                panels, _ = self.run_panels(lang, kind, {name: dict(BASE, document_url=page, fulltext_url=text) for name, (page, text) in cases.items()})
                for name, html in panels.items():
                    with self.subTest(lang=lang, kind=kind, case=name):
                        self.assertNotIn(INVENTED, html)
                        self.assertNotRegex(html, r'(?i)api[_-]?key')
                        links = {label: href for href, label in anchors(html)}
                        wanted = {'plain': {'page': PLAIN_PAGE, 'text': PLAIN_TEXT}, 'key in the text link': {'page': PLAIN_PAGE},
                                  'key in the second link field': {'text': PLAIN_TEXT}, 'one address twice, with the key': {},
                                  'one address twice': {'document': PLAIN_TEXT}}[name]
                        self.assertEqual(links, {word[part]: href for part, href in wanted.items()})
                        if name == 'key in the text link':
                            # the label of the omitted link goes with it
                            self.assertNotIn(('Full text / PDF:' if lang == 'en' else '全文 / PDF：') + '</span>', html)
                        if name == 'key in the second link field':
                            self.assertNotIn(('Landing page:' if lang == 'en' else '来源页面：') + '</span>', html)

    def test_a_link_without_a_credential_parameter_renders_exactly_as_before(self):
        for lang in ('en', 'zh'):
            for kind in ('screening', 'records'):
                panels, _ = self.run_panels(lang, kind, {'plain': dict(BASE, document_url=PLAIN_PAGE, fulltext_url=PLAIN_TEXT)})
                html = panels['plain']
                with self.subTest(lang=lang, kind=kind):
                    self.assertIn('<a href="https://example.org/bill?id=3&amp;session=2019" target="_blank" rel="noopener" onclick="event.stopPropagation()">'
                                  + LABELS[lang]['page'] + '</a>', html)
                    self.assertIn('<a href="https://example.org/bill/text.pdf" target="_blank" rel="noopener" onclick="event.stopPropagation()">'
                                  + LABELS[lang]['text'] + '</a>', html)

    def test_a_panel_with_only_credential_addresses_keeps_its_other_rows_and_drops_the_empty_block(self):
        # A fetched-text row and an address that cannot be shown: the provenance block stays for the rows that remain, and is gone when nothing remains.
        for lang in ('en', 'zh'):
            for kind in ('screening', 'records'):
                bare = {key: value for key, value in BASE.items() if key not in ('fulltext_source', 'fulltext_chars')}
                panels, _ = self.run_panels(lang, kind, {'rows remain': dict(BASE, fulltext_url=KEYED), 'nothing remains': dict(bare, fulltext_url=KEYED, document_url=KEYED)})
                with self.subTest(lang=lang, kind=kind):
                    self.assertIn('class="ww-label"', panels['rows remain'])
                    self.assertEqual(anchors(panels['rows remain']), [])
                    self.assertNotIn('border-left:3px solid #ea580c', panels['nothing remains'])

    # -- the table of All records -----------------------------------------------------------------------------------------------------------------

    def test_the_view_link_of_a_row_shows_only_for_an_address_without_a_credential_parameter(self):
        rows = {'plain': {'id': 1, 'policy_id': 'XX-1', 'title': 'Example law', 'url': 'https://example.org/bill'},
                'document first': {'id': 2, 'policy_id': 'XX-2', 'title': 'Example law', 'document_url': PLAIN_PAGE, 'fulltext_url': KEYED},
                'key in url': {'id': 3, 'policy_id': 'XX-3', 'title': 'Example law', 'url': KEYED, 'document_url': PLAIN_PAGE},
                'key in document_url': {'id': 4, 'policy_id': 'XX-4', 'title': 'Example law', 'document_url': KEYED},
                'key in fulltext_url': {'id': 5, 'policy_id': 'XX-5', 'title': 'Example law', 'fulltext_url': KEYED}}
        for lang in ('en', 'zh'):
            _, shown = self.run_panels(lang, 'records', {}, rows)
            for name, html in shown.items():
                with self.subTest(lang=lang, row=name):
                    self.assertNotIn(INVENTED, html)
                    cell = re.search(r'<td class="c-link">(.*?)</td>', html, re.S)[1]
                    expected = {'plain': 'https://example.org/bill', 'document first': PLAIN_PAGE}.get(name)
                    self.assertEqual([href for href, _ in anchors(cell)], [expected] if expected else [])
                    if expected:
                        self.assertIn('target="_blank" rel="noopener" onclick="event.stopPropagation()">' + ('查看' if lang == 'zh' else 'View') + '</a>', cell)

    # -- Groups and the check -------------------------------------------------------------------------------------------------------------------------

    def test_groups_builds_no_link_from_an_api_field(self):
        for key in ('/policy_groups.html', '/zh/policy_groups.html'):
            scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', self.pages[key], re.S))
            self.assertNotRegex(scripts, r'''<a[ "']|createElement\(\s*['"]a['"]|\b\w+\.(?:url|document_url|fulltext_url)\b|fulltext_url|document_url''')

    def test_the_build_check_passes_on_the_shipped_pages(self):
        self.assertEqual(check_v5.detail_link_guard_issues(self.pages), [])

    def test_the_build_check_fails_on_a_copy_with_a_guard_taken_out(self):
        removals = (('/viz/en/all_records.html', 'hasCredentialParam(r.fulltext_url) ? "" : r.fulltext_url', 'r.fulltext_url'),
                    ('/viz/zh/all_records.html', 'hasCredentialParam(r.document_url) ? "" : r.document_url', 'r.document_url'),
                    ('/viz/en/all_records.html', '(viewUrl&&!hasCredentialParam(viewUrl))', '(viewUrl)'),
                    ('/viz/en/screening.html', 'hasCredentialParam(r.fulltext_url) ? "" : r.fulltext_url', 'r.fulltext_url'),
                    ('/viz/zh/screening.html', 'hasCredentialParam(r.document_url) ? "" : r.document_url', 'r.document_url'),
                    ('/viz/zh/screening.html', 'api[_-]?key|key|token', 'api[_-]?key|token'))
        for key, old, new in removals:
            with self.subTest(page=key, removed=old[:40]):
                self.assertIn(old, self.pages[key])
                broken = dict(self.pages, **{key: self.pages[key].replace(old, new)})
                self.assertTrue(any(issue.startswith(key) for issue in check_v5.detail_link_guard_issues(broken)))

    def test_the_build_check_fails_when_a_page_is_missing_or_groups_gains_an_unguarded_link(self):
        missing = {key: text for key, text in self.pages.items() if key != '/viz/zh/screening.html'}
        self.assertTrue(any(issue.startswith('/viz/zh/screening.html') for issue in check_v5.detail_link_guard_issues(missing)))
        groups = dict(self.pages, **{'/policy_groups.html': self.pages['/policy_groups.html'].replace(
            '</script>', "; const x = '<a href=\"' + m.url + '\">link</a>'; </script>", 1)})
        self.assertTrue(any(issue.startswith('/policy_groups.html') for issue in check_v5.detail_link_guard_issues(groups)))


if __name__ == '__main__':
    unittest.main()
