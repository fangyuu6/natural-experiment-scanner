"""Collected paper records become prospective S0 cards using metadata only.

Exact catalog constructs and located population declarations drive mapping.
Published coefficients, uncertainty, significance and outcome files are never
read here. A card is a fresh candidate; S1--S7 certification remains pending.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from typing import Any

from .cards import validate_card
from .connectors import ALIASES
from .connectors.catalog import CONNECTOR_CATALOG
from .connectors.common import NAMES, STATES
from .coverage import coverage_bounded_card
from .first_order import first_order_gate, target_mapping


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reference(value: Any) -> dict | None:
    if not isinstance(value, Mapping):
        return None
    url = value.get('source_url', value.get('source_ref'))
    if _text(url) and _text(value.get('locator')):
        return {'source_ref': url, 'locator': value['locator']}
    return None


def _year(value: Any) -> bool:
    return type(value) is int and 1800 <= value <= 9999


def _state(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value in STATES.values() else NAMES.get(value)


def _park(record: Mapping, issues: list[str], **reports) -> dict:
    return dict(disposition='PARK', study_id=record.get('study_id'),
                reason='; '.join(issues), issues=issues, card=None,
                outcome_values_read=0, outcome_association_calls=0,
                execution_authorized=False, readiness_granted=False, **reports)


def _constructs(record: Mapping, proposal: Mapping) -> tuple[str | None, str | None, list[str]]:
    issues = []
    values = []
    for field, definition in (('target_outcome', 'target_definition'),
                              ('primary_outcome', 'primary_definition')):
        original = record.get(field)
        value = proposal.get(field, original)
        if not _text(value):
            issues.append('Missing exact ' + field + ' construct')
            value = None
        elif value != original and proposal.get(definition) != original:
            issues.append('Proposed ' + field + ' must bind the verbatim paper definition')
        values.append(value)
    if proposal and _reference(proposal) != _reference(record.get('outcome_evidence')):
        issues.append('Outcome mapping requires the verified outcome evidence locator')
    return values[0], values[1], issues


def literature_record_to_card(record: Mapping[str, Any], *, record_ref: str | None = None,
                              catalog: Mapping | None = None) -> dict:
    """Return ``CARD`` with a valid S0 candidate or ``PARK`` with explicit gaps.

    The caller must first collect/validate the record. ``status=VERIFIED`` is
    required here but grants no legal or outcome-data readiness. A proposal
    uses exact catalog IDs and binds differing IDs to verbatim definitions via
    ``target_definition``/``primary_definition``. Population filters and units
    must be explicit; neither demographics nor geography are guessed.
    """
    if not isinstance(record, Mapping):
        return _park({}, ['Literature record must be an object'])
    if record.get('status') != 'VERIFIED':
        return _park(record, ['Full literature record is not collector-verified'])
    issues = []
    for field in ('study_id', 'policy_id', 'policy_definition', 'target_population', 'population'):
        if not _text(record.get(field)):
            issues.append('Missing verified ' + field)
    policy_ref = _reference(record.get('policy_evidence'))
    outcome_ref = _reference(record.get('outcome_evidence'))
    if policy_ref is None:
        issues.append('Located verified policy definition required')
    if outcome_ref is None:
        issues.append('Located verified primary outcome required')
    proposal = record.get('outcome_mapping', {})
    if not isinstance(proposal, Mapping):
        return _park(record, ['Outcome mapping proposal must be an object'])
    target, primary, mapping_issues = _constructs(record, proposal)
    issues.extend(mapping_issues)
    filters = proposal.get('population_filters')
    source_filters = proposal.get('source_population_filters')
    if not isinstance(filters, Mapping) or not isinstance(source_filters, Mapping):
        issues.append('Explicit target and source population filters required')
    elif dict(filters) != dict(source_filters):
        issues.append('Source filters differ from the registered measurement population')
    if not _text(proposal.get('outcome_unit')):
        issues.append('Explicit original measurement unit required')

    catalog = CONNECTOR_CATALOG if catalog is None else catalog
    connector = proposal.get('connector', record.get('connector_lead'))
    if isinstance(connector, Mapping):
        connector = connector.get('connector')
    if 'connector' not in proposal and _text(connector):
        lead = ALIASES.get(connector.lower(), connector.lower())
        # Existing registry leads can be descriptive prose. Only an exact
        # catalog name can select a parser; other prose does not block the
        # unique exact-construct mapping below.
        if lead not in catalog:
            connector = None
    if _text(connector):
        connector = ALIASES.get(connector.lower(), connector.lower())
    else:
        # Alias names refer to the same parser and do not create ambiguity.
        equivalent = {'qcew': 'qcew_annual', 'cps_monthly_aggregates': 'cps_monthly'}
        matches = {equivalent.get(ALIASES.get(name, name), ALIASES.get(name, name)) for name, entry in catalog.items()
                   if entry.get('available') is True and entry.get('access') == 'public_no_key'
                   and primary in entry.get('target_measures', {}).get(target, ())}
        if len(matches) == 1:
            connector = next(iter(matches))
        else:
            connector = None
            issues.append('Exact first-order connector mapping unavailable' if not matches
                          else 'Multiple first-order connectors require explicit source selection')

    window = record.get('study_window', {})
    adoption = record.get('adoption', {})
    if not isinstance(window, Mapping) or not isinstance(adoption, Mapping):
        return _park(record, issues + ['Located study and adoption coverage windows required'])
    start = window.get('start_year', window.get('start'))
    end = window.get('end_year', window.get('end'))
    legal_start, legal_end = adoption.get('coverage_start'), adoption.get('coverage_end')
    if not all(_year(v) for v in (start, end, legal_start, legal_end)):
        issues.append('Study and legal-source coverage bounds must be explicit integer years')
    elif start > end or legal_start > legal_end:
        issues.append('Study or legal-source coverage bounds are reversed')
    if _reference(window) is None or _reference(adoption) is None:
        issues.append('Study grid and adoption table require source URL and page/table locators')
    if adoption.get('reported_scope_complete') is not True:
        issues.append('Complete paper-reported adoption scope requires verification')
    universe_input = adoption.get('universe')
    universe = [_state(u) for u in universe_input] if isinstance(universe_input, list) else []
    if not universe or None in universe or len(set(universe)) != len(universe):
        issues.append('Explicit unique paper state universe required; adoption extrema are insufficient')
    rows = adoption.get('states')
    reported = []
    seen = set()
    if not isinstance(rows, list) or not rows:
        issues.append('Located treated-state adoption years required')
    else:
        for row in rows:
            if not isinstance(row, Mapping):
                issues.append('Adoption rows must be objects')
                continue
            unit = _state(row.get('state'))
            if unit is None or unit not in universe or unit in seen:
                issues.append('Adoption state is invalid, duplicate or outside the paper universe')
            if not _year(row.get('year')) or _reference(row) is None:
                issues.append('Every adoption year requires an integer year and located source')
            seen.add(unit)
            reported.append({k: copy.deepcopy(row[k]) for k in ('year', 'locator', 'source_url') if k in row} | {'state': unit})
    if issues:
        return _park(record, issues)

    identity = record['study_id']
    slug = re.sub(r'[^A-Za-z0-9_-]+', '_', identity).strip('_') or 'paper'
    # Keep distinct source IDs distinct when sanitization or truncation collides.
    design_id = 'run4_lit_' + slug[:90] + '_' + hashlib.sha256(identity.encode()).hexdigest()[:10]
    relation = proposal.get('relation', 'direct' if target == primary else 'closest_proxy')
    first = dict(target_outcome=target, target_population=record['target_population'],
                 primary_outcome=primary, relation=relation,
                 proxy_justification=proposal.get('proxy_justification', ''),
                 availability='available', statute_refs=[policy_ref], literature_refs=[outcome_ref],
                 secondary_outcomes=[], evidence_status='collector_verified_paper_record',
                 purpose_source_type='published_did_study',
                 purpose_basis='Collector-validated located paper purpose; direct legal verification remains S1')
    outcome = dict(connector=connector, measure_id=primary, paths=[], years=[start, end],
                   units=sorted(universe), frequency='annual', calendar='calendar_year',
                   population_filters=copy.deepcopy(dict(filters)),
                   source_population_filters=copy.deepcopy(dict(source_filters)),
                   outcome_unit=proposal['outcome_unit'])
    # Parser options are metadata, never agent-proposed local data paths/code.
    for key in ('value_field', 'response_label', 'question_id', 'breakout_id', 'variables',
                'numerator_variables', 'denominator_variables', 'industry_code', 'ownership_code'):
        if key in proposal:
            outcome[key] = copy.deepcopy(proposal[key])
    if connector == 'cps_voting':
        outcome['period_years'] = list(range(start + start % 2, end + 1, 2))
    card = dict(schema_version='1.0', version='v1', design_id=design_id,
                policy_family=record['policy_id'], mechanism=record['policy_definition'],
                track='DiD', unit='state', outcome_family=primary,
                population={'description': record['population'], 'filters': copy.deepcopy(dict(filters))},
                outcome=outcome, sources=[dict(source_id='published_study', url=adoption['source_url'],
                    locator=adoption['locator'], license='Open version; reuse license verification required before freeze',
                    complete_list=adoption.get('complete_list') is True, coverage_end=f'{legal_end}-12-31')],
                lineage=dict(queue_version='run4_parallel_literature_records', screen_version='S0L_collected',
                    source_text_ids=['doi:' + str(record.get('doi') or identity)],
                    legal_instrument_ids=['policy:' + record['policy_id']]),
                treatment=dict(source_type='published_did_study', study_id=identity, universe=sorted(universe),
                    seed=20261008, complete_list_source_id='published_study', predicate=record['policy_definition'],
                    coverage_end=f'{legal_end}-12-31', clock='calendar_year',
                    source_window=dict(start_year=legal_start, end_year=legal_end,
                        basis='Located paper-reported grid; adoption extrema never define zero coverage'),
                    historical_convention='Unlisted universe members remain unknown until S1; outside paper scope unknown',
                    reported_adoption_records=reported, complete_list_certification='S1_PENDING'),
                first_order=first, analysis=dict(clock='calendar_year', controls='never_treated',
                    min_pre_periods=3, min_controls=5, min_cohorts=2, min_treated=6,
                    spec_status='FRESH_S1_S7_REQUIRED', selection_expected_sign_used=False), sensitivities=[],
                automation=dict(intake={'source_type': 'published_did_study', 'stage': 'S0L'},
                    literature_record={'study_id': identity, 'record_ref': record_ref,
                        'collector_verified': True, 's1_certified': False, 'outcome_data_certified': False,
                        'requires_purpose_verification': True,
                        'published_result_values_read': 0, 'execution_authorized': False},
                    run3={'known_target_population_gap': proposal.get('target_population_gap') is True}))
    card = coverage_bounded_card(card, catalog=catalog)
    from .connectors.rates import prospective_card
    card = prospective_card(card)
    mapping = target_mapping(card)
    gate = first_order_gate(card)
    coverage = card['automation']['coverage_window']
    # Boundaries must not silently remove the original early cohort to improve
    # support. Count only actually declared connector periods, including holes.
    earliest = min(row['year'] for row in reported)
    periods = coverage['effective_period_years']
    window_support = dict(earliest_reported_cohort=earliest,
        pre_years=[year for year in periods if year < earliest],
        post_years=[year for year in periods if year > earliest],
        early_cohorts_dropped=False, source_and_data_certification_granted=False)
    window_support['passed'] = len(window_support['pre_years']) >= 3 and len(window_support['post_years']) >= 2
    card['automation']['literature_window_support'] = window_support
    errors = validate_card(card)
    errors.extend(mapping['issues'] if not mapping['mapped'] else [])
    if coverage['parked']:
        errors.append('V6 window parked: ' + coverage['reason'])
    elif not window_support['passed']:
        errors.append('V6 window lacks three pre-periods or two post-periods for the earliest reported cohort; no early cohort may be dropped')
    if not gate['passed']:
        # Purpose acceptance is a downstream review requirement. Candidate
        # emission must preserve the pending status, while every other gate
        # failure still parks before a candidate card can enter that review.
        substantive = [issue for issue in gate['issues']
                       if issue != 'V6 purpose-verification job and explicit hub acceptance required']
        errors.extend(substantive or ([] if gate['issues'] else [gate['reason']]))
    if errors:
        return _park(record, list(dict.fromkeys(errors)), first_order_gate=gate,
                     target_mapping=mapping, coverage_window=coverage, window_support=window_support)
    return dict(disposition='CARD', study_id=identity,
                reason='Verified paper, exact first-order mapping and V6 supported window; purpose review and S1--S7 pending',
                issues=[], card=card, first_order_gate=gate, target_mapping=mapping,
                coverage_window=coverage, window_support=window_support,
                outcome_values_read=0, outcome_association_calls=0,
                readiness_granted=False, execution_authorized=False)
