"""
Unit tests for GestureEngine and MouseController (`dry_run=True`).
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.gesture_engine import GestureEngine, GestureResult, Point3D
from src.mouse_controller import MouseController


def make_hand(
    thumb: tuple[float, float] = (0.50, 0.50),
    index: tuple[float, float] = (0.65, 0.50),
    middle: tuple[float, float] = (0.70, 0.50),
    ring: tuple[float, float] = (0.75, 0.50),
    pinky: tuple[float, float] = (0.80, 0.50),
) -> List[Point3D]:
    """Create 21 normalized hand landmarks with configurable fingertip positions."""
    pts = [Point3D(0.5, 0.7, 0.0) for _ in range(21)]
    pts[4] = Point3D(thumb[0], thumb[1], 0.0)
    pts[8] = Point3D(index[0], index[1], 0.0)
    pts[12] = Point3D(middle[0], middle[1], 0.0)
    pts[16] = Point3D(ring[0], ring[1], 0.0)
    pts[20] = Point3D(pinky[0], pinky[1], 0.0)
    return pts


def test_config_file_has_required_fields():
    """The user's config file may hold tuned values: check structure, not numbers."""
    from src.gesture_engine import DEFAULT_CONFIG

    cfg_path = PROJECT_ROOT / "config" / "gestures.json"
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    for key, default in DEFAULT_CONFIG.items():
        if key == "gestures":
            continue
        assert key in data, key
        assert isinstance(data[key], type(default)) or (
            isinstance(default, float) and isinstance(data[key], (int, float))
        ), key
    assert data["release_threshold"] >= data["pinch_threshold"]

    gestures = data["gestures"]
    for gid in DEFAULT_CONFIG["gestures"]:
        assert gid in gestures, gid
    assert gestures["no_pinch"]["action"] == "move_cursor"
    assert gestures["fist"]["action"] == "move_cursor"
    assert gestures["thumb_index_pinch"]["action"] == "grab"
    assert gestures["thumb_middle_pinch"]["action"] == "left_click"
    assert gestures["thumb_ring_pinch"]["action"] == "right_click"
    assert gestures["thumb_index_middle_pinch"]["action"] == "drag_hold"
    assert gestures["thumb_pinky_pinch"]["action"] == "task_switcher"


def test_built_in_defaults():
    from src.gesture_engine import DEFAULT_CONFIG as D

    assert D["pinch_threshold"] == pytest.approx(0.055)
    assert D["movement_mode"] == "relative"
    assert D["relative_gain_min"] == pytest.approx(0.35)
    assert D["relative_gain_max"] == pytest.approx(1.2)
    assert D["fist_dpi_multiplier"] == pytest.approx(25.0)      # open hand 1x ... fist 25x, in between by how far the fingers are curled (2026-11-04)
    assert D["shape_dpi_min"] == pytest.approx(1.0) and D["shape_dpi_enabled"] is True
    assert D["switcher_navigation"] == "pointer"
    assert D["scroll_gain"] == pytest.approx(36000.0)   # 1.5x the original 24000: scrolls further per hand move (2026-11-04 request)
    assert D["switcher_fist_multiplier"] == pytest.approx(15.0)
    from src.gesture_engine import RECORD_FLING_DEFAULT
    assert RECORD_FLING_DEFAULT is False


def test_config_file_ships_with_record_off():
    data = json.loads((PROJECT_ROOT / "config" / "gestures.json").read_text(encoding="utf-8"))
    assert data["record_fling_enabled"] is False


def test_no_pinch_moves_and_thumb_index_pinch_is_grab_with_hysteresis():
    engine = GestureEngine()

    # Open hand, no pinch -> move_cursor at palm anchor (wrist/MCP9 at (0.5, 0.7))
    res0 = engine.process(make_hand(thumb=(0.50, 0.50), index=(0.65, 0.50)), timestamp=1.0)
    assert res0.active_gesture == "no_pinch"
    assert res0.action == "move_cursor"
    assert res0.is_moving is True
    assert res0.cursor_point == pytest.approx((0.50, 0.70))

    # Enter pinch: distance 0.03 <= 0.055 -> grab (palm anchor drives the held cursor)
    res1 = engine.process(make_hand(thumb=(0.50, 0.50), index=(0.53, 0.50)), timestamp=1.1)
    assert res1.active_gesture == "thumb_index_pinch"
    assert res1.action == "grab"
    assert res1.is_moving is True
    assert res1.cursor_point == pytest.approx((0.50, 0.70))

    # Hysteresis zone: 0.068 is > pinch_threshold but <= release_threshold -> stays grab
    res2 = engine.process(make_hand(thumb=(0.50, 0.50), index=(0.568, 0.50)), timestamp=1.2)
    assert res2.active_gesture == "thumb_index_pinch"
    assert res2.action == "grab"

    # Release: distance 0.09 > release_threshold (0.075). A short gap is bridged by the
    # release grace (0.15 s), then the pinch ends and the cursor moves again
    res_gap = engine.process(make_hand(thumb=(0.50, 0.50), index=(0.59, 0.50)), timestamp=1.3)
    assert res_gap.active_gesture == "thumb_index_pinch"
    res3 = engine.process(make_hand(thumb=(0.50, 0.50), index=(0.59, 0.50)), timestamp=1.46)
    assert res3.active_gesture == "no_pinch"
    assert res3.action == "move_cursor"
    assert res3.is_moving is True


def test_cursor_follows_wrist_palm_line_not_fingertips():
    engine = GestureEngine()
    hand = make_hand()
    hand[0] = Point3D(0.40, 0.80)  # wrist
    hand[9] = Point3D(0.60, 0.40)  # middle finger MCP (palm)

    res = engine.process(hand, timestamp=1.0)
    assert res.cursor_point == pytest.approx((0.50, 0.60))  # midpoint (ratio 0.5)

    # Moving fingertips (without pinching) does not move the anchor
    hand2 = make_hand(index=(0.30, 0.20), middle=(0.80, 0.20), ring=(0.85, 0.30), pinky=(0.90, 0.40))
    hand2[0], hand2[9] = hand[0], hand[9]
    assert engine.process(hand2, timestamp=1.1).cursor_point == pytest.approx((0.50, 0.60))

    # Ratio is configurable: 0.0 = wrist, 1.0 = palm (MCP9)
    engine.config["cursor_anchor_ratio"] = 0.0
    assert engine.process(hand, timestamp=1.2).cursor_point == pytest.approx((0.40, 0.80))
    engine.config["cursor_anchor_ratio"] = 1.0
    assert engine.process(hand, timestamp=1.3).cursor_point == pytest.approx((0.60, 0.40))


def test_finger_approaching_thumb_freezes_cursor_before_click():
    engine = GestureEngine()
    # Middle finger 0.06 from thumb: above click_threshold (0.052) but within
    # release_threshold (0.075) -> frozen, no click yet
    res = engine.process(make_hand(middle=(0.56, 0.50)), timestamp=1.0)
    assert res.action == "none"
    assert res.is_moving is False


def test_drag_hold_priority_and_click_cooldowns():
    engine = GestureEngine()

    # Thumb + Index + Middle all pinched (held past confirm time) -> drag_hold over grab
    drag_hand = make_hand(thumb=(0.50, 0.50), index=(0.52, 0.50), middle=(0.52, 0.51))
    assert engine.process(drag_hand, timestamp=1.93).action == "grab"  # not confirmed yet
    res_drag = engine.process(drag_hand, timestamp=2.0)
    assert res_drag.active_gesture == "thumb_index_middle_pinch"
    assert res_drag.action == "drag_hold"
    assert res_drag.is_moving is True

    # Release index and middle (grace 0.15 s, then the post-grab click lockout 0.35 s)
    open_hand = make_hand(thumb=(0.50, 0.50), index=(0.70, 0.50), middle=(0.70, 0.50))
    engine.process(open_hand, timestamp=2.5)
    assert engine.process(open_hand, timestamp=2.66).active_gesture == "no_pinch"
    click_hand = make_hand(thumb=(0.50, 0.50), index=(0.70, 0.50), middle=(0.52, 0.50))

    # First frame only starts the confirmation timer
    assert engine.process(click_hand, timestamp=3.1).action == "none"
    click1 = engine.process(click_hand, timestamp=3.17)
    assert click1.active_gesture == "thumb_middle_pinch"
    assert click1.action == "left_click"
    assert click1.is_moving is False

    # Within the 0.35s cooldown -> debounced; cursor stays frozen
    click_debounced = engine.process(click_hand, timestamp=3.27)
    assert click_debounced.action == "none"
    assert click_debounced.is_moving is False

    # Still held long after any cooldown -> never repeats (one pinch = one click)
    held = [engine.process(click_hand, timestamp=3.55 + i / 30).action for i in range(30)]
    assert "left_click" not in held
    # Open the fingers (long enough to end the long-press drag), pinch again -> clicks again
    t = 4.6
    for _ in range(8):
        engine.process(make_hand(), timestamp=t)
        t += 1 / 30
    again = [engine.process(click_hand, timestamp=t + i / 30).action for i in range(3)]
    assert again.count("left_click") == 1


def test_right_click_and_drag_toggle_and_customization(tmp_path: Path):
    cfg_file = tmp_path / "custom_gestures.json"
    engine = GestureEngine(config_path=cfg_file)

    ring_hand = make_hand(thumb=(0.50, 0.50), index=(0.70, 0.50), ring=(0.52, 0.50))
    engine.process(ring_hand, timestamp=5.0)
    rc = engine.process(ring_hand, timestamp=5.07)
    assert rc.active_gesture == "thumb_ring_pinch"
    assert rc.action == "right_click"

    pinky_hand = make_hand(thumb=(0.50, 0.50), index=(0.70, 0.50), pinky=(0.52, 0.50))
    engine.process(pinky_hand, timestamp=6.0)
    dt = engine.process(pinky_hand, timestamp=6.07)
    assert dt.active_gesture == "thumb_pinky_pinch"
    assert dt.action == "task_switcher"

    # Customize gesture mapping & movement mode and persist
    engine.set_gesture_action("thumb_ring_pinch", "left_click", enabled=True)
    engine.set_movement_mode("absolute")
    engine.save_config()

    reloaded = GestureEngine(config_path=cfg_file)
    assert reloaded.config["movement_mode"] == "absolute"
    assert reloaded.config["gestures"]["thumb_ring_pinch"]["action"] == "left_click"


def test_single_frame_middle_pinch_does_not_click():
    engine = GestureEngine()
    actions = [engine.process(make_hand(middle=(0.52, 0.50)), timestamp=1.0).action]  # one noisy frame
    actions += [engine.process(make_hand(), timestamp=1.0 + i / 30).action for i in range(1, 6)]
    assert "left_click" not in actions


def test_middle_ring_pinky_pinch_survive_flicker():
    """These used to flicker: jitter across the threshold / a 1-frame dropout / the
    neighbour finger drifting close all reset the confirmation every frame."""
    for finger, tip_name, action, jitter in (
        ("middle", "middle", "left_click", [0.050, 0.056, 0.049, 0.060, 0.051, 0.058]),  # around 0.052
        ("ring", "ring", "right_click", [0.042, 0.048, 0.041, 0.060, 0.043, 0.050]),     # around 0.044
    ):
        engine = GestureEngine()
        t, actions = 1.0, []
        for k, dist in enumerate(jitter):
            hand = make_hand(**{tip_name: (0.50 + dist, 0.50)})
            if k == 3:
                hand = make_hand()  # 1-frame tracking dropout
            actions.append(engine.process(hand, timestamp=t).action)
            t += 1 / 30
        assert actions.count(action) == 1, (finger, actions)
    # Pinky: neighbour (ring) drifts close after the pinch was recognized -> still held
    engine = GestureEngine()
    hand = make_thumb_pinky_hand()
    out, t = _run(engine, [hand] * 3)
    assert "task_switcher" in [r.action for r in out]
    drift = make_thumb_pinky_hand()
    drift[16] = Point3D(drift[4].x + 0.012, drift[4].y)  # ring tip now about as close as the pinky
    engine.process(drift, timestamp=t)
    assert "pinky" in engine._secondary_since  # pinch kept (dominance is checked on entry only)


def test_forming_thumb_index_pinch_with_middle_nearby_is_not_a_left_click():
    """Closing into a thumb+index pinch drags the middle finger near the thumb too."""
    engine = GestureEngine()
    t = 1.0
    # Index 0.060 (not yet pinched: > 0.055) and middle 0.045 (< click 0.052) for several
    # frames: middle is not clearly closer than index (0.045 >= 0.7 * 0.060) -> no click
    for _ in range(5):
        res = engine.process(make_hand(index=(0.56, 0.50), middle=(0.545, 0.50)), timestamp=t)
        assert res.action != "left_click"
        t += 1 / 30
    # Then the index pinch lands with middle still nearby (0.045) -> grab, never a
    # drag_hold (its mouseDown/up would look like a click)
    for _ in range(5):
        res = engine.process(make_hand(index=(0.53, 0.50), middle=(0.545, 0.50)), timestamp=t)
        assert res.action == "grab"
        t += 1 / 30


def test_no_left_click_right_after_thumb_index_pinch_ends():
    """Snap noise / release: index pinch drops while the middle finger is still close."""
    engine = GestureEngine()
    t = 1.0
    engine.process(make_hand(index=(0.53, 0.50), middle=(0.60, 0.50)), timestamp=t)  # grab
    # Index jumps away (> release 0.075) while middle is right at the thumb
    for _ in range(9):  # 0.3 s < lockout 0.35 s
        t += 1 / 30
        res = engine.process(make_hand(index=(0.60, 0.50), middle=(0.52, 0.50)), timestamp=t)
        assert res.action != "left_click"
    # A deliberate middle pinch after grace + lockout still clicks
    t += 0.4
    engine.process(make_hand(index=(0.70, 0.50), middle=(0.52, 0.50)), timestamp=t)
    t += 0.07
    assert engine.process(make_hand(index=(0.70, 0.50), middle=(0.52, 0.50)), timestamp=t).action == "left_click"


