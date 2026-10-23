"""NAMED_PANEL Q1 (2026-10-16): structural exclusions on the cards that stopped at S3 (fix 4, option B).

Hub ruling: docs/plans/l2-pipeline/HUB_RULING_20261016_NAMED_PANEL.md, Q1. For every card parked at S3 with
"Registered support insufficient", by script and the same rule for all of them:

* ``treatment.excluded_units`` gets exactly the treated or control states that (i) adopted before the registered
  window, (ii) have too few pre- or post-periods for the registered window, or (iii) are absent from the outcome
  series. The list is computed from the S1-certified TREATMENT.json, the registered calendar and the state list of
  the outcome metadata, with every data cell treated as present: a suppressed, unreliable or unavailable flag never
  removes a state (``support_gap_rule`` is not set).
* Floors, all required: at most 1/3 of the registered treated states removed; at least 10 treated states in at least
  3 cohorts; at least 5 control states; at least 5 of the 10 audited states retained. The RI feasibility gate is
  not touched.
* A design that clears the floors is requeued through the existing requeue route (new card version, status
  SUPERSEDED_NAMED_PANEL_Q1) only if the unchanged S3 check, replayed on metadata alone, passes with the exclusions.
  This is the last support rescue: one shot per design, no change of window, outcome or thresholds.
* Wording: "added after the S3 stop, before any forecast or outcome calculation" (never "pre-registered"). Each design
  exports its excluded-state list with the reason per state; rows carry ``route = post_stop_structural``.

Nothing here opens an outcome value: only TREATMENT.json, the card, and the unit/year/flag columns of metadata.csv.
"""
from copy import deepcopy
from pathlib import Path

from .storage import ensure_once, sha

RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261016_NAMED_PANEL.md'
STATEMENT = 'added after the S3 stop, before any forecast or outcome calculation'
SUPERSEDED = 'SUPERSEDED_NAMED_PANEL_Q1'
PARK_REASON = 'S3 Disposition.STOPPED: Registered support insufficient'
ROUTE = 'post_stop_structural'
FLOORS = dict(max_removed_share_numerator=1, max_removed_share_denominator=3, min_treated=10, min_cohorts=3,
              min_controls=5, min_audited_retained=5)
REASONS = ('adopted_before_window', 'too_few_pre_periods', 'too_few_post_periods', 'absent_from_outcome_series')


