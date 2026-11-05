"""Finger tracker around the Windows cursor (port of hand-mouse/src/finger_overlay.py, same behaviour).

Where the fingertips are and what the hand is doing, drawn next to the real cursor so the user does
not have to look at the camera preview. This module only describes the hand; dashboard.qml draws it.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional, Sequence

TIPS = (4, 8, 12, 16, 20)            # thumb, index, middle, ring, pinky
PALM = (0, 5, 9, 13, 17)
TIP_LIMIT = 4.0                      # palm lengths; a wild landmark cannot fling a dot off screen
CONTACT_RATIO = 0.35                 # thumb-to-fingertip distance / palm length that counts as a touch

MODES = {"mouse.move": "move", "mouse.left_click": "left", "mouse.drag": "left",
         "mouse.right_click": "right", "mouse.scroll": "scroll"}


def mode_of(actions: Iterable[str]) -> str:
    for action in actions:
        if action in MODES:
            return MODES[action]
    return "idle"


def _clamp(value: float) -> float:
    return max(-TIP_LIMIT, min(TIP_LIMIT, value))


def overlay_frame(landmarks: Sequence[Sequence[float]], mirrored: bool, actions: Iterable[str] = ()
                  ) -> Optional[Dict[str, Any]]:
    """Fingertip offsets from the palm centre in palm lengths, the finger touching the thumb (1-4 into
    `tips`, -1 for none) and a display mode. The user's right is the screen's right: a mirrored
    preview already is, an unmirrored one is flipped. None when the hand is unusable."""
    if len(landmarks) < 21:
        return None
    try:
        points = [(float(p[0]), float(p[1])) for p in landmarks[:21]]
    except (TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in points):
        return None
    cx = sum(points[i][0] for i in PALM) / len(PALM)
    cy = sum(points[i][1] for i in PALM) / len(PALM)
    palm = math.dist(points[0], points[9])
    if palm < 1e-6:
        return None
    flip = 1.0 if mirrored else -1.0
    tips = [[_clamp(flip * (points[i][0] - cx) / palm), _clamp((points[i][1] - cy) / palm)] for i in TIPS]
    ratio, finger = min((math.dist(points[4], points[tip]) / palm, n) for n, tip in enumerate(TIPS[1:], start=1))
    return {"tips": tips, "contact": finger if ratio < CONTACT_RATIO else -1, "mode": mode_of(actions)}
