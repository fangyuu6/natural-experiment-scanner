"""Exclusive durable writes and bounded paths; no overwrite/resume authority."""
from __future__ import annotations
import hashlib, json, os
from pathlib import Path
from typing import Any
from .contracts import Artifact

def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""): h.update(block)
    return h.hexdigest()

def local(root: Path, relative: str) -> Path:
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts or "\\" in relative: raise ValueError("Unsafe artifact path")
    target = root / p
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()): raise ValueError("Artifact escapes root")
    public_input(target)
    return target

def write_once(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded(value) + b"\n"); handle.flush(); os.fsync(handle.fileno())
    return path

def artifact(path: Path, role: str) -> Artifact:
    return Artifact(str(path), sha(path), role)

def output_root(path: Path, repo: Path) -> Path:
    path = path.resolve()
    allowed = [repo / "scripts/l2_pipeline", repo / "docs/plans/l2-pipeline", repo / "data/l2_pipeline", Path("/private/tmp"), Path("/tmp").resolve()]
    import tempfile
    allowed.append(Path(tempfile.gettempdir()).resolve())
    if not any(path.is_relative_to(p.resolve()) for p in allowed): raise ValueError("Output must stay in task-owned package/docs or scratchpad")
    return path


def public_input(path: Path) -> Path:
    resolved=Path(path).resolve()
    if any(part in {".codex",".aws",".wrangler"} or part.startswith(".env") for part in resolved.parts) or resolved.name in {"auth.json","credentials","credentials.json"}:
        raise ValueError("Credential/configuration files cannot be research inputs")
    return resolved

def ensure_once(path: Path, value: Any) -> Path:
    """Resume deterministic preparation only when the existing bytes match."""
    if path.exists():
        if path.read_bytes()!=encoded(value)+b"\n": raise ValueError("Existing preparation artifact changed: "+str(path))
        return path
    return write_once(path,value)
