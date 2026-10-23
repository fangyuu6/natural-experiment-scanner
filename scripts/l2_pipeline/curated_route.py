"""T219 (HUB_RULING_20261021_CURATED_HISTORY): the autopilot hook of the curated-database history route.

A design whose card declares ``automation.history_source`` (kind curated_database) goes
    history   : frozen mapping + pinned RAND workbook -> one row per state (CURATED_BUILD.json) -> seeded audit plan ->
                blind A/B history packets on the audit sample only (existing ``emit_history_job`` completion packets)
    reconcile : worker records vs database rows by script (classify / evaluate); replacements and the 5-state extension are
                further blind packets; pass -> C42 exclusions + RECONCILIATION.json; fail / inconclusive -> PARK (fresh coding)
    audit     : certified history files (DB_HISTORY_SOURCE.json, CERTIFIED_HISTORY.json, TREATMENT.json, S1 provenance)
    S1        : ``accept`` checks the files and returns the READY stage result.
A failed or inconclusive audit PARKs the design with kind CURATED_ROUTE; the route change back to fresh coding is a card
revision without ``history_source`` (hub, in a window). Nothing here opens an outcome, an estimate or a forecast.
The workers never see a database value: they get the card predicate and the unit list only (``curated_history.assert_blind``).

Q2 retry (HUB_RULING_20261023_PARK_REOPEN section 4, panel 5-0): the four designs in RETRY_DESIGNS that parked with CURATED_AUDIT_INCONCLUSIVE
(unverifiable share 1.0, no comparison ever happened) get ONE extra blind-audit round, as a NEW attempt under a logged receipt
(``data/l2_pipeline/curated/retry/<design>.json``, write-once, written before the requeue). The retry attempt re-derives the audit plan and must
reproduce the first audit's database sha256, mapping hash, plan, seed and sample exactly (no redraw, no mapping change); it skips the ledger claim
(the first audit's claim stays), emits its own packets under the round tags r1b, r2b, ... (never r1, r2), and evaluates those rounds alone with the
unchanged pass rule (``curated_history.evaluate``). A second inconclusive verdict is a FINAL park (PARKED_C83_FINAL.json): closed for good.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from . import curated_history as ch
from .storage import ensure_once, sha

MAPPING_DIR = 'data/l2_pipeline/curated/mappings'
LEDGER_DIR = 'data/l2_pipeline/curated/ledger'
XLSX = 'data/l4/rand_firearm_law_database/raw/TL-A243-2-v4 State Firearm Law Database 6.0.xlsx'
STATUS = 'SUPERSEDED_CURATED_ROUTE'
PARK_KIND = 'CURATED_ROUTE'
SCHEMA_BUILD = 'l2_curated_build_v1'
RETRY_RULE = 'Q2'
RETRY_RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261023_PARK_REOPEN.md'
RETRY_DIR = 'data/l2_pipeline/curated/retry'
RETRY_SUFFIX = 'b'
SCHEMA_RETRY = 'l2_curated_retry_v1'
SCHEMA_RETRY_RESULT = 'l2_curated_retry_result_v1'
RESUME_RULING = 'docs/plans/l2-pipeline/HUB_RULING_20261023_T287_CLOSURES.md'
RESUME_DIR = 'data/l2_pipeline/curated/resume'
SCHEMA_RESUME = 'l2_curated_resume_v1'
# T287 Q5 (panel 5-0): one design whose first audit claim never produced a verdict (workers lost); frozen like RETRY_DESIGNS.
RESUME_DESIGNS = frozenset({'b1020rn_state_preemption_punitive_firearm_homicide'})
INCONCLUSIVE = 'CURATED_AUDIT_INCONCLUSIVE'
SONNET_QUEUE = 'data/l2_pipeline/registry/SONNET_QUEUE'
# The four designs of the Q2 vote; frozen: a design outside this set never gets a second round.
RETRY_DESIGNS = frozenset({
    'b1020rn_campus_ccw_ban_violent_crime',
    'b1020rn_dv_firearm_removal_at_scene_firearm_homicide',
    'b1020rn_mental_health_commitment_prohibitor_firearm_homicide',
    'b1020rn_straw_purchase_law_firearm_homicide',
})
SWITCH_DESIGNS = (
    'b1020rn_campus_ccw_ban_violent_crime',
    'b1020rn_dv_firearm_removal_at_scene_firearm_homicide',
    'b1020rn_mental_health_commitment_prohibitor_firearm_homicide',
    'b1020rn_stalking_misdemeanor_firearm_restriction_firearm_homicide',
    'b1020rn_state_preemption_punitive_firearm_homicide',
    'b1020rn_straw_purchase_law_firearm_homicide',
    'b1020rn_violent_misdemeanor_firearm_restriction_firearm_homicide',
)


def _read(path):
    return json.loads(Path(path).read_text())


def _bind(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def declared(row) -> bool:
    """Cheap tick-time test. An unreadable card is simply 'not declared'; a malformed declaration is an error."""
    try:
        payload = _read(row['card'])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return ch.declaration(payload) is not None


# ---------------------------------------------------------------- mapping file
def mapping_path(repo, design_id):
    return Path(repo) / MAPPING_DIR / (design_id + '.json')


def check_frozen(spec):
    """R-CH 3: two independent mappers and a third reader signed the mapping before any audit. A draft is refused."""
    freeze = spec.get('freeze') or {}
    ids = [(freeze.get(k) or {}).get('id') for k in ('mapper_a', 'mapper_b', 'third_reader')]
    if freeze.get('state') != 'frozen' or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != 3:
        raise ch.Ineligible('mapping is not frozen by two independent mappers and a third reader (state: %s)' % freeze.get('state'))
    for k in ('mapper_a', 'mapper_b', 'third_reader'):
        if not (freeze[k].get('file_sha256') or '').strip() or not freeze[k].get('signed_utc'):
            raise ch.Ineligible('freeze entry %s lacks its file hash or time' % k)


def freeze(repo, final_path, mapper_a, mapper_b, third_reader, utc):
    """Hub tool after the two independent mapper runs and the third reader: write the frozen mapping (write-once) into
    ``data/l2_pipeline/curated/mappings/``. ``mapper_*`` = dict(id, file) naming each reader's own mapping file (hashed here);
    ``final_path`` = the reconciled mapping JSON (classes decided by the third reader). The three ids must differ."""
    spec = _read(final_path)
    entries = {}
    for key, who in (('mapper_a', mapper_a), ('mapper_b', mapper_b), ('third_reader', third_reader)):
        entries[key] = dict(id=who['id'], file_sha256=ch.file_sha(who['file']), signed_utc=utc)
    spec['freeze'] = dict(state='frozen', **entries)
    spec['mappers'] = [entries[k]['id'] for k in ('mapper_a', 'mapper_b', 'third_reader')]
    spec.pop('eligible', None)
    check_frozen(spec)
    ch.validate_mapping(spec)
    return ensure_once(mapping_path(repo, spec['design_id']), spec)


def load_spec(repo, payload):
    """The hash-bound, frozen mapping of this card; every binding to the card is checked."""
    decl = ch.declaration(payload)
    if decl is None:
        raise ValueError('No history_source declaration on the card')
    path = mapping_path(repo, payload['design_id'])
    if not path.is_file():
        raise ch.Ineligible('mapping file missing: %s' % path.name)
    spec = _read(path)
    if spec.get('eligible') is False:
        raise ch.Ineligible('mapping says not eligible: ' + str(spec.get('reason'))[:200])
    if ch.mapping_sha(spec) != decl['mapping_sha256']:
        raise ValueError('mapping file hash differs from the card declaration')
    check_frozen(spec)
    ch.validate_mapping(spec)
    t = payload['treatment']
    years = payload['outcome'].get('years') or payload['outcome'].get('period_years')
    bad = [name for name, ok in (
        ('design_id', spec.get('design_id') == payload['design_id']),
        ('policy_family', spec['policy_family'] == payload.get('policy_family')),
        ('mechanism', spec['mechanism'] == payload.get('mechanism')),
        ('universe', sorted(spec['universe']) == sorted(t['universe'])),
        ('window_start', bool(years) and spec['window_start'] == years[0]),
        ('coverage_end', str(spec['coverage_end_year']) == str(t['coverage_end'])[:4]),
        ('transition_year_drop', spec['transition_year_drop'] is True and payload.get('transition_year_drop') is True)) if not ok]
    if bad:
        raise ch.Ineligible('mapping does not match the card: ' + ', '.join(bad))
    return spec


# ---------------------------------------------------------------- build + plan (history action, step 1)
def prepare(pilot, row):
    """CURATED_BUILD.json (write-once): database rows -> history, audit plan, ledger claim. Raises Park when not eligible."""
    from .c42 import Park
    root = pilot.backend.root(row)
    target = root / 'CURATED_BUILD.json'
    if target.exists():
        return _read(target)
    payload = _read(row['card'])
    try:
        spec = load_spec(pilot.repo, payload)
        xlsx = pilot.repo / XLSX
        if ch.file_sha(xlsx) != spec['database']['xlsx_sha256']:
            raise ch.Ineligible('database workbook differs from the pinned version')
        history = ch.build_history(ch.load_rows(xlsx), spec)
    except ch.Ineligible as exc:
        raise Park('CURATED_INELIGIBLE: ' + str(exc) + '; fresh coding (card revision without history_source)',
                   dict(kind=PARK_KIND, route='fresh_coding', outcome_values_read=0)) from exc
    plan = ch.audit_plan(history, spec, payload['design_id'])
    receipt = retry_receipt(pilot.repo, row)
    resume = resume_receipt(pilot.repo, row)
    if receipt is None and resume is not None:
        # T287 Q5: the first audit's claim stays and never produced a verdict; this attempt must reproduce the same audit, never a new draw.
        ch.resume_claim(pilot.repo / LEDGER_DIR, spec, spec['universe'], payload['design_id'])
        same_audit(resume, spec, history, plan)
    elif receipt is None:
        ch.claim_audit(pilot.repo / LEDGER_DIR, spec, spec['universe'], payload['design_id'])
    else:
        # Q2 retry attempt: the first audit's ledger claim stays; this attempt must reproduce the same audit, never a new draw.
        same_audit(receipt, spec, history, plan)
    return deepcopy(_write_build(target, payload, spec, history, plan, row))


def _write_build(target, payload, spec, history, plan, row):
    value = dict(schema=SCHEMA_BUILD, rule=ch.RULING, design_id=payload['design_id'], card_sha256=sha(row['card']),
                 mapping=spec, mapping_sha256=history['mapping_sha256'], xlsx_sha256=spec['database']['xlsx_sha256'],
                 history=history, plan=plan, outcome_values_read=0, execution_authorized=False)
    ensure_once(target, value)
    return value


# ---------------------------------------------------------------- packets
def round_dir(pilot, row):
    return pilot.case_dir(row) / 'curated'


def round_jobs(pilot, row, tag, units):
    """Blind A/B history packets for ``units`` (completion packets of the canonical 51-unit jobs). Idempotent per tag."""
    from .agents.history import emit_history_job
    if (retry_receipt(pilot.repo, row) is not None) != tag.endswith(RETRY_SUFFIX):
        raise ValueError('Round tag %s does not belong to this attempt (retry rounds end in %r, first-audit rounds never do)' % (tag, RETRY_SUFFIX))
    inventory = round_dir(pilot, row) / (tag + '.json')
    if inventory.exists():
        return _read(inventory)
    canonical = pilot.backend.jobs(row, 'history', pilot)
    if len(canonical) != 2:
        raise ValueError('Curated route needs the canonical A/B history jobs')
    jobs = [str(emit_history_job(path, jobs_root=round_dir(pilot, row) / ('jobs_' + tag), attempt_id='cur_' + tag,
                                 completion_units=list(units)).job_path) for path in canonical]
    value = dict(tag=tag, units=list(units), jobs=jobs)
    ensure_once(inventory, value)
    return value


def _round_files(pilot, row):
    folder = round_dir(pilot, row)
    return [_read(p) for p in sorted(folder.glob('r*.json'))] if folder.is_dir() else []


def collect_round(pilot, row, jobs):
    """(complete, unreadable) : complete when every packet is collected or finally unreadable. Never completes a gap."""
    from .c42 import Park
    from .agents.replacement import ContractRejection
    complete, unreadable = True, []
    for path in jobs:
        try:
            state = pilot.backend.job_state(path)
        except ContractRejection:
            unreadable.append(path)
            continue
        pilot.ledger(row, 'history', state, 'budget', job=path)
        if state == 'done':
            try:
                pilot.backend.collect(path)
            except ContractRejection:
                unreadable.append(path)
            except Park as exc:
                if 'INSUFFICIENT_WORK' not in str(exc):
                    raise
                unreadable.append(path)
            except ValueError as exc:
                if 'INSUFFICIENT_WORK' not in str(exc):
                    raise
                unreadable.append(path)
        else:
            complete = False
            if state == 'pending':
                pilot.pending.append((row, path))
    return complete, unreadable


def start(pilot, row):
    """History action: build, plan, first round of blind packets, collection. Returns the checkpoint value or None (waiting)."""
    build = prepare(pilot, row)
    receipt = retry_receipt(pilot.repo, row)
    tag = base_tag(receipt)
    if receipt is not None and not (round_dir(pilot, row) / (tag + '.json')).exists():
        log_retry_round(pilot, row, receipt)       # logged before the packets exist (and so before any dispatch)
    rnd = round_jobs(pilot, row, tag, build['plan']['sample'])
    if receipt is not None and set(rnd['jobs']) & set(receipt['first_audit']['round_jobs']):
        raise ValueError('CURATED_RETRY_MISMATCH: a retry packet reuses a first-audit packet')
    ensure_once(pilot.case_dir(row) / 'jobs' / 'history.json', dict(jobs=rnd['jobs'], curated=True))   # the inventory other tools read
    complete, unreadable = collect_round(pilot, row, rnd['jobs'])
    if not complete:
        return None
    return dict(jobs=rnd['jobs'], curated=True, round=tag, unreadable_jobs=unreadable, sample=build['plan']['sample'])


# ---------------------------------------------------------------- worker records -> checks
def fresh_records(job_paths, units):
    """unit -> reconciled A/B record, or None (unreadable, unknown, A/B disagreement). Conflicts are conservative (unverified)."""
    from .agents.jobs import load_job
    from .manual_evidence import collection_path
    from .stages.s1_treatment import reconcile
    values = {}
    for path in job_paths:
        job = load_job(Path(path))
        try:
            receipt = _read(collection_path(job))
            if receipt.get('validated') is not True:
                return {u: None for u in units}
            values[job.role] = _read(receipt['promoted_path'])['records']
        except (OSError, ValueError, KeyError):
            return {u: None for u in units}
    if set(values) != {'A', 'B'}:
        return {u: None for u in units}
    out = {u: None for u in units}
    try:
        comparison = reconcile(values['A'], values['B'], list(units))
    except ValueError:
        return out
    by_b = {r['unit']: r for r in values['B']}
    for rec in comparison['accepted']:
        other = by_b.get(rec['unit']) or {}
        if rec.get('best_evidence_start_year') == other.get('best_evidence_start_year'):
            out[rec['unit']] = rec
    return out


def phase_of(unit, plan, n_unverified):
    if unit in plan['sample']:
        return 'sample'
    backups = plan['backup_order']
    if unit in backups[:n_unverified]:
        return 'replacement'
    return 'extension'


def make_checks(plan, history, spec, rounds_records):
    """One check per audited unit (database record vs worker record). ``rounds_records``: list of unit->record dicts."""
    db = {r['unit']: r for r in history['accepted']}
    merged = {}
    for rec in rounds_records:
        merged.update(rec)
    base = {u: merged.get(u) for u in plan['sample'] if u in merged}
    n_unverified = sum(1 for u, rec in base.items() if ch.classify(db[u], rec, spec)['status'] == 'unverified')
    checks = []
    for unit in sorted(merged):
        cls = ch.classify(db[unit], merged[unit], spec)
        fresh = merged[unit] or {}
        locator = fresh.get('locator') or ('A/B reconciled record' if merged[unit] else None)
        checks.append(dict(unit=unit, official_locator=locator, phase=phase_of(unit, plan, n_unverified), **cls))
    return checks


def next_units(plan, checks):
    """(units, phase) still to audit before ``evaluate`` can run, or ([], None)."""
    keyed = {c['unit']: c for c in checks}
    backups = plan['backup_order']
    unverified = [u for u in plan['sample'] if u in keyed and keyed[u]['status'] == 'unverified']
    repl = backups[:len(unverified)]
    missing = [u for u in repl if u not in keyed]
    if missing:
        return missing, 'replacement'
    final = [keyed[u] for u in plan['sample'] if u in keyed and keyed[u]['status'] != 'unverified'] + [keyed[u] for u in repl]
    timing = sum(c['status'] == 'timing' for c in final)
    details = sum(c['status'] == 'detail' for c in final)
    if (timing == 1 or details >= 3) and not plan['census']:
        extension = backups[len(unverified):len(unverified) + ch.EXTENSION]
        missing = [u for u in extension if u not in keyed]
        if missing:
            return missing, 'extension'
    return [], None


def _verdict_path(root):
    return root / 'CURATED_VERDICT.json'


def reconcile(pilot, row):
    """Reconcile action: classify, evaluate, further rounds if the rule asks, then exclusions + RECONCILIATION.json on a pass."""
    from .c42 import Park, register_exclusions
    from .intake.official_history import conflict_scope
    from . import prewindow
    root = pilot.backend.root(row)
    build = _read(root / 'CURATED_BUILD.json')
    spec, history, plan = build['mapping'], build['history'], build['plan']
    receipt = retry_receipt(pilot.repo, row)
    base = base_tag(receipt)
    r1 = _read(round_dir(pilot, row) / (base + '.json'))      # r1 in a first audit, r1b in the Q2 retry attempt: this attempt's rounds only
    rounds_records = [fresh_records(r1['jobs'], r1['units'])]
    for extra in [p for p in _round_files(pilot, row) if p['tag'] != base]:
        complete, _ = collect_round(pilot, row, extra['jobs'])
        if not complete:
            return None
        rounds_records.append(fresh_records(extra['jobs'], extra['units']))
    checks = make_checks(plan, history, spec, rounds_records)
    units, phase = next_units(plan, checks)
    if units:
        tag = 'r%d%s' % (2 + len([p for p in _round_files(pilot, row) if p['tag'] != base]), RETRY_SUFFIX if receipt is not None else '')
        rnd = round_jobs(pilot, row, tag, units)
        pilot.ledger(row, 'reconcile', 'CURATED_NEXT_ROUND', 'record', round=tag, phase=phase, units=units)
        collect_round(pilot, row, rnd['jobs'])
        return None
    verdict = ch.evaluate(plan, checks)
    path = ensure_once(_verdict_path(root), dict(schema='l2_curated_verdict_v1', design_id=row['design_id'], checks=checks,
                                                 verdict=verdict, plan_sha256=ch.digest(plan), outcome_values_read=0))
    ledger_dir = pilot.repo / LEDGER_DIR
    final = receipt is not None and verdict['verdict'] == 'inconclusive'
    if receipt is None:
        try:
            ch.record_result(ledger_dir, spec, spec['universe'], verdict['verdict'])
        except ValueError as exc:
            if 'already recorded' not in str(exc):
                raise
    else:
        record_retry_result(pilot, row, receipt, verdict, path, closed=final)     # the first audit's ledger result is never touched
    if not verdict['passed']:
        reason = 'CURATED_AUDIT_%s: %s; route %s' % (verdict['verdict'].upper(), verdict.get('reason') or 'pass rule not met', verdict['route'])
        extra = dict(final=True, retry_round=base, closed_for_good=True) if final else dict(retry_round=base) if receipt is not None else {}
        if final:
            reason += '; second audit round (Q2 retry) also inconclusive: closed for good'
        raise Park(reason, dict(kind=PARK_KIND, route=verdict['route'], verdict=verdict, outcome_values_read=0,
                                verdict_file=str(path), **extra))
    # pass: exclusions (reversal / placeholder / pre-window states), RECONCILIATION.json, AUDIT_SELECTION.json
    ctx = pilot.backend.ctx(row, strict=False)
    cfg = deepcopy(ctx.card.payload['treatment'])
    cfg['universe'] = list(spec['universe'])
    reasons = {u: [r] for u, r in history['exclusion_reasons'].items()}
    accepted = ch.apply_corrections(history, checks, spec)
    for unit in prewindow.bound_units(accepted):
        reasons.setdefault(unit, []).append(prewindow.REASON)
    cfg['excluded_conflict_state_reasons'] = {u: sorted(set(r)) for u, r in sorted(reasons.items())}
    cfg['excluded_conflict_states'] = sorted(set(cfg.get('excluded_conflict_states', [])) | set(reasons))
    cfg['excluded_units'] = sorted(set(cfg.get('excluded_units', [])) | set(cfg['excluded_conflict_states']))
    cfg['certified_spell_end_periods'] = {}
    conflict_scope(cfg, [])
    declaration = register_exclusions(ctx, cfg, [_bind(root / 'CURATED_BUILD.json'), _bind(path)])
    eligible = [r for r in accepted if r['unit'] not in cfg['excluded_units']]
    counts = dict(retained_treated=sum(r['status'] == 'treated' for r in eligible),
                  retained_controls=sum(r['status'] == 'never_treated' for r in eligible), curated=True,
                  audited_states=len(checks), comparison_group='never_treated')
    ensure_once(root / 'AUDIT_SELECTION.json', dict(curated=True, sample=plan['sample'], seed=plan['seed'],
                                                     plan_sha256=ch.digest(plan), build_sha256=sha(root / 'CURATED_BUILD.json')))
    ensure_once(root / 'RECONCILIATION.json', dict(
        design_id=row['design_id'], historical_comparison=dict(accepted=accepted, gaps=[], counts=dict(known_match=len(accepted)),
                                                              agreement=None, paired_known=0),
        audit_eligible_records=eligible, excluded_conflict_states=cfg['excluded_conflict_states'],
        excluded_conflict_state_reasons=cfg['excluded_conflict_state_reasons'], counts=counts,
        c42_declaration=_bind(declaration), curated=dict(build=_bind(root / 'CURATED_BUILD.json'), verdict=_bind(path)),
        outcome_values_read=0))
    from .rules import reconcile_minimums
    floor = reconcile_minimums(ctx.card.payload, pilot.repo) or dict(min_retained_treated=6, min_retained_controls=5)
    need_t = max(floor['min_retained_treated'], ctx.card.payload.get('analysis', {}).get('min_treated', 6))
    need_c = max(floor['min_retained_controls'], ctx.card.payload.get('analysis', {}).get('min_controls', 5))
    failed = []
    if counts['retained_treated'] < need_t:
        failed.append('V7 retained treated below minimum')
    if counts['retained_controls'] < need_c:
        failed.append('V7 retained controls below minimum')
    if failed:
        raise Park('; '.join(failed), dict(counts=counts, minimums=dict(treated=need_t, controls=need_c),
                                           excluded_conflict_states=cfg['excluded_conflict_states']))
    return dict(counts=counts, jobs=[], curated=True, excluded_conflict_states=cfg['excluded_conflict_states'],
                excluded_conflict_state_reasons=cfg['excluded_conflict_state_reasons'], verdict=verdict['verdict'])


def audit(pilot, row):
    """Audit action: the certified history files. The audit itself ran in the reconcile action (blind A/B on the sample)."""
    root = pilot.backend.root(row)
    build = _read(root / 'CURATED_BUILD.json')
    spec, history, plan = build['mapping'], build['history'], build['plan']
    verdict_file = _read(_verdict_path(root))
    ctx = pilot.backend.ctx(row, strict=False)
    files = ch.certify(root, history, spec, plan, verdict_file['checks'], verdict_file['verdict'],
                       card_excluded=ctx.card.payload['treatment'].get('excluded_units', []))
    return dict(jobs=[], curated=True, artifacts={k: str(v) for k, v in files.items()})


def accept(ctx):
    """S1 for a curated design: the certified files must exist and agree with each other."""
    from .contracts import Disposition, StageResult
    from .storage import artifact
    root = ctx.design_dir
    names = ('DB_HISTORY_SOURCE.json', 'CERTIFIED_HISTORY.json', 'TREATMENT.json', 'S1_IMPORT_PROVENANCE.json')
    missing = [n for n in names if not (root / n).is_file()]
    if missing:
        raise ValueError('Curated S1 lacks ' + ', '.join(missing))
    treatment, certificate, proof = (_read(root / 'TREATMENT.json'), _read(root / 'CERTIFIED_HISTORY.json'),
                                     _read(root / 'S1_IMPORT_PROVENANCE.json'))
    if (treatment.get('audit', {}).get('passed') is not True or proof.get('v3_passed') is not True
            or proof.get('source_type') != ch.SOURCE_TYPE
            or treatment.get('source_sha256') != sha(root / 'CERTIFIED_HISTORY.json')
            or certificate.get('history_source', {}).get('label') != ch.HISTORY_SOURCE
            or certificate['history_source'].get('db_history_source_sha256') != sha(root / 'DB_HISTORY_SOURCE.json')):
        raise ValueError('Curated history files do not agree')
    for item in proof['evidence']:
        if sha(item['path']) != item['sha256']:
            raise ValueError('Curated S1 evidence changed: ' + item['path'])
    return StageResult('S1', ctx.card.design_id, Disposition.READY, 'Curated database history passed the blind audit',
                       artifacts=tuple(artifact(root / n, Path(n).stem.lower()) for n in names[1:]), metrics=treatment['audit'])


# ---------------------------------------------------------------- Q2: one extra blind-audit round (HUB_RULING_20261023_PARK_REOPEN s.4)
def retry_path(repo, design_id):
    return Path(repo) / RETRY_DIR / (design_id + '.json')


def retry_result_path(repo, design_id):
    return Path(repo) / RETRY_DIR / (design_id + '.result.json')


def retry_receipt(repo, row):
    """The Q2 receipt of this design when ``row`` is its retry attempt, else None (a first audit, or any other attempt)."""
    repo = Path(getattr(repo, 'repo', repo))
    if row.get('design_id') not in RETRY_DESIGNS:
        return None
    try:
        value = _read(retry_path(repo, row['design_id']))
    except (OSError, ValueError):
        return None
    if value.get('schema') != SCHEMA_RETRY or value.get('retry_attempt_id') != row.get('attempt_id'):
        return None
    return value


def resume_path(repo, design_id):
    return Path(repo) / RESUME_DIR / (design_id + '.json')


def resume_receipt(repo, row):
    """The T287 resume receipt when ``row`` is the single attempt it names, else None. A design outside RESUME_DESIGNS never resumes."""
    repo = Path(getattr(repo, 'repo', repo))
    if row.get('design_id') not in RESUME_DESIGNS:
        return None
    try:
        value = _read(resume_path(repo, row['design_id']))
    except (OSError, ValueError):
        return None
    if (value.get('schema') != SCHEMA_RESUME or value.get('resume_attempt_id') != row.get('attempt_id') or value.get('design_id') != row['design_id']
            or value.get('no_verdict_existed') is not True or value.get('outcome_values_read') != 0):
        return None
    ruling = value.get('ruling') or {}
    try:
        if ruling.get('sha256') != sha(ruling['path']):
            return None
    except (KeyError, OSError, TypeError):
        return None
    return value


def build_resume_receipt(pilot, payload, first_attempt_id, first_root, first_case_dir, new_attempt_id, ruling_path=None):
    """The T287 Q5 receipt for the design of card ``payload``: facts of the first attempt's audit plan (``first_root`` = its work directory, ``first_case_dir`` =
    its autopilot directory), which never produced a verdict (no verdict file, no later round, ledger result None). Pure; the caller writes it once
    (write-once) before the requeue."""
    design = payload['design_id']
    if design not in RESUME_DESIGNS:
        raise ValueError('Q5 resume names a design outside the ruling list')
    first_root, first_case_dir = Path(first_root), Path(first_case_dir)
    build_file = first_root / 'CURATED_BUILD.json'
    if not build_file.is_file():
        raise ValueError('First audit build missing: CURATED_BUILD.json')
    rounds = sorted((first_case_dir / 'curated').glob('r*.json'))
    if _verdict_path(first_root).exists() or any(p.name != 'r1.json' for p in rounds):
        raise ValueError('The first audit produced a verdict or later rounds: no resume')
    build = _read(build_file)
    spec = load_spec(pilot.repo, payload)
    ch.resume_claim(Path(pilot.repo) / LEDGER_DIR, spec, spec['universe'], design)
    ruling = Path(ruling_path or Path(pilot.repo) / RESUME_RULING)
    plan = build['plan']
    return dict(schema=SCHEMA_RESUME, design_id=design, rule='T287_Q5', ruling=_bind(ruling), first_attempt_id=first_attempt_id,
                resume_attempt_id=new_attempt_id, no_verdict_existed=True, outcome_values_read=0,
                first_audit=dict(attempt_id=first_attempt_id, xlsx_sha256=build['xlsx_sha256'], mapping_sha256=build['mapping_sha256'],
                                 plan_sha256=ch.digest(plan), seed=plan['seed'], sample=plan['sample'], build_sha256=sha(build_file)))


def base_tag(receipt):
    return 'r1' + (RETRY_SUFFIX if receipt is not None else '')


def same_audit(receipt, spec, history, plan):
    """The retry attempt must be the same audit: database sha256, mapping hash, plan (sample, seed, backups) all equal the first audit's."""
    first = receipt['first_audit']
    got = dict(xlsx_sha256=spec['database']['xlsx_sha256'], mapping_sha256=history['mapping_sha256'], plan_sha256=ch.digest(plan),
               seed=plan['seed'], sample=plan['sample'])
    bad = sorted(k for k, v in got.items() if first.get(k) != v)
    if bad:
        raise ValueError('CURATED_RETRY_MISMATCH: the retry audit differs from the first audit in ' + ', '.join(bad) + ' (no redraw, no mapping change)')


