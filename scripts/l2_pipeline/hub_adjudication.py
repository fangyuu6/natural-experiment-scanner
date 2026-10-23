"""Explicit, hash-bound hub adjudications; original coder packets stay intact."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from .storage import sha,public_input


def load(repo,design_id,predicate):
    path=Path(repo)/'data/l2_pipeline/autopilot'/design_id/'manual_evidence/HUB_ADJUDICATION.json'
    if not path.exists():return None
    value=json.loads(path.read_text());authority=value['authority']
    source=public_input(Path(repo)/authority['path'])
    if (value.get('schema')!='l2_hub_adjudication_v1' or value.get('design_id')!=design_id
        or value.get('predicate')!=predicate or sha(source)!=authority['sha256']
        or value.get('execution_authorized') is not False):raise ValueError('Hub adjudication identity changed')
    value['binding']=dict(path=str(path.resolve()),sha256=sha(path))
    for state,decision in value['states'].items():
        if decision['decision']!='include':raise ValueError('Unsupported hub adjudication')
        from datetime import date
        date.fromisoformat(decision['effective_date'])
    return value


def effective_context(ctx):
    value=load(ctx.repo,ctx.card.design_id,ctx.card.payload['mechanism'])
    if value is None:return ctx
    if (ctx.design_dir/'FREEZE_PREP.json').exists() or (ctx.design_dir/'FREEZE_FINAL.json').exists():
        retained=json.loads((ctx.design_dir/'SCIENTIFIC_CARD.json').read_text())
        if retained.get('automation',{}).get('hub_adjudication')!=value['binding']:
            raise ValueError('Hub adjudication requires a new unfrozen version')
    payload=deepcopy(dict(ctx.card.payload))
    for registered in value['sensitivities']:
        item=deepcopy(registered)
        treatment=item.get('changes',{}).get('treatment',{})
        if 'excluded_units' in treatment:
            treatment['excluded_units']=sorted(set(treatment['excluded_units']) |
                set(payload['treatment'].get('excluded_units',[])))
        existing=next((s for s in payload['sensitivities'] if s['name']==item['name']),None)
        if existing is not None and existing not in (item,registered):raise ValueError('Hub sensitivity differs from existing declaration')
        if existing is None:payload['sensitivities'].append(item)
        else:payload['sensitivities'][payload['sensitivities'].index(existing)]=item
    payload.setdefault('automation',{})['hub_adjudication']=value['binding']
    return replace(ctx,card=replace(ctx.card,payload=payload))
