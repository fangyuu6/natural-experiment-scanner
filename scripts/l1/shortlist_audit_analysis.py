#!/usr/bin/env python3
"""L1 work package C, part 1: the pre-registered analysis of the blind shortlist audit (protocol Sec. 6.4-6.6 and Sec. 7).

Written and hashed before the hub opened any coder output file (hash in data/pipeline/shortlist_audit_v1_key/ANALYSIS_FREEZE.json).
Stdlib only, Python 3.9+. The frozen field list, enums, seed and bootstrap size are read from the protocol's machine-readable block
through scripts/l1/shortlist_audit.py (validation and the third-read rule are that script's own functions).

  lock     both coders have all batches, every file validates, write LOCK_AB.json with the file hashes     [never opens KEY.csv]
  analyze  needs the lock and the complete third read; reads KEY.csv and the coder files; writes FINAL_LABELS.csv and the results file

Usage:  python3 -B scripts/l1/shortlist_audit_analysis.py lock
        python3 -B scripts/l1/shortlist_audit_analysis.py analyze [--notes-file FILE]
Options only for dry runs on synthetic data: --key-dir --packet-dir --out-md --out-labels --replicates --seed --freeze --session-log.

Conventions fixed here (the protocol leaves them open):
  * random numbers: Python's random.Random(seed).random(), index = lo + int(random() * n); the stratified bootstrap draws, replicate by
    replicate, n_h units with replacement in each stratum in the order (arm, stratum) of the key; the agreement bootstrap uses a second
    generator with the same seed and draws, replicate by replicate, groups A, B, C, all in this order.
  * percentile interval: linear interpolation between order statistics at 2.5% and 97.5% of the replicates that are defined.
  * Wilson interval: z = 1.959964, applied to the weighted share at the effective size n_eff of Sec. 7.4.
  * a "usable" unit has a label that is not U; every share is a ratio estimator over usable units (Sec. 7.2).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import random
import sys
from array import array
from collections import Counter, defaultdict
from operator import itemgetter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortlist_audit as sa  # noqa: E402  (frozen draw/validate script; read-only here)

NAN = float('nan')
Z = 1.959963984540054
YN = ('Yes', 'No')
FIELDS8 = ['did_treated', 'did_comparison', 'did_timing', 'did_viable',
           'rd_running_variable', 'rd_cutoff', 'rd_assignment', 'rd_viable']
AXES = {'D': ('did_viable', ('did_treated', 'did_comparison', 'did_timing')),
        'R': ('rd_viable', ('rd_running_variable', 'rd_cutoff', 'rd_assignment'))}
AXIS_NAME = {'D': 'DiD', 'R': 'RD'}
GROUPS_A = ['FR', 'RG', 'SL', 'OT']
BANDS = ['L', 'M', 'H']
PAPER = {'D': 0.587, 'R': 0.422}
BATCH_THIRDS = ((1, 8), (9, 16), (17, 23))


# ----------------------------------------------------------------------------------------------------------------------
# Formatting and small statistics
# ----------------------------------------------------------------------------------------------------------------------

def isnan(x):
    return x is None or x != x


def pct(x, digits=1):
    return 'n/a' if isnan(x) else '%.*f%%' % (digits, 100.0 * x)


def pts(x, digits=1):
    return 'n/a' if isnan(x) else '%+.*f pts' % (digits, 100.0 * x)


def num(x, digits=3):
    return 'n/a' if isnan(x) else '%.*f' % (digits, x)


def ival(lo, hi, fmt=pct):
    return 'n/a' if isnan(lo) or isnan(hi) else '[%s, %s]' % (fmt(lo), fmt(hi))


def wilson(p, n):
    if isnan(p) or isnan(n) or n <= 0:
        return NAN, NAN
    p = min(1.0, max(0.0, p))
    z2 = Z * Z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = Z * math.sqrt(max(0.0, p * (1.0 - p) / n + z2 / (4.0 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def quantile(sorted_vals, q):
    m = len(sorted_vals)
    if m == 0:
        return NAN
    pos = q * (m - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, m - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def boot_ci(values):
    vals = sorted(v for v in values if v == v)
    return quantile(vals, 0.025), quantile(vals, 0.975), len(values) - len(vals)


def make_summer(idx):
    idx = list(idx)
    if not idx:
        return lambda seq: 0
    if len(idx) == 1:
        only = idx[0]
        return lambda seq: seq[only]
    getter = itemgetter(*idx)
    return lambda seq: sum(getter(seq))


def md_table(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '|'.join('---' for _ in headers) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return out


def kappa_ac1(n, agree, a_yes, b_yes):
    if n == 0:
        return NAN, NAN, NAN
    po = agree / n
    pa, pb = a_yes / n, b_yes / n
    pe_k = pa * pb + (1 - pa) * (1 - pb)
    kappa = (po - pe_k) / (1 - pe_k) if pe_k < 1 else NAN
    pi = (pa + pb) / 2.0
    pe_g = 2 * pi * (1 - pi)
    ac1 = (po - pe_g) / (1 - pe_g) if pe_g < 1 else NAN
    return po, kappa, ac1


# ----------------------------------------------------------------------------------------------------------------------
# Reading coder files, labels
# ----------------------------------------------------------------------------------------------------------------------

def load_codes(packet_dir, letter, spec):
    """All batches of one coder; every file is validated by the frozen script's own rules. Returns rows, file hashes, items."""
    folder = Path(packet_dir) / ('coder_' + letter)
    items = sa.read_csv(folder / ('items_%s.csv' % letter))[0]
    expected = defaultdict(set)
    for r in items:
        expected[int(r['batch'])].add(r['item_id'])
    rows, hashes = {}, {}
    for nn in sorted(expected):
        path = folder / 'out' / ('codes_%s_b%02d.csv' % (letter, nn))
        if not path.exists():
            raise SystemExit('missing file: %s' % path)
        with open(path, newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            header, recs = reader.fieldnames, list(reader)
        errors = sa.validate_rows(header, recs, spec, expected[nn])
        if errors:
            raise SystemExit('%s does not validate: %s' % (path.name, errors[0]))
        hashes['coder_%s/out/%s' % (letter, path.name)] = sa.sha256_file(path)
        for r in recs:
            rows[r['item_id']] = dict(r, batch=str(nn))
    return rows, hashes, items


def final_label(a, b, c):
    """Sec. 6.6: A and B agree -> that answer; else the answer two of the three give; else Unresolved; both NA -> Unavailable.
    Returns (1 | 0 | None, how)."""
    if a == 'NA' and b == 'NA':
        return None, 'unavailable'
    if a in YN and a == b:
        return (1 if a == 'Yes' else 0), 'agree'
    votes = Counter(x for x in (a, b, c) if x in YN)
    top = [ans for ans, k in votes.items() if k >= 2]
    if top:
        return (1 if top[0] == 'Yes' else 0), 'majority'
    return None, 'unresolved'


def label_word(lab, how):
    if lab == 1:
        return 'Yes'
    if lab == 0:
        return 'No'
    return 'Unavailable' if how == 'unavailable' else 'Unresolved'


def g_of(fD, fR, yD, yR):
    """Listed-design label (Sec. 7.1): 1 if some flagged axis is 1, 0 if every flagged axis is 0, else U."""
    ys = ([yD] if fD else []) + ([yR] if fR else [])
    if any(y == 1 for y in ys):
        return 1
    if ys and all(y == 0 for y in ys):
        return 0
    return None


def v_of(yD, yR):
    """Either-axis label (Sec. 7.1), flag ignored."""
    if yD == 1 or yR == 1:
        return 1
    if yD == 0 and yR == 0:
        return 0
    return None


def check_sessions(log_path, coders):
    """The last logged attempt of every coder batch must be clean (Sec. 5.5)."""
    if not Path(log_path).exists():
        print('WARNING: no coder session log at %s' % log_path)
        return None
    rows = sa.read_csv(log_path)[0]
    last = {}
    for r in rows:
        key = (r['coder'], int(r['batch']))
        if key not in last or int(r['attempt']) >= int(last[key]['attempt']):
            last[key] = r
    bad = ['%s b%02d (%s)' % (k[0], k[1], r['status']) for k, r in sorted(last.items()) if k[0] in coders and r['status'] != 'clean']
    if bad:
        raise SystemExit('last session of these batches is not clean: ' + ', '.join(bad))
    return rows


# ----------------------------------------------------------------------------------------------------------------------
# Step 1: lock
# ----------------------------------------------------------------------------------------------------------------------

def cmd_lock(args):
    spec = sa.read_spec(args.protocol)
    key_dir, packet_dir = Path(args.key_dir), Path(args.packet_dir)
    out = key_dir / 'LOCK_AB.json'
    if out.exists():
        raise SystemExit('lock exists already: ' + str(out))
    files = {}
    for letter in 'AB':
        rows, hashes, items = load_codes(packet_dir, letter, spec)
        if len(rows) != len(items):
            raise SystemExit('coder %s: %d rows for %d items' % (letter, len(rows), len(items)))
        files.update(hashes)
        print('coder %s: %d batches, %d items, all files valid' % (letter, len(hashes), len(rows)))
    check_sessions(args.session_log or key_dir / 'CODER_SESSION_LOG.csv', 'AB')
    record = {'locked_at_utc': sa.utc_now(), 'protocol_sha256': sa.sha256_file(args.protocol),
              'analysis_script_sha256': sa.sha256_file(__file__), 'draw_script_sha256': sa.sha256_file(sa.__file__),
              'files': files}
    sa.write_text_new(out, json.dumps(record, indent=1) + '\n')
    print('locked %d files -> %s' % (len(files), out))


# ----------------------------------------------------------------------------------------------------------------------
# Step 2: analysis
# ----------------------------------------------------------------------------------------------------------------------

class Registry:
    """Ratio estimators (shares) and functions of them, evaluated on the sample and on every bootstrap replicate."""

    def __init__(self, weights):
        self.W = weights
        self.ratios, self.derived, self.by_key = [], [], {}

    def prop(self, key, base, lab):
        W = self.W
        yes = [i for i in base if lab[i] == 1]
        usable = [i for i in base if lab[i] is not None]
        unk = [i for i in base if lab[i] is None]
        sw = sum(W[i] for i in usable)
        sw2 = sum(W[i] * W[i] for i in usable)
        swb = sum(W[i] for i in base)
        swy = sum(W[i] for i in yes)
        rec = {'key': key, 'num': yes, 'den': usable, 'k': len(yes), 'n': len(usable), 'base': len(base), 'U': len(unk),
               'point': min(1.0, swy / sw) if sw > 0 else NAN, 'neff': (sw * sw / sw2) if sw2 > 0 else NAN,
               'lower': swy / swb if swb > 0 else NAN,
               'upper': min(1.0, (swy + sum(W[i] for i in unk)) / swb) if swb > 0 else NAN,
               'index': len(self.ratios)}
        self.ratios.append(rec)
        self.by_key[key] = rec
        return rec

    def derive(self, key, fn):
        def safe(v, fn=fn):
            try:
                return fn(v)
            except ZeroDivisionError:
                return NAN
        rec = {'key': key, 'fn': safe, 'index': len(self.derived)}
        self.derived.append(rec)
        self.by_key[key] = rec
        return rec

    def finish_points(self):
        v = [r['point'] for r in self.ratios]
        for d in self.derived:
            d['point'] = d['fn'](v)

    def bootstrap(self, units, R, seed):
        ranges, lo = [], 0
        while lo < len(units):
            s = (units[lo]['arm'], units[lo]['stratum'])
            hi = lo
            while hi < len(units) and (units[hi]['arm'], units[hi]['stratum']) == s:
                hi += 1
            ranges.append((lo, hi - lo))
            lo = hi
        sums_n = [make_summer(r['num']) for r in self.ratios]
        sums_d = [make_summer(r['den']) for r in self.ratios]
        K = len(self.ratios)
        boots = [array('d') for _ in range(K)]
        dboots = [array('d') for _ in self.derived]
        rnd = random.Random(seed).random
        n_units = len(units)
        for _rep in range(R):
            counts = [0] * n_units
            for start, size in ranges:
                for _j in range(size):
                    counts[start + int(rnd() * size)] += 1
            cw = [c * w for c, w in zip(counts, self.W)]
            vals = []
            for k in range(K):
                d = sums_d[k](cw)
                if d > 0:
                    x = sums_n[k](cw) / d
                    vals.append(x if x < 1.0 else 1.0)
                else:
                    vals.append(NAN)
            for k in range(K):
                boots[k].append(vals[k])
            for j, dr in enumerate(self.derived):
                dboots[j].append(dr['fn'](vals))
        for rec, b in zip(self.ratios, boots):
            rec['ci'] = boot_ci(b)
        for rec, b in zip(self.derived, dboots):
            rec['ci'] = boot_ci(b)


def agreement_bootstrap(groups, R, seed):
    """Bootstrap over items (Sec. 7.6) for percent agreement, kappa and AC1; groups: name -> list of (row_A, row_B)."""
    prep = {}
    for g, pairs in groups.items():
        for f in FIELDS8:
            valid, agree, ay, by = [], [], [], []
            for i, (a, b) in enumerate(pairs):
                if a[f] in YN and b[f] in YN:
                    valid.append(i)
                    if a[f] == b[f]:
                        agree.append(i)
                    if a[f] == 'Yes':
                        ay.append(i)
                    if b[f] == 'Yes':
                        by.append(i)
            prep[(g, f)] = tuple(make_summer(x) for x in (valid, agree, ay, by))
    boots = {k: ([], [], []) for k in prep}
    rnd = random.Random(seed).random
    for _rep in range(R):
        for g, pairs in groups.items():
            n = len(pairs)
            counts = [0] * n
            for _j in range(n):
                counts[int(rnd() * n)] += 1
            for f in FIELDS8:
                s_valid, s_agree, s_ay, s_by = prep[(g, f)]
                nv = s_valid(counts)
                stats = kappa_ac1(nv, s_agree(counts), s_ay(counts), s_by(counts)) if nv else (NAN, NAN, NAN)
                for lst, s in zip(boots[(g, f)], stats):
                    lst.append(s)
    return {k: tuple(boot_ci(lst)[:2] for lst in v) for k, v in boots.items()}


def agreement_point(pairs, field):
    n = ag = ay = by = yy = yn = ny = nn = 0
    for a, b in pairs:
        x, y = a[field], b[field]
        if x in YN and y in YN:
            n += 1
            ag += x == y
            ay += x == 'Yes'
            by += y == 'Yes'
            yy += x == 'Yes' and y == 'Yes'
            yn += x == 'Yes' and y == 'No'
            ny += x == 'No' and y == 'Yes'
            nn += x == 'No' and y == 'No'
    po, kappa, ac1 = kappa_ac1(n, ag, ay, by)
    return {'n': n, 'po': po, 'kappa': kappa, 'ac1': ac1, 'pa': ay / n if n else NAN, 'pb': by / n if n else NAN,
            'cross': (yy, yn, ny, nn)}


def anchor_stats(pairs):
    """pairs: (rater label 1/0, human label 1/0) for the anchors with a usable rater label."""
    tp = sum(1 for r, h in pairs if r == 1 and h == 1)
    fn = sum(1 for r, h in pairs if r == 0 and h == 1)
    fp = sum(1 for r, h in pairs if r == 1 and h == 0)
    tn = sum(1 for r, h in pairs if r == 0 and h == 0)
    n = tp + fn + fp + tn
    out = {'n': n, 'tp': tp, 'fn': fn, 'fp': fp, 'tn': tn}
    if n:
        out['agree'] = (tp + tn) / n
        _po, kappa, _ac1 = kappa_ac1(n, tp + tn, tp + fp, tp + fn)
        out['kappa'] = kappa
        out['rater_yes'] = (tp + fp) / n
        out['human_yes'] = (tp + fn) / n
    out['sens'] = tp / (tp + fn) if tp + fn else NAN
    out['spec'] = tn / (tn + fp) if tn + fp else NAN
    return out


def link_category(r):
    if r.get('error') == 'no url':
        return 'no address'
    st = r.get('http_status') or ''
    if st:
        return {'2': '2xx', '3': '3xx', '4': '4xx', '5': '5xx'}.get(st[0], st)
    return 'error: ' + (r.get('error') or 'unknown')


def cmd_analyze(args):
    spec = sa.read_spec(args.protocol)
    key_dir, packet_dir = Path(args.key_dir), Path(args.packet_dir)
    out_md = Path(args.out_md)
    out_labels = Path(args.out_labels) if args.out_labels else key_dir / 'FINAL_LABELS.csv'
    for p in (out_md, out_labels):
        if p.exists():
            raise SystemExit('refusing to overwrite ' + str(p))
    R = args.replicates or spec['bootstrap']['replicates']
    seed = args.seed or spec['bootstrap']['seed']
    freeze = json.loads(Path(args.freeze or key_dir / 'FREEZE.json').read_text(encoding='utf-8'))
    protocol_hash = sa.sha256_file(args.protocol)
    if protocol_hash != freeze['protocol_sha256']:
        raise SystemExit('REFUSED: the protocol on disk is not the frozen protocol')
    lock_path = key_dir / 'LOCK_AB.json'
    if not lock_path.exists():
        raise SystemExit('REFUSED: no lock; run the lock step first')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))

    codes = {}
    hashes = {}
    for letter in 'AB':
        codes[letter], h, _items = load_codes(packet_dir, letter, spec)
        hashes.update(h)
    for name, digest in lock['files'].items():
        if hashes.get(name) != digest:
            raise SystemExit('REFUSED: %s changed after the lock' % name)
    if set(hashes) != set(lock['files']):
        raise SystemExit('REFUSED: the set of coder files differs from the lock')
    diffs = sa.disagreements(codes['A'], codes['B'])
    disputed = {d[0] for d in diffs}
    codes['C'], hashes_c, items_c = load_codes(packet_dir, 'C', spec)
    if set(codes['C']) != disputed:
        raise SystemExit('REFUSED: coder C coded %d items, the third read needs exactly %d' % (len(codes['C']), len(disputed)))
    session_rows = check_sessions(args.session_log or key_dir / 'CODER_SESSION_LOG.csv', 'ABC')

    # ---- key and units -----------------------------------------------------------------------------------------------
    key_rows = sa.read_csv(key_dir / 'KEY.csv')[0]
    key = {}
    for r in key_rows:
        r = dict(r)
        r['N_h'], r['n_h'], r['batch'] = int(r['N_h']), int(r['n_h']), int(r['batch'])
        r['w'] = r['N_h'] / r['n_h']
        key[r['item_id']] = r
    if set(key) != set(codes['A']) or set(key) != set(codes['B']):
        raise SystemExit('REFUSED: the key and the coder files do not cover the same items')
    for arm in 'ABC':
        for stratum, cell in spec['arms'][arm]['strata'].items():
            members = [r for r in key.values() if r['stratum'] == stratum]
            if len(members) != cell['n'] or any(r['N_h'] != cell['N'] or r['n_h'] != cell['n'] for r in members):
                raise SystemExit('REFUSED: stratum %s differs from the protocol' % stratum)
    N_A = sum(c['N'] for c in spec['arms']['A']['strata'].values())
    N_B = sum(c['N'] for c in spec['arms']['B']['strata'].values())

    final = {}   # item -> axis -> (lab, how)
    for iid in key:
        c = codes['C'].get(iid)
        final[iid] = {ax: final_label(codes['A'][iid][AXES[ax][0]], codes['B'][iid][AXES[ax][0]], c[AXES[ax][0]] if c else None)
                      for ax in 'DR'}

    ordered = sorted((k for k in key.values() if k['arm'] in 'AB'), key=lambda k: (k['arm'], k['stratum'], k['item_id']))
    units, W = [], []
    for k in ordered:
        iid = k['item_id']
        u = {'id': iid, 'arm': k['arm'], 'stratum': k['stratum'], 'group': k['group'], 'band': k['band'], 'flag': k['flag'],
             'fD': k['did'] == '1', 'fR': k['rd'] == '1', 'orphan': k['orphan'] == '1', 'flagged': k['flagged'] == '1',
             'gates': [x for x in k['gates_failed'].split('|') if x], 'w': k['w'], 'batch': k['batch']}
        a, b = codes['A'][iid], codes['B'][iid]
        y = {'final': {}, 'strict': {}, 'lenient': {}}
        for ax in 'DR':
            field = AXES[ax][0]
            y['final'][ax] = final[iid][ax][0]
            if a[field] in YN and b[field] in YN:
                y['strict'][ax] = 1 if (a[field] == 'Yes' and b[field] == 'Yes') else 0
                y['lenient'][ax] = 1 if (a[field] == 'Yes' or b[field] == 'Yes') else 0
            else:
                y['strict'][ax] = y['lenient'][ax] = None
        u['y'] = y
        units.append(u)
        W.append(u['w'])
    LAB = {}
    for scheme in ('final', 'strict', 'lenient'):
        LAB[scheme] = {
            'yD': [u['y'][scheme]['D'] for u in units], 'yR': [u['y'][scheme]['R'] for u in units],
            'g': [g_of(u['fD'], u['fR'], u['y'][scheme]['D'], u['y'][scheme]['R']) for u in units],
            'v': [v_of(u['y'][scheme]['D'], u['y'][scheme]['R']) for u in units]}

    reg = Registry(W)
    idx_A = [i for i, u in enumerate(units) if u['arm'] == 'A']
    idx_B = [i for i, u in enumerate(units) if u['arm'] == 'B']

    # ---- Sec. 7.2 shortlist results ------------------------------------------------------------------------------------
    domains = [('All shortlisted candidates', idx_A)]
    for gname in GROUPS_A:
        domains.append(('Source group ' + gname, [i for i in idx_A if units[i]['group'] == gname]))
    for b in BANDS:
        domains.append(('Band ' + b, [i for i in idx_A if units[i]['band'] == b]))
    for gname in GROUPS_A:
        for b in BANDS:
            domains.append(('%s x band %s' % (gname, b), [i for i in idx_A if units[i]['group'] == gname and units[i]['band'] == b]))
    domains.append(('Flag D only', [i for i in idx_A if units[i]['flag'] == 'D']))
    domains.append(('Flag R (with or without DiD)', [i for i in idx_A if units[i]['flag'] == 'R']))
    domains.append(('State Legislation orphan rows', [i for i in idx_A if units[i]['group'] == 'SL' and units[i]['orphan']]))
    domains.append(('State Legislation other rows', [i for i in idx_A if units[i]['group'] == 'SL' and not units[i]['orphan']]))
    domains.append(('Shortlist without State Legislation orphan rows', [i for i in idx_A if not (units[i]['group'] == 'SL' and units[i]['orphan'])]))
    dom_names = [d[0] for d in domains]
    short_domains = dom_names[:1 + len(GROUPS_A) + len(BANDS)]   # strict / lenient variants: all, groups, bands
    for scheme in ('final', 'strict', 'lenient'):
        L = LAB[scheme]
        for name, idx in domains:
            if scheme != 'final' and name not in short_domains:
                continue
            if idx:
                reg.prop((scheme, name, 'PL'), idx, L['g'])
            iD = [i for i in idx if units[i]['fD']]
            iR = [i for i in idx if units[i]['fR']]
            if iD:
                reg.prop((scheme, name, 'PD'), iD, L['yD'])
            if iR:
                reg.prop((scheme, name, 'PR'), iR, L['yR'])

    def get(*k):
        return reg.by_key.get(k)

    # score gradient (derived): P_L(H) - P_L(L) and P_L(M) - P_L(L); within Federal Register and State Legislation as extras
    for tag, names in (('overall', ['Band ' + b for b in BANDS]),
                       ('FR', ['FR x band ' + b for b in BANDS]), ('SL', ['SL x band ' + b for b in BANDS])):
        low, mid, high = (get('final', n, 'PL') for n in names)
        if low and mid and high:
            reg.derive(('grad', tag, 'H-L'), lambda v, a=high['index'], b=low['index']: v[a] - v[b])
            reg.derive(('grad', tag, 'M-L'), lambda v, a=mid['index'], b=low['index']: v[a] - v[b])

    # ---- Sec. 7.3 arm B comparison ---------------------------------------------------------------------------------------
    VA = reg.prop(('V', 'A'), idx_A, LAB['final']['v'])
    VB = reg.prop(('V', 'B'), idx_B, LAB['final']['v'])
    ia, ib = VA['index'], VB['index']
    reg.derive(('cmp', 'diff'), lambda v: v[ia] - v[ib])
    reg.derive(('cmp', 'VU'), lambda v: (N_A * v[ia] + N_B * v[ib]) / (N_A + N_B))
    reg.derive(('cmp', 'lift'), lambda v: v[ia] / ((N_A * v[ia] + N_B * v[ib]) / (N_A + N_B)))
    reg.derive(('cmp', 'capture'), lambda v: N_A * v[ia] / (N_A * v[ia] + N_B * v[ib]))
    reg.derive(('cmp', 'specificity'), lambda v: N_B * (1 - v[ib]) / (N_B * (1 - v[ib]) + N_A * (1 - v[ia])))
    for gname in GROUPS_A:
        idx = [i for i in idx_A if units[i]['group'] == gname]
        if idx:
            reg.prop(('V', 'A', gname), idx, LAB['final']['v'])
    b_flagged = reg.prop(('V', 'B', 'flagged'), [i for i in idx_B if units[i]['flagged']], LAB['final']['v'])
    b_unflag = reg.prop(('V', 'B', 'unflagged'), [i for i in idx_B if not units[i]['flagged']], LAB['final']['v'])
    reg.derive(('cmp', 'B flagged - unflagged'), lambda v, a=b_flagged['index'], b=b_unflag['index']: v[a] - v[b])
    for gname in ('FR', 'SL', 'OT'):
        reg.prop(('V', 'B', gname), [i for i in idx_B if units[i]['group'] == gname], LAB['final']['v'])
    b_strata = sorted({units[i]['stratum'] for i in idx_B})
    for s in b_strata:
        reg.prop(('V', 'B', s), [i for i in idx_B if units[i]['stratum'] == s], LAB['final']['v'])
    rules = ['wave', 'covid', 'data', 'min_gate']
    for rname in rules:
        idx = [i for i in idx_B if rname in units[i]['gates']]
        if idx:
            reg.prop(('V', 'B', 'fails ' + rname), idx, LAB['final']['v'])
    combos = sorted({'+'.join(units[i]['gates']) for i in idx_B})
    for c in combos:
        idx = [i for i in idx_B if '+'.join(units[i]['gates']) == c]
        reg.prop(('V', 'B', 'only ' + (c or 'none')), idx, LAB['final']['v'])

    reg.finish_points()
    reg.bootstrap(units, R, seed)

    # ---- Sec. 7.6 agreement -----------------------------------------------------------------------------------------------
    pairs_all = [(codes['A'][i], codes['B'][i]) for i in sorted(key)]
    groups = {'A': [(codes['A'][i], codes['B'][i]) for i in sorted(key) if key[i]['arm'] == 'A'],
              'B': [(codes['A'][i], codes['B'][i]) for i in sorted(key) if key[i]['arm'] == 'B'],
              'C': [(codes['A'][i], codes['B'][i]) for i in sorted(key) if key[i]['arm'] == 'C'],
              'all': pairs_all}
    agree_ci = agreement_bootstrap(groups, R, seed)
    agree = {(g, f): agreement_point(p, f) for g, p in groups.items() for f in FIELDS8}

    anchors = sorted(i for i in key if key[i]['arm'] == 'C')
    anchor_out = {}
    for ax in 'DR':
        for rater in ('Coder A', 'Coder B', 'Final label'):
            pairs = []
            for iid in anchors:
                h = int(key[iid]['human_did' if ax == 'D' else 'human_rd'])
                r_ = final[iid][ax][0] if rater == 'Final label' else {'Yes': 1, 'No': 0}.get(codes[rater[-1]][iid][AXES[ax][0]])
                if r_ is not None:
                    pairs.append((r_, h))
            anchor_out[(ax, rater)] = anchor_stats(pairs)

    # ---- assemble the report -----------------------------------------------------------------------------------------------
    L = []
    add = L.append

    def prop_cells(rec):
        if rec is None:
            return ['-'] * 7 + ['no units']
        p = rec['point']
        wlo, whi = wilson(p, rec['neff'])
        blo, bhi = rec['ci'][0], rec['ci'][1]
        ushare = rec['U'] / rec['base'] if rec['base'] else NAN
        notes = []
        est = pct(p)
        if not isnan(ushare) and ushare > 0.20:
            est = 'bounds %s (point %s)' % (ival(rec['lower'], rec['upper']), pct(p))
            notes.append('U > 20%: the bounds are the headline')
        if not (isnan(blo) or isnan(wlo)) and max(abs(blo - wlo), abs(bhi - whi)) > 0.03:
            notes.append('ends differ by > 3 pts')
        if rec['ci'][2]:
            notes.append('%d replicates undefined' % rec['ci'][2])
        if rec['n'] < 10:
            notes.append('n < 10')
        return ['%d/%d' % (rec['k'], rec['n']), '%d/%d (%s)' % (rec['U'], rec['base'], pct(ushare, 0)), est,
                ival(blo, bhi), ival(wlo, whi), num(rec['neff'], 1), ival(rec['lower'], rec['upper']), '; '.join(notes)]

    PROP_HEAD = ['Domain', 'k/n usable', 'U / drawn', 'Estimate', 'Bootstrap 95%', 'Wilson 95%', 'n_eff', 'Bounds (U to 0, U to 1)', 'Notes']

    def prop_table(rows):
        return md_table(PROP_HEAD, [[name] + prop_cells(rec) for name, rec in rows])

    n_items = {arm: sum(1 for k in key.values() if k['arm'] == arm) for arm in 'ABC'}
    status = {c: Counter(codes[c][i]['text_status'] for i in codes[c]) for c in 'ABC'}
    n_disputed = len(disputed)
    PL, PD, PR = get('final', 'All shortlisted candidates', 'PL'), get('final', 'All shortlisted candidates', 'PD'), get('final', 'All shortlisted candidates', 'PR')

    add('# Shortlist blind audit v1: results')
    add('')
    add('Protocol `docs/plans/l1/SHORTLIST_AUDIT_PROTOCOL_20261016.md` (sha256 `%s`). Generated by `scripts/l1/shortlist_audit_analysis.py` '
        '(sha256 `%s`) at %s. All figures below are produced by that script from the coder files and the key; no label was edited by hand. '
        'Every labelled figure is "Claude-coder viability" (Sec. 1): the coders are Claude Sonnet 5.5 sessions, not the paper\'s human assistants, '
        'and the audit says nothing about whether a design would survive estimation.' % (protocol_hash, sa.sha256_file(__file__), sa.utc_now()))
    add('')
    add('Sample: %d items (arm A shortlist %d, arm B enacted and not shortlisted %d, arm C anchors %d). Bootstrap: %d replicates, seed %d, '
        'Python %s random.Random. Weights w = N/n of the stratum. Unweighted counts k/n (usable units) are printed with every weighted figure; '
        'U = label Unresolved or Unavailable; no finite-population correction; no p-values.'
        % (len(key), n_items['A'], n_items['B'], n_items['C'], R, seed, platform.python_version()))
    if args.notes_file:
        add('')
        add('## Run notes')
        add('')
        add(Path(args.notes_file).read_text(encoding='utf-8').strip())

    # key results
    add('')
    add('## 1. Key results')
    add('')
    add('Claude-coder viability measured on a random sample of the list, to be read beside the paper\'s human panel (precision among flagged texts: DiD 58.7%, RD 42.2%) and beside the anchor result below, never alone.')
    add('')
    add('**Shortlist (arm A).** P_L: share of shortlisted candidates whose listed design is judged viable. P_D and P_R: the paper\'s precision, measured on the list.')
    add('')
    for t in prop_table([('P_L, listed design viable (N = %d)' % N_A, PL), ('P_D, DiD viable among DiD-flagged (N = 7,746)', PD),
                         ('P_R, RD viable among RD-flagged (N = 1,170)', PR)]):
        add(t)
    add('')
    add('Strict (both coders Yes) and lenient (either coder Yes), items where both coders read the text:')
    add('')
    for t in prop_table([('%s %s' % (sc.capitalize(), t_), get(sc, 'All shortlisted candidates', kd)) for sc in ('strict', 'lenient')
                         for kd, t_ in (('PL', 'P_L'), ('PD', 'P_D'), ('PR', 'P_R'))]):
        add(t)
    add('')
    add('**Enacted policies the list leaves out (arm B).** V = judged viable on either axis.')
    add('')
    for t in prop_table([('V_A, shortlist', VA), ('V_B, enacted not shortlisted (N = %d)' % N_B, VB)]):
        add(t)
    add('')
    rows = []
    for name, key_, fmt in (('Difference V_A - V_B', ('cmp', 'diff'), pts), ('Lift V_A / V_U', ('cmp', 'lift'), lambda x: num(x, 2)),
                            ('Capture (share of all viable enacted policies on the list)', ('cmp', 'capture'), pct)):
        d = reg.by_key[key_]
        rows.append([name, fmt(d['point']), ival(d['ci'][0], d['ci'][1], fmt)])
    for t in md_table(['Quantity', 'Estimate', 'Bootstrap 95%'], rows):
        add(t)
    add('')
    add('**Agreement of coders A and B (all items).**')
    add('')
    rows = []
    for f in ('did_viable', 'rd_viable'):
        s_ = agree[('all', f)]
        ci_ = agree_ci[('all', f)]
        rows.append([f, s_['n'], '%s %s' % (pct(s_['po']), ival(*ci_[0])), '%s %s' % (num(s_['kappa']), ival(*ci_[1], fmt=num)),
                     '%s %s' % (num(s_['ac1']), ival(*ci_[2], fmt=num))])
    for t in md_table(['Field', 'n', 'Agreement', 'Kappa', 'AC1'], rows):
        add(t)
    add('')
    add('Items sent to the third read: %d of %d (%s).' % (n_disputed, len(key), pct(n_disputed / len(key))))
    add('')
    add('**Anchors: the final label against the paper\'s human label (arm C, %d texts).**' % n_items['C'])
    add('')
    rows = []
    for ax in 'DR':
        s_ = anchor_out[(ax, 'Final label')]
        if s_['n']:
            rows.append([AXIS_NAME[ax], s_['n'], pct(s_['agree']), num(s_['kappa']),
                         '%s (%d/%d)' % (pct(s_['sens']), s_['tp'], s_['tp'] + s_['fn']), '%s (%d/%d)' % (pct(s_['spec']), s_['tn'], s_['tn'] + s_['fp']),
                         pct(s_['rater_yes']), pct(s_['human_yes'])])
    for t in md_table(['Axis', 'n', 'Agreement', 'Kappa', 'Sensitivity', 'Specificity', 'Coder Yes rate', 'Human Yes rate'], rows):
        add(t)

    # 7.2 tables
    add('')
    add('## 2. Shortlist by domain (Sec. 7.2)')
    for kind, title in (('PL', 'P_L: listed design viable'), ('PD', 'P_D: DiD viable among DiD-flagged units'), ('PR', 'P_R: RD viable among RD-flagged units')):
        add('')
        add('### %s' % title)
        add('')
        for t in prop_table([(n, get('final', n, kind)) for n in dom_names]):
            add(t)

    add('')
    add('## 3. Score gradient (Sec. 7.2)')
    add('')
    rows = []
    for tag, label in (('overall', 'All groups'), ('FR', 'Federal Register only (extra)'), ('SL', 'State Legislation only (extra)')):
        for diff in ('H-L', 'M-L'):
            d = reg.by_key.get(('grad', tag, diff))
            if d:
                rows.append([label, 'P_L(%s) - P_L(%s)' % tuple(diff.split('-')), pts(d['point']), ival(d['ci'][0], d['ci'][1], pts)])
    for t in md_table(['Scope', 'Difference', 'Estimate', 'Bootstrap 95%'], rows):
        add(t)
    add('')
    add('Band means (Sec. 2 tables above): L below 3.3, M 3.3 to below 3.6, H 3.6 and above. Differences carry bootstrap intervals only.')

    add('')
    add('## 4. Strict and lenient variants (Sec. 6.7, 7.2)')
    add('')
    add('Items where A and B are both non-NA only. Strict: Yes only if A and B both said Yes. Lenient: Yes if either said Yes. Third-read answers are not used here.')
    for scheme in ('strict', 'lenient'):
        for kind, title in (('PL', 'P_L'), ('PD', 'P_D'), ('PR', 'P_R')):
            add('')
            add('### %s %s' % (scheme.capitalize(), title))
            add('')
            for t in prop_table([(n, get(scheme, n, kind)) for n in short_domains]):
                add(t)

    # 7.3
    add('')
    add('## 5. Shortlist against the enacted policies it leaves out (Sec. 7.3)')
    add('')
    add('V = share of units judged viable on either axis (flag ignored). These figures describe the five screening rules together (which also drop policies for data, adoption waves and the COVID window), not the AI score alone.')
    add('')
    for t in prop_table([('V_A, shortlist (N = %d)' % N_A, VA), ('V_B, enacted not shortlisted (N = %d)' % N_B, VB)]):
        add(t)
    add('')
    rows = []
    for name, key_, fmt in (('Difference V_A - V_B', ('cmp', 'diff'), pts), ('Base rate of the enacted universe V_U', ('cmp', 'VU'), pct),
                            ('Lift V_A / V_U', ('cmp', 'lift'), lambda x: num(x, 2)),
                            ('Capture: share of all viable enacted policies the list holds', ('cmp', 'capture'), pct),
                            ('Specificity: share of non-viable enacted policies the list leaves out', ('cmp', 'specificity'), pct)):
        d = reg.by_key[key_]
        rows.append([name, fmt(d['point']), ival(d['ci'][0], d['ci'][1], fmt), '%d undefined' % d['ci'][2] if d['ci'][2] else ''])
    for t in md_table(['Quantity', 'Estimate', 'Bootstrap 95%', 'Notes'], rows):
        add(t)
    add('')
    add('V by source group inside the shortlist (extra) and inside arm B:')
    add('')
    rows = [(n, get('V', 'A', n)) for n in GROUPS_A if get('V', 'A', n)]
    rows = [('Arm A, group ' + n, r) for n, r in rows]
    rows += [('Arm B, flagged by the pipeline', b_flagged), ('Arm B, not flagged', b_unflag)]
    rows += [('Arm B, group ' + n, get('V', 'B', n)) for n in ('FR', 'SL', 'OT') if get('V', 'B', n)]
    rows += [('Arm B, stratum ' + s, get('V', 'B', s)) for s in b_strata]
    for t in prop_table(rows):
        add(t)
    d = reg.by_key[('cmp', 'B flagged - unflagged')]
    add('')
    add('Arm B, V(flagged) - V(unflagged): %s, bootstrap %s.' % (pts(d['point']), ival(d['ci'][0], d['ci'][1], pts)))
    add('')
    add('V in arm B by the screening rules a unit fails (a unit can fail several; "fails" rows overlap, "only" rows are exclusive combinations):')
    add('')
    rows = [('Fails ' + r_, get('V', 'B', 'fails ' + r_)) for r_ in rules if get('V', 'B', 'fails ' + r_)]
    rows += [('Fails exactly: ' + (c.replace('+', ', ') if c else 'none'), get('V', 'B', 'only ' + (c or 'none'))) for c in combos]
    for t in prop_table(rows):
        add(t)

    # 7.5 U shares
    add('')
    add('## 6. Unavailable and unresolved (Sec. 7.5)')
    add('')
    lab_counts = {ax: Counter(label_word(*final[i][ax]) for i in key if key[i]['arm'] in 'AB') for ax in 'DR'}
    add('Final labels over arms A and B (%d items): DiD %s; RD %s.' % (
        n_items['A'] + n_items['B'], ', '.join('%s %d' % kv for kv in sorted(lab_counts['D'].items())),
        ', '.join('%s %d' % kv for kv in sorted(lab_counts['R'].items()))))
    add('')
    add('Per-domain U share and bounds are in the "U / drawn" and "Bounds" columns of every table above. A domain with U above 20%% has its bounds interval as headline. Headline P_L: U share %s, bounds %s.'
        % (pct(PL['U'] / PL['base'], 1), ival(PL['lower'], PL['upper'])))
    add('')
    add('Text status by coder: ' + '; '.join('coder %s: %s' % (c, ', '.join('%s %d' % kv for kv in sorted(status[c].items()))) for c in 'ABC'))

    # 7.6 agreement
    add('')
    add('## 7. Agreement between coders A and B (Sec. 7.6)')
    add('')
    add('Items where both coders are non-NA on the field; unweighted; intervals are bootstrap over items (%d replicates, seed %d). '
        'Cohen\'s kappa; Gwet\'s AC1 = (p_o - p_e) / (1 - p_e) with p_e = 2 pi (1 - pi), pi the mean Yes share of the two coders. Cross-count: A Yes/B Yes, A Yes/B No, A No/B Yes, A No/B No.' % (R, seed))
    for g, title in (('all', 'All items'), ('A', 'Arm A (shortlist)'), ('B', 'Arm B (enacted, not shortlisted)'), ('C', 'Arm C (anchors)')):
        add('')
        add('### %s' % title)
        add('')
        rows = []
        for f in FIELDS8:
            s = agree[(g, f)]
            ci = agree_ci[(g, f)]
            rows.append([f, s['n'], pct(s['po']), ival(*ci[0]), num(s['kappa']), ival(*ci[1], fmt=num), num(s['ac1']), ival(*ci[2], fmt=num),
                         pct(s['pa']), pct(s['pb']), ' / '.join(str(x) for x in s['cross'])])
        for t in md_table(['Field', 'n', 'Agreement', 'Boot 95%', 'Kappa', 'Boot 95%', 'AC1', 'Boot 95%', 'A Yes rate', 'B Yes rate', 'Cross-count'], rows):
            add(t)

    add('')
    add('### Third read')
    add('')
    by_arm = Counter(key[i]['arm'] for i in disputed)
    n_d = sum(1 for d in diffs if 'did_viable' in d[1])
    n_r = sum(1 for d in diffs if 'rd_viable' in d[1])
    n_both = sum(1 for d in diffs if len(d[1]) == 2)
    add('Items sent to the third read (A and B differ on did_viable or rd_viable, NA against Yes/No counts as different): %d of %d (%s); arm A %d of %d, arm B %d of %d, arm C %d of %d. '
        'Differ on DiD %d, on RD %d, on both %d. Coder C batches: %d.'
        % (n_disputed, len(key), pct(n_disputed / len(key)), by_arm['A'], n_items['A'], by_arm['B'], n_items['B'], by_arm['C'], n_items['C'],
           n_d, n_r, n_both, len({int(r['batch']) for r in items_c})))
    side = Counter()
    for iid, fields, a, b in diffs:
        c = codes['C'][iid]
        for f in fields:
            av, bv, cv = a[f], b[f], c[f]
            if av in YN and bv in YN:
                side[(f, 'C = A' if cv == av else ('C = B' if cv == bv else 'C unavailable'))] += 1
            else:
                answer = av if av in YN else bv
                side[(f, 'one coder NA; C agrees with the other' if cv == answer else ('one coder NA; C unavailable' if cv == 'NA' else 'one coder NA; C differs'))] += 1
    rows = [[f, kind, n] for (f, kind), n in sorted(side.items())]
    add('')
    for t in md_table(['Axis', 'Where C sides', 'Items'], rows or [['-', 'no third-read items', 0]]):
        add(t)
    add('')
    add('Final-label routes (arms A and B): ' + '; '.join('%s: %s' % (AXIS_NAME[ax], ', '.join('%s %d' % kv for kv in sorted(Counter(final[i][ax][1] for i in key if key[i]['arm'] in 'AB').items()))) for ax in 'DR'))

    add('')
    add('### Drift check: Yes rate by batch range')
    add('')
    rows = []
    for f in FIELDS8:
        cells = [f]
        for letter in 'AB':
            for lo, hi in BATCH_THIRDS:
                items = [codes[letter][i] for i in key if lo <= key[i]['batch'] <= hi and codes[letter][i][f] in YN]
                k_ = sum(1 for r in items if r[f] == 'Yes')
                cells.append('%s (%d/%d)' % (pct(k_ / len(items) if items else NAN), k_, len(items)))
        rows.append(cells)
    heads = ['Field'] + ['%s b%d-%d' % (letter, lo, hi) for letter in 'AB' for lo, hi in BATCH_THIRDS]
    for t in md_table(heads, rows):
        add(t)

    # 7.7 anchors
    add('')
    add('## 8. Anchors against the paper\'s human labels (Sec. 7.7)')
    add('')
    add('Arm C, %d panel texts, unweighted, descriptive: nothing in Sections 1 to 6 is adjusted by it. Human label = the paper\'s adjudicated label. Sensitivity = coder Yes among human Yes; specificity = coder No among human No. Wilson 95%% intervals.' % n_items['C'])
    for ax in 'DR':
        add('')
        add('### %s' % AXIS_NAME[ax])
        add('')
        rows = []
        for rater in ('Coder A', 'Coder B', 'Final label'):
            s = anchor_out[(ax, rater)]
            if not s['n']:
                rows.append([rater, 0] + ['n/a'] * 7)
                continue
            rows.append([rater, s['n'], '%s %s' % (pct(s['agree']), ival(*wilson(s['agree'], s['n']))), num(s['kappa']),
                         '%s (%d/%d) %s' % (pct(s['sens']), s['tp'], s['tp'] + s['fn'], ival(*wilson(s['sens'], s['tp'] + s['fn']))),
                         '%s (%d/%d) %s' % (pct(s['spec']), s['tn'], s['tn'] + s['fp'], ival(*wilson(s['spec'], s['tn'] + s['fp']))),
                         '%s %s' % (pct(s['rater_yes']), ival(*wilson(s['rater_yes'], s['n']))),
                         '%s %s' % (pct(s['human_yes']), ival(*wilson(s['human_yes'], s['n']))),
                         'TP %d, FN %d, FP %d, TN %d' % (s['tp'], s['fn'], s['fp'], s['tn'])])
        for t in md_table(['Rater', 'n', 'Agreement', 'Kappa', 'Sensitivity', 'Specificity', 'Rater Yes rate', 'Human Yes rate', 'Counts'], rows):
            add(t)
    add('')
    add('Unusable anchor labels (rater NA or U) are left out of n. The anchor sample is stratified by the human RD label (10 RD-Yes of 32, 20 RD-No of 123), so its Yes rates are sample rates, not panel rates.')

    # 7.8 description
    add('')
    add('## 9. Description (Sec. 7.8)')
    add('')
    link = {}
    link_path = key_dir / 'LINK_CHECK.csv'
    if link_path.exists():
        link = {r['item_id']: r for r in sa.read_csv(link_path)[0]}

    def grp(iid):
        k = key[iid]
        return 'C anchors' if k['arm'] == 'C' else '%s-%s' % (k['arm'], k['group'])

    add('### Partial or unavailable text, by group')
    add('')
    glist = sorted({grp(i) for i in key})
    rows = []
    for gname in glist:
        ids = [i for i in key if grp(i) == gname]
        cells = [gname, len(ids)]
        for letter in 'AB':
            cnt = Counter(codes[letter][i]['text_status'] for i in ids)
            cells += ['%s (%d)' % (pct(cnt['partial'] / len(ids)), cnt['partial']), '%s (%d)' % (pct(cnt['unavailable'] / len(ids)), cnt['unavailable'])]
        rows.append(cells)
    for t in md_table(['Group', 'Items', 'A partial', 'A unavailable', 'B partial', 'B unavailable'], rows):
        add(t)
    add('')
    add('### Partial or unavailable text, by link-check result (information only, Sec. 6.2)')
    add('')
    if link:
        cats = sorted({link_category(link[i]) for i in key if i in link})
        rows = []
        for c in cats:
            ids = [i for i in key if i in link and link_category(link[i]) == c]
            cells = [c, len(ids)]
            for letter in 'AB':
                cnt = Counter(codes[letter][i]['text_status'] for i in ids)
                cells += ['%s (%d)' % (pct(cnt['partial'] / len(ids)), cnt['partial']), '%s (%d)' % (pct(cnt['unavailable'] / len(ids)), cnt['unavailable'])]
            rows.append(cells)
        for t in md_table(['Link check', 'Items', 'A partial', 'A unavailable', 'B partial', 'B unavailable'], rows):
            add(t)
    else:
        add('LINK_CHECK.csv not found.')
    add('')
    add('### Component that fails most often when a flagged axis is judged No (arm A)')
    add('')
    add('Per source group and axis: the coder sessions (A and B) whose own viable answer is No, on units where the pipeline flagged the axis and the final label is No. Cells are the share of those sessions answering No on the component.')
    add('')
    rows = []
    for gname in GROUPS_A:
        for ax in 'DR':
            field, comps = AXES[ax]
            sess = []
            for i in key:
                k = key[i]
                if k['arm'] != 'A' or k['group'] != gname or not (k['did' if ax == 'D' else 'rd'] == '1') or final[i][ax][0] != 0:
                    continue
                for letter in 'AB':
                    r_ = codes[letter][i]
                    if r_['text_status'] != 'unavailable' and r_[field] == 'No':
                        sess.append(r_)
            if not sess:
                rows.append([gname, AXIS_NAME[ax], 0, '-', '-', '-', '-'])
                continue
            shares = [sum(1 for r_ in sess if r_[c] == 'No') / len(sess) for c in comps]
            top = [c for c, s_ in zip(comps, shares) if s_ == max(shares)]
            rows.append([gname, AXIS_NAME[ax], len(sess)] + [pct(s_) for s_ in shares] + [', '.join(top)])
    for t in md_table(['Group', 'Axis', 'Sessions', 'Component 1 No (treated / running variable)', 'Component 2 No (comparison / cutoff)', 'Component 3 No (timing / assignment)', 'Fails most often'], rows):
        add(t)
    add('')
    add('### Version read')
    add('')
    rows = []
    for letter in 'ABC':
        cnt = Counter(codes[letter][i]['version_read'] for i in codes[letter])
        rows.append(['Coder ' + letter, len(codes[letter])] + [cnt.get(v, 0) for v in spec['enums']['version_read']])
    for t in md_table(['Coder', 'Items'] + spec['enums']['version_read'], rows):
        add(t)
    add('')
    src_rows = []
    for letter in 'ABC':
        cnt = Counter(codes[letter][i]['source_used'] for i in codes[letter])
        src_rows.append(['Coder ' + letter] + [cnt.get(v, 0) for v in spec['enums']['source_used']])
    for t in md_table(['Source used'] + spec['enums']['source_used'], src_rows):
        add(t)
    add('')
    add('### Coder sessions and contaminated batches (Sec. 5.5)')
    add('')
    if session_rows is None:
        add('No session log was available.')
    else:
        att = Counter((r['coder'], r['batch']) for r in session_rows)
        extra = [r for r in session_rows if int(r['attempt']) > 1 or r['status'] != 'clean']
        add('Logged coder sessions: %d for %d coder batches; sessions marked contaminated: %d; batches coded more than once: %d.'
            % (len(session_rows), len(att), sum(1 for r in session_rows if r['status'] != 'clean'), sum(1 for v in att.values() if v > 1)))
        if extra:
            add('')
            for t in md_table(['Coder', 'Batch', 'Attempt', 'Status', 'Paths outside the packet folder', 'Note'],
                              [[r['coder'], r['batch'], r['attempt'], r['status'], r['paths_outside_packet'] or '-', r['note'] or '-'] for r in extra]):
                add(t)
        models = Counter(r['model'] for r in session_rows)
        add('')
        add('Models seen in the coder sessions: ' + ', '.join('%s (%d)' % kv for kv in sorted(models.items())) + '.')
    excl = key_dir / 'EXCLUDED_FILES.tsv'
    if excl.exists():
        add('')
        add('Files set aside and kept (never deleted):')
        add('')
        for line in excl.read_text(encoding='utf-8').strip().split('\n'):
            add('- ' + line.replace('\t', ' | '))

    add('')
    add('## 10. Files')
    add('')
    add('- Final labels: `%s` (sha256 `%s`).' % (out_labels.name, '{LABELS_SHA}'))
    add('- Coder files (sha256 in `LOCK_AB.json`, locked %s): %d from coders A and B, %d from coder C.' % (lock['locked_at_utc'], len(lock['files']), len(hashes_c)))
    add('- Key: `KEY.csv` sha256 `%s`.' % sa.sha256_file(key_dir / 'KEY.csv'))
    add('- Analysis script sha256 at the lock: `%s`.' % lock['analysis_script_sha256'])

    # ---- final labels file -----------------------------------------------------------------------------------------------
    cols = ['item_id', 'arm', 'stratum', 'group', 'band', 'flag', 'weight', 'batch', 'A_text_status', 'B_text_status', 'C_text_status',
            'A_did_viable', 'B_did_viable', 'C_did_viable', 'final_did', 'final_did_route',
            'A_rd_viable', 'B_rd_viable', 'C_rd_viable', 'final_rd', 'final_rd_route', 'g_listed_design', 'v_either_axis', 'human_did', 'human_rd']
    pos = {u['id']: n for n, u in enumerate(units)}
    label_rows = []
    for iid in sorted(key):
        k = key[iid]
        c = codes['C'].get(iid)
        row = {'item_id': iid, 'arm': k['arm'], 'stratum': k['stratum'], 'group': k['group'], 'band': k['band'], 'flag': k['flag'],
               'weight': '%.6f' % k['w'], 'batch': k['batch'], 'A_text_status': codes['A'][iid]['text_status'],
               'B_text_status': codes['B'][iid]['text_status'], 'C_text_status': c['text_status'] if c else '',
               'human_did': k['human_did'], 'human_rd': k['human_rd']}
        for ax, tag in (('D', 'did'), ('R', 'rd')):
            field = AXES[ax][0]
            row['A_' + field] = codes['A'][iid][field]
            row['B_' + field] = codes['B'][iid][field]
            row['C_' + field] = c[field] if c else ''
            row['final_' + tag] = label_word(*final[iid][ax])
            row['final_%s_route' % tag] = final[iid][ax][1]
        if iid in pos:
            n_ = pos[iid]
            g_, v_ = LAB['final']['g'][n_], LAB['final']['v'][n_]
            row['g_listed_design'] = 'U' if g_ is None else g_
            row['v_either_axis'] = 'U' if v_ is None else v_
        label_rows.append(row)
    import io
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(cols)
    for row in label_rows:
        writer.writerow([row.get(c, '') for c in cols])
    labels_text = buf.getvalue()
    import hashlib
    labels_sha = hashlib.sha256(labels_text.encode('utf-8')).hexdigest()
    report = '\n'.join(L).replace('{LABELS_SHA}', labels_sha) + '\n'
    sa.write_text_new(out_labels, labels_text)
    sa.write_text_new(out_md, report)

    # ---- console summary ---------------------------------------------------------------------------------------------------
    def line(name, rec):
        wlo, whi = wilson(rec['point'], rec['neff'])
        return '%s: %s boot %s Wilson %s k/n %d/%d U %d/%d bounds %s' % (name, pct(rec['point']), ival(*rec['ci'][:2]), ival(wlo, whi),
                                                                    rec['k'], rec['n'], rec['U'], rec['base'], ival(rec['lower'], rec['upper']))
    print(line('P_L', PL))
    print(line('P_D', PD))
    print(line('P_R', PR))
    print(line('V_A', VA))
    print(line('V_B', VB))
    for name, key_ in (('diff', ('cmp', 'diff')), ('V_U', ('cmp', 'VU')), ('lift', ('cmp', 'lift')), ('capture', ('cmp', 'capture')), ('specificity', ('cmp', 'specificity'))):
        d = reg.by_key[key_]
        print('%s: %s boot %s' % (name, num(d['point'], 4), ival(d['ci'][0], d['ci'][1], lambda x: num(x, 4))))
    for f in ('did_viable', 'rd_viable'):
        s = agree[('all', f)]
        print('agreement %s: n %d, %s, kappa %s, AC1 %s' % (f, s['n'], pct(s['po']), num(s['kappa']), num(s['ac1'])))
    print('third read: %d of %d items' % (n_disputed, len(key)))
    for ax in 'DR':
        for rater in ('Coder A', 'Coder B', 'Final label'):
            s = anchor_out[(ax, rater)]
            if s['n']:
                print('anchors %s %s: n %d agree %s kappa %s sens %s spec %s coderYes %s humanYes %s' % (
                    AXIS_NAME[ax], rater, s['n'], pct(s['agree']), num(s['kappa']), pct(s['sens']), pct(s['spec']), pct(s['rater_yes']), pct(s['human_yes'])))
    print('wrote', out_md, 'and', out_labels)


# ----------------------------------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--protocol', default=str(sa.PROTOCOL))
    common.add_argument('--key-dir', default=str(sa.KEY_DIR))
    common.add_argument('--packet-dir', default=str(sa.PACKET_DIR))
    common.add_argument('--session-log')
    p = sub.add_parser('lock', parents=[common])
    p.set_defaults(fn=cmd_lock)
    p = sub.add_parser('analyze', parents=[common])
    p.add_argument('--out-md', default=str(sa.ROOT / 'docs/plans/l1/BLIND_AUDIT_V1.md'))
    p.add_argument('--out-labels')
    p.add_argument('--notes-file')
    p.add_argument('--replicates', type=int)
    p.add_argument('--seed', type=int)
    p.add_argument('--freeze')
    p.set_defaults(fn=cmd_analyze)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == '__main__':
    main()
