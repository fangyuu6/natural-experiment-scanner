"""Presentation-only grouping and horizontal padding for stored DiD results."""
import re
import xml.etree.ElementTree as ET

NUMBER = r'-?\d+(?:\.\d+)?'


def _number(value):
    return format(float(value), '.6f').rstrip('0').rstrip('.') or '0'


def pad_event_svg(fragment, event_times):
    """Inset the recorded event domain by half a time unit at either end.

    The two responsive SVG layers retain every y coordinate, point, band,
    errorbar cap, accessible title and description. Gridlines keep full width.
    """
    times = list(event_times)
    if not times or min(times) == max(times):
        return fragment
    first, last = min(times), max(times)
    scale = (last - first) / (last - first + 1)
    inset = .5 / (last - first + 1)
    wrapper = ET.fromstring('<div>' + fragment + '</div>')
    for node in wrapper.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
    geometry, labels = list(wrapper)
    symbol = geometry.find('.//symbol')
    left, _, span, _ = map(float, symbol.attrib['viewBox'].split())

    def x(value):
        return _number(left + span * inset + (float(value) - left) * scale)

    for node in symbol.iter():
        classes = node.attrib.get('class', '').split()
        if 'we-grid' in classes or 'we-zero' in classes:
            continue
        for key in ('x', 'x1', 'x2', 'cx'):
            if key in node.attrib:
                node.set(key, x(node.attrib[key]))
        if 'points' in node.attrib:
            node.set('points', ' '.join(x(pair.split(',')[0]) + ',' + pair.split(',')[1]
                                       for pair in node.attrib['points'].split()))
        if 'd' in node.attrib:
            # The projection combines only M x y, H x and V y commands.
            node.set('d', re.sub(r'([MH])(' + NUMBER + ')',
                                lambda m: m[1] + x(m[2]), node.attrib['d']))
    for node in labels:
        if node.tag == 'circle':
            match = re.fullmatch(r'cx:calc\((' + NUMBER + r')% \+ (' + NUMBER + r')px\)', node.attrib['style'])
            fraction = inset + float(match[1]) / 100 * scale
            node.set('style', 'cx:calc(' + _number(fraction * 100) + '% + ' + _number(48 - 60 * fraction) + 'px)')
        elif node.tag == 'text' and node.attrib.get('x', '').endswith('%'):
            fraction = inset + float(node.attrib['x'][:-1]) / 100 * scale
            node.set('x', _number(fraction * 100) + '%')
            node.set('dx', _number(48 - 60 * fraction))
    # Recheck phone label clearance after the domain inset; marks stay intact.
    ticks = [node for node in labels if 'we-x-tick' in node.get('class', '').split()]
    chosen = [node for node in ticks if node.text == '0']
    def phone_x(node):
        return float(node.get('x')[:-1]) / 100 * 297.6 + float(node.get('dx'))
    for node in ticks:
        node.set('class', ' '.join(c for c in node.get('class').split() if c != 'we-x-narrow'))
    for node in chosen:
        node.set('class', node.get('class') + ' we-x-narrow')
    for node in sorted((n for n in ticks if n.text != '0'), key=lambda n: (abs(int(n.text.replace('−', '-'))), int(n.text.replace('−', '-')))):
        if all(abs(phone_x(node) - phone_x(other)) >=
               8 + (len(node.text) + len(other.text)) * 7.25 / 2 for other in chosen):
            chosen.append(node)
            node.set('class', node.get('class') + ' we-x-narrow')
        else:
            node.set('class', ' '.join(c for c in node.get('class').split() if c != 'we-x-narrow'))
    for root in (geometry, labels):
        root.set('xmlns', 'http://www.w3.org/2000/svg')
    return ''.join(ET.tostring(root, encoding='unicode', short_empty_elements=False)
                   for root in (geometry, labels))


