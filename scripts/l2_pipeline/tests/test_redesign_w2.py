"""REDESIGN W2 (HUB_RULING_20261017_REDESIGN section 3): one forecast packet, one review, no third direction coder, A/B disagreement
states go to the fresh audit, the screen tests both coders' rosters. Synthetic data only; no outcome value is read or estimated."""
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.l2_pipeline import redesign, rules
from scripts.l2_pipeline.agents import collect_job
from scripts.l2_pipeline.agents.jobs import emit_stage_jobs, load_job
from scripts.l2_pipeline.autopilot import Backend, atomic, read
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.predictions import lock_predictions
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_autopilot import OfficialBridgeTests
from scripts.l2_pipeline.tests.test_c56 import ContractTests
from scripts.l2_pipeline.tests.test_redesign_w1 import Base, rules_value
from scripts.l2_pipeline.tests.test_window_20261017_fixes import F2ResumeKeepsPurposeTests, window_lock

CHAIN_R2 = [a for a in ['history', 'reconcile', 'screen', 'audit', 'S0', 'S1', 'S2', 'S3', 'S4', 'ri', 'purpose', 'purpose-direction', 'forecasts',
                        'lock', 'draft-dry-load', 'draft', 'prep', 'approve-prep', 'calibrate', 'seal', 'dry-load', 'sealed', 'approve-final',
                        'execute', 'S7', 'register'] if a != 'sealed']


def rules_value_r2(**changes):
    value = rules_value(version='R2', chain=CHAIN_R2, reconcile=dict(min_retained_treated=6, min_retained_controls=5, count_audit_resolvable=True),
                        audit=dict(conflict_rows=True, conflict_cap=10, exclude_prior_sample=False),
                        forecast=dict(mode='bundle'), review=dict(mode='single'), direction=dict(third_coder=False))
    value.update(changes)
    return value


# --- 1-3 forecast bundle --------------------------------------------------------------------------------------------------------------

