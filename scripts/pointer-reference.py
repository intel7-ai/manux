"""Generate a reference trace from the Python PointerEngine (trigger / off styles) for the JS port.

Usage: python -I make_pointer_reference.py <hand-mouse dir> <out.json>
Loads only src/gesture_engine.py, src/pointer_workspace.py and src/pointer_engine.py (pure Python).
"""
import json
import math
import random
import sys
import types
from pathlib import Path

root = Path(sys.argv[1]).resolve()
pkg = types.ModuleType("src")
pkg.__path__ = [str(root / "src")]
sys.modules["src"] = pkg
from src.gesture_engine import GestureEngine, Point3D  # noqa: E402
from src.pointer_engine import PointerEngine, PointerSettings  # noqa: E402

ASPECT = 9 / 16
DT = 1 / 30
rng = random.Random(7)


def pointing_hand(cx, cy, thumb=None, jitter=0.0):
    """Index extended up, middle/ring/pinky curled, thumb resting beside them (camera coords)."""
    s = 0.22
    pts = [None] * 21
    pts[0] = (0.0, 0.0, 0.0)
    bases = {5: (-0.22, -0.95), 9: (-0.02, -1.0), 13: (0.16, -0.92), 17: (0.32, -0.78)}
    for base, (bx, by) in bases.items():
        pts[base] = (bx, by, 0.0)
        if base == 5:   # extended index
            pts[6], pts[7], pts[8] = (bx - .02, by - .45, -.02), (bx - .03, by - .78, -.03), (bx - .04, by - 1.05, -.04)
        else:           # curled into the palm
            pts[base + 1] = (bx, by - .25, -.05)
            pts[base + 2] = (bx, by + .05, -.08)
            pts[base + 3] = (bx, by + .25, -.06)
    pts[1], pts[2], pts[3] = (-.25, -.15, 0.0), (-.42, -.35, -.02), (-.55, -.55, -.03)
    pts[4] = thumb if thumb is not None else (-.62, -.72, -.03)
    out = []
    for x, y, z in pts:
        out.append(Point3D(cx + x * s + rng.gauss(0, jitter), cy + y * s * (16 / 9) * .56 + rng.gauss(0, jitter), z * s))
    return out


def on_index_side(cx, cy):
    """Thumb tip pressed on the side of the index's first segment (#5-#6)."""
    return (-.31, -1.15, -.01)


def scenario():
    frames = []
    x, y = .5, .62

    def add(n, *, move=(0.0, 0.0), thumb=None, active=True, lost=False, jitter=.0008, style="trigger", anchor="index"):
        nonlocal x, y
        for _ in range(n):
            x += move[0]; y += move[1]
            hand = None if lost else pointing_hand(x, y, thumb, jitter)
            frames.append({"hand": hand, "active": active, "style": style, "anchor": anchor})

    add(20)                                        # still pointing
    add(25, move=(.012, .004))                     # move: latch
    add(30)                                        # settle: latch ends after .75 s
    add(8, thumb=on_index_side(x, y))              # quick press -> left click on release
    add(12)
    add(30, thumb=on_index_side(x, y))             # held still -> right click once
    add(12)
    add(6, thumb=on_index_side(x, y))              # press then move -> drag
    add(15, move=(.01, 0), thumb=on_index_side(x, y))
    add(12)
    add(5, lost=True)                              # hand lost
    add(15)
    add(10, active=False)                          # paused
    add(20, move=(-.008, .002), style="off")       # tracking only
    add(15, thumb=on_index_side(x, y), style="off")
    add(20, anchor="palm", style="off", move=(.004, -.003))
    add(20, jitter=.004)                           # noisy still hand
    return frames


def main():
    eng = PointerEngine(GestureEngine())
    trace = []
    t = 1.0
    cursor = [400.0, 300.0]
    for i, f in enumerate(scenario()):
        settings = PointerSettings(click_style=f["style"], anchor=f["anchor"])
        out = eng.step(t, f["hand"], ASPECT, settings, active=f["active"])
        record = None
        if out is not None and out.cursor_point is not None:
            cursor[0] += 5; cursor[1] += 1   # stand-in cursor motion for the rewind history
        if out is not None:
            record = [round(cursor[0]), round(cursor[1])]
            eng.record_cursor(t, tuple(record))
        trace.append({
            "t": t, "active": f["active"], "style": f["style"], "anchor": f["anchor"], "record": record,
            "hand": None if f["hand"] is None else [[p.x, p.y, p.z] for p in f["hand"]],
            "out": None if out is None else {
                "action": out.action, "preview": list(out.preview_point),
                "cursor": None if out.cursor_point is None else list(out.cursor_point),
                "warp": None if out.warp is None else list(out.warp),
                "latched": out.latched, "armed": out.click_armed, "source": out.pointer_source,
                "ratio": None if not math.isfinite(out.trigger_ratio) else out.trigger_ratio,
            },
        })
        t += DT
    Path(sys.argv[2]).write_text(json.dumps({"aspect": ASPECT, "frames": trace}), encoding="utf-8")
    actions = {}
    for fr in trace:
        a = fr["out"] and fr["out"]["action"]
        actions[a] = actions.get(a, 0) + 1
    print(len(trace), actions, "warps", sum(1 for fr in trace if fr["out"] and fr["out"]["warp"]))


main()
