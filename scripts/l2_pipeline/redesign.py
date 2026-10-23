"""Fail-fast screen, purpose label, classified parks and the reopen tool (HUB_RULING_20261017_REDESIGN, window W1).

Everything here is outcome-blind: the screen reads the value-free support sidecar and the A/B-reconciled roster, the label reads
the purpose verifier's answer, the park table reads the text of a reason. Nothing reads or estimates an outcome value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

from . import rules as rules_module
from .c42 import Park
from .storage import ensure_once, sha

RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261017_REDESIGN.md'
STATUS = 'SUPERSEDED_REDESIGN_R1'
REQUEST = 'REDESIGN_REOPEN_REQUEST.json'
S3_TEXT = 'S3 Disposition.STOPPED: Registered support insufficient'
RI_TEXT = 'STOPPED_PRE_RUN: RI199/RI999 feasibility below 90%'
LABELS = ('target_matched', 'broader_than_target', 'unresolved')


def _read(path, default=None):
    from .autopilot import read
    return read(path, default)


def out_dir(pilot, version):
    return pilot.registry.parent / 'autopilot' / ('redesign_' + version.lower())


# --- 1. screen: the outcome-blind gates before any agent packet -------------------------------------------------------------------

def _rosters(report, excluded, conflict_units):
    """Engine rosters the screen tests. R1: the A/B-reconciled roster. R2: the same plus every audit conflict state at coder A's
    coding, and again at coder B's coding; a conflict state is only dated by the audit later, so each side is a possible outcome."""
    from copy import deepcopy
    from .intake.official_history import engine_records
    accepted = report['historical_comparison']['accepted']
    if not conflict_units:
        return [('reconciled', engine_records(accepted, excluded))]
    ids = {c['unit'] for c in conflict_units}
    free = set(excluded) - ids
    base = [r for r in accepted if r['unit'] not in ids]
    return [('coder_' + side.upper(), engine_records(base + [deepcopy(c[side]) for c in conflict_units], free)) for side in ('a', 'b')]


def screen_action(pilot, row):
    """S3 sidecar check and RI feasibility on the A/B-reconciled roster. Park only if every variant is proven to fail."""
    from . import prescreen
    rules = rules_module.of_row(pilot, row)
    if rules is None:
        raise ValueError('screen needs a card that pins rules')
    ctx = pilot.backend.ctx(row)
    payload = ctx.card.payload
    report = ctx.read('RECONCILIATION.json')
    excluded = set(payload.get('treatment', {}).get('excluded_units', []))
    value = dict(schema='l2_redesign_screen_v1', rules_sha256=rules_module.pin_of_row(row)['sha256'], status='PROCEED',
                 outcome_values_read=0, actual_data_estimation_calls=0)
    conflicts = report.get('audit_conflict_units') or [] if rules_module.at_least(rules, 2) else []
    try:
        rosters = _rosters(report, excluded, conflicts)
    except (ValueError, KeyError, TypeError) as exc:
        return dict(value, inconclusive='Roster not shaped for the screen: ' + str(exc)[:300])
    cfg = rules['screen']
    fields = {'variant', 'year_shift', 'passed', 'reasons', 'inconclusive', 's3_pass', 'ri_pass', 'support_basis'}
    proofs, shown, texts = [], [], []
    for name, roster in rosters:
        result = prescreen.evaluate(payload, Path(row['card']).parent, excluded_units=row.get('fresh_policy', {}).get('excluded_units', []),
                                    repo=pilot.repo, roster=roster, shifts=tuple(cfg['shifts']), pool_rule=cfg['pool_rule'])
        variants = result.get('variants', [])
        shown.append((name, len(roster), [{k: v for k, v in t.items() if k in fields} for t in variants]))
        proven = result.get('status') == 'PRESCREEN_PARK' and bool(variants) and all(t.get('passed') is False for t in variants)
        proofs.append(proven)
        if not proven:
            if result.get('inconclusive'):
                value['inconclusive'] = result['inconclusive']
        else:
            primary = next((t for t in variants if t.get('variant') == 'primary'), variants[0])
            texts.append(S3_TEXT if 's3' in primary.get('reasons', []) else RI_TEXT)
    if len(rosters) == 1:
        value.update(roster_units=shown[0][1], variants=shown[0][2])
    else:
        value.update(roster_units=shown[0][1], variants=shown[0][2],
                     rosters=[dict(roster=n, roster_units=u, variants=v) for n, u, v in shown])
    if not all(proofs):
        return value
    raise Park(texts[0], dict(kind='SCREEN', screen=value, outcome_values_read=0, actual_data_estimation_calls=0))


