"""Outcome adapter registry with explicit parked dispositions for missing sources."""
from __future__ import annotations

from collections.abc import Callable

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData
from .cps import cps_monthly, cps_monthly_aggregates
from .cps_nurse import cps_nurse_hours
from .published import acs_b14004, cdc_public, cps_voting, qcew, qcew_constructed, standardized_csv
from .public_annual import acs1_state, laus_state, qcew_annual
from .downloads import download_plan as _annual_download_plan, fetch_public as _annual_fetch_public
from .first_order import (FIRST_ORDER_MEASURES, acs1_health_insurance,
    brfss_state_prevalence, ipeds_completions, ipeds_enrollment, nchs_mortality,
    fbi_ucr_state)
from .first_order_downloads import first_order_download_plan, fetch_first_order_public
from .natality import cdc_natality
from .fars import fars_state, MEASURES as FARS_MEASURES
from .state_crime import ca_doj_crime, FIELDS as CA_CRIME_FIELDS
from .catalog import CONNECTOR_CATALOG, target_mapped, v2_supported
from .outcome_v2 import construct as construct_outcome_v2
FIRST_ORDER_MEASURES['cdc_natality'] = {'births_count', 'birth_rate'}
FIRST_ORDER_MEASURES['fars_state'] = FARS_MEASURES
FIRST_ORDER_MEASURES['ca_doj_crime'] = set(CA_CRIME_FIELDS)

Adapter = Callable[[ConnectorRequest], PanelData]
ADAPTERS: dict[str, Adapter] = {
    'standardized_csv': standardized_csv,
    'cps_monthly': cps_monthly,
    'cps_monthly_aggregates': cps_monthly_aggregates,
    'cps_nurse_hours': cps_nurse_hours,
    'cps_voting': cps_voting,
    'qcew': qcew_annual,
    'qcew_annual': qcew_annual,
    'acs1_state': acs1_state,
    'laus_state': laus_state,
    'qcew_constructed': qcew_constructed,
    'cdc_public': cdc_public,
    'acs_b14004': acs_b14004,
    'acs1_health_insurance': acs1_health_insurance,
    'brfss_state_prevalence': brfss_state_prevalence,
    'ipeds_completions': ipeds_completions,
    'ipeds_enrollment': ipeds_enrollment,
    'nchs_mortality': nchs_mortality,
    'fbi_ucr_state': fbi_ucr_state,
    'cdc_natality': cdc_natality,
    'fars_state': fars_state,
    'ca_doj_crime': ca_doj_crime,
}
ALIASES = {'cps': 'cps_monthly', 'acs': 'acs_b14004', 'acs1': 'acs1_state',
           'laus': 'laus_state', 'cdc': 'cdc_public'}


def _v2_adapter(series_id):
    def build(request):
        if not v2_supported(request.options, request.options.get('measure_id')):
            raise ConnectorError('V2 connector requires exact series/measure/population binding')
        entry = CONNECTOR_CATALOG['outcome_v2_' + series_id]
        if request.options.get('series_id') != series_id or not set(request.years) <= set(entry['series_years']):
            raise ConnectorError('V2 requested series or years differ from connector coverage')
        return construct_outcome_v2(request)
    return build


for _name, _entry in CONNECTOR_CATALOG.items():
    if _entry.get('series_id'):
        ADAPTERS[_name] = _v2_adapter(_entry['series_id'])
        FIRST_ORDER_MEASURES[_name] = {_entry['series_id']}
PARKED = {
    'fbi': 'Choose fbi_ucr_state legacy 2010–2019 or certify a separate CDE/NIBRS vintage.',
    'nces': 'Choose ipeds_completions/ipeds_enrollment; CCD pupil populations remain unavailable.',
    'eia': 'Add EIA public-file parser with fixed sector, units, frequency, and revision vintage.',
    'acs_pums': 'Add ACS public microdata parser; summary B14004 cannot identify undocumented residents.',
    'cdc_wonder': 'Add certified final public-file adapter; restricted queries are not automated.',
}


