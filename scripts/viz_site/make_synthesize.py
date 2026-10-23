#!/usr/bin/env python3
"""Render development L3 aggregate panels from durable repository inputs.

--check compares output bytes without writing. No estimation or release.
Legacy plot helpers remain available to the Estimates page.
"""
import argparse
from html import escape as esc
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.viz_site.shared_chrome import render_slots, marked, include_slots

NOISE = ROOT / 'docs/plans/l3-results/NOISE_FIGURE_DATA.json'
META = ROOT / 'data/l3_meta/l2_set_v2.json'
COUNTS = ROOT / 'scripts/viz_site/data/l2_counts.json'
CITATION = 'https://doi.org/10.1093/restud/rdaf070'


COPY = {
 'en': dict(title='Synthesize', headline='Party control did not help these forecasts.',
    intro=['Minimum wage and gas tax: the simplest model scored best.'],
    adoption='Forecasting which states raise policy rates', noise='L2 estimates against their own noise bands',
    training="Trained on earlier years; scored given each year's raise count.",
    mw='Minimum wage', gt='Gas tax', party='Party control', economic='Local conditions',
    interval='95% interval', unavailable='Unavailable',
    interval_reason='95% forward intervals unavailable: 54/999 resamples failed.',
    axis='Change in forecast score → higher is better', zero='0: no gain',
    units='Units: natural-log score (nats) per decision year. Local conditions include policy-rate erosion.',
    f1note='Dot: score gain; line: 95% interval.',
    noise_note='Line: original 95% interval. Each row has its own units and scale.', band_label='95% of shuffles',
    missing='Not yet assessed', exceeds='Outside band', within='Within band',
    noise_summary='4 of 12 estimates lie outside their own noise bands.',
    table='Data table', policy='Policy / outcome', estimate='Estimate', threshold='Own threshold', status='Comparison',
    literature='DellaVigna &amp; Kim document stronger partisan diffusion since 2000.',
    next='Next: compare party control with policy-specific need across many policies.',
    method='Method appendix', years='years', raises='raises', scored='scored', baseline='Baseline-only score',
    fallback='12 of 15 references available.',
    levels=['L1 · Screen all policies','L2 · Estimate every suitable design','L3 · Synthesize effects and adoption']),
 'zh': dict(title='综合', headline='党派控制没有帮助这些预测。',
    intro=['最低工资与汽油税：最简模型得分最高。'],
    adoption='预测哪些州会上调政策标准', noise='L2 估计与各自噪声带',
    training='用往年数据训练；给定当年上调州数评分。',
    mw='最低工资', gt='汽油税', party='党派控制', economic='当地条件',
    interval='95% 区间', unavailable='不可用',
    interval_reason='95% 前向区间不可用：54/999 次重抽样失败。',
    axis='预测得分变化 → 越高越好', zero='0：无增益',
    units='单位：每个决策年的自然对数得分（nats）。当地条件包含政策标准的实际购买力侵蚀。',
    f1note='点：得分增量；区间线：95% 区间。',
    noise_note='区间线：原研究 95% 区间。各行单位与尺度不同。', band_label='95% 随机重排',
    missing='尚未评估', exceeds='带外', within='带内',
    noise_summary='12 个估计中，4 个落在各自噪声带外。',
    table='数据表', policy='政策 / 结局', estimate='估计', threshold='自身阈值', status='比较',
    literature='DellaVigna 与 Kim 记录了 2000 年后更强的党派扩散。',
    next='下一步：跨政策比较党派控制与各领域需要的预测贡献。',
    method='方法附录', years='年', raises='次上调', scored='评分', baseline='仅含基线的得分',
    fallback='15 个参照中，12 个可用。',
    levels=['L1 · 筛查全部政策','L2 · 估计全部适合的设计','L3 · 综合效应与采纳规律'])}
CASES = {
 'paid_sick_leave': ('Paid sick leave','带薪病假'), 'medicaid_expansion':('Medicaid expansion','Medicaid 扩张'),
 'cigarette_tax':('Cigarette tax','卷烟税'), 'shall_issue':('Shall-issue carry permits','须发放持枪许可'),
 'minimum_wage_step':('Minimum-wage step','最低工资上调'), 'right_to_work':('Right-to-work laws','工作权法'),
 'renewable_portfolio':('Renewable portfolio standards','可再生能源配额'),
 'death_penalty_abolition':('Death-penalty abolition','废除死刑'),
 'salary_history_ban':('Salary-history bans','薪资历史禁问'), 'smoke_free_air':('Smoke-free air','无烟法规')}
OUTCOMES = [
 ('Private employment','私营部门就业'), ('Weekly earnings per job','每个岗位的周收入'),
 ('Uninsured share','未投保率'), ('Tax-paid cigarette sales per capita','人均完税卷烟销量'),
 ('Violent crime, excluding rape','暴力犯罪（不含强奸）'), ('Restaurant employment','餐饮业就业'),
 ('Restaurant weekly wages','餐饮业周工资'), ('Union membership','工会会员率'), ('Union coverage','工会覆盖率'),
 ('Nonhydro renewable generation','非水电可再生能源发电'), ('Retail electricity price','零售电价'),
 ('Homicide rate','凶杀率'), ('Female/male earnings, hires aged 25–54','25–54 岁新入职者女男收入比'),
 ('Female/male earnings, hires aged 35–54','35–54 岁新入职者女男收入比'), ('Adult smoking prevalence','成人吸烟率')]
UNIT_ZH = {'log points':'对数点', 'proportion':'比例', 'percentage points':'百分点'}

