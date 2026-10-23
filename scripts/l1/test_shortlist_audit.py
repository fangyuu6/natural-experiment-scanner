"""Checks for scripts/l1/shortlist_audit.py: URL and band rules, the seeded draw, ids, the coder-file validator, the third-read
list, and a whole miniature run (freeze, draw, leak check, validate, third) on synthetic frames. Needs no real data except
FrameManifestMatchesProtocol, which is skipped when the real frames or protocol are not there.

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/l1 -p 'test_shortlist_audit.py'
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
spec = importlib.util.spec_from_file_location("shortlist_audit", HERE / "shortlist_audit.py")
sa = importlib.util.module_from_spec(spec)
sys.modules["shortlist_audit"] = sa
spec.loader.exec_module(sa)

COLUMNS = ["item_id", "text_status", "version_read", "source_used", "did_treated", "did_comparison", "did_timing",
           "did_viable", "did_reason", "rd_running_variable", "rd_cutoff", "rd_assignment", "rd_viable", "rd_reason", "note"]
ENUMS = {"text_status": ["full", "partial", "unavailable"],
         "version_read": ["enrolled_or_final", "amended_or_engrossed", "introduced_or_proposed", "other", "none"],
         "source_used": ["official", "archive", "aggregator", "none"], "yn": ["Yes", "No"]}
MINI_SPEC = {"columns": COLUMNS, "enums": ENUMS}


def good_row(item="AAAAAA", did="Yes", rd="No"):
    return {"item_id": item, "text_status": "full", "version_read": "enrolled_or_final", "source_used": "official",
            "did_treated": "Yes" if did == "Yes" else "No", "did_comparison": "Yes" if did == "Yes" else "No",
            "did_timing": "Yes", "did_viable": did, "did_reason": "Section 2 applies the rule to large firms from 2020.",
            "rd_running_variable": "No", "rd_cutoff": "No", "rd_assignment": "No", "rd_viable": rd,
            "rd_reason": "No numeric cutoff decides who is treated.", "note": ""}


class UrlBandAndKeys(unittest.TestCase):
    def test_public_jurisdiction_matches_the_screening_page(self):
        page_spec = importlib.util.spec_from_file_location("restructure_screening", REPO / "scripts/viz_site/restructure_screening.py")
        page = importlib.util.module_from_spec(page_spec)
        page_spec.loader.exec_module(page)
        for value in ["AK State", "DC State", "US Federal", "", "Illinois State", "TX State", "Federal"]:
            self.assertEqual(sa.public_jurisdiction(value), page.public_jurisdiction(value), value)

    def test_safe_public_url_drops_keys_and_keeps_session_parameters(self):
        self.assertEqual(sa.safe_public_url("https://api.govinfo.gov/packages/X/htm?api_key=SECRET"), "https://api.govinfo.gov/packages/X/htm")
        self.assertEqual(sa.safe_public_url("http://www.oklegislature.gov/BillInfo.aspx?Bill=hb1&Session=1900"),
                         "http://www.oklegislature.gov/BillInfo.aspx?Bill=hb1&Session=1900")
        self.assertEqual(sa.safe_public_url("https://iga.in.gov/x?SessionID=91&DocNum=5&token=abc#frag"), "https://iga.in.gov/x?SessionID=91&DocNum=5")
        self.assertEqual(sa.safe_public_url("https://user:pw@example.org/a"), "")
        self.assertEqual(sa.safe_public_url("ftp://example.org/a"), "")
        self.assertEqual(sa.safe_public_url(""), "")
        self.assertEqual(sa.safe_public_url("https://a.gov/p?q=a%20b&email=x@y.z"), "https://a.gov/p?q=a%20b")

    def test_constructed_federal_register_address(self):
        self.assertEqual(sa.constructed_fr_url("fedreg-2016-16288"), "https://www.federalregister.gov/d/2016-16288")
        self.assertEqual(sa.constructed_fr_url("fedreg-E8-24080"), "https://www.federalregister.gov/d/E8-24080")
        self.assertEqual(sa.constructed_fr_url("AR-HB 1007-2018S2"), "")
        self.assertEqual(sa.constructed_fr_url(""), "")

    def test_band_edges(self):
        self.assertEqual([sa.band_of(s) for s in (3.0, 3.2, 3.29, 3.4, 3.5, 3.59, 3.6, 4.8)], list("LLLMMMHH"))

    def test_strata_names(self):
        self.assertEqual(sa.stratum_A("FR", "L", "D"), "A-FR-L-D")
        self.assertEqual(sa.stratum_A("RG", "H", "R"), "A-RG-R")
        self.assertEqual(sa.stratum_A("OT", "M", "D"), "A-OT")
        self.assertEqual(sa.group_of_A("Federal Regulations", "Regulations.gov"), "RG")
        self.assertEqual(sa.group_of_A("Federal Regulations", "Federal Register"), "FR")
        self.assertEqual(sa.group_of_A("Federal Legislation", "GovInfo"), "OT")
        self.assertEqual(sa.stratum_B("Federal Regulations", True), "B-FR-F")
        self.assertEqual(sa.stratum_B("State Legislation", False), "B-SL-U")
        self.assertEqual(sa.stratum_B("State/Local Executive", True), "B-OT")

    def test_gates_failed(self):
        row = {"wave_flag": "1", "confound_flags": "covid_window|fentanyl_wave", "data_verdict": "empty", "min_gate": "2.0"}
        self.assertEqual(sa.gates_failed(row), "wave|covid|data|min_gate")
        row = {"wave_flag": "0", "confound_flags": "", "data_verdict": "mixed", "min_gate": "3.0"}
        self.assertEqual(sa.gates_failed(row), "")


class DrawRules(unittest.TestCase):
    def units(self, n=200):
        return [{"unit_key": "unit-%04d" % i} for i in range(n)]

    def test_rank_key_format(self):
        expect = hashlib.sha256("S|draw|A-X|unit-1".encode()).hexdigest()
        self.assertEqual(sa.rank_key("S", "A-X", "unit-1"), expect)

    def test_sample_ignores_input_order_and_has_size_n(self):
        units = self.units()
        base = sa.draw_stratum("seed", "A-X", units, 25)
        shuffled = units[:]
        random.Random(5).shuffle(shuffled)
        again = sa.draw_stratum("seed", "A-X", shuffled, 25)
        self.assertEqual([u["unit_key"] for u in base], [u["unit_key"] for u in again])
        self.assertEqual(len({u["unit_key"] for u in base}), 25)

    def test_different_seed_or_stratum_gives_a_different_sample(self):
        units = self.units()
        a = {u["unit_key"] for u in sa.draw_stratum("seed1", "A-X", units, 25)}
        b = {u["unit_key"] for u in sa.draw_stratum("seed2", "A-X", units, 25)}
        c = {u["unit_key"] for u in sa.draw_stratum("seed1", "A-Y", units, 25)}
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_selection_is_roughly_uniform(self):
        counts = [0] * 10
        for seed in range(400):
            for u in sa.draw_stratum("s%d" % seed, "A-X", self.units(100), 10):
                counts[int(u["unit_key"][-4:]) // 10] += 1
        self.assertTrue(all(300 <= c <= 500 for c in counts), counts)

    def test_ids_are_unique_short_and_crockford(self):
        keys = ["unit-%d" % i for i in range(5000)]
        ids = sa.unique_ids("seed", keys, 6)
        self.assertEqual(len(set(ids.values())), len(keys))
        self.assertTrue(all(set(v) <= set(sa.ID_ALPHABET) and len(v) >= 6 for v in ids.values()))
        self.assertEqual(sa.make_id("seed", "unit-1", 6), sa.make_id("seed", "unit-1", 6))
        self.assertNotEqual(sa.make_id("seed", "unit-1", 6), sa.make_id("other", "unit-1", 6))


class Validator(unittest.TestCase):
    def check(self, rows, expected=None):
        return sa.validate_rows(COLUMNS, rows, MINI_SPEC, expected)

    def test_accepts_good_rows_and_unavailable_rows(self):
        unavailable = {c: "NA" for c in COLUMNS}
        unavailable.update(item_id="BBBBBB", text_status="unavailable", version_read="none", source_used="none", note="no address")
        self.assertEqual(self.check([good_row(), unavailable], {"AAAAAA", "BBBBBB"}), [])

    def test_header_must_match(self):
        self.assertTrue(sa.validate_rows(COLUMNS[::-1], [], MINI_SPEC))

    def test_viable_needs_all_components(self):
        row = good_row()
        row["did_comparison"] = "No"
        self.assertTrue(any("component" in e for e in self.check([row])))

    def test_na_only_when_unavailable_and_enums(self):
        row = good_row()
        row["did_timing"] = "NA"
        self.assertTrue(self.check([row]))
        row = good_row()
        row["text_status"] = "unavailable"
        self.assertTrue(self.check([row]))
        row = good_row()
        row["version_read"] = "none"
        self.assertTrue(self.check([row]))
        row = good_row()
        row["did_viable"] = "yes"
        self.assertTrue(self.check([row]))

    def test_reasons_notes_duplicates_and_membership(self):
        row = good_row()
        row["did_reason"] = "short"
        self.assertTrue(self.check([row]))
        row = good_row()
        row["note"] = "x" * 201
        self.assertTrue(self.check([row]))
        self.assertTrue(any("duplicate" in e for e in self.check([good_row(), good_row()])))
        self.assertTrue(any("not in this batch" in e for e in self.check([good_row("ZZZZZZ")], {"AAAAAA"})))
        self.assertTrue(any("missing" in e for e in self.check([good_row()], {"AAAAAA", "CCCCCC"})))

    def test_disagreements_count_na_against_a_value(self):
        a = {"X1": good_row("X1", "Yes", "No"), "X2": good_row("X2", "No", "No"), "X3": good_row("X3", "Yes", "No")}
        b = {"X1": good_row("X1", "Yes", "No"), "X2": good_row("X2", "Yes", "No"), "X3": good_row("X3", "Yes", "No")}
        b["X3"]["rd_viable"] = "NA"
        found = {i: f for i, f, _, _ in sa.disagreements(a, b)}
        self.assertEqual(found, {"X2": ["did_viable"], "X3": ["rd_viable"]})


class MiniatureRun(unittest.TestCase):
    """Synthetic frames and a synthetic protocol with the same two machine-read blocks as the real one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.key = self.dir / "key"
        self.packet = self.dir / "packet"
        frames = self.key / "frames"
        frames.mkdir(parents=True)
        rng = random.Random(1)

        def frame_rows(arm, stratum, n, start):
            rows = []
            for i in range(n):
                row = {c: "" for c in sa.FRAME_COLUMNS}
                row.update(unit_key="%s-unit-%03d" % (arm.lower(), start + i), api_id=str(start + i), source_family="State Legislation",
                           source="State Legislation", group="SL", title="A bill number %d" % i, jurisdiction="Ohio",
                           date="2019-01-%02d" % (1 + i % 28), year="2019", document_url="https://example.org/bill/%d" % (start + i),
                           url_constructed="0", composite_score="3.2", band="L", did="1", rd="0", flag="D", flagged="1",
                           best_design="DID", min_gate="3.0", wave_flag="0", confound_flags="", data_verdict="public_ok",
                           orphan="0", stratum=stratum)
                rows.append(row)
            return rows
        A = frame_rows("A", "A-SL-L-D", 40, 0) + frame_rows("A", "A-SL-H-R", 12, 100)
        B = frame_rows("B", "B-SL-F", 60, 200)
        C = []
        for i in range(10):
            C.append({"unit_key": "panel-%03d" % i, "eval_id": str(i), "title": "Panel text %d" % i, "jurisdiction": "Texas", "date": "2025-02-01",
                      "document_url": "https://example.org/panel/%d" % i, "human_did": "1", "human_rd": str(i % 2), "human_source_did": "unanimous",
                      "human_source_rd": "unanimous", "origin": "fresh", "stratum": "C-RD1" if i % 2 else "C-RD0"})
        names = {"A": "frame_A_shortlist.csv", "B": "frame_B_enacted_not_shortlisted.csv", "C": "frame_C_anchor_panel.csv"}
        sa.write_csv_new(frames / names["A"], sa.FRAME_COLUMNS, A)
        sa.write_csv_new(frames / names["B"], sa.FRAME_COLUMNS, B)
        sa.write_csv_new(frames / names["C"], sa.ANCHOR_COLUMNS, C)
        arms, manifest = {}, {"frames": {}}
        plan = {"A": {"A-SL-L-D": (40, 10), "A-SL-H-R": (12, 5)}, "B": {"B-SL-F": (60, 8)}, "C": {"C-RD1": (5, 2), "C-RD0": (5, 3)}}
        for arm in "ABC":
            sha = sa.sha256_file(frames / names[arm])
            arms[arm] = {"frame": names[arm], "frame_sha256": sha, "frame_rows": sum(v[0] for v in plan[arm].values()),
                         "strata": {s: {"N": N, "n": n} for s, (N, n) in plan[arm].items()}}
            manifest["frames"][arm] = {"sha256": sha, "strata": {s: N for s, (N, n) in plan[arm].items()}}
        (frames / "FRAME_MANIFEST.json").write_text(json.dumps(manifest))
        total = sum(n for arm in plan.values() for (N, n) in arm.values())
        self.spec = {"seed": "MINI-SEED", "batch_size": 7, "id_length": 6, "total_n": total, "arms": arms, "columns": COLUMNS, "enums": ENUMS}
        self.write_protocol("Please judge each document. Output file out/codes_{CODER}_bNN.csv.")

    def tearDown(self):
        self.tmp.cleanup()

    def write_protocol(self, instructions):
        self.protocol = self.dir / "PROTOCOL.md"
        self.protocol.write_text("# Mini protocol\n\n<!-- CODER_INSTRUCTIONS_BEGIN -->\n# Coder {CODER}\n%s\n<!-- CODER_INSTRUCTIONS_END -->\n\n"
                                 "<!-- DRAW_SPEC_BEGIN -->\n```json\n%s\n```\n<!-- DRAW_SPEC_END -->\n" % (instructions, json.dumps(self.spec, indent=1)),
                                 encoding="utf-8")

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sa.main(list(args) + ["--protocol", str(self.protocol), "--key-dir", str(self.key), "--packet-dir", str(self.packet)])
        return out.getvalue()

    def test_whole_flow(self):
        self.run_cli("freeze")
        self.run_cli("draw")
        sheet, header = sa.read_csv(self.packet / "coder_sheet.csv")
        self.assertEqual(header, sa.VISIBLE_COLUMNS)
        self.assertEqual(len(sheet), self.spec["total_n"])
        key, _ = sa.read_csv(self.key / "KEY.csv")
        self.assertEqual({r["item_id"] for r in key}, {r["item_id"] for r in sheet})
        for stratum, cell in [(s, c) for a in self.spec["arms"].values() for s, c in a["strata"].items()]:
            rows = [r for r in key if r["stratum"] == stratum]
            self.assertEqual(len(rows), cell["n"])
            self.assertAlmostEqual(sum(float(r["weight"]) for r in rows), cell["N"], places=3)
        # batches: same members for both coders, different order, size 7
        items_a, _ = sa.read_csv(self.packet / "coder_A" / "items_A.csv")
        items_b, _ = sa.read_csv(self.packet / "coder_B" / "items_B.csv")
        self.assertEqual({(r["batch"], r["item_id"]) for r in items_a}, {(r["batch"], r["item_id"]) for r in items_b})
        self.assertNotEqual([r["item_id"] for r in items_a], [r["item_id"] for r in items_b])
        sizes = {}
        for r in items_a:
            sizes[r["batch"]] = sizes.get(r["batch"], 0) + 1
        self.assertEqual(sorted(sizes.values()), [7, 7, 7, 7])
        # nothing hidden in what coders can open
        released = "".join(p.read_text(encoding="utf-8") for p in self.packet.rglob("*") if p.is_file())
        for hidden in ("composite", "band", "weight", "a-unit-", "b-unit-", "panel-0"):
            self.assertNotIn(hidden, released.lower().replace("coder", ""), hidden)
        manifest = json.loads((self.key / "DRAW_MANIFEST.json").read_text())
        self.assertEqual(manifest["leak_check"], "clean")
        # coders A and B: A answers everything, B disagrees on two items
        first = items_a[0]["batch"]
        ids = [r["item_id"] for r in items_a if r["batch"] == first]
        for letter, flip in (("A", False), ("B", True)):
            rows = [good_row(i, "Yes", "No") for i in ids]
            if flip:
                rows[0]["did_viable"] = "No"
                rows[0]["did_treated"] = "No"
                rows[1]["rd_viable"] = "No"
            path = self.packet / ("coder_" + letter) / "out" / ("codes_%s_b%02d.csv" % (letter, int(first)))
            sa.write_csv_new(path, COLUMNS, rows)
            self.assertIn("OK", self.run_cli("validate", "--codes", str(path)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.run_cli("third")
        third, _ = sa.read_csv(self.packet / "coder_C" / "items_C.csv")
        self.assertEqual({r["item_id"] for r in third}, {ids[0]})  # only the did_viable difference; row 1 differs on nothing visible
        listed, _ = sa.read_csv(self.key / "third_read_list.csv")
        self.assertEqual([r["item_id"] for r in listed], [ids[0]])
        with self.assertRaises(SystemExit):
            self.run_cli("third", "--require-complete")

    def test_third_read_includes_every_disagreement(self):
        self.run_cli("freeze")
        self.run_cli("draw")
        items_a, _ = sa.read_csv(self.packet / "coder_A" / "items_A.csv")
        first = items_a[0]["batch"]
        ids = [r["item_id"] for r in items_a if r["batch"] == first]
        a_rows = [good_row(i, "Yes", "No") for i in ids]
        b_rows = [good_row(i, "Yes", "No") for i in ids]
        b_rows[1]["rd_viable"] = "No"  # same as A: no difference
        b_rows[2]["did_viable"] = "No"
        b_rows[2]["did_treated"] = "No"
        for letter, rows in (("A", a_rows), ("B", b_rows)):
            sa.write_csv_new(self.packet / ("coder_" + letter) / "out" / ("codes_%s_b%02d.csv" % (letter, int(first))), COLUMNS, rows)
        with contextlib.redirect_stdout(io.StringIO()):
            self.run_cli("third")
        third, _ = sa.read_csv(self.packet / "coder_C" / "items_C.csv")
        self.assertEqual({r["item_id"] for r in third}, {ids[2]})

    def test_draw_refuses_an_altered_protocol_or_frame(self):
        self.run_cli("freeze")
        self.write_protocol("Altered instructions {CODER}.")
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("draw")
        self.assertIn("REFUSED", str(ctx.exception))
        self.write_protocol("Please judge each document. Output file out/codes_{CODER}_bNN.csv.")
        self.run_cli("draw")  # the original text is accepted again
        frame = self.key / "frames" / "frame_C_anchor_panel.csv"
        self.assertTrue(frame.exists())

    def test_draw_does_not_overwrite(self):
        self.run_cli("freeze")
        self.run_cli("draw")
        with self.assertRaises(FileExistsError):
            self.run_cli("draw")


class FrameManifestMatchesProtocol(unittest.TestCase):
    def test_real_protocol_and_frames_agree(self):
        protocol = sa.PROTOCOL
        manifest_path = sa.KEY_DIR / "frames" / "FRAME_MANIFEST.json"
        if not protocol.exists() or not manifest_path.exists():
            self.skipTest("real protocol or frames not present")
        spec_ = sa.read_spec(protocol)
        manifest = json.loads(manifest_path.read_text())
        for arm in "ABC":
            self.assertEqual(spec_["arms"][arm]["frame_sha256"], manifest["frames"][arm]["sha256"])
            self.assertEqual({s: c["N"] for s, c in spec_["arms"][arm]["strata"].items()}, manifest["frames"][arm]["strata"])
        self.assertEqual(spec_["total_n"], sum(c["n"] for a in spec_["arms"].values() for c in a["strata"].values()))
        self.assertEqual(spec_["total_n"] % spec_["batch_size"], 0)


if __name__ == "__main__":
    unittest.main()
