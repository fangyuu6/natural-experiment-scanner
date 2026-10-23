"""T226 supply efficiency (2026-10-22): packet telemetry, closeness-to-FINISHED claim order, the perfect-history count-floor gate,
the shared source pack (P1), no re-queue of host-blocked unit sets (P2), release of unattempted packets (P4), the park text.
Synthetic data only; no network, no outcome value."""
import csv
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import count_floor, release_policy
from scripts.l2_pipeline.agents import history_chunks as hc
from scripts.l2_pipeline.agents import packet_telemetry as tele
from scripts.l2_pipeline.agents import queue_order
from scripts.l2_pipeline.agents import shared_pack
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.agents import unit_blockers as ub
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.tests.test_release_fix_20261018 import ReleaseCase


# --- (c) perfect-history count floor ------------------------------------------------------------------------------------------

FLOORS = dict(min_treated=6, min_cohorts=2, min_controls=5, min_pre_periods=3, min_post_periods=2, source='card')


class CountFloorTests(unittest.TestCase):
    def rect(self, units, years):
        return {(u, y) for u in units for y in years}

    def test_wide_panel_is_reachable(self):
        units = ['S%02d' % i for i in range(30)]
        years = list(range(1990, 2011))
        r = count_floor.perfect_history_figures(set(units), years, self.rect(units, years), FLOORS)
        self.assertTrue(r['reachable'])
        self.assertEqual(r['treatable_max'], 30)
        self.assertGreaterEqual(r['cohorts_max'], 2)

    def test_six_years_allow_only_one_cohort(self):
        units = ['S%02d' % i for i in range(30)]
        years = list(range(2008, 2014))   # 3 pre + 2 post needs a start in {2011, 2012}: with 6 years exactly 2 starts? check below
        r = count_floor.perfect_history_figures(set(units), years, self.rect(units, years), FLOORS)
        # pre>=3 and post>=2 inside 2008-2013: starts 2011 and 2012 are feasible, so two cohorts are possible
        self.assertEqual(sorted({s for s in range(2008, 2014)}) and r['cohorts_max'], 2)
        short = list(range(2008, 2013))   # 5 years: only start 2011
        r = count_floor.perfect_history_figures(set(units), short, self.rect(units, short), FLOORS)
        self.assertFalse(r['reachable'])
        self.assertEqual(r['cohorts_max'], 1)
        self.assertTrue(any('cohorts' in x for x in r['reasons']))

    def test_transition_year_drop_needs_one_more_year(self):
        units = ['S%02d' % i for i in range(30)]
        years = list(range(2008, 2014))
        plain = count_floor.perfect_history_figures(set(units), years, self.rect(units, years), FLOORS)
        drop = count_floor.perfect_history_figures(set(units), years, self.rect(units, years), FLOORS, transition_year_drop=True)
        self.assertEqual(plain['cohorts_max'], 2)
        self.assertEqual(drop['cohorts_max'], 1)

    def test_no_usable_data_fails(self):
        units = ['S%02d' % i for i in range(20)]
        r = count_floor.perfect_history_figures(set(units), list(range(1990, 2011)), set(), FLOORS)
        self.assertFalse(r['reachable'])
        self.assertEqual(r['treatable_max'], 0)

    def test_few_usable_units_cannot_hold_treated_and_controls(self):
        units = ['S%02d' % i for i in range(10)]
        years = list(range(1990, 2011))
        r = count_floor.perfect_history_figures(set(units), years, self.rect(units, years), FLOORS)   # 10 units < 6 + 5
        self.assertFalse(r['reachable'])
        self.assertTrue(any('min_treated + min_controls' in x for x in r['reasons']))

    def test_not_yet_treated_controls_need_fewer_units(self):
        units = ['S%02d' % i for i in range(10)]
        years = list(range(1990, 2011))
        r = count_floor.perfect_history_figures(set(units), years, self.rect(units, years), FLOORS, controls='not_yet_treated')
        self.assertTrue(r['reachable'])

    def test_units_outside_the_universe_are_ignored(self):
        units = ['S%02d' % i for i in range(30)]
        years = list(range(1990, 2011))
        r = count_floor.perfect_history_figures({'S00', 'S01'}, years, self.rect(units, years), FLOORS)
        self.assertEqual(r['usable_units'], 2)
        self.assertFalse(r['reachable'])

    def test_floors_never_below_engine_and_rules_raise_them(self):
        weak = count_floor.floors_in_force(dict(min_treated=1, min_cohorts=1, min_controls=1, min_pre_periods=1))
        self.assertEqual((weak['min_treated'], weak['min_cohorts'], weak['min_controls'], weak['min_pre_periods']), (6, 2, 5, 3))
        rules = {'version': 'R3', 'structural': dict(max_removed_share=[1, 3], min_treated=10, min_cohorts=3, min_controls=5, min_audited_retained=5)}
        strong = count_floor.floors_in_force(dict(min_treated=6, min_cohorts=2, min_controls=5, min_pre_periods=3), rules)
        self.assertEqual((strong['min_treated'], strong['min_cohorts']), (10, 3))
        self.assertEqual(strong['source'], 'card+rules_R3')
        r1 = count_floor.floors_in_force(dict(min_treated=6), {'version': 'R1'})
        self.assertEqual(r1['min_treated'], 6)   # R1 and R2 have no structural floors: nothing is added

    def test_hook_parks_only_a_proceed_result_and_keeps_other_fields(self):
        base = dict(status='PROCEED', reasons=[], variants=[1])
        bad = dict(status='COUNT_FLOOR', reasons=['S3_COUNT_FLOOR: x'], variants=[])
        with patch.object(count_floor, 'evaluate', return_value=bad):
            out = count_floor.apply_to_prescreen(base, {}, None)
            self.assertEqual(out['status'], 'PRESCREEN_PARK')
            self.assertEqual(out['reasons'], ['S3_COUNT_FLOOR: x'])
            self.assertEqual(out['variants'], [1])
            self.assertEqual(count_floor.apply_to_prescreen(dict(status='PRESCREEN_PARK', reasons=['a']), {}, None)['reasons'], ['a'])
        with patch.object(count_floor, 'evaluate', return_value=dict(status='INCONCLUSIVE', reasons=[], variants=[])):
            self.assertEqual(count_floor.apply_to_prescreen(base, {}, None)['status'], 'PROCEED')
        with patch.object(count_floor, 'evaluate', return_value=dict(status='PROCEED', reasons=[], variants=[])):
            self.assertEqual(count_floor.apply_to_prescreen(base, {}, None)['status'], 'PROCEED')

    def test_non_did_card_is_inconclusive(self):
        self.assertEqual(count_floor.evaluate(dict(track='RDD'))['status'], 'INCONCLUSIVE')
        self.assertEqual(count_floor.evaluate(dict(track='DiD', unit='county'))['status'], 'INCONCLUSIVE')
        self.assertEqual(count_floor.evaluate(dict(track='DiD'))['status'], 'INCONCLUSIVE')   # broken card: never raises

    def test_gate0_reason_maps_to_s3_for_the_audit(self):
        import inspect
        from scripts.l2_pipeline import gate0
        self.assertIn("'s3_count_floor':'S3'", inspect.getsource(gate0.record_audit))

    def test_live_cards_known_to_fail_are_parked_and_a_wide_card_is_not(self):
        reg = Path(__file__).resolve().parents[3] / 'data/l2_pipeline/registry/ACTIVE_DESIGNS.json'
        if not reg.is_file():
            self.skipTest('no live registry')
        rows = {r['design_id']: r for r in json.loads(reg.read_text())['designs']}
        seen = {}
        for design in ('b1011tl_reinsurance_1332_waiver', 'b1010_alr'):
            row = rows.get(design)
            if row is None or not Path(row['card']).is_file():
                self.skipTest('card missing')
            seen[design] = count_floor.evaluate(json.loads(Path(row['card']).read_text()))['status']
        self.assertEqual(seen['b1011tl_reinsurance_1332_waiver'], 'COUNT_FLOOR')
        self.assertEqual(seen['b1010_alr'], 'PROCEED')


