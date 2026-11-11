"""The control hand on the screen (2026-11-10).

A box the person draws on the camera image stands for the whole monitor: where the PALM CENTRE is inside the box is where the cursor is
(no relative movement, no speed scaling); the box edges are the monitor edges. The hand is drawn around the cursor, and the clicks are
done by that hand: a click or a drag lands where the thumb and the pinching finger meet on the drawn hand, not at the palm. Both the
palm point and the drawn joints go through a One Euro filter (steady when still, little lag when moving). The drawn hand has a fixed
size, so a far, small hand looks as large as a near one. Pinches and their clicks are still judged by the rule engine.

Hand shapes (2026-11-10): thumb + index, judged from the shape alone (ShapePinch), clicks between the fingers when it opens again
quickly, and drags from there when it stays closed for PINCH_HOLD. A fist holds the page: move it down to browse down, up to browse
up; one fist scrolls one way only (Ratchet), so bringing the hand back is not a scroll. Only the ring and pinky pinches of the rule
engine stay on.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SIZE_PX = 110.0          # wrist -> middle-finger knuckle on the screen (logical pixels)
THUMB_TIP = 4
PALM = (0, 5, 9, 13, 17)  # wrist and the four knuckles: their mean is the palm centre, the cursor
TIPS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
CLICK_FINGERS = ("middle", "ring", "pinky")
ACTION_FINGER = {"left_click": "middle", "right_click": "ring", "drag_hold": "middle"}
ACTION_NAMES = {"left_click": "좌클릭", "right_click": "우클릭", "drag_hold": "끌기", "scroll_grab": "스크롤", "task_switcher": "창 전환",
                "show_desktop": "바탕화면"}


def palm_centre(landmarks: Sequence[Any]) -> Tuple[float, float]:
    return (sum(landmarks[i].x for i in PALM) / len(PALM), sum(landmarks[i].y for i in PALM) / len(PALM))


ACROSS = .7          # knuckle row (index -> little knuckle) / palm length of a hand facing the camera (manux recordings: .71)


def hand_px(landmarks: Sequence[Any], width: int, height: int) -> float:
    """The hand's size in the image, steady when the hand turns (from manux-merged-20261110 `HandDrawer`).

    The plain wrist -> middle knuckle length collapses when the fingers turn toward the camera, which drew a huge, broken
    hand (2026-11-10 report). Bending the hand shortens the palm length, turning it edge-on narrows the knuckle row; the
    larger of the two (the row divided by its usual share) stays near the facing-camera size."""
    pts = [(p.x * width, p.y * height) for p in landmarks]
    return max(math.dist(pts[0], pts[9]), math.dist(pts[5], pts[17]) / ACROSS)


class HandSize:
    """A slow One Euro filter on `hand_px`, so the drawn size does not twitch (manux `HandDrawer`: OneEuro(.3, .002))."""

    def __init__(self) -> None:
        self._f = OneEuro(.3, .002)

    def reset(self) -> None:
        self._f.reset()

    def __call__(self, landmarks: Sequence[Any], width: int, height: int, now: Optional[float] = None) -> Optional[float]:
        if len(landmarks) != 21:
            return None
        return self._f((hand_px(landmarks, width, height),), now)[0]


def view(landmarks: Sequence[Any], width: int, height: int, guides: Sequence[Tuple[str, str]] = (), action: str = "none",
         size_px: float = SIZE_PX, size: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """21 points relative to the palm centre, in screen pixels: [x0, y0, x1, y1, ...], plus "click": where the pinch meets
    (None = no pinch). `size` = the hand's size in the image (`HandSize`); `hand_px` when not given. None when the hand box
    is degenerate."""
    if len(landmarks) != 21:
        return None
    pts = [(p.x * width, p.y * height) for p in landmarks]
    if size is None:
        size = hand_px(landmarks, width, height)
    if size < 1.0:
        return None
    k = size_px / size
    cx, cy = palm_centre(landmarks)
    ox, oy = cx * width, cy * height
    flat: List[float] = []
    for x, y in pts:
        flat += [round((x - ox) * k, 1), round((y - oy) * k, 1)]
    return {"pts": flat, "guides": [[f, s] for f, s in guides], "action": ACTION_NAMES.get(action, ""),
            "click": click_offset(flat, guides, action)}


def click_finger(guides: Sequence[Tuple[str, str]], action: str = "none") -> Optional[str]:
    """The finger doing the click or drag: the firing action's, else a click finger touching the thumb."""
    if action in ACTION_FINGER:
        return ACTION_FINGER[action]
    for state in ("action", "pinched"):
        for finger, s in guides:
            if s == state and finger in CLICK_FINGERS:
                return finger
    return None


