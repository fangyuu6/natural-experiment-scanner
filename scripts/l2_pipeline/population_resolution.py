"""V6.3 outcome-blind mechanical series selection and versioned C46 requeue."""
from copy import deepcopy
from pathlib import Path
from fractions import Fraction
from .storage import sha,ensure_once
from .autopilot import read
from .connectors import outcome_v8,outcome_v9,outcome_v10,outcome_v11,population_v63

AUTHORITY = Path(__file__).resolve().parents[2]/'docs/plans/l2-pipeline/HUB_RULING_V6_3.md'


def bind(path):
    return dict(path=str(Path(path).resolve()),sha256=sha(path))


def purpose_evidence(pilot,row):
    """Retain verified purpose across an unfrozen metadata/card revision."""
    path=pilot.backend.root(row)/'PURPOSE_VERIFICATION.json'
    if path.exists():
        purpose=read(path)
        if purpose.get('card_sha256')!=row['card_sha256']:raise ValueError('Purpose card binding changed')
        return path,purpose
    pointer=row.get('purpose_ruling')
    if not pointer:
        parked=read(pilot.case_dir(row)/'PARKED.json',{})
        if parked.get('ruling'):pointer=dict(path=parked['ruling'],sha256=parked['ruling_sha256'])
    if not pointer or sha(pointer['path'])!=pointer['sha256']:raise ValueError('Retained purpose ruling missing/changed')
    prior=read(pointer['path'])
    if (prior.get('card_sha256')!=row['card_sha256'] or prior.get('purpose_rule')!='V6.3' or
        prior.get('decision') not in {'accept','park'}):raise ValueError('Retained purpose ruling card differs')
    evidence=prior['purpose_evidence']
    if sha(evidence['path'])!=evidence['sha256']:raise ValueError('Retained purpose evidence changed')
    return Path(evidence['path']),read(evidence['path'])


def series_break_metadata(spec):
    if spec.get('series_breaks'):return spec['series_breaks']
    pointer=spec.get('support_artifact')
    if not pointer:return spec.get('limitations',[])
    path=Path(__file__).resolve().parents[2]/pointer['path']
    if sha(path)!=pointer['sha256']:raise ValueError('Series-break support metadata changed')
    data=read(path)
    if data.get('outcome_values_in_sidecar') is not False:raise ValueError('Value-free support metadata required')
    breaks=sorted({(r['year'],str(r['series_break'])) for r in data['metadata'] if r.get('series_break')})
    return [dict(year=y,note=note,support_sha256=pointer['sha256']) for y,note in breaks]


