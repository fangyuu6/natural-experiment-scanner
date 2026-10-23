"""Intake replay of S3 and the structural unit list on a card (HUB_RULING_20261017_REDESIGN section 4, window W3).

Outcome-blind: this module reads the card, the certified roster of a reused history (TREATMENT.json records) and the value-free
support sidecar. It opens no outcome value, runs no estimate and dispatches no agent.

* ``structural_list`` builds the units a card declares as ``treatment.excluded_units`` at build time (absent from the outcome series,
  adopted before the window, too few pre or post periods) with a reason per unit, and ``receipt`` hashes that list so a card carries
  ``automation.intake_replay``.
* ``replay`` runs the unchanged S3 check on sidecar keys with the structural list applied, and the floors of ``rules.structural``.
* ``check_card`` is the ingest hook for a card that pins R3 rules: ``CARD`` (proceeds), ``NEEDS_EXCLUSIONS`` (ingest refuses with the
  exact units to add), ``NOT_EVALUABLE`` (the card cannot be evaluated: ``S3_SIBLING: ...``), ``SKIPPED`` (no certified roster: the screening
  map and the prescreen decide as before). The park itself is the existing Gate 0 / prescreen one: ``prescreen.evaluate`` calls
  ``replay_certified`` for an R3 card that reuses a certified history and returns ``PRESCREEN_PARK`` with the same reason, which Gate 0
  records as GATE0_PARK at ingest (no agent packet, outside the L2 100, one in eight still runs the full chain as the audit).
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path


RULE = 'REDESIGN_W3_INTAKE_REPLAY'
SCHEMA = 'l2_intake_replay_v1'
PARK_PREFIX = 'S3_SIBLING: '
REASONS = ('adopted_before_window', 'too_few_pre_periods', 'too_few_post_periods', 'absent_from_outcome_series')


# --- the list a card carries ------------------------------------------------------------------------------------------------------

def structural_list(universe, series_units, adopters, window, thresholds, observed=None, transition_year_drop=True):
    """{unit: reason} for a card being built. ``universe`` and ``series_units`` are unit ids; ``adopters`` {unit: operative year};
    ``window`` (first, last) the outcome calendar; ``thresholds`` {min_pre_periods, min_post_periods}; ``observed`` the calendar years
    with any usable unit (all years when None). Same arithmetic as ``classify_states`` of the card assembler: pre/post counts use observed
    years only, as the S3 support check does."""
    first, last = window
    years = [y for y in range(first, last + 1) if observed is None or y in observed]
    result = {}
    for unit in universe:
        if unit not in series_units:
            result[unit] = 'absent_from_outcome_series'
    for unit, year in sorted(adopters.items()):
        if unit in result:
            continue
        pre = [y for y in years if y < year]
        post = [y for y in years if y >= year + (1 if transition_year_drop else 0)]
        if len(pre) < thresholds['min_pre_periods']:
            result[unit] = 'adopted_before_window' if year < first else 'too_few_pre_periods'
        elif len(post) < thresholds['min_post_periods']:
            result[unit] = 'too_few_post_periods'
    return dict(sorted(result.items()))


def receipt(excluded):
    """``automation.intake_replay`` for a card whose structural list is ``excluded`` = {unit: reason}."""
    if not isinstance(excluded, dict) or any(r not in REASONS for r in excluded.values()):
        raise ValueError('intake_replay reasons must be one of ' + ', '.join(REASONS))
    listed = dict(sorted(excluded.items()))
    digest = hashlib.sha256(json.dumps(dict(schema=SCHEMA, rule=RULE, excluded_units=listed), sort_keys=True).encode()).hexdigest()
    return dict(rule=RULE, excluded_units=listed, receipt_sha256=digest)


def declared(payload):
    """The card's receipt, shape- and hash-checked, or None. A card that lists structural units must list them in
    ``treatment.excluded_units`` too; an edited list raises."""
    value = (payload.get('automation') or {}).get('intake_replay')
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'rule', 'excluded_units', 'receipt_sha256'} or value['rule'] != RULE:
        raise ValueError('automation.intake_replay malformed')
    if receipt(value['excluded_units']) != value:
        raise ValueError('automation.intake_replay hash differs from its list')
    missing = sorted(set(value['excluded_units']) - set(payload.get('treatment', {}).get('excluded_units', [])))
    if missing:
        raise ValueError('automation.intake_replay names units missing from treatment.excluded_units: ' + ', '.join(missing))
    return value


# --- the replay -------------------------------------------------------------------------------------------------------------------

def series_keys(payload, repo):
    """(eligible (unit, period) keys, units present in the outcome series) of the card's primary outcome, from the value-free sidecar.
    Without a sidecar: the optimistic complete rectangle over the declared universe (it can only prove a roster geometry failure)."""
    from . import prescreen
    from .support import declared_annual_periods
    from .variants import registered_cards
    primary = registered_cards(deepcopy(payload))['primary']
    spec = prescreen.catalog_spec(primary['outcome'], repo) or {}
    if not spec.get('support_artifact') and primary['outcome'].get('support_path'):
        spec = dict(spec, series_id=primary['outcome']['series_id'],
                    support_artifact=dict(path=primary['outcome']['support_path'], sha256=primary['outcome']['support_sha256']))
    if spec.get('support_artifact'):
        return prescreen.flags(spec, repo), prescreen.sidecar_units(spec, repo)
    present = set(primary['treatment'].get('universe', []))
    years = declared_annual_periods(primary['outcome'])
    return {(u, y) for u in present for y in years}, present


def replay(payload, records, present, keys, floors):
    """Pure decision on a card's roster (the certified ``records``, unfiltered), the units present in the series and the eligible keys.

    {status, excluded (the full list applied), missing (structural units the card does not list, with reasons), figures, failures,
    issues, replay_passed}. ``status`` CARD, NEEDS_EXCLUSIONS or NOT_EVALUABLE. The floors apply only when a structural list exists
    (declared on the card or computed here): a card with no structural unit is judged by the unchanged S3 check alone."""
    from . import named_panel
    computed, details = named_panel.structural_exclusions(payload, records, sorted(present))
    listed = declared(payload)
    structural = set(computed) | (set(listed['excluded_units']) if listed else set())
    applied = set(payload['treatment'].get('excluded_units', [])) | set(computed)
    result = named_panel.s3_replay_keys(payload, records, applied, keys)
    figures, failures = named_panel.check_floors(records, applied, None, result.get('comparison_controls', []), floors)
    issues = sorted(result.get('issues', {}))
    out = dict(excluded=sorted(applied), structural=sorted(structural), missing={u: computed[u]['reason'] for u in sorted(computed)},
               figures=figures, failures=failures if structural else [], issues=issues, replay_passed=bool(result.get('passed')),
               details=details, outcome_values_read=0)
    if not result.get('passed'):
        return dict(out, status='NOT_EVALUABLE', reason=PARK_PREFIX + (', '.join(issues) or 'S3 support failed'))
    if structural and failures:
        return dict(out, status='NOT_EVALUABLE', reason=PARK_PREFIX + '; '.join(failures))
    if computed:
        return dict(out, status='NEEDS_EXCLUSIONS',
                    reason='card lacks structural exclusions: ' + ', '.join('%s (%s)' % (u, computed[u]['reason']) for u in sorted(computed)))
    return dict(out, status='CARD', reason=None)


def replay_certified(payload, certified, repo, rules):
    """``replay`` for a card that reuses a certified history: the source's own exclusions are applied on top of the card's, the
    sidecar keys come from the card's primary outcome. ``rules`` is the card's validated R3 (or later) rules document."""
    from . import rules as rules_module
    effective = deepcopy(payload)
    effective['treatment']['excluded_units'] = sorted(set(effective['treatment'].get('excluded_units', [])) | set(certified['excluded_units']))
    keys, present = series_keys(effective, repo)
    value = replay(effective, certified['records'], present, keys, rules_module.structural_floors(rules))
    return dict(value, source_design_id=certified['source_design_id'])


def check_card(pilot, path, payload):
    """Ingest hook. Only a card that pins R3 rules and names a certified, ready source is replayed; everything else is SKIPPED."""
    from . import history_reuse, rules as rules_module
    rules = rules_module.of_payload(payload, pilot.repo)
    if not rules_module.at_least(rules, 3):
        return dict(status='SKIPPED', why='the card does not pin R3 or later rules')
    declared(payload)   # a tampered list is an error, not a skip
    certified = history_reuse.certified_roster(pilot, dict(design_id=payload['design_id'], card=str(path)))
    if certified is None:
        return dict(status='SKIPPED', why='no certified source roster is available yet')
    return replay_certified(payload, certified, pilot.repo, rules)
