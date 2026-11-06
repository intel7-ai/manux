"""A synthetic hand with controllable finger bending (no real person's data).

World joints in metres like MediaPipe: wrist at the origin, palm along +y, the index side toward +x,
the palm normal along z. `curl` bends a finger toward the palm (z) joint by joint, in radians.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

# (x of the knuckle, bone lengths) for index, middle, ring, pinky
_FINGERS = {
    "index": (0.030, 0.092, (0.045, 0.025, 0.020)),
    "middle": (0.010, 0.095, (0.050, 0.030, 0.022)),
    "ring": (-0.010, 0.090, (0.045, 0.028, 0.020)),
    "pinky": (-0.030, 0.082, (0.035, 0.020, 0.018)),
}
_THUMB_BONES = (0.040, 0.032, 0.028)
NAMES = ("thumb", "index", "middle", "ring", "pinky")


def make_hand(curl: Optional[Dict[str, float]] = None, thumb_spread: float = 0.9, thumb_curl: float = 0.0
              ) -> List[List[float]]:
    """21 world joints. `curl[name]` is the bend per joint in radians (0 = straight finger)."""
    curl = curl or {}
    pts = np.zeros((21, 3))
    for name, base in zip(NAMES[1:], (5, 9, 13, 17)):
        x, y, bones = _FINGERS[name]
        bend = float(curl.get(name, 0.0))
        pts[base] = (x, y, 0.0)
        pos = np.array([x, y, 0.0])
        angle = 0.0
        for k, length in enumerate(bones):
            angle += bend
            pos = pos + length * np.array([0.0, math.cos(angle), math.sin(angle)])
            pts[base + 1 + k] = pos
    # thumb: carpal joint at the side of the wrist, bones spread away from the index finger
    pos = np.array([0.020, 0.025, 0.0])
    pts[1] = pos
    direction_angle = thumb_spread           # from the +y axis toward +x
    bend = float(curl.get("thumb", thumb_curl))
    angle = 0.0
    for k, length in enumerate(_THUMB_BONES):
        angle += bend
        step = np.array([math.sin(direction_angle) * math.cos(angle), math.cos(direction_angle) * math.cos(angle),
                         math.sin(angle)])
        pos = pos + length * step
        pts[2 + k] = pos
    return [[round(float(v), 6) for v in row] for row in pts]


def rotation(rx: float, ry: float, rz: float) -> np.ndarray:
    cx, cy, cz = math.cos(rx), math.cos(ry), math.cos(rz)
    sx, sy, sz = math.sin(rx), math.sin(ry), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def move(world: Sequence[Sequence[float]], rot: Optional[np.ndarray] = None, scale: float = 1.0,
         shift: Sequence[float] = (0.0, 0.0, 0.0)) -> List[List[float]]:
    """The same hand rotated, scaled and moved as a whole (a rigid motion, not a change of pose)."""
    pts = np.asarray(world, dtype=np.float64)
    if rot is not None:
        pts = pts @ rot.T
    return (pts * scale + np.asarray(shift)).tolist()


def mirror(world: Sequence[Sequence[float]]) -> List[List[float]]:
    """The mirror image (a left hand when the input is a right hand)."""
    pts = np.asarray(world, dtype=np.float64).copy()
    pts[:, 0] *= -1.0
    return pts.tolist()
