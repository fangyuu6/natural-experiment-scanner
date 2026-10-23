"""V5 three-door homepage and standalone map/flow presentation contracts."""
from html import unescape
from pathlib import Path
import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from scripts.viz_site import build, make_story_map as mapgen, make_story_flow as flowgen
from scripts.viz_site import restructure_estimates as estimates

HERE = Path(__file__).resolve().parents[1]
BASELINE = HERE.parents[1] / 'deploy_output/_release/polish_baseline_20261004/policy_db.html'


HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
class El {
 constructor(attrs={},text=''){this.attrs={...attrs};this.dataset={};this.textContent=text;this.events={};this.style={setProperty(k,v){this[k]=v},removeProperty(k){delete this[k]}};this.clientWidth=600;this.scrollWidth=600;this.scrollLeft=0;this.isConnected=true;
  const self=this,set=new Set((attrs.class||'').split(' ').filter(Boolean));
  this.classList={add(...xs){xs.forEach(x=>set.add(x));self.attrs.class=[...set].join(' ')},remove(...xs){xs.forEach(x=>set.delete(x));self.attrs.class=[...set].join(' ')},contains(x){return set.has(x)},toggle(x,on){if(on===undefined)on=!set.has(x);on?this.add(x):this.remove(x);return on}};
  Object.entries(attrs).forEach(([k,v])=>{if(k.startsWith('data-'))this.dataset[k.slice(5)]=String(v)});
 }
 setAttribute(k,v){if(k==='class'){this.classList.remove(...(this.attrs.class||'').split(' '));this.classList.add(...String(v).split(' '));return}this.attrs[k]=String(v);if(k.startsWith('data-'))this.dataset[k.slice(5)]=String(v)}
 getAttribute(k){return Object.hasOwn(this.attrs,k)?this.attrs[k]:null}
 removeAttribute(k){delete this.attrs[k];if(k.startsWith('data-'))delete this.dataset[k.slice(5)]}
 hasAttribute(k){return Object.hasOwn(this.attrs,k)}
 get tabIndex(){return Number(this.getAttribute('tabindex')??-1)}set tabIndex(v){this.setAttribute('tabindex',v)}
 addEventListener(k,f){(this.events[k]??=[]).push(f)}removeEventListener(k,f){this.events[k]=(this.events[k]||[]).filter(x=>x!==f)}
 emit(k,target=this){(this.events[k]||[]).forEach(f=>f({type:k,target,pointerType:'mouse',key:'',preventDefault(){}}))}
 closest(){return this}matches(){return true}focus(){document.activeElement=this}
 getBoundingClientRect(){return {top:20,bottom:440,left:0,right:600,width:600,height:420}}
 querySelector(k){return (this.one||{})[k]||null}querySelectorAll(k){return (this.many||{})[k]||[]}
 getScreenCTM(){return {a:1}}contains(e){return (this.children||[]).includes(e)}
 animate(frames,options){const animation={frames,options,cancel(){this.canceled=true},pause(){},play(){}};this.animations??=[];this.animations.push(animation);return animation}
}
function setup(mode={}){
 let time=0,serial=0;const timers=new Map(),observers=[],queries={},events={};
 const doc=new El();doc.documentElement=new El();doc.readyState='complete';doc.hidden=false;doc.querySelector=()=>null;doc.querySelectorAll=()=>[];
 const win={innerWidth:mode.mobile?390:1440,innerHeight:900,addEventListener(k,f){(events[k]??=[]).push(f)},getComputedStyle(){return {display:'grid',gridTemplateColumns:'1 1 1 1',paddingLeft:'0',getPropertyValue(k){return k==='--nx-ease-feedback'?'ease-out':'cubic-bezier(.2,.7,.2,1)'}}},
  removeEventListener(k,f){events[k]=(events[k]||[]).filter(x=>x!==f)},
  matchMedia(k){return queries[k]??=( {matches:k==='print'?false:!mode.reduce,addEventListener(_,f){this.changed=f}} )},
  setTimeout(f,ms){const id=++serial;timers.set(id,{f,due:time+ms});return id},clearTimeout(id){timers.delete(id)},
  requestAnimationFrame(f){const id=++serial;timers.set(id,{f:()=>f(time),due:time+16});return id},cancelAnimationFrame(id){timers.delete(id)},
  IntersectionObserver:class {constructor(f){this.f=f;observers.push(this)}observe(e){this.el=e}disconnect(){this.gone=true}}
 };
 if(mode.unsupported)delete win.IntersectionObserver;
 const sandbox={window:win,document:doc,Set,WeakMap,Number,Array,Math,JSON,getComputedStyle:win.getComputedStyle,Date:{now:()=>time},performance:{now:()=>time},IntersectionObserver:win.IntersectionObserver};
 vm.createContext(sandbox);if(!mode.noUtility)vm.runInContext(input.motion,sandbox);
 function enter(e){const obs=observers.find(o=>o.el===e&&!o.gone);if(obs){obs.f([{target:e,isIntersecting:true,intersectionRatio:1}]);advance(16)}}
 function advance(ms){const end=time+ms;let n=0;while(true){const next=[...timers].filter(([id,t])=>t.due<=end).sort((a,b)=>a[1].due-b[1].due||a[0]-b[0])[0];if(!next)break;assert.ok(n++<10000);time=next[1].due;timers.delete(next[0]);next[1].f()}time=end}
 return {sandbox,doc,win,queries,events,timers,observers,enter,advance,now:()=>time,run(s){vm.runInContext(s,sandbox)}};
}
function mapSetup(mode={}){
 const h=setup(mode),D=input.data,defaultId=input.default,L=input.labels,p=D.p[defaultId],root=new El(),area=new El(),control=new El(),icon=new El(),svg=new El(),list=new El(),pst=new El(),pn=new El(),ro=new El(),r1=new El(),r2=new El(),scope=new El(),lgd=new El(),lgt=new El(),hl=new El();
 const states=D.c.map((c,i)=>new El({'data-c':c,'data-y':p.y[i],d:'geometry-'+c,class:'sm-s s-'+(p.y[i]?bin(p,p.y[i]):'n'),...(p.y[i]?{tabindex:'0','aria-label':L['s.'+c]+L.al_sep+p.y[i]}:{})}));
 const dots=Object.entries(D.p).flatMap(([id,p])=>Object.keys(p.e).map(c=>new El({'data-f':id,'data-c':c,class:'sm-dot'+(id===defaultId?' on':'')})));
 const tabs=Object.keys(D.p).map(id=>new El({'data-f':id,class:'nx-tab','aria-checked':String(id===defaultId),tabindex:id===defaultId?0:-1},L['p.'+id+'.short']));
 const ends=Array.from({length:5},()=>new El());
 const strings=Object.entries(L).map(([k,v])=>new El({'data-k':k},v));
 root.one={'.sm-svg':svg,'#sm-hl':hl,'#sm-ro':ro,'#sm-ro1':r1,'#sm-ro2':r2,'#sm-tabs':list,'#sm-pn':pn,'#sm-pst':pst,'#sm-scope':scope,'#sm-map':area,'#sm-control':control,'#sm-control-icon':icon,'#sm-lgdot':lgd,'#sm-lgt':lgt,'#sm-d':new El({},JSON.stringify(D))};
 root.many={'#sm-t i':strings,'#sm-f path':states,'.nx-tab':tabs,'.sm-dot':dots,'.sm-rk i':ends};svg.children=states;
 pst.textContent=L.stats.replace('{n}',p.y.filter(Boolean).length).replace('{y0}',Math.min(...p.y.filter(Boolean))).replace('{y1}',Math.max(...p.y));pn.textContent=L['p.'+defaultId+'.name'];
 h.doc.getElementById=id=>id==='sm-root'?root:null;h.run(input.map);
 function choose(id){list.emit('click',tabs.find(t=>t.dataset.f===id))}
 function bin(p,y){return p.b.findIndex(b=>y<=b[1])+1}
 function snapshot(){return JSON.stringify({states:states.map(s=>({...s.attrs})),dots:dots.map(d=>({...d.attrs})),stat:pst.textContent,ends:ends.map(e=>e.textContent),name:pn.textContent})}
 function final(id){const p=D.p[id];states.forEach((s,i)=>{assert.equal(s.classList.contains('sm-wait'),false);assert.equal(s.getAttribute('class'),'sm-s s-'+(p.y[i]?bin(p,p.y[i]):'n'))});dots.forEach(d=>{assert.equal(d.classList.contains('sm-wait'),false);assert.equal(d.classList.contains('on'),d.dataset.f===id)});const years=p.y.filter(Boolean);assert.equal(pst.textContent,L.stats.replace('{n}',years.length).replace('{y0}',Math.min(...years)).replace('{y1}',Math.max(...years)));assert.equal(area.classList.contains('nx-motion-armed'),false)}
 return {...h,root,area,control,svg,list,states,dots,pst,tabs,choose,snapshot,final};
}
if(input.task==='map'){
 // All eight clocks include empty calendar years; each cohort uses final bins.
 for(const id of Object.keys(input.data.p)){
  const h=mapSetup(),p=input.data.p[id];h.choose(id);const baseline=h.snapshot();h.control.emit('click');
  const years=p.y.filter(Boolean),lo=Math.min(...years),hi=Math.max(...years),step=Math.max(190,Math.min(380,4000/(hi-lo+1)));
  assert.equal(h.control.dataset.action,'pause');assert.ok(h.states.every(s=>s.classList.contains('sm-wait')));
  h.advance(399);assert.ok(h.states.every(s=>s.classList.contains('sm-wait')));h.advance(1);
  for(let year=lo;year<=hi;year++){
   if(year>lo)h.advance(400+(year-lo)*step+.001-h.now());
   h.states.forEach((s,i)=>assert.equal(!s.classList.contains('sm-wait'),p.y[i]>0&&p.y[i]<=year));
   assert.ok(h.pst.textContent.endsWith(String(year)));if(p.y.filter(y=>y>0&&y<=year).length===1)assert.ok(h.pst.textContent.includes('1 jurisdiction ·'));h.dots.forEach(d=>assert.equal(!d.classList.contains('sm-wait'),d.dataset.f===id&&p.y[input.data.c.indexOf(d.dataset.c)]<=year&&p.y[input.data.c.indexOf(d.dataset.c)]>0));
  }
  h.advance(step+700);h.final(id);assert.equal(h.snapshot(),baseline);assert.equal(h.control.dataset.action,'replay');
  h.advance(20000);assert.equal(h.snapshot(),baseline);assert.equal(h.timers.size,0);
 }
 // Preparation precedes visibility; scroll-in cannot show final coloured fills/stat.
 {
  const h=mapSetup();assert.ok(h.area.classList.contains('nx-motion-pending'));assert.ok(h.area.classList.contains('nx-motion-prepared'));
  assert.ok(h.states.every(s=>s.classList.contains('sm-wait')));assert.ok(h.pst.textContent.startsWith('0 jurisdictions'));
  h.win.NXMotion.finishAll('startup');h.final(input.default);assert.ok(!h.area.classList.contains('nx-motion-prepared'));
 }
 // Initial sequence is once; pause resumes the remaining active-time clock.
 {
  const h=mapSetup();h.enter(h.area);h.advance(1099);assert.ok(h.states.every(s=>s.classList.contains('sm-wait')));h.advance(401);h.control.emit('click');const paused=h.snapshot();assert.equal(h.control.dataset.action,'resume');h.advance(20000);assert.equal(h.snapshot(),paused);
  h.doc.hidden=true;h.doc.emit('visibilitychange');h.doc.hidden=false;h.doc.emit('visibilitychange');h.advance(1000);assert.equal(h.snapshot(),paused);
  h.control.emit('click');h.advance(15000);h.final(input.default);h.enter(h.area);h.advance(3000);h.final(input.default);
 }
 // Hidden tabs pause automatic clocks, returning resumes them.
 {
  const h=mapSetup();h.enter(h.area);h.advance(1300);h.doc.hidden=true;h.doc.emit('visibilitychange');const snap=h.snapshot();h.advance(20000);assert.equal(h.snapshot(),snap);h.doc.hidden=false;h.doc.emit('visibilitychange');h.advance(15000);h.final(input.default);
 }
 // Family switch cancels old callbacks and commits the newest final map instantly.
 {
  const h=mapSetup();h.enter(h.area);h.advance(1300);const ids=Object.keys(input.data.p);h.choose(ids[0]);h.choose(ids[7]);const snap=h.snapshot();h.advance(20000);h.final(ids[7]);assert.equal(h.snapshot(),snap);
 }
 // Keyboard focus on an unrevealed adopted state finishes without changing its target.
 {
  const h=mapSetup();h.enter(h.area);const p=input.data.p[input.default],i=p.y.findIndex(y=>y>0),state=h.states[i];h.doc.activeElement=state;h.svg.emit('focusin',state);h.final(input.default);assert.equal(h.doc.activeElement,state);assert.ok(h.root.one['#sm-ro1'].textContent.includes(String(p.y[i])));
 }
 // Pointer skips; print, reduce, absent utility and unsupported observer preserve final state.
 {
  const h=mapSetup();h.enter(h.area);h.svg.emit('pointerdown',h.states[0]);h.final(input.default);
 }
 for(const mode of [{reduce:true},{unsupported:true},{noUtility:true}]){const h=mapSetup(mode);h.enter(h.area);h.advance(20000);h.final(input.default)}
 for(const change of ['reduce','print']){
  const h=mapSetup();h.enter(h.area);h.advance(1300);if(change==='reduce'){const q=h.queries['(prefers-reduced-motion: no-preference)'];q.matches=false;q.changed()}else h.events.beforeprint.forEach(f=>f());h.final(input.default);h.advance(20000);h.final(input.default);
 }
}
if(input.task==='flow'){
 for(const layout of ['desktop','tablet','vertical'])for(const reason of ['complete','hidden','offscreen','reduce','print']){
  const h=setup(),root=new El(),sc=new El({width:'228'}),tc=new El({x:'228',width:'713'}),gates=Array.from({length:5},()=>new El()),pipeline=[330,461,605,723].map(cx=>new El({cx:String(cx)})),nodes=Array.from({length:5},()=>new El()),ends=[new El(),new El()],svg=new El(),vs=new El(),hw=new El(),steps=new El();
  svg.pauseAnimations=()=>svg.paused=true;svg.unpauseAnimations=()=>svg.paused=false;svg.getCurrentTime=()=>2.8;
  const smil=Array.from({length:28},(_,i)=>Object.assign(new El({'data-phase':String(i%14*12/14)}),{beginElementAt(t){this.begin=t},endElement(){this.ended=true},ownerSVGElement:svg}));svg.many={'animateMotion, animate':smil};
  const vertical=Array.from({length:9},(_,i)=>Object.assign(new El({'data-phase':String(i*12/9)}),{parentNode:new El()}));
  hw.getBoundingClientRect=()=>({width:layout==='desktop'?1050:0});
  root.one={'.sf-svg':svg,'.sf-vs--a':vs,'.sf-source-clip':sc,'.sf-trunk-clip':tc,'.sf-hw':hw,'.sf-tip':null,'.sf-vstep:last-child':new El(),'.sf-vleft':new El(),'.sf-vsteps':steps};
  root.many={'.sf-dot':pipeline,'.sf-notch':gates,'.sf-vn':nodes,'.sf-endbox, .sf-vend':ends,'.sf-vd':vertical};
  h.doc.querySelectorAll=k=>k==='.story-flow'?[root]:[];h.sandbox.getComputedStyle=()=>({display:layout==='tablet'?'grid':'block'});h.run(input.flow);h.enter(root);assert.equal(sc.getAttribute('width'),'0');let nextStarted=false,nextFinal=false;const next=new El();h.win.NXMotion.once(next,ctx=>{nextStarted=true;ctx.finish('complete')},{finish(){nextFinal=true}});h.enter(next);assert.equal(nextStarted,false);assert.equal(nextFinal,false);
  assert.ok(pipeline.every(n=>Number(n.style.opacity)===0));h.advance(500);assert.equal(sc.getAttribute('width'),'228');assert.ok(pipeline.every(n=>Number(n.style.opacity)===0));h.advance(250);assert.equal(nextFinal,true,'An intersecting intro waiting behind the flow must reveal its final content after 600ms.');assert.equal(Number(pipeline[0].style.opacity),1);assert.equal(Number(pipeline[3].style.opacity),0);h.advance(850);assert.equal(tc.getAttribute('width'),'713');h.advance(1216);assert.ok(root.classList.contains('sf-dots'));assert.equal(nextStarted,false);assert.ok(pipeline.every(n=>n.style.opacity===undefined));
  if(layout==='desktop')assert.ok(smil.every(a=>typeof a.begin==='number'&&a.begin<=0));else assert.ok(vertical.every(e=>e.animations[0].options.iterations===2&&e.animations[0].options.duration===12000));
  if(reason==='hidden'){h.doc.hidden=true;h.doc.emit('visibilitychange')}
  else if(reason==='offscreen'){h.observers.at(-1).f([{target:root,isIntersecting:false,intersectionRatio:0}]);
  }else if(reason==='reduce'){const q=h.queries['(prefers-reduced-motion: no-preference)'];q.matches=false;q.changed()}
  else if(reason==='print')h.events.beforeprint.forEach(f=>f());
  h.advance(30000);assert.ok(!root.classList.contains('sf-dots'));assert.ok(!root.classList.contains('sf-revealing'));assert.equal(sc.getAttribute('width'),'228');assert.equal(tc.getAttribute('width'),'713');assert.ok(gates.every(e=>e.style.opacity===undefined));assert.ok(svg.paused);assert.equal(h.timers.size,0);
  h.enter(root);h.advance(30000);assert.ok(!root.classList.contains('sf-dots'));
 }
 {
  const h=setup(),root=new El(),svg=new El(),sc=new El({width:'228'}),tc=new El({x:'228',width:'713'}),vs=new El(),hw=new El();svg.pauseAnimations=()=>svg.paused=true;
  root.one={'.sf-svg':svg,'.sf-vs--a':vs,'.sf-source-clip':sc,'.sf-trunk-clip':tc,'.sf-hw':hw,'.sf-tip':null,'.sf-vstep:last-child':new El(),'.sf-vleft':new El(),'.sf-vsteps':new El()};
  h.doc.querySelectorAll=k=>k==='.story-flow'?[root]:[];h.run(input.flow);h.enter(root);h.advance(500);assert.ok(Number(tc.getAttribute('width'))<713);
  root.getBoundingClientRect=()=>({top:-600,bottom:-20,left:0,right:600,width:600,height:580});h.advance(16);
  assert.equal(sc.getAttribute('width'),'228');assert.equal(tc.getAttribute('width'),'713');assert.ok(!root.classList.contains('sf-revealing'));assert.ok(!root.classList.contains('sf-dots'));h.advance(30000);assert.equal(h.timers.size,0);
 }
 for(const mode of [{reduce:true},{unsupported:true},{noUtility:true}]){
  const h=setup(mode),root=new El(),svg=new El(),sc=new El({width:'228'}),tc=new El({x:'228',width:'713'}),vs=new El();svg.pauseAnimations=()=>svg.paused=true;
  root.one={'.sf-svg':svg,'.sf-vs--a':vs,'.sf-source-clip':sc,'.sf-trunk-clip':tc,'.sf-hw':new El(),'.sf-tip':null,'.sf-vstep:last-child':new El(),'.sf-vleft':new El(),'.sf-vsteps':new El()};
  h.doc.querySelectorAll=k=>k==='.story-flow'?[root]:[];h.run(input.flow);h.enter(root);h.advance(30000);assert.equal(sc.getAttribute('width'),'228');assert.equal(tc.getAttribute('width'),'713');assert.equal(h.timers.size,0);
 }
}

