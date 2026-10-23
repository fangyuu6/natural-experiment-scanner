"""T277 / Q3 ruling (HUB_RULING_20261023_PARK_REOPEN.md section 4): a rebound lock carries the source's independence record, N10 needs two distinct workers.

Synthetic roots only; no model call, no real forecast."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.predictions import import_unchanged_predictions,lock_predictions
from scripts.l2_pipeline.forecast_slots import (independence,worker_ids,n10_rule,n10_eligible_record,carried_independence,build_carry,read_carry,CARRY_NAME)

PERSONAS=['Card','Finkelstein','Roth']
POINTS={'Card':1,'Finkelstein':4,'Roth':7}

def slot(i,persona,worker):
    author=dict(worker_id=worker,model_id='claude-sonnet-5-5') if worker is not None else dict(worker_id=None,model_id='claude-sonnet-5-5')
    return dict(slot=i,kind='kind%d'%i,model_id='claude-sonnet-5.5',author_provenance=author,pack_sha256='%064x'%i,prompt_sha256='%064x'%(i+10))

def make_source(root,workers,points=POINTS,keys='new',n_eff=None):
    """A source root with three collected forecasts and a lock. keys: 'none' (legacy), 'new' (keys from `independence`), 'old' (old flag: not single and n>=3)."""
    spec=dict(design_id='invented',track='DiD',population='population',outcome=dict(outcome_unit='hours',years=[1,9]),
        analysis=dict(estimand='ATT',weights='equal',controls='never',clock='full'),
        support=dict(variants=[dict(variant='primary',cell_inventory=[dict(treated=['A','B'],controls=['C'],period=8)])]))
    ensure_once(root/'SPEC.json',spec)
    for i,persona in enumerate(PERSONAS,1):
        folder=root/'predictions'/persona
        original=ensure_once(folder/(persona+'.filled.json'),dict(central_ATT=points[persona],interval_80=[-10,10]))
        value=dict(persona=persona,design_id='invented',spec_sha256=sha(root/'SPEC.json'),point=points[persona],interval=[-10,10],interval_level=.8,
            rationale='invented',disclosures=[],normalization={},forecast_slot=slot(i,persona,workers[persona]))
        submission=ensure_once(folder/'submission.json',value)
        ensure_once(folder/'COLLECTION.json',dict(validated=True,role=persona,stage='S5',design_id='invented',
            original_path=str(original.resolve()),original_sha256=sha(original),promoted_sha256=sha(submission)))
    lock=lock_predictions(root,PERSONAS,'invented',sha(root/'SPEC.json'))
    if keys!='none':
        value=json.loads(lock.read_text());lock.unlink()
        slots={p:json.loads((root/'predictions'/p/'submission.json').read_text())['forecast_slot'] for p in PERSONAS}
        values=[json.loads((root/'predictions'/p/'submission.json').read_text()) for p in PERSONAS]
        ind=independence(values)
        extra=dict(forecast_slots=slots,n_eff=ind['n_eff'] if n_eff is None else n_eff,SINGLE_CONSENSUS=ind['SINGLE_CONSENSUS'],
            n10_eligible=not ind['SINGLE_CONSENSUS'])
        if keys=='new':
            extra=dict(forecast_slots=slots,**independence(values,slots))
            if n_eff is not None:extra['n_eff']=n_eff
        lock.write_text(json.dumps(dict(value,**extra),sort_keys=True))
    return lock

def make_target(root,source):
    ensure_once(root/'SPEC.json',dict(json.loads((source/'SPEC.json').read_text()),version='new'))
    import_unchanged_predictions(source,root,PERSONAS)
    return root

class Fixture(unittest.TestCase):
    def setUp(self):
        self._t=tempfile.TemporaryDirectory();self.addCleanup(self._t.cleanup);self.tmp=Path(self._t.name)
    def pair(self,workers,**kw):
        old=self.tmp/'old';new=self.tmp/'new';lock=make_source(old,workers,**kw);make_target(new,old);return old,new,lock
    def relock(self,new):
        return json.loads(lock_predictions(new,PERSONAS,'invented',sha(new/'SPEC.json')).read_text())

THREE={p:'worker_'+p for p in PERSONAS}
ONE={p:'worker_one' for p in PERSONAS}

class RuleTests(unittest.TestCase):
    def test_worker_ids_fail_closed(self):
        ok={p:slot(1,p,'w_'+p) for p in PERSONAS}
        self.assertEqual(len(worker_ids(ok)),3)
        for bad in (None,{},{'a':None},{**ok,'Card':slot(1,'Card',None)},{**ok,'Card':slot(1,'Card','  ')},{**ok,'Card':dict(slot=1)}):
            self.assertIsNone(worker_ids(bad))
    def test_n10_rule_table(self):
        self.assertTrue(n10_rule(False,3,2));self.assertTrue(n10_rule(False,5,3))
        for args in ((True,3,3),(None,3,3),(False,2,3),(False,3,1),(False,3,None),(False,None,3),(False,True,3),(False,3,True),(0,3,3)):
            self.assertFalse(n10_rule(*args),args)
    def test_independence_with_and_without_slots(self):
        values=[dict(point=p,interval=[-10,10],interval_level=.8) for p in (1,4,7)]
        three={p:slot(i,p,'w'+str(i)) for i,p in enumerate(PERSONAS,1)}
        self.assertTrue(independence(values,three)['n10_eligible']);self.assertEqual(independence(values,three)['n_workers'],3)
        one={p:slot(i,p,'w') for i,p in enumerate(PERSONAS,1)}
        out=independence(values,one);self.assertFalse(out['n10_eligible']);self.assertEqual(out['n_workers'],1);self.assertFalse(out['SINGLE_CONSENSUS'])
        two={**one,'Roth':slot(3,'Roth','other')};self.assertTrue(independence(values,two)['n10_eligible'])
        out=independence(values);self.assertFalse(out['n10_eligible']);self.assertIsNone(out['n_workers'])   # worker data missing -> ineligible
        self.assertFalse(independence(values,{**three,'Roth':slot(3,'Roth',None)})['n10_eligible'])
    def test_reader_requires_two_workers_read_time(self):
        from scripts.l3_meta.reader import n10_eligible
        values=[dict(point=p,interval=[-10,10],interval_level=.8) for p in (1,4,7)]
        def export(workers,**over):
            slots={p:slot(i,p,w) for i,(p,w) in enumerate(workers.items(),1)}
            return dict(forecast_independence=dict(forecast_slots=slots,**independence(values,slots)),**over)
        good=export(THREE);self.assertTrue(n10_eligible(good))
        self.assertFalse(n10_eligible(export(ONE)))
        # 3c: a stored old-style flag True with one worker id reads as not independent; nothing is edited
        legacy=export(ONE);legacy['forecast_independence'].pop('n_workers');legacy['forecast_independence']['n10_eligible']=True
        self.assertFalse(n10_eligible(legacy))
        stored_lie=export(ONE);stored_lie['forecast_independence']['n_workers']=3;stored_lie['forecast_independence']['n10_eligible']=True
        self.assertFalse(n10_eligible(stored_lie))
        no_slots=export(THREE);no_slots['forecast_independence'].pop('forecast_slots');self.assertFalse(n10_eligible(no_slots))
        null=export(THREE);null['forecast_independence']['forecast_slots']['Roth']['author_provenance']['worker_id']=None;self.assertFalse(n10_eligible(null))
        single=export(THREE);single['forecast_independence']['SINGLE_CONSENSUS']=True;self.assertFalse(n10_eligible(single))
        self.assertFalse(n10_eligible(dict(forecast_independence={})));self.assertFalse(n10_eligible({}))

class ImportCarryTests(Fixture):
    def test_import_from_keyed_source_gives_four_keys_and_source_record(self):                    # (i)
        old,new,source_lock=self.pair(THREE)
        proof=json.loads((new/'FORECAST_IMPORT.json').read_text());carried=proof['independence_carried']
        self.assertEqual(carried['source_lock_sha256'],sha(source_lock));self.assertEqual(carried['source_lock_path'],str(source_lock.resolve()))
        self.assertEqual((carried['source_attempt'],carried['n_workers']),('old',3));self.assertEqual(carried['worker_ids'],THREE)
        lock=self.relock(new);source=json.loads(source_lock.read_text())
        for key in ('n_eff','SINGLE_CONSENSUS','n10_eligible','forecast_slots'):self.assertIn(key,lock)
        self.assertEqual((lock['n_eff'],lock['SINGLE_CONSENSUS'],lock['n_workers']),(source['n_eff'],source['SINGLE_CONSENSUS'],3))
        self.assertTrue(lock['n10_eligible']);self.assertEqual(lock['forecast_slots'],source['forecast_slots'])
        self.assertEqual(lock['independence_source'],dict(source_attempt='old',source_lock_path=str(source_lock.resolve()),source_lock_sha256=sha(source_lock),forecast_import='FORECAST_IMPORT.json'))
        self.assertEqual(sha(new/'predictions/Card/Card.filled.json'),sha(old/'predictions/Card/Card.filled.json'))
        # the proof is write-once: importing again changes nothing
        before=(new/'FORECAST_IMPORT.json').read_bytes();import_unchanged_predictions(old,new,PERSONAS);self.assertEqual((new/'FORECAST_IMPORT.json').read_bytes(),before)
    def test_one_worker_source_is_not_n10_eligible(self):                                          # (ii)
        _,new,source_lock=self.pair(ONE,keys='old')
        self.assertTrue(json.loads(source_lock.read_text())['n10_eligible'])                       # the old flag says True, as in the 8 Amendment 2 rows
        lock=self.relock(new)
        self.assertFalse(lock['n10_eligible']);self.assertEqual(lock['n_workers'],1);self.assertFalse(lock['SINGLE_CONSENSUS'])
        from scripts.l3_meta.reader import n10_eligible
        self.assertFalse(n10_eligible(dict(forecast_independence={k:lock[k] for k in ('n_eff','SINGLE_CONSENSUS','n10_eligible','forecast_slots','n_workers')})))
    def test_tampered_source_lock_raises(self):                                                    # (iii)
        _,new,source_lock=self.pair(THREE)
        source_lock.write_text(source_lock.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'Source forecast lock changed'):self.relock(new)
        self.assertFalse((new/'PREDICTIONS_LOCK.json').exists())
    def test_legacy_source_without_keys_locks_without_keys(self):                                  # (iv)
        _,new,_=self.pair(THREE,keys='none')
        self.assertNotIn('independence_carried',json.loads((new/'FORECAST_IMPORT.json').read_text()))
        lock=self.relock(new)
        for key in ('n_eff','SINGLE_CONSENSUS','n10_eligible','forecast_slots','n_workers','independence_source'):self.assertNotIn(key,lock)
    def test_missing_or_null_worker_id_in_keyed_source_fails_closed(self):
        for workers in (dict(THREE,Roth=None),):
            with self.subTest(workers=workers),tempfile.TemporaryDirectory() as t:
                old,new=Path(t)/'old',Path(t)/'new'
                make_source(old,workers,keys='old')
                ensure_once(new/'SPEC.json',dict(json.loads((old/'SPEC.json').read_text()),version='new'))
                with self.assertRaisesRegex(ValueError,'worker ids'):import_unchanged_predictions(old,new,PERSONAS)
    def test_recomputed_independence_must_equal_the_source(self):
        _,new,_=self.pair(THREE,n_eff=1.0)       # a source whose recorded n_eff disagrees with its own forecasts
        with self.assertRaisesRegex(ValueError,'Recomputed independence differs'):self.relock(new)
    def test_old_import_proof_is_never_rewritten_on_resume(self):
        old,new=self.tmp/'old',self.tmp/'new';make_source(old,THREE)
        ensure_once(new/'SPEC.json',dict(json.loads((old/'SPEC.json').read_text()),version='new'))
        with patch('scripts.l2_pipeline.forecast_slots.carried_independence',return_value=None):import_unchanged_predictions(old,new,PERSONAS)   # = a proof written before the fix
        before=(new/'FORECAST_IMPORT.json').read_bytes()
        import_unchanged_predictions(old,new,PERSONAS)                                              # resume with the fixed code: no 'changed' error
        self.assertEqual((new/'FORECAST_IMPORT.json').read_bytes(),before)
        self.assertNotIn('n_eff',self.relock(new))

class SidecarTests(Fixture):
    def legacy_rebound(self,workers,**kw):
        """A rebound as written before the fix: keyed source, import proof without the carried block, keyless lock."""
        old,new=self.tmp/'old',self.tmp/'new';make_source(old,workers,**kw)
        ensure_once(new/'SPEC.json',dict(json.loads((old/'SPEC.json').read_text()),version='new'))
        with patch('scripts.l2_pipeline.forecast_slots.carried_independence',return_value=None):import_unchanged_predictions(old,new,PERSONAS)
        lock_predictions(new,PERSONAS,'invented',sha(new/'SPEC.json'));return old,new
    def write(self,new):
        return ensure_once(new/CARRY_NAME,build_carry(new))
    def test_three_worker_rebound_enters_through_sidecar_only(self):
        old,new=self.legacy_rebound(THREE,keys='old')
        lock_before=(new/'PREDICTIONS_LOCK.json').read_bytes();proof_before=(new/'FORECAST_IMPORT.json').read_bytes()
        self.assertEqual(read_carry(new),(None,[]))                                                 # no sidecar yet
        path=self.write(new)
        self.assertEqual((new/'PREDICTIONS_LOCK.json').read_bytes(),lock_before);self.assertEqual((new/'FORECAST_IMPORT.json').read_bytes(),proof_before)
        block,problems=read_carry(new);self.assertEqual(problems,[]);self.assertTrue(n10_eligible_record(block))
        value=json.loads(path.read_text());self.assertEqual(value['carried']['source_lock_sha256'],sha(old/'PREDICTIONS_LOCK.json'))
        self.assertEqual(value['target_lock']['sha256'],sha(new/'PREDICTIONS_LOCK.json'));self.assertEqual(value['independence']['n_workers'],3)
    def test_one_worker_rebound_stays_out_even_with_a_sidecar(self):
        _,new=self.legacy_rebound(ONE,keys='old')
        self.write(new);block,problems=read_carry(new)
        self.assertEqual(problems,[]);self.assertFalse(block['n10_eligible']);self.assertEqual(block['n_workers'],1);self.assertFalse(n10_eligible_record(block))
    def test_reader_and_sidecar_share_one_eligibility_function(self):
        from scripts.l3_meta.reader import n10_eligible
        _,new=self.legacy_rebound(THREE,keys='old');self.write(new)
        block,_=read_carry(new)
        self.assertTrue(n10_eligible_record(block));self.assertTrue(n10_eligible(dict(forecast_independence=block)))
        # same answer for the same record from the lock writer, the reader and the sidecar block, and one switch flips all three
        with patch('scripts.l2_pipeline.forecast_slots.n10_rule',return_value=False):
            self.assertFalse(n10_eligible_record(block));self.assertFalse(n10_eligible(dict(forecast_independence=block)))
            self.assertFalse(independence([dict(point=p,interval=[-10,10],interval_level=.8) for p in (1,4,7)],block['forecast_slots'])['n10_eligible'])
        for workers,keys in ((THREE,'old'),(ONE,'old'),(dict(THREE,Roth=THREE['Card']),'old')):
            with self.subTest(workers=workers),tempfile.TemporaryDirectory() as t:
                self.tmp=Path(t);_,target=self.legacy_rebound(workers,keys=keys);self.write(target)
                sidecar,_=read_carry(target);lock=self.relock_import(target)
                self.assertEqual(n10_eligible(dict(forecast_independence=sidecar)),lock)
    def relock_import(self,target):
        """What the fixed lock writer would say about the same facts (a fresh target with the carried proof)."""
        old=self.tmp/'old';fresh=self.tmp/'fresh';make_target(fresh,old)
        lock=json.loads(lock_predictions(fresh,PERSONAS,'invented',sha(fresh/'SPEC.json')).read_text())
        from scripts.l3_meta.reader import n10_eligible
        return n10_eligible(dict(forecast_independence={k:lock[k] for k in ('n_eff','SINGLE_CONSENSUS','n10_eligible','forecast_slots','n_workers')}))
    def test_sidecar_is_rejected_when_any_hash_moves(self):
        for what in ('target_lock','source_lock','import','carry'):
            with self.subTest(what=what),tempfile.TemporaryDirectory() as t:
                self.tmp=Path(t);old,new=self.legacy_rebound(THREE,keys='old');self.write(new)
                if what=='target_lock':(new/'PREDICTIONS_LOCK.json').write_text((new/'PREDICTIONS_LOCK.json').read_text()+' ')
                if what=='source_lock':(old/'PREDICTIONS_LOCK.json').write_text((old/'PREDICTIONS_LOCK.json').read_text()+' ')
                if what=='import':(new/'FORECAST_IMPORT.json').write_text((new/'FORECAST_IMPORT.json').read_text()+' ')
                if what=='carry':
                    v=json.loads((new/CARRY_NAME).read_text());v['independence']['n_workers']=1;v['independence']['n10_eligible']=True;(new/CARRY_NAME).write_text(json.dumps(v))
                block,problems=read_carry(new);self.assertIsNone(block);self.assertTrue(problems)
    def test_sidecar_refused_when_not_needed_or_unsupported(self):
        _,new,_=self.pair(THREE);self.relock(new)                   # the fixed import already has the keys
        with self.assertRaisesRegex(ValueError,'already carries'):build_carry(new)
        with tempfile.TemporaryDirectory() as t:
            self.tmp=Path(t);_,legacy=self.legacy_rebound(THREE,keys='none')
            with self.assertRaisesRegex(ValueError,'no independence record'):build_carry(legacy)
        with tempfile.TemporaryDirectory() as t:
            self.tmp=Path(t)
            old,new=Path(t)/'old',Path(t)/'new'
            make_source(old,dict(THREE,Roth=None),keys='none')
            ensure_once(new/'SPEC.json',dict(json.loads((old/'SPEC.json').read_text()),version='new'))
            with patch('scripts.l2_pipeline.forecast_slots.carried_independence',return_value=None):import_unchanged_predictions(old,new,PERSONAS)
            lock_predictions(new,PERSONAS,'invented',sha(new/'SPEC.json'))
            # source lock gains keys with a null worker id afterwards: the sidecar must fail closed
            src=json.loads((old/'PREDICTIONS_LOCK.json').read_text());src.update(n_eff=3.0,SINGLE_CONSENSUS=False,n10_eligible=True,
                forecast_slots={p:json.loads((old/'predictions'/p/'submission.json').read_text())['forecast_slot'] for p in PERSONAS})
            (old/'PREDICTIONS_LOCK.json').write_text(json.dumps(src))
            with self.assertRaisesRegex(ValueError,'worker ids'):build_carry(new)

if __name__=='__main__':unittest.main()
