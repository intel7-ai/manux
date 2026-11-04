from dataclasses import replace
import json
import math
from pathlib import Path
import pytest
from engine.observation import Observation
from engine.controller import Controller
from engine.pointer import Pointer


def pose(**kwargs):
    return replace(Observation(tip=(.5,.35),palm=(.5,.60),size=.12,
        fingers=(.99,.65,.64,.68),signature=(0.,.9,-.1,.01),pinch=.8,aspect=.5625),**kwargs)


class Session:
    def __init__(self,fps=30):self.c=Controller();self.t=0.;self.dt=1/fps;self.shown=(.5,.5);self.events=[];self.rows=[]
    def feed(self,f,seconds=.1,**kwargs):
        for _ in range(max(1,round(seconds/self.dt))):
            self.t+=self.dt
            r=self.c.step(f,self.t,self.shown,**kwargs)
            self.shown=r['position'];self.events.extend(r['actions']);self.rows.append(r)
        return r
    def ready(self):
        self.feed(pose(),.35)
        assert self.c.state=='move'


@pytest.mark.parametrize('fps',[15,20,30,60])
def test_press_and_release_clicks_without_tip_displacement(fps):
    s=Session(fps);s.ready();before=s.shown
    s.feed(pose(tip=(.5,.41),signature=(0.,.68,-.32,.04)),.1)
    assert s.c.state=='tap'
    assert s.shown==before
    s.feed(pose(),.16)
    assert len([a for a in s.events if a['type']=='click'])==1
    assert math.dist(before,s.shown)<1e-6


@pytest.mark.parametrize('fps',[15,20,30,60])
def test_two_taps_are_two_clicks_same_point(fps):
    s=Session(fps);s.ready()
    for _ in range(2):
        s.feed(pose(tip=(.5,.41),signature=(0.,.68,-.32,.04)),.1)
        s.feed(pose(),.2)
    clicks=[a for a in s.events if a['type']=='click']
    assert len(clicks)==2
    assert clicks[1]['double']
    assert not any(a['type']=='down' for a in s.events)


def test_folded_index_cannot_be_learned_as_neutral_after_loss():
    s=Session();s.ready();s.feed(None)
    s.feed(pose(tip=(.5,.7),signature=(0.,-.2,-.4,.03)),2)
    assert s.c.state=='ready'
    assert not s.events


def test_articulation_and_impossible_tip_flip_never_move_cursor():
    s=Session();s.ready();before=s.shown
    # The historical failure: palm scarcely moves, fingertip jumps to the bottom.
    s.feed(pose(tip=(.5,.93),palm=(.505,.61)),.1)
    assert s.shown==before
    assert s.c.state=='clutch'
    s.feed(pose(),.4)
    assert math.dist(s.shown,before)<1e-6


def test_hand_reentry_clutches_at_displayed_position_then_moves_from_it():
    s=Session();s.ready();s.shown=(.72,.3)
    s.feed(None)
    returned=pose(tip=(.25,.3),palm=(.25,.55))
    s.feed(returned,.4)
    assert math.dist(s.shown,(.72,.3))<1e-6
    s.feed(replace(returned,tip=(.27,.3),palm=(.27,.55)),.25)
    assert s.shown[0]>.72
    assert s.shown[0]<.8


def test_anchor_outside_work_area_does_not_snap_on_next_frame():
    p=Pointer();p.anchor((.94,.85),(.3,.6),0)
    for i in range(10):assert math.dist(p.update((.94,.85),.03*(i+1)),(.3,.6))<1e-6


def test_all_screen_edges_reachable_from_off_center_anchor():
    p=Pointer();p.anchor((.4,.6),(.7,.2),0)
    assert p.target((.115,.115))==pytest.approx((0.,0.))
    assert p.target((.885,.885))==pytest.approx((1.,1.))


def test_palm_initial_direction_locks_drag_and_fold_releases_once():
    s=Session();s.ready()
    open_hand=pose(fingers=(.995,.995,.995,.995))
    s.feed(open_hand,.2)
    s.feed(replace(open_hand,tip=(.54,.35),palm=(.54,.60)),.1)
    assert s.c.state=='drag'
    assert [a['type'] for a in s.events]==['down']
    s.feed(replace(open_hand,tip=(.54,.4),palm=(.54,.65)),.2)
    assert s.c.state=='drag'
    s.feed(pose(),.2)
    assert [a['type'] for a in s.events]==['down','up']


