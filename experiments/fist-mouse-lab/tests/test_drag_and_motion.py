from dataclasses import replace
import pytest
from src.fist_engine import FistEngine,Features
from src.cursor_motion import CursorMotion,Glide


class Backend:
    def __init__(self):self.position=(100,100);self.events=[];self.escape=False
    def cursor(self):return self.position
    def escape_pressed(self):return self.escape
    def move(self,point):self.position=tuple(point);self.events.append(('move',self.position))
    def press_left(self):self.events.append(('down',self.position))
    def release_left(self):self.events.append(('up',self.position))
    def click(self,button):self.events.append((button,self.position))
    def wheel(self,amount):self.events.append(('wheel',amount))


def result(primary='none',point=(100,100),dragging=False,mode='fist',clicks=None):
    return dict(primary=primary,cursor=point,dragging=dragging,mode=mode,clicks=clicks or [],wheel=0)


def test_glide_is_short_monotonic_and_never_passes_target():
    glide=Glide((0,100),(100,0),1,.016)
    points=[glide.position(1+i*.004) for i in range(8)]
    assert all(0<=x<=100 and 0<=y<=100 for x,y in points)
    assert [p[0] for p in points]==sorted(p[0] for p in points)
    assert points[-1]==(100,0)


def test_new_target_replaces_unfinished_motion_without_queue():
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.submit((500,100));now[0]+=.004;motion._tick()
    motion.submit((50,100));now[0]+=.06;motion._tick()
    assert native.position==(50,100)
    assert all(e[1][0]<500 for e in native.events)


def test_loss_discards_motion_and_releases_drag():
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.apply(result('press',clicks=['left']))
    motion.apply(result('hold',(300,100),True));motion.apply(result('cancel',mode='waiting'))
    now[0]+=.05;motion._tick()
    assert native.position==(100,100)
    assert [e[0] for e in native.events]==['down','up']
    assert not motion.held


def test_thumb_hold_drag_has_one_down_and_one_up_without_extra_click():
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.apply(result('press',clicks=['left']))
    motion.apply(result('hold',(300,100),True));now[0]+=.06;motion._tick()
    assert motion.held and native.position==(300,100)
    motion.apply(result('release',(310,100)))
    assert native.events[-2:]==[('move',(310,100)),('up',(310,100))]
    assert sum(e[0]=='down' for e in native.events)==1
    assert not any(e[0]=='left' for e in native.events)


@pytest.mark.parametrize('method',['escape','stale','close'])
def test_stop_paths_never_leave_left_button_held(method):
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    motion.apply(result('press',clicks=['left']))
    if method=='escape':native.escape=True;motion._tick()
    elif method=='stale':now[0]+=.3;motion._tick()
    else:motion.close()
    assert native.events[-1][0]=='up' and not motion.held


def test_two_short_thumb_strokes_are_two_button_pairs():
    native=Backend();motion=CursorMotion(native,False)
    for _ in range(2):
        motion.apply(result('press',clicks=['left']));motion.apply(result('release'))
    assert [e[0] for e in native.events]==['down','up','down','up']


def test_click_snaps_to_final_target_before_button_down():
    native=Backend();motion=CursorMotion(native,False)
    motion.submit((400,100))
    motion.apply(result('press',(400,100),clicks=['left']))
    assert native.events[-2:]==[('move',(400,100)),('down',(400,100))]
    assert motion.plan is None
    motion.cancel()


FIST=Features((.5,.5),.12,(.65,.43,.4,.35),.5)


def test_engine_thumb_hold_then_hand_move_starts_drag_and_return_releases():
    engine=FistEngine();cursor=(500,300);t=1.;results=[]
    poses=[FIST]*15+[replace(FIST,thumb=1.)]*12
    poses += [replace(FIST,thumb=1.,anchor=(.5+i*.002,.5)) for i in range(20)]
    poses += [replace(FIST,anchor=(.538,.5))]*6
    for f in poses:
        t+=1/30;r=engine.step(f,t,cursor,(0,0,1000,600));cursor=r['cursor'];results.append(r)
    assert sum(r['primary']=='press' for r in results)==1
    assert sum(r['primary']=='release' for r in results)==1
    assert any(r['dragging'] for r in results)
    assert cursor[0]>540
    assert not results[-1]['primary_down']
    assert not any(r['double'] for r in results)


def test_held_thumb_with_tiny_tremor_does_not_start_drag():
    engine=FistEngine();cursor=(500,300)
    for i in range(50):
        f=FIST if i<15 else replace(FIST,thumb=1.,anchor=(.5+(.0004 if i%2 else 0),.5))
        r=engine.step(f,1+i/30,cursor,(0,0,1000,600));cursor=r['cursor']
        assert not r['dragging']
    r=engine.step(None,3,cursor,(0,0,1000,600))
    assert r['primary']=='cancel' and not r['primary_down']