# Mobile value cap: about 9.5 × 13px; reserve 140px for labels plus the 12px gap.
CSS = '''
.synth{max-width:1100px;margin:40px auto 0;color:var(--nx-text)}
.synth h1,.synth h2,.synth h3{color:var(--nx-ink)}
.synth-hero{margin-bottom:44px;max-width:800px}
.synth-kicker{font-size:14px;letter-spacing:.04em;color:var(--nx-accent);margin-bottom:12px}
.synth-hero h1{font:600 clamp(30px,4vw,48px)/1.15 var(--nx-display);letter-spacing:-.02em;text-wrap:balance;margin:0 0 20px}
:lang(zh) .synth-hero h1{line-height:1.4;letter-spacing:0}
.synth-hero p{margin:6px 0;max-width:760px}
:lang(zh) .synth-hero p{word-break:normal;text-wrap:pretty}
:lang(zh) .synth p,:lang(zh) .synth .small{text-wrap:pretty}
.zh-tail{white-space:nowrap}
.synth-levels{display:grid;gap:4px;margin:20px 0;max-width:460px}
.synth-levels .level{display:flex;align-items:center;min-height:44px;padding:4px 12px;border-left:2px solid var(--nx-line);font-size:14px}
.synth-levels .level-current{border-color:var(--nx-accent);background:var(--nx-plate)}
.synth figure{margin:0 0 52px;padding:24px;border:1px solid var(--nx-line);border-radius:12px;min-width:0}
.synth figure>h2{font-size:26px;line-height:1.35;margin-bottom:8px;text-wrap:balance}
.synth figcaption,.synth .small{font-size:13px;line-height:1.65;color:var(--nx-text);margin-top:12px}
.adoption-panels{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:36px;margin-top:24px}
.policy-panel{min-width:0}.policy-panel h3{font-size:21px}.policy-scope{font-size:13px;margin:8px 0 22px}
.interval-reason:empty{display:none}
@media(min-width:820px){.adoption-panels{grid-template-rows:repeat(7,auto);row-gap:0}.policy-panel{display:grid;grid-row:span 7;grid-template-rows:subgrid}.interval-reason:empty{display:block}}
.gain-row{margin:18px 0}.gain-label{display:flex;justify-content:space-between;gap:12px;font-size:15px}
.gain-label b{font-variant-numeric:tabular-nums;color:var(--nx-ink)}
.gain-range{font-size:12px;line-height:1.6;margin-top:2px;min-height:20px}
.synth svg{display:block;width:100%;height:36px;overflow:visible}
.synth .grid-line{stroke:var(--nx-line);stroke-width:1;vector-effect:non-scaling-stroke}
.synth .zero{stroke:var(--nx-muted);stroke-width:1;stroke-dasharray:3 3;vector-effect:non-scaling-stroke}
.synth .whisker{stroke:currentColor;stroke-width:2;vector-effect:non-scaling-stroke}
.party{color:var(--nx-accent)}.economic{color:var(--nx-muted)}
.synth .crossing,.synth .within{color:var(--nx-ink)}
.synth .band{fill:#e1e6ee;stroke:var(--nx-line-2);stroke-width:1;vector-effect:non-scaling-stroke}
.plot-axis{position:relative;height:20px;font-size:12px;font-variant-numeric:tabular-nums;color:var(--nx-muted);margin:0 10px}
.plot-axis span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.plot-axis .tick:first-child{left:0;transform:none}.plot-axis .tick:last-of-type{right:0;transform:none}
.plot-axis .zero-label{position:absolute;top:-20px;transform:translateX(-50%);font-size:12px;line-height:16px;white-space:nowrap}
.adoption-axis{margin-top:24px;container-type:inline-size}
@container(max-width:345.6px){.adoption-axis .tick-secondary{display:none}}
.axis-caption{font-size:13px;margin-top:8px}
.band-key{display:inline-flex;align-items:center;gap:6px;margin-right:10px}
.synth .band-key svg{display:inline-block;width:44px;height:18px;overflow:visible;flex:none}
.band-edge{stroke:var(--nx-muted);stroke-width:1;fill:none}
.noise-summary{font-size:16px;color:var(--nx-ink);margin:12px 0 24px}
.noise-row{display:grid;grid-template-columns:minmax(0,260px) minmax(0,1fr) 150px;gap:20px;align-items:center;padding:13px 0;border-top:1px solid var(--nx-line);min-width:0}
.noise-label{font-size:14px;line-height:1.5}.noise-label b{font-weight:600;color:var(--nx-ink)}
.noise-label span{display:block;font-size:13px;margin-top:3px}.noise-plot{min-width:0}
.noise-value{font-size:13px;line-height:1.6;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.noise-value b{font-size:15px;font-weight:600}
.missing-reference{padding:12px 0;font-size:13px;color:var(--nx-muted)}
.synth details{margin-top:20px;border-top:1px solid var(--nx-line);padding-top:8px}
.synth summary{min-height:44px;display:list-item;cursor:pointer;line-height:44px;color:var(--nx-accent)}
.synth table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:12px}
.synth th,.synth td{padding:8px 5px;border-bottom:1px solid var(--nx-line);text-align:left;vertical-align:top;overflow-wrap:anywhere}
.synth th:first-child{width:30%}.synth td div{margin-bottom:5px}.synth .context{margin-bottom:28px;max-width:1000px}
.synth .context p{margin-bottom:16px}.synth .method-link{display:inline-flex;min-height:44px;align-items:center}
@media(max-width:819px){.adoption-panels{grid-template-columns:1fr;gap:28px}.policy-panel+.policy-panel{border-top:1px solid var(--nx-line);padding-top:24px}.noise-row{grid-template-columns:minmax(0,210px) minmax(0,1fr);gap:8px 20px}.noise-value{grid-column:2}.noise-label{grid-row:span 2}}
@media(max-width:600px){.synth{margin-top:28px}.synth figure{padding:16px;margin-bottom:36px}.synth figure>h2{font-size:23px}.synth-hero{margin-bottom:28px}.noise-row{grid-template-columns:minmax(0,1fr) minmax(0,min(124px,calc(100% - 152px)));gap:8px 12px;align-items:start}.noise-label{grid-column:1;grid-row:1}.noise-value{grid-column:2;grid-row:1;text-align:right}.noise-plot{grid-column:1/-1;grid-row:2}.noise-label span{display:block;margin-left:0}.synth table,.synth tbody,.synth tr,.synth th,.synth td{display:block;width:auto}.synth th:first-child{width:auto}.synth tbody tr{padding:12px 0;border-bottom:1px solid var(--nx-line)}.synth tbody th,.synth tbody td{border:0;padding:4px 0}.synth thead{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.synth td::before{content:attr(data-label);display:block;font-weight:600}}
@media(prefers-reduced-motion:reduce){.synth svg,.synth .noise-row{opacity:1!important;transform:none!important}}
@media print{.synth details{display:block}.synth details>table{display:table}.synth svg{opacity:1!important;transform:none!important}}
'''
MOTION = '''<script>
(function(){
 var motion=window.NXMotion;if(!motion)return;
 document.querySelectorAll('.synth [data-nx-intro]').forEach(function(element){
  var targets=Array.prototype.slice.call(element.querySelectorAll('[data-nx-reveal]'));
  motion.revealOnce(element,targets);
 });
})();
</script>'''



def value(n, digits=2):
    return f'{n:.{digits}f}'.replace('-', '−')


def svg_plot(id_, title, description, estimate, ci, low, high, *, threshold=None, color='party', hollow=False, minimal=False):
    def x(n): return 20 + (n-low)/(high-low)*960
    ticks = (low, -3, 1, high) if (low, high)==(-6,1.5) else (low,high)
    body = '' if minimal else ''.join(f'<line class="grid-line" x1="{x(t):.4f}" x2="{x(t):.4f}" y1="3" y2="33"/>' for t in ticks)
    if threshold is not None:
        body += f'<rect class="band" x="{x(-threshold):.4f}" y="8" width="{x(threshold)-x(-threshold):.4f}" height="20" rx="3"/>'
    body += f'<line class="zero" x1="{x(0):.4f}" x2="{x(0):.4f}" y1="1" y2="35"/>'
    body += f'<g class="plot-marks {color}">'
    if ci:
        caps = '' if minimal else f' M{x(ci[0]):.4f} 12V24 M{x(ci[1]):.4f} 12V24'
        body += f'<path class="whisker" fill="none" d="M{x(ci[0]):.4f} 18H{x(ci[1]):.4f}{caps}"/>'
    # Non-scaling round strokes keep the point at nine screen pixels.
    body += f'<path class="mark" d="M{x(estimate):.4f} 18h.01"/>'
    if hollow:
        body += f'<path class="mark-hole" d="M{x(estimate):.4f} 18h.01"/>'
    body += '</g>'
    return f'<svg class="nx-plot" data-nx-reveal viewBox="0 0 1000 36" preserveAspectRatio="none" role="img" aria-labelledby="{id_}-title {id_}-desc"><title id="{id_}-title">{esc(title)}</title><desc id="{id_}-desc">{esc(description)}</desc>{body}</svg>'


def axis(low, high, *, short=False, thousands=False, zero_label=None, ticks=None):
    fmt = (lambda n: format(n, '.3g').replace('-', '−')) if short else (lambda n: format(n, 'g').replace('-', '−'))
    if thousands:
        fmt = lambda n: format(float(format(n, '.3g')), ',.0f').replace('-', '−')
    ticks = ticks if ticks is not None else (low, -3, 0, high) if zero_label else (low, 0, high)
    # Match the SVG's 20…980 inset across the flexible width, without fixed-pixel drift.
    tick_html = ''.join(f'<span class="tick{" tick-secondary" if zero_label and t == -3 else ""}" data-svg-x="{20+(t-low)/(high-low)*960:.4f}"' +
                        (' data-min-panel-width="360"' if zero_label and t == -3 else '') +
                        f' style="{"right:0;left:auto" if t==high else "left:"+format((t-low)/(high-low)*100,".3f")+"%"}">{fmt(t)}</span>' for t in ticks)
    zero = f'<div class="zero-label" style="left:{(-low)/(high-low)*100:.3f}%">{zero_label}</div>' if zero_label else ''
    return f'<div class="plot-axis {"adoption-axis" if zero_label else ""}" style="margin-left:2%;margin-right:2%" aria-hidden="true">{tick_html}{zero}</div>'


def table_html(id_, headings, rows):
    header = ''.join(f'<th id="{id_}-col-{i}" scope="col">{h}</th>' for i,h in enumerate(headings))
    body = []
    for i,row in enumerate(rows):
        cells = f'<th id="{id_}-row-{i}" scope="row" headers="{id_}-col-0">{row[0]}</th>'
        cells += ''.join(f'<td headers="{id_}-row-{i} {id_}-col-{j}" data-label="{esc(headings[j],quote=True)}">{cell}</td>' for j,cell in enumerate(row[1:],1))
        body.append('<tr>'+cells+'</tr>')
    return f'<table><thead><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table>'



