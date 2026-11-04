from dataclasses import replace
import math
import pytest
from src.fist_engine import FistEngine,Features,extract
from src.cursor_motion import CursorMotion
from test_drag_and_motion import Backend
from test_reach_area import hand_at

FIST=Features((.5,.3),.12,(.65,.43,.4,.35),.5)


def sequence(poses,fps=30,bounds=(0,0,1920,1080)):
    native=Backend();now=[1.];motion=CursorMotion(native,False,lambda:now[0])
    engine=FistEngine(area_scale=.63);results=[]
    for f in poses:
        now[0]+=1/fps
        actual,owned=motion.feedback()
        out=engine.step(f,now[0],actual,bounds,cursor_owned=owned)
        motion.apply(out);motion._tick();results.append(out)
    return results,native,motion


@pytest.mark.parametrize('fps',[20,30,60])
def test_quick_click_with_palm_displacement_never_holds_os_button(fps):
    # Thumb extension moves/rolls the palm by ~60 screen pixels. It is a tap.
    frames=[FIST]*round(.4*fps)
    frames += [replace(FIST,thumb=1.,anchor=(.52,.3))]*round(.16*fps)
    frames += [replace(FIST,anchor=(.52,.3))]*round(.15*fps)
    rs,native,motion=sequence(frames,fps)
    assert sum(r['clicks']==['left'] for r in rs)==1
    assert not any(r['dragging'] or r['primary_down'] for r in rs)
    assert not any(e[0] in ('down','up') for e in native.events)
    assert sum(e[0]=='left' for e in native.events)==1


def test_thumb_opening_displacement_is_not_later_counted_as_drag():
    rs,_,_=sequence([FIST]*15+[replace(FIST,thumb=1.,anchor=(.53,.3))]*30+[FIST]*6)
    assert not any(r['dragging'] for r in rs)
    assert sum(r['clicks']==['left'] for r in rs)==1


def test_hold_then_translate_emits_one_drag_and_no_click():
    poses=[FIST]*15+[replace(FIST,thumb=1.)]*15
    poses += [replace(FIST,thumb=1.,anchor=(.5+i*.002,.3)) for i in range(24)]
    poses += [replace(FIST,anchor=(.546,.3))]*5
    rs,native,motion=sequence(poses)
    assert sum(r['primary']=='press' for r in rs)==1
    assert sum(r['primary']=='release' for r in rs)==1
    assert not any(r['clicks'] for r in rs)
    assert [e[0] for e in native.events if e[0] in ('down','up')]==['down','up']
    assert not motion.held


@pytest.mark.parametrize('corner',[(0,0),(1,0),(0,1),(1,1)])
def test_single_click_at_each_edge_stays_on_the_exact_pixel(corner):
    x=.185+corner[0]*.63;y=.185+corner[1]*.63
    pose=replace(FIST,anchor=(x,y))
    rs,native,_=sequence([pose]*15+[replace(pose,thumb=1.,anchor=(x+.006,y+.006))]*4+[pose]*5)
    clicks=[r for r in rs if r['clicks']]
    assert len(clicks)==1
    assert clicks[0]['cursor']==(corner[0]*1919,corner[1]*1079)
    assert not any(r['dragging'] for r in rs)


def test_outside_thumb_does_not_destroy_valid_palm_tracking():
    points=hand_at(.5,.5);points[4]=(-.01,points[4][1],points[4][2])
    f=extract(points,1920,1080)
    assert f is not None and not f.thumb_visible
    assert all(f.fingers_visible)
    rs,_,_=sequence([FIST]*15+[replace(FIST,thumb=1.,thumb_visible=False)]*20)
    assert not any(r['clicks'] or r['primary_down'] for r in rs)


def test_short_occlusion_keeps_tap_but_never_invents_a_click():
    poses=[FIST]*15+[replace(FIST,thumb=1.)]*4
    poses += [replace(FIST,thumb=2.,thumb_visible=False)]*2+[FIST]*5
    rs,_,_=sequence(poses)
    assert sum(r['clicks']==['left'] for r in rs)==1
    assert not any(r['clicks'] for r in rs if not r.get('thumb_visible',True))
    rs,_,_=sequence([FIST]*15+[replace(FIST,thumb=1.)]*4+
                    [replace(FIST,thumb_visible=False)]*10+[FIST]*5)
    assert not any(r['clicks'] for r in rs)


def test_a_brief_cropped_other_finger_does_not_reset_pending_click():
    poses=[FIST]*15+[replace(FIST,thumb=1.)]*4
    poses += [replace(FIST,fingers_visible=(True,False,True,True))]*2+[FIST]*3
    rs,_,_=sequence(poses)
    assert sum(r['clicks']==['left'] for r in rs)==1


def test_stationary_noise_with_held_thumb_does_not_drag():
    poses=[FIST]*15
    poses += [replace(FIST,thumb=1.,anchor=(.5+.0015*math.sin(i),.3+.001*math.cos(i))) for i in range(90)]
    rs,native,_=sequence(poses)
    assert not any(r['dragging'] for r in rs)
    assert not any(e[0]=='down' for e in native.events)
