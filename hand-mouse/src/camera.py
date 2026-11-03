"""
Camera Input Module for Hand-Mouse
Supports:
1. Local PC USB Webcams (index 0, 1, 2, ... e.g. SNAP U2 USB webcam)
2. App-less Smartphone Browser Camera (`source="phone"` or `--phone-web`) over HTTPS + WebSocket
3. Smartphone IP Camera apps over Wi-Fi (HTTP MJPEG / RTSP streams)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional, Union

import cv2
import numpy as np

from src.phone_web_bridge import PhoneWebCameraServer


@dataclass
class CameraConfig:
    source: Union[int, str] = 0
    width: int = 1280
    height: int = 720
    fps: int = 60
    mirror: bool = True
    auto_reconnect: bool = True
    phone_port: int = 8443


class CameraStream:
    """Unified camera stream handling USB webcams, app-less smartphone web bridge, and IP streams."""

    def __init__(self, config: Optional[CameraConfig] = None) -> None:
        self.config = config or CameraConfig()
        self.cap: Optional[cv2.VideoCapture] = None
        self.phone_server: Optional[PhoneWebCameraServer] = None
        self.is_phone_web: bool = False
        self.is_network_stream: bool = False
        self._fps_ema: float = 0.0
        self._last_frame_time: float = time.perf_counter()

        # Dedicated capture thread keeps ONLY the newest frame. Reading synchronously let
        # unprocessed frames queue up in the driver whenever processing slowed down (e.g.
        # another program in front), so the app processed ever-older frames -> growing lag.
        self._grab_thread: Optional[threading.Thread] = None
        self._grab_stop = threading.Event()
        self._cond = threading.Condition()
        self._latest: Optional[np.ndarray] = None
        self._latest_id = 0
        self._consumed_id = 0
        self._grab_ok = True
        self.dropped_frames = 0

    @staticmethod
    def parse_source(source_str: Union[int, str]) -> Union[int, str]:
        """Convert numeric string '0' -> int 0, 'phone' -> 'phone', or normalize IP shorthand."""
        if isinstance(source_str, int):
            return source_str
        s = str(source_str).strip()
        if s.isdigit():
            return int(s)
        if s.lower() in ("phone", "phone-web", "web", "browser"):
            return "phone"
        # If user enters IP:PORT like "192.168.0.15:8080", auto-prepend http:// and /video
        if not s.startswith(("http://", "https://", "rtsp://", "rtmp://")) and ":" in s:
            if "/" not in s:
                return f"http://{s}/video"
            return f"http://{s}"
        return s

    def open(self) -> bool:
        """Open the USB webcam, app-less smartphone browser server, or network stream."""
        self.release()
        source = self.parse_source(self.config.source)
        self.config.source = source

        # 1. App-less Smartphone Web Browser mode
        if source == "phone":
            self.is_phone_web = True
            self.is_network_stream = False
            self.phone_server = PhoneWebCameraServer(port=self.config.phone_port)
            ok = self.phone_server.start()
            self._last_frame_time = time.perf_counter()
            return ok

        self.is_phone_web = False
        self.is_network_stream = isinstance(source, str) and source.startswith(
            ("http://", "https://", "rtsp://", "rtmp://")
        )

        # 2. Local USB Webcam (index 0, 1, ...)
        if isinstance(source, int):
            # In OpenCV 5.0 on Windows, default CAP_ANY works reliably with USB UVC cameras (like SNAP U2)
            self.cap = cv2.VideoCapture(source)
            if not self.cap or not self.cap.isOpened():
                self.cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        else:
            # 3. Network MJPEG / RTSP stream
            self.cap = cv2.VideoCapture(source)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap or not self.cap.isOpened():
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._last_frame_time = time.perf_counter()
        self._start_grabber()
        return True

    # ------------------------------------------------------------------ capture thread
    def _start_grabber(self) -> None:
        self._stop_grabber()
        self._grab_stop.clear()
        with self._cond:
            self._latest = None
            self._latest_id = self._consumed_id = 0
            self._grab_ok = True
        self._grab_thread = threading.Thread(target=self._grab_loop, args=(self.cap,), name="camera-grab", daemon=True)
        self._grab_thread.start()

    def _grab_loop(self, cap: "cv2.VideoCapture") -> None:
        while not self._grab_stop.is_set():
            ok, frame = cap.read()
            with self._cond:
                if ok and frame is not None:
                    if self._latest_id != self._consumed_id:
                        self.dropped_frames += 1  # an older frame was never processed: skip it
                    self._latest = frame
                    self._latest_id += 1
                    self._grab_ok = True
                else:
                    self._grab_ok = False
                self._cond.notify_all()
            if not ok:
                time.sleep(0.01)

    def _stop_grabber(self) -> None:
        t = self._grab_thread
        if t is not None:
            self._grab_stop.set()
            t.join(timeout=1.0)
        self._grab_thread = None

    def _read_latest(self, timeout: float = 0.2) -> tuple[bool, Optional[np.ndarray]]:
        """Newest frame not processed yet (waits briefly for the next one)."""
        with self._cond:
            self._cond.wait_for(
                lambda: self._latest_id != self._consumed_id or not self._grab_ok, timeout=timeout
            )
            if self._latest_id == self._consumed_id or self._latest is None:
                return False, None
            self._consumed_id = self._latest_id
            return True, self._latest

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read a frame, optionally mirror it, and compute real-time FPS."""
        if self.is_phone_web and self.phone_server is not None:
            ok, frame = self.phone_server.read_frame()
            # Only mirror when an actual phone camera frame is active (do not mirror QR text screen!)
            if ok and frame is not None and self.phone_server.has_client and self.config.mirror:
                frame = cv2.flip(frame, 1)
            self._update_fps()
            return ok, frame

        if self.cap is None or not self.cap.isOpened():
            if self.config.auto_reconnect and self.is_network_stream:
                self.open()
            return False, None

        if self._grab_thread is not None:
            ok, frame = self._read_latest()
        else:
            ok, frame = self.cap.read()
        if not ok or frame is None:
            if self.config.auto_reconnect and self.is_network_stream:
                time.sleep(0.1)
                self.open()
            return False, None

        if self.config.mirror:
            frame = cv2.flip(frame, 1)

        self._update_fps()
        return True, frame

    def _update_fps(self) -> None:
        now = time.perf_counter()
        dt = now - self._last_frame_time
        self._last_frame_time = now
        if dt > 0:
            inst_fps = 1.0 / dt
            self._fps_ema = inst_fps if self._fps_ema == 0.0 else (0.9 * self._fps_ema + 0.1 * inst_fps)

    @property
    def fps(self) -> float:
        return self._fps_ema

    def switch_source(self, new_source: Union[int, str]) -> bool:
        """Dynamically switch between USB webcam indices or smartphone mode."""
        old_source = self.config.source
        self.config.source = self.parse_source(new_source)
        if not self.open():
            self.config.source = old_source
            self.open()
            return False
        return True

    def release(self) -> None:
        self._stop_grabber()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.phone_server is not None:
            self.phone_server.stop()
            self.phone_server = None
        self.is_phone_web = False
