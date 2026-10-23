"""Prospective class closures and deduplicated exports; synthetic inputs only."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.l2_pipeline import class_table as c,c64,c65
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.stages.s7_export import exports
from scripts.l2_pipeline.tests.test_pipeline import card


def cell(t=2002,base=2000,post=True,units=('A','B')):
    return dict(t=t,base_period=base,post=post,treated=list(units),controls=['C'],n_treated=len(units))


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve();self.root=self.repo/'data/work';self.root.mkdir(parents=True)
        self.row=dict(design_id='fake',attempt_id='a')
        self.ctx=SimpleNamespace(repo=self.repo,design_dir=self.root,card=SimpleNamespace(design_id='fake'),read=lambda n:c.read(self.root/n))
        self.p=SimpleNamespace(repo=self.repo,registry=self.repo/'registry',backend=SimpleNamespace(root=lambda row:self.root,ctx=lambda row:self.ctx),case_dir=lambda row:self.repo/'case')
        ensure_once(self.root/'SPEC.json',dict(design_id='fake',track='DiD',population={},outcome={},analysis={},support=dict(variants=[dict(variant='primary',cell_inventory=[cell()])]),c76b2=dict(class_table=2),interval_path=['Webb','CT_complete_control_path','point_plus_RI_if_uncertified']))
        ensure_once(self.root/'SUPPORT.json',dict(variants=[dict(variant='primary',cell_inventory=[cell(),cell(1999,1998,False)])]))
        self.job=ensure_once(self.repo/'job.json',{})
        self.finding=dict(id='S1',description='registered fallback',impact='report_only',evidence='SPEC.interval_path',class_issue=dict(kind='registered_point_ri'))

    def review(self,sealed=False,finding=None):
        scope='sealed_candidate' if sealed else 'draft_readiness'
        if sealed:
            files={n:dict(sha256=sha(self.root/n),role='verification') for n in ('SPEC.json','SUPPORT.json')}
            ensure_once(self.root/'FREEZE_FINAL.json',dict(files=files))
        candidate=self.root/('FREEZE_FINAL.json' if sealed else 'SPEC.json')
        path=self.root/('EXECUTION_REVIEW.json' if sealed else 'REVIEW_DRAFT_a.json')
        ensure_once(path,dict(design_id='fake',candidate_sha256=sha(candidate),review_scope=scope,
            must=[],should=[finding or self.finding],verdict='PASS',complete_sweeps=1,fix_cycles=0,
            independence_attestation=dict(wrote_implementation=False,wrote_fixes=False,real_outcome_associations_read=False)))
        ensure_once(self.job.with_suffix('.job.json'),dict(design_id='fake',job_sha256=sha(self.job),promote_path=str(path),contract=dict(candidate_sha256=sha(candidate))))
        return path,scope

    def accept(self,sealed=False):
        path,scope=self.review(sealed)
        with patch('scripts.l2_pipeline.registration.hub_signer',return_value='Owner D1'),patch('scripts.l2_pipeline.c54.draft_identity',return_value=('a',path)),patch.object(c,'selected',return_value=True):
            result=c.accept(self.p,self.row,self.job,scope)
            self.assertTrue(result)
            self.assertTrue(c.accept(self.p,self.row,self.job,scope))
        queue=c.read(self.p.registry/'HUB_QUEUE.json')['entries']
        self.assertEqual(len(queue),1);self.assertEqual(queue[0]['status'],'SPOTCHECK');self.assertFalse(queue[0]['blocking'])
        self.assertEqual(queue[0]['action'],'spotcheck')
        ensure_once(self.p.registry/'ACTIVE_DESIGNS.json',dict(designs=[self.row]))
        from scripts.l2_pipeline.c54 import apply_open_queue
        self.assertEqual(apply_open_queue(self.p,scheduler_owned=True),[])
        self.assertEqual(apply_open_queue(self.p,scheduler_owned=True),[])
        return path

    def test_draft_closure_validated_idempotent_and_exported(self):
        self.accept()
        value=c.export_annotations(self.root)
        self.assertEqual(next(iter(value['accepted_closures'].values()))['rule_id'],'R01')

    def test_sealed_closure_validated_without_changing_frozen_bytes(self):
        before={n:sha(self.root/n) for n in ('SPEC.json','SUPPORT.json')}
        self.accept(True)
        self.assertEqual(before,{n:sha(self.root/n) for n in before})
        self.assertTrue(c.export_annotations(self.root)['accepted_closures'])

    def test_primary_spec_bytes_change_escalates(self):
        _,scope=self.review()
        with (self.root/'SPEC.json').open('a') as stream:stream.write('\n')
        self.assertFalse(c.accept(self.p,self.row,self.job,scope))
        self.assertFalse(list(self.root.glob('*RESOLUTION*')))

    def test_science_and_unknown_component_remain_novel(self):
        finding=copy.deepcopy(self.finding);finding['class_issue']['components']=[dict(kind='change_primary')]
        _,scope=self.review(finding=finding)
        self.assertFalse(c.accept(self.p,self.row,self.job,scope))
        self.assertEqual(c.classify(dict(kind='registered_point_ri',operations=['change_sample']),c.load_design(self.root))['classification'],'NOVEL')

    def test_identical_cells_with_different_operator_stay_novel(self):
        design=c.load_design(self.root);primary=design['SUPPORT']['variants'][0]
        design['SUPPORT']['variants'].append(dict(primary,variant='alternate'))
        design['SPEC'].update(analysis=dict(weights='equal'),sensitivities=[dict(name='alternate',changes={})])
        issue=dict(kind='duplicate_sensitivity',variant='alternate')
        self.assertEqual(c.classify(issue,design)['classification'],'KNOWN')
        design['SPEC']['sensitivities'][0]['changes']={'analysis':{'weights':'population'}}
        self.assertEqual(c.classify(issue,design)['classification'],'NOVEL')

    def test_different_cohort_or_event_time_is_a_different_operator(self):
        design=c.load_design(self.root)
        primary=design['SUPPORT']['variants'][0]
        primary['cell_inventory'][0].update(g=2001,event_time=1)
        design['SPEC'].update(analysis=dict(weights='equal'),sensitivities=[dict(name='alternate',changes={})])
        issue=dict(kind='duplicate_sensitivity',variant='alternate')
        for coordinate in ('g','event_time'):
            alternate=copy.deepcopy(primary);alternate['variant']='alternate'
            design['SUPPORT']['variants']=[primary,alternate]
            self.assertEqual(c.classify(issue,design)['classification'],'KNOWN')
            alternate['cell_inventory'][0][coordinate]+=1
            self.assertEqual(c.classify(issue,design)['classification'],'NOVEL')

    def test_existing_unsealed_annotations_are_preserved_byte_for_byte(self):
        path=self.root/c65.NAME;path.write_bytes(b'{"schema": "legacy_annotation", "notes": ["retained"]}\n')
        before=path.read_bytes()
        self.assertEqual(c65.retain(self.ctx),path)
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse((self.root/c65.PANEL_NAME).exists())

    def test_missing_promised_key_fails(self):
        self.accept()
        path=next(self.root.glob('DRAFT_RESOLUTION*'));value=c.read(path)
        value['export_annotations']={};path.write_text(json.dumps(value))
        receipt=next(self.root.glob('CLASS_TABLE_ACCEPTED*'));r=c.read(receipt);r['closure_sha256']=sha(path);receipt.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'annotation missing'):c.export_annotations(self.root)

    def test_seeded_sample_has_reproducible_one_fifth_probability(self):
        self.assertEqual([c.selected(str(i)) for i in range(50)],[c.selected(str(i)) for i in range(50)])
        count=sum(c.selected(str(i)) for i in range(10000));self.assertTrue(1850<count<2150)

    def test_support_mass_not_design_name(self):
        path=self.root/'panels/primary/metadata.csv';path.parent.mkdir(parents=True)
        path.write_text('unit,year,series_break\nA,2001,definition_change\n')
        value=c65.annotations(self.root)['series_break'][0]
        self.assertEqual(value['treated_cell_mass'],2);self.assertEqual(value['mass_share'],1.)
        self.assertEqual(value['lead_mass_share'],0.)
        self.assertIsNone(c65.annotations('b1010_permitless_carry'))

    def test_inline_and_reference_l3_contract_match_and_tamper_rejected(self):
        numerical=dict(s_pre=2.,H=4,placebo_profiles=[dict(draw_index=i,x=[1,2,3]) for i in range(100)])
        result=dict(status='COMPLETED',l3_export_v2=numerical,actual_data_estimation_calls=1)
        result_path=ensure_once(self.root/'RESULT.json',result)
        registration=dict(schema=c64.SCHEMA,geography={})
        kwargs=dict(card=card(),terminal=dict(status='COMPLETED'),result=result,provenance={},l3_registration=registration)
        old=exports(**kwargs)['l3_meta']
        new=exports(**kwargs,result_reference=c64.reference(result_path))['l3_meta']
        path=ensure_once(self.root/'L3_EXPORT_DATA.json',new)
        from scripts.l3_meta.reader import read_export
        self.assertEqual(read_export(path),old)
        self.assertNotIn('draw_index',path.read_text())
        result_path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):read_export(path)

    def test_n10_single_consensus_never_eligible(self):
        from scripts.l3_meta.reader import n10_eligible
        slots={p:dict(author_provenance=dict(worker_id='worker_'+p)) for p in ('Card','Finkelstein','Roth')}
        value=dict(forecast_independence=dict(SINGLE_CONSENSUS=True,n10_eligible=True,n_eff=3,forecast_slots=slots,n_workers=3))
        self.assertFalse(n10_eligible(value))
        value['forecast_independence']['SINGLE_CONSENSUS']=False
        self.assertTrue(n10_eligible(value))
        # Q3 ruling 3b/3c: one worker id (or no worker data) is never eligible, whatever the stored flag says
        one=dict(value['forecast_independence'],forecast_slots={p:dict(author_provenance=dict(worker_id='w')) for p in slots},n_workers=1)
        self.assertFalse(n10_eligible(dict(forecast_independence=one)))
        no_slots={k:v for k,v in value['forecast_independence'].items() if k not in ('forecast_slots','n_workers')}
        self.assertFalse(n10_eligible(dict(forecast_independence=no_slots)))

    def test_s7_writes_references_and_preserves_canonical_result_bytes(self):
        from scripts.l2_pipeline.stages.s7_export import run
        result=ensure_once(self.root/'RESULT.json',dict(status='STOPPED',l3_export_v2=dict(placebo_profiles=[dict(draw_index=1)])))
        digest=sha(result)
        ensure_once(self.root/'TERMINAL.json',dict(status='STOPPED'))
        ensure_once(self.root/c64.SPEC_NAME,dict(schema=c64.SCHEMA,geography={}))
        ctx=SimpleNamespace(design_dir=self.root,card=SimpleNamespace(payload=card(),design_id='fake'),read=lambda n:c.read(self.root/n))
        run(ctx)
        output=c.read(self.root/'EXPORT.json')
        self.assertEqual(output['l3_meta']['result']['$ref']['sha256'],digest)
        self.assertEqual(sha(result),digest)
        self.assertNotIn('draw_index',(self.root/'EXPORT.json').read_text())
        resolved=c64.resolve_references(output['l3_meta'],self.root)
        self.assertEqual(resolved['l3_export_v2']['placebo_profiles'],[dict(draw_index=1)])

    def test_reference_path_escape_and_missing_receipt_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'escapes'):
            c64.resolve_references({'$ref':dict(path='../outside.json',sha256='a'*64,pointer='')},self.root)
        self.accept()
        next(self.root.glob('CLASS_TABLE_ACCEPTED*')).unlink()
        with self.assertRaisesRegex(ValueError,'receipt missing'):c.export_annotations(self.root)

    def test_old_design_not_automatically_closed(self):
        spec=c.read(self.root/'SPEC.json');del spec['c76b2'];(self.root/'SPEC.json').write_text(json.dumps(spec))
        _,scope=self.review()
        self.assertFalse(c.accept(self.p,self.row,self.job,scope))


if __name__=='__main__':unittest.main()
