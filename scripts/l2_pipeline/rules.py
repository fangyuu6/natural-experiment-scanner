"""Versioned L2 rules file (HUB_RULING_20261017_REDESIGN, window REDESIGN W1).

Thresholds, chain order and the park-reason table are data in ``data/l2_pipeline/rules/RULES_Rn.json``. A rules edit is a new
write-once version plus a pointer move, never a code window and never a change to ``code_inventory``. Only a new field needs
code: ``validate`` accepts a closed key set per version, so an unknown key is an error.

A card pins a version in ``automation.rules = {version, sha256, path}``; the pin is re-checked against the file bytes wherever
the card is loaded (Backend.ctx, ingest, requeue). A card without the pin is R0 = the behaviour that existed before this file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

from .storage import encoded, ensure_once, sha

REPO = Path(__file__).resolve().parents[2]
RULES_DIR = 'data/l2_pipeline/rules'
POINTER = 'data/l2_pipeline/registry/RULES_CURRENT.json'
SCHEMA = 'l2_rules_v1'

KNOWN_ACTIONS = ('history', 'reconcile', 'screen', 'audit', 'purpose', 'purpose-direction', 'S0', 'S1', 'S2', 'S3', 'S4', 'ri',
                 'forecasts', 'lock', 'draft-dry-load', 'draft', 'prep', 'approve-prep', 'calibrate', 'seal', 'dry-load', 'sealed',
                 'approve-final', 'execute', 'S7', 'register')
GATES = KNOWN_ACTIONS + ('dispatch', '*')
# The sections each version may carry; a section the code of that window does not read is not in that version.
SECTION_KEYS = {1: frozenset({'schema', 'version', 'note', 'chain', 'purpose', 'screen', 'reconcile', 'audit', 'park_reasons'})}
# R2 (window W2) adds: forecast (one packet), review (one review), direction (no third coder).
SECTION_KEYS[2] = SECTION_KEYS[1] | {'forecast', 'review', 'direction'}
# R3 (window W3) adds: structural (floors of the card's structural exclusion list), intake (throttle), history (reuse by default).
SECTION_KEYS[3] = SECTION_KEYS[2] | {'structural', 'intake', 'history'}
REQUIRED = frozenset({'schema', 'version', 'chain', 'purpose', 'screen', 'reconcile', 'audit', 'park_reasons'})
REQUIRED_BY_VERSION = {1: REQUIRED, 2: REQUIRED | {'forecast', 'review', 'direction'},
                       3: REQUIRED | {'forecast', 'review', 'direction', 'structural', 'intake', 'history'}}
STRUCTURAL_KEYS = frozenset({'max_removed_share', 'min_treated', 'min_cohorts', 'min_controls', 'min_audited_retained'})
AUDIT_KEYS = {1: {'max_known_errors', 'max_unresolved', 'sample_size'},
              2: {'conflict_rows', 'conflict_cap', 'exclude_prior_sample'},
              3: {'conflict_rows', 'conflict_cap', 'exclude_prior_sample'}}
RECONCILE_KEYS = {1: {'min_retained_treated', 'min_retained_controls'},
                  2: {'min_retained_treated', 'min_retained_controls', 'count_audit_resolvable'},
                  3: {'min_retained_treated', 'min_retained_controls', 'count_audit_resolvable'}}
PARK_KEYS = frozenset({'id', 'gates', 'contains', 'disposition', 'reopen'})
REOPEN = re.compile(r'^(never|on_new_rules|check:[a-z_]+)$')
DESCRIPTOR_KEYS = frozenset({'version', 'sha256', 'path'})
_LOADED = {}  # rules sha256 -> parsed value (the bytes are re-hashed on every use; this only skips the parse)


def _int(value, minimum):
    return type(value) is int and value >= minimum


def number(version):
    if not isinstance(version, str) or re.fullmatch(r'R[1-9][0-9]*', version) is None:
        raise ValueError('Rules version must look like R1')
    return int(version[1:])


def validate(value):
    """Return ``value`` if it is a well-formed rules document, else raise ValueError. Closed key set, typed values."""
    if not isinstance(value, dict):
        raise ValueError('Rules must be an object')
    n = number(value.get('version'))
    keys = SECTION_KEYS.get(n)
    if keys is None:
        raise ValueError('Rules version not supported by this code: ' + value['version'])
    unknown = sorted(set(value) - keys)
    if unknown:
        raise ValueError('Unknown rules key (a new field needs a code window): ' + ', '.join(unknown))
    missing = sorted((REQUIRED_BY_VERSION[n] & keys) - set(value))
    if missing:
        raise ValueError('Missing rules section: ' + ', '.join(missing))
    if value['schema'] != SCHEMA:
        raise ValueError('Rules schema differs')
    if 'note' in value and not isinstance(value['note'], str):
        raise ValueError('Rules note must be text')
    chain = value['chain']
    if not isinstance(chain, list) or not chain or len(set(chain)) != len(chain) or any(a not in KNOWN_ACTIONS for a in chain):
        raise ValueError('chain must be a non-empty list of distinct known actions')
    purpose = value['purpose']
    if not isinstance(purpose, dict) or set(purpose) != {'mode'} or purpose['mode'] != 'label':
        raise ValueError('purpose must be {"mode": "label"}')
    screen = value['screen']
    if (not isinstance(screen, dict) or set(screen) != {'shifts', 'pool_rule'} or not isinstance(screen['pool_rule'], bool)
            or not isinstance(screen['shifts'], list) or not screen['shifts'] or len(set(screen['shifts'])) != len(screen['shifts'])
            or any(type(s) is not int or abs(s) > 3 for s in screen['shifts'])):
        raise ValueError('screen must be {"shifts": [ints], "pool_rule": bool}')
    reconcile = value['reconcile']
    floor = {k: v for k, v in (reconcile.items() if isinstance(reconcile, dict) else []) if k != 'count_audit_resolvable'}
    if (not isinstance(reconcile, dict) or set(reconcile) != RECONCILE_KEYS[n] or not floor or not all(_int(v, 1) for v in floor.values())
            or ('count_audit_resolvable' in reconcile and type(reconcile['count_audit_resolvable']) is not bool)):
        raise ValueError('reconcile must carry the two positive minimums' + (' and count_audit_resolvable' if n >= 2 else ''))
    audit = value['audit']
    if not isinstance(audit, dict) or set(audit) != AUDIT_KEYS[n]:
        raise ValueError('audit must carry ' + ('conflict_rows, conflict_cap and exclude_prior_sample (the limits 2 / 5 / 10 are constants of the collector)' if n >= 2
                         else 'max_known_errors, max_unresolved and sample_size (10: the sample size is not wired to the rules yet)'))
    if n == 1 and (not _int(audit['max_known_errors'], 0) or not _int(audit['max_unresolved'], 0) or audit['sample_size'] != 10):
        raise ValueError('audit must carry max_known_errors, max_unresolved and sample_size (10: the sample size is not wired to the rules yet)')
    if n >= 2:
        if (type(audit['conflict_rows']) is not bool or type(audit['exclude_prior_sample']) is not bool
                or not _int(audit['conflict_cap'], 1) or audit['conflict_cap'] > 10):
            raise ValueError('audit.conflict_rows and audit.exclude_prior_sample must be booleans, audit.conflict_cap an integer 1..10')
        if value['forecast'] != {'mode': 'bundle'}:
            raise ValueError('forecast must be {"mode": "bundle"}')
        if value['review'] != {'mode': 'single'}:
            raise ValueError('review must be {"mode": "single"}')
        if value['direction'] != {'third_coder': False}:
            raise ValueError('direction must be {"third_coder": false}')
        if 'sealed' in chain:
            raise ValueError('A single-review chain has no sealed step')
    if n >= 3:
        structural = value['structural']
        share = structural.get('max_removed_share') if isinstance(structural, dict) else None
        if (not isinstance(structural, dict) or set(structural) != STRUCTURAL_KEYS or not isinstance(share, list) or len(share) != 2
                or not all(_int(v, 1) for v in share) or share[0] > share[1]
                or not all(_int(structural[k], 0) for k in STRUCTURAL_KEYS - {'max_removed_share'})
                or not all(_int(structural[k], 1) for k in ('min_treated', 'min_cohorts', 'min_controls'))):
            raise ValueError('structural must be {max_removed_share: [n, d], min_treated, min_cohorts, min_controls, min_audited_retained}')
        intake = value['intake']
        if not isinstance(intake, dict) or set(intake) != {'max_open_before_screen'} or not _int(intake['max_open_before_screen'], 1):
            raise ValueError('intake must be {"max_open_before_screen": positive integer}')
        if value['history'] != {'reuse': 'default'}:
            raise ValueError('history must be {"reuse": "default"}')
    table = value['park_reasons']
    if not isinstance(table, list):
        raise ValueError('park_reasons must be a list')
    ids = set()
    for row in table:
        if not isinstance(row, dict) or set(row) != PARK_KEYS:
            raise ValueError('Every park_reasons row needs exactly: ' + ', '.join(sorted(PARK_KEYS)))
        if not isinstance(row['id'], str) or not row['id'] or row['id'] in ids:
            raise ValueError('park_reasons ids must be distinct non-empty text')
        ids.add(row['id'])
        if (not isinstance(row['gates'], list) or not row['gates'] or any(g not in GATES for g in row['gates'])):
            raise ValueError('park_reasons gates must be known actions: ' + row['id'])
        if (not isinstance(row['contains'], list) or not row['contains']
                or any(not isinstance(s, str) or not s.strip() for s in row['contains'])):
            raise ValueError('park_reasons contains must be non-empty text (substring match): ' + row['id'])
        if row['disposition'] != 'PARK':
            raise ValueError('park_reasons disposition must be PARK: ' + row['id'])
        if not isinstance(row['reopen'], str) or REOPEN.fullmatch(row['reopen']) is None:
            raise ValueError('park_reasons reopen must be never, on_new_rules or check:<name>: ' + row['id'])
    return value


def rules_path(version, repo=None):
    number(version)
    return Path(repo or REPO) / RULES_DIR / ('RULES_' + version + '.json')


def relative_path(version):
    number(version)
    return RULES_DIR + '/RULES_' + version + '.json'


def load(path):
    path = Path(path)
    digest = sha(path)
    if digest not in _LOADED:
        _LOADED[digest] = validate(json.loads(path.read_text()))
    return deepcopy(_LOADED[digest])


def descriptor(version, repo=None):
    path = rules_path(version, repo)
    return dict(version=version, sha256=sha(path), path=relative_path(version))


def current(repo=None):
    """The pointer, bound to the file bytes, or None if nothing is published."""
    pointer = Path(repo or REPO) / POINTER
    if not pointer.is_file():
        return None
    value = json.loads(pointer.read_text())
    if set(value) != {'version', 'path', 'sha256', 'published_at'}:
        raise ValueError('Rules pointer malformed')
    if value['path'] != relative_path(value['version']) or sha(Path(repo or REPO) / value['path']) != value['sha256']:
        raise ValueError('Rules pointer differs from the rules file bytes')
    return value


def stamp(card=None, repo=None):
    """``automation.rules`` for a card created now: the pointer's version, or None before the first publish."""
    pointer = current(repo)
    if pointer is None:
        return None
    return dict(version=pointer['version'], sha256=pointer['sha256'], path=pointer['path'])


