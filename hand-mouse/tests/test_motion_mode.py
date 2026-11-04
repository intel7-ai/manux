"""Motion mode: entering with a pose, swipes as commands, timeouts (synthetic hands, no camera)."""
from types import SimpleNamespace

from src.motion_mode import ACTION_KEYS, MotionMode, hand_features


def hand(cx=0.5, cy=0.5, fingers="open", thumb="out", bent=(), half=()):
    """21 points. Palm 0.1 long. fingers: open | folded. thumb: out | tucked | mid."""
    wrist = (cx, cy + 0.1)
    mcps = {5: (cx - 0.04, cy + 0.01), 9: (cx - 0.013, cy), 13: (cx + 0.013, cy + 0.005), 17: (cx + 0.04, cy + 0.02)}
    pts = {i: (cx, cy + 0.05) for i in range(21)}
    pts[0] = wrist
    for mcp, (x, y) in mcps.items():
        pts[mcp] = (x, y)
        pts[mcp + 3] = (x, y - 0.08) if fingers == "open" else (x, y + 0.02)    # the tip
        if mcp in bent:
            pts[mcp + 3] = (x, y + 0.02)             # this finger folded into the palm
        elif mcp in half:
            pts[mcp + 3] = (x, y - 0.025)            # this finger only half extended (curl ratio ~1.2)
        pts[mcp + 1], pts[mcp + 2] = (x, y - 0.02), (x, y - 0.05)
    p13 = mcps[13]
    pts[4] = {"out": (cx - 0.15, cy + 0.05), "tucked": (cx + 0.02, cy + 0.03),
              "mid": (p13[0] - 0.06, p13[1])}[thumb]
    return [SimpleNamespace(x=pts[i][0], y=pts[i][1], z=0.0) for i in range(21)]


def run(mm, frames, t0=0.0, fps=30, **kw):
    events, t = [], t0
    for lm in frames:
        ev = mm.update(t, lm, **kw)
        if ev:
            events.append(ev)
        t += 1 / fps
    return events, t


def swipe_frames(x_from, x_to, n=10, thumb="out", fingers="open", dy=0.0):
    return [hand(cx=x_from + (x_to - x_from) * i / (n - 1), cy=0.5 + dy * i / (n - 1), thumb=thumb, fingers=fingers)
            for i in range(n)]


def entered(mm=None, thumb_variant="out"):
    mm = mm or MotionMode()
    events, t = run(mm, [hand(fingers="folded", thumb="out")] * 30)
    assert mm.active and events[0].kind == "enter"
    return mm, t


# ---------------------------------------------------------------------- features
def test_features_tell_the_poses_apart():
    f_open = hand_features(hand(fingers="open", thumb="out"))
    f_fold = hand_features(hand(fingers="folded", thumb="tucked"))
    assert min(f_open["curl"]) > 1.45 and f_open["thumb_tuck"] >= 1.0
    assert max(f_fold["curl"]) < 1.1 and f_fold["thumb_tuck"] <= 0.45
    assert 0.45 < hand_features(hand(thumb="mid"))["thumb_tuck"] < 0.75


# ---------------------------------------------------------------------- entering
def test_the_entry_pose_held_still_enters_motion_mode_after_the_hold():
    mm = MotionMode()
    events, t = run(mm, [hand(fingers="folded", thumb="out")] * 20)           # 0.67 s: not yet
    assert not mm.active and not events
    events, t = run(mm, [hand(fingers="folded", thumb="out")] * 10, t0=t)     # past 0.8 s
    assert mm.active and events[0].kind == "enter" and events[0].info["how"] == "pose"


def test_other_poses_and_a_travelling_hand_do_not_enter():
    for fingers, thumb in (("folded", "tucked"), ("folded", "mid"), ("open", "out"), ("open", "tucked")):
        mm = MotionMode()
        run(mm, [hand(fingers=fingers, thumb=thumb)] * 45)
        assert not mm.active, (fingers, thumb)         # an ordinary fist / open hand never enters
    mm = MotionMode()
    run(mm, [hand(cx=0.3 + 0.01 * i, fingers="folded", thumb="out") for i in range(45)])
    assert not mm.active                               # the pose while the hand travels
    mm = MotionMode()
    run(mm, [hand(fingers="folded", thumb="out")] * 45, blocked=True)
    assert not mm.active                               # a pinch/scroll/drag is in progress or just ended


