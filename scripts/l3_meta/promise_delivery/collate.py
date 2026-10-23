"""T240 collation: validate reader files, anchors, kappa, third-read packets, adjudication, freeze hashes.
Usage: collate.py compare | thirds | final"""
import glob,hashlib,json,os,sys
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'../../..'))
OUT=os.path.join(ROOT,'data/l3_meta/promise_delivery_20261022')
CAT=set('employment crime_justice health_behavior education mortality traffic_safety civic_political healthcare_use earnings_income energy_environment health_coverage poverty_safety_net births_fertility public_finance business_innovation family_demography markets_trade other transport_operations housing_construction agriculture_fisheries unemployment_ui'.split())
SRC={'purpose_clause','findings','summary','sponsor_or_agency','inferred'}
def load(reader):
    rec={}
    for f in sorted(glob.glob('%s/coded/reader%s_chunk*.json'%(OUT,reader))):
        for r in json.load(open(f))['codings']:
            if r['packet_id'] in rec:raise SystemExit('dup '+r['packet_id'])
            rec[r['packet_id']]=r
    return rec
def check(r):
    e=[]
    if r.get('promised_direction') not in (1,-1,0):e.append('dir')
    if r.get('match_to_design_outcome') not in ('same','related','different'):e.append('match')
    if r.get('sold_outcome_category') not in CAT:e.append('cat')
    if r.get('claim_source') not in SRC:e.append('src')
    if r.get('confidence') not in ('high','medium','low'):e.append('conf')
    if not isinstance(r.get('sold_outcome'),str) or not r['sold_outcome']:e.append('sold')
    return e
def kappa(a,b):
    n=len(a);labs=sorted(set(a)|set(b));po=sum(x==y for x,y in zip(a,b))/n
    pe=sum((a.count(l)/n)*(b.count(l)/n) for l in labs)
    return (po-pe)/(1-pe) if pe<1 else 1.0
def all_ids():
    ids=[]
    for f in sorted(glob.glob(OUT+'/packets/chunk*.json')):ids+=[i['packet_id'] for i in json.load(open(f))['items']]
    return ids
def anchors_pass(rec,gold):
    ok=0;bad=[]
    for k,g in gold.items():
        r=rec[k]
        if r['promised_direction']==g['direction'] and r['match_to_design_outcome']==g['match']:ok+=1
        else:bad.append(k)
    return ok,bad
def compare():
    gold=json.load(open(OUT+'/ANCHOR_GOLD.json'));ids=all_ids();A=load('A');B=load('B');rep={}
    for nm,R in (('A',A),('B',B)):
        miss=[i for i in ids if i not in R];bad={i:check(R[i]) for i in R if check(R[i])}
        ok,badl=anchors_pass(R,gold) if not miss else (None,None)
        rep[nm]=dict(n=len(R),missing=len(miss),invalid=bad,anchors_pass=ok,anchors_failed=badl)
    des=[i for i in ids if i not in gold]
    for scope,sel in (('designs',des),('all',ids)):
        for f in ('match_to_design_outcome','promised_direction'):
            rep['kappa_%s_%s'%(scope,f)]=round(kappa([A[i][f] for i in sel],[B[i][f] for i in sel]),4)
    rep['disagree_designs']=[i for i in des if A[i]['promised_direction']!=B[i]['promised_direction'] or A[i]['match_to_design_outcome']!=B[i]['match_to_design_outcome']]
    rep['disagree_anchors']=[i for i in gold if A[i]['promised_direction']!=B[i]['promised_direction'] or A[i]['match_to_design_outcome']!=B[i]['match_to_design_outcome']]
    json.dump(rep,open(OUT+'/COMPARE_AB.json','w'),indent=1);print(json.dumps({k:v for k,v in rep.items() if not k.startswith('disagree')},indent=1)[:3000]);print('disagree',len(rep['disagree_designs']),len(rep['disagree_anchors']))
def thirds():
    rep=json.load(open(OUT+'/COMPARE_AB.json'));need=set(rep['disagree_designs'])|set(rep['disagree_anchors']);items=[]
    for f in sorted(glob.glob(OUT+'/packets/chunk*.json')):items+=[i for i in json.load(open(f))['items'] if i['packet_id'] in need]
    n=max(1,-(-len(items)//29))
    for c in range(n):
        json.dump(dict(chunk=c+1,items=items[c::n]),open(OUT+'/packets/third_chunk%d.json'%(c+1),'x'),indent=1,ensure_ascii=False)
    print(len(items),'items in',n,'third chunks')
def final():
    A=load('A');B=load('B');C={}
    for f in sorted(glob.glob(OUT+'/coded/readerC_chunk*.json')):
        for r in json.load(open(f))['codings']:C[r['packet_id']]=r
    gold=json.load(open(OUT+'/ANCHOR_GOLD.json'));key=json.load(open(OUT+'/PACKET_KEY.json'))['design_ids'];fin={}
    for i in all_ids():
        row=dict(packet_id=i,design_id=key.get(i),is_anchor=i in gold)
        for f in ('match_to_design_outcome','promised_direction'):
            v=[A[i][f],B[i][f]]
            if v[0]==v[1]:row[f]=v[0]
            elif i in C:
                c=C[i][f];row[f]=c if c in v else 'unclear'
            else:row[f]='unclear'
        row['n_readers']=3 if i in C else 2
        row['claim_source']=A[i]['claim_source'] if A[i]['claim_source']==B[i]['claim_source'] else ('inferred' if 'inferred' in (A[i]['claim_source'],B[i]['claim_source']) else A[i]['claim_source'])
        row['sold_outcome_A']=A[i]['sold_outcome'];row['sold_outcome_B']=B[i]['sold_outcome'];row['sold_outcome_category_A']=A[i]['sold_outcome_category']
        fin[i]=row
    ok=sum(fin[k]['promised_direction']==g['direction'] and fin[k]['match_to_design_outcome']==g['match'] for k,g in gold.items())
    path=OUT+'/CODED_FINAL.json'
    json.dump(dict(rows=[fin[i] for i in fin],anchors_pass_adjudicated=ok),open(path,'x'),indent=1)
    files=sorted(glob.glob(OUT+'/coded/*.json'))+[path,OUT+'/PACKET_KEY.json',OUT+'/ANCHOR_GOLD.json',OUT+'/COMPARE_AB.json']+sorted(glob.glob(OUT+'/packets/*.json'))
    with open(OUT+'/CODED_FROZEN_SHA256.txt','x') as fh:
        for f in files:fh.write('%s  %s\n'%(hashlib.sha256(open(f,'rb').read()).hexdigest(),os.path.relpath(f,OUT)))
    print('frozen',len(fin),'anchors adjudicated pass',ok)
if __name__=='__main__':
    {'compare':compare,'thirds':thirds,'final':final}[sys.argv[1]]()
