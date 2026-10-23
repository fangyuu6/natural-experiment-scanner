"""Literature-table isolation, immutable resume and one fresh audit repair."""
from __future__ import annotations
import argparse
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import literature_adoption_intake as adapter
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import Disposition, StageContext, StageResult
from scripts.l2_pipeline.storage import artifact, sha, write_once
from scripts.l2_pipeline.tests.test_pipeline import card


class CorrectionTests(unittest.TestCase):
    def records(self):
        return [{'unit':'IN','start_period':2001,'status':'treated','source_window':{'start_year':2000,'end_year':2004}},
                {'unit':'CA','start_period':None,'status':'never_treated','source_window':{'start_year':2000,'end_year':2004}}]

    def test_outside_both_window_bounds_is_sensitivity_only(self):
        for year in (1999,2005):
            originals=self.records();snapshot=copy.deepcopy(originals)
            primary,sensitivity,corrections=adapter.corrected_records(originals,[{'unit':'IN','status':'known_error','corrected_start_period':year}],[2000,2004])
            self.assertEqual([r['unit'] for r in primary],['CA'])
            self.assertEqual(sensitivity[0]['start_period'],year)
            self.assertTrue(sensitivity[0]['primary_excluded'])
            self.assertEqual(sensitivity[0]['published_start_period'],2001)
            self.assertEqual(corrections,{'IN':year})
            self.assertEqual(originals,snapshot)

    def test_in_window_is_corrected_in_primary_and_invalid_year_rejects(self):
        primary,sensitivity,_=adapter.corrected_records(self.records(),[{'unit':'IN','status':'known_error','corrected_start_period':2003}],[2000,2004])
        self.assertEqual(primary[0]['start_period'],2003)
        self.assertEqual(sensitivity,[])
        for year in (0,-1,True,2003.0,'2003',10000):
            with self.assertRaisesRegex(ValueError,'Invalid annual'):
                adapter.corrected_records(self.records(),[{'unit':'IN','status':'known_error','corrected_start_period':year}],[2000,2004])


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.batch=self.root/'cards';self.batch.mkdir()
        for design in ('a_failed','b_parked','c_ready'):
            value=card();value['design_id']=design;write_once(self.batch/(design+'.json'),value)
        self.args=argparse.Namespace(batch=self.batch,action='downstream',stage='S2',design_id=None,work_dir=self.root/'work',jobs_root=self.root/'jobs',confirmation=self.root/'confirmation.json')
    def tearDown(self):self.tmp.cleanup()

    def test_exception_and_audit_park_do_not_abort_next_design(self):
        def process(ctx,args,pol):
            if ctx.card.design_id=='a_failed':raise ValueError('fixture malformed audit')
            disposition=Disposition.PARKED if ctx.card.design_id=='b_parked' else Disposition.READY
            return StageResult('S2',ctx.card.design_id,disposition,'fixture').as_dict(),0
        with patch.object(adapter,'process_design',side_effect=process):report=adapter.process_batch(self.args)
        self.assertEqual(report['cards'],3)
        self.assertEqual(report['dispositions'],{'failed_no_retry':1,'parked':1,'ready':1})
        self.assertEqual(report['results'][-1]['design_id'],'c_ready')

    def test_repeatable_design_selection_and_missing_id_are_explicit(self):
        self.args.design_id=['b_parked','c_ready','absent']
        with patch.object(adapter,'process_design',side_effect=lambda ctx,args,pol:(StageResult('S2',ctx.card.design_id,Disposition.READY,'fixture').as_dict(),0)) as process:
            report=adapter.process_batch(self.args)
        self.assertEqual(process.call_count,2)
        self.assertEqual(report['results'][-1]['design_id'],'absent')
        self.assertEqual(report['results'][-1]['disposition'],Disposition.FAILED)

    def test_successful_s1_receipt_is_reused_byte_for_byte(self):
        ctx=StageContext(load_card(self.batch/'c_ready.json'),self.root/'work',self.root)
        confirmation=write_once(self.root/'confirmation.json',{'fixture':'hub'})
        treatment=write_once(ctx.design_dir/'TREATMENT.json',{'confirmation_sha256':sha(confirmation)})
        proof=write_once(ctx.design_dir/'S1_IMPORT_PROVENANCE.json',{'evidence':[{'path':'confirmation.json','sha256':sha(confirmation)}]})
        result=StageResult('S1',ctx.card.design_id,Disposition.READY,'Original S1 provenance',artifacts=(artifact(treatment,'treatment'),artifact(proof,'provenance')))
        path=write_once(ctx.design_dir/'receipts/S1/1.json',{'card_sha256':sha(ctx.card.path),'result':result.as_dict()})
        before={p:p.read_bytes() for p in (path,treatment,proof)}
        with patch.object(adapter,'confirmation_check',return_value=confirmation),patch.object(adapter,'collect_job') as collect:
            actual=adapter.finish(ctx,None,confirmation)
        collect.assert_not_called();self.assertEqual(actual['reason'],'Original S1 provenance')
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertEqual(len(list((ctx.design_dir/'receipts/S1').glob('*.json'))),1)


class FreshAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.template_text=(adapter.REPO/'scripts/l2_pipeline/agents/templates/v1/auditor.md').read_text()
        paths={'ADAPTER':'scripts/l2_pipeline/literature_adoption_intake.py','RULING_PATH':'docs/ruling.md','MAP':'data/map.json','MAP_RECEIPT':'data/map_receipt.json'}
        self.patches=[patch.object(adapter,'REPO',self.root)]
        for key,relative in paths.items():
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
            self.patches.append(patch.object(adapter,key,path))
        template=self.root/'scripts/l2_pipeline/agents/templates/v1/auditor.md';template.parent.mkdir(parents=True);template.write_text(self.template_text)
        reader=self.root/'scripts/l3_pooled/dta_reader.py';reader.parent.mkdir(parents=True);reader.write_text('# fixture')
        self.source=write_once(self.root/'source.json',{'fixture':'published source'})
        value=card();value['outcome']['years']=[2000,2004]
        cfg={'universe':[str(i) for i in range(10)],'seed':44,'complete_list_source_id':'fixture','predicate':'fixture','coverage_end':'2004-12-31','historical_convention':'fixture'}
        value['treatment']=cfg
        self.ctx=StageContext(load_card(write_once(self.root/'card.json',value)),self.root/'work',self.root)
        self.records=[{'unit':u,'status':'treated','start_period':2001,'source_window':{'start_year':2000,'end_year':2004}} for u in cfg['universe']]
        self.selection=write_once(self.ctx.design_dir/'AUDIT_SELECTION.json',{'sample':cfg['universe'],'seed':44})
        self.imported=write_once(self.ctx.design_dir/'LITERATURE_IMPORT.json',{'records':self.records})
        self.projection=write_once(self.ctx.design_dir/'LITERATURE_SOURCE_PROJECTION.json',{'source_window':{'start_year':2000,'end_year':2004}})
        self.lock=write_once(self.ctx.design_dir/'LITERATURE_IMPORT_LOCK.json',{'source_path':str(self.source)})
        self.confirmation=write_once(self.root/'confirmation.json',{'fixture':'hub'})
        self.patches.extend([patch.object(adapter,'source_for',return_value=(cfg,{'sha256':sha(self.source)},self.source)),patch.object(adapter,'confirmation_check',return_value=self.confirmation)])
        for p in self.patches:p.start()
        self.jobs=self.root/'jobs'
        self.prior=adapter.emit_auditor(self.ctx,self.records,self.projection,self.imported,self.lock,self.selection,self.jobs)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def output(self,job,unresolved=6,blind=True):
        value={'design_id':self.ctx.card.design_id,'role':'auditor','source_sha256':sha(self.source),'selection_sha256':sha(self.selection),
               'checks':[{'unit':u,'status':'unresolved' if i<unresolved else 'match','official_locator':'https://example.invalid/official'} for i,u in enumerate(job.contract['sample'])]}
        if blind:value['blindness_attestation']={'other_submissions_read':False,'outcome_associations_read':False}
        if 'auditor_identity' in job.contract:value['auditor_identity']=job.contract['auditor_identity']
        return write_once(Path(job.output_path),value)

    def test_unresolved_is_parked_and_fresh_attempt_keeps_originals(self):
        original=self.output(self.prior);before=original.read_bytes()
        first=adapter.finish(self.ctx,self.prior,self.confirmation)
        self.assertEqual(first['disposition'],Disposition.PARKED)
        self.assertEqual(first['metrics']['known_start_errors'],0)
        self.assertEqual(first['metrics']['unresolved'],6)
        _,count=adapter.emit_reaudit(self.ctx,self.jobs,self.confirmation)
        self.assertEqual(count,1)
        replacement=load_job(self.jobs/'reaudits/S1/invented/auditor.attempt2.md')
        self.assertEqual(replacement.contract['sample'],self.prior.contract['sample'])
        self.assertEqual(replacement.contract['selection_sha256'],self.prior.contract['selection_sha256'])
        self.assertIn('attempts/attempt2',replacement.output_path)
        self.assertNotEqual(replacement.promote_path,self.prior.promote_path)
        self.assertTrue(replacement.contract['fresh_auditor_required'])
        self.assertEqual(replacement.contract['search_budget']['maximum_search_queries'],100)
        self.assertFalse(replacement.contract['execution_authorized'])
        self.assertNotIn(str(original),{p['path'] for p in replacement.inputs})
        # Re-emission is idempotent, and resume selects only the replacement.
        adapter.emit_reaudit(self.ctx,self.jobs,self.confirmation)
        self.assertEqual(adapter.current_auditor(self.ctx,self.jobs).job_path,replacement.job_path)
        self.assertEqual(original.read_bytes(),before)
        self.output(replacement,unresolved=3)
        finished=adapter.finish(self.ctx,replacement,self.confirmation)
        self.assertEqual(finished['disposition'],Disposition.READY)
        self.assertEqual(original.read_bytes(),before)
        self.assertTrue(Path(self.prior.promote_path).is_file())
        self.assertTrue(Path(replacement.promote_path).is_file())

    def test_attestation_failure_emits_attempt2_without_repairing_old_answer(self):
        original=self.output(self.prior,blind=False);before=original.read_bytes()
        first=adapter.finish(self.ctx,self.prior,self.confirmation)
        self.assertEqual(first['disposition'],Disposition.PARKED)
        result,count=adapter.emit_reaudit(self.ctx,self.jobs,self.confirmation)
        self.assertEqual(count,1)
        self.assertEqual(result['metrics']['repair_kind'],'blindness_attestation_repair')
        self.assertEqual(original.read_bytes(),before)

    def test_more_than_two_known_errors_is_not_eligible_for_fresh_audit(self):
        value={'design_id':self.ctx.card.design_id,'role':'auditor','source_sha256':sha(self.source),'selection_sha256':sha(self.selection),
               'blindness_attestation':{'other_submissions_read':False,'outcome_associations_read':False},
               'checks':[{'unit':u,'status':'known_error' if i<3 else 'match','official_locator':'https://example.invalid/official','corrected_start_period':2003} for i,u in enumerate(self.prior.contract['sample'])]}
        write_once(Path(self.prior.output_path),value)
        result,count=adapter.emit_reaudit(self.ctx,self.jobs,self.confirmation)
        self.assertEqual(count,0);self.assertEqual(result['disposition'],Disposition.PARKED)
        self.assertFalse((self.jobs/'reaudits/S1/invented/auditor.attempt2.md').exists())


if __name__=='__main__':unittest.main()
