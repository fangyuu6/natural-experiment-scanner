"""C58 allowlisted blind-history transport; archived packets remain evidence."""
from copy import deepcopy

CARD_FIELDS = {'design_id', 'version', 'mechanism', 'unit'}
PLAN_FIELDS = {'type', 'design_id', 'attempt_id', 'mechanism', 'universe', 'target_window',
    'clock', 'enforcement_rule', 'navigation_candidates', 'evidence_standard', 'research_scope',
    'requires_before_s1', 's1_ready', 'execution_authorized', 'predicate_clarification',
    'fresh_audit', 'coverage_rule', 'interruption_rule'}


def card(value):
    return {k: deepcopy(v) for k, v in value.items() if k in CARD_FIELDS}


def plan(value):
    return {k: deepcopy(v) for k, v in value.items() if k in PLAN_FIELDS}


def is_history(job):
    return job.stage == 'S1' and job.role in {'A', 'B'} and job.contract.get('jobs_kind') == 'official_history_collection'


def answer_bearing(contract):
    """Find screening treatment metadata recursively, including unknown nesting."""
    keys = {'screen_expected_support', 'cohort_years', 'cohorts', 'treated_states',
            'treated_units', 'treated_list', 'coded_years', 'adoption_years', 'adoption_dates',
            'screening_adoptions', 'screening_adoption', 'adoption_screen', 'adopters', 'treatment_years', 'start_periods'}
    def visit(value):
        if isinstance(value, dict):
            return any(k.lower() in keys or visit(v) for k, v in value.items())
        return isinstance(value, list) and any(visit(v) for v in value)
    return visit(contract.get('card', {}))


def check(job):
    if not is_history(job): return
    if set(job.contract.get('card', {})) - CARD_FIELDS or answer_bearing(job.contract):
        raise ValueError('C58 history packet contains non-allowlisted card metadata')
    from pathlib import Path
    import json
    for item in job.inputs:
        if Path(item['path']).name == 'COLLECTION_PLAN.json':
            value = json.loads(Path(item['path']).read_text())
            if set(value) - PLAN_FIELDS or answer_bearing({'card':value}):
                raise ValueError('C58 history plan contains non-allowlisted metadata')


def clean_pending(path, root):
    """Fresh immutable packet with retained prior-byte binding; no dispatch."""
    from pathlib import Path
    import hashlib
    from .jobs import load_job
    from .history import emit_history_job
    old = load_job(Path(path))
    if not is_history(old): return old.job_path
    try: check(old)
    except ValueError: pass
    else: return old.job_path
    digest = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()
    existing=Path(root)/digest/'S1'/old.design_id/(old.role+'.c58_clean.md')
    if existing.exists():
        job=load_job(existing)
        if job.contract.get('previous_packet_hashes',{}).get(str(Path(old.job_path)))!=old.job_sha256:
            raise ValueError('Existing clean history predecessor changed')
    else:
        job = emit_history_job(path, jobs_root=Path(root)/digest, attempt_id='c58_clean')
    check(job)
    return job.job_path


def official_locator(row, sources):
    """An independent locator must resolve to an official source in this answer."""
    indexed = {s.get('source_id'): s for s in sources}
    for ref in row.get('evidence_refs', []):
        source = indexed.get(ref.get('source_id'), {})
        kind = source.get('source_type', source.get('type', ''))
        if (kind in {'official', 'official_statute', 'official_code', 'official_history',
                     'official_session_law', 'official_order', 'official_source'}
                and source.get('url') and ref.get('locator') and ref.get('quote')):
            return True
    return False


def exact_copy(values, screening, universe):
    """Both complete state histories must exactly reproduce the screening map.

    Missing or aggregate-only screening data cannot establish exact copying.
    No outcome values or associations are read.
    """
    adopters = screening.get('adopters', {})
    never = set(screening.get('never_through_coverage_end', []))
    if set(adopters) | never != set(universe) or set(adopters) & never: return False
    if len(values) != 2: return False
    for value in values:
        rows = value.get('records', [])
        if len(rows) != len(universe) or {r.get('unit') for r in rows} != set(universe): return False
        for row in rows:
            unit = row['unit']
            if official_locator(row, value.get('sources', [])): return False
            if unit in adopters:
                # Operation year, not the subsequent first-full annual label.
                if row.get('status') != 'treated' or row.get('best_evidence_start_year') != adopters[unit]: return False
            elif row.get('status') != 'never_treated': return False
    return True
