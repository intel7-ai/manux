"""Synthetic hands for the pinch / motion tests, and the v0.3 loader for the parity tests.

The hand shapes follow v0.3 `hand-mouse/tests/test_gesture_mouse.py` (`make_real_hand` and friends) and
`tests/test_motion_mode.py` (`hand`). Landmarks are (x, y, z) tuples. No recorded data.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
import random
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from manux.core.contracts import Activity, FrameContext, FrameInfo, HandObservation, OutputTarget, Stage

Hand = List[Tuple[float, float, float]]
JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))
HAND_MOUSE = Path(__file__).resolve().parents[5] / "hand-mouse"


# ----------------------------------------------------------------------------- pinch hands (v0.3 geometry)
def real_hand(curled: bool = False, shift=(0.0, 0.0)) -> Hand:
    """Wrist below, MCPs above it, tips extended or curled (v0.3 `make_real_hand`)."""
    sx, sy = shift
    pts = [(0.5 + sx, 0.8 + sy, 0.0)] * 21
    pts = list(pts)
    for (mcp_i, tip_i), x in zip(JOINTS, (0.44, 0.48, 0.52, 0.56)):
        pts[mcp_i] = (x + sx, 0.60 + sy, 0.0)
        pts[tip_i - 1] = (x + sx, (0.66 if curled else 0.50) + sy, 0.0)
        pts[tip_i] = (x + sx, (0.68 if curled else 0.40) + sy, 0.0)
    pts[4] = ((0.465 if curled else 0.36) + sx, (0.67 if curled else 0.62) + sy, 0.0)
    return pts


def _set(h: Hand, i: int, x: float, y: float) -> Hand:
    h = list(h)
    h[i] = (x, y, 0.0)
    return h


def open_hand(shift=(0.0, 0.0)) -> Hand:
    return real_hand(False, shift)


def fist(shift=(0.0, 0.0)) -> Hand:
    return real_hand(True, shift)


def half_fist(tip_y: float = 0.52) -> Hand:
    """Fingers loosely curled (mean curl between open and fist): hand-shape speed > 1."""
    h = real_hand(False)
    for _, tip in JOINTS:
        h = _set(h, tip, h[tip][0], tip_y)
    return h


def scroll_pinch(shift=(0.0, 0.0)) -> Hand:
    """Thumb on the index tip, index bent toward it, the others open."""
    sx, sy = shift
    h = real_hand(False, shift)
    h = _set(h, 8, 0.43 + sx, 0.47 + sy)
    h = _set(h, 7, 0.435 + sx, 0.52 + sy)
    return _set(h, 4, 0.435 + sx, 0.485 + sy)


def drag_pinch(shift=(0.0, 0.0)) -> Hand:
    """Thumb, index and middle tips together (three-finger drag)."""
    sx, sy = shift
    h = scroll_pinch(shift)
    h = _set(h, 12, 0.445 + sx, 0.49 + sy)
    return _set(h, 11, 0.46 + sx, 0.53 + sy)


def middle_pinch(shift=(0.0, 0.0)) -> Hand:
    """Thumb on the middle tip; index away (v0.3 `_middle_pinch_hand` with the index open)."""
    sx, sy = shift
    h = real_hand(False, shift)
    h = _set(h, 12, 0.48 + sx, 0.48 + sy)
    return _set(h, 4, 0.495 + sx, 0.485 + sy)


def ring_pinch(shift=(0.0, 0.0)) -> Hand:
    """Thumb 0.02 below the ring tip (v0.3 `_thumb_on(16, 0.02)`)."""
    sx, sy = shift
    h = real_hand(False, shift)
    h = _set(h, 16, 0.52 + sx, 0.50 + sy)
    return _set(h, 4, 0.52 + sx, 0.52 + sy)


def pinky_pinch(shift=(0.0, 0.0)) -> Hand:
    """v0.3 `make_thumb_pinky_hand`."""
    sx, sy = shift
    h = real_hand(False, shift)
    h = _set(h, 16, 0.52 + sx, 0.55 + sy)
    h = _set(h, 20, 0.53 + sx, 0.58 + sy)
    return _set(h, 4, 0.515 + sx, 0.585 + sy)


def desktop_hand(shift=(0.0, 0.0)) -> Hand:
    """Four fingers open, thumb tucked across the palm (v0.3 `make_desktop_hand`)."""
    sx, sy = shift
    return _set(real_hand(False, shift), 4, 0.535 + sx, 0.63 + sy)


def thumbs_up(shift=(0.0, 0.0)) -> Hand:
    """Four fingers folded, thumb out and above the index knuckle (motion mode entry pose)."""
    sx, sy = shift
    return _set(real_hand(True, shift), 4, 0.40 + sx, 0.52 + sy)


def approach(shift=(0.0, 0.0)) -> Hand:
    """Middle tip 0.04 from the thumb: inside the approach zone, no click."""
    sx, sy = shift
    h = real_hand(False, shift)
    h = _set(h, 12, 0.48 + sx, 0.48 + sy)
    return _set(h, 4, 0.48 + sx, 0.52 + sy)


PINCH_POSES = {
    "open": open_hand, "fist": fist, "half": lambda shift=(0, 0): _shift(half_fist(), shift),
    "scroll": scroll_pinch, "drag": drag_pinch, "middle": middle_pinch, "ring": ring_pinch,
    "pinky": pinky_pinch, "desktop": desktop_hand, "thumbs_up": thumbs_up, "approach": approach,
}


def _shift(h: Hand, shift) -> Hand:
    return [(x + shift[0], y + shift[1], z) for x, y, z in h]


def _lerp(a: Hand, b: Hand, k: float) -> Hand:
    return [(pa[0] + (pb[0] - pa[0]) * k, pa[1] + (pb[1] - pa[1]) * k, pa[2] + (pb[2] - pa[2]) * k)
            for pa, pb in zip(a, b)]


def pinch_sequence(seed: int, frames: int) -> List[Tuple[float, Optional[Hand]]]:
    """A random walk over the pinch poses: holds of 1-45 frames, blends between poses, a drifting hand,
    landmark jitter, uneven frame times and hand losses (short and longer than app.hand_lost_reset)."""
    rnd = random.Random(seed)
    names = list(PINCH_POSES)
    weights = [3, 2, 1, 4, 2, 4, 3, 2, 3, 1, 1]
    out: List[Tuple[float, Optional[Hand]]] = []
    t = 1.0
    prev = open_hand()
    pos = [0.0, 0.0]
    while len(out) < frames:
        if rnd.random() < 0.06:
            for _ in range(rnd.choice((1, 2, 4, 8, 15))):
                t += 1 / 30
                out.append((t, None))
            continue
        name = rnd.choices(names, weights)[0]
        target = PINCH_POSES[name]()
        blend = rnd.randint(0, 5)
        hold = rnd.randint(1, 45)
        vel = (rnd.uniform(-0.01, 0.01), rnd.uniform(-0.01, 0.01)) if rnd.random() < 0.6 else (0.0, 0.0)
        sigma = rnd.choice((0.0, 0.001, 0.003, 0.006))
        for k in range(blend + hold):
            base = _lerp(prev, target, (k + 1) / (blend + 1)) if k < blend else target
            pos[0] = max(-0.2, min(0.2, pos[0] + vel[0]))
            pos[1] = max(-0.2, min(0.2, pos[1] + vel[1]))
            h = [(x + pos[0] + rnd.gauss(0, sigma), y + pos[1] + rnd.gauss(0, sigma), z + rnd.gauss(0, sigma))
                 for x, y, z in base]
            t += rnd.choice((1 / 30, 1 / 30, 1 / 60, 0.045))
            out.append((t, h))
        prev = target
    return out[:frames]


# ----------------------------------------------------------------------------- motion hands (v0.3 test geometry)
def motion_hand(cx=0.5, cy=0.5, fingers="open", thumb="out", bent=(), half=()) -> Hand:
    """v0.3 `tests/test_motion_mode.hand`. Palm 0.1 long. fingers: open | folded. thumb: out | tucked | mid | up | side."""
    wrist = (cx, cy + 0.1)
    mcps = {5: (cx - 0.04, cy + 0.01), 9: (cx - 0.013, cy), 13: (cx + 0.013, cy + 0.005), 17: (cx + 0.04, cy + 0.02)}
    pts: Dict[int, Tuple[float, float]] = {i: (cx, cy + 0.05) for i in range(21)}
    pts[0] = wrist
    for mcp, (x, y) in mcps.items():
        pts[mcp] = (x, y)
        pts[mcp + 3] = (x, y - 0.08) if fingers == "open" else (x, y + 0.02)
        if mcp in bent:
            pts[mcp + 3] = (x, y + 0.02)
        elif mcp in half:
            pts[mcp + 3] = (x, y - 0.025)
        pts[mcp + 1], pts[mcp + 2] = (x, y - 0.02), (x, y - 0.05)
    p13 = mcps[13]
    if fingers == "folded" and thumb == "out":
        thumb = "up"
    pts[4] = {"out": (cx - 0.15, cy + 0.05), "tucked": (cx + 0.02, cy + 0.03), "mid": (p13[0] - 0.06, p13[1]),
              "up": (cx - 0.06, cy - 0.06), "side": (cx - 0.15, cy + 0.05)}[thumb]
    return [(pts[i][0], pts[i][1], 0.0) for i in range(21)]


def swipe(x_from, x_to, n=10, thumb="out", fingers="open", dy=0.0) -> List[Hand]:
    return [motion_hand(cx=x_from + (x_to - x_from) * i / (n - 1), cy=0.5 + dy * i / (n - 1), thumb=thumb,
                        fingers=fingers) for i in range(n)]


def motion_sequence(seed: int, frames: int) -> List[Tuple[float, Optional[Hand], str, bool]]:
    """(t, hand | None, request, blocked) frames: thumbs-up holds, rests, swipes of every kind, folded hands,
    hand losses, button presses and stretches where another feature is busy (blocked)."""
    rnd = random.Random(seed)
    out: List[Tuple[float, Optional[Hand], str, bool]] = []
    t = 0.0
    x = 0.5
    blocked = False
    while len(out) < frames:
        kind = rnd.choices(("up", "rest", "swipe", "fold", "none", "button"), (3, 3, 7, 1, 1, 0.3))[0]
        if rnd.random() < 0.1:
            blocked = not blocked
        thumb = rnd.choice(("out", "out", "tucked", "tucked", "mid"))
        hands: List[Optional[Hand]]
        if kind == "up":
            hands = [motion_hand(cx=x + rnd.uniform(-0.01, 0.01), fingers="folded") for _ in range(rnd.randint(5, 40))]
        elif kind == "rest":
            hands = [motion_hand(cx=x, thumb=thumb) for _ in range(rnd.randint(3, 25))]
        elif kind == "swipe":
            to = max(0.1, min(0.9, x + rnd.choice((-1, 1)) * rnd.uniform(0.05, 0.4)))
            n = rnd.randint(4, 30)
            bent = (rnd.choice((5, 9, 13, 17)),) if rnd.random() < 0.1 else ()
            hands = [motion_hand(cx=x + (to - x) * i / (n - 1), cy=0.5 + rnd.choice((0.0, 0.0, 0.2)) * i / (n - 1),
                                 thumb=thumb, bent=bent if i == n // 2 else ()) for i in range(n)]
            x = to
        elif kind == "fold":
            hands = [motion_hand(cx=x, fingers="folded", thumb="tucked") for _ in range(rnd.randint(3, 15))]
        elif kind == "none":
            hands = [None] * rnd.randint(1, 50)
        else:
            t += 1 / 30
            out.append((t, motion_hand(cx=x, thumb=thumb), "toggle", blocked))
            continue
        sigma = rnd.choice((0.0, 0.0, 0.002))
        for h in hands:
            if h is not None and sigma:
                h = [(px + rnd.gauss(0, sigma), py + rnd.gauss(0, sigma), pz) for px, py, pz in h]
            t += rnd.choice((1 / 30, 1 / 30, 1 / 60, 0.045))
            out.append((t, h, "", blocked))
    return out[:frames]


# ----------------------------------------------------------------------------- contracts helpers
def obs(t: float, hand: Hand, seq: int = 0) -> HandObservation:
    return HandObservation(FrameInfo(seq, t, 1280, 720, True), tuple(hand), "Right", 0.9)


def ctx(t: float, mode: str = "pinch", settings=None, others: Optional[Dict[str, Activity]] = None,
        seq: int = 0) -> FrameContext:
    return FrameContext(FrameInfo(seq, t, 1280, 720, True), Stage.MAIN, OutputTarget.WINDOWS, mode,
                        settings or {}, others or {})


def check_pairs(intents) -> None:
    """Every begin gets an end before the next begin of the same gesture; hold only inside begin..end."""
    open_: Dict[str, bool] = {}
    for i in intents:
        if i.phase == "begin":
            assert not open_.get(i.gesture), f"second begin of {i.gesture} at {i.t}"
            open_[i.gesture] = True
        elif i.phase == "end":
            assert open_.get(i.gesture), f"end without begin of {i.gesture} at {i.t}"
            open_[i.gesture] = False
        elif i.phase == "hold" and i.gesture in ("pinch.scroll", "pinch.drag"):
            assert open_.get(i.gesture), f"hold without begin of {i.gesture} at {i.t}"


# ----------------------------------------------------------------------------- v0.3 loader (tests only)
@contextmanager
def v03_modules() -> Iterator[types.SimpleNamespace]:
    """v0.3 `gesture_engine` (+ its stdlib-only app_paths / profiles) and `motion_mode`, loaded by path.

    `hand-mouse/src/__init__.py` imports the camera (cv2), so a stand-in `src` package is used and removed
    afterwards. `apps/manux/src` never imports `hand-mouse/`."""
    saved = {k: v for k, v in sys.modules.items() if k == "src" or k.startswith("src.")}
    for k in saved:
        del sys.modules[k]
    stub = types.ModuleType("src")
    stub.__path__ = [str(HAND_MOUSE / "src")]
    sys.modules["src"] = stub
    try:
        from src import gesture_engine, motion_mode, profiles  # type: ignore
        yield types.SimpleNamespace(ge=gesture_engine, mm=motion_mode, profiles=profiles)
    finally:
        for k in [k for k in sys.modules if k == "src" or k.startswith("src.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def v03_shipped_config(ge) -> dict:
    """What a fresh v0.3 install runs with: code defaults + the shipped config/gestures.json."""
    cfg = copy.deepcopy(ge.DEFAULT_CONFIG)
    shipped = json.loads((HAND_MOUSE / "config" / "gestures.json").read_text(encoding="utf-8"))
    ge.GestureEngine._deep_merge(cfg, shipped)
    return cfg
