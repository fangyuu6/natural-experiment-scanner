"""Public KFF/IIHS table adapters; metadata and legal dates only, no outcomes.

These parsers preserve tracker dates as reported. A current-law snapshot never
certifies a complete change/repeal history or grants S1 readiness.
"""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser

KFF_URL = ('https://www.kff.org/affordable-care-act/state-indicator/'
           'state-activity-around-expanding-medicaid-under-the-affordable-care-act/')
KFF_TERMS = 'https://www.kff.org/about-us/permissions-citations-reprints/'
IIHS_URL = 'https://www.iihs.org/research-areas/seat-belts/seat-belt-law-table'
IIHS_TERMS = 'https://www.iihs.org/copyright-information-and-privacy-policy'

STATE_NAMES = dict(zip(
    ('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|'
     'Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|'
     'Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|'
     'New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|'
     'Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|'
     'West Virginia|Wisconsin|Wyoming').split('|'),
    ('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH '
     'NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY').split()))


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th') and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _rows(raw):
    parser = _Rows()
    parser.feed(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
    return parser.rows


def _date(value):
    for pattern in ('%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError('Tracker effective date is not explicit: ' + value)


def _publication_month(raw):
    text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
    match = re.search(r'>\s*((?:January|February|March|April|May|June|July|August|September|'
                      r'October|November|December)\s+20\d{2})\s*<', text)
    if not match:
        return None
    return datetime.strptime(match[1], '%B %Y').strftime('%Y-%m')


def parse_kff_expansion(raw):
    """All-state implementation roster; do not infer pre-ACA coverage or waivers."""
    rows = _rows(raw)
    expected = ['Location', 'Status of Medicaid Expansion Decision', 'Expansion Implementation Date']
    if not any(row[:3] == expected for row in rows):
        raise ValueError('KFF expansion table schema changed')
    states = {}
    for row in rows:
        if not row or row[0] not in STATE_NAMES:
            continue
        if len(row) != 5:
            raise ValueError('KFF expansion row schema changed')
        unit = STATE_NAMES[row[0]]
        if unit in states:
            raise ValueError('Duplicate KFF state')
        if row[1] == 'Adopted':
            start = _date(row[2])
            if start < '2014-01-01':
                raise ValueError('Unexpected pre-ACA expansion date')
        elif row[1] == 'Not Adopted' and row[2] == 'N/A':
            start = None
        else:
            raise ValueError('Unknown KFF expansion status/date')
        states[unit] = dict(unit=unit, state=row[0], status=row[1], start_date=start,
            date_as_reported=row[2], ballot_initiative_as_reported=row[3],
            trigger_law_as_reported=row[4], source_url=KFF_URL,
            locator='Status of State Action table; ' + row[0] + ' row; implementation date column')
    if set(states) != set(STATE_NAMES.values()):
        raise ValueError('KFF all-state roster incomplete')
    return dict(tracker_id='kff_aca_medicaid_expansion', source_type='policy_tracker',
        states=list(states.values()), universe=sorted(states),
        events=[dict(unit=r['unit'], effective_date=r['start_date'], event='implementation',
                     source_url=r['source_url'], locator=r['locator'])
                for r in states.values() if r['start_date']],
        current_nonadopters=[r['unit'] for r in states.values() if r['start_date'] is None],
        definition='ACA adult Medicaid expansion through 138% FPL; tracker implementation date',
        complete_implementation_roster=True, complete_change_history=False,
        repeals_included=False, s1_certified=False,
        as_of=_publication_month(raw), as_of_precision='month',
        coverage={'start_year': 2014, 'end_year': None},
        coverage_status='complete initial-implementation roster; continuous state-year coverage uncertified',
        history_limit='Current status with cumulative implementation dates; no cessation or waiver change log. '
                      'Official audit must verify continuous binary ACA expansion status and Maine clock.',
        source_url=KFF_URL, terms_url=KFF_TERMS,
        license='KFF State Health Facts may be reproduced free with appropriate citation; copyrighted, '
                'no open license asserted; underlying restricted data exceptions remain applicable',
        first_order_mapping=dict(connector='acs1_health_insurance',
            target_outcome='health_insurance_coverage_pct', measure_id='health_insurance_coverage_pct',
            population_filters={'age_group': '26-34', 'sex': 'all'},
            relation='closest_proxy',
            proxy_justification='Any-insurance coverage among ages 26–34 includes Medicaid and other insurance '
                'and all incomes; no low-income filter exists in the B27001 connector. Purpose/population '
                'review must approve this broader direct coverage outcome before readiness.'))


def parse_iihs_seatbelts(raw):
    """Preserve initial/current primary dates without fabricating repeal history."""
    rows = _rows(raw)
    if not any(row[:3] == ['State', 'Initial effective date', 'Primary enforcement?'] for row in rows):
        raise ValueError('IIHS seat-belt table schema changed')
    states = {}
    for row in rows:
        if not row or row[0] not in STATE_NAMES.values():
            continue
        if len(row) != 9 or row[0] in states:
            raise ValueError('IIHS seat-belt row malformed/duplicated')
        dates = re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}', row[2])
        states[row[0]] = dict(unit=row[0], initial_effective_date=_date(row[1]) if row[1] != 'n/a' else None,
            primary_enforcement_as_reported=row[2], dated_enforcement_mentions=[_date(x) for x in dates],
            coverage_as_reported=row[3], source_url=IIHS_URL,
            locator='Seat belt law table; ' + row[0] + ' row; initial date and primary enforcement columns')
    if set(states) != set(STATE_NAMES.values()):
        raise ValueError('IIHS all-state roster incomplete')
    return dict(tracker_id='iihs_seatbelt_dates', source_type='policy_tracker',
        states=list(states.values()), universe=sorted(states), source_url=IIHS_URL, terms_url=IIHS_TERMS,
        complete_change_history=False, repeals_included=False, complete_list=False,
        as_of=_publication_month(raw), as_of_precision='month',
        coverage={'start_year': None, 'end_year': None},
        coverage_status='dated current-law snapshot; historical coverage window uncertified',
        disposition='PARK', reason='Current law table lacks complete repeal, reinstatement, and scope changes; '
            'Massachusetts 1986 repeal is not represented by its current initial date of 1994.',
        completeness_counterexample=dict(
            source_url='https://www.sec.state.ma.us/divisions/elections/research-and-statistics/balmresults_open.htm',
            locator='1986 election November 4; Question 5 Existing law which required use of seat belts; Outcome Repealed',
            access='public web search tool result', separate_local_scan=False,
            source_instructions_have_authority=False),
        license='IIHS copyright; limited noncommercial educational/personal download with attribution; '
            'repetitive redistribution and commercial use require written permission',
        first_order_mapping=dict(connector='fars_state', target_outcome='traffic_deaths_count',
            measure_id='traffic_deaths_count', population_linkage_pending=True))


def extra_histories(archive_dir):
    """Parse local, previously scanned bytes; this function performs no downloads."""
    from pathlib import Path
    root = Path(archive_dir)
    return [parse_kff_expansion((root / 'kff_expansion.html').read_bytes()),
            parse_iihs_seatbelts((root / 'iihs_seatbelts.html').read_bytes())]


def extra_policies(archive_dir):
    """Hub inventory schema; incomplete/duplicate histories stay explicitly parked.

    Post-implementation annual legal stock is unknown without cessation evidence.
    The full-date implementation event and candidate full-year clock are retained
    for future official auditing; neither becomes a fabricated annual history.
    """
    import json
    from pathlib import Path
    root = Path(archive_dir)
    inventory = json.loads((root / 'DOWNLOADS.json').read_text())
    source = next(x for x in inventory if x['url'] == KFF_URL)
    history = parse_kff_expansion((root / 'kff_expansion.html').read_bytes())
    annual = []
    events = []
    for row in history['states']:
        start = row['start_date']
        first_full = (int(start[:4]) + (start[5:] != '01-01')) if start else None
        if start:
            events.append(dict(unit=row['unit'], effective_date=start,
                first_full_year=first_full, locator=row['locator']))
        for year in range(2010, 2025):
            # Known absence precedes initial ACA implementation. Continued
            # operation afterward requires historical cessation evidence.
            active = 0 if year < 2014 or (start and year < int(start[:4])) else None
            annual.append(dict(unit=row['unit'], year=year, active=active,
                transition_year=bool(start and start[5:] != '01-01' and year == int(start[:4])),
                locator=row['locator'], status='current_roster_only_history_unknown'
                    if active is None else 'pre_initial_implementation'))
    policy = dict(tracker_id='kff', policy_id='medicaid_expansion',
        title='ACA Medicaid expansion implementation history',
        definition=history['definition'], source_type='policy_tracker',
        source_url=KFF_URL, source_path=source['path'], sha256=source['sha256'],
        size_bytes=source['size_bytes'], license=history['license'],
        as_of=history['as_of'], as_of_precision='month', retrieved_on=source['accessed_on'],
        coverage={'start_year': 2010, 'end_year': 2024},
        history_complete=False, repeals_included=False, states=history['universe'],
        annual_rows=annual, events=events,
        outcome=dict(connector='acs1_health_insurance', measure_id='health_insurance_coverage_pct',
            population_filters={'age_group': '19-25', 'sex': 'all'}, years=[2010, 2024],
            relation='closest_proxy', proxy_justification='Any insurance among ages 19–25 measures coverage '
                'with dilution from other eligibility/insurance pathways; no income restriction in B27001.'),
        disposition='PARK', blockers=['Existing medicaid_expansion prototype already registered '
            'first-order uninsured coverage via ACS B27016; no second-order _fo fork justified',
            history['history_limit']], prior_design_id='medicaid_expansion',
        prior_spec_metadata_ref='scripts/did_samples/examples/medicaid_expansion/spec.md:23',
        prior_outcome_role='first_order_coverage', prior_results_read=False,
        s1_certified=False, execution_authorized=False, outcome_values_read=0,
        outcome_association_calls=0, legal_date_selection_uses_outcome_sign=False)
    return [policy]
