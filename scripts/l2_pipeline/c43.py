"""Non-dispatching C43 recheck; only source coding and metadata are opened."""
from copy import deepcopy
import json
from pathlib import Path
from .agents.jobs import load_job
from .storage import ensure_once, sha, public_input
from .stages.s1_treatment import reconcile
from .c42 import exclusion_scope
from .intake.official_history import engine_records
from .intake.history_routing import validate_fresh_policy
from .v11_v12 import comparison_group, register_card
from .support import support_check, standard_annual_declaration, placebo_support
from .engine.design import build_design
from .hub import ri_geometry_feasibility

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c43'


def read(p): return json.loads(public_input(Path(p)).read_text())
def bind(p): return dict(path=str(Path(p).resolve()),sha256=sha(p))


def recheck(row):
    card=read(row['card']); jobs=sorted([load_job(Path(p)) for p in row['history_jobs']],key=lambda j:j.role)
    if [j.role for j in jobs]!=['A','B']: raise ValueError('Independent A/B required')
    values=[];evidence=[bind(row['card']),bind(ROOT/'docs/plans/l2-pipeline/HUB_RULING_V11_V12.md')]
    for j in jobs:
        c=read(Path(j.output_dir)/'COLLECTION.json')
        if (c.get('validated') is not True or c.get('mechanism_issues') or
                c['original_sha256']!=sha(j.output_path) or c['promoted_sha256']!=sha(c['promoted_path'])):
            raise ValueError('History collector binding changed')
        values.append(read(c['promoted_path']))
        evidence.extend(bind(p) for p in (j.job_path,j.output_path,c['promoted_path'],Path(j.output_dir)/'COLLECTION.json'))
    universe=jobs[0].contract['manual']['universe']
    if universe!=jobs[1].contract['manual']['universe']: raise ValueError('A/B universe differs')
    comparison=reconcile(*(v['records'] for v in values),universe)
    b={r['unit']:r for r in values[1]['records']}
    for r in comparison['accepted']:
        if r.get('best_evidence_start_year')!=b[r['unit']].get('best_evidence_start_year'):
            comparison['gaps'].append(dict(unit=r['unit'],kind='best_evidence_year_conflict'))
    cfg=exclusion_scope(dict(card['treatment'],universe=universe),comparison,values)
    excluded=set(cfg.get('excluded_units',[]))
    eligible=[r for r in comparison['accepted'] if r['unit'] not in excluded]
    policy=validate_fresh_policy(row['fresh_policy'],universe)
    for item in row.get('prior_sample_evidence',[]):
        if sha(item['path'])!=item['sha256']: raise ValueError('Prior sample binding changed')
        if not set(item['sample'])<=set(policy['excluded_units']): raise ValueError('Prior sample disjointness lost')
        evidence.append(item)
    fresh=sorted(r['unit'] for r in eligible if r['status']=='treated' and r['unit'] not in policy['excluded_units'])
    group=comparison_group(eligible)
    outcome=card['outcome'];support_path=ROOT/outcome['support_path']
    if sha(support_path)!=outcome['support_sha256']: raise ValueError('Outcome metadata hash changed')
    metadata=read(support_path)
    if metadata.get('outcome_values_in_sidecar') not in (False,0): raise ValueError('Sidecar includes outcomes')
    evidence.append(bind(support_path))
    years=outcome['period_years']
    keys={(m['unit'],int(m['year'])) for m in metadata['metadata'] if str(m.get('annual_usable'))=='1'
          and str(m.get('linkage_certified',1))=='1' and str(m.get('suppression_flag',0))=='0'}
    records=engine_records(eligible,excluded)
    analysis=deepcopy(card['analysis'])
    # S3 selects again after registered metadata exclusions, independent of y.
    support=support_check(keys,records,analysis,period_years=years,transition_year_drop=True)
    selected=support.get('comparison_group',group)
    prospective=register_card(card,selected);prospective['treatment']=cfg
    declared=standard_annual_declaration(records,analysis,years,summary=support,transition_year_drop=True)
    try:
        design=build_design(declared,[(u,t,0.,None) for u,t in support.get('analysis_mask',[])])
    except ValueError as exc:
        design=None;design_error=str(exc)
    else:design_error=None
    placebo=placebo_support(design) if design else []
    ri=ri_geometry_feasibility(declared,design) if design and support['passed'] else dict(passed=False,status='NOT_RUN_SUPPORT_FAILED',outcome_values_read=0)
    post_counts={str(g):len({c['period'] for c in support['cells'] if c['cohort']==g}) for g in sorted({c['cohort'] for c in support['cells']})}
    gates=dict(retained_treated=len([r for r in eligible if r['status']=='treated'])>=6,
        fresh_eligible=len(fresh)>=10,V3_errors_unresolved='NOT_RUN_NEW_AUDIT_REQUIRED',
        metadata_support=support['passed'],six_supported_treated=len(support.get('retained_treated',[]))>=6,
        two_supported_cohorts=support.get('cohorts',0)>=2,
        three_pre=bool(support.get('retained_treated')) and not support['issues'].get('insufficient_pre',0),
        two_post=bool(post_counts) and all(n>=2 for n in post_counts.values()),
        five_controls_each_cell=bool(support['cells']) and all(len(c['controls'])>=5 for c in support['cells']),
        RI199_RI999=ri.get('passed') is True,
        placebo=any(p['status']=='supported' for p in placebo),
        frequency_adapter=outcome.get('frequency','annual')=='annual')
    roster=dict(retained_treated=sum(r['status']=='treated' for r in eligible),
        never_controls=sum(r['status']=='never_treated' for r in eligible),
        not_yet_pool=sum(r['status'] in {'never_treated','treated','not_yet_treated'} for r in eligible),
        comparison_group=group,fresh_eligible_treated=len(fresh),fresh_units=fresh)
    value=dict(design_id=row['design_id'],roster=roster,support_comparison_group=selected,
        supported_treated=len(support.get('retained_treated',[])),supported_controls=len(support.get('comparison_controls',[])),
        post_periods_by_cohort=post_counts,gates=gates,status='PARKED',
        excluded_conflict_state_reasons=cfg['excluded_conflict_state_reasons'],certified_spell_end_periods=cfg.get('certified_spell_end_periods',{}),
        comparison=comparison,fresh_policy=policy,support=support,ri=ri,placebo=placebo,engine_error=design_error,
        evidence=evidence,metadata_only=True,outcome_values_read=0,actual_data_estimation_calls=0,dispatches=0)
    directory=OUT/'recheck'/row['design_id']
    ensure_once(directory/'CARD_S2_S4.json',prospective)
    ensure_once(directory/'RECHECK.json',value)
    return value


def main():
    rows=read(ROOT/'data/l2_pipeline/registry/ACTIVE_DESIGNS.json')['designs']
    parked={r['design_id'] for r in [json.loads(l) for l in (ROOT/'data/l2_pipeline/registry/AUTOPILOT_LEDGER.jsonl').read_text().splitlines()] if r.get('decision')=='PARK_RECHECK'}
    values=[recheck(r) for r in rows if r['design_id'] in parked]
    if len(values)!=5: raise ValueError('Expected five PARK designs')
    summary=dict(designs=values,dispatches=0,loop_restarted=False,new_downloads=[],actual_data_estimation_calls=0)
    ensure_once(OUT/'PARK_RECHECK.json',summary)
    print(json.dumps([dict(design_id=r['design_id'],roster=r['roster'],supported_treated=r['supported_treated'],supported_controls=r['supported_controls'],gates=r['gates']) for r in values],indent=2))

if __name__=='__main__':main()
