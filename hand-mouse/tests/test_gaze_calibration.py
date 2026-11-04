"""Ridge calibration: known mappings are recovered, bad input fails softly."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gaze.calibration import RidgeGazeModel  # noqa: E402

GRID = np.array([(x, y) for y in (0.1, 0.5, 0.9) for x in (0.1, 0.5, 0.9)], dtype=float)


def _samples(n_per_point=12, seed=3, cols=6):
    rng = np.random.default_rng(seed)
    targets = np.repeat(GRID, n_per_point, axis=0)
    x = rng.normal(0.0, 1.0, size=(targets.shape[0], cols))
    return x, targets, rng


def test_linear_mapping_is_recovered():
    x, _t, _ = _samples()
    y = np.stack([0.5 + 0.2 * x[:, 0] - 0.1 * x[:, 1], 0.5 + 0.15 * x[:, 1] + 0.05 * x[:, 2]], axis=1)
    # Targets here are the mapping itself; unique-target check needs >=5 distinct values, which holds.
    m = RidgeGazeModel(alpha=1e-8, degree=1)
    report = m.fit(x, y, min_samples=30, min_targets=5)
    assert report.ok and report.train_rmse < 1e-6
    probe = np.array([0.3, -0.4, 1.0, 0.0, 0.2, -0.1])
    want = [0.5 + 0.2 * 0.3 - 0.1 * -0.4, 0.5 + 0.15 * -0.4 + 0.05 * 1.0]
    assert np.allclose(m.predict(probe), want, atol=1e-6)


def test_quadratic_mapping_needs_degree_two():
    x, _t, _ = _samples(n_per_point=40, cols=3)
    y = np.stack([0.5 + 0.3 * x[:, 0] ** 2 + 0.1 * x[:, 0] * x[:, 1], 0.5 + 0.2 * x[:, 1] ** 2], axis=1)
    lin, quad = RidgeGazeModel(alpha=1e-8, degree=1), RidgeGazeModel(alpha=1e-8, degree=2)
    r1, r2 = lin.fit(x, y), quad.fit(x, y)
    assert r1.ok and r2.ok
    assert r2.train_rmse < 1e-6 < r1.train_rmse
    assert r2.n_terms > r1.n_terms


def test_constant_column_is_dropped_but_fit_works():
    x, _t, _ = _samples(cols=3)
    x[:, 2] = 0.25  # head never moved during calibration
    y = np.stack([0.5 + 0.2 * x[:, 0], 0.5 + 0.2 * x[:, 1]], axis=1)
    m = RidgeGazeModel(alpha=1e-8)
    assert m.fit(x, y).ok
    assert np.allclose(m.predict(np.array([0.1, 0.2, 9.0])), [0.52, 0.54], atol=1e-6)  # const col ignored


def test_failure_reasons_and_no_exceptions():
    x, t, _ = _samples()
    y = t
    assert RidgeGazeModel().fit(x[:10], y[:10]).reason == "too-few-samples"
    assert RidgeGazeModel().fit(x, np.tile(y[:1], (len(y), 1))).reason == "too-few-targets"
    assert RidgeGazeModel().fit(np.zeros_like(x), y).reason == "degenerate-features"
    assert RidgeGazeModel().fit(x, y[:, :1]).reason == "bad-input"
    assert RidgeGazeModel().fit(x[:5], y).reason == "bad-input"
    assert RidgeGazeModel().fit(np.zeros((0, 6)), np.zeros((0, 2))).reason == "bad-input"
    assert RidgeGazeModel().fit("abc", y).reason == "bad-input"
    x_bad = x.copy()
    x_bad[0, 0] = np.nan
    rep = RidgeGazeModel().fit(x_bad, y)  # the NaN row is dropped, the rest still fits
    assert rep.ok and rep.n_samples == len(y) - 1
    # degree 2 over 6 features has 27 terms: 20 samples must fail softly instead of overfitting silently
    few = RidgeGazeModel(degree=2).fit(x[::5][:20], y[::5][:20], min_samples=10, min_targets=5)
    assert not few.ok and few.reason == "too-few-samples" and few.n_terms == 27


def test_predict_guards():
    m = RidgeGazeModel()
    assert m.predict(np.zeros(6)) is None  # not fitted
    x, t, _ = _samples()
    assert m.fit(x, t).ok
    assert m.predict(np.zeros(5)) is None  # wrong width
    assert m.predict(np.array([np.nan] * 6)) is None
    assert m.predict("bad") is None
    assert m.predict(np.zeros(6)) is not None


def test_refit_failure_clears_previous_model():
    x, t, _ = _samples()
    m = RidgeGazeModel()
    assert m.fit(x, t).ok and m.fitted
    assert not m.fit(x[:3], t[:3]).ok
    assert not m.fitted and m.predict(np.zeros(6)) is None
