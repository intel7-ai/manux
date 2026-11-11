"""
Once-per-second performance log (logs/perf_latest.csv, overwritten every run).

Records FPS, hand-detection time, whole-loop time, camera frames skipped and which
program is in the foreground, so lag that only appears while another program is on
screen can be traced to its cause (CPU contention vs camera vs window refresh).
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path
from typing import List, Optional

HEADER = [
    "time", "fps", "detect_ms_avg", "detect_ms_max", "loop_ms_avg", "loop_ms_max",
    "camera_frames_skipped", "hand_mouse_in_front", "foreground_window",
    # how well the hand was held this second, while the app was on its main screen (empty otherwise):
    # frames looked at, frames with the control hand, of those kept only by HandFollower,
    # frames with a hand found but refused by the left/right filter, median hand size (wrist to knuckle / frame width)
    "main_frames", "hand_frames", "followed_frames", "refused_frames", "hand_size",
]


def foreground_window_title() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return ""


class PerfLog:
    def __init__(self, path: Path, interval: float = 1.0) -> None:
        self.path = Path(path)
        self.interval = interval
        self._det: List[float] = []
        self._loop: List[float] = []
        self._last_write = time.perf_counter()
        self._last_dropped = 0
        self._main_frames = self._hand_frames = self._followed = self._refused = 0
        self._sizes: List[float] = []
        self._file = None
        self._writer = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self.path, "w", newline="", encoding="utf-8-sig")
            self._writer = csv.writer(self._file)
            self._writer.writerow(HEADER)
        except OSError:
            self._file = None

    def frame(self, detect_ms: Optional[float], loop_ms: float) -> None:
        if detect_ms is not None:
            self._det.append(detect_ms)
        self._loop.append(loop_ms)

    def hand(self, *, found: bool, raw: bool, followed: bool = False, size: Optional[float] = None) -> None:
        """One frame on the main screen: the control hand was `found`; `raw` = the detector returned any hand at all."""
        self._main_frames += 1
        self._hand_frames += bool(found)
        self._followed += bool(followed)
        self._refused += bool(raw and not found)
        if found and size is not None:
            self._sizes.append(size)

    def tick(self, fps: float, dropped_total: int) -> None:
        now = time.perf_counter()
        if self._writer is None or now - self._last_write < self.interval:
            return
        self._last_write = now
        title = foreground_window_title()
        det, loop = self._det or [0.0], self._loop or [0.0]
        self._writer.writerow([
            time.strftime("%H:%M:%S"),
            f"{fps:.1f}",
            f"{sum(det) / len(det):.1f}", f"{max(det):.1f}",
            f"{sum(loop) / len(loop):.1f}", f"{max(loop):.1f}",
            dropped_total - self._last_dropped,
            "yes" if title.startswith("Hand-Mouse") else "no",
            title,
            *((self._main_frames, self._hand_frames, self._followed, self._refused,
               f"{sorted(self._sizes)[len(self._sizes) // 2]:.4f}" if self._sizes else "")
              if self._main_frames else ("", "", "", "", "")),
        ])
        self._file.flush()
        self._last_dropped = dropped_total
        self._det.clear()
        self._loop.clear()
        self._main_frames = self._hand_frames = self._followed = self._refused = 0
        self._sizes.clear()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
