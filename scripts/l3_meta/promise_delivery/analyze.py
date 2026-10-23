"""T240 join + analysis, per PREPLAN_T240_20261022.md section B. Usage: analyze.py <live_set.json> <out_tag>"""
import json,math,os,random,statistics,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from collate import load
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'../../..'))
OUT=os.path.join(ROOT,'data/l3_meta/promise_delivery_20261022')
def wilson(k,n,z=1.959964):
    if n==0:return (None,None)
    p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return (round(c-h,3),round(c+h,3))
def phi(x):return 0.5*(1+math.erf(x/math.sqrt(2)))
def bins(rows):
    out=dict(sold=0,none=0,opposite=0)
    for r in rows:out[r['bin']]+=1
    return out
def run(live_path,tag):
    live=json.load(open(live_path));byid={d['design_id']:d for d in live['designs']}
    coded=json.load(open(OUT+'/CODED_FINAL.json'))['rows'];coded=[r for r in coded if not r['is_anchor']]
    res=dict(live_pin=live_path,tag=tag)
    cnt=dict(coded=len(coded),coded_in_pin=sum(r['design_id'] in byid for r in coded),pin_designs=len(byid),
      unclear_match=sum(r['match_to_design_outcome']=='unclear' for r in coded),unclear_direction=sum(r['promised_direction']=='unclear' for r in coded))
    matched=[r for r in coded if r['match_to_design_outcome']=='same'];cnt['matched_same']=len(matched)
    directed=[r for r in matched if r['promised_direction'] in (1,-1)];cnt['directed']=len(directed)
    noninf=[r for r in directed if r['claim_source']!='inferred'];cnt['directed_non_inferred']=len(noninf)
    def unit(rows):
        u=[]
        for r in rows:
            d=byid.get(r['design_id'])
            if not d or d['status']!='estimated':continue
            po=d['primary_outcomes'][0];e=po.get('estimate');c=po.get('noise_cutoff');sd=po.get('noise_sd')
            if e is None or c is None:continue
            s=r['promised_direction'];m=s*e
            b='none' if abs(e)<=c else ('sold' if m>0 else 'opposite')
            chance=2*(1-phi(c/sd)) if sd else None
            u.append(dict(design_id=r['design_id'],domain=d['domain'],bin=b,ratio=abs(e)/c if c else None,chance_beyond=chance,epoch='retrospective'))
        return u
    cnt['estimated_with_noise_line_main']=len(unit(noninf));cnt['estimated_with_noise_line_incl_inferred']=len(unit(directed))
    A=load('A');B=load('B')
    agree=[r for r in noninf if A[r['packet_id']]['match_to_design_outcome']=='same' and B[r['packet_id']]['match_to_design_outcome']=='same']
    moved=[r for r in coded if {A[r['packet_id']]['match_to_design_outcome'],B[r['packet_id']]['match_to_design_outcome']}=={'same','related'} and r['promised_direction'] in (1,-1) and r['claim_source']!='inferred']
    moved=[r for r in noninf]+[r for r in moved if r['match_to_design_outcome']!='same']
    cnt['sens_AB_agree_same']=len(agree);cnt['sens_disputes_moved_rows']=len(moved)
    cnt['coded_prospective_not_evaluable']=sum(1 for r in coded if byid.get(r['design_id'],{}).get('status')!='estimated');cnt['coded_retrospective_estimated']=sum(1 for r in coded if byid.get(r['design_id'],{}).get('status')=='estimated')
    res['counts']=cnt
    for name,rows in (('main',noninf),('incl_inferred',directed),('sens_AB_agree_same',agree),('sens_disputes_moved',moved)):
        u=unit(rows);n=len(u);b=bins(u);section=dict(n=n,bins=b)
        section['wilson95']={k:wilson(v,n) for k,v in b.items()}
        ch=[x['chance_beyond'] for x in u if x['chance_beyond'] is not None]
        if ch:
            m=statistics.mean(ch);section['chance_reference_share']=dict(sold=round(m/2,3),opposite=round(m/2,3),none=round(1-m,3),n=len(ch))
        doms={}
        for x in u:doms.setdefault(x['domain'],[]).append(x)
        rng=random.Random(20261022);labels=[x['domain'] for x in u];shown={}
        flags=[1 if x['bin']=='sold' else 0 for x in u]
        sizes={d:len(v) for d,v in doms.items()};shuf={d:[] for d in sizes if sizes[d]>=8}
        if shuf:
            for _ in range(5000):
                perm=labels[:];rng.shuffle(perm)
                tot={d:[0,0] for d in shuf}
                for l,f in zip(perm,flags):
                    if l in tot:tot[l][0]+=f;tot[l][1]+=1
                for d in shuf:shuf[d].append(tot[d][0]/tot[d][1] if tot[d][1] else 0)
        for d,v in sorted(doms.items()):
            bb=bins(v);row=dict(n=len(v))
            if len(v)>=8:
                k=bb['sold'];sh=k/len(v);s=sorted(shuf[d]);lo,hi=s[int(0.025*5000)],s[int(0.975*5000)-1]
                row.update(bins=bb,share_sold=round(sh,3),wilson95_sold=wilson(k,len(v)),shuffle_band_2_5_97_5=(round(lo,3),round(hi,3)),outside_band=bool(sh<lo or sh>hi),
                    median_abs_est_over_noise_line=round(statistics.median(x['ratio'] for x in v),2))
            else:row['note']='count only (fewer than 8 tagged designs)'
            shown[d]=row
        section['by_domain']=shown;res[name]=section
    res['units_main']=unit(noninf)
    res['note']='amended anchor gate, exploratory (HUB_RULING_20261022_PROMISE_ANCHORS.md)'
    p=OUT+'/RESULTS_PROMISE_DELIVERY_%s.json'%tag
    json.dump(res,open(p,'x'),indent=1);print(json.dumps({k:v for k,v in res.items() if k!='units_main'},indent=1))
if __name__=='__main__':run(sys.argv[1],sys.argv[2])
