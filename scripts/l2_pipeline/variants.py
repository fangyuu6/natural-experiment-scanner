"""One complete scientific inventory for preparation and approved execution."""
from __future__ import annotations

import copy
import re
from .cards import variant_card


def secondary_entries(registration: dict) -> list[dict]:
    entries = registration.get('secondary_outcomes', registration.get('first_order', {}).get('secondary_outcomes', []))
    if not isinstance(entries, list):
        raise ValueError('Registered secondary inventory must be a list')
    return entries


def secondary_name(entry: dict) -> str:
    outcome = entry.get('outcome')
    if entry.get('role') != 'secondary' or not isinstance(outcome, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', outcome):
        raise ValueError('Invalid registered secondary outcome')
    return 'secondary_' + outcome


def registered_names(registration: dict) -> list[str]:
    names = ['primary'] + [s['name'] for s in registration['sensitivities']] + [secondary_name(s) for s in secondary_entries(registration)]
    hygiene=registration.get('c79_hygiene')
    if hygiene is not None:
        from .hygiene import RULE
        removed={r['name'] for r in hygiene['dropped']}
        added=hygiene['added']
        if (hygiene.get('rule')!=RULE or hygiene.get('threshold')!=.1 or hygiene.get('cap')!=3
                or len(added)>3 or removed.intersection(names) or not set(added)<=set(names)):
            raise ValueError('C79 sensitivity inventory differs')
        for entry in registration['sensitivities']:
            if entry['name'] in added and (entry.get('report_only') is not True or entry.get('gates_primary') is not False):
                raise ValueError('C79 omit-one must remain report-only')
    if len(names) != len(set(names)):
        raise ValueError('Duplicate registered variant')
    return names


def secondary_card(card: dict, entry: dict) -> dict:
    """Resolve a secondary's own measurement, never inherit primary filters.

    Full outcome fields may be registered explicitly. Compact series entries
    resolve fixed filters and units from the certified connector contract.
    Treatment geometry and primary thresholds are copied without changes.
    """
    from .connectors import CONNECTOR_CATALOG, ConnectorNotReady
    connector = entry.get('connector')
    contract = CONNECTOR_CATALOG.get(connector, {})
    fixed = contract.get('fixed_population_filters')
    filters = entry.get('population_filters', fixed)
    missing_filters = filters is None
    if missing_filters:
        filters = {}
    if fixed and filters != fixed:
        raise ValueError('Secondary population differs from registered connector: ' + entry['outcome'])
    result = copy.deepcopy(card)
    # Preserve the registered sample/calendar, not primary source identities.
    outcome = {k: copy.deepcopy(card['outcome'][k]) for k in ('years', 'period_years', 'units', 'frequency') if k in card['outcome']}
    outcome.update({k: copy.deepcopy(v) for k, v in entry.items() if k not in ('outcome', 'role', 'predeclared_basis')})
    outcome.update(connector=connector, series_id=entry.get('series_id', entry['outcome']),
                   measure_id=entry.get('measure_id', entry['outcome']),
                   source_measure_id=entry.get('source_measure_id', entry['outcome']),
                   population_filters=copy.deepcopy(filters), source_population_filters=copy.deepcopy(filters))
    if 'outcome_unit' not in outcome:
        outcome['outcome_unit'] = contract.get('outcome_unit', card['outcome'].get('outcome_unit'))
    result['outcome'] = outcome
    result['population'] = dict(result['population'], filters=copy.deepcopy(filters))
    if missing_filters:
        result['_secondary_unsupported_reason']='Secondary requires its own registered population_filters: ' + entry['outcome']
    from .connectors.rates import prospective_card, legacy_card
    if not legacy_card(card):
        if 'outcome_scale' in entry:
            result['outcome_scale'] = entry['outcome_scale']
        result = prospective_card(result)
    return result


def registered_cards(card: dict) -> dict[str, dict]:
    registered_names(card)
    return {'primary': dict(card), **{s['name']: variant_card(card, s.get('changes', {})) for s in card['sensitivities']},
            **{secondary_name(s): secondary_card(card, s) for s in secondary_entries(card)}}


def secondary_support_reason(support: dict) -> str:
    reason = support.get('reason') or '; '.join(map(str, support.get('stop_issues', []))) or 'Registered metadata support failed'
    if support.get('issues', {}).get('insufficient_treated'):
        n = len(support.get('retained_treated', []))
        minimum = support.get('effective_support_minimums', {}).get('min_treated')
        if minimum is not None:
            reason += f'; retained treated {n} < registered minimum {minimum}'
    return reason
