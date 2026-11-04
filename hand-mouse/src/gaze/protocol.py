"""Measurement protocol: 9-point calibration, separate validation points, optional drift check.

`run_session` works on any frame source with `capture(target, n_frames)`, `advance(seconds)` and
`now()`: the synthetic source in tests/`--synthetic`, or the camera source in
scripts/gaze_validate.py. The report holds targets and statistics only. It never contains
per-frame gaze points, landmarks or images (privacy rule in docs/gaze-experiment.md).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .estimator import GazeEstimator
from .metrics import ScreenSpec, summarize_errors

Point = Tuple[float, float]

CALIBRATION_POINTS: Tuple[Point, ...] = tuple((x, y) for y in (0.1, 0.5, 0.9) for x in (0.1, 0.5, 0.9))
# Different from every calibration point, so calibration error is not reported as accuracy.
VALIDATION_POINTS: Tuple[Point, ...] = ((0.3, 0.3), (0.7, 0.3), (0.4, 0.5), (0.3, 0.7), (0.7, 0.7))


def _measure(source: Any, estimator: GazeEstimator, screen: ScreenSpec, points: Sequence[Point],
             frames_per_point: int, settle_frames: int) -> Dict[str, Any]:
    per_point: List[Dict[str, Any]] = []
    all_deg: List[float] = []
    all_px: List[float] = []
    total = 0
    for target in points:
        estimator.reset_filter()
        deg: List[float] = []
        px: List[float] = []
        measured = 0
        for i, (t, lm) in enumerate(source.capture(target, settle_frames + frames_per_point)):
            res = estimator.estimate(lm, t)  # settle frames also feed the smoothing filter
            if i < settle_frames:
                continue
            measured += 1
            if res.valid and res.point is not None:
                deg.append(screen.error_deg(res.point, target))
                px.append(screen.error_px(res.point, target))
        total += measured
        all_deg += deg
        all_px += px
        per_point.append({
            "target": [target[0], target[1]], "frames": measured,
            "valid_rate": (len(deg) / measured) if measured else 0.0,
            "median_error_deg": float(np.median(deg)) if deg else None,
            "median_error_px": float(np.median(px)) if px else None,
        })
    return {"per_point": per_point, "error_deg": summarize_errors(all_deg, total),
            "error_px": summarize_errors(all_px, total)}


def run_session(source: Any, screen: ScreenSpec, estimator: GazeEstimator, frames_per_point: int = 15,
                settle_frames: int = 5, drift_minutes: float = 0.0, drift_interval_s: float = 60.0,
                calibration_points: Sequence[Point] = CALIBRATION_POINTS,
                validation_points: Sequence[Point] = VALIDATION_POINTS) -> Dict[str, Any]:
    rejected: Dict[str, int] = {}
    for target in calibration_points:
        for i, (_t, lm) in enumerate(source.capture(target, settle_frames + frames_per_point)):
            if i < settle_frames:
                continue
            reason = estimator.add_calibration_sample(lm, target)
            if reason != "ok":
                rejected[reason] = rejected.get(reason, 0) + 1
    fit = estimator.fit()
    report: Dict[str, Any] = {
        "screen": asdict(screen),
        "settings": {"frames_per_point": frames_per_point, "settle_frames": settle_frames,
                     "calibration_points": len(calibration_points), "validation_points": len(validation_points)},
        "calibration": {**asdict(fit), "rejected_frames": rejected},
        "validation": None, "drift": [],
        "notes": ["Calibration RMSE (train_rmse) is not accuracy. Accuracy is the validation error below.",
                  "Statistics only: no per-frame gaze points, landmarks or images are stored."],
    }
    if not fit.ok:
        return report
    t_start = source.now()
    report["validation"] = _measure(source, estimator, screen, validation_points, frames_per_point, settle_frames)
    if drift_minutes and drift_minutes > 0:
        drift: List[Dict[str, Any]] = [{"t_s": 0.0, "median_error_deg": report["validation"]["error_deg"]["median"],
                                        "median_error_px": report["validation"]["error_px"]["median"],
                                        "valid_rate": report["validation"]["error_deg"]["valid_rate"]}]
        while source.now() - t_start < drift_minutes * 60.0:
            source.advance(max(1.0, drift_interval_s))
            m = _measure(source, estimator, screen, validation_points, frames_per_point, settle_frames)
            drift.append({"t_s": source.now() - t_start, "median_error_deg": m["error_deg"]["median"],
                          "median_error_px": m["error_px"]["median"], "valid_rate": m["error_deg"]["valid_rate"]})
        report["drift"] = drift
        pts = [(d["t_s"] / 60.0, d["median_error_deg"]) for d in drift if d["median_error_deg"] is not None]
        if len(pts) >= 3:
            slope = float(np.polyfit([p[0] for p in pts], [p[1] for p in pts], 1)[0])
            report["drift_slope_deg_per_min"] = slope
    return report
