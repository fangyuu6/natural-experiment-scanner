#!/usr/bin/env python3
"""L3 meta: how a pinned L2 module is re-pinned after an L2 code window (rule id EX-1; PREREG_AMEND_20261016_REPIN_V1.md).

A hash record lists the L2 modules that a registered run loads, with their sha256.  When L2 changes one of them (a code window), the old line can
never be edited (records are write-once).  The registered way to rebind is a numbered supplement of the record (..._S2.txt, ..._S3.txt, ...) with a
section whose heading starts with `L2 modules rebound`; each line of that section

    <path under scripts/l2_pipeline or scripts/l2_batch1> sha256 <64 hex>

REPLACES the earlier line of the same path in the earlier records of the same group (and adds the path when no earlier record lists it).
Nothing else can be rebound: a rebound line for any other path, or a clash between two records outside such a section, is an error.

This file is standard library only.  It is used by
  * refresh_all.check_group   (the convention check before anything is computed),
  * refresh_all.Binding       (the effective hash-record parser of build_sweep, in the parent process and in the spawned workers),
  * repin_l2.py              (writes the supplement text),
  * the sweep-extension builder (build_sweep_ext.py).
It changes no pinned file: build_sweep.py, sweep_core.py and the other builders are used as they are.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

HEX64 = r"[0-9a-f]{64}"
REBOUND_HEADING = "l2 modules rebound"
L2_PREFIXES = ("scripts/l2_pipeline/", "scripts/l2_batch1/")


def is_l3_code(path):
    """L3 code files: a later record's line replaces an earlier line of the same path (no heading needed; the supplement is the dated act)."""
    return str(path).startswith("scripts/l3_meta/") and str(path).endswith(".py")


def rebound_lines(text):
    """{path: sha} of the lines under every heading that starts with `L2 modules rebound` (case-insensitive), in file order."""
    out = OrderedDict()
    section = ""
    for line in text.splitlines():
        if line and not line[0].isspace():
            section = line.strip().rstrip(":").lower() if line.rstrip().endswith(":") else ""
            continue
        if not section.startswith(REBOUND_HEADING):
            continue
        m = re.match(rf"^\s+(\S+)\s+sha256\s+({HEX64})", line)
        if m:
            path, sha = m.group(1), m.group(2)
            if not path.startswith(L2_PREFIXES) or not path.endswith(".py"):
                raise RuntimeError(f"rebound line for {path}: only L2 modules can be rebound")
            out[path] = sha
    return out


def _natural(p):
    m = re.search(r"_([SB])(\d+)\.txt$", p.name)
    return (m.group(1), int(m.group(2))) if m else (p.name, 0)


def record_paths(main, glob_suffix="_[SB]*.txt"):
    """The main record and its numbered supplements / later records in the same folder, in the order in which they apply (B1, S1, S2, ..., S10)."""
    main = Path(main)
    return [main] + sorted(main.parent.glob(main.stem + glob_suffix), key=_natural)


def effective_l2(texts):
    """texts: [(name, text)] in record order.  Returns (effective {path: sha} of the L2 lines, {path: name of the record whose line counts},
    {path: [names of the records whose line was replaced]}).  A plain line that clashes with an earlier line is an error; a rebound line replaces."""
    eff, owner, over = OrderedDict(), {}, {}
    for name, text in texts:
        rb = rebound_lines(text)
        section = ""
        for line in text.splitlines():
            if line and not line[0].isspace():
                section = line.strip().rstrip(":").lower() if line.rstrip().endswith(":") else ""
                continue
            if not section.startswith("l2 modules") or section.startswith(REBOUND_HEADING):
                continue
            m = re.match(rf"^\s+(\S+)\s+sha256\s+({HEX64})", line)
            if not m:
                continue
            p, h = m.group(1), m.group(2)
            if p in eff and eff[p] != h:
                raise RuntimeError(f"{name} contradicts {owner[p]} for {p} (a changed module needs a rebound line)")
            eff[p], owner[p] = h, name
        for p, h in rb.items():
            if p in eff and eff[p] != h:
                over.setdefault(p, []).append(owner[p])
            eff[p], owner[p] = h, name
    return eff, owner, over


def merged_record(parse_one, paths):
    """build_sweep.parse_hash_record with the rebound rule.  parse_one(path) -> {'files', 'listed', 't_amend_utc'} (build_sweep._parse_one).
    Same result as the original merge when no record has a rebound section."""
    paths = [Path(p) for p in paths]
    rec = parse_one(paths[0])
    rec = {"files": dict(rec["files"]), "listed": dict(rec["listed"]), "t_amend_utc": rec["t_amend_utc"]}
    for extra in paths[1:]:
        e = parse_one(extra)
        rb = rebound_lines(extra.read_text(encoding="utf-8"))
        for k, v in e["listed"].items():
            if k in rb:
                rec["listed"][k] = rb[k]
            elif k in rec["listed"] and rec["listed"][k] != v and not is_l3_code(k):
                raise RuntimeError(f"{extra.name} contradicts the record for {k}")
            else:
                rec["listed"][k] = v
        for k, v in e["files"].items():
            rec["files"][k] = v
        rec.setdefault("supplements", []).append(extra.name)
    return rec


def install(build_sweep_module):
    """Replace build_sweep.parse_hash_record by the rebound-aware merge (in this process only).  Returns an undo function."""
    B = build_sweep_module
    if not hasattr(B, "_parse_one"):            # a stand-in module without the record parser (tests): nothing to replace
        return lambda: None
    orig = B.parse_hash_record

    def parse(path=None):
        path = Path(path) if path is not None else Path(B.HASH_REC)
        return merged_record(B._parse_one, record_paths(path))

    parse.__wrapped__ = orig
    B.parse_hash_record = parse

    def undo():
        B.parse_hash_record = orig

    return undo
