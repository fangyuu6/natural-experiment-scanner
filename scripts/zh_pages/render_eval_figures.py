#!/usr/bin/env python3
"""Render stored evaluation data for documentation; never run an evaluator.

The published appendix supplies seven score distributions. The original agreement
figure covers its first five models, not the two subsequently added models. The
classification comparison below is the existing renderer's run-0 comparison,
using the already-exported appendix rather than importing evaluation/API code.
Only partials/eval_figures.html is generated. Canonical documentation embeds the
same named blocks; the owning source editor refreshes those blocks when needed.
"""
from __future__ import annotations
import argparse
import csv
from html import escape
import math
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
APPENDIX = ROOT / 'paper/appendix/full_cross_model_results.csv'
LABELS = ROOT / 'data/output/cross_model/model_accuracy_report.csv'
OUTPUT = HERE / 'partials/eval_figures.html'
MODELS = ('deepseek-v3.2', 'gpt-5.4', 'minimax-2.5', 'grok-4.1-fast',
          'xiaomi-mimo-v2-flash', 'qwen-3.6-plus', 'minimax-2.7')
AGREEMENT_MODELS = MODELS[:5]


def load_data(appendix=APPENDIX, labels=LABELS):
    for path in (appendix, labels):
        if not path.is_file():
            raise ValueError('missing local numerical source: ' + str(path))
    with labels.open(encoding='utf-8', newline='') as f:
        names = {row['model']: row['label'] for row in csv.DictReader(f)}
    with appendix.open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows or len({r['policy_id'] for r in rows}) != len(rows):
        raise ValueError('empty or duplicate-policy appendix')
    scores = {}
    for model in MODELS:
        if model not in names:
            raise ValueError('missing model identity: ' + model)
        values = [float(r[model + '_composite_score']) for r in rows]
        if not all(math.isfinite(v) and 1 <= v <= 5 for v in values):
            raise ValueError('invalid score distribution: ' + model)
        scores[model] = values
    rates = {}
    for a in AGREEMENT_MODELS:
        for b in AGREEMENT_MODELS:
            pairs = [(r[a + '_design_type'].strip().upper() or 'NONE',
                      r[b + '_design_type'].strip().upper() or 'NONE') for r in rows]
            rates[a, b] = sum(x == y for x, y in pairs) / len(pairs)
    return names, scores, rates


def block(name, body):
    return '<!-- EVAL:' + name + ':START -->\n' + body + '\n<!-- EVAL:' + name + ':END -->'


def distribution(names, scores):
    out = ['<figure class="eval-distribution" aria-label="Composite Score Distribution by Model">']
    for model in MODELS:
        values = scores[model]
        # A conventional Gaussian density, with the complete observations retained
        # as a rug. This changes presentation only, not population or score values.
        mean = sum(values) / len(values)
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1))
        bandwidth = max(.08, sd * len(values) ** (-.2))
        axis = [1 + i * 4 / 160 for i in range(161)]
        density = [sum(math.exp(-.5 * ((x - v) / bandwidth) ** 2) for v in values) for x in axis]
        peak = max(density)
        points = [(12 + (x - 1) * 154, d / peak * 21) for x, d in zip(axis, density)]
        path = 'M ' + ' L '.join(f'{x:.3f},{26 - y:.3f}' for x, y in points)
        path += ' L ' + ' L '.join(f'{x:.3f},{26 + y:.3f}' for x, y in reversed(points)) + ' Z'
        out.append(f'<div class="eval-model" data-model="{model}"><div class="eval-model-name">{escape(names[model])}</div>')
        out.append('<div class="eval-model-plot">')
        out.append('<svg class="eval-density" width="640" height="52" viewBox="0 0 640 52" preserveAspectRatio="none" role="img" aria-label="' + escape(names[model]) + '">')
        out.append('<path d="' + path + '" fill="#dce8fa" stroke="#3263c3" stroke-width="1.2"/>')
        out.append('</svg>')
        # Only the horizontal coordinates scale. An SVG without a viewBox keeps
        # every rug mark's radius and vertical spacing in native CSS pixels,
        # including on phones. Percentages align with the density's score axis.
        out.append('<svg class="eval-rug" width="100%" height="24" role="img" aria-label="' + escape(names[model]) + '">')
        for i, value in enumerate(values):
            x = (12 + (value - 1) * 154) / 640 * 100
            out.append(f'<circle cx="{x:.6f}%" cy="{6 + i % 3 * 6}" r="1.7" fill="#3263c3" opacity=".55" data-score="{value:g}"><title>{value:g}</title></circle>')
        out.append('</svg></div></div>')
    out.append('<div class="eval-axis"><div class="eval-ticks" aria-hidden="true"><span>1</span><span>2</span><span>3</span><span>4</span><span>5</span></div></div>')
    out.append('<figcaption>Composite Score</figcaption></figure>')
    return block('DISTRIBUTION', '\n'.join(out))


def agreement(names, rates):
    # Keep every original cell once. Column hooks let the page use compact
    # numeric columns and turn full column identities vertically on phones.
    # Row identities remain readable while the figure's own frame scrolls.
    out = ['<figure class="eval-agreement" aria-label="Cross-model agreement matrix"><div class="eval-matrix-scroll" tabindex="0">',
           '<table class="eval-matrix"><colgroup><col class="eval-identity-col">' +
           '<col class="eval-value-col">' * len(AGREEMENT_MODELS) +
           '</colgroup><thead><tr><th scope="col"></th>']
    for model in AGREEMENT_MODELS:
        out.append('<th scope="col"><span class="eval-matrix-name">' + escape(names[model]) + '</span></th>')
    out.append('</tr></thead><tbody>')
    for a in AGREEMENT_MODELS:
        out.append('<tr><th scope="row">' + escape(names[a]) + '</th>')
        for b in AGREEMENT_MODELS:
            value = rates[a, b]
            # A single-hue ramp; all diagonals still contain their original 1.00.
            alpha = .08 + .36 * value
            background = '#f3f4f6' if a == b else f'rgba(50,99,195,{alpha:.3f})'
            out.append(f'<td data-pair="{a}|{b}" data-rate="{value:g}" style="background:{background}">{value:.2f}</td>')
        out.append('</tr>')
    out.append('</tbody></table></div></figure>')
    return block('AGREEMENT', '\n'.join(out))


def render():
    names, scores, rates = load_data()
    return distribution(names, scores) + '\n' + agreement(names, rates) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    try:
        expected = render()
    except (ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != expected:
            print('stale evaluation partial: ' + str(OUTPUT), file=sys.stderr)
            return 1
        canonical = (HERE / 'documentation.en.html').read_text(encoding='utf-8')
        for name in ('DISTRIBUTION', 'AGREEMENT'):
            pattern = r'<!-- EVAL:' + name + r':START -->.*?<!-- EVAL:' + name + r':END -->'
            embedded = re.search(pattern, canonical, re.S)
            generated = re.search(pattern, expected, re.S)
            if embedded and embedded.group() != generated.group():
                print('stale embedded evaluation block: ' + name, file=sys.stderr)
                return 1
        print('internal evaluation partial current; any public embeddings checked; no evaluation invoked')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding='utf-8')
        print('rendered ' + str(OUTPUT.relative_to(ROOT)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
