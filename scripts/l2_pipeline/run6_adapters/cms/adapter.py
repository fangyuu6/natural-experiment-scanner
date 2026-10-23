"""Replay scanned official CMS policy pages; no outcome data or interpolation."""
import hashlib
import html
import json
from scripts.l2_pipeline.run6_adapters.paths import require
from pathlib import Path
import re

from scripts.l2_pipeline.policy_tracker_extra import _rows, STATE_NAMES

from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT / 'cms'
RIGHTS = 'US federal government authored material; public domain in US (17 USC105); third-party materials excluded'


def write(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main(argv=None):
    import argparse
    global ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=ROOT)
    args=parser.parse_args(argv)
    ROOT=args.data_root.resolve()
    downloads = json.loads((ROOT / 'DOWNLOADS.json').read_text())
    byname = {Path(x['path']).name: x for x in downloads if 'path' in x}
    source = byname['cms_eligibility_current.html']
    raw = Path(source['path']).read_bytes()
    rows = _rows(raw)
    states = []
    for row in rows:
        if not row:
            continue
        name = re.sub(r'\d+$', '', row[0]).strip()
        if name in STATE_NAMES:
            states.append(dict(unit=STATE_NAMES[name], raw_cells=row,
                               locator='State Medicaid, CHIP and BHP Income Eligibility Standards; ' + name))
    require(len(states) == 51 and len({x['unit'] for x in states}) == 51, 'Run6 validation failed: cms/adapter.py:33')
    write('ELIGIBILITY_SNAPSHOT.json', dict(source_url=source['url'], raw_source_sha256=source['sha256'],
        as_of='2023-12-01', states=states, complete_snapshot=True, complete_change_history=False,
        repeals_included=False, no_historical_zeros_inferred=True))
    bhp = byname['cms_bhp.html']
    text = html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', Path(bhp['path']).read_text())))
    anchors = ['Minnesota has implemented a BHP since January 1, 2015',
               'Oregon has implemented a BHP since July 1, 2024',
               'initially began on April 1, 2015', 'suspended on April 1, 2024',
               'reinstate its BHP effective July 1, 2026']
    require(all(a in text for a in anchors), 'Run6 validation failed: cms/adapter.py:43')
    events = [dict(unit=u, effective_date=d, event=e, locator='Basic Health Program Implementation Experiences paragraph')
        for u,d,e in [('MN','2015-01-01','implementation'), ('NY','2015-04-01','implementation'),
                      ('NY','2024-04-01','suspension'), ('OR','2024-07-01','implementation'),
                      ('DC','2026-01-01','implementation'), ('NY','2026-07-01','reinstatement_approved')]]
    # A narrative roster records dated changes; it does not certify all annual
    # nonadoption cells or no interruption in every state. Preserve unknown.
    annual = []
    for unit in sorted(STATE_NAMES.values()):
        for year in range(2010, 2025):
            annual.append(dict(unit=unit, year=year, active=None,
                locator='CMS BHP narrative: no explicit all-state annual stock; ' + unit + '/' + str(year)))
    policy = dict(tracker_id='cms_bhp', policy_id='cms_basic_health_program',
        policy_family='basic_health_program', title='Basic Health Program', domain='Insurance',
        definition='State Basic Health Program coverage for eligible residents generally between 133 and 200 percent FPL',
        source_url=bhp['url'], source_path=bhp['path'], sha256=bhp['sha256'], size_bytes=bhp['size_bytes'],
        license=RIGHTS, as_of='2026-10-09 retrieval; narrative includes 2026 changes',
        coverage=dict(start_year=2010,end_year=2024), states=sorted(STATE_NAMES.values()),
        annual_rows=annual, events=events, history_complete=False, repeals_included=True,
        repeal_scope='Explicit New York suspension and approved reinstatement retained; no all-state repeal certification',
        source_check=bhp['scan'],
        outcome=dict(connector='acs1_health_insurance', measure_id='health_insurance_coverage_pct',
            target_outcome='health_insurance_coverage_pct', relation='closest_proxy',
            proxy_justification='Any-insurance coverage in ages26–34 measures insurance receipt; higher-income residents and other insurance types dilute BHP coverage, requiring purpose and population review.',
            target_population_gap=True, target_population_description='BHP-eligible lawfully present residents generally133–200 percent FPL',
            population_description='Civilian noninstitutionalized ages26–34, all incomes, all insurance types',
            population_filters={'age_group':'26-34','sex':'all'},years=[2008,2024], outcome_unit='percent insured',
            measurement_url='https://www.census.gov/programs-surveys/acs/data.html',
            evidence_locator='ACS B27001 age26–34 any-insurance coverage; no BHP eligibility/income isolation'))
    write('POLICIES.json', [policy])
    maine = byname['cms_maine_spa_list.html']
    write('SOURCE_CATALOG.json', [dict(tracker_id='cms_aca_medicaid_expansion', source_url=source['url'],
        coverage='51-jurisdiction eligibility snapshot, 2023-12-01; no complete annual history',
        as_of='2023-12-01',definition='ACA adult expansion group MAGI eligibility; not all waivers/pre-ACA adult coverage',
        history_complete=False, completeness_verdict='SNAPSHOT_ONLY', repeals_included=False,
        disposition='SOURCE_SUPPLEMENT_EXISTING_DESIGN', existing_design='medicaid_expansion',
        official_clock_evidence=dict(url=maine['url'], path=maine['path'], sha256=maine['sha256'],
            locator='ME-18-0006 adult eligibility: effective2018-07-02, approved2019-04-03; ME-18-0031 benefits: effective2018-10-01',
            implementation_date_not_inferred_from_approval_or_retroactive_eligibility=True),
        kff_cross_reference='Existing run5 KFF implementation roster retained read-only; cessation history still uncertified'),
        dict(tracker_id='cms_bhp',source_url=bhp['url'],as_of=policy['as_of'],coverage=policy['coverage'],
             definition=policy['definition'],history_complete=False,completeness_verdict='DATED_NARRATIVE_NOT_ALL_STATE_HISTORY',
             repeals_included=True,repeal_scope=policy['repeal_scope'],observed_events=events,
             disposition='PARK_HISTORY_INCOMPLETE; fewer than ten implementation states')])
    write('REPORT.json',dict(raw_downloads=len(byname), outcome_values_read=0,outcome_association_calls=0,
                            complete_histories=0,official_snapshot_states=len(states)))


if __name__ == '__main__':
    main()
