"""Ridge regression from gaze features to normalized screen coordinates. numpy only.

`fit` never raises: it returns a `FitReport` with ok=False and a reason instead. Constant
feature columns (for example head yaw when the user kept the head still) are dropped, so a
calibration done with a still head still works, but head movement afterwards is then not
compensated (documented limitation).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations_with_replacement
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class FitReport:
    ok: bool
    reason: str
    n_samples: int = 0
    n_targets: int = 0
    n_terms: int = 0
    # Error on the calibration samples themselves, in normalized screen units. This is NOT
    # accuracy: it is optimistic, because the same samples were used to fit.
    train_rmse: Optional[float] = None


class RidgeGazeModel:
    def __init__(self, alpha: float = 1.0, degree: int = 1) -> None:
        self.alpha = max(0.0, float(alpha))
        self.degree = 2 if int(degree) >= 2 else 1
        self._keep: Optional[np.ndarray] = None
        self._mean: Optional[np.ndarray] = None
        self._std: Optional[np.ndarray] = None
        self._w: Optional[np.ndarray] = None  # (terms + 1, 2), first row = intercept

    @property
    def fitted(self) -> bool:
        return self._w is not None

    def _expand(self, z: np.ndarray) -> np.ndarray:
        cols = [z]
        if self.degree >= 2 and z.shape[1] >= 1:
            pairs = [z[:, i] * z[:, j] for i, j in combinations_with_replacement(range(z.shape[1]), 2)]
            cols.append(np.stack(pairs, axis=1))
        return np.concatenate(cols, axis=1)

    def fit(self, features: np.ndarray, targets: np.ndarray, min_samples: int = 30,
            min_targets: int = 5) -> FitReport:
        self._w = None
        try:
            x = np.asarray(features, dtype=float)
            y = np.asarray(targets, dtype=float)
        except Exception:  # noqa: BLE001
            return FitReport(False, "bad-input")
        if x.ndim != 2 or y.ndim != 2 or y.shape[1] != 2 or x.shape[0] != y.shape[0] or x.shape[0] == 0:
            return FitReport(False, "bad-input")
        ok_rows = np.isfinite(x).all(axis=1) & np.isfinite(y).all(axis=1)
        x, y = x[ok_rows], y[ok_rows]
        n = x.shape[0]
        n_targets = len({(round(float(a), 6), round(float(b), 6)) for a, b in y})
        if n < min_samples:
            return FitReport(False, "too-few-samples", n, n_targets)
        if n_targets < min_targets:
            return FitReport(False, "too-few-targets", n, n_targets)
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        keep = std > 1e-6
        if not keep.any():
            return FitReport(False, "degenerate-features", n, n_targets)
        z = (x[:, keep] - mean[keep]) / std[keep]
        design = self._expand(z)
        terms = design.shape[1]
        if n < terms + 2:
            return FitReport(False, "too-few-samples", n, n_targets, terms)
        a = np.concatenate([np.ones((n, 1)), design], axis=1)
        reg = np.eye(terms + 1) * self.alpha
        reg[0, 0] = 0.0  # intercept is not penalized
        try:
            w = np.linalg.solve(a.T @ a + reg + np.eye(terms + 1) * 1e-12, a.T @ y)
        except np.linalg.LinAlgError:
            return FitReport(False, "singular", n, n_targets, terms)
        if not np.isfinite(w).all():
            return FitReport(False, "singular", n, n_targets, terms)
        self._keep, self._mean, self._std, self._w = keep, mean, std, w
        rmse = float(np.sqrt(np.mean(np.sum((a @ w - y) ** 2, axis=1))))
        return FitReport(True, "ok", n, n_targets, terms, rmse)

    def predict(self, feature: np.ndarray) -> Optional[np.ndarray]:
        if self._w is None or self._keep is None or self._mean is None or self._std is None:
            return None
        try:
            x = np.asarray(feature, dtype=float).reshape(1, -1)
        except Exception:  # noqa: BLE001
            return None
        if x.shape[1] != self._keep.shape[0] or not np.isfinite(x).all():
            return None
        z = (x[:, self._keep] - self._mean[self._keep]) / self._std[self._keep]
        a = np.concatenate([np.ones((1, 1)), self._expand(z)], axis=1)
        out = (a @ self._w)[0]
        return out if np.isfinite(out).all() else None