def test_mouse_controller_absolute_and_relative_and_drag_safety():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    cfg = {
        "smoothing": 1.0,  # full step for exact deterministic assertion
        "active_margin": 0.15,
        "movement_mode": "absolute",
        "relative_sensitivity": 2.0,
    }

    # Center of frame (0.5, 0.5) -> center of screen (~960, ~540)
    move_res = GestureResult(
        active_gesture="thumb_index_pinch",
        action="move_cursor",
        cursor_point=(0.5, 0.5),
        pinch_distance=0.02,
        distances={"index": 0.02},
        is_moving=True,
    )
    pos = ctrl.apply(move_res, cfg)
    assert abs(pos[0] - 960) <= 1
    assert abs(pos[1] - 540) <= 1

    # Drag hold engages mouseDown and releases automatically when gesture changes
    drag_res = GestureResult(
        active_gesture="thumb_index_middle_pinch",
        action="drag_hold",
        cursor_point=(0.6, 0.6),
        pinch_distance=0.02,
        distances={"index": 0.02, "middle": 0.02},
        is_moving=True,
    )
    ctrl.apply(drag_res, cfg)
    assert ctrl.is_dragging is True

    # Switching back to move_cursor releases drag_hold automatically
    ctrl.apply(move_res, cfg)
    assert ctrl.is_dragging is False

    # Relative mode: clutching behavior
    cfg["movement_mode"] = "relative"
    ctrl.apply(
        GestureResult(active_gesture="NONE", action="none", cursor_point=None, is_moving=False),
        cfg,
    )
    assert ctrl.anchor_hand_pos is None

    # First pinch sets anchor at current screen pos
    start_pos = ctrl.last_screen_pos
    ctrl.apply(
        GestureResult(
            active_gesture="thumb_index_pinch",
            action="move_cursor",
            cursor_point=(0.30, 0.30),
            pinch_distance=0.02,
            is_moving=True,
        ),
        cfg,
    )
    assert ctrl.anchor_hand_pos == pytest.approx((0.30, 0.30))
    assert ctrl.last_screen_pos == start_pos

    # Moving hand +0.05 right while pinched moves cursor positively
    new_pos = ctrl.apply(
        GestureResult(
            active_gesture="thumb_index_pinch",
            action="move_cursor",
            cursor_point=(0.35, 0.30),
            pinch_distance=0.02,
            is_moving=True,
        ),
        cfg,
    )
    assert new_pos[0] > start_pos[0]

    # Drag toggle + release_all safety check
    ctrl.apply(
        GestureResult(
            active_gesture="thumb_pinky_pinch",
            action="drag_toggle",
            cursor_point=(0.35, 0.30),
            is_moving=False,
        ),
        cfg,
    )
    assert ctrl.is_dragging is True
    ctrl.release_all()
    assert ctrl.is_dragging is False
    assert ctrl.anchor_hand_pos is None



def _res(action: str, pt: tuple[float, float]) -> GestureResult:
    return GestureResult(active_gesture="x", action=action, cursor_point=pt, is_moving=True)


def _grab(pinch: tuple[float, float]) -> GestureResult:
    return GestureResult(active_gesture="thumb_index_pinch", action="grab",
                         cursor_point=(0.5, 0.7), pinch_point=pinch, is_moving=True)


NONE = GestureResult(action="none", is_moving=False)
FRAME = 1 / 30
ABS_DPI_X = 1919 / 0.40  # screen px per normalized unit with active_margin 0.30

REC_CFG = {
    "smoothing": 1.0,
    "active_margin": 0.30,
    "movement_mode": "absolute",
    "fling_strength": 1.0,
    "record_gain_min": 1.0,
    "record_gain_max": 4.0,
    "record_speed_low": 0.2,
    "record_speed_high": 1.2,
    "record_smoothing": 1.0,  # exact paths in tests
    "record_release_trim": 0.0,
    "record_min_length": 0.01,
    "replay_min_duration": 0.15,
    "replay_max_duration": 0.45,
}


def _ctrl(x: int = 400, y: int = 300, aspect: float = 0.75) -> MouseController:
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.last_screen_pos = (x, y)
    ctrl.frame_aspect = aspect
    return ctrl


def _stroke(start, delta, frames):
    """Points from start moving by delta in `frames` equal steps (start included)."""
    return [(start[0] + delta[0] * i / frames, start[1] + delta[1] * i / frames) for i in range(frames + 1)]


def _record(ctrl, points, cfg=REC_CFG, t0: float = 1.0):
    t = t0
    for pt in points:
        ctrl.apply(_grab(pt), cfg, now=t)
        t += FRAME
    ctrl.apply(NONE, cfg, now=t)  # release -> replay starts
    return t


def _replay(ctrl, t, cfg=REC_CFG, result=NONE):
    """Tick the replay to the end; return the cursor positions seen."""
    seen = [ctrl.last_screen_pos]
    for _ in range(60):
        if not ctrl.is_playing:
            break
        t += FRAME
        ctrl.apply(result, cfg, now=t)
        seen.append(ctrl.last_screen_pos)
    return t, seen


def test_recording_keeps_cursor_still_and_records_path():
    ctrl = _ctrl()
    t = 1.0
    for pt in _stroke((0.5, 0.5), (0.1, 0.0), 10):
        ctrl.apply(_grab(pt), REC_CFG, now=t)
        t += FRAME
    assert ctrl.last_screen_pos == (400, 300)
    assert len(ctrl.recorded_path) == 11
    assert ctrl.fling_count == 0 and not ctrl.is_playing


def test_release_replays_drawn_path_shape_with_visible_motion():
    ctrl = _ctrl(400, 300, aspect=0.75)
    # Slow L-shape: right 0.1, then down 0.1 (0.15 frame-widths/s -> min gain 1.0)
    path = _stroke((0.5, 0.5), (0.1, 0.0), 20) + _stroke((0.6, 0.5), (0.0, 0.1), 20)[1:]
    t = _record(ctrl, path)
    assert ctrl.is_playing and ctrl.fling_count == 1
    t, seen = _replay(ctrl, t)

    end = seen[-1]
    assert end[0] - 400 == pytest.approx(0.1 * 1920, abs=3)          # right leg
    assert end[1] - 300 == pytest.approx(0.1 * 0.75 * 1920, abs=3)   # down leg (physical)
    # The corner of the L is passed through (path is drawn, not a straight jump)
    assert any(abs(x - end[0]) <= 15 and abs(y - 300) <= 15 for x, y in seen)
    # Animated: several intermediate positions, no single teleport step
    steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(seen, seen[1:])]
    assert len([s for s in steps if s > 0]) >= 5
    assert max(steps) < 0.5 * (192 + 144)


def test_fast_stroke_is_amplified_more_than_slow_stroke():
    slow, fast = _ctrl(100, 540), _ctrl(100, 540)
    _replay(slow, _record(slow, _stroke((0.5, 0.5), (0.1, 0.0), 20)))  # 0.15/s -> x1
    _replay(fast, _record(fast, _stroke((0.5, 0.5), (0.1, 0.0), 2)))   # 1.5/s  -> x4
    d_slow = slow.last_screen_pos[0] - 100
    d_fast = fast.last_screen_pos[0] - 100
    assert d_slow == pytest.approx(192, abs=3)
    assert d_fast == pytest.approx(min(1819, 4 * 192), abs=3)


def test_replay_duration_is_bounded():
    ctrl = _ctrl(100, 540)
    long_path = _stroke((0.5, 0.5), (0.1, 0.0), 60)  # 2 s recording
    t = _record(ctrl, long_path)
    t_start = t
    t, _ = _replay(ctrl, t)
    assert t - t_start <= 0.45 + 2 * FRAME


def test_release_trim_drops_finger_opening_jump():
    cfg = dict(REC_CFG, record_release_trim=0.06)
    ctrl = _ctrl(400, 300)
    path = _stroke((0.5, 0.5), (0.1, 0.0), 20) + [(0.6, 0.45)]  # last frame: fingers open
    _replay(ctrl, _record(ctrl, path, cfg=cfg), cfg=cfg)
    assert ctrl.last_screen_pos[1] == 300  # the upward release jitter was trimmed


def test_tiny_path_is_ignored():
    ctrl = _ctrl()
    _record(ctrl, _stroke((0.5, 0.5), (0.004, 0.0), 5))
    assert ctrl.fling_count == 0 and not ctrl.is_playing
    assert ctrl.last_screen_pos == (400, 300)


def test_new_pinch_stops_replay_in_place():
    ctrl = _ctrl(100, 540)
    t = _record(ctrl, _stroke((0.5, 0.5), (0.1, 0.0), 2))
    t += FRAME
    ctrl.apply(NONE, REC_CFG, now=t)
    mid = ctrl.last_screen_pos
    assert ctrl.is_playing and mid[0] > 100
    t += FRAME
    ctrl.apply(_grab((0.3, 0.3)), REC_CFG, now=t)  # catch
    assert not ctrl.is_playing
    t += FRAME
    ctrl.apply(_grab((0.3, 0.3)), REC_CFG, now=t)
    assert ctrl.last_screen_pos == mid


def test_hand_movement_is_ignored_during_replay():
    a, b = _ctrl(400, 300), _ctrl(400, 300)
    path = _stroke((0.5, 0.5), (0.1, 0.0), 10)
    _replay(a, _record(a, path))
    _replay(b, _record(b, path), result=_res("move_cursor", (0.9, 0.9)))
    assert a.last_screen_pos == b.last_screen_pos


def test_hand_lost_mid_recording_still_replays():
    ctrl = _ctrl(400, 300)
    t = 1.0
    for pt in _stroke((0.5, 0.5), (0.1, 0.0), 10):
        ctrl.apply(_grab(pt), REC_CFG, now=t)
        t += FRAME
    ctrl.hand_lost(REC_CFG, now=t)  # blurred out
    assert ctrl.is_playing
    for _ in range(30):
        t += FRAME
        ctrl.hand_lost(REC_CFG, now=t)
    assert not ctrl.is_playing and ctrl.last_screen_pos[0] > 400


def test_absolute_keeps_offset_after_replay():
    ctrl = _ctrl(400, 300)
    t, _ = _replay(ctrl, _record(ctrl, _stroke((0.5, 0.5), (0.1, 0.0), 10)))
    rest = ctrl.last_screen_pos
    t += FRAME
    assert ctrl.apply(_res("move_cursor", (0.45, 0.5)), REC_CFG, now=t) == rest
    t += FRAME
    moved = ctrl.apply(_res("move_cursor", (0.46, 0.5)), REC_CFG, now=t)
    assert moved[0] - rest[0] == pytest.approx(0.01 * ABS_DPI_X, abs=2)


def test_fling_strength_scales_replay():
    a, b = _ctrl(100, 540), _ctrl(100, 540)
    path = _stroke((0.5, 0.5), (0.1, 0.0), 20)
    _replay(a, _record(a, path))
    cfg2 = dict(REC_CFG, fling_strength=2.0)
    _replay(b, _record(b, path, cfg=cfg2), cfg=cfg2)
    assert (b.last_screen_pos[0] - 100) == pytest.approx(2 * (a.last_screen_pos[0] - 100), abs=3)


def test_smooth_thread_animates_to_the_end():
    ctrl = _ctrl(400, 300)
    ctrl.smooth_playback = True  # real-time 240 Hz thread (still dry_run: no OS events)
    _record(ctrl, _stroke((0.5, 0.5), (0.1, 0.0), 10))
    deadline = time.perf_counter() + 2.0
    while ctrl.is_playing and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert not ctrl.is_playing
    # 0.3 frame-widths/s -> gain 1.0 + 3.0 * (0.3 - 0.2) / 1.0 = 1.3
    assert ctrl.last_screen_pos[0] - 400 == pytest.approx(0.1 * 1920 * 1.3, abs=5)


def test_click_freeze_does_not_reset_absolute_mapping():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_res("move_cursor", (0.5, 0.5)), REC_CFG)
    ctrl.apply(GestureResult(action="left_click", cursor_point=(0.5, 0.5), is_moving=False), REC_CFG)
    assert ctrl.abs_offset == (0.0, 0.0)
    assert ctrl.apply(_res("move_cursor", (0.70, 0.70)), REC_CFG) == (1919, 1079)


REL_CFG = dict(REC_CFG, movement_mode="relative", smoothing=1.0,
               relative_gain_min=0.6, relative_gain_max=2.0,
               relative_accel_speed_low=0.1, relative_accel_speed_high=0.8)


def _rel_move(ctrl, speed: float, frames: int = 10):
    t, x = 0.0, 0.5
    start = ctrl.apply(_res("move_cursor", (x, 0.5)), REL_CFG, now=t)
    for _ in range(frames):
        t += FRAME
        x += speed * FRAME
        ctrl.apply(_res("move_cursor", (x, 0.5)), REL_CFG, now=t)
    return ctrl.last_screen_pos[0] - start[0], x - 0.5


def test_relative_fine_control_has_pointer_acceleration():
    slow_px, slow_hand = _rel_move(MouseController(dry_run=True, screen_size=(1920, 1080)), 0.05)
    fast_px, fast_hand = _rel_move(MouseController(dry_run=True, screen_size=(1920, 1080)), 1.5, frames=4)
    assert slow_px / (slow_hand * 1920) == pytest.approx(0.6, abs=0.05)
    assert fast_px / (fast_hand * 1920) == pytest.approx(2.0, abs=0.1)


def test_relative_starts_from_landing_point_after_replay():
    ctrl = _ctrl(400, 300)
    t, _ = _replay(ctrl, _record(ctrl, _stroke((0.5, 0.5), (0.1, 0.0), 10), cfg=REL_CFG), cfg=REL_CFG)
    landed = ctrl.last_screen_pos
    t += FRAME
    assert ctrl.apply(_res("move_cursor", (0.2, 0.8)), REL_CFG, now=t) == landed


def test_engine_reports_pinch_point_for_grab():
    engine = GestureEngine()
    res = engine.process(make_hand(thumb=(0.50, 0.50), index=(0.53, 0.50)), timestamp=1.0)
    assert res.action == "grab"
    assert res.pinch_point == pytest.approx((0.515, 0.50))
    assert res.cursor_point == pytest.approx((0.50, 0.70))


def make_real_hand(curled: bool, shift: tuple[float, float] = (0.0, 0.0)) -> List[Point3D]:
    """Hand with real joint geometry: wrist below, MCPs above it, tips extended or curled."""
    sx, sy = shift
    pts = [Point3D(0.5 + sx, 0.8 + sy) for _ in range(21)]
    mcp_x = (0.44, 0.48, 0.52, 0.56)
    for (mcp_i, tip_i), x in zip(GestureEngine.FINGER_JOINTS, mcp_x):
        pts[mcp_i] = Point3D(x + sx, 0.60 + sy)
        pts[tip_i - 1] = Point3D(x + sx, (0.66 if curled else 0.50) + sy)  # DIP
        pts[tip_i] = Point3D(x + sx, (0.68 if curled else 0.40) + sy)
    # Thumb tip: in a fist it rests right next to the curled index tip (would read as a pinch)
    pts[4] = Point3D((0.465 if curled else 0.36) + sx, (0.67 if curled else 0.62) + sy)
    return pts


