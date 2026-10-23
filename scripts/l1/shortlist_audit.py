#!/usr/bin/env python3
"""L1 work package C, part 1: blind audit of the 7,781-candidate shortlist (frames, freeze, seeded draw, coder packets).

READ-ONLY on the policy table, the derived views and the paper's panel files. Writes only new files:
  key directory     data/pipeline/shortlist_audit_v1_key/   frames/, FREEZE.json, KEY.csv, DRAW_MANIFEST.json, (later) LINK_CHECK.csv
  packet directory  data/pipeline/shortlist_audit_v1/       coder_sheet.csv, coder_A/, coder_B/ (and coder_C/ after the third-read step)
Protocol (hashed before any draw): docs/plans/l1/SHORTLIST_AUDIT_PROTOCOL_20261016.md. The draw reads its parameters
(seed, strata, sample sizes, frame hashes, output columns, coder instructions) from the protocol's machine-readable blocks, so the
sample is a function of the hashed text, the frames and nothing else.

Subcommands
  frame      build frames A (shortlist), B (enacted, not shortlisted), C (anchor panel) + FRAME_MANIFEST.json   [no sampling]
  freeze     write FREEZE.json (protocol hash, script hash, frame hashes) and the protocol's .sha256 sidecar    [before the draw]
  draw       verify the freeze, draw exactly as the protocol says, write key, sheets and the two coder packets
  validate   check one coder output file against the frozen field list
  third      build the third-read packet from the items on which coders A and B differ
  linkcheck  one polite GET per sampled document_url; information only, no item is ever replaced

Stdlib only, Python 3.9+.   Usage:  python3 -B scripts/l1/shortlist_audit.py <subcommand> [options]
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
PAPER = Path('../NaturalExperimentPolicyTracker-paper')
POLICY_CSV = ROOT / 'data/output/policy_db_20260415_483k.csv'
DERIVED_CSV = ROOT / 'data/output/derived/derived_views.csv'
PANEL_DIR = PAPER / 'research/fresh_eval'
PROTOCOL = ROOT / 'docs/plans/l1/SHORTLIST_AUDIT_PROTOCOL_20261016.md'
KEY_DIR = ROOT / 'data/pipeline/shortlist_audit_v1_key'
PACKET_DIR = ROOT / 'data/pipeline/shortlist_audit_v1'

csv.field_size_limit(2 ** 31 - 1)

# ----------------------------------------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------------------------------------

STATE_NAMES = dict(pair.split(':') for pair in (
    'AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|'
    'CT:Connecticut|DE:Delaware|DC:District of Columbia|FL:Florida|GA:Georgia|'
    'HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|'
    'LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|'
    'MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|'
    'NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|'
    'OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|'
    'SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|'
    'WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming'
).split('|'))


def public_jurisdiction(value):
    """Same spelling rule as scripts/viz_site/restructure_screening.public_jurisdiction (a test keeps them equal)."""
    value = re.sub(r' State$', '', value or '')
    return STATE_NAMES.get(value, value)


_CRED_NAMES = {'key', 'auth', 'authorization', 'email', 'mail', 'user', 'username', 'userid', 'user_id', 'sig', 'pwd'}
_CRED_PARTS = ('apikey', 'api_key', 'api-key', 'token', 'secret', 'password', 'passwd', 'signature', 'credential')


def is_credential_param(name):
    n = name.lower()
    return n in _CRED_NAMES or any(p in n for p in _CRED_PARTS)


def safe_public_url(value):
    """Keep the document's own address, drop credentials and API keys. Unlike the Screening page's safe_url this keeps
    legislative session parameters (Session=, SessionID=, txtsessionyear= ...), without which many state links break."""
    try:
        url = urlsplit((value or '').strip())
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
            return ''
        parts = [p for p in url.query.split('&') if p and not is_credential_param(p.split('=', 1)[0])]
        return urlunsplit((url.scheme, url.netloc, url.path, '&'.join(parts), ''))
    except ValueError:
        return ''


def constructed_fr_url(policy_id):
    """A Federal Register item without a stored address: its public short address is built from the document number."""
    match = re.fullmatch(r'fedreg-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)', policy_id or '')
    return 'https://www.federalregister.gov/d/' + match.group(1) if match else ''


def clean(value):
    return ' '.join(str(value or '').split())


def band_of(score):
    """AI design score band: L below 3.3, M from 3.3 to below 3.6, H from 3.6 up."""
    return 'L' if score < 3.3 else ('M' if score < 3.6 else 'H')


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            block = stream.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_csv_new(path, columns, rows):
    """Create a new file (never overwrite), UTF-8, LF line ends."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'x', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, '') for c in columns])


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        return list(reader), reader.fieldnames


