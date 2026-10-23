"""C20-ruling amendments and C21 preparation; no model or estimation calls."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from .storage import ensure_once, sha, artifact
from .contracts import StageContext, StageResult, Disposition
from .cards import load_card, validate_card
from .intake.official_history import binding

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO/'data/l2_pipeline/jobs/c21'
EVIDENCE = REPO/'data/l2_pipeline/evidence/20261009/c21'
RULING = REPO/'docs/plans/l2-pipeline/HUB_RULINGS_C20.md'
C18 = REPO/'data/l2_pipeline/sources/research_panel/c18_20261010'
DVRO = REPO/'data/l2_pipeline/jobs/c19_dvro_v21'


def read(p):
    return json.loads(Path(p).read_text())


def build_catalog(version=5):
    from .connectors.wonder import c21_series_specs
    old=Path(__file__).parent/'connectors/OUTCOME_CATALOG_V4.json'
    data=read(old)
    data.update(schema_version=version,catalog_id='first_order_outcomes_v'+str(version),
                version_lineage=dict(previous=binding(old),ruling=binding(RULING)),
                registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v'+str(version)+'.construct',
                registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v'+str(version)+'.register')
    fragment=REPO/'data/l2_pipeline/connectors_v2/wonder/parsed/C21_SERIES_FRAGMENT.json'
    data['acquisition_entrypoint']='Offline only: python -B -m scripts.l2_pipeline.connectors.wonder parse-c21; python -B -m scripts.l2_pipeline.connectors.c21_fars'
    data['series'].update(read(fragment)['series'] if fragment.is_file() else c21_series_specs())
    fars=read(EVIDENCE/'fars_catalog_fragment.json')
    data['series'].update(fars['series'])
    for sid in data['series']:
        data['series'][sid]['registered_connector']='outcome_v'+str(version)+'_'+sid
    data['targets']['intimate_partner_homicide_mortality']=dict(
        series=['wonder_firearm_homicide_deaths','wonder_firearm_homicide_deaths_female'],
        measurement='Same homicide construct over broader resident population under C20 ruling3',
        purpose_verification_required=True,legal_certification_granted=False)
    for sid in list(c21_series_specs())+list(fars['series']):
        data['targets'][sid]=dict(series=[sid],purpose_verification_required=True,
                                  legal_certification_granted=False)
    data['discipline'].update(outcome_association_calls=0,policy_outcome_association_calls=0,
        wonder_stop_state='C21 new exports parsed' if fragment.is_file() else 'C21 four new exports/receipts pending; inherited V4 exports verified')
    path=ensure_once(old.with_name('OUTCOME_CATALOG_V'+str(version)+'.json'),data)
    ensure_once(EVIDENCE/('CATALOG_V'+str(version)+'.json'),dict(catalog=binding(path),
        previous=binding(old),fars_fragment=binding(EVIDENCE/'fars_catalog_fragment.json'),
        wonder_exports_parsed=fragment.is_file(),outcome_association_calls=0))
    return path


def outcome(sid, spec, years, units, version=5):
    paths = [REPO/e['raw_path'] for e in spec.get('exports', [])]
    if spec.get('panel_path'):
        paths = [REPO/spec['panel_path']]
    return dict(connector='outcome_v'+str(version)+'_'+sid, series_id=sid, measure_id=sid,
        source_measure_id=sid, frequency='annual', years=[min(years), max(years)],
        period_years=years, units=units, population_filters=spec['population_filters'],
        source_population_filters=spec['population_filters'],
        outcome_unit=spec['units'], source_outcome_unit=spec['units'],
        paths=[str(p) for p in paths], source_sha256={str(p):sha(p) for p in paths},
        source_urls={str(p):spec['source_url'] for p in paths},
        license=spec.get('license', 'US federal government public domain; cite source'),
        measurement_refs=[dict(source_ref=spec['source_url'],locator=sid)],
        source_validation_required=True)


def amend_history(imported, check, rule):
    """Amend only the ruled dated tuple; a law ruling never certifies absence."""
    result = deepcopy(imported); unit = check['unit']
    row = next(r for r in result['records'] if r['unit'] == unit)
    before = deepcopy(row)
    operation = None
    if rule == 6:
        # Existing Utah panel is zero; rejection of this candidate cannot prove
        # absence of every other law. Keep its original uncertified history.
        row.update(candidate_law_treated=False, rejected_candidate_law='UT HB101 2021',
                   history_absence_certified=False)
    else:
        operation = '2021-07-01' if rule == 7 else '1996-01-01'
        year = int(operation[:4]); first_full = year+int(operation[5:] != '01-01')
        for cell in result['annual_rows']:
            if cell['unit'] == unit:
                cell.update(active=int(cell['year'] >= year), value=int(cell['year'] >= year),
                            c20_ruling=rule)
        row.update(status='treated', start_period=year, operational_date=operation,
                   official_operational_calendar_year=year, first_full_start_period=first_full,
                   transition_year_drop=operation[5:] != '01-01',
                   intervals=[dict(start_period=year,end_period=result['coverage']['end_year'])],
                   left_censored=year <= result['coverage']['start_year'],
                   never_control_eligible=False, history_absence_certified=False)
        # Replace affected event labels; keep all unrelated source events.
        result['events'] = [e for e in result['events'] if e['unit'] != unit]
        result['events'].append(dict(unit=unit, year=year, event='activation',
                                    prior_active=0,active=1,locator='HUB_RULINGS_C20 ruling '+str(rule)))
    result.update(schema_version='research_panel_c21_history_v1', version='c21',
                  original_full_history_certification=False, legal_certification_granted=False,
                  independent_fresh_audit_required=True,
                  ruling_amendment=dict(rule=rule, unit=unit, before=before, after=deepcopy(row),
                                        original_check=check, ruling=binding(RULING)))
    return result


def build_cards(version=5):
    from .connectors.outcome_v5 import catalog
    from .connectors.rates import prospective_card
    from .coverage import coverage_bounded_card
    data = catalog(Path(__file__).parent/('connectors/OUTCOME_CATALOG_V'+str(version)+'.json'),version); report=[]
    card_version='v23' if version==6 else 'v22'
    card_dir=ROOT/'cards_v23' if version==6 else ROOT/'cards'
    suffix='_V23' if version==6 else ''
    configs=[('dvro',DVRO/'cards/dvro.json','wonder_firearm_homicide_deaths',3)]
    for name,primary,r in [('siegel_dvrosurrender','wonder_firearm_homicide_deaths',8),
                         ('siegel_mcdv','wonder_firearm_homicide_deaths',7),
                         ('siegel_mcdvsurrender','wonder_firearm_homicide_deaths',6),
                         ('siegel_loststolen','wonder_firearm_homicide_deaths',3),
                         ('cspp_guncontrol_stand_your_ground','wonder_homicide_deaths',3),
                         ('cspp_regulation_mandatory_seatbelts','fars_motor_vehicle_occupant_fatality_rate',4),
                         ('cspp_regulation_bicycle_helmets','fars_pedalcyclist_fatality_rate_age5_14',5)]:
        configs.append((name,C18/'cards'/('c18_'+name+'.json'),primary,r))
    for short,original,primary,rule in configs:
        card=read(original); card['version']=card_version
        if short!='dvro':
            from .sources.research_panel.s1 import load_inputs
            variable,_,_=load_inputs(card['treatment'])
            catalog=read(card['treatment']['catalog_path'])
            source_ids=set(variable.get('source_ids',[variable['source_id']]))
            receipts=[r for r in catalog['files'] if r.get('source_id') in source_ids]
            card['schema_version']='1.0'
            card['sources']=[dict(source_id=r['source_id']+'_'+str(i),url=r['url'],locator=card['treatment']['policy_id'],
                license=r.get('license','unverified'),path=r['path'],sha256=r['sha256']) for i,r in enumerate(receipts)]
            card['lineage']=dict(queue_version='c21_recard',screen_version='c18_research_panel',
                                 source_text_ids=[card['treatment']['policy_id']])
            card['analysis']=read(Path(__file__).parent/'examples/design.json')['analysis']
            card['analysis'].update(min_post_periods=2,spec_status='PROSPECTIVE_DRAFT_REQUIRES_FRESH_S1')
            card['sensitivities']=[]
            card['automation']=dict(c18_intake={k:card.pop(k) for k in ('intake_status','execution_authorized','selection_rule')})
            card['population'].pop('target',None)
        a=card.setdefault('automation',{}); a.pop('coverage_window',None)
        secondary=['wonder_firearm_homicide_deaths_female','wonder_firearm_deaths'] if short in ('dvro','siegel_dvrosurrender','siegel_mcdv','siegel_mcdvsurrender') else ['wonder_firearm_homicide_deaths'] if 'stand_your' in short else ['fars_all_road_fatality_rate'] if 'seatbelt' in short else []
        if short!='dvro':
            purpose=C18/'purpose_work'/card['design_id']/'c18_20261010/PURPOSE_HUB_RULING_V62.json'
            card['first_order'].update(read(purpose)['card_patch']['first_order'])
        else:
            purpose=Path(card['automation']['c19_binding']['purpose_acceptance']['path'])
        spec=data['series'][primary]
        years=card['outcome']['period_years']
        observed=spec['years'] or list(range(1999,2025))
        years=sorted(set(years)&set(observed))
        units=card['treatment'].get('universe', [])
        if not units:
            from .sources.research_panel.core import STATES
            units=list(STATES)
        card['outcome']=outcome(primary,spec,years,units,version)
        card['outcome_scale']='rate'
        target='intimate_partner_homicide_mortality' if short in ('dvro','siegel_dvrosurrender','siegel_mcdv','siegel_mcdvsurrender') else primary
        proxy=target!=primary or 'helmet' in short or 'seatbelt' in short
        first=card['first_order']
        first.update(target_outcome=target, target_construct=target, primary_outcome=primary,
                     evidence_status='verified_by_hub', availability='available' if spec['public_available'] else 'unavailable',
                     relation='closest_proxy' if proxy else 'direct',
                     proxy_justification='Same homicide construct over all state residents; intimate-partner population dilution remains.' if target!=primary else 'Declared core road-user population measures fatal harm; wider statutory coverage creates dilution.',
                     secondary_outcomes=[])
        first.pop('proxy_reason',None)
        for sid in secondary:
            s=data['series'][sid]; fields=outcome(sid,s,years,units,version)
            first['secondary_outcomes'].append(dict(outcome=sid,role='secondary',
                predeclared_basis='HUB_RULINGS_C20 ruling '+str(rule),outcome_scale='rate',**fields))
        card['population'].update(filters=spec['population_filters'],description=spec['population'])
        a['c21_binding']=dict(original_card=binding(original),purpose_acceptance=binding(purpose),
            ruling=binding(RULING),catalog=binding(Path(__file__).parent/('connectors/OUTCOME_CATALOG_V'+str(version)+'.json')),
            rule=rule,execution_authorized=False,outcome_association_calls=0)
        if rule in (6,7,8):
            from .sources.research_panel.s1 import load_inputs
            _,imported,paths=load_inputs(card['treatment'])
            job=read(next((C18/'calibration_jobs/S1'/card['design_id']).glob('*.job.json')))
            checks=read(job['output_path'])['checks']; unit={6:'UT',7:'VA',8:'IL'}[rule]
            history=amend_history(imported,next(c for c in checks if c['unit']==unit),rule)
            history['source_bindings']=[binding(p) for p in paths.values()]+[binding(job['output_path'])]
            hp=ensure_once(ROOT/'histories'/card['design_id']/'HISTORY_C21.json',history)
            card['treatment'].update(source_type='research_panel_c21', c21_history=binding(hp),
                audit_scope='C21 ruled history; original fixed samples retained; fresh S1 required')
        if short=='dvro':
            card['treatment']['c21_s1_carry']=dict(source_card=binding(original),
                source_work_dir=str(DVRO/'work'/card['design_id']/'v21'))
        card=prospective_card(coverage_bounded_card(card))
        errors=validate_card(card)
        if errors: raise ValueError(errors)
        path=ensure_once(card_dir/(short+'.json'),card)
        report.append(dict(card=binding(path),primary=primary,secondaries=secondary,
            history=card['treatment'].get('c21_history'),s1_pass_granted=False))
    ensure_once(ROOT/('SUPERSESSION'+suffix+'.json'),dict(old_card=binding(DVRO/'cards/dvro.json'),
        old_jobs_root=str(DVRO/'s5_jobs'),status='SUPERSEDED_STOPPED_BEFORE_LOCK',
        ruling=binding(RULING),new_card=binding(card_dir/'dvro.json'),old_files_modified=False))
    ensure_once(EVIDENCE/('CARD_AMENDMENTS'+suffix+'.json'),dict(cards=report,model_calls=0,outcome_association_calls=0))
    return report


def carry_s1(ctx):
    """Transport a passed legal audit across exactly the authorized recard."""
    cfg=ctx.card.payload['treatment']['c21_s1_carry']; src=Path(cfg['source_card']['path'])
    if sha(src)!=cfg['source_card']['sha256']: raise ValueError('Original S1 card changed')
    old=read(src); current=deepcopy(ctx.card.payload['treatment']); current.pop('c21_s1_carry')
    if current!=old['treatment'] or ctx.card.mechanism!=old['mechanism'] or ctx.card.payload['transition_year_drop']!=old['transition_year_drop']:
        raise ValueError('C21 S1 carry may not change treatment or clock')
    if (ctx.card.payload['outcome']['series_id']!='wonder_firearm_homicide_deaths' or
        ctx.card.payload['outcome']['period_years']!=old['outcome']['period_years'] or
        ctx.card.payload['outcome_scale']!='rate'):
        raise ValueError('C21 S1 carry limited to ruled homicide rate/window')
    if sha(RULING)!=ctx.card.payload['automation']['c21_binding']['ruling']['sha256']:
        raise ValueError('C20 ruling changed')
    source=Path(cfg['source_work_dir']); receipt=sorted((source/'receipts/S1').glob('*.json'))[-1]
    value=read(receipt)
    if value['card_sha256']!=sha(src) or value['result']['disposition']!='ready':
        raise ValueError('C21 requires actual passed S1 receipt')
    artifacts=[]
    for item in value['result']['artifacts']:
        # C19 migration preserved original receipts; bind durable copies by
        # basename plus exact SHA, never depend on their old scratchpad paths.
        p=source/Path(item['path']).name
        if sha(p)!=item['sha256']: raise ValueError('Passed S1 artifact changed')
        dest=ctx.design_dir/p.name; dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists() and sha(dest)!=sha(p): raise ValueError('S1 carry destination changed')
        if not dest.exists(): shutil.copyfile(p,dest)
        artifacts.append(artifact(dest,item['role']))
    proof=ensure_once(ctx.design_dir/'S1_C21_CARRY.json',dict(
        source_card=binding(src),source_receipt=binding(receipt),ruling=binding(RULING),
        copied_artifacts=[a.__dict__ for a in artifacts],
        treatment_changed=False,clock_changed=False,new_audit_granted=False,
        outcome_association_calls=0,execution_authorized=False))
    return StageResult('S1',ctx.card.design_id,Disposition.READY,
        'Passed V21 legal audit retained for C20-authorized outcome-only recard',
        artifacts=tuple(artifacts+[artifact(proof,'s1_carry')]),metrics=value['result']['metrics'])


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['catalog','cards','prepare'])
    parser.add_argument('--catalog-version',type=int,choices=[5,6],default=5)
    args=parser.parse_args(argv)
    if args.action=='catalog':print(build_catalog(args.catalog_version));return 0
    if args.action=='cards': print(json.dumps(build_cards(args.catalog_version))); return 0
    from .cli import run_stage
    from .stages import s1_treatment
    from . import hub
    card_dir=ROOT/'cards_v23' if args.catalog_version==6 else ROOT/'cards'
    suffix='_V23' if args.catalog_version==6 else ''
    ctx=StageContext(load_card(card_dir/'dvro.json'),ROOT/'work',REPO)
    previous=s1_treatment.run
    s1_treatment.run=lambda c: carry_s1(c) if c.card.payload['treatment'].get('c21_s1_carry') else previous(c)
    try:
        results=[]
        for stage in ('S0','S1','S2','S3','S4'):
            result=run_stage(ctx,stage).as_dict();results.append(result)
            if result['disposition']!='ready':break
        ensure_once(EVIDENCE/('DVRO_STAGES'+suffix+'.json'),dict(results=results,model_calls=0,outcome_association_calls=0))
        if results[-1]['stage']=='S4' and results[-1]['disposition']=='ready':
            return hub.main(['prepare','S5','--design',str(ctx.card.path),'--work-dir',str(ctx.workspace),
                             '--jobs-root',str(ROOT/'s5_jobs'),'--attempt-id','c21'])
        print(json.dumps(results));return 2
    finally:s1_treatment.run=previous


if __name__=='__main__':raise SystemExit(main())
