"""V5 site projection. Read one pinned snapshot; never ship private paper records.

The snapshot is pinned in scripts/viz_site/data/snapshot_pin.json (path and sha256), so a later replication wave cannot
change a release build; a receipt pin (``pin_snapshot``, used by ``--check``) wins over the file.
Local previews may use a historical, internally bound snapshot after mutable inputs
move on. ``assert_release_ready`` rejects that drift; it is never a refresh hook.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
STAGES = ('closed_by_triage', 'waiting_for_access', 'not_started', 'in_progress', 'finished_uncounted',
          'attempted_matched', 'attempted_directional', 'attempted_not_consistent',
          'attempted_not_comparable', 'confirmed')
# Groups whose paper has a counted primary first estimate.
ATTEMPTED = ('attempted_matched', 'attempted_directional', 'attempted_not_consistent',
             'attempted_not_comparable', 'confirmed')
GROUP = {**{s: 'closed_by_triage' for s in ('closed_restricted', 'closed_no_printed_target', 'closed_out_of_scope', 'closed_duplicate')},
         **{s: 'waiting_for_access' for s in ('waiting_login', 'waiting_account', 'waiting_data_unavailable', 'waiting_journal_table')},
         'matched': 'attempted_matched', 'directional': 'attempted_directional',
         'not_consistent': 'attempted_not_consistent', 'not_comparable': 'attempted_not_comparable',
         'not_started': 'not_started', 'in_progress': 'in_progress', 'finished_uncounted': 'finished_uncounted',
         'confirmed': 'confirmed'}
LABELS = {
'en': ('Closed by AI triage', 'Waiting for access', 'Not started', 'In progress', 'Finished, not counted',
       'Attempted · matched, awaiting confirmation', 'Attempted · directional',
       'Attempted · inconsistent or capped', 'Attempted · not comparable', 'Re-coded by a second AI agent'),
'zh': ('AI 初筛关闭', '等待访问', '未开始', '进行中', '已做完，未计入', '已估计 · 吻合，等待复核',
       '已估计 · 同方向', '已估计 · 不一致或受门槛限制', '已估计 · 不可比', '第二位 AI 智能体重写确认')}
REASONS = {
'en': {'closed_restricted':'Restricted data', 'closed_no_printed_target':'No printed target', 'closed_out_of_scope':'Out of scope', 'closed_duplicate':'Duplicate', 'waiting_login':'Login required', 'waiting_account':'Account required', 'waiting_data_unavailable':'Data unavailable', 'waiting_journal_table':'Journal table needed'},
'zh': {'closed_restricted':'受限数据', 'closed_no_printed_target':'无已发表目标值', 'closed_out_of_scope':'范围之外', 'closed_duplicate':'重复记录', 'waiting_login':'需要登录', 'waiting_account':'需要账户', 'waiting_data_unavailable':'数据暂不可得', 'waiting_journal_table':'等待期刊原表'}}
_META = {}
_CATALOGUE_IDS = []
_CATALOGUE_RECORDS = {}
_PIN = None
# V5.1: the release pin, read by load_snapshot when no receipt pin was given (a --check of an earlier preview).
PIN_FILE = ROOT / 'scripts/viz_site/data/snapshot_pin.json'
SNAPSHOT_PATH = re.compile(r'data/replication/snapshot_v5/snapshot_\d+T\d+Z\.json')
# Owner, 2026-10-14 (R27): the papers whose numbers match are named by citation; the file holds the reviewed surnames and years.
CITATIONS_FILE = ROOT / 'scripts/viz_site/data/matched_citations.json'
# V5.1: the public description of each confirmed paper's card (target, outcome, sample, estimator), reviewed per paper, EN and ZH.
DESCRIPTIONS_FILE = ROOT / 'scripts/viz_site/data/confirmed_descriptions.json'
LINKS_FILE = ROOT / 'scripts/viz_site/data/paper_links.json'

def require(condition, message):
    if not condition:
        raise ValueError('V5 snapshot: ' + message)

def digest(data):
    return sha256(data).hexdigest()

def dt(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None

def tight(ours, reference):
    point, published = number(ours.get('point')), number(reference.get('point'))
    if point is None or published is None:
        return False
    scale = max(abs(published), abs(number(reference.get('se')) or Decimal(0)))
    return abs(point-published) <= Decimal('.10')*scale

def attempted(s):
    return sum(s['grouped_stage_counts'][k] for k in ATTEMPTED)

def _bound_json(s, relative):
    require(relative in s['input_hashes'], 'evidence missing from captured manifest: ' + relative)
    data = (ROOT / relative).read_bytes()
    require(digest(data) == s['input_hashes'][relative]['sha256'], 'selected evidence changed: ' + relative)
    return json.loads(data)

def _run_path(run, field):
    return 'data/replication/r1/' + run['folder'] + '/' + run[field].split('#')[0]

def validate_snapshot(s):
    require(s['schema_version'] == 5 and s['policy_id'] == 'independent_v5', 'wrong schema/policy')
    require(set(s['grouped_stage_counts']) == set(STAGES), 'grouped stages must match the ledger')
    papers = s['papers']; ids = [p['paper_id'] for p in papers]
    require(len(ids) == len(set(ids)) == s['total'] == 503, 'catalogue denominator or uniqueness')
    detailed, grouped = Counter(p['stage'] for p in papers), Counter(p['stage_group'] for p in papers)
    for p in papers:
        require(GROUP.get(p['stage']) == p['stage_group'], 'detailed/grouped stage conflict')
    for k,v in s['stage_counts'].items():
        require(detailed[k] == v, 'detailed count ' + k)
    require(all(grouped[k] == s['grouped_stage_counts'][k] for k in STAGES), 'grouped count')
    require(sum(s['grouped_stage_counts'].values()) == s['closure']['sum_stages'] == s['closure']['unique_paper_ids'] == s['total'], 'ledger not closed')
    require(s['matched_independent'] == grouped['attempted_matched'] + grouped['confirmed'], 'inclusive match count')
    require(s['confirmed_independent'] == grouped['confirmed'], 'confirmed count')
    for p in papers:
        r = p.get('primary')
        require(bool(r) == (p['stage_group'] in ATTEMPTED), 'primary/stage conflict')
        if not r:
            continue
        require(r['run_id'] == p['primary_run_id'], 'primary id mismatch')
        require(r['code_origin'] == 'independent' and r['frozen'] and r['eligible_for_primary'] and r['target_fixed_before_estimation'], 'primary qualification')
        require(r['author_code_opened_before_freeze'] is not True, 'author-code exposure')
        require(r['first_estimate_utc'] and r['freeze_utc'] and dt(r['freeze_utc']) <= dt(r['first_estimate_utc']), 'first-estimate chronology')
        candidates = [x for x in p['runs'] if x.get('eligible_for_primary') and not x.get('is_audit')]
        require(candidates and min(candidates, key=lambda x:x['order_key'])['run_id'] == r['run_id'], 'primary not earliest eligible run')
        if p['stage_group'] in ('attempted_matched', 'confirmed'):
            require(r['grade'] == 'TIGHT' and r['grade_numeric'] == 'TIGHT' and not r['gate_reasons'] and r['target_verified'] and tight(r['ours'], r['published']), 'match/gate conflict')
        if p['stage_group'] != 'confirmed':
            continue
        audits = [a for a in p['audits'] if a.get('eligible') and a.get('confirmed') and a.get('outcome') == 'agree']
        require(bool(audits), 'confirmed without qualifying second coder')
        for a in audits:
            second = next((x for x in p['runs'] if x['run_id'] == a['run_id']), None)
            require(second and second['code_origin'] == 'independent' and second['frozen'] and second['eligible_for_primary'], 'audit run missing or unqualified')
            require(second['coder_id'] and second['coder_id'] != r['coder_id'] and second['target'] == r['target'] and second['spec'] == r['spec'], 'audit coder/target/spec mismatch')
            require(tight(second['ours'], r['published']) and tight(second['ours'], r['ours']), 'audit comparator disagreement')
            for run in (r, second):
                _bound_json(s, _run_path(run, 'freeze_path'))
                _bound_json(s, _run_path(run, 'first_estimate_path'))
                _bound_json(s, _run_path(run, 'result_path'))
            freeze = _bound_json(s, _run_path(second, 'freeze_path'))
            require(all(freeze.get(k) is False for k in ('first_coder_code_opened_before_freeze', 'first_coder_results_opened_before_freeze', 'author_code_opened_before_freeze')), 'second-coder exposure')
            require(freeze.get('frozen_before_first_new_estimation') is True and freeze.get('audit_contract_sha256') == a['audit_contract_sha256'], 'audit contract binding')
            require(freeze['target'] == r['target'] and freeze['spec'] == r['spec'], 'audit frozen target/spec binding')
    require(sum(bool(p.get('primary')) for p in papers) == attempted(s), 'attempted count')

def pin_snapshot(relative_immutable_path):
    """Select the already-reviewed immutable capture before rendering/checking."""
    global _PIN
    require(load_snapshot.cache_info().currsize == 0, 'pin must precede first snapshot read')
    require(SNAPSHOT_PATH.fullmatch(relative_immutable_path), 'invalid immutable pin')
    _PIN = relative_immutable_path

def read_pin_file():
    """The release pin of scripts/viz_site/data/snapshot_pin.json, or None when the file is absent.

    It holds {snapshot_path, snapshot_sha256, pinned_utc, note}; a file that exists but is malformed stops the build."""
    if not PIN_FILE.is_file():
        return None
    pin = json.loads(PIN_FILE.read_text(encoding='utf-8'))
    require(isinstance(pin, dict) and set(pin) == {'snapshot_path', 'snapshot_sha256', 'pinned_utc', 'note'}, 'pin file fields')
    require(isinstance(pin['snapshot_path'], str) and SNAPSHOT_PATH.fullmatch(pin['snapshot_path']), 'pin file must name an immutable capture')
    require(isinstance(pin['snapshot_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', pin['snapshot_sha256']), 'pin file sha256')
    require(isinstance(pin['pinned_utc'], str) and isinstance(pin['note'], str), 'pin file fields')
    return pin

def resolve_pin():
    """(immutable path, expected sha256 or None, source). A receipt pin (a --check of an earlier preview) wins over the pin file;
    without either, the dashboard's immutable copy stands for latest.json (the old behaviour)."""
    pin = read_pin_file()
    if _PIN:
        return _PIN, (pin['snapshot_sha256'] if pin and pin['snapshot_path'] == _PIN else None), 'receipt'
    if pin:
        return pin['snapshot_path'], pin['snapshot_sha256'], 'pin file'
    return None, None, 'dashboard'