def bind(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


# --- class membership ---------------------------------------------------------------------------

def s3_stopped_rows(pilot):
    """Active designs whose current attempt is parked at S3 for insufficient registered support, not yet rescued."""
    from .autopilot import read
    rows = []
    for row in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs']:
        park = read(pilot.case_dir(row) / 'PARKED.json', {})
        if park.get('action') != 'S3' or park.get('reason') != PARK_REASON:
            continue
        card = read(row['card'])
        if (card.get('analysis') or {}).get('support_gap_rule') or (card.get('automation') or {}).get('named_panel_q1'):
            continue  # V7 already registered, or this rescue already spent
        rows.append(row)
    return rows


# --- structural computation ---------------------------------------------------------------------

def structural_exclusions(payload, records, series_units):
    """({unit: {reason, status, start_period}}, details). Every data cell of the series states is taken as present."""
    from .support import V7_SUPPORT_GAP_RULE, support_check, declared_annual_periods
    from .variants import registered_cards
    from .stages.s3_support import variant_treatment
    primary = registered_cards(dict(payload))['primary']
    outcome = primary['outcome']
    tyd = primary.get('transition_year_drop', False)
    roster = variant_treatment(records, primary)
    present = set(series_units)
    analysis = dict(primary['analysis'], support_gap_rule=V7_SUPPORT_GAP_RULE)
    if outcome.get('frequency') == 'biennial':
        from .biennial import periods, support as biennial_support
        calendar = periods(outcome)
        keys = {(u, t) for u in present for t in calendar}
        summary = biennial_support(keys, roster, analysis, outcome, tyd)
    else:
        calendar = declared_annual_periods(outcome)
        keys = {(u, t) for u in present for t in calendar}
        summary = support_check(keys, roster, analysis, period_years=calendar, transition_year_drop=tyd)
    by_unit = {r['unit']: r for r in roster}
    result = {}
    first = calendar[0]
    for unit, row in by_unit.items():
        if unit not in present:
            result[unit] = dict(reason='absent_from_outcome_series')
    for drop in summary.get('support_gap_drops', []):
        unit = drop['unit']
        if unit in result:
            continue
        text = drop['reason']
        if text == 'Insufficient registered clean pre-periods':
            start = by_unit[unit].get('start_period')
            result[unit] = dict(reason='adopted_before_window' if start is not None and start < first else 'too_few_pre_periods')
        elif text == 'No registered post-period support':
            result[unit] = dict(reason='too_few_post_periods')
        else:
            raise ValueError('Unexpected structural drop reason: ' + text)  # a data-gap drop cannot occur on a full grid
    for drop in summary.get('post_support_drops', []):
        for unit in drop['members']:
            result.setdefault(unit, dict(reason='too_few_post_periods'))
    for unit, item in result.items():
        item.update(status=by_unit[unit]['status'], start_period=by_unit[unit].get('start_period'))
    return result, dict(calendar=[calendar[0], calendar[-1]], periods=len(calendar), transition_year_drop=bool(tyd),
                        adapter=summary.get('adapter'), registered_roster=len(roster))


def check_floors(records, excluded, sample, controls, floors=None):
    """Floors of the ruling, on the registered roster (TREATMENT.json records) minus the structural list.

    ``controls`` are the states that serve as comparison units in the replayed S3 geometry (never-treated states, or
    not-yet-treated ones when the card compares with those). ``floors`` default to ``FLOORS`` (the Q1/Q2 behaviour);
    R3 cards pass ``rules.structural_floors``. ``sample=None`` (no fresh audit sample, a reused certified history) skips
    the audited-retained floor."""
    floors = FLOORS if floors is None else floors
    treated = [r for r in records if r['status'] == 'treated']
    removed = [r for r in treated if r['unit'] in excluded]
    kept = [r for r in treated if r['unit'] not in excluded]
    audited = [] if sample is None else [u for u in sample if u not in excluded]
    figures = dict(registered_treated=len(treated), removed_treated=len(removed), retained_treated=len(kept),
                   retained_cohorts=len({r['start_period'] for r in kept}), retained_controls=len(controls),
                   audited_retained=None if sample is None else len(audited), audited_total=None if sample is None else len(sample))
    failures = []
    if floors['max_removed_share_denominator'] * len(removed) > floors['max_removed_share_numerator'] * len(treated):
        failures.append('more than %d/%d of the registered treated states removed' % (floors['max_removed_share_numerator'],
                                                                                         floors['max_removed_share_denominator']))
    if len(kept) < floors['min_treated']:
        failures.append('fewer than %d treated states retained' % floors['min_treated'])
    if figures['retained_cohorts'] < floors['min_cohorts']:
        failures.append('fewer than %d cohorts retained' % floors['min_cohorts'])
    if len(controls) < floors['min_controls']:
        failures.append('fewer than %d control states' % floors['min_controls'])
    if sample is not None and len(audited) < floors['min_audited_retained']:
        failures.append('fewer than %d of the audited states retained' % floors['min_audited_retained'])
    return figures, failures


def s3_replay(payload, records, excluded, metadata_path):
    """The unchanged S3 primary check on metadata alone, with the named units excluded (S3 itself reads no values)."""
    from .engine.panels import metadata_eligible_keys
    return s3_replay_keys(payload, records, excluded, metadata_eligible_keys(metadata_path))


def s3_replay_keys(payload, records, excluded, keys):
    """``s3_replay`` on an explicit set of eligible (unit, period) keys (the value-free sidecar flags at intake)."""
    from .stages.s3_support import variant_treatment
    from .support import support_check, declared_annual_periods
    from .variants import registered_cards
    card = dict(payload)
    card['treatment'] = dict(card['treatment'], excluded_units=sorted(set(card['treatment'].get('excluded_units', [])) | set(excluded)))
    primary = registered_cards(card)['primary']
    treatment = variant_treatment(records, primary)
    if primary['outcome'].get('frequency') == 'biennial':
        from .biennial import support as biennial_support
        return biennial_support(keys, treatment, primary['analysis'], primary['outcome'], primary.get('transition_year_drop', False))
    return support_check(keys, treatment, primary['analysis'], period_years=declared_annual_periods(primary['outcome']),
                         transition_year_drop=primary.get('transition_year_drop', False))


def plan_design(pilot, row):
    """The complete, deterministic Q1 decision for one design (no writes)."""
    from .autopilot import read
    from .engine.panels import metadata_keys
    ctx = pilot.backend.ctx(row)
    root = ctx.design_dir
    payload = dict(ctx.card.payload)
    treatment = read(root / 'TREATMENT.json')
    support = read(root / 'SUPPORT.json')
    primary_support = next(v for v in support['variants'] if v['variant'] == 'primary')
    metadata = root / 'panels/primary/metadata.csv'
    if support.get('outcome_values_parsed') != 0 or primary_support.get('passed') is not False:
        raise ValueError('Q1 applies to an S3 stop that parsed no outcome values')
    records = treatment['records']
    series_units = sorted({u for u, _ in metadata_keys(metadata)})
    excluded, details = structural_exclusions(payload, records, series_units)
    baseline = s3_replay(payload, records, [], metadata)
    baseline_matches = (baseline.get('passed') is False and baseline.get('issues') == primary_support.get('issues')
                        and baseline.get('post_cells') == primary_support.get('post_cells'))
    replay = s3_replay(payload, records, excluded, metadata) if excluded else None
    figures, failures = check_floors(records, excluded, treatment['sample'], [] if replay is None else replay.get('comparison_controls', []))
    decision, why = 'stay_stopped', []
    if not excluded:
        why.append('no structural exclusion applies; the S3 stop does not come from the roster')
    else:
        why.extend(failures)
        if not baseline_matches:
            why.append('the replay without exclusions does not reproduce the stored S3 result')
        if replay is not None and not replay.get('passed'):
            why.append('the unchanged S3 check still fails with the structural list: ' + ', '.join(sorted(replay.get('issues', {}))))
        if not why:
            decision = 'requeue'
    return dict(design_id=row['design_id'], attempt_id=row['attempt_id'], card_version=read(row['card'])['version'],
                decision=decision, stay_stopped_because=why, excluded={u: excluded[u] for u in sorted(excluded)},
                figures=figures, details=details, baseline_replay_reproduces_stored_s3=baseline_matches,
                replay_passed=None if replay is None else bool(replay.get('passed')),
                replay_issues={} if replay is None else replay.get('issues', {}),
                replay_retained_treated=None if replay is None else len(replay.get('retained_treated', [])),
                outcome_values_read=0)


# --- card, ruling, application ------------------------------------------------------------------

def build_card(raw_card, plan, version, ruling_ref, class_ref):
    """The next card version: the prior card plus the structural list and its record; nothing else changes."""
    card = deepcopy(raw_card)
    card['version'] = version
    excluded = sorted(set(card['treatment'].get('excluded_units', [])) | set(plan['excluded']))
    card['treatment']['excluded_units'] = excluded
    for entry in card.get('sensitivities', []):
        listed = entry.get('changes', {}).get('treatment', {})
        if 'excluded_units' in listed:
            listed['excluded_units'] = sorted(set(listed['excluded_units']) | set(plan['excluded']))
    figures = plan['figures']
    card.setdefault('automation', {})['named_panel_q1'] = dict(
        rule='NAMED_PANEL_Q1', statement=STATEMENT, route=ROUTE, ruling=ruling_ref, class_ruling=class_ref,
        prior_card_version=raw_card['version'], excluded_units={u: v['reason'] for u, v in sorted(plan['excluded'].items())},
        k_of_n='%d of %d registered treated states' % (figures['removed_treated'], figures['registered_treated']),
        estimand='adopters inside the registered window', last_support_rescue=True, execution_authorized=False)
    return card


def next_version(pilot, row):
    from .autopilot import read
    number = int(read(row['card'])['version'][1:])
    taken = {p.name for p in (pilot.registry.parent / 'autopilot/work' / row['design_id']).glob('v*')}
    taken |= {p.name for p in (pilot.registry.parent / 'autopilot/requeues' / row['design_id']).glob('v*')}
    while 'v' + str(number + 1) in taken:
        number += 1
    return 'v' + str(number + 1)


def class_ruling(plans):
    return dict(schema='l2_named_panel_q1_class_ruling_v1', date='2026-10-16', statement=STATEMENT, route=ROUTE,
                floors=FLOORS, authority_file=RULING, scope='every active design parked at S3 for insufficient registered support, no V7 rule, no prior Q1',
                outcome_values_read=0, forecasts_or_estimates_existing=0, last_support_rescue=True,
                designs=[dict(design_id=p['design_id'], decision=p['decision'], excluded=sorted(p['excluded']),
                              stay_stopped_because=p['stay_stopped_because'], k_of_n='%d of %d' % (
                                  p['figures']['removed_treated'], p['figures']['registered_treated'])) for p in plans])


def plan_all(pilot):
    return [plan_design(pilot, row) for row in sorted(s3_stopped_rows(pilot), key=lambda r: r['design_id'])]


def validate_q1_ruling(pilot, old, new_payload, decision, card_path):
    """Requeue hook: the new card must be exactly what the script derives from the old card, today, from the same files."""
    from .autopilot import read
    from .population_resolution import rebase_sources
    authority = Path(pilot.repo) / RULING
    if (decision.get('authority') != bind(authority) or decision.get('execution_authorized') is not False
            or decision.get('superseded_status') != SUPERSEDED or decision.get('purpose_rule') != 'V6.2'
            or decision.get('decision') != 'accept'):
        raise ValueError('NAMED_PANEL Q1 ruling malformed')
    class_ref = decision.get('class_ruling')
    if not class_ref or sha(class_ref['path']) != class_ref['sha256']:
        raise ValueError('NAMED_PANEL Q1 class ruling bytes changed')
    if not any(r['design_id'] == old['design_id'] for r in s3_stopped_rows(pilot)):
        raise ValueError('Design is not an unrescued S3-stopped card')
    plan = plan_design(pilot, old)
    if plan['decision'] != 'requeue':
        raise ValueError('NAMED_PANEL Q1 floors or replay do not pass: ' + '; '.join(plan['stay_stopped_because']))
    listed = next(d for d in read(class_ref['path'])['designs'] if d['design_id'] == old['design_id'])
    if listed['excluded'] != sorted(plan['excluded']) or listed['decision'] != 'requeue':
        raise ValueError('Class ruling list differs from the recomputed list')
    expected = rebase_sources(build_card(read(old['card']), plan, read(card_path)['version'], bind(authority), class_ref), old['card'])
    if read(card_path) != expected:
        raise ValueError('NAMED_PANEL Q1 card differs from the scripted card')
    return plan


def export(plan, new_version=None, requeued=False):
    return dict(schema='l2_named_panel_q1_export_v1', design_id=plan['design_id'], route=ROUTE, statement=STATEMENT,
                prior_card_version=plan['card_version'], new_card_version=new_version, requeued=requeued,
                decision=plan['decision'], stay_stopped_because=plan['stay_stopped_because'],
                k_of_n='%d of %d registered treated states' % (plan['figures']['removed_treated'], plan['figures']['registered_treated']),
                excluded_states=[dict(unit=u, **v) for u, v in sorted(plan['excluded'].items())], figures=plan['figures'],
                replay_passed=plan['replay_passed'], replay_issues=plan['replay_issues'], outcome_values_read=0)


def apply(pilot=None):
    """Window step: record the ruling in the ledger, then requeue each passing design. Idempotent."""
    from .autopilot import Autopilot, read
    from .c70_l3 import protected
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/named_panel/q1'
    with guard(p.registry):
        if not (p.registry / 'AUTOPILOT_STOP').exists() or read(p.registry / 'CODE_TASK.lock', {}).get('task') != 'NAMED_PANEL':
            raise ValueError('NAMED_PANEL stopped code window required')
        clean_stop(p.repo)
        application = out / 'APPLICATION.json'
        if application.exists():
            return read(application)
        plans = plan_all(p)
        authority = p.repo / RULING
        class_path = ensure_once(out / 'CLASS_RULING.json', class_ruling(plans))
        # The ruling and its hashes are in the ledger before any requeue exists.
        p.ledger({}, 'named_panel_q1', 'RULING_RECORDED', 'record', ruling=bind(authority), class_ruling=bind(class_path),
                 designs=len(plans), requeue_candidates=sum(x['decision'] == 'requeue' for x in plans))
        class_ref, ruling_ref = bind(class_path), bind(authority)
        results = []
        by_id = {r['design_id']: r for r in read(p.registry / 'ACTIVE_DESIGNS.json')['designs']}
        for plan in plans:
            row = by_id[plan['design_id']]
            if plan['decision'] != 'requeue' or protected(p.backend.root(row)):
                ensure_once(out / 'exports' / (plan['design_id'] + '.json'), export(plan))
                continue
            version = next_version(p, row)
            card = ensure_once(out / 'cards' / plan['design_id'] / 'CARD.json',
                               build_card_rebased(read(row['card']), row['card'], plan, version, ruling_ref, class_ref))
            ruling = ensure_once(out / 'rulings' / (plan['design_id'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=plan['design_id'], prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='accept', purpose_rule='V6.2', implementation_rule='NAMED_PANEL_Q1', superseded_status=SUPERSEDED,
                impact_tags=['s3_support'], authority=ruling_ref, class_ruling=class_ref,
                reason='NAMED_PANEL Q1: structural exclusions on an S3-stopped card; ' + STATEMENT + '; last support rescue',
                execution_authorized=False))
            receipt = requeue(plan['design_id'], card, ruling, p)
            ensure_once(out / 'exports' / (plan['design_id'] + '.json'), export(plan, version, True))
            results.append(dict(design_id=plan['design_id'], new_version=version, restart=receipt['restart_action'],
                                new_attempt=receipt['new_row']['attempt_id']))
        value = dict(schema='l2_named_panel_q1_application_v1', ruling=ruling_ref, class_ruling=class_ref, designs=len(plans),
                     requeued=results, stayed_stopped=[x['design_id'] for x in plans if x['decision'] != 'requeue'],
                     dispatches=0, actual_data_estimation_calls=0, outcome_values_read=0)
        ensure_once(application, value)
        return value


def build_card_rebased(raw, old_card_path, plan, version, ruling_ref, class_ref):
    from .population_resolution import rebase_sources
    return rebase_sources(build_card(raw, plan, version, ruling_ref, class_ref), old_card_path)


# --- Q2 (HUB_RULING_20261016_INTAKE_PANEL, 2026-10-16): the same Q1 rule, once, for the S3 parks created after the Q1 window ------

Q2_RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261016_INTAKE_PANEL.md'
Q2_AFTER = '2026-10-16T06:28:15.971027+00:00'   # CLEAN_STOP that opened the NAMED_PANEL window (Q1 saw every park before it)
Q2_TASK = 'WINDOW_20261017'
# Hub note 2026-10-17 ~02:35Z: the scope is frozen when the code window opens (the time written into CODE_TASK.lock by
# begin_code_task), not at a fixed clock time, so designs that parked between 21:00Z and the window are inside the rule.


def s3_park_times(pilot):
    """{(design_id, attempt_id): time of the latest S3 PARK event} from the ledger (the park marker has no time)."""
    import json
    times = {}
    with (pilot.registry / 'AUTOPILOT_LEDGER.jsonl').open() as stream:
        for line in stream:
            event = json.loads(line)
            if event.get('decision') == 'PARK' and event.get('action') == 'S3':
                times[(event.get('design_id'), event.get('attempt_id'))] = event['time']
    return times


def q2_cut(pilot, until=None):
    """The frozen end of the Q2 scope: the open time of the WINDOW_20261017 code window (CODE_TASK.lock 'time').

    ``until`` is only for a read-only preview outside the window (an explicit ISO time); without it the lock is required."""
    from datetime import datetime
    from .autopilot import read
    if until is not None:
        return datetime.fromisoformat(until).isoformat()
    lock = read(pilot.registry / 'CODE_TASK.lock', {})
    if lock.get('task') != Q2_TASK or not lock.get('time'):
        raise ValueError(Q2_TASK + ' code-task lock with its open time required')
    return datetime.fromisoformat(lock['time']).isoformat()


def q2_rows(pilot, until=None):
    """The Q1 class (unrescued S3 support parks) restricted to parks filed after the Q1 window and up to the window-open cut."""
    from datetime import datetime
    times = s3_park_times(pilot)
    after, until = datetime.fromisoformat(Q2_AFTER), datetime.fromisoformat(q2_cut(pilot, until))
    rows = []
    for row in s3_stopped_rows(pilot):
        stamp = times.get((row['design_id'], row['attempt_id']))
        if stamp and after < datetime.fromisoformat(stamp) <= until:
            rows.append(row)
    return rows


def plan_q2(pilot, until=None):
    return [plan_design(pilot, row) for row in sorted(q2_rows(pilot, until), key=lambda r: r['design_id'])]


def q2_class_ruling(plans, until):
    value = class_ruling(plans)
    value.update(schema='l2_named_panel_q2_class_ruling_v1', date='2026-10-17',
                 scope='every active design whose S3 support park was filed after %s (and up to the window-open time %s); scope correction of the Q1 class, '
                       'not a new rescue; no further S3 rescue after this run' % (Q2_AFTER, until),
                 scope_frozen_at=until, scope_authority_file=Q2_RULING)
    return value


def apply_q2(pilot=None):
    """Window step: Q1's rule on the post-window S3 parks, once. Ledger record first; requeue only designs that pass."""
    from .autopilot import Autopilot, read
    from .c70_l3 import protected
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/named_panel/q2'
    with guard(p.registry):
        if not (p.registry / 'AUTOPILOT_STOP').exists() or read(p.registry / 'CODE_TASK.lock', {}).get('task') != Q2_TASK:
            raise ValueError(Q2_TASK + ' stopped code window required')
        clean_stop(p.repo)
        application = out / 'APPLICATION.json'
        if application.exists():
            return read(application)
        until = q2_cut(p)
        plans = plan_q2(p, until)
        authority, scope = p.repo / RULING, p.repo / Q2_RULING
        class_path = ensure_once(out / 'CLASS_RULING.json', q2_class_ruling(plans, until))
        p.ledger({}, 'named_panel_q2', 'RULING_RECORDED', 'record', ruling=bind(authority), scope_ruling=bind(scope),
                 class_ruling=bind(class_path), designs=len(plans), requeue_candidates=sum(x['decision'] == 'requeue' for x in plans))
        class_ref, ruling_ref = bind(class_path), bind(authority)
        results = []
        by_id = {r['design_id']: r for r in read(p.registry / 'ACTIVE_DESIGNS.json')['designs']}
        for plan in plans:
            row = by_id[plan['design_id']]
            if plan['decision'] != 'requeue' or protected(p.backend.root(row)):
                ensure_once(out / 'exports' / (plan['design_id'] + '.json'), export(plan))
                continue
            version = next_version(p, row)
            card = ensure_once(out / 'cards' / plan['design_id'] / 'CARD.json',
                               build_card_rebased(read(row['card']), row['card'], plan, version, ruling_ref, class_ref))
            ruling = ensure_once(out / 'rulings' / (plan['design_id'] + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=plan['design_id'], prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='accept', purpose_rule='V6.2', implementation_rule='NAMED_PANEL_Q1', superseded_status=SUPERSEDED,
                impact_tags=['s3_support'], authority=ruling_ref, class_ruling=class_ref, scope_ruling=bind(scope),
                reason='NAMED_PANEL Q1 rule applied once to the S3 parks filed after the Q1 window (INTAKE_PANEL Q2); '
                       + STATEMENT + '; last support rescue',
                execution_authorized=False))
            receipt = requeue(plan['design_id'], card, ruling, p)
            ensure_once(out / 'exports' / (plan['design_id'] + '.json'), export(plan, version, True))
            results.append(dict(design_id=plan['design_id'], new_version=version, restart=receipt['restart_action'],
                                new_attempt=receipt['new_row']['attempt_id']))
        value = dict(schema='l2_named_panel_q2_application_v1', ruling=ruling_ref, scope_ruling=bind(scope), class_ruling=class_ref,
                     designs=len(plans), requeued=results, stayed_stopped=[x['design_id'] for x in plans if x['decision'] != 'requeue'],
                     dispatches=0, actual_data_estimation_calls=0, outcome_values_read=0)
        ensure_once(application, value)
        return value


C42_STOP = 'ValueError: C42 overlay removed existing exclusions'
C64_STOP = 'ValueError: S4 Disposition.FAILED: Purpose direction requires S0 evidence bound to the target'
RECOVERABLE = {C42_STOP: ('inspect', 'code_fix_named_panel_c42',
                          'c42.effective_context accepts structural exclusions listed in automation.named_panel_q1'),
               C64_STOP: ('S4', 'code_fix_named_panel_c64',
                          'c64.contract treats an unconfirmed researcher purpose-direction placeholder as a missing coding, not zero')}


def recover_c42_stops(pilot=None):
    """Window step after a code fix: clear the STOPs the first application of Q1 produced, one exact reason each.

    Found after the first application: (1) the retained C42 overlay did not list the added structural exclusions
    (stop at 'inspect'); (2) one legacy card carried an unconfirmed researcher purpose-direction placeholder (stop at
    S4). With the fixes the contexts build; each STOP is superseded through the existing C62 route (evidence file, hub
    queue entry, ledger) and nothing is dispatched. Idempotent; only an exact-reason STOP of a Q1 attempt.
    """
    from .autopilot import Autopilot, read
    from .c62_apply import supersede
    from .ops.watchdog import guard
    p = pilot or Autopilot()
    cleared = []
    with guard(p.registry):
        if not (p.registry / 'AUTOPILOT_STOP').exists() or not read(p.registry / 'CODE_TASK.lock', {}).get('task', '').startswith('NAMED_PANEL'):
            raise ValueError('NAMED_PANEL stopped code window required')
        for row in read(p.registry / 'ACTIVE_DESIGNS.json')['designs']:
            stop = p.stop_record(row)
            if not stop or stop.get('reason') not in RECOVERABLE:
                continue
            action, tag, fix = RECOVERABLE[stop['reason']]
            if stop.get('action') != action or not (read(row['card']).get('automation') or {}).get('named_panel_q1'):
                continue
            p.backend.ctx(row)  # raises if the context still does not build
            evidence = ensure_once(p.case_dir(row) / ('CODE_FIX_NAMED_PANEL_' + tag.rsplit('_', 1)[-1] + '.json'), dict(
                schema='l2_code_fix_supersession_v1', code_task='NAMED_PANEL_FIX', stop=stop, impact_tags=[tag], fix=fix,
                dispatches=0, execution_authorized=False, outcome_values_read=0))
            case = p.case_dir(row)
            # The first supersession of an attempt is C62's; a later stop of the same attempt uses C84's slot.
            suffix = 'C62' if not (case / 'STOP_SUPERSEDED_C62.json').exists() else 'C84'
            if suffix == 'C84' and (case / 'STOP_SUPERSEDED_C84.json').exists():
                continue
            supersede(p, row, evidence, [tag], suffix=suffix)
            cleared.append(row['design_id'])
    return cleared


def preview(pilot=None):
    """Read-only: the plans and the floors for every design in the class."""
    from .autopilot import Autopilot
    plans = plan_all(pilot or Autopilot())
    return dict(class_ruling=class_ruling(plans), plans=plans)


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(apply() if sys.argv[1:] == ['apply'] else recover_c42_stops() if sys.argv[1:] == ['recover'] else preview(), indent=2, default=str))
