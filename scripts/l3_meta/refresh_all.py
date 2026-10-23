#!/usr/bin/env python3
"""L3 meta: one-command refresh of every L3 result when the live L2 set grows (rule id EX-1, section 1: a new version is built when the live
set, the code or the rule changes; no change, no new version).

Runs, in dependency order, each into a NEW folder data/l3_meta/refresh/<YYYYMMDD>_<run id>/<step>/ (an existing folder is never reused):

    live     the live L2 set                                   build_live_set.py   (read-only on L2)
    ex1      EX-1: Q1, Q3, Q5, N10                              build_ex1.py
    n1       N1                                                 build_n1.py
    sweep    outcome sweep: series, series_count, gate (the reproduction gate), sweep, d3runs, manifest      build_sweep.py
    d1       D1                                                 build_d1.py
    d3_v1    D3-V1                                              build_d3.py
    d2       D2                                                 build_d2.py
    d3_v2    D3-V2                                              build_d3_v2.py
    cal      CAL: Q1 and D1 with the D2 negative-control reference      build_cal.py

The builders are called exactly as they are (their own main functions: no copy, no edit).  Registered conventions are kept in two layers.
(1) Before anything is computed this file checks every hash record of the selected builders against the files on disk: the amendment texts,
the frozen files, the entries D1-D3, the pinned code, the pinned L2 modules and the frozen coding inputs.  One mismatch refuses the whole
refresh (exit 3, nothing computed).  (2) The builders then run their own guards.

Rebinding.  The builders were written for one snapshot of the live set and, for sweep, D1, D2, D3-V2 and CAL, pin that snapshot's sha256 in the
hash record.  The refresh leaves data/l3_meta/l2_set_live.json and every earlier version folder untouched and points the builders at the run's
own live set: module constants and default arguments that hold the canonical live path are replaced in memory, and the hash-record parser of
build_sweep drops the lines of a record that name a snapshot (live set, earlier result folders, L2 data) and adds one line for the run's live
set.  Every other line (amendments, frozen files, pinned code, pinned L2 modules, frozen coding inputs) is still verified by the builder itself.
N1 and D3-V1 use their own flag --live-set-changed-ok.  After each step the result is checked against the run's live set (same sha256, same
roster) and against the run's upstream files.

Outputs: the step folders, logs/, RUN_LOG.jsonl, REFRESH_MANIFEST.json (inputs' hashes, n per question, headline numbers, changes against the
previous refresh, or against the existing versions on the first run) and docs/plans/l3-meta/REFRESH_<date>.md.  The run reads L2 files only
(through the builders), writes nothing under scripts/l2_pipeline or data/l2_pipeline, uses no network and deletes nothing.

To add a builder: a Step in real_steps() (its main function is called with --out-dir), a ConvGroup in REAL_GROUPS when it has a hash record, a headline
spec, and its module name in BUILDER_MODULES (the rebinding then finds the live-set path it holds without any other change).

Run:   .venv_did/bin/python -I -B scripts/l3_meta/refresh_all.py [--dry-run] [--force] [--steps ex1,n1,...] [--workers N] [--resume RUN_DIR]
Exit:  0 done or nothing to do; 1 a step failed; 2 usage; 3 refused (a convention does not match its record).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib
import json
import math
import os
import re
import sys
import time
import traceback
import types
from collections import OrderedDict
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import l3_pins  # noqa: E402  (the rebound rule for pinned L2 modules; standard library only)

SCHEMA = "l3-refresh-manifest-v1"
REFRESH_REL = "data/l3_meta/refresh"
DIGEST_REL = "docs/plans/l3-meta"
CANON_LIVE_REL = "data/l3_meta/l2_set_live.json"
DOCS = "docs/plans/l3-meta/"
ENV_SPEC = "L3_REFRESH_BINDING"
ORIG_INIT_ATTR = "_l3_refresh_orig_init"
HEX64 = r"[0-9a-f]{64}"
REL_TOL = 1e-9
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
REAL = object()          # sentinel: use the real implementation

# A hash-record line that names one of these is a snapshot (it changes when L2 grows or when an earlier version is replaced), not a convention.
SNAPSHOT_PATTERNS = (
    r"^data/l3_meta/l2_set_live(_\d{8}T\d{6}Z(_\d+)?)?\.json$",
    r"^data/l3_meta/(ex1_v3|n1_v1|n1_v2|d1_v1|d2_v1|d3_v1|d3_v2|cal_v1|sweep_v1|q2_v1)/",
    r"^data/l3_meta/refresh[^/]*/",
    r"^data/l2_pipeline/",
)


def is_snapshot(path, patterns=SNAPSHOT_PATTERNS):
    return any(re.search(p, str(path)) for p in patterns)


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel_to(root, p):
    p, root = Path(p), Path(root)
    try:
        return str(p.relative_to(root))
    except ValueError:
        try:
            return str(p.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(p)


def sha256_file(p, chunk=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def clean(obj):
    """JSON-safe copy: NaN and inf become None, numpy scalars and arrays become python, tuples and sets become lists."""
    if isinstance(obj, (str, bytes, bool)) or obj is None:
        return obj if not isinstance(obj, bytes) else obj.decode("utf-8", "replace")
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, int):
        return obj
    if isinstance(obj, dict):
        return OrderedDict((str(k), clean(v)) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(clean(v) for v in obj)
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "tolist"):
        return clean(obj.tolist())
    if hasattr(obj, "item"):
        return clean(obj.item())
    return str(obj)


def dumps(obj):
    return json.dumps(clean(obj), indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def canon(obj):
    return json.dumps(clean(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def write_new(path, text):
    """Write a text file that must not exist yet."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)


def dig(obj, path, default=None):
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
    return cur


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def same_value(a, b, tol=REL_TOL):
    """Equality with a relative tolerance for numbers; lists and dicts element by element; a boolean equals only a boolean."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_number(a) and is_number(b):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same_value(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k], tol) for k in a)
    return a == b


def fmt(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    if isinstance(x, (list, tuple)):
        return "[" + ", ".join(fmt(v, nd) for v in x) + "]"
    return str(x)


def pct(x, nd=1):
    return "-" if x is None else f"{100 * x:.{nd}f}"


# ---------------------------------------------------------------------------------------------
# hash records (the registered conventions)
# ---------------------------------------------------------------------------------------------
def parse_record(path):
    """files {path: sha} (the `file:` / `sha256:` pairs: the amendment texts), entries {D1: sha, ...}, sections {name: {path: sha}}."""
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    files, entries, sections = OrderedDict(), OrderedDict(), OrderedDict()
    section = ""
    for i, line in enumerate(lines):
        if line and not line[0].isspace():
            m = re.match(r"^file:\s*(\S+)\s*$", line)
            if m and i + 1 < len(lines):
                s = re.match(rf"^sha256:\s*({HEX64})\s*$", lines[i + 1])
                if s:
                    files[m.group(1)] = s.group(1)
            if line.rstrip().endswith(":"):
                section = line.strip().rstrip(":")
            continue
        m = re.match(rf"^\s+(D[123])\s+({HEX64})\s*$", line)
        if m:
            entries[m.group(1)] = m.group(2)
            continue
        m = re.match(rf"^\s+(\S+)\s+sha256\s+({HEX64})", line)
        if m:
            sections.setdefault(section, OrderedDict())[m.group(1)] = m.group(2)
    t = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    return {"files": files, "entries": entries, "sections": sections, "t_amend_utc": t.group(1) if t else None}


def section_kind(name):
    n = name.lower()
    if "frozen files" in n:
        return "frozen"
    if n.startswith("l2 modules"):
        return "l2"
    if "inputs" in n:
        return "inputs"
    if "files added" in n:
        return "added"
    return "other"


def entry_sha256(text, tag):
    """The hash rule of the D questions: the text from the line `## Dk. ` to the line before the next line that starts with `## `."""
    m = re.search(r"^## " + re.escape(tag) + r"\. .*?(?=^## |\Z)", text, re.M | re.S)
    return hashlib.sha256(m.group(0).encode("utf-8")).hexdigest() if m else None


def record_group_paths(root, main_rel):
    """A record and its numbered supplements (..._S1.txt) and later records (..._B1.txt) in the same folder."""
    return l3_pins.record_paths(Path(root) / main_rel)


class ConvGroup:
    """One registered convention: a hash record (with supplements) and what of it is verified.

    inputs: True = every non-snapshot line of the sections `inputs at amendment`; False = none; a list = exactly these paths."""

    def __init__(self, name, record, inputs=True, entries=None, required=()):
        self.name, self.record, self.inputs, self.entries, self.required = name, record, inputs, entries, tuple(required)


REAL_GROUPS = OrderedDict((g.name, g) for g in [
    ConvGroup("ex1", DOCS + "PREREG_AMEND_20261015_HASH.txt",
              inputs=["data/l3_meta/l2_set_v2.json", "docs/plans/l2-pipeline/HUB_RULING_20261015_CLAUDE_ONLY.md"]),
    ConvGroup("n1", DOCS + "PREREG_AMEND_20261019_N1_V2B_HASH.txt",   # N1-V2 (2026-10-17 ruling); the V1 record file stays as it was
              inputs=["data/l3_meta/n1_sets_v2_20261019/n1_sets_v2.json", "data/l3_meta/covariates/state_year_v2.csv"]),
    ConvGroup("sweep", DOCS + "PREREG_AMEND_20261015_SWEEP_HASH.txt", inputs=True,
              required=["PREREG_AMEND_20261015_SWEEP_HASH_S1.txt", "PREREG_AMEND_20261015_SWEEP_HASH_B1.txt"]),
    ConvGroup("dq", DOCS + "PREREG_AMEND_20261015_D_HASH.txt", inputs=True,
              entries=(DOCS + "PREREG_AMEND_20261015_D_QUESTIONS.md", ("D1", "D2", "D3"))),
    ConvGroup("d2", DOCS + "PREREG_AMEND_20261015_D2_HASH.txt", inputs=True, required=["PREREG_AMEND_20261015_D2_HASH_S1.txt"]),
    ConvGroup("cal", DOCS + "PREREG_AMEND_20261015_CAL_HASH.txt", inputs=True),
    ConvGroup("sweep_ext", DOCS + "PREREG_AMEND_20261016_SWEEP_EXT_HASH.txt", inputs=True),
    ConvGroup("sweep_ext_wire", DOCS + "PREREG_AMEND_20261016_SWEEP_EXT_WIRE_HASH.txt", inputs=True),
])


def _verify_file(root, path, want):
    p = Path(root) / path
    if not p.is_file():
        return "file is missing"
    return None if sha256_file(p) == want else "differs from its record"


def check_group(root, group, snapshot_fn=is_snapshot):
    """Problems (strings) of one convention group, and a summary.  A snapshot line of an `inputs` section is not checked."""
    problems = []
    summary = OrderedDict(group=group.name, records=[], amendments=OrderedDict(), frozen=0, inputs=0, l2_modules=0, added=0,
                          snapshot_lines_exempt=[], entries=OrderedDict())
    recs = record_group_paths(root, group.record)
    names = {p.name for p in recs}
    if not recs[0].is_file():
        return [f"{group.name}: hash record {group.record} is missing"], summary
    for need in group.required:
        if need not in names:
            problems.append(f"{group.name}: supplement {need} is missing")
    listed_all = set()
    # a line of an `L2 modules` section is replaced by a later `L2 modules rebound` line of the same path (PREREG_AMEND_20261016_REPIN_V1.md)
    last_rebound = {}
    for i, rp in enumerate(recs):
        if rp.is_file():
            try:
                for p_ in l3_pins.rebound_lines(rp.read_text(encoding="utf-8")):
                    last_rebound[p_] = i
            except RuntimeError as e:
                problems.append(f"{group.name}: {rp.name}: {e}")
    summary["l2_rebound"] = sorted(last_rebound)
    # L3 code files: the line of the latest record counts (l3_pins.is_l3_code)
    latest_l3 = {}
    for i, rp in enumerate(recs):
        if rp.is_file():
            for p_ in parse_record(rp)["sections"].values():
                for path_ in p_:
                    if l3_pins.is_l3_code(path_):
                        latest_l3[path_] = i
    for i, rp in enumerate(recs):
        rrel = rel_to(root, rp)
        summary["records"].append(OrderedDict(path=rrel, sha256=sha256_file(rp)))
        rec = parse_record(rp)
        for path, want in rec["files"].items():
            why = _verify_file(root, path, want)
            summary["amendments"][path] = want
            if why:
                problems.append(f"{group.name}: amendment {path} {why} ({rrel})")
        for section, items in rec["sections"].items():
            kind = section_kind(section)
            listed_all.update(items)
            for path, want in items.items():
                if kind in ("frozen", "added", "l2"):
                    if kind == "l2" and last_rebound.get(path, -1) > i:
                        continue            # replaced by a later rebound line: that line is checked instead
                    if l3_pins.is_l3_code(path) and latest_l3.get(path, -1) > i:
                        continue            # an L3 code file listed again by a later supplement
                    why = _verify_file(root, path, want)
                    summary["frozen" if kind == "frozen" else "l2_modules" if kind == "l2" else "added"] += 1
                    if why:
                        problems.append(f"{group.name}: {kind} file {path} {why} ({rrel})")
                elif kind == "inputs":
                    if snapshot_fn(path):
                        summary["snapshot_lines_exempt"].append(path)
                        continue
                    if group.inputs is True or (group.inputs and path in group.inputs):
                        why = _verify_file(root, path, want)
                        summary["inputs"] += 1
                        if why:
                            problems.append(f"{group.name}: input {path} {why} ({rrel})")
        if group.entries:
            qpath, tags = group.entries
            qp = Path(root) / qpath
            if not qp.is_file():
                problems.append(f"{group.name}: question file {qpath} is missing")
            elif rec["entries"]:
                qtext = qp.read_text(encoding="utf-8")
                for tag in tags:
                    want, got = rec["entries"].get(tag), entry_sha256(qtext, tag)
                    summary["entries"][tag] = want
                    if want is None or got is None or want != got:
                        problems.append(f"{group.name}: entry {tag} of {qpath} differs from the record ({rrel})")
    if group.inputs and group.inputs is not True:
        for path in group.inputs:
            if path not in listed_all:
                problems.append(f"{group.name}: {path} is not listed in the record any more")
    n_l2 = sum(1 for p in problems if " l2 file scripts/l2_pipeline/" in p)
    if n_l2:
        problems.append(f"{group.name}: {n_l2} pinned L2 module(s) changed since the record; a change of a pinned L2 module needs a new dated record "
                        "(a numbered supplement of the sweep hash record) before the next number is computed")
    return problems, summary


def check_conventions(root, group_names, groups=None, snapshot_fn=is_snapshot):
    groups = groups if groups is not None else REAL_GROUPS
    problems, summaries = [], OrderedDict()
    for name in group_names:
        p, s = check_group(root, groups[name], snapshot_fn)
        problems += p
        summaries[name] = s
    return problems, summaries


# ---------------------------------------------------------------------------------------------
# rebinding of the builders (in memory, undone at the end)
# ---------------------------------------------------------------------------------------------
BUILDER_MODULES = ("sweep_core", "build_sweep", "build_d1", "build_d2", "build_d3", "build_d3_v2", "build_cal", "build_n1", "build_ex1",
                   "build_sweep_ext", "build_d1_ext")


def _bind_worker():
    """Rebind the run's paths inside a (spawned) worker process; the binding arrives through the environment variable L3_REFRESH_BINDING."""
    spec = json.loads(os.environ[ENV_SPEC])
    sys.dont_write_bytecode = True
    if spec["here"] not in sys.path:
        sys.path.insert(0, spec["here"])
    import sweep_core as S
    import build_sweep as B
    S.LIVE = Path(spec["live"])
    B.OUT = Path(spec["sweep_dir"])
    B.LOG = B.OUT / "RUN_LOG.jsonl"
    l3_pins.install(B)          # the worker's own L2 pin check follows the rebound rule too
    return S, B


