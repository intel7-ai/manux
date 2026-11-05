"""Feature `pinch`: v0.3 behaviours on synthetic hands, and the feature contract (contracts section 5)."""
from __future__ import annotations

import math

import pytest

from manux.actions.bindings import resolve_bindings
from manux.core.contracts import Activity, Feature
from manux.features.pinch import (
    DEFAULT_BINDINGS, DRAG, GESTURE_PHASES, GESTURES, MOVE, MOVE_FIST, SCROLL, SETTINGS, THUMB_MIDDLE, THUMB_PINKY,
    THUMB_RING, THUMB_TUCKED_OPEN, V03_KEYS, PinchFeature,
)
from manux.settings.migrate_v03 import RENAMES

import pinch_hands as H
from pinch_hands import check_pairs, ctx, obs


def run(feature, hands, t0=1.0, fps=30, settings=None):
    """Step every hand (None = no hand); return (all intents, next t)."""
    out, t = [], t0
    for h in hands:
        out.extend(feature.step(obs(t, h) if h is not None else None, ctx(t, settings=settings)))
        t += 1 / fps
    return out, t


def gestures(intents, phase=None):
    return [i.gesture for i in intents if phase is None or i.phase == phase]


# ----------------------------------------------------------------------------- declarations
def test_declarations_fit_the_contract_and_the_catalog():
    f = PinchFeature()
    assert isinstance(f, Feature) and f.id == "pinch" and f.modes == frozenset({"pinch"})
    assert set(GESTURES) == set(GESTURE_PHASES) == set(DEFAULT_BINDINGS)
    assert all(g.startswith("pinch.") for g in GESTURES)
    bindings, problems = resolve_bindings(DEFAULT_BINDINGS, gesture_phases=GESTURE_PHASES)
    assert not problems and set(bindings) == set(GESTURES)
    assert bindings[MOVE_FIST].params["gain"] == 25.0
    assert all(k.startswith("pinch.") for k in SETTINGS)
    assert SETTINGS["pinch.pinch_threshold"] == 0.025 and SETTINGS["pinch.release_threshold"] == 0.045


def test_v03_renames_are_in_the_settings_converter():
    for v03, v04 in V03_KEYS.items():
        assert RENAMES[v03] == v04
        assert v04 in SETTINGS


# ----------------------------------------------------------------------------- v0.3 behaviours
def test_open_hand_moves_the_palm_anchor_and_a_fist_moves_fast():
    f = PinchFeature()
    out, t = run(f, [H.open_hand()] * 3)
    assert gestures(out) == [MOVE] * 3
    assert out[0].phase == "hold" and out[0].point == pytest.approx((0.49, 0.7)) and out[0].info["gain"] == 1.0
    out, _ = run(f, [H.fist()] * 10, t0=t)
    assert MOVE_FIST in gestures(out)                  # after fist_confirm_time (0.12 s)
    assert out[-1].info["hand_shape"] == "fist"


def test_curled_hand_reports_the_hand_shape_gain():
    out, _ = run(PinchFeature(), [H.half_fist()] * 10)
    moves = [i for i in out if i.gesture == MOVE]
    assert moves and moves[-1].info["gain"] > 1.15 and moves[-1].info["hand_shape"] in ("half", "fist")


def test_a_finger_approaching_the_thumb_freezes_the_cursor():
    out, _ = run(PinchFeature(), [H.approach()] * 5)
    assert out == []


def test_thumb_index_grabs_scroll_with_begin_hold_end():
    f = PinchFeature()
    out, t = run(f, [H.scroll_pinch(shift=(0, 0.01 * k)) for k in range(10)])
    assert gestures(out)[:3] == [SCROLL] * 3 and out[0].phase == "begin" and out[1].phase == "hold"
    assert f.activity().holding
    out2, t = run(f, [H.open_hand()] * 15, t0=t)
    ends = [i for i in out2 if i.gesture == SCROLL]
    assert [i.phase for i in ends] == ["end"]          # releasing frames send no hold, then one end
    assert ends[0].point == out[-1].point               # the end carries the last point sent
    check_pairs(out + out2)
    act = f.activity()
    assert not act.holding and act.last_release_t > 1.0
    assert MOVE not in gestures(out2)[:3]               # cursor held still right after a scroll (scroll_cursor_hold)


