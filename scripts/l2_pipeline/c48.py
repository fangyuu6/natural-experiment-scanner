"""C48 authorized queue application and intake; no dispatch or estimation."""
from copy import deepcopy
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc
from .storage import ensure_once,sha
from .cards import validate_card
from .population_resolution import bind
from .connectors import outcome_v9,population_v63
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c48'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULINGS_C48.md'
UNRESOLVED=('bulk10_health_ichia_immigrant_child_coverage','bulk10_health_pharmacist_contraception_fertility')
RECOLLECT=('bulk10_health_speed_limit_70plus_rural_interstate','bulk10_health_naloxone_coprescribing_mandate')
REBIND=('b1010_bac_008_per_se','bulk1010_lab_snap_bbce')


def row_for(p,did):return next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==did)


def reopen(p,row,evidence):
    stop=p.stop_record(row)
    if not stop:return
    paths=[q for q in p.case_dir(row).glob('STOP*.json') if read(q)==stop]
    if len(paths)!=1:raise ValueError('Unique retained stop required')
    ensure_once(p.case_dir(row)/'STOP_SUPERSEDED_C48.json',dict(stop=bind(paths[0]),evidence=list(evidence)+[bind(AUTH)],authority=bind(AUTH),execution_authorized=False))


def outcome(sid,units,years):
    spec=outcome_v9.catalog(verify_sources=False)['series'][sid]
    value=dict(connector='outcome_v9_'+sid,series_id=sid,measure_id=sid,frequency='annual',years=[min(years),max(years)],period_years=list(years),units=list(units),
        population_filters=deepcopy(spec['population_filters']),source_population_filters=deepcopy(spec['population_filters']),outcome_unit=spec['units'],source_outcome_unit=spec['units'],
        license=spec['license'],catalog_path=str(outcome_v9.CATALOG_PATH.relative_to(ROOT)),catalog_sha256=sha(outcome_v9.CATALOG_PATH),source_validation_required=True)
    for key in ('paths','source_sha256'): 
        if spec.get(key):value[key]=deepcopy(spec[key])
    if spec.get('support_artifact'):value.update(support_path=spec['support_artifact']['path'],support_sha256=spec['support_artifact']['sha256'])
    return value