def first_audit_facts(pilot, old):
    """Facts of the first (inconclusive) audit of attempt ``old``, read from its own files. Nothing is written."""
    root = pilot.backend.root(old)
    build_file, verdict_file, r1_file = root / 'CURATED_BUILD.json', _verdict_path(root), round_dir(pilot, old) / 'r1.json'
    for f in (build_file, verdict_file, r1_file):
        if not f.is_file():
            raise ValueError('First audit file missing: ' + f.name)
    build, verdict = _read(build_file), _read(verdict_file)
    if verdict['verdict'].get('verdict') != 'inconclusive' or verdict['verdict'].get('route') != 'not_evaluable' or verdict['verdict'].get('passed') is not False:
        raise ValueError('The first audit was not inconclusive')
    plan = build['plan']
    rounds = {p.name: sha(p) for p in sorted(round_dir(pilot, old).glob('r*.json'))}
    jobs = sorted({j for p in sorted(round_dir(pilot, old).glob('r*.json')) for j in _read(p)['jobs']})
    return dict(attempt_id=old['attempt_id'], xlsx_sha256=build['xlsx_sha256'], mapping_sha256=build['mapping_sha256'],
                plan_sha256=ch.digest(plan), seed=plan['seed'], sample=plan['sample'], build_sha256=sha(build_file),
                verdict_sha256=sha(verdict_file), unverifiable_share=verdict['verdict'].get('unverifiable_share'),
                round_files=rounds, round_jobs=jobs)


