"""Latest-only internet camera frames, with the same consumer contract as a webcam.

WebRTC timestamps are relative media time, not a sensor-to-PC latency measurement.
JPEG relay frames retain the existing wall-clock freshness checks.
"""
from __future__ import annotations

import math
import threading
import time

from manux.capture.camera import transform
from manux.capture.remote import MAX_AGE, decode_jpeg, jpeg_size
from manux.core.contracts import FrameInfo

MAX_VIDEO_DIMENSION = 1280
MAX_VIDEO_PIXELS = 1280 * 960


class InternetCamera:
    is_network_stream = True

    def __init__(self, *, clock=time.perf_counter, wall_clock=time.time, decoder=decode_jpeg):
        self._clock, self._wall, self._decoder = clock, wall_clock, decoder
        self._cond = threading.Condition()
        self._connected = False
        self._generation = 0
        self._seq = 0
        self._latest = None
        self._last_seq = -1
        self._last_receive = 0.
        self._last_read = None
        self._mode = None
        self._fps = 0.
        self._media_first = self._media_last = None
        self._media_started = None
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
            self._last_read, self._mode, self._fps = None, None, 0.
            self._media_first = self._media_last = self._media_started = None
            self.last_latency_ms = self.facing = None
            self._cond.notify_all()
            return self._generation

    def disconnect(self):
        with self._cond:
            self._generation += 1
            self._connected = False
            self._latest = None
            self._mode, self._fps, self.last_latency_ms, self.facing = None, 0., None, None
            self._cond.notify_all()

    release = disconnect

    def open(self):
        return self.is_open

    @property
    def is_open(self):
        with self._cond:
            return self._connected and self._clock() - self._last_receive <= 2.

    @property
    def fps(self):
        return self._fps if self.is_open else 0.

    def actual_mode(self):
        with self._cond:
            return (*self._mode, self.fps) if self._mode and self.is_open else None

    def submit(self, metadata, jpeg, *, generation=None):
        if jpeg_size(jpeg) != (metadata.width, metadata.height):
            raise ValueError("JPEG and metadata dimensions differ")
        now, age = self._clock(), self._wall() - metadata.captured_at
        with self._cond:
            if (not self._connected or generation is not None and generation != self._generation
                    or metadata.seq <= self._last_seq or not -.25 <= age <= MAX_AGE):
                self.dropped_frames += 1
                return False
            self._last_seq = metadata.seq
            self._put(("jpeg", jpeg, metadata.mirrored, metadata.rotation, metadata.facing,
                       (metadata.width, metadata.height), now, max(0., age), self._generation))
            return True

    def submit_video(self, image, *, media_time, generation, mirrored, rotation=0, facing="user"):
        """Accept decoded BGR pixels from the current authenticated RTC receiver only."""
        if (len(image.shape) != 3 or image.shape[2] != 3 or str(image.dtype) != "uint8"
                or not 0 < image.shape[0] <= MAX_VIDEO_DIMENSION
                or not 0 < image.shape[1] <= MAX_VIDEO_DIMENSION
                or image.shape[0] * image.shape[1] > MAX_VIDEO_PIXELS
                or not isinstance(media_time, (int, float)) or not math.isfinite(media_time)
                or type(mirrored) is not bool or type(rotation) is not int or rotation not in (0, 90, 180, 270)
                or facing not in ("user", "environment")):
            raise ValueError("invalid RTC video frame")
        now = self._clock()
        with self._cond:
            if not self._connected or generation != self._generation:
                return False
            if self._media_last is not None and media_time <= self._media_last:
                self.dropped_frames += 1
                return False
            if self._media_first is None:
                self._media_first, self._media_started = media_time, now
            lag = now - self._media_started - (media_time - self._media_first)
            # Re-anchor only when the stream is ahead (clock drift / skipped frames).
            # A delayed receiver must catch up; it cannot make a stale queue fresh.
            if lag < 0:
                self._media_first, self._media_started, lag = media_time, now, 0.
            self._media_last = media_time
            if lag > MAX_AGE:
                self.dropped_frames += 1
                return False
            self._put(("video", image, mirrored, rotation, facing,
                       (image.shape[1], image.shape[0]), now, lag, generation))
            return True

    def _put(self, packet):
        if self._latest is not None:
            self.dropped_frames += 1
        self._latest = packet
        self._last_receive = packet[6]
        self._cond.notify_all()

    def read(self, timeout=.2):
        with self._cond:
            self._cond.wait_for(lambda: self._latest is not None or not self._connected, max(0., timeout))
            packet, self._latest = self._latest, None
        if packet is None:
            return None
        kind, payload, mirror, rotation, facing, shape, received, age, generation = packet
        if age + self._clock() - received > MAX_AGE:
            self.dropped_frames += 1
            return None
        image = self._decoder(payload) if kind == "jpeg" else payload
        if image is None or image.shape[:2] != (shape[1], shape[0]):
            return None
        image = transform(image, mirror, rotation)
        with self._cond:
            if (not self._connected or generation != self._generation
                    or age + self._clock() - received > MAX_AGE):
                return None
            if self._last_read is not None and received > self._last_read:
                rate = 1 / (received - self._last_read)
                self._fps = rate if not self._fps else .9 * self._fps + .1 * rate
            self._last_read = received
            self._mode = (image.shape[1], image.shape[0])
            self.last_latency_ms = age * 1000 if kind == "jpeg" else None
            self.facing = facing
            info = FrameInfo(self._seq, received, *self._mode, mirror, rotation)
            self._seq += 1
            return info, image
