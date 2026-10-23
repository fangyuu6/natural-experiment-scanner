#!/usr/bin/env python3
"""T186: researcher records for the ready-now cards (RAND firearm database v6.0, CSPP v2.6, SPID v1.2).

Reads only local dated tables (no outcome value, no network). Writes records for scripts/l1/b1020_cards.py
(group 'ready_now'). Run from the repo root:
  PYTHONPATH=$HOME/Library/Python/3.9/lib/python/site-packages .venv_did/bin/python -B scripts/l1/b1020_readynow_records.py
"""
import csv
import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/l2_pipeline/intake/bulk_20261020/ready_now/records'
LIT = json.load(open('/path/to/scratch/scratch/T186/work/lit.json'))
RAND_X = 'data/l4/rand_firearm_law_database/raw/TL-A243-2-v4 State Firearm Law Database 6.0.xlsx'
RAND = pd.read_pickle('/path/to/scratch/scratch/T186/rand_db.pkl')
STATES = ('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND '
          'OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY').split()


def lit(doi, fit, locator):
    m = LIT[doi.lower()]
    return dict(doi=m['doi'], title=m['title'], authors=m['authors'], year=m['year'], journal=m['journal'],
                locator='%s %s, %s; %s (Crossref title read)' % (m['authors'].split(',')[0], m['year'], m['journal'], locator),
                locator_short='%s %s, %s' % (m['authors'].split(',')[0], m['year'], locator), fit=fit, supports=['target_outcome'],
                license='unverified (publisher copyright; cite only)')


def rand_intervals(cls, subs, effect=None):
    m = RAND[(RAND['Law Class'] == cls) & (RAND['Law Class Subtype'].isin(subs))]
    if effect:
        m = m[m['Effect'] == effect]
    out = {}
    for st, g in m.groupby('State Postal Abbreviation'):
        iv = []
        for _, r in g.iterrows():
            s = (int(r['Effective Date Year']), int(r['Effective Date Month']) if pd.notna(r['Effective Date Month']) else 1)
            e = (int(r['Supersession Date Year']), int(r['Supersession Date Month']) if pd.notna(r['Supersession Date Month']) else 1) if pd.notna(r['Supersession Date Year']) else None
            iv.append((s, e, r['Law ID'], str(r['Law Class Subtype'])))
        iv.sort(key=lambda t: (t[0], t[1] or (9999, 1)))
        out[st] = iv
    return out


def merge(iv):
    """Merge in-force intervals; return (first_start, gaps, open_end)."""
    iv = sorted(iv, key=lambda t: (t[0], t[1] or (9999, 1)))
    cur_s, cur_e = iv[0][0], iv[0][1]
    gaps = []
    for s, e, _, _ in iv[1:]:
        if cur_e is None:
            continue
        if s <= (cur_e[0], cur_e[1] + 1) or s <= cur_e:
            if e is None or e > cur_e:
                cur_e = e
        else:
            gaps.append((cur_e, s))
            cur_e = e
    return iv[0][0], gaps, cur_e


def rand_card(cfg):
    w0, w1 = cfg['window']
    main = rand_intervals(cfg['cls'], cfg['main'], cfg.get('effect'))
    cover = rand_intervals(cfg['cls'], cfg['main'] + cfg.get('cover', []), cfg.get('effect'))
    amb_rows = rand_intervals(cfg['cls'], cfg.get('ambiguous', []), None) if cfg.get('ambiguous') else {}
    states, detail, other = {}, {}, {}
    for st, iv in cover.items():
        if st not in STATES:
            continue
        first, gaps, end = merge(iv)
        mfirst = min(i[0] for i in main[st]) if st in main else None
        if mfirst is None:
            other[st] = dict(status='excluded_ambiguous', year=None, note='Only a related subtype is coded (%s); not the card predicate; excluded from both arms.' % iv[0][3])
            continue
        if gaps or (end is not None and end[0] <= w1):
            why = 'a gap or end of the coded provision (%s)' % ('; '.join('%s-%02d to %s-%02d' % (a[0], a[1], b[0], b[1]) for a, b in gaps) if gaps else 'superseded %s-%02d with no successor row' % end)
            other[st] = dict(status='reversal_or_gap', year=first[0], note='RAND shows ' + why + '; treatment status ambiguous inside the window; excluded from both arms.')
            continue
        y = first[0]
        states[st] = y
        ids = ', '.join(sorted({i[2] for i in iv if i[0] <= (w1, 12)})[:3])
        detail[st] = dict(date='%d-%02d' % first, source='RAND State Firearm Law Database v6.0, Database tab, Law ID %s; effective date %d-%02d' % (ids, first[0], first[1]), confidence='primary_compilation')
    for st in amb_rows:
        if st not in states and st not in other and st not in cover:
            other[st] = dict(status='excluded_ambiguous', year=None, note='A related stronger or narrower subtype is coded (%s) but not the card predicate; excluded from both arms.' % amb_rows[st][0][3])
    never = sorted(s for s in STATES if s not in cover and s not in amb_rows)
    return states, never, other, detail


