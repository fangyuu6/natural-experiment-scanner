"""Explicit total-ownership sum from certified complete official QCEW slices.

2004+ suppression is represented by published N rows, while nonexistent macro
cells do not appear (official FAQ 13). Absent components may therefore be zero
only for hash-bound complete official industry slices. An N component makes the
sum unavailable; suppression is never inferred or reconstructed.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, STATES, sha
from .published import _finalize, _numeric

COMPONENTS = ('1', '2', '3', '5')
BASIS_URLS = (
    'https://www.bls.gov/cew/questions-and-answers.htm',
    'https://www.bls.gov/cew/additional-resources/open-data/csv-data-slices.htm',
    'https://www.bls.gov/cew/classifications/ownerships/ownership-titles.htm',
)


def total_ownership(request: ConnectorRequest):
    from .public_annual import _csv_rows, _licensed
    if request.options.get('source_geography', 'state') != 'state':
        raise ConnectorNotReady('Certified QCEW ownership sum currently requires official state rows')
    if request.frequency != 'annual' or min(request.years) < 2004:
        raise ConnectorNotReady('Certified missing ownership components require annual 2004+ QCEW publication rules')
    measure = request.options.get('value_field', 'annual_avg_emplvl')
    if measure not in ('annual_avg_emplvl', 'annual_avg_estabs_count', 'total_annual_wages'):
        raise ConnectorNotReady('QCEW total ownership sum supports additive measures only')
    name = request.options.get('ownership_completeness_certificate')
    if not name:
        raise ConnectorNotReady('QCEW total ownership sum requires a complete official industry-slice certificate')
    guarded = replace(request, options=dict(request.options, paths=[name]))
    certificate_path = guarded.paths()[0]
    certificate = json.loads(certificate_path.read_text())
    industry = str(request.filters['industry'])
    if (certificate.get('schema') != 'qcew_complete_industry_ownership_v1'
            or certificate.get('industry') != industry
            or certificate.get('full_industry_slices') is not True
            or certificate.get('absent_component_rule') != 'nonexistent_macro_cell_zero'
            or certificate.get('ownership_components') != list(COMPONENTS)
            or not set(request.years) <= set(certificate.get('years', []))
            or not set(BASIS_URLS) <= {b.get('url') for b in certificate.get('official_basis', [])}):
        raise ConnectorError('QCEW ownership certificate scope or official basis mismatch')
    request = _licensed(request, 'Bureau of Labor Statistics')
    paths = request.paths()
    certified = {str(Path(s['path']).resolve()): s for s in certificate.get('source_files', [])}
    file_years, seen = set(), {}
    for path in paths:
        binding = certified.get(str(path.resolve()))
        if not binding or binding.get('sha256') != sha(path) or binding.get('size_bytes') != path.stat().st_size:
            raise ConnectorError('QCEW ownership certificate does not bind complete source bytes')
        year = binding.get('year')
        if type(year) is not int or year in file_years:
            raise ConnectorError('QCEW complete ownership sources require one industry slice per year')
        file_years.add(year)
        expected = f'https://data.bls.gov/cew/data/api/{year}/a/industry/{industry}.csv'
        if binding.get('url') != expected or binding.get('final_url', expected) != expected:
            raise ConnectorError('QCEW ownership source must be the complete official annual industry slice')
        count = 0
        for source in _csv_rows(path):
            count += 1
            if source.get('industry_code') != industry or source.get('year') != str(year) or source.get('qtr') != 'A':
                raise ConnectorError('QCEW certified slice contains out-of-scope records')
            area, ownership = source['area_fips'].zfill(5), source['own_code']
            if (area[:2] not in STATES or area[2:] != '000' or source['size_code'] != '0'
                    or STATES[area[:2]] not in request.units or year not in request.years):
                continue
            if ownership not in COMPONENTS and ownership != '0':
                raise ConnectorError('Unexpected QCEW ownership in certified state slice')
            key = area, year, ownership
            if key in seen:
                raise ConnectorError('Duplicate certified QCEW state/year/ownership')
            disclosure = source['disclosure_code']
            if disclosure not in ('', 'N'):
                raise ConnectorError('Unknown certified QCEW disclosure code')
            field = 'annual_avg_estabs' if measure == 'annual_avg_estabs_count' and measure not in source else measure
            if field not in source:
                raise ConnectorError('QCEW additive field absent: ' + measure)
            value = '' if disclosure == 'N' else source[field]
            if value != '':
                _numeric(value)
            seen[key] = dict(value=value, suppressed=disclosure == 'N', path=str(path))
        if count == 0:
            raise ConnectorError('Empty complete QCEW industry slice')
    if not set(request.years) <= file_years:
        raise ConnectorNotReady('A requested year lacks its certified complete QCEW slice')
    values = {}
    for fips, unit in STATES.items():
        if unit not in request.units:
            continue
        for year in request.years:
            area = fips + '000'
            direct = seen.get((area, year, '0'))
            components = [seen.get((area, year, own)) for own in COMPONENTS]
            present = [c for c in components if c is not None]
            missing_codes = [own for own, c in zip(COMPONENTS, components) if c is None]
            suppressed = any(c['suppressed'] for c in present)
            if direct is not None:
                # A disclosed direct total can include suppressed components.
                value, suppressed = direct['value'], direct['suppressed']
                sources = direct['path']
                used_direct = 1
            else:
                value = '' if suppressed or any(c['value'] == '' for c in present) else sum(_numeric(c['value']) for c in present)
                sources = ';'.join(sorted({c['path'] for c in present}))
                used_direct = 0
            values[unit, year] = dict(value=value, source=sources, linkage_certified=True,
                counts=dict(suppression_flag=int(suppressed), ownership_components_expected=4,
                    ownership_components_present=len(present), certified_absent_components=len(missing_codes),
                    certified_absent_ownership_codes=';'.join(missing_codes), published_total_used=used_direct))
    panel = _finalize(request, values, paths, 'total UI/UCFE-covered ' + measure,
        ('All ownerships are federal/state/local/private; nonprofit or government EMS classified outside NAICS621910 remains outside this establishment outcome.',
         'Sum of published annual averages can differ from a separately rounded total (BLS FAQ9). Any suppressed component makes a constructed total unavailable.'))
    return replace(panel, details=dict(source_geography='state', measure=measure,
        ownership_aggregation='direct ownership0 if present; otherwise sum ownership1+2+3+5',
        absent_component_rule='zero only for hash-certified complete official industry slices under 2004+ FAQ13',
        ownership_completeness_certificate=str(certificate_path), certificate_sha256=sha(certificate_path),
        certificate=certificate, official_basis_urls=BASIS_URLS))
