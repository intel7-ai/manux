"""Synthetic face landmarks for tests and `gaze_validate.py --synthetic`. numpy only.

`build_face` writes only the landmarks that `FaceIndices` names (all other rows stay zero), so
tests can inject their own indices and prove the feature code does not depend on the default
Face Mesh numbers. `SyntheticSource` plays a simulated person looking at on-screen targets,
with optional noise, blinks, dropped frames and slow drift. It models nothing about a real
face or a real model: results from it say the code is consistent, not how accurate gaze is.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from .features import DEFAULT_INDICES, FaceIndices


def build_face(indices: FaceIndices = DEFAULT_INDICES, gaze_h: float = 0.0, gaze_v: float = 0.0,
               yaw_deg: float = 0.0, pitch_deg: float = 0.0, cx: float = 0.5, cy: float = 0.5,
               aspect: float = 0.5625, openness: float = 0.35, eye_w: float = 0.06,
               face_w: float = 0.30, face_h: float = 0.35) -> np.ndarray:
    pts = np.zeros((max(478, indices.required()), 3), dtype=float)
    ys0 = cy * aspect  # work in aspect space, convert back at the end

    def put(idx: int, x: float, ys: float, z: float = 0.0) -> None:
        pts[idx] = (x, ys / aspect, z)

    def eye(left: int, right: int, iris: int, upper: int, lower: int, ex: float, ey: float) -> None:
        put(left, ex - eye_w / 2, ey)
        put(right, ex + eye_w / 2, ey)
        put(iris, ex + gaze_h * eye_w, ey + gaze_v * eye_w)
        put(upper, ex, ey - openness * eye_w / 2)
        put(lower, ex, ey + openness * eye_w / 2)

    eye(indices.eye_a_left, indices.eye_a_right, indices.eye_a_iris, indices.eye_a_upper,
        indices.eye_a_lower, cx - 0.07, ys0 - 0.03)
    eye(indices.eye_b_left, indices.eye_b_right, indices.eye_b_iris, indices.eye_b_upper,
        indices.eye_b_lower, cx + 0.07, ys0 - 0.03)
    dz = face_w * math.tan(math.radians(yaw_deg))
    put(indices.right_cheek, cx - face_w / 2, ys0, -dz / 2)
    put(indices.left_cheek, cx + face_w / 2, ys0, dz / 2)
    dzp = face_h * math.tan(math.radians(pitch_deg))
    put(indices.forehead, cx, ys0 - face_h / 2, dzp / 2)
    put(indices.chin, cx, ys0 + face_h / 2, -dzp / 2)
    return pts


class SyntheticSource:
    """A simulated person. `capture(target, n)` returns [(time_s, landmarks_or_None)] frames."""

    def __init__(self, indices: FaceIndices = DEFAULT_INDICES, aspect: float = 0.5625, fps: float = 30.0,
                 gain_h: float = 0.30, gain_v: float = 0.20, noise: float = 0.0, head_wobble_deg: float = 0.0,
                 drift_h_per_min: float = 0.0, drift_v_per_min: float = 0.0, blink_rate: float = 0.0,
                 dropout_rate: float = 0.0, seed: int = 1) -> None:
        self.indices, self.aspect, self.fps = indices, aspect, fps
        self.gain_h, self.gain_v, self.noise = gain_h, gain_v, noise
        self.head_wobble_deg = head_wobble_deg
        self.drift_h_per_min, self.drift_v_per_min = drift_h_per_min, drift_v_per_min
        self.blink_rate, self.dropout_rate = blink_rate, dropout_rate
        self.rng = np.random.default_rng(seed)
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += max(0.0, float(seconds))

    def true_offsets(self, target: Tuple[float, float]) -> Tuple[float, float]:
        """Iris offsets (eye widths) a perfect, drift-free person has when looking at `target`."""
        return self.gain_h * (target[0] - 0.5), self.gain_v * (target[1] - 0.5)

    def _frame(self, target: Tuple[float, float]) -> Optional[np.ndarray]:
        if self.rng.random() < self.dropout_rate:
            return None
        h, v = self.true_offsets(target)
        minutes = self._t / 60.0
        h += self.drift_h_per_min * minutes + self.rng.normal(0.0, self.noise)
        v += self.drift_v_per_min * minutes + self.rng.normal(0.0, self.noise)
        openness = 0.02 if self.rng.random() < self.blink_rate else 0.35
        return build_face(self.indices, h, v, self.rng.normal(0.0, self.head_wobble_deg),
                          self.rng.normal(0.0, self.head_wobble_deg), aspect=self.aspect, openness=openness)

    def capture(self, target: Tuple[float, float], n_frames: int) -> List[Tuple[float, Optional[np.ndarray]]]:
        frames = []
        for _ in range(int(n_frames)):
            self._t += 1.0 / self.fps
            frames.append((self._t, self._frame(target)))
        return frames
