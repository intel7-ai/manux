import json
"""QtRuntime (camera -> tracking -> pointer -> mouse thread) with a fake camera and tracker."""
import time

import numpy as np
import pytest

from src.gesture_engine import GestureEngine, Point3D
from src.hand_tracker import HandDetection
from src.mouse_controller import MouseController
from src.qt_runtime import QtRuntime, UiState
from tests.test_gesture_mouse import make_real_hand


class FakeCamera:
    def __init__(self):
        self.config = type("C", (), {"source": 0, "mirror": True})()
        self.is_phone_web = False
        self.phone_server = None
        self.fps = 30.0
        self.dropped_frames = 0

    def read(self):
        return True, np.full((720, 1280, 3), 90, np.uint8)

    def actual_mode(self):
        return (1280, 720, 30.0)


class FakeTracker:
    """Returns one "Left"-labelled hand (a mirrored image of the physical right hand)."""
    def __init__(self):
        self.dx = 0.0
        self.hand = make_real_hand(curled=False)
        self.present = True
        self.last_detect_ms = 9.0
        self.options = {"min_detection_confidence": .6}

    def detect(self, frame, roi=None):
        if not self.present:
            return []
        lms = [Point3D(p.x + self.dx, p.y, p.z) for p in self.hand]
        return [HandDetection(landmarks=lms, handedness="Left", score=.95)]


def runtime(tmp_path):
    rt = QtRuntime(camera=FakeCamera(), tracker=FakeTracker(), engine=GestureEngine(),
                   mouse=MouseController(dry_run=True, screen_size=(1920, 1080)), perf_log=None,
                   dry_run=True, log_dir=tmp_path)
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off")
    return rt


def steps(rt, n, move=0.0):
    for _ in range(n):
        rt.tracker.dx += move
        rt._step()
        time.sleep(1 / 120)
    return rt._snap


def test_starts_paused_then_space_moves_the_cursor(tmp_path):
    rt = runtime(tmp_path)
    start = rt.mouse.last_screen_pos
    snap = steps(rt, 10, move=.01)
    assert snap.paused and rt.mouse.last_screen_pos == start and snap.detected
    rt.post("key", ord(" "))
    snap = steps(rt, 20, move=.01)
    assert not snap.paused and rt.mouse.last_screen_pos[0] > start[0]
    assert snap.metrics[0].startswith("1280") and "클릭 끔" in snap.status


def test_tracking_test_view_lifecycle(tmp_path):
    rt = runtime(tmp_path)
    rt.post("tracking_start")
    snap = steps(rt, 2)
    v = snap.tracking
    assert v is not None and v["paused"] and v["total"] == 12 and v["target"] is not None
    rt.post("key", ord(" "))
    snap = steps(rt, 3)
    assert not snap.tracking["paused"]
    rt.post("tracking_close")
    snap = steps(rt, 2)
    assert snap.tracking is None and snap.paused


def test_failure_mark_saves_a_file_and_reports_it(tmp_path):
    rt = runtime(tmp_path)
    rt.post("key", ord(" "))
    steps(rt, 5)
    rt.post("failure_mark")
    seen = None
    for _ in range(500):   # the recorder keeps 2 s after the mark
        snap = steps(rt, 1)
        if snap.failures:
            seen = snap.failures
            break
    assert seen is not None and "직접 표시 1건" in seen[0]
    assert len(list((tmp_path / "failures").glob("*-manual.json"))) == 1
    data = json.loads(next((tmp_path / "failures").glob("*-manual.json")).read_text(encoding="utf-8"))
    assert data["schema"] == 2 and "summary" in data and "config" in data["meta"]
    assert any(f.get("path") == "full" and "raw" in f for f in data["frames"])
    assert (tmp_path / "failures" / "index.csv").exists()


def test_hand_setup_stage_forwards_detections_and_keeps_control_off(tmp_path):
    rt = runtime(tmp_path)
    rt.post("key", ord(" "))
    steps(rt, 2)
    rt.ui = UiState(stage="capture", control_hand="Right")
    snap = steps(rt, 2, move=.02)
    assert snap.paused and len(snap.detections) == 1 and not snap.detected


