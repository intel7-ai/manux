"""Camera-relative hand elevation from the wrist and the palm's knuckles.

The angle is measured in the camera's vertical plane (world y and z): 0° points the fingers at the
camera, +90° straight up, -90° straight down. Unlike an elevation angle (`atan2(-y, hypot(x, z))`) it
does not fold back at vertical, so tilting the hand back past upright keeps increasing instead of
reversing. It only wraps when the fingers point away from the camera, which a facing user does not do.
"""
from __future__ import annotations

from math import atan2, degrees, hypot

# The vertical-plane part of the wrist→knuckle vector must be at least this share of its length,
# otherwise the fingers point sideways and the plane angle is mostly landmark noise.
MIN_PLANE_SHARE = .35


def palm_pitch(world) -> float | None:
    if world is None:
        return None
    wrist = world[0]
    x, y, z = (sum(world[i][axis] - wrist[axis] for i in (5, 9, 13, 17)) / 4
               for axis in range(3))
    length = hypot(x, y, z)
    if length < 1e-4 or hypot(y, z) < MIN_PLANE_SHARE * length:
        return None
    return degrees(atan2(-y, -z))