def test_fist_is_detected_and_overrides_pinches():
    engine = GestureEngine()
    open_res = engine.process(make_real_hand(curled=False), timestamp=1.0)
    assert open_res.active_gesture == "no_pinch" and open_res.dpi_multiplier == 1.0

    t, results = 1.0, []
    for _ in range(8):  # held fist: thumb touching index/middle never clicks or grabs
        t += 1 / 30
        results.append(engine.process(make_real_hand(curled=True), timestamp=t))
    assert all(r.action not in ("left_click", "right_click", "grab", "task_switcher") for r in results)
    fists = [r for r in results if r.active_gesture == "fist"]
    assert len(fists) >= 4                       # after fist_confirm_time (0.12 s)
    assert all(r.action == "move_cursor" and r.is_moving for r in fists)
    assert all(r.dpi_multiplier == pytest.approx(25.0) for r in fists)

    # Open the hand again -> back to normal movement, and no click from the fingers opening
    for _ in range(6):
        t += 1 / 30
        res = engine.process(make_real_hand(curled=False), timestamp=t)
        assert res.action not in ("left_click", "right_click", "drag_toggle", "grab", "task_switcher")
    assert res.active_gesture == "no_pinch" and res.dpi_multiplier == 1.0


def _loose_fist(ratio_tip_y: float) -> List[Point3D]:
    h = make_real_hand(curled=True)
    for _, tip_i in GestureEngine.FINGER_JOINTS:
        h[tip_i] = Point3D(h[tip_i].x, ratio_tip_y)
    return h


def test_fist_hysteresis_and_disable():
    engine = GestureEngine()
    fist = make_real_hand(curled=True)
    out, t = _run(engine, [fist] * 6)
    assert out[-1].active_gesture == "fist"
    # Slightly loosened (ratio ~1.1: between enter 0.95 and exit 1.2) -> stays a fist
    loose = _loose_fist(0.585)
    assert engine.process(loose, timestamp=t).active_gesture == "fist"
    # Fresh engine: the same loose hand is NOT enough to enter a fist
    out, _ = _run(GestureEngine(), [loose] * 10)
    assert all(r.active_gesture != "fist" for r in out)

    engine2 = GestureEngine()
    engine2.config["gestures"]["fist"]["enabled"] = False
    out, _ = _run(engine2, [fist] * 10)
    assert all(r.active_gesture != "fist" for r in out)


def _fist_res(pt, mult):
    return GestureResult(active_gesture="fist" if mult != 1.0 else "no_pinch", action="move_cursor",
                         cursor_point=pt, is_moving=True, dpi_multiplier=mult)


def test_fist_multiplies_relative_movement():
    def run(mult):
        ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
        t, x = 0.0, 0.5
        start = ctrl.apply(_fist_res((x, 0.5), mult), REL_CFG, now=t)
        for _ in range(10):
            t += FRAME
            x += 0.05 * FRAME  # slow -> min gain
            ctrl.apply(_fist_res((x, 0.5), mult), REL_CFG, now=t)
        return ctrl.last_screen_pos[0] - start[0]

    assert run(3.0) == pytest.approx(3 * run(1.0), abs=2)


def test_fist_triples_absolute_movement_without_jumps():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    p0 = ctrl.apply(_fist_res((0.40, 0.5), 1.0), REC_CFG, now=0.0)
    # Make a fist at the same spot -> no jump
    p1 = ctrl.apply(_fist_res((0.40, 0.5), 3.0), REC_CFG, now=0.03)
    assert p1 == p0
    # Fist move of 0.02 -> 3x the normal absolute motion
    p2 = ctrl.apply(_fist_res((0.42, 0.5), 3.0), REC_CFG, now=0.06)
    assert p2[0] - p1[0] == pytest.approx(3 * 0.02 * ABS_DPI_X, abs=2)
    # Open the hand at the same spot -> no jump, then normal 1x motion
    p3 = ctrl.apply(_fist_res((0.42, 0.5), 1.0), REC_CFG, now=0.09)
    assert p3 == p2
    p4 = ctrl.apply(_fist_res((0.43, 0.5), 1.0), REC_CFG, now=0.12)
    assert p4[0] - p3[0] == pytest.approx(0.01 * ABS_DPI_X, abs=2)



def test_pinch_survives_short_tracking_dropouts():
    engine = GestureEngine()
    t = 1.0
    pinched = make_hand(index=(0.53, 0.50))
    dropout = make_hand(index=(0.62, 0.50))  # tip landmark jumps away for a moment
    assert engine.process(pinched, timestamp=t).action == "grab"
    # Repeated 2-frame dropouts (67 ms < 0.15 s grace), each followed by a settled pinch (2 frames),
    # never break the pinch. (One lone good frame between them does not restart the release timer.)
    for _ in range(4):
        for hand in (dropout, dropout, pinched, pinched):
            t += 1 / 30
            assert engine.process(hand, timestamp=t).action == "grab"
    # A real release (longer than the grace) ends it
    for _ in range(6):
        t += 1 / 30
        res = engine.process(dropout, timestamp=t)
    assert res.active_gesture == "no_pinch"


def test_pinch_held_when_tips_drift_but_neighbor_joints_stay_close():
    """Camera angle: the index tip hides behind the thumb and drifts, its DIP stays close."""
    engine = GestureEngine()
    engine.config["pinch_release_grace"] = 0.0  # isolate the joint check
    hand = make_hand(index=(0.53, 0.50))
    assert engine.process(hand, timestamp=1.0).action == "grab"
    angled = make_hand(index=(0.60, 0.50))  # tip-tip 0.10 > release 0.075
    angled[7] = Point3D(0.54, 0.50)         # index DIP still 0.04 from the thumb tip
    for i in range(5):
        assert engine.process(angled, timestamp=1.1 + i * 0.05).action == "grab"
    angled[7] = Point3D(0.62, 0.50)         # really opened
    assert engine.process(angled, timestamp=1.5).active_gesture == "no_pinch"


def test_replay_trims_the_release_grace_period():
    cfg = dict(REC_CFG, record_release_trim=0.0, pinch_release_grace=0.15)
    ctrl = _ctrl(400, 300)
    # 0.5 s stroke right, then 0.15 s of hand drifting down while the grace runs
    path = _stroke((0.5, 0.5), (0.1, 0.0), 15) + _stroke((0.6, 0.5), (0.0, 0.05), 5)[1:]
    _replay(ctrl, _record(ctrl, path, cfg=cfg), cfg=cfg)
    assert abs(ctrl.last_screen_pos[1] - 300) <= 20  # the post-release drift is not replayed
    assert ctrl.last_screen_pos[0] > 500



def test_alt_tab_action_switches_windows_and_releases_held_button():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(GestureResult(action="drag_toggle", is_moving=False), {})
    assert ctrl.is_dragging
    pos = ctrl.last_screen_pos
    ctrl.apply(GestureResult(active_gesture="thumb_pinky_pinch", action="alt_tab", is_moving=False), {})
    assert "alt_tab" in ctrl.event_log
    assert ctrl.is_dragging is False          # never leave the mouse button held across windows
    assert ctrl.last_screen_pos == pos       # cursor does not move



def _sw(gesture: str, action: str, pt=(0.5, 0.5)) -> GestureResult:
    return GestureResult(active_gesture=gesture, action=action, cursor_point=pt,
                         is_moving=action == "move_cursor")


SW_CFG = {"switcher_navigation": "arrows", "switcher_step": 0.05, "switcher_timeout": 15.0,
          "switcher_lost_confirm": 1.0}
PTR_CFG = dict(SW_CFG, switcher_navigation="pointer", movement_mode="relative", smoothing=1.0,
               relative_gain_min=0.35, relative_gain_max=1.2,
               relative_accel_speed_low=0.1, relative_accel_speed_high=0.8)


def test_task_switcher_open_navigate_confirm():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    pos = ctrl.last_screen_pos
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=1.0)
    assert ctrl.switcher_open and "switcher_open" in ctrl.event_log

    # Still holding the opening pinch (repeat fires) -> must not confirm immediately
    ctrl.apply(_sw("NONE", "none"), SW_CFG, now=1.1)
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=1.7)
    assert ctrl.switcher_open

    # Open hand: move right 2 steps, then left 1 step; cursor never moves
    t = 2.0
    for x in (0.50, 0.53, 0.56, 0.605, 0.55):
        ctrl.apply(_sw("no_pinch", "move_cursor", (x, 0.5)), SW_CFG, now=t)
        t += 0.05
    assert ctrl.switcher_moves == ["right", "right", "left"]
    assert ctrl.last_screen_pos == pos

    # Down 1 step (vertical uses physical units: 0.05 / 0.75 normalized)
    ctrl.apply(_sw("no_pinch", "move_cursor", (0.55, 0.5 + 0.05 / 0.75 + 0.001)), SW_CFG, now=t)
    assert ctrl.switcher_moves[-1] == "down"

    # Thumb + middle (left click gesture) confirms instead of clicking
    ctrl.apply(_sw("thumb_middle_pinch", "left_click"), SW_CFG, now=t + 0.1)
    assert ctrl.switcher_open  # waits switcher_confirm_delay (0.25 s) in case it is a fist forming
    ctrl.apply(_sw("NONE", "none"), SW_CFG, now=t + 0.4)
    assert not ctrl.switcher_open
    assert "switcher_confirm" in ctrl.event_log and "left_click" not in ctrl.event_log


def test_task_switcher_fist_cancels_and_safety_closes():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=1.0)
    ctrl.apply(_sw("no_pinch", "move_cursor"), SW_CFG, now=1.2)
    ctrl.apply(_sw("fist", "move_cursor"), SW_CFG, now=1.3)
    assert not ctrl.switcher_open and "switcher_cancel" in ctrl.event_log

    # Hand lost for > 1 s -> auto-confirm (Alt never stays held)
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=2.0)
    ctrl.hand_lost(SW_CFG, now=2.5)
    assert ctrl.switcher_open
    ctrl.hand_lost(SW_CFG, now=3.1)
    assert not ctrl.switcher_open

    # Timeout
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=10.0)
    ctrl.apply(_sw("no_pinch", "move_cursor"), SW_CFG, now=26.0)
    assert not ctrl.switcher_open

    # release_all (pause / quit) always closes it
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=30.0)
    ctrl.release_all()
    assert not ctrl.switcher_open


def test_fist_detected_before_cancel_does_not_leak_into_movement():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), SW_CFG, now=1.0)
    ctrl.apply(_sw("no_pinch", "move_cursor", (0.5, 0.5)), SW_CFG, now=1.1)
    ctrl.apply(_sw("fist", "move_cursor", (0.5, 0.5)), SW_CFG, now=1.2)
    pos = ctrl.last_screen_pos
    # After closing, movement re-anchors where the hand is: no jump
    assert ctrl.apply(_sw("no_pinch", "move_cursor", (0.3, 0.3)), REL_CFG, now=1.3) == pos



def test_record_pinch_never_turns_into_fist_or_drag_when_fingers_curl():
    """Pinching curls the other fingers and bends the index: looks like a fist / drag."""
    engine = GestureEngine()
    t = 1.0
    # Thumb tip on the index tip (outer side), other fingers loose -> grab starts
    start = make_real_hand(curled=False)
    start[4] = Point3D(start[8].x - 0.02, start[8].y)
    assert engine.process(start, timestamp=t).action == "grab"
    for _ in range(12):  # recording for 0.4 s with a normal pinch
        t += 1 / 30
        assert engine.process(start, timestamp=t).action == "grab"
    # Mid-recording every finger curls (fist geometry) and the middle tip drifts right
    # onto the thumb -> neither fast move (fist) nor drag takes over the recording
    for i in range(15):
        t += 1 / 30
        curled = make_real_hand(curled=True, shift=(0.01 * i, 0.0))
        curled[4] = Point3D(curled[8].x - 0.02, curled[8].y)
        curled[12] = Point3D(curled[4].x + 0.01, curled[4].y + 0.01)
        res = engine.process(curled, timestamp=t)
        assert res.active_gesture == "thumb_index_pinch" and res.action == "grab", i


def test_fist_still_works_without_a_pinch():
    out, _ = _run(GestureEngine(), [make_real_hand(curled=True)] * 6)
    assert out[-1].active_gesture == "fist"


def test_default_fine_control_is_more_precise():
    cfg = GestureEngine().config
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    t, x = 0.0, 0.5
    start = ctrl.apply(_res("move_cursor", (x, 0.5)), cfg, now=t)
    for _ in range(30):  # slow, careful 0.05 frame-widths/s for 1 s
        t += FRAME
        x += 0.05 * FRAME
        ctrl.apply(_res("move_cursor", (x, 0.5)), cfg, now=t)
    moved = ctrl.last_screen_pos[0] - start[0]
    # 0.05 frame widths -> ~0.35 x 1920 x 0.05 ~= 34 px (was ~58 px at gain 0.6)
    assert 20 <= moved <= 45



def test_deliberate_three_finger_pinch_still_drags():
    engine = GestureEngine()
    hand = make_hand(thumb=(0.50, 0.50), index=(0.52, 0.50), middle=(0.52, 0.51))
    engine.process(hand, timestamp=1.0)
    assert engine.process(hand, timestamp=1.07).action == "drag_hold"



def test_quick_double_pinch_is_two_clicks_and_held_pinch_is_debounced():
    engine = GestureEngine()
    click = make_hand(middle=(0.52, 0.50))
    open_ = make_hand()
    t, clicks = 1.0, []
    # pinch 3 frames -> click, open 2 frames, pinch again 3 frames -> click (all in ~0.27 s,
    # well inside the 0.35 s cooldown that used to swallow the second click)
    for hand in (click, click, click, open_, open_, click, click, click):
        clicks.append(engine.process(hand, timestamp=t).action)
        t += 1 / 30
    assert clicks.count("left_click") == 2
    # Holding the pinch does not machine-gun clicks inside the cooldown
    held = [engine.process(click, timestamp=t + i / 30).action for i in range(6)]
    assert held.count("left_click") == 0


def test_cursor_is_held_for_the_second_click_of_a_double_click():
    cfg = dict(REL_CFG, double_click_window=0.45, double_click_move_tolerance=0.03)
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    t = 0.0
    ctrl.apply(_res("move_cursor", (0.50, 0.5)), cfg, now=t)
    click_pos = ctrl.apply(GestureResult(active_gesture="thumb_middle_pinch", action="left_click",
                                         cursor_point=(0.50, 0.5), is_moving=False), cfg, now=t + 0.03)
    # Small hand drift between the two pinches -> cursor stays on the same pixel
    for i, x in enumerate((0.505, 0.51, 0.515)):
        assert ctrl.apply(_res("move_cursor", (x, 0.5)), cfg, now=t + 0.1 + i * 0.03) == click_pos
    # A clear move (> tolerance) releases the hold immediately, without a jump
    p = ctrl.apply(_res("move_cursor", (0.56, 0.5)), cfg, now=t + 0.2)
    assert p == click_pos  # re-anchored here
    p2 = ctrl.apply(_res("move_cursor", (0.60, 0.5)), cfg, now=t + 0.23)
    assert p2[0] > click_pos[0]


