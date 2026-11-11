"""Finger tracker around the Windows cursor (from manux-merged-20261110 `src/manux/ui/finger.py`, same behaviour).

Where the fingertips are and what the hand is doing, drawn next to the real cursor so the user does not have to look
at the camera preview. This module only describes the hand; `src/FingerTracker.qml` draws it and judges nothing.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

TIPS = (4, 8, 12, 16, 20)            # thumb, index, middle, ring, pinky
PALM = (0, 5, 9, 13, 17)
TIP_LIMIT = 4.0                      # palm lengths; a wild landmark cannot fling a dot off screen
CONTACT_RATIO = 0.35                 # thumb-to-fingertip distance / palm length that counts as a touch

# this app's action names (GestureResult.action) -> the tracker's display mode
MODES = {"move_cursor": "move", "left_click": "left", "drag_hold": "left", "drag_toggle": "left",
         "right_click": "right", "scroll": "scroll", "scroll_grab": "scroll"}


def mode_of(action: str, moving: bool = False) -> str:
    if action in MODES:
        return MODES[action]
    return "move" if moving else "idle"


def _clamp(value: float) -> float:
    return max(-TIP_LIMIT, min(TIP_LIMIT, value))


def _xy(p: Any):
    if hasattr(p, "x"):
        return float(p.x), float(p.y)
    return float(p[0]), float(p[1])


def overlay_frame(landmarks: Sequence[Any], mirrored: bool, action: str = "none", moving: bool = False
                  ) -> Optional[Dict[str, Any]]:
    """Fingertip offsets from the palm centre in palm lengths, the finger touching the thumb (1-4 into `tips`, -1 for
    none) and a display mode. The user's right is the screen's right: a mirrored preview already is, an unmirrored one
    is flipped. None when the hand is unusable."""
    if landmarks is None or len(landmarks) < 21:
        return None
    try:
        points = [_xy(p) for p in landmarks[:21]]
    except (TypeError, ValueError, IndexError, AttributeError):
        return None
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in points):
        return None
    cx = sum(points[i][0] for i in PALM) / len(PALM)
    cy = sum(points[i][1] for i in PALM) / len(PALM)
    palm = math.dist(points[0], points[9])
    if palm < 1e-6:
        return None
    flip = 1.0 if mirrored else -1.0
    tips = [[round(_clamp(flip * (points[i][0] - cx) / palm), 3), round(_clamp((points[i][1] - cy) / palm), 3)]
            for i in TIPS]
    ratio, finger = min((math.dist(points[4], points[tip]) / palm, n) for n, tip in enumerate(TIPS[1:], start=1))
    return {"tips": tips, "contact": finger if ratio < CONTACT_RATIO else -1, "mode": mode_of(action, moving)}


def preview_hand(landmarks: Sequence[Any], guides: Sequence[Any], anchor: Optional[Sequence[float]], moving: bool
                 ) -> Optional[Dict[str, Any]]:
    """What `src/HandSkeleton.qml` draws over the camera preview, normalised to the frame (0..1): the 21 joints, the
    thumb -> finger pinch lines [tip index, near|pinched|action], the cursor anchor and whether the cursor moves."""
    if landmarks is None or len(landmarks) != 21:
        return None
    tips = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
    return {
        "lm": [[round(x, 4), round(y, 4)] for x, y in (_xy(p) for p in landmarks)],
        "guides": [[tips[f], s] for f, s in guides if f in tips],
        "anchor": [round(float(anchor[0]), 4), round(float(anchor[1]), 4)] if anchor is not None else [],
        "moving": bool(moving),
    }