console.log('PASS '+input.task);
'''


def runtime(task):
    motion = re.search(r'<script>(.*?)</script>', (HERE / 'partials/motion.html').read_text(), re.S)[1]
    partial = (HERE / 'partials/story_map.html').read_text()
    data = json.loads(re.search(r'<script type="application/json" id="sm-d">(.*?)</script>', partial, re.S)[1])
    labels = json.loads((HERE / 'i18n/story_map.en.json').read_text())
    labels = {k.removeprefix('map.').replace('state.', 's.'): v for k, v in labels.items()}
    return dict(task=task, motion=motion, map=mapgen.JS, data=data, default=mapgen.DEFAULT_POLICY,
                labels=labels, flow=flowgen.JS)


def run_node(data):
    completed = subprocess.run(['node', '-e', HARNESS], input=json.dumps(data), text=True, capture_output=True)
    if completed.returncode:
        raise AssertionError(completed.stderr + completed.stdout)
    return completed.stdout


@unittest.skipUnless(shutil.which('node'), 'node required for motion contracts')
class MapMotionTests(unittest.TestCase):
    def test_all_family_chronology_controls_cancellation_and_static(self):
        self.assertIn('PASS map', run_node(runtime('map')))

    def test_static_data_labels_dots_and_geometry_match_stored_baseline(self):
        old = BASELINE.read_text()
        new = (HERE / 'partials/story_map.html').read_text()
        blob = r'<script type="application/json" id="sm-d">(.*?)</script>'
        before_data, after_data = (json.loads(re.search(blob, markup, re.S)[1]) for markup in (old, new))
        self.assertEqual(before_data['c'], after_data['c'])
        for family in mapgen.story_data()['policies']:
            pid = family['id']
            self.assertEqual(after_data['p'][pid]['y'], [family['years'].get(code, 0) for code in mapgen.CODES])
            self.assertEqual(after_data['p'][pid]['e'], {code: value for code, value in before_data['p'][pid]['e'].items() if code in family['years']})
            for code, year in family['years'].items():
                self.assertEqual(before_data['p'][pid]['y'][mapgen.CODES.index(code)], year)
        # The assembler removes internal geometry comments from served HTML.
        marked_old = old.replace('<g id="sm-f">', mapgen.GEO_BEGIN + '<g id="sm-f">').replace('<g id="sm-dots">', mapgen.GEO_END + '<g id="sm-dots">')
        before, after = mapgen.geometry_from_partial(marked_old), mapgen.geometry_from_partial(new)
        self.assertEqual([(s['c'], s['x'], s['y'], s['h']) for s in before['states']],
                         [(s['c'], s['x'], s['y'], s['h']) for s in after['states']])
        # Only tiny secondary rings/edge specks may be cleaned; main state polygons stay exact.
        self.assertEqual([s['d'].split('z')[0] for s in before['states']],
                         [s['d'].split('z')[0] for s in after['states']])
        self.assertEqual(len(after['states']), 51)
        with patch.object(mapgen, 'GEO', Path('/private/tmp/wp2-no-geography')):
            self.assertEqual(mapgen.render_partial(), new)


@unittest.skipUnless(shutil.which('node'), 'node required for motion contracts')
class FlowMotionTests(unittest.TestCase):
    def test_finite_two_passes_cleanup_and_three_layouts(self):
        self.assertIn('PASS flow', run_node(runtime('flow')))

    def test_stored_flow_geometry_is_preserved_and_no_gate_count_is_drawn(self):
        old = BASELINE.read_text()
        new = build.render_partial('story_flow', 'en', build.Errors())
        for cls in ('sf-rib', 'sf-hair', 'sf-trunk', 'sf-brk'):
            pattern = r'<path class="' + cls + r'" d="([^"]+)"'
            self.assertEqual(re.findall(pattern, old), re.findall(pattern, new), cls)
        # R25: the five hairlines that led to the gate counts are gone; the four node leaders and the leader of the starting count stay.
        pattern = r'<path class="sf-lead" d="([^"]+)"'
        before, after = re.findall(pattern, old), re.findall(pattern, new)
        self.assertEqual(before[1:], after[1:])
        self.assertTrue(before[0].endswith(after[0]) and before[0] != after[0])
        self.assertEqual(after[0].count('M'), 1)
        pattern = r'<path class="sf-notch" d="([^"]+)"'
        self.assertEqual(''.join(re.findall(pattern, old)), ''.join(re.findall(pattern, new)))
        self.assertEqual(re.findall(r'<animateMotion[^>]*repeatCount="([^"]+)"', new), ['2'] * 14)
        # Final reference numbers never roll or get replaced by an intermediate value.
        self.assertNotIn('textContent', flowgen.JS[flowgen.JS.index('function motion(root)'):])

    def test_flow_prints_only_counts_that_v5_pages_print_in_both_languages(self):
        # Owner, 2026-10-14 (V5.1): the figure returns on Screening with the texts, the four source families and the candidates only.
        for lang in ('en', 'zh'):
            errors = build.Errors()
            figure = build.render_partial('story_flow', lang, errors)
            self.assertEqual(errors.errors, [])
            visible = ' '.join(unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<(style|script)\b.*?</\1>', '', figure, flags=re.S))).split())
            self.assertEqual(set(re.findall(r'\d[\d,.]*\d|\d', visible)),
                             {'483,414', '315,727', '125,919', '41,496', '272', '7,781', '2020', '2021', '3'})   # F1d, F1e: both languages say 2020–2021
            self.assertNotRegex(visible, r'\d\s?k\b|万')
            self.assertNotIn('sf-gc', figure)
            self.assertNotIn('sf-vchips', figure)
            for word in ('gate', 'shortlist', 'pipeline', '关卡', '闸门', '流水线', '最多 8'):
                self.assertNotIn(word, visible.lower())


class HomeStaticTests(unittest.TestCase):
    def test_map_visible_copy_matches_approved_baseline(self):
        def words(text):
            text = re.search(r'<div class="sm" id="sm-root">.*?<script type="application/json" id="sm-d">', text, re.S)[0]
            text = re.sub(r'<p class="sm-cap".*?</p>', '', text, flags=re.S)
            text = re.sub(r'<b><span class="sr-only">[^<]*</span>', '<b>', text)
            text = re.sub(r'<i data-k="(?:control\.[^"]+|stats_one)">[^<]*</i>', '', text)
            text = re.sub(r'<(style|script)\b.*?</\1>', '', text, flags=re.S)
            text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
            text = re.sub(r'<[^>]*>', ' ', text)
            return ' '.join(unescape(text).split())
        old = BASELINE.read_text()
        new = build.render_partial('story_map', 'en', build.Errors())
        # R29 (TRIM_SPEC_V51.md, HM2 and HM7; the baseline file is in deploy_output and stays as it is): the eight per-policy captions are gone and the
        # first policy is named "Recognition of other states’ licenses". Every other word of the card is the approved baseline's.
        old = re.sub(r'<i data-k="cap\.[^"]+">[^<]*</i>', '', old).replace('License recognition and endorsement', 'Recognition of other states’ licenses')
        # R21: the Nurse compact map again counts 41 jurisdictions (Connecticut was drawn as a state without the law), as in the approved baseline.
        self.assertEqual(words(old), words(new))

    def test_below_the_map_the_home_is_the_staircase(self):
        # Owner, 2026-10-14 (V5.1): the blocks below the map were weak; the previous staircase, with V5 content, replaces them.
        for lang in ('en', 'zh'):
            errors = build.Errors()
            story = build.render_story_block(lang, errors)
            self.assertEqual(errors.errors, [])
            strings = build.load_dict('home_story', lang, errors)
            ladder = re.search(r'<section class="nx-sec nx-ladder".*?</section>', story, re.S)[0]
            self.assertEqual(len(re.findall(r'<li class="nx-st" data-nx-intro="stair-step">', ladder)), 3)
            for key, route in zip(('screen', 'estimate', 'explore'), ('screening', 'worked_examples', 'synthesize')):
                self.assertIn(f'<a href="/viz/{lang}/{route}.html">{strings[f"story.{key}.word"]}</a>', ladder)
            self.assertEqual(re.findall(r'<h2 class="nx-h2" id="hs-levels">(.*?)</h2>', ladder), [strings['story.levels.h']])
            # The headline block and the staircase; no doors, feature card, author block, update stamp or paper title.
            self.assertEqual(story.count('data-home-block='), 2)
            self.assertNotRegex(story, r'class="[^"]*(?:v5-door|home-evidence|home-feature|home-author|home-asof|home-contribution)')
            self.assertNotIn('research-flow', story)
            # Owner, 2026-10-13/14: no author block, update stamp, disclosure, recall line or paper title on Home.
            visible = unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<(style|script)\b.*?</\1>', '', story, flags=re.S)))
            for removed in ('AEJ draft', 'AEJ 稿', 'University of Southern California', '南加州大学', 'Results updated', '结果更新',
                            'Research design, rules and review', '研究设计、规则与审阅', 'Method →', '方法 →', 'Earlier public draft', '较早公开稿',
                            'Recall of published', '召回率', 'revised draft', '修订稿', '99/113', 'we wrote', 'Finding Causal Variation'):
                self.assertNotIn(removed, visible)
            # One staircase script, after the staircase, with the shared motion utility as its only dependency.
            self.assertEqual(story.count("document.querySelector('.nx-stair')"), 1)
            self.assertLess(story.index('class="nx-sec nx-ladder"'), story.index("document.querySelector('.nx-stair')"))
            self.assertNotIn('{{', story)
        # The static fallback under reduced motion and print: every reveal target and every counted figure shows its final state.
        chrome = (HERE / 'partials/head_css.html').read_text()
        self.assertEqual(chrome.count('.nx-tally, #countGroups, #countPolicies, #countJurs { visibility: visible !important; }'), 2)
        self.assertEqual(chrome.count('[data-nx-reveal] { opacity: 1 !important; transform: none !important; }'), 2)
        self.assertIn('@media (prefers-reduced-motion: reduce)', chrome)

    def test_hero_holds_the_shared_adoption_map_between_headline_and_staircase(self):
        for lang in ('en', 'zh'):
            errors = build.Errors()
            story = build.render_story_block(lang, errors)
            self.assertEqual(errors.errors, [])
            map_markup = build.render_partial('story_map', lang, errors)
            self.assertEqual(errors.errors, [])
            # One source: the generated partial itself, once, with its own caption and playback control.
            self.assertEqual(story.count(map_markup), 1)
            self.assertEqual(story.count('id="sm-root"'), 1)
            self.assertEqual(story.count('id="sm-control"'), 1)
            self.assertLess(story.index('class="home-thesis'), story.index('id="sm-root"'))
            self.assertLess(story.index('id="sm-root"'), story.index('class="nx-sec nx-ladder"'))
            ids = re.findall(r'\bid="([^"]+)"', story)
            self.assertEqual(len(ids), len(set(ids)))
            # Sources stay on the Adoption page: one link, no table.
            self.assertNotIn('id="map-sources"', story)
            self.assertEqual(story.count(f'href="/viz/{lang}/adoption.html#map-sources"'), 1)
            self.assertNotIn('nx-map-sources', story)

    def test_hero_layout_columns_follow_the_adoption_page_rules(self):
        source = (HERE / 'partials/home_story.html').read_text()
        # The same stylesheet and card classes the Adoption page uses for this component.
        self.assertIn('{{css:design_hero}}', source)
        self.assertIn('nx-herogrid', source)
        self.assertIn('nx-herotext', source)
        self.assertIn('nx-herocard', source)
        # 1100 px and wider: design_hero.css draws 2fr | 3fr; 700 px and below and 701-1099 px: one column (R16 removed the 701-1099 split).
        self.assertNotRegex(source, r'@media\(min-width:701px\) and \(max-width:1099px\)\{\.home-hero\{display:grid;grid-template-columns:minmax\(0,2fr\) minmax\(0,3fr\)')
        self.assertIn('@media(max-width:700px)', source)
        self.assertIn('.home-v5 .nx-frame{margin-inline:0}', source)
        self.assertIn('.home-v5 .nx-in{padding-inline:8px}', source)
        hero = (HERE / 'design/design_hero.css').read_text()
        self.assertIn('grid-template-columns:minmax(0,2fr) minmax(0,3fr)', hero)
        self.assertIn('.sm-svg{grid-area:2/1/3/3;width:100%;height:auto;max-height:360px', hero)

    def test_english_home_map_rows_keep_their_height_when_the_policy_changes(self):
        # R17 and R18 (BUILD_V5.md, "Fix round 12"): the arithmetic was checked against the R16 browser sweep of every width
        # from 320 to 1440 px; if any pinned input below changes, that sweep has to be run again.
        home = (HERE / 'partials/home_story.html').read_text()
        story_map = (HERE / 'partials/story_map.html').read_text()
        hero = (HERE / 'design/design_hero.css').read_text()
        chrome = (HERE / 'partials/head_css.html').read_text()
        # Caption: R29 (HM2) removed the per-policy captions, so the line under the card is the same one-line provenance for every policy and no rule
        # reserves room for two or three lines any more (it was 4.5em from 361 to 490 px in Home and 78 px at 360 px and below in the hero stylesheet).
        self.assertNotIn('.sm-cap{min-height', home)
        self.assertNotIn('.sm .sm-cap{min-height', hero)
        self.assertIn('.sm .sm-cap{max-width:var(--nx-col-text,680px);margin:12px 0 0;font-size:13px;line-height:1.5;', story_map)
        self.assertIn('<p class="sm-cap" id="sm-cap">{{t:map.provenance}}</p>', story_map)
        self.assertNotIn('sm-definition', story_map)
        self.assertNotIn('map.cap.', story_map)
        # Title row: a 24 px name line over a 20 px stats line is 44 px; only the 15 px name can wrap (68 px).
        self.assertIn('.sm .sm-pn{display:block;font-size:15px;font-weight:600;line-height:24px;', story_map)
        self.assertIn('.sm .sm-pst{display:block;font-size:13px;line-height:20px;', story_map)
        self.assertIn('@media (max-width: 360px) {\n  .sm .sm-head { min-height: 68px; }\n}', hero)
        # The row's width is the viewport minus 22.4 px (0.7rem body padding each side, 900 px and below) minus 16 px
        # (8 px row padding each side, 600 px and below).
        self.assertIn('@media (max-width: 900px) {\n    body { padding: .7rem; }', chrome)
        self.assertIn('.home-v5 .nx-in{padding-inline:8px}', home)
        self.assertIn('.home-v5 .nx-frame{margin-inline:0}', home)
        title_rule = '@media (min-width:361px) and (max-width:390px){.home-v5:not(:lang(zh)) .sm-pn{font-size:min(15px,calc((100vw - 60px)/22))}}'
        self.assertEqual(home.count(title_rule), 1)
        # The fitted size is exactly 15 px at the rule's last pixel (no step at 391 px) and 13.68 px at its first.
        self.assertEqual((390 - 60) / 22, 15)
        self.assertAlmostEqual((361 - 60) / 22, 13.68, places=2)
        # Both rules are English-only and Home-only; neither is in the shared map partial or the shared hero stylesheet.
        for source in (story_map, hero):
            self.assertNotIn('.home-v5', source)
            self.assertNotIn('min(15px', source)

    def test_staircase_styles_stay_scoped_to_home_and_retired_table_styles_remain_scoped(self):
        story = (HERE / 'partials/home_story.html').read_text()
        # The reviewed staircase rules of design_hero.css, re-scoped from .story to .home-v5 (Home is no longer inside .story).
        for rule in ('.home-v5 .nx-ladder{margin-top:calc(var(--nx-gap) - 32px);--lv-word:var(--nx-type-feature,28px)}',
                     '@media(max-width:759px){.home-v5 .nx-ladder{--lv-word:22px}}',
                     '.home-v5:lang(zh) .nx-lv-word{font-size:var(--lv-word);line-height:1.2}',
                     '.home-v5 .nx-lv-word a{display:inline-flex;align-items:center;min-height:44px;padding-block:6px;',
                     '.home-v5 .nx-lv{list-style:none;margin:0;padding-inline:0}',
                     '.home-v5 .nx-nd{margin:0;overflow-wrap:normal}',
                     '.home-v5 .nx-stair .nx-st[data-nx-intro].nx-motion-complete [data-nx-reveal]{opacity:1}'):
            self.assertIn(rule, story)
        for retired in ('.home-evidence', '.v5-door', '.home-feature', '.home-comparison', '.home-result', 'grid-template-areas'):
            self.assertNotIn(retired, story)
        self.assertIn('@media(max-width:700px)', story)
        hero = (HERE / 'design/design_hero.css').read_text()
        # The shared stylesheet still draws the staircase: three columns that rise to the right, one column on a phone.
        self.assertIn('.nx-stair{display:grid;grid-template-columns:1.5fr 1fr 1fr;gap:32px;align-items:start;padding:0}', hero)
        self.assertIn('.nx-st:nth-child(1){margin-top:80px}.nx-st:nth-child(2){margin-top:40px}.nx-st:nth-child(3){margin-top:0}', hero)
        self.assertIn('.nx-st:nth-child(2){margin-left:24px}.nx-st:nth-child(3){margin-left:48px}', hero)
        source = (HERE.parents[1] / 'scripts/zh_pages/policy_db.en.html').read_text().split('<!-- NX:MOTION:START -->')[0]
        self.assertIn('#tableView thead tr::before', source)
        self.assertNotRegex(source, r'(?m)^\s*thead tr::before')

    def test_standalone_map_bilingual_controls_keep_accessible_names(self):
        for lang in ('en', 'zh'):
            errors = build.Errors()
            markup = build.render_partial('story_map', lang, errors)
            self.assertEqual(errors.errors, [])
            self.assertNotIn('{{', markup)
            self.assertEqual(markup.count('id="sm-control"'), 1)
            self.assertLess(markup.index('id="sm-control"'), markup.index('<svg class="sm-svg"'))
            self.assertNotIn('repeatCount="indefinite"', markup)
            self.assertNotIn('animation:infinite', markup)
            self.assertRegex(markup, r'<button[^>]*id="sm-control"[^>]*aria-label="[^"{}]+"')

    def test_map_tokens_resolve_canonical_aliases(self):
        self.assertEqual(mapgen.token_problems(), [])
        css = mapgen.TOKENS.read_text()
        original = Path.read_text
        def source(path, *args, **kwargs):
            return css.replace('--nx-text-2:var(--nx-text)', '--nx-text-2:var(--nx-missing)') if path == mapgen.TOKENS else original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', source):
            self.assertTrue(any('--nx-text-2' in issue for issue in mapgen.token_problems()))


if __name__ == '__main__':
    unittest.main()
