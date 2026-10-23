"""T226(c): outcome-blind S3 count-floor bound, applied before any history packet exists.

Question asked: even with a PERFECT history (every unit that could be treated is treated at the best possible year, every other
unit is a clean control), can the declared outcome/support data reach the minimum treated / cohort / control counts the card
is held to? If not, no history packet can change the result, so the design parks at the named gate S3_COUNT_FLOOR before
any packet is spent (it rides the existing Gate 0 receipt, so it is reported separately, excluded from the 100, and the same
1-in-8 audit lottery sends a few through the full chain).

What it is NOT: it never reads an outcome value, never uses an adoption list (Gate 0's C62 prescreen already screens the
card's own list), and never changes a floor. Floors are exactly those in force for the card:
  * the card's own analysis thresholds, never below the engine minimums (support.ENGINE_MINIMUMS), and
  * the R3 structural floors (rules.structural_floors) when the card pins rules R3 or later.
A card with no usable support sidecar uses the optimistic complete rectangle (universe x declared years); it can then only
prove failure, never success, like the prescreen.
"""
from __future__ import annotations

from copy import deepcopy

GATE = 'S3_COUNT_FLOOR'


def floors_in_force(analysis, rules=None):
    from .support import ENGINE_MINIMUMS, minimum_post_periods
    out = {}
    for key, engine in ENGINE_MINIMUMS.items():
        value = analysis.get(key, engine)
        out[key] = max(engine, value) if type(value) is int else engine
    out['min_post_periods'] = minimum_post_periods(analysis)
    out['source'] = 'card'
    if rules is not None:
        from . import rules as rules_module
        structural = rules_module.structural_floors(rules)
        if structural:
            for key in ('min_treated', 'min_cohorts', 'min_controls'):
                out[key] = max(out[key], structural[key])
            out['source'] = 'card+rules_' + rules['version']
    return out


def perfect_history_figures(universe, years, usable, floors, *, transition_year_drop=False, controls='never_treated'):
    """Pure bound. ``usable`` is a set of (unit, year) with usable outcome support.

    A unit can be treated at start year s if it has at least min_pre_periods usable years before s and at least
    min_post_periods usable years from s on (from s+1 when the transition year is dropped). It can be a control if it has at
    least min_pre_periods + min_post_periods usable years in all. Treated and never-treated controls are disjoint units.
    """
    pre_n, post_n = floors['min_pre_periods'], floors['min_post_periods']
    by_unit = {}
    for unit, year in usable:
        if unit in universe:
            by_unit.setdefault(unit, set()).add(year)
    years = sorted(years)
    starts_of = {}
    for unit, have in by_unit.items():
        ok = []
        for s in years:
            before = sum(1 for y in have if y < s)
            after = sum(1 for y in have if y > s) if transition_year_drop else sum(1 for y in have if y >= s)
            if before >= pre_n and after >= post_n:
                ok.append(s)
        if ok:
            starts_of[unit] = ok
    n_usable = sum(1 for have in by_unit.values() if len(have) >= pre_n + post_n)
    treatable = len(starts_of)
    distinct_starts = len({s for ok in starts_of.values() for s in ok})
    cohorts_max = min(treatable, distinct_starts)
    need_units = floors['min_treated'] + (floors['min_controls'] if controls == 'never_treated' else 0)
    reasons = []
    if treatable < floors['min_treated']:
        reasons.append('treatable units %d < min_treated %d' % (treatable, floors['min_treated']))
    if cohorts_max < floors['min_cohorts']:
        reasons.append('possible cohorts %d < min_cohorts %d' % (cohorts_max, floors['min_cohorts']))
    if controls == 'never_treated':
        if n_usable - min(treatable, floors['min_treated']) < floors['min_controls'] or n_usable < need_units:
            reasons.append('usable units %d < min_treated + min_controls %d' % (n_usable, need_units))
    elif n_usable < max(floors['min_treated'], floors['min_controls'] + 1):
        reasons.append('usable units %d too few for the declared control group' % n_usable)
    return dict(treatable_max=treatable, cohorts_max=cohorts_max, usable_units=n_usable, universe_n=len(universe),
                floors={k: floors[k] for k in ('min_treated', 'min_cohorts', 'min_controls', 'min_pre_periods', 'min_post_periods')},
                floor_source=floors.get('source'), reachable=not reasons, reasons=reasons)


def evaluate(card, repo=None, *, rules=None):
    """{status: PROCEED | COUNT_FLOOR | INCONCLUSIVE, variants: [...], reasons: [...]}; never raises on missing data."""
    from .prescreen import REPO, bind_outcome, catalog_spec, flags
    from .support import declared_annual_periods
    from .variants import registered_cards
    repo = repo or REPO
    out = dict(schema='l2_count_floor_v1', gate=GATE, status='INCONCLUSIVE', variants=[], reasons=[], outcome_values_read=0)
    if card.get('track') != 'DiD' or card.get('unit', 'state') != 'state':
        out['why'] = 'track or unit not handled'
        return out
    try:
        card = deepcopy(card)
        card['outcome'] = bind_outcome(card['outcome'], repo)
        if rules is None:
            from . import rules as rules_module
            rules = rules_module.of_payload(card, repo)
        trials = []
        for name, variant in registered_cards(card).items():
            if name.startswith('secondary_'):
                continue  # a secondary cannot rescue the primary estimand (same rule as the prescreen)
            spec = catalog_spec(variant['outcome'], repo) or {}
            if not spec.get('support_artifact') and variant['outcome'].get('support_path'):
                spec = dict(spec, series_id=variant['outcome']['series_id'], support_artifact=dict(
                    path=variant['outcome']['support_path'], sha256=variant['outcome']['support_sha256']))
            years = declared_annual_periods(variant['outcome'])
            universe = set(variant['treatment'].get('universe', [])) - set(variant['treatment'].get('excluded_units', []))
            if not universe:
                raise ValueError('empty universe')
            optimistic = not spec.get('support_artifact')
            usable = {(u, y) for u in universe for y in years} if optimistic else flags(spec, repo)
            figures = perfect_history_figures(universe, years, usable, floors_in_force(variant['analysis'], rules),
                transition_year_drop=bool(variant.get('transition_year_drop', False)),
                controls=variant['analysis'].get('controls', 'never_treated'))
            figures.update(variant=name, support_basis='optimistic_complete_rectangle' if optimistic else 'certified_sidecar')
            trials.append(figures)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, AttributeError) as exc:
        out['why'] = type(exc).__name__ + ': ' + str(exc)
        return out
    out['variants'] = trials
    if trials and all(not t['reachable'] for t in trials):
        out['status'] = 'COUNT_FLOOR'
        out['reasons'] = [GATE + ': even a perfect history cannot reach the floors: ' + '; '.join(sorted({r for t in trials for r in t['reasons']}))]
    elif trials:
        out['status'] = 'PROCEED'
    return out


def apply_to_prescreen(value, card, repo=None):
    """Gate 0 hook: a prescreen result that would PROCEED becomes PRESCREEN_PARK when the bound fails. Pure on ``value``'s other fields."""
    if value.get('status') != 'PROCEED':
        return value
    bound = evaluate(card, repo)
    value = dict(value, count_floor=bound)
    if bound['status'] == 'COUNT_FLOOR':
        value.update(status='PRESCREEN_PARK', reasons=list(value.get('reasons', [])) + bound['reasons'])
    return value