def sweep_worker_init():
    """Initializer of the sweep worker processes (spawned, so they import a fresh build_sweep): rebinds the run's paths, then runs the
    builder's own initializer (which loads and checks the L2 modules)."""
    _, B = _bind_worker()
    orig = getattr(B, ORIG_INIT_ATTR, None) or B._init_worker
    orig()


def probe_init():
    _bind_worker()


def probe_task(_):
    import sweep_core as S
    import build_sweep as B
    return {"sweep_dir": str(B.OUT), "log": str(B.LOG), "live": str(S.LIVE)}


class Binding:
    """Points the builders at the run's own files for the duration of a refresh and records every replacement."""

    def __init__(self, root, live_path, live_sha, sweep_dir, results, modules=BUILDER_MODULES, snapshot_fn=is_snapshot, canon_live_rel=CANON_LIVE_REL,
                 sweep_mod="build_sweep", cal_mod="build_cal", d3v2_mod="build_d3_v2"):
        self.sweep_mod, self.cal_mod, self.d3v2_mod = sweep_mod, cal_mod, d3v2_mod
        self.root, self.live_path, self.live_sha = Path(root), Path(live_path), live_sha
        self.sweep_dir, self.results = Path(sweep_dir), dict(results)
        self.modules, self.snapshot_fn = tuple(modules), snapshot_fn
        self.canon = self.root / canon_live_rel
        self.live_rel = rel_to(self.root, live_path)
        self._undo = []
        self._prev_env = None
        self.patched = OrderedDict(constants=[], defaults=[], parsers=[], env=[])
        self.dropped = OrderedDict()
        self.active = False

    def _set_attr(self, mod, name, value, why):
        self._undo.append(("attr", mod, name, getattr(mod, name)))
        setattr(mod, name, value)
        self.patched["constants"].append(f"{mod.__name__}.{name} ({why})")

    def _set_defaults(self, mod, fn):
        d, kw = fn.__defaults__, fn.__kwdefaults__
        new_d = tuple(self.live_path if (isinstance(x, Path) and x == self.canon) else x for x in (d or ()))
        new_kw = {k: (self.live_path if (isinstance(v, Path) and v == self.canon) else v) for k, v in (kw or {}).items()}
        if new_d != tuple(d or ()) or new_kw != dict(kw or {}):
            self._undo.append(("defaults", fn, d, kw))
            fn.__defaults__ = new_d if d is not None else None
            fn.__kwdefaults__ = new_kw if kw is not None else None
            self.patched["defaults"].append(f"{mod.__name__}.{fn.__name__}")

    def _effective_parser(self, orig):
        binding = self

        def parse(*args, **kwargs):
            rec = orig(*args, **kwargs)
            listed, dropped = OrderedDict(), []
            for p, h in rec["listed"].items():
                if binding.snapshot_fn(p):
                    dropped.append(p)
                else:
                    listed[p] = h
            if binding.live_sha:
                listed[binding.live_rel] = binding.live_sha
            out = dict(rec)
            out["listed"] = listed
            where = args[0] if args else kwargs.get("path")
            binding.dropped.setdefault(rel_to(binding.root, where) if where else "default", set()).update(dropped)
            return out

        parse.__wrapped__ = orig
        return parse

    def apply(self):
        if self.active:
            return self
        mods = {n: importlib.import_module(n) for n in self.modules}
        for name, mod in mods.items():
            for attr, val in list(vars(mod).items()):
                if isinstance(val, Path) and val == self.canon:
                    self._set_attr(mod, attr, self.live_path, "live set")
                elif isinstance(val, types.FunctionType) and val.__module__ == mod.__name__:
                    self._set_defaults(mod, val)
        if self.sweep_mod in mods:
            B = mods[self.sweep_mod]
            self._set_attr(B, "OUT", self.sweep_dir, "sweep folder")
            self._set_attr(B, "LOG", self.sweep_dir / "RUN_LOG.jsonl", "sweep log")
            self._undo.append(("attr", B, "parse_hash_record", B.parse_hash_record))
            l3_pins.install(B)                  # rebound rule for pinned L2 modules (undone with the attribute above)
            B.parse_hash_record = self._effective_parser(B.parse_hash_record)
            self.patched["parsers"].append("build_sweep.parse_hash_record (rebound rule + snapshot lines dropped)")
            self._undo.append(("attr", B, "_init_worker", B._init_worker))
            setattr(B, ORIG_INIT_ATTR, B._init_worker)
            self._undo.append(("newattr", B, ORIG_INIT_ATTR, None))
            B._init_worker = sweep_worker_init
            self.patched["parsers"].append("build_sweep._init_worker (sweep workers)")
        if self.cal_mod in mods:
            for attr, key in (("EX1_JSON", "ex1"), ("D1_JSON", "d1"), ("D2_JSON", "d2"), ("D3_JSON", "d3_v2")):
                if key in self.results:
                    self._set_attr(mods[self.cal_mod], attr, Path(self.results[key]), "upstream result of this run")
        if self.d3v2_mod in mods and "d3_v1" in self.results:
            self._set_attr(mods[self.d3v2_mod], "D3V1_RESULTS", Path(self.results["d3_v1"]), "upstream result of this run")
        self._prev_env = os.environ.get(ENV_SPEC)
        os.environ[ENV_SPEC] = json.dumps({"here": str(HERE), "live": str(self.live_path), "sweep_dir": str(self.sweep_dir)})
        self.patched["env"].append(ENV_SPEC)
        self.active = True
        return self

    def restore(self):
        for item in reversed(self._undo):
            kind = item[0]
            if kind == "attr":
                setattr(item[1], item[2], item[3])
            elif kind == "newattr":
                if hasattr(item[1], item[2]):
                    delattr(item[1], item[2])
            elif kind == "defaults":
                item[1].__defaults__, item[1].__kwdefaults__ = item[2], item[3]
        self._undo = []
        if self.active:
            if self._prev_env is None:
                os.environ.pop(ENV_SPEC, None)
            else:
                os.environ[ENV_SPEC] = self._prev_env
        self.active = False

    def __enter__(self):
        return self.apply()

    def __exit__(self, *exc):
        self.restore()
        return False

    def unbound(self):
        """References to the canonical live path that survived the rebinding (must be empty while the binding is active)."""
        out = []
        for name in self.modules:
            mod = sys.modules.get(name)
            if mod is None:
                continue
            for attr, val in vars(mod).items():
                if isinstance(val, Path) and val == self.canon:
                    out.append(f"{name}.{attr}")
                elif isinstance(val, types.FunctionType) and val.__module__ == name:
                    if any(isinstance(x, Path) and x == self.canon for x in (val.__defaults__ or ())) or \
                            any(isinstance(x, Path) and x == self.canon for x in (val.__kwdefaults__ or {}).values()):
                        out.append(f"{name}.{attr}() default")
        return out

    def probe_workers(self):
        """Start one spawned worker with the same initializer mechanism as the sweep and ask it where it would read and write.  Returns problems."""
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(processes=1, initializer=probe_init) as pool:
            got = pool.apply(probe_task, (None,))
        want = {"sweep_dir": str(self.sweep_dir), "log": str(self.sweep_dir / "RUN_LOG.jsonl"), "live": str(self.live_path)}
        return [f"sweep worker {k} is {got.get(k)}, expected {v}" for k, v in want.items() if got.get(k) != v]

    def native_record_checks(self):
        """The builders' own hash-record checks, run under the effective parser (no live line is needed for them)."""
        B, D3, D2, C = (importlib.import_module(n) for n in ("build_sweep", "build_d3", "build_d2", "build_cal"))
        problems = list(B.verify_records()) + list(D3.verify_records())
        problems += list(D2.verify_d2_records()[0]) + list(C.verify_cal_records()[0])
        return problems

    def describe(self):
        return OrderedDict(live_path=self.live_rel, live_sha256=self.live_sha, constants=self.patched["constants"], function_defaults=self.patched["defaults"],
                           record_parsers=self.patched["parsers"], env=self.patched["env"],
                           snapshot_lines_dropped=OrderedDict((k, sorted(v)) for k, v in self.dropped.items()),
                           sanctioned_flags=OrderedDict(n1="--live-set-changed-ok", d3_v1="--live-set-changed-ok"))


# ---------------------------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------------------------
class StepFailed(Exception):
    pass


class Ctx:
    """What a step may use: the run's folders and live set."""

    def __init__(self, root, run_dir, live_path, live_sha, live, workers=4):
        self.root, self.run_dir, self.live_path, self.live_sha, self.live = Path(root), Path(run_dir), Path(live_path), live_sha, live
        self.workers = workers
        self.folders = OrderedDict()
        self.logs_dir = self.run_dir / "logs"

    def dir(self, key):
        return self.folders[key]

    def file(self, key, name):
        return self.folders[key] / name


class Step:
    """One unit of work.  `folder` is the output folder key (the stages of the sweep share one); `needs` are step names that must be done."""

    def __init__(self, name, folder, needs=(), groups=(), run=None, files=(), headline=(), provenance=None, baseline=None, codes=None, label=None):
        self.name, self.folder, self.needs, self.groups = name, folder, tuple(needs), tuple(groups)
        self.run, self.files, self.headline, self.provenance = run, tuple(files), tuple(headline), provenance
        self.baseline, self.codes, self.label = baseline, codes, label or name


