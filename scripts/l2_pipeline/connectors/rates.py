"""Prospective event rates; legacy measurement contracts remain reproducible.

No treatment, policy associations, network requests, or denominator inference.
The historical catalogs stay unchanged because frozen cards bind their bytes.
"""
from __future__ import annotations

import copy
import csv
from dataclasses import replace
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from xml.etree import ElementTree
import zipfile

from .common import ConnectorError, ConnectorNotReady, NAMES, PanelData

ROOT = Path(__file__).resolve().parents[3]
LEGACY = Path(__file__).with_name('LEGACY_OUTCOME_SCALES.json')
CATALOG = Path(__file__).with_name('OUTCOME_RATE_CATALOG.json')


def card_hash(card):
    # Execution attaches only this non-scientific path to the registered card.
    declared = {k: v for k, v in card.items() if k != '_card_path'}
    return hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def legacy_card(card):
    return LEGACY.is_file() and card_hash(card) in json.loads(LEGACY.read_text())['cards']


@lru_cache(maxsize=1)
def source_units():
    units = {}
    # Literal resource paths are included in the static snapshot inventory.
    for path in (Path(__file__).with_name('OUTCOME_CATALOG_V2.json'),
                 Path(__file__).with_name('OUTCOME_CATALOG_V3.json'),
                 Path(__file__).with_name('OUTCOME_CATALOG_V4.json')):
        data = json.loads(path.read_text())
        units.update({sid: spec['units'] for sid, spec in data['series'].items()})
    return units


def source_unit(outcome):
    sid = outcome.get('series_id') or outcome.get('measure_id') or ''
    return source_units().get(sid, outcome.get('source_outcome_unit', outcome.get('outcome_unit', '')))


def count_kind(outcome):
    """Use declared source measures, not policy names or observed values."""
    sid = outcome.get('series_id') or outcome.get('measure_id') or ''
    # Unsupported secondaries deliberately carry outcome_unit=None; they must
    # remain parked by S2 without interrupting the implemented primary.
    unit = str(source_unit(outcome) or '').lower()
    if any(token in unit for token in ('usd', 'dollar', 'per capita', 'per 1,000 population')):
        return None
    if 'rate' in sid or 'percent' in unit:
        return None
    if 'birth' in sid or unit in ('births', 'live births'):
        return 'births'
    if 'death' in sid or unit in ('deaths', 'traffic deaths', 'fatalities'):
        return 'deaths'
    if sid.endswith('_count') or unit in ('count', 'counts', 'fatal crashes', 'persons',
                                         'jobs', 'thousands of payroll jobs'):
        return 'events'
    if outcome.get('connector') in ('qcew', 'qcew_annual', 'qcew_constructed') and sid in ('jobs', 'employment'):
        return 'events'
    if outcome.get('connector') == 'acs_b14004' and sid == 'public_college_enrollment':
        return 'events'
    return None


def resolved_scale(card):
    explicit = card.get('outcome_scale', card.get('outcome', {}).get('outcome_scale'))
    if explicit is not None:
        if explicit not in ('rate', 'count'):
            raise ConnectorError('outcome_scale must be rate or count')
        return explicit
    if legacy_card(card):
        return None  # No added fields/labels in archived panel serialization.
    # Outcome-only requests are the archival adapter contract, not design intake.
    if 'design_id' not in card:
        return None
    return 'rate' if count_kind(card.get('outcome', {})) else None


