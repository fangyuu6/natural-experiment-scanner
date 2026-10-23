"""Merged A/B reader records: T241 files plus T245 files (anchor ids from T241 kept)."""
import glob,json,os
from collate import OUT,load as _load
def load(r):
    rec=dict(_load(r))
    for f in sorted(glob.glob('%s/T245/coded/reader%s_chunk*.json'%(OUT,r))):
        for x in json.load(open(f))['codings']:rec.setdefault(x['packet_id'],x)
    return rec