def test_double_click_hold_expires():
    cfg = dict(REL_CFG, double_click_window=0.2)
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_res("move_cursor", (0.50, 0.5)), cfg, now=0.0)
    ctrl.apply(GestureResult(action="left_click", cursor_point=(0.50, 0.5), is_moving=False), cfg, now=0.03)
    ctrl.apply(_res("move_cursor", (0.51, 0.5)), cfg, now=0.3)   # after the window: re-anchor
    p = ctrl.apply(_res("move_cursor", (0.53, 0.5)), cfg, now=0.33)
    assert p[0] > 960  # moving again after the window



def test_pointer_switcher_keeps_cursor_moving_and_clicks_the_window():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), PTR_CFG, now=1.0)
    assert ctrl.switcher_open
    start = ctrl.last_screen_pos
    t, x = 1.1, 0.5
    for _ in range(10):  # open hand moving right -> the cursor follows
        ctrl.apply(_sw("no_pinch", "move_cursor", (x, 0.5)), PTR_CFG, now=t)
        t += FRAME
        x += 0.5 * FRAME
    assert ctrl.last_screen_pos[0] > start[0] + 20
    assert ctrl.switcher_moves == []  # no arrow keys in pointer mode
    # Fist moves it fast too (does not cancel in pointer mode)
    fist = GestureResult(active_gesture="fist", action="move_cursor", cursor_point=(x, 0.5),
                         is_moving=True, dpi_multiplier=15.0)
    ctrl.apply(fist, PTR_CFG, now=t)
    assert ctrl.switcher_open
    # Thumb + middle clicks the thumbnail under the cursor, then Alt is released
    ctrl.apply(_sw("thumb_middle_pinch", "left_click"), PTR_CFG, now=t + 0.1)
    ctrl.apply(_sw("NONE", "none"), PTR_CFG, now=t + 0.4)
    assert not ctrl.switcher_open
    assert ctrl.event_log[-2:] == ["switcher_click", "switcher_confirm"]


def test_pointer_switcher_thumb_ring_cancels():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), PTR_CFG, now=1.0)
    ctrl.apply(_sw("no_pinch", "move_cursor"), PTR_CFG, now=1.1)
    ctrl.apply(_sw("thumb_ring_pinch", "right_click"), PTR_CFG, now=1.2)
    ctrl.hand_lost(PTR_CFG, now=1.5)  # pending cancel also runs on frames without a hand
    assert not ctrl.switcher_open and ctrl.event_log[-1] == "switcher_cancel"
    assert "right_click" not in ctrl.event_log


def test_closing_into_a_fist_never_becomes_a_pinch():
    """Hand closing into a fist: the thumb lands on the curling index tip on the way."""
    engine = GestureEngine()
    t = 1.0
    steps = 8
    for i in range(steps + 1):  # gradually curl every finger, thumb riding on the index tip
        f = i / steps
        hand = make_real_hand(curled=False)
        for (mcp_i, tip_i) in GestureEngine.FINGER_JOINTS:
            y_open, y_closed = 0.40, 0.68
            hand[tip_i] = Point3D(hand[tip_i].x, y_open + (y_closed - y_open) * f)
        if f >= 0.5:  # thumb folds onto the index tip once the fingers are half curled
            hand[4] = Point3D(hand[8].x + 0.02, hand[8].y + 0.005)
        res = engine.process(hand, timestamp=t)
        assert res.action != "grab", i
        t += 1 / 30
    for _ in range(5):  # hold the closed fist
        res = engine.process(hand, timestamp=t)
        assert res.action != "grab"
        t += 1 / 30
    assert res.active_gesture == "fist"


def test_pinch_with_other_fingers_curled_is_a_pinch_not_a_fist():
    """'OK'-style pinch: middle/ring/pinky curled, index bent but reaching the thumb."""
    engine = GestureEngine()
    hand = make_real_hand(curled=True)
    hand[8] = Point3D(0.44, 0.47)   # index tip beyond its MCP (ratio ~1.6)
    hand[4] = Point3D(0.455, 0.475)  # thumb tip on it
    t, results = 1.0, []
    for _ in range(8):
        results.append(engine.process(hand, timestamp=t).action)
        t += 1 / 30
    assert results[0] != "grab"           # fist-like hand: short confirmation first
    assert results[-1] == "grab"          # then a real pinch
    assert engine.process(hand, timestamp=t).active_gesture == "thumb_index_pinch"
    # ...and it never passed through fast move (fist) on the way
    assert all(r in ("none", "grab") for r in results)


def _run(engine, hands, t0=1.0):
    t, out = t0, []
    for h in hands:
        out.append(engine.process(h, timestamp=t))
        t += 1 / 30
    return out, t


def make_thumb_pinky_hand() -> List[Point3D]:
    """Thumb+pinky pinch: index, middle, ring extended; pinky bent toward the thumb."""
    hand = make_real_hand(curled=False)
    hand[16] = Point3D(0.52, 0.55)   # ring half bent along with it (ratio ~1.25)
    hand[20] = Point3D(0.53, 0.58)   # pinky tip bent in (ratio ~1.1, not tucked)
    hand[4] = Point3D(0.515, 0.585)  # thumb tip on it
    return hand


def test_thumb_pinky_pinch_opens_switcher_with_index_middle_extended():
    """Regression: the Win+D posture check used to swallow this pinch."""
    engine = GestureEngine()
    out, _ = _run(engine, [make_thumb_pinky_hand()] * 4)
    actions = [r.action for r in out]
    assert "task_switcher" in actions
    assert "right_click" not in actions  # the half-bent ring next to the thumb is not a click


def test_show_desktop_action_sends_win_d_and_releases_drag():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(GestureResult(action="drag_toggle", is_moving=False), {})
    ctrl.apply(GestureResult(active_gesture="index_middle_spread", action="show_desktop", is_moving=False), {})
    assert "show_desktop" in ctrl.event_log and not ctrl.is_dragging



def test_pinky_pinch_threshold_requires_close_fingers_and_is_configurable():
    hand = make_thumb_pinky_hand()
    hand[4] = Point3D(0.53, 0.58 + 0.058)  # 0.058 away: loose gap, must NOT trigger at default 0.045
    out, _ = _run(GestureEngine(), [hand] * 4)
    assert "task_switcher" not in [r.action for r in out]
    loose = GestureEngine()
    loose.config["pinky_click_threshold"] = 0.065
    out, _ = _run(loose, [hand] * 4)
    assert "task_switcher" in [r.action for r in out]


def _switcher_pinch_hand(ring_y=0.66, pinky=(0.53, 0.58), spread=None):
    """Thumb+pinky pinch with index/middle extended (together unless `spread`), ring folded."""
    h = make_real_hand(curled=False)
    if spread is not None:
        h[8], h[12] = Point3D(0.46 - 0.02 * spread, 0.40), Point3D(0.46 + 0.02 * spread, 0.40)
    h[16] = Point3D(0.52, ring_y)     # ring folded (ratio ~0.7)
    h[20] = Point3D(*pinky)           # pinky bent out to meet the thumb (ratio ~1.06)
    h[4] = Point3D(pinky[0] - 0.015, pinky[1] + 0.005)
    return h


def test_switcher_fist_moves_gently():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    cfg = dict(PTR_CFG, switcher_fist_multiplier=4.0)
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), cfg, now=1.0)

    def fist(x, t):
        ctrl.apply(GestureResult(active_gesture="fist", action="move_cursor", cursor_point=(x, 0.5),
                                 is_moving=True, dpi_multiplier=15.0), cfg, now=t)

    fist(0.50, 1.1)
    start = ctrl.last_screen_pos[0]
    fist(0.51, 1.2)  # slow: 0.1 frame-widths/s -> min gain 0.35
    moved = ctrl.last_screen_pos[0] - start
    assert ctrl.switcher_open
    assert moved == pytest.approx(0.01 * 1920 * 0.35 * 4.0, abs=3)  # x4, not x15


# ------------------------------------------------------------------ show desktop (Win+D)
def make_desktop_hand(tucked: bool = True, shift: tuple[float, float] = (0.0, 0.0)) -> List[Point3D]:
    """Four fingers extended; thumb tucked across the palm (near the ring/pinky MCPs)."""
    h = make_real_hand(curled=False, shift=shift)
    sx, sy = shift
    h[4] = Point3D(0.535 + sx, 0.63 + sy) if tucked else Point3D(0.36 + sx, 0.62 + sy)
    return h


def test_thumb_tucked_four_fingers_open_shows_desktop_once_per_hold():
    engine = GestureEngine()
    out, t = _run(engine, [make_desktop_hand()] * 30)  # 1 s hold
    actions = [r.action for r in out]
    assert actions.count("show_desktop") == 1
    first = actions.index("show_desktop")
    assert 0.6 <= first / 30 <= 0.7  # after desktop_hold (0.6 s since 2026-11-04; was 0.4)
    assert out[first].active_gesture == "thumb_tucked_open"
    # Cursor keeps moving while the posture is held (it is an open hand)
    assert all(r.is_moving for r in out if r.action != "show_desktop")
    # Re-arms only after the posture is released (and after the cooldown)
    out2, _ = _run(engine, [make_desktop_hand(tucked=False)] * 30 + [make_desktop_hand()] * 25, t0=t)
    assert [r.action for r in out2].count("show_desktop") == 1


def test_normal_open_hand_and_brief_tuck_never_show_desktop():
    out, _ = _run(GestureEngine(), [make_desktop_hand(tucked=False)] * 40)
    assert "show_desktop" not in [r.action for r in out]
    brief = [make_desktop_hand()] * 8 + [make_desktop_hand(tucked=False)] * 5  # 0.27 s < 0.4 s
    out, _ = _run(GestureEngine(), brief * 3)
    assert "show_desktop" not in [r.action for r in out]


def test_show_desktop_needs_all_four_fingers_open_and_palm_facing():
    def bent(tip_i):
        h = make_desktop_hand()
        mcp_i = tip_i - 3
        h[tip_i] = Point3D(h[mcp_i].x, 0.66)  # that finger folded
        return h
    for tip_i in (8, 12, 16, 20):
        out, _ = _run(GestureEngine(), [bent(tip_i)] * 20)
        assert "show_desktop" not in [r.action for r in out], tip_i
    sideways = make_desktop_hand()
    for i_, x in ((5, 0.49), (9, 0.495), (13, 0.50), (17, 0.505)):
        sideways[i_] = Point3D(x, sideways[i_].y)
    out, _ = _run(GestureEngine(), [sideways] * 20)
    assert "show_desktop" not in [r.action for r in out]


def test_pinch_gestures_are_never_the_desktop_posture():
    """Every pinch bends a finger toward the thumb, so none can look like four open fingers."""
    out, _ = _run(GestureEngine(), [make_thumb_pinky_hand()] * 20)
    actions = [r.action for r in out]
    assert "show_desktop" not in actions and "task_switcher" in actions
    ring = make_real_hand(curled=False)
    ring[16] = Point3D(0.53, 0.56)
    ring[4] = Point3D(0.515, 0.565)
    out, _ = _run(GestureEngine(), [ring] * 20)
    assert "show_desktop" not in [r.action for r in out]


def test_thumb_tuck_is_reported_for_the_hud():
    res = GestureEngine().process(make_desktop_hand(), timestamp=1.0)
    assert res.distances["thumb_tuck"] <= 0.45
    res = GestureEngine().process(make_desktop_hand(tucked=False), timestamp=1.0)
    assert res.distances["thumb_tuck"] > 0.45


def test_desktop_and_switcher_lock_each_other_out():
    engine = GestureEngine()
    out, t = _run(engine, [make_desktop_hand()] * 25)
    assert "show_desktop" in [r.action for r in out]
    out2, t = _run(engine, [make_thumb_pinky_hand()] * 5, t0=t)  # within 1 s
    assert "task_switcher" not in [r.action for r in out2]
    out3, _ = _run(engine, [make_real_hand(curled=False)] * 3 + [make_thumb_pinky_hand()] * 5, t0=t + 1.0)
    assert "task_switcher" in [r.action for r in out3]


# ------------------------------------------------------------------ switcher: closed hand moves
def test_switcher_moves_with_any_closed_hand_shape():
    """Closing into a fist often passes through a thumb+index pinch / drag shape: in the
    switcher those must move the cursor (fast) instead of freezing it."""
    for gesture, action in (("fist", "move_cursor"), ("thumb_index_pinch", "grab"),
                            ("thumb_index_middle_pinch", "drag_hold")):
        ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
        cfg = dict(PTR_CFG, switcher_fist_multiplier=4.0)
        ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), cfg, now=1.0)
        res = lambda x: GestureResult(active_gesture=gesture, action=action, cursor_point=(x, 0.5),
                                      pinch_point=(x, 0.4), is_moving=True)
        ctrl.apply(res(0.50), cfg, now=1.1)
        start = ctrl.last_screen_pos[0]
        ctrl.apply(res(0.51), cfg, now=1.2)
        moved = ctrl.last_screen_pos[0] - start
        assert moved == pytest.approx(0.01 * 1920 * 0.35 * 4.0, abs=3), gesture
        assert ctrl.switcher_open and not ctrl.is_dragging, gesture


# ------------------------------------------------------------------ Record & Fling OFF (default)
@pytest.mark.record_off
def test_record_off_thumb_index_touch_just_keeps_moving():
    engine = GestureEngine()
    engine.config["grab_scroll_enabled"] = False  # thumb+index with no job at all
    assert engine.config["record_fling_enabled"] is False
    for i, x in enumerate((0.53, 0.53, 0.528, 0.531)):
        res = engine.process(make_hand(index=(x, 0.50)), timestamp=1.0 + i / 30)
        assert res.action == "move_cursor" and res.is_moving and res.active_gesture == "no_pinch"


@pytest.mark.record_off
def test_record_off_fist_is_not_blocked_by_a_thumb_index_touch():
    engine = GestureEngine()
    engine.config["grab_scroll_enabled"] = False  # thumb+index with no job at all
    t = 1.0
    start = make_real_hand(curled=False)
    start[4] = Point3D(start[8].x - 0.02, start[8].y)  # thumb touching the index tip
    engine.process(start, timestamp=t)
    for i in range(6):  # fingers close into a fist while the thumb stays on the index
        t += 1 / 30
        fist = make_real_hand(curled=True)
        res = engine.process(fist, timestamp=t)
    assert res.active_gesture == "fist"


