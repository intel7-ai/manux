"""Synthetic overlapping fingertips must not latch whichever finger arrived first."""
import math

import pytest

from manux.features.pinch import DRAG, THUMB_MIDDLE, THUMB_RING, PinchFeature
import pinch_hands as H
from pinch_hands import ctx, obs, check_pairs


def nearby(middle, ring):
    hand = H.open_hand()
    hand[4] = (.5, .5, 0.)
    hand[12], hand[16] = (.5-middle, .5, 0.), (.5+ring, .5, 0.)
    hand[11], hand[15] = (.48, .55, 0.), (.52, .55, 0.)
    return hand


def run(hands, **values):
    feature = PinchFeature()
    settings = dict(click_threshold=.04, ring_click_threshold=.044,
                    click_release_margin=.01, long_press_drag_multiplier=2., **values)
    frames = [feature.step(obs(1+i/30, hand) if hand is not None else None,
                          ctx(1+i/30, settings=settings)) for i, hand in enumerate(hands)]
    return frames


def taps(frames):
    return [intent.gesture for frame in frames for intent in frame if intent.phase == "tap"]


@pytest.mark.parametrize("distances", [(.02, .021), (.021, .02), (.02, .02), (0., 0.),
                                      (.001, .002), (.002, .001)])
def test_ambiguous_pair_never_clicks_or_becomes_a_drag(distances):
    frames = run([nearby(*distances)] * 45)
    assert taps(frames) == []
    assert not any(intent.gesture == DRAG for frame in frames for intent in frame)


@pytest.mark.parametrize("first,second,wanted", [
    ((.01, .035), (.035, .01), THUMB_RING),
    ((.035, .01), (.01, .035), THUMB_MIDDLE),
])
def test_candidate_change_restarts_confirmation_in_either_direction(first, second, wanted):
    frames = run([nearby(*first)] + [nearby(*second)] * 10)
    assert taps(frames[:3]) == []
    assert taps(frames) == [wanted]


def test_repeated_candidate_changes_do_not_accumulate_confirmation():
    frames = run([nearby(.01,.035), nearby(.035,.01)] * 30)
    assert taps(frames) == []
    assert not any(intent.gesture == DRAG for frame in frames for intent in frame)


@pytest.mark.parametrize("interruption", [None, nearby(.02,.021), nearby(.06,.07)])
def test_interrupted_candidate_needs_a_fresh_confirmation(interruption):
    frames = run([nearby(.01,.035), interruption] + [nearby(.01,.035)] * 8)
    assert taps(frames[:4]) == []
    assert taps(frames) == [THUMB_MIDDLE]


def test_confirmed_left_click_survives_neighbor_jitter_and_drags_at_user_gain():
    frames = run([nearby(.01,.035)] * 6 + [nearby(.025,.02)] * 40 + [H.open_hand()] * 10)
    assert taps(frames) == [THUMB_MIDDLE]
    drags = [intent for frame in frames for intent in frame if intent.gesture == DRAG]
    assert [intent.phase for intent in drags].count("begin") == 1
    assert [intent.phase for intent in drags].count("end") == 1
    assert all(intent.scale == 2. for intent in drags)
    check_pairs([intent for frame in frames for intent in frame])


@pytest.mark.parametrize("first,second", [(H.middle_pinch, H.ring_pinch), (H.ring_pinch, H.middle_pinch)])
def test_release_allows_the_other_click_then_a_repeat(first, second):
    frames = run([first()] * 8 + [H.open_hand()] * 8 + [second()] * 8
                 + [H.open_hand()] * 8 + [second()] * 8)
    wanted = THUMB_MIDDLE if first == H.middle_pinch else THUMB_RING
    other = THUMB_RING if wanted == THUMB_MIDDLE else THUMB_MIDDLE
    assert taps(frames) == [wanted, other, other]


def test_drag_cannot_start_before_click_confirmation():
    frames = run([H.middle_pinch()] * 40, click_confirm_time=1.5)
    assert taps(frames) == []
    assert not any(intent.gesture == DRAG for frame in frames for intent in frame)


def test_confirmed_right_click_never_turns_into_left_click_or_drag_from_neighbor_jitter():
    frames = run([nearby(.035,.01)] * 6 + [nearby(.02,.025)] * 40)
    assert taps(frames) == [THUMB_RING]
    assert not any(intent.gesture == DRAG for frame in frames for intent in frame)


@pytest.mark.parametrize("distances,wanted", [((.01,.035),THUMB_MIDDLE), ((.035,.01),THUMB_RING)])
@pytest.mark.parametrize("angle,mirror,scale,offset", [
    (0,1,.7,(.2,.1)), (45,-1,1.,(-.1,.1)), (90,1,1.3,(0.,0.)),
    (135,-1,.7,(.15,-.1)), (180,1,1.,(0.,0.)), (270,-1,1.3,(0.,0.)),
])
def test_distinct_clicks_survive_rotation_mirroring_position_and_hand_size(distances,wanted,angle,mirror,scale,offset):
    radians=math.radians(angle)
    transformed=[]
    for x,y,z in nearby(*distances):
        x,y=(x-.5)*scale*mirror,(y-.5)*scale
        transformed.append((.5+offset[0]+x*math.cos(radians)-y*math.sin(radians),
                            .5+offset[1]+x*math.sin(radians)+y*math.cos(radians),z))
    assert taps(run([transformed]*10)) == [wanted]
