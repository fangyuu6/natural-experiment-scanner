import numpy as np, pandas as pd, json, itertools
D='data/l3_meta/new_angles_20261021/'
O='/path/to/scratch/scratch/T256/check/'
r=pd.read_csv(D+'inputs/design_roster.csv');f=pd.read_csv(D+'states_decide/mapping/FINAL_codes.csv')
m=r.merge(f[['design_id','direction_final']],on='design_id')
m['dg']=m.domain.replace({'education':'other'})
nz=m[m.direction_final!=0].reset_index(drop=True)
a=(np.sign(nz.overall_att)*nz.direction_final).values.astype(int)
def S(fam,a):
    ag=pairs=0; per={}
    for k in pd.unique(fam):
        idx=np.where(fam==k)[0]
        if len(idx)<2: continue
        n=len(idx)*(len(idx)-1)//2
        s=sum(a[i]==a[j] for i,j in itertools.combinations(idx,2))
        per[k]=(len(idx),n,s); ag+=s; pairs+=n
    return (ag/pairs if pairs else np.nan),pairs,per
fam=nz.policy_family.values
obs,pairs,per=S(fam,a)
nfam=len(per)
rng=np.random.default_rng(20261022+7)
def shuf(strat):
    out=[]
    for _ in range(2000):
        f2=fam.copy()
        if strat:
            for g in nz.dg.unique():
                ix=np.where(nz.dg.values==g)[0]; f2[ix]=fam[rng.permutation(ix)]
        else: f2=fam[rng.permutation(len(fam))]
        out.append(S(f2,a)[0])
    return np.array(out)
res={}
for nm,st in [('within_domain',True),('unrestricted',False)]:
    n=shuf(st); res[nm]=dict(mean=float(np.nanmean(n)),p95=float(np.nanpercentile(n,95)),pct=float((n<obs).mean()*100),diff=float(obs-np.nanmean(n)),pattern=bool(obs>np.nanpercentile(n,95) and obs-np.nanmean(n)>=0.15))
loo={}
for k in per:
    keep=fam!=k; loo[k]=S(fam[keep],a[keep])[0]
print('n_nz',len(nz),'families',nfam,'pairs',pairs,'S',obs)
print('per',per);print(res);print('loo',loo)
json.dump(dict(n_nz=len(nz),families=nfam,pairs=pairs,S=obs,per={k:list(map(int,v)) for k,v in per.items()},res=res,loo=loo),open(O+'check_q5.json','w'),indent=1)