def cspp(var, w0, w1, pulse=False):
    c = pd.read_csv(ROOT / 'data/l4/cspp/raw/correlates2-6.csv', low_memory=False)
    d = c[['year', 'st', var]].dropna()
    d = d[(d.year >= w0) & (d.year <= w1)]
    states, detail, other, never = {}, {}, {}, []
    for st, g in d.groupby('st'):
        if st not in STATES:
            continue
        g = g.sort_values('year')
        s = ''.join(str(int(x)) for x in g[var])
        y0 = int(g.year.min())
        if '1' not in s:
            never.append(st)
            continue
        if pulse:
            states[st] = y0 + s.index('1')
            detail[st] = dict(date=str(states[st]), source='CSPP v2.6 correlates2-6.csv, %s: first enactment-year flag' % var, confidence='secondary')
            continue
        if s.startswith('1'):
            other[st] = dict(status='left_censored', year=y0, note='Coded 1 in the first year of the CSPP series (%d); adoption date unknown; excluded from both arms.' % y0)
            continue
        i = s.index('1')
        if '10' in s[i:]:
            j = s.index('10', i) + 1
            other[st] = dict(status='reversal', year=y0 + i, note='Coded 1 from %d and back to 0 from %d; reversal inside the window; excluded from both arms.' % (y0 + i, y0 + j))
            continue
        states[st] = y0 + i
        detail[st] = dict(date=str(y0 + i), source='CSPP v2.6 correlates2-6.csv, %s: first year coded 1' % var, confidence='secondary')
    return states, sorted(never), other, detail


def spid(var, w0, w1):
    states, detail = {}, {}
    for r in csv.DictReader(open(ROOT / 'data/l4/spid/raw/spid_v1.2/SPID_v1.2.tab'), delimiter='\t'):
        if r['policy'].strip('"') == var:
            nm = r['statenam'].strip('"')
            states[nm] = int(r['adopt_year'])
    ab = dict(zip('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming'.split('|'), sorted(STATES, key=lambda s: s) and
                 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()))
    st = {ab[k]: v for k, v in states.items()}
    det = {s: dict(date=str(y), source='SPID v1.2 (Boehmke et al.), SPID_v1.2.tab, policy %s: adoption year' % var, confidence='secondary') for s, y in st.items()}
    never = sorted(s for s in STATES if s not in st)
    return st, never, {}, det


FIREARM_WINDOW = [1999, 2024]
CARDS = []


def add(**kw):
    CARDS.append(kw)


