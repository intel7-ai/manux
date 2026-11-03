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
