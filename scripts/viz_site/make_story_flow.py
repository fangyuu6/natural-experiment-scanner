#!/usr/bin/env python3
"""Generate partials/story_flow.html, the converging-flow figure of the Screening page (stdlib only, 3.9+).
It sat on the Home until V5 and returns on Screening in V5.1 (R25).

    python3 scripts/viz_site/make_story_flow.py             # (re)write partials/story_flow.html
    python3 scripts/viz_site/make_story_flow.py --check     # exit 1 when the file, or the en/zh dictionaries, are stale
    python3 scripts/viz_site/make_story_flow.py --preview   # also write deploy_output/_landing_options/story_flow_preview_{en,zh}.html
    python3 scripts/viz_site/make_story_flow.py --shots     # --preview, then PNGs flow_v7_{en,zh}_{1440,1100,820,390}.png + tooltip shots
                                                            # (headless Chrome: set CHROME=/path/to/chrome or use the default macOS install)
    python3 scripts/viz_site/make_story_flow.py --selfcheck # --preview, then headless Chrome: text size, label gaps, frame heights, counts

Input : deploy_output/funnel_data.json
            total, stages[*].n_out                      trunk thickness 483,414 -> 7,781 through the five gates; only the two ends
                                                        are printed ({{num:..|int}} placeholders), the gates carry no count
            by_source_family[*].n[0]                    the four ribbons at stage 0
        scripts/viz_site/i18n/story_flow.{en,zh}.json   every word of the graphic
        deploy_output/_release/design_spec.md + design_tokens.css   the art director's spec (v3); the colours, type sizes, frame and
                                                        motion below follow it, and the --nx-* tokens are used when the page loads them
Output: scripts/viz_site/partials/story_flow.html
        One language-neutral file for both languages: words are {{t:..}} / {{tf:..}} placeholders, and every figure shown as
        text is a {{num:..}} placeholder that build.py resolves from funnel_data.json (so en/zh number styles stay right).
        What the generator bakes in is GEOMETRY ONLY (ribbon and trunk thickness), which is why --check exists: the partial
        is stale when funnel_data.json moves. build.py does not know this file: run --check next to build.py --check.
        The partial IS the figure frame (surface, hairline border, radius 12, padding 24 / 16 / full-bleed at <= 600 like the map
        card): do not wrap it in another frame. It holds no links.

THE STORY (what the trunk shows, in the order of documentation.html#overview-pipeline, phases 2-4)
  Classify          long federal bills (>= 3 structural markers and >= 10,000 characters) are split into chapters (at most 8; the figure
                    does not print the number): the trunk peels into thin parallel strands ("chapters") that re-merge at Extract; the
                    thick strand on top is the through-path of every short and state bill. Nothing else about the split is drawn.
  Extract           the first model call per bill or chapter flags RD, DiD or neither.
  Literature check  the second model call estimates how saturated the literature is; the score adjustment is +-0.4.
  Score             four sub-scores, composite, rule corrections, hard caps.
  Screen            five rules: white notches in the trunk. No count label sits at a notch: V5.1 draws only counts that the V5 pages print
                    (483,414 texts, the four source families, 7,781 candidates). The starting count hangs from the trunk start by a hairline.

DRAWING RULES (SVG user units; the SVG is authored at the 1050-unit inner width of the frame, so it is shown at scale >= 0.95)
  ribbons   one per source family, thickness = K * sqrt(n at stage 0), K = T / sqrt(total) = 0.0633; a floor of MIN_TH keeps the 272-row
            family visible. Each ribbon is compressed by the same factor into its slot of the trunk, hence the merge keeps sqrt
            proportions. Flat fill, hairline separation.
  trunk     thickness T = 44 at 483,414, steps down at each gate to K * sqrt(n_out of the gate). One compound path (strands included)
            so overlaps never double-paint. Flat fill.
  strands   illustrative, NOT counted (funnel_data.json has no chapter counts): the through-path is the top 24 units of the trunk,
            four hairline strands peel off downwards and come back.
  dots      MOTION: one shared 2.8s reveal, then two finite 12s passes controlled by NXMotion:
            14 small dots (r 2.2, accent at 62%) travel along the four ribbons into the trunk and through the five steps, one linear
            pass of 12 s, start phases staggered within the two-pass window. Dots per ribbon follow the ribbon thickness (6 / 4 / 3 / 1).
            SVG <animateMotion> + <mpath> along the routes in <defs>; opacity by <animate>. 9 of the 14 fade out at gate 1 and 3 at
            gate 4 (opacity -> 0 over about 0.4 s); 2 (about 1 in 7) run on into the end box, which is drawn over them. The vertical
            variant has 9 dots sliding down the rail (CSS transform only), 8 fade out at the Screen row, 1 runs on.
            No-JS, print and prefers-reduced-motion show the finished static figure, without dots. NXMotion cancels the sequence
            when the figure leaves view, its layout changes or the tab is hidden. 14 dots, no filters, no layout work during dot travel.
  text      names 13 / 600, sub-lines 13 / 400, counts 13 / 600 (tabular), end box 28 / 700; nothing below 13 units,
            so nothing renders below 12.4px. Label boxes keep >= 8 units apart (checked by --selfcheck).
  vertical  when the frame's inner width is < 1000px (a CSS container query on the frame itself, about a 1100px viewport) a list
            replaces the SVG: source rows, a thin rail with the dots, five step rows with a 10px dot, a flat end box.
The family key "State/Local Executive" contains a slash that the {{num:..}} expression grammar cannot address, so its count
is derived in the expression as total minus the other three; this script asserts that the four families partition the total.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEPLOY = ROOT / "deploy_output"
OUT = HERE / "partials" / "story_flow.html"
I18N = HERE / "i18n"
PREVIEW_DIR = DEPLOY / "_landing_options"
TOKENS = DEPLOY / "_release" / "design_tokens.css"
MAX_BYTES = 70 * 1024

# ---- desktop geometry (SVG user units, viewBox 0 0 W H = the frame's inner width at a 1100px column) ----
W, H = 1050, 206
C, T = 100.0, 44.0              # trunk centre line and thickness at 483,414 texts
TOP, BOT = C - T / 2, C + T / 2
MIN_TH = 1.5                    # floor for the hairline ribbon
XS, XE = 150.0, 228.0           # ribbons run XS -> XE, the trunk starts at XE
SRC_Y = [C - 66, C - 22, C + 22, C + 66]
STEPS = [("classify", "n1"), ("extract", "n2"), ("lit", "n3"), ("score", "n4"), ("screen", "n5")]
NODE_X = {"classify": 330.0, "extract": 461.0, "lit": 605.0, "score": 723.0}
HIT_HW = {"classify": 46.0, "extract": 73.0, "lit": 57.0, "score": 48.0}
CAPTION_HW = {"classify": 55.0, "extract": 86.0, "lit": 57.0, "score": 56.0, "screen": 54.0}
GATE_X = [741.0, 787.0, 833.0, 879.0, 925.0]        # the five gates: white notches in the trunk
SCREEN_X = (GATE_X[0] + GATE_X[-1]) / 2             # the Screen label is centred over the five gates
SCREEN_HW = 92.0
END_X, END_W, END_H = 940.0, 110.0, 56.0
DOT_R = 4.5
TITLE_Y, SUB_Y = TOP - 50.0, TOP - 33.0   # leave room for a two-line caption above the trunk
LEAD_Y = TOP - 14.0             # hairline leader from label to trunk
COUNT_Y = BOT + 30.0            # baseline of the starting count, which hangs from the trunk start
# strand section between Classify and Extract: peel (F0-F1), parallel (F1-F2), re-merge (F2-F3)
F0, F1, F2, F3 = 346.0, 374.0, 414.0, 442.0
THROUGH = 24.0                  # thickness of the through-path strand (top of the trunk)
CHAP_PACKED = 5.0               # the four thin strands, packed under the through-path
CHAP_SPREAD_Y = [BOT + 6.0, BOT + 17.0, BOT + 28.0, BOT + 39.0]   # their centre lines where they run apart
CHAP_TH = 3.0                   # hairline strands

# ---- the flowing dots (two finite passes, after the source-to-shortlist introduction) ----
DUR = 12.0                      # seconds per pass, two passes along the whole route
PD_R, PD_OP = 2.2, 0.5          # dot radius (units) and opacity; two finite passes after the reveal
YD = C - 3.0                    # height of the dots inside the trunk (inside the through-path strand and inside every narrowed trunk)
DOT_WEIGHTS = [6, 4, 3, 1]      # dots per ribbon, by thickness: regs 315,727 / leg 125,919 / state 41,496 / exec 272
DOT_FATES = [["g1", "g1", "g1", "g4", "g1", "k"], ["g1", "g1", "g4", "k"], ["g1", "g4", "g1"], ["g1"]]   # g1/g4: fades out at that gate; k: reaches the end box
FADE_BEFORE, FADE_AFTER = 6.0, 36.0   # a fading dot dims from gate x - 6 to gate x + 36 (about 0.4 s at the dots' speed)
V_DOTS_FILTERED, V_DOTS_KEPT = 8, 1   # the vertical rail: 9 dots, one keeps going

FAMILIES = [                    # (json key, dictionary key, path segment for {{num:..}} or None)
    ("Federal Regulations", "sf.src.fedreg", "Federal_Regulations"),
    ("Federal Legislation", "sf.src.fedleg", "Federal_Legislation"),
    ("State Legislation", "sf.src.state", "State_Legislation"),
    ("State/Local Executive", "sf.src.exec", None),
]
GATE_KEYS = ["all", "strict_enacted", "no_wave", "no_covid", "data_ok", "min_gate"]


# --------------------------------------------------------------------------- data

def load_funnel() -> dict:
    return json.loads((DEPLOY / "funnel_data.json").read_text(encoding="utf-8-sig"))


def geometry(d: dict) -> dict:
    total = d["total"]
    stages = {s["key"]: s for s in d["stages"]}
    if [s["key"] for s in d["stages"]] != GATE_KEYS or len(GATE_X) != len(GATE_KEYS) - 1:
        raise SystemExit("funnel_data.json stages are not %s" % GATE_KEYS)
    fam = d["by_source_family"]
    counts = []
    for key, _, _ in FAMILIES:
        if key not in fam:
            raise SystemExit("funnel_data.json has no source family %r" % key)
        counts.append(fam[key]["n"][0])
    if sum(counts) != total or stages["all"]["n_out"] != total:
        raise SystemExit("source families do not partition the total (%d vs %d)" % (sum(counts), total))
    outs = [stages[g]["n_out"] for g in GATE_KEYS]
    if any(b > a for a, b in zip(outs, outs[1:])):
        raise SystemExit("funnel_data.json stage counts are not non-increasing: %s" % outs)

    k = T / math.sqrt(total)
    s = [max(k * math.sqrt(n), MIN_TH) for n in counts]                 # ribbon thickness at the source
    slot = [x * T / sum(s) for x in s]                                  # the same ribbons inside the trunk
    tops, acc = [], TOP
    for x in slot:
        tops.append(acc)
        acc += x
    halves = [T / 2] + [k * math.sqrt(stages[g]["n_out"]) / 2 for g in GATE_KEYS[1:]]
    return {"counts": counts, "k": k, "s": s, "slot": slot, "slot_top": tops, "halves": halves,
            "total": total, "short": stages["min_gate"]["n_out"]}


# --------------------------------------------------------------------------- helpers

def n(x: float) -> str:
    s = ("%.1f" % x).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def t(key: str) -> str:
    return "{{t:%s}}" % key


def num(expr: str, fmt: str = "") -> str:
    return "{{num:%s%s}}" % (expr, "|" + fmt if fmt else "")


def fam_count(i: int) -> str:
    _, _, seg = FAMILIES[i]
    if seg:
        return num("funnel_data.by_source_family.%s.n.0" % seg)
    others = "".join("-funnel_data.by_source_family.%s.n.0" % FAMILIES[j][2] for j in range(3))
    return num("funnel_data.total" + others)


def tf(key: str, **slots: str) -> str:
    return "{{tf:%s%s}}" % (key, "".join("|%s=%s" % (a, b) for a, b in slots.items()))


def tf_aria() -> str:
    return tf("sf.aria", total="int(funnel_data.total)", short="int(funnel_data.stages[key=min_gate].n_out)")


# --------------------------------------------------------------------------- desktop SVG

def ribbon_paths(g: dict) -> list:
    """The four ribbons: filled bands, and a stroked hairline for the thinnest one."""
    xm = XS + (XE - XS) * 0.5
    out = []
    for i in range(4):
        y, s, sl, top = SRC_Y[i], g["s"][i], g["slot"][i], g["slot_top"][i]
        if s <= MIN_TH + 0.01:                                          # hairline: a stroked line, not a band
            yc = top + sl / 2
            out.append('<path class="sf-hair" d="M%s,%sC%s,%s %s,%s %s,%s"/>' % (n(XS), n(y), n(xm), n(y), n(xm), n(yc), n(XE), n(yc)))
            continue
        d = ("M%s,%sC%s,%s %s,%s %s,%sL%s,%sC%s,%s %s,%s %s,%sZ"
             % (n(XS), n(y - s / 2), n(xm), n(y - s / 2), n(xm), n(top), n(XE), n(top),
                n(XE), n(top + sl), n(xm), n(top + sl), n(xm), n(y + s / 2), n(XS), n(y + s / 2)))
        out.append('<path class="sf-rib" d="%s"/>' % d)
    return out


def strand_geometry() -> list:
    """Packed and spread (top, bottom) of the four thin strands, top to bottom."""
    base = TOP + THROUGH
    out = []
    for i, cy in enumerate(CHAP_SPREAD_Y):
        pt = base + i * CHAP_PACKED
        out.append((pt, pt + CHAP_PACKED, cy - CHAP_TH / 2, cy + CHAP_TH / 2))
    return out


def s_edge(xa: float, ya: float, xb: float, yb: float) -> str:
    """Cubic S from (xa,ya) to (xb,yb) with horizontal tangents at both ends (SVG 'C' segment text)."""
    xm = (xa + xb) / 2
    return "C%s,%s %s,%s %s,%s" % (n(xm), n(ya), n(xm), n(yb), n(xb), n(yb))


def strand_shapes() -> list:
    """Sub-paths (clockwise, 1 unit of overlap at both ends) of the through-path and the four thin strands."""
    parts = ["M%s,%sH%sV%sH%sZ" % (n(F0 - 1), n(TOP), n(F3 + 1), n(TOP + THROUGH), n(F0 - 1))]
    for pt, pb, st, sb in strand_geometry():
        parts.append("M%s,%sH%s%sH%s%sH%sV%sH%s%sH%s%sH%sZ"
                     % (n(F0 - 1), n(pt), n(F0), s_edge(F0, pt, F1, st), n(F2), s_edge(F2, st, F3, pt), n(F3 + 1),
                        n(pb), n(F3), s_edge(F3, pb, F2, sb), n(F1), s_edge(F1, sb, F0, pb), n(F0 - 1)))
    return parts


def trunk_paths(g: dict):
    """One compound path (pre-strand trunk, strands, trunk from the re-merge with the five steps), the notches and the starting count's leader."""
    hv = g["halves"]
    pre = "M%s,%sH%sV%sH%sZ" % (n(XE), n(TOP), n(F0 + 1), n(BOT), n(XE))
    top = "M%s,%s" % (n(F3 - 1), n(TOP))
    for j in range(1, len(hv)):
        top += "H%sV%s" % (n(GATE_X[j - 1]), n(C - hv[j]))
    top += "H%sV%s" % (n(END_X + 1), n(C + hv[-1]))
    bot = ""
    for j in range(len(hv) - 1, 0, -1):
        bot += "H%sV%s" % (n(GATE_X[j - 1]), n(C + hv[j - 1]))
    post = top + bot + "H%sZ" % n(F3 - 1)
    body = pre + "".join(strand_shapes()) + post
    notches, leads = [], []
    for j in range(1, len(hv)):
        x, h = GATE_X[j - 1], hv[j - 1]
        notches.append('<path class="sf-notch" d="M%s,%sV%s"/>' % (n(x), n(C - h), n(C + h)))
    leads.append("M%s,%sV%s" % (n(XE + 30), n(BOT + 2), n(COUNT_Y - 14)))          # the 483,414 label hangs from the trunk start
    return ('<path class="sf-trunk" d="%s"/>' % body, "".join(notches), '<path class="sf-lead" d="%s"/>' % "".join(leads))


