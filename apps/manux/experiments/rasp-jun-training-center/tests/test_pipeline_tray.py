"""Pure-logic tests for the status text, overlay placement and the inference pipeline."""

import time
from types import SimpleNamespace

from src.overlay import CORNERS, HEIGHT, WIDTH, corner_position
from src.pipeline import InferencePipeline
from src.status import describe_state, short_label


def _res(**kw):
    base = dict(action="none", active_gesture="NONE", is_moving=False, dpi_multiplier=1.0, distances={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_describe_state_priorities():
    assert describe_state(_res(), paused=True)[0].startswith("PAUSED")
    assert describe_state(_res(action="scroll_grab"))[0].startswith("SCROLLING")
    assert describe_state(_res(action="move_cursor", active_gesture="fist", dpi_multiplier=20.0))[0].startswith("FAST")
    assert describe_state(_res(), recent_action="left_click")[0] == "ACTION: LEFT_CLICK"
    assert describe_state(_res())[0] == "NO HAND"


def test_short_label():
    assert short_label(_res(), has_hand=False)[0] == "NO HAND"
    assert short_label(_res(), has_hand=True, paused=True)[0] == "PAUSED"
    assert short_label(_res(action="drag_hold"), has_hand=True)[0] == "DRAG"
    assert short_label(_res(action="move_cursor"), has_hand=True)[0] == "TRACKING"
    assert short_label(_res(), has_hand=True, recent_action="right_click")[0] == "RIGHT CLICK"


def test_overlay_corners_stay_on_screen():
    for corner in CORNERS:
        x, y = corner_position(corner, 1920, 1080)
        assert 0 <= x <= 1920 - WIDTH and 0 <= y <= 1080 - HEIGHT
    assert corner_position("top-left", 1920, 1080)[0] < corner_position("top-right", 1920, 1080)[0]


class _FakeCamera:
    is_phone_web = False
    phone_server = None
    fps = 30.0
    dropped_frames = 0
    config = SimpleNamespace(source=0)

    def read(self):
        import numpy as np
        time.sleep(0.002)
        return True, np.zeros((48, 64, 3), dtype=np.uint8)


class _FakeTracker:
    last_detect_ms = 1.0

    def detect(self, frame):
        return []


class _FakeEngine:
    config = {}
    pinched_fingers = set()

    def process(self, landmarks):
        return _res()


class _FakeMouse:
    recorded_path = []
    fling_count = 0
    grab_speed = 0.0
    switcher_open = False
    last_screen_pos = (1, 2)
    frame_aspect = 1.0
    released = 0

    def hand_lost(self, config):
        return self.last_screen_pos

    def release_all(self):
        self.released += 1


def _pipeline(max_frames=0):
    return InferencePipeline(_FakeCamera(), _FakeTracker(), _FakeEngine(), _FakeMouse(), None, max_frames)


def test_pipeline_runs_without_a_renderer_and_skips_frames_when_hidden():
    p = _pipeline(max_frames=20)
    p.publish_frames = False
    p.start()
    deadline = time.time() + 5
    while not p.finished and time.time() < deadline:
        time.sleep(0.01)
    p.stop()
    assert p.frames == 20 and p.error is None
    assert p.latest().frame is None and p.latest().has_hand is False


def test_pipeline_publishes_frames_when_visible_and_runs_posted_tasks():
    p = _pipeline()
    p.start()
    snap = p.wait_snapshot(2.0)
    assert snap is not None and snap.frame is not None
    p.post(lambda: "done")
    p.set_paused(True)
    deadline = time.time() + 2
    while p.messages.empty() and time.time() < deadline:
        time.sleep(0.01)
    assert p.messages.get_nowait() == "done"
    p.stop()
    assert p.mouse.released >= 1  # pausing releases held buttons on the pipeline thread