add(slug='mental_health_commitment_prohibitor_firearm_homicide', src='rand', cls='prohibited possessor',
    main=['mental health : committed to mh facility'],
    cover=['mental health : committed to mh facility : outpatient', 'mental health : committed to mh facility : voluntary'],
    effect='Restrictive', window=FIREARM_WINDOW, series='wonder_firearm_homicide_deaths', family='firearm_prohibitor_mental_health_commitment',
    unit_text='firearm homicide deaths, state of residence', seed=202620101,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that bars a person committed to a mental health facility from possessing or buying a firearm. Does not count: federal law alone, bars that apply only to persons adjudicated incompetent, or only to voluntary or outpatient commitment without the inpatient commitment bar, and administrative reporting rules alone. Once in force the state stays coded 1; a repeal or gap is flagged as a reversal.',
    prov='Operative year = effective year of the first row of the RAND State Firearm Law Database v6.0, law class prohibited possessor, subtype mental health committed to a mental health facility, effect restrictive; voluntary and outpatient commitment rows are used only to test whether the provision stayed in force.',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_state_mental_health_commitment_firearm_prohibitor', pop='All residents of the state (persons committed to a facility are a small share; the law reaches them and the guns they could obtain)',
    proxy='Same-construct series (deaths from firearm homicide); the law bars a small group, so the effect on the all-resident rate is diluted and federal law already bars the same group, so the state law mainly changes reporting and enforcement.',
    dil='The barred group is a small share of firearm homicide offenders and federal law covers the same group; no numeric dilution factor is inferred.',
    rating='C+', refs=[('10.1016/j.annepidem.2014.03.004', 'partial - reviews evidence on mental illness, firearm access restrictions and violence; not a state panel of commitment bars', 'mental illness, firearm prohibition and violence review'),
                       ('10.1007/s11606-019-04922-x', 'partial - state firearm law panel with homicide outcomes; includes laws on prohibited persons', 'state firearm laws and homicide and suicide panel')])
add(slug='campus_ccw_ban_violent_crime', src='rand', cls='firearms in college/university',
    main=['public and private : CCW not allowed', 'public and private : possession prohibited'], cover=[],
    effect='Restrictive', ambiguous=['public : CCW not allowed', 'public : possession prohibited', 'private : possession prohibited'],
    window=[1979, 2019], series='fbi_ucr_violent_crime_rate', family='campus_concealed_carry_ban', unit_text='violent crimes per 100,000 residents', seed=202620102,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that bars concealed carry of firearms on the grounds of both public and private colleges and universities (a concealed-carry ban or a possession ban covering both sectors). Does not count: bans that cover only public or only private institutions, institution-level policy alone, and permission statutes. Once in force the state stays coded 1; a repeal or replacement by a permission is flagged as a reversal.',
    prov='Operative year = effective year of the first RAND State Firearm Law Database v6.0 row, law class firearms in college/university, subtype public and private (concealed carry not allowed, or possession prohibited), effect restrictive; states with only a public-only or private-only ban row are excluded from both arms.',
    target='reported_violent_crime', tdesc='violent_crime_after_college_campus_concealed_carry_ban', pop='All residents of the state; the law applies on campus grounds, so the statewide rate is a thin proxy',
    proxy='The catalog series is the statewide violent crime rate; campus crime is a tiny share of it, so any effect is strongly diluted. The campus-specific series is not in the catalog.',
    dil='Campus grounds are a very small share of statewide violent crime; no numeric dilution factor is inferred.',
    rating='C', refs=[('10.1080/15388220.2021.1930016', 'partial - campus safety before and after a campus carry permission law; campus level, not a state panel', 'campus carry law and campus safety'),
                      ('10.1111/jels.12219', 'partial - state concealed carry laws and violent crime panel (state rate outcome)', 'state carry laws and violent crime panel')])
add(slug='dv_firearm_removal_at_scene_firearm_homicide', src='rand', cls='firearm removal at scene of domestic violence',
    main=['authorized', 'required'], cover=[], effect='Restrictive', window=FIREARM_WINDOW, series='wonder_firearm_homicide_deaths',
    family='domestic_violence_firearm_removal_at_scene', unit_text='firearm homicide deaths, state of residence', seed=202620103,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that authorizes or requires a law enforcement officer responding to a domestic violence incident to remove firearms from the scene. Does not count: seizure only as evidence of another offence, court-ordered surrender after a protective order, and agency policy alone. Once in force the state stays coded 1; a repeal is flagged as a reversal.',
    prov='Operative year = effective year of the first RAND State Firearm Law Database v6.0 row, law class firearm removal at scene of domestic violence, subtype authorized or required, effect restrictive (the two subtypes are one predicate; the plan row named authorized only).',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_domestic_violence_scene_firearm_removal_law', pop='All residents of the state; the law reaches households with a domestic violence call',
    proxy='The catalog has no intimate-partner homicide series; the closest same-construct series is the statewide firearm homicide rate. Intimate-partner firearm homicides are a minority of all firearm homicides, so any effect is diluted.',
    dil='Intimate-partner homicides are a minority of firearm homicides; no numeric dilution factor is inferred.',
    rating='C+', refs=[('10.7326/M16-2849', 'partial - state intimate partner violence firearm laws and intimate partner homicide, state panel', 'IPV firearm laws and intimate partner homicide'),
                       ('10.1177/0193841X06287307', 'partial - domestic violence firearm restriction laws and intimate partner homicide', 'DV firearm restriction laws and homicide')])