# --- (b) claim order ----------------------------------------------------------------------------------------------------------

class TmpQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.root = self.repo / 'data/l2_pipeline/registry/SONNET_QUEUE'
        for s in q.STATES:
            (self.root / s).mkdir(parents=True)

    def packet(self, name, design, enqueued, done_actions=()):
        p = self.root / 'pending' / name
        p.mkdir()
        (p / 'PACKET.json').write_text(json.dumps(dict(row=dict(design_id=design, attempt_id='a1'))))
        (p / 'STATE.json').write_text(json.dumps(dict(enqueued_at=enqueued, expiries=0)))
        case = self.repo / 'data/l2_pipeline/autopilot' / design / 'a1/actions'
        case.mkdir(parents=True, exist_ok=True)
        for action in done_actions:
            (case / (action + '.json')).write_text('{}')
        return p

    def names(self, now):
        return [p.name for p in q.pending_order(self.root, now=now)]


class QueueOrderTests(TmpQueue):
    def test_fewer_remaining_stages_first_then_oldest_then_name(self):
        now = 10_000
        self.packet('p1', 'far', 100)
        self.packet('p2', 'near', 300, ['history', 'reconcile', 'audit', 'purpose', 'S0', 'S1', 'S2', 'S3'])
        self.packet('p3', 'mid', 200, ['history', 'reconcile', 'audit'])
        self.packet('p4', 'near2', 250, ['history', 'reconcile', 'audit', 'purpose', 'S0', 'S1', 'S2', 'S3'])
        self.packet('p0', 'far0', 100)
        self.assertEqual(self.names(now), ['p4', 'p2', 'p3', 'p0', 'p1'])   # near2 and near tie on stages: older first; far ties: name

    def test_a_packet_waiting_past_the_cap_goes_first(self):
        self.packet('old', 'far', 1_000)
        self.packet('new', 'near', 500_000, ['history', 'reconcile', 'audit', 'purpose', 'S0', 'S1', 'S2', 'S3'])
        now = 1_000 + queue_order.AGE_CAP_SECONDS + 5
        self.assertEqual(self.names(now), ['old', 'new'])
        self.assertEqual(self.names(1_000 + 60), ['new', 'old'])

    def test_unreadable_row_sorts_last_without_error(self):
        self.packet('good', 'far', 100)
        bad = self.root / 'pending' / 'bad'
        bad.mkdir()
        (bad / 'STATE.json').write_text(json.dumps(dict(enqueued_at=1)))
        (bad / 'PACKET.json').write_text('not json')
        self.assertEqual(self.names(500), ['good', 'bad'])

    def test_requeued_time_is_used_for_ties(self):
        a = self.packet('a', 'd1', 100)
        b = self.packet('b', 'd2', 100)
        (a / 'STATE.json').write_text(json.dumps(dict(enqueued_at=100, requeued_at=900)))
        self.assertEqual(self.names(1000), ['b', 'a'])


