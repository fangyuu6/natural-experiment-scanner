import numpy as np, pandas as pd
B='data/l3_meta/new_angles_20261021/inputs/'
r=pd.read_csv(B+'design_roster.csv'); pk=pd.read_csv(B+'policy_keys.csv')
r['dg']=r.domain.replace({'education':'other'})
d=r[r.noise_sd.notna()].copy(); d['bn']=d.beyond_noise.astype(str)=='True'
d['std']=d.overall_att.abs()/d.noise_sd
cs=pd.read_csv(B+'cohort_states.csv')
nt=cs.groupby('design_id').n_states.sum()  # placeholder
print(len(d), d.dg.value_counts().to_dict())
print('dropped (no noise_sd):',r[r.noise_sd.isna()].design_id.tolist())
def gaps(dg,bn):
    out={}
    for g in dg.unique():
        m=dg==g; out[g]=bn[m].mean()-bn[~m].mean()
    return out
doms=sorted(d.dg.unique()); dg=d.dg.values; bn=d.bn.values.astype(float)
obs=gaps(pd.Series(dg),pd.Series(bn))
rng=np.random.default_rng(20261022+7); N=2000
src=d.source_group.values
def shuf(strat):
    res={g:[] for g in doms}
    for _ in range(N):
        p=dg.copy()
        if strat:
            for s in np.unique(src):
                ix=np.where(src==s)[0]; p[ix]=rng.permutation(dg[ix])
        else: p=rng.permutation(dg)
        for g in doms:
            m=p==g; res[g].append(bn[m].mean()-bn[~m].mean())
    return {g:np.array(v) for g,v in res.items()}
for strat in (True,False):
    S=shuf(strat); print('STRATIFIED' if strat else 'UNSTRATIFIED')
    for g in doms:
        m=dg==g; o=obs[g]; s=S[g]
        print(f"{g:15s} n={m.sum():2d} bn={int(bn[m].sum())} share={bn[m].mean():.4f} gap={o*100:+.2f}pt  strict>={np.mean(s>o):.4f} >=:{np.mean(s>=o):.4f}  <strict:{np.mean(s<o):.4f} <=:{np.mean(s<=o):.4f} p97.5={np.percentile(s,97.5)*100:+.2f} p2.5={np.percentile(s,2.5)*100:+.2f}")
print(d.groupby('dg').agg(n=('std','size'),med_sd=('noise_sd','median'),med_std=('std','median')))
from scipy.stats import spearmanr
sh=d.groupby('dg').bn.mean(); ms=d.groupby('dg').noise_sd.median()
print('spearman share vs noise', spearmanr(sh,ms))
print(ms.rank())