def write_text_new(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'x', encoding='utf-8', newline='') as stream:
        stream.write(text)


# ----------------------------------------------------------------------------------------------------------------------
# Protocol blocks
# ----------------------------------------------------------------------------------------------------------------------

def read_spec(protocol_path):
    text = Path(protocol_path).read_text(encoding='utf-8')
    match = re.search(r'<!-- DRAW_SPEC_BEGIN -->\s*```json\n(.*?)\n```\s*<!-- DRAW_SPEC_END -->', text, re.S)
    if not match:
        raise SystemExit('DRAW_SPEC block not found in ' + str(protocol_path))
    return json.loads(match.group(1))


def read_instructions(protocol_path):
    text = Path(protocol_path).read_text(encoding='utf-8')
    match = re.search(r'<!-- CODER_INSTRUCTIONS_BEGIN -->\n(.*?)\n<!-- CODER_INSTRUCTIONS_END -->', text, re.S)
    if not match:
        raise SystemExit('CODER_INSTRUCTIONS block not found in ' + str(protocol_path))
    return match.group(1) + '\n'


# ----------------------------------------------------------------------------------------------------------------------
# Frames
# ----------------------------------------------------------------------------------------------------------------------

FRAME_COLUMNS = ['unit_key', 'api_id', 'source_family', 'source', 'group', 'title', 'jurisdiction', 'date', 'year',
                 'document_url', 'url_constructed', 'fulltext_url', 'composite_score', 'band', 'did', 'rd', 'flag',
                 'flagged', 'best_design', 'min_gate', 'wave_flag', 'confound_flags', 'data_verdict', 'gates_failed',
                 'orphan', 'stratum']
ANCHOR_COLUMNS = ['unit_key', 'eval_id', 'title', 'jurisdiction', 'date', 'document_url', 'human_did', 'human_rd',
                  'human_source_did', 'human_source_rd', 'origin', 'stratum']


def group_of_A(source_family, source):
    if source == 'Regulations.gov':
        return 'RG'
    if source_family == 'Federal Regulations':
        return 'FR'
    if source_family == 'State Legislation':
        return 'SL'
    return 'OT'


def stratum_A(group, band, flag):
    """Arm A (the shortlist): Federal Register and State Legislation by band and flag, Regulations.gov by flag, rest one."""
    if group == 'OT':
        return 'A-OT'
    if group == 'RG':
        return 'A-RG-' + flag
    return 'A-%s-%s-%s' % (group, band, flag)


def stratum_B(source_family, flagged):
    """Arm B (enacted, not shortlisted): Federal regulations and State legislation by pipeline flag, rest one."""
    if source_family == 'Federal Regulations':
        return 'B-FR-' + ('F' if flagged else 'U')
    if source_family == 'State Legislation':
        return 'B-SL-' + ('F' if flagged else 'U')
    return 'B-OT'


def gates_failed(derived_row):
    failed = []
    if derived_row['wave_flag'] != '0':
        failed.append('wave')
    if 'covid_window' in (derived_row['confound_flags'] or ''):
        failed.append('covid')
    if derived_row['data_verdict'] not in ('public_ok', 'mixed'):
        failed.append('data')
    try:
        if float(derived_row['min_gate'] or 0) < 3:
            failed.append('min_gate')
    except ValueError:
        failed.append('min_gate')
    return '|'.join(failed)


