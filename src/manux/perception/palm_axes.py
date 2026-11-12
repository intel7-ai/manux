"""
Direction calibration for the palm / fist cursor (2026-11-04).

With the elbow resting on a chair, the forearm swings about the elbow: a stroke the user means as "up and down"
shows up tilted in the camera image and the cursor went diagonally. The wizard records the palm centre while the
user moves the hand up and down a few times, measures the main direction of the strokes (principal axis of the
points, in square pixels) and builds a 2x2 matrix that turns that direction into screen vertical. Left / right
already works as it is, so the image horizontal stays screen horizontal (2026-11-04: the user asked for up/down
only). A hand step along either direction keeps its length, so the cursor speed is unchanged.

Pure logic like motion_mode.py: feed (time, palm centre | None, frame aspect); read the prompt and progress; when
done, `axes` holds (xx, xy, yx, yy) for `apply_axes`, or `error` says why the strokes could not be used.
Limits: one average direction per axis. The arc about the elbow still bends long strokes a little.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULTS: Dict[str, float] = {
    "palm_axes_prep": 1.5,        # s to read the prompt before a step records
    "palm_axes_stroke": 4.0,      # s of strokes recorded per step
    "palm_axes_min_span": 0.05,   # the strokes must cover at least this much (frame widths, standard deviation x 2)
    "palm_axes_min_ratio": 2.0,   # and be this much longer than wide (a straight back-and-forth, not a circle)
    "palm_axes_min_angle": 40.0,  # degrees between the two measured directions
}

STEPS = (
    ("vertical", "주먹을 쥐고 평소처럼 위아래로 여러 번 움직이세요"),
)
HORIZONTAL = (1.0, 0.0)           # left / right is not measured: the image horizontal

Axes = Tuple[float, float, float, float]


def palm_anchor_point(pts: Sequence[Tuple[float, float, float]], ratio: float) -> Tuple[float, float]:
    """Cursor anchor on wrist (#0) -> middle MCP (#9), shared by rules and MLP recovery."""
    ratio = max(0.0, min(1.0, float(ratio)))
    wrist, palm = pts[0], pts[9]
    return (wrist[0] + (palm[0] - wrist[0]) * ratio, wrist[1] + (palm[1] - wrist[1]) * ratio)


def palm_point(landmarks):
    """Original wrist-to-middle-MCP midpoint, in normalized image coordinates."""
    if landmarks is None or len(landmarks) != 21:
        return None
    return ((landmarks[0][0] + landmarks[9][0]) / 2, (landmarks[0][1] + landmarks[9][1]) / 2)


def corrected_point(point, aspect, axes):
    if point is None or not valid_axes(axes):
        return point
    dx, dy = apply_axes(point[0] - .5, (point[1] - .5) * aspect, axes, .6)
    return .5 + dx, .5 + dy / aspect


def principal_direction(points: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float, float, float]]:
    """Main direction of a point cloud: (ux, uy, major sd, minor sd), or None with too few points."""
    n = len(points)
    if n < 5:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points) / n
    syy = sum((p[1] - my) ** 2 for p in points) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points) / n
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    half = math.hypot((sxx - syy) / 2.0, sxy)
    major = math.sqrt(max(0.0, (sxx + syy) / 2.0 + half))
    minor = math.sqrt(max(0.0, (sxx + syy) / 2.0 - half))
    return math.cos(angle), math.sin(angle), major, minor


def axes_from_directions(vertical: Tuple[float, float], horizontal: Tuple[float, float]) -> Optional[Axes]:
    """Matrix turning a hand step (square pixels, y down) into a screen step: the measured horizontal direction
    becomes +x and the vertical one +y, lengths along them kept. None when the two directions are too alike."""
    hx, hy = horizontal
    vx, vy = vertical
    if hx < 0:                    # point "right" to +x and "down" to +y, whichever way the strokes were measured
        hx, hy = -hx, -hy
    if vy < 0:
        vx, vy = -vx, -vy
    det = hx * vy - vx * hy
    if abs(det) < 1e-6:
        return None
    return (vy / det, -vx / det, -hy / det, hx / det)


