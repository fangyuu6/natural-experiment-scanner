"""YIELD_FIXES ruling (ii), 2026-10-16: reuse an already-certified adoption history.

A new design on the same policy (same treatment definition and states) may take the S1
products of an earlier design whose history passed A/B reconciliation and the fresh V3
audit, instead of new A/B coding. The history is an outcome-free legal artifact: nothing in
this module reads an outcome, an estimate or a forecast, and every later gate (purpose, S0,
S2, S3 support, RI, forecasts, reviews) still runs on the new design's own card.

The link is explicit and hash-bound: ``card.automation.history_reuse`` names the source
design; ``HISTORY_REUSE.json`` records the source design id and version, the compared
fields and the SHA-256 of every reused artifact; S1 provenance carries that record.
Anything that does not match exactly is refused (no partial reuse, no judgement calls).
"""
from copy import deepcopy
from pathlib import Path
import json
import re
import shutil

from .storage import ensure_once, sha

RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261016_HISTORY_REUSE.md'
STATUS = 'SUPERSEDED_HISTORY_REUSE'
SCHEMA = 'l2_history_reuse_v1'
# Retained verbatim in the new design root so every S1 consumer finds the usual names.
COPIED = ('RECONCILIATION.json', 'CODING_LOCK.json', 'AUDIT_SELECTION.json', 'OFFICIAL_AUDIT.json')
# Must sit in the source design root. OFFICIAL_AUDIT.json is found through the audit job's promote_path:
# a source that was requeued keeps it in the directory of the attempt that ran the audit.
IN_ROOT = ('RECONCILIATION.json', 'CODING_LOCK.json', 'AUDIT_SELECTION.json',
           'TREATMENT.json', 'CERTIFIED_HISTORY.json', 'S1_IMPORT_PROVENANCE.json')
REQUIRED = IN_ROOT + ('OFFICIAL_AUDIT.json',)
# Present in a source that audited R2 conflict rows: kept with the other artifacts so the new design loads the same resolutions.
OPTIONAL = ('AUDIT_RESOLUTIONS.json',)
EXCLUSION_KEYS = ('excluded_conflict_states', 'excluded_conflict_state_reasons', 'universe',
                  'certified_spell_end_periods')


class SourceNotReady(ValueError):
    """The named source exists but is between attempts (S1 not READY or artifacts absent): wait, do not STOP."""


def _read(path):
    return json.loads(Path(path).read_text())


def _bind(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def norm(text):
    return re.sub(r'\s+', ' ', str(text)).strip()


def _year(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.match(r'\d{4}', value):
        return int(value[:4])
    return None


def declaration(payload):
    """The card's explicit link, or None. Malformed links are errors, never ignored."""
    value = (payload.get('automation') or {}).get('history_reuse')
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'source_design_id', 'rule'}
            or not isinstance(value['source_design_id'], str) or not value['source_design_id']
            or value['rule'] != RULING):
        raise ValueError('Invalid history_reuse declaration')
    return value


def declared(row):
    """Cheap tick-time test: does this row's card declare certified-history reuse?

    An unreadable card is simply "not declared" (every other stage fails on it anyway); a readable
    card with a malformed declaration is an error, never ignored."""
    try:
        payload = _read(row['card'])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return declaration(payload) is not None


def mismatches(new, old):
    """Fields that differ between two cards' policy definitions (empty list = same policy)."""
    a, b = new.get('treatment', {}), old.get('treatment', {})
    bad = []
    for label, x, y in (('treatment.predicate', a.get('predicate'), b.get('predicate')),
                        ('mechanism', new.get('mechanism'), old.get('mechanism'))):
        if not norm(x) or norm(x) != norm(y):
            bad.append(label)
    for key in ('policy_family', 'unit', 'track'):
        if new.get(key) is None or new.get(key) != old.get(key):
            bad.append(key)
    if new.get('transition_year_drop') is not True or old.get('transition_year_drop') is not True:
        bad.append('transition_year_drop')
    if a.get('start_period_kind') != b.get('start_period_kind'):
        bad.append('treatment.start_period_kind')
    if not a.get('universe') or sorted(a['universe']) != sorted(b.get('universe') or []):
        bad.append('treatment.universe')
    # The source coders classified starts through its coverage_end: a longer new window
    # would need years nobody coded.
    first, second = _year(a.get('coverage_end')), _year(b.get('coverage_end'))
    if first is None or second is None or first > second:
        bad.append('treatment.coverage_end')
    return bad


