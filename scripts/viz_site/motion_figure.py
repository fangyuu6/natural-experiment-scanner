"""The Home motion figure: one canvas animation of the three levels, as a render function.

render(lang) returns one HTML string: a style block, the figure markup (canvas, strip, findings, controls), the inline data
and the inline script. Everything is inline (no external script, font or network); stdlib only.

Where it appears is one switch, MOTION_PLACEMENT:
  'home'     between the adoption-map card and the research-level staircase (restructure_home.render calls slot('home'))
  'explore'  at the top of the Explore page: the page's render adds `{motion_figure.slot('explore', lang)}` at the place
             it wants (one line; restructure_explore.py is not edited here)
  'none'     the figure is off; the animation code stays untouched
slot(place, lang, counts) returns the figure when MOTION_PLACEMENT is `place` and an empty string otherwise.

Numbers and wording come from existing sources, never from this file alone:
  screening   counts passed by the caller (Home passes the staircase figures), else data/motion_data.json
  estimates   restructure_estimates (attempted, public_counts, category_labels) for the pinned snapshot
  explore     data/l3_findings.json (the findings, their short labels and the one exploratory label)
  maps        data/motion_data.json, written by make_motion_data.py (SPID v1.2 and the 8 verified policies); the wall shows no
              count of maps or policies, because the Explore page names a different database count
  names       i18n/story_map.{lang}.json (the nurse compact's short name and the year label of the Home map)
The animation code is assets/motion_figure.js, the styles assets/motion_figure.css.
"""
from html import escape
import json
from pathlib import Path
import re

from scripts.viz_site import restructure_estimates as estimates

HERE = Path(__file__).resolve().parent
MOTION_PLACEMENT = 'home'                           # 'home' | 'explore' | 'none'
MOTION_VERSION = 'v14'                              # 'v14': the owner's finalized dots-on-the-map animation (motion_figure_v14.py); 'v51': the bar-and-grid figure of V5.1/V5.2 (this module's render)
WIDTH = 'column'                                    # 'column': the page column (1100 px); 'card': the width of the Home map card
# Owner-taste switches (hub decisions of the art gate, one constant each):
STRIP_NUMBERS = False       # False: the bottom strip names the three levels (the staircase below carries the numbers); True: names with numbers
ESTIMATE_LANE = False       # True: a schematic 'Estimate and interval' lane in act 2, the papers leave it; False: they leave the nurse map
TALL_BELOW = 900            # a figure narrower than this many px (its own width) takes the tall layout
AUTOPLAY_VISIBLE = 0.6      # share of the figure in view that starts the animation
POSTER_SECONDS = 1.5        # the still shown before play (bar and counters); reduced motion shows the last frame instead
DATA_FILE = HERE / 'data' / 'motion_data.json'
FINDINGS_FILE = HERE / 'data' / 'l3_findings.json'
ASSETS = HERE / 'assets'
MIN_FINDINGS = 6

# Every word on the canvas and in the strip, per language. Lists run from the longest wording to the shortest.
STRINGS = {
    'en': {
        'levels': ('Screening', 'Estimates', 'Explore'),
        'texts': 'policy texts', 'cands': 'candidates', 'dot': '1 dot ≈ {n} texts',
        'ofStates': '{a} of {n} jurisdictions', 'estLab': 'Estimate and interval',
        'wallCap': ['State policy adoption maps', 'Policy adoption maps'],
        'clockCap': ['Descriptive: median number of states with the policy 5 years after the first state',
                     'Descriptive: median states with the policy by year 5', 'Descriptive: median by year 5'],
        'clockHead': ['Year', 'after the first state'],
        'key': 'Years after the first state', 'none': 'none recorded', 'unobs': 'under 5 years observed',
        'bands': ['before 1900', '1900–49', '1950–79', '1980–99', '2000 on'], 'med': 'median {m}',
        'unit3': 'findings', 'nurse': 'the nurse licensure compact',
        'replay': 'Replay', 'pause': 'Pause', 'play': 'Play',
        'result': '{label}: {n} {unit}', 'results': ', ',
        'aria': ('Animation in three acts. Screening: {texts} unique policy texts are screened to {cands} candidates. '
                 'Estimates: a design is shown for one policy, {nurse}, and {papers} published papers are re-estimated. '
                 'By result, {results}. '
                 'Explore: a wall of state policy adoption maps, and {n} findings, listed beside it.'),
        'figure': 'Screening, Estimates and Explore',
    },
    'zh': {
        'levels': ('筛查', '估计', '探索'),
        'texts': '份政策文本', 'cands': '条候选记录', 'dot': '1 个点 ≈ {n} 份文本',
        'ofStates': '{a} / {n} 个辖区', 'estLab': '估计值与置信区间',
        'wallCap': ['州政策采纳地图', '政策采纳地图'],
        'clockCap': ['描述性：首个州之后第 5 年已采纳的州数（中位数）', '描述性：第 5 年已采纳州数（中位）', '描述性：第 5 年中位数'],
        'clockHead': ['首个州之后第', '年'],
        'key': '晚于首个州的年数', 'none': '无记录', 'unobs': '观察期不足 5 年',
        'bands': ['1900 年前', '1900–49', '1950–79', '1980–99', '2000 年起'], 'med': '中位数 {m}',
        'unit3': '项发现', 'nurse': '护士执照协定',
        'replay': '重播', 'pause': '暂停', 'play': '播放',
        'result': '{n} 篇{label}', 'results': '，',
        'aria': ('分三幕的动画。筛查：{texts} 份不重复政策文本筛出 {cands} 条候选记录。'
                 '估计：展示了一项政策（{nurse}）的设计，{papers} 篇已发表论文被重新估计：{results}。'
                 '探索：一面州政策采纳地图墙，旁边列出 {n} 项发现。'),
        'figure': '筛查、估计与探索',
    },
}