# --- 2. purpose as a label ---------------------------------------------------------------------------------------------------------

def _population(payload):
    first = payload.get('first_order', {})
    population = payload.get('population', {})
    return first.get('target_population', population.get('target', population.get('description')))


def label_from(purpose, payload):
    """(label, matches_target, matches_population) from the verifier's answer and the card. UNRESOLVED -> ('unresolved', None, None)."""
    if not isinstance(purpose, dict) or purpose.get('status') != 'VERIFIED':
        return 'unresolved', None, None
    first = payload.get('first_order', {})
    target = purpose.get('target_outcome') == first.get('target_outcome')
    population = purpose.get('target_population') == _population(payload)
    return ('target_matched' if target and population else 'broader_than_target'), target, population


def _write_label(ctx, rules, label, status, target, population, tried, reason):
    tried = [t for t in tried if isinstance(t, str)] if isinstance(tried, list) else []
    value = dict(schema='l2_purpose_label_v1', label=label, verifier_status=status, matches_card_target=target,
                 matches_card_population=population, tried=tried, unresolved_reason=reason,
                 rules_sha256=rules_module.pin_of_payload(ctx.card.payload)['sha256'], execution_authorized=False)
    ensure_once(ctx.design_dir / 'PURPOSE_LABEL.json', value)
    return value


def purpose_label_action(pilot, row):
    """Emit/collect the verifier job as before, then record a label. No Park, no STOP, no requeue, no series change."""
    from .autopilot import environmental
    rules = rules_module.of_row(pilot, row)
    if rules is None or rules['purpose']['mode'] != 'label':
        raise ValueError('purpose label needs rules with purpose.mode == label')
    ctx = pilot.backend.ctx(row)
    path = ctx.design_dir / 'PURPOSE_LABEL.json'
    if path.exists():
        return _read(path)
    inventory = pilot.case_dir(row) / 'jobs' / 'purpose.json'
    value = _read(inventory)
    if value is None:
        value = dict(jobs=pilot.backend.jobs(row, 'purpose', pilot))
        if not value['jobs']:
            raise ValueError('No jobs for purpose')
        ensure_once(inventory, value)
    try:
        jobs = pilot.collect_submissions(row, 'purpose', value['jobs'])
    except Exception as exc:  # replacement exhausted, collector rejection after the one replacement, dead packet: a label, not a STOP
        if environmental(exc):
            raise
        pilot.pending[:] = [(r, p) for r, p in pilot.pending if not (r == row and str(p).startswith(str(pilot.case_dir(row))))]
        return _write_label(ctx, rules, 'unresolved', 'REJECTED_AFTER_REPLACEMENT', None, None, [],
                            (type(exc).__name__ + ': ' + str(exc))[:500])
    if jobs is None:
        return None
    purpose = ctx.read('PURPOSE_VERIFICATION.json')
    label, target, population = label_from(purpose, ctx.card.payload)
    reason = ' '.join(str(purpose.get('unresolved_reason', '')).split())[:500] if label == 'unresolved' else None
    return _write_label(ctx, rules, label, purpose.get('status'), target, population, purpose.get('tried', []), reason)


def label_transport_failure(pilot, row, path, reason):
    """A dead or exhausted purpose packet of an R1 card becomes the label 'unresolved' (called from the queue's hub escalation)."""
    meta = _read(Path(path).with_suffix('.job.json'), {}) or {}
    if not meta.get('contract', {}).get('label_mode'):
        return False
    rules = rules_module.of_row(pilot, row)
    ctx = pilot.backend.ctx(row)
    if rules is not None and pilot.done(row, 'purpose') is None and not (ctx.design_dir / 'PURPOSE_LABEL.json').exists():
        value = _write_label(ctx, rules, 'unresolved', 'NO_RESULT', None, None, [], str(reason)[:500])
        ensure_once(pilot.checkpoint(row, 'purpose'), value)
        pilot.ledger(row, 'purpose', 'PURPOSE_LABEL_UNRESOLVED', 'record', reason=str(reason)[:240])
    return True


# --- 3. park table -----------------------------------------------------------------------------------------------------------------

def class_of(pilot, row, action, reason):
    """The park_reasons row that matches an R1 card's stop/park text, or None (R0 cards and unmatched text)."""
    if rules_module.pin_of_row(row) is None:
        return None
    rules = rules_module.of_row(pilot, row)
    return rules_module.classify(rules, action, reason) if rules else None


