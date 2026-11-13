"""A moving hand picture for a gesture in the AI connection list (2026-11-11, user: "show, from the registered data, which
hand the command was registered with, so I can check it later").

`preview(key, registry)` returns the joints to draw (2D, fitted into a 0..1 square) and where they came from, in order:
  1. the examples saved when the shape was taught (data/posture_examples, the oldest = the registration),
  2. the look shown to the camera with [손 모양·움직임 보여주기] (logs/pose_probe, newest first),
  3. the hand just before the gesture really ran (logs/composite_fires, newest first),
  4. a drawn stand-in of the built-in shape (no data at all).
Only joints are read; nothing is written and no picture is kept."""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = DATA_ROOT
EXAMPLES_DIR = ROOT / "data" / "posture_examples"
PROBE_DIR = ROOT / "logs" / "pose_probe"
FIRES_DIR = ROOT / "logs" / "composite_fires"
ASPECT = 720 / 1280
MAX_FRAMES = 30          # ~2 s at 15 pictures a second
# short: the caption sits under a 96 px picture (2026-11-11: the longer ones wrapped into 2-3 lines)
SOURCES = {"example": "등록할 때 손", "probe": "보여준 손", "fire": "실행될 때 손", "drawn": "기본 그림"}

# built-in gesture keys -> (hand shape, movement)
BUILTIN = {
    "thumb_index": ("thumb_index", ""), "thumb_middle": ("thumb_middle", ""), "thumb_ring": ("thumb_ring", ""),
    "thumb_pinky": ("thumb_pinky", ""), "thumb_index_middle": ("thumb_index_middle", ""),
    "thumb_middle_drag": ("thumb_middle", ""), "palm_side": ("thumb_tucked", ""), "v_sign": ("v_sign", ""),
    "rock_sign": ("rock_sign", ""), "thumbs_up": ("thumbs_up", ""),
    "swipe_left": ("open_hand", "left"), "swipe_right": ("open_hand", "right"), "swipe_down": ("open_hand", "down"),
    "swipe_tab_left": ("thumb_tucked", "left"), "swipe_tab_right": ("thumb_tucked", "right"),
}
MOVES = {"swipe_left": "left", "swipe_right": "right", "swipe_up": "up", "swipe_down": "down"}


def what(key: str, registry, sequences=None, paths=None) -> Tuple[List[str], str]:
    """The hand shape(s) of a gesture key (a sequence has several) and its movement ("" = held still)."""
    comp = registry.composite_gestures.get(key)
    if comp is not None:
        trig = str(comp.get("trigger_type", ""))
        move = MOVES.get(trig, "double_pinch" if trig == "double_pinch" else "")
        return [str(comp.get("base_posture", ""))], move
    motion = registry.custom_motions.get(key)
    if motion is not None:
        return [str(motion.get("posture", ""))], str(motion.get("direction", ""))
    if key.startswith("seq_"):
        try:
            from manux.compat.hand_mouse.sequence import SequenceStore
            item = (sequences or SequenceStore()).items.get(key) or {}
            return [str(s) for s in item.get("steps") or []], "sequence"
        except Exception:
            return [], ""
    if key.startswith("path_"):
        try:
            from manux.compat.hand_mouse.path_gesture import PathStore
            item = (paths or PathStore()).items.get(key) or {}
            return [str(item.get("posture", ""))], "draw_" + str(item.get("shape", ""))
        except Exception:
            return [], ""
    if key in BUILTIN:
        shape, move = BUILTIN[key]
        return [shape], move
    return [], ""


# ---------------------------------------------------------------------------- recorded joints
def _xy(lm: Sequence[Any], aspect: float) -> Optional[List[Tuple[float, float]]]:
    if not lm or len(lm) != 21:
        return None
    try:
        return [(float(p[0]), float(p[1]) * aspect) for p in lm]     # frame widths in both directions
    except (TypeError, ValueError, IndexError):
        return None


