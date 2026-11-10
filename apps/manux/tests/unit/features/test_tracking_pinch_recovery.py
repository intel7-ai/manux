"""Current-frame tracking recovery must sustain a click and the ensuing long-press drag."""
from dataclasses import replace

import pytest

from manux.features.pinch import DRAG, THUMB_MIDDLE, THUMB_RING, PinchFeature
from manux.tracking.landmarker import RawHand
from manux.tracking.tracker import HandTracker
import pinch_hands as H
from test_click_world_contact import world_contact


@pytest.mark.parametrize("fps", [14, 30])
@pytest.mark.parametrize("finger,wanted", [("middle", THUMB_MIDDLE), ("ring", THUMB_RING)])
def test_uncertain_crop_frames_do_not_repeatedly_reset_click_confirmation(fps, finger, wanted):
    shape = (H.middle_pinch if finger == "middle" else H.ring_pinch)()
    small = tuple((.5 + (x - .5) * .4, .5 + (y - .5) * .4, z * .4) for x, y, z in shape)
    good = RawHand(small, "Left", .99, world_contact(shape, finger))
    class Detector:
        last_detect_ms = 1.
        def detect(self, image, roi=None):
            return [replace(good, score=.6)] if roi else [good]
    tracker = HandTracker(Detector())
    tracker.auto_distance = True
    feature, out = PinchFeature(), []
    for i in range(2 * fps):
        frame = replace(H.obs(1 + i / fps, small).frame, seq=i)
        tracked = tracker.step(frame, None, "Right")
        out.extend(feature.step(tracked.hand, H.ctx(frame.t, settings={"long_press_drag_multiplier": 2.})))
    assert [i.gesture for i in out if i.phase == "tap"] == [wanted]
    drags = [i for i in out if i.gesture == DRAG]
    if finger == "middle":
        assert drags and drags[0].phase == "begin" and all(i.scale == 2. for i in drags)
    else:
        assert not drags
