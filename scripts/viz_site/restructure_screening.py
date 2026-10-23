"""V5 Screening renderer. Reads the first row of each policy_id once. The candidate list is embedded and works offline; the panel of an opened
candidate is the original colour-coded one (legacy_detail.py), loaded from the API on an explicit open, with the compact card as the fallback."""
from __future__ import annotations
import csv
from datetime import date, datetime
import html
import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


# Query parameters whose NAME (whole name, any case) carries a credential. Names that only contain such a word stay: a legislative
# Session=2019 or txtSessionYear=2019 is part of the address.
CREDENTIAL_PARAMS = frozenset((
    'api_key', 'apikey', 'api-key', 'x-api-key', 'api_token', 'key', 'token', 'access_token', 'access-token', 'refresh_token',
    'id_token', 'auth', 'authorization', 'password', 'passwd', 'pwd', 'secret', 'client_secret', 'signature', 'sig',
    'credential', 'credentials', 'jsessionid', 'phpsessid', 'email', 'mail', 'user', 'username', 'userid', 'user_id',
))
# SessionID=112 is the legislative session on ilga.gov and azleg.gov; as a login session it is a long opaque value.
SESSION_ID_PARAMS = frozenset(('sessionid', 'session_id'))
SESSION_TOKEN_MIN = 16


def credential_param(segment):
    name, _, value = segment.partition('=')
    name = unquote_plus(name).strip().lower()
    return name in CREDENTIAL_PARAMS or (name in SESSION_ID_PARAMS and len(unquote_plus(value).strip()) >= SESSION_TOKEN_MIN)


def strip_credential_params(query):
    """The query string as written, minus the credential parameters (and the one separator beside each); nothing else changes."""
    parts = re.split(r'([&;])', query)
    out, started = [], False
    for i in range(0, len(parts), 2):
        if parts[i] and credential_param(parts[i]):
            continue
        if started:
            out.append(parts[i - 1])
        out.append(parts[i])
        started = True
    return ''.join(out)


def safe_url(value):
    """Keep the document's own address (path, query order and percent-encoding as written) and drop credentials, API keys and the fragment."""
    try:
        url = urlsplit(value or '')
        if url.scheme not in ('https', 'http') or not url.hostname or '@' in url.netloc:
            return ''
        path = re.sub(r'(?i);(?:jsessionid|phpsessid)=[^/?#;]*', '', url.path)
        # api.govinfo.gov package links need a key and answer 401 to visitors; the same document has a public address.
        api = re.fullmatch(r'/packages/([^/]+)/granules/([^/]+)/htm', path) if (url.hostname or '').lower() == 'api.govinfo.gov' else None
        if api:
            return 'https://www.govinfo.gov/content/pkg/%s/html/%s.htm' % api.groups()
        return urlunsplit((url.scheme, url.netloc, path, strip_credential_params(url.query), ''))
    except ValueError:
        return ''


# Source spellings are unified before filter options are built.
STATE_NAMES = dict(pair.split(':') for pair in (
    'AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|'
    'CT:Connecticut|DE:Delaware|DC:District of Columbia|FL:Florida|GA:Georgia|'
    'HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|'
    'LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|'
    'MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|'
    'NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|'
    'OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|'
    'SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|'
    'WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming'
).split('|'))


def public_jurisdiction(value):
    value = re.sub(r' State$', '', value or '')
    return STATE_NAMES.get(value, value)


# The model run behind the paper's current abstract (owner, 2026-10-14: option C); read from the paper folder's sibling.
PILOT_TABLE = 'research/model_pilot_20260930/comparison_table.md'


def wilson(k, n, z=1.96):
    """Pointwise Wilson 95% score interval in percent: the method behind the paper's Table 1 intervals (z = 1.96)."""
    p = k / n
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return 100 * max(0.0, (centre - half) / denominator), 100 * min(1.0, (centre + half) / denominator)