class GateExitTests(TmpQueue):
    def test_gate_exit_reads_park_stop_finished_and_unknown_attempt(self):
        case = self.repo / 'data/l2_pipeline/autopilot/D1/a1'
        (case / 'actions').mkdir(parents=True)
        self.assertEqual(tele.gate_exit(self.root, dict(design_id='D1', attempt_id='a1'))[0], 'in_flight')
        (case / 'PARKED.json').write_text(json.dumps(dict(action='S3', reason='S3 Disposition.STOPPED: Registered support insufficient')))
        self.assertEqual(tele.gate_exit(self.root, dict(design_id='D1', attempt_id='a1')),
                         ('PARK:S3', 'S3 Disposition.STOPPED: Registered support insufficient'))
        self.assertEqual(tele.gate_exit(self.root, dict(design_id='D1', attempt_id='zz'))[0], 'unknown_attempt')
        (case / 'PARKED.json').unlink()
        (case / 'actions/register.json').write_text('{}')
        self.assertEqual(tele.gate_exit(self.root, dict(design_id='D1', attempt_id='a1'))[0], 'FINISHED')


# --- (a) telemetry, P4, P1, P2 on the real chunk fixture -----------------------------------------------------------------------

def drop_records(units, finding, urls=None, complete=False):
    def mutate(job, value):
        value['records'] = [r for r in value['records'] if r['unit'] not in units]
        value['collection_complete'] = complete
        kept = [e for e in value['research_log'] if e['unit'] not in units]
        for unit in units:
            entry = dict(unit=unit, query=unit + ' statute', finding=finding)
            if urls is not None:
                entry['urls'] = urls
            kept.append(entry)
        value['research_log'] = kept
    return mutate


