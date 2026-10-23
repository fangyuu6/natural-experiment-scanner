/* Offline DOM contract for the actual page script; no browser or network. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {ui,script}=JSON.parse(fs.readFileSync(0,'utf8'));
class Element{
 constructor(tag){
  this.tagName=tag;this.children=[];this.dataset={};this.events={};this.style={removeProperty(key){delete this[key];}};this.attributes={};this.value='';this._text='';
  const classes=new Set();
  this.classList={toggle(name,force){if(force)classes.add(name);else classes.delete(name);},contains:name=>classes.has(name)};
 }
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 append(...items){for(const item of items){if(item.tagName==='fragment')this.append(...item.children);else{item.parent=this;this.children.push(item);}}}
 replaceChildren(...items){this._text='';this.children=[];this.append(...items);}
 addEventListener(name,fn){this.events[name]=fn;}
 setAttribute(name,value){this.attributes[name]=String(value);}
 getAttribute(name){return this.attributes[name];}
 querySelector(selector){return this.children.find(child=>child.tagName===selector);}
 getBoundingClientRect(){return {top:this.top??0};}
 closest(selector){if(selector===this.tagName||selector==='.'+this.className)return this;return this.parent?.closest(selector);}
 focus(){document.activeElement=this;}
}
const ids={};
for(const id of ['design-family','design-year','design-strip','design-selected-heading','design-selected-clock','design-readout','design-inventory-body','design-timing-grid'])ids[id]=new Element('div');
ids['design-opportunity-data']={textContent:JSON.stringify(ui)};
ids['design-family'].value=ui.families[0].id;
ids['design-year'].value=String(ui.families[0].summary.modal_years[0]);
ids['design-year'].options=[{value:'all'},...ui.years.map(y=>({value:String(y)}))];
const gridRows=ui.families.map(f=>{const row=new Element('g');row.className='do-grid-row';row.dataset.family=f.id;return row;});
const cells=gridRows.flatMap(row=>ui.years.map(y=>{const cell=new Element('g');cell.className='do-cell';cell.dataset={family:row.dataset.family,year:String(y),count:String(ui.families.find(f=>f.id===row.dataset.family).groups.find(g=>g.year===y)?.count||0)};cell.append(new Element('rect'));row.append(cell);return cell;}));
const yearLabels=ui.years.map(year=>{const label=new Element('text');label.dataset.year=String(year);return label;});
const content=new Element('div'),inventory=new Element('details');
const gridBox=new Element('div');gridBox.clientWidth=754;gridBox.scrollLeft=0;
gridBox.getBoundingClientRect=()=>({left:0,right:gridBox.clientWidth});
gridBox.scrollBy=options=>{assert.equal(options.behavior,'instant');gridBox.scrollLeft=Math.max(gridBox.clientWidth-1034,Math.min(0,gridBox.scrollLeft+options.left));};
for(const cell of cells)cell.children[0].getBoundingClientRect=()=>{
 const left=ui.years.indexOf(Number(cell.dataset.year))*47-(1034-gridBox.clientWidth+gridBox.scrollLeft);
 return {left,right:left+44};
};
const document={querySelector:selector=>selector==='.do-grid-scroll'?gridBox:selector==='.do-cell.is-selected'?cells.find(cell=>cell.classList.contains('is-selected')):selector==='.do-content'?content:selector==='.do-inventory'?inventory:undefined,getElementById:id=>ids[id],createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('text');node.textContent=text;return node;},createDocumentFragment:()=>new Element('fragment'),querySelectorAll:selector=>selector==='.do-cell'?cells:selector==='.do-grid-row'?gridRows:selector==='.do-year-label'?yearLabels:[]};
const scrolls=[];
const animations=[],introOptions=[],finished=[];
const motion={
 once(element,run,options){
  introOptions.push(options);const durations=[];
  options.prepare();
  run({animate(target,frames,options){animations.push({target,frames,options});},timer(fn,delay){durations.push(delay);}});
  assert.deepEqual(durations,[850]);return {finish(){finished.push(element);options.finish();}};
 },
 transition(element,run,options){assert(element);assert([180,320].includes(options.maxDuration));run({animate(target,frames,options){assert.equal(target,ids['design-readout']);assert.equal(options.duration,320);assert(frames.every(frame=>Object.keys(frame).join()==='opacity'));},timer(fn,delay){assert([180,320].includes(delay));},finish(){}});}
};
const context={document,window:{NXMotion:motion,getComputedStyle(){return {getPropertyValue(){return '320ms';}};}},console,innerHeight:800,scrollBy(options){scrolls.push({...options});}};
vm.runInNewContext(script,context);
assert.equal(introOptions.length,1);assert.equal(introOptions[0].maxDuration,1000);
assert.equal(animations.length,cells.filter(cell=>Number(cell.dataset.count)>0).length);assert(animations.every(a=>a.target.tagName==='rect'&&a.options.duration===220&&a.frames.every(frame=>frame.opacity===1)));
assert(animations.every(a=>Number(a.target.parent.dataset.count)>0),'Zero-cell backgrounds remain fully static.');
assert(Math.max(...animations.map(a=>a.options.duration+a.options.delay))<=1000);
const stripButtons=[...ids['design-strip'].children];
assert.equal(stripButtons.length,23);
assert.deepEqual(stripButtons.map(button=>button.dataset.year),['all',...ui.years.map(String)]);
assert(stripButtons.every(button=>button.tagName==='button'&&button.type==='button'));
function readRows(){return ids['design-readout'].children.filter(e=>e.className!=='do-empty');}
function verifySelection(family,year){
 const expected=family.jurisdictions.filter(row=>row.strict&&(year==='all'||row.year===Number(year)));
 assert.equal(readRows().length,expected.length);
 assert.deepEqual(readRows().map(li=>li.children[0].textContent),expected.map(row=>row.name));
 assert.deepEqual(readRows().map(li=>li.children[1].textContent),expected.map(row=>String(row.date??row.year)));
 for(const [index,li] of readRows().entries()){
  const source=expected[index];const group=li.children[2];
  assert.equal(group.children.filter(e=>e.className==='do-source-word').length,source.sources.length?1:0);
  const links=group.children.filter(e=>e.tagName==='a');
  assert.deepEqual(links.map(e=>e.textContent),source.sources.map((_,i)=>String(i+1)));
  assert.deepEqual(links.map(e=>e.href),source.sources);
  assert.deepEqual(links.map(e=>e.getAttribute('aria-label')),source.sources.map((_,i)=>ui.strings.source+' '+(i+1)));
 }
 if(year!=='all'){
  const selected=cells.find(cell=>cell.dataset.family===family.id&&cell.dataset.year===String(year));
  const bounds=selected.children[0].getBoundingClientRect();
  assert(bounds.left>=0&&bounds.right<=gridBox.clientWidth,'Selected column must be visible after every selection.');
 }
 assert.equal(ids['design-selected-clock'].textContent,family.clock);
 assert(ids['design-selected-heading'].textContent.includes(family.label));
 assert(!ids['design-selected-heading'].textContent.includes('NaN'));
 assert.deepEqual(gridRows.filter(row=>row.classList.contains('is-selected')).map(row=>row.dataset.family),[family.id]);
 assert.deepEqual(cells.filter(cell=>cell.getAttribute('aria-pressed')==='true').map(cell=>[cell.dataset.family,cell.dataset.year]),year==='all'?[]:[[family.id,String(year)]]);
 assert.deepEqual(cells.filter(cell=>cell.tabIndex===0).map(cell=>[cell.dataset.family,cell.dataset.year]),[[family.id,year==='all'?String(ui.years[0]):String(year)]]);
 assert.deepEqual(stripButtons.filter(button=>button.getAttribute('aria-pressed')==='true').map(button=>button.dataset.year),[String(year)]);
 assert.deepEqual(ids['design-strip'].children,stripButtons,'Strip buttons must retain keyboard focus across selection updates.');
}
for(const family of ui.families){
 ids['design-family'].value=family.id;ids['design-family'].events.change();
 verifySelection(family,String(family.summary.modal_years[0]));
 const inventory=ids['design-inventory-body'].children;
 assert.equal(inventory.length,51);
 for(const [index,tr] of inventory.entries()){
  const source=family.jurisdictions[index];
  assert.equal(tr.dataset.jurisdiction,source.code);
  assert.equal(tr.children[1].textContent,String(source.date??source.year??ui.strings.unknown));
  assert.equal(tr.children[2].textContent,source.status_label+(source.exclusion_reason||''));
  const links=tr.children[3].children[0].children;
  assert.deepEqual(links.map(link=>link.textContent),source.sources.map((_,i)=>String(i+1)));
  assert.deepEqual(links.map(link=>link.getAttribute('aria-label')),source.sources.map((_,i)=>ui.strings.source+' '+(i+1)));
 }
 for(const year of [...ui.years,'all']){
  ids['design-year'].value=String(year);ids['design-year'].events.change();verifySelection(family,String(year));
  const button=stripButtons.find(b=>b.dataset.year===String(year));button.focus();
  ids['design-strip'].events.click({target:button.children[0]});
  verifySelection(family,String(year));assert.equal(document.activeElement,button);
 }
}
for(const cell of cells){
 let prevented=false;
 ids['design-timing-grid'].events.keydown({key:'Enter',target:cell,preventDefault(){prevented=true;}});
 assert(prevented);verifySelection(ui.families.find(f=>f.id===cell.dataset.family),cell.dataset.year);
}
const cell=cells.find(c=>c.dataset.family==='paid_sick_leave'&&c.dataset.year==='2018');
ids['design-timing-grid'].events.click({target:cell});verifySelection(ui.families.find(f=>f.id==='paid_sick_leave'),'2018');
let prevented=false;
ids['design-timing-grid'].events.keydown({key:' ',target:cell,preventDefault(){prevented=true;}});assert(prevented);
verifySelection(ui.families.find(f=>f.id==='paid_sick_leave'),'2018');
const row=gridRows.find(row=>row.dataset.family==='tnc_framework');
ids['design-timing-grid'].events.click({target:row});
const family=ui.families.find(f=>f.id===row.dataset.family);verifySelection(family,String(family.summary.modal_years[0]));
function key(target,key){
 let prevented=false;
 ids['design-timing-grid'].events.keydown({key,target,preventDefault(){prevented=true;}});
 return prevented;
}
const origin=cells.find(c=>c.dataset.family===ui.families[0].id&&c.dataset.year==='2020');
ids['design-timing-grid'].events.click({target:origin});origin.focus();
for(const [direction,index] of [['ArrowRight',17],['ArrowDown',39],['ArrowDown',61],['Home',44],['ArrowLeft',44],['End',65],['ArrowRight',65],['ArrowUp',43]]){
 assert(key(document.activeElement,direction));
 assert.equal(document.activeElement,cells[index]);
 assert.deepEqual(cells.filter(c=>c.tabIndex===0),[cells[index]]);
 assert.equal(origin.getAttribute('aria-pressed'),'true','Arrows move focus without selecting.');
}
assert(!key(document.activeElement,'Tab'),'Tab must leave the grid normally.');
assert(key(document.activeElement,'Enter'));
verifySelection(ui.families[1],'2025');
const bottom=cells.at(-1);bottom.focus();key(bottom,'ArrowDown');assert.equal(document.activeElement,bottom);
const top=cells[0];top.focus();key(top,'ArrowUp');assert.equal(document.activeElement,top);
key(top,' ');verifySelection(ui.families[0],String(ui.years[0]));
// The guard reserves room for the first record, with no animation, and keeps the selected cell visible.
const heading=ids['design-selected-heading'];
heading.top=768;cell.top=460;
for(const [height,expected] of [[900,58],[800,158],[720,238]]){
 context.innerHeight=height;scrolls.length=0;
 ids['design-timing-grid'].events.click({target:cell});
 assert.deepEqual(scrolls,[{top:expected,behavior:'instant'}]);
}
heading.top=500;scrolls.length=0;ids['design-timing-grid'].events.click({target:cell});assert.equal(scrolls.length,0);
heading.top=768;cell.top=120;scrolls.length=0;key(cell,'Enter');assert.deepEqual(scrolls,[{top:48,behavior:'instant'}]);
cell.top=60;scrolls.length=0;key(cell,' ');assert.equal(scrolls.length,0);
row.top=300;scrolls.length=0;ids['design-timing-grid'].events.click({target:row});assert.deepEqual(scrolls,[{top:228,behavior:'instant'}]);
assert(finished.length>0,'Selection settles the first reveal.');
ids['design-timing-grid'].events.pointerover({target:cell});
assert.deepEqual(yearLabels.filter(label=>label.classList.contains('is-crosshair')).map(label=>label.dataset.year),[cell.dataset.year]);
assert.deepEqual(gridRows.filter(row=>row.classList.contains('is-crosshair')).map(row=>row.dataset.family),[cell.dataset.family]);
ids['design-timing-grid'].events.focusout({relatedTarget:undefined});
assert(yearLabels.every(label=>!label.classList.contains('is-crosshair')));
inventory.events.toggle();
console.log('Strict ledger, one grid Tab stop, arrow navigation, keyboard selection and instant readout guard verified offline.');
