"""Stopped-host mechanical code revisions; sealed inputs remain immutable."""
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from .autopilot import read, Autopilot
from .storage import ensure_once, sha
from .population_resolution import bind, rebase_sources
from .predictions import primary_forecast_target, import_unchanged_predictions

# --- Authorised added sensitivity (T187, 2026-10-20) ---------------------------------------------------------------------------------
# A C57 rebind keeps the scientific card byte-for-byte. The one exception is a hub-approved report-only sensitivity: the rebind may then
# differ from the old card in exactly (a) one sensitivity appended at the end, (b) the stale `analysis.screen_expected_support` block
# removed, (c) the C57 revision note. It is allowed only when the ruling carries `added_sensitivity` and BOTH hub files it binds say so:
# the approval file has a "Hub approval" line that names the design, and the register file has the line
# `C57-SENSITIVITY <design_id> <name> <sha256 of the canonical sensitivity JSON>`. The primary specification, every earlier sensitivity,
# the estimand and the forecast target are untouched; anything else still fails with "changed scientific card".
RULING_ROOT = Path(__file__).resolve().parents[2] / 'docs/plans/l2-pipeline'
ADDED_KEYS = {'name', 'sensitivity_sha256', 'approval', 'register', 'drop_screen_expected_support'}
ALLOWED_CHANGES = ('excluded_units', 'start_overrides')
_APPROVAL = re.compile(r'^[\s>"*_-]*Hub approval \d{4}-\d{2}-\d{2}')


