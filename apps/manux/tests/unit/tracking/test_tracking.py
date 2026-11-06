from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from manux.core.contracts import FrameInfo
from manux.tracking.crop import HandCrop
from manux.tracking.landmarker import RawHand, _hands, clamp_roi, crop_to_frame
from manux.tracking.selection import HandChoice, physical_side, select
from manux.tracking.tracker import HandTracker


def hand_at(cx, cy, span=0.05, label="Left", score=0.95, world=True):
    pts = tuple((cx + span * (i % 5 - 2) / 2, cy + span * (i // 5 - 2) / 2, 0.0) for i in range(21))
    w = tuple((0.01 * i, 0.0, 0.0) for i in range(21)) if world else None
    return RawHand(pts, label, score, w)


FRAME = FrameInfo(seq=0, t=1.0, width=1280, height=720, mirrored=True)


class FakeLandmarker:
    def __init__(self, full, crop=None):
        self.full, self.crop, self.calls, self.last_detect_ms = full, crop, [], 3.0

    def detect(self, image, roi=None):
        self.calls.append(roi)
        if roi is None:
            return list(self.full)
        return list(self.crop or [])

    def close(self):
        pass


# ---------------------------------------------------------------- selection

def test_physical_side_flips_on_a_mirrored_image_and_ignores_unsure_labels():
    assert physical_side(hand_at(.5, .5, label="Left"), mirrored=True) == "Right"
    assert physical_side(hand_at(.5, .5, label="Left"), mirrored=False) == "Left"
    assert physical_side(hand_at(.5, .5, score=0.5), mirrored=True) == ""


def test_select_needs_exactly_one_hand_on_that_side():
    a, b = hand_at(.3, .5, label="Left"), hand_at(.7, .5, label="Right")
    assert select([a, b], "Right", mirrored=True) is a
    assert select([a, a], "Right", mirrored=True) is None
    assert select([a], "", mirrored=True) is None


def test_hand_choice_needs_five_seconds_and_five_frames():
    choice = HandChoice()
    h = [hand_at(.5, .5, label="Left")]
    assert choice.observe(h, True, 0.0) == ""
    t = 0.0
    while t < 5.0:
        t += 0.2
        side = choice.observe(h, True, t)
    assert side == "Right"
    assert choice.observe([], True, t + 0.1) == "" and choice.side == ""


# ---------------------------------------------------------------- crop

def test_crop_follows_a_small_hand_and_skips_a_big_one():
    crop = HandCrop()
    small = hand_at(.5, .5, span=.05).landmarks
    roi = crop.update(small, 1280, 720)
    assert roi is not None and roi[2] - roi[0] == roi[3] - roi[1] >= crop.min_px
    assert crop.update(small, 1280, 720) == roi and crop.moves == 1     # stable while centred
    assert crop.update(hand_at(.5, .5, span=.6).landmarks, 1280, 720) is None


def test_crop_points_map_back_to_the_whole_frame():
    to_frame = crop_to_frame((100, 50, 300, 250), 1000, 500)
    assert to_frame(NS(x=0.5, y=0.5, z=0.1)) == pytest.approx((0.2, 0.3, 0.02))
    assert clamp_roi((-10, -10, 5000, 5000), 640, 480) == (0, 0, 640, 480)


def test_mediapipe_result_parsing_keeps_world_joints():
    lm = [NS(x=0.1, y=0.2, z=0.0)] * 21
    wl = [NS(x=0.01, y=0.02, z=0.03)] * 21
    res = NS(hand_landmarks=[lm], hand_world_landmarks=[wl],
             handedness=[[NS(category_name="Left", score=0.9)]])
    (h,) = _hands(res, lambda p: (p.x, p.y, p.z))
    assert h.label == "Left" and h.score == 0.9 and h.world[0] == (0.01, 0.02, 0.03)
    assert _hands(NS(hand_landmarks=[], handedness=[]), lambda p: p) == []


# ---------------------------------------------------------------- tracker

def test_tracker_returns_the_control_hand_as_an_observation():
    right = hand_at(.5, .5, label="Left")            # mirrored: physical right
    tr = HandTracker(FakeLandmarker([right]))
    res = tr.step(FRAME, image=None, control_hand="Right")
    assert res.hand is not None and res.hand.handedness == "Right" and res.hand.world is not None
    assert res.hand.frame is FRAME and res.path == "full" and len(res.hands) == 1
    assert tr.step(FRAME, None, control_hand="Left").hand is None
    assert tr.step(FRAME, None, "Right", detect=False).path == "skipped"


def test_crop_miss_falls_back_to_the_whole_frame():
    right = hand_at(.5, .5, label="Left")
    fake = FakeLandmarker([right], crop=[])
    tr = HandTracker(fake, use_crop=True)
    tr.step(FRAME, None, "Right")                     # sets the crop
    assert tr.crop.roi is not None
    res = tr.step(FRAME, None, "Right")
    assert res.path == "crop_miss>full" and res.hand is not None
    assert fake.calls[-2] is not None and fake.calls[-1] is None


def test_malformed_landmarks_become_no_hand():
    bad = RawHand(((0.5, 0.5, 0.0),) * 20, "Left", 0.95)
    assert HandTracker(FakeLandmarker([bad])).step(FRAME, None, "Right").hand is None


@pytest.mark.parametrize("requested", [None, 2])
def test_landmarker_defaults_to_one_hand_and_preserves_explicit_two(monkeypatch, tmp_path, requested):
    """Exercise the adapter's actual MediaPipe options, without loading a model or camera."""
    import sys
    import manux.tracking.landmarker as module
    from manux.app import schema
    from manux.settings.store import Settings
    calls = []
    def create(options):
        calls.append(options)
        return NS(close=lambda: None)
    vision = NS(HandLandmarkerOptions=lambda **values: values, RunningMode=NS(VIDEO="video"),
                HandLandmarker=NS(create_from_options=create))
    monkeypatch.setitem(sys.modules, "mediapipe", NS(tasks=NS(vision=vision, BaseOptions=lambda **values: values)))
    monkeypatch.setattr(module, "ensure_model", lambda path: path)
    settings = Settings(schema(), {"tracking.num_hands": requested} if requested else {})
    options = module.options_from(settings.section("tracking"))
    assert options["num_hands"] == (requested or 1)
    model_path = tmp_path / "한글 경로" / "손 모형.task"
    model_path.parent.mkdir()
    model_bytes = b"synthetic model bytes"
    model_path.write_bytes(model_bytes)
    detector = module.MediaPipeLandmarker(model_path, **options)
    try:
        assert calls[0]["num_hands"] == (requested or 1)
        assert detector._crop_options["num_hands"] == 1
        assert calls[0]["base_options"] == {"model_asset_buffer": model_bytes}
        assert detector._crop_options["base_options"]["model_asset_buffer"] is calls[0]["base_options"]["model_asset_buffer"]
    finally:
        detector.close()
