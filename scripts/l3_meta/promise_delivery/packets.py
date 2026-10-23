"""T240 blind packet builder + validator for promise-vs-delivery coding (pattern of scripts/l2_pipeline/f1_coding.py: whitelist + fail on result fields).

Usage: python3 scripts/l3_meta/promise_delivery/packets.py build   (writes data/l3_meta/promise_delivery_20261022/packets/)
"""
import glob,hashlib,json,os,random,re,sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'../../..'))
OUT=os.path.join(ROOT,'data/l3_meta/promise_delivery_20261022')
LIVE=os.path.join(ROOT,'data/l3_meta/l2_set_live.json')
SALT='T240-promise-delivery-20261022'
WHITELIST=('packet_id','policy_name','policy_family','outcome_label','outcome_unit','treatment_definition','enactment','statute_excerpts')
FORBIDDEN=re.compile(r'(\bestimate[sd]?\b|(?<!per )\bse\b|ci95|ri_p|p[-_ ]?value|beyond_noise|\bnoise\b|\batt\b|forecast|point_est|confidence interval|standard error|not_evaluable|\bresults?\b)',re.I)

def validate(p):
    extra=set(p)-set(WHITELIST)
    if extra:raise ValueError('non-whitelisted key(s): %s'%sorted(extra))
    for k,v in p.items():
        vals=[v] if isinstance(v,str) else v
        if not isinstance(vals,list) or any(not isinstance(x,str) for x in vals):raise ValueError('field %s must be str or list of str (no numeric fields)'%k)
        for x in vals:
            m=FORBIDDEN.search(x)
            if m and k not in ('policy_name',):raise ValueError('result-like text %r in %s'%(m.group(0),k))
    return p

def clean(x):
    """Strip estimator wording from outcome labels (method text, not part of the outcome): 'cohort-size-weighted ... ATT, ' prefix and 'estimated '."""
    x=re.sub(r'^.*?\bATT,\s*','',x);return re.sub(r'\bestimated\s+','',x)

def pid(design_id):return 'P'+hashlib.sha256((SALT+design_id).encode()).hexdigest()[:8]

def card_index():
    idx={}
    pats=['data/l2_pipeline/**/CARD.json','data/l2_pipeline/cards/**/*.json']
    for pat in pats:
        for f in glob.glob(os.path.join(ROOT,pat),recursive=True):
            try:j=json.load(open(f))
            except Exception:continue
            if isinstance(j,dict) and isinstance(j.get('design_id'),str):
                if j['design_id'] not in idx or os.path.getmtime(f)>os.path.getmtime(idx[j['design_id']]):idx[j['design_id']]=f
    return idx

def card_fields(f):
    j=json.load(open(f));t=j.get('treatment') or {}
    mech=j.get('mechanism') if isinstance(j.get('mechanism'),str) else (t.get('predicate') if isinstance(t.get('predicate'),str) else '')
    ad=((t.get('screening_adoption') or {}).get('adopters')) or t.get('adopters') or {}
    en=[]
    if isinstance(ad,dict):en=['%s %s'%(k,v) for k,v in sorted(ad.items()) if isinstance(v,(int,str))][:70]
    ex=[]
    for s in j.get('sources') or []:
        loc=s.get('locator') if isinstance(s,dict) else None
        if isinstance(loc,str) and ('::' in loc or str(s.get('source_id','')).startswith('statute')):ex.append(re.sub(r'\s+',' ',loc)[:300])
    return mech[:700],en,ex[:6]

def design_packet(d,cards):
    po=d['primary_outcomes'][0]
    p=dict(packet_id=pid(d['design_id']),policy_name=d['policy_name'],policy_family=str(d.get('policy_family') or ''),
           outcome_label=clean(str(po.get('label') or '')),outcome_unit=clean(str(po.get('unit') or d.get('outcome_unit') or '')),
           treatment_definition='',enactment=[],statute_excerpts=[])
    f=cards.get(d['design_id'])
    if f:
        p['treatment_definition'],p['enactment'],p['statute_excerpts']=card_fields(f)
    return validate(p),bool(f)

def build():
    from anchors import ANCHORS
    live=json.load(open(LIVE));cards=card_index()
    items=[];gold={};n_card=0
    for d in live['designs']:
        p,c=design_packet(d,cards);n_card+=c;items.append(p)
    for i,a in enumerate(ANCHORS):
        p=dict(packet_id=pid('ANCHOR-%02d'%i),policy_name=a['policy_name'],policy_family=a['policy_family'],outcome_label=a['outcome_label'],
               outcome_unit=a['outcome_unit'],treatment_definition=a['treatment_definition'],enactment=[],statute_excerpts=[])
        items.append(validate(p));gold[p['packet_id']]=dict(direction=a['direction'],match=a['match'],sold=a['sold'])
    random.Random(20261022).shuffle(items)
    nch=6;os.makedirs(OUT+'/packets',exist_ok=True)
    for c in range(nch):
        chunk=items[c::nch]
        path=OUT+'/packets/chunk%d.json'%(c+1)
        if os.path.exists(path):raise SystemExit('exists: '+path)
        json.dump(dict(chunk=c+1,items=chunk),open(path,'w'),indent=1,ensure_ascii=False)
    key={pid(d['design_id']):d['design_id'] for d in live['designs']}
    for name,obj in (('PACKET_KEY.json',dict(design_ids=key,live_pin_sha256=hashlib.sha256(open(LIVE,'rb').read()).hexdigest())),('ANCHOR_GOLD.json',gold)):
        path=OUT+'/'+name
        if os.path.exists(path):raise SystemExit('exists: '+path)
        json.dump(obj,open(path,'w'),indent=1)
    print('designs',len(live['designs']),'with card',n_card,'anchors',len(ANCHORS),'items',len(items))

if __name__=='__main__':
    sys.path.insert(0,os.path.dirname(__file__))
    if sys.argv[1:]==['build']:build()
    else:print(__doc__)
