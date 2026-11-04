"""Compare real Windows scheduling with the saved old output driver.

In-memory cursor only. A constant-speed synthetic target is sampled at 20/30 Hz;
the actual driver thread runs normally. This measures output, not hand tracking.
"""
import importlib.util
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.cursor_motion import CursorMotion

ROOT=Path(__file__).resolve().parents[1]


class Backend:
    def __init__(self):self.position=(0,0);self.writes=[]
    def cursor(self):return self.position
    def escape_pressed(self):return False
    def move(self,point):self.position=tuple(point);self.writes.append((time.perf_counter(),self.position))
    def release_left(self):pass


def measure(driver,fps):
    backend=Backend();motion=driver(backend);start=time.perf_counter();samples=[];next_frame=0;frames=0
    try:
        for tick in range(240):
            deadline=start+tick/120
            time.sleep(max(0,deadline-time.perf_counter()))
            elapsed=time.perf_counter()-start
            if elapsed>=next_frame:
                motion.submit((round(900*elapsed),100));next_frame+=1/fps;frames+=1
            if elapsed>.3:samples.append((elapsed,backend.position[0]))
        time.sleep(.07)
    finally:motion.close()
    steps=[b[1]-a[1] for a,b in zip(samples,samples[1:])]
    writes=[w for w in backend.writes if w[0]-start>.3]
    intervals=[(b[0]-a[0])*1000 for a,b in zip(writes,writes[1:])]
    speeds=[(b[1][0]-a[1][0])/(b[0]-a[0]) for a,b in zip(writes,writes[1:]) if b[0]>a[0]]
    return dict(camera_fps=fps,frames=frames,moves=len(backend.writes),
                moves_per_frame=round(len(backend.writes)/frames,2),
                median_output_interval_ms=round(statistics.median(intervals),2),
                p95_output_interval_ms=round(sorted(intervals)[int((len(intervals)-1)*.95)],2),
                output_speed_variation=round(statistics.pstdev(speeds)/max(1,statistics.mean(speeds)),3),
                stalled_120hz_samples_pct=round(sum(v==0 for v in steps)/len(steps)*100,1),
                step_variation=round(statistics.pstdev(steps)/statistics.mean(steps),3),
                mean_output_lag_ms=round(statistics.mean(t-x/900 for t,x in samples)*1000,1))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',help='Optional older cursor_motion.py to compare against')
    parser.add_argument('--report',default='.runtime/motion-cadence-comparison.json')
    args=parser.parse_args()
    old=None
    if args.baseline:
        spec=importlib.util.spec_from_file_location('old_motion',ROOT/args.baseline)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    results=[]
    for fps in (20,30):
        row={}
        if old:row['before']=measure(old.CursorMotion,fps)
        row['after']=measure(CursorMotion,fps)
        results.append(row)
    report=dict(scope='Synthetic trajectory, in-memory backend, actual Windows threads. Not camera accuracy.',results=results)
    path=ROOT/args.report;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