def _bez(p0, p1, p2, p3, steps=48):
    out = []
    for i in range(steps + 1):
        u = i / steps
        a, b, c, d = (1 - u) ** 3, 3 * (1 - u) ** 2 * u, 3 * (1 - u) * u * u, u ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def dot_route(i: int, g: dict):
    """Route of the dots of ribbon i: its centre line, a short S into the trunk at height YD, then straight to just inside the end box.
    Returns (path d, function x -> fraction of the route length where the dot is at that x)."""
    xm = XS + (XE - XS) * 0.5
    y0, yc = SRC_Y[i], g["slot_top"][i] + g["slot"][i] / 2
    x_end = END_X + 16
    pts = (_bez((XS, y0), (xm, y0), (xm, yc), (XE, yc)) + _bez((XE, yc), (XE + 20, yc), (XE + 20, YD), (XE + 40, YD))[1:] + [(x_end, YD)])
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))

    def frac(x: float) -> float:
        for k in range(len(pts) - 1):
            (ax, _), (bx, _) = pts[k], pts[k + 1]
            if ax <= x <= bx and bx > ax:
                return min((cum[k] + (cum[k + 1] - cum[k]) * (x - ax) / (bx - ax)) / cum[-1], 1.0)
        return 1.0

    d = "M%s,%sC%s,%s %s,%s %s,%sC%s,%s %s,%s %s,%sH%s" % (n(XS), n(y0), n(xm), n(y0), n(xm), n(yc), n(XE), n(yc),
                                                        n(XE + 20), n(yc), n(XE + 20), n(YD), n(XE + 40), n(YD), n(x_end))
    return d, frac


def dot_sequence() -> list:
    """(ribbon, fate) of every dot, interleaved so the four ribbons all carry a steady trickle (weights = DOT_WEIGHTS)."""
    total, got, used, seq = sum(DOT_WEIGHTS), [0] * 4, [0] * 4, []
    for k in range(total):
        i = max(range(4), key=lambda r: (DOT_WEIGHTS[r] * (k + 1) / total - got[r], -r))
        seq.append((i, DOT_FATES[i][got[i]]))
        got[i] += 1
    return seq


def dots_svg(g: dict) -> str:
    """The flowing dots: <defs> with the four routes, then one circle per dot with animateMotion (position) and animate (opacity)."""
    routes = [dot_route(i, g) for i in range(4)]
    defs = "<defs>%s</defs>" % "".join('<path id="sf-r%d" d="%s"/>' % (i, routes[i][0]) for i in range(4))
    circles = []
    total = len(dot_sequence())
    for k, (i, fate) in enumerate(dot_sequence()):
        phase = n(DUR * k / total)
        frac = routes[i][1]
        if fate == "k":                                         # keeps going into the end box (which is drawn over the dot)
            ktimes, vals = "0;0.03;0.97;1", "0;%s;%s;0" % (PD_OP, PD_OP)
        else:
            gx = GATE_X[0] if fate == "g1" else GATE_X[3]
            ktimes = "0;0.03;%.4f;%.4f;1" % (frac(gx - FADE_BEFORE), frac(gx + FADE_AFTER))
            vals = "0;%s;%s;0;0" % (PD_OP, PD_OP)
        circles.append('<circle class="sf-pd" r="%s" opacity="0">'
                       '<animateMotion dur="%ss" begin="indefinite" data-phase="%s" repeatCount="2" rotate="0"><mpath href="#sf-r%d"/></animateMotion>'
                       '<animate attributeName="opacity" values="%s" keyTimes="%s" dur="%ss" begin="indefinite" data-phase="%s" repeatCount="2"/></circle>'
                       % (n(PD_R), n(DUR), phase, i, vals, ktimes, n(DUR), phase))
    return defs + "".join(circles)


