"""C54 evidence-bound closures and implemented generic bug migrations.

No LLM calls, code generation, calibration, outcome fits or science revisions.
Unknown findings remain open verbatim. Adding a repair handler requires code QA.
"""
from copy import deepcopy
from pathlib import Path
from .storage import ensure_once, sha
from .population_resolution import bind, rebase_sources

AUTH_REL='docs/plans/l2-pipeline/HUB_RULING_C54_CLOSURES.md'
FROZEN=('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json',
        'TERMINAL.json','FAILURE.json','EXECUTION_ONCE.json')
DEFERRED={
 'The preparation acceptance report should handle unavailable candidate width ratios when Webb fails algebraically and CT remains certifiable.':
    ('nonblocking_preparation_status_report_failure','Defer report-only null width formatting to the next code version; registered certification, selected intervals and estimates are unchanged.'),
 'Update the retained treatment records clock_note to distinguish the operation-year start_period from audited_first_full_start_period.':
    ('nonblocking_documentation_inconsistency','Defer stale clock_note correction to the next code version; recorded operation/full-year fields and the registered support masks are unchanged.'),
 'Make the pre-signature full-outcome dry-load exception explicit in the approval contract, or require an appropriate approval before numeric loading.':
    ('approval_boundary_gap','Defer approval-contract documentation to the next code version; dry-load performs validation without outcome associations; registered estimate, sample and gates are unchanged.'),
}
METADATA_BUG='The bound metadata CSV column order is incompatible with the registered loader, preventing all registered results from being reported.'
GROUP_BUG='The metadata-selected comparison group is written into SPEC but is not propagated into the retained scientific execution card. The candidate therefore fails the registered card/SPEC identity check and cannot report its primary or companion estimates.'


def draft_identity(p,row,jobs):
    from .autopilot import read
    if len(jobs)!=1:raise ValueError('A single design-bound draft review is required')
    meta=read(Path(jobs[0]).with_suffix('.job.json'));root=p.backend.root(row)
    attempt=meta['contract']['attempt_id'];path=Path(meta['promote_path'])
    if (meta.get('design_id')!=row['design_id'] or meta.get('design_version')!=read(row['card'])['version']
        or path!=root/('REVIEW_DRAFT_'+attempt+'.json')
        or sha(jobs[0])!=meta['job_sha256']
        or meta['contract']['candidate_sha256']!=sha(root/'SPEC.json')):
        raise ValueError('Draft job identity or candidate binding differs')
    if path.exists():
        review=read(path)
        if review.get('candidate_sha256')!=sha(root/'SPEC.json') or review.get('design_id')!=row['design_id']:
            raise ValueError('Draft review identity/candidate differs')
    return attempt,path


def valid_hub_closure(p, row, job):
    """Honor complete hash-bound hub evidence without rewriting it or dispatching."""
    from .autopilot import read
    from .registration import _validate_resolution
    try:
        ctx = p.backend.ctx(row)
        if any((ctx.design_dir/n).exists() for n in FROZEN): return False
        attempt, _ = draft_identity(p, row, [job])
        path = ctx.design_dir/('DRAFT_RESOLUTION_'+attempt+'.json')
        if not path.is_file(): return False
        _validate_resolution(ctx, attempt, read(path))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def close_should(p,row,job):
    from .autopilot import read
    from .registration import _review, _validate_resolution, finding_sha256, hub_signer
    from .closure_evidence import snapshot
    meta=read(Path(job).with_suffix('.job.json'));review=read(meta['promote_path'])
    if review.get('must') or review.get('verdict')!='PASS':return False
    if any(f.get('description') not in DEFERRED or f.get('impact')!=DEFERRED[f['description']][0] for f in review.get('should',[])):
        return False
    ctx=p.backend.ctx(row);root=ctx.design_dir
    if any((root/n).exists() for n in FROZEN):return False
    attempt,path=draft_identity(p,row,[job])
    resolutions=[]
    for finding in review.get('should',[]):
        policy=DEFERRED.get(finding.get('description'))
        if not policy or finding.get('impact')!=policy[0]:return False
        resolutions.append(dict(bucket='should',id=finding['id'],finding_sha256=finding_sha256(finding),
            finding_verbatim=finding,status='declined',reason=policy[1],
            evidence=[snapshot(path,root),snapshot(root/'SPEC.json',root),snapshot(p.repo/AUTH_REL,root)]))
    if not resolutions:return True
    value=dict(schema='l2_draft_resolution_v1',design_id=row['design_id'],review_attempt=attempt,
        review_sha256=sha(path),source_spec=bind(root/'SPEC.json'),target_spec_sha256=sha(root/'SPEC.json'),
        target_unchanged=True,hub_signer=hub_signer(ctx) or 'hub autopilot (standing authorization 2026-10-10)',
        execution_authorized=False,outcome_associations_read=False,resolutions=resolutions)
    _validate_resolution(ctx,attempt,value)
    closure=ensure_once(root/('DRAFT_RESOLUTION_'+attempt+'.json'),value)
    _review(ctx,attempt)
    p.ledger(row,'draft','SHOULD_DEFERRED_C54','c54',review=bind(path),closure=bind(closure),
             findings=review['should'],reasons=[r['reason'] for r in resolutions],target_unchanged=True)
    return True


