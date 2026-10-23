"""C81: exactly one focused completion per design; immutable original evidence."""
from pathlib import Path
import copy, json
from .storage import ensure_once, sha
from .c42 import Park
RULE='HUB_RULING_C81_HISTORY_COMPLETION.md'

def read(p): return json.loads(Path(p).read_text())
def bind(p): return dict(path=str(p),sha256=sha(p))
ATTEMPT='c81_completion'

def legacy_owned(intent,attempt):
    """True when a slot written before attempt keying (the design directory itself) belongs to `attempt`:
    its INTENT names an original job whose contract carries that attempt id."""
    from .agents.jobs import load_job
    try: return load_job(Path(read(intent)['original_job']['path'])).contract.get('attempt_id')==attempt
    except (OSError,ValueError,KeyError,TypeError): return False

def slot(pilot,row):
    """One completion slot per (design, attempt) (2026-10-18). The slot of an earlier attempt, and every job written
    in it, stays as it is; a reopened attempt (requeue) gets `attempts/<attempt_id>` beside it. A slot written before
    this keying stays in use by the attempt that owns it. A row without an attempt id keeps the design directory."""
    base=pilot.registry.parent/'autopilot/history_completion'/row['design_id'];attempt=row.get('attempt_id')
    if attempt is None or legacy_owned(base/'INTENT.json',attempt):return base
    from .agents.jobs import _safe_component
    _safe_component(attempt)
    return base/'attempts'/attempt

def queue_park(reason,job):
    """An engineering event of the packet (not a check on the design) is a reopenable PARK, never a STOP (F10)."""
    from .release_policy import PREFIX,PARK_KIND
    return Park(PREFIX+reason,dict(kind=PARK_KIND,job=str(job),reopen='release_recovery',outcome_values_read=0,rule=RULE))

def completion_job(path,root,job,units):
    """The completion job of this slot. One already written is loaded as written and never rendered again (the prompt
    text, the template or the host plan may have moved on since; the bytes of a job a worker may hold must not change).
    A new one is emitted once. A write-once failure parks the design (reopenable); it never stops it."""
    from .agents.history import emit_history_job
    from .agents.jobs import load_job
    from .storage import output_root
    written=output_root(root/'job',Path(job.repo))/'S1'/job.design_id/(job.role+'.'+ATTEMPT+'.md')
    try:
        if written.is_file() and written.with_suffix('.job.json').is_file():
            found=load_job(written)
            if ((found.design_id,found.role)!=(job.design_id,job.role) or found.contract.get('history_completion') is not True
                    or found.contract['manual']['universe']!=list(units)
                    or found.contract.get('previous_packet_hashes',{}).get(str(Path(job.job_path)))!=job.job_sha256):
                raise ValueError('Existing completion job is not bound to this original and gap: '+str(written))
            return found
        return emit_history_job(path,jobs_root=root/'job',attempt_id=ATTEMPT,completion_units=units)
    except ValueError as exc:
        if isinstance(exc,Park) or not str(exc).startswith(('Existing job changed','Existing preparation artifact changed','Existing completion job is not bound')):raise
        raise queue_park('history-completion packet cannot be written again unchanged ('+str(exc)[:240]+'); no check of the design failed',job.job_path) from exc

def gaps(job,value):
    known={r.get('unit') for r in value.get('records',[]) if isinstance(r,dict) and r.get('status') is not None}
    return [u for u in job.contract['manual']['universe'] if u not in known]

def completed_value(job,value):
    from .manual_evidence import collection_directory
    pointer=collection_directory(job)/'C81_COMPLETION.json'
    if not pointer.exists(): return value
    receipt=read(pointer)
    for key in ('original','completion','merged'):
        b=receipt[key]
        if sha(b['path'])!=b['sha256']:raise ValueError('C81 completion binding changed')
    if receipt['original']!=bind(job.output_path):raise ValueError('C81 original identity changed')
    return read(receipt['merged']['path'])

def raw_records(path):
    # Preserve every original record's exact JSON bytes, including whitespace.
    text=Path(path).read_bytes().decode("utf-8");decoder=json.JSONDecoder();i=text.index('{')+1
    while True:
        while text[i].isspace() or text[i]==',':i+=1
        key,end=decoder.raw_decode(text,i);i=end
        while text[i].isspace() or text[i]==':':i+=1
        if key=='records':break
        _,i=decoder.raw_decode(text,i)
    if text[i]!='[':raise ValueError('History records must be an array')
    i+=1;out={}
    while True:
        while text[i].isspace() or text[i]==',':i+=1
        if text[i]==']':break
        row,end=decoder.raw_decode(text,i)
        if row['unit'] in out:raise ValueError('Duplicate original history unit')
        out[row['unit']]=text[i:end];i=end
    return out