def vertical_dots() -> str:
    """Dots sliding down the rail: a strip per dot (full height, translated by CSS), the dot is the strip's ::before. The 'filtered' strips
    end where the Screen row starts (the script sets --sf-lastH), the 'kept' one runs on into the end box."""
    total = V_DOTS_FILTERED + V_DOTS_KEPT
    xs = ["38%", "50%", "62%"]
    def strip(k):
        return '<span class="sf-vd" data-phase="%s" style="--x:%s"></span>' % (n(DUR * k / total), xs[k % 3])
    filt = "".join(strip(k) for k in range(V_DOTS_FILTERED))
    kept = "".join(strip(V_DOTS_FILTERED + k) for k in range(V_DOTS_KEPT))
    return '<div class="sf-vdots" aria-hidden="true"><div class="sf-vdf">%s</div><div class="sf-vdk">%s</div></div>' % (filt, kept)


def node_svg(key: str, idx: str, g: dict) -> str:
    """One labelled step: hit area, hairline leader and dot (not for Screen: its gates are the notches), name, one sub-line."""
    screen = key == "screen"
    cx = SCREEN_X if screen else NODE_X[key]
    hw = SCREEN_HW if screen else HIT_HW[key]
    hit_y = TITLE_Y - 22
    out = ['<g class="sf-node" data-k="%s">' % key,
           '<rect class="sf-hit" x="%s" y="%s" width="%s" height="%s"/>' % (n(cx - hw), n(hit_y), n(2 * hw), n(BOT + 8 - hit_y))]
    if screen:
        x1, x2 = GATE_X[0], GATE_X[-1]
        out.append('<path class="sf-brk" d="M%s,%sV%sH%sV%s"/>' % (n(x1), n(TOP - 5), n(TOP - 10), n(x2), n(TOP - 5)))
    else:
        out.append('<path class="sf-lead" d="M%s,%sV%s"/>' % (n(cx), n(LEAD_Y), n(C - DOT_R - 2)))
        out.append('<circle class="sf-dot" cx="%s" cy="%s" r="%s"/>' % (n(cx), n(C), n(DOT_R)))
    out.append('<text class="sf-nm" x="%s" y="%s" text-anchor="middle">%s</text>' % (n(cx), n(TITLE_Y), t("sf.%s.name" % idx)))
    caption_hw = CAPTION_HW[key]
    out.append('<foreignObject class="sf-caption" x="%s" y="%s" width="%s" height="32">'
               '<div xmlns="http://www.w3.org/1999/xhtml" class="sf-caption-wrap"><span class="sf-sb">%s</span></div></foreignObject>'
               % (n(cx - caption_hw), n(SUB_Y - 13), n(2 * caption_hw), t("sf.%s.c1" % idx)))
    out.append("</g>")
    return "".join(out)


def strand_label_svg() -> str:
    """The only words about the chapter split: a hairline bracket under the parallel strands and a small label."""
    ly = CHAP_SPREAD_Y[-1] + CHAP_TH / 2 + 8
    return ('<path class="sf-brk" d="M%s,%sV%sH%sV%s"/><text class="sf-st" x="%s" y="%s" text-anchor="middle">%s</text>'
            % (n(F1), n(ly), n(ly + 5), n(F2), n(ly), n((F1 + F2) / 2), n(ly + 20), t("sf.strands")))


def desktop_svg(g: dict) -> str:
    trunk, notches, leads = trunk_paths(g)
    cl = ['<svg class="sf-svg" viewBox="0 0 %d %d" role="img" aria-label="%s" xmlns="http://www.w3.org/2000/svg">' % (W, H, tf_aria())]
    cl.append(strand_label_svg())
    cl.append('<defs><clipPath id="sf-intro-sources"><rect class="sf-source-clip" x="0" y="0" width="%s" height="%s"/></clipPath>'
              '<clipPath id="sf-intro-trunk"><rect class="sf-trunk-clip" x="%s" y="0" width="%s" height="%s"/></clipPath></defs>'
              % (n(XE), H, n(XE), n(END_X + 1 - XE), H))
    cl.append('<g class="sf-ribbons" clip-path="url(#sf-intro-sources)">%s</g>' % "".join(ribbon_paths(g)))
    cl.append('<g class="sf-trunk-wrap" clip-path="url(#sf-intro-trunk)">%s</g>' % trunk)
    cl.append(dots_svg(g))
    cl.append(leads)
    cl.append(notches)
    # the starting count hangs from the trunk start
    tx = XE + 30
    cl.append('<text class="sf-trunk-t" x="%s" y="%s" text-anchor="middle">%s</text><text class="sf-trunk-l" x="%s" y="%s" text-anchor="middle">%s</text>'
              % (n(tx), n(COUNT_Y), num("funnel_data.total", "int"), n(tx), n(COUNT_Y + 15), t("sf.trunk.l")))
    # sources
    for i, (_, key, _) in enumerate(FAMILIES):
        y = SRC_Y[i]
        cl.append('<text class="sf-sn" x="0" y="%s">%s</text>' % (n(y - 2), t(key)))
        cl.append('<text class="sf-sc" x="0" y="%s">%s</text>' % (n(y + 14), fam_count(i)))
    for key, idx in STEPS:
        cl.append(node_svg(key, idx, g))
    # end box
    ex, ey = END_X, C - END_H / 2
    cl.append('<g class="sf-end" data-k="end">'
              '<rect class="sf-hit" x="%s" y="%s" width="%s" height="%s"/>'
              '<rect class="sf-endbox" x="%s" y="%s" width="%s" height="%s" rx="8"/>'
              '<text class="sf-en" x="%s" y="%s" text-anchor="middle">%s</text>'
              '<text class="sf-el" x="%s" y="%s" text-anchor="middle">%s</text></g>'
              % (n(ex), n(ey), n(END_W), n(END_H), n(ex), n(ey), n(END_W), n(END_H),
                 n(ex + END_W / 2), n(C - 1), num("funnel_data.stages[key=min_gate].n_out", "int"),
                 n(ex + END_W / 2), n(C + 17), t("sf.end.label")))
    cl.append("</svg>")
    return "".join(cl)


# --------------------------------------------------------------------------- vertical (phone / tablet) version

V_GUT, V_CHIP, V_RAIL, V_R = 44.0, 36.0, 10.0, 12.0


def vertical_merge(g: dict):
    """Inline SVG with the four strands merging into a vertical rail (flat, sqrt widths), plus the rail widths. Top strand = leftmost slot."""
    raw = [max(x, MIN_TH) for x in g["slot"]]
    scale = V_RAIL / sum(raw)
    th = [max(x * scale, 1.2) for x in raw]
    rail = sum(th)
    total_h = 4 * V_CHIP
    end_y = total_h + 28
    paths, left = [], 5.0
    for i in range(4):
        cy = V_CHIP / 2 + i * V_CHIP
        sx = left + th[i] / 2
        left += th[i]
        r = min(V_R, V_GUT - sx - 4)
        paths.append('<path d="M%s,%sH%sQ%s,%s %s,%sV%s" stroke-width="%s"/>'
                     % (n(V_GUT), n(cy), n(sx + r), n(sx), n(cy), n(sx), n(cy + r), n(end_y), n(th[i])))
    svg = ('<svg class="sf-vm" width="%s" height="%s" viewBox="0 0 %s %s" aria-hidden="true" focusable="false">'
           '<g fill="none" stroke="var(--sf-flow)">%s</g></svg>' % (n(V_GUT), n(end_y), n(V_GUT), n(end_y), "".join(paths)))
    return svg, rail, end_y - total_h


def vertical_html(g: dict) -> str:
    merge, rail, extra = vertical_merge(g)
    rail_b = max(rail * g["halves"][-1] / g["halves"][0], 2.0)
    src = "".join('<li><span class="sf-vnm">%s</span><i class="sf-ld"></i><span class="sf-vct">%s</span></li>' % (t(key), fam_count(i))
                  for i, (_, key, _) in enumerate(FAMILIES))
    steps = []
    for key, idx in STEPS:
        steps.append('<li class="sf-vstep"><span class="sf-vn" data-k="%s"></span><div class="sf-vx"><span class="sf-vnm2">%s</span><span class="sf-vsb">%s</span></div></li>'
                     % (key, t("sf.%s.name" % idx), t("sf.%s.c1" % idx)))
    return (
        '<div class="sf-v sf-in" role="img" aria-label="%s" style="--rail:%spx;--rail-b:%spx;--merge-h:%spx">'
        '<div class="sf-vsrc">%s<ul class="sf-vlist">%s</ul></div>'
        '<div class="sf-vs sf-vs--a">%s<div class="sf-vtr">%s</div><ol class="sf-vsteps">%s</ol></div>'
        '<div class="sf-vs sf-vs--b">'
        '<div class="sf-vend" data-k="end"><b class="sf-ven">%s</b><span class="sf-vel">%s</span></div></div>'
        '</div>'
        % (tf_aria(), n(rail), n(rail_b), n(extra), merge, src, vertical_dots(),
           tf("sf.trunk", n="int(funnel_data.total)"), "".join(steps),
           num("funnel_data.stages[key=min_gate].n_out", "int"), t("sf.end.label")))


# --------------------------------------------------------------------------- CSS / JS (language-neutral, light theme only)