def shared_pack_state(repo):
    """The queue's shared pack as it stands: urls, hashes and design tags of every page. Pages are served by URL to any packet that asks
    for them (agents.shared_pack.lookup), so a page already in the pack when the receipt is logged is available to all four designs alike."""
    from .agents import shared_pack
    entries = []
    for e in shared_pack.index(Path(repo) / SONNET_QUEUE).get('entries', []):
        entries.append(dict(url=e.get('url'), sha256=e.get('sha256'), units=sorted(e.get('units') or []), designs=sorted(e.get('designs') or [])))
    entries.sort(key=lambda e: (str(e['url']), str(e['sha256'])))
    return dict(entries=entries, entries_sha256=ch.digest(entries))


def pages_not_offered_to_all(snapshot, now):
    """Urls of pages added after ``snapshot`` (the receipt's) that name some but not all four retry designs. A page for this round is
    fixed in advance (in the receipt's snapshot) or tagged to all four designs (or to none)."""
    seen = {(e['url'], e['sha256']) for e in snapshot['entries']}
    return [e['url'] for e in now['entries'] if (e['url'], e['sha256']) not in seen
            and set(e['designs']) & RETRY_DESIGNS and not RETRY_DESIGNS <= set(e['designs'])]


def build_receipt(pilot, old, new_version, ruling_path=None):
    """The Q2 receipt for design ``old`` (facts of its inconclusive audit + the retry attempt that may run it). Pure; the caller writes it once."""
    design = old['design_id']
    if design not in RETRY_DESIGNS:
        raise ValueError('Q2 retry names a design outside the ruling list')
    ruling = Path(ruling_path or Path(pilot.repo) / RETRY_RULING)
    pack = shared_pack_state(pilot.repo)
    return dict(schema=SCHEMA_RETRY, design_id=design, rule=RETRY_RULE, rounds_allowed=1, tag_suffix=RETRY_SUFFIX,
                ruling=_bind(ruling), first_attempt_id=old['attempt_id'], retry_attempt_id='requeue_' + new_version,
                retry_card_version=new_version, first_audit=first_audit_facts(pilot, old), shared_pack=pack, outcome_values_read=0)