def drinking_card(old,version,*,restore_years=False):
    from .connectors.c48_fars import SID
    from .coverage import coverage_bounded_card
    spec=outcome_v9.catalog(verify_sources=False)['series'][SID];new=deepcopy(old);new['version']=version
    new['outcome_family']='drinking_driver_crash_deaths'
    years=list(range(*[old['outcome']['years'][0],old['outcome']['years'][1]+1])) if restore_years else old['outcome']['period_years']
    new['outcome']=outcome(SID,old['outcome'].get('units',old['treatment'].get('universe',[])),years)
    new['population'].update(description=spec['population'],filters=deepcopy(spec['population_filters']))
    new['outcome_scale']='rate';new['outcome']['outcome_scale']='rate'
    first=new['first_order'];first.update(target_outcome='drinking_driver_crash_deaths',primary_outcome=SID,relation='closest_proxy',
        justification='C48 §3: alcohol-involved crash deaths are the primary construct; ACCIDENT.DRUNK_DR > 0; per100k residents.',
        proxy_justification='Same alcohol-driving crash-death construct measured across all ages and all persons killed in qualifying crashes. Broader exposure/population dilutes youth-specific and BAC-threshold effects; reporting underascertainment remains. No numeric dilution factor is inferred.')
    first['secondary_outcomes']=[dict(outcome='fars_all_road_fatality_rate',role='secondary',predeclared_basis='C48 §3 all-road deaths secondary; distinct broader crash-death construct',**{k:v for k,v in outcome('fars_all_road_fatality_rate',old['outcome'].get('units',old['treatment'].get('universe',[])),years).items() if k not in {'units','years','period_years','frequency'}})]
    first['population_resolution']=dict(rule='V6.3',construct='drinking_driver_crash_deaths',decision='accept',selected=dict(series_id=SID,filters=deepcopy(spec['population_filters']),ages=[0,120]),
        authority=bind(AUTH),catalog=bind(outcome_v9.CATALOG_PATH),population_metadata=bind(population_v63.CURRENT_PATH),
        expected_dilution=first['proxy_justification'],outcome_values_read=0,outcome_association_calls=0)
    for source in new['sources']:
        if source['source_id']=='outcome':source.update(url=spec['source_url'],locator=SID+'; '+spec['population'],license=spec['license'])
    # Remove obsolete V8 numerical-support screens and missing-age caveats from
    # this NEW version. The prior card remains the complete historical record.
    screen=new['analysis'].pop('screen_expected_support',None)
    if screen:new.setdefault('automation',{})['prior_support_screen_superseded']=dict(reason='C48 primary construct changed; S3 must compute support from new series metadata',prior_card_version=old['version'])
    intake=new.get('automation',{}).get('intake',{});intake.pop('pending_rebind',None)
    new.setdefault('automation',{}).pop('coverage_window',None)
    if restore_years:
        new['notes']=[n for n in new.get('notes',[]) if not any(t in n for t in ('Pending re-bind','Expected dilution','V8 limitation','no underage drinking or alcohol-involved crash series','uses the youth traffic-death construct'))]
        new['notes'].append('C48 §3 new primary uses all-age drinking-driver crash deaths; restored1990–99 calendar because resident denominators exist. S0 history and S3 support still required; no outcome associations inspected.')
    new=coverage_bounded_card(new)
    errors=validate_card(new)
    if errors:raise ValueError(str(errors))
    return new