@pytest.mark.record_off
def test_record_off_no_click_lockout_after_an_accidental_touch():
    engine = GestureEngine()
    engine.config["grab_scroll_enabled"] = False  # thumb+index with no job at all
    engine.process(make_hand(index=(0.53, 0.50)), timestamp=1.0)        # brief thumb-index touch
    t = 1.0
    for _ in range(6):                                                  # released (> grace)
        t += 1 / 30
        engine.process(make_hand(), timestamp=t)
    click = make_hand(middle=(0.52, 0.50))
    actions = [engine.process(click, timestamp=t + (i + 1) / 30).action for i in range(3)]
    assert "left_click" in actions  # no 0.35 s lockout


@pytest.mark.record_off
def test_record_off_drag_still_works():
    engine = GestureEngine()
    hand = make_hand(thumb=(0.50, 0.50), index=(0.52, 0.50), middle=(0.52, 0.51))
    engine.process(hand, timestamp=1.0)
    assert engine.process(hand, timestamp=1.07).action == "drag_hold"


@pytest.mark.record_off
def test_record_can_be_switched_on_at_runtime():
    engine = GestureEngine()
    hand = make_hand(index=(0.53, 0.50))
    assert engine.process(hand, timestamp=1.0).action == "scroll_grab"  # default: grab-scroll
    engine.config["record_fling_enabled"] = True  # settings-window checkbox
    engine.reset()
    assert engine.process(hand, timestamp=2.0).action == "grab"


def test_switcher_fist_after_a_brushed_click_keeps_switcher_open_and_moves():
    """Closing the hand into a fist brushes the thumb past the middle finger (= left_click).
    That must not open a window: the fist that follows cancels the pending click."""
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    cfg = dict(PTR_CFG, switcher_fist_multiplier=4.0, switcher_confirm_delay=0.25)
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), cfg, now=1.0)
    ctrl.apply(_sw("no_pinch", "move_cursor", (0.50, 0.5)), cfg, now=1.1)
    ctrl.apply(_sw("thumb_middle_pinch", "left_click"), cfg, now=1.15)  # brushed while closing
    t, x = 1.2, 0.50
    start = None
    for _ in range(10):
        ctrl.apply(GestureResult(active_gesture="fist", action="move_cursor", cursor_point=(x, 0.5),
                                 is_moving=True, dpi_multiplier=15.0), cfg, now=t)
        start = start or ctrl.last_screen_pos[0]
        t += 1 / 30
        x += 0.3 / 30
    assert ctrl.switcher_open
    assert "switcher_click" not in ctrl.event_log
    assert ctrl.last_screen_pos[0] > start + 20  # the fist moved the cursor


def test_switcher_deliberate_click_lands_where_the_cursor_was():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.apply(_sw("thumb_pinky_pinch", "task_switcher"), PTR_CFG, now=1.0)
    ctrl.apply(_sw("no_pinch", "move_cursor", (0.5, 0.5)), PTR_CFG, now=1.1)
    ctrl.apply(_sw("thumb_middle_pinch", "left_click"), PTR_CFG, now=1.2)
    for i in range(1, 10):  # held pinch (cursor frozen) -> fires after the delay
        ctrl.apply(_sw("NONE", "none"), PTR_CFG, now=1.2 + i / 30)
    assert not ctrl.switcher_open
    assert ctrl.event_log[-2:] == ["switcher_click", "switcher_confirm"]



# ------------------------------------------------------------------ middle / ring pinch strength
def _middle_pinch_hand(index_tip):
    """Thumb on the middle tip (middle not tucked), ring/pinky open, index where given."""
    h = make_real_hand(curled=False)
    h[12] = Point3D(0.48, 0.48)
    h[4] = Point3D(0.495, 0.485)
    h[8] = Point3D(*index_tip)
    return h


@pytest.mark.record_off
def test_middle_click_with_index_folded_out_of_the_way():
    """People fold the index down to reach the middle finger: that used to block the click."""
    engine = GestureEngine()
    out, _ = _run(engine, [_middle_pinch_hand(index_tip=(0.44, 0.66))] * 4)  # index tucked
    assert "left_click" in [r.action for r in out]


@pytest.mark.record_off
def test_middle_click_with_index_right_next_to_it():
    """The index ends up near the thumb too (even inside the thumb+index pinch distance)."""
    engine = GestureEngine()
    out, _ = _run(engine, [_middle_pinch_hand(index_tip=(0.53, 0.48))] * 4)  # index ~0.035 away
    assert "left_click" in [r.action for r in out]


@pytest.mark.record_off
def test_ring_click_with_pinky_right_next_to_it():
    engine = GestureEngine()
    h = make_real_hand(curled=False)
    h[16] = Point3D(0.52, 0.50)
    h[4] = Point3D(0.52, 0.52)        # thumb on the ring tip: 0.02
    h[20] = Point3D(0.545, 0.52)      # pinky right beside the ring tip: ~0.025 from the thumb
    out, _ = _run(engine, [h] * 4)
    actions = [r.action for r in out]
    assert "right_click" in actions and "task_switcher" not in actions


@pytest.mark.record_off
@pytest.mark.record_off
def test_thumb_resting_on_a_fist_never_clicks():
    engine = GestureEngine()
    out, _ = _run(engine, [make_real_hand(curled=True)] * 10)
    assert all(r.action in ("move_cursor", "none") for r in out)  # no clicks, ever
    assert out[-1].active_gesture == "fist"


# ------------------------------------------------------------------ overlay guide lines
def test_pinch_guides():
    from src.hand_tracker import pinch_guides
    cfg = GestureEngine().config
    far = {"index": 0.20, "middle": 0.22, "ring": 0.25, "pinky": 0.30}
    assert pinch_guides(far, cfg, set()) == []                       # nothing near: no line
    near = dict(far, ring=0.07)                                      # 0.07 / 0.052 = 1.35x
    assert pinch_guides(near, cfg, set()) == [("ring", "near")]      # only the nearest finger
    assert pinch_guides(near, cfg, {"middle"}) == [("middle", "pinched")]
    assert pinch_guides(near, cfg, {"middle"}, action="left_click") == [("middle", "action")]
    assert pinch_guides(near, cfg, set(), action="task_switcher") == [("pinky", "action")]
    assert pinch_guides(near, cfg, {"ring"}, gesture="fist") == []   # thumb rests on a fist


@pytest.mark.record_off
def test_three_finger_drag_needs_index_and_middle_really_touching():
    engine = GestureEngine()
    real = make_hand(thumb=(0.50, 0.50), index=(0.52, 0.50), middle=(0.52, 0.51))  # both touching
    out, _ = _run(engine, [real] * 3)
    assert "drag_hold" in [r.action for r in out]


@pytest.mark.record_off
def test_held_click_pinch_is_steady_frozen_and_never_repeats():
    """The HUD used to flip between green (moving) and red (repeated clicks) every few frames."""
    engine = GestureEngine()
    ring = make_real_hand(curled=False)
    ring[16] = Point3D(0.52, 0.50)
    ring[4] = Point3D(0.52, 0.53)
    out, t = _run(engine, [ring] * 60)  # 2 s held
    actions = [r.action for r in out]
    assert actions.count("right_click") == 1
    after = out[actions.index("right_click") + 1:]
    assert all(r.active_gesture == "thumb_ring_pinch" and not r.is_moving for r in after)
    # Jitter between the freeze distance and the hold distance no longer flips it to moving
    jitter = make_real_hand(curled=False)
    jitter[16] = Point3D(0.52, 0.50)
    jitter[4] = Point3D(0.52, 0.565)    # 0.065: above release 0.05 / click 0.052, inside hold 0.072
    out2, _ = _run(engine, [jitter, ring] * 10, t0=t)
    assert all(not r.is_moving for r in out2)
    assert "right_click" not in [r.action for r in out2]


def test_slightly_bent_fingers_are_not_a_fist():
    """Reported: bending the fingers a little already counted as a fist."""
    for tip_y in (0.50, 0.55, 0.58):  # ratios ~1.5 / ~1.25 / ~1.1: bent, not curled into the palm
        out, _ = _run(GestureEngine(), [_loose_fist(tip_y)] * 15)
        assert all(r.active_gesture != "fist" for r in out), tip_y


def test_fingers_pointing_at_the_camera_are_not_a_fist():
    """Foreshortened in 2D (tips look close to the wrist) but far away in depth (z)."""
    h = make_real_hand(curled=True)  # 2D positions of a fist...
    for _, tip_i in GestureEngine.FINGER_JOINTS:
        h[tip_i] = Point3D(h[tip_i].x, h[tip_i].y, -0.25)  # ...but the tips are far toward the camera
    out, _ = _run(GestureEngine(), [h] * 15)
    assert all(r.active_gesture != "fist" for r in out)


def test_brief_fist_shape_is_ignored():
    hands = [make_real_hand(curled=True)] * 2 + [make_real_hand(curled=False)] * 3  # ~67 ms
    out, _ = _run(GestureEngine(), hands * 3)
    assert all(r.active_gesture != "fist" for r in out)



# ------------------------------------------------------------------ grab-scroll (thumb+index)
@pytest.mark.record_off
def test_thumb_index_pinch_grabs_the_page_by_default():
    engine = GestureEngine()
    out, _ = _run(engine, [make_hand(index=(0.53, 0.50))] * 3)
    assert all(r.action == "scroll_grab" and not r.is_moving for r in out)
    engine.config["record_fling_enabled"] = True  # Record & Fling takes priority when on
    engine.reset()
    out, _ = _run(engine, [make_hand(index=(0.53, 0.50))] * 2, t0=5.0)
    assert out[-1].action == "grab"


def _scroll_res(pt):
    return GestureResult(active_gesture="thumb_index_pinch", action="scroll_grab",
                         cursor_point=pt, is_moving=False)


SCROLL_CFG = {"scroll_gain": 2400.0, "scroll_deadzone": 0.01, "scroll_invert": False}


def _scroll(dy=0.0, dx=0.0, cfg=SCROLL_CFG, frames=10):
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    pos = ctrl.last_screen_pos
    x, y = 0.5, 0.5
    for i in range(frames + 1):
        ctrl.apply(_scroll_res((x + dx * i / frames, y + dy * i / frames)), cfg, now=i / 30)
    for i in range(6):  # hold still: the smoothed point catches up
        ctrl.apply(_scroll_res((x + dx, y + dy)), cfg, now=(frames + 1 + i) / 30)
    return ctrl, pos


def test_pull_down_scrolls_the_page_up_like_a_touchscreen():
    ctrl, pos = _scroll(dy=0.2)
    v, h = ctrl.scroll_total
    assert v > 0 and h == 0                    # wheel up: content follows the hand down
    # ~0.2 frame heights * 0.75 aspect * 2400, minus the deadzone / smoothing tail
    assert 200 < v < 400
    assert ctrl.last_screen_pos == pos          # the cursor stays put while scrolling
    ctrl_up, _ = _scroll(dy=-0.2)
    assert ctrl_up.scroll_total[0] < 0          # push up -> scroll down


def test_horizontal_drag_scrolls_sideways():
    ctrl, _ = _scroll(dx=0.2)
    assert ctrl.scroll_total[1] < 0 and ctrl.scroll_total[0] == 0  # hand right -> view left


def test_scroll_deadzone_and_invert():
    still, _ = _scroll(dy=0.005)                # tremor-sized motion
    assert still.scroll_total == [0, 0]
    inv, _ = _scroll(dy=0.2, cfg=dict(SCROLL_CFG, scroll_invert=True))
    assert inv.scroll_total[0] < 0              # wheel direction instead


def test_scroll_stops_when_the_pinch_is_released():
    ctrl, _ = _scroll(dy=0.2)
    total = list(ctrl.scroll_total)
    for i in range(5):  # released: open hand moving, no more scrolling
        ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor",
                                 cursor_point=(0.5, 0.7 + i * 0.02), is_moving=True), SCROLL_CFG, now=2 + i / 30)
    assert ctrl.scroll_total == total


INERTIA_CFG = dict(SCROLL_CFG, scroll_inertia=True, scroll_inertia_time=0.4, scroll_inertia_min_speed=600.0)


def _open_hand(y=0.7):
    return GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(0.5, y), is_moving=True)


def _flick(ctrl, stop_before_release=False):
    """Grab, pull down fast (0.25 of the frame in ~0.2 s), release."""
    t, y = 0.0, 0.5
    for i in range(8):
        ctrl.apply(_scroll_res((0.5, y)), INERTIA_CFG, now=t)
        t += 1 / 30
        y += 0.035
    if stop_before_release:
        for _ in range(8):  # hand comes to rest while still pinched
            ctrl.apply(_scroll_res((0.5, y)), INERTIA_CFG, now=t)
            t += 1 / 30
    return t


def test_flick_release_keeps_the_page_coasting_then_it_stops():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    t = _flick(ctrl)
    at_release = ctrl.scroll_total[0]
    ctrl.apply(_open_hand(), INERTIA_CFG, now=t)
    assert ctrl.scroll_coasting
    totals = []
    for i in range(1, 90):  # 3 s of open hand
        ctrl.apply(_open_hand(), INERTIA_CFG, now=t + i / 30)
        totals.append(ctrl.scroll_total[0])
    assert totals[5] > at_release                      # still scrolling after the release
    assert totals[-1] == totals[-10]                   # ...and it came to rest
    assert not ctrl.scroll_coasting
    # it slowed down: the first 0.2 s coasted farther than the next 0.2 s
    assert totals[5] - at_release > totals[11] - totals[5] > 0


def test_no_inertia_when_the_hand_stopped_before_release():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    t = _flick(ctrl, stop_before_release=True)
    at_release = ctrl.scroll_total[0]
    for i in range(30):
        ctrl.apply(_open_hand(), INERTIA_CFG, now=t + i / 30)
    assert ctrl.scroll_total[0] == at_release and not ctrl.scroll_coasting


def test_pinching_again_catches_a_coasting_page_and_clicks_stop_it():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    t = _flick(ctrl)
    ctrl.apply(_open_hand(), INERTIA_CFG, now=t)
    ctrl.apply(_scroll_res((0.5, 0.3)), INERTIA_CFG, now=t + 1 / 30)   # catch
    assert not ctrl.scroll_coasting
    caught = ctrl.scroll_total[0]
    ctrl.apply(_scroll_res((0.5, 0.3)), INERTIA_CFG, now=t + 2 / 30)
    assert ctrl.scroll_total[0] == caught
    # flick again, then a click stops the coasting immediately
    ctrl2 = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl2.frame_aspect = 0.75
    t2 = _flick(ctrl2)
    ctrl2.apply(_open_hand(), INERTIA_CFG, now=t2)
    ctrl2.apply(GestureResult(action="left_click", is_moving=False), INERTIA_CFG, now=t2 + 1 / 30)
    assert not ctrl2.scroll_coasting