def choose(card,purpose,source=None,metadata=None):
    if source is None or metadata is None:source,metadata=population_v63.current_catalog()
    if purpose.get('status')!='VERIFIED':
        from .c42 import Park  # a ValueError subclass: callers that catch ValueError (Gate 0, prescreen) behave as before
        raise Park('NEEDS_RULING: unresolved construct question',dict(kind='PURPOSE_UNRESOLVED',outcome_values_read=0))
    target=population_v63.target_metadata(purpose['target_outcome'],family=card['policy_family'],meta=metadata,construct=purpose.get('target_construct'))
    if target is None and purpose.get('target_construct') and purpose['target_outcome']!='unintended_pregnancy_incidence':
        construct=purpose['target_construct']
        known={s['construct'] for s in metadata['series'].values()}|{t['construct'] for t in metadata['targets']}
        if construct in known:
            target=next((t for t in metadata['targets'] if t.get('family')==card['policy_family'] and t['construct']==construct),None)
            preferred={t['preferred_series'] for t in metadata['targets'] if t['construct']==construct and t.get('preferred_series')}
            target=target or dict(construct=construct,ages=[0,120],restrictions={},rule='Evidence-based structured purpose construct; catalog age support, no empirical subgroup weights',**(dict(preferred_series=next(iter(preferred))) if len(preferred)==1 else {}))
    if target is None:
        from .c42 import Park
        raise Park('NEEDS_RULING: genuinely new construct question: '+purpose['target_outcome'],
                   dict(kind='PURPOSE_NEW_CONSTRUCT',target_outcome=purpose['target_outcome'],outcome_values_read=0))
    # The instrument-specific §3 ordering binds even when a verifier reports
    # the canonical births identifier rather than the earlier purpose alias.
    target=next((t for t in metadata['targets'] if t.get('family')==card['policy_family'] and t['construct']==target['construct']),target)
    eligible=purpose.get('target_population_metadata',target)
    lo,hi=eligible['ages'];restrictions=eligible.get('restrictions',{})
    candidates=[];narrowness={}
    for sid,semantic in metadata['series'].items():
        spec=source['series'].get(sid)
        if semantic['construct']!=target['construct'] or not spec or not spec['public_available']:continue
        for variant in semantic.get('variants',[semantic]):
            a,b=variant['ages'];other=variant.get('restrictions',{})
            # An unobserved subgroup share is never invented. Only series with
            # no additional subgroup restrictions or matching restrictions can
            # rank on declared age support. Different measurement units (jobs
            # versus persons) have distinct constructs in the metadata.
            if any(restrictions.get(k)!=v for k,v in other.items()):continue
            overlap=max(0,min(hi,b)-max(lo,a)+1)
            if not overlap and sid!=target.get('preferred_series'):continue
            filters=variant['filters']
            if not set(spec.get('required_filters',[]))<=filters.keys():continue
            # At equal age coverage/width, a matching additional demographic
            # restriction narrows the measurement population while retaining
            # the eligible group. No unobserved subgroup weights are assigned.
            narrowness[(sid,str(sorted(filters.items())))]=len(other)
            candidates.append(dict(series_id=sid,filters=filters,ages=[a,b],coverage_numerator=overlap,coverage_denominator=hi-lo+1,
                population_width=b-a+1,role=semantic.get('role'),supported_periods=len(spec.get('years',[])),supported_years=list(spec.get('years',[])),series_breaks=series_break_metadata(spec),comparison_basis='declared eligible age-support overlap; no empirical population weights or outcome values'))
    # V6.3 §3 fixes the pharmacist ordering explicitly, independent of realized
    # births, coverage years, data availability beyond catalog public status,
    # support diagnostics, or estimated associations.
    if target.get('preferred_series'):
        candidates=[v for v in candidates if v['series_id']==target['preferred_series']]
    if target.get('preferred_role'):
        preferred=[v for v in candidates if v['role']==target['preferred_role']]
        if preferred:candidates=preferred
        else:
            fallback=target['fallback'];candidates=[v for v in candidates if v['series_id']==fallback['series_id'] and v['filters']==fallback['filters']]
    candidates.sort(key=lambda v:(-Fraction(v['coverage_numerator'],v['coverage_denominator']),v['population_width'],-narrowness[(v['series_id'],str(sorted(v['filters'].items())))],-v['supported_periods']))
    if len(candidates)>1:
        def rank(v):return (Fraction(v['coverage_numerator'],v['coverage_denominator']),-v['population_width'],narrowness[(v['series_id'],str(sorted(v['filters'].items())))],v['supported_periods'])
        if rank(candidates[0])==rank(candidates[1]):raise ValueError('NEEDS_RULING: equal population and supported-window series tie')
    selected=candidates[0] if candidates else None
    reason=('V6.3: same-construct series with largest declared eligible-population coverage, then narrower population, then longest supported window (C63); '+target['rule']) if selected else ('V6.3 PARK: no same-construct public series for '+target['construct']+' in CURRENT catalog; broader deaths of a different kind or a downstream mechanism cannot substitute.')
    if selected and target.get('equivalence_citation'):
        reason=target['equivalence_citation']+': hub-approved aggregate series; subgroup dilution retained, no numeric dilution factor inferred'
    return dict(rule='V6.3',construct=target['construct'],eligible_scope=deepcopy(eligible),selected=selected,candidates=candidates,
        decision='accept' if selected else 'park',reason=reason,equivalence_evidence=target.get('equivalence_evidence',[]),equivalence_citation=target.get('equivalence_citation'),secondary_series=target.get('secondary_series',[]),outcome_values_read=0,outcome_association_calls=0)


