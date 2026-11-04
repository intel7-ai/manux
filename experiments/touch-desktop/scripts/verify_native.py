"""Controller -> Win32 -> receipts in an isolated window. No camera claims."""
import ctypes
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import time
import tkinter as tk
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine.controller import Controller
from engine.observation import Observation
from engine.output import Output


def main():
    try:ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:pass
    out=Output();original=out.native.cursor();out.enable(True);c=Controller()
    root=tk.Tk();root.title('MANUX Touch · Input Verification');root.geometry('820x550+60+60')
    label=tk.Label(root,text='MANUX 실제 입력 전달 검사 · 이 창 안에서만 실행합니다',font=('맑은 고딕',15));label.pack(pady=10)
    canvas=tk.Canvas(root,width=800,height=460,bg='#eaf0e9',highlightthickness=0);canvas.pack()
    events=[];tasks=[];phase='';done=False;click_target=None;drop_target=None
    neutral=Observation((.5,.35),(.5,.6),.12,(.99,.65,.64,.68),(0.,.9,-.1,.01),.8)
    palm=replace(neutral,fingers=(.995,)*4)
    pressed=replace(neutral,tip=(.5,.41),signature=(0.,.68,-.32,.04))
    def receipt(kind,e):events.append(dict(kind=kind,phase=phase,x=e.x_root,y=e.y_root,ctrl=bool(e.state&4),delta=getattr(e,'delta',0)))
    canvas.bind('<ButtonPress-1>',lambda e:receipt('down',e))
    canvas.bind('<Double-Button-1>',lambda e:(receipt('down',e),receipt('double',e)))
    canvas.bind('<ButtonRelease-1>',lambda e:receipt('up',e))
    canvas.bind('<B1-Motion>',lambda e:receipt('drag',e))
    canvas.bind('<MouseWheel>',lambda e:receipt('wheel',e))
    def frames(f,n):tasks.extend([('frame',f)]*n)
    def finish(error=''):
        nonlocal done
        if done:return
        done=True;out.close()
        ups=[e for e in events if e['kind']=='up' and e['phase']=='click']
        drops=[e for e in events if e['kind']=='up' and e['phase']=='drag']
        click_error=max((math.dist((e['x'],e['y']),click_target) for e in ups),default=999)
        drop_error=math.dist((drops[-1]['x'],drops[-1]['y']),drop_target) if drops else 999
        checks=dict(three_clicks=len(ups)==3,one_double=sum(e['kind']=='double' and e['phase']=='click' for e in events)==1,
                    click_at_visible_point=click_error<=2,drag_motion=any(e['kind']=='drag' for e in events),
                    one_drag_release=len(drops)==1,drop_at_target=drop_error<=12,
                    wheel=any(e['kind']=='wheel' and not e['ctrl'] for e in events),
                    ctrl_wheel=any(e['kind']=='wheel' and e['ctrl'] for e in events),
                    ctrl_released=not bool(out.native.api.GetAsyncKeyState(0x11)&0x8000),left_released=not out.motor.held)
        report=dict(passed=not error and all(checks.values()),checks=checks,error=error,click_error_px=click_error,drop_error_px=drop_error,events=events,
                    note='Synthetic observed features -> current Controller -> real Windows events, received by own Tk window. Does not measure real hand recognition.')
        (ROOT/'.runtime/native-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        out.native.move(original);root.after(350,root.destroy)
    def begin():
        nonlocal click_target,drop_target
        start_button.configure(state='disabled')
        root.lift();root.focus_force();root.update_idletasks()
        click_target=(canvas.winfo_rootx()+160,canvas.winfo_rooty()+140)
        drop_target=(canvas.winfo_rootx()+570,canvas.winfo_rooty()+350)
        tasks.append(('target',((160,140),'click')));frames(neutral,12)
        frames(pressed,3);frames(neutral,30)
        for _ in range(2):frames(pressed,3);frames(neutral,4)
        frames(neutral,12)
        tasks.append(('target',((340,260),'drag')));frames(neutral,12);frames(palm,5)
        grabbed=replace(palm,palm=(.54,.6),tip=(.54,.35));frames(grabbed,4)
        tasks.append(('drag_path',grabbed))
        tasks.append(('target',((650,140),'wheel')));frames(neutral,12);frames(palm,5)
        for i in range(1,12):frames(replace(palm,palm=(.5,.6-i*.012),tip=(.5,.35-i*.012)),1)
        frames(neutral,7)
        tasks.append(('target',((650,140),'zoom')));frames(neutral,12);frames(replace(neutral,pinch=.22),7)
        for i in range(1,9):frames(replace(neutral,pinch=.22+i*.08),1)
        frames(replace(palm,pinch=1.5),6);tasks.append(('finish',None));root.after(100,tick)
    def tick():
        nonlocal phase,c
        try:
            api=ctypes.windll.user32;api.GetForegroundWindow.restype=ctypes.c_void_p
            api.GetAncestor.argtypes=[ctypes.c_void_p,ctypes.c_uint];api.GetAncestor.restype=ctypes.c_void_p
            if api.GetForegroundWindow()!=api.GetAncestor(root.winfo_id(),2):finish('Verification window lost focus');return
            action,value=tasks.pop(0)
            if action=='finish':root.after(100,finish);return
            if action=='target':
                local,phase=value;label.configure(text='MANUX 실제 입력 검사: '+phase)
                out.motor.cancel();out.motor.resume();out.native.move((canvas.winfo_rootx()+local[0],canvas.winfo_rooty()+local[1]))
                c=Controller();c.reset(out.normalized())
            elif action=='drag_path':
                x,y,w,h=out.bounds();goal=((drop_target[0]-x)/(w-1),(drop_target[1]-y)/(h-1))
                hand=[]
                for g,s,a,lo,hi in zip(goal,c.pointer.anchor_screen,c.pointer.anchor_hand,c.pointer.active_bounds[:2],c.pointer.active_bounds[2:]):
                    hand.append(a+(g-s)/(1-s)*(hi-a) if g>=s else lo+g/s*(a-lo))
                more=[]
                for i in range(1,13):
                    q=i/12;p=tuple(a+(b-a)*q for a,b in zip(value.palm,hand));delta=tuple(b-a for a,b in zip(value.palm,p))
                    f=replace(value,palm=p,tip=tuple(a+d for a,d in zip(value.tip,delta)))
                    more.append(('frame',f))
                more.extend([('frame',f)]*8);more.extend([('frame',replace(f,fingers=neutral.fingers))]*6)
                tasks[:0]=more
            else:
                r=c.step(value,time.perf_counter(),out.normalized());out.apply(r)
            root.after(33,tick)
        except Exception as error:finish(str(error))
    root.protocol('WM_DELETE_WINDOW',lambda:finish('Closed by user'))
    start_button=tk.Button(root,text='입력 검사 시작',command=begin)
    start_button.pack(pady=5)
    try:root.mainloop()
    finally:out.close()

if __name__=='__main__':main()