def adoption_figure(lang, results):
    c = COPY[lang]; panels=[]; rows=[]; connector='至' if lang=='zh' else 'to'
    for r in results:
        name=c[r['key']];s=r['scope'];f=r['forward']; yrs=s['decision_years']
        scope = (f'{int(s["events"])} raises in sample · {min(yrs)}–{max(yrs)}<br>{len(f["S"])} scored years · {int(s["scored_events"])} scored raises' if lang=='en' else
                 f'{min(yrs)}–{max(yrs)} 年 · 样本内 {int(s["events"])} 次上调<br>{len(f["S"])} 个评分年份 · {int(s["scored_events"])} 次上调')
        panel=f'<section class="policy-panel"><h3>{name}</h3><p class="policy-scope">{scope}</p>'
        reason = c['interval_reason'] if r['key'] == 'mw' else ''
        hidden = '' if reason else ' aria-hidden="true"'
        panel+=f'<p class="small interval-reason"{hidden}>{reason}</p>'
        for key,label,color in (('Delta_P',c['party'],'party'),('Delta_E',c['economic'],'economic')):
            gain=f['gains'][key];iv=r['intervals'][key];ci=[iv['lower'],iv['upper']] if iv['available'] else None
            interval=f'{c["interval"]}: {value(ci[0])} {connector} {value(ci[1])}' if ci else c['interval_reason']
            panel+=f'<div class="gain-row"><div class="gain-label"><span>{label}</span><b>{value(gain)}</b></div>'
            if ci:
                panel+=f'<p class="gain-range">{interval}</p>'
            else:
                panel+='<p class="gain-range" aria-hidden="true"></p>'
            panel+=svg_plot(r['key']+'-'+key,name+' / '+label,f'{c["axis"]}. {value(gain)}. {interval}',gain,ci,-6,1.5,color=color)+'</div>'
            interval_cell=f'{value(ci[0],6)} {connector} {value(ci[1],6)}' if ci else c['unavailable']
            rows.append((f'{name} / {label}',value(gain,6),interval_cell))
        panel+=axis(-6,1.5,zero_label=c['zero'])+f'<p class="axis-caption">{c["axis"]}</p></section>'
        panels.append(panel)
    table=table_html('adoption-data',(c['policy'],c['estimate'],c['interval']),rows)
    return f'<figure id="adoption-test" data-nx-intro><h2>{c["adoption"]}</h2><p class="small">{c["training"]}</p><p class="small figure-key">{c["f1note"]}</p><div class="adoption-panels">{"".join(panels)}</div><details><summary>{c["table"]}</summary><p class="small">{c["units"]}</p><p class="small">{c["interval_reason"]}</p>{table}</details></figure>'


def noise_figure(lang, d):
    c=COPY[lang];idx=lang=='zh';rows=[];table=[];connector='至' if idx else 'to'
    assert [r['order'] for r in d['rows']] == list(range(1,16))
    assert (d['planned_outcomes'],sum(r['reference_available'] for r in d['rows']),sum(r['exceeds'] is True for r in d['rows']))==(15,12,4)
    for r,outcome in zip(d['rows'],OUTCOMES):
        case=CASES[r['case']][idx];unit=UNIT_ZH[r['original_units']] if idx else r['original_units']
        full_outcome = ('Log average restaurant weekly wage','餐饮业平均周工资的对数')[idx] if r['order']==7 else r['outcome_label'][lang]
        status=c['missing'] if not r['reference_available'] else c['exceeds'] if r['exceeds'] else c['within']
        color='crossing' if r['exceeds'] else 'within'
        if r['reference_available']:
            label=f'<div class="noise-label"><b>{esc(case)}</b><span>{esc(outcome[idx])} · {unit}</span></div>'
            threshold=r['reference_threshold'];extent=max(abs(r['ci95'][0]),abs(r['ci95'][1]),abs(r['estimate']),threshold)*1.12
            title=case+' / '+full_outcome
            description=f'{full_outcome}. {c["estimate"]} {value(r["estimate"],6)}; {c["interval"]} {value(r["ci95"][0],6)} {connector} {value(r["ci95"][1],6)}; {c["threshold"]} ±{value(threshold,6)} {unit}. {status}.'
            plot=svg_plot('noise-'+str(r['order']),title,description,r['estimate'],r['ci95'],-extent,extent,threshold=threshold,color=color,hollow=not r['exceeds'])+axis(-extent,extent,short=True)
            # The renewable-share estimate must remain legible in its raw units.
            digits=5 if r['order']==10 else 3
            readout=f'<b>{value(r["estimate"],digits)}</b><br><span class="{color}">{status}</span>'
            rows.append(f'<div class="noise-row" data-outcome-order="{r["order"]}" data-reference="true">{label}<div class="noise-plot">{plot}</div><div class="noise-value">{readout}</div></div>')
        comparison=f'{value(r["reference_threshold"],6)}<br>{status}' if r['reference_available'] else c['missing']
        estimate=f'<div>{value(r["estimate"],6)} {unit}</div><div>{c["interval"]}: [{value(r["ci95"][0],6)}, {value(r["ci95"][1],6)}]</div>'
        table.append((f'{esc(case)} / {esc(full_outcome)}',estimate,comparison))
    data_table=table_html('noise-data',(c['policy'],c['estimate']+' / '+c['interval'],c['threshold']+' / '+c['status']),table)
    band_key=f'<span class="band-key"><svg viewBox="0 0 44 18" aria-hidden="true"><rect class="band" x="6" y="4" width="32" height="10" rx="2"/><path class="band-edge" d="M6 2V16 M38 2V16"/></svg><span>{c["band_label"]}</span></span>'
    return f'<figure id="noise-floor" data-nx-intro><h2>{c["noise"]}</h2><p class="small figure-key">{band_key}{c["noise_note"]}</p><p class="noise-summary">{c["noise_summary"]}</p>{"".join(rows)}<details><summary>{c["table"]}</summary><p class="small">{c["fallback"]}</p>{data_table}</details></figure>'


def page(lang, results, noise):
    c=COPY[lang];slots=render_slots(lang,'synthesize',features={'github':True,'examples':True})
    headline='党派控制没有帮助<wbr>这些预测。' if lang=='zh' else c['headline']
    home='/zh/policy_db.html' if lang=='zh' else '/policy_db.html'
    levels=f'<nav class="synth-levels" aria-label="{c["title"]}"><a class="level" href="{home}#explore">{c["levels"][0]}</a><a class="level" href="/viz/{lang}/worked_examples.html">{c["levels"][1]}</a><div class="level level-current" aria-current="step">{c["levels"][2]}</div></nav>'
    html=f'''<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{c['title']} · Natural Experiment Scanner</title>
{marked('style',slots['style'])}<style>{CSS}</style>{marked('motion',slots['motion'])}</head>
<body><div class="page">{marked('header',slots['header'])}{marked('nav',slots['nav'])}
<main class="synth"><div class="synth-hero"><p class="synth-kicker">L3 · {c['title']}</p><h1 data-nx-wrap="phrase">{headline}</h1>{''.join('<p>'+p+'</p>' for p in c['intro'])}</div>
{levels}{adoption_figure(lang,results)}{noise_figure(lang,noise)}
<div class="context"><p><a href="{CITATION}" rel="noopener">{c['literature']}</a></p><p>{c['next']}</p><a class="method-link" href="{'/zh' if lang=='zh' else ''}/documentation.html#synthesis-appendix">{c['method']} →</a></div></main>
{marked('footer',slots['footer'])}</div>{MOTION}</body></html>
'''
    return keep_zh_tails(html) if lang == 'zh' else html


def keep_zh_tails(html):
    """Keep the last four CJK characters of each paragraph together, including links."""
    def paragraph(match):
        body = re.sub(r'([\u3400-\u9fff]{2,4}[。！？]?)(?=(?:</[^>]+>)*$)',
                      r'<span class="zh-tail">\1</span>', match[2])
        return match[1] + body + '</p>'
    return re.sub(r'(<p\b[^>]*>)(.*?)</p>', paragraph, html, flags=re.S)