def run_builder(ctx, module_name, argv, log_name):
    """Call the main function of a builder in this process, with its output in logs/.  Raises StepFailed on a refusal or an error."""
    mod = importlib.import_module(module_name)
    ctx.logs_dir.mkdir(parents=True, exist_ok=True)
    n, suffix = 1, ""
    while (ctx.logs_dir / f"{log_name}{suffix}.stdout.txt").exists():
        n += 1
        suffix = f".try{n}"
    out_path, err_path = ctx.logs_dir / f"{log_name}{suffix}.stdout.txt", ctx.logs_dir / f"{log_name}{suffix}.stderr.txt"
    rc, problem = None, None
    with open(out_path, "x", encoding="utf-8") as out, open(err_path, "x", encoding="utf-8") as err:
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = mod.main([str(a) for a in argv])
        except SystemExit as e:
            if e.code not in (None, 0):
                problem = f"{module_name} stopped: {e.code}"
            rc = 0 if e.code in (None, 0) else e.code
        except Exception as e:  # noqa: BLE001  (a builder error is a failed step, never a crash of the refresh)
            problem = f"{module_name} raised {type(e).__name__}: {str(e)[:300]}"
            err.write(traceback.format_exc())
    if problem is None and rc not in (None, 0):
        problem = f"{module_name} returned {rc}"
    if problem:
        raise StepFailed(problem + f" (see {rel_to(ctx.root, err_path)})")
    return {"stdout_log": rel_to(ctx.root, out_path), "stderr_log": rel_to(ctx.root, err_path)}


REAL_RESULT_FILES = OrderedDict([("ex1", "RESULTS_EX1_V3.json"), ("d1", "RESULTS_D1_V1.json"), ("d2", "RESULTS_D2_V1.json"),
                                 ("d3_v1", "RESULTS_D3_V1.json"), ("d3_v2", "RESULTS_D3_V2.json")])


def real_steps():
    """The steps of the real refresh (the live set is built by the refresh itself and is the first need of most steps)."""
    def builder(module, key, extra=(), sweep=False):
        def run(ctx):
            argv = ["--out-dir", ctx.dir(key)] + list(extra) + (["--sweep-dir", ctx.dir("sweep")] if sweep else [])
            return run_builder(ctx, module, argv, key)
        return run

    def sweep_stage(stage):
        def run(ctx):
            return run_builder(ctx, "build_sweep", ["--stage", stage, "--workers", ctx.workers], "sweep_" + stage)
        return run

    def prov(fname, **kw):
        return dict(file=fname, live_sha="stamp.live_set_sha256", D="stamp.D", roster="designs", **kw)

    def sweep_ext_run(ctx):
        return run_builder(ctx, "build_sweep_ext", ["--sweep-dir", ctx.dir("sweep"), "--out-dir", ctx.dir("sweep_ext"), "--workers", ctx.workers], "sweep_ext")

    def d1_ext_run(ctx):
        return run_builder(ctx, "build_d1_ext", ["--sweep-dir", ctx.dir("sweep"), "--ext-dir", ctx.dir("sweep_ext"), "--out-dir", ctx.dir("d1_ext"),
                                                 "--v1-results", ctx.file("d1", "RESULTS_D1_V1.json")], "d1_ext")

    sweep_up = {"stamp.sweep_run_sha256": ("sweep", "SWEEP_RUN.json"), "stamp.gate_report_sha256": ("sweep", "GATE_REPORT.json")}
    live = ("live",)
    return [
        Step("ex1", "ex1", live, ("ex1",), builder("build_ex1", "ex1"), ("RESULTS_EX1_V3.json", "l2_set_live_used.json", "RUN_LOG.jsonl"),
             headline=EX1_HEADLINE, provenance=prov("RESULTS_EX1_V3.json"), baseline="data/l3_meta/ex1_v3", label="EX-1 (Q1, Q3, Q5, N10)",
             codes=dict(file="RESULTS_EX1_V3.json", question="Q1", list="designs", id="design_id", mode="q1")),
        Step("n1", "n1", live, ("n1",), builder("build_n1_v2", "n1", ["--live-set-changed-ok"]), ("RESULTS_N1_V2.json", "N1_V2_SPLIT.json", "l2_set_live_used.json", "RUN_LOG.jsonl"),
             headline=N1_HEADLINE, provenance=prov("RESULTS_N1_V2.json"), baseline="data/l3_meta/n1_v2", label="N1",
             codes=dict(file="RESULTS_N1_V2.json", question="N1", list="designs", id="design_id", key="code")),
        Step("sweep_series", "sweep", live, ("sweep",), sweep_stage("series"), ("series_store_v1.npz", "SERIES_MANIFEST.json"),
             headline=SWEEP_SERIES_HEADLINE, baseline="data/l3_meta/sweep_v1", label="sweep: series store"),
        Step("sweep_series_count", "sweep", ("sweep_series",), ("sweep",), sweep_stage("series_count"), ("series_store_v1_count.npz", "SERIES_MANIFEST_COUNT.json"),
             label="sweep: death-count series"),
        Step("sweep_gate", "sweep", ("sweep_series_count",), ("sweep",), sweep_stage("gate"), ("GATE_REPORT.json",), headline=SWEEP_GATE_HEADLINE,
             provenance=dict(file="GATE_REPORT.json", live_sha="live_set_sha256"), baseline="data/l3_meta/sweep_v1", label="sweep: reproduction gate"),
        Step("sweep_sweep", "sweep", ("sweep_gate",), ("sweep",), sweep_stage("sweep"), ("SWEEP_RUN.json",), headline=SWEEP_RUN_HEADLINE,
             baseline="data/l3_meta/sweep_v1", label="sweep: every swept series"),
        Step("sweep_d3runs", "sweep", ("sweep_sweep",), ("sweep",), sweep_stage("d3runs"), ("D3_RUNS.json",), headline=SWEEP_D3_HEADLINE,
             baseline="data/l3_meta/sweep_v1", label="sweep: D3 totals"),
        Step("sweep_manifest", "sweep", ("sweep_d3runs",), ("sweep",), sweep_stage("manifest"), ("SWEEP_MANIFEST.json",),
             provenance=dict(file="SWEEP_MANIFEST.json", live_sha="live_set_sha256"), label="sweep: manifest"),
        Step("d1", "d1", ("sweep_manifest",), ("sweep", "dq"), builder("build_d1", "d1", sweep=True), ("RESULTS_D1_V1.json", "l2_set_live_used.json", "RUN_LOG.jsonl"),
             headline=D1_HEADLINE, provenance=prov("RESULTS_D1_V1.json", upstream=sweep_up), baseline="data/l3_meta/d1_v1", label="D1",
             codes=dict(file="RESULTS_D1_V1.json", question="D1", list="designs", id="design_id", key="code")),
        Step("sweep_ext", "sweep_ext", ("sweep_manifest",), ("sweep", "sweep_ext", "sweep_ext_wire"), sweep_ext_run, ("SWEEP_EXT_RUN.json",),
             headline=SWEEP_EXT_HEADLINE, label="sweep extension (55 series)",
             provenance=dict(file="SWEEP_EXT_RUN.json", live_sha="live_set_sha256", upstream={"gate_report_sha256": ("sweep", "GATE_REPORT.json"),
                                                                                              "sweep_run_sha256": ("sweep", "SWEEP_RUN.json")})),
        Step("d1_ext", "d1_ext", ("sweep_ext", "d1"), ("sweep", "dq", "sweep_ext", "sweep_ext_wire"), d1_ext_run,
             ("RESULTS_D1_V2.json", "D1_V2_PROVENANCE.json", "l2_set_live_used.json"), headline=D1EXT_HEADLINE, label="D1-V2 (V1 + extension series)",
             provenance=prov("RESULTS_D1_V2.json", upstream=sweep_up)),
        Step("d3_v1", "d3_v1", live, ("dq",), builder("build_d3", "d3_v1", ["--live-set-changed-ok"]), ("RESULTS_D3_V1.json", "NEEDS_ENGINE_RUNS.csv", "l2_set_live_used.json"),
             headline=D3V1_HEADLINE, provenance=prov("RESULTS_D3_V1.json"), baseline="data/l3_meta/d3_v1", label="D3-V1"),
        Step("d2", "d2", ("sweep_manifest",), ("sweep", "dq", "d2"), builder("build_d2", "d2", sweep=True),
             ("RESULTS_D2_V1.json", "NC_CELLS_D2_V1.csv", "l2_set_live_used.json"), headline=D2_HEADLINE, provenance=prov("RESULTS_D2_V1.json", upstream=sweep_up),
             baseline="data/l3_meta/d2_v1", label="D2", codes=dict(file="RESULTS_D2_V1.json", question="D2", list="designs", id="design_id", key="code")),
        Step("d3_v2", "d3_v2", ("sweep_manifest", "d3_v1"), ("sweep", "dq"), builder("build_d3_v2", "d3_v2", sweep=True), ("RESULTS_D3_V2.json", "l2_set_live_used.json"),
             headline=D3V2_HEADLINE, baseline="data/l3_meta/d3_v2", label="D3-V2",
             provenance=prov("RESULTS_D3_V2.json", upstream={"stamp.sweep_gate_report_sha256": ("sweep", "GATE_REPORT.json"),
                                                            "stamp.d3_runs_sha256": ("sweep", "D3_RUNS.json"), "stamp.sweep_run_sha256": ("sweep", "SWEEP_RUN.json")}),
             codes=dict(file="RESULTS_D3_V2.json", question="D3", list="designs", id="design_id", key="code")),
        Step("cal", "cal", ("ex1", "d1", "d2", "d3_v2", "sweep_manifest"), ("sweep", "dq", "d2", "cal"), builder("build_cal", "cal", sweep=True),
             ("RESULTS_CAL_V1.json", "l2_set_live_used.json"), headline=CAL_HEADLINE, baseline="data/l3_meta/cal_v1", label="CAL (Q1 and D1 calibrated)",
             provenance=dict(file="RESULTS_CAL_V1.json", live_sha="stamp.live_set_sha256", D="stamp.D",
                             upstream={"stamp.ex1_v3_sha256": ("ex1", "RESULTS_EX1_V3.json"), "stamp.d1_v1_sha256": ("d1", "RESULTS_D1_V1.json"),
                                       "stamp.d2_v1_sha256": ("d2", "RESULTS_D2_V1.json"), "stamp.d3_v2_sha256": ("d3_v2", "RESULTS_D3_V2.json"),
                                       "stamp.sweep_run_sha256": ("sweep", "SWEEP_RUN.json")})),
    ]


# ---------------------------------------------------------------------------------------------
# headline numbers: (label, result file, path in the file)
# ---------------------------------------------------------------------------------------------
_E = "RESULTS_EX1_V3.json"
EX1_HEADLINE = [
    ("Q1.D", _E, "questions.Q1.D"), ("Q1.n", _E, "questions.Q1.n"), ("Q1.k", _E, "questions.Q1.k"), ("Q1.F", _E, "questions.Q1.F"),
    ("Q1.rate", _E, "questions.Q1.rate_k_over_n"), ("Q1.expected_5pct", _E, "questions.Q1.expected_exceedances_under_5pct_reference"),
    ("Q1.interval95", _E, "questions.Q1.family_bootstrap.interval95"),
    ("Q3.n", _E, "questions.Q3.n_descriptive"), ("Q3.F", _E, "questions.Q3.F"), ("Q3.median_hits", _E, "questions.Q3.scores.all.median_hits"),
    ("Q3.naive_negative_hits", _E, "questions.Q3.scores.all.naive_always_negative_hits"),
    ("Q3.coverage80", _E, "questions.Q3.scores.all.coverage80_equal_design"),
    ("Q3.skill_S_n", _E, "questions.Q3.primary_S_complete_five_P_gt0.n"), ("Q3.skill_S", _E, "questions.Q3.primary_S_complete_five_P_gt0.S"),
    ("Q3.three_slot_designs", _E, "questions.Q3.coverage_codes.FIELD_MISSING (three-slot set)"),
    ("Q3.three_slot_warning", _E, "questions.Q3.three_slot_variant.WARNING"),
    ("Q5.D", _E, "questions.Q5.all_D.ALL.D"), ("Q5.estimated", _E, "questions.Q5.all_D.ALL.estimated"),
    ("Q5.reportable", _E, "questions.Q5.all_D.ALL.completed_reportable"),
    ("Q5.loop_D", _E, "questions.Q5.autopilot_loop.ALL.D"), ("Q5.loop_estimated", _E, "questions.Q5.autopilot_loop.ALL.estimated"),
    ("N10.n", _E, "questions.N10.n"), ("N10.F", _E, "questions.N10.F"), ("N10.r10", _E, "questions.N10.all_eligible.r10"),
]
_N = "RESULTS_N1_V2.json"
N1_HEADLINE = [
    ("N1.D", _N, "question.N1.D"), ("N1.n", _N, "question.N1.n"), ("N1.F", _N, "question.N1.F"),
    ("N1.theta1", _N, "question.N1.primary_literal_null.theta"), ("N1.interval95", _N, "question.N1.primary_literal_null.interval95"),
    ("N1.p_le_05", _N, "question.N1.primary_literal_null.permutation_p.p_le_05"),
    ("N1.expected_p_le_05", _N, "question.N1.primary_literal_null.permutation_p.expected_p_le_05"),
    ("N1.S1_theta1", _N, "question.N1.S1_size_matched_null.theta"),
]
SWEEP_SERIES_HEADLINE = [("SWEEP.series", "SERIES_MANIFEST.json", "n_series"), ("SWEEP.series_not_swept", "SERIES_MANIFEST.json", "n_skipped")]
SWEEP_GATE_HEADLINE = [("SWEEP.designs_gated", "GATE_REPORT.json", "n_designs"), ("SWEEP.gate_pass", "GATE_REPORT.json", "summary.PASS"),
                       ("SWEEP.gate_repro_fail", "GATE_REPORT.json", "summary.REPRO_FAIL")]
