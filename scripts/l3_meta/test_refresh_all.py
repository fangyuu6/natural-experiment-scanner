"""Checks of refresh_all.py on INVENTED data (a throwaway repository in a temporary folder; no repo data file is read, no real builder runs).

  * hash records: parsing of sections, the amendment pair, the D-question entries, supplements; the gate passes on a consistent record and refuses
    a changed amendment, a changed frozen file, a changed strict input, a changed question entry, a missing supplement; a changed SNAPSHOT line is
    not a convention;
  * refusal: a refused refresh computes nothing and creates no run folder; a hash record edited after the previous refresh is refused;
  * order and folders: dependency order, a blocked step when its upstream failed, independent steps still run, a new folder per run (r1, r2),
    nothing overwritten, a folder from an earlier run is never reused;
  * --dry-run writes nothing; "no change, no new version"; --force; a changed roster / registry / code starts a new version;
  * provenance: a result made with another live set or without this run's upstream file fails its step;
  * manifest: inputs' hashes, n per question, headline numbers, changes against the previous refresh and against the existing versions on the first
    run (reproduction check), roster diff, needs-input list; the digest (name collision, Chinese summary, numbers);
  * resume: finished steps are kept, the others run again in new folders;
  * the lock, the rebinding of builder modules (constants, default arguments, effective record parser, undo, unbound scan, worker initializer),
    the real step plan (arguments of every builder call, with the builders stubbed), deep_diff / compare_npz / roster_diff / same_value.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/test_refresh_all.py
"""
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import refresh_all as R  # noqa: E402  (importing reads no data file)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write(p, text):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def design(did, status="estimated", est=None, gate=None, family=None):
    return {"design_id": did, "status": status, "gate": gate, "source_group": "registry_terminal", "policy_family": family or did, "point_estimate": est}


QUESTION_TEXT = "# D questions\n\n## D1. first\ntext one\n\n## D2. second\ntext two\n\n## D3. third\ntext three\n"


