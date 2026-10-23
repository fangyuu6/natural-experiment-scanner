"""Content-addressed code packages and isolated execution, with no live imports."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from .storage import encoded, local, sha

_ACTIVE = None


def active():
    return _ACTIVE


def create(root, inventory, repo=None):
    root = Path(root)
    repo = Path(repo or Path(__file__).resolve().parents[2]).resolve()
    # Invented dependency inventories may use a separate source tree in tests.
    if not all(Path(p).is_relative_to(repo) for p in inventory):
        import os
        pipeline = [Path(p) for p in inventory if p.endswith('/scripts/l2_pipeline/freeze.py')]
        repo = pipeline[0].parents[2] if pipeline else Path(os.path.commonpath(list(inventory)))
        if repo.is_file(): repo = repo.parent
    entries = {str(Path(p).relative_to(repo)): h for p, h in inventory.items()}
    digest = hashlib.sha256(encoded(entries)).hexdigest()
    base = 'code_snapshots/' + digest
    for source, h in inventory.items():
        original = Path(source)
        if active() is not None:
            original = Path(__file__).resolve().parents[2]/original.relative_to(Path(active()['code_snapshot']['source_repo']))
        data = original.read_bytes()
        if hashlib.sha256(data).hexdigest() != h:
            raise ValueError('Code changed while snapshotting: ' + source)
        for relative in (base+'/objects/'+h, base+'/tree/'+str(Path(source).relative_to(repo))):
            target = local(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != data: raise ValueError('Snapshot bytes changed: '+relative)
            else:
                with target.open('xb') as handle: handle.write(data)
    record = dict(schema='l2_code_snapshot_v1', digest=digest, base=base,
                  source_repo=str(repo), entries=entries)
    validate(root, record, inventory)
    from .storage import ensure_once
    ensure_once(root/('CODE_SNAPSHOT_'+digest+'.json'),dict(code_snapshot=record,code_inventory=inventory,snapshot_root=str(root.resolve()),execution_authorized=False))
    return record


def validate(root, record, inventory):
    if record.get('schema') != 'l2_code_snapshot_v1': raise ValueError('Invalid code snapshot schema')
    entries = record['entries']
    digest = hashlib.sha256(encoded(entries)).hexdigest()
    if record['digest'] != digest or record['base'] != 'code_snapshots/'+digest:
        raise ValueError('Code snapshot address mismatch')
    repo = Path(record['source_repo'])
    if {str(repo / p): h for p, h in entries.items()} != inventory:
        raise ValueError('Code snapshot inventory mismatch')
    base = record['base']
    expected = set()
    for p, h in entries.items():
        if len(h) != 64 or any(c not in '0123456789abcdef' for c in h):
            raise ValueError('Invalid snapshot hash')
        for relative in (base+'/objects/'+h, base+'/tree/'+p):
            target = local(Path(root), relative)
            expected.add(target)
            if sha(target) != h: raise ValueError('Tampered code snapshot: '+relative)
    actual = {p for p in local(Path(root), base).rglob('*') if p.is_file()}
    if actual != expected: raise ValueError('Unbound code snapshot file')
    return local(Path(root), base+'/tree')


def call(root, manifest, operation, payload):
    """The child imports only verified snapshot code and installed libraries.

    -I excludes cwd/PYTHONPATH; no live module objects or caller callbacks cross
    the boundary. A second snapshot validation runs inside the bound worker.
    """
    tree = validate(root, manifest['code_snapshot'], manifest['code_inventory'])
    bootstrap = """import sys,json
request=json.load(sys.stdin)
sys.path.insert(0,request['tree'])
from scripts.l2_pipeline import snapshots
snapshots._ACTIVE=request['manifest']
from scripts.l2_pipeline.snapshot_worker import perform
try:
    result=perform(request)
except Exception as exc:
    result={'_snapshot_error':type(exc).__name__,'message':str(exc)}
print(json.dumps(result))
"""
    request = dict(root=str(Path(root).resolve()), tree=str(tree), manifest=manifest,
                   operation=operation, payload=payload)
    proc = subprocess.run([sys.executable, '-I', '-B', *(['-O'] if sys.flags.optimize else []), '-c', bootstrap],
                          input=json.dumps(request), capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError('Snapshot operation refused: '+proc.stderr.strip())
    result = json.loads(proc.stdout)
    if isinstance(result, dict) and '_snapshot_error' in result:
        import builtins
        kind = result['_snapshot_error']
        allowed = {'ValueError', 'TypeError', 'RuntimeError', 'OSError', 'FileNotFoundError', 'FileExistsError', 'KeyError'}
        exception = getattr(builtins, kind) if kind in allowed else RuntimeError
        raise exception(result['message'])
    return result


def manifest_for(root, stage='final'):
    path = Path(root)/('FREEZE_'+stage.upper()+'.json')
    if not path.is_file(): return None
    value = json.loads(path.read_text())
    return value if 'code_snapshot' in value else None


def context_payload(ctx):
    return dict(card_path=str(ctx.card.path), card_payload=ctx.card.payload,
                workspace=str(ctx.workspace), repo=str(ctx.repo))