SWEEP_RUN_HEADLINE = [("SWEEP.swept", "SWEEP_RUN.json", "n_swept"), ("SWEEP.errors", "SWEEP_RUN.json", "n_error")]
SWEEP_D3_HEADLINE = [("SWEEP.d3_pairs", "D3_RUNS.json", "n_pairs"), ("SWEEP.d3_pairs_with_total", "D3_RUNS.json", "n_pairs_with_total")]
_D1 = "RESULTS_D1_V1.json"
D1_HEADLINE = [
    ("D1.D", _D1, "question.D1.D"), ("D1.n", _D1, "question.D1.n_designs_eval"), ("D1.F", _D1, "question.D1.F"),
    ("D1.theta", _D1, "question.D1.theta_D1_plain_mean_Z"), ("D1.interval95", _D1, "question.D1.E1.result.interval95"),
    ("D1.families_beyond", _D1, "question.D1.series_calibration.families_beyond_line"),
    ("D1.families_expected", _D1, "question.D1.series_calibration.families_beyond_expected"),
    ("D1.designs_p_le_05", _D1, "question.D1.pattern_counts.designs_p_le_05"),
]
SWEEP_EXT_HEADLINE = [("SWEEPEXT.swept", "SWEEP_EXT_RUN.json", "n_swept"), ("SWEEPEXT.errors", "SWEEP_EXT_RUN.json", "n_error"),
                      ("SWEEPEXT.series", "SWEEP_EXT_RUN.json", "n_ext_series")]
_D1X = "RESULTS_D1_V2.json"
D1EXT_HEADLINE = [
    ("D1X.D", _D1X, "question.D1.D"), ("D1X.n", _D1X, "question.D1.n_designs_eval"), ("D1X.F", _D1X, "question.D1.F"),
    ("D1X.theta", _D1X, "question.D1.theta_D1_plain_mean_Z"), ("D1X.interval95", _D1X, "question.D1.E1.result.interval95"),
    ("D1X.families_beyond", _D1X, "question.D1.series_calibration.families_beyond_line"),
    ("D1X.families_expected", _D1X, "question.D1.series_calibration.families_beyond_expected"),
    ("D1X.designs_p_le_05", _D1X, "question.D1.pattern_counts.designs_p_le_05"),
]
_D2 = "RESULTS_D2_V1.json"
D2_HEADLINE = [
    ("D2.D", _D2, "question.D2.D"), ("D2.designs", _D2, "question.D2.designs_d1_eval"), ("D2.m", _D2, "question.D2.estimate_nc_cells.m"),
    ("D2.F", _D2, "question.D2.estimate_nc_cells.F"), ("D2.n_beyond", _D2, "question.D2.estimate_nc_cells.n_beyond"),
    ("D2.pi_NC", _D2, "question.D2.estimate_nc_cells.pi_NC"), ("D2.delta", _D2, "question.D2.estimate_nc_cells.delta_D2"),
    ("D2.interval95", _D2, "question.D2.E1.result.interval95"),
    ("D2.width_ratio", _D2, "question.D2.empirical_null_width_nc_series.ratio_observed_to_reference_mean"),
    ("D2.reading", _D2, "question.D2.reading_guide_label"), ("D2.cells_group_not_coded", _D2, "question.D2.cell_codes.GROUP_NOT_CODED"),
]
D3V1_HEADLINE = [("D3V1.n", "RESULTS_D3_V1.json", "question.D3.n_pairs_eval"), ("D3V1.F", "RESULTS_D3_V1.json", "question.D3.F"),
                 ("D3V1.engine_runs_needed", "RESULTS_D3_V1.json", "question.D3.engine_runs_needed.count")]
_D3 = "RESULTS_D3_V2.json"
D3V2_HEADLINE = [
    ("D3.D", _D3, "question.D3.D"), ("D3.n", _D3, "question.D3.n_pairs_eval"), ("D3.F", _D3, "question.D3.F"),
    ("D3.theta", _D3, "question.D3.theta_D3_plain_mean_Z"), ("D3.interval95", _D3, "question.D3.E1.result.interval95"),
    ("D3.signals", _D3, "question.D3.pattern_counts.pairs_with_substitution_signal"),
]
_C = "RESULTS_CAL_V1.json"
CAL_HEADLINE = [
    ("CAL.kappa", _C, "question.CAL.kappa.kappa_hat"), ("CAL.kappa_interval95", _C, "question.CAL.kappa.interval95_BD"),
    ("CAL.Q1_n", _C, "question.CAL.Q1.at_1.n"), ("CAL.Q1_k_uncalibrated", _C, "question.CAL.Q1.at_1.k"),
    ("CAL.Q1_k_calibrated", _C, "question.CAL.Q1.at_kappa_hat.k"), ("CAL.Q1_expected", _C, "question.CAL.Q1.at_kappa_hat.expected_nominal"),
    ("CAL.D1_n", _C, "question.CAL.D1.at_1.n_designs"), ("CAL.D1_families_uncalibrated", _C, "question.CAL.D1.at_1.families_beyond_T_D"),
    ("CAL.D1_families_calibrated", _C, "question.CAL.D1.at_kappa_hat.families_beyond_T_D"),
    ("CAL.D1_families_expected", _C, "question.CAL.D1.at_kappa_hat.families_expected_sum_mu"),
    ("CAL.D1_theta_calibrated", _C, "question.CAL.D1.at_kappa_hat.theta"), ("CAL.D1_theta_interval95", _C, "question.CAL.D1.intervals.theta.joint.interval95"),
    ("CAL.fit", _C, "question.CAL.fit_to_negative_controls.label"),
]
# question label -> headline key that holds its n
N_PER_QUESTION = OrderedDict([("Q1", "Q1.n"), ("Q3", "Q3.n"), ("Q5", "Q5.D"), ("N10", "N10.n"), ("N1", "N1.n"), ("SWEEP", "SWEEP.swept"), ("D1", "D1.n"), ("D1ext", "D1X.n"),
                              ("D2", "D2.m"), ("D3", "D3.n"), ("CAL", "CAL.Q1_n")])


def extract_headline(spec, path_of):
    """{label: value}: a missing file or path gives None.  `path_of(file name)` returns the path of a result file."""
    cache, out = {}, OrderedDict()
    for label, fname, path in spec:
        if fname not in cache:
            p = path_of(fname)
            try:
                cache[fname] = read_json(p) if p is not None and Path(p).is_file() else None
            except (OSError, ValueError):
                cache[fname] = None
        out[label] = dig(cache[fname], path) if cache[fname] is not None else None
    return out


def design_codes(spec, folder):
    """{design_id: code}: the per-design coverage code of one question (read from its result), or {}."""
    p = Path(folder) / spec["file"]
    if not p.is_file():
        return {}
    out = {}
    for r in dig(read_json(p), spec["list"], []):
        did = r.get(spec["id"])
        if spec.get("mode") == "q1":
            out[did] = "EVAL" if "exceed" in r else ("STOP_PRE_RUN:" + str(r.get("gate")) if r.get("status") != "estimated" else "NO_NOISE_REF")
        else:
            out[did] = r.get(spec["key"])
    return out


# ---------------------------------------------------------------------------------------------
# provenance of a result, comparisons
# ---------------------------------------------------------------------------------------------
def check_provenance(spec, ctx, folder, live_designs, live_sha):
    """Problems (strings): does the result of a step belong to this run's live set and to this run's upstream files?"""
    problems = []
    p = Path(folder) / spec["file"]
    if not p.is_file():
        return [f"{spec['file']} is missing"]
    res = read_json(p)
    if spec.get("live_sha"):
        got = dig(res, spec["live_sha"])
        if got != live_sha:
            problems.append(f"{spec['file']}: {spec['live_sha']} is {str(got)[:16]}, the run's live set is {str(live_sha)[:16]}")
    if spec.get("D"):
        got = dig(res, spec["D"])
        if got != len(live_designs):
            problems.append(f"{spec['file']}: D is {got}, the live set has {len(live_designs)} designs")
    if spec.get("roster"):
        ids = [r.get("design_id") for r in (dig(res, spec["roster"]) or [])]
        if sorted(ids) != sorted(d["design_id"] for d in live_designs):
            problems.append(f"{spec['file']}: the design list differs from the run's live set")
    for key, (step_key, fname) in (spec.get("upstream") or {}).items():
        up = ctx.folders.get(step_key)
        want = sha256_file(up / fname) if (up is not None and (up / fname).is_file()) else None
        got = dig(res, key)
        if want is None:
            problems.append(f"{spec['file']}: upstream file {fname} of this run is missing")
        elif got != want:
            problems.append(f"{spec['file']}: {key} does not match this run's {fname}")
    return problems


def compare_headlines(prev, new):
    """Rows (key, old, new, status), status same | changed | new | gone; numbers compare with a relative tolerance of 1e-9."""
    rows = []
    for k in list(OrderedDict.fromkeys(list(prev) + list(new))):
        if k not in prev:
            rows.append((k, None, new[k], "new" if new[k] is not None else "same"))
        elif k not in new:
            rows.append((k, prev[k], None, "gone" if prev[k] is not None else "same"))
        else:
            rows.append((k, prev[k], new[k], "same" if same_value(prev[k], new[k]) else "changed"))
    return rows


def roster_of(live):
    return OrderedDict((d["design_id"], OrderedDict(status=d.get("status"), gate=d.get("gate"), source_group=d.get("source_group"),
                                                    point_estimate=d.get("point_estimate"))) for d in live["designs"])


def roster_diff(prev, new):
    """Designs added, removed, with a changed status or gate, with a changed point estimate (rosters as made by roster_of)."""
    added, removed = sorted(set(new) - set(prev)), sorted(set(prev) - set(new))
    moved, est = [], []
    for k in sorted(set(prev) & set(new)):
        a, b = prev[k], new[k]
        if (a["status"], a["gate"]) != (b["status"], b["gate"]):
            moved.append(OrderedDict(design_id=k, **{"from": [a["status"], a["gate"]], "to": [b["status"], b["gate"]]}))
        if not same_value(a.get("point_estimate"), b.get("point_estimate")):
            est.append(OrderedDict(design_id=k, **{"from": a.get("point_estimate"), "to": b.get("point_estimate")}))
    return OrderedDict(added=added, removed=removed, status_changed=moved, estimate_changed=est)


PROVENANCE_TOP = ("code", "run_utc", "built_utc", "live_set_sha256", "amendment_sha256", "python", "numpy", "scipy", "seconds")   # top-level provenance fields of the sweep files
IGNORE_KEYS = ("stamp", "inputs", "audit") + PROVENANCE_TOP
IGNORE_FIELD_RE = re.compile(r"(^|_)(sha256|utc|seconds|file|path|folder)$|^sweep_file")
# stamp fields that move in a rerun on the same data: clocks, the live-set hash and the hashes of this run's upstream files
EXPECTED_STAMP_DIFFS = {"run_utc", "cutoff_utc", "built_utc", "live_set_sha256", "live_set_matches_amendment_record", "live_set_matches_record", "outside_D", "seconds",
                        "sweep_run_sha256", "gate_report_sha256", "sweep_manifest_sha256", "sweep_gate_report_sha256", "d3_runs_sha256", "ex1_v3_sha256", "d1_v1_sha256",
                        "d2_v1_sha256", "d3_v2_sha256", "hash_record_sha256", "hash_record_s1_sha256"}


