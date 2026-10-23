from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

class Disposition(str, Enum):
    READY = "ready"
    PARKED = "parked"
    STOPPED = "stopped"
    FAILED = "failed_no_retry"

@dataclass(frozen=True)
class DesignCard:
    design_id: str
    version: str
    policy_family: str
    mechanism: str
    track: str
    unit: str
    outcome_family: str
    payload: Mapping[str, Any]
    path: Path
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: Path) -> DesignCard:
        return cls(**{k: value[k] for k in ("design_id", "version", "policy_family", "mechanism", "track", "unit", "outcome_family")}, payload=value, path=path)

@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str
    role: str

@dataclass(frozen=True)
class StageResult:
    stage: str
    design_id: str
    disposition: Disposition
    reason: str
    reopen_condition: str | None = None
    artifacts: tuple[Artifact, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class StageContext:
    card: DesignCard
    workspace: Path
    repo: Path
    @property
    def design_dir(self) -> Path:
        return self.workspace / self.card.design_id / self.card.version
    def read(self, name: str) -> Any:
        import json
        return json.loads((self.design_dir / name).read_text())

class Stage(Protocol):
    def run(self, context: StageContext) -> StageResult: ...
