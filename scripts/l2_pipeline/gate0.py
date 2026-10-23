"""C76B2 prospective cheap gates, seeded audits and catalog re-evaluation.

Receipts are immutable per card/catalog/rule. Sealed designs are exempt, and
an audit selection bypasses only Gate 0, never a scientific/full-chain gate.
"""
from pathlib import Path
import hashlib,json,random
from .storage import ensure_once,sha

RULE='gate0_c76b2_v1'
AUDIT_SEED=7602001
PARK='GATE0_PARK'
COUNTING_RULE='L2_COUNTING_RULE: GATE0_PARK reported separately; excluded from 100 until audit validation'


def catalog_identity(repo):
    from .connectors.population_v63 import CURRENT_PATH
    relative=Path('scripts/l2_pipeline/connectors')/CURRENT_PATH.name
    path=Path(repo)/relative
    if not path.is_file():return dict(path=str(relative),sha256=None,catalog_id='unavailable')
    return dict(path=str(relative),sha256=sha(path),catalog_id=json.loads(path.read_text())['catalog_id'])


def audit_selected(design_id,seed=AUDIT_SEED):
    # Independent seeded Bernoulli sampling, stable over catalog bumps/restarts.
    digest=hashlib.sha256((str(seed)+':'+design_id).encode()).hexdigest()
    return random.Random(int(digest,16)).randrange(8)==0


def eligible(pilot,row):
    if not row.get('card') or not row.get('prescreen_enabled'):return False
    root=pilot.backend.root(row)
    return bool(row.get('prescreen_enabled') and not any((root/f).exists() for f in
        ('FREEZE_PREP.json','FREEZE_FINAL.json','REGISTRATION.json','EXECUTION_ONCE.json')))


def _receipt(pilot,row):
    binding=dict(rule=RULE,card_sha256=sha(row['card']),catalog=catalog_identity(pilot.repo))
    digest=hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
    return pilot.case_dir(row)/'gate0'/digest/'GATE0.json',binding


def persist(pilot,row):
    """Run no jobs and fit nothing; preserve earlier receipts on version bumps."""
    from .autopilot import read,atomic
    if not eligible(pilot,row):return dict(status='PROCEED',exempt=True)
    path,binding=_receipt(pilot,row)
    value=read(path)
    if value is None:
        from .prescreen import evaluate
        from .history_reuse import certified_roster
        value=evaluate(read(row['card']),Path(row['card']).parent,
            excluded_units=row.get('fresh_policy',{}).get('excluded_units',[]),repo=pilot.repo,certified=certified_roster(pilot,row))
        from .count_floor import apply_to_prescreen  # T226(c): perfect-history count-floor bound, before any history packet
        value=apply_to_prescreen(value,read(row['card']),pilot.repo)
        pre_stopped=value['status']=='PRESCREEN_PARK'
        selected=pre_stopped and audit_selected(row['design_id'])
        value.update(schema='l2_gate0_v1',**binding,pre_stopped=pre_stopped,
            status=PARK if pre_stopped and not selected else 'PROCEED',
            audit=dict(seed=AUDIT_SEED,probability=1/8,selected=selected,
                       status='PENDING_FULL_CHAIN' if selected else 'NOT_SELECTED'),
            counting_rule=COUNTING_RULE,processed=False,counts_toward_100=False,
            receipt=str(path))
        ensure_once(path,value)
        pilot.ledger(row,'gate0',value['status'],'record',processed=False,reasons=value['reasons'],receipt=str(path))
    if value['card_sha256']!=sha(row['card']):raise ValueError('Gate 0 card binding changed')
    atomic(pilot.case_dir(row)/'GATE0_CURRENT.json',dict(receipt=str(path),sha256=sha(path),**binding))
    return value


def next_status(pilot,row):
    """Pure read, including on dry ticks; refresh() owns catalog-bump writes."""
    if not eligible(pilot,row):return None
    value=current(pilot,row,require_current_catalog=False)
    return PARK if value and value['status']==PARK else None


