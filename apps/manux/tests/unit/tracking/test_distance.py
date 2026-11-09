from dataclasses import replace
import math

import pytest

from manux.core.contracts import FrameInfo, HandObservation
from manux.tracking.distance import DistanceAssist, palm_fraction
from manux.tracking.tracker import HandTracker
from manux.tracking.landmarker import RawHand


def hand(size=.16, tilt=0., score=.99):
    frame = FrameInfo(0, 0., 640, 480, True)
    bases = {0: (0., 0.), 5: (-.35, -.85), 9: (0., -1.), 17: (.35, -.8)}
    world, image = [], []
    for i in range(21):
        x, y = bases.get(i, (.1 * (i % 5), -.7))
        world.append((x * .08, y * .08 * math.cos(tilt), y * .08 * math.sin(tilt)))
        image.append((.5 + x * size * frame.aspect, .6 + y * size * math.cos(tilt), 0.))
    return HandObservation(frame, tuple(image), "Right", score, tuple(world))


def settle(assist, observation, seconds=4.):
    for i in range(round(seconds * 30)):
        assist.update(observation, i / 30)


def test_near_hand_unchanged_far_hand_bounded_and_gradual():
    near, far = DistanceAssist(), DistanceAssist()
    settle(near, hand(.2))
    assert near.gain == 1.
    before = far.gain
    for i in range(150):
        far.update(hand(.04), i / 30)
        assert 1 <= far.gain <= 2.5
        assert far.gain - before <= .8 / 30 + 1e-8
        before = far.gain
    assert far.gain > 2.4


def test_finger_curl_and_palm_tilt_are_not_camera_distance():
    palm = hand(.12)
    curled = list(palm.landmarks)
    for i in (4, 8, 12, 16, 20):
        curled[i] = palm.landmarks[0]
    assert palm_fraction(replace(palm, landmarks=tuple(curled))) == pytest.approx(palm_fraction(palm))
    assert palm_fraction(hand(.12, tilt=.8)) == pytest.approx(palm_fraction(palm))


def test_occlusion_low_confidence_outlier_and_disable_never_trigger_zoom_runaway():
    assist = DistanceAssist()
    settle(assist, hand(.16))
    assist.update(hand(.02), 4.1)
    assert assist.gain == 1.  # one false small hand cannot amplify movement
    for i in range(10):
        assist.update(hand(.02, score=.4), 4.2 + i / 30)
    assert assist.gain == 1.
    assist.update(None, 6.)
    assert assist.gain == 1. and assist.last_valid is None
    settle(assist, hand(.08))
    assert assist.gain > 1.9
    assist.update(hand(.08), 5., enabled=False)
    assert assist.gain == 1. and assist.fraction == 0


def test_auto_crop_keeps_full_coordinates_and_recovers_with_bounded_search():
    palm = hand(.08)
    raw = RawHand(palm.landmarks, "Left", .99, palm.world)
    class Detector:
        def __init__(self): self.calls = []; self.found = True
        def detect(self, image, roi=None):
            self.calls.append(roi)
            return [raw] if self.found else []
    detector = Detector()
    tracker = HandTracker(detector)
    tracker.auto_distance = True
    first = tracker.step(palm.frame, None, "Right")
    second = tracker.step(replace(palm.frame, seq=1, t=.05), None, "Right")
    assert first.hand.landmarks == second.hand.landmarks == palm.landmarks
    assert second.zoom > 1. and detector.calls[-1] is not None
    # A resolution change must never use last frame's pixel ROI.
    tracker.step(replace(palm.frame, seq=2, t=.1, width=960, height=720), None, "Right")
    assert detector.calls[-1] is None
    detector.found = False
    tracker.reset()
    total = 0
    for i in range(30):
        previous = len(detector.calls)
        result = tracker.step(replace(palm.frame, seq=i, t=1 + i / 30), None, "Right")
        assert result.hand is None and len(detector.calls) - previous <= 2
        total += len(detector.calls) - previous - 1
    assert total <= 4
