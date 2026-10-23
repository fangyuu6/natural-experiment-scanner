"""C79 prospective, metadata-only sensitivity registration; primary is unchanged."""
import copy
import json
from collections import Counter
from pathlib import Path

RULE='C79_support_cells_operator_v1'


def cell_signature(row):
    cells=row.get('cell_inventory')
    if not cells:return None
    # Every geometry field participates, including controls, base and post/lead.
    geometry=[{k:(sorted(v) if k in {'treated','controls'} else v) for k,v in c.items()} for c in cells]
    return tuple(sorted(json.dumps(c,sort_keys=True,separators=(',',':')) for c in geometry))


def treated_mass(row):
    counts=Counter()
    for cell in row.get('cell_inventory',[]):
        if cell.get('post') is True:
            counts.update(cell.get('treated',[]))
    total=sum(counts.values())
    return {u:n/total for u,n in counts.items()} if total else {}


def operator(card):
    # Normalize the same declared measurement scales used by variant_card.
    from .connectors.rates import legacy_card,prospective_card
    if not legacy_card(card):card=prospective_card(card)
    # Geometry is independently compared; operator includes all analysis knobs.
    return dict(analysis={k:v for k,v in card['analysis'].items() if k not in
            {'full_year_variant','excluded_periods','screen_expected_support'}},
        outcome={k:v for k,v in card.get('outcome',{}).items() if k not in {'years','period_years','units'}},
        population=card.get('population'),outcome_scale=card.get('outcome_scale'))


SMO_RULE='T3_FORWARD_20261022'
SMO_REASON=('start_minus_one cannot be estimated under the transition-year drop (its first post year is the dropped year); not registered '
            '(HUB_RULING_20261019_NOVEL_GATE T3 forward rule, HUB_RULING_20261022_DRAFT_STOP)')


def unsupported_smo(card,row,primary):
    """T3 forward rule (5-0): the shifted-start check is dropped when the card drops the transition year, the primary is supported and the check
    fails ONLY on unsupported post cells (the exact T3(a) predicate of the NOVEL_GATE ruling)."""
    return (card.get('transition_year_drop') is True and row.get('passed',row.get('support_pass')) is False
            and set(row.get('issues',{}))=={'unsupported_post_cells'} and primary.get('passed',primary.get('support_pass')) is True)


def plan(card,support,smo_rule=False):
    from .cards import variant_card
    rows={r['variant']:r for r in support['variants']};primary=rows['primary']
    signature=cell_signature(primary);kept=[];dropped=[]
    for entry in card['sensitivities']:
        row=rows.get(entry['name'],{})
        if smo_rule and entry['name']=='start_minus_one' and unsupported_smo(card,row,primary):
            dropped.append(dict(name=entry['name'],reason=SMO_REASON))
        elif (signature is not None and row.get('passed',row.get('support_pass')) is True
                and cell_signature(row)==signature and operator(variant_card(card,entry.get('changes',{})))==operator(card)):
            dropped.append(dict(name=entry['name'],reason='identical to primary'))
        else:kept.append(copy.deepcopy(entry))
    shares=treated_mass(primary);added=[]
    for unit,share in sorted(shares.items(),key=lambda x:(-x[1],x[0])):
        if share<.1 or len(added)>=3:continue
        name=('omit_state_' if card['analysis'].get('support_adapter')=='annual_full_year_v1' else 'omit_')+unit
        if any(e['name']==name for e in kept):continue
        changes={'treatment':{'excluded_units':sorted(set(card['treatment'].get('excluded_units',[]))|{unit})}}
        if card['analysis'].get('support_adapter')=='annual_full_year_v1':
            declaration=copy.deepcopy(card['treatment']['full_year_declaration'])
            declaration['exclusions']=sorted(set(declaration.get('exclusions',[]))|{unit})
            changes['treatment']['full_year_declaration']=declaration
            changes['analysis']={'full_year_variant':name}
        entry=dict(name=name,changes=changes,report_only=True,gates_primary=False,
                   c79=dict(unit=unit,treated_post_mass_share=share,threshold=.1))
        kept.append(entry);added.append(name)
    notes=dict(rule=RULE,dropped=dropped,added=added,threshold=.1,cap=3,
                report_only=True,treated_post_mass_shares=shares)
    if smo_rule:notes['smo_rule']=SMO_RULE   # absent in every SPEC built before the rule: those replay with the old plan
    return notes,kept


