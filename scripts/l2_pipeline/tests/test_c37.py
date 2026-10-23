"""Wave completeness and prospective gates, without dispatch or outcome fits."""
import copy
import builtins
import io
import json
import os
from pathlib import Path
import tempfile
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.intake import c37, v8b_calendar
from scripts.l2_pipeline.intake.config import IntakeConfig
from scripts.l2_pipeline.intake.fresh_gate import fresh_gate


class C37WaveTests(unittest.TestCase):
    def rows(self, n):
        return [dict(policy_id=f'synthetic:{i:02}', screening_pass=True,
                     mapping=dict(family='firearms')) for i in range(n)]

    def test_overflow_keeps_every_design_across_waves(self):
        rows = c37.order_waves(self.rows(19))
        self.assertEqual([sum(r['wave'] == w for r in rows) for w in (1, 2, 3, 4)], [6, 6, 6, 1])
        self.assertEqual({r['policy_id'] for r in rows}, {r['policy_id'] for r in self.rows(19)})
        self.assertEqual(sum(r['emit_eligible'] for r in rows), 6)
        self.assertTrue(all(r['disposition'] == 'QUEUED_NEXT_WAVE' for r in rows[6:]))

    def test_spare_current_wave_slots_are_used(self):
        rows = self.rows(4)
        rows.append(dict(policy_id='synthetic:other', screening_pass=False, mapping=dict(family='health')))
        scheduled = c37.order_waves(rows, occupied={'firearms': 4})
        self.assertEqual(sum(r['emit_eligible'] for r in scheduled), 3)
        self.assertEqual(sum(r['wave'] == 2 for r in scheduled), 2)
        self.assertEqual(rows[0], self.rows(4)[0])

    def test_invalid_wave_occupancy_is_refused(self):
        for occupied in ({'firearms': 7}, {'firearms': -1}):
            with self.assertRaises(ValueError): c37.order_waves(self.rows(1), occupied=occupied)


class C37CalibrationTests(unittest.TestCase):
    def fixture(self):
        sample = [f'U{i}' for i in range(8)]
        job = SimpleNamespace(contract=dict(sample=sample))
        checks = [dict(unit=u, status='unresolved', official_locator={'url': 'https://official.example/law'},
                       class_a_offset=False) for u in sample]
        checks[0].update(status='match', class_a_offset=True, direction_match=True,
                         panel_year=2000, official_operational_calendar_year=2000)
        return job, dict(checks=checks, blindness_attestation=dict(other_submissions_read=False,
                         outcome_associations_read=False))

    def verdict(self, job, answer):
        with patch.object(c37.audit, 'validate_audit_clock'):
            return c37.calibration_verdict(job, answer)

    def test_class_a_confirmation_does_not_certify_unresolved_states(self):
        job, answer = self.fixture(); before = copy.deepcopy(answer)
        verdict = self.verdict(job, answer)
        self.assertTrue(verdict['confirmed'])
        self.assertEqual(len(verdict['unresolved_states']), 7)
        self.assertFalse(verdict['s1_ready']); self.assertFalse(verdict['execution_authorized'])
        self.assertEqual(answer, before)

    def test_one_contradicting_class_a_blocks_the_whole_design(self):
        job, answer = self.fixture()
        answer['checks'][0].update(status='known_error', direction_match=False,
                                   official_operational_calendar_year=1999)
        verdict = self.verdict(job, answer)
        self.assertFalse(verdict['confirmed']); self.assertEqual(verdict['verdict'], 'CONTRADICTED')

    def test_no_class_a_cannot_confirm_direction(self):
        job, answer = self.fixture(); answer['checks'][0]['class_a_offset'] = False
        verdict = self.verdict(job, answer)
        self.assertFalse(verdict['confirmed']); self.assertEqual(verdict['verdict'], 'INCONCLUSIVE')

    def test_structured_empty_locator_is_rejected(self):
        job, answer = self.fixture(); answer['checks'][0]['official_locator'] = []
        with self.assertRaises(ValueError): self.verdict(job, answer)


