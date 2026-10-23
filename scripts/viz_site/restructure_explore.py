"""V5 Explore presentation. Reads stored evidence only; never fits a model.

V5.1 (R25, owner 2026-10-14): the lead, the figure labels and the Methods panel are in plain words, and a "Questions" list shows
what L3 is working on. The copy is that of docs/plans/site-restructure/EXPLORE_PLAIN_COPY_V51.md, sections 2, 3 and 5.

R26 (content check of R25): the first figure is titled by what it shows, odds ratios have two decimals, the axes carry round
odds ratios as ticks, apostrophes and quotation marks are typographic, and the Questions list has a third status word for the
questions that have no data yet.

V5.1 release (owner 2026-10-20: L3 is a broad layer that yields many cross-policy patterns, not one study): the page is Explore.
Head (heading, one lead sentence, the one exploratory label), Findings (scripts/viz_site/data/l3_findings.json: the findings, the
"not seen" line), the Questions board, then the 35-policy adoption study as one section (heading, lead, the sentence that reconciles
it with the party-control finding, its two figures, Methods, Sources).

V5.1 panel round F1 (2026-10-20): seven findings (the party-control-moves-toward-the-law's-party finding is gone); the line on re-estimates is
a template filled from the pinned Estimates snapshot (`finding_line`), so it cannot disagree with Estimates; Methods name the version of the
adoption database behind the 648 policies; the figures say odds ratio where the number is one."""
from functools import lru_cache
from html import escape
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'docs/plans/l3-pooled/RESULTS.md'
META = ROOT / 'data/l3_meta/l2_set_v2.json'
LIVE = ROOT / 'data/l3_meta/l2_set_live.json'
EXECUTION = ROOT / 'scripts/l3_pooled/spec/v1_8/EXECUTION.json'
MOTION = ROOT / 'scripts/viz_site/data/motion_data.json'
QUESTIONS = ROOT / 'scripts/viz_site/data/explore_questions.json'
FINDINGS = ROOT / 'scripts/viz_site/data/l3_findings.json'
STORIES = ROOT / 'scripts/viz_site/data/l3_stories.json'
# Words an exploratory L3 result must never carry on the page (owner: never "confirmed" for an L3 result).
CONFIRM_WORDS = re.compile(r'confirm|\bproven?\b|已确认|确认|证实|已证明', re.I)
STATUS = {'finished': ('Finished', '已完成'), 'in_progress': ('In progress', '进行中'), 'waiting': ('Waiting for data', '等待数据')}