def supersede_stop(p,row,evidence):
    from .autopilot import read,atomic,utc
    stop=p.stop_record(row)
    if not stop:return
    root=p.backend.root(row)
    if any((root/n).exists() for n in FROZEN):raise ValueError('C54 cannot supersede frozen or consumed design')
    slot=p.case_dir(row)
    candidates=[path for path in slot.glob('STOP*.json') if not path.name.startswith('STOP_SUPERSEDED') and read(path)==stop]
    if len(candidates)!=1:raise ValueError('C54 requires unique retained stop')
    marker=ensure_once(slot/'STOP_SUPERSEDED_C54.json',dict(stop=bind(candidates[0]),
        evidence=[*evidence,bind(p.repo/AUTH_REL)],execution_authorized=False))
    queue=read(p.registry/'HUB_QUEUE.json',dict(entries=[]))
    for entry in queue['entries']:
        if (entry['design_id'],entry['attempt_id'])==(row['design_id'],row['attempt_id']) and not entry.get('status','').startswith('SUPERSEDED_'):
            entry.update(prior_status=entry['status'],status='SUPERSEDED_C54',superseded_at=utc(),resolution=bind(marker))
    atomic(p.registry/'HUB_QUEUE.json',queue)
    p.ledger(row,'resume','SUPERSEDED_C54','c54',stop=bind(candidates[0]),resolution=bind(marker))


def implementation_findings(p,row,review):
    """Classify only implemented repairs with a value-free design-specific proof."""
    from .autopilot import read
    from .engine.panels import metadata_keys
    from .v11_v12 import register_card
    root=p.backend.root(row);bugs=[];remaining=[]
    for f in review.get('must',[]):
        if f.get('description')==METADATA_BUG:
            panels=read(root/'PANELS.json')['variants'];bad=[]
            for v in panels:
                folder=root/'panels'/v['variant'];path=folder/'metadata.csv'
                if sha(path)!=v['metadata_sha256']:raise ValueError('Metadata binding changed')
                with path.open() as handle:header=handle.readline().strip().split(',')
                if header[:2]!=['unit','year']:
                    bad.append(dict(variant=v['variant'],metadata=bind(path),key_count=len(metadata_keys(path))))
            if bad:bugs.append(dict(kind='metadata_named_columns',finding=f,proof=bad));continue
        if f.get('description')==GROUP_BUG:
            spec=read(root/'SPEC.json');card=dict(p.backend.ctx(row).card.payload)
            primary=next(s for s in read(root/'SUPPORT.json')['variants'] if s['variant']=='primary')
            group=primary.get('comparison_group');normalized=register_card(card,group)
            if (group==spec.get('comparison_group') and card['analysis']!=spec['analysis']
                and normalized['analysis']==spec['analysis'] and normalized['treatment']==spec['treatment']):
                bugs.append(dict(kind='scientific_card_registered_group',finding=f,proof=dict(
                    support=bind(root/'SUPPORT.json'),spec=bind(root/'SPEC.json'),registered_group=group,
                    intake_group=card['analysis']['controls'],normalized_fields_match_spec=True)));continue
        remaining.append(f)
    return bugs,remaining


