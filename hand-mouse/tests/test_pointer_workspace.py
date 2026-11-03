from types import SimpleNamespace

import pytest

from src.pointer_workspace import (
    FINGERTIPS, calibrated_pointer, pointer_axes,
    thumb_contacts, thumb_distances,
)


@pytest.mark.parametrize(
    ("touching", "ambiguous", "expected"),
    [
        ((1,), (), (0, 1)),
        ((1, 2), (), (0, 1, 2)),
        ((3,), (), (0, 3)),
        ((1,), (2,), ()),
        ((), (), ()),
    ],
)
def test_thumb_contact_combinations(touching, ambiguous, expected):
    points = [SimpleNamespace(x=.7, y=.4, z=0) for _ in range(21)]
    points[4].x = .3
    points[5].x = .4
    points[17].x = .6
    for finger in touching:
        points[FINGERTIPS[finger]].x = .32
    for finger in ambiguous:
        points[FINGERTIPS[finger]].x = .355
    assert thumb_contacts(thumb_distances(points, .5625)) == expected


def test_pointer_calibration_removes_axis_mixing_around_image_center():
    center, right, up = (.5, .5), (.6, .54), (.53, .35)
    aspect = .5625
    axes = pointer_axes(center, right, up, aspect)
    x, y = calibrated_pointer(center, axes, aspect)
    assert (x, y) == pytest.approx(center)
    right_x, right_y = calibrated_pointer(right, axes, aspect)
    up_x, up_y = calibrated_pointer(up, axes, aspect)
    assert right_x > x and right_y == pytest.approx(y)
    assert up_x == pytest.approx(x) and up_y < y


def test_pointer_calibration_rejects_short_or_parallel_strokes():
    assert pointer_axes((.5, .5), (.51, .5), (.5, .35), .5625) is None
    assert pointer_axes((.5, .5), (.6, .4), (.61, .39), .5625) is None


def test_index_tip_guard_rejects_spikes_but_accepts_whole_hand_motion():
    from src.pointer_workspace import IndexTipGuard

    points = [SimpleNamespace(x=.5, y=.5) for _ in range(21)]
    points[17].x = .6
    points[8].x = .56
    guard = IndexTipGuard()
    assert guard.accept(points, .5625)
    for point in points:
        point.x += .10
    assert guard.accept(points, .5625)
    points[8].x += .05
    assert not guard.accept(points, .5625)
    points[8].x -= .05
    assert guard.accept(points, .5625)
    points[8].x += .05
    assert not guard.accept(points, .5625)
    assert guard.accept(points, .5625)


@pytest.mark.parametrize("click", ["left_click", "right_click"])
def test_pointer_pose_clicks_on_release_directly_from_pointing(click):
    from src.pointer_workspace import PointerPose

    pose = PointerPose()
    assert pose.update(1.0, pointing=True) == "move_cursor"
    assert pose.update(1.04, pointing=True, pinching=True) == "pointer_wait"
    assert pose.update(1.08, pointing=False, click=click) == "pointer_release"
    assert pose.update(1.12, pointing=True, released=True) == "pointer_release"
    assert pose.update(1.16, pointing=True, released=True) == click
    assert pose.update(1.20, pointing=True, released=True) == "move_cursor"


def test_pointing_pose_accepts_index_aimed_toward_camera_but_not_open_palm():
    from src.pointer_workspace import is_pointing_pose

    points = [SimpleNamespace(x=.5, y=.5, z=0) for _ in range(21)]
    for step in range(4):
        points[5 + step].z = -.02 * step
    # The wrist ratio alone misses this index even though its joints are straight.
    assert is_pointing_pose(points, (1.25, 1.2, 1.2, 1.2), .5625)
    for base in (9, 13, 17):
        for step in range(4):
            points[base + step].z = -.02 * step
    assert not is_pointing_pose(points, (1.7, 1.7, 1.7, 1.7), .5625)


def _trigger_hand(thumb_gap, tip_x=.5):
    """Index MCP #5 at (.5, .5), PIP #6 straight above, palm width .12; thumb tip beside #5-#6."""
    points = [SimpleNamespace(x=.5, y=.5, z=0) for _ in range(21)]
    points[6].y = .42
    points[8].x, points[8].y = tip_x, .30
    points[17].x = .62
    points[4].x, points[4].y = .5 - thumb_gap, .46
    return points


FAR, NEAR, PRESSED = .09, .05, .02  # ratios .75, .42, .17 with the defaults (.50 / .28 / .40)


