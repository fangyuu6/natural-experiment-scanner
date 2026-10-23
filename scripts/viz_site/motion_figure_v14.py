"""The Home motion figure, fourteenth round (owner 2026-10-21: "give it back the whole earlier version").

render(lang, given) returns one HTML string: a style block, the figure (canvas and two buttons), the inline data and the inline
script. Everything is inline; stdlib only. One canvas animation of the three levels on one map of the United States:
  Screening  dots standing for the policy texts land on the map; the candidates stay lit
  Estimates  one candidate becomes a design, the states of the nurse licensure compact turn on in the years they joined,
             an estimate rises and the re-estimated papers fill a grid by result
  Explore    the camera pulls back: the nurse-compact map is one tile of a wall of state-policy maps; a small example (four laws
             whose studies measure traffic deaths) and a broad example (the whole wall) run on one clock, years after the first state
The animation code is assets/motion_v14_{common,scene,stage}.js (the v14 sources of docs/plans/motion/v14/src, adapted: the paper
count and result groups come from the site's loaders, one language per page, play once and hold the last frame); the data is
data/motion_v14_data.json (docs/plans/motion/v14/build/data14.js as JSON without the traffic-rate and lead blocks).
Numbers: screening totals and the paper counts are passed in or read from the loaders of the pages that print them; the wall,
the clock and the traffic example are data (SPID v1.2 and the 8 verified policies of the Home map), never typed in the script.
"""
from html import escape
import json
from pathlib import Path

from scripts.viz_site import restructure_estimates as estimates

HERE = Path(__file__).resolve().parent
ASSETS = HERE / 'assets'
DATA_FILE = HERE / 'data' / 'motion_v14_data.json'
SCRIPTS = ('motion_v14_common.js', 'motion_v14_scene.js', 'motion_v14_stage.js')
GROUPS = ('number_matches', 'same_direction', 'not_comparable', 'opposite_sign')      # the Estimates page's four groups, in its order

TEXT = {
    'en': {'figure': 'Screening, Estimates and Explore', 'replay': 'Replay', 'pause': 'Pause', 'play': 'Play', 'result': '{label}: {n} {unit}',
           'aria': ('Animation on one map of the United States. Screening: dots standing for {texts} policy texts land on the country and the '
                    'candidates stay lit. Estimates: a design is shown for one policy, the nurse licensure compact, whose states turn blue in the '
                    'years they joined; an estimate rises and {papers} papers are re-estimated and fill a grid by result ({results}). '
                    'Explore: the camera pulls back, and the nurse-compact map is one of {tiles} small maps of US state policies (724 from a published '
                    'database of state policy adoptions and the 8 verified policies), in five bands by the year the first state adopted. A state is colored by '
                    'how many years after the first state it adopted. Both examples run on one clock, years after the first state, from 0 to 5. '
                    'A small example, one outcome: the maps of four laws whose published studies use traffic deaths lift out of the wall and fill in '
                    'the states that had adopted by each year; the states not yet adopting are the comparison group of each study. A broad example: '
                    'the whole wall runs on the same clock; the median number of states within 5 years of the first, band by band from the oldest '
                    'policies to the newest, is {med}. The last frame holds the whole wall.'),
           'sep': ', '},
    'zh': {'figure': '筛查、估计与探索', 'replay': '重播', 'pause': '暂停', 'play': '播放', 'result': '{n} 篇{label}',
           'aria': ('动画，在一张美国地图上。筛查：代表 {texts} 份政策文本的点落到各州，其中的候选记录亮着。估计：以护士执照协定为例展示一个研究设计，它的各州在加入的那一年变蓝；'
                    '升起一个估计，{papers} 篇论文被重新估计，按结果填满格子（{results}）。探索：镜头拉远，护士协定这张图是 {tiles} 张美国州政策小地图中的一张'
                    '（724 张来自公开的州政策采纳数据库，8 张是已核实的政策），按首个州采纳的年代分成五带。州的颜色表示它比首个州晚几年采纳。两个例子用同一把时钟：首个州之后第 0 年到第 5 年。'
                    '小的例子，一个结果变量：已发表研究都以交通死亡为结果的四项法律的地图从墙里浮出，每过一年补上那一年采纳的州；尚未采纳的州，就是每项研究用来比较的对照组。'
                    '大的例子：整面墙按同一把时钟走；首个州之后 5 年内采纳的州数，各年代带的中位数从最老的政策到最新的依次是 {med}。最后一帧停在整面墙上。'),
           'sep': '，'},
}


def _read(path):
    return path.read_text(encoding='utf-8')


