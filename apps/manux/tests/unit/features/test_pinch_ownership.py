"""Synthetic contact sequences: task-switch intent and uninterrupted click-to-drag ownership."""
from dataclasses import replace
import math

import pytest

from manux.features.pinch import DRAG, SCROLL, THUMB_MIDDLE, THUMB_PINKY, THUMB_RING, PinchFeature
import pinch_hands as H
from test_click_world_contact import world_contact


def feed(feature, poses, start=1., fps=30):
    out = []
    for i, (points, world) in enumerate(poses):
        now = start + i / fps
        out.extend(feature.step(replace(H.obs(now, points), world=world),
                                H.ctx(now, settings={"long_press_drag_multiplier": 2.})))
    return out


def pose(name):
    points = {"middle": H.middle_pinch, "ring": H.ring_pinch, "pinky": H.pinky_pinch,
              "open": H.open_hand}[name]()
    return points, world_contact(points, None if name == "open" else name)


def taps(out):
    return [i.gesture for i in out if i.phase == "tap"]


def test_projected_pinky_without_world_contact_never_switches_windows():
    hand = H.pinky_pinch()
    assert taps(feed(PinchFeature(), [(hand, world_contact(hand))] * 45)) == []


@pytest.mark.parametrize("fps", [14, 30])
def test_pinky_requires_deliberate_hold_and_remains_once_per_contact(fps):
    feature = PinchFeature()
    short = max(1, round(.2 * fps))
    assert taps(feed(feature, [pose("pinky")] * short, fps=fps)) == []
    out = feed(feature, [pose("pinky")] * fps, start=1 + short / fps, fps=fps)
    assert taps(out) == [THUMB_PINKY]


@pytest.mark.parametrize("name,wanted", [("middle", THUMB_MIDDLE), ("ring", THUMB_RING)])
def test_click_cannot_turn_into_task_switch_until_all_fingers_release(name, wanted):
    sequence = [pose(name)] * 8 + [pose("pinky")] * 36
    feature = PinchFeature()
    assert taps(feed(feature, sequence)) == [wanted]
    out = feed(feature, [pose("open")] * 12 + [pose("pinky")] * 20, start=1 + len(sequence) / 30)
    assert taps(out) == [THUMB_PINKY]


def test_confirmed_middle_is_not_stolen_by_an_overlapping_index_before_drag():
    hand = H.middle_pinch()
    hand[8] = (hand[4][0] + .005, hand[4][1], hand[4][2])
    world = list(world_contact(hand, "middle"))
    palm = math.dist(world[0], world[9])
    world[8] = (world[4][0] + .2 * palm, world[4][1], world[4][2])
    world[12] = (world[4][0] + .5 * palm, world[4][1], world[4][2])
    out = feed(PinchFeature(), [(hand, world_contact(hand, "middle"))] * 6 + [(hand, tuple(world))] * 40)
    assert taps(out) == [THUMB_MIDDLE]
    assert not any(i.gesture == SCROLL for i in out)
    drags = [i for i in out if i.gesture == DRAG]
    assert drags and drags[0].phase == "begin" and all(i.scale == 2. for i in drags)


def test_corroborated_right_click_can_follow_a_fully_released_scroll():
    feature = PinchFeature()
    scroll = H.scroll_pinch()
    prior = feed(feature, [(scroll, world_contact(scroll, "index"))] * 10 + [pose("open")] * 10)
    assert any(i.gesture == SCROLL and i.phase == "end" for i in prior)
    click = feed(feature, [pose("ring")] * 2, start=1 + 20 / 30)
    assert taps(click) == [THUMB_RING]


def test_confirmed_middle_contact_can_curl_into_a_fist_without_losing_drag():
    curled = H.fist()
    out = feed(PinchFeature(), [pose("middle")] * 6 + [(curled, world_contact(curled, "middle"))] * 40)
    assert taps(out) == [THUMB_MIDDLE]
    assert any(i.gesture == DRAG and i.phase == "begin" for i in out)


def test_brief_contact_noise_keeps_long_press_but_real_release_ends_drag():
    hand, touching = pose("middle")
    separated = list(touching)
    palm = math.dist(separated[0], separated[9])
    separated[12] = (separated[4][0] + .75 * palm, separated[4][1], separated[4][2])
    poses = [(hand, tuple(separated) if i % 12 in (10, 11) else touching) for i in range(48)]
    out = feed(PinchFeature(), poses + [pose("open")] * 10)
    assert taps(out) == [THUMB_MIDDLE]
    phases = [i.phase for i in out if i.gesture == DRAG]
    assert phases.count("begin") == 1 and phases.count("end") == 1


def test_drag_holds_through_disagreeing_depth_then_releases_on_clear_open_hand():
    hand, touching = pose("middle")
    feature = PinchFeature()
    initial = feed(feature, [(hand, touching)] * 40)
    assert any(i.gesture == DRAG and i.phase == "begin" for i in initial)

    noisy = world_contact(hand)
    during = feed(feature, [(hand, noisy)] * 7 + [(hand, touching)] * 2, start=1 + 40 / 30)
    assert len(during) == 9 and all(i.gesture == DRAG and i.phase == "hold" for i in during)

    release = feed(feature, [pose("open")] * 6, start=1 + 49 / 30)
    assert sum(i.gesture == DRAG and i.phase == "end" for i in release) == 1