def test_trigger_ratio_measures_thumb_to_index_side_in_palm_widths():
    from src.pointer_workspace import trigger_ratio

    assert trigger_ratio(_trigger_hand(PRESSED), 1.0) == pytest.approx(.02 / .12)
    assert trigger_ratio(_trigger_hand(FAR), 1.0) == pytest.approx(.09 / .12)
    tiny = _trigger_hand(PRESSED)
    tiny[17].x = .51
    assert trigger_ratio(tiny, 1.0) != trigger_ratio(tiny, 1.0)  # NaN for a too-small palm


def test_trigger_quick_press_is_a_left_click_at_the_rewound_cursor():
    from src.pointer_workspace import ThumbTrigger

    trig = ThumbTrigger()
    for i, x in enumerate((100, 110, 120, 130)):
        assert trig.update(1.0 + i * .04, _trigger_hand(FAR), 1.0, pointing=True) == "move_cursor"
        trig.record(1.0 + i * .04, (x, 50))
    assert trig.update(1.16, _trigger_hand(NEAR), 1.0, pointing=True) == "pointer_wait"  # frozen
    assert trig.warp is None
    assert trig.update(1.20, _trigger_hand(PRESSED), 1.0, pointing=True) == "pointer_wait"
    assert trig.warp == (120, 50)  # last cursor position at least 100 ms before the press
    assert trig.update(1.24, _trigger_hand(.04), 1.0, pointing=True) == "pointer_wait"  # hysteresis
    assert trig.update(1.30, _trigger_hand(FAR), 1.0, pointing=True) == "left_click"
    assert trig.update(1.34, _trigger_hand(FAR), 1.0, pointing=True) == "move_cursor"


def test_trigger_long_still_press_is_one_right_click_and_no_left_click():
    from src.pointer_workspace import ThumbTrigger

    trig = ThumbTrigger()
    assert trig.update(1.0, _trigger_hand(PRESSED), 1.0, pointing=True) == "pointer_wait"
    actions = [trig.update(1.0 + i * .05, _trigger_hand(PRESSED), 1.0, pointing=True)
               for i in range(1, 20)]
    assert actions.count("right_click") == 1
    assert trig.update(2.1, _trigger_hand(FAR), 1.0, pointing=True) == "pointer_wait"


def test_trigger_press_and_move_drags_until_release_or_hand_loss():
    from src.pointer_workspace import ThumbTrigger

    trig = ThumbTrigger()
    trig.update(1.0, _trigger_hand(PRESSED), 1.0, pointing=True)
    # Small tip shake from the press itself is not a drag (.02 / .12 palm widths)
    assert trig.update(1.04, _trigger_hand(PRESSED, tip_x=.52), 1.0, pointing=True) == "pointer_wait"
    assert trig.update(1.08, _trigger_hand(PRESSED, tip_x=.56), 1.0, pointing=True) == "drag_hold"
    assert trig.update(1.12, _trigger_hand(PRESSED, tip_x=.60), 1.0, pointing=True) == "drag_hold"
    # No left click after a drag
    assert trig.update(1.16, _trigger_hand(FAR, tip_x=.60), 1.0, pointing=True) == "pointer_wait"

    trig.update(2.0, _trigger_hand(PRESSED), 1.0, pointing=True)
    assert trig.update(2.04, _trigger_hand(PRESSED, tip_x=.56), 1.0, pointing=True) == "drag_hold"
    lost = _trigger_hand(PRESSED)
    lost[17].x = .5  # palm collapses -> NaN ratio
    assert trig.update(2.08, lost, 1.0, pointing=True) == "pointer_wait"
    assert trig.state == "idle"


def test_trigger_only_moves_while_pointing():
    from src.pointer_workspace import ThumbTrigger

    trig = ThumbTrigger()
    assert trig.update(1.0, _trigger_hand(FAR), 1.0, pointing=False) == "pointer_wait"


def test_trigger_reads_thresholds_from_config_and_ignores_bad_values():
    from src.pointer_workspace import ThumbTrigger

    trig = ThumbTrigger()
    trig.configure({"trigger_press_ratio": .1, "trigger_long_press": "x", "trigger_rewind": -1})
    assert trig.settings["trigger_press_ratio"] == .1
    assert trig.settings["trigger_long_press"] == ThumbTrigger.DEFAULTS["trigger_long_press"]
    assert trig.settings["trigger_rewind"] == ThumbTrigger.DEFAULTS["trigger_rewind"]
    assert trig.update(1.0, _trigger_hand(PRESSED), 1.0, pointing=True) == "pointer_wait"
    assert trig.state == "approach"  # .17 is above the stricter .1 press ratio