CSS = '''
.explore-v5{max-width:1100px;margin:24px auto;color:#111827;line-height:1.65;min-width:0}
.explore-v5 *{box-sizing:border-box}.explore-v5 h1{font-size:38px;font-weight:600;line-height:1.15;letter-spacing:normal;margin:0 0 16px;text-wrap:balance}
.explore-v5 h2{font-size:23px;line-height:1.35;margin:0 0 12px}.explore-v5 h3{font-size:18px;margin:0 0 12px}
.explore-v5 p{margin:8px 0 16px}.explore-v5 .ex-muted{color:#536174;font-size:13px}
.explore-v5 section,.explore-v5 figure{margin:34px 0 0;padding-top:28px;border-top:1px solid #dce1e8;min-width:0}
.explore-v5 figure{margin-inline:0}
.explore-v5 .ex-questions{list-style:none;margin:0;padding:0}
.explore-v5 .ex-questions li{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,19rem);gap:2px 24px;align-items:baseline;padding:10px 0;border-top:1px solid #e5e7eb}
.explore-v5 .ex-questions+h3{margin-top:24px}
.explore-v5 .ex-questions li:first-child{border-top:0;padding-top:0}
.explore-v5 .ex-findings{list-style:none;margin:0;padding:0}
.explore-v5 .ex-findings li{display:grid;grid-template-columns:minmax(0,17rem) minmax(0,1fr);gap:4px 24px;align-items:baseline;padding:12px 0;border-top:1px solid #e5e7eb}
.explore-v5 .ex-findings li:first-child{border-top:0;padding-top:0}
.explore-v5 .ex-findings+p.ex-how{margin-top:16px;font-size:13px;color:#536174;max-width:75ch}
.explore-v5 .ex-findings p.ex-numbers{margin-top:6px;font-size:13px;color:#536174;max-width:75ch}
.explore-v5 .ex-findings h3{font-size:16px;font-weight:600;line-height:1.45;margin:0}.explore-v5 .ex-findings p{margin:0;font-size:16px;overflow-wrap:break-word}
.explore-v5 .ex-q a{display:inline-flex;align-items:center;min-height:44px}
.explore-v5 .ex-data{font-size:13px;color:#536174}
.explore-v5 .ex-row{margin:22px 0}.explore-v5 .ex-row-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:15px}
.explore-v5 .ex-value{font-variant-numeric:tabular-nums;font-size:14px}.explore-v5 svg{width:100%;height:36px;display:block;overflow:visible}
.explore-v5 .ex-chart{position:relative}.explore-v5 .ex-dot{position:absolute;top:14px;width:8px;height:8px;border-radius:50%;background:#1d4ed8;transform:translateX(-50%)}
.explore-v5 .ex-ticks{position:relative;height:20px;color:#536174;font-size:var(--nx-type-tick)}.explore-v5 .ex-ticks span{position:absolute;transform:translateX(-50%)}
.explore-v5 .ex-axis{display:flex;justify-content:space-between;color:#536174;font-size:var(--nx-type-tick);gap:4px 16px;flex-wrap:wrap}
.explore-v5 details{border-top:1px solid #dce1e8;padding:8px 0;margin-top:24px}.explore-v5 summary{min-height:44px;cursor:pointer;align-content:center;font-weight:400;font-size:16px;color:#1d4ed8}
.explore-v5 dl{display:grid;grid-template-columns:minmax(110px,1fr) minmax(0,3fr);gap:12px 24px;font-size:14px}.explore-v5 dt{font-weight:600}.explore-v5 dd{margin:0;overflow-wrap:anywhere}
.explore-v5 .ex-count{font-size:28px;line-height:1.3;font-variant-numeric:tabular-nums;letter-spacing:-.02em}.explore-v5 code{overflow-wrap:anywhere;white-space:normal;font-size:var(--nx-type-tick)}
.explore-v5 figure>figcaption{margin-top:16px}.explore-v5 .ex-muted{max-width:75ch}.explore-v5 .ex-lead{max-width:860px;font-size:18px;text-wrap:pretty}.explore-v5 .ex-label{margin:-4px 0 0;font-size:14px;color:#536174}.explore-v5 #study>p{max-width:860px}.explore-v5 .ex-methods{font-size:14px}.explore-v5 :focus-visible{outline:3px solid #1d4ed8;outline-offset:3px}
.explore-v5 [hidden]{display:none!important}:lang(zh) .explore-v5{line-height:1.75}
@media(max-width:700px){.explore-v5 .ex-questions li{grid-template-columns:minmax(0,1fr);gap:2px 16px}.explore-v5 .ex-findings li{grid-template-columns:minmax(0,1fr);gap:2px 16px}}
@media(max-width:600px){.explore-v5 h1{font-size:30px}.explore-v5 dl{grid-template-columns:1fr;gap:5px}.explore-v5 dd{margin-bottom:13px}.explore-v5 .ex-row-head{display:flex;flex-direction:column;align-items:flex-start;gap:4px}.explore-v5 .ex-value{max-width:100%;overflow-wrap:anywhere}.explore-v5 h2{font-size:21px}}
'''


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def load_inputs():
    text = REPORT.read_text(encoding='utf-8').split('## Runner-written final reporting', 1)[1]
    result = json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])
    meta, live, execution = _read(META), _read(LIVE), _read(EXECUTION)
    if any(row['confidence'] != .975 for row in result['H1'].values()):
        raise ValueError('H1 registered confidence changed')
    if result['H1'] != meta['pooled_adoption_reference']['historical_H1']:
        raise ValueError('C17 and stored H1 reference disagree')
    if any(row['lower'] >= 0 or row['upper'] <= 0 for row in result['H1'].values()):
        raise ValueError('H1 interpretation requires review')
    # The source/fitted counts are recorded in this durable, reviewed build report.
    build_report = (ROOT / 'docs/plans/l3-results/SYNTH_BUILD_V2.md').read_text()
    fitted = re.search(r'(\d+) policies / (\d+) fitted events / ([\d,]+) rows; source (\d+) / (\d+) / ([\d,]+)', build_report)
    if not fitted:
        raise ValueError('Missing source-to-fit sample counts')
    sample = list(map(lambda x: int(x.replace(',', '')), fitted.groups()))
    report = (ROOT / 'docs/plans/l3-pooled/V2_BATCH_REPORT.md').read_text()
    roster = int(re.search(r'DK denominators unchanged:(\d+) policies', report).group(1))
    return result, meta, live, execution, sample, roster