def prospective_card(card):
    """Return a fresh copy; never rewrite loaded archived or frozen bytes."""
    result = copy.deepcopy(dict(card))
    # C66 applies only to the new connector bindings; archived card bytes and
    # their frozen estimator never acquire a new sensitivity retroactively.
    outcome=result.get('outcome',{})
    if outcome.get('connector') in {'outcome_v15_ipeds_enrollment_per_population',
                                     'outcome_v15_ipeds_completions_per_population',
                                     'outcome_v16_ipeds_enrollment_per_population',
                                     'outcome_v16_ipeds_completions_per_population',
                                     'outcome_v17_ipeds_enrollment_per_population',
                                     'outcome_v17_ipeds_completions_per_population',
                                     'outcome_v18_ipeds_enrollment_per_population',
                                     'outcome_v18_ipeds_completions_per_population',
                                     'outcome_v19_ipeds_enrollment_per_population',
                                     'outcome_v19_ipeds_completions_per_population'}:
        declared=dict(name='ipeds_coverage_99',changes=dict(outcome=dict(institution_coverage_min=0.99)))
        entries=result.setdefault('sensitivities',[])
        existing=[s for s in entries if s.get('name')==declared['name']]
        if existing and (len(existing)!=1 or existing[0].get('changes')!=declared['changes']):
            raise ConnectorError('C66 IPEDS coverage sensitivity conflicts with required >=99% rule')
        if not existing:entries.append(declared)
        outcome.setdefault('institution_coverage_min',0.95)
    scale = resolved_scale(result)
    if scale is not None:
        result['outcome_scale'] = scale
        if scale == 'rate' and count_kind(result['outcome']):
            kind = count_kind(result['outcome'])
            result['outcome']['source_outcome_unit'] = source_unit(result['outcome']) or 'count'
            sid = result['outcome'].get('series_id', result['outcome'].get('measure_id'))
            peer = json.loads(CATALOG.read_text())['published_rate_peers'].get(sid)
            result['outcome']['outcome_unit'] = source_units().get(peer, rate_unit(kind))
    return result


def rate_unit(kind):
    return ('births per 1,000 women in declared maternal-age group' if kind == 'births'
            else kind + ' per 100,000 residents in declared age/sex population')


def scale_metadata(sid, units):
    kind = count_kind(dict(series_id=sid, outcome_unit=units))
    return dict(outcome_scale_default='rate' if kind else 'source',
                count_type=kind, rate_catalog=str(CATALOG.relative_to(ROOT)))


def _numeric(value):
    if value is None or str(value).strip().lower() in {'', 'not available', 'unreliable',
            'suppressed', '*', '**', 'na', 'n/a', 'null', '--'}:
        return None
    try:
        number = float(str(value).replace(',', ''))
    except ValueError as exc:
        raise ConnectorError('Invalid population denominator') from exc
    if not math.isfinite(number) or number <= 0:
        raise ConnectorError('Population denominator must be finite and positive')
    return number


def _wonder_denominators(request, kind, *, catalog_data=None):
    from .wonder import AGE_LABELS, parse_export, verify_receipt
    if catalog_data is None:
        if str(request.options.get('connector', '')).startswith('outcome_v6_'):
            from .outcome_v6 import catalog
        elif str(request.options.get('connector', '')).startswith('outcome_v5_'):
            from .outcome_v5 import catalog
        else:
            from .wonder_registry import catalog
        catalog_data = catalog()
    spec = catalog_data['series'][request.options['series_id']]
    field = 'Female Population' if kind == 'births' else 'Population'
    age = request.filters.get('age_group')
    values, reliability = {}, {}
    bound = {str((ROOT / item['raw_path']).resolve()): item for item in spec['exports']}
    for path in request.paths():
        export = bound.get(str(path.resolve()))
        if export is None:
            raise ConnectorError('Denominator must come from the bound WONDER export')
        verify_receipt(path, export)
        if age and export.get('age_schema') != ('10' if age in ('15-17', '18-19') else '9'):
            continue
        fields = next(csv.reader([path.read_text().splitlines()[0]], delimiter='\t'))
        if field not in fields:
            continue
        cells = parse_export(path, measure=field, mother_age=AGE_LABELS[age] if age else None)
        if set(values) & set(cells):
            raise ConnectorError('Overlapping denominator vintages')
        values.update({key: _numeric(cell['value']) for key, cell in cells.items()})
        published_rate = 'Fertility Rate' if kind == 'births' else 'Crude Rate'
        if published_rate in fields:
            reliability.update(parse_export(path, measure=published_rate,
                                           mother_age=AGE_LABELS[age] if age else None))
    return values, reliability


