"""pose-angles-v1 values are pinned: a formula change must come with a new FEATURE_VERSION.

The pinned numbers are a regression pin produced by this implementation on a synthetic hand
(`pose_hands.make_hand`), not an independent reference: they catch an accidental change of a value
or of the column order. Meaning of the columns: perception/pose_angles.py.
"""
from __future__ import annotations

import numpy as np
import pytest

import pose_hands as H
from manux.core.contracts import FrameInfo, HandObservation
from manux.perception import pose_angles as pa
from manux.perception.pose_angles import COLUMNS, FEATURE_VERSION, FEATURES, frame_pose_angles, observation_pose_angles, pose_angles

OPEN = [1.37558, 1.93097, 2.06495, 1.91859, 1.65273, 0.71713, 0.7642, 0.36021, 0.26171, 0.25557, 0.92835, 0.62811,
        0.75489, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 0.8854, 0.78495, 0.76, 0.93699, 1.06192, 0.44844, 1.22369,
        1.47555, 1.51418, 1.51961, 1.0, 1.0, 1.0, 1.0]
BENT = [1.25299, 1.38669, 1.45525, 1.91859, 1.65273, 0.8575, 0.7642, 0.36021, 0.23926, 1.13966, 0.99495, 0.61564,
        0.75489, -0.92106, -0.83289, -0.40569, -0.42211, -1.0, -1.0, 0.81511, 0.78495, 0.76, 0.16526, 0.18466,
        0.24245, 0.62657, 0.83649, 1.63818, 1.57399, 0.80598, 0.81021, 1.0, 1.0]
BENT_HAND = {"index": 0.8, "middle": 0.8, "thumb": 0.4}
COLUMN = {name: i for i, name in enumerate(COLUMNS)}


def row(**curl):
    return pose_angles(H.make_hand(curl or None))


def test_version_and_size():
    assert FEATURE_VERSION == "pose-angles-v1"
    assert FEATURES == 33 == len(COLUMNS) == len(set(COLUMNS))


def test_column_order_is_fixed():
    assert COLUMNS[:5] == ("tip_wrist_thumb", "tip_wrist_index", "tip_wrist_middle", "tip_wrist_ring", "tip_wrist_pinky")
    assert COLUMNS[13:19] == ("cos_thumb_ip", "cos_thumb_mcp", "cos_index_pip", "cos_middle_pip", "cos_ring_pip",
                              "cos_pinky_pip")
    assert COLUMNS[25:] == ("thumb_to_index_tip", "thumb_to_middle_tip", "thumb_to_ring_tip", "thumb_to_pinky_tip",
                            "straight_index", "straight_middle", "straight_ring", "straight_pinky")


def test_values_are_pinned():
    a, b = pose_angles(H.make_hand()), pose_angles(H.make_hand(BENT_HAND))
    assert a.dtype == b.dtype == np.float32 and a.shape == b.shape == (FEATURES,)
    np.testing.assert_allclose(a, OPEN, atol=2e-5)
    np.testing.assert_allclose(b, BENT, atol=2e-5)


@pytest.mark.parametrize("rot,scale,shift", [
    (H.rotation(0.3, -0.5, 1.1), 1.0, (0.0, 0.0, 0.0)),
    (None, 1.7, (0.0, 0.0, 0.0)),
    (None, 1.0, (0.4, -0.2, 0.9)),
    (H.rotation(2.0, 0.2, -2.6), 0.6, (-0.3, 0.1, 0.5)),
])
@pytest.mark.parametrize("curl", [{}, BENT_HAND])
def test_whole_hand_motion_does_not_change_the_row(rot, scale, shift, curl):
    """Position, size and orientation are removed by the hand frame (local3d-v1)."""
    world = H.make_hand(curl)
    moved = H.move(world, rot, scale, shift)
    np.testing.assert_allclose(pose_angles(moved), pose_angles(world), atol=1e-5)


@pytest.mark.parametrize("curl", [{}, BENT_HAND, {"pinky": 1.0, "ring": 0.5}])
def test_a_mirrored_hand_gives_the_same_row(curl):
    """Only distances, cosines and the x / y parts are used, so a left hand reads like a right hand."""
    world = H.make_hand(curl)
    np.testing.assert_allclose(pose_angles(H.mirror(world)), pose_angles(world), atol=1e-5)


def test_bending_a_finger_changes_that_fingers_columns_only():
    straight, bent = row(), row(index=0.9)
    assert straight[COLUMN["straight_index"]] == pytest.approx(1.0)
    assert bent[COLUMN["straight_index"]] < 0.8
    assert bent[COLUMN["cos_index_pip"]] > straight[COLUMN["cos_index_pip"]] + 0.4
    assert bent[COLUMN["tip_wrist_index"]] < straight[COLUMN["tip_wrist_index"]]
    for name in ("straight_middle", "straight_ring", "straight_pinky", "cos_middle_pip", "cos_ring_pip",
                 "cos_pinky_pip", "tip_wrist_middle", "tip_wrist_ring", "tip_wrist_pinky"):
        assert bent[COLUMN[name]] == pytest.approx(straight[COLUMN[name]], abs=1e-6), name


