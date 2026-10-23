"""WP5 contracts: preserved originals, stored figures and page interactions.

Node uses local in-memory API responses only. Native button markup is checked
alongside its click behaviour; actual keyboard dispatch/layout needs a browser.
No site build or evaluation is invoked by this suite.
"""
from __future__ import annotations

import ast
from collections import Counter
import csv
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
from scripts.zh_pages import build, render_eval_figures as figures

ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "deploy_output/_release/polish_baseline_20261004"
GROUPS = ROOT / "scripts/zh_pages/policy_groups.en.html"
DOCS = ROOT / "scripts/zh_pages/documentation.en.html"


def normalize(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def strip_generated(text):
    text = re.sub(r"<!-- NX:[A-Z]+:START -->.*?<!-- NX:[A-Z]+:END -->", "", text, flags=re.S)
    text = re.sub(r"<!-- EVAL:[A-Z]+:START -->.*?<!-- EVAL:[A-Z]+:END -->", "", text, flags=re.S)
    text = re.sub(r"<!-- SCREENING:START -->.*?<!-- SCREENING:END -->", "", text, flags=re.S)
    text = re.sub(r"<!-- SYNTH-METHOD:START -->.*?<!-- SYNTH-METHOD:END -->", "", text, flags=re.S)
    return re.sub(r"<(script|style|footer)\b[^>]*>.*?</\1>", "", text, flags=re.S)


class Records(HTMLParser):
    """Collect complete paragraphs/cells including inline markup text."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.records, self.open = [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.open.append([tag, dict(attrs), []])

    def handle_data(self, data):
        for record in self.open:
            record[2].append(data)

    def handle_endtag(self, tag):
        if self.open and self.open[-1][0] == tag:
            kind, attrs, text = self.open.pop()
            self.records.append((kind, attrs, normalize("".join(text))))


def page_script(path):
    return re.findall(r'<script\b(?![^>]*id="public-details-links")[^>]*>(.*?)</script>', path.read_text(), re.S)[-1]


class OriginalContentTests(unittest.TestCase):
    def test_sources_do_not_depend_on_promoted_home(self):
        with patch.object(build, 'DEPLOY', Path('/nonexistent-promoted-output')):
            for page in build.PAGES:
                source, _ = build.load_source(page)
                self.assertIn('<html', source)
                if page == 'policy_db':
                    _, found = build.set_aside_story(page, source)
                    self.assertTrue(found)
                    self.assertIn('id="tableView"', source)
        with self.assertRaises(build.BuildError):
            build.set_aside_story('policy_db', '<html><body>Missing story</body></html>')

    def test_exact_pairs_apply_and_preserve_ids_data_values_api_lookups(self):
        chrome = build.shared_chrome()
        for page in ("policy_groups", "documentation"):
            with self.subTest(page=page):
                en, _ = build.load_source(page)
                core, slots = chrome.detach_slots(en)
                self.assertEqual(set(slots), {"style", "header", "nav", "footer", "motion"})
                errors = []
                zh = core
                for name in ("common", page):
                    pairs = build.load_pairs(build.I18N / (name + ".zh.json"), errors)
                    pairs = chrome.body_pairs(pairs, core, slots)
                    zh, applied = build.apply_pairs(zh, pairs, page, errors, soft=name == "common")
                    if name == page:
                        self.assertGreater(applied, 20)
                self.assertEqual(errors, [])
                zh = build.rewrite_links(build.set_lang(zh), Counter())
                self.assertEqual(build.integrity(core, zh), [])
                self.assertRegex(zh, r'<html\b[^>]*lang="zh-CN"')
                self.assertRegex(zh, r"[\u4e00-\u9fff]")

    def test_method_is_a_reader_facing_summary_with_existing_links(self):
        source = DOCS.read_text()
        core = strip_generated(source)
        self.assertLess(len(Records(core).records), 25)
        for old_id in ('overview', 'scoring', 'scoring-confounds', 'screening', 'data-sources', 'validation', 'references'):
            self.assertIn('id="' + old_id + '"', source)
        for obsolete in ('<code>', 'EVAL:DISTRIBUTION:START', 'hash-locked', 'Qwen 3.6', 'GLM 5.2', 'pipeline'):
            self.assertNotIn(obsolete, core)
        for term in ('regression discontinuity', 'difference-in-differences', 'parallel trends'):
            self.assertIn(term, core)
        # Content check of R25 (S1): the Explore page no longer says the plan was time-stamped in a private record, so Method does not either.
        for term in ('time-stamped', 'private research record'):
            self.assertNotIn(term, core)
        # Owner, 2026-10-14: no AI-agent disclosure sentence on Method; the screening figures name the September 30 draft.
        self.assertNotIn('Research design, rules and review', core)
        self.assertNotIn('a second, independent AI agent', core)
        self.assertIn('revised draft dated September 30, 2026', core)
        self.assertNotIn('September 22', core)
        self.assertIn('No adoption wave', source)
        # R29 trim (TRIM_SPEC_V51.md), the four section 4 fallbacks: ME2 keeps the draft sentence, ME3 the earlier-assessments clause, ME4d the old
        # rule name, ME7a the sentence on what the assessments weigh. The rest is cut as the spec writes it.
        # R30 (owner, 2026-10-15): the access limit is said once, in the Sources box of Screening. V5.1 panel round F1 (2026-10-20): rule 4 says
        # which datasets the rule counts (G2 of the funnel: public_ok or mixed), in place of "another dataset that has not been classified".
        for kept in ('Current figures come from the revised draft dated September 30, 2026.', 'The candidate list uses earlier assessments than the benchmark.',
                     'Data availability: a named dataset must match a public source',
                     'Assessments consider the research question, changes in policy exposure, potential comparison groups and available outcome data.'):
            self.assertIn(kept, source)
        self.assertIn('alongside a dataset that matches no known source.</li>', source)
        self.assertNotIn('has not been classified', source)
        self.assertNotIn('Access is not verified', source)
        for cut in ('Enacted policies:', 'describes the research', 'published-study benchmark', 'Screening methods and results', 'How estimates are compared'):
            self.assertNotIn(cut, source)
        self.assertIn('href="/viz/en/screening.html#sc-benchmark-title">Screening results</a>', source)
        self.assertIn('>Overview</a>', source)
        # R27: the meta description follows the lead ("study patterns across policies"), at its old length (187 characters since F1 replaced "reproduce published estimates" by "re-estimate published papers"), with US as in the site's other copy.
        description = re.search(r'<meta name="description" content="([^"]*)"', source)[1]
        self.assertEqual(description, "Method for Natural Experiment Scanner: we screen US policy texts for RD and DiD research, re-estimate published papers with independently written code, and study patterns across policies.")
        self.assertEqual(len(description), 187)
        self.assertIn("<p>We screen US policy texts for regression discontinuity (RD)", source)
        self.assertNotIn("U.S. policy texts", source)
        self.assertNotIn('a database of 483,414', description)
        self.assertNotIn('AI assisted', core)
        self.assertNotIn('our own code', core)

    def test_method_screening_counts_remain_localized_and_data_driven(self):
        source, _ = build.load_source("documentation")
        core, slots = build.shared_chrome().detach_slots(source)
        block = re.search(r'<!-- SCREENING:START -->(.*?)<!-- SCREENING:END -->', core, re.S)[1]
        self.assertEqual(block.count("<li>"), 5)
        en = build.render_screening_numbers(core)
        self.assertIn("7,781 candidates; 250 have no matched public dataset", en)
        self.assertNotIn("Access is not verified", en)
        errors = []
        pairs = build.load_pairs(build.I18N / "documentation.zh.json", errors)
        zh, _ = build.apply_pairs(core, build.shared_chrome().body_pairs(pairs, core, slots), "documentation", errors)
        self.assertEqual(errors, [])
        zh = build.render_screening_numbers(zh)
        self.assertIn("7,781 条候选；250 条无对应的公开数据集", zh)
        # V5.1 panel round F1: the Chinese rule 4 says the same as the English one (a dataset on neither the public nor the restricted list).
        self.assertIn("在列出受限数据的同时，列有一个不对应任何已知来源的数据集。</li>", zh)
        self.assertNotIn("尚未分类", zh)
        self.assertNotIn("数据访问尚未核实", zh)
        self.assertNotIn("{{screening_", zh)
        with tempfile.TemporaryDirectory() as temp:
            data = json.loads((build.DEPLOY / "funnel_data.json").read_text())
            data["notes"]["final_stage_with_zero_public_dataset_matches"] = 123
            (Path(temp) / "funnel_data.json").write_text(json.dumps(data))
            with patch.object(build, "DEPLOY", Path(temp)):
                self.assertIn("123 have no matched public dataset", build.render_screening_numbers(core))

    def test_isolated_home_preview_refreshes_both_stories_without_writing_source(self):
        chrome = build.shared_chrome()
        source = ('<!doctype html><html lang="en"><head>' + chrome.marked("style") +
                  '</head><body>' + chrome.marked("header") + chrome.marked("nav") +
                  '<!-- STORY:START --><div id="old-story">Old</div><!-- STORY:END -->' +
                  chrome.marked("footer") + chrome.marked("motion") + '</body></html>')
        def story(lang):
            return '<!-- STORY:START --><div id="current-story">%s</div><!-- STORY:END -->' % ("Current" if lang == "en" else "当前")
        with tempfile.TemporaryDirectory() as temp:
            production = Path(temp) / "production"
            production.mkdir()
            (production / "policy_db.en.html").write_text(source)
            preview = Path(temp) / "preview"
            with patch.object(build, "HERE", production), patch.object(build, "load_pairs", return_value=[]), \
                 patch.object(build, "render_story", side_effect=story):
                self.assertEqual(build.main(["--pages", "policy_db", "--output-dir", str(preview)]), 0)
                self.assertEqual(build.main(["--pages", "policy_db", "--check"]), 0)
            self.assertEqual((production / "policy_db.en.html").read_text(), source)
            for path in (preview / "policy_db.html", preview / "zh/policy_db.html"):
                self.assertIn('id="current-story"', path.read_text())
                self.assertNotIn('id="old-story"', path.read_text())

    def test_groups_existing_api_paths_and_default_request_state_preserved(self):
        old = (BASELINE / "policy_groups.html").read_text()
        new = GROUPS.read_text()
        self.assertEqual(Counter(build.STRUCT["/api/ strings"].findall(new)), Counter(build.STRUCT["/api/ strings"].findall(old)))
        state = r'const state = (\{[^;]+\});'
        self.assertEqual(re.search(state, new)[1], re.search(state, old)[1])

    def test_all_records_trim_keeps_accessible_names_and_drops_first_and_last(self):
        # R29 (TRIM_SPEC_V51.md, AR1 to AR9): no lede and no link to Groups, no visible "Per page" label (the select keeps its name as an
        # aria-label in both languages), no First / Last buttons (the pager script no longer touches them), a plain row count.
        source = (ROOT / "scripts/zh_pages/policy_db.en.html").read_text()
        pairs = json.loads((build.I18N / "policy_db.zh.json").read_text())
        for gone in ('<p class="db-lede">', 'Browse policy groups', '<label>Per page</label>', 'btnFirst', 'btnLast', '>First<', '>Last<', '>RD Only<', '>DiD Only<', 'Bill ID <span'):
            self.assertNotIn(gone, source)
        self.assertIn('<select id="pageSizeSelect" aria-label="Per page">', source)
        self.assertIn(['aria-label="Per page"', 'aria-label="每页"'], pairs)
        self.assertIn('start+"\u2013"+end+" of "+total.toLocaleString();', source)
        self.assertIn('placeholder="Search ID, title, treatment, outcomes, datasets..."', source)
        for pair in pairs:
            en, zh = pair if isinstance(pair, list) else (pair["en"], pair["zh"])
            for old in ("Search and sort all policy records.", "<label>Per page</label>", ">First</button>", ">Last</button>", "Browse policy groups"):
                self.assertNotIn(old, en)
            self.assertNotIn("综合得分<span", zh)

    def test_group_copy_is_plain_and_original_invitation_preserved(self):
        old = (BASELINE / "policy_groups.html").read_text()
        new = GROUPS.read_text()
        warning = r'<div class="warning-callout">(.*?)</div>'
        self.assertEqual(re.search(warning, new, re.S)[1], "Design suggestions are AI-generated.")
        subtitle = r'<p class="subtitle">(.*?)</p>'
        self.assertEqual(re.search(subtitle, new, re.S)[1], "Related policy texts across jurisdictions")
        for path in (GROUPS, DOCS):
            text = path.read_text()
            self.assertIn("Open to any collaborations and PhD opportunities!", text)
            self.assertIn('href="mailto:fangyu@usc.edu"', text)
        self.assertNotIn("Work in progress: this documentation", DOCS.read_text())

    def test_static_reference_navigation_and_motion_fallback_contract(self):
        docs = DOCS.read_text()
        sidebar = re.search(r'<nav class="sidebar" id="sidebar">(.*?)</nav>', docs, re.S)[1]
        links = re.findall(r'<a href="#([^"]+)"([^>]*)>', sidebar)
        self.assertEqual(len(links), 7)
        self.assertNotIn("hidden", sidebar)
        ids = set(re.findall(r'\bid="([^"]+)"', docs))
        self.assertTrue(all(target in ids for target, _ in links))
        self.assertIn("data-nx-anchor-toolbar", docs)
        self.assertNotRegex(docs, r'\.sidebar\s*\{[^}]*display\s*:\s*none')
        self.assertNotRegex(docs, r'\.sub-link\s*\{[^}]*display\s*:\s*none')
        for source in (docs, GROUPS.read_text()):
            self.assertNotRegex(source, r'transition\s*:\s*all\b')
            self.assertNotRegex(page_script(DOCS if source is docs else GROUPS), r"behavior\s*:\s*['\"]smooth")
            html_tag = re.search(r'<html\b[^>]*>', source)[0]
            self.assertNotIn("nx-motion", html_tag)


class KeyboardFocusTests(unittest.TestCase):
    """R27: the post-launch pixel check found no ring on the Groups headers (the card clips what lies outside the header) and half a
    ring on the Method sidebar links (the sidebar clips it). The ring is drawn inside the element, in the site's focus colour."""
    FOCUS = "outline:2pxsolid#1d4ed8"  # whitespace is stripped from a rule before it is compared

    def rule(self, source, selector):
        found = re.findall(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", source)
        self.assertEqual(len(found), 1, selector)
        return re.sub(r"\s+", "", found[0])

    def test_groups_headers_draw_their_focus_ring_inside_the_card(self):
        source = GROUPS.read_text()
        self.assertIn(":focus-visible { outline: 2px solid #1d4ed8; outline-offset: 2px; }", source)
        # The reason for an inset ring: the card clips, and the header fills the card.
        self.assertIn("overflow: hidden", self.rule_text(source, ".group-card"))
        ring = self.rule(source, ".group-header:focus-visible")
        self.assertIn(self.FOCUS + ";", ring)
        self.assertIn("outline-offset:-2px", ring)
        # The ring follows the card's rounded corners (8px less its 1px border), so no corner of it is clipped.
        self.assertIn("border-radius:7px", ring)
        self.assertIn("border-radius:7px7px00", self.rule(source, '.group-header[aria-expanded="true"]:focus-visible'))

    def rule_text(self, source, selector):
        return re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", source)[1]

    def test_method_sidebar_links_draw_their_focus_ring_inside_the_link(self):
        source = DOCS.read_text()
        self.assertIn("overflow-y:auto", self.rule_text(source, ".sidebar"))
        ring = self.rule(source, ".sidebar a:focus-visible,.sidebar-overlay a:focus-visible")
        self.assertIn(self.FOCUS + ";", ring)
        self.assertIn("outline-offset:-2px", ring)

    def test_both_languages_get_the_same_rings(self):
        for page, selector in (("policy_groups", ".group-header:focus-visible"), ("documentation", ".sidebar a:focus-visible")):
            source, _ = build.load_source(page)
            self.assertIn(selector, source)
            self.assertIn("outline-offset:-2px", re.sub(r"\s+", "", source[source.index(selector):source.index(selector) + 200]))


class CopyEditsR27Tests(unittest.TestCase):
    """R27: arbiter O1 (Groups warning) and copy gate O4 (the All records reset tooltip) must hold in the template, the dictionary and the shared rewrite."""
    def test_groups_warning_keeps_the_label_and_not_the_advice_in_every_place_it_is_written(self):
        from scripts.viz_site import public_page
        pairs = json.loads((build.I18N / "common.zh.json").read_text())
        pair = next(p for p in pairs if p[0].startswith("Design suggestions"))
        self.assertEqual(pair, ["Design suggestions are AI-generated.", "设计建议由 AI 生成。"])
        for lang, text in (("en", "Design suggestions are AI-generated."), ("zh", "设计建议由 AI 生成。")):
            source = f'<html lang="{"zh-CN" if lang == "zh" else "en"}"><body><div class="warning-callout">anything</div></body></html>'
            self.assertIn(f'<div class="warning-callout">{text}</div>', public_page.reader_chrome(source))
        self.assertNotIn("check the original sources", GROUPS.read_text())
        self.assertNotIn("请核对原始资料", (build.I18N / "common.zh.json").read_text())

    def test_all_records_reset_tooltip_says_filters_in_chinese(self):
        text = (build.I18N / "policy_db.zh.json").read_text()
        self.assertIn('title=\\"重置所有筛选\\">重置</button>', text)
        self.assertNotIn("重置所有筛查", text)


class EvaluationFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with figures.APPENDIX.open(newline="", encoding="utf-8") as handle:
            cls.rows = list(csv.DictReader(handle))
        with figures.LABELS.open(newline="", encoding="utf-8") as handle:
            cls.names = {r["model"]: r["label"] for r in csv.DictReader(handle)}

    def test_all_700_stored_score_observations_and_seven_model_identities(self):
        rendered = figures.render()
        models = re.findall(r'<div class="eval-model" data-model="([^"]+)">(.*?)(?=<div class="eval-model"|<div class="eval-axis")', rendered, re.S)
        self.assertEqual([m for m, _ in models], list(figures.MODELS))
        self.assertEqual(len(self.rows), 100)
        self.assertEqual(len(re.findall(r'data-score="', rendered)), 700)
        for model, block in models:
            self.assertIn(self.names[model], unescape(block))
            plotted = [float(v) for v in re.findall(r'data-score="([^"]+)"', block)]
            self.assertEqual(plotted, [float(r[model + "_composite_score"]) for r in self.rows])
            self.assertRegex(block, r'<svg\b[^>]*width="\d+"[^>]*height="\d+"[^>]*viewBox=')
            self.assertIn('<path d="M ', block)
            rug = re.search(r'<svg class="eval-rug"([^>]*)>(.*?)</svg>', block, re.S)
            self.assertNotIn("viewBox", rug[1])
            self.assertIn('height="24"', rug[1])
            dots = re.findall(r'<circle cx="([\d.]+)%" cy="([\d.]+)" r="([\d.]+)"', rug[2])
            self.assertEqual(len(dots), len(self.rows))
            self.assertTrue(all(radius == "1.7" for _, _, radius in dots))
            for (x, y, _), score in zip(dots, plotted):
                self.assertAlmostEqual(float(x), (12 + (score - 1) * 154) / 640 * 100, places=5)
                self.assertIn(float(y), (6, 12, 18))
        self.assertEqual(rendered.count('class="eval-ticks"'), 1)

    def test_five_model_matrix_has_every_rate_and_original_diagonal(self):
        rendered = figures.render()
        matrix = re.search(r'<!-- EVAL:AGREEMENT:START -->(.*?)<!-- EVAL:AGREEMENT:END -->', rendered, re.S)[1]
        cells = re.findall(r'<td data-pair="([^|]+)\|([^"]+)" data-rate="([^"]+)"[^>]*>([^<]+)</td>', matrix)
        self.assertEqual(len(cells), 25)
        self.assertEqual({a for a, _, _, _ in cells}, set(figures.AGREEMENT_MODELS))
        self.assertEqual({b for _, b, _, _ in cells}, set(figures.MODELS[:5]))
        for a, b, rate, display in cells:
            expected = sum((r[a + "_design_type"].strip().upper() or "NONE") ==
                           (r[b + "_design_type"].strip().upper() or "NONE") for r in self.rows) / len(self.rows)
            self.assertAlmostEqual(float(rate), expected)
            self.assertEqual(display, f"{expected:.2f}")
            if a == b:
                self.assertEqual(display, "1.00")
        for model in figures.AGREEMENT_MODELS:
            self.assertEqual(unescape(matrix).count(self.names[model]), 2)
        for model in figures.MODELS[5:]:
            self.assertNotIn(self.names[model], matrix)

    def test_internal_partial_remains_current_without_public_embedding(self):
        expected = figures.render()
        self.assertEqual(figures.OUTPUT.read_text(), expected)
        for name in ("DISTRIBUTION", "AGREEMENT"):
            pattern = r'<!-- EVAL:' + name + r':START -->.*?<!-- EVAL:' + name + r':END -->'
            self.assertFalse(re.findall(pattern, DOCS.read_text(), re.S))
        before = {p: p.read_bytes() for p in (figures.OUTPUT, DOCS)}
        self.assertEqual(figures.main(["--check"]), 0)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_missing_numerical_source_fails_without_generating_a_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "absent.csv"
            with self.assertRaisesRegex(ValueError, "missing local numerical source.*absent.csv"):
                figures.load_data(appendix=absent)
            with self.assertRaisesRegex(ValueError, "missing local numerical source.*absent.csv"):
                figures.load_data(labels=absent)
            with patch.object(figures, "load_data", side_effect=ValueError("missing local numerical source: absent.csv")), patch.object(figures, "OUTPUT", Path(tmp) / "figures.html"):
                self.assertEqual(figures.main([]), 1)
                self.assertFalse(figures.OUTPUT.exists())

    def test_check_rejects_stale_partial_and_stale_embedded_block_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            output = directory / "eval_figures.html"
            canonical = directory / "documentation.en.html"
            output.write_text(figures.render())
            canonical.write_text(DOCS.read_text() + figures.render().replace("Composite Score Distribution by Model", "Changed label", 1))
            with patch.object(figures, "HERE", directory), patch.object(figures, "OUTPUT", output):
                before = output.read_bytes(), canonical.read_bytes()
                self.assertEqual(figures.main(["--check"]), 1)
                self.assertEqual((output.read_bytes(), canonical.read_bytes()), before)
                canonical.write_text(DOCS.read_text())
                output.write_text("stale")
                self.assertEqual(figures.main(["--check"]), 1)
                self.assertEqual(output.read_text(), "stale")

    def test_renderer_imports_only_local_presentation_dependencies(self):
        tree = ast.parse(Path(figures.__file__).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, {"__future__", "argparse", "csv", "html", "math", "re", "pathlib", "sys"})
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertFalse(calls & {"eval", "exec", "__import__", "compile"})


NODE_COMMON = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
class Element {
  constructor(attrs={}) { this.attrs={...attrs}; this.style={removeProperty(k){delete this[k]}}; this.hidden=false; this.offsetHeight=44; this.offsetTop=0; this.dataset={}; this.listeners={}; this._html=''; this.textContent=''; this.top=0; this.children=[]; this.value=''; this.focused=false;
    this.classList={values:new Set((attrs.class||'').split(/\s+/)),add(x){this.values.add(x)},remove(x){this.values.delete(x)},contains(x){return this.values.has(x)},toggle(x,on){if(on===undefined)on=!this.values.has(x);on?this.values.add(x):this.values.delete(x)}};
  }
  get hash(){return this.attrs.href||''} get innerHTML(){return this._html||this.textContent} set innerHTML(v){this._html=v}
  get firstChild(){return this.children[0]||null} appendChild(el){this.children.push(el);return el}
  getAttribute(k){return this.attrs[k]??null} setAttribute(k,v){this.attrs[k]=v} removeAttribute(k){delete this.attrs[k]}
  addEventListener(k,f){(this.listeners[k]??=[]).push(f)} fire(k,e={}){for(const f of this.listeners[k]||[])f(e)}
  getBoundingClientRect(){return {top:this.top,height:44}} focus(){this.focused=true} contains(el){return el===this||this.children.includes(el)}
  scrollIntoView(opts){this.scrolled=opts}
  querySelector(sel){if(sel==='.loading, [data-retry]')return this._html.includes('class="loading"')?new Element():null; if(sel==='.grp-arrow')return new Element();return null}
  querySelectorAll(){return []}
}
const elements={}, events={}, winEvents={}, requests=[];
const document={documentElement:new Element(),getElementById(id){return elements[id]??=new Element()},createElement(){return new Element()},querySelector(s){return s==='.summary-row'?(elements.summaryRow??=new Element()):null},addEventListener(k,f){(events[k]??=[]).push(f)},querySelectorAll(){return []}};
const window={addEventListener(k,f){(winEvents[k]??=[]).push(f)},location:{hash:''}};
const context={document,window,location:window.location,console,URLSearchParams,Map,Set,Object,Math,Number,parseFloat,isNaN,String,Array,innerWidth:390,history:{replaceState(a,b,hash){window.location.hash=hash}},getComputedStyle(){return {getPropertyValue(){return '56'}}},requestAnimationFrame(f){f(0)},setTimeout(f){f();return 1},clearTimeout(){}};
context.fetch=async url=>{requests.push(url);return {ok:true,json:async()=>input.fixtures[url]??input.detail}};
'''


@unittest.skipUnless(shutil.which("node"), "Node required for page-script behaviour")
class OriginalInteractionTests(unittest.TestCase):
    def run_node(self, path, setup, checks, **data):
        payload = {"source": page_script(path), "fixtures": {}, **data}
        code = NODE_COMMON + setup + "\nvm.createContext(context);\nvm.runInContext(input.source+'\\n'+input.checks,context);"
        payload["checks"] = checks
        result = subprocess.run(["node", "-e", code], input=json.dumps(payload), text=True, capture_output=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_groups_native_button_expansion_and_api_driven_counts_pagination(self):
        setup = "context.assert=assert; context.input=input; context.requests=requests; context.elements=elements;"
        checks = r'''
        (async()=>{
          renderGroupsList({totals:{groups:43,policies:8765},total:43,groups:[{family:'labor',display_name:'Labor',count:23,jurisdictions:7,has_rd:1,has_did:1}]});
          assert.equal(elements.countGroups.textContent,'43'); assert.equal(elements.countPolicies.textContent,'8,765');
          const html=elements.groupsContainer.innerHTML;
          assert.match(html,/<button type="button" class="group-header"[^>]*aria-expanded="false"[^>]*aria-controls="grp-body-labor"/);
          assert.match(html,/onclick="toggleGroup\(this.dataset.family, this\)"/); assert.match(html,/23 texts/); assert.match(html,/7 jurisdictions/); assert.match(html,/RD\/DiD/);
          assert.match(elements.groupsPagination.innerHTML,/page-info">1 \/ 3</);
          const header=new elements.countGroups.constructor(); header.dataset.family='labor'; header.setAttribute('aria-expanded','false');
          const body=elements['grp-body-labor']=new elements.countGroups.constructor(); body.hidden=true; body.innerHTML='<p class="loading">Loading...</p>';
          // Native button semantics provide Enter/Space activation. Exercise
          // that button's exact click action twice; real key dispatch is a
          // browser acceptance check, not something this DOM mock simulates.
          assert.ok(!/onkey(?:down|up)=/.test(html));
          await toggleGroup(header.dataset.family,header);await toggleGroup(header.dataset.family,header);
          assert.equal(header.getAttribute('aria-expanded'),'false');assert.equal(body.hidden,true);
          await toggleGroup('labor',header); assert.equal(header.getAttribute('aria-expanded'),'true');assert.equal(body.hidden,false);
          assert.match(body.innerHTML,/page-info">1 \/ 2</);assert.match(body.innerHTML,/3\.7/);
          assert.equal(requests.filter(u=>u.includes('/api/policy-groups/labor?')).length,1);
          state.page=3;renderGroupsPagination({total:43});assert.match(elements.groupsPagination.innerHTML,/disabled onclick="goGroupsPage\(4\)"/);
          state.page=2;state.q='paid leave';state.topic='labor_market';await fetchGroupsList();
          const q=new URLSearchParams(requests.at(-1).split('?')[1]);assert.equal(q.get('q'),'paid leave');assert.equal(q.get('page'),'2');assert.equal(q.get('size'),'20');assert.equal(q.get('topic'),'labor_market');
          assert.equal(countJurisdictions(['GA State','Georgia State','DC','US Federal']),3);
          await fetchTopics();assert.equal(elements.countJurs.textContent,'3');assert.equal(elements.topicSelect.children[0].value,'labor_market');
          console.log('groups behavioural assertions passed');
        })().catch(e=>{console.error(e);throw e});
        '''
        self.run_node(GROUPS, setup, checks, detail={"rows": [{"title": "Policy", "jurisdiction": "GA", "date": "2020-01-01", "composite_score": 3.7, "rd": 1}], "total": 23, "size": 20, "page": 1}, fixtures={"/api/filters": {"topics": ["labor_market"], "jurisdictions": ["GA State", "Georgia State", "DC", "US Federal"]}, "/api/policy-groups?page=2&size=20&topic=labor_market&q=paid+leave": {"groups": [], "total": 0, "size": 50, "totals": {"groups": 0, "policies": 0}}})

    def test_groups_first_page_server_order_and_on_demand_page_cache(self):
        setup = r'''
        context.assert=assert;context.requests=requests;
        context.fetch=async url=>{
          requests.push(url);
          const params=new URLSearchParams(url.split('?')[1]);
          const page=Number(params.get('page')),size=Number(params.get('size'));
          const all=Array.from({length:8825},(_,i)=>({family:'family_'+i,display_name:'Group '+i,count:100-i,jurisdictions:i<50?1:5}));
          return {ok:true,json:async()=>({groups:all.slice((page-1)*size,page*size),total:8825,size,page,totals:{groups:8825,policies:141463}})};
        };
        '''
        checks = r'''
        (async()=>{
          state.size=2;
          const first=await fetchGroupsList();
          assert.deepEqual(Array.from(first.groups,g=>g.family),['family_0','family_1']);
          assert.equal(first.total,8825);assert.equal(requests.length,1);
          state.page=2;
          const second=await fetchGroupsList();
          assert.deepEqual(Array.from(second.groups,g=>g.family),['family_2','family_3']);assert.equal(requests.length,2);
          state.page=1;await fetchGroupsList();assert.equal(requests.length,2);
          state.topic='labor_market';state.q='leave';await fetchGroupsList();assert.equal(requests.length,3);
          assert.ok(requests.at(-1).includes('topic=labor_market')&&requests.at(-1).includes('q=leave'));
        })().catch(e=>{console.error(e);throw e});
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_deep_link_loaded_page_and_unknown_target_fallback(self):
        setup = r'''
        context.assert=assert;context.elements=elements;context.requests=requests;
        const header=new Element();header.setAttribute('aria-expanded','false');
        const card=new Element();card.querySelector=()=>header;
        context.header=header;context.card=card;
        document.getElementById=id=>id==='grp-target'?card:(elements[id]??=new Element());
        window.scrollTo=(x,y)=>window.scrollPosition=[x,y];
        context.fetch=async url=>{
          requests.push(url);
          const page=Number(new URLSearchParams(url.split('?')[1]).get('page'));
          return {ok:true,json:async()=>({groups:[{family:page===3?'target':'family_'+page,display_name:'Group',count:10,jurisdictions:1}],total:53,size:20,page,totals:{groups:53,policies:3000}})};
        };
        const body=elements['grp-body-target']=new Element();body.hidden=true;
        '''
        checks = r'''
        (async()=>{
          await loadGroups();state.page=3;await loadGroups();state.page=1;await loadGroups();
          window.location.hash='#grp-target';await resolveDeepLink();
          assert.equal(state.page,3);assert.equal(requests.length,2);assert.match(elements.groupsPagination.innerHTML,/page-info">3 \/ 3</);
          assert.match(elements.groupsContainer.innerHTML,/grp-target/);
          assert.equal(header.getAttribute('aria-expanded'),'true');assert.equal(card.scrolled.behavior,'auto');
          await resolveDeepLink();assert.equal(header.getAttribute('aria-expanded'),'true');
          const html=elements.groupsContainer.innerHTML;
          window.location.hash='#grp-missing';await resolveDeepLink();
          assert.equal(elements.groupsContainer.innerHTML,html);assert.deepEqual(window.scrollPosition,[0,0]);assert.equal(requests.length,2);
          window.location.hash='#grp-%zz';await resolveDeepLink();assert.equal(elements.groupsContainer.innerHTML,html);
        })().catch(e=>{console.error(e);throw e});
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_failed_page_preserves_list_pagination_and_retries(self):
        setup = r'''
        context.assert=assert;context.elements=elements;context.requests=requests;
        let fail=true;
        context.fetch=async url=>{
          requests.push(url);const page=Number(new URLSearchParams(url.split('?')[1]).get('page'));
          if(page===2&&fail){fail=false;return {ok:false,status:429}};
          return {ok:true,json:async()=>({groups:[{family:'family_'+page,display_name:'Group',count:10,jurisdictions:1}],total:53,size:20,page,totals:{groups:53,policies:3000}})};
        };
        '''
        checks = r'''
        (async()=>{
          await loadGroups();const html=elements.groupsContainer.innerHTML,pagination=elements.groupsPagination.innerHTML;
          state.page=2;assert.equal(await loadGroups(),false);
          assert.equal(elements.groupsContainer.innerHTML,html);assert.equal(elements.groupsPagination.innerHTML,pagination);assert.equal(state.page,1);
          state.page=2;assert.equal(await loadGroups(),true);assert.match(elements.groupsContainer.innerHTML,/grp-family_2/);assert.equal(requests.length,3);
          const body=elements['grp-body-family_2']=new elements.countGroups.constructor();body.innerHTML='<table><tr><td>Existing detail</td></tr></table>';
          const old=body.innerHTML;fetch=async()=>({ok:false,status:429});await goGroupPage('family_2',2);assert.equal(body.innerHTML,old);
        })().catch(e=>{console.error(e);throw e});
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_late_query_response_does_not_replace_latest_filter(self):
        setup = r'''
        const waiting=[];
        context.fetch=async url=>new Promise(resolve=>waiting.push({url,resolve}));
        context.assert=assert;context.elements=elements;context.waiting=waiting;
        '''
        checks = r'''
        (async()=>{
          const response=name=>({ok:true,json:async()=>({groups:[{family:name,display_name:name,count:1,jurisdictions:1}],total:1,size:50,totals:{groups:1,policies:1}})});
          state.q='old';const old=loadGroups();
          state.q='new';const latest=loadGroups();
          waiting[1].resolve(response('new'));await latest;
          assert.match(elements.groupsContainer.innerHTML,/grp-new/);
          waiting[0].resolve(response('old'));await old;
          assert.match(elements.groupsContainer.innerHTML,/grp-new/);assert.ok(!elements.groupsContainer.innerHTML.includes('grp-old'));
        })().catch(e=>{console.error(e);throw e});
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_reduced_motion_keeps_final_values_and_first_arrival_runs_once(self):
        setup = r'''
        const motionCalls=[];
        context.NXMotion=window.NXMotion={allowed(){return false},once(el,run,opts){motionCalls.push(['once',el]);opts.finish();return {finish:opts.finish}},transition(el,run,opts){motionCalls.push(['transition',el]);opts.finish();return {finish:opts.finish}}};
        context.assert=assert;context.elements=elements;context.motionCalls=motionCalls;
        '''
        checks = r'''
        arriveCount('countGroups',1234);arriveCount('countGroups',4567);
        assert.equal(elements.countGroups.textContent,'4,567');assert.equal(motionCalls.filter(c=>c[0]==='once').length,1);
        const body=new elements.countGroups.constructor(), header=new elements.countGroups.constructor();body.hidden=true;
        setGroupOpen(body,header,true);assert.equal(body.hidden,false);assert.equal(header.getAttribute('aria-expanded'),'true');
        setGroupOpen(body,header,false);assert.equal(body.hidden,true);assert.equal(header.getAttribute('aria-expanded'),'false');
        assert.equal(body.style.height,undefined);assert.equal(body.style.overflow,undefined);
        swapContent(body,'<p>Final content</p>');assert.equal(body.innerHTML,'<p>Final content</p>');
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_all_first_arrivals_share_one_clock_and_latest_query_wins(self):
        setup = r'''
        const intros=[], frames=[];
        let finished=false;
        context.NXMotion=window.NXMotion={
          once(el,run,opts){const h={finish(){if(!finished){finished=true;opts.finish()}}};intros.push({el,run,opts,h});return h;}
        };
        context.assert=assert;context.elements=elements;context.intros=intros;context.frames=frames;
        context.startIntro=()=>intros[0].run({raf(f){frames.push(f)},finish(){intros[0].h.finish()}});
        '''
        checks = r'''
        arriveCount('countGroups',80);arriveCount('countPolicies',8000);
        assert.equal(intros.length,1);assert.equal(intros[0].el,elements.summaryRow);
        assert.notEqual(elements.countGroups.textContent,'80');
        startIntro();frames.shift()(100);frames.shift()(400);
        assert.equal(elements.countGroups.textContent,'70');assert.equal(elements.countPolicies.textContent,'7,000');
        // A delayed first API value joins the same frame and never queues a second intro.
        arriveCount('countJurs',40);assert.equal(elements.countJurs.textContent,'35');assert.equal(intros.length,1);
        frames.shift()(700);
        assert.equal(elements.countGroups.textContent,'80');assert.equal(elements.countPolicies.textContent,'8,000');assert.equal(elements.countJurs.textContent,'40');
        arriveCount('countGroups',11);arriveCount('countPolicies',1100);
        assert.equal(intros.length,1);assert.equal(elements.countGroups.textContent,'11');assert.equal(elements.countPolicies.textContent,'1,100');
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_pending_intro_cleanup_commits_available_values_only(self):
        setup = r'''
        let finalizer;
        elements.countJurs=new Element();elements.countJurs.textContent='—';
        context.NXMotion=window.NXMotion={once(el,run,opts){finalizer=opts.finish;return {finish:opts.finish}}};
        context.assert=assert;context.elements=elements;context.finishPending=()=>finalizer();
        '''
        checks = r'''
        arriveCount('countGroups',1234);arriveCount('countPolicies',9876);
        finishPending();assert.equal(elements.countGroups.textContent,'1,234');assert.equal(elements.countPolicies.textContent,'9,876');assert.equal(elements.countJurs.textContent,'—');
        arriveCount('countJurs',51);assert.equal(elements.countJurs.textContent,'51');
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_collapse_reaches_zero_box_and_late_rows_keep_closed_state(self):
        setup = r'''
        const transitions=[];
        context.getComputedStyle=()=>({getPropertyValue(){return '11.2px'}});
        context.NXMotion=window.NXMotion={allowed(){return true},transition(el,run,opts){
          const record={frames:[],finish:opts.finish};transitions.push(record);
          run({animate(target,frames){record.frames.push(frames)},timer(){},finish:opts.finish});return record;
        }};
        context.assert=assert;context.elements=elements;context.transitions=transitions;
        '''
        checks = r'''
        const body=document.createElement('div'),header=document.createElement('button');
        body.animate=()=>{};body.getBoundingClientRect=()=>({height:122.4});
        setGroupOpen(body,header,false);
        // Padding must shrink with the border-box height: otherwise the box
        // clamps to its two paddings and leaves the review's closing strip.
        const closing=transitions[0].frames[0];
        assert.equal(closing[0].height,'122.4px');assert.equal(closing[0].paddingTop,'11.2px');
        assert.equal(closing[1].height,'0px');assert.equal(closing[1].paddingTop,'0px');assert.equal(closing[1].paddingBottom,'0px');
        transitions[0].finish();assert.equal(body.hidden,true);assert.equal(header.getAttribute('aria-expanded'),'false');
        replaceGroupDetail(body,'<table><tr><td>Late response</td></tr></table>');
        assert.equal(body.hidden,true);assert.equal(transitions.length,1);assert.match(body.innerHTML,/Late response/);
        setGroupOpen(body,header,true);setGroupOpen(body,header,false);setGroupOpen(body,header,true);
        transitions.at(-1).finish();assert.equal(body.hidden,false);assert.equal(header.getAttribute('aria-expanded'),'true');
        assert.equal(body.style.height,undefined);assert.equal(body.style.overflow,undefined);
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_placeholder_rows_resize_without_second_count_intro(self):
        setup = r'''
        const transitions=[];
        context.NXMotion=window.NXMotion={allowed(){return true},transition(el,run,opts){
          const record={frames:[],finish:opts.finish};transitions.push(record);
          run({animate(target,frames){record.frames.push(frames)},timer(){},finish:opts.finish});return record;
        }};
        context.assert=assert;context.elements=elements;context.transitions=transitions;
        '''
        checks = r'''
        const body=document.createElement('div');body.animate=()=>{};
        body.innerHTML='<p class="loading">Loading...</p>';
        body.getBoundingClientRect=()=>({height:body.innerHTML.includes('loading')?112:798});
        replaceGroupDetail(body,'<table><tr><td>Arriving rows</td></tr></table>');
        assert.deepEqual(transitions[0].frames[0],[{height:'112px'},{height:'798px'}]);
        transitions[0].finish();assert.equal(body.hidden,false);assert.match(body.innerHTML,/Arriving rows/);
        assert.equal(body.style.height,undefined);assert.equal(body.style.overflow,undefined);
        window.NXMotion.allowed=()=>false;
        replaceGroupDetail(body,'<table><tr><td>Reduced rows</td></tr></table>');
        assert.equal(transitions.length,1);assert.match(body.innerHTML,/Reduced rows/);
        '''
        self.run_node(GROUPS, setup, checks)

    def test_groups_errors_do_not_expose_parser_details_or_lose_final_counts(self):
        setup = "context.assert=assert; context.elements=elements; context.fetch=async()=>{throw Error('Unexpected token SECRET-PARSER');};"
        checks = r'''
        (async()=>{
          arriveCount('countGroups',1234);arriveCount('countGroups',987);assert.equal(elements.countGroups.textContent,'987');
          await loadGroups();assert.match(elements.groupsContainer.innerHTML,/Failed to load groups/);assert.ok(!elements.groupsContainer.innerHTML.includes('SECRET-PARSER'));
          renderGroupsList({totals:{groups:0,policies:0},total:0,groups:[]});assert.equal(elements.countGroups.textContent,'0');assert.equal(elements.groupsPagination.style.display,'none');
          assert.match(elements.groupsContainer.innerHTML,/No groups match/);
        })().catch(e=>{console.error(e);throw e});
        '''
        self.run_node(GROUPS, setup, checks)

    def test_docs_deepest_leaf_parent_menu_escape_outside_click_and_anchor(self):
        docs = DOCS.read_text()
        sidebar_html = re.search(r'<nav class="sidebar" id="sidebar">(.*?)</nav>', docs, re.S)[1]
        links = [{"href": "#" + target, "data-section": re.search(r'data-section="([^"]+)"', attrs)[1]}
                 for target, attrs in re.findall(r'<a href="#([^"]+)"([^>]*)>', sidebar_html)]
        setup = r'''
        const makeLinks=()=>input.links.map((a,i)=>{const l=new Element(a);l.offsetTop=i*44;return l});
        const links=makeLinks(), mobile=makeLinks(), sidebar=elements.sidebar=new Element(), menu=elements.sidebarMobile=new Element(), button=new Element(), toolbar=new Element();menu.hidden=true;toolbar.children.push(button,menu);
        sidebar.querySelectorAll=()=>links;menu.querySelectorAll=s=>s==='[id]'?[]:mobile;menu.children.push(...mobile);toolbar.querySelector=()=>button;
        document.querySelector=s=>s==='.docs-toolbar'?toolbar:null;
        document.querySelectorAll=s=>s==='a[href^="#"]'?[...links,...mobile]:[];
        document.createElement=()=>{const el=new Element();el._html=input.sidebar;return el};
        input.links.forEach((a,i)=>{const el=elements[a.href.slice(1)]=new Element();el.id=a.href.slice(1);el.top=i*120;el.heading=new Element();el.querySelector=()=>el.heading;});
        document.getElementById=id=>elements[id]||null;
        const transitions=[];
        context.NXMotion=window.NXMotion={transition(el,run,opts){transitions.push(el);opts.finish();return {finish:opts.finish}},landing(){}};
        context.transitions=transitions;context.innerHeight=900;window.scrollY=0;document.scrollingElement={scrollTop:0,scrollHeight:2040};
        context.assert=assert;context.input=input;context.elements=elements;context.links=links;context.mobile=mobile;context.button=button;context.toolbar=toolbar;context.menu=menu;context.events=events;context.winEvents=winEvents;
        '''
        checks = r'''
        assert.equal(links.filter(l=>l.getAttribute('aria-current')==='location').length,1);
        assert.equal(transitions.length,0);
        const leaf='scoring-confounds', index=input.links.findIndex(l=>l.href==='#'+leaf);
        input.links.forEach((a,i)=>elements[a.href.slice(1)].top=(i-index)*120);
        winEvents.scroll[0]();
        assert.equal(transitions.length,1);
        assert.equal(links.filter(l=>l.getAttribute('aria-current')==='location').length,1);
        assert.equal(links.find(l=>l.hash==='#'+leaf).getAttribute('aria-current'),'location');
        const parent=links.find(l=>l.hash==='#scoring');assert.ok(parent.classList.contains('parent-current'));assert.equal(parent.getAttribute('aria-current'),null);
        assert.equal(mobile.filter(l=>l.getAttribute('aria-current')==='location').length,1);
        button.fire('click');assert.equal(menu.hidden,false);assert.equal(button.getAttribute('aria-expanded'),'true');
        events.keydown[0]({key:'Escape'});assert.equal(menu.hidden,true);assert.equal(button.getAttribute('aria-expanded'),'false');assert.equal(button.focused,true);
        button.fire('click');events.click[0]({target:new elements.sidebar.constructor()});assert.equal(menu.hidden,true);
        let prevented=false;links.find(l=>l.hash==='#'+leaf).fire('click',{preventDefault(){prevented=true}});
        assert.equal(prevented,true);assert.equal(location.hash,'#'+leaf);assert.equal(elements[leaf].scrolled.behavior,'auto');assert.equal(menu.hidden,true);
        button.fire('click');mobile.find(l=>l.hash==='#'+leaf).fire('click',{preventDefault(){}});
        assert.equal(elements[leaf].heading.getAttribute('tabindex'),'-1');assert.equal(elements[leaf].heading.focused,true);assert.equal(elements[leaf].focused,false);assert.equal(menu.hidden,true);
        button.fire('click');innerWidth=1440;winEvents.resize[0]();assert.equal(menu.hidden,true);assert.equal(button.getAttribute('aria-expanded'),'false');
        // Scrolling to the bottom reaches References, even though its heading
        // cannot rise to the top-offset threshold in a 900px viewport.
        window.scrollY=document.scrollingElement.scrollTop=1140;
        winEvents.scroll[0]();
        const last=input.links.at(-1).href;
        assert.equal(links.find(l=>l.hash===last).getAttribute('aria-current'),'location');
        assert.equal(mobile.find(l=>l.hash===last).getAttribute('aria-current'),'location');
        // All three final sidebar entries remain selected after a clamped
        // click and its queued scroll event, not only the final entry.
        for(const item of input.links.slice(-3)) {
          links.find(l=>l.hash===item.href).fire('click',{preventDefault(){}});
          winEvents.scroll[0]();
          assert.equal(links.find(l=>l.hash===item.href).getAttribute('aria-current'),'location');
          assert.equal(mobile.find(l=>l.hash===item.href).getAttribute('aria-current'),'location');
        }
        // Actual scrolling away releases that clicked selection.
        window.scrollY=document.scrollingElement.scrollTop=100;
        input.links.forEach((a,i)=>elements[a.href.slice(1)].top=(i-index)*120);
        winEvents.scroll[0]();
        assert.equal(links.find(l=>l.hash==='#'+leaf).getAttribute('aria-current'),'location');

        '''
        self.run_node(DOCS, setup, checks, links=links, sidebar=sidebar_html)


if __name__ == "__main__":
    unittest.main()
