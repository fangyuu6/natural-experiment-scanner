"""One host scheduler for durable, independently stopped L2 design chains.

Progress is reconstructed from receipts and immutable action checkpoints. Agent
processes survive a scheduler restart. An ambiguous launch/execution consumes
its slot conservatively; it is never retried. No scientific declaration is fixed
or invented here. Only standing-authorized existing acceptance functions run.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from .storage import encoded, ensure_once, public_input, sha
from .c42 import Park
from .agents.replacement import ContractRejection, RULE as REPLACEMENT_RULE, ORPHAN_RULE

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO/'data/l2_pipeline/registry'
RULES = {
    'accept': 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §1 (existing V6.2/V3/V7/V8b/V9/RI rules)',
    'prep': 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §2 (draft PASS, MUST=0)',
    'final': 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §3 (sealed PASS, MUST=0, snapshot, DRY-LOAD)',
    'execute': 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §4 (single snapshot run, no retry)',
    'stop': 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §1/4/5 (NEEDS_RULING, failure or MUST)',
    'record': 'HUB_STANDING_AUTHORIZATION_AUTOPILOT.md §6 (durable decisions)',
    'budget': 'C57 addendum §6 (history/fresh audit Codex; purpose/forecast/reviews Sonnet; existing dispatches retained)',
    'exclude': 'HUB_RULING_C42_UNKNOWN_STATES.md §1/2 (exclude both arms; unchanged gates)',
    'replacement': REPLACEMENT_RULE,
    'orphan': ORPHAN_RULE,
    'c54': 'HUB_RULING_C54_CLOSURES.md §1/2/3/4',
    'redesign': 'HUB_RULING_20261017_REDESIGN.md §1/§2 (rules file, screen before dispatch, purpose label, park table)',
}
CHAIN = ('history','reconcile','audit','purpose','purpose-direction','S0','S1','S2','S3','S4','ri','forecasts',
         'lock','draft-dry-load','draft','prep','approve-prep','calibrate','seal','dry-load','sealed',
         'approve-final','execute','S7','register')


def read(path, default=None):
    path = public_input(Path(path))
    return json.loads(path.read_text()) if path.exists() else default


def durable(path, repo=REPO):
    path = public_input(Path(path))
    if not path.is_relative_to(repo/'data/l2_pipeline'):
        raise ValueError('Autopilot durable output must be in repo data/l2_pipeline')
    return path


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with tmp.open('wb') as stream:
        stream.write(encoded(value)+b'\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp,path)
    descriptor = os.open(path.parent,os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


@contextmanager
def scheduler_lock(registry):
    registry.mkdir(parents=True,exist_ok=True)
    with (registry/'AUTOPILOT.lock').open('a') as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another autopilot scheduler/ingest owns the global lock') from None
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)


# Host/volume conditions that say nothing about a design (LOOP_EPERM 2026-10-16:
# one EPERM while writing a STOP.json crashed the whole loop and STOPped nothing useful).
ENVIRONMENT_ERRNOS = frozenset({errno.EPERM, errno.EACCES, errno.EROFS, errno.ENOSPC, errno.EDQUOT,
                                errno.EIO, errno.ENXIO, errno.ENODEV, errno.ETIMEDOUT})
MAX_ENVIRONMENT_TICKS = 5
ENVIRONMENT_RETRY_SECONDS = 60.0


def environmental(exc):
    return isinstance(exc, OSError) and exc.errno in ENVIRONMENT_ERRNOS


def utc():
    return datetime.now(timezone.utc).isoformat()


class Backend:
    """The real stage adapter. Tests inject agent completion, never model calls."""
    def __init__(self, repo=REPO):
        self.repo = repo
        self.children = {}
        self.async_local = True

    def ctx(self,row,strict=False):
        from .cards import load_card
        from .contracts import DesignCard, StageContext
        path = public_input(Path(row['card']))
        if sha(path)!=row['card_sha256']: raise ValueError('NEEDS_RULING: intake card bytes changed')
        # Legacy intake cards can finish legal research. They cannot pass S0 or
        # reach numerical preparation until a schema-valid registration exists.
        card = load_card(path) if strict else DesignCard.from_mapping(read(path),path)
        from . import rules
        rules.of_payload(card.payload,self.repo)  # a card that pins rules must match the rules file bytes
        from .run2_preparation import effective_context
        from .c42 import effective_context as c42_context
        from .c48_audit import effective_context as c48_context
        from .hub_adjudication import effective_context as adjudicated
        return c48_context(adjudicated(c42_context(effective_context(StageContext(card,durable(row['work_dir'],self.repo),self.repo)))))

    def root(self,row):
        try:return self.ctx(row).design_dir
        except Park:
            # An overlay's engineering PARK (V3.1) must not hide the directory from the tools that reopen the design (requeue, release_recovery).
            card=read(public_input(Path(row['card'])))
            return durable(row['work_dir'],self.repo)/card['design_id']/card['version']

    def job_state(self,path):
        meta = read(Path(path).with_suffix('.job.json'))
        from .agents.sonnet_queue import state as external_state
        queued=external_state(path,self.repo)
        if queued is not None:return queued
        out = public_input(Path(meta['output_dir']))
        result = read(out/'DISPATCH_RESULT.json')
        if result:
            if result.get('status')!='COMPLETED' or result.get('exit_code')!=0:
                if not public_input(Path(meta['output_path'])).exists():
                    raise ContractRejection(path,'DEAD_NO_RESULT','Agent dead without result: '+str(path))
                raise ValueError('Agent FAILED_NO_RETRY: '+str(path)+': '+str(result.get('error',result)))
            return 'done'
        # Output can be written while the worker still runs. Wait for its host
        # completion; old manual submissions without a dispatch are collectible.
        if (out/'DISPATCH_ONCE.json').exists():
            from .agents.liveness import status
            state = status(self.repo,out/'DISPATCH_ONCE.json',reconcile=True)
            if state=='failed':
                if not public_input(Path(meta['output_path'])).exists():
                    raise ContractRejection(path,'DEAD_NO_RESULT','Agent dead without result: '+str(path))
                raise ValueError('Agent FAILED_NO_RETRY: reconciled dead/expired worker: '+str(path))
            return 'running'
        if public_input(Path(meta['output_path'])).exists(): return 'done'
        if (out/'dispatch.log').exists() or public_input(Path(meta['result_path'])).exists():
            raise ValueError('Ambiguous agent output without completion; no retry: '+str(path))
        return 'pending'

    def collect(self,path):
        from .agents import collect_job
        from .agents.jobs import load_job
        from .agents.replacement import rejection_kind
        job = load_job(Path(path))  # Changed code/inputs/packets are not CONTRACT failures.
        value = None
        try:
            output = public_input(Path(job.output_path))
            value = read(output)
            if value is None: raise ContractRejection(path,'SCHEMA_FORMAT','Completed agent has no submission')
            receipt = collect_job(Path(path))
        except ContractRejection: raise
        except Exception as exc:
            kind = rejection_kind(exc,value)
            if kind: raise ContractRejection(path,kind,str(exc)) from exc
            if job.role=='purpose_verifier':
                from .agents.purpose import unresolved_reason
                reason = unresolved_reason(value,exc)
                if reason:  # an honest 'no evidence found' is a documented PARK, reopenable on new evidence, never a STOP
                    raise Park('S0 purpose unresolved: '+reason,dict(kind='PURPOSE_UNRESOLVED',collector_error=str(exc),
                        job=str(path),outcome_values_read=0)) from exc
            raise
        if receipt.get('validated') is not True or receipt.get('mechanism_issues'):
            raise ValueError('NEEDS_RULING: agent collection rejected: '+json.dumps(receipt,sort_keys=True))
        return receipt

    def global_running(self):
        # All existing dispatchers and legacy jobs count, including jobs outside
        # ACTIVE_DESIGNS. Unknown legacy reservations conservatively occupy slots.
        from .agents.liveness import running
        return running(self.repo)

    def launch(self,path,claim):
        from .agents import dispatch
        from .agents.contract_tests import check_job_types
        from .agents.jobs import load_job
        job = load_job(Path(path)); check_job_types([job])
        argv = [sys.executable,'-B','-m','scripts.l2_pipeline.hub','dispatch',job.stage,
                '--job',str(path),'--max-concurrency','1','--hub-outside-sandbox','--scheduled-worker']
        # The single scheduler owns the budget; each child uses the existing hub
        # dispatcher, its exclusive DISPATCH_ONCE and zero-retry collector path.
        log = claim.with_suffix('.log')
        with log.open('xb') as stream:
            process = subprocess.Popen(argv,cwd=self.repo,stdin=subprocess.DEVNULL,
                                       stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        self.children[process.pid]=process
        return dict(pid=process.pid,started_at=utc(),argv=argv)

    def replacement_job(self,path,slot,attempt_id):
        from .agents.replacement import emit
        return emit(path,slot,attempt_id).job_path

    def launch_liveness(self,launched):
        from .agents.liveness import process_table
        if not launched.get('argv'):
            # Injected test backends and old local receipts retain PID behavior.
            return 'alive' if self.alive(launched['pid']) else 'dead'
        table = process_table()
        if table is None: return 'ambiguous'
        process = table.get(launched['pid'])
        if not process: return 'dead'
        argv = launched['argv']
        if '--job' not in argv: return 'ambiguous'
        job = argv[argv.index('--job')+1]
        actual = process['argv']
        if '--job' not in actual or actual[actual.index('--job')+1:actual.index('--job')+2]!=[job]:
            return 'dead'
        return 'alive'

    def alive(self,pid):
        if pid in self.children: return self.children[pid].poll() is None
        try: os.kill(pid,0)
        except ProcessLookupError: return False
        except PermissionError: return True
        return True

    def launch_local(self,request):
        log=request.parent/'worker.log'
        with log.open('xb') as stream:
            process=subprocess.Popen([sys.executable,'-B','-m','scripts.l2_pipeline.autopilot',
                'local-worker',str(request),'--hub-outside-sandbox'],cwd=self.repo,
                stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        self.children[process.pid]=process
        return dict(pid=process.pid,started_at=utc())

    def jobs(self,row,action,autopilot):
        root = self.root(row)
        if action=='history':
            if row.get('history_jobs'): return row['history_jobs']
            from .intake.history_routing import route_official_history
            routing = row['routing']
            receipt = route_official_history(row['design_id'],row['attempt_id'],
                intake_root=Path(routing['intake_root']),root=autopilot.case_dir(row)/'route',
                audit_seed=routing['audit_seed'],prior_jobs=routing.get('prior_jobs', []))
            return receipt['job_paths']
        if action=='audit':
            return [str(autopilot.case_dir(row)/'audit_jobs/S1'/row['design_id']/'auditor.md')]
        if action=='purpose':
            existing = root/'agents/purpose_verifier/COLLECTION.json'
            if existing.exists(): return [read(existing)['job_path']]
            from .agents.purpose import emit_purpose_verification
            return [emit_purpose_verification(self.ctx(row),autopilot.case_dir(row)/'purpose_jobs').job_path]
        from .agents import emit_stage_jobs
        ctx = self.ctx(row,strict=True)
        if action=='forecasts': stage='S5'; draft=False
        else: stage='S6'; draft=action=='draft'
        roles=None
        if action=='forecasts':
            from . import rules
            if rules.at_least(rules.of_payload(ctx.card.payload,self.repo),2): roles=['forecast_bundle']   # REDESIGN R2: one packet
        return [j.job_path for j in emit_stage_jobs(ctx,stage,roles=roles,
            jobs_root=autopilot.case_dir(row)/(action+'_jobs'),allow_draft=draft,
            attempt_id=row['attempt_id']+'_'+action)]

    def perform(self,row,action,autopilot):
        from . import registration
        from .cli import run_stage, preflight
        from .contracts import Disposition
        from .agents.purpose import accept_purpose_verification
        if action=='purpose-direction':
            from .agents.purpose_direction import action as direction_action
            return direction_action(autopilot,row)
        ctx = self.ctx(row,strict=action not in {'reconcile','purpose'})
        root = ctx.design_dir
        if action=='reconcile':
            from .autopilot_history import prepare
            return prepare(ctx,autopilot.done(row,'history')['jobs'],row['fresh_policy'],autopilot.case_dir(row)/'audit_jobs')
        if action=='purpose':
            if row.get('purpose_ruling'):
                pointer=row['purpose_ruling'];value=read(pointer['path'])
                if (sha(pointer['path'])!=pointer['sha256'] or value.get('decision')!='accept'
                        or value.get('design_id')!=ctx.card.design_id or value.get('card_sha256')!=sha(ctx.card.path)
                        or value.get('purpose_rule') not in {'V6.2','V6.3'}):
                    raise ValueError('NEEDS_RULING: prior V6.2 acceptance changed')
                return value
            existing = root/'PURPOSE_HUB_RULING_V62.json'
            if existing.exists():
                value = read(existing)
                if value.get('decision')!='accept' or value.get('card_sha256')!=sha(ctx.card.path):
                    raise ValueError('NEEDS_RULING: purpose ruling not accepted/bound')
                return value
            purpose = ctx.read('PURPOSE_VERIFICATION.json')
            if purpose.get('status')=='UNRESOLVED':
                raise Park('S0 purpose unresolved: '+' '.join(str(purpose.get('unresolved_reason','')).split())[:500],
                           dict(kind='PURPOSE_UNRESOLVED',outcome_values_read=0))
            from .population_resolution import precheck
            resolved=precheck(autopilot,row,purpose)
            if resolved:return resolved
            first = ctx.card.payload.get('first_order',{})
            if (purpose.get('status')!='VERIFIED' or purpose['target_outcome']!=first.get('target_outcome')
                    or purpose['target_population']!=first.get('target_population',ctx.card.payload['population'].get('target',ctx.card.payload['population'].get('description')))):
                raise ValueError('NEEDS_RULING: V6.2 target/population requires new interpretation')
            return accept_purpose_verification(ctx,'accept','Standing authorization; exact registered target/population and validated V6.2 citations')
        if action in {'S0','S1','S2','S3','S4','execute','S7'}:
            if action=='S0':
                from .population_resolution import precheck
                resolved=precheck(autopilot,row)
                if resolved:return resolved
            if action=='S2':
                from .c50 import window_precheck
                revised=window_precheck(autopilot,row,scheduler_owned=True)
                if revised:return revised
            stage = 'S6' if action=='execute' else action
            prior = sorted((root/'receipts'/stage).glob('*.json'))
            if prior:
                result = read(prior[-1])
                if result['card_sha256']!=sha(ctx.card.path): raise ValueError('Stage receipt card changed')
                result = result['result']
                for a in result['artifacts']:
                    if sha(a['path'])!=a['sha256']: raise ValueError('Stage receipt artifact changed')
            elif stage=='S1':
                from .autopilot_history import accept,accept_v8b
                if ctx.card.payload.get('transition_year_drop') is not True:
                    raise ValueError('NEEDS_RULING: V9/C11 operation-year-drop clock is not declared on this card')
                from . import curated_route
                if curated_route.declared(row):
                    preflight(ctx,'S1')
                    result = curated_route.accept(ctx).as_dict()
                else:
                  if row['route']=='V9':
                    from .c48_audit import prepare_s1
                    ctx=prepare_s1(ctx,autopilot.done(row,'audit')['jobs'][0])
                  preflight(ctx,'S1')
                  if row['route']=='V8b':autopilot.v8b_action(row,'reconcile')
                  result = (accept if row['route']=='V9' else accept_v8b)(ctx,autopilot.done(row,'audit')['jobs'][0]).as_dict()
                ensure_once(root/'receipts/S1/autopilot.json',dict(card_sha256=sha(ctx.card.path),result=result))
            else:
                from . import snapshots
                frozen = snapshots.manifest_for(root) if stage=='S7' else None
                if frozen:
                    result=snapshots.call(root,frozen,'run_stage',dict(context=snapshots.context_payload(ctx),stage='S7'))
                else: result = run_stage(ctx,stage).as_dict()
            if result['disposition'] not in {Disposition.READY, 'ready'}:
                if stage=='S6' and (root/'TERMINAL.json').exists():
                    if ctx.read('TERMINAL.json')['status']=='STOPPED': return result
                if result['disposition'] in {Disposition.PARKED, 'parked'} or stage=='S3':
                    metrics=dict(result.get('metrics',{}))
                    if stage=='S3' and (root/'SUPPORT.json').exists(): metrics['support']=ctx.read('SUPPORT.json')
                    raise Park(stage+' '+str(result['disposition'])+': '+result['reason'], metrics)
                raise ValueError(stage+' '+str(result['disposition'])+': '+result['reason'])
            if stage=='S3' and (root/'C42_PREPARATION.json').exists():
                from .c42 import post_support_gate
                result=dict(result,c42_post_support=post_support_gate(ctx.read('SUPPORT.json'),ctx.card.payload['analysis']))
            return result
        if action=='ri':
            from .hub import precheck_s5
            value = precheck_s5(ctx)
            if value.get('passed') is not True: raise Park('STOPPED_PRE_RUN: RI199/RI999 feasibility below 90%', value)
            return value
        signer = registration.hub_signer(ctx) or 'hub autopilot (standing authorization 2026-10-10)'
        args = SimpleNamespace(signer=signer,signed_at=utc(),review_attempt=autopilot.draft_attempt(row) if action=='prep' else row['attempt_id']+'_draft',
                               test_report=autopilot.test_report)
        if action=='draft-dry-load':
            from .c83_l3 import retain as retain_c83
            retain_c83(ctx)
            from .dry_load import draft
            return draft(ctx)
        if action=='lock': return registration.lock(ctx)
        if action=='prep':
            if (root/'FREEZE_PREP.json').exists():
                from .freeze import verify
                value=verify(root,'prep',signer,require_approval=False)
                if not value['ok']: raise ValueError('Existing preparatory snapshot failed: '+str(value['issues']))
                return value
            if args.test_report is None:
                from .freeze import code_inventory
                digest = hashlib.sha256(encoded(code_inventory())).hexdigest()
                args.test_report = autopilot.registry.parent/'autopilot/checks'/('TESTS_'+digest+'.json')
                if not args.test_report.exists(): registration.check_tests(args.test_report,no_archival_fits=True)
            return registration.prep(ctx,args,[ctx.card.design_id])
        if action=='approve-prep':
            if (root/'APPROVAL_PREP.json').exists():
                from .freeze import require_verified
                return require_verified(root,'prep',signer)
            return registration.approve(ctx,args,'prep')
        if action=='calibrate':
            value = registration.calibration(ctx)
            ri = ctx.read('RI_FEASIBILITY.json')['feasibility']
            if ri.get('support_pass') is not True: raise ValueError('RI calibration feasibility below 90%')
            return value
        if action=='seal': return dict(results=registration.seal([ctx]))
        if action=='dry-load': return registration.dry_load(ctx)
        if action=='approve-final':
            if (root/'APPROVAL_FINAL.json').exists():
                from .freeze import require_verified
                return require_verified(root,'final',signer)
            return registration.approve(ctx,args,'final')
        if action=='register': return autopilot.register_terminal(row,root)
        raise ValueError('Unknown autopilot action: '+action)


class Autopilot:
    def __init__(self,registry=REGISTRY,backend=None,budget=6,test_report=None,local_budget=2,agent_backends="mixed",role_backends=None):
        self.backend = backend or Backend()
        self.repo = self.backend.repo
        self.registry = durable(registry,self.repo)
        if type(budget) is not int or budget<1: raise ValueError('Worker budget must be a positive integer')
        self.budget = budget
        if type(local_budget) is not int or local_budget<1:raise ValueError('Local worker budget must be positive')
        self.local_budget=local_budget
        self.test_report = public_input(Path(test_report)) if test_report else None
        if agent_backends not in {"mixed","codex","sonnet_queue"}:raise ValueError("Unknown agent backend")
        self.agent_backends=agent_backends
        self.role_backends=role_backends or {}
        from .agents.sonnet_queue import route
        for role in self.role_backends:route("validation",role,self.role_backends)
        self.pending = []
        self._ledger_states = None

    def ledger(self,row,action,decision,rule,**details):
        if 'attempt_id' in details: details['replacement_attempt_id']=details.pop('attempt_id')
        from . import rules
        pin = rules.pin_of_row(row) if row else None
        if pin: details.setdefault('rules_sha256',pin['sha256'])
        value = dict(time=utc(),design_id=row.get('design_id'),attempt_id=row.get('attempt_id'),action=action,decision=decision,
                     rule=RULES[rule],**details)
        path = self.registry/'AUTOPILOT_LEDGER.jsonl'
        self.registry.mkdir(parents=True,exist_ok=True)
        if path.exists():
            # A hard crash can leave only the final append incomplete. Preserve
            # that tail as evidence before truncating it; prior decisions remain.
            with path.open('rb+') as stream:
                stream.seek(0,os.SEEK_END);size=stream.tell()
                if size:
                    stream.seek(size-1)
                    if stream.read(1)!=b'\n':
                        stream.seek(0);data=stream.read();boundary=data.rfind(b'\n')+1
                        digest=hashlib.sha256(data).hexdigest()
                        ensure_once(self.registry/('AUTOPILOT_LEDGER_RECOVERY_'+digest+'.json'),dict(
                            prior_sha256=digest,tail_hex=data[boundary:].hex(),retained_bytes=boundary,
                            rule=RULES['record']))
                        stream.truncate(boundary);stream.flush();os.fsync(stream.fileno())
        from .autopilot_ledger import lane, state, states
        if self._ledger_states is None: self._ledger_states = states(path)
        key=lane(value);prior=self._ledger_states.get(key)
        # DECIDE announces entry into an action, not every poll of a waiting one.
        if decision not in {'CLEAN_STOP','LOOP_START'} and prior and (state(prior)==state(value) or
                (decision=='DECIDE' and (prior['decision'].startswith('WAIT_') or prior['decision']=='DECIDE'))):
            return False
        with path.open('ab') as stream:
            stream.write(encoded(value)+b'\n'); stream.flush(); os.fsync(stream.fileno())
        if action in {'resume','requeue'} or decision=='MECHANICAL_REQUEUE':
            self._ledger_states={k:v for k,v in self._ledger_states.items() if k[0]!=row.get('design_id')}
        self._ledger_states[key]=value
        return True

    def case_dir(self,row):
        from .agents.jobs import _safe_component
        for key in ('design_id','attempt_id'): _safe_component(row[key])
        return self.registry.parent/'autopilot'/row['design_id']/row['attempt_id']

    def checkpoint(self,row,action):
        return self.case_dir(row)/'actions'/(action+'.json')

    def done(self,row,action):
        return read(self.checkpoint(row,action))

    def stop(self,row,action,reason):
        value = dict(design_id=row['design_id'],attempt_id=row['attempt_id'],action=action,
                     status='NEEDS_RULING',reason=reason,time=utc(),rule=RULES['stop'])
        path = self.case_dir(row)/('STOP_C84.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C84.json').exists() else 'STOP_C70.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C70.json').exists() else 'STOP_C62.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C62.json').exists() else 'STOP_C59.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C59.json').exists() else 'STOP_C54.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C54.json').exists() else 'STOP_C52.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C52.json').exists() else 'STOP_C50.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C50.json').exists() else 'STOP_C49.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C49.json').exists() else 'STOP_C48.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C48.json').exists() else 'STOP_TRANSPORT.json' if (self.case_dir(row)/'STOP_SUPERSEDED_TRANSPORT.json').exists() else 'STOP_C45.json' if (self.case_dir(row)/'STOP_SUPERSEDED_C45.json').exists() else 'STOP.json')
        if not path.exists(): ensure_once(path,value)
        else: value = read(path)
        queue = read(self.registry/'HUB_QUEUE.json',dict(schema='l2_hub_queue_v1',entries=[]))
        key = (value['design_id'],value['attempt_id'])
        if not any((v['design_id'],v['attempt_id'])==key and not v.get('status','').startswith('SUPERSEDED_') for v in queue['entries']):
            queue['entries'].append(value); atomic(self.registry/'HUB_QUEUE.json',queue)
        self.ledger(row,action,'STOP', 'stop',reason=reason)

    def stop_record(self,row):
        root=self.case_dir(row)
        corrected=read(root/'STOP_SUPERSEDED_C84.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C84 stop supersession bytes changed')
            return read(root/'STOP_C84.json')
        corrected=read(root/'STOP_SUPERSEDED_C70.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C70 stop supersession bytes changed')
            return read(root/'STOP_C70.json')
        corrected=read(root/'STOP_SUPERSEDED_C62.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C62 stop supersession bytes changed')
            return read(root/'STOP_C62.json')
        corrected=read(root/'STOP_SUPERSEDED_C59.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C59 stop supersession bytes changed')
            return read(root/'STOP_C59.json')
        corrected=read(root/'STOP_SUPERSEDED_C54.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C54 stop supersession bytes changed')
            return read(root/'STOP_C54.json')
        corrected=read(root/'STOP_SUPERSEDED_C52.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C52 stop supersession bytes changed')
            return read(root/'STOP_C52.json')
        corrected=read(root/'STOP_SUPERSEDED_C50.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C50 stop supersession bytes changed')
            return read(root/'STOP_C50.json')
        corrected=read(root/'STOP_SUPERSEDED_C49.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C49 stop supersession bytes changed')
            return read(root/'STOP_C49.json')
        corrected=read(root/'STOP_SUPERSEDED_C48.json')
        if corrected:
            for pointer in [corrected['stop'],*corrected['evidence']]:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C48 stop supersession bytes changed')
            return read(root/'STOP_C48.json')
        repair=read(root/'STOP_SUPERSEDED_TRANSPORT.json')
        if repair:
            if sha(repair['stop']['path'])!=repair['stop']['sha256'] or sha(repair['repair']['path'])!=repair['repair']['sha256']:
                raise ValueError('Transport stop supersession bytes changed')
            return read(root/'STOP_TRANSPORT.json')
        if (root/'STOP_C45.json').exists(): return read(root/'STOP_C45.json')
        old=read(root/'STOP.json')
        superseded=read(root/'STOP_SUPERSEDED_C45.json')
        if old and superseded:
            if superseded['stop_sha256']!=sha(root/'STOP.json'):
                raise ValueError('Superseded stop bytes changed')
            return None
        return old

    def next_action(self,row):
        from .gate0 import next_status,eligible
        gate0_status=next_status(self,row)
        if gate0_status:return gate0_status
        if not eligible(self,row) and row.get('prescreen_enabled') and read(self.case_dir(row)/'PRESCREEN.json',{}).get('status')=='PRESCREEN_PARK': return 'PRESCREEN_PARK'
        if self.stop_record(row): return 'PARK' if self.closed_not_evaluable(row) else 'STOP'
        if (self.case_dir(row)/'PARKED_C83_FINAL.json').exists(): return 'PARK'
        if (self.case_dir(row)/'PARKED.json').exists():
            from .c83_history import reopened
            if not reopened(self,row): return 'PARK'
        if row.get('status')=='COMPLETED': return 'FINISHED'
        return self.chain_next(row)

    def closed_not_evaluable(self,row):
        """T258 (HUB_RULING_20261022_DRAFT_STOP): a documented NOT_EVALUABLE closure (FINAL_DECISIONS_*.json) of THIS attempt, recorded while the design
        sat in STOP, is terminal: it is counted as PARK, not STOP. A closure row naming an older attempt (a design reopened by a later rule) does not
        match, so reopened designs keep their normal STOP/chain behaviour."""
        for path in sorted(self.registry.glob('FINAL_DECISIONS_*.json')):
            for r in (read(path) or {}).get('designs',[]):
                if (r.get('design_id')==row['design_id'] and r.get('attempt')==row['attempt_id']
                        and r.get('decision')=='NOT_EVALUABLE' and r.get('state_at_closure')=='STOP'):return True
        return False

    def chain_next(self,row):
        """First action of the row's chain without a checkpoint (the loop part of next_action; also used by the REDESIGN requeue)."""
        for action in self.chain_for(row):
            if action=='screen' and (self.done(row,'audit') is not None or row.get('route')!='V9' or self.history_reused(row)):continue
            if action=='purpose-direction':
                from .f1_coding import f1_protected as protected
                if protected(self.backend.root(row)) or not hasattr(self.backend,'ctx'): continue
            # Retained reviews/frozen packages cannot acquire new pre-review artifacts.
            if action=='draft-dry-load' and (self.done(row,'draft') is not None or (self.backend.root(row)/'FREEZE_PREP.json').exists()):continue
            if self.done(row,action) is None: return action
        return 'FINISHED'

    def chain_for(self,row):
        """The action order of this row's card: its rules file's chain, or the R0 constant for a card without pinned rules."""
        from . import rules
        if rules.pin_of_row(row) is None:return CHAIN
        return rules.chain(rules.of_row(self,row))

    def draft_attempt(self,row):
        inventory=self.done(row,'draft') or read(self.case_dir(row)/'jobs/draft.json')
        if not inventory:return row['attempt_id']+'_draft'
        from .c54 import draft_identity
        return draft_identity(self,row,inventory['jobs'])[0]

    def review_gate(self,row,paths,scope):
        for path in paths:
            meta = read(Path(path).with_suffix('.job.json'))
            review = read(meta['promote_path'])
            if review.get('must')!=[] or review.get('verdict')!='PASS':
                raise ValueError('MUST>0 or non-PASS '+scope+' review: '+str(path))
            if review.get('review_scope')!=scope: raise ValueError('Review scope differs')
            # Existing registration verifier additionally enforces independence,
            # current code, candidate binding, and evidence-backed SHOULD closure.
            if review.get('should'):
                from .class_table import accept as accept_class
                if accept_class(self,row,path,scope):continue
                from .class_table import enabled
                if enabled(self.backend.root(row)):
                    if scope=='draft_readiness':
                        from .c54 import valid_hub_closure
                        if valid_hub_closure(self,row,path):continue
                    elif scope=='sealed_candidate':
                        from .sealed_closure import accept
                        try:
                            accept(self,row,[path]);continue
                        except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
                    raise ValueError('NEEDS_RULING: NOVEL/science finding requires explicit validated hub closure')
                if scope=='draft_readiness':
                    from .c54 import close_should
                    if close_should(self,row,path):continue
                    from .maintenance import close_should as close_evidenced
                    if close_evidenced(self,row,path):continue
                elif scope=='sealed_candidate':
                    from .sealed_closure import accept
                    try:
                        accept(self,row,[path]);continue
                    except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
                raise ValueError('NEEDS_RULING: review SHOULD requires evidence-backed hub closure: '+json.dumps(review['should'],ensure_ascii=False))

    def agent_action(self,row,action):
        if action=='history' and row.get('prescreen_enabled'):
            from .gate0 import before_history
            gate0=before_history(self,row)
            if gate0.get('requeued'):return gate0
            if gate0['status']=='GATE0_PARK':return None
        if action=='purpose':
            from . import rules
            if rules.pin_of_row(row) is not None:
                from . import redesign
                return redesign.purpose_label_action(self,row)
        if action=='purpose' and row.get('purpose_ruling'):
            return self.backend.perform(row,action,self)
        # Write-once packet inventory survives a crash during collection.
        inventory = self.case_dir(row)/'jobs'/(action+'.json')
        value = read(inventory)
        if value is None:
            value = dict(jobs=self.backend.jobs(row,action,self))
            if not value['jobs']: raise ValueError('No jobs for '+action)
            ensure_once(inventory,value)
        jobs = self.collect_submissions(row,action,value['jobs'])
        if jobs is None: return None
        value = dict(value,jobs=jobs)
        if action in {'draft','sealed'}:
            self.review_gate(row,value['jobs'],'draft_readiness' if action=='draft' else 'sealed_candidate')
        if action=='purpose':
            result=self.backend.perform(row,action,self)
            if result.get('requeued'):return result
        return value

    def replacement_slot(self,row,path):
        from .agents.jobs import _safe_component
        from .write_once import replacement_slot
        role=read(Path(path).with_suffix('.job.json')).get('role',Path(path).stem);_safe_component(role)
        # Per job lineage, not per design: an attempt opened by a requeue never meets an earlier attempt's slot (write_once.py).
        if row.get('attempt_id'):_safe_component(row['attempt_id'])
        return replacement_slot(self.registry.parent/'autopilot',row['design_id'],row.get('attempt_id'),role,path)

    def effective_job(self,row,path):
        selection=read(self.case_dir(row)/'COLLECTION_SELECTION_C48.json')
        if selection and str(path) in selection['jobs']:
            selected=selection['jobs'][str(path)]
            for pointer in selected['evidence']:
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('C48 recollection binding changed')
            path=selected['job']
        slot=self.replacement_slot(row,path)
        repair=read(slot/'TRANSPORT_REPAIR.json')
        if repair and str(path) in {repair['original_job'],repair['failed_replacement_job']}:
            for key in ('diagnosis','repaired_job','repaired_sidecar'):
                pointer=repair[key]
                if sha(pointer['path'])!=pointer['sha256']:raise ValueError('Transport repair binding changed')
            path=repair['repaired_job']['path']
        else:
            receipt=read(slot/'REPLACEMENT.json')
            if receipt and receipt['original_job']==str(path):path=receipt['replacement_job']
        from .agents.history_packet import clean_pending
        from .agents.sonnet_queue import locate, root_for, identity
        meta=read(Path(path).with_suffix('.job.json'))
        if meta and meta.get('contract',{}).get('jobs_kind')=='official_history_collection':
            queued=locate(root_for(self.repo),identity(path))
            out=Path(meta['output_dir'])
            claim=self.registry.parent/'autopilot/worker_claims'/(identity(path)+'.json')
            if (queued is None and not claim.exists() and not claim.with_suffix('.started.json').exists()
                    and not any((out/n).exists() for n in
                        ('DISPATCH_ONCE.json','dispatch.log','DISPATCH_RESULT.json'))
                    and not Path(meta['output_path']).exists() and not Path(meta['result_path']).exists()):
                cleaned=clean_pending(path,self.registry.parent/'autopilot/c58/clean_jobs')
                # A replacement can belong to the C58-clean packet, while the
                # immutable inventory still names its pre-C58 predecessor.
                # Resolve that replacement before inspecting worker state.
                if str(cleaned)!=str(path):return Autopilot.effective_job(self,row,cleaned)
        return str(path)

    def replace_submission(self,row,action,exc):
        slot=self.replacement_slot(row,exc.job)
        if (slot/'REPLACEMENT.json').exists():
            self.ledger(row,action,'REPLACEMENT_EXHAUSTED','replacement',job=exc.job,kind=exc.kind,reason=str(exc))
            if exc.kind in {'V9_SOURCE_ID_URL','V9_RESEARCH_LOG','V9_ABSENCE'} or str(exc)=='V9 requires a state-specific research log for every unit':
                raise Park('§7 contract replacement exhausted: '+str(exc),dict(job=exc.job,kind=exc.kind,rule=RULES['replacement'])) from exc
            raise ValueError('§7 replacement failed; HUB_QUEUE: '+str(exc)) from exc
        intent=dict(design_id=row['design_id'],role=read(Path(exc.job).with_suffix('.job.json'))['role'],
            original_job=exc.job,action=action,kind=exc.kind,reason=str(exc),rule=RULES['replacement'])
        ensure_once(slot/'INTENT.json',intent)
        # Deterministic fresh id makes crash recovery idempotent, never a second job.
        attempt_id='c45_contract_'+hashlib.sha256(str(slot).encode()).hexdigest()[:16]
        path=self.backend.replacement_job(exc.job,slot,attempt_id)
        receipt=dict(intent,replacement_job=str(path),attempt_id=attempt_id)
        ensure_once(slot/'REPLACEMENT.json',receipt)
        self.ledger(row,action,'REPLACEMENT_PREPARED','replacement',**{k:v for k,v in receipt.items() if k not in {'design_id','action','rule'}})
        return str(path)

    def collect_submissions(self,row,action,paths):
        complete=True;jobs=[];failures=[]
        # Collect the other role even when one packet fails. Accepted output is
        # retained and reused; inventories and old submissions are immutable.
        for original in paths:
            path=self.effective_job(row,original);jobs.append(path)
            try:
                state=self.backend.job_state(path)
                self.ledger(row,action,state,'replacement' if path!=original else 'budget',job=path)
                if state=='done':
                    try: self.backend.collect(path)
                    except Park as exc:
                        if action!='history' or 'INSUFFICIENT_WORK' not in str(exc): raise
                        from .c83_history import complete_history
                        if not complete_history(self,row,path): complete=False
                else:
                    complete=False
                    if state=='pending':self.pending.append((row,path))
            except ContractRejection as exc:
                failures.append(exc);complete=False
                self.ledger(row,action,'CONTRACT_REJECTED','replacement',job=exc.job,kind=exc.kind,reason=str(exc))
        for exc in failures:
            path=self.replace_submission(row,action,exc)
            self.pending.append((row,path))
        return jobs if complete else None

    def local_alive(self,row,action):
        root=self.case_dir(row)/'local'/action
        started=read(root/'STARTED.json')
        return bool(started and not (root/'RESULT.json').exists() and self.backend.alive(started['pid']))

    def local_action(self,row,action):
        root=self.case_dir(row)/'local'/action
        result=read(root/'RESULT.json')
        if result:
            if result['status']!='COMPLETED':raise ValueError('Local '+action+' failed: '+result['reason'])
            return result['value']
        if self.local_alive(row,action):
            self.ledger(row,action,'WAIT_LOCAL_WORKER','record');return None
        if (root/'REQUEST.json').exists():raise ValueError('Interrupted/failed local launch; no retry: '+action)
        running=sum(not (p.parent/'RESULT.json').exists() and self.backend.alive(read(p)['pid'])
                    for p in (self.repo/'data/l2_pipeline/autopilot').glob('*/*/local/*/STARTED.json'))
        if running>=self.local_budget:
            self.ledger(row,action,'WAIT_LOCAL_BUDGET','budget',running=running,budget=self.local_budget);return None
        if action=='execute':
            ensure_once(self.case_dir(row)/'EXECUTE_INTENT.json',dict(time=utc(),rule=RULES['execute']))
        request=ensure_once(root/'REQUEST.json',dict(schema='l2_autopilot_local_worker_v1',action=action,row=row,
            registry=str(self.registry),test_report=str(self.test_report) if self.test_report else None,
            rule=RULES['execute' if action=='execute' else 'accept']))
        started=self.backend.launch_local(request)
        ensure_once(root/'STARTED.json',started)
        self.ledger(row,action,'LAUNCHED_LOCAL','record',**started)
        return None

    def tick(self,dry_run=False,max_steps=40):
        if not dry_run:
            from .maintenance import daily_batch
            daily_batch(self)
        self.pending = []
        if not dry_run and type(self.backend) is Backend:
            from .c54 import apply_open_queue
            apply_open_queue(self,scheduler_owned=True)
            from .redesign import apply_open_requests
            apply_open_requests(self)
        if not dry_run:
            atomic(self.repo/'data/l2_pipeline/registry/AUTOPILOT_WORKER_BUDGET.json',dict(max_workers=self.budget,rule=RULES['budget']))
        rows = read(self.registry/'ACTIVE_DESIGNS.json',{}).get('designs',[])
        report = []
        for row in rows:
            action = 'inspect'
            detail = {}
            try:
                root = self.backend.root(row)
                terminal = read(root/'TERMINAL.json') or read(root/'FAILURE.json')
                executing=self.local_alive(row,'execute') if self.backend.async_local else False
                if terminal and self.done(row,'execute') is None:
                    if (self.case_dir(row)/'EXECUTE_INTENT.json').exists():
                        if executing:
                            self.ledger(row,'execute','WAIT_TERMINAL_RECEIPT','execute')
                            report.append(dict(design_id=row['design_id'],next='execute'));continue
                        if terminal['status']=='FAILED_NO_RETRY':
                            # Export the retained failure before queueing; never run again.
                            if not dry_run:
                                self.backend.perform(row,'S7',self); self.backend.perform(row,'register',self)
                            raise ValueError('Run failure: '+str(terminal.get('message',terminal.get('reason'))))
                        if not dry_run: ensure_once(self.checkpoint(row,'execute'),dict(resumed_terminal=True))
                    else:
                        # A finished pre-existing design is entirely read-only.
                        self.ledger(row,'inspect','FINISHED_READ_ONLY','execute')
                        report.append(dict(design_id=row['design_id'],next='FINISHED')); continue
                if (root/'EXECUTION_ONCE.json').exists() and not terminal and not executing:
                    local_result=read(self.case_dir(row)/'local/execute/RESULT.json')
                    if local_result and local_result.get('status')=='FAILED_NO_RETRY':
                        raise ValueError('Run failure without terminal: '+local_result['reason'])
                    raise ValueError('Consumed EXECUTION_ONCE without terminal; no retry')
                if (self.case_dir(row)/'EXECUTE_INTENT.json').exists() and not terminal and not executing:
                    local_result=read(self.case_dir(row)/'local/execute/RESULT.json')
                    if local_result and local_result.get('status')=='FAILED_NO_RETRY':
                        raise ValueError('Run failure before terminal: '+local_result['reason'])
                    raise ValueError('Interrupted execution intent; no retry')
                for review_path in [root/'EXECUTION_REVIEW.json',*sorted(root.glob('REVIEW_DRAFT*.json'))]:
                    review=read(review_path)
                    if review and review.get('must'):
                        raise ValueError('MUST>0 in retained review: '+str(review_path)+' '+json.dumps(review['must'],ensure_ascii=False))
                if not dry_run:
                    from .gate0 import refresh
                    refreshed=refresh(self,row)
                    if refreshed and refreshed.get('requeued'):
                        row=refreshed['receipt']['new_row'];root=self.backend.root(row)
                    from .maintenance import resume_should,resume_code_fix
                    resume_should(self,row)
                    resume_code_fix(self,row)
                    from .sealed_closure import resume as resume_sealed
                    resume_sealed(self,row)
                for _ in range(max_steps):
                    action = self.next_action(row)
                    if action in {'STOP','PARK','PRESCREEN_PARK','GATE0_PARK','FINISHED'}:
                        if action=='STOP' and not dry_run:
                            stopped=self.stop_record(row)
                            self.stop(row,stopped['action'],stopped['reason'])
                        if action in {'PARK','PRESCREEN_PARK','GATE0_PARK','FINISHED'} and not dry_run:
                            parked=read(self.case_dir(row)/'PARKED.json',{})
                            self.ledger(row,'observe','OBSERVED_'+action,'record',reason=str(parked.get('reason',''))[:240])
                        if not dry_run:
                            from .gate0 import record_audit
                            record_audit(self,row,terminal=action,reason=str(read(self.case_dir(row)/'PARKED.json',{}).get('reason','')),
                                estimated=read(root/'RESULT.json',{}).get('actual_data_estimation_calls',0)>0 if action=='FINISHED' else False)
                        break
                    rule = 'prep' if action in {'prep','approve-prep'} else 'final' if action in {'approve-final','dry-load','sealed'} else 'execute' if action in {'execute','S7','register'} else 'accept'
                    self.ledger(row,action,'PLAN' if dry_run else 'DECIDE',rule)
                    if dry_run:
                        if action=='history' and row.get('history_jobs'):
                            detail=dict(jobs={p:self.backend.job_state(p) for p in row['history_jobs']},
                                        global_running=self.backend.global_running())
                        break
                    if row['route']=='V8b' and action in {'history','reconcile','audit'}:
                        value = self.v8b_action(row,action)
                    elif action in {'history','reconcile','audit'} and self.curated_declared(row):
                        value = self.curated_action(row,action)
                    elif action in {'history','reconcile','audit'} and self.history_reused(row):
                        value = self.reuse_action(row,action)
                    elif action=='screen':
                        from . import redesign
                        value = redesign.screen_action(self,row)
                    elif action in {'history','audit','purpose','forecasts','draft','sealed'}:
                        value = self.agent_action(row,action)
                    elif action in {'calibrate','execute'} and self.backend.async_local:
                        value=self.local_action(row,action)
                    else:
                        if action=='execute':
                            ensure_once(self.case_dir(row)/'EXECUTE_INTENT.json',dict(time=utc(),rule=RULES['execute']))
                        value = self.backend.perform(row,action,self)
                    if value is None: break
                    if value.get('requeued'):
                        row=value['receipt']['new_row'];root=self.backend.root(row)
                        self.ledger(row,action,'MECHANICAL_REQUEUE','record',restart=value['receipt']['restart_action'])
                        continue
                    ensure_once(self.checkpoint(row,action),value)
                    if action=='reconcile' and value.get('excluded_conflict_states'):
                        self.ledger(row,action,'EXCLUDE','exclude',
                            excluded_conflict_states=value['excluded_conflict_states'],
                            excluded_conflict_state_reasons=value['excluded_conflict_state_reasons'],
                            counts=value['counts'])
                    self.ledger(row,action,'ACCEPT',rule)
                report.append(dict(design_id=row['design_id'],next=action,**detail))
            except Park as exc:
                if not dry_run:
                    park_name='PARKED_C83_FINAL.json' if exc.metrics.get('final') or (self.case_dir(row)/'HISTORY_COMPLETION_REQUEUE.json').exists() else 'PARKED.json'
                    from . import redesign, rules
                    klass=redesign.class_of(self,row,action,str(exc))
                    ensure_once(self.case_dir(row)/park_name,dict(
                        design_id=row['design_id'], attempt_id=row['attempt_id'], action=action,
                        status='PARKED', reason=str(exc), metrics=exc.metrics, rule=RULES['exclude'],
                        **(dict(class_id=klass['id'],reopen=klass['reopen'],rules_sha256=rules.pin_of_row(row)['sha256']) if klass else {})))
                self.ledger(row,action,'WOULD_PARK' if dry_run else 'PARK','exclude',
                            reason=str(exc),metrics=exc.metrics)
                report.append(dict(design_id=row['design_id'],next='PARK',reason=str(exc),metrics=exc.metrics))
            except Exception as exc:
                if environmental(exc): raise  # host condition: abort this tick, retry; the design is not STOPped
                reason = type(exc).__name__+': '+str(exc)
                from . import redesign
                if not dry_run:
                    if not redesign.park_or_stop(self,row,action,reason): self.stop(row,action,reason)
                else: self.ledger(row,action,'WOULD_STOP','stop',reason=reason)
                report.append(dict(design_id=row['design_id'],next='STOP',reason=reason))
        if not dry_run:
            from collections import Counter
            counts=dict(Counter(r['next'] for r in report))
            if not self.pending:
                from . import tick_blockers
                self.ledger({},'tick','IDLE','record',blockers=counts,**tick_blockers.idle_extra(self,report))
                tick_blockers.note_starved(self,report)
            self.schedule()
            from .gate0 import summarize_audits
            summarize_audits(self,rows)
            active=self.registry/'ACTIVE_DESIGNS.json'
            atomic(self.registry/'LOOP_STATE.json',dict(schema='l2_loop_state_v1',
                time=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),
                next_counts=counts,queued_jobs=len(self.pending),
                active_designs_sha256=sha(active) if active.is_file() else None))
        return dict(designs=report,budget=self.budget,queued_jobs=len(self.pending),dry_run=dry_run,
            gate0_park_designs=sorted({r['design_id'] for r in report if r.get('next')=='GATE0_PARK'}),
            prescreen_park_designs=sorted({r['design_id'] for r in report if r.get('next')=='PRESCREEN_PARK'}),
            l2_design_count_excluding_prescreen=len({r['design_id'] for r in report if r.get('next') not in {'PRESCREEN_PARK','GATE0_PARK'}}))

    def queue_reconciliations(self):
        receipts = self.repo/'data/l2_pipeline/registry/worker_reconciliations'
        queue = read(self.registry/'HUB_QUEUE.json',dict(schema='l2_hub_queue_v1',entries=[]))
        known = {v.get('reconciliation_receipt') for v in queue['entries']}
        for path in sorted(receipts.glob('*.json')):
            if str(path) in known: continue
            receipt = read(path)
            entry = dict(design_id='orphan_worker_'+path.stem[:12],attempt_id='c44_liveness',
                action='dispatch',status='DEAD_NO_RESULT',reason=receipt['reason'],rule=RULES['orphan'],
                reconciliation_receipt=str(path),reservation=receipt['reservation'],retries=0)
            queue['entries'].append(entry)
            self.ledger(entry,'dispatch','DEAD_NO_RESULT','orphan',receipt=str(path),automatic_redispatch=False)
        if queue['entries'] != read(self.registry/'HUB_QUEUE.json',dict(entries=[]))['entries']:
            atomic(self.registry/'HUB_QUEUE.json',queue)

    def executor(self,row,path):
        # Existing launch claims/CLI receipts retain their original transport.
        from .agents.sonnet_queue import locate,root_for,identity,route
        if locate(root_for(self.repo),identity(path)):return 'sonnet_queue'
        meta=read(Path(path).with_suffix('.job.json'));out=Path(meta['output_dir'])
        claim=self.repo/'data/l2_pipeline/autopilot/worker_claims'/(hashlib.sha256(str(path).encode()).hexdigest()+'.json')
        if claim.exists() or any((out/n).exists() for n in ('DISPATCH_ONCE.json','dispatch.log','DISPATCH_RESULT.json')):return 'codex'
        if type(self.backend) is not Backend:return 'codex'  # injected transport tests
        overrides=dict(self.role_backends)
        overrides.update(row.get('agent_backend_overrides',{}))
        overrides.update(meta.get('contract',{}).get('role_backend_overrides',{}))
        if meta['role'] in overrides:return route(row['design_id'],meta['role'],overrides)
        if self.agent_backends!='mixed':return self.agent_backends
        return route(row['design_id'],meta['role'])

    def schedule(self):
        from .agents.sonnet_queue import root_for,reap,locate,identity,enqueue,reroute_pending
        from .agents.purpose_direction import is_direction,transport_failure
        queue_root=root_for(self.repo)
        if queue_root.exists():
            for event in reap(queue_root):
                if event['status']=='rerouted_codex':
                    row=event.pop('row')
                    self.ledger(row,'dispatch','REROUTED_CODEX_C57','budget',**event)
            for event in reroute_pending(queue_root):
                row=event.pop('row')
                self.ledger(row,'dispatch','REROUTED_CODEX_C57','budget',**event)
            # YIELD_FIXES: a STOPped design must not keep claimable packets.
            from .agents.sonnet_queue import retire_stopped, hub_reason
            active={r['design_id']:r for r in read(self.registry/'ACTIVE_DESIGNS.json',{}).get('designs',[])}
            def terminal(attempt):
                """STOPped, or finally PARKed (a PARK that the C81 completion reopens is still running)."""
                if self.stop_record(attempt):return True
                case=self.case_dir(attempt)
                if (case/'PARKED_C83_FINAL.json').exists():return True
                if (case/'PARKED.json').exists():
                    from .c83_history import reopened
                    return not reopened(self,attempt)
                return False
            def stopped(packet_row,job_path):
                current=active.get(packet_row.get('design_id'))
                if not current:return False
                if current.get('attempt_id')==packet_row.get('attempt_id'):return terminal(current)
                # A later attempt exists (requeue): the old packet is obsolete only if its own
                # attempt is STOPped or PARKed and the current attempt keeps no reference to the job.
                if not terminal(packet_row):return False
                jobs=self.case_dir(current)/'jobs'
                return not any(job_path in f.read_text() for f in (jobs.glob('*.json') if jobs.is_dir() else []))
            try:
                for event in retire_stopped(queue_root,stopped):
                    row=event.pop('row')
                    self.ledger(row,'dispatch','RETIRED_STOPPED','record',**event)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                # Housekeeping must never end the loop; the error is visible and retried next tick.
                self.ledger({},'dispatch','RETIRE_STOPPED_ERROR','record',reason=type(exc).__name__+': '+str(exc)[:200])
            # Durable hub packets make escalation restart-safe after a crash.
            for packet in sorted((queue_root/'hub').iterdir()):
                value=read(packet/'PACKET.json')
                reason=hub_reason(packet)+': '+value['job_path']
                if not transport_failure(self,value['row'],value['job_path'],reason):
                    # F10: a queue event PARKs the design (reopenable by release_recovery); it never STOPs it.
                    from .release_policy import dispose_hub_packet
                    dispose_hub_packet(self,packet,hub_reason(packet))
        running = self.backend.global_running()
        self.queue_reconciliations()
        claims = self.repo/'data/l2_pipeline/autopilot/worker_claims'
        # A launched child may not yet have reserved DISPATCH_ONCE. Its durable
        # launch receipt counts too; a lost launch window is never relaunched.
        for p in sorted(claims.glob('*.json')):
            if p.name.endswith('.started.json'): continue
            claim = read(p)
            if claim.get('backend')=='sonnet_queue':continue
            if self.effective_job(claim['row'],claim['job'])!=claim['job']: continue
            # Only the design's currently waiting role can invoke §7. Historical
            # launch claims never stop or re-dispatch a completed/advanced design.
            action=self.next_action(claim['row'])
            if action not in {'history','audit','purpose','forecasts','draft','sealed'}: continue
            try: state = self.backend.job_state(claim['job'])
            except ContractRejection:
                continue  # The waiting stage handles this packet on the next tick.
            except Exception as exc:
                self.stop(claim['row'],'dispatch',type(exc).__name__+': '+str(exc));continue
            launched = read(p.with_suffix('.started.json'))
            if state=='done': continue
            if state=='running':
                # Canonical job liveness can verify a CLI that outlived its host.
                continue
            from .agents.liveness import age, MAX_AMBIGUOUS_SECONDS
            liveness = self.backend.launch_liveness(launched) if launched else 'ambiguous'
            if liveness=='alive' or (liveness=='ambiguous' and age(p,launched or claim)<MAX_AMBIGUOUS_SECONDS):
                running += 1
            else:
                waiting=(claim['row'],claim['job']) in self.pending
                if waiting:
                    try:
                        replacement=self.replace_submission(claim['row'],action,ContractRejection(
                            claim['job'],'DEAD_NO_RESULT','Dead/expired consumed launch claim without result'))
                        self.pending=[(r,replacement if path==claim['job'] else path) for r,path in self.pending]
                    except Exception as exc:self.stop(claim['row'],action,str(exc))
        seen = set()
        for row,path in self.pending:
            path=self.effective_job(row,path)
            if path in seen or self.stop_record(row): continue
            seen.add(path)
            if is_direction(path) and self.done(row,'purpose-direction') is not None:continue
            executor=self.executor(row,path)
            if executor=='sonnet_queue':
                try:
                    if self.backend.job_state(path)=='pending':
                        launched=enqueue(path,row,queue_root)
                        self.ledger(row,'dispatch','LAUNCHED','budget',job=path,**launched)
                except Park as exc:
                    from .release_policy import park_from_exception
                    if not park_from_exception(self,queue_root,exc):
                        reason=type(exc).__name__+': '+str(exc)
                        if not transport_failure(self,row,path,reason):self.stop(row,'dispatch',reason)
                except Exception as exc:
                    reason=type(exc).__name__+': '+str(exc)
                    if not transport_failure(self,row,path,reason):self.stop(row,'dispatch',reason)
                continue
            if running>=self.budget:
                self.ledger(row,'dispatch','WAIT_GLOBAL_BUDGET','budget',running=running,budget=self.budget)
                continue
            claim = claims/(hashlib.sha256(str(path).encode()).hexdigest()+'.json')
            if claim.exists(): continue
            try:
                # Re-read immediately before reservation: another old host may
                # have completed or reserved the packet since this tick's plan.
                if self.backend.job_state(path)!='pending': continue
                from .agents.sonnet_queue import claude_only
                if claude_only():raise ValueError('Claude-only: Codex launch refused')
                rule='replacement' if read(Path(path).with_suffix('.job.json')).get('contract',{}).get('contract_replacement') else 'budget'
                self.ledger(row,'dispatch','RESERVE',rule,job=path,running=running,budget=self.budget)
                ensure_once(claim,dict(job=path,row=row,rule=RULES[rule],started_at=utc()))
                launched = self.backend.launch(path,claim)
                ensure_once(claim.with_suffix('.started.json'),launched)
                running += 1
                self.ledger(row,'dispatch','LAUNCHED',rule,job=path,**launched)
            except Exception as exc:
                reason=type(exc).__name__+': '+str(exc)
                if not transport_failure(self,row,path,reason):self.stop(row,'dispatch',reason)

        from .agents.sonnet_queue import write_status
        write_status(self)

    def v8b_action(self,row,action):
        from .intake.config import IntakeConfig
        from .intake import v8b_calendar
        from .intake.fresh_gate import fresh_gate
        root = Path(row['routing']['intake_root'])
        v8b_calendar.configure(IntakeConfig.from_existing(root)); v8b_calendar.install()
        fresh_gate(root,[row['design_id']],validate_audit_clock=v8b_calendar.validate_audit_clock)
        if action in {'history','reconcile'}: return dict(validated_route='V8b',changes=0)
        jobs = row['routing']['fresh_jobs']
        effective=self.collect_submissions(row,action,jobs)
        return dict(jobs=effective) if effective is not None else None

    def history_reused(self,row):
        from . import history_reuse
        return history_reuse.declared(row)

    def curated_declared(self,row):
        from . import curated_route
        return curated_route.declared(row)

    def curated_action(self,row,action):
        """T219 (HUB_RULING_20261021_CURATED_HISTORY): history, reconcile and audit of a design whose card declares a curated database."""
        from . import curated_route
        if action=='history':
            if row.get('prescreen_enabled'):
                from .gate0 import before_history
                gate0=before_history(self,row)
                if gate0.get('requeued'):return gate0
                if gate0['status']=='GATE0_PARK':return None
            value=curated_route.start(self,row)
            if value is not None:self.ledger(row,'history','CURATED_HISTORY_AUDIT_PACKETS_COLLECTED','record',sample=value['sample'])
            return value
        if action=='reconcile':return curated_route.reconcile(self,row)
        return curated_route.audit(self,row)

    def reuse_action(self,row,action):
        """YIELD_FIXES ruling (ii): history, reconcile and audit come from an already-certified history."""
        from . import history_reuse
        if action=='history':
            if row.get('prescreen_enabled'):
                from .gate0 import before_history
                gate0=before_history(self,row)
                if gate0.get('requeued'):return gate0
                if gate0['status']=='GATE0_PARK':return None
            try:value=history_reuse.reuse(self,row)
            except history_reuse.SourceNotReady as exc:
                # The source is between attempts (for example a catalog requeue): wait, never STOP.
                self.ledger(row,'history','WAIT_HISTORY_REUSE_SOURCE','record',reason=str(exc))
                return None
            self.ledger(row,'history','HISTORY_REUSED','record',source_design_id=value['source_design_id'],record=value['record'])
            return value['checkpoints']['history']
        return history_reuse.checkpoint(self,row,action)

    def register_terminal(self,row,root):
        """Append without changing any existing event; write a bound receipt first."""
        from .cli import run_stage
        from . import snapshots
        ctx = self.backend.ctx(row,strict=True)
        frozen = snapshots.manifest_for(root)
        # S7 must run inside the final import tree, as execution does.
        if not (root/'EXPORT.json').exists():
            if frozen: snapshots.call(root,frozen,'run_stage',dict(context=snapshots.context_payload(ctx),stage='S7'))
            else: run_stage(ctx,'S7')
        export = read(root/'EXPORT.json')
        fragments = {'REGISTRY_EVENT.json':export['registry_event'],'SITE_EXPORT_DATA.json':export['estimates_page'],
                     'L3_EXPORT_DATA.json':export['l3_meta']}
        for name,value in fragments.items(): ensure_once(root/name,value)
        terminal_path = root/('TERMINAL.json' if (root/'TERMINAL.json').exists() else 'FAILURE.json')
        terminal = read(terminal_path); result = read(root/'RESULT.json',{})
        run_id = row['design_id']+'__'+ctx.card.version+'__'+row['attempt_id']
        record = dict(design_id=row['design_id'],spec_version=ctx.card.version,run_id=run_id,
            status=terminal['status'],terminal_phase='POST_ATTEMPT',is_deviation=False,retries=0,
            package_path=str(root),terminal_path=str(terminal_path),terminal_sha256=sha(terminal_path),
            original_event=export['registry_event'],original_event_sha256=sha(root/'REGISTRY_EVENT.json'),
            actual_data_estimation_calls=result.get('actual_data_estimation_calls',0),
            study_estimated=result.get('actual_data_estimation_calls',0)>0,
            reason=terminal.get('reason',terminal.get('message')),
            s7_exports={n:sha(root/n) for n in ('EXPORT.json',*fragments)})
        for filename,key in [('RESULT.json','result'),('EXECUTION_ONCE.json','execution_once'),
                             ('APPROVAL_FINAL.json','approval_final'),('DRY_LOAD.json','dry_load')]:
            if (root/filename).exists(): record[key+'_sha256']=sha(root/filename)
        if (root/'RESULT.json').exists(): record['result_path']=str(root/'RESULT.json')
        if isinstance(export.get('l3_meta',{}).get('result'),dict) and '$ref' in export['l3_meta']['result']:
            from .c64 import read_l3_export
            read_l3_export(root/'L3_EXPORT_DATA.json')
            if isinstance(result.get('l3_export_v2'),dict):
                record['l3_export_v2_reference']=dict(path=str(root/'RESULT.json'),sha256=sha(root/'RESULT.json'),pointer='/l3_export_v2')
        if frozen: record['final_snapshot_digest']=frozen['code_snapshot']['digest']
        from .gate0 import record_audit
        record_audit(self,row,terminal=terminal['status'],reason=record.get('reason',''),estimated=record['study_estimated'])
        receipt = ensure_once(self.case_dir(row)/'TERMINAL_APPEND.json',record)
        path = self.registry/'TERMINAL_RUNS.json'
        # Share an advisory registry lock with cooperating writers; also retain
        # old bytes and compare hash immediately before atomic replacement.
        with (self.registry/'TERMINAL_RUNS.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            before = sha(path) if path.exists() else None
            registry = read(path,dict(schema='l2_terminal_run_registry_snapshot_v1',runs=[],pre_run_stops=[]))
            existing = [v for v in registry['runs'] if v['run_id']==run_id]
            if existing:
                if existing!=[record]: raise ValueError('Terminal append identity conflict')
                return dict(run_id=run_id,receipt=str(receipt),resumed=True)
            if any(v['design_id']==row['design_id'] for v in registry['runs']):
                raise ValueError('NEEDS_RULING: terminal design duplicate; amended runs are hub-only')
            # A neighbour can append between this design's crash and recovery.
            # Keep each distinct before-image instead of treating that legitimate
            # registry advance as a changed immutable preparation artifact.
            digest=hashlib.sha256(encoded(registry)).hexdigest()
            ensure_once(self.case_dir(row)/('TERMINAL_REGISTRY_BEFORE_'+digest+'.json'),registry)
            registry['runs'].append(record)
            registry['run_count']=len(registry['runs'])
            registry['design_count']=len({v['design_id'] for v in registry['runs']})
            registry['total_terminal_design_count_including_pre_run']=len({v['design_id'] for v in registry['runs']+registry['pre_run_stops']})
            # Preserve the historical aggregate and mark it explicitly stale;
            # no pre-registration outcome/party association is tabulated here.
            if 'terminal_summary' in registry:
                registry.setdefault('terminal_summary_as_of_run_count',len(registry['runs'])-1)
            if before is not None and sha(path)!=before: raise ValueError('Concurrent terminal registry change')
            atomic(path,registry)
        return dict(run_id=run_id,receipt=str(receipt))


def ingest(cards_dir,pilot,*,card_paths=None):
    from .cards import load_card
    from .intake.history_routing import validate_fresh_policy, prior_sample_metadata
    manifest = read(pilot.registry/'ACTIVE_DESIGNS.json',dict(schema='l2_active_designs_v1',designs=[]))
    terminal = read(pilot.registry/'TERMINAL_RUNS.json',dict(runs=[],pre_run_stops=[]))
    seen = {r['design_id'] for r in manifest['designs']+terminal['runs']+terminal['pre_run_stops']}
    # Find jobs outside ACTIVE_DESIGNS too. An explicitly declared, validated
    # V8b route can own its already-completed calibration/prepared fresh packets;
    # a running wave or a different route is still a duplicate.
    rows = []
    prior_card_cache = {}
    paths = sorted(public_input(Path(p)) for p in card_paths) if card_paths is not None else sorted(public_input(Path(cards_dir)).glob('*.json'))
    if not paths: raise ValueError('No design cards')
    from .ops.ingest_gate import check as check_ingest
    ingest_gate=check_ingest(pilot,paths)
    from .ops.ingest_gate import throttle
    throttle(pilot,paths)  # R3 cards only: bounded number of designs waiting before screen
    from .c42 import completeness
    reports = []
    for path in paths:
        try:
            value = read(path)
            errors = completeness(value)
            did = value.get('design_id', path.stem) if isinstance(value, dict) else path.stem
        except (ValueError, OSError, KeyError, TypeError) as exc:
            did, errors = path.stem, [type(exc).__name__+': '+str(exc)]
        reports.append(dict(card=str(path),design_id=did,complete=not errors,missing_or_invalid=errors))
    for report in reports:
        print('CARD_COMPLETENESS '+json.dumps(report,sort_keys=True),flush=True)
    if any(not r['complete'] for r in reports):
        raise ValueError('Incomplete intake cards; fix every missing_or_invalid field before ingest')
    existing_jobs={}
    for sidecar in (pilot.repo/'data/l2_pipeline').rglob('*.job.json'):
        meta=read(sidecar)
        if meta.get('design_id'):existing_jobs.setdefault(meta['design_id'],[]).append((sidecar,meta))
    # Validate the entire intake before any manifest/card mutation.
    for path in paths:
        card = load_card(path); did=card.design_id
        from . import rules
        rules.of_payload(card.payload,pilot.repo)  # a card that pins rules must match the rules file bytes
        from . import history_reuse,intake_replay
        required=history_reuse.required_source(pilot,card.payload)  # R3: reuse of a certified same-policy history is the default
        if required:raise ValueError('History reuse is the default under these rules: card '+did+' must carry automation.history_reuse naming '+required)
        replayed=intake_replay.check_card(pilot,path,card.payload)  # R3: outcome-blind S3 replay on the certified roster (a failing one is parked by Gate 0 below)
        if replayed['status']=='NEEDS_EXCLUSIONS':raise ValueError('Intake replay: '+did+' '+replayed['reason'])
        if did in seen: raise ValueError('Duplicate terminal/in-flight design: '+did)
        seen.add(did)
        routing = dict(card.payload.get('automation',{}).get('autopilot',{}))
        kind = routing.get('route','V9')
        if kind not in {'V9','V8b'}: raise ValueError('Only V9 or validated V8b routes are authorized')
        row = dict(design_id=did,card=str(path.resolve()),card_sha256=sha(path),route=kind,routing=routing,
            attempt_id='autopilot_'+card.version,work_dir=str(pilot.registry.parent/'autopilot/work'),managed_execution=True)
        intake = public_input(Path(routing['intake_root'])) if routing.get('intake_root') else pilot.case_dir(row)/'intake'
        routing['intake_root']=str(intake)
        intake_card=intake/'cards'/(did+'.json')
        generated = not routing.get('existing_intake',False) and not (intake/'imports'/did/'PANEL_IMPORT.json').exists()
        if intake_card.exists() and sha(intake_card)!=sha(path):
            raise ValueError('Generic router intake card differs')
        if kind=='V8b' and generated: raise ValueError('V8b requires an existing validated intake route')
        own_jobs=existing_jobs.get(did,[])
        if own_jobs:
            owned_v8b=kind=='V8b' and all(
                p.resolve().is_relative_to(intake.resolve()) and
                m.get('contract',{}).get('card_sha256')==sha(path) and
                not ((Path(m['output_dir'])/'DISPATCH_ONCE.json').exists() and
                     not (Path(m['output_dir'])/'DISPATCH_RESULT.json').exists())
                for p,m in own_jobs)
            if not owned_v8b:raise ValueError('Duplicate prepared/in-flight design: '+did)
        if generated:
            universe=card.payload['treatment'].get('universe')
            if not universe or len(set(universe))!=len(universe): raise ValueError('V9 intake requires a declared unique treatment universe')
            row['generated_intake']=dict(units=universe)
        if kind=='V9':
            prior=list(routing.get('prior_jobs',[]))
            policy_id=card.payload['treatment'].get('policy_id')
            for entries in existing_jobs.values():
                for packet,meta in entries:
                    c=meta.get('contract',meta)
                    if meta.get('role')!='auditor' or not isinstance(c.get('sample'),list):continue
                    prior_policy=c.get('policy_id')
                    if policy_id and not prior_policy and c.get('card_path'):
                        old_path=c['card_path']
                        if old_path not in prior_card_cache:
                            prior_card_cache[old_path]=read(old_path,{})
                        prior_policy=prior_card_cache[old_path].get('treatment',{}).get('policy_id')
                    same=(c.get('mechanism')==card.mechanism or c.get('predicate')==card.mechanism
                          or c.get('mechanism')==card.payload['treatment'].get('predicate')
                          or c.get('predicate')==card.payload['treatment'].get('predicate')
                          or bool(policy_id and prior_policy==policy_id))
                    if same:
                        prior.append(str(packet.resolve()))
            routing['prior_jobs']=sorted(set(map(str,prior)))
            excluded,previous,_ = prior_sample_metadata(routing['prior_jobs'])
            from .intake.config import IntakeConfig
            # Generic router additionally discovers this intake's emitted samples.
            for source in sorted((intake/'calibration_jobs/S1'/did).glob('*.job.json')):
                units,seeds,_ = prior_sample_metadata([source]); excluded+=units; previous|=seeds
            for source in sorted((intake/'fresh_audit_jobs/S1'/did).glob('*.job.json')):
                meta=read(source)
                if Path(meta['output_path']).exists() or (Path(meta['output_dir'])/'DISPATCH_ONCE.json').exists():
                    units,seeds,_=prior_sample_metadata([source]); excluded+=units;previous|=seeds
            seed=routing.get('audit_seed',int(hashlib.sha256((did+sha(path)).encode()).hexdigest()[:8],16))
            routing['audit_seed']=seed
            if seed in previous | {card.payload['treatment'].get('seed')}: raise ValueError('Fresh V9 seed was previously used')
            universe=row['generated_intake']['units'] if generated else read(intake/'imports'/did/'PANEL_IMPORT.json')['units']
            row['fresh_policy']=validate_fresh_policy(dict(seed=seed,excluded_units=sorted(set(excluded)),sample_size=10),universe)
        else:
            from .intake.config import IntakeConfig
            from .intake import v8b_calendar
            from .intake.fresh_gate import fresh_gate
            v8b_calendar.configure(IntakeConfig.from_existing(intake));v8b_calendar.install()
            fresh_gate(intake,[did],validate_audit_clock=v8b_calendar.validate_audit_clock)
            routing['fresh_jobs']=[str(p) for p in sorted((intake/'fresh_audit_jobs/S1'/did).glob('*.md'))]
            if not routing['fresh_jobs']: raise ValueError('Validated V8b route has no fresh jobs')
        row['prescreen_enabled']=True
        from .gate0 import persist
        persist(pilot,row)
        rows.append(row)
    # Cards remain at their original hash-bound repository path: moving a card
    # would change relative public source bindings. External cards are refused.
    for row in rows:
        durable(row['card'],pilot.repo)
        if row.get('generated_intake'):
            intake=durable(row['routing']['intake_root'],pilot.repo)
            card_path=intake/'cards'/(row['design_id']+'.json')
            card_path.parent.mkdir(parents=True,exist_ok=True)
            if not card_path.exists():
                with card_path.open('xb') as stream:
                    stream.write(Path(row['card']).read_bytes());stream.flush();os.fsync(stream.fileno())
            ensure_once(intake/'imports'/row['design_id']/'PANEL_IMPORT.json',row['generated_intake'])
        pilot.ledger(row,'ingest','ACCEPT','accept',route=row['route'])
    if ingest_gate is not None:atomic(pilot.registry/'INGEST_GATE.json',ingest_gate)
    manifest['designs'].extend(rows);atomic(pilot.registry/'ACTIVE_DESIGNS.json',manifest)
    return dict(ingested=len(rows),designs=[r['design_id'] for r in rows])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=['tick','loop','ingest','local-worker','requeue','sonnet-worker-instructions'],default='tick')
    parser.add_argument('cards_dir',nargs='?',type=Path)
    parser.add_argument('--registry',type=Path,default=REGISTRY)
    parser.add_argument('--design')
    parser.add_argument('--card',type=Path)
    parser.add_argument('--ruling',type=Path)
    parser.add_argument('--max-workers',type=int,default=6)
    parser.add_argument('--agent-backends',choices=['mixed','codex','sonnet_queue'],default='mixed',help='mixed routes research roles to Codex and purpose/forecast/reviews to Sonnet')
    parser.add_argument('--role-backend',action='append',default=[],metavar='ROLE=BACKEND')
    parser.add_argument('--max-local-workers',type=int,default=2,help='Python calibration/run workers; separate from Codex budget')
    parser.add_argument('--test-report',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--hub-outside-sandbox',action='store_true')
    parser.add_argument('--time-limit',type=float,default=28800,help='Loop wall-clock limit in seconds')
    parser.add_argument('--poll-seconds',type=float,default=15)
    parser.add_argument('--stop-file',type=Path,default=REGISTRY/'AUTOPILOT_STOP')
    args=parser.parse_args(argv)
    if args.time_limit<=0 or not 0<args.poll_seconds<=60: parser.error('Positive time limit; poll seconds 0..60')
    try:
        if args.action=='sonnet-worker-instructions':
            from .agents.sonnet_queue import WORKER_INSTRUCTIONS
            print(WORKER_INSTRUCTIONS,end='');return 0
        overrides=dict(item.split('=',1) for item in args.role_backend)
        if args.action=='requeue':
            if not args.design or args.card is None or args.ruling is None:raise ValueError('requeue requires --design --card --ruling')
            from .requeue import requeue
            print(json.dumps(requeue(args.design,args.card,args.ruling,Autopilot(args.registry)),indent=2));return 0
        if args.action=='local-worker':
            from .agents.dispatch import require_host
            require_host(args.hub_outside_sandbox)
            request_path=durable(args.cards_dir);request=read(request_path)
            if request.get('schema')!='l2_autopilot_local_worker_v1' or request.get('action') not in {'calibrate','execute'}:
                raise ValueError('Invalid local worker request')
            pilot=Autopilot(request['registry'],test_report=request.get('test_report'))
            try:
                value=pilot.backend.perform(request['row'],request['action'],pilot)
                ensure_once(request_path.parent/'RESULT.json',dict(status='COMPLETED',value=value))
                return 0
            except Exception as exc:
                ensure_once(request_path.parent/'RESULT.json',dict(status='FAILED_NO_RETRY',reason=type(exc).__name__+': '+str(exc)))
                return 1
        pilot=Autopilot(args.registry,budget=args.max_workers,test_report=args.test_report,local_budget=args.max_local_workers,agent_backends=args.agent_backends,role_backends=overrides)
        with scheduler_lock(pilot.repo/'data/l2_pipeline/registry'):
            if args.action=='ingest':
                if args.cards_dir is None: raise ValueError('ingest requires cards_dir')
                print(json.dumps(ingest(args.cards_dir,pilot),indent=2));return 0
            if not args.dry_run:
                from .agents.dispatch import require_host
                require_host(args.hub_outside_sandbox)
            if (pilot.registry/'CODE_TASK.lock').exists():raise ValueError('Code task owns maintenance lock')
            start=time.monotonic();environment_ticks=0
            if args.action=='loop':pilot.ledger({},'loop','LOOP_START','record',pid=os.getpid())
            while True:
                if args.action=='loop' and (args.stop_file.exists() or (pilot.registry/'CODE_TASK.lock').exists() or time.monotonic()-start>=args.time_limit):
                    pilot.ledger({},'loop','CLEAN_STOP','record',workers_retained=True);break
                try: report=pilot.tick(dry_run=args.dry_run)
                except OSError as exc:
                    if args.action!='loop' or args.dry_run or not environmental(exc): raise
                    environment_ticks+=1
                    print('ENVIRONMENT_ERROR tick '+str(environment_ticks)+'/'+str(MAX_ENVIRONMENT_TICKS)+': '+str(exc),file=sys.stderr,flush=True)
                    if environment_ticks>=MAX_ENVIRONMENT_TICKS: raise
                    time.sleep(ENVIRONMENT_RETRY_SECONDS);continue
                environment_ticks=0
                print(json.dumps(report,indent=2),flush=True)
                if args.action!='loop' or args.dry_run: break
                time.sleep(min(args.poll_seconds,max(0,args.time_limit-(time.monotonic()-start))))
        return 0
    except (OSError,ValueError,KeyError) as exc:
        if args.action=='loop' and not args.dry_run:
            try: atomic(args.registry/'LOOP_CRASH.json',dict(schema='l2_loop_crash_v1',pid=os.getpid(),time=utc(),
                                                              error=type(exc).__name__+': '+str(exc)))
            except OSError: pass  # the marker is best effort; the watchdog also sees the missing CLEAN_STOP
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__': raise SystemExit(main())