def apply_axes(dx: float, dy: float, axes: Optional[Sequence[float]], strength: float = 1.0) -> Tuple[float, float]:
    """`strength` blends the correction with no correction (1 = as measured, 0 = off): the measured tilt can be larger
    than the one the user feels (2026-11-04: a 50 degree measurement pushed the cursor past vertical the other way)."""
    if not axes or len(axes) != 4:
        return dx, dy
    s = max(0.0, min(1.0, float(strength)))
    xx, xy, yx, yy = (float(a) for a in axes)
    xx, xy, yx, yy = 1.0 + s * (xx - 1.0), s * xy, s * yx, 1.0 + s * (yy - 1.0)
    return xx * dx + xy * dy, yx * dx + yy * dy


def valid_axes(axes: Any) -> bool:
    try:
        vals = [float(a) for a in axes]
    except (TypeError, ValueError):
        return False
    return len(vals) == 4 and all(math.isfinite(v) for v in vals) and abs(vals[0] * vals[3] - vals[1] * vals[2]) > 1e-6


class PalmAxesCalibration:
    def __init__(self, config: Optional[Dict[str, Any]] = None, start: float = 0.0) -> None:
        self.config: Dict[str, float] = dict(DEFAULTS)
        if config:
            self.config.update({k: float(v) for k, v in config.items() if k in DEFAULTS})
        self.step = 0
        self._step_t0 = start
        self._points: Dict[str, List[Tuple[float, float]]] = {"vertical": [], "horizontal": []}
        self.directions: Dict[str, Tuple[float, float, float, float]] = {}
        self.axes: Optional[Axes] = None
        self.error = ""
        self.done = False

    def cfg(self, key: str) -> float:
        return float(self.config[key])

    @property
    def key(self) -> str:
        return STEPS[min(self.step, len(STEPS) - 1)][0]

    def view(self, now: float) -> Tuple[str, float, str]:
        """(prompt, progress 0..1 of this step, small note) for the screen."""
        if self.done:
            return ("방향 보정 완료" if self.axes else "방향 보정 실패", 1.0, self.error)
        prompt = STEPS[self.step][1] if len(STEPS) == 1 else f"{self.step + 1}/{len(STEPS)}  {STEPS[self.step][1]}"
        elapsed = now - self._step_t0
        prep = self.cfg("palm_axes_prep")
        if elapsed < prep:
            return prompt, 0.0, "준비하세요…"
        return prompt, max(0.0, min(1.0, (elapsed - prep) / self.cfg("palm_axes_stroke"))), "기록 중"

    def update(self, now: float, palm: Optional[Tuple[float, float]], aspect: float) -> bool:
        """Feed one frame. True once the whole calibration has finished (successfully or not)."""
        if self.done:
            return True
        elapsed = now - self._step_t0
        if elapsed >= self.cfg("palm_axes_prep") and palm is not None:
            self._points[self.key].append((float(palm[0]), float(palm[1]) * aspect))
        if elapsed < self.cfg("palm_axes_prep") + self.cfg("palm_axes_stroke"):
            return False
        d = principal_direction(self._points[self.key])
        name = "위아래" if self.key == "vertical" else "좌우"
        if d is None:
            return self._fail(f"{name} 움직임 동안 손이 거의 보이지 않았어요.")
        if 2.0 * d[2] < self.cfg("palm_axes_min_span"):
            return self._fail(f"{name} 움직임이 너무 작아요. 조금 더 크게 움직여 주세요.")
        if d[2] < self.cfg("palm_axes_min_ratio") * d[3]:
            return self._fail(f"{name} 움직임이 한 방향으로 모이지 않았어요. 같은 선을 따라 왕복해 주세요.")
        self.directions[self.key] = d
        self.step += 1
        self._step_t0 = now
        if self.step < len(STEPS):
            return False
        v, h = self.directions["vertical"], self.directions.get("horizontal", HORIZONTAL)
        between = math.degrees(math.acos(min(1.0, abs(v[0] * h[0] + v[1] * h[1]))))
        if between < self.cfg("palm_axes_min_angle"):
            return self._fail(f"위아래 움직임이 거의 옆으로 기울어 있어요({90 - between:.0f}°). 위아래로 다시 해 주세요.")
        self.axes = axes_from_directions(v[:2], h[:2])
        if self.axes is None:
            return self._fail("방향을 계산하지 못했어요. 다시 해 주세요.")
        tilt = math.degrees(math.atan2(abs(v[0]), abs(v[1])))
        self.error = f"위아래가 화면에서 {tilt:.0f}° 기울어 있었어요. 이제 수직으로 움직여요."
        self.done = True
        return True

    def _fail(self, why: str) -> bool:
        self.error = why
        self.done = True
        return True