@lru_cache(maxsize=1)
def load_snapshot():
    """Capture the pinned snapshot exactly once; returned object remains server-side only."""
    dashboard = json.loads((ROOT / 'data/replication/dashboard_pipeline.json').read_bytes())
    immutable, expected, source = resolve_pin()
    if immutable:
        raw = (ROOT / immutable).read_bytes()
        require(expected is None or digest(raw) == expected, 'pinned snapshot differs from its recorded sha256: ' + immutable)
    else:
        raw = (ROOT / 'data/replication/snapshot_v5/latest.json').read_bytes()
        immutable = dashboard['snapshot_path']
    s = json.loads(raw)
    require(SNAPSHOT_PATH.fullmatch(immutable), 'immutable path')
    require((ROOT / immutable).read_bytes() == raw, 'immutable copy differs')
    # A pin that is not the dashboard's snapshot (a --check of an earlier preview, or a release after later waves) must not be
    # replaced by, or compared with, the later live dashboard.
    dashboard_matches = dashboard.get('snapshot_path') == immutable
    if dashboard_matches:
        for key in ('as_of', 'policy_id', 'total', 'stage_counts', 'grouped_stage_counts', 'matched_independent', 'confirmed_independent', 'closure'):
            require(dashboard[key] == s[key], 'dashboard differs: ' + key)
    catalogue = (ROOT / 'data/replication/paper_catalog_v1.jsonl').read_bytes()
    require(digest(catalogue) == s['input_hashes']['data/replication/paper_catalog_v1.jsonl']['sha256'], 'catalogue hash')
    records = [json.loads(row) for row in catalogue.splitlines() if row]
    catalogue_ids = [record['paper_id'] for record in records]
    require(len(catalogue_ids) == len(set(catalogue_ids)) and set(catalogue_ids) == {p['paper_id'] for p in s['papers']}, 'catalogue membership')
    _CATALOGUE_IDS[:] = catalogue_ids
    _CATALOGUE_RECORDS.clear()
    _CATALOGUE_RECORDS.update({record['paper_id']: record for record in records})
    freeze = s['freeze']
    require(freeze['policy_id'] == s['policy_id'], 'freeze policy')
    require(digest((ROOT / freeze['spec_path']).read_bytes()) == freeze['spec_sha256'], 'frozen reporting spec hash')
    for name in ('TOLERANCE_FREEZE_V2.txt', 'TOLERANCE_FREEZE_V3.txt', 'TOLERANCE_FREEZE_V3_2.txt'):
        p = 'docs/plans/replication/' + name
        require(digest((ROOT / p).read_bytes()) == s['input_hashes'][p]['sha256'], 'rule freeze changed')
    validate_snapshot(s)
    validate_citations(s)
    validate_links(s)
    validate_descriptions(s)
    drift = []
    for relative, captured in s['input_hashes'].items():
        path = ROOT / relative
        actual = digest(path.read_bytes()) if path.is_file() else None
        if actual != captured['sha256']:
            drift.append({'path': relative, 'captured_sha256': captured['sha256'], 'current_sha256': actual})
    _META.update(snapshot_path=immutable, snapshot_sha256=digest(raw), pin_source=source, as_of=s['as_of'], input_count=len(s['input_hashes']), input_drift=drift,
                 release_ready=not drift, dashboard_matches_snapshot=dashboard_matches, total=s['total'], attempted=attempted(s), matched=s['matched_independent'], confirmed=s['confirmed_independent'])
    return s