def test_the_button_enters_and_leaves():
    mm = MotionMode()
    ev = mm.update(1.0, hand(), request="toggle")
    assert mm.active and ev.kind == "enter" and ev.info["how"] == "button"
    ev = mm.update(2.0, hand(), request="toggle")
    assert not mm.active and ev.kind == "exit" and ev.info["why"] == "request"


# ---------------------------------------------------------------------- swipes
def test_open_hand_with_the_thumb_out_swipes_back_and_forward():
    mm, t = entered()
    ev, t = run(mm, swipe_frames(0.7, 0.4), t0=t)                     # to the user's left
    assert [e.action for e in ev if e.kind == "swipe"] == ["back"] and ev[0].info["variant"] == "nav"
    ev, t = run(mm, [hand(cx=0.4)] * 40, t0=t)                        # rest past the cooldown and return block
    ev, t = run(mm, swipe_frames(0.4, 0.7), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]


def test_open_hand_with_the_thumb_tucked_swipes_the_tabs():
    mm, t = entered()
    ev, t = run(mm, swipe_frames(0.7, 0.4, thumb="tucked"), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["tab_prev"] and ev[0].info["variant"] == "tab"
    ev, t = run(mm, [hand(cx=0.4, thumb="tucked")] * 40, t0=t)
    ev, t = run(mm, swipe_frames(0.4, 0.7, thumb="tucked"), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["tab_next"]


def test_ambiguous_thumb_slow_vertical_short_or_closed_strokes_send_nothing():
    cases = {
        "too slow": swipe_frames(0.3, 0.6, n=60),
        "too short": swipe_frames(0.5, 0.6),
        "vertical": swipe_frames(0.5, 0.55, dy=0.3),
        "fingers folded": swipe_frames(0.3, 0.7, fingers="folded", thumb="out"),
    }
    for name, frames in cases.items():
        mm, t = entered()
        settle = [frames[0]] * 12                       # the hand rests where the stroke starts (no tracking jump)
        ev, _ = run(mm, settle + frames, t0=t)
        assert not [e for e in ev if e.kind == "swipe"], name


def test_a_thumb_halfway_keeps_the_last_decision_and_with_none_yet_sends_nothing():
    mm, t = entered()
    ev, t = run(mm, [hand(cx=0.7, thumb="out")] * 8, t0=t)                 # an open hand, thumb out: the state is "nav"
    ev, t = run(mm, [hand(cx=0.7, thumb="mid")] * 8 + swipe_frames(0.7, 0.4, thumb="mid"), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["back"]
    fresh = MotionMode()
    fresh.update(0.0, hand(thumb="mid"), request="on")        # switched on by the button with no thumb decision
    ev, _ = run(fresh, [hand(cx=0.7, thumb="mid")] * 8 + swipe_frames(0.7, 0.4, thumb="mid"), t0=0.1)
    assert not [e for e in ev if e.kind == "swipe"]


def test_the_hand_coming_back_is_not_a_second_swipe_but_a_later_one_is():
    mm, t = entered()
    ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)                     # swipe right
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]
    ev, t = run(mm, swipe_frames(0.7, 0.3, n=15), t0=t)               # the hand coming back, slower: ignored
    assert not [e for e in ev if e.kind == "swipe"]
    ev, t = run(mm, [hand(cx=0.3)] * 30, t0=t)                       # rest where the hand came back to
    ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)                     # a deliberate swipe after the pause
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]
    ev, t = run(mm, [hand(cx=0.7)] * 30, t0=t)
    ev, t = run(mm, swipe_frames(0.7, 0.3), t0=t)                     # and the other way, after resting
    assert [e.action for e in ev if e.kind == "swipe"] == ["back"]
    ev, t = run(mm, [hand(cx=0.3)] * 10, t0=t)                        # a third of a second of rest...
    ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)                     # ...then as quick as the last one: allowed
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]


def test_swipes_are_ignored_outside_motion_mode():
    mm = MotionMode()
    ev, _ = run(mm, swipe_frames(0.3, 0.7) * 3)
    assert not ev and not mm.active


