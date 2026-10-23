"""Isolated code copies for parallel development; never merges or launches jobs."""
import argparse,json,shutil
from pathlib import Path
from ..storage import sha,ensure_once
REPO=Path(__file__).resolve().parents[3]


def inventory(root):
    root=Path(root).resolve()
    return {str(p.relative_to(root)):sha(p) for p in (root/'scripts').rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def prepare(name,repo=REPO):
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name):raise ValueError('Safe task name required')
    root=Path(repo)/'data/l2_pipeline/devcopy'/name
    if root.exists():raise FileExistsError('Development copy already exists')
    from ..freeze import code_inventory
    for filename in code_inventory():
        source=Path(filename);target=root/source.relative_to(repo)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    ensure_once(root/'COPY_MANIFEST.json',dict(schema='l2_devcopy_v1',production_imports_forbidden=True,files=inventory(root)))
    return root


def seal_copy(root):
    root=Path(root).resolve()
    if not root.is_relative_to(REPO/'data/l2_pipeline/devcopy'):raise ValueError('Not a development copy')
    from ..autopilot import atomic
    atomic(root/'COPY_MANIFEST.json',dict(schema='l2_devcopy_v1',production_imports_forbidden=True,files=inventory(root)))


def validated_files(repo):
    root=Path(repo).resolve();allowed=set()
    for manifest in (root/'data/l2_pipeline/devcopy').glob('*/COPY_MANIFEST.json'):
        value=json.loads(manifest.read_text());base=manifest.parent
        if value.get('schema')!='l2_devcopy_v1' or value.get('production_imports_forbidden') is not True:continue
        for relative,digest in value.get('files',{}).items():
            p=base/relative
            if (relative.startswith('scripts/') and '..' not in Path(relative).parts and not p.is_symlink()
                and p.resolve().is_relative_to(base.resolve()) and p.is_file() and sha(p)==digest):allowed.add(p.resolve())
    return allowed

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('name');a=p.parse_args();print(prepare(a.name))
