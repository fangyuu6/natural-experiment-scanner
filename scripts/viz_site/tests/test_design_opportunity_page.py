"""Template/nav/home contract checks, without writing site builds."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from scripts.viz_site import build as vb
from scripts.viz_site import restructure_estimates as estimates

ROOT = Path(__file__).resolve().parents[3]


class ArchitectureTests(unittest.TestCase):
    def test_home_uses_only_pinned_snapshot_for_estimates_numbers(self):
        # Deliberately differ from the live fixture and from legacy L1/L2 counts.
        grouped = {name: 0 for name in estimates.STAGES}
        grouped.update(attempted_matched=3, attempted_directional=29,
                       attempted_not_consistent=7, attempted_not_comparable=11, confirmed=2)
        snapshot = {'as_of': '2026-10-14T00:30:00+02:00', 'total': 601,
                    'grouped_stage_counts': grouped, 'matched_independent': 5, 'confirmed_independent': 2,
                    'papers': estimates.load_snapshot()['papers']}
        with patch.object(estimates, 'load_snapshot', return_value=snapshot), patch.dict(vb._data_cache, {
                'funnel_data': {'total': 987654}, 'l2_counts': {'processed': 991, 'estimated': 992}}):
            for lang in vb.LANGS:
                errors = vb.Errors()
                home = vb.render_partial('home_story', lang, errors)
                self.assertEqual(errors.errors, [])
                ladder = re.search(r'<section class="nx-sec nx-ladder".*?</section>', home, re.S)[0]
                steps = re.findall(r'<li class="nx-st".*?</li>', ladder, re.S)
                self.assertEqual(len(steps), 3)
                # Map geometry and style rules hold arbitrary digits and class names; the markup and text must not.
                markup = re.sub(r'<(style|script|svg)\b.*?</\1>', '', home, flags=re.S)
                # Owner, 2026-10-14 (V5.1): the Estimate step shows the snapshot's re-estimated papers and number matches, nothing else.
                line = re.sub(r'<[^>]+>', '', steps[1].split('<p class="nx-nd', 1)[1])
                # R26 (content check M1): "52 papers re-estimated, 36 within 10%"; the 10 is Estimates' rule, the 52 is the fixture's own count
                # and the 36 the number matches of the pinned snapshot's papers (V5.1 pin; 32 under the earlier one).
                self.assertEqual(re.findall(r'\d+', line), ['52', str(estimates.public_counts(snapshot)['number_matches']), '10'])
                self.assertEqual(estimates.public_counts(snapshot)['number_matches'], 36)
                self.assertNotIn('601', markup)  # the catalogue size is never shown
                self.assertNotIn('2026-10-13', markup)  # and Home carries no update stamp
                self.assertNotRegex(markup, r'987654|991|992')  # neither the funnel fixture nor the legacy L2 counts
                for step, route in zip(steps, ('screening', 'worked_examples', 'synthesize')):
                    self.assertIn(f'href="/viz/{lang}/{route}.html"', step)

    @unittest.skipUnless(shutil.which('node'), 'Local Node unavailable')
    def test_worker_blocks_data_json_without_external_calls(self):
        code = """import fs from 'node:fs';
const source=fs.readFileSync('worker/src/index.js','utf8');
const {default:worker}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const env=new Proxy({}, {get(){throw new Error('Unexpected external access')}});
for(const path of ['/design_opportunity.json','/viz/en/design_opportunity.json','/outcome_inventory.json']){
 const response=await worker.fetch(new Request('https://offline.invalid'+path),env,{});
 if(response.status!==404)throw new Error(path+' '+response.status);
}
"""
        proc = subprocess.run(['node', '--input-type=module', '-e', code], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == '__main__':
    unittest.main()