def method_block(lang):
    if lang == 'en':
        title = 'Synthesis appendix'
        paras = [
            'L1 aims to screen all policies for DiD, RDD and RCT designs; L2 aims to estimate every suitable design; L3 combines L2 terminal states and independent adoption panels.',
            'Pipeline designs check official treatment histories and outcome data, then freeze the plan and five AI forecasts before execution; amendments are logged. Current pipeline DiD estimates use Callaway–Sant’Anna, cohort-size weights and never-treated controls, with pre-trend and placebo stop rules; support requires at least 6 treated states (8 where registered), 2 adoption cohorts, 5 never-treated controls, 3 pre-periods and 2 post-periods.',
            'The 27 primary result records specify approximate nominal 95% Webb wild-cluster-t intervals (12), control-path Conley–Taber intervals (11), or point estimates with randomization inference only (4). Interval selection requires at least 93% simulated coverage across the registered calibration designs; the amended extreme-risk-protection-order interval lacks calibration for its changed geometry, and the energy-fund unit is uncertified, so neither interval is displayed.',
            'The noise floor is the 95th percentile of absolute estimates from randomly reassigning adoption dates across eligible states; its placebo band is separate from the confidence interval. The development synthesis compares primary estimates with this floor, scores locked forecasts and counts reasons designs were not estimated. The completed frozen pooled study covered 35 policies and 597 adoption events; all three intervals include zero.',
        ]
    else:
        title = '综合研究附录'
        paras = [
            'L1 目标是筛查全部政策的 DiD、RDD 与 RCT 设计；L2 目标是估计全部适合的设计；L3 汇总 L2 终态与独立采纳面板。',
            '流水线设计核查官方处理史与结局数据，在执行前冻结方案和五个 AI 预测，并记录修订。当前流水线 DiD 采用 Callaway–Sant’Anna 估计，按队列大小加权，以从未处理州为对照，按前趋势与安慰剂规则停止；支持至少需要 6 个处理州（登记为 8 州时从其规定）、2 个采纳队列、5 个从未处理对照州、3 个处理前期与 2 个处理后期。',
            '27 项流水线主结果记录的方法为：12 项近似名义 95% Webb 野生聚类 t 区间，11 项对照路径 Conley–Taber 区间，4 项仅点估计加随机化推断。区间方法须在全部预设校准设计中达到至少 93% 的模拟覆盖率；极端风险保护令修订后的区间未按新样本结构校准，能源基金的来源单位未认证，故这两个区间不显示。',
            '噪声底线是在符合条件的州之间随机重排采纳日期，所得估计绝对值的第 95 百分位；其安慰剂带与置信区间分别表示不同对象。开发版综合将主结局估计与此底线比较，同时评定锁定预测，统计设计未获估计的原因。已完成的冻结合并研究涵盖 35 项政策、597 次采纳事件；三个区间均包含零。',
        ]
    return '<!-- SYNTH-METHOD:START -->\n<section id="synthesis-appendix"><h2>' + title + '</h2>' + ''.join('<p>' + p + '</p>' for p in paras) + '</section>\n<!-- SYNTH-METHOD:END -->'


def write_or_check(path, content, check):
    if check:
        if not path.exists() or path.read_text() != content:
            raise ValueError('stale output: ' + str(path.relative_to(ROOT)))
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(content,encoding='utf-8')


# V2 reads completed records only. Legacy rendering helpers remain available for
# existing consumers and their regression tests; candidate CLI uses page_v3.
NEW_CASES = [
 ('pfml', 'Paid family and medical leave', '带薪家庭与医疗假', 'Women 20–44 employment', '20–44 岁女性就业率', 'labor', 'batch1'),
 ('eitc_first_refundable', 'Refundable earned income tax credit (EITC)', '可退还劳动所得税收抵免（EITC）', 'Women 25–54 with own children under 18: employment', '25–54 岁、家中有本人未满 18 岁子女的女性就业率', 'labor', 'batch1'),
 ('btb_private', 'Private ban-the-box', '私营雇主初始申请犯罪记录禁问', 'Men 25–54 employment', '25–54 岁男性就业率', 'labor', 'batch2'),
 ('film_refundable', 'Refundable film credits', '可退还影视税收抵免', 'Private film-industry jobs', '私营影视行业岗位', 'labor', 'batch2'),
 ('medical_cannabis', 'Medical cannabis', '医用大麻', 'Adults 25–64 employment', '25–64 岁成年人就业率', 'health', 'batch2'),
 ('automatic_voter', 'Automatic voter registration', '自动选民登记', 'Citizens 18+: reported turnout', '18 岁及以上公民报告投票率', 'civic', 'batch2'),
 ('public_btb', 'Public-sector ban-the-box', '公共部门初始申请犯罪记录禁问', 'Men 25–54 employment', '25–54 岁男性就业率', 'labor', 'batch3'),
 ('mw_indexing', 'Minimum-wage CPI indexing', '最低工资 CPI 指数化', 'Persons 16–24 employment', '16–24 岁人口就业率', 'labor', 'batch3'),
]
DOMAINS = {
 'labor': ('Labor & work', '劳动与就业'),
 'health': ('Health', '健康'),
 'safety': ('Public safety', '公共安全'),
 'energy': ('Energy', '能源'),
 'civic': ('Voting', '选举'),
}
PROTOTYPE_DOMAINS = {
 'paid_sick_leave': 'labor', 'minimum_wage_step': 'labor',
 'right_to_work': 'labor', 'salary_history_ban': 'labor',
 'medicaid_expansion': 'health', 'cigarette_tax': 'health', 'smoke_free_air': 'health',
 'shall_issue': 'safety', 'death_penalty_abolition': 'safety', 'renewable_portfolio': 'energy',
}
V2_COPY = {
 'en': dict(headline='Employment estimates and reassignment thresholds.',
  intro='Family and medical leave, EITC and private ban-the-box. The pooled adoption study reached no registered conclusion.',
  noise='Policy estimates and 95% reassignment thresholds',
  summary='18 designs · 23 outcomes · 20 reassignment comparisons · 4 beyond threshold',
  note='Points: estimates; lines: original nominal 95% intervals. Grey bands: 95% reassignment thresholds, a cutoff containing 95% of absolute estimates among feasible random treatment reassignments. Each row has its own unit and scale. Filled points are beyond threshold; hollow points stay within it.',
  missing='Reassignment comparison pending', labor='Blue marks: labor policies.',
  adoption='Party control and local need',
  model='Model: policy adoption in each state-year at risk, with policy fixed effects.',
  pooled_units='Log odds per 1 SD',
  pooled_note='Marginal 97.5% intervals, as registered.',
  pooled_result='All three intervals include zero.',
  party='Party control', need='Local need', difference='Coefficient difference: party minus need',
  data_scope='Adoption dates come from published studies; the panels use different samples.',
  dk='DellaVigna and Kim: descriptive replication', dk_note='From 2000, adoption is positively associated with prior adoption in states sharing the same government-control category: unified Democratic, unified Republican or divided.',
  late='Matching-control adoption share, from 2000', change='Change from before 2000',
  dk_units='Log odds per unit of matching-control adoption share · 95% intervals',
  dk_result='The change interval includes zero.',
  earlier='Earlier adoption prototypes',
  earlier_note='Minimum wage and gas tax: the baseline-only model scored best.',
  next='The next batches extend coverage: Screen all policies for DiD, RDD and RCT designs; Estimate every suitable design; Synthesize across L2 results and independent adoption studies.',
  table='Data and study records', estimate='Estimate', score='Score gain', interval='Interval', threshold='95% reassignment threshold', policy='Policy / outcome',
  scope='Eight new frozen estimates plus prototypes; the collection grows with each batch.',
  no_interval='no interval (pre-registered)',
  within='Within threshold', exceeds='Beyond threshold', nominal='Nominal 95%', approximate='Approx. nominal 95%',
  mw_reading='Minimum-wage step: the weekly-pay estimate is positive; employment remains imprecisely estimated.',
  deviation='Deviation 001: sensitivity-loader fix; amended run completed; primary and Class B outputs unchanged.',
  deviation_record='Original batch1_20261006_final: STOPPED_SUPPORT. Amended batch1_20261007_dev001: PUBLISHABLE_EXAMPLE. Both runs retained; primary and Class B outputs byte-identical; the registered 11-month sensitivity completed.'),
 'zh': dict(headline='就业估计与重排阈值。',
  intro='家庭与医疗假、EITC、私营雇主初始申请犯罪记录禁问。合并采纳研究未得出预注册结论。',
  noise='政策估计与 95% 重排阈值', summary='18 项设计 · 23 个结局 · 20 项重排比较 · 4 项超阈值',
  note='点为估计，线为原研究名义 95% 区间。灰带为 95% 重排阈值，即可估计的随机处理重排中，估计绝对值有 95% 不超过该值。各行单位与尺度不同。实心点超过阈值，空心点未超过阈值。',
  missing='重排比较待补', labor='蓝色标记：劳动政策。',
  adoption='党派控制与当地需要',
  model='模型：各风险州年是否采纳政策，含政策固定效应。',
  pooled_units='每增加 1 个标准差的对数胜算变化',
  pooled_note='按预注册规格使用边际 97.5% 区间。',
  pooled_result='三个区间均包含零。',
  party='党派控制', need='当地需要', difference='系数差：党派减需要',
  data_scope='采纳日期来自已发表研究；两面板使用不同样本。',
  dk='DellaVigna 与 Kim：描述性复现', dk_note='自 2000 年起，一州采纳与执政类型相同州的既有采纳呈正关联；类型为民主党统一执政、共和党统一执政或分立政府。',
  late='自 2000 年起执政类型相同州采纳占比', change='相对 2000 年前的变化',
  dk_units='执政类型相同州采纳占比每单位的对数胜算 · 95% 区间', dk_result='变化区间包含零。',
  earlier='早期采纳原型', earlier_note='最低工资与汽油税：仅含基线的模型得分最高。',
  next='后续批次继续扩大覆盖：筛查全部政策的 DiD、RDD 与 RCT 设计，估计每项适合的设计，再综合 L2 结果与独立采纳研究。',
  table='数据与研究记录', estimate='估计', score='得分增量', interval='区间', threshold='95% 重排阈值', policy='政策 / 结局',
  scope='八项新增冻结估计与原型，随批次扩展。',
  no_interval='无区间（预注册）',
  within='阈值内', exceeds='超阈值', nominal='名义 95%', approximate='近似名义 95%',
  mw_reading='最低工资上调：餐饮业周收入点估计为正；就业估计仍不精确。',
  deviation='Deviation 001：修复敏感性加载；修订运行完成；主结果与 B 类诊断未变。',
  deviation_record='原运行 batch1_20261006_final：STOPPED_SUPPORT。修订运行 batch1_20261007_dev001：PUBLISHABLE_EXAMPLE。两次运行均保留；主结果与 B 类诊断逐字节相同；预注册的 11 个月敏感性已完成。'),
}