def _parked_before_s1(pilot, source, new_payload):
    """R3 cards only: a source that is stopped or parked before it certified a history will not certify one by waiting."""
    from . import rules as rules_module
    try:
        if not rules_module.at_least(rules_module.of_payload(new_payload, pilot.repo), 3):
            return False
        return pilot.next_action(source) in {'STOP', 'PARK', 'PRESCREEN_PARK', 'GATE0_PARK'}
    except (ValueError, OSError, KeyError, TypeError):
        return False


def _rows(pilot):
    from .autopilot import read
    return {r['design_id']: r for r in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs']}


def verify_source(pilot, row, new_payload, source_id):
    """Return the hash-bound facts of a certified source, or raise with the exact reason."""
    from .autopilot import read
    rows = _rows(pilot)
    source = rows.get(source_id)
    if source is None or source_id == row['design_id']:
        raise ValueError('History reuse source is not another active design: ' + str(source_id))
    root = pilot.backend.root(source)
    if (root / 'HISTORY_REUSE.json').exists():
        raise ValueError('History reuse must name the originally certified design, not a reuse')
    s1 = pilot.done(source, 'S1')
    if not s1 or s1.get('disposition') != 'ready':
        if _parked_before_s1(pilot, source, new_payload):
            raise ValueError('History reuse source is not certified: ' + source_id + ' is stopped or parked before S1')
        raise SourceNotReady('History reuse source S1 is not READY: ' + source_id)
    missing = [n for n in IN_ROOT if not (root / n).is_file()]
    if missing:
        raise SourceNotReady('History reuse source lacks certified artifacts: ' + ', '.join(missing))
    treatment = read(root / 'TREATMENT.json')
    certificate = read(root / 'CERTIFIED_HISTORY.json')
    proof = read(root / 'S1_IMPORT_PROVENANCE.json')
    if (treatment.get('audit', {}).get('passed') is not True or proof.get('v3_passed') is not True
            or proof.get('source_type') != 'autopilot_official_v9'
            or treatment.get('source_sha256') != sha(root / 'CERTIFIED_HISTORY.json')
            or certificate.get('audit') != treatment.get('audit')):
        raise ValueError('History reuse source is not a certified fresh-V3 history: ' + source_id)
    for item in proof['evidence']:
        if sha(item['path']) != item['sha256']:
            raise ValueError('History reuse source evidence changed: ' + item['path'])
    audit_jobs = (pilot.done(source, 'audit') or {}).get('jobs') or []
    history_jobs = (pilot.done(source, 'history') or {}).get('jobs') or []
    if len(audit_jobs) != 1 or len(history_jobs) != 2:
        raise ValueError('History reuse source lacks its A/B history and audit jobs')
    artifacts = {n: root / n for n in IN_ROOT}
    audit_answer = Path(read(Path(audit_jobs[0]).with_suffix('.job.json'))['promote_path'])
    if not audit_answer.is_file():
        raise SourceNotReady('History reuse source lacks certified artifacts: OFFICIAL_AUDIT.json')
    artifacts['OFFICIAL_AUDIT.json'] = audit_answer
    for name in OPTIONAL:
        if (root / name).is_file():
            artifacts[name] = root / name
    source_payload = read(source['card'])
    bad = mismatches(new_payload, source_payload)
    if bad:
        raise ValueError('History reuse refused; different policy definition: ' + ', '.join(bad))
    overlay_path = root / 'C42_PREPARATION.json'
    if not overlay_path.is_file():
        raise ValueError('History reuse source lacks its C42 exclusion declaration')
    overlay = read(overlay_path)
    if sorted(overlay['prepared_payload']['treatment'].get('universe') or []) != sorted(new_payload['treatment']['universe']):
        raise ValueError('History reuse refused; coded universe differs')
    return dict(row=source, root=root, payload=source_payload, overlay=overlay, treatment=treatment,
                audit_job=audit_jobs[0], history_jobs=list(history_jobs), artifacts=artifacts)


def certified_roster(pilot, row):
    """Outcome-free roster of the certified source, for the intake S3 replay; None whenever the source is not usable yet.

    The same checks as `reuse` (verify_source). Not usable means the replay is skipped and the screening map decides
    as before; nothing here can park a design, and no outcome, estimate or forecast is read."""
    try:
        payload = read_card(row)
        link = declaration(payload)
        if link is None:
            return None
        source = verify_source(pilot, row, payload, link['source_design_id'])
        cfg = _cfg(payload, source['overlay']['prepared_payload']['treatment'])
        return dict(source_design_id=link['source_design_id'], records=deepcopy(source['treatment']['records']),
                    sample=list(source['treatment']['sample']), excluded_units=list(cfg['excluded_units']))
    except (ValueError, OSError, KeyError, TypeError):
        return None


def required_source(pilot, payload):
    """Intake rule of R3 (history.reuse default): the design id of a same-policy source whose history is certified (or still
    being certified, between attempts), for a card that does not yet declare ``automation.history_reuse``; else None.

    The same exact-match test as ``verify_source`` (policy definition first, then the certified artifacts). A source that is
    stopped or parked before S1 never counts: the card then runs its own A/B history as before."""
    from . import rules as rules_module
    rules = rules_module.of_payload(payload, pilot.repo)
    if not rules_module.at_least(rules, 3) or rules['history']['reuse'] != 'default' or declaration(payload) is not None:
        return None
    row = dict(design_id=payload['design_id'], card=None)
    for design_id, source in sorted(_rows(pilot).items()):
        if design_id == payload['design_id']:
            continue
        try:
            if mismatches(payload, read_card(source)):
                continue
            verify_source(pilot, row, payload, design_id)
        except SourceNotReady:
            return design_id
        except (ValueError, OSError, KeyError, TypeError):
            continue
        return design_id
    return None


def _copy_once(source, target):
    target = Path(target)
    if target.exists():
        if target.read_bytes() != Path(source).read_bytes():
            raise ValueError('Existing reused artifact changed: ' + str(target))
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def _cfg(raw, overlay):
    """Exclusion-only treatment overlay for the new card; the source card's own exclusions stay behind."""
    cfg = deepcopy(raw['treatment'])
    for key in EXCLUSION_KEYS:
        if key in overlay:
            cfg[key] = deepcopy(overlay[key])
    cfg['excluded_units'] = sorted(set(raw['treatment'].get('excluded_units', []))
                                   | set(overlay.get('excluded_conflict_states', [])))
    return cfg


def _materialise(ctx, row, record, path):
    """Copy the retained artifacts and register the exclusion overlay; repeatable after a crash."""
    from .c42 import register_exclusions
    root = ctx.design_dir
    for name in COPIED + tuple(n for n in OPTIONAL if n in record['source_artifacts']):
        _copy_once(record['source_artifacts'][name]['path'], root / name)
    if not (root / 'C42_PREPARATION.json').exists():
        declaration_binding = dict(record['source_c42_declaration'])
        overlay = _read(declaration_binding['path'])['prepared_payload']['treatment']
        register_exclusions(ctx, _cfg(read_card(row), overlay), [declaration_binding, _bind(path)])


def reuse(pilot, row):
    """First call verifies the live source and records the link; later calls re-check the record.

    After the link exists only its recorded, hash-bound artifacts matter, so a later
    requeue of the source design cannot disturb a design already reusing its history.
    """
    ctx = pilot.backend.ctx(row)
    link = declaration(ctx.card.payload)
    if link is None:
        raise ValueError('No history_reuse declaration on the card')
    root = ctx.design_dir
    record_path = root / 'HISTORY_REUSE.json'
    if record_path.exists():
        # The record keeps the ruling hash it was made under; later wording fixes to the ruling file
        # must not stop designs that already reuse a history.
        record = _read(record_path)
        if (record.get('schema') != SCHEMA or record.get('design_id') != row['design_id']
                or record.get('card_sha256') != sha(row['card'])
                or record.get('source_design_id') != link['source_design_id']):
            raise ValueError('History reuse record binding changed')
        for item in [*record['source_artifacts'].values(), record['source_c42_declaration']]:
            if sha(item['path']) != item['sha256']:
                raise ValueError('History reuse source artifact changed: ' + item['path'])
        _materialise(ctx, row, record, record_path)
        return _value(record, record_path)
    ruling = pilot.repo / RULING
    if not ruling.is_file():
        raise ValueError('History reuse ruling missing')
    if any((root / n).exists() for n in ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'TERMINAL.json', 'EXECUTION_ONCE.json')):
        raise ValueError('History reuse cannot change a frozen or finished design')
    raw = read_card(row)
    source = verify_source(pilot, row, raw, link['source_design_id'])
    cfg = _cfg(raw, source['overlay']['prepared_payload']['treatment'])
    certified = source['treatment']
    reconciliation = _read(source['root'] / 'RECONCILIATION.json')
    exclusions = sorted(cfg.get('excluded_conflict_states', []))
    record = dict(schema=SCHEMA, design_id=row['design_id'], design_version=raw['version'],
        card_sha256=sha(row['card']), source_design_id=link['source_design_id'],
        source_version=read_card(source['row'])['version'], source_attempt_id=source['row']['attempt_id'],
        source_card_sha256=sha(source['row']['card']), source_root=str(source['root']),
        compared=dict(predicate_sha256=sha_text(norm(raw['treatment']['predicate'])),
            mechanism_sha256=sha_text(norm(raw['mechanism'])), policy_family=raw['policy_family'],
            universe=sorted(raw['treatment']['universe']),
            coverage_end=[raw['treatment'].get('coverage_end'), source['payload']['treatment'].get('coverage_end')],
            transition_year_drop=True, start_period_kind=raw['treatment'].get('start_period_kind')),
        source_artifacts={n: _bind(path) for n, path in source['artifacts'].items()},
        source_c42_declaration=_bind(source['root'] / 'C42_PREPARATION.json'),
        audit=certified['audit'], sample=certified['sample'], excluded_conflict_states=exclusions,
        copies={n: str(root / n) for n in COPIED + tuple(n for n in OPTIONAL if n in source['artifacts'])}, rule=RULING, rule_sha256=sha(ruling),
        no_new_ab_coding=True, no_new_fresh_audit=True,
        outcome_values_read=0, dispatches=0, execution_authorized=False,
        checkpoints=dict(
            history=dict(jobs=source['history_jobs'], reused_from=link['source_design_id']),
            reconcile=dict(jobs=[], reused_from=link['source_design_id'], counts=reconciliation['counts'],
                excluded_conflict_states=exclusions,
                excluded_conflict_state_reasons=cfg.get('excluded_conflict_state_reasons', {})),
            audit=dict(jobs=[source['audit_job']], reused_from=link['source_design_id'])))
    path = ensure_once(record_path, record)
    _materialise(ctx, row, record, path)
    return _value(record, path)