class BundleTests(ContractTests):
    ATTESTATION = dict(other_submissions_read=False, this_design_outcomes_read=False, this_design_estimates_read=False, outside_slot_pack_read=True)

    def bundle(self, attempt='a1_forecasts'):
        return emit_stage_jobs(self.ctx, 'S5', roles=['forecast_bundle'], jobs_root=self.root / 'jobs', attempt_id=attempt)[0]

    def ledgers(self, job, worker='bundle_worker'):
        return {persona: dict(persona=persona, design_id=job.design_id, spec_sha256=job.contract['spec_sha256'], point=1 + i, interval=[-1, 4],
                              interval_level=.8, rationale='synthetic primary ATT original units', disclosures=[],
                              author_provenance=dict(worker_id=worker, model_id=member['model_id']), blindness_attestation=dict(self.ATTESTATION))
                for i, (persona, member) in enumerate(sorted(job.contract['bundle_members'].items()))}

    def write(self, job, members, **top):
        path = Path(job.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(design_id=job.design_id, spec_sha256=job.contract['spec_sha256'], members=members, **top)))

    def member_outputs(self, job):
        return [Path(m['output_path']) for m in job.contract['bundle_members'].values()]

    def test_bundle_one_job_three_ledgers(self):
        job = self.bundle()
        self.assertEqual((job.stage, job.role), ('S5', 'forecast_bundle'))
        self.assertEqual(set(job.contract['bundle_members']), {'Card', 'Finkelstein', 'Roth'})
        for persona, member in job.contract['bundle_members'].items():       # the per-persona jobs exist, hash-bound, marked as bundle members
            member_job = load_job(Path(member['job_path']))
            self.assertEqual((member_job.job_sha256, member_job.role), (member['job_sha256'], persona))
            self.assertIs(member_job.contract['bundle_member'], True)
        plain = emit_stage_jobs(self.ctx, 'S5', jobs_root=self.root / 'plain')   # R0 / R1 emission: three jobs, no bundle marker
        self.assertEqual(len(plain), 3)
        self.assertTrue(all('bundle_member' not in j.contract for j in plain))
        # Backend.jobs hands out one packet for an R2 card, three for R0 / R1
        backend = Backend(self.ctx.repo)
        pilot = SimpleNamespace(case_dir=lambda row: self.root / 'case' / row['attempt_id'])
        for attempt, rules_doc, expected in (('r0', None, 3), ('r1', dict(version='R1'), 3), ('r2', dict(version='R2'), 1)):
            with patch.object(Backend, 'ctx', return_value=self.ctx), patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_doc):
                self.assertEqual(len(backend.jobs(dict(design_id=self.ctx.card.design_id, attempt_id=attempt), 'forecasts', pilot)), expected)
        # the author writes one file; the collector splits it into the three ledgers and each is collected through its own job
        self.write(job, self.ledgers(job))
        receipt = collect_job(job.job_path)
        self.assertIs(receipt['validated'], True)
        self.assertIs(receipt['single_author_bundle'], True)
        for persona in job.contract['bundle_members']:
            value = read(self.ctx.design_dir / 'predictions' / persona / 'submission.json')
            self.assertIs(value['single_author_bundle'], True)
            self.assertTrue((self.ctx.design_dir / 'predictions' / persona / 'COLLECTION.json').is_file())
        self.assertTrue((self.ctx.design_dir / 'predictions' / 'forecast_bundle' / 'COLLECTION.json').is_file())
        personas = sorted(job.contract['bundle_members'])
        from scripts.l2_pipeline.forecast_slots import roster
        lock = lock_predictions(self.ctx.design_dir, list(roster(self.ctx)), job.design_id, job.contract['spec_sha256'])
        locked = json.loads(lock.read_text())
        self.assertTrue(any('forecast_bundle' in name and name.endswith('bundle.json') for name in locked['original_files']))
        self.assertEqual(len({s['author_provenance']['worker_id'] for s in locked['forecast_slots'].values()}), 1)
        from scripts.l2_pipeline.ops.forecast_authors import annotation
        note = annotation(self.ctx.design_dir, {})
        self.assertIs(note['shared_author_personas'], True)
        self.assertEqual(note['shared_workers'], {'bundle_worker': personas})

    def test_bundle_rejects_one_bad_member(self):
        job = self.bundle()
        members = self.ledgers(job)
        members['Roth']['author_provenance']['model_id'] = 'some-other-model'
        self.write(job, members)
        for call in (lambda: collect_job(job.job_path, self_check=True), lambda: collect_job(job.job_path)):
            with self.assertRaisesRegex(ValueError, 'Forecast model differs from registered slot'):
                call()
        self.assertFalse(any(p.exists() for p in self.member_outputs(job)))     # nothing was written for the good members either
        self.assertFalse((self.ctx.design_dir / 'predictions' / 'forecast_bundle' / 'COLLECTION.json').exists())
        # one correction through the existing replacement: same registered contract, fresh attempt, nothing else changes
        from scripts.l2_pipeline.agents.replacement import emit, rejection_kind
        members['Roth'] = self.ledgers(job)['Roth']
        del members['Finkelstein']
        self.write(job, members)
        with self.assertRaises(ValueError) as found:
            collect_job(job.job_path)
        self.assertEqual(rejection_kind(found.exception), 'SCHEMA_FORMAT')
        again = emit(job.job_path, self.root / 'replacement', 'c45_test')
        self.assertEqual(again.contract['bundle_members'], job.contract['bundle_members'])
        self.write(again, self.ledgers(again))
        self.assertIs(collect_job(again.job_path)['validated'], True)
        self.assertTrue(all(p.is_file() for p in self.member_outputs(job)))
        self.assertTrue((self.ctx.design_dir / 'predictions' / 'forecast_bundle' / 'COLLECTION.json').is_file())

    def test_bundle_attestation_keys(self):
        job = self.bundle()
        for label, change in (('outside false', dict(outside_slot_pack_read=False)), ('outcomes read', dict(this_design_outcomes_read=True)),
                              ('peers read', dict(other_submissions_read=True))):
            members = self.ledgers(job)
            members['Card']['blindness_attestation'].update(change)
            self.write(job, members)
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'Forecast slot blindness unconfirmed'):
                collect_job(job.job_path, self_check=True)
        members = self.ledgers(job)
        members['Card']['blindness_attestation']['extra'] = False
        self.write(job, members)
        with self.assertRaisesRegex(ValueError, 'Forecast slot blindness unconfirmed'):
            collect_job(job.job_path, self_check=True)
        members = self.ledgers(job)
        members['Roth']['author_provenance']['worker_id'] = 'someone_else'
        self.write(job, members)
        with self.assertRaisesRegex(ValueError, 'share one author'):
            collect_job(job.job_path, self_check=True)
        # a per-persona job that is not a bundle member keeps the old rule: outside_slot_pack_read must stay false
        single = emit_stage_jobs(self.ctx, 'S5', jobs_root=self.root / 'single')[0]
        value = dict(self.ledgers(self.bundle('a2_forecasts'))['Card'], persona=single.role)
        value['author_provenance']['model_id'] = single.contract['forecast_slot']['model_id']
        Path(single.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(single.output_path).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Forecast slot blindness unconfirmed'):
            collect_job(single.job_path, self_check=True)
        value['blindness_attestation']['outside_slot_pack_read'] = False
        Path(single.output_path).write_text(json.dumps(value))
        self.assertIs(collect_job(single.job_path, self_check=True)['validated'], True)

    def test_bundle_claim_once(self):
        from scripts.l2_pipeline.agents.sonnet_queue import claim, enqueue, route
        with patch.dict(os.environ, {'L2_CLAUDE_ONLY': '1'}):
            job = self.bundle()
            self.assertEqual(route(job.design_id, job.role, job.contract['role_backend_overrides']), 'sonnet_queue')
            queue = self.root / 'queue_bundle'
            self.assertEqual(enqueue(job.job_path, {}, queue)['backend'], 'sonnet_queue')
            first = claim(queue, 'worker_one')
            self.assertIsNotNone(first)
            from scripts.l2_pipeline.agents.sonnet_queue import identity, self_check
            self.write(job, self.ledgers(job))                               # the worker's own check runs the same collector, write-free
            checked = self_check(queue, identity(job.job_path), first['token'])
            self.assertIs(checked['validated'], True)
            self.assertFalse(any(p.exists() for p in self.member_outputs(job)))
            self.assertIsNone(claim(queue, 'worker_one'))                     # one packet: nothing left for anyone
            self.assertIsNone(claim(queue, 'worker_two'))
            # R0 / R1 persona rule untouched: one session, one persona per design
            persona_queue = self.root / 'queue_personas'
            for persona_job in emit_stage_jobs(self.ctx, 'S5', jobs_root=self.root / 'persona_jobs'):
                enqueue(persona_job.job_path, {}, persona_queue)
            self.assertIsNotNone(claim(persona_queue, 'worker_one'))
            self.assertIsNone(claim(persona_queue, 'worker_one'))
            self.assertIsNotNone(claim(persona_queue, 'worker_two'))


# --- 4 one review ---------------------------------------------------------------------------------------------------------------------

class SingleReviewTests(unittest.TestCase):
    def setUp(self):
        self.new_root()

    def new_root(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def freeze(self, review, snapshot=None, extra=()):
        ensure_once(self.root / 'SPEC.json', dict(spec=1))
        ensure_once(self.root / 'RULES_SNAPSHOT.json', snapshot if snapshot is not None else rules_value_r2())
        (self.root / 'REVIEW.json').write_text(json.dumps(review))
        names = ['SPEC.json', 'REVIEW.json', 'RULES_SNAPSHOT.json', *extra]
        ensure_once(self.root / 'FREEZE_FINAL.json', dict(files={n: dict(sha256=sha(self.root / n)) for n in names}))
        return sha(self.root / 'FREEZE_FINAL.json')

    def passing(self, **changes):
        review = dict(design_id='d', candidate_sha256=sha(self.root / 'SPEC.json') if (self.root / 'SPEC.json').exists() else None,
                      review_scope='draft_readiness', complete_sweeps=1, fix_cycles=0, must=[], should=[], verdict='PASS',
                      independence_attestation=dict(wrote_implementation=False, wrote_fixes=False, real_outcome_associations_read=False))
        review.update(changes)
        return review

    def test_single_review_final_approval(self):
        from scripts.l2_pipeline.stages.s6_execute import sealed_review_evidence
        ensure_once(self.root / 'SPEC.json', dict(spec=1))
        candidate = self.freeze(self.passing())
        evidence = sealed_review_evidence(self.root, candidate, 'd', 'hub')      # no EXECUTION_REVIEW.json anywhere
        self.assertEqual(evidence['review_mode'], 'single')
        self.assertFalse((self.root / 'EXECUTION_REVIEW.json').exists())
        with self.assertRaisesRegex(ValueError, 'frozen candidate'):             # REVIEW.json changed after the freeze
            (self.root / 'REVIEW.json').write_text(json.dumps(self.passing(verdict='PASS ')))
            sealed_review_evidence(self.root, candidate, 'd', 'hub')
        # the same bytes under R1 still demand the sealed review
        self.new_root()
        ensure_once(self.root / 'SPEC.json', dict(spec=1))
        candidate = self.freeze(self.passing(), snapshot=rules_value())
        with self.assertRaises((OSError, ValueError)):
            sealed_review_evidence(self.root, candidate, 'd', 'hub')

    def test_single_review_requires_pass_or_a_frozen_hub_resolution(self):
        from scripts.l2_pipeline.stages.s6_execute import sealed_review_evidence
        ensure_once(self.root / 'SPEC.json', dict(spec=1))
        stop = self.passing(verdict='STOP', must=[dict(id='m', description='d', evidence='e', impact='incorrect_registered_number')])
        with self.assertRaisesRegex(ValueError, 'PASS or hub resolution'):
            sealed_review_evidence(self.root, self.freeze(stop), 'd', 'hub')
        # a resolved review: the resolution file is frozen with the candidate and names the same signer
        self.new_root()
        ensure_once(self.root / 'SPEC.json', dict(spec=1))
        ensure_once(self.root / 'DRAFT_RESOLUTION_x.json', dict(hub_signer='hub'))
        resolved = dict(design_id='d', candidate_sha256=sha(self.root / 'SPEC.json'), review_scope='draft_readiness', status='RESOLVED_SINGLE_REVIEW',
                        independent_verdict='STOP', execution_authorized=False, resolution_path=str(self.root / 'DRAFT_RESOLUTION_x.json'),
                        resolution_sha256=sha(self.root / 'DRAFT_RESOLUTION_x.json'))
        candidate = self.freeze(resolved, extra=('DRAFT_RESOLUTION_x.json',))
        self.assertIn('review_resolution_sha256', sealed_review_evidence(self.root, candidate, 'd', 'hub'))
        with self.assertRaisesRegex(ValueError, 'signer'):
            sealed_review_evidence(self.root, candidate, 'd', 'another hub')


# --- 5 no third direction coder ---------------------------------------------------------------------------------------------------------

class DirectionTests(unittest.TestCase):
    def run_action(self, rules_doc):
        from scripts.l2_pipeline.agents import purpose_direction as module
        ctx = SimpleNamespace(card=SimpleNamespace(payload={}), repo=Path('.'), design_dir=Path('.'))
        pilot = SimpleNamespace(backend=SimpleNamespace(ctx=lambda row: ctx), case_dir=lambda row: Path('.'),
                                collect_submissions=lambda row, action, jobs: jobs)
        emitted = []
        emit = lambda c, role, root: emitted.append(role) or SimpleNamespace(job_path=role)
        with patch.object(module, 'refs', return_value=True), patch.object(module, 'emit', side_effect=emit), \
                patch.object(module, 'resolve', side_effect=[None, dict(purpose_direction=1)]), patch('scripts.l2_pipeline.c70_l3.protected', return_value=False), \
                patch.object(module, 'missing', side_effect=lambda c, reason, detail=None: dict(reason=reason, purpose_direction=None)), \
                patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_doc):
            return module._action(pilot, {}), emitted

    def test_direction_disagreement_no_third_job(self):
        value, emitted = self.run_action(rules_value_r2())
        self.assertEqual(emitted, ['purpose_direction_A', 'purpose_direction_B'])
        self.assertEqual(value, dict(reason='coders_disagree', purpose_direction=None))
        value, emitted = self.run_action(rules_value())                          # R1: the third coder is still emitted
        self.assertEqual(emitted, ['purpose_direction_A', 'purpose_direction_B', 'purpose_direction_third'])
        value, emitted = self.run_action(None)                                   # R0 too
        self.assertEqual(len(emitted), 3)


# --- 6-8 reconcile floor, audit conflict rows, accept -----------------------------------------------------------------------------------

class ConflictBase(OfficialBridgeTests):
    N, TREATED = 40, 30
    POLICY_EXCLUDED = ['U0', 'U1']

    def setUp(self):
        from scripts.l2_pipeline.agents import collect_job as collect
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        from scripts.l2_pipeline.contracts import DesignCard, StageContext
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.universe = ['U' + str(n) for n in range(self.N)]
        self.card = read(Path(__file__).parents[1] / 'examples/design.json')
        self.card['treatment'].update(universe=self.universe, excluded_units=[])
        self.card['mechanism'] = self.card['treatment']['predicate'] = 'one'
        self.card['transition_year_drop'] = True
        self.path = ensure_once(self.root / 'card.json', self.card)
        self.jobs = emit_pair(self.card, self.universe, root=self.root / 'route', attempt_id='test')
        for job in self.jobs:
            path = Path(job.output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            output = golden_output(job)
            for record in output['records'][self.TREATED:]:
                record.update(status='never_treated', start_period=None, evidence_status='absent (V2_LIST)',
                              absence_basis=dict(official_code_history_no_earlier_enactment=True, no_earlier_in_compilations_on_disk=True,
                                                 no_search_log_contradiction=True))
                record['evidence_refs'][0]['supports'] = 'absence_code_history'
            path.write_text(json.dumps(output))
            collect(job.job_path)
        self.repo = Path(__file__).resolve().parents[3]
        self.use_card(self.card)
        self.policy = dict(seed=55, excluded_units=list(self.POLICY_EXCLUDED), sample_size=10)

    def use_card(self, card, name='card.json'):
        from scripts.l2_pipeline.contracts import DesignCard, StageContext
        self.path = ensure_once(self.root / name, card) if not (self.root / name).exists() else self.root / name
        self.ctx = StageContext(DesignCard.from_mapping(card, self.path), self.root / 'work', self.repo)

    def disagree(self, units, kind='known_conflict'):
        """Make coder B differ from coder A on `units` and collect B again."""
        job = self.jobs[1]
        out = Path(job.output_path)
        value = read(out)
        for record in value['records']:
            if record['unit'] in units:
                record['best_evidence_start_year'] += 1
                record['evidence_refs'][0]['year'] += 1
                if kind == 'known_conflict':
                    record['start_period'] += 1
        out.write_text(json.dumps(value))
        for name in ('COLLECTION.json', 'NORMALIZATION.json', 'NORMALIZED_HISTORY.json'):
            (out.parent / name).unlink()
        collect_job(job.job_path)

    def prepare_r2(self):
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_value_r2()):
            return self.prepare()

    def audit_r2(self, job, dated=(), unresolved=(), errors=0):
        from scripts.l2_pipeline.agents.contract_tests import check_job_types, golden_output
        check_job_types([job])
        output = golden_output(job)
        for check in output['checks'][:errors]:
            check.update(status='known_error', corrected_start_period=2003)
        rows = [dict(unit=u, status='official_date', official_locator='https://example.invalid/law', corrected_start_period=2005) for u in dated]
        rows += [dict(unit=u, status='unresolved', official_locator='https://example.invalid/law') for u in unresolved]
        if job.contract.get('conflict_units'):
            output['conflict_checks'] = rows
        path = Path(job.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output))
        return collect_job(job.job_path)


