"""Synthetic hands for the pointer tests (no recordings, no personal data: D-006).

`real_hand` and `thumb_on` are the v0.3 test helpers `make_real_hand` / `_thumb_on`
(hand-mouse/tests/test_gesture_mouse.py) returning lists of (x, y, z) tuples.
"""
from __future__ import annotations

import random
from typing import List, Sequence, Tuple

from manux.core.contracts import FrameContext, FrameInfo, HandObservation, OutputTarget, Stage

Pt = Tuple[float, float, float]
FINGER_JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))
ASPECT = 9 / 16


def real_hand(curled: bool, shift: Tuple[float, float] = (0.0, 0.0)) -> List[Pt]:
    """Wrist below, MCPs above it, tips extended or curled (v0.3 make_real_hand)."""
    sx, sy = shift
    pts = [(0.5 + sx, 0.8 + sy, 0.0) for _ in range(21)]
    for (mcp_i, tip_i), x in zip(FINGER_JOINTS, (0.44, 0.48, 0.52, 0.56)):
        pts[mcp_i] = (x + sx, 0.60 + sy, 0.0)
        pts[tip_i - 1] = (x + sx, (0.66 if curled else 0.50) + sy, 0.0)
        pts[tip_i] = (x + sx, (0.68 if curled else 0.40) + sy, 0.0)
    pts[4] = ((0.465 if curled else 0.36) + sx, (0.67 if curled else 0.62) + sy, 0.0)
    return pts


def thumb_on(tip_i: int, d: float, neighbours=(), tip_y: float = 0.50) -> List[Pt]:
    """Thumb d below finger tip `tip_i` (v0.3 _thumb_on)."""
    h = real_hand(curled=False)
    tip = (h[tip_i][0], tip_y, 0.0)
    h[tip_i] = tip
    h[4] = (tip[0], tip[1] + d, 0.0)
    side = 1
    for nb_i, nd in neighbours:
        h[nb_i] = (tip[0] + side * nd, tip[1] + d, 0.0)
        side = -side
    return h


def pointing_hand() -> List[Pt]:
    """Index extended, middle / ring / pinky curled, thumb away from the index side."""
    open_, fist = real_hand(False), real_hand(True)
    pts = list(fist)
    for i in (5, 6, 7, 8):
        pts[i] = open_[i]
    pts[6] = (0.44, 0.55, 0.0)
    pts[4] = (0.36, 0.64, 0.0)
    return pts


def trigger_hand(gap: float) -> List[Pt]:
    """Pointing hand with the thumb tip `gap` left of the index's first segment (#5-#6)."""
    pts = pointing_hand()
    pts[4] = (0.44 - gap, 0.575, 0.0)
    return pts


def thumbs_up() -> List[Pt]:
    pts = real_hand(True)
    pts[4] = (0.38, 0.45, 0.0)
    return pts


def index_pinch() -> List[Pt]:
    pts = real_hand(False)
    pts[8] = (0.44, 0.47, 0.0)
    pts[4] = (0.445, 0.48, 0.0)
    return pts


def three_pinch() -> List[Pt]:
    pts = index_pinch()
    pts[12] = (0.45, 0.48, 0.0)
    return pts


def free_hand(rng: random.Random) -> List[Pt]:
    """Random finger curls and the thumb tip near a random fingertip: the ambiguous cases."""
    pts = real_hand(False)
    for (mcp_i, tip_i), x in zip(FINGER_JOINTS, (0.44, 0.48, 0.52, 0.56)):
        tip_y = rng.uniform(.40, .70)
        pts[tip_i - 1] = (x + rng.uniform(-.01, .01), .60 + (tip_y - .60) * .6, 0.0)
        pts[tip_i] = (x + rng.uniform(-.015, .015), tip_y, 0.0)
    near = pts[rng.choice((8, 12, 12, 16, 16, 20))]
    pts[4] = (near[0] + rng.uniform(-.04, .04), near[1] + rng.uniform(-.01, .06), 0.0)
    return pts


def index_middle_hand(rng: random.Random) -> List[Pt]:
    """Thumb among the index and middle tips (thumb + index pinch, three-finger hold, near misses)."""
    pts = real_hand(False)
    for tip_i, x in ((8, .46), (12, .47)):
        tip_y = rng.uniform(.44, .58)
        pts[tip_i - 1] = (x + rng.uniform(-.01, .01), (.60 + tip_y) / 2, 0.0)
        pts[tip_i] = (x + rng.uniform(-.012, .012), tip_y, 0.0)
    for tip_i in (16, 20):                     # ring and pinky half curled or extended
        x, y, z = pts[tip_i]
        pts[tip_i] = (x, rng.choice((y, .52, .58)), z)
    a, b = pts[8], pts[12]
    k = rng.random()
    pts[4] = (a[0] + (b[0] - a[0]) * k + rng.gauss(0, .012), a[1] + (b[1] - a[1]) * k + rng.gauss(.01, .012), 0.0)
    return pts