@lru_cache(maxsize=1)
def load_questions():
    """The questions of the Questions list in the order of the copy file; the first one is the adoption study itself."""
    rows = _read(QUESTIONS)['rows']
    if [row['n'] for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('Explore questions are not numbered 1..N in order')
    for row in rows:
        if row['status'] not in STATUS or set(row['question']) != {'en', 'zh'} or set(row['data']) != {'en', 'zh'}:
            raise ValueError('Explore question row %s is malformed' % row['n'])
        if len(row['question']['en'].split()) > 12:
            raise ValueError('Explore question %s is longer than 12 words' % row['n'])
    return rows


def fill_counts(text, computed, name):
    """A text with placeholders is a template filled from the loader named in `computed` (restructure_estimates.reestimate_summary, the pinned
    snapshot): the counts are never typed into the file."""
    if not computed:
        if '{' in text:
            raise ValueError('Explore finding %s has a placeholder but no computed entry' % name)
        return text
    from scripts.viz_site import restructure_estimates as estimates
    if computed.get('loader', '').split()[0] != 'scripts/viz_site/restructure_estimates.py':
        raise ValueError('Explore finding %s names a loader this page does not know' % name)
    values = estimates.reestimate_summary()
    if set(values) != set(computed['fields']):
        raise ValueError('Explore finding %s: computed fields differ from the loader' % name)
    return text.format(**values)


def finding_line(row, lang):
    """The Home-side sentence of a finding (the audit wording of l3_findings.json, filled from its loader where it is a template). The Explore page
    prints the plain version (`explore_text`); this line is kept for the audit trail and its tests."""
    return fill_counts(row[lang]['line'], row.get('computed'), row.get('id'))


# What the plain Explore findings must not carry in a headline (owner, 2026-10-21: "听不懂"), checked at build time.
HEAD_JARGON = re.compile(r'noise line|reshuffle|random timing|excess|variance|false alarm|p\s*=|标准差|噪声线|重排|随机时点|误报|方差', re.I)
MAX_HEAD_WORDS = 15
MAX_HEAD_CHARS_ZH = 36


def explore_text(item, field, lang, computed=None):
    """One field ('head', 'why' or 'numbers') of a plain Explore finding as the page prints it."""
    if computed is None:
        doc = _read(FINDINGS)
        computed = next((r.get('computed') for r in doc['findings'] + doc['not_seen']['parts'] if r['id'] == item.get('id')), None)
    return fill_counts(item[lang][field], computed, item.get('id'))


@lru_cache(maxsize=1)
def load_findings():
    """(label, findings, explore, reconcile) of scripts/viz_site/data/l3_findings.json: the one exploratory label of this page (`label`; the Home
    figure prints `label_home`), the audit rows of the findings (`findings`, whose short labels Home reads), the plain Explore view (`explore`: the lead,
    the findings it prints with a headline, one sentence on why it matters and a numbers line) and the sentence that reconciles the 35-policy
    study with the party-control finding. A finding that is not exploratory, a missing text, jargon in a headline or a word of confirmation stops the build."""
    doc = _read(FINDINGS)
    rows = sorted(doc['findings'], key=lambda row: row['order'])
    if not rows or [row['order'] for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError('Explore findings are not numbered 1..N in order')
    view = doc['explore']
    known = {row['id']: row for row in rows} | {part['id']: part for part in doc['not_seen']['parts']}
    texts = [part[lang] for part in (doc['label'], doc['label_home'], view['lead'], view['how'], doc['reconcile_35']) for lang in ('en', 'zh')]
    for row in rows:
        if row.get('status') != 'exploratory':
            raise ValueError('Explore finding %s is not marked exploratory' % row.get('id'))
    ids = [item['id'] for item in view['items']]
    if not ids or len(set(ids)) != len(ids) or any(i not in known for i in ids) or set(view['removed']) & set(ids):
        raise ValueError('Explore view: its findings must be distinct audit rows of the file and none of the removed ones')
    for item in view['items']:
        computed = known[item['id']].get('computed')
        for lang in ('en', 'zh'):
            for field in ('head', 'why', 'numbers'):
                texts.append(explore_text(item, field, lang, computed))
            head = item[lang]['head']
            if HEAD_JARGON.search(head) or len(head.split()) > MAX_HEAD_WORDS and lang == 'en' or len(head) > MAX_HEAD_CHARS_ZH and lang == 'zh':
                raise ValueError('Explore headline is not plain or too long: ' + head)
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Explore finding text is missing')
        if CONFIRM_WORDS.search(text):
            raise ValueError('Explore finding text claims confirmation: ' + text[:60])
    return doc['label'], rows, view, doc['reconcile_35']


def _pick(lang, en, zh):
    return zh if lang == 'zh' else en


def native_count():
    """Policies in the full database (SPID v1.2): the number the Home wall of maps counts, read from its data."""
    return int(json.loads(MOTION.read_text(encoding='utf-8'))['tiles']['spid'])


def interval_figure(lang, rows, *, h1, planning_effect=None, roster=None, fitted=None):
    """One figure of odds ratios with their ranges. The log-odds view and its selector are gone (V5.1): every number is an odds ratio."""
    if h1 and (planning_effect is None or not math.isfinite(planning_effect)):
        raise ValueError("H1 requires a finite registered planning effect")
    if fitted is None:
        raise ValueError("the figure titles and captions need the number of fitted policies")
    tr = lambda en, zh: _pick(lang, en, zh)
    key = 'h1' if h1 else 'h0'
    title = tr(f'Results for the {fitted} policies', f'{fitted} 项政策的结果') if h1 else tr('Same-party states already adopted', '同党派州已采纳')
    level = tr('97.5% interval', '97.5% 区间') if h1 else tr('95% interval', '95% 区间')
    low, high = (-.3, .5) if h1 else (-.3, 2.1)
    lo, hi = math.exp(low), math.exp(high)
    at = lambda odds: 10 + 380 * (odds - lo) / (hi - lo)
    x = lambda value: at(math.exp(value))
    entries = []
    for name, p, l, u in rows:
        label = tr(*name)
        value = f'{math.exp(p):.2f} [{math.exp(l):.2f}, {math.exp(u):.2f}]'.replace('-', '−')
        entries.append(f'<div class="ex-row"><div class="ex-row-head"><span>{escape(label)}</span><span class="ex-value">{value}</span></div><div class="ex-chart"><svg viewBox="0 0 400 36" preserveAspectRatio="none" aria-hidden="true"><path d="M10 18H390" stroke="#e2e8f0"/><path d="M{x(0):.3f} 2V34" stroke="#94a3b8"/>'
                       +(f'<path d="M{x(planning_effect):.3f} 2V34" stroke="#b45309" stroke-dasharray="3 3"/>' if h1 else '')
                       +f'<path d="M{x(l):.3f} 18H{x(u):.3f}" stroke="#1d4ed8" stroke-width="2"/></svg><span class="ex-dot" style="left:{x(p)/4:.3f}%" aria-hidden="true"></span></div></div>')
    axis = (tr('Odds ratio of adopting in a year, per typical difference between states (1 = no link)', '某年采纳政策的优势比，各州间每相差一个典型差距（1 = 无关联）') if h1 else
            tr('Odds ratio of adopting in a year when all, rather than none, of the states under the same party control have already adopted (1 = no link)',
               '与本州党派控制类型相同的州已全部（而非全无）采纳时，某年采纳政策的优势比（1 = 无关联）'))
    ticks = [(.8, '0.8'), (1, '1.0'), (1.2, '1.2'), (1.4, '1.4'), (1.6, '1.6')] if h1 else [(1, '1'), (2, '2'), (4, '4'), (6, '6'), (8, '8')]
    tick_html = ''.join(f'<span style="left:{at(odds)/4:.3f}%">{label}</span>' for odds, label in ticks)
    if h1:
        caption = tr('The planned claim that party matters more than need is not supported. Every interval ends below the dashed line.',
                     '事先设定的“党派比需要更重要”的论断未获支持。每个区间的上端都低于虚线。')
        dashed = tr(f'Dashed line: the link size the study was planned to detect ({math.exp(planning_effect):.2f}).',
                    f'虚线：研究计划检测的关联大小（{math.exp(planning_effect):.2f}）。')
        planning = f'<p class="ex-muted">{escape(dashed)}</p>'
    else:
        caption = tr(f'This figure uses all {roster} policies of the replication version; the one above uses {fitted}.',
                     f'本图使用复现版本的全部 {roster} 项政策；上图使用 {fitted} 项。')
        planning = ''
    return f'<figure id="{key}"><h3>{escape(title)}</h3><p class="ex-muted">{escape(level)}</p>{planning}<div>{"".join(entries)}<div class="ex-ticks">{tick_html}</div><div class="ex-axis"><span>{escape(axis)}</span></div></div><figcaption>{escape(caption)}</figcaption></figure>'


STORY_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')


def story_numbers(text):
    """The numbers a sentence prints (sign, thousands separator and a trailing percent sign dropped), in the order printed."""
    return [n.rstrip(',') for n in STORY_NUMBER.findall(text)]


@lru_cache(maxsize=1)
def load_stories():
    """(groups, items) of scripts/viz_site/data/l3_stories.json (V5.5, T237): the exploratory stories printed on Explore before the technical checks. A story
    has a plain headline without a number or jargon, one sentence on why it matters and a muted numbers line; every number of that line is in the
    story's audit list and the two languages print the same numbers. A missing text, jargon or a word of confirmation stops the build."""
    doc = _read(STORIES)
    groups, items = doc['groups'], doc['items']
    ids = [item['id'] for item in items]
    if not items or len(set(ids)) != len(ids) or any(item['group'] not in groups for item in items):
        raise ValueError('Explore stories: ids must be distinct and each in a known group')
    for item in items:
        audit = set(item['audit'])
        for lang in ('en', 'zh'):
            for field in ('head', 'why', 'numbers'):
                text = item[lang][field]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('Explore story text is missing: %s' % item['id'])
                if CONFIRM_WORDS.search(text):
                    raise ValueError('Explore story text claims confirmation: ' + text[:60])
            head = item[lang]['head']
            if HEAD_JARGON.search(head) or re.search(r'\d', head) or (len(head.split()) > MAX_HEAD_WORDS and lang == 'en') or (len(head) > MAX_HEAD_CHARS_ZH and lang == 'zh'):
                raise ValueError('Explore story headline is not plain or too long: ' + head)
            if set(story_numbers(item[lang]['numbers'])) - audit:
                raise ValueError('Explore story %s prints a number that is not in its audit list' % item['id'])
        if sorted(story_numbers(item['en']['numbers'])) != sorted(story_numbers(item['zh']['numbers'])):
            raise ValueError('Explore story %s: the numbers differ between languages' % item['id'])
    return groups, items


def stories(lang):
    """The story sections, one per group in the order of the file: each story as a plain headline, one sentence on why it matters and a muted numbers
    line, in the markup of the Findings list. The exploratory label is printed once, in the page head."""
    groups, items = load_stories()
    out = []
    for key in groups:
        rows = ''.join(f'<li><h3>{escape(item[lang]["head"])}</h3><div><p>{escape(item[lang]["why"])}</p>'
                       f'<p class="ex-numbers">{escape(item[lang]["numbers"])}</p></div></li>' for item in items if item['group'] == key)
        out.append(f'<section id="stories-{key}" class="ex-stories" aria-labelledby="ex-stories-{key}-h"><h2 id="ex-stories-{key}-h">{escape(groups[key][lang])}</h2>'
                   f'<ol class="ex-findings">{rows}</ol></section>')
    return ''.join(out)


def findings(lang):
    """The Findings section: each finding as a plain headline, one sentence on why it matters and a muted line with its numbers. The exploratory
    label is printed once, in the page head, and the findings carry no ids of the result files."""
    tr = lambda en, zh: _pick(lang, en, zh)
    _, rows, view, _ = load_findings()
    known = {row['id']: row for row in rows} | {part['id']: part for part in _read(FINDINGS)['not_seen']['parts']}
    items = ''.join(
        f'<li><h3>{escape(explore_text(item, "head", lang))}</h3><div><p>{escape(explore_text(item, "why", lang))}</p>'
        f'<p class="ex-numbers">{escape(explore_text(item, "numbers", lang, known[item["id"]].get("computed")))}</p></div></li>'
        for item in view['items'])
    return (f'<section id="findings" aria-labelledby="ex-findings-h"><h2 id="ex-findings-h">{tr("Technical checks", "技术核对")}</h2>'
            f'<ol class="ex-findings">{items}</ol><p class="ex-how">{escape(view["how"][lang])}</p></section>')


def questions(lang, fitted, events):
    """The Questions list, grouped under its three status headings (R29 trim): the question and, where it has one, its data line; no results
    (the adoption study's are in the figures). Each heading is printed once; rows keep their order of the copy file inside a group."""
    tr = lambda en, zh: _pick(lang, en, zh)
    groups = {key: [] for key in STATUS}
    for row in load_questions():
        question = escape(row['question'][lang])
        if row['n'] == 1:
            question = f'<a href="#h1">{question}</a>'
        data = row['data'][lang].format(fitted=fitted, events=events)
        data = f'<span class="ex-data">{escape(data)}</span>' if data else ''
        groups[row['status']].append(f'<li><span class="ex-q">{question}</span>{data}</li>')
    blocks = ''.join(f'<h3>{escape(tr(*STATUS[key]))}</h3><ol class="ex-questions">{"".join(items)}</ol>' for key, items in groups.items() if items)
    return f'<section id="questions" aria-labelledby="ex-questions-h"><h2 id="ex-questions-h">{tr("Questions", "问题")}</h2>{blocks}</section>'


def sources(lang):
    tr = lambda en, zh: _pick(lang, en, zh)
    return f'''<details class="ex-methods" id="explore-sources"><summary>{tr('Sources', '来源')}</summary><p>{tr('State Policy Innovation and Diffusion (SPID) database', '州政策创新与扩散数据库（SPID）')}{tr(':', '：')}</p><p>Frederick J. Boehmke, Mark Brockway, Bruce A. Desmarais, Jeffrey J. Harden, Scott LaCombe, Fridolin Linder, and Hanna Wallach (2020). “SPID: A New Database for Inferring Public Policy Innovativeness and Diffusion Networks.” <em>Policy Studies Journal</em> 48(2): 517–545. <a href="https://doi.org/10.1111/psj.12357">doi:10.1111/psj.12357</a>.</p><p>Stefano DellaVigna and Woojin Kim (2026). “Policy Diffusion and Polarization across U.S. States.” <em>The Review of Economic Studies</em> 93(3): 1602–1634. <a href="https://doi.org/10.1093/restud/rdaf070">doi:10.1093/restud/rdaf070</a>. <a href="https://zenodo.org/records/15590121">{tr('Replication data', '复现数据')}</a>{tr('.', '。')}</p></details>'''


def render(lang):
    if lang not in ('en','zh'):raise ValueError(lang)
    tr=lambda en,zh:_pick(lang,en,zh)
    result,meta,live,execution,sample,roster=load_inputs()
    planning_effect=.64*execution["h1_scaling"]["party_scale"]
    fitted,events,rows,supplied,source_events,source_rows=sample
    # The caption says every range ends below the dashed line: fail closed if the planned effect no longer exceeds an upper limit.
    if any(result['H1'][key]['upper'] >= planning_effect for key in ('party', 'need', 'difference')):
        raise ValueError('Explore caption says every range ends below the dashed line; the planning effect no longer exceeds the upper limits')
    h1=[(names,*(result['H1'][key][v] for v in ['estimate','lower','upper'])) for key,names in [('party',('Democratic control','民主党执政')),('need',('Local need','当地需要')),('difference',('Democratic control relative to local need','民主党执政相对当地需要'))]]
    h0=[(names,result['H0']['point'][key],*result['H0']['inference']['intervals'][key]) for key,names in [('late_A',('2000–2020','2000–2020 年')),('late_minus_early_A',('2000–2020 compared with 1950–1999','2000–2020 年与 1950–1999 年相比'))]]
    study_heading=tr('Party control and local need','党派控制与当地需要')
    study_lead=tr(f'On average across {fitted} policies ({events} adoptions), neither Democratic control nor greater local need (poverty, traffic deaths) clearly predicted which states adopted in a given year.',
            f'对 {fitted} 项政策（{events} 次采纳）取平均，民主党执政和更大的当地需要（贫困、交通事故死亡），均未能明确预测哪些州会在某一年采纳政策。')
    sample_paragraph=tr(f'The State Policy Innovation and Diffusion database lists {native_count()} state policies; the version posted with DellaVigna and Kim’s replication data lists {roster}; {supplied} have a local-need measure and the other data needed, and the model can use {fitted}: {events} adoptions in {rows:,} state-year records of the 48 contiguous states.',
                        f'州政策创新与扩散数据库共列有 {native_count()} 项州政策；随 DellaVigna 与 Kim 复现数据发布的版本列有 {roster} 项；{supplied} 项有当地需要指标及所需的其他数据，模型可用 {fitted} 项：美国本土 48 州的 {rows:,} 条州—年记录，含 {events} 次采纳。')
    model_paragraph=tr('From the year after a policy’s first adoption, the model asks each year which states not yet adopting do so, with a baseline for every policy and year. Party control is +1 when Democrats hold the governorship and both legislative chambers on 1 January, −1 when Republicans do, 0 otherwise, the same way for every policy. Local need is an earlier measure chosen in advance for each policy type: poverty for welfare, tax-credit and minimum-wage laws; traffic deaths for drunk-driving and helmet laws. A typical difference is one standard deviation.',
                       '从政策首次被采纳的下一年起，模型逐年考察尚未采纳的州是否采纳，每项政策、每一年有各自的基准。党派控制：1 月 1 日，民主党同时掌握州长职位和两院，记为 +1；共和党同时掌握，记为 −1；其余记为 0；所有政策记法相同。当地需要是事先为每类政策选定的较早年份的指标：福利、税收抵免和最低工资类法律用贫困率，酒驾和头盔类法律用交通事故死亡率。“典型差距”指该指标的一个标准差。')
    label,rows_found,view,reconcile=load_findings()
    page_lead=view['lead'][lang]
    head=f'<header><h1>{tr("Explore","探索")}</h1><p class="ex-lead">{escape(page_lead)}</p><p class="ex-label">{escape(label[lang])}</p></header>'
    methods=f'''<details class="ex-methods" id="study-methods"><summary>{tr('Methods', '方法')}</summary><p>{escape(sample_paragraph)}</p><p>{escape(model_paragraph)}</p></details>'''
    study=(f'<section id="study" aria-labelledby="ex-study-h"><h2 id="ex-study-h">{escape(study_heading)}</h2><p class="ex-lead">{escape(study_lead)}</p><p>{escape(reconcile[lang])}</p>'
           f'{interval_figure(lang,h1,h1=True,planning_effect=planning_effect,fitted=fitted)}{interval_figure(lang,h0,h1=False,roster=roster,fitted=fitted)}{methods}{sources(lang)}</section>')
    return f'<style>{CSS}</style><main class="explore-v5">{head}{stories(lang)}{findings(lang)}{questions(lang,fitted,events)}{study}</main>'
