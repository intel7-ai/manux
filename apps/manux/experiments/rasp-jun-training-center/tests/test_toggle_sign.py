"""The V sign (index and middle finger up, ring and little finger folded) held still switches the mouse control on / off."""
from src.motion_mode import hand_features
from src.toggle_sign import ToggleSign
from tests.test_motion_mode import hand, run


def vsign(cx=0.5, cy=0.5, thumb="out"):
    return hand(cx=cx, cy=cy, fingers="open", thumb=thumb, bent=(13, 17))


def test_the_pose_is_only_index_and_middle_finger_up():
    ts = ToggleSign()
    assert ts.pose(hand_features(vsign()))
    assert ts.pose(hand_features(vsign(thumb="tucked")))                       # the thumb does not matter
    assert not ts.pose(hand_features(hand(fingers="open")))                    # open hand
    assert not ts.pose(hand_features(hand(fingers="folded")))                  # fist
    assert not ts.pose(hand_features(hand(fingers="open", bent=(9, 13, 17))))  # pointing with the index only
    assert not ts.pose(hand_features(hand(fingers="open", bent=(9, 13))))      # the rock sign (index and little finger)
    assert not ts.pose(hand_features(hand(fingers="open", bent=(5, 9))))       # ring and little finger up


def test_holding_the_sign_toggles_once_and_it_must_be_let_go_before_it_counts_again():
    ts = ToggleSign()
    ev, t = run(ts, [vsign()] * 12)                                            # 0.4 s: not yet (hold 0.6 s)
    assert not ev and 0.4 < ts.progress(t) < 1.0
    ev, t = run(ts, [vsign()] * 10, t0=t)                                      # past 0.6 s
    assert [e.kind for e in ev] == ["toggle"]
    ev, t = run(ts, [vsign()] * 60, t0=t)                                      # holding on: nothing more
    assert not ev
    ev, t = run(ts, [hand(fingers="open")] * 12 + [vsign()] * 40, t0=t)        # let go, made again: toggles back
    assert [e.kind for e in ev] == ["toggle"]


def test_a_moving_or_blocked_hand_and_a_disabled_sign_never_toggle():
    assert not run(ToggleSign(), [vsign(cx=0.3 + 0.01 * i) for i in range(60)])[0]
    assert not run(ToggleSign(), [vsign()] * 60, blocked=True)[0]
    assert not run(ToggleSign({"toggle_sign_enabled": 0.0}), [vsign()] * 60)[0]
    ts = ToggleSign()
    frames = [vsign()] * 12 + [hand(fingers="open")] + [vsign()] * 22           # a noisy frame in the middle of the hold
    assert [e.kind for e in run(ts, frames)[0]] == ["toggle"]
    ts = ToggleSign()
    frames = [vsign()] * 12 + [hand(fingers="open")] * 12 + [vsign()] * 14     # a real dropout: starts over, 0.47 s left < hold
    assert not run(ts, frames)[0]
