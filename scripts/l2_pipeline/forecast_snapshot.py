"""Relocation-safe originals: resolve only locked bytes inside the study root."""
import hashlib
import json
from pathlib import Path
from .storage import encoded, local, sha


def create(root, lock):
    entries = lock.get('original_files', {})
    digest = hashlib.sha256(encoded(entries)).hexdigest()
    base = 'forecast_snapshots/' + digest
    for relative, expected in entries.items():
        source = local(root, relative)
        if sha(source) != expected:
            raise ValueError('Original forecast lock changed: ' + relative)
        target = local(root, base + '/objects/' + expected)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target) != expected: raise ValueError('Forecast object changed')
        else:
            with target.open('xb') as handle: handle.write(source.read_bytes())
    record = dict(schema='l2_forecast_snapshot_v1', digest=digest, base=base, entries=entries)
    validate(root, record, lock)
    return record


def validate(root, record, lock):
    entries = lock.get('original_files', {})
    digest = hashlib.sha256(encoded(entries)).hexdigest()
    if (record.get('schema') != 'l2_forecast_snapshot_v1' or record.get('entries') != entries
            or record.get('digest') != digest or record.get('base') != 'forecast_snapshots/' + digest):
        raise ValueError('Forecast snapshot address/inventory mismatch')
    expected = set()
    for relative, h in entries.items():
        if len(h) != 64 or any(c not in '0123456789abcdef' for c in h):
            raise ValueError('Invalid forecast object hash')
        if sha(local(root, relative)) != h: raise ValueError('Locked forecast bytes changed')
        target = local(root, record['base'] + '/objects/' + h)
        if sha(target) != h: raise ValueError('Tampered forecast snapshot')
        expected.add(target)
    actual = {p for p in local(root, record['base']).rglob('*') if p.is_file()}
    if actual != expected: raise ValueError('Unbound forecast snapshot file')


def locked_relative(lock, persona, collection):
    prefix = 'predictions/' + persona + '/'
    candidates = [p for p, h in lock.get('original_files', {}).items()
                  if p.startswith(prefix) and h == collection.get('original_sha256')
                  and Path(p).name == Path(collection['original_path']).name]
    if len(candidates) != 1:
        raise ValueError('Original forecast lock identity ambiguous/missing')
    return candidates[0]


def original(root, lock, persona, collection, record=None):
    """The recorded absolute origin is provenance, never a filesystem authority."""
    if record is None:
        path = Path(collection['original_path']).resolve()
        if not path.is_relative_to(Path(root).resolve()):
            raise ValueError('Original forecast escapes execution root')
        relative = str(path.relative_to(Path(root).resolve()))
    else:
        validate(root, record, lock)
        relative = locked_relative(lock,persona,collection)
        path = local(root, record['base'] + '/objects/' + lock['original_files'][relative])
    if relative not in lock.get('original_files', {}) or sha(path) != lock['original_files'][relative]:
        raise ValueError('Original forecast is not locked')
    if collection.get('original_sha256') != sha(path):
        raise ValueError('Original forecast collection hash mismatch')
    return json.loads(path.read_text())
