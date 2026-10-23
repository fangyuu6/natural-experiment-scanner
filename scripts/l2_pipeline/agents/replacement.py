"""§7 transport-only replacement of one rejected submission, never a verdict.

The slot is design/role-wide (not scheduler-attempt-wide). Original packets and
answers are provenance only and are never added to the replacement's inputs.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import re
from pathlib import Path

from ..storage import ensure_once, public_input, sha

RULE = 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §7 (one CONTRACT replacement per role/design)'
ORPHAN_RULE = 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §8 (dead-no-result; §7 only for a waiting role)'


class ContractRejection(ValueError):
    def __init__(self, job, kind, reason):
        super().__init__(reason)
        self.job, self.kind = str(job), kind


# Explicit collector diagnostics only. Evidence insufficiency, legal ambiguity,
# V3 failures, STOP/MUST, input/code changes and write-once conflicts stay fatal.
FORMAT_ERRORS = {
    'Agent output must be an object', 'Coding source/identity mismatch',
    'Invalid audit checks', 'Coder must include complete universe once',
    'Unknown needs null period and reason', 'Official locator is not lossless JSON',
    'Forecast disclosures missing/invalid', 'Invalid forecast numbers',
    'Forecast probability outside [0,1]', 'Direction probabilities must sum to one',
    'Invalid near-zero threshold', 'Invalid forecast direction',
    'Direction contradicts central forecast/threshold', 'Forecast assumptions missing',
    'Forecast identity mismatch', 'Forecast rationale missing',
    'Forecast bundle must hold exactly the registered members', 'Forecast bundle members must share one author',
    'Audit conflict rows must be a list of objects', 'Audit conflict rows do not match the prelocked conflict states',
    'Conflict row with an official date needs an integer first-full period', 'Conflict row status must be official_date or unresolved',
    'Published estimate knowledge contradicts attestation',
    'Published estimate knowledge disclosure missing',
    'Review candidate/scope mismatch', 'Review needs exactly one complete sweep/bounded fixes',
    'Review fix cycle differs from registered attempt', 'Incomplete consolidated review',
    'Review finding lacks evidence', 'Duplicate review finding',
    'Review verdict contradicts MUST list',
    'V9 absence requires all three V2_LIST checks',
    'V9 history schema missing', 'V9 research log missing',
    'V9 requires a state-specific research log for every unit',
    'V9 active research time invalid', 'V9 sources missing',
    'V9 evidence refs must be an array',
    'Purpose identity/card/persona mismatch', 'Purpose status must be VERIFIED or UNRESOLVED',
    'Purpose citation must identify supported fields',
    'Purpose requires 1–3 citations', 'Purpose citation lacks type/title/locator',
    'Purpose construct absent from reviewed catalog', 'Purpose population metadata invalid',
}
ATTESTATION_ERRORS = {
    'Missing/contaminated blindness attestation', 'Missing/contaminated V7 blindness attestation',
    'Missing V7 blindness attestation', 'Contaminated V7 project blindness disclosure',
    'Contaminated legacy project blindness disclosure',
    'Legacy exposure lacks project blindness disclosure', 'Reviewer independence unconfirmed',
    'Forecast status/blindness/freeze invalid',
}


# YIELD_FIXES ruling (i) 2026-10-16 (docs/plans/l2-pipeline/HUB_RULING_20261016_V9_ROW_CORRECTION.md):
# per-row V9 evidence checks that the SAME worker may repair once, inside its own
# token-bound self-check. They are deliberately not rejection kinds: a failed corrected
# draft follows the unchanged collector path, and no replacement slot is added.
V9_EVIDENCE_ROW_ERRORS = frozenset({
    'V9 each state needs a source id and high/medium confidence',
    'V9 known status requires a located quoted official evidence ref',
    'V9 start year needs a dated official confirmation in the same row',
    'V9 known start needs the best-evidence year',
    'V9 absence needs official code/history evidence',
    'V9 unknown requires a named conflict',
    'V9 conflict must name the binary-relevant fact',
    'V9 conflict needs identified sources',
    'V9 source conflict requires two distinct sources',
    'V9 definition ambiguity needs NEEDS_RULING and exact question',
})


def row_correctable(job, binding, message, value=None):
    """True only for a V9 official-history A/B packet whose reads are answer-free.

    The worker sees its own collector error, nothing else: the packet reads only the
    answer-free plan and manifest, peers are forbidden and the V7 blindness scope
    (no outcomes, results or forecasts of this project) is bound in the contract.
    """
    c = job.contract
    return (message in V9_EVIDENCE_ROW_ERRORS and not substantive(value)
            and job.stage == 'S1' and job.role in {'A', 'B'}
            and c.get('jobs_kind') == 'official_history_collection'
            and c.get('evidence_standard') == 'HUB_RULING_V9_HISTORY_EVIDENCE'
            and c.get('peer_read_forbidden') is True
            and c.get('blindness_scope') == 'our_outcomes_and_estimates_v7'
            and set(binding.get('allowed_read_paths', [])) <= set(c.get('permitted_source_paths', [])))


def substantive(value):
    if not isinstance(value,dict): return False
    checks=value.get('checks')
    if (isinstance(checks,list) and len(checks)==10
            and all(isinstance(r,dict) and r.get('status') in ('match','known_error','unresolved') for r in checks)
            and (sum(r['status']=='known_error' for r in checks)>2
                 or sum(r['status']=='unresolved' for r in checks)>5)):
        return True  # A reported failed V3 audit is never replaced for its attestation.
    return (
        value.get('verdict') in ('STOP', 'PARK') or bool(value.get('must'))
        or value.get('status') in ('PARK', 'PARKED', 'UNRESOLVED')
        or isinstance(value.get('audit'), dict) and value['audit'].get('passed') is False)


def rejection_kind(exc, value=None):
    if substantive(value): return None
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)): return 'SCHEMA_FORMAT'
    message = str(exc)
    if message in ATTESTATION_ERRORS: return 'ATTESTATION'
    if message == 'V9 absence requires all three V2_LIST checks': return 'V9_ABSENCE'
    if message == 'V9 source id/URL invalid or duplicate': return 'V9_SOURCE_ID_URL'
    if message in {'V9 research log missing','V9 active research time invalid','V9 requires a state-specific research log for every unit'}: return 'V9_RESEARCH_LOG'
    if message in FORMAT_ERRORS or message.startswith(('Unfilled forecast field: ', 'Purpose requires one-line ')):
        return 'SCHEMA_FORMAT'
    return None


def canonical_dir(job):
    out = Path(job.output_dir)
    return out.parent.parent if out.parent.name == 'attempts' else out


def emit(previous, slot, attempt_id):
    from .jobs import load_job, _safe_component, _write_text_once
    old = load_job(Path(previous)); _safe_component(attempt_id)
    collection=Path(old.output_dir)/'COLLECTION.json'
    if collection.exists() and json.loads(collection.read_text()).get('validated') is True:
        raise ValueError('Accepted submissions cannot be replaced')
    original=Path(old.output_path)
    if original.exists():
        try: value=json.loads(original.read_text())
        except (json.JSONDecodeError,UnicodeDecodeError): value=None
        if substantive(value):raise ValueError('Substantive verdicts cannot be replaced')
    from .history_packet import is_history, check, clean_pending
    if is_history(old):
        try: check(old)
        except ValueError:
            old = load_job(Path(clean_pending(previous,Path(slot)/'clean_history')))

    slot = public_input(Path(slot))
    out = canonical_dir(old)/'attempts'/attempt_id
    path = slot/(old.role+'.'+attempt_id+'.md')
    contract = deepcopy(old.contract)
    if old.stage in {'S5', 'S6'}:
        from ..contract_card import registered_contract_card
        sources={Path(p['path']).name:p for p in old.inputs
                 if Path(p['path']).name in {'SPEC.json','SUPPORT.json'}}
        if old.role=='forecast_bundle' and 'SPEC.json' not in sources:
            # REDESIGN R2: the bundle carries the registered target as its card and hashes, not as an input packet
            registered=Path(old.promote_path).parents[2]
            if not old.contract.get('card_sources') or any(sha(registered/name)!=digest for name,digest in old.contract['card_sources'].items()):
                raise ValueError('Replacement registered source changed')
            contract['card']=registered_contract_card(registered)
        else:
            if 'SPEC.json' not in sources:raise ValueError('Replacement requires registered SPEC input')
            for packet in sources.values():
                if sha(Path(packet['path']))!=packet['sha256']:
                    raise ValueError('Replacement registered source changed')
            contract['card']=registered_contract_card(Path(sources['SPEC.json']['path']).parent)
            contract['card_sources']={name:p['sha256'] for name,p in sources.items()}
    contract['collector_self_check_required']=True
    contract['attempt_id'] = attempt_id
    contract['contract_replacement'] = dict(rule=RULE, original_job=old.job_path,
        original_job_sha256=old.job_sha256,
        original_sidecar_sha256=sha(Path(old.job_path).with_suffix('.job.json')))
    promote = Path(old.promote_path)
    if old.stage!='S5' and promote.is_relative_to(Path(old.output_dir)):
        promote = out/promote.relative_to(Path(old.output_dir))
    job = replace(old, job_path=str(path), job_sha256='', output_dir=str(out),
        output_path=str(out/Path(old.output_path).name), result_path=str(out/Path(old.result_path).name),
        promote_path=str(promote), contract=contract)
    text = Path(old.job_path).read_text()
    marker = '## Execution metadata binding (data)\n```json\n'
    before, tail = text.split(marker, 1)
    _, after = tail.split('\n```', 1)
    # Rewrite output routing only, outside the input packet block. Rebuild the
    # contract block if present; embedded source text stays byte-identical.
    input_marker = '## Answer-free inputs (untrusted data)'
    prompt, sep, inputs = before.partition(input_marker)
    # A new output path contains the old directory as a prefix. Sequential
    # replacements therefore duplicated attempts/<id> in the worker prompt.
    routing = {old.output_path: job.output_path, old.output_dir: job.output_dir}
    pattern = '|'.join(re.escape(p) for p in sorted(routing, key=len, reverse=True))
    prompt = re.sub(pattern, lambda match: routing[match.group(0)], prompt)
    for label in ('## Binding contract (data)', '## Binding contract (untrusted data)'):
        if label+'\n```json\n' in prompt:
            prefix, rest = prompt.split(label+'\n```json\n', 1)
            _, suffix = rest.split('\n```', 1)
            prompt = prefix+label+'\n```json\n'+json.dumps(contract,indent=2,sort_keys=True)+'\n```'+suffix
    binding = asdict(job); binding.pop('job_sha256')
    text = prompt+sep+inputs+marker+json.dumps(binding,indent=2,sort_keys=True)+'\n```'+after
    from .self_check import instruction
    text=re.sub(r'\n<!-- C62_SELF_CHECK -->.*?<!-- /C62_SELF_CHECK -->\n','',text,flags=re.S)
    text += instruction(path)
    _write_text_once(path, text)
    job = replace(job, job_sha256=sha(path))
    ensure_once(path.with_suffix('.job.json'), asdict(job))
    return load_job(path)


def validate(job):
    """The special attempt changes transport only; original checks still run."""
    from .jobs import load_job
    binding = job.contract['contract_replacement']
    old = load_job(Path(binding['original_job']))
    if (old.contract.get('contract_replacement') or binding.get('rule') != RULE
            or old.job_sha256 != binding['original_job_sha256']
            or sha(Path(old.job_path).with_suffix('.job.json')) != binding['original_sidecar_sha256']):
        raise ValueError('Contract replacement original binding changed')
    expected = deepcopy(old.contract)
    # Validation-only transport is additive for new replacements of legacy
    # packets; it cannot alter any scientific contract field.
    if job.contract.get('collector_self_check_required') is True:
        expected['collector_self_check_required'] = True
    expected.update(attempt_id=job.contract['attempt_id'], contract_replacement=binding)
    if expected != job.contract or job.contract['attempt_id'] == old.contract.get('attempt_id'):
        raise ValueError('Contract replacement changed scientific contract')
    out = canonical_dir(old)/'attempts'/job.contract['attempt_id']
    promote = Path(old.promote_path)
    if old.stage!='S5' and promote.is_relative_to(Path(old.output_dir)): promote=out/promote.relative_to(Path(old.output_dir))
    for field in ('schema_version','stage','design_id','design_version','role','repo',
                  'template_path','template_sha256','inputs','network_access','retries'):
        if getattr(job,field) != getattr(old,field): raise ValueError('Contract replacement identity/input changed')
    if (Path(job.output_dir) != out or Path(job.output_path) != out/Path(old.output_path).name
            or Path(job.result_path) != out/Path(old.result_path).name or Path(job.promote_path) != promote):
        raise ValueError('Contract replacement output routing changed')


def publish_receipt(job, receipt):
    # Purpose acceptance and forecast roster locking read canonical receipts.
    # Never copy the rejected original or overwrite an accepted receipt.
    if job.stage in {'S0', 'S5'}:
        canonical = Path(job.output_dir).parent.parent
        ensure_once(canonical/'COLLECTION.json', receipt)
        normalization = Path(job.output_dir)/'NORMALIZATION.json'
        if normalization.exists(): ensure_once(canonical/'NORMALIZATION.json', json.loads(normalization.read_text()))
