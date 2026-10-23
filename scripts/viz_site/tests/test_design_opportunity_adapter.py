"""Content and geometry checks for the timing-map adapter; no browser required."""
import copy
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from scripts.viz_site.design_opportunity_adapter import (
    CELL_WIDTH, DEFAULTS, ROW_HEIGHT, SVG_WIDTH, SVG_HEIGHT, YEARS, normalize_payload,
    render_design_opportunity, render_inventory_rows, render_readout,
)
from scripts.viz_site.validate_design_opportunity import validate_public_fields

ROOT = Path(__file__).resolve().parents[3]


class DesignOpportunityAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads((ROOT / "scripts/viz_site/data/design_opportunity.json").read_text())

    def markup(self, lang="en", payload=None):
        return render_design_opportunity(payload or self.payload, lang)

    def ui(self, markup):
        data = re.search(r'<script type="application/json" id="design-opportunity-data">(.*?)</script>', markup, re.S)
        self.assertIsNotNone(data)
        return json.loads(data.group(1))

    def graphic(self, markup):
        return ET.fromstring(re.search(r'<svg\b.*?</svg>', markup, re.S).group(0))

    def test_grid_has_exactly_eight_by_twenty_two_cells(self):
        grid = self.graphic(self.markup())
        self.assertEqual((grid.attrib["width"], grid.attrib["height"]), (str(SVG_WIDTH), str(SVG_HEIGHT)))
        self.assertNotIn("viewBox", grid.attrib)
        cells = [node for node in grid.iter() if "do-cell" in node.attrib.get("class", "").split()]
        self.assertEqual(len(cells), 8 * 22)
        self.assertEqual(sum(int(node.attrib["data-count"]) for node in cells), 213)
        stops = [cell for cell in cells if cell.attrib["tabindex"] == "0"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].attrib["data-family"], self.payload["family"][0]["id"])
        self.assertEqual(stops[0].attrib["data-year"], "2020")
        for cell in cells:
            self.assertIn(cell.attrib["tabindex"], ("0", "-1"))
            self.assertEqual(cell.attrib["role"], "button")
            self.assertIn(cell.attrib["aria-pressed"], ("true", "false"))
            self.assertIn(cell.attrib["data-year"], cell.attrib["aria-label"])
        for family in self.payload["family"]:
            row = [cell for cell in cells if cell.attrib["data-family"] == family["id"]]
            self.assertEqual([int(cell.attrib["data-year"]) for cell in row], list(YEARS))
        summary = {row["family_id"]: row for row in self.payload["opportunity_summary"]}
        for cell in cells:
            group = next((group for group in summary[cell.attrib["data-family"]]["year_group_sizes"]
                          if group["year"] == int(cell.attrib["data-year"])), None)
            self.assertEqual(int(cell.attrib["data-count"]), group["count"] if group else 0)

    def test_svg_geometry_all_marks_and_labels_inside_canvas(self):
        grid = self.graphic(self.markup())
        self.assertEqual(grid.attrib["role"], "group")
        self.assertIn("design-grid-desc", grid.attrib["aria-labelledby"])
        self.assertGreaterEqual(CELL_WIDTH - 3, 44)
        self.assertEqual(ROW_HEIGHT, 58)
        for node in grid.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "rect":
                coordinate = lambda key: float(node.attrib[key][:-1]) * SVG_WIDTH / 100 if node.attrib[key].endswith("%") else float(node.attrib[key])
                x, y, width, height = (coordinate(key) for key in ("x", "y", "width", "height"))
                self.assertGreaterEqual(x, 0)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(x + width, SVG_WIDTH)
                self.assertLessEqual(y + height, SVG_HEIGHT)
            if tag == "text":
                self.assertGreaterEqual(float(node.attrib["x"].rstrip("%")), 0)
                self.assertLessEqual(float(node.attrib["x"].rstrip("%")), SVG_WIDTH)
                self.assertGreaterEqual(float(node.attrib["y"]), 14)
                self.assertLessEqual(float(node.attrib["y"]), SVG_HEIGHT)
        rows = [node for node in grid.iter() if "do-grid-row" in node.attrib.get("class", "").split()]
        for row in rows:
            bands = [node for node in row if node.attrib.get("class") == "do-rowbg"]
            self.assertEqual(len(bands), 1)
            self.assertEqual(float(bands[0].attrib["height"]), ROW_HEIGHT)
        for index, row in enumerate(rows[:-1]):
            last_text_y = max(float(text.attrib["y"]) for text in row.iter() if text.tag.endswith("text"))
            next_rect_y = min(float(rect.attrib["y"]) for cell in rows[index + 1]
                              if "do-cell" in cell.attrib.get("class", "").split()
                              for rect in cell if rect.tag.endswith("rect"))
            self.assertLess(last_text_y, next_rect_y)

    def test_fixed_columns_and_every_second_tick_keep_all_data(self):
        ns = {'s': 'http://www.w3.org/2000/svg'}
        for lang in ('en', 'zh'):
            html = self.markup(lang)
            grid, labels, totals = [ET.fromstring(svg) for svg in re.findall(r'<svg\b.*?</svg>', html, re.S)]
            ticks = [n for n in grid.iter() if n.get('class') == 'do-year-label']
            self.assertEqual([int(n.get('data-year')) for n in ticks], list(YEARS))
            self.assertEqual(len([n for n in ticks if n.get('data-tick') == 'even']), 11)
            cells = [n for n in grid.iter() if n.get('class') == 'do-cell']
            for cell in cells:
                rect = cell.find('s:rect', ns)
                self.assertGreaterEqual(float(rect.get('width')), 44)
                self.assertGreaterEqual(float(rect.get('height')), 44)
            family_labels = [n.text for n in labels.iter() if n.get('class') == 'do-row-label']
            self.assertEqual(family_labels, [f['label'] for f in self.ui(html)['families']])
            headings = [n for n in totals.iter() if n.get('class') == 'do-summary-heading']
            values = [n for n in totals.iter() if n.get('class') == 'do-grid-summary']
            self.assertEqual(len(values), 24)
            self.assertTrue(all(n.get('text-anchor') == 'end' for n in headings + values))
            self.assertEqual({n.get('x') for n in headings}, {n.get('x') for n in values})
            for family, row in zip(self.ui(html)['families'], [values[i:i+3] for i in range(0, 24, 3)]):
                summary = family['summary']
                self.assertEqual([n.text for n in row], [str(summary['strict_dated_jurisdictions']), str(summary['year_group_count']), f"{100*summary['modal_share']:.1f}%"])
            self.assertIn('do-grid-legend', html)
            self.assertEqual(html.count('class="do-legend-bin"'), 6)

    def test_en_zh_numeric_and_source_parity(self):
        en, zh = (self.ui(self.markup(lang)) for lang in ("en", "zh"))
        self.assertEqual(en["years"], zh["years"])
        for first, second in zip(en["families"], zh["families"]):
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(first["groups"], second["groups"])
            self.assertEqual(first["summary"], second["summary"])
            self.assertEqual(len(first["jurisdictions"]), 51)
            for en_row, zh_row in zip(first["jurisdictions"], second["jurisdictions"]):
                for key in ("code", "year", "date", "precision", "strict", "status", "sources"):
                    self.assertEqual(en_row.get(key), zh_row.get(key))
        self.assertEqual(set(DEFAULTS["en"]), set(DEFAULTS["zh"]))

    def test_nulls_and_statuses_are_preserved_not_controls(self):
        payload = copy.deepcopy(self.payload)
        snapshot = copy.deepcopy(payload)
        ui = normalize_payload(payload, "en")
        for family in ui["families"]:
            self.assertEqual(len(family["jurisdictions"]), 51)
            for row in family["jurisdictions"]:
                original = next(source for source in payload["family_jurisdiction"]
                                if source["family_id"] == family["id"] and source["jurisdiction"] == row["code"])
                self.assertEqual(row["year"], original["recorded_year"])
                self.assertEqual(row["date"], original["recorded_date"])
                self.assertEqual(row["precision"], original["date_precision"])
                self.assertEqual(row["status"], original["inventory_status"])
        self.assertEqual(payload, snapshot)
        body = render_inventory_rows(ui["families"][0], ui["strings"])
        self.assertEqual(body.count('<tr data-jurisdiction='), 51)
        self.assertIn('Unknown', body)
        self.assertNotIn('never-treated', self.markup())

    def test_readout_filters_withheld_and_supports_whole_row(self):
        ui = normalize_payload(self.payload, "en")
        family = next(row for row in ui["families"] if row["id"] == "tnc_framework")
        readout = render_readout(family, None, ui["strings"])
        self.assertEqual(readout.count('<li>'), 42)
        for code in ("AK", "CT", "MT", "NH", "OH", "OK"):
            name = next(row["name"] for row in family["jurisdictions"] if row["code"] == code)
            self.assertNotIn('>' + name + '</span>', readout)
        self.assertIn('href="https://', readout)
        self.assertEqual(render_readout(family, 2004, ui["strings"]), '<li class="do-empty">No strictly verified dates in this year.</li>')
        self.assertIn('<option value="all">All recorded years</option>', self.markup())
        self.assertRegex(self.markup(), r'<option value="2020" selected>2020 · 6</option>')

    def test_short_family_names_and_readout_controls_share_one_section(self):
        for lang in ("en", "zh"):
            markup = self.markup(lang)
            ui = self.ui(markup)
            for family, original in zip(ui["families"], self.payload["family"]):
                short = original["short_" + lang]
                if lang == "zh" and original["id"] == "min_wage_15":
                    short = "15 美元最低工资"
                self.assertEqual(family["label"], short)
            selected = re.search(r'<section class="do-selected"[^>]*>.*?</section>', markup, re.S)
            self.assertIsNotNone(selected)
            for identifier in ("design-family", "design-year", "design-strip", "design-selected-heading", "design-readout"):
                self.assertIn(f'id="{identifier}"', selected.group())
            strip = re.search(r'<div\b[^>]*id="design-strip"[^>]*>(.*?)</div>', selected.group(), re.S)
            self.assertIsNotNone(strip)
            self.assertIn('role="group"', strip.group())

    def test_public_inventory_omits_all_research_notes_and_localizes_exclusions(self):
        payload = copy.deepcopy(self.payload)
        for row in payload["family_jurisdiction"]:
            row["scope_note"] = "PRIVATE_WORKING_NOTE_SENTINEL V1 FIX research had null"
        for lang in ("en", "zh"):
            normalized = normalize_payload(payload, lang)
            markup = self.markup(lang, payload)
            self.assertNotIn("scope_note", markup)
            self.assertNotIn("PRIVATE_WORKING_NOTE_SENTINEL", markup)
            self.assertEqual(validate_public_fields(normalized), [])
            self.assertNotRegex(markup, r'(?i)\b(?:V[123]|FIX|Researcher|research\s+had|TODO|null)\b')
            for family in normalized["families"]:
                self.assertEqual(render_inventory_rows(family, normalized["strings"]).count('<tr data-jurisdiction='), 51)
                for row in family["jurisdictions"]:
                    self.assertNotIn("scope_note", row)
                    reason = row.get("exclusion_reason")
                    if reason:
                        self.assertFalse(row["strict"])
                        self.assertNotRegex(reason, r'^(?:Disputed|Removed):')
                        if lang == "zh":
                            self.assertRegex(reason, r'[\u4e00-\u9fff]')

    def test_public_marker_check_reports_nested_fields_without_echoing_private_text(self):
        for marker in ("V1", "v2", "V3", "FIX", "Researcher", "research had", "TODO", "null"):
            with self.subTest(marker=marker):
                errors = validate_public_fields({"families": [{"note": "private " + marker + " text"}]})
                self.assertEqual(len(errors), 1)
                self.assertIn("public.families[0].note", errors[0])
                self.assertNotIn("private", errors[0])
        self.assertEqual(validate_public_fields({"date": None, "name": "Fixing the prefix", "url": "https://example.test/nullified"}), [])

    def test_renderer_rejects_markers_in_public_evaluation_prose(self):
        for lang in ("en", "zh"):
            with self.subTest(lang=lang):
                payload = copy.deepcopy(self.payload)
                payload["run_evidence"][0]["label"][lang] = "Researcher"
                with self.assertRaisesRegex(ValueError, "internal research marker"):
                    self.markup(lang, payload)

    def test_three_runs_have_correct_links_and_compact_notes(self):
        for lang in ("en", "zh"):
            markup = self.markup(lang)
            self.assertIn(f'href="/viz/{lang}/worked_examples.html#paid_sick_leave-heading"', markup)
            self.assertIn(f'href="/viz/{lang}/worked_examples.html#medicaid_expansion-heading"', markup)
            self.assertIn(f'href="/viz/{lang}/worked_examples.html"', markup)
            self.assertEqual(markup.count('<li data-example='), 3)
        en = self.markup()
        self.assertIn('86% matched synthetic coverage', en)
        self.assertIn('microdata DDD context', en)
        self.assertIn('Stopped at the pre-trend check and economic-size check.', en)
        castle = re.search(r'<li data-example="castle_doctrine">.*?</li>', en).group()
        self.assertNotIn('Homicide rate', castle)
        self.assertNotIn('0.114', castle)

    def test_escaping_and_inert_json(self):
        payload = copy.deepcopy(self.payload)
        payload["family"][0]["label_en"] = '<img src=x onerror=alert(1)>'
        payload["family_jurisdiction"][0]["scope_note"] = '</script><script>alert(1)</script>'
        payload["family_jurisdiction"][0]["sources"] = ['javascript:alert(1)', 'https://example.test/?a=1&b=2']
        markup = self.markup(payload=payload)
        self.assertNotIn('<img src=x', markup)
        self.assertNotIn('<script>alert(1)', markup)
        self.assertNotIn('javascript:', markup)
        self.assertNotIn("scope_note", self.ui(markup)["families"][0]["jurisdictions"][0])
        script = re.search(r'<script type="application/json".*?</script>', markup, re.S).group()
        self.assertTrue(script.isascii())

    def test_visible_wording_stays_flat_static_and_scope_specific(self):
        markup = re.sub(r'<script\b.*?</script>', '', self.markup(), flags=re.S)
        self.assertNotRegex(markup.lower(), r'\b(effect|effects|diffusion|representative|quality score|opportunity abundance)\b')
        self.assertNotRegex(markup, r'<(?:animate|h[4-6])\b')
        self.assertEqual(markup.count('<figure '), 1)
        self.assertEqual(markup.count('class="do-timing-grid"'), 1)
        self.assertEqual(markup.count('<details '), 1)
        self.assertNotRegex(markup, r'[\u4e00-\u9fff]')
        self.assertEqual(markup.count('<th scope="col">'), 8)


if __name__ == '__main__':
    unittest.main()