def pilot_recall(table, abstract, positives):
    """DiD and RD benchmark recall of the model run behind the paper's current abstract.

    `table` is the pilot's comparison table, `abstract` the DiD and RD percentages printed in the paper's abstract and
    `positives` the benchmark's DID and RD positive-paper counts (Table 1). The model column is the one whose two
    printed percentages equal the abstract's; its counts must reproduce those percentages and the benchmark size."""
    try:
        block = table.split('**Benchmark (positive-only rediscovery recall', 1)[1].split('\n**', 1)[0]
    except IndexError:
        raise ValueError('AEJ model comparison table format changed')
    lines = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in block.splitlines() if line.startswith('|')]
    header, body = lines[0], lines[2:]

    def row(label):
        found = [r for r in body if re.fullmatch(label, r[0]) and len(r) == len(header)]
        if len(found) != 1:
            raise ValueError('AEJ model comparison table format changed')
        return found[0]

    def count(r, j):
        match = re.fullmatch(r'(\d+) = ([\d.]+)%(?: \(\d+\))?', r[j])
        return match and (int(match[1]), float(match[2]))

    did, rd = row(r'DID recall /\d+'), row(r'RD recall /\d+')
    columns = [j for j in range(1, len(header)) if count(did, j) and count(rd, j) and [count(did, j)[1], count(rd, j)[1]] == abstract]
    if len(columns) != 1:
        raise ValueError('AEJ abstract recall matches no single model column of the comparison table')
    rows = []
    for design, r, n in (('DiD', did, positives[0]), ('RD', rd, positives[1])):
        k, rate = count(r, columns[0])
        if r[0].rsplit('/', 1)[1] != str(n) or round(100 * k / n, 1) != rate:
            raise ValueError('AEJ model-run recall fraction, percentage and benchmark size disagree')
        low, high = (float(f'{value:.1f}') for value in wilson(k, n))
        rows.append(dict(design=design, k=k, n=n, rate=rate, low=low, high=high))
    return rows, header[columns[0]]


def pilot_precision(table, model):
    """DiD and RD precision (percent) of one model column of the pilot's comparison table.

    The first block of the table scores the 158 freshly coded texts against the final adjudicated labels (3-run majority).
    `model` is the column `pilot_recall` took the recall rows from, so recall and precision describe one run. Each printed
    percentage must equal TP / (TP + FP) of the same column's TP/FP/FN/TN counts."""
    try:
        block = table.split('**fresh158 vs final adjudicated label, majority', 1)[1].split('\n**', 1)[0]
    except IndexError:
        raise ValueError('AEJ model comparison table format changed')
    lines = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in block.splitlines() if line.startswith('|')]
    if len(lines) < 3 or lines[0].count(model) != 1:
        raise ValueError('AEJ model comparison table format changed')
    header, body = lines[0], lines[2:]
    column = header.index(model)

    def cell(label):
        found = [r for r in body if re.fullmatch(label, r[0]) and len(r) == len(header)]
        if len(found) != 1:
            raise ValueError('AEJ model comparison table format changed')
        return found[0][column]

    values = []
    for design in ('DID', 'RD'):
        printed = re.fullmatch(r'([\d.]+) \[\d+,\d+\]', cell(design + r' precision %'))
        counts = re.fullmatch(r'(\d+)/(\d+)/\d+/\d+', cell(design + r' TP/FP/FN/TN \(n pos=\d+/\d+\)'))
        if not printed or not counts:
            raise ValueError('AEJ model comparison table format changed')
        flagged = int(counts[1]) + int(counts[2])
        if not flagged or round(100 * int(counts[1]) / flagged, 1) != float(printed[1]):
            raise ValueError('AEJ model-run precision and its counts disagree')
        values.append(float(printed[1]))
    return values


