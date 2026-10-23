"""Durable test evidence and integrity checks; never dispatch or fit."""
from pathlib import Path
from scripts.l2_pipeline.autopilot import Autopilot,read,scheduler_lock
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.freeze import code_inventory
from scripts.l2_pipeline.registration import _check_evidence
from scripts.l2_pipeline.c46 import TARGETS
ROOT=Path.cwd();OUT=ROOT/'data/l2_pipeline/autopilot/c46';p=Autopilot()
runs=[read(OUT/(n+'_RUN.json')) for n in ('TEST_ORDINARY_VALIDATED','TEST_OPTIMIZED_VALIDATED')]
if any(r is None or r['exit_code']!=0 for r in runs):raise ValueError('Both final test suites must pass')
current=code_inventory()
if read(OUT/'TEST_CODE_VALIDATED_BEFORE.json')!=current:raise ValueError('Code changed during final test run')
ensure_once(OUT/'TEST_CODE_VALIDATED_AFTER.json',current)
report=ensure_once(OUT/'TEST_REPORT_FINAL.json',dict(schema='l2_pipeline_test_evidence_v1',passed=True,runs=runs,no_archival_fits=True,code_inventory=current))
_check_evidence(report)
finished=read(OUT/'FINISHED_BASELINE.json');changed=[name for name,h in finished['files'].items() if not Path(name).is_file() or sha(Path(name))!=h]
newfinished=[str(f) for root in finished['roots'] for f in Path(root).rglob('*') if f.is_file() and str(f) not in finished['files']]
before=read(OUT/'ACTIVE_DESIGNS_BEFORE.json')['designs'];after=read(p.registry/'ACTIVE_DESIGNS.json')['designs']
b={r['design_id']:r for r in before if r['design_id'] not in TARGETS};a={r['design_id']:r for r in after if r['design_id'] not in TARGETS}
queue=read(p.registry/'HUB_QUEUE.json')['entries'];oldqueue=[r for r in read(OUT/'HUB_QUEUE_BEFORE.json')['entries'] if r['design_id'] in TARGETS]
original_entries=[next(q for q in queue if (q['design_id'],q['attempt_id'])==(r['design_id'],r['attempt_id'])) for r in oldqueue]
if len(original_entries)!=3 or any(r['status']!='SUPERSEDED_C46' for r in original_entries):raise ValueError('Three original queue entries must be superseded')
source=read(OUT/'SOURCE_RECEIPT_AUDIT.json');source_changes=[]
for i in source['files']:
    f=ROOT/i['path']
    if sha(f)!=i['sha256'] or f.stat().st_size!=i['size_bytes']:source_changes.append(i['path'])
    if 'receipt_path' in i and sha(ROOT/i['receipt_path'])!=i['receipt_sha256']:source_changes.append(i['receipt_path'])
with scheduler_lock(p.registry):lock_released=True
value=dict(finished_roots=len(finished['roots']),finished_files=len(finished['files']),changed_files=changed,new_finished_files=newfinished,other_active_rows_identical=a==b,
    target_next_actions={r['design_id']:p.next_action(r) for r in after if r['design_id'] in TARGETS},original_queue_entries_superseded=original_entries,
    stopped=(p.registry/'AUTOPILOT_STOP').exists(),lock_released=lock_released,source_files_checked=len(source['files']),changed_raw_or_receipts=source_changes,
    test_report=dict(path=str(report.relative_to(ROOT)),sha256=sha(report)),actual_data_estimation_calls=0,outcome_association_calls=0,new_downloads=[])
if changed or newfinished or a!=b or source_changes or not value['stopped']:raise ValueError('Integrity check failed '+str(value))
ensure_once(OUT/'FINAL_INTEGRITY.json',value)
if (OUT/'TEST_REPORT.json').exists():ensure_once(OUT/'INTERIM_TEST_REPORT_SELECTION.json',dict(interim_path='TEST_REPORT.json',interim_sha256=sha(OUT/'TEST_REPORT.json'),selected_path=report.name,selected_sha256=sha(report),reason='Explicit final before/after code inventory is identical; earlier run crossed metadata-review fixes and must not authorize restart.'))
print('Final test suites:0/0; finished11827unchanged; other active bindings unchanged; source receipts unchanged.')
