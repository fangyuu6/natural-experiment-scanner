import numpy as np, pandas as pd, json
from scipy.stats import binomtest
D='data/l3_meta/new_angles_20261021/'
O='/path/to/scratch/scratch/T256/check/'
r=pd.read_csv(D+'inputs/design_roster.csv');f=pd.read_csv(D+'states_decide/mapping/FINAL_codes.csv')
m=r.merge(f[['design_id','direction_final']],on='design_id').merge(pd.read_csv(D+'inputs/policy_keys.csv')[['design_id','policy_key']],on='design_id')
m['dg']=m.domain.replace({'education':'other'})
gt=pd.read_csv(D+'inputs/group_time_export.csv')
cs=pd.read_csv(D+'inputs/cohort_states.csv',dtype={'states':str,'onset_years':str})
sy=pd.read_csv('data/l3_meta/covariates/state_year_v2.csv',dtype={'fips':str})
INC={(s,y):v for s,y,v in zip(sy.state,sy.year,sy.ln_pcpi_real_2024usd)}
URB={(s,y):v for s,y,v in zip(sy.state,sy.year,sy.urban_share)}
def cohort_x(row,var,mode):
    if not isinstance(row.states,str) or not row.states: return np.nan
    sts=row.states.split('|'); ons=row.onset_years.split('|') if isinstance(row.onset_years,str) else []
    vals=[]
    for i,s in enumerate(sts):
        o=ons[i] if i<len(ons) else ''
        if o in('','None','nan'): continue   # unknown onset year dropped
        yr=int(row.g)-1 if mode=='g1' else int(o)-1 if mode=='on1' else int(o)
        v=var.get((s,yr))
        if v is not None and np.isfinite(v): vals.append(v)
    return np.mean(vals) if vals else np.nan
def build(primary=True,mode='g1'):
    out=[]
    for _,d in m.iterrows():
        if primary and (d.direction_final==0 or not np.isfinite(d.noise_sd)): continue
        sc=d.noise_sd; sg=d.direction_final
        g=gt[gt.design_id==d.design_id]; c=cs[(cs.design_id==d.design_id)&(cs.matched==1)]
        y=[];w=[];xi=[];xu=[]
        for _,row in c.iterrows():
            gg=g[(g.g==row.g)&(g.event_time.between(0,3))]
            if len(gg)!=4 or not np.isfinite(gg.att).all(): continue
            xa=cohort_x(row,INC,mode); xb=cohort_x(row,URB,mode)
            y.append(gg.att.mean()*sg/sc); w.append(row.n_states); xi.append(xa); xu.append(xb)
        out.append(dict(id=d.design_id,law=d.policy_key,dom=d.dg,y=np.array(y),w=np.array(w,float),xi=np.array(xi),xu=np.array(xu)))
    return out
def halves(x):
    out=np.zeros(len(x),int); idx=np.where(np.isfinite(x))[0]
    o=idx[np.argsort(x[idx],kind='mergesort')]; h=len(o)//2
    out[o[:h]]=-1; out[o[len(o)-h:]]=1; return out
def elig(d,key):
    x=d[key]; ok=np.isfinite(x)&np.isfinite(d['y'])
    return ok
def per_design(d,key):
    ok=elig(d,key)
    if ok.sum()<4: return None
    y=d['y'][ok];w=d['w'][ok];x=d[key][ok]; lab=halves(x)
    return y,w,lab
def fe(items,lab_override=None):
    np_=dp=nm=dm=0;n=0
    for i,(y,w,lab) in enumerate(items):
        if lab_override is not None: lab=lab_override[i]
        if not((lab==1).any() and (lab==-1).any()): continue
        yy=y-np.sum(y*w)/w.sum(); p=lab==1;q=lab==-1
        np_+=np.sum(yy[p]*w[p]);dp+=w[p].sum();nm+=np.sum(yy[q]*w[q]);dm+=w[q].sum();n+=1
    return np_/dp-nm/dm
def run(designs,key,rng,nperm=2000):
    its=[];keep=[]
    for d in designs:
        t=per_design(d,key)
        if t is not None: its.append(t);keep.append(d)
    Dd=[]
    for y,w,lab in its:
        Dd.append(y[lab==1].mean()-y[lab==-1].mean())   # unweighted mean of y
    Dd=np.array(Dd); pos=int((Dd>0).sum()); n=len(Dd); neg=int((Dd<0).sum())
    share=pos/n; p=binomtest(pos,pos+neg,0.5).pvalue if pos+neg>0 else np.nan
    obs=fe(its)
    null=[]
    for _ in range(nperm):
        lo=[]
        for y,w,lab in its:
            pm=rng.permutation(len(y)); lo.append(lab[pm])
        null.append(fe(its,lo))
    null=np.array(null)
    return dict(n=n,pos=pos,neg=neg,share=share,p=float(p),fe=float(obs),lo=float(np.percentile(null,2.5)),hi=float(np.percentile(null,97.5)),
                nmeans=float(Dd.mean())),keep,its
rng=np.random.default_rng(20261022+7)
res={}
for mode in ['g1','on1','on']:
  designs=build(True,mode)
  for key in ['xi','xu']:
    R,keep,its=run(designs,key,rng); res[f'{key}_{mode}']=R; print(key,mode,R)
    if mode=='g1':
        # one vote per law
        laws={}
        for d,(y,w,lab) in zip(keep,its): laws.setdefault(d['law'],[]).append(y[lab==1].mean()-y[lab==-1].mean())
        lv=np.array([np.mean(v) for v in laws.values()]);R['law_votes']=[int((lv>0).sum()),int((lv<0).sum())]
        for dom in sorted(set(d['dom'] for d in keep)):
            sub=[d for d in keep if d['dom']!=dom]
            R2,_,_=run(sub,key,rng,500); R.setdefault('lodo',{})[dom]=dict(share=R2['share'],p=R2['p'],fe=R2['fe'])
        print('  law votes',R['law_votes'],'lodo',R['lodo'])
json.dump(res,open(O+'check_q6.json','w'),indent=1,default=float)