# ---------------------------------------------------------------------- leaving
def test_it_is_a_toggle_and_stays_on_by_itself():
    mm, t = entered()
    ev, t = run(mm, [hand()] * 600, t0=t)                             # 20 s of nothing
    assert mm.active and mm.remaining(t) == 0.0
    ev, t = run(mm, [None] * 150, t0=t)                               # and 5 s without a hand
    assert mm.active


def test_the_timeout_and_hand_lost_exits_still_work_when_asked_for():
    mm, t = entered(MotionMode({"motion_timeout": 6.0}))
    ev, t = run(mm, [hand()] * 120, t0=t)
    assert mm.active and mm.remaining(t) > 0
    ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)                     # a swipe resets the countdown
    ev, t = run(mm, [hand(cx=0.7)] * 150, t0=t)
    assert mm.active
    ev, t = run(mm, [hand(cx=0.7)] * 60, t0=t)
    assert not mm.active and ev[-1].info["why"] == "timeout"
    mm, t = entered(MotionMode({"motion_hand_lost_exit": 1.5}))
    ev, t = run(mm, [None] * 60, t0=t)
    assert not mm.active and ev[-1].info["why"] == "hand lost"


def test_the_entry_pose_held_again_switches_it_off_but_not_the_pose_that_switched_it_on():
    mm, t = entered()                                                 # still holding the entry pose
    ev, t = run(mm, [hand(fingers="folded", thumb="out")] * 60, t0=t)  # 2 s more of the same pose
    assert mm.active and not ev                                       # that is not "off"
    ev, t = run(mm, [hand(cx=0.5)] * 6, t0=t)                         # the pose is let go
    assert mm.active
    ev, t = run(mm, [hand(fingers="folded", thumb="out")] * 14, t0=t)  # ~0.45 s into the hold
    assert mm.active and 0.3 < mm.leaving(t) < 0.8
    ev, t = run(mm, [hand(fingers="folded", thumb="out")] * 16, t0=t)  # past 0.8 s
    assert not mm.active and ev[-1].kind == "exit" and ev[-1].info["why"] == "pose"


def test_swipes_that_were_refused_are_recorded_with_the_reason():
    mm, t = entered()
    ev, t = run(mm, [hand(cx=0.3)] * 8 + swipe_frames(0.3, 0.42, n=14), t0=t)    # a short stroke (0.12 in 0.43 s)
    assert not [e for e in ev if e.kind == "swipe"]
    assert mm.last_miss is not None and "short" in mm.last_miss["why"]
    ev, t = run(mm, [hand(cx=0.42)] * 20 + swipe_frames(0.42, 0.7), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"] and mm.last_miss is None


def test_the_command_mapping_can_be_changed_but_only_to_known_commands():
    mm = MotionMode({"motion_actions": {"nav": {"left": "tab_prev", "right": "launch_missiles"}}})
    mm, t = entered(mm)
    ev, t = run(mm, swipe_frames(0.7, 0.4), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["tab_prev"]
    ev, t = run(mm, [hand(cx=0.4)] * 40, t0=t)
    ev, t = run(mm, swipe_frames(0.4, 0.7), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]     # the unknown command was refused
    assert set(ACTION_KEYS) == {"back", "forward", "tab_prev", "tab_next"}


def test_entering_progress_is_reported_while_the_pose_is_held():
    mm = MotionMode()
    run(mm, [hand(fingers="folded", thumb="out")] * 10)             # ~0.3 s into the 0.8 s hold
    assert 0.2 < mm.entering(10 / 30) < 0.6
    run(mm, [hand(fingers="open", thumb="out")] * 10, t0=10 / 30)   # the pose is dropped for good (longer than the grace)
    assert mm.entering(0.7) == 0.0


def test_the_corner_badge_text_follows_the_mode():
    import time as _time
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from src.qt_dashboard import QtDashboard
    stub = SimpleNamespace(motion_state=(True, 4.2, "뒤로", 0.0, 0.0), motion_exit_until=0.0)
    visible, text = QtDashboard._motion_badge_now(stub)
    assert visible and "켜짐" in text and "뒤로" in text and "4초" in text
    stub = SimpleNamespace(motion_state=(False, 0.0, "", 0.5, 0.0), motion_exit_until=0.0)
    assert "진입 중" in QtDashboard._motion_badge_now(stub)[1]
    stub = SimpleNamespace(motion_state=(False, 0.0, "", 0.0, 0.0), motion_exit_until=_time.perf_counter() + 1)
    assert QtDashboard._motion_badge_now(stub) == (True, "모션 모드 꺼짐")
    stub = SimpleNamespace(motion_state=(False, 0.0, "", 0.0, 0.0), motion_exit_until=0.0)
    assert QtDashboard._motion_badge_now(stub) == (False, "")
    stub = SimpleNamespace(motion_state=(True, 0.0, "", 0.0, 0.0), motion_exit_until=0.0)
    assert QtDashboard._motion_badge_now(stub) == (True, "모션 모드 켜짐")           # no timeout: no countdown
    stub = SimpleNamespace(motion_state=(True, 0.0, "", 0.0, 0.5), motion_exit_until=0.0)
    assert "끄는 중" in QtDashboard._motion_badge_now(stub)[1]


def test_a_return_as_fast_as_the_swipe_is_still_the_hand_coming_back():
    """Repeating a swipe means bringing the hand back; that must not read as the opposite command."""
    mm, t = entered()
    for _ in range(3):                                                # forward x3: stroke, return, stroke, return ...
        ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)
        assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]
        ev, t = run(mm, swipe_frames(0.7, 0.3), t0=t)                 # just as fast, no rest in between
        assert not [e for e in ev if e.kind == "swipe"]
        assert mm.last_miss is None or "return_stroke" in mm.last_miss["why"]
        ev, t = run(mm, [hand(cx=0.3)] * 3, t0=t)                     # (a breath of rest is not enough)
        assert not [e for e in ev if e.kind == "swipe"]


