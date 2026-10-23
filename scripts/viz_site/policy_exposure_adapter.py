"""Render the public L4 projection as static, escaped HTML; no data fetching.

Numeric ledger entries bind visible values to the input JSON. The adapter never
serializes provenance, private codings, arbitrary object fields or raw quotes.
"""
from __future__ import annotations

import math
import re
import calendar
import hashlib
import json
from pathlib import Path
from datetime import date
from html import escape, unescape
from string import Formatter
from urllib.parse import urlsplit


def get_path(data, path):
    value = data
    try:
        for key in path.split('.'):
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError) as exc:
        raise ValueError('missing JSON display fact: ' + path) from exc
    return value


def validate(data):
    clocks = data['descriptive']['three_clocks']
    cases = clocks['cases']
    designs = data['design']['did_cases']
    ids = [r['case_id'] for r in cases]
    if len(set(ids)) != len(ids) or set(ids) != {r['case_id'] for r in designs}:
        raise ValueError('clock cases and designs must match uniquely')
    total_n = total_changed = total_records = 0
    for row in cases:
        pair = row['signing_effective']
        n, changed, share = pair['denominator'], pair['changed_records'], pair['share']
        if n == 0:
            if changed is not None or share is not None:
                raise ValueError('unavailable signing pairs must use null, not zero')
        elif not isinstance(changed, int) or not 0 <= changed <= n or not math.isclose(share, changed / n):
            raise ValueError('inconsistent case numerator, denominator or share')
        if n > row['examples_page_window_records']:
            raise ValueError('comparable records exceed displayed window records')
        total_n += n
        total_changed += changed or 0
        total_records += row['examples_page_window_records']
    pair = clocks['signing_effective']
    if (total_n, total_changed, total_records) != (
        pair['denominator'], pair['changed_records'], clocks['denominators']['examples_page_window']['records']
    ) or total_n != clocks['denominators']['comparable']['records']:
        raise ValueError('aggregate denominators do not match the rows')
    if not math.isclose(pair['share'], total_changed / total_n):
        raise ValueError('inconsistent overall share')
    for row in designs:
        if row['period_unit'] not in ('calendar_year', 'fiscal_year', 'quarter'):
            raise ValueError('unknown period unit')
        if row['display_status'] not in ('shown', 'stopped'):
            raise ValueError('unknown display status')
        for lang, link in row['worked_example_links'].items():
            expected = f"/viz/{lang}/worked_examples.html#{row['case_id']}-heading"
            if link != expected:
                raise ValueError('unexpected worked-example anchor')
    for row in clocks['enforcement_examples']:
        if row['state'] == 'IN' and not row['scope_conditional']:
            raise ValueError('Indiana statewide interpretation must remain conditional')
    return data