def scientific_card(card,root):
    root=Path(root);path=root/'SPEC.json'
    if not path.exists():return card
    spec=json.loads(path.read_text())
    if 'c79_hygiene' not in spec:return card
    source=json.loads((root/'SUPPORT.json').read_text())
    notes,sensitivities=plan(card,source,smo_rule='smo_rule' in spec['c79_hygiene'])
    if notes!=spec['c79_hygiene'] or sensitivities!=spec['sensitivities']:
        raise ValueError('C79 scientific sensitivity registration differs')
    return dict(card,sensitivities=sensitivities)


def inventory_names(root):
    path=Path(root)/'SPEC.json'
    spec=json.loads(path.read_text()) if path.exists() else {}
    return ('SUPPORT_C79.json','PANELS_C79.json') if 'c79_hygiene' in spec else ('SUPPORT.json','PANELS.json')


def prepare_support(card,support,notes,sensitivities,treatment,root,base_dir=None):
    """Reuse primary outcome bytes; regenerate omitted-unit support from metadata."""
    from .cards import variant_card
    from .engine.panels import metadata_eligible_keys
    from .stages.s3_support import variant_treatment
    from .support import support_check,declared_annual_periods
    root=Path(root);panels=json.loads((root/'PANELS.json').read_text())
    removed={r['name'] for r in notes['dropped']}
    rows=[copy.deepcopy(r) for r in support['variants'] if r['variant'] not in removed]
    panel_rows=[copy.deepcopy(r) for r in panels['variants'] if r['variant'] not in removed]
    primary=next(r for r in panels['variants'] if r['variant']=='primary')
    metadata=root/'panels/primary/metadata.csv';keys=metadata_eligible_keys(metadata)
    for name in notes['added']:
        entry=next(s for s in sensitivities if s['name']==name);variant=variant_card(card,entry['changes'])
        records=variant_treatment(treatment['records'],variant)
        if card['analysis'].get('support_adapter')=='annual_full_year_v1':
            import csv
            from .full_year import construct_full_year
            from .storage import public_input
            source=public_input(Path(base_dir)/card['analysis']['support_metadata_path'])
            with source.open(newline='') as stream:meta=list(csv.DictReader(stream))
            declaration=copy.deepcopy(variant['treatment']['full_year_declaration'])
            from .support import minimum_post_periods
            declaration['min_post_periods']=minimum_post_periods(variant['analysis'])
            if variant.get('transition_year_drop'):declaration['transition_year_drop']=True
            summary,_=construct_full_year(declaration,
                variant['analysis']['full_year_variant'],meta,treatment['source_checks'])
            summary=dict(summary,passed=summary['support_pass'],state='SUPPORTED' if summary['support_pass'] else 'UNSUPPORTED')
        elif card['outcome'].get('frequency')=='biennial':
            from .biennial import support as biennial_support
            summary=biennial_support(keys,records,variant['analysis'],variant['outcome'],variant.get('transition_year_drop',False))
        else:
            summary=support_check(keys,records,variant['analysis'],period_years=declared_annual_periods(variant['outcome']),transition_year_drop=variant.get('transition_year_drop',False))
        if card['analysis'].get('support_adapter')=='annual_full_year_v1':
            summary.update({k:primary[k] for k in ('panel_path','panel_sha256','metadata_path','metadata_sha256')})
            summary['inventory_test_pass']=True
        rows.append(dict(summary,variant=name,report_only=True,gates_primary=False))
        panel_rows.append(dict(primary,variant=name,panel_relative='panels/primary/panel.csv',metadata_relative='panels/primary/metadata.csv'))
    return dict(support,variants=rows),dict(panels,variants=panel_rows)
