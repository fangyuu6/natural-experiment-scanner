"""Run6 read-only source adapters. Reparse saved public bytes; never interpolate.

Usage: python3 -m scripts.l2_pipeline.run6_adapters.social.build_social
Only this directory is written. Downloads are acquired separately with the
existing download_public initial local normalized heuristic check.
"""
from __future__ import annotations
import hashlib
import html
from html.parser import HTMLParser
import json
from scripts.l2_pipeline.run6_adapters.paths import require
from pathlib import Path
import re

from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT / 'social'
NAMES = 'Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming'.split('|')
ABBR = 'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()
STATES = dict(zip(NAMES, ABBR))


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', value))).strip()


class Tables(HTMLParser):
    """Extract complete rows and keep superscript annotations in the raw cells."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.cell_tag = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.finish_row()
            self.row = []
        if tag in {'th', 'td'}:
            self.finish_cell()
            self.cell = ''
            self.cell_tag = tag
        if tag == 'br' and self.cell is not None:
            self.cell += ' '

    def handle_data(self, value):
        if self.cell is not None:
            self.cell += value

    def handle_endtag(self, tag):
        if tag in {'th', 'td'}:
            self.finish_cell()
        if tag == 'tr':
            self.finish_row()

    def finish_cell(self):
        if self.cell is not None and self.row is not None:
            self.row.append(re.sub(r'\s+', ' ', self.cell).strip())
        self.cell = None

    def finish_row(self):
        self.finish_cell()
        if self.row is not None:
            self.rows.append(self.row)
        self.row = None


def rows(path):
    parser = Tables()
    parser.feed(path.read_text())
    parser.finish_row()
    return parser.rows


def provenance(name):
    manifest = json.loads((ROOT / 'DOWNLOADS.json').read_text())
    item = next(r for r in manifest if Path(r['path']).name == name)
    require('sha256' in item, 'Run6 validation failed: social/build_social.py:78')
    path = ROOT / 'raw' / name
    require(item['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest(), 'Run6 validation failed: social/build_social.py:80')
    return {k: item[k] for k in ('url', 'size_bytes', 'sha256', 'license')} | {'source_path': str(path)}


def policy(tracker, identifier, title, name):
    prov = provenance(name)
    return {'tracker_id': tracker, 'policy_id': identifier, 'family_id': identifier,
            'title': title, 'source_url': prov.pop('url'), **prov,
            'history_complete': False, 'repeals_included': False,
            'interval_rows': [], 'annual_rows': [], 'events': [],
            'run6_disposition': 'source_lead_only', 'count_toward_distinct_ready_families': False}


def dol():
    name = 'dol_history.html'
    output = policy('dol_whd', 'state_minimum_wage', 'State basic nonfarm minimum wage', name)
    snapshots = []
    years = []
    for row in rows(ROOT / 'raw' / name):
        if row and row[0] == 'State or other jurisdiction':
            years = [int(re.search(r'\d{4}', v).group()) for v in row[1:]]
        elif row and row[0] in STATES and years:
            require(len(row) == len(years) + 1, (row[0], years, row))
            for year, value in zip(years, row[1:]):
                scalar = re.fullmatch(r'\$?(\d+(?:\.\d+)?|\.\d+)', value)
                snapshots.append({'unit': STATES[row[0]], 'year': year,
                                  'active': None, 'raw_value': value,
                                  'numeric_value': float(scalar.group(1)) if scalar else None,
                                  'snapshot_month': 2 if year in (1968, 1972) else 1,
                                  'snapshot_day': 1,
                                  'locator': f'{row[0]}/{year}',
                                  'definition_requires_resolution': scalar is None})
    require(len(snapshots) == 51 * 39, len(snapshots))
    output.update({'definition': 'Basic state nonfarm rates at January 1, except February 1968/1972; retain employer/worker exclusions, tiered schedules and original units.',
                   'as_of': '2025-01 (source stated last revision)',
                   'coverage': {'start_year': 1968, 'end_year': 2024, 'observed_years': sorted({r['year'] for r in snapshots}), 'units': 51},
                   'snapshot_rows': snapshots,
                   'annual_rows': snapshots,
                   'history_completeness_rule': '39 explicit snapshot years; 2000–2024 columns contiguous. Snapshots do not certify all-calendar-day exposures or a unified applicable wage.',
                   'repeal_scope': 'Lower observed rates retained; between-snapshot repeal or reinstatement dates unavailable.',
                   'duplicate_candidate': 'Existing L2 minimum-wage design; do not enqueue a second copy.',
                   'source_quality_flags': [{'unit': 'FL', 'year': 1981, 'raw_value': '$12.00', 'reason': 'Unexpected historical cell; official audit required before use.'}, {'unit': 'GA', 'year': 1981, 'raw_value': '$5.15', 'reason': 'Unexpected historical cell; official audit required before use.'}],
                   'stop_reason': 'Incomplete pre-2000 years, within-year clock absent, tier definitions and suspect cells require audit; duplicates existing minimum-wage design.'})
    return output


def dol_indexing():
    name = 'dol_current.html'
    output = policy('dol_whd_current', 'minimum_wage_indexing', 'Automatic state minimum wage indexing', name)
    raw = (ROOT / 'raw' / name).read_text()
    blocks = re.split(r'<h2[^>]*>', raw, flags=re.I)
    snippets = []
    for block in blocks[1:]:
        title, _, rest = block.partition('</h2>')
        state = clean(title)
        if state not in STATES:
            continue
        paragraphs = [clean(v) for v in re.findall(r'<p\b[^>]*>(.*?)</p>', rest, re.I | re.S)]
        evidence = [v for v in paragraphs if re.search(r'adjusted annually|indexed|consumer price|employment cost index', v, re.I)]
        for text in evidence:
            snippets.append({'unit': STATES[state], 'snapshot_date': '2026-07-01', 'raw_value': text,
                             'active': None, 'locator': f'{state}/indexing paragraph',
                             'future_implementation': 'scheduled' in text.lower()})
    output.update({'definition': 'Automatic adjustment of statutory rate under a price/earnings formula; distinct from legislated level increases.',
                   'as_of': '2026-07-01', 'coverage': {'start_year': 2026, 'end_year': 2026, 'units': len({r['unit'] for r in snippets})},
                   'snapshot_rows': snippets, 'states': sorted({r['unit'] for r in snippets}),
                   'repeal_scope': 'Current/future paragraphs cannot establish previous suspensions, repeals, restart dates, or explicit nonindexing in omitted states.',
                   'stop_reason': 'No national longitudinal indexing history acquired; need adoption, pause, repeal and reinstatement intervals for every state.'})
    return output


def nces():
    name = 'nces_policy.html'
    attendance = policy('nces_digest_234_10', 'compulsory_school_attendance', 'Compulsory school attendance age range', name)
    kindergarten = policy('nces_digest_234_10', 'kindergarten_requirement', 'State kindergarten program and attendance requirements', name)
    observed = []
    kind = []
    years = [2000, 2002, 2004, 2006, 2010, 2015, 2017, 2020]
    for row in rows(ROOT / 'raw' / name):
        if not row or row[0] not in STATES:
            continue
        require(len(row) == 27, (row[0], len(row)))
        for i, year in enumerate(years):
            value, footnote = row[1 + 2*i:3 + 2*i]
            match = re.fullmatch(r'(\d+) to (\d+)', value)
            observed.append({'unit': STATES[row[0]], 'year': year, 'active': None,
                             'raw_value': value, 'footnote': footnote,
                             'minimum_age': int(match.group(1)) if match else None,
                             'maximum_age': int(match.group(2)) if match else None,
                             'locator': f'Table234.10/{row[0]}/attendance/{year}'})
        kind.append({'unit': STATES[row[0]], 'year': 2020, 'active': None,
                     'offer_program_raw': row[23], 'offer_full_day_raw': row[24],
                     'attendance_raw': row[25], 'footnote': row[26],
                     'locator': f'Table234.10/{row[0]}/kindergarten/2020'})
    require(len(observed) == 51*8 and len(kind) == 51, 'Run6 validation failed: social/build_social.py:174')
    historical = []
    historical_kind = []
    historical_sources = []
    for older_name, older_years, kind_year, offer, full, attendance_col in [
        ('nces_policy_2008.html', [2000, 2002, 2004, 2006, 2007], 2008, 15, 17, 19),
        ('nces_policy_2014.html', [2000, 2002, 2004, 2006, 2008, 2010, 2014], 2014, 21, 22, 23),
    ]:
        historical_sources.append(provenance(older_name))
        all_rows = rows(ROOT / 'raw' / older_name)
        for i, row in enumerate(all_rows):
            if older_name == 'nces_policy_2014.html' and row and row[0] == 'Columbia':
                require(all_rows[i-1][0] == 'District of' and all(not v for v in all_rows[i-1][1:]), 'Run6 validation failed: social/build_social.py:186')
                row[0] = 'District of Columbia'
        table_rows = [r for r in all_rows if r and r[0] in STATES]
        require(len(table_rows) == 51, 'Run6 validation failed: social/build_social.py:189')
        for row in table_rows:
            for i, year in enumerate(older_years):
                historical.append({'unit': STATES[row[0]], 'year': year, 'active': None,
                                   'raw_value': row[1+2*i], 'footnote': row[2+2*i],
                                   'source_name': older_name,
                                   'locator': f'{older_name}/{row[0]}/attendance/{year}'})
            historical_kind.append({'unit': STATES[row[0]], 'year': kind_year, 'active': None,
                                    'offer_program_raw': row[offer], 'offer_full_day_raw': row[full],
                                    'attendance_raw': row[attendance_col],
                                    'raw_cells_with_footnotes': row[offer:], 'source_name': older_name,
                                    'locator': f'{older_name}/{row[0]}/kindergarten/{kind_year}'})
    attendance.update({'definition': 'Legal compulsory attendance minimum/maximum ages; preserve footnoted conditions and state exceptions.', 'as_of': '2020 latest observation; Digest2021 publication',
                       'coverage': {'start_year': 2000, 'end_year': 2020, 'observed_years': years, 'units': 51},
                       'annual_rows': observed, 'snapshot_rows': observed, 'states': ABBR,
                       'historical_snapshot_rows': historical, 'historical_sources': historical_sources,
                       'repeal_scope': 'Age changes across eight source snapshots retained; actual change/repeal date and intervening reversals unknown.',
                       'stop_reason': 'Selected snapshots only; no complete annual history and no first-order K–12 attendance connector in requested outcome set.'})
    kindergarten.update({'definition': 'State requirement to offer kindergarten/full-day programs or to attend kindergarten; conditional district/child exemptions retained.',
                         'as_of': '2020', 'coverage': {'start_year': 2008, 'end_year': 2020, 'observed_years': [2008, 2014, 2020], 'units': 51},
                         'annual_rows': historical_kind + kind, 'snapshot_rows': historical_kind + kind, 'states': ABBR,
                         'historical_sources': historical_sources,
                         'repeal_scope': 'Three snapshots preserve observed requirement changes; intervening repeal/exception changes unknown.',
                         'stop_reason': 'Selected2008/2014/2020 snapshots only; IPEDS postsecondary enrollment is not a first-order outcome of kindergarten attendance.'})
    return [attendance, kindergarten]


def nber():
    name = 'nber_eitc.html'
    output = policy('nber_taxsim_eitc', 'state_eitc', 'State earned income tax credit parameters', name)
    raw = (ROOT / 'raw' / name).read_text()
    parsed = []
    carried = [None]*6
    for index, row in enumerate(re.split(r'<tr\b[^>]*>', raw, flags=re.I)[1:]):
        parts = re.split(r'<td\b[^>]*>', row, flags=re.I)[1:]
        if len(parts) != 6:
            continue
        values = [clean(v.split('</tr>')[0]) for v in parts]
        for i in range(5):
            if values[i] == '"':
                values[i] = carried[i]
            elif values[i]:
                carried[i] = values[i]
        if values[1] not in ABBR:
            continue
        parsed.append({'unit': values[1], 'rate_raw': values[2], 'years_raw': values[3],
                       'refundability_raw': values[4], 'notes_raw': values[5],
                       'locator': f'TAXSIM/table/row{index+1}'})
    require(len(parsed) > 100, len(parsed))
    annual = []
    for record in parsed:
        spec = record['years_raw'] or ''
        tokens = re.split(r'\s*(?:,|and)\s*', spec)
        specified = []
        malformed = False
        for token in tokens:
            token = re.sub(r'\s+', '', token)
            match = re.fullmatch(r'(\d{4})(?:-(\d{2}|\d{4})|(\+))?', token)
            if not match:
                malformed = True
                break
            start = int(match.group(1))
            end = 2018 if match.group(3) else int(match.group(2) or start)
            if end < 100:
                end += start//100*100
            if end < start:
                malformed = True
                break
            specified.extend(range(start, min(end, 2018)+1))
        record['year_parse_valid'] = not malformed
        if not malformed:
            for year in specified:
                annual.append({'unit': record['unit'], 'year': year, 'active': None,
                               'credit_component_present': 1,
                               'rate_raw': record['rate_raw'], 'refundability_raw': record['refundability_raw'],
                               'notes_raw': record['notes_raw'], 'locator': record['locator']})
    output.update({'definition': 'TAXSIM component schedules for EITC percentages, refundability and eligibility; simultaneous MD/RI components remain separate rows within one family.',
                   'as_of': '2019-08 source certification; through tax year2018 only',
                   'coverage': {'start_year': 1977, 'end_year': 2018, 'states_with_explicit_rows': sorted({r['unit'] for r in parsed})},
                   'parameter_rows': parsed, 'annual_rows': annual, 'states': sorted({r['unit'] for r in parsed}),
                   'repeal_scope': 'NC note explicitly reports credit ending2014; omitted states/years are not coded as nonadoption. Other repeals outside explicit schedules not certified.',
                   'source_quality_flags': ['MD years_raw=20084-14 malformed and unexpanded.', 'Minnesota Working Family Credit is excluded by source table and documented externally.', 'Title1977–2018 and scattered future entries inconsistent; cap expansion at explicitly certified2018.'],
                   'duplicate_candidate': 'Existing L2 EITC design; parameter/refundability clauses do not add families.',
                   'stop_reason': 'No explicit full51-state zero history, Minnesota excluded, unresolved eligibility/components and source typos; duplicates existing EITC design.'})
    return output


def main(argv=None):
    import argparse
    global ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=ROOT)
    args=parser.parse_args(argv)
    ROOT=args.data_root.resolve()
    policies = []
    failures = []
    for source_name, builder in (('dol', dol), ('dol_indexing', dol_indexing), ('nces', nces), ('nber', nber)):
        try:
            result = builder()
            policies.extend(result if isinstance(result, list) else [result])
        except Exception as exc:
            failures.append(dict(source=source_name, status='stopped', error=str(exc)))
    (ROOT / 'POLICIES.json').write_text(json.dumps(policies, indent=2)+'\n')
    by_id = {policy['policy_id']: policy for policy in policies}
    def count(identifier, key):
        return len(by_id.get(identifier, {}).get(key, []))
    checks = {'source_hashes_verified': not failures,
              'dol_snapshots': count('state_minimum_wage', 'annual_rows'),
              'nces_attendance_snapshots': count('compulsory_school_attendance', 'annual_rows'),
              'nces_kindergarten_snapshots': count('kindergarten_requirement', 'annual_rows'),
              'nber_parameter_rows': count('state_eitc', 'parameter_rows'),
              'nber_explicit_component_years': count('state_eitc', 'annual_rows'),
              'ready_distinct_families': 0, 'outcome_values_read': False,
              'zero_filled_missing_source_rows': False, 'interpolated_snapshots': False,
              'failures': failures}
    (ROOT / 'ADAPTER_CHECKS.json').write_text(json.dumps(checks, indent=2)+'\n')
    print(json.dumps(checks))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