def build_metadata():
    load_snapshot()
    return dict(_META)

def assert_release_ready():
    load_snapshot()
    require(not _META['input_drift'], 'captured inputs have changed; local historical preview only')

PUBLIC_CATEGORIES = ('number_matches', 'same_direction', 'not_comparable', 'opposite_sign')
# The gap strip gives each plotted estimate its own lane: the first lane sits LANE_TOP px from the top, lanes LANE_STEP px apart,
# and the strip is LANE_STEP x (plotted estimates) + LANE_PAD px high, so it grows and shrinks with the number plotted.
LANE_TOP, LANE_STEP, LANE_PAD = 10, 10, 16


def public_category(p):
    """Classify the independent first estimate by its numeric comparison only.

    Only re-estimated papers are shown; a paper without a first estimate has no public category."""
    run = p.get('primary')
    if not run:
        return None
    # F6 (Finkelstein MF1): the papers beyond 10% are two groups. Not comparable: no published standard error to compare intervals.
    # Opposite sign: a compared estimate that disagrees (third_group_definitions checks that its papers do have the opposite sign).
    grade = run.get('grade_numeric')
    return {'TIGHT': 'number_matches', 'DIRECTIONAL': 'same_direction', 'NOT_COMPARABLE': 'not_comparable'}.get(grade, 'opposite_sign')


def public_counts(s):
    counts = Counter(c for c in map(public_category, s['papers']) if c)
    return {key: counts[key] for key in PUBLIC_CATEGORIES}


def has_mismatch(s):
    """True when a paper's first estimate is compared and disagrees (opposite sign or no overlap): the Opposite sign group is not empty.
    (F6: the label no longer depends on it; the groups are Not comparable and Opposite sign.)"""
    return any(p.get('primary') and p['primary'].get('grade_numeric') not in
               ('TIGHT', 'DIRECTIONAL', 'NOT_COMPARABLE') for p in s['papers'])


def category_labels(s, lang):
    return dict(zip(PUBLIC_CATEGORIES, ('Number matches', 'Same direction', 'Not comparable', 'Opposite sign')
                    if lang == 'en' else ('数字一致', '方向一致', '不可比', '符号相反')))


def progress_line(lang='en', include_date=True):
    s = load_snapshot(); count = public_counts(s)['number_matches']
    if lang == 'zh':
        line = f"AI 编写的代码已重新估计 {attempted(s)} 篇已发表美国州政策论文；其中 {count} 篇的选定点估计与发表值之差不超过发表值绝对值的 10%（若发表标准误更大，则以标准误为基数）。"
    else:
        line = f"AI-written code has re-estimated {attempted(s)} published US state-policy papers; {count} selected point estimates are within 10% of the larger of the published estimate’s absolute value and its standard error."
    date = dt(s['as_of']).astimezone(timezone.utc).date().isoformat()
    return line + ((f' 截至 {date}。' if lang == 'zh' else f' As of {date}.') if include_date else '')


def nowrap_words(markup):
    """Keep hyphenated words whole with the existing .nx-seg class: a break after "re-" reads as two words on a phone."""
    return markup.replace('re-estimated', '<span class="nx-seg">re-estimated</span>')


def progress_html(lang='en', include_date=False):
    return nowrap_words(escape(progress_line(lang, include_date=include_date)))


def count_definition(lang='en'):
    # The papers whose sample, timing and specification also match are among the number matches (validate_snapshot: a matched or
    # confirmed paper is graded TIGHT), so the sentence says "of these".
    s = load_snapshot()
    checked, matches = s['matched_independent'], public_counts(s)['number_matches']
    require(checked <= matches, 'papers whose sample, timing and specification also match are not among the number matches')
    if lang == 'zh':
        return f'{matches} 篇中有 {checked} 篇的样本、政策时点与模型设定也与论文一致；研究设计未经检验。'
    return f'For {checked} of the {matches}, the sample, policy timing and model specification also match the paper; the research design is not tested.'


def _signed_pair(p):
    """(ours, published) point estimates of a paper's first estimate as Decimals, or None when either is missing."""
    run = p['primary']
    ours, published = number(run['ours'].get('point')), number(run['published'].get('point'))
    return None if ours is None or published is None else (ours, published)


