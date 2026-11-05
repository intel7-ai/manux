"""Feature `pointer`: v0.3 pointer cases (hand-mouse/tests/test_pointer_engine.py and
test_pointer_workspace.py, ported) and the Feature contract rules (docs/contracts.md section 5)."""
from __future__ import annotations

import math
import random
from types import SimpleNamespace

import pytest

import pointer_hands as H
from manux.core.contracts import GESTURE_ID_RE, Activity, Feature, Intent
from manux.features.pointer import DEFAULT_BINDINGS, GESTURES, SETTINGS, PointerFeature
from manux.features.pointer.engine import PointerCore
from manux.features.pointer.pinch import PointerPinch, palm_anchor_point
from manux.features.pointer.settings import DEFAULTS, V03_KEYS, PointerSettings, from_mapping
from manux.features.pointer.workspace import (
    FINGERTIPS, IndexTipGuard, MoveLatch, PointerPose, ThumbTrigger, calibrated_pointer,
    is_pointing_pose, pointer_axes, thumb_contacts, thumb_distances, trigger_ratio,
)

DT = 1 / 30
ASPECT = H.ASPECT


# ============================================================================ workspace (v0.3 test_pointer_workspace.py)

@pytest.mark.parametrize(
    ("touching", "ambiguous", "expected"),
    [
        ((1,), (), (0, 1)),
        ((1, 2), (), (0, 1, 2)),
        ((3,), (), (0, 3)),
        ((1,), (2,), ()),
        ((), (), ()),
    ],
)
def test_thumb_contact_combinations(touching, ambiguous, expected):
    points = [SimpleNamespace(x=.7, y=.4, z=0) for _ in range(21)]
    points[4].x = .3
    points[5].x = .4
    points[17].x = .6
    for finger in touching:
        points[FINGERTIPS[finger]].x = .32
    for finger in ambiguous:
        points[FINGERTIPS[finger]].x = .355
    assert thumb_contacts(thumb_distances(points, .5625)) == expected


def test_pointer_calibration_removes_axis_mixing_around_image_center():
    center, right, up = (.5, .5), (.6, .54), (.53, .35)
    aspect = .5625
    axes = pointer_axes(center, right, up, aspect)
    x, y = calibrated_pointer(center, axes, aspect)
    assert (x, y) == pytest.approx(center)
    right_x, right_y = calibrated_pointer(right, axes, aspect)
    up_x, up_y = calibrated_pointer(up, axes, aspect)
    assert right_x > x and right_y == pytest.approx(y)
    assert up_x == pytest.approx(x) and up_y < y


def test_pointer_calibration_rejects_short_or_parallel_strokes():
    assert pointer_axes((.5, .5), (.51, .5), (.5, .35), .5625) is None
    assert pointer_axes((.5, .5), (.6, .4), (.61, .39), .5625) is None


def test_index_tip_guard_rejects_spikes_but_accepts_whole_hand_motion():

    points = [SimpleNamespace(x=.5, y=.5) for _ in range(21)]
    points[17].x = .6
    points[8].x = .56
    guard = IndexTipGuard()
    assert guard.accept(points, .5625)
    for point in points:
        point.x += .10
    assert guard.accept(points, .5625)
    points[8].x += .05
    assert not guard.accept(points, .5625)
    points[8].x -= .05
    assert guard.accept(points, .5625)
    points[8].x += .05
    assert not guard.accept(points, .5625)
    assert guard.accept(points, .5625)


@pytest.mark.parametrize("click", ["left_click", "right_click"])
def test_pointer_pose_clicks_on_release_directly_from_pointing(click):

    pose = PointerPose()
    assert pose.update(1.0, pointing=True) == "move_cursor"
    assert pose.update(1.04, pointing=True, pinching=True) == "pointer_wait"
    assert pose.update(1.08, pointing=False, click=click) == "pointer_release"
    assert pose.update(1.12, pointing=True, released=True) == "pointer_release"
    assert pose.update(1.16, pointing=True, released=True) == click
    assert pose.update(1.20, pointing=True, released=True) == "move_cursor"