def merge(original,completion,units):
    left=read(original);right=read(completion);rows=left.get('records',[])
    if len({r['unit'] for r in rows})!=len(rows):raise ValueError('Duplicate original units')
    if {r['unit'] for r in right['records']}!=set(units):raise ValueError('Completion changed gap universe')
    if any(r.get('status') is not None for r in rows if r['unit'] in units):raise ValueError('Completion overwrites classified record')
    # Source IDs cannot silently change the meaning of preserved records.
    sources={s['source_id']:s for s in left.get('sources',[])}
    for source in right.get('sources',[]):
        if source['source_id'] in sources and sources[source['source_id']]!=source:
            raise ValueError('Completion source ID collides with original; use unique source IDs')
        sources[source['source_id']]=source
    by={r['unit']:r for r in right['records']}
    merged=copy.deepcopy(left);merged['records']=[by.get(r['unit'],r) for r in rows]
    merged['records'] += [by[u] for u in units if u not in {r['unit'] for r in rows}]
    merged.update(collection_complete=True,sources=list(sources.values()),
        research_log=left.get('research_log',[])+right.get('research_log',[]))
    slices=raw_records(original);fresh=raw_records(completion)
    records=[fresh[r['unit']] if r['unit'] in units else slices[r['unit']] for r in merged['records']]
    merged.pop('records')
    body=json.dumps(merged,ensure_ascii=False,sort_keys=True,indent=2)
    return (body[:-1]+',"records":[\n'+',\n'.join(records)+'\n]}\n').encode()

def complete_history(pilot,row,path):
    from .agents.jobs import load_job
    from .manual_evidence import collection_directory
    from .c59_queue import require_complete_history
    from .c70_l3 import protected
    job=load_job(Path(path));root=slot(pilot,row);intent=root/'INTENT.json'
    if protected(pilot.backend.root(row)):raise ValueError('C81 cannot amend protected design')
    original=read(job.output_path);units=gaps(job,original)
    if not units:raise Park('INSUFFICIENT_WORK: explicit incomplete checkpoint has no identifiable gap; final',dict(final=True))
    receipt=dict(original_job=bind(job.job_path),original=bind(job.output_path),role=job.role,units=units,rule=RULE)
    if intent.exists() and read(intent)!=receipt:
        chosen=read(intent)
        original_chosen=load_job(Path(chosen['original_job']['path']))
        if not (collection_directory(original_chosen)/'C81_COMPLETION.json').exists():
            # Let the sole selected completion finish before assessing a second role gap.
            complete_history(pilot,row,original_chosen.job_path)
            return False
        raise Park('INSUFFICIENT_WORK: peer role also incomplete after sole design completion; final',dict(final=True))
    ensure_once(intent,receipt)
    completion=completion_job(path,root,job,units)
    from .agents.replacement import ContractRejection
    try: state=pilot.backend.job_state(completion.job_path)
    except ContractRejection as exc:
        raise Park('INSUFFICIENT_WORK: completion failed without usable submission; final',dict(final=True,role=job.role)) from exc
    pilot.ledger(row,'HISTORY_COMPLETION',state,'record',role=job.role,job=completion.job_path,units=units,rule_c81=RULE)
    if state!='done':
        if state=='pending' and not any(p==completion.job_path for _,p in pilot.pending):pilot.pending.append((row,completion.job_path))
        return False
    try:
        require_complete_history(completion,read(completion.output_path))
        pilot.backend.collect(completion.job_path)
    except (Park,ContractRejection,ValueError) as exc:
        raise Park('INSUFFICIENT_WORK: completion incomplete or invalid; final',dict(final=True,role=job.role,completion_error=str(exc))) from exc
    merged=root/'MERGED_HISTORY.json';data=merge(job.output_path,completion.output_path,units)
    if merged.exists():
        if merged.read_bytes()!=data:raise ValueError('C81 immutable merged history differs')
    else:
        with merged.open('xb') as stream:stream.write(data)
    ensure_once(collection_directory(job)/'C81_COMPLETION.json',dict(original=bind(job.output_path),completion=bind(completion.output_path),merged=bind(merged),classified_records_preserved_byte_for_byte=True))
    pilot.backend.collect(job.job_path)
    return True

def reopened(pilot,row):
    case=pilot.case_dir(row);receipt=case/'HISTORY_COMPLETION_REQUEUE.json'
    if not receipt.exists() or (case/'PARKED_C83_FINAL.json').exists():return False
    pointer=read(receipt)['park']
    if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C81 original park changed')
    return True

def requeue_current(pilot=None, *, scheduler_owned=False):
    """Schedule reopening through a durable request; safe while loop is running."""
    from .autopilot import Autopilot
    pilot=pilot or Autopilot()
    from .c70_l3 import protected
    values=[]
    for row in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs']:
        case=pilot.case_dir(row);park=case/'PARKED.json'
        if not park.exists() or 'INSUFFICIENT_WORK' not in read(park).get('reason','') or 'QUEUE_PARK' in read(park).get('reason',''):continue  # a queue park is reopened by release_recovery
        if protected(pilot.backend.root(row)) or (case/'PARKED_C83_FINAL.json').exists():continue
        receipt=case/'HISTORY_COMPLETION_REQUEUE.json'
        ensure_once(receipt,dict(design_id=row['design_id'],park=bind(park),rule=RULE,maximum_completion_jobs=1))
        values.append(dict(design_id=row['design_id'],receipt=bind(receipt)))
    return values
