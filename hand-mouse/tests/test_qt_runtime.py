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
    for _ in range(200):
        snap = steps(rt, 1)
        if snap.failures:
            seen = snap.failures
            break
    assert seen is not None and "직접 표시 1건" in seen[0]
    assert len(list((tmp_path / "failures").glob("*-manual.json"))) == 1


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
