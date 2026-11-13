"""Drawing mode (2026-11-10): draw a shape on the monitor with the hand, its command runs.

- Pen: the pointing index finger (index straight, the other three folded) = pen down, at the index fingertip; any
  other hand shape = pen up. The box drawn on the camera image = the whole monitor. Short slips do not break the line.
- Each stroke is recognised when the pen lifts (src/stroke_recognizer.py); shapes can be drawn one after another.
- A shape's command is an ordinary binding (src/action_bindings.py, key "draw_*", track "drawing"), so hotkeys,
  YouTube keys, programs, mouse actions and per-program commands all work, set in [설정] -> 그리기 모드.
- While the mode is on the other hand controls are off (like motion mode); the OS cursor does not move.

Shapes: built-in ones (stroke_recognizer.BUILTIN) and the user's own, drawn with the mouse in the settings
(config/draw_shapes.json: name and the examples, nothing else).
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from manux.compat.hand_mouse.stroke_recognizer import BUILTIN, normalize, path_length, recognize

ROOT = DATA_ROOT
DEFAULT_PATH = ROOT / "config" / "draw_shapes.json"
THRESHOLD = .80          # recognition score needed (0..1)
MARGIN = .03             # ... and this far ahead of the runner-up
MIN_PATH = .06           # monitor fractions: shorter strokes are taps, not drawings
MIN_POINTS = 8
MAX_POINTS = 400

# mnemonic defaults for the built-in shapes (changeable in the settings)
DEFAULT_BINDINGS: Dict[str, Tuple[str, str]] = {
    "draw_circle": ("hotkey", "f5"),                    # a circular arrow: reload
    "draw_check": ("hotkey", "enter"),                  # OK
    "draw_triangle": ("youtube_key", "media_play_pause"),  # play ▶
    "draw_zigzag": ("hotkey", "ctrl+z"),                # Z: undo
    "draw_square": ("hotkey", "win+shift+s"),           # a box: screen capture
    "draw_x_mark": ("mouse_action", "toggle_draw"),     # X: leave the drawing mode
}


class ShapeStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.user: Dict[str, Dict[str, Any]] = {}
        self.disabled: set = set()
        self.threshold = THRESHOLD
        self._templates: Optional[Dict[str, List[List[Tuple[float, float]]]]] = None
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.user = {k: v for k, v in (data.get("shapes") or {}).items() if isinstance(v, dict) and v.get("examples")}
        self.disabled = set(data.get("disabled") or [])
        try:
            self.threshold = min(.98, max(.5, float(data.get("threshold", THRESHOLD))))
        except (TypeError, ValueError):
            self.threshold = THRESHOLD
        self._templates = None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"shapes": self.user, "disabled": sorted(self.disabled), "threshold": self.threshold}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self._templates = None

    # ------------------------------------------------------------------ the list
    def shapes(self) -> List[Dict[str, Any]]:
        out = [{"key": k, "name": name, "builtin": True, "examples": len(ex), "enabled": k not in self.disabled,
                "preview": _preview(ex[0])} for k, (name, ex) in BUILTIN.items()]
        out += [{"key": k, "name": v.get("name", k), "builtin": False, "examples": len(v["examples"]),
                 "enabled": k not in self.disabled, "preview": _preview(v["examples"][0])} for k, v in self.user.items()]
        return out

    def name(self, key: str) -> str:
        if key in BUILTIN:
            return BUILTIN[key][0]
        return str(self.user.get(key, {}).get("name", key))

    def templates(self) -> Dict[str, List[List[Tuple[float, float]]]]:
        if self._templates is None:
            t = {k: [normalize(e) for e in ex] for k, (_, ex) in BUILTIN.items() if k not in self.disabled}
            t.update({k: [normalize(e) for e in v["examples"]] for k, v in self.user.items() if k not in self.disabled})
            self._templates = t
        return self._templates

    # ------------------------------------------------------------------ editing (settings)
    def add(self, name: str, examples: Sequence[Sequence[Sequence[float]]]) -> str:
        """A new shape from 1..5 examples drawn with the mouse. Returns its key."""
        name = str(name or "").strip()[:30]
        if not name:
            raise ValueError("그림 이름을 적어 주세요.")
        clean = [[(float(p[0]), float(p[1])) for p in e] for e in examples if len(e) >= MIN_POINTS]
        clean = [e for e in clean if path_length(e) > 0][:5]
        if not clean:
            raise ValueError("그림을 한 번 이상 그려 주세요.")
        if any(v.get("name") == name for v in self.user.values()) or any(n == name for n, _ in BUILTIN.values()):
            raise ValueError("같은 이름의 그림이 이미 있어요.")
        base = "draw_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "shape")
        key, i = base, 2
        while key in self.user or key in BUILTIN:
            key, i = f"{base}_{i}", i + 1
        self.user[key] = {"name": name, "examples": [[[round(x, 4), round(y, 4)] for x, y in _scale(e)] for e in clean]}
        self.save()
        return key

    def add_example(self, key: str, example: Sequence[Sequence[float]]) -> None:
        if key not in self.user:
            raise ValueError("직접 등록한 그림에만 예시를 더할 수 있어요.")
        e = [(float(p[0]), float(p[1])) for p in example]
        if len(e) < MIN_POINTS:
            raise ValueError("그림이 너무 짧아요.")
        self.user[key]["examples"] = (self.user[key]["examples"] + [[[round(x, 4), round(y, 4)] for x, y in _scale(e)]])[-5:]
        self.save()

    def remove(self, key: str) -> bool:
        if key in self.user:
            del self.user[key]
            self.disabled.discard(key)
            self.save()
            return True
        return False

    def set_enabled(self, key: str, on: bool) -> None:
        (self.disabled.discard if on else self.disabled.add)(key)
        self.save()

    def set_threshold(self, value: float) -> None:
        self.threshold = min(.98, max(.5, float(value)))
        self.save()

    def test(self, stroke: Sequence[Sequence[float]]) -> Dict[str, Any]:
        """What a stroke (drawn in the settings) would be recognised as."""
        return judge([(float(p[0]), float(p[1])) for p in stroke], self)


def _scale(e: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Examples are kept in a 0..1 box (any drawing size)."""
    xs, ys = [p[0] for p in e], [p[1] for p in e]
    side = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    return [((x - min(xs)) / side, (y - min(ys)) / side) for x, y in e]


