"""T241 blind anchor audit + amended-gate scoring per HUB_RULING_20261022_PROMISE_ANCHORS.md.
Usage: audit.py   (reads audit/coded/labeler*.json, ANCHOR_GOLD.json, coded/reader{A,B}; writes audit/AUDIT_RESULT.json)"""
import glob,json,math,os,sys
sys.path.insert(0,os.path.dirname(__file__))
from collate import load,OUT,kappa,all_ids
A_DIR=OUT+'/audit'
gold=json.load(open(OUT+'/ANCHOR_GOLD.json'))
lab={}
for f in sorted(glob.glob(A_DIR+'/coded/labeler*_chunk*.json')):
    j=json.load(open(f));lab[j['reader']]={r['packet_id']:r for r in j['codings']}
assert sorted(lab)==['L1','L2','L3'] and all(len(v)==30 and set(v)==set(gold) for v in lab.values()),'labelers incomplete'
F={'direction':'promised_direction','match':'match_to_design_outcome'}
aud={};table=[]
for pid,g in gold.items():
    row=dict(packet_id=pid,gold=g,labels={k:[lab[l][pid][F[k]] for l in ('L1','L2','L3')] for k in F},status={},audited={})
    for k in F:
        ls=row['labels'][k];gv=g[k if k=='match' else 'direction']
        if sum(x==gv for x in ls)>=2:st,val='FIRM',gv
        elif len(set(ls))==1:st,val='CORRECTED',ls[0]
        else:st,val='CONTESTED',None
        row['status'][k]=st;row['audited'][k]=val
    row['purpose_sources']=[lab[l][pid].get('purpose_source') for l in ('L1','L2','L3')]
    table.append(row)
T={r['packet_id']:r for r in table}
cont={k:sum(r['status'][k]=='CONTESTED' for r in table) for k in F}
# anchors "CONTESTED on either field" count
anch_cont=sum(any(r['status'][k]=='CONTESTED' for k in F) for r in table)
uninformative=cont['match']>10 or cont['direction']>10
M=[p for p,r in T.items() if r['status']['match']!='CONTESTED']
D=[p for p in M if T[p]['audited']['match']=='same' and T[p]['status']['direction']!='CONTESTED']
Rd={'A':load('A'),'B':load('B')}
ids=[i for i in all_ids() if i not in gold]
out=dict(table=table,counts=dict(contested_match=cont['match'],contested_direction=cont['direction'],anchors_with_any_contested=anch_cont,corrected_match=sum(r['status']['match']=='CORRECTED' for r in table),corrected_direction=sum(r['status']['direction']=='CORRECTED' for r in table),M=len(M),D=len(D)),uninformative_rule_triggered=uninformative,readers={})
kap=(round(kappa([Rd['A'][i]['match_to_design_outcome'] for i in ids],[Rd['B'][i]['match_to_design_outcome'] for i in ids]),4),round(kappa([Rd['A'][i]['promised_direction'] for i in ids],[Rd['B'][i]['promised_direction'] for i in ids]),4))
out['kappa_designs_match_direction']=kap
for nm,R in Rd.items():
    mc=sum(R[p]['match_to_design_outcome']==T[p]['audited']['match'] for p in M)
    dc=sum(R[p]['promised_direction']==T[p]['audited']['direction'] for p in D)
    fs=sum(R[p]['match_to_design_outcome']=='same' and T[p]['audited']['match']!='same' for p in M)
    need_m=math.ceil(0.80*len(M)-1e-9);need_d=math.ceil(0.90*len(D)-1e-9)
    # joint count on scored items (report only): match correct and (direction correct if D)
    joint=sum(R[p]['match_to_design_outcome']==T[p]['audited']['match'] and (p not in D or R[p]['promised_direction']==T[p]['audited']['direction']) for p in M)
    ev=len(M)>=15 and len(D)>=8
    ga,gb,gc=mc>=need_m,dc>=need_d,fs<=1
    gd=kap[0]>=0.70 and kap[1]>=0.70
    full=dict(joint=sum(R[p]['promised_direction']==g['direction'] and R[p]['match_to_design_outcome']==g['match'] for p,g in gold.items()),
      direction_only=sum(R[p]['promised_direction']==g['direction'] for p,g in gold.items()),match_only=sum(R[p]['match_to_design_outcome']==g['match'] for p,g in gold.items()))
    dropped_miss=[p for p in gold if (T[p]['status']['match']=='CONTESTED' or T[p]['status']['direction']=='CONTESTED') and (R[p]['match_to_design_outcome']!=gold[p]['match'] or R[p]['promised_direction']!=gold[p]['direction'])]
    dropped_pass=[p for p in gold if (T[p]['status']['match']=='CONTESTED' or T[p]['status']['direction']=='CONTESTED') and p not in dropped_miss]
    out['readers'][nm]=dict(full30_original_gold=full,match_scored=len(M),match_correct=mc,match_need=need_m,direction_scored=len(D),direction_correct=dc,direction_need=need_d,
      false_same=fs,false_same_ids=[p for p in M if R[p]['match_to_design_outcome']=='same' and T[p]['audited']['match']!='same'],
      joint_correct_scored=joint,joint_need_ceil_0_93=math.ceil(0.93*len(M)-1e-9),
      contested_dropped_anchors_reader_misses=len(dropped_miss),contested_dropped_anchors_reader_passes=len(dropped_pass),
      evaluable=ev,gate_match=ga,gate_direction=gb,gate_false_same=gc,gate_kappa=gd,PASS=bool(ev and ga and gb and gc and gd and not uninformative),
      match_miss_ids=[p for p in M if R[p]['match_to_design_outcome']!=T[p]['audited']['match']],direction_miss_ids=[p for p in D if R[p]['promised_direction']!=T[p]['audited']['direction']])
out['PASS_BOTH']=all(v['PASS'] for v in out['readers'].values())
json.dump(out,open(A_DIR+'/AUDIT_RESULT.json','w'),indent=1)
for r in table:print(r['packet_id'],T[r['packet_id']]['gold'],r['labels'],r['status'])
print(json.dumps({k:v for k,v in out.items() if k!='table'},indent=1))