def test_runs_on_its_own_thread_and_stops(tmp_path):
    rt = runtime(tmp_path)
    rt.max_frames = 15
    rt.start()
    got = None
    deadline = time.time() + 10
    while time.time() < deadline and not rt.finished:
        got = rt.wait_snapshot(.1) or got
    assert rt.finished and rt.error is None and got is not None and rt.frames == 15


def test_cursor_keeps_moving_while_the_ui_thread_is_busy(tmp_path):
    """The reason for the thread: a stalled UI frame must not stall tracking or the cursor."""
    rt = runtime(tmp_path)
    real_read = rt.camera.read

    def paced_read():
        time.sleep(1 / 30)
        rt.tracker.dx += .004              # the hand keeps moving
        return real_read()

    rt.camera.read = paced_read
    rt.post("key", ord(" "))
    rt.start()
    try:
        time.sleep(.2)
        frames0, pos0 = rt.frames, rt.mouse.last_screen_pos
        time.sleep(.5)                      # UI thread "busy": no polling, no rendering
        assert rt.frames - frames0 >= 10
        assert rt.mouse.last_screen_pos[0] > pos0[0]
    finally:
        rt.stop()
    assert rt.error is None


def test_picking_a_calibration_profile_reloads_both_engines(tmp_path, monkeypatch):
    from src import profiles
    monkeypatch.setattr(profiles, "RUN_OVERRIDE", None)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    import src.gesture_engine as ge
    monkeypatch.setattr(ge, "DEFAULT_CONFIG_PATH", cfg_dir / "gestures.json")
    profiles.save_profile(cfg_dir, "민수", {"pinch_threshold": 0.0123})
    rt = runtime(tmp_path)
    base = rt.engine.config["pinch_threshold"]
    steps(rt, 2)
    assert rt.engine.config["pinch_threshold"] == base
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", profile="민수")
    steps(rt, 2)
    assert rt.engine.config["pinch_threshold"] == 0.0123
    assert rt.pointer.gestures.config["pinch_threshold"] == 0.0123
    assert profiles.get_active(cfg_dir) == "민수"
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", profile=None)
    steps(rt, 2)
    assert rt.engine.config["pinch_threshold"] == base and profiles.get_active(cfg_dir) is None


def test_motion_mode_suspends_the_pointer_and_sends_the_swipe_command(tmp_path):
    from tests.test_motion_mode import hand as motion_hand
    rt = runtime(tmp_path)
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", motion_request=0)
    rt.post("key", ord(" "))                                   # start controlling
    rt.tracker.hand = [Point3D(p.x, p.y, 0.0) for p in motion_hand(cx=0.30, fingers="open", thumb="out")]
    steps(rt, 4)
    assert not rt.motion.active
    pos = rt.mouse.last_screen_pos
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", motion_request=1)   # the button
    steps(rt, 3)
    assert rt.motion.active
    # a swipe to the user's right with the thumb out = "forward"; the pointer does not follow the hand
    rt.tracker.dx = 0.0
    steps(rt, 22, move=0.0135)
    assert "motion:forward" in rt.mouse.event_log
    assert rt.mouse.last_screen_pos == pos
    assert rt._snap.motion[0] is True
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", motion_request=2)   # button again
    steps(rt, 3)
    assert not rt.motion.active


def test_pressing_the_motion_button_while_paused_does_nothing(tmp_path):
    rt = runtime(tmp_path)
    rt.ui = UiState(stage="main", control_hand="Right", click_style="off", motion_request=1)
    steps(rt, 5)                                               # still paused (starts paused)
    assert not rt.motion.active


