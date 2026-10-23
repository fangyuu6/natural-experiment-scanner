"""Pure inline chrome inclusion for viz and hand-maintained page consumers.

Original owners install all five NX slots; builders replace only their contents.
Body translation and API code never pass through this renderer.
"""
from html import escape
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SLOTS = ('style', 'header', 'nav', 'footer', 'motion')
ORIGINAL_ROUTES = ('policy_db', 'policy_groups', 'documentation')
NAV_ROUTES = ('screening', 'worked_examples', 'synthesize', 'documentation')
# Use the same header column on primary and secondary pages.
ALIGNED_HEADER_ROUTES = frozenset(('policy_db', 'screening', 'worked_examples', 'synthesize', 'documentation', 'policy_groups', 'all_records', 'adoption'))
SLOT_RX = re.compile(r'<!-- NX:(STYLE|HEADER|NAV|FOOTER|MOTION):START -->(.*?)<!-- NX:\1:END -->', re.S)
COMMENT = re.compile(r'<!--.*?-->\s*', re.S)
CSS_COMMENT = re.compile(r'/\*.*?\*/', re.S)


def url(page, lang):
    if page in ORIGINAL_ROUTES:
        return ('/zh/' if lang == 'zh' else '/') + page + '.html'
    return '/viz/' + lang + '/' + page + '.html'


def marked(slot, content=''):
    if slot not in SLOTS:
        raise ValueError('unknown chrome slot: ' + slot)
    name = slot.upper()
    return '<!-- NX:%s:START -->\n%s\n<!-- NX:%s:END -->' % (name, content.strip(), name)


def language_switch(lang, page, *, legacy=False):
    other = 'zh' if lang == 'en' else 'en'
    on_en, on_zh = (' class="on"', '') if lang == 'en' else ('', ' class="on"')
    label = 'Switch language · 切换语言'
    rendered = ('<a class="lang-switch" href="%s" hreflang="%s" lang="%s" title="%s" aria-label="%s">'
            '<span class="lang-pill"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.6 3.9 5.6 3.9 9s-1.3 6.4-3.9 9c-2.6-2.6-3.9-5.6-3.9-9S9.4 5.6 12 3z"/></svg>'
            '<span%s>EN</span><span class="lang-sep" aria-hidden="true">|</span><span%s>中</span></span></a>') % (
                url(page, other), 'zh-CN' if other == 'zh' else 'en',
                'zh-CN' if lang == 'zh' else 'en', label, label, on_en, on_zh)
    if legacy:
        # Pre-slot consumers retain their existing pill structure and CSS.
        rendered = rendered.replace('<span class="lang-pill">', '', 1)
        rendered = rendered.replace('<span class="lang-sep" aria-hidden="true">|</span>', '', 1)
        rendered = rendered.replace('</span></a>', '</a>', 1)
    return rendered


def nav_attributes(route, page):
    if route == page:
        return 'class="vt-btn vt-active" aria-current="page"'
    if route == 'worked_examples' and page == 'policy_exposure_records':
        return 'class="vt-btn vt-active" aria-current="true"'
    if route == 'screening' and page in ('policy_groups', 'all_records', 'funnel', 'adoption', 'design_opportunity'):
        return 'class="vt-btn vt-active" aria-current="true"'
    return 'class="vt-btn vt-link"'


def _render(text, lang, page, features):
    strings = json.loads((HERE / 'i18n' / ('common.' + lang + '.json')).read_text())
    text = COMMENT.sub('', text)
    text = re.sub(r'{{if:(github|examples|design_opportunity|policy_exposure|aligned_header|legacy_header)}}(.*?){{endif}}',
                  lambda m: m[2] if features.get(m[1], False) else '', text, flags=re.S)
    def replace(m):
        value = m[1]
        if value.startswith('t:'):
            return escape(strings[value[2:]], quote=True)
        if value.startswith('nav:'):
            return nav_attributes(value[4:], page)
        if value.startswith('css:'):
            return CSS_COMMENT.sub('', (HERE / 'design' / (value[4:] + '.css')).read_text()).strip()
        return {'lang_code': lang, 'lang_switch': language_switch(lang, page),
                'documentation_current': 'aria-current="page"' if page == 'documentation' else ''}[value]
    return re.sub(r'{{([^{}]+)}}', replace, text).strip()