def test_thumb_middle_clicks_once_per_pinch():
    f = PinchFeature()
    out, t = run(f, [H.middle_pinch()] * 12)            # 0.4 s: past confirm, short of the long press
    assert gestures(out, "tap") == [THUMB_MIDDLE]
    assert f.activity().holding                          # still pressed
    out, t = run(f, [H.open_hand()] * 15 + [H.middle_pinch()] * 5, t0=t)
    assert gestures(out, "tap") == [THUMB_MIDDLE]        # opened and pinched again: one more click


def test_ring_and_pinky_taps():
    out, t = run(PinchFeature(), [H.ring_pinch()] * 6)
    assert gestures(out, "tap") == [THUMB_RING]
    out, _ = run(PinchFeature(), [H.pinky_pinch()] * 6)
    assert gestures(out, "tap") == [THUMB_PINKY]


def test_long_press_on_thumb_middle_becomes_a_drag():
    f = PinchFeature()
    out, t = run(f, [H.middle_pinch(shift=(0.002 * k, 0)) for k in range(45)])
    assert gestures(out, "tap") == [THUMB_MIDDLE]
    drag = [i for i in out if i.gesture == DRAG]
    assert drag[0].phase == "begin" and drag[0].info["gain"] == 15.0
    out2, _ = run(f, [H.open_hand()] * 10, t0=t)
    phases = [i.phase for i in out2 if i.gesture == DRAG]
    assert phases[-1] == "end" and phases.count("end") == 1   # after the release grace
    check_pairs(out + out2)


def test_three_finger_pinch_drags():
    f = PinchFeature()
    out, t = run(f, [H.drag_pinch()] * 6)
    assert DRAG in gestures(out, "begin")
    out2, _ = run(f, [H.open_hand()] * 10, t0=t)
    check_pairs(out + out2)
    assert DRAG in gestures(out2, "end")


def test_thumb_tucked_open_hand_held_still_shows_the_desktop_once():
    out, _ = run(PinchFeature(), [H.desktop_hand()] * 60, t0=5.0)
    assert gestures(out, "tap") == [THUMB_TUCKED_OPEN]


def test_thumbs_up_freezes_the_cursor_and_can_be_switched_off():
    assert run(PinchFeature(), [H.thumbs_up()] * 10)[0] == []
    out, _ = run(PinchFeature(), [H.thumbs_up()] * 10, settings={"motion_pose_freeze": False})
    assert out and set(gestures(out)) <= {MOVE, MOVE_FIST}


def test_disabled_gesture_and_bad_setting_value():
    out, _ = run(PinchFeature(), [H.ring_pinch()] * 6, settings={"enable_thumb_ring_pinch": False})
    assert THUMB_RING not in gestures(out)
    out, _ = run(PinchFeature(), [H.ring_pinch()] * 6, settings={"ring_click_threshold": "x"})
    assert gestures(out, "tap") == [THUMB_RING]          # a bad value falls back to the default


# ----------------------------------------------------------------------------- contract rules
def test_no_hand_and_reset():
    f = PinchFeature()
    assert f.step(None, ctx(1.0)) == [] and f.activity() == Activity()
    out, t = run(f, [H.scroll_pinch()] * 5)
    assert f.activity().holding
    f.reset("settings")
    assert f.activity().holding is False and f.mapper.open is None and f.last_frame is None
    assert not f.core.pinched_fingers
    # after a reset the next scroll starts with a fresh begin (the engine's release_all ended the old one)
    out2, _ = run(f, [H.open_hand()] * 15 + [H.scroll_pinch()] * 3, t0=t + 1)
    assert [i.phase for i in out2 if i.gesture == SCROLL][:1] == ["begin"]


def test_short_hand_loss_keeps_a_drag_until_the_engine_resets():
    f = PinchFeature()
    out, t = run(f, [H.middle_pinch()] * 40)            # long press: dragging
    assert gestures(out, "begin") == [DRAG]
    lost, t = run(f, [None] * 6, t0=t)                  # 0.2 s < app.hand_lost_reset
    assert lost == [] and f.activity().holding
    back, t = run(f, [H.middle_pinch()] * 3, t0=t)
    assert [i.phase for i in back if i.gesture == DRAG] == ["hold"] * 3
    lost, t = run(f, [None] * 10, t0=t)                 # longer: the engine resets the feature
    f.reset("hand_lost")
    assert not f.activity().holding
    again, _ = run(f, [H.open_hand()] * 3, t0=t)
    assert DRAG not in gestures(again)                   # no stray end/hold: release_all closed it
    check_pairs(out + back)


def test_last_release_is_minus_infinity_before_anything_happened():
    assert PinchFeature().activity().last_release_t == -math.inf
