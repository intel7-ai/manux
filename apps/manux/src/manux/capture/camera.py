"""Camera capture that keeps only the newest frame (port of hand-mouse/src/camera.py).

A grab thread reads the driver continuously and keeps the latest frame; `read()` returns the
newest frame not handed out yet, with its `FrameInfo`. Older unprocessed frames are dropped
(counted in `dropped_frames`), so processing never falls behind the camera.

Not ported from v0.3: the app-less phone browser bridge (`phone_web_bridge.py`). Network
streams (http/rtsp URLs) still work through OpenCV.

OpenCV is imported only when a real camera is opened, so tests run with a fake capture and
without OpenCV installed.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple, Union

import numpy as np

from manux.core.contracts import FrameInfo

Source = Union[int, str]


@dataclass
class CameraConfig:
    source: Source = 0
    width: int = 1280
    height: int = 720
    fps: int = 60
    mirror: bool = True
    rotation: int = 0          # 0 / 90 / 180 / 270, clockwise, applied after mirroring
    auto_reconnect: bool = True


def parse_source(source: Source) -> Source:
    """'0' -> 0; 'IP:PORT' -> 'http://IP:PORT/video'; URLs unchanged (same rules as v0.3)."""
    if isinstance(source, int):
        return source
    s = str(source).strip()
    if s.isdigit():
        return int(s)
    if not s.startswith(("http://", "https://", "rtsp://", "rtmp://")) and ":" in s:
        return f"http://{s}/video" if "/" not in s else f"http://{s}"
    return s


def transform(frame: np.ndarray, mirror: bool, rotation: int) -> np.ndarray:
    """Mirror left-right, then rotate clockwise by `rotation` degrees."""
    if mirror:
        frame = frame[:, ::-1]
    if rotation:
        frame = np.rot90(frame, k=-(rotation // 90))
    return np.ascontiguousarray(frame)


def _open_cv2(source: Source, cfg: CameraConfig) -> Any:
    import cv2  # only here: tests and the architecture check run without OpenCV

    if isinstance(source, int):
        cap = cv2.VideoCapture(source)
        if not cap or not cap.isOpened():
            cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(source)
    if not cap or not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
    cap.set(cv2.CAP_PROP_FPS, cfg.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class LatestFrameCamera:
    """`opener(source, config) -> capture or None`; a capture needs read(), release(), and optionally get()."""

    def __init__(self, config: Optional[CameraConfig] = None,
                 opener: Callable[[Source, CameraConfig], Any] = _open_cv2,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.config = config or CameraConfig()
        self._opener = opener
        self._clock = clock
        self._cap: Any = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._latest: Optional[np.ndarray] = None
        self._latest_t = 0.0
        self._latest_id = 0
        self._consumed_id = 0
        self._grab_ok = True
        self._seq = 0
        self._fps_ema = 0.0
        self._last_read_t: Optional[float] = None
        self.dropped_frames = 0

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> bool:
        self.release()
        self.config.source = parse_source(self.config.source)
        self._cap = self._opener(self.config.source, self.config)
        if self._cap is None:
            return False
        self._stop.clear()
        with self._cond:
            self._latest, self._latest_id, self._consumed_id, self._grab_ok = None, 0, 0, True
        self._thread = threading.Thread(target=self._grab_loop, args=(self._cap,), name="camera-grab", daemon=True)
        self._thread.start()
        return True

    def release(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._cap is not None:
            try:
                self._cap.release()
            finally:
                self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def is_network_stream(self) -> bool:
        s = self.config.source
        return isinstance(s, str) and s.startswith(("http://", "https://", "rtsp://", "rtmp://"))

    # ------------------------------------------------------------------ grab thread
    def _grab_loop(self, cap: Any) -> None:
        while not self._stop.is_set():
            ok, frame = cap.read()
            with self._cond:
                if ok and frame is not None:
                    if self._latest_id != self._consumed_id:
                        self.dropped_frames += 1
                    self._latest, self._latest_t = frame, self._clock()
                    self._latest_id += 1
                    self._grab_ok = True
                else:
                    self._grab_ok = False
                self._cond.notify_all()
            if not ok:
                time.sleep(0.01)

    # ------------------------------------------------------------------ reading
    def read(self, timeout: float = 0.2) -> Optional[Tuple[FrameInfo, np.ndarray]]:
        """The newest unprocessed frame with its FrameInfo, or None (no new frame in time / not open)."""
        if self._cap is None:
            if self.config.auto_reconnect and self.is_network_stream:
                self.open()
            return None
        with self._cond:
            self._cond.wait_for(lambda: self._latest_id != self._consumed_id or not self._grab_ok, timeout=timeout)
            if self._latest_id == self._consumed_id or self._latest is None:
                frame = None
            else:
                self._consumed_id = self._latest_id
                frame, t = self._latest, self._latest_t
        if frame is None:
            if not self._grab_ok and self.config.auto_reconnect and self.is_network_stream:
                self.open()
            return None
        frame = transform(frame, self.config.mirror, self.config.rotation)
        self._update_fps(t)
        info = FrameInfo(seq=self._seq, t=t, width=int(frame.shape[1]), height=int(frame.shape[0]),
                         mirrored=self.config.mirror, rotation=self.config.rotation)
        self._seq += 1
        return info, frame

    def _update_fps(self, t: float) -> None:
        if self._last_read_t is not None and t > self._last_read_t:
            inst = 1.0 / (t - self._last_read_t)
            self._fps_ema = inst if self._fps_ema == 0.0 else 0.9 * self._fps_ema + 0.1 * inst
        self._last_read_t = t

    @property
    def fps(self) -> float:
        return self._fps_ema

    def actual_mode(self) -> Optional[Tuple[int, int, float]]:
        """(width, height, fps) the driver really opened, or None (unknown / not a local camera)."""
        cap = self._cap
        if cap is None or not isinstance(self.config.source, int) or not hasattr(cap, "get"):
            return None
        try:
            import cv2
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(cap.get(cv2.CAP_PROP_FPS))
        except Exception:
            return None
        return (w, h, round(fps, 1)) if w > 0 and h > 0 else None


def brightness(frame: np.ndarray) -> float:
    """Mean grey level 0..255 of a BGR frame (for the dark-room warning), sampled on a coarse grid."""
    small = frame[:: max(1, frame.shape[0] // 36), :: max(1, frame.shape[1] // 64)]
    if small.ndim == 3:
        b, g, r = small[..., 0], small[..., 1], small[..., 2]
        grey = 0.114 * b + 0.587 * g + 0.299 * r
    else:
        grey = small
    return float(np.mean(grey))
