"""F9 (REDESIGN W3 follow-up, 2026-10-18): a bounded input for the S6 reviewer packet.

The 2026-10-18 incident: sealed and draft reviewer packets were released by workers because the review needs a complete
sweep of a ~330k-token review-inputs file plus ~470 code files, and the template asks for synthetic numerical tests run
with the repository's Python, which a read-only worker cannot do. The review is not rewritten (same MUST/SHOULD
definitions, same output schema, same independence attestation, same collector); what changes is the *input* the queue hands
to the worker: the pipeline computes it, the worker judges it.

``build(job)`` returns the pack for one S6 reviewer job. It holds, inside about 40,000 tokens for everything the worker
reads (the pack, the prompt, the trimmed output schema and the packet record; 3 characters are counted as one token):

* ``identity``: design, scope, candidate hash, fix cycle, the previous STOP findings the reviewer must re-check;
* ``card``: the registered card the job carries (the mechanism text is cut at 1,500 characters; hash given);
* ``artifacts``: one compact entry per registered design artifact (SPEC and its addenda, TREATMENT, SUPPORT, PANELS,
  RECONCILIATION, FREEZE, PREDICTIONS_LOCK, RI, interval and calibration results, ...): size, SHA-256, every pass/fail flag
  found anywhere in it (``pass_flags``, so no pipeline verdict is lost by compaction) and a depth/length/string-limited copy;
* ``code``: the design's frozen code snapshot against the current pipeline tree ("the template"): files identical,
  changed, removed, a unified diff of every changed file (cut), and the result of parsing every snapshot Python file;
* ``pipeline_checks``: what the pipeline itself ran and recorded *by the time of this review*: for a sealed-candidate review the
  regression suite (plain and ``-O``: command, exit code, the ``Ran N tests`` and result lines), the dry-load, the readiness
  booleans and the earlier draft review verdict; for a draft-readiness review only the draft dry-load and, as plain facts,
  ``support_failed_variants`` (the regression suite and readiness artifacts are produced by the mechanical gates after approval,
  T258 / HUB_RULING_20261022_DRAFT_STOP, so they cannot be in a draft pack);
* ``limits``: the budget, the estimate, and every artifact that had to be cut harder than others or left out.

The worker is told to judge from the pack, run nothing, and name anything missing in a SHOULD finding. Nothing here reads
an outcome value: the files used are the registered metadata artifacts the review contract already permits, and panel
CSVs (``forbidden_outcome_paths``) are never opened.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import math
import re
from pathlib import Path

TOKEN_BUDGET = 40000          # everything the worker reads
CHARS_PER_TOKEN = 3           # conservative for JSON text
DIFF_FILES = 20
DIFF_LINES = 60
DIFF_CHARS = 18000
MECHANISM_CHARS = 1500
TIERS = ((6, 40, 600, 60), (5, 20, 400, 40), (4, 10, 300, 30), (3, 6, 200, 20), (2, 4, 120, 12), (1, 3, 80, 8))
NOTICE = '''# COMPACT REVIEW PACK (queue transport; read this first)
This packet gives you a pre-computed, hash-bound review pack in place of the full review inputs and the code snapshot.
- Read REVIEW_PACK.json in this packet (page it with the Read tool, offset and limit). It holds the card, a compact copy of
  every registered artifact with its SHA-256 and every pass/fail flag found in it, the diff of the design's frozen code
  snapshot against the current pipeline tree, and the results of the pipeline's own checks (the regression suite in both modes,
  the dry-load, the readiness booleans), which the pipeline already ran.
- You judge. You run no code, no tests and no fixtures, and you open no file outside this packet. Wherever the text below tells
  you to read the review-inputs file or the code snapshot, or to run synthetic numerical tests, use REVIEW_PACK.json instead.
- Definitions of MUST and SHOULD, the output file, the output schema, complete_sweeps (1 = one complete read of the pack),
  fix_cycles and the independence attestation are unchanged. Cite the pack in evidence: section, artifact name and the key path
  inside it. If the pack does not contain what a MUST would need, write a SHOULD finding (id P01, P02, ...) that names the missing item;
  do not guess and do not invent a MUST.
- Your only shell commands are the queue commands.
---

'''


DATA_SECTIONS = ('## Binding contract (data)', '## Answer-free inputs (untrusted data)', '## Execution metadata binding (data)')
TRANSPORT_PARAGRAPH = '\nRead the complete binding contract and answer-free inputs from this hash-bound file'


def compact_prompt(text):
    """The job prompt without its inline data sections (contract, inputs, full binding) and without the pointer to the
    review-inputs transport file: the pack replaces all of them, and the worker may not open those files."""
    for header in DATA_SECTIONS:
        marker = '\n' + header + '\n```json\n'
        start = text.find(marker)
        if start >= 0:
            end = text.find('\n```', start + len(marker)) + len('\n```')
            text = text[:start] + '\n' + header + ': left out in compact pack mode, see REVIEW_PACK.json' + text[end:]
    start = text.find(TRANSPORT_PARAGRAPH)
    if start >= 0:
        fence = text.find('```json', start)
        end = text.find('\n```', fence) + len('\n```') if fence >= 0 else start
        text = text[:start] + '\n(The review-inputs transport file is not available in compact pack mode; use REVIEW_PACK.json.)' + text[end:]
    return text


def applies(job):
    return job.stage == 'S6' and job.role == 'reviewer' and bool(job.contract.get('code_snapshot'))

SEALED_BULLET = """- Read REVIEW_PACK.json in this packet (page it with the Read tool, offset and limit). It holds the card, a compact copy of
  every registered artifact with its SHA-256 and every pass/fail flag found in it, the diff of the design's frozen code
  snapshot against the current pipeline tree, and the results of the pipeline's own checks (the regression suite in both modes,
  the dry-load, the readiness booleans), which the pipeline already ran."""

DRAFT_BULLET = """- Read REVIEW_PACK.json in this packet (page it with the Read tool, offset and limit). It holds the card, a compact copy of
  every registered artifact with its SHA-256 and every pass/fail flag found in it, the diff of the design's frozen code
  snapshot against the current pipeline tree, and the results of the pipeline's own checks that exist at the draft stage
  (the draft dry-load, and the registered variants whose support failed, as plain facts). The regression suite (plain and -O)
  and the readiness booleans (FREEZE_FINAL, CALIBRATION_GATES, FINAL_READINESS) do NOT exist at this stage: the mechanical
  gates produce them after approval and approve-final refuses to run without them, so their absence from this pack is
  expected and is not a finding."""


def notice_for(job):
    """The packet NOTICE for this reviewer job: the draft-readiness review gets the truthful bullet (no regression-suite
    results at the draft stage); the sealed-candidate review keeps the original text byte for byte."""
    if job.contract.get('review_scope') == 'draft_readiness':
        return NOTICE.replace(SEALED_BULLET, DRAFT_BULLET)
    return NOTICE



def _sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dump(value):
    return json.dumps(value, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False)


# --- compaction -------------------------------------------------------------------------------------------------------

def shrink(value, depth, max_list, max_str, max_keys):
    """A copy of ``value`` limited in depth, list length, string length and keys per object; the cut is always marked."""
    if isinstance(value, dict):
        if depth <= 0:
            return {'_object_keys': sorted(map(str, value))[:max_keys], '_n': len(value)}
        keys = sorted(value, key=str)
        out = {str(k): shrink(value[k], depth - 1, max_list, max_str, max_keys) for k in keys[:max_keys]}
        if len(keys) > max_keys:
            out['_more_keys'] = dict(n=len(keys) - max_keys, names=[str(k) for k in keys[max_keys:max_keys + 12]])
        return out
    if isinstance(value, list):
        if depth <= 0:
            return {'_list_len': len(value)}
        head = [shrink(x, depth - 1, max_list, max_str, max_keys) for x in value[:max_list]]
        return head if len(value) <= max_list else {'_list_len': len(value), 'first': head}
    if isinstance(value, str) and len(value) > max_str:
        return value[:max_str] + '...[+' + str(len(value) - max_str) + ' chars; sha256 ' + hashlib.sha256(value.encode()).hexdigest()[:16] + ']'
    return value


def fit(value, cap):
    """The least-cut copy of ``value`` whose JSON is at most ``cap`` characters, with the tier used."""
    for tier, limits in enumerate(TIERS):
        reduced = shrink(value, *limits)
        if len(_dump(reduced)) <= cap:
            return reduced, tier
    keys = sorted(map(str, value)) if isinstance(value, dict) else None
    return {'_cut_to_fit': True, 'top_level_keys': (keys or [])[:30]}, len(TIERS)


def pass_flags(value, limit=60):
    """Every boolean whose key says pass/valid/ok/complete, with its key path (the pipeline's own verdicts), first ``limit``."""
    found = {}

    def visit(item, path):
        if len(found) >= limit:
            return
        if isinstance(item, dict):
            for key in sorted(item, key=str):
                child = item[key]
                here = path + [str(key)]
                if isinstance(child, bool) and re.search(r'pass|valid|ok$|complete|ready|support_pass|feasible', str(key), re.I):
                    found['.'.join(here)] = child
                else:
                    visit(child, here)
        elif isinstance(item, list):
            for i, child in enumerate(item[:50]):
                visit(child, path + [str(i)])
    visit(value, [])
    return found


def regression_summary(value):
    """TEST_EVIDENCE.json without the transcripts: per run the command, exit code and the closing lines."""
    runs = []
    for run in value.get('runs', []) if isinstance(value, dict) else []:
        text = str(run.get('transcript', ''))
        lines = [ln for ln in text.splitlines() if ln.strip()]
        ran = next((ln for ln in reversed(lines) if re.match(r'Ran \d+ tests?', ln)), None)
        verdict = next((ln for ln in reversed(lines) if re.match(r'(OK|FAILED)', ln)), None)
        runs.append(dict(command=[Path(str(c)).name if i == 0 else c for i, c in enumerate(run.get('command', []))],
                         exit_code=run.get('exit_code'), ran=ran, verdict=verdict, tail=lines[-6:],
                         transcript_chars=len(text), transcript_sha256=hashlib.sha256(text.encode()).hexdigest()))
    return dict(schema=value.get('schema'), passed=value.get('passed'), no_archival_fits=value.get('no_archival_fits'),
                code_files=len(value.get('code_inventory', {})), runs=runs)


# --- sections ---------------------------------------------------------------------------------------------------------

def code_section(snapshot_tree, inventory, entries=None):
    """The frozen snapshot against the current tree. ``inventory`` maps current absolute paths to the hash at snapshot time;
    ``snapshot_tree`` is the snapshot's ``tree/`` directory and ``entries`` maps tree-relative paths to hashes (for the parse check)."""
    snapshot_tree = Path(snapshot_tree)
    identical, changed, removed, diffs, used = 0, [], [], [], 0
    for path, old in sorted(inventory.items()):
        live = Path(path)
        if not live.is_file():
            removed.append(path)
        elif _sha_file(live) == old:
            identical += 1
        else:
            changed.append(dict(path=path, snapshot_sha256=old, current_sha256=_sha_file(live)))
    parsed, errors = 0, []
    for rel in sorted(entries or {}):
        if rel.endswith('.py'):
            try:
                ast.parse((snapshot_tree / rel).read_bytes())
                parsed += 1
            except (SyntaxError, ValueError, OSError) as exc:
                errors.append(dict(path=rel, error=type(exc).__name__ + ': ' + str(exc)[:160]))
    for item in changed[:DIFF_FILES]:
        live = Path(item['path'])
        rel = next((r for r in (entries or {}) if item['path'].endswith('/' + r) or item['path'] == r), None)
        before = (snapshot_tree / rel) if rel else None
        if before is None or not before.is_file() or live.suffix not in ('.py', '.json', '.md', '.txt', ''):
            diffs.append(dict(path=item['path'], diff=['(no text diff: binary or not in the snapshot tree)']))
            continue
        try:
            lines = list(difflib.unified_diff(before.read_text().splitlines(), live.read_text().splitlines(),
                                              'snapshot/' + rel, 'current/' + rel, lineterm='', n=2))
        except (UnicodeDecodeError, OSError):
            lines = ['(not readable as text)']
        keep = [ln[:300] for ln in lines[:DIFF_LINES]]
        cost = sum(len(ln) + 1 for ln in keep)
        if used + cost > DIFF_CHARS:
            diffs.append(dict(path=item['path'], diff=['(diff left out: the total diff budget is spent; hashes above)']))
            continue
        used += cost
        diffs.append(dict(path=item['path'], diff=keep + (['... (' + str(len(lines) - DIFF_LINES) + ' more diff lines)'] if len(lines) > DIFF_LINES else [])))
    return dict(reference='current pipeline tree (the template every design runs); the snapshot is what this design executes',
                files_in_snapshot=len(inventory), identical=identical, changed_count=len(changed), removed_count=len(removed),
                changed=changed[:DIFF_FILES * 2], changed_not_listed=max(0, len(changed) - DIFF_FILES * 2), removed=removed[:20],
                diffs=diffs, python_files_parsed=parsed, python_parse_errors=errors[:20])


def contract_view(contract):
    """The contract without its bulk lists (counts and a hash stand in), for the packet's OUTPUT_SCHEMA.json."""
    bulk = {'code_inventory', 'permitted_code_paths', 'snapshot_source_inventory', 'permitted_metadata_paths',
            'forbidden_outcome_paths', 'card', 'card_sources', 'code_snapshot'}
    view = {k: v for k, v in contract.items() if k not in bulk}
    for key in sorted(bulk & set(contract)):
        item = contract[key]
        view[key + '_summary'] = dict(count=len(item) if hasattr(item, '__len__') else None,
                                      sha256=hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest())
    return view


def sources(job, design_dir=None):
    """(entry without content, value to compact) for every registered JSON artifact the job binds; never a panel or outcome file."""
    forbidden = {str(Path(p).resolve()) for p in job.contract.get('forbidden_outcome_paths', [])}
    items = []
    for item in job.inputs:
        path = Path(item['path'])
        name = path.name
        if str(path.resolve()) in forbidden or name == 'panel.csv' or path.suffix != '.json' or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        try:
            shown = str(path.resolve().relative_to(Path(design_dir).resolve())) if design_dir else name
        except ValueError:
            shown = name
        entry = dict(name=shown, sha256=item['sha256'], size_bytes=path.stat().st_size, pass_flags=pass_flags(value))
        items.append((entry, regression_summary(value) if name == 'TEST_EVIDENCE.json' else value))
    return items


def allocate(sizes, room):
    """Water-filling: an artifact that fits its fair share keeps its natural size, the rest share what is left."""
    caps = [0] * len(sizes)
    left, pending = room, sorted(range(len(sizes)), key=lambda i: sizes[i])
    while pending:
        share = left // len(pending)
        i = pending.pop(0)
        caps[i] = min(sizes[i], max(share, 400))
        left -= caps[i]
    return caps


def artifacts(job, room, design_dir=None, items=None):
    """One compact entry per registered artifact, together at most about ``room`` characters."""
    items = items if items is not None else sources(job, design_dir)
    sizes = [len(_dump(shrink(value, *TIERS[0]))) for _, value in items]
    rows, tiers = [], {}
    for (entry, value), cap in zip(items, allocate(sizes, room)):
        content, tier = fit(value, cap)
        rows.append(dict(entry, content=content, compaction_tier=tier))
        if tier:
            tiers[entry['name']] = tier
    return rows, tiers


def support_failed_variants(design_dir):
    """Plain facts for a draft pack: every registered variant whose support check failed (variant, issues, the transition-year-drop flag
    of the primary and whether the primary passed). Read from the effective SUPPORT file; None when it is absent or unreadable."""
    root = Path(design_dir)
    for name in ('SUPPORT_C79.json', 'SUPPORT.json'):
        path = root / name
        if path.is_file():
            break
    else:
        return None
    try:
        rows = json.loads(path.read_text())['variants']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    primary = next((r for r in rows if r.get('variant') == 'primary'), {})
    return dict(source=path.name, primary_passed=primary.get('passed'), transition_year_drop=primary.get('transition_year_drop'),
                variants=[dict(variant=r.get('variant'), disposition=r.get('disposition'), issues=r.get('issues'))
                          for r in rows if r.get('variant') != 'primary' and r.get('passed') is False])


def build(job, overhead_chars=0):
    """The review pack for one S6 reviewer job (a dict). ``overhead_chars``: characters the worker reads besides the pack."""
    c = job.contract
    design_dir = Path(c['snapshot_root'])
    record = c['code_snapshot']
    budget = TOKEN_BUDGET * CHARS_PER_TOKEN - overhead_chars
    card = dict(c.get('card') or {})
    if isinstance(card.get('mechanism'), str) and len(card['mechanism']) > MECHANISM_CHARS:
        card['mechanism'] = card['mechanism'][:MECHANISM_CHARS] + '...[cut; ' + str(len(c['card']['mechanism'])) + ' characters in the registered card]'
    head = dict(
        schema='l2_review_pack_v1', identity=dict(
            design_id=job.design_id, design_version=job.design_version, review_scope=c.get('review_scope'),
            candidate_sha256=c.get('candidate_sha256'), expected_fix_cycles=c.get('expected_fix_cycles'),
            previous_must_ids=c.get('previous_must_ids', []), missing_readiness_artifacts=c.get('missing_readiness_artifacts', []),
            job_sha256=job.job_sha256, output_schema='see OUTPUT_SCHEMA.json (review_output_schema)'),
        card=card, card_sources=c.get('card_sources', {}))
    tree = design_dir / record['base'] / 'tree'
    code = code_section(tree, c.get('snapshot_source_inventory', {}), record.get('entries', {}))
    # Pipeline checks come from the bound artifacts themselves.
    names = {Path(i['path']).name: Path(i['path']) for i in job.inputs}
    checks = {}
    for key, name in (('regression', 'TEST_EVIDENCE.json'), ('dry_load', 'DRY_LOAD.json'), ('dry_load_draft', 'DRY_LOAD_DRAFT.json'),
                      ('readiness', 'FINAL_READINESS.json'), ('readiness_evidence', 'FINAL_READINESS_EVIDENCE.json'),
                      ('earlier_review', 'REVIEW.json'), ('ri_feasibility', 'RI_FEASIBILITY.json')):
        path = names.get(name) or (design_dir / name)
        if path.is_file():
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            checks[key] = regression_summary(value) if key == 'regression' else fit(value, 4000)[0]
    if c.get('review_scope') == 'draft_readiness':
        failed = support_failed_variants(design_dir)
        if failed is not None:
            checks['support_failed_variants'] = failed
    fixed = len(_dump(dict(head, code=code, pipeline_checks=checks)))
    room = max(8000, budget - fixed - 4000)
    items = sources(job, design_dir)
    rows, tiers = artifacts(job, room, design_dir, items)
    pack = dict(head, artifacts=rows, code=code, pipeline_checks=checks)
    # Tighten until the whole pack fits; then drop the largest compact entries as a last resort (their hash and flags stay listed).
    left_out = []
    while len(_dump(pack)) > budget - 1500 and room > 1500:
        room = int(room * 0.85)
        rows, tiers = artifacts(job, room, design_dir, items)
        pack = dict(head, artifacts=rows, code=code, pipeline_checks=checks)
    while len(_dump(pack)) > budget - 1500 and pack['artifacts']:
        victim = max(range(len(pack['artifacts'])), key=lambda i: len(_dump(pack['artifacts'][i])))
        gone = pack['artifacts'].pop(victim)
        left_out.append(dict(name=gone['name'], sha256=gone['sha256'], size_bytes=gone['size_bytes'], pass_flags=gone['pass_flags']))
    pack['limits'] = dict(token_budget_all_reads=TOKEN_BUDGET, chars_per_token=CHARS_PER_TOKEN, overhead_chars=overhead_chars,
                          pack_budget_chars=budget, compacted_artifacts=tiers, left_out=left_out,
                          note='compaction keeps every pass/fail flag; sha256 identifies the full registered artifact')
    text = _dump(pack)
    pack['limits']['pack_chars'] = len(text)
    pack['limits']['pack_tokens_estimate'] = math.ceil(len(_dump(pack)) / CHARS_PER_TOKEN)
    return pack