def build_frames_AB():
    derived = {}
    with open(DERIVED_CSV, newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            derived.setdefault(row['policy_id'], row)
    rows_A, rows_B, seen = [], [], set()
    with open(POLICY_CSV, newline='', encoding='utf-8', errors='replace') as stream:
        for row in csv.DictReader(stream):
            key = row['policy_id']
            if key in seen:
                continue
            seen.add(key)
            d = derived.get(key)
            if d is None or d['strict_enacted'] != '1':
                continue
            in_A = d['viable_strict'] == '1'
            raw_url = row['url'] or ''
            detail = {}
            if in_A or not raw_url:
                try:
                    detail = json.loads(row.get('detail_json') or '{}')
                except ValueError:
                    detail = {}
            document_url = safe_public_url(raw_url or detail.get('document_url'))
            constructed = '0'
            if not document_url:
                document_url = constructed_fr_url(key)
                constructed = '1' if document_url else '0'
            try:
                score = float(row['composite_score'] or 0)
            except ValueError:
                score = 0.0
            did, rd = row['did'], row['rd']
            flagged = did == '1' or rd == '1'
            flag = 'R' if rd == '1' else ('D' if did == '1' else 'N')
            family, source = row['source_family'], row['source']
            out = {
                'unit_key': key, 'api_id': row['id'], 'source_family': family, 'source': source,
                'group': group_of_A(family, source) if in_A else ('FR' if family == 'Federal Regulations' else 'SL' if family == 'State Legislation' else 'OT'),
                'title': clean(row['title']), 'jurisdiction': public_jurisdiction(clean(row['jurisdiction'])),
                'date': clean(row['date']), 'year': clean(row['year']),
                'document_url': document_url, 'url_constructed': constructed,
                'fulltext_url': safe_public_url(detail.get('fulltext_url')) if in_A else '',
                'composite_score': row['composite_score'], 'band': band_of(score), 'did': did, 'rd': rd, 'flag': flag,
                'flagged': '1' if flagged else '0', 'best_design': row['best_design'],
                'min_gate': d['min_gate'], 'wave_flag': d['wave_flag'], 'confound_flags': d['confound_flags'],
                'data_verdict': d['data_verdict'], 'gates_failed': '' if in_A else gates_failed(d),
                'orphan': '1' if (family == 'State Legislation' and not clean(row['date'])) else '0',
            }
            if in_A:
                if flag == 'N':
                    raise SystemExit('shortlisted unit without a design flag: ' + key)
                out['stratum'] = stratum_A(out['group'], out['band'], flag)
                rows_A.append(out)
            else:
                out['stratum'] = stratum_B(family, flagged)
                rows_B.append(out)
    rows_A.sort(key=lambda r: r['unit_key'])
    rows_B.sort(key=lambda r: r['unit_key'])
    return rows_A, rows_B


def build_frame_C():
    truth = {}
    for line in (PANEL_DIR / 'round2_panel_truth.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            d = json.loads(line)
            truth[int(d['eval_id'])] = d
    origin = {}
    with open(PANEL_DIR / 'ra_template_key.csv', newline='', encoding='utf-8-sig') as stream:
        for r in csv.DictReader(stream):
            origin[int(r['eval_id'])] = r['origin']
    template = {}
    with open(PANEL_DIR / 'ra_response_template.csv', newline='', encoding='utf-8-sig') as stream:
        for r in csv.DictReader(stream):
            template[int(r['eval_id'])] = r
    rows = []
    for eid, d in sorted(truth.items()):
        kind = origin.get(eid, 'supplement')
        if kind == 'calibration' or d.get('DID') is None or d.get('RD') is None or eid not in template:
            continue
        t = template[eid]
        url = safe_public_url(t['document_url'])
        if not url or ' ' in t['document_url'].strip():
            continue
        rows.append({
            'unit_key': 'panel-%03d' % eid, 'eval_id': str(eid), 'title': clean(t['title']),
            'jurisdiction': public_jurisdiction(clean(t['jurisdiction'])), 'date': clean(t['introduced_date']),
            'document_url': url, 'human_did': '1' if d['DID'] else '0', 'human_rd': '1' if d['RD'] else '0',
            'human_source_did': d.get('DID_source', ''), 'human_source_rd': d.get('RD_source', ''), 'origin': kind,
            'stratum': 'C-RD1' if d['RD'] else 'C-RD0'})
    rows.sort(key=lambda r: r['unit_key'])
    return rows


def cmd_frame(args):
    key_dir = Path(args.key_dir)
    frames = key_dir / 'frames'
    A, B = build_frames_AB()
    C = build_frame_C()
    files = {'A': 'frame_A_shortlist.csv', 'B': 'frame_B_enacted_not_shortlisted.csv', 'C': 'frame_C_anchor_panel.csv'}
    write_csv_new(frames / files['A'], FRAME_COLUMNS, A)
    write_csv_new(frames / files['B'], FRAME_COLUMNS, B)
    write_csv_new(frames / files['C'], ANCHOR_COLUMNS, C)
    manifest = {
        'built_at_utc': utc_now(),
        'script': 'scripts/l1/shortlist_audit.py', 'script_sha256': sha256_file(__file__),
        'sources': {
            'policy_csv': {'path': str(POLICY_CSV.relative_to(ROOT)), 'sha256': sha256_file(POLICY_CSV)},
            'derived_views': {'path': str(DERIVED_CSV.relative_to(ROOT)), 'sha256': sha256_file(DERIVED_CSV)},
            'panel_truth': {'path': str(PANEL_DIR / 'round2_panel_truth.jsonl'), 'sha256': sha256_file(PANEL_DIR / 'round2_panel_truth.jsonl')},
            'panel_template': {'path': str(PANEL_DIR / 'ra_response_template.csv'), 'sha256': sha256_file(PANEL_DIR / 'ra_response_template.csv')},
            'panel_key': {'path': str(PANEL_DIR / 'ra_template_key.csv'), 'sha256': sha256_file(PANEL_DIR / 'ra_template_key.csv')},
        },
        'frames': {arm: {'file': files[arm], 'sha256': sha256_file(frames / files[arm]), 'rows': n,
                         'strata': dict(sorted(Counter(r['stratum'] for r in rows).items()))}
                   for arm, rows, n in (('A', A, len(A)), ('B', B, len(B)), ('C', C, len(C)))},
        'notes': {'A_url_constructed': sum(r['url_constructed'] == '1' for r in A),
                  'B_url_constructed': sum(r['url_constructed'] == '1' for r in B),
                  'A_blank_url': sum(not r['document_url'] for r in A), 'B_blank_url': sum(not r['document_url'] for r in B),
                  'A_orphans': sum(r['orphan'] == '1' for r in A), 'B_orphans': sum(r['orphan'] == '1' for r in B)},
    }
    write_text_new(frames / 'FRAME_MANIFEST.json', json.dumps(manifest, indent=1, sort_keys=False) + '\n')
    print(json.dumps({arm: {'rows': v['rows'], 'sha256': v['sha256'], 'strata': v['strata']} for arm, v in manifest['frames'].items()}, indent=1))
    print(json.dumps(manifest['notes']))


# ----------------------------------------------------------------------------------------------------------------------
# Freeze
# ----------------------------------------------------------------------------------------------------------------------

def cmd_freeze(args):
    protocol, key_dir = Path(args.protocol), Path(args.key_dir)
    spec = read_spec(protocol)
    manifest = json.loads((key_dir / 'frames/FRAME_MANIFEST.json').read_text(encoding='utf-8'))
    for arm in 'ABC':
        if spec['arms'][arm]['frame_sha256'] != manifest['frames'][arm]['sha256']:
            raise SystemExit('protocol frame hash differs from the frame on disk: arm ' + arm)
        for stratum, cell in spec['arms'][arm]['strata'].items():
            if manifest['frames'][arm]['strata'].get(stratum) != cell['N']:
                raise SystemExit('protocol stratum size differs from the frame: ' + stratum)
        if sum(manifest['frames'][arm]['strata'].values()) != sum(c['N'] for c in spec['arms'][arm]['strata'].values()):
            raise SystemExit('protocol strata do not cover the frame: arm ' + arm)
    record = {
        'statement': 'Written before the sample was drawn. No draw with the registered seed existed when this file was made.',
        'frozen_at_utc': utc_now(),
        'protocol': str(protocol.relative_to(ROOT)) if protocol.is_absolute() and ROOT in protocol.parents else str(protocol),
        'protocol_sha256': sha256_file(protocol),
        'script': 'scripts/l1/shortlist_audit.py', 'script_sha256': sha256_file(__file__),
        'frames': {arm: manifest['frames'][arm]['sha256'] for arm in 'ABC'},
        'frame_manifest_sha256': sha256_file(key_dir / 'frames/FRAME_MANIFEST.json'),
        'seed': spec['seed'],
    }
    write_text_new(key_dir / args.freeze_name, json.dumps(record, indent=1) + '\n')
    sidecar = Path(str(protocol) + '.sha256')
    write_text_new(sidecar, '%s  %s\n' % (record['protocol_sha256'], protocol.name))
    print(json.dumps(record, indent=1))


# ----------------------------------------------------------------------------------------------------------------------
# Draw
# ----------------------------------------------------------------------------------------------------------------------

ID_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
VISIBLE_COLUMNS = ['item_id', 'title', 'jurisdiction', 'document_date', 'document_url']
ITEM_COLUMNS = ['batch', 'item_id', 'title', 'jurisdiction', 'document_date', 'document_url']
KEY_COLUMNS = ['item_id', 'arm', 'stratum', 'N_h', 'n_h', 'weight', 'batch', 'pos_A', 'pos_B', 'unit_key', 'api_id',
               'source_family', 'source', 'group', 'title', 'jurisdiction', 'date', 'document_url', 'url_constructed',
               'fulltext_url', 'composite_score', 'band', 'did', 'rd', 'flag', 'flagged', 'best_design', 'min_gate',
               'wave_flag', 'confound_flags', 'data_verdict', 'gates_failed', 'orphan', 'eval_id', 'human_did',
               'human_rd', 'human_source_did', 'human_source_rd']
FORBIDDEN_IN_RELEASE = ['composite', 'min_gate', 'stratum', 'weight', 'best_design', 'intuition', 'shortlist', 'policy_id',
                        'viable_strict', 'api_key', 'pipeline', 'llm', 'glm', 'claude', 'sonnet', 'haiku', 'precision']


def rank_key(seed, stratum, unit_key):
    return hashlib.sha256(('%s|draw|%s|%s' % (seed, stratum, unit_key)).encode('utf-8')).hexdigest()


def make_id(seed, unit_key, length):
    num = int.from_bytes(hashlib.sha256(('%s|id|%s' % (seed, unit_key)).encode('utf-8')).digest()[:8], 'big')
    chars = []
    for _ in range(length):
        chars.append(ID_ALPHABET[num & 31])
        num >>= 5
    return ''.join(chars)


def unique_ids(seed, unit_keys, start_length=6):
    for length in range(start_length, 13):
        ids = {k: make_id(seed, k, length) for k in unit_keys}
        if len(set(ids.values())) == len(ids):
            return ids
    raise SystemExit('could not make unique item ids')


def draw_stratum(seed, stratum, units, n):
    """Simple random sample without replacement: the n units with the smallest sha256(seed|draw|stratum|unit_key)."""
    return sorted(units, key=lambda u: rank_key(seed, stratum, u['unit_key']))[:n]


def load_frame(frames_dir, arm_spec):
    path = Path(frames_dir) / arm_spec['frame']
    rows, _ = read_csv(path)
    return rows


def cmd_draw(args):
    protocol, key_dir, packet_dir = Path(args.protocol), Path(args.key_dir), Path(args.packet_dir)
    freeze_path = Path(args.freeze) if args.freeze else key_dir / 'FREEZE.json'
    spec = read_spec(protocol)
    freeze = json.loads(freeze_path.read_text(encoding='utf-8'))
    if sha256_file(protocol) != freeze['protocol_sha256']:
        raise SystemExit('REFUSED: the protocol on disk is not the frozen protocol (hash differs)')
    frames_dir = Path(args.frames_dir) if args.frames_dir else key_dir / 'frames'
    seed, batch_size = spec['seed'], spec['batch_size']
    selected, key_rows = [], []
    for arm in 'ABC':
        arm_spec = spec['arms'][arm]
        frame_path = frames_dir / arm_spec['frame']
        if sha256_file(frame_path) != arm_spec['frame_sha256'] or arm_spec['frame_sha256'] != freeze['frames'][arm]:
            raise SystemExit('REFUSED: frame %s differs from the frozen frame' % arm)
        rows, _ = read_csv(frame_path)
        by_stratum = defaultdict(list)
        for r in rows:
            by_stratum[r['stratum']].append(r)
        if set(by_stratum) != set(arm_spec['strata']):
            raise SystemExit('REFUSED: strata of frame %s differ from the protocol' % arm)
        for stratum, cell in arm_spec['strata'].items():
            units = by_stratum[stratum]
            if len(units) != cell['N'] or not 0 < cell['n'] <= cell['N']:
                raise SystemExit('REFUSED: stratum %s has %d units, protocol says %d' % (stratum, len(units), cell['N']))
            for u in draw_stratum(seed, stratum, units, cell['n']):
                selected.append((arm, stratum, cell, u))
    keys = [u['unit_key'] for _, _, _, u in selected]
    if len(set(keys)) != len(keys):
        raise SystemExit('a unit was drawn twice')
    if len(selected) != spec['total_n']:
        raise SystemExit('drawn %d units, protocol says %d' % (len(selected), spec['total_n']))
    ids = unique_ids(seed, keys, spec['id_length'])
    by_id = {}
    for arm, stratum, cell, u in selected:
        item_id = ids[u['unit_key']]
        row = {c: u.get(c, '') for c in KEY_COLUMNS}
        row.update({'item_id': item_id, 'arm': arm, 'stratum': stratum, 'N_h': cell['N'], 'n_h': cell['n'],
                    'weight': '%.6f' % (cell['N'] / cell['n']), 'document_url': u['document_url'], 'date': u['date']})
        by_id[item_id] = row
    order_batch = sorted(by_id, key=lambda i: hashlib.sha256(('%s|batch|%s' % (seed, i)).encode('utf-8')).hexdigest())
    for pos, item_id in enumerate(order_batch):
        by_id[item_id]['batch'] = pos // batch_size + 1
    n_batches = (len(order_batch) + batch_size - 1) // batch_size

    def coder_order(letter):
        return sorted(by_id, key=lambda i: (by_id[i]['batch'], hashlib.sha256(('%s|order%s|%s' % (seed, letter, i)).encode('utf-8')).hexdigest()))

    orders = {c: coder_order(c) for c in 'AB'}
    for c in 'AB':
        for pos, item_id in enumerate(orders[c], start=1):
            by_id[item_id]['pos_' + c] = pos

    def visible(row):
        return {'item_id': row['item_id'], 'title': row['title'], 'jurisdiction': row['jurisdiction'],
                'document_date': row['date'], 'document_url': row['document_url'], 'batch': row['batch']}

    instructions = read_instructions(protocol)
    written = []
    # key (hidden)
    key_out = sorted(by_id.values(), key=lambda r: r['item_id'])
    write_csv_new(key_dir / 'KEY.csv', KEY_COLUMNS, key_out)
    written.append(key_dir / 'KEY.csv')
    # master coder sheet: visible fields only, sorted by the random item id
    write_csv_new(packet_dir / 'coder_sheet.csv', VISIBLE_COLUMNS, [visible(r) for r in key_out])
    written.append(packet_dir / 'coder_sheet.csv')
    header = spec['columns']
    for c in 'AB':
        folder = packet_dir / ('coder_' + c)
        write_csv_new(folder / ('items_%s.csv' % c), ITEM_COLUMNS, [visible(by_id[i]) for i in orders[c]])
        write_text_new(folder / 'INSTRUCTIONS.md', instructions.replace('{CODER}', c))
        write_csv_new(folder / 'codes_template.csv', header, [])
        (folder / 'out').mkdir(parents=True, exist_ok=True)
        written += [folder / ('items_%s.csv' % c), folder / 'INSTRUCTIONS.md', folder / 'codes_template.csv']
    problems = leak_check(packet_dir, key_out, written, spec)
    manifest = {
        'drawn_at_utc': utc_now(), 'protocol_sha256': freeze['protocol_sha256'], 'script_sha256': sha256_file(__file__),
        'seed': seed, 'total_n': len(by_id), 'batches': n_batches, 'batch_size': batch_size,
        'arm_n': dict(Counter(r['arm'] for r in key_out)),
        'strata': {arm: {s: {'N': c['N'], 'n': c['n'], 'weight': round(c['N'] / c['n'], 6)} for s, c in spec['arms'][arm]['strata'].items()} for arm in 'ABC'},
        'files': {str(p.relative_to(ROOT)) if ROOT in p.parents else str(p): sha256_file(p) for p in written},
        'leak_check': problems or 'clean',
    }
    write_text_new(key_dir / 'DRAW_MANIFEST.json', json.dumps(manifest, indent=1) + '\n')
    print(json.dumps({k: manifest[k] for k in ('drawn_at_utc', 'total_n', 'batches', 'arm_n', 'leak_check')}, indent=1))
    for p in written:
        print(sha256_file(p)[:16], p)
    if problems:
        raise SystemExit('LEAK CHECK FAILED: ' + '; '.join(problems))


def leak_check(packet_dir, key_rows, files, spec):
    """Nothing but the allowed fields, no unit key, no banned word, no credential in anything coders can open."""
    problems = []
    unit_keys = [r['unit_key'] for r in key_rows if len(r['unit_key']) >= 8 and not r['unit_key'].isdigit()]
    for path in files:
        if path.name == 'KEY.csv':
            continue
        text = Path(path).read_text(encoding='utf-8')
        low = text.lower()
        if path.name == 'coder_sheet.csv' and text.splitlines()[0].split(',') != VISIBLE_COLUMNS:
            problems.append('%s: header' % path.name)
        if path.name.startswith('items_') and text.splitlines()[0].split(',') != ITEM_COLUMNS:
            problems.append('%s: header' % path.name)
        if path.name == 'INSTRUCTIONS.md':
            # titles of real documents may contain any word; only the text we wrote is checked for hints about the audit
            for word in FORBIDDEN_IN_RELEASE:
                if re.search(r'(?<![a-z])%s(?![a-z])' % re.escape(word), low):
                    problems.append('%s: contains %r' % (path.name, word))
        for key in unit_keys:
            if key.lower() in low:
                problems.append('%s: contains a policy id' % path.name)
                break
        if re.search(r'[?&](api_?key|token|access_token|secret|password)=', low):
            problems.append('%s: credential-like query' % path.name)
    return problems


# ----------------------------------------------------------------------------------------------------------------------
# Validate coder output
# ----------------------------------------------------------------------------------------------------------------------

def validate_rows(header, rows, spec, expected_ids=None):
    errors = []
    columns, enums = spec['columns'], spec['enums']
    if header != columns:
        return ['header differs from the frozen field list']
    seen = set()
    for n, row in enumerate(rows, start=2):
        where = 'line %d (%s)' % (n, row.get('item_id', ''))
        if None in row or any(v is None for v in row.values()):
            errors.append(where + ': wrong number of fields')
            continue
        item = row['item_id']
        if expected_ids is not None and item not in expected_ids:
            errors.append(where + ': item is not in this batch')
        if item in seen:
            errors.append(where + ': duplicate item')
        seen.add(item)
        status = row['text_status']
        if status not in enums['text_status']:
            errors.append(where + ': text_status')
            continue
        yn_fields = ['did_treated', 'did_comparison', 'did_timing', 'did_viable', 'rd_running_variable', 'rd_cutoff', 'rd_assignment', 'rd_viable']
        if status == 'unavailable':
            for f in yn_fields:
                if row[f] != 'NA':
                    errors.append(where + ': %s must be NA when the text is unavailable' % f)
            if row['version_read'] != 'none' or row['source_used'] != 'none':
                errors.append(where + ': version_read and source_used must be none when the text is unavailable')
            if row['did_reason'] != 'NA' or row['rd_reason'] != 'NA':
                errors.append(where + ': reasons must be NA when the text is unavailable')
            if not row['note'].strip():
                errors.append(where + ': say why in note')
        else:
            for f in yn_fields:
                if row[f] not in enums['yn']:
                    errors.append(where + ': %s must be Yes or No' % f)
            if row['version_read'] not in enums['version_read'] or row['version_read'] == 'none':
                errors.append(where + ': version_read')
            if row['source_used'] not in enums['source_used'] or row['source_used'] == 'none':
                errors.append(where + ': source_used')
            for f in ('did_reason', 'rd_reason'):
                if not 10 <= len(row[f].strip()) <= 300:
                    errors.append(where + ': %s needs 10 to 300 characters' % f)
            for axis, parts in (('did', ('did_treated', 'did_comparison', 'did_timing')), ('rd', ('rd_running_variable', 'rd_cutoff', 'rd_assignment'))):
                if row[axis + '_viable'] == 'Yes' and any(row[p] != 'Yes' for p in parts):
                    errors.append(where + ': %s_viable is Yes but a component is not' % axis)
        if len(row['note']) > 200:
            errors.append(where + ': note longer than 200 characters')
    if expected_ids is not None:
        missing = sorted(set(expected_ids) - seen)
        if missing:
            errors.append('missing items: ' + ', '.join(missing[:10]) + (' ...' if len(missing) > 10 else ''))
    return errors


def cmd_validate(args):
    spec = read_spec(args.protocol)
    path = Path(args.codes)
    with open(path, newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        header, rows = reader.fieldnames, list(reader)
    expected = None
    match = re.search(r'codes_([ABC])_b(\d+)\.csv$', path.name)
    items_path = Path(args.items) if args.items else (path.parent.parent / ('items_%s.csv' % match.group(1)) if match else None)
    if items_path and items_path.exists() and match:
        items, _ = read_csv(items_path)
        expected = {r['item_id'] for r in items if int(r['batch']) == int(match.group(2))}
    errors = validate_rows(header, rows, spec, expected)
    if errors:
        print('%s: %d problem(s)' % (path.name, len(errors)))
        for e in errors[:50]:
            print('  ' + e)
        raise SystemExit(1)
    print('%s: OK, %d rows' % (path.name, len(rows)))


# ----------------------------------------------------------------------------------------------------------------------
# Third read
# ----------------------------------------------------------------------------------------------------------------------

def read_coder_outputs(packet_dir, letter):
    rows = {}
    for path in sorted(glob.glob(str(Path(packet_dir) / ('coder_%s/out/codes_%s_b*.csv' % (letter, letter))))):
        for r in read_csv(path)[0]:
            rows[r['item_id']] = r
    return rows


def disagreements(codes_a, codes_b):
    out = []
    for item_id in sorted(set(codes_a) & set(codes_b)):
        a, b = codes_a[item_id], codes_b[item_id]
        diff = [f for f in ('did_viable', 'rd_viable') if a[f] != b[f]]
        if diff:
            out.append((item_id, diff, a, b))
    return out


def cmd_third(args):
    protocol, key_dir, packet_dir = Path(args.protocol), Path(args.key_dir), Path(args.packet_dir)
    spec = read_spec(protocol)
    codes_a, codes_b = read_coder_outputs(packet_dir, 'A'), read_coder_outputs(packet_dir, 'B')
    sheet, _ = read_csv(packet_dir / 'coder_sheet.csv')
    all_ids = {r['item_id'] for r in sheet}
    if args.require_complete and (set(codes_a) != all_ids or set(codes_b) != all_ids):
        raise SystemExit('REFUSED: coder A has %d and coder B has %d of %d items' % (len(codes_a), len(codes_b), len(all_ids)))
    diffs = disagreements(codes_a, codes_b)
    by_id = {r['item_id']: r for r in sheet}
    seed, size = spec['seed'], spec['batch_size']
    order = sorted([d[0] for d in diffs], key=lambda i: hashlib.sha256(('%s|third|%s' % (seed, i)).encode('utf-8')).hexdigest())
    items = []
    for pos, item_id in enumerate(order):
        r = by_id[item_id]
        items.append({'batch': pos // size + 1, 'item_id': item_id, 'title': r['title'], 'jurisdiction': r['jurisdiction'],
                      'document_date': r['document_date'], 'document_url': r['document_url']})
    folder = packet_dir / 'coder_C'
    write_csv_new(folder / 'items_C.csv', ITEM_COLUMNS, items)
    write_text_new(folder / 'INSTRUCTIONS.md', read_instructions(protocol).replace('{CODER}', 'C'))
    write_csv_new(folder / 'codes_template.csv', spec['columns'], [])
    (folder / 'out').mkdir(parents=True, exist_ok=True)
    write_csv_new(key_dir / 'third_read_list.csv', ['item_id', 'fields', 'A_did', 'B_did', 'A_rd', 'B_rd'],
                  [{'item_id': i, 'fields': '|'.join(f), 'A_did': a['did_viable'], 'B_did': b['did_viable'],
                    'A_rd': a['rd_viable'], 'B_rd': b['rd_viable']} for i, f, a, b in diffs])
    print('items coded by both: %d; third read needed on %d (%d batches)' % (len(set(codes_a) & set(codes_b)), len(items), (len(items) + size - 1) // size))


# ----------------------------------------------------------------------------------------------------------------------
# Link check (information only)
# ----------------------------------------------------------------------------------------------------------------------

def cmd_linkcheck(args):
    import urllib.error
    import urllib.request
    sheet, _ = read_csv(Path(args.packet_dir) / 'coder_sheet.csv')
    out_rows = []
    for n, r in enumerate(sheet, start=1):
        url = r['document_url']
        rec = {'item_id': r['item_id'], 'url': url, 'http_status': '', 'final_host': '', 'content_type': '', 'bytes_read': '', 'error': ''}
        if not url:
            rec['error'] = 'no url'
        else:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; research-link-check)'})
            try:
                with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                    body = resp.read(20000)
                    rec.update(http_status=str(resp.status), final_host=urlsplit(resp.geturl()).hostname or '',
                               content_type=resp.headers.get('Content-Type', '').split(';')[0], bytes_read=str(len(body)))
            except urllib.error.HTTPError as exc:
                rec.update(http_status=str(exc.code), final_host=urlsplit(exc.geturl()).hostname or '')
            except Exception as exc:  # network, TLS, timeout
                rec['error'] = type(exc).__name__
            time.sleep(args.delay)
        out_rows.append(rec)
        if n % 50 == 0:
            print('checked %d of %d' % (n, len(sheet)), flush=True)
    write_csv_new(Path(args.key_dir) / 'LINK_CHECK.csv', ['item_id', 'url', 'http_status', 'final_host', 'content_type', 'bytes_read', 'error'], out_rows)
    print(dict(Counter(r['http_status'] or r['error'] for r in out_rows)))


# ----------------------------------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='cmd', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--protocol', default=str(PROTOCOL))
    common.add_argument('--key-dir', default=str(KEY_DIR))
    common.add_argument('--packet-dir', default=str(PACKET_DIR))
    sub.add_parser('frame', parents=[common]).set_defaults(fn=cmd_frame)
    p = sub.add_parser('freeze', parents=[common]); p.add_argument('--freeze-name', default='FREEZE.json'); p.set_defaults(fn=cmd_freeze)
    p = sub.add_parser('draw', parents=[common]); p.add_argument('--freeze'); p.add_argument('--frames-dir'); p.set_defaults(fn=cmd_draw)
    p = sub.add_parser('validate', parents=[common]); p.add_argument('--codes', required=True); p.add_argument('--items'); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser('third', parents=[common]); p.add_argument('--require-complete', action='store_true'); p.set_defaults(fn=cmd_third)
    p = sub.add_parser('linkcheck', parents=[common]); p.add_argument('--timeout', type=float, default=20.0); p.add_argument('--delay', type=float, default=0.4); p.set_defaults(fn=cmd_linkcheck)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == '__main__':
    main()