def third_group_split(s):
    """How the two groups beyond 10% without a match (Not comparable and Opposite sign) are made up: (no published standard error, opposite sign, same sign with intervals that do not
    overlap). The first are not plotted; every paper of the group falls in exactly one of the three."""
    group = [p for p in s['papers'] if public_category(p) in ('not_comparable', 'opposite_sign')]
    no_se = [p for p in group if not number(p['primary']['published'].get('se'))]
    rest = [p for p in group if p not in no_se]
    opposite = [p for p in rest if _signed_pair(p) and _signed_pair(p)[0] * _signed_pair(p)[1] < 0]
    require(all(gap(p['primary']) is None for p in no_se), 'a paper without a published standard error is plotted')
    return len(no_se), len(opposite), len(rest) - len(opposite)


def reestimate_summary(s=None):
    """The counts the Explore finding on re-estimates prints, from the pinned snapshot with the page's own rules: the papers re-estimated,
    the papers within 10% (Number matches), the others, and how many of the others are closer to zero (the same sign as the published
    estimate and a smaller absolute value). Nothing is typed in: the Explore line is formatted from this."""
    s = s or load_snapshot()
    compared = [p for p in s['papers'] if p.get('primary')]
    others = [p for p in compared if public_category(p) != 'number_matches']
    closer = [p for p in others if _signed_pair(p) and _signed_pair(p)[0] * _signed_pair(p)[1] > 0 and abs(_signed_pair(p)[0]) < abs(_signed_pair(p)[1])]
    require(len(compared) == attempted(s), 'compared papers differ from the attempted count')
    return {'papers': len(compared), 'within': len(compared) - len(others), 'other': len(others), 'closer': len(closer)}


def gap(run, reference=None):
    ours, pub = run.get('ours', {}), reference or run.get('published', {})
    o, p = number(ours.get('point')), number(pub.get('point'))
    reasons = run.get('numeric_checks', {}).get('reason_codes', [])
    if run.get('grade') == 'NOT_COMPARABLE' or run.get('grade_numeric') == 'NOT_COMPARABLE' or run.get('numeric_checks', {}).get('grade') == 'NOT_COMPARABLE' or o is None or p is None or any('unit' in x or 'scale_mismatch' in x for x in reasons):
        return None
    scale = max(abs(p), abs(number(pub.get('se')) or Decimal(0)))
    return (o-p)/scale if scale else None

def fmt(value):
    n = number(value)
    return '—' if n is None else f'{n:.6g}'.replace('-', '−')

def fmt_gap(value):
    if value is None:
        return '—'
    arrow = ('← ' if value < 0 else '→ ') if abs(value) > Decimal('.3') else ''
    percent = value * 100
    return arrow + ('0.000' if abs(percent) < Decimal('.0005') else f'{percent:.3f}'.replace('-', '−')) + '%'

def _fold(text):
    """Lower case without accents or punctuation, words one space apart: 'Cáceres-Delpiano J' becomes 'caceres delpiano j'."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()

@lru_cache(maxsize=1)
def reviewed_citations():
    """Author surnames and year of the papers named on Estimates: paper id -> entry (scripts/viz_site/data/matched_citations.json)."""
    return json.loads(CITATIONS_FILE.read_text(encoding='utf-8'))['papers']

def catalogue_authors(record):
    """The author strings of a catalogue record; an element that is only an initial ('J.') belongs to the name before it."""
    names = []
    for name in record['authors']:
        if names and re.fullmatch(r'[A-Za-z]\.?', name.strip()):
            names[-1] += ' ' + name.strip()
        else:
            names.append(name)
    return names

def citation_text(entry, lang):
    """Author-year as the case citations of this site write it: 'Jacobsen & Kotchen (2013)', 'Cengiz et al. (2019)'.
    Chinese keeps the Latin names and the site's connectors: 'Jacobsen 与 Kotchen（2013）', 'Cengiz 等（2019）'."""
    names, year = entry['authors'], entry['year']
    if lang == 'zh':
        who = names[0] if len(names) == 1 else (f'{names[0]} 与 {names[1]}' if len(names) == 2 else f'{names[0]} 等')
        return f'{who}（{year}）'
    who = names[0] if len(names) == 1 else (f'{names[0]} & {names[1]}' if len(names) == 2 else f'{names[0]} et al.')
    return f'{who} ({year})'

def validate_citations(s):
    """Re-check every reviewed citation against the catalogue record and the snapshot on each load.

    The surnames were typed after reading the replication folder of each paper (the sources are listed in the file). This stops a
    typo, a changed catalogue or a paper that left the Number-matches group from reaching the page; a match without an entry stays anonymous."""
    papers = {p['paper_id']: p for p in s['papers']}
    entries = reviewed_citations()
    for paper_id, entry in entries.items():
        p, record = papers.get(paper_id), _CATALOGUE_RECORDS.get(paper_id)
        require(p is not None and record is not None, 'citation of a paper that is not in the snapshot and the catalogue: ' + paper_id)
        require(public_category(p) == 'number_matches', 'only papers whose numbers match are named: ' + paper_id)
        names, year = entry['authors'], entry['year']
        require(year == p['year'] == record['year'], 'citation year differs from the snapshot or the catalogue: ' + paper_id)
        listed = catalogue_authors(record)
        require(len(names) == len(listed) and all(f' {_fold(n)} ' in f' {_fold(a)} ' for n, a in zip(names, listed)),
                'citation authors differ from the catalogue: ' + paper_id)
        require(_fold(names[0]) == _fold(record['first_author']) and paper_id.startswith(re.sub(r'[^a-z]', '', _fold(names[0])) + str(year) + '-'),
                'citation first author differs from the catalogue or the paper id: ' + paper_id)
        require(record.get('citation_verified') is True or bool(entry.get('note')),
                'a paper whose catalogue citation is not verified needs a note on how the citation was verified: ' + paper_id)
    require(len({citation_text(e, 'en') for e in entries.values()}) == len(entries), 'two papers share one citation')

@lru_cache(maxsize=1)
def reviewed_descriptions():
    """Public descriptions of the confirmed papers' cards: paper id -> entry (scripts/viz_site/data/confirmed_descriptions.json)."""
    return json.loads(DESCRIPTIONS_FILE.read_text(encoding='utf-8'))['papers']