def _preview(e: Sequence[Sequence[float]]) -> List[List[float]]:
    """24 points in 0..1 for the settings list's small picture."""
    pts = _scale([(float(p[0]), float(p[1])) for p in e])
    step = max(1, len(pts) // 24)
    return [[round(x, 3), round(y, 3)] for x, y in pts[::step]] + [[round(pts[-1][0], 3), round(pts[-1][1], 3)]]


def judge(stroke: Sequence[Tuple[float, float]], store: ShapeStore) -> Dict[str, Any]:
    """{"key", "name", "score", "ok", "why"} for one finished stroke (monitor fractions or any units)."""
    if len(stroke) < MIN_POINTS:
        return {"key": None, "name": "", "score": 0.0, "ok": False, "why": "너무 짧아요"}
    key, s, second, s2 = recognize(stroke, store.templates())
    if key is None:
        return {"key": None, "name": "", "score": 0.0, "ok": False, "why": "등록된 그림이 없어요"}
    ok = s >= store.threshold and s - s2 >= MARGIN
    why = "" if ok else ("비슷한 그림이 없어요" if s < store.threshold else f"{store.name(second)}와 헷갈려요")
    return {"key": key, "name": store.name(key), "score": round(s, 3), "ok": ok, "why": why}


def ensure_bindings(registry=None, store: Optional[ShapeStore] = None) -> None:
    """The built-in shapes' default commands, as defaults of the binding registry (in memory: nothing is written; a
    shape the user sets in the settings is saved like any binding). A user's own shape has no command until set."""
    from manux.compat.hand_mouse.action_bindings import DEFAULT_GESTURE_BINDINGS
    for key, (atype, aval) in DEFAULT_BINDINGS.items():
        DEFAULT_GESTURE_BINDINGS.setdefault(key, {"gesture_key": key, "track": "drawing", "action_type": atype,
                                                  "action_value": aval, "enabled": True, "app_overrides": []})


ensure_bindings()


POINT_STRAIGHT = 1.5     # index tip -> wrist / index knuckle -> wrist: straight from here (about 1.9 fully straight)
POINT_CURLED = 1.3       # middle, ring, pinky at or under this: folded
POINT_FRAMES = 2         # pointing frames before the pen goes down
PEN_GRACE = .3           # s the pen stays down through a slip (the finger read as bent, the hand lost for a moment)


def is_pointing(landmarks, aspect_hw: float = 9 / 16) -> bool:
    """Index straight, middle / ring / pinky folded (the thumb may do anything). In 3D (MediaPipe's z is on x's scale,
    y is scaled by the frame's height / width): a finger pointed at the screen points at the camera too and looks
    short in the picture."""
    def p(i):
        q = landmarks[i]
        return (q.x, q.y * aspect_hw, getattr(q, "z", 0.0) or 0.0)
    w = p(0)
    ratios = [math.dist(p(tip), w) / max(1e-6, math.dist(p(mcp), w)) for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20))]
    return ratios[0] >= POINT_STRAIGHT and all(r <= POINT_CURLED for r in ratios[1:])


