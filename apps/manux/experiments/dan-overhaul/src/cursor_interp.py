"""
Cursor interpolation: smooth ~144 Hz cursor motion from a 30 fps camera.

The camera (and so hand tracking) only updates ~30 times per second, so the cursor used
to jump 30 times per second. The main loop now only sets a *target*; a background thread
slides the real cursor from where it currently is to that target over one camera-frame
interval. Cost: up to one frame (~33 ms) of extra latency; gain: no visible stepping.
Before any click / button press the controller calls `snap()` so clicks land exactly on
the target.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional, Tuple

Point = Tuple[float, float]


def os_set_cursor_pos(x: int, y: int) -> None:
    """Fast absolute cursor placement (SetCursorPos on Windows)."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    else:  # pragma: no cover
        import pyautogui

        pyautogui.moveTo(int(x), int(y), _pause=False)


class CursorInterpolator:
    def __init__(self, set_pos: Callable[[int, int], None] = os_set_cursor_pos, rate_hz: float = 144.0) -> None:
        self._set_pos = set_pos
        self.rate_hz = rate_hz
        self.glide_fraction = 0.5          # share of the frame interval the glide takes (1.0 = until the next frame, ~17 ms more lag)
        self._lock = threading.Lock()
        self._from: Optional[Point] = None
        self._to: Optional[Point] = None
        self._t0 = 0.0
        self._dur = 1 / 30
        self._interval = 1 / 30           # EMA of the time between target updates
        self._last_target_t: Optional[float] = None
        self._sent: Optional[Tuple[int, int]] = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ pure logic
    def position_at(self, now: float) -> Optional[Point]:
        if self._to is None:
            return None
        if self._from is None or self._dur <= 0:
            return self._to
        a = max(0.0, min(1.0, (now - self._t0) / self._dur))
        return (self._from[0] + (self._to[0] - self._from[0]) * a,
                self._from[1] + (self._to[1] - self._from[1]) * a)

    def done_at(self, now: float) -> bool:
        return self._to is None or self._dur <= 0 or (now - self._t0) >= self._dur

    def set_target(self, x: float, y: float, now: float) -> None:
        """New target from the main loop: glide there over ~one camera-frame interval."""
        with self._lock:
            if self._last_target_t is not None:
                dt = max(0.012, min(0.1, now - self._last_target_t))
                self._interval = 0.7 * self._interval + 0.3 * dt
            self._last_target_t = now
            cur = self.position_at(now)
            self._from = cur if cur is not None else (float(x), float(y))
            self._to = (float(x), float(y))
            self._t0 = now
            self._dur = self._interval * self.glide_fraction     # reach the target early: less lag, still smooth at 144 Hz
        self._ensure_thread()
        self._wake.set()

    def snap(self, x: Optional[float] = None, y: Optional[float] = None) -> None:
        """Jump to (x, y) — or finish the current glide — right now (before a click)."""
        with self._lock:
            target = (float(x), float(y)) if x is not None and y is not None else self._to
            if target is None:
                return
            self._from = self._to = target
            self._dur = 0.0
            p = (int(round(target[0])), int(round(target[1])))
            self._sent = p
        self._set_pos(*p)

    # ------------------------------------------------------------------ thread
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="cursor-interp", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        period = 1.0 / max(30.0, self.rate_hz)
        while not self._stop.is_set():
            now = time.perf_counter()
            with self._lock:
                pos = self.position_at(now)
                done = self.done_at(now)
                p = None if pos is None else (int(round(pos[0])), int(round(pos[1])))
                send = p is not None and p != self._sent
                if send:
                    self._sent = p
            if send:
                self._set_pos(*p)
            if done:
                self._wake.wait(0.05)
                self._wake.clear()
            else:
                time.sleep(period)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