def refresh(pilot,row):
    """Call once per real tick before next_action; never from a dry tick."""
    from .autopilot import read
    if not row.get('card') or not Path(row['card']).is_file():return None
    root=pilot.backend.root(row)
    if any((root/f).exists() for f in ('FREEZE_PREP.json','FREEZE_FINAL.json','REGISTRATION.json','EXECUTION_ONCE.json')):return None
    parked=read(pilot.case_dir(row)/'PARKED.json')
    if parked:return refresh_park(pilot,row,parked)
    if not eligible(pilot,row):return None
    prior=current(pilot,row,require_current_catalog=False)
    legacy=read(pilot.case_dir(row)/'PRESCREEN.json',{})
    if (prior and prior['catalog']!=catalog_identity(pilot.repo)) or (not prior and legacy.get('status')=='PRESCREEN_PARK'):
        return before_history(pilot,row)
    return None


def refresh_park(pilot,row,parked):
    """Reassess a documented park once per catalog version, preserve old bytes.

    Only the existing purpose-evidence validator may accept a versioned card
    requeue. Other causes retain their park with fresh availability evidence.
    """
    from .autopilot import read
    from .population_resolution import choose,purpose_evidence,apply
    path,binding=_receipt(pilot,row);receipt=path.with_name('PARK_REEVALUATION.json')
    existing=read(receipt)
    if existing:return existing
    card=read(row['card']);first=card.get('first_order',{})
    report=dict(schema='l2_gate0_park_reevaluation_v1',design_id=row['design_id'],
        **binding,prior_park=dict(parked),requeued=False,outcome_values_read=0,
        actual_data_estimation_calls=0)
    try:
        evidence,purpose=purpose_evidence(pilot,row)
        verified=True
    except (ValueError,OSError,KeyError):
        verified=False
        purpose=dict(status='VERIFIED',target_outcome=first.get('target_outcome',''),
            target_construct=first.get('population_resolution',{}).get('construct'))
    try:
        resolution=choose(card,purpose)
        report['current_population_resolution']=resolution
        from .connectors.population_v63 import current_catalog,catalog_module
        source,_=current_catalog();catalog=catalog_module(source['schema_version']).CATALOG_PATH
        changed=card['outcome'].get('catalog_sha256')!=sha(catalog)
        related=parked.get('action') in {'purpose','S0','S2','S3','ri'}
        legal_ready=all(pilot.done(row,a) is not None for a in ('history','reconcile','audit'))
        if changed and related and verified and legal_ready and resolution.get('selected'):
            report.update(apply(pilot,row,scheduler_owned=True,output=receipt.parent/'requeue',superseded_status='SUPERSEDED_GATE0_CATALOG'))
        else:
            report['reason']='Current catalog re-evaluated; retained park unless verified purpose, completed legal gates and a changed selected catalog permit existing mechanical requeue'
    except (ValueError,OSError,KeyError,TypeError) as exc:
        report['inconclusive']=str(exc)
    ensure_once(receipt,report)
    pilot.ledger(row,'gate0-catalog','REQUEUED' if report['requeued'] else 'PARK_REEVALUATED','record',receipt=str(receipt),processed=False)
    return report


def before_history(pilot,row):
    """Call under the scheduler lock immediately before creating history jobs."""
    if not eligible(pilot,row):return dict(status='PROCEED',exempt=True)
    from .c50 import window_precheck
    from .c42 import Park
    from .autopilot import read,atomic
    try:
        revision=window_precheck(pilot,row,scheduler_owned=True)
        if revision:return dict(status='REQUEUED',**revision)
    except Park as exc:
        # Window failures also participate in the identical audit lottery.
        path,binding=_receipt(pilot,row)
        window_path=path.with_name('WINDOW.json')
        selected=audit_selected(row['design_id'])
        value=dict(schema='l2_gate0_v1',design_id=row['design_id'],**binding,
            pre_stopped=True,status='PROCEED' if selected else PARK,
            reasons=['WINDOW: '+str(exc)],metrics=exc.metrics,
            audit=dict(seed=AUDIT_SEED,probability=1/8,selected=selected,
                       status='PENDING_FULL_CHAIN' if selected else 'NOT_SELECTED'),
            counting_rule=COUNTING_RULE,processed=False,counts_toward_100=False,
            outcome_values_read=0,actual_data_estimation_calls=0,receipt=str(window_path))
        ensure_once(window_path,value)
        atomic(pilot.case_dir(row)/'GATE0_WINDOW_CURRENT.json',dict(receipt=str(window_path),sha256=sha(window_path),**binding))
        return value
    return persist(pilot,row)


