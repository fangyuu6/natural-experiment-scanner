"""Immutable closure evidence; mutable source paths never authorize a closure."""
from pathlib import Path
from .storage import public_input, sha, ensure_once


def snapshot(source, root):
    source=public_input(source); digest=sha(source)
    # Copies are inert evidence, including when their source was executable code.
    dest=Path(root)/'closure_evidence'/digest/(source.name+'.txt')
    dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
        with dest.open('xb') as handle: handle.write(source.read_bytes())
    if sha(dest)!=digest: raise ValueError('Closure evidence copy changed')
    ensure_once(dest.parent/(dest.name+'.COPY.json'),dict(schema='l2_closure_evidence_copy_v1',
        source_name=source.name,path=dest.name,sha256=digest))
    return dict(path=str(dest.resolve()),sha256=digest)


def validate(evidence, repo, *, frozen_files=None, root=None):
    repo=Path(repo).resolve(); path=Path(evidence.get('path',''))
    # Normalize the trusted root (macOS /var aliases /private/var); reject
    # symlinks inside the evidence path below that root.
    candidate=path if path.is_absolute() else Path(root or repo).resolve()/path
    if candidate.is_symlink() or any(p.is_symlink() for p in candidate.parents):
        raise ValueError('Closure evidence cannot be a symlink')
    candidate=public_input(candidate)
    if (not candidate.is_relative_to(repo) or not candidate.is_file()
            or sha(candidate)!=evidence.get('sha256')):
        raise ValueError('Closure evidence changed or escapes project')
    relative=candidate.relative_to(repo)
    if 'scripts' in relative.parts or 'docs' in relative.parts or candidate.suffix in {'.py','.sh'}:
        raise ValueError('Closure evidence must cite immutable copies, not code or ruling docs')
    if frozen_files is not None:
        name=str(candidate.relative_to(Path(root).resolve()))
        entry=frozen_files.get(name,{})
        if entry.get('sha256')!=evidence['sha256'] or entry.get('role')=='code':
            raise ValueError('Closure evidence absent from immutable frozen inventory')
    elif not ({'evidence','closure_evidence'} & set(relative.parts)):
        raise ValueError('Closure evidence must cite immutable hashed evidence copies')
    elif 'closure_evidence' in relative.parts:
        import json
        receipt=candidate.parent/(candidate.name+'.COPY.json')
        try:value=json.loads(receipt.read_text())
        except (OSError,ValueError):raise ValueError('Closure evidence copy receipt missing') from None
        if (candidate.parent.name!=evidence['sha256'] or value.get('schema')!='l2_closure_evidence_copy_v1'
                or value.get('path')!=candidate.name or value.get('sha256')!=evidence['sha256']):
            raise ValueError('Closure evidence copy receipt changed')
    return candidate