def test_pointing_pose_accepts_index_aimed_toward_camera_but_not_open_palm():

    points = [SimpleNamespace(x=.5, y=.5, z=0) for _ in range(21)]
    for step in range(4):
        points[5 + step].z = -.02 * step
    # The wrist ratio alone misses this index even though its joints are straight.
    assert is_pointing_pose(points, (1.25, 1.2, 1.2, 1.2), .5625)
    for base in (9, 13, 17):
        for step in range(4):
            points[base + step].z = -.02 * step
    assert not is_pointing_pose(points, (1.7, 1.7, 1.7, 1.7), .5625)


def _trigger_hand(thumb_gap, tip_x=.5):
    """Index MCP #5 at (.5, .5), PIP #6 straight above, palm width .12; thumb tip beside #5-#6."""
    points = [SimpleNamespace(x=.5, y=.5, z=0) for _ in range(21)]
    points[6].y = .42
    points[8].x, points[8].y = tip_x, .30
    points[17].x = .62
    points[4].x, points[4].y = .5 - thumb_gap, .46
    return points


FAR, NEAR, PRESSED = .09, .05, .02  # ratios .75, .42, .17 with the defaults (.50 / .28 / .40)


def test_trigger_ratio_measures_thumb_to_index_side_in_palm_widths():

    assert trigger_ratio(_trigger_hand(PRESSED), 1.0) == pytest.approx(.02 / .12)
    assert trigger_ratio(_trigger_hand(FAR), 1.0) == pytest.approx(.09 / .12)
    tiny = _trigger_hand(PRESSED)
    tiny[17].x = .51
    assert trigger_ratio(tiny, 1.0) != trigger_ratio(tiny, 1.0)  # NaN for a too-small palm


def test_trigger_quick_press_is_a_left_click_at_the_rewound_cursor():

    trig = ThumbTrigger()
    for i, x in enumerate((100, 110, 120, 130)):
        assert trig.update(1.0 + i * .04, _trigger_hand(FAR), 1.0, pointing=True) == "move_cursor"
        trig.record(1.0 + i * .04, (x, 50))
    assert trig.update(1.16, _trigger_hand(NEAR), 1.0, pointing=True) == "pointer_wait"  # frozen
    assert trig.warp is None
    assert trig.update(1.20, _trigger_hand(PRESSED), 1.0, pointing=True) == "pointer_wait"
    assert trig.warp == (120, 50)  # last cursor position at least 100 ms before the press
    assert trig.update(1.24, _trigger_hand(.04), 1.0, pointing=True) == "pointer_wait"  # hysteresis
    assert trig.update(1.30, _trigger_hand(FAR), 1.0, pointing=True) == "left_click"
    assert trig.update(1.34, _trigger_hand(FAR), 1.0, pointing=True) == "move_cursor"


def test_trigger_long_still_press_is_one_right_click_and_no_left_click():

    trig = ThumbTrigger()
    assert trig.update(1.0, _trigger_hand(PRESSED), 1.0, pointing=True) == "pointer_wait"
    actions = [trig.update(1.0 + i * .05, _trigger_hand(PRESSED), 1.0, pointing=True)
               for i in range(1, 20)]
    assert actions.count("right_click") == 1
    assert trig.update(2.1, _trigger_hand(FAR), 1.0, pointing=True) == "pointer_wait"


def test_trigger_press_and_move_drags_until_release_or_hand_loss():

    trig = ThumbTrigger()
    trig.update(1.0, _trigger_hand(PRESSED), 1.0, pointing=True)
    # Small tip shake from the press itself is not a drag (.02 / .12 palm widths)
    assert trig.update(1.04, _trigger_hand(PRESSED, tip_x=.52), 1.0, pointing=True) == "pointer_wait"
    assert trig.update(1.08, _trigger_hand(PRESSED, tip_x=.56), 1.0, pointing=True) == "drag_hold"
    assert trig.update(1.12, _trigger_hand(PRESSED, tip_x=.60), 1.0, pointing=True) == "drag_hold"
    # No left click after a drag
    assert trig.update(1.16, _trigger_hand(FAR, tip_x=.60), 1.0, pointing=True) == "pointer_wait"

    trig.update(2.0, _trigger_hand(PRESSED), 1.0, pointing=True)
    assert trig.update(2.04, _trigger_hand(PRESSED, tip_x=.56), 1.0, pointing=True) == "drag_hold"
    lost = _trigger_hand(PRESSED)
    lost[17].x = .5  # palm collapses -> NaN ratio
    assert trig.update(2.08, lost, 1.0, pointing=True) == "pointer_wait"
    assert trig.state == "idle"