def pinned(payload):
    """The raw ``automation.rules`` descriptor of a card payload, shape-checked, or None for an R0 card."""
    automation = payload.get('automation') if isinstance(payload, dict) else None
    pin = automation.get('rules') if isinstance(automation, dict) else None
    if pin is None:
        return None
    if (not isinstance(pin, dict) or set(pin) != DESCRIPTOR_KEYS or not all(isinstance(v, str) and v for v in pin.values())):
        raise ValueError('automation.rules must be {version, sha256, path}')
    number(pin['version'])
    if pin['path'] != relative_path(pin['version']) or re.fullmatch(r'[0-9a-f]{64}', pin['sha256']) is None:
        raise ValueError('automation.rules path or hash malformed')
    return pin


def pin_of_payload(payload):
    return pinned(payload)


def of_payload(payload, repo=None):
    """The validated rules a card pins (bytes re-hashed), None for an R0 card, ValueError when the pin does not match."""
    pin = pinned(payload)
    if pin is None:
        return None
    path = Path(repo or REPO) / pin['path']
    if not path.is_file() or sha(path) != pin['sha256']:
        raise ValueError('NEEDS_RULING: card rules pin differs from the rules file bytes: ' + pin['version'])
    value = load(path)
    if value['version'] != pin['version']:
        raise ValueError('Rules file version differs from the card pin')
    return value