def _census_total(request):
    """Existing Census total-resident population only; never age/sex guesses."""
    from .common import sha
    from .wonder import scan_source
    base = ROOT / 'data/l4/covariates_public/raw/census_popest'
    urls = {
        'st8090ts.txt': 'https://www2.census.gov/programs-surveys/popest/tables/1980-1990/state/asrh/st8090ts.txt',
        'co-est2001-12-00.pdf': 'https://www2.census.gov/programs-surveys/popest/tables/1990-2000/intercensal/st-co/co-est2001-12-00.pdf',
        'st-est00int-01.csv': 'https://www2.census.gov/programs-surveys/popest/tables/2000-2010/intercensal/state/st-est00int-01.csv',
        'nst-est2020int-pop.xlsx': 'https://www2.census.gov/programs-surveys/popest/tables/2010-2020/intercensal/national/nst-est2020int-pop.xlsx',
        'NST-EST2025-ALLDATA.csv': 'https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/state/totals/NST-EST2025-ALLDATA.csv'}
    values, inv = {}, []
    for name, first, last in [('st8090ts.txt', 1980, 1989), ('co-est2001-12-00.pdf', 1990, 1999),
            ('st-est00int-01.csv', 2000, 2009), ('nst-est2020int-pop.xlsx', 2010, 2019),
            ('NST-EST2025-ALLDATA.csv', 2020, 2025)]:
        if not set(request.years) & set(range(first, last + 1)):
            continue
        path = base / name
        if not path.is_file():
            raise ConnectorNotReady('Existing Census denominator missing: ' + str(path))
        if path.suffix == '.pdf':
            try:
                from pypdf import PdfReader
            except ImportError:
                program = shutil.which('pdftotext')
                if not program:
                    raise ConnectorNotReady('Census 1990s PDF population requires existing pypdf or pdftotext; no installation attempted')
                result = subprocess.run([program, '-layout', str(path), '-'], capture_output=True, text=True, check=False)
                if result.returncode:
                    raise ConnectorNotReady('Existing Census PDF text extraction failed')
                text = result.stdout
            else:
                text = '\n'.join(p.extract_text() or '' for p in PdfReader(path).pages)
        elif path.suffix == '.xlsx':
            # Census's single table needs no spreadsheet runtime/package.
            with zipfile.ZipFile(path) as archive:
                ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                shared = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
                strings = [''.join(item.itertext()) for item in shared]
                sheet = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                xlsx_rows = []
                for cell_row in sheet.findall('.//x:row', ns):
                    cells = {}
                    for cell in cell_row.findall('x:c', ns):
                        value = cell.find('x:v', ns)
                        if value is None:
                            continue
                        cells[re.sub(r'\d', '', cell.attrib['r'])] = strings[int(value.text)] if cell.attrib.get('t') == 's' else value.text
                    xlsx_rows.append(cells)
                text = '\n'.join(strings)
        else:
            text = path.read_text(encoding='utf-8-sig')
        scan = scan_source(text.encode())
        if scan['suspicious_patterns']:
            raise ConnectorError('Suspicious source instructions in population material; independent review required')
        inv.append(dict(path=str(path), url=urls[name], size_bytes=path.stat().st_size, sha256=sha(path),
                        license='US Census Bureau federal government product; existing provenance license UNVERIFIED',
                        source_check=scan, ml_detector_run=False))
        if first == 1980:
            blocks = {}
            for line in text.splitlines():
                bits = line.split()
                if bits and bits[0] in NAMES.values() and all(x.isdigit() for x in bits[1:]):
                    blocks.setdefault(bits[0], []).extend(int(x) for x in bits[1:])
            if len(blocks) != 51 or any(len(v) != 11 for v in blocks.values()):
                raise ConnectorError('Census 1980s row/state inventory differs')
            parsed = [(unit, v[:10]) for unit, v in blocks.items()]
        elif first == 1990:
            numbers = [list(map(lambda x: int(x.replace(',', '')), re.findall(r'\d{1,3}(?:,\d{3})+', line)))
                       for line in text.splitlines() if len(re.findall(r'\d{1,3}(?:,\d{3})+', line)) == 12]
            if len(numbers) != 52:
                raise ConnectorError('Census 1990s national + state row inventory differs')
            # The final April 2000 column is not used; its national footnote
            # splits the printed number in PDF extraction. Reconcile every
            # selected July 1990–1999 column before mapping state positions.
            if any(sum(state[i] for state in numbers[1:]) != numbers[0][i] for i in range(1, 11)):
                raise ConnectorError('Census 1990s state totals fail national reconciliation')
            parsed = [(unit, v[1:11]) for unit, v in zip(NAMES.values(), numbers[1:])]
        elif first == 2010:
            parsed = [(NAMES[r.get('A', '').lstrip('.')], [r[c] for c in 'CDEFGHIJKL']) for r in xlsx_rows
                      if r.get('A', '').lstrip('.') in NAMES]
        elif first == 2000:
            parsed = [(NAMES[r[0].lstrip('.')], [int(v.replace(',', '')) for v in r[2:12]])
                      for r in csv.reader(text.splitlines()) if r and r[0].lstrip('.') in NAMES]
        else:
            parsed = [(NAMES[r['NAME']], [int(r['POPESTIMATE' + str(y)]) for y in range(2020, 2026)])
                      for r in csv.DictReader(text.splitlines()) if r['NAME'] in NAMES]
        if len(parsed) != 51:
            raise ConnectorError('Census population does not cover all 51 jurisdictions')
        for unit, populations in parsed:
            values.update({(unit, year): _numeric(value) for year, value in zip(range(first, last + 1), populations)})
    return values, tuple(inv)