def test_inertia_can_be_switched_off():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    cfg = dict(INERTIA_CFG, scroll_inertia=False)
    t, y = 0.0, 0.5
    for i in range(8):
        ctrl.apply(_scroll_res((0.5, y)), cfg, now=t)
        t += 1 / 30
        y += 0.035
    ctrl.apply(_open_hand(), cfg, now=t)
    assert not ctrl.scroll_coasting


def _pinch_hand_at(dy: float, opened: bool = False) -> List[Point3D]:
    """Whole hand shifted vertically by dy; thumb on the index tip unless `opened`."""
    h = [Point3D(p.x, p.y + dy) for p in make_hand(index=(0.53, 0.50))]
    if opened:
        h[8] = Point3D(0.62, 0.50 + dy)  # fingers apart (beyond release distance)
    return h


@pytest.mark.record_off
def test_flick_up_then_spring_back_coasts_the_same_way():
    """Reported: scrolling up, the inertia went down. At the end of a flick the hand
    springs back while the fingers open (inside the release grace); that motion used to
    scroll the page back and set the inertia direction."""
    def run(use_releasing: bool):
        engine = GestureEngine()
        ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
        ctrl.frame_aspect = 0.75
        cfg = dict(engine.config, scroll_inertia=True)
        t, y = 1.0, 0.0
        frames = [(y0, False) for y0 in [-0.035 * i for i in range(9)]]      # quick push up
        frames += [(-0.28 + 0.02 * k, True) for k in range(1, 5)]          # spring back, fingers open
        frames += [(-0.20, True)] * 6                                        # released
        totals = []
        for dy, opened in frames:
            res = engine.process(_pinch_hand_at(dy, opened), timestamp=t)
            if not use_releasing:
                res.releasing = False  # what the old code effectively did
            ctrl.apply(res, cfg, now=t)
            totals.append(ctrl.scroll_total[0])
            t += 1 / 30
        before_release = totals[8]
        for i in range(60):
            ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor",
                                     cursor_point=(0.5, 0.5), is_moving=True), cfg, now=t + i / 30)
        return before_release, totals, ctrl.scroll_total[0]

    before, totals, end = run(use_releasing=True)
    assert before < 0                             # pushing up scrolled the page down (wheel < 0)
    assert all(b <= a for a, b in zip(totals[8:], totals[9:]))  # never pulled back up while releasing
    assert end < totals[-1]                       # inertia keeps going the same way (more negative)

    _, old_totals, old_end = run(use_releasing=False)
    assert old_end >= old_totals[8]               # old behaviour: page dragged back / coasted backwards


@pytest.mark.record_off
def test_middle_click_works_with_grab_scroll_on_and_index_nearby():
    """Reported: thumb+middle only showed CURSOR FROZEN (the click never registered)."""
    def hand(d_mid, d_idx):
        h = make_real_hand(curled=False)
        h[12] = Point3D(0.48, 0.48)
        h[4] = Point3D(0.48 + d_mid, 0.48)
        h[8] = Point3D(0.48 + d_mid + d_idx, 0.48)
        return h
    engine = GestureEngine()
    assert engine.config["grab_scroll_enabled"] is True
    out, _ = _run(engine, [hand(0.025, 0.03)] * 6)       # middle closer -> click
    assert "left_click" in [r.action for r in out]
    out, _ = _run(GestureEngine(), [hand(0.03, 0.02)] * 6)  # index closer -> grab / scroll
    assert "scroll_grab" in [r.action for r in out] and "left_click" not in [r.action for r in out]



def _thumb_on(tip_i, d, neighbours=(), tip_y=0.50):
    """Thumb d below finger tip `tip_i`; neighbour tips (tip index, distance) beside the thumb."""
    h = make_real_hand(curled=False)
    tip = Point3D(h[tip_i].x, tip_y)
    h[tip_i] = tip
    h[4] = Point3D(tip.x, tip.y + d)
    side = 1
    for nb_i, nd in neighbours:
        h[nb_i] = Point3D(tip.x + side * nd, tip.y + d)
        side = -side
    return h


def _fired(hand):
    out, _ = _run(GestureEngine(), [hand] * 6)
    return [a for a in (r.action for r in out) if a in ("left_click", "right_click", "task_switcher", "scroll_grab")]


@pytest.mark.record_off
def test_ring_pinch_with_neighbours_gives_exactly_one_right_click():
    """Same kind of problem as the middle finger: neighbours sit right next to the ring."""
    assert _fired(_thumb_on(16, 0.025, [(12, 0.03)])) == ["right_click"]               # used to add a left click
    assert _fired(_thumb_on(16, 0.02, [(20, 0.022)])) == ["right_click"]               # used to give nothing
    assert _fired(_thumb_on(16, 0.02, [(20, 0.03), (12, 0.03)])) == ["right_click"]
    assert _fired(_thumb_on(16, 0.02, tip_y=0.62)) == ["right_click"]                  # deeply bent ring (0.9)


@pytest.mark.record_off
def test_middle_and_pinky_pinch_with_neighbours():
    assert _fired(_thumb_on(12, 0.025, [(16, 0.03)])) == ["left_click"]
    assert _fired(_thumb_on(20, 0.03, [(16, 0.045)])) == ["task_switcher"]
    # ring tip about as close as the pinky tip (curled hand): the pinky is not clearly the one
    assert "task_switcher" not in _fired(_thumb_on(20, 0.03, [(16, 0.035)]))


@pytest.mark.record_off
def test_fist_still_never_clicks_with_relaxed_tuck_rule():
    out, _ = _run(GestureEngine(), [make_real_hand(curled=True)] * 10)
    assert all(r.action in ("move_cursor", "none") for r in out)


# ------------------------------------------------------------------ long press -> drag
def _drag_frames(engine, ctrl, cfg, hands, t0=1.0):
    t, out = t0, []
    for h in hands:
        r = engine.process(h, timestamp=t)
        ctrl.apply(r, cfg, now=t)
        out.append((r, ctrl.is_dragging, ctrl.last_screen_pos))
        t += 1 / 30
    return out, t


def _shift(hand, dx):
    return [Point3D(p.x + dx, p.y, p.z) for p in hand]


@pytest.mark.record_off
def test_long_press_thumb_middle_drags_a_file_and_release_drops_it():
    engine = GestureEngine()
    engine.config["long_press_time"] = 0.6   # these tests are about the drag, not the default delay
    cfg = dict(engine.config)
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    pinch = _thumb_on(12, 0.02)
    held = [pinch] * 22                                     # ~0.73 s: click, then drag starts at 0.6 s
    moving = [_shift(pinch, 0.01 * k) for k in range(1, 16)]  # move the hand while holding
    release = [make_real_hand(curled=False)] * 8           # > 0.15 s drag drop-grace
    out, _ = _drag_frames(engine, ctrl, cfg, held + moving + release)
    actions = [r.action for r, _, _ in out]
    assert actions.count("left_click") == 1                 # the short-press click (selects the file)
    first_drag = actions.index("drag_hold")
    assert 0.55 <= first_drag / 30 <= 0.7
    assert ctrl.event_log.count("mouseDown") == 1 and ctrl.event_log.count("mouseUp") == 1
    assert ctrl.event_log.index("left_click") < ctrl.event_log.index("mouseDown") < ctrl.event_log.index("mouseUp")
    down_pos = out[first_drag][2]
    during = [pos for _, dragging, pos in out[len(held):len(held) + len(moving)] if dragging]
    assert during and during[-1][0] > down_pos[0] + 50      # the cursor follows the hand while dragging
    assert not out[-1][1]                                   # released -> dropped
    assert "double_click" not in actions


@pytest.mark.record_off
def test_short_pinch_is_still_a_single_click_without_drag():
    engine = GestureEngine()
    out, _ = _run(engine, [_thumb_on(12, 0.02)] * 8 + [make_real_hand(curled=False)] * 10)
    actions = [r.action for r in out]
    assert actions.count("left_click") == 1 and "drag_hold" not in actions


@pytest.mark.record_off
def test_drag_survives_short_tracking_dropouts():
    engine = GestureEngine()
    engine.config["long_press_time"] = 0.6   # these tests are about the drag, not the default delay
    pinch = _thumb_on(12, 0.02)
    lost = make_real_hand(curled=False)
    out, _ = _run(engine, [pinch] * 22 + [lost, lost, lost] + [pinch] * 5)  # 0.1 s dropout mid-drag
    actions = [r.action for r in out]
    assert all(a == "drag_hold" for a in actions[19:])      # never dropped the file


@pytest.mark.record_off
def test_long_press_drag_can_be_switched_off_and_ring_is_unaffected():
    engine = GestureEngine()
    engine.config["long_press_drag"] = False
    out, _ = _run(engine, [_thumb_on(12, 0.02)] * 40)
    assert "drag_hold" not in [r.action for r in out]
    out, _ = _run(GestureEngine(), [_thumb_on(16, 0.02)] * 40)   # ring: right click only
    actions = [r.action for r in out]
    assert actions.count("right_click") == 1 and "drag_hold" not in actions


# ------------------------------------------------------------------ inertia only on a real release
def _scroll_session(frames, cfg_over=None):
    """frames: list of ('pinch', dy) / ('jitter', dy) / ('lost', None) / ('open', dy)."""
    engine = GestureEngine()
    cfg = dict(engine.config, scroll_inertia=True, **(cfg_over or {}))
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    t, log = 1.0, []
    for kind, dy in frames:
        if kind == "lost":
            engine.process(None, timestamp=t)
            ctrl.hand_lost(cfg, now=t)
            log.append(("lost", ctrl.scroll_total[0], ctrl.scroll_coasting))
        else:
            h = _pinch_hand_at(dy, opened=(kind == "open"))
            if kind == "jitter":  # index slightly past the normal release distance
                h[8] = Point3D(h[4].x + 0.085, h[4].y)
            r = engine.process(h, timestamp=t)
            ctrl.apply(r, cfg, now=t)
            log.append((r.action, ctrl.scroll_total[0], ctrl.scroll_coasting))
        t += 1 / 30
    return log


@pytest.mark.record_off
def test_no_inertia_while_still_pinching_through_a_short_hand_loss():
    moving = [("pinch", -0.03 * i) for i in range(8)]
    lost = [("lost", None)] * 5                                   # 0.17 s blur
    back = [("pinch", -0.30 - 0.03 * i) for i in range(5)]        # hand back further along
    log = _scroll_session(moving + lost + back)
    assert not any(coast for _, _, coast in log)                  # never coasted while pinched
    assert log[-1][0] == "scroll_grab"                            # still grabbing after the loss
    after_lost = [total for a, total, _ in log[13:]]
    jump = abs(after_lost[0] - log[12][1])
    assert jump < 400                                             # resumed without a big jump


@pytest.mark.record_off
def test_index_jitter_while_scrolling_does_not_release():
    frames = [("pinch", -0.03 * i) for i in range(8)]
    frames += [("jitter", -0.24), ("pinch", -0.27), ("jitter", -0.30), ("jitter", -0.33), ("pinch", -0.36)]
    log = _scroll_session(frames)
    assert all(a == "scroll_grab" for a, _, _ in log)
    assert not any(coast for _, _, coast in log)


@pytest.mark.record_off
def test_real_release_still_starts_the_inertia():
    frames = [("pinch", -0.035 * i) for i in range(9)] + [("open", -0.3)] * 12
    log = _scroll_session(frames)
    assert any(coast for _, _, coast in log[9:])


@pytest.mark.record_off
def test_long_hand_loss_ends_the_scroll_without_inertia():
    frames = [("pinch", -0.035 * i) for i in range(9)] + [("lost", None)] * 15  # 0.5 s gone
    log = _scroll_session(frames)
    assert not any(coast for _, _, coast in log)


def test_long_press_drag_survives_a_short_hand_loss():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    drag = GestureResult(active_gesture="thumb_middle_pinch", action="drag_hold",
                         cursor_point=(0.5, 0.5), is_moving=True)
    ctrl.apply(drag, {}, now=1.0)
    assert ctrl.is_dragging
    for i in range(5):                       # 0.17 s blur: still holding the file
        ctrl.hand_lost({}, now=1.03 + i / 30)
    assert ctrl.is_dragging
    for i in range(10):                      # gone for good: drop it
        ctrl.hand_lost({}, now=1.3 + i / 30)
    assert not ctrl.is_dragging

def test_index_preset_moves_gently_and_reanchors_after_click():
    mouse = MouseController(dry_run=True)
    config = {"movement_mode": "absolute", "smoothing": 1.0}
    move = GestureResult(action="move_cursor", is_moving=True, cursor_point=(.5, .5))
    try:
        initial = mouse.apply(move, config, now=1.0, index_tip=(.5, .5))
        moved = mouse.apply(move, config, now=1.04, index_tip=(.52, .5))
        assert 0 < moved[0] - initial[0] <= .02 * mouse.screen_w
        assert mouse.apply(GestureResult(action="left_click"), config,
                           now=1.10, index_tip=(.52, .5)) == moved
        assert mouse.apply(move, config, now=1.14, index_tip=(.65, .5)) == moved
        assert mouse.event_log[-1] == "left_click"
    finally:
        mouse.close()


def test_pointer_source_change_does_not_move_cursor_between_landmarks():
    mouse = MouseController(dry_run=True)
    move = GestureResult(action="move_cursor", is_moving=True, cursor_point=(.5, .5))
    try:
        initial = mouse.apply(move, now=1.0, index_tip=(.4, .5), pointer_source="palm")
        assert mouse.apply(move, now=1.04, index_tip=(.65, .3), pointer_source="index") == initial
        moved = mouse.apply(move, now=1.08, index_tip=(.68, .3), pointer_source="index")
        assert moved[0] > initial[0]
        assert mouse.apply(move, now=1.12, index_tip=(.43, .5), pointer_source="palm") == moved
    finally:
        mouse.close()



def test_warp_to_moves_now_and_relative_motion_continues_from_there():
    mouse = MouseController(dry_run=True)
    move = GestureResult(action="move_cursor", is_moving=True, cursor_point=(.5, .5))
    try:
        mouse.apply(move, now=1.0, index_tip=(.5, .5))
        mouse.apply(move, now=1.04, index_tip=(.56, .5))
        mouse.warp_to(300, 200)
        assert mouse.last_screen_pos == (300, 200)
        wait = GestureResult(action="pointer_wait")
        assert mouse.apply(wait, now=1.08, index_tip=(.60, .5)) == (300, 200)
        assert mouse.apply(move, now=1.12, index_tip=(.60, .5)) == (300, 200)  # re-anchors, no jump
        assert mouse.apply(move, now=1.16, index_tip=(.62, .5))[0] > 300
    finally:
        mouse.close()