def test_trigger_only_moves_while_pointing():

    trig = ThumbTrigger()
    assert trig.update(1.0, _trigger_hand(FAR), 1.0, pointing=False) == "pointer_wait"


def test_trigger_reads_thresholds_from_config_and_ignores_bad_values():

    trig = ThumbTrigger()
    trig.configure({"trigger_press_ratio": .1, "trigger_long_press": "x", "trigger_rewind": -1})
    assert trig.settings["trigger_press_ratio"] == .1
    assert trig.settings["trigger_long_press"] == ThumbTrigger.DEFAULTS["trigger_long_press"]
    assert trig.settings["trigger_rewind"] == ThumbTrigger.DEFAULTS["trigger_rewind"]
    assert trig.update(1.0, _trigger_hand(PRESSED), 1.0, pointing=True) == "pointer_wait"
    assert trig.state == "approach"  # .17 is above the stricter .1 press ratio


def _feed(latch, xs, t0=0.0, dt=1 / 30):
    out = []
    for i, x in enumerate(xs):
        out.append(latch.update(t0 + i * dt, (x, .5), .5625))
    return out


def test_move_latch_starts_on_real_motion_and_ignores_tremor():

    latch = MoveLatch()
    jitter = [.5 + (.002 if i % 2 else -.002) for i in range(30)]  # ~0.12 fw/s frame-to-frame
    assert not any(_feed(latch, jitter))
    moving = [.5 + .015 * i for i in range(10)]                     # ~0.45 fw/s
    assert _feed(latch, moving, t0=1.0)[-1] is True


def test_move_latch_holds_until_still_for_the_latch_time():

    latch = MoveLatch()
    _feed(latch, [.3 + .015 * i for i in range(10)])               # moving until t = 0.3
    still = _feed(latch, [.435] * 40, t0=10 / 30)                  # then perfectly still
    times = [10 / 30 + i / 30 for i in range(40)]
    released_at = times[still.index(False)]
    assert .75 <= released_at - 9 / 30 <= .75 + .1 + 2 / 30        # 0.75 s after the last motion (+window)


def test_move_latch_survives_a_short_hand_loss_but_not_a_long_one():

    latch = MoveLatch()
    _feed(latch, [.3 + .015 * i for i in range(10)])
    assert latch.expire(.6) is True                                 # 0.3 s without the hand
    assert latch.expire(1.2) is False                               # > 0.75 s


def test_move_latch_slow_continuation_keeps_it_and_config_is_read():

    latch = MoveLatch()
    _feed(latch, [.3 + .015 * i for i in range(10)])
    slow = [.435 + .004 * i for i in range(60)]                    # ~0.12 fw/s: above "still"
    assert all(_feed(latch, slow, t0=10 / 30))
    latch.configure({"pointer_move_latch": .2, "pointer_still_speed": "bad"})
    assert latch.settings["pointer_move_latch"] == .2
    assert latch.settings["pointer_still_speed"] == MoveLatch.DEFAULTS["pointer_still_speed"]


# ============================================================================ engine core (v0.3 test_pointer_engine.py)

class _P:
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


def _pts(hand):
    return [_P(*p) for p in hand]


def run(core, hands, settings, t0=1.0, active=True):
    out, t = [], t0
    for h in hands:
        out.append(core.step(t, None if h is None else _pts(h), ASPECT, settings, active=active))
        t += DT
    return out, t


def test_tracking_only_always_moves_and_never_clicks():
    core = PointerCore()
    pinch = H.thumb_on(12, .01)                          # thumb on the middle tip
    frames, _ = run(core, [pinch] * 20, PointerSettings(click_style="pinch", tracking_only=True))
    assert {f.action for f in frames} == {"move_cursor"}
    f = frames[-1]
    assert f.cursor_point == pytest.approx(f.pointer_point) and f.click_armed
    assert f.preview_point == pytest.approx(pinch[8][:2])   # index fingertip
    frames, _ = run(PointerCore(), [pinch] * 5, PointerSettings(click_style="off"))
    assert {f.action for f in frames} == {"move_cursor"}