_PIN_BY_CARD = {}


def of_row(pilot, row):
    """Rules of a registry row's card (None = R0). The parse is cached by card bytes; the rules bytes are re-checked every call."""
    key = (row.get('card'), row.get('card_sha256'))
    if key not in _PIN_BY_CARD:
        from .autopilot import read
        _PIN_BY_CARD[key] = pinned(read(row['card']) or {})
    if _PIN_BY_CARD[key] is None:
        return None
    return of_payload({'automation': {'rules': _PIN_BY_CARD[key]}}, pilot.repo)


def pin_of_row(row):
    """Descriptor (no file check) for ledger stamping; None for R0 rows and rows whose card cannot be read."""
    key = (row.get('card'), row.get('card_sha256'))
    if key not in _PIN_BY_CARD:
        if not row.get('card'):
            return None
        try:
            from .autopilot import read
            _PIN_BY_CARD[key] = pinned(read(row['card']) or {})
        except (OSError, ValueError, TypeError, KeyError):
            return None
    return _PIN_BY_CARD[key]


def chain(rules):
    return tuple(rules['chain'])


def at_least(rules, n):
    """True if ``rules`` (a validated document, or None for R0) is version Rn or later."""
    return rules is not None and number(rules['version']) >= n


def of_ctx(ctx):
    """Validated rules a context's card pins (None = R0)."""
    return of_payload(ctx.card.payload, ctx.repo)


