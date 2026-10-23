"""Protocol v2 invented vectors and diagnostic projections; no real files."""
from __future__ import annotations
import hashlib, math
SEED=20261001
COUNTS=dict(training=1000,power=1000,heldout=100,B=99,RI=199)
OFFSETS=dict(training=1000000,evaluation=2000000,power=3000000)
GRID=(0,.1,.2,.5,1,2,3,5,10)
def wilson(successes, n):
    if not n:
        return None
    z = 1.959963984540054
    p, d = successes/n, 1+z*z/n
    center = (p+z*z/(2*n))/d
    radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0.,center-radius), min(1.,center+radius)]

def null_critical(values, check, alpha):
    """Train strict-tail cutoffs independently of evaluation; ties never reject."""
    ordered=sorted(float(v) for v in values)
    n=len(ordered)
    return ordered[math.floor(alpha*n)] if check=='pretrend_joint' else ordered[min(n-1,math.ceil((1-alpha)*n))]

def seed_for(case_id, stage, repetition):
    binding = int.from_bytes(hashlib.sha256(case_id.encode()).digest()[:8], 'big')
    return (SEED + OFFSETS[stage] + binding + repetition) % (2**64)

def invented_vectors(case_id, op, sigma, stage, repetitions, outcomes=1):
    import numpy as np
    vectors=[]
    for rep in range(repetitions):
        rng=np.random.default_rng(seed_for(case_id,stage,rep))
        # Generate each calendar step, including gaps, before selecting the mask.
        span=op['periods'][-1]-op['periods'][0]+1
        rw=np.cumsum(rng.normal(0,sigma,(outcomes,len(op['units']),span)),axis=2)
        ui,ti=op['indices']; actual=[op['periods'][j]-op['periods'][0] for j in ti]
        # Levels cancel under every contrast; retain explicit invented unit/time levels.
        levels=np.array([i*.3+t*.01 for i,t in zip(ui,actual)])
        vectors.append(rw[:,ui,actual]+levels)
    return np.array(vectors)

def project(op, vectors):
    import numpy as np
    return dict(pre=vectors@op['pre'].T,
                ri=(vectors@op['ri'].reshape(-1,len(op['keys'])).T).reshape(
                    *vectors.shape[:-1],*op['ri'].shape[:2]),
                placebo=vectors@op['placebo'])

def values(projected, response=None, magnitude=0.):
    import numpy as np
    p=projected['pre']; ri=projected['ri']; placebo=projected['placebo']
    if response is not None:
        p=p+magnitude*response['pre'];ri=ri+magnitude*response['ri'];placebo=placebo+magnitude*response['placebo']
    pvalue=(1+(np.mean(ri**2,axis=-1)>=np.mean(p**2,axis=-1)[...,None]).sum(axis=-1))/(ri.shape[-2]+1)
    # Joint main outcome conjunction: min p, max magnitude, BEFORE tail training.
    return dict(pretrend_joint=np.min(pvalue,axis=-1),
                pretrend_economic=np.max(np.abs(p),axis=(-2,-1)),
                placebo=np.max(np.abs(placebo),axis=-1))

def rejected(v, critical):
    return {k:(a<critical[k] if k=='pretrend_joint' else a>critical[k]) for k,a in v.items()}

def measured(rejections):
    import numpy as np
    n=len(next(iter(rejections.values())))
    union=np.logical_or.reduce(list(rejections.values()))
    return dict(repetitions=n,full_blocking_set_rejected=int(union.sum()),
                full_blocking_set_rejection_rate=float(np.mean(union)),
                full_blocking_set_rejection_ci95=wilson(int(union.sum()),n),
                per_check={k:dict(rejection_rate=float(a.mean()),rejected=int(a.sum()),
                                 monte_carlo_ci95=wilson(int(a.sum()),n)) for k,a in rejections.items()})
