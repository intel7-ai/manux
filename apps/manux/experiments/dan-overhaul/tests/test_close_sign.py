"""The rock sign (index and little finger up, middle and ring folded) held still asks to close the window; once more closes it."""
from src.close_sign import CloseSign
from tests.test_motion_mode import hand, run


def rock(cx=0.5, cy=0.5, thumb="out"):
    return hand(cx=cx, cy=cy, fingers="open", thumb=thumb, bent=(9, 13))


def test_the_pose_is_only_index_and_little_finger_up():
    cs = CloseSign()
    from src.motion_mode import hand_features
    assert cs.pose(hand_features(rock()))
    assert cs.pose(hand_features(rock(thumb="tucked")))                       # the thumb does not matter
    assert not cs.pose(hand_features(hand(fingers="open")))                   # open hand
    assert not cs.pose(hand_features(hand(fingers="folded")))                 # fist
    assert not cs.pose(hand_features(hand(fingers="open", bent=(9, 13, 17)))) # a pointing index only
    assert not cs.pose(hand_features(hand(fingers="open", bent=(13, 17))))    # "V"/pointing with the middle finger
    assert not cs.pose(hand_features(hand(fingers="open", bent=(5, 9))))      # ring and little finger up


def test_holding_the_sign_asks_and_holding_on_closes_while_letting_go_cancels():
    cs = CloseSign()
    ev, t = run(cs, [rock()] * 8)                                             # 0.27 s: nothing yet
    assert not ev and 0.4 < cs.progress(t) < 1.0 and not cs.pending(t)
    ev, t = run(cs, [rock()] * 9, t0=t)                                       # past 0.5 s
    assert [e.kind for e in ev] == ["ask"] and cs.pending(t) > 3
    ev, t = run(cs, [rock()] * 8, t0=t)                                       # holding on for 0.27 s: not yet
    assert not ev and 0.4 < cs.progress(t) < 1.0
    ev, t = run(cs, [rock()] * 10, t0=t)                                      # 0.5 s after the question
    assert [e.kind for e in ev] == ["close"] and cs.pending(t) == 0
    # letting go after the question cancels that hold; the sign made again within the window closes
    cs = CloseSign()
    ev, t = run(cs, [rock()] * 20)
    ev, t = run(cs, [hand(fingers="open")] * 12, t0=t)
    ev, t = run(cs, [rock()] * 20, t0=t)
    assert [e.kind for e in ev] == ["close"]


def test_the_question_runs_out_and_a_moving_or_blocked_hand_never_asks():
    cs = CloseSign()
    ev, t = run(cs, [rock()] * 30)
    assert [e.kind for e in ev] == ["ask"]
    ev, t = run(cs, [hand(fingers="open")] * 150, t0=t)                       # 5 s later
    assert cs.pending(t) == 0
    ev, t = run(cs, [rock()] * 20, t0=t)                                      # a new hold starts from scratch: no closing yet
    assert not [e for e in ev if e.kind == "close"]
    moving = [rock(cx=0.3 + 0.01 * i) for i in range(60)]                     # the hand travels
    assert not run(CloseSign(), moving)[0]
    assert not run(CloseSign(), [rock()] * 60, blocked=True)[0]               # a pinch / scroll / drag just ended
    cs = CloseSign({"close_sign_enabled": 0.0})
    assert not run(cs, [rock()] * 60)[0]


def test_a_noisy_frame_does_not_restart_the_hold():
    cs = CloseSign()
    frames = [rock()] * 8 + [hand(fingers="open")] + [rock()] * 8
    ev, _ = run(cs, frames)
    assert [e.kind for e in ev] == ["ask"]


def test_holding_the_sign_on_after_closing_does_not_ask_again_until_it_is_let_go():
    cs = CloseSign()
    ev, t = run(cs, [rock()] * 100)                                           # 3.3 s of holding
    assert [e.kind for e in ev] == ["ask", "close"]                           # ... closes once, then nothing
    ev, t = run(cs, [hand(fingers="open")] * 12 + [rock()] * 20, t0=t)        # let go and made again: a new question
    assert [e.kind for e in ev] == ["ask"]
