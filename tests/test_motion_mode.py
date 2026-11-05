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
    pts[4] = {"out": (cx - 0.10, cy - 0.06), "tucked": (cx + 0.02, cy + 0.03),
              "mid": (p13[0] - 0.06, p13[1])}[thumb]
    # thumb joints: in a line from the base when it is stretched out, zig-zagged otherwise
    bx, by = wrist
    tx, ty = pts[4]
    dx, dy = tx - bx, ty - by
    k = 0.0 if thumb == "out" else 0.55
    pts[1] = (bx, by)
    pts[2] = (bx + dx / 3 - dy * k * 0.5, by + dy / 3 + dx * k * 0.5)
    pts[3] = (bx + dx * 2 / 3 + dy * k * 0.5, by + dy * 2 / 3 - dx * k * 0.5)
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
    assert set(ACTION_KEYS) == {"back", "forward", "tab_prev", "tab_next", "close_window"}
    mm2 = MotionMode({"motion_actions": {"nav": {"left": "close_window"}}})        # Alt+F4 only through the two-step question
    assert mm2._actions["nav"]["left"] == "back"


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
    # The dashboard also tracks the independent close and enable/disable signs.
    def state(**kwargs):
        return SimpleNamespace(close_state=(0.0, 0.0), toggle_progress=0.0, **kwargs)

    stub = state(motion_state=(True, 4.2, "뒤로", 0.0, 0.0), motion_exit_until=0.0)
    visible, text = QtDashboard._motion_badge_now(stub)
    assert visible and "켜짐" in text and "뒤로" in text and "4초" in text
    stub = state(motion_state=(False, 0.0, "", 0.5, 0.0), motion_exit_until=0.0)
    assert "진입 중" in QtDashboard._motion_badge_now(stub)[1]
    stub = state(motion_state=(False, 0.0, "", 0.0, 0.0), motion_exit_until=_time.perf_counter() + 1)
    assert QtDashboard._motion_badge_now(stub) == (True, "모션 모드 꺼짐")
    stub = state(motion_state=(False, 0.0, "", 0.0, 0.0), motion_exit_until=0.0)
    assert QtDashboard._motion_badge_now(stub) == (False, "")
    stub = state(motion_state=(True, 0.0, "", 0.0, 0.0), motion_exit_until=0.0)
    assert QtDashboard._motion_badge_now(stub) == (True, "모션 모드 켜짐")           # no timeout: no countdown
    stub = state(motion_state=(True, 0.0, "", 0.0, 0.5), motion_exit_until=0.0)
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


def test_the_entry_pose_accepts_a_thumbs_up_hand_but_not_a_half_fist():
    mm = MotionMode()
    f = hand_features(hand(fingers="folded", thumb="out"))
    assert mm.entry_pose(f)
    f["curl"] = (1.02, 0.9, 0.85, 1.0)                                 # the index only loosely folded: still a thumbs-up
    assert mm.entry_pose(f)
    f["thumb_tuck"] = 0.5                                              # a straight thumb counts wherever it points (its distance
    assert mm.entry_pose(f)                                            # from the other knuckles no longer matters)
    f["thumb_tuck"] = 0.9
    f["curl"] = (1.18, 1.1, 1.0, 1.05)                                 # a half fist (the cursor-speed shape): not the entry pose
    assert not mm.entry_pose(f)
    f["curl"], f["thumb_straight"] = (0.9, 0.9, 0.85, 1.0), 0.6
    assert not mm.entry_pose(f)                                        # a bent thumb (folded, tucked, lying under the fist) is a fist
    f["thumb_straight"], f["curl"] = 1.0, (1.4, 0.9, 0.85, 1.0)
    assert not mm.entry_pose(f)                                        # a finger really out is not a thumbs-up


def test_a_half_fist_held_still_does_not_switch_motion_mode_on():
    """2026-11-04: moving the cursor in the half-fist shape (thumb out, fingers loosely curled) and pausing toggled the mode."""
    mm = MotionMode()
    half = hand(fingers="folded", thumb="out")
    for p in half[:0]:
        pass
    # fingers loosely curled: tips at ratio ~1.15 (folded() puts them at 0.8)
    pts = [type(p)(x=p.x, y=p.y, z=0.0) for p in half]
    for tip, mcp in ((8, 5), (12, 9), (16, 13), (20, 17)):
        pts[tip] = type(pts[tip])(x=pts[mcp].x, y=pts[mcp].y - 0.015, z=0.0)       # a little above the knuckle: ratio ~1.15
    assert max(hand_features(pts)["curl"]) > 1.05
    events, _ = run(mm, [pts] * 60)                                    # held still for 2 s
    assert not mm.active and not events