def validate_implementation_ruling(p,decision,card_path):
    """Live migration may change version/lineage only, never registered science."""
    from .autopilot import read
    if decision.get('authority')!=bind(p.repo/AUTH_REL) or decision.get('execution_authorized') is not False:
        raise ValueError('C54 implementation authority missing')
    row=next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==decision['design_id'])
    prior=rebase_sources(read(row['card']),row['card']);new=read(card_path)
    expected=deepcopy(prior);expected['version']='v'+str(int(prior['version'][1:])+1)
    expected.setdefault('automation',{})['c54_implementation_revision']=dict(authority=bind(p.repo/AUTH_REL),
        prior_card=bind(row['card']),science_unchanged=True,execution_authorized=False)
    if expected!=new:raise ValueError('C54 implementation repair would change science')
    if decision.get('superseded_status')!='SUPERSEDED_C54':raise ValueError('C54 status required')
    jobs=read(p.case_dir(row)/'jobs/draft.json')['jobs'];_,path=draft_identity(p,row,jobs)
    bugs,_=implementation_findings(p,row,read(path))
    if not bugs or decision.get('implementation_findings')!=bugs:raise ValueError('C54 implemented bug proof missing')


def rebuild(p,row,bugs,remaining,scheduler_owned=False):
    from .autopilot import read
    from .requeue import requeue
    from .c42 import Park
    root=p.backend.root(row)
    if any((root/n).exists() for n in FROZEN):raise ValueError('C54 cannot rebuild frozen or consumed design')
    slot=p.registry.parent/'autopilot/c54/resolutions'/row['design_id']/row['attempt_id']
    existing=read(slot/'APPLICATION.json')
    if existing:return existing
    card=rebase_sources(read(row['card']),row['card']);card['version']='v'+str(int(card['version'][1:])+1)
    card.setdefault('automation',{})['c54_implementation_revision']=dict(authority=bind(p.repo/AUTH_REL),
        prior_card=bind(row['card']),science_unchanged=True,execution_authorized=False)
    path=ensure_once(slot/'CARD.json',card)
    ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=row['design_id'],
        card_sha256=sha(path),prior_card_sha256=sha(row['card']),decision='accept',purpose_rule='V6.2',
        reason='C54 generic implemented repair; unchanged registered science; fresh draft required',
        authority=bind(p.repo/AUTH_REL),superseded_status='SUPERSEDED_C54',implementation_rule='C54 §2',impact_tags=['draft_load'],
        implementation_findings=bugs,execution_authorized=False))
    receipt=requeue(row['design_id'],path,ruling,p,scheduler_owned=scheduler_owned);new=receipt['new_row']
    stages=[];failure=None
    for action in ('S0','S1','S2','S3','S4'):
        if p.done(new,action):continue
        try:
            value=p.backend.perform(new,action,p);ensure_once(p.checkpoint(new,action),value);stages.append(action)
        except Park as exc:
            failure=dict(action=action,reason=str(exc),metrics=exc.metrics)
            ensure_once(p.case_dir(new)/'PARKED.json',dict(design_id=new['design_id'],attempt_id=new['attempt_id'],status='PARKED',**failure));break
        except Exception as exc:
            failure=dict(action=action,reason=type(exc).__name__+': '+str(exc))
            p.stop(new,action,'C54 rebuild failed: '+failure['reason']);break
    # A science finding is retained before packet generation, even if the
    # emitter fails. It can never disappear through an infrastructure exception.
    if remaining:
        p.stop(new,'draft','C54 §3 science MUST remains open: '+__import__('json').dumps(remaining,ensure_ascii=False))
    jobs=[]
    if not failure:
        try:
            from .agents import emit_stage_jobs
            old_inventory=read(p.case_dir(row)/'jobs/draft.json')
            _,prior_review=draft_identity(p,row,old_inventory['jobs'])
            emitted=emit_stage_jobs(p.backend.ctx(new,strict=True),'S6',
                jobs_root=p.case_dir(new)/'draft_jobs_c54',allow_draft=True,
                attempt_id=new['attempt_id']+'_c54',previous_review_path=prior_review)
            jobs=[j.job_path for j in emitted]
            ensure_once(p.case_dir(new)/'jobs/draft.json',dict(jobs=jobs))
        except Exception as exc:
            failure=dict(action='draft',reason=type(exc).__name__+': '+str(exc))
            if not remaining:p.stop(new,'draft','C54 draft emission failed: '+failure['reason'])
    value=dict(design_id=row['design_id'],prior_row=row,new_row=new,bugs=bugs,science_must_open=remaining,
        minimal_options=['Bind a common-definition reconciled outcome and freeze a new design.',
                         'Register a single consistent-definition segment, rerun support gates and freeze a new design.'] if remaining else [],
        requeue=receipt,rebuilt_stages=stages,failure=failure,draft_jobs=[bind(j) for j in jobs],
        actual_data_estimation_calls=0,outcome_association_calls=0,dispatches=0)
    ensure_once(slot/'APPLICATION.json',value)
    return value