class DrawMode:
    """The pen and the strokes. step() once per camera frame while the mode is on.

    2026-11-10 report: with the thumb + index pinch as the pen "the line breaks, so most shapes are not recognised".
    Now the pen is the pointing index finger: pointing = pen down, at the index fingertip; any other hand shape = up. A
    slip shorter than PEN_GRACE (the finger read as bent for a few frames, the hand lost for a moment) does not end the
    stroke: the line goes on from where the finger is seen again."""

    def __init__(self, store: ShapeStore) -> None:
        from manux.compat.hand_mouse.virtual_hand import OneEuro
        self.store = store
        # the fingertip moves more and faster than the palm: a quicker filter than the palm cursor's (less lag = truer shapes)
        self.filter = OneEuro(1.2, .02, speed_scale=1000.0)
        self.active = False
        self.stroke: List[Tuple[float, float]] = []
        self.pen_xy: Optional[Tuple[float, float]] = None
        self.down = False
        self._pointing_n = 0
        self._slip_since: Optional[float] = None
        self.last: Optional[Dict[str, Any]] = None       # the last judged stroke (+ its points, for the overlay)
        self.last_t = 0.0
        self.aspect = 16 / 9

    def set_active(self, on: bool) -> None:
        self.active = bool(on)
        self.reset()

    def reset(self) -> None:
        self.filter.reset()
        self.stroke = []
        self.pen_xy = None
        self.down = False
        self._pointing_n = 0
        self._slip_since = None

    def _slip(self, t: float) -> Optional[Dict[str, Any]]:
        """A frame without a pointing finger: the stroke ends only after PEN_GRACE."""
        self._pointing_n = 0
        if not self.down:
            return None
        if self._slip_since is None:
            self._slip_since = t
        if t - self._slip_since < PEN_GRACE:
            return None
        return self._finish(t)

    def _finish(self, t: float) -> Optional[Dict[str, Any]]:
        stroke, self.stroke = self.stroke, []
        self.down, self._slip_since = False, None
        if len(stroke) < MIN_POINTS or path_length(stroke) < MIN_PATH:
            return None
        result = judge([(px * self.aspect, py) for px, py in stroke], self.store)
        self.last = {**result, "points": [[round(a, 4), round(b, 4)] for a, b in stroke]}
        self.last_t = t
        return result

    def lost(self, t: float = 0.0) -> Optional[Dict[str, Any]]:
        """No hand this frame: like a slip (a short loss keeps the stroke; a long one ends and judges it)."""
        return self._slip(t)

    def step(self, t: float, landmarks, box, width: int = 0, height: int = 0, aspect: float = 16 / 9
             ) -> Optional[Dict[str, Any]]:
        """The judged stroke when the pen lifted this frame, else None. `aspect` = the monitor's width / height: the
        stroke is judged in the shape seen on the screen (a circle on screen is an ellipse in monitor fractions)."""
        self.aspect = aspect
        x0, y0, x1, y1 = box
        tip = landmarks[8]
        nx = min(1.0, max(0.0, (tip.x - x0) / (x1 - x0)))
        ny = min(1.0, max(0.0, (tip.y - y0) / (y1 - y0)))
        x, y = self.filter((nx, ny), t)
        self.pen_xy = (x, y)
        if not is_pointing(landmarks, height / width if width and height else 9 / 16):
            return self._slip(t)
        self._slip_since = None
        if not self.down:
            self._pointing_n += 1
            if self._pointing_n >= POINT_FRAMES:
                self.down, self.stroke = True, [(x, y)]
            return None
        if math.dist(self.stroke[-1], (x, y)) > .002:
            self.stroke.append((x, y))
            if len(self.stroke) > MAX_POINTS:
                self.stroke = self.stroke[::2]
        return None

    def view(self, t: float) -> Dict[str, Any]:
        """What the drawing overlay shows (monitor fractions)."""
        v: Dict[str, Any] = {"on": self.active, "pen": list(self.pen_xy) if self.pen_xy else [],
                             "down": self.down, "stroke": [[round(a, 4), round(b, 4)] for a, b in self.stroke]}
        if self.last is not None and t - self.last_t < 1.5:
            v["last"] = {k: self.last[k] for k in ("name", "ok", "why", "points", "score")}
            v["last"]["age"] = round(t - self.last_t, 2)
            v["last"]["action"] = self.last.get("action", "")
        return v