def _declared_denominators(request, kind):
    """Exact local population CSV contract, usable with every count connector."""
    config = request.options.get('population_denominator')
    if not isinstance(config, dict):
        return None, ()
    if config.get('population_filters') != dict(request.filters):
        raise ConnectorError('Denominator population filters differ from the outcome')
    requested_sex = str(request.filters.get('sex', 'all')).lower()
    expected_sex = 'female' if kind == 'births' or requested_sex in ('female', 'women', 'f') else 'male' if requested_sex in ('male', 'men', 'm') else 'all'
    if config.get('sex') != expected_sex:
        raise ConnectorError('Denominator sex differs from declared outcome population')
    options = dict(request.options, paths=config.get('paths', []),
                   source_sha256=config.get('source_sha256', {}),
                   license=config.get('license', ''), source_urls=config.get('source_urls', {}))
    if not options['license'] or not options['source_sha256'] or not options['source_urls']:
        raise ConnectorError('Denominator requires source hashes, URLs, and license')
    names = options['paths'] if isinstance(options['paths'], list) else [options['paths']]
    if (not names or set(names) != set(options['source_sha256']) or
            set(names) != set(options['source_urls']) or
            any(not re.fullmatch(r'[a-f0-9]{64}', options['source_sha256'][n]) or
                not options['source_urls'][n].startswith('https://') for n in names)):
        raise ConnectorError('Every denominator path requires its own exact SHA-256 and public HTTPS URL')
    from .common import inventory
    source_request = replace(request, options=options)
    values, inv = {}, []
    for path in source_request.paths():
        inv.append(inventory(path, source_request))
        with path.open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            if not {'unit', 'year', 'population'} <= set(reader.fieldnames or []):
                raise ConnectorError('Denominator CSV requires unit/year/population')
            for cell in reader:
                key = cell['unit'], int(cell['year'])
                if key in values:
                    raise ConnectorError('Duplicate denominator state/year')
                values[key] = _numeric(cell['population'])
    return values, tuple(inv)


def _native_rate(request, panel, kind):
    """Published crude/fertility peers retain the source's own denominators."""
    peers = json.loads(CATALOG.read_text())['published_rate_peers']
    sid = request.options.get('series_id', request.options.get('measure_id'))
    peer = peers.get(sid)
    if peer is None:
        return None
    from . import ADAPTERS
    options = dict(request.options, outcome_scale='count')  # prevent recursive scale conversion
    if request.options['connector'] == 'cdc_natality':
        options.update(measure_id='birth_rate', value_field='state_rate')
        name = 'cdc_natality'
    else:
        name = request.options['connector'].rsplit(sid, 1)[0] + peer
        options.update(connector=name, series_id=peer, measure_id=peer)
        options.pop('value_field', None)
    if name not in ADAPTERS:
        return None
    published = ADAPTERS[name](replace(request, options=options))
    by_key = {(r['unit'], r['period']): r for r in published.rows}
    rows, metas = [], []
    for original, meta in zip(panel.rows, panel.metadata):
        peer_row = by_key.get((original['unit'], original['period']), {})
        # A rate never reveals a suppressed/missing numerator.
        value = peer_row.get('outcome_value') if meta['available'] else ''
        available = value not in ('', None)
        rows.append(dict(original, outcome_value=value if available else '',
                         outcome_unit=peer_row.get('outcome_unit', rate_unit(kind)),
                         outcome_scale='rate', rate_source_series_id=peer))
        metas.append(dict(meta, available=int(available), annual_usable=int(available),
                          missing_reason=None if available else 'Numerator or published rate unavailable'))
    return replace(panel, rows=tuple(rows), metadata=tuple(metas),
                   details=dict(panel.details, outcome_scale='rate', rate_source_series_id=peer,
                                denominator='Publisher Census age/sex population; never back-calculated',
                                rate_multiplier=1000 if kind == 'births' else 100000))