DESCRIPTION_FIELDS = {'target', 'outcome', 'sample', 'estimator', 'published_interval_note', 'note'}

def validate_descriptions(s):
    """Re-check the reviewed card descriptions against the snapshot and the catalogue on each load.

    Every confirmed paper needs an entry; an entry needs the paper's own year, venue and DOI (snapshot and catalogue agree), the
    table and row of the frozen target, the journal cell the snapshot verified (where the entry names one), and a calculated
    published interval exactly when the snapshot's published interval is calculated from a reported SE."""
    papers = {p['paper_id']: p for p in s['papers']}
    entries = reviewed_descriptions()
    for p in s['papers']:
        require(p['stage_group'] != 'confirmed' or p['paper_id'] in entries, 'named target needs a reviewed public description: ' + p['paper_id'])
    for paper_id, entry in entries.items():
        p, record = papers.get(paper_id), _CATALOGUE_RECORDS.get(paper_id)
        require(p is not None and record is not None and p['stage_group'] == 'confirmed',
                'description of a paper that is not confirmed in the snapshot and the catalogue: ' + paper_id)
        for field in ('year', 'venue', 'doi'):
            require(entry[field] == p[field] == record[field], 'description ' + field + ' differs from the snapshot or the catalogue: ' + paper_id)
        run = p['primary']
        require(entry['frozen_target'] and all(run['target'].get(k) == v for k, v in entry['frozen_target'].items()),
                'description does not belong to the frozen target: ' + paper_id)
        journal = entry.get('journal_target')
        if journal:
            verified = run.get('journal_verification') or {}
            require(verified.get('verified') is True and all((verified.get('locator') or {}).get(k) == v for k, v in journal.items()),
                    'description names a journal cell the snapshot has not verified: ' + paper_id)
        calculated = run['published'].get('ci_method') == 'normal_approximation_from_reported_se'
        for lang in ('en', 'zh'):
            text = entry[lang]
            require(set(text) == DESCRIPTION_FIELDS and all(isinstance(text[k], str) and text[k] for k in DESCRIPTION_FIELDS - {'published_interval_note'}),
                    'description fields are incomplete: ' + paper_id + ' ' + lang)
            require(bool(text['published_interval_note']) == calculated,
                    'the published interval is calculated from a reported SE exactly when its note says so: ' + paper_id + ' ' + lang)

LINK_HOSTS = ('papers.ssrn.com', 'doi.org', 'www.nber.org', 'ideas.repec.org')

@lru_cache(maxsize=1)
def paper_links():
    """Page of each named paper: paper id -> https URL (scripts/viz_site/data/paper_links.json). SSRN page when one was verified, else the
    DOI or the working-paper page of the catalogue. A paper without an entry stays unlinked; nothing is guessed."""
    return {pid: e['url'] for pid, e in json.loads(LINKS_FILE.read_text(encoding='utf-8'))['papers'].items()}

def validate_links(s):
    known = {p['paper_id'] for p in s['papers']}
    for paper_id, url in paper_links().items():
        require(paper_id in known, 'a link for a paper that is not in the snapshot: ' + paper_id)
        parts = urlsplit(url)
        require(parts.scheme == 'https' and parts.hostname in LINK_HOSTS and not parts.username and not parts.query.lower().count('key'),
                'paper link is not an https SSRN, DOI, NBER or RePEc address: ' + paper_id)

def version_display(entry):
    """The Version row of a card: the venue without a leading "The", then the year in one pair of parentheses, then a trailing note of the venue
    string (a working-paper number) after a semicolon: "Review of Economics and Statistics 95(1):34-49 (2013); NBER Working Paper 16194".
    A pure formatting step: every fact of the venue string is kept."""
    venue = re.sub(r'^The\s+', '', entry['venue'].strip())
    note = re.fullmatch(r'(.*?)\s*\(([^()]*)\)', venue)
    if note:
        return f"{note[1]} ({entry['year']}); {note[2]}"
    return f"{venue} ({entry['year']})"

def citation_entry(p):
    """The reviewed citation of a paper in the Number-matches group, or None: the paper stays anonymous."""
    return reviewed_citations().get(p['paper_id']) if public_category(p) == 'number_matches' else None

def is_named(p):
    return p['stage_group'] == 'confirmed' or citation_entry(p) is not None

def label(p, number, lang):
    entry = citation_entry(p)
    if entry:
        return citation_text(entry, lang)
    return p['title'] if p['stage_group'] == 'confirmed' else (f'Paper {number}' if lang == 'en' else f'论文 {number}')

def reason(p, lang):
    stage = p['stage']
    if stage not in REASONS[lang]:
        return ''
    # Free-form reasons can identify papers. Only the existing stage category is public.
    decisions = {'closed_restricted': {'RESTRICTED'}, 'closed_no_printed_target': {'NO_PRINTED_TARGET'}, 'closed_out_of_scope': {'OUT_OF_SCOPE'}, 'closed_duplicate': {'DUPLICATE'}, 'waiting_login': {'NEEDS_LOGIN_PACKAGE'}, 'waiting_account': {'NEEDS_ACCOUNT'}, 'waiting_data_unavailable': {'DATA_UNAVAILABLE'}, 'waiting_journal_table': {'NEEDS_JOURNAL_TABLE'}}
    evidence = any((t.get('record', {}).get('decision_new') or t.get('record', {}).get('decision')) in decisions[stage] for t in p.get('triage', []) if not t.get('record', {}).get('superseded', False))
    queue = p.get('queue') or {}
    evidence = evidence or bool(queue.get('reason') or queue.get('failure_reason'))
    if not evidence:
        return 'Evidence missing' if lang == 'en' else '证据缺失'
    return REASONS[lang][stage]

def catalogue_papers(s):
    """Keep display keys tied to fixed catalogue order, independent of snapshot sorting."""
    if not _CATALOGUE_IDS:
        load_snapshot()
    by_id = {p['paper_id']: p for p in s['papers']}
    return [by_id[paper_id] for paper_id in _CATALOGUE_IDS]

def paper_numbers(s):
    """Anonymous labels count 1..N down the list as the page shows it (category, then gap); a named paper has none.

    A paper is named when its numbers match and its citation is reviewed (R27), or when it is confirmed. No number on the page is a
    position in the catalogue."""
    return {p['paper_id']: n for n, p in enumerate((p for _, p, _ in plot_rows(s) if not is_named(p)), 1)}

