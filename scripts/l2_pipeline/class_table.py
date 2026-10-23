"""Prospective, deterministic SHOULD closures; no outcome reads or science edits."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from .storage import encoded, sha, ensure_once

RULES = {
    'registered_point_ri': ('R01', 'SPEC.interval_path == [Webb, CT_complete_control_path, point_plus_RI_if_uncertified]'),
    'stale_contract_card': ('R02', 'SPEC.analysis.screen_expected_support scalar/list differs from primary SUPPORT'),
    'series_break': ('R03', 'integer break year in structured metadata; SUPPORT cell crossing mass computed'),
    'duplicate_sensitivity': ('R04', 'nonempty canonical SUPPORT cell sets and registered estimator operator identical to primary'),
    'FIELD_MISSING': ('R05', 'C64 FIELD_MISSING status and null fields; structured legal-status reason code'),
    'storage_only': ('R06', 'SPEC.l3_export_v2 or SPEC_ADDENDUM_C64 declares S6_after_final_freeze/S7; only deduplicate_storage operation'),
}


def table_value():
    body = dict(schema='l2_c76_should_class_table_v1', version=2, mode='ACTIVE',
        auto_close=True, tick_reads_table=True,
        rules=[dict(issue=k, rule_id=v[0], predicate=v[1], disposition='declined') for k,v in RULES.items()])
    return dict(body, sha256=hashlib.sha256(encoded(body)).hexdigest())


def validate_table(table):
    if table != table_value():
        raise ValueError('Class table version, predicates, or SHA changed')
    return table['sha256']


def read(path):
    return json.loads(Path(path).read_text())


def load_design(root):
    """Read declarations only; never RESULT, outcomes, forecasts or fits."""
    root = Path(root)
    value = {name: read(root/(name+'.json')) if (root/(name+'.json')).is_file() else {}
             for name in ('SPEC','SUPPORT','SPEC_ADDENDUM_C64','C65_EXPORT_ANNOTATIONS')}
    from .hygiene import inventory_names
    support_name,_=inventory_names(root)
    if support_name!='SUPPORT.json':value['SUPPORT']=read(root/support_name)
    breaks=set()
    for path in sorted((root/'panels').glob('*/metadata.csv')):
        with path.open() as stream:
            for row in csv.DictReader(stream):
                if row.get('series_break'):
                    breaks.add(int(row['year']))
    for item in value['C65_EXPORT_ANNOTATIONS'].get('series_break',[]):
        breaks.update(item.get('years',[]))
    value['metadata']={'break_years':sorted(breaks)}
    return value


def _cells(variant):
    cells=variant.get('cell_inventory',[])
    result=[]
    for c in cells:
        # Cohort and event-time coordinates participate in estimator grouping;
        # equal calendar cells alone do not establish an identical operator.
        if not all(k in c for k in ('t','base_period','treated','controls','post','n_treated')):
            return None
        if c['n_treated'] != len(c['treated']) or len(set(c['treated'])) != len(c['treated']):
            return None
        result.append((c['t'],c['base_period'],tuple(sorted(c['treated'])),
                       tuple(sorted(c['controls'])),c['post'],c['n_treated'],
                       json.dumps({k:c[k] for k in ('g','event_time') if k in c},sort_keys=True)))
    return sorted(result) if result and len(set(result))==len(result) else None


def break_mass(variant, year):
    cells=variant.get('cell_inventory',[])
    if _cells(variant) is None or type(year) is not int:
        raise ValueError('Complete structured SUPPORT cells and integer break year required')
    result={}
    for label, post in (('post',True),('lead',False)):
        chosen=[c for c in cells if c['post'] is post]
        total=sum(c['n_treated'] for c in chosen)
        crossing=[c for c in chosen if min(c['t'],c['base_period']) < year <= max(c['t'],c['base_period'])]
        mass=sum(c['n_treated'] for c in crossing)
        result[label]=dict(total_mass=total,crossing_mass=mass,mass_share=mass/total if total else None,
                           crossing_cells=len(crossing))
    return result


def classify(issue, design, table=None):
    """Return KNOWN(rule_id) only for the exact structured issue scope.

    Descriptions/reasons are never read. Mixed issues must list every component;
    one unsupported component makes the whole finding NOVEL.
    """
    table=table or table_value(); digest=validate_table(table)
    novel=dict(classification='NOVEL',rule_id=None,disposition='NEEDS_RULING',table_sha256=digest)
    if not isinstance(issue,dict) or issue.get('kind') not in RULES:
        return novel
    if set(issue)-{'kind','variant','year','fields','operations','components'}:
        return novel
    extra={'registered_point_ri':set(),'stale_contract_card':set(),'series_break':{'year'},
        'duplicate_sensitivity':{'variant'},'FIELD_MISSING':{'fields'},'storage_only':{'fields','operations'}}
    if set(issue)-({'kind','components'}|extra[issue['kind']]):return novel
    if issue.get('operations') and issue['kind'] != 'storage_only':
        return novel
    if issue.get('components'):
        parts=[classify(x,design,table) for x in issue['components']]
        if any(x['classification']!='KNOWN' for x in parts):return novel
    spec=design.get('SPEC',{}); variants=design.get('SUPPORT',{}).get('variants',[])
    primary=next((v for v in variants if v.get('variant')=='primary'),{})
    kind=issue['kind']; proof=None
    if kind=='registered_point_ri':
        path=spec.get('interval_path')
        if path==['Webb','CT_complete_control_path','point_plus_RI_if_uncertified']:
            proof=dict(interval_path=path)
    elif kind=='stale_contract_card':
        screen=spec.get('analysis',{}).get('screen_expected_support',{})
        actual={'comparison_group':primary.get('comparison_group'),
                'treated_n':len(primary['retained_treated']) if isinstance(primary.get('retained_treated'),list) else None,
                'cohort_years':primary.get('cohorts'),
                'window':[min(primary['period_years']),max(primary['period_years'])] if primary.get('period_years') else None}
        differences={k:dict(screen=screen[k],registered=v) for k,v in actual.items()
                     if k in screen and v is not None and screen[k]!=v}
        if differences:proof=dict(differing_fields=differences)
    elif kind=='series_break':
        year=issue.get('year')
        if type(year) is int and year in design.get('metadata',{}).get('break_years',[]):
            try:proof=dict(year=year,mass=break_mass(primary,year))
            except ValueError:pass
    elif kind=='duplicate_sensitivity':
        name=issue.get('variant'); other=next((v for v in variants if v.get('variant')==name),{})
        a,b=_cells(primary),_cells(other)
        from .hygiene import operator
        sensitivity=next((v for v in spec.get('sensitivities',[]) if v.get('name')==name),None)
        # SPEC is not a design card; compare the registered analysis patch
        # directly without passing a specification to card schema validation.
        changes=(sensitivity or {}).get('changes',{})
        same_operator=(sensitivity is not None and 'analysis' in spec and
            all(k in {'analysis','treatment','outcome','transition_year_drop'} for k in changes) and
            operator(spec)==operator(dict(spec,analysis={**spec['analysis'],**changes.get('analysis',{})})) and
            all(spec.get('outcome',{}).get(k)==v for k,v in changes.get('outcome',{}).items()) and
            not changes.get('outcome_scale') and not changes.get('population'))
        if name!='primary' and a is not None and a==b and same_operator:
            proof=dict(variant=name,cell_operator_sha256=hashlib.sha256(encoded(a)).hexdigest(),cells=len(a))
    elif kind=='FIELD_MISSING':
        add=design.get('SPEC_ADDENDUM_C64',{}); geo=add.get('geography',{})
        fields=issue.get('fields',[]); reasons=[]
        if fields and set(fields)<= {'geography','purpose_direction'}:
            for field in fields:
                if field=='geography' and geo.get('status')=='FIELD_MISSING' and geo.get('U') is None and geo.get('R') is None:
                    reasons.append('LEGAL_STATUS_UNRESOLVED')
                elif field=='purpose_direction' and add.get('purpose_direction_status')=='FIELD_MISSING' and add.get('first_order',{}).get('purpose_direction') is None:
                    reasons.append('PURPOSE_DIRECTION_UNCODED')
            if len(reasons)==len(fields):proof=dict(fields=fields,reason_codes=reasons)
    elif kind=='storage_only':
        block=spec.get('l3_export_v2') or design.get('SPEC_ADDENDUM_C64',{})
        if (issue.get('operations')==['deduplicate_storage'] and issue.get('fields')==['l3_export_v2']
                and block.get('computation_stage')=='S6_after_final_freeze' and block.get('export_stage')=='S7'):
            proof=dict(field='l3_export_v2',declaration='SPEC.l3_export_v2' if spec.get('l3_export_v2') else 'SPEC_ADDENDUM_C64',computation_stage=block['computation_stage'],export_stage='S7',science_changes=[])
    if proof is None:return novel
    if issue.get('components'):proof=dict(proof,components=parts)
    return dict(classification='KNOWN',rule_id=RULES[kind][0],disposition='declined',proof=proof,table_sha256=digest)


def enabled(root):
    path=Path(root)/'SPEC.json'
    return path.is_file() and read(path).get('c76b2',{}).get('class_table')==2


def selected(identity, denominator=5, seed='C76B2-class-table-v2'):
    """Stable pseudorandom selection, independent of estimates and findings."""
    return int(hashlib.sha256(encoded([seed,identity])).hexdigest(),16)%denominator==0


def replay_inventory(repo, inventory):
    """Hash-bound replay against historical hub decisions; never write closures."""
    repo=Path(repo);records=[]
    for item in inventory['items']:
        root=repo/item['root'];review=root/item['review']
        if sha(review)!=item['review_sha256']:raise ValueError('Replay review changed')
        finding=next(f for f in read(review)['should'] if f['id']==item['id'])
        if hashlib.sha256(encoded(finding)).hexdigest()!=item['finding_sha256']:raise ValueError('Replay finding changed')
        historical='NEW_SPEC_REQUIRED' if item.get('ruling_action')=='new_spec' else 'declined'
        if item.get('closure'):
            closure=root/item['closure']
            if sha(closure)!=item['closure_sha256']:raise ValueError('Replay closure changed')
            matches=[r for r in read(closure)['resolutions'] if r['finding_sha256']==item['finding_sha256']]
            if len(matches)!=1:raise ValueError('Replay closure finding absent')
            historical=matches[0]['status']
        else:
            if sha(repo/item['ruling']['path'])!=item['ruling']['sha256']:raise ValueError('Replay ruling changed')
        if historical!=item['expected_historical_disposition']:raise ValueError('Historical disposition differs')
        actual=classify(item['issue'],load_design(root))
        unsafe=actual['classification']=='KNOWN' and (item['expected_classification']!='KNOWN'
            or actual['rule_id']!=item['expected_rule_id'] or actual['disposition']!=historical)
        records.append(dict(root=item['root'],id=item['id'],finding_sha256=item['finding_sha256'],actual=actual,unsafe=unsafe))
    known=sum(r['actual']['classification']=='KNOWN' for r in records)
    return dict(total=len(records),known=known,novel=len(records)-known,unsafe=sum(r['unsafe'] for r in records),
        table_sha256=table_value()['sha256'],inventory_sha256=hashlib.sha256(encoded(inventory)).hexdigest(),
        records=records,outcome_values_read=False)


def accept(p,row,job,scope):
    """Close only a complete KNOWN review, validated by the existing validators."""
    from .registration import _validate_resolution, hub_signer
    from .closure_evidence import snapshot
    from .stages.s6_execute import sealed_review_evidence
    from .c54 import draft_identity
    root=p.backend.root(row)
    if not enabled(root): return False
    if any((root/n).exists() for n in ('EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json')):return False
    if (p.case_dir(row)/'EXECUTE_INTENT.json').exists():return False
    meta=read(Path(job).with_suffix('.job.json'));path=Path(meta['promote_path'])
    review=read(path);sealed=scope=='sealed_candidate'
    if scope not in ('draft_readiness','sealed_candidate'):return False
    candidate=root/('FREEZE_FINAL.json' if sealed else 'SPEC.json')
    if (review.get('must') or review.get('verdict')!='PASS' or review.get('review_scope')!=scope
            or review.get('design_id')!=row['design_id'] or review.get('candidate_sha256')!=sha(candidate)
            or meta.get('job_sha256')!=sha(job) or meta.get('design_id')!=row['design_id']):return False
    if not sealed and (root/'FREEZE_PREP.json').exists():return False
    if sealed and path.resolve()!=(root/'EXECUTION_REVIEW.json').resolve():return False
    design=load_design(root);rows=[];notes={}
    for finding in review.get('should',[]):
        result=classify(finding.get('class_issue',{}),design)
        if result['classification']!='KNOWN':return False
        digest=hashlib.sha256(encoded(finding)).hexdigest()
        key=result['rule_id']+'_'+digest[:16]
        notes[key]=dict(rule_id=result['rule_id'],proof=result['proof'],report_only=True)
        rows.append(dict(bucket='should',id=finding['id'],finding_sha256=digest,
            status='declined',reason='Owner D1=A: '+result['rule_id']+' predicate verified; retain registered science and export annotation.',
            classification=result,evidence=[]))
    if not rows:return False
    # Draft cards carry no signer before REGISTRATION.json; the standing authorization signs, as in autopilot.py and c54.py.
    signer=hub_signer(p.backend.ctx(row)) or 'hub autopilot (standing authorization 2026-10-10)'
    value=dict(schema='l2_sealed_review_resolution_v1' if sealed else 'l2_draft_resolution_v1',
        design_id=row['design_id'],review_sha256=sha(path),hub_signer=signer,
        execution_authorized=False,outcome_associations_read=False,resolutions=rows,
        class_table_sha256=table_value()['sha256'],export_annotations=notes,
        promised_export_keys=sorted(notes),auto_closed=True)
    names=[n for n in ('SPEC.json','SUPPORT.json','SUPPORT_C79.json','SPEC_ADDENDUM_C64.json','C65_EXPORT_ANNOTATIONS.json') if (root/n).is_file()]
    if sealed:
        manifest=read(candidate)
        # Every declaration consumed by the classifier must be frozen, including
        # the metadata from which break years are read.
        names += [str(x.relative_to(root)) for x in sorted((root/'panels').glob('*/metadata.csv'))]
        if any(n not in manifest['files'] for n in names):return False
        evidence=[dict(path=n,sha256=sha(root/n)) for n in names if n in manifest['files']]
        if not {'SPEC.json','SUPPORT.json'}<=set(e['path'] for e in evidence):return False
        for e in evidence:
            if manifest['files'][e['path']]['sha256']!=e['sha256']:return False
        value['candidate_sha256']=sha(candidate)
        target=root/'EXECUTION_REVIEW_RESOLUTION.json'
    else:
        attempt,review_path=draft_identity(p,row,[job])
        if review_path.resolve()!=path.resolve():return False
        evidence=[snapshot(root/n,root) for n in names]
        value.update(review_attempt=attempt,source_spec=dict(path=str(root/'SPEC.json'),sha256=sha(candidate)),
            target_spec_sha256=sha(candidate),target_unchanged=True)
        target=root/('DRAFT_RESOLUTION_'+attempt+'.json')
    for record in rows:record['evidence']=evidence
    if target.exists() and read(target)!=value:return False
    if not sealed:_validate_resolution(p.backend.ctx(row),attempt,value)
    else:
        # Validate before creating the immutable live closure. The temporary
        # view copies declarations and hashes only, never panel outcomes.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='class-validation-',dir=root) as tmp:
            view=Path(tmp)
            for name in (*names,'FREEZE_FINAL.json','EXECUTION_REVIEW.json'):
                target_copy=view/name;target_copy.parent.mkdir(parents=True,exist_ok=True)
                target_copy.write_bytes((root/name).read_bytes())
            ensure_once(view/'EXECUTION_REVIEW_RESOLUTION.json',value)
            sealed_review_evidence(view,sha(candidate),row['design_id'],signer)
    ensure_once(target,value)
    if sealed:
        sealed_review_evidence(root,sha(candidate),row['design_id'],signer)
        from .sealed_closure import accept as accept_sealed
        accept_sealed(p,row,[job])
    receipt=ensure_once(root/('CLASS_TABLE_ACCEPTED_'+sha(path)+'.json'),dict(
        schema='l2_c76_class_acceptance_v2',closure=target.name,closure_sha256=sha(target),
        review=path.name,review_sha256=sha(path),candidate_sha256=sha(candidate),
        promised_export_keys=sorted(notes),class_table_sha256=table_value()['sha256']))
    if selected([row['design_id'],row['attempt_id'],scope]):
        from .autopilot import read as aread,atomic
        queue=aread(p.registry/'HUB_QUEUE.json',dict(entries=[]))
        entry=dict(design_id=row['design_id'],attempt_id=row['attempt_id'],status='SPOTCHECK',action='spotcheck',
            blocking=False,scope=scope,reason='Seeded 1/5 automatic closure audit',
            closure_path=str(target),closure_sha256=sha(target),receipt_sha256=sha(receipt))
        if entry not in queue['entries']:
            queue['entries'].append(entry);atomic(p.registry/'HUB_QUEUE.json',queue)
    return True


def export_annotations(root, base=None):
    """Revalidate accepted closure bytes and every promised export key at S7."""
    import copy
    from .registration import _validate_resolution
    from .stages.s6_execute import sealed_review_evidence
    from types import SimpleNamespace
    root=Path(root);value=copy.deepcopy(base or {})
    notes={};accepted=set()
    frozen=root/'FREEZE_FINAL.json'
    if frozen.exists():
        for name,binding in read(frozen)['files'].items():
            if name.startswith('CLASS_TABLE_ACCEPTED_'):
                if not (root/name).is_file() or sha(root/name)!=binding['sha256']:
                    raise ValueError('Frozen class acceptance receipt missing or changed')
    for receipt_path in sorted(root.glob('CLASS_TABLE_ACCEPTED_*.json')):
        receipt=read(receipt_path)
        closure=root/receipt['closure'];review=root/receipt['review']
        if closure.parent!=root or review.parent!=root:raise ValueError('Closure reference escapes package')
        if sha(closure)!=receipt['closure_sha256'] or sha(review)!=receipt['review_sha256']:
            raise ValueError('Accepted closure/review bytes changed')
        resolution=read(closure);annotation=resolution.get('export_annotations',{})
        accepted.add(closure.name)
        promised=receipt['promised_export_keys']
        if promised!=resolution.get('promised_export_keys') or any(k not in annotation for k in promised):
            raise ValueError('Promised closure export annotation missing')
        if resolution['schema']=='l2_sealed_review_resolution_v1':
            sealed_review_evidence(root,sha(root/'FREEZE_FINAL.json'),resolution['design_id'],resolution['hub_signer'])
        else:
            # Draft acceptance receipts are part of the prospective frozen package.
            frozen=root/'FREEZE_FINAL.json'
            if frozen.exists():
                files=read(frozen)['files']
                if closure.name not in files or files[closure.name]['sha256']!=sha(closure):
                    raise ValueError('Draft closure is not bound to final candidate')
        for key,item in annotation.items():
            if key in notes and notes[key]!=item:raise ValueError('Conflicting closure annotations')
            notes[key]=item
    closures=list(root.glob('DRAFT_RESOLUTION_*.json'))+[root/'EXECUTION_REVIEW_RESOLUTION.json']
    for closure in closures:
        if not closure.is_file():continue
        resolution=read(closure)
        if resolution.get('auto_closed') and closure.name not in accepted:
            raise ValueError('Automatic closure acceptance receipt missing')
        if closure.name in accepted or not (resolution.get('export_annotations') or resolution.get('promised_export_keys')):continue
        if resolution.get('schema')=='l2_sealed_review_resolution_v1':
            sealed_review_evidence(root,sha(root/'FREEZE_FINAL.json'),resolution['design_id'],resolution['hub_signer'])
        else:
            if not frozen.exists() or read(frozen)['files'].get(closure.name,{}).get('sha256')!=sha(closure):
                raise ValueError('Draft export annotation closure was not accepted into the freeze')
        annotation=resolution.get('export_annotations',{})
        if any(k not in annotation for k in resolution.get('promised_export_keys',[])):
            raise ValueError('Promised closure export annotation missing')
        for key,item in annotation.items():
            if key in notes and notes[key]!=item:raise ValueError('Conflicting closure annotations')
            notes[key]=item
    if notes:value['accepted_closures']=notes
    return value or None

