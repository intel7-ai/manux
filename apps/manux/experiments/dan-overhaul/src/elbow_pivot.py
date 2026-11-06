"""
Elbow pivot for the palm / fist cursor (2026-11-04, not wired into the app yet).

With the elbow resting on the chair the forearm turns about the elbow. Seen by the camera:
- left / right: the hand swings about the elbow, so it draws an ARC around it;
- up / down: the forearm bends, so the hand moves toward / away from the elbow (a RADIAL line), which is why
  "straight up" came out diagonal: the line points at the elbow, below and to the side of the hand.
The elbow itself is often hidden (seated, arm on the chair), but it barely moves, so it can be found as the
centre of the arcs the wrist draws during left / right sweeps. With that centre a hand step becomes
(arc length about the elbow -> screen x, change of distance from the elbow -> screen y): both the tilt and the
bend of the arc are straightened, wherever the elbow is.

Pure logic like palm_axes.py: feed `observe(time, wrist, aspect)` every frame; once `elbow` is known,
`convert(prev, cur, aspect)` turns a wrist step into a straightened step (same units as the input, y down).
`set_prior(point)` takes an elbow from elsewhere (a Pose model, a manual setting) until the arcs give one.

Limits: a 2-D picture of a 3-D arm. An arm pointing at the camera foreshortens the radius; a moved elbow is
followed only after a few new sweeps (`elbow_pivot_window`).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

DEFAULTS: Dict[str, float] = {
    "elbow_pivot_window": 6.0,       # s of wrist positions kept for the fit
    "elbow_min_step": 0.003,         # a frame moves at least this much (square frame units) to count as movement
    "elbow_sideways_cos": 0.6,       # a step counts as a left / right sweep when |cos| to the image horizontal >= this
    "elbow_min_points": 12,          # sweep points needed before fitting
    "elbow_min_span_deg": 20.0,      # the sweep points must cover at least this much arc about the fitted centre
    "elbow_min_radius": 0.2,         # plausible forearm length in the image (square frame units). 0.12 let wrist-only
                                     # arcs through (2026-11-04 logs: radius 0.13-0.15 with a hand ~0.12 long)
    "elbow_max_radius": 1.6,
    "elbow_max_residual": 0.08,      # rms distance of the points from the circle, relative to the radius
    "elbow_below_only": 1.0,         # 1 = the elbow must be below the wrist (seated, arm on the chair)
    "elbow_smooth": 0.3,             # how far a new fit moves the estimate (0..1)
}


def fit_circle(points: Sequence[Point]) -> Optional[Tuple[float, float, float, float]]:
    """Least-squares circle (Kasa): (cx, cy, r, rms residual), or None when the points are (nearly) on a line."""
    n = len(points)
    if n < 3:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    suu = svv = suv = suuu = svvv = suvv = svuu = 0.0
    for x, y in points:
        u, v = x - mx, y - my
        uu, vv = u * u, v * v
        suu += uu
        svv += vv
        suv += u * v
        suuu += uu * u
        svvv += vv * v
        suvv += u * vv
        svuu += v * uu
    det = suu * svv - suv * suv
    if abs(det) < 1e-12:
        return None
    b1 = 0.5 * (suuu + suvv)
    b2 = 0.5 * (svvv + svuu)
    uc = (b1 * svv - b2 * suv) / det
    vc = (suu * b2 - suv * b1) / det
    cx, cy = mx + uc, my + vc
    r = math.sqrt(uc * uc + vc * vc + (suu + svv) / n)
    rms = math.sqrt(sum((math.hypot(x - cx, y - cy) - r) ** 2 for x, y in points) / n)
    return cx, cy, r, rms


def polar_step(elbow: Point, prev: Point, cur: Point) -> Tuple[float, float]:
    """(arc length about the elbow, change of distance from it) of a step, in the input units.
    Arc is + when the hand swings to image +x while above the elbow; distance is + moving away."""
    ax, ay = prev[0] - elbow[0], prev[1] - elbow[1]
    bx, by = cur[0] - elbow[0], cur[1] - elbow[1]
    ra, rb = math.hypot(ax, ay), math.hypot(bx, by)
    if ra < 1e-9 or rb < 1e-9:
        return 0.0, 0.0
    turn = math.atan2(ax * by - ay * bx, ax * bx + ay * by)
    return turn * 0.5 * (ra + rb), rb - ra


class ElbowPivot:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, float] = dict(DEFAULTS)
        if config:
            self.config.update({k: float(v) for k, v in config.items() if k in DEFAULTS})
        self._sweep: Deque[Tuple[float, Point]] = deque()
        self._last: Optional[Point] = None
        self.elbow: Optional[Point] = None          # square frame units (x, y * aspect), y down
        self.radius: Optional[float] = None
        self.source = ""                            # "pose" | "arcs" | "prior" | ""
        self.last_fit: Optional[Tuple[float, float, float, float]] = None
        self._pose_until = -1e9                     # a Pose elbow wins over the arcs until then

    def cfg(self, key: str) -> float:
        return float(self.config[key])

    def reset(self) -> None:
        self._sweep.clear()
        self._last = None
        self.elbow = self.radius = None
        self.source = ""
        self.last_fit = None

    def set_prior(self, point: Optional[Point], aspect: float = 1.0) -> None:
        """An elbow from elsewhere (normalised image x, y); used until the arcs give a better one."""
        if point is None or self.source == "arcs":
            return
        self.elbow = (float(point[0]), float(point[1]) * aspect)
        self.source = "prior"

    def set_pose(self, point: Point, aspect: float, now: float, hold: float = 0.6) -> None:
        """The elbow the Pose model sees (normalised image x, y): it replaces the arc estimate for `hold` s."""
        self.elbow = (float(point[0]), float(point[1]) * aspect)
        self.source = "pose"
        self._pose_until = now + hold

    def observe(self, now: float, wrist: Optional[Point], aspect: float) -> bool:
        """Feed the wrist (normalised image x, y). True when this frame updated the elbow estimate."""
        if wrist is None:
            self._last = None
            return False
        p = (float(wrist[0]), float(wrist[1]) * aspect)
        last, self._last = self._last, p
        window = self.cfg("elbow_pivot_window")
        while self._sweep and now - self._sweep[0][0] > window:
            self._sweep.popleft()
        if last is None:
            return False
        dx, dy = p[0] - last[0], p[1] - last[1]
        step = math.hypot(dx, dy)
        if step < self.cfg("elbow_min_step") or abs(dx) / step < self.cfg("elbow_sideways_cos"):
            return False                            # still, or an up / down (radial) step: not an arc sample
        self._sweep.append((now, p))
        if len(self._sweep) < self.cfg("elbow_min_points") or now < self._pose_until:
            return False
        return self._refit(p)

    def _refit(self, wrist: Point) -> bool:
        pts = [q for _, q in self._sweep]
        fit = fit_circle(pts)
        self.last_fit = fit
        if fit is None:
            return False
        cx, cy, r, rms = fit
        if not (self.cfg("elbow_min_radius") <= r <= self.cfg("elbow_max_radius")):
            return False
        if rms > self.cfg("elbow_max_residual") * r:
            return False
        if self.cfg("elbow_below_only") >= 1.0 and cy <= wrist[1]:
            return False
        angles = sorted(math.atan2(q[1] - cy, q[0] - cx) for q in pts)
        span = math.degrees(angles[-1] - angles[0])
        if span > 180.0:                            # wrapped around -pi / pi: measure the other way
            gaps = [b - a for a, b in zip(angles, angles[1:])] + [angles[0] + 2 * math.pi - angles[-1]]
            span = math.degrees(2 * math.pi - max(gaps))
        if span < self.cfg("elbow_min_span_deg"):
            return False
        k = self.cfg("elbow_smooth") if self.source == "arcs" else 1.0
        if self.elbow is None or self.source != "arcs":
            self.elbow, self.radius = (cx, cy), r
        else:
            self.elbow = (self.elbow[0] + k * (cx - self.elbow[0]), self.elbow[1] + k * (cy - self.elbow[1]))
            self.radius = self.radius + k * (r - self.radius)
        self.source = "arcs"
        return True

    def convert(self, prev: Point, cur: Point, aspect: float) -> Optional[Tuple[float, float]]:
        """A wrist step (normalised image points) as a straightened step in square frame units, y down. None without
        an elbow.

        The step is turned by how far the forearm (elbow -> hand) leans away from the nearest image axis: along the
        forearm stays along that axis, across it stays across. A forearm leaning 30 degrees off vertical makes
        bending it move the cursor straight up / down, and the swing about the elbow straight left / right; the
        lean changes along the swing, so the arc comes out straight too. It used to map "arc -> x, toward the
        elbow -> y", which is only right with the forearm pointing up: with the Pose elbow beside the hand
        (forearm lying sideways) x and y came out swapped (2026-11-04)."""
        if self.elbow is None:
            return None
        a = (float(prev[0]), float(prev[1]) * aspect)
        b = (float(cur[0]), float(cur[1]) * aspect)
        rx = 0.5 * (a[0] + b[0]) - self.elbow[0]
        ry = 0.5 * (a[1] + b[1]) - self.elbow[1]
        if math.hypot(rx, ry) < 1e-6:
            return b[0] - a[0], b[1] - a[1]
        theta = math.atan2(ry, rx)
        lean = theta - round(theta / (math.pi / 2)) * (math.pi / 2)
        c, s = math.cos(-lean), math.sin(-lean)
        dx, dy = b[0] - a[0], b[1] - a[1]
        return dx * c - dy * s, dx * s + dy * c

    def info(self) -> Dict[str, Any]:
        """For the failure log / HUD."""
        return {"elbow": [round(v, 4) for v in self.elbow] if self.elbow else None,
                "radius": round(self.radius, 4) if self.radius else None, "source": self.source,
                "sweep_points": len(self._sweep)}
