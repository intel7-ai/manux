"""
Clean strokes for the fist cursor (2026-11-04).

The fist cursor runs at up to 35x, so the hand's wobble turns into a zigzag. The user moves it in clean straight
lines or smooth curves, so the filter keeps a HEADING and lets each step through only along it: the sideways
wobble is dropped, and the heading itself follows where the hand has been going over the last `path_window`
seconds, turning at most `path_max_turn` rad/s. The result is a straight line, or a curve whose bend changes
smoothly. So that it cannot drift off where the hand really is (a first wobbly step would otherwise set a wrong
line for good), each step also closes `path_pull` of the sideways gap to the real hand path. A sharp turn back (more than `path_reverse_deg`) takes the new direction at once, and a nearly still hand
passes through untouched (fine aiming is left to the tremor filter).

Pure logic: `step(now, dx, dy) -> (dx, dy)` in any units (the mouse controller feeds square frame units).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

DEFAULTS: Dict[str, float] = {
    "path_window": 0.12,         # s of movement that sets the direction
    "path_min_move": 0.002,      # the hand must have moved this much in that window (square frame units) to steer
    "path_follow": 0.25,         # how far the heading turns toward the new direction per step (0..1)
    "path_max_turn": 8.0,        # rad/s the heading may turn at most
    "path_reverse_deg": 110.0,   # a direction change bigger than this is a deliberate turn back: jump to it
    "path_idle": 0.25,           # s of a still hand after which the heading is forgotten
    "path_pull": 0.05,           # share of the sideways gap to the real hand path closed per step
}
# Tuned on 224 fist strokes from the 2026-11-04 evening logs: cursor direction change per frame mean 14.1 -> 9.4
# degrees, 90th percentile 32 -> 21, cursor travel kept at 91 % (the dropped part is the sideways wobble).


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


class PathSmoother:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, float] = dict(DEFAULTS)
        if config:
            self.config.update({k: float(v) for k, v in config.items() if k in DEFAULTS})
        self.reset()

    def cfg(self, key: str) -> float:
        return float(self.config[key])

    def reset(self) -> None:
        self._pos = (0.0, 0.0)
        self._out = (0.0, 0.0)
        self._hist: Deque[Tuple[float, float, float]] = deque()
        self.heading: Optional[float] = None
        self._last_t: Optional[float] = None
        self._moving_at = -1e9

    def step(self, now: float, dx: float, dy: float) -> Tuple[float, float]:
        dt = 1.0 / 30.0 if self._last_t is None else max(1e-3, min(0.2, now - self._last_t))
        self._last_t = now
        self._pos = (self._pos[0] + dx, self._pos[1] + dy)
        self._hist.append((now, self._pos[0], self._pos[1]))
        window = self.cfg("path_window")
        while len(self._hist) > 2 and now - self._hist[1][0] >= window:
            self._hist.popleft()
        t0, x0, y0 = self._hist[0]
        bx, by = self._pos[0] - x0, self._pos[1] - y0
        if math.hypot(bx, by) < self.cfg("path_min_move") or (self.heading is None and len(self._hist) < 3):
            if now - self._moving_at > self.cfg("path_idle"):
                self.heading = None
            self._out = self._pos
            return dx, dy                                   # nearly still (or just starting): leave it alone
        self._moving_at = now
        target = math.atan2(by, bx)
        if self.heading is None or abs(_wrap(target - self.heading)) > math.radians(self.cfg("path_reverse_deg")):
            self.heading = target
        else:
            turn = _wrap(target - self.heading) * max(0.0, min(1.0, self.cfg("path_follow")))
            limit = self.cfg("path_max_turn") * dt
            self.heading = _wrap(self.heading + max(-limit, min(limit, turn)))
        hx, hy = math.cos(self.heading), math.sin(self.heading)
        along = max(0.0, dx * hx + dy * hy)                 # a step against the stroke is wobble
        gx, gy = self._pos[0] - self._out[0], self._pos[1] - self._out[1]
        side = (-hy * gx + hx * gy) * self.cfg("path_pull")  # close part of the sideways gap to the real path
        sx, sy = along * hx - side * hy, along * hy + side * hx
        self._out = (self._out[0] + sx, self._out[1] + sy)
        return sx, sy
