"""Answer-free first-order outcome gate for prospective intake and specs.

Legacy cards can still be loaded and their frozen studies retained. A new S0
or S4 passage requires located statute-purpose/literature evidence and a
measurement declaration; no historical result values or outcome files are read.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any

RULE = "first_order_outcome_v1"
UNAVAILABLE = "first-order outcome unavailable"

# Conservative capabilities for older adapters. New public adapters publish
# FIRST_ORDER_MEASURES in connectors, so new domains do not require this gate
# to depend on their outcome parsers or download paths.
_MEASURES = {
    "cps_monthly": {"employment", "employment_share", "employment_percent"},
    "cps_monthly_aggregates": {"employment", "employment_share", "employment_percent"},
    "cps_nurse_hours": {"nurse_hours", "hours"},
    "cps_voting": {"voter_turnout"},
    "qcew": {"employment", "jobs", "wages", "annual_avg_wkly_wage", "avg_annual_pay"},
    "qcew_annual": {"employment", "jobs", "wages", "annual_avg_wkly_wage", "avg_annual_pay"},
    "qcew_constructed": {"wages"},
    "laus_state": {"unemployment", "unemployment_rate"},
    "acs_b14004": {"public_college_enrollment"},
    "cdc_public": {"overdose_mortality", "drug_poisoning_mortality"},
}
_LABOR = {"employment", "employment_share", "employment_percent", "jobs", "unemployment", "unemployment_rate", "hours", "nurse_hours", "wages", "earnings", "annual_avg_wkly_wage", "avg_annual_pay"}

# Reviewed V6.1 construct names, not population eligibility or legal approval.
# Other differing target names need a reviewed catalog binding or new alias;
# similarity of words never establishes a measurement contract.
_CONSTRUCT_ALIASES = {
    "colorectal_cancer_screening_receipt_pct_among_statutorily_covered_nonsymptomatic_enrollees":
        "colorectal_screening_pct",
}

# Applied C9 decisions bind only these reviewed designs and exact target names.
# They do not authorize a general behavior-to-mortality substitution.
_C9_TRAFFIC_TARGETS = {
    "run3_minfine": "alcohol_impaired_driving",
    "run3_opencont": "alcohol_involved_motor_vehicle_crash_fatalities",
}


def _construct_target(target: Any, design_id: str | None = None) -> Any:
    if design_id in _C9_TRAFFIC_TARGETS and target == _C9_TRAFFIC_TARGETS[design_id]:
        return "traffic_deaths_count"
    return _CONSTRUCT_ALIASES.get(target, target) if isinstance(target, str) else target


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _refs(value: Any) -> bool:
    return (isinstance(value, list) and bool(value) and
            all(isinstance(ref, Mapping) and _text(ref.get("source_ref")) and
                _text(ref.get("locator")) for ref in value))


def _proxy_justified(evidence: Mapping[str, Any]) -> bool:
    reason = evidence.get("proxy_justification")
    return (_text(reason) and "\n" not in reason and "\r" not in reason)


def _same_construct(outcome: Mapping[str, Any], target: Any, primary: Any, *, design_id: str | None = None) -> bool:
    """Use exact reviewed construct identities/bindings without reading values."""
    if not _text(target) or not _text(primary):
        return False
    target = _construct_target(target, design_id)
    primary = _CONSTRUCT_ALIASES.get(primary, primary)
    if target == primary:
        return True
    if outcome.get('connector') in {'outcome_v8_'+primary,'outcome_v9_'+primary,'outcome_v10_'+primary,'outcome_v11_'+primary,'outcome_v12_'+primary,'outcome_v13_'+primary,'outcome_v14_'+primary,'outcome_v15_'+primary,'outcome_v16_'+primary,'outcome_v17_'+primary,'outcome_v18_'+primary,'outcome_v19_'+primary}:
        from .connectors.population_v63 import same_construct
        if same_construct(target, primary,version=int(outcome['connector'].split('_')[1][1:])):
            return True
    from .connectors import ALIASES
    from .connectors.catalog import CONNECTOR_CATALOG
    name = outcome.get("connector", "")
    name = ALIASES.get(name.lower(), name.lower()) if isinstance(name, str) else ""
    return primary in CONNECTOR_CATALOG.get(name, {}).get("target_measures", {}).get(target, ())


def _population_proxy_allowed(card: Mapping[str, Any]) -> bool:
    first = card.get("first_order", {})
    outcome = card.get("outcome", {})
    if not isinstance(first, Mapping) or not isinstance(outcome, Mapping):
        return False
    return (first.get("relation") == "closest_proxy" and _proxy_justified(first) and
            "dilution" in first["proxy_justification"].lower() and
            first.get("primary_outcome") == outcome.get("measure_id") and
            _same_construct(outcome, first.get("target_outcome"), outcome.get("measure_id"),
                            design_id=card.get("design_id")))


def _population_gap(card: Mapping[str, Any]) -> bool:
    automation = card.get("automation", {})
    return bool(automation.get("run3", {}).get("known_target_population_gap") or
                automation.get("v6_reassessment", {}).get("target_population_gap"))


def _supported(outcome: Mapping[str, Any], measure: str) -> bool:
    from . import connectors
    name = outcome.get("connector", "")
    if not isinstance(name, str):
        return False
    name = connectors.ALIASES.get(name.lower(), name.lower())
    if name not in connectors.ADAPTERS:
        # Opaque already-constructed panels require independently located
        # measurement evidence; they cannot confer support by name alone.
        if name != "opaque_state_year":
            return False
    capabilities = dict(_MEASURES)
    capabilities.update(getattr(connectors, "FIRST_ORDER_MEASURES", {}))
    if name.startswith('outcome_v2_'):
        return connectors.v2_supported(outcome, measure)
    if name == "nchs_mortality" and measure in {"mortality_deaths_count", "mortality_age_adjusted_rate"}:
        from .connectors.first_order import NCHS_SUPPORTED_CAUSES
        filters = outcome.get("population_filters", {})
        if not isinstance(filters, Mapping) or filters.get("cause_name") not in NCHS_SUPPORTED_CAUSES:
            return False
    if name in {"qcew", "qcew_annual"}:
        field = outcome.get("value_field", "annual_avg_emplvl")
        field = {"avg_wkly_wage":"annual_avg_wkly_wage"}.get(field, field)
        matches = {"annual_avg_emplvl": {"employment", "jobs"},
                   "annual_avg_wkly_wage": {"wages", "annual_avg_wkly_wage"},
                   "avg_annual_pay": {"wages", "avg_annual_pay"}}
        return measure in matches.get(field, set())
    if name in capabilities:
        return measure in capabilities[name]
    if name == "acs1_state":
        variables = outcome.get("variables", outcome.get("numerator_variables", []))
        if isinstance(variables, str):
            variables = [variables]
        if not isinstance(variables, list) or not variables:
            return False
        denominator = outcome.get("denominator_variables", [])
        if not isinstance(denominator, list):
            return False
        variables = variables + denominator
        # ACS generic table adapter must identify the table that measures the
        # declared construct; employment tables cannot be relabeled insurance.
        if measure in {"health_insurance_coverage", "uninsured_share"}:
            return all(isinstance(v, str) and v.startswith("B27001_") for v in variables)
        if measure in {"public_college_enrollment", "college_enrollment"}:
            return all(isinstance(v, str) and v.startswith(("B14004_", "B14007_")) for v in variables)
        if measure in {"bachelors_attainment", "educational_attainment"}:
            return all(isinstance(v, str) and v.startswith("B15003_") for v in variables)
        return False
    # Generic imported panels need a source-measure binding and a located
    # measurement definition. S2 remains responsible for source byte checks.
    return (name in {"standardized_csv", "opaque_state_year"} and
            outcome.get("source_measure_id") == measure and
            _refs(outcome.get("measurement_refs")))


def first_order_gate(card: Mapping[str, Any], *, _check_sensitivities: bool = True) -> dict[str, Any]:
    """Check declarations and parser capabilities without opening any data."""
    evidence = card.get("first_order")
    issues: list[str] = []
    if not isinstance(evidence, Mapping):
        issues.append("Card must name its statute-purpose and literature-based direct target")
        evidence = {}
    target, primary = evidence.get("target_outcome"), evidence.get("primary_outcome")
    # REDESIGN R1: purpose is a label for L3, not a gate. The card pins a rules file whose purpose.mode is "label"; the three
    # purpose-verification issues are skipped, every target-mapping and outcome-registration issue stays. R0 cards: unchanged.
    from . import rules as _rules
    _pinned = _rules.of_payload(card)
    label_mode = _pinned is not None and _pinned["purpose"]["mode"] == "label"
    if evidence.get("evidence_status") == "pending_policy_specific_verification" and not label_mode:
        issues.append("Statute-purpose/literature leads require policy-specific verification")
    for field in ("target_outcome", "primary_outcome"):
        if not _text(evidence.get(field)):
            issues.append("Missing first_order." + field)
    # V6.2 purpose is the targeted behavior/welfare outcome. Either located
    # statute purpose or at least two distinct established studies supports it;
    # instrument text and literature need not both be available. Substantive
    # policy/outcome identity remains an explicit hub verification decision.
    papers = evidence.get("literature_refs")
    literature_route = (_refs(papers) and len({
        r["source_ref"].split("#", 1)[0].rstrip("/") for r in papers
        if "supports" not in r or "target_outcome" in r["supports"]}) >= 2 and len({
        " ".join(r["title"].casefold().split()) if _text(r.get("title")) else
        r["source_ref"].split("#", 1)[0].rstrip("/") for r in papers
        if "supports" not in r or "target_outcome" in r["supports"]}) >= 2)
    statutes = evidence.get("statute_refs")
    statute_route = (_refs(statutes) and any(
        "supports" not in r or "target_outcome" in r["supports"] for r in statutes))
    if label_mode:
        pass
    elif not statute_route and not literature_route:
        issues.append("V6.2 located statute purpose or at least two literature studies required")
    route = evidence.get("purpose_route")
    if label_mode:
        pass
    elif route is not None and route not in {"statute", "established_literature"}:
        issues.append("Unknown declared purpose_route")
    elif route == "statute" and not statute_route:
        issues.append("Declared statute purpose_route requires a located statute supporting target_outcome")
    elif route == "established_literature" and not literature_route:
        issues.append("Declared literature purpose_route requires two distinct studies")
    relation = evidence.get("relation")
    if relation not in {"direct", "closest_proxy"}:
        issues.append("Primary must be direct or a justified closest_proxy")
    elif relation == "direct" and primary != target:
        issues.append("Direct primary differs from policy target")
    elif relation == "closest_proxy":
        if not _proxy_justified(evidence):
            issues.append("Closest measurable proxy requires one-line justification")
        if primary in _LABOR and target not in _LABOR:
            issues.append("Employment/labor is a downstream outcome for this target; register it only as a secondary")
    outcome = card.get("outcome", {})
    if not isinstance(outcome, Mapping):
        outcome = {}
    if relation == "closest_proxy" and not _same_construct(outcome, target, primary, design_id=card.get("design_id")):
        issues.append("V6.1 primary proxy must measure the same construct as the statutory target")
    if _population_gap(card) and not _population_proxy_allowed(card):
        issues.append("Broader target population requires a same-construct closest_proxy with one-line dilution rationale")
    if outcome.get("measure_id") != primary or not _text(primary):
        issues.append("Primary outcome must bind outcome.measure_id")
    availability = evidence.get("availability")
    if availability not in {"available", "unavailable"}:
        issues.append("Declare first_order.availability")
    secondaries = evidence.get("secondary_outcomes")
    if not isinstance(secondaries, list):
        issues.append("Explicit secondary_outcomes registration list required (empty is allowed)")
        secondaries = []
    names: set[str] = set()
    for entry in secondaries:
        if (not isinstance(entry, Mapping) or not _text(entry.get("outcome")) or
                entry.get("role") != "secondary" or not _text(entry.get("connector"))):
            issues.append("Every downstream outcome must be explicitly registered as secondary")
            continue
        if entry["outcome"] == primary or entry["outcome"] in names:
            issues.append("Duplicate/primary entry in secondary outcomes")
        names.add(entry["outcome"])
    for owner in (outcome, card.get("analysis", {})):
        if isinstance(owner, Mapping) and owner.get("secondary_outcomes"):
            issues.append("Register secondaries in first_order.secondary_outcomes only")
    if _check_sensitivities:
        # A sensitivity may change ages/table cells but cannot quietly become
        # a downstream primary. Check its merged scientific declaration too.
        for sensitivity in card.get("sensitivities", []):
            if not isinstance(sensitivity, Mapping):
                continue  # Card schema/validator handles malformed inventory.
            changes = sensitivity.get("changes", {})
            if not isinstance(changes, Mapping) or not any(k in changes for k in ("outcome", "analysis")):
                continue
            variant = copy.deepcopy(dict(card))
            def merge(target: dict, patch: Mapping) -> None:
                for key, value in patch.items():
                    if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                        merge(target[key], value)
                    else:
                        target[key] = copy.deepcopy(value)
            for field in ("outcome", "analysis"):
                if field in changes and isinstance(changes[field], Mapping):
                    merge(variant[field], changes[field])
            # Parent V6 coverage validates each sensitivity against its own
            # retained requested window, rather than inheriting primary bounds.
            if card.get('automation',{}).get('coverage_window',{}).get('rule')=='coverage_bounded_windows_v6':
                variant.get('automation',{}).pop('coverage_window',None)
            result = first_order_gate(variant, _check_sensitivities=False)
            if not result["passed"]:
                issues.append("Sensitivity " + str(sensitivity.get("name")) + " violates first-order registration: " + result["reason"])
    coverage=card.get('automation',{}).get('coverage_window',{})
    if card.get('treatment', {}).get('source_type') == 'policy_tracker':
        earliest = card.get('automation', {}).get('earliest_cohort_check', {})
        if earliest.get('passed') is not True:
            issues.append('Tracker earliest-cohort pre/post coverage is pending or insufficient')
    from .coverage import legal_window_issues
    issues.extend(legal_window_issues(card))
    v6=coverage.get('rule')=='coverage_bounded_windows_v6'
    if v6:
        from .coverage import coverage_bounded_card
        bounded=coverage_bounded_card(card)
        checked=bounded['automation']['coverage_window']
        if card.get('outcome',{}).get('years') != bounded['outcome'].get('years') or card.get('outcome',{}).get('period_years') != bounded['outcome'].get('period_years'):
            issues.append('Card outcome window differs from recomputed V6 coverage bounds')
        # Compare effective windows: treatment-only variants inherit the base
        # window and need not repeat redundant outcome fields.
        from .cards import variant_card
        def windows(value):
            return [(s['name'], variant_card(value,s.get('changes',{}))['outcome'].get('years'),
                     variant_card(value,s.get('changes',{}))['outcome'].get('period_years'))
                    for s in value.get('sensitivities',[])]
        if windows(card) != windows(bounded):
            issues.append('Sensitivity windows differ from recomputed V6 coverage bounds')
        coverage=checked
        if coverage.get('parked'):
            issues.append('Coverage-bounded window pending: '+coverage.get('reason','unknown coverage'))
        mapping=target_mapping(card)
        if not mapping['mapped']:
            issues.extend(mapping['issues'])
        if evidence.get('evidence_status') != 'verified_by_hub' and not label_mode:
            issues.append('V6 purpose-verification job and explicit hub acceptance required')
    # Invalid scientific declarations park for review, distinct from missing
    # measurement infrastructure. Unavailability never substitutes employment.
    unavailable = availability == "unavailable" or (
        availability == "available" and _text(primary) and not _supported(outcome, primary))
    passed = not issues and not unavailable
    return {"rule": RULE, "passed": passed,
            "reason": "First-order target and measurement registered" if passed else
                      UNAVAILABLE if unavailable else "first-order outcome registration incomplete",
            "issues": issues, "target_outcome": target, "primary_outcome": primary,
            "relation": relation, "registered_secondaries": secondaries,
            "outcome_values_read": 0, "outcome_association_calls": 0}


def require_first_order(card: Mapping[str, Any]) -> dict[str, Any]:
    report = first_order_gate(card)
    if not report["passed"]:
        raise ValueError(report["reason"] + (": " + "; ".join(report["issues"]) if report["issues"] else ""))
    return report


def target_mapping(card: Mapping[str, Any]) -> dict[str, Any]:
    """V6 prospective catalog mapping, separate from substantive purpose approval."""
    from .connectors.catalog import CONNECTOR_CATALOG, target_mapped
    from .connectors import ALIASES
    outcome=card.get('outcome', {})
    first=card.get('first_order', {})
    name=outcome.get('connector','')
    name=ALIASES.get(name.lower(),name.lower()) if isinstance(name,str) else ''
    target=first.get('target_outcome'); measure=outcome.get('measure_id')
    entry=CONNECTOR_CATALOG.get(name,{})
    mapped=target_mapped(name,_construct_target(target, card.get("design_id")),measure) and _supported(outcome,measure)
    if (not mapped and name in {'outcome_v8_'+str(measure),'outcome_v9_'+str(measure),'outcome_v10_'+str(measure),'outcome_v11_'+str(measure),'outcome_v12_'+str(measure),'outcome_v13_'+str(measure),'outcome_v14_'+str(measure),'outcome_v15_'+str(measure),'outcome_v16_'+str(measure),'outcome_v17_'+str(measure),'outcome_v18_'+str(measure),'outcome_v19_'+str(measure)} and
            entry.get('available') and entry.get('access')=='public_no_key' and
            _same_construct(outcome,target,measure) and _supported(outcome,measure)):
        mapped=True
    issues=[]
    if not mapped: issues.append('Exact target/measure is absent from available connector capabilities')
    if entry.get('available') is False:
        mapped=False; issues.append('Public source is currently unavailable without credentials')
    scope=entry.get('geographic_scope')
    units=outcome.get('units',card.get('treatment',{}).get('universe',[]))
    if isinstance(scope,(list,tuple,set,frozenset)) and not set(units)<=set(scope):
        mapped=False; issues.append('Declared units exceed public source geographic coverage')
    if first.get('relation') == 'closest_proxy' and (not _proxy_justified(first) or not _same_construct(outcome,target,measure,design_id=card.get("design_id"))):
        mapped=False; issues.append('Proxy requires a same-construct target/measure and one-line justification')
    if _population_gap(card) and not _population_proxy_allowed(card):
        mapped=False; issues.append('Statutory target population is not isolated by declared source filters')
    return dict(rule='outcome_first_catalog_v6',mapped=bool(mapped),connector=name,
        target_outcome=target,measure_id=measure,issues=issues,
        purpose_approval_granted=False,outcome_values_read=0,outcome_association_calls=0)