def sensitivity_sha256(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _hub_file(ref, root):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('C57 sensitivity authority reference malformed')
    path = Path(ref['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.name.startswith('HUB_RULING_') or path.suffix != '.md':
        raise ValueError('C57 sensitivity authority must be a HUB_RULING_*.md file under docs/plans/l2-pipeline')
    if sha(path) != ref['sha256']:
        raise ValueError('C57 sensitivity authority changed')
    return path


def authorised_sensitivity(extra, entry, design_id, root=None):
    """Fail-closed: True only when the two hub files authorise exactly this design and this sensitivity entry."""
    if not isinstance(extra, dict) or set(extra) != ADDED_KEYS or type(extra['drop_screen_expected_support']) is not bool:
        raise ValueError('C57 added sensitivity malformed')
    root = RULING_ROOT if root is None else root
    approval, register = _hub_file(extra['approval'], root), _hub_file(extra['register'], root)
    if not any(design_id in line for line in approval.read_text().splitlines() if _APPROVAL.match(line)):
        raise ValueError('C57 added sensitivity: no "Hub approval" line names the design')
    if approval.name not in register.read_text():
        raise ValueError('C57 added sensitivity: register does not cite the approval ruling')
    marker = 'C57-SENSITIVITY %s %s %s' % (design_id, extra['name'], extra['sensitivity_sha256'])
    if marker not in {line.strip() for line in register.read_text().splitlines()}:
        raise ValueError('C57 added sensitivity: register does not name this design and exact sensitivity')
    if (not isinstance(entry, dict) or set(entry) != {'name', 'changes'} or entry['name'] != extra['name']
            or sensitivity_sha256(entry) != extra['sensitivity_sha256']):
        raise ValueError('C57 added sensitivity differs from the registered one')
    changes = entry['changes']
    if not isinstance(changes, dict) or set(changes) != {'treatment'} or not isinstance(changes['treatment'], dict) \
            or len(changes['treatment']) != 1 or next(iter(changes['treatment'])) not in ALLOWED_CHANGES:
        raise ValueError('C57 added sensitivity may change only treatment.excluded_units or treatment.start_overrides')
    (key, value), = changes['treatment'].items()
    if key == 'excluded_units' and not (isinstance(value, list) and value and all(isinstance(u, str) for u in value) and value == sorted(set(value))):
        raise ValueError('C57 added sensitivity excluded_units must be a sorted list of unique units')
    if key == 'start_overrides' and not (isinstance(value, dict) and value and all(isinstance(u, str) and type(y) is int for u, y in value.items())):
        raise ValueError('C57 added sensitivity start_overrides must map units to integer periods')
    return True


def apply_addition(card, extra, entry, design_id, root=None):
    """The card a rebind with an authorised addition must be (pure; `card` is the rebased, version-bumped old card)."""
    authorised_sensitivity(extra, entry, design_id, root)
    card = deepcopy(card)
    if entry['name'] in {s.get('name') for s in card.get('sensitivities', [])} | {'primary'}:
        raise ValueError('C57 added sensitivity name already registered')
    card['sensitivities'] = [*card['sensitivities'], deepcopy(entry)]
    if extra['drop_screen_expected_support']:
        if 'screen_expected_support' not in card['analysis']:
            raise ValueError('C57 added sensitivity: no stale screen block to drop')
        card['analysis'] = {k: v for k, v in card['analysis'].items() if k != 'screen_expected_support'}
    revision = card['automation']['c57_implementation_revision']
    revision.update(science_unchanged=False, primary_science_unchanged=True,
                    added_sensitivity=dict(name=extra['name'], sensitivity_sha256=extra['sensitivity_sha256'],
                                           approval=extra['approval'], register=extra['register'],
                                           drop_screen_expected_support=extra['drop_screen_expected_support']))
    return card


def check_added_controls(treatment, entry, primary_excluded):
    """S3/S4-time check for an `excluded_units` sensitivity: it only ADDS units to the primary exclusions, and every added unit is a retained
    never-treated control of TREATMENT.json (so the variant removes controls from the pool, nothing else). Returns the added units."""
    value = entry['changes']['treatment'].get('excluded_units')
    if value is None:
        return []
    added = sorted(set(value) - set(primary_excluded))
    if not set(primary_excluded) <= set(value):
        raise ValueError('C57 added sensitivity drops a primary exclusion')
    status = {r['unit']: r['status'] for r in treatment['records']}
    bad = [u for u in added if status.get(u) != 'never_treated']
    if not added or bad:
        raise ValueError('C57 added sensitivity must add retained never-treated controls only: ' + ','.join(bad))
    return added


CONSUMED=('APPROVAL_FINAL.json','EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json','RESULT.json')


def validate_rebind(p, old, ruling, card_path):
    authority=ruling['authority'];auth=read(authority['path'])
    if sha(authority['path'])!=authority['sha256'] or auth.get('schema')!='l2_c57_mechanical_authorization_v1':
        raise ValueError('C57 authority changed')
    root=p.backend.root(old)
    if (not (p.registry/'AUTOPILOT_STOP').exists() or old['design_id'] not in auth['designs']
        or any((root/n).exists() for n in CONSUMED) or old.get('status')=='COMPLETED'
        or ruling.get('execution_authorized') is not False or ruling.get('superseded_status')!='SUPERSEDED_C57'):
        raise ValueError('C57 cannot revise consumed/unauthorized design')
    expected=rebase_sources(read(old['card']),old['card']);expected['version']='v'+str(int(expected['version'][1:])+1)
    expected.setdefault('automation',{})['c57_implementation_revision']=dict(prior_card=bind(old['card']),
        prior_spec=bind(root/'SPEC.json'),authority=authority,science_unchanged=True,execution_authorized=False)
    extra=ruling.get('added_sensitivity')
    if extra is not None:
        proposed=read(card_path)
        expected=apply_addition(expected,extra,(proposed.get('sensitivities') or [None])[-1],old['design_id'])
    if read(card_path)!=expected:raise ValueError('C57 mechanical revision changed scientific card')
    if ruling.get('prior_spec')!=bind(root/'SPEC.json') or ruling.get('prior_support')!=bind(root/'SUPPORT.json'):
        raise ValueError('C57 prior science binding changed')
    if not p.stop_record(old):raise ValueError('C57 requires retained stop evidence')


def science_equal(old, new):
    """All SPEC fields except version/source-byte identities must be identical."""
    fields=set(old)|set(new)
    return [k for k in sorted(fields-{'version','card_sha256'}) if without_labels(old).get(k)!=without_labels(new).get(k)]


# REDESIGN R1 (HUB_RULING_20261017_REDESIGN.md: purpose is a label for L3, not science): the only label-only SPEC field is
# l3_export_v2.purpose_label, which S4 stamps. Nothing else is ignored.
LABEL_ONLY_FIELDS=(('l3_export_v2','purpose_label'),)


def without_labels(spec):
    spec=dict(spec)
    for outer,inner in LABEL_ONLY_FIELDS:
        if isinstance(spec.get(outer),dict) and inner in spec[outer]:
            spec[outer]={k:v for k,v in spec[outer].items() if k!=inner}
    return spec


def science_equal_added(old, new, name, drop_screen_expected_support):
    """science_equal when the only authorised change is ONE report-only variant `name` (and the stale screen block dropped): the new SPEC
    minus that variant (SPEC sensitivity and SUPPORT row, wherever they sit: S1 may append its own audit variant after it) and with the old
    screen block put back must equal the old SPEC. Returns the differing fields (empty = fine); a missing or duplicated variant is itself
    a difference."""
    new = deepcopy(new)
    sens = new.get('sensitivities') or []
    rows = [r for r in (new.get('support') or {}).get('variants', []) if r.get('variant') == name]
    if [s for s in sens if s.get('name') == name] == [] or sum(s.get('name') == name for s in sens) != 1 or len(rows) != 1 \
            or any(s.get('name') == name for s in old.get('sensitivities') or []) \
            or any(r.get('variant') == name for r in (old.get('support') or {}).get('variants', [])):
        return ['sensitivities', 'support']
    new['sensitivities'] = [s for s in sens if s.get('name') != name]
    new['support'] = dict(new['support'], variants=[r for r in new['support']['variants'] if r.get('variant') != name])
    if drop_screen_expected_support:
        if 'screen_expected_support' in new.get('analysis', {}) or 'screen_expected_support' not in old.get('analysis', {}):
            return ['analysis']
        new['analysis'] = dict(new['analysis'], screen_expected_support=old['analysis']['screen_expected_support'])
    return science_equal(old, new)


def migrate(p, old, output, authority, addition=None):
    """`addition` = dict(extra=<ruling added_sensitivity>, entry=<sensitivity>) for an authorised added sensitivity (see apply_addition)."""
    from .requeue import requeue
    from .agents import emit_stage_jobs
    from .agents.jobs import PERSONAS
    from .registration import lock
    output=Path(output);root=p.backend.root(old)
    prior=read(output/'APPLICATION.json')
    if prior:return prior
    card=rebase_sources(read(old['card']),old['card']);card['version']='v'+str(int(card['version'][1:])+1)
    card.setdefault('automation',{})['c57_implementation_revision']=dict(prior_card=bind(old['card']),
        prior_spec=bind(root/'SPEC.json'),authority=bind(authority),science_unchanged=True,execution_authorized=False)
    if addition:card=apply_addition(card,addition['extra'],addition['entry'],old['design_id'])
    path=ensure_once(output/'CARD.json',card)
    ruling=ensure_once(output/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=old['design_id'],
        card_sha256=sha(path),prior_card_sha256=sha(old['card']),decision='accept',purpose_rule='V6.2',
        implementation_rule='C57',superseded_status='SUPERSEDED_C57',authority=bind(authority),impact_tags=['preparation_binding','draft_load','review_should'],
        prior_spec=bind(root/'SPEC.json'),prior_support=bind(root/'SUPPORT.json'),
        reason='C57 mechanical implementation rebind; retained science and forecasts; fresh draft/sealed reviews required',
        execution_authorized=False,**(dict(added_sensitivity=addition['extra']) if addition else {})))
    receipt=requeue(old['design_id'],path,ruling,p);row=receipt['new_row'];stages=[]
    for action in ('S0','S1','S2','S3','S4'):
        if not p.done(row,action):
            value=p.backend.perform(row,action,p);ensure_once(p.checkpoint(row,action),value);stages.append(action)
    newroot=p.backend.root(row);oldspec=read(root/'SPEC.json');newspec=read(newroot/'SPEC.json')
    differences=science_equal_added(oldspec,newspec,addition['extra']['name'],addition['extra']['drop_screen_expected_support']) if addition else science_equal(oldspec,newspec)
    if differences:raise ValueError('C57 science changed verbatim: '+__import__('json').dumps({k:dict(old=oldspec.get(k),new=newspec.get(k)) for k in differences}))
    if primary_forecast_target(oldspec)!=primary_forecast_target(newspec):raise ValueError('C57 forecast target changed')
    # An addition keeps the personas of the old lock (R2 bundle mode may hold fewer than the five defaults); the old route keeps PERSONAS.
    forecast=(import_unchanged_predictions(root,newroot,list(read(root/'PREDICTIONS_LOCK.json')['personas']),addition['extra']['name']) if addition
        else import_unchanged_predictions(root,newroot,list(PERSONAS)));lock(p.backend.ctx(row))
    ensure_once(p.checkpoint(row,'forecasts'),dict(imported=True,evidence=bind(forecast),jobs=[]))
    ensure_once(p.checkpoint(row,'lock'),dict(imported=True,lock=bind(newroot/'PREDICTIONS_LOCK.json')))
    previous=read(p.case_dir(old)/'jobs/draft.json')['jobs'][0]
    previous_path=Path(read(Path(previous).with_suffix('.job.json'))['promote_path'])
    if read(previous_path).get('verdict')!='STOP': previous_path=None
    jobs=emit_stage_jobs(p.backend.ctx(row,strict=True),'S6',jobs_root=output/'draft_jobs',allow_draft=True,
        attempt_id=row['attempt_id']+'_c57',previous_review_path=previous_path)
    ensure_once(p.case_dir(row)/'jobs/draft.json',dict(jobs=[j.job_path for j in jobs]))
    value=dict(design_id=row['design_id'],prior_row=old,new_row=row,rebuilt_stages=stages,
        prior_spec=bind(root/'SPEC.json'),new_spec=bind(newroot/'SPEC.json'),prior_support=bind(root/'SUPPORT.json'),
        new_support=bind(newroot/'SUPPORT.json'),science_differing_fields=differences,science_unchanged=not addition,
        **(dict(added_sensitivity=addition['extra']['name'],primary_science_unchanged=True) if addition else {}),
        forecast_import=bind(forecast),draft_jobs=[bind(j.job_path) for j in jobs],
        next_action=p.next_action(row),execution_authorized=False,dispatches=0,actual_data_estimation_calls=0)
    ensure_once(output/'APPLICATION.json',value);return value


def stale_flag(root, audit, authority, output):
    """Additive set annotation; never edit any locked forecast or sealed file."""
    root=Path(root);lock_path=root/'PREDICTIONS_LOCK.json';lock=read(lock_path)
    fields={}
    job=next(j for j in audit['jobs'] if j['stage']=='S5')
    if job['old_controls']!=job['registered_controls']:
        fields['analysis.controls']=dict(contract=job['old_controls'],registered=job['registered_controls'])
    if job['old_screen_support']!=audit['registered_treated_n']:
        fields['analysis.screen_expected_support.treated_n']=dict(contract=job['old_screen_support'],registered=audit['registered_treated_n'])
    for relative,h in {**lock['files'],**lock['original_files']}.items():
        if sha(root/relative)!=h:raise ValueError('Forecast set changed')
    return ensure_once(output,dict(schema='l2_forecast_set_annotation_v1',design_id=audit['design_id'],
        version=audit['version'],forecast_lock=bind(lock_path),stale_contract_card=True,differing_fields=fields,
        differing_fields_empty=not bool(fields),authority=bind(authority),forecasts_stand=True,
        l3_q3_include_and_exclude=True,original_forecasts_modified=False,actual_data_estimation_calls=0))