# Compact evidence is embedded in the existing table, so new batches need no new page surface.
STUDY_RECORDS = {
 'pfml': ('1994–2024', ('First fully exposed calendar year; 2020–2021 omitted', '首个完整暴露日历年；剔除 2020–2021 年'), ('Census Basic Monthly CPS', '人口普查局月度 CPS')),
 'eitc_first_refundable': ('2000–2024', ('First applicable refundable tax year; 2020–2021 omitted', '首个适用可退还税年；剔除 2020–2021 年'), ('Census Basic Monthly CPS', '人口普查局月度 CPS')),
 'btb_private': ('1994–2020', ('First full year after operative year; all employment sectors', '启动年之后首个完整年；全部就业部门'), ('Census Basic Monthly CPS', '人口普查局月度 CPS')),
 'film_refundable': ('2001–2012', ('First full year after operative year; private NAICS 5121 workplace jobs', '启动年之后首个完整年；私营 NAICS 5121 工作地岗位'), ('BLS QCEW', '劳工统计局 QCEW')),
 'medical_cannabis': ('1994–2020', ('First full year after operative year', '启动年之后首个完整年'), ('Census Basic Monthly CPS', '人口普查局月度 CPS')),
 'automatic_voter': ('1994–2024', ('Election waves after operative year', '启动年之后的选举轮次'), ('Census published citizen turnout', '人口普查局公布的公民投票率')),
 'public_btb': ('1994–2020', ('First full year after operative year; all employment sectors', '启动年之后首个完整年；全部就业部门'), ('Census Basic Monthly CPS', '人口普查局月度 CPS')),
 'mw_indexing': ('1994–2014', ('First full year after operative CPI formula year; indexed-law package includes initial wage-level step', 'CPI 公式启动年之后首个完整年；指数化法规组合含初始工资水平调整'), ('Census Basic Monthly CPS', '人口普查局月度 CPS')),
}


def load_l2():
    """Preserve raw outcomes and frozen statuses across expanding batches."""
    d = json.loads(NOISE.read_text())
    rows = []
    for r, outcome in zip(d['rows'], OUTCOMES):
        r = dict(r)
        r['domain'] = PROTOTYPE_DOMAINS[r['case']]
        r['name'] = CASES[r['case']]
        r['label'] = outcome
        r['full_label'] = (('Log average restaurant weekly wage', '餐饮业平均周工资的对数')
                           if r['order'] == 7 else tuple(r['outcome_label'][lang] for lang in ('en', 'zh')))
        rows.append(r)
    for key, en, zh, out_en, out_zh, domain, batch in NEW_CASES:
        directory = {'batch1': 'scripts/l2_batch1/results/batch1_20261007_dev001',
                     'batch2': 'scripts/l2_batch2/results/batch2_20261007_final',
                     'batch3': 'scripts/l2_batch3/results/batch3_20261007_final'}[batch]
        source = ROOT / directory / (key + '.json')
        result = json.loads(source.read_text())
        if result['status'] != 'PUBLISHABLE_EXAMPLE':
            raise ValueError('nonpublishable result: ' + key)
        primary = result['registered_results']['primary']['result']['overall']
        ref = result['noise_floor']
        rows.append(dict(order=len(rows)+1, case=key, name=(en,zh), label=(out_en,out_zh),
          full_label=(out_en,out_zh), domain=domain, estimate=primary['att'], ci95=primary['ci95'],
          original_units='jobs' if key == 'film_refundable' else 'percentage points',
          reference_available=True, reference_threshold=ref['absolute_cutoff'],
          exceeds=ref['strictly_exceeds_cutoff'], interval_metadata=primary.get('interval'),
          ri_p=ref['p_two_sided'] if batch == 'batch3' else None,
          provenance=dict(result_path=str(source.relative_to(ROOT)))))
    if (len(set(r['case'] for r in rows)), len(rows), sum(r['reference_available'] for r in rows),
        sum(r['exceeds'] is True for r in rows)) != (18,23,20,4):
        raise ValueError('unexpected L2 display roster')
    return rows


def interval_name(lang, row):
    meta = row.get('interval_metadata') or {}
    approximate = meta.get('approximate') or 'Approximate' in (row.get('interval_label') or '')
    return V2_COPY[lang]['approximate' if approximate else 'nominal']


def noise_figure_v2(lang, rows):
    c=V2_COPY[lang]; idx=lang=='zh'; body=[]; table=[]; punctuation='：' if idx else ': '
    for domain, labels in DOMAINS.items():
        body.append(f'<h3 class="domain-title">{labels[idx]}</h3>')
        for r in sorted((row for row in rows if row['domain']==domain), key=lambda row: (row['order'] <= 15, row['order'])):
            unit=({'jobs':'个岗位', **UNIT_ZH}[r['original_units']] if idx else r['original_units'])
            name=r['name'][idx]; outcome=r['label'][idx]; full=r['full_label'][idx]
            status=c['missing'] if not r['reference_available'] else c['exceeds'] if r['exceeds'] else c['within']
            record_id='study-'+str(r['order'])
            policy=f'<a href="#{record_id}">{esc(name)}</a>' if r['case'] in STUDY_RECORDS else esc(name)
            display_unit='百分点' if idx else 'percentage points'
            display_point=r['estimate']; display_ci=r['ci95']
            if r['case']=='medicaid_expansion':
                unit=display_unit; display_point*=100; display_ci=[v*100 for v in r['ci95']]
            label=f'<div class="noise-label"><b>{policy}</b><span>{esc(outcome)} · {unit}</span></div>'
            digits=2 if r['case'] in ('medicaid_expansion','film_refundable') else 5 if r['order']==10 else 1 if unit in ('jobs','个岗位') else 3
            readout=f'<b>{value(display_point,digits)}</b><br><span>{status}</span>'
            visible_interval=''
            if r['case'] in ('film_refundable','medicaid_expansion'):
                visible_interval=f'<div class="row-interval">{interval_name(lang,r)}{punctuation}[{value(display_ci[0],2)}, {value(display_ci[1],2)}]</div>'
            if r['reference_available']:
                threshold=r['reference_threshold'];extent=max(*(abs(v) for v in (r['ci95'] or [])),abs(r['estimate']),threshold)*1.12
                interval=(f'{interval_name(lang,r)} [{value(r["ci95"][0],6)}, {value(r["ci95"][1],6)}]' if r['ci95'] else c['no_interval'])
                description=f'{full}. {c["estimate"]} {value(r["estimate"],6)}; {interval}; {c["threshold"]} ±{value(threshold,6)} {unit}. {status}.'
                color='labor-mark' if domain=='labor' else 'within'
                plot=svg_plot('v2-noise-'+str(r['order']),name+' / '+full,description,r['estimate'],r['ci95'],-extent,extent,threshold=threshold,color=color,hollow=not r['exceeds'])
                plot+=axis(-extent,extent,short=True,thousands=r['case']=='film_refundable')
            else:
                plot='<div class="missing-reference" aria-hidden="true"></div>'
            reading=f'<p class="small row-reading">{c["mw_reading"]}</p>' if r['order']==7 else ''
            body.append(f'<div class="noise-row {"labor-row" if domain=="labor" else ""}" data-outcome-order="{r["order"]}" data-case="{r["case"]}" data-reference="{str(r["reference_available"]).lower()}">{label}<div class="noise-plot">{plot}</div><div class="noise-value">{readout}</div>{visible_interval}{reading}</div>')
            raw_unit=({'jobs':'个岗位', **UNIT_ZH}[r['original_units']] if idx else r['original_units'])
            compare=f'±{value(r["reference_threshold"],6)} {raw_unit}<br>{status}' if r['reference_available'] else c['missing']
            interval=(f'{interval_name(lang,r)}{punctuation}[{value(r["ci95"][0],6)}, {value(r["ci95"][1],6)}]' if r['ci95'] else c['no_interval'])
            estimate=f'{value(r["estimate"],6)} {raw_unit}<br>{interval}'
            if r.get('ri_p') is not None:
                compare+=f'<br>RI p = {value(r["ri_p"],3)}'
            meta=r.get('interval_metadata') or {}
            evidence=esc(meta.get('method') or '')
            if meta.get('simulated_coverage') is not None:
                evidence+=(' · 模拟覆盖率 ' if idx else ' · Simulated coverage ')+f'{meta["simulated_coverage"]:.0%}'
            estimate+=f'<div class="small">{evidence}</div>'
            record=f'<span id="{record_id}">{esc(name)} / {esc(full)}</span>'
            if r['case'] in STUDY_RECORDS:
                window, clock, source=STUDY_RECORDS[r['case']]
                record+=f'<div class="small">{window} · {clock[idx]} · {source[idx]}</div>'
            if r['case']=='eitc_first_refundable':
                population=('Women aged 25–54 with a CPS-observed own unmarried child under 18 in the household', '25–54 岁、CPS 记录家中有本人未满 18 岁未婚子女的女性')[idx]
                record+=f'<div class="small">{population}</div>'
            if r['case'] in ('pfml','eitc_first_refundable'):
                record+='<div><a href="#deviation-001">Deviation 001</a></div>'
            table.append((record,estimate,compare))
    data=table_html('v2-noise-data',(c['policy'],c['estimate']+' / '+c['interval'],c['threshold']),table)
    provenance=f'<p class="small"><a href="#deviation-001">{c["deviation"]}</a></p>'
    return f'<figure id="noise-floor" data-nx-intro><h2>{c["noise"]}</h2><p class="small">{c["scope"]}</p><p class="noise-summary">{c["summary"]}</p><p class="small figure-key">{c["note"]}{"" if idx else " "}{c["labor"]}</p>{provenance}{"".join(body)}<details><summary>{c["table"]}</summary><p id="deviation-001" class="small">{c["deviation_record"]}</p>{data}</details></figure>'


