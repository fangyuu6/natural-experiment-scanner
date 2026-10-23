"""Explicit, portable paths for the run4 source-verification route."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Run4Paths:
    repo_root: Path = REPO_ROOT
    cards_dir: Path | None = None
    jobs_root: Path | None = None

    def __post_init__(self):
        root = Path(self.repo_root).resolve()
        object.__setattr__(self, 'repo_root', root)
        for name, suffix in (('cards_dir', 'data/l2_pipeline/cards/run4'),
                             ('jobs_root', 'data/l2_pipeline/jobs/run4')):
            supplied = getattr(self, name)
            target = root / (supplied if supplied is not None else suffix)
            object.__setattr__(self, name, target.resolve())

    def reference(self, value: str | Path) -> Path:
        """Archive references are absolute or relative to the selected repository."""
        return (self.repo_root / Path(value)).resolve()
