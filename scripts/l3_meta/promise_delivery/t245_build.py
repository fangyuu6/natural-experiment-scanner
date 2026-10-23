"""T245: packets for pin designs not coded in T241 (same protocol) + the 30 anchors again. Usage: t245_build.py"""
import json,os,random,sys,hashlib
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from packets import *
from anchors import ANCHORS
PIN=os.path.join(ROOT,'data/l3_meta/refresh/20261022_r1/live/l2_set_live.json')
T=OUT+'/T245'
live=json.load(open(PIN));cards=card_index()
coded={r['design_id'] for r in json.load(open(OUT+'/CODED_FINAL.json'))['rows'] if not r['is_anchor']}
unc=[d for d in live['designs'] if d['design_id'] not in coded]
items=[];keymap={}
for d in unc:
    p,c=design_packet(d,cards);items.append(p);keymap[p['packet_id']]=d['design_id']
for i,a in enumerate(ANCHORS):
    p=dict(packet_id=pid('ANCHOR-%02d'%i),policy_name=a['policy_name'],policy_family=a['policy_family'],outcome_label=a['outcome_label'],outcome_unit=a['outcome_unit'],treatment_definition=a['treatment_definition'],enactment=[],statute_excerpts=[])
    items.append(validate(p))
random.Random(20261022245).shuffle(items)
for c in range(2):
    path=T+'/packets/chunk%d.json'%(c+1)
    json.dump(dict(chunk=c+1,items=items[c::2]),open(path,'x'),indent=1,ensure_ascii=False)
json.dump(dict(design_ids=keymap,pin=PIN,pin_sha256=hashlib.sha256(open(PIN,'rb').read()).hexdigest(),uncoded=len(unc),uncoded_estimated=sum(d['status']=='estimated' for d in unc),coded_not_in_pin=len(coded-{d['design_id'] for d in live['designs']})),open(T+'/PACKET_KEY_T245.json','x'),indent=1)
print(len(unc),sum(d['status']=='estimated' for d in unc),len(items))