def _json_text(value):
    """JSON safe inside a script element."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/').replace('<!--', '<\\!--')


def _only(script, lang):
    """The scene script holds both languages; a page ships the one it shows (the other block becomes empty)."""
    if lang == 'zh':
        start, end = script.index('/*EN:START*/'), script.index('/*ZH:START*/')
        return script[:start] + 'en: {},\n    ' + script[end:]
    start = script.index('/*ZH:START*/')
    end = script.index('\n  };', start)
    return script[:start] + 'zh: {}' + script[end:]


def _lang(value, lang):
    """Every {"en": ..., "zh": ...} pair of the data keeps the page's language only."""
    if isinstance(value, dict):
        if value and set(value) <= {'en', 'zh'} and 'en' in value:
            return value[lang]
        return {key: _lang(item, lang) for key, item in value.items()}
    if isinstance(value, list):
        return [_lang(item, lang) for item in value]
    return value


def _icons():
    return {'pause': '<svg class="mf-i-pause" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M5.5 4v8M10.5 4v8"/></svg>',
            'play': '<svg class="mf-i-play" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M5.8 3.8v8.4l6.4-4.2z"/></svg>',
            'replay': '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3.2 8a4.8 4.8 0 1 0 1.5-3.5M3 2.8v2.9h2.9"/></svg>'}


def counts(lang, given=None):
    """Screening totals (the Home staircase's figures) and the Estimates counts of the pinned snapshot."""
    snapshot = estimates.load_snapshot()
    public = estimates.public_counts(snapshot)
    names = estimates.category_labels(snapshot, lang)
    if tuple(public) != GROUPS or tuple(names) != GROUPS:
        raise ValueError('the figure draws the result groups of Estimates; Estimates now has ' + ', '.join(public))
    papers = estimates.attempted(snapshot)
    if sum(public[key] for key in GROUPS) != papers:
        raise ValueError('the three result groups do not add up to the papers re-estimated')
    given = given or {}
    if 'texts' not in given or 'candidates' not in given:
        raise ValueError('the figure needs the Screening totals (texts, candidates) from the Home staircase')
    return {'texts': given['texts'], 'cands': given['candidates'], 'papers': papers,
            'match': public['number_matches'], 'dir': public['same_direction'], 'nc': public['not_comparable'], 'os': public['opposite_sign'],
            'labels': [names[key] for key in GROUPS], 'names': names, 'public': public}


def render(lang='en', given=None):
    if lang not in TEXT:
        raise ValueError(lang)
    t = TEXT[lang]
    data = _lang(json.loads(_read(DATA_FILE)), lang)
    n = counts(lang, given)
    data['counts'].update(texts=n['texts'], cands=n['cands'], papers=n['papers'], match=n['match'], dir=n['dir'], nc=n['nc'], os=n['os'])
    for policy in data['policies']:
        policy.pop('en', None)
        policy.pop('zh', None)
    data['labels'] = n['labels']
    data['lang'] = lang
    med = ' · '.join(str(band['median']) for band in data['clock']['bands'])
    results = t['sep'].join(t['result'].format(label=n['names'][key], n=n['public'][key], unit='paper' if n['public'][key] == 1 else 'papers')
                            for key in GROUPS)
    aria = t['aria'].format(texts=f"{n['texts']:,}", papers=n['papers'], results=results, tiles=data['tiles']['n'], med=med)
    ui = {'pause': t['pause'], 'play': t['play']}
    ic = _icons()
    scripts = [_read(ASSETS / name) for name in SCRIPTS]
    scripts[1] = _only(scripts[1], lang)
    mount = ("(function(){var f=document.getElementById('motion-figure'),d=document.getElementById('mf-data');"
             "if(!f||!d||!window.NESM||!window.Path2D)return;"
             "try{var D=JSON.parse(d.textContent),u=JSON.parse(document.getElementById('mf-ui').textContent);"
             "f.classList.add('mf-js');NESM.mount(f,D,u);}catch(e){f.classList.remove('mf-js');if(window.console)console.error('motion figure: '+e.message);}})();")
    script = '\n'.join(scripts + [mount])
    if '</script' in script.lower():
        raise ValueError('the figure scripts must not contain a closing script tag')
    css = _read(ASSETS / 'motion_v14.css')
    return (
        f'<style>{css}</style>'
        f'<section class="mf-sec" aria-label="{escape(t["figure"])}">'
        f'<figure class="mf" id="motion-figure"><p class="mf-nojs">{escape(aria)}</p><div class="mf-stage">'
        f'<canvas class="mf-canvas" role="img" aria-label="{escape(aria)}"></canvas>'
        f'<div class="mf-ctl">'
        f'<button type="button" class="mf-btn" data-act="toggle" aria-pressed="false" aria-label="{escape(t["pause"])}" title="{escape(t["pause"])}">{ic["pause"]}{ic["play"]}</button>'
        f'<button type="button" class="mf-btn" data-act="replay" aria-label="{escape(t["replay"])}" title="{escape(t["replay"])}">{ic["replay"]}</button></div>'
        f'</div>'
        f'<script type="application/json" id="mf-ui">{_json_text(ui)}</script>'
        f'<script type="application/json" id="mf-data">{_json_text(data)}</script>'
        f'</figure></section>'
        f'<script>{script}</script>')
