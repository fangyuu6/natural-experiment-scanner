#!/usr/bin/env python3
"""Export readable, tidy stage/domain counts from the immutable queue summary."""
import argparse
import csv
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--directory',type=Path,default=ROOT/'data/pipeline/queue_v2')
    args=ap.parse_args()
    summary=json.loads((args.directory/'summary.json').read_text())
    with (args.directory/'counts_by_domain.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['track','stage','domain','count'])
        for track,stages in summary['counts'].items():
            for stage,values in stages.items():
                w.writerow([track,stage,'__all__',values['total']])
                for domain,count in sorted(values['by_domain'].items()):w.writerow([track,stage,domain,count])
    print(args.directory/'counts_by_domain.csv')

if __name__=='__main__':main()