def test_inactive_and_lost_hand_give_no_action_and_disarm_clicks():
    core = PointerCore()
    hand = H.real_hand(curled=False)
    paused, t = run(core, [hand] * 3, PointerSettings(), active=False)
    assert all(f.action is None and f.preview_point for f in paused)
    assert core.step(t, None, ASPECT, PointerSettings(), active=True) is None
    assert core.click_armed is False


def test_palm_anchor_reports_the_palm_point():
    core = PointerCore()
    hand = H.real_hand(curled=False)
    f = core.step(1.0, _pts(hand), ASPECT, PointerSettings(anchor="palm", click_style="off"), active=True)
    assert f.pointer_source == "palm"
    assert f.preview_point == pytest.approx(palm_anchor_point(_pts(hand), {"cursor_anchor_ratio": .5}))


def test_moving_hand_is_latched_and_a_pinch_meanwhile_does_not_click():
    core = PointerCore()
    pinch = H.thumb_on(12, .01)
    moving = [H.shifted(pinch, .02 * i) for i in range(20)]      # 0.6 frame widths / s
    frames, _ = run(core, moving, PointerSettings(click_style="pinch"))
    assert any(f.latched for f in frames)
    assert all(f.action not in ("left_click", "right_click", "pointer_release") for f in frames)


def test_resting_contact_needs_a_release_before_a_pinch_clicks():
    core = PointerCore()
    s = PointerSettings(click_style="pinch")
    rest = H.thumb_on(12, .01)                                  # contact already there
    frames, t = run(core, [rest] * 15, s)
    assert all(f.action not in ("left_click", "pointer_release") for f in frames)
    assert not frames[-1].click_armed
    open_hand = H.real_hand(curled=False)                       # thumb leaves: armed
    frames, t = run(core, [open_hand] * 5, s, t0=t)
    assert frames[-1].click_armed
    frames, t = run(core, [rest] * 6 + [open_hand] * 6, s, t0=t)  # press, then release
    actions = [f.action for f in frames]
    assert "pointer_release" in actions and actions.count("left_click") == 1


def test_trigger_rewind_is_returned_as_a_warp():
    core = PointerCore()
    s = PointerSettings(click_style="trigger")
    hand = H.real_hand(curled=False)
    t = 1.0
    for i in range(10):                                         # idle, cursor history recorded
        core.step(t, _pts(hand), ASPECT, s, active=True)
        core.record_cursor(t, (100 + i, 50))
        t += DT
    assert core.click_armed
    pressed = list(hand)
    pressed[4] = ((hand[5][0] + hand[6][0]) / 2 + .005, (hand[5][1] + hand[6][1]) / 2, 0.0)  # thumb on index side
    f = core.step(t, _pts(pressed), ASPECT, s, active=True)
    assert f.warp is not None and f.warp[0] < 109 and f.trigger_ratio < .28


# ============================================================================ pinch detector (the kept part of v0.3 GestureEngine)

def _pinch_run(pinch, hands, t0=1.0):
    out, t = [], t0
    for h in hands:
        out.append(pinch.process(None if h is None else _pts(h), t))
        t += DT
    return out, t


def _pinch():
    pinch = PointerPinch()
    pinch.config.update(PointerSettings().config)
    return pinch


@pytest.mark.parametrize("finger,click", [(12, "left_click"), (16, "right_click")])
def test_a_held_pinch_clicks_once_and_again_only_after_opening(finger, click):
    pinch = _pinch()
    held = H.thumb_on(finger, .01)
    out, t = _pinch_run(pinch, [held] * 20)
    assert [r.click for r in out].count(click) == 1 and out[-1].pinching
    out, t = _pinch_run(pinch, [H.real_hand(False)] * 4 + [held] * 6, t)
    assert [r.click for r in out].count(click) == 1


def test_a_fist_or_a_thumbs_up_never_clicks():
    pinch = _pinch()
    out, _ = _pinch_run(pinch, [H.real_hand(True)] * 10 + [H.thumbs_up()] * 10)
    assert not any(r.click or r.pinching for r in out)


