from types import SimpleNamespace
from src.hand_selection import HandFollower, HandSelection, physical_side, selected_detection


def detection(side, score=0.95):
    return SimpleNamespace(handedness=side, score=score)


def hand_at(side, x=.5, y=.5, size=.04, score=.95):
    corners = [SimpleNamespace(x=x - size / 2, y=y - size / 2), SimpleNamespace(x=x + size / 2, y=y + size / 2)]
    return SimpleNamespace(handedness=side, score=score, landmarks=corners)


def following():
    """Mirrored camera, right hand as the control hand: MediaPipe should say "Left"."""
    follower = HandFollower()
    assert follower.select([hand_at("Left")], "Right", True, 0.0) is not None
    assert not follower.followed
    return follower


def test_follower_needs_a_strict_reading_to_start():
    follower = HandFollower()
    assert follower.select([hand_at("Right")], "Right", True, 0.0) is None
    assert follower.select([hand_at("Left", score=.5)], "Right", True, .1) is None
    assert follower.select([], "Right", True, .2) is None


def test_follower_keeps_a_flipped_or_unsure_hand_and_corrects_its_side():
    follower = following()
    flipped = hand_at("Right", x=.51)
    kept = follower.select([flipped], "Right", True, .1)
    assert follower.followed and kept.handedness == "Left" and kept.landmarks is flipped.landmarks
    assert flipped.handedness == "Right"
    assert follower.select([hand_at("Left", x=.52, score=.5)], "Right", True, .2) is not None
    assert follower.followed
    assert follower.select([hand_at("Left", x=.52)], "Right", True, .3) is not None
    assert not follower.followed


def test_follower_refuses_a_hand_elsewhere_or_of_another_size():
    assert following().select([hand_at("Right", x=.7)], "Right", True, .1) is None
    assert following().select([hand_at("Right", y=.7)], "Right", True, .1) is None
    assert following().select([hand_at("Right", size=.1)], "Right", True, .1) is None
    assert following().select([hand_at("Right", size=.01)], "Right", True, .1) is None


def test_follower_refuses_two_hands_even_when_one_is_close():
    follower = following()
    assert follower.select([hand_at("Right"), hand_at("Right", x=.8)], "Right", True, .1) is None
    assert not follower.followed


def test_follower_stops_after_a_long_gap():
    follower = following()
    assert follower.select([], "Right", True, .4) is None
    assert follower.select([hand_at("Right")], "Right", True, .49) is not None
    assert follower.select([hand_at("Right")], "Right", True, 1.0) is None


def test_follower_stops_without_a_strict_reading_and_starts_again_with_one():
    follower = following()
    t = 0.0
    while t < 2.9:
        t += .1
        assert follower.select([hand_at("Right")], "Right", True, t) is not None
    assert follower.select([hand_at("Right")], "Right", True, 3.1) is None
    assert follower.select([hand_at("Left")], "Right", True, 3.2) is not None
    assert follower.select([hand_at("Right")], "Right", True, 3.3) is not None
    assert follower.followed


def test_follower_forgets_the_hand_when_the_control_hand_changes():
    follower = following()
    assert follower.select([hand_at("Right")], "Left", True, .1) is not None
    assert not follower.followed
    assert follower.select([hand_at("Left")], "Left", True, .2) is not None
    assert follower.followed
    follower = following()
    follower.reset()
    assert follower.select([hand_at("Right")], "Right", True, .1) is None


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
