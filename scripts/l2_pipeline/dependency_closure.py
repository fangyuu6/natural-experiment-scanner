"""Static repository dependency binding; never import or execute study code.

The execution entry point and every runtime pipeline module are roots. All
pipeline Python files remain bound as in the original guard, including tests,
but only runtime roots are traversed. Repo imports are followed transitively,
including lazy imports, package initializers and literal dynamic imports.
Dynamic imports with a fixed package prefix bind the entire package. Installed
libraries are covered separately by the frozen environment.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from .storage import sha

POLICY = "repo_dependency_closure_v1"


def dependency_inventory(repo: Path, entry_points: Iterable[Path], *,
                         pipeline_root: Path | None = None) -> dict[str, str]:
    repo = Path(repo).resolve()
    pending: list[Path] = []
    bound: set[Path] = set()
    visited: set[Path] = set()

    def checked(path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(repo):
            raise ValueError("Dependency escapes repository: " + str(path))
        if not resolved.is_file():
            raise ValueError("Missing dependency: " + str(path))
        return resolved

    def add(path: Path, *, traverse: bool = True) -> None:
        path = checked(path)
        bound.add(path)
        if traverse and path.suffix == ".py" and path not in visited:
            pending.append(path)

    def module(name: str, *, required: bool = False) -> None:
        if not name or any(not part.isidentifier() for part in name.split(".")):
            raise ValueError("Invalid dependency module: " + name)
        parts = name.split(".")
        top = repo / parts[0]
        # Installed/stdlib imports are environment dependencies, not repo files.
        if not top.is_dir() and not top.with_suffix(".py").is_file():
            if required:
                raise ValueError("Missing repository import: " + name)
            return
        for length in range(1, len(parts) + 1):
            init = repo.joinpath(*parts[:length], "__init__.py")
            if init.is_file():
                add(init)
        base = repo.joinpath(*parts)
        if base.with_suffix(".py").is_file():
            add(base.with_suffix(".py"))
        elif (base / "__init__.py").is_file():
            add(base / "__init__.py")
        elif not base.is_dir() and required:
            raise ValueError("Missing repository import: " + name)

    if pipeline_root is not None:
        pipeline_root = Path(pipeline_root).resolve()
        if not pipeline_root.is_relative_to(repo):
            raise ValueError("Pipeline root escapes repository")
        for source in sorted(pipeline_root.rglob("*.py")):
            if "__pycache__" not in source.parts:
                runtime = "tests" not in source.relative_to(pipeline_root).parts
                add(source, traverse=runtime)
    for entry in entry_points:
        add(Path(entry) if Path(entry).is_absolute() else repo / entry)

    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        try:
            tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        except SyntaxError as exc:
            raise ValueError("Cannot inspect dependency: " + str(source)) from exc
        relative = source.relative_to(repo)
        package = list(relative.parent.parts)
        dynamic_names = {"__import__"}
        for statement in ast.walk(tree):
            if isinstance(statement, ast.ImportFrom) and statement.module == "importlib":
                dynamic_names.update(alias.asname or alias.name for alias in statement.names
                                     if alias.name == "import_module")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module(alias.name, required=(repo / alias.name.split(".")[0]).is_dir())
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.level > len(package):
                        raise ValueError("Relative import escapes repository: " + str(source))
                    prefix = package[:len(package) - node.level + 1]
                    name = ".".join(prefix + ([node.module] if node.module else []))
                else:
                    name = node.module or ""
                module(name, required=bool(node.level) or (repo / name.split(".")[0]).is_dir())
                # 'from package import child' may load a module or an attribute.
                for alias in node.names:
                    if alias.name != "*":
                        module(name + "." + alias.name)
            elif isinstance(node, ast.Call):
                func = node.func
                loader_name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
                if loader_name in {"spec_from_file_location", "SourceFileLoader", "run_path"}:
                    raise ValueError("File-based code loader needs an explicit repository binding: " + str(source))
                dynamic = ((isinstance(func, ast.Name) and func.id in dynamic_names)
                           or (isinstance(func, ast.Attribute) and func.attr == "import_module"))
                if dynamic and node.args:
                    argument = node.args[0]
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        name = argument.value
                        if name.startswith("."):
                            raise ValueError("Relative dynamic import needs an explicit binding: " + str(source))
                        module(name, required=(repo / name.split(".")[0]).is_dir())
                    elif (isinstance(argument, ast.BinOp) and isinstance(argument.op, ast.Add)
                          and isinstance(argument.left, ast.Constant)
                          and isinstance(argument.left.value, str)
                          and argument.left.value.endswith(".")):
                        target = repo.joinpath(*argument.left.value.rstrip(".").split("."))
                        if not target.is_dir():
                            raise ValueError("Dynamic import package is unbound: " + str(source))
                        for child in sorted(target.rglob("*.py")):
                            add(child)
                    else:
                        raise ValueError("Dynamic import needs a static repository binding: " + str(source))
                # Bind import-time schema/resources declared alongside a module.
                if (isinstance(func, ast.Attribute) and func.attr == "with_name"
                    and isinstance(func.value, ast.Call) and len(func.value.args) == 1
                    and isinstance(func.value.func, ast.Name) and func.value.func.id == "Path"
                    and isinstance(func.value.args[0], ast.Name) and func.value.args[0].id == "__file__"
                    and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                    add(source.with_name(node.args[0].value), traverse=False)

    return {str(path): sha(path) for path in sorted(bound)}
