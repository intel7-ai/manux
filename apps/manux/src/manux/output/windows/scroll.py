"""Grab scroll with inertia, and the wheel pump. No OS calls: wheel amounts go to a `send(v, h)` callback.

Ported from v0.3 `MouseController._grab_scroll`, `_launch_velocity`, `_end_scroll`, `_tick_scroll_inertia`,
`_take_steps` and `ScrollPump`.

Not ported (S3): cursor centring on the scroll area / browsing series (UI Automation, `scroll_target.py`),
and the "seamless" coast that starts while the fingers are still opening (needs a release signal from the
pinch feature, S4). Inertia starts at the `end` phase.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from manux.output.windows.config import WindowsOutputConfig

Send = Callable[[int, int], None]


class ScrollPump:
    """Spreads wheel amounts over time.

    The camera delivers ~30 updates a second; sent as they come, each one is a burst of 1-4 notches and
    the page steps. The pump takes amounts into a pending pool and hands out a share on every tick
    (`1 - exp(-dt / tau)`), so a thread ticking every ~8 ms produces a steady stream of small events.
    Totals are preserved; the added delay is about `tau` (0.03 s)."""

    def __init__(self, tau: float = 0.03) -> None:
        self.tau = tau
        self._pending = [0.0, 0.0]
        self._carry = [0.0, 0.0]
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def add(self, v: float, h: float) -> None:
        with self._lock:
            self._pending[0] += v
            self._pending[1] += h

    def clear(self) -> None:
        """Drop what has not been sent yet (the user caught the page)."""
        with self._lock:
            self._pending = [0.0, 0.0]
            self._carry = [0.0, 0.0]

    def pending(self) -> Tuple[float, float]:
        with self._lock:
            return self._pending[0], self._pending[1]

    def step(self, now: float, tau: Optional[float] = None) -> Tuple[int, int]:
        tau = self.tau if tau is None else tau
        dt = 0.0 if self._last is None else max(0.0, min(0.1, now - self._last))
        self._last = now
        share = 1.0 if tau <= 0 else 1.0 - math.exp(-dt / tau)
        with self._lock:
            take = [self._pending[0] * share, self._pending[1] * share]
            self._pending[0] -= take[0]
            self._pending[1] -= take[1]
            self._carry[0] += take[0]
            self._carry[1] += take[1]
            v, h = int(self._carry[0]), int(self._carry[1])
            self._carry[0] -= v
            self._carry[1] -= h
        return v, h

    def flush(self) -> Tuple[int, int]:
        with self._lock:
            v = int(round(self._pending[0] + self._carry[0]))
            h = int(round(self._pending[1] + self._carry[1]))
            self._pending = [0.0, 0.0]
            self._carry = [0.0, 0.0]
        return v, h


def take_steps(acc: List[float], step: int = 10) -> Tuple[int, int]:
    """Pull whole `step`-sized wheel amounts out of the accumulator."""
    v, h = int(acc[0] / step) * step, int(acc[1] / step) * step
    acc[0] -= v
    acc[1] -= h
    return v, h


class GrabScroll:
    """Touchscreen-style scrolling: pull the hand down -> the page follows it down (wheel up).

    begin(point) anchors; hold(point) sends wheel amounts past a deadzone; end() may start inertia;
    tick(now) advances inertia. 120 wheel units = one notch."""

    def __init__(self, config: WindowsOutputConfig, send: Send) -> None:
        self.config = config
        self._send_raw = send
        self.total = [0, 0]                     # wheel units sent (vertical, horizontal)
        self.last_inertia: Optional[Dict[str, object]] = None
        self.inertia_launches = 0
        self._reset_grab()
        self._inertia_v: Optional[List[float]] = None
        self._inertia_t = 0.0
        self._inertia_acc = [0.0, 0.0]
        self._last_t = -math.inf

    def _reset_grab(self) -> None:
        self._prev: Optional[Tuple[float, float]] = None
        self._anchor: Optional[Tuple[float, float]] = None
        self._live = False
        self._acc = [0.0, 0.0]
        self._hist: Deque[Tuple[float, float, float]] = deque()
        self._recent: Deque[Tuple[float, float, float]] = deque()
        self._gain = 1.0

    @property
    def active(self) -> bool:
        return self._prev is not None

    @property
    def coasting(self) -> bool:
        return self._inertia_v is not None

    def _send(self, v: int, h: int) -> None:
        if not (v or h):
            return
        self.total[0] += v
        self.total[1] += h
        self._send_raw(v, h)

    def begin(self, point: Tuple[float, float], now: float, gain: float = 1.0) -> None:
        self.stop_inertia()       # pinching again catches a coasting page
        self._reset_grab()
        self._prev = self._anchor = (float(point[0]), float(point[1]))
        self._gain = gain
        self._last_t = now

    def hold(self, point: Tuple[float, float], now: float) -> None:
        if self._prev is None:
            return
        c = self.config
        x, y = float(point[0]), float(point[1])
        if now - self._last_t > c.reanchor_gap:
            # the hand is back after a short tracking loss: continue from here (no jump)
            self._prev = (x, y)
            self._hist.clear()
            self._last_t = now
            return
        self._last_t = now
        alpha = 0.5
        px, py = self._prev
        sx, sy = px + alpha * (x - px), py + alpha * (y - py)  # light jitter smoothing
        self._prev = (sx, sy)
        if not self._live:
            ax, ay = self._anchor  # type: ignore[misc]
            if math.hypot(sx - ax, (sy - ay) * c.frame_aspect) < c.scroll_deadzone:
                return
            self._live = True  # past the deadzone: scroll from here on
            return
        sign = -1.0 if c.scroll_invert else 1.0
        gain = c.scroll_gain * self._gain
        dy = (sy - py) * c.frame_aspect  # physical units (frame widths)
        dx = sx - px
        rv, rh = sign * dy * gain, sign * -dx * gain  # hand down -> wheel up; hand right -> scroll left
        self._acc[0] += rv
        self._acc[1] += rh
        self._hist.append((now, rv, rh))
        while len(self._hist) > 2 and now - self._hist[0][0] > 0.1:
            self._hist.popleft()
        self._recent.append((now, rv, rh))
        while len(self._recent) > 2 and now - self._recent[0][0] > 0.4:
            self._recent.popleft()
        self._send(*take_steps(self._acc))

    def end(self, now: float, launch: bool = True) -> None:
        """Pinch released: a page that was still moving keeps coasting (inertia)."""
        if self._prev is None:
            return
        v = self._launch_velocity(now) if launch else None
        if v is not None:
            self._inertia_v = [v[0], v[1]]
            self._inertia_t = now
            self._inertia_acc = [0.0, 0.0]
            self.inertia_launches += 1
        self._reset_grab()

    def _launch_velocity(self, now: float) -> Optional[Tuple[float, float]]:
        """Wheel units/s to coast with, from the last ~0.1 s of the grab (None = do not coast)."""
        c = self.config
        if not (self._live and c.scroll_inertia and len(self._hist) >= 2):
            return None
        span = max(1e-3, self._hist[-1][0] - self._hist[0][0])
        vv = sum(r[1] for r in list(self._hist)[1:]) / span
        vh = sum(r[2] for r in list(self._hist)[1:]) / span
        raw = (vv, vh)
        # Follow the page's RECENT direction (last ~0.4 s): a flick back after a long pull coasts back
        rv_sum = sum(r[1] for r in self._recent)
        rh_sum = sum(r[2] for r in self._recent)
        same_way = vv * rv_sum + vh * rh_sum > 0
        # A mostly vertical flick must not drift sideways (and the other way round)
        lock = c.scroll_inertia_axis_lock
        if abs(vh) < abs(vv) * lock:
            vh = 0.0
        elif abs(vv) < abs(vh) * lock:
            vv = 0.0
        cap = c.scroll_inertia_max_speed       # a release flick can be 5-7x faster than the pull
        speed = math.hypot(vv, vh)
        if speed > cap > 0:
            vv, vh = vv * cap / speed, vh * cap / speed
        launched = same_way and math.hypot(vv, vh) >= c.scroll_inertia_min_speed
        self.last_inertia = {"raw": [round(raw[0]), round(raw[1])], "used": [round(vv), round(vh)],
                             "recent": [round(rv_sum), round(rh_sum)], "same_way": bool(same_way),
                             "launched": bool(launched), "hist_n": len(self._hist)}
        return (vv, vh) if launched else None

    def stop_inertia(self) -> None:
        self._inertia_v = None

    def tick(self, now: float) -> None:
        """Exponential slow-down (frame-rate independent), like flicking a phone page."""
        if self._inertia_v is None:
            return
        tau = max(0.05, float(self.config.scroll_inertia_time))
        dt = max(0.0, min(0.1, now - self._inertia_t))
        self._inertia_t = now
        decay = math.exp(-dt / tau)
        vv, vh = self._inertia_v
        self._inertia_acc[0] += vv * tau * (1.0 - decay)
        self._inertia_acc[1] += vh * tau * (1.0 - decay)
        self._inertia_v = [vv * decay, vh * decay]
        self._send(*take_steps(self._inertia_acc))
        if math.hypot(*self._inertia_v) < 60.0:  # half a notch per second: done
            self._inertia_v = None

    def cancel(self) -> None:
        """Safety: end the grab and any coasting without sending anything more."""
        self._reset_grab()
        self._inertia_v = None
