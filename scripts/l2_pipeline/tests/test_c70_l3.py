"""C70 N13–N16 contracts: invented outcomes only; never a production fit."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline import c64, c70_l3 as c
from scripts.l2_pipeline.storage import encoded
from scripts.l2_pipeline.l3_addendum import amendment_fields, calendar_year, cohort_points
from scripts.l2_pipeline.engine import estimator, ri
from scripts.l2_pipeline.engine.design import build_design, structural_rows
from scripts.l2_pipeline.tests.test_equivalence import invented_fixture


def blind(*extra):
    return dict(coders=['blind_A','blind_B'],blind_attestation={k:False for k in ('estimate_read','forecast_read',*extra)})


class Metadata(unittest.TestCase):
    def test_s7_late_unbound_amendment_stays_unavailable(self):
        from scripts.l2_pipeline.stages.s7_export import run
        from scripts.l2_pipeline.tests.test_pipeline import card
        from scripts.l2_pipeline.storage import ensure_once, sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            ensure_once(root/'TERMINAL.json',{'status':'STOPPED'})
            ensure_once(root/c64.SPEC_NAME,{'schema':c64.SCHEMA,'geography':{}})
            ensure_once(root/'FREEZE_FINAL.json',{'files':{c64.SPEC_NAME:{'sha256':sha(root/c64.SPEC_NAME)}}})
            ensure_once(root/c.SPEC_NAME,c.contract({}))  # late, unbound data
            value=card();ctx=SimpleNamespace(design_dir=root,card=SimpleNamespace(payload=value,design_id=value['design_id']),read=lambda name:json.loads((root/name).read_text()))
            run(ctx)
            output=json.loads((root/'EXPORT.json').read_text())['l3_meta']['l3_export_v2']
            self.assertEqual(output['amendment_v2']['status'],'fields unavailable')
            self.assertEqual(output['amendment_field_status'],{f:'FIELD_MISSING' for f in c.FIELDS})

    def test_s7_frozen_amendment_digest_required_final_takes_precedence(self):
        from scripts.l2_pipeline.storage import ensure_once, sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);spec=ensure_once(root/c.SPEC_NAME,c.contract({}))
            ensure_once(root/'FREEZE_PREP.json',{'files':{c.SPEC_NAME:{'sha256':sha(spec)}}})
            self.assertEqual(c.frozen_contract(root),c.contract({}))
            spec.write_text('{}')
            with self.assertRaisesRegex(ValueError,'absent or changed'):c.frozen_contract(root)
            ensure_once(root/'FREEZE_FINAL.json',{'files':{}})
            self.assertIsNone(c.frozen_contract(root))

    def test_real_card_loader_roundtrip_for_blind_inputs(self):
        from scripts.l2_pipeline.cards import load_card, CardError
        from scripts.l2_pipeline.tests.test_pipeline import card
        value=card()
        value['l3_amendment_v2']=dict(statute_variant=[],interruption_ledger={'pending':'blind'},pub_ledger={'pending':'blind'},pub_ledger_sha256='a'*64,targets_file=c.TARGETS_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'card.json';path.write_text(json.dumps(value))
            loaded=load_card(path)
            self.assertEqual(loaded.payload['l3_amendment_v2'],value['l3_amendment_v2'])
            # Intake shape acceptance is separate from pre-freeze blind evidence.
            with self.assertRaises(ValueError): c.contract(dict(loaded.payload))
            for invalid in ['not an object', {'statute_variant':{}}, {'pub_ledger_sha256':'bad'}, {'outcome_associations':True}]:
                value['l3_amendment_v2']=invalid;path.write_text(json.dumps(value))
                with self.assertRaises(CardError):load_card(path)

    def test_future_unsealed_proposal_loads_and_attempted_design_unchanged(self):
        from scripts.l2_pipeline import c64_metadata as metadata
        from scripts.l2_pipeline.cards import load_card
        from scripts.l2_pipeline.tests.test_pipeline import card
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp); registry=repo/'data/l2_pipeline/registry';registry.mkdir(parents=True)
            covariates=repo/metadata.COVARIATES_PATH;covariates.parent.mkdir(parents=True);covariates.write_text('{}')
            work=repo/'work';rows=[]
            for identity in ['open','attempted']:
                package=work/identity/'v1';package.mkdir(parents=True)
                value=card();value['design_id']=identity
                path=package/'CARD.json';path.write_text(json.dumps(value))
                rows.append(dict(design_id=identity,card=str(path),work_dir=str(work)))
            (work/'attempted/v1/EXECUTION_ONCE.json').write_text('{}')
            (registry/'ACTIVE_DESIGNS.json').write_text(json.dumps(dict(designs=rows)))
            before={r['card']:Path(r['card']).read_bytes() for r in rows}
            result=metadata.plan_next_versions(repo,repo/'proposals')
            loaded=load_card(Path(result['designs'][0]['next_card']))
            self.assertEqual(set(loaded.payload['l3_export_v2']['amendment_v2']['field_status']),set(c.FIELDS))
            self.assertIsNone(loaded.payload['l3_amendment_v2']['pub_ledger'])
            self.assertEqual(result['designs'][1]['status'],'addendum fields unavailable')
            self.assertEqual(before,{r['card']:Path(r['card']).read_bytes() for r in rows})
            self.assertEqual(result,metadata.plan_next_versions(repo,repo/'proposals'))

    def test_all_six_fields_prospective_and_missing_is_null(self):
        value=c.contract({})
        self.assertEqual(set(value['field_status']),set(c.FIELDS))
        for key in c.FIELDS.values(): self.assertIsNone(value[key])
        self.assertEqual(value['field_status']['F14'],'FIELD_MISSING')
        self.assertEqual(value['outcome_compute_stage'],'S6_after_final_freeze')

    def test_existing_c64_supplement_preserved_new_supplement_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/c64.SPEC_NAME).write_bytes(b'existing C64 bytes')
            ctx=SimpleNamespace(design_dir=root,card=SimpleNamespace(payload={}))
            c64.retain(ctx); first=(root/c.SPEC_NAME).read_bytes()
            c64.retain(ctx)
            self.assertEqual((root/c.SPEC_NAME).read_bytes(),first)
            self.assertEqual((root/c64.SPEC_NAME).read_bytes(),b'existing C64 bytes')
            ctx.card.payload={'treatment':{'universe':['A']},'l3_amendment_v2':{'statute_variant':[]}}
            with self.assertRaises(ValueError): c64.retain(ctx)

    def test_every_frozen_marker_blocks_registration_without_writes(self):
        for marker in ['FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json','SEALED_IMPLEMENTATION.json','RESULT.json','L3_EXPORT_DATA.json']:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/marker).write_bytes(b'original')
                self.assertIsNone(c64.retain(SimpleNamespace(design_dir=root)))
                self.assertEqual([p.name for p in root.iterdir()],[marker])

    def test_legacy_export_is_explicitly_unavailable_and_no_fit(self):
        with patch('scripts.l2_pipeline.l3_addendum.amendment_fields',side_effect=AssertionError('No computation at S7')):
            block=c64.export_block({},None,{'schema':c64.SCHEMA})
        self.assertEqual(block['amendment_field_status'],{f:'FIELD_MISSING' for f in c.FIELDS})
        self.assertIsNone(block['placebo_group'])
        block=c64.export_block({},None,c.merge_registration({'schema':c64.SCHEMA},None))
        self.assertEqual(block['amendment_v2']['status'],'fields unavailable')

    def test_statute_disagreement_needs_third_blind_coder(self):
        row=dict(unit='A',variant='stronger',coder_variants=['same','stronger'],locator='statute p2',first_cohort_locator='statute p1',**blind('literature_sign_read'))
        with self.assertRaises(ValueError): c.validate_statute_variant([row],['A'])
        row.update(adjudicator='blind_C',adjudication_locator='statute p2 comparison')
        self.assertEqual(c.validate_statute_variant([row],['A'])[0]['variant'],'stronger')
        row['blind_attestation']['estimate_read']=True
        with self.assertRaises(ValueError): c.validate_statute_variant([row],['A'])

    def test_interruption_no_absence_without_full_search_and_cells(self):
        value=dict(as_of_date='2002-12-31',interruption_coverage_certified=True,onset_years={'A':2001},
            searches=[dict(unit='A',searched_through='2002-12-31',evidence_ref='archive search')],
            rows=[dict(unit='A',year=y,status='in_force') for y in (2001,2002)],**blind())
        self.assertTrue(c.validate_interruption_ledger(value,['A'])['interruption_coverage_certified'])
        for bad in [dict(value,searches=[]),dict(value,rows=value['rows'][:1]),dict(value,rows=[dict(unit='A',year=2001,status='unknown'),value['rows'][1]])]:
            with self.assertRaises(ValueError): c.validate_interruption_ledger(bad,['A'])
        value['rows'][1].update(status='interrupted',interruption=dict(type='court_block',start_date='2002-06-01',both_coders_agree=True,evidence_ref='order'))
        self.assertEqual(c.validate_interruption_ledger(value,['A'])['rows'][1]['interruption']['type'],'court_block')

    def test_publication_rule_hash_inventory_blinding(self):
        candidate=dict(year=2001,direction_a_i=1,match=dict(policy_scope_dose='match',outcome='match'),estimate=dict(ci=[.1,1.]))
        value=dict(targets_candidates=[candidate],targets_candidates_sha256=hashlib.sha256(encoded([candidate])).hexdigest(),targets_file_sha256='a'*64,
            rows=[dict(candidate_index=0,first_public_year=2000,first_public_source='agency',reported_significance=None,**{'class':'POS'})],**blind('adoption_dates_read'))
        source={'designs':[{'design_id':'synthetic','candidates':[candidate]}]}
        digest=hashlib.sha256(encoded(value)).hexdigest()
        self.assertEqual(c.validate_pub_ledger(value,1,digest,source,'a'*64,'synthetic'),value)
        with self.assertRaises(ValueError): c.validate_pub_ledger(value,1,'0'*64,source,'a'*64,'synthetic')
        value['rows'][0]['class']='NULL'
        with self.assertRaises(ValueError): c.validate_pub_ledger(value,1,hashlib.sha256(encoded(value)).hexdigest(),source,'a'*64,'synthetic')

    def test_publication_embedded_subset_and_nonfinite_estimates_rejected(self):
        source={'designs':[{'design_id':'synthetic','candidates':[{'year':2001}]}]}
        value=dict(targets_candidates=[],targets_candidates_sha256=hashlib.sha256(encoded([])).hexdigest(),targets_file_sha256='a'*64,rows=[],**blind('adoption_dates_read'))
        with self.assertRaises(ValueError): c.validate_pub_ledger(value,1,hashlib.sha256(encoded(value)).hexdigest(),source,'a'*64,'synthetic')
        for estimate in [{'ci':[float('nan'),1]}, {'point':1,'se':float('inf')}]:
            candidate=dict(match={'policy_scope_dose':'match','outcome':'match'},estimate=estimate)
            with self.assertRaises(ValueError):c.publication_class(candidate,1,'sig')

    def test_empty_statutes_and_forged_empty_interruption_certification(self):
        value=c.contract({'treatment':{'universe':['A']},'l3_amendment_v2':{'statute_variant':[]}}, {'records':[{'unit':'A','status':'treated','start_period':2001}]})
        self.assertEqual(value['field_status']['F13'],'FIELD_MISSING')
        ledger=dict(as_of_date='2002-12-31',interruption_coverage_certified=True,onset_years={},searches=[dict(unit='A',searched_through='2002-12-31',evidence_ref='search')],rows=[],**blind())
        with self.assertRaises(ValueError):c.validate_interruption_ledger(ledger,['A'])
        ledger['never_adopted_units']=['A']
        with self.assertRaises(ValueError):c.validate_interruption_ledger(ledger,['A'],{'records':[{'unit':'A','status':'treated','start_period':2001}]})

    def test_context_rejects_alternative_or_changed_locked_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            source={'pub_ledger':{},'targets_file':'forged/TARGETS.json'}
            with self.assertRaisesRegex(ValueError,'locked TARGETS path'):
                c.context_contract({'l3_amendment_v2':source},repo)
            source['targets_file']=c.TARGETS_PATH
            path=repo/c.TARGETS_PATH;path.parent.mkdir(parents=True);path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'TARGETS bytes changed'):
                c.context_contract({'l3_amendment_v2':source},repo)

    def test_publication_significance_order_and_no_invented_se(self):
        base=dict(direction_a_i=-1,match=dict(policy_scope_dose='partial',outcome='match'))
        self.assertEqual(c.publication_class(dict(base,estimate={'ci':[-1,1],'point':-10,'se':.1}),1,'sig'),'NULL')
        self.assertEqual(c.publication_class(dict(base,estimate={'point':-2,'se':.5}),1,None),'WRONG')
        self.assertEqual(c.publication_class(base,1,None),'UNCLASSIFIED')
        self.assertEqual(c.publication_class(base,1,'sig'),'WRONG')


class Numerical(unittest.TestCase):
    def setUp(self):
        self.declaration,self.rows=invented_fixture()
        self.design=build_design(self.declaration,self.rows)
        self.outcomes={(u,t):y for u,t,y,g in structural_rows(self.declaration,self.rows)}
        self.registration=dict(amendment_v2=c.contract({}),geography={'R':list('GHIJKL')})

    def test_cohort_points_exact_engine_aggregation_and_all_post_years(self):
        expected=estimator.estimate_design(self.design,self.outcomes,B=3)['group']
        actual=cohort_points(self.declaration,self.design,self.outcomes)
        for ours,reference in zip(actual,expected):
            self.assertAlmostEqual(ours['att'],reference['att'],12)
            self.assertEqual(ours['n_treated'],3)
        self.assertEqual(actual[0]['n_periods'],8)

    def test_fixed_draws_retain_index_hash_and_every_cohort(self):
        old=ri.permutation_reference(self.declaration,self.design,self.outcomes,count=7)
        new=ri.permutation_reference(self.declaration,self.design,self.outcomes,count=7,l3_export=dict(purpose_direction=1,amendment_v2=c.contract({})))
        self.assertEqual(old,{k:v for k,v in new.items() if k not in {'placebo_profiles','placebo_labels','placebo_group'}})
        self.assertEqual([r['draw_index'] for r in new['placebo_group']],list(range(7)))
        labels=ri.predraw_labels(self.design,7)
        fake=ri.relabeled_design(self.declaration,self.design,labels[0])
        expected=estimator.estimate_design(fake,self.outcomes,B=3)['group']
        for ours,ref in zip(new['placebo_group'][0]['group'],expected):self.assertAlmostEqual(ours['att'],ref['att'],12)

    def test_loo_matches_independent_estimator_rebuild(self):
        calls=[]
        value=amendment_fields(self.declaration,self.design,self.outcomes,self.registration,on_estimate=lambda:calls.append(1))
        self.assertEqual(len(calls),6)
        self.assertEqual([r['unit'] for r in value['loo_control_att']],list('GHIJKL'))
        reduced=copy.deepcopy(self.declaration)
        reduced['case']['sample_mask']['include_units'].remove('G');reduced['never_controls'].remove('G')
        design=build_design(reduced,[r for r in self.rows if r[0]!='G'])
        expected=estimator.estimate_design(design,self.outcomes,B=3)['group']
        for ours,ref in zip(value['loo_control_att'][0]['group'],expected):self.assertAlmostEqual(ours['att'],ref['att'],12)
        self.assertTrue(all(r['calendar_year']==r['g']+r['event_time'] for r in value['group_time']))
        self.assertIsNone(value['statute_variant'])

    def test_missing_risk_set_does_not_invent_loo(self):
        self.registration['geography']['R']=None
        value=amendment_fields(self.declaration,self.design,self.outcomes,self.registration)
        self.assertIsNone(value['loo_control_att']);self.assertEqual(value['amendment_field_status']['F12'],'FIELD_MISSING')

    def test_unregistered_numerical_fields_not_computed(self):
        self.assertEqual(amendment_fields({}, {}, {}, {}),{})
        self.assertEqual(amendment_fields({}, {}, {}, c.merge_registration({},None)),{})

    def test_subannual_calendar_years_and_explicit_fiscal_mapping(self):
        for frequency,period,year in [('monthly',2020*12+11,2020),('quarterly',2021*4+2,2021)]:
            declaration=dict(case={'panel':{'frequency':frequency}})
            self.assertEqual(calendar_year(declaration,period),year)
        fiscal=dict(case={'panel':{'frequency':'annual','time_basis':{'kind':'fiscal_year'}}})
        self.assertIsNone(calendar_year(fiscal,2020))
        fiscal['period_calendar_years']={2020:2019}
        self.assertEqual(calendar_year(fiscal,2020),2019)
        self.assertEqual(calendar_year({'period_years':[1999,2000]},1),2000)
