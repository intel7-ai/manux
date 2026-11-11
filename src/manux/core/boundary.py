"""Camera frame boundary proximity calculation (core layer).

Computes directional proximity of the control hand to the camera boundaries (left, right, top, bottom).
Used by EdgeGlowOverlay to warn the user before hand tracking is lost due to moving outside the FOV.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


@dataclass(frozen=True)
class EdgeProximity:
    """Proximity intensity for each of the 4 screen borders [0.0 = safe, 1.0 = boundary reached]."""
    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0

    @property
    def active(self) -> bool:
        """True if any border has proximity > 0."""
        return self.left > 0.0 or self.right > 0.0 or self.top > 0.0 or self.bottom > 0.0

    @property
    def max_intensity(self) -> float:
        return max(self.left, self.right, self.top, self.bottom)


def calc_edge_proximity(
    landmarks: Optional[Sequence[Tuple[float, float, float] | Tuple[float, float]]],
    *,
    mirrored: bool = True,
    margin: float = 0.06,
) -> EdgeProximity:
    """Compute normalized proximity intensity [0.0..1.0] for left, right, top, bottom edges.

    Parameters:
    - landmarks: 21 hand landmarks (x, y, [z]) normalized in [0.0, 1.0].
    - mirrored: if True, x=0 in camera is screen right, x=1 is screen left (or vice versa depending on source).
      In MANUX, camera frames are mirrored prior to landmark tracking, so `x` directly matches screen coordinates
      when mirrored=True.
    - margin: edge distance threshold (default 0.06 / 6%). Only triggers when ANY joint gets within margin.

    Returns:
    - EdgeProximity with (left, right, top, bottom) in range [0.0, 1.0].
    """
    if landmarks is None or len(landmarks) < 21 or margin <= 0.0:
        return EdgeProximity()

    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")

    valid_count = 0
    for pt in landmarks:
        x, y = float(pt[0]), float(pt[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        valid_count += 1
        # When mirrored=True, camera x already maps to user screen x.
        # When mirrored=False, x is inverted.
        sx = x if mirrored else (1.0 - x)
        sy = y

        if sx < min_x:
            min_x = sx
        if sx > max_x:
            max_x = sx
        if sy < min_y:
            min_y = sy
        if sy > max_y:
            max_y = sy

    if valid_count < 21:
        return EdgeProximity()

    d_left = min_x
    d_right = 1.0 - max_x
    d_top = min_y
    d_bottom = 1.0 - max_y

    def _to_intensity(dist: float) -> float:
        if dist >= margin:
            return 0.0
        return max(0.0, min(1.0, (margin - dist) / margin))

    return EdgeProximity(
        left=_to_intensity(d_left),
        right=_to_intensity(d_right),
        top=_to_intensity(d_top),
        bottom=_to_intensity(d_bottom),
    )