def test_index_drag_hold_follows_the_tip_and_releases_the_button():
    mouse = MouseController(dry_run=True)
    drag = lambda x: GestureResult(action="drag_hold", is_moving=True, cursor_point=(x, .5))
    try:
        mouse.warp_to(400, 300)
        start = mouse.apply(drag(.5), now=1.0, index_tip=(.5, .5))
        assert start == (400, 300) and mouse.is_dragging
        assert mouse.apply(drag(.56), now=1.04, index_tip=(.56, .5))[0] > 400
        mouse.apply(GestureResult(action="pointer_wait"), now=1.08, index_tip=(.56, .5))
        assert not mouse.is_dragging
        assert mouse.event_log[-2:] == ["mouseDown", "mouseUp"]
    finally:
        mouse.close()


@pytest.mark.record_off
def test_long_press_drag_moves_the_cursor_at_15x():
    engine = GestureEngine()
    engine.config["long_press_time"] = 0.6   # these tests are about the drag, not the default delay
    out, _ = _run(engine, [_thumb_on(12, 0.02)] * 22)
    drags = [r for r in out if r.action == "drag_hold"]
    assert drags and all(r.dpi_multiplier == pytest.approx(15.0) for r in drags)

    def moved(mult):
        ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
        cfg = dict(REL_CFG)
        res = lambda x: GestureResult(active_gesture="thumb_middle_pinch", action="drag_hold",
                                      cursor_point=(x, 0.5), is_moving=True, dpi_multiplier=mult)
        ctrl.apply(res(0.50), cfg, now=0.0)
        start = ctrl.last_screen_pos[0]
        ctrl.apply(res(0.51), cfg, now=0.2)    # slow (0.05 frame widths/s): min gain
        return ctrl.last_screen_pos[0] - start
    assert moved(15.0) == pytest.approx(15 * moved(1.0), rel=0.1)


@pytest.mark.record_off
def test_ring_needs_a_real_touch_middle_unchanged():
    """Reported: right click fired before the ring finger actually touched the thumb."""
    out, _ = _run(GestureEngine(), [_thumb_on(16, 0.048)] * 8)   # almost touching
    assert "right_click" not in [r.action for r in out]
    out, _ = _run(GestureEngine(), [_thumb_on(16, 0.035)] * 8)   # touching
    assert "right_click" in [r.action for r in out]
    out, _ = _run(GestureEngine(), [_thumb_on(12, 0.048)] * 8)   # middle keeps its distance
    assert "left_click" in [r.action for r in out]


@pytest.mark.record_off
def test_default_long_press_does_not_turn_a_slow_click_into_a_drag():
    """A pinch held ~0.8 s (a slow click) is still just the click; the drag needs the 1.0 s default."""
    out, _ = _run(GestureEngine(), [_thumb_on(12, 0.02)] * 24)    # ~0.8 s
    actions = [r.action for r in out]
    assert actions.count("left_click") == 1 and "drag_hold" not in actions
    out, _ = _run(GestureEngine(), [_thumb_on(12, 0.02)] * 40)    # ~1.3 s
    assert "drag_hold" in [r.action for r in out]


@pytest.mark.record_off
def test_long_press_flag_does_not_outlive_the_pinch():
    """Logged 2026-11-04: after one long-press drag, a thumb+index grab cleared the held pinch but
    left `_long_fired` set, so EVERY later thumb+middle pinch became a drag instantly (the click
    looked ignored). A new pinch must wait the full long_press_time again."""
    engine = GestureEngine()
    engine.config["grab_scroll_enabled"] = True
    t = 0.0
    out = []
    for hand in ([_thumb_on(12, 0.02)] * 40            # long press -> drag (flag set)
                 + [_thumb_on(8, 0.02)] * 8            # thumb+index grab clears the held pinch
                 + [make_real_hand(curled=False)] * 12  # hand open
                 + [_thumb_on(12, 0.02)] * 10):        # a normal ~0.3 s click pinch
        out.append(engine.process(hand, timestamp=t))
        t += 1 / 30
    tail = [r.action for r in out[-10:]]
    assert "drag_hold" not in tail and "left_click" in tail


@pytest.mark.record_off
def test_thumb_index_grab_ends_when_the_tips_are_clearly_apart():
    """Logged 2026-11-04: with tips 0.06 apart (more than a palm width at that distance) the grab was
    still held for seconds because the scroll release margin made the limit 0.065."""
    engine = GestureEngine()
    engine.config.update(grab_scroll_enabled=True, release_threshold=0.045)   # the shipped config values
    hands = [_thumb_on(8, 0.02)] * 10 + [_thumb_on(8, 0.060)] * 12      # pinched, then ~0.4 s at 0.060
    out = []
    for i, h in enumerate(hands):
        out.append(engine.process(h, timestamp=i / 30))
    assert out[9].action == "scroll_grab"
    assert out[-1].action != "scroll_grab"


def _inertia_launch(moves, cfg_over=None):
    """Drive the grab scroll directly: moves = [(dx, dy)] per frame (hand point steps, 30 fps)."""
    cfg = dict(GestureEngine().config, scroll_inertia=True, **(cfg_over or {}))
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    x, y, t = 0.5, 0.5, 1.0
    ctrl._grab_scroll((x, y), cfg, t)
    for dx, dy in moves:
        t += 1 / 30
        x, y = x + dx, y + dy
        ctrl._grab_scroll((x, y), cfg, t)
    ctrl._end_scroll(cfg, t + 1 / 30)
    return ctrl


def test_inertia_follows_the_recent_flick_not_the_whole_grab():
    """A long pull down followed by a quick flick back up must coast up (it used to coast nowhere)."""
    pull_down = [(0, 0.004)] * 20
    flick_up = [(0, -0.012)] * 4
    ctrl = _inertia_launch(pull_down + flick_up)
    assert ctrl.last_inertia["same_way"] and ctrl.scroll_coasting
    assert ctrl._inertia_v[0] < 0          # hand up -> page content up -> wheel down (negative)


def test_inertia_stays_on_the_vertical_axis_for_a_vertical_flick():
    ctrl = _inertia_launch([(0, 0.004)] * 10 + [(0.002, 0.012)] * 4)   # ~17 % sideways wobble
    assert ctrl.scroll_coasting and ctrl._inertia_v[1] == 0.0 and ctrl._inertia_v[0] > 0
    diag = _inertia_launch([(0.012, 0.010)] * 6)                         # a real diagonal keeps both
    assert diag.scroll_coasting and diag._inertia_v[0] != 0 and diag._inertia_v[1] != 0


def test_inertia_launch_speed_is_capped():
    ctrl = _inertia_launch([(0, 0.004)] * 5 + [(0, 0.06)] * 4)
    assert ctrl.scroll_coasting and math.hypot(*ctrl._inertia_v) <= 3600.0 + 1e-6
    assert ctrl.last_inertia["raw"][0] > ctrl.last_inertia["used"][0]


@pytest.mark.record_off
def test_drag_ends_when_fingers_part_even_if_the_thumb_rests_near_the_middle_dip():
    """Logged 2026-11-04: tips 0.08-0.09 apart still held the drag because the thumb stayed ~0.07
    from the middle DIP (the DIP shortcut counted at any distance up to the release limit)."""
    engine = GestureEngine()
    engine.config["long_press_time"] = 0.6
    pinch = _thumb_on(12, 0.02)
    apart = list(pinch)
    apart[4] = Point3D(pinch[12].x + 0.0523, pinch[12].y + 0.067)   # tip 0.085 away
    k = 0.062 / 0.085                                                  # ...with the DIP only 0.062 from the thumb
    apart[11] = Point3D(apart[4].x + (pinch[12].x - apart[4].x) * k, apart[4].y + (pinch[12].y - apart[4].y) * k)
    out, _ = _run(engine, [pinch] * 22 + [apart] * 12)
    actions = [r.action for r in out]
    assert actions[21] == "drag_hold" and actions[-1] != "drag_hold"


@pytest.mark.record_off
def test_scroll_freezes_when_the_fingers_part_but_not_on_a_one_frame_spike():
    """Replay of 2026-11-04 logs: scrolling went on with the fingers already apart (hand kept 'browsing'),
    but freezing on every threshold touch stalled ~17 % of grab frames. A one-frame spike over the limit
    keeps scrolling; fingers staying apart (or clearly open) freeze the page."""
    def last(gap, n):
        e = GestureEngine()
        e.config.update(grab_scroll_enabled=True, release_threshold=0.045, scroll_release_margin=0.015)
        for i in range(6):
            e.process(_thumb_on(8, 0.02), timestamp=i / 30)
        r = None
        for i in range(n):
            r = e.process(_thumb_on(8, gap), timestamp=(6 + i) / 30)
        return r
    spike = last(0.065, 1)      # limit 0.06 -> 8 % over, first frame
    parted = last(0.065, 3)     # still apart 0.067 s later
    opening = last(0.085, 1)    # 40 % over: clearly opening at once
    assert spike.action == "scroll_grab" and not spike.releasing
    assert parted.action == "scroll_grab" and parted.releasing
    assert opening.action == "scroll_grab" and opening.releasing


def test_hovering_around_the_release_limit_does_not_keep_the_pinch_alive():
    """Logged 2026-11-04: the tips hovered just over the limit with one frame back under it now and then;
    every such frame restarted the release timer, so the page kept scrolling with the fingers apart."""
    e = GestureEngine()
    e.config.update(release_threshold=0.045, pinch_release_grace=0.15)
    t = 1.0
    assert e.process(make_hand(index=(0.53, 0.50)), timestamp=t).action == "grab"
    apart = make_hand(index=(0.62, 0.50))         # clearly over the limit
    close = make_hand(index=(0.53, 0.50))
    res = None
    for i in range(6):                            # apart x3, one close frame, apart x2: released by the 0.15 s grace
        t += 1 / 30
        res = e.process(close if i % 4 == 3 else apart, timestamp=t)
    assert res.active_gesture == "no_pinch"


# ------------------------------------------------------------------ scroll pump (smooth wheel stream)
from src.mouse_controller import ScrollPump


def test_scroll_pump_spreads_a_burst_over_many_small_steps_and_keeps_the_total():
    p = ScrollPump(tau=0.03)
    p.step(0.0)
    p.add(240, 0)                                   # one camera frame's worth: 2 notches at once
    sent = []
    for i in range(1, 60):                          # ticks every 8 ms for ~0.5 s
        v, _ = p.step(i * 0.008)
        sent.append(v)
    assert max(sent) < 90                           # no single event is the whole burst
    assert sum(1 for v in sent if v) >= 12          # a stream of many events
    assert abs(sum(sent) - 240) <= 2                # nothing lost (integer rounding only)
    assert sent[0] > 0 and sent[:5] == sorted(sent[:5], reverse=True)   # starts quickly, then tapers


def test_scroll_pump_clear_drops_what_was_not_sent_yet():
    p = ScrollPump(tau=0.03)
    p.step(0.0)
    p.add(500, 0)
    first = p.step(0.008)[0]
    p.clear()
    assert sum(p.step(0.008 * (i + 2))[0] for i in range(40)) == 0 and first > 0
    assert p.pending() == (0.0, 0.0)


def test_scroll_pump_flush_returns_the_rest_and_zero_tau_sends_at_once():
    p = ScrollPump(tau=0.03)
    p.step(0.0)
    p.add(100, 40)
    v1, h1 = p.step(0.008)
    v, h = p.flush()
    assert abs((v1 + v) - 100) <= 1 and abs((h1 + h) - 40) <= 1 and p.pending() == (0.0, 0.0)
    q = ScrollPump(tau=0.0)
    q.step(0.0)
    q.add(120, -60)
    assert q.step(0.01, tau=0.0) == (120, -60)


@pytest.mark.record_off
def test_cursor_stays_put_for_a_moment_after_a_scroll_grab_ends():
    """Logged 2026-11-04: the hand is still moving up/down when the grab ends, and the cursor followed it
    (17 px median, 65 px p90 in 0.4 s). It is held for scroll_cursor_hold, then follows the hand again."""
    engine = GestureEngine()
    engine.config["grab_scroll_enabled"] = True
    t = 1.0
    for i in range(8):                                          # grabbing, hand moving
        engine.process(_pinch_hand_at(-0.02 * i), timestamp=t); t += 1 / 30
    after = []
    for i in range(24):                                         # fingers open, hand keeps moving up
        r = engine.process(_pinch_hand_at(-0.16 - 0.02 * i, opened=True), timestamp=t)
        after.append((round(t - 1.0 - 8 / 30, 2), r.action)); t += 1 / 30
    held = [a for tt, a in after if tt < 0.30]
    assert "move_cursor" not in held[3:]                        # (the first frames are still the grace period)
    assert after[-1][1] == "move_cursor"                        # 0.7 s later it follows the hand again
    engine2 = GestureEngine()
    engine2.config.update(grab_scroll_enabled=True, scroll_cursor_hold=0.0)
    t = 1.0
    for i in range(8):
        engine2.process(_pinch_hand_at(-0.02 * i), timestamp=t); t += 1 / 30
    free = [engine2.process(_pinch_hand_at(-0.16 - 0.02 * i, opened=True), timestamp=t + i / 30).action for i in range(12)]
    assert "move_cursor" in free[6:]                            # without the hold it moves right away


def test_approach_zone_keeps_its_size_with_a_tight_release_threshold():
    """A profile with release_threshold 0.028 shrank the 'finger is coming, freeze the cursor' zone."""
    e = GestureEngine()
    e.config.update(pinch_threshold=0.022, release_threshold=0.028, approach_freeze_distance=0.045)
    near = [Point3D(p.x, p.y) for p in make_hand(index=(0.60, 0.50))]       # thumb 0.07 from the index tip
    near[8] = Point3D(near[4].x + 0.04, near[4].y)                           # 0.04: inside 0.045, outside 0.028
    assert e.process(near, timestamp=1.0).action != "move_cursor"
    e.config["approach_freeze_distance"] = 0.0
    assert e.process(near, timestamp=2.0).action == "move_cursor"