def shifted(hand: Sequence[Pt], dx: float, dy: float = 0.0) -> List[Pt]:
    return [(x + dx, y + dy, z) for x, y, z in hand]


def lerp(a: Sequence[Pt], b: Sequence[Pt], k: float) -> List[Pt]:
    return [tuple(p + (q - p) * k for p, q in zip(pa, pb)) for pa, pb in zip(a, b)]  # type: ignore[misc]


def observation(points: Sequence[Pt], t: float, seq: int = 0) -> HandObservation:
    return HandObservation(frame_info(t, seq), tuple(points), "Right", 0.9)


def frame_info(t: float, seq: int = 0) -> FrameInfo:
    return FrameInfo(seq=seq, t=t, width=1280, height=720, mirrored=True)


def context(t: float, seq: int = 0, **settings) -> FrameContext:
    return FrameContext(frame_info(t, seq), Stage.MAIN, OutputTarget.WINDOWS, "pointer", settings)


# ----------------------------------------------------------------------------- random sequences

def _poses(rng: random.Random) -> List[Pt]:
    kind = rng.choice((
        "open", "open", "pointing", "pointing", "pointing", "middle", "middle", "ring", "ring",
        "trigger_press", "trigger_near", "fist", "thumbs_up", "index_pinch", "three_pinch",
        "free", "free", "free", "free", "index_middle", "index_middle",
    ))
    if kind == "index_middle":
        return index_middle_hand(rng)
    if kind == "free":
        return free_hand(rng)
    if kind == "open":
        return real_hand(False)
    if kind == "pointing":
        return pointing_hand()
    if kind == "middle":
        return thumb_on(12, rng.uniform(.0, .08), tip_y=rng.choice((.50, .50, .62)))
    if kind == "ring":
        return thumb_on(16, rng.uniform(.0, .07), [(12, .03)] if rng.random() < .3 else ())
    if kind == "trigger_press":
        return trigger_hand(rng.uniform(.0, .025))
    if kind == "trigger_near":
        return trigger_hand(rng.uniform(.03, .07))
    if kind == "fist":
        return real_hand(True)
    if kind == "thumbs_up":
        return thumbs_up()
    if kind == "index_pinch":
        return index_pinch()
    return three_pinch()


def random_frames(seed: int, n: int, *, dt: float = 1 / 30, with_inactive: bool = True):
    """Yield (t, points | None, active, detecting) for `n` frames: poses blended into each other,
    slow and fast hand travel (move latch), tremor, index-tip spikes, short and long hand losses,
    pauses."""
    rng = random.Random(seed)
    t = 1.0
    cur = pointing_hand()
    pos = [0.0, 0.0]
    count = 0
    while count < n:
        r = rng.random()
        if r < .06:                                    # hand loss: short (< hand_lost_reset) or long
            for _ in range(rng.choice((1, 2, 3, 6, 12, 20))):
                yield t, None, True, True
                t += dt
                count += 1
            continue
        if with_inactive and r < .08:                  # paused / not detecting
            active = rng.random() < .5
            for _ in range(rng.randint(2, 15)):
                yield t, shifted(cur, *pos), active, not active
                t += dt
                count += 1
            continue
        target = _poses(rng)
        speed = rng.choice((0.0, 0.0, 0.0, .002, .004, .006, .008, .02))
        vel = (speed * rng.choice((-1, 1)), speed * rng.uniform(-.5, .5))
        blend = rng.randint(1, 8)
        hold = rng.randint(1, 50)
        still = rng.choice((0, 0, blend + 2, blend + 6))   # start still (a press), then travel (a drag)
        for i in range(blend + hold):
            k = min(1.0, (i + 1) / blend)
            hand = lerp(cur, target, k)
            if i >= still:
                pos[0] = max(-.25, min(.25, pos[0] + vel[0]))
                pos[1] = max(-.15, min(.15, pos[1] + vel[1]))
            pts = shifted(hand, *pos)
            if rng.random() < .15:                     # tremor
                pts = [(x + rng.gauss(0, .0015), y + rng.gauss(0, .0015), z) for x, y, z in pts]
            if rng.random() < .004:                    # isolated index-tip spike
                x, y, z = pts[8]
                pts[8] = (x + .05, y, z)
            yield t, pts, True, True
            t += dt * rng.choice((1, 1, 1, 1, .9, 1.1))
            count += 1
            if count >= n:
                return
        cur = target
