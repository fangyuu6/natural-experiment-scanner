"""SUPPORT-derived series-break annotations; never infer by design names."""
import csv
import json
from pathlib import Path
from .storage import ensure_once, sha

AUTH = 'docs/plans/l2-pipeline/HUB_RULING_C65_DRAFT_SHOULDS.md'
CLOSURES = 'docs/plans/l2-pipeline/HUB_CLOSURES_20261011.md'
NAME = 'C65_EXPORT_ANNOTATIONS.json'
PANEL_NAME = 'C65_PANEL_METADATA.json'


def annotations(root):
    root=Path(root)
    from .hygiene import inventory_names
    support_name,_=inventory_names(root)
    if not (root/support_name).is_file():return None
    from .class_table import break_mass
    support=json.loads((root/support_name).read_text())
    primary=next((v for v in support.get('variants',[]) if v.get('variant')=='primary'),{})
    breaks={}
    for path in sorted((root/'panels').glob('*/metadata.csv')):
        with path.open() as stream:
            for row in csv.DictReader(stream):
                if row.get('series_break'):
                    year=int(row['year']);breaks.setdefault(year,set()).add(row['series_break'])
    entries=[]
    for year,labels in sorted(breaks.items()):
        mass=break_mass(primary,year)
        entries.append(dict(years=[year],series_break=sorted(labels),support_mass=mass,
            treated_cell_mass=mass['post']['crossing_mass'],total_treated_cell_mass=mass['post']['total_mass'],
            mass_share=mass['post']['mass_share'],lead_mass_share=mass['lead']['mass_share'],
            report_only=True,source='SUPPORT.cell_inventory'))
    return dict(notes=[],series_break=entries) if entries else None


def retain(ctx):
    root=ctx.design_dir
    if any((root/n).exists() for n in ('FREEZE_PREP.json','FREEZE_FINAL.json','TERMINAL.json','EXECUTION_ONCE.json')): return None
    if (root/NAME).exists():return root/NAME
    value=annotations(root)
    if value is None:return None
    bindings=[];cells=[]
    for path in sorted((root/'panels').glob('*/metadata.csv')):
        bindings.append(dict(path=str(path.resolve()),sha256=sha(path)))
        with path.open() as stream:
            for row in csv.DictReader(stream):
                if row.get('series_break'):
                    cells.append(dict(variant=path.parent.name,unit=row['unit'],year=int(row['year']),series_break=row['series_break']))
    from .hygiene import inventory_names
    support_name,_=inventory_names(root)
    metadata=ensure_once(root/PANEL_NAME,dict(schema='l2_c65_panel_metadata_v2',
        support_sha256=sha(root/support_name),source_metadata=bindings,cell_flags=cells,
        role='additive_metadata_flags',availability_masks_unchanged=True,outcome_values_read=False))
    value.update(schema='l2_c65_export_annotations_v2',design_id=ctx.card.design_id,
        panel_metadata=dict(path=str(metadata),sha256=sha(metadata)),
        primary_specification_unchanged=True,diagnostic_gates_unchanged=True,outcome_associations_read=False)
    return ensure_once(root/NAME,value)
