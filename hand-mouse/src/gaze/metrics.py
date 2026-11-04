"""Screen geometry and error statistics for gaze measurement. Pure Python/numpy.

Conversion used by the measurement tool (flat screen, viewer facing its center):
  1 degree = viewing_distance_cm * tan(1 deg)            [cm on screen]
  px per cm = pixels / physical size (per axis)
  error_deg = atan(error_cm / viewing_distance_cm)
Near the screen center this matches the 1 deg = distance * tan(1 deg) rule; at the edges the
true angle is a bit smaller than this flat approximation says, so errors there are slightly
overstated. The tool takes the screen size and the distance from the person running it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class ScreenSpec:
    width_px: int = 1920
    height_px: int = 1080
    width_cm: float = 53.1       # 24-inch 16:9 panel (assumption used in the research notes)
    height_cm: float = 29.9
    distance_cm: float = 60.0

    def __post_init__(self) -> None:
        for name in ("width_px", "height_px", "width_cm", "height_cm", "distance_cm"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be a positive number")

    @property
    def px_per_cm_x(self) -> float:
        return self.width_px / self.width_cm

    @property
    def px_per_cm_y(self) -> float:
        return self.height_px / self.height_cm

    @property
    def cm_per_degree(self) -> float:
        return self.distance_cm * math.tan(math.radians(1.0))

    @property
    def px_per_degree(self) -> float:
        """Approximate pixels for one degree near the screen center (mean of both axes)."""
        return self.cm_per_degree * (self.px_per_cm_x + self.px_per_cm_y) / 2.0

    def norm_to_px(self, point: Tuple[float, float]) -> Tuple[float, float]:
        return point[0] * self.width_px, point[1] * self.height_px

    def error_px(self, predicted_norm: Tuple[float, float], target_norm: Tuple[float, float]) -> float:
        dx = (predicted_norm[0] - target_norm[0]) * self.width_px
        dy = (predicted_norm[1] - target_norm[1]) * self.height_px
        return math.hypot(dx, dy)

    def error_deg(self, predicted_norm: Tuple[float, float], target_norm: Tuple[float, float]) -> float:
        dx_cm = (predicted_norm[0] - target_norm[0]) * self.width_cm
        dy_cm = (predicted_norm[1] - target_norm[1]) * self.height_cm
        return math.degrees(math.atan(math.hypot(dx_cm, dy_cm) / self.distance_cm))


def summarize_errors(errors: Iterable[float], total_frames: Optional[int] = None) -> Dict[str, Optional[float]]:
    """Median/mean/p90/max of finite errors plus the valid rate (valid / total frames)."""
    vals = np.array([e for e in errors if e is not None and math.isfinite(e)], dtype=float)
    total = int(total_frames) if total_frames is not None else int(vals.size)
    rate = float(vals.size / total) if total > 0 else 0.0
    if vals.size == 0:
        return {"n": 0, "valid_rate": rate, "median": None, "mean": None, "p90": None, "max": None}
    return {
        "n": int(vals.size),
        "valid_rate": rate,
        "median": float(np.median(vals)),
        "mean": float(vals.mean()),
        "p90": float(np.percentile(vals, 90)),
        "max": float(vals.max()),
    }
