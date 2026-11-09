"""The cursor glides between camera frames (>= 60 Hz) instead of stepping once per frame."""
from __future__ import annotations

import time

from manux.core.contracts import ActionCommand
from manux.output.windows.backend import FakeBackend
from manux.output.windows.config import WindowsOutputConfig
from manux.output.windows.sink import WindowsOutputSink
from manux.platform.monitors import ScreenLayout

ABS = WindowsOutputConfig(movement_mode="absolute", smoothing=1.0, active_margin=0.0)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def manual_sink(config=ABS):
    clock = Clock()
    sink = WindowsOutputSink(FakeBackend((960, 540)), ScreenLayout.single(1920, 1080), config,
                             threaded=True, clock=clock)
    sink._ensure_glide = lambda: None     # drive glide_step by hand
    return sink, clock


def moves(sink):
    return [e[1:] for e in sink.backend.events if e[0] == "move"]


def move(sink, t, x, y=.5):
    return sink.apply(ActionCommand(t, "mouse.move", "hold", "point.move", point=(x, y)))


def test_glides_several_steps_per_camera_frame():
    sink, clock = manual_sink()
    for frame in range(10):                  # 30 fps, hand sweeping right
        clock.t = 100 + frame / 30
        move(sink, clock.t, .2 + .05 * frame)
        for tick in range(1, 5):             # 120 Hz glide ticks
            sink.glide_step(clock.t + tick / 120)
    xs = [x for x, _ in moves(sink)][3:]
    assert len(xs) >= 3 * 8                  # >= 3 cursor updates per frame, i.e. >= 90 Hz here
    assert all(b >= a for a, b in zip(xs, xs[1:]))
    assert max(b - a for a, b in zip(xs, xs[1:])) < .05 * 1920 / 2


def test_glide_ends_on_the_target():
    sink, clock = manual_sink()
    move(sink, 100, .3)
    clock.t = 100 + 1 / 30
    move(sink, clock.t, .7)
    while sink.glide_step(clock.t):
        clock.t += 1 / 120
    assert sink.backend.pos == sink.cursor_pos


def test_click_lands_on_the_hand_not_mid_glide():
    sink, clock = manual_sink()
    move(sink, 100, .3)
    clock.t = 100 + 1 / 30
    move(sink, clock.t, .8)
    sink.glide_step(clock.t + 1 / 120)       # part way
    sink.apply(ActionCommand(clock.t, "mouse.left_click", "tap", "pinch.click"))
    i = next(i for i, e in enumerate(sink.backend.events) if e[0] != "move")
    assert sink.backend.events[i - 1][1:] == sink.cursor_pos


def test_zero_rate_jumps_per_frame():
    sink, clock = manual_sink(WindowsOutputConfig(movement_mode="absolute", smoothing=1.0, active_margin=0.0,
                                                  cursor_hz=0))
    move(sink, 100, .3)
    move(sink, 100.033, .7)
    assert moves(sink)[-1] == sink.cursor_pos


def test_real_thread_updates_at_least_60_hz():
    sink = WindowsOutputSink(FakeBackend((960, 540)), ScreenLayout.single(1920, 1080), ABS, threaded=True)
    try:
        start = time.perf_counter()
        frames = 0
        while time.perf_counter() - start < .5:
            frames += 1
            move(sink, time.perf_counter(), .1 + .8 * ((frames * 7) % 30) / 30)
            time.sleep(1 / 30)
        elapsed = time.perf_counter() - start
        rate = len(moves(sink)) / elapsed
        assert rate >= 60, (rate, frames)
    finally:
        sink.close()
