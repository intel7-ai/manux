"""A small hand is looked at in several shifted crops and the joints are averaged (no camera, fake landmarkers)."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.hand_tracker import HandDetection, HandLandmark, HandTracker, merge_looks


def detection(offset=(0.0, 0.0), z=0.0, score=0.9, world=None):
    """A hand of 21 points: wrist at (0.5, 0.8), the four knuckles 0.05 away; the whole shape moved by `offset`."""
    pts = [HandLandmark(x=0.5 + offset[0], y=0.8 + offset[1], z=z) for _ in range(21)]
    for k, x in zip((5, 9, 13, 17), (0.45, 0.48, 0.52, 0.55)):
        pts[k] = HandLandmark(x=x + offset[0], y=0.75 + offset[1], z=z)
    return HandDetection(landmarks=pts, handedness="Right", score=score, from_crop=True, world=world)


def test_the_looks_are_averaged_and_one_far_off_look_is_dropped():
    got = merge_looks([[detection((0.001, 0.0))], [detection((-0.001, 0.0))], [detection((0.0, 0.001))]])[0]
    assert got.landmarks[0].x == pytest.approx(0.5, abs=1e-6) and got.landmarks[0].y == pytest.approx(0.8 + 0.001 / 3)
    wild = merge_looks([[detection()], [detection((0.0005, 0.0))], [detection((0.2, 0.2))]])[0]   # the third one is somewhere else
    assert wild.landmarks[0].x == pytest.approx(0.50025, abs=1e-6) and wild.landmarks[0].y == pytest.approx(0.8)


def test_looks_that_found_nothing_are_skipped_and_one_look_stays_as_it_is():
    only = detection((0.01, 0.0))
    assert merge_looks([[], [only], []])[0] is only
    assert merge_looks([[], []]) == []
    assert merge_looks([]) == []
    world = [(0.0, 0.0, 0.0)] * 21
    got = merge_looks([[detection(world=world)], [detection(world=[(0.02, 0.0, 0.0)] * 21)]])[0]
    assert got.world[0][0] == pytest.approx(0.01) and got.handedness == "Right" and got.score == pytest.approx(0.9)


def test_averaging_looks_with_independent_noise_lowers_the_noise():
    rng = np.random.default_rng(1)
    single, merged = [], []
    for _ in range(300):
        looks = [[detection((rng.normal(0, 0.004), rng.normal(0, 0.004)))] for _ in range(3)]
        single.append(looks[0][0].landmarks[0].x - 0.5)
        merged.append(merge_looks(looks)[0].landmarks[0].x - 0.5)
    assert np.std(merged) < 0.7 * np.std(single)                       # about 1/sqrt(3) = 0.58 in theory


class FakeLandmarker:
    def __init__(self, shift=0.0):
        self.shift = shift
        self.seen = []

    def detect_for_video(self, image, ts):
        self.seen.append(image.numpy_view().shape[:2])
        lm = [SimpleNamespace(x=0.5 + self.shift, y=0.5, z=0.0) for _ in range(21)]
        return SimpleNamespace(hand_landmarks=[lm], handedness=[], hand_world_landmarks=[])


def make_tracker(extra, max_side=200):
    tracker = HandTracker.__new__(HandTracker)
    tracker._crop_landmarker, tracker._crop_timestamp_ms = FakeLandmarker(0.0), 0
    tracker._tta_landmarkers, tracker._tta_timestamps = [FakeLandmarker(0.01), FakeLandmarker(-0.01)], [0, 0]
    tracker.crop_max_side, tracker.crop_upscale_side, tracker.crop_max_zoom = 640, 0, 4.0
    tracker.crop_tta, tracker.crop_tta_max_side = extra, max_side
    tracker.last_detect_ms = 0.0
    return tracker


def test_a_small_crop_gets_extra_looks_and_a_big_crop_does_not():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    tracker = make_tracker(2)
    found = tracker._detect_crop(frame, (600, 300, 700, 400))           # 100 px: a far hand
    assert [len(t.seen) for t in [tracker._crop_landmarker, *tracker._tta_landmarkers]] == [1, 1, 1]
    assert len(found) == 1 and found[0].landmarks[0].x == pytest.approx((600 + 0.5 * 100) / 1280, abs=1e-4)   # the shifts cancel
    big = make_tracker(2)
    big._detect_crop(frame, (400, 200, 700, 500))                       # 300 px: a close hand
    assert [len(t.seen) for t in [big._crop_landmarker, *big._tta_landmarkers]] == [1, 0, 0]
    off = make_tracker(0)
    off._detect_crop(frame, (600, 300, 700, 400))
    assert [len(t.seen) for t in [off._crop_landmarker, *off._tta_landmarkers]] == [1, 0, 0]


def test_the_extra_crops_stay_inside_the_frame_at_the_edge():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    tracker = make_tracker(2)
    tracker._detect_crop(frame, (1180, 620, 1280, 720))                 # the bottom-right corner
    sizes = [t.seen[0] for t in [tracker._crop_landmarker, *tracker._tta_landmarkers]]
    assert sizes == [(100, 100)] * 3                                    # the same size: shifted back inside, not cut off