class ReconcileFloorTests(ConflictBase):
    N, TREATED = 17, 12

    def scenario(self, retained, conflicts):
        """12 treated states: `retained` agreed, `conflicts` disputed by A and B, the rest excluded by the card itself."""
        treated = ['U' + str(i) for i in range(12)]
        card = deepcopy(self.card)
        card['treatment']['excluded_units'] = treated[retained + conflicts:]
        self.use_card(card, 'card_%d_%d.json' % (retained, conflicts))
        self.disagree(treated[retained:retained + conflicts])

    def test_reconcile_reachable_floor(self):
        self.scenario(retained=4, conflicts=1)                                   # 4 retained + 1 audit-resolvable = 5 < 6
        with self.assertRaises(Park) as parked:
            self.prepare_r2()
        self.assertIn('retained treated below minimum', str(parked.exception))
        self.assertEqual(parked.exception.metrics['counts']['audit_conflict_states'], 1)

    def test_reconcile_reachable_floor_reached(self):
        self.scenario(retained=4, conflicts=2)                                   # 4 + 2 = 6: the audit can still reach the minimum
        job = self.prepare_r2()
        self.assertEqual([c['unit'] for c in job.contract['conflict_units']], ['U4', 'U5'])
        report = read(self.ctx.design_dir / 'RECONCILIATION.json')
        self.assertEqual(report['counts']['retained_treated'], 4)
        # R0 / R1: the same design (a fresh copy) parks at the old floor, the audit-resolvable states never count there
        self.setUp()
        self.scenario(retained=4, conflicts=2)
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_value()), self.assertRaises(Park) as old:
            self.prepare()
        self.assertNotIn('audit_conflict_states', old.exception.metrics['counts'])

    def test_prior_sample_exclusions_no_longer_park(self):
        self.policy['excluded_units'] = ['U' + str(i) for i in range(12)]       # every treated state was in a prior audit sample
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_value()), self.assertRaises(Park) as old:
            self.prepare()
        self.assertEqual(old.exception.metrics['counts']['fresh_treated'], 0)
        self.setUp()
        self.policy['excluded_units'] = ['U' + str(i) for i in range(12)]
        job = self.prepare_r2()                                                  # R2: the pool is every retained treated state
        self.assertEqual(len(job.contract['sample']), 10)
        self.assertTrue(set(job.contract['sample']) <= {'U' + str(i) for i in range(12)})
        self.assertIs(read(self.ctx.design_dir / 'RECONCILIATION.json')['fresh_policy']['pool_rule'], False)


