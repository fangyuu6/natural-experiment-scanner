"""WP1 consumer, translation, presentation and motion behavior contracts."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts.viz_site import build, shared_chrome as chrome
from scripts.zh_pages import build as zh_build

FEATURES = dict(github=True, examples=True, design_opportunity=True, policy_exposure=True)
ROUTES = (*chrome.ORIGINAL_ROUTES, 'screening', 'all_records', 'overview', 'funnel', 'adoption', 'design_opportunity',
          'worked_examples', 'policy_exposure', 'policy_exposure_records', 'synthesize')


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs))


class ChromeTests(unittest.TestCase):
    def test_all_routes_share_renderer_and_preserve_body(self):
        body = '<main id="body"><input value="same" data-code="RD"><script>fetch("/api/policies")</script></main>'
        original = '\n'.join(chrome.marked(s) for s in chrome.SLOTS) + body
        for lang in ('en', 'zh'):
            for route in ROUTES:
                with self.subTest(lang=lang, route=route):
                    slots = chrome.render_slots(lang, route, features=FEATURES)
                    included = chrome.include_slots(original, lang, route, features=FEATURES)
                    core, found = chrome.detach_slots(included)
                    self.assertIn(body, core)
                    self.assertEqual(len(found), 5)
                    for slot in chrome.SLOTS:
                        self.assertEqual(found[slot], chrome.marked(slot, slots[slot]))
                    self.assertEqual(chrome.include_slots(included, lang, route, features=FEATURES), included)
                    nav = Links(slots['nav']).links
                    self.assertEqual(len(nav), 4)
                    self.assertEqual(chrome.NAV_ROUTES, ('screening', 'worked_examples', 'synthesize', 'documentation'))
                    self.assertNotIn(chrome.url('policy_db', lang), [a['href'] for a in nav])
                    self.assertEqual([a['href'] for a in nav], [chrome.url(p, lang) for p in chrome.NAV_ROUTES])
                    active = [a for a in nav if 'aria-current' in a]
                    secondary_parent = {
                        'policy_exposure_records': 'worked_examples',
                        'policy_groups': 'screening', 'all_records': 'screening',
                        'funnel': 'screening', 'adoption': 'screening',
                        'design_opportunity': 'screening',
                    }
                    self.assertEqual(len(active), 1 if route in chrome.NAV_ROUTES or route in secondary_parent else 0)
                    if active:
                        parent = secondary_parent.get(route, route)
                        self.assertEqual(active[0]['href'], chrome.url(parent, lang))
                        self.assertEqual(active[0]['aria-current'], 'true' if route in secondary_parent else 'page')
                    header_links = Links(slots['header']).links
                    self.assertEqual(header_links[0]['href'], chrome.url('policy_db', lang))
                    switch = next(a for a in header_links if a.get('class') == 'lang-switch')
                    self.assertEqual(switch['href'], chrome.url(route, 'zh' if lang == 'en' else 'en'))
                    for a in header_links + Links(slots['footer']).links:
                        if a['href'].startswith(('https:', 'http:')):
                            self.assertEqual(a.get('rel'), 'noopener')
                        else:
                            self.assertNotIn('target', a)
                    self.assertNotRegex(included, r'<html[^>]*class="nx-motion')
                    self.assertNotIn('{{', included)

    def test_viz_partial_and_original_slots_are_identical(self):
        for lang in ('en', 'zh'):
            for page in ROUTES:
                ctx = dict(lang=lang, page=page, strings={})
                errors = build.Errors()
                with patch.object(build, 'chrome_features', return_value=FEATURES):
                    actual = build.render('{{partial:head_css}}{{partial:header}}{{partial:footer}}', ctx, errors, 'consumer')
                self.assertEqual(errors.errors, [])
                _, slots = chrome.detach_slots(actual)
                expected = chrome.render_slots(lang, page, features=FEATURES)
                self.assertEqual(slots, {s: chrome.marked(s, expected[s]) for s in chrome.SLOTS})

    def test_missing_or_duplicate_slots_fail_without_mutation(self):
        for text in (chrome.marked('header'), chrome.marked('header') * 2,
                     '<!-- NX:HEADER:START -->broken'):
            with self.assertRaises(ValueError):
                chrome.include_slots(text, 'en', 'policy_db', features=FEATURES)
        self.assertEqual(chrome.include_slots('legacy body', 'en', 'policy_db'), 'legacy body')

    def test_translation_excludes_generated_chrome_but_checks_body_pairs(self):
        old = '\n'.join(chrome.marked(s, '<a>Documentation</a>' if s == 'header' else '') for s in chrome.SLOTS)
        en = old + '<p id="content" data-id="same">Body</p><script>fetch("/api/policies")</script>'
        core, slots = chrome.detach_slots(en)
        pairs = [dict(en=x, zh='译文', ref=str(i), all=False, optional=False)
                 for i, x in enumerate(('>Documentation</a>', 'Body', 'Missing body'))]
        selected = chrome.body_pairs(pairs, core, slots)
        self.assertEqual([p['en'] for p in selected], ['Body', 'Missing body'])
        errors = []
        translated, _ = zh_build.apply_pairs(core, selected, 'body', errors)
        self.assertEqual(len(errors), 1)
        self.assertIn('Missing body', errors[0])
        self.assertEqual(zh_build.integrity(core, translated), [])
        result = chrome.restore_slots(translated, slots, 'zh', 'policy_db', features=FEATURES)
        self.assertIn('方法', result)
        self.assertIn('fetch("/api/policies")', result)
        changed = translated.replace('data-id="same"', 'data-id="different"')
        self.assertTrue(zh_build.integrity(core, changed))

    def test_hidden_optional_routes_do_not_appear(self):
        slots = chrome.render_slots('en', 'overview', features=dict(github=False))
        self.assertEqual([a['href'] for a in Links(slots['nav']).links],
                         [chrome.url(p, 'en') for p in chrome.NAV_ROUTES])
        self.assertNotIn('github.com', slots['header'] + slots['footer'])

    def test_runtime_moves_to_head_once_without_changing_body(self):
        page = '<html><head>' + chrome.marked('style') + '</head><body>' + ''.join(
            chrome.marked(s) for s in chrome.SLOTS if s != 'style') + '<main>same body</main></body></html>'
        result = chrome.include_slots(page, 'zh', 'policy_groups', features=FEATURES)
        self.assertLess(result.index('<!-- NX:MOTION:START -->'), result.index('</head>'))
        self.assertEqual(result.count('<!-- NX:MOTION:START -->'), 1)
        self.assertEqual(chrome.include_slots(result, 'zh', 'policy_groups', features=FEATURES), result)
        self.assertIn('<main>same body</main>', result)

    def test_original_preview_keeps_both_languages_out_of_deploy(self):
        source = '<html lang="en"><head>' + chrome.marked('style') + '</head><body>' + ''.join(
            chrome.marked(s) for s in chrome.SLOTS if s != 'style') + '<main id="same"></main></body></html>'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            production = base / 'production'
            production.mkdir()
            sentinel = production / 'documentation.html'
            sentinel.write_text('unchanged')
            with patch.object(zh_build, 'DEPLOY', production), patch.object(zh_build, 'load_source', return_value=(source, 0)), \
                    patch.object(zh_build, 'load_keep', return_value=set()), patch.object(zh_build, 'load_pairs', return_value=[]), \
                    patch.object(zh_build, 'scan_strings', return_value=[]), \
                    patch.object(zh_build, 'set_aside_story', side_effect=lambda page, text: (text, False)):
                self.assertEqual(zh_build.main(['--out', str(base / 'preview/zh')]), 0)
            self.assertEqual(sentinel.read_text(), 'unchanged')
            for page in chrome.ORIGINAL_ROUTES:
                self.assertTrue((base / 'preview' / (page + '.html')).is_file())
                self.assertTrue((base / 'preview/zh' / (page + '.html')).is_file())


class PresentationTests(unittest.TestCase):
    def test_real_selection_fallback_preserves_all_rendered_results(self):
        selection = build.worked_example_selection()
        original = deepcopy(selection)
        errors = build.Errors()
        strings = dict(build.load_dict('common', 'en', errors))
        strings.update(build.load_dict('worked_examples', 'en', errors))
        ctx = dict(lang='en', page='worked_examples', strings=strings)
        template = (build.PARTIALS_DIR / 'worked_examples_body.html').read_text()
        base = build._render_case_page(selection, ctx, errors, template)
        with patch('importlib.import_module', side_effect=ModuleNotFoundError(name='scripts.viz_site.worked_examples_polish')):
            rendered = build.render_case_page(selection, ctx, errors, template)
        self.assertEqual(rendered, base)
        self.assertEqual(selection, original)
        self.assertEqual(errors.errors, [])

    def test_absent_extension_keeps_legacy_renderer(self):
        with patch('importlib.import_module', side_effect=ModuleNotFoundError(name='scripts.viz_site.worked_examples_polish')), patch.object(build, '_render_case_page', return_value='legacy') as base:
            self.assertEqual(build.render_case_page({'cases': []}, {'lang': 'en'}, build.Errors(), 'template'), 'legacy')
            base.assert_called_once()

    def test_extension_receives_services_and_isolated_inputs(self):
        selection, context = {'cases': [{'estimate': 1.25, 'interval': [0.1, 2.4]}]}, {'lang': 'zh', 'strings': {'x': 'y'}}
        before = deepcopy((selection, context))
        def extension(selected, ctx, errors, template, *, services):
            selected['cases'][0]['estimate'] = 999
            ctx['strings']['x'] = 'changed'
            self.assertIs(services['lean_event_svg'], build.lean_event_svg)
            self.assertIs(services['case_page_typography'], build.case_page_typography)
            return services['render_case_page'](selected, ctx, errors, template)
        module = types.SimpleNamespace(render_case_page=extension)
        with patch('importlib.import_module', return_value=module), patch.object(build, '_render_case_page', return_value='extended'):
            self.assertEqual(build.render_case_page(selection, context, build.Errors(), 'template'), 'extended')
        self.assertEqual((selection, context), before)

    def test_extension_dependency_failure_is_not_silently_skipped(self):
        with patch('importlib.import_module', side_effect=ModuleNotFoundError(name='broken_dependency')):
            with self.assertRaises(ModuleNotFoundError):
                build.render_case_page({}, {}, build.Errors(), '')


NODE_HARNESS = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = JSON.parse(require('node:fs').readFileSync(0,'utf8'));
function classes(){const s=new Set();return {add(...xs){xs.forEach(x=>s.add(x))},remove(...xs){xs.forEach(x=>s.delete(x))},toggle(x,on){on?s.add(x):s.delete(x)},contains(x){return s.has(x)}}}
function setup(options={}){
 const timers=new Map(),frames=new Map(),observers=[],events={},queries={};let serial=0,clock=0;
 const css={};const root={classList:classes(),style:{setProperty(k,v){css[k]=v}},clientHeight:800,clientWidth:1000};
 const document={documentElement:root,readyState:options.loading?'loading':'complete',querySelector(){return options.toolbar},querySelectorAll(){return options.nav?[options.nav]:[]},addEventListener(name,fn){const old=events['document:'+name];events['document:'+name]=()=>{if(old)old();fn()}}};
 const window={innerHeight:800,innerWidth:1000,performance:{now:()=>0},getComputedStyle(){return {position:'sticky',top:'12px',getPropertyValue(k){return k==='--nx-ease-feedback'?'ease-out':'cubic-bezier(.2,.7,.2,1)'}}},
  matchMedia(q){return queries[q]||(queries[q]={matches:q==='print'?false:!options.reduce,addEventListener(_,f){this.change=f}})},
  addEventListener(name,fn){events[name]=fn},setTimeout(fn,ms){if(options.timerFail)throw Error('timer');const id=++serial;timers.set(id,{fn,ms});return id},clearTimeout(id){timers.delete(id)},
  requestAnimationFrame(fn){const id=++serial;frames.set(id,fn);return id},cancelAnimationFrame(id){frames.delete(id)},
  IntersectionObserver:class{constructor(fn){if(options.observerFail)throw Error('observer');this.fn=fn;observers.push(this)}observe(el){this.el=el}disconnect(){this.disconnected=true}}
 };
 if(options.unsupported)delete window.IntersectionObserver;
 const sandbox={window,document,Set,WeakMap,Number,Array,Math,Date:{now:()=>clock},performance:window.performance};
 vm.runInNewContext(source,sandbox);
 const element=()=>({classList:classes(),isConnected:true,events:{},addEventListener(name,fn){this.events[name]=fn},removeEventListener(name,fn){if(this.events[name]===fn)delete this.events[name]},getBoundingClientRect(){return this.rect||{top:20,bottom:100,left:0,right:100}}});
 function flushFrame(){const [id,fn]=[...frames][0]||[];if(fn){frames.delete(id);fn(clock)}}
 function enter(el,ratio=1,batch=false){const o=observers.find(o=>o.el===el);assert.ok(o);o.fn([{target:el,isIntersecting:ratio>0,intersectionRatio:ratio}]);if(!batch)flushFrame()}
 return {api:window.NXMotion,root,css,window,timers,frames,observers,queries,events,element,enter,flushFrame,advance(ms){clock+=ms},fire(ms){const [id,t]=[...timers].find(([id,t])=>t.ms===ms);timers.delete(id);t.fn()}};
}
// Unsupported, reduce and observer/timer failure preserve the stored final value.
for(const mode of [{unsupported:true},{reduce:true},{observerFail:true},{timerFail:true}]){
 const h=setup(mode),el=h.element();let runs=0,final=42;
 const handle=h.api.once(el,c=>{runs++;final=0},{finish(){final=42}});
 if(mode.timerFail)h.enter(el);
 assert.equal(final,42);assert.equal(runs,0);assert.equal(handle.state,'finished');assert.ok(!el.classList.contains('nx-motion-armed'));
 if(!mode.timerFail)assert.ok(!h.root.classList.contains('nx-motion'));
}
// Prepaint opt-in is temporary, and a broken startup cannot conceal final HTML.
for(const mode of [{loading:true},{loading:true,reduce:true},{loading:true,timerFail:true}]){
 const h=setup(mode);
 if(!mode.reduce&&!mode.timerFail){assert.ok(h.root.classList.contains('nx-motion-boot'));h.events['document:DOMContentLoaded']()}
 assert.ok(!h.root.classList.contains('nx-motion-boot'));
}
{
 const h=setup(),el=h.element();let runs=0,final=42;
 const handle=h.api.once(el,c=>{runs++;assert.ok(el.classList.contains('nx-motion-armed'));final=0;c.timer(()=>c.finish(),600)},{finish(){final=42},maxDuration:900});
 assert.equal(final,42);assert.equal(runs,0);assert.ok(!el.classList.contains('nx-motion-armed'));
 assert.ok(el.classList.contains('nx-motion-pending'));
 h.enter(el);assert.equal(runs,1);assert.equal(final,0);
 assert.ok(!el.classList.contains('nx-motion-pending'));
 h.fire(600);assert.equal(final,42);assert.equal(h.timers.size,0);
 h.enter(el);h.api.once(el,()=>runs++,{finish(){}});assert.equal(runs,1);assert.equal(handle.state,'finished');
}
// Reversed observer delivery still starts the top illustration first.
{
 const h=setup(),top=h.element(),bottom=h.element(),last=h.element(),order=[];
 top.compareDocumentPosition=e=>4;bottom.compareDocumentPosition=e=>e===top?2:4;last.compareDocumentPosition=e=>2;
 h.api.once(bottom,c=>{order.push('bottom')},{finish(){}});
 const first=h.api.once(top,c=>{order.push('top')},{finish(){}});
 h.api.once(last,c=>{order.push('last')},{finish(){}});
 h.enter(last,1,true);h.enter(bottom,1,true);h.enter(top,1,true);assert.deepEqual(order,[]);
 h.flushFrame();assert.deepEqual(order,['top']);first.finish();assert.deepEqual(order,['top','bottom']);h.api.finishAll();
}
// Prepared geometry is synchronous, gated by preference and restored on failure.
for(const mode of [{},{reduce:true},{unsupported:true}]){
 const h=setup(mode),el=h.element();let value=42,runs=0;
 h.api.once(el,c=>runs++,{prepare(){value=0},finish(){value=42}});
 assert.equal(value,mode.reduce||mode.unsupported?42:0);assert.equal(runs,0);
 h.api.finishAll();assert.equal(value,42);assert.ok(!el.classList.contains('nx-motion-prepared'));
}
{
 const h=setup(),el=h.element();let value=42;
 const handle=h.api.once(el,()=>{throw Error('must not start')},{prepare(){value=0;throw Error('prepare')},finish(){value=42}});
 assert.equal(value,42);assert.equal(handle.state,'finished');assert.ok(el.classList.contains('nx-motion-complete'));
}
// No media event: the next tracked frame, timer, or watchdog must finish all counters.
for(const trigger of ['frame','timer','watchdog']){
 const h=setup(),a=h.element(),b=h.element();let aFinal=4,bFinal=0;
 h.api.once(a,c=>{c.raf(()=>{aFinal=5});c.timer(()=>{aFinal=6},100)}, {finish(){aFinal=51},maxDuration:777});h.enter(a);
 h.api.once(b,()=>{bFinal=1}, {finish(){bFinal=2212}});h.enter(b);
 h.queries['(prefers-reduced-motion: no-preference)'].matches=false;
 if(trigger==='frame'){const [id,fn]=[...h.frames][0];h.frames.delete(id);fn(16)}
 else h.fire(trigger==='timer'?100:777);
 assert.equal(aFinal,51);assert.equal(bFinal,2212);assert.equal(h.timers.size,0);assert.equal(h.frames.size,0);
 assert.ok(!h.root.classList.contains('nx-motion'));assert.ok(!a.classList.contains('nx-motion-armed'));assert.ok(!b.classList.contains('nx-motion-pending'));
 h.queries['(prefers-reduced-motion: no-preference)'].matches=true;h.queries['(prefers-reduced-motion: no-preference)'].change();
 h.api.once(a,()=>{aFinal=0},{finish(){}});assert.equal(aFinal,51);
}
{
 const h=setup(),a=h.element(),b=h.element();let ctx,bRuns=0,settings;
 h.api.once(a,c=>{ctx=c;c.animate({animate(_,s){settings=s;return {cancel(){}}}},[],{duration:180})},{finish(){}});h.enter(a);
 h.api.once(b,()=>bRuns++,{finish(){}});h.enter(b);assert.equal(bRuns,0);
 assert.equal(settings.easing,'cubic-bezier(.2,.7,.2,1)');ctx.releaseIntro();assert.equal(bRuns,1);
 assert.ok(ctx.active());h.api.finishAll();
}
{
 const h=setup(),el=h.element();let value=42;
 h.api.once(el,c=>{c.raf(()=>{value=0})},{finish(){value=42}});h.enter(el);
 assert.ok(!el.classList.contains('nx-motion-frame'));
 const [id,fn]=[...h.frames][0];h.frames.delete(id);fn(16);assert.equal(value,0);assert.ok(el.classList.contains('nx-motion-frame'));
 h.api.finishAll();assert.equal(value,42);assert.ok(!el.classList.contains('nx-motion-frame'));
}
{
 const h=setup(),a=h.element(),b=h.element();let bRuns=0,bFinal=0;
 const first=h.api.once(a,()=>{},{finish(){}});h.enter(a);
 h.api.once(b,()=>bRuns++,{finish(){bFinal=42}});h.enter(b);assert.equal(bRuns,0);
 b.rect={top:900,bottom:1000,left:0,right:100};h.enter(b,0);first.finish();assert.equal(bFinal,42);assert.equal(bRuns,0);
}
{
 const h=setup(),a=h.element(),b=h.element();let aFinal=0,bRuns=0;
 h.api.once(a,()=>{}, {finish(){aFinal=42}});h.enter(a);a.rect={top:-200,bottom:-100,left:0,right:100};
 h.api.once(b,()=>bRuns++, {finish(){}});h.enter(b);assert.equal(aFinal,42);assert.equal(bRuns,1);
}
{
 const h=setup(),el=h.element();let final=0,cancels=0,smilEnds=0;
 h.api.once(el,c=>{c.timer(()=>{},200);c.raf(()=>{});c.animate({animate(){return {cancel(){cancels++}}}},[],{});c.smil({beginElement(){},endElement(){smilEnds++}})},{finish(){final=42}});h.enter(el);
 h.queries['(prefers-reduced-motion: no-preference)'].matches=false;h.queries['(prefers-reduced-motion: no-preference)'].change();
 assert.equal(final,42);assert.equal(cancels,1);assert.equal(smilEnds,1);assert.equal(h.timers.size,0);assert.equal(h.frames.size,0);assert.ok(!h.root.classList.contains('nx-motion'));assert.ok(!el.classList.contains('nx-motion-armed'));
 h.queries['(prefers-reduced-motion: no-preference)'].matches=true;h.queries['(prefers-reduced-motion: no-preference)'].change();let replay=0;h.api.once(el,()=>replay++,{finish(){}});assert.equal(replay,0);
}
{
 const h=setup(),el=h.element();let value=0;
 h.api.transition(el,()=>value=1,{finish(){value=10}});
 h.api.transition(el,c=>{assert.equal(value,10);value=2;c.timer(()=>{},200)},{finish(){value=20}});
 assert.equal(value,2);h.events.beforeprint();assert.equal(value,20);assert.equal(h.timers.size,0);assert.ok(!h.root.classList.contains('nx-motion'));h.events.afterprint();
 h.api.transition(el,()=>{throw Error('consumer')},{finish(){value=30}});assert.equal(value,30);assert.ok(!el.classList.contains('nx-motion-armed'));
 const missing=h.api.transition(el,()=>value=0,{});assert.equal(missing.state,'finished');assert.equal(value,30);
}
{
 const h=setup(),el=h.element();let final=0;
 h.api.once(el,()=>{},{finish(){final=42},maxDuration:777});h.enter(el);
 h.fire(777);assert.equal(final,42);assert.equal(h.timers.size,0);
}
{
 const h=setup(),el=h.element();let ctx,final=0,timerKey,frameKey;
 h.api.once(el,c=>{ctx=c;timerKey=c.timer(()=>c.finish(),100);frameKey=c.raf(()=>{})},{finish(){final=42},maxDuration:500});h.enter(el);
 h.advance(40);ctx.pause();assert.ok(ctx.paused());assert.equal(h.timers.size,1);assert.equal([...h.timers.values()][0].ms,120);assert.equal(h.frames.size,0);
 h.advance(1000);ctx.resume();assert.ok(!ctx.paused());assert.equal(h.frames.size,1);assert.ok([...h.timers.values()].some(t=>t.ms===60));
 ctx.cancelRaf(frameKey);assert.equal(h.frames.size,0);h.fire(60);assert.equal(final,42);assert.equal(h.timers.size,0);
}
{
 const h=setup(),el=h.element();let ctx,value=4;
 h.api.once(el,c=>{ctx=c;c.raf(()=>{});c.timer(()=>{},600)},{finish(){value=51},maxDuration:900});h.enter(el);ctx.pause();
 h.queries['(prefers-reduced-motion: no-preference)'].matches=false;h.fire(120);
 assert.equal(value,51);assert.equal(h.frames.size,0);assert.equal(h.timers.size,0);assert.ok(!h.root.classList.contains('nx-motion'));
}
{
 const nav={scrollLeft:0,getBoundingClientRect(){return {left:0,right:300}},querySelector(){return {getBoundingClientRect(){return {left:800-nav.scrollLeft,right:900-nav.scrollLeft}}}}};
 const h=setup({nav});assert.equal(nav.scrollLeft,600);h.events.resize();assert.equal(nav.scrollLeft,600);
}
{
 const h=setup(),el=h.element();h.api.landing(el);assert.ok(el.classList.contains('nx-landing'));h.fire(1200);assert.ok(!el.classList.contains('nx-landing'));assert.equal(h.timers.size,0);
 h.api.landing(el);h.events.beforeprint();assert.ok(!el.classList.contains('nx-landing'));
 const toolbar={getBoundingClientRect(){return {top:12,bottom:76,height:64}}};const layout=setup({toolbar});assert.equal(layout.css['--nx-anchor-offset'],'92px');
}
for(const tag of ['SECTION','TABLE']){
 const h=setup(),parent=h.element(),heading=h.element();parent.tagName=tag;parent.querySelector=selector=>{assert.equal(selector,tag==='TABLE'?'caption':'h1,h2,h3,h4,h5,h6');return heading};
 h.api.landing(parent);assert.ok(heading.classList.contains('nx-landing'));assert.ok(!parent.classList.contains('nx-landing'));h.api.finishAll();assert.ok(!heading.classList.contains('nx-landing'));
}
// Reusable delayed reveals prepare synchronously, register all WAAPI work at start,
// and restore original CSS declarations on every supported/failure exit.
function revealTargets(count=15,options={}){
 const animations=[],targets=[];let cancellations=0;
 for(let index=0;index<count;index++){
  const values=new Map([['opacity',{value:index===0?'.7':'',priority:index===0?'important':''}],['transform',{value:index===0?'rotate(1deg)':'',priority:''}]]);
  const style={getPropertyValue:p=>values.get(p)?.value||'',getPropertyPriority:p=>values.get(p)?.priority||'',setProperty(p,value,priority=''){values.set(p,{value:String(value),priority})},removeProperty(p){values.delete(p)}};
  const target={style};
  if(!options.missing)target.animate=(frames,settings)=>{animations.push({target,frames,settings});if(options.fail)throw Error('WAAPI failure');return {cancel(){cancellations++}}};
  targets.push(target);
 }
 return {targets,animations,cancellations:()=>cancellations,checkOriginal(){
  targets.forEach((target,i)=>{assert.equal(target.style.getPropertyValue('opacity'),i===0?'.7':'');assert.equal(target.style.getPropertyPriority('opacity'),i===0?'important':'');assert.equal(target.style.getPropertyValue('transform'),i===0?'rotate(1deg)':'')});
 }};
}
for(const reason of ['complete','watchdog','focus','reduce','print','explicit']){
 const h=setup(),el=h.element(),r=revealTargets();
 const handle=h.api.revealOnce(el,r.targets);
 assert.equal(handle.state,'pending');assert.equal(r.animations.length,0);
 assert(r.targets.every(target=>target.style.getPropertyValue('opacity')==='0'));
 h.enter(el);
 assert.equal(r.animations.length,15,'Every delayed animation is created immediately.');
 r.animations.forEach((animation,i)=>{assert.equal(animation.settings.duration,420);assert.equal(animation.settings.delay,Math.min(i*65,700));assert.equal(animation.settings.fill,'both');assert.deepEqual(Array.from(animation.frames,frame=>frame.opacity),[0,1]);assert.deepEqual(Array.from(animation.frames,frame=>frame.transform),['translateY(6px)','translateY(0)'])});
 assert.deepEqual([...h.timers.values()].map(t=>t.ms).sort((a,b)=>a-b),[120,1200,1500],'Timers only monitor preference, finish and watchdog.');
 if(reason==='complete')h.fire(1200);
 else if(reason==='watchdog')h.fire(1500);
 else if(reason==='focus')el.events.focusin();
 else if(reason==='reduce'){h.queries['(prefers-reduced-motion: no-preference)'].matches=false;h.queries['(prefers-reduced-motion: no-preference)'].change()}
 else if(reason==='print')h.events.beforeprint();
 else handle.finish();
 r.checkOriginal();assert.equal(handle.state,'finished');assert.equal(r.cancellations(),15);
 assert.equal(h.timers.size,0);assert.equal(h.frames.size,0);assert(!el.events.focusin);
 assert.equal(h.api.revealOnce(el,r.targets),handle);assert.equal(r.animations.length,15);r.checkOriginal();
}
for(const options of [{reduce:true},{unsupported:true},{observerFail:true},{timerFail:true}]){
 const h=setup(options),el=h.element(),r=revealTargets(2);
 const handle=h.api.revealOnce(el,r.targets);
 if(options.timerFail&&handle.state!=='finished')h.enter(el);
 r.checkOriginal();assert.equal(handle.state,'finished');assert.equal(r.animations.length,0);
}
for(const options of [{missing:true},{fail:true}]){
 const h=setup(),el=h.element(),r=revealTargets(2,options);
 const handle=h.api.revealOnce(el,r.targets);
 if(!options.missing)h.enter(el);
 r.checkOriginal();assert.equal(handle.state,'finished');assert.equal(h.timers.size,0);assert(!el.events.focusin);
}

console.log('motion behavior OK');
'''


class MotionTests(unittest.TestCase):
    def test_callbacks_with_mocked_runtime_failures(self):
        self.assertIsNotNone(shutil.which('node'), 'Node is required for motion callback verification')
        html = (Path(build.PARTIALS_DIR) / 'motion.html').read_text()
        script = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S))
        result = subprocess.run(['node', '-e', NODE_HARNESS], input=json.dumps(script),
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('motion behavior OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
