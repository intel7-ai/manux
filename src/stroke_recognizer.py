"""Shapes drawn in the drawing mode -> which registered shape (2026-11-10).

A template matcher in the manner of the $1 unistroke recognizer (Wobbrock, Wilson, Li 2007), changed where hand drawing
needs it:
- uniform scaling (not to a square), so a flat zigzag or a line keeps its shape;
- only small rotations are tried (+-ROTATE): an arrow up and an arrow right are different commands;
- both drawing directions are compared, so a circle drawn either way matches one example.
One example is enough to register a shape; more examples (drawn a little differently) make it surer.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
N = 64                   # points after resampling
ROTATE = 20.0            # degrees tried each way
ROTATE_STEP = 5.0
HALF_DIAG = 0.5 * math.sqrt(2.0)   # the largest mean distance between two strokes in the unit box


def path_length(pts: Sequence[Point]) -> float:
    return sum(math.dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def resample(pts: Sequence[Point], n: int = N) -> List[Point]:
    pts = [tuple(p) for p in pts]
    if len(pts) < 2:
        return [pts[0]] * n if pts else []
    step = path_length(pts) / (n - 1)
    if step <= 0:
        return [pts[0]] * n
    out: List[Point] = [pts[0]]
    acc = 0.0
    i = 1
    prev = pts[0]
    while i < len(pts):
        d = math.dist(prev, pts[i])
        if acc + d >= step and d > 0:
            t = (step - acc) / d
            q = (prev[0] + t * (pts[i][0] - prev[0]), prev[1] + t * (pts[i][1] - prev[1]))
            out.append(q)
            prev, acc = q, 0.0
        else:
            acc += d
            prev = pts[i]
            i += 1
    while len(out) < n:
        out.append(pts[-1])
    return out[:n]


def normalize(pts: Sequence[Point]) -> List[Point]:
    """Resampled, centred on the centroid, the larger side of the bounding box = 1."""
    r = resample(pts)
    if not r:
        return []
    xs, ys = [p[0] for p in r], [p[1] for p in r]
    side = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    cx, cy = sum(xs) / len(r), sum(ys) / len(r)
    return [((x - cx) / side, (y - cy) / side) for x, y in r]


def _rotate(pts: Sequence[Point], deg: float) -> List[Point]:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


CLOSED = .2              # start-end gap (in box sides) under which a stroke is a closed shape
SHIFTS = 16              # start points tried around a closed shape


def is_closed(pts: Sequence[Point]) -> bool:
    return len(pts) > 2 and math.dist(pts[0], pts[-1]) < CLOSED


def starts(pts: Sequence[Point]) -> List[List[Point]]:
    """A closed shape can be begun anywhere: the same loop from SHIFTS start points (an open stroke: itself)."""
    if not is_closed(pts):
        return [list(pts)]
    loop = list(pts[:-1])
    n = len(loop)
    out = []
    for k in range(SHIFTS):
        i = k * n // SHIFTS
        shifted = loop[i:] + loop[:i]
        out.append(shifted + [shifted[0]])
    return out


DIRECTION_WEIGHT = .5    # share of the direction-of-travel term: position alone cannot tell a square from a circle
DIRECTION_SCALE = 6      # the direction gap (0..1, typically .01-.05) on the position distance's scale


CHORD = 3                # directions over this many points: steady against hand jitter


def _dirs(pts: Sequence[Point]) -> List[Point]:
    out = []
    for p, q in zip(pts, pts[CHORD:]):
        d = math.dist(p, q) or 1.0
        out.append(((q[0] - p[0]) / d, (q[1] - p[1]) / d))
    return out


def _direction_gap(a: Sequence[Point], b: Sequence[Point]) -> float:
    """0 (the same direction all along) .. 1 (opposite): (1 - cos) / 2 averaged over the segments."""
    da, db = _dirs(a), _dirs(b)
    return sum((1 - (u[0] * v[0] + u[1] * v[1])) / 2 for u, v in zip(da, db)) / max(1, len(da))


def distance(a: Sequence[Point], b: Sequence[Point]) -> float:
    """How far two normalised strokes are (0 = the same; on the scale of HALF_DIAG): point positions and the direction
    of travel, the best over small rotations, both drawing directions and (closed shapes) the start point."""
    best = float("inf")
    for cand in (b, list(reversed(b))):
        for a2 in starts(a):
            deg = -ROTATE
            while deg <= ROTATE + 1e-9:
                r = _rotate(a2, deg)
                pos = sum(math.dist(p, q) for p, q in zip(r, cand)) / len(r)
                best = min(best, (1 - DIRECTION_WEIGHT) * pos + DIRECTION_WEIGHT * DIRECTION_SCALE * HALF_DIAG * _direction_gap(r, cand))
                deg += ROTATE_STEP
    return best


def score(d: float) -> float:
    return max(0.0, 1.0 - d / HALF_DIAG)


def recognize(stroke: Sequence[Point], templates: Dict[str, Sequence[Sequence[Point]]]
              ) -> Tuple[Optional[str], float, Optional[str], float]:
    """(best key, its score, runner-up key, its score). Templates: key -> examples (normalised strokes)."""
    s = normalize(stroke)
    scored: List[Tuple[float, str]] = []
    for key, examples in templates.items():
        if examples:
            scored.append((max(score(distance(s, e)) for e in examples), key))
    scored.sort(reverse=True)
    if not scored:
        return None, 0.0, None, 0.0
    second = scored[1] if len(scored) > 1 else (0.0, None)
    return scored[0][1], scored[0][0], second[1], second[0]


# ---------------------------------------------------------------------------- built-in shapes (screen coords: y down)
def _circle(clockwise: bool = True) -> List[Point]:
    k = 1 if clockwise else -1
    return [(math.cos(-math.pi / 2 + k * 2 * math.pi * i / 48), math.sin(-math.pi / 2 + k * 2 * math.pi * i / 48))
            for i in range(49)]


def _poly(*corners: Point) -> List[Point]:
    out: List[Point] = []
    for a, b in zip(corners, corners[1:]):
        out += [(a[0] + (b[0] - a[0]) * t / 12, a[1] + (b[1] - a[1]) * t / 12) for t in range(12)]
    return out + [corners[-1]]


BUILTIN: Dict[str, Tuple[str, List[List[Point]]]] = {
    "draw_circle": ("원 ○", [_circle(True)]),
    "draw_check": ("체크 ✓", [_poly((0, .5), (.35, 1), (1, 0))]),
    "draw_triangle": ("삼각형 △", [_poly((.5, 0), (1, .87), (0, .87), (.5, 0))]),
    "draw_zigzag": ("Z 지그재그", [_poly((0, 0), (1, 0), (0, 1), (1, 1))]),
    "draw_square": ("사각형 □", [_poly((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]),
    "draw_x_mark": ("엑스 ✕ (한 획: ↘ 후 ↗)", [_poly((0, 0), (1, 1), (1, 0), (0, 1))]),
}
