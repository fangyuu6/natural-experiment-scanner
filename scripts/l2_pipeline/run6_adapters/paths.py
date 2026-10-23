"""Separate executable code from retained run6 data and source evidence."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / 'data/l2_pipeline/runs/run6'


def require(condition, message='Run6 validation failed'):
    """Validation must remain active under python -O."""
    if not condition:
        raise ValueError(str(message))


def code_path(path: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    """Reject data-resident executables, including symlinks into data/."""
    root = Path(repo_root).resolve()
    target = (root / Path(path)).resolve()
    if target.suffix.lower() != '.py':
        raise ValueError(f'Pipeline code must be Python: {target}')
    if not target.is_relative_to(root):
        raise ValueError(f'Pipeline code must live inside the repository: {target}')
    if target.relative_to(root).parts[0].casefold() == 'data':
        raise ValueError(f'Pipeline code cannot live under data/: {target}')
    if not target.is_file():
        raise ValueError(f'Pipeline code must exist: {target}')
    return target


MOVED_ADAPTERS = {
    f'data/l2_pipeline/runs/run6/{old}':
    f'scripts/l2_pipeline/run6_adapters/{old}'
    for old in ('adapter.py', 'verify.py', 'intake_selection.py',
                'cdc/fetch_cdc.py', 'cdc/parse_cdc.py', 'cms/adapter.py',
                'pdaps/build_pdaps.py', 'social/build_social.py')
}
MOVED_ADAPTERS.update({
    'data/l2_pipeline/cards/run2/_meta/literature_adoption_intake.py':
    'scripts/l2_pipeline/literature_adoption_intake.py',
    **{f'data/l2_pipeline/cards/run4/_meta/{name}.py':
       f'scripts/l2_pipeline/run4_adapters/{name}.py'
       for name in ('build_additional', 'build_l1', 'build_literature',
                    'finalize_metadata', 'published_study_intake', 'verify_dry_run')},
})


def relocated_code_path(path: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve registered historical references without executing archived code."""
    root = Path(repo_root).resolve()
    candidate = root / Path(path)
    try:
        relative = candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        relative = str(path)
    return code_path(MOVED_ADAPTERS.get(relative, candidate), root)