def deep_diff(a, b, tol=REL_TOL, limit=25, ignore_re=IGNORE_FIELD_RE, top_ignore=IGNORE_KEYS):
    """(n differences, examples, largest relative numeric difference inside the tolerance) between two parsed results.  The top-level
    provenance blocks and the provenance-only fields (hashes, paths, clocks) are skipped."""
    acc = {"n": 0, "ex": [], "max_rel": 0.0}

    def walk(x, y, path, top):
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y), key=str):
                if (top and k in top_ignore) or ignore_re.search(str(k)):
                    continue
                sub = f"{path}.{k}" if path else str(k)
                if k not in x or k not in y:
                    acc["n"] += 1
                    if len(acc["ex"]) < limit:
                        acc["ex"].append(f"{sub}: only in {'new' if k in y else 'old'}")
                else:
                    walk(x[k], y[k], sub, False)
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                acc["n"] += 1
                if len(acc["ex"]) < limit:
                    acc["ex"].append(f"{path}: length {len(x)} -> {len(y)}")
            for i, (u, v) in enumerate(zip(x, y)):
                walk(u, v, f"{path}[{i}]", False)
        elif is_number(x) and is_number(y):
            if not same_value(x, y, tol):
                acc["n"] += 1
                if len(acc["ex"]) < limit:
                    acc["ex"].append(f"{path}: {x!r} -> {y!r}")
            elif x != y:
                acc["max_rel"] = max(acc["max_rel"], abs(x - y) / max(1.0, abs(x), abs(y)))
        elif x != y:
            acc["n"] += 1
            if len(acc["ex"]) < limit:
                acc["ex"].append(f"{path}: {str(x)[:60]!r} -> {str(y)[:60]!r}")

    walk(a, b, "", True)
    return acc["n"], acc["ex"], acc["max_rel"]


def provenance_differences(old, new):
    """Top-level provenance fields (clocks, hashes, code hashes, versions) that differ between two results without a stamp block, with the code files named."""
    out = []
    for k in PROVENANCE_TOP:
        a, b = old.get(k), new.get(k)
        if a is None and b is None or same_value(a, b):
            continue
        if k == "code" and isinstance(a, dict) and isinstance(b, dict):
            out += [f"code:{Path(f).name}" for f in sorted(set(a) | set(b)) if a.get(f) != b.get(f)]
        else:
            out.append(k)
    return out


CODE_FIELD_RE = re.compile(r"(^|_)code(_|$)")


def stamp_differences(old, new):
    """Keys of the stamp that differ, split into the provenance fields that always move (clocks, live-set and upstream-file hashes), the code-hash
    fields (the code that made an upstream file, recorded in the stamp) and the others, which must not differ."""
    a, b = old.get("stamp") or {}, new.get("stamp") or {}
    expected, code, other = [], [], []
    for k in sorted(set(a) | set(b)):
        if not same_value(a.get(k), b.get(k)):
            (expected if k in EXPECTED_STAMP_DIFFS else code if CODE_FIELD_RE.search(k) else other).append(k)
    return expected, code, other


def compare_npz(old_path, new_path, tol=REL_TOL):
    """(largest relative difference, names of arrays that differ) of two sweep files; the provenance text array is skipped."""
    import numpy as np
    a, b = np.load(old_path, allow_pickle=False), np.load(new_path, allow_pickle=False)
    bad, worst = [], 0.0
    for k in sorted(set(a.files) | set(b.files)):
        if k == "meta_json":
            continue
        if k not in a.files or k not in b.files:
            bad.append(k)
            continue
        x, y = a[k], b[k]
        if x.shape != y.shape:
            bad.append(k)
        elif x.dtype.kind in "fiu":
            xf, yf = x.astype(float), y.astype(float)
            both_nan = np.isnan(xf) & np.isnan(yf)
            diff = np.where(both_nan, 0.0, np.abs(xf - yf) / np.maximum(1.0, np.maximum(np.abs(xf), np.abs(yf))))
            if np.isnan(diff).any():
                bad.append(k)
            else:
                m = float(diff.max()) if diff.size else 0.0
                worst = max(worst, m)
                if m > tol:
                    bad.append(k)
        elif not (x == y).all():
            bad.append(k)
    return worst, bad


# ---------------------------------------------------------------------------------------------
# configuration of the real repository
# ---------------------------------------------------------------------------------------------
REAL_CODE_FILES = ["scripts/l3_meta/" + f for f in (
    "build_live_set.py", "build_ex1.py", "build_n1.py", "build_sweep.py", "sweep_core.py", "sweep_d3.py", "build_d1.py", "build_d2.py", "reconcile_d2.py",
    "inputs_b_common.py", "build_d3.py", "build_d3_v2.py", "build_cal.py")]
REAL_ORCHESTRATOR_FILES = ["scripts/l3_meta/refresh_all.py"]      # recorded in the manifest; editing it does not start a new version
REAL_STATIC_INPUTS = [
    "data/l3_meta/l2_set_v2.json", "data/l3_meta/n1_sets_v2/n1_sets_v2.json", "data/l3_meta/n1_sets_v2_20261019/n1_sets_v2.json", "data/l3_meta/n1_sets/n1_sets_v1.json", "data/l3_meta/covariates/state_year_v2.csv",
    "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv", "data/l3_meta/d2_plausibility/d2_codes_coder1_v1.csv", "data/l3_meta/d2_plausibility/d2_codes_coder2_v1.csv",
    "data/l3_meta/d2_plausibility/d2_codes_third_v1.csv", "docs/plans/l3-meta/OUTCOME_DIMENSION_INVENTORY_20261015.csv",
    "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json", "docs/plans/l2-pipeline/L2_COUNTING_RULE.md"]
REAL_REGISTRY_FILES = ["data/l2_pipeline/registry/TERMINAL_RUNS.json", "data/l2_pipeline/registry/FORECAST_SET_ANNOTATIONS.json",
                       "data/l2_pipeline/registry/ACTIVE_DESIGNS.json"]
FINGERPRINT_REGISTRY = ("data/l2_pipeline/registry/TERMINAL_RUNS.json", "data/l2_pipeline/registry/FORECAST_SET_ANNOTATIONS.json")


def real_live_preview():
    import build_live_set as L
    payload, _, _ = L.build(dry_run=True)
    return payload


def real_live_write(payload, out_dir):
    import build_live_set as L
    live, _ = L.write_versioned(payload, Path(out_dir), payload["as_of"])
    return live


def real_binding_factory(cfg, ctx, results):
    return Binding(cfg.root, ctx.live_path, ctx.live_sha, ctx.folders.get("sweep", ctx.run_dir / "sweep"), results, snapshot_fn=cfg.snapshot_fn)


class Config:
    """Everything the refresh needs; the defaults are the real repository, tests replace the pieces.  binding_factory=None runs without rebinding."""

    def __init__(self, root=ROOT, steps=None, groups=None, snapshot_fn=is_snapshot, code_files=None, static_inputs=None, registry_files=None,
                 live_preview=None, live_write=None, binding_factory=REAL, today=None, baseline_live_rel=CANON_LIVE_REL, refresh_rel=REFRESH_REL,
                 digest_rel=DIGEST_REL, result_files=None, fingerprint_registry=FINGERPRINT_REGISTRY, orchestrator_files=None):
        self.root = Path(root)
        self.steps = steps if steps is not None else real_steps()
        self.groups = groups if groups is not None else REAL_GROUPS
        self.snapshot_fn = snapshot_fn
        self.code_files = list(code_files if code_files is not None else REAL_CODE_FILES)
        self.static_inputs = list(static_inputs if static_inputs is not None else REAL_STATIC_INPUTS)
        self.registry_files = list(registry_files if registry_files is not None else REAL_REGISTRY_FILES)
        self.live_preview = live_preview or real_live_preview
        self.live_write = live_write or real_live_write
        self.binding_factory = real_binding_factory if binding_factory is REAL else binding_factory
        self.today = today or (lambda: dt.date.today().strftime("%Y%m%d"))
        self.baseline_live_rel, self.refresh_rel, self.digest_rel = baseline_live_rel, refresh_rel, digest_rel
        self.result_files = OrderedDict(result_files if result_files is not None else REAL_RESULT_FILES)
        self.fingerprint_registry = tuple(fingerprint_registry)
        self.orchestrator_files = list(orchestrator_files if orchestrator_files is not None else REAL_ORCHESTRATOR_FILES)

    def step(self, name):
        return next(s for s in self.steps if s.name == name)


def material_fingerprint(live):
    """The part of the live set that decides a result: the design rows and the counts (not the clock, the paths scanned or the in-flight designs)."""
    counts = {k: live["counts"].get(k) for k in ("processed", "estimated", "not_evaluable")}
    return sha256_bytes(canon({"designs": live["designs"], "counts": counts}).encode("utf-8"))


class RunLog:
    def __init__(self, path):
        self.path = Path(path)

    def add(self, event, **kw):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(clean(dict(utc=utc_now(), event=event, **kw)), ensure_ascii=False, sort_keys=True) + "\n")

    def events(self):
        if not self.path.is_file():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------------------------------------
