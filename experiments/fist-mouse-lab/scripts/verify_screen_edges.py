"""Verify full desktop corner pixels with movement only; no clicks or keys.

The camera controller must be stopped before running. Restore the original
cursor even on failure. This proves coordinate/output coverage, not vision.
"""
import ctypes
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.native import NativeMouse
from src.fist_engine import FistEngine,Features
from src.absolute_pointer import AbsolutePointer
from src.worker import Worker


def main():
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError,OSError):pass
    root=Path(__file__).resolve().parents[1]
    settings=Worker().settings
    mouse=NativeMouse();original=mouse.cursor();bounds=mouse.bounds();checks=[];errors=[]
    area=AbsolutePointer.area(settings['area_scale'],settings.get('area_base'))
    try:
        for label,u,v in [('top_left',0,0),('top_right',1,0),('bottom_right',1,1),('bottom_left',0,1),('center',.5,.5)]:
            if mouse.escape_pressed():raise RuntimeError('Cancelled with Escape')
            anchor=(area[0]+u*(area[2]-area[0]),(area[1]+v*(area[3]-area[1]))*9/16)
            f=Features(anchor,.12,(.65,.43,.4,.35),.5,9/16)
            engine=FistEngine(**settings,double_time=mouse.double_time)
            cursor=mouse.cursor()
            for i in range(8):
                result=engine.step(f,1+i/30,cursor,bounds);cursor=result['cursor']
                if result['clicks'] or result['wheel']:raise AssertionError('Unexpected command in movement-only fixture')
            expected=(round(bounds[0]+u*(bounds[2]-1)),round(bounds[1]+v*(bounds[3]-1)))
            start=time.perf_counter();mouse.move(cursor);elapsed=(time.perf_counter()-start)*1000
            actual=mouse.cursor()
            checks.append(dict(corner=label,expected=expected,actual=actual,output_ms=round(elapsed,3)))
            # An even-sized screen has a half-pixel mathematical center;
            # either adjacent pixel is valid there. Corners must remain exact.
            tolerance=1 if label=='center' else 0
            if any(abs(a-b)>tolerance for a,b in zip(actual,expected)):
                raise AssertionError(f'{label}: {actual} != {expected}')
            time.sleep(.12)
    except Exception as error:errors.append(str(error))
    finally:mouse.move(original)
    report=dict(passed=not errors and len(checks)==5,area=area,bounds=bounds,checks=checks,errors=errors,
                scope='Full desktop pixels, movement only. Synthetic gesture input; no camera accuracy claim.')
    (root/'.runtime').mkdir(exist_ok=True)
    (root/'.runtime/screen-edge-test.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
