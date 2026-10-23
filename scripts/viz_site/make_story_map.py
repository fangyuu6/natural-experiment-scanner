#!/usr/bin/env python3
"""Generate partials/story_map.html, the curated map card of the homepage hero: 8 publicly sourced staggered adoptions (stdlib only, 3.9+).

    python3 scripts/viz_site/make_story_map.py             # (re)write partials/story_map.html and the policy keys of i18n/story_map.*.json
    python3 scripts/viz_site/make_story_map.py --check     # exit 1 if the partial or those i18n keys are stale (like make_story_assets.py --check)
    python3 scripts/viz_site/make_story_map.py --preview   # also write deploy_output/_landing_options/story_map_preview_{en,zh}.html
                                                           # (preview-only: ?policy=<id> picks a policy, &focus=tab, &hover=<state code>, &open=1)
    python3 scripts/viz_site/make_story_map.py --selfcheck # validate_curated.py, then check EVERY policy in headless Chrome at 1400, 1100, 820
                                                           # and 390 px, EN + ZH (needs Chrome; set CHROME=/path); also proves the check can fail
    VIZ_SITE_SCRATCH_DIR=/path/to/scratch                  # optional preview/selfcheck output directory; previews embed their CSS

Input : scripts/viz_site/data/curated_adoption.json  8 policies in display order: per state the adoption year (its source urls stay in
                                                     that file and are validated there, the card does not use them), db_evidence (hand-checked
                                                     rows of our policy database); names and short names in EN and ZH. validate_curated.py
                                                     runs first (this script stops if it fails).
        data/geo/states-albers-10m.json, counties-albers-10m.json   public us-atlas@3 TopoJSON, already projected to 975 x 610
                                                     (Albers USA, Alaska and Hawaii as insets); gitignored
        design/design_tokens.css, design/design_hero.css   the art director's tokens and hero grid (design spec 14); the page must load both
Output: scripts/viz_site/partials/story_map.html   ONE card (.nx-frame.nx-mapcard, sits in .nx-herocard) + one provenance line + inline
        <style> + one JSON data block + a small <script>; no links, no source data; every sentence is a {{t:..}} / {{tf:..}} placeholder
        (i18n/story_map.{en,zh}.json). The policy names, short names, legend titles and scope notes in those files are generated from the data file (this
        script keeps them in sync; --check fails when they are stale), everything else in them is hand-written.

THE CARD (design spec 14.2 / 14.3). >= 1100 (hero grid, card ~622 px wide at 1440): header row (policy name left, "<n> states . <first>-<last>"
right), the map (static), a two-line readout, one legend row, then the selector: a 4 x 2 grid of the policies' short names (48 px cells).
<= 1099: the same 8 buttons are ONE horizontally scrolling row of pills above the map (>= 44 px, the selected one aligned at the leading edge instantly, an edge
fade on the overflowing sides); <= 600 the card is full-bleed. The selector is a role="radiogroup" of role="radio" buttons: arrows move focus
AND select (Down / Up move one row in the grid), Home / End, Space / Enter select, roving tabindex.
One finite chronological introduction uses NXMotion; explicit pause/resume/replay is available. Choosing a policy paints its final state immediately. Without JavaScript the
default policy (DEFAULT_POLICY) is drawn statically and the selector is hidden (a <noscript> rule).
  * States are coloured by the BIN of the year they adopted, five bins light to dark (--nx-b1 .. --nx-b5); states without the law are the
    plain land tone: no label, no legend entry, no text about them (hover shows the state name only). There is no "in place by" bucket and
    no hatch: these are real adoption years and there is no coverage concept.
  * BIN RULE (year_bins): five contiguous year ranges covering year_min..year_max. Equal width when the span is 5-11 years and every range holds
    a state (2016-2024 -> 2016-17, 2018-19, 2020-21, 2022-23, 2024); otherwise (a wide span such as paid family leave 2004-2024, or an equal
    bin that would be empty such as 2020-21 for ride-hailing) quantile bins: the distinct adoption years are cut into five consecutive groups
    with near-equal state counts (least squares around n/5), each bin running from the year after the previous one to its own last year. Every
    bin is non-empty; the legend labels each bin with its year range ("2016-17", "2018-22", a single year when the bin is one year).
  * Readout (no floating tooltip): hover, focus or tap on a state draws a 1.25 px outline and writes ONE 24 px line, "<State>: <year>" and, when
    the data file has db_evidence for that state and policy, " . In our database: <title> (<year>)"; nowrap, an ellipsis when it is too long
    (the full text is then in the title attribute and always in the live region's text), so nothing ever moves. Every coloured state is
    keyboard-focusable with an aria-label "<State>: <year>".
  * Database dots: a small white dot with a dark outline (3 px radius on screen) at an interior point of each state that has db_evidence for the
    selected policy; the legend entry "Bill in our database" appears only when the selected policy has a dot.
  * Legend (one row at >= 1280 px): title = the year basis of the policy (LEGEND_TITLES: "Year signed", "Year effective", "Year benefits start",
    "Year enacted"), the five bins with their year ranges (cells of RAMP_CELL px, tick labels >= 8 px apart), and "Bill in our database" only
    when the selected policy has a dot.
  * Under the card (12 px below the frame, left-aligned, one muted line): the provenance line (map.provenance; the per-policy captions were
    removed in R29). That is all: there is no Sources button or panel and no source data in the partial (user decision).

DATA IN THE PARTIAL: <script type="application/json" id="sm-d"> {"c": state codes (fixed order), "p": {policy: {"y": 51 years
  (0 = none), "b": the 5 bins, "e": {state: [db title, db year]}}}}. Geometry is shared.

GEOMETRY PIPELINE (this file, no third-party code): decode the quantised, delta-encoded arcs (transform scale/translate),
  simplify every arc once with Douglas-Peucker (TOL_STATE for the state topology, TOL_COUNTY for the county mesh), round to
  0.1 px, chain arcs into as few sub-paths as possible and emit relative-coordinate SVG path data. Shared arcs are simplified
  once, so neighbouring states never gap or overlap. The county mesh keeps the arcs shared by exactly two counties of the
  SAME state; the state border mesh keeps arcs shared by two states, the coastline those used by one state only. Rings
  smaller than MIN_RING_AREA px^2 are dropped (specks), never a state; rings and county arcs that do not reach 2 px inside the 975 x 610
  frame (the western Aleutians) are dropped too, so nothing is drawn outside the card. Every state also carries its label point (data-cx,
  data-cy: an interior point of its largest ring) where its database dot sits.
  The generated partial must stay <= BUDGET bytes uncompressed (260 000; the script fails otherwise).

--check works on machines without data/geo: the geometry is then read back from the existing partial (marked by the
  geom:begin / geom:end comments) and only the data-dependent parts are regenerated and compared.
"""
from __future__ import annotations

import argparse
import collections
import html as html_mod
import importlib.util
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEPLOY = ROOT / "deploy_output"
GEO = ROOT / "data" / "geo"
OUT = HERE / "partials" / "story_map.html"
PREVIEW_DIR = Path(os.environ.get("VIZ_SITE_SCRATCH_DIR", str(DEPLOY / "_landing_options")))
DESIGN = HERE / "design"                                # the art director's source of truth (build.py {{css:name}} reads it)
TOKENS = DESIGN / "design_tokens.css"                  # --nx-* tokens and shared classes (.nx-frame .nx-tabs ...): the page must load it
HERO_CSS = DESIGN / "design_hero.css"                  # hero grid + compact map card (design spec 14): the page must load it after the tokens
CURATED = HERE / "data" / "curated_adoption.json"      # 8 publicly sourced staggered adoptions (validate_curated.py checks it)

TOL_STATE = 0.4         # px, Douglas-Peucker tolerance for states / state borders / coastline (the card draws the 975 px frame at ~0.6)
TOL_COUNTY = 2.0        # px, for the county-interior mesh (texture only; the budget is what limits it: see BUDGET)
MIN_RING_AREA = 4.0     # px^2, smaller state rings (specks) are dropped
SMALL_AREA = 30.0       # px^2, states smaller than this get a transparent hit circle (DC ...)
BUDGET = 260_000        # bytes, generated partial
VIEW_W, VIEW_H = 975, 610

FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY",
}
CODES = sorted(FIPS.values())
GEO_BEGIN, GEO_END = "<!-- geom:begin -->", "<!-- geom:end -->"


# --------------------------------------------------------------------------- topology decoding and simplification

def _load_topology(name: str) -> dict:
    return json.loads((GEO / (name + "-albers-10m.json")).read_text(encoding="utf-8"))


def _decode_arcs(topo: dict) -> list:
    """Arcs as lists of (x, y) floats (pixels of the 975 x 610 frame): undo delta encoding, then the transform."""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def _douglas_peucker(pts: list, tol: float) -> list:
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = pts[i]
        bx, by = pts[j]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        dmax, k = -1.0, -1
        for m in range(i + 1, j):
            px, py = pts[m]
            if l2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > dmax:
                dmax, k = d, m
        if dmax > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [p for p, f in zip(pts, keep) if f]