def _design_dir(pilot, row):
    """The design directory of the row for the freeze check; None when it cannot be found (the caller then keeps the STOP)."""
    try:
        return pilot.backend.root(row)
    except Exception:
        return None


def write_once_park(pilot, row, action, reason):
    """A write-once guard failure of an unfrozen attempt is an engineering event (the file is there and a fresh derivation
    differs), not a check on the design: PARK it, reopenably (release_recovery --include-parks), whatever rules the card pins.
    False leaves the unchanged STOP: another guard, a frozen or sealed attempt, or an attempt that already has a PARKED.json."""
    from . import write_once
    if not write_once.matches(reason) or write_once.frozen(_design_dir(pilot, row)):
        return False
    case = pilot.case_dir(row)
    if (case / 'PARKED.json').exists() or (case / 'PARKED_C83_FINAL.json').exists():
        return False
    from .autopilot import utc
    value = write_once.park_value(row, action, reason, utc())
    ensure_once(case / 'PARKED.json', value)
    pilot.ledger(row, action, 'PARK_WRITE_ONCE', 'redesign', reopen='release_recovery', reason=str(reason)[:240])
    return True


def park_or_stop(pilot, row, action, reason):
    """True if the reason was written as a classified PARK; False means the caller does the unchanged STOP."""
    if write_once_park(pilot, row, action, reason):
        return True
    klass = class_of(pilot, row, action, reason)
    if klass is None:
        return False
    pin = rules_module.pin_of_row(row)
    from .autopilot import utc
    ensure_once(pilot.case_dir(row) / 'PARKED.json', dict(
        design_id=row['design_id'], attempt_id=row['attempt_id'], action=action, status='PARKED', reason=reason,
        metrics=dict(kind='CLASSIFIED', outcome_values_read=0), class_id=klass['id'], reopen=klass['reopen'],
        rules_sha256=pin['sha256'], time=utc(), rule=pilot_rules_text(pilot)))
    pilot.ledger(row, action, 'PARK_CLASSIFIED', 'redesign', class_id=klass['id'], reopen=klass['reopen'], reason=reason[:240])
    return True


def pilot_rules_text(pilot):
    from .autopilot import RULES
    return RULES['redesign']


# --- 4. read-only plan, classification receipts, reopen requests -------------------------------------------------------------------

def _state(pilot, row):
    from .autopilot import read
    stop = pilot.stop_record(row)
    parked = read(pilot.case_dir(row) / 'PARKED.json') or read(pilot.case_dir(row) / 'PARKED_C83_FINAL.json')
    if stop:
        return 'STOP', stop.get('action'), stop.get('reason', '')
    if parked and pilot.next_action(row) == 'PARK':
        return 'PARK', parked.get('action'), parked.get('reason', '')
    return None, None, None


def _reconcile_reachable(pilot, row, rules):
    """RECONCILE_FLOOR: the roster minimum is reachable once the audit-resolvable conflict states are counted (R2 and later)."""
    if not rules_module.at_least(rules, 2) or not rules['reconcile'].get('count_audit_resolvable') or not rules['audit']['conflict_rows']:
        return False, 'these rules do not count audit-resolvable states'
    from .intake.official_history import conflict_rows
    report = _read(pilot.backend.root(row) / 'RECONCILIATION.json')
    card = _read(row['card']) or {}
    if not report:
        return False, 'no RECONCILIATION.json'
    conflicts = conflict_rows(report['historical_comparison'], set(card.get('treatment', {}).get('excluded_units', [])),
                              rules['audit']['conflict_cap'])
    minimum = max(rules['reconcile']['min_retained_treated'], card.get('analysis', {}).get('min_treated', 6))
    retained = report.get('counts', {}).get('retained_treated', 0)
    return retained + len(conflicts) >= minimum, 'retained treated %d + audit conflict states %d vs minimum %d' % (retained, len(conflicts), minimum)


def certified_sibling_source(pilot, row):
    """The certified source a HISTORY park can be reopened onto (R3): an exact-match certified design other than the one the card
    already names, in deterministic order; None when there is none."""
    from . import history_reuse
    payload = _read(row['card']) or {}
    current = (history_reuse.declaration(payload) or {}).get('source_design_id')
    found = [d for d in history_reuse.candidates(pilot, row) if d != current]
    return found[0] if found else None


def _certified_sibling(pilot, row, rules):
    """HISTORY classes (R3): reopen only onto an exact-match certified sibling, whose history and audit are then reused."""
    if not rules_module.at_least(rules, 3) or rules['history']['reuse'] != 'default':
        return False, 'these rules do not reuse certified history by default'
    source = certified_sibling_source(pilot, row)
    return (source is not None), ('certified sibling ' + source) if source else 'no exact-match certified sibling'