@lru_cache(maxsize=1)
def load_data():
    csv.field_size_limit(2**31 - 1)
    source_note = (ROOT / 'paper/aej_latest/SOURCE.txt').read_text()
    source = Path(re.search(r'^Source folder: (.+)$', source_note, re.M)[1])
    declared = re.search(r'^Declared draft date in main.tex: (.+?) \(', source_note, re.M)[1]
    main = (source / 'main.tex').read_text()
    if declared not in main:
        raise ValueError('AEJ source note draft date no longer matches main.tex')
    title = re.search(r'\\title\{([^{}]+)\}', main)[1]
    tex = (source / 'tables/tab_benchmark_rediscovery_20260719.tex').read_text()
    positives = [int(re.search(axis + r' axis \((\d+) positive papers\)', tex)[1]) for axis in ('DID', 'RD')]
    abstract = re.search(r'recovers\s+([\d.]+)\s+percent\s+of\s+the\s+DID\s+designs\s+and\s+([\d.]+)\s+percent\s+of\s+the\s+RD\s+designs', main)
    if not abstract:
        raise ValueError('AEJ abstract recall sentence changed')
    table = (source.parent / PILOT_TABLE).read_text()
    rows, model = pilot_recall(table, [float(value) for value in abstract.groups()], positives)
    precision = pilot_precision(table, model)
    # The revised draft is the main.tex saved with that abstract; its day must be the pilot folder's day.
    saved = re.search(r'^main\.tex\t(\d{4})-(\d{2})-(\d{2})T', source_note, re.M)
    pilot = re.search(r'model_pilot_(\d{4})(\d{2})(\d{2})', PILOT_TABLE)
    if not saved or saved.groups() != pilot.groups():
        raise ValueError('AEJ revised draft date and model pilot date differ')
    saved_on = date(*map(int, saved.groups()))
    draft = f'{saved_on:%B} {saved_on.day}, {saved_on.year}'
    benchmark = (source / 'appendix/D_benchmark_full.tex').read_text()
    bills = int(re.search(r'^Bill & \d+ & \d+ & (\d+)', benchmark, re.M)[1])
    benchmark_total = int(re.search(r'^Total & \d+ & \d+ & (\d+)', benchmark, re.M)[1])
    derived = {}
    with (ROOT / 'data/output/derived/derived_views.csv').open(newline='') as stream:
        for row in csv.DictReader(stream):
            derived.setdefault(row['policy_id'], row['viable_strict'] == '1')
    shortlist_ids = {key for key, value in derived.items() if value}
    members, seen = [], set()
    with (ROOT / 'data/output/policy_db_20260415_483k.csv').open(newline='', errors='replace') as stream:
        for row in csv.DictReader(stream):
            key = row['policy_id']
            if key in seen:
                continue
            seen.add(key)
            if key not in shortlist_ids:
                continue
            detail = json.loads(row.get('detail_json') or '{}')
            fields = ['treatment', 'control_group', 'treatment_timing', 'rd_info', 'outcomes', 'datasets']
            scores = ['policy_significance', 'identification', 'treatment_intensity', 'data_feasibility']
            # R28: the CSV column `id` is the row id that GET /api/policies/{id}/detail takes (`id` above stays the policy_id).
            if not re.fullmatch(r'\d+', row['id'] or ''):
                raise ValueError('Screening member without a numeric CSV id: ' + key)
            members.append(dict(score=float(row['composite_score'] or 0), id=key, api_id=int(row['id']), title=row['title'] or key, design=('Both' if row['did']=='1' and row['rd']=='1' else 'DiD' if row['did']=='1' else 'RD'), source=row['source_family'], topic=row['policy_topic'], year=row['year'], jurisdiction=public_jurisdiction(row['jurisdiction']), family=row['policy_family'], url=safe_url(row['url'] or detail.get('document_url')), elements={k:str(detail.get(k) or '') for k in fields}, scores={k:str(detail.get(k) or '') for k in scores}))
    members.sort(key=lambda row: (-row['score'], row['id']))
    from scripts.viz_site.public_page import credential_leaks
    if credential_leaks(json.dumps(members)) or any(row['url'] != safe_url(row['url']) for row in members):
        raise ValueError('Screening members carry an API key or a credential parameter')
    if shortlist_ids - seen:
        raise ValueError('Shortlist IDs missing from policy members')
    if len({row['api_id'] for row in members}) != len(members):
        raise ValueError('Screening members share a CSV id')
    funnel = json.loads((ROOT / 'deploy_output/funnel_data.json').read_text())
    audit = json.loads((ROOT / 'paper/aej_latest/COUNT_AUDIT.json').read_text())
    if len(members) != funnel['stages'][-1]['n_out'] or len(seen) != funnel['total'] or len(members) != audit['derived']['unique_first_viable_strict_1']:
        raise ValueError('Screening source counts do not reconcile')
    return dict(total=len(seen), count=len(members), members=members, recall=rows, draft=draft, title=title, model=model, precision=precision, federal=Counter(r['source'] for r in members)['Federal Regulations'], bills=bills, benchmark_total=benchmark_total, stages=funnel['stages'], as_of=funnel['generated'])


def metadata():
    d = load_data()
    return {k:v for k,v in d.items() if k not in ('members', 'stages')}