class Renderer:
    def __init__(self, data, lang, strings):
        self.data = validate(data)
        self.lang, self.strings = lang, strings
        self.numbers = []
        self.designs = {r['case_id']: (i, r) for i, r in enumerate(data['design']['did_cases'])}
        self.clocks = data['descriptive']['three_clocks']
        self.psl = data['descriptive']['paid_sick_leave']

    def t(self, key, **slots):
        template = self.strings['lm.' + key]
        expected = {field for _, field, _, _ in Formatter().parse(template) if field}
        if expected != set(slots):
            raise ValueError(f'lm.{key}: expected slots {expected}, got {set(slots)}')
        return template.format(**slots)

    def num(self, path, fmt='raw', *, context=None):
        value = get_path(self.data, path)
        if fmt == 'len':
            rendered = str(len(value))
        elif fmt == 'quarter':
            encoded = str(value)
            rendered = self.t('quarter.value', year=encoded[:-1], quarter=encoded[-1])
        elif fmt.startswith('axis_year_'):
            rendered = str(int(str(value)[:4]) + int(fmt.removeprefix('axis_year_')))
        elif fmt == 'year':
            rendered = str(value)[:4]
        elif fmt == 'month_number':
            month = int(str(value)[5:7])
            rendered = calendar.month_abbr[month] if self.lang == 'en' else str(month)
        elif fmt == 'month':
            month = int(str(value)[5:7])
            rendered = calendar.month_abbr[month] if self.lang == 'en' else str(month) + ' 月'
        elif fmt == 'short_date':
            day = date.fromisoformat(value)
            rendered = f'{day.day} {calendar.month_abbr[day.month]} {day.year}' if self.lang == 'en' else value
        elif fmt == 'score':
            rendered = f'{value:.3f}'
        elif fmt == 'pct':
            rendered = '0%' if value == 0 else f'{value * 100:.1f}%'
        elif fmt == 'title_number':
            rendered = re.search(r'\d+', value).group(0)
        elif fmt == 'act_number':
            rendered = re.search(r'PA(\d+)', value).group(1)
        elif fmt == 'name_year':
            rendered = re.search(r'(?<!\d)(\d{4})(?!\d)', value).group(1)
        else:
            rendered = str(value)
        self.numbers.append({'context': context or path, 'value': rendered, 'json_path': path, 'format': fmt})
        return rendered

    def text(self, key, **slots):
        return escape(self.t(key, **slots))

    def html_num(self, path, fmt='raw', *, context=None):
        """Escape a display value exactly at the HTML-text boundary."""
        return escape(self.num(path, fmt, context=context))

    def source(self, urls, label=None, *, title=None):
        safe = []
        for raw in urls:
            for value in raw.split(';'):
                url = value.strip()
                parts = urlsplit(url)
                if parts.scheme not in ('http', 'https') or not parts.netloc or parts.username or parts.password:
                    raise ValueError('unsafe source URL')
                if url not in safe:
                    safe.append(url)
        links, names = [], {}
        for i, url in enumerate(safe):
            parts = urlsplit(url)
            name = title if i == 0 and title else parts.netloc.removeprefix('www.')
            names[name] = names.get(name, 0) + 1
            if names[name] > 1:
                name += ' ' + str(names[name])
            accessible = self.t('source') + ': ' + parts.netloc.removeprefix('www.')
            title_class = ' class="lm-source-title"' if i == 0 and title else ''
            visible_name = escape(name)
            links.append(f'<a{title_class} href="{escape(url, quote=True)}" target="_blank" rel="noopener" aria-label="{escape(accessible, quote=True)}">{visible_name}</a>')
        return '<span class="lm-sources">' + '<span class="lm-source-sep" aria-hidden="true"> · </span>'.join(links) + '</span>' if links else ''

    def small(self, text):
        return '<span class="lm-small">' + text + '</span>'

    def table(self, headers, rows, *, caption=None, classes='', anchor=None, caption_sources=''):
        def cell_class(col, header):
            if 'lm-clock-table' in classes and col >= 3:
                return ' class="lm-clock-num"'
            return ' class="lm-score"' if header == self.t('score') else ''
        compact_columns = {'nurse-corridors': {1}, 'agency-responsibility': {1},
                           'legal-status': {1}, 'policy-lifecycle': {1}, 'frozen-predictions': {1}}
        head = ''.join('<th scope="col"' + cell_class(col, h) + '>' + escape(h) + '</th>' for col, h in enumerate(headers))
        body = ''.join('<tr>' + ''.join(
            '<td data-label="' + (escape(h, quote=True) if col and col not in compact_columns.get(anchor, set()) else '') + '"' + (' class="lm-empty"' if not v and h == self.t('event.scope') else cell_class(col, h)) + f'><span class="lm-value">{v}</span></td>' for col, (h, v) in enumerate(zip(headers, row))
        ) + '</tr>' for row in rows)
        mobile_heading = '<span class="lm-mobile-heading">' + escape(headers[1]) + '</span>' if anchor == 'nurse-corridors' else ''
        cap = '<caption>' + escape(caption) + mobile_heading + (self.small(caption_sources) if caption_sources else '') + '</caption>' if caption else ''
        identity = ' id="' + escape(anchor, quote=True) + '"' if anchor else ''
        return f'<table{identity} class="lm-table {classes}">{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    def period(self, design, path):
        unit = design['period_unit']
        if unit == 'fiscal_year':
            month = self.num(path + '.fiscal_start_month')
            return self.t('period.fiscal_year', month=calendar.month_name[int(month)] if self.lang == 'en' else month)
        return self.t('period.' + unit)

    def main_figure(self):
        ordered = sorted(enumerate(self.clocks['cases']), key=lambda ir: (
            ir[1]['signing_effective']['share'] is None,
            -(ir[1]['signing_effective']['share'] or 0),
            -ir[1]['signing_effective']['denominator'], ir[1]['case_id']))
        out = []
        for i, row in ordered:
            cid = row['case_id']
            j, design = self.designs[cid]
            base = f'descriptive.three_clocks.cases.{i}.signing_effective'
            notes = []
            if design['display_status'] == 'stopped':
                notes.append(self.t('stopped'))
            if design['period_unit'] != 'calendar_year':
                notes.append(self.period(design, f'design.did_cases.{j}'))
            for k, gap in enumerate(self.clocks['enforcement_examples']):
                if gap['case_id'] == cid:
                    p = f'descriptive.three_clocks.enforcement_examples.{k}'
                    slots = dict(state=gap['state'], effective=self.num(p + '.effective_first_period'), enforceable=self.num(p + '.enforceable_first_period'))
                    if gap['scope_conditional']:
                        slots.pop('effective')
                        slots['alternative'] = self.num(p + '.alternative_first_period')
                    notes.append(self.t('gap.note' if gap['scope_conditional'] else 'gap.simple', **slots))
            note = ''.join('<span class="lm-note">' + re.sub(r'MN \d{4} → \d{4}', lambda m: '<span class="lm-nw">' + m.group(0) + '</span>', escape(n)) + '</span>' for n in notes)
            label = self.strings.get('lm.case.' + cid, design['label'][self.lang])
            missing = row['signing_effective']['share'] is None
            if missing:
                track, pct, count = '', '—', '—'
            else:
                share = row['signing_effective']['share']
                fill = f'<span class="lm-fill" style="width:{share * 100:.6f}%"></span>' if share else ''
                track = '<span class="lm-track" aria-hidden="true">' + fill + '</span>'
                pct = self.html_num(base + '.share', 'pct')
                count = self.html_num(base + '.changed_records') + '/' + self.html_num(base + '.denominator')
            out.append(f'<li><a class="lm-row{" lm-missing" if missing else ""}" data-case="{escape(cid)}" href="{escape(design["worked_example_links"][self.lang], quote=True)}">'
                       f'<span class="lm-name">{escape(label)}{note}</span>{track}<span class="lm-pct">{pct}</span><span class="lm-count">{count}</span></a></li>')
        return '<figure><div class="lm-frame"><div class="lm-hd"><span>' + self.text('policy') + '</span><span class="lm-share-heading">' + self.text('share.changed') + '</span><span class="lm-count-heading">' + self.text('share.comparable') + '</span></div><ol class="lm-rows" aria-label="' + self.text('figure.label') + '">' + ''.join(out) + '</ol></div><figcaption class="lm-cap" data-prose="figure.caption">' + self.text('figure.caption') + '</figcaption></figure>'

    def event_date(self, event, path):
        if event['date'] is None:
            return self.text('not.stated')
        date = escape(self.num(path + '.date'))
        if event['event_id'] == 'W2V-056':
            return self.text('date.by', date=self.num(path + '.date')) + self.small(self.text('date.bound'))
        precision = event['precision']
        if precision not in (None, '', 'day'):
            key = {'year': 'date.year', 'month': 'date.month', 'month (inferred)': 'date.month.inferred'}.get(precision)
            note = self.text(key) if key else escape(precision)
            date += self.small(note)
        return date

    def event_scope(self, gap, event):
        category = event['scope_category']
        if category == 'specific plaintiffs/defendants only':
            key = 'mn.partial.scope' if gap['state'] == 'MN' else 'in.order.scope'
        elif category == 'whole law':
            key = 'mn.law.scope' if gap['state'] == 'MN' else 'in.law.scope'
        else:
            key = 'mn.law.scope' if gap['state'] == 'MN' else 'in.restore.scope'
        return self.text(key)

    def timeline(self, gap, path):
        effective = gap['effective_date']
        if effective is None:
            return ''
        signing = gap['signing_date']
        start = date(int((signing or effective)[:4]), 1, 1)
        end = date(start.year + 3, 1, 1)
        cohort_end = date(gap['enforceable_first_period'], 1, 1)
        length = 36
        if not start < cohort_end <= end:
            raise ValueError('cohort endpoint must fit the three-year timeline')
        def position(value):
            observed = date.fromisoformat(value)
            months = (observed.year - start.year) * 12 + observed.month - 1
            months += (observed.day - 1) / calendar.monthrange(observed.year, observed.month)[1]
            return 100 * months / length
        def span(lo, hi, kind):
            return f'<span class="lm-span {kind}" style="left:{lo:.6f}%;width:{max(0, hi-lo):.6f}%"></span>'
        effective_pos = position(effective)
        restored = next(((j, e) for j, e in enumerate(gap['events']) if e['kind'] == 'resumed_enforcement' and e['date'] and e['precision'] == 'day'), None)
        struck = next(((j, e) for j, e in enumerate(gap['events']) if e['kind'] == 'struck' and e['date']), None)
        desktop = f'<div class="lm-timeline" data-axis-start="{start.isoformat()}" data-axis-end="{end.isoformat()}" aria-hidden="true"><span class="lm-axis"></span>'
        mobile = []
        # Month labels are compact; exact observed days stay in the records table.
        def month_label(value_path):
            return self.t('month.year', year=self.num(value_path, 'year'), month=self.num(value_path, 'month_number'))
        if signing and abs(effective_pos-position(signing)) < 3:
            first_label = self.text('signed.effective')
            first_date = self.t('signed.effective.months', year=self.num(path + '.effective_date', 'year'),
                                sign_month=self.num(path + '.signing_date', 'month_number'),
                                effective_month=self.num(path + '.effective_date', 'month_number'))
        else:
            first_label = self.text('clock.effective')
            first_date = month_label(path + '.effective_date')
        def mobile_date(value_path):
            if self.lang == 'zh':
                return '<span>' + self.html_num(value_path, 'year') + ' 年</span><span>' + self.html_num(value_path, 'month_number') + ' 月</span>'
            return escape(month_label(value_path))
        if gap['state'] == 'MN' and signing:
            mobile.append('<li><span class="lm-date">' + mobile_date(path + '.signing_date') + '</span><span>' + self.text('signed') + '</span></li>')
        mobile.append('<li><span class="lm-date">' + mobile_date(path + '.effective_date') + '</span><span>' + self.text('clock.effective') + '</span></li>')
        def marker(pos, label, value, classes=''):
            return f'<span class="lm-mark" style="left:{pos:.6f}%"></span><span class="lm-event {classes}" style="left:{pos:.6f}%">{label}<span>{escape(value)}</span></span>'
        desktop += marker(effective_pos, first_label, first_date, 'lm-r' if gap['state'] == 'IN' else '')
        bounds = gap['injunction_date_bounds']
        gap_start = None
        if struck and restored:
            j, event = struck
            if bounds:
                if not bounds['bounds_are_not_observed_days']:
                    raise ValueError('uncertain order bounds must not be observed days')
                lo, hi = position(bounds['earliest']), position(bounds['latest'])
                desktop += span(effective_pos, lo, '') + span(lo, hi, 'lm-unknown')
                gap_start = hi
                year = self.num(path + '.injunction_date_bounds.earliest', 'year')
                order_left = (lo + hi) / 2
                desktop += f'<span class="lm-event lm-order" style="left:{order_left:.6f}%">' + self.text('order.year', year=year) + '<span>' + self.text('date.day.unknown') + '</span></span>'
                mobile.append('<li class="lm-off"><span class="lm-date">' + '<span>' + escape(year) + '</span>' + self.small(self.text('date.day.unknown')) + '</span><span>' + self.text('order.year', year=year) + '</span></li>')
            elif event['precision'] == 'day':
                gap_start = position(event['date'])
                desktop += span(effective_pos, gap_start, '')
                desktop += marker(gap_start, self.text('struck.down'), month_label(path + f'.events.{j}.date'))
                mobile.append('<li class="lm-off"><span class="lm-date">' + mobile_date(path + f'.events.{j}.date') + '</span><span>' + self.text('struck.down') + '</span></li>')
            k, resumed = restored
            restored_pos = position(resumed['date'])
            if gap_start is not None:
                desktop += span(gap_start, restored_pos, 'lm-off')
                if gap['scope_conditional']:
                    desktop += f'<span class="lm-conditional" style="left:{(gap_start+restored_pos)/2:.6f}%">' + self.text('conditional') + '</span>'
            desktop += span(restored_pos, position(cohort_end.isoformat()), '')
            desktop += marker(restored_pos, self.text('event.resumed_enforcement'), month_label(path + f'.events.{k}.date'), 'lm-r lm-label-high')
            mobile.append('<li><span class="lm-date">' + mobile_date(path + f'.events.{k}.date') + '</span><span>' + self.text('event.resumed_enforcement') + (self.small(self.text('conditional')) if gap['scope_conditional'] else '') + '</span></li>')
        # Equal month spacing uses a three-year axis derived from the source start.
        # Tick years are display transformations, never invented legal-event dates.
        axis_path = path + ('.signing_date' if signing else '.effective_date')
        for offset in range(4):
            tick_year = self.num(axis_path, f'axis_year_{offset}', context='timeline axis year')
            pos = 100 * offset / 3
            edge = ' lm-t0' if offset == 0 else ' lm-t1' if offset == 3 else ''
            desktop += f'<span class="lm-tick{edge}" style="left:{pos:.6f}%">{escape(tick_year)}</span>'
        return desktop + '</div><ol class="lm-milestones">' + ''.join(mobile) + '</ol>'

    def gaps(self):
        panels = []
        for i, gap in enumerate(self.clocks['enforcement_examples']):
            key = gap['state'].lower()
            p = f'descriptive.three_clocks.enforcement_examples.{i}'
            alternative = self.num(p + '.alternative_first_period')
            enforceable = self.num(p + '.enforceable_first_period')
            if gap['scope_conditional']:
                readout = escape(alternative) + ' <span>' + self.text('statewide.reading', enforceable=enforceable) + '</span>'
                caption_slots = dict(alternative=alternative, enforceable=enforceable)
            else:
                resumed_index = next(j for j, event in enumerate(gap['events']) if event['kind'] == 'resumed_enforcement')
                caption_slots = dict(enforceable=enforceable, alternative=alternative,
                                     restored_year=self.num(p + f'.events.{resumed_index}.date', 'year'))
                readout = self.html_num(p + '.effective_first_period') + ' → ' + escape(enforceable) + ' <span>' + self.text('gap.reading') + '</span>'
            timeline = self.timeline(gap, p)
            panels.append('<div class="lm-tl"><h3 data-prose="' + key + '.title">' + self.text(key + '.title') + '</h3><p class="lm-readout">' + readout + '</p>' + timeline + '<p class="lm-cap" data-prose="' + key + '.caption">' + self.text(key + '.caption', **caption_slots) + '</p></div>')
        return '<section class="lm-block"><h2 data-prose="gaps.title">' + self.text('gaps.title') + '</h2><div class="lm-gaps lm-frame">' + ''.join(panels) + '</div></section>'

    def enforcement_events(self):
        # Jurisdiction subheads replace repeated state cells. Repeated scope is
        # omitted only within a jurisdiction; changes remain attached to events.
        groups = []
        for i, gap in enumerate(self.clocks['enforcement_examples']):
            p = f'descriptive.three_clocks.enforcement_examples.{i}'
            state = gap['state']
            records = []
            if gap['signing_date'] is not None and not any(e['kind'] == 'enacted' and e['date'] == gap['signing_date'] for e in gap['events']):
                records.append([self.text('clock.signing'), escape(self.num(p + '.signing_date')), self.text(state.lower() + '.law.scope'), self.source(gap['date_source_urls'] + gap['source_urls'])])
            if gap['effective_date'] is not None and not any(e['kind'] == 'effective' and e['date'] == gap['effective_date'] for e in gap['events']):
                records.append([self.text('clock.effective'), escape(self.num(p + '.effective_date')), self.text(state.lower() + '.law.scope'), self.source(gap['date_source_urls'] + gap['source_urls'])])
            for j, event in enumerate(gap['events']):
                scope = self.text('mn.retroactivity.scope') if event['event_id'] in ('W2V-041', 'W2V-042') else self.event_scope(gap, event)
                records.append([self.text('event.' + event['kind']), self.event_date(event, p + f'.events.{j}'), scope, self.source(['https://www.revisor.mn.gov/laws/2003/0/28/'] if event['event_id'] == 'W2V-036' else event['source_urls'])])
            prior = None
            for record in records:
                scope = record[2]
                if scope == prior:
                    record[2] = ''
                prior = scope
            table = self.table([self.t(k) for k in ('event', 'date', 'event.scope', 'source')], records)
            body = table.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
            groups.append('<tbody><tr class="lm-jurisdiction"><th scope="rowgroup" colspan="4">' + escape(state) + '</th></tr>' + body + '</tbody>')
        headers = ''.join('<th scope="col">' + self.text(k) + '</th>' for k in ('event', 'date', 'event.scope', 'source'))
        return '<table id="enforcement-events" class="lm-table"><caption>' + self.text('evidence.events.caption') + '</caption><thead><tr>' + headers + '</tr></thead>' + ''.join(groups) + '</table>'

    def signing_effective_records(self, *, changed_only=False):
        """Expose the existing numerator and denominator without rerunning DiD.

        Reuse the exporter's sample selector with the displayed contract windows.
        Only public dates, cohorts, jurisdiction and source URLs reach the page.
        """
        from scripts.l4.page.export_policy_exposure import Inputs, window_clock_records, source_urls
        inputs = Inputs(Path(__file__).resolve().parents[2])
        units = inputs.csv('data/l4/clock_audit/unit_clocks.csv')
        records = []
        for comparison in self.clocks['cases']:
            cid = comparison['case_id']
            _, design = self.designs[cid]
            window = design['examples_page_window']
            if design['period_unit'] == 'quarter':
                periods = {y * 10 + q for y in range(window['start'] // 10, window['end'] // 10 + 1)
                           for q in range(1, 5) if window['start'] <= y * 10 + q <= window['end']}
            else:
                periods = set(range(window['start'], window['end'] + 1))
            selected = window_clock_records(inputs, cid, window, periods, units)
            paired = [u for u in selected if u['first_treated_period_signing']]
            changed = sum(int(u['first_treated_period_signing']) != int(u['first_treated_period_effective']) for u in paired)
            expected = comparison['signing_effective']
            if len(paired) != expected['denominator'] or changed != (expected['changed_records'] or 0):
                raise ValueError('Signing/effective trace disagrees with display contract: ' + cid)
            treatments = inputs.csv(f'data/did_samples/{cid}/treatment_table.csv')
            for u in paired:
                signing, effective = u['first_treated_period_signing'], u['first_treated_period_effective']
                if changed_only and signing == effective:
                    continue
                matches = [r for r in treatments if u['unit'] in (r.get('abbr'), r.get('unit'), r.get('state_abbr'))]
                urls = source_urls(json.loads(u['legal_source_urls']) + [u['registered_reference']] +
                                   [r.get(k, '') for r in matches for k in ('source_urls','source_url','source1_url','source2_url','source3_url')])
                label = self.strings.get('lm.case.' + cid, design['label'][self.lang])
                def period(value):
                    return self.t('quarter.value', year=value[:-1], quarter=value[-1]) if design['period_unit'] == 'quarter' else ('FY' if design['period_unit'] == 'fiscal_year' else '') + value
                if changed_only:
                    value = ('<span class="we-clock-period"><span>' + escape(period(effective))
                             + '</span> <span>→ ' + escape(period(signing)) + '</span></span>')
                    records.append([escape(u['unit']), escape(label), value, self.source(urls)])
                    continue
                difference = self.text('trace.changed') if signing != effective else '—'
                value = escape(period(signing)) + ' → ' + escape(period(effective))
                if signing != effective:
                    value = '<span class="lm-changed">' + value + '</span>'
                records.append([escape(label) + self.small(escape(u['unit'])), escape(u['signing_date']), escape(u['effective_date']), value, difference, self.source(urls)])
        for path, content in inputs.data.items():
            expected = self.data['provenance']['input_sha256'].get(path)
            if expected is None or hashlib.sha256(content).hexdigest() != expected:
                raise ValueError('Signing/effective trace source snapshot changed: ' + path)
        inputs.verify_unchanged()
        if changed_only:
            if len(records) != self.clocks['signing_effective']['changed_records']:
                raise ValueError('Changed timing records disagree with display contract')
            return self.table([self.t('jurisdiction'), self.t('policy'),
                               self.t('clock.effective') + ' → ' + self.t('clock.signing'), self.t('source')],
                              records, classes='we-timing-table')
        # Stable partition preserves the case/state order within both groups.
        records.sort(key=lambda row: 'class="lm-changed"' not in row[3])
        return self.table([self.t(k) for k in ('policy','clock.signing','clock.effective','trace.first_period','trace.change','source')], records,
                          caption=self.t('trace.caption', changed=self.num('descriptive.three_clocks.signing_effective.changed_records'), comparable=self.num('descriptive.three_clocks.signing_effective.denominator')),
                          anchor='signing-effective-records')

    def clause_value(self, clause, path, field):
        if clause['status'] != 'coded':
            return self.text('unresolved' if clause['status'] == 'unresolved' else 'not.stated')
        result = []
        seen_accrual = []
        for i, value in enumerate(clause['value']):
            if field == 'accrual_rate' and self.data_path_state(path) == 'OR':
                ratio = value['hours_earned'] / value['hours_worked']
                if any(scope == value['scope'] and math.isclose(ratio, seen_ratio) for scope, seen_ratio in seen_accrual):
                    continue
                seen_accrual.append((value['scope'], ratio))
            p = path + f'.value.{i}'
            if field == 'accrual_rate':
                term = self.text('accrual', earned=self.num(p + '.hours_earned'), worked=self.num(p + '.hours_worked'))
            elif field == 'annual_use_cap':
                term = self.text('hours', hours=self.num(p + '.hours')) if value['hours'] is not None else self.text('not.stated')
            elif field == 'carryover':
                term = self.text('hours', hours=self.num(p + '.hours')) if value['hours'] is not None else self.text('carryover.required' if value['rule'] == 'required' else 'not.required' if value['rule'] == 'not_required' else value['rule'])
            else:
                term = self.text('paid.' + value['paid_leave'])
            scope = value['scope']
            lo, hi = scope['min_employees'], scope['max_employees']
            qualifier = ''
            if hi not in (None, 'unbounded'):
                qualifier = self.text('scope.range', min=self.num(p + '.scope.min_employees'), max=self.num(p + '.scope.max_employees'))
            elif lo is not None:
                qualifier = self.text('scope.min', min=self.num(p + '.scope.min_employees'))
            if scope['employee_class'] not in ('all', None):
                qualifier += (' · ' if qualifier else '') + self.text('or.geography' if self.data_path_state(path) == 'OR' and scope['employee_class'] == 'other_condition' else 'other_condition')
            if field == 'employer_coverage' and self.data_path_state(path) == 'OR' and value['paid_leave'] == 'not_required' and scope['employee_class'] in ('all', None) and lo == 0 and hi == 9:
                qualifier = self.text('scope.range.compact', min=self.num(p + '.scope.min_employees'), max=self.num(p + '.scope.max_employees')) + ' · ' + self.text('or.no.geography')
            result.append(term + (self.small(qualifier) if qualifier else ''))
        return '<br>'.join(result)

    def data_path_state(self, path):
        row_path = path.split('.clauses.')[0]
        return get_path(self.data, row_path)['state']

    def baseline_label(self, row, path):
        year = self.num(path + '.adoption_date', 'year')
        if row['state'] == 'MI':
            return self.text('baseline.mi', year=year, bill=self.num(path + '.version_label.en', 'act_number'))
        return self.text('original', year=year)

    def dc_cap(self, path):
        base = path + '.audited_scope.annual_use_day_tiers.'
        slots = {}
        for i, prefix in enumerate(('high', 'mid', 'low')):
            slots[prefix + '_days'] = self.num(base + f'{i}.days')
            slots[prefix + '_min'] = self.num(base + f'{i}.min_employees')
            if prefix != 'high':
                slots[prefix + '_max'] = self.num(base + f'{i}.max_employees')
        return self.text('dc.cap', **slots) + ' ' + self.text('dc.more')

    def featured_clauses(self):
        rows = []
        for sid in self.psl['featured_statute_ids']:
            i, row = next((i, r) for i, r in enumerate(self.psl['clause_rows']) if r['statute_id'] == sid)
            p = f'descriptive.paid_sick_leave.clause_rows.{i}'
            clauses = row['clauses']
            label = escape(row['state']) + self.small(self.baseline_label(row, p))
            accrual = self.clause_value(clauses['accrual_rate'], p + '.clauses.accrual_rate', 'accrual_rate')
            if sid in ('CA_enacted', 'AZ_enacted'):
                accrual = self.text('accrual.minimum', earned=self.num(p + '.clauses.accrual_rate.value.0.hours_earned'), worked=self.num(p + '.clauses.accrual_rate.value.0.hours_worked'))
            if sid == 'CA_enacted':
                # Audited source wording can extend the public projection without
                # changing a disputed matrix field. Missing fields fail explicitly.
                days_path = p + '.audited_scope.use_limit_days'
                cap = self.text('ca.cap', hours=self.num(p + '.audited_scope.use_limit_hours'), days=self.num(days_path))
            elif sid == 'AZ_enacted':
                cap = self.text('az.cap', small=self.num(p + '.clauses.annual_use_cap.value.0.hours'), large=self.num(p + '.clauses.annual_use_cap.value.1.hours'), threshold=self.num(p + '.clauses.annual_use_cap.value.1.scope.min_employees'))
            else:
                cap = self.text('ct.cap', hours=self.num(p + '.clauses.annual_use_cap.value.0.hours'))
                if row['audited_scope']['worker_scope'] != 'service_workers':
                    raise ValueError('unsupported audited CT worker scope')
                cap += self.small(self.text('ct.scope', threshold=self.num(p + '.audited_scope.min_employees')))
            rows.append([label, accrual, cap])
        return self.table([self.t('law'), self.t('accrual.header'), self.t('cap.header')], rows, classes='lm-clauses')

    def earlier_name(self, row, path):
        earlier = row['closest_earlier_text']
        if earlier['id'].startswith('MODEL_ABB_2016'):
            return self.text('abb', year=self.num(path + '.name', 'name_year'))
        if earlier['id'].startswith('SF_PSLO_2006'):
            return self.text('sf', year=self.num(path + '.id', 'name_year'))
        for number in re.findall(r'\d+(?:\.\d+)?', earlier['name']):
            self.numbers.append({'context':'earlier text title','value':number,'json_path':path+'.name','format':'in_title'})
        return escape(earlier['name'])

    def lineage_rows(self, featured=False):
        rows = []
        selected = self.psl['lineage']['featured_statute_ids'] if featured else [r['statute_id'] for r in self.psl['lineage']['rows']]
        for sid in selected:
            i, row = next((i, r) for i, r in enumerate(self.psl['lineage']['rows']) if r['statute_id'] == sid)
            p = f'descriptive.paid_sick_leave.lineage.rows.{i}.closest_earlier_text'
            earlier = row['closest_earlier_text']
            name, score, tier = '—', '—', '—'
            if earlier is not None:
                tier = self.text('tier.' + earlier['evidence_tier'])
                name = self.earlier_name(row, p)
                if not featured:
                    name = self.source(earlier['source_urls'], unescape(name), title=unescape(name)) or name
                score = self.html_num(p + '.similarity_score', 'score')
            elif row['temporal_status'] != 'resolved':
                name += self.small(self.text('lineage.' + row['temporal_status']))
            if row['target_text_date']['status'] == 'disputed' and row['temporal_status'] != 'resolved':
                name += self.small(self.text('lineage.date.disputed'))
            for j, comparison in enumerate(row['retained_comparisons']):
                cp = f'descriptive.paid_sick_leave.lineage.rows.{i}.retained_comparisons.{j}'
                note = self.text('lineage.retained.' + comparison['date_order'],
                                 score=self.num(cp + '.similarity_score', 'score'),
                                 year=self.num(cp + '.id', 'name_year'))
                # The retained result describes overlap, never a directional precursor.
                name += self.small(note + self.source(comparison['source_urls'], comparison['name']))
            if row['official_comparison_documents']:
                tier += self.small(self.text('document' if sid == 'DC_enacted' else 'official'))
            law = escape(sid.split('_')[0])
            if sid == 'MI_enacted':
                law += self.small(escape(row['version_label'][self.lang]))
            result = [law, name, score, tier]
            if not featured:
                # Witness statements are never promoted to committee conclusions.
                doc = self.source([d['source_url'] for d in row['official_comparison_documents']]) or '—'
                if sid == 'DC_enacted':
                    dp = 'descriptive.paid_sick_leave.lineage.documentary_pair_validation'
                    j, pair = next((j, r) for j, r in enumerate(self.psl['lineage']['documentary_pair_validation']) if r['target_id'].startswith('DC_'))
                    doc = self.text('dc.document') + self.small(self.text('dc.missed', rank=self.num(dp + f'.{j}.rank'), score=self.num(dp + f'.{j}.score', 'score'), pool=self.num(dp + f'.{j}.candidate_pool_size'))) + self.small(doc)
                elif sid == 'CA_enacted':
                    doc = self.text('ca.document') + self.small(doc)
                result.append(doc)
            rows.append(result)
        return rows

    def evidence(self):
        # Eight existing objects remain reachable, with one result/source link each.
        anchors = {'three_clocks': 'enforcement-events', 'nurse_neighbor_channels': 'nurse-corridors',
                   'enforcement_responsibility': 'agency-responsibility', 'policy_lifecycle': 'policy-lifecycle',
                   'frozen_predictions': 'frozen-predictions', 'selected_legal_status': 'legal-status'}
        blocks = []
        for key, slots, urls in self.evidence_items():
            extra = ''
            fact = self.text('index.' + key + '.fact', **slots) if key != 'three_clocks' else ''
            if key == 'three_clocks':
                base = 'descriptive.three_clocks.signing_effective_day_lag.'
                extra = ' '.join(self.text(k) for k in ('case.plan', 'clock.convention'))
                extra += ' ' + self.text('clock.lag', median=self.num(base+'median_days'), n=self.num(base+'n'), p90=self.num(base+'p90_days'), maximum=self.num(base+'max_days'))
            elif key == 'cigarette_neighbor_exposure':
                status = '<a href="/viz/' + escape(self.lang) + '/worked_examples.html#cigarette_tax-heading">' + status + '</a>'
            elif key == 'paid_sick_leave':
                base = 'descriptive.paid_sick_leave.'
                for status in ('no_eligible_candidates', 'date_order_unresolved'):
                    count = self.psl['lineage']['temporal_counts'][status]
                    if count:
                        fact += ' ' + self.text('index.lineage.' + status, count=self.num(base + 'lineage.temporal_counts.' + status))
                extra = ' ' + self.text('coding.agreement', exact=self.num(base+'coding_agreement.exact_fields'), paired=self.num(base+'coding_agreement.stated_paired_fields'))
                extra += ' ' + self.text('external.agreement', identical=self.num(base+'external_projection_agreement.identical_fields'), comparable=self.num(base+'external_projection_agreement.comparable_fields'), date=self.num(base+'external_projection_agreement.snapshot_date'))
            status = self.text('index.' + key + '.status')
            if key in anchors:
                status = '<a href="/viz/' + escape(self.lang) + '/policy_exposure_records.html#' + anchors[key] + '">' + status + '</a>'
            elif key == 'cigarette_neighbor_exposure':
                status = '<a href="/viz/' + escape(self.lang) + '/worked_examples.html#cigarette_tax-heading">' + status + '</a>'
            elif key == 'paid_sick_leave':
                status = '<a href="#clause-matrix">' + status + '</a>'
            else:
                status = self.source(urls[:1], self.t('index.' + key + '.status'), title=self.t('index.' + key + '.status'))
            blocks.append('<dt>' + self.text('index.' + key + '.label') + '</dt><dd>' + fact + extra + self.small(status) + '</dd>')
        contents = ['<dl class="lm-index">' + ''.join(blocks) + '</dl>']
        rows = []
        for i, row in enumerate(self.clocks['cases']):
            j, design = self.designs[row['case_id']]
            p = f'descriptive.three_clocks.cases.{i}'
            q = f'design.did_cases.{j}'
            window = design['examples_page_window']
            label = escape(self.strings.get('lm.case.' + design['case_id'], design['label'][self.lang]))
            if design['display_status'] == 'stopped':
                label += self.small(self.text('stopped'))
            fmt = 'quarter' if design['period_unit'] == 'quarter' else 'raw'
            start, end = self.num(q + '.examples_page_window.start', fmt), self.num(q + '.examples_page_window.end', fmt)
            if design['period_unit'] == 'fiscal_year':
                start, end = 'FY' + start, 'FY' + end
            window_label = escape(start + '–' + end)
            changed = '—' if row['signing_effective']['changed_records'] is None else self.html_num(p + '.signing_effective.changed_records')
            gap = self.html_num(p + '.effective_enforceable.recorded_gap_changed_records')
            conditional = row['effective_enforceable']['additional_statewide_reading_records']
            if conditional:
                gap += ' + ' + self.html_num(p + '.effective_enforceable.additional_statewide_reading_records') + self.small(self.text('conditional'))
            rule_key = 'minimum_days' if design['full_exposure_min_days'] is not None else design['treatment_rule']
            rule = self.text('rule.' + rule_key, **({'days':self.num(q + '.full_exposure_min_days')} if rule_key == 'minimum_days' else {}))
            rows.append([label, window_label, escape(self.period(design, q)) + self.small(rule), self.html_num(p + '.examples_page_window_records'), self.html_num(p + '.signing_effective.denominator'), changed, gap])
        contents.append(self.table([self.t(k) for k in ('policy','window','period','records','comparable','changed','gap.header')], rows, caption=self.t('evidence.clocks.caption'), classes='lm-clock-table'))
        clause_rows = []
        for i, row in enumerate(self.psl['clause_rows']):
            p = f'descriptive.paid_sick_leave.clause_rows.{i}'
            label = escape(row['state']) + self.small(self.baseline_label(row, p))
            fields = [self.clause_value(row['clauses'][f], p + '.clauses.' + f, f) for f in ('accrual_rate','annual_use_cap','employer_coverage','carryover')]
            if row['state'] == 'CA':
                fields[1] = self.text('ca.cap', hours=self.num(p + '.audited_scope.use_limit_hours'), days=self.num(p + '.audited_scope.use_limit_days'))
            if row['state'] == 'DC':
                fields[1] = self.dc_cap(p)
            if row['state'] == 'CT':
                fields[1] = self.text('ct.cap', hours=self.num(p + '.clauses.annual_use_cap.value.0.hours'))
                fields[2] += self.small(self.text('ct.scope', threshold=self.num(p + '.audited_scope.min_employees')))
            urls = row['source_urls'] + row.get('audited_scope', {}).get('source_urls', [])
            label += self.small(self.source(urls, row['state'] + ' ' + self.t('law')))
            clause_rows.append([label] + fields)
        contents.append(self.table([self.t(k) for k in ('law','accrual.header','cap.header','coverage','carryover')], clause_rows, caption=self.t('evidence.clauses.caption'), anchor='clause-matrix'))
        contents.append(self.table([self.t(k) for k in ('law','lineage.title','score','tier','document')], self.lineage_rows(), caption=self.t('evidence.lineage.caption'), classes='lm-lineage'))
        return '<details class="lm-evidence"><summary>' + self.text('evidence') + '</summary><div class="lm-evidence-body">' + ''.join(contents) + '</div></details>'

    def evidence_items(self):
        n = self.num
        d = 'descriptive.'
        clocks = self.clocks
        clock_slots = dict(records=n(d+'three_clocks.denominators.examples_page_window.records'), comparable=n(d+'three_clocks.denominators.comparable.records'), changed=n(d+'three_clocks.signing_effective.changed_records'))
        yield 'three_clocks', clock_slots, [u for r in clocks['enforcement_examples'] for u in r['source_urls']]
        yield 'paid_sick_leave', dict(originals=n(d+'paid_sick_leave.original_jurisdictions'), versions=n(d+'paid_sick_leave.collected_text_versions'), template=n(d+'paid_sick_leave.lineage.tier_counts.D'), below=n(d+'paid_sick_leave.lineage.tier_counts.none')), [u for r in self.psl['clause_rows'] for u in r['source_urls']]
        c = self.data['descriptive']['cigarette_neighbor_exposure']
        yield 'cigarette_neighbor_exposure', dict(events=n(d+'cigarette_neighbor_exposure.raise_events'), pairs=n(d+'cigarette_neighbor_exposure.event_neighbor_pairs'), eligible=n(d+'cigarette_neighbor_exposure.locally_eligible_pairs'), controls=n(d+'cigarette_neighbor_exposure.strict_controls')), c['source_urls']
        c = self.data['descriptive']['nurse_neighbor_channels']
        years = re.findall(r'\d{4}', next(k for k in c['channel_class_counts'] if k.startswith('opened_2018')))
        self.numbers.append({'context':'nurse channel period', 'value':'–'.join(years), 'json_path':d+'nurse_neighbor_channels.channel_class_counts.opened_2018_2024', 'format':'years_in_key'})
        yield 'nurse_neighbor_channels', dict(opened=n(d+'nurse_neighbor_channels.channel_class_counts.opened_2018_2024'), pairs=n(d+'nurse_neighbor_channels.directed_adjacent_pairs'), start=years[0], end=years[1]), c['source_urls']
        c = self.data['descriptive']['enforcement_responsibility']
        yield 'enforcement_responsibility', dict(laws=n(d+'enforcement_responsibility.original_laws'), pilot=n('design.enforcement_timeliness.pilot_states')), [u for r in c['rows'] for u in r['source_urls']]
        c = self.data['descriptive']['policy_lifecycle']
        yield 'policy_lifecycle', dict(candidates=n(d+'policy_lifecycle.reviewed_candidates'), two=n(d+'policy_lifecycle.verification_counts.two_sources'), one=n(d+'policy_lifecycle.verification_counts.one_source'), rejected=n(d+'policy_lifecycle.verification_counts.rejected'), unverified=n(d+'policy_lifecycle.verification_counts.not_verified')), [u for r in c['cards'] for u in r['source_urls']]
        c = self.data['prediction']
        yield 'frozen_predictions', dict(versions=n('prediction.clause_rules.statute_versions'), conditions=n('prediction.clause_rules.conditions'), routes=n('prediction.clause_rules.candidate_routes'), predictions=n('prediction.frozen_predictions','len')), [u for r in c['frozen_predictions'] for u in r['source_urls']]
        c = self.data['descriptive']['selected_legal_status']
        yield 'selected_legal_status', dict(recorded_laws=n(d+'selected_legal_status.law_event_groups', 'len'), laws=n(d+'selected_legal_status.laws_with_primary_document_interruptions'), events=n(d+'selected_legal_status.event_records'), unsearched=n(d+'selected_legal_status.individually_unsearched_map_records'), mapped=n(d+'selected_legal_status.map_records')), [u for r in c['law_event_groups'][:2] for u in r['source_urls']]

    def supplementary_tables(self):
        # These compact tables preserve the descriptive results and frozen
        # predictions in the evidence index, without additional summary panels.
        output = []
        rows = []
        p = 'descriptive.nurse_neighbor_channels.channel_class_counts'
        for key in self.data['descriptive']['nurse_neighbor_channels']['channel_class_counts']:
            # Period ranges are encoded in the JSON class name, never typed.
            years = re.findall(r'\d{4}', key)
            kind = 'throughout' if key.startswith('eligible_') else 'never' if key.startswith('never_') else 'future' if key.startswith('opens_') else 'opened'
            term = self.text('nurse.' + kind, **(dict(start=years[0], end=years[1]) if len(years) == 2 else dict(year=years[0])))
            if years:
                self.numbers.append({'context': term, 'value': '–'.join(years), 'json_path': p + '.' + key, 'format':'years_in_key'})

            rows.append([term, self.html_num(p + '.' + key)])
        output.append(self.table([self.t('channel'),self.t('records')], rows, caption=self.t('index.nurse_neighbor_channels.label'), anchor='nurse-corridors', caption_sources=self.source(self.data['descriptive']['nurse_neighbor_channels']['source_urls'], 'Nurse Licensure Compact map')))
        rows = []
        for i, row in enumerate(self.data['descriptive']['enforcement_responsibility']['rows']):
            agency = escape(row['agency_name'] or '—')
            if row['state'] == 'AZ':
                agency = self.text('agency.az')
            rows.append([escape(row['state']), agency, self.text('funding.' + row['funding_category']) if row['funding_category'] else self.text('not.stated'), self.source(row['source_urls'], row['state'] + ' ' + self.t('agency'))])
        output.append(self.table([self.t('law'),self.t('agency'),self.t('funding'),self.t('source')], rows, caption=self.t('index.enforcement_responsibility.label'), anchor='agency-responsibility'))
        rows = []
        for i, card in enumerate(self.data['descriptive']['policy_lifecycle']['cards']):
            events = '<br>'.join(self.html_num(f'descriptive.policy_lifecycle.cards.{i}.ending_events.{j}.date') + (' · ' + self.text('lifecycle.date.' + event['date_type']) if event['date_type'] in ('effective', 'signed') else '') + ' · ' + self.text('lifecycle.' + event['type']) for j, event in enumerate(card['ending_events']))
            rows.append([self.text('lifecycle.' + card['id'], **({'bill': self.num(f'descriptive.policy_lifecycle.cards.{i}.label', 'title_number')} if card['id'] == 'iowa' else {})),events,self.source(card['source_urls'], card['label'])])
        output.append(self.table([self.t('policy'),self.t('event'),self.t('source')],rows,caption=self.t('index.policy_lifecycle.label'), anchor='policy-lifecycle'))
        rows = []
        for i, pred in enumerate(self.data['prediction']['frozen_predictions']):
            slots = {}
            if '{days}' in self.strings['lm.prediction.' + pred['prediction_id']]:
                days = re.search(r'\b(\d+) days\b', pred['statement']).group(1)
                self.numbers.append({'context':pred['prediction_id'], 'value':days, 'json_path':f'prediction.frozen_predictions.{i}.statement', 'format':'days_in_statement'})
                slots['days'] = days
            statement = self.text('prediction.' + pred['prediction_id'], **slots)
            self.numbers.append({'context':'frozen prediction ID','value':pred['prediction_id'],'json_path':f'prediction.frozen_predictions.{i}.prediction_id','format':'raw'})
            rows.append([escape(pred['prediction_id']),statement,self.source(pred['source_urls'], pred['prediction_id'] + ' ' + self.t('prediction')) or '—'])
        output.append(self.table([self.t('prediction'),self.t('statement'),self.t('source')],rows,caption=self.t('index.frozen_predictions.label') + ' · ' + self.t('awaiting.data'), anchor='frozen-predictions'))
        rows = []
        for i, row in enumerate(self.data['descriptive']['selected_legal_status']['law_event_groups']):
            rows.append([escape(row['jurisdiction']) if self.lang == 'en' else self.text('status.jurisdiction.' + str(i)), escape(row['law'].replace('licence', 'license')) if self.lang == 'en' else self.text('status.law.' + str(i)),self.html_num(f'descriptive.selected_legal_status.law_event_groups.{i}.events'),self.source(row['source_urls'], row['law'])])
            for number in re.findall(r'\d+(?:\.\d+)?',row['law']):
                self.numbers.append({'context':'law title','value':number,'json_path':f'descriptive.selected_legal_status.law_event_groups.{i}.law','format':'in_title'})
        output.append(self.table([self.t('jurisdiction'),self.t('law'),self.t('events'),self.t('source')],rows,caption=self.t('evidence.status.caption'), anchor='legal-status'))
        return output

    def render(self):
        n = self.html_num
        overall = 'descriptive.three_clocks.signing_effective.'
        intro = '<div class="lm-first"><div class="lm-intro"><h1 id="lm-title" class="lm-title" data-prose="title">' + self.text('title') + '</h1><p class="lm-sub" data-prose="sub">' + self.text('sub', cases=n('design.did_cases','len')) + '</p><p class="lm-key"><span class="lm-share">' + n(overall+'share','pct') + '</span><span class="lm-ratio">' + n(overall+'changed_records') + '/' + n(overall+'denominator') + '</span></p><p class="lm-keytext" data-prose="keytext">' + self.text('keytext') + '</p><p class="lm-def" data-prose="definition">' + self.text('definition') + '</p></div>' + self.main_figure() + '</div>'
        scores = {}
        summary_pairs = []
        for state in ('AZ', 'CA'):
            i, row = next((i, row) for i, row in enumerate(self.psl['lineage']['rows']) if row['statute_id'] == state + '_enacted')
            earlier = row['closest_earlier_text']
            summary_pairs.append(earlier)
            if earlier is not None:
                p = f'descriptive.paid_sick_leave.lineage.rows.{i}.closest_earlier_text'
                scores[state.lower() + '_score'] = n(p + '.similarity_score', 'score')
                key = 'summary.model' if earlier['id'].startswith('MODEL_') else 'summary.sf' if earlier['id'].startswith('SF_PSLO_2006') else None
                scores[state.lower() + '_text'] = self.t(key) if key else unescape(self.earlier_name(row, p))
        summary_key = 'clauses.summary' if (all(r is not None and r['evidence_tier'] == 'D' for r in summary_pairs)
                                            and summary_pairs[1]['id'].startswith('SF_PSLO_2006')) else 'clauses.scope'
        if summary_key == 'clauses.scope':
            scores = {}
        summary = '<a href="#clause-matrix">' + self.text('evidence') + '</a>' + (' ' if self.lang == 'en' else '') + self.text(summary_key, jurisdictions=n('descriptive.paid_sick_leave.original_jurisdictions'), **scores)
        clauses = '<section class="lm-block"><h2 data-prose="clauses.title">' + self.text('clauses.title') + '</h2><p class="lm-sub" data-prose="clauses.summary">' + summary + '</p></section>'
        return intro + self.gaps() + clauses + self.evidence()


def render_policy_exposure(data, lang, strings):
    return Renderer(data, lang, strings).render()


def render_treatment_timing(data, lang, strings):
    """Render the short timing result on Estimates from the audited contract."""
    renderer = Renderer(data, lang, strings)

    def text(key, **slots):
        template = strings['we.timing.' + key]
        expected = {field for _, field, _, _ in Formatter().parse(template) if field}
        if expected != set(slots):
            raise ValueError(f'we.timing.{key}: expected slots {expected}, got {set(slots)}')
        return escape(template.format(**slots))

    base = 'descriptive.three_clocks.'
    result = text('result', changed=renderer.num(base + 'signing_effective.changed_records'),
                  comparable=renderer.num(base + 'signing_effective.denominator'))
    examples = {row['state']: i for i, row in enumerate(renderer.clocks['enforcement_examples'])}
    slots = {}
    for state in ('MN', 'IN'):
        path = base + f'enforcement_examples.{examples[state]}.'
        for clock in ('effective', 'enforceable'):
            slots[state.lower() + '_' + clock] = renderer.num(path + clock + '_first_period')
    trace = '#signing-effective-records'
    ratio = ('{changed} of {comparable}' if lang == 'en' else '{changed} 条改变').format(changed=renderer.num(base + 'signing_effective.changed_records'), comparable=renderer.num(base + 'signing_effective.denominator'))
    result = result.replace(ratio, '<a href="' + escape(trace, quote=True) + '">' + ratio + '</a>', 1)
    return ('<section id="treatment-timing" class="we-timing" aria-labelledby="timing-title">'
            '<h2 id="timing-title">' + text('title') + '</h2><p>' + result + '</p><p>'
            + text('examples', **slots) + '</p><details id="signing-effective-records" class="we-timing-records">'
            + '<summary>' + text('records') + '</summary>'
            + renderer.signing_effective_records(changed_only=True) + '</details></section>')


def render_policy_exposure_records(data, lang, strings):
    renderer = Renderer(data, lang, strings)
    return ('<h1 id="lm-title" class="lm-title">' + escape(strings['lm.records.title']) + '</h1>'
            '<div class="lm-records">' + renderer.signing_effective_records() + renderer.enforcement_events() + ''.join(renderer.supplementary_tables())
            + '<p class="lm-cap">' + renderer.source(data['descriptive']['cigarette_neighbor_exposure']['source_urls'], renderer.t('index.cigarette_neighbor_exposure.label')) + '</p></div>')