CSS = r"""
.story-flow {
  /* local custom properties inherit the shared --nx-* design tokens when available */
  --sf-ink: var(--nx-ink, #111827); --sf-body: var(--nx-text-2, #4b5563); --sf-muted: var(--nx-muted, #6b7280);
  --sf-line: var(--nx-line, #e5e7eb); --sf-line2: var(--nx-line-2, #d1d5db); --sf-plate: var(--nx-plate, #f9fafb);
  --sf-accent: var(--nx-accent, #1d4ed8); --sf-slate: var(--nx-slate, #8793a6); --sf-flow: #bfdbfe; --sf-tr: var(--nx-tr, 120ms ease-out);
  position: relative; display: block; margin: 0; padding: 24px; color: var(--sf-ink); text-align: left; line-height: 1.4;
  background: var(--sf-plate); border: 1px solid var(--sf-line); border-radius: 12px;
  container-type: inline-size;
  font-family: var(--nx-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif);
}
@media (max-width: 1099px) { .story-flow { padding: 16px; } }
@media (max-width: 600px) {
  .story-flow { margin-inline: calc(var(--nx-gutter, 16px) * -1); padding-inline: 0; border-inline: 0; border-radius: 0; }
  .story-flow .sf-in { padding-inline: var(--nx-gutter, 16px); }
}
.story-flow:lang(zh) { line-height: 1.6; font-synthesis: none; }
.story-flow *, .story-flow *::before, .story-flow *::after { box-sizing: border-box; }
.story-flow ol, .story-flow ul, .story-flow li, .story-flow p { margin: 0; padding: 0; list-style: none; }
.story-flow svg { display: block; }
.story-flow i { font-style: normal; }

/* visually hidden step list: the screen-reader text, and the keyboard focus targets that drive the tooltip */
.story-flow .sf-sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.story-flow .sf-sr .sf-nm, .story-flow .sf-sr .sf-tt { display: block; }

/* ---- desktop: one SVG authored at the frame's 1050-unit inner width; shown when that width is >= 1000px (a 1100px viewport: scale >= 0.95,
   so the 13-unit text is >= 12.4px). Narrower, or without container queries: the vertical version. ---- */
.story-flow .sf-hw { display: none; position: relative; }
.story-flow .sf-v { display: block; }
@container (min-width: 1000px) {
  .story-flow .sf-hw { display: block; }
  .story-flow .sf-v { display: none; }
}
.story-flow .sf-svg { width: 100%; height: auto; max-width: 1050px; margin: 0 auto; overflow: visible; }
.story-flow .sf-svg text { font-family: inherit; }
.story-flow .sf-rib { fill: var(--sf-flow); stroke: var(--sf-plate); stroke-width: 1; }
.story-flow .sf-hair { fill: none; stroke: var(--sf-flow); stroke-width: 1.5; }
.story-flow .sf-trunk { fill: var(--sf-flow); }
.story-flow .sf-notch { fill: none; stroke: #fff; stroke-width: 2; }
.story-flow .sf-lead { fill: none; stroke: var(--sf-line2); stroke-width: 1; }
.story-flow .sf-brk { fill: none; stroke: var(--sf-line2); stroke-width: 1; }
.story-flow .sf-pd { display: none; fill: var(--sf-accent); pointer-events: none; }
.story-flow .sf-sn { font-size: 13px; font-weight: 600; fill: var(--sf-ink); }
.story-flow .sf-sc { font-size: 13px; fill: var(--sf-body); font-variant-numeric: tabular-nums; }
.story-flow .sf-trunk-t { font-size: 13px; font-weight: 600; fill: var(--sf-ink); font-variant-numeric: tabular-nums; }
.story-flow .sf-trunk-l { font-size: 13px; fill: var(--sf-body); }

.story-flow:lang(zh) .sf-sc, .story-flow:lang(zh) .sf-trunk-l,
.story-flow:lang(zh) .sf-sb, .story-flow:lang(zh) .sf-el, .story-flow:lang(zh) .sf-st { font-size: 13.75px; }

/* nodes */
.story-flow .sf-hit { fill: #fff; fill-opacity: 0; pointer-events: all; }
.story-flow .sf-node, .story-flow .sf-end { outline: none; cursor: default; }
.story-flow .sf-dot { fill: var(--sf-accent); }
.story-flow .sf-nm { font-size: 13px; font-weight: 600; fill: var(--sf-ink); }
.nx-motion .story-flow .sf-nm { transition: fill var(--sf-tr); }
.story-flow .sf-caption { overflow: visible; }
.story-flow .sf-caption-wrap { text-align: center; line-height: 16px; overflow-wrap: normal; hyphens: none; }
.story-flow .sf-sb { font-size: 13px; color: var(--sf-body); fill: var(--sf-body); }
.story-flow .sf-node:hover .sf-nm, .story-flow .sf-node.is-on .sf-nm { fill: var(--sf-accent); }
.story-flow .sf-endbox { fill: var(--sf-accent); }
.story-flow .sf-en { font-size: 28px; font-weight: 700; fill: #fff; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.story-flow .sf-el { font-size: 13px; font-weight: 500; fill: #fff; }

/* the chapter-split label: small, neutral, upright */
.story-flow .sf-st { font-size: 13px; fill: var(--sf-muted); }

/* tooltip (HTML, placed by the script) */
.story-flow .sf-tip { position: absolute; z-index: 5; max-width: 290px; padding: 8px 12px; background: #fff; color: var(--sf-ink);
  border: 1px solid var(--sf-line2); border-radius: 8px; font-size: 13px; line-height: 1.5; pointer-events: none; text-wrap: balance; }
.story-flow .sf-tip[hidden] { display: none; }
.story-flow .sf-tip b { display: block; margin-bottom: 2px; font-size: 13px; }

/* ---- vertical version: source rows, a thin rail with 10px dots, flat end box ---- */
.story-flow .sf-vsrc { position: relative; padding: 0 0 var(--merge-h) 44px; }
.story-flow .sf-vm { position: absolute; left: 0; top: 0; }
.story-flow .sf-vlist li { display: flex; align-items: center; justify-content: space-between; gap: 8px; height: 36px; padding-left: 8px; font-size: 13px; }
.story-flow .sf-ld { flex: 1; align-self: flex-end; margin: 0; border-bottom: 1px dotted var(--sf-line2); transform: translateY(-5px); }
.story-flow .sf-vnm { min-width: 0; font-weight: 600; color: var(--sf-ink); }
.story-flow .sf-vct { flex: none; font-size: 13px; color: var(--sf-body); font-variant-numeric: tabular-nums; }
.story-flow .sf-vs { position: relative; padding-left: 44px; }
.story-flow .sf-vs--a::before { content: ""; position: absolute; top: 0; bottom: 0; left: 5px; width: var(--rail); background: var(--sf-flow); }
/* the flowing dots on the rail (user-approved motion for this figure): one full-height strip per dot, moved by transform only */
.story-flow .sf-vdots { display: none; position: absolute; z-index: 0; left: 5px; width: var(--rail); top: 0; bottom: 0; overflow: hidden; pointer-events: none; }
.story-flow .sf-vdf { position: absolute; left: 0; right: 0; top: 0; bottom: var(--sf-lastH, 90px); }
.story-flow .sf-vdk { position: absolute; left: 0; right: 0; top: 0; bottom: 0; }
.story-flow .sf-vd { position: absolute; left: 0; right: 0; top: 0; height: 100%; opacity: 0; }
.story-flow .sf-vd::before { content: ""; position: absolute; top: 0; left: var(--x, 50%); width: 4.4px; height: 4.4px; margin: -2.2px 0 0 -2.2px; border-radius: 50%; background: var(--sf-accent); }
.story-flow .sf-vs--b { padding-top: 14px; }
.story-flow .sf-vs--b::before { content: ""; position: absolute; top: 0; height: 14px; left: calc(5px + var(--rail) / 2 - var(--rail-b) / 2); width: var(--rail-b); background: var(--sf-flow); }
.story-flow .sf-vtr { display: flex; align-items: center; gap: 8px; min-height: 44px; font-size: 13px; font-weight: 600; color: var(--sf-ink); font-variant-numeric: tabular-nums; }
.story-flow .sf-vtr b { font-weight: 600; }
.story-flow .sf-vstep { position: relative; padding: 0 0 16px; }
.story-flow .sf-vstep:last-child { padding-bottom: 14px; }
.story-flow .sf-vn { position: absolute; top: 6px; left: calc(5px + var(--rail) / 2 - 5px - 44px); width: 10px; height: 10px; border-radius: 50%; background: var(--sf-accent); z-index: 1; }
.story-flow .sf-vx { min-width: 0; }
.story-flow .sf-vnm2 { font-size: 15px; font-weight: 600; line-height: 22px; color: var(--sf-ink); }
.story-flow .sf-vsb { display: block; font-size: 13px; color: var(--sf-body); }
.story-flow .sf-vend { position: relative; z-index: 1; display: flex; align-items: center; gap: 14px; height: 64px; margin-left: -44px; padding: 0 16px; background: var(--sf-accent); border-radius: 8px; color: #fff; }
.story-flow .sf-ven { font-size: 28px; line-height: 1; font-weight: 700; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.story-flow .sf-vel { font-size: 13px; font-weight: 500; line-height: 1.35; color: #fff; }

/* Tablet: the research stages share one horizontal rail. */
@container (min-width: 560px) and (max-width: 999px) {
  .story-flow .sf-vs--a { padding-left: 0; }
  .story-flow .sf-vtr { padding-left: 44px; }
  .story-flow .sf-vs--a::before { top: 44px; bottom: auto; left: 5px; right: 5px; width: auto; height: 6px; }
  .story-flow .sf-vsteps { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 16px; }
  .story-flow .sf-vstep, .story-flow .sf-vstep:last-child { padding: 20px 0 0; }
  .story-flow .sf-vn { top: 0; left: 5px; }
  .story-flow .sf-vdots { top: 44px; bottom: auto; left: 5px; right: 5px; width: auto; height: 6px; }
  .story-flow .sf-vdf { bottom: 0; right: 20%; }
  .story-flow .sf-vd::before { top: var(--x, 50%); left: 0; }
  .story-flow .sf-vs--b { padding-left: 0; padding-top: 16px; }
  .story-flow .sf-vs--b::before { display: none; }
  .story-flow .sf-vend { margin-left: 0; }
}

/* Every changing state is armed by NXMotion only after its finalizer is registered. */
.nx-motion .story-flow.nx-motion-armed.sf-dots .sf-pd,
.nx-motion .story-flow.nx-motion-armed.sf-dots .sf-vdots { display: block; }
.nx-motion .story-flow.nx-motion-armed.sf-revealing .sf-vm { clip-path: inset(0 0 calc((1 - var(--sf-source-progress, 1)) * 100%) 0); }
.nx-motion .story-flow.nx-motion-armed.sf-revealing .sf-vs--a::before { transform: scaleY(var(--sf-trunk-progress, 1)); transform-origin: top; }
@container (min-width: 560px) and (max-width: 999px) {
  .nx-motion .story-flow.nx-motion-armed.sf-revealing .sf-vs--a::before { transform: scaleX(var(--sf-trunk-progress, 1)); transform-origin: left; }
}

@media (prefers-reduced-motion: reduce) {                 /* a static figure: no dots at all */
  .story-flow .sf-pd, .story-flow .sf-vdots { display: none !important; }
  .story-flow .sf-nm { transition: none !important; }
  .story-flow .sf-ribbons, .story-flow .sf-trunk-wrap, .story-flow .sf-vm { clip-path: none !important; }
  .story-flow .sf-notch, .story-flow .sf-vn { opacity: 1 !important; }
  .story-flow .sf-vs--a::before, .story-flow .sf-endbox, .story-flow .sf-vend { transform: none !important; }
}
@media print {
  .story-flow .sf-pd, .story-flow .sf-vdots { display: none !important; }
  .story-flow .sf-ribbons, .story-flow .sf-trunk-wrap, .story-flow .sf-vm { clip-path: none !important; }
  .story-flow .sf-notch, .story-flow .sf-vn { opacity: 1 !important; }
  .story-flow .sf-vs--a::before, .story-flow .sf-endbox, .story-flow .sf-vend { transform: none !important; }
}
"""

