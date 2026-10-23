"""Release gate for the Home figure of the fourteenth round (motion_figure_v14.py). check_v5.motion_figure_issues hands over to issues()
when the page carries this figure. Same spirit as the earlier gate: placement, numbers read from the loaders, a text alternative,
two 44 px buttons, nothing external, no Chinese on the English page, the shipped data equal to data/motion_v14_data.json."""
from html import unescape
import json
import re

from scripts.viz_site import motion_figure_v14 as figure
from scripts.viz_site import restructure_estimates as estimates

CJK = re.compile('[㐀-䶿一-鿿豈-﫿　-〿＀-￯]')
EXTERNAL = re.compile(r'https?://|//[a-z0-9.-]+\.[a-z]{2,}/|@import|url\(|<(?:link|iframe|img|video|audio|embed|object)\b|<script[^>]+src=|'
                      r'\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|\bimport\(|xmlns', re.I)


def region_of(home):
    """The figure as the Home page carries it: style block, section, script (markup string), else None."""
    at = home.find('id="motion-figure"')
    section = home.rfind('<section class="mf-sec"', 0, at) if at >= 0 else -1
    style = home.rfind('<style>', 0, section) if section >= 0 else -1
    ladder = home.find('class="nx-sec nx-ladder"')
    end = home.rfind('</script>', 0, ladder) + len('</script>') if ladder >= 0 else -1
    return home[style:end] if 0 <= style < section < end else None


def _strip_pairs(value, lang):
    return figure._lang(value, lang)


def issues(home, lang, screening, snapshot, nav_names):
    """Problems with the Home figure. `screening` is (texts, candidates) as Screening prints them (None when that is reported elsewhere);
    `nav_names` are the site's three level names in order."""
    if home.count('id="motion-figure"') != 1:
        return ['the Home page needs exactly one animated figure (id="motion-figure"), it has %d' % home.count('id="motion-figure"')]
    region = region_of(home)
    if region is None:
        return ['the animated figure must sit between the adoption map and the staircase, inside its section, style and script']
    out = []
    after_map, before_stairs = home.find('id="sm-root"'), home.find('class="nx-sec nx-ladder"')
    if not 0 <= after_map < home.find('id="motion-figure"') < before_stairs:
        out.append('the animated figure must come after the adoption map (id="sm-root") and before the staircase')
    if 'data-home-block' in region or region.count('class="mf-sec"') != 1:
        out.append('the animated figure is one section and carries no data-home-block')
    public = estimates.public_counts(snapshot)
    names = estimates.category_labels(snapshot, lang)
    papers = estimates.attempted(snapshot)
    # The data: what the canvas draws is what the loaders give; the maps, the clock and the traffic example are the data file's.
    shipped_m = re.search(r'<script type="application/json" id="mf-data">(.*?)</script>', region, re.S)
    try:
        shipped = json.loads(shipped_m[1]) if shipped_m else None
    except ValueError:
        shipped = None
    if not shipped:
        return out + ['the figure needs its data (id="mf-data")']
    counts = shipped.get('counts', {})
    wanted = {'papers': papers, 'match': public['number_matches'], 'dir': public['same_direction'], 'nc': public['not_comparable'], 'os': public['opposite_sign']}
    if any(counts.get(key) != value for key, value in wanted.items()) or shipped.get('labels') != [names[key] for key in figure.GROUPS]:
        out.append('the figure\'s result counts or group labels differ from the loaders of the Estimates page')
    if screening and (counts.get('texts'), counts.get('cands')) != (int(screening[0].replace(',', '')), int(screening[1].replace(',', ''))):
        out.append('the figure\'s Screening totals differ from the Screening page\'s count line')
    base = _strip_pairs(json.loads(figure.DATA_FILE.read_text(encoding='utf-8')), lang)
    for policy in base['policies']:
        policy.pop('en', None)
        policy.pop('zh', None)
    for key in ('map', 'tiles', 'clock', 'traffic', 'open', 'policies'):
        if shipped.get(key) != base.get(key):
            out.append('the figure\'s %s data differ from scripts/viz_site/data/motion_v14_data.json' % key)
    if shipped.get('lang') != lang:
        out.append('the figure\'s language is not the page\'s')
    # Text alternative: on the canvas, and the same words in the no-script paragraph.
    canvas = re.search(r'<canvas [^>]*>', region)
    label = unescape((re.search(r'aria-label="([^"]*)"', canvas[0]) or [None, ''])[1]) if canvas else ''
    nojs = re.search(r'<p class="mf-nojs">(.*?)</p>', region, re.S)
    if not canvas or 'role="img"' not in canvas[0] or not label:
        out.append('the figure\'s canvas must be an image with a text alternative')
    if not nojs or unescape(nojs[1]) != label:
        out.append('the figure needs the same text alternative as a paragraph for pages without script (class mf-nojs)')
    for number in ((screening[0] if screening else None), str(papers), str(shipped['tiles']['n'])):
        if number and number not in label:
            out.append('the canvas text alternative lacks the number ' + number)
    for key in figure.GROUPS:
        said = ('%s: %d %s' % (names[key], public[key], 'paper' if public[key] == 1 else 'papers')) if lang == 'en' else ('%d 篇%s' % (public[key], names[key]))
        if said not in label:
            out.append('the canvas text alternative must say "%s"' % said)
    if re.search(r'one candidate becomes|一个候选成为|一个成为研究设计', label + region):
        out.append('the figure says a screened candidate becomes the design shown; the design shown (the nurse licensure compact) is not a screened candidate')
    if not all(name in region for name in nav_names):
        out.append('the figure must name its three levels as the navigation does: ' + ', '.join(nav_names))
    # Controls.
    buttons = [dict(re.findall(r'([\w-]+)="([^"]*)"', tag)) for tag in re.findall(r'<button\b[^>]*class="mf-btn"[^>]*>', region)]
    toggle = [b for b in buttons if b.get('data-act') == 'toggle']
    replay = [b for b in buttons if b.get('data-act') == 'replay']
    if len(buttons) != 2 or len(toggle) != 1 or len(replay) != 1 or 'aria-pressed' not in toggle[0] or not all(b.get('aria-label') for b in buttons):
        out.append('the figure needs two labelled buttons: toggle (with aria-pressed) and replay')
    if not re.search(r'\.mf-btn\{[^}]*width:44px;height:44px', region):
        out.append('the figure\'s buttons must be 44 px')
    # Nothing external; English page without Chinese; one reduced-motion path in the script.
    out.extend('the figure loads or sends something: ' + match.group() for match in sorted(set(EXTERNAL.finditer(region)), key=lambda m: m.start())[:3])
    if lang == 'en' and CJK.search(region):
        out.append('the English figure holds Chinese text')
    if 'prefers-reduced-motion' not in region:
        out.append('the figure must honour prefers-reduced-motion (final frame at once)')
    return out