def _thin(frames: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
    if len(frames) <= MAX_FRAMES:
        return frames
    step = len(frames) / MAX_FRAMES
    return [frames[int(i * step)] for i in range(MAX_FRAMES)]


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def from_examples(posture: str, folder: Optional[Path] = None) -> List[List[Tuple[float, float]]]:
    for p in sorted(Path(folder or EXAMPLES_DIR).glob(f"*-{posture}*.json")):    # oldest first: the registration
        d = _read(p)
        if not d or d.get("label") != posture:
            continue
        aspect = float(d.get("aspect") or ASPECT)
        frames = [f for f in (_xy(lm, aspect) for lm in d.get("frames") or []) if f]
        if frames:
            return _thin(frames)
    return []


def from_probes(posture: str, folder: Optional[Path] = None) -> List[List[Tuple[float, float]]]:
    for p in sorted(Path(folder or PROBE_DIR).glob("*.json"), reverse=True):
        d = _read(p)
        if not d or (d.get("result") or {}).get("posture") != posture:
            continue
        frames = [f for f in (_xy(x.get("lm"), ASPECT) for x in d.get("frames") or []
                              if isinstance(x, dict) and x.get("posture") == posture) if f]
        if len(frames) >= 3:
            return _thin(frames)
    return []


def from_fires(key: str, posture: str, folder: Optional[Path] = None) -> List[List[Tuple[float, float]]]:
    for p in sorted(Path(folder or FIRES_DIR).glob(f"*-{key}.json"), reverse=True):
        d = _read(p)
        if not d:
            continue
        frames = [f for f in (_xy(x.get("lm"), ASPECT) for x in d.get("frames") or []
                              if isinstance(x, dict) and x.get("posture") == posture) if f]
        if len(frames) >= 3:
            return _thin(frames[-MAX_FRAMES:])
    return []


# ---------------------------------------------------------------------------- a drawn stand-in (no data)
# A right hand as the mirrored preview shows it: palm to the camera, fingers up, thumb on the left. Unit box, y down.
_MCP = {1: (.32, .66), 5: (.38, .45), 9: (.49, .42), 13: (.59, .45), 17: (.68, .51)}
_LEN = {5: (.15, .09, .08), 9: (.16, .10, .08), 13: (.15, .09, .08), 17: (.11, .07, .07)}
WRIST = (.50, .90)
# shape -> (thumb, (index, middle, ring, pinky)); up / fold / half, thumb: out / tuck / up / pinch:<finger mcp>
SHAPES = {
    "open_hand": ("out", ("up", "up", "up", "up")),
    "open_hand_back": ("out", ("up", "up", "up", "up")),      # drawn mirrored: the thumb on the other side
    "open_hand_down": ("out", ("up", "up", "up", "up")),      # mirrored and squashed: seen from above the back
    "open_hand_up": ("out", ("up", "up", "up", "up")),        # squashed: the palm seen from above
    "thumb_tucked": ("tuck", ("up", "up", "up", "up")),
    "fist": ("tuck", ("fold", "fold", "fold", "fold")),
    "fingers_bent": ("tuck", ("half", "half", "half", "half")),
    "v_sign": ("tuck", ("up", "up", "fold", "fold")),
    "v_sign_down": ("tuck", ("up", "up", "fold", "fold")),
    "two_fingers": ("tuck", ("up", "up", "fold", "fold")),
    "rock_sign": ("tuck", ("up", "fold", "fold", "up")),
    "index_point": ("tuck", ("up", "fold", "fold", "fold")),
    "l_sign": ("out", ("up", "fold", "fold", "fold")),
    "pinky_up": ("tuck", ("fold", "fold", "fold", "up")),
    "shaka": ("out", ("fold", "fold", "fold", "up")),
    "thumbs_up": ("up", ("fold", "fold", "fold", "fold")),
    "thumbs_down": ("up", ("fold", "fold", "fold", "fold")),
    "knife_hand": ("out", ("up", "up", "up", "up")),
    "thumb_index": ("pinch:5", ("pinch", "up", "up", "up")),
    "thumb_middle": ("pinch:9", ("up", "pinch", "up", "up")),
    "thumb_ring": ("pinch:13", ("up", "up", "pinch", "up")),
    "thumb_pinky": ("pinch:17", ("up", "up", "up", "pinch")),
    "thumb_index_middle": ("pinch:5", ("pinch", "pinch", "up", "up")),
}
UPSIDE_DOWN = ("v_sign_down", "thumbs_down")


def _pinch_point(mcp: int) -> Tuple[float, float]:
    x, y = _MCP[mcp]
    return (x - .07 - (.03 if mcp >= 13 else 0), y - .02 if mcp >= 13 else y - .09)


def drawn(shape: str) -> List[Tuple[float, float]]:
    """21 joints of a stand-in hand for a built-in shape; [] for a shape it cannot draw (a taught one)."""
    if shape not in SHAPES:
        return []
    thumb, fingers = SHAPES[shape]
    pts: List[Tuple[float, float]] = [WRIST] * 21
    pts = list(pts)
    for mcp, state in zip((5, 9, 13, 17), fingers):
        x, y = _MCP[mcp]
        a, b, c = _LEN[mcp]
        lean = (x - .5) * .25                      # fingers fan out a little
        if state == "up":
            chain = [(x, y), (x + lean * a, y - a), (x + lean * (a + b), y - a - b), (x + lean * (a + b + c), y - a - b - c)]
        elif state == "half":                      # bent forward: the finger looks short
            chain = [(x, y), (x, y - a * .8), (x, y - a * .95), (x, y - a * .85)]
        elif state == "pinch":
            tx, ty = _pinch_point(5 if mcp in (5, 9) and thumb == "pinch:5" else mcp)
            chain = [(x, y), ((x * 2 + tx) / 3, y - a * .8), ((x + tx * 2) / 3, (y - a * .8 + ty) / 2 - .02), (tx, ty)]
        else:                                      # folded into the palm
            chain = [(x, y), (x - .01, y - a * .5), (x - .04, y - a * .35), (x - .035, y - a * .05)]   # a hook
        for i, p in enumerate(chain):
            pts[mcp + i] = p
    if thumb == "out":
        thumb_chain = [(.40, .80), _MCP[1], (.24, .57), (.18, .49)]
    elif thumb == "tuck":
        thumb_chain = [(.40, .80), (.36, .68), (.44, .60), (.53, .58)]
    elif thumb == "up":
        thumb_chain = [(.40, .78), (.36, .62), (.34, .48), (.33, .36)]
    else:
        tx, ty = _pinch_point(int(thumb.split(":")[1]))
        thumb_chain = [(.40, .80), _MCP[1], ((_MCP[1][0] + tx) / 2 - .03, (_MCP[1][1] + ty) / 2), (tx, ty)]
    for i, p in enumerate(thumb_chain):
        pts[1 + i] = p
    if shape == "knife_hand":                      # the palm turned sideways: narrow
        pts = [(.5 + (x - .5) * .3, y) for x, y in pts]
    if shape in ("open_hand_back", "open_hand_down"):
        pts = [(1 - x, y) for x, y in pts]
    if shape in ("open_hand_down", "open_hand_up"):
        pts = [(x, .5 + (y - .5) * .45) for x, y in pts]
    if shape in UPSIDE_DOWN:
        pts = [(1 - x, 1 - y) for x, y in pts]
    return pts


# ---------------------------------------------------------------------------- fitting and the result
def fit(frames: List[List[Tuple[float, float]]], margin: float = .1) -> List[List[List[float]]]:
    """Into a 0..1 square, the same scale for every frame (so the hand's own motion stays visible)."""
    xs = [x for f in frames for x, _ in f]
    ys = [y for f in frames for _, y in f]
    if not xs:
        return []
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    size = max(w, h, 1e-6)
    k = (1 - 2 * margin) / size
    ox, oy = min(xs) - (size - w) / 2, min(ys) - (size - h) / 2
    return [[[round(margin + (x - ox) * k, 4), round(margin + (y - oy) * k, 4)] for x, y in f] for f in frames]


def preview(key: str, registry, sequences=None, paths=None, examples_dir: Optional[Path] = None,
            probe_dir: Optional[Path] = None, fires_dir: Optional[Path] = None) -> Dict[str, Any]:
    """{"frames": [[ [x, y] * 21 ], ...] in 0..1, "fps", "source", "move", "shapes": [names], "steps": [frame index
    where each sequence step starts]}; frames [] when there is nothing to draw."""
    shapes, move = what(key, registry, sequences, paths)
    out: Dict[str, Any] = {"frames": [], "fps": 15, "source": "", "move": move, "shapes": shapes, "steps": []}
    clips: List[List[List[Tuple[float, float]]]] = []
    sources: List[str] = []
    for shape in shapes:
        frames, src = from_examples(shape, examples_dir), "example"
        if not frames:
            frames, src = from_probes(shape, probe_dir), "probe"
        if not frames and move != "sequence":
            frames, src = from_fires(key, shape, fires_dir), "fire"
        if not frames:
            one = drawn(shape)
            frames, src = ([one] * 8 if one else []), "drawn"
        if frames:
            clips.append(frames)
            sources.append(src)
    if not clips:
        return out
    if move == "sequence":                         # each step shown on its own scale, one after another
        allf: List[List[List[float]]] = []
        for clip in clips:
            out["steps"].append(len(allf))
            fitted = fit(clip)
            allf.extend((fitted * math.ceil(10 / len(fitted)))[:max(10, len(fitted))])
        out["frames"] = allf
    else:
        out["frames"] = fit(clips[0])
    names = list(dict.fromkeys(SOURCES[s] for s in sources))
    out["source"] = " · ".join(names)
    return out
