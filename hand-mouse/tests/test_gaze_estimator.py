"""GazeEstimator: calibration flow, valid:False paths, smoothing, hostile input."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gaze.estimator import GazeEstimator  # noqa: E402
from src.gaze.protocol import CALIBRATION_POINTS  # noqa: E402
from src.gaze.synthetic import SyntheticSource, build_face  # noqa: E402


def _calibrate(est, source, frames=15):
    rejected = {}
    for target in CALIBRATION_POINTS:
        for _t, lm in source.capture(target, frames):
            r = est.add_calibration_sample(lm, target)
            if r != "ok":
                rejected[r] = rejected.get(r, 0) + 1
    return est.fit(), rejected


def test_calibrated_estimator_recovers_the_target():
    src = SyntheticSource(noise=0.0)
    est = GazeEstimator(alpha=1e-8, smooth=False)
    report, rejected = _calibrate(est, src)
    assert report.ok and not rejected and report.n_targets == 9 and est.calibrated
    for target in [(0.3, 0.3), (0.7, 0.7), (0.5, 0.5)]:
        t, lm = src.capture(target, 1)[0]
        res = est.estimate(lm, t)
        assert res.valid and res.reason == "ok"
        assert res.point == pytest.approx(target, abs=1e-6)
        assert res.uncertainty is not None and res.uncertainty < 1e-6


def test_not_calibrated_and_garbage_input_never_raise():
    est = GazeEstimator()
    face = build_face()
    assert est.estimate(face, 0.0).reason == "not-calibrated"
    assert est.estimate(None, 0.0).reason == "no-face"
    for bad in ("x", 3, [], np.zeros((478, 3)), np.full((478, 3), np.nan), [[1, 2]]):
        res = est.estimate(bad, 0.0)
        assert not res.valid and res.point is None
    assert est.add_calibration_sample(face, (float("nan"), 0.5)) == "bad-target"
    assert est.add_calibration_sample(face, "oops") == "bad-target"
    assert est.add_calibration_sample(None, (0.5, 0.5)) == "no-face"


def test_invalid_frames_are_named_and_never_enter_calibration():
    est = GazeEstimator()
    assert est.add_calibration_sample(build_face(openness=0.02), (0.5, 0.5)) == "eye-closed"
    assert est.add_calibration_sample(build_face(yaw_deg=50), (0.5, 0.5)) == "head-away"
    assert est.add_calibration_sample(build_face(pitch_deg=-50), (0.5, 0.5)) == "head-away"
    assert est.add_calibration_sample(build_face(), (0.5, 0.5)) == "ok"
    assert est.n_samples == 1


def test_too_little_calibration_leaves_estimator_uncalibrated():
    est = GazeEstimator()
    src = SyntheticSource()
    for lm in (src.capture((0.5, 0.5), 10)):
        est.add_calibration_sample(lm[1], (0.5, 0.5))
    assert not est.fit().ok and not est.calibrated
    res = est.estimate(build_face(), 0.0)
    assert not res.valid and res.reason == "not-calibrated"
    assert not GazeEstimator().fit().ok  # no samples at all


def test_estimate_marks_closed_eye_and_head_away_as_invalid_after_calibration():
    src = SyntheticSource()
    est = GazeEstimator(alpha=1e-8)
    assert _calibrate(est, src)[0].ok
    blink = est.estimate(build_face(openness=0.02), 1.0)
    away = est.estimate(build_face(yaw_deg=60), 1.1)
    assert (blink.valid, blink.reason) == (False, "eye-closed")
    assert (away.valid, away.reason) == (False, "head-away")


def test_smoothing_reduces_jitter_and_is_reset_after_a_gap():
    src = SyntheticSource(noise=0.02, seed=5)
    est = GazeEstimator(alpha=1.0)
    assert _calibrate(est, src, frames=25)[0].ok
    est.reset_filter()
    raw, smooth = [], []
    for t, lm in src.capture((0.5, 0.5), 120)[20:]:
        res = est.estimate(lm, t)
        if res.valid:
            raw.append(res.raw_point[0])
            smooth.append(res.point[0])
    assert len(raw) > 60
    assert np.std(smooth) < 0.6 * np.std(raw)
    # a lost face must not smear the next estimate: the first valid frame after a gap is unfiltered
    est.estimate(None, 10.0)
    t, lm = src.capture((0.9, 0.9), 1)[0]
    after = est.estimate(lm, 11.0)
    assert after.valid and after.point == after.raw_point


def test_non_finite_time_disables_smoothing_instead_of_raising():
    src = SyntheticSource(noise=0.0)
    est = GazeEstimator(alpha=1e-8)
    assert _calibrate(est, src)[0].ok
    _t, lm = src.capture((0.4, 0.6), 1)[0]
    res = est.estimate(lm, float("nan"))
    assert res.valid and res.point == res.raw_point


def test_clear_calibration_resets_everything():
    src = SyntheticSource()
    est = GazeEstimator()
    assert _calibrate(est, src)[0].ok
    est.clear_calibration()
    assert not est.calibrated and est.n_samples == 0 and est.report is None
    assert est.estimate(build_face(), 0.0).reason == "not-calibrated"
