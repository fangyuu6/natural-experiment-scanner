"""Explicit attempt configuration; executable location never determines data paths."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class IntakeConfig:
    root: Path
    calibration_seed: int
    audit_seed: int
    design_prefix: str
    schema_version: str = 'research_panel_intake_v1'
    expected_variables: int | None = None
    expected_emitted: int | None = None
    family_cap: int = 6

    @property
    def repo(self):
        return REPO

    @classmethod
    def from_existing(cls, root):
        """Recover declared seeds and identity from the locked metadata only."""
        root = Path(root).resolve()
        intake = json.loads((root/'INTAKE.json').read_text())
        emitted = [r for r in intake['rows'] if r['emit_eligible']]
        seeds = {(r['sample_plan']['calibration_seed'], r['sample_plan']['audit_seed'])
                 for r in emitted}
        if len(seeds) != 1:
            raise ValueError('Intake needs one declared calibration/audit seed pair')
        prefixes = {r['design_id'].removesuffix(r['policy_id'].replace(':', '_'))
                    for r in emitted}
        if len(prefixes) != 1 or not next(iter(prefixes)).endswith('_'):
            raise ValueError('Intake needs one declared design prefix')
        calibration, audit = next(iter(seeds))
        return cls(root, calibration, audit, next(iter(prefixes))[:-1],
                   intake['schema_version'], intake['summary']['catalog_variables'],
                   intake['summary']['emitted'])