def apply():
    from .agents import collect_job
    from .agents.jobs import load_job
    from .requeue import requeue
    p=Autopilot();results=[]
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('Stopped scheduler required')
        for did in RECOLLECT:
            row=row_for(p,did);action='history' if 'speed_limit' in did else 'audit'
            inventory=read(p.case_dir(row)/'jobs'/(action+'.json'));selection={};receipts=[]
            for path in inventory['jobs']:
                job=load_job(Path(path));receipt=collect_job(path)
                if not receipt.get('validated') or receipt.get('mechanism_issues'):raise ValueError('C48 re-collection did not pass')
                evidence=[bind(path),bind(Path(path).with_suffix('.job.json')),bind(job.output_path),bind(Path(job.output_dir)/'COLLECTION.json')]
                selection[path]=dict(job=path,evidence=evidence);receipts.append(receipt)
            path=ensure_once(p.case_dir(row)/'COLLECTION_SELECTION_C48.json',dict(jobs=selection,authority=bind(AUTH),rule='C48 §2 corrected V7 scope; originals accepted without new dispatch'))
            ensure_once(p.checkpoint(row,action),dict(jobs=inventory['jobs']))
            reopen(p,row,[bind(path)])
            result=ensure_once(OUT/'resolutions'/did/'RECOLLECTION.json',dict(design_id=did,action=action,receipts=receipts,selection=bind(path),dispatches=0,replacements=0,next_action=p.next_action(row)))
            results.append(bind(result));p.ledger(row,action,'RECOLLECTED_V7_C48','record',receipt=bind(result),dispatches=0)
        for did in UNRESOLVED:
            row=row_for(p,did)
            if p.done(row,'S1') is None:
                result=p.backend.perform(row,'S1',p);ensure_once(p.checkpoint(row,'S1'),result)
            overlay=p.backend.root(row)/'C48_AUDIT_PREPARATION.json'
            reopen(p,row,[bind(overlay)])
            result=ensure_once(OUT/'resolutions'/did/'V31.json',dict(design_id=did,overlay=bind(overlay),unresolved_units=read(overlay)['unresolved_units'],next_action=p.next_action(row),dispatches=0))
            results.append(bind(result));p.ledger(row,'S1','REGISTERED_V31_C48','record',receipt=bind(result))
    for did in REBIND:
        row=row_for(p,did);slot=OUT/'resolutions'/did
        existing=read(slot/'APPLICATION.json')
        if existing:results.append(bind(slot/'APPLICATION.json'));continue
        old=read(row['card']);root=p.backend.root(row);purpose=read(root/'PURPOSE_VERIFICATION.json')
        if 'bac_008' in did:
            new=drinking_card(old,'v'+str(int(old['version'][1:])+1));new['first_order'].update(target_population=purpose['target_population'],verified_target_outcome=purpose['target_outcome'],evidence_status='verified_by_hub',purpose_rule='V6.2')
            reason='C48 §3: BAC0.08 primary drinking-driver crash deaths; all-road deaths secondary. Prior A/B/fresh V3 retained.'
        else:
            from .population_resolution import choose,revised_card
            resolution=choose(old,purpose);new=revised_card(old,purpose,resolution,ROOT)
            if new['outcome']['series_id']!='acs1_snap_household_receipt_rate':raise ValueError('C48 SNAP same-construct choice differs')
            new['first_order']['purpose_rule']='V6.2'
            reason='C48 §4: household SNAP receipt measures the same participation construct; eligibility broadening targets receipt.'
        card=ensure_once(slot/'CARD.json',new)
        ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=did,prior_card_sha256=sha(row['card']),card_sha256=sha(card),decision='accept',reason=reason,
            authority=bind(AUTH),purpose_evidence=bind(root/'PURPOSE_VERIFICATION.json'),reviewed_citations=purpose['citations'],purpose_rule='V6.2',superseded_status='SUPERSEDED_C48',execution_authorized=False))
        receipt=requeue(did,card,ruling,p)
        path=ensure_once(slot/'APPLICATION.json',receipt);results.append(bind(path))
    with scheduler_lock(p.registry):
        q=read(p.registry/'HUB_QUEUE.json');count=0
        for entry in q['entries']:
            if entry['design_id'] in UNRESOLVED+RECOLLECT+REBIND and not entry['status'].startswith('SUPERSEDED_'):
                entry.update(status='SUPERSEDED_C48',prior_status=entry['status'],superseded_at=utc(),authority=bind(AUTH));count+=1
        atomic(p.registry/'HUB_QUEUE.json',q)
    return ensure_once(OUT/'QUEUE_APPLICATION.json',dict(resolutions=results,designs=list(UNRESOLVED+RECOLLECT+REBIND),dispatches=0,downloads=[],actual_data_estimation_calls=0))


def prepare_intake():
    source=ROOT/'data/l2_pipeline/intake/bulk_20261010b/cards';destination=OUT/'intake/cards_final';cards=[]
    for path in sorted(source.glob('*.json')):
        old=read(path)
        if any(x in path.stem for x in ('zero_tolerance','keg_registration')):
            new=drinking_card(old,'v2',restore_years=True)
            for record in new['sources']:
                if record.get('path') and not Path(record['path']).is_absolute():
                    record['path']=str((path.parent/record['path']).resolve())
            target=ensure_once(destination/path.name,new)
        else:target=path
        cards.append(dict(design_id=old['design_id'],original=bind(path),card=bind(target)))
    if len(cards)!=23:raise ValueError('Expected all23 cards without support pre-filtering')
    return ensure_once(OUT/'INTAKE_PREPARATION_FINAL.json',dict(cards=cards,expected_cards=23,support_prefilter=False,dispatches=0,outcome_association_calls=0))

if __name__=='__main__':
    import sys
    if sys.argv[1:] == ['apply']:print(apply())
    elif sys.argv[1:] == ['prepare-intake']:print(prepare_intake())
    else:raise SystemExit('Use apply or prepare-intake')