add(slug='state_preemption_comprehensive_firearm_homicide', src='rand', cls='local laws preempted by state',
    main=['comprehensive'], cover=[], effect='Permissive', ambiguous=['limited'], window=FIREARM_WINDOW, series='wonder_firearm_homicide_deaths',
    family='state_firearm_preemption_comprehensive', unit_text='firearm homicide deaths, state of residence', seed=202620104,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that bars cities and counties from adopting any firearm regulation of their own (comprehensive preemption). Does not count: preemption limited to some subjects, and laws that only restrict local gun regulation of a single topic. Once in force the state stays coded 1; a repeal is flagged as a reversal.',
    prov='Operative year = effective year of the first RAND State Firearm Law Database v6.0 row, law class local laws preempted by state, subtype comprehensive, effect permissive; states with only a limited-preemption row are excluded from both arms.',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_comprehensive_state_firearm_preemption', pop='All residents of the state, urban residents most (the law removes local regulation)',
    proxy='Same-construct series (deaths from firearm homicide); the law removes local regulation, so its reach depends on how many cities would have regulated, which is unknown.',
    dil='The law matters only where local governments would otherwise regulate; no numeric dilution factor is inferred.',
    rating='B-', refs=[('10.1007/s11524-018-0273-3', 'partial - firearm laws and homicide in urban counties (preemption is among the laws studied)', 'firearm laws and homicide in urban counties'),
                       ('10.1007/s11606-019-04922-x', 'partial - state firearm law panel with homicide outcomes', 'state firearm laws and homicide panel')])
add(slug='state_preemption_punitive_firearm_homicide', src='rand', cls='local laws preempted by state',
    main=['comprehensive - punitive'], cover=[], effect='Permissive', window=FIREARM_WINDOW, series='wonder_firearm_homicide_deaths',
    family='state_firearm_preemption_punitive', unit_text='firearm homicide deaths, state of residence', seed=202620105,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that bars local firearm regulation and also penalizes local officials or governments that adopt or enforce one (punitive preemption). Does not count: preemption without a penalty on officials or governments. Once in force the state stays coded 1; a repeal is flagged as a reversal.',
    prov='Operative year = effective year of the first RAND State Firearm Law Database v6.0 row, law class local laws preempted by state, subtype comprehensive punitive, effect permissive; the contrast is against states that never added a penalty (they may have plain preemption).',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_punitive_state_firearm_preemption', pop='All residents of the state, urban residents most',
    proxy='Same-construct series (deaths from firearm homicide); most treated states already had plain preemption, so the design tests the added penalty only.',
    dil='The penalty changes local behaviour only in cities that would have regulated; no numeric dilution factor is inferred.',
    rating='C+', refs=[('10.1007/s11524-018-0273-3', 'partial - firearm laws and homicide in urban counties', 'firearm laws and homicide in urban counties'),
                       ('10.1007/s11606-019-04922-x', 'partial - state firearm law panel with homicide outcomes', 'state firearm laws and homicide panel')])