def test_scroll_does_not_turn_into_drag_and_holds_still():
    s=Session();s.ready()
    hand=pose(fingers=(.995,)*4)
    s.feed(hand,.2)
    s.feed(replace(hand,palm=(.5,.66),tip=(.5,.41)),.1)
    assert s.c.state=='scroll'
    s.feed(replace(hand,palm=(.52,.70),tip=(.52,.45)),.1)
    assert any(a['type']=='scroll' for a in s.events)
    count=len(s.events);s.feed(replace(hand,palm=(.52,.70),tip=(.52,.45)),.5)
    assert len(s.events)==count
    assert not any(a['type']=='down' for a in s.events)


def test_scope_is_identical_and_never_activates_excluded_function():
    s=Session();s.c.scope='click';s.ready()
    hand=pose(fingers=(.995,)*4)
    s.feed(hand,.2);s.feed(replace(hand,palm=(.56,.60),tip=(.56,.35)),.2)
    assert s.c.state!='drag'
    assert not any(a['type']=='down' for a in s.events)


def test_zoom_confirm_freezes_ratio_until_relaxed_reset():
    s=Session();s.ready();pinch=pose(pinch=.24)
    s.feed(pinch,.23);assert s.c.state=='zoom'
    s.feed(replace(pinch,pinch=.52),.13)
    assert any(a['type']=='zoom' for a in s.events)
    s.feed(pose(fingers=(.995,)*4,pinch=1.2),.2)
    assert s.c.state=='zoom_done'
    count=len(s.events)
    s.feed(pose(fingers=(.995,)*4,palm=(.7,.8),pinch=1.5),.5)
    assert len(s.events)==count
    assert not any(a['type']=='down' for a in s.events)
    s.feed(pose(),.5)
    assert s.c.state=='move'


def test_ambiguous_thumb_pinch_does_not_start_zoom():
    s=Session();s.ready()
    s.feed(pose(pinch=.25,pinches=(.25,.29,.5,.7)),.4)
    assert s.c.state=='move'


def test_loss_cancels_press_without_click_and_releases_drag():
    s=Session();s.ready();s.feed(pose(signature=(0.,.65,-.4,.08)),.1);s.feed(None)
    assert not s.events
    s.ready();h=pose(fingers=(.995,)*4);s.feed(h,.2);s.feed(replace(h,palm=(.54,.6),tip=(.54,.35)),.1)
    s.feed(None)
    assert [a['type'] for a in s.events]==['down','up']


@pytest.mark.parametrize('before_tip,before_palm,after_tip,after_palm',[
    ((.872,.346),(.838,.566),(.820,.736),(.844,.590)),
    ((.614,.293),(.679,.567),(.641,.614),(.700,.577)),
    ((.722,.230),(.761,.513),(.714,.895),(.769,.640)),
])
def test_recorded_coordinate_discontinuities_are_blocked(before_tip,before_palm,after_tip,after_palm):
    # Tip/palm coordinates observed in the real trace at 1.268, 15.566,
    # and 18.793 seconds. Other features are controlled neutral fixtures;
    # this regression checks discontinuity handling, not recognition accuracy.
    s=Session();s.feed(pose(tip=before_tip,palm=before_palm),.4)
    assert s.c.state=='move'
    before=s.shown
    r=s.feed(pose(tip=after_tip,palm=after_palm),1/30)
    assert not r['moving']
    assert math.dist(s.shown,before)<1e-6


def test_stationary_scroll_with_camera_jitter_sends_no_wheel():
    s=Session();s.ready();hand=pose(fingers=(.995,)*4)
    s.feed(hand,.2);s.feed(replace(hand,palm=(.5,.66),tip=(.5,.41)),.1)
    assert s.c.state=='scroll'
    for i in range(90):s.feed(replace(hand,palm=(.5,.66+(-1)**i*.003),tip=(.5,.41)),1/30)
    assert not s.events
