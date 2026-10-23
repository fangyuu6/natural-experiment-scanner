import sys; sys.path.insert(0,'scripts/l3_meta/estimator_check_20261021')
import numpy as np, pandas as pd, eclib as E, sim_validate as V
from differences import ATTgt
rng=np.random.default_rng(7)
o,tau=V.make_panel(rng,het=True)
rows=[(u,t,o.y[u,t],o.D['adoption'][u] if o.D['adoption'][u] is not None else np.nan) for (u,t) in o.keys]
df=pd.DataFrame(rows,columns=['unit','time','y','cohort']).set_index(['unit','time'])
for cg in ('never_treated','not_yet_treated'):
    m=ATTgt(data=df,cohort_name='cohort')
    m.fit('y',control_group=cg,est_method='reg',progress_bar=False)
    r=m.aggregate('simple')
    print(cg); print(r)
cn=E.make_cells(o.D,'never');cy=E.make_cells(o.D,'nyt');K=len(o.keys)
print('mine never',E.agg_weights(cn,o.index,K)@o.yvec,'nyt',E.agg_weights(cy,o.index,K)@o.yvec)
