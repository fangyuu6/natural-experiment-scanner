"""C63 explicit per-packet read staging, without modifying canonical jobs."""
import json, os
from pathlib import Path
from ..storage import sha,ensure_once,public_input

PAGEABLE_LINE_LIMIT=2000  # a Claude worker's Read tool cannot page inside one very long line

def stage(packet):
    packet=Path(packet);binding=json.loads((packet/'PACKET.json').read_text())
    manifest=packet/'READ_SCOPE.json'
    if manifest.exists():
        value=json.loads(manifest.read_text())
        for r in value['files']:
            if sha(packet/r['staged'])!=r['sha256']:raise ValueError('Staged read bytes changed')
        return value
    rows=[]
    for name in binding['allowed_read_paths']:
        original=Path(name)
        if original.is_symlink():raise ValueError('Read scope cannot include symlinks')
        source=public_input(original)
        if not source.exists():raise ValueError('Allowed read source missing')
        paths=sorted(source.rglob('*')) if source.is_dir() else [source]
        for p in paths:
            if p.is_symlink():raise ValueError('Read scope cannot include symlinks')
            if not p.is_file():continue
            p=public_input(p);rel='read_scope/'+str(len(rows))+'/'+p.name
            target=packet/rel;target.parent.mkdir(parents=True,exist_ok=True)
            data=p.read_bytes();form='verbatim'
            if p.suffix=='.json' and max(map(len,data.split(b'\n')),default=0)>PAGEABLE_LINE_LIMIT:
                # Canonical bytes (the ones the prompt hash-binds) stay untouched at `original`; the worker gets an
                # indented copy of the same JSON, and the manifest records both hashes.
                try:data=(json.dumps(json.loads(data),indent=1,sort_keys=True)+'\n').encode();form='pretty_json_v1'
                except ValueError:pass
            target.write_bytes(data);target.chmod(0o444)
            rows.append(dict(original=str(p),staged=rel,sha256=sha(target),source_sha256=sha(p),staged_form=form))
    value=dict(schema='l2_packet_read_scope_v1',files=rows,
        embedded_inputs=['PROMPT.md','OUTPUT_SCHEMA.json']+(['REVIEW_PACK.json'] if (packet/'REVIEW_PACK.json').exists() else []),
        enforcement='Allowlisted copies and hash-checked accessor; host filesystem sandbox still required to prevent arbitrary OS reads')
    with manifest.open('xb') as handle:  # exclusive, indented so the worker can page it
        handle.write((json.dumps(value,indent=1,sort_keys=True)+'\n').encode());handle.flush();os.fsync(handle.fileno())
    return value


def read_allowed(packet,relative):
    packet=Path(packet).resolve();scope=stage(packet)
    row=next((r for r in scope['files'] if r['staged']==relative),None)
    if row is None:raise ValueError('Read outside staged packet scope')
    path=(packet/relative).resolve()
    if not path.is_relative_to(packet/'read_scope') or sha(path)!=row['sha256']:raise ValueError('Staged read binding differs')
    return path.read_bytes()


def persona(binding):
    meta=json.loads(Path(binding['job_path']).with_suffix('.job.json').read_text())
    return (meta['design_id'],meta['role']) if meta['stage']=='S5' or meta.get('contract',{}).get('jobs_kind')=='s0_purpose_direction_c83' else None


def authors(root):
    """Include historical and expired leases; claim reservations survive expiry."""
    root=Path(root);path=root/'FORECAST_AUTHORS.json'
    value=json.loads(path.read_text()) if path.exists() else {'claims':[]}
    seen={(r['worker'],r['design_id'],r['persona']) for r in value['claims']}
    for state in ('pending','in_progress','done','hub'):
        for packet in (root/state).glob('*'):
            if not (packet/'PACKET.json').exists():continue
            key=persona(json.loads((packet/'PACKET.json').read_text()))
            if not key:continue
            leases=[packet/'LEASE.json',*packet.glob('EXPIRED_*.json')]
            for p in leases:
                if not p.exists():continue
                lease=json.loads(p.read_text());lease=lease.get('lease',lease)
                k=(lease['worker'],*key)
                if k not in seen:
                    # NAMED_PANEL Q3: `base` is the name the worker asked for (claim() may append a suffix to a repeated name).
                    base=lease.get('requested',lease['worker'])
                    value['claims'].append(dict(worker=k[0],design_id=k[1],persona=k[2],**({'base':base} if base!=k[0] else {})));seen.add(k)
    return value


def can_claim(history,worker,key,requested=None):
    # One session = one forecast persona per design. A repeated worker name gets a suffix from claim(), so the
    # rule is also checked on the name the worker asked for (`base`), exactly as strongly as before the suffix existed.
    names={worker,requested} - {None}
    return key is None or not any((r['worker'] in names or r.get('base',r['worker']) in names)
                                  and r['design_id']==key[0] and r['persona']!=key[1] for r in history['claims'])