add(slug='straw_purchase_law_firearm_homicide', src='rand', cls='gun trafficking',
    main=['straw purchase - buyer liability', 'straw purchase - buyer and seller liability'], cover=[], effect='Restrictive',
    window=FIREARM_WINDOW, series='wonder_firearm_homicide_deaths', family='straw_purchase_state_offense', unit_text='firearm homicide deaths, state of residence', seed=202620106,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that makes it a state offence to buy a firearm on behalf of a person who may not lawfully have one (a straw purchase), with liability on the buyer or on both buyer and seller. Does not count: federal law alone, seller-only liability, and lost or stolen reporting. Once in force the state stays coded 1; a repeal is flagged as a reversal.',
    prov='Operative year = effective year of the first RAND State Firearm Law Database v6.0 row, law class gun trafficking, subtype straw purchase (buyer liability, or buyer and seller liability), effect restrictive (the plan row named buyer liability only; buyer and seller liability includes the buyer offence).',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_state_straw_purchase_offense', pop='All residents of the state',
    proxy='Same-construct series (deaths from firearm homicide); federal law already makes a straw purchase an offence, so the state law mainly adds state enforcement.',
    dil='Federal law already covers the conduct; no numeric dilution factor is inferred.',
    rating='C+', refs=[('10.1007/s11524-009-9351-x', 'partial - state firearm seller accountability policies and firearm trafficking; trafficking, not homicide, outcome', 'state seller accountability policies and trafficking'),
                       ('10.1007/s11606-019-04922-x', 'partial - state firearm law panel with homicide outcomes', 'state firearm laws and homicide panel')])
add(slug='concealed_carry_permit_training_violent_crime', src='cspp', var='btrain', window=[1986, 2016], series='fbi_ucr_violent_crime_rate',
    family='concealed_carry_permit_training_requirement', unit_text='violent crimes per 100,000 residents', seed=202620107,
    predicate='State-year = 1 from the first calendar year in which a state requires training or testing as a condition for a concealed-carry permit. Does not count: training required only for some applicants, and states with no permit requirement. Once in force the state stays coded 1; a return to 0 is flagged as a reversal.',
    prov='Operative year = first year coded 1 for the variable btrain (training or testing requirement for carry permit) in the CSPP v2.6 correlates file (source: Sorens, Muedini and Ruger 2008); the codebook text for the coding of 1 is internally inconsistent, so states coded 1 in the first series year are excluded, and the two states that return to 0 are excluded.',
    target='reported_violent_crime', tdesc='violent_crime_after_concealed_carry_permit_training_requirement', pop='All residents of the state; the law affects permit applicants only',
    proxy='Same-construct series (violent crime); permit holders are a small share of residents and of offenders, so any effect is diluted.',
    dil='Permit applicants are a small share of residents; no numeric dilution factor is inferred.',
    rating='C', refs=[('10.2105/ajph.2017.304057', 'partial - ease of concealed carry permit access (including training conditions) and homicide, state panel', 'concealed carry permit access and homicide'),
                      ('10.1111/jels.12219', 'partial - state concealed carry laws and violent crime panel', 'state carry laws and violent crime panel')])
add(slug='violent_misdemeanor_firearm_restriction_firearm_homicide', src='cspp', var='mdrestriction', window=[1999, 2017], series='wonder_firearm_homicide_deaths',
    family='violent_misdemeanor_firearm_prohibition', unit_text='firearm homicide deaths, state of residence', seed=202620108,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that bars a person convicted of a violent misdemeanor from possessing a firearm. Does not count: federal law alone, bars limited to domestic violence misdemeanors, and bars that last only while a sentence is served. Once in force the state stays coded 1; a return to 0 is flagged as a reversal.',
    prov='Operative year = first year coded 1 for the variable mdrestriction (violent misdemeanor restriction in place) in the CSPP v2.6 correlates file (source: Schiller and Sidorsky 2022); states coded 1 in the first series year are excluded as left-censored.',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_violent_misdemeanor_firearm_prohibition', pop='All residents of the state; the law bars a group of misdemeanants',
    proxy='Same-construct series (deaths from firearm homicide); the barred group is a small share of homicide offenders, so any effect is diluted.',
    dil='The barred group is a small share of homicide offenders; no numeric dilution factor is inferred.',
    rating='C+', refs=[('10.7326/M16-2849', 'partial - state intimate partner violence firearm laws and intimate partner homicide, state panel', 'IPV firearm laws and intimate partner homicide'),
                       ('10.1177/0193841X06287307', 'partial - domestic violence firearm restriction laws and intimate partner homicide', 'DV firearm restriction laws and homicide')])