def display_keys(s):
    """Row and cell keys: the position in the list as the page shows it (p01 to the last re-estimated paper)."""
    return {p['paper_id']: f'p{n:02}' for n, (_, p, _) in enumerate(plot_rows(s), 1)}

def result_anchor(s, paper_id):
    """Id of a named paper's detail section: v5-result-1, v5-result-2 ... in catalogue order."""
    named = [p['paper_id'] for p in catalogue_papers(s) if p['stage_group'] == 'confirmed']
    return f'v5-result-{named.index(paper_id) + 1}'

def public_projection(s, lang):
    keys, numbers = display_keys(s), paper_numbers(s)
    return [{'key': keys[p['paper_id']], 'name': label(p, numbers.get(p['paper_id']), lang), 'category': public_category(p)}
            for p in catalogue_papers(s) if public_category(p)]

def eligible_audits(p):
    return [next(r for r in p['runs'] if r['run_id']==a['run_id']) for a in p['audits'] if a.get('eligible')]

def comparable_ports(p):
    primary = p.get('primary')
    if not primary:
        return []
    # Conservative: only the exact captured target AND spec contract permits overlay.
    return [r for r in p['runs'] if r['code_origin']=='port_of_author_code' and r.get('target') == primary.get('target') and r.get('spec') and r['spec'] == primary.get('spec') and gap(r,primary['published']) is not None]

def plot_rows(s):
    rows = [(i,p,gap(p['primary'])) for i,p in enumerate(catalogue_papers(s),1) if p.get('primary')]
    return sorted(rows, key=lambda x:(PUBLIC_CATEGORIES.index(public_category(x[1])), x[2] is None, x[2] or Decimal(0), x[0]))


def grade_label(p, lang, s=None):
    return category_labels(s or load_snapshot(), lang)[public_category(p)]

def mark(value, kind):
    if value is None:
        return ''
    clipped=max(Decimal('-.3'),min(Decimal('.3'),value)); x=float((clipped+Decimal('.3'))/Decimal('.6')*100)
    off=' is-offscale' if clipped!=value else ''
    return f'<span class="v5-mark v5-{kind}{off}" style="left:{x:.5f}%" aria-hidden="true"></span>'

def unavailable_reason(p, lang):
    reasons = p['primary'].get('numeric_checks', {}).get('reason_codes', [])
    if 'interval_unavailable_outside_point_tolerance' in reasons:
        return ('Outside the point tolerance; a comparable interval is unavailable.' if lang == 'en'
                else '超出点估计容差，且缺少可比较的区间。')
    if any('unit' in x or 'scale_mismatch' in x for x in reasons):
        return 'Units or scales differ.' if lang == 'en' else '单位或尺度不同。'
    return 'Comparable target or uncertainty information is unavailable.' if lang == 'en' else '缺少可比较的目标值或不确定性信息。'


def detail_source(lang):
    """How the detailed papers were re-estimated; printed once above the cards."""
    return 'Starting from the authors’ posted data, AI agents wrote and ran code independently of the authors’ code.' if lang == 'en' else 'AI 智能体使用作者公开的数据，独立于原作者代码编写并运行分析代码。'


def _confirmed_detail(p, anchor, lang):
    en=lang=='en'; r=p['primary']; second=eligible_audits(p)[0]
    entry=reviewed_descriptions().get(p['paper_id'])
    require(entry is not None, 'named target needs a reviewed public description: ' + p['paper_id'])
    text=entry[lang]
    rows=[]
    for name, v in [(('Published' if en else '发表值'),r['published']), (('Our estimate' if en else '我们的估计值'),r['ours']), (('A second AI agent' if en else '第二个 AI 智能体'),second['ours'])]:
        ci=v.get('ci95') or v.get('ci')
        if v is second['ours'] and v.get('point') is not None and v.get('se') is not None:
            ci=[v['point'] - 1.96 * v['se'], v['point'] + 1.96 * v['se']]  # the same point +/- 1.96 SE rule as the Our estimate row
        interval=' / '.join(fmt(x) for x in ci) if ci else ('Unavailable' if en else '不可用')
        if v is r['published'] and text['published_interval_note']:
            interval += (' (' + text['published_interval_note'] + ')') if en else ('（' + text['published_interval_note'] + '）')
        rows.append(f'<tr><th scope="row">{name}</th><td data-label="{("Estimate" if en else "估计值")}">{fmt(v.get("point"))}</td><td data-label="{("SE" if en else "标准误")}">{fmt(v.get("se"))}</td><td data-label="{("95% CI" if en else "95% 置信区间")}">{interval}</td></tr>')
    # The public descriptions are reviewed per paper (scripts/viz_site/data/confirmed_descriptions.json, checked on load against the
    # snapshot and the catalogue). Exact journal values above always come from the selected frozen run.
    fields=[('Version' if en else '版本', version_display(entry)),
            ('Target' if en else '目标', text['target']),
            ('Outcome / units' if en else '结果变量 / 单位', text['outcome']),
            ('Sample' if en else '样本', text['sample']),
            ('Estimator' if en else '估计方法', text['estimator']),
            ('Target coverage' if en else '目标覆盖','One selected coefficient' if en else '一个选定系数')]
    dl=''.join(f'<dt>{k}</dt><dd>{escape(v)}</dd>' for k,v in fields)
    return f'''<details class="v5-result" id="{anchor}"><summary>{escape(p['title'])} <span>{p['year']}</span></summary><div class="v5-result-body"><p><a href="https://doi.org/{escape(p['doi'],quote=True)}" rel="noopener">{('Published paper' if en else '已发表论文')} ↗</a></p><dl>{dl}</dl><table><thead><tr><th></th><th>{('Estimate' if en else '估计值')}</th><th>{('SE' if en else '标准误')}</th><th>{('95% CI' if en else '95% 置信区间')}</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></details>'''