def pooled_figure(lang, d, earlier):
    c=V2_COPY[lang]; body=[]; table=[]; colon='：' if lang=='zh' else ': '
    for key in ('party','need','difference'):
        r=d['H1'][key];ci=[r['lower'],r['upper']];label=c[key]
        body.append(f'<div class="gain-row"><div class="gain-label"><span>{label}</span><b>{value(r["estimate"],3)}</b></div><p class="gain-range">97.5%{colon}[{value(ci[0],3)}, {value(ci[1],3)}]</p>'+svg_plot('pooled-'+key,label,c['pooled_note'],r['estimate'],ci,-.35,.35,color='within',minimal=True)+'</div>')
        table.append((label,value(r['estimate'],6),f'97.5%{colon}['+value(ci[0],6)+', '+value(ci[1],6)+']'))
    scope = (f'{d["fit_scope"]["policies"]} policies · {d["fit_scope"]["events"]} adoption events' if lang=='en' else f'{d["fit_scope"]["policies"]} 项政策 · {d["fit_scope"]["events"]} 次采纳事件')
    primary='<section class="pooled-primary"><p class="policy-scope">'+scope+'</p><p class="small">'+c['pooled_note']+'</p>'+''.join(body)+axis(-.35,.35)+f'<p class="small">{c["pooled_units"]}</p></section>'
    h0=d['H0']; small=[]
    for key,label in (('late_A',c['late']),('late_minus_early_A',c['change'])):
        point=h0['point'][key];ci=h0['inference']['intervals'][key]
        small.append(f'<div class="gain-row"><div class="gain-label"><span>{label}</span><b>{value(point)}</b></div><p class="gain-range">95%{colon}[{value(ci[0])}, {value(ci[1])}]</p>'+svg_plot('dk-'+key,label,c['dk_units'],point,ci,-.3,2.1,color='within',minimal=True)+'</div>')
        table.append((label,value(point,6),f'95%{colon}['+value(ci[0],6)+', '+value(ci[1],6)+']'))
    replication=f'<section class="dk-panel"><h3>{c["dk"]}</h3><p class="small">{c["dk_note"]}</p>{"".join(small)}{axis(-.3,2.1,ticks=(-.3,2.1))}<p class="small">{c["dk_units"]}{"。" if lang=="zh" else ". "}{c["dk_result"]}</p><a class="method-link" href="{CITATION}" rel="noopener">DellaVigna &amp; Kim →</a></section>'
    old_rows=[]
    for r in earlier:
        for key,label in (('Delta_P',COPY[lang]['party']),('Delta_E',COPY[lang]['economic'])):
            iv=r['intervals'][key];interval=(f'95%{colon}['+value(iv['lower'],6)+', '+value(iv['upper'],6)+']') if iv['available'] else COPY[lang]['interval_reason']
            old_rows.append((COPY[lang][r['key']]+' / '+label,value(r['forward']['gains'][key],6),interval))
    old=table_html('earlier-data',(c['policy'],c['score'],c['interval']),old_rows)
    return f'<figure id="adoption-test" data-nx-intro><h2>{c["adoption"]}</h2><div class="pooled-panels">{primary}{replication}</div><details><summary>{c["table"]}</summary><p class="small">{c["data_scope"]}</p><p class="small">{c["model"]}</p>{table_html("pooled-data",(c["policy"],c["estimate"],c["interval"]),table)}</details><details><summary>{c["earlier"]}</summary><p class="small">{c["earlier_note"]}</p><p class="small">{COPY[lang]["units"]}</p>{old}</details></figure>'


V2_CSS = CSS + '''
:lang(zh) .synth p,:lang(zh) .synth .noise-label,:lang(zh) .synth .noise-label span,:lang(zh) .synth .noise-value,:lang(zh) .synth .small{line-height:1.75}
.synth .domain-title{font-size:17px;line-height:1.5;margin:26px 0 8px}
.synth .labor-mark{color:var(--nx-accent)}
.synth .noise-label a{color:var(--nx-ink);text-decoration:underline;text-underline-offset:3px}
:lang(zh) .synth-hero h1[data-nx-wrap=phrase]{word-break:normal;overflow-wrap:anywhere}
.synth .noise-label .zh-tail{display:inline}
.synth .gain-label span{min-width:0}
.synth .gain-label b{flex-shrink:0}
.synth .row-reading{grid-column:1/-1;margin:0}
.synth .row-interval{grid-column:2/-1;font-size:12px;line-height:1.75}
@media(max-width:600px){.synth .row-interval{grid-column:1/-1;grid-row:3}}
.pooled-panels{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:36px;margin-top:20px}
.pooled-primary,.dk-panel{min-width:0}.dk-panel{border-left:1px solid var(--nx-line);padding-left:28px}
.dk-panel h3{font-size:18px;line-height:1.5}.pooled-primary .policy-scope{font-size:17px;margin-bottom:8px}
@media(max-width:819px){.pooled-panels{grid-template-columns:1fr;gap:24px}.dk-panel{border-left:0;border-top:1px solid var(--nx-line);padding:24px 0 0}}
'''


def page_v2(lang, pooled, rows, earlier):
    c=V2_COPY[lang]; slots=render_slots(lang,'synthesize',features={'github':True,'examples':True})
    home='/zh/policy_db.html' if lang=='zh' else '/policy_db.html'
    levels=f'<nav class="synth-levels" aria-label="{COPY[lang]["title"]}"><a class="level" href="{home}#explore">{COPY[lang]["levels"][0]}</a><a class="level" href="/viz/{lang}/worked_examples.html">{COPY[lang]["levels"][1]}</a><div class="level level-current" aria-current="step">{COPY[lang]["levels"][2]}</div></nav>'
    html=f'''<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{COPY[lang]['title']} · Natural Experiment Scanner</title>
{marked('style',slots['style'])}<style>{V2_CSS}</style>{marked('motion',slots['motion'])}</head>
<body><div class="page">{marked('header',slots['header'])}{marked('nav',slots['nav'])}
<main class="synth"><div class="synth-hero"><p class="synth-kicker">L3 · {COPY[lang]['title']}</p><h1 data-nx-wrap="phrase">{c['headline']}</h1><p>{c['intro']}</p></div>
{levels}{noise_figure_v2(lang,rows)}{pooled_figure(lang,pooled,earlier)}
<div class="context"><p>{c['next']}</p></div></main>
{marked('footer',slots['footer'])}</div>{MOTION}{EVIDENCE_HOOK}</body></html>
'''
    return keep_zh_tails(keep_zh_terms(html)) if lang=='zh' else html


