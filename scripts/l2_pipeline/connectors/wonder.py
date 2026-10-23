"""Audited CDC WONDER web-form transport and offline aggregate export connector.

The public XML API is intentionally not used. Live transport requires a reviewed
HTML form, its exact fields, a robots receipt, and a semantic query plan. After
any CAPTCHA/access-control response the persistent stop marker forbids requests.
No cookies, passwords, account tokens or secret headers are written to disk.
"""
from __future__ import annotations
import argparse
import base64
import codecs
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from scripts.l2_pipeline.connectors.common import (
    ConnectorError, ConnectorNotReady, ConnectorRequest, NAMES, STATES,
    PanelData, row, metadata, inventory)

ROOT = Path(__file__).resolve().parents[3] / 'data/l2_pipeline/connectors_v2/wonder'
PERMISSION = Path('docs/plans/l2-pipeline/OWNER_PERMISSION_CDC_WONDER.md')
LICENSE = 'US government public domain; CDC WONDER Data Use Restrictions: statistical use only, no re-identification, no back-calculation of suppressed cells; cite database/version/query date'
BLOCK_PATTERNS = (r'captcha', r'recaptcha', r'hcaptcha', r'cf-chl-',
                  r'verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human',
                  r'checking your browser', r'bot[ -]?check', r'access denied',
                  r'automated requests.*blocked')