def download_plan(request: ConnectorRequest):
    """Route the existing hub download API to the correct public source family."""
    if request.options.get('connector') in FIRST_ORDER_MEASURES or request.options.get('connector') == 'cdc_natality':
        return first_order_download_plan(request)
    return _annual_download_plan(request)


def fetch_public(plan, destination, **options):
    if plan.get('source_agency'):
        return fetch_first_order_public(plan, destination, **options)
    return _annual_fetch_public(plan, destination, **options)


def construct(request: ConnectorRequest) -> PanelData:
    name = request.options['connector'].lower()
    name = ALIASES.get(name, name)
    if name in PARKED:
        raise ConnectorNotReady(name + ': ' + PARKED[name])
    if name not in ADAPTERS:
        raise ConnectorNotReady('No registered outcome connector: ' + name)
    from .rates import apply_scale
    return apply_scale(request, ADAPTERS[name](request))


def connector_inventory() -> dict[str, dict[str, str]]:
    return {**{name: {'status': 'implemented_local_files', 'network': 'explicit_public_download_plan'
                     if name in ('qcew', 'qcew_annual', 'acs1_state', 'laus_state') or name in FIRST_ORDER_MEASURES else 'disabled'} for name in ADAPTERS},
            **{name: {'status': 'parked', 'required': reason} for name, reason in PARKED.items()}}


# Prospective V4 entries use hash-bound local browser exports. Register them for
# the ordinary hub process as well as the scoped run8 wrapper; old names retain
# their existing measurement and construction contracts.
from .wonder_registry import register as _register_outcome_v4
_register_outcome_v4()
from .outcome_v5 import register as _register_outcome_v5
_register_outcome_v5()
from .outcome_v6 import register as _register_outcome_v6
_register_outcome_v6()

__all__ = ['ConnectorError', 'ConnectorNotReady', 'ConnectorRequest', 'PanelData',
           'construct', 'connector_inventory', 'download_plan', 'fetch_public',
           'FIRST_ORDER_MEASURES', 'CONNECTOR_CATALOG', 'target_mapped', 'first_order_download_plan', 'fetch_first_order_public']

# Additive prospective catalogs. Existing V2–V7 names keep their bindings.
from .outcome_v7 import register as _register_outcome_v7
_register_outcome_v7()
from .outcome_v8 import register as _register_outcome_v8
_register_outcome_v8()

from .outcome_v9 import register as _register_outcome_v9
_register_outcome_v9()

# C49 prospective additive catalog; existing names retain immutable bindings.
from .outcome_v10 import register as _register_outcome_v10
_register_outcome_v10()

from .outcome_v11 import register as _register_outcome_v11
_register_outcome_v11()
from .outcome_v12 import register as _register_outcome_v12
_register_outcome_v12()

from .outcome_v13 import register as _register_outcome_v13
_register_outcome_v13()

from .outcome_v14 import register as _register_outcome_v14
_register_outcome_v14()

from .outcome_v15 import register as _register_outcome_v15
_register_outcome_v15()

# YIELD_FIXES 2026-10-16 additive catalog: V15 plus local public sources for three parked series.
from .outcome_v16 import register as _register_outcome_v16
_register_outcome_v16()

# NAMED_PANEL 2026-10-16: V17 (LAUS employment-population ratio). Dormant until its build step writes the V17 files.
from .outcome_v17 import register_if_built as _register_outcome_v17
_register_outcome_v17()

# T184 2026-10-20: V18 (public state-year series: NIAAA, BDS, SchoolVaxView, NCES graduation, state finance 1996-2011,
# BEA, FHFA, SNAP, QCEW slices). Dormant until its build step writes the V18 files.
from .outcome_v18 import register_if_built as _register_outcome_v18
_register_outcome_v18()

# T273E/T277 2026-10-23: V19 (six QCEW industry slices: NAICS 6213, 4853, 23). Dormant until its build step writes the V19 files.
from .outcome_v19 import register_if_built as _register_outcome_v19
_register_outcome_v19()