def apply_scale(request, panel, *, catalog_data=None):
    """A missing denominator parks readiness; it never restores count scale."""
    if request.options.get('outcome_scale') != 'rate' or panel.details.get('outcome_scale') == 'rate':
        return panel
    kind = count_kind(request.options)
    if kind is None:
        return panel  # already a source-published rate, percentage or continuous outcome
    native = _native_rate(request, panel, kind)
    if native is not None:
        return native
    values, extra = _declared_denominators(request, kind)
    reliability = {}
    if request.options.get('series_id', '').startswith('wonder_'):
        published_values, reliability = _wonder_denominators(request, kind, catalog_data=catalog_data)
        # An explicitly declared Census denominator cannot override the
        # original source's published unreliability or suppression flags.
        if values is None:
            values = published_values
    if values is None and any('source_population' in r for r in panel.rows):
        values = {(r['unit'], r['year']): _numeric(r.get('source_population')) for r in panel.rows}
    if values is None and kind == 'deaths':
        filters = request.filters
        all_age = (not any(k in filters for k in ('age_group', 'age_min', 'age_max'))
                   and set(filters) <= {'age', 'sex', 'race', 'ethnicity', 'cause_name', 'cause', 'population'}
                   and filters.get('population', 'all') in ('all', 'all_residents', 'all_traffic_fatalities')
                   and filters.get('age', 'All Ages') == 'All Ages'
                   and filters.get('sex', 'all') in ('all', 'All', 'Both Sexes')
                   and filters.get('race', 'all') in ('all', 'All Races', 'All Races-All Origins')
                   and filters.get('ethnicity', 'all') in ('all', 'All Origins'))
        occurrence = request.options.get('series_id', '').startswith('cdc_vsrr_')
        if all_age and not occurrence:
            values, extra = _census_total(request)
    if values is None:
        raise ConnectorNotReady('Rate default requires exact age/sex Census/WONDER population denominator; declare outcome.population_denominator')
    required = {(r['unit'], r['year']) for r, m in zip(panel.rows, panel.metadata) if m['available']}
    missing = sorted(key for key in required if values.get(key) is None)
    if missing:
        raise ConnectorNotReady('Population denominator unavailable for rate: ' + repr(missing) +
                                '; birth D10 1999–2002 has no female population; no count fallback or back-calculation')
    multiplier = 1000 if kind == 'births' else 100000
    count_multiplier = 1000 if source_unit(request.options) == 'thousands of payroll jobs' else 1
    rows, metas = [], []
    for original, meta in zip(panel.rows, panel.metadata):
        denominator = values.get((original['unit'], original['year']))
        published = reliability.get((original['unit'], original['year']), {})
        unreliable = original.get('unreliable', False) or published.get('unreliable', False)
        available = bool(meta['available']) and denominator is not None and not unreliable
        value = float(original['outcome_value']) * count_multiplier / denominator * multiplier if available else ''
        rows.append(dict(original, outcome_value=value, outcome_unit=rate_unit(kind),
                         outcome_scale='rate', population_denominator=denominator,
                         rate_multiplier=multiplier, unreliable=unreliable))
        metas.append(dict(meta, available=int(available), annual_usable=int(available),
                          denominator_available=int(denominator is not None), unreliable=unreliable,
                          missing_reason='Published rate unreliable; no ratio rescue' if unreliable else meta.get('missing_reason')))
    limitations = panel.limitations
    if request.options.get('connector') == 'fars_state':
        limitations += ('Rates retain crash-state fatalities divided by same-state resident population; denominator does not change numerator geography.',)
    return replace(panel, rows=tuple(rows), metadata=tuple(metas), inventory=panel.inventory + extra,
                   limitations=limitations,
                   details=dict(panel.details, outcome_scale='rate', rate_multiplier=multiplier,
                                source_count_multiplier=count_multiplier,
                                denominator_back_calculation=False, count_fallback=False))