class AuditConflictTests(ConflictBase):
    def test_audit_contract_has_conflict_rows(self):
        disputed = ['U' + str(i) for i in range(12)]                             # 12 disputed states, 18 agreed
        self.disagree(disputed)
        self.policy['excluded_units'] = []
        job = self.prepare_r2()
        sample = job.contract['sample']
        listed = [c['unit'] for c in job.contract['conflict_units']]
        self.assertEqual(listed, sorted(disputed)[:10])                          # cap 10, sorted
        self.assertEqual(len(sample), 10)
        self.assertFalse(set(sample) & set(disputed))
        self.assertTrue(all(c['a']['start_period'] + 1 == c['b']['start_period'] for c in job.contract['conflict_units']))
        self.assertIn('v3_r1', job.template_path)
        report = read(self.ctx.design_dir / 'RECONCILIATION.json')
        self.assertEqual([c['unit'] for c in report['audit_conflict_units']], listed)
        cfg = read(self.ctx.design_dir / 'C42_PREPARATION.json')['prepared_payload']['treatment']
        self.assertTrue(set(disputed) <= set(cfg['excluded_units']))             # every disputed state stays excluded until the audit dates it
        # the V3 gate counts the random sample only: ten unresolved conflict rows do not fail it
        self.audit_r2(job, unresolved=listed)
        from scripts.l2_pipeline.autopilot_history import accept
        self.assertEqual(accept(self.ctx, job.job_path).disposition, 'ready')
        # and a conflict row missing from the answer is refused by the collector
        self.setUp()
        self.disagree(disputed)
        self.policy['excluded_units'] = []
        job = self.prepare_r2()
        with self.assertRaisesRegex(ValueError, 'prelocked conflict states') as refused:
            self.audit_r2(job, unresolved=listed[:-1])
        from scripts.l2_pipeline.agents.replacement import rejection_kind
        self.assertEqual(rejection_kind(refused.exception), 'SCHEMA_FORMAT')     # the one correction through the existing replacement

    def test_accept_resolves_conflicts(self):
        from scripts.l2_pipeline.autopilot_history import accept
        from scripts.l2_pipeline.c42 import effective_context
        disputed = ['U2', 'U3', 'U4']
        self.disagree(['U2', 'U3'])
        self.disagree(['U4'], kind='best_evidence_year_conflict')
        self.policy['excluded_units'] = []
        job = self.prepare_r2()
        self.assertEqual({c['unit']: c['kind'] for c in job.contract['conflict_units']},
                         {'U2': 'known_conflict', 'U3': 'known_conflict', 'U4': 'best_evidence_year_conflict'})
        self.audit_r2(job, dated=['U2', 'U4'], unresolved=['U3'])
        with patch('scripts.l2_pipeline.rules.of_payload', return_value=rules_value_r2()):
            result = accept(self.ctx, job.job_path)
        self.assertEqual(result.disposition, 'ready')
        treatment = read(self.ctx.design_dir / 'TREATMENT.json')
        by_unit = {r['unit']: r for r in treatment['records']}
        self.assertEqual((by_unit['U2']['audited_first_full_start_period'], by_unit['U2']['start_period']), (2005, 2004))   # engine clock = operation year
        self.assertEqual(by_unit['U4']['audited_first_full_start_period'], 2005)
        self.assertEqual(by_unit['U2']['audit_resolution']['kind'], 'known_conflict')
        self.assertNotIn('U3', by_unit)                                          # unresolved: still excluded
        self.assertIn('U3', treatment['excluded_primary_units'])
        self.assertNotIn('U2', treatment['excluded_primary_units'])
        self.assertEqual(treatment['excluded_conflict_states'], ['U3'])
        proof = read(self.ctx.design_dir / 'S1_IMPORT_PROVENANCE.json')
        self.assertTrue(any(Path(i['path']).name == 'AUDIT_RESOLUTIONS.json' for i in proof['evidence']))
        ctx = effective_context(self.ctx)                                        # the overlay builds from the hash-valid resolutions
        self.assertEqual(ctx.card.payload['treatment']['excluded_conflict_states'], ['U3'])
        self.assertNotIn('U2', ctx.card.payload['treatment']['excluded_units'])
        path = self.ctx.design_dir / 'AUDIT_RESOLUTIONS.json'
        edited = read(path)
        edited['resolved'][0]['start_period'] += 1
        path.chmod(0o644)
        path.write_text(json.dumps(edited))
        with self.assertRaisesRegex(ValueError, 'Audit resolutions differ'):
            effective_context(self.ctx)

    def test_conflict_resolution_rules(self):
        from scripts.l2_pipeline.intake.official_history import resolve_conflicts, conflict_rows
        comparison = dict(gaps=[dict(unit='B', kind='known_conflict', a={}, b={}), dict(unit='A', kind='best_evidence_year_conflict', a={}, b={}),
                                dict(unit='C', kind='one_sided_missing'), dict(unit='D', kind='known_conflict', a={}, b={})])
        self.assertEqual([c['unit'] for c in conflict_rows(comparison)], ['A', 'B', 'D'])
        self.assertEqual([c['unit'] for c in conflict_rows(comparison, excluded={'A'}, cap=1)], ['B'])
        units = conflict_rows(comparison)
        good = [dict(unit='A', status='official_date', official_locator='p1', corrected_start_period=2003),
                dict(unit='B', status='unresolved', official_locator='p2'), dict(unit='D', status='unresolved', official_locator='p3')]
        resolved, unresolved = resolve_conflicts(units, good)
        self.assertEqual(([r['unit'] for r in resolved], unresolved), (['A'], ['B', 'D']))
        for bad in ([good[0], good[1]], good + [dict(unit='A', status='unresolved', official_locator='x')],
                    [dict(good[0], corrected_start_period=None)] + good[1:], [dict(good[1], status='match')] + [good[0], good[2]],
                    [dict(good[0], official_locator='')] + good[1:]):
            with self.assertRaises(ValueError):
                resolve_conflicts(units, bad)