def _apply_row(p,row,entries,scheduler_owned):
    from .autopilot import read
    root=p.backend.root(row)
    if row.get('status')=='COMPLETED' or any((root/n).exists() for n in FROZEN):return None
    # A closure authorizes only the reviewed SHOULD or the bound filename bug.
    # Later prep failures, other STOPs and consumed repairs stay open.
    if (p.case_dir(row)/'STOP_SUPERSEDED_C54.json').exists():return None
    relevant=[e for e in entries if
        (e['action'] in {'draft','inspect'} and ('review SHOULD requires evidence-backed hub closure' in e['reason'] or 'MUST>0' in e['reason'])) or
        (e['action']=='prep' and e['reason'].startswith('FileNotFoundError:') and
         str(root/('REVIEW_DRAFT_'+row['attempt_id']+'_draft.json')) in e['reason'])]
    if not relevant:return None
    inventory=read(p.case_dir(row)/'jobs/draft.json')
    if not inventory:return None
    attempt,path=draft_identity(p,row,inventory['jobs'])
    if not path.exists():return None
    review=read(path)
    if review.get('must'):
        bugs,remaining=implementation_findings(p,row,review)
        if bugs:return rebuild(p,row,bugs,remaining,scheduler_owned)
        p.ledger(row,'draft','SCIENCE_MUST_OPEN_C54','c54',findings=review['must'],
            minimal_options=['Resolve the registered science in a new frozen version.', 'Retain STOP pending hub ruling.'])
        return None
    if any(e['action']=='prep' for e in relevant):
        if attempt==row['attempt_id']+'_draft' or (root/('REVIEW_DRAFT_'+row['attempt_id']+'_draft.json')).exists():
            return None
    from .class_table import enabled
    if not (valid_hub_closure(p,row,inventory['jobs'][0]) or
            (not enabled(root) and close_should(p,row,inventory['jobs'][0]))):
        p.ledger(row,'draft','SHOULD_OPEN_C54','c54',findings=review.get('should',[]));return None
    from .registration import _review
    _review(p.backend.ctx(row),attempt)
    evidence=[bind(path)]
    closure=root/('DRAFT_RESOLUTION_'+attempt+'.json')
    if closure.exists():evidence.append(bind(closure))
    supersede_stop(p,row,evidence)
    if not p.done(row,'draft'):ensure_once(p.checkpoint(row,'draft'),inventory)
    return dict(design_id=row['design_id'],attempt=attempt,next_action=p.next_action(row),science_changed=False)


def apply_open_queue(p,scheduler_owned=False):
    from .autopilot import read,scheduler_lock
    from contextlib import nullcontext
    # Standalone application and live ticks share the same exclusive scheduler
    # transaction; one malformed row must never abort unrelated design chains.
    applications=[]
    with (nullcontext() if scheduler_owned else scheduler_lock(p.registry)):
        queue=read(p.registry/'HUB_QUEUE.json',dict(entries=[]));rows=read(p.registry/'ACTIVE_DESIGNS.json',dict(designs=[]))['designs']
        for row in rows:
            entries=[e for e in queue['entries'] if (e['design_id'],e['attempt_id'])==(row['design_id'],row['attempt_id'])
                     and not e.get('status','').startswith('SUPERSEDED_')]
            if not entries:continue
            try:
                value=_apply_row(p,row,entries,True)
                if value:applications.append(value)
            except Exception as exc:
                p.ledger(row,'c54','C54_APPLY_ERROR','c54',reason=type(exc).__name__+': '+str(exc),
                         retained_open_entries=entries)
                applications.append(dict(design_id=row['design_id'],error=type(exc).__name__+': '+str(exc),retained_open=True))
    return applications
