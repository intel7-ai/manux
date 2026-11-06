"""Tests for hand-tremor stabilization."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.filters import OneEuroFilter, TremorStabilizer
from src.gesture_engine import GestureEngine, GestureResult
from src.mouse_controller import MouseController

FRAME = 1 / 30
CFG = dict(min_cutoff=1.0, beta=8.0, deadzone=0.0)


def _std(v):
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / len(v))


def test_one_euro_removes_most_tremor_when_still():
    rng = random.Random(1)
    f = OneEuroFilter(min_cutoff=1.0, beta=8.0)
    raw = [0.5 + rng.gauss(0, 0.003) for _ in range(150)]  # ~0.3% frame tremor
    out = [f(x, i * FRAME) for i, x in enumerate(raw)]
    assert _std(out[30:]) < _std(raw[30:]) * 0.35


def test_one_euro_follows_fast_motion_with_little_lag():
    f = OneEuroFilter(min_cutoff=1.0, beta=8.0)
    out = 0.0
    for i in range(16):  # 1.0 frame-widths/s for ~0.5 s
        out = f(0.2 + 1.0 * i * FRAME, i * FRAME)
    target = 0.2 + 1.0 * 15 * FRAME
    assert target - out < 0.03  # lags < 3% of the frame at a fast move


def test_deadband_holds_cursor_perfectly_still_and_follows_without_jump():
    s = TremorStabilizer()
    rng = random.Random(2)
    outs = [s((0.5 + rng.uniform(-0.0015, 0.0015), 0.5), i * FRAME, 1.0, 8.0, 0.002) for i in range(60)]
    assert len({o for o in outs[5:]}) == 1  # tiny tremor never moves the output
    # A real move is followed smoothly (no step larger than the move itself)
    prev = outs[-1]
    for i in range(10):
        o = s((0.5 + 0.01 * (i + 1), 0.5), (60 + i) * FRAME, 1.0, 8.0, 0.002)
        assert o[0] >= prev[0] and o[0] - prev[0] < 0.02
        prev = o
    assert prev[0] > 0.55


def test_tremor_filter_steadies_the_real_cursor():
    rng = random.Random(3)

    def run(enabled):
        cfg = dict(GestureEngine().config, tremor_filter=enabled)
        ctrl = MouseController(dry_run=True, screen_size=(1920, 1080))
        xs = []
        for i in range(90):
            pt = (0.5 + rng.gauss(0, 0.002), 0.5 + rng.gauss(0, 0.002))
            ctrl.apply(GestureResult(active_gesture="no_pinch", action="move_cursor",
                                     cursor_point=pt, is_moving=True), cfg, now=i * FRAME)
            xs.append(ctrl.last_screen_pos[0])
        return max(xs[20:]) - min(xs[20:])

    assert run(True) <= run(False) * 0.3
