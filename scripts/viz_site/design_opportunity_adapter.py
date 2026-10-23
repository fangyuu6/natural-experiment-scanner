"""Static, bilingual presentation of the strictly verified timing ledger.

This module never computes panel support or comparison counts. Its JSON adapter
preserves unknown source values; controls and cells share the same timing ledger.
"""
from __future__ import annotations

import json
import textwrap
from html import escape
from urllib.parse import urlsplit

YEARS = tuple(range(2004, 2026))
SVG_WIDTH = 1034
SVG_HEIGHT = 550
GRID_LEFT = 0
GRID_TOP = 50
CELL_WIDTH = 47
ROW_HEIGHT = 58

DEFAULTS = {
    "en": {
        "family": "Family", "year": "Recorded year", "dated": "Dated jurisdictions",
        "groups": "Year groups", "largest": "Largest group", "share": "Largest-year share",
        "inventory": "Source inventory", "jurisdiction": "Jurisdiction", "date": "Date",
        "status": "Status", "sources": "Date sources", "source": "Source",
        "unknown": "Unknown", "empty": "No strictly verified dates in this year.",
        "evaluations": "Recorded evaluations", "grid_title": "Policy timing in eight curated families",
        "grid_desc": "Counts of strictly verified jurisdictions by recorded year, under each row's date rule. Family and year selectors show the same records below.",
        "legend": "Strictly verified jurisdictions", "verified": "Verified", "fixed": "Corrected and verified",
        "single_source": "Single source; withheld", "disputed": "Disputed", "removed": "Removed",
        "strict_dated": "Strict dated", "withheld_single_source": "Single source; withheld",
        "undated": "No recorded date", "outside_cutoff_or_unresolved": "Outside cutoff or unresolved",
        "all": "All", "all_years": "All recorded years", "no_recorded_date": "No recorded date", "not_adopted": "No qualifying recorded date",
        "unresolved": "Unresolved", "future": "After cutoff", "excluded": "Excluded",
        "coverage": "Nominal 95% intervals: {coverage}% matched synthetic coverage.",
        "context": "Published comparator: microdata DDD context.", "stopped": "Stopped at {reason}.",
    },
    "zh": {
        "family": "政策族", "year": "记录年份", "dated": "有日期的辖区",
        "groups": "年份组数", "largest": "最大年份组", "share": "最大年份占比",
        "inventory": "来源清单", "jurisdiction": "辖区", "date": "日期",
        "status": "状态", "sources": "日期来源", "source": "来源",
        "unknown": "未知", "empty": "该年份没有严格核验日期。",
        "evaluations": "已记录的评估", "grid_title": "八个核验政策族的政策时点",
        "grid_desc": "按各行日期规则，显示每个记录年份中严格核验的辖区数。下方政策族与年份选项显示同一组记录。",
        "legend": "严格核验辖区数", "verified": "已核验", "fixed": "修正后核验",
        "single_source": "单一来源，暂不纳入", "disputed": "有争议", "removed": "已移除",
        "strict_dated": "严格核验日期", "withheld_single_source": "单一来源，暂不纳入",
        "undated": "无记录日期", "outside_cutoff_or_unresolved": "晚于截止日期或待核实",
        "all": "全部", "all_years": "全部记录年份", "no_recorded_date": "无记录日期", "not_adopted": "无符合定义的记录日期",
        "unresolved": "待核实", "future": "晚于截止日期", "excluded": "已排除",
        "coverage": "名义 95% 区间：模拟覆盖率 {coverage}%。",
        "context": "已发表对照：微观数据 DDD 背景。", "stopped": "已停止：{reason}。",
    },
}


PUBLIC_EXCLUSIONS = {
    ("license_recognition", "NJ"): {
        "en": "2013 endorsement law vs 2018/2019 versions, no defensible year",
        "zh": "2013 年认可法与 2018/2019 年版本之间无可确定的年份"},
    ("tnc_framework", "CA"): {
        "en": "2013 CPUC rule, 2014 insurance law, 2019 checks; no single act",
        "zh": "2013 年 CPUC 规则、2014 年保险法与 2019 年审查，无单一法案"},
    ("salary_history_ban", "AL"): {
        "en": "Law bars refusing to hire over non-disclosure, not asking",
        "zh": "该法禁止因未披露薪资而拒绝录用，并未禁止询问"},
}