def click_offset(pts: Sequence[float], guides: Sequence[Tuple[str, str]], action: str = "none") -> Optional[List[float]]:
    """Where the thumb and the pinching finger meet on the drawn hand, relative to the palm centre (logical px)."""
    finger = click_finger(guides, action)
    if finger is None:
        return None
    t, f = THUMB_TIP, TIPS[finger]
    return [round((pts[2 * t] + pts[2 * f]) / 2, 1), round((pts[2 * t + 1] + pts[2 * f + 1]) / 2, 1)]


class OneEuro:
    """One Euro filter (Casiez et al. 2012) over a point of any dimension: the cutoff rises with the speed."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0, speed_scale: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff, self.speed_scale = min_cutoff, beta, d_cutoff, speed_scale
        self.reset()

    def reset(self, x: Optional[Sequence[float]] = None) -> None:
        self.x = None if x is None else list(x)
        self.dx = None if x is None else [0.0] * len(x)
        self.t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        return 1.0 / (1.0 + 1.0 / (2 * math.pi * cutoff * dt))

    def __call__(self, x: Sequence[float], t: Optional[float] = None) -> List[float]:
        x = list(x)
        if self.x is None or len(self.x) != len(x):
            self.reset(x)
            self.t = t
            return list(x)
        dt = (t - self.t) if (t is not None and self.t is not None and t > self.t) else 1 / 30
        self.t = t
        ad = self._alpha(self.d_cutoff, dt)
        self.dx = [ad * (a - b) / dt + (1 - ad) * d for a, b, d in zip(x, self.x, self.dx)]
        speed = math.sqrt(sum(d * d for d in self.dx)) * self.speed_scale
        a = self._alpha(self.min_cutoff + self.beta * speed, dt)
        self.x = [a * n + (1 - a) * o for n, o in zip(x, self.x)]
        return list(self.x)


class ViewSmoother:
    """Steadies the drawn joints, each with its own speed (a still palm stays still while a finger closes)."""

    MIN_CUTOFF, BETA = .5, .03           # px: a few px of jitter is damped hard, a 300 px/s pinch is followed

    def __init__(self) -> None:
        self._filters = [OneEuro(self.MIN_CUTOFF, self.BETA) for _ in range(21)]

    def reset(self) -> None:
        for f in self._filters:
            f.reset()

    def __call__(self, pts: Sequence[float], now: Optional[float] = None) -> List[float]:
        out: List[float] = []
        for i, f in enumerate(self._filters):
            out += [round(v, 1) for v in f(pts[2 * i:2 * i + 2], now)]
        return out


DEFAULT_BOX = (.2, .15, .8, .85)     # x0, y0, x1, y1 on the (mirrored) camera image, 0..1
MIN_BOX = .1
AIM_BACK = .15                       # seconds: a click lands where the palm was this long before the fingers touched


def valid_box(box) -> bool:
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return False
    return 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and x1 - x0 >= MIN_BOX and y1 - y0 >= MIN_BOX


class BoxMapper:
    """Palm centre in the box -> 0..1 on the monitor; the box edges are the monitor edges. A One Euro filter steadies it. While a
    click finger touches the thumb the point stays still, at where the palm was just before the touch (the aim, not the pinch)."""

    MIN_CUTOFF, BETA, SPEED_SCALE = .4, .012, 1000.0  # speed in thousandths of the monitor per second
    # from manux-merged-20261110 BoxMapper: a hand found again after a gap eases in instead of landing across the screen
    # in one frame (2.2/min in a 40 min replay); the aim-back only goes back a tilt-sized way (it went any distance: 8.9/min)
    EASE = .2              # s from the last position to the live one
    GAP = .1               # s without a hand after which the next position eases in
    AIM_BACK_MAX = .02     # monitor fractions

    def __init__(self) -> None:
        self.pos: Optional[Tuple[float, float]] = None
        self._history: deque = deque(maxlen=30)
        self._held = False
        self._filter = OneEuro(self.MIN_CUTOFF, self.BETA, speed_scale=self.SPEED_SCALE)
        self._ease_from: Optional[Tuple[float, float]] = None
        self._ease_t = 0.0
        self._last_t: Optional[float] = None

    @property
    def held(self) -> bool:
        return self._held

    def reset(self) -> None:
        self.pos = None
        self._history.clear()
        self._held = False
        self._filter.reset()
        self._ease_from = None
        self._last_t = None

    def target(self, landmarks: Sequence[Any], box, hold: bool = False, now: float = 0.0, aim: bool = True) -> Tuple[float, float]:
        x0, y0, x1, y1 = box
        cx, cy = palm_centre(landmarks)
        nx = min(1.0, max(0.0, (cx - x0) / (x1 - x0)))
        ny = min(1.0, max(0.0, (cy - y0) / (y1 - y0)))
        gap = self._last_t is not None and now - self._last_t > self.GAP
        self._last_t = now
        if hold and self.pos is not None:
            if not self._held:
                self._held = True
                self._ease_from = None
                earlier = [p for t, p in self._history if t <= now - AIM_BACK] if aim else []
                if earlier and math.dist(earlier[-1], self.pos) <= self.AIM_BACK_MAX:
                    self.pos = earlier[-1]
            return self.pos
        if self.pos is not None and gap:
            self._ease_from, self._ease_t = self.pos, now   # found again after a gap: ease in from where it was
            self._filter.reset((nx, ny))
            self._filter.t = now
        elif self._held:
            self._filter.reset(self.pos)                    # released: follow the hand from the held point at its own speed
            self._filter.t = now
            self._ease_from = None
        self._held = False
        fx, fy = self._filter((nx, ny), now)
        # the filter only nears a clamped edge: snap so the box edge really is the monitor edge
        fx = nx if abs(fx - nx) < 1e-3 else fx
        fy = ny if abs(fy - ny) < 1e-3 else fy
        if self._ease_from is not None:
            k = (now - self._ease_t) / self.EASE
            if k >= 1.0:
                self._ease_from = None
            else:
                k = k * k * (3 - 2 * k)                     # smoothstep: no kick at either end
                fx, fy = self._ease_from[0] + (fx - self._ease_from[0]) * k, self._ease_from[1] + (fy - self._ease_from[1]) * k
        self.pos = (fx, fy)
        self._history.append((now, self.pos))
        return self.pos


def holds_still(guides: Sequence[Tuple[str, str]], action: str, gesture: str = "") -> bool:
    """A click finger touching the thumb holds the point (the click lands where it aimed). A drag or a fist (it grabs) must move."""
    if action == "drag_hold" or gesture == "fist":
        return False
    return any(finger in CLICK_FINGERS and state in ("pinched", "action") for finger, state in guides)


def pinch_point(pts: Sequence[float], finger: str) -> List[float]:
    t, f = THUMB_TIP, TIPS[finger]
    return [round((pts[2 * t] + pts[2 * f]) / 2, 1), round((pts[2 * t + 1] + pts[2 * f + 1]) / 2, 1)]


# 2026-11-10: in the virtual hand none of the rule engine's pinches act. Left click / drag (thumb + index) and right click
# (thumb + middle) come from the hand shape (ShapePinch), the task switcher from ShapeSwitch, the fist scrolls.
VIRTUAL_PINCHES: Tuple[str, ...] = ()
VIRTUAL_OFF_ACTIONS = frozenset({"left_click", "right_click", "scroll_grab", "grab", "drag_hold",   # index / middle / ring
                                 "task_switcher", "alt_tab", "drag_toggle",                       # pinky (ShapeSwitch opens it)
                                 "show_desktop"})                                                 # thumb tucked, sideways


def virtual_guides(guides: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
    return [(f, s) for f, s in guides if f in VIRTUAL_PINCHES]


class Grip:
    """A fist holding the page: "scroll" or None, kept through a short loss of the shape (one shaky frame does not let go)."""

    GRACE = .4       # seconds the shape may vanish without letting go (a flickering fist kept stopping the page)

    def __init__(self) -> None:
        self.kind: Optional[str] = None
        self.mode: Optional[str] = None
        self.started = False          # True on the frame the grip began
        self._lost: Optional[float] = None

    def reset(self) -> None:
        self.kind = self.mode = None
        self.started = False
        self._lost = None

    def step(self, kind: Optional[str], now: float) -> Optional[str]:
        self.started = False
        if kind is not None:
            self._lost = None
            if self.kind is None:
                self.kind, self.started = kind, True
                self.mode = "scroll"
        elif self.kind is not None:
            if self._lost is None:
                self._lost = now
            if now - self._lost > self.GRACE:
                self.reset()
        return self.mode


class ShapePinch:
    """A thumb + finger pinch judged from the hand shape alone (not the rule engine's pinch): the thumb tip to finger tip distance over
    the hand size (wrist -> middle knuckle), the finger out from the wrist (a fist also brings the thumb onto the fingers), and that
    finger nearer the thumb than the other of index / middle (a thumb + middle pinch often brushes the index).
    step() gives "down" when the fingers close, "up" when they open, else None.
    Replayed over this branch's recordings (2026-11-10): index 93 % of the thumb + index repetitions (75 % with the older
    "middle away" rule), about 6 false closes a minute while resting or preparing; middle about 59 % of thumb + middle."""

    CLOSE, OPEN, FRAMES = .18, .24, 2   # OPEN .24 (was .28): quick double taps do not open the fingers wide
    FINGER_OUT = .8       # finger tip at least this many hand sizes from the wrist

    def __init__(self, finger: str = "index") -> None:
        self.finger = finger
        self.other = "middle" if finger == "index" else "index"
        self.closed = False
        self._n = 0
        self.ratio = 1.0

    def reset(self) -> None:
        self.closed, self._n = False, 0

    def step(self, landmarks: Sequence[Any], width: int, height: int, frames: Optional[int] = None) -> Optional[str]:
        """frames: closed frames needed (default FRAMES; 1 for a double click's second tap)."""
        if len(landmarks) != 21:
            return None
        pt = [(p.x * width, p.y * height) for p in landmarks]
        size = math.dist(pt[0], pt[9])
        if size < 1.0:
            return None
        tip, other = TIPS[self.finger], TIPS[self.other]
        self.ratio = math.dist(pt[THUMB_TIP], pt[tip]) / size
        if not self.closed:
            if (self.ratio < self.CLOSE and math.dist(pt[0], pt[tip]) / size > self.FINGER_OUT
                    and self.ratio < math.dist(pt[THUMB_TIP], pt[other]) / size):
                self._n += 1
                if self._n >= (frames or self.FRAMES):
                    self.closed, self._n = True, 0
                    return "down"
            else:
                self._n = 0
            return None
        if self.ratio > self.OPEN:
            self.closed = False
            return "up"
        return None


class ShapeTriple:
    """Thumb, index and middle tips all together (right click), from the hand shape alone: both thumb -> index and thumb -> middle
    under CLOSE hand sizes, both fingers out from the wrist. Same "down" / "up" events as ShapePinch."""

    CLOSE, OPEN, FRAMES = .22, .30, 2
    FINGER_OUT = .8

    def __init__(self) -> None:
        self.closed = False
        self._n = 0

    def reset(self) -> None:
        self.closed, self._n = False, 0

    def step(self, landmarks: Sequence[Any], width: int, height: int) -> Optional[str]:
        if len(landmarks) != 21:
            return None
        pt = [(p.x * width, p.y * height) for p in landmarks]
        size = math.dist(pt[0], pt[9])
        if size < 1.0:
            return None
        far = max(math.dist(pt[THUMB_TIP], pt[8]), math.dist(pt[THUMB_TIP], pt[12])) / size
        if not self.closed:
            out = min(math.dist(pt[0], pt[8]), math.dist(pt[0], pt[12])) / size > self.FINGER_OUT
            if far < self.CLOSE and out:
                self._n += 1
                if self._n >= self.FRAMES:
                    self.closed, self._n = True, 0
                    return "down"
            else:
                self._n = 0
            return None
        if far > self.OPEN:
            self.closed = False
            return "up"
        return None


def triple_point(pts: Sequence[float]) -> List[float]:
    """Centre of the thumb, index and middle tips on the drawn hand."""
    ids = (THUMB_TIP, TIPS["index"], TIPS["middle"])
    return [round(sum(pts[2 * i] for i in ids) / 3, 1), round(sum(pts[2 * i + 1] for i in ids) / 3, 1)]


class ShapeSwitch:
    """The task switcher from the hand shape alone: thumb folded onto the palm, the other four fingers straight and pointing up, held
    still for HOLD seconds. Fires once, then waits for the shape to go away. (The rule engine's own "thumb tucked" desktop posture is
    the same hand turned sideways; it is off in the virtual hand.)"""

    EXTENDED = 1.5        # each finger: tip-to-wrist over knuckle-to-wrist
    TUCK = .5             # thumb tip to the nearest index/middle/ring/pinky knuckle joint, over the palm length
    UP = .94              # wrist -> middle tip within ~20 degrees of straight up (cosine)
    HOLD, STILL = .5, .03 # seconds held; palm-centre travel allowed meanwhile (frame fraction)

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._since: Optional[float] = None
        self._anchor: Optional[Tuple[float, float]] = None
        self._armed = True

    def shape(self, landmarks: Sequence[Any], width: int, height: int) -> bool:
        if len(landmarks) != 21:
            return False
        pt = [(p.x * width, p.y * height) for p in landmarks]
        w = pt[0]
        palm = math.dist(w, pt[9])
        if palm < 1.0:
            return False
        for mcp, tip in ((5, 8), (9, 12), (13, 16), (17, 20)):
            if math.dist(pt[tip], w) / max(1e-6, math.dist(pt[mcp], w)) <= self.EXTENDED:
                return False
        if min(math.dist(pt[THUMB_TIP], pt[j]) for j in (5, 6, 9, 10, 13, 17)) / palm > self.TUCK:
            return False
        vx, vy = pt[12][0] - w[0], pt[12][1] - w[1]
        return -vy / max(1e-6, math.hypot(vx, vy)) >= self.UP

    def step(self, landmarks: Sequence[Any], width: int, height: int, now: float) -> bool:
        if not self.shape(landmarks, width, height):
            self._since, self._anchor, self._armed = None, None, True
            return False
        here = palm_centre(landmarks)
        if self._since is None or math.dist(here, self._anchor) > self.STILL:
            self._since, self._anchor = now, here         # moving: the hold starts over
            return False
        if self._armed and now - self._since >= self.HOLD:
            self._armed = False
            return True
        return False


PINCH_HOLD = .45     # seconds thumb + index stay closed before it is a drag instead of a click


class Ratchet:
    """The page scroll of one fist: the first move past DEADZONE sets the way, and from then on only moves that way count. Moving
    back (to start the next stroke) is ignored, with a BAND of slack so tremor does not creep the page. Gives the y to scroll by."""

    DEADZONE, BAND = .008, .006      # camera-height fractions
    ONE_WAY = False                  # 2026-11-10: off, the lock held too hard; a held fist browses both ways

    def __init__(self) -> None:
        self._filter = OneEuro(1.0, .01, speed_scale=1000.0)
        self.reset()

    def reset(self) -> None:
        self.start: Optional[float] = None
        self.way = 0
        self.ext = 0.0
        self.out = 0.0
        self._filter.reset()

    def step(self, y: float, now: Optional[float] = None) -> float:
        y = self._filter((y,), now)[0]
        if not self.ONE_WAY:
            return y
        if self.start is None:
            self.start = self.ext = self.out = y
            return self.out
        if self.way == 0:
            if abs(y - self.start) <= self.DEADZONE:
                return self.out
            self.way = 1 if y > self.start else -1
            self.ext = self.start + self.way * self.DEADZONE
        ahead = (y - self.ext) * self.way
        if ahead > 0:
            self.out += ahead * self.way
            self.ext = y
        elif ahead < -self.BAND:
            self.ext = y + self.way * self.BAND
        return self.out