def classify(rules, action, reason):
    """First park_reasons row whose gate list contains ``action`` (or ``*``) and one of whose strings is in ``reason``.

    A park record without an action (a requeue-park or a text copied from an older record) is matched on the text alone."""
    text = str(reason)
    for row in rules['park_reasons']:
        if ('*' in row['gates'] or action is None or action in row['gates']) and any(s in text for s in row['contains']):
            return row
    return None


def structural_floors(rules):
    """The floors of ``rules.structural`` in the shape of ``named_panel.FLOORS``; None for rules before R3."""
    if not at_least(rules, 3):
        return None
    value = rules['structural']
    return dict(max_removed_share_numerator=value['max_removed_share'][0], max_removed_share_denominator=value['max_removed_share'][1],
                min_treated=value['min_treated'], min_cohorts=value['min_cohorts'], min_controls=value['min_controls'],
                min_audited_retained=value['min_audited_retained'])


def audit_limits(payload, repo=None):
    rules = of_payload(payload, repo)
    if rules is None:
        return None
    if 'max_known_errors' not in rules['audit']:   # R2 has no audit limits: the collector's constants 2 / 5 apply
        return None
    return dict(max_known_errors=rules['audit']['max_known_errors'], max_unresolved=rules['audit']['max_unresolved'])


def reconcile_minimums(payload, repo=None):
    rules = of_payload(payload, repo)
    return None if rules is None else dict(rules['reconcile'])


def diff(a, b):
    """Sections that differ between two rules documents (park_reasons by row id)."""
    out = {}
    for key in sorted(set(a) | set(b)):
        if key == 'park_reasons':
            ra = {r['id']: r for r in a.get(key, [])}
            rb = {r['id']: r for r in b.get(key, [])}
            changes = {i: ('added' if i not in ra else 'removed' if i not in rb else 'changed')
                       for i in sorted(set(ra) | set(rb)) if ra.get(i) != rb.get(i)}
            if changes:
                out[key] = changes
        elif a.get(key) != b.get(key) and key not in {'version'}:
            out[key] = dict(old=a.get(key), new=b.get(key))
    return out


