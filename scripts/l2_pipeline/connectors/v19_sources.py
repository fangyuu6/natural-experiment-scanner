"""Catalog V19 source definitions: six BLS QCEW private-ownership industry slices (T273E, 2026-10-23).

NAICS 6213 (offices of other health practitioners), 4853 (taxi and limousine service) and sector 23 (construction),
establishments / employment / average weekly wage, read from the pinned raw files under
``data/l2_pipeline/connectors_v2/bls_qcew_industry/raw`` by the unchanged V18 QCEW parser. Local bytes only, no network.
V18's parser module is not edited: the V19 list is passed to it the same way the V18 tests do.
"""
from . import v18_sources
from .v18_build import settings

QCEW_SLICES_V19 = [
    # sid, naics, field, units, construct, population text
    ('bls_qcew_private_offices_of_other_health_practitioners_establishments', '6213', 'annual_avg_estabs_count', 'establishments (annual average)',
     'other_health_practitioner_office_establishments',
     'Private-ownership establishments in NAICS 6213 (offices of other health practitioners), state, annual average'),
    ('bls_qcew_private_offices_of_other_health_practitioners_employment', '6213', 'annual_avg_emplvl', 'jobs',
     'other_health_practitioner_office_payroll_employment',
     'Private-ownership employment in NAICS 6213 (offices of other health practitioners), state, annual average of covered employment'),
    ('bls_qcew_private_taxi_limousine_employment', '4853', 'annual_avg_emplvl', 'jobs', 'taxi_limousine_payroll_employment',
     'Private-ownership employment in NAICS 4853 (taxi and limousine service), state, annual average of covered employment'),
    ('bls_qcew_private_taxi_limousine_establishments', '4853', 'annual_avg_estabs_count', 'establishments (annual average)',
     'taxi_limousine_establishments',
     'Private-ownership establishments in NAICS 4853 (taxi and limousine service), state, annual average'),
    ('bls_qcew_private_construction_avg_weekly_wage', '23', 'annual_avg_wkly_wage', 'USD per week (nominal)', 'construction_wages',
     'Private-ownership average weekly wage in NAICS sector 23 (construction), state, covered employment'),
    ('bls_qcew_private_construction_employment', '23', 'annual_avg_emplvl', 'jobs', 'construction_payroll_employment',
     'Private-ownership employment in NAICS sector 23 (construction), state, annual average of covered employment'),
]

# One-line phrase aliases (free in V18 targets and V18 population aliases) so the T254/T269 target text works unchanged.
PHRASE_ALIASES = {
    'other_health_practitioner_office_establishments': 'entry of independent practitioner practices',
    'taxi_limousine_payroll_employment': 'employment in taxi and limousine service',
    'construction_wages': 'construction wages',
}

CAVEATS = {
    '6213': 'NAICS 6213 covers chiropractors, optometrists, mental health practitioners, physical, occupational and speech therapists, podiatrists and others; many nurse-practitioner practices are coded 6211 (offices of physicians), so this series is a proxy that dilutes the signal.',
    '4853': 'QCEW counts unemployment-insurance-covered payroll employers only: ride-hailing drivers are independent contractors and are not in payroll, so this series measures the incumbent payroll taxi and limousine sector; the 2022 NAICS vintage (data from 2023) may move ridesharing services into 4853.',
    '23': 'Sector 23 is all construction (residential building, heavy civil, specialty trades), not only prevailing-wage public works, so any effect is diluted; the average weekly wage is total wages divided by average employment and 13 weeks, nominal and composition-sensitive.',
}


def all_series():
    """The six V19 slices, parsed by V18's own QCEW parser (disclosure code N -> missing and flagged)."""
    with settings(v18_sources, QCEW_SLICES=QCEW_SLICES_V19):
        out = v18_sources.qcew_slices()
    for s in out:
        s.aliases = [s.construct]
        s.limitations = list(s.limitations) + [CAVEATS[s.filters['industry']]]
    return out