ZH_JURISDICTIONS = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    "阿拉巴马 阿拉斯加 亚利桑那 阿肯色 加利福尼亚 科罗拉多 康涅狄格 特拉华 哥伦比亚特区 佛罗里达 佐治亚 夏威夷 爱达荷 伊利诺伊 印第安纳 艾奥瓦 堪萨斯 肯塔基 路易斯安那 缅因 马里兰 马萨诸塞 密歇根 明尼苏达 密西西比 密苏里 蒙大拿 内布拉斯加 内华达 新罕布什尔 新泽西 新墨西哥 纽约 北卡罗来纳 北达科他 俄亥俄 俄克拉何马 俄勒冈 宾夕法尼亚 罗得岛 南卡罗来纳 南达科他 田纳西 得克萨斯 犹他 佛蒙特 弗吉尼亚 华盛顿 西弗吉尼亚 威斯康星 怀俄明".split(),
))


def _strings(lang, strings=None):
    if lang not in DEFAULTS:
        raise ValueError("language must be en or zh")
    result = dict(DEFAULTS[lang])
    for key, value in (strings or {}).items():
        short = key.removeprefix("do.")
        if short in result:
            result[short] = value
    return result


def _safe_url(value):
    """Only source links, never executable or local-data URLs."""
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    return value if parsed.scheme in ("http", "https") and parsed.netloc else None


def _localized(value, lang):
    return value.get(lang) if isinstance(value, dict) else value


def normalize_payload(payload, lang="en", strings=None):
    """Return the small UI ledger without modifying or filling unknown source data."""
    labels = _strings(lang, strings)
    summaries = {row["family_id"]: row for row in payload["opportunity_summary"]}
    rows = payload["family_jurisdiction"]
    families = []
    for family in payload["family"]:
        identifier = family["id"]
        jurisdictions = []
        for row in rows:
            if row["family_id"] != identifier:
                continue
            status = row.get("inventory_status")
            jurisdictions.append({
                "code": row["jurisdiction"],
                "name": row.get("jurisdiction_name_" + lang) or (ZH_JURISDICTIONS.get(row["jurisdiction"]) if lang == "zh" else None) or row.get("jurisdiction_name_en") or row["jurisdiction"],
                "year": row.get("recorded_year"), "date": row.get("recorded_date"),
                "precision": row.get("date_precision"), "strict": row.get("included_strict") is True,
                "status": status, "status_label": labels.get(status, status or labels["unknown"]),
                "sources": [url for source in row.get("sources", []) if (url := _safe_url(source))],
                "exclusion_reason": PUBLIC_EXCLUSIONS.get((identifier, row["jurisdiction"]), {}).get(lang),
            })
        summary = summaries[identifier]
        families.append({"id": identifier, "label": family["short_" + lang].replace("15美元", "15 美元"),
                         "clock": family["clock_" + lang], "summary": dict(summary),
                         "groups": list(summary["year_group_sizes"]), "jurisdictions": jurisdictions})
    ui = {"families": families, "years": list(YEARS), "strings": labels, "lang": lang}
    from scripts.viz_site.validate_design_opportunity import validate_public_fields
    errors = validate_public_fields(ui)
    if errors:
        raise ValueError("; ".join(errors))
    return ui


def _null_text(value, labels):
    return labels["unknown"] if value is None else str(value)


def _sources(row, labels, compact=False, hosts=False):
    links = "".join('<a href="' + escape(url, quote=True) + '" aria-label="' + escape(labels["source"] + " " + str(index) + (": " + urlsplit(url).netloc.removeprefix("www.") if hosts else ""), quote=True) + '\">' +
                    str(index) + "</a>" for index, url in enumerate(row["sources"], 1))
    if not links:
        return escape(labels["unknown"])
    prefix = "" if compact else '<span class="do-source-word">' + escape(labels["source"]) + '</span>'
    return prefix + links


def render_inventory_rows(family, labels):
    out = []
    for row in family["jurisdictions"]:
        date = row["date"] if row["date"] is not None else row["year"]
        note = row.get("exclusion_reason")
        status = escape(row["status_label"])
        if note:
            status += '<span class="do-inventory-note">' + escape(str(note)) + "</span>"
        out.append('<tr data-jurisdiction="' + escape(row["code"], quote=True) + '"><th scope="row">' +
                   escape(row["name"]) + '</th><td>' + escape(_null_text(date, labels)) +
                   '</td><td>' + status + '</td><td>' + _sources(row, labels, compact=True) + '</td></tr>')
    return "".join(out)


