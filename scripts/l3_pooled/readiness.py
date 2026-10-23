#!/usr/bin/env python3
"""Deterministic readiness counts only; no politics, indicator values, or fitted outcomes.

Run from project root: python3 scripts/l3_pooled/readiness.py
Every author zero remains uncertified. No supplied sample/effect flags are read.
"""
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from dta_reader import rows

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/l3_pooled'
SRC = OUT / 'downloads'
RULES = ROOT / 'scripts/l3_pooled'
ERAS = ['pre-1980', '1980–1999', '2000–2009', '2010+']
ABBREVS = 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()
NAMES = ['Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming']
STATE = dict(zip(NAMES, ABBREVS))
CONTIG = set(ABBREVS) - {'AK','HI'}


def read(path, delimiter=','):
    with Path(path).open(newline='') as f:
        return list(csv.DictReader(f, delimiter=delimiter))


def write(name, records, fields=None):
    records = list(records)
    fields = fields or list(records[0])
    with (OUT / name).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)


def era(y):
    return ERAS[0] if y < 1980 else ERAS[1] if y < 2000 else ERAS[2] if y < 2010 else ERAS[3]


def load_author(bundle):
    policies, pairs, seen, panel = {}, {}, set(), []
    cols = {'policy_num','policy','policy_description','source','policyarea','keyword_category',
            'first_year','last_year','state','year','adopt_year','adopted'}
    if bundle == 'SPID':
        cols.add('last_year_orig')
    for r in rows(SRC / f'DVKim_{bundle}_iqt.dta', cols):
        p = f"dv_{bundle.lower()}:{r['policy_num']}"
        k = (p, r['state'])
        assert (k, r['year']) not in seen
        seen.add((k, r['year']))
        stable = {x:r[x] for x in cols - {'state','year','adopt_year','adopted'}}
        assert p not in policies or policies[p] == stable
        policies[p] = stable
        pair = pairs.setdefault(k, {'adopt_year':r['adopt_year'], 'start':r['year'], 'end':r['year'], 'zeros':0,'rows':0})
        assert pair['adopt_year'] == r['adopt_year']
        pair['start'], pair['end'] = min(pair['start'],r['year']), max(pair['end'],r['year'])
        pair['zeros'] += r['adopted'] == 0
        pair['rows'] += 1
        if r['adopt_year'] is not None:
            assert r['adopted'] == int(r['year'] >= r['adopt_year'])
        else:
            assert r['adopted'] == 0
        panel.append((p,r['state'],r['year'],r['adopt_year']))
    assert all(v['rows']==v['end']-v['start']+1 for v in pairs.values()), 'Internal panel gaps'
    return policies,pairs,panel


