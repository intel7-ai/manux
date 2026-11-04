import math

from src.finger_overlay import TIP_LIMIT, overlay_frame
from src.hand_tracker import HandLandmark


def _hand(thumb=(0.40, 0.50)):
    """Open right hand, palm centred near (0.5, 0.55), wrist below; the thumb tip is movable."""
    pts = [(0.5, 0.7)] + [(0.45, 0.6)] * 4 + [(0.45, 0.5)] * 4 + [(0.5, 0.5)] * 4 + [(0.55, 0.5)] * 4 + [(0.6, 0.52)] * 4
    pts[4] = thumb
    pts[8], pts[12], pts[16], pts[20] = (0.45, 0.3), (0.5, 0.28), (0.55, 0.3), (0.6, 0.35)
    return [HandLandmark(x, y) for x, y in pts]


def test_tips_are_palm_relative_and_mirrored_like_the_preview():
    frame = overlay_frame(_hand(), mirrored=True, action="move_cursor")
    assert frame["mode"] == "move"
    index_dx, index_dy = frame["tips"][1]
    assert index_dx < 0 and index_dy < 0               # up and to the left of the palm
    flipped = overlay_frame(_hand(), mirrored=False)
    assert flipped["tips"][1][0] == -index_dx           # unmirrored camera: flip so right stays right


def test_only_the_finger_nearest_the_thumb_touches():
    assert overlay_frame(_hand(), True)["contact"] == -1
    touching = overlay_frame(_hand(thumb=(0.5, 0.29)), True)     # thumb on the middle fingertip
    assert touching["contact"] == 2


def test_unusable_hands_and_far_landmarks():
    assert overlay_frame(_hand()[:20], True) is None
    flat = [HandLandmark(0.5, 0.5)] * 21
    assert overlay_frame(flat, True) is None
    broken = _hand()
    broken[8] = HandLandmark(math.nan, 0.1)
    assert overlay_frame(broken, True) is None
    far = _hand()
    far[20] = HandLandmark(40.0, 0.5)
    assert overlay_frame(far, True)["tips"][4][0] == TIP_LIMIT


def test_unknown_actions_show_as_other():
    assert overlay_frame(_hand(), True, "show_desktop")["mode"] == "other"
    assert overlay_frame(_hand(), True, "right_click")["mode"] == "right"
