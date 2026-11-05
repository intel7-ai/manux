from __future__ import annotations

import threading

import numpy as np
import pytest

from manux.capture.camera import CameraConfig, LatestFrameCamera, brightness, parse_source, transform


class FakeCapture:
    """Hands out numbered frames; `step()` lets the test release one frame at a time."""

    def __init__(self, h=4, w=6):
        self.h, self.w = h, w
        self.n = 0
        self.gate = threading.Semaphore(0)
        self.released = False

    def step(self, frames=1):
        for _ in range(frames):
            self.gate.release()

    def read(self):
        if not self.gate.acquire(timeout=0.05):
            return False, None
        self.n += 1
        frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        frame[0, 0, 0] = self.n
        frame[0, -1, 1] = 200
        return True, frame

    def release(self):
        self.released = True


def camera(fake, **cfg):
    ticks = iter(range(1, 10_000))
    cam = LatestFrameCamera(CameraConfig(**cfg), opener=lambda s, c: fake, clock=lambda: float(next(ticks)))
    assert cam.open()
    return cam


def wait_for(fake, n):
    import time
    end = time.time() + 2
    while fake.n < n and time.time() < end:
        time.sleep(0.005)
    time.sleep(0.02)


def test_parse_source_rules():
    assert parse_source("1") == 1
    assert parse_source("192.168.0.2:8080") == "http://192.168.0.2:8080/video"
    assert parse_source("rtsp://x/y") == "rtsp://x/y"


def test_read_returns_frame_info_and_applies_mirror():
    fake = FakeCapture()
    cam = camera(fake, mirror=True)
    try:
        fake.step()
        wait_for(fake, 1)
        info, frame = cam.read()
        assert (info.seq, info.width, info.height, info.mirrored) == (0, 6, 4, True)
        assert frame[0, 0, 1] == 200          # the right edge moved to the left
        assert cam.read(timeout=0.05) is None  # nothing new yet
    finally:
        cam.release()
    assert fake.released


def test_only_the_newest_frame_is_kept():
    fake = FakeCapture()
    cam = camera(fake, mirror=False)
    try:
        fake.step(3)
        wait_for(fake, 3)
        info, frame = cam.read()
        assert frame[0, 0, 0] == 3 and cam.dropped_frames == 2
        assert info.t == 3.0                  # time of capture, not of read
    finally:
        cam.release()


def test_rotation_swaps_width_and_height():
    frame = np.zeros((4, 6, 3), dtype=np.uint8)
    assert transform(frame, False, 90).shape[:2] == (6, 4)
    assert transform(frame, False, 180).shape[:2] == (4, 6)


def test_failed_open_reports_false():
    cam = LatestFrameCamera(CameraConfig(), opener=lambda s, c: None)
    assert not cam.open() and cam.read() is None


def test_brightness():
    assert brightness(np.full((36, 64, 3), 100, dtype=np.uint8)) == pytest.approx(100, abs=0.5)
