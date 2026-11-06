"""Tests for cursor interpolation (the real cursor is never moved here)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.cursor_interp import CursorInterpolator
from src.mouse_controller import MouseController

FRAME = 1 / 30


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, x, y):
        self.calls.append((time.perf_counter(), x, y))


def test_glides_from_current_position_to_target_over_one_frame():
    it = CursorInterpolator(set_pos=Recorder())
    it.set_target(100, 100, now=0.0)
    it.set_target(130, 100, now=FRAME)        # next camera frame
    assert it.position_at(FRAME) == pytest.approx((100, 100))
    # the glide takes glide_fraction (0.5) of a frame interval: the cursor is there early instead of one frame late
    assert it.position_at(FRAME + FRAME / 4) == pytest.approx((115, 100), abs=0.5)
    assert it.position_at(FRAME * 2) == pytest.approx((130, 100))
    assert it.done_at(FRAME * 1.5)
    it.close()


def test_retarget_mid_glide_continues_from_where_the_cursor_is():
    it = CursorInterpolator(set_pos=Recorder())
    it.set_target(0, 0, now=0.0)
    it.set_target(300, 0, now=FRAME)
    mid = it.position_at(FRAME * 1.5)         # halfway: ~150
    it.set_target(300, 300, now=FRAME * 1.5)  # new target arrives early
    assert it.position_at(FRAME * 1.5) == pytest.approx(mid)  # no jump
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


def test_thread_moves_cursor_many_times_per_camera_frame():
    rec = Recorder()
    it = CursorInterpolator(set_pos=rec, rate_hz=144)
    t = time.perf_counter()
    it.set_target(0, 0, now=t)
    it.snap(0, 0)
    for k in range(1, 7):                     # 6 camera frames at ~30 fps, 60 px each
        time.sleep(FRAME)
        it.set_target(60 * k, 0, now=time.perf_counter())
    time.sleep(0.08)
    it.close()
    xs = [x for _, x, _ in rec.calls]
    assert xs[-1] == 360                       # ends exactly on the last target
    assert len(set(xs)) >= 6 * 3               # several intermediate positions per frame
    steps = [b - a for a, b in zip(xs, xs[1:])]
    assert max(steps) < 60                     # never the whole 60 px frame jump at once


def test_controller_uses_interpolation_and_snaps_before_clicks():
    ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
    rec = Recorder()
    ctrl._interp = CursorInterpolator(set_pos=rec)
    ctrl.dry_run = False          # only exercise the cursor path (no clicks are sent here)
    ctrl.interpolation = True
    ctrl._move_os_cursor(100, 100)
    ctrl._move_os_cursor(400, 300)
    assert ctrl.last_screen_pos == (400, 300)  # logic always uses the exact target
    ctrl._flush_cursor()
    assert rec.calls[-1][1:] == (400, 300)     # real cursor exactly on target before a click
    ctrl.interpolation = False
    ctrl._move_os_cursor(50, 60)
    assert rec.calls[-1][1:] == (50, 60)       # interpolation off: immediate
    ctrl.close()
    ctrl.dry_run = True
