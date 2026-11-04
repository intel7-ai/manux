"""Bounded native integration check against this script's own window only."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.native import NativeMouse
from src.absolute_pointer import AbsolutePointer
from src.cursor_motion import CursorMotion


def run():
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError,OSError):pass
    import tkinter as tk
    mouse=NativeMouse();motion=CursorMotion(mouse);original=mouse.cursor();events=[];errors=[];done=False
    root=tk.Tk();root.title('MANUX Fist Mouse · native input check')
    root.geometry('660x420+50+50');root.attributes('-topmost',True)
    label=tk.Label(root,text='Testing only inside this window · Esc to stop',font=('Segoe UI',14));label.pack(pady=16)
    canvas=tk.Canvas(root,bg='#c8e0c0',highlightthickness=0);canvas.pack(fill='both',expand=True,padx=20,pady=20)
    for seq,kind in (('<ButtonRelease-1>','left'),('<Double-Button-1>','double'),
                     ('<ButtonRelease-3>','right'),('<MouseWheel>','wheel'),('<B1-Motion>','drag')):
        canvas.bind(seq,lambda e,k=kind:events.append(dict(kind=k,delta=getattr(e,'delta',0))))
    api=mouse.api
    api.WindowFromPoint.argtypes=[wintypes.POINT];api.WindowFromPoint.restype=wintypes.HWND
    api.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];api.GetAncestor.restype=wintypes.HWND
    def stop():errors.append('cancelled');root.quit()
    root.bind('<Escape>',lambda _:stop());root.protocol('WM_DELETE_WINDOW',stop)
    actions=[]
    def guard(point):
        x,y=map(int,point)
        if not(canvas.winfo_rootx()+15<x<canvas.winfo_rootx()+canvas.winfo_width()-15 and
               canvas.winfo_rooty()+15<y<canvas.winfo_rooty()+canvas.winfo_height()-15):
            raise RuntimeError('Destination outside our target')
        hit=api.WindowFromPoint(wintypes.POINT(x,y))
        if api.GetAncestor(hit,2)!=api.GetAncestor(root.winfo_id(),2):
            raise RuntimeError('Our target is covered')
    def step():
        nonlocal done
        try:
            if mouse.escape_pressed():stop();return
            if actions:
                point,action,wheel=actions.pop(0);guard(point)
                primary=action if action in ('press','hold','release','click') else 'none'
                command=dict(cursor=point,mode='fist',primary=primary,dragging=primary=='hold',
                             clicks=['left'] if primary=='click' else ['right'] if action=='right' else [],wheel=wheel)
                motion.apply(command)
                def verify():
                    try:
                        guard(mouse.cursor())
                        if mouse.cursor()!=point:raise AssertionError(f'Cursor output mismatch: {mouse.cursor()} != {point}')
                        root.after(min(80,max(15,int(mouse.double_time*150))),step)
                    except Exception as error:errors.append(repr(error));root.quit()
                root.after(70,verify)
            else:
                counts={k:sum(e['kind']==k for e in events) for k in ('left','double','right','wheel','drag')}
                assert counts['left']==3 and counts['double']==1 and counts['right']==1 and counts['wheel']==2,counts
                assert counts['drag']>=1,counts
                assert [e['delta'] for e in events if e['kind']=='wheel']==[120,-120]
                assert not motion.held
                done=True;root.quit()
        except Exception as error:errors.append(repr(error));root.quit()
    def prepare():
        canvas.focus_force()
        x=canvas.winfo_rootx()+canvas.winfo_width()//2;y=canvas.winfo_rooty()+canvas.winfo_height()//2
        bounds=(canvas.winfo_rootx()+25,canvas.winfo_rooty()+25,canvas.winfo_width()-50,canvas.winfo_height()-50)
        area=AbsolutePointer.area(.76)
        for u,v in ((0,0),(1,0),(0,1),(1,1),(.5,.5)):
            anchor=(area[0]+u*.76,(area[1]+v*.76)*9/16)
            target,_=AbsolutePointer.target(anchor,9/16,bounds,.76)
            actions.append((tuple(round(n) for n in target),None,0))
        actions.extend([((x,y),'click',0),((x,y),'click',0),
                        ((x,y),'right',0),((x-90,y+35),None,0),((x-90,y+35),'press',0),
                        ((x+90,y+35),'hold',0),((x+90,y+35),'release',0),
                        ((x,y),None,120),((x,y),None,-120)])
        step()
    root.after(600,prepare);root.after(8000,stop)
    try:root.mainloop()
    finally:
        motion.close();mouse.move(original);root.destroy()
    result=dict(passed=done and not errors,events=events,errors=errors,
                scope='Owned window: camera area -> four screen corners and center, two thumb down/up cycles -> double, held-button drag, right click, signed wheel. Not hand recognition.')
    folder=Path(__file__).resolve().parents[1]/'.runtime'
    folder.mkdir(exist_ok=True)
    (folder/'native-test.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(run())
