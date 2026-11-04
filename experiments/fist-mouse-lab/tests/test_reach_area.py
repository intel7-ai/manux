from dataclasses import replace
import math
import pytest
from src.reach_area import DEFAULT_AREA,ReachCalibration,fully_visible,suggest_from_movement,valid_area
from src.absolute_pointer import AbsolutePointer
from src.fist_engine import Features,FistEngine,extract


def hand_at(x,y,aspect=9/16):
    """Articulated fist with a physical footprint, not a point-sized hand."""
    p=[(0.,0.,0.) for _ in range(21)]
    p[:5]=[(0.,0.,0.),(-.03,-.03,0.),(-.055,-.045,0.),(-.065,-.075,0.),(-.045,-.10,0.)]
    for base,bx,by in ((5,-.04,-.10),(9,-.01,-.12),(13,.02,-.11),(17,.05,-.08)):
        p[base:base+4]=[(bx,by,0.),(bx,by-.035,-.005),(bx+.01,by-.01,-.02),(bx+.012,by+.012,-.02)]
    cx,cy=(.5*p[0][a]+.25*p[9][a]+.25*p[13][a] for a in (0,1))
    return [(x+px-cx,y+(py-cy)/aspect,pz) for px,py,pz in p]


@pytest.mark.parametrize('uv',[(0,0),(1,0),(0,1),(1,1)])
def test_whole_fist_stays_visible_while_cursor_reaches_last_screen_pixel(uv):
    base=(.26,.38,.69,.73)
    x=base[0]+uv[0]*(base[2]-base[0]);y=base[1]+uv[1]*(base[3]-base[1])
    points=hand_at(x,y)
    assert fully_visible(points)
    features=extract(points,1920,1080)
    assert features is not None
    engine=FistEngine(area_base=base,area_scale=1.)
    cursor=(960,540)
    for i in range(12):
        r=engine.step(features,1+i/30,cursor,(0,0,1920,1080));cursor=r['cursor']
    assert r['mode']=='fist'
    assert cursor==(uv[0]*1919,uv[1]*1079)
    assert not r['clicks']


def test_camera_border_mapping_requires_a_clipped_hand():
    assert extract(hand_at(1.,.5),1920,1080) is None
    assert extract(hand_at(.5,0.),1920,1080) is None


def test_shorter_hand_travel_still_covers_entire_monitor():
    for scale in (.55,.8,1.):
        left,top,right,bottom=AbsolutePointer.area(scale,DEFAULT_AREA)
        lo,_=AbsolutePointer.target((left,top*9/16),9/16,(0,0,1920,1080),scale,DEFAULT_AREA)
        hi,_=AbsolutePointer.target((right,bottom*9/16),9/16,(0,0,1920,1080),scale,DEFAULT_AREA)
        assert lo==pytest.approx((0,0))
        assert hi==pytest.approx((1919,1079))


def test_approach_to_edge_reaches_final_pixel_despite_filter_deadband():
    mapper=AbsolutePointer();base=(.26,.38,.69,.73)
    for i in range(60):
        u=.5+min(1.,i/50)*(.5-.0002)
        x=base[0]+u*(base[2]-base[0])
        point=mapper.update((x,.5*9/16),9/16,(0,0,1920,1080),1+i/30,1.,.55,base)
    assert point[0]==1919


def test_registered_range_never_expands_past_its_visible_limits():
    assert AbsolutePointer.area(2,DEFAULT_AREA)==pytest.approx(DEFAULT_AREA)
    assert valid_area(DEFAULT_AREA)
    assert not valid_area((.8,.2,.3,.9))
    assert not valid_area((float('nan'),.2,.8,.9))


def add_corner(trial,start,position):
    points=hand_at(*position)
    features=extract(points,1920,1080)
    for i in range(40):trial.update(features,points,start+i/30)


def test_two_comfortable_corners_register_with_inward_room():
    c=ReachCalibration(0)
    add_corner(c,2.2,(.3,.4));add_corner(c,6.2,(.65,.7))
    area,_=c.finish()
    assert valid_area(area)
    assert area[0]>.3 and area[1]>.4 and area[2]<.65 and area[3]<.7


def test_clipped_or_missing_registration_keeps_previous_area():
    c=ReachCalibration(0)
    for i in range(50):c.update(None,None,2+i/30)
    add_corner(c,6.2,(.65,.7))
    assert c.finish()[0] is None


def test_reversed_and_unstable_registration_is_rejected():
    c=ReachCalibration(0)
    add_corner(c,2.2,(.65,.7));add_corner(c,6.2,(.3,.4))
    assert c.finish()[0] is None
    c.upper=[(.2+i*.005,.4) for i in range(40)]
    c.lower=[(.7,.7)]*40
    assert c.finish()[0] is None


def test_suggestion_requires_real_coverage_not_stationary_noise():
    samples=[]
    for i in range(150):
        x=.4+(.3 if i%2 else 0);y=.4+(.32 if i%3 else 0)
        points=hand_at(x,y);f=extract(points,1920,1080)
        samples.append(dict(raw=points,tracking='tracked',result={'mode':'fist'},
                            features={'anchor':f.anchor,'aspect':f.aspect}))
    area=suggest_from_movement(samples)
    assert valid_area(area)
    assert suggest_from_movement([samples[0]]*150) is None


def test_worker_preserves_centered_camera_percent_and_click_calibration(monkeypatch,tmp_path):
    import json
    import src.worker as module
    (tmp_path/'.local').mkdir()
    saved=dict(area_scale=.58,thumb_enter=.8441,thumb_exit=.6704,scroll_speed=1.)
    (tmp_path/'.local/control.json').write_text(json.dumps(saved),encoding='utf-8')
    monkeypatch.setattr(module,'ROOT',tmp_path)
    worker=module.Worker()
    assert worker.settings['thumb_enter']==saved['thumb_enter']
    assert worker.settings['thumb_exit']==saved['thumb_exit']
    assert worker.settings['area_base'] is None
    assert worker.settings['area_scale']==.58