add(slug='stalking_misdemeanor_firearm_restriction_firearm_homicide', src='cspp', var='stalkingmd_enacted', pulse=True, window=[1999, 2017], series='wonder_firearm_homicide_deaths',
    family='stalking_misdemeanor_firearm_prohibition', unit_text='firearm homicide deaths, state of residence', seed=202620109,
    predicate='State-year = 1 from the first calendar year in which a state statute is in force that bars a person convicted of misdemeanor stalking from possessing a firearm. Does not count: federal law alone, and bars limited to felony stalking. Once in force the state stays coded 1.',
    prov='Operative year = first year the CSPP v2.6 variable stalkingmd_enacted (stalking misdemeanor, enacted) is coded 1 (source: Schiller and Sidorsky 2022); the variable is an enactment-year flag, not a state-level in-force dummy, so the year is an enactment year; later flags are treated as amendments.',
    target='firearm_homicide_deaths', tdesc='firearm_homicide_after_stalking_misdemeanor_firearm_prohibition', pop='All residents of the state; the law bars a small group of misdemeanants',
    proxy='Same-construct series (deaths from firearm homicide); stalking convictions are a small share of homicide precursors, so any effect is diluted.',
    dil='The barred group is very small; no numeric dilution factor is inferred.',
    rating='C', refs=[('10.7326/M16-2849', 'partial - state intimate partner violence firearm laws and intimate partner homicide, state panel', 'IPV firearm laws and intimate partner homicide'),
                      ('10.1136/ip.2009.024620', 'partial - domestic violence policies and intimate partner homicide in large cities', 'DV policies and intimate partner homicide')])
add(slug='sex_offender_broad_community_notification_rape', src='spid', var='broadcom', window=[1979, 2010], series='fbi_ucr_rape_legacy_rate',
    family='sex_offender_broad_community_notification', unit_text='reported rapes (legacy definition) per 100,000 residents', seed=202620110,
    predicate='State-year = 1 from the first calendar year in which a state requires broad community notification about registered sex offenders (public release to the community at large, not only to agencies or victims). Does not count: registration without public release, notification limited to schools or neighbours, and notification to agencies only. Once in force the state stays coded 1.',
    prov='Operative year = adoption year in the SPID v1.2 table for the policy broadcom (broad community notification of sex offenders; source Boehmke and Skinner); SPID gives a year only and does not separate enactment from effective date, so the plus-one and minus-one start sensitivities carry the uncertainty.',
    target='rape', tdesc='reported_rape_after_broad_community_notification_of_sex_offenders', pop='All residents of the state; the outcome is reported rapes (reporting can move with the law)',
    proxy='Same-construct series (reported rape); it measures offences reported to police, so reporting and offending cannot be separated and the sign is not interpretable as deterrence alone.',
    dil='Notification applies to registered offenders only; no numeric dilution factor is inferred.',
    rating='C+', refs=[('10.1086/658485', 'yes - same policy class (registration and notification laws) and a crime outcome', 'registration and notification laws and criminal behavior'),
                       ('10.1086/658483', 'yes - same policy class (sex offender registries) and a crime outcome', 'sex offender registries')],
    extra_notes=['HUB_RULING_20261020_SEX_OFFENDER_CARDS: panel (5 of 5) wrote this one card of the three SPID notification rows; window fixed at 1979-2010 before any screen result; the outcome is the reported rape rate, not offending.',
                 'States absent from the SPID table are controls "relative to weaker or other-label notification": the federal Wetterling and Megan laws pushed all states to adopt some registration and notification between 1994 and 1999, so absence is not "untreated".',
                 'If this card fails the outcome-blind support or RI screen it is dropped and the family is not retried through the neighbour or any-active notification rows.'])