def render_slots(lang, page, *, features=None):
    """Return unmarked slot bodies; no output writes or data reads beyond chrome."""
    if lang not in ('en', 'zh'):
        raise ValueError('unknown language: ' + lang)
    features = dict(features or {})
    features["aligned_header"] = page in ALIGNED_HEADER_ROUTES
    features["legacy_header"] = not features["aligned_header"]
    combined = (HERE / 'partials/header.html').read_text()
    # Source header and nav have an internal split; generated markers are separate.
    header, nav = combined.split('<!-- NX:NAV -->')
    bodies = {'header': header, 'nav': nav}
    for slot, filename in (('style', 'head_css'), ('footer', 'footer'), ('motion', 'motion')):
        bodies[slot] = (HERE / 'partials' / (filename + '.html')).read_text()
    return {slot: _render(body, lang, page, features) for slot, body in bodies.items()}


def detach_slots(text):
    """Exclude generated chrome from exact-pair translation and body integrity."""
    slots = {}
    def detach(m):
        slot = m[1].lower()
        if slot in slots:
            raise ValueError('duplicate NX chrome slot: ' + slot)
        slots[slot] = m[0]
        return '<!--@@NX-' + slot.upper() + '@@-->'
    core = SLOT_RX.sub(detach, text)
    if slots and set(slots) != set(SLOTS):
        raise ValueError('original chrome needs all five slots: ' + ', '.join(SLOTS))
    # Detect malformed markers rather than silently leaving a stale component.
    if re.search(r'<!-- NX:(?:STYLE|HEADER|NAV|FOOTER|MOTION):(?:START|END) -->', core):
        raise ValueError('unbalanced NX chrome slots')
    return core, slots


def restore_slots(core, slots, lang, page, *, features=None):
    if not slots:
        return core
    rendered = render_slots(lang, page, features=features)
    for slot in SLOTS:
        token = '<!--@@NX-' + slot.upper() + '@@-->'
        if core.count(token) != 1:
            raise ValueError('lost NX slot: ' + slot)
        core = core.replace(token, marked(slot, rendered[slot]))
    # Original consumers installed motion beside the header. Run it in the head before
    # any figure can paint, preserving one slot and the rest of the page byte-for-byte.
    if '</head>' in core:
        motion = marked('motion', rendered['motion'])
        if core.index(motion) > core.index('</head>'):
            core = core.replace(motion, '', 1).replace('</head>', motion + '\n</head>', 1)
    return core


def include_slots(text, lang, page, *, features=None):
    core, slots = detach_slots(text)
    return restore_slots(core, slots, lang, page, features=features)


# Exact retired chrome pairs only. Never exempt body strings or title/ID/API pairs.
LEGACY_CHROME_PAIRS = frozenset((
    '<h1 style="margin:0;">Natural Experiment Scanner</h1>',
    '>Visualizations</a>', '>Documentation</a>', '>Policy Table</div>', '>Policy Groups</a>',
    '<br>Author: Fangyu Liu · ', '<div class="brand">Natural Experiment Scanner</div>',
    'class="vt-btn vt-link">Policy Table</a>',
    '<a href="policy_db.html">Natural Experiment Scanner</a>',
    '<a href="/viz/en/overview.html">Visualizations</a>', '<br>Author: ',
    '>Documentation<', 'Open to any collaborations and PhD opportunities! ',
    '>Get in touch: fangyu@usc.edu</a>', '>Get in touch: fangyu@usc.edu<',
))


def body_pairs(pairs, core, slots):
    """Retire unused chrome pairs only for opted-in consumers; body checks stay strict."""
    if not slots:
        return pairs
    old_chrome = '\n'.join(slots.values())
    return [p for p in pairs if p['en'] in core or
            (p['en'] not in LEGACY_CHROME_PAIRS and p['en'] not in old_chrome)]