def render_readout(family, year, labels):
    selected = [row for row in family["jurisdictions"] if row["strict"] and (year is None or row["year"] == year)]
    if not selected:
        return '<li class="do-empty">' + escape(labels["empty"]) + '</li>'
    return "".join('<li><span class="do-jurisdiction">' + escape(row["name"]) + '</span> <span class="do-date">' +
                   escape(str(row["date"] if row["date"] is not None else row["year"])) +
                   '</span><span class="do-date-sources">' + _sources(row, labels) + '</span></li>' for row in selected)


def map_row(family_id, row):
    """A row the maps colour and the sources table lists: strictly dated, or single-source with a year the curated data does not leave open."""
    from scripts.viz_site.validate_curated import OPEN_YEAR
    return row["strict"] or (row["status"] == "withheld_single_source" and (family_id, row["code"]) not in OPEN_YEAR)


def render_map_sources(payload, lang="en", strings=None):
    """Render the dated ledger as one collapsed homepage disclosure."""
    ui = normalize_payload(payload, lang, strings)
    labels = dict(ui["strings"], sources="Sources" if lang == "en" else "来源")
    summary = "Dates and sources by state" if lang == "en" else "各州日期与来源"
    family_data = {family["id"]: family for family in payload["family"]}
    out = ['<style>.story .nx-map-sources{grid-column:2;min-width:0;margin:8px 0 0;font-size:var(--nx-type-tick);line-height:1.5;color:var(--nx-text-2)}'
           '.nx-map-sources>summary{min-height:44px;box-sizing:border-box;padding:12px 0;cursor:pointer;color:var(--nx-muted)}'
           '.nx-map-sources>summary:focus-visible{outline:2px solid var(--nx-accent);outline-offset:2px}'
           '.nx-map-sources section{margin:16px 0 24px}.nx-map-sources h3{margin:0 0 6px;font-size:14px;color:var(--nx-text)}'
           '.nx-map-sources p{margin:0 0 8px}.nx-map-sources table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:var(--nx-type-tick)}'
           '.nx-map-sources th,.nx-map-sources td{padding:6px 4px;text-align:left;vertical-align:top;overflow-wrap:anywhere;border-bottom:1px solid var(--nx-line-2)}'
           '.nx-map-sources td:nth-child(2){white-space:nowrap;font-variant-numeric:tabular-nums}'
           '.nx-map-sources td:last-child{word-break:normal}'
           '.nx-map-sources th{position:static;cursor:default;white-space:normal}'
           '.nx-map-sources section[hidden]{display:none}'
           '.nx-map-sources th:first-child{width:36%}.nx-map-sources th:nth-child(2){width:32%}'
           '.nx-map-sources a{display:inline-flex;align-items:flex-start;justify-content:center;vertical-align:top;min-width:32px;min-height:44px;text-align:center}'
           '.story:lang(zh) .nx-map-sources{line-height:1.75}'
           '@media(max-width:600px){.nx-map-sources a{min-width:44px}}'
           '@media(max-width:479px){.nx-map-sources table,.nx-map-sources tbody{display:block}'
           '.nx-map-sources thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}'
           '.nx-map-sources tbody tr{display:grid;grid-template-columns:minmax(0,1fr) auto;padding:8px 0;border-bottom:1px solid var(--nx-line-2)}'
           '.nx-map-sources tbody th,.nx-map-sources tbody td{width:auto;padding:0 4px;border-bottom:0}'
           '.nx-map-sources tbody th:first-child{width:auto}'
           '.nx-map-sources tbody td:last-child{grid-column:1/-1;padding-top:4px}'
           '.nx-map-sources a{align-items:center}}</style>',
           '<details class="nx-map-sources" id="map-sources"><summary>' + escape(summary) + '</summary>']
    for family in ui["families"]:
        identifier = family["id"]
        clock = family_data[identifier].get("date_basis_" + lang) or family["clock"]
        out.append('<section data-family="' + escape(identifier, quote=True) + '"><h3>' + escape(family["label"]) +
                   '</h3><p>' + escape(clock) + '</p><table><thead><tr>')
        out.extend('<th scope="col">' + escape(labels[key]) + '</th>' for key in ("jurisdiction", "date", "sources"))
        out.append('</tr></thead><tbody>')
        for row in family["jurisdictions"]:
            if not map_row(identifier, row) or (row["date"] is None and row["year"] is None):
                continue
            date = row["date"] if row["date"] is not None else row["year"]
            out.append('<tr data-jurisdiction="' + escape(row["code"], quote=True) + '"><th scope="row">' +
                       escape(row["name"]) + '</th><td>' + escape(str(date)) + '</td><td>' +
                       _sources(row, labels, compact=True, hosts=True) + '</td></tr>')
        out.append('</tbody></table></section>')
    return "".join(out) + '</details>' + r'''<script>
(function(){
 var root=document.getElementById('map-sources');if(!root)return;
 function show(id){root.querySelectorAll('section[data-family]').forEach(function(section){section.hidden=section.dataset.family!==id})}
 var selected=document.querySelector('#sm-tabs .nx-tab[aria-checked="true"]');if(selected)show(selected.dataset.f);
 document.addEventListener('nx:map-policy',function(event){if(event.detail)show(event.detail.policy)});
})();
</script>'''