def publish(file, reason, repo=None, *, now=None):
    """Validate FILE, write it once as the next version, move the pointer atomically, ledger RULES_PUBLISHED."""
    from .autopilot import Autopilot, Backend, atomic, utc
    repo = Path(repo or REPO)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('publish needs a reason')
    value = validate(json.loads(Path(file).read_text()))
    version = value['version']
    target = rules_path(version, repo)
    previous = current(repo)
    expected = 1 if previous is None else number(previous['version']) + 1
    if target.exists():
        raise ValueError('Rules version already exists: ' + version)
    if number(version) != expected:
        raise ValueError('Next rules version must be R' + str(expected))
    old_value = load(repo / previous['path']) if previous else {}
    ensure_once(target, value)
    pointer = dict(version=version, path=relative_path(version), sha256=sha(target), published_at=now or utc())
    atomic(repo / POINTER, pointer)
    sections = diff(old_value, value)
    Autopilot(repo / 'data/l2_pipeline/registry', Backend(repo)).ledger({}, 'rules', 'RULES_PUBLISHED', 'record', version=version, sha256=pointer['sha256'],
                                                          reason=reason, sections=sorted(sections), diff=sections)
    return pointer


def validate_requeue(pilot, old, new_payload, decision):
    """Requeue hook for REDESIGN_R1: the new card is the old card plus ``automation.rules`` (and its version), nothing else."""
    from .autopilot import read
    pin = pinned(new_payload)
    if pin is None:
        raise ValueError('REDESIGN requeue needs automation.rules on the new card')
    new_rules = of_payload(new_payload, pilot.repo)
    if decision.get('implementation_rule') != 'REDESIGN_R1' or decision.get('rules') != pin or decision.get('decision') != 'resume':
        raise ValueError('REDESIGN_R1 ruling malformed')
    from .cards import load_card
    previous = load_card(Path(old['card'])).payload   # same normalisation as new_payload

    from .requeue import retained_reason
    reason = retained_reason(pilot, old)
    row = classify(new_rules, retained_action(pilot, old), reason)
    sibling = None   # R3 HISTORY classes: the one extra difference is automation.history_reuse naming the mechanical sibling
    if row is not None and row['reopen'] == 'check:certified_sibling':
        from .redesign import certified_sibling_source
        sibling = certified_sibling_source(pilot, old)

    def core(payload, link=False):
        value = deepcopy(payload)
        value.pop('version', None)
        automation = value.get('automation')
        if isinstance(automation, dict):
            automation.pop('rules', None)
            if link:
                automation.pop('history_reuse', None)
            if not automation:
                value.pop('automation')   # a card that had no automation block gains one only to carry the pin
        return value
    if sibling is None:
        if core(previous) != core(new_payload):
            raise ValueError('REDESIGN_R1 card may differ from the old card only in automation.rules and version')
    else:
        link = (new_payload.get('automation') or {}).get('history_reuse')
        from . import history_reuse
        if (link != dict(source_design_id=sibling, rule=history_reuse.RULING)
                or core(previous, True) != core(new_payload, True)):
            raise ValueError('REDESIGN_R1 card may differ from the old card only in automation.rules, version and the certified-sibling link')
    old_pin = pinned(previous)
    if old_pin is not None and number(old_pin['version']) >= number(pin['version']):
        raise ValueError('The design already runs under rules ' + old_pin['version'])
    if row is not None and row['reopen'].startswith('check:'):
        from .redesign import run_check
        if not run_check(row['reopen'][len('check:'):], pilot, old, new_rules)[0]:
            row = None
    elif row is not None and row['reopen'] != 'on_new_rules':
        row = None
    if row is None:
        raise ValueError('Retained stop/park reason is not reopenable under ' + pin['version'] + ': ' + str(reason)[:200])
    return row


def retained_action(pilot, old):
    from .autopilot import read
    record = pilot.stop_record(old) or read(pilot.case_dir(old) / 'PARKED.json', {}) or {}
    return record.get('action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('show')
    p = sub.add_parser('publish')
    p.add_argument('--file', required=True, type=Path)
    p.add_argument('--reason', required=True)
    d = sub.add_parser('diff')
    d.add_argument('a')
    d.add_argument('b')
    args = parser.parse_args(argv)
    if args.cmd == 'show':
        pointer = current()
        print(json.dumps(dict(pointer=pointer, rules=load(REPO / pointer['path']) if pointer else None), indent=2, sort_keys=True))
    elif args.cmd == 'publish':
        print(json.dumps(publish(args.file, args.reason), indent=2, sort_keys=True))
    else:
        print(json.dumps(diff(load(rules_path(args.a)), load(rules_path(args.b))), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