def keep_zh_terms(html):
    def text_node(match):
        return re.sub(r'重排阈值|重排比较|重排|区间|百分点|禁问|阈值|采纳|对数点|研究', r'<span class="zh-tail">\g<0></span>', match[0])
    # Only text nodes; attributes, scripts and CSS must retain their exact bytes.
    parts=re.split(r'(<script\b.*?</script>|<style\b.*?</style>|<[^>]*>)', html, flags=re.S)
    return ''.join(part if part.startswith('<') else text_node(re.match(r'[\s\S]*',part)) for part in parts)


EVIDENCE_HOOK = """<script>
(function(){
  function revealEvidence(){
    var target=document.getElementById(location.hash.slice(1));
    if(!target)return;
    for(var el=target;el;el=el.parentElement)if(el.tagName==='DETAILS')el.open=true;
    target.scrollIntoView({block:'start'});
  }
  window.addEventListener('hashchange',revealEvidence);
  revealEvidence();
})();
</script>"""


V3_CSS = V2_CSS + '''
.synth-hero h1{font-family:var(--nx-sans,system-ui,sans-serif);font-size:32px;margin-bottom:12px}
.synth .share-axis{display:flex;justify-content:space-between;font-size:12px;color:var(--nx-muted);margin:0 10px}
.synth .stacked-counts{display:flex;height:44px;color:white;font-size:20px;font-weight:600}
.synth .stacked-counts span{display:flex;align-items:center;justify-content:center;background:var(--nx-accent)}
.synth .stacked-counts span+span{background:#64748b}
.synth .gate-row .aggregate-track{background:transparent}
.synth .aggregate-chart{gap:14px;margin:18px 0}
.synth .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}

.synth .aggregate-meta{font-size:13px;color:var(--nx-muted);margin:0 0 22px}
.synth .aggregate-chart{margin:24px 0;display:grid;gap:22px}
.synth .aggregate-row{min-width:0}
.synth .aggregate-dot{fill:none;stroke:var(--nx-accent);stroke-width:10;stroke-linecap:round;vector-effect:non-scaling-stroke}
.synth .aggregate-label{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:7px;font-size:15px;line-height:1.5}
.synth .aggregate-label span{min-width:0;overflow-wrap:anywhere}
.synth .aggregate-label b{white-space:nowrap;font-variant-numeric:tabular-nums;font-weight:600;color:var(--nx-ink)}
.synth .aggregate-track{height:18px;background:var(--nx-plate);border-radius:3px;overflow:hidden}
.synth .aggregate-bar{display:block;height:100%;background:var(--nx-accent);border-radius:3px}
.synth .reference .aggregate-bar,.synth .gate-row .aggregate-bar{background:var(--nx-muted)}
.synth .gate-row .aggregate-track{height:8px}
.synth .aggregate-chart .gate-row{margin-left:0}
.synth .funnel-group{font-size:14px;margin:4px 0 0;padding-top:18px;border-top:1px solid var(--nx-line)}
.synth .pending-analysis{padding:4px 0 20px;margin-bottom:24px;border-bottom:1px solid var(--nx-line)}
.synth .pending-analysis h2{font-size:19px;margin:0 0 8px}
.synth .pending-analysis p{font-size:14px;margin:0}
.synth .context a{min-height:44px;display:inline-flex;align-items:center;margin-right:24px}
:lang(zh) .synth p,:lang(zh) .synth .aggregate-label{line-height:1.75}
@media(max-width:600px){.synth .aggregate-label{font-size:14px;gap:10px}.synth .aggregate-chart .gate-row{margin-left:0}.synth .aggregate-label b{font-size:14px}}
'''

GATE_LABELS = {
    'support': ('Data support below threshold', '数据支持未达门槛'),
    'history': ('Too few treated states retained (7); source checks exhausted (5)', '保留处理州不足（7）；来源核验额度耗尽（5）'),
    'connector': ('No usable outcome series registered in connectors', '连接器未登记可用结局序列'),
    'ri': ('Reassignment test infeasible', '重排检验不可行'),
    'fresh': ('Too few new states for an independent audit', '可供独立审计的新州不足'),
    'pre_run_stop': ('Reassignment test infeasible before estimation', '估计前重排检验不可行'),
    'other': ('Outcome registration incomplete', '结局登记不全'),
    'v3': ('History audit failed', '历史审计未通过'),
}


def load_aggregate_inputs():
    """Read existing aggregates; never compute a new policy association."""
    meta = json.loads(META.read_text(encoding='utf-8'))
    counts = json.loads(COUNTS.read_text(encoding='utf-8'))
    if counts['processed'] != counts['estimated'] + counts['not_evaluable']:
        raise ValueError('processed count does not reconcile')
    if counts['pipeline_processed'] != counts['pipeline_estimated'] + counts['not_evaluable']:
        raise ValueError('pipeline count does not reconcile')
    if set(counts['gates']) != set(GATE_LABELS) or sum(counts['gates'].values()) != counts['not_evaluable']:
        raise ValueError('evaluability gates do not reconcile')
    return meta, counts


def aggregate_bar(label, amount, denominator, readout, *, kind='', key=''):
    """HTML labels stay readable at 390 px; bars share the stated denominator."""
    width = 100 * amount / denominator
    return (f'<div class="aggregate-row {kind}" data-series="{key}" data-value="{amount}" data-denominator="{denominator}">'
            f'<div class="aggregate-label"><span>{esc(label)}</span><b>{esc(readout)}</b></div>'
            f'<div class="aggregate-track" aria-hidden="true"><span class="aggregate-bar" style="width:{width:.4f}%"></span></div></div>')


def share_plot(key, label, amount, denominator, readout, reference=.05):
    x = 10 + 380 * amount / denominator / .30
    ref = 10 + 380 * reference / .30
    return (f'<div class="aggregate-row" data-series="{key}" data-value="{amount}" data-denominator="{denominator}">'
            f'<div class="aggregate-label"><span>{esc(label)}</span><b>{esc(readout)}</b></div>'
            f'<svg viewBox="0 0 400 28" preserveAspectRatio="none" aria-hidden="true"><path class="grid-line" d="M10 14H390"/>'
            f'<path class="zero" d="M{ref:.2f} 3V25"/><path class="aggregate-dot" d="M{x:.2f} 14h.001"/></svg></div>')


def q1_panel(lang, meta):
    zh = lang == 'zh'; q = meta['questions']['Q1']
    title = '多少估计超过噪声底线' if zh else 'How often estimates exceed the noise floor'
    rate = q['rate_k_over_n']; archive = q['by_source']['archive']; pipeline = q['by_source']['pipeline']
    chart = share_plot('exceedance', '全部主结局' if zh else 'All primary outcomes', rate, 1, f'{q["k"]}/{q["n"]} · {rate:.1%}')
    for key, label, row in [('archive', '手工设计' if zh else 'Hand-built designs', archive), ('pipeline', '流水线设计' if zh else 'Pipeline designs', pipeline)]:
        chart += share_plot(key, label, row['k'], row['n'], f'{row["k"]}/{row["n"]} · {row["k"]/row["n"]:.1%}')
    lo, hi = q['full_roster_bounds']
    note = (f'设计主结局，含诊断后停止设计；{q["n"]}/{q["D"]} 有参照。'
            if zh else f'Design primary outcomes, including diagnostic stops; {q["n"]}/{q["D"]} have a reference.')
    bounds = (f'将缺参照设计全部计为未超过或全部计为超过，{q["D"]} 项设计的比例范围为 {lo:.1%}–{hi:.1%}。' if zh else
              f'Counting all designs without a reference as below or above the floor gives bounds of {lo:.1%}–{hi:.1%} over {q["D"]} designs.')
    definition = ('噪声底线：在符合条件的州之间随机重排采纳日期，取所得估计绝对值的第 95 百分位。' if zh else
                  'Noise floor: the 95th percentile of absolute estimates from randomly reassigning adoption dates across eligible states.')
    reference = '按 95% 截点构造的 5%' if zh else '5% by construction of the 95% cutoff'
    return (f'<figure id="q1"><h2>{title}</h2><p class="aggregate-meta">{definition}</p>'
            f'<div class="aggregate-chart">{chart}<div class="share-axis"><span>0%</span><span>30%</span></div></div>'
            f'<p class="small" data-series="reference" data-value="0.05" data-denominator="1">┆ {reference}</p><figcaption>{note}</figcaption>'
            f'<details><summary>{"参照覆盖" if zh else "Reference coverage"}</summary><p class="small">{bounds}</p></details></figure>')