JS = r"""
(function () {
  function init(root) {
    var wrap = root.querySelector('.sf-hw'), tip = root.querySelector('.sf-tip'), items = {}, cur = null;
    if (!wrap || !tip) { return; }
    [].forEach.call(root.querySelectorAll('.sf-sr li[data-k]'), function (li) { items[li.getAttribute('data-k')] = li; });
    function mark(k, on) {
      [].forEach.call(root.querySelectorAll('[data-k="' + k + '"]'), function (el) {
        if (el.tagName !== 'LI') { el.classList[on ? 'add' : 'remove']('is-on'); }
      });
    }
    function hide() {
      if (cur) { mark(cur, false); cur = null; }
      tip.hidden = true;
    }
    function show(k) {
      var node = wrap.querySelector('[data-k="' + k + '"]'), li = items[k];
      hide();
      if (!node || !li) { return; }
      cur = k; mark(k, true);
      var r = node.getBoundingClientRect(), wr = wrap.getBoundingClientRect();
      if (!r.width) { return; }
      var b = document.createElement('b'), s = document.createElement('span');
      b.textContent = li.querySelector('.sf-nm').textContent;
      s.textContent = li.querySelector('.sf-tt').textContent.replace(/([0-9.,])(\u4e07)/g, '$1\u2060$2');
      tip.textContent = ''; tip.appendChild(b); tip.appendChild(s); tip.hidden = false;
      var left = r.left - wr.left + r.width / 2 - tip.offsetWidth / 2;
      tip.style.left = Math.max(0, Math.min(left, wr.width - tip.offsetWidth)) + 'px';
      var above = r.bottom - wr.top > wr.height * 0.45;
      tip.style.top = (above ? r.top - wr.top - tip.offsetHeight - 8 : r.bottom - wr.top + 6) + 'px';
    }
    [].forEach.call(wrap.querySelectorAll('[data-k]'), function (node) {
      var k = node.getAttribute('data-k');
      node.addEventListener('mouseenter', function () { show(k); });
      node.addEventListener('mouseleave', hide);
      node.addEventListener('click', function (e) { e.stopPropagation(); if (cur === k && !tip.hidden) { hide(); } else { show(k); } });
    });
    [].forEach.call(root.querySelectorAll('.sf-sr li[data-k]'), function (li) {
      var k = li.getAttribute('data-k');
      li.addEventListener('focus', function () { show(k); });
      li.addEventListener('blur', hide);
    });
    root.addEventListener('keydown', function (e) { if (e.key === 'Escape') { hide(); } });
    document.addEventListener('click', hide);
  }
  function motion(root) {
    var utility = window.NXMotion, svg = root.querySelector('.sf-svg'), vs = root.querySelector('.sf-vs--a');
    var sourceClip = root.querySelector('.sf-source-clip'), trunkClip = root.querySelector('.sf-trunk-clip');
    var sourceWidth = Number(sourceClip.getAttribute('width')), trunkWidth = Number(trunkClip.getAttribute('width'));
    var gates = [].slice.call(root.querySelectorAll('.sf-notch')), nodes = [].slice.call(root.querySelectorAll('.sf-vn'));
    var pipelineDots = [].slice.call(root.querySelectorAll('.sf-dot'));
    var marks = gates.concat(nodes, pipelineDots);
    var ends = [].slice.call(root.querySelectorAll('.sf-endbox, .sf-vend'));
    var handle, observer, context, layout;
    function mode() {
      if (root.querySelector('.sf-hw').getBoundingClientRect().width) return 'desktop';
      return getComputedStyle(root.querySelector('.sf-vsteps')).display === 'grid' ? 'tablet' : 'vertical';
    }
    function measure() {
      var last = root.querySelector('.sf-vstep:last-child');
      vs.style.setProperty('--sf-lastH', last.offsetHeight + 'px');
    }
    function final() {
      root.classList.remove('sf-revealing', 'sf-dots');
      root.style.removeProperty('--sf-source-progress'); root.style.removeProperty('--sf-trunk-progress');
      sourceClip.setAttribute('width', sourceWidth); trunkClip.setAttribute('width', trunkWidth);
      marks.forEach(function (el) { el.style.removeProperty('opacity'); });
      ends.forEach(function (el) { el.style.removeProperty('transform'); el.style.removeProperty('transform-origin'); el.style.removeProperty('transform-box'); });
      if (svg.pauseAnimations) svg.pauseAnimations();
      if (observer) observer.disconnect();
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('resize', resize);
    }
    function stop(reason) { if (handle && handle.active()) handle.finish(reason); }
    function inView() {
      var rect = root.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
    }
    function visibility() { if (document.hidden && context) stop('hidden'); }
    function resize() { measure(); if (context && mode() !== layout) stop('layout-change'); }
    function dots(ctx) {
      if (!ctx.active()) return;
      root.classList.remove('sf-revealing'); root.classList.add('sf-dots');
      if (layout === 'desktop') {
        var animations = [].slice.call(svg.querySelectorAll('animateMotion, animate'));
        if (animations.some(function (el) { return typeof el.beginElementAt !== 'function' || typeof el.endElement !== 'function'; })) {
          ctx.finish('unsupported-animation'); return;
        }
        if (svg.unpauseAnimations) svg.unpauseAnimations();
        root.setAttribute('data-sf-dot-start', svg.getCurrentTime ? svg.getCurrentTime() : 2.8);
        animations.forEach(function (el) {
          if (!ctx.active()) return;
          ctx.track(el); el.beginElementAt(-Number(el.getAttribute('data-phase')));
        });
      } else {
        [].forEach.call(root.querySelectorAll('.sf-vd'), function (el) {
          var kept = el.parentNode.classList.contains('sf-vdk'), direction = layout === 'tablet' ? 'X' : 'Y';
          ctx.animate(el, [
            {transform: 'translate' + direction + '(0)', opacity: 0, offset: 0},
            {opacity: .5, offset: kept ? .04 : .05},
            {opacity: .5, offset: kept ? .96 : .9},
            {transform: 'translate' + direction + '(100%)', opacity: 0, offset: 1}
          ], {duration: 12000, iterations: 2, delay: -Number(el.getAttribute('data-phase')) * 1000, easing: 'linear'});
        });
      }
      ctx.timer(function () { ctx.finish('complete'); }, 24000);
    }
    measure(); final();
    if (!utility || !utility.allowed()) return;
    handle = utility.once(root, function (ctx) {
      context = ctx; layout = mode();
      if (document.hidden || !inView()) { ctx.finish(document.hidden ? 'hidden' : 'offscreen'); return; }
      document.addEventListener('visibilitychange', visibility);
      window.addEventListener('resize', resize);
      observer = new IntersectionObserver(function (entries) {
        if (entries.some(function (entry) { return !entry.isIntersecting; })) stop('offscreen');
      });
      observer.observe(root);
      root.classList.add('sf-revealing');
      var started;
      function frame(time) {
        if (!ctx.active()) return;
        if (!inView()) { ctx.finish('offscreen'); return; }
        if (started === undefined) started = time;
        var elapsed = time - started;
        var source = Math.min(1, elapsed / 450), trunk = Math.max(0, Math.min(1, (elapsed - 450) / 1050));
        sourceClip.setAttribute('width', sourceWidth * source); trunkClip.setAttribute('width', trunkWidth * trunk);
        root.style.setProperty('--sf-source-progress', source); root.style.setProperty('--sf-trunk-progress', trunk);
        gates.forEach(function (el, i) { el.style.opacity = Math.max(0, Math.min(1, (elapsed - 1500 - i * 180) / 180)); });
        nodes.forEach(function (el, i) { el.style.opacity = Math.max(0, Math.min(1, (elapsed - 1500 - i * 180) / 180)); });
        pipelineDots.forEach(function (el) {
          var arrival = (Number(el.getAttribute('cx')) - Number(trunkClip.getAttribute('x'))) / trunkWidth;
          el.style.opacity = trunk >= arrival ? 1 : 0;
        });
        var end = .96 + .04 * Math.max(0, Math.min(1, (elapsed - 2400) / 400));
        ends.forEach(function (el) { el.style.transformBox = 'fill-box'; el.style.transformOrigin = 'right center'; el.style.transform = 'scaleX(' + end + ')'; });
        if (elapsed < 2800) ctx.raf(frame);
        else {
          sourceClip.setAttribute('width', sourceWidth); trunkClip.setAttribute('width', trunkWidth);
          marks.forEach(function (el) { el.style.removeProperty('opacity'); });
          ends.forEach(function (el) { el.style.removeProperty('transform'); });
          ctx.releaseIntro();
          dots(ctx);
        }
      }
      /* Arm only drawing geometry; source names and all numeric labels remain final. */
      sourceClip.setAttribute('width', 0); trunkClip.setAttribute('width', 0);
      root.style.setProperty('--sf-source-progress', 0); root.style.setProperty('--sf-trunk-progress', 0);
      marks.forEach(function (el) { el.style.opacity = 0; });
      ctx.raf(frame);
    }, {finish: final, threshold: .35, maxDuration: 28000});
  }
  [].forEach.call(document.querySelectorAll('.story-flow'), function (root) {
    if (!root.getAttribute('data-sf-init')) { root.setAttribute('data-sf-init', '1'); init(root); motion(root); }
  });
})();
"""