def render_timing_grid(families, labels):
    first_year = max(families[0]["groups"], key=lambda group: (group["count"], -group["year"]))["year"]
    out = [f'<div class="do-grid-scroll"><svg xmlns="http://www.w3.org/2000/svg" id="design-timing-grid" class="do-timing-grid" width="{SVG_WIDTH}" height="{SVG_HEIGHT}" data-nx-intro="timing" role="group" aria-labelledby="design-grid-title design-grid-desc">',
           '<title id="design-grid-title">' + escape(labels["grid_title"]) + '</title>',
           '<desc id="design-grid-desc">' + escape(labels["grid_desc"]) + '</desc>',
           '<g font-size="13" fill="#374151">']
    for column, year in enumerate(YEARS):
        x = GRID_LEFT + column * CELL_WIDTH + CELL_WIDTH / 2
        tick = 'even' if column % 2 == 0 else 'odd'
        out.append(f'<text x="{100*(column+.5)/len(YEARS):g}%" y="34" class="do-year-label" data-year="{year}" font-size="12" text-anchor="middle" data-tick="{tick}">{year}</text>')
    for index, family in enumerate(families):
        y = GRID_TOP + index * ROW_HEIGHT
        out.append('<g class="do-grid-row" style="--row:' + str(index) + '" data-family="' + escape(family["id"], quote=True) + '">')
        out.append(f'<rect class="do-rowbg" x="0" y="{y - 6}" width="100%" height="58" rx="6" fill="none"/>')
        counts = {group["year"]: group["count"] for group in family["groups"]}
        for column, year in enumerate(YEARS):
            count = counts.get(year, 0)
            x = GRID_LEFT + column * CELL_WIDTH
            tint = 0 if count == 0 else 1 if count <= 2 else 2 if count <= 5 else 3 if count <= 9 else 4 if count <= 14 else 5
            fill = ("#eceef2", "#a8c5f9", "#7ba4f0", "#5283e0", "#3263c3", "#1e4798")[tint]
            ink = "#ffffff" if tint >= 4 else "#111827"
            cell_label = (f'{family["label"]}，{year} 年：{count}' if labels["source"] == "来源"
                          else f'{family["label"]} · {year}: {count}')
            tabindex = 0 if index == 0 and year == first_year else -1
            out.append(f'<g tabindex="{tabindex}" role="button" aria-pressed="false" aria-label="' + escape(cell_label, quote=True) + '" class="do-cell" data-family="' + escape(family["id"], quote=True) + f'" data-year="{year}" data-count="{count}" style="--i:{column};--row:{index}">')
            out.append('<title>' + escape(cell_label) + '</title>')
            stroke = "none" if count == 0 else fill
            out.append(f'<rect x="{100*column/len(YEARS):g}%" y="{y}" width="{CELL_WIDTH - 3}" style="width:calc(100% / 22 - 3px)" height="44" rx="2" fill="{fill}" stroke="{stroke}"/>')
            if count:
                out.append(f'<text x="{100*(column+.5)/len(YEARS):g}%" dx="-1.5" y="{y + 28}" text-anchor="middle" fill="{ink}">{count}</text>')
            out.append('</g>')
        out.append('</g>')
    out.append('</g></svg></div>')
    # Fixed companion columns share the heatmap's row coordinates. Only the
    # year cells scroll, so dates and summaries keep their visible context.
    def canvas(css, width):
        return f'<svg xmlns="http://www.w3.org/2000/svg" class="{css}" width="{width}" height="{SVG_HEIGHT}" role="group"><g font-size="13" fill="#374151">'

    out.append(canvas('do-grid-labels', 224))
    for index, family in enumerate(families):
        y = GRID_TOP + index * ROW_HEIGHT
        out.append('<g class="do-grid-row" style="--row:' + str(index) + '" data-family="' + escape(family["id"], quote=True) + '">')
        out.append(f'<rect class="do-rowbg" x="0" y="{y - 6}" width="224" height="58" rx="6" fill="none"/>')
        clock_lines = textwrap.wrap(family["clock"], width=28 if labels["source"] != "来源" else 15)
        start = y + 22 - len(clock_lines) * 8.5 + 5
        out.append(f'<text x="8" y="{start:g}" class="do-row-label" font-weight="600">{escape(family["label"])}</text>')
        for n, text in enumerate(clock_lines, 1):
            out.append(f'<text x="8" y="{start + n * 17:g}" class="do-grid-clock">{escape(text)}</text>')
        out.append('</g>')
    out.append('</g></svg>')
    out.append(canvas('do-grid-totals', 288))
    summary_x = (96, 174, 280)
    # Wrap to the actual numeric column widths, accounting for CJK glyphs.
    for key, x, width in zip(("dated", "groups", "share"), summary_x, (88, 70, 98)):
        char_width = 13 if labels["source"] == "来源" else 6.9
        lines = textwrap.wrap(labels[key], width=int(width / char_width), break_long_words=False)
        for line, text in enumerate(lines):
            out.append(f'<text class="do-summary-heading" x="{x}" y="{18 + line * 16}" text-anchor="end">{escape(text)}</text>')
    for index, family in enumerate(families):
        y = GRID_TOP + index * ROW_HEIGHT
        out.append('<g class="do-grid-row" style="--row:' + str(index) + '" data-family="' + escape(family["id"], quote=True) + '">')
        out.append(f'<rect class="do-rowbg" x="0" y="{y - 6}" width="288" height="58" rx="6" fill="none"/>')
        summary = family["summary"]
        modal = summary.get("modal_share")
        values = (_null_text(summary.get("strict_dated_jurisdictions"), labels),
                  _null_text(summary.get("year_group_count"), labels),
                  labels["unknown"] if modal is None else f"{100 * modal:.1f}%")
        for key, value, x in zip(("dated", "groups", "share"), values, summary_x):
            out.append(f'<text class="do-grid-summary" data-summary="{key}" x="{x}" y="{y + 28}" text-anchor="end">{escape(value)}</text>')
        out.append('</g>')
    out.append('</g></svg>')
    out.append('<div class="do-grid-legend"><span>' + escape(labels["legend"]) + '</span>')
    for label, fill in zip(("0", "1–2", "3–5", "6–9", "10–14", "15+"), ("#eceef2", "#a8c5f9", "#7ba4f0", "#5283e0", "#3263c3", "#1e4798")):
        out.append(f'<span class="do-legend-bin"><i aria-hidden="true" style="background:{fill}"></i>{label}</span>')
    out.append('</div>')
    return "".join(out)