def flow(lang):
    """The converging-flow figure, as partials/story_flow.html (built by make_story_flow.py) renders it with its own dictionary.

    Owner, 2026-10-14 (V5.1): "the Screening page could add the pipeline the previous website made". The figure prints only
    counts this page or Method already print: the texts, the four source families and the candidates."""
    from scripts.viz_site import build as viz
    errors = viz.Errors()
    figure = viz.render_partial('story_flow', lang, errors, 'screening')
    if errors.errors:
        raise ValueError('; '.join(errors.errors))
    return figure


def render(lang='en'):
    d = load_data()
    zh = lang == 'zh'
    t = json.loads((HERE / f'i18n/screening.{lang}.json').read_text())
    esc = html.escape
    def p(key): return esc(t[key])
    if not zh and t['paper_title'] != d['title']:
        raise ValueError('Screening paper title no longer matches the paper source')
    draft_date = datetime.strptime(d['draft'], '%B %d, %Y')
    draft_label = f'{draft_date.year} 年 {draft_date.month} 月 {draft_date.day} 日' if zh else d['draft']
    updated = datetime.fromisoformat(d['as_of'].replace('Z', '+00:00'))
    updated_label = f'{updated.year} 年 {updated.month} 月 {updated.day} 日' if zh else f'{updated:%B} {updated.day}, {updated.year}'
    space, stop, semicolon = ('', '。', '；') if zh else (' ', '.', '; ')
    chart = []
    for row in d['recall']:
        chart.append(f'''<div class="sc-recall-row"><strong>{row['design']}</strong><div class="sc-recall-track" aria-hidden="true"><span class="sc-interval" style="left:{row['low']}%;width:{row['high']-row['low']:.1f}%"></span><span class="sc-dot" style="left:{row['rate']}%"></span></div><span>{row['k']}/{row['n']} · {row['rate']:.1f}% <small>[{row['low']:.1f}, {row['high']:.1f}]</small></span></div>''')
    filters=''.join(f'<label>{p(key)}<select data-field="{key}" aria-label="{p(key)}"><option value="">{p("all")}</option></select></label>' for key in ['design','source','topic','year','jurisdiction'])
    payload = json.dumps(d['members'], ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    translations = json.dumps(t, ensure_ascii=False).replace('<', '\\u003c')
    countline = t['countline'].format(total=f'{d["total"]:,}', count=f'{d["count"]:,}')
    deployment = t['deployment'].format(federal=round(100*d['federal']/d['count']), bills=round(100*d['bills']/d['benchmark_total']),
                                        federal_n=f'{d["federal"]:,}', count=f'{d["count"]:,}', bills_n=d['bills'], total=d['benchmark_total'])
    from scripts.viz_site import legacy_detail
    return f'''<style>{CSS}{DETAIL_CSS}{legacy_detail.css()}</style>
<main class="sc-page" id="main">
<header class="sc-intro"><h1>{p('title')}</h1><p class="sc-countline">{esc(countline)}</p><p>{p('subtitle')}</p><p class="sc-muted">{p('sampling')}</p></header>
<div class="sc-flow">{flow(lang)}</div>
<section class="sc-benchmark" aria-labelledby="sc-benchmark-title"><h2 id="sc-benchmark-title">{p('finding')}</h2><p class="sc-muted">{p('paper_title')}<br>{p("benchmark_model").format(model=esc(d["model"]), draft=esc(draft_label))}</p><div class="sc-recall">{''.join(chart)}</div><p class="sc-muted">{p('chart_key')}</p><p class="sc-deployment">{p('precision').format(did=d['precision'][0], rd=d['precision'][1])}</p><p class="sc-deployment">{esc(deployment)}</p></section>
<details class="sc-method" id="screening-method"><summary>{p('method')}</summary><p>{p('paper_source')}{space}{esc(draft_label)}{stop}</p><p><a href="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6487478">{p('public_paper')}</a>{stop}</p><p class="sc-muted">{p('as_of')}{space}<time datetime="{updated.date().isoformat()}">{esc(updated_label)}</time>{stop}</p></details>
<section class="sc-search" aria-labelledby="sc-browse-heading"><div class="sc-section-heading"><h2 id="sc-browse-heading">{p('shortlist')}</h2><a href="/viz/{lang}/all_records.html">{p('all_records')} →</a></div><p class="sc-muted">{p('shortlist_model')}</p><label class="sc-search-label" for="sc-query">{p('search')}</label><input id="sc-query" type="search" placeholder="{p('search_hint')}" autocomplete="off"><div class="sc-filters">{filters}</div></section>
<section aria-label="{p('results_label')}"><div class="sc-results-heading"><p id="sc-result-count" role="status" aria-live="polite">{d['count']:,} {p('results')}</p><button id="sc-reset" type="button">{p('reset')}</button></div><div id="sc-records"></div><noscript><p>{p('noscript')}</p></noscript><nav class="sc-pagination" aria-label="{p('pagination')}"><button type="button" id="sc-prev">← {p('previous')}</button><span id="sc-page-number"></span><button type="button" id="sc-next">{p('next')} →</button></nav></section>
<div class="sc-secondary"><a href="{'/zh' if zh else ''}/policy_groups.html">{p('groups')} →</a><a href="/viz/{lang}/adoption.html">{p('map')} →</a></div>
</main><script type="application/json" id="sc-members">{payload}</script><script>const scText={translations};{DISPLAY_JS}{legacy_detail.script(lang)}{JS}</script>'''


CSS = '''
.sc-page{max-width:1100px;margin:24px auto 56px;padding:0;color:#111827;font-size:15px;line-height:1.65;overflow-wrap:anywhere}.sc-page *{box-sizing:border-box}.sc-page h1{font-size:38px;font-weight:600;letter-spacing:normal;line-height:1.15;margin:0 0 16px;text-wrap:balance}.sc-page h2{font-size:20px;font-weight:600;margin:0}.sc-page p{margin:8px 0}.sc-page a{color:#1d4ed8;text-underline-offset:3px}.sc-kicker,.sc-muted{color:#64748b;font-size:13px}.sc-countline{font-size:21px}.sc-intro{margin-bottom:30px}.sc-flow{margin:0 0 32px;--nx-gutter:.7rem;overflow-wrap:normal}.sc-section-heading,.sc-results-heading,.sc-secondary{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.sc-section-heading a,.sc-secondary a{min-height:44px;display:inline-flex;align-items:center}.sc-search-label{display:block;margin-top:12px}.sc-page input,.sc-page select,.sc-page button{font:inherit;font-size:var(--nx-type-input);min-height:44px;border:1px solid #cbd5e1;border-radius:5px;background:white;color:#111827;max-width:100%}.sc-page input{width:100%;padding:10px 12px}.sc-page select{display:block;width:100%;padding:8px;min-width:0}.sc-page button{padding:8px 12px;cursor:pointer}.sc-page button:disabled{opacity:.45;cursor:default}.sc-filters{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:14px 0 24px}.sc-filters label{font-size:13px;min-width:0}.sc-benchmark{padding:20px 0 0;border-top:1px solid #e2e8f0}.sc-benchmark .sc-muted{text-wrap:balance}.sc-recall{max-width:730px;margin-top:14px}.sc-recall-row{display:grid;grid-template-columns:40px minmax(90px,1fr) 205px;gap:18px;align-items:center;margin:15px 0;font-size:14px}.sc-recall-row small{font-size:var(--nx-type-tick);color:#64748b}.sc-recall-track{height:26px;position:relative;background:linear-gradient(#e2e8f0,#e2e8f0) center/100% 1px no-repeat}.sc-interval{position:absolute;top:12px;height:2px;background:#1d4ed8}.sc-dot{position:absolute;top:8px;width:10px;height:10px;border-radius:50%;background:#1d4ed8;transform:translateX(-50%)}.sc-deployment{max-width:760px;font-size:13px;color:#475569}.sc-results-heading{margin:14px 0}.sc-record{border-top:1px solid #e2e8f0;padding:6px 0}.sc-record summary{padding:12px 0;cursor:pointer;min-height:44px}.sc-record-title{font-weight:600;font-size:16px}.sc-record-meta{display:block;color:#64748b;font-size:13px;margin-top:4px}.sc-record-body{padding:6px 0 20px}.sc-compact{max-width:820px}.sc-record-body dl{display:grid;grid-template-columns:155px minmax(0,1fr);gap:8px 16px;margin:14px 0}.sc-record-body dt{color:#64748b;font-size:13px}.sc-record-body dd{margin:0;font-size:14px}.sc-record-body a,.sc-record-body button{display:inline-flex;align-items:center;min-height:44px;margin-right:12px}.sc-note summary{padding:4px 0;font-size:14px;font-weight:400}.sc-note-more,.sc-note-less{color:#1d4ed8;text-decoration:underline;text-underline-offset:3px}.sc-note-less,.sc-note[open] .sc-note-preview,.sc-note[open] .sc-note-more{display:none}.sc-note[open] .sc-note-less{display:inline}.sc-note-full{white-space:normal}.sc-record-body .sc-scores{color:#475569;font-size:13px}.sc-pagination{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:20px 0;font-size:13px}.sc-method{margin:0 0 24px}.sc-search{border-top:1px solid #e2e8f0;padding-top:20px}.sc-method summary{cursor:pointer;color:#1d4ed8;min-height:44px;align-content:center}.sc-method p{max-width:840px;font-size:14px;text-wrap:pretty}.sc-method p a{display:inline-flex;align-items:center;min-height:44px}.sc-stages{list-style:none;margin:18px 0;padding:0;max-width:530px}.sc-stages li{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #eef2f7;padding:7px 0}.sc-secondary{border-top:1px solid #e2e8f0;margin-top:24px;padding-top:12px;justify-content:flex-start;gap:24px}.sc-page :focus-visible{outline:3px solid #1d4ed8;outline-offset:3px}html[lang^=zh] .sc-page{line-height:1.75}@media(max-width:600px){.sc-page{padding:0}.sc-page h1{font-size:30px}.sc-countline{font-size:19px}.sc-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.sc-filters label:last-child{grid-column:1/-1}.sc-recall-row{grid-template-columns:32px minmax(0,1fr);gap:8px}.sc-recall-row>span:last-child{grid-column:2}.sc-record-body dl{grid-template-columns:1fr;gap:2px}.sc-record-body dd{margin-bottom:10px}.sc-pagination button{padding:8px}.sc-pagination{font-size:var(--nx-type-tick)}.sc-page h2{font-size:18px}}
'''


# R28: the opened candidate shows the original panel (scripts/viz_site/legacy_detail.py). The panel was designed inside the old table's open row,
# so it brings that row's surroundings with it: the grey cell, 14 px text, the 44 px buttons of the table. The rules below give it the same
# surroundings on this page and undo the page-wide rules that would otherwise restyle its links and chapter buttons; the panel's own rules follow.
DETAIL_CSS = '''
.sc-detail-note{font-size:14px}.sc-detail-failed{color:#b91c1c}.sc-detail{margin:10px 0 6px;padding:.8rem;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;font-size:14px;line-height:1.6;color:#111827;text-align:left;overflow-wrap:anywhere;cursor:default}html[lang^=zh] .sc-detail{line-height:1.75}.sc-record-body :where(.sc-detail) a{display:inline;align-items:normal;min-height:0;margin-right:0}.sc-detail :where(button).chapter-pill{margin-right:0;max-width:none;padding:.25rem .55rem;border:1px solid #d8b4fe;border-radius:9999px;background:#fff;color:#4c1d95;font-size:var(--nx-type-input)}@media(max-width:700px){.sc-detail{padding:.6rem}}
'''


# Pure display functions shared by the browser and offline visible-text checks.
# Embedded members and filter values retain their source values.
DISPLAY_JS = r'''
// SCREENING DISPLAY START
const scIsZh=()=>/[\u4e00-\u9fff]/.test(scText.title);
const scEscape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function scCleanText(value){
  return String(value??'').replace(/\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b/g,raw=>{
    const date=new Date(raw);return Number.isNaN(date.getTime())?raw.slice(0,10):(scIsZh()?`${date.getUTCFullYear()} \u5e74 ${date.getUTCMonth()+1} \u6708 ${date.getUTCDate()} \u65e5`:date.toLocaleDateString('en-US',{year:'numeric',month:'long',day:'numeric',timeZone:'UTC'}));
  }).replace(/\bdate_--\b|\bdate_--/g,'').replace(/\bother[:_]/g,'').replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9-]*)+\b/g,token=>scText[token]||token.replace(/_/g,' ')).replace(/_/g,' ').replace(/\bgvwr\b/gi,scText.gvwr);
}
function scDisplayText(value){return scCleanText(value).trim();}
// The opened panel is markup built from the API record: the same cleaner runs over its text and its tooltips, never over tags, links or styles.
function scCleanHtml(html){
  return String(html).replace(/(<[^>]*>)|([^<]+)/g,(part,tag,text)=>tag?tag.replace(/\btitle="([^"]*)"/g,(attribute,value)=>'title="'+scCleanText(value)+'"'):scCleanText(text));
}
function scDisplayLabel(value){
  if(!value)return '';
  if(scText[value])return scText[value];
  const text=scDisplayText(value);return text.charAt(0).toUpperCase()+text.slice(1);
}
function scDisplayFamily(value){return scIsZh()&&!scText[value]?scText.family_selected:scDisplayLabel(value);}
function scDisplayTitle(row){
  const title=String(row.title||'').trim();
  if(title&&title!==row.id&&!/^(?:\d+|fedreg-[\w-]+)$/i.test(title))return scDisplayText(title);
  return [scDisplayLabel(row.topic)||scText.policy_record,scDisplayLabel(row.source)].filter(Boolean).join(' · ');
}
function scDisplayElement(key,value){
  if(key!=='rd_info')return scDisplayText(value);
  return String(value).split('|').map(part=>{
    const match=part.trim().match(/^([^:]+):\s*(.*)$/);
    if(!match)return scDisplayText(part);
    if(match[2].trim()==='date_--')return '';
    // Source placeholders contain drafting instructions, not observed RD values.
    if(/number_from_legislative_text|provide_the_specific_numeric_cutoff|should_be_inserted_here/i.test(match[2]))return '';
    const label=scText['rd_'+match[1].trim().toLowerCase()]||scDisplayText(match[1]);
    const text=scDisplayText(match[2]).replace(/\s+[.-]+\s*$/,'').replace(/\s+/g,' ').trim();
    return text?label+(scIsZh()?'\uff1a':': ')+text:'';
  }).filter(Boolean).join(' · ');
}
function scDisplayNote(key,value){
  const text=scDisplayElement(key,value),limit=key==='rd_info'?120:240;
  if(text.length<=limit)return scEscape(text);
  let preview=text.slice(0,limit);
  const boundary=preview.lastIndexOf(' ');
  if(boundary>limit*0.7)preview=preview.slice(0,boundary);
  return `<details class="sc-note"><summary><span class="sc-note-preview">${scEscape(preview.trimEnd())}… </span><span class="sc-note-more">${scEscape(scText.show_more)}</span><span class="sc-note-less">${scEscape(scText.show_less)}</span></summary><span class="sc-note-full">${scEscape(text)}</span></details>`;
}
function scDisplayRecord(r){
  const esc=scEscape,label=scDisplayLabel,colon=scIsZh()?'\uff1a':': ';
  const meta=[label(r.design),label(r.source),label(r.jurisdiction),scDisplayText(r.year)||scText.unknown_year,scText.score+colon+r.score.toFixed(2)].filter(Boolean).map((x,i,a)=>i===a.length-1?'<span class="nx-seg">'+esc(x)+'</span>':esc(x)).join(' · ');
  // The compact card is what a visitor sees first, while the panel loads and when it cannot load; the panel replaces it once it is ready.
  return `<details class="sc-record" data-api-id="${esc(r.api_id??'')}"><summary><span class="sc-record-title">${esc(scDisplayTitle(r))}</span><span class="sc-record-meta">${meta}</span></summary><div class="sc-record-body"><p class="sc-muted">${esc(scText.model_labels)}</p><p class="sc-detail-note sc-muted" role="status" hidden></p><div class="sc-detail" hidden></div><div class="sc-compact"><dl>${Object.entries(r.elements).filter(([k,v])=>v).map(([k,v])=>`<dt>${esc(label(k))}</dt><dd>${scDisplayNote(k,v)}</dd>`).join('')}</dl><p class="sc-scores">${Object.entries(r.scores).map(([k,v])=>`${esc(label(k))}${colon}${esc(scDisplayText(v)||scText.unavailable)}`).join(' · ')}</p>${r.url?`<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(scText.original)} ↗</a>`:''}</div>${r.family?`<button type="button" data-family="${esc(r.family)}">${esc(scText.same_family)}</button>`:''}</div></details>`;
}
// SCREENING DISPLAY END
'''

JS = r'''
(()=>{'use strict';
const rows=JSON.parse(document.getElementById('sc-members').textContent),query=document.getElementById('sc-query'),selectors=[...document.querySelectorAll('.sc-filters select')],list=document.getElementById('sc-records'),count=document.getElementById('sc-result-count'),number=document.getElementById('sc-page-number');let page=0, family='';const size=15;
const fmt=n=>n.toLocaleString(document.documentElement.lang),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label=scDisplayLabel;
selectors.forEach(select=>{[...new Set(rows.map(r=>r[select.dataset.field]).filter(Boolean))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true})).forEach(value=>{const o=document.createElement('option');o.value=value;o.textContent=label(value);select.append(o)});select.addEventListener('change',()=>{page=0;render()})});
const haystacks=rows.map(r=>[r.id,r.title,r.family,r.topic,r.jurisdiction].join(' ').toLowerCase());
function render(){const q=query.value.trim().toLowerCase();const selected=rows.filter((r,i)=>(!q||haystacks[i].includes(q))&&(!family||r.family===family)&&selectors.every(s=>!s.value||r[s.dataset.field]===s.value));const pages=Math.max(1,Math.ceil(selected.length/size));page=Math.min(page,pages-1);count.textContent=`${fmt(selected.length)} / ${fmt(rows.length)} ${scText.results}`+(family?' · '+scDisplayFamily(family):'');number.textContent=`${page+1} / ${pages}`;document.getElementById('sc-prev').disabled=page===0;document.getElementById('sc-next').disabled=page>=pages-1;
list.innerHTML=selected.slice(page*size,(page+1)*size).map(scDisplayRecord).join('')||`<p>${esc(scText.empty)}</p>`;
list.querySelectorAll('[data-family]').forEach(b=>b.addEventListener('click',()=>{family=b.dataset.family;query.value='';selectors.forEach(s=>s.value='');page=0;render();count.scrollIntoView({block:'start'})}));}
query.addEventListener('input',()=>{page=0;family='';render()});document.getElementById('sc-reset').addEventListener('click',()=>{page=0;family='';query.value='';selectors.forEach(s=>s.value='');render()});document.getElementById('sc-prev').addEventListener('click',()=>{page--;render();count.scrollIntoView({block:'start'})});document.getElementById('sc-next').addEventListener('click',()=>{page++;render();count.scrollIntoView({block:'start'})});render();
// The candidate's detail is fetched only when its card is opened (never ahead of time, never in a loop): one panel at a time as in the original,
// the answer kept for the session, a failure not kept, so opening the card again is the retry. The compact card stays until the panel is ready.
const byApiId=new Map(rows.map(r=>[r.api_id,r])),panels=new Map(),pending=new Map();
const request=id=>{if(!pending.has(id))pending.set(id,scDetail.fetchDetail(id).finally(()=>pending.delete(id)));return pending.get(id)};
function openDetail(card){
  const row=byApiId.get(Number(card.dataset.apiId)),box=card.querySelector('.sc-detail'),compact=card.querySelector('.sc-compact'),note=card.querySelector('.sc-detail-note');
  if(!row||!box||box.dataset.state==='ready'||box.dataset.state==='loading')return;
  const ready=html=>{box.innerHTML='<div class="detail-content" id="dc-'+row.api_id+'">'+html+'</div>';box.hidden=false;compact.hidden=true;note.hidden=true;box.dataset.state='ready'};
  if(panels.has(row.api_id)){ready(panels.get(row.api_id));return}
  box.dataset.state='loading';note.className='sc-detail-note sc-muted';note.textContent=scText.detail_loading;note.hidden=false;
  request(row.api_id).then(detail=>{
    // The record must be this candidate's own; a row id that now belongs to another policy shows the compact card, not another policy's panel.
    if(String(detail.policy_id)!==String(row.id))throw new Error('detail of another policy');
    return scDetail.buildDetailHtml(detail,'dc-'+row.api_id);
  }).then(html=>{panels.set(row.api_id,html);ready(html)}).catch(()=>{box.dataset.state='';note.className='sc-detail-note sc-detail-failed';note.textContent=scText.detail_failed;note.hidden=false});
}
list.addEventListener('toggle',event=>{
  const card=event.target;
  if(!card.classList||!card.classList.contains('sc-record')||!card.open)return;
  list.querySelectorAll('details.sc-record[open]').forEach(other=>{if(other!==card)other.open=false});
  openDetail(card);
},true);
})();
'''