# --------------------------------------------------------------------------- assembly

def render_partial() -> str:
    d = load_funnel()
    g = geometry(d)
    sr = []
    for key, idx in STEPS:
        tip = t("sf.%s.tip" % idx)
        sr.append('<li tabindex="0" data-k="%s"><span class="sf-nm">%s</span><span class="sf-tt">%s</span></li>' % (key, t("sf.%s.name" % idx), tip))

    head = [
        "<!-- GENERATED by scripts/viz_site/make_story_flow.py from deploy_output/funnel_data.json (geometry only).",
        "     Do not edit by hand: re-run the script; python3 scripts/viz_site/make_story_flow.py --check fails when this file is stale.",
        "     Converging-flow figure of the Screening page, built to deploy_output/_release/design_spec.md (v3): it IS the figure frame",
        "     (plate surface, hairline border, radius 12): do not wrap it in another frame. No links. Four source ribbons (thickness",
        "     proportional to the square root of the count) merge into one flat trunk that passes Classify (long federal bills peel into up",
        "     to 8 chapter strands that re-merge at Extract), Extract, Literature check and Score, then Screen: five white notches step the",
        "     trunk down, with no count at a notch. SVG authored at 1050 units; narrower than 1000px of inner width (container query)",
        "     a vertical version is shown instead. Words are t-colon-key placeholders, figures are num-colon-expression placeholders.",
        "     Self-contained: every rule is scoped under .story-flow and every class carries an sf- prefix. Light theme only; --nx-* tokens with fallbacks.",
        "     Geometry: Federal Regulations %d, Federal Legislation %d, State Legislation %d, State/Local Executive %d; total %d, candidates %d. -->"
        % (*g["counts"], g["total"], g["short"]),
    ]
    body = [
        "<style>", CSS.strip("\n"), "</style>",
        '<div class="story-flow" data-component="story-flow">',
        '<div class="sf-hw">', desktop_svg(g), '<div class="sf-tip" hidden aria-hidden="true"></div>', "</div>",
        vertical_html(g),
        '<ol class="sf-sr" aria-label="%s">' % t("sf.steps.aria"), *sr, "</ol>",
        "</div>",
        "<script>", JS.strip("\n"), "</script>",
    ]
    return "\n".join(head + body) + "\n"


# --------------------------------------------------------------------------- checks

PH = re.compile(r"\{\{(?:t|tf):([A-Za-z0-9_.\-]+)")
ENGINEERING = re.compile(r"pydantic|\bwal\b|\bd1\b|worker|sqlite|\bapi\b|json|wrangler|\br2\b|sqrt|square root|schema|llm call|structural marker|retry|boilerplate|skipped", re.I)
# words that must not appear anywhere in the rendered figure (text, popovers, aria labels), EN and ZH (design review round 1, B17)
VAGUE = [r"\bsuch as\b", r"\be\.g\.", r"\betc\b", r"\band more\b", r"\bvarious\b", r"\blike\b", r"\bsome\b", r"\band so on\b",
         "\u4f8b\u5982", "\u4e4b\u7c7b", "\u7b49(?![\u7ea7\u4e8e\u5f85\u540c])", "\u5982(?![\u4f55\u540c\u679c])", "\u4e4b\u7c7b"]
BANNED = [(r"\bCore\b", ""), (r"skipped", "i"), (r"boilerplate", "i"), (r"1\.6%", ""), (r"square root", "i"), (r"schema", "i"),
          (r"LLM call", "i"), (r"structural marker", "i"), (r"retry", "i"),
          ("\u6838\u5fc3", ""), ("\u8df3\u8fc7", ""), ("\u6837\u677f", ""), ("\u5e73\u65b9\u6839", ""), ("\u91cd\u8bd5", ""),
          ("\u7ed3\u6784\u6807\u8bb0", ""), ("LLM \u9010\u7ae0", ""), ("\u7b2c\u4e8c\u6b21\u6a21\u578b\u8c03\u7528", "")]


def dictionary_problems(text: str) -> list:
    out = []
    en = json.loads((I18N / "story_flow.en.json").read_text(encoding="utf-8"))
    zh = json.loads((I18N / "story_flow.zh.json").read_text(encoding="utf-8"))
    if set(en) != set(zh):
        out.append("en/zh key sets differ: %s" % sorted(set(en) ^ set(zh)))
    used = set(PH.findall(text))
    for k in sorted(used - set(en)):
        out.append("key used by the partial but missing from the dictionaries: %s" % k)
    for k in sorted(set(en) - used):
        out.append("dictionary key never used by the partial: %s" % k)
    slot = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for k in en:
        if k in zh and set(slot.findall(en[k])) != set(slot.findall(zh[k])):
            out.append("placeholder slots differ between en and zh for %s" % k)
    for k, v in en.items():
        if ENGINEERING.search(v):
            out.append("engineering word in en value %s: %r" % (k, v))
    for k, v in en.items():
        # The Extract and Score popovers explain terms (RD, DiD, the four scores) and may run longer (content check of R25, S2).
        limit = 32 if k in ("sf.n2.tip", "sf.n4.tip") else 18
        if k.endswith(".tip") and len(v.split()) > limit:
            out.append("popover %s has %d words (limit %d; only the Extract and Score popovers may be longer)" % (k, len(v.split()), limit))
    for lang_, d in (("en", en), ("zh", zh)):                         # vague wording: name the items or drop the tail
        for k, v in d.items():
            for pat in VAGUE:
                if re.search(pat, v, re.I):
                    out.append("vague wording %r in %s value %s: %r" % (pat, lang_, k, v))
    for lang_, d in (("en", en), ("zh", zh)):
        for k, v in d.items():
            for pat, fl in BANNED:
                if re.search(pat, v, re.I if fl else 0):
                    out.append("banned wording %r in %s value %s" % (pat, lang_, k))
    if re.search(r"<a[\s>]", text.split("<script>")[0]):
        out.append("the partial contains a link")
    if re.search(r"italic", CSS):
        out.append("the CSS contains an italic (the spec forbids italics)")
    return out