# the refresh
# ---------------------------------------------------------------------------------------------
class Refresher:
    def __init__(self, cfg, out=print):
        self.cfg, self.root, self.out = cfg, cfg.root, out
        self.refresh_dir = self.root / cfg.refresh_rel

    # -- selection and gate
    def select(self, names=None):
        order = [s.name for s in self.cfg.steps]
        if not names:
            return list(order)
        wanted = set()
        for n in names:
            n = n.strip()
            hit = [s.name for s in self.cfg.steps if s.name == n or s.folder == n or s.name.startswith(n + "_")]
            if not hit:
                raise SystemExit(f"unknown step {n!r}; steps: {', '.join(order)}")
            wanted.update(hit)
        return [s for s in order if s in wanted]

    def groups_of(self, names):
        out = []
        for n in names:
            for g in self.cfg.step(n).groups:
                if g not in out:
                    out.append(g)
        return out

    def gate(self, names):
        return check_conventions(self.root, self.groups_of(names), self.cfg.groups, self.cfg.snapshot_fn)

    def anchor_problems(self, summaries, prev):
        """A hash record edited since the previous refresh (the records are not edited after T_amend)."""
        problems = []
        if not prev:
            return problems
        old = (prev.get("conventions") or {}).get("records") or {}
        for g, s in summaries.items():
            for r in s["records"]:
                if r["path"] in old and old[r["path"]] != r["sha256"]:
                    problems.append(f"{g}: hash record {r['path']} was edited after the previous refresh ({prev['run']['folder']})")
        return problems

    # -- previous refresh and the existing versions
    def previous(self):
        """The latest complete full refresh manifest (dict) or None."""
        if not self.refresh_dir.is_dir():
            return None
        runs = sorted((p for p in self.refresh_dir.iterdir() if p.is_dir() and re.match(r"^\d{8}_r\d+$", p.name)),
                      key=lambda p: (p.name[:8], int(p.name.split("_r")[1])))
        for p in reversed(runs):
            m = p / "REFRESH_MANIFEST.json"
            if m.is_file():
                try:
                    j = read_json(m)
                except ValueError:
                    continue
                if j.get("status") == "complete" and not j["run"].get("subset"):
                    j["_manifest_sha256"] = sha256_file(m)
                    return j
        return None

    def baseline(self, names=None):
        """The existing versions (before any refresh): headline numbers from their result files, the canonical live set, the code hashes in their stamps."""
        head, codes = OrderedDict(), OrderedDict()
        for s in self.cfg.steps:
            if names is not None and s.name not in names:
                continue
            if not s.baseline or not s.headline:
                continue
            base = self.root / s.baseline
            if base.is_dir():
                head.update(extract_headline(s.headline, lambda f, b=base: b / f))
        for s in self.cfg.steps:
            if s.provenance and s.baseline and (self.root / s.baseline / s.provenance["file"]).is_file():
                st = dig(read_json(self.root / s.baseline / s.provenance["file"]), "stamp") or {}
                if isinstance(st.get("code"), str) and st.get("code_sha256"):
                    codes[st["code"]] = st["code_sha256"]
                elif isinstance(st.get("code"), dict):
                    codes.update(st["code"])
        live_p = self.root / self.cfg.baseline_live_rel
        live = read_json(live_p) if live_p.is_file() else None
        return {"kind": "existing versions (before the first refresh)", "headline": head, "code": codes,
                "roster": roster_of(live) if live else None, "live_sha256": sha256_file(live_p) if live_p.is_file() else None,
                "live_as_of": live.get("as_of") if live else None,
                "outside_D": OrderedDict((k, (len(v) if isinstance(v, (list, dict)) else v)) for k, v in (live.get("excluded_not_counted") or {}).items()) if live else None}

    # -- inputs and fingerprints
    def hashes(self, rels):
        out = OrderedDict()
        for r in rels:
            p = self.root / r
            out[r] = OrderedDict(sha256=sha256_file(p), bytes=p.stat().st_size) if p.is_file() else None
        return out

    def code_hashes(self):
        return OrderedDict((f, sha256_file(self.root / f)) for f in self.cfg.code_files if (self.root / f).is_file())

    def fingerprint(self, live, registry_hashes, code_hashes, record_hashes):
        return OrderedDict(
            roster=material_fingerprint(live),
            registry=OrderedDict((k, (registry_hashes.get(k) or {}).get("sha256")) for k in self.cfg.fingerprint_registry if k in registry_hashes),
            code=OrderedDict(code_hashes), records=OrderedDict(record_hashes))

    def why_changed(self, fp, prev_fp):
        reasons = []
        if fp["roster"] != prev_fp.get("roster"):
            reasons.append("live roster")
        for k, v in fp["registry"].items():
            if (prev_fp.get("registry") or {}).get(k) != v:
                reasons.append(f"registry {k.split('/')[-1]}")
        for k, v in fp["code"].items():
            if (prev_fp.get("code") or {}).get(k) != v:
                reasons.append(f"code {k.split('/')[-1]}")
        for k, v in fp["records"].items():
            if (prev_fp.get("records") or {}).get(k) != v:
                reasons.append(f"record {k.split('/')[-1]}")
        return reasons

    def next_run(self, date):
        ns = []
        if self.refresh_dir.is_dir():
            ns = [int(m.group(1)) for p in self.refresh_dir.iterdir() for m in [re.match(rf"^{date}_r(\d+)$", p.name)] if m]
        return f"r{(max(ns) + 1) if ns else 1}"

    # -- dry run
    def dry_run(self, names, args):
        t0 = time.time()
        problems, summaries = self.gate(names)
        prev = self.previous()
        problems += self.anchor_problems(summaries, prev)
        date = args.date or self.cfg.today()
        self.out(f"L3 refresh, dry run (nothing is written).  Planned folder: {self.cfg.refresh_rel}/{date}_{self.next_run(date)}")
        self.out("conventions: " + "; ".join(
            f"{g}: {len(s['records'])} record(s), {s['frozen']} frozen, {s['inputs']} inputs, {s['l2_modules']} L2 modules, {len(s['snapshot_lines_exempt'])} snapshot lines exempt"
            for g, s in summaries.items()))
        if problems:
            self.out("REFUSED, a convention does not match its record:")
            for p in problems:
                self.out("  - " + p)
            return EXIT_REFUSED
        self.out("conventions: every hash matches its record")
        try:
            live = self.cfg.live_preview()
        except Exception as e:  # noqa: BLE001
            self.out(f"the live L2 set could not be built: {type(e).__name__}: {str(e)[:300]}")
            return EXIT_FAILED
        self.out(f"live set (read in memory): as of {live['as_of']}, D = {len(live['designs'])} "
                 f"({live['counts'].get('estimated')} estimated, {live['counts'].get('not_evaluable')} not evaluable)")
        base = prev if prev else self.baseline(names)
        prev_roster = prev["live_set"]["roster"] if prev else base.get("roster")
        if prev_roster:
            d = roster_diff(prev_roster, roster_of(live))
            self.out(f"roster against {'refresh ' + prev['run']['folder'] if prev else 'the existing versions'}: +{len(d['added'])} designs, -{len(d['removed'])}, "
                     f"{len(d['status_changed'])} status changes, {len(d['estimate_changed'])} estimate changes")
        recs = {r["path"]: r["sha256"] for s in summaries.values() for r in s["records"]}
        fp = self.fingerprint(live, self.hashes(self.cfg.registry_files), self.code_hashes(), recs)
        if prev:
            reasons = self.why_changed(fp, prev["fingerprint"])
            self.out("against the previous refresh: " + ("changed (" + ", ".join(reasons) + ")" if reasons else "NO CHANGE: a real run builds no new version (use --force)"))
        else:
            self.out("no previous refresh: a real run builds the first one and compares it with the existing versions")
        self.out("steps:")
        for n in names:
            s = self.cfg.step(n)
            self.out(f"  {n:<19} -> {s.folder}/   needs {', '.join(s.needs) or '-'}")
        if self.cfg.binding_factory:
            for line in self.binding_self_check():
                self.out(line)
        self.out(f"dry run done in {time.time() - t0:.0f}s")
        return EXIT_OK

    def binding_self_check(self):
        """Rebinding targets exist and the builders' own record checks pass under the effective parser (nothing is computed)."""
        try:
            b = Binding(self.root, self.root / self.cfg.refresh_rel / "_selfcheck" / "l2_set_live.json", None, self.root / self.cfg.refresh_rel / "_selfcheck" / "sweep", {},
                        snapshot_fn=self.cfg.snapshot_fn)
            with b:
                left = b.unbound()
                native = b.native_record_checks()
                probe = b.probe_workers()
                d = b.describe()
            lines = [f"binding: {len(d['constants'])} constants, {len(d['function_defaults'])} default arguments, {len(d['record_parsers'])} wrapped functions; "
                     f"unbound references {len(left)}; the builders' own record checks {'pass' if not native else 'FAIL'}; "
                     f"a spawned sweep worker {'sees the run folder' if not probe else 'does NOT see the run folder'}"]
            return lines + ["  - unbound " + x for x in left] + ["  - " + x for x in native[:5]] + ["  - " + x for x in probe]
        except Exception as e:  # noqa: BLE001
            return [f"binding self-check could not run: {type(e).__name__}: {e}"]

    # -- real run
    def run(self, args):
        names = self.select(args.steps.split(",") if args.steps else None)
        if args.dry_run:
            return self.dry_run(names, args)
        problems, summaries = self.gate(names)
        prev = self.previous()
        problems += self.anchor_problems(summaries, prev)
        checks = RunLog(self.refresh_dir / "CHECKS.jsonl")
        if problems:
            self.out("REFUSED: a convention does not match its record; nothing was computed.")
            for p in problems:
                self.out("  - " + p)
            checks.add("refused", problems=problems, steps=names)
            return EXIT_REFUSED
        self.refresh_dir.mkdir(parents=True, exist_ok=True)
        lock_fh = open(self.refresh_dir / ".lock", "a")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.out("another refresh holds the lock; nothing was done.")
            lock_fh.close()
            return EXIT_FAILED
        try:
            return self._run_locked(names, summaries, prev, args)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    def _start_new(self, names, subset, prev, args, fp_inputs):
        cfg = self.cfg
        self.out("reading the live L2 set (read-only on L2) ...")
        t0 = time.time()
        try:
            live = cfg.live_preview()
        except Exception as e:  # noqa: BLE001
            self.out(f"the live L2 set could not be built: {type(e).__name__}: {str(e)[:300]}")
            RunLog(self.refresh_dir / "CHECKS.jsonl").add("live_set_failed", error=f"{type(e).__name__}: {str(e)[:300]}")
            return EXIT_FAILED
        fp = self.fingerprint(live, fp_inputs["registry"], fp_inputs["code"], fp_inputs["records"])
        if prev and not args.force and not subset:
            reasons = self.why_changed(fp, prev["fingerprint"])
            if not reasons:
                self.out(f"no change since refresh {prev['run']['folder']}: no new version (use --force to build one anyway).")
                RunLog(self.refresh_dir / "CHECKS.jsonl").add("no_change", compared_to=prev["run"]["folder"], live_as_of=live["as_of"])
                return EXIT_OK
            self.out("changed since the previous refresh: " + ", ".join(reasons))
        date = args.date or cfg.today()
        run_id = self.next_run(date)
        run_dir = self.refresh_dir / f"{date}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        live_path = Path(cfg.live_write(live, run_dir / "live"))
        live_seconds = round(time.time() - t0, 1)
        log = RunLog(run_dir / "RUN_LOG.jsonl")
        log.add("run_start", run=f"{date}_{run_id}", steps=names, subset=subset, live_path=rel_to(self.root, live_path), live_sha256=sha256_file(live_path),
                fingerprint=fp, registry_at_start=fp_inputs["registry"], command=" ".join(sys.argv[1:]), force=bool(args.force), live_seconds=live_seconds)
        return dict(run_dir=run_dir, date=date, run_id=run_id, live=live, live_path=live_path, fp=fp, log=log, registry_start=fp_inputs["registry"],
                    live_seconds=live_seconds)

    def _start_resume(self, args, fp_inputs):
        run_dir = Path(args.resume)
        run_dir = run_dir if run_dir.is_absolute() else self.root / run_dir
        log = RunLog(run_dir / "RUN_LOG.jsonl")
        start = next((e for e in log.events() if e["event"] == "run_start"), None)
        if not start:
            self.out(f"{run_dir} has no run_start event: cannot resume")
            return EXIT_USAGE
        if (run_dir / "REFRESH_MANIFEST.json").is_file():
            self.out(f"{rel_to(self.root, run_dir)} is complete (it has REFRESH_MANIFEST.json): nothing to resume.")
            return EXIT_OK
        live_path = self.root / start["live_path"]
        if not live_path.is_file() or sha256_file(live_path) != start["live_sha256"]:
            self.out("the run's live set is missing or changed on disk: cannot resume")
            return EXIT_REFUSED
        if dict(start["fingerprint"]["code"]) != dict(fp_inputs["code"]):
            self.out("the code changed since the run started: cannot resume (start a new run)")
            return EXIT_REFUSED
        date, run_id = run_dir.name.split("_", 1)
        return dict(run_dir=run_dir, date=date, run_id=run_id, live=read_json(live_path), live_path=live_path, fp=start["fingerprint"], log=log,
                    registry_start=start.get("registry_at_start") or fp_inputs["registry"], live_seconds=start.get("live_seconds"))

    def _run_locked(self, names, summaries, prev, args):
        cfg = self.cfg
        subset = len(names) != len(cfg.steps)
        started = utc_now()
        recs = {r["path"]: r["sha256"] for s in summaries.values() for r in s["records"]}
        fp_inputs = {"registry": self.hashes(cfg.registry_files), "code": self.code_hashes(), "records": recs}
        run = self._start_resume(args, fp_inputs) if args.resume else self._start_new(names, subset, prev, args, fp_inputs)
        if isinstance(run, int):
            return run
        run_dir, log, live, live_path = run["run_dir"], run["log"], run["live"], run["live_path"]
        self.out(f"run {run['date']}_{run['run_id']}: live set as of {live['as_of']}, D = {len(live['designs'])}")
        ctx = Ctx(self.root, run_dir, live_path, sha256_file(live_path), live, workers=min(max(args.workers, 1), 6))
        ctx.folders["live"] = live_path.parent
        done = {}
        for e in log.events():
            if e["event"] == "step_end" and e.get("status") == "ok":
                done[e["step"]] = e
        redo = self._allocate(names, run_dir, ctx, done)
        results = {k: ctx.folders[k] / fname for k, fname in cfg.result_files.items() if k in ctx.folders}
        outcomes = OrderedDict()
        binding, bound, left, pre = None, None, [], []
        try:
            names_to_run = names
            if cfg.binding_factory:
                try:
                    binding = cfg.binding_factory(cfg, ctx, results)
                    binding.apply()
                    left = list(binding.unbound())
                    if left:
                        pre.append("the rebinding left references to the canonical live set: " + ", ".join(left))
                    for check in (binding.probe_workers, binding.native_record_checks):
                        try:
                            pre += list(check())
                        except Exception as e:  # noqa: BLE001
                            pre.append(f"preflight {check.__name__} failed: {type(e).__name__}: {str(e)[:200]}")
                except Exception as e:  # noqa: BLE001
                    pre.append(f"the rebinding could not be applied: {type(e).__name__}: {str(e)[:200]}")
                if pre:
                    for n in names:
                        outcomes[n] = OrderedDict(step=n, label=cfg.step(n).label, status="blocked", message="preflight: " + "; ".join(pre[:3]))
                    self.out("preflight failed, nothing computed:")
                    for x in pre:
                        self.out("  - " + x)
                    names_to_run = []
            for n in names_to_run:
                s = cfg.step(n)
                if s.folder not in redo:
                    ev = {k: v for k, v in done[n].items() if k not in ("event", "utc")}
                    outcomes[n] = OrderedDict(ev, resumed=True)
                    self.out(f"  {n}: done earlier ({ev['folder']})")
                    continue
                bad = [d for d in s.needs if d != "live" and outcomes.get(d, {}).get("status") != "ok"]
                if bad:
                    outcomes[n] = OrderedDict(step=n, label=s.label, status="blocked", message="needs " + ", ".join(bad))
                    log.add("step_end", **outcomes[n])
                    self.out(f"  {n}: blocked (needs {', '.join(bad)})")
                    continue
                outcomes[n] = self._exec(s, ctx, log)
        finally:
            if binding is not None:
                try:
                    bound = binding.describe()
                finally:
                    binding.restore()
        return self._finish(names, subset, summaries, prev, args, ctx, outcomes, log, started, run, recs, bound, left, pre)

    def _allocate(self, names, run_dir, ctx, done):
        """Output folder per folder key: the folder of earlier finished steps when all steps of the key are intact, else a new folder."""
        redo = set()
        for n in names:
            s = self.cfg.step(n)
            if s.folder in ctx.folders:
                continue
            members = [m for m in names if self.cfg.step(m).folder == s.folder]
            if all(m in done and self._intact(done[m]) for m in members):
                ctx.folders[s.folder] = self.root / done[members[0]]["folder"]
            else:
                p, k = run_dir / s.folder, 2
                while p.exists():
                    p = run_dir / f"{s.folder}_try{k}"
                    k += 1
                ctx.folders[s.folder] = p
                redo.add(s.folder)
        return redo

    def _intact(self, ev):
        folder = self.root / ev["folder"]
        return all((folder / f).is_file() and sha256_file(folder / f) == h for f, h in (ev.get("files") or {}).items())

    def _exec(self, s, ctx, log):
        t0 = time.time()
        folder = ctx.folders[s.folder]
        out = OrderedDict(step=s.name, label=s.label, key=s.folder, status="failed", folder=rel_to(self.root, folder), message=None)
        log.add("step_start", step=s.name, folder=out["folder"])
        self.out(f"  {s.name}: running ...")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            out.update(s.run(ctx) or {})
            missing = [f for f in s.files if not (folder / f).is_file()]
            if missing:
                raise StepFailed("expected output missing: " + ", ".join(missing))
            out["files"] = OrderedDict((f, sha256_file(folder / f)) for f in s.files)
            if s.provenance:
                prob = check_provenance(s.provenance, ctx, folder, ctx.live["designs"], ctx.live_sha)
                out["provenance"] = OrderedDict(ok=not prob, problems=prob)
                if prob:
                    raise StepFailed("provenance: " + "; ".join(prob[:3]))
            out["status"] = "ok"
        except StepFailed as e:
            out["message"] = str(e)
        except Exception as e:  # noqa: BLE001
            out["message"] = f"{type(e).__name__}: {str(e)[:300]}"
        out["seconds"] = round(time.time() - t0, 1)
        log.add("step_end", **out)
        self.out(f"  {s.name}: {out['status']} in {out['seconds']}s" + (f" - {out['message']}" if out.get("message") else ""))
        return out

    # -- manifest and digest
    def _finish(self, names, subset, summaries, prev, args, ctx, outcomes, log, started, run, recs, bound, left, pre=()):
        cfg, live, run_dir = self.cfg, run["live"], run["run_dir"]
        ok_all = bool(names) and all(outcomes.get(n, {}).get("status") == "ok" for n in names) and not left and not pre
        headline, codes = OrderedDict(), OrderedDict()
        for n in names:
            s = cfg.step(n)
            if outcomes.get(n, {}).get("status") != "ok":
                continue
            if s.headline:
                headline.update(extract_headline(s.headline, lambda f, k=s.folder: ctx.folders[k] / f))
            if s.codes:
                try:
                    codes[s.codes["question"]] = design_codes(s.codes, ctx.folders[s.folder])
                except Exception:  # noqa: BLE001
                    codes[s.codes["question"]] = {}
        base = prev if prev else self.baseline(names)
        wanted = set(headline)
        prev_head = OrderedDict((k, v) for k, v in (prev["headline"] if prev else base["headline"]).items() if k in wanted)
        prev_roster = prev["live_set"]["roster"] if prev else base.get("roster")
        prev_code = prev["code"] if prev else base["code"]
        code_hashes = run["fp"]["code"]
        outside_now = OrderedDict((k, (len(v) if isinstance(v, (list, dict)) else v)) for k, v in (live.get("excluded_not_counted") or {}).items())
        changes = OrderedDict(
            previous=OrderedDict(kind=("refresh" if prev else base["kind"]), folder=(prev["run"]["folder"] if prev else None),
                                 manifest_sha256=(prev or {}).get("_manifest_sha256"), live_sha256=(prev["live_set"]["sha256"] if prev else base.get("live_sha256")),
                                 live_as_of=(prev["live_set"]["as_of"] if prev else base.get("live_as_of"))),
            roster=roster_diff(prev_roster, roster_of(live)) if prev_roster else None,
            outside_D=OrderedDict(previous=(prev["live_set"].get("outside_D") if prev else base.get("outside_D")), now=outside_now),
            headline=[OrderedDict(key=k, previous=a, now=b, status=st) for k, a, b, st in compare_headlines(prev_head, headline)],
            code=[OrderedDict(file=f, previous=prev_code.get(f), now=h) for f, h in code_hashes.items() if prev_code.get(f) not in (None, h)])
        try:
            reproduction = self._reproduction(names, ctx, outcomes) if (not prev and ok_all) else None
        except Exception as e:  # noqa: BLE001  (a report that cannot be made must not cost the manifest of a finished run)
            reproduction = OrderedDict(error=f"{type(e).__name__}: {str(e)[:300]}")
        registry_start = run["registry_start"]
        registry_end = self.hashes(cfg.registry_files)
        drift = [k for k in registry_start if (registry_start.get(k) or {}).get("sha256") != (registry_end.get(k) or {}).get("sha256")]
        manifest = OrderedDict(
            schema_version=SCHEMA, status="complete" if ok_all else "partial",
            run=OrderedDict(date=run["date"], run_id=run["run_id"], folder=rel_to(self.root, run_dir), started_utc=started, finished_utc=utc_now(), subset=subset, steps=names,
                            resumed=bool(args.resume), command=" ".join(sys.argv[1:]), workers=ctx.workers, python=sys.version.split()[0]),
            live_set=OrderedDict(path=rel_to(self.root, ctx.live_path), sha256=ctx.live_sha, as_of=live["as_of"], D=len(live["designs"]),
                                 estimated=live["counts"].get("estimated"), not_evaluable=live["counts"].get("not_evaluable"),
                                 outside_D=outside_now,
                                 build_seconds=run.get("live_seconds"), material_fingerprint=run["fp"]["roster"], roster=roster_of(live)),
            fingerprint=run["fp"],
            conventions=OrderedDict(ok=True, groups=summaries, records=recs),
            code=OrderedDict(code_hashes), orchestrator=OrderedDict((k, (v or {}).get("sha256")) for k, v in self.hashes(cfg.orchestrator_files).items()),
            inputs=self.hashes(cfg.static_inputs),
            l2_registry=OrderedDict(at_start=registry_start, at_end=registry_end, changed_during_run=drift),
            binding=bound, unbound_references=left, preflight_problems=list(pre),
            steps=[outcomes[n] for n in names if n in outcomes],
            n_per_question=OrderedDict((q, headline.get(k)) for q, k in N_PER_QUESTION.items() if k in headline),
            headline=headline, changes_vs_previous=changes, reproduction_vs_existing_versions=reproduction,
            design_codes=codes, needs_input=self._needs_input(headline, codes),
            audit=OrderedDict(l2_files_written=0, network=False, downloads=0, files_deleted=0))
        if ok_all:
            write_new(run_dir / "REFRESH_MANIFEST.json", dumps(manifest))
        else:
            k = 1
            while (run_dir / f"REFRESH_PARTIAL_{k}.json").exists():
                k += 1
            write_new(run_dir / f"REFRESH_PARTIAL_{k}.json", dumps(manifest))
        log.add("run_end", status=manifest["status"], failed=[n for n in names if outcomes.get(n, {}).get("status") != "ok"])
        digest = None
        if ok_all and not args.no_digest:
            try:
                digest = self._write_digest(manifest, run["date"], run["run_id"])
            except Exception as e:  # noqa: BLE001  (the manifest is already written; the digest can be rendered again from it)
                self.out(f"digest not written: {type(e).__name__}: {e}")
        self.out(f"{manifest['status']}: {rel_to(self.root, run_dir)}" + (f"; digest {rel_to(self.root, digest)}" if digest else ""))
        for n in names:
            o = outcomes.get(n, {})
            if o.get("status") != "ok":
                self.out(f"  FAILED {n}: {o.get('message')}")
        RunLog(self.refresh_dir / "CHECKS.jsonl").add("run", folder=rel_to(self.root, run_dir), status=manifest["status"])
        return EXIT_OK if ok_all else EXIT_FAILED

    def _reproduction(self, names, ctx, outcomes):
        """First refresh: are the new results the existing ones?  Result files are compared field by field, sweep files array by array."""
        out = OrderedDict()
        for n in names:
            s = self.cfg.step(n)
            if outcomes.get(n, {}).get("status") != "ok" or not s.baseline:
                continue
            base = self.root / s.baseline
            for f in s.files:
                if not f.endswith(".json") or f == "l2_set_live_used.json" or f.startswith("SERIES_MANIFEST") or f == "SWEEP_MANIFEST.json":
                    continue
                old, new = base / f, ctx.folders[s.folder] / f
                if not old.is_file() or not new.is_file():
                    continue
                a, b = read_json(old), read_json(new)
                nd, ex, mx = deep_diff(a, b)
                exp, code, other = stamp_differences(a, b)
                out[f"{base.name}/{f}"] = OrderedDict(identical=(nd == 0 and not other), n_differences=nd, examples=ex, max_relative_numeric_difference_within_tolerance=mx,
                                                      stamp_differences_provenance=exp, stamp_differences_code=code, stamp_differences_other=other,
                                                      provenance_differences=provenance_differences(a, b))
        if outcomes.get("sweep_sweep", {}).get("status") == "ok" and "sweep" in ctx.folders:
            base, new = self.root / "data/l3_meta/sweep_v1", ctx.folders["sweep"]
            worst, bad, n = 0.0, [], 0
            for p in sorted(new.glob("*.npz")):
                if p.name.startswith("series_store") or not (base / p.name).is_file():
                    continue
                w, b = compare_npz(base / p.name, p)
                worst, n = max(worst, w), n + 1
                bad += [f"{p.name}:{x}" for x in b]
            out["sweep_v1/<design>.npz"] = OrderedDict(identical=(not bad and n > 0), files_compared=n, max_relative_difference=worst, arrays_differing=bad[:25])
            for f in ("series_store_v1.npz", "series_store_v1_count.npz"):
                if (base / f).is_file() and (new / f).is_file():
                    w, b = compare_npz(base / f, new / f)
                    out[f"sweep_v1/{f}"] = OrderedDict(identical=not b, max_relative_difference=w, arrays_differing=b[:25])
        return out

    def _needs_input(self, headline, codes):
        """Designs the registered rules cannot include yet because a frozen input is missing (a human step supplies it)."""
        out = []
        miss = sorted(d for d, c in (codes.get("N1") or {}).items() if c == "OTHER:design_not_in_sets_file")
        if miss:
            out.append(OrderedDict(question="N1", what="no N1 geography sets for these designs (build_n1_sets.py, then a new dated N1 record)", designs=miss))
        for q in ("D1", "D2", "D3"):
            other = sorted(d for d, c in (codes.get(q) or {}).items() if c == "OTHER")
            if other:
                out.append(OrderedDict(question=q, what="coded OTHER (not gated, swept or run)", designs=other))
        if headline.get("Q3.three_slot_warning"):
            out.append(OrderedDict(question="Q3-3 / N10-3", what=headline["Q3.three_slot_warning"]))
        if (headline.get("Q3.three_slot_designs") or 0) > 0:
            out.append(OrderedDict(question="Q3", what=f"{headline['Q3.three_slot_designs']} estimated designs carry a three-slot forecast set that the five-slot rule cannot score; "
                                                       "the labelled three-slot variants are not implemented"))
        if (headline.get("D2.cells_group_not_coded") or 0) > 0:
            out.append(OrderedDict(question="D2", what="cells whose policy group is not in the coded 35 x 16 grid", cells=headline.get("D2.cells_group_not_coded")))
        return out

    def _write_digest(self, manifest, date, run_id):
        d = self.root / self.cfg.digest_rel
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"REFRESH_{date}.md"
        if path.exists():
            path = d / f"REFRESH_{date}_{run_id}.md"
        write_new(path, render_digest(manifest))
        return path


