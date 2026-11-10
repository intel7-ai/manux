"""Geometry for Dan's cursor hand (hand-mouse@49dfaea, src/virtual_hand.py).

Only the display is normalized. The engine still decides cursor movement and gestures.
"""
from __future__ import annotations

import math
from typing import Sequence

SIZE_PX = 110.0  # wrist to middle knuckle, in logical display pixels
PALM = (0, 5, 9, 13, 17)
TIPS = (8, 12, 16, 20)
OFFSET_LIMIT = 304.0  # keep projections of an edge-on hand inside the overlay


def overlay_frame(
    landmarks: Sequence[Sequence[float]],
    mirrored: bool,
    aspect: float,
    guides: Sequence[tuple[int, str]] = (),
) -> dict | None:
    """Project the 21 joints into logical pixels around the palm, like Dan's hand view."""
    if len(landmarks) != 21 or not math.isfinite(aspect) or aspect <= 0:
        return None
    flip = 1 if mirrored else -1
    points = [(flip * p[0], aspect * p[1]) for p in landmarks]
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in points):
        return None
    size = math.dist(points[0], points[9])
    if size < 1e-6:
        return None
    cx = sum(points[i][0] for i in PALM) / len(PALM)
    cy = sum(points[i][1] for i in PALM) / len(PALM)
    scale = SIZE_PX / size
    return {
        "pts": [
            round(max(-OFFSET_LIMIT, min(OFFSET_LIMIT, offset * scale)), 1)
            for x, y in points
            for offset in (x - cx, y - cy)
        ],
        "guides": [[tip, state] for tip, state in guides if tip in TIPS],
    }