def test_the_opposite_direction_counts_after_the_hand_rested():
    mm, t = entered()
    ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]
    ev, t = run(mm, [hand(cx=0.7)] * 12, t0=t)                        # 0.4 s of rest at the end of the stroke
    ev, t = run(mm, swipe_frames(0.7, 0.3), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["back"]
    ev, t = run(mm, swipe_frames(0.3, 0.7), t0=t)                     # straight back the other way, no rest: ignored
    assert not [e for e in ev if e.kind == "swipe"]
    mm2 = MotionMode({"motion_reverse_pause": 0.0})                   # the rule can be switched off
    mm2, t2 = entered(mm2)
    ev, t2 = run(mm2, swipe_frames(0.3, 0.7), t0=t2)
    ev, t2 = run(mm2, [hand(cx=0.7)] * 4, t0=t2)                      # a breath, far short of 0.25 s
    ev, t2 = run(mm2, swipe_frames(0.7, 0.3), t0=t2)
    assert [e.action for e in ev if e.kind == "swipe"] == ["back"]


def swing_frames(x_from, x_to, thumbs, n=10):
    """A stroke whose thumb pose changes while it goes: thumbs[i] for the i-th third/fraction of the frames."""
    out = []
    for i in range(n):
        thumb = thumbs[min(len(thumbs) - 1, i * len(thumbs) // n)]
        out.append(hand(cx=x_from + (x_to - x_from) * i / (n - 1), thumb=thumb))
    return out


def test_a_tucked_thumb_that_pops_out_during_a_left_swing_is_still_a_tab_swipe():
    """Logged 2026-11-04: on left strokes the thumb popped out (9 of 55 started tucked and ended out), so
    'thumb tucked + left' was read as 'thumb out + left' = back instead of the previous tab."""
    mm, t = entered()
    rest = [hand(cx=0.7, thumb="tucked")] * 8                        # the thumb tucked before and at the start
    ev, t = run(mm, rest + swing_frames(0.7, 0.4, ["tucked", "tucked", "out", "out"]), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["tab_prev"]
    mm, t = entered()
    ev, t = run(mm, [hand(cx=0.7, thumb="out")] * 8 + swing_frames(0.7, 0.4, ["out"]), t0=t)   # out all the way
    assert [e.action for e in ev if e.kind == "swipe"] == ["back"]
    mm, t = entered()
    ev, t = run(mm, [hand(cx=0.4, thumb="out")] * 8 + swing_frames(0.4, 0.7, ["out", "out", "tucked", "tucked"]), t0=t)
    assert [e.action for e in ev if e.kind == "swipe"] == ["tab_next"]      # the right swing that ends tucked


# ---------------------------------------------------------------------- only a completely open hand swipes
def stroke_with(finger_kw, x_from=0.3, x_to=0.7, n=10):
    return [hand(cx=x_from + (x_to - x_from) * i / (n - 1), **finger_kw) for i in range(n)]


def test_a_swipe_needs_all_four_fingers_fully_extended():
    mm = MotionMode()
    f_half = hand_features(hand(half=(9,)))
    assert 1.1 < f_half["curl"][1] < 1.3 and not mm.open_hand(f_half)
    assert mm.open_hand(hand_features(hand())) and not mm.open_hand(hand_features(hand(bent=(5,))))
    cases = {
        "index folded": dict(bent=(5,)),
        "middle folded": dict(bent=(9,)),
        "ring half open": dict(half=(13,)),
        "pinky half open": dict(half=(17,)),
        "two fingers half open": dict(half=(9, 13)),
    }
    for name, kw in cases.items():
        mm, t = entered()
        ev, _ = run(mm, [hand(cx=0.3)] * 12 + stroke_with(kw), t0=t)
        assert not [e for e in ev if e.kind == "swipe"], name
        assert mm.last_miss is not None and ("bent" in mm.last_miss["why"] or "not_open" in mm.last_miss["why"]
                                              or "ends_closed" in mm.last_miss["why"]), name
    mm, t = entered()
    ev, _ = run(mm, [hand(cx=0.3)] * 12 + stroke_with({}), t0=t)            # the same stroke with a fully open hand
    assert [e.action for e in ev if e.kind == "swipe"] == ["forward"]


def test_a_finger_that_bends_for_a_moment_during_the_stroke_refuses_it():
    mm, t = entered()
    frames = [hand(cx=0.3)] * 12 + [hand(cx=0.3 + 0.4 * i / 9, bent=(9,) if i in (2, 3) else ()) for i in range(10)]
    ev, _ = run(mm, frames, t0=t)
    assert not [e for e in ev if e.kind == "swipe"] and "bent" in mm.last_miss["why"]


def test_pose_names_for_logs_and_later_motions():
    mm = MotionMode()
    assert mm.pose(hand_features(hand(fingers="folded", thumb="out"))) == "entry"
    assert mm.pose(hand_features(hand(fingers="open", thumb="tucked"))) == "open"
    assert mm.pose(hand_features(hand(fingers="folded", thumb="tucked"))) == "fist"
    assert mm.pose(hand_features(hand(bent=(5,)))) == "other"


def test_a_noisy_frame_does_not_throw_away_a_hold_but_a_long_dropout_does():
    mm = MotionMode()
    pose = hand(fingers="folded", thumb="out")
    blip = hand(fingers="open", thumb="out")                           # one frame where the fingers read as open
    frames = [pose] * 10 + [blip] * 3 + [pose] * 14                    # 0.1 s dropout (inside the 0.2 s grace)
    events, t = run(mm, frames)
    assert mm.active and events[0].kind == "enter"                     # 27 frames = 0.9 s, the hold survived the blip
    mm = MotionMode()
    events, t = run(mm, [pose] * 10 + [blip] * 9 + [pose] * 14)        # 0.3 s dropout: starts over, 0.47 s left < hold
    assert not mm.active


def test_the_entry_pose_is_forgiving_about_a_thumbs_up_hand_that_is_not_perfect():
    mm = MotionMode()
    f = hand_features(hand(fingers="folded", thumb="out"))
    assert mm.entry_pose(f)
    f["curl"] = (1.18, 0.9, 0.85, 1.0)                                 # the index only loosely folded
    assert mm.entry_pose(f)
    f["thumb_tuck"] = 0.9                                              # the thumb not fully out
    assert mm.entry_pose(f)
    f["thumb_tuck"] = 0.7
    assert not mm.entry_pose(f)                                        # ... but a thumb half across is a fist
    f["thumb_tuck"], f["curl"] = 1.2, (1.4, 0.9, 0.85, 1.0)
    assert not mm.entry_pose(f)                                        # a finger really out is not a thumbs-up