# ---------------------------------------------------------------------------------------------
# digest
# ---------------------------------------------------------------------------------------------
def pretty_lines(m):
    """[(label, key of its n, text)] for the questions of the L3 synthesis."""
    h = m["headline"].get
    ci = lambda k: h(k) or [None, None]  # noqa: E731
    out = []
    if h("Q1.n") is not None:
        out.append(("Q1 noise share", "Q1", f"{fmt(h('Q1.k'))} of {fmt(h('Q1.n'))} estimates beyond their own noise line ({pct(h('Q1.rate'))}%); {fmt(h('Q1.expected_5pct'), 1)} expected at 5%"))
    if h("Q3.n") is not None:
        out.append(("Q3 AI forecasts", "Q3", f"median forecast has the right sign in {fmt(h('Q3.median_hits'))} of {fmt(h('Q3.n'))} designs; 80% intervals hold the estimate "
                                              f"{pct(h('Q3.coverage80'), 0)}% of the time; Brier skill rests on {fmt(h('Q3.skill_S_n'))} designs"))
    if h("Q5.D") is not None:
        out.append(("Q5 where estimation stops", "Q5", f"{fmt(h('Q5.estimated'))} of {fmt(h('Q5.D'))} designs estimated; automatic loop {fmt(h('Q5.loop_estimated'))} of {fmt(h('Q5.loop_D'))}"))
    if h("N10.n") is not None:
        out.append(("N10 forecast disagreement", "N10", f"{fmt(h('N10.n'))} designs in {fmt(h('N10.F'))} families"))
    if h("N1.n") is not None:
        out.append(("N1 adopters vs free states", "N1", f"mean Z {fmt(h('N1.theta1'))} (95% {fmt(ci('N1.interval95')[0])} to {fmt(ci('N1.interval95')[1])}); "
                                                          f"{fmt(h('N1.p_le_05'))} designs at p <= .05, {fmt(h('N1.expected_p_le_05'), 1)} expected"))
    if h("SWEEP.designs_gated") is not None:
        out.append(("Outcome sweep", "SWEEP", f"reproduction gate: {fmt(h('SWEEP.gate_pass'))} of {fmt(h('SWEEP.designs_gated'))} designs pass; {fmt(h('SWEEP.swept'))} swept over {fmt(h('SWEEP.series'))} series"))
    if h("D1.n") is not None:
        out.append(("D1 breadth of outcomes", "D1", f"mean Z {fmt(h('D1.theta'))}; {fmt(h('D1.families_beyond'))} outcome families beyond the line, {fmt(h('D1.families_expected'), 1)} expected"))
    if h("D2.m") is not None:
        out.append(("D2 negative controls", "D2", f"{fmt(h('D2.n_beyond'))} of {fmt(h('D2.m'))} cells beyond the line ({pct(h('D2.pi_NC'))}%); excess {pct(h('D2.delta'))} points; "
                                                   f"spread ratio {fmt(h('D2.width_ratio'))}"))
    if h("D3.n") is not None:
        out.append(("D3 substitution", "D3", f"mean Z {fmt(h('D3.theta'))}; {fmt(h('D3.signals'))} substitution signals"))
    if h("CAL.kappa") is not None:
        out.append(("CAL calibration", "CAL", f"widening factor {fmt(h('CAL.kappa'))} ({fmt(ci('CAL.kappa_interval95')[0])} to {fmt(ci('CAL.kappa_interval95')[1])}); "
                                               f"Q1 {fmt(h('CAL.Q1_k_uncalibrated'))} -> {fmt(h('CAL.Q1_k_calibrated'))} of {fmt(h('CAL.Q1_n'))} ({fmt(h('CAL.Q1_expected'), 1)} expected); "
                                               f"D1 families {fmt(h('CAL.D1_families_uncalibrated'))} -> {fmt(h('CAL.D1_families_calibrated'))} ({fmt(h('CAL.D1_families_expected'), 1)} expected)"))
    return out