def test_a_fist_whose_thumb_lies_on_the_index_is_not_the_entry_pose():
    """Logged 2026-11-04: a fist with the thumb beside the index (far from the ring/pinky knuckles, but folded) toggled the mode."""
    from src.motion_mode import MotionMode
    m = MotionMode()
    base = {"curl": (0.7, 0.6, 0.6, 0.6), "palm": (0.5, 0.5)}
    assert m.entry_pose({**base, "thumb_tuck": 1.3, "thumb_len": 0.25}) is False     # folded thumb
    assert m.entry_pose({**base, "thumb_tuck": 1.3, "thumb_len": 0.8}) is True       # thumbs-up


# ---------------------------------------------------------------------- closing the window (Alt+F4), two steps
def down_frames(y_from=0.35, y_to=0.65, n=10, thumb="out", fingers="open", cx=0.5):
    return [hand(cx=cx, cy=y_from + (y_to - y_from) * i / (n - 1), thumb=thumb, fingers=fingers) for i in range(n)]


def test_one_downward_swipe_only_asks_and_a_second_one_closes_the_window():
    mm, t = entered()
    ev, t = run(mm, down_frames(), t0=t)
    assert [e.kind for e in ev] == ["close_ask"] and mm.close_pending(t) > 2.0       # nothing is sent yet
    ev, t = run(mm, [hand(cy=0.35)] * 25, t0=t)                                      # the hand comes back up and rests
    ev, t = run(mm, down_frames(), t0=t)
    assert [(e.kind, e.action) for e in ev] == [("swipe", "close_window")]
    ev, t = run(mm, [hand(cy=0.35)] * 25 + down_frames(), t0=t)                      # asked again from scratch, not closed
    assert [e.kind for e in ev] == ["close_ask"]


def test_the_close_question_runs_out_and_is_cancelled_by_leaving_motion_mode():
    mm, t = entered()
    ev, t = run(mm, down_frames(), t0=t)
    ev, t = run(mm, [hand(cy=0.35)] * 100, t0=t)                                     # more than 3 s later
    assert mm.close_pending(t) == 0
    ev, t = run(mm, down_frames(), t0=t)
    assert [e.kind for e in ev] == ["close_ask"]                                     # a new question, no closing
    mm.update(t, None, request="off")
    assert mm.close_pending(t) == 0


def test_slow_lowering_sideways_or_bent_hands_do_not_ask():
    for frames in (down_frames(n=40),                                                # slow
                   down_frames(cx=0.5)[:4],                                          # too short
                   [hand(cx=0.5 + 0.2 * i / 9, cy=0.35 + 0.3 * i / 9) for i in range(10)],   # diagonal
                   [hand(cy=0.35 + 0.3 * i / 9, bent=(9,)) for i in range(10)]):     # a folded finger
        mm, t = entered()
        ev, _ = run(mm, frames, t0=t)
        assert not [e for e in ev if e.kind in ("close_ask", "swipe")]


def test_a_horizontal_swipe_is_not_a_close_question():
    mm, t = entered()
    ev, _ = run(mm, swipe_frames(0.7, 0.4), t0=t)
    assert [e.kind for e in ev] == ["swipe"] and ev[0].action == "back"


def test_a_fist_pointing_at_the_camera_is_not_the_entry_pose():
    """Logged 2026-11-04 16:37: palm down, fist towards the camera - the palm is foreshortened (width / length ~2), which inflated
    the thumb measures and passed for a thumbs-up."""
    mm = MotionMode()
    base = {"curl": (0.9, 0.8, 0.5, 0.8), "thumb_tuck": 1.5, "thumb_len": 0.7, "palm": (0.5, 0.5)}
    assert mm.entry_pose({**base, "palm_ratio": 1.1}) is True                 # thumbs-up, palm to the camera
    assert mm.entry_pose({**base, "palm_ratio": 2.1}) is False                # fist pointing at the camera


