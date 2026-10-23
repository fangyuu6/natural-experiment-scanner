"""C59 queue routing regressions; synthetic metadata and checkpoints only."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.c59_queue import require_complete_history
from scripts.l2_pipeline.population_resolution import precheck
from scripts.l2_pipeline.storage import sha


class QueueTests(unittest.TestCase):
    def test_literature_route_may_have_no_statute_but_must_have_papers(self):
        import json
        from copy import deepcopy
        from scripts.l2_pipeline.cards import validate_card
        from scripts.l2_pipeline.first_order import first_order_gate
        root=Path(__file__).resolve().parents[3]
        card=json.loads((root/'scripts/l2_pipeline/examples/design.json').read_text())
        card['first_order'].update(purpose_route='established_literature',statute_refs=[],
            literature_refs=[dict(source_ref='https://fixture.edu/paper'+str(i),title='Distinct study '+str(i),
                locator='Abstract',supports=['target_outcome']) for i in (1,2)])
        self.assertEqual(validate_card(card),[])
        one=deepcopy(card);one['first_order']['literature_refs']=one['first_order']['literature_refs'][:1]
        self.assertFalse(first_order_gate(one)['passed'])
        missing=deepcopy(card);missing['first_order']['literature_refs']=[]
        self.assertTrue(validate_card(missing))
        statutory=deepcopy(card);statutory['first_order']['purpose_route']='statute'
        self.assertTrue(any('Statute purpose route' in e for e in validate_card(statutory)))

    def test_incomplete_checkpoint_parks_without_inventing_unknown(self):
        job = SimpleNamespace(role='B', contract={'jobs_kind':'official_history_collection'})
        value = {'collection_complete':False, 'records':[{'unit':'AA', 'status':None}]}
        with self.assertRaises(Park) as caught: require_complete_history(job, value)
        self.assertEqual(caught.exception.metrics['unclassified_units'], ['AA'])
        self.assertIsNone(value['records'][0]['status'])
        self.assertEqual(caught.exception.metrics['extra_replacements'], 0)

    def test_null_status_and_explicit_incomplete_both_park(self):
        job = SimpleNamespace(role='A', contract={'jobs_kind':'official_history_collection'})
        for value in ({'records':[{'unit':'AA','status':None}]}, {'collection_complete':False,'records':[]}):
            with self.assertRaises(Park): require_complete_history(job,value)
        require_complete_history(job, {'records':[{'unit':'AA','status':'unknown'}]})
        require_complete_history(SimpleNamespace(contract={}), {'collection_complete':False})

    def test_intake_catalog_hashes_do_not_skip_verified_purpose(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp).resolve(); pilot=Autopilot(repo/'data/l2_pipeline/registry', Backend(repo))
            bound=repo/'metadata.json'; atomic(bound, {'synthetic':True})
            pointer={'path':str(bound),'sha256':sha(bound)}
            selection={k:pointer for k in ('catalog','population_metadata','authority')}
            card=repo/'CARD.json'; row={'card':str(card)}
            first={'purpose_rule':'V6.2','evidence_status':'pending_S0_verification','population_resolution':selection}
            atomic(card, {'first_order':first})
            purpose={'status':'VERIFIED','target_construct':'known_construct'}
            with patch('scripts.l2_pipeline.population_resolution.apply',return_value={'requeued':True}) as apply:
                self.assertEqual(precheck(pilot,row,purpose), {'requeued':True})
                apply.assert_called_once_with(pilot,row,purpose,scheduler_owned=True)
            first.update(purpose_rule='V6.3',evidence_status='verified_by_hub')
            atomic(card, {'first_order':first})
            with patch('scripts.l2_pipeline.population_resolution.apply') as apply:
                self.assertIsNone(precheck(pilot,row,purpose)); apply.assert_not_called()

    def test_clean_predecessor_resolves_existing_replacement_before_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp).resolve(); pilot=Autopilot(repo/'data/l2_pipeline/registry',Backend(repo))
            row={'design_id':'fixture','attempt_id':'v1'}
            original=repo/'original.md'; cleaned=repo/'clean.md'; replacement=repo/'replacement.md'
            for path in (original,cleaned,replacement):
                out=repo/path.stem
                atomic(path.with_suffix('.job.json'),dict(role='B',contract={'jobs_kind':'official_history_collection'},
                    output_dir=str(out),output_path=str(out/'submission.json'),result_path=str(out/'result.txt')))
            slot=pilot.replacement_slot(row,original)
            atomic(slot/'REPLACEMENT.json',dict(original_job=str(cleaned),replacement_job=str(replacement)))
            atomic(repo/'replacement/DISPATCH_ONCE.json',{'active':True})
            with patch('scripts.l2_pipeline.agents.history_packet.clean_pending',return_value=str(cleaned)) as clean:
                self.assertEqual(pilot.effective_job(row,original),str(replacement))
                clean.assert_called_once()

    def test_running_original_is_not_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp).resolve();pilot=Autopilot(repo/'data/l2_pipeline/registry',Backend(repo))
            row={'design_id':'fixture','attempt_id':'v1'};path=repo/'old.md';out=repo/'out'
            atomic(path.with_suffix('.job.json'),dict(role='A',contract={'jobs_kind':'official_history_collection'},
                output_dir=str(out),output_path=str(out/'submission.json'),result_path=str(out/'result.txt')))
            atomic(out/'DISPATCH_ONCE.json',{'active':True})
            with patch('scripts.l2_pipeline.agents.history_packet.clean_pending') as clean:
                self.assertEqual(pilot.effective_job(row,path),str(path));clean.assert_not_called()