ZH_NAMES = {"ex1": "EX-1", "n1": "N1", "sweep": "扫描含复现关", "d1": "D1", "d3_v1": "D3-V1", "d2": "D2", "d3_v2": "D3-V2", "cal": "校准"}


def render_digest(m):
    run, ls, ch = m["run"], m["live_set"], m["changes_vs_previous"]
    steps = m["steps"]
    rows = ch["headline"]
    n_same = sum(1 for r in rows if r["status"] == "same")
    changed = [r for r in rows if r["status"] != "same"]
    ros = ch.get("roster") or {}
    n_added, n_removed = len(ros.get("added", [])), len(ros.get("removed", []))
    first = ch["previous"]["kind"] != "refresh"
    base_asof = (ch["previous"].get("live_as_of") or "")[:10]
    prev_en = f"the existing versions (live set of {base_asof})" if first else f"refresh {ch['previous']['folder']}"
    prev_zh = f" {base_asof} 已有的结果" if first else f"上一次刷新（{ch['previous']['folder']}）"
    keys = list(OrderedDict.fromkeys(s.get("key") or s["step"] for s in steps))
    ok_keys = [k for k in keys if all(s["status"] == "ok" for s in steps if (s.get("key") or s["step"]) == k)]
    names_zh = "、".join(ZH_NAMES.get(k, k) for k in keys)
    L = [f"# L3 refresh {run['date']} ({run['run_id']})", "", "## 给主人（中文摘要）",
         f"1. 一键重跑 {len(ok_keys)}/{len(keys)} 项完成（{names_zh}），全部用同一份新的 L2 名单：{ls['D']} 个设计，{ls['estimated']} 个出估计，{ls['not_evaluable']} 个未评估，截至 {ls['as_of']}。",
         "2. 登记约定：每个构建器的修正文件、冻结文件、固定的 L2 模块和编码输入，哈希都和记录一致。",
         f"3. 和{prev_zh}相比：{n_same} 个数字完全一致" + (f"，{len(changed)} 个不同（见下）。" if changed else "，没有变化。"),
         (f"4. L2 名单：新增 {n_added} 个设计，去掉 {n_removed} 个。" if ros else "4. L2 名单：没有上次名单可比。")]
    if m.get("needs_input"):
        L.append("5. 有设计缺冻结输入，规则暂时算不进去：" + "；".join(f"{x['question']}：{x['what']}" for x in m["needs_input"]) + "。")
    L += ["", "## Run", "",
          f"- Folder `{run['folder']}`; live set `{ls['path']}` as of {ls['as_of']}: D = {ls['D']} ({ls['estimated']} estimated, {ls['not_evaluable']} not evaluable), sha256 `{ls['sha256'][:16]}`.",
          f"- Conventions: {len(m['conventions']['groups'])} registered groups checked before anything was computed; every hash matches its record.",
          f"- Compared with {prev_en}. Manifest: `{run['folder']}/REFRESH_MANIFEST.json`.", "",
          "| Step | Status | Seconds |", "|---|---|---|", f"| Live L2 set (read-only on L2) | ok | {ls.get('build_seconds', '-')} |"]
    for s in steps:
        L.append(f"| {s.get('label', s['step'])} | {s['status']} | {s.get('seconds', '-')} |")
    L += ["", "## Numbers", "", "| Question | Result | n |", "|---|---|---|"]
    pl = pretty_lines(m)
    if pl:
        for label, qkey, text in pl:
            L.append(f"| {label} | {text} | {fmt(m['n_per_question'].get(qkey))} |")
    else:
        for k, v in m["headline"].items():
            L.append(f"| {k} | {fmt(v, 4)} | |")
    L += ["", "## Changes", ""]
    if not changed:
        L.append(f"No headline number differs from {prev_en}.")
    else:
        L += ["| Number | Before | Now |", "|---|---|---|"] + [f"| {r['key']} | {fmt(r['previous'], 4)} | {fmt(r['now'], 4)} |" for r in changed[:40]]
        if len(changed) > 40:
            L.append(f"... and {len(changed) - 40} more in the manifest.")
    if ros and (n_added or n_removed or ros.get("status_changed") or ros.get("estimate_changed")):
        L += ["", f"Roster: +{n_added} / -{n_removed} designs, {len(ros['status_changed'])} status changes, {len(ros['estimate_changed'])} estimate changes."]
        if n_added:
            L.append("Added: " + ", ".join(f"`{d}`" for d in ros["added"][:30]) + ".")
        for x in ros["status_changed"][:20]:
            L.append(f"- `{x['design_id']}`: {x['from'][0]} {x['from'][1] or ''} -> {x['to'][0]} {x['to'][1] or ''}")
    out = ch.get("outside_D") or {}
    if isinstance(out.get("previous"), dict) and isinstance(out.get("now"), dict) and out["previous"] != out["now"]:
        L += ["", "Not yet counted (outside D): " + ", ".join(f"{k} {out['previous'].get(k, 0)} -> {v}" for k, v in out["now"].items() if out["previous"].get(k, 0) != v) + "."]
    rep = m.get("reproduction_vs_existing_versions")
    if rep and isinstance(rep.get("error"), str):
        L += ["", "Reproduction check not made: " + rep["error"]]
    elif rep:
        same = [k for k, v in rep.items() if v.get("identical")]
        diff = [k for k, v in rep.items() if not v.get("identical")]
        codes = sorted({c for v in rep.values() for c in (v.get("provenance_differences") or []) if c.startswith("code:")}
                       | {f"{k}" for v in rep.values() for k in (v.get("stamp_differences_code") or [])})
        L += ["", f"Reproduction: {len(same)} of {len(rep)} result files equal the existing versions field by field (clocks, hashes, paths and code hashes aside)"
              + (f"; different: {', '.join(diff)}." if diff else ".")]
        if codes:
            L.append("Code hashes that differ from those recorded in the existing files: " + ", ".join(codes) + ".")
    if m.get("needs_input"):
        L += ["", "## Needs input", ""]
        for x in m["needs_input"]:
            tail = f" ({len(x['designs'])}: " + ", ".join(f"`{d}`" for d in x["designs"][:12]) + ")" if x.get("designs") else f" ({x.get('cells')} cells)"
            L.append(f"- {x['question']}: {x['what']}{tail}")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="check the conventions, read the live set in memory, print the plan; write nothing")
    ap.add_argument("--force", action="store_true", help="build a new version even when nothing changed since the previous refresh")
    ap.add_argument("--steps", default=None, help="comma-separated step or folder names (default all)")
    ap.add_argument("--workers", type=int, default=4, help="processes of the sweep stages (default 4, at most 6)")
    ap.add_argument("--date", default=None, help="YYYYMMDD of the run folder (default today, local time)")
    ap.add_argument("--resume", default=None, help="continue an unfinished run folder: finished steps are kept, the others run again in new folders")
    ap.add_argument("--no-digest", action="store_true", help="do not write the Markdown digest")
    ap.add_argument("--root", default=str(ROOT), help="repository root (tests)")
    ap.add_argument("--refresh-dir", default=REFRESH_REL, help="where the run folders go (default data/l3_meta/refresh; a scratch folder for trial runs)")
    ap.add_argument("--digest-dir", default=DIGEST_REL, help="where REFRESH_<date>.md goes (default docs/plans/l3-meta)")
    ap.add_argument("--list-steps", action="store_true")
    return ap


def main(argv=None, cfg=None, out=print):
    args = build_parser().parse_args(argv)
    if args.date and not re.match(r"^\d{8}$", args.date):
        out("--date must be YYYYMMDD")
        return EXIT_USAGE
    cfg = cfg or Config(root=Path(args.root), refresh_rel=args.refresh_dir, digest_rel=args.digest_dir)
    if args.list_steps:
        for s in cfg.steps:
            out(f"{s.name:<20} folder {s.folder:<8} needs {', '.join(s.needs) or '-'}")
        return EXIT_OK
    return Refresher(cfg, out=out).run(args)


if __name__ == "__main__":
    import refresh_all as _self  # the pickled references of the sweep workers must point to this module, not to __main__
    sys.exit(_self.main())
