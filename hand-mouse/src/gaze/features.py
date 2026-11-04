"""Face landmarks -> gaze features and approximate head pose. Pure numpy, never raises.

Landmark indices follow the MediaPipe Face Mesh convention (478 points, iris 468..477). They
are given from that convention and have NOT been verified against the real model in this work:
"미확인 인덱스, 실제 모델로 검증 필요". Every index is injectable through `FaceIndices`, and
the synthetic tests use their own indices so they do not depend on the default values.

Coordinates: x, y are normalized image coordinates, z is on roughly the same scale as x.
Vertical distances are scaled by `aspect` (= image height / width) so angles are not skewed.
Head angles are approximations from landmark geometry, in degrees. Their sign is not verified
against the real model and the image may be mirrored, so only their magnitude is relied on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class FaceIndices:
    """Face Mesh landmark indices (unverified against the real model)."""

    # Each eye lists its two corners in IMAGE order (left corner, right corner) so that a
    # positive horizontal iris offset always means "iris moved toward image +x" for both eyes.
    eye_a_left: int = 33       # subject's right eye, outer corner
    eye_a_right: int = 133     # subject's right eye, inner corner
    eye_a_iris: int = 468
    eye_a_upper: int = 159
    eye_a_lower: int = 145
    eye_b_left: int = 362      # subject's left eye, inner corner
    eye_b_right: int = 263     # subject's left eye, outer corner
    eye_b_iris: int = 473
    eye_b_upper: int = 386
    eye_b_lower: int = 374
    right_cheek: int = 234     # image-left side of the face when not mirrored
    left_cheek: int = 454
    forehead: int = 10
    chin: int = 152

    def required(self) -> int:
        return max(getattr(self, f.name) for f in fields(self)) + 1


DEFAULT_INDICES = FaceIndices()


@dataclass(frozen=True)
class HeadPose:
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    face_cx: float      # face center in normalized image coordinates
    face_cy: float
    face_width: float   # cheek-to-cheek distance, normalized x units


@dataclass(frozen=True)
class GazeFeatures:
    h: float            # mean horizontal iris offset, in eye widths (about -0.5..0.5)
    v: float            # mean vertical iris offset, in eye widths (positive = down in image)
    h_a: float
    v_a: float
    h_b: float
    v_b: float
    openness: float     # min over both eyes of (eyelid gap / eye width); small = closed
    pose: HeadPose

    def vector(self) -> np.ndarray:
        """Regression input, roughly unit-scaled: [h, v, yaw, pitch, face_cx, face_cy]."""
        p = self.pose
        return np.array([self.h, self.v, p.yaw_deg / 45.0, p.pitch_deg / 45.0, p.face_cx, p.face_cy],
                        dtype=float)


FEATURE_NAMES = ("h", "v", "yaw", "pitch", "face_cx", "face_cy")


def as_array(landmarks: Any, need: int) -> Optional[np.ndarray]:
    """Return an (N, 3) float array, or None when the input is unusable."""
    if landmarks is None:
        return None
    try:
        if isinstance(landmarks, np.ndarray):
            arr = np.asarray(landmarks, dtype=float)
        else:
            rows = []
            for p in landmarks:
                if hasattr(p, "x"):
                    rows.append([float(p.x), float(p.y), float(getattr(p, "z", 0.0))])
                else:
                    rows.append([float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0])
            arr = np.array(rows, dtype=float)
    except Exception:  # noqa: BLE001 - never raise on bad input
        return None
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < need:
        return None
    if arr.shape[1] == 2:
        arr = np.concatenate([arr, np.zeros((arr.shape[0], 1))], axis=1)
    return arr[:, :3]


def _space(p: np.ndarray, aspect: float) -> np.ndarray:
    return np.array([p[0], p[1] * aspect, p[2]], dtype=float)


def head_pose_from_landmarks(landmarks: Any, aspect: float = 0.5625,
                             indices: FaceIndices = DEFAULT_INDICES) -> Optional[HeadPose]:
    """Approximate yaw/pitch/roll in degrees; None when the face is unusable."""
    arr = as_array(landmarks, indices.required())
    if arr is None or not math.isfinite(aspect) or aspect <= 0:
        return None
    rc, lc = _space(arr[indices.right_cheek], aspect), _space(arr[indices.left_cheek], aspect)
    fh, ch = _space(arr[indices.forehead], aspect), _space(arr[indices.chin], aspect)
    across = lc - rc
    up = fh - ch
    if not (np.isfinite(across).all() and np.isfinite(up).all()):
        return None
    width = float(np.hypot(across[0], across[1]))
    height = float(np.hypot(up[0], up[1]))
    if width < 1e-6 or height < 1e-6:
        return None
    # abs() keeps the angle independent of image mirroring; the sign is not meaningful.
    yaw = math.degrees(math.atan2(across[2] * (1.0 if across[0] >= 0 else -1.0), abs(across[0])))
    pitch = math.degrees(math.atan2(up[2], abs(up[1])))
    roll = math.degrees(math.atan2(across[1], abs(across[0])))
    mid = (arr[indices.right_cheek] + arr[indices.left_cheek]) / 2.0
    return HeadPose(yaw, pitch, roll, float(mid[0]), float(mid[1]), width)


def _eye(arr: np.ndarray, aspect: float, left: int, right: int, iris: int, upper: int, lower: int):
    pl, pr = _space(arr[left], aspect)[:2], _space(arr[right], aspect)[:2]
    pi = _space(arr[iris], aspect)[:2]
    pu, pd = _space(arr[upper], aspect)[:2], _space(arr[lower], aspect)[:2]
    axis = pr - pl
    w = float(np.hypot(axis[0], axis[1]))
    if not np.isfinite(axis).all() or w < 1e-6:
        return None
    e1 = axis / w
    e2 = np.array([-e1[1], e1[0]])  # image-down direction when the eye axis points to image +x
    mid = (pl + pr) / 2.0
    h = float(np.dot(pi - mid, e1) / w)
    v = float(np.dot(pi - mid, e2) / w)
    openness = float(abs(np.dot(pd - pu, e2)) / w)
    return h, v, openness


def extract_features(landmarks: Any, aspect: float = 0.5625,
                     indices: FaceIndices = DEFAULT_INDICES) -> Optional[GazeFeatures]:
    """Gaze features for one frame, or None (no face, short array, NaN, degenerate geometry)."""
    arr = as_array(landmarks, indices.required())
    if arr is None:
        return None
    pose = head_pose_from_landmarks(arr, aspect, indices)
    if pose is None:
        return None
    a = _eye(arr, aspect, indices.eye_a_left, indices.eye_a_right, indices.eye_a_iris,
             indices.eye_a_upper, indices.eye_a_lower)
    b = _eye(arr, aspect, indices.eye_b_left, indices.eye_b_right, indices.eye_b_iris,
             indices.eye_b_upper, indices.eye_b_lower)
    if a is None or b is None:
        return None
    if not all(math.isfinite(x) for x in (*a, *b)):
        return None
    return GazeFeatures(h=(a[0] + b[0]) / 2.0, v=(a[1] + b[1]) / 2.0, h_a=a[0], v_a=a[1],
                        h_b=b[0], v_b=b[1], openness=min(a[2], b[2]), pose=pose)