def _runs(payload, lang, labels):
    if not payload["run_evidence"]:
        return ""
    out = ['<section class="do-runs" aria-labelledby="design-evaluations-heading"><h2 id="design-evaluations-heading">' + escape(labels["evaluations"]) + '</h2><ul>']
    for row in payload["run_evidence"]:
        link = _localized(row["link"], lang)
        if not isinstance(link, str) or not (link == f"/viz/{lang}/worked_examples.html" or link.startswith(f"/viz/{lang}/worked_examples.html#")):
            raise ValueError("recorded evaluation link must target the existing examples page")
        runs = row.get("primary_runs", [])
        windows = []
        outcomes = []
        coverage = []
        for run in runs:
            years = run.get("calendar_years")
            if years:
                window = f"{min(years)}–{max(years)}"
                if window not in windows:
                    windows.append(window)
            outcome = _localized(run.get("outcome"), lang)
            if outcome and outcome not in outcomes:
                outcomes.append(outcome)
            measured = run.get("coverage")
            if measured and measured.get("value") is not None:
                value = measured["value"]
                value = value * 100 if 0 <= value <= 1 else value
                if value not in coverage:
                    coverage.append(value)
        details = [("、".join(windows) + " 年") if lang == "zh" and windows else ", ".join(windows)]
        if row["example_id"] != "castle_doctrine":
            details.append(("；" if lang == "zh" else "; ").join(outcomes))
        if row.get("stop_reason"):
            details.append(labels["stopped"].format(reason=_localized(row["stop_reason"], lang)))
        elif coverage:
            details.append(labels["coverage"].format(coverage="/".join(f"{n:g}" for n in coverage)))
        if row["example_id"] == "medicaid_expansion":
            details.append(labels["context"])
        out.append('<li data-example="' + escape(row["example_id"], quote=True) + '"><a href="' + escape(link, quote=True) + '">' +
                   escape(_localized(row["label"], lang)) + '</a><span>' + escape(" · ".join(str(value) for value in details if value)) + '</span></li>')
    return "".join(out) + '</ul></section>'


