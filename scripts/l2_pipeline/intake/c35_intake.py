"""C35 extended-window intake: source/flag metadata only, preflight-capable hub entry."""
from __future__ import annotations
import argparse,collections,json
from pathlib import Path
from . import build_intake as base
from .config import IntakeConfig,REPO
from scripts.l2_pipeline.storage import sha,ensure_once
from scripts.l2_pipeline.connectors import outcome_v7

ROOT=REPO/'data/l2_pipeline/sources/research_panel/c35_20261010'
CATALOG=REPO/'data/l2_pipeline/sources/research_panel/CATALOG.json'
SCREEN=REPO/'data/l2_pipeline/sources/research_panel/v8b_20261009/ELIGIBILITY_EVIDENCE.json'
CONFIG=IntakeConfig(ROOT,351010,352010,'c35')
SUPPORT={}


def load_support(spec):
    """Never open the parsed artifact: read the separate value-free sidecar only."""
    record=spec['support_artifact'];path=REPO/record['path']
    if sha(path)!=record['sha256']:raise ValueError('C35 support metadata changed')
    data=json.loads(path.read_text())
    if data.get('outcome_values_in_sidecar') is not False or data['series_id']!=spec['series_id']:
        raise ValueError('C35 support identity/value boundary differs')
    from scripts.l2_pipeline.connectors.c35_core import SUPPORT_FIELDS
    for m in data['metadata']:
        if set(m)-SUPPORT_FIELDS:raise ValueError('Outcome values in C35 support sidecar')
    keys=[(m['unit'],m['year']) for m in data['metadata']]
    if len(keys)!=len(set(keys)):raise ValueError('Duplicate C35 support metadata')
    return {key:m for key,m in zip(keys,data['metadata'])}


def mapping(v):
    pid=v['policy_id'];name=pid.split(':')[1]
    if name=='regulation_mandatory_seatbelts':sid='fars_motor_vehicle_occupant_fatality_rate';family='road_safety';target='occupant_fatality_rate'
    elif name=='regulation_bicycle_helmets':sid='fars_pedalcyclist_fatality_rate_age5_14';family='road_safety';target='child_pedalcyclist_fatality_rate'
    elif name in {'absvot','earlvot','elecdayreg','mailreg','motorvoter'}:sid='turnout_vep_highest_office_rate';family='voting_access';target='voter_turnout'
    elif name=='death_penalty':sid='wonder_homicide_rate';family='criminal_justice';target='homicide_rate'
    elif (pid.startswith('siegel:') and name in {'dvro','dvrosurrender','mcdv','mcdvsurrender','loststolen'}) or name in {'guncontrol_stand_your_ground','w_guncontrol_stand_your_ground'}:
        sid='wonder_homicide_rate' if 'stand_your_ground' in name else 'wonder_firearm_homicide_rate';family='firearms';target='homicide_mortality' if 'stand_your_ground' in name else 'firearm_homicide_rate'
    else:return base.mapping(v)
    s=CATALOG_DATA['series'][sid]
    result=dict(family=family,target=target,connector=s['registered_connector'],series_id=sid,
        connector_exists=True,years=s['years'],population=s['population'],population_filters=s['population_filters'],
        source_url=s['source_url'],free_public=True,license=s['license'],outcome_scale='rate',frequency=s['frequency'][0],
        purpose_status='PENDING_V62_S0',local_support_certified=False,
        paths=s['paths'],source_sha256=s['source_sha256'],outcome_unit=s['units'],
        catalog_path=str(outcome_v7.CATALOG_PATH.relative_to(REPO)),catalog_sha256=sha(outcome_v7.CATALOG_PATH),
        support_path=s['support_artifact']['path'],support_sha256=s['support_artifact']['sha256'])
    for key in ('analysis_only','republication_prohibited'):
        if key in s:result[key]=s[key]
    if name=='death_penalty':result['alternative_series']='fbi_ucr_homicide_rate'
    if family=='voting_access':result['secondary_series']='turnout_vep_total_ballots_rate'
    return result


def usable(flags):
    return bool(flags.get('available') and flags.get('annual_usable') and flags.get('linkage_certified') and not flags.get('provisional'))


