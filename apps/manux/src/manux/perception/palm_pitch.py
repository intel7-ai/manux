"""Camera-relative hand elevation from the wrist and the palm's knuckles."""
from __future__ import annotations

from math import atan2, degrees, hypot


def palm_pitch(world) -> float | None:
    if world is None:
        return None
    wrist = world[0]
    x, y, z = (sum(world[i][axis] - wrist[axis] for i in (5, 9, 13, 17)) / 4
               for axis in range(3))
    if hypot(x, y, z) < 1e-4:
        return None
    return degrees(atan2(-y, hypot(x, z)))