MISSING = {'', 'suppressed', 'unreliable', 'not applicable', 'not available',
           'n/a', 'na', '*', '**', '--', '---', '...', '…', '1-9'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_once(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if path.exists():
        if path.read_text() != content:
            raise ConnectorError('Refuse overwrite of audited artifact: ' + str(path))
    else:
        with path.open('x') as stream:
            stream.write(content)


def scan_source(body):
    """Skill initial local normalization/heuristic check, not an ML detector."""
    text = unicodedata.normalize('NFKC', body.decode('utf-8', 'replace'))
    text = ''.join(c for c in text if c not in '\u200b\u200c\u200d\u2060\ufeff'
                   and not 0xE0000 <= ord(c) < 0xE0080)
    # Include common encoding wrappers during the local skill initial check.
    normalized=text
    for token in re.findall(r'[A-Za-z0-9+/=]{20,}', text):
        try:
            decoded=base64.b64decode(token).decode('utf-8')
            if decoded.isprintable(): normalized+='\n'+decoded
        except (ValueError,UnicodeDecodeError):
            pass
    normalized+='\n'+codecs.decode(text,'rot_13')
    patterns = [r'ignore (?:all |the )?(?:previous|prior) instructions',
                r'(?:assistant|chatgpt)\s+(?:must|should)\s+',
                r'(?:send|reveal|print)\s+(?:your )?(?:credentials|system prompt|api key)']
    hits = [p for p in patterns if re.search(p, normalized, re.I)]
    return dict(method='local Unicode normalization, Base64/ROT13 decoding and heuristic initial check',
                ml_detector_run=False, reason='llm_guard/transformers not installed',
                suspicious_patterns=hits, sha256=digest(body),
                source_authority='untrusted_data', clean_scan_proves_safety=False)


class WebClient:
    """Credential-free WONDER web requests, >=10s between requests, no retries.

    Cookies stay in memory only. Persist exact POST fields/timestamps and response
    receipts before exposing data. HTTP429 parks; obey Retry-After by leaving
    retry to a later invocation. HTTP403, CAPTCHA and access denied stop durably.
    """
    def __init__(self, root=ROOT, opener=None, interval=10, clock=time.monotonic,
                 sleep=time.sleep):
        if interval < 10 or not math.isfinite(interval):
            raise ConnectorError('WONDER requests require interval >=10 seconds')
        self.root, self.interval, self.clock, self.sleep = Path(root), interval, clock, sleep
        self.last = None
        self.opener = opener or urllib.request.build_opener(
            _WonderRedirects(),
            urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, url, label, fields=None, semantic_query=None):
        if (self.root / 'BLOCKED.json').exists():
            raise ConnectorNotReady('WONDER stopped by persistent BLOCKED.json; browser hub handoff required')
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != 'wonder.cdc.gov'
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.fragment or parsed.query):
            raise ConnectorError('Only plain official WONDER HTTPS form endpoints')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', label):
            raise ConnectorError('Unsafe response label')
        if fields is not None:
            if not (self.root / 'TERMS_ACCEPTANCE.json').is_file() and label != 'accept_terms':
                raise ConnectorNotReady('Record authorized terms acceptance before querying')
            if not isinstance(fields, list) or any(
                not isinstance(p, (list, tuple)) or len(p) != 2 or
                any(not isinstance(x, str) for x in p) for p in fields):
                raise ConnectorError('Exact form fields must be a list of string pairs')
            if any(re.search(r'password|secret|api.?key|authorization', k, re.I) for k, _ in fields):
                raise ConnectorError('Credential fields are forbidden')
        if self.last is not None:
            self.sleep(max(0, self.interval - (self.clock() - self.last)))
        self.last = self.clock()
        record = dict(url=url, timestamp_utc=utc(), method='POST' if fields is not None else 'GET',
                      form_fields=fields, semantic_query=semantic_query, source_authority='untrusted_data')
        if fields is not None:
            record['owner_permission_sha256'] = digest(PERMISSION.read_bytes())
        audit = self.root / 'requests' / (label + '.request.json')
        write_once(audit, record)
        data = urllib.parse.urlencode(fields).encode() if fields is not None else None
        req = urllib.request.Request(url, data=data, headers={
            'User-Agent': 'HistoricalStateOutcomes/1.0 (public statistical research)',
            **({'Content-Type': 'application/x-www-form-urlencoded'} if data is not None else {})})
        try:
            with self.opener.open(req, timeout=45) as response:
                body, status, final_url = response.read(50_000_001), response.status, response.geturl()
        except urllib.error.HTTPError as exc:
            body, status, final_url = exc.read(50_000_001), exc.code, exc.url
        except urllib.error.URLError as exc:
            write_once(self.root / 'requests' / (label + '.error.json'),
                       dict(status='network_error', timestamp_utc=utc(), reason=str(exc.reason)))
            raise ConnectorNotReady('WONDER network error; no retries') from exc
        if len(body) > 50_000_000:
            raise ConnectorError('WONDER response exceeds bounded size')
        response_path = self.root / 'raw' / (label + '.response')
        response_path.parent.mkdir(parents=True, exist_ok=True)
        with response_path.open('xb') as stream:
            stream.write(body)
        result = dict(path=str(response_path), url=url, final_url=final_url,
                      size_bytes=len(body), sha256=digest(body), license=LICENSE,
                      http_status=status, timestamp_utc=utc(), scan=scan_source(body))
        write_once(self.root / 'requests' / (label + '.receipt.json'), result)
        blocked = status in (401, 403) or any(re.search(p, body.decode('utf-8','replace'), re.I)
                                              for p in BLOCK_PATTERNS)
        if blocked:
            write_once(self.root / 'BLOCKED.json', dict(status='STOP_BROWSER_HANDOFF_REQUIRED',
                       receipt=result, reason='Access-control or CAPTCHA/bot-check response; no bypass',
                       terms_accepted=False if label == 'accept_terms' else
                       (self.root / 'TERMS_ACCEPTANCE.json').exists()))
            raise ConnectorNotReady('WONDER access control/bot-check detected; stopped')
        if status == 429:
            raise ConnectorNotReady('WONDER rate limited; park for later, no retry')
        if status >= 400:
            raise ConnectorNotReady('WONDER HTTP ' + str(status))
        # Redirects never authorize a different host or export endpoint.
        if urllib.parse.urlsplit(final_url).hostname != 'wonder.cdc.gov':
            raise ConnectorNotReady('WONDER redirected outside official host; no content interpretation')
        return body, result


class _WonderRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'wonder.cdc.gov' or parsed.username or parsed.password:
            raise ConnectorNotReady('Refuse redirect outside official WONDER HTTPS host')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _CapturedForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms=[]
        self.active=None

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag == 'form':
            self.active={'action': attrs.get('action',''), 'method': attrs.get('method','get').lower(), 'names': set()}
            self.forms.append(self.active)
        elif self.active is not None and tag in ('input','select','textarea','button') and attrs.get('name'):
            self.active['names'].add(attrs['name'])

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active=None


def _validate_semantics(q):
    if q.get('geography') not in {'state_of_residence', 'maternal_residence_state'}:
        raise ConnectorError('Only residence state aggregates authorized')
    groups = q.get('group_by')
    if groups not in [['state', 'year'], ['state', 'year', 'mother_age_group']]:
        raise ConnectorError('Only state/year and natality maternal-age grouping authorized')
    first, last = q.get('years', [None, None])
    if type(first) is not int or type(last) is not int or not 1999 <= first <= last <= 2024:
        raise ConnectorError('Only declared final1999–2024 windows; revise latest release prospectively')
    if q.get('suppression') != 'preserve_no_imputation_no_back_calculation':
        raise ConnectorError('WONDER suppression must be respected')


def reviewed_post(manifest_path, client=None):
    """Submit only exact reviewed web form fields, not guessed XML API fields.

    The hub/browser supplies a manifest with raw captured form path/hash, exact
    form_fields, robots allowance and semantic_query, after reviewing data scope.
    This function is inactive while BLOCKED.json is present.
    """
    client = client or WebClient()
    if (client.root / 'BLOCKED.json').exists():
        raise ConnectorNotReady('Persistent access-control stop; do not call WONDER again')
    m = json.loads(Path(manifest_path).read_text())
    if m.get('workflow') != 'reviewed_https_web_form' or m.get('robots_permitted') is not True:
        raise ConnectorError('Reviewed web workflow and robots allowance required')
    form = Path(m['captured_form_path']).read_bytes()
    if digest(form) != m['captured_form_sha256']:
        raise ConnectorError('Captured form changed since review')
    if scan_source(form)['suspicious_patterns']:
        raise ConnectorNotReady('Suspicious captured form content; independently review source')
    if any(re.search(p, form.decode('utf-8','replace'), re.I) for p in BLOCK_PATTERNS):
        write_once(client.root / 'BLOCKED.json', dict(status='STOP_BROWSER_HANDOFF_REQUIRED',
                   timestamp_utc=utc(), reason='Access-control challenge in captured form'))
        raise ConnectorNotReady('Captured form contains access-control challenge')
    parser=_CapturedForm()
    parser.feed(form.decode('utf-8','replace'))
    submitted=m.get('form_fields')
    if not isinstance(submitted,list) or any(not isinstance(pair,list) or len(pair)!=2 for pair in submitted):
        raise ConnectorError('Reviewed manifest must declare exact string-pair form fields')
    url=m['url']
    matching=[f for f in parser.forms if f['method']=='post' and
              urllib.parse.urljoin(m['captured_form_url'],f['action'])==url]
    if len(matching)!=1 or not {pair[0] for pair in submitted} <= matching[0]['names']:
        raise ConnectorError('Posted endpoint/field names must match one captured HTTPS POST form')
    action = m.get('action')
    if action == 'accept_terms':
        if m.get('owner_permission_path') != str(PERMISSION) or m.get('data_use_restrictions_reviewed') is not True:
            raise ConnectorError('Owner permission and exact terms review required')
        body, receipt = client.request(m['url'], 'accept_terms', m['form_fields'])
        confirmation=m.get('acceptance_confirmation_marker')
        if not isinstance(confirmation,str) or not confirmation or confirmation.encode() not in body:
            raise ConnectorNotReady('No reviewed acceptance confirmation in response; terms not recorded accepted')
        write_once(client.root / 'TERMS_ACCEPTANCE.json', dict(accepted_at_utc=utc(),
                   permission_path=str(PERMISSION), permission_sha256=digest(PERMISSION.read_bytes()),
                   captured_form_sha256=digest(form), receipt=receipt,
                   acceptance_fields=m['form_fields'], conditions='aggregate state-year only; no re-identification; preserve suppression; cite source; rate limits; no bypass'))
    elif action == 'query':
        _validate_semantics(m['semantic_query'])
        body, receipt = client.request(m['url'], m['query_id'], m['form_fields'], m['semantic_query'])
    else:
        raise ConnectorError('Only terms acceptance and aggregate query actions')
    return receipt


def _number(text, *, count=False):
    text = str(text).strip()
    if text.lower() in MISSING or re.search(r'\(Unreliable\)$', text, re.I):
        return None
    try:
        value = float(text.replace(',', ''))
    except ValueError as exc:
        raise ConnectorError('Unexpected WONDER cell: ' + repr(text)) from exc
    if not math.isfinite(value) or value < 0 or count and not value.is_integer():
        raise ConnectorError('Invalid WONDER nonnegative numeric cell')
    # Owner suppression condition applies even to accidental raw small counts.
    if count and 0 < value < 10:
        raise ConnectorError('WONDER count1–9 violates suppression contract; refuse publication')
    return int(value) if count else value


AGE_LABELS = {'under15': 'Under 15 years', '15-17': '15 - 17 years',
              '18-19': '18 - 19 years', '15-19': '15-19 years',
              **{a: a + ' years' for a in ('20-24','25-29','30-34','35-39','40-44','45-49')},
              '50+': '50 years and over'}


def parse_export(path, *, measure, mother_age=None):
    """Parse exact published cells; never sum ages, totals or vintages."""
    lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    start = None
    for i, line in enumerate(lines):
        fields = next(csv.reader([line], delimiter='\t' if '\t' in line else ','))
        if any(x in fields for x in ('Year','Year of Death')) and any(x in fields for x in ('State','State of Residence')):
            start = i
            break
    if start is None:
        raise ConnectorError('No WONDER state/year export header')
    reader = csv.DictReader(lines[start:], delimiter='\t' if '\t' in lines[start] else ',')
    state_field = 'State' if 'State' in reader.fieldnames else 'State of Residence'
    year_field = 'Year' if 'Year' in reader.fieldnames else 'Year of Death'
    age_field = next((x for x in ('Age of Mother 10','Age of Mother 9','Age of Mother',"Mother's Age") if x in reader.fieldnames), None)
    if measure not in reader.fieldnames:
        raise ConnectorNotReady('Requested measure absent from WONDER export: ' + measure)
    if age_field and mother_age is None:
        raise ConnectorError('One exact maternal age must be selected')
    selected = {}
    for cell in reader:
        if (cell.get('Notes') or '').strip().lower() == 'total':
            continue
        state = (cell.get(state_field) or '').strip()
        if not state or state.lower() in {'total','united states'}:
            continue
        if state not in NAMES:
            raise ConnectorError('Unexpected WONDER state label: ' + repr(state))
        year_raw = (cell.get(year_field,'') or '').strip()
        if not re.fullmatch(r'\d{4}', str(year_raw)):
            raise ConnectorError('Invalid WONDER annual period')
        age = cell.get(age_field,'') if age_field else ''
        if mother_age is not None and age != mother_age:
            continue
        key = (NAMES[state], int(year_raw))
        if key in selected:
            raise ConnectorError('Duplicate state/year/population export cell; never sum overlapping groups')
        raw = cell.get(measure) or ''
        value = _number(raw, count=measure in {'Deaths','Births','Infant Deaths'})
        count_raw = next((cell[x] for x in ('Deaths','Births','Infant Deaths') if x in cell), '')
        suppressed = any(str(x).strip().lower() in {'suppressed','*','**','1-9'} for x in (raw,count_raw))
        if suppressed:
            value = None
        selected[key] = dict(value=value, published_cell_present=True,
                             suppressed=suppressed, unreliable='unreliable' in raw.lower(),
                             source_value=raw, age_group=age)
    return selected


def verify_receipt(path, expected=None):
    path = Path(path)
    receipt_path = path.with_name(path.name + '.receipt.json')
    if not receipt_path.is_file():
        raise ConnectorNotReady('WONDER export requires query receipt')
    r = json.loads(receipt_path.read_text())
    if r.get('file') != path.name or r.get('sha256') != digest(path.read_bytes()) or r.get('size_bytes') != path.stat().st_size:
        raise ConnectorError('WONDER raw export byte/size/file binding changed')
    for field in ('database','database_version','query_timestamp_utc','query_parameters','population_filters'):
        if not r.get(field):
            raise ConnectorError('Missing WONDER export provenance: ' + field)
    if expected is not None:
        for field in ('sha256','series_id','database_code','population_filters','years_requested'):
            if r.get(field) != expected.get(field):
                raise ConnectorError('Receipt differs from catalog binding: ' + field)
    terms = r.get('terms_acceptance', {})
    if terms.get('accepted') is not True:
        raise ConnectorError('No recorded terms acceptance')
    restriction_reference = terms.get('restrictions_text_file','')
    # Two MCD receipts append a human-readable identical-text note after the file.
    restriction_reference = re.sub(r' \(identical text, hash verified on the MCD(?: 1999-2020)? page\)$', '', restriction_reference)
    restrictions = ROOT / restriction_reference
    if not restrictions.resolve().is_relative_to((ROOT/'raw').resolve()) or not restrictions.is_file() or digest(restrictions.read_bytes()) != terms.get('restrictions_text_sha256'):
        raise ConnectorError('WONDER restrictions receipt changed')
    if terms.get('owner_permission_path') != str(PERMISSION) or digest(PERMISSION.read_bytes()) != terms.get('owner_permission_sha256'):
        raise ConnectorError('WONDER owner permission binding changed')
    printed = r.get('query_date_as_printed_by_wonder')
    if not printed or printed not in path.read_text():
        raise ConnectorError('WONDER printed query timestamp differs')
    first,last = map(int,r['years_requested'].split('-'))
    return r, set(range(first,last+1))


def _build(request: ConnectorRequest, *, catalog_data=None):
    if request.frequency != 'annual':
        raise ConnectorNotReady('WONDER final connector supports annual data only')
    if any(x in request.options for x in ('party','adoption','treatment','estimator')):
        raise ConnectorError('Outcome-only inputs required')
    series = request.options.get('series_id')
    from .wonder_registry import catalog
    spec = (catalog_data if catalog_data is not None else catalog())['series'].get(series)
    if spec is None or spec.get('adapter') != 'wonder':
        raise ConnectorError('Not a registered WONDER series')
    if not set(request.years) <= set(spec['years']):
        raise ConnectorNotReady('Requested years exceed declared final release')
    age = request.filters.get('age_group')
    if spec.get('required_filters') == ['age_group']:
        if set(request.filters) != {'age_group'} or age not in spec['age_groups']:
            raise ConnectorNotReady('Bind one exact published maternal-age bin')
        if not set(request.years) <= set(spec['age_group_years'][age]):
            raise ConnectorNotReady('Years exceed exact maternal-age observed coverage')
    elif dict(request.filters) != dict(spec.get('population_filters', {})):
        raise ConnectorNotReady('Exact registered cause/age/maternal-age population required')
    bound = {str((Path(__file__).resolve().parents[3] / x['raw_path']).resolve()): x for x in spec['exports']}
    values, inv = {}, []
    for path in request.paths():
        export = bound.get(str(path.resolve()))
        if export is None:
            raise ConnectorError('Export is not bound to the exact registered series')
        receipt, allowed_years = verify_receipt(path, export)
        if age and export.get('age_schema') != ('10' if age in ('15-17','18-19') else '9'):
            continue
        if spec['value_field'] not in next(csv.reader([path.read_text().splitlines()[0]],delimiter='\t')):
            continue
        parsed = parse_export(path, measure=spec['value_field'], mother_age=AGE_LABELS[age] if age else None)
        if any(y not in allowed_years for _,y in parsed):
            raise ConnectorError('Export cells exceed receipt year block')
        if set(values) & set(parsed):
            raise ConnectorError('Overlapping WONDER database vintages: choose explicit disjoint years')
        values.update(parsed)
        item=inventory(path,request); item.update(receipt); inv.append(item)
    rows, meta = [], []
    for unit in request.units:
        for year in request.years:
            cell = values.get((unit,year), {})
            value = cell.get('value')
            record = row(unit, year, '' if value is None else value, spec['units'],
                         suppressed=cell.get('suppressed',False),
                         unreliable=cell.get('unreliable',False),
                         source_series_id=series, source_value=cell.get('source_value'), age_group=age)
            rows.append(record)
            meta.append(metadata(record, value is not None,
                published_cell_present=int(bool(cell)), suppressed=cell.get('suppressed',False),
                unreliable=cell.get('unreliable',False),
                missing_reason=None if value is not None else
                'Published suppressed/unreliable/absent cell; never imputed'))
    return PanelData(tuple(rows),tuple(meta),tuple(inv),
                     ('Final database vintages are selected in disjoint year blocks.',
                      'Opioid involvement uses multiple-cause T40 codes conditioned on underlying overdose.',
                      'Infant mortality rate requires linked live-birth denominator, not population-based crude mortality.'),
                     dict(series_id=series, suppression_preserved=True,
                          imputation=False, back_calculation=False,
                          outcome_association_calls=0))


def build(request: ConnectorRequest, *, catalog_data=None):
    from .rates import apply_scale
    return apply_scale(request, _build(request, catalog_data=catalog_data), catalog_data=catalog_data)


def _coverage(cells):
    years = sorted({y for _, y in cells})
    states = sorted({u for u, _ in cells})
    return dict(years=years, states=states, published_cells=len(cells),
                numeric_cells=sum(x['value'] is not None for x in cells.values()),
                suppressed_cells=sum(x['suppressed'] for x in cells.values()),
                unreliable_cells=sum(x['unreliable'] for x in cells.values()),
                not_available_cells=sum(x['source_value'].strip().lower() in {'not available','not applicable'} for x in cells.values()),
                observed_years_by_state={u: sorted(y for unit,y in cells if unit==u) for u in states},
                numeric_years_by_state={u: sorted(y for (unit,y),x in cells.items() if unit==u and x['value'] is not None) for u in states},
                by_year={str(y): dict(published_cells=sum(year==y for _,year in cells), numeric_cells=sum(year==y and x['value'] is not None for (_,year),x in cells.items()), suppressed_cells=sum(year==y and x['suppressed'] for (_,year),x in cells.items())) for y in years})


C21_SERIES = {
    'wonder_homicide_deaths': dict(population='all-mechanism homicide, all ages and sexes',
        population_filters={'injury_intent': 'homicide', 'injury_mechanism': 'all'}),
    'wonder_firearm_homicide_deaths_female': dict(population='female firearm homicide, all ages',
        population_filters={'injury_intent': 'homicide', 'injury_mechanism': 'firearm', 'sex': 'female'}),
}
C21_BLOCKS = (('D76', 1999, 2017), ('D158', 2018, 2024))


def c21_series_specs():
    """Prospective catalog fragments; availability requires a completed offline audit."""
    from copy import deepcopy
    template = json.loads((Path(__file__).resolve().parent/'OUTCOME_CATALOG_V4.json').read_text())['series']['wonder_firearm_homicide_deaths']
    specs = {}
    for sid, definition in C21_SERIES.items():
        spec = deepcopy(template)
        for key in ('exports', 'query_receipts', 'observed_coverage', 'local_observed_coverage',
                    'local_source_paths', 'coverage_basis'):
            spec.pop(key, None)
        spec.update(definition, series_id=sid, registered_connector='outcome_v5_'+sid,
                    adapter_version='v5_wonder_offline', years=[], public_download_years=[],
                    public_available=False, browser_receipts_verified=False,
                    status='awaiting_browser_exports', requested_year_range=[1999, 2024])
        spec['query_spec'].update(semantic_filters=definition['population_filters'],
                                  query_executed=False, terms_accepted=False)
        spec['definition_changes'] = [
            '1999–2017 bridged-race D76 and 2018–2024 single-race D158 use disjoint years; preserve published resident count and corresponding age/sex population.',
            'Suppressed deaths and unreliable crude rates remain unavailable; no imputation, back-calculation or ratio rescue.',
            'Female firearm homicide uses the female resident denominator; all-mechanism homicide uses all residents.']
        specs[sid] = spec
    return specs


def _verify_c21_semantics(receipt, sid):
    """Check the browser's recorded population, independently of a file-name match."""
    if receipt.get('series_id') != sid:
        raise ConnectorError('C21 receipt series differs from filename')
    filters = receipt.get('population_filters', {})
    intent = str(filters.get('injury_intent', '')).lower()
    mechanism = str(filters.get('injury_mechanism', '')).lower()
    sex = str(filters.get('sex', '')).lower()
    age = str(filters.get('age', '')).lower()
    if not (intent == 'homicide' or intent.startswith('homicide (')) or age not in {'all', 'all ages'}:
        raise ConnectorError('C21 requires homicide and all ages')
    if sid == 'wonder_firearm_homicide_deaths_female':
        if not (mechanism == 'firearm' or mechanism.startswith('firearm (')) or sex not in {'female', 'female (f)', 'female (2)', 'female (v7=f)'}:
            raise ConnectorError('C21 female series requires female firearm homicide')
    elif mechanism not in {'all', 'all mechanisms', 'all injury mechanisms', 'all mechanisms (v23=*all*)'} or sex not in {'all', 'both sexes'}:
        raise ConnectorError('C21 all-mechanism homicide requires all mechanisms and sexes')


def collect_c21_exports(*, raw_root=None):
    """Read only four new exports and their receipts; never alter raw artifacts."""
    repo = Path(__file__).resolve().parents[3]
    raw_root = Path(raw_root) if raw_root is not None else ROOT/'raw'
    verified, scans = {}, []
    expected = [(sid, code, first, last, raw_root/f'{sid}__{code}_{first}-{last}.tsv')
                for sid in C21_SERIES for code, first, last in C21_BLOCKS]
    absent = [str(path) for _, _, _, _, path in expected
              if not path.is_file() or not Path(str(path)+'.receipt.json').is_file()]
    if absent:
        raise ConnectorNotReady('C21 requires all four browser exports and receipts: '+', '.join(absent))
    for sid, code, first, last, path in expected:
        receipt, years = verify_receipt(path)
        if receipt.get('database_code') != code or years != set(range(first, last+1)):
            raise ConnectorError('C21 database/year block differs from declared disjoint vintage')
        _verify_c21_semantics(receipt, sid)
        for source in (path, Path(str(path)+'.receipt.json'),
                       ROOT/receipt['terms_acceptance']['restrictions_text_file']):
            scan = scan_source(source.read_bytes())
            scans.append(dict(path=str(source.relative_to(repo)), **scan))
            if scan['suspicious_patterns']:
                raise ConnectorError('Suspicious source instructions require independent review')
        parsed = parse_export(path, measure='Deaths')
        rates = parse_export(path, measure='Crude Rate')
        population = parse_export(path, measure='Population')
        if any(y not in years for _, y in parsed):
            raise ConnectorError('C21 export cells exceed receipt years')
        if not parsed:
            raise ConnectorError('C21 export contains no state-year cells')
        item = dict(receipt, raw_path=str(path.relative_to(repo)),
            receipt_path=str(Path(str(path)+'.receipt.json').relative_to(repo)),
            receipt_sha256=digest(Path(str(path)+'.receipt.json').read_bytes()))
        verified.setdefault(sid, []).append((item, parsed, rates, population))
    return verified, scans


def publish_c21():
    """Publish a new durable data version and catalog fragment, without editing catalogs."""
    repo = Path(__file__).resolve().parents[3]
    verified, scans = collect_c21_exports()
    specs = c21_series_specs()
    panels, downloads = [], []
    for sid, entries in verified.items():
        cells, rates, populations = {}, {}, {}
        for export, block, rate_block, population_block in entries:
            if set(cells) & set(block):
                raise ConnectorError('C21 overlapping source vintages')
            cells.update(block); rates.update(rate_block); populations.update(population_block)
            downloads.append({key: export.get(key) for key in
                ('raw_path', 'url', 'size_bytes', 'sha256', 'license', 'receipt_path', 'receipt_sha256')})
        coverage = _coverage(cells)
        rate_cells = {}
        for key, cell in cells.items():
            rate = rates.get(key, {})
            value = rate.get('value') if cell['value'] is not None else None
            rate_cells[key] = dict(rate, value=value, suppressed=cell['suppressed'],
                                  unreliable=rate.get('unreliable', False),
                                  source_value=rate.get('source_value', ''))
        path = ROOT/'parsed'/f'c21_{sid}.json'
        payload = dict(series_id=sid, units='deaths', coverage=coverage,
            rate_units='deaths per 100,000 residents in declared age/sex population',
            rate_coverage=_coverage(rate_cells),
            source_paths=[x[0]['raw_path'] for x in entries],
            cells=[dict(unit=u, year=y, **cell,
                published_crude_rate=rate_cells[u,y]['value'],
                published_rate_unreliable=rate_cells[u,y]['unreliable'],
                source_population=populations.get((u,y),{}).get('value'))
                for (u,y),cell in sorted(cells.items())],
            suppression_preserved=True, imputation=False, back_calculation=False,
            outcome_association_calls=0)
        write_once(path, payload)
        exports = [x[0] for x in entries]
        specs[sid].update(exports=exports,
            query_receipts=[dict(path=x['receipt_path'],sha256=x['receipt_sha256']) for x in exports],
            browser_receipts_verified=True, public_available=True, status='implemented_receipt_verified',
            years=coverage['years'], public_download_years=coverage['years'],
            observed_coverage=coverage, local_observed_coverage=coverage,
            observed_rate_coverage=payload['rate_coverage'],
            coverage_basis='receipt_verified_published_cells',
            local_source_paths=payload['source_paths'], license=LICENSE,
            parsed_panel=dict(path=str(path.relative_to(repo)), sha256=digest(path.read_bytes())))
        specs[sid]['query_spec'].update(query_executed=True, terms_accepted=True)
        panels.append(dict(series_id=sid, path=str(path.relative_to(repo)),
                           sha256=digest(path.read_bytes()), coverage=coverage,
                           rate_coverage=payload['rate_coverage']))
    fragment = ROOT/'parsed/C21_SERIES_FRAGMENT.json'
    write_once(fragment, dict(series=specs, outcome_association_calls=0))
    audit = ROOT/'checks/PARSE_C21_AUDIT.json'
    write_once(audit, dict(exports_verified=4, downloads=downloads, panels=panels,
        source_checks=scans, new_network_downloads=[], outcome_association_calls=0,
        source_authority='untrusted_data', clean_scan_proves_safety=False,
        ml_detector_run=False))
    return dict(fragment=str(fragment.relative_to(repo)), audit=str(audit.relative_to(repo)),
                exports_verified=4, panels=len(panels), outcome_association_calls=0)


def publish_v4():
    """Offline reproducible receipt audit, coverage and prospective catalog publication."""
    from copy import deepcopy
    repo = Path(__file__).resolve().parents[3]
    destination = repo/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V4.json'
    data = json.loads(destination.with_name('OUTCOME_CATALOG_V3.json').read_text())
    data.update(schema_version=4, catalog_id='first_order_outcomes_v4',
                registration_entrypoint='scripts.l2_pipeline.connectors.wonder_registry.register',
                registered_entrypoint='scripts.l2_pipeline.connectors.wonder_registry.construct',
                acquisition_entrypoint='Offline browser exports only; python3 -m scripts.l2_pipeline.connectors.wonder parse-v4',
                outcome_association_calls=0)
    data['discipline']['outcome_association_calls']=0
    files=sorted((ROOT/'raw').glob('*.tsv'))
    if len(files)!=28:
        raise ConnectorError('Expected exactly 28 browser exports')
    source_scans=[]
    for path in sorted((ROOT/'raw').iterdir()):
        if path.is_file() and path.suffix in {'.tsv','.txt','.html','.json','.csv','.response'}:
            scan=scan_source(path.read_bytes())
            source_scans.append(dict(path=str(path.relative_to(repo)),**scan))
            if scan['suspicious_patterns']:
                raise ConnectorError('Suspicious source instructions require independent review')
    verified=[]
    for path in files:
        receipt,years=verify_receipt(path)
        scan=scan_source(path.read_bytes())
        if scan['suspicious_patterns']:
            raise ConnectorError('Suspicious source instructions require independent review')
        item=dict(receipt,raw_path=str(path.relative_to(repo)),receipt_path=str(Path(str(path)+'.receipt.json').relative_to(repo)),
                  receipt_sha256=digest(Path(str(path)+'.receipt.json').read_bytes()),scan=scan)
        if 'births_by_maternal_age' in path.name:
            item['age_schema']='10' if 'age10__' in path.name else '9'
        verified.append(item)
    births=data['series']['wonder_births_by_maternal_age']
    fert=deepcopy(data['series']['wonder_birth_rate_by_maternal_age'])
    fert.update(series_id='wonder_fertility_rate_by_maternal_age', units='births per 1,000 women in exact maternal-age group',
                value_field='Fertility Rate', population='Maternal-residence age-specific fertility; female age-group population denominator')
    data['series'][fert['series_id']]=fert
    data['series']['wonder_birth_rate_by_maternal_age'].update(status='unavailable_age_grouped_measure', years=[], public_available=False, public_download_years=[],
            reason='WONDER Birth Rate cannot be grouped by maternal age. Fertility Rate is a separately identified measure; no substitution under this ID.')
    panels=[]
    for sid,spec in data['series'].items():
        spec['registered_connector']='outcome_v4_'+sid
        if spec['adapter']!='wonder':
            source_sid=spec.get('alias_of',sid)
            summaries=json.loads((ROOT/'parsed/historical_coverage.json').read_text())['panels']
            local=[]
            for summary in summaries:
                if summary['series_id']!=source_sid:
                    continue
                if spec.get('adapter')=='historical_alias' and summary['filters']!=spec.get('population_filters'):
                    continue
                path=Path(summary['panel_path'])
                if not path.resolve().is_relative_to((ROOT/'parsed').resolve()):
                    raise ConnectorError('Historical archived panel outside authorized directory')
                archive=json.loads(path.read_text())
                for receipt in archive['inventory']:
                    raw=Path(receipt['path'])
                    if not raw.resolve().is_relative_to((ROOT/'raw').resolve()) or raw.stat().st_size!=receipt['size_bytes'] or digest(raw.read_bytes())!=receipt['sha256']:
                        raise ConnectorError('Historical archived panel byte provenance differs')
                metas=archive['metadata']
                local.append(dict(filters=summary['filters'],path=str(path.relative_to(repo)),sha256=digest(path.read_bytes()),
                        years=sorted({m['year'] for m in metas if m.get('published_cell_present')}),
                        states=sorted({m['unit'] for m in metas if m.get('published_cell_present')}),
                        published_cells=sum(bool(m.get('published_cell_present')) for m in metas),
                        numeric_cells=sum(m['available'] for m in metas),suppressed_cells=sum(m.get('suppressed',False) for m in metas),
                        unreliable_cells=sum(m.get('unreliable',False) for m in metas),
                        numeric_years_by_state={u:sorted(m['year'] for m in metas if m['unit']==u and m['available']) for u in sorted({m['unit'] for m in metas})}))
            spec.update(coverage_basis='publisher_declared_support_inherited_v3',publisher_support_years=spec['years'],
                        locally_downloaded_years=sorted({y for x in local for y in x['years']}) if local else None,
                        publisher_declared_download_years=spec.get('public_download_years',[]),
                        local_observed_coverage=local or None,
                        observed_coverage=dict(basis='archived_parsed_metadata_and_byte_verified_inventory' if local else 'unknown_local_observations',
                                               local_panels=local or None))
            continue
        if sid=='wonder_birth_rate_by_maternal_age':
            continue
        is_age=sid in ('wonder_births_by_maternal_age','wonder_fertility_rate_by_maternal_age')
        exports=[v for v in verified if v['series_id']==('wonder_births_by_maternal_age' if is_age else sid)]
        if not exports:
            raise ConnectorError('No exports for '+sid)
        spec.update(exports=exports,query_receipts=[dict(path=v['receipt_path'],sha256=v['receipt_sha256']) for v in exports],
                    browser_receipts_verified=True,access='public_no_account_no_key',adapter_version='v4_wonder_offline',
                    public_available=True,status='implemented_receipt_verified',release_lag='Final published records through 2024; query date 2026-10-09; retain exact vintages',
                    license=LICENSE)
        spec['query_spec'].update(query_executed=True,terms_accepted=True)
        if is_age:
            spec.update(required_filters=['age_group'],population_filters={},age_groups=list(AGE_LABELS),source_age_labels=AGE_LABELS)
            if sid==fert['series_id']:
                spec['age_groups']=[a for a in AGE_LABELS if a not in ('under15','50+')]
                spec['definition_changes']=['Birth Rate by age unavailable; Fertility Rate per 1,000 women in the maternal-age group is distinct.', 'No fertility measure in D10 1999-2002.', '2003 onward offers 15-17 and 18-19 as well as separately exported published 15-19 totals; never sum rates or overlapping age bins.', 'Natality database changes 2002/2003 and 2006/2007 retained as source vintages; maternal residence and published birth/female population measures remain explicit.']
            else:
                spec['definition_changes']=['D10 1999-2002 publishes 15-19 only; 15-17 and 18-19 begin 2003.','Age 9 and age 10 exports overlap outside the teen sub-bins. Use age 9 for shared groups, age 10 only for 15-17/18-19. Never add overlapping groups or infer suppressed 15-19 cells from sub-bins.','Natality database changes 2002/2003 and 2006/2007 retained as source vintages; no race-specific series spliced.']
        elif sid=='wonder_infant_mortality_rate':
            spec['value_field']='Death Rate'
            spec['definition_changes']=['Linked Birth/Infant Death period files D18 1999-2002, D31 2003-2006 and D69 2007-2024 grouped by Year of Death; deaths per 1,000 live births, not under-1 population crude rate.', 'Published linked period-file rate is retained with its source matching/weighting definition; no ratio recomputed from death and birth counts.']
        else:
            spec['definition_changes']=['1999-2017 ICD-10 bridged-race D76/D77, 2018-2024 ICD-10 single-race D158/D157. All-race resident death counts with identical cause/age scopes form a consistent count series; race classification changes at 2017/2018. No race-specific population splice or mortality rate constructed.', 'Cause reporting/coding anomalies and vintage revisions remain in the raw export footer; no adjustment or interpolation.']
        if sid=='wonder_opioid_overdose_deaths':
            spec['definition_changes'].append('Underlying all-intent overdose X40-X44/X60-X64/X85/Y10-Y14 AND any multiple-cause T40.0-T40.4 or T40.6; this is opioid-involved overdose, not opioid-only overdose. Changes in specific-drug reporting may affect comparability.')
        coverages={}
        for age in spec['age_groups'] if is_age else [None]:
            cells={}
            used=[]
            for export in exports:
                if is_age and export['age_schema']!=('10' if age in ('15-17','18-19') else '9'):
                    continue
                path=repo/export['raw_path']
                fields=next(csv.reader([path.read_text().splitlines()[0]],delimiter='\t'))
                if spec['value_field'] not in fields:
                    continue
                parsed=parse_export(path,measure=spec['value_field'],mother_age=AGE_LABELS[age] if age else None)
                first,last=map(int,export['years_requested'].split('-'))
                if any(y<first or y>last for _,y in parsed) or set(cells)&set(parsed):
                    raise ConnectorError('Year block exceeds receipt or overlaps')
                cells.update(parsed);used.append(export['raw_path'])
            coverage=_coverage(cells)
            coverages[age or 'all']=coverage
            panel_path=ROOT/'parsed'/('v4_'+sid+('_age'+age.replace('+','plus') if age else '')+'.json')
            payload=dict(series_id=sid,age_group=age,units=spec['units'],coverage=coverage,source_paths=used,
                         cells=[dict(unit=u,year=y,**x) for (u,y),x in sorted(cells.items())],
                         suppression_preserved=True,imputation=False,back_calculation=False,outcome_association_calls=0)
            panel_path.parent.mkdir(parents=True,exist_ok=True);panel_path.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
            panels.append(dict(series_id=sid,age_group=age,path=str(panel_path.relative_to(repo)),sha256=digest(panel_path.read_bytes()),coverage=coverage))
        spec['observed_coverage']=coverages if is_age else coverages['all']
        spec['coverage_basis']='receipt_verified_published_cells'
        spec['local_observed_coverage']=spec['observed_coverage']
        spec['years']=sorted({y for x in coverages.values() for y in x['years']})
        spec['public_download_years']=spec['years']
        spec['local_source_paths']=[x['raw_path'] for x in exports]
        if is_age:
            spec['age_group_years']={a:x['years'] for a,x in coverages.items()}
        if spec['years']:
            spec['query_spec']['years']=[min(spec['years']),max(spec['years'])]
    # Map first-order leads prospectively; legal/population certification remains S1 work.
    targets=data['targets']
    drug=['wonder_drug_overdose_deaths','wonder_opioid_overdose_deaths']
    for name in ('pdmp','naloxone_access','pill_mill_regulation','cannabis_policy','drug_overdose_policy'):
        targets[name].update(series=list(dict.fromkeys(targets[name]['series']+drug)), selection_authorized=True,
                             measurement='Resident all-drug overdose and opioid-involved overdose; first-order policy target subject to legal-purpose and eligible-population audit')
    targets['good_samaritan']=dict(series=drug,measurement='Overdose deaths and opioid-involved overdose for overdose-response immunity laws',purpose_verification_required=True,legal_certification_granted=False)
    for name in ('alcohol_tax','bac_limits','zero_tolerance_driving','minimum_legal_drinking_age','alcohol_control'):
        targets.setdefault(name,{})
        targets[name].update(series=['wonder_alcohol_induced_deaths','wonder_motor_vehicle_traffic_deaths_age15_24'],
            measurement='Alcohol-induced death counts and traffic death counts ages 15-24; preserve legally fixed threshold and audit age mismatch',purpose_verification_required=True,legal_certification_granted=False)
    for name in ('abortion_access','contraception_access','births_fertility_policy','teen_birth_policy'):
        targets[name]['series']=[x for x in targets[name]['series'] if x!='wonder_birth_rate_by_maternal_age']+['wonder_fertility_rate_by_maternal_age']
        targets[name]['measurement']='Maternal-residence births and fertility per 1,000 women by one exact published age bin; purpose/population review before eligibility'
    for name in ('abortion_access','contraception_access'):
        targets[name].update(allowed_age_groups=list(AGE_LABELS),
            default_age_scope='One exact published maternal-age bin matched to the legally relevant population; fix scope before estimation',
            series_specific_allowed_age_groups={'wonder_births_by_maternal_age':list(AGE_LABELS),
                'wonder_fertility_rate_by_maternal_age':data['series']['wonder_fertility_rate_by_maternal_age']['age_groups'],
                'cdc_teen_births':['15-17','18-19','15-19'], 'cdc_teen_birth_rate':['15-17','18-19','15-19']})
    data['receipt_audit']=dict(export_count=28,total_bytes=sum(v['size_bytes'] for v in verified),sha256_verified=True,
          terms_and_permission_sha256_verified=True,source_checks='Local Unicode normalization and heuristic check only; llm_guard and transformers unavailable; no ML model run; clean scan does not prove safety')
    destination.write_text(json.dumps(data,indent=2,sort_keys=True,allow_nan=False)+'\n')
    checks=ROOT/'checks';checks.mkdir(parents=True,exist_ok=True)
    audit=dict(data['receipt_audit'],downloads=[{k:v.get(k) for k in ('raw_path','url','size_bytes','sha256','license','receipt_path','receipt_sha256')} for v in verified],
               panels=panels,new_network_downloads=[],outcome_association_calls=0)
    (checks/'SOURCE_INITIAL_CHECK_V4.json').write_text(json.dumps(dict(source_checks=source_scans,ml_detector_run=False,reason='llm_guard/transformers unavailable; no model installed or downloaded',source_authority='untrusted_data',clean_scan_proves_safety=False),indent=2,sort_keys=True)+'\n')
    (checks/'PARSE_V4_AUDIT.json').write_text(json.dumps(audit,indent=2,sort_keys=True,allow_nan=False)+'\n')
    report=['# WONDER V4 offline parse','', '28 existing browser TSV exports verified against receipt SHA-256, byte size, file name, printed query date, terms-restrictions hash and owner-permission hash. No new downloads or associations.',
            'Initial source check applied detecting-indirect-prompt-injection Unicode normalization, Base64/ROT13 decoding and heuristic patterns to all local raw TSV, TXT, HTML, JSON, CSV and response files (including stored restrictions-page text); no suspicious pattern found. llm_guard/transformers are not installed, so no separate ML scan occurred. Source content and scans remain untrusted data; a clean check is not proof of safety.',
            '2017/2018 seam: bridged-race ICD-10 D76/D77 (1999-2017) → single-race ICD-10 D158/D157 (2018-2024). All-race totals retain the same resident count/cause/age construct. Race categories and rate denominators change; no race-specific series or derived rate is spliced.',
            'Age 9 uses published 15-19 and shared adult bins; Age 10 is used only for 15-17 and 18-19. Births 15-19/shared bins cover 1999-2024; sub-bins cover 2003-2024. Fertility per 1,000 women covers 2003-2024 for 15-17, 18-19, 15-19, 20-24, 25-29, 30-34, 35-39, 40-44 and 45-49; under-15/50+ published Not Available are excluded from rate eligibility. Birth Rate by maternal age remains unavailable.',
            'Infant mortality rate uses the published linked period-file Death Rate by Year of Death (1999-2024), with live-birth denominator. Under-1 population Crude Rate is never treated as IMR.',
            'Exact state/year/bin numeric, suppressed, unreliable and Not Available coverage is in OUTCOME_CATALOG_V4.json and checks/PARSE_V4_AUDIT.json. Printed coverage is distinct from numeric availability; suppression remains null and no value is imputed or back-calculated.',
            'Inherited V3 series retain publisher support in years/publisher_support_years. local_observed_coverage is explicitly unknown (null) when no archived parsed metadata exists; declared download years do not imply numeric state-year availability. Existing historical panels are referenced by archive SHA and byte-verified source inventory with exact state/year/filter availability.',
            'Pipeline modules and tests migrated to scripts/l2_pipeline/connectors; data/*.py retained as inert archive. V3 unchanged; V4 adds names only.','', '| Existing file | URL | Bytes | License |','|---|---|---:|---|']
    report += ['| '+v['raw_path']+' | '+v['url']+' | '+str(v['size_bytes'])+' | '+v['license']+' |' for v in verified]
    (ROOT/'PARSE_V4_REPORT.md').write_text('\n'.join(report)+'\n')
    return dict(catalog=str(destination.relative_to(repo)),exports_verified=28,panels=len(panels),outcome_association_calls=0)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','post','parse-v4','parse-c21'])
    parser.add_argument('--manifest',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.command=='parse-c21':
            print(json.dumps(publish_c21(), indent=2))
        elif args.command=='parse-v4':
            print(json.dumps(publish_v4(), indent=2))
        elif args.command=='status':
            print(json.dumps(json.loads((ROOT/'BLOCKED.json').read_text()) if
                  (ROOT/'BLOCKED.json').exists() else {'status':'not_blocked'},indent=2))
        else:
            if args.manifest is None:raise ConnectorError('--manifest required')
            print(json.dumps(reviewed_post(args.manifest),indent=2))
    except (ConnectorError,OSError,ValueError) as exc:
        parser.exit(2,str(exc)+'\n')

if __name__=='__main__':main()