def test_cursor_direction_wizard_measures_saves_and_straightens_the_palm_cursor(tmp_path):
    """The palm cursor wizard: up/down strokes, the cursor does not move meanwhile, and a tilted up/down stroke
    afterwards moves the cursor straight down."""
    import math
    rt = runtime(tmp_path)
    rt.engine.config.update({"palm_axes_prep": 0.05, "palm_axes_stroke": 0.6, "palm_axes_strength": 1.0})
    base = [Point3D(p.x, p.y, p.z) for p in rt.tracker.hand]
    tilt = math.radians(30)

    def place(s, direction):
        ux, uy = (math.sin(tilt), math.cos(tilt)) if direction == "v" else (1.0, 0.0)
        rt.tracker.hand = [Point3D(p.x + ux * s, p.y + uy * s / rt.mouse.frame_aspect * 1.0, p.z) for p in base]

    ui = dict(stage="main", control_hand="Right", click_style="off", use_index_pointer=False)
    rt.ui = UiState(**ui)
    rt.post("key", ord(" "))                                   # start controlling
    steps(rt, 2)
    rt.ui = UiState(**ui, palm_axes_request=1)
    pos = rt.mouse.last_screen_pos
    t_end = time.perf_counter() + 3.0
    k = 0
    while rt._snap.palm_axes[0] or k < 3:
        place(0.05 * math.sin(k / 3.0), "v")
        steps(rt, 1)
        k += 1
        assert time.perf_counter() < t_end, rt._snap.palm_axes
    assert rt.mouse.last_screen_pos == pos                      # the cursor stayed put during the wizard
    assert rt._snap.palm_axes[4], rt._snap.palm_axes            # correction saved
    saved = json.loads((tmp_path / "no_user_config.json").read_text(encoding="utf-8"))
    assert len(saved["palm_axes"]) == 4
    # a tilted "down" stroke now moves the cursor (nearly) straight down
    start = rt.mouse.last_screen_pos
    for i in range(20):
        place(0.008 * i, "v")
        steps(rt, 1)
    end = rt.mouse.last_screen_pos
    assert end[1] - start[1] > 20 and abs(end[0] - start[0]) < 0.25 * (end[1] - start[1])


def test_elbow_pivot_straightens_a_bend_toward_the_hidden_elbow(tmp_path):
    """Palm cursor with `elbow_pivot` on: left/right swings about an elbow below the picture locate it; bending the
    forearm afterwards (a tilted line toward the elbow in the image) moves the cursor straight down."""
    import math
    rt = runtime(tmp_path)
    rt.engine.config["elbow_pivot"] = True
    base = [Point3D(p.x, p.y, p.z) for p in rt.tracker.hand]
    anchor = rt.engine.palm_anchor_point(base)
    aspect = rt.mouse.frame_aspect if rt.mouse.frame_aspect else 9 / 16
    elbow = (anchor[0] + 0.25, anchor[1] * (720 / 1280) + 0.3)       # square units, below and to the right
    r0 = math.hypot(anchor[0] - elbow[0], anchor[1] * (720 / 1280) - elbow[1])
    phi0 = math.atan2(anchor[0] - elbow[0], -(anchor[1] * (720 / 1280) - elbow[1]))

    def place(phi, r):
        x, y = elbow[0] + r * math.sin(phi), (elbow[1] - r * math.cos(phi)) / (720 / 1280)
        rt.tracker.hand = [Point3D(p.x + x - anchor[0], p.y + y - anchor[1], p.z) for p in base]

    ui = dict(stage="main", control_hand="Right", click_style="off", use_index_pointer=False)
    rt.ui = UiState(**ui)
    rt.post("key", ord(" "))
    for k in range(90):                                              # swing left / right about the elbow
        place(phi0 + math.radians(15) * math.sin(2 * math.pi * k / 30), r0)
        steps(rt, 1)
    assert rt.elbow.source == "arcs", rt.elbow.info()
    place(phi0, r0)
    steps(rt, 3)
    start = rt.mouse.last_screen_pos
    for i in range(20):                                              # bend: toward the elbow along the tilted line
        place(phi0, r0 - 0.008 * i)
        steps(rt, 1)
    end = rt.mouse.last_screen_pos
    assert end[1] - start[1] > 20 and abs(end[0] - start[0]) < 0.2 * (end[1] - start[1]), (start, end)