class SupplyCase(ReleaseCase):
    def work(self, entry, worker, now, tokens=None, dt=600):
        """work_chunk with an optional token count and a chosen finish time (no evidence rule on the packet)."""
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        job = load_job(Path(entry['job_path']))
        lease = q.claim(self.queue, worker, entry['job_id'], now=now)
        rule = Path(lease['packet']) / 'EVIDENCE_RULE.json'
        if rule.exists():
            rule.unlink()
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text(json.dumps(golden_output(job)))
        return q.finish(self.queue, entry['job_id'], lease['token'], now=now + dt, tokens=tokens)

    def second_attempt(self, attempt='queue_test2', row_attempt='t2'):
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        pair = emit_pair(self.card, self.card['treatment']['universe'], root=self.root / ('route_' + attempt), attempt_id=attempt)
        return pair[0], dict(design_id=pair[0].design_id, attempt_id=row_attempt)

    def finish_chunks(self, parent_id, mutate_first=None, now=1_000_000):
        out = []
        for i, entry in enumerate(self.chunks(parent_id)):
            if entry.get('blocked'):
                continue
            out.append(self.work_chunk(entry, 'w%d-%d' % (i, now), now + 100 * i, mutate=mutate_first if i == 0 else None)[0])
        return out


class TelemetryTests(SupplyCase):
    def test_finish_keeps_optional_tokens_and_summary_reports_them(self):
        r = self.enqueue()
        first, second = self.chunks(r['job_id'])
        done = self.work(first, 'tele-a', 2_000_000, tokens=123456, dt=600)
        self.assertTrue(done.endswith(first['job_id']))
        self.assertEqual(json.loads((Path(done) / 'TELEMETRY.json').read_text())['tokens'], 123456)
        done2 = self.work(second, 'tele-b', 2_000_700, dt=200)   # no --tokens: never blocked, no file
        self.assertFalse((Path(done2) / 'TELEMETRY.json').exists())
        rows = {r['job_id']: r for r in tele.rows(self.queue, ('done',))}
        a, b = rows[first['job_id']], rows[second['job_id']]
        self.assertEqual((a['tokens'], b['tokens']), (123456, ''))
        self.assertEqual(a['minutes'], 10.0)
        self.assertEqual(a['verdict'], 'validated')
        self.assertEqual(a['worker'], 'tele-a')
        self.assertEqual(a['design_id'], self.row['design_id'])
        self.assertEqual((a['role'], a['chunk'], a['chunk_total']), ('A', 1, 2))
        self.assertTrue(a['finished_at'].endswith('Z'))

    def test_bad_token_values_never_block_a_finish(self):
        r = self.enqueue()
        first = self.chunks(r['job_id'])[0]
        done = self.work(first, 'tele-c', 3_000_000, tokens=-5, dt=100)
        self.assertEqual(Path(done).parent.name, 'done')
        self.assertFalse((Path(done) / 'TELEMETRY.json').exists())

    def test_csv_jsonl_and_by_day_summary(self):
        r = self.enqueue()
        self.finish_chunks(r['job_id'], now=4_000_000)
        out = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(out, ignore_errors=True))
        with patch('sys.stdout'):
            tele.main(['--root', str(self.queue), '--csv', str(out / 't.csv'), '--jsonl', str(out / 't.jsonl'), '--by', 'day'])
        with (out / 't.csv').open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0].keys()), tele.FIELDS)
        self.assertGreaterEqual(len(rows), 3)   # two chunks + the inert parent
        lines = (out / 't.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), len(rows))
        summary = tele.summarize(tele.rows(self.queue), 'day')
        self.assertTrue(summary)


class UnattemptedTests(SupplyCase):
    def claim_and_try(self, entry, worker, now, mutate):
        job = load_job(Path(entry['job_path']))
        lease = q.claim(self.queue, worker, entry['job_id'], now=now)
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        value = golden_output(job)
        mutate(job, value)
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text(json.dumps(value))
        return q.finish(self.queue, entry['job_id'], lease['token'], now=now + 5)

    def test_pure_detector(self):
        binding = dict(chunk=dict(units=['U0', 'U1', 'U2']))
        out = Path(tempfile.mkdtemp()) / 'o.json'
        self.addCleanup(lambda: __import__('shutil').rmtree(out.parent, ignore_errors=True))
        value = dict(collection_complete=False, records=[dict(unit='U0')], research_log=[
            dict(unit='U1', query='x', finding='Not researched; left out.'),
            dict(unit='U2', query='x', finding='HTTP 403 on the statute page', urls=['https://example.test/a'])])
        out.write_text(json.dumps(value))
        self.assertEqual(q.unattempted_units(binding, out), ['U1'])
        value['research_log'][0]['urls'] = ['https://example.test/x']
        out.write_text(json.dumps(value))
        self.assertEqual(q.unattempted_units(binding, out), [])   # a named URL is an attempt, even a failed one
        value['research_log'] = [e for e in value['research_log'] if e['unit'] != 'U1']
        out.write_text(json.dumps(value))
        self.assertEqual(q.unattempted_units(binding, out), ['U1'])   # no log entry at all
        value['collection_complete'] = True
        out.write_text(json.dumps(value))
        self.assertEqual(q.unattempted_units(binding, out), [])
        self.assertEqual(q.unattempted_units(dict(), out), [])   # not a chunk

    def test_skipped_units_hand_the_packet_back_twice_then_it_finishes_partial(self):
        r = self.enqueue()
        entry = self.chunks(r['job_id'])[0]
        skip = drop_records({'U0', 'U1'}, 'Not researched; left out.')
        first = self.claim_and_try(entry, 'p4-a', 5_000_000, skip)
        self.assertEqual(first['status'], 'released_unattempted')
        self.assertEqual(first['units'], ['U0', 'U1'])
        self.assertTrue((self.queue / 'pending' / entry['job_id']).is_dir())
        self.assertEqual(json.loads((self.queue / 'pending' / entry['job_id'] / 'STATE.json').read_text())['releases'], 1)
        second = self.claim_and_try(entry, 'p4-b', 5_001_000, skip)
        self.assertEqual(second['status'], 'released_unattempted')
        third = self.claim_and_try(entry, 'p4-c', 5_002_000, skip)   # budget spent: the truthful partial is accepted, never the hub
        self.assertTrue(str(third).endswith(entry['job_id']))
        self.assertTrue((self.queue / 'done' / entry['job_id']).is_dir())

    def test_a_real_failed_attempt_finishes_without_release(self):
        r = self.enqueue()
        entry = self.chunks(r['job_id'])[0]
        got = self.claim_and_try(entry, 'p4-d', 6_000_000,
                                 drop_records({'U0'}, 'HTTP 403 Forbidden on the official page', urls=['https://example.test/a']))
        self.assertTrue(str(got).endswith(entry['job_id']))


class SharedPackTests(SupplyCase):
    def delivery(self, url='https://law.justia.test/codes/x', units=('U0',), body=b'<html><body>Sec. 1. effective 2001.</body></html>'):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(d, ignore_errors=True))
        (d / 'p.html').write_bytes(body)
        (d / 'HANDOFF.json').write_text(json.dumps(dict(schema='l2_browser_evidence_handoff_v1', items=[dict(
            file='p.html', url=url, final_url=url, fetched_at_utc='2026-10-22T08:00:00Z', bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(), browser='Test Browser 1', content_type='text/html', units=list(units))])))
        return d

    def test_add_plan_apply_lookup_and_idempotence(self):
        d = self.delivery()
        plan = shared_pack.add_delivery(self.queue, d, apply=False)
        self.assertEqual(plan[0]['action'], 'would_add')
        self.assertIsNone(shared_pack.lookup(self.queue, 'https://law.justia.test/codes/x'))
        done = shared_pack.add_delivery(self.queue, d, apply=True)
        self.assertEqual(done[0]['action'], 'add')
        hit = shared_pack.lookup(self.queue, 'http://www.law.justia.test/codes/x/')   # canonical match: scheme, www, trailing slash
        self.assertEqual(hit['units'], ['U0'])
        self.assertTrue(shared_pack.covers(self.queue, 'U0'))
        self.assertFalse(shared_pack.covers(self.queue, 'U7'))
        again = shared_pack.add_delivery(self.queue, d, apply=True)
        self.assertEqual(again[0]['action'], 'already_in_pack')
        self.assertEqual(len(shared_pack.index(self.queue)['entries']), 1)
        self.assertEqual(oct((self.queue / 'SHARED_PACK' / hit['file']).stat().st_mode & 0o777), '0o444')

    def test_tampered_delivery_is_refused(self):
        d = self.delivery()
        (d / 'p.html').write_bytes(b'<html>other</html>')
        from scripts.l2_pipeline.agents import browser_evidence as be
        with self.assertRaises(be.ImportRefused):
            shared_pack.add_delivery(self.queue, d, apply=True)
        self.assertEqual(shared_pack.index(self.queue)['entries'], [])

    def test_fetch_evidence_serves_the_pack_without_network(self):
        from scripts.l2_pipeline.agents import evidence_fetch as ef
        self.enqueue()
        shared_pack.add_delivery(self.queue, self.delivery(), apply=True)
        entry = self.chunks(next(iter((self.queue / 'chunked').iterdir())).name)[0]
        lease = q.claim(self.queue, 'pack-w', entry['job_id'], now=7_000_000)

        def no_network(*a, **k):
            raise AssertionError('network used')
        got = ef.fetch_evidence(self.queue, entry['job_id'], lease['token'], 'https://law.justia.test/codes/x', get=no_network, now=7_000_010)
        self.assertEqual(got['via'], 'browser')
        self.assertTrue(got['shared_pack'])
        self.assertTrue(got['quote_check_available'])
        log = json.loads((self.queue / 'in_progress' / entry['job_id'] / 'EVIDENCE_LOG.json').read_text())
        self.assertEqual(len(log['entries']), 1)
        self.assertTrue(Path(log['entries'][0]['archive_abs_path']).is_file())
        again = ef.fetch_evidence(self.queue, entry['job_id'], lease['token'], 'https://law.justia.test/codes/x', get=no_network, now=7_000_020)
        self.assertEqual(again['sha256'], got['sha256'])   # the staged entry answers; no second log line
        log = json.loads((self.queue / 'in_progress' / entry['job_id'] / 'EVIDENCE_LOG.json').read_text())
        self.assertEqual(len(log['entries']), 1)

    def test_wanted_lists_blocked_urls_not_yet_in_the_pack(self):
        r = self.enqueue()
        self.finish_chunks(r['job_id'], mutate_first=drop_records({'U0', 'U1'}, '403 Forbidden from the host', urls=['https://law.justia.test/a', 'https://other.test/b']),
                           now=8_000_000)
        w = shared_pack.wanted(self.queue)
        self.assertEqual({(x['unit'], x['host']) for x in w['rows']}, {('U0', 'law.justia.test'), ('U0', 'other.test'), ('U1', 'law.justia.test'), ('U1', 'other.test')})
        shared_pack.add_delivery(self.queue, self.delivery(url='https://law.justia.test/a', units=('U0', 'U1')), apply=True)
        w2 = shared_pack.wanted(self.queue)
        self.assertEqual({x['host'] for x in w2['rows']}, {'other.test'})


