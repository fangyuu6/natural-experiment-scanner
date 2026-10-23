"""Programmatic agent plans. Execution is an explicit future integration hook.
No subprocess runs here: this build task authorizes no new paid LLM/API calls.
Separate session roots expose only answer-free, byte-locked inputs.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from ..storage import sha, write_once

@dataclass(frozen=True)
class AgentInvocation:
    role: str
    working_dir: str
    argv: tuple[str, ...]
    prompt: str
    output: str
    template_sha256: str

def plan(role: str, directory: Path, *, codex: str = "codex") -> AgentInvocation:
    kind = "coder" if role in {"A", "B"} else "reviewer" if role == "reviewer" else "prediction"
    template = Path(__file__).parent.parent / "templates" / (kind + ".txt")
    # -C points at an independent bundle, never the repository or another answer.
    prompt=template.read_text().replace("{{ROLE}}", role)
    prompt+=("\nHost plan: register a current stage job before dispatch through "
             "python3 -B -m scripts.l2_pipeline.hub dispatch <stage>. "
             "The hub's --max-concurrency accepts 1..4 (default 4). "
             "Workers never dispatch; this archived invocation is not a wave launch command.\n")
    return AgentInvocation(role, str(directory), (codex, "exec", "--ephemeral", "-C", str(directory),
           "-s", "workspace-write", "--color", "never", "-o", str(directory / "submission.json"), "-"),
           prompt, str(directory / "submission.json"), sha(template))

def lock_submissions(paths: Sequence[Path], output: Path) -> Path:
    if len(paths) != len(set(p.resolve() for p in paths)): raise ValueError("Submissions must be independent paths")
    return write_once(output, {"files":{str(p):sha(p) for p in paths}})