class C37PreparedGateTests(unittest.TestCase):
    def setUp(self):
        # Archived packet paths and credentials bind the original repository.
        # A nested devcopy reads those fixtures in place, using devcopy code;
        # neither packet bytes nor scientific contracts are rebound for tests.
        stack=ExitStack();self.addCleanup(stack.close)
        repo=Path(__file__).resolve().parents[3]
        if (repo.parent.name=='devcopy' and repo.parents[1].name=='l2_pipeline'
                and repo.parents[2].name=='data' and (repo/'COPY_MANIFEST.json').is_file()):
            source=repo.parents[3]
            for name in ('WAVE','FILTERED','COLLECTION'):
                stack.enter_context(patch.object(c37,name,source/getattr(c37,name).relative_to(repo)))
        from scripts.l2_pipeline import v8b
        for name in ('ROOT','ATTEMPT','CALIBRATION_SEED','AUDIT_SEED'):
            stack.enter_context(patch.object(v8b_calendar,name,getattr(v8b_calendar,name)))
        stack.enter_context(patch.object(v8b,'validate_job',v8b.validate_job))
        # Prime tempfile's one-time probe before enforcing a fully read-only run.
        tempfile.gettempdir()
        def guarded_open(original):
            def checked(path,mode='r',*args,**kwargs):
                if any(c in mode for c in 'wax+'):
                    raise AssertionError('C37 archived fixtures are read-only')
                return original(path,mode,*args,**kwargs)
            return checked
        raw_open=os.open
        def checked_os_open(path,flags,*args,**kwargs):
            if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
                raise AssertionError('C37 archived fixtures are read-only')
            return raw_open(path,flags,*args,**kwargs)
        stack.enter_context(patch('builtins.open',guarded_open(builtins.open)))
        stack.enter_context(patch('io.open',guarded_open(io.open)))
        stack.enter_context(patch('os.open',checked_os_open))
        for name in ('mkdir','rmdir','unlink','remove','rename','replace','chmod'):
            stack.enter_context(patch.object(os,name,side_effect=AssertionError('C37 archived fixtures are read-only')))

    def test_c36_fresh_remains_blocked_and_has_disjoint_seeds(self):
        # Skip on a clean source checkout without task-generated prospective inputs.
        if not (c37.WAVE / 'INTAKE.json').exists(): self.skipTest('C37 preparation absent')
        config = IntakeConfig.from_existing(c37.WAVE)
        v8b_calendar.configure(config); v8b_calendar.install()
        report = c37.read(c37.WAVE / 'INTAKE.json')
        self.assertEqual(report['summary']['emitted'], 3)
        for row in report['rows']:
            plan = row['sample_plan']
            self.assertEqual((plan['calibration_seed'], plan['audit_seed']), (361010, 362010))
            self.assertFalse(set(plan['calibration']) & set(plan['fresh_audit']))
            self.assertFalse(set(plan['calibration']) & set(plan['previously_audited']))
        acceptance = c37.WAVE / 'work/c36_cspp_guncontrol_opencarry/calibration/agents/auditor/attempts/c36_20261010/CALIBRATION_HUB_ACCEPTANCE.json'
        if acceptance.exists():
            # C39 collected the inconclusive calibration; the gate still refuses.
            with self.assertRaisesRegex(ValueError, 'Fresh audit requires hash-bound hub-confirmed calibration'):
                fresh_gate(c37.WAVE, validate_audit_clock=v8b_calendar.validate_audit_clock)
        else:
            with self.assertRaises(FileNotFoundError):
                fresh_gate(c37.WAVE, validate_audit_clock=v8b_calendar.validate_audit_clock)

    def test_filtered_death_penalty_gate_and_original_scientific_bytes(self):
        if not (c37.FILTERED / 'INTAKE.json').exists(): self.skipTest('C37 collection absent')
        v8b_calendar.configure(IntakeConfig.from_existing(c37.FILTERED)); v8b_calendar.install()
        fresh_gate(c37.FILTERED, validate_audit_clock=v8b_calendar.validate_audit_clock)
        provenance = c37.read(c37.COLLECTION / 'FILTERED_JOB_PROVENANCE.json')['jobs']
        for row in provenance:
            old = c37.read(Path(row['original']['path']).with_suffix('.job.json'))
            new = c37.read(Path(row['relocated']['path']).with_suffix('.job.json'))
            self.assertEqual(old['contract'], new['contract'])
            self.assertEqual(old['inputs'], new['inputs'])
            self.assertEqual(new['contract']['sample'], row['sample'])


if __name__ == '__main__': unittest.main()