def test_a_pinch_brings_the_thumb_tip_close_to_that_fingertip():
    straight, pinch = row(), row(index=0.8, middle=0.8, thumb=0.4)
    for name in ("thumb_to_index_tip", "thumb_to_middle_tip"):
        assert pinch[COLUMN[name]] < straight[COLUMN[name]] - 0.4, name
    assert pinch[COLUMN["thumb_to_ring_tip"]] > pinch[COLUMN["thumb_to_index_tip"]]


def test_pinky_and_ring_straightness_follow_their_own_fingers():
    folded = row(pinky=1.1, ring=1.1)
    assert folded[COLUMN["straight_pinky"]] < 0.75 and folded[COLUMN["straight_ring"]] < 0.75
    assert folded[COLUMN["straight_index"]] == pytest.approx(1.0)
    assert folded[COLUMN["ratio_pinky_ring_span"]] < row()[COLUMN["ratio_pinky_ring_span"]]


def test_values_stay_in_their_ranges_for_many_poses():
    rng = np.random.default_rng(3)
    for _ in range(200):
        curl = {name: float(rng.uniform(0, 1.3)) for name in H.NAMES}
        out = pose_angles(H.move(H.make_hand(curl, thumb_spread=float(rng.uniform(0.2, 1.4))),
                                 H.rotation(*rng.uniform(-3, 3, size=3)), float(rng.uniform(0.5, 2.0)),
                                 rng.uniform(-1, 1, size=3)))
        assert out is not None and np.isfinite(out).all()
        for name in COLUMNS:
            v = float(out[COLUMN[name]])
            if name.startswith("cos_"):
                assert -1.0 - 1e-6 <= v <= 1.0 + 1e-6, name
            if name.startswith("straight_"):
                assert 0.0 <= v <= 1.0 + 1e-6, name
            if name == "ratio_pinky_ring_span":
                assert 0.0 <= v <= 4.0 + 1e-6


@pytest.mark.parametrize("bad", [
    None, [], [[0.0, 0.0, 0.0]] * 20, [[0.0, 0.0, 0.0]] * 22, "hand", 5, [[0.0, 0.0]] * 21, [["a", "b", "c"]] * 21,
    [[float("nan"), 0.0, 0.0]] * 21, [[float("inf"), 0.0, 0.0]] * 21, [[0.0, 0.0, 0.0]] * 21,    # zero-size hand
])
def test_an_unusable_hand_is_none_not_an_error(bad):
    assert pose_angles(bad) is None


def test_a_hand_without_the_x_axis_is_none():
    """Index and little-finger knuckles on the palm axis: no x direction can be formed."""
    world = np.array(H.make_hand())
    world[5] = world[17] = world[9]
    assert pose_angles(world) is None


def hand_obs(world, t=0.0):
    frame = FrameInfo(seq=0, t=t, width=1280, height=720, mirrored=True)
    lm = [(0.5, 0.5, 0.0)] * 21
    return HandObservation(frame, lm, "Right", 0.9, world=tuple(tuple(p) for p in world) if world else None)


def test_observation_helper():
    assert observation_pose_angles(None) is None
    assert observation_pose_angles(hand_obs(None)) is None
    np.testing.assert_allclose(observation_pose_angles(hand_obs(H.make_hand())), OPEN, atol=2e-5)


def test_recorded_frames_give_rows_and_a_validity_mask():
    frames = [{"t": 0.0, "world": H.make_hand()}, {"t": 0.03}, {"t": 0.06, "world": None},
              {"t": 0.09, "world": [[0.0, 0.0, 0.0]] * 21}, {"t": 0.12, "world": H.make_hand(BENT_HAND)}, "junk"]
    rows, valid = frame_pose_angles(frames)
    assert rows.shape == (6, FEATURES) and rows.dtype == np.float32
    assert valid.tolist() == [True, False, False, False, True, False]
    np.testing.assert_allclose(rows[0], OPEN, atol=2e-5)
    np.testing.assert_allclose(rows[4], BENT, atol=2e-5)
    assert not rows[~valid].any()


def test_no_state_between_calls():
    a, b = pose_angles(H.make_hand(BENT_HAND)), pose_angles(H.make_hand())
    np.testing.assert_array_equal(pose_angles(H.make_hand(BENT_HAND)), a)
    np.testing.assert_array_equal(pose_angles(H.make_hand()), b)


def test_module_exports():
    assert pa.FEATURE_VERSION == FEATURE_VERSION and pa.FEATURES == FEATURES
