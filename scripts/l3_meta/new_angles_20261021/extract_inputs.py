#!/usr/bin/env python3
"""T232 step 1: read-only cohort export for the 77 estimated designs (EXPLORATORY, EX-1).
Reads: refresh 20261021_r1 live set, each design's stored RESULT/case JSON (group_time, event_study, overall),
n1_sets_v2 (cohort -> states). Writes data/l3_meta/new_angles_20261021/inputs/*.csv. Touches no pipeline file.
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_20261021/extract_inputs.py"""
import json, csv, sys, hashlib, gzip
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
LIVE = ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"
N1 = ROOT / "data/l3_meta/n1_sets_v2/n1_sets_v2.json"
OUT = ROOT / "data/l3_meta/new_angles_20261021/inputs"
OUT.mkdir(parents=True, exist_ok=True)

def resolve(o, ptr):
    parts = ptr.strip('/').split('/')
    def rec(o, parts):
        if not parts: return o
        if isinstance(o, list):
            for n in range(1, len(parts) + 1):
                k = '/'.join(parts[:n])
                for e in o:
                    if isinstance(e, dict) and e.get('id') == k:
                        try: return rec(e, parts[n:])
                        except (KeyError, IndexError, ValueError, TypeError): pass
            return rec(o[int(parts[0])], parts[1:])
        for n in range(1, len(parts) + 1):
            k = '/'.join(parts[:n])
            if isinstance(o, dict) and k in o:
                try: return rec(o[k], parts[n:])
                except (KeyError, IndexError, ValueError, TypeError): pass
        raise KeyError(ptr)
    return rec(o, parts)

def unmove(v, srcdir):
    if isinstance(v, dict) and 'moved_to' in v:
        arch = json.loads(gzip.open(srcdir / v['moved_to']).read())
        return resolve(arch, v['path'])
    return v

live = json.load(open(LIVE))
D = [x for x in live['designs'] if x['status'] == 'estimated']
n1 = {d['design_id']: d for d in json.load(open(N1))['designs']}
roster, gt_rows, es_rows = [], [], []
for x in D:
    did = x['design_id']
    j = json.load(open(ROOT / x['source']['path']))
    ptr = x['source']['pointer']
    node = resolve(j, ptr.rsplit('/overall', 1)[0] if ptr.endswith('/overall') else ptr)
    res = node.get('result', node) if isinstance(node, dict) else None
    if isinstance(res, dict):
        sd = (ROOT / x['source']['path']).parent
        res = dict(res, group_time=unmove(res.get('group_time'), sd), event_study=unmove(res.get('event_study'), sd))
    po = x['primary_outcomes'][0]
    row = dict(design_id=did, domain=x['domain'], policy_family=x['policy_family'], topic_cluster=x.get('topic_cluster'),
               source_group=x['source_group'], outcome_family=x.get('outcome_family'), unit=po.get('unit'),
               overall_att=x['point_estimate'], noise_sd=po.get('noise_sd'), noise_cutoff=po.get('noise_cutoff'),
               beyond_noise=po.get('beyond_noise'), has_gt=0, has_es=0, n1_sets=int(did in n1))
    if isinstance(res, dict) and res.get('group_time'):
        row['has_gt'] = 1
        T = n1.get(did, {}).get('T_cohorts', {})
        for r in res['group_time']:
            g = r.get('g')
            states = T.get(str(g))
            gt_rows.append(dict(design_id=did, g=g, event_time=r['event_time'], att=r['att'], se=r.get('se'),
                                n_treated=r.get('n_treated'), n_control=r.get('n_control'), post=r.get('post'),
                                period=r.get('t', r.get('period')), states='|'.join(states) if states else ''))
    if isinstance(res, dict) and res.get('event_study'):
        row['has_es'] = 1
        for r in res['event_study']:
            es_rows.append(dict(design_id=did, event_time=r['event_time'], att=r['att'], se=r.get('se'),
                                n_cohorts=r.get('n_cohorts'), n_treated=r.get('n_treated'),
                                cohorts='|'.join(str(c) for c in (r.get('cohorts') or [])), thin=r.get('thin_support')))
    roster.append(row)

def w(name, rows):
    with open(OUT / name, 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys())); wr.writeheader(); wr.writerows(rows)
w('design_roster.csv', roster); w('group_time_export.csv', gt_rows); w('event_study_export.csv', es_rows)
print(len(roster), 'designs;', sum(r['has_gt'] for r in roster), 'with group_time;', sum(r['has_es'] for r in roster), 'with event_study;', sum(r['n1_sets'] for r in roster), 'with n1 sets')
print('missing:', [r['design_id'] for r in roster if not r['has_gt']])
print('missing noise_sd:', [r['design_id'] for r in roster if not r['noise_sd']])
