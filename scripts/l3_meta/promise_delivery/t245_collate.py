"""T245: gate / thirds / final / analysis. Usage: t245_collate.py gate|thirds|final|analyze"""
import glob,json,math,os,sys,hashlib
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from collate import OUT,kappa,check
T=OUT+'/T245'
def load(r):
    rec={}
    for f in sorted(glob.glob('%s/coded/reader%s_chunk*.json'%(T,r))):
        for x in json.load(open(f))['codings']:
            assert x['packet_id'] not in rec;rec[x['packet_id']]=x
    return rec
gold=json.load(open(OUT+'/ANCHOR_GOLD.json'));aud={r['packet_id']:r for r in json.load(open(OUT+'/audit/AUDIT_RESULT.json'))['table']}
key=json.load(open(T+'/PACKET_KEY_T245.json'))
ids=[i for c in (1,2) for i in [x['packet_id'] for x in json.load(open('%s/packets/chunk%d.json'%(T,c)))['items']]]
des=[i for i in ids if i not in gold]
MF,DF='match_to_design_outcome','promised_direction'
def gate():
    R={'A':load('A'),'B':load('B')};out=dict(label='amended anchor gate, exploratory (same gate as T241)',readers={})
    M=[p for p,r in aud.items() if r['status']['match']!='CONTESTED']
    D=[p for p in M if aud[p]['audited']['match']=='same' and aud[p]['status']['direction']!='CONTESTED']
    for n,r in R.items():
        assert set(r)==set(ids),n;bad={i:check(r[i]) for i in r if check(r[i])};assert not bad,bad
    kap=(round(kappa([R['A'][i][MF] for i in des],[R['B'][i][MF] for i in des]),4),round(kappa([R['A'][i][DF] for i in des],[R['B'][i][DF] for i in des]),4))
    kap_all=(round(kappa([R['A'][i][MF] for i in ids],[R['B'][i][MF] for i in ids]),4),round(kappa([R['A'][i][DF] for i in ids],[R['B'][i][DF] for i in ids]),4))
    out.update(M=len(M),D=len(D),kappa_designs_match_direction=kap,kappa_all_items=kap_all,n_designs=len(des))
    for n,r in R.items():
        mc=sum(r[p][MF]==aud[p]['audited']['match'] for p in M);dc=sum(r[p][DF]==aud[p]['audited']['direction'] for p in D)
        fs=sum(r[p][MF]=='same' and aud[p]['audited']['match']!='same' for p in M)
        nm,nd=math.ceil(0.8*len(M)-1e-9),math.ceil(0.9*len(D)-1e-9)
        full=dict(joint=sum(r[p][DF]==g['direction'] and r[p][MF]==g['match'] for p,g in gold.items()),direction_only=sum(r[p][DF]==g['direction'] for p,g in gold.items()),match_only=sum(r[p][MF]==g['match'] for p,g in gold.items()))
        ga,gb,gc,gd=mc>=nm,dc>=nd,fs<=1,min(kap)>=0.70
        out['readers'][n]=dict(match_correct=mc,match_need=nm,direction_correct=dc,direction_need=nd,false_same=fs,full30_original_gold=full,gate_match=ga,gate_direction=gb,gate_false_same=gc,gate_kappa=gd,PASS=bool(ga and gb and gc and gd),
            match_miss=[p for p in M if r[p][MF]!=aud[p]['audited']['match']],dir_miss=[p for p in D if r[p][DF]!=aud[p]['audited']['direction']])
    out['PASS_BOTH']=all(v['PASS'] for v in out['readers'].values())
    out['disagree_designs']=[i for i in des if R['A'][i][MF]!=R['B'][i][MF] or R['A'][i][DF]!=R['B'][i][DF]]
    out['disagree_anchors']=[i for i in gold if R['A'][i][MF]!=R['B'][i][MF] or R['A'][i][DF]!=R['B'][i][DF]]
    json.dump(out,open(T+'/GATE_T245.json','x'),indent=1);print(json.dumps(out,indent=1))
def thirds():
    g=json.load(open(T+'/GATE_T245.json'));need=set(g['disagree_designs'])|set(g['disagree_anchors']);items=[]
    for c in (1,2):items+=[x for x in json.load(open('%s/packets/chunk%d.json'%(T,c)))['items'] if x['packet_id'] in need]
    json.dump(dict(chunk=1,items=items),open(T+'/packets/third_chunk1.json','x'),indent=1,ensure_ascii=False);print(len(items))
def final():
    A,B=load('A'),load('B');C={}
    for f in sorted(glob.glob(T+'/coded/readerC_chunk*.json')):
        for x in json.load(open(f))['codings']:C[x['packet_id']]=x
    rows=[]
    for i in des:
        row=dict(packet_id=i,design_id=key['design_ids'][i],is_anchor=False)
        for f in (MF,DF):
            v=[A[i][f],B[i][f]]
            row[f]=v[0] if v[0]==v[1] else ((C[i][f] if C[i][f] in v else 'unclear') if i in C else 'unclear')
        row['n_readers']=3 if i in C else 2
        a,b=A[i]['claim_source'],B[i]['claim_source'];row['claim_source']=a if a==b else ('inferred' if 'inferred' in (a,b) else a)
        row['sold_outcome_A']=A[i]['sold_outcome'];row['sold_outcome_B']=B[i]['sold_outcome'];row['sold_outcome_category_A']=A[i]['sold_outcome_category'];rows.append(row)
    ok=sum(1 for p,g in gold.items() if (lambda f:f)(1) and (A[p][DF]==g['direction'] and A[p][MF]==g['match']))
    json.dump(dict(rows=rows),open(T+'/CODED_T245_NEW.json','x'),indent=1)
    old=json.load(open(OUT+'/CODED_FINAL.json'))['rows'];allr=old+rows
    json.dump(dict(rows=allr,note='T241 rows + T245 rows'),open(T+'/CODED_FINAL_T241_PLUS_T245.json','x'),indent=1)
    files=sorted(glob.glob(T+'/coded/*.json'))+sorted(glob.glob(T+'/packets/*.json'))+[T+'/CODED_T245_NEW.json',T+'/CODED_FINAL_T241_PLUS_T245.json',T+'/GATE_T245.json',T+'/PACKET_KEY_T245.json']
    with open(T+'/T245_FROZEN_SHA256.txt','x') as fh:
        for f in files:fh.write('%s  %s\n'%(hashlib.sha256(open(f,'rb').read()).hexdigest(),os.path.relpath(f,OUT)))
    print(len(rows),len(allr))
if __name__=='__main__':{'gate':gate,'thirds':thirds,'final':final}[sys.argv[1]]()