# --- 9 screen on both coders' rosters ---------------------------------------------------------------------------------------------------

class ScreenTests(Base):
    def conflict_report(self, row):
        root = self.pilot.backend.root(row)
        card = read(row['card'])
        units = card['treatment']['universe']
        agreed = [dict(unit=u, status='treated', start_period=2006, best_evidence_start_year=2005, mechanism='one', locator='t') for u in units[:4]]
        a = dict(unit=units[4], status='treated', start_period=2006, best_evidence_start_year=2005, mechanism='one', locator='t')
        b = dict(a, start_period=2009, best_evidence_start_year=2008)
        gap = dict(unit=units[4], kind='known_conflict', a=a, b=b)
        ensure_once(root / 'RECONCILIATION.json', dict(historical_comparison=dict(accepted=agreed, gaps=[gap], counts={}), counts=dict(retained_treated=4),
                                                       audit_conflict_units=[dict(unit=units[4], kind='known_conflict', a=a, b=b)]))

    def rows(self):
        self.publish()
        self.publish(rules_value_r2())
        return self.make_row('s1', mutate=lambda c: c['automation'].update(rules=self.pin('R2')))

    def test_screen_two_rosters_park_only_if_both_fail(self):
        row = self.rows()
        self.conflict_report(row)
        fail = lambda reason='s3': dict(status='PRESCREEN_PARK', variants=[dict(variant='primary', passed=False, reasons=[reason])])
        ok = dict(status='PROCEED', variants=[dict(variant='primary', passed=True, reasons=[])])
        seen = []

        def evaluate(*args, **kwargs):
            seen.append({r['unit']: r['start_period'] for r in kwargs['roster']})
            return results.pop(0)
        for results, parks in (([fail(), ok], False), ([ok, fail()], False), ([fail(), fail()], True)):
            seen.clear()
            with patch('scripts.l2_pipeline.prescreen.evaluate', side_effect=evaluate):
                if parks:
                    with self.assertRaises(Park) as raised:
                        redesign.screen_action(self.pilot, row)
                    self.assertIn('Registered support insufficient', str(raised.exception))
                else:
                    self.assertEqual(redesign.screen_action(self.pilot, row)['status'], 'PROCEED')
            unit = read(row['card'])['treatment']['universe'][4]
            self.assertEqual([r[unit] for r in seen], [2005, 2008])             # coder A's roster, then coder B's (operation-year labels)
            self.assertTrue(all(len(r) == 5 for r in seen))
        # R1 rules: one roster, the disputed state is simply excluded
        self.setUp()
        self.publish()
        row = self.make_row('s2')
        self.conflict_report(row)
        with patch('scripts.l2_pipeline.prescreen.evaluate', return_value=dict(status='PROCEED', variants=[])) as evaluate:
            redesign.screen_action(self.pilot, row)
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(len(evaluate.call_args.kwargs['roster']), 4)

    def test_unknown_check_fails_closed_and_reconcile_check_needs_r2(self):
        self.assertEqual(redesign.run_check('no_such_check', self.pilot, {}, rules_value_r2())[0], False)
        row = self.rows()
        self.conflict_report(row)
        self.assertFalse(redesign.run_check('reconcile_reachable', self.pilot, row, rules_value())[0])        # R1 cannot count them
        self.assertFalse(redesign.run_check('reconcile_reachable', self.pilot, row, rules_value_r2())[0])     # 4 retained + 1 = 5 < 6
        report = self.pilot.backend.root(row) / 'RECONCILIATION.json'
        value = read(report)
        value['historical_comparison']['gaps'] += [dict(unit=u, kind='known_conflict', a={}, b={}) for u in ('Z1', 'Z2')]
        report.chmod(0o644)
        atomic(report, value)
        self.assertTrue(redesign.run_check('reconcile_reachable', self.pilot, row, rules_value_r2())[0])      # 4 + 3 = 7
        with patch.dict(redesign.CHECKS, {'boom': lambda *a: (_ for _ in ()).throw(KeyError('x'))}):
            self.assertFalse(redesign.run_check('boom', self.pilot, row, rules_value_r2())[0])

    def test_plan_reopens_reconcile_floor_designs_only_under_r2(self):
        self.publish()
        self.publish(rules_value_r2(park_reasons=rules_value()['park_reasons'] + [
            dict(id='RECONCILE_FLOOR', gates=['reconcile'], contains=['retained treated below minimum'], disposition='PARK', reopen='check:reconcile_reachable')]))
        row = self.make_row('p1', pinned=False)
        atomic(self.pilot.case_dir(row) / 'PARKED.json', dict(design_id='p1', attempt_id=row['attempt_id'], action='reconcile', status='PARKED',
                                                              reason='V7 retained treated below minimum', metrics={}))
        self.conflict_report(row)
        self.done(row, 'history')
        report = self.pilot.backend.root(row) / 'RECONCILIATION.json'
        value = read(report)
        value['historical_comparison']['gaps'] += [dict(unit=u, kind='known_conflict', a={}, b={}) for u in ('Z1', 'Z2')]
        report.chmod(0o644)
        atomic(report, value)
        by_version = {v: {i['design_id']: i for i in redesign.plan(self.pilot, v)}['p1'] for v in ('R1', 'R2')}
        self.assertFalse(by_version['R1']['eligible'])
        self.assertTrue(by_version['R2']['eligible'], by_version['R2']['why_not'])
        self.assertEqual(by_version['R2']['class_id'], 'RECONCILE_FLOOR')