def q3_panel(lang, meta):
    zh = lang == 'zh'; scores = meta['questions']['Q3']['locked_median_and_interval_scores']; chart=[]
    for key, label in [('pipeline','流水线' if zh else 'Pipeline'), ('all','合并' if zh else 'Pooled'), ('archive','手工批次' if zh else 'Hand-built batches')]:
        q=scores[key]; n=q['design_n']; hits=q['median_direction_hits']
        members=[r for r in scores['designs'] if key=='all' or (r['source_group']=='pipeline')==(key=='pipeline')]
        negative=sum(r['b']<0 for r in members)
        x=10+380*hits/n; ref=10+380*negative/n
        show_baseline=key!='archive'
        readout=f'{hits}/{n} · {negative}/{n}' if show_baseline else f'{hits}/{n}'
        baseline=(f'<span class="sr-only" data-series="{key}-baseline" data-value="{negative}" data-denominator="{n}"></span>' if show_baseline else '')
        baseline_mark=(f'<path d="M{x:.2f} 14H{ref:.2f}" stroke="var(--nx-muted)"/><path d="M{ref:.2f} 6V22" stroke="var(--nx-ink)" stroke-width="2"/>' if show_baseline else '')
        chart.append(f'<div class="aggregate-row" data-group="{key}"><div class="aggregate-label"><span>{label}</span><b>{readout}</b></div>'
                     f'<span class="sr-only" data-series="{key}-forecast" data-value="{hits}" data-denominator="{n}"></span>'
                     f'{baseline}<svg viewBox="0 0 400 28" preserveAspectRatio="none" aria-hidden="true"><path class="grid-line" d="M10 14H390"/>'
                     f'{baseline_mark}<path class="aggregate-dot" d="M{x:.2f} 14h.001"/></svg></div>')
    title='预测符号正确的比例' if zh else 'Share of signs forecast correctly'
    key='● 预测中位数 · ┃ 一律猜负' if zh else '● Median forecast · ┃ Always negative'
    note=(f'{scores["all"]["design_n"]}/{meta["counts"]["design_D"]} 项设计，{scores["all"]["slots"]} 条运行前锁定预测；每项取五个 AI 角色的中位数。' if zh else
          f'{scores["all"]["design_n"]}/{meta["counts"]["design_D"]} designs, {scores["all"]["slots"]} forecasts locked before their runs; the median of five AI personas per design.')
    return f'<figure id="q3"><h2>{title}</h2><p class="small">{key}</p><div class="aggregate-chart">{"".join(chart)}<div class="share-axis"><span>0%</span><span>100%</span></div></div><figcaption>{note}</figcaption></figure>'


def q5_panel(lang, counts):
    zh=lang=='zh'; n=counts['processed']; estimated=counts['estimated']; unavailable=counts['not_evaluable']
    title='设计在哪一关止步' if zh else 'Where designs stop'
    yes='已估计' if zh else 'Estimated'; no='未估计' if zh else 'Not estimated'
    chart=(f'<div class="stacked-counts" role="img" aria-label="{yes} {estimated}; {no} {unavailable}">'
           f'<span data-series="estimated" data-value="{estimated}" data-denominator="{n}" style="flex:{estimated}">{estimated}</span>'
           f'<span data-series="not_evaluable" data-value="{unavailable}" data-denominator="{n}" style="flex:{unavailable}">{unavailable}</span></div>'
           f'<div class="aggregate-label"><span>{yes}</span><span>{no}</span></div>')
    gates = {key: count for key, count in counts['gates'].items() if key not in ('ri', 'pre_run_stop')}
    gates['reassignment'] = counts['gates']['ri'] + counts['gates']['pre_run_stop']
    for key,count in sorted(gates.items(),key=lambda item:-item[1]):
        label = (('估计前可用重排不足 90%' if zh else 'Reassignment draws below 90% before estimation')
                 if key == 'reassignment' else GATE_LABELS[key][zh])
        chart+=aggregate_bar(label, count, n, str(count), kind='gate-row', key=key)
    for key in ('ri', 'pre_run_stop'):
        chart+=f'<span class="sr-only" data-series="{key}" data-value="{counts["gates"][key]}" data-denominator="{n}"></span>'
    note=(f'{n} 项：截至 {counts["as_of"]} 全部已尝试设计。流水线 {counts["pipeline_estimated"]}/{counts["pipeline_processed"]} 项已估计；估计总数含 {counts["stopped_after_run"]} 项诊断后停止。另有 {counts["in_progress"]} 项进行中、{counts["parked_unfinished"]} 项历史检索未完成，未计入。'
          if zh else f'{n} designs: all attempts through {counts["as_of"]}. Pipeline: {counts["pipeline_estimated"]}/{counts["pipeline_processed"]} estimated; the total includes {counts["stopped_after_run"]} diagnostic stops. {counts["in_progress"]} ongoing and {counts["parked_unfinished"]} unfinished history searches are outside this roster.')
    return f'<figure id="q5"><h2>{title}</h2><div class="aggregate-chart">{chart}</div><figcaption>{note}</figcaption></figure>'


def historical_panels(lang, meta):
    ref=meta['pooled_adoption_reference']
    pooled={'H1':ref['historical_H1'], 'H0':{'point':ref['historical_H0_point'],'inference':ref['historical_H0_inference']}, 'fit_scope':{'policies':35,'events':597}}
    html=pooled_figure(lang,pooled,[])
    definition=('党派控制：1 月 1 日民主党统一执政减共和党统一执政，再标准化；正值指向民主党，所有政策方向相同。当地需要取预先登记的该政策滞后指标。' if lang=='zh' else
                "Party control: January-1 unified Democratic minus unified Republican control, standardized; positive means Democratic control, with the same sign for every policy. Local need is the policy's pre-registered lagged indicator.")
    html=html.replace('<div class="pooled-panels">', f'<p class="small">{definition}</p><div class="pooled-panels">')
    html=re.sub(r'<details><summary>'+re.escape(V2_COPY[lang]['earlier'])+r'</summary>.*?</details>', '', html, flags=re.S)
    return html


def page_v3(lang, meta, counts):
    zh = lang == 'zh'; slots = render_slots(lang, 'synthesize', features={'github': True, 'examples': True})
    title = COPY[lang]['title']; d = meta['counts']['design_D']
    headline = '跨政策的规律' if zh else 'Patterns across policies'
    finding = ('35 项政策的采纳研究中，党派控制、当地需要及二者之差的区间均包含零。' if zh else
               'Across 35 policies, the adoption estimates for party control, local need and their difference all have intervals that include zero.')
    intro = (f'开发样本：截至 {meta["as_of"]} 完成的 {d} 项设计，共已尝试 {counts["processed"]} 项。' if zh else
             f'Development sample: {d} designs complete on {meta["as_of"]}, from {counts["processed"]} attempted designs.')
    estimates = '逐设计结果见估计页 →' if zh else 'Design-level results on Estimates →'
    method = '方法附录 →' if zh else 'Method appendix →'
    html = f'''<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Natural Experiment Scanner</title>
{marked('style', slots['style'])}<style>{V3_CSS}</style>{marked('motion', slots['motion'])}</head>
<body><div class="page">{marked('header', slots['header'])}{marked('nav', slots['nav'])}
<main class="synth"><div class="synth-hero"><h1>{headline}</h1><p>{finding}</p><p class="small">{intro}</p></div>
{historical_panels(lang,meta)}{q1_panel(lang, meta)}{q3_panel(lang, meta)}{q5_panel(lang, counts)}
<div class="context"><a href="/viz/{lang}/worked_examples.html">{estimates}</a><a href="{'/zh' if zh else ''}/documentation.html#synthesis-appendix">{method}</a></div></main>
{marked('footer', slots['footer'])}</div></body></html>
'''
    return html


def main():
    """Compatibility entry point: V5 rendering, always into an isolated preview."""
    parser = argparse.ArgumentParser(description='Build V5 Explore EN/ZH; preview only, no estimation or release.')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/nes-site-v5').resolve())
    args = parser.parse_args()
    from scripts.viz_site import build
    command = ['--page', 'synthesize', '--output-dir', str(args.output_dir)]
    if args.check:
        command.append('--check')
    return build.main(command)


if __name__ == '__main__':
    sys.exit(main())