def current(pilot,row,*,require_current_catalog=True):
    from .autopilot import read
    if not row.get('card'):return None
    pointers=[read(pilot.case_dir(row)/n) for n in ('GATE0_WINDOW_CURRENT.json','GATE0_CURRENT.json')]
    if not any(pointers):return None
    _,binding=_receipt(pilot,row)
    if not require_current_catalog:binding.pop('catalog')
    for filename in ('GATE0_WINDOW_CURRENT.json','GATE0_CURRENT.json'):
        pointer=read(pilot.case_dir(row)/filename)
        if pointer and all(pointer.get(k)==v for k,v in binding.items()):
            if sha(pointer['receipt'])!=pointer['sha256']:raise ValueError('Gate 0 receipt changed')
            return read(pointer['receipt'])
    return None


def record_audit(pilot,row,*,terminal,reason='',estimated=False):
    """Full-chain terminal accounting; unresolved STOP is censored, not success."""
    value=current(pilot,row)
    if not value or not value['audit']['selected']:return None
    from .autopilot import read
    paths=set()
    for reason_code in value.get('reasons',[]):
        code=reason_code.split(':',1)[0].lower()
        paths.add({'fresh':'audit','s3':'S3','s3_count_floor':'S3','s3_track':'S3','ri':'ri','s2_path':'S2','s0_construct':'S0','window':'S2'}.get(code,code))
    parked=read(pilot.case_dir(row)/'PARKED.json',{})
    matched_gate=terminal in {'PARK','PARKED'} and parked.get('action') in paths
    passed_gates=bool(paths) and all(pilot.done(row,action) is not None for action in paths)
    reached_terminal=estimated or terminal in {'FINISHED','COMPLETED','ESTIMATED'}
    complete=bool(reached_terminal or matched_gate or passed_gates)
    false_reject=bool(reached_terminal or passed_gates) if complete else None
    result=dict(schema='l2_gate0_audit_v1',design_id=row['design_id'],attempt_id=row['attempt_id'],
        gate0_receipt=value['receipt'],gate0_sha256=sha(value['receipt']),terminal=terminal,
        reason=reason,completed=complete,false_reject=false_reject,
        assessed_gate_actions=sorted(paths),matched_park_gate=bool(matched_gate),
        assessment_basis='same named gate failed' if matched_gate else 'named gates passed' if passed_gates else 'full-chain terminal' if reached_terminal else 'censored before named gates',
        outcome_values_read=0,actual_data_estimation_calls=0)
    from .autopilot import atomic
    atomic(pilot.case_dir(row)/'GATE0_AUDIT.json',result)
    return result


def summarize_audits(pilot,rows):
    from .autopilot import read,atomic
    audits=[read(pilot.case_dir(r)/'GATE0_AUDIT.json') for r in rows]
    audits=[a for a in audits if a]
    complete=[a for a in audits if a['completed']]
    false=sum(a['false_reject'] for a in complete)
    value=dict(schema='l2_gate0_audit_summary_v1',rule=RULE,seed=AUDIT_SEED,
        selected=sum(bool((current(pilot,r) or {}).get('audit',{}).get('selected')) for r in rows),
        completed=len(complete),censored=sum(not a['completed'] for a in audits),false_rejects=false,
        false_reject_rate=false/len(complete) if complete else None,
        validated=False,counts_toward_100=False,counting_rule=COUNTING_RULE,
        validation_note='No automatic validation threshold was authorized; retain separate count pending audit validation')
    atomic(pilot.registry/'GATE0_AUDIT_SUMMARY.json',value)
    return value