def _read(path):
    return path.read_text(encoding='utf-8')


def _json_text(value):
    """JSON safe inside a script element."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/').replace('<!--', '<\\!--')


def findings(lang):
    """(label, [short label of each finding]) from l3_findings.json, in the file's order. The label is the exploratory one for Home."""
    doc = json.loads(_read(FINDINGS_FILE))
    rows = sorted(doc['findings'], key=lambda row: row['order'])
    if len(rows) < MIN_FINDINGS:
        raise ValueError(f'the motion figure needs at least {MIN_FINDINGS} findings; {FINDINGS_FILE.name} has {len(rows)}')
    for row in rows:
        if row.get('status') != 'exploratory':
            raise ValueError('finding %s is not marked exploratory' % row.get('id'))
    # The Home figure prints no ranges: it reads label_home when the file has it, else the shared label.
    return doc.get('label_home', doc['label'])[lang], [row['short'][lang] for row in rows]


CATEGORIES = ('number_matches', 'same_direction', 'not_comparable', 'opposite_sign')      # the Estimates page's four groups, in its order


def counts(lang, given=None):
    """Every number in the strip. `given` (the Home staircase figures) wins where it has a key; the rest is read at the
    source: motion_data.json for Screening, the pinned snapshot for Estimates."""
    data = json.loads(_read(DATA_FILE))
    snapshot = estimates.load_snapshot()
    public = estimates.public_counts(snapshot)
    names = estimates.category_labels(snapshot, lang)
    if tuple(public) != CATEGORIES or tuple(names) != CATEGORIES:
        raise ValueError('the figure draws the four result groups of Estimates; Estimates now has ' + ', '.join(public))
    given = given or {}
    return {'texts': given.get('texts', data['counts']['texts']), 'cands': given.get('candidates', data['counts']['cands']),
            'papers': given.get('papers', estimates.attempted(snapshot)), 'matched': given.get('matched', public['number_matches']),
            'match': public['number_matches'], 'dir': public['same_direction'], 'nc': public['not_comparable'], 'os': public['opposite_sign'],
            'labels': [names[key] for key in CATEGORIES]}


def staircase_line(lang, key, **numbers):
    """The wording of a Home staircase line (home_story.{lang}.json), each number in bold. The staircase's own segments (a number with
    its unit) stay one no-wrap piece each, so the line breaks only between them: the strip says what the staircase says."""
    raw = json.loads(_read(HERE / f'i18n/home_story.{lang}.json'))[f'story.{key}.line_html']
    raw = raw.format(**{name: f'\x01{name}\x02' for name in numbers})
    raw = raw.replace('<span class="nx-screen-arrow" aria-hidden="true">→</span>', '→')
    raw = re.sub(r'<span class="(?:nx-seg|nx-candidates)">', '<span class="mf-seg">', raw)
    parts = re.split(r'(<[^>]+>)', raw)
    if any(part.startswith('<') and part not in ('<span class="mf-seg">', '</span>') for part in parts):
        raise ValueError('the staircase line has markup the strip does not know: ' + raw)
    text = ''.join(part if part.startswith('<') else escape(part) for part in parts)
    for name, value in numbers.items():
        text = text.replace(f'\x01{name}\x02', f'<b class="mf-val">{value}</b>')
    return text


def label_html(text):
    """A finding's short label as markup. A hyphenated word ("re-estimates") is one no-wrap piece, so a line never breaks inside it; the
    text itself is unchanged."""
    marked = re.sub(r'[A-Za-z]+(?:-[A-Za-z]+)+', lambda match: '<span class="mf-nb">' + match.group() + '</span>', escape(text))
    # A row is a flex line (counter, text): a label with a span inside goes in one text span, so that the span is not a flex item of its own.
    return marked if marked == escape(text) else '<span class="mf-t">' + marked + '</span>'


def _width_css():
    if WIDTH == 'card':        # the right-hand column of the Home hero grid: 3 of 5 parts after the gap
        return '@media(min-width:1100px){.mf{max-width:calc((100% - var(--nx-hero-gap,48px)) * .6);margin-left:auto}}'
    return ''