# ------------------------------------------------------------------ scrolling and inertia as one movement
def _seam_session(releasing_frames, finish="end", seamless=True):
    """Pull the page for 10 frames, then `releasing_frames` frames with the fingers opening (hand still
    drifting). finish: 'end' = release confirmed, 'close' = the fingers closed again."""
    cfg = dict(GestureEngine().config, scroll_inertia=True, scroll_inertia_seamless=seamless)
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    t, y = 1.0, 0.5
    ctrl._grab_scroll((0.5, y), cfg, t)
    totals = []
    for i in range(10):                              # pulling: ~270 wheel units per frame
        t += 1 / 30
        y += 0.01
        ctrl._grab_scroll((0.5, y), cfg, t)
        totals.append(ctrl.scroll_total[0])
    after_pull = ctrl.scroll_total[0]
    during = []
    for i in range(releasing_frames):
        t += 1 / 30
        before = ctrl.scroll_total[0]
        ctrl._grab_scroll((0.5, y + 0.002 * i), cfg, t, releasing=True)
        during.append(ctrl.scroll_total[0] - before)
    if finish == "end":
        t += 1 / 30
        ctrl._end_scroll(cfg, t)
    else:
        t += 1 / 30
        ctrl._grab_scroll((0.5, y + 0.05), cfg, t)           # fingers closed again, hand elsewhere
    return ctrl, after_pull, during


def test_the_page_keeps_moving_while_the_fingers_open_and_the_release_does_not_restart_it():
    ctrl, after_pull, during = _seam_session(4)
    assert all(d != 0 for d in during[1:])                  # no frame of standing still before the coast
    assert ctrl.scroll_coasting and ctrl.inertia_launches == 1   # confirming the release did not launch a second one
    assert ctrl.scroll_total[0] > after_pull
    for _ in range(5):                                      # and it goes on coasting afterwards
        ctrl._tick_scroll_inertia(dict(GestureEngine().config), 2.0 + _ / 30)
    assert ctrl.scroll_coasting is True or ctrl.scroll_total[0] > after_pull


def test_without_the_seamless_start_the_page_stands_still_while_the_fingers_open():
    ctrl, after_pull, during = _seam_session(4, seamless=False)
    assert during == [0, 0, 0, 0]                           # the old behaviour: a pause
    assert ctrl.inertia_launches == 1                        # coasting only started on confirmation


def test_closing_the_fingers_again_takes_the_provisional_coast_back_without_a_jump():
    ctrl, after_pull, during = _seam_session(3, finish="close")
    assert not ctrl.scroll_coasting and ctrl.inertia_launches == 1
    before = ctrl.scroll_total[0]
    cfg = dict(GestureEngine().config)
    ctrl._grab_scroll((0.5, 0.5 + 0.1 + 0.05), cfg, 3.0)     # the hand is exactly where the re-anchor put it
    assert ctrl.scroll_total[0] - before == 0                # ...so no jump: scrolling continues from here


# ------------------------------------------------------------------ one side pinch at a time; show desktop only when deliberate
@pytest.mark.record_off
def test_a_held_middle_pinch_is_not_turned_into_a_right_click_by_the_thumb_sliding_to_the_ring():
    """Logged 2026-11-04: 10 of 17 right clicks fired while thumb+middle was held for seconds (hand angle)."""
    engine = GestureEngine()
    out, t = _run(engine, [_thumb_on(12, 0.02)] * 12)                      # middle pinch held (click fired)
    both = _thumb_on(12, 0.03, [(16, 0.03)])                               # ring tip now as close as the middle
    ring_nearer = _thumb_on(16, 0.02, [(12, 0.04)])                        # the thumb has slid onto the ring
    out2, t = _run(engine, [both] * 4 + [ring_nearer] * 10, t0=t)
    assert "right_click" not in [r.action for r in out2]
    out3, _ = _run(engine, [make_real_hand(curled=False)] * 8 + [_thumb_on(16, 0.02)] * 8, t0=t)   # released, then ring
    assert "right_click" in [r.action for r in out3]


@pytest.mark.record_off
def test_the_exclusive_rule_can_be_switched_off():
    engine = GestureEngine()
    engine.config["side_pinch_exclusive"] = False
    out, t = _run(engine, [_thumb_on(12, 0.02)] * 12)
    out2, _ = _run(engine, [_thumb_on(16, 0.02, [(12, 0.045)])] * 10, t0=t)
    assert "right_click" in [r.action for r in out2]


def test_show_desktop_needs_a_still_hand_and_a_quiet_moment_before():
    # a hand that travels while in the posture (an open hand steering the cursor) never fires
    moving = [make_desktop_hand(shift=(0.012 * i, 0.0)) for i in range(40)]
    out, _ = _run(GestureEngine(), moving)
    assert "show_desktop" not in [r.action for r in out]
    out, _ = _run(GestureEngine(), [make_desktop_hand()] * 30)             # the same pose held still does
    assert "show_desktop" in [r.action for r in out]
    # right after another gesture ended (a scroll grab released a moment ago) it must wait
    e = GestureEngine()
    e._scroll_released_at = 0.0
    out, _ = _run(e, [make_desktop_hand()] * 25, t0=0.2)                   # 0.2 s .. 1.0 s after the release
    assert "show_desktop" not in [r.action for r in out]
    out, _ = _run(e, [make_desktop_hand()] * 25, t0=1.4)                   # held on past the quiet period
    assert "show_desktop" in [r.action for r in out]


def test_thumb_out_with_four_folded_fingers_is_the_motion_pose_not_a_fist():
    """It is the entry pose of motion mode; the fist branch would run the cursor at 20x DPI while it is held."""
    h = make_real_hand(curled=True)
    far = Point3D(h[0].x - 0.30, h[0].y)            # the thumb tip well away from the palm
    pose = list(h)
    pose[4] = far
    out, _ = _run(GestureEngine(), [pose] * 8)
    assert all(r.active_gesture == "motion_pose" and r.cursor_point is None and r.action == "none" for r in out)
    out, _ = _run(GestureEngine(), [h] * 8)         # an ordinary fist still is one
    assert any(r.active_gesture == "fist" for r in out)
    e = GestureEngine()
    e.config["motion_pose_freeze"] = False
    out, _ = _run(e, [pose] * 8)
    assert all(r.active_gesture != "motion_pose" for r in out)


# ------------------------------------------------------------------ browsing keeps the cursor in the middle of the page
def _browse_ctrl(target=(960, 540, "Chrome_WidgetWin_1"), start=(200, 150)):
    """A controller whose window probe answers `target`. The window fallback is opt-in (scroll_center_fallback_window)."""
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    ctrl.frame_aspect = 0.75
    ctrl.window_probe = (lambda x, y: target) if target is not None else (lambda x, y: None)
    ctrl.last_screen_pos = start
    return ctrl


def _move_result(x=0.5):
    return GestureResult(active_gesture="no_pinch", action="move_cursor", cursor_point=(x, 0.5), is_moving=True)


def test_a_page_grab_puts_the_cursor_in_the_middle_of_its_window_and_keeps_it_there():
    ctrl = _browse_ctrl()
    cfg = dict(GestureEngine().config, scroll_center_fallback_window=True)
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)                   # the grab starts
    assert ctrl.last_screen_pos == (960, 540) and "scroll_center:960,540:window" in ctrl.event_log
    for i in range(1, 8):
        ctrl._grab_scroll((0.5, 0.5 + 0.01 * i), cfg, 1.0 + i / 30)
    assert ctrl.last_screen_pos == (960, 540)                 # the hand moves, the cursor does not
    ctrl.apply(_move_result(0.3), cfg, now=1.3)               # (the engine would move it while the page is let go)
    assert ctrl.last_screen_pos == (960, 540)


def test_the_cursor_stays_during_the_coast_and_the_session_hold_then_follows_the_hand_again():
    ctrl = _browse_ctrl()
    cfg = dict(GestureEngine().config, scroll_inertia=True, scroll_center_fallback_window=True)
    t = 1.0
    ctrl._grab_scroll((0.5, 0.5), cfg, t)
    for i in range(10):                                        # a quick flick
        t += 1 / 30
        ctrl._grab_scroll((0.5, 0.5 + 0.02 * (i + 1)), cfg, t)
    ctrl._end_scroll(cfg, t)
    assert ctrl.scroll_coasting
    for i in range(30):                                        # 1 s of coasting while the hand returns
        t += 1 / 30
        ctrl.apply(_move_result(0.2 + 0.01 * i), cfg, now=t)
        assert ctrl.last_screen_pos == (960, 540), i
    ctrl._stop_scroll_inertia()
    t += 0.5
    ctrl.apply(_move_result(0.4), cfg, now=t)                  # inside scroll_session_hold (1.2 s): still locked
    assert ctrl.last_screen_pos == (960, 540)
    t += 1.0
    ctrl.apply(_move_result(0.4), cfg, now=t)
    ctrl.apply(_move_result(0.45), cfg, now=t + 1 / 30)
    assert ctrl.last_screen_pos != (960, 540)                  # after the hold it follows the hand again


def test_continuous_browsing_does_not_recentre_when_the_cursor_is_already_there():
    ctrl = _browse_ctrl()
    cfg = dict(GestureEngine().config, scroll_center_fallback_window=True)
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    ctrl._end_scroll(cfg, 1.5)
    n = sum(1 for e in ctrl.event_log if e.startswith("scroll_center"))
    ctrl._grab_scroll((0.5, 0.5), cfg, 2.0)                    # grabbing again within the session
    assert sum(1 for e in ctrl.event_log if e.startswith("scroll_center")) == n   # no second jump
    assert ctrl.last_screen_pos == (960, 540)


def test_no_window_or_switched_off_means_the_old_behaviour():
    cfg = dict(GestureEngine().config, scroll_center_fallback_window=True)
    ctrl = _browse_ctrl(target=None)                           # the desktop / our own window: nothing to centre on
    ctrl._grab_scroll((0.5, 0.5), cfg, 1.0)
    assert ctrl.last_screen_pos == (200, 150)
    ctrl = _browse_ctrl()
    ctrl._grab_scroll((0.5, 0.5), dict(cfg, scroll_center_cursor=False), 1.0)
    assert ctrl.last_screen_pos == (200, 150) and not ctrl._browse_centered      # switched off: no move, no lock
    ctrl.apply(_move_result(0.3), cfg, now=1.3)
    ctrl.apply(_move_result(0.35), cfg, now=1.33)
    assert ctrl.last_screen_pos != (200, 150)                  # (and the cursor follows the hand)


# ------------------------------------------------------------------ cursor speed follows how far the fingers are curled
def _shape_hand(tip_y, thumb=(0.30, 0.64)):
    """make_real_hand with every fingertip at tip_y (wrist 0.8, MCP 0.6, so the curl ratio is (0.8 - tip_y) / 0.2):
    0.40 open (2.0), 0.52 loose (1.4), 0.55 half (1.25), 0.58 (1.1), 0.68 fist (0.6). The thumb is far from the fingers."""
    h = make_real_hand(curled=True)
    for _, tip_i in GestureEngine.FINGER_JOINTS:
        h[tip_i] = Point3D(h[tip_i].x, tip_y)
        h[tip_i - 1] = Point3D(h[tip_i].x, (tip_y + 0.60) / 2)
    h[4] = Point3D(*thumb)
    return h


def _last(hands, config=None):
    e = GestureEngine()
    if config:
        e.config.update(config)
    r = None
    for i, h in enumerate(hands):
        r = e.process(h, timestamp=1.0 + i / 30)
    return r


def test_shape_dpi_runs_from_the_minimum_to_the_maximum_on_a_log_scale():
    e = GestureEngine()
    assert e.shape_dpi(2.0) == pytest.approx(1.0) and e.shape_dpi(1.45) == pytest.approx(1.0)      # open
    assert e.shape_dpi(0.85) == pytest.approx(25.0) and e.shape_dpi(0.3) == pytest.approx(25.0)    # fist
    assert e.shape_dpi(1.15) == pytest.approx(5.0, rel=0.01)                                      # halfway = the geometric mean
    values = [e.shape_dpi(c) for c in (1.4, 1.3, 1.2, 1.1, 1.0, 0.9)]
    assert values == sorted(values) and values[0] > 1.0 and values[-1] < 25.0


def test_the_cursor_speed_follows_how_far_the_hand_is_curled():
    open_, loose, half, fist = (_last([_shape_hand(y)] * 10) for y in (0.40, 0.52, 0.55, 0.68))
    assert open_.dpi_multiplier == 1.0 and open_.hand_shape == "open" and open_.action == "move_cursor"
    assert 1.0 < loose.dpi_multiplier < half.dpi_multiplier < 25.0 and half.hand_shape == "half"
    assert half.action == "move_cursor" and half.is_moving
    f = _last([make_real_hand(curled=True)] * 10)
    assert f.hand_shape == "fist" and f.dpi_multiplier == pytest.approx(25.0)             # a real fist is the maximum


def test_closing_the_hand_speeds_the_cursor_up_smoothly_and_opening_slows_it_down_again():
    e = GestureEngine()
    mult = []
    for i, y in enumerate([0.40] * 4 + [0.44, 0.48, 0.52, 0.55, 0.55, 0.55] + [0.40] * 8):
        mult.append(e.process(_shape_hand(y), timestamp=1.0 + i / 30).dpi_multiplier)
    closing = mult[4:10]
    assert closing == sorted(closing) and closing[-1] > 1.5 and closing[0] < closing[-1]
    assert max(b - a for a, b in zip(mult, mult[1:])) < 8.0                                # no jump from 1x to 25x
    assert mult[-1] == pytest.approx(1.0, abs=0.2)


def test_a_curled_pinch_pose_is_not_sped_up_and_a_thumb_about_to_touch_still_freezes_the_cursor():
    pinch = _last([_shape_hand(0.55, thumb=(0.4375, 0.55))] * 8)             # curled, the thumb ON the index tip
    assert pinch.dpi_multiplier == 1.0 and pinch.action != "move_cursor" or pinch.dpi_multiplier == 1.0
    near = _last([_shape_hand(0.55, thumb=(0.4375, 0.55 + 0.02))] * 8)       # 0.02 from it: about to touch
    assert near.action != "move_cursor"


def test_the_speed_steps_are_settings():
    assert _last([_shape_hand(0.55)] * 8, {"shape_dpi_enabled": False}).dpi_multiplier == 1.0
    assert _last([make_real_hand(curled=True)] * 8, {"fist_dpi_multiplier": 30.0}).dpi_multiplier == pytest.approx(30.0)
    flat = _last([_shape_hand(0.55)] * 8, {"shape_dpi_curve": 0.5})
    steep = _last([_shape_hand(0.55)] * 8, {"shape_dpi_curve": 2.0})
    assert flat.dpi_multiplier > _last([_shape_hand(0.55)] * 8).dpi_multiplier > steep.dpi_multiplier
    assert _last([_shape_hand(0.55)] * 8, {"shape_dpi_min": 2.0}).dpi_multiplier > _last([_shape_hand(0.55)] * 8).dpi_multiplier
