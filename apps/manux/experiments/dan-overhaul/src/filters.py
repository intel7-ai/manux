"""
Hand-tremor stabilization for cursor movement.

- OneEuroFilter: adaptive low-pass (Casiez et al., CHI 2012). Strong smoothing when the
  hand is nearly still (removes tremor), little smoothing when it moves fast (low lag).
- Deadband: movements smaller than `radius` are ignored entirely, so the cursor stays
  perfectly still while aiming; beyond it the output follows without a jump.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * max(1e-6, cutoff))
    return 1.0 / (1.0 + tau / max(1e-6, dt))


class OneEuroFilter:
    """1D One Euro filter."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 8.0, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: Optional[float] = None
        self._dx = 0.0
        self._t: Optional[float] = None

    def reset(self) -> None:
        self._x = None
        self._dx = 0.0
        self._t = None

    def __call__(self, x: float, t: float) -> float:
        if self._x is None or self._t is None or t <= self._t:
            if self._x is None:
                self._x, self._t = x, t
            return self._x
        dt = min(0.2, t - self._t)
        self._t = t
        dx = (x - self._x) / dt
        a_d = _alpha(self.d_cutoff, dt)
        self._dx = self._dx + a_d * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = _alpha(cutoff, dt)
        self._x = self._x + a * (x - self._x)
        return self._x


class TremorStabilizer:
    """2D One Euro filter followed by a deadband (normalized hand coordinates)."""

    def __init__(self) -> None:
        self._fx = OneEuroFilter()
        self._fy = OneEuroFilter()
        self._out: Optional[Tuple[float, float]] = None

    def reset(self) -> None:
        self._fx.reset()
        self._fy.reset()
        self._out = None

    def __call__(
        self,
        pt: Tuple[float, float],
        t: float,
        min_cutoff: float,
        beta: float,
        deadzone: float,
    ) -> Tuple[float, float]:
        for f in (self._fx, self._fy):
            f.min_cutoff, f.beta = max(0.01, min_cutoff), max(0.0, beta)
        x = self._fx(float(pt[0]), t)
        y = self._fy(float(pt[1]), t)
        if self._out is None:
            self._out = (x, y)
            return self._out
        ox, oy = self._out
        d = math.hypot(x - ox, y - oy)
        if d > deadzone:
            k = (d - deadzone) / d  # follow, staying `deadzone` behind: no jump
            self._out = (ox + (x - ox) * k, oy + (y - oy) * k)
        return self._out
