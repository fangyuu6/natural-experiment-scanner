"""Read-only intake packet and source geometry checks; no outcomes or fits."""
from pathlib import Path
import sys,json,collections
from scripts.l2_pipeline.intake.config import IntakeConfig
from scripts.l2_pipeline.intake import v8b_calendar
from scripts.l2_pipeline.intake.fresh_gate import fresh_gate
ROOT = None
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.connectors.common import ConnectorRequest
from scripts.l2_pipeline.connectors.fars import _contract

def require(condition,message):
    if not condition:raise ValueError(message)

def main(config):
    global ROOT
    ROOT = config.root
    v8b_calendar.configure(config)
    v8b_calendar.install()
    data=json.loads((ROOT/'INTAKE.json').read_text());rows=data['rows']
    require(len(rows)==config.expected_variables and len({r['policy_id'] for r in rows})==config.expected_variables,'Catalog denominator/identity')
    emitted=[r for r in rows if r['emit_eligible']]
    require(len(emitted)==config.expected_emitted,'Expected distinct drafts')
    counts=collections.Counter(r['mapping']['family'] for r in emitted)
    require(max(counts.values())<=config.family_cap,'Family cap')
    report=[]
    for r in emitted:
        did=r['design_id'];g=r['geometry'];plan=r['sample_plan'];m=r['mapping']
        require(g['treated_n']>=6 and g['supported_cohorts_n']>=2 and g['min_cohort_controls']>=5,'Supported geometry')
        for unit in g['treated_states']:
            state=next(s for s in g['state_counts'] if s['unit']==unit)
            require(state['pre_n']>=3 and state['post_n']>=2 and not state['source_excluded'],'Retained periods')
        require(len(plan['calibration'])==8 and len(plan['fresh_audit'])==10,'Fixed sample size')
        require(not set(plan['calibration'])&set(plan['fresh_audit']),'Disjoint calibration/audit')
        require(not set(plan['calibration'])&set(plan['previously_audited']),'Calibration reused an earlier sample')
        require(plan['calibration_seed']==config.calibration_seed and plan['audit_seed']==config.audit_seed,'Own fixed seeds')
        cp=ROOT/'cards'/(did+'.json');card=json.loads(cp.read_text());outcome=card['outcome']
        require(outcome['outcome_scale']=='rate' and card['transition_year_drop'] is True,'Prospective rate/drop defaults')
        require(card['execution_authorized'] is False and card['first_order']['evidence_status']=='pending','No purpose/readiness promotion')
        s0=load_job(ROOT/'s0_jobs'/'S0'/did/'purpose_verifier.md')
        require(s0.contract['purpose_rule']=='V6.2','S0 template version')
        for kind in ('calibration','fresh_audit'):
            j=load_job(ROOT/(kind+'_jobs')/'S1'/did/('auditor.'+ROOT.name+'.md'))
            require(Path(j.output_dir).is_relative_to(ROOT),'Output escaped intake')
            require(j.contract['audit_target']=='official_operational_calendar_year','C11 operational clock')
            require(j.contract['no_s1_promotion'] is True,'Audit promotes S1')
        if m['connector']=='cps_voting':
            require(outcome['frequency']=='biennial' and outcome['population_filters']==
                    {'civilian_noninstitutional':True,'citizens':True,'age_min':18},'Voting connector exact contract')
            require(all(y%2==0 for y in g['outcome_retained_years']),'Voting invented odd years')
        if m['connector']=='fars_state':
            # Invoke the actual adapter's metadata contract; no source data open.
            request=ConnectorRequest.from_card(card,config.repo)
            _contract(request)
        report.append(dict(policy_id=r['policy_id'],treated_n=g['treated_n'],min_controls=g['min_cohort_controls'],status='PASS'))
    try:fresh_gate(ROOT, validate_audit_clock=v8b_calendar.validate_audit_clock)
    except FileNotFoundError:gate='BLOCKED_PENDING_CALIBRATION'
    else:raise ValueError('Fresh launch was permitted before confirmation')
    print(json.dumps(dict(status='PASS',rows=report,fresh_launch_gate=gate,model_calls=0,outcome_values_read=0),indent=2))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intake-root', type=Path, required=True)
    args = parser.parse_args()
    main(IntakeConfig.from_existing(args.intake_root))