def _value(record, path):
    return dict(source_design_id=record['source_design_id'], record=str(path), checkpoints=record['checkpoints'])


def sha_text(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def read_card(row):
    return _read(row['card'])


def checkpoint(pilot, row, action):
    """Value written as the history/reconcile/audit checkpoint of a reuse design."""
    value = reuse(pilot, row)
    return value['checkpoints'][action]


def candidates(pilot, row):
    """Certified sources that match this row's card exactly (deterministic order)."""
    payload = read_card(row)
    found = []
    for design_id in sorted(_rows(pilot)):
        if design_id == row['design_id']:
            continue
        try:
            verify_source(pilot, row, payload, design_id)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        found.append(design_id)
    return found


def apply(pilot=None):
    """Stopped-window reopen of designs STOPped at history that have a matching certified source.

    Uses the existing card-bound requeue: a new card version that only adds the explicit
    ``automation.history_reuse`` link. Nothing is dispatched, fitted or deleted.
    """
    from .autopilot import Autopilot, read
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    from .c70_l3 import protected
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/yield_fixes/history_reuse'
    ruling = p.repo / RULING
    with guard(p.registry):
        if not (p.registry / 'AUTOPILOT_STOP').exists() or read(p.registry / 'CODE_TASK.lock', {}).get('task') != 'YIELD_FIXES':
            raise ValueError('YIELD_FIXES stopped code window required')
        clean_stop(p.repo)
        application = out / 'APPLICATION.json'
        if application.exists():
            return read(application)
        results = []
        for row in read(p.registry / 'ACTIVE_DESIGNS.json')['designs']:
            stop = p.stop_record(row)
            if (not stop or stop.get('action') not in {'history', 'dispatch'} or p.done(row, 'history') is not None
                    or protected(p.backend.root(row)) or declared(row)):
                continue
            sources = candidates(p, row)
            if not sources:
                continue
            payload = read(row['card'])
            payload['version'] = 'v' + str(int(payload['version'][1:]) + 1)
            payload.setdefault('automation', {})['history_reuse'] = dict(source_design_id=sources[0], rule=RULING)
            card = ensure_once(Path(row['card']).with_name(Path(row['card']).stem + '_reuse.json'), payload)
            value = ensure_once(out / 'rulings' / (row['design_id'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=row['design_id'], prior_card_sha256=sha(row['card']),
                card_sha256=sha(card), decision='resume', superseded_status=STATUS,
                reason='Ruling (ii) 2026-10-16: reuse the certified history of ' + sources[0] + ' for the same policy',
                authority=_bind(ruling), prior_stop=stop, source_design_id=sources[0], execution_authorized=False))
            results.append(requeue(row['design_id'], card, value, p))
        ensure_once(application, dict(records=results, dispatches=0, actual_data_estimation_calls=0))
        return read(application)


if __name__ == '__main__':
    print(json.dumps(apply(), indent=2))
