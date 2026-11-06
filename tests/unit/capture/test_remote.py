"""Synthetic JPEG only; no webcam or personal recordings."""
import time

import numpy as np
import pytest

from manux.capture.camera import transform
from manux.capture.remote import RemoteCamera, jpeg_size
from manux.core.contracts import RemoteFrameMetadata

cv2 = pytest.importorskip("cv2")


def jpeg():
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    return cv2.imencode(".jpg", image)[1].tobytes()


def meta(seq=0, captured_at=None, **kwargs):
    return RemoteFrameMetadata(seq, captured_at if captured_at is not None else time.time(),
                               16, 12, kwargs.get("mirrored", False), kwargs.get("rotation", 0), "environment")


def test_only_newest_jpeg_is_decoded_and_transform_matches_metadata():
    decoded = []
    def decode(data):
        decoded.append(data)
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    source = RemoteCamera(decoder=decode)
    source.connect()
    data = jpeg()
    assert source.submit(meta(0), data)
    assert source.submit(meta(1, mirrored=True, rotation=90), data)
    frame, image = source.read(0)
    assert len(decoded) == 1 and source.dropped_frames == 1
    assert (frame.width, frame.height, frame.mirrored, frame.rotation) == (12, 16, True, 90)
    assert np.array_equal(image, transform(decode(data), True, 90))
    assert source.actual_mode()[:2] == (12, 16)
    assert source.read(0) is None


def test_stale_reordered_future_and_consumer_delayed_frames_are_dropped():
    now = [1000.0]
    source = RemoteCamera(clock=lambda: now[0], wall_clock=lambda: now[0])
    source.connect()
    assert not source.submit(meta(0, 998), jpeg())
    assert not source.submit(meta(0, 1001), jpeg())
    assert source.submit(meta(2, 1000), jpeg())
    assert not source.submit(meta(1, 1000), jpeg())
    assert not source.submit(meta(2, 1000), jpeg())
    now[0] += 0.8
    assert source.read(0) is None
    assert source.is_open
    now[0] += 1.3
    assert not source.is_open and source.actual_mode() is None and source.fps == 0


def test_disconnect_wakes_waiter_and_invalidates_inflight_decode():
    import threading
    source = RemoteCamera()
    source.connect()
    result = []
    waiter = threading.Thread(target=lambda: result.append(source.read(5)))
    waiter.start()
    source.disconnect()
    waiter.join(1)
    assert not waiter.is_alive() and result == [None]
    assert not source.is_open
    assert not source.submit(meta(), jpeg())

    def disconnect_during_decode(data):
        source.disconnect()
        source.connect()
        return np.zeros((12, 16, 3), np.uint8)
    source = RemoteCamera(decoder=disconnect_during_decode)
    source.connect()
    source.submit(meta(), jpeg())
    assert source.read(0) is None
    source.release()
    assert not source.open()


@pytest.mark.parametrize("bad", [b"", b"not-jpeg", b"\xff\xd8\xff\xc0\x00\x00\xff\xd9"])
def test_invalid_jpeg_rejected(bad):
    with pytest.raises(ValueError):
        jpeg_size(bad)


def test_wrong_dimensions_and_broken_compressed_data():
    source = RemoteCamera()
    source.connect()
    wrong = RemoteFrameMetadata(0, time.time(), 1280, 960, False, 0, "user")
    with pytest.raises(ValueError):
        source.submit(wrong, jpeg())
    source._decoder = lambda _: None
    source.submit(meta(), jpeg())
    assert source.read(0) is None


def test_receive_time_not_phone_wall_clock_drives_frameinfo_and_fps():
    now = [10.0]
    source = RemoteCamera(clock=lambda: now[0], wall_clock=lambda: 1000.0)
    source.connect()
    source.submit(meta(0, 999.95), jpeg())
    info, _ = source.read(0)
    assert info.t == 10 and source.last_latency_ms == pytest.approx(50)
    now[0] += .04
    source.submit(meta(1, 999.95), jpeg())
    info2, _ = source.read(0)
    assert info2.seq > info.seq and info2.t > info.t
    assert source.fps == pytest.approx(25)
