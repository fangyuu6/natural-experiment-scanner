import json,numpy as np,pandas as pd
from scipy.stats import binomtest
B='data/l3_meta/new_angles_20261021/'
r=pd.read_csv(B+'inputs/design_roster.csv'); f=pd.read_csv(B+'states_decide/mapping/FINAL_codes.csv')[['design_id','direction_final']]
pk=pd.read_csv(B+'inputs/policy_keys.csv')[['design_id','policy_key']]
r=r.merge(f,on='design_id').merge(pk,on='design_id'); r['dg']=r.domain.replace({'education':'other'})
live=json.load(open('data/l3_meta/refresh/20261022_r2/live/l2_set_live.json')); L={x['design_id']:x for x in live['designs']}
r['pe']=r.design_id.map(lambda i:float(L[i]['point_estimate']))
assert np.abs(r.pe-r.overall_att).max()<1e-9
nz=r[r.direction_final!=0].copy(); print('nonzero',len(nz),'zero',(r.direction_final==0).sum())
nz['hit']=(np.sign(nz.overall_att)==nz.direction_final).astype(int); nz['bn']=nz.beyond_noise.astype(str)=='True'
nz['nbn']=nz.beyond_noise.isna()
print('beyond_noise NaN among nonzero',nz.nbn.sum())
bn=nz[nz.bn]
print('right&bn',bn.hit.sum(),'wrong&bn',(1-bn.hit).sum(),'not dist',len(nz)-len(bn),'(of which NaN',nz.nbn.sum(),')')
Ha=nz.hit.mean(); Hb=bn.hit.mean()
print('H_all',nz.hit.sum(),len(nz),Ha,binomtest(int(nz.hit.sum()),len(nz),.5).pvalue)
print('H_bn',bn.hit.sum(),len(bn),Hb,binomtest(int(bn.hit.sum()),len(bn),.5).pvalue)
# sign flip null
rng=np.random.default_rng(20261022+7); N=2000
n=len(nz); k=nz.hit.sum()
flips=rng.integers(0,2,(N,n)); # flipping direction => hit flips
hs=np.where(flips==1,1-nz.hit.values,nz.hit.values).mean(1)
dev=abs(Ha-.5); print('signflip two-sided p',(np.abs(hs-.5)>=dev-1e-12).mean())
# LODO
for g in sorted(nz.dg.unique()):
    s=nz[nz.dg!=g]; print('LODO drop',g,len(s),round(s.hit.mean(),4), 'bn:',round(s[s.bn].hit.mean(),4),int(s.bn.sum()))
# precision terciles
nz['prec']=nz.noise_sd/nz.overall_att.abs()
q=nz.dropna(subset=['prec']); q=q.assign(t=pd.qcut(q.prec,3,labels=False))
print(q.groupby('t').agg(n=('hit','size'),H=('hit','mean'),lo=('prec','min'),hi=('prec','max')))
# domains >=8
print(nz.groupby('dg').hit.agg(['size','mean']))
# one vote per law
law=nz.groupby('policy_key').hit.mean(); print('laws',len(law),'maj hit share',(law>.5).sum(),(law==.5).sum(),(law<.5).sum(),'mean',law.mean())
lb=bn.groupby('policy_key').hit.mean(); print('bn laws',len(lb),(lb>.5).sum(),(lb<.5).sum(),(lb==.5).sum())
print('MISS LIST'); print(bn[bn.hit==0][['design_id','dg','overall_att','direction_final']].to_string())
# placebo sign rate
rates=[];allh=0;alln=0
for _,x in nz[nz.source_group=='autopilot_estimate'].iterrows():
    j=json.load(open(L[x.design_id]['source']['path'])); pp=j['l3_export_v2']['placebo_profiles']; h=[]
    for p in pp:
        rows=[(c['att'],c['n_treated']) for c in p['group_time'] if c['event_time']>=0 and c['att'] is not None and c['n_treated']]
        if not rows: continue
        a=sum(u*w for u,w in rows)/sum(w for u,w in rows)
        if a!=0: h.append(int(np.sign(a)==x.direction_final))
    rates.append(np.mean(h)); allh+=sum(h); alln+=len(h)
print('placebo designs',len(rates),'mean rate',np.mean(rates),'pooled',allh/alln)
