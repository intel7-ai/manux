"""AttentionGate: default off, hysteresis, minimum frames, missing face, reset."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gaze.attention import AttentionGate  # noqa: E402
from src.gaze.features import HeadPose  # noqa: E402


def pose(yaw=0.0, pitch=0.0):
    return HeadPose(yaw, pitch, 0.0, 0.5, 0.5, 0.3)


def feed(gate, poses):
    return [gate.update(p) for p in poses]


def test_default_gate_is_disabled_and_never_blocks():
    gate = AttentionGate()
    assert gate.enabled is False
    assert all(feed(gate, [None, pose(80), pose(0, 80), None]))


def test_entering_needs_consecutive_frames_inside_the_enter_limits():
    gate = AttentionGate(enabled=True, min_enter_frames=3)
    assert feed(gate, [pose(), pose(), pose()]) == [False, False, True]
    gate = AttentionGate(enabled=True, min_enter_frames=3)
    assert feed(gate, [pose(), pose(), pose(25), pose(), pose(), pose()]) == [False] * 5 + [True]  # run broken


def test_enter_limit_is_inclusive_and_exit_limit_is_wider():
    gate = AttentionGate(enabled=True, enter_yaw_deg=20, exit_yaw_deg=28, min_enter_frames=1, min_exit_frames=2)
    assert gate.update(pose(yaw=20.0)) is True  # exactly on the enter limit counts as inside
    # between enter (20) and exit (28): stays attending, no flicker
    assert feed(gate, [pose(24), pose(26), pose(-27)]) == [True, True, True]
    # just outside the exit limit for 1 frame is not enough
    assert feed(gate, [pose(30), pose(0)]) == [True, True]
    # two consecutive frames outside leaves
    assert feed(gate, [pose(30), pose(-31)]) == [True, False]


def test_not_attending_between_enter_and_exit_limits_stays_out():
    gate = AttentionGate(enabled=True, enter_yaw_deg=20, exit_yaw_deg=28, min_enter_frames=1)
    assert gate.update(pose(24)) is False  # inside exit limit but outside enter limit: not enough to enter


def test_pitch_is_gated_like_yaw():
    gate = AttentionGate(enabled=True, enter_pitch_deg=15, exit_pitch_deg=24, min_enter_frames=1, min_exit_frames=1)
    assert gate.update(pose(0, 16)) is False
    assert gate.update(pose(0, 10)) is True
    assert gate.update(pose(0, 25)) is False


def test_missing_face_leaves_after_min_exit_frames_and_blocks_entering():
    gate = AttentionGate(enabled=True, min_enter_frames=1, min_exit_frames=2)
    assert gate.update(pose()) is True
    assert feed(gate, [None, None]) == [True, False]
    assert gate.update(None) is False
    # one good frame after a loss re-enters (min_enter_frames=1) and a lone loss does not leave
    assert gate.update(pose()) is True
    assert feed(gate, [None, pose()]) == [True, True]


def test_nan_pose_counts_as_missing():
    gate = AttentionGate(enabled=True, min_enter_frames=1, min_exit_frames=1)
    assert gate.update(pose()) is True
    assert gate.update(pose(float("nan"))) is False


def test_exit_limits_never_narrower_than_enter_limits():
    gate = AttentionGate(enabled=True, enter_yaw_deg=30, exit_yaw_deg=10, min_enter_frames=1, min_exit_frames=1)
    assert gate.exit_yaw == 30
    assert gate.update(pose(29)) is True and gate.update(pose(29)) is True  # no flip-flop


def test_reset_and_frame_counts_clamped():
    gate = AttentionGate(enabled=True, min_enter_frames=0, min_exit_frames=-3)
    assert gate.min_enter_frames == 1 and gate.min_exit_frames == 1
    assert gate.update(pose()) is True
    gate.reset()
    assert gate.attending is False
    gate.enabled = False
    assert gate.update(None) is True
