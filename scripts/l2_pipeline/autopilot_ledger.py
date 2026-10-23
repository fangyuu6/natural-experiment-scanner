"""Durable state transitions and lossless stopped-host ledger compaction."""
import hashlib
import json
import os
from pathlib import Path
from .storage import encoded, ensure_once, sha

NOTES = {'SHOULD_OPEN_C54', 'SCIENCE_MUST_OPEN_C54', 'OBSERVED_PARK',
         'OBSERVED_PRESCREEN_PARK', 'OBSERVED_GATE0_PARK', 'OBSERVED_FINISHED', 'IDLE', 'QUEUE_STARVED',
         'STARVED_BLOCKED'}

VOLATILE = {'time', 'running', 'started_at', 'pid'}


def lane(value):
    key = (value.get('design_id'), value.get('attempt_id'), value.get('action'), value.get('job'))
    return key + (value['decision'],) if value.get('decision') in NOTES else key


def state(value):
    return encoded({k:v for k,v in value.items() if k not in VOLATILE})


def states(path):
    result = {}
    if Path(path).exists():
        with Path(path).open() as stream:
            for line in stream:
                value = json.loads(line)
                if value.get('action') in {'resume','requeue'} or value.get('decision')=='MECHANICAL_REQUEUE':
                    result={k:v for k,v in result.items() if k[0]!=value.get('design_id')}
                result[lane(value)] = value
    return result


def compact(path, archive_dir):
    """Caller holds scheduler lock. Archive exact original before atomic replace.

    Preserve raw first occurrences and every per-design transition, including
    requeue followed by the same STOP when historical rows lack attempt IDs.
    Malformed/incomplete evidence refuses compaction without changing the active.
    """
    path=Path(path);archive_dir=Path(archive_dir)
    data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    previous={};kept=[]
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):raise ValueError('Incomplete ledger; recover before compaction')
        value=json.loads(line);key=lane(value);signature=state(value)
        if value.get('action') in {'resume','requeue'} or value.get('decision') in {'MECHANICAL_REQUEUE','ACCEPT'}:
            previous={k:v for k,v in previous.items() if k[0]!=value.get('design_id')}
        if value.get('design_id') is None or previous.get(key)!=signature:kept.append(line)
        previous[key]=signature
    archive=archive_dir/('AUTOPILOT_LEDGER_ORIGINAL_'+digest+'.jsonl')
    archive.parent.mkdir(parents=True,exist_ok=True)
    if archive.exists():
        if sha(archive)!=digest:raise ValueError('Ledger archive hash mismatch')
    else:
        with archive.open('xb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
    fd=os.open(archive.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    active=b''.join(kept)
    receipt=dict(schema='l2_c54_ledger_compaction_v1',archive=str(archive.resolve()),
        original_sha256=digest,original_bytes=len(data),original_records=len(data.splitlines()),
        active_sha256=hashlib.sha256(active).hexdigest(),active_bytes=len(active),active_records=len(kept),
        preserved='first occurrence and every lane transition; requeue/resume/accept reset design state; all global events retained',
        ignored_volatile_fields=sorted(VOLATILE))
    ensure_once(archive_dir/('COMPACTION_'+digest+'_'+receipt['active_sha256']+'.json'),receipt)
    tmp=path.with_name(path.name+'.compact-'+str(os.getpid()))
    with tmp.open('xb') as stream:
        stream.write(active);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return receipt
