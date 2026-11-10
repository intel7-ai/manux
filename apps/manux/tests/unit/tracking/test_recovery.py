"""Synthetic detector failures, not personal recordings or measured recognition rates."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from manux.core.contracts import FrameInfo
from manux.tracking.landmarker import RawHand, _hands
from manux.tracking.selection import select
from manux.tracking.tracker import HandTracker
from test_tracking import FRAME, FakeLandmarker, hand_at


@pytest.mark.parametrize("bad", [hand_at(.5, .5, label="Right"), hand_at(.5, .5, score=.6),
                                 RawHand(((.5, .5, 0.),) * 20, "Left", .95)])
def test_crop_without_a_valid_control_hand_recovers_from_current_full_frame(bad):
    good = hand_at(.5, .5)
    detector = FakeLandmarker([good], [bad])
    tracker = HandTracker(detector, use_crop=True)
    tracker.step(FRAME, None, "Right")
    start = len(detector.calls)
    frame = replace(FRAME, seq=1, t=1.05)
    result = tracker.step(frame, None, "Right")
    assert result.hand is not None and result.hand.frame is frame
    assert result.hand.landmarks == good.landmarks and result.hand.handedness == "Right"
    assert result.path == "crop_miss>full" and result.zoom == 1.
    assert detector.calls[start:][0] is not None and detector.calls[start:][1:] == [None]
    assert result.detect_ms == 6.


def test_failed_recovery_returns_no_hand_without_reusing_previous_observation():
    good = hand_at(.5, .5)
    wrong = hand_at(.5, .5, label="Right")
    detector = FakeLandmarker([good], [wrong])
    tracker = HandTracker(detector)
    tracker.auto_distance = True
    tracker.step(FRAME, None, "Right")
    detector.full = [wrong]
    start = len(detector.calls)
    result = tracker.step(replace(FRAME, seq=1, t=1.05), None, "Right")
    assert result.hand is None and len(detector.calls) - start == 2
    assert tracker.crop.roi is None


@pytest.mark.parametrize("uncertain", [False, True])
def test_full_frame_wrong_or_uncertain_hand_does_not_prevent_target_search(uncertain):
    wrong = hand_at(.5, .5, score=.6) if uncertain else hand_at(.5, .5, label="Right")
    good = hand_at(.5, .5)
    detector = FakeLandmarker([wrong], [good])
    tracker = HandTracker(detector)
    tracker.auto_distance = True
    result = tracker.step(FRAME, None, "Right")
    assert result.hand is not None and result.hand.landmarks == good.landmarks
    assert result.path == "search" and len(detector.calls) == 2


@pytest.mark.parametrize("side,hands", [("", [hand_at(.5, .5)]),
                                       ("Right", [hand_at(.3, .5), hand_at(.7, .5)])])
def test_search_does_not_choose_a_hand_during_selection_or_ambiguous_duplicates(side, hands):
    detector = FakeLandmarker(hands, [hands[0]])
    tracker = HandTracker(detector)
    tracker.auto_distance = True
    result = tracker.step(FRAME, None, side)
    assert result.hand is None and detector.calls == [None]


def test_switching_control_hand_discards_old_hand_crop_immediately():
    old, new = hand_at(.3, .5), hand_at(.7, .5, label="Right")
    detector = FakeLandmarker([old, new], [old])
    tracker = HandTracker(detector, use_crop=True)
    tracker.step(FRAME, None, "Right")
    detector.calls.clear()
    result = tracker.step(replace(FRAME, seq=1, t=1.05), None, "Left")
    assert result.hand is not None and result.hand.landmarks == new.landmarks
    assert detector.calls == [None]


def test_repeated_crop_failures_use_full_view_briefly_instead_of_double_detection_every_frame():
    good = hand_at(.5, .5)
    detector = FakeLandmarker([good], [replace(good, score=.6)])
    tracker = HandTracker(detector, use_crop=True)
    extras = 0
    for i in range(30):
        start = len(detector.calls)
        result = tracker.step(replace(FRAME, seq=i, t=1 + i / 30), None, "Right")
        assert result.hand is not None
        extras += len(detector.calls) - start - 1
    assert 1 <= extras <= 4


def test_lost_corner_hand_is_redetected_at_last_region_before_scanning_elsewhere():
    good = hand_at(.1, .1, span=.02)
    class Detector(FakeLandmarker):
        expected_roi = None
        def detect(self, image, roi=None):
            detected = super().detect(image, roi)
            return detected if roi is None or roi == self.expected_roi else []
    detector = Detector([good], [])
    tracker = HandTracker(detector)
    tracker.auto_distance = True
    tracker.step(FRAME, None, "Right")
    previous_roi = tracker.crop.roi
    detector.expected_roi = previous_roi
    detector.full = []
    lost = tracker.step(replace(FRAME, seq=1, t=1.05), None, "Right")
    assert lost.hand is None
    detector.crop = [good]
    recovered = tracker.step(replace(FRAME, seq=2, t=1.1), None, "Right")
    assert recovered.hand is not None and detector.calls[-1] == previous_roi
    # A successful fresh crop must continue tracking even while the full view misses the tiny hand.
    start = len(detector.calls)
    again = tracker.step(replace(FRAME, seq=3, t=1.15), None, "Right")
    assert again.hand is not None and len(detector.calls) - start == 1


@pytest.mark.parametrize("width,height", [(640, 480), (480, 640), (1920, 480)])
@pytest.mark.parametrize("point", [(.02, .02), (.98, .02), (.02, .98), (.98, .98), (.3, .5)])
def test_search_reaches_edges_and_corners_in_landscape_portrait_and_wide_frames(width, height, point):
    """A small hand missed by full-frame detection must be covered by a later search ROI."""
    good = hand_at(*point, span=.008)
    px, py = point[0] * width, point[1] * height
    class Detector(FakeLandmarker):
        def detect(self, image, roi=None):
            self.calls.append(roi)
            return [good] if roi and roi[0] <= px < roi[2] and roi[1] <= py < roi[3] else []
    detector = Detector([])
    tracker = HandTracker(detector)
    tracker.auto_distance = True
    found = None
    for i in range(24):
        start = len(detector.calls)
        frame = FrameInfo(i, 1 + i * .26, width, height, True)
        result = tracker.step(frame, None, "Right")
        assert len(detector.calls) - start <= 2
        if result.hand:
            found = result.hand
            break
    assert found is not None and found.landmarks == good.landmarks


@pytest.mark.parametrize("categories", [[[NS(category_name="Left", score=0.)]], [], [[]],
                                         [[NS(category_name=None, score=.99)]]])
def test_missing_or_zero_handedness_is_not_promoted_to_a_certain_control_hand(categories):
    points = [NS(x=.5, y=.5, z=0.)] * 21
    result = NS(hand_landmarks=[points], handedness=categories)
    hands = _hands(result, lambda p: (p.x, p.y, p.z))
    assert select(hands, "Right", True) is None
    assert select(hands, "Left", True) is None
