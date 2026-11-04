"""Replay a local numeric trace. Measures continuity, NOT recognition accuracy."""
from collections import Counter
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.observation import Observation
from engine.controller import Controller

data=json.loads((ROOT/'.runtime/recorded-before-rebuild.json').read_text(encoding='utf-8-sig'))
c=Controller(data.get('settings'));shown=(.5,.5);modes=Counter();actions=Counter();old_steps=[];steps=[];rows=[]
previous_old=None
for row in data['samples']:
    f=Observation.from_record(row['features']) if row.get('features') else None
    out=c.step(f,row['at'],shown,valid=row.get('age_ms',0)<180)
    a=out['position'];steps.append(math.hypot((a[0]-shown[0])*1920,(a[1]-shown[1])*1080));shown=a
    old=row['result']['cursor']
    if previous_old:old_steps.append(math.dist(old,previous_old))
    previous_old=old
    modes[out['state']]+=1
    actions.update(a['type'] for a in out['actions'])
    rows.append({'at':row['at'],'state':out['state'],'position':shown,'actions':out['actions'],'reason':out['reason']})
report=dict(frames=len(rows),old_max_frame_displacement_px=round(max(old_steps),1),
            new_max_frame_displacement_px=round(max(steps),1),old_over_300px=sum(v>300 for v in old_steps),
            new_over_300px=sum(v>300 for v in steps),states=dict(modes),actions=dict(actions),
            note='Same numeric observations; this measures continuity, not intended-action accuracy. No images used.')
report['valid_frames']=sum(bool(row.get('features')) for row in data['samples'])
report['sufficient_hand_data']=report['valid_frames']>=100
if not report['sufficient_hand_data']:
    report['note']='Insufficient hand data in this automatically overwritten trace. These numbers do not establish improvement. Coordinate failure cases are preserved separately as targeted regression tests.'
(ROOT/'.runtime/replay-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
(ROOT/'.runtime/replayed.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