def _icons():
    return {'pause': '<svg class="mf-i-pause" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M5.5 4v8M10.5 4v8"/></svg>',
            'play': '<svg class="mf-i-play" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M5.8 3.8v8.4l6.4-4.2z"/></svg>',
            'replay': '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3.2 8a4.8 4.8 0 1 0 1.5-3.5M3 2.8v2.9h2.9"/></svg>'}


def render(lang='en', given=None):
    if lang not in STRINGS:
        raise ValueError(lang)
    s = STRINGS[lang]
    names = json.loads(_read(HERE / f'i18n/story_map.{lang}.json'))
    n = counts(lang, given)
    label, shorts = findings(lang)
    data = json.loads(_read(DATA_FILE))
    data.pop('inputs', None)
    data.pop('asof', None)
    results = s['results'].join(s['result'].format(label=name, n=n[key], unit='paper' if n[key] == 1 else 'papers') for key, name in zip(('match', 'dir', 'nc', 'os'), n['labels']))
    aria = s['aria'].format(texts=f"{n['texts']:,}", cands=f"{n['cands']:,}", papers=n['papers'], results=results,
                            nurse=s['nurse'], n=len(shorts))
    cfg = {'lang': lang, 'lane': ESTIMATE_LANE, 'tallBelow': TALL_BELOW, 'autoplay': AUTOPLAY_VISIBLE, 'poster': POSTER_SECONDS,
           'counts': {'papers': n['papers'], 'match': n['match'], 'dir': n['dir'], 'nc': n['nc'], 'os': n['os']},
           'labels': n['labels'], 'findings': shorts,
           's': {'texts': s['texts'], 'cands': s['cands'], 'dot': s['dot'], 'ofStates': s['ofStates'],
                 'nurse': names['map.p.nurse_licensure_compact.short'], 'signed': names['map.lg.nurse_licensure_compact'],
                 'wallCap': s['wallCap'], 'clockCap': s['clockCap'], 'clockHead': s['clockHead'],
                 'key': s['key'], 'none': s['none'], 'unobs': s['unobs'], 'bands': s['bands'], 'med': s['med']}}
    if ESTIMATE_LANE:
        cfg['s']['estLab'] = s['estLab']
    ic = _icons()
    if STRIP_NUMBERS:
        screen = staircase_line(lang, 'screen', texts=f"{n['texts']:,}", candidates=f"{n['cands']:,}")
        estimate = staircase_line(lang, 'estimate', papers=f'<span class="mf-count">{n["papers"]}</span>', matched=n['matched'])
        head, comma, tail = estimate.partition('，' if lang == 'zh' else ', ')
        lines = (f'<p class="mf-line">{screen}</p>', f'<p class="mf-line">{head}<span class="mf-late">{comma}{tail}</span></p>',
                 f'<p class="mf-line"><b class="mf-val">{len(shorts)}</b> {escape(s["unit3"])}</p>')
    else:
        lines = ('', '', '')       # the strip names the levels; their numbers are the staircase's, the animation's and the text alternative's
    cells = ''.join(f'<li class="mf-cell" data-lv="{k}"><p class="mf-lab">{escape(s["levels"][k - 1])}</p>{lines[k - 1]}</li>' for k in (1, 2, 3))
    items = ''.join(f'<li>{label_html(short)}</li>' for short in shorts)
    css = _read(ASSETS / 'motion_figure.css') + _width_css()
    script = _read(ASSETS / 'motion_figure.js')
    if '</script' in script.lower():
        raise ValueError('motion_figure.js must not contain a closing script tag')
    return (
        f'<style>{css}</style>'
        f'<section class="mf-sec" aria-label="{escape(s["figure"])}">'
        f'<figure class="mf" id="motion-figure"><div class="mf-stage">'
        f'<canvas class="mf-canvas" role="img" aria-label="{escape(aria)}" aria-describedby="mf-findings"></canvas>'
        f'<div class="mf-ctl">'
        f'<button type="button" class="mf-btn" data-act="toggle" aria-pressed="false" aria-label="{escape(s["pause"])}" title="{escape(s["pause"])}">{ic["pause"]}{ic["play"]}</button>'
        f'<button type="button" class="mf-btn" data-act="replay" aria-label="{escape(s["replay"])}" title="{escape(s["replay"])}">{ic["replay"]}</button></div>'
        f'<div class="mf-find" id="mf-findings"><p class="mf-tag">{escape(label)}</p><ol>{items}</ol></div>'
        f'<ol class="mf-strip">{cells}</ol>'
        f'</div>'
        f'<script type="application/json" id="mf-cfg">{_json_text(cfg)}</script>'
        f'<script type="application/json" id="mf-data">{_json_text(data)}</script>'
        f'</figure></section>'
        f'<script>{script}</script>')


def slot(place, lang='en', given=None):
    """The figure when MOTION_PLACEMENT is `place`, else ''. Pages call this where they want the figure."""
    if place not in ('home', 'explore'):
        raise ValueError(place)
    if MOTION_PLACEMENT != place:
        return ''
    if MOTION_VERSION == 'v14':
        from scripts.viz_site import motion_figure_v14
        return motion_figure_v14.render(lang, given)
    return render(lang, given)
