"""Data contract, anonymity and graph invariants for the public V5 projection."""
from copy import deepcopy
from collections import Counter
from decimal import Decimal
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.viz_site import restructure_estimates as v5
from scripts.viz_site.check_v5 import standalone
from scripts.viz_site.public_page import PublicText

# The V5.1 pin (scripts/viz_site/data/snapshot_pin.json) and what that snapshot holds: 503 papers, 53 re-estimated (36 number matches,
# 8 same direction, 9 third group), 45 of them plotted, 3 confirmed; all 36 number matches are named, the other 17 are anonymous.
PIN_PATH = 'data/replication/snapshot_v5/snapshot_20261018T085028678040Z.json'
PIN_SHA = '736ed162b5a627dfff3665051e45425fa26b04392ade5c2a2d6d42f0d8fe1f1c'
OLD_PIN_PATH = 'data/replication/snapshot_v5/snapshot_20261013T102801529434Z.json'
TOTAL, ATTEMPTED, COUNTS, PLOTTED, CONFIRMED, NAMED, ANONYMOUS = 503, 53, [36, 8, 8, 1], 45, 3, 36, 17

class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.elements=[]; self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.elements.append((tag,dict(attrs)))

class EstimatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot=v5.load_snapshot()
        cls.html={lang:v5.render(lang) for lang in ('en','zh')}

    def test_catalogue_order_and_display_keys_ignore_snapshot_sort(self):
        catalogue = [json.loads(line)['paper_id'] for line in (v5.ROOT / 'data/replication/paper_catalog_v1.jsonl').read_text().splitlines() if line]
        self.assertEqual([p['paper_id'] for p in v5.catalogue_papers(self.snapshot)], catalogue)
        reordered = deepcopy(self.snapshot)
        reordered['papers'].reverse()
        self.assertEqual(v5.public_projection(self.snapshot, 'en'), v5.public_projection(reordered, 'en'))
        self.assertEqual([(i, p['paper_id'], gap) for i, p, gap in v5.plot_rows(self.snapshot)],
                         [(i, p['paper_id'], gap) for i, p, gap in v5.plot_rows(reordered)])

    def test_ledger_closes_and_primary_count_matches(self):
        s=self.snapshot
        self.assertEqual(sum(s['grouped_stage_counts'].values()),s['total'])
        self.assertEqual(v5.attempted(s),sum(bool(p['primary']) for p in s['papers']))
        self.assertEqual(s['matched_independent'],s['grouped_stage_counts']['attempted_matched']+s['grouped_stage_counts']['confirmed'])

    def test_three_public_categories_and_grid_show_only_reestimated_papers(self):
        counts = v5.public_counts(self.snapshot)
        self.assertEqual(list(counts), list(v5.PUBLIC_CATEGORIES))
        self.assertEqual(len(v5.PUBLIC_CATEGORIES), 4)
        self.assertEqual(list(counts.values()), COUNTS)
        self.assertEqual(sum(counts.values()), v5.attempted(self.snapshot))
        self.assertEqual(len(v5.public_projection(self.snapshot, 'en')), v5.attempted(self.snapshot))
        for lang,html in self.html.items():
            self.assertNotIn('v5-cell', html)       # V5.7: the pictogram is gone; the legend carries the counts
            labels=v5.category_labels(self.snapshot,lang)
            self.assertEqual(list(labels), list(v5.PUBLIC_CATEGORIES))
            legend=re.search(r'<div class="v5-legend"[^>]*>(.*?)</div>',html,re.S)[1]
            self.assertEqual(re.findall(r'<span data-category="([^"]+)"',legend), list(v5.PUBLIC_CATEGORIES))
            for category in v5.PUBLIC_CATEGORIES:
                self.assertIn(f'{v5.escape(labels[category])} <b>{counts[category]}</b>',html)
            self.assertNotIn('data-stage=', html)
            self.assertNotIn('v5-filter', html)

    def test_papers_without_a_reestimate_are_not_displayed(self):
        removed = self.snapshot['total'] - v5.attempted(self.snapshot)
        self.assertEqual(removed, TOTAL - ATTEMPTED)
        for lang,html in self.html.items():
            visible = PublicText(html).parts
            self.assertIsNone(re.search(r'not[_ -]yet[_ -]replicated|尚未复现', html, re.I))
            self.assertEqual(standalone(removed, visible), [])
            # Nothing else on the page describes the papers that were left out.
            self.assertNotIn('restricted data' if lang == 'en' else '受限数据', ' '.join(visible))
            # Owner, 2026-10-13/14: the size of the catalogue is shown nowhere.
            self.assertEqual(standalone(self.snapshot['total'], visible), [])
        self.assertIsNone(v5.public_category(next(p for p in self.snapshot['papers'] if not p.get('primary'))))

    def test_named_papers_link_to_their_own_page_and_the_rest_stay_unlinked(self):
        # V5.7 (owner, 2026-10-23): author-year title is a link to the SSRN page (else the DOI or working-paper page); no guesses.
        links = v5.paper_links()
        named = [p for p in self.snapshot['papers'] if v5.is_named(p)]
        self.assertEqual(len(named), 36)
        self.assertEqual(set(links) - {p['paper_id'] for p in named}, set())
        for url in links.values():
            self.assertRegex(url, r'^https://(papers\.ssrn\.com/sol3/papers\.cfm\?abstract_id=\d+|doi\.org/10\.\S+|www\.nber\.org/papers/w\d+|ideas\.repec\.org/\S+)$')
        for lang, html in self.html.items():
            rows = re.findall(r'<div class="v5-row" data-display-key="[^"]+"[^>]*><div class="v5-row-name">(.*?)</div>', html)
            self.assertEqual(len(rows), ATTEMPTED)
            external = [r for r in rows if 'target="_blank" rel="noopener"' in r]
            self.assertEqual(len(external), len(links))
            for r in rows:
                if not r.startswith('<a href="https://'):
                    self.assertRegex(r, r'^(Paper|论文) \d+$')
            self.assertNotIn('Sources', html)
            self.assertNotIn('来源', html)
            self.assertNotIn('Estimate source', html)

    def test_the_pictogram_grid_is_gone_and_one_section_carries_legend_and_chart(self):
        # V5.7 (owner, 2026-10-23; panel Card, Finkelstein, Roth): the 53-marker grid was deleted; its heading, number and legend
        # moved into the dot-chart section, legend above the chart.
        self.assertNotIn('v5-grid', v5.CSS)
        self.assertNotIn('v5-cell', v5.CSS)
        for lang, html in self.html.items():
            self.assertNotIn('v5-grid', html.replace(v5.CSS, ''))
            self.assertNotIn('id="v5-catalogue"', html)
            self.assertEqual(html.count('<h2 id="v5-gap">'), 1)
            heading = re.search(r'<h2 id="v5-gap">([^<]+)</h2>', html)[1]
            self.assertIn(' · 53', heading)
            self.assertIn('45', heading)
            self.assertLess(html.index('id="v5-gap"'), html.index('class="v5-legend"'))
            self.assertLess(html.index('class="v5-legend"'), html.index('class="v5-strip"'))

    def test_the_gap_strip_is_as_tall_as_its_lanes(self):
        # The strip was drawn for 36 lanes (304 px). Its height now follows the number plotted, so every lane sits inside it.
        for html in self.html.values():
            tops = [int(x) for x in re.findall(r'class="v5-strip-lane"[^>]*style="top:(\d+)px"', html)]
            height = int(re.search(r'<div class="v5-strip"[^>]*style="height:(\d+)px"', html)[1])
            self.assertEqual(len(tops), PLOTTED)
            self.assertEqual(tops, [v5.LANE_TOP + n * v5.LANE_STEP for n in range(PLOTTED)])
            self.assertEqual(height, v5.LANE_STEP * PLOTTED + v5.LANE_PAD)
            self.assertGreaterEqual(height, tops[-1] + v5.LANE_STEP)
        self.assertNotIn('height:304px', v5.CSS)
        self.assertEqual(v5.LANE_STEP * 36 + v5.LANE_PAD, 376)  # G7: lanes 10 px apart (marks 9 px)

    def test_numerical_matches_include_results_still_under_review(self):
        pending=[p for p in self.snapshot['papers'] if p.get('primary') and p['primary']['grade_numeric']=='TIGHT' and p['primary']['gate_reasons']]
        self.assertTrue(pending)
        for lang,expected in [('en','Number matches'),('zh','数字一致')]:
            for p in pending:
                self.assertEqual(v5.grade_label(p,lang),expected)
                self.assertEqual(v5.public_category(p),'number_matches')
        # F6 (Finkelstein MF1): the papers beyond 10% are two groups with exact names, whatever the snapshot holds.
        self.assertTrue(v5.has_mismatch(self.snapshot))
        self.assertEqual(v5.category_labels(self.snapshot,'en')['not_comparable'],'Not comparable')
        self.assertEqual(v5.category_labels(self.snapshot,'en')['opposite_sign'],'Opposite sign')
        self.assertEqual(v5.category_labels(self.snapshot,'zh')['not_comparable'],'不可比')
        self.assertEqual(v5.category_labels(self.snapshot,'zh')['opposite_sign'],'符号相反')
        base=v5.public_counts(self.snapshot)
        mixed=deepcopy(self.snapshot)
        p=next(p for p in mixed['papers'] if p['stage_group']=='attempted_not_consistent' and p['primary']['grade_numeric']=='TIGHT')
        p['primary']['grade_numeric']='NOT_CONSISTENT'
        self.assertEqual(v5.public_category(p),'opposite_sign')
        self.assertEqual(v5.public_counts(mixed)['number_matches'],base['number_matches']-1)
        self.assertEqual(v5.public_counts(mixed)['opposite_sign'],base['opposite_sign']+1)

    def test_the_definitions_line_names_the_two_groups_beyond_10_percent(self):
        # F6 (Finkelstein MF1): "Doesn't match" is gone. Not comparable = no published standard error to compare intervals (not plotted);
        # Opposite sign = beyond 10% with the opposite sign. The line is counted from the snapshot.
        self.assertIn("Same direction: beyond 10%, same sign, overlapping intervals. Not comparable: no published standard error to compare intervals (not plotted). Opposite sign: beyond 10%, opposite sign.",self.html['en'])
        self.assertIn('方向一致：符号相同，差距超出 10%，区间重叠。不可比：发表值无标准误、无法比较区间（不绘制）。符号相反：差距超出 10%，符号相反。',self.html['zh'])
        for lang in ('en','zh'):
            self.assertNotIn("Doesn’t match",self.html[lang])
            self.assertNotIn('对不上',self.html[lang])
        self.assertIn('Not comparable <b>8</b>',self.html['en'])
        self.assertIn('Opposite sign <b>1</b>',self.html['en'])
        self.assertIn('不可比 <b>8</b>',self.html['zh'])
        self.assertIn('符号相反 <b>1</b>',self.html['zh'])
        self.assertIn('<h3 data-category="not_comparable">Not comparable · 8</h3>',self.html['en'])
        self.assertIn('<h3 data-category="opposite_sign">Opposite sign · 1</h3>',self.html['en'])
        self.assertIn('<h3 data-category="not_comparable">不可比 · 8</h3>',self.html['zh'])
        self.assertIn('<h3 data-category="opposite_sign">符号相反 · 1</h3>',self.html['zh'])
        self.assertEqual(v5.third_group_split(self.snapshot),(8,1,0))

    def test_the_third_group_split_and_the_reestimate_counts_follow_the_snapshot(self):
        # F1: the definitions line and the Explore line on re-estimates are counted from the snapshot, not typed.
        self.assertEqual(v5.reestimate_summary(self.snapshot),{'papers':53,'within':36,'other':17,'closer':13})
        self.assertEqual(v5.reestimate_summary(),v5.reestimate_summary(self.snapshot))
        self.assertEqual(v5.third_group_split(self.snapshot),(8,1,0))
        moved=deepcopy(self.snapshot)
        paper=next(p for p in moved['papers'] if p['paper_id'].startswith('zoorob2024'))
        paper['primary']['ours']['point']=-0.4          # the published -0.51 and a smaller size: no longer the opposite sign
        self.assertEqual(v5.third_group_split(moved),(8,0,1))
        self.assertEqual(v5.reestimate_summary(moved)['closer'],14)
        # A same-sign paper with intervals that do not overlap would sit in the Opposite sign group under a wrong name: the build stops.
        with self.assertRaises(ValueError):
            v5.third_group_definitions(moved,True)
        # A paper without a published standard error that the strip would plot stops the build.
        plotted=deepcopy(self.snapshot)
        paper=next(p for p in plotted['papers'] if p.get('primary') and v5.public_category(p)=='not_comparable' and not v5.number(p['primary']['published'].get('se')))
        paper['primary']['grade']='TIGHT'; paper['primary']['grade_numeric']='NOT_CONSISTENT'; paper['primary']['numeric_checks']['grade']='TIGHT'
        with self.assertRaises(ValueError):
            v5.third_group_split(plotted)

    def test_the_version_row_is_tidy_and_keeps_every_fact(self):
        # F1d (McCloskey S-N, round 1 Card S11): no stray "The", the year in one pair of parentheses, the working-paper number after a semicolon.
        shown = {pid: v5.version_display(entry) for pid, entry in v5.reviewed_descriptions().items()}
        self.assertEqual(sorted(shown.values()), ['Proceedings of the National Academy of Sciences 116(26):12624-12626 (2019)',
                                                  'Review of Economics and Statistics 95(1):34-49 (2013); NBER Working Paper 16194',
                                                  'Review of Economics and Statistics 98(4):671-684 (2016)'])
        for pid, text in shown.items():
            entry = v5.reviewed_descriptions()[pid]
            self.assertNotRegex(text, r'^The |\)\s*\(')
            self.assertIn(str(entry['year']), text)
            self.assertTrue(all(word in text for word in re.sub(r'^The\s+|[()]', ' ', entry['venue']).split()), pid)   # nothing of the venue string is lost
        for lang in ('en', 'zh'):
            self.assertIn('Review of Economics and Statistics 95(1):34-49 (2013); NBER Working Paper 16194', self.html[lang])
            self.assertNotIn('The Review of Economics and Statistics', self.html[lang])

    def test_ledger_mutation_is_rejected(self):
        s=deepcopy(self.snapshot);s['grouped_stage_counts']['not_started']+=1
        with self.assertRaisesRegex(ValueError,'grouped count'):
            v5.validate_snapshot(s)

    def test_confirmed_coder_and_target_mutations_are_rejected(self):
        for field in ('coder_id','target'):
            s=deepcopy(self.snapshot);p=next(p for p in s['papers'] if p['stage_group']=='confirmed')
            audit=next(a for a in p['audits'] if a['eligible'])
            run=next(r for r in p['runs'] if r['run_id']==audit['run_id'])
            run[field]=p['primary'][field] if field=='coder_id' else {}
            with self.assertRaisesRegex(ValueError,'audit coder/target/spec mismatch'):
                v5.validate_snapshot(s)

    def test_primary_substitution_is_rejected(self):
        s=deepcopy(self.snapshot);p=next(p for p in s['papers'] if p['primary'])
        p['primary']['run_id']='replacement'
        with self.assertRaisesRegex(ValueError,'primary id mismatch'):
            v5.validate_snapshot(s)

    def test_decimal_endpoints(self):
        self.assertTrue(v5.tight({'point':'1.1'},{'point':'1','se':0}))
        self.assertFalse(v5.tight({'point':'1.1000000000001'},{'point':'1','se':0}))
        self.assertTrue(v5.tight({'point':'-.1'},{'point':'0','se':'1'}))
        self.assertFalse(v5.tight({'point':None},{'point':'1'}))

    def test_zero_missing_and_unit_mismatch_never_become_zero_gap(self):
        self.assertIsNone(v5.gap({'ours':{'point':1},'published':{'point':0,'se':0}}))
        self.assertIsNone(v5.gap({'ours':{},'published':{'point':1}}))
        self.assertIsNone(v5.gap({'ours':{'point':1},'published':{'point':1},'numeric_checks':{'reason_codes':['unit_mismatch']}}))
        self.assertEqual(v5.gap({'ours':{'point':1.2},'published':{'point':1}}),Decimal('.2'))

    def test_public_projection_and_render_contain_no_unconfirmed_identity(self):
        for lang,html in self.html.items():
            public=json.dumps(v5.public_projection(self.snapshot,lang),ensure_ascii=False)
            for p in self.snapshot['papers']:
                if p['stage_group']=='confirmed':
                    continue
                for field in ('paper_id','slug','doi','title'):
                    value=p.get(field)
                    if value:
                        self.assertNotIn(value,public)
                        # V5.7: a named paper (reviewed citation) links to its SSRN, DOI or working-paper page; its DOI may sit in that href only.
                        link = v5.paper_links().get(p['paper_id']) if v5.is_named(p) else None
                        self.assertNotIn(value,html.replace(v5.escape(link or '',quote=True),''))
            self.assertNotIn('data/replication/r1/',html)

    def test_the_page_has_no_interactive_picker_or_script_injection(self):
        for html in self.html.values():
            self.assertNotIn('id="v5-paper-picker"',html)
            self.assertNotIn('ArrowDown',html)
            self.assertNotIn('innerHTML',html)

    def test_plot_groups_categories_then_sorts_first_estimate_signed_gap(self):
        rows=v5.plot_rows(self.snapshot)
        categories=[v5.PUBLIC_CATEGORIES.index(v5.public_category(p)) for _,p,_ in rows]
        self.assertEqual(categories,sorted(categories))
        for category in v5.PUBLIC_CATEGORIES[:3]:
            values=[value for _,p,value in rows if v5.public_category(p)==category and value is not None]
            self.assertEqual(values,sorted(values))
        self.assertEqual(len(rows),v5.attempted(self.snapshot))
        self.assertEqual(len({p['paper_id'] for _,p,_ in rows}),len(rows))

    def test_port_needs_exact_target_and_spec(self):
        run={'code_origin':'port_of_author_code','target':{'table':'1'},'spec':{'outcome':'y'},'ours':{'point':1},'published':{'point':1}}
        p={'primary':deepcopy(run),'runs':[run]}
        self.assertEqual(len(v5.comparable_ports(p)),1)
        p['runs'][0]['spec']={'outcome':'z'}
        self.assertEqual(v5.comparable_ports(p),[])

    def test_progress_uses_numeric_matches_and_plain_comparison(self):
        for lang in ('en','zh'):
            text=v5.progress_line(lang)
            for count in (v5.attempted(self.snapshot), v5.public_counts(self.snapshot)['number_matches']):
                self.assertIn(str(count),text)
            self.assertNotIn(str(self.snapshot['total']),text)
            self.assertIn('10%',text)
            self.assertNotIn('confirmed',text)
            self.assertNotIn('确认',text)

    def test_progress_markup_keeps_re_estimated_on_one_line_without_changing_the_text(self):
        marked = v5.progress_html('en')
        self.assertIn('has <span class="nx-seg">re-estimated</span> ', marked)
        self.assertEqual(re.sub(r'<[^>]+>', '', marked), v5.escape(v5.progress_line('en', include_date=False)))
        self.assertEqual(v5.progress_html('zh'), v5.escape(v5.progress_line('zh', include_date=False)))
        self.assertIn(marked, self.html['en'])
        self.assertIn(v5.progress_html('zh'), self.html['zh'])

    def test_progress_can_share_a_single_homepage_date_and_count_definition(self):
        date=self.snapshot['as_of'][:10]
        for lang in ('en','zh'):
            self.assertIn(date,v5.progress_line(lang))
            self.assertNotIn(date,v5.progress_line(lang,include_date=False))
            self.assertIn(v5.count_definition(lang),self.html[lang])

    def test_no_method_disclosure_one_empty_anchor_and_only_confirmed_result_sections(self):
        for html in self.html.values():
            elements=Elements(html).elements
            # Owner, 2026-10-14 (R18): the "Method & materials" disclosure had nothing left to show and is gone; the id stays on
            # one empty block at the end of the article because the four policy_exposure stubs link to #method-materials.
            self.assertEqual(sum(a.get('class')=='v5-method' for _,a in elements),0)
            self.assertEqual(sum(tag=='details' and a.get('id')=='method-materials' for tag,a in elements),0)
            self.assertEqual([(tag,a) for tag,a in elements if a.get('id')=='method-materials'],[('div',{'id':'method-materials'})])
            self.assertTrue(html.endswith('<div id="method-materials"></div></article><script>'+v5.SCRIPT+'</script>'))
            self.assertEqual(sum(a.get('class')=='v5-result' for _,a in elements),self.snapshot['confirmed_independent'])
            self.assertNotIn('github.com',html)
            self.assertNotIn('l2-noise',html)

    def test_method_anchor_is_invisible_unstyled_and_not_focusable(self):
        # The anchor is a bare empty div: no text, no tabindex or href, no class, and no rule in the page CSS selects it
        # (the old .v5-method rules are gone), so it adds no height, border or margin.
        self.assertNotIn('v5-method',v5.CSS)
        self.assertNotIn('method-materials',v5.CSS)
        for lang,html in self.html.items():
            for text in ('Method & materials','Method &amp; materials','方法与材料'):
                self.assertNotIn(text,html,lang)
            self.assertEqual(html.count('method-materials'),1,lang)
            self.assertIn('</details>\n<div id="method-materials"></div></article>',html)

    def run_landing_script(self,body):
        """Run the page's own hash-landing script in a minimal DOM (no browser, no network)."""
        harness='''const vm=require('vm');
const make=(tag,parent,extra)=>Object.assign({tagName:tag,parentElement:parent,scrolled:0,scrollIntoView(o){this.scrolled++;this.last=o}},extra||{});
const html=make('HTML',null),article=make('ARTICLE',html);
const events={};
const run=(hash,ids)=>{const location={hash};
 const document={getElementById:x=>ids[x]||null,addEventListener:(k,v)=>events[k]=v};
 events.hashchange=null;events.click=null;
 vm.runInNewContext(CODE,{location,document,window:{addEventListener:(k,v)=>events[k]=v}});};
'''.replace('CODE',json.dumps(v5.SCRIPT))+body
        result=subprocess.run(['node','-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_landing_script_handles_a_target_that_is_not_a_details_element(self):
        self.run_landing_script('''
const anchor=make('DIV',article);          // what #method-materials is now
run('#method-materials',{'method-materials':anchor});
if(anchor.scrolled!==1||anchor.last.block!=='start')throw Error('no landing scroll');
for(const node of [anchor,article,html])if('open' in node)throw Error('open set on '+node.tagName);
events.hashchange();                       // same page, hash changes again
if(anchor.scrolled!==2)throw Error('hashchange did not land');
events.click({target:{closest:()=>({hash:'#method-materials'})}});   // an in-page link to the anchor
if('open' in anchor)throw Error('click opened a non-details element');
events.click({target:{closest:()=>null}}); // a click that is not on a link
run('#no-such-id',{'method-materials':anchor});      // unknown id, malformed escape: no throw, no scroll
run('#%E0%A4%A',{'method-materials':anchor});
if(anchor.scrolled!==2)throw Error('unknown id scrolled');
''')

    def test_landing_script_still_opens_details_targets_and_their_ancestors(self):
        self.run_landing_script('''
const outer=make('DETAILS',article,{open:false}),inner=make('DETAILS',outer,{open:false});
run('#v5-result-233',{'v5-result-233':inner});
if(!inner.open||!outer.open||inner.scrolled!==1)throw Error('details not opened');
inner.open=false;outer.open=false;
events.click({target:{closest:()=>({hash:'#v5-result-233'})}});
if(!inner.open||outer.open)throw Error('link click should open only the target details');
''')

    def test_missing_reason_is_not_invented_from_queue_presence(self):
        p={'stage':'waiting_login','queue':{'state':'BLOCKED_NEEDS_LOGIN_PACKAGE'},'triage':[]}
        self.assertEqual(v5.reason(p,'en'),'Evidence missing')
        p['triage']=[{'record':{'decision':'READY_R1A','reason':'Old decision'}}]
        self.assertEqual(v5.reason(p,'en'),'Evidence missing')
        p['triage']=[{'record':{'decision':'NEEDS_LOGIN_PACKAGE'}}]
        self.assertEqual(v5.reason(p,'en'),'Login required')
        p['triage']=[];p['queue']['failure_reason']='Package needs login'
        self.assertEqual(v5.reason(p,'en'),'Login required')

    def test_secondary_out_of_range_values_include_arrow(self):
        self.assertEqual(v5.fmt_gap(Decimal('.45')),'→ 45.000%')
        self.assertEqual(v5.fmt_gap(Decimal('-.4')),'← −40.000%')
        self.assertEqual(v5.fmt_gap(None),'—')
        self.assertEqual(v5.fmt_gap(Decimal('.038')),'3.800%')
        self.assertEqual(v5.fmt_gap(Decimal('-.000001')),'0.000%')
        for html in self.html.values():
            import re
            values = re.findall(r'class="v5-row-value">(.*?)</div>', html)
            self.assertTrue(all(v == '—' or v.endswith('%') for v in values))
            self.assertTrue(all(a['title'].endswith('%') for _, a in Elements(html).elements if a.get('class') == 'v5-strip-lane'))

    def test_a_published_interval_is_labelled_as_calculated_exactly_where_the_paper_prints_none(self):
        # Building codes: no CI printed; Goodman: the journal table prints the SE only; cannabis: the paper prints its own interval.
        for lang, labels in [('en', ['(calculated from reported SE; no CI printed in the paper)', '(calculated from reported SE)']),
                             ('zh', ['（根据报告的标准误计算；原文未列置信区间）', '（根据报告的标准误计算）'])]:
            for label in labels:
                self.assertEqual(self.html[lang].count(label), 1, label)
            self.assertEqual(self.html[lang].count('calculated from reported SE' if lang == 'en' else '根据报告的标准误计算'), 2)
        self.assertIn('2.0 / 47.6</td>', self.html['en'])

    def test_noncomparable_grades_have_no_numeric_gap_even_with_finite_points(self):
        run={'ours':{'point':1.2},'published':{'point':1},'grade':'NOT_COMPARABLE'}
        self.assertIsNone(v5.gap(run))
        unavailable=[(i,p,g) for i,p,g in v5.plot_rows(self.snapshot) if p['stage_group']=='attempted_not_comparable']
        self.assertEqual(len(unavailable),self.snapshot['grouped_stage_counts']['attempted_not_comparable'])
        self.assertTrue(all(g is None for _,_,g in unavailable))
        keys,numbers=v5.display_keys(self.snapshot),v5.paper_numbers(self.snapshot)
        for lang,html in self.html.items():
            for i,p,_ in unavailable:
                self.assertIn(f'data-display-key="{keys[p["paper_id"]]}" data-comparable="false"',html)
                self.assertNotIn(v5.unavailable_reason(p,lang),html)
            lanes=[a for _,a in Elements(html).elements if a.get('class')=='v5-strip-lane']
            self.assertEqual(len(lanes),v5.attempted(self.snapshot)-len(unavailable))
            self.assertFalse(any(v5.label(p,numbers[p['paper_id']],lang) in a.get('title','') for i,p,_ in unavailable for a in lanes))

    def test_anonymous_labels_count_from_one_in_list_order_and_no_number_is_a_catalogue_position(self):
        # Owner, 2026-10-14 (R21): "Paper 1" ... "Paper N" in the order the list shows them, the same in both languages; the confirmed
        # papers open from v5-result-1, v5-result-2 ... R27: the papers whose numbers match are named by citation, so only
        # the others are counted, 1..k (the eight "same direction" and the nine of the third group: 1..17).
        anonymous=[p['paper_id'] for _,p,_ in v5.plot_rows(self.snapshot) if not v5.is_named(p)]
        n=len(anonymous)
        self.assertEqual(n,ANONYMOUS)
        self.assertEqual(n,v5.attempted(self.snapshot)-v5.public_counts(self.snapshot)['number_matches'])
        self.assertEqual(v5.paper_numbers(self.snapshot),{pid:k for k,pid in enumerate(anonymous,1)})
        self.assertEqual(v5.display_keys(self.snapshot),{p['paper_id']:f'p{k:02}' for k,(_,p,_) in enumerate(v5.plot_rows(self.snapshot),1)})
        for lang,word in (('en','Paper'),('zh','论文')):
            html=self.html[lang]
            self.assertEqual([int(k) for k in re.findall(rf'<div class="v5-row-name">{word} (\d+)</div>',html)],list(range(1,n+1)))
            self.assertFalse(re.search(rf'{word} 0\d',html))
            self.assertFalse(re.search(r'data-(?:display-)?key="p\d{3}"',html))
            self.assertEqual(re.findall(r'id="(v5-result-\d+)"',html),[f'v5-result-{k}' for k in range(1,CONFIRMED+1)])
            for anchor in re.findall(r'href="#(v5-result-\d+)"',html):
                self.assertIn(f'id="{anchor}"',html)
        unnamed=next(p for _,p,_ in v5.plot_rows(self.snapshot) if not v5.is_named(p))
        self.assertEqual(v5.label(unnamed,7,'en'),'Paper 7')
        self.assertEqual(v5.label(unnamed,7,'zh'),'论文 7')

    def test_the_papers_whose_numbers_match_are_named_and_no_other_paper_is(self):
        # Owner, 2026-10-14 (R27): "name the papers whose numbers match; the rest stay anonymous".
        matching={p['paper_id'] for _,p,_ in v5.plot_rows(self.snapshot) if v5.public_category(p)=='number_matches'}
        self.assertEqual(len(matching),NAMED)
        self.assertEqual(set(v5.reviewed_citations()),matching)
        for _,p,_ in v5.plot_rows(self.snapshot):
            self.assertEqual(v5.is_named(p),p['paper_id'] in matching)
        for lang,word in (('en','Paper'),('zh','论文')):
            html=self.html[lang]
            names=re.findall(r'<div class="v5-row-name">(.*?)</div>',html)
            self.assertEqual(len(names),ATTEMPTED)
            self.assertEqual(sum(1 for name in names if not re.fullmatch(rf'{word} \d+',re.sub(r'<[^>]+>','',name))),NAMED)
            # The anonymous rows are exactly the ones outside the group: same direction, then the third group.
            self.assertEqual([re.sub(r'<[^>]+>','',name) for name in names[NAMED:]],[f'{word} {k}' for k in range(1,ANONYMOUS+1)])
            self.assertEqual(html.count('<h3 data-category="number_matches">'),1)

    def test_citations_are_author_year_and_chinese_keeps_the_latin_names(self):
        by_id={p['paper_id']:p for _,p,_ in v5.plot_rows(self.snapshot)}
        expected={
            'jacobsen2013-8f3fae1c':('Jacobsen & Kotchen (2013)','Jacobsen 与 Kotchen（2013）'),
            'shover2019-e698786e':('Shover et al. (2019)','Shover 等（2019）'),
            'hinrichs2012-05c49ad9':('Hinrichs (2012)','Hinrichs（2012）'),
            'caceresdelpiano2012-72596a1d':('Cáceres-Delpiano & Giolito (2012)','Cáceres-Delpiano 与 Giolito（2012）'),
            'cengiz2019-a72ccb47':('Cengiz et al. (2019)','Cengiz 等（2019）'),
            'son2022-96bf154a':('Son (2022)','Son（2022）'),
            'brummund2020-657584ac':('Brummund & Strain (2020)','Brummund 与 Strain（2020）'),
            'backes2012-301f80a1':('Backes (2012)','Backes（2012）'),
            'gruber1994-d375ee8c':('Gruber (1994)','Gruber（1994）'),
            'jayaratne1996-9d8d9ce9':('Jayaratne & Strahan (1996)','Jayaratne 与 Strahan（1996）'),
            'rees2019-234de41c':('Rees et al. (2019)','Rees 等（2019）'),
            'goodman2016-c6f7ffbe':('Goodman (2016)','Goodman（2016）'),
        }
        for pid,(en,zh) in expected.items():
            self.assertEqual(v5.label(by_id[pid],None,'en'),en)
            self.assertEqual(v5.label(by_id[pid],None,'zh'),zh)
        shown={lang:[v5.label(by_id[pid],None,lang) for pid in v5.reviewed_citations()] for lang in ('en','zh')}
        for lang in ('en','zh'):
            self.assertEqual(len(set(shown[lang])),NAMED)
            for text in shown[lang]:
                self.assertIsNone(re.search(r'[\u4e00-\u9fff]',text) if lang=='en' else None)
                self.assertRegex(text,r'^[^()（）]+[(（]\d{4}[)）]$')
        # More than two authors: the first author and "et al."; two: both; one: the surname alone.
        for pid,entry in v5.reviewed_citations().items():
            count=len(entry['authors'])
            en=v5.citation_text(entry,'en')
            self.assertEqual(en.startswith(entry['authors'][0]),True)
            self.assertEqual(' & ' in en,count==2)
            self.assertEqual(' et al. ' in en,count>2)
        # The strip tooltips carry the same labels as the list.
        for lang,html in self.html.items():
            titles=[a['title'] for _,a in Elements(html).elements if a.get('class')=='v5-strip-lane']
            self.assertEqual(len(titles),PLOTTED)
            self.assertTrue(all(re.match(r'[^·]+ · ',t) for t in titles))
        self.assertIn('Holbein &amp; Hillygus (2016)',self.html['en'])
        self.assertIn('Holbein 与 Hillygus（2016）',self.html['zh'])

    def test_each_row_shows_the_label_and_the_gap_of_its_own_paper(self):
        from scripts.viz_site.check_v5 import estimates_row_issues
        for lang,html in self.html.items():
            self.assertEqual(estimates_row_issues(html,self.snapshot,lang),[])
        # Two swapped labels are the mismatch the check exists for.
        swapped=self.html['en'].replace('Holbein &amp; Hillygus (2016)','@@').replace('Cengiz et al. (2019)','Holbein &amp; Hillygus (2016)').replace('@@','Cengiz et al. (2019)')
        self.assertNotEqual(swapped,self.html['en'])
        self.assertTrue(estimates_row_issues(swapped,self.snapshot,'en'))
        # A named paper in the "same direction" group is a leak of the rule.
        leaked=self.html['en'].replace('>Paper 1<','>Smith (2001)<')
        self.assertTrue(estimates_row_issues(leaked,self.snapshot,'en'))

    def test_every_citation_is_rechecked_against_the_catalogue_and_the_snapshot(self):
        entries=deepcopy(v5.reviewed_citations())
        def failing(change,pattern):
            changed=deepcopy(entries)
            change(changed)
            with patch.object(v5,'reviewed_citations',return_value=changed):
                with self.assertRaisesRegex(ValueError,pattern):
                    v5.validate_citations(self.snapshot)
        with patch.object(v5,'reviewed_citations',return_value=entries):
            v5.validate_citations(self.snapshot)
        failing(lambda e:e['jacobsen2013-8f3fae1c'].update(year=2014),'citation year')
        failing(lambda e:e['jacobsen2013-8f3fae1c'].update(authors=['Jacobsen','Kochen']),'citation authors differ')
        failing(lambda e:e['jacobsen2013-8f3fae1c'].update(authors=['Jacobsen']),'citation authors differ')
        failing(lambda e:e['jacobsen2013-8f3fae1c'].update(authors=['Kotchen','Jacobsen']),'citation authors differ')
        failing(lambda e:e['cengiz2019-a72ccb47'].update(authors=['Cengiz','Dube','Lindner']),'citation authors differ')
        failing(lambda e:e.update({'nobody1999-00000000':{'authors':['Nobody'],'year':1999}}),'not in the snapshot')
        # A paper outside the group is never named, even with a perfect entry.
        outside=next(p for _,p,_ in v5.plot_rows(self.snapshot) if v5.public_category(p)=='same_direction')
        record=v5._CATALOGUE_RECORDS[outside['paper_id']]
        failing(lambda e:e.update({outside['paper_id']:{'authors':[record['first_author']],'year':record['year']}}),'only papers whose numbers match')
        # Two papers may not share a label.
        failing(lambda e:e.update({'yan2021-b3c3cb05':dict(e['myers2017-99c9b319'],year=2021)}),'citation')

    def test_catalogue_authors_join_a_lone_initial_to_the_name_before_it(self):
        self.assertEqual(v5.catalogue_authors({'authors':['Son','J.']}),['Son J.'])
        self.assertEqual(v5.catalogue_authors({'authors':['Carpenter','Lawler']}),['Carpenter','Lawler'])
        self.assertEqual(v5.catalogue_authors({'authors':['Holbein JB','Hillygus DS']}),['Holbein JB','Hillygus DS'])
        self.assertEqual(v5._fold('Cáceres-Delpiano J'),'caceres delpiano j')

    def test_the_citation_file_lists_local_sources_that_exist(self):
        data=json.loads(v5.CITATIONS_FILE.read_text(encoding='utf-8'))
        self.assertEqual(sorted(data),['about','papers'])
        for pid,entry in data['papers'].items():
            self.assertEqual(sorted(k for k in entry if k!='note'),['authors','sources','year'],pid)
            self.assertIn('paper_catalog_v1.jsonl',entry['sources'])
            self.assertGreaterEqual(len(entry['sources']),2,pid)
            for source in entry['sources']:
                self.assertTrue((v5.ROOT/'data/replication'/source).is_file(),(pid,source))
        self.assertIn('citation_verified',data['papers']['brummund2020-657584ac']['note'])

    def test_no_internal_anchor_of_the_estimates_page_breaks(self):
        for lang,html in self.html.items():
            ids=set(re.findall(r'\bid="([^"]+)"',html))
            for anchor in re.findall(r'href="#([^"]+)"',html):
                self.assertIn(anchor,ids,(lang,anchor))
            # The confirmed papers keep their rows' links and their detail sections (numbered in catalogue order); the other named papers have no section to open.
            confirmed=[p for _,p,_ in v5.plot_rows(self.snapshot) if p['stage_group']=='confirmed']
            self.assertEqual(len(confirmed),CONFIRMED)
            self.assertEqual(re.findall(r'<a href="#(v5-result-\d+)">([^<]+)</a>',html),
                             [(v5.result_anchor(self.snapshot,p['paper_id']),'details' if lang=='en' else '详情') for p in confirmed])

    def test_the_second_coder_is_not_called_independent_and_the_cannabis_estimator_is_two_way_fixed_effects(self):
        for lang,html in self.html.items():
            for text in ('second, independent','第二个独立'):
                self.assertNotIn(text,html)
        self.assertIn('<th scope="row">A second AI agent</th>',self.html['en'])
        self.assertIn('a second AI agent agrees',self.html['en'])
        self.assertIn('<th scope="row">第二个 AI 智能体</th>',self.html['zh'])
        self.assertIn('第二个 AI 智能体结果一致',self.html['zh'])
        # Frozen estimate (FREEZE.json spec_choices "fixed_effects" and "estimator"): state and year fixed effects, numerically OLS, state-clustered SE.
        self.assertIn('<dt>Estimator</dt><dd>Two-way fixed effects; state-clustered SE</dd>',self.html['en'])
        self.assertIn('<dt>估计方法</dt><dd>双向固定效应；标准误按州聚类</dd>',self.html['zh'])
        self.assertNotIn('OLS with state-clustered SE',self.html['en'])
        self.assertNotIn('普通最小二乘，标准误按州聚类',self.html['zh'])
        self.assertIn('<dt>Estimator</dt><dd>Unweighted OLS; state-clustered SE; state and year effects; control for log overall enrollment</dd>',self.html['en'])
        self.assertIn('<dt>估计方法</dt><dd>非加权普通最小二乘；标准误按州聚类；控制州效应、年份效应及总入学人数的对数</dd>',self.html['zh'])

    def test_plot_status_marks_and_single_strip_are_explicit(self):
        for html in self.html.values():
            elements=Elements(html).elements
            classes=Counter(a.get('class') for _,a in elements)
            self.assertEqual(classes['v5-strip'],1)
            self.assertEqual(classes['v5-mark v5-number_matches'],v5.public_counts(self.snapshot)['number_matches'])
            self.assertEqual(classes['v5-coder-agrees'],self.snapshot['confirmed_independent'])
            self.assertEqual(classes['v5-row'],v5.attempted(self.snapshot))
            self.assertTrue(any(tag=='details' and a.get('class')=='v5-row-list' and 'open' not in a for tag,a in elements))
            self.assertLess(html.index('id="v5-confirmed"'),html.index('class="v5-row-list"'))
            self.assertLess(html.index('id="v5-gap"'),html.index('id="v5-confirmed"'))
            self.assertLess(html.index('class="v5-legend"'),html.index('class="v5-strip"'))

    def test_grid_category_order_and_lean_public_copy(self):
        for lang,html in self.html.items():
            groups=re.findall(r'<h3 data-category="([^"]+)">',html)
            self.assertEqual([v5.PUBLIC_CATEGORIES.index(c) for c in groups],sorted(v5.PUBLIC_CATEGORIES.index(c) for c in groups))
            for token in ('Frozen', 'frozen', 'SHA-256', 'Snapshot', 'queue', 'triage', 'pending checks', '口径待核', '冻结', '两人确认', '数字对上待复核', '已做完未计入', '等期刊原表', '等数据访问', '数据受限'):
                self.assertNotIn(token,html)
            self.assertIn(v5.count_definition(lang),html)
            self.assertEqual(html.count('class="v5-coder-agrees"'),CONFIRMED)
        self.assertNotIn('passed every check',self.html['en'])
        # F1 (Card S9, McCloskey S1): the 7 are among the 36 number matches, and the English sentence says so as the Chinese one does.
        # F1d (McCloskey S5): the 7 are among the 36 number matches, and the sentence names the 36 (Chinese: 36 篇中有 7 篇).
        self.assertIn('For 7 of the 36, the sample, policy timing and model specification',self.html['en'])
        self.assertIn('36 篇中有 7 篇的样本、政策时点与模型设定',self.html['zh'])
        self.assertNotIn('For 7 papers',self.html['en'])
        # R29 (ES8, fallback): the data-track sentence is printed once, above the cards, not in each card.
        for lang,sentence in (('en','Starting from the authors’ posted data'),('zh','AI 智能体使用作者公开的数据')):
            self.assertEqual(self.html[lang].count(sentence),1)
            self.assertLess(self.html[lang].index(sentence),self.html[lang].index('<details class="v5-result"'))
        self.assertIn('One selected estimate per paper; papers with the easiest-to-access data go first.',self.html['en'])
        self.assertNotIn('with the easiest-to-access data first',self.html['en'])

    def test_tolerance_disclosure_covers_standard_error_allowance(self):
        # This counted result is more than 10% from the published point;
        # the larger standard error is what makes the comparison qualify.
        self.assertTrue(v5.tight({'point': '.017624'}, {'point': '.020', 'se': '.024'}))
        self.assertFalse(v5.tight({'point': '.017624'}, {'point': '.020'}))
        self.assertIn('within 10% of the larger of the published estimate’s absolute value and its standard error', v5.progress_line('en'))   # F1d (Liebman)
        self.assertIn('若发表标准误更大', v5.progress_line('zh'))
        # R29 (ES2, fallback of the trim spec): the research-design limit stays, in fewer words.
        self.assertIn('the research design is not tested', v5.count_definition('en'))
        self.assertIn('研究设计未经检验', v5.count_definition('zh'))

    def test_no_authorship_line_or_update_stamp_and_missing_interval_reason_are_explicit(self):
        # Owner, 2026-10-14: "Estimates和Method那两句也删". R17 left the Method & materials block as a title-only shell for its
        # anchor; R18 replaced the shell by one empty div with the same id (see the anchor tests above).
        for lang, html in self.html.items():
            for text in ('Research design, rules and review', '研究设计、规则与审阅', 'Results updated', '结果更新'):
                self.assertNotIn(text, html)
            self.assertEqual([a for tag,a in Elements(html).elements if tag=='time'], [])
            self.assertIn('<div id="method-materials"></div>', html)
            self.assertNotRegex(html, r'<details[^>]*>\s*<summary>[^<]*</summary>\s*</details>')
            self.assertNotIn('we wrote', html)
            self.assertNotIn('second coder', html)
        self.assertIn('no published standard error to compare intervals', self.html['en'])
        self.assertIn(f'{PLOTTED} estimates plotted', self.html['en'])
        self.assertIn(f'{ATTEMPTED-PLOTTED} without comparable intervals are omitted', self.html['en'])
        self.assertIn(f'绘制 {PLOTTED} 项估计', self.html['zh'])
        self.assertIn(f'{ATTEMPTED-PLOTTED} 项缺少可比较区间的估计不绘制', self.html['zh'])
        self.assertIn('−30%', self.html['en'])
        self.assertIn('−10%', self.html['en'])
        self.assertNotIn('standard error or 0', self.html['en'])

    def test_public_counts_follow_changed_first_estimate(self):
        changed=deepcopy(self.snapshot)
        p=next(p for p in changed['papers'] if v5.public_category(p)=='number_matches')
        p['primary']['grade_numeric']='DIRECTIONAL'
        counts=v5.public_counts(changed)
        self.assertEqual(list(counts.values()),[COUNTS[0]-1,COUNTS[1]+1,COUNTS[2],COUNTS[3]])
        with patch.object(v5,'load_snapshot',return_value=changed):
            for lang in ('en','zh'):
                html=v5.render(lang)
                self.assertIn(str(COUNTS[0]-1),v5.progress_line(lang))
                self.assertIn(f'{v5.category_labels(changed,lang)["same_direction"]} <b>{COUNTS[1]+1}</b>',html)


    # ---- V5.1: the pin file ----
    def pin_file(self, **change):
        pin = dict(json.loads(v5.PIN_FILE.read_text()), **change)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'snapshot_pin.json'
        path.write_text(json.dumps(pin))
        return path

    def test_the_pin_file_names_the_release_snapshot_and_the_build_uses_it(self):
        pin = json.loads(v5.PIN_FILE.read_text())
        self.assertEqual(sorted(pin), ['note', 'pinned_utc', 'snapshot_path', 'snapshot_sha256'])
        self.assertEqual((pin['snapshot_path'], pin['snapshot_sha256']), (PIN_PATH, PIN_SHA))
        self.assertEqual(v5.digest((v5.ROOT / PIN_PATH).read_bytes()), PIN_SHA)
        self.assertEqual(v5.resolve_pin(), (PIN_PATH, PIN_SHA, 'pin file'))
        meta = v5.build_metadata()
        self.assertEqual((meta['snapshot_path'], meta['snapshot_sha256'], meta['pin_source']), (PIN_PATH, PIN_SHA, 'pin file'))
        self.assertEqual((meta['total'], meta['attempted'], meta['confirmed'], meta['release_ready']), (TOTAL, ATTEMPTED, CONFIRMED, True))
        # latest.json is not read: a later wave moves it, never the build.
        self.assertTrue(re.fullmatch(r'data/replication/snapshot_v5/snapshot_\d+T\d+Z\.json', pin['snapshot_path']))

    def test_a_pin_file_with_another_digest_or_an_unknown_capture_is_rejected(self):
        with patch.object(v5, 'PIN_FILE', self.pin_file(snapshot_sha256='0' * 64)):
            with self.assertRaisesRegex(ValueError, 'pinned snapshot differs from its recorded sha256'):
                v5.load_snapshot.__wrapped__()
        # Another immutable capture with the digest of this one is the same mismatch (the bytes decide, not the name).
        with patch.object(v5, 'PIN_FILE', self.pin_file(snapshot_path=OLD_PIN_PATH)):
            with self.assertRaisesRegex(ValueError, 'pinned snapshot differs from its recorded sha256'):
                v5.load_snapshot.__wrapped__()
        for bad in (dict(snapshot_path='data/replication/snapshot_v5/latest.json'), dict(snapshot_sha256='ABC'), dict(snapshot_path=PIN_PATH + 'x'),
                    dict(snapshot_sha256=PIN_SHA.upper())):
            with patch.object(v5, 'PIN_FILE', self.pin_file(**bad)):
                with self.assertRaisesRegex(ValueError, 'pin file'):
                    v5.load_snapshot.__wrapped__()
        loose = self.pin_file()
        loose.write_text(json.dumps({'snapshot_path': PIN_PATH}))
        with patch.object(v5, 'PIN_FILE', loose):
            with self.assertRaisesRegex(ValueError, 'pin file fields'):
                v5.read_pin_file()

    def test_without_a_pin_file_the_dashboards_snapshot_stands_for_latest(self):
        missing = Path(tempfile.gettempdir()) / 'no-such-snapshot-pin.json'
        with patch.object(v5, 'PIN_FILE', missing):
            self.assertIsNone(v5.read_pin_file())
            self.assertEqual(v5.resolve_pin(), (None, None, 'dashboard'))

    def test_a_receipt_pin_wins_over_the_pin_file(self):
        # build_v5.py --check and check_v5.py pin the snapshot their preview was built from, whatever the file says today.
        with patch.object(v5, '_PIN', OLD_PIN_PATH):
            self.assertEqual(v5.resolve_pin(), (OLD_PIN_PATH, None, 'receipt'))
        with patch.object(v5, '_PIN', PIN_PATH):
            self.assertEqual(v5.resolve_pin(), (PIN_PATH, PIN_SHA, 'receipt'))  # same capture as the file: its digest is still enforced
        with patch.object(v5, '_PIN', OLD_PIN_PATH), patch.object(v5, 'validate_citations'), patch.object(v5, 'validate_descriptions'), patch.dict(v5._META):
            old = v5.load_snapshot.__wrapped__()
            # The pin is not the dashboard's snapshot, so the dashboard is neither compared nor consulted for it.
            self.assertEqual((v5._META['pin_source'], v5._META['snapshot_path'], v5._META['dashboard_matches_snapshot']), ('receipt', OLD_PIN_PATH, False))
            self.assertEqual(v5.attempted(old), 42)
        with self.assertRaisesRegex(ValueError, 'pin must precede first snapshot read'):
            v5.pin_snapshot(OLD_PIN_PATH)  # the live snapshot is already loaded
        self.assertEqual(v5.build_metadata()['snapshot_path'], PIN_PATH)

    def test_the_dashboard_must_agree_only_when_it_is_the_pinned_snapshot(self):
        meta = v5.build_metadata()
        dashboard = json.loads((v5.ROOT / 'data/replication/dashboard_pipeline.json').read_text())
        self.assertEqual(meta['dashboard_matches_snapshot'], dashboard['snapshot_path'] == meta['snapshot_path'])
        if meta['dashboard_matches_snapshot']:
            for key in ('as_of', 'total', 'grouped_stage_counts', 'matched_independent', 'confirmed_independent'):
                self.assertEqual(dashboard[key], self.snapshot[key])

    # ---- V5.1: reviewed descriptions of the confirmed papers ----
    def test_every_confirmed_paper_has_a_reviewed_description_that_matches_the_snapshot_and_the_catalogue(self):
        entries = v5.reviewed_descriptions()
        confirmed = {p['paper_id']: p for p in self.snapshot['papers'] if p['stage_group'] == 'confirmed'}
        self.assertEqual(len(confirmed), CONFIRMED)
        self.assertEqual(set(entries), set(confirmed))
        v5.validate_descriptions(self.snapshot)
        for paper_id, entry in entries.items():
            record = v5._CATALOGUE_RECORDS[paper_id]
            p = confirmed[paper_id]
            self.assertEqual((entry['year'], entry['venue'], entry['doi']), (p['year'], p['venue'], p['doi']))
            self.assertEqual((entry['year'], entry['venue'], entry['doi']), (record['year'], record['venue'], record['doi']))
            self.assertEqual(set(entry['en']), set(entry['zh']))
            for source in entry['sources']:
                self.assertTrue((v5.ROOT / 'data/replication' / source).is_file(), (paper_id, source))

    def test_the_two_earlier_descriptions_are_kept_word_for_word(self):
        entries = v5.reviewed_descriptions()
        shover, jacobsen = entries['shover2019-e698786e'], entries['jacobsen2013-8f3fae1c']
        self.assertEqual([shover['en'][k] for k in ('target', 'outcome', 'sample', 'estimator')],
                         ['Table 1, third column, medical cannabis law', 'Age-adjusted opioid overdose mortality; percent difference from a log-outcome model',
                          'State panel, 1999–2017', 'Two-way fixed effects; state-clustered SE'])
        self.assertEqual([shover['zh'][k] for k in ('target', 'outcome', 'sample', 'estimator')],
                         ['表 1，第 3 列，医用大麻法', '年龄调整后的阿片类药物过量死亡率；对数模型换算的百分比差异', '1999–2017 年州级面板', '双向固定效应；标准误按州聚类'])
        self.assertEqual([jacobsen['en'][k] for k in ('target', 'outcome', 'sample', 'estimator')],
                         ['Table 3, column (1), Code Change; REStat p. 40', 'Electricity use, kWh per residence-month',
                          'Gainesville, Florida, 2004–2006; 64,471 bills from 2,239 residences',
                          'Unweighted OLS; residence-clustered SE; month-year effects and housing controls'])
        self.assertEqual([jacobsen['zh'][k] for k in ('target', 'outcome', 'sample', 'estimator')],
                         ['表 3，第 (1) 列，建筑规范变更；期刊第 40 页', '住宅月用电量，千瓦时／户／月', '佛罗里达州盖恩斯维尔，2004–2006 年；2,239 户住宅的 64,471 条月度账单',
                          '非加权普通最小二乘；标准误按住宅聚类；控制月份—年份效应及住房特征'])

    def test_the_third_card_describes_goodman_table_3_from_the_journal_cell_the_snapshot_verified(self):
        goodman = v5.reviewed_descriptions()['goodman2016-c6f7ffbe']
        p = next(p for p in self.snapshot['papers'] if p['paper_id'] == 'goodman2016-c6f7ffbe')
        run = p['primary']
        self.assertEqual(goodman['frozen_target'], {'table': '6a', 'row': 'mandate_st'})
        self.assertEqual(run['target']['table'], '6a')  # the frozen target is the working-paper cell ...
        self.assertTrue(run['journal_verification']['verified'])  # ... which the journal prints as Table 3, panel B, column (5), p. 679
        self.assertEqual({k: run['journal_verification']['locator'][k] for k in goodman['journal_target']}, goodman['journal_target'])
        self.assertEqual((run['published']['point'], run['published']['se']), (0.142, 0.017))
        self.assertEqual(goodman['en']['target'], 'Table 3, panel B (Selective), column (5), college entrance exam mandate; REStat p. 679')
        for lang, html in self.html.items():
            self.assertEqual(html.count('Review of Economics and Statistics 98(4):671-684 (2016)'), 1)
        self.assertIn('<dt>Sample</dt><dd>ACT states except Michigan, even years 1994–2008; 24 states, 192 state-years; mandate in force from 2002 in Colorado and Illinois</dd>', self.html['en'])
        self.assertIn('<dt>Outcome / units</dt><dd>First-time freshmen enrollment at selective colleges, by state of residence; log points</dd>', self.html['en'])
        self.assertIn('<dt>样本</dt><dd>除密歇根州外的 ACT 州，1994–2008 年的偶数年；24 个州、192 个州年观测；科罗拉多州和伊利诺伊州自 2002 年起实施该要求</dd>', self.html['zh'])

    def test_a_confirmed_paper_without_a_description_or_a_description_that_disagrees_stops_the_build(self):
        entries = deepcopy(v5.reviewed_descriptions())
        def failing(change, pattern):
            changed = deepcopy(entries)
            change(changed)
            with patch.object(v5, 'reviewed_descriptions', return_value=changed):
                with self.assertRaisesRegex(ValueError, pattern):
                    v5.validate_descriptions(self.snapshot)
        with patch.object(v5, 'reviewed_descriptions', return_value=entries):
            v5.validate_descriptions(self.snapshot)
        failing(lambda e: e.pop('goodman2016-c6f7ffbe'), 'named target needs a reviewed public description')
        failing(lambda e: e['goodman2016-c6f7ffbe'].update(year=2017), 'description year differs')
        failing(lambda e: e['goodman2016-c6f7ffbe'].update(venue='The Review of Economics and Statistics 98(4)'), 'description venue differs')
        failing(lambda e: e['jacobsen2013-8f3fae1c'].update(doi='10.1162/rest_a_00244'), 'description doi differs')
        failing(lambda e: e['shover2019-e698786e']['frozen_target'].update(row='Medical marijuana law'), 'frozen target')
        failing(lambda e: e['goodman2016-c6f7ffbe']['journal_target'].update(page=680), 'journal cell the snapshot has not verified')
        failing(lambda e: e['goodman2016-c6f7ffbe']['en'].update(published_interval_note=None), 'calculated from a reported SE')
        failing(lambda e: e['shover2019-e698786e']['zh'].update(published_interval_note='根据报告的标准误计算'), 'calculated from a reported SE')
        failing(lambda e: e['shover2019-e698786e']['en'].pop('sample'), 'fields are incomplete')
        failing(lambda e: e['shover2019-e698786e']['zh'].update(estimator=''), 'fields are incomplete')
        failing(lambda e: e.update({'nobody1999-00000000': deepcopy(e['shover2019-e698786e'])}), 'not confirmed')
        # A description of a paper that is in the snapshot but not confirmed is refused as well.
        outside = next(p for p in self.snapshot['papers'] if p['stage_group'] == 'attempted_matched')
        failing(lambda e: e.update({outside['paper_id']: deepcopy(e['shover2019-e698786e'])}), 'not confirmed')
        # The render guard: no entry, no card.
        with patch.object(v5, 'reviewed_descriptions', return_value={k: v for k, v in entries.items() if k != 'goodman2016-c6f7ffbe'}):
            with self.assertRaisesRegex(ValueError, 'named target needs a reviewed public description'):
                v5.render('en')

    def test_a_description_file_in_the_repository_is_well_formed(self):
        data = json.loads(v5.DESCRIPTIONS_FILE.read_text(encoding='utf-8'))
        self.assertEqual(sorted(data), ['about', 'papers'])
        for paper_id, entry in data['papers'].items():
            self.assertEqual(sorted(k for k in entry if k != 'journal_target'), ['doi', 'en', 'frozen_target', 'sources', 'venue', 'year', 'zh'], paper_id)

    # ---- V5.1: new citations ----
    def test_a_citation_of_a_paper_with_an_unverified_catalogue_citation_needs_its_note(self):
        entries = deepcopy(v5.reviewed_citations())
        flagged = [pid for pid in entries if v5._CATALOGUE_RECORDS[pid].get('citation_verified') is not True]
        self.assertEqual(flagged, ['brummund2020-657584ac'])
        for pid in flagged:
            self.assertTrue(entries[pid].get('note'))
        entries['brummund2020-657584ac'].pop('note')
        with patch.object(v5, 'reviewed_citations', return_value=entries):
            with self.assertRaisesRegex(ValueError, 'not verified needs a note'):
                v5.validate_citations(self.snapshot)

    def test_the_four_papers_named_under_the_v51_pin(self):
        entries = v5.reviewed_citations()
        for pid, text in (('backes2012-301f80a1', 'Backes (2012)'), ('gruber1994-d375ee8c', 'Gruber (1994)'),
                          ('jayaratne1996-9d8d9ce9', 'Jayaratne & Strahan (1996)'), ('rees2019-234de41c', 'Rees et al. (2019)')):
            self.assertEqual(v5.citation_text(entries[pid], 'en'), text)
            self.assertIs(v5._CATALOGUE_RECORDS[pid]['citation_verified'], True)
            self.assertEqual(v5.public_category(next(p for p in self.snapshot['papers'] if p['paper_id'] == pid)), 'number_matches')
        self.assertEqual(len(entries), NAMED)

    def test_release_guard_rejects_source_drift(self):
        with patch.dict(v5._META,{'input_drift':[{'path':'changed'}]}):
            with self.assertRaisesRegex(ValueError,'local historical preview only'):
                v5.assert_release_ready()

if __name__=='__main__':
    unittest.main()