def revised_card(card,purpose,resolution,repo):
    from .cards import validate_card
    from .agents.purpose import purpose_basis
    source,meta=population_v63.current_catalog()
    current=population_v63.catalog_module(source['schema_version'])
    prefix='outcome_v'+str(source['schema_version'])+'_'
    new=deepcopy(card)
    import re
    match=re.fullmatch(r'v(\d+)',card['version'])
    new['version']='v'+str(int(match[1])+1) if match else card['version']+'_v63'
    first=new['first_order']
    new['outcome_family']=resolution['construct']
    first.update(target_outcome=resolution['construct'],verified_target_outcome=purpose['target_outcome'],target_population=purpose['target_population'],
        purpose_rule='V6.3',evidence_status='verified_by_hub',population_resolution=deepcopy(resolution))
    basis=purpose_basis(purpose['citations']);first.update(purpose_basis=basis,purpose_route='statute' if basis=='statute_purpose' else 'established_literature',
        statute_refs=[{k:r[k] for k in ['source_ref','locator','supports']} for r in purpose['citations'] if r['source_type']=='statute'],
        literature_refs=[{k:r[k] for k in ['source_ref','title','locator','supports']} for r in purpose['citations'] if r['source_type']=='paper'])
    first['population_resolution'].update(catalog=bind(current.CATALOG_PATH),population_metadata=bind(population_v63.CURRENT_PATH),authority=bind(AUTHORITY))
    if resolution['selected']:
        selected=resolution['selected'];sid=selected['series_id'];spec=source['series'][sid]
        old=new['outcome'];filters=selected['filters'];years=spec['years']
        # Rebind the primary declaration, removing all old-series files,
        # variables, support sidecars and source population/scale options.
        new['outcome']=dict(connector=prefix+sid,series_id=sid,measure_id=sid,frequency=spec['frequency'][0],years=[min(years),max(years)],period_years=years,
            units=list(old.get('units',new['treatment'].get('universe',[]))),population_filters=filters,source_population_filters=filters,outcome_unit=spec['units'],source_outcome_unit=spec['units'],
            license=spec['license'],catalog_path=str(current.CATALOG_PATH.relative_to(repo)),catalog_sha256=sha(current.CATALOG_PATH),source_validation_required=True)
        new.pop('outcome_scale',None)
        if spec.get('paths'):
            new['outcome'].update(paths=spec['paths'],source_sha256=spec['source_sha256'])
        elif spec.get('exports'):
            paths=[str((repo/e['raw_path']).resolve()) for e in spec['exports']]
            new['outcome'].update(paths=paths,source_sha256={str((repo/e['raw_path']).resolve()):e['sha256'] for e in spec['exports']})
        if spec.get('support_artifact'):
            new['outcome'].update(support_path=spec['support_artifact']['path'],support_sha256=spec['support_artifact']['sha256'])
        if spec.get('analysis_only'):
            new['outcome'].update(analysis_only=True,republication_prohibited=True)
        # A catalog revision may promote a previously registered secondary.
        # Its old declaration cannot remain both secondary and primary.
        if 'secondary_outcomes' in first:
            first['secondary_outcomes']=[s for s in first['secondary_outcomes'] if s.get('outcome')!=sid and s.get('measure_id')!=sid]
        if resolution.get('secondary_series'):
            first['secondary_outcomes']=[dict(outcome=secondary,role='secondary',predeclared_basis=resolution['equivalence_citation'],
                **secondary_declaration(secondary,source,current,prefix,repo)) for secondary in resolution['secondary_series']]
        new['population'].update(description=spec['population'],filters=filters)
        first.update(primary_outcome=sid,relation='closest_proxy',availability='available',
            proxy_justification='Same construct over the catalog population; expected dilution because only the legally eligible and exposed population is directly affected; the aggregate includes other ages or eligibility statuses, and statutory exceptions remain. No numeric dilution factor is inferred.',
            justification=resolution['reason'])
        first['population_resolution']['expected_dilution']=first['proxy_justification']
        for record in new['sources']:
            if record['source_id']=='outcome':record.update(url=spec['source_url'],locator=sid+'; '+spec['population'],license=spec['license'])
        new.setdefault('automation',{}).pop('coverage_window',None)
        from .coverage import coverage_bounded_card
        current.register();new=coverage_bounded_card(new)
    errors=validate_card(new)
    if errors:raise ValueError(str(errors))
    return new


def secondary_declaration(sid,source,current,prefix,repo):
    spec=source['series'][sid]
    return dict(connector=prefix+sid,series_id=sid,measure_id=sid,population_filters=deepcopy(spec['population_filters']),
        source_population_filters=deepcopy(spec['population_filters']),outcome_unit=spec['units'],source_outcome_unit=spec['units'],
        license=spec['license'],catalog_path=str(current.CATALOG_PATH.relative_to(repo)),catalog_sha256=sha(current.CATALOG_PATH),source_validation_required=True,
        paths=deepcopy(spec['paths']),source_sha256=deepcopy(spec['source_sha256']),support_path=spec['support_artifact']['path'],support_sha256=spec['support_artifact']['sha256'])


def rebase_sources(card,old_path):
    new=deepcopy(card)
    for record in new['sources']:
        if record.get('path') and not Path(record['path']).is_absolute():
            record['path']=str((Path(old_path).parent/record['path']).resolve())
    return new


