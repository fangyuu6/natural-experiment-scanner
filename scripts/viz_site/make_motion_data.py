#!/usr/bin/env python3
"""Build scripts/viz_site/data/motion_data.json: the data of the Home motion figure (port of docs/plans/motion/v14/build_data14.js).

The figure has three acts. Screening narrows the policy texts to the shortlist; Estimates colours the nurse licensure compact
map by year and counts the re-estimated papers; Explore pulls back to one small map per state policy, in five bands by the
year the first state adopted, and runs the wall on one clock (years after the first state).

Inputs (read only; every path and its SHA-256 is recorded under "inputs" in the output):
  scripts/viz_site/partials/story_map.html        state shapes of the Home map card (975 x 610 frame) and their label points
  scripts/viz_site/make_story_map.py              story_data(): the 8 verified policies exactly as the Home map draws them
  scripts/viz_site/data/curated_adoption.json     read through story_data()
  scripts/viz_site/public_page.py                 simplify_adoption(): the Home card's three year bands, legend and colours (code, not hashed:
                                                  its effect is the "bins", "binLabels" and "binColors" of the output)
  data/l3_pooled/downloads/SPID_v1.2.tab          DellaVigna-Kim SPID v1.2: policy, state, adopt_year (all states, 724 policies)
  deploy_output/funnel_data.json                  the policy-text count (483,414) and what each screening step keeps
                                                  (read only; Screening prints the same numbers). When this file is not on the
                                                  machine, docs/plans/motion/v7/build/data.js (counts.texts, counts.cands,
                                                  counts.gates) is used instead and the output says so under "inputs".
Nothing about papers or the snapshot is stored here: the counts of the Estimates act come from the site's own loader
(restructure_estimates.public_counts / attempted) when the page is built.

Output is deterministic: the same inputs give the same bytes. Stdlib only.

  python3 scripts/viz_site/make_motion_data.py            write data/motion_data.json
  python3 scripts/viz_site/make_motion_data.py --check    fail when the committed file differs from a rebuild
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = HERE / 'data' / 'motion_data.json'
STORY_MAP = HERE / 'partials' / 'story_map.html'
SPID = ROOT / 'data' / 'l3_pooled' / 'downloads' / 'SPID_v1.2.tab'
FUNNEL = ROOT / 'deploy_output' / 'funnel_data.json'
V7_COUNTS = ROOT / 'docs' / 'plans' / 'motion' / 'v7' / 'build' / 'data.js'

CLOCK_K = 5                                  # the clock runs 0..5 years after the first state
OBS_END = {'s': 2017, 'c': 2025}             # last year observed: SPID ends in 2017, the 8 verified policies in 2025
ERA = (1900, 1950, 1980, 2000)               # bands: before 1900, 1900-49, 1950-79, 1980-99, 2000 on
TILE_TOL = 2.2                               # px, Douglas-Peucker tolerance of the small maps of the wall
NURSE = 'nurse_licensure_compact'

NAME = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA', 'Colorado': 'CO',
    'Connecticut': 'CT', 'Delaware': 'DE', 'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
    'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS', 'Kentucky': 'KY', 'Louisiana': 'LA',
    'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN',
    'Mississippi': 'MS', 'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH',
    'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND',
    'Ohio': 'OH', 'Oklahoma': 'OK', 'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI',
    'South Carolina': 'SC', 'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
    'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY'}

_NUM = re.compile(r'-?(?:\d+\.?\d*|\.\d+)')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- state shapes (read back from the Home map)

def parse_path(d: str) -> list:
    """Rings (lists of (x, y) floats) of an SVG path made of M/m, l and z, the format of make_story_map._ring_d."""
    rings, ring, cur, start, i = [], None, (0.0, 0.0), (0.0, 0.0), 0
    for m in re.finditer(r'([MmLlZz])([^MmLlZz]*)', d):
        cmd, nums = m.group(1), [float(x) for x in _NUM.findall(m.group(2))]
        if cmd in 'Mm':
            if ring:
                rings.append(ring)
            x, y = nums[0], nums[1]
            cur = (x, y) if cmd == 'M' else (cur[0] + x, cur[1] + y)
            start, ring = cur, [cur]
            rest = nums[2:]
            for k in range(0, len(rest), 2):          # a move followed by pairs is a line
                cur = (cur[0] + rest[k], cur[1] + rest[k + 1]) if cmd == 'm' else (rest[k], rest[k + 1])
                ring.append(cur)
        elif cmd in 'Ll':
            for k in range(0, len(nums), 2):
                cur = (cur[0] + nums[k], cur[1] + nums[k + 1]) if cmd == 'l' else (nums[k], nums[k + 1])
                ring.append(cur)
        else:                                          # z
            if ring:
                rings.append(ring)
                ring = None
            cur = start
    if ring:
        rings.append(ring)
    return rings


def ring_area(ring: list) -> float:
    s = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def douglas_peucker(pts: list, tol: float) -> list:
    n = len(pts)
    if n <= 4:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1e-9
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / length
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def tenths(v: float) -> int:
    return int(round(v * 10))


def num(v: int) -> str:
    """Integer tenths -> shortest SVG number: 13 -> '1.3', -4 -> '-.4', 20 -> '2'."""
    if v == 0:
        return '0'
    sign = '-' if v < 0 else ''
    whole, frac = divmod(abs(v), 10)
    return sign + (str(whole) if whole else '') + ('.' + str(frac) if frac else '')


def seq(values: list) -> str:
    out = []
    for v in values:
        t = num(v)
        if out and not (t[0] == '-' or (t[0] == '.' and '.' in out[-1])):
            out.append(' ')
        out.append(t)
    return ''.join(out)


def encode(rings: list) -> str:
    """Rings of integer tenths as one closed path: absolute M for the first ring, relative m for the others."""
    parts, start = [], None
    for ring in rings:
        x0, y0 = ring[0]
        parts.append(('M' + seq([x0, y0])) if start is None else ('m' + seq([x0 - start[0], y0 - start[1]])))
        deltas = []
        for (ax, ay), (bx, by) in zip(ring, ring[1:]):
            deltas += [bx - ax, by - ay]
        if deltas:
            parts.append('l' + seq(deltas))
        parts.append('z')
        start = ring[0]
    return ''.join(parts)


def tile_path(d: str) -> str:
    """The small-map shape of a state: rings of at least 6 px^2 (all rings for a one-ring state), simplified."""
    rings = parse_path(d)
    big = [r for r in rings if ring_area(r) >= 6 or len(rings) == 1] or rings
    out = []
    for r in big:
        simple = douglas_peucker(r, TILE_TOL)
        pts, last = [], None
        for x, y in simple:
            p = (tenths(x), tenths(y))
            if p != last:
                pts.append(p)
            last = p
        if len(pts) >= 3:
            out.append(pts)
    return encode(out)


def state_geometry() -> dict:
    text = STORY_MAP.read_text(encoding='utf-8')
    found = re.findall(r'<path class="sm-s [^"]*" data-c="(\w\w)" data-cx="([^"]+)" data-cy="([^"]+)"[^>]*? d="([^"]*)"/>', text)
    if len(found) != 51:
        raise SystemExit('story_map.html: expected 51 state paths, found %d' % len(found))
    found.sort(key=lambda row: row[0])
    return {'codes': [r[0] for r in found], 'lab': [[float(r[1]), float(r[2])] for r in found],
            'd': [r[3] for r in found], 'ds': [tile_path(r[3]) for r in found]}


# --------------------------------------------------------------------------- adoptions

def spid_policies() -> dict:
    lines = [l for l in SPID.read_text(encoding='utf-8').splitlines() if l]
    head = [s.strip('"') for s in lines[0].split('\t')]
    out, bad = {}, 0
    for line in lines[1:]:
        row = dict(zip(head, [s.strip('"') for s in line.split('\t')]))
        try:
            year = int(round(float(row['adopt_year'])))
        except ValueError:
            bad += 1
            continue
        code = NAME.get(row['statenam'])
        if code is None:
            bad += 1
            continue
        adopted = out.setdefault(row['policy'], {})
        if code not in adopted or year < adopted[code]:
            adopted[code] = year
    return out


def era(year: int) -> int:
    k = 0
    while k < len(ERA) and year >= ERA[k]:
        k += 1
    return k


def median(values: list):
    v = sorted(values)
    n = len(v)
    m = v[(n - 1) // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return int(m) if m == int(m) else m


def card_bands() -> dict:
    """The year bands, labels and colours of the nurse licensure compact on the Home card, read from the card as the public page
    shows it (public_page.simplify_adoption projects the five bands of the partial onto three; its data, its legend and its style
    block are read here, nothing is retyped)."""
    from scripts.viz_site import public_page
    card = public_page.simplify_adoption(STORY_MAP.read_text(encoding='utf-8') + '</head>')      # a page head, which carries the band colours
    first = re.search(r'data-f="([^"]+)" aria-checked="true"', card)      # the tab the card opens on
    if not first or first[1] != NURSE:
        raise SystemExit('the Home card no longer opens on the nurse licensure compact')
    bands = json.loads(re.search(r'<script type="application/json" id="sm-d">(.*?)</script>', card, re.S)[1])['p'][NURSE]['b']
    labels = re.findall(r'<i>([^<]*)</i>', re.search(r'<span class="sm-rk" aria-hidden="true">(.*?)</span>', card, re.S)[1])
    colors = [hexa for _, hexa in sorted(re.findall(r'\.sm \.r(\d)\{background:(#[0-9a-fA-F]{6})\}', card))]
    if not (len(bands) == len(labels) == len(colors) >= 2):
        raise SystemExit('the Home card legend does not match its year bands')
    return {'bins': [list(b) for b in bands], 'binLabels': labels, 'binColors': [c.lower() for c in colors]}


def tiles_and_clock(codes: list) -> tuple:
    from scripts.viz_site import make_story_map
    idx = {c: i for i, c in enumerate(codes)}
    tiles = [{'src': 's', 'id': pid, 'a': a} for pid, a in spid_policies().items()]
    verified = make_story_map.story_data()['policies']
    for p in verified:
        tiles.append({'src': 'c', 'id': p['id'], 'a': dict(p['years'])})
    for t in tiles:
        t['y0'] = min(t['a'].values())
    tiles.sort(key=lambda t: (era(t['y0']), t['y0'], 0 if t['src'] == 's' else 1, t['id']))
    bands = [0] * 5
    for t in tiles:
        bands[era(t['y0'])] += 1
    packed = []
    for t in tiles:
        row = [t['y0']]
        for code, year in sorted(t['a'].items(), key=lambda kv: (kv[1], kv[0])):
            row += [idx[code], year - t['y0']]
        packed.append(row)
    by_band = [[] for _ in range(5)]
    for t in tiles:
        by_band[era(t['y0'])].append(t)
    clock_bands = []
    for lst in by_band:
        within = [sum(1 for y in t['a'].values() if y - t['y0'] <= CLOCK_K)
                  for t in lst if t['y0'] + CLOCK_K <= OBS_END[t['src']]]
        clock_bands.append({'n': len(within), 'median': median(within)})
    unobserved = sum(1 for t in tiles if t['y0'] + CLOCK_K > OBS_END[t['src']])
    nurse = [p for p in verified if p['id'] == NURSE][0]
    nurse_tile = [i for i, t in enumerate(tiles) if t['src'] == 'c' and t['id'] == NURSE][0]
    out_tiles = {'n': len(tiles), 'bands': bands, 'src': ''.join(t['src'] for t in tiles), 'packed': packed,
                 'verified': len(verified), 'spid': len(tiles) - len(verified), 'open': nurse_tile}
    clock = {'k': CLOCK_K, 'obsEnd': OBS_END, 'bands': clock_bands, 'unobserved': unobserved}
    nurse_out = {'id': NURSE, 'y0': nurse['y0'], 'y1': nurse['y1'], 'n': nurse['n'],
                 'a': sorted([[idx[c], y] for c, y in nurse['years'].items()]),
                 # the Home card's own year bands, their labels and colours, so the figure draws the map as the card does
                 **card_bands()}
    return out_tiles, clock, nurse_out


# --------------------------------------------------------------------------- screening counts

def screening_counts() -> tuple:
    if FUNNEL.is_file():
        funnel = json.loads(FUNNEL.read_text(encoding='utf-8'))
        stages = funnel['stages']
        if stages[0]['n_out'] != funnel['total']:
            raise SystemExit('funnel_data.json: the first stage is not the total')
        gates = [s['n_out'] for s in stages[1:]]
        return funnel['total'], gates[-1], gates, {'path': 'deploy_output/funnel_data.json', 'sha256': sha256(FUNNEL)}
    text = V7_COUNTS.read_text(encoding='utf-8')
    counts = json.loads(text[text.index('{'):text.rindex('};') + 1])['counts']
    return (counts['texts'], counts['cands'], counts['gates'],
            {'path': 'docs/plans/motion/v7/build/data.js', 'sha256': sha256(V7_COUNTS),
             'note': 'deploy_output/funnel_data.json is not on this machine; counts.texts, counts.cands and counts.gates used instead'})


def build() -> str:
    geo = state_geometry()
    tiles, clock, nurse = tiles_and_clock(geo['codes'])
    texts, cands, gates, funnel_input = screening_counts()
    inputs = {
        'scripts/viz_site/partials/story_map.html': {'sha256': sha256(STORY_MAP)},
        'scripts/viz_site/data/curated_adoption.json': {'sha256': sha256(HERE / 'data' / 'curated_adoption.json')},
        'data/l3_pooled/downloads/SPID_v1.2.tab': {'sha256': sha256(SPID)},
        funnel_input['path']: {k: v for k, v in funnel_input.items() if k != 'path'}}
    if gates[-1] != cands:
        raise SystemExit('the last screening step does not end at the shortlist count')
    doc = {'asof': {'spid': 'SPID v1.2', 'verified': json.loads((HERE / 'data' / 'curated_adoption.json').read_text(encoding='utf-8'))['as_of']},
           'inputs': inputs,
           'counts': {'texts': texts, 'cands': cands, 'gates': gates},
           'map': geo, 'nurse': nurse, 'tiles': tiles, 'clock': clock}
    return json.dumps(doc, ensure_ascii=False, separators=(',', ':'), sort_keys=True) + '\n'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', action='store_true', help='fail when the committed data/motion_data.json differs from a rebuild')
    ap.add_argument('--out', type=Path, default=OUT)
    args = ap.parse_args(argv)
    text = build()
    if args.check:
        if not args.out.is_file() or args.out.read_text(encoding='utf-8') != text:
            print('motion_data.json is missing or stale: run python3 scripts/viz_site/make_motion_data.py', file=sys.stderr)
            return 1
        print('motion_data.json is current (%d bytes)' % len(text.encode('utf-8')))
        return 0
    args.out.write_text(text, encoding='utf-8')
    doc = json.loads(text)
    t = doc['tiles']
    print('wrote %s (%d bytes): %d maps (%d SPID + %d verified), bands %s, clock medians %s, unobserved %d'
          % (args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out, len(text.encode('utf-8')), t['n'], t['spid'], t['verified'],
             t['bands'], [b['median'] for b in doc['clock']['bands']], doc['clock']['unobserved']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
