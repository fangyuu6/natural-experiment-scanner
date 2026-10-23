"""V6 outcome-first capability catalog, independent of policy signs and values.

A mapping is a measurement lead, not certified population/source availability.
Exact target-to-measure bindings prevent employment substitution and do not
license downstream outcomes. Geographic/year/population support is checked by
adapters and intake before readiness; source rights remain a freeze requirement.
"""
from __future__ import annotations
import json
from pathlib import Path


def _entry(start, end, bindings, domains, populations, url, *, units='US_states',
           access='public_no_key', excluded_years=()):
    return dict(coverage_start=start, coverage_end=end,
        coverage=dict(start=start, end=end, excluded_years=list(excluded_years)),
        targets=frozenset(bindings), measures=frozenset(m for ms in bindings.values() for m in ms),
        target_measures={t: frozenset(ms) for t, ms in bindings.items()},
        domains=tuple(domains), populations=tuple(populations), units=units,
        access=access, available=access == 'public_no_key', geographic_scope=units,
        source_url=url, purpose_verification_required=True)


def _identity(*tags):
    return {tag: {tag} for tag in tags}


CONNECTOR_CATALOG = {
    'acs1_health_insurance': _entry(2008, 2024, {
        **_identity('health_insurance_coverage_pct'),
        'dependent_health_insurance_coverage_pct': {'health_insurance_coverage_pct'}},
        ['Health', 'Insurance'], ['Civilian noninstitutionalized; exact B27001 age bin and sex'],
        'https://www.census.gov/programs-surveys/acs/data.html', excluded_years=[2020]),
    'brfss_state_prevalence': _entry(2011, 2024, {
        **_identity('colorectal_screening_pct', 'mammography_pct', 'cervical_screening_pct',
            'current_smoking_pct', 'binge_drinking_pct', 'obesity_pct',
            'health_insurance_coverage_pct', 'drinking_driving_pct'),
        'guideline_colorectal_screening_pct': {'colorectal_screening_pct'}},
        ['Health', 'Transportation'], ['Adult published question/response/breakout denominator'],
        'https://data.cdc.gov/resource/dttw-5yxu.csv'),
    'ipeds_completions': _entry(2002, 2024, _identity('bachelors_completions_count'),
        ['Education'], ['Public institutions by location; total bachelor awards; optional fixed institution roster'],
        'https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx'),
    'ipeds_enrollment': _entry(2002, 2024, _identity('undergraduate_enrollment_count'),
        ['Education'], ['Public institutions by location; undergraduate total; optional fixed institution roster'],
        'https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx'),
    'nchs_mortality': _entry(1999, 2017, _identity('drug_overdose_deaths_count',
        'drug_overdose_age_adjusted_rate', 'mortality_deaths_count', 'mortality_age_adjusted_rate'),
        ['Health'], ['Final state cells; all-age all-sex overdose or exact published leading cause'],
        'https://data.cdc.gov/resource/bi63-dtpu.csv'),
    'cdc_natality': _entry(1990, 2019, {**_identity('births_count', 'birth_rate'),
        'teen_births_count': {'births_count'}, 'teen_birth_rate': {'birth_rate'}},
        ['Health'], ['Maternal residence state; published ages15–17,18–19,15–19 only'],
        'https://data.cdc.gov/resource/y268-sna3.csv'),
    'fars_state': _entry(1982, 2022, {
        **_identity('traffic_deaths_count', 'fatal_crashes_count',
            'child_traffic_deaths_count', 'teen_traffic_deaths_count', 'child_bicycle_deaths_count'),
        'traffic_deaths': {'traffic_deaths_count'}, 'road_safety_fatalities': {'traffic_deaths_count'}},
        ['Transportation'], ['All traffic fatalities or exact person age range plus driver/occupant/bicyclist role'],
        'https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars'),
    'ca_doj_crime': _entry(1985, 2025, _identity('violent_crime_count', 'homicide_count',
        'rape_count', 'robbery_count', 'aggravated_assault_count', 'property_crime_count',
        'burglary_count', 'larceny_count', 'motor_vehicle_theft_count'),
        ['Law and Crime'], ['CA reported agency offenses; complete independently certified annual agency roster'],
        'https://data-openjustice.doj.ca.gov/data/crimes-and-clearances-including-arson', units=('CA',)),
    'fbi_ucr_state': _entry(2010, 2019, _identity('violent_crime_count', 'homicide_count',
        'rape_count', 'robbery_count', 'aggravated_assault_count', 'property_crime_count',
        'burglary_count', 'larceny_count', 'motor_vehicle_theft_count'),
        ['Law and Crime'], ['Estimated full-state legacy SRS total'],
        'https://ucr.fbi.gov/crime-in-the-u.s', access='unavailable_without_key_or_blocked'),
    'cps_monthly': _entry(1994, 2025, {'employment': {'employment', 'employment_share', 'employment_percent'},
        **_identity('employment_share', 'employment_percent')}, ['Labor'],
        ['Civilian noninstitutionalized ages15–79; sex/own-child filters; employment only'],
        'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html'),
    'cps_nurse_hours': _entry(1994, 2025, {'hours': {'hours', 'nurse_hours'}, 'nurse_hours': {'hours', 'nurse_hours'}},
        ['Labor', 'Health'], ['Registered nurses under audited CPS occupation crosswalk'],
        'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html'),
    'cps_voting': _entry(1980, 2024, _identity('voter_turnout'), ['Civil Rights'],
        ['Citizen civilian noninstitutional residents ages18+; even election years'],
        'https://www.census.gov/topics/public-sector/voting/data/tables.html'),
    'qcew_annual': _entry(2001, 2024, {'wages': {'wages', 'annual_avg_wkly_wage', 'avg_annual_pay'},
        'earnings': {'wages', 'annual_avg_wkly_wage', 'avg_annual_pay'},
        'jobs': {'jobs', 'employment'}, 'employment': {'employment', 'jobs'},
        **_identity('annual_avg_wkly_wage', 'avg_annual_pay')}, ['Labor', 'Macroeconomics'],
        ['Covered private jobs by exact NAICS; establishment state; no worker demographics/hours'],
        'https://www.bls.gov/cew/downloadable-data-files.htm'),
    'laus_state': _entry(1976, 2024, _identity('unemployment', 'unemployment_rate'), ['Labor'],
        ['Civilian noninstitutional ages16+; official full-state annual M13'],
        'https://download.bls.gov/pub/time.series/la/la.data.2.AllStatesU'),
    'acs_b14004': _entry(2005, 2024, _identity('public_college_enrollment'), ['Education'],
        ['Resident public-college enrollment, published B14004 population'],
        'https://www.census.gov/programs-surveys/acs/data.html', excluded_years=[2020]),
}
# California public access is verified; reuse rights need explicit review.
CONNECTOR_CATALOG['ca_doj_crime'].update(available=False, license_status='reuse_rights_unverified')
# Existing names retain identical measurement contracts.
CONNECTOR_CATALOG['qcew'] = CONNECTOR_CATALOG['qcew_annual']
CONNECTOR_CATALOG['cps_monthly_aggregates'] = CONNECTOR_CATALOG['cps_monthly']