def render_design_opportunity(payload, lang="en", strings=None):
    """Render one graphic, selected record view, summaries and frozen run links."""
    ui = normalize_payload(payload, lang, strings)
    labels = ui["strings"]
    families = ui["families"]
    if not families:
        raise ValueError("timing ledger has no families")
    selected = families[0]
    first_year = max(selected["groups"], key=lambda group: (group["count"], -group["year"]))["year"]
    out = ['<div class="do-content"><figure class="do-graphic">', render_timing_grid(families, labels), '</figure>',
           '<section class="do-selected" aria-labelledby="design-selected-heading"><div class="do-controls"><label for="design-family">' + escape(labels["family"]) + '<select id="design-family">']
    out.extend('<option value="' + escape(family["id"], quote=True) + '">' + escape(family["label"]) + '</option>' for family in families)
    out.append('</select></label><label for="design-year">' + escape(labels["year"]) + '<select id="design-year">')
    out.append('<option value="all">' + escape(labels["all_years"]) + '</option>')
    out.extend(f'<option value="{year}"' + (' selected' if year == first_year else '') + f'>{year} · ' + str(next((g['count'] for g in selected['groups'] if g['year'] == year), 0)) + '</option>' for year in YEARS)
    out.append('</select></label></div><div class="do-strip" id="design-strip" role="group" aria-label="' + escape(labels["year"], quote=True) + '"></div><h2 id="design-selected-heading">' +
               escape(selected["label"]) + f' <span class="do-nb">· {first_year}</span></h2><p id="design-selected-clock">' + escape(selected["clock"]) +
               '</p><ul id="design-readout" aria-live="polite">' + render_readout(selected, first_year, labels) + '</ul></section>')
    out.append('<div class="do-summary-wrap"><table class="do-summaries"><thead><tr><th scope="col">' + escape(labels["family"]) + '</th><th scope="col">' + escape(labels["dated"]) +
               '</th><th scope="col">' + escape(labels["groups"]) + '</th><th scope="col">' + escape(labels["share"]) + '</th></tr></thead><tbody>')
    for family in families:
        summary = family["summary"]
        modal = summary.get("modal_share")
        share = labels["unknown"] if modal is None else f"{100 * modal:.1f}%"
        out.append('<tr data-family="' + escape(family["id"], quote=True) + '"><th scope="row">' + escape(family["label"]) + '<span class="do-summary-clock">' + escape(family["clock"]) + '</span></th><td>' +
                   escape(_null_text(summary.get("strict_dated_jurisdictions"), labels)) + '</td><td>' + escape(_null_text(summary.get("year_group_count"), labels)) +
                   '</td><td>' + escape(share) + '</td></tr>')
    out.append('</tbody></table></div><details class="do-inventory"><summary>' + escape(labels["inventory"]) + '</summary><table><thead><tr>')
    out.extend('<th scope="col">' + escape(labels[key]) + '</th>' for key in ('jurisdiction', 'date', 'status', 'sources'))
    out.append('</tr></thead><tbody id="design-inventory-body">' + render_inventory_rows(selected, labels) + '</tbody></table></details>')
    run_markup = _runs(payload, lang, labels)
    from scripts.viz_site.validate_design_opportunity import validate_public_fields
    errors = validate_public_fields(run_markup)
    if errors:
        raise ValueError("; ".join(errors))
    out.append(run_markup)
    # ASCII encoding keeps the bilingual data from introducing CJK into EN HTML;
    # escaping < prevents a source string from closing the inert JSON script.
    # Missing dates remain unknown; omit absent optional fields in the public JSON.
    def without_absent(value):
        if isinstance(value, dict):
            return {key: without_absent(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [without_absent(item) for item in value]
        return value
    encoded = json.dumps(without_absent(ui), ensure_ascii=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    out.append('<script type="application/json" id="design-opportunity-data">' + encoded + '</script></div>')
    return "".join(out)
