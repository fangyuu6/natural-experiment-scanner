"""T258 (HUB_RULING_20261022_DRAFT_STOP): truthful draft-review notice, support-failed facts in the draft pack, the T3 forward rule
(start_minus_one is not registered when the transition-year drop makes it unsupported) and terminal NOT_EVALUABLE STOPs. No estimates."""
import copy, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.l2_pipeline.agents import review_pack
from scripts.l2_pipeline.autopilot import Autopilot
from scripts.l2_pipeline.hygiene import plan, scientific_card, SMO_RULE


def job(scope):
    return SimpleNamespace(contract={'review_scope': scope})


class NoticeTests(unittest.TestCase):
    def test_sealed_notice_is_byte_identical(self):
        self.assertIs(review_pack.notice_for(job('sealed_candidate')), review_pack.NOTICE)
        self.assertIn('which the pipeline already ran', review_pack.notice_for(job('sealed_candidate')))

    def test_draft_notice_does_not_promise_regression_results(self):
        text = review_pack.notice_for(job('draft_readiness'))
        self.assertNotIn('which the pipeline already ran', text)
        self.assertNotIn('the regression suite in both modes,', text)
        self.assertIn('do NOT exist at this stage', text)
        self.assertIn('is not a finding', text)
        # only the one bullet differs
        self.assertEqual(text.replace(review_pack.DRAFT_BULLET, review_pack.SEALED_BULLET), review_pack.NOTICE)

    def test_support_failed_variants_are_plain_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(review_pack.support_failed_variants(root))
            rows = [dict(variant='primary', passed=True, transition_year_drop=True),
                    dict(variant='start_minus_one', passed=False, disposition='stopped', issues={'unsupported_post_cells': 6}),
                    dict(variant='start_plus_one', passed=True)]
            (root / 'SUPPORT.json').write_text(json.dumps(dict(variants=rows)))
            facts = review_pack.support_failed_variants(root)
            self.assertEqual(facts['source'], 'SUPPORT.json')
            self.assertEqual(facts['variants'], [dict(variant='start_minus_one', disposition='stopped', issues={'unsupported_post_cells': 6})])
            (root / 'SUPPORT_C79.json').write_text(json.dumps(dict(variants=rows[:1] + rows[2:])))
            self.assertEqual(review_pack.support_failed_variants(root)['variants'], [])      # effective file wins
            (root / 'SUPPORT_C79.json').write_text('not json')
            self.assertIsNone(review_pack.support_failed_variants(root))


class ForwardRuleTests(unittest.TestCase):
    def fixture(self, issues=None, drop=True, primary_passed=True):
        card = json.loads((Path(__file__).parents[1] / 'examples/design.json').read_text())
        card.update(analysis=dict(controls='never_treated', weights='equal'), treatment={}, transition_year_drop=drop,
                    sensitivities=[dict(name='start_minus_one', changes={}), dict(name='different', changes={'analysis': {'weights': 'other'}})])
        cells = [dict(g=2000, t=2001, post=True, treated=['A'], controls=['X'], base_period=1999)]
        smo = dict(variant='start_minus_one', passed=False, issues=issues or {'unsupported_post_cells': 6})
        support = dict(variants=[dict(variant='primary', passed=primary_passed, cell_inventory=cells), smo,
                                 dict(variant='different', passed=True, cell_inventory=cells[:1] and [dict(cells[0], treated=['A', 'B'])])])
        return card, support

    def test_default_plan_keeps_start_minus_one(self):
        card, support = self.fixture()
        notes, kept = plan(card, support)
        self.assertNotIn('smo_rule', notes)
        self.assertIn('start_minus_one', [e['name'] for e in kept])

    def test_rule_drops_exactly_the_t3a_case(self):
        card, support = self.fixture()
        before = copy.deepcopy((card, support))
        notes, kept = plan(card, support, smo_rule=True)
        self.assertEqual(notes['smo_rule'], SMO_RULE)
        self.assertEqual([d['name'] for d in notes['dropped']], ['start_minus_one'])
        self.assertNotIn('start_minus_one', [e['name'] for e in kept])
        self.assertEqual((card, support), before)

    def test_every_other_case_stays_registered(self):
        for kwargs in (dict(issues={'unsupported_post_cells': 6, 'min_controls': 1}), dict(drop=False), dict(primary_passed=False),
                       dict(issues={'min_controls': 1})):
            card, support = self.fixture(**kwargs)
            notes, kept = plan(card, support, smo_rule=True)
            self.assertIn('start_minus_one', [e['name'] for e in kept], kwargs)
            self.assertNotIn('start_minus_one', [d['name'] for d in notes['dropped']], kwargs)

    def test_old_specs_replay_with_the_old_plan_and_new_ones_with_the_new(self):
        card, support = self.fixture()
        for flag in (False, True):
            notes, kept = plan(card, support, smo_rule=flag)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'SPEC.json').write_text(json.dumps(dict(c79_hygiene=notes, sensitivities=kept)))
                (root / 'SUPPORT.json').write_text(json.dumps(support))
                names = [s['name'] for s in scientific_card(card, root)['sensitivities']]
                self.assertEqual('start_minus_one' in names, not flag)
                # a SPEC without the marker must not be re-planned with the new rule (it would then differ from its stored notes)
                if not flag:
                    self.assertNotIn('smo_rule', json.loads((root / 'SPEC.json').read_text())['c79_hygiene'])


class TerminalNotEvaluableTests(unittest.TestCase):
    def check(self, rows, row):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'FINAL_DECISIONS_20261020.json').write_text(json.dumps(dict(designs=rows)))
            return Autopilot.closed_not_evaluable(SimpleNamespace(registry=Path(tmp)), row)

    def test_only_this_attempt_closed_while_in_stop(self):
        base = dict(design_id='d', attempt='requeue_v4', decision='NOT_EVALUABLE', state_at_closure='STOP')
        row = dict(design_id='d', attempt_id='requeue_v4')
        self.assertTrue(self.check([base], row))
        self.assertFalse(self.check([dict(base, attempt='requeue_v3')], row))            # reopened under a later attempt
        self.assertFalse(self.check([dict(base, state_at_closure='PARK')], row))
        self.assertFalse(self.check([dict(base, decision='ESTIMATED')], row))
        self.assertFalse(self.check([dict(base, design_id='other')], row))
        self.assertFalse(self.check([], row))

    def test_absent_ledger_is_not_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(Autopilot.closed_not_evaluable(SimpleNamespace(registry=Path(tmp)), dict(design_id='d', attempt_id='a')))


if __name__ == '__main__':
    unittest.main()
