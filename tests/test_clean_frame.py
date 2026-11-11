"""The preview is drawn on the camera frame. What keeps pixels (video tracker, hand pictures) must get them undrawn."""
import dataclasses
from types import SimpleNamespace

import numpy as np

from src.crop_recorder import CropRecorder
from src.hand_tracker import HandTracker
from src.roi_tracker import RoiTracker
from tests.test_qt_runtime import runtime, steps


def test_the_tracker_and_the_recorder_get_the_camera_picture_without_the_preview_marks(tmp_path):
    rt = runtime(tmp_path)
    rt.ui = dataclasses.replace(rt.ui, hand_crop=True)
    rt.roi_tracker = RoiTracker("mil", 3.0)
    rt._crop_rec = CropRecorder()
    pictures = []
    rt._record_frame = lambda t0, detections, result, frame=None: pictures.append(frame)
    shown = []
    original = rt.camera.read

    def read():
        ok, frame = original()
        shown.append(frame)
        return ok, frame

    rt.camera.read = read
    steps(rt, 3)
    assert (shown[-1] != 90).any()                     # the preview was drawn on the frame itself
    assert pictures and all((p == 90).all() for p in pictures)
    assert (rt.roi_tracker._frame == 90).all()


def marks_share(size_px):
    """Share of the hand's box that the marks of a pinching hand cover."""
    frame = np.zeros((720, 1280, 3), np.uint8)
    x0, y0 = 600, 300
    # 21 joints spread over a square of size_px, like a hand seen from afar
    grid = [(x0 + (i % 5) * size_px / 4, y0 + (i // 5) * size_px / 4) for i in range(21)]
    grid = [(x, min(y, y0 + size_px)) for x, y in grid]
    landmarks = [SimpleNamespace(x=x / 1280, y=y / 720) for x, y in grid]
    HandTracker.draw_hand(frame, landmarks, [("index", "action")])
    return float((frame[y0:y0 + size_px, x0:x0 + size_px].sum(axis=2) > 0).mean())


def test_marks_leave_a_far_hand_visible_and_keep_their_size_on_a_near_hand():
    far = marks_share(40)
    assert far < .75
    frame = np.zeros((720, 1280, 3), np.uint8)
    points = [SimpleNamespace(x=(300 + (i % 5) * 60) / 1280, y=(100 + (i // 5) * 60) / 720) for i in range(21)]
    HandTracker.draw_hand(frame, points)
    # below the last joint, at (300, 340), no line passes: radius 4 as before on a big hand
    assert frame[343, 300].sum() > 0 and frame[347, 300].sum() == 0