def numeric_punctuation(html):
    """Use ASCII interval punctuation, leaving Chinese prose punctuation intact."""
    html = re.sub(r'［([−+\-\d][^［］<>]*)］',
                  lambda m: '[' + m[1].replace('，', ', ') + ']', html)
    html = re.sub(r'([\d%])((?:</?span\b[^>]*>)*)\s*(?=\[[−+\-\d])', r'\1 \2', html)
    return re.sub(r'(\[[−+\-\d][^\[\]<>]*\](?:</?span\b[^>]*>)*)\s*(?=[\u3400-\u9fffA-Za-z%])', r'\1 ', html)


def _group_outcomes(article):
    pattern = (r'<div class="we-readouts">\s*((?:<div class="we-readout">.*?</div>\s*)+)'
               r'(.*?)</div>\s*<figure class="we-figure"><div class="we-plot-panels">'
               r'(.*?)</div>(<figcaption>.*?</figcaption>)</figure>')

    def group(match):
        readouts = re.findall(r'<div class="we-readout">.*?</div>', match[1], re.S)
        plots = re.findall(r'<div class="we-plot">(?:<p>.*?</p>)?'
                           r'(<div class="we-chart">.*?</div>)</div>', match[3], re.S)
        # Retain the original structure when optional figures are incomplete.
        if len(readouts) != len(plots):
            return match[0]
        blocks = ''.join('<div class="we-outcome-block">' + readout +
                         '<div class="we-plot">' + plot + '</div></div>'
                         for readout, plot in zip(readouts, plots))
        return ('<figure class="we-figure"><div class="we-readouts we-outcome-panels">' +
                blocks + match[2] + '</div>' + match[4] + '</figure>')

    return re.sub(pattern, group, article, flags=re.S)


def render_case_page(selection, ctx, errs, template, *, services):
    """Consume WP1's base renderer without modifying its research inputs."""
    html = services['render_case_page'](selection, ctx, errs, template)
    # Remove the duplicate only when the introduction carries this inventory.
    stopped = len(selection['omitted']) + sum(not case['shown'] for case in selection['cases'])
    counts = [str(selection['prepared']), str(selection['shown']), str(stopped)]
    if re.findall(r'\d+', ctx['strings'].get('cases.intro_html', '')) == counts:
        html = re.sub(r'<p class="we-selection">.*?</p>', '', html, count=1, flags=re.S)
    domains = {}
    for example in selection['examples']:
        headlines = [r for r in example['runs'] if r['headline']]
        for index, row in enumerate(headlines):
            domains['event-' + example['id'] + '-' + str(index) + '-368'] = [p['event_time'] for p in row['events']]
    for case in selection['cases']:
        if not case['shown']:
            continue
        for row in case['primary_estimates'].values():
            points = case.get('figures', {}).get(row['id'], {}).get('points', [])
            domains['event-' + re.sub(r'[^A-Za-z0-9_.-]', '-', case['case_id'] + '-' + row['id'])] = [p['event_time'] for p in points]

    def pad(match):
        identifier = re.search(r'<svg id="([^"]+)"', match[0])[1]
        times = domains.get(identifier)
        return pad_event_svg(match[0], times) if times else match[0]

    html = re.sub(r'<svg\b[^>]*class="we-chart-geometry".*?</svg><svg\b.*?</svg>', pad, html, flags=re.S)
    html = re.sub(r'<article class="we-example".*?</article>',
                  lambda m: _group_outcomes(m[0]), html, flags=re.S)
    # Case titles belong below the appendix heading in the page hierarchy.
    html = re.sub(r'<h2( id="[^"]+-heading")>(.*?)</h2>',
                  r'<h3\1>\2</h3>', html, flags=re.S)
    html = re.sub(r'(<aside class="we-stopped"[^>]*><p>)(.*?)(：|: )',
                  r'\1<strong>\2</strong>\3', html, flags=re.S)
    return numeric_punctuation(html) if ctx['lang'] == 'zh' else html