# --- rules R2, the shipped file, the window step ------------------------------------------------------------------------------------

class RulesR2Tests(unittest.TestCase):
    def test_r2_schema(self):
        self.assertEqual(rules.validate(rules_value_r2())['version'], 'R2')
        for bad, message in ((rules_value_r2(chain=CHAIN_R2[:12] + ['sealed'] + CHAIN_R2[12:]), 'no sealed step'),
                             (rules_value_r2(forecast=dict(mode='three')), 'forecast'),
                             (rules_value_r2(review=dict(mode='double')), 'review'),
                             (rules_value_r2(direction=dict(third_coder=True)), 'direction'),
                             (rules_value_r2(audit=dict(max_known_errors=2, max_unresolved=5, sample_size=10)), 'conflict_rows'),
                             (rules_value_r2(audit=dict(conflict_rows=True, conflict_cap=10, exclude_prior_sample=False, max_known_errors=2)), 'exclude_prior_sample'),
                             (rules_value_r2(audit=dict(conflict_rows=True, conflict_cap=10, pool_rule=False)), 'exclude_prior_sample'),
                             (rules_value_r2(audit=dict(max_known_errors=2, max_unresolved=5, sample_size=10, conflict_rows=True, conflict_cap=11, exclude_prior_sample=False)), 'conflict_cap'),
                             (rules_value_r2(reconcile=dict(min_retained_treated=6, min_retained_controls=5)), 'count_audit_resolvable')):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                rules.validate(bad)
        r2_only = rules_value_r2()
        del r2_only['review']
        with self.assertRaisesRegex(ValueError, 'Missing rules section'):
            rules.validate(r2_only)
        with self.assertRaisesRegex(ValueError, 'Unknown rules key'):
            rules.validate(rules_value(forecast=dict(mode='bundle')))          # R1 files do not carry the R2 sections
        with self.assertRaisesRegex(ValueError, 'conflict_rows|max_known_errors'):
            rules.validate(rules_value(audit=dict(max_known_errors=2, max_unresolved=5, sample_size=10, conflict_rows=True, conflict_cap=10, exclude_prior_sample=False)))

    def test_shipped_rules_r2_file(self):
        repo = Path(__file__).resolve().parents[3]
        candidates = [repo / 'docs/plans/l2-pipeline/rules/RULES_R2.json',
                      repo / 'docs/plans/l2-pipeline/redesign-w2/new/docs/plans/l2-pipeline/rules/RULES_R2.json']
        path = next(p for p in candidates if p.is_file())
        value = rules.validate(json.loads(path.read_text()))
        shipped_r1 = json.loads((repo / 'docs/plans/l2-pipeline/rules/RULES_R1.json').read_text())
        self.assertEqual(value['park_reasons'], shipped_r1['park_reasons'])      # the park table is unchanged; RECONCILE_FLOOR keeps its check
        self.assertEqual([a for a in shipped_r1['chain'] if a != 'sealed'], value['chain'])
        self.assertEqual(rules.diff(shipped_r1, value).keys() - {'note'}, {'chain', 'reconcile', 'audit', 'forecast', 'review', 'direction'})


