"""Generic routing, fresh disjointness and read-only C38 byte regression."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.intake import c38, history_routing as route
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.storage import encoded, ensure_once, sha


class GenericHistoryRoutingTests(unittest.TestCase):
    def card(self):
        return dict(design_id='arbitrary_new_rdd_design', version='v_future',
            mechanism='Exact locked predicate (0 = no, 1 = yes)',
            treatment=dict(predicate='Exact locked predicate (0 = no, 1 = yes)', seed=101),
            outcome=dict(series_id='current_card_outcome', frequency='annual',
                         period_years=[1990, 1991, 1992], population_filters={'age':'all'},
                         paths=['forbidden_outcome.csv'], values=[12345]), track='RDD')

    def test_unlisted_design_preserves_predicate_and_current_outcome(self):
        card = self.card(); original = copy.deepcopy(card)
        plan = route.planning(card, ['AA', 'BB'], attempt_id='future_attempt')
        self.assertEqual(plan['mechanism'], original['mechanism'])
        self.assertEqual(plan['outcome']['series_id'], 'current_card_outcome')
        self.assertEqual(plan['outcome']['population_filters'], {'age':'all'})
        self.assertNotIn('paths', plan['outcome']); self.assertNotIn('values', plan['outcome'])
        self.assertEqual(plan['clock'], c38.CLOCK)
        self.assertEqual(card, original)
        card['treatment']['predicate'] = 'Different predicate'
        self.assertEqual(route.planning(card, ['AA'], attempt_id='future_attempt')['mechanism'], 'Different predicate')
        card['treatment']['predicate'] = ''
        with self.assertRaises(ValueError): route.planning(card, ['AA'], attempt_id='future_attempt')

    def test_clarification_requires_verbatim_repository_ruling_and_locator(self):
        path = route.REPO / 'docs/plans/l2-pipeline/HUB_RULINGS_C39.md'
        quote = 'A later legislative repeal adds no second event.'
        value = dict(text=quote, ruling_file=str(path), locator='§1')
        plan = route.planning(self.card(), ['AA'], attempt_id='x', predicate_clarification=value)
        self.assertEqual(plan['mechanism'], self.card()['mechanism'])
        self.assertEqual(plan['predicate_clarification']['ruling']['sha256'], sha(path))
        for changed in (dict(value, text='Invented clarification'), dict(value, locator='')):
            with self.assertRaises(ValueError): route.validate_clarification(changed)

    def test_fresh_sampler_excludes_prior_audit_and_calibration_before_draw(self):
        units = [f'S{i:02d}' for i in range(25)]
        policy = route.validate_fresh_policy(dict(seed=402010, excluded_units=units[:8], sample_size=10), units)
        records = [dict(unit=u, status='treated') for u in units]
        sample = route.select_fresh_sample(records, policy)
        self.assertEqual(len(sample), 10); self.assertFalse(set(sample) & set(units[:8]))
        self.assertEqual(sample, route.select_fresh_sample(list(reversed(records)), policy))
        self.assertEqual(route.select_fresh_sample(records[:17], policy), units[8:17])
        with self.assertRaises(ValueError): route.select_fresh_sample(records + records[:1], policy)
        with self.assertRaises(ValueError): route.validate_fresh_policy(dict(seed=True, excluded_units=[], sample_size=10), units)
        with self.assertRaises(ValueError): route.validate_fresh_policy(dict(seed=1, excluded_units=['XX'], sample_size=10), units)

    def test_generic_cli_and_receipt_use_only_current_metadata_and_prior_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); intake = base/'intake'; root = base/'output'
            card = self.card(); did = card['design_id']
            ensure_once(intake/'cards'/f'{did}.json', card)
            units = [f'S{i:02d}' for i in range(25)]
            ensure_once(intake/'imports'/did/'PANEL_IMPORT.json', {'units':units})
            prior = ensure_once(intake/'old.job.json', dict(role='auditor', design_id=did,
                contract=dict(sample=['S00'],seed=99, sampled_tuples=[{'forbidden_date':1777}])))
            ensure_once(intake/'calibration_jobs/S1'/did/'auditor.job.json',
                dict(role='auditor',design_id=did,contract=dict(sample=['S01'],seed=100,
                prior_sample_bindings=[dict(path=str(prior),sha256=sha(prior))])))
            # An unused reservation is not an earlier completed/dispatched audit.
            ensure_once(intake/'fresh_audit_jobs/S1'/did/'auditor.job.json', dict(role='auditor',
                design_id=did, output_path=str(base/'absent'), output_dir=str(base/'work'),
                contract=dict(sample=['S02'],seed=101)))
            args=['prepare','--design-id',did,'--attempt-id','new_attempt',
                '--intake-root',str(intake),'--output-root',str(root),'--audit-seed','102']
            with patch('builtins.print'):
                self.assertEqual(route.main(args),0)
                self.assertEqual(route.main(args),0)  # Exact deterministic resume.
            receipt=route.read(root/'routing_ledger'/did/'ROUTE_RECEIPT.json')
            self.assertEqual(receipt['fresh_audit']['excluded_units'],['S00','S01'])
            for path in receipt['job_paths']:
                job=load_job(Path(path)); self.assertEqual(job.contract['manual']['seed'],102)
                self.assertEqual(job.contract['manual']['fresh_audit'],receipt['fresh_audit'])
                text=Path(path).read_text()
                self.assertNotIn('forbidden_outcome.csv',text); self.assertNotIn('forbidden_date',text)
                self.assertFalse((Path(job.output_dir)/'DISPATCH_ONCE.json').exists())
            for seed in (99,100,101):
                with self.assertRaises(ValueError): route.route_official_history(did,'x',intake_root=intake,root=base/'bad',audit_seed=seed)
            altered=route.read(prior); altered['contract']['sample']=['S03']
            prior.write_bytes(encoded(altered)+b'\n')
            with self.assertRaises(ValueError): route.prior_sample_metadata([intake/'calibration_jobs/S1'/did/'auditor.job.json'])

    def test_legacy_flat_prior_packet_is_supported_without_reading_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=ensure_once(Path(tmp)/'old.job.json', dict(role='auditor',policy_id='legacy:policy',
                sample=['AA','BB'],seed=10,sampled_tuples=[{'old_answer':1777}]))
            units,seeds,evidence=route.prior_sample_metadata([path])
            self.assertEqual(units,['AA','BB']);self.assertEqual(seeds,{10})
            self.assertEqual(evidence[0]['design_id'],'legacy:policy')
            self.assertNotIn('old_answer',json.dumps(evidence))

    def test_c40_production_routes_bind_current_cards_and_park_open_carry(self):
        root=route.REPO/'data/l2_pipeline/sources/research_panel/c40_20261010'
        if not (root/'C39_LEDGER_C40.json').exists():self.skipTest('C40 preparation absent')
        ledger=route.read(root/'C39_LEDGER_C40.json');self.assertEqual(len(ledger['rows']),4)
        for row in ledger['rows'][:3]:
            card=route.read(row['original_card']['path'])
            self.assertEqual(sha(row['original_card']['path']),row['original_card']['sha256'])
            self.assertEqual(row['mechanism'],card['mechanism'])
            self.assertEqual(row['fresh_audit']['seed'],402010)
            prior=set(u for e in row['prior_sample_evidence'] for u in e['sample'])
            self.assertEqual(prior,set(row['fresh_audit']['excluded_units']))
            for p in row['job_paths']:
                job=load_job(Path(p));self.assertEqual(job.contract['attempt_id'],'c40')
                self.assertEqual(job.contract['mechanism'],card['treatment']['predicate'])
                self.assertEqual(job.contract['manual']['fresh_audit'],row['fresh_audit'])
                self.assertEqual(job.contract['card']['outcome'],row['outcome'])
                self.assertEqual({Path(i['path']).name for i in job.inputs},{'COLLECTION_PLAN.json','MANIFEST.json'})
                reservation=Path(job.output_dir)/'DISPATCH_ONCE.json'
                if reservation.exists():
                    reserved=route.read(reservation)
                    self.assertEqual(reserved['job_sha256'],job.job_sha256)
                    self.assertEqual(reserved['retries'],0)
            for k in ('series_id','frequency','period_years','population_filters'):
                self.assertEqual(row['outcome'][k],card['outcome'][k])
        self.assertEqual(ledger['rows'][0]['predicate_clarification']['locator'],'C39 §1')
        self.assertEqual(ledger['rows'][1]['lineage']['retry_of'],'run7_minor_abortion_parental_involvement')
        self.assertEqual(ledger['rows'][3]['disposition'],'PARK')
        self.assertEqual(ledger['rows'][3]['emitted_jobs'],0)

    def test_c38_archives_preserved_and_clean_regeneration(self):
        from scripts.l2_pipeline.agents.history import emit_history_job
        from scripts.l2_pipeline.agents.history_packet import check
        import tempfile
        for did in c38.ROUTES:
            for role in ('A', 'B'):
                old = c38.JOBS/'S1'/did/(role+'.'+c38.ATTEMPT+'.md')
                before = old.read_bytes()
                with tempfile.TemporaryDirectory() as tmp:
                    job = emit_history_job(old, jobs_root=Path(tmp), attempt_id='c58_test')
                    check(job)
                self.assertEqual(old.read_bytes(), before)


if __name__=='__main__':unittest.main()
