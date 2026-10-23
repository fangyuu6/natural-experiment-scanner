"""Homepage disclosure retains the dated ledger after the timing page retires."""
from pathlib import Path
import json
import unittest
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from scripts.viz_site.design_opportunity_adapter import map_row, normalize_payload, render_map_sources


ROOT = Path(__file__).resolve().parents[3]


class MapSourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads((ROOT / 'scripts/viz_site/data/design_opportunity.json').read_text())

    def parsed(self, lang):
        return ET.fromstring('<div>' + render_map_sources(self.payload, lang) + '</div>')

    def test_one_closed_disclosure_contains_every_displayed_verified_record_and_source(self):
        for lang in ('en', 'zh'):
            root = self.parsed(lang)
            details = root.findall('details')
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0].get('id'), 'map-sources')
            self.assertNotIn('open', details[0].attrib)
            self.assertEqual(len(details[0].findall('summary')), 1)
            families = normalize_payload(self.payload, lang)['families']
            sections = details[0].findall('section')
            self.assertEqual(len(sections), len(families))
            total = 0
            for section, family, original in zip(sections, families, self.payload['family']):
                self.assertEqual(section.get('data-family'), family['id'])
                self.assertEqual(section.find('h3').text, family['label'])
                self.assertEqual(section.find('p').text, original['date_basis_' + lang])
                rows = section.findall('./table/tbody/tr')
                expected = [row for row in family['jurisdictions'] if map_row(family['id'], row) and (row['date'] is not None or row['year'] is not None)]
                self.assertEqual(len(rows), len(expected))
                total += len(rows)
                for row, record in zip(rows, expected):
                    self.assertEqual(row.get('data-jurisdiction'), record['code'])
                    self.assertEqual(row.find('th').text, record['name'])
                    self.assertEqual(row.find('td').text, str(record['date'] or record['year']))
                    self.assertEqual([link.get('href') for link in row.findall('.//a')], record['sources'])
                    self.assertIsNone(row.find('./td/span[@class="nx-date-status"]'))
            # R22: the 213 strictly dated rows plus the seven single-source rows the maps now colour (Alaska ride-hailing included).
            self.assertEqual(total, sum(row['included_strict'] and (row['recorded_date'] is not None or row['recorded_year'] is not None)
                                        for row in self.payload['family_jurisdiction']) + 7)
            self.assertEqual(total, 220)
            self.assertEqual(len(root.findall('.//span[@class="nx-date-status"]')), 0)

    @unittest.skipUnless(shutil.which('node'), 'node required for source-selection contract')
    def test_selection_initializes_and_follows_map_event(self):
        script = re.search(r'<script>(.*?)</script>', render_map_sources(self.payload), re.S)[1]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const sections=['nurse_licensure_compact','tnc_framework','paid_sick_leave'].map(family=>({dataset:{family},hidden:false}));
const listeners={};
const document={getElementById(){return{querySelectorAll(){return sections}}},
 querySelector(){return{dataset:{f:'nurse_licensure_compact'}}},addEventListener(name,fn){listeners[name]=fn}};
vm.runInNewContext(JSON.parse(require('node:fs').readFileSync(0,'utf8')),{document});
assert.deepEqual(sections.map(s=>s.hidden),[false,true,true]);
listeners['nx:map-policy']({detail:{policy:'tnc_framework'}});
assert.deepEqual(sections.map(s=>s.hidden),[true,false,true]);
listeners['nx:map-policy']({detail:{policy:'paid_sick_leave'}});
assert.deepEqual(sections.map(s=>s.hidden),[true,true,false]);
"""
        completed = subprocess.run(['node', '-e', harness], input=json.dumps(script), text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_source_membership_matches_colored_map_and_accessible_hosts(self):
        from scripts.viz_site.make_story_map import story_data
        from urllib.parse import urlsplit
        counts = {}
        root = self.parsed('en')
        mapped = {family['id']: set(family['years']) for family in story_data()['policies']}
        for section in root.findall('./details/section'):
            members = {row.get('data-jurisdiction') for row in section.findall('./table/tbody/tr')}
            self.assertEqual(members, mapped[section.get('data-family')])
            counts[section.get('data-family')] = len(members)
        self.assertEqual(counts['nurse_licensure_compact'], 41)
        self.assertEqual(counts['tnc_framework'], 48)
        for link in root.findall('.//a'):
            self.assertIn(urlsplit(link.get('href')).netloc.removeprefix('www.'), link.get('aria-label'))
        # R29 (AD2): the column heads read "Date" and "Sources".
        rendered = render_map_sources(self.payload)
        self.assertIn('<th scope="col">Sources</th>', rendered)
        self.assertIn('<th scope="col">Date</th>', rendered)
        self.assertNotIn('Sources consulted', rendered)
        self.assertNotIn('Recorded date', rendered)

    def test_single_source_rows_are_drawn_and_listed(self):
        # Owner, 2026-10-14 (R21): seven states with a recorded year were drawn as states without the law.
        # R22: the last open flag (Alaska ride-hailing, HB 132, 2017) was closed on 2026-10-14, so it is drawn and listed too.
        from scripts.viz_site.make_story_map import story_data
        drawn = {(p['id'], c): y for p in story_data()['policies'] for c, y in p['years'].items()}
        seven = {('nurse_licensure_compact', 'CT'): 2024, ('tnc_framework', 'CT'): 2017, ('tnc_framework', 'MT'): 2015,
                 ('tnc_framework', 'NH'): 2016, ('tnc_framework', 'OH'): 2015, ('tnc_framework', 'OK'): 2015,
                 ('tnc_framework', 'AK'): 2017}
        for key, year in seven.items():
            self.assertEqual(drawn.get(key), year, key)
        for lang in ('en', 'zh'):
            root = self.parsed(lang)
            listed = {(s.get('data-family'), r.get('data-jurisdiction')): r.find('td').text
                      for s in root.findall('./details/section') for r in s.findall('./table/tbody/tr')}
            for key, year in seven.items():
                self.assertTrue(listed[key].startswith(str(year)), (key, listed[key]))
            self.assertEqual(set(listed), set(drawn))
            self.assertEqual(len(root.findall('.//span[@class="nx-date-status"]')), 0)

    def test_bilingual_dates_and_links_match_without_live_retired_page_link(self):
        def ledger(lang):
            return [(row.get('data-jurisdiction'), row.find('td').text,
                     [link.get('href') for link in row.findall('.//a')])
                    for row in self.parsed(lang).findall('.//tbody/tr')]
        self.assertEqual(ledger('en'), ledger('zh'))
        self.assertEqual(self.parsed('en').find('./details/summary').text, 'Dates and sources by state')
        self.assertEqual(self.parsed('zh').find('./details/summary').text, '各州日期与来源')
        for lang in ('en', 'zh'):
            html = render_map_sources(self.payload, lang)
            self.assertNotIn('design_opportunity.html', html)
            self.assertNotIn('Unknown', html)
            self.assertNotIn('未知', html)
            self.assertIn("section.hidden=section.dataset.family!==id", html)
            self.assertIn("nx:map-policy", html)
            self.assertNotIn('<section hidden', html)
            self.assertIn('white-space:nowrap', html)


if __name__ == '__main__':
    unittest.main()