def log_retry_round(pilot, row, receipt):
    """Ledger row written before the retry packets are emitted: same audit, new round, shared pack as it stands."""
    pack = shared_pack_state(pilot.repo)
    bad = pages_not_offered_to_all(receipt['shared_pack'], pack)
    if bad:
        raise ValueError('A shared-pack page added after the receipt names some but not all four retry designs: ' + ', '.join(bad))
    pilot.ledger(row, 'history', 'CURATED_RETRY_ROUND_LOGGED', 'record', round=base_tag(receipt), receipt=str(retry_path(pilot.repo, row['design_id'])),
                 receipt_sha256=sha(retry_path(pilot.repo, row['design_id'])), first_attempt=receipt['first_attempt_id'],
                 plan_sha256=receipt['first_audit']['plan_sha256'], mapping_sha256=receipt['first_audit']['mapping_sha256'],
                 xlsx_sha256=receipt['first_audit']['xlsx_sha256'], shared_pack_entries_sha256=pack['entries_sha256'])


def record_retry_result(pilot, row, receipt, verdict, verdict_path, closed):
    """Write-once result of the retry round, beside the receipt (the policy ledger keeps the first audit's result)."""
    return ensure_once(retry_result_path(pilot.repo, row['design_id']), dict(
        schema=SCHEMA_RETRY_RESULT, design_id=row['design_id'], retry_attempt_id=row['attempt_id'], verdict=verdict['verdict'],
        route=verdict['route'], passed=verdict['passed'], closed_for_good=closed, verdict_file=str(verdict_path),
        verdict_file_sha256=sha(verdict_path), receipt_sha256=sha(retry_path(pilot.repo, row['design_id']))))


