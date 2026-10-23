"""Additive authorship annotations. Never read forecast numeric fields/results."""
import os,hashlib
from pathlib import Path
from ..autopilot import read,atomic
from ..storage import ensure_once,sha


def author_index(repo):
    index={};queue=Path(repo)/'data/l2_pipeline/registry/SONNET_QUEUE'
    for p in (queue/'done').glob('*/PACKET.json'):
        binding=read(p);lease=read(p.parent/'LEASE.json',{})
        if lease.get('worker'):index[str(Path(binding['job_path']).resolve())]=lease['worker']
    return index


def annotation(root,index):
    root=Path(root);lock=root/'PREDICTIONS_LOCK.json';value=read(lock);authors={}
    for persona in value['personas']:
        collection=read(root/'predictions'/persona/'COLLECTION.json',{})
        job=collection.get('job_path')
        slot=value.get('forecast_slots',{}).get(persona,{})
        authors[persona]=slot.get('author_provenance',{}).get('worker_id') or (index.get(str(Path(job).resolve())) if job else None)
    shared={w:sorted(p for p,a in authors.items() if a==w) for w in set(authors.values()) if w and list(authors.values()).count(w)>1}
    return dict(schema='l2_forecast_authors_c63_v1',design_id=value['design_id'],version=root.name,
        forecast_lock=dict(path=str(lock.resolve()),sha256=sha(lock)),authors=authors,
        forecast_slots=value.get('forecast_slots',{}),n_eff=value.get('n_eff'),
        SINGLE_CONSENSUS=value.get('SINGLE_CONSENSUS',False),n10_eligible=value.get('n10_eligible',True),
        author_provenance_complete=all(authors.values()),shared_author_personas=bool(shared),shared_workers=shared,
        forecasts_stand=True,l3_q3_include_and_exclude=True,original_forecasts_modified=False)


def retain(ctx):
    if (ctx.design_dir/'FREEZE_PREP.json').exists():return
    value=annotation(ctx.design_dir,author_index(ctx.repo))
    ensure_once(ctx.design_dir/'FORECAST_AUTHOR_ANNOTATION.json',value)


def audit(repo,output):
    repo=Path(repo);output=Path(output);index=author_index(repo);sets=[]
    for directory,dirs,files in os.walk(repo/'data/l2_pipeline'):
        dirs[:]=[d for d in dirs if d not in {'code_snapshots','forecast_snapshots','raw','devcopy','c63','rehearsal'}]
        if 'PREDICTIONS_LOCK.json' not in files:continue
        value=annotation(Path(directory),index)
        path=ensure_once(output/(value['design_id']+'__'+value['version']+'__'+value['forecast_lock']['sha256'][:12]+'_'+hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:10]+'.json'),value)
        sets.append(dict(value,annotation=dict(path=str(path.resolve()),sha256=sha(path))))
    registry=repo/'data/l2_pipeline/registry/FORECAST_SET_ANNOTATIONS.json'
    old=read(registry,{'schema':'l2_forecast_set_annotations_v1','sets':[]})
    # Keep every old C57 entry verbatim; distinct authorship annotations append.
    for value in sets:
        if value not in old['sets']:old['sets'].append(value)
    old.update(forecasts_stand=True,l3_q3_include_and_exclude=True)
    atomic(registry,old)
    ensure_once(output/'AUDIT.json',dict(locked_sets=len(sets),shared_author_sets=sum(s['shared_author_personas'] for s in sets),sets=sets))
    return sets


def q3_views(rows,annotations):
    """L3 Q3 consumes both views; no score/association is calculated here."""
    tagged=[]
    for row in rows:
        matches=[a for a in annotations if (a['design_id'],a.get('version'))==(row['design_id'],row.get('spec_version',row.get('version')))]
        shared=any(a.get('shared_author_personas') is True for a in matches)
        stale=any(a.get('stale_contract_card') is True for a in matches)
        tagged.append(dict(row,shared_author_personas=shared,stale_contract_card=stale))
    return dict(include_all=tagged,exclude_shared_author=[r for r in tagged if not r['shared_author_personas']],
        exclude_flagged=[r for r in tagged if not r['shared_author_personas'] and not r['stale_contract_card']])