def supported_geometry(imported,m):
    if m['series_id'] not in SUPPORT:return base.geometry(imported,m)
    flags=SUPPORT[m['series_id']]
    years=sorted(set(m['years'])&set(range(imported['coverage']['start_year'],imported['coverage']['end_year']+1)))
    # Release years only. The policy onset may lie between even-year elections.
    states=[];treated=[];cohort_controls={};tr={r['unit']:r for r in imported['records']}
    for r in imported['records']:
        g=r['start_period'];retained=[y for y in years if y!=g]
        available=[y for y in retained if usable(flags.get((r['unit'],y),{}))]
        pre=[y for y in available if g is not None and y<g];post=[y for y in available if g is not None and y>g]
        qualified=(r['status']=='treated' and not r['primary_excluded'] and g is not None and
            bool(years) and min(years)<=g<=max(years) and len(pre)>=3 and len(post)>=2)
        if qualified:treated.append(r['unit'])
        states.append(dict(unit=r['unit'],panel_onset=g,status=r['status'],pre_n=len(pre),post_n=len(post),
            transition_drop=g if g in years else None,qualified_treated=qualified,source_excluded=r['primary_excluded'],
            reversals=r['reversal_years'],unusable_period_n=len(retained)-len(available),
            support_flags_only=True,pre_post_years=pre[-3:]+post[:2] if qualified else []))
    groups=sorted({tr[u]['start_period'] for u in treated})
    for g in groups:
        # One common support horizon for each cohort; controls must cover every
        # treated member's declared minimum pre/post periods without suppression.
        used=sorted({y for s in states if s['qualified_treated'] and s['panel_onset']==g for y in s['pre_post_years']})
        horizon=max(used);pool=[r['unit'] for r in imported['records'] if not r['primary_excluded'] and
            (r['never_control_eligible'] or (r['status']=='treated' and r['start_period']>horizon)) and
            all(usable(flags.get((r['unit'],y),{})) for y in used)]
        cohort_controls[str(g)]=dict(control_n=len(pool),units=pool,pre_post_years=used)
    dropped=[u for u in treated if cohort_controls[str(tr[u]['start_period'])]['control_n']<5]
    supported=[u for u in treated if u not in dropped];groups_supported=sorted({tr[u]['start_period'] for u in supported})
    minimum=min((cohort_controls[str(g)]['control_n'] for g in groups_supported),default=0)
    controls=[r['unit'] for r in imported['records'] if r['never_control_eligible']]
    report=dict(outcome_retained_years=years,raw_prepost_treated_n=len(treated),treated_n=len(supported),treated_states=supported,
        dropped_insufficient_controls=dropped,never_control_n=len(controls),never_controls=controls,cohorts_n=len(groups),
        supported_cohorts_n=len(groups_supported),min_cohort_controls=minimum,cohort_controls=cohort_controls,state_counts=states,
        passed=len(supported)>=6 and minimum>=5 and len(groups_supported)>=2,
        rule='C18>=6treated/2cohorts/5controls;3pre/2post on published grid, drop transition; C35 available/usable/linkage flags required for treated periods and every control horizon',
        source_geometry_only=True,suppression_or_population_support_not_certified=True,
        metadata_support_checked=True,legal_operation_clock_certified=False,outcome_values_read=0)
    if m.get('alternative_series'):
        s=CATALOG_DATA['series'][m['alternative_series']]
        alternative=supported_geometry(imported,dict(m,series_id=s['series_id'],years=s['years'],alternative_series=None))
        report['alternative_homicide_geometry']=alternative
        if not report['passed'] and alternative['passed']:
            primary=report
            m.update(series_id=s['series_id'],connector=s['registered_connector'],years=s['years'],
                population=s['population'],population_filters=s['population_filters'],source_url=s['source_url'],
                license=s['license'],paths=s['paths'],source_sha256=s['source_sha256'],outcome_unit=s['units'],
                support_path=s['support_artifact']['path'],support_sha256=s['support_artifact']['sha256'],
                alternative_series='wonder_homicide_rate',route_basis='WONDER support fails; UCR metadata support passes')
            report=dict(alternative,primary_wonder_geometry=primary,source_choice_uses_outcomes=False)
    return report


def completed_policies():
    """Read terminal registry identifiers and bound cards, never result values."""
    path=REPO/'data/l2_pipeline/registry/TERMINAL_RUNS.json';registry=json.loads(path.read_text())
    policies=set()
    for r in registry.get('runs',[])+registry.get('pre_run_stops',[]):
        did=r['design_id']
        package=r.get('package_path')
        if package and (Path(package)/'CARD.json').is_file():
            card=json.loads((Path(package)/'CARD.json').read_text())
            pid=card.get('treatment',{}).get('policy_id')
            if pid and pid.startswith(('cspp:','siegel:')):policies.add(pid)
        if did.startswith('c18_'):policies.add(did.removeprefix('c18_').replace('cspp_','cspp:',1).replace('siegel_','siegel:',1))
    return policies


def build():
    global CATALOG_DATA,SUPPORT
    CATALOG_DATA=outcome_v7.catalog(verify_sources=False);outcome_v7.register()
    SUPPORT={sid:load_support(s) for sid,s in CATALOG_DATA['series'].items() if s['adapter']=='c35_artifact'}
    bindings=[outcome_v7.CATALOG_PATH,Path(__file__),*[REPO/s['support_artifact']['path'] for s in CATALOG_DATA['series'].values() if s['adapter']=='c35_artifact']]
    base.build(CONFIG,catalog=CATALOG,screening_evidence=SCREEN,mapping_resolver=mapping,
        geometry_resolver=supported_geometry,rescreen_all=True,extra_bindings=bindings,completed_policy_ids=completed_policies())
    ensure_once(ROOT/'DISCIPLINE.json',dict(outcome_values_read=0,source='support sidecars only, no parsed artifact reads',
        outcome_association_calls=0,model_calls=0,preflight_only=True,existing_completed_policy_ids=sorted(completed_policies()),
        calibration_seed=CONFIG.calibration_seed,fresh_seed=CONFIG.audit_seed,
        turnout_primary='highest_office',turnout_secondary='total_ballots',turnout_missing_numerators_never_substituted=True,
        death_penalty_primary='wonder_homicide_rate',death_penalty_alternative='fbi_ucr_homicide_rate'))


def main(argv=None):
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('action',choices=['build','verify','dispatch']);args,rest=parser.parse_known_args(argv)
    if args.action=='build':build();return 0
    outcome_v7.register()
    if args.action=='verify':
        from .verify_intake import main as verify
        verify(IntakeConfig.from_existing(ROOT));return 0
    from .dispatch import main as dispatch
    return dispatch(['--intake-root',str(ROOT),'dispatch',*rest])

if __name__=='__main__':raise SystemExit(main())
