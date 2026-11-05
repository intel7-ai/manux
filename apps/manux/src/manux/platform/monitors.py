"""Monitor layout (multi-monitor bounds). Ported from v0.3 `hand-mouse/src/screens.py`.

Windows places every monitor in one virtual-desktop coordinate space. The primary monitor starts at
(0, 0); a monitor to its left or above has negative coordinates, and monitors of different sizes leave
dead areas the cursor cannot enter. `ScreenLayout.clamp()` keeps a point on the monitor nearest to it.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Monitor:
    """One monitor in virtual-desktop pixels; `right` / `bottom` are exclusive."""

    left: int
    top: int
    right: int
    bottom: int
    primary: bool = False

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> Tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def distance(self, x: float, y: float) -> float:
        dx = max(self.left - x, 0.0, x - (self.right - 1))
        dy = max(self.top - y, 0.0, y - (self.bottom - 1))
        return math.hypot(dx, dy)

    def clamp(self, x: float, y: float) -> Tuple[float, float]:
        return (max(float(self.left), min(float(self.right - 1), float(x))),
                max(float(self.top), min(float(self.bottom - 1), float(y))))


class ScreenLayout:
    def __init__(self, monitors: Sequence[Monitor]) -> None:
        usable = [m for m in monitors if m.width > 0 and m.height > 0]
        if not usable:
            usable = [Monitor(0, 0, 1920, 1080, primary=True)]
        if not any(m.primary for m in usable):
            first = usable[0]
            usable[0] = Monitor(first.left, first.top, first.right, first.bottom, primary=True)
        # Primary first, then left-to-right / top-to-bottom, so "next monitor" is predictable
        self.monitors: List[Monitor] = sorted(usable, key=lambda m: (not m.primary, m.left, m.top))

    @classmethod
    def single(cls, width: int, height: int) -> "ScreenLayout":
        return cls([Monitor(0, 0, int(width), int(height), primary=True)])

    @classmethod
    def detect(cls, fallback_size: Optional[Tuple[int, int]] = None) -> "ScreenLayout":
        """Real monitors on Windows; one monitor of `fallback_size` (or 1920x1080) elsewhere."""
        monitors = windows_monitors() if sys.platform == "win32" else []
        if monitors:
            return cls(monitors)
        w, h = fallback_size or (1920, 1080)
        return cls.single(w, h)

    @property
    def primary(self) -> Monitor:
        return self.monitors[0]

    def monitor_at(self, x: float, y: float) -> Optional[Monitor]:
        for m in self.monitors:
            if m.contains(x, y):
                return m
        return None

    def nearest(self, x: float, y: float) -> Monitor:
        return self.monitor_at(x, y) or min(self.monitors, key=lambda m: m.distance(x, y))

    def clamp(self, x: float, y: float) -> Tuple[float, float]:
        """Keep (x, y) on the monitor nearest to it (no dead areas, no off-screen)."""
        return self.nearest(x, y).clamp(x, y)

    def next_after(self, x: float, y: float) -> Monitor:
        current = self.nearest(x, y)
        i = self.monitors.index(current)
        return self.monitors[(i + 1) % len(self.monitors)]

    def __len__(self) -> int:
        return len(self.monitors)


def windows_monitors() -> List[Monitor]:  # pragma: no cover - needs Windows
    """Every monitor from EnumDisplayMonitors; empty list when it fails or not on Windows."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        found: List[Monitor] = []
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def callback(hmon, _hdc, _rect, _data):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcMonitor
                found.append(Monitor(r.left, r.top, r.right, r.bottom, primary=bool(info.dwFlags & 1)))
            return 1

        user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
        return found
    except Exception:
        return []
