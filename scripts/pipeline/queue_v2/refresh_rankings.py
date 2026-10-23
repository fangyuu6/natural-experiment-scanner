#!/usr/bin/env python3
"""Refresh compact views from queue files; no source ingestion or API calls."""
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('queue_build',Path(__file__).with_name('build.py'))
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)

def records(path):
    with path.open() as f:
        for line in f:yield json.loads(line)

def main():
    out=q.ROOT/'data/pipeline/queue_v2'
    did=sorted(records(out/'did_designs.jsonl'),key=q.ranking_key)
    compact=[]
    for rank,d in enumerate(did,1):
        compact.append({**{k:v for k,v in d.items() if k!='source_records'},'rank':rank,'representative_sources':d['source_records'][:8],'all_source_record_ids':[s['source_record_id'] for s in d['source_records']]})
    rdd={};rct={}
    for d in records(out/'rdd_candidates.jsonl'):
        fam=d['policy_family']
        if fam not in rdd or q.ranking_key(d)<q.ranking_key(rdd[fam]):rdd[fam]=d
    for d in records(out/'rct_mentions.jsonl'):
        if d['disposition']=='government_gambling_lottery':continue
        fam=d['policy_family']
        if fam not in rct or d['title_or_quoted_evidence_available']>rct[fam]['title_or_quoted_evidence_available']:rct[fam]=d
    result={'did_ranked':compact,'rdd_family_exemplars':sorted(rdd.values(),key=q.ranking_key),'rct_family_exemplars':list(rct.values())}
    (out/'research_candidates.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'did_families':len(did),'rdd_families':len(rdd),'rct_families':len(rct)}))

if __name__=='__main__':main()
