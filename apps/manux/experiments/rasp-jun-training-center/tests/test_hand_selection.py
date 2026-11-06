from types import SimpleNamespace
from src.hand_selection import HandSelection, physical_side, selected_detection


def detection(side, score=0.95):
    return SimpleNamespace(handedness=side, score=score)


def test_setup_accepts_only_one_stable_high_confidence_hand():
    gate = HandSelection()
    left = detection("Right")
    for i in range(5):
        assert gate.observe([left], True, i * .1) == ""
    assert gate.observe([left, detection("Left")], True, .5) == ""
    for i in range(50):
        assert gate.observe([left], True, 1 + i * .1) == ""
    assert gate.observe([left], True, 6.0) == "Left"
    assert gate.observe([left], True, 6.4) == ""
    assert gate.seconds_remaining(6.4) == 5.0


def test_selected_hand_is_used_even_when_opposite_hand_appears_first():
    left, right = detection("Right"), detection("Left")
    assert physical_side(left, False) == "Right"
    assert selected_detection([right, left], "Left", True) is left
    assert selected_detection([right], "Left", True) is None
    assert selected_detection([detection("Right", .4)], "Left", True) is None
    assert selected_detection([left, left], "Left", True) is None
