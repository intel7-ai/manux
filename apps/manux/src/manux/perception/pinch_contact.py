"""Size-independent fingertip contact evidence from world landmarks."""

import math
from typing import Mapping, Sequence

Pt = tuple[float, float, float]


def world_tip_distances(world: Sequence[Pt] | None) -> dict[str, float] | None:
    if world is None or len(world) != 21:
        return None
    if any(len(point) != 3 or not all(math.isfinite(value) for value in point) for point in world):
        return None
    scale = math.dist(world[0], world[9])
    if scale <= 1e-6:
        return None
    return {name: math.dist(world[4], world[index]) / scale
            for name, index in (("index", 8), ("middle", 12), ("ring", 16), ("pinky", 20))}


def clear_pinky_contact(distances: Mapping[str, float], neighbor_ratio: float = .75) -> bool:
    pinky = distances["pinky"]
    other = min(distances["index"], distances["middle"], distances["ring"])
    return pinky <= .30 and pinky < other * min(neighbor_ratio, .75) and other - pinky >= .05