CHECKS = {'reconcile_reachable': _reconcile_reachable, 'certified_sibling': _certified_sibling}


def run_check(name, pilot, row, rules):
    """(allowed, why) for a park_reasons ``reopen: check:<name>``. An unknown name, or any error reading the evidence, fails closed."""
    function = CHECKS.get(name)
    if function is None:
        return False, 'unknown check ' + str(name)
    try:
        return function(pilot, row, rules)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, type(exc).__name__ + ': ' + str(exc)[:160]


def plan(pilot, rules_version=None):
    """One dict per design: state, action, reason, class, whether a reopen under ``rules_version`` is allowed and why not. Read-only."""
    from .autopilot import read
    from .c70_l3 import protected
    version = rules_version or (rules_module.current(pilot.repo) or {}).get('version')
    rules = rules_module.load(rules_module.rules_path(version, pilot.repo)) if version else None
    rows = []
    for row in read(pilot.registry / 'ACTIVE_DESIGNS.json', {}).get('designs', []):
        item = dict(design_id=row['design_id'], attempt_id=row['attempt_id'], state=None, action=None, reason=None, class_id=None,
                    reopen=None, eligible=False, why_not=None)
        try:
            root = pilot.backend.root(row)
            action = pilot.next_action(row)
        except Exception as exc:  # a row the plan cannot read is reported, never touched
            item.update(state='UNREADABLE', why_not=type(exc).__name__ + ': ' + str(exc)[:160])
            rows.append(item)
            continue
        if action == 'FINISHED' or row.get('status') == 'COMPLETED':
            item.update(state='FINISHED', why_not='finished')
        elif action in {'PRESCREEN_PARK', 'GATE0_PARK'}:
            item.update(state=action, why_not='intake park (not part of the L2 100)')
        elif action not in {'STOP', 'PARK'}:
            item.update(state='RUNNING', why_not='not stopped or parked')
        else:
            state, act, reason = _state(pilot, row)
            klass = rules_module.classify(rules, act, reason) if rules else None
            item.update(state=state, action=act, reason=str(reason)[:300], class_id=klass['id'] if klass else None,
                        reopen=klass['reopen'] if klass else None)
            pin = rules_module.pin_of_row(row)
            if protected(root) or any((root / n).exists() for n in ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'TERMINAL.json')):
                item['why_not'] = 'frozen or terminal'
            elif (pilot.case_dir(row) / 'jobs' / 'forecasts.json').exists() or pilot.done(row, 'forecasts') is not None:
                item['why_not'] = 'has a forecast inventory (continues under its own rules)'
            elif klass is None:
                item['why_not'] = 'reason matches no park_reasons row'
            elif klass['reopen'] != 'on_new_rules' and not klass['reopen'].startswith('check:'):
                item['why_not'] = 'class ' + klass['id'] + ' reopen=' + klass['reopen']
            elif pin is not None and version and rules_module.number(pin['version']) >= rules_module.number(version):
                item['why_not'] = 'already on ' + pin['version']
            else:
                allowed, why = (True, None) if klass['reopen'] == 'on_new_rules' else run_check(klass['reopen'][len('check:'):], pilot, row, rules)
                item['eligible'] = allowed
                if not allowed:
                    item['why_not'] = 'class ' + klass['id'] + ' reopen=' + klass['reopen'] + ' fails: ' + str(why)
        rows.append(item)
    return rows


def classify_all(pilot, version=None):
    """Receipts only: which class each stopped/parked design falls in. The old STOP/PARKED files are never edited."""
    version = version or rules_module.current(pilot.repo)['version']
    rules = rules_module.load(rules_module.rules_path(version, pilot.repo))
    pin = rules_module.descriptor(version, pilot.repo)
    written = []
    for item in plan(pilot, version):
        if item['state'] not in {'STOP', 'PARK'}:
            continue
        value = dict(schema='l2_redesign_class_v1', design_id=item['design_id'], attempt_id=item['attempt_id'], state=item['state'],
                     action=item['action'], class_id=item['class_id'], reopen=item['reopen'],
                     reason_sha256=hashlib.sha256(str(item['reason']).encode()).hexdigest(), rules_sha256=pin['sha256'],
                     outcome_values_read=0)
        written.append(str(ensure_once(out_dir(pilot, version) / 'classes' / (item['design_id'] + '__' + item['attempt_id'] + '.json'), value)))
    return written


