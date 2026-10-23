"""Owner item 25: reject internal copy and keep reader controls usable."""
from html import escape
import json
from pathlib import Path
import re
import subprocess
import unittest

from scripts.viz_site.public_page import banned_terms, prepare_public_page, legend_sizes, DETAIL_LINKS, FAVICON


class PublicPageTests(unittest.TestCase):
    def test_banned_copy_fails_build_in_both_languages_and_accessible_text(self):
        words = ('frozen', 'hash-locked', 'SHA-256', 'gate', 'capped', 'capping', 'cap', 'primary-run',
                 'R1a', 'snapshot', 'queue', 'triage', 'lease', 'adapter', 'schema', 'receipt',
                 'stage keys', 'internal IDs', 'run-17', 'persona', 'DEV v2', 'EX-1', 'C17',
                 'results.json', '两人确认', '数字对上，待复核', '口径待核', '已做完未计入',
                 '等期刊原表', '等待数据访问', '数据受限')
        for word in words:
            for content in (f'<p>{word}</p>', f'<details><p>{word}</p></details>', f'<i aria-label="{word}"></i>'):
                with self.subTest(word=word, content=content):
                    with self.assertRaisesRegex(ValueError, 'Public wording'):
                        prepare_public_page('<html><head></head><body>'+content+'</body></html>')

    def test_standard_economics_and_code_are_allowed(self):
        source = '<p>difference-in-differences; event study; standard error; confidence interval; odds ratio; pre-registered</p><script>const snapshot={hash:1};</script><style>.gate{display:none}</style>'
        self.assertEqual(banned_terms(source), [])

    def test_dynamic_screening_content_is_scanned(self):
        row = {'title':'State policy', 'elements':{'outcome':'hash-locked estimate'}, 'scores':{}}
        source = '<script type="application/json" id="sc-members">'+json.dumps([row])+'</script>'
        self.assertIn('hash-locked', banned_terms(source))
        row['elements']['outcome']='Natural gas pipeline capacity, farm-gate prices, benefit receipt'
        source = '<script type="application/json" id="sc-members">'+json.dumps([row])+'</script>'
        self.assertEqual(banned_terms(source), [])
        self.assertIn('frozen', banned_terms('<script>const scText={"description":"frozen"};</script>'))

    def test_an_api_key_parameter_in_the_final_page_fails_the_build(self):
        from scripts.viz_site.public_page import credential_leaks
        key = 'INVENTED0123456789abcdefINVENTED012345'  # invented, never a real key
        for where in (f'<a href="https://api.example.gov/p/htm?api_key={key}">text</a>',
                      f'<script type="application/json" id="d">{{"u":"https://api.example.gov/p?x=1&API_KEY={key}"}}</script>',
                      f'<a href="https://api.example.gov/p?api_key%3D{key}">text</a>'):
            with self.subTest(where=where[:40]):
                with self.assertRaisesRegex(ValueError, 'API key') as caught:
                    prepare_public_page('<html><head></head><body>' + where + '</body></html>')
                self.assertNotIn(key, str(caught.exception))
        for fine in ('<a href="https://example.gov/p?Session=2019&docId=15">text</a>', '<p>api_key=short</p>', '<p>an API key is needed</p>'):
            self.assertEqual(credential_leaks(fine), 0, fine)
            prepare_public_page('<html><head></head><body>' + fine + '</body></html>')

    def test_five_entry_legend_fails_build(self):
        source='<html><head></head><body><div class="v5-legend">'+('<span>Result</span>'*5)+'</div></body></html>'
        with self.assertRaisesRegex(ValueError, 'maximum 4'):
            prepare_public_page(source)

    def test_indexing_is_inherited_and_script_is_idempotent(self):
        from scripts.viz_site.public_page import inherit_indexing, inherited_robots, _Robots
        source = '<html><head><meta name="robots" content="noindex"></head><body></body></html>'
        for route in ('policy_db.html', 'documentation.html', 'policy_groups.html',
                      'viz/en/worked_examples.html', 'viz/en/synthesize.html',
                      'viz/en/all_records.html', 'index.html', 'zh/index.html'):
            with self.subTest(route=route):
                once = prepare_public_page(inherit_indexing(source, route))
                self.assertEqual(_Robots(once).values, [])
                self.assertEqual(once, prepare_public_page(inherit_indexing(once, route)))
                self.assertEqual(once.count('id="public-details-links"'), 1)
        for route in ('viz/en/screening.html', 'viz/zh/adoption.html'):
            self.assertEqual(_Robots(inherit_indexing(source, route)).values, ['noindex'])

    def test_every_page_gets_one_inline_svg_icon_in_the_accent_colour(self):
        # Owner request, 2026-10-14 (R27): a small inline SVG favicon in the site's accent colour; no new file, no release key.
        from urllib.parse import unquote
        import xml.etree.ElementTree as ElementTree
        heads = ('<head></head>', '<head><meta charset="UTF-8"><title>x</title></head>', '<head><link rel="icon" href="data:,"><title>x</title></head>',
                 '<head>\n  <meta charset="utf-8">\n  <link rel="icon" href="data:,">\n</head>')
        for head in heads:
            with self.subTest(head=head):
                once = prepare_public_page(f'<html>{head}<body></body></html>')
                icons = re.findall(r'<link\s+rel="icon"[^>]*>', once)
                self.assertEqual(icons, [FAVICON])
                self.assertNotIn('data:,', once)
                self.assertLess(once.index('rel="icon"'), once.index('</head>'))
                self.assertEqual(prepare_public_page(once), once)
                self.assertEqual(banned_terms(once), [])
        svg = ElementTree.fromstring(unquote(re.search(r'href="data:image/svg\+xml,([^"]+)"', FAVICON)[1]))
        self.assertTrue(svg.tag.endswith('svg'))
        self.assertEqual(svg.get('viewBox'), '0 0 32 32')
        accent = re.search(r'--nx-accent:(#[0-9a-f]{6})', (Path(__file__).resolve().parents[1] / 'design/design_tokens.css').read_text())[1]
        fills = {node.get('fill') for node in svg.iter() if node.get('fill') not in (None, 'none')}
        self.assertEqual(fills, {accent})
        self.assertLess(len(FAVICON), 600)

    def test_deep_links_open_target_and_ancestor_details_on_load_hash_and_same_link(self):
        # Execute actual shared script against a minimal DOM; no network/browser.
        code = re.search(r'<script[^>]*>(.*?)</script>', DETAIL_LINKS, re.S)[1]
        harness = '''const vm=require('vm');
for(const id of ['method-materials','screening-method','explore-sources']){
 const outer={tagName:'DETAILS',open:false,parentElement:null};
 const target={tagName:'DETAILS',open:false,parentElement:outer,scrollIntoView(){this.scrolled=true}};
 const events={};const location={hash:'#'+id,origin:'http://local',pathname:'/page',href:'http://local/page#'+id};
 const context={location,URL,window:{addEventListener:(k,v)=>events[k]=v},document:{getElementById:x=>x===id?target:null,addEventListener:(k,v)=>events[k]=v}};
 vm.runInNewContext(CODE,context);
 const check=()=>{if(!target.open||!outer.open||!target.scrolled)throw Error(id)};check();
 target.open=false;outer.open=false;events.hashchange();check();
 target.open=false;outer.open=false;events.click({target:{closest:()=>({href:location.href})}});check();
}
'''.replace('CODE', json.dumps(code))
        result=subprocess.run(['node','-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_deep_link_to_a_plain_block_scrolls_without_opening_or_throwing(self):
        # R18: #method-materials on Estimates is an empty <div>, not a <details>. Same shared script, minimal DOM, no browser.
        code = re.search(r'<script[^>]*>(.*?)</script>', DETAIL_LINKS, re.S)[1]
        harness = '''const vm=require('vm');
const make=(tag,parent)=>({tagName:tag,parentElement:parent,scrolled:0,scrollIntoView(o){this.scrolled++;this.last=o}});
const html=make('HTML',null),article=make('ARTICLE',html),anchor=make('DIV',article);
const events={};
const load=(hash,ids)=>{const location={hash,origin:'http://local',pathname:'/page',href:'http://local/page'+hash};
 events.hashchange=null;events.click=null;
 vm.runInNewContext(CODE,{location,URL,window:{addEventListener:(k,v)=>events[k]=v},document:{getElementById:x=>ids[x]||null,addEventListener:(k,v)=>events[k]=v}});
 return location};
let location=load('#method-materials',{'method-materials':anchor});
if(anchor.scrolled!==1||anchor.last.block!=='start')throw Error('no landing scroll');
for(const node of [anchor,article,html])if('open' in node)throw Error('open set on '+node.tagName);
events.hashchange();if(anchor.scrolled!==2)throw Error('hashchange did not land');
events.click({target:{closest:()=>({href:location.href})}});if(anchor.scrolled!==3)throw Error('same-page link did not land');
for(const node of [anchor,article,html])if('open' in node)throw Error('open set after click on '+node.tagName);
load('#no-such-id',{'method-materials':anchor});load('#%E0%A4%A',{'method-materials':anchor});load('',{});
if(anchor.scrolled!==3)throw Error('an unknown or malformed hash scrolled');
'''.replace('CODE', json.dumps(code))
        result=subprocess.run(['node','-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_adoption_has_three_year_bands_and_one_evidence_entry(self):
        from scripts.viz_site.public_page import simplify_adoption
        source=(Path(__file__).resolve().parents[1]/'partials/story_map.html').read_text()
        source='<html><head></head><body>'+source+'</body></html>'
        result=simplify_adoption(source)
        self.assertEqual(legend_sizes(result),[('nx-legend nx-in',4)])
        data=json.loads(re.search(r'id="sm-d">(.*?)</script>',result,re.S)[1])
        original=json.loads(re.search(r'id="sm-d">(.*?)</script>',source,re.S)[1])
        for key,p in data['p'].items():
            self.assertEqual(len(p['b']),3)
            self.assertEqual(p['y'],original['p'][key]['y'])
            self.assertEqual(p['e'],original['p'][key]['e'])
            self.assertTrue(all(a[1]+1==b[0] for a,b in zip(p['b'],p['b'][1:])))
        self.assertEqual(result,simplify_adoption(result))
