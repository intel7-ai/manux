"""Shape-drawing gestures: hold a hand shape and draw a shape with the palm, e.g. "주먹으로 원 그리기" (2026-11-10).

Outside the drawing mode and in every hand mode. While the gesture's hand shape is held (src/pose_probe.hand_posture,
short blanks forgiven), the palm's path is kept; when the shape ends, or the hand stops for PAUSE s after moving, the
path is judged against the drawing mode's shapes (src/draw_mode.judge). Only a shape registered for that hand shape
fires. The command is an ordinary binding (key "path_*", track "path"); the gestures are in config/path_gestures.json.
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = DATA_ROOT
DEFAULT_PATH = ROOT / "config" / "path_gestures.json"
BLANK = .15              # s of no / another shape forgiven inside a stroke
PAUSE = .35              # s of the palm standing still (after moving) that ends a stroke
STILL = .08              # hand sizes per PAUSE that count as standing still
MIN_TRAVEL = 2.0         # hand sizes of path: shorter is not a drawing
MAX_SECONDS = 4.0
COOLDOWN = 1.0
ROTATIONS = {"rotate_cw": "시계 방향으로 돌리기", "rotate_ccw": "반시계 방향으로 돌리기"}
TURN = 70.0              # degrees the hand (a thumbs-up: the thumb) turns while the shape is held
TURN_SECONDS = 1.5       # ... within this long
TURN_STAY = 1.5          # hand sizes the palm may travel meanwhile (a turn of the wrist, not a sweep)


def shape_name(shape: str) -> str:
    if shape in ROTATIONS:
        return ROTATIONS[shape]
    from manux.compat.hand_mouse.draw_mode import ShapeStore
    return ShapeStore().name(shape) + " 그리기"


class PathStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.items: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        items = data.get("gestures") if isinstance(data, dict) else None
        self.items = {k: v for k, v in (items or {}).items() if isinstance(v, dict) and v.get("posture") and v.get("shape")}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"gestures": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def find(self, posture: str, shape: str) -> Optional[str]:
        return next((k for k, v in self.items.items() if v["posture"] == posture and v["shape"] == shape), None)

    def add(self, name: str, posture: str, shape: str) -> str:
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        from manux.compat.hand_mouse.draw_mode import ShapeStore
        from manux.compat.hand_mouse.sequence import POSTURES
        if posture not in POSTURES:
            raise ValueError(f"모르는 손 모양: {posture}")
        store = ShapeStore()
        if shape not in {s["key"] for s in store.shapes()} and shape not in ROTATIONS:
            raise ValueError(f"모르는 그림: {shape}")
        if self.find(posture, shape):
            raise ValueError("이미 있는 손 모양 그리기예요")
        name = str(name or "").strip()[:30] or f"{POSTURE_NAMES_KO.get(posture, posture)} 유지한 채 {shape_name(shape)}"
        key, i = f"path_{posture}_{shape.replace('draw_', '')}", 2
        while key in self.items:
            key, i = f"path_{posture}_{shape.replace('draw_', '')}_{i}", i + 1
        self.items[key] = {"name": name, "posture": posture, "shape": shape, "enabled": True}
        self.save()
        return key

    def remove(self, key: str) -> bool:
        if key in self.items:
            del self.items[key]
            self.save()
            return True
        return False

    def set_enabled(self, key: str, on: bool) -> None:
        if key in self.items:
            self.items[key]["enabled"] = bool(on)
            self.save()

    def rows(self) -> List[Dict[str, Any]]:
        from manux.compat.hand_mouse.action_bindings import POSTURE_NAMES_KO
        return [{"key": k, "name": v["name"], "posture": v["posture"], "shape": v["shape"],
                 "steps_text": f"{POSTURE_NAMES_KO.get(v['posture'], v['posture'])} 유지한 채 {shape_name(v['shape'])}",
                 "enabled": bool(v.get("enabled", True)), "motion_only": bool(v.get("motion_only"))}
                for k, v in self.items.items()]

    def set_motion_only(self, key: str, on: bool) -> bool:
        """Only while motion mode is on (2026-11-11 request). Off = in every mode, as before."""
        if key not in self.items:
            return False
        if on:
            self.items[key]["motion_only"] = True
        else:
            self.items[key].pop("motion_only", None)
        self.save()
        return True

    def active(self, motion: Optional[bool] = None) -> Dict[str, Tuple[str, str]]:
        """`motion`: is motion mode on? False leaves out the "motion mode only" ones; None = all of them."""
        return {k: (v["posture"], v["shape"]) for k, v in self.items.items()
                if v.get("enabled", True) and (motion is not False or not v.get("motion_only"))}


class PathDetector:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.posture = ""
        self.points: List[Tuple[float, float, float]] = []    # (t, x, y) in hand sizes
        self.sizes: List[float] = []                           # hand sizes seen in this stroke (the median is used)
        self.turns: List[Tuple[float, float]] = []             # (t, unwrapped angle) in this stroke
        self.off_since: Optional[float] = None
        self.cooldown_until = float("-inf")

    def _finish(self, t: float, gestures: Dict[str, Tuple[str, str]], shapes) -> Optional[str]:
        pts, posture = self.points, self.posture
        self.points, self.posture, self.off_since, self.sizes, self.turns = [], "", None, [], []
        if len(pts) < 15 or t < self.cooldown_until:
            return None
        xy = [(x, y) for _, x, y in pts]
        if sum(math.dist(a, b) for a, b in zip(xy, xy[1:])) < MIN_TRAVEL:
            return None
        wanted = {k: shape for k, (p, shape) in gestures.items() if p == posture and shape not in ROTATIONS}
        if not wanted:
            return None
        from manux.compat.hand_mouse.draw_mode import judge
        result = judge(xy, shapes)
        if not result.get("ok"):
            return None
        for k, shape in wanted.items():
            if shape == result["key"]:
                self.cooldown_until = t + COOLDOWN
                return k
        return None

    def _turned(self, t: float, gestures: Dict[str, Tuple[str, str]]) -> Optional[str]:
        """A registered rotation for the held shape, done: the hand turned TURN degrees within TURN_SECONDS while the
        palm stayed about where it was."""
        rot = {shape: k for k, (p, shape) in gestures.items() if p == self.posture and shape in ROTATIONS}
        if not rot or len(self.turns) < 5 or t < self.cooldown_until:
            return None
        recent = [(tt, a) for tt, a in self.turns if t - tt <= TURN_SECONDS]
        if len(recent) < 4:
            return None
        start_t = recent[0][0]
        delta = recent[-1][1] - recent[0][1]
        moved = [p for p in self.points if p[0] >= start_t]
        if moved and max(math.dist((moved[0][1], moved[0][2]), (p[1], p[2])) for p in moved) > TURN_STAY:
            return None
        shape = "rotate_ccw" if delta >= TURN else "rotate_cw" if delta <= -TURN else None
        if shape in rot:
            self.cooldown_until = t + COOLDOWN
            self.turns, self.points = [], []
            return rot[shape]
        return None

    def step(self, t: float, posture: Optional[str], palm: Optional[Tuple[float, float]], size: float,
             gestures: Dict[str, Tuple[str, str]], shapes, angle: Optional[float] = None) -> Optional[str]:
        """One frame (posture None / palm None = no hand). The gesture key when one is drawn or turned, else None.
        `angle`: the hand's direction in degrees (a thumbs-up: the thumb's), for rotations."""
        from manux.compat.hand_mouse.pose_probe import unless_bound
        postures = {p for p, _ in gestures.values()}
        posture = unless_bound(posture, postures)      # back / palm-down open hand: one stands in for the other
        here = posture if posture in postures else ""
        if self.posture and here != self.posture:
            if self.off_since is None:
                self.off_since = t
            if t - self.off_since > BLANK:
                return self._finish(t, gestures, shapes)
            return None
        self.off_since = None
        if not here or palm is None or size <= 0:
            return None
        if not self.posture:
            self.posture, self.points, self.sizes, self.turns = here, [], [], []
        # one hand size for the stroke (the median so far): a turning hand looks small edge-on, and dividing each
        # frame by its own size blew the path up (2026-11-10)
        self.sizes = (self.sizes + [size])[-60:]
        ref = sorted(self.sizes)[len(self.sizes) // 2]
        x, y = palm[0] / ref, palm[1] / ref
        self.points.append((t, x, y))
        self.points = [p for p in self.points if t - p[0] <= MAX_SECONDS]
        if angle is not None:
            if self.turns:
                prev = self.turns[-1][1]
                angle = prev + ((angle - prev + 180) % 360 - 180)
            self.turns = [p for p in self.turns if t - p[0] <= MAX_SECONDS] + [(t, angle)]
            key = self._turned(t, gestures)
            if key:
                return key
        # the hand stops after moving: the stroke is over
        recent = [p for p in self.points if t - p[0] <= PAUSE]
        drawn = sum(math.dist((a[1], a[2]), (b[1], b[2])) for a, b in zip(self.points, self.points[1:]))
        if drawn >= MIN_TRAVEL and recent and t - self.points[0][0] > PAUSE + .2:
            # (only after real drawing: a palm that stays put may be turning the wrist - a rotation)
            span = max(math.dist((recent[0][1], recent[0][2]), (p[1], p[2])) for p in recent)
            if span < STILL:
                key = self._finish(t, gestures, shapes)
                self.posture = here                  # still holding the shape: a new stroke may start from here
                return key
        return None
