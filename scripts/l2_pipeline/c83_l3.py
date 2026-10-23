"""Additive pre-freeze F1/F6 registration; existing SPEC bytes stay unchanged."""
import copy,json
from pathlib import Path
from .storage import ensure_once,sha,public_input
SPEC_NAME='SPEC_ADDENDUM_C83.json'
SCHEMA='l2_f1_f6_c83_v1'

def geography(ctx):
    from .c64_metadata import geography as derive,COVARIATES_PATH
    treatment=ctx.design_dir/'TREATMENT.json'
    value=derive(ctx.card.payload,json.loads(treatment.read_text()) if treatment.exists() else None)
    value['risk_set_status']=value['status']
    source=public_input(ctx.repo/COVARIATES_PATH)
    if source.exists():
        manifest=json.loads(source.read_text())
        value['state_covariates']=dict(path=COVARIATES_PATH,sha256=sha(source),manifest=manifest)
        pointer=manifest.get('certified_covariate_file')
        if isinstance(pointer,dict) and manifest.get('status')=='CERTIFIED':
            path=public_input(ctx.repo/pointer['path'])
            required={'democratic_unified_control_jan1','citizen_ideology','ln_real_per_capita_personal_income','urban_share','land_border_adjacency'}
            if path.is_relative_to(ctx.repo) and sha(path)==pointer.get('sha256') and required<=set(pointer.get('fields',[])):
                value['state_covariates']['certified_file']=copy.deepcopy(pointer)
                value['covariates_status']='AVAILABLE'
            else:raise ValueError('Certified F6 covariate file binding/fields changed')
        else:
            value['covariates_status']='FIELD_MISSING';value['status']='FIELD_MISSING'
            value['reasons'].append('State covariate manifest has no certified joined file covering Klarner, Berry, BEA, Census urban share and land adjacency')
    else:
        value['covariates_status']='FIELD_MISSING';value['status']='FIELD_MISSING'
        value['reasons'].append('Hashed state covariate manifest missing')
    return value

def retain(ctx):
    from .c70_l3 import protected
    if protected(ctx.design_dir):return None
    path=ctx.design_dir/SPEC_NAME
    if path.exists():return path
    direction=ctx.design_dir/'PURPOSE_DIRECTION_C83.json'
    if not direction.exists():return None
    first=json.loads(direction.read_text())
    from .agents.purpose import validate_purpose_direction
    validate_purpose_direction(first)
    return ensure_once(path,dict(schema=SCHEMA,first_order=first,geography=geography(ctx),
        bindings=[dict(path=direction.name,sha256=sha(direction))],coded_at_stage='S0',
        field_status={'F1':'FIELD_MISSING' if first['purpose_direction'] is None else 'AVAILABLE','F6':geography(ctx)['status']},outcome_values_read=0,outcome_association_calls=0))

def overlay(registration,addition):
    value=copy.deepcopy(registration)
    if addition is None or value is None:return value
    if addition.get('schema')!=SCHEMA:raise ValueError('C83 addendum schema differs')
    from .agents.purpose import validate_purpose_direction
    validate_purpose_direction(addition['first_order'])
    value['first_order']=copy.deepcopy(addition['first_order'])
    value['purpose_direction_status']=('FIELD_MISSING' if value['first_order']['purpose_direction'] is None else
        'DIRECTION_UNDETERMINED' if value['first_order']['purpose_direction']==0 else 'EVAL')
    value['geography']=copy.deepcopy(addition['geography'])
    value['c83_field_status']=copy.deepcopy(addition['field_status'])
    return value

def frozen(root):
    root=Path(root)
    for name in ('FREEZE_FINAL.json','FREEZE_PREP.json'):
        path=root/name
        if not path.exists():continue
        binding=json.loads(path.read_text()).get('files',{}).get(SPEC_NAME)
        if binding is None:return None
        addition=root/SPEC_NAME
        if sha(addition)!=binding['sha256']:raise ValueError('Frozen C83 addendum changed')
        return json.loads(addition.read_text())
    return None