class PriorityWorklistTests(unittest.TestCase):
    def test_empty_page_counts_as_a_blocked_host(self):
        self.assertEqual(ub.classify('statutes.capitol.texas.gov returned an empty page'), 'host_blocked')
        self.assertEqual(ub.classify('blank page from the statute site'), 'host_blocked')

    def test_units_left_out_of_a_curated_audit_packet_come_from_its_own_notes(self):
        value = dict(records=[dict(unit='FL')], research_log=[dict(unit='FL'), dict(unit='TX'), dict(unit='NM')],
                     incomplete_units={'PA': 'no readable text'})
        self.assertEqual(shared_pack._units_left_out(None, value), ['NM', 'PA', 'TX'])
        self.assertEqual(shared_pack._units_left_out(dict(units=['FL', 'GA']), value), ['GA'])

    def test_priority_set_is_the_hub_list(self):
        self.assertEqual(set(shared_pack.PRIORITY_UNITS), {'TX', 'NM', 'FL', 'MA', 'PA', 'GA', 'LA', 'MD'})
        self.assertIn('statutes.capitol.texas.gov', shared_pack.PRIORITY_HOSTS)


class BlockedUnitTests(SupplyCase):
    ALL_FIRST = {'U%d' % i for i in range(9)}

    def test_classifier(self):
        self.assertEqual(ub.classify('HTTP 403 from justia'), 'host_blocked')
        self.assertEqual(ub.classify('Forbidden; Wayback copy unavailable'), 'host_blocked')
        self.assertEqual(ub.classify('JS shell, no text layer'), 'host_blocked')
        self.assertIsNone(ub.classify('read timed out; TLS alert; 429 rate limit'))
        self.assertIsNone(ub.classify('Not researched; left out.'))
        self.assertIsNone(ub.classify('No dated official source found'))

    def test_full_chunk_blocked_in_an_earlier_attempt_is_not_enqueued_again(self):
        r = self.enqueue()
        self.finish_chunks(r['job_id'], mutate_first=drop_records(self.ALL_FIRST, 'HTTP 403 Forbidden', urls=['https://x.test/a']), now=9_000_000)
        past = ub.history(self.queue, self.row['design_id'])
        self.assertEqual({u for u, v in past.items() if v['outcome'] == 'blocked'}, self.ALL_FIRST)
        self.assertEqual({v['outcome'] for u, v in past.items() if u not in self.ALL_FIRST}, {'ok'})
        # the same attempt is never blocked by its own packets
        self.assertEqual(ub.blocked_units(self.queue, self.row['design_id'], sorted(self.ALL_FIRST), self.row['attempt_id']), {})
        job2, row2 = self.second_attempt()
        res = q.enqueue(job2.job_path, row2, self.queue)
        chunks = self.chunks(res['job_id'])
        self.assertEqual(sorted(chunks[0]['blocked']), sorted(self.ALL_FIRST))
        self.assertNotIn('blocked', chunks[1])
        self.assertIsNone(q.locate(self.queue, chunks[0]['job_id']))
        self.assertIsNotNone(q.locate(self.queue, chunks[1]['job_id']))
        self.assertEqual(q.state(job2.job_path, self.queue.parent.parent.parent), 'running')

    def test_merge_without_the_blocked_chunk_is_incomplete_and_lists_the_blocked_units(self):
        r = self.enqueue()
        self.finish_chunks(r['job_id'], mutate_first=drop_records(self.ALL_FIRST, '403 Forbidden', urls=['https://x.test/a']), now=9_100_000)
        job2, row2 = self.second_attempt()
        res = q.enqueue(job2.job_path, row2, self.queue)
        self.finish_chunks(res['job_id'], now=9_200_000)
        self.assertEqual(q.state(job2.job_path, self.queue.parent.parent.parent), 'done')
        merged = json.loads(Path(job2.output_path).read_text())
        self.assertFalse(merged['collection_complete'])
        self.assertEqual(sorted(merged['chunk_merge']['blocked']['1']), sorted(self.ALL_FIRST))
        self.assertEqual(len(merged['records']), 8)
        self.assertTrue((self.queue / 'done' / res['job_id']).is_dir())

    def test_every_chunk_blocked_parks_without_a_packet(self):
        r = self.enqueue()
        for i, entry in enumerate(self.chunks(r['job_id'])):
            units = set(entry['units'])
            self.work_chunk(entry, 'b%d' % i, 9_300_000 + 100 * i, mutate=drop_records(units, '403 Forbidden', urls=['https://x.test/a']))
        job2, row2 = self.second_attempt()
        res = q.enqueue(job2.job_path, row2, self.queue)
        self.assertTrue(all(c.get('blocked') for c in self.chunks(res['job_id'])))
        self.assertEqual(list((self.queue / 'pending').iterdir()), [])
        from scripts.l2_pipeline.c42 import Park
        with self.assertRaises(Park) as caught:
            q.state(job2.job_path, self.queue.parent.parent.parent)
        self.assertTrue(str(caught.exception).startswith('INSUFFICIENT_WORK: every planned history unit already failed on a blocked host'))
        self.assertEqual(caught.exception.metrics['kind'], 'HISTORY_UNITS_BLOCKED')

    def test_a_pack_page_or_an_allow_entry_or_a_later_record_lifts_the_block(self):
        r = self.enqueue()
        self.finish_chunks(r['job_id'], mutate_first=drop_records(self.ALL_FIRST, '403 Forbidden', urls=['https://x.test/a']), now=9_400_000)
        design = self.row['design_id']
        units = sorted(self.ALL_FIRST)
        self.assertEqual(len(ub.blocked_units(self.queue, design, units, 'next')), 9)
        # 1. allow list
        (self.queue / ub.ALLOW_FILE).write_text(json.dumps({design: ['U0']}))
        self.assertEqual(len(ub.blocked_units(self.queue, design, units, 'next')), 8)
        (self.queue / ub.ALLOW_FILE).write_text(json.dumps({design: '*'}))
        self.assertEqual(ub.blocked_units(self.queue, design, units, 'next'), {})
        (self.queue / ub.ALLOW_FILE).unlink()
        # 2. a pack page tagged with a unit is a new access route for that unit only
        SharedPackTests.delivery(self, units=('U3',))
        shared_pack.add_delivery(self.queue, SharedPackTests.delivery(self, url='https://law.justia.test/q', units=('U3',)), apply=True)
        self.assertNotIn('U3', ub.blocked_units(self.queue, design, units, 'next'))
        self.assertEqual(len(ub.blocked_units(self.queue, design, units, 'next')), 8)

    def test_mixed_chunk_still_runs(self):
        r = self.enqueue()
        self.finish_chunks(r['job_id'], mutate_first=drop_records({'U0', 'U1'}, '403 Forbidden', urls=['https://x.test/a']), now=9_500_000)
        job2, row2 = self.second_attempt()
        res = q.enqueue(job2.job_path, row2, self.queue)
        self.assertTrue(all('blocked' not in c for c in self.chunks(res['job_id'])))