CSS = """
.v5{max-width:1100px;margin:24px auto 56px;color:#111827;font-size:15px;line-height:1.65;min-width:0}.v5 *{box-sizing:border-box}.v5 h1{font-size:38px;line-height:1.15;margin:0 0 16px;font-weight:600}.v5 h2{font-size:23px;line-height:1.3;margin:0 0 12px;font-weight:600}.v5 h3{font-size:17px;font-weight:600;margin:22px 0 8px}.v5 p{margin:8px 0 14px}.v5 section{margin:38px 0}.v5 .v5-progress{font-size:18px}.v5 .v5-scope{max-width:860px}.v5 .v5-small{max-width:75ch;font-size:13px;color:#526071}.v5-legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin:14px 0 18px;font-size:13px}.v5-legend span{display:flex;align-items:center;gap:6px}.v5-chip{width:12px;height:12px;display:inline-block;flex:none;border-radius:50%;background:var(--cell)}
.v5 [data-category="number_matches"]{--cell:#2166ac}.v5 [data-category="same_direction"]{--cell:#9a6700}.v5 [data-category="not_comparable"]{--cell:#64748b}.v5 [data-category="opposite_sign"]{--cell:#a23b72}

.v5-row{max-width:560px;display:grid;grid-template-columns:minmax(0,1fr) 100px;gap:18px;align-items:center;border-bottom:1px solid #e5e7eb;min-height:52px;padding:8px 0}.v5-row-name{font-size:13px;line-height:1.5;overflow-wrap:anywhere}.v5-row-name small{display:block;color:#526071;font-size:var(--nx-type-tick);margin-top:3px}.v5-row-value{font-size:13px;font-variant-numeric:tabular-nums;text-align:right;overflow-wrap:anywhere}.v5-mark{display:block;position:absolute;top:50%;transform:translate(-50%,-50%);width:9px;height:9px;border-radius:50%;background:var(--cell)}.v5-not_comparable{border-radius:0}.v5 [data-category="same_direction"]>.v5-chip,.v5-mark.v5-same_direction{border-radius:0;clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%)}.v5 [data-category="opposite_sign"]>.v5-chip,.v5-mark.v5-opposite_sign{border-radius:0;clip-path:polygon(50% 0,100% 100%,0 100%)}.v5 [data-category="not_comparable"]>.v5-chip{border-radius:0}
.v5-strip{position:relative;margin:4px 8px 0;background:linear-gradient(to right,transparent 0 33.333%,#eff6ff 33.333% 66.666%,transparent 66.666% 100%);border-inline:1px solid #cbd5e1}.v5-strip:before{content:"";position:absolute;left:50%;top:0;bottom:0;border-left:1px solid #94a3b8}.v5-strip-lane{display:block;position:absolute;left:0;right:0;height:0}.v5-band-label{text-align:center;font-size:var(--nx-type-tick);color:#526071}.v5-axis-scale{position:relative;height:22px;font-size:var(--nx-type-tick);color:#64748b;margin:3px 8px}.v5-axis-scale span{position:absolute;transform:translateX(-50%)}.v5-axis-scale span:first-child{transform:none}.v5-axis-scale span:last-child{transform:translateX(-100%)}.v5-formula{overflow-wrap:anywhere}
.v5-row-list,.v5-result{border-top:1px solid #cbd5e1}.v5-row-list>summary,.v5-result summary{min-height:52px;cursor:pointer;align-content:center;font-size:16px;color:#1d4ed8;padding:8px 0}.v5-result summary span{color:#64748b;font-size:13px}.v5-result-body{padding:8px 0 20px}.v5-result dl{display:grid;grid-template-columns:160px minmax(0,1fr);gap:6px 16px;font-size:13px}.v5-result dt{color:#64748b}.v5-result dd{margin:0;overflow-wrap:anywhere}.v5 table{width:100%;border-collapse:collapse;font-size:13px}.v5 th,.v5 td{text-align:left;padding:9px;border-bottom:1px solid #e5e7eb;overflow-wrap:anywhere}.v5 a{color:#1d4ed8;text-underline-offset:3px}.v5-result-body>p>a:only-child{display:inline-flex;align-items:center;min-height:44px}.v5:lang(zh){line-height:var(--nx-lh-body)}.v5:lang(zh) :is(.v5-small,.v5-band-label,.v5-row-name,.v5-row-value,.v5-axis-scale){line-height:var(--nx-lh-cap)}.v5 a:focus-visible,.v5 summary:focus-visible{outline:2px solid #111827;outline-offset:3px}
@media(max-width:600px){.v5{margin-top:24px}.v5 h1{font-size:30px}.v5 h2{font-size:21px}.v5 .v5-progress{font-size:17px}.v5-row{grid-template-columns:minmax(0,1fr) 85px;gap:6px 12px;padding:10px 0}.v5-result summary{overflow-wrap:anywhere}.v5-result dl{grid-template-columns:minmax(0,1fr);gap:3px}.v5-result dd{margin-bottom:8px}.v5 table,.v5 tbody{display:block}.v5 thead{position:absolute;width:1px;height:1px;clip:rect(0,0,0,0);overflow:hidden}.v5 tr{display:grid;grid-template-columns:1fr;padding:6px 0;border-bottom:1px solid #e5e7eb}.v5 th,.v5 td{display:block;border:0;padding:3px 0}.v5 td:before{content:attr(data-label) ": ";color:#64748b}.v5:lang(zh) td:before{content:attr(data-label) "\\ff1a"}.v5:lang(zh){line-height:1.75}}
"""

SCRIPT = """(function(){
function landing(){let target;try{target=document.getElementById(decodeURIComponent(location.hash.slice(1)));}catch(_){return;}if(target){let parent=target;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}target.scrollIntoView({block:'start'});}}
window.addEventListener('hashchange',landing);document.addEventListener('click',event=>{const a=event.target.closest('a[href^="#"]');if(a){const target=document.getElementById(a.hash.slice(1));if(target&&target.tagName==='DETAILS')target.open=true;}});if(location.hash)landing();
})();"""


