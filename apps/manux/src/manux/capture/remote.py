"""Latest-JPEG source ported from Dan (한순중), phone_web_bridge.py/camera.py @9da1f634e8c0afd9d3d6b32a56c83cddfb23d5f8.

The consumer decodes only the newest packet. No network, engine or Windows input here.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from manux.capture.camera import transform
from manux.core.contracts import FrameInfo, RemoteFrameMetadata

MAX_PACKET = 1024 * 1024
MAX_AGE = 0.75


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Read bounded JPEG SOF dimensions before letting a decoder allocate pixels."""
    if not 4 <= len(data) <= MAX_PACKET or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise ValueError("expected JPEG")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 255:
            break
        while i < len(data) and data[i] == 255:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker in (0xDA, 0xD9):
            break
        size = int.from_bytes(data[i:i + 2], "big")
        if size < 2 or i + size > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            if size < 8:
                break
            return int.from_bytes(data[i + 5:i + 7], "big"), int.from_bytes(data[i + 3:i + 5], "big")
        i += size
    raise ValueError("missing JPEG dimensions")


def decode_jpeg(data: bytes):
    import cv2

    try:
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        return None


class RemoteCamera:
    """Camera.read-compatible source. None means frame_missing; stale frames are never replayed."""

    is_network_stream = True

    def __init__(self, *, clock=time.perf_counter, wall_clock=time.time, decoder=decode_jpeg,
                 stall_seconds=2.0):
        self._clock, self._wall, self._decoder = clock, wall_clock, decoder
        self._stall = stall_seconds
        self._cond = threading.Condition()
        self._connected = False
        self._generation = 0
        self._latest = None
        self._last_seq = -1
        self._seq = 0
        self._last_receive = 0.0
        self._last_read = None
        self._fps = 0.0
        self._mode = None
        self.dropped_frames = 0
        self.last_latency_ms = None
        self.facing = None

    def connect(self):
        with self._cond:
            self._generation += 1
            self._connected = True
            self._latest = None
            self._last_seq = -1
            self._last_receive = self._clock()
            self._last_read, self._fps, self._mode = None, 0.0, None
            self.last_latency_ms, self.facing = None, None
            self._cond.notify_all()

    def disconnect(self):
        with self._cond:
            self._generation += 1
            self._connected = False
            self._latest = None
            self._mode, self._fps, self.last_latency_ms = None, 0.0, None
            self._cond.notify_all()

    def open(self):
        return self.is_open  # the authenticated link owns connect(), not the consumer

    release = disconnect

    @property
    def is_open(self):
        with self._cond:
            return self._connected and self._clock() - self._last_receive <= self._stall

    @property
    def fps(self):
        return self._fps if self.is_open else 0.0

    def actual_mode(self):
        with self._cond:
            return (*self._mode, self.fps) if self._mode and self.is_open else None

    def submit(self, meta: RemoteFrameMetadata, jpeg: bytes) -> bool:
        if jpeg_size(jpeg) != (meta.width, meta.height):
            raise ValueError("JPEG and metadata dimensions differ")
        now, wall = self._clock(), self._wall()
        with self._cond:
            if not self._connected or meta.seq <= self._last_seq or not -0.25 <= wall - meta.captured_at <= MAX_AGE:
                self.dropped_frames += 1
                return False
            self._last_seq = meta.seq
            if self._latest is not None:
                self.dropped_frames += 1
            self._latest = (meta, jpeg, now, wall - meta.captured_at, self._generation)
            self._last_receive = now
            self._cond.notify_all()
            return True

    def read(self, timeout=0.2):
        with self._cond:
            self._cond.wait_for(lambda: self._latest is not None or not self._connected, timeout=max(0, timeout))
            packet, self._latest = self._latest, None
        if packet is None:
            return None
        meta, jpeg, received, age, generation = packet
        if age + self._clock() - received > MAX_AGE:
            self.dropped_frames += 1
            return None
        image = self._decoder(jpeg)
        if image is None or image.shape[:2] != (meta.height, meta.width):
            return None
        image = transform(image, meta.mirrored, meta.rotation)
        with self._cond:
            if not self._connected or self._generation != generation or age + self._clock() - received > MAX_AGE:
                return None
            if self._last_read is not None and received > self._last_read:
                fps = 1 / (received - self._last_read)
                self._fps = fps if not self._fps else 0.9 * self._fps + 0.1 * fps
            self._last_read = received
            self._mode = (image.shape[1], image.shape[0])
            self.last_latency_ms = max(0.0, age * 1000)
            self.facing = meta.facing
            info = FrameInfo(self._seq, received, *self._mode, meta.mirrored, meta.rotation)
            self._seq += 1
            return info, image