# --- park text ----------------------------------------------------------------------------------------------------------------

class ParkTextTests(unittest.TestCase):
    def test_lease_expiry_and_worker_releases_are_named_but_keep_the_pinned_prefix(self):
        lease = release_policy.text_for('Sonnet lease expired twice', 'lease_expired')
        self.assertTrue(lease.startswith(release_policy.PREFIX))
        self.assertIn('LEASE EXPIRED', lease)
        self.assertIn('Sonnet lease expired twice', lease)
        rel = release_policy.text_for('Sonnet packet released by workers 3 times', 'released_repeatedly')
        self.assertIn('RELEASED BY WORKERS', rel)
        structural = release_policy.text_for('Sonnet packet released as structurally unfinishable (too_large): x', 'structural_release')
        self.assertTrue(structural.startswith(release_policy.PREFIX + 'Sonnet packet released as structurally'))
        self.assertEqual(release_policy.text_for('QUEUE_PARK: already', 'lease_expired'), 'QUEUE_PARK: already')
        self.assertEqual(release_policy.text_for('x'), release_policy.PREFIX + 'x')

    def test_parked_reason_still_classifies_as_packet_lost(self):
        from scripts.l2_pipeline import rules
        path = Path(__file__).resolve().parents[3] / 'data/l2_pipeline/rules/RULES_R3.json'
        if not path.is_file():
            self.skipTest('no rules file')
        value = rules.load(path)
        text = release_policy.text_for('Sonnet lease expired twice', 'lease_expired') + '; HUB_QUEUE required'
        self.assertEqual(rules.classify(value, 'dispatch', text)['id'], 'PACKET_LOST')
        real = rules.classify(value, 'history', 'INSUFFICIENT_WORK: official history is an incomplete research checkpoint')
        self.assertEqual(real['id'], 'HISTORY_INSUFFICIENT')


if __name__ == '__main__':
    unittest.main()
