#!/usr/bin/env python3
"""T232 step 1b: cohort -> states (and calendar onset year) for the estimated designs. EXPLORATORY (EX-1). Structure only, no effect values used.
Sources, in order: n1_sets_v2 (T_cohorts, T_operative_onset_year); else the design RESULT.json l3_export_v2.geography.treatment_records
(audited_first_full_start_period -> cohort g; onset year = g-1 when g is a calendar year). A cohort is kept only if its state count equals
n_treated of the stored group_time row at event time 0.
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_20261021/map_states.py"""
import json, csv, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
IN = ROOT / "data/l3_meta/new_angles_20261021/inputs"
live = json.load(open(ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"))
D = {x['design_id']: x for x in live['designs'] if x['status'] == 'estimated'}
n1 = {d['design_id']: d for d in json.load(open(ROOT / "data/l3_meta/n1_sets_v2/n1_sets_v2.json"))['designs']}
n_e0 = collections.defaultdict(dict)
for r in csv.DictReader(open(IN / "group_time_export.csv")):
    if r['post'] == 'True' and r['event_time'] == '0' and r['n_treated']:
        n_e0[r['design_id']][int(r['g'])] = int(float(r['n_treated']))
rows = []
for did, x in D.items():
    cohorts, src = {}, None
    if did in n1:
        src = 'n1_sets_v2'
        on = n1[did]['T_operative_onset_year']
        for g, sts in n1[did]['T_cohorts'].items():
            cohorts[int(g)] = (sts, [on.get(s) for s in sts])
    elif x['source_group'] not in ('prototype', 'manual_batch'):
        j = json.load(open(ROOT / x['source']['path']))
        tr = (j.get('l3_export_v2') or {}).get('geography', {}).get('treatment_records')
        if tr:
            src = 'result_treatment_records'
            m = collections.defaultdict(list)
            for t in tr:
                g = t.get('audited_first_full_start_period')
                if g: m[int(g)].append(t['unit'])
            for g, sts in m.items():
                cohorts[g] = (sorted(sts), [g - 1 if g > 1900 else None] * len(sts))
    for g, n in n_e0[did].items():
        sts, on = cohorts.get(g, ([], []))
        ok = len(sts) == n
        rows.append(dict(design_id=did, g=g, n_treated_e0=n, n_states=len(sts), matched=int(ok), source=src or 'none',
                         states='|'.join(sts) if ok else '', onset_years='|'.join(str(o) for o in on) if ok else ''))
with open(IN / "cohort_states.csv", 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
by = collections.defaultdict(lambda: [0, 0])
for r in rows:
    by[r['design_id']][0] += 1; by[r['design_id']][1] += r['matched']
full = [d for d, (a, b) in by.items() if a == b]
part = [d for d, (a, b) in by.items() if 0 < b < a]
print('designs', len(by), 'fully mapped', len(full), 'partly', len(part), 'none', len(by) - len(full) - len(part))
print('cohorts', len(rows), 'matched', sum(r['matched'] for r in rows))
print('partly:', [(d, by[d]) for d in part])