def test_a_fist_with_the_thumb_bent_underneath_is_not_the_entry_pose_whatever_the_thumb_distances():
    """Logged 2026-11-04 16:43: a fist with the thumb underneath toggled the mode; the old test was 'thumb far from the knuckles'."""
    mm = MotionMode()
    fist = {"curl": (0.8, 0.7, 0.5, 0.7), "thumb_tuck": 1.6, "thumb_len": 0.9, "palm_ratio": 1.1, "palm": (0.5, 0.5)}
    assert mm.entry_pose({**fist, "thumb_straight": 0.62}) is False
    assert mm.entry_pose({**fist, "thumb_straight": 0.97}) is True
    assert mm.entry_pose({**fist, "thumb_straight": 0.97, "thumb_tuck": 0.3}) is True      # a straight thumb pointing along the fist


def test_only_a_thumb_pointing_up_enters_other_directions_stay_free():
    mm = MotionMode()
    base = {"curl": (0.8, 0.7, 0.5, 0.7), "thumb_tuck": 1.6, "thumb_len": 0.9, "thumb_straight": 0.98, "palm_ratio": 1.1, "palm": (0.5, 0.5)}
    assert mm.entry_pose({**base, "thumb_up": 1.0}) is True                  # thumbs-up
    assert mm.entry_pose({**base, "thumb_up": 0.8}) is True                  # leaning
    assert mm.entry_pose({**base, "thumb_up": 0.0}) is False                 # pointing sideways
    assert mm.entry_pose({**base, "thumb_up": -1.0}) is False                # thumbs-down
    f = hand_features(hand(fingers="folded", thumb="out"))
    assert f["thumb_up"] > 0.8 and mm.entry_pose(f)                          # the synthetic thumbs-up hand


# Real frames (2026-11-04 logs): a thumbs-up (20:30) and a fist turned knuckles-up with the bent thumb's tip
# toward the face (21:11), which used to toggle motion mode (palm width / length 0.58, thumb length 0.5).
_THUMBS_UP = [[0.5787, 0.6358, -0.0], [0.5784, 0.5722, -0.0149], [0.5924, 0.5015, -0.0225], [0.6034, 0.4452, -0.0304], [0.6083, 0.403, -0.035], [0.639, 0.5178, -0.0044], [0.6609, 0.5033, -0.0212], [0.6339, 0.5271, -0.0289], [0.6221, 0.5382, -0.0319], [0.6503, 0.5554, -0.0041], [0.6679, 0.5415, -0.023], [0.6341, 0.5611, -0.0263], [0.6267, 0.5651, -0.0245], [0.6572, 0.5938, -0.007], [0.6756, 0.583, -0.0265], [0.6434, 0.5944, -0.0206], [0.6335, 0.5963, -0.0117], [0.661, 0.6322, -0.0112], [0.6809, 0.6205, -0.0207], [0.6571, 0.6214, -0.0137], [0.6448, 0.6205, -0.0059]]
_KNUCKLES_UP_FIST = [[0.324, 0.3602, -0.0], [0.3089, 0.3119, -0.0041], [0.3033, 0.255, -0.0077], [0.3113, 0.2123, -0.0137], [0.332, 0.2016, -0.0175], [0.3399, 0.2316, 0.009], [0.3382, 0.1932, -0.0065], [0.3252, 0.2253, -0.0133], [0.3267, 0.2448, -0.0154], [0.3524, 0.245, 0.0046], [0.3495, 0.2018, -0.0115], [0.332, 0.2447, -0.0117], [0.3367, 0.2638, -0.0064], [0.3647, 0.2638, -0.0032], [0.3596, 0.2229, -0.02], [0.3415, 0.2658, -0.0131], [0.3459, 0.2858, -0.0026], [0.377, 0.2902, -0.0116], [0.3697, 0.2515, -0.0201], [0.3544, 0.2775, -0.0141], [0.3553, 0.2977, -0.0062]]


def test_a_knuckles_up_fist_is_not_the_entry_pose_but_a_thumbs_up_is():
    from src.motion_mode import MotionMode, hand_features
    m = MotionMode()
    assert m.entry_pose(hand_features(_THUMBS_UP))
    assert not m.entry_pose(hand_features(_KNUCKLES_UP_FIST))