# Bind one connector to one V2 series; union coverage would manufacture years
# or silently combine distinct age populations. This reads metadata only.
OUTCOME_V2_CATALOG = json.loads(Path(__file__).with_name('OUTCOME_CATALOG_V2.json').read_text())
for _series_id, _spec in OUTCOME_V2_CATALOG['series'].items():
    _years = _spec['years']
    _targets = {target: {_series_id} for target, definition in OUTCOME_V2_CATALOG['targets'].items()
                if _series_id in definition['series']}
    # V6.2 permits the verified behavioral target to use the published series
    # identity directly. Legal-instrument aliases remain leads; neither route
    # confers purpose approval or relaxes exact filters, access or coverage.
    _targets.update(_identity(_series_id))
    _entry_v2 = _entry(min(_years), max(_years), _targets, [], [_spec['population']],
        _spec['source_url'], excluded_years=set(range(min(_years), max(_years) + 1)) - set(_years))
    _entry_v2.update(series_id=_series_id, series_years=tuple(_years),
        fixed_population_filters=_spec.get('population_filters', {}),
        required_filters=tuple(_spec.get('required_filters', [])),
        allowed_causes=tuple(_spec.get('causes', [])), allowed_age_groups=tuple(_spec.get('age_groups', [])),
        frequencies=tuple(_spec['frequency']), outcome_unit=_spec['units'],
        source_validation_required=True, legal_certification_granted=False)
    CONNECTOR_CATALOG['outcome_v2_' + _series_id] = _entry_v2

# Rate defaults are prospective metadata; historical JSON catalogs are frozen
# byte-bound source contracts and are deliberately kept unchanged.
from .rates import scale_metadata as _scale_metadata
for _connector, _contract in CONNECTOR_CATALOG.items():
    _measures = _contract.get('measures', ())
    if len(_measures) == 1:
        _sid = next(iter(_measures))
        _contract.update(_scale_metadata(_sid, _contract.get('outcome_unit', '')))
    else:
        _contract['measure_outcome_scales'] = {m: _scale_metadata(m, '') for m in _measures}


def v2_supported(outcome, measure):
    """Require the exact series and declared published population at intake."""
    entry = CONNECTOR_CATALOG.get(outcome.get('connector'), {})
    if not entry.get('series_id') or measure != entry['series_id'] or outcome.get('series_id') != entry['series_id']:
        return False
    filters = outcome.get('population_filters', {})
    if not isinstance(filters, dict):
        return False
    fixed, required = entry['fixed_population_filters'], entry['required_filters']
    if fixed and filters != fixed:
        return False
    if not fixed and set(filters) != set(required):
        return False
    if 'cause_name' in required and filters['cause_name'] not in entry['allowed_causes']:
        return False
    if 'age_group' in required and filters['age_group'] not in entry['allowed_age_groups']:
        return False
    return outcome.get('frequency', 'annual') in entry['frequencies']


def target_mapped(connector, target, measure=None):
    """Check exact construct binding; external access/population remain separate.

    No substring/semantic inference or sign-dependent ranking. Access-blocked
    endpoints cannot confer an available connector mapping at S0.
    """
    if not isinstance(connector, str) or not isinstance(target, str):
        return False
    entry = CONNECTOR_CATALOG.get(connector)
    if not entry or not entry['available'] or entry['access'] != 'public_no_key' or target not in entry['targets']:
        return False
    return measure is None or (isinstance(measure, str) and measure in entry['target_measures'][target])
