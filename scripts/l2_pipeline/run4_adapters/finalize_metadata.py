"""Replay the archived finalize_metadata recipe with explicit local input roots.

This is a one-shot migration/replay utility, not S0L intake. It preserves the
original recipe and writes run4 metadata only when main/run is explicitly called.
Use a separate repository copy for historical replay; S0L uses the active collector.
"""
from pathlib import Path
import argparse

import json, pathlib, hashlib, collections

def run(repo_root, research_root):
    R = pathlib.Path(repo_root)
    L = R / 'data/l2_pipeline/literature'
    M = R / 'data/l2_pipeline/cards/run4/_meta'
    J = R / 'data/l2_pipeline/jobs/run4'

    def sha(p):
        return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

    def save(p, x):
        p.write_text(json.dumps(x, indent=2, ensure_ascii=False) + '\n')
    reg = json.load(open(L / 'registry.json'))
    downloads = json.load(open(L / 'DOWNLOADS.json'))
    confirmed = {'dee_grabowski_morrisey_2005_gdl', 'depasquale_stange_2016', 'family_planning_income_waivers'}
    for r in reg['studies']:
        r['completion']['full_record_hub_check_completed'] = r['study_id'] in confirmed
        r['completion']['s1_certified'] = False
        r['completion']['missing_adoption_or_uncertainty'] = not (r['completion']['paper_adoption_rows_located'] and r['completion']['published_estimate_with_se_or_ci'])
        r['completion']['completeness_note'] = 'Paper citation, definition, primary population/data, complete reported timing and exact uncertain estimate/source locators checked; independent official legal audit pending' if r['study_id'] in confirmed else 'Partial bibliography record, not promoted as one of60 fully verified records; see source-version and gaps fields'
        if r['study_id'] == 'dee_grabowski_morrisey_2005_gdl':
            r['published_result_roles'] = {'0': 'Model3 coefficient; paper text calls5.6% preferred but citesModel4 inconsistently', '1': 'Table4Model4 including state-specific trends; retain inconsistency, no silent substitution'}
        if r['study_id'] == 'depasquale_stange_2016':
            r['published_result_roles'] = {'0': 'Employment co-primary, not the selected hours construct', '1': 'Continuous exposure nurse-only hours DiD', '2': 'Continuous exposure hours DDD', '3': 'Binary membership nurse-only hours DiD', '4': 'Binary membership hours DDD'}
    reg['counts'].update(full_record_hub_checks_completed=len(confirmed), full_record_shortfall=60 - len(confirmed), full_record_target_met=False)
    reg['status'] = 'INCOMPLETE_INTAKE_NOT_30_PLUS_6_READY'
    reg['scan_disclosure'] = {'skill': 'detecting-indirect-prompt-injection', 'local_method': 'Source-specific normalized heuristic initial checks; flags/limitations retained per entry/receipt', 'dedicated_ml_scan_performed': False, 'reason': 'llm_guard and transformers unavailable', 'web_only_sources': 'untrusted browser/search/tool data; no separate local scan claimed', 'clean_scan_is_not_safety_proof': True}
    reg['research_subagents_used'] = 3
    reg['pipeline_model_calls'] = 0
    reg['external_paid_llm_api_calls'] = 0
    lookup = {d['sha256']: d for d in downloads['files']}
    retained = []
    for p in sorted((J / 'source_archive').rglob('*')):
        if not p.is_file():
            continue
        digest = sha(p)
        origin = lookup.get(digest)
        retained.append({'path': str(p), 'size_bytes': p.stat().st_size, 'sha256': digest, 'copy_not_new_download': True, 'derived_text_not_download': origin is None, 'original_download_path': origin['path'] if origin else None, 'url': origin['url'] if origin else None, 'license': origin['license'] if origin else 'Derived text from matched statutory source; see sibling original and l1_text provenance'})
    downloads['retained_task_artifacts'] = retained
    downloads['scan_disclosure'] = reg['scan_disclosure']
    save(L / 'DOWNLOADS.json', downloads)
    reg['downloads_manifest_sha256'] = sha(L / 'DOWNLOADS.json')
    save(L / 'registry.json', reg)
    checks = []
    for r in reg['studies']:
        checks.append({'study_id': r['study_id'], 'citation_url': r['citation_url'], 'doi': r.get('doi'), 'full_record_completed': r['completion']['full_record_hub_check_completed'], 'adoption_status': r['adoption']['status'], 'reported_rows': len(r['adoption']['states']), 'all_reported_adoption_years_numeric': bool(r['adoption']['states']) and all((type(z.get('year')) is int for z in r['adoption']['states'])), 'has_published_uncertainty': r['completion']['published_estimate_with_se_or_ci'], 'source_version_gaps': r.get('gaps', []), 's0_status': r['run4_selection_status']})
    save(L / 'FULL_RECORD_CHECKLIST.json', {'target': 60, 'fully_checked': 3, 'shortfall': 57, 'rule': 'A DOI or one located row is insufficient. Paper source-version, full reported timing scope and exact estimate uncertainty/units must all be checked; remaining records are candidates.', 'records': checks})
    save(M / 'RUN4_GAPS.json', {'status': 'INCOMPLETE', 'literature_card_target': 30, 'accepted_literature_cards': 3, 'literature_card_shortfall': 27, 'registry_full_record_target': 60, 'full_records_checked': 3, 'registry_full_record_shortfall': 57, 'bibliography_retained': len(reg['studies']), 'l1_cards': 6, 'l1_parked': 6, 's1_certified': 0, 'main_blockers': ['Full published timing tables or exact SE/CI not available/located for remaining papers', 'Published first-order target unavailable in approved connectors; employment or substitution outcomes cannot replace it', 'Paper legal windows leave earliest analyzed cohort fewer than3pre or fewer than6states with2strictpost periods', 'Current-stock records do not certify historical zeros or withdrawals', 'Existing primary-outcome overlap excludes bicyclehelmet/socialhost; changing units/age does not create_fo', 'Published-versus-preprint numeric versions, corrected erratum and conception/calendar clocks require explicit harmonization'], 'speed65_candidate': 'Bounded governmental history is viable, but paper only gives aggregate timing; individual reported-paper clock requirement remains unmet', 'expected_frozen_yield': {'guaranteed': 0, 'current_accepted_literature_conditional_upper_bound': 3, 'not_a_sign_based_forecast': True}, 'no_actual_dispatch_or_fitting': True})
    print(json.dumps({'registry': reg['counts'], 'downloads': downloads['file_count'], 'download_bytes': downloads['total_bytes'], 'retained_artifacts': len(retained)}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--research-root', type=Path, required=True, help='Archived run4 source bundle containing health/labor/transport or l1_* inputs')
    args = parser.parse_args(argv)
    run(args.repo_root.resolve(), args.research_root.resolve())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
