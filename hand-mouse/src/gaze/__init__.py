"""Webcam gaze experiment, stage 1 (see docs/gaze-experiment.md).

Pure logic only (numpy + the existing OneEuroFilter): no MediaPipe, OpenCV or Qt import here,
so every module is testable without a camera or a model. Nothing in this package is wired into
the hand pointer. The hand baseline is unchanged.
"""

from __future__ import annotations

from .attention import AttentionGate
from .calibration import FitReport, RidgeGazeModel
from .estimator import GazeEstimator, GazeResult
from .features import FaceIndices, GazeFeatures, HeadPose, extract_features, head_pose_from_landmarks
from .metrics import ScreenSpec, summarize_errors

__all__ = [
    "AttentionGate",
    "FaceIndices",
    "FitReport",
    "GazeEstimator",
    "GazeFeatures",
    "GazeResult",
    "HeadPose",
    "RidgeGazeModel",
    "ScreenSpec",
    "extract_features",
    "head_pose_from_landmarks",
    "summarize_errors",
]
