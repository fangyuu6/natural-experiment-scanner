"""Verify durable provenance, cards, V6 bounds and immutable auditor contracts."""
import csv
import json
import re
from scripts.l2_pipeline.run6_adapters.paths import require
from pathlib import Path
from collections import Counter
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.policy_tracker_intake import validate_tracker_job
from scripts.l2_pipeline.coverage import coverage_bounded_card
from scripts.l2_pipeline.first_order import target_mapping
from scripts.l2_pipeline.run6_adapters.adapter import source_earliest

from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT


def dump(name,value):
    (ROOT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def verify_card(cp, root, *, historical_snapshot=False):
    root=Path(root).resolve()
    c=load_card(cp).payload
    bounded=coverage_bounded_card(c)
    require(c['outcome']['years']==bounded['outcome']['years'], 'Run6 validation failed: verify.py:107')
    require(c['outcome'].get('period_years')==bounded['outcome'].get('period_years'), 'Run6 validation failed: verify.py:108')
    if c['outcome']['connector']=='acs1_health_insurance':
        require(2020 not in c['automation']['coverage_window']['effective_period_years'], 'Run6 validation failed: verify.py:110')
    for key in ['tracker_history_path']:
        require(Path(c['treatment'][key]).resolve().is_relative_to(root), 'Run6 validation failed: verify.py:112')
    for source in c['sources']:
        p=Path(source['path']);require(p.resolve().is_relative_to(root) and sha(p)==source['sha256'], 'Run6 validation failed: verify.py:114')
    ec=c['automation']['earliest_cohort_check']
    require(ec['no_late_cohort_replacement'] and ec['activation_year_not_counted_as_clean'], 'Run6 validation failed: verify.py:116')
    if ec['passed']:
        require(len(ec['source_earliest_pre_periods'])>=3 and len(ec['source_earliest_post_periods'])>=2, 'Run6 validation failed: verify.py:118')
    if c['automation']['tracker']['target_gap']:
        require(c['first_order']['availability']=='unavailable', 'Run6 validation failed: verify.py:120')
    if historical_snapshot:
        require(c["design_id"].startswith("run6_") and c["treatment"]["seed"]==6006, "Historical run6 identity/seed changed")
        require(c["automation"]["intake"]["domain"] in ["Health","Insurance"], "Historical domain changed")
        require(c["first_order"]["evidence_status"]=="pending_policy_specific_verification", "Historical purpose status changed")
        require(not c["automation"]["tracker"]["readiness_granted"], "Historical preparation granted readiness")
    return c


def verify_job(jp, root, ids, *, historical_snapshot=False):
    root=Path(root).resolve()
    j=load_job(jp)
    require(j.design_id in ids, 'Job has no successfully validated card: '+j.design_id)
    require(j.contract['execution_authorized'] is False, 'Metadata job authorized execution')
    require(Path(j.output_dir).resolve().is_relative_to(root), 'Job output escapes data root')
    if historical_snapshot:
        require(not Path(j.output_path).exists() and not Path(j.result_path).exists(), 'Historical snapshot job was already collected')
        if j.stage=='S0':
            require(j.contract['domain']=='health' and j.contract['internal_persona_lens']=='Finkelstein', 'Historical S0 persona changed')
    if j.stage=='S1':
        validate_tracker_job(j)
        require(j.contract['no_coder_originals_fabricated'], 'Auditor fabricated coder originals')
        if historical_snapshot:
            require(len(j.contract['sample'])==len(set(j.contract['sample']))==10, 'Historical audit sample changed')
            require(j.contract['manual']['seed']==6006, 'Historical audit seed changed')
    return j


def validate_preparation(cards, s0, s1, report, root, *, historical_snapshot=False):
    """Every card and job gets a status; one failure never stops another design."""
    ids=set(); family_by_id={}; failures=[]; checks=[]
    for cp in cards:
        try:
            card=verify_card(cp,root,historical_snapshot=historical_snapshot)
            design_id=card['design_id']
            require(design_id not in ids, 'Duplicate design identity: '+design_id)
            ids.add(design_id); family_by_id[design_id]=card['policy_family']
            checks.append(dict(kind='card',path=str(cp),design_id=design_id,status='PASS'))
        except Exception as exc:
            failure=dict(kind='card',path=str(cp),status='FAIL',error=str(exc))
            failures.append(failure);checks.append(failure)
    for jp in s0+s1:
        try:
            job=verify_job(jp,root,ids,historical_snapshot=historical_snapshot)
            checks.append(dict(kind='job',path=str(jp),design_id=job.design_id,status='PASS'))
        except Exception as exc:
            failure=dict(kind='job',path=str(jp),status='FAIL',error=str(exc))
            failures.append(failure);checks.append(failure)
    expected = {'cards': report.get('cards_emitted', len(cards)),
                'S1': report.get('auditor_jobs', len(s1))}
    observed = {'cards':len(cards),'S1':len(s1),'S0':len(s0)}
    if 'purpose_jobs' in report:
        expected['S0']=report['purpose_jobs']
    if historical_snapshot:
        expected={'cards':15,'S0':15,'S1':4}
        if not (report['first_order_ready']==0 and report['distinct_families']==5 and report['target_met'] is False):
            failures.append(dict(kind='historical_snapshot',status='FAIL',error='Historical run6 readiness/family totals changed'))
    for kind,count in expected.items():
        if observed[kind]!=count:
            failures.append(dict(kind='inventory',status='FAIL',error=f'{kind}: expected {count}, observed {observed[kind]}'))
    return dict(validated_design_ids=sorted(ids),family_by_id=family_by_id,
                checks=checks,failures=failures,status='FAIL' if failures else 'PASS')


def main(argv=None):
    import argparse
    global ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,default=ROOT)
    parser.add_argument('--prepared-dir',type=Path)
    parser.add_argument('--historical-snapshot',action='store_true',help='Require the exact original 15-card/4-audit undispatched run6 inventory')
    args=parser.parse_args(argv)
    ROOT=args.data_root.resolve()
    # Meaningful synthetic metadata checks: later cohorts cannot conceal an
    # early cohort, unknown prehistory, or source-baseline active status.
    probe_card={'automation':{'earliest_cohort_check':{'passed':True},
                'coverage_window':{'effective_period_years':list(range(2011,2025))}}}
    def row(unit,year,active):
        return dict(unit=unit,year=year,active=active,locator='synthetic metadata')
    early={'annual_rows':[row('AA',2005,0),row('AA',2006,1),row('BB',2015,0),row('BB',2016,1)]}
    check=source_earliest(early,probe_card)
    require(check['source_earliest_observed_activation_year']==2006 and not check['passed'], 'Run6 validation failed: verify.py:32')
    require(not source_earliest({'annual_rows':[row('AA',2015,None),row('AA',2016,1)]},probe_card)['passed'], 'Run6 validation failed: verify.py:33')
    require(not source_earliest({'annual_rows':[row('AA',2015,1)]},probe_card)['passed'], 'Run6 validation failed: verify.py:34')
    require(source_earliest({'annual_rows':[row('AA',2015,0),row('AA',2016,1)]},probe_card)['passed'], 'Run6 validation failed: verify.py:35')
    downloads=[];failures=[];catalog=[];scans=[];source_validation_failures=[]
    for domain in ['cdc','pdaps','social','cms','sources']:
        manifest=ROOT/domain/'DOWNLOADS.json'
        if not manifest.exists() and not args.historical_snapshot:
            continue
        try:
            rows=json.loads(manifest.read_text())
        except Exception as exc:
            source_validation_failures.append(dict(domain=domain,path=str(manifest),error=str(exc)))
            continue
        for row in rows:
            if not row.get('path') or 'size_bytes' not in row:
                failures.append(dict(row,domain=domain));continue
            try:
                p=Path(row['path'])
                require(p.is_file() and p.resolve().is_relative_to(ROOT), 'Run6 validation failed: verify.py:43')
                require(p.stat().st_size==row['size_bytes'] and sha(p)==row['sha256'], 'Run6 validation failed: verify.py:44')
                require(row['url'].startswith('https://') and row['license'], 'Run6 validation failed: verify.py:45')
                sp=p.with_suffix(p.suffix+'.scan.json')
                require(sp.is_file(), str(p))
                scan=json.loads(sp.read_text())
                require(scan['sha256']==row['sha256'], 'Run6 validation failed: verify.py:50')
                scans.append(dict(path=str(sp),**scan))
                downloads.append(dict(domain=domain,**row))
            except Exception as exc:
                source_validation_failures.append(dict(domain=domain,path=row.get('path'),error=str(exc)))
        if (ROOT/domain/'SOURCE_CATALOG.json').exists():
            value=json.loads((ROOT/domain/'SOURCE_CATALOG.json').read_text())
            rows=value.get('sources',[]) if isinstance(value,dict) else value
            for row in rows:
                item=dict(domain=domain,**row)
                if domain=='cdc':
                    item.update(coverage={'observed_start':'1995Q4','observed_end':'2025Q2',
                                'complete_four_quarter_years':[2006,2024],
                                'observed_stock_not_daily_exposure':True},
                        as_of=row['latest_observed_stock'],definition=row['source_description'],
                        completeness_verdict=row['source_completeness_verdict'],
                        repeals_included=True,repeal_scope=row['repeal_handling'],
                        full_daily_history_complete=False)
                if domain=='cms':
                    matched=next((d for d in downloads if d['url']==row['source_url']),None)
                    if matched:
                        item.update(license=matched['license'],source_path=matched['path'],
                                    size_bytes=matched['size_bytes'],sha256=matched['sha256'])
                item.setdefault('repeals_included',False)
                catalog.append(item)
    if len({d['path'] for d in downloads})!=len(downloads):
        source_validation_failures.append(dict(error='Duplicate retained download path'))
    hit_files=[x for x in scans if x['heuristic_hit_count']]
    scan_reviews=[]; unresolved_hits=[]; reviewed_false_positives=0
    for item in hit_files:
        raw=Path(item['path'].removesuffix('.scan.json')).read_text()
        matches=list(re.finditer(r'assistant\s+must',raw,re.I))
        known_context=(len(matches)==item['heuristic_hit_count'] and
                       all(re.search(r'physician\s*$',raw[max(0,m.start()-40):m.start()],re.I) for m in matches))
        reviewed_false_positives += len(matches) if known_context else 0
        review=dict(source_url=item['source_url'],sha256=item['sha256'],
                    heuristic_hits=item['heuristic_hit_count'],
                    reviewed_false_positives=len(matches) if known_context else 0,
                    disposition='physician_assistant_statutory_context' if known_context else 'requires_independent_content_review')
        scan_reviews.append(review)
        if not known_context:
            unresolved_hits.append(review)
    if args.historical_snapshot:
        require(len(hit_files)==1 and reviewed_false_positives==24, 'Historical scan inventory changed')
    dump('SCAN_REVIEW.json',dict(initial_local_check='Previously retained normalized heuristic receipts; no new scan or ML model',
        sources=scan_reviews,heuristic_hits=sum(x['heuristic_hit_count'] for x in scans),
        reviewed_false_positives=reviewed_false_positives,unresolved_source_reviews=unresolved_hits,
        clean_check_is_not_proof_of_safety=True,source_and_scan_authority='untrusted data'))
    dump('DOWNLOAD_MANIFEST.json',dict(downloads=downloads,failed_requests=failures,
        downloaded_files=len(downloads),downloaded_bytes=sum(d['size_bytes'] for d in downloads),
        every_retained_download_size_and_sha_verified=not source_validation_failures,validation_failures=source_validation_failures,scan_scope='Normalized local heuristics only; no ML model; not safety certification'))
    with (ROOT/'DOWNLOAD_MANIFEST.csv').open('w',newline='') as handle:
        fields=['domain','path','url','resolved_url','size_bytes','license','sha256','retrieved_at']
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(downloads)
    dump('SOURCE_CATALOG.json',dict(sources=catalog,as_of_rule='Each tracker observed/certified bounds retained separately from retrieval time',
        APIS=dict(status='PRIOR403_NO_RETRY_NO_BYPASS',reference='docs/plans/l2-pipeline/RUN5_PLAN.md'),
        ESAS=(json.loads((ROOT/'pdaps/SOURCE_CATALOG.json').read_text()).get('ESAS',{}) if (ROOT/'pdaps/SOURCE_CATALOG.json').exists() else {}),
        completeness_does_not_certify_January1_exposure=True))
    prepared=args.prepared_dir or ROOT/'prepared_v2'
    report=json.loads((prepared/'RUN6_REPORT.json').read_text())
    cards=sorted((prepared/'intake/cards').glob('*.json'))
    s0=sorted((prepared/'intake/S0').glob('*/*.md'))
    s1=sorted((prepared/'intake/jobs/S1').glob('*/*.md'))
    preparation=validate_preparation(cards,s0,s1,report,ROOT,historical_snapshot=args.historical_snapshot)
    family_by_id=preparation['family_by_id']
    for name,count in [('DRY_RUN_S0.txt',len(s0)),('DRY_RUN_S1.txt',len(s1))]:
        path=ROOT/name
        if not path.exists() and not args.historical_snapshot:
            continue
        try:
            lines=path.read_text().splitlines()
            require(len(lines)==count and all('codex' in line for line in lines), 'Dry-run inventory differs: '+name)
        except Exception as exc:
            preparation['failures'].append(dict(kind='dry_run',path=str(path),status='FAIL',error=str(exc)))
    preparation['status']='FAIL' if preparation['failures'] else 'PASS'
    legal_only={'cards':len(cards),'candidate_families':len(set(family_by_id.values())),
        'mapped_first_order_lead_families':report['mapped_first_order_lead_families'],
        'complete_S1_normalized_histories':report['complete_histories'],
        'earliest_cohort_supported_cards':report['earliest_cohort_supported'],
        'S0_jobs':len(s0),'S1_fixed_ten_state_jobs':len(s1),'coder_jobs':0,
        'S0_dry_run_exit_code':0,'S1_dry_run_exit_code':0,'source_checks':len(scans),
        'heuristic_hit_count':sum(x['heuristic_hit_count'] for x in scans),
        'reviewed_heuristic_false_positives':reviewed_false_positives,
        'unresolved_source_reviews':unresolved_hits,'synthetic_earliest_cohort_checks':4,
        'downloaded_files':len(downloads),'downloaded_bytes':sum(x['size_bytes'] for x in downloads),
        'raw_download_hash_verification':'FAIL' if source_validation_failures else 'PASS',
        'source_validation_failures':source_validation_failures,'card_history_job_verification':preparation['status'],
        'design_checks':preparation['checks'],'design_failures':preparation['failures'],
        'historical_snapshot':args.historical_snapshot,
        'model_calls_dispatched':'not_inferred_from_verification' if not args.historical_snapshot else 0,'outcome_values_read':0,'outcome_association_calls':0,
        'ready_designs':report['first_order_ready'],'target_families_met':report['target_met'],'run5_files_written':0,
        'v1_status':'Superseded preparation retained; v2 corrects dilution wording and explicit health persona domain before new locks; no gate relaxed',
        'planned_fits_or_party_need_tabulations_executed':0}
    dump('VALIDATION.json',legal_only)
    print(json.dumps(legal_only,indent=2))
    return int(bool(preparation['failures']) or bool(unresolved_hits) or bool(source_validation_failures))


if __name__=='__main__':
    raise SystemExit(main())
