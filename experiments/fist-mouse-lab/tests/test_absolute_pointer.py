from dataclasses import replace
import math
import statistics
import pytest
from src.absolute_pointer import AbsolutePointer
from src.fist_engine import FistEngine,Features


@pytest.mark.parametrize('aspect',[9/16,16/9,1.])
@pytest.mark.parametrize('uv',[(0,0),(1,0),(0,1),(1,1),(.5,.5)])
def test_camera_area_matches_screen_corners_and_center(aspect,uv):
    mapper=AbsolutePointer();area=mapper.area(.76)
    anchor=(area[0]+uv[0]*.76,(area[1]+uv[1]*.76)*aspect)
    position=mapper.update(anchor,aspect,(-1920,0,1920,1080),1)
    assert position==pytest.approx((-1920+uv[0]*1919,uv[1]*1079))


def test_outside_area_clamps_exactly_to_screen_not_beyond():
    m=AbsolutePointer()
    assert m.update((-.1,1.1),1,(0,0,1920,1080),1)==(0,1079)
    assert m.update((1.2,-.2),1,(0,0,1920,1080),2)==(1919,0)


def test_revisiting_same_hand_position_has_no_accumulated_drift():
    m=AbsolutePointer();bounds=(0,0,1920,1080);t=1.
    for end in ((.7,.5),(.3,.5),(.5,.5)):
        for i in range(60):
            t+=1/60;pos=m.update(end,1,bounds,t)
    assert math.dist(pos,(959.5,539.5))<2


def test_pixel_jitter_is_reduced_without_a_frame_delay_buffer():
    m=AbsolutePointer();out=[];raw=[]
    for i in range(180):
        jitter=3*math.sin(i*2.3)
        p=m.update((.5+jitter/1919*.76,.5),1,(0,0,1920,1080),1+i/30)
        if i>30:out.append(p[0]);raw.append(959.5+jitter)
    assert statistics.pstdev(out)<statistics.pstdev(raw)*.35


def test_fast_continuous_motion_lag_stays_under_25ms():
    m=AbsolutePointer();errors=[]
    for i in range(30):
        x=400+i*30
        anchor=(.12+x/1919*.76,.5)
        pos=m.update(anchor,1,(0,0,1920,1080),1+i/30)
        if i>8:errors.append(x-pos[0])
    assert statistics.mean(errors)/900<.025


def run_frames(engine,pose,count=20,at=1,cursor=(400,300)):
    results=[]
    for i in range(count):
        r=engine.step(pose,at+i/30,cursor,(0,0,1000,600));cursor=r['cursor'];results.append(r)
    return results


FIST=Features((.5,.5),.12,(.65,.43,.4,.35),.5)


def test_absolute_mode_reacquires_the_camera_position_not_an_old_corner():
    e=FistEngine();rs=run_frames(e,FIST)
    e.step(None,2,rs[-1]['cursor'],(0,0,1000,600))
    rs=run_frames(e,replace(FIST,anchor=(.3,.3)),at=3,cursor=rs[-1]['cursor'])
    expected,_=AbsolutePointer.target((.3,.3),1,(0,0,1000,600),.76)
    assert math.dist(rs[-1]['cursor'],expected)<2
    assert all(not r['clicks'] for r in rs)


def test_resting_thumb_between_click_thresholds_does_not_freeze_motion():
    e=FistEngine();rs=run_frames(e,FIST)
    rs=run_frames(e,replace(FIST,anchor=(.53,.5),thumb=.72),at=2,cursor=rs[-1]['cursor'])
    assert rs[-1]['cursor'][0]>530
    assert not any(r['clicks'] for r in rs)


def test_fist_borderline_finger_bend_does_not_stutter_pointer():
    e=FistEngine();rs=run_frames(e,FIST)
    rs=run_frames(e,replace(FIST,fingers=(.88,.43,.4,.35),anchor=(.52,.5)),at=1.7,cursor=rs[-1]['cursor'])
    assert all(r['mode']=='fist' for r in rs)
    assert rs[-1]['cursor'][0]>510


def test_absolute_mode_keeps_two_clicks_and_target_lock():
    e=FistEngine();cursor=(500,300);results=[]
    for i in range(45):
        pose=replace(FIST,thumb=1.) if 20<=i<24 or 29<=i<33 else FIST
        r=e.step(pose,1+i/60,cursor,(0,0,1000,600));cursor=r['cursor'];results.append(r)
    assert sum(len(r['clicks']) for r in results)==2
    assert sum(r['double'] for r in results)==1


def test_area_resize_recalculates_mapping_immediately():
    m=AbsolutePointer();m.update((.6,.5),1,(0,0,1920,1080),1,scale=.76)
    p=m.update((.6,.5),1,(0,0,1920,1080),2,scale=.5)
    assert p[0]==pytest.approx(.7*1919)
