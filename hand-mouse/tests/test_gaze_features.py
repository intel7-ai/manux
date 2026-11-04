"""Gaze features: roundtrip on synthetic faces, injected indices, hostile input."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gaze.features import FaceIndices, extract_features, head_pose_from_landmarks  # noqa: E402
from src.gaze.synthetic import build_face  # noqa: E402

# Indices that differ from the Face Mesh defaults, so these tests cannot pass by accident
# through the default numbers (which are unverified against the real model).
CUSTOM = FaceIndices(eye_a_left=0, eye_a_right=1, eye_a_iris=2, eye_a_upper=3, eye_a_lower=4,
                     eye_b_left=5, eye_b_right=6, eye_b_iris=7, eye_b_upper=8, eye_b_lower=9,
                     right_cheek=10, left_cheek=11, forehead=12, chin=13)


@pytest.mark.parametrize("indices", [FaceIndices(), CUSTOM])
def test_iris_offsets_roundtrip(indices):
    face = build_face(indices, gaze_h=0.12, gaze_v=-0.05)
    f = extract_features(face, indices=indices)
    assert f is not None
    assert f.h == pytest.approx(0.12, abs=1e-9) and f.v == pytest.approx(-0.05, abs=1e-9)
    assert f.h_a == pytest.approx(f.h_b) and f.openness == pytest.approx(0.35, abs=1e-9)


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (20, 0), (-30, 10), (5, -25)])
def test_head_angles_roundtrip_and_aspect_independent(yaw, pitch):
    for aspect in (0.5625, 0.75):
        pose = head_pose_from_landmarks(build_face(CUSTOM, yaw_deg=yaw, pitch_deg=pitch, aspect=aspect),
                                        aspect, CUSTOM)
        assert pose is not None
        assert pose.yaw_deg == pytest.approx(yaw, abs=1e-6)
        assert pose.pitch_deg == pytest.approx(pitch, abs=1e-6)
        assert pose.roll_deg == pytest.approx(0.0, abs=1e-6)


def test_mirrored_image_keeps_angle_magnitudes_and_offset_magnitudes():
    face = build_face(CUSTOM, gaze_h=0.1, yaw_deg=18, pitch_deg=7)
    mirrored = face.copy()
    mirrored[:, 0] = 1.0 - mirrored[:, 0]
    a, b = extract_features(face, indices=CUSTOM), extract_features(mirrored, indices=CUSTOM)
    assert abs(a.pose.yaw_deg) == pytest.approx(abs(b.pose.yaw_deg), abs=1e-6)
    assert abs(a.pose.pitch_deg) == pytest.approx(abs(b.pose.pitch_deg), abs=1e-6)
    assert abs(a.h) == pytest.approx(abs(b.h), abs=1e-6)


def test_closed_eye_has_small_openness():
    f = extract_features(build_face(CUSTOM, openness=0.02), indices=CUSTOM)
    assert f is not None and f.openness == pytest.approx(0.02, abs=1e-9)


def test_object_style_landmarks_are_accepted():
    class P:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    face = build_face(CUSTOM, gaze_h=0.07)
    objs = [P(*row) for row in face]
    f = extract_features(objs, indices=CUSTOM)
    assert f is not None and f.h == pytest.approx(0.07, abs=1e-9)


def test_hostile_input_returns_none_never_raises():
    face = build_face(CUSTOM)
    nan_face = face.copy()
    nan_face[2, 0] = float("nan")
    inf_face = face.copy()
    inf_face[12, 1] = float("inf")
    collapsed = face.copy()
    collapsed[0] = collapsed[1]  # eye corners on top of each other -> zero eye width
    flat_head = face.copy()
    flat_head[10] = flat_head[11]  # zero face width
    cases = [None, [], "face", 5, face[:5], face[:, :1], nan_face, inf_face, collapsed, flat_head,
             [[1, 2], [3]], np.zeros((478, 3)), object()]
    for case in cases:
        assert extract_features(case, indices=CUSTOM) is None
    assert extract_features(face, aspect=0.0, indices=CUSTOM) is None
    assert extract_features(face, aspect=float("nan"), indices=CUSTOM) is None


def test_two_column_landmarks_work_without_z():
    face = build_face(CUSTOM, gaze_h=0.05)
    f = extract_features(face[:, :2], indices=CUSTOM)
    assert f is not None and f.h == pytest.approx(0.05, abs=1e-9)
    assert math.isfinite(f.pose.yaw_deg)