def validate_ruling(pilot,old,new,decision):
    """Recompute the mechanical decision before publishing the active row."""
    evidence=decision['purpose_evidence']
    if sha(evidence['path'])!=evidence['sha256']:raise ValueError('Purpose evidence changed')
    if Path(decision['authority']['path']).resolve()!=AUTHORITY.resolve():raise ValueError('V6.3 authority differs')
    retained,purpose=purpose_evidence(pilot,old);previous=read(old['card'])
    if bind(retained)!=evidence:raise ValueError('V6.3 purpose evidence binding differs')
    resolution=choose(previous,purpose)
    from .connectors.rates import prospective_card
    expected=prospective_card(rebase_sources(revised_card(previous,purpose,resolution,pilot.repo),old['card']))
    if resolution!=decision.get('population_resolution') or new!=expected:
        raise ValueError('Mechanical population choice/card differs')
    if decision['decision']!=resolution['decision']:raise ValueError('Mechanical decision differs')


def apply(pilot,row,purpose=None,*,scheduler_owned=False,output=None,superseded_status=None):
    from .requeue import requeue
    evidence,retained=purpose_evidence(pilot,row)
    purpose=purpose or retained
    if purpose!=retained:raise ValueError('Mechanical purpose differs from retained evidence')
    old=read(row['card']);resolution=choose(old,purpose)
    new=rebase_sources(revised_card(old,purpose,resolution,pilot.repo),row['card'])
    out=output or pilot.registry.parent/'autopilot/population_resolutions'/row['design_id']/new['version']
    card=ensure_once(out/'CARD.json',new)
    ruling=ensure_once(out/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=row['design_id'],prior_card_sha256=sha(row['card']),card_sha256=sha(card),
        decision=resolution['decision'],reason=resolution['reason'],authority=bind(AUTHORITY),purpose_evidence=bind(evidence),population_resolution=resolution,
        purpose_rule='V6.3',superseded_status=superseded_status or ('SUPERSEDED_C50' if 'HUB_RULINGS_C50.md' in (resolution.get('equivalence_citation') or '') else 'SUPERSEDED_C49' if resolution.get('equivalence_citation') or source_version(new)==10 else 'SUPERSEDED_C47'),execution_authorized=False))
    # A transport intent lets C46 requeue reopen a live, unfrozen card without
    # routing a population-only issue into HUB_QUEUE or creating a hub STOP.
    # A parked design already carries its reopen intent (PARKED.json); a marker left by
    # an earlier failed attempt keeps its bytes and cannot block a later catalog's
    # re-evaluation (YIELD_FIXES bug b: the 14 Gate0 catalog attempts of 2026-10-12).
    intent=pilot.case_dir(row)/'MECHANICAL_REQUEUE.json'
    if not ((pilot.case_dir(row)/'PARKED.json').exists() and intent.exists()):
        ensure_once(intent,dict(card=bind(card),ruling=bind(ruling)))
    receipt=requeue(row['design_id'],card,ruling,pilot,scheduler_owned=scheduler_owned)
    return dict(requeued=True,receipt=receipt,outcome_values_read=0,actual_data_estimation_calls=0)


def source_version(card):
    import re
    match=re.match(r'outcome_v(\d+)_',card['outcome'].get('connector',''))
    return int(match[1]) if match else 9


def precheck(pilot,row,purpose=None):
    """Called after purpose collection and again before S0 opens any data."""
    card=read(row['card']);first=card.get('first_order',{});selection=first.get('population_resolution')
    if selection and 'population_metadata' in selection:
        for key in ['catalog','population_metadata','authority']:
            pointer=selection[key]
            if sha(pointer['path'])!=pointer['sha256']:raise ValueError('V6.3 selection metadata changed')
        for pointer in selection.get('equivalence_evidence',[]):
            if sha(pointer['path'])!=pointer['sha256']:raise ValueError('Construct equivalence evidence changed')
        # Intake authors may preselect a catalog series before the independent
        # purpose job. Hashes bind that proposal; they do not verify its purpose.
        if first.get('purpose_rule')=='V6.3' and first.get('evidence_status')=='verified_by_hub':
            return None
    # Historical explicit hub rulings remain bound, including the C46 cases.
    if row.get('purpose_ruling'):return None
    purpose=purpose or read(pilot.backend.root(row)/'PURPOSE_VERIFICATION.json')
    if not purpose:return None
    if selection and (first.get('purpose_rule')!='V6.3' or first.get('evidence_status')!='verified_by_hub' or 'population_metadata' not in selection):
        return apply(pilot,row,purpose,scheduler_owned=True)
    if (purpose.get('target_outcome')!=first.get('target_outcome') or
        purpose.get('target_population')!=first.get('target_population',card['population'].get('target',card['population']['description'])) or
        purpose.get('target_population')!=card['population']['description']):
        return apply(pilot,row,purpose,scheduler_owned=True)
    return None
