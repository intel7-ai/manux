"""Tests for cursor interpolation (smooth 144 Hz cursor motion from 30 fps camera feed)."""

from __future__ import annotations

import time
import pytest

from manux.core.contracts import ActionCommand, OutputTarget
from manux.output.windows import FakeBackend, WindowsOutputConfig, WindowsOutputSink
from manux.platform.monitors import ScreenLayout
from manux.output.windows.interp import CursorInterpolator

FRAME = 1.0 / 30.0


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, x: int, y: int) -> None:
        self.calls.append((time.perf_counter(), x, y))


def test_glides_from_current_position_to_target_over_one_frame():
    rec = Recorder()
    it = CursorInterpolator(set_pos=rec)
    it.set_target(100, 100, now=0.0)
    it.set_target(130, 100, now=FRAME)
    assert it.position_at(FRAME) == pytest.approx((100, 100))
    # Use the entire camera interval: reaching it halfway used to leave a visible idle half-frame.
    assert it.position_at(FRAME + FRAME * 0.25) == pytest.approx((107.5, 100), abs=0.5)
    assert it.position_at(FRAME + FRAME * 0.75) == pytest.approx((122.5, 100), abs=0.5)
    assert it.position_at(FRAME * 2) == pytest.approx((130, 100))
    assert not it.done_at(FRAME * 1.5)
    assert it.done_at(FRAME * 2)
    it.close()


def test_retarget_mid_glide_continues_smoothly_without_jumps():
    rec = Recorder()
    it = CursorInterpolator(set_pos=rec)
    it.set_target(0, 0, now=0.0)
    it.set_target(300, 0, now=FRAME)
    mid = it.position_at(FRAME * 1.25)
    assert mid is not None and 0 < mid[0] < 300
    it.set_target(300, 300, now=FRAME * 1.25)
    assert it.position_at(FRAME * 1.25) == pytest.approx(mid)
    it.close()


def test_snap_lands_exactly_on_target_immediately():
    rec = Recorder()
    it = CursorInterpolator(set_pos=rec)
    it.set_target(10, 10, now=0.0)
    it.set_target(500, 400, now=FRAME)
    it.snap()
    assert rec.calls[-1][1:] == (500, 400)
    assert it.position_at(FRAME) == (500, 400)
    it.close()


def test_thread_moves_cursor_multiple_times_per_frame():
    rec = Recorder()
    it = CursorInterpolator(set_pos=rec, rate_hz=144.0)
    t = time.perf_counter()
    it.set_target(0, 0, now=t)
    it.snap(0, 0)
    for k in range(1, 5):
        time.sleep(FRAME)
        it.set_target(50 * k, 0, now=time.perf_counter())
    time.sleep(0.08)
    it.close()
    xs = [x for _, x, _ in rec.calls]
    assert xs[-1] == 200
    assert len(set(xs)) >= 4 * 2


def test_threaded_sink_interpolates_and_snaps_before_clicks():
    backend = FakeBackend((0, 0))
    sink = WindowsOutputSink(backend, ScreenLayout.single(1920, 1080), WindowsOutputConfig(), threaded=True)
    try:
        now = time.perf_counter()
        # Move cursor
        cmd_move = ActionCommand(now, "mouse.move", "hold", "pinch.move", (0.5, 0.5))
        sink.apply(cmd_move)
        # Left click should snap immediately to cursor position
        cmd_click = ActionCommand(now + 0.03, "mouse.left_click", "tap", "pinch.thumb_middle", (0.5, 0.5))
        sink.apply(cmd_click)
        assert backend.clicks() == ["left"]
        assert backend.cursor_pos() == sink.cursor.pos
    finally:
        sink.close()