class Env:
    """An invented repository, an invented live set that tests can change, and invented builders (steps a, b, c)."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        self.designs = [design("d1", est=0.5), design("d2", est=-0.2), design("d3", "not_evaluable", gate="support")]
        self.calls, self.fail, self.stale, self.clock = [], set(), set(), 0
        docs = self.root / "docs/plans/l3-meta"
        write(docs / "AMEND_A.md", "amendment A\n")
        write(docs / "FROZEN.md", "frozen text\n")
        write(docs / "INPUT_STRICT.csv", "a,b\n1,2\n")
        write(docs / "QUESTIONS.md", QUESTION_TEXT)
        write(docs / "AMEND_Q.md", "amendment Q\n")
        write(self.root / "data/l3_meta/l2_set_live.json", json.dumps(self.live()))              # clock 1: one in-flight design
        write(self.root / "data/l2_pipeline/registry/TERMINAL_RUNS.json", '{"runs": []}')
        write(self.root / "scripts/l3_meta/fake_builder.py", "# invented code\n")
        write(self.root / "scripts/l3_meta/refresh_all.py", "# invented orchestrator\n")
        write(self.root / "data/l3_meta/static_input.csv", "x\n1\n")
        self.write_records()
        # the "existing version" of step a (what the first refresh is compared with)
        base = self.root / "data/l3_meta/a_v1"
        base.mkdir(parents=True)
        (base / "RESULT_A.json").write_text(json.dumps(self.result_a(sha(self.root / "data/l3_meta/l2_set_live.json"), self.designs)), encoding="utf-8")

    @staticmethod
    def snapshot(path):
        return R.is_snapshot(path) or str(path).startswith("data/l3_meta/a_v1/")

    # -- records
    def write_records(self):
        d = self.root / "docs/plans/l3-meta"
        live_sha = sha(self.root / "data/l3_meta/l2_set_live.json")
        write(d / "G1_HASH.txt", "\n".join([
            "Invented record G1", "file: docs/plans/l3-meta/AMEND_A.md", f"sha256: {sha(d / 'AMEND_A.md')}", "bytes: 12",
            "amended_utc (T_amend): 2026-10-15T00:00:00Z",
            "frozen files this amendment refers to (read-only, unchanged, re-verified at T_amend):",
            f"  docs/plans/l3-meta/FROZEN.md sha256 {sha(d / 'FROZEN.md')}",
            "inputs at amendment (read-only):",
            f"  docs/plans/l3-meta/INPUT_STRICT.csv sha256 {sha(d / 'INPUT_STRICT.csv')}",
            f"  data/l3_meta/l2_set_live.json sha256 {live_sha} (as_of 2026-10-15T00:00:00Z; a snapshot)",
            f"  data/l3_meta/a_v1/RESULT_A.json sha256 {'0' * 64}",
            "note: invented", ""]))
        entries = {t: R.entry_sha256(QUESTION_TEXT, t) for t in ("D1", "D2", "D3")}
        write(d / "GQ_HASH.txt", "\n".join([
            "Invented record GQ", "file: docs/plans/l3-meta/AMEND_Q.md", f"sha256: {sha(d / 'AMEND_Q.md')}", "bytes: 12",
            "entry sha256 (rule: text of the entry up to the next heading):"] + [f"  {t} {h}" for t, h in entries.items()] + [
            "amended_utc (T_amend): 2026-10-15T00:00:00Z", ""]))
        write(d / "GQ_HASH_S1.txt", "\n".join(["Supplement", "files added (read-only from here on):", f"  docs/plans/l3-meta/FROZEN.md sha256 {sha(d / 'FROZEN.md')}", ""]))

    def groups(self):
        return OrderedDict([
            ("g1", R.ConvGroup("g1", "docs/plans/l3-meta/G1_HASH.txt", inputs=True)),
            ("gq", R.ConvGroup("gq", "docs/plans/l3-meta/GQ_HASH.txt", inputs=False, entries=("docs/plans/l3-meta/QUESTIONS.md", ("D1", "D2", "D3")),
                               required=["GQ_HASH_S1.txt"])),
        ])

    # -- live set
    def live(self):
        self.clock += 1
        counts = {"processed": len(self.designs), "estimated": sum(1 for d in self.designs if d["status"] == "estimated"),
                  "not_evaluable": sum(1 for d in self.designs if d["status"] != "estimated")}
        return {"as_of": f"2026-10-15T00:00:{self.clock:02d}Z", "counts": counts, "designs": [dict(d) for d in self.designs],
                "excluded_not_counted": {"in_flight": ["x"] * self.clock}}

    def write_live(self, payload, out_dir):
        p = Path(out_dir) / "l2_set_live.json"
        write(p, json.dumps(payload))
        return p

    # -- invented builders
    def result_a(self, live_sha, designs):
        est = [d for d in designs if d["status"] == "estimated"]
        return {"stamp": {"live_set_sha256": live_sha, "D": len(designs), "run_utc": "2026-10-15T00:00:00Z", "code_sha256": "c" * 64},
                "question": {"A": {"n": len(est), "value": 0.5, "interval95": [0.1, 0.9]}},
                "designs": [{"design_id": d["design_id"], "code": "EVAL" if d["status"] == "estimated" else "STOP_PRE_RUN"} for d in designs]}

    def make_run(self, key, fname, upstream=None):
        def run(ctx):
            self.calls.append(key)
            if key in self.fail:
                raise R.StepFailed(f"{key} refused")
            folder = ctx.dir(key)
            live_sha = "f" * 64 if key in self.stale else ctx.live_sha
            res = self.result_a(live_sha, ctx.live["designs"])
            if upstream:
                res["stamp"]["upstream_sha256"] = sha(ctx.file(upstream, "RESULT_A.json"))
            (folder / fname).write_text(json.dumps(res), encoding="utf-8")
            return {}
        return run

    def steps(self):
        prov = dict(file="RESULT_A.json", live_sha="stamp.live_set_sha256", D="stamp.D", roster="designs")
        head = [("A.n", "RESULT_A.json", "question.A.n"), ("A.value", "RESULT_A.json", "question.A.value"), ("A.interval95", "RESULT_A.json", "question.A.interval95")]
        files = ("RESULT_A.json",)
        codes = dict(file="RESULT_A.json", question="A", list="designs", id="design_id", key="code")
        return [
            R.Step("a", "a", ("live",), ("g1",), self.make_run("a", "RESULT_A.json"), files, head, prov, baseline="data/l3_meta/a_v1", label="Step A", codes=codes),
            R.Step("b", "b", ("a",), ("g1", "gq"), self.make_run("b", "RESULT_A.json", upstream="a"), files,
                   [("B.n", "RESULT_A.json", "question.A.n")], dict(prov, upstream={"stamp.upstream_sha256": ("a", "RESULT_A.json")}), label="Step B"),
            R.Step("c", "c", ("live",), ("g1",), self.make_run("c", "RESULT_A.json"), files, [("C.n", "RESULT_A.json", "question.A.n")], prov, label="Step C"),
        ]

    def cfg(self, **kw):
        base = dict(root=self.root, steps=self.steps(), groups=self.groups(), code_files=["scripts/l3_meta/fake_builder.py"],
                    static_inputs=["data/l3_meta/static_input.csv"], registry_files=["data/l2_pipeline/registry/TERMINAL_RUNS.json"],
                    live_preview=self.live, live_write=self.write_live, binding_factory=None, today=lambda: "20261015", snapshot_fn=self.snapshot,
                    fingerprint_registry=("data/l2_pipeline/registry/TERMINAL_RUNS.json",), orchestrator_files=["scripts/l3_meta/refresh_all.py"])
        base.update(kw)
        return R.Config(**base)

    def run(self, *argv, **cfgkw):
        lines = []
        rc = R.main(list(argv), cfg=self.cfg(**cfgkw), out=lines.append)
        return rc, lines

    def manifest(self, run="20261015_r1"):
        return json.loads((self.root / "data/l3_meta/refresh" / run / "REFRESH_MANIFEST.json").read_text(encoding="utf-8"))

    def tree(self):
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(self.root))] = sha(p)
        return out


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l3_refresh_test_")      # left to the operating system (the repository rule is: nothing is deleted)
        self.env = Env(self.tmp)


# ---------------------------------------------------------------------------------------------
class Records(Case):
    def test_parse_record_sections_and_pairs(self):
        rec = R.parse_record(self.env.root / "docs/plans/l3-meta/G1_HASH.txt")
        self.assertEqual(list(rec["files"]), ["docs/plans/l3-meta/AMEND_A.md"])
        kinds = {R.section_kind(k): list(v) for k, v in rec["sections"].items()}
        self.assertEqual(kinds["frozen"], ["docs/plans/l3-meta/FROZEN.md"])
        self.assertIn("data/l3_meta/l2_set_live.json", kinds["inputs"])
        self.assertEqual(rec["t_amend_utc"], "2026-10-15T00:00:00Z")

    def test_entries_and_supplement_are_read(self):
        g = self.env.groups()["gq"]
        problems, summary = R.check_group(self.env.root, g, self.env.snapshot)
        self.assertEqual(problems, [])
        self.assertEqual(sorted(summary["entries"]), ["D1", "D2", "D3"])
        self.assertEqual(len(summary["records"]), 2)                       # the record and its supplement S1
        self.assertEqual(summary["added"], 1)

    def test_gate_passes_on_a_consistent_record(self):
        problems, summaries = R.check_conventions(self.env.root, ["g1", "gq"], self.env.groups(), self.env.snapshot)
        self.assertEqual(problems, [])
        self.assertEqual(summaries["g1"]["inputs"], 1)                      # INPUT_STRICT only; the live set and the a_v1 line are snapshots
        self.assertEqual(len(summaries["g1"]["snapshot_lines_exempt"]), 2)

    def test_changed_amendment_is_refused(self):
        write(self.env.root / "docs/plans/l3-meta/AMEND_A.md", "amendment A, edited\n")
        problems, _ = R.check_conventions(self.env.root, ["g1"], self.env.groups(), self.env.snapshot)
        self.assertTrue(any("AMEND_A.md" in p and "differs" in p for p in problems))

    def test_changed_frozen_file_and_strict_input_are_refused(self):
        write(self.env.root / "docs/plans/l3-meta/FROZEN.md", "changed\n")
        write(self.env.root / "docs/plans/l3-meta/INPUT_STRICT.csv", "a,b\n9,9\n")
        problems, _ = R.check_conventions(self.env.root, ["g1"], self.env.groups(), self.env.snapshot)
        self.assertTrue(any("FROZEN.md" in p for p in problems))
        self.assertTrue(any("INPUT_STRICT.csv" in p for p in problems))

    def test_snapshot_lines_are_not_conventions(self):
        write(self.env.root / "data/l3_meta/l2_set_live.json", '{"designs": []}')
        (self.env.root / "data/l3_meta/a_v1/RESULT_A.json").write_text("{}", encoding="utf-8")
        problems, _ = R.check_conventions(self.env.root, ["g1"], self.env.groups(), self.env.snapshot)
        self.assertEqual(problems, [])

    def test_changed_pinned_l2_module_gets_one_explanatory_line(self):
        d = self.env.root / "docs/plans/l3-meta"
        mod = self.env.root / "scripts/l2_pipeline/engine/x.py"
        write(mod, "a = 1\n")
        write(d / "GL2_HASH.txt", "\n".join(["Invented record GL2", "file: docs/plans/l3-meta/AMEND_A.md", f"sha256: {sha(d / 'AMEND_A.md')}",
                                             "L2 modules (section 2.2):", f"  scripts/l2_pipeline/engine/x.py sha256 {sha(mod)}", ""]))
        g = R.ConvGroup("gl2", "docs/plans/l3-meta/GL2_HASH.txt")
        self.assertEqual(R.check_group(self.env.root, g, self.env.snapshot)[0], [])
        write(mod, "a = 2\n")
        problems, summary = R.check_group(self.env.root, g, self.env.snapshot)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("x.py", problems[0])
        self.assertIn("1 pinned L2 module(s) changed since the record", problems[1])
        self.assertIn("numbered supplement", problems[1])

    def test_changed_question_entry_is_refused(self):
        write(self.env.root / "docs/plans/l3-meta/QUESTIONS.md", QUESTION_TEXT.replace("text two", "text 2 changed"))
        problems, _ = R.check_conventions(self.env.root, ["gq"], self.env.groups(), self.env.snapshot)
        self.assertTrue(any("entry D2" in p for p in problems))
        self.assertFalse(any("entry D1" in p for p in problems))

    def test_missing_supplement_is_refused(self):
        (self.env.root / "docs/plans/l3-meta/GQ_HASH_S1.txt").rename(self.env.root / "docs/plans/l3-meta/GQ_HASH_X.txt")
        problems, _ = R.check_conventions(self.env.root, ["gq"], self.env.groups(), self.env.snapshot)
        self.assertTrue(any("supplement GQ_HASH_S1.txt is missing" in p for p in problems))

    def test_explicit_input_list_checks_only_those_paths(self):
        g = R.ConvGroup("g1", "docs/plans/l3-meta/G1_HASH.txt", inputs=["docs/plans/l3-meta/INPUT_STRICT.csv"])
        write(self.env.root / "docs/plans/l3-meta/FROZEN.md", "x")
        problems, _ = R.check_group(self.env.root, g, self.env.snapshot)
        self.assertEqual([p for p in problems if "INPUT" in p], [])
        g2 = R.ConvGroup("g1", "docs/plans/l3-meta/G1_HASH.txt", inputs=["docs/plans/l3-meta/NOT_LISTED.csv"])
        problems, _ = R.check_group(self.env.root, g2, self.env.snapshot)
        self.assertTrue(any("not listed in the record" in p for p in problems))

    def test_entry_hash_follows_the_heading_rule(self):
        t = "## D1. a\nx\n## D2. b\ny\n"
        self.assertEqual(R.entry_sha256(t, "D1"), hashlib.sha256("## D1. a\nx\n".encode()).hexdigest())
        self.assertEqual(R.entry_sha256(t, "D2"), hashlib.sha256("## D2. b\ny\n".encode()).hexdigest())
        self.assertIsNone(R.entry_sha256(t, "D3"))


# ---------------------------------------------------------------------------------------------
class Orchestration(Case):
    def test_full_run_order_folders_manifest_digest(self):
        rc, lines = self.env.run()
        self.assertEqual(rc, 0, lines)
        self.assertEqual(self.env.calls, ["a", "b", "c"])
        run = self.env.root / "data/l3_meta/refresh/20261015_r1"
        for sub in ("a", "b", "c", "live"):
            self.assertTrue((run / sub).is_dir())
        m = self.env.manifest()
        self.assertEqual(m["status"], "complete")
        self.assertEqual(m["run"]["folder"], "data/l3_meta/refresh/20261015_r1")
        self.assertEqual(m["live_set"]["D"], 3)
        self.assertEqual(m["live_set"]["sha256"], sha(run / "live/l2_set_live.json"))
        self.assertEqual(m["n_per_question"], {})                          # the invented headline keys are not real questions
        self.assertEqual(m["headline"]["A.n"], 2)
        self.assertEqual(m["headline"]["B.n"], 2)
        self.assertIn("data/l3_meta/static_input.csv", m["inputs"])
        self.assertEqual(m["inputs"]["data/l3_meta/static_input.csv"]["sha256"], sha(self.env.root / "data/l3_meta/static_input.csv"))
        self.assertEqual(m["code"]["scripts/l3_meta/fake_builder.py"], sha(self.env.root / "scripts/l3_meta/fake_builder.py"))
        self.assertEqual(m["audit"], {"l2_files_written": 0, "network": False, "downloads": 0, "files_deleted": 0})
        digest = self.env.root / "docs/plans/l3-meta/REFRESH_20261015.md"
        text = digest.read_text(encoding="utf-8")
        self.assertIn("给主人", text)
        self.assertIn("一键重跑 3/3 项完成（a、b、c）", text)
        self.assertIn("| Live L2 set (read-only on L2) | ok |", text)
        self.assertIn("| Step A | ok |", text)
        self.assertIn("A.n", text)
        self.assertEqual(m["live_set"]["build_seconds"] is not None, True)
        self.assertEqual(sorted(m["orchestrator"]), ["scripts/l3_meta/refresh_all.py"])

    def test_listed_steps_are_selectable_by_name_or_folder(self):
        rc, _ = self.env.run("--steps", "a,c", "--no-digest")
        self.assertEqual(rc, 0)
        self.assertEqual(self.env.calls, ["a", "c"])
        m = self.env.manifest()
        self.assertTrue(m["run"]["subset"])

    def test_dependency_blocks_downstream_but_not_independent_steps(self):
        self.env.fail.add("a")
        rc, lines = self.env.run()
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.calls, ["a", "c"])                       # b is blocked, c still runs
        run = self.env.root / "data/l3_meta/refresh/20261015_r1"
        self.assertFalse((run / "REFRESH_MANIFEST.json").exists())
        part = json.loads((run / "REFRESH_PARTIAL_1.json").read_text(encoding="utf-8"))
        self.assertEqual(part["status"], "partial")
        st = {s["step"]: s["status"] for s in part["steps"]}
        self.assertEqual(st, {"a": "failed", "b": "blocked", "c": "ok"})
        self.assertFalse((self.env.root / "docs/plans/l3-meta/REFRESH_20261015.md").exists())      # no digest for an incomplete run
        self.assertTrue(any("FAILED a" in x for x in lines))

    def test_each_run_gets_a_new_folder_and_nothing_is_overwritten(self):
        self.assertEqual(self.env.run()[0], 0)
        before = self.env.tree()
        self.assertEqual(self.env.run("--force")[0], 0)
        after = self.env.tree()
        for k, v in before.items():
            if k.startswith("data/l3_meta/refresh/20261015_r1/") or k == "docs/plans/l3-meta/REFRESH_20261015.md":
                self.assertEqual(after[k], v, k)                          # the first version is byte-identical afterwards
        self.assertTrue((self.env.root / "data/l3_meta/refresh/20261015_r2/REFRESH_MANIFEST.json").is_file())
        self.assertTrue((self.env.root / "docs/plans/l3-meta/REFRESH_20261015_r2.md").is_file())    # the first digest is kept

    def test_an_existing_run_folder_is_never_reused(self):
        stray = self.env.root / "data/l3_meta/refresh/20261015_r1"
        write(stray / "stray.txt", "someone's file")
        rc, _ = self.env.run()
        self.assertEqual(rc, 0)
        self.assertEqual((stray / "stray.txt").read_text(), "someone's file")
        self.assertFalse((stray / "REFRESH_MANIFEST.json").exists())
        self.assertTrue((self.env.root / "data/l3_meta/refresh/20261015_r2/REFRESH_MANIFEST.json").is_file())

    def test_canonical_files_are_never_touched(self):
        before = {k: v for k, v in self.env.tree().items() if not k.startswith(("data/l3_meta/refresh", "docs/plans/l3-meta/REFRESH"))}
        self.env.run()
        after = {k: v for k, v in self.env.tree().items() if not k.startswith(("data/l3_meta/refresh", "docs/plans/l3-meta/REFRESH"))}
        self.assertEqual(before, after)

    def test_dry_run_writes_nothing(self):
        before = self.env.tree()
        rc, lines = self.env.run("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.env.tree(), before)
        self.assertEqual(self.env.calls, [])
        text = "\n".join(lines)
        self.assertIn("nothing is written", text)
        self.assertIn("every hash matches", text)
        self.assertIn("no previous refresh", text)
        self.assertFalse((self.env.root / "data/l3_meta/refresh").exists())

    def test_dry_run_reports_roster_and_no_change(self):
        self.assertEqual(self.env.run()[0], 0)
        rc, lines = self.env.run("--dry-run")
        self.assertEqual(rc, 0)
        self.assertTrue(any("NO CHANGE" in x for x in lines), lines)
        self.env.designs.append(design("d4", est=1.0))
        rc, lines = self.env.run("--dry-run")
        self.assertTrue(any("changed (live roster)" in x for x in lines), lines)
        self.assertTrue(any("+1 designs" in x for x in lines), lines)

    def test_a_live_set_that_cannot_be_built_fails_cleanly(self):
        def boom():
            raise OSError("registry unreadable")
        rc, lines = self.env.run(live_preview=boom)
        self.assertEqual(rc, 1)
        self.assertTrue(any("could not be built" in x and "registry unreadable" in x for x in lines))
        self.assertEqual(self.env.calls, [])
        self.assertFalse(any(p.name.startswith("2026") for p in (self.env.root / "data/l3_meta/refresh").iterdir()))
        rc, lines = self.env.run("--dry-run", live_preview=boom)
        self.assertEqual(rc, 1)

    def test_select_unknown_step_exits(self):
        with self.assertRaises(SystemExit):
            self.env.run("--steps", "nope")


class Refusal(Case):
    def test_a_mismatch_refuses_and_computes_nothing(self):
        write(self.env.root / "docs/plans/l3-meta/AMEND_A.md", "tampered\n")
        rc, lines = self.env.run()
        self.assertEqual(rc, 3)
        self.assertEqual(self.env.calls, [])
        self.assertFalse(any(p.name.startswith("2026") for p in (self.env.root / "data/l3_meta/refresh").iterdir()))
        self.assertTrue(any("REFUSED" in x for x in lines))
        log = (self.env.root / "data/l3_meta/refresh/CHECKS.jsonl").read_text()
        self.assertIn("refused", log)

    def test_dry_run_refuses_too(self):
        write(self.env.root / "docs/plans/l3-meta/FROZEN.md", "tampered\n")
        rc, lines = self.env.run("--dry-run")
        self.assertEqual(rc, 3)
        self.assertTrue(any("FROZEN.md" in x for x in lines))

    def test_only_the_selected_steps_conventions_are_checked(self):
        write(self.env.root / "docs/plans/l3-meta/AMEND_Q.md", "tampered\n")        # belongs to group gq, used by step b only
        self.assertEqual(self.env.run("--steps", "a,c", "--no-digest")[0], 0)
        self.assertEqual(self.env.run("--steps", "b")[0], 3)

    def test_a_record_edited_after_the_previous_refresh_is_refused(self):
        self.assertEqual(self.env.run()[0], 0)
        rec = self.env.root / "docs/plans/l3-meta/G1_HASH.txt"
        rec.write_text(rec.read_text() + "an added line\n", encoding="utf-8")
        rc, lines = self.env.run("--force")
        self.assertEqual(rc, 3)
        self.assertTrue(any("edited after the previous refresh" in x for x in lines), lines)

    def test_the_lock_is_exclusive(self):
        refresh = self.env.root / "data/l3_meta/refresh"
        refresh.mkdir(parents=True)
        with open(refresh / ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc, lines = self.env.run()
            self.assertEqual(rc, 1)
            self.assertTrue(any("lock" in x for x in lines))
        self.assertEqual(self.env.calls, [])


class NoChange(Case):
    def test_no_change_no_new_version(self):
        self.assertEqual(self.env.run()[0], 0)
        n_calls = len(self.env.calls)
        rc, lines = self.env.run()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.env.calls), n_calls)
        self.assertTrue(any("no change since refresh" in x for x in lines))
        self.assertFalse((self.env.root / "data/l3_meta/refresh/20261015_r2").exists())
        self.assertIn("no_change", (self.env.root / "data/l3_meta/refresh/CHECKS.jsonl").read_text())

    def test_force_builds_a_new_version(self):
        self.assertEqual(self.env.run()[0], 0)
        self.assertEqual(self.env.run("--force")[0], 0)
        self.assertTrue((self.env.root / "data/l3_meta/refresh/20261015_r2").is_dir())

    def test_material_changes_start_a_new_version(self):
        self.assertEqual(self.env.run()[0], 0)
        self.env.designs[2] = design("d3", "estimated", est=0.3)                  # a design becomes estimated
        rc, lines = self.env.run()
        self.assertEqual(rc, 0)
        self.assertTrue(any("live roster" in x for x in lines))
        write(self.env.root / "data/l2_pipeline/registry/TERMINAL_RUNS.json", '{"runs": [1]}')
        rc, lines = self.env.run()
        self.assertTrue(any("registry TERMINAL_RUNS.json" in x for x in lines), lines)
        write(self.env.root / "scripts/l3_meta/fake_builder.py", "# changed code\n")
        rc, lines = self.env.run()
        self.assertTrue(any("code fake_builder.py" in x for x in lines), lines)

    def test_timestamps_and_in_flight_designs_are_not_material(self):
        a = self.env.live()
        b = self.env.live()
        self.assertNotEqual(a["as_of"], b["as_of"])
        self.assertNotEqual(a["excluded_not_counted"], b["excluded_not_counted"])
        self.assertEqual(R.material_fingerprint(a), R.material_fingerprint(b))
        c = self.env.live()
        c["designs"][0]["point_estimate"] = 0.51
        self.assertNotEqual(R.material_fingerprint(a), R.material_fingerprint(c))

    def test_editing_the_orchestrator_does_not_start_a_new_version(self):
        self.assertEqual(self.env.run()[0], 0)
        write(self.env.root / "scripts/l3_meta/refresh_all.py", "# the orchestrator was edited\n")
        rc, lines = self.env.run()
        self.assertEqual(rc, 0)
        self.assertTrue(any("no change since refresh" in x for x in lines), lines)

    def test_subset_runs_do_not_count_as_the_previous_refresh(self):
        self.assertEqual(self.env.run("--steps", "a", "--no-digest")[0], 0)
        self.assertIsNone(R.Refresher(self.env.cfg()).previous())
        rc, lines = self.env.run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.env.calls.count("a"), 2)


class Provenance(Case):
    def test_result_made_with_another_live_set_fails_its_step(self):
        self.env.stale.add("a")
        rc, lines = self.env.run()
        self.assertEqual(rc, 1)
        self.assertTrue(any("provenance" in x for x in lines), lines)
        part = json.loads(next((self.env.root / "data/l3_meta/refresh/20261015_r1").glob("REFRESH_PARTIAL_*.json")).read_text())
        a = next(s for s in part["steps"] if s["step"] == "a")
        self.assertEqual(a["status"], "failed")
        self.assertFalse(a["provenance"]["ok"])

    def test_missing_output_fails_its_step(self):
        steps = self.env.steps()
        steps[2].files = ("RESULT_A.json", "NOT_WRITTEN.json")
        rc, lines = self.env.run(steps=steps)
        self.assertEqual(rc, 1)
        self.assertTrue(any("expected output missing" in x for x in lines))

    def test_upstream_file_of_another_run_is_detected(self):
        run = self.env.root / "data/l3_meta/refresh/20261015_r1"
        ctx = R.Ctx(self.env.root, run, run / "live/l2_set_live.json", "s" * 64, {"designs": []})
        write(run / "a/RESULT_A.json", "{}")
        ctx.folders["a"] = run / "a"
        folder = run / "b"
        write(folder / "RESULT_A.json", json.dumps({"stamp": {"live_set_sha256": "s" * 64, "D": 0, "upstream_sha256": "0" * 64}, "designs": []}))
        spec = dict(file="RESULT_A.json", live_sha="stamp.live_set_sha256", D="stamp.D", roster="designs", upstream={"stamp.upstream_sha256": ("a", "RESULT_A.json")})
        problems = R.check_provenance(spec, ctx, folder, [], "s" * 64)
        self.assertTrue(any("does not match this run's RESULT_A.json" in p for p in problems))

    def test_roster_mismatch_is_detected(self):
        run = self.env.root / "x"
        ctx = R.Ctx(self.env.root, run, run / "l.json", "s" * 64, {"designs": []})
        folder = run / "b"
        write(folder / "RESULT_A.json", json.dumps({"stamp": {"live_set_sha256": "s" * 64, "D": 1}, "designs": [{"design_id": "other"}]}))
        spec = dict(file="RESULT_A.json", live_sha="stamp.live_set_sha256", D="stamp.D", roster="designs")
        problems = R.check_provenance(spec, ctx, folder, [{"design_id": "d1"}], "s" * 64)
        self.assertTrue(any("design list differs" in p for p in problems))


class Comparison(Case):
    def test_first_run_is_compared_with_the_existing_version(self):
        self.assertEqual(self.env.run()[0], 0)
        m = self.env.manifest()
        ch = m["changes_vs_previous"]
        self.assertTrue(ch["previous"]["kind"].startswith("existing versions"))
        self.assertIsNone(ch["previous"]["folder"])
        by = {r["key"]: r for r in ch["headline"]}
        self.assertEqual(by["A.n"]["status"], "same")
        self.assertEqual(by["B.n"]["status"], "new")                          # b has no existing version
        rep = m["reproduction_vs_existing_versions"]
        self.assertTrue(rep["a_v1/RESULT_A.json"]["identical"])
        self.assertIn("live_set_sha256", rep["a_v1/RESULT_A.json"]["stamp_differences_provenance"])
        self.assertEqual(rep["a_v1/RESULT_A.json"]["stamp_differences_other"], [])
        self.assertEqual(rep["a_v1/RESULT_A.json"]["stamp_differences_code"], [])
        self.assertEqual(ch["outside_D"]["previous"], {"in_flight": 1})                 # the existing live set had one in-flight design
        self.assertEqual(ch["outside_D"]["now"], {"in_flight": 2})
        self.assertIn("equal the existing versions", (self.env.root / "docs/plans/l3-meta/REFRESH_20261015.md").read_text())

    def test_a_difference_is_reported_with_its_roster_explanation(self):
        self.env.designs.append(design("d4", est=1.0))                          # L2 grew before the first refresh
        self.assertEqual(self.env.run()[0], 0)
        m = self.env.manifest()
        by = {r["key"]: r for r in m["changes_vs_previous"]["headline"]}
        self.assertEqual(by["A.n"]["status"], "changed")
        self.assertEqual((by["A.n"]["previous"], by["A.n"]["now"]), (2, 3))
        self.assertEqual(m["changes_vs_previous"]["roster"]["added"], ["d4"])
        self.assertFalse(m["reproduction_vs_existing_versions"]["a_v1/RESULT_A.json"]["identical"])
        text = (self.env.root / "docs/plans/l3-meta/REFRESH_20261015.md").read_text()
        self.assertIn("`d4`", text)
        self.assertIn("A.n", text)

    def test_second_run_is_compared_with_the_first_refresh(self):
        self.assertEqual(self.env.run()[0], 0)
        self.env.designs[2] = design("d3", "estimated", est=0.3)
        self.assertEqual(self.env.run()[0], 0)
        m = self.env.manifest("20261015_r2")
        ch = m["changes_vs_previous"]
        self.assertEqual(ch["previous"]["kind"], "refresh")
        self.assertEqual(ch["previous"]["folder"], "data/l3_meta/refresh/20261015_r1")
        self.assertEqual(ch["roster"]["status_changed"][0]["design_id"], "d3")
        by = {r["key"]: r for r in ch["headline"]}
        self.assertEqual((by["A.n"]["previous"], by["A.n"]["now"]), (2, 3))
        self.assertIsNone(m["reproduction_vs_existing_versions"])
        self.assertEqual(m["design_codes"]["A"]["d3"], "EVAL")

    def test_code_change_is_listed(self):
        self.assertEqual(self.env.run()[0], 0)
        write(self.env.root / "scripts/l3_meta/fake_builder.py", "# changed\n")
        self.assertEqual(self.env.run()[0], 0)
        ch = self.env.manifest("20261015_r2")["changes_vs_previous"]["code"]
        self.assertEqual([c["file"] for c in ch], ["scripts/l3_meta/fake_builder.py"])

    def test_registry_change_during_the_run_is_noted(self):
        steps = self.env.steps()
        orig = steps[0].run

        def run(ctx):
            write(self.env.root / "data/l2_pipeline/registry/TERMINAL_RUNS.json", '{"runs": ["new"]}')
            return orig(ctx)
        steps[0].run = run
        self.assertEqual(self.env.run(steps=steps)[0], 0)
        self.assertEqual(self.env.manifest()["l2_registry"]["changed_during_run"], ["data/l2_pipeline/registry/TERMINAL_RUNS.json"])

    def test_needs_input_lists_designs_a_frozen_input_cannot_cover(self):
        r = R.Refresher(self.env.cfg())
        out = r._needs_input({"D2.cells_group_not_coded": 3},
                             {"N1": {"x": "EVAL", "y": "OTHER:design_not_in_sets_file"}, "D1": {"z": "OTHER", "w": "EVAL"}, "D2": {}})
        q = {x["question"]: x for x in out}
        self.assertEqual(q["N1"]["designs"], ["y"])
        self.assertEqual(q["D1"]["designs"], ["z"])
        self.assertEqual(q["D2"]["cells"], 3)
        self.assertEqual(r._needs_input({}, {"N1": {"x": "EVAL"}}), [])
        out = r._needs_input({"Q3.three_slot_designs": 2, "Q3.three_slot_warning": "estimated designs with a three-slot lock exist: d9"}, {})
        self.assertEqual([x["question"] for x in out], ["Q3-3 / N10-3", "Q3"])
        self.assertIn("d9", out[0]["what"])


class Resume(Case):
    def test_resume_keeps_finished_steps_and_reruns_the_rest_in_new_folders(self):
        self.env.fail.add("b")
        rc, _ = self.env.run()
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.calls, ["a", "b", "c"])
        run = self.env.root / "data/l3_meta/refresh/20261015_r1"
        a_before = sha(run / "a/RESULT_A.json")
        self.env.fail.clear()
        self.env.calls.clear()
        rc, lines = self.env.run("--resume", str(run))
        self.assertEqual(rc, 0, lines)
        self.assertEqual(self.env.calls, ["b"])                               # a and c are kept
        self.assertEqual(sha(run / "a/RESULT_A.json"), a_before)
        m = self.env.manifest()
        self.assertEqual(m["status"], "complete")
        self.assertTrue(m["run"]["resumed"])
        st = {s["step"]: s for s in m["steps"]}
        self.assertTrue(st["a"]["resumed"] and st["c"]["resumed"] and "resumed" not in st["b"])
        self.assertEqual(st["b"]["folder"], "data/l3_meta/refresh/20261015_r1/b_try2")      # the failed attempt's folder stays as it was
        self.assertTrue((run / "REFRESH_PARTIAL_1.json").is_file())

    def test_resume_refuses_when_the_code_changed(self):
        self.env.fail.add("b")
        self.env.run()
        write(self.env.root / "scripts/l3_meta/fake_builder.py", "# changed\n")
        self.env.fail.clear()
        rc, lines = self.env.run("--resume", "data/l3_meta/refresh/20261015_r1")
        self.assertEqual(rc, 3)
        self.assertTrue(any("code changed" in x for x in lines))

    def test_resume_of_a_complete_run_does_nothing(self):
        self.assertEqual(self.env.run()[0], 0)
        before = self.env.tree()
        self.env.calls.clear()
        rc, lines = self.env.run("--resume", "data/l3_meta/refresh/20261015_r1")
        self.assertEqual(rc, 0)
        self.assertTrue(any("nothing to resume" in x for x in lines))
        self.assertEqual(self.env.calls, [])
        self.assertEqual(self.env.tree(), before)

    def test_resume_of_a_missing_run_is_a_usage_error(self):
        (self.env.root / "data/l3_meta/refresh/20261015_r9").mkdir(parents=True)
        rc, _ = self.env.run("--resume", "data/l3_meta/refresh/20261015_r9")
        self.assertEqual(rc, 2)

    def test_changed_output_of_a_finished_step_is_rerun(self):
        self.env.fail.add("b")
        self.env.run()
        run = self.env.root / "data/l3_meta/refresh/20261015_r1"
        (run / "c/RESULT_A.json").write_text("{}", encoding="utf-8")                   # c's output was damaged
        self.env.fail.clear()
        self.env.calls.clear()
        self.assertEqual(self.env.run("--resume", str(run))[0], 0)
        self.assertEqual(self.env.calls, ["b", "c"])


class FakeBinding:
    """Stands in for Binding in the orchestration: records the order of calls and can report preflight problems."""

    def __init__(self, probe=(), native=(), unbound=()):
        self.events, self._probe, self._native, self._unbound = [], list(probe), list(native), list(unbound)

    def apply(self):
        self.events.append("apply")

    def restore(self):
        self.events.append("restore")

    def unbound(self):
        return list(self._unbound)

    def probe_workers(self):
        self.events.append("probe")
        return list(self._probe)

    def native_record_checks(self):
        self.events.append("native")
        return list(self._native)

    def describe(self):
        return {"fake": True}


class Preflight(Case):
    def run_with(self, binding):
        return self.env.run(binding_factory=lambda cfg, ctx, results: binding)

    def test_binding_is_applied_checked_and_restored_around_the_steps(self):
        fb = FakeBinding()
        rc, _ = self.run_with(fb)
        self.assertEqual(rc, 0)
        self.assertEqual(fb.events, ["apply", "probe", "native", "restore"])
        self.assertEqual(self.env.calls, ["a", "b", "c"])
        self.assertEqual(self.env.manifest()["binding"], {"fake": True})

    def test_worker_probe_problem_blocks_everything_before_any_step(self):
        fb = FakeBinding(probe=["sweep worker sweep_dir is /old, expected /new"])
        rc, lines = self.run_with(fb)
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.calls, [])
        self.assertEqual(fb.events[-1], "restore")
        part = json.loads(next((self.env.root / "data/l3_meta/refresh/20261015_r1").glob("REFRESH_PARTIAL_*.json")).read_text())
        self.assertEqual({s["status"] for s in part["steps"]}, {"blocked"})
        self.assertIn("sweep worker sweep_dir is /old", part["preflight_problems"][0])
        self.assertTrue(any("preflight failed" in x for x in lines))

    def test_builders_own_record_check_problem_blocks_everything(self):
        rc, _ = self.run_with(FakeBinding(native=["data/x differs from the CAL hash record"]))
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.calls, [])

    def test_unbound_reference_blocks_everything(self):
        rc, _ = self.run_with(FakeBinding(unbound=["build_x.LIVE"]))
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.calls, [])
        part = json.loads(next((self.env.root / "data/l3_meta/refresh/20261015_r1").glob("REFRESH_PARTIAL_*.json")).read_text())
        self.assertEqual(part["unbound_references"], ["build_x.LIVE"])

    def test_binding_that_cannot_be_applied_blocks_everything(self):
        class Broken(FakeBinding):
            def apply(self):
                raise ImportError("no module named build_x")
        fb = Broken()
        rc, lines = self.run_with(fb)
        self.assertEqual(rc, 1)
        self.assertEqual(self.env.calls, [])
        self.assertEqual(fb.events[-1], "restore")
        self.assertTrue(any("could not be applied" in x for x in lines), lines)

    def test_a_failing_report_does_not_lose_the_manifest(self):
        orig = R.Refresher._reproduction

        def boom(self, *a, **k):
            raise ValueError("report broke")
        R.Refresher._reproduction = boom
        try:
            rc, _ = self.env.run()
        finally:
            R.Refresher._reproduction = orig
        self.assertEqual(rc, 0)
        self.assertIn("report broke", self.env.manifest()["reproduction_vs_existing_versions"]["error"])

    def test_binding_is_restored_when_a_step_fails(self):
        fb = FakeBinding()
        self.env.fail.add("a")
        rc, _ = self.run_with(fb)
        self.assertEqual(rc, 1)
        self.assertEqual(fb.events[-1], "restore")


class Digest(Case):
    def _manifest(self, **over):
        h = OrderedDict([("Q1.n", 52), ("Q1.k", 9), ("Q1.rate", 9 / 52), ("Q1.expected_5pct", 2.6), ("N1.n", 51), ("N1.theta1", 2.53), ("N1.interval95", [1.5, 3.55]),
                         ("N1.p_le_05", 26), ("N1.expected_p_le_05", 2.55), ("CAL.kappa", 1.3154), ("CAL.kappa_interval95", [1.07, 1.52])])
        m = {"run": {"date": "20261015", "run_id": "r1", "folder": "data/l3_meta/refresh/20261015_r1"},
             "live_set": {"path": "x/l2_set_live.json", "as_of": "2026-10-15T16:14:57Z", "D": 141, "estimated": 55, "not_evaluable": 86, "sha256": "ab" * 32},
             "conventions": {"groups": {"a": {}, "b": {}}}, "steps": [{"step": "ex1", "key": "ex1", "label": "EX-1", "status": "ok", "seconds": 12.0}],
             "headline": h, "n_per_question": {"Q1": 52, "N1": 51, "CAL": None},
             "changes_vs_previous": {"previous": {"kind": "refresh", "folder": "data/l3_meta/refresh/20261014_r1", "live_as_of": "2026-10-14T00:00:00Z"},
                                     "headline": [{"key": k, "previous": v, "now": v, "status": "same"} for k, v in h.items()],
                                     "roster": {"added": [], "removed": [], "status_changed": [], "estimate_changed": []}},
             "needs_input": [], "reproduction_vs_existing_versions": None}
        m.update(over)
        return m

    def test_digest_has_a_chinese_summary_numbers_and_no_change_line(self):
        text = R.render_digest(self._manifest())
        self.assertIn("## 给主人（中文摘要）", text)
        self.assertIn("9 of 52 estimates beyond their own noise line (17.3%)", text)
        self.assertIn("mean Z 2.53 (95% 1.50 to 3.55)", text)
        self.assertIn("No headline number differs from refresh data/l3_meta/refresh/20261014_r1", text)
        self.assertNotIn("Needs input", text)

    def test_digest_lists_changes_roster_and_needs_input(self):
        m = self._manifest()
        m["changes_vs_previous"]["headline"][0] = {"key": "Q1.n", "previous": 52, "now": 53, "status": "changed"}
        m["changes_vs_previous"]["roster"] = {"added": ["new_design"], "removed": [], "status_changed": [{"design_id": "z", "from": ["not_evaluable", "support"], "to": ["estimated", None]}],
                                              "estimate_changed": []}
        m["needs_input"] = [{"question": "N1", "what": "no N1 geography sets", "designs": ["new_design"]}]
        text = R.render_digest(m)
        self.assertIn("| Q1.n | 52 | 53 |", text)
        self.assertIn("Added: `new_design`", text)
        self.assertIn("`z`: not_evaluable support -> estimated", text)
        self.assertIn("## Needs input", text)
        self.assertIn("缺冻结输入", text)

    def test_digest_names_the_outside_d_change_and_code_hash_differences(self):
        m = self._manifest()
        m["changes_vs_previous"]["outside_D"] = {"previous": {"in_flight": 7, "gate0_park": 1}, "now": {"in_flight": 27, "gate0_park": 1}}
        m["reproduction_vs_existing_versions"] = {
            "x/R.json": {"identical": True, "provenance_differences": ["code:build_sweep.py", "run_utc"], "stamp_differences_code": []},
            "x/S.json": {"identical": True, "provenance_differences": [], "stamp_differences_code": ["sweep_run_code"]}}
        text = R.render_digest(m)
        self.assertIn("Not yet counted (outside D): in_flight 7 -> 27.", text)
        self.assertIn("Reproduction: 2 of 2 result files equal the existing versions", text)
        self.assertIn("Code hashes that differ from those recorded in the existing files: code:build_sweep.py, sweep_run_code.", text)
        m["reproduction_vs_existing_versions"]["x/S.json"]["identical"] = False
        self.assertIn("different: x/S.json.", R.render_digest(m))

    def test_digest_without_known_questions_falls_back_to_a_plain_table(self):
        m = self._manifest(headline=OrderedDict([("A.n", 2)]), n_per_question={})
        m["changes_vs_previous"]["headline"] = [{"key": "A.n", "previous": 2, "now": 2, "status": "same"}]
        self.assertIn("| A.n | 2 | |", R.render_digest(m))


# ---------------------------------------------------------------------------------------------
class Helpers(unittest.TestCase):
    def test_same_value_tolerance_and_structures(self):
        self.assertTrue(R.same_value(1.0, 1.0 + 1e-12))
        self.assertFalse(R.same_value(1.0, 1.0 + 1e-6))
        self.assertTrue(R.same_value([1, 2.0], [1, 2.0 + 1e-13]))
        self.assertFalse(R.same_value([1, 2], [1, 2, 3]))
        self.assertTrue(R.same_value(float("nan"), float("nan")))
        self.assertFalse(R.same_value(True, 1))                                  # booleans are not numbers
        self.assertTrue(R.same_value({"a": [1]}, {"a": [1]}))
        self.assertFalse(R.same_value("x", "y"))

    def test_compare_headlines_statuses(self):
        rows = {r[0]: r for r in R.compare_headlines({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 2.5, "d": 4})}
        self.assertEqual([rows[k][3] for k in "abcd"], ["same", "changed", "gone", "new"])
        rows = {r[0]: r for r in R.compare_headlines({"x": None}, {"y": None})}                   # an empty key that appears or disappears is no change
        self.assertEqual([rows[k][3] for k in "xy"], ["same", "same"])

    def test_roster_diff(self):
        a = R.roster_of({"designs": [design("x", est=1.0), design("y", "not_evaluable", gate="support"), design("z", est=2.0)]})
        b = R.roster_of({"designs": [design("x", est=1.0), design("y", "estimated", est=0.1), design("w", est=3.0)]})
        d = R.roster_diff(a, b)
        self.assertEqual((d["added"], d["removed"]), (["w"], ["z"]))
        self.assertEqual(d["status_changed"][0]["design_id"], "y")
        self.assertEqual(d["estimate_changed"][0]["design_id"], "y")

    def test_deep_diff_skips_provenance_and_applies_tolerance(self):
        a = {"stamp": {"run_utc": "1"}, "inputs": {"p": 1}, "question": {"n": 3, "x": 1.0, "sweep_file_sha256": "a", "path": "p1", "list": [1, 2, {"k": 5}]}}
        b = {"stamp": {"run_utc": "2"}, "inputs": {"p": 2}, "question": {"n": 3, "x": 1.0 + 1e-13, "sweep_file_sha256": "b", "path": "p2", "list": [1, 2, {"k": 5}]}}
        n, ex, mx = R.deep_diff(a, b)
        self.assertEqual(n, 0)
        self.assertGreater(mx, 0.0)
        b["question"]["list"][2]["k"] = 6
        b["question"]["extra"] = 1
        n, ex, mx = R.deep_diff(a, b)
        self.assertEqual(n, 2)
        self.assertTrue(any("question.list[2].k" in e for e in ex))
        self.assertTrue(any("question.extra: only in new" in e for e in ex))

    def test_provenance_differences_of_sweep_files(self):
        old = {"live_set_sha256": "a", "run_utc": "1", "code": {"scripts/x/build_a.py": "1", "scripts/x/build_b.py": "2"}, "summary": {"PASS": 3}}
        new = {"live_set_sha256": "b", "run_utc": "2", "code": {"scripts/x/build_a.py": "9", "scripts/x/build_b.py": "2", "scripts/x/build_c.py": "3"}, "summary": {"PASS": 3}}
        self.assertEqual(R.deep_diff(old, new)[0], 0)                               # provenance is not a result
        self.assertEqual(R.provenance_differences(old, new), ["code:build_a.py", "code:build_c.py", "run_utc", "live_set_sha256"])
        new["summary"]["PASS"] = 2
        self.assertEqual(R.deep_diff(old, new)[0], 1)

    def test_stamp_differences_split(self):
        old = {"stamp": {"run_utc": "1", "live_set_sha256": "a", "code_sha256": "c", "sweep_run_code": {"f": 1}, "n": 3}}
        new = {"stamp": {"run_utc": "2", "live_set_sha256": "b", "code_sha256": "d", "sweep_run_code": {"f": 2}, "n": 4}}
        expected, code, other = R.stamp_differences(old, new)
        self.assertEqual(expected, ["live_set_sha256", "run_utc"])
        self.assertEqual(code, ["code_sha256", "sweep_run_code"])
        self.assertEqual(other, ["n"])

    def test_compare_npz(self):
        d = Path(tempfile.mkdtemp(prefix="l3_refresh_npz_"))
        base = dict(att=np.array([1.0, np.nan, 3.0]), sids=np.array(["a", "b"]), n=np.array([1, 2]), meta_json=np.array("x"))
        np.savez(d / "a.npz", **base)
        np.savez(d / "b.npz", **dict(base, meta_json=np.array("different provenance")))
        self.assertEqual(R.compare_npz(d / "a.npz", d / "b.npz"), (0.0, []))
        np.savez(d / "c.npz", **dict(base, att=np.array([1.0, np.nan, 3.0 + 1e-3])))
        worst, bad = R.compare_npz(d / "a.npz", d / "c.npz")
        self.assertEqual(bad, ["att"])
        self.assertGreater(worst, 1e-4)
        np.savez(d / "d.npz", **dict(base, sids=np.array(["a", "z"])))
        self.assertEqual(R.compare_npz(d / "a.npz", d / "d.npz")[1], ["sids"])
        np.savez(d / "e.npz", **dict(base, att=np.array([1.0, 2.0, 3.0])))           # NaN against a number
        self.assertEqual(R.compare_npz(d / "a.npz", d / "e.npz")[1], ["att"])

    def test_clean_and_dumps_never_write_nan(self):
        text = R.dumps({"a": float("nan"), "b": [float("inf"), np.float64(1.5), np.int64(3)], "c": (1, 2), "d": {1, 2}})
        self.assertEqual(json.loads(text), {"a": None, "b": [None, 1.5, 3], "c": [1, 2], "d": [1, 2]})

    def test_design_codes_modes(self):
        d = Path(tempfile.mkdtemp(prefix="l3_refresh_codes_"))
        (d / "r.json").write_text(json.dumps({"designs": [{"design_id": "a", "status": "estimated", "exceed": True}, {"design_id": "b", "status": "estimated"},
                                                          {"design_id": "c", "status": "not_evaluable", "gate": "support"}]}))
        got = R.design_codes(dict(file="r.json", list="designs", id="design_id", mode="q1"), d)
        self.assertEqual(got, {"a": "EVAL", "b": "NO_NOISE_REF", "c": "STOP_PRE_RUN:support"})
        (d / "s.json").write_text(json.dumps({"designs": [{"design_id": "a", "code": "EVAL"}]}))
        self.assertEqual(R.design_codes(dict(file="s.json", list="designs", id="design_id", key="code"), d), {"a": "EVAL"})
        self.assertEqual(R.design_codes(dict(file="none.json", list="designs", id="design_id", key="code"), d), {})

    def test_extract_headline_missing_file_and_path_give_none(self):
        d = Path(tempfile.mkdtemp(prefix="l3_refresh_head_"))
        (d / "r.json").write_text(json.dumps({"q": {"n": 4, "l": [1, 2]}}))
        got = R.extract_headline([("n", "r.json", "q.n"), ("l1", "r.json", "q.l.1"), ("x", "r.json", "q.zz"), ("m", "missing.json", "a")], lambda f: d / f)
        self.assertEqual(dict(got), {"n": 4, "l1": 2, "x": None, "m": None})


# ---------------------------------------------------------------------------------------------
LIVE_CANON_NAME = "data/l3_meta/l2_set_live.json"


def fake_modules(root):
    """Two invented builder modules that hold the canonical live path as a constant and as a default argument."""
    canon = Path(root) / LIVE_CANON_NAME
    src_core = (
        "from pathlib import Path\n"
        f"LIVE = Path({str(canon)!r})\n"
        "OTHER = Path('/somewhere/else.json')\n"
        "def load(path=LIVE):\n    return path\n"
        "def load_kw(*, path=LIVE):\n    return path\n"
        "def plain(x=3):\n    return x\n")
    src_b = (
        "from pathlib import Path\n"
        f"LIVE = Path({str(canon)!r})\n"
        "OUT = Path('/old/out')\n"
        "LOG = OUT / 'RUN_LOG.jsonl'\n"
        "EX1_JSON = Path('/old/ex1.json')\n"
        "D3V1_RESULTS = Path('/old/d31.json')\n"
        "def parse_hash_record(path=None):\n    return {'files': {}, 'listed': {'data/l3_meta/l2_set_live.json': 'old', 'data/l3_meta/d1_v1/RESULTS_D1_V1.json': 'old', 'scripts/x.py': 'keep'}}\n"
        "def _init_worker():\n    return 'orig'\n")
    mods = []
    for name, src in (("l3fake_core", src_core), ("l3fake_b", src_b)):
        m = types.ModuleType(name)
        exec(compile(src, name, "exec"), m.__dict__)
        sys.modules[name] = m
        mods.append(m)
    return mods


class Rebinding(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="l3_refresh_bind_"))
        self.core, self.b = fake_modules(self.root)
        self.new_live = self.root / "data/l3_meta/refresh/20261015_r1/live/l2_set_live.json"
        self.sweep = self.root / "data/l3_meta/refresh/20261015_r1/sweep"

    def tearDown(self):
        for n in ("l3fake_core", "l3fake_b"):
            sys.modules.pop(n, None)

    def binding(self, **kw):
        return R.Binding(self.root, self.new_live, kw.pop("live_sha", "n" * 64), self.sweep, kw.pop("results", {}), modules=("l3fake_core", "l3fake_b"),
                         sweep_mod="l3fake_b", cal_mod="l3fake_b", d3v2_mod="l3fake_b")

    def test_constants_and_defaults_are_replaced_and_restored(self):
        canon = self.root / LIVE_CANON_NAME
        b = self.binding()
        b.apply()
        try:
            self.assertEqual(self.core.LIVE, self.new_live)
            self.assertEqual(self.b.LIVE, self.new_live)
            self.assertEqual(self.core.load(), self.new_live)                        # default argument
            self.assertEqual(self.core.load_kw(), self.new_live)                     # keyword-only default
            self.assertEqual(self.core.plain(), 3)
            self.assertEqual(self.core.OTHER, Path("/somewhere/else.json"))
            self.assertEqual(self.b.OUT, self.sweep)
            self.assertEqual(self.b.LOG, self.sweep / "RUN_LOG.jsonl")
            self.assertEqual(b.unbound(), [])
            self.assertIn("l3fake_core.load", b.describe()["function_defaults"])
        finally:
            b.restore()
        self.assertEqual(self.core.LIVE, canon)
        self.assertEqual(self.core.load(), canon)
        self.assertEqual(self.core.load_kw(), canon)
        self.assertEqual(self.b.OUT, Path("/old/out"))
        self.assertEqual(self.b._init_worker(), "orig")
        self.assertFalse(hasattr(self.b, R.ORIG_INIT_ATTR))
        self.assertNotIn(R.ENV_SPEC, os.environ)

    def test_effective_record_drops_snapshots_and_adds_the_run_live_set(self):
        with self.binding() as b:
            rec = self.b.parse_hash_record("docs/plans/l3-meta/X.txt")
            self.assertEqual(rec["listed"], {"scripts/x.py": "keep", b.live_rel: "n" * 64})
            self.assertEqual(sorted(b.describe()["snapshot_lines_dropped"]["docs/plans/l3-meta/X.txt"]),
                             ["data/l3_meta/d1_v1/RESULTS_D1_V1.json", "data/l3_meta/l2_set_live.json"])
        self.assertIn("data/l3_meta/l2_set_live.json", self.b.parse_hash_record()["listed"])

    def test_upstream_results_are_pointed_at_this_runs_files(self):
        with self.binding(results={"ex1": self.root / "run/ex1/R.json", "d3_v1": self.root / "run/d3_v1/R.json"}):
            self.assertEqual(self.b.EX1_JSON, self.root / "run/ex1/R.json")
            self.assertEqual(self.b.D3V1_RESULTS, self.root / "run/d3_v1/R.json")
        self.assertEqual(self.b.EX1_JSON, Path("/old/ex1.json"))

    def test_unbound_scan_finds_a_reference_the_rebinding_missed(self):
        b = self.binding()
        b.apply()
        try:
            self.core.HIDDEN = self.root / LIVE_CANON_NAME
            self.core.late = types.FunctionType((lambda p=self.root / LIVE_CANON_NAME: p).__code__, self.core.__dict__, "late", (self.root / LIVE_CANON_NAME,))
            found = b.unbound()
            self.assertIn("l3fake_core.HIDDEN", found)
            self.assertIn("l3fake_core.late() default", found)
        finally:
            b.restore()

    def test_worker_initializer_rebinds_in_a_fresh_process_image(self):
        import sweep_core as S
        import build_sweep as B
        saved = (S.LIVE, B.OUT, B.LOG, B._init_worker)
        called = []
        os.environ[R.ENV_SPEC] = json.dumps({"here": str(HERE), "live": "/run/live.json", "sweep_dir": "/run/sweep"})
        try:
            B._init_worker = lambda: called.append(True)
            R.sweep_worker_init()
            self.assertEqual(S.LIVE, Path("/run/live.json"))
            self.assertEqual(B.OUT, Path("/run/sweep"))
            self.assertEqual(B.LOG, Path("/run/sweep/RUN_LOG.jsonl"))
            self.assertEqual(called, [True])
        finally:
            S.LIVE, B.OUT, B.LOG, B._init_worker = saved
            os.environ.pop(R.ENV_SPEC, None)


class SpawnProbe(unittest.TestCase):
    """A spawned process (as the sweep uses) receives the run's paths through the initializer; no L2 module is loaded."""

    def test_spawned_worker_sees_the_runs_folder_and_live_set(self):
        root = R.ROOT
        b = R.Binding(root, root / "x/live.json", None, root / "x/sweep", {})
        with b:
            self.assertEqual(b.probe_workers(), [])
            b.sweep_dir = root / "elsewhere"                                         # the environment still carries the original binding
            problems = b.probe_workers()
            self.assertTrue(any("sweep_dir" in p for p in problems), problems)
        self.assertNotIn(R.ENV_SPEC, os.environ)


