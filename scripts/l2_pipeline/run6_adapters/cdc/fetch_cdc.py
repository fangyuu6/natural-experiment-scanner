from pathlib import Path
import sys,json,csv,urllib.parse,concurrent.futures,traceback
from scripts.l2_pipeline.policy_tracker_sources import CDC,CDC_LICENSE,download_public
from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT / 'cdc'

def fetch(item):
 tid,view=item; out=ROOT/'raw'/tid; out.mkdir(parents=True,exist_ok=True); receipts=[]
 try:
  def get(url,path):
   r=download_public(url,path,CDC_LICENSE);receipts.append(r);return r
  get('https://data.cdc.gov/api/views/'+view+'.json',out/'metadata.json')
  base='https://data.cdc.gov/resource/'+view
  counturl=base+'.json?'+urllib.parse.urlencode({'$select':'count(*) as n'})
  get(counturl,out/'count_before.json');expected=int(json.loads((out/'count_before.json').read_text())[0]['n'])
  pages=[];n=0
  for offset in range(0,expected,50000):
   url=base+'.csv?'+urllib.parse.urlencode({'$limit':50000,'$offset':offset,'$order':'year,quarter,locationabbr,measureid,provisionid'})
   target=out/('page_%06d.csv'%offset);get(url,target);pages.append(target)
   with target.open(newline='',encoding='utf-8-sig') as f:n+=sum(1 for _ in csv.DictReader(f))
  get(counturl,out/'count_after.json');after=int(json.loads((out/'count_after.json').read_text())[0]['n'])
  if n!=expected or after!=expected: raise ValueError('count mismatch '+str((n,expected,after)))
  combined=out/'history.csv'
  with combined.open('w',newline='') as f:
   writer=None
   for page in pages:
    with page.open(newline='',encoding='utf-8-sig') as g:
     reader=csv.DictReader(g)
     if writer is None: writer=csv.DictWriter(f,fieldnames=reader.fieldnames);writer.writeheader()
     writer.writerows(reader)
  result={'tracker_id':tid,'status':'downloaded','verified_rows':n,'count_before':expected,'count_after':after,'paged_downloads':len(pages),'source_url':base+'.csv','combined_path':str(combined),'downloads':receipts}
 except Exception as e: result={'tracker_id':tid,'status':'stopped','error':str(e),'downloads':receipts}
 (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(tid,result['status'],result.get('verified_rows'),result.get('error'),flush=True)
 return result
def main(argv=None):
 import argparse
 global ROOT
 parser=argparse.ArgumentParser(description='Explicit public CDC metadata download; never called by replay/import')
 parser.add_argument('--data-root',type=Path,default=ROOT)
 parser.add_argument('--max-concurrency',type=int,choices=range(1,5),default=3)
 args=parser.parse_args(argv)
 ROOT=args.data_root.resolve()
 items=[(k,v) for k,v in CDC.items() if k!='cdc_tobacco_tax']
 with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_concurrency) as pool: result=list(pool.map(fetch,items))
 (ROOT/'FETCH_REPORT.json').write_text(json.dumps(result,indent=2)+'\n')
 (ROOT/'DOWNLOADS.json').write_text(json.dumps([r for d in result for r in d['downloads']],indent=2)+'\n')
 return int(any(row['status']=='stopped' for row in result))

if __name__=='__main__':
 raise SystemExit(main())
