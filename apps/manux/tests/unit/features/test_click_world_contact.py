"""Synthetic 2D overlaps and folded touching fingers, not recorded personal landmarks."""
from dataclasses import replace
import math

import pytest

from manux.features.pinch import DRAG, THUMB_MIDDLE, THUMB_RING, PinchFeature
from manux.features.pinch.engine import world_tip_distances
from manux.features.pinch.preview import PinchPreview
from manux.features.pinch.settings import resolve
import pinch_hands as H


def world_contact(hand, finger=None):
    world=[((x-.5)*.4,(y-.8)*.4,z*.4) for x,y,z in hand]
    thumb=world[4]
    for name,index in (("index",8),("middle",12),("ring",16),("pinky",20)):
        dx,dy,dz=(.005,0.,0.) if name==finger else (.03,-.04,.05)
        world[index]=(thumb[0]+dx,thumb[1]+dy,thumb[2]+dz)
    return tuple(world)


def folded(finger):
    hand=H.open_hand()
    index=12 if finger=="middle" else 16
    x=hand[index][0]
    hand[index]=(x,.68,0.)
    hand[4]=(x+.015,.685,0.)
    return hand


def run(hand,world,frames=12):
    feature=PinchFeature()
    out=[]
    for i in range(frames):
        t=1+i/30
        out.extend(feature.step(replace(H.obs(t,hand),world=world),
                                H.ctx(t,settings={"long_press_drag_multiplier":2.})))
    return out


def taps(out):
    return [i.gesture for i in out if i.phase=="tap"]


def test_projected_middle_overlap_without_3d_contact_cannot_click_or_drag():
    hand=H.middle_pinch()
    out=run(hand,world_contact(hand),frames=45)
    assert taps(out)==[]
    assert all(i.gesture!=DRAG for i in out)


@pytest.mark.parametrize("finger,wanted",[("middle",THUMB_MIDDLE),("ring",THUMB_RING)])
def test_touching_folded_finger_is_not_rejected_by_2d_curl(finger,wanted):
    hand=folded(finger)
    assert taps(run(hand,None))==[]
    assert taps(run(hand,world_contact(hand,finger)))==[wanted]


def test_world_contact_selects_ring_even_when_middle_looks_closer_in_image():
    hand=H.open_hand()
    hand[4],hand[12],hand[16]=(.5,.5,0.),(.49,.5,0.),(.52,.5,0.)
    assert taps(run(hand,world_contact(hand,"ring")))==[THUMB_RING]


def test_folded_middle_contact_keeps_two_x_long_press_drag():
    hand=folded("middle")
    out=run(hand,world_contact(hand,"middle"),frames=45)
    assert taps(out)==[THUMB_MIDDLE]
    drags=[i for i in out if i.gesture==DRAG]
    assert drags and drags[0].phase=="begin" and all(i.scale==2. for i in drags)


def test_3d_separation_releases_even_if_tips_still_overlap_in_image():
    feature=PinchFeature()
    hand=H.middle_pinch()
    out=[]
    for i in range(32):
        world=world_contact(hand,"middle" if i<8 or i>=20 else None)
        t=1+i/30
        out.extend(feature.step(replace(H.obs(t,hand),world=world),H.ctx(t)))
    assert taps(out)==[THUMB_MIDDLE,THUMB_MIDDLE]


@pytest.mark.parametrize("scale,angle",[(.6,0),(1.,45),(1.5,120),(2.,240)])
def test_world_contact_is_independent_of_hand_size_position_and_rotation(scale,angle):
    hand=folded("ring")
    radians=math.radians(angle)
    world=tuple((.1+scale*(x*math.cos(radians)-z*math.sin(radians)),
                 -.2+scale*y,.3+scale*(x*math.sin(radians)+z*math.cos(radians)))
                for x,y,z in world_contact(hand,"ring"))
    assert taps(run(hand,world))==[THUMB_RING]


def test_preview_and_control_share_world_contact_without_preview_intents():
    preview=PinchPreview()
    hand=folded("ring")
    for i in range(6):
        guides,_,_=preview.step(replace(H.obs(1+i/30,hand),world=world_contact(hand,"ring")),resolve({}))
    assert any(index==16 for index,state in guides)
    assert "ring" in preview.core.pinched_fingers
    assert not hasattr(preview,"mapper")


@pytest.mark.parametrize("world",[None,(),((0.,0.,0.),)*21,((float("nan"),0.,0.),)*21])
def test_absent_or_invalid_world_preserves_image_only_compatibility(world):
    assert world_tip_distances(world) is None
    if world is not None and (len(world)!=21 or any(not math.isfinite(v) for p in world for v in p)):
        with pytest.raises(ValueError):
            run(H.middle_pinch(),world)  # The observation contract rejects malformed coordinates first.
    else:
        assert taps(run(H.middle_pinch(),world))==[THUMB_MIDDLE]