def _reaches_frame(pts: list, depth: float = 4.0) -> bool:
    """True when a ring / arc (points in tenths of px) reaches at least `depth` px inside the 975 x 610 frame. The Aleutian fragments of
    Alaska's inset lie west of x = 0: drawn, they sit outside the card (design review item 17), so they are not emitted at all."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) >= depth * 10 and min(xs) <= (VIEW_W - depth) * 10 and max(ys) >= depth * 10 and min(ys) <= (VIEW_H - depth) * 10)


def _tenths(pts: list) -> list:
    """Round to 0.1 px as integer tenths and drop consecutive duplicates."""
    out = []
    for x, y in pts:
        p = (int(round(x * 10)), int(round(y * 10)))
        if not out or out[-1] != p:
            out.append(p)
    return out


def _simplified(arcs: list, tol: float, wanted=None) -> dict:
    return {i: _tenths(_douglas_peucker(a, tol)) for i, a in enumerate(arcs) if wanted is None or i in wanted}


def _arc_ids(geometry: dict):
    """Every arc index (as stored, sign removed) used by a topojson Polygon / MultiPolygon, per ring."""
    polys = [geometry["arcs"]] if geometry["type"] == "Polygon" else geometry["arcs"]
    for poly in polys:
        for ring in poly:
            yield ring


# --------------------------------------------------------------------------- path data

def _num(v: int) -> str:
    """Integer tenths -> shortest SVG number: 13 -> '1.3', -4 -> '-.4', 20 -> '2'."""
    if v == 0:
        return "0"
    s = "-" if v < 0 else ""
    i, f = divmod(abs(v), 10)
    return s + (str(i) if i else "") + ("." + str(f) if f else "")


def _seq(values: list) -> str:
    """Numbers joined with the fewest separators (a minus sign or a second leading dot separates by itself)."""
    out = []
    for v in values:
        t = _num(v)
        if out and not (t[0] == "-" or (t[0] == "." and "." in out[-1])):
            out.append(" ")
        out.append(t)
    return "".join(out)


def _ring_d(ring: list, start) -> str:
    """Closed sub-path for one ring (points in tenths): absolute M when start is None, else relative m."""
    x0, y0 = ring[0]
    head = ("M" + _seq([x0, y0])) if start is None else ("m" + _seq([x0 - start[0], y0 - start[1]]))
    deltas = []
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        deltas += [bx - ax, by - ay]
    return head + ("l" + _seq(deltas) if deltas else "") + "z"


def _open_d(chains: list) -> str:
    out, cur = [], None
    for ch in chains:
        if len(ch) < 2:
            continue
        x0, y0 = ch[0]
        out.append(("M" + _seq([x0, y0])) if cur is None else ("m" + _seq([x0 - cur[0], y0 - cur[1]])))
        deltas = []
        for (ax, ay), (bx, by) in zip(ch, ch[1:]):
            deltas += [bx - ax, by - ay]
        out.append("l" + _seq(deltas))
        cur = ch[-1]
    return "".join(out)


def _chain(arc_ids: list, arcs: dict) -> list:
    """Join arcs that meet at a node into longer polylines, then order the polylines by nearest neighbour so that the
    relative 'm' jumps stay short. Deterministic (input sorted)."""
    ids = [i for i in sorted(arc_ids) if len(arcs[i]) >= 2]
    inc = {}
    for i in ids:
        inc.setdefault(arcs[i][0], []).append(i)
        inc.setdefault(arcs[i][-1], []).append(i)
    used, lines = set(), []
    for i in ids:
        if i in used:
            continue
        used.add(i)
        line = list(arcs[i])
        while True:
            end = line[-1]
            nxt = next((j for j in inc.get(end, ()) if j not in used), None)
            if nxt is None:
                break
            used.add(nxt)
            a = arcs[nxt]
            line.extend(a[1:] if a[0] == end else a[-2::-1])
        lines.append(line)
    if not lines:
        return []
    lines.sort(key=lambda l: (l[0][1], l[0][0]))
    order, rest = [lines[0]], lines[1:]
    while rest:
        ex, ey = order[-1][-1]
        best, flip, bd = 0, False, None
        for k, l in enumerate(rest):
            d0 = (l[0][0] - ex) ** 2 + (l[0][1] - ey) ** 2
            d1 = (l[-1][0] - ex) ** 2 + (l[-1][1] - ey) ** 2
            d, f = (d0, False) if d0 <= d1 else (d1, True)
            if bd is None or d < bd:
                best, flip, bd = k, f, d
        l = rest.pop(best)
        order.append(l[::-1] if flip else l)
    return order


def _area(ring: list) -> float:
    s = 0
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        s += ax * by - bx * ay
    return s / 200.0                                   # tenths^2 -> px^2, signed


def _inside(pts: list, x: float, y: float) -> bool:
    """Ray casting: is (x, y) (tenths) inside the polygon pts (tenths)?"""
    ins = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / float(yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def _edge_dist(pts: list, x: float, y: float) -> float:
    best = None
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
        d = math.hypot(x - ax - t * dx, y - ay - t * dy)
        best = d if best is None or d < best else best
    return best


def _label_point(ring: list):
    """A point (px) inside the largest ring of a state where a marker can sit: the centroid when it is inside the polygon, else the
    interior grid point farthest from the boundary (a concave state such as MD or MI can have its centroid in the water)."""
    cx, cy = _centroid(ring)
    if _inside(ring, cx * 10, cy * 10) and _edge_dist(ring, cx * 10, cy * 10) >= 15:
        return cx, cy
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    best, bd = (cx, cy), -1.0
    step = max(10, int((max(xs) - min(xs)) / 40))
    for gx in range(min(xs), max(xs) + 1, step):
        for gy in range(min(ys), max(ys) + 1, step):
            if _inside(ring, gx, gy):
                d = _edge_dist(ring, gx, gy)
                if d > bd:
                    best, bd = (gx / 10.0, gy / 10.0), d
    return best


def _centroid(ring: list):
    a2 = cx = cy = 0
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        cr = ax * by - bx * ay
        a2 += cr
        cx += (ax + bx) * cr
        cy += (ay + by) * cr
    if a2 == 0:
        return sum(p[0] for p in ring) / len(ring) / 10.0, sum(p[1] for p in ring) / len(ring) / 10.0
    return cx / (3 * a2) / 10.0, cy / (3 * a2) / 10.0




# --------------------------------------------------------------------------- geometry

def build_geometry():
    """{'states': [{c, d, x, y, h}], 'cm': d, 'bm': d, 'co': d} from data/geo, or None when the files are missing.
    h = the state is too small to hover comfortably (DC): it gets a transparent hit circle at its centroid (x, y)."""
    if not ((GEO / "states-albers-10m.json").is_file() and (GEO / "counties-albers-10m.json").is_file()):
        return None
    st = _load_topology("states")
    sarcs = _simplified(_decode_arcs(st), TOL_STATE)

    def ring_points(ring):
        pts = []
        for a in ring:
            seg = sarcs[a] if a >= 0 else sarcs[~a][::-1]
            pts.extend(seg[1:] if pts else seg)
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        return pts

    states, users = [], {}
    for g in st["objects"]["states"]["geometries"]:
        code = FIPS.get(g["id"])
        if code is None:
            continue
        rings = []
        for ring in _arc_ids(g):
            pts = ring_points(ring)
            if len(pts) < 3 or abs(_area(pts)) < MIN_RING_AREA or not _reaches_frame(pts):
                continue
            rings.append((abs(_area(pts)), pts, ring))
        if not rings:
            raise SystemExit("state %s lost all rings" % code)
        rings.sort(key=lambda r: -r[0])
        # Preserve every state's main ring; remove only secondary coast specks.
        rings = rings[:1] + [r for r in rings[1:] if r[0] >= 12.0 or
                            max(max(p[k] for p in r[1]) - min(p[k] for p in r[1]) for k in (0, 1)) > 60]
        for _, _, arc_ids in rings:
            for a in arc_ids:
                users.setdefault(a if a >= 0 else ~a, set()).add(code)
        total = sum(r[0] for r in rings)
        cx, cy = _label_point(rings[0][1])
        d, start = [], None
        for _, pts, _ in rings:
            d.append(_ring_d(pts, start))
            start = pts[0]
        states.append({"c": code, "d": "".join(d), "x": cx, "y": cy, "h": total < SMALL_AREA})
    states.sort(key=lambda s: s["c"])
    if [s["c"] for s in states] != CODES:
        raise SystemExit("states topology does not contain exactly the 50 states + DC")
    border = [i for i, u in users.items() if len(u) == 2]
    coast = [i for i, u in users.items() if len(u) == 1]

    ct = _load_topology("counties")
    carcs = _decode_arcs(ct)
    cusers = {}
    for g in ct["objects"]["counties"]["geometries"]:
        if g["id"][:2] not in FIPS:
            continue
        for ring in _arc_ids(g):
            for a in ring:
                cusers.setdefault(a if a >= 0 else ~a, set()).add(g["id"])
    interior = {i for i, u in cusers.items() if len(u) == 2 and len({c[:2] for c in u}) == 1}
    csimple = _simplified(carcs, TOL_COUNTY, interior)
    interior = {i for i in interior if len(csimple[i]) >= 2 and _reaches_frame(csimple[i])
                and max(max(p[k] for p in csimple[i]) - min(p[k] for p in csimple[i]) for k in (0, 1)) >= 20}
    return {"states": states, "bm": _open_d(_chain(border, sarcs)), "co": _open_d(_chain(coast, sarcs)),
            "cm": _open_d(_chain(list(interior), csimple))}


def _fmt1(v: float) -> str:
    s = "%.1f" % v
    return s[:-2] if s.endswith(".0") else s


def geometry_from_partial(text: str):
    """Read the geometry back from an existing partial (used when data/geo is not on this machine)."""
    if GEO_BEGIN not in text or GEO_END not in text:
        return None
    block = text[text.index(GEO_BEGIN):text.index(GEO_END)]
    hits = {m.group(1) for m in re.finditer(r'<circle class="sm-hit" data-c="(\w\w)" cx="[^"]+" cy="[^"]+" r="1"/>', block)}
    states = []
    for m in re.finditer(r'<path class="sm-s [^"]*" data-c="(\w\w)" data-cx="([^"]+)" data-cy="([^"]+)"[^>]*? d="([^"]*)"/>', block):
        states.append({"c": m.group(1), "d": m.group(4), "x": float(m.group(2)), "y": float(m.group(3)), "h": m.group(1) in hits})
    geo = {"states": states}
    for key in ("cm", "bm", "co"):
        m = re.search(r'<path id="sm-%s" class="sm-%s" d="([^"]*)"/>' % (key, key), block)
        if not m:
            return None
        geo[key] = m.group(1)
    return geo if [s["c"] for s in states] == CODES else None




# --------------------------------------------------------------------------- data (scripts/viz_site/data/curated_adoption.json)

DEFAULT_POLICY = "nurse_licensure_compact"       # 40 verified states, 2016-2024: seven distinct years in five bins, a clear stagger, 5 database dots

# Legend title = the year basis of the policy (year_basis_en/zh of the data file, shortened to <= 22 characters EN / 10 ZH).
LEGEND_TITLES = {
    "license_recognition": ("Year signed", "签署年份"),
    "nurse_licensure_compact": ("Year signed", "签署年份"),
    "medical_licensure_compact": ("Year signed", "签署年份"),
    "tnc_framework": ("Year signed", "签署年份"),
    "paid_sick_leave": ("Year effective", "生效年份"),
    "paid_family_leave": ("First benefit payment year", "首次支付福利年份"),
    "salary_history_ban": ("Year effective", "生效年份"),
    "min_wage_15": ("Year enacted", "颁布年份"),
}


def _validator():
    spec = importlib.util.spec_from_file_location("validate_curated", HERE / "validate_curated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bin_label(lo: int, hi: int) -> str:
    """'2018' for a one-year bin, '2016–17' for a range inside one century, else '1999–2001'. (The page script has the same function.)"""
    if lo == hi:
        return str(lo)
    return "%d–%02d" % (lo, hi % 100) if lo // 100 == hi // 100 else "%d–%d" % (lo, hi)


def year_bins(years: list, lo: int, hi: int) -> list:
    """BIN RULE. Five contiguous year ranges [(a, b), ...] that cover lo..hi without gaps, light to dark.
      1. Equal width: when the span lo..hi is 5-11 years long, split it into five ranges whose lengths differ by at most one year (the
         longer ones first: 2016-2024 -> 2016-17, 2018-19, 2020-21, 2022-23, 2024), provided every range holds at least one state.
      2. Otherwise (a wide span such as paid family leave 2004-2024, or an equal-width bin that would be empty such as 2020-21 for
         ride-hailing): quantile bins. The distinct adoption years are cut into five consecutive groups whose state counts are as equal as
         possible (least squares around n/5; ties go to the earlier cut); a bin runs from the year after the previous bin's last year to
         its own last year, the first starts at lo and the last ends at hi. So every bin is non-empty and the legend has no gaps."""
    span = hi - lo + 1
    if 5 <= span <= 11:
        base, extra = divmod(span, 5)
        bins, a = [], lo
        for i in range(5):
            w = base + (1 if i < extra else 0)
            bins.append((a, a + w - 1))
            a += w
        if all(any(x <= y <= z for y in years) for x, z in bins):
            return bins
    counts = collections.Counter(years)
    ds = sorted(counts)
    if len(ds) < 5:
        raise SystemExit("a policy needs >= 5 distinct adoption years for five bins, got %d" % len(ds))
    ideal = len(years) / 5.0
    inf = float("inf")
    cost = [[inf] * (len(ds) + 1) for _ in range(6)]
    cut = [[0] * (len(ds) + 1) for _ in range(6)]
    cost[0][0] = 0.0
    for j in range(1, 6):
        for i in range(j, len(ds) + 1):
            for m in range(j - 1, i):
                c = cost[j - 1][m] + (sum(counts[d] for d in ds[m:i]) - ideal) ** 2
                if c < cost[j][i] - 1e-9:
                    cost[j][i], cut[j][i] = c, m
    groups, i = [], len(ds)
    for j in range(5, 0, -1):
        m = cut[j][i]
        groups.append(ds[m:i])
        i = m
    groups.reverse()
    bins, a = [], lo
    for gi, g in enumerate(groups):
        b = hi if gi == 4 else g[-1]
        bins.append((a, b))
        a = b + 1
    return bins


def bin_index(y: int, bins: list) -> int:
    """1..5: the bin that holds year y."""
    for i, (_, b) in enumerate(bins):
        if y <= b:
            return i + 1
    return 5


def cols_for(n: int) -> int:
    """Columns of the selector grid (design spec 14.2): n <= 4 -> n; 5-6 -> 3; 7-12 -> 4; more -> 5."""
    return n if n <= 4 else (3 if n <= 6 else (4 if n <= 12 else 5))


def story_data():
    """The 8 policies of the curated data file, validated first (validate_curated.py), with bins computed per policy."""
    doc = json.loads(CURATED.read_text(encoding="utf-8"))
    validator = _validator()
    errs = validator.validate(doc)
    if errs:
        raise SystemExit("curated_adoption.json is not valid:\n  " + "\n  ".join(errs[:12]))
    policies = []
    for p in doc["policies"]:
        years = {c: int(e["year"]) for c, e in p["states"].items()}
        if len(years) != p["n_states"] or (min(years.values()), max(years.values())) != (p["year_min"], p["year_max"]):
            raise SystemExit("%s: n_states / year_min / year_max do not match the states" % p["id"])
        if p["id"] not in LEGEND_TITLES or len(LEGEND_TITLES[p["id"]][0]) > 28 or len(LEGEND_TITLES[p["id"]][1]) > 10:
            raise SystemExit("%s: legend title missing or longer than 28 (EN) / 10 (ZH) characters" % p["id"])
        # Every state with a recorded year is drawn, single-source rows included (R21: they were drawn as states without the law);
        # a year the curated data itself leaves open would stay undrawn (validate_curated.OPEN_YEAR; empty since 2026-10-14).
        years = {c: y for c, y in years.items() if p["states"][c]["verification"] in ("verified", "fixed", "single_source")
                 and (p["id"], c) not in validator.OPEN_YEAR}
        y0, y1 = min(years.values()), max(years.values())
        policies.append({
            "id": p["id"], "short_en": p["short_en"], "short_zh": p["short_zh"], "name_en": p.get("display_name_en", p["name_en"]), "name_zh": p.get("display_name_zh", p["name_zh"]),
            "n": len(years), "y0": y0, "y1": y1, "years": dict(sorted(years.items())),
            "scopes": {c: {lang: row["display_scope_" + lang].replace("{year}", str(row["year"])) for lang in ("en", "zh")} for c, row in p["states"].items() if c in years and "display_scope_en" in row and "display_scope_zh" in row},
            "evidence": {e["state"]: (e["title"], int(e["year"])) for e in p["db_evidence"] if e["state"] in years},
            "bins": year_bins(list(years.values()), y0, y1)})
    if DEFAULT_POLICY not in [p["id"] for p in policies]:
        raise SystemExit("DEFAULT_POLICY %s is not in the data" % DEFAULT_POLICY)
    return {"policies": policies, "default": DEFAULT_POLICY, "as_of": doc["as_of"]}


def policy_i18n(data: dict) -> dict:
    """{lang: {key: text}} of everything that comes from the data file (names, short names) or from LEGEND_TITLES."""
    out = {"en": {}, "zh": {}}
    for p in data["policies"]:
        out["en"]["map.p.%s.short" % p["id"]] = p["short_en"]
        out["zh"]["map.p.%s.short" % p["id"]] = p["short_zh"].replace("15美元", "15 美元")
        out["en"]["map.p.%s.name" % p["id"]] = p["name_en"]
        out["zh"]["map.p.%s.name" % p["id"]] = p["name_zh"]
        out["en"]["map.lg.%s" % p["id"]] = LEGEND_TITLES[p["id"]][0]
        out["zh"]["map.lg.%s" % p["id"]] = LEGEND_TITLES[p["id"]][1]
        for c, labels in p["scopes"].items():
            for lang in ("en", "zh"):
                out[lang]["map.scope.%s.%s" % (p["id"], c)] = labels[lang]
    return out


def sync_i18n(data: dict, write: bool) -> list:
    """Make i18n/story_map.{en,zh}.json carry exactly the policy keys of the data (map.p.<id>.short / .name, map.lg.<id>, map.scope.<id>.<state>); every other
    key is hand-written and stays. Returns the files that differ (and rewrites them when write is True)."""
    stale = []
    want = policy_i18n(data)
    for lang in ("en", "zh"):
        path = HERE / "i18n" / ("story_map.%s.json" % lang)
        cur = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
        new = collections.OrderedDict((k, v) for k, v in cur.items() if not k.startswith(("map.p.", "map.lg.", "map.scope.")))
        new.update(want[lang])
        text = json.dumps(new, ensure_ascii=False, indent=2) + "\n"
        if text != path.read_text(encoding="utf-8"):
            stale.append(path.name)
            if write:
                path.write_text(text, encoding="utf-8")
    return stale


# --------------------------------------------------------------------------- colours (design_spec.md v3 s2.4, exact hexes)

# Historical no-CSS fallback colours; the partial reads each as var(--nx-<name>, <value>).
# --selfcheck resolves the canonical stylesheet roles, including later text-role aliases.
NX = {"ink": "#111827", "text": "#374151", "text-2": "#4b5563", "muted": "#6b7280", "line": "#e5e7eb", "line-2": "#d1d5db",
      "plate": "#f9fafb", "land": "#eceef2", "dark": "#1f2937", "accent": "#1d4ed8", "accent-soft": "#eff6ff",
      "b1": "#a8c5f9", "b2": "#7ba4f0", "b3": "#5283e0", "b4": "#3263c3", "b5": "#1e4798"}
BIN_TOKENS = ("b1", "b2", "b3", "b4", "b5")        # the five bins, light to dark
RAMP_CELL = 62                                      # px per bin in the legend: the widest tick label (52.4 px at 12 px) keeps >= 9 px to its neighbour,
                                                    # and the widest dotted legend ("Year effective" + ramp + "Bill in our database") still fits one 572 px row
RAMP_W = 5 * RAMP_CELL + 4                          # plus the 1 px gaps
# literals of design_spec 2.4 that have no token (strokes of the map)
COUNTY, STATE_LINE, COAST = "#94a3b8", "#cdd2da", "#aeb6c3"


def _rgb(hexcolor: str) -> tuple:
    return tuple(int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))


def _rgb_css(hexcolor: str) -> str:
    return "rgb(%d, %d, %d)" % _rgb(hexcolor)          # the form getComputedStyle returns


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _oklab(hexcolor: str):
    r, g, b = (_lin(c) for c in _rgb(hexcolor))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def ramp_report() -> str:
    """OKLab distances (x100) of the five bins and of the land colour, printed in the partial header and enforced (spec 2.4)."""
    bins = [_oklab(NX[k]) for k in BIN_TOKENS]
    de = lambda p, q: 100.0 * math.sqrt(sum((p[k] - q[k]) ** 2 for k in range(3)))
    adj = min(de(bins[i], bins[i + 1]) for i in range(4))
    ne = min(de(_oklab(NX["land"]), b) for b in bins)
    if adj < 10.0 or ne < 14.0:
        raise SystemExit("palette too tight: adjacent %.1f, to land %.1f (OKLab x100)" % (adj, ne))
    return "min step between bins %.1f, land to a bin %.1f (OKLab x100)" % (adj, ne)


def _tok(name: str) -> str:
    return "var(--nx-%s,%s)" % (name, NX[name])


# --------------------------------------------------------------------------- partial text

# Final bins/geometry are the static fallback. Chronology is armed only by NXMotion with registered cleanup. The partial uses the shared classes of design_tokens.css (.nx-frame .nx-mapcard .nx-tabs .nx-tab .nx-mapcol .nx-legend .nx-in
# selector) and, at >= 1100, the compact card of design_hero.css (the card sits in .nx-herocard); colours are @name@ = var(--nx-name, same hex).
CSS = """
.sm,.sm *,.sm *::before,.sm *::after{box-sizing:border-box;transition:none;animation:none}
.sm [hidden]{display:none!important}
.sm .nx-tab{scroll-snap-align:start}
html.nx-motion .sm .nx-tab{transition:background-color var(--nx-motion-feedback,120ms) ease-out,border-color var(--nx-motion-feedback,120ms) ease-out,color var(--nx-motion-feedback,120ms) ease-out}
@media(prefers-reduced-motion:no-preference){
html.nx-motion.nx-motion-boot .sm .nx-mapcol:not(.nx-motion-complete) .sm-s,
html.nx-motion .sm .nx-mapcol.nx-motion-pending .sm-s{fill:@land@}
html.nx-motion.nx-motion-boot .sm .nx-mapcol:not(.nx-motion-complete) .sm-dot,
html.nx-motion .sm .nx-mapcol.nx-motion-pending .sm-dot{opacity:0}
html.nx-motion.nx-motion-boot .sm .nx-mapcol:not(.nx-motion-prepared):not(.nx-motion-complete) .sm-pst{visibility:hidden}
}
html.nx-motion .sm .nx-mapcol.nx-motion-armed.sm-playing .sm-s{transition:fill 280ms cubic-bezier(.2,0,0,1)}
html.nx-motion .sm .nx-mapcol.nx-motion-armed .sm-wait{fill:@land@}
html.nx-motion .sm .nx-mapcol.nx-motion-armed .sm-dot.sm-wait{opacity:0}
.sm .nx-mapcol{position:relative}
.sm .sm-control{display:none;position:absolute;right:0;bottom:0;width:44px;height:44px;padding:8px;align-items:center;justify-content:center;border:0;background:transparent;color:@ink@;cursor:pointer}
.sm .sm-control svg{width:28px;height:28px;border:1px solid @line-2@;border-radius:50%;padding:5px;fill:none;stroke:currentColor;stroke-width:1.5}
html.nx-motion .sm.sm-controls .sm-control{display:flex}
html.nx-motion .sm .sm-control:hover{color:@accent@}
.sm .sm-control:focus-visible{outline:2px solid @accent@;outline-offset:-2px;border-radius:8px}
.sm .sm-ro{padding-right:44px;min-height:44px;padding-top:10px}
@media(min-width:601px){.sm .nx-legend>span#sm-lgdot{margin-left:auto}}
@media(min-width:601px) and (max-width:1099px){.sm .nx-map .sm-svg{margin-left:0}}
@media print{.sm .sm-control{display:none!important}.sm .sm-dot.sm-wait{opacity:1!important}.sm .sm-s.s-n{fill:@land@!important}.sm .sm-s.s-1{fill:@b1@!important}.sm .sm-s.s-2{fill:@b2@!important}.sm .sm-s.s-3{fill:@b3@!important}.sm .sm-s.s-4{fill:@b4@!important}.sm .sm-s.s-5{fill:@b5@!important}.sm .sm-s{transition:none!important}}
@media(prefers-reduced-motion:reduce){.sm .sm-control{display:none!important}.sm *{transition:none!important}}
.sm .nx-tab:focus-visible{outline:2px solid @accent@;outline-offset:2px}
.sm .nx-tabs .nx-tab[aria-checked=true],.sm .nx-tabs .nx-tab[aria-checked=true]:hover{background:@dark@;border-color:@dark@;color:#fff}
.sm .nx-mapcard{overflow:hidden}
.sm .nx-map svg{display:block;width:100%;height:auto;max-height:440px;margin-inline:auto;overflow:hidden;touch-action:pan-y}
.sm .sm-head{margin:0 0 8px}
.sm .sm-pn{display:block;font-size:15px;font-weight:600;line-height:24px;color:@ink@}
.sm .sm-pst{display:block;font-size:13px;line-height:20px;color:@muted@;font-variant-numeric:tabular-nums}
.sm .sm-ro{height:44px;margin-top:0;font-size:13px;line-height:24px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:@muted@}
.sm .sm-ro span+span:not(:empty)::before{content:" · "}
.sm .sm-ro span+span{color:@text-2@}
.sm .nx-legend{display:flex;flex-wrap:wrap;align-items:flex-start;gap:8px 24px;margin-top:8px;font-size:13px;line-height:17px;color:@text-2@}
.sm .nx-legend>span{display:inline-flex;align-items:flex-start;gap:8px;max-width:100%}
.sm .nx-legend>span:first-child{flex-direction:column;gap:4px}
.sm .nx-legend b{font-weight:600;color:@ink@;white-space:nowrap}
.sm .sm-rw{display:block;padding-top:3px}
.sm .sm-rw{max-width:100%}
.sm .nx-ramp{max-width:100%;display:grid;grid-template-columns:repeat(5,1fr);gap:1px;width:%RAMP%px;height:10px;border-radius:3px;overflow:hidden}
.sm .nx-ramp i{display:block}
.sm .r1{background:@b1@}.sm .r2{background:@b2@}.sm .r3{background:@b3@}.sm .r4{background:@b4@}.sm .r5{background:@b5@}
.sm .sm-rk{max-width:100%;display:grid;grid-template-columns:repeat(5,1fr);width:%RAMP%px;margin-top:1px;font-size:var(--nx-type-tick);line-height:14px;color:@muted@;font-variant-numeric:tabular-nums;text-align:center;white-space:nowrap}
.sm .sm-rk i{font-style:normal}
.sm .sm-lgsvg{display:block;flex:none;margin-top:2.5px}
.sm .sm-lgsvg circle,.sm .sm-dot{fill:#fff;stroke:@ink@;stroke-width:1.25px;vector-effect:non-scaling-stroke}
.sm-s{fill:@land@;stroke:none}
.sm-s:focus,.sm-svg:focus{outline:none}
.s-1{fill:@b1@}.s-2{fill:@b2@}.s-3{fill:@b3@}.s-4{fill:@b4@}.s-5{fill:@b5@}
.sm-cm,.sm-bm,.sm-co,.sm-hl{fill:none;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke;pointer-events:none}
.sm-cm{stroke:#94a3b8;stroke-opacity:.25;stroke-width:.5px}
.sm-bm{stroke:#cdd2da;stroke-width:.75px}
.sm-co{stroke:#aeb6c3;stroke-width:.9px}
.sm-hl{stroke:@ink@;stroke-width:1.25px}
.sm-hl.f{stroke:@accent@;stroke-width:2px}
.sm-hit{fill:transparent;stroke:transparent;stroke-width:16px;vector-effect:non-scaling-stroke;pointer-events:all}
.sm .sm-dot{display:none;pointer-events:none}.sm .sm-dot.on{display:inline}
.sm .sm-cap{max-width:var(--nx-col-text,680px);margin:12px 0 0;font-size:13px;line-height:1.5;color:@muted@}
@media(max-width:1099px){.sm .nx-tabs{padding-block:3px;margin-top:-3px;margin-bottom:9px}
.sm .nx-tab:focus-visible{outline-offset:1px}
.sm .nx-tabs.fr{-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - var(--nx-pad,16px)),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - var(--nx-pad,16px)),transparent)}
.sm .nx-tabs.fl{-webkit-mask-image:linear-gradient(90deg,transparent,#000 var(--nx-pad,16px));mask-image:linear-gradient(90deg,transparent,#000 var(--nx-pad,16px))}
.sm .nx-tabs.fl.fr{-webkit-mask-image:linear-gradient(90deg,transparent,#000 var(--nx-pad,16px),#000 calc(100% - var(--nx-pad,16px)),transparent);mask-image:linear-gradient(90deg,transparent,#000 var(--nx-pad,16px),#000 calc(100% - var(--nx-pad,16px)),transparent)}}
@media(min-width:1100px){.sm .sm-head{display:flex;justify-content:space-between;align-items:baseline;gap:4px 16px;flex-wrap:wrap;grid-column:1/-1;grid-row:1}
.sm .sm-pn,.sm .sm-pst{display:inline}.sm .sm-pst{line-height:24px}
.sm .sm-ro{grid-column:1/-1;grid-row:3}
.nx-herocard .sm .nx-mapcard .nx-legend{margin-top:8px}
.nx-herocard .sm .nx-mapcard .nx-tabs{margin-top:8px}}
@media(max-width:600px){.sm .nx-legend{min-height:74px}.sm .nx-legend>span:first-child{flex-direction:column;gap:4px}}
@media(forced-colors:active){.sm .nx-tab[aria-checked=true]{outline:2px solid CanvasText}}
"""

# Selector (radiogroup), one-line readout (ellipsis when long, the full text in title), repaint, dots. The map
# needs no script for the default policy.
JS = r"""
(function(){
var R=document.getElementById('sm-root');if(!R)return;
var L={},P={},D={},cur='',svg=R.querySelector('.sm-svg'),hl=R.querySelector('#sm-hl'),ro=R.querySelector('#sm-ro'),r1=R.querySelector('#sm-ro1'),r2=R.querySelector('#sm-ro2'),
 list=R.querySelector('#sm-tabs'),tabs=[].slice.call(R.querySelectorAll('.nx-tab')),pn=R.querySelector('#sm-pn'),pst=R.querySelector('#sm-pst'),scope=R.querySelector('#sm-scope'),
 motion=window.NXMotion,area=R.querySelector('#sm-map'),control=R.querySelector('#sm-control'),icon=R.querySelector('#sm-control-icon'),handle=null,clock=null,revealed=Infinity,userPaused=false,
 lgd=R.querySelector('#sm-lgdot'),lgt=R.querySelector('#sm-lgt'),dots=[].slice.call(R.querySelectorAll('.sm-dot')),ends=[].slice.call(R.querySelectorAll('.sm-rk i'));
[].forEach.call(R.querySelectorAll('#sm-t i'),function(e){L[e.dataset.k]=e.textContent});
[].forEach.call(R.querySelectorAll('#sm-f path'),function(p){P[p.dataset.c]=p});
try{D=JSON.parse(R.querySelector('#sm-d').textContent)}catch(_){}
function F(s,o){return s.replace(/\{(\w+)\}/g,function(m,k){return o.hasOwnProperty(k)?o[k]:m})}
function statline(st){return F(st.n===1?L.stats_one:L.stats,st)}
function mk(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function lab(a,b){return a===b?''+a:(Math.floor(a/100)===Math.floor(b/100)?a+'–'+('0'+b%100).slice(-2):a+'–'+b)}
function binOf(p,y){for(var i=0;i<5;i++)if(y<=p.b[i][1])return i+1;return 5}
function fit(){var full=[r1.textContent,r2.textContent,scope.textContent].filter(Boolean).join(' \u00b7 ');if(ro.scrollWidth>ro.clientWidth+1)ro.title=full;else ro.removeAttribute('title')}
function say(c){var p=D.p[cur],y=p.y[D.c.indexOf(c)],ev=p.e&&p.e[c];
 r1.textContent=y>0?F(L.ro_year,{state:L['s.'+c],y:y}):L['s.'+c];r2.textContent=ev?F(L.ro_db,{title:ev[0],y:ev[1]}):'';scope.textContent=L['scope.'+cur+'.'+c]||'';fit()}
function show(c,f){var y=D.p[cur].y[D.c.indexOf(c)];if(clock&&y>revealed){if(f)finish('focus-readout');else return};say(c);hl.setAttribute('d',P[c].getAttribute('d'));hl.setAttribute('class',f?'sm-hl f':'sm-hl')}
function reset(){r1.textContent=L.ro_default;r2.textContent='';scope.textContent='';ro.removeAttribute('title');hl.removeAttribute('d')}
function dr(){var m=svg.getScreenCTM();if(!m||!m.a)return;var r=(3/m.a).toFixed(2);dots.forEach(function(d){d.setAttribute('r',r)})}
function pick(e){if(e.type==='pointerdown')finish('pointer');var el=e.target.closest?e.target.closest('[data-c]'):null;if(el&&svg.contains(el)&&P[el.dataset.c])show(el.dataset.c,false);else reset()}
svg.addEventListener('pointermove',pick);svg.addEventListener('pointerdown',pick);
svg.addEventListener('pointerleave',function(e){if(e.pointerType==='mouse')reset()});
document.addEventListener('pointerdown',function(e){if(!svg.contains(e.target))reset()});
svg.addEventListener('focusin',function(e){var c=e.target.dataset&&e.target.dataset.c,kb=true;if(!c)return;
 try{kb=e.target.matches(':focus-visible')}catch(_){}
 if(kb)show(c,true)});
svg.addEventListener('focusout',reset);
document.addEventListener('keydown',function(e){if(e.key==='Escape')reset()});
if(!D.p||!D.c||!list||!tabs.length){if(list)list.hidden=true;return}
function stats(p){var n=0,lo=1e4,hi=0;p.y.forEach(function(y){if(y>0){n++;if(y<lo)lo=y;if(y>hi)hi=y}});return{n:n,y0:lo,y1:hi}}
function fade(){var m=list.scrollWidth-list.clientWidth;list.classList.toggle('fl',list.scrollLeft>1);list.classList.toggle('fr',list.scrollLeft<m-1)}
function vis(b,force){var a=list.getBoundingClientRect(),r=b.getBoundingClientRect(),pd=parseFloat(getComputedStyle(list).paddingLeft)||0;
 if(list.scrollWidth>list.clientWidth+1&&(force||r.left<a.left+pd||r.right>a.right-pd))list.scrollLeft+=r.left-a.left-pd;
 fade()}
list.addEventListener('scroll',fade,{passive:true});
function stop(i){tabs.forEach(function(t,j){t.tabIndex=j===i?0:-1})}
function sel(b){finish('family');var id=b.dataset.f,p=D.p[id],st;if(!p)return;cur=id;reset();st=stats(p);
 for(var i=0;i<D.c.length;i++){var c=D.c[i],q=P[c],y=p.y[i];if(!q)continue;
  if(y>0){q.setAttribute('class','sm-s s-'+binOf(p,y));q.setAttribute('data-y',y);q.setAttribute('tabindex','0');q.setAttribute('role','img');
   q.setAttribute('aria-label',L['s.'+c]+L.al_sep+F(L.al_year,{y:y}))}
  else{q.setAttribute('class','sm-s s-n');['data-y','tabindex','role','aria-label'].forEach(function(a){q.removeAttribute(a)})}}
 pst.textContent=statline(st);pn.textContent=L['p.'+id+'.name'];lgt.textContent=L['lg.'+id];
 svg.setAttribute('aria-label',L.aria_a+pn.textContent+L.aria_b);
 ends.forEach(function(e,k){e.textContent=lab(p.b[k][0],p.b[k][1])});
 dots.forEach(function(d){d.classList.toggle('on',d.dataset.f===id)});lgd.hidden=!Object.keys(p.e||{}).length;
 tabs.forEach(function(t){t.setAttribute('aria-checked',t===b?'true':'false')});stop(tabs.indexOf(b));
 vis(b,true);if(window.CustomEvent)document.dispatchEvent(new window.CustomEvent("nx:map-policy",{detail:{policy:id}}))}
tabs.forEach(function(t){var p=D.p[t.dataset.f],s=stats(p);t.setAttribute('aria-label',F(L.row_aria,{name:L['p.'+t.dataset.f+'.short'],n:s.n,y0:s.y0,y1:s.y1}))});
list.addEventListener('click',function(e){var b=e.target.closest?e.target.closest('.nx-tab'):null;if(b)sel(b)});
list.addEventListener('keydown',function(e){var i=tabs.indexOf(e.target),k=e.key,n=tabs.length,cs=getComputedStyle(list),cols=cs.display==='grid'?cs.gridTemplateColumns.split(' ').length:1,j;
 if(i<0||e.altKey||e.ctrlKey||e.metaKey)return;
 if(k==='ArrowRight')j=(i+1)%n;else if(k==='ArrowLeft')j=(i+n-1)%n;
 else if(k==='ArrowDown')j=cols>1?(i+cols<n?i+cols:i%cols):(i+1)%n;
 else if(k==='ArrowUp')j=cols>1?(i-cols>=0?i-cols:i+cols*Math.floor((n-1-i)/cols)):(i+n-1)%n;
 else if(k==='Home')j=0;else if(k==='End')j=n-1;else return;
 e.preventDefault();sel(tabs[j]);tabs[j].focus()});
function action(state){
 control.setAttribute('aria-label',L['control.'+state]);control.dataset.action=state;
 icon.setAttribute('d',state==='pause'?'M7 5v14M17 5v14':state==='resume'?'M8 5l10 7-10 7z':'M19 9a8 8 0 1 0 1 7M19 3v6h-6');
}
function finalMap(){
 clock=null;revealed=Infinity;userPaused=false;area.classList.remove('sm-playing');
 Object.keys(P).forEach(function(c){P[c].classList.remove('sm-wait')});
 dots.forEach(function(d){d.classList.remove('sm-wait')});
 if(D.p[cur])pst.textContent=statline(stats(D.p[cur]));action('replay');
}
function finish(reason){if(handle&&handle.active())handle.finish(reason);else finalMap()}
function prepareMap(){
 var st=stats(D.p[cur]);
 Object.keys(P).forEach(function(c){P[c].classList.add('sm-wait')});
 dots.forEach(function(d){d.classList.add('sm-wait')});
 pst.textContent=statline({n:0,y0:st.y0,y1:st.y0}).replace(st.y0+'–'+st.y0,''+st.y0);
}
function run(ctx,replay){
 clock=ctx;userPaused=false;revealed=0;action('pause');
 var id=cur,p=D.p[id],st=stats(p),span=st.y1-st.y0+1,step=Math.max(190,Math.min(380,4000/span)),hold=replay?400:1100;
 Object.keys(P).forEach(function(c){P[c].classList.add('sm-wait')});dots.forEach(function(d){d.classList.add('sm-wait')});
 function frame(year){
  if(!ctx.active()||cur!==id)return;
  revealed=year;var count=0;
  D.c.forEach(function(c,i){var y=p.y[i],on=y>0&&y<=year;if(on)count++;if(P[c])P[c].classList.toggle('sm-wait',!on)});
  dots.forEach(function(d){var y=p.y[D.c.indexOf(d.dataset.c)];d.classList.toggle('sm-wait',!(d.dataset.f===id&&y>0&&y<=year))});
  var text=statline({n:count,y0:st.y0,y1:Math.max(st.y0,year)});
  pst.textContent=year<=st.y0?text.replace(st.y0+'–'+st.y0,''+st.y0):text;
 }
 frame(st.y0-1);
 ctx.timer(function(){area.classList.add('sm-playing')},hold);
 for(var year=st.y0;year<=st.y1;year++)(function(y){ctx.timer(function(){frame(y)},hold+(y-st.y0)*step)})(year);
 ctx.timer(function(){ctx.finish('complete')},hold+span*step+700);
 if(document.hidden){ctx.pause();action('resume')}
}
if(control&&motion){
 control.addEventListener('click',function(){
  if(!motion.allowed())return;
  if(clock){if(clock.paused()){userPaused=false;clock.resume();action('pause')}else{userPaused=true;clock.pause();action('resume')}}
  else handle=motion.transition(area,function(ctx){run(ctx,true)},{finish:finalMap,maxDuration:12000});
 });
 document.addEventListener('visibilitychange',function(){if(!clock)return;if(document.hidden){clock.pause();action('resume')}else if(!userPaused){clock.resume();action('pause')}});
}
cur=(tabs.filter(function(t){return t.getAttribute('aria-checked')==='true'})[0]||tabs[0]).dataset.f;
dr();vis(tabs.filter(function(t){return t.dataset.f===cur})[0],false);
window.addEventListener('resize',function(){dr();fade()});
if(motion&&control){R.classList.add('sm-controls');action('replay');handle=motion.once(area,run,{prepare:prepareMap,finish:finalMap,threshold:.60,maxDuration:12000})}
})();
"""


def _esc(s: str) -> str:
    return html_mod.escape(s, quote=True)


def render_partial(geo=None) -> str:
    data = story_data()
    pols = data["policies"]
    dp = next(p for p in pols if p["id"] == data["default"])
    if geo is None:
        geo = build_geometry()
    if geo is None:
        old = OUT.read_text(encoding="utf-8-sig").replace("\r\n", "\n") if OUT.is_file() else ""
        geo = geometry_from_partial(old)
    if geo is None:
        raise SystemExit("no geometry: data/geo/{states,counties}-albers-10m.json are missing and %s has no geometry to reuse"
                         % OUT.name)
    pos = {s["c"]: (s["x"], s["y"]) for s in geo["states"]}
    report = ramp_report()
    css = re.sub(r"@([a-z0-9-]+)@", lambda m: _tok(m.group(1)), CSS).replace("%RAMP%", str(RAMP_W))

    paths = []                                                  # the DEFAULT policy, drawn statically (no-JS fallback)
    for s in geo["states"]:
        y = dp["years"].get(s["c"], 0)
        attrs = 'class="sm-s s-%s" data-c="%s" data-cx="%s" data-cy="%s"' % (bin_index(y, dp["bins"]) if y else "n", s["c"], _fmt1(s["x"]), _fmt1(s["y"]))
        if y:                                                   # a state with a law: focusable, labelled, has a year
            label = "{{t:map.state.%s}}{{t:map.al_sep}}{{tf:map.al_year|y=raw(%d)}}" % (s["c"], y)
            attrs += ' data-y="%d" tabindex="0" role="img" aria-label="%s"' % (y, label)
        paths.append("<path %s d=\"%s\"/>" % (attrs, s["d"]))
    hits = "".join('<circle class="sm-hit" data-c="%s" cx="%s" cy="%s" r="1"/>' % (s["c"], _fmt1(s["x"]), _fmt1(s["y"]))
                   for s in geo["states"] if s["h"])
    dot_html = "".join('<circle class="sm-dot%s" data-f="%s" data-c="%s" cx="%s" cy="%s" r="5"/>'
                       % (" on" if p["id"] == dp["id"] else "", p["id"], c, _fmt1(pos[c][0]), _fmt1(pos[c][1]))
                       for p in pols for c in sorted(p["evidence"]))
    names = "".join('<i data-k="s.%s">{{t:map.state.%s}}</i>' % (c, c) for c in CODES)
    names += "".join('<i data-k="p.%s.name">{{t:map.p.%s.name}}</i><i data-k="p.%s.short">{{t:map.p.%s.short}}</i>'
                     % ((p["id"],) * 4) + '<i data-k="lg.%s">{{t:map.lg.%s}}</i>' % ((p["id"],) * 2) for p in pols)
    names += "".join('<i data-k="scope.%s.%s">{{t:map.scope.%s.%s}}</i>' % (p["id"], c, p["id"], c) for p in pols for c in p["scopes"])
    strings = "".join('<i data-k="%s">{{t:map.%s}}</i>' % (k, k) for k in (
        "al_year", "al_sep", "stats", "stats_one", "aria_a", "aria_b", "row_aria", "ro_default", "ro_year", "ro_db", "control.pause", "control.resume", "control.replay"))

    # per policy: years (51), bins, database evidence per state
    blob_p = {}
    for p in pols:
        blob_p[p["id"]] = {"y": [p["years"].get(c, 0) for c in CODES], "b": [list(b) for b in p["bins"]],
                           "e": {c: [t, y] for c, (t, y) in sorted(p["evidence"].items())}}
    blob = json.dumps({"c": CODES, "p": blob_p}, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

    tabs = "".join(
        '<button type="button" role="radio" class="nx-tab" id="sm-tab-%s" data-f="%s" aria-checked="%s" tabindex="%d">{{t:map.p.%s.short}}</button>'
        % (p["id"], p["id"], "true" if p is dp else "false", 0 if p is dp else -1, p["id"]) for p in pols)
    ends = [bin_label(*dp["bins"][0]), bin_label(*dp["bins"][1]), bin_label(*dp["bins"][2]), bin_label(*dp["bins"][3]), bin_label(*dp["bins"][4])]

    lines = [
        "<!-- GENERATED by scripts/viz_site/make_story_map.py from scripts/viz_site/data/curated_adoption.json (8 publicly sourced adoptions,",
        "     as of %s; validate_curated.py runs first) and data/geo/*-albers-10m.json (us-atlas@3). Do not edit by hand: re-run the script" % data["as_of"],
        "     (--check fails when stale). Needs design/design_tokens.css and design/design_hero.css on the page (the card sits in .nx-herocard).",
        "     FINAL static map: family selection repaints instantly; NXMotion owns the finite initial chronology, no tooltip (a readout under the map names the year and, when",
        "     the database holds the enacting bill, that bill). Default policy (drawn without JS) = %s." % data["default"],
        "     Per policy: states / year range / bins (equal-width in years when the span is 5-11 years and no bin is empty, else quantile bins):",
    ]
    for p in pols:
        lines.append("       %-26s %2d states, %d-%d, bins %s, %d database dots" % (
            p["id"], p["n"], p["y0"], p["y1"], " ".join(bin_label(*b) for b in p["bins"]), len(p["evidence"])))
    lines += [
        "     Palette: %s -->" % report,
        "<style>", css.strip(), "</style>",
        '<div class="sm" id="sm-root">',
        "<noscript><style>.sm .nx-tabs,.nx-herocard .sm .nx-tabs{display:none!important}</style></noscript>",
        '<div class="nx-frame nx-mapcard">',
        '<div class="nx-tabs" id="sm-tabs" role="radiogroup" aria-label="{{t:map.tabs_aria}}" style="--nx-cols:%d">%s</div>' % (cols_for(len(pols)), tabs),
        '<div class="nx-mapcol nx-map" id="sm-map">',
        '<p class="sm-head nx-in"><b class="sm-pn" id="sm-pn">{{t:map.p.%s.name}}</b><span class="sm-pst" id="sm-pst">'
        '{{tf:map.stats|n=raw(%d)|y0=raw(%d)|y1=raw(%d)}}</span></p>' % (dp["id"], dp["n"], dp["y0"], dp["y1"]),
        '<button type="button" class="sm-control" id="sm-control" aria-label="{{t:map.control.replay}}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path id="sm-control-icon" d="M19 9a8 8 0 1 0 1 7M19 3v6h-6"/></svg></button>',
        '<svg class="sm-svg" preserveAspectRatio="xMinYMid" viewBox="0 0 %d %d" tabindex="-1" role="group" aria-label="{{t:map.aria_a}}{{t:map.p.%s.name}}{{t:map.aria_b}}">' % (VIEW_W, VIEW_H, dp["id"]),
        '<defs><clipPath id="sm-clean"><rect x="4" y="0" width="971" height="610"/></clipPath></defs>',
        '<g clip-path="url(#sm-clean)">',
        GEO_BEGIN,
        '<g id="sm-f">', "\n".join(paths), "</g>",
        '<path id="sm-cm" class="sm-cm" d="%s"/>' % geo["cm"],
        '<path id="sm-bm" class="sm-bm" d="%s"/>' % geo["bm"],
        '<path id="sm-co" class="sm-co" d="%s"/>' % geo["co"],
        hits,
        GEO_END,
        "</g>",
        '<g id="sm-dots">%s</g>' % dot_html,
        '<path id="sm-hl" class="sm-hl"/>',
        "</svg>",
        '<div class="sm-ro nx-in" id="sm-ro" aria-live="polite"><span id="sm-ro1">{{t:map.ro_default}}</span><span id="sm-ro2"></span><span id="sm-scope"></span></div>',
        "</div>",
        '<div class="nx-legend nx-in">',
        '<span><b id="sm-lgt">{{t:map.lg.%s}}</b><span class="sm-rw"><span class="nx-ramp" aria-hidden="true">%s</span>'
        '<span class="sm-rk" aria-hidden="true">%s</span></span></span>'
        % (dp["id"], "".join('<i class="r%d"></i>' % i for i in range(1, 6)), "".join("<i>%s</i>" % e for e in ends)),
        '<span id="sm-lgdot"%s><svg class="sm-lgsvg" width="12" height="12" viewBox="0 0 12 12" focusable="false" aria-hidden="true">'
        '<circle cx="6" cy="6" r="3.5"/></svg>{{t:map.lg_db}}</span>' % ("" if dp["evidence"] else " hidden"),
        "</div>",
        "</div>",
        '<p class="sm-cap" id="sm-cap">{{t:map.provenance}}</p>',
        '<div id="sm-t" hidden>%s%s</div>' % (names, strings),
        '<script type="application/json" id="sm-d">%s</script>' % blob,
        "</div>",
        "<script>", JS.strip(), "</script>",
    ]
    text = "\n".join(lines) + "\n"
    if len(text.encode("utf-8")) > BUDGET:
        raise SystemExit("story_map.html is %d bytes, over the %d budget: raise TOL_COUNTY / TOL_STATE / MIN_RING_AREA"
                         % (len(text.encode("utf-8")), BUDGET))
    return text


def expectation(data: dict, strings: dict) -> dict:
    """What the page must show for EVERY policy, computed straight from the data file and the dictionary (independent of the HTML and of
    the page script): per state its bin class, the colour that bin promises (as getComputedStyle prints it), whether it has a year, the
    year, its exact aria-label, both readout lines and its source links; per policy the class counts, the exact stats line, header,
    map label, selector label, legend labels and database dots."""
    fill = {"n": _rgb_css(NX["land"])}
    fill.update({str(i + 1): _rgb_css(NX[k]) for i, k in enumerate(BIN_TOKENS)})
    fmt = lambda s, **kw: re.sub(r"\{(\w+)\}", lambda m: str(kw.get(m.group(1), m.group(0))), s)
    pols = {}
    for p in data["policies"]:
        states, counts = {}, {}
        for c in CODES:
            y = p["years"].get(c, 0)
            b = str(bin_index(y, p["bins"])) if y else "n"
            name = strings["map.state." + c]
            ev = p["evidence"].get(c)
            states[c] = [b, fill[b], 1 if y else 0, y, (name + strings["map.al_sep"] + fmt(strings["map.al_year"], y=y)) if y else "",
                         fmt(strings["map.ro_year"], state=name, y=y) if y else name,
                         fmt(strings["map.ro_db"], title=ev[0], y=ev[1]) if ev else "",
                         p["scopes"].get(c, {}).get("en" if strings["map.al_sep"] == ": " else "zh", "")]
            counts[b] = counts.get(b, 0) + 1
        name, short = strings["map.p.%s.name" % p["id"]], strings["map.p.%s.short" % p["id"]]
        pols[p["id"]] = {
            "name": name, "short": short, "states": states, "counts": counts, "n": p["n"],
            "stats": fmt(strings["map.stats"], n=p["n"], y0=p["y0"], y1=p["y1"]),
            "aria": strings["map.aria_a"] + name + strings["map.aria_b"],
            "row_aria": fmt(strings["map.row_aria"], name=short, n=p["n"], y0=p["y0"], y1=p["y1"]),
            "ends": [bin_label(*b) for b in p["bins"]], "dots": sorted(p["evidence"]), "lgt": strings["map.lg.%s" % p["id"]],
            "order": [c for _, c in sorted((y, c) for c, y in p["years"].items())]}
    return {"order": [p["id"] for p in data["policies"]], "default": data["default"], "p": pols,
            "bins": [_rgb_css(NX[k]) for k in BIN_TOKENS], "land": _rgb_css(NX["land"]), "ro_default": strings["map.ro_default"],
            "lg_db": strings["map.lg_db"]}


# --------------------------------------------------------------------------- preview pages

PREVIEW = """<!DOCTYPE html>
<html lang="%(html_lang)s">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="robots" content="noindex">
<link rel="icon" href="data:,">
<title>Story map preview</title>
%(head)s
<style id="nx-tokens">%(tokens)s</style>
<style id="nx-hero">%(hero)s</style>
</head>
<body>
%(motion)s
<main class="page"><div class="story" id="story">
<div class="nx-herogrid">
<div class="nx-herotext"><h2 class="nx-h1">Preview of the hero block</h2><p class="nx-lead">The map card sits in the right column (3fr) of the hero grid; this text is only a stand-in for the assembler's copy.</p></div>
<section class="nx-herocard" aria-labelledby="hs-map"><h2 class="sr-only" id="hs-map">Map</h2>
%(body)s
</section>
</div>
</div></main>
<script>window.__SM_EXPECT=%(expect)s;</script>
<script>
%(pick)s
</script>
<script>
%(check)s
</script>
</body>
</html>
"""

# Preview-only (never part of the partial): ?policy=<id> selects that policy (a click on its button), &focus=tab focuses the selected
# button (for screenshots), &hover=<state code> shows that state in the readout.
PICK_JS = r"""
(function(){
var m=/[?&]policy=([a-z_0-9]+)/.exec(location.search),b=m&&document.getElementById('sm-tab-'+m[1]);
if(b)b.click();
if(/[?&]focus=tab/.test(location.search)){var s=document.querySelector('.nx-tab[aria-checked=true]');if(s)s.focus()}
var h=/[?&]hover=([A-Z]{2})/.exec(location.search),p=h&&document.querySelector('#sm-f path[data-c='+h[1]+']');
if(p)p.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerType:'mouse'}));
})();
"""

# Preview-only. Active with ?selfcheck. For EVERY policy (the static default first, then each button clicked, then all again in reverse
# order, then the keyboard) it compares every state's bin class, COMPUTED fill, focusability, data-y, exact aria-label and both exact
# readout lines (a simulated hover) with the expectation computed from the data file; checks that the class counts add up to the
# policy's n_states, the stats line, header, map label, the legend bin labels, the database dots (a subset of the coloured
# states, the legend entry only when there is a dot), and the sources list (every coloured state has a row with its links, in a new tab,
# rel=noopener). Once for the page: 8 selector buttons, the layout numbers of the spec (hero context at >= 1100: card <= 700, header
# on one line, map aspect 1.6, cells >= 48; below: one scrolling pill row of >= 44 px, full-bleed card at <= 600), the readout area does
# not change size on hover, no text below 12 px, no shadow, all motion is gated, final parity holds for every family. &mutate corrupts one expected
# value on purpose: the check MUST then fail (it proves the check can fail). &frame=<w> hosts the same check in an iframe <w> px wide
# (headless Chrome never makes a window narrower than 500 px; needs --allow-file-access-from-files). Result goes to data-selfcheck.
SELFCHECK_JS = r"""
(function(){
var fm=/[?&]frame=(\d+)/.exec(location.search);
if(fm){var fr=document.createElement('iframe'),mu=/[?&]mutate/.test(location.search)?'&mutate':'';
 fr.style.cssText='position:absolute;left:0;top:0;border:0;width:'+fm[1]+'px;height:2400px';
 fr.addEventListener('load',function(){var v=fr.contentDocument.documentElement.getAttribute('data-selfcheck');if(v)document.documentElement.setAttribute('data-selfcheck',v)});
 fr.src=location.href.split('?')[0]+'?selfcheck'+mu;document.body.appendChild(fr);return}
if(!/[?&]selfcheck/.test(location.search))return;
var R=document.getElementById('sm-root'),EX=window.__SM_EXPECT,bad=[],res={},nstate=0,geo={};
function err(m){bad.push(m)}
function q(s){return R.querySelector(s)}
function qa(s){return [].slice.call(R.querySelectorAll(s))}
var tabs=qa('[role=radio]'),list=q('[role=radiogroup]'),pn=q('#sm-pn'),pst=q('#sm-pst'),cap=q('#sm-cap'),svg=q('.sm-svg'),ro=q('#sm-ro'),r1=q('#sm-ro1'),r2=q('#sm-ro2'),
 card=q('.nx-mapcard'),lgd=q('#sm-lgdot'),lgt=q('#sm-lgt'),VW=innerWidth,D=VW>=1100;
if(window.NXMotion)NXMotion.finishAll('selfcheck-final');
if(/[?&]mutate/.test(location.search)){var mp=EX.p[EX.order[3]],mc=Object.keys(mp.states).filter(function(c){return mp.states[c][2]})[0];mp.states[mc][3]+=1}
function same(a,b){return a.length===b.length&&a.every(function(x,i){return x===b[i]})}
function near(a,b,t){return Math.abs(a-b)<=t}
var cardH0=null;
function check(id,how){
 var E=EX.p[id],tag=how+' '+id+': ',counts={},nlaw=0,h0=card.getBoundingClientRect().height;
 if(cardH0===null)cardH0=h0;else if(!near(h0,cardH0,0.6))err(tag+'changing policy changed card height '+cardH0+' -> '+h0);
 qa('#sm-f path').forEach(function(p){
  var c=p.dataset.c,e=E.states[c],m=(p.getAttribute('class').match(/(?:^| )s-(\w+)/)||[])[1],f=getComputedStyle(p).fill;nstate++;
  if(!e){err(tag+c+' not in the data');return}
  if(m!==e[0])err(tag+c+' class s-'+m+' != bin '+e[0]);
  if(f!==e[1])err(tag+c+' fill '+f+' != '+e[1]);
  counts[m]=(counts[m]||0)+1;
  if(e[2]){nlaw++;
   if(p.getAttribute('tabindex')!=='0')err(tag+c+' is not focusable');
   if(p.getAttribute('aria-label')!==e[4])err(tag+c+' aria-label '+p.getAttribute('aria-label')+' != '+e[4]);
   if(+p.dataset.y!==e[3])err(tag+c+' data-y '+p.dataset.y+' != '+e[3])}
  else if(p.hasAttribute('tabindex')||p.hasAttribute('aria-label')||p.hasAttribute('data-y'))err(tag+c+' has no law but is focusable / labelled / dated');
  p.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerType:'mouse'}));
  if(r1.textContent!==e[5])err(tag+c+' readout line 1 "'+r1.textContent+'" != "'+e[5]+'"');
  if(r2.textContent!==e[6])err(tag+c+' readout line 2 "'+r2.textContent+'" != "'+e[6]+'"');
  if(q('#sm-scope').textContent!==e[7])err(tag+c+' scope readout differs');
  if(!q('#sm-hl').getAttribute('d'))err(tag+c+' has no hover outline');
  if(!near(ro.getBoundingClientRect().height,44,0.6))err(tag+c+' readout height changed');
  var full=[r1.textContent,r2.textContent,q('#sm-scope').textContent].filter(Boolean).join(' \u00b7 ');
  if(ro.scrollWidth>ro.clientWidth+1){if(ro.title!==full)err(tag+c+' truncated readout lacks its full text in title')}else if(ro.hasAttribute('title'))err(tag+c+' readout has a title but is not truncated');
  if(!near(card.getBoundingClientRect().height,h0,0.6))err(tag+c+' hover changed the card height '+h0+' -> '+card.getBoundingClientRect().height)});
 svg.dispatchEvent(new PointerEvent('pointerleave',{pointerType:'mouse'}));
 if(r1.textContent!==EX.ro_default||r2.textContent!==''||q('#sm-hl').hasAttribute('d'))err(tag+'readout / outline not reset after the pointer left');
 var keys=Object.keys(E.counts),sum=0,all=0;
 keys.forEach(function(k){if((counts[k]||0)!==E.counts[k])err(tag+'bin '+k+': '+(counts[k]||0)+' != '+E.counts[k])});
 if(Object.keys(counts).length!==keys.length)err(tag+'unexpected classes '+Object.keys(counts));
 Object.keys(counts).forEach(function(k){all+=counts[k];if(k!=='n')sum+=counts[k]});
 if(all!==51)err(tag+'classes add up to '+all+', not 51');
 if(sum!==E.n||nlaw!==E.n)err(tag+'coloured states: classes '+sum+', focusable '+nlaw+', n_states '+E.n);
 if(pst.textContent!==E.stats)err(tag+'stats "'+pst.textContent+'" != "'+E.stats+'"');
 if(pn.textContent!==E.name)err(tag+'header "'+pn.textContent+'" != "'+E.name+'"');
 if(svg.getAttribute('aria-label')!==E.aria)err(tag+'map label differs: '+svg.getAttribute('aria-label'));
 var ends=qa('.sm-rk i').map(function(e){return e.textContent});if(!same(ends,E.ends))err(tag+'legend bins '+ends+' != '+E.ends);
 if(lgt.getBoundingClientRect().height>20)err(tag+'the legend title wraps ('+lgt.getBoundingClientRect().height+' px tall)');
 if(lgt.textContent!==E.lgt||!lgt.textContent||lgt.textContent.length>28)err(tag+'legend title "'+lgt.textContent+'" != "'+E.lgt+'" (or empty / over 28 characters)');
 var rk=qa('.sm-rk i').map(function(e){var g=document.createRange();g.selectNodeContents(e);return g.getBoundingClientRect()});
 for(var kk=0;kk<rk.length-1;kk++)if(rk[kk+1].left-rk[kk].right<8)err(tag+'legend tick labels '+ends[kk]+' / '+ends[kk+1]+' are '+(rk[kk+1].left-rk[kk].right).toFixed(1)+' px apart (< 8)');
 if(VW>=1280){var lc=[].map.call(q('.nx-legend').children,function(e){return Math.round(e.getBoundingClientRect().top)});if(!lgd.hidden&&Math.max.apply(0,lc)-Math.min.apply(0,lc)>1)err(tag+'legend is not ONE row at >= 1280: tops '+lc)}
 var on=tabs.filter(function(t){return t.getAttribute('aria-checked')==='true'});
 if(on.length!==1||on[0].dataset.f!==id)err(tag+'checked button is '+on.map(function(t){return t.dataset.f}));
 if(tabs.filter(function(t){return t.tabIndex===0}).length!==1||on[0]&&on[0].tabIndex!==0)err(tag+'roving tabindex broken');
 // database dots: a subset of the coloured states, the legend entry only when there is one
 var dots=qa('.sm-dot').filter(function(d){return getComputedStyle(d).display!=='none'}),dc=dots.map(function(d){return d.dataset.c}).sort();
 if(!same(dc,E.dots))err(tag+'dots on '+dc+' != '+E.dots);
 dots.forEach(function(d){if(d.dataset.f!==id)err(tag+'a dot of '+d.dataset.f+' is visible');if(!E.states[d.dataset.c][2])err(tag+'dot on a state without a year: '+d.dataset.c);
  var m=svg.getScreenCTM(),rr=+d.getAttribute('r')*m.a;if(rr<2.4||rr>3.6)err(tag+'dot is '+rr.toFixed(2)+' px in radius, expected ~3');
  if(getComputedStyle(d).fill!=='rgb(255, 255, 255)')err(tag+'dot fill '+getComputedStyle(d).fill)});
 if(lgd.hidden!==(E.dots.length===0)||(!lgd.hidden&&lgd.textContent.trim()!==EX.lg_db))err(tag+'database legend entry: hidden '+lgd.hidden+', dots '+E.dots.length);
 res[id]={counts:counts,nlaw:nlaw,dots:dc.length,rows:nlaw}}
// the page: selector buttons
if(!list)err('no role=radiogroup');
if(tabs.length!==8||EX.order.length!==8)err('selector buttons '+tabs.length+' / expected '+EX.order.length+', not 8');
if(!same(tabs.map(function(t){return t.dataset.f}),EX.order))err('selector order '+tabs.map(function(t){return t.dataset.f}));
if(svg.getAttribute('tabindex')!=='-1')err('the map svg is a sequential tab stop of its own (needs tabindex=-1)');
if(q('#sm-scope').parentNode!==ro)err('scope has a separate layout row instead of sharing the readout');
if(list&&(list.style.getPropertyValue('--nx-cols')||'').trim()!=='4')err('--nx-cols is '+list.style.getPropertyValue('--nx-cols')+' for 8 policies, expected 4');
tabs.forEach(function(t,i){var E=EX.p[EX.order[i]],tr=t.getBoundingClientRect();
 if(t.tagName!=='BUTTON'||t.getAttribute('type')!=='button')err('button '+i+' is not <button type=button>');
 if(t.textContent!==E.short)err('button '+i+' text "'+t.textContent+'" != "'+E.short+'"');
 if(t.getAttribute('aria-label')!==E.row_aria)err('button '+i+' aria-label "'+t.getAttribute('aria-label')+'" != "'+E.row_aria+'"');
 if(tr.height<(D?47.5:43.5))err('button '+i+' is '+tr.height+' px tall (< '+(D?48:44)+')')});
// the page: legend
var segs=qa('.nx-ramp i'),lg=q('.nx-legend');
if(segs.length!==5)err('legend ramp has '+segs.length+' segments, not 5');
segs.forEach(function(e,i){if(getComputedStyle(e).backgroundColor!==EX.bins[i])err('legend bin '+(i+1)+' colour '+getComputedStyle(e).backgroundColor+' != '+EX.bins[i])});
if(lg.children.length!==2)err('legend has '+lg.children.length+' entries, expected 2 (the ramp, the database dot)');
var lts=[].map.call(lg.children,function(e){return Math.round(e.getBoundingClientRect().top)});
if(!lgd.hidden&&Math.max.apply(0,lts)-Math.min.apply(0,lts)>1&&VW>=1280)err('legend is not ONE row at >= 1280: tops '+lts);
if(/no law|not enough|no matching|not yet covered|coverage|没有匹配|数据不足|暂未覆盖|未发现|覆盖/i.test(card.innerText+' '+cap.textContent))err('text about states without a law / without data / coverage is on the page');
// the page: layout numbers
var cr=card.getBoundingClientRect(),sr=svg.getBoundingClientRect(),lr=list.getBoundingClientRect(),cso=getComputedStyle(card),sso=getComputedStyle(svg);
geo={vw:VW,card:Math.round(cr.height),cardw:Math.round(cr.width),map:Math.round(sr.width)+'x'+Math.round(sr.height),list:Math.round(lr.width)+'x'+Math.round(lr.height)};
if(document.documentElement.scrollWidth>VW+1)err('page scrolls horizontally: '+document.documentElement.scrollWidth+' > '+VW);
if(cso.overflowX!=='hidden'||cso.overflowY!=='hidden')err('the card does not clip');
if(sso.overflowX!=='hidden')err('the map svg does not clip');
['sm-f','sm-cm','sm-bm','sm-co'].forEach(function(id){var b=document.getElementById(id).getBBox();
 if(b.x<-0.5||b.y<-0.5||b.x+b.width>975.5||b.y+b.height>610.5)err('layer '+id+' reaches outside the frame')});
if(D){var hd=q('.sm-head').getBoundingClientRect(),inner=cr.width-2-48;
 if(cr.height>700)err('card is '+cr.height+' px tall, over 700');
 if(!near(sr.width,inner,2.5)||!near(sr.height,inner/1.6,3))err('map is '+sr.width+' x '+sr.height+', expected '+inner+' x '+inner/1.6);
 if(hd.height>26)err('the header takes '+hd.height+' px: more than one line');
 var tops=tabs.map(function(t){return Math.round(t.getBoundingClientRect().top)}).filter(function(v,i,a){return a.indexOf(v)===i});
 if(tops.length!==2)err('selector grid has '+tops.length+' rows, expected 2 (4 x 2)');
 if(getComputedStyle(list).display!=='grid')err('selector is not a grid at >= 1100');
 if(tabs.some(function(t){return t.scrollWidth>t.clientWidth+1}))err('a selector label overflows its cell');
 var hr=document.querySelector('.nx-herotext').getBoundingClientRect();if(hr.right>cr.left+0.5)err('hero text and the card overlap')}
else{var tt=tabs.map(function(t){return Math.round(t.getBoundingClientRect().top)}),ls=getComputedStyle(list);
 if(Math.max.apply(0,tt)-Math.min.apply(0,tt)>1)err('pills are not in ONE row: tops '+tt);
 if(ls.overflowX!=='auto'&&ls.overflowX!=='scroll')err('pill row does not scroll: overflow-x '+ls.overflowX);
 if(ls.scrollSnapType.indexOf('x')<0)err('pill row has no scroll-snap');
 if(sr.height>441)err('map is '+sr.height+' tall, over 440');
 if(VW<=600){if(!near(cr.left,0,1)||!near(cr.right,VW,1))err('card is not full-bleed: '+cr.left+'..'+cr.right);
  if(parseFloat(cso.borderLeftWidth)!==0)err('full-bleed card keeps its side border');
  if(!near(sr.width,VW,1))err('map is '+sr.width+' wide, not full width')}}
// the page: type floor, no shadow, nothing moves
qa('*').forEach(function(el){var cs=getComputedStyle(el),tag=el.tagName+'.'+(typeof el.className==='string'?el.className:'');
 if(cs.animationName!=='none')err('animation on '+tag);
 if(!window.NXMotion||!NXMotion.allowed()){if(cs.transitionDuration.split(',').some(function(d){return parseFloat(d)>0}))err('ungated transition on '+tag);}
 if(cs.backgroundImage!=='none')err('background-image / gradient on '+tag);
 if(cs.boxShadow!=='none')err('shadow on '+tag);
 if(el.getClientRects().length&&[].some.call(el.childNodes,function(n){return n.nodeType===3&&n.textContent.trim()})&&parseFloat(cs.fontSize)<12)err('text below 12px on '+tag+': '+cs.fontSize)});
if(R.querySelectorAll('button').length!==9)err('expected eight selector buttons and one motion control');
if(q('#sm-control').compareDocumentPosition(svg)&Node.DOCUMENT_POSITION_PRECEDING)err('motion control follows SVG focus targets');
if(!window.NXMotion||!NXMotion.allowed()){if(getComputedStyle(q('#sm-control')).display!=='none')err('motion control visible in static mode')}
if(window.NXMotion)NXMotion.finishAll('selfcheck-final');
if(R.querySelector('a,[href]'))err('a link is present in the card');
if(/\bsources?\b|来源/i.test(card.innerText+' '+cap.textContent+' '+R.innerText))err('the word "Sources" / 来源 is on the page');
if(/https?:\/\//.test(R.outerHTML.replace(/<script type="application\/json"[\s\S]*?<\/script>/,'').replace(/<script>[\s\S]*?<\/script>/,'')))err('an external url is in the markup');
if(/https?:\/\//.test(q('#sm-d').textContent))err('an external url is in the data block');
if(R.querySelector('input,select,textarea,video,audio'))err('interactive control present');
if(R.querySelector('animate,animateTransform,set'))err('SMIL animation present');
// the caption: ONE muted line, left-aligned, 12 px under the card
var cpt=cap.getBoundingClientRect(),cr2=card.getBoundingClientRect();
geo.foot=Math.round(cpt.bottom-cr2.top);
if(D&&!near(cpt.left,cr2.left,1.5))err('caption is not flush with the card edge on the left');
if(!near(cpt.top-cr2.bottom,12,1.5))err('caption is '+(cpt.top-cr2.bottom)+' px under the card, expected 12');
if(cpt.height>21&&VW>=820)err('caption takes '+cpt.height+' px: more than one line');
if(D&&VW>=1280&&(cr2.height>640||geo.foot>700))err('card is '+cr2.height+' px tall (cap 640) / '+geo.foot+' px with the caption (cap 700)');
if(D&&lg.getBoundingClientRect().top-sr.bottom>44)err('map to legend is '+(lg.getBoundingClientRect().top-sr.bottom)+' px (> 44): an empty band');
// every policy: the static default, each button clicked, all again in reverse
var cur=tabs.filter(function(t){return t.getAttribute('aria-checked')==='true'})[0];
if(!cur||cur.dataset.f!==EX.default)err('default policy is '+(cur&&cur.dataset.f)+', expected '+EX.default);
else check(EX.default,'default');
EX.order.forEach(function(f){document.getElementById('sm-tab-'+f).click();check(f,'click')});
EX.order.slice().reverse().forEach(function(f){document.getElementById('sm-tab-'+f).click();check(f,'back')});
// the pill strip below 1100: the selected pill is scrolled to the leading edge (instantly), a fade marks the overflowing sides
if(!D&&list.scrollWidth>list.clientWidth+1){
 var mk=function(){var c=getComputedStyle(list);return c.maskImage||c.webkitMaskImage||'none'},
  mid=document.getElementById('sm-tab-'+EX.order[4]),lst=document.getElementById('sm-tab-'+EX.order[7]),fst=document.getElementById('sm-tab-'+EX.order[0]);
 mid.click();var la=list.getBoundingClientRect(),lrr=mid.getBoundingClientRect(),mx=list.scrollWidth-list.clientWidth;
 if(list.scrollLeft>1&&list.scrollLeft<mx-1&&Math.abs(lrr.left-la.left-(parseFloat(getComputedStyle(list).paddingLeft)||0))>2.5)err('selected pill is not fully aligned at the strip leading edge');
 if(!list.classList.contains('fl')||!list.classList.contains('fr')||mk()==='none')err('both-sided fade missing with the strip scrolled mid-way ('+list.className+')');
 lst.click();if(!list.classList.contains('fl')||list.classList.contains('fr')||mk()==='none')err('left-only fade wrong at the end of the strip ('+list.className+')');
 fst.click();if(list.classList.contains('fl')||!list.classList.contains('fr')||mk()==='none')err('right-only fade wrong at the start of the strip ('+list.className+')');
 if(list.scrollLeft>1)err('first pill is not on the inner grid line after selecting it (scrollLeft '+list.scrollLeft+')')}
if(D&&(function(){var c=getComputedStyle(list);return (c.maskImage||c.webkitMaskImage||'none')!=='none'})())err('the pill-strip fade is on at >= 1100');
// the keyboard (radio group): arrows move focus AND select; Home / End; wrap; Down / Up move by one row in the grid, by one in the strip
function key(el,k){el.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true}))}
function step(from,k,to){tabs[from].focus();key(tabs[from],k);var id=EX.order[to];
 if(document.activeElement!==tabs[to])err(k+' on button '+from+' did not focus button '+to);
 if(tabs[to].getAttribute('aria-checked')!=='true')err(k+' on button '+from+' did not select button '+to);
 if(tabs.filter(function(t){return t.tabIndex===0}).length!==1||tabs[to].tabIndex!==0)err(k+': roving tabindex not on button '+to);
 check(id,k)}
document.getElementById('sm-tab-'+EX.order[0]).click();
step(0,'ArrowRight',1);step(1,'ArrowLeft',0);step(0,'ArrowLeft',7);step(7,'ArrowRight',0);step(3,'End',7);step(5,'Home',0);
if(D){step(1,'ArrowDown',5);step(5,'ArrowUp',1);step(5,'ArrowDown',1);step(1,'ArrowUp',5)}
else{step(1,'ArrowDown',2);step(2,'ArrowUp',1)}
tabs[4].focus();tabs[4].click();check(EX.order[4],'enter');
document.getElementById('sm-tab-'+EX.default).click();check(EX.default,'final');
document.documentElement.setAttribute('data-selfcheck',JSON.stringify({ok:!bad.length,bad:bad.slice(0,12),nbad:bad.length,ntabs:tabs.length,nstate:nstate,vw:VW,geo:geo,families:res}));
})();
"""


def write_previews() -> int:
    spec = importlib.util.spec_from_file_location("viz_build", HERE / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    errs = build.Errors()
    build.check_dict_pair("story_map", errs)
    data = story_data()
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for lang in build.LANGS:
        head = build.render_partial("head_css", lang, errs)
        body = build.render_partial("story_map", lang, errs)
        expect = json.dumps(expectation(data, build.load_dict("story_map", lang, errs)), separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")
        outputs[lang] = PREVIEW % {"html_lang": build.HTML_LANG[lang], "head": head, "body": body, "pick": PICK_JS.strip(),
                                   "tokens": TOKENS.read_text(encoding="utf-8"), "hero": HERO_CSS.read_text(encoding="utf-8"),
                                   "check": SELFCHECK_JS.strip(), "expect": expect, "motion": (HERE / "partials" / "motion.html").read_text(encoding="utf-8")}
    if errs.errors:
        print("PREVIEW FAILED:", file=sys.stderr)
        for e in errs.errors:
            print("  - " + e, file=sys.stderr)
        return 1
    for lang, page in outputs.items():
        dest = PREVIEW_DIR / ("story_map_preview_%s.html" % lang)
        dest.write_text(page, encoding="utf-8")
        print("wrote %s (%d bytes)" % (dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest, len(page.encode("utf-8"))))
    return 0


def _chrome():
    import os
    import shutil
    for cand in (os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if cand and Path(cand).is_file():
            return cand
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def token_problems():
    """Check resolved canonical map colours and required shared classes.

    Return None if a stylesheet is absent; historical no-CSS fallbacks stay unchanged.
    """
    if not TOKENS.is_file() or not HERO_CSS.is_file():
        return None
    css = TOKENS.read_text(encoding="utf-8-sig")
    hero = HERO_CSS.read_text(encoding="utf-8-sig")
    bad = []
    # The canonical role sheet supersedes the map's historical no-CSS fallback.
    # Resolve aliases before comparison; text/text-2 now share the body role.
    values = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;{}]+)", re.sub(r"/\*.*?\*/", "", css, flags=re.S)))
    def resolve(name, seen=()):
        if name in seen or name not in values:
            return None
        value = values[name].strip()
        alias = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", value)
        if alias:
            return resolve(alias[1], seen + (name,))
        return value.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else None
    expected = dict(NX, text=NX['text-2'])
    for k, v in expected.items():
        actual = resolve('--nx-' + k)
        if actual is None:
            bad.append("--nx-%s is missing or does not resolve to a colour" % k)
        elif actual != v:
            bad.append("--nx-%s resolves to %s, canonical map role expects %s" % (k, actual, v))
    for cls in ("nx-frame", "nx-mapcard", "nx-tabs", "nx-tab", "nx-in", "nx-mapcol", "nx-legend"):
        if "." + cls not in css:
            bad.append("class .%s is not in design_tokens.css (the partial relies on it)" % cls)
    for cls in ("nx-herocard", "nx-herogrid", "nx-herotext", "sm-svg"):
        if "." + cls not in hero:
            bad.append("class .%s is not in design_hero.css (the partial relies on it)" % cls)
    return bad


def _run_selfcheck_page(chrome: str, lang: str, width: int, height: int, query: str):
    """The preview page in headless Chrome (--dump-dom, no CDP) -> the parsed data-selfcheck result, or None."""
    import subprocess
    url = (PREVIEW_DIR / ("story_map_preview_%s.html" % lang)).as_uri() + "?" + query
    run = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--allow-file-access-from-files",
                          "--window-size=%d,%d" % (width, height), "--virtual-time-budget=4000", "--dump-dom", url],
                         capture_output=True, text=True, timeout=180)
    m = re.search(r'data-selfcheck="([^"]*)"', run.stdout)
    return json.loads(html_mod.unescape(m.group(1))) if m else None


def data_checks(data: dict) -> int:
    """Checks that need no browser: for every policy the number of coloured states, the evidence, the sources, the bins, and the data
    embedded in the partial against the data file; the i18n pair."""
    bad = 0
    text = OUT.read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="sm-d">(.*?)</script>', text, re.S)
    blob = json.loads(m.group(1).replace("<\\/", "</")) if m else None
    if not blob or blob.get("c") != CODES:
        print("FAIL the partial has no data block with the 51 states in order", file=sys.stderr)
        return 1
    for p in data["policies"]:
        pid, why = p["id"], []
        if len(p["years"]) != p["n"]:
            why.append("n coloured %d != n_states %d" % (len(p["years"]), p["n"]))
        if not set(p["evidence"]) <= set(p["years"]):
            why.append("database dots %s are not all coloured states" % sorted(set(p["evidence"]) - set(p["years"])))
        b = p["bins"]
        if len(b) != 5 or b[0][0] != p["y0"] or b[-1][1] != p["y1"] or any(b[i][1] + 1 != b[i + 1][0] for i in range(4)):
            why.append("bins %s are not five contiguous ranges %d..%d" % (b, p["y0"], p["y1"]))
        if any(not any(lo <= y <= hi for y in p["years"].values()) for lo, hi in b):
            why.append("an empty bin in %s" % b)
        e = blob["p"].get(pid)
        if not e or e["y"] != [p["years"].get(c, 0) for c in CODES] or e["b"] != [list(x) for x in b]:
            why.append("the data block of the partial differs from the data file (years / bins)")
        else:
            if set(e) != {"y", "b", "e"} or e["e"] != {c: [t, y] for c, (t, y) in p["evidence"].items()}:
                why.append("the data block of the partial differs from the data file (database rows) or carries more than years / bins / evidence")
        print("%s %-26s %2d coloured = n_states, bins %-34s dots %d"
              % ("ok  " if not why else "FAIL", pid, len(p["years"]), " ".join(bin_label(*x) for x in b), len(p["evidence"])))
        for w in why:
            print("     " + w, file=sys.stderr)
        bad += 1 if why else 0
    en = json.loads((HERE / "i18n" / "story_map.en.json").read_text(encoding="utf-8"))
    zh = json.loads((HERE / "i18n" / "story_map.zh.json").read_text(encoding="utf-8"))
    if sorted(en) != sorted(zh):
        print("FAIL i18n key sets differ: %s" % sorted(set(en) ^ set(zh)), file=sys.stderr)
        bad += 1
    else:
        print("ok   i18n: EN and ZH have the same %d keys" % len(en))
    return bad


def selfcheck() -> int:
    """Validate chronology/control contracts, static final data and all-family browser parity.
    The browser checks geometry, keyboard/readout/control placement and reduced/print defaults.
    A deliberately corrupted expected year must fail, proving final-parity checks are active.
    """
    bad = 0
    errs = _validator().validate(json.loads(CURATED.read_text(encoding="utf-8")))
    print("%s validate_curated.py: %s" % ("ok  " if not errs else "FAIL", "the data file is valid" if not errs else errs[0]))
    bad += 1 if errs else 0
    text = OUT.read_text(encoding="utf-8")
    # Behavior checks exercise equal-year cohorts, empty years, controls and static cleanup
    # with the same NXMotion runtime used in the browser; no animation ban on curated dates.
    import unittest
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromName("scripts.viz_site.tests.test_polish_home.MapMotionTests")
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
    bad += 0 if result.wasSuccessful() else 1
    for pat in (r"@keyframes", r"<animate", r"<set\b", r"setInterval", r"setTimeout", r"requestAnimationFrame", r"IntersectionObserver",
                r"<input", r"<select", r"<textarea", r'type="range"', r"autoplay", r"(?<![a-z-])scroll-behavior", r"box-shadow", r"scrollIntoView",
                r"<details", r"overflow\s*:\s*(auto|scroll)", r"max-height\s*:\s*260", r"id=\"sm-tip\"", r"fetch\s*\("):
        if re.search(pat, text, re.I):
            print("FAIL partial contains %r" % pat, file=sys.stderr)
            bad += 1
    for m in re.finditer(r"([a-z-]+)\s*:\s*[^;{}]*-gradient", text, re.I):          # a gradient only as the edge fade (mask-image) of the pill strip
        if "mask-image" not in m.group(1).lower():
            print("FAIL partial uses a gradient in %r" % m.group(1), file=sys.stderr)
            bad += 1
    n_btn = len(re.findall(r"<button\b", text))
    if n_btn != 9 or len(re.findall(r'<button type="button" role="radio"', text)) != 8:
        print("FAIL partial has %d <button>, expected eight role=radio buttons and one playback control" % n_btn, file=sys.stderr)
        bad += 1
    markup = re.sub(r"<script.*?</script>", "", re.sub(r"<!--.*?-->", "", text, flags=re.S), flags=re.S)
    for pat, what in ((r"<a[\s>]", "a link"), (r"https?://", "an external url"), (r"\bhref\s*=", "an href"), (r"[Ss]ources?\b|\u6765\u6e90", "the word Sources"),
                      (r"sm-src|sm-foot|sm-ov|sm-sl", "sources markup / css")):
        if re.search(pat, markup):
            print("FAIL the partial contains %s (the Sources panel is gone)" % what, file=sys.stderr)
            bad += 1
    if "https://" in re.search(r'<script type="application/json" id="sm-d">(.*?)</script>', text, re.S).group(1):
        print("FAIL the data block carries urls", file=sys.stderr)
        bad += 1
    if ":focus-visible" not in text or 'role="radiogroup"' not in text:
        print("FAIL partial lacks the focus ring rule or the radiogroup", file=sys.stderr)
        bad += 1
    words = re.compile(r"no law|not enough data|no matching|not yet covered|\u6ca1\u6709\u5339\u914d|\u6570\u636e\u4e0d\u8db3|\u6682\u672a\u8986\u76d6|\u672a\u53d1\u73b0|coverage", re.I)
    for name, body in [("partial", re.sub(r"<script type=\"application/json\".*?</script>", "", text, flags=re.S))] + [
            ("i18n/story_map.%s.json" % lg, (HERE / "i18n" / ("story_map.%s.json" % lg)).read_text(encoding="utf-8")) for lg in ("en", "zh")]:
        hit = words.search(body)
        if hit:
            print("FAIL %s mentions states without a law / without data / coverage: %r" % (name, hit.group(0)), file=sys.stderr)
            bad += 1
    data = story_data()
    print("%s partial text: gated finite chronology, static final bins, no shadow / raw timer, %d buttons, no links, no Sources, no source data (%d bytes)"
          % ("ok  " if not bad else "FAIL", n_btn, len(text.encode("utf-8"))))
    bad += data_checks(data)
    tp = token_problems()
    if tp is None:
        print("FAIL scripts/viz_site/design/design_tokens.css or design_hero.css not found: the partial needs them", file=sys.stderr)
        return 1
    for line in tp:
        print("FAIL tokens: " + line, file=sys.stderr)
    bad += 1 if tp else 0
    print("%s tokens: design_tokens.css resolves all %d canonical map colours and supplies the required classes"
          % ("ok  " if not tp else "FAIL", len(NX)))
    chrome = _chrome()
    if not chrome:
        print("selfcheck needs Chrome: install it or set CHROME=/path/to/chrome", file=sys.stderr)
        return 2
    import importlib.util as iu
    spec = iu.spec_from_file_location("viz_build", HERE / "build.py")
    build = iu.module_from_spec(spec)
    spec.loader.exec_module(build)
    ids = [p["id"] for p in data["policies"]]
    for lang in ("en", "zh"):
        ex = expectation(data, build.load_dict("story_map", lang, build.Errors()))
        for width in (1400, 1100, 820, 390):
            res = _run_selfcheck_page(chrome, lang, 1400 if width == 1400 else 900, 1000, "selfcheck" if width == 1400 else "frame=%d" % width)
            if res is None:
                print("FAIL %s %d px: no self-check result in the page" % (lang, width), file=sys.stderr)
                bad += 1
                continue
            fams_ok = sorted(res["families"]) == sorted(ids) and all(
                res["families"][f]["counts"] == ex["p"][f]["counts"] and res["families"][f]["nlaw"] == ex["p"][f]["n"]
                and res["families"][f]["dots"] == len(ex["p"][f]["dots"]) and res["families"][f]["rows"] == ex["p"][f]["n"]
                for f in ids if f in res["families"])
            ok = res["ok"] and fams_ok and res["ntabs"] == 8 and res["vw"] == width
            g = res["geo"]
            print("%s %s %4d px  %d policies, %d selector buttons, %d state checks, %d problems | card %d x %d px (%d px with the caption), map %s"
                  % ("ok  " if ok else "FAIL", lang, res["vw"], len(res["families"]), res["ntabs"], res["nstate"], res["nbad"], g["cardw"], g["card"], g["foot"], g["map"]))
            if ok and lang == "en" and width == 1400:
                for f in ids:
                    r = res["families"][f]
                    print("       %-26s %2d coloured, %d dots, %2d sources rows: %s" % (f, r["nlaw"], r["dots"], r["rows"], " ".join(
                        "%s:%d" % (k, r["counts"][k]) for k in sorted(r["counts"]))))
            for b in res["bad"]:
                print("     " + b, file=sys.stderr)
            bad += 0 if ok else 1
    neg = _run_selfcheck_page(chrome, "en", 1400, 1000, "selfcheck&mutate")
    if neg is None or neg["ok"]:
        print("FAIL the self-check did not catch a deliberately corrupted expectation", file=sys.stderr)
        bad += 1
    else:
        print("ok   negative control: a corrupted expectation is reported (%d problem(s): %s)" % (neg["nbad"], neg["bad"][0]))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when partials/story_map.html or the policy keys of i18n/story_map.*.json are stale; write nothing")
    ap.add_argument("--preview", action="store_true",
                    help="also write deploy_output/_landing_options/story_map_preview_{en,zh}.html (not production outputs)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="write the previews, then check every policy (data file, text, headless Chrome: bin == year, sources, dots, selector, layout, chronology/final parity/static fallback)")
    args = ap.parse_args(argv)
    data = story_data()
    fresh = render_partial()
    if args.check:
        bad = 0
        current = OUT.read_text(encoding="utf-8-sig").replace("\r\n", "\n") if OUT.is_file() else None
        if current != fresh:
            print("partials/story_map.html is stale: run python3 scripts/viz_site/make_story_map.py", file=sys.stderr)
            bad = 1
        stale = sync_i18n(data, write=False)
        if stale:
            print("%s: the policy keys are stale: run python3 scripts/viz_site/make_story_map.py" % ", ".join(stale), file=sys.stderr)
            bad = 1
        if not bad:
            print("story_map.html is up to date (%d bytes)" % len(fresh.encode("utf-8")))
        return bad
    for name in sync_i18n(data, write=True):
        print("updated i18n/%s (policy names, legend titles and scope notes from the data file)" % name)
    OUT.write_text(fresh, encoding="utf-8")
    print("wrote %s (%d bytes)" % (OUT.relative_to(ROOT), len(fresh.encode("utf-8"))))
    if args.selfcheck:
        return write_previews() or selfcheck()
    return write_previews() if args.preview else 0


if __name__ == "__main__":
    sys.exit(main())
