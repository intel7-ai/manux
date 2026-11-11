"""Regression tests for user-registered motions / composites (training data, runtime shape checks)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.action_bindings import DIRECTION_NAMES_KO, POSTURE_NAMES_KO, ActionBindingRegistry, CompositeGestureDetector
from src.gesture_catalog import BY_KEY, direction_speed
from src.motion_mode import custom_posture_ok, hand_features
from tests.test_motion_mode import _KNUCKLES_UP_FIST, _THUMBS_UP, hand


def _pts(rows):
    return [SimpleNamespace(x=r[0], y=r[1], z=r[2]) for r in rows]


def test_every_registrable_direction_has_a_velocity_projection():
    for d in DIRECTION_NAMES_KO:
        assert direction_speed(d, 0.0, 0.0) == 0.0
    assert direction_speed("up", 0.0, -1.0) == pytest.approx(1.0)        # image y grows downward
    assert direction_speed("up", 0.0, 1.0) == pytest.approx(-1.0)
    assert direction_speed("diag_down_right", 1.0, 1.0) > 1.0
    assert direction_speed("left", -0.5, 0.0) == pytest.approx(0.5)
    assert direction_speed("nowhere", 1.0, 1.0) is None


def test_training_data_loads_for_an_up_motion(tmp_path: Path):
    """Used to fail with KeyError: 'up' (only left/right/down were handled), which broke training and the simulator."""
    from src.capture_dataset import load_sessions
    from src.gesture_engine import Point3D
    from src.hand_tracker import HandDetection
    from src.recorder import SessionRecorder

    reg = ActionBindingRegistry(path=tmp_path / "bindings.json")
    key = reg.register_motion_gesture(key="", name="", posture="open_hand", direction="up",
                                      action_type="hotkey", action_value="ctrl+t")["key"]
    try:
        rec = SessionRecorder(tmp_path / "rec", "guided", {"camera": {}}, now=0.0)
        t = 0.0
        for i in range(60):
            phase = "do" if 20 <= i < 40 else "rest"
            dy = -0.01 * (i - 20) if phase == "do" else 0.0
            lm = [Point3D(p.x, p.y + dy, 0.0) for p in hand()]
            det = HandDetection(landmarks=lm, handedness="Right", score=0.9, world=[(0.01 * j, 0.0, 0.0) for j in range(21)])
            label = {"target": key if phase == "do" else "none", "task": key, "phase": phase, "rep": 1, "set": 0,
                     "condition": "기본"}
            rec.frame(t, det, label=label, rule={"action": "none"})
            t += 1 / 30
        path = rec.close()
        load_sessions([path], window=15)            # must not raise
    finally:
        reg.delete_motion_gesture(key)
    assert key not in BY_KEY


@pytest.mark.parametrize("posture", sorted(POSTURE_NAMES_KO))
def test_every_registrable_posture_has_a_shape_check(posture):
    """A posture the form offers must not silently mean "any hand" (only "any" does)."""
    open_hand = hand_features(hand(fingers="open", thumb="out"))
    if posture in ("any", "open_hand"):
        assert custom_posture_ok(posture, open_hand)
    else:
        assert not custom_posture_ok(posture, open_hand), posture
    assert not custom_posture_ok("no_such_shape", open_hand)


def test_pinch_and_thumbs_up_shapes_are_recognised():
    from tests.test_gesture_mouse import _thumb_on
    for posture, tip in (("thumb_index", 8), ("thumb_middle", 12), ("thumb_ring", 16), ("thumb_pinky", 20)):
        f = hand_features(_thumb_on(tip, 0.01))
        assert custom_posture_ok(posture, f), posture
        others = {"thumb_index", "thumb_middle", "thumb_ring", "thumb_pinky"} - {posture}
        assert not any(custom_posture_ok(o, f) for o in others), posture
    assert custom_posture_ok("thumbs_up", hand_features(_pts(_THUMBS_UP)))
    assert not custom_posture_ok("thumbs_up", hand_features(_pts(_KNUCKLES_UP_FIST)))


def test_composite_detector_sees_a_thumbs_up_base_shape():
    """Composite base "thumbs_up" was offered but never detected, so such a composite could never fire."""
    assert CompositeGestureDetector._detect_base_posture(_pts(_THUMBS_UP), "fist", set()) == "thumbs_up"
    assert CompositeGestureDetector._detect_base_posture(_pts(_KNUCKLES_UP_FIST), "fist", set()) == "fist"


def test_registering_never_overwrites_an_existing_gesture(tmp_path: Path):
    reg = ActionBindingRegistry(path=tmp_path / "bindings.json")
    keys = []
    try:
        a = reg.register_motion_gesture("", "첫 번째", "open_hand", "up", "hotkey", "ctrl+t")["key"]
        b = reg.register_motion_gesture("", "두 번째", "open_hand", "up", "hotkey", "ctrl+w")["key"]
        keys += [a, b]
        assert a != b and reg.custom_motions[a]["name"] == "첫 번째" and reg.custom_motions[b]["name"] == "두 번째"
        back = reg.get_binding("swipe_left")["action_value"]
        with pytest.raises(ValueError):
            reg.register_motion_gesture("swipe_left", "", "fist", "up", "hotkey", "ctrl+q")
        assert reg.get_binding("swipe_left")["action_value"] == back
        with pytest.raises(ValueError):
            reg.register_motion_gesture(a, "", "fist", "up", "hotkey", "ctrl+q")
        c1 = reg.register_composite_gesture("", "", "thumb_index", "hold", "hotkey", "f5")["key"]
        c2 = reg.register_composite_gesture("", "", "thumb_index", "hold", "hotkey", "f6")["key"]
        assert c1 != c2 and len(reg.composite_gestures) == 2
    finally:
        for k in keys:
            reg.delete_motion_gesture(k)