def test_clicks_are_locked_out_right_after_a_fist_opens():
    pinch = _pinch()
    _, t = _pinch_run(pinch, [H.real_hand(True)] * 8)           # fist confirmed
    out, t = _pinch_run(pinch, [H.thumb_on(12, .01)] * 6, t)    # opens straight into a pinch (0.2 s)
    assert not any(r.click for r in out)


def test_pinch_state_is_forgotten_after_hand_lost_reset():
    pinch = _pinch()
    _, t = _pinch_run(pinch, [H.thumb_on(12, .01)] * 6)
    assert "middle" in pinch.pinched_fingers
    _, t = _pinch_run(pinch, [None] * 5, t)                     # 0.17 s: kept
    assert "middle" in pinch.pinched_fingers
    _pinch_run(pinch, [None] * 6, t)                            # > 0.3 s: forgotten
    assert pinch.pinched_fingers == set()


# ============================================================================ settings

def test_settings_are_prefixed_and_keep_v03_defaults():
    assert all(k.startswith("pointer.") for k in SETTINGS)
    assert SETTINGS["pointer.click_style"] == "pinch" and SETTINGS["pointer.anchor"] == "index"
    assert SETTINGS["pointer.move_latch"] == MoveLatch.DEFAULTS["pointer_move_latch"]
    for key, value in ThumbTrigger.DEFAULTS.items():
        assert SETTINGS["pointer." + key] == value
    # shipped hand-mouse/config/gestures.json values, not the GestureEngine code defaults
    assert SETTINGS["pointer.pinch_threshold"] == .025 and SETTINGS["pointer.release_threshold"] == .045
    assert set(V03_KEYS) <= set(DEFAULTS)


def test_bad_setting_values_fall_back_to_defaults():
    s = from_mapping({"click_style": "laser", "anchor": 3, "axes": (1, 2), "tracking_only": "yes",
                      "move_latch": "x", "trigger_rewind": math.nan, "pointer.pinch_threshold": .03,
                      "unknown": 1})
    assert (s.click_style, s.anchor, s.axes, s.tracking_only) == ("pinch", "index", None, False)
    assert s.config["pointer_move_latch"] == .75 and s.config["trigger_rewind"] == .10
    assert s.config["pinch_threshold"] == .03 and "unknown" not in s.config
    assert from_mapping({"axes": [1, 0, 0, 1], "click_style": "off"}).clicks_off


# ============================================================================ Feature contract (contracts section 5)

def test_feature_declares_its_gestures_bindings_and_protocol():
    feature = PointerFeature()
    assert isinstance(feature, Feature)
    assert feature.id == "pointer" and feature.modes == frozenset({"pointer"})
    assert feature.gestures == GESTURES and len(set(GESTURES)) == len(GESTURES)
    assert all(GESTURE_ID_RE.match(g) and g.startswith("pointer.") for g in GESTURES)
    assert DEFAULT_BINDINGS == {"pointer.move": "mouse.move", "pointer.left_click": "mouse.left_click",
                                "pointer.right_click": "mouse.right_click", "pointer.drag": "mouse.drag"}


def _steps(feature, hands, t0=1.0, **settings):
    intents, t = [], t0
    for n, h in enumerate(hands):
        hand = None if h is None else H.observation(h, t, n)
        intents.append(list(feature.step(hand, H.context(t, n, **settings))))
        t += DT
    return intents, t


def test_hand_none_gives_no_intents_and_no_error():
    feature = PointerFeature()
    out, _ = _steps(feature, [None] * 20)
    assert out == [[]] * 20
    assert feature.activity() == Activity()


def test_pointing_hand_moves_the_cursor_with_its_calibrated_point():
    feature = PointerFeature()
    out, _ = _steps(feature, [H.pointing_hand()] * 3)
    moves = [i for frame in out for i in frame]
    assert moves and all(i.gesture == "pointer.move" and i.phase == "hold" for i in moves)
    assert moves[-1].point == pytest.approx(H.pointing_hand()[8][:2])
    assert moves[-1].source == "pointer" and moves[-1].info["anchor"] == "index"


def test_a_pinch_click_is_one_tap_after_the_release():
    feature = PointerFeature()
    rest, open_hand = H.thumb_on(12, .01), H.real_hand(False)
    out, _ = _steps(feature, [open_hand] * 5 + [rest] * 6 + [open_hand] * 6)
    taps = [(n, i) for n, frame in enumerate(out) for i in frame if i.phase == "tap"]
    assert [i.gesture for _, i in taps] == ["pointer.left_click"]
    assert taps[0][0] > 10 and taps[0][1].point is None          # on the release, in place


