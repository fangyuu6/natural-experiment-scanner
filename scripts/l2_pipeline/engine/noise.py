"""Diagnostic relabeling reference in original outcome units, never a CI."""
import math,statistics
from .ri import require_feasible
def quantile(values,p):
    values=sorted(values); pos=(len(values)-1)*p; lo=int(pos); hi=min(lo+1,len(values)-1)
    return values[lo]+(pos-lo)*(values[hi]-values[lo])

def noise_summary(reference, att):
    values=reference['overall']; n=len(values)
    feasibility=reference['feasibility']
    require_feasible(feasibility)
    if reference['draws']!=999 or feasibility['requested_draws']!=999 or n!=feasibility['feasible_draws']:
        raise ValueError('Real noise-floor requires 999 fixed draws and matching feasible inventory')
    cutoff=sorted(abs(v) for v in values)[math.ceil(.95*n)-1]
    return dict(draws=n,feasibility=feasibility,permutation_draws=values,sample_sd=statistics.stdev(values),
        p_two_sided=(1+sum(abs(v)>=abs(att) for v in values))/(n+1),
        central_quantiles=[quantile(values,.025),quantile(values,.975)],absolute_cutoff=cutoff,
        strictly_exceeds_cutoff=abs(att)>cutoff,draw_sha256=reference['draw_sha256'],
        quantiles_are_confidence_interval=False,interpretation='diagnostic relabeling reference; not randomized adoption')
