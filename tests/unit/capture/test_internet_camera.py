import time

import numpy as np
import pytest

from manux.capture.internet import InternetCamera
from manux.core.contracts import RemoteFrameMetadata


def test_rtc_latest_only_media_timing_generation_and_no_replay():
    clock = [10.]
    camera = InternetCamera(clock=lambda: clock[0])
    generation = camera.connect()
    frame = np.zeros((12, 16, 3), np.uint8)
    assert camera.submit_video(frame, media_time=0., generation=generation, mirrored=False)
    clock[0] += .04
    next_frame = frame + 100
    assert camera.submit_video(next_frame, media_time=.04, generation=generation, mirrored=False)
    info, image = camera.read(0)
    assert info.t == clock[0] and (image == 100).all()
    assert camera.dropped_frames == 1 and camera.last_latency_ms is None
    assert camera.read(0) is None
    assert not camera.submit_video(frame, media_time=.04, generation=generation, mirrored=False)
    clock[0] += 1.
    assert not camera.submit_video(frame, media_time=.08, generation=generation, mirrored=False)
    assert camera.read(0) is None
    assert camera.submit_video(frame, media_time=1.04, generation=generation, mirrored=False)
    camera.disconnect()
    assert not camera.is_open and camera.read(0) is None
    camera.connect()
    assert not camera.submit_video(frame, media_time=2., generation=generation, mirrored=False)


def test_relay_staleness_and_disconnect_during_decode():
    cv2 = pytest.importorskip("cv2")
    image = np.zeros((12, 16, 3), np.uint8)
    jpeg = cv2.imencode(".jpg", image)[1].tobytes()
    clock = [10.]
    camera = InternetCamera(clock=lambda: clock[0], wall_clock=lambda: 100.)
    generation = camera.connect()
    stale = RemoteFrameMetadata(0, 98., 16, 12, False, 0, "user")
    assert not camera.submit(stale, jpeg, generation=generation)
    fresh = RemoteFrameMetadata(1, 100., 16, 12, True, 0, "user")
    assert camera.submit(fresh, jpeg, generation=generation)
    clock[0] += 1.
    assert camera.read(0) is None  # waited too long in the latest slot
    generation = camera.connect()
    assert camera.submit(fresh, jpeg, generation=generation)
    def decoding(_):
        camera.disconnect()
        camera.connect()
        return image
    camera._decoder = decoding
    assert camera.read(0) is None  # old decode cannot enter the replacement session


def test_video_geometry_finiteness_and_stall():
    clock = [10.]
    camera = InternetCamera(clock=lambda: clock[0])
    generation = camera.connect()
    image = np.zeros((12, 16, 3), np.uint8)
    with pytest.raises(ValueError):
        camera.submit_video(image, media_time=float("nan"), generation=generation, mirrored=True)
    with pytest.raises(ValueError):
        camera.submit_video(image, media_time=0., generation=generation, mirrored=1)
    assert camera.submit_video(image, media_time=0., generation=generation, mirrored=True)
    assert camera.read(0)[0].mirrored
    clock[0] += 2.1
    assert not camera.is_open and camera.actual_mode() is None and camera.fps == 0.


@pytest.mark.parametrize("width,height", [(1280, 960), (960, 1280)])
def test_hd_rtc_frames_keep_orientation_and_generation(width, height):
    camera = InternetCamera()
    generation = camera.connect()
    image = np.zeros((height, width, 3), np.uint8)
    image[:, :8, 0] = 255
    assert camera.submit_video(image, media_time=0., generation=generation, mirrored=False)
    info, result = camera.read(0)
    assert (info.width, info.height) == (width, height)
    assert result.shape == (height, width, 3) and (result[:, :8, 0] == 255).all()
    camera.disconnect()
    camera.connect()
    assert not camera.submit_video(image, media_time=1., generation=generation, mirrored=False)


@pytest.mark.parametrize("width,height", [(1281, 960), (960, 1281), (1280, 961)])
def test_rtc_geometry_above_hd_budget_is_rejected(width, height):
    camera = InternetCamera()
    generation = camera.connect()
    with pytest.raises(ValueError, match="invalid RTC"):
        camera.submit_video(np.zeros((height, width, 3), np.uint8), media_time=0.,
                            generation=generation, mirrored=False)