def main():
    OUT.mkdir(exist_ok=True)
    nat = {r['policy']:r for r in read(SRC/'SPID_v1.2_policies.tab','\t')}
    native_events = {(r['policy'],STATE[r['statenam']]):int(r['adopt_year']) for r in read(SRC/'SPID_v1.2.tab','\t')}
    assert len(nat)==724 and len(native_events)==17823
    sp, spp, sp_rows = load_author('SPID')
    nb, nbp, nb_rows = load_author('NBER')
    assert len(sp)==648 and len(nb)==57
    write('author_state_observation_windows.csv',[dict(author_id=p,state=st,**v,zero_evidence='author_panel_zero_uncertified',certified_risk_years=0) for pairmap in [spp,nbp] for (p,st),v in sorted(pairmap.items())])
    exact = {r['policy']:p for p,r in sp.items() if r['policy'] in nat}
    policy_links=[]
    for key in sorted(set(nat)|{r['policy'] for r in sp.values()}):
        p = exact.get(key) or next((p for p,r in sp.items() if r['policy']==key), '')
        n = nat.get(key,{})
        a = sp.get(p,{})
        policy_links.append(dict(spid_policy=key if n else '', author_id=p, author_policy=a.get('policy',''),
            status='matched_id' if n and a else 'native_only' if n else 'author_only',
            native_description=n.get('description',''),author_description=a.get('policy_description',''),
            rule='exact policy string; retain both dates and source definitions' if n and a else 'no automatic alias'))
    for p,r in nb.items():
        policy_links.append(dict(spid_policy='',author_id=p,author_policy=r['policy'],status='nber_definition_unmerged',
            native_description='',author_description=r['policy_description'],rule='not additive until legal-tool deduplication'))
    write('policy_crosswalk.csv',policy_links)
    # State-year date reconciliation restricted to shared contiguous-48 geography.
    rec=[]
    for key,p in exact.items():
        for st in sorted(CONTIG):
            ny=native_events.get((key,st)); pair=spp.get((p,st)); ay=pair['adopt_year'] if pair else None
            status = 'matched_year' if ny is not None and ny==ay else 'conflicting_year' if ny is not None and ay is not None else 'native_only_year' if ny is not None else 'author_only_year' if ay is not None else 'neither_dated'
            rec.append(dict(spid_policy=key,author_id=p,state=st,native_adopt_year=ny,author_adopt_year=ay,status=status,
                author_pair_present=int(pair is not None),resolution='retain conflict; no preferred date' if status=='conflicting_year' else 'one-sided date is not proof of nonadoption' if 'only' in status else ''))
    write('adoption_year_reconciliation.csv',rec)
    # Cross-bundle lexical candidates are review links, never a merge or queue priority.
    def norm(s): return re.sub('[^a-z0-9]+',' ',s.lower()).strip()
    def tokens(s): return set(norm(s).split())-{'the','a','of','and','state','states','law','laws','act','does','in','for','to'}
    candidates=[]
    for p,r in nb.items():
        tt=tokens(r['policy_description'])
        scored=[]
        for k,n in nat.items():
            nt=tokens(n['description']); sim=len(tt&nt)/len(tt|nt) if tt|nt else 0
            if sim>=.35: scored.append((sim,k))
        for sim,k in sorted(scored,reverse=True)[:5]:
            candidates.append(dict(author_id=p,spid_policy=k,token_jaccard=round(sim,4),status='lexical_review_only',rule='>=.35 token Jaccard; at most 5 candidates; no merge'))
    write('nber_overlap_candidates.csv',candidates,['author_id','spid_policy','token_jaccard','status','rule'])
    dictionary = {r['policy']:r for r in read(OUT/'need_dictionary_v0.csv')} if (OUT/'need_dictionary_v0.csv').exists() else {}
    author_dictionary = {r['author_id']:r for r in read(OUT/'author_need_dictionary_v0.csv')} if (OUT/'author_need_dictionary_v0.csv').exists() else {}
    trules = read(RULES/'tracker_rules.csv') if (RULES/'tracker_rules.csv').exists() else []
    tracker_map={}
    tracker_candidates=[]
    for key,n in nat.items():
        for t in trules:
            field=t['match_field']
            value=n.get(field,'') if field != 'combined' else ' '.join([n['policy'],n['description'],n['source']])
            if re.search(t['match_regex'],value,re.I):
                primary=key not in tracker_map
                if primary: tracker_map[key]=t
                tracker_candidates.append(dict(policy=key,primary=int(primary),**t))
    write('tracker_candidates.csv',tracker_candidates)
    # Count-only calendar support: strict v0 lag at Jan 1, no values loaded.
    def need_support(key,y):
        d=dictionary.get(key,author_dictionary.get(key,{}))
        if d.get('defensibility') not in {'candidate_objective_need','candidate'}: return False
        try:
            lag=int(d.get('count_lag_years','2'))
            ref=y-lag
            excluded={int(v) for v in re.findall(r'\d{4}',d.get('excluded_reference_years',''))}
            valid={int(v) for v in re.findall(r'\d{4}',d.get('valid_reference_years',''))}
            if valid:
                prior=[v for v in valid if v<=ref and y-v<=4]
                if not prior: return False
                ref=max(prior)
            return int(d['first_year']) <= ref <= int(d['last_year']) and ref not in excluded
        except (ValueError,KeyError): return False
    windows=[]
    for key,n in nat.items():
        p=exact.get(key); ps=[v for (pk,_),v in spp.items() if pk==p] if p else []
        ends=[v['end'] for v in ps]
        latest=max(ends) if ends else ''
        surviving=sum(v['adopt_year'] is None or v['adopt_year']>latest for v in ps) if ps else 0
        t=tracker_map.get(key,{})
        windows.append(dict(policy=key,topic=n['majortopic_str'],source=n['source'],author_id=p or '',
            native_first_event=n['first_year'],native_last_event=n['last_year'],
            author_panel_start=min(v['start'] for v in ps) if ps else '',author_panel_end=latest,
            author_state_pairs=len(ps),author_explicit_zero_rows=sum(v['zeros'] for v in ps),
            certified_zero_rows=0,certified_risk_state_years=0,classification='partial' if ps else 'not_usable',
            reason='explicit author zeros; source monitoring/clock/complete geography not certified' if ps else 'event-only archive; absent pairs do not certify zero; no exact author ID',
            candidate_unadopted_pairs_at_panel_end=surviving,certified_future_pairs=0,
            tracker_rule=t.get('rule_id',''),tracker_url=t.get('tracker_url',''),tracker_status=t.get('status','none'),
            need_rule=dictionary.get(key,{}).get('rule_id',''),need_class=dictionary.get(key,{}).get('defensibility','pending')))
    write('policy_observation_windows.csv',windows)
    # Include author-only and unmerged NBER definitions in window accounting too.
    aw=[]
    for bundle,pols,pairs in [('SPID',sp,spp),('NBER',nb,nbp)]:
        for p,r in pols.items():
            ps=[v for (pk,_),v in pairs.items() if pk==p]; end=max(v['end'] for v in ps)
            aw.append(dict(author_id=p,policy=r['policy'],bundle=bundle,domain=r['policyarea'],
                panel_start=min(v['start'] for v in ps),panel_end=end,state_pairs=len(ps),
                explicit_zero_rows=sum(v['zeros'] for v in ps),certified_zero_rows=0,
                classification='partial',reason='supplied panel labels; no independent zero/clock certification',
                candidate_unadopted_pairs_at_panel_end=sum(v['adopt_year'] is None or v['adopt_year']>end for v in ps),certified_future_pairs=0))
    write('author_observation_windows.csv',aw)
    write('source_windows.csv',read(RULES/'source_windows.csv'))
    write('cspp_crosswalk.csv',read(RULES/'cspp_links.csv'))
    counts=defaultdict(Counter)
    def add(scope,domain,y,event,risk,key=''):
        for group in ['ALL',domain]:
            c=counts[(scope,era(y),group)]
            c['raw_events']+=int(event); c['author_candidate_at_risk_state_years']+=int(risk)
            c['need_candidate_calendar_events']+=int(event and need_support(key,y))
            c['need_candidate_calendar_at_risk_state_years']+=int(risk and need_support(key,y))
    for (key,st),y in native_events.items(): add('native_50',nat[key]['majortopic_str'],y,True,False,key)
    for bundle,pols,panel in [('SPID',sp,sp_rows),('NBER',nb,nb_rows)]:
        for p,st,y,ay in panel:
            key=pols[p]['policy'] if bundle=='SPID' and pols[p]['policy'] in nat else p
            domain=nat[key]['majortopic_str'] if key in nat else pols[p]['policyarea']
            add('author_'+bundle.lower()+'_48',domain,y,ay==y,ay is None or y<=ay,key)
    for scope in ['native_50','author_spid_48','author_nber_48']:
        domains={d for s,e,d in counts if s==scope}
        for e in ERAS:
            for d in domains: counts[(scope,e,d)]
    write('count_audit.csv', [dict(scope=s,era=e,domain=d,**{k:c[k] for k in ['raw_events','author_candidate_at_risk_state_years','need_candidate_calendar_events','need_candidate_calendar_at_risk_state_years']},certified_events=0,certified_at_risk_state_years=0,certified_need_paired_events=0,certified_need_paired_risk_years=0) for (s,e,d),c in sorted(counts.items())])
    # Balanced queue independent of adoption years, adoption totals, any political/need value.
    # Exact metadata/ID and author geography presence are availability criteria.
    buckets=defaultdict(list)
    for w in windows:
        if w['author_id'] and w['author_state_pairs']==48 and w['tracker_rule']:
            buckets[w['topic']].append(w)
    seed='l3-readiness-source-queue-v0-20261006'
    def rank(w):
        t=tracker_map.get(w['policy'],{})
        return (0 if t.get('status')=='verified_definition_history' else 1,hashlib.sha256((seed+'|'+w['policy']).encode()).hexdigest())
    for b in buckets.values(): b.sort(key=rank)
    chosen=[]
    while len(chosen)<100:
        changed=False
        for domain in sorted(buckets):
            if buckets[domain] and len(chosen)<100:
                chosen.append(buckets[domain].pop(0)); changed=True
        if not changed: break
    write('first100_queue.csv',[dict(rank=i,policy=w['policy'],description=nat[w['policy']]['description'],domain=w['topic'],source=w['source'],author_id=w['author_id'],author_state_pairs=w['author_state_pairs'],tracker_rule=w['tracker_rule'],tracker_url=w['tracker_url'],tracker_status=w['tracker_status'],need_rule=w['need_rule'],need_class=w['need_class'],status='queued_for_source_completion',rule='exact ID; 48 supplied state pairs; tracker endpoint required; round-robin domains; verified definition/history first; seeded SHA256 tie-break; no dates/counts') for i,w in enumerate(chosen,1)])
    latest_observed=max(y for p,st,y,ay in sp_rows+nb_rows if ay==y)
    latest_panel_counts={}
    for bundle,pols,pairs in [('SPID',sp,spp),('NBER',nb,nbp)]:
        at_latest=[(p,st) for (p,st),v in pairs.items() if v['start']<=latest_observed<=v['end'] and (v['adopt_year'] is None or v['adopt_year']>latest_observed)]
        latest_panel_counts[bundle]=dict(policies=len({p for p,st in at_latest}),pairs=len(at_latest))
    write('endpoint_remaining_audit.csv',[dict(bundle=b,endpoint_year=y,policies=sum(w['candidate_unadopted_pairs_at_panel_end']>0 for w in aw if w['bundle']==b and w['panel_end']==y),candidate_pairs=sum(w['candidate_unadopted_pairs_at_panel_end'] for w in aw if w['bundle']==b and w['panel_end']==y),certified_future_pairs=0) for b in ['SPID','NBER'] for y in sorted({w['panel_end'] for w in aw if w['bundle']==b})])
    summary=dict(latest_observed_adoption_year=latest_observed,author_remaining_at_global_latest=latest_panel_counts,native_policies=len(nat),native_events=len(native_events),author_spid_policies=len(sp),author_nber_policies=len(nb),
        policy_link_status=dict(Counter(r['status'] for r in policy_links)),date_status=dict(Counter(r['status'] for r in rec)),
        native_window_class=dict(Counter(r['classification'] for r in windows)),
        author_panel_rows={'SPID':len(sp_rows),'NBER':len(nb_rows)},
        author_state_pairs={'SPID':len(spp),'NBER':len(nbp)},
        candidate_remaining_native_matched_policies=sum(w['candidate_unadopted_pairs_at_panel_end']>0 for w in windows),
        candidate_remaining_native_matched_pairs=sum(w['candidate_unadopted_pairs_at_panel_end'] for w in windows),
        candidate_remaining_author_policies=sum(w['candidate_unadopted_pairs_at_panel_end']>0 for w in aw),
        candidate_remaining_author_pairs=sum(w['candidate_unadopted_pairs_at_panel_end'] for w in aw),
        certified_future_policies=0,certified_future_pairs=0,tracker_candidate_policies=len(tracker_map),
        queue_size=len(chosen),queue_tracker_candidates=sum(bool(w['tracker_rule']) for w in chosen),
        queue_domains=dict(Counter(w['topic'] for w in chosen)),need_classes=dict(Counter(w['need_class'] for w in windows)),
        certified_events=0,certified_risk_years=0,real_association_fit_calls=0)
    # Power based on native calendar candidates only; certification has no MDE.
    eligible=[(y,k) for (k,st),y in native_events.items() if need_support(k,y)]
    early=sum(1985<=y<=1994 for y,k in eligible);late=sum(2010<=y<=2017 for y,k in eligible);post=sum(y>=2000 for y,k in eligible)
    def mde(n): return round(3.08*.3*math.sqrt(5/n),4) if n else None
    summary['count_power_illustration']=dict(scale='event-equivalent assumed sigma=.3 nats; DE=5; alpha=.025 two-sided; 80% power; NOT risk-score precision',candidate_early_1985_1994_events=early,candidate_late_2010_2017_events=late,candidate_post2000_events=post,post2000_mde=mde(post),early_late_mde=round(3.08*.3*math.sqrt(5*(1/early+1/late)),4) if early and late else None,certified_mde=None)
    assert sum(c['raw_events'] for (s,e,d),c in counts.items() if s=='native_50' and d=='ALL')==17823
    assert sum(c['raw_events'] for (s,e,d),c in counts.items() if s=='author_spid_48' and d=='ALL')==13893
    assert sum(c['raw_events'] for (s,e,d),c in counts.items() if s=='author_nber_48' and d=='ALL')==1541
    assert len(rec)==646*48 and sum(summary['date_status'].values())==len(rec)
    assert len({w['policy'] for w in chosen})==100
    assert len(dictionary)==724, 'Build need dictionary before count audit'
    assert len(author_dictionary)==59, 'Build one-sided author need dictionary before count audit'
    (OUT/'readiness_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    # Check retained inputs before reporting reuse; no new files downloaded by this script.
    manifest=read(SRC/'DOWNLOAD_MANIFEST.csv')
    for r in manifest:
        b=(SRC/r['file']).read_bytes()
        assert len(b)==int(r['local_bytes']) and hashlib.sha256(b).hexdigest()==r['sha256']
    write('READINESS_INPUT_MANIFEST.csv',manifest)
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
