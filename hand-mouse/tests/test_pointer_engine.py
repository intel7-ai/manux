"""PointerEngine: the portable core of the Qt index pointer (landmarks in, intent out)."""
import pytest

from src.gesture_engine import GestureEngine, Point3D
from src.pointer_engine import PointerEngine, PointerSettings
from tests.test_gesture_mouse import _thumb_on, make_real_hand

DT = 1 / 30
ASPECT = 9 / 16


def engine():
    return PointerEngine(GestureEngine())


def shifted(hand, dx):
    return [Point3D(p.x + dx, p.y, p.z) for p in hand]


def run(eng, hands, settings, t0=1.0, active=True):
    out, t = [], t0
    for h in hands:
        out.append(eng.step(t, h, ASPECT, settings, active=active))
        t += DT
    return out, t


def test_tracking_only_always_moves_and_never_clicks():
    eng = engine()
    pinch = _thumb_on(12, .01)                          # thumb on the middle tip
    frames, _ = run(eng, [pinch] * 20, PointerSettings(click_style="pinch", tracking_only=True))
    assert {f.action for f in frames} == {"move_cursor"}
    f = frames[-1]
    assert f.cursor_point == pytest.approx(f.pointer_point) and f.click_armed
    assert f.preview_point == pytest.approx((pinch[8].x, pinch[8].y))   # index fingertip
    frames, _ = run(engine(), [pinch] * 5, PointerSettings(click_style="off"))
    assert {f.action for f in frames} == {"move_cursor"}


def test_inactive_and_lost_hand_give_no_action_and_disarm_clicks():
    eng = engine()
    hand = make_real_hand(curled=False)
    paused, t = run(eng, [hand] * 3, PointerSettings(), active=False)
    assert all(f.action is None and f.preview_point for f in paused)
    assert eng.step(t, None, ASPECT, PointerSettings(), active=True) is None
    assert eng.click_armed is False


def test_palm_anchor_reports_the_palm_point():
    eng = engine()
    hand = make_real_hand(curled=False)
    f = eng.step(1.0, hand, ASPECT, PointerSettings(anchor="palm", click_style="off"), active=True)
    assert f.pointer_source == "palm"
    assert f.preview_point == pytest.approx(eng.gestures.palm_anchor_point(hand))


def test_moving_hand_is_latched_and_a_pinch_meanwhile_does_not_click():
    eng = engine()
    pinch = _thumb_on(12, .01)
    moving = [shifted(pinch, .02 * i) for i in range(20)]      # 0.6 frame widths / s
    frames, _ = run(eng, moving, PointerSettings(click_style="pinch"))
    assert any(f.latched for f in frames)
    assert all(f.action not in ("left_click", "right_click", "pointer_release") for f in frames)


def test_resting_contact_needs_a_release_before_a_pinch_clicks():
    eng = engine()
    s = PointerSettings(click_style="pinch")
    rest = _thumb_on(12, .01)                                  # contact already there
    frames, t = run(eng, [rest] * 15, s)
    assert all(f.action not in ("left_click", "pointer_release") for f in frames)
    assert not frames[-1].click_armed
    open_hand = make_real_hand(curled=False)                    # thumb leaves: armed
    frames, t = run(eng, [open_hand] * 5, s, t0=t)
    assert frames[-1].click_armed
    frames, t = run(eng, [rest] * 6 + [open_hand] * 6, s, t0=t)  # press, then release
    actions = [f.action for f in frames]
    assert "pointer_release" in actions and actions.count("left_click") == 1


def test_trigger_rewind_is_returned_as_a_warp():
    eng = engine()
    s = PointerSettings(click_style="trigger")
    hand = make_real_hand(curled=False)
    t = 1.0
    for i in range(10):                                         # idle, cursor history recorded
        eng.step(t, hand, ASPECT, s, active=True)
        eng.record_cursor(t, (100 + i, 50))
        t += DT
    assert eng.click_armed
    pressed = [Point3D(p.x, p.y, p.z) for p in hand]
    pressed[4] = Point3D((hand[5].x + hand[6].x) / 2 + .005, (hand[5].y + hand[6].y) / 2)  # thumb on index side
    f = eng.step(t, pressed, ASPECT, s, active=True)
    assert f.warp is not None and f.warp[0] < 109 and f.trigger_ratio < .28
