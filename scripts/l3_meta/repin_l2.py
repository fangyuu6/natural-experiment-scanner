#!/usr/bin/env python3
"""L3 meta: re-pin after an L2 code window (PREREG_AMEND_20261016_REPIN rule in PREREG_AMEND_20261016_SWEEP_EXT_WIRE_V1.md section 2).

Compares the L2 modules listed in a hash group (the main record plus its supplements, rebound rule applied) with the files on disk and, for the
`sweep` group, with the modules that the sweep code actually loads (sweep_core.l2_files()).  Prints the drift and the text of the next numbered
supplement; with --write the supplement is written once as <record stem>_S<n>.txt.  It never edits an existing record and writes nothing else.

    .venv_did/bin/python -I -B scripts/l3_meta/repin_l2.py --group sweep --reason "L2 YIELD_FIXES window of 2026-10-15/16 closed ..." [--write]
    .venv_did/bin/python -I -B scripts/l3_meta/repin_l2.py --group sweep_ext --reason "..." [--write]

Writing the supplement is a registered act (a dated record): run it only when the L2 window is closed, and run the reproduction gate afterwards
(refresh_all.py --force; section 6 of L3_STATE_20261016.md).  Standard library plus sweep_core for the `sweep` group (read-only on L2).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import l3_pins  # noqa: E402

RECORDS = {
    "sweep": "docs/plans/l3-meta/PREREG_AMEND_20261015_SWEEP_HASH.txt",
    "sweep_ext": "docs/plans/l3-meta/PREREG_AMEND_20261016_SWEEP_EXT_HASH.txt",
}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def lazy_closure(loaded):
    """Adds to `loaded` ({repo-relative path: sha}) every scripts/l2_pipeline module that a loaded module can import, including imports written inside
    functions (loaded lazily while a sweep task runs, e.g. rules.py), followed transitively.  Static (ast) and read-only; fail-closed: a module that
    is only importable, not yet imported, is listed too, so the record can only be stricter than what is loaded."""
    import ast
    pkg = "scripts/l2_pipeline"
    out = dict(loaded)
    todo = [(p, True) for p in out]   # (module, follow function-level imports): only already-loaded modules are followed into their function bodies;
    while todo:                       # a newly added module runs only its top-level imports when it is loaded
        path, deep = todo.pop()
        f = ROOT / path
        if not f.is_file():
            continue
        parts = path[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
            here = parts
        else:
            here = parts[:-1]
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        cands = []
        for node in (ast.walk(tree) if deep else tree.body):
            if isinstance(node, ast.Import):
                for a in node.names:
                    cands.append(a.name.split("."))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = here[:len(here) - (node.level - 1)] if node.level > 1 else list(here)
                else:
                    base = []
                mod = base + (node.module.split(".") if node.module else [])
                cands.append(mod)
                for a in node.names:
                    cands.append(mod + [a.name])
        for c in cands:
            if "/".join(c[:2]) != pkg:
                continue
            for k in range(len(c), 1, -1):
                rel = "/".join(c[:k])
                for q in (rel + ".py", rel + "/__init__.py"):
                    if (ROOT / q).is_file() and q not in out:
                        out[q] = sha256_file(ROOT / q)
                        todo.append((q, False))
    return dict(sorted(out.items()))


def refresh_entry_seed(entry="scripts/l3_meta/build_ex1.py"):
    """T284: the L2 modules that the refresh process loads through the L3 entry script (build_ex1.py and the scripts/l3_meta modules it imports,
    followed transitively, function-level imports included) and that S.load_l2() does not import.  Static (ast), read-only, fail-closed like
    lazy_closure: returns {repo-relative scripts/l2_pipeline path: sha} for every module those files import from the L2 package (the seed that
    lazy_closure then follows).  Without it forecast_slots.py (imported by build_ex1.py and reader.py) was missing from the hash record (2026-10-23)."""
    import ast
    pkg = "scripts/l2_pipeline"
    l3 = "scripts/l3_meta"
    out, seen, todo = {}, set(), [entry]
    while todo:
        path = todo.pop()
        if path in seen or not (ROOT / path).is_file():
            continue
        seen.add(path)
        try:
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        cands = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                cands += [a.name.split(".") for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                mod = node.module.split(".")
                cands.append(mod)
                cands += [mod + [a.name] for a in node.names]
        for c in cands:
            if "/".join(c[:2]) == pkg:
                for k in range(len(c), 1, -1):
                    rel = "/".join(c[:k])
                    for q in (rel + ".py", rel + "/__init__.py"):
                        if (ROOT / q).is_file():
                            out[q] = sha256_file(ROOT / q)
            elif len(c) == 1 and (ROOT / l3 / (c[0] + ".py")).is_file():   # local L3 module (sys.path has scripts/l3_meta)
                todo.append(f"{l3}/{c[0]}.py")
            elif "/".join(c[:2]) == l3 and len(c) > 2 and (ROOT / l3 / (c[2] + ".py")).is_file():
                todo.append(f"{l3}/{c[2]}.py")
    return dict(sorted(out.items()))


def analyse(group, loaded=None):
    """Returns dict(records, effective, changed {path: (recorded, disk)}, missing [paths], unlisted {path: disk sha}, next_number)."""
    main = ROOT / RECORDS[group]
    recs = l3_pins.record_paths(main)
    eff, owner, _ = l3_pins.effective_l2([(p.name, p.read_text(encoding="utf-8")) for p in recs])
    changed, missing = {}, []
    for path, want in eff.items():
        p = ROOT / path
        if not p.is_file():
            missing.append(path)
        elif sha256_file(p) != want:
            changed[path] = (want, sha256_file(p))
    unlisted = {p: h for p, h in (loaded or {}).items() if p not in eff}
    nums = [int(m.group(1)) for r in recs if (m := re.search(r"_S(\d+)\.txt$", r.name))]
    return dict(records=[r.name for r in recs], effective=eff, changed=changed, missing=missing, unlisted=unlisted, next_number=(max(nums) + 1) if nums else 1)


def supplement_text(group, info, reason, utc):
    main = ROOT / RECORDS[group]
    lines = main.read_text(encoding="utf-8").splitlines()
    amend_file = next((lines[i].split(None, 1)[1] for i in range(len(lines) - 1) if lines[i].startswith("file:")), "?")
    amend_sha = next((lines[i + 1].split(None, 1)[1] for i in range(len(lines) - 1) if lines[i].startswith("file:")), "?")
    out = [f"Supplement {info['next_number']} to {main.name} (re-pin of L2 modules after an L2 code window; rule id EX-1 continues)",
           f"reason: {reason}",
           "No ATT, placebo ATT, D-statistic or other number of the sweep family was computed between the previous record and this supplement; the "
           "reproduction gate (refresh_all.py --force, then the comparison with the previous refresh) is run AFTER this supplement is hashed.",
           f"file: {amend_file.strip()}", f"sha256: {amend_sha.strip()}", "(unchanged)",
           f"supplement_utc: {utc}"]
    if info["changed"]:
        out.append("L2 modules rebound (each line replaces the earlier line of the same path; the earlier hash is in the comment of the line below it):")
        for p in sorted(info["changed"]):
            out.append(f"  {p} sha256 {info['changed'][p][1]}")
        out.append("previous hashes (comment, not parsed as pins):")
        for p in sorted(info["changed"]):
            out.append(f"  # {p} was {info['changed'][p][0]}")
    if info["unlisted"]:
        out.append("L2 modules added (loaded by the sweep code and not listed before):")
        for p in sorted(info["unlisted"]):
            out.append(f"  {p} sha256 {info['unlisted'][p]}")
    out.append("note: further changes are recorded in the same way (a numbered supplement) before the next number is computed.")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--group", choices=sorted(RECORDS) + ["all"], required=True, help="`all` = every group in turn (the one-command re-pin)")
    ap.add_argument("--reason", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only-unlisted", action="append", default=None, metavar="PATH",
                    help="add only these loaded-and-unlisted modules (repeatable); the static lazy closure over-approximates, the gate stays fail-closed for the rest")
    args = ap.parse_args(argv)
    if args.group == "all":
        rc = 0
        for g in sorted(RECORDS, key=lambda x: x != "sweep"):
            print(f"=== {g}")
            rc = max(rc, main(["--group", g] + sum([["--only-unlisted", x] for x in (args.only_unlisted or [])], []) + (["--reason", args.reason] if args.reason else []) + (["--write"] if args.write else [])))
        return rc
    loaded = None
    if args.group == "sweep":
        import sweep_core as S
        S.load_l2()
        loaded = lazy_closure({**refresh_entry_seed(), **S.l2_files()})
    info = analyse(args.group, loaded)
    if args.only_unlisted is not None:
        info["unlisted"] = {p: h for p, h in info["unlisted"].items() if p in set(args.only_unlisted)}
    print(f"records: {info['records']}")
    print(f"listed L2 modules (effective): {len(info['effective'])}; changed {len(info['changed'])}; missing {len(info['missing'])}; loaded and unlisted {len(info['unlisted'])}")
    for p, (a, b) in sorted(info["changed"].items()):
        print(f"  CHANGED  {p}  {a[:12]} -> {b[:12]}")
    for p in info["missing"]:
        print(f"  MISSING  {p}")
    for p, h in sorted(info["unlisted"].items()):
        print(f"  UNLISTED {p}  {h[:12]}")
    if info["missing"]:
        print("a listed module is missing: stop, a missing pinned file is not a re-pin")
        return 2
    if not info["changed"] and not info["unlisted"]:
        print("nothing to re-pin")
        return 0
    utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = supplement_text(args.group, info, args.reason or "REASON NOT GIVEN", utc)
    target = ROOT / RECORDS[args.group]
    target = target.with_name(f"{target.stem}_S{info['next_number']}.txt")
    if args.write:
        if not args.reason:
            print("--reason is required with --write")
            return 2
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {target.relative_to(ROOT)} sha256 {sha256_file(target)}")
    else:
        print(f"--- draft of {target.name} (not written) ---")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