def _drag_hands(lose_hand: bool):
    press = H.trigger_hand(.01)
    hands = [H.pointing_hand()] * 30 + [press] * 2                  # still: latch ends, armed, press
    hands += [H.shifted(press, .006 * i) for i in range(1, 12)]     # travel while pressed: drag
    hands += [None] * 3 if lose_hand else [H.shifted(H.pointing_hand(), .066)] * 3
    return hands


@pytest.mark.parametrize("lose_hand", [False, True])
def test_a_trigger_drag_begins_holds_and_ends_also_on_hand_loss(lose_hand):
    feature = PointerFeature()
    out, _ = _steps(feature, _drag_hands(lose_hand), click_style="trigger")
    drag = [i.phase for frame in out for i in frame if i.gesture == "pointer.drag"]
    assert drag[0] == "begin" and drag[-1] == "end" and drag.count("begin") == drag.count("end") == 1
    assert set(drag[1:-1]) == {"hold"}
    warps = [i for frame in out for i in frame if i.info.get("warp")]
    assert len(warps) == 1 and warps[0].gesture == "pointer.move"
    assert feature.activity().holding is False and feature.activity().last_release_t > 1.0


def test_activity_reports_holding_during_a_drag():
    feature = PointerFeature()
    out, _ = _steps(feature, _drag_hands(False)[:-3], click_style="trigger")
    assert any(i.phase == "begin" for frame in out for i in frame)
    assert feature.activity().holding is True and feature.activity().last_release_t == -math.inf


def test_reset_clears_the_state_and_emits_nothing():
    feature = PointerFeature()
    _steps(feature, _drag_hands(False)[:-3], click_style="trigger")
    assert feature.activity().holding
    feature.reset("mode_change")
    assert feature.activity().holding is False and feature.last_frame is None
    core = feature.core
    assert not core.pressing and not core.click_armed and not core.latch.latched
    assert core.trigger.state == "idle" and core.pose.pending is None
    assert core.pinch.pinched_fingers == set()
    # no stray `end` afterwards for the drag the engine already released
    out, _ = _steps(feature, [H.pointing_hand()] * 3, t0=10.0, click_style="trigger")
    assert not any(i.gesture == "pointer.drag" for frame in out for i in frame)


@pytest.mark.parametrize("style", ["pinch", "trigger", "off"])
def test_every_begin_gets_an_end_and_intents_follow_the_contract(style):
    feature = PointerFeature()
    open_drag, begins, t, n = False, 0, 0.0, 0
    for n, (t, pts, active, _d) in enumerate(H.random_frames(24, 3000)):
        if not active and n % 7 == 0:
            feature.reset(random.Random(n).choice(("pause", "mode_change", "settings", "hand_lost")))
            open_drag = False                                    # the engine's release_all closes it
            continue
        hand = None if pts is None else H.observation(pts, t, n)
        for intent in feature.step(hand, H.context(t, n, click_style=style)):
            assert isinstance(intent, Intent) and intent.gesture in GESTURES and intent.t == t
            if intent.gesture == "pointer.drag":
                if intent.phase == "begin":
                    assert not open_drag
                    open_drag, begins = True, begins + 1
                else:
                    assert open_drag and intent.phase in ("hold", "end")
                    open_drag = intent.phase == "hold"
            elif intent.gesture == "pointer.move":
                assert intent.phase == "hold" and intent.point is not None
            else:
                assert intent.phase == "tap"
    if open_drag:                                                 # closed the way a hand loss does
        out = feature.step(None, H.context(t + DT, n + 1, click_style=style))
        assert [i.phase for i in out if i.gesture == "pointer.drag"] == ["end"]
    if style == "trigger":
        assert begins > 0


def test_v03_pointer_keys_convert_to_pointer_settings():
    from manux.settings.migrate_v03 import RENAMES

    for short, v03_key in V03_KEYS.items():
        assert RENAMES[v03_key] == "pointer." + short
    for key in ThumbTrigger.DEFAULTS:
        assert RENAMES[key] == "pointer." + key and "pointer." + key in SETTINGS