SRC = {
    'rand': [dict(url='https://www.rand.org/pubs/tools/TLA243-2-v4.html', locator='RAND State Firearm Law Database v6.0 (TL-A243-2-v4), Database tab; state firearm laws 1979 to September 2025, effective dates by law row',
                  license='ODbL (attribution, indicate changes) with RAND noncommercial notice; row-level text not published', complete_list=True, path=RAND_X)],
    'cspp': [dict(url='https://ippsr.msu.edu/public-policy/correlates-state-policy', locator='Correlates of State Policy v2.6, correlates2-6.csv (state by year); codebook_2-6.csv for the variable',
                  license='CSPP terms (academic use, cite); see data/l4/cspp/README.md', complete_list=True, path='data/l4/cspp/raw/correlates2-6.csv')],
    'spid': [dict(url='https://doi.org/10.7910/DVN/CVYSR7', locator='State Policy Innovation and Diffusion (SPID) database v1.2, SPID_v1.2.tab (state by policy adoption year)',
                  license='Harvard Dataverse CC0 waiver per dataset record (check dataset page); cite Boehmke et al.', complete_list=True, path='data/l4/spid/raw/spid_v1.2/SPID_v1.2.tab')],
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    for cfg in CARDS:
        w0, w1 = cfg['window']
        if cfg['src'] == 'rand':
            states, never, other, detail = rand_card(cfg)
        elif cfg['src'] == 'cspp':
            states, never, other, detail = cspp(cfg['var'], w0, w1, cfg.get('pulse', False))
        else:
            states, never, other, detail = spid(cfg['var'], w0, w1)
        adopt_in = {s: y for s, y in states.items()}
        rec = {
            'schema': 'bulk20261020_ready_now_record_v1', 'created': '2026-10-20', 'group': 'ready_now', 'record_id': cfg['slug'],
            'design_id': 'b1020rn_' + cfg['slug'], 'seed': cfg['seed'], 'rating': cfg['rating'], 'verdict': 'CONDITIONAL',
            'verdict_note': '{treated} treated states in {cohorts} cohorts and {never} never-treated states pass the geometry screen on the local dated table; the outcome is a proxy for the targeted harm and any effect is diluted.',
            'policy': dict(name=cfg['family'].replace('_', ' '), family=cfg['family'], predicate_clean=cfg['predicate'], predicate_with_provenance=cfg['prov'],
                           clock_note='Year = calendar year of the source date; the transition year is dropped by the V8b rule. Window %d-%d fixed before any screen result. %s' % (w0, w1, 'RAND dates are effective dates (month precision).' if cfg['src'] == 'rand' else 'Source dates are adoption years (year precision); enacted versus effective is not distinguished.')),
            'population': dict(description=cfg['unit_text'] + ', state of residence', filters=None),
            'outcome': dict(series_id=cfg['series'], outcome_unit=cfg['unit_text']),
            'first_order': dict(target_outcome=cfg['target'], target_construct=cfg['target'], target_description=cfg['tdesc'], relation='closest_proxy', target_population=cfg['pop'],
                                proxy_justification=cfg['proxy'], expected_dilution=cfg['dil'], statute_refs=[], purpose_direction=None,
                                literature=[lit(d, f, l) for d, f, l in cfg['refs']]),
            'adoption': dict(window=[w0, w1], coverage_end='%d-12-31' % w1, complete_list=True, states=dict(sorted(adopt_in.items())),
                             never=never, other_status=other, detail=detail, sources=SRC[cfg['src']], after_window={},
                             reversals_or_ambiguities=['%s: %s' % (s, o['note']) for s, o in sorted(other.items()) if o['status'] in ('reversal', 'reversal_or_gap')]),
            'notes': ['T186 ready-now card from a local dated table; outcome-blind (no outcome value read).',
                      'Date basis: ' + cfg['prov'],
                      'Overlap: other cards on the same outcome series share the control pool; the cards are not independent of each other.'] + cfg.get('extra_notes', []),
            'l1_cross_reference': [], 'disposition': 'ready', 'first_order_class': 'first_order', 'disposition_reason': '',
        }
        json.dump(rec, open(OUT / (cfg['slug'] + '.json'), 'w'), indent=1, ensure_ascii=False)
        cohorts = sorted(set(adopt_in.values()))
        summary.append((cfg['slug'], len(adopt_in), len(never), len(other), cohorts[:3], cohorts[-2:]))
        print(cfg['slug'], 'treated', len(adopt_in), 'never', len(never), 'other', len(other), sorted(adopt_in.items(), key=lambda t: t[1]))


main()