class PurposeKeptWindowTests(F2ResumeKeepsPurposeTests):
    def stopped_after_a_dropped_purpose(self):
        """As the base fixture, but the v3 card is left for the window step to write (it differs once the card carries the rules stamp)."""
        from scripts.l2_pipeline import requeue as module
        self.accept_purpose()
        with patch.object(module, 'kept_purpose', return_value=None):
            first = module.requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        row = first['new_row']
        self.pilot.stop(row, 'purpose', 'ValueError: NEEDS_RULING: V6.2 target/population requires new interpretation')
        return row

    def test_apply_runs_in_the_open_window_whatever_its_name(self):
        from scripts.l2_pipeline import purpose_kept_reopen as module
        from scripts.l2_pipeline.c85 import RULE
        self.stopped_after_a_dropped_purpose()
        (self.repo / RULE).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / RULE).write_text('fixture C85 ruling')
        window_lock(self.registry, 'WINDOW_REDESIGN_2')
        with patch.object(module, 'CLASS', (self.card['design_id'],)):
            with self.assertRaisesRegex(ValueError, 'WINDOW_20261017 stopped code window'):   # an explicit task still has to match the lock
                module.apply(self.pilot, task='WINDOW_20261017')
            done = module.apply(self.pilot)                                                # no argument: the lock's WINDOW_* task
            self.assertEqual([d['restart_action'] for d in done], ['purpose-direction'])
            window_lock(self.registry, 'OTHER')
            with self.assertRaisesRegex(ValueError, 'stopped code window'):
                module.apply(self.pilot)

    def test_the_five_cards_carry_the_current_rules_stamp_and_nothing_else_changes(self):
        from scripts.l2_pipeline import purpose_kept_reopen as module
        from scripts.l2_pipeline.c85 import RULE
        from scripts.l2_pipeline.storage import sha
        self.stopped_after_a_dropped_purpose()
        (self.repo / RULE).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / RULE).write_text('fixture C85 ruling')
        window_lock(self.registry)
        old_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        old_card = read(old_row['card'])
        with patch.object(rules, 'REPO', self.repo), patch.object(module, 'CLASS', (self.card['design_id'],)):
            for n, value in enumerate((rules_value(), rules_value_r2()), 1):
                source = self.repo / ('rules_source_%d.json' % n)
                source.write_text(json.dumps(value))
                if n == 2:   # the pointer is still R1: the five must not run on it
                    with self.assertRaisesRegex(ValueError, 'Rules R2 must be published'):
                        module.apply(self.pilot)
                    self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0], old_row)
                rules.publish(source, 'test publish R%d' % n, self.repo)
            done = module.apply(self.pilot)
            self.assertEqual([d['restart_action'] for d in done], ['S0'])               # the R2 chain's first undone step (as redesign request)
            self.assertEqual(done[0]['purpose_kept_from'], 'old')                       # the accepted purpose is kept as the label
            new_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
            card = read(new_row['card'])
            pin = rules.descriptor('R2', self.repo)
            self.assertEqual(card['automation']['rules'], pin)
            stripped = json.loads(json.dumps(card))
            stripped['automation'].pop('rules')
            if not stripped['automation']:
                stripped.pop('automation')
            expected = dict(old_card)
            expected.pop('automation', None)
            stripped.pop('automation', None)
            self.assertEqual({k: v for k, v in stripped.items() if k != 'version'}, {k: v for k, v in expected.items() if k != 'version'})
            ruling = read(self.repo / 'data/l2_pipeline/autopilot/purpose_kept/rulings' / (self.card['design_id'] + '.json'))
            self.assertEqual((ruling['rules'], ruling['implementation_rule']), (pin, 'REDESIGN_R1'))   # the receipt records the stamp
            self.assertEqual(rules.pin_of_row(new_row), pin)
            self.assertEqual(module.apply(self.pilot), [])


for _cls, _parent in ((PurposeKeptWindowTests, F2ResumeKeepsPurposeTests), (ReconcileFloorTests, OfficialBridgeTests),
                      (AuditConflictTests, OfficialBridgeTests), (ConflictBase, OfficialBridgeTests), (BundleTests, ContractTests),
                      (ScreenTests, Base)):
    for _name in dir(_parent):
        if _name.startswith('test_') and _name not in _cls.__dict__:
            setattr(_cls, _name, None)
del _cls, _parent, _name, OfficialBridgeTests, ContractTests, F2ResumeKeepsPurposeTests, Base


if __name__ == '__main__':
    unittest.main()