def load_build():
    spec = importlib.util.spec_from_file_location("viz_build", HERE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SELFCHECK_JS = r"""
(function () {
  var q = location.search;
  if (q.indexOf('selfcheck') < 0) { return; }
  var mw = /[?&]w=(\d+)/.exec(q);
  if (mw) {                                   /* parent mode: the page itself in an iframe of exactly w px (true narrow viewport) */
    window.addEventListener('message', function (e) {
      if (e.data && e.data.sfcheck) { document.body.setAttribute('data-sfcheck', JSON.stringify(e.data.sfcheck)); }
    });
    window.addEventListener('load', function () {
      var f = document.createElement('iframe');
      f.style.cssText = 'border:0;width:' + mw[1] + 'px;height:1500px;position:absolute;left:0;top:0;background:#fff';
      f.src = location.pathname + '?selfcheck';
      document.body.appendChild(f);
    });
    return;
  }
  var BANNED = %BANNED%;
  function run() {
    var out = { bad: [], notes: {} }, root = document.querySelector('.story-flow'), svg = root.querySelector('.sf-svg');
    var fr = root.getBoundingClientRect(), r = svg.getBoundingClientRect();
    out.vw = window.innerWidth; out.frameH = Math.round(fr.height * 10) / 10; out.frameW = Math.round(fr.width);
    out.mode = r.width > 0 ? 'desktop' : getComputedStyle(root.querySelector('.sf-vsteps')).display === 'grid' ? 'tablet' : 'vertical';
    out.scrollW = document.documentElement.scrollWidth;
    out.links = root.querySelectorAll('a').length;
    out.sr = [].map.call(root.querySelectorAll('.sf-sr li'), function (li) { return li.getAttribute('data-k'); });
    var all = root.textContent + ' ' + [].map.call(root.querySelectorAll('[aria-label]'), function (e) { return e.getAttribute('aria-label'); }).join(' ');
    out.banned = BANNED.filter(function (b) { return new RegExp(b[0], b[1]).test(all); }).map(function (b) { return b[0]; });
    out.italic = [].filter.call(root.querySelectorAll('*'), function (e) { return getComputedStyle(e).fontStyle === 'italic'; }).length;
    var minpx = 1e9, minwho = '';
    if (out.mode === 'desktop') {
      var vb = svg.viewBox.baseVal, s = r.width / vb.width;
      out.scale = Math.round(s * 1000) / 1000; out.svgH = Math.round(r.height * 10) / 10;
      var texts = [].slice.call(svg.querySelectorAll('text, .sf-caption .sf-sb'));
      texts.forEach(function (t) {
        var px = parseFloat(getComputedStyle(t).fontSize) * s;
        if (px < minpx) { minpx = px; minwho = t.textContent.trim().slice(0, 24); }
      });
      var inner = { 'sf-en': 1, 'sf-el': 1 };
      var boxes = texts.map(function (t) {
        var b = t.getBoundingClientRect();
        return { txt: t.textContent.trim(), cls: t.getAttribute('class'), l: b.left, r: b.right, tp: b.top + 2.5 * s, bt: b.bottom - 3.5 * s };
      });
      var gap = 8 * s - 0.5;                                   /* label boxes keep >= 8 units apart (spec section 8) */
      for (var i = 0; i < boxes.length; i++) {
        for (var j = i + 1; j < boxes.length; j++) {
          var a = boxes[i], b = boxes[j];
          if (a.tp < b.bt && b.tp < a.bt && a.l - gap < b.r && b.l - gap < a.r) { out.bad.push('labels closer than 8 units: "' + a.txt + '" / "' + b.txt + '"'); }
        }
      }
      var shapes = [].slice.call(svg.querySelectorAll('.sf-endbox')).map(function (e) {
        var b = e.getBoundingClientRect(); return { cls: e.getAttribute('class'), l: b.left, r: b.right, tp: b.top, bt: b.bottom };
      });
      boxes.forEach(function (a) {
        if (inner[a.cls]) { return; }
        shapes.forEach(function (b) {
          if (a.l - gap < b.r && b.l - gap < a.r && a.tp < b.bt && b.tp < a.bt) { out.bad.push('label near the end box: "' + a.txt + '"'); }
        });
      });
      var fills = [].slice.call(svg.querySelectorAll('.sf-trunk, .sf-rib, .sf-hair')), pt = svg.createSVGPoint();
      boxes.forEach(function (a) {
        if (inner[a.cls]) { return; }
        var hit = 0;
        for (var xi = 0; xi <= 8; xi++) {
          for (var yi = 0; yi <= 3; yi++) {
            var cx = a.l + (a.r - a.l) * xi / 8, cy = a.tp + (a.bt - a.tp) * yi / 3;
            fills.forEach(function (p) {
              var m = p.getScreenCTM().inverse(); pt.x = cx; pt.y = cy;
              if (p.isPointInFill(pt.matrixTransform(m))) { hit++; }
            });
          }
        }
        if (hit) { out.bad.push('label on a band: "' + a.txt + '" (' + hit + ' sample points)'); }
      });
      var tips = {}, wrap = root.querySelector('.sf-hw'), tip = root.querySelector('.sf-tip');
      [].forEach.call(wrap.querySelectorAll('.sf-node, .sf-end'), function (node) {
        node.dispatchEvent(new Event('mouseenter'));
        var tr = tip.getBoundingClientRect(), wr = wrap.getBoundingClientRect();
        if (tr.left < wr.left - 1 || tr.right > wr.right + 1) { out.bad.push('tooltip ' + node.getAttribute('data-k') + ' leaves the graphic'); }
        node.dispatchEvent(new Event('mouseleave'));
      });
    } else {
      [].slice.call(root.querySelectorAll('.sf-v *')).forEach(function (e) {
        var own = [].some.call(e.childNodes, function (c) { return c.nodeType === 3 && c.textContent.trim(); });
        if (!own) { return; }
        var px = parseFloat(getComputedStyle(e).fontSize);
        if (px < minpx) { minpx = px; minwho = e.textContent.trim().slice(0, 24); }
      });
    }
    out.minpx = Math.round(minpx * 100) / 100; out.minwho = minwho;
    /* ---- the flowing dots: counts, routes, reduced motion, and sampled motion ---- */
    var pd = [].slice.call(root.querySelectorAll('.sf-pd')), vd = [].slice.call(root.querySelectorAll('.sf-vd'));
    out.nPd = pd.length; out.nVd = vd.length; out.nMotion = root.querySelectorAll('animateMotion').length;
    out.badPaths = [].filter.call(root.querySelectorAll('animateMotion mpath'), function (m) {
      var el = document.getElementById((m.getAttribute('href') || '').slice(1));
      return !el || el.tagName.toLowerCase() !== 'path';
    }).length;
    out.nMpath = root.querySelectorAll('animateMotion mpath').length;
    out.finite = [].every.call(root.querySelectorAll('animateMotion, animate'), function (a) { return a.getAttribute('repeatCount') === '2' && a.getAttribute('begin') === 'indefinite'; });
    out.cssFinite = document.getAnimations().every(function (a) { return a.effect.getTiming().iterations <= 2; });
    out.pdDisplay = pd.length ? getComputedStyle(pd[0]).display : ''; out.vdotsDisplay = getComputedStyle(root.querySelector('.sf-vdots')).display;
    out.cssAnims = document.getAnimations().filter(function (a) { return a.playState === 'running'; }).length;
    out.smilPaused = svg.animationsPaused ? svg.animationsPaused() : null;
    var rmOn = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!rmOn && out.mode === 'desktop') {
      var s0 = svg.getBoundingClientRect(), sc0 = s0.width / svg.viewBox.baseVal.width;
      svg.pauseAnimations();
      var snap = function (tm) {
        svg.setCurrentTime(Number(root.getAttribute('data-sf-dot-start') || 2.8) + tm);
        return pd.map(function (c) { var b = c.getBoundingClientRect(); return { x: ((b.left + b.right) / 2 - s0.left) / sc0, y: ((b.top + b.bottom) / 2 - s0.top) / sc0, o: parseFloat(getComputedStyle(c).opacity) }; });
      };
      var a1 = snap(1.0), a2 = snap(2.0), a3 = snap(3.0);
      out.moved = a1.filter(function (d, i) { return Math.hypot(a2[i].x - d.x, a2[i].y - d.y) > 1 && Math.hypot(a3[i].x - a2[i].x, a3[i].y - a2[i].y) > 1; }).length;
      var reach = pd.map(function () { return -1; }), top = 0, low = 1e9;
      for (var tm = 0; tm < %DUR%; tm += 0.1) {
        snap(tm).forEach(function (d, i) { if (d.o > 0.2 && d.x > reach[i]) { reach[i] = d.x; } if (d.o > 0.2) { top = Math.max(top, d.o); } });
      }
      out.survivors = reach.filter(function (x) { return x > %END_X% - 6; }).length;
      out.filtered = reach.filter(function (x) { return x < %GATE5% + 40; }).length;
      out.peakOpacity = Math.round(top * 100) / 100;
      var ys = snap(0.5).concat(snap(4.1)).concat(snap(6.3)).filter(function (d) { return d.o > 0.2; });
      out.dotInBand = ys.filter(function (d) { return d.x > %XE% + 45; }).every(function (d) { return d.y > %TOP% + 1 && d.y < %BOT% - 1; });
      svg.setCurrentTime(0); svg.unpauseAnimations();
    }
    if (!rmOn && out.mode !== 'desktop') {
      var an = document.getAnimations().filter(function (a) { return a.effect && a.effect.target && a.effect.target.classList && a.effect.target.classList.contains('sf-vd'); });
      var vs2 = function (tm) { an.forEach(function (a) { a.pause(); a.currentTime = tm * 1000; }); return vd.map(function (e) { var b = e.getBoundingClientRect(); return out.mode === 'tablet' ? b.left : b.top; }); };
      var v1 = vs2(1.0), v2 = vs2(2.0), v3 = vs2(3.0);
      out.moved = v1.filter(function (t, i) { return Math.abs(v2[i] - t) > 1 && Math.abs(v3[i] - v2[i]) > 1; }).length;
      an.forEach(function (a) { a.currentTime = 0; a.play(); });
    }
    if (window.parent !== window) { window.parent.postMessage({ sfcheck: out }, '*'); }
    else { document.body.setAttribute('data-sfcheck', JSON.stringify(out)); }
  }
  window.addEventListener('load', function () { setTimeout(run, 3200); });
})();
"""

TIP_JS = r"""
(function () {
  var m = /[?&]tip=([a-z]+)/.exec(location.search), mt = /[?&]t=([0-9.]+)/.exec(location.search);
  window.addEventListener('load', function () {
    setTimeout(function () {
      if (m) {
        var node = document.querySelector('.sf-hw [data-k="' + m[1] + '"]');
        if (node) { node.dispatchEvent(new Event('mouseenter')); }
      }
      if (mt) {                                   /* freeze the dots at second t, for the screenshots */
        var svg = document.querySelector('.sf-svg');
        if (svg && svg.getBoundingClientRect().width) { svg.pauseAnimations(); svg.setCurrentTime(parseFloat(mt[1])); }
        document.getAnimations().forEach(function (a) { a.pause(); a.currentTime = parseFloat(mt[1]) * 1000; });
      }
    }, 80);
  });
})();
"""

PREVIEW = """<!DOCTYPE html>
<html lang="{{html_lang}}">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="robots" content="noindex">
<link rel="icon" href="data:,">
<title>Story flow preview</title>
{{partial:head_css}}
{{partial:motion}}
%s<style> .pv-note { margin: 0 0 0.8rem; color: #6b7280; font-size: 0.82rem; } .pv-col .story { padding-top: 0; } </style>
</head>
<body>
<main class="page">
<div class="pv-col">
<p class="pv-note">Preview of partials/story_flow.html (%s)</p>
<div class="story">
{{partial:story_flow}}
</div>
</div>
</main>
<script>%s</script>
<script>%s</script>
</body>
</html>
"""


def selfcheck_js() -> str:
    return (SELFCHECK_JS.replace("%BANNED%", json.dumps(BANNED)).replace("%DUR%", n(DUR)).replace("%END_X%", n(END_X))
            .replace("%GATE5%", n(GATE_X[-1])).replace("%XE%", n(XE)).replace("%TOP%", n(TOP)).replace("%BOT%", n(BOT)))


def write_previews() -> int:
    build = load_build()
    errs = build.Errors()
    for lang in build.LANGS:
        tokens = ("<style>\n%s</style>\n" % TOKENS.read_text(encoding="utf-8")) if TOKENS.is_file() else ""
        template = PREVIEW % (tokens.replace("{{", "{ {"), lang.upper(), TIP_JS.replace("{{", "{ {"), selfcheck_js().replace("{{", "{ {"))
        strings = dict(build.load_dict("common", lang, errs) or {})
        for name in build.partials_used(template, errs):
            strings.update(build.load_dict(name, lang, errs) or {})
        out = build.render(template, {"lang": lang, "page": "home", "strings": strings}, errs, "story_flow preview")
        build.lint_output(out, lang, "story_flow preview", errs, "home")
        if not errs.errors:
            PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            dest = PREVIEW_DIR / ("story_flow_preview_%s.html" % lang)
            dest.write_text(out, encoding="utf-8")
            print("wrote %s (%d bytes)" % (dest.relative_to(ROOT), len(out.encode("utf-8"))))
    for w in errs.warnings:
        print("warning: " + w, file=sys.stderr)
    for e in errs.errors:
        print("error: " + e, file=sys.stderr)
    return 1 if errs.errors else 0


# --------------------------------------------------------------------------- headless Chrome: shots and self-check

def _chrome():
    for cand in (os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if cand and Path(cand).is_file():
            return cand
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


CHROME_FLAGS = ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--force-device-scale-factor=1"]
SHOT_SIZES = [(1440, 400), (1100, 400), (820, 860), (390, 900), (360, 900), (320, 900)]       # (viewport width, window height)
MIN_WINDOW = 500                                                      # Chrome will not lay out a window narrower than this


def _shot(chrome: str, url: str, dest: Path, width: int, height: int, extra=()) -> None:
    win = max(width, MIN_WINDOW)
    cmd = [chrome, *CHROME_FLAGS, "--window-size=%d,%d" % (win, height), "--virtual-time-budget=3000", "--screenshot=%s" % dest, *extra, url]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _wrapper(lang: str, width: int, height: int, query: str = "") -> Path:
    """A page holding the preview in an iframe of exactly `width` px, centred in the (>= 500 px) window so that the centred crop done
    by sips afterwards leaves exactly the iframe; media and container queries then see a true narrow viewport."""
    src = (PREVIEW_DIR / ("story_flow_preview_%s.html" % lang)).as_uri() + query
    dest = PREVIEW_DIR / ("_flow_v7_wrap_%s_%d.html" % (lang, width))
    dest.write_text('<!DOCTYPE html><html><body style="margin:0;background:#fff;display:flex;justify-content:center"><iframe src="%s" style="border:0;width:%dpx;height:%dpx" '
                    'title="preview"></iframe></body></html>' % (src, width, height), encoding="utf-8")
    return dest


def shots() -> int:
    chrome = _chrome()
    if not chrome:
        print("--shots needs Chrome: install it or set CHROME=/path/to/chrome", file=sys.stderr)
        return 2
    made = []
    for lang in ("en", "zh"):
        page = (PREVIEW_DIR / ("story_flow_preview_%s.html" % lang)).as_uri()
        for width, height in SHOT_SIZES:
            dest = PREVIEW_DIR / ("flow_v7_%s_%d.png" % (lang, width))
            if width < MIN_WINDOW:
                wrap = _wrapper(lang, width, height)
                _shot(chrome, wrap.as_uri(), dest, width, height)
                subprocess.run(["sips", "--cropToHeightWidth", str(height), str(width), str(dest)], capture_output=True)
                wrap.unlink()
            else:
                _shot(chrome, page, dest, width, height)
            made.append(dest)
        for tm in (1.5, 4.0, 6.5):                                                              # the dots frozen at three moments (desktop)
            dest = PREVIEW_DIR / ("flow_v7_%s_dots_t%s.png" % (lang, str(tm).replace(".", "p")))
            _shot(chrome, page + "?t=" + str(tm), dest, 1440, 400)
            made.append(dest)
        wrap = _wrapper(lang, 390, 900, "?t=3.0")                                               # and on the vertical rail
        dest = PREVIEW_DIR / ("flow_v7_%s_dots_390.png" % lang)
        _shot(chrome, wrap.as_uri(), dest, 390, 900)
        subprocess.run(["sips", "--cropToHeightWidth", "900", "390", str(dest)], capture_output=True)
        wrap.unlink()
        made.append(dest)
        for key in ("classify", "extract", "lit", "score", "screen"):                                   # tooltips on the desktop layout
            dest = PREVIEW_DIR / ("flow_v7_%s_tip_%s.png" % (lang, key))
            _shot(chrome, page + "?tip=" + key, dest, 1440, 420)
            made.append(dest)
    for dest in made:
        print(("wrote " if dest.is_file() else "MISSING ") + str(dest.relative_to(ROOT)))
    return 0 if all(p.is_file() for p in made) else 1


def selfcheck() -> int:
    import html
    chrome = _chrome()
    if not chrome:
        print("--selfcheck needs Chrome: install it or set CHROME=/path/to/chrome", file=sys.stderr)
        return 2
    bad = 0
    # (viewport width, "?w=" iframe mode, expected layout, frame height cap)
    cases = [(1440, False, "desktop", 260), (1100, False, "desktop", 260), (820, True, "tablet", 520), (390, True, "vertical", 700),
             (360, True, "vertical", 720), (320, True, "vertical", 760)]
    for lang in ("en", "zh"):
        base = (PREVIEW_DIR / ("story_flow_preview_%s.html" % lang)).as_uri() + "?selfcheck"
        for width, framed, mode, cap in cases:
            for extra, tag in (((), ""), (("--force-prefers-reduced-motion",), " reduced-motion")):
                url = base + ("&w=%d" % width if framed else "")
                win = max(width, MIN_WINDOW) if not framed else 1000
                run = subprocess.run([chrome, *CHROME_FLAGS, "--window-size=%d,900" % win, "--virtual-time-budget=4000", *extra, "--dump-dom", url],
                                     capture_output=True, text=True, timeout=120)
                m = re.search(r'data-sfcheck="([^"]*)"', run.stdout)
                if not m:
                    print("FAIL %s %d%s: no self-check result" % (lang, width, tag), file=sys.stderr)
                    bad += 1
                    continue
                res = json.loads(html.unescape(m.group(1)))
                problems = list(res["bad"])
                if res["mode"] != mode:
                    problems.append("layout is %s, expected %s" % (res["mode"], mode))
                if res["minpx"] < 12:
                    problems.append("smallest text renders at %.2f px (%s)" % (res["minpx"], res["minwho"]))
                if res["frameH"] > cap:
                    problems.append("frame is %.0f px tall (cap %d)" % (res["frameH"], cap))
                if res["banned"]:
                    problems.append("banned wording in the rendered figure: %s" % res["banned"])
                if res["links"]:
                    problems.append("%d <a> link(s) left in the graphic" % res["links"])
                # --- the flowing dots
                if not 10 <= res["nPd"] <= 16:
                    problems.append("%d desktop dots (want 10-16)" % res["nPd"])
                if not 8 <= res["nVd"] <= 10:
                    problems.append("%d vertical dots (want 8-10)" % res["nVd"])
                if res["nMotion"] != res["nPd"] or res["nMpath"] != res["nPd"] or res["badPaths"]:
                    problems.append("animateMotion: %d for %d dots, %d mpath, %d without an existing path id" % (res["nMotion"], res["nPd"], res["nMpath"], res["badPaths"]))
                if not res["finite"] or not res["cssFinite"]:
                    problems.append("a dot animation is not limited to two passes")
                if res["nPd"] > 20 or res["cssAnims"] > 20:
                    problems.append("more than 20 animated elements")
                if tag:                                                    # prefers-reduced-motion: a static figure
                    if res["pdDisplay"] != "none" or res["vdotsDisplay"] != "none":
                        problems.append("dots still displayed under prefers-reduced-motion (%s / %s)" % (res["pdDisplay"], res["vdotsDisplay"]))
                    if res["cssAnims"]:
                        problems.append("%d CSS animation(s) running under prefers-reduced-motion" % res["cssAnims"])
                    if res["smilPaused"] is not True:
                        problems.append("SVG animations not paused under prefers-reduced-motion")
                else:
                    want_moved = 0.9 * (res["nPd"] if res["mode"] == "desktop" else res["nVd"])
                    if res.get("moved", 0) < want_moved:
                        problems.append("only %s dots move between t=1,2,3 s (want >= %.0f)" % (res.get("moved"), want_moved))
                    if res["mode"] == "desktop":
                        if not 1 <= res["survivors"] <= 3:
                            problems.append("%d dots reach the end box (want 1-3, about 1 in 8)" % res["survivors"])
                        if res["filtered"] < res["nPd"] - 4:
                            problems.append("only %d dots fade out before the end (want >= %d)" % (res["filtered"], res["nPd"] - 4))
                        if not res["dotInBand"]:
                            problems.append("a visible dot leaves the trunk band")
                if res["italic"]:
                    problems.append("%d italic element(s)" % res["italic"])
                if res["scrollW"] > res["vw"]:
                    problems.append("horizontal scroll: document %d px wide in a %d px viewport" % (res["scrollW"], res["vw"]))
                if res["sr"] != [k for k, _ in STEPS]:
                    problems.append("screen-reader list is %s" % res["sr"])
                print("%s %s %4d px %-8s%s frame %5.1f px  min text %5.2f px  dots %d/%d%s" % (
                    "ok  " if not problems else "FAIL", lang, width, res["mode"], tag, res["frameH"], res["minpx"], res["nPd"], res["nVd"],
                    "" if tag else (" moved %s, %s reach the end" % (res.get("moved"), res.get("survivors", "-")))))
                for p in problems:
                    print("     " + p, file=sys.stderr)
                bad += 1 if problems else 0
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when partials/story_flow.html is stale vs funnel_data.json (or the dictionaries are off); write nothing")
    ap.add_argument("--preview", action="store_true", help="also write deploy_output/_landing_options/story_flow_preview_{en,zh}.html")
    ap.add_argument("--shots", action="store_true", help="--preview, then flow_v7_{en,zh}_{1440,1100,820,390}.png and tooltip PNGs in deploy_output/_landing_options (headless Chrome)")
    ap.add_argument("--selfcheck", action="store_true", help="--preview, then headless Chrome: smallest rendered text, label gaps, frame heights, count row, tooltips, reduced motion")
    args = ap.parse_args(argv)
    fresh = render_partial()
    problems = dictionary_problems(fresh)
    if len(fresh.encode("utf-8")) > MAX_BYTES:
        problems.append("partial is %d bytes (limit %d)" % (len(fresh.encode("utf-8")), MAX_BYTES))
    if args.check:
        current = OUT.read_text(encoding="utf-8-sig").replace("\r\n", "\n") if OUT.is_file() else None
        if current != fresh:
            problems.append("partials/story_flow.html is stale: run python3 scripts/viz_site/make_story_flow.py")
        for p in problems:
            print(p, file=sys.stderr)
        if problems:
            return 1
        print("story_flow.html is up to date")
        return 0
    for p in problems:
        print("warning: " + p, file=sys.stderr)
    OUT.write_text(fresh, encoding="utf-8")
    print("wrote %s (%d bytes)" % (OUT.relative_to(ROOT), len(fresh.encode("utf-8"))))
    if args.preview or args.shots or args.selfcheck:
        rc = write_previews()
        if rc:
            return rc
    if args.shots:
        rc = shots()
        if rc:
            return rc
    if args.selfcheck:
        return selfcheck()
    return 0


if __name__ == "__main__":
    sys.exit(main())
