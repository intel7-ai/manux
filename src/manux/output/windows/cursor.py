"""Cursor mapping: normalized point (0..1, image coords) -> screen pixels. No OS calls.

Ported from v0.3 `MouseController.map_to_screen_absolute`, `_relative_step`, `_compute_target_screen_pos`,
`absolute_monitor`, `cycle_monitor`, `warp_to`.

- relative (default): trackpad-like deltas with pointer acceleration. Scale each axis by the
  extended desktop span relative to the primary monitor, preserving camera aspect correction.
- absolute: the camera's active box maps onto one monitor (the one under the cursor until
  `next_monitor()` picks another). `gain` != 1 adds (gain - 1) x each hand delta to an offset so the
  cursor moves faster without a jump when the gain changes.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from manux.output.windows.config import WindowsOutputConfig
from manux.platform.monitors import Monitor, ScreenLayout


class CursorMapper:
    def __init__(self, screens: ScreenLayout, config: WindowsOutputConfig,
                 start: Optional[Tuple[int, int]] = None) -> None:
        self.screens = screens
        self.config = config
        self.pos: Tuple[int, int] = start if start is not None else screens.primary.center
        self._anchor: Optional[Tuple[float, float]] = None      # smoothed hand point (relative)
        self._rel_pos: Tuple[float, float] = (float(self.pos[0]), float(self.pos[1]))
        self._rel_t = 0.0
        self._smoothed: Optional[Tuple[float, float]] = None    # absolute
        self._abs_monitor: Optional[Monitor] = None
        self._abs_offset: Tuple[float, float] = (0.0, 0.0)
        self._abs_prev: Optional[Tuple[float, float]] = None
        self._rebase = False

    @property
    def anchored(self) -> bool:
        return self._anchor is not None

    # ------------------------------------------------------------------ state changes
    def pause(self) -> None:
        """The hand stopped driving the cursor: the next point starts fresh (no jump)."""
        self._anchor = None
        self._abs_prev = None
        if self.config.movement_mode != "absolute":
            self._smoothed = None

    def warp(self, x: int, y: int, rebase: bool = False) -> None:
        """The cursor is at (x, y) now: relative movement continues from there.

        Absolute mode maps the hand straight back unless `rebase` shifts the mapping so the cursor stays here."""
        self.pos = (int(x), int(y))
        self._anchor = None
        self._abs_prev = None
        self._smoothed = None
        self._rebase = bool(rebase)

    def reset(self) -> None:
        self.pause()
        self._abs_monitor = None  # absolute re-picks the monitor under the cursor

    @property
    def absolute_monitor(self) -> Monitor:
        if self._abs_monitor is None or self._abs_monitor not in self.screens.monitors:
            self._abs_monitor = self.screens.nearest(*self.pos)
        return self._abs_monitor

    def next_monitor(self) -> Monitor:
        """Jump to the centre of the next monitor; absolute mode maps onto it from now on."""
        target = self.screens.next_after(*self.pos)
        self._abs_monitor = target
        self._abs_offset = (0.0, 0.0)
        self.warp(*target.center)
        return target

    # ------------------------------------------------------------------ mapping
    def map_absolute(self, nx: float, ny: float, clamp: bool = True) -> Tuple[float, float]:
        """Camera box [margin, 1 - margin] -> the absolute monitor. clamp=False maps the margin beyond the edges."""
        margin = max(0.0, min(0.45, float(self.config.active_margin)))
        span = max(1e-6, 1.0 - 2.0 * margin)
        x = (float(nx) - margin) / span
        y = (float(ny) - margin) / span
        if clamp:
            x = max(0.0, min(1.0, x))
            y = max(0.0, min(1.0, y))
        m = self.absolute_monitor
        return m.left + x * (m.width - 1), m.top + y * (m.height - 1)

    def step(self, point: Tuple[float, float], now: float, gain: float = 1.0) -> Tuple[int, int]:
        """New cursor position for this hand point; also stored in `pos`."""
        if self.config.movement_mode == "absolute":
            self.pos = self._absolute(point, gain)
        else:
            self.pos = self._relative(point, now, gain)
        return self.pos

    def _relative(self, point: Tuple[float, float], now: float, gain: float) -> Tuple[int, int]:
        c = self.config
        smoothing = max(0.05, min(1.0, float(c.smoothing)))
        g_min = max(0.0, float(c.relative_gain_min))
        g_max = max(g_min, float(c.relative_gain_max))
        v_lo = max(0.0, float(c.relative_accel_speed_low))
        v_hi = max(v_lo + 1e-6, float(c.relative_accel_speed_high))
        hx, hy = float(point[0]), float(point[1])
        if self._anchor is None:
            # (Re)start from wherever the cursor is: no jump after a click, a pause or hand loss
            self._anchor = (hx, hy)
            self._rel_pos = (float(self.pos[0]), float(self.pos[1]))
            self._rel_t = now
            self._rebase = False
            return self.pos
        ax, ay = self._anchor
        sx = ax + smoothing * (hx - ax)  # EMA on the hand point suppresses landmark jitter
        sy = ay + smoothing * (hy - ay)
        self._anchor = (sx, sy)
        dt = max(1e-3, min(0.1, now - self._rel_t))
        self._rel_t = now
        dx = sx - ax
        dy = (sy - ay) * c.frame_aspect  # physical units (frame widths)
        speed = math.hypot(dx, dy) / dt
        t = max(0.0, min(1.0, (speed - v_lo) / (v_hi - v_lo)))
        g = (g_min + (g_max - g_min) * t) * gain
        bounds = self.screens.bounds
        primary = self.screens.primary
        vertical_scale = primary.width * bounds.height / primary.height
        self._rel_pos = bounds.clamp(self._rel_pos[0] + dx * bounds.width * g,
                                     self._rel_pos[1] + dy * vertical_scale * g)
        px, py = self.screens.clamp(*self._rel_pos)
        return int(round(px)), int(round(py))

    def _absolute(self, point: Tuple[float, float], gain: float) -> Tuple[int, int]:
        smoothing = max(0.05, min(1.0, float(self.config.smoothing)))
        mx, my = self.map_absolute(point[0], point[1], clamp=False)
        if self._rebase:
            # after a warp: shift the mapping so the cursor stays put
            self._abs_offset = (float(self.pos[0]) - mx, float(self.pos[1]) - my)
            self._smoothed = (float(self.pos[0]), float(self.pos[1]))
            self._rebase = False
        elif gain != 1.0 and self._abs_prev is not None:
            k = gain - 1.0
            self._abs_offset = (self._abs_offset[0] + (mx - self._abs_prev[0]) * k,
                                self._abs_offset[1] + (my - self._abs_prev[1]) * k)
        self._abs_prev = (mx, my)
        mon = self.absolute_monitor
        raw_x, raw_y = mon.clamp(mx + self._abs_offset[0], my + self._abs_offset[1])
        if self._smoothed is None:
            sm_x, sm_y = raw_x, raw_y
        else:
            sm_x = self._smoothed[0] + smoothing * (raw_x - self._smoothed[0])
            sm_y = self._smoothed[1] + smoothing * (raw_y - self._smoothed[1])
        self._smoothed = (sm_x, sm_y)
        fx, fy = mon.clamp(sm_x, sm_y)
        return int(round(fx)), int(round(fy))
