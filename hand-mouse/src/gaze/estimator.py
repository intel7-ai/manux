"""Gaze estimator: calibration samples -> ridge model -> smoothed screen point. Never raises.

Output `point` is in normalized screen coordinates (0..1, may fall slightly outside). It is
NOT used by the hand pointer; stage 1 only measures how accurate it is.

Reasons when `valid` is False: no-face, eye-closed, head-away, not-calibrated, bad-prediction.
`uncertainty` is the calibration RMSE (optimistic, see FitReport.train_rmse), not accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import numpy as np

from ..filters import OneEuroFilter
from .calibration import FitReport, RidgeGazeModel
from .features import DEFAULT_INDICES, FaceIndices, GazeFeatures, extract_features


@dataclass(frozen=True)
class GazeResult:
    valid: bool
    reason: str
    point: Optional[Tuple[float, float]] = None      # smoothed, normalized screen coordinates
    raw_point: Optional[Tuple[float, float]] = None  # before smoothing
    uncertainty: Optional[float] = None
    features: Optional[GazeFeatures] = None


class GazeEstimator:
    def __init__(self, aspect: float = 0.5625, indices: FaceIndices = DEFAULT_INDICES,
                 alpha: float = 1.0, degree: int = 1, min_samples: int = 30, min_targets: int = 5,
                 max_head_angle_deg: float = 35.0, min_openness: float = 0.12,
                 smooth: bool = True, min_cutoff: float = 1.0, beta: float = 2.0) -> None:
        # Defaults for max_head_angle_deg, min_openness and the filter are NOT tuned on real
        # users. They are starting points to be measured with scripts/gaze_validate.py.
        self.aspect = aspect
        self.indices = indices
        self.model = RidgeGazeModel(alpha=alpha, degree=degree)
        self.min_samples = min_samples
        self.min_targets = min_targets
        self.max_head_angle_deg = max_head_angle_deg
        self.min_openness = min_openness
        self.smooth = smooth
        self._fx = OneEuroFilter(min_cutoff=min_cutoff, beta=beta)
        self._fy = OneEuroFilter(min_cutoff=min_cutoff, beta=beta)
        self._samples: List[Tuple[np.ndarray, Tuple[float, float]]] = []
        self.report: Optional[FitReport] = None

    # ---- frame screening -------------------------------------------------------------
    def _screen(self, landmarks: Any) -> Tuple[Optional[GazeFeatures], str]:
        feats = extract_features(landmarks, self.aspect, self.indices)
        if feats is None:
            return None, "no-face"
        if feats.openness < self.min_openness:
            return feats, "eye-closed"
        p = feats.pose
        if abs(p.yaw_deg) > self.max_head_angle_deg or abs(p.pitch_deg) > self.max_head_angle_deg:
            return feats, "head-away"
        return feats, "ok"

    # ---- calibration -----------------------------------------------------------------
    def add_calibration_sample(self, landmarks: Any, target: Tuple[float, float]) -> str:
        """Add one frame looking at `target` (normalized screen). Returns 'ok' or the reject reason."""
        try:
            tx, ty = float(target[0]), float(target[1])
        except Exception:  # noqa: BLE001
            return "bad-target"
        if not (math.isfinite(tx) and math.isfinite(ty)):
            return "bad-target"
        feats, reason = self._screen(landmarks)
        if reason != "ok" or feats is None:
            return reason
        self._samples.append((feats.vector(), (tx, ty)))
        return "ok"

    @property
    def n_samples(self) -> int:
        return len(self._samples)

    def clear_calibration(self) -> None:
        self._samples.clear()
        self.model = RidgeGazeModel(alpha=self.model.alpha, degree=self.model.degree)
        self.report = None
        self.reset_filter()

    def fit(self) -> FitReport:
        if not self._samples:
            self.report = FitReport(False, "too-few-samples")
            return self.report
        x = np.stack([s[0] for s in self._samples])
        y = np.array([s[1] for s in self._samples], dtype=float)
        self.report = self.model.fit(x, y, self.min_samples, self.min_targets)
        self.reset_filter()
        return self.report

    @property
    def calibrated(self) -> bool:
        return self.model.fitted

    def reset_filter(self) -> None:
        self._fx.reset()
        self._fy.reset()

    # ---- estimation ------------------------------------------------------------------
    def estimate(self, landmarks: Any, t: float) -> GazeResult:
        feats, reason = self._screen(landmarks)
        if reason != "ok" or feats is None:
            self.reset_filter()  # do not smooth across a gap
            return GazeResult(False, reason, features=feats)
        if not self.calibrated:
            return GazeResult(False, "not-calibrated", features=feats)
        raw = self.model.predict(feats.vector())
        if raw is None:
            self.reset_filter()
            return GazeResult(False, "bad-prediction", features=feats)
        rx, ry = float(raw[0]), float(raw[1])
        if self.smooth and math.isfinite(t):
            sx, sy = self._fx(rx, t), self._fy(ry, t)
        else:
            sx, sy = rx, ry
        unc = self.report.train_rmse if self.report else None
        return GazeResult(True, "ok", (sx, sy), (rx, ry), unc, feats)