class RealModules(unittest.TestCase):
    """The names the rebinding depends on exist in the real builders (importing them reads no data file)."""

    def test_every_expected_target_exists_and_is_found_by_the_scan(self):
        root = R.ROOT
        b = R.Binding(root, root / "x/live.json", "n" * 64, root / "x/sweep", {"ex1": root / "x/ex1.json", "d1": root / "x/d1.json", "d2": root / "x/d2.json",
                                                                               "d3_v1": root / "x/d31.json", "d3_v2": root / "x/d32.json"})
        with b:
            d = b.describe()
            self.assertEqual(b.unbound(), [])
            consts = " ".join(d["constants"])
            for name in ("sweep_core.LIVE", "build_d3.LIVE", "build_n1.LIVE", "build_ex1.LIVE", "build_sweep.OUT", "build_sweep.LOG", "build_cal.EX1_JSON",
                         "build_cal.D1_JSON", "build_cal.D2_JSON", "build_cal.D3_JSON", "build_d3_v2.D3V1_RESULTS"):
                self.assertIn(name, consts)
            for fn in ("build_d1.load_live", "build_d3.load_live", "build_n1.load_live"):
                self.assertIn(fn, d["function_defaults"])
            import build_sweep as B
            import build_cal as C
            self.assertEqual(B.OUT, root / "x/sweep")
            self.assertEqual(C.D3_JSON, root / "x/d32.json")
            self.assertTrue(callable(B.parse_hash_record) and hasattr(B.parse_hash_record, "__wrapped__"))
        import build_sweep as B
        self.assertEqual(B.OUT, root / "data/l3_meta/sweep_v1")
        self.assertFalse(hasattr(B.parse_hash_record, "__wrapped__"))

    def test_real_steps_call_the_builders_with_the_registered_arguments(self):
        calls = []
        orig = R.run_builder

        def stub(ctx, module, argv, log):
            calls.append((module, [str(a) for a in argv], log))
            return {}
        R.run_builder = stub
        try:
            ctx = R.Ctx(R.ROOT, Path("/r"), Path("/r/live/l2_set_live.json"), "s", {"designs": []}, workers=5)
            for k in ("live", "ex1", "n1", "sweep", "d1", "d3_v1", "d2", "d3_v2", "cal", "sweep_ext", "d1_ext"):
                ctx.folders[k] = Path("/r") / k
            steps = {s.name: s for s in R.real_steps()}
            for s in steps.values():
                s.run(ctx)
        finally:
            R.run_builder = orig
        got = {c[2]: c for c in calls}
        self.assertEqual(got["ex1"][:2], ("build_ex1", ["--out-dir", "/r/ex1"]))
        self.assertEqual(got["n1"][:2], ("build_n1_v2", ["--out-dir", "/r/n1", "--live-set-changed-ok"]))
        self.assertEqual(got["d3_v1"][:2], ("build_d3", ["--out-dir", "/r/d3_v1", "--live-set-changed-ok"]))
        for k, mod in (("d1", "build_d1"), ("d2", "build_d2"), ("d3_v2", "build_d3_v2"), ("cal", "build_cal")):
            self.assertEqual(got[k][:2], (mod, ["--out-dir", f"/r/{k}", "--sweep-dir", "/r/sweep"]))
        self.assertEqual(got["sweep_ext"][:2], ("build_sweep_ext", ["--sweep-dir", "/r/sweep", "--out-dir", "/r/sweep_ext", "--workers", "5"]))
        self.assertEqual(got["d1_ext"][:2], ("build_d1_ext", ["--sweep-dir", "/r/sweep", "--ext-dir", "/r/sweep_ext", "--out-dir", "/r/d1_ext",
                                                              "--v1-results", "/r/d1/RESULTS_D1_V1.json"]))
        stages = [c[1] for c in calls if c[0] == "build_sweep"]
        self.assertEqual([s[1] for s in stages], ["series", "series_count", "gate", "sweep", "d3runs", "manifest"])
        self.assertTrue(all(s[2:] == ["--workers", "5"] for s in stages))

    def test_step_graph_is_closed_and_ordered(self):
        steps = R.real_steps()
        seen = {"live"}
        for s in steps:
            self.assertTrue(set(s.needs) <= seen, f"{s.name} needs {s.needs}")
            seen.add(s.name)
        groups = set(R.REAL_GROUPS)
        for s in steps:
            self.assertTrue(set(s.groups) <= groups)
        self.assertEqual([s.name for s in steps if s.folder == "sweep"],
                         ["sweep_series", "sweep_series_count", "sweep_gate", "sweep_sweep", "sweep_d3runs", "sweep_manifest"])

    def test_headline_labels_are_unique_and_n_keys_exist(self):
        labels = [h[0] for s in R.real_steps() for h in s.headline]
        self.assertEqual(len(labels), len(set(labels)))
        for q, key in R.N_PER_QUESTION.items():
            self.assertIn(key, labels)

    def test_snapshot_patterns(self):
        for p in ("data/l3_meta/l2_set_live.json", "data/l3_meta/l2_set_live_20261012T100733Z.json", "data/l3_meta/sweep_v1/GATE_REPORT.json",
                  "data/l3_meta/d2_v1/NC_CELLS_D2_V1.csv", "data/l2_pipeline/registry/TERMINAL_RUNS.json", "data/l3_meta/refresh_dev/x/y.json",
                  "data/l3_meta/refresh/20261015_r1/ex1/RESULTS_EX1_V3.json"):
            self.assertTrue(R.is_snapshot(p), p)
        for p in ("data/l3_meta/n1_sets/n1_sets_v1.json", "data/l3_meta/covariates/state_year_v2.csv", "data/l3_meta/d2_plausibility/d2_reconciled_v1.csv",
                  "scripts/l3_meta/build_d1.py", "scripts/l2_pipeline/engine/ri.py", "docs/plans/l3-meta/PREREG_L3_META.md", "data/l3_meta/l2_set_v2.json"):
            self.assertFalse(R.is_snapshot(p), p)


if __name__ == "__main__":
    unittest.main(verbosity=1)
