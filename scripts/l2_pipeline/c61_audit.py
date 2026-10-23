"""Outcome-free audits for C61; no execution or mutation of study packages."""
import csv,json,os
from pathlib import Path
from .autopilot import Autopilot,read
from .storage import ensure_once,sha
BASE=Path('data/l2_pipeline/autopilot/c61')


def study_roots():
    roots=set()
    for directory,dirs,files in os.walk('data/l2_pipeline'):
        dirs[:]=[d for d in dirs if d not in {'code_snapshots','raw','.git','node_modules','predictions','forecast_snapshot'}]
        if 'SUPPORT.json' in files and 'SPEC.json' in files:roots.add(Path(directory))
    return roots


def mask_audit():
    p=Autopilot();active={p.backend.root(r).resolve():r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs']}
    report=[]
    for root in sorted(study_roots()):
        finished=(root/'TERMINAL.json').exists() or (root/'RESULT.json').exists() or (root/'results.json').exists()
        if not finished and root.resolve() not in active:continue
        support=read(root/'SUPPORT.json');panels=read(root/'PANELS.json').get('variants',[])
        result=read(root/'RESULT.json') or read(root/'results.json') or {}
        rows=[]
        for s in support.get('variants',[]):
            name=s['variant'];meta=root/'panels'/name/'metadata.csv'
            pinfo=next((r for r in panels if r['variant']==name),{})
            if pinfo.get('metadata_relative'):meta=root/pinfo['metadata_relative']
            if not meta.exists() and name.startswith('secondary_'):meta=root/'secondary_panels'/name.removeprefix('secondary_')/'metadata.csv'
            if not meta.exists():
                rows.append(dict(variant=name,status='NO_METADATA',supported=s.get('support_pass',s.get('passed'))));continue
            flagged=set();old_eligible=set()
            with meta.open() as f:
                for r in csv.DictReader(f):
                    key=r['unit'],int(r['year'])
                    if any(str(r.get(k,'0')).strip().lower() not in {'0','false',''} for k in ['suppressed','unreliable','suppression_flag']):flagged.add(key)
                    if all(r.get(k,'1')=='1' for k in ['available','annual_usable','linkage_certified']) and r.get('suppression_flag','0')=='0':old_eligible.add(key)
            mask=set(map(tuple,s.get('analysis_mask',[])));used=set();post=set()
            for cell in s.get('cell_inventory',[]):
                keys={(u,t) for u in cell.get('treated',[])+cell.get('controls',[]) for t in (cell['t'],cell['base_period'])}
                used |= keys
                if cell['post']:post |= keys
            rr=result.get('registered_results',{}).get(name,result.get('omissions',{}).get(name,{}))
            estimated=rr.get('status')=='ESTIMATED'
            if not result and finished:
                # Some package results live in a run subdirectory. Bind the actual
                # returned inventory before claiming any cell entered an estimate.
                for rp in root.glob('results/*/*.json'):
                    v=read(rp);rr=v.get('registered_results',{}).get(name,{})
                    if rr.get('status')=='ESTIMATED':estimated=True
            rows.append(dict(variant=name,metadata_path=str(meta),metadata_sha256=sha(meta),
                flagged_cells=len(flagged),flagged_old_eligible=len(flagged&old_eligible),
                flagged_analysis_mask=len(flagged&mask),flagged_in_supported_cells=len(flagged&used),
                flagged_in_post_contrasts=len(flagged&post),estimated=estimated,
                flagged_entered_estimate=len(flagged&used) if estimated else 0,
                affected_keys=[list(k) for k in sorted(flagged&used)]))
        spec=read(root/'SPEC.json')
        report.append(dict(design_id=spec.get('design_id'),version=spec.get('version'),root=str(root),
            finished=finished,active=root.resolve() in active,terminal=(read(root/'TERMINAL.json') or {}).get('status'),variants=rows))
    for root,r in active.items():
        if not any(Path(x['root']).resolve()==root for x in report):
            report.append(dict(design_id=r['design_id'],root=str(root),active=True,finished=False,variants=[],status='NO_S3_METADATA_YET'))
    value=dict(schema='l2_c61_s03_impact_v1',studies=report,all_active_count=len(active),
        finished_count=sum(r['finished'] for r in report),actual_data_estimation_calls=0,results_modified=False)
    ensure_once(BASE/'S03_IMPACT.json',value);return value


def timing_audit():
    from .variants import registered_cards
    from .registered import numerical_declaration
    from .engine.design import build_design
    from .engine.estimator import contrast_weights
    root=Path('data/l2_pipeline/autopilot/work/b1010b_zero_tolerance_under21/v6')
    card=read(root/'SCIENTIFIC_CARD.json') or read(root/'CARD.json')
    spec=read(root/'SPEC.json');card={**card,**{k:spec[k] for k in ('analysis','treatment','outcome','sensitivities')}}
    cards=registered_cards(card);support=read(root/'SUPPORT.json');ops={};details={}
    for name in ['primary','start_minus_one','start_plus_one']:
        s=next(s for s in support['variants'] if s['variant']==name)
        d=numerical_declaration(cards[name],s,read(root/'TREATMENT.json'),name)
        design=build_design(d,[(u,t,0.,None) for u,t in s['analysis_mask']])
        cells=[c for c in design['cells'] if c['post']];mass=sum(c['n_treated'] for c in cells);w={}
        for c in cells:
            for k,v in contrast_weights(c).items():w[k]=w.get(k,0)+c['n_treated']/mass*v
        ops[name]=w
        details[name]=dict(post_cells=len(cells),cohorts=len(design['groups']),mask_cells=len(design['mask']),
            operators=[[u,t,w[u,t]] for u,t in sorted(w)])
    same=ops['primary']==ops['start_minus_one']
    value=dict(design_id=spec['design_id'],spec_sha256=sha(root/'SPEC.json'),variants=details,
        start_minus_one_identical_for_every_outcome_vector=same,
        interpretation='Registered standard annual start_shift changes cohort labels while operative_period and transition-year exclusion retain actual operation. Negative label lands on excluded year; all ATT coefficients are identical. This row is not evidence of robustness to earlier exposure.',
        full_year_adapter_is_different_registration=True,actual_data_estimation_calls=0,outcome_values_parsed=0,
        decision='BY_CONSTRUCTION_MUST_CLOSED' if same else 'REQUIRES_INVESTIGATION')
    ensure_once(BASE/'ZERO_TOLERANCE_TIMING.json',value);return value

if __name__=='__main__':
    a=mask_audit();b=timing_audit();print(dict(studies=len(a['studies']),finished=a['finished_count'],timing=b['decision']))