def _bump(version):
    if not isinstance(version, str) or not version.startswith('v') or not version[1:].isdigit():
        raise ValueError('Unexpected card version: ' + str(version))
    return 'v' + str(int(version[1:]) + 1)


def apply_open_requests(pilot):
    """Tick hook. The hub files registry/REDESIGN_REOPEN_REQUEST.json = {scope: 'all' | [class ids], rules_version}; each eligible
    design is requeued once per (design, version) under a new card that differs from the old one only in automation.rules."""
    from .autopilot import atomic
    request = _read(pilot.registry / REQUEST)
    if not request:
        return []
    scope, version = request.get('scope'), request.get('rules_version')
    if set(request) != {'scope', 'rules_version'} or not (scope == 'all' or (isinstance(scope, list) and scope and all(isinstance(s, str) for s in scope))):
        pilot.ledger({}, 'redesign', 'REDESIGN_REQUEST_INVALID', 'redesign', request=request)
        return []
    pin = rules_module.descriptor(version, pilot.repo)
    results = []
    from .requeue import requeue
    authority = pilot.repo / RULING
    for item in plan(pilot, version):
        if not item['eligible'] or (scope != 'all' and item['class_id'] not in scope):
            continue
        receipt = out_dir(pilot, version) / 'requests' / (item['design_id'] + '.json')
        if receipt.exists():
            continue
        row = next(r for r in _read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs'] if r['design_id'] == item['design_id'])
        try:
            payload = _read(row['card'])
            payload['version'] = _bump(payload['version'])
            payload.setdefault('automation', {})['rules'] = pin
            if item['reopen'] == 'check:certified_sibling':   # R3: the reopened card names the certified source (the one allowed extra difference)
                from . import history_reuse
                payload['automation']['history_reuse'] = dict(source_design_id=certified_sibling_source(pilot, row), rule=history_reuse.RULING)
            card = ensure_once(Path(row['card']).with_name(Path(row['card']).stem + '_' + payload['version'] + '.json'), payload)
            ruling = ensure_once(out_dir(pilot, version) / 'rulings' / (item['design_id'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=item['design_id'], prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='resume', superseded_status=STATUS, implementation_rule='REDESIGN_R1', rules=pin,
                authority=dict(path=str(authority.resolve()), sha256=sha(authority)),
                reason='HUB_RULING_20261017_REDESIGN: reopen under rules ' + version + ' (class ' + str(item['class_id']) + ')',
                execution_authorized=False))
            done = requeue(item['design_id'], card, ruling, pilot, scheduler_owned=True)
            ensure_once(receipt, dict(design_id=item['design_id'], rules_version=version, rules_sha256=pin['sha256'], class_id=item['class_id'],
                                      new_attempt=done['new_row']['attempt_id'], restart_action=done['restart_action']))
            results.append(dict(design_id=item['design_id'], restart=done['restart_action']))
        except Exception as exc:
            from .autopilot import environmental
            if environmental(exc):
                raise
            pilot.ledger(row, 'redesign', 'REDESIGN_APPLY_ERROR', 'redesign', reason=type(exc).__name__ + ': ' + str(exc)[:300])
    return results


def main(argv=None):
    from .autopilot import Autopilot, atomic, utc
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--rules-version')
    sub.add_parser('classify')
    r = sub.add_parser('request')
    r.add_argument('--scope', required=True, help='all, or comma-separated class ids')
    r.add_argument('--rules-version', help='default: the version the pointer names (RULES_CURRENT.json)')
    args = parser.parse_args(argv)
    pilot = Autopilot()
    if args.cmd == 'plan':
        rows = plan(pilot, args.rules_version)
        counts = {}
        for row in rows:
            counts[(row['state'], row['class_id'], row['eligible'])] = counts.get((row['state'], row['class_id'], row['eligible']), 0) + 1
        print(json.dumps(dict(designs=rows, counts=[dict(state=k[0], class_id=k[1], eligible=k[2], n=v) for k, v in sorted(counts.items(), key=str)]),
                         indent=2, sort_keys=True))
    elif args.cmd == 'classify':
        print(json.dumps(classify_all(pilot), indent=2))
    else:
        scope = 'all' if args.scope == 'all' else args.scope.split(',')
        version = args.rules_version or (rules_module.current(pilot.repo) or {}).get('version')
        if not version:
            parser.error('no rules published; give --rules-version')
        rules_module.descriptor(version, pilot.repo)
        path = pilot.registry / REQUEST
        atomic(path, dict(scope=scope, rules_version=version))  # re-filing is harmless: one receipt per (design, version)
        print('filed', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