def third_group_definitions(s, en):
    """The definitions line: Same direction, then Not comparable (beyond 10%, no published standard error to compare intervals, not plotted)
    and Opposite sign (beyond 10%, opposite sign). F6 (Finkelstein MF1): two exact groups instead of "Doesn’t match". The groups are checked
    against the snapshot: every Not comparable paper has no published standard error, every Opposite sign paper has the opposite sign."""
    lang = 'en' if en else 'zh'
    labels = category_labels(s, lang)
    counts = public_counts(s)
    no_se, opposite, apart = third_group_split(s)
    require(no_se == counts['not_comparable'] and opposite == counts['opposite_sign'] and apart == 0,
            'a group of papers beyond 10% is not what its name says (Not comparable: no published standard error; Opposite sign): '
            + str((no_se, opposite, apart, counts['not_comparable'], counts['opposite_sign'])))
    require(no_se + opposite > 0, 'the groups beyond 10% are empty')
    if en:
        parts = ([f"{labels['not_comparable']}: no published standard error to compare intervals (not plotted)."] if no_se else []) \
                + ([f"{labels['opposite_sign']}: beyond 10%, opposite sign."] if opposite else [])
        return 'Same direction: beyond 10%, same sign, overlapping intervals. ' + ' '.join(parts)
    parts = ([f"{labels['not_comparable']}：发表值无标准误、无法比较区间（不绘制）。"] if no_se else []) \
            + ([f"{labels['opposite_sign']}：差距超出 10%，符号相反。"] if opposite else [])
    return '方向一致：符号相同，差距超出 10%，区间重叠。' + ''.join(parts)


def render(lang='en'):
    s=load_snapshot(); en=lang=='en'; labels=category_labels(s,lang); counts=public_counts(s)
    legend=''.join(f'<span data-category="{k}"><i class="v5-chip" aria-hidden="true"></i>{escape(labels[k])} <b>{counts[k]}</b></span>' for k in PUBLIC_CATEGORIES)
    rows=[]; marks=[]; last_category=None
    numbers, keys = paper_numbers(s), display_keys(s)
    for _,p,value in plot_rows(s):
        category=public_category(p); shown=label(p,numbers.get(p['paper_id']),lang); name=escape(shown)
        if category != last_category:
            rows.append(f'<h3 data-category="{category}">{escape(labels[category])} · {counts[category]}</h3>')
            last_category=category
        link=paper_links().get(p['paper_id']) if is_named(p) else None
        if link:
            name=f'<a href="{escape(link,quote=True)}" target="_blank" rel="noopener">{name}</a>'
        if p['stage_group']=='confirmed':
            name+=f'<small class="v5-coder-agrees">{("a second AI agent agrees" if en else "第二个 AI 智能体结果一致")} · <a href="#{result_anchor(s,p["paper_id"])}">{("details" if en else "详情")}</a></small>'
        val=fmt_gap(value) if value is not None else '—'
        rows.append(f'<div class="v5-row" data-display-key="{keys[p["paper_id"]]}" data-comparable="{str(value is not None).lower()}" data-category="{category}"><div class="v5-row-name">{name}</div><div class="v5-row-value">{val}</div></div>')
        if value is not None:
            description=escape(f'{shown} · {labels[category]} · {val}',quote=True)
            marks.append(f'<span class="v5-strip-lane" data-category="{category}" style="top:{LANE_TOP+len(marks)*LANE_STEP}px" title="{description}">{mark(value,category)}</span>')
    confirmed=''.join(_confirmed_detail(p,result_anchor(s,p['paper_id']),lang) for p in catalogue_papers(s) if p['stage_group']=='confirmed')
    formula=('Gap: difference from the published estimate, divided by the larger of its absolute value and its standard error (absolute value alone if none is reported). Off-scale values sit at the edge; the list gives them in full.' if en else '差距：重估值与发表值之差，除以发表值绝对值与发表标准误中的较大者（未报告标准误时仅用绝对值）。超出坐标范围的值显示在边缘；列表给出完整数值。')
    method=('One selected estimate per paper; papers with the easiest-to-access data go first. Code starts from authors’ posted data or raw data.' if en else '每篇论文选定一个估计，优先处理数据最易取得的论文。代码从作者公开的数据或原始数据出发。')
    definitions=third_group_definitions(s,en)
    plotted=len(marks); omitted=attempted(s)-plotted
    strip_description=((f'{plotted} estimates plotted, grouped by comparison result; {omitted} without comparable intervals are omitted.' if omitted else f'{plotted} estimates plotted, grouped by comparison result.') if en
                       else (f'绘制 {plotted} 项估计，按比较结果分组；{omitted} 项缺少可比较区间的估计不绘制。' if omitted else f'绘制 {plotted} 项估计，按比较结果分组。'))
    strip_height=LANE_STEP*plotted+LANE_PAD
    band=('Within 10%' if en else '差距不超过 10%')
    return f'''<style>{CSS}</style><article class="v5" lang="{lang}"><header><h1>{('Estimates' if en else '估计')}</h1><p class="v5-progress">{progress_html(lang)}</p><p class="v5-scope">{escape(count_definition(lang))}</p><p class="v5-small">{method}</p></header>
<section aria-labelledby="v5-gap"><h2 id="v5-gap">{('Comparison results' if en else '比较结果')} · {attempted(s)}{' ' if en else ''}{('(' + str(plotted) + ' plotted)' if en else '（图示 ' + str(plotted) + ' 项）')}</h2><div class="v5-legend" aria-label="{('Comparison results' if en else '比较结果')}">{legend}</div><p class="v5-small">{definitions}</p><div class="v5-band-label">{band}</div><div class="v5-strip" role="img" aria-label="{strip_description}" style="height:{strip_height}px">{''.join(marks)}</div><div class="v5-axis-scale"><span style="left:0">−30%</span><span style="left:33.333%">−10%</span><span style="left:50%">0%</span><span style="left:66.666%">+10%</span><span style="left:100%">+30%</span></div><p class="v5-small v5-formula">{formula}</p></section>
<section aria-labelledby="v5-confirmed"><h2 id="v5-confirmed">{('Selected paper details' if en else '部分论文详情')}</h2><p class="v5-small">{detail_source(lang)}</p>{confirmed}</section>
<details class="v5-row-list"><summary>{('All re-estimated papers' if en else '全部重估论文')} · {attempted(s)}</summary>{''.join(rows)}</details>
<div id="method-materials"></div></article><script>{SCRIPT}</script>'''
