"""Opaque state/year copying: masks and outcome tails remain separate contracts."""
from __future__ import annotations
import csv, json
from pathlib import Path
from .storage import public_input, sha, ensure_once


def opaque_rows(path: Path) -> tuple[bytes, dict[tuple[str,int],bytes]]:
    """Parse only the two key columns; preserve every other byte, including NaN."""
    with public_input(path).open('rb') as handle:
        header=next(handle)
        if header.rstrip().split(b',')[:2]!=[b'unit',b'year']: raise ValueError('Opaque panel needs unit/year prefix')
        result={}
        for line in handle:
            prefix=line.split(b',',2)
            if len(prefix)!=3: raise ValueError('Invalid opaque panel row prefix')
            key=(prefix[0].decode('ascii'),int(prefix[1]))
            if key in result: raise ValueError('Duplicate opaque panel key')
            result[key]=line
    return header,result


def build_opaque_panel(card: dict, base: Path, output: Path) -> dict:
    cfg=card['outcome'];source=public_input(base/cfg['paths'][0]);mask=public_input(base/cfg['mask_path'])
    if sha(source)!=cfg['source_sha256'] or sha(mask)!=cfg['mask_sha256']: raise ValueError('Opaque input binding changed')
    raw=json.loads(mask.read_text());keys={(r[0],r[1]) for r in raw}
    if len(keys)!=len(raw) or any(type(y) is not int for _,y in keys): raise ValueError('Invalid opaque mask')
    header,rows=opaque_rows(source)
    if not keys<=rows.keys(): raise ValueError('Opaque mask includes absent source rows')
    output.mkdir(parents=True,exist_ok=True)
    with (output/'panel.csv').open('xb') as handle: handle.write(header+b''.join(rows[k] for k in sorted(keys)))
    with (output/'metadata.csv').open('x',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['unit','year']);writer.writerows(sorted(keys))
    report={'design_id':card['design_id'],'connector':'opaque_state_year','frequency':'annual',
        'cells':len(keys),'available_cells':len(keys),'panel_path':str(output/'panel.csv'),
        'panel_sha256':sha(output/'panel.csv'),'metadata_path':str(output/'metadata.csv'),
        'metadata_sha256':sha(output/'metadata.csv'),
        'source_files':[{'path':str(p),'sha256':sha(p),'size_bytes':p.stat().st_size} for p in (source,mask)],
        'construction':{'mask_declared_before_copy':True,'row_bytes_preserved':True,'outcome_values_parsed':0},
        'outcome_association_calls':0,'treatment_inputs_read':False,'new_downloads':0,'state':'constructed',
        'limitations':['Preconstructed public-panel reuse; no new source aggregation. Empty unsupported masks retained.']}
    ensure_once(output/'CONSTRUCTION.json',report)
    return report
