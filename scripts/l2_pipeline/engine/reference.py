"""Independent endpoint implementation, separate from estimator weights."""
import math
def independent_reference(design, outcomes):
    """Separate endpoint-mean implementation; never calls engine contrast weights."""
    cells=[]
    for c in design['cells']:
        means=[]
        for role in ('treated','controls'):
            current=math.fsum(outcomes[u,c['t']] for u in c[role])/len(c[role])
            base=math.fsum(outcomes[u,c['base_period']] for u in c[role])/len(c[role])
            means.append(current-base)
        cells.append(dict(g=c['g'],t=c['t'],base_period=c['base_period'],post=c['post'],
                          n_treated=c['n_treated'],att=means[0]-means[1]))
    post=[c for c in cells if c['post']]
    mass=sum(c['n_treated'] for c in post)
    overall=math.fsum(c['n_treated']*c['att'] for c in post)/mass
    return dict(cells=cells,att=overall,method='independent Python math.fsum endpoint means',tolerance=1e-8)

def reference_agreement(result, reference):
    ours={(c['g'],c['t'],c['base_period']):c['att'] for c in result['group_time']}
    other={(c['g'],c['t'],c['base_period']):c['att'] for c in reference['cells']}
    differences=[abs(ours[k]-other[k]) for k in ours.keys() & other.keys()]
    overall=abs(result['overall']['att']-reference['att'])
    return dict(passed=ours.keys()==other.keys() and overall<=1e-8 and all(d<=1e-8 for d in differences),
                cell_inventory_matches=ours.keys()==other.keys(),maximum_cell_absolute_difference=max(differences,default=None),
                overall_absolute_difference=overall,tolerance=1e-8,reference=reference)
