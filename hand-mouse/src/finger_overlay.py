"""
Finger tracker around the Windows cursor (2026-11-04): where the fingertips are and what the hand
is doing, drawn next to the real cursor so the user does not have to look at the camera preview.

Ported from the Electron overlay (src/overlay-frame.js), which adapted park's transparent HUD
(park branch, 11/03). This module only describes the hand; dashboard.qml draws it.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

TIPS = (4, 8, 12, 16, 20)            # thumb, index, middle, ring, pinky
PALM = (0, 5, 9, 13, 17)
TIP_LIMIT = 4.0                      # palm lengths; a wild landmark cannot fling a dot off screen
CONTACT_RATIO = 0.35                 # thumb-to-fingertip distance / palm length that counts as a touch

MODES = {
    "move_cursor": "move", "grab": "move",
    "left_click": "left", "drag_hold": "left", "drag_toggle": "left",
    "right_click": "right",
    "scroll_grab": "scroll",
    "none": "idle",
}


def _clamp(value: float) -> float:
    return max(-TIP_LIMIT, min(TIP_LIMIT, value))


def overlay_frame(landmarks: Sequence[Any], mirrored: bool, action: str = "none") -> Optional[Dict[str, Any]]:
    """Fingertip offsets from the palm centre in palm lengths, the finger touching the thumb (index
    1-4 into `tips`, -1 for none) and a display mode. The user's right is the screen's right: a
    mirrored preview already is, an unmirrored one is flipped. None when the hand is unusable."""
    if len(landmarks) < 21:
        return None
    try:
        points = [(float(p.x), float(p.y)) for p in landmarks[:21]]
    except (AttributeError, TypeError, ValueError):
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
    # Only the finger nearest the thumb counts: a curled finger resting beside it is not a touch.
    ratio, finger = min((math.dist(points[4], points[tip]) / palm, n) for n, tip in enumerate(TIPS[1:], start=1))
    return {
        "tips": tips,
        "contact": finger if ratio < CONTACT_RATIO else -1,
        "mode": MODES.get(action, "other"),
    }