CLOSURE_GATE = 'HISTORY_INSUFFICIENT'    # existing approved class (the adoption history could not be certified); no new gate name needed


def closure_row(pilot, row):
    """FINAL_DECISIONS row for a retry attempt that ended in the second inconclusive verdict (PARKED_C83_FINAL.json), else None.
    Written by the hub through final_decisions.append (apply_curated_retry.py --close); this function only builds it."""
    from datetime import datetime, timezone
    from .autopilot import read
    receipt = retry_receipt(pilot.repo, row)
    final = pilot.case_dir(row) / 'PARKED_C83_FINAL.json'
    result = read(retry_result_path(pilot.repo, row['design_id']))
    if receipt is None or not final.is_file() or not result or result.get('closed_for_good') is not True:
        return None
    when = datetime.fromtimestamp(final.stat().st_mtime, timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    first = receipt['first_audit']
    return dict(design_id=row['design_id'], attempt=row['attempt_id'], decision='NOT_EVALUABLE', gate=CLOSURE_GATE,
                basis=('curated database audit CURATED_AUDIT_INCONCLUSIVE twice (unverifiable share %s in round r1, again inconclusive in the one extra round %s '
                       'on the same database sha256 %s..., mapping hash %s..., sample and seed); closed for good (HUB_RULING_20261023_PARK_REOPEN Q2)'
                       % (first.get('unverifiable_share'), base_tag(receipt), first['xlsx_sha256'][:12], first['mapping_sha256'][:12])),
                state_at_closure='PARK', park_time_utc=when, source=RETRY_RULING)


def only_version_differs(old_card_path, new_payload, card_path=None):
    """True when the retry card differs from the old card in `version` and nothing else, compared in ONE representation.
    requeue() hands the hook the LOADED card (cards.load_card applies connectors.rates.prospective_card: a rate card such as the dv, mental_health and
    straw cards gets its outcome.outcome_unit normalised), while the stored old card is raw JSON. So both sides go through prospective_card (idempotent),
    and when the new card is a real file its raw JSON is also compared with the old file's raw JSON. Any other difference (mapping, treatment,
    outcome, window, analysis) is still refused."""
    from .connectors.rates import prospective_card

    def diff(a, b):
        return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    old_raw = _read(old_card_path)
    if diff(prospective_card(old_raw), prospective_card(new_payload)) != {'version'}:
        return False
    if card_path is not None and Path(str(card_path)).is_file() and diff(old_raw, _read(card_path)) != {'version'}:
        return False
    return True


def validate_retry(pilot, old, decision, new_payload, card_path, receipt=None):
    """Requeue hook (``curated_retry_rule`` in the card-bound ruling): the one extra round for one of the four designs, once.
    Refuses a third round (the retry attempt itself, or any attempt but the one that parked inconclusive), a changed card (only ``version``
    may differ), a changed audit (database, mapping hash, plan, seed, sample), a stale first audit, and a shared pack that is not offered to all four."""
    from .autopilot import read
    design = old['design_id']
    if design not in RETRY_DESIGNS or new_payload.get('design_id', design) != design:
        raise ValueError('Q2 retry names a design outside the ruling list')
    if (decision.get('curated_retry_rule') != RETRY_RULE or decision.get('decision') != 'resume' or decision.get('execution_authorized') is not False
            or decision.get('superseded_status') != STATUS):
        raise ValueError('Q2 retry ruling malformed')
    ruling_file = Path(pilot.repo) / RETRY_RULING
    authority = decision.get('authority')
    if (not isinstance(authority, dict) or Path(str(authority.get('path', ''))).resolve() != ruling_file.resolve()
            or authority.get('sha256') != sha(ruling_file)):
        raise ValueError('Q2 retry authority is not the 2026-10-23 park-reopen ruling')
    receipt = receipt if receipt is not None else read(retry_path(pilot.repo, design))
    if not receipt or receipt.get('schema') != SCHEMA_RETRY:
        raise ValueError('Q2 retry needs its receipt logged before the requeue')
    if old['attempt_id'] == receipt['retry_attempt_id']:
        raise ValueError('Q2 retry: the extra round is spent; a second inconclusive closes the design for good')
    if old['attempt_id'] != receipt['first_attempt_id'] or receipt['retry_attempt_id'] != 'requeue_' + str(new_payload.get('version')):
        raise ValueError('Q2 retry: exactly one extra round, from the attempt that parked inconclusive, as the attempt named in the receipt')
    if pilot.stop_record(old) or (pilot.case_dir(old) / 'PARKED_C83_FINAL.json').exists():
        raise ValueError('Q2 retry: the attempt is stopped or finally parked')
    parked = read(pilot.case_dir(old) / 'PARKED.json', {}) or {}
    metrics = parked.get('metrics') or {}
    if (parked.get('action') != 'reconcile' or metrics.get('kind') != PARK_KIND or metrics.get('route') != 'not_evaluable'
            or not str(parked.get('reason', '')).startswith(INCONCLUSIVE)):
        raise ValueError('Q2 retry requires the attempt parked at reconcile with CURATED_AUDIT_INCONCLUSIVE, route not_evaluable')
    if not only_version_differs(old['card'], new_payload, card_path):
        raise ValueError('Q2 retry card may differ from the old card only in version')
    if receipt['first_audit'] != first_audit_facts(pilot, old):
        raise ValueError('Q2 retry: the first audit changed since the receipt was logged')
    spec = load_spec(pilot.repo, new_payload)
    xlsx = Path(pilot.repo) / XLSX
    if ch.file_sha(xlsx) != spec['database']['xlsx_sha256']:
        raise ValueError('database workbook differs from the pinned version')
    history = ch.build_history(ch.load_rows(xlsx), spec)
    same_audit(receipt, spec, history, ch.audit_plan(history, spec, design))
    bad = pages_not_offered_to_all(receipt['shared_pack'], shared_pack_state(pilot.repo))
    if bad:
        raise ValueError('A shared-pack page added after the receipt names some but not all four retry designs: ' + ', '.join(bad))
    return dict(design_id=design, retry_attempt_id=receipt['retry_attempt_id'], card=str(card_path))


def roster_minimums(repo, payload, spec):
    """Outcome-blind roster check before a switch: the database roster after the card's own exclusions must reach the
    pipeline minimums (V7), else the audit would be spent on a design that parks at reconcile. -> (ok, counts)"""
    from .rules import reconcile_minimums
    from . import prewindow
    history = ch.build_history(ch.load_rows(Path(repo) / XLSX), spec)
    excluded = set(payload['treatment'].get('excluded_units', [])) | set(history['excluded']) | set(prewindow.bound_units(history['accepted']))
    kept = [r for r in history['accepted'] if r['unit'] not in excluded]
    counts = dict(retained_treated=sum(r['status'] == 'treated' for r in kept), retained_controls=sum(r['status'] == 'never_treated' for r in kept))
    floor = reconcile_minimums(payload, repo) or dict(min_retained_treated=6, min_retained_controls=5)
    need_t = max(floor['min_retained_treated'], payload.get('analysis', {}).get('min_treated', 6))
    need_c = max(floor['min_retained_controls'], payload.get('analysis', {}).get('min_controls', 5))
    return counts['retained_treated'] >= need_t and counts['retained_controls'] >= need_c, dict(counts, need_treated=need_t, need_controls=need_c)


# ---------------------------------------------------------------- window tool: switch designs onto the curated route
def apply(pilot=None, designs=SWITCH_DESIGNS):
    """Stopped-window switch (CODE_TASK.lock task CURATED_ROUTE, CLEAN_STOP observed). For each named design that has a frozen
    mapping: the old attempt gets a hub PARKED.json (write-once, bytes of everything else untouched) and the design is requeued
    under a card revision that adds only ``automation.history_source``. A design without a frozen mapping is left as it is.
    Never mixes fresh and curated rows: the new attempt starts at history with its own directory."""
    from .autopilot import Autopilot, read
    from .ops.watchdog import clean_stop, guard
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.repo / 'data/l2_pipeline/autopilot/curated_route'
    ruling = p.repo / ch.RULING
    results = []
    with guard(p.registry):
        if not (p.registry / 'AUTOPILOT_STOP').exists() or read(p.registry / 'CODE_TASK.lock', {}).get('task') != 'CURATED_ROUTE':
            raise ValueError('CURATED_ROUTE stopped code window required')
        clean_stop(p.repo)
        application = out / 'APPLICATION.json'
        if application.exists():
            return read(application)
        rows = {r['design_id']: r for r in read(p.registry / 'ACTIVE_DESIGNS.json')['designs']}
        for design in designs:
            row = rows.get(design)
            if row is None:
                results.append(dict(design_id=design, status='not_active'))
                continue
            payload = read(row['card'])
            path = mapping_path(p.repo, design)
            try:
                spec = _read(path)
                check_frozen(spec)
                ch.validate_mapping(spec)
            except (OSError, ValueError) as exc:
                results.append(dict(design_id=design, status='left_on_fresh_route', reason=str(exc)[:200]))
                continue
            if (payload.get('automation') or {}).get('history_source') or (payload.get('automation') or {}).get('history_reuse'):
                results.append(dict(design_id=design, status='already_declared'))
                continue
            ok, roster = roster_minimums(p.repo, payload, spec)
            if not ok:
                results.append(dict(design_id=design, status='left_on_fresh_route', reason='database roster below the pipeline minimums', roster=roster))
                continue
            if not p.stop_record(row) and not (p.case_dir(row) / 'PARKED.json').exists():
                ensure_once(p.case_dir(row) / 'PARKED.json', dict(
                    design_id=design, attempt_id=row['attempt_id'], action='history', status='PARKED',
                    reason='CURATED_ROUTE_SUPERSEDE: hub route change to the curated database history (fresh history packets stay as they are)',
                    metrics=dict(kind=PARK_KIND, outcome_values_read=0), rule=ch.RULING))
            new = deepcopy(payload)
            new['version'] = 'v' + str(int(payload['version'][1:]) + 1)
            new.setdefault('automation', {})['history_source'] = dict(
                kind=ch.SOURCE_KIND, mapping_sha256=ch.mapping_sha(spec), reason='access', rule=ch.RULING)
            card = ensure_once(Path(row['card']).with_name(Path(row['card']).stem + '_curated.json'), new)
            value = ensure_once(out / 'rulings' / (design + '.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=design, prior_card_sha256=sha(row['card']), card_sha256=sha(card),
                decision='resume', superseded_status=STATUS,
                reason='HUB_RULING_20261021_CURATED_HISTORY: adoption history from the audited RAND database (route chosen for access reasons before any estimate)',
                authority=_bind(ruling), prior_stop=p.stop_record(row), execution_authorized=False))
            results.append(dict(design_id=design, status='requeued', receipt=requeue(design, card, value, p)['new_row']['attempt_id']))
        ensure_once(application, dict(records=results, dispatches=0, actual_data_estimation_calls=0))
        return read(application)


if __name__ == '__main__':
    print(json.dumps(apply(), indent=2))
