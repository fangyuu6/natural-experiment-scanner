"""Changed primary geometry cannot silently reuse old forecasts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from scripts.l2_pipeline.predictions import import_unchanged_predictions, lock_predictions
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.engine.runner import fit_primary
from scripts.l2_pipeline.tests.test_equivalence import invented_fixture


class ForecastImport(unittest.TestCase):
    def fixture(self, root):
        old,new=root/'old',root/'new'
        spec=dict(design_id='invented',track='DiD',population='population',outcome=dict(outcome_unit='hours',years=[1,9]),
            analysis=dict(estimand='ATT',weights='equal',controls='never',clock='full'),
            support=dict(variants=[dict(variant='primary',cell_inventory=[dict(treated=['A','B'],controls=['C'],period=8)])]))
        ensure_once(old/'SPEC.json',spec);ensure_once(new/'SPEC.json',dict(spec,version='new'))
        folder=old/'predictions/Card'
        original=ensure_once(folder/'Card.filled.json',dict(central_ATT=1,interval_80=[0,2]))
        value=dict(persona='Card',design_id='invented',spec_sha256=sha(old/'SPEC.json'),point=1,interval=[0,2],
            interval_level=.8,rationale='invented',disclosures=[],normalization={})
        submission=ensure_once(folder/'submission.json',value)
        ensure_once(folder/'COLLECTION.json',dict(validated=True,role='Card',stage='S5',design_id='invented',
            original_path=str(original.resolve()),original_sha256=sha(original),promoted_sha256=sha(submission)))
        return old,new,spec

    def test_unchanged_primary_rebinds_and_locks_identical_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            old,new,_=self.fixture(Path(temp));before=sha(old/'predictions/Card/Card.filled.json')
            import_unchanged_predictions(old,new,['Card'])
            lock_predictions(new,['Card'],'invented',sha(new/'SPEC.json'))
            self.assertEqual(sha(new/'predictions/Card/Card.filled.json'),before)
            value=json.loads((new/'predictions/Card/submission.json').read_text())
            self.assertEqual(value['point'],1);self.assertEqual(value['interval'],[0,2])

    def test_changed_sample_refuses_before_copying_any_forecast(self):
        with tempfile.TemporaryDirectory() as temp:
            old,new,spec=self.fixture(Path(temp));spec=copy.deepcopy(spec)
            spec['support']['variants'][0]['cell_inventory'][0]['treated']=['B']
            (new/'SPEC.json').write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError,'estimand/sample changed'): import_unchanged_predictions(old,new,['Card'])
            self.assertFalse((new/'predictions').exists())

    def test_corrupted_collected_original_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            old,new,_=self.fixture(Path(temp))
            (old/'predictions/Card/Card.filled.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'binding changed'): import_unchanged_predictions(old,new,['Card'])

    def test_invented_parallel_parity_and_independent_reference(self):
        declaration,rows=invented_fixture()
        def fit(_):return fit_primary(declaration,rows,B=99)[0]
        serial=[fit(i) for i in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:parallel=list(pool.map(fit,range(2)))
        self.assertEqual(serial,parallel)
        self.assertTrue(all(row['reference']['passed'] for row in parallel))
