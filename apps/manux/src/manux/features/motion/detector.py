"""Motion detection, ported from v0.3 `hand-mouse/src/motion_mode.py` (`hand_features`, `MotionMode`).

Arithmetic and check order copied unchanged (parity: tests/unit/features/test_motion_parity.py). Pure logic:
(time, 21 landmarks | None) in, `MotionEvent | None` out. No camera, Qt or OS input.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from manux.features.motion.settings import DEFAULTS

WRIST, MIDDLE_MCP = 0, 9
THUMB_TIP = 4
INDEX_MCP = 5
FINGER_JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))      # (MCP, tip): index, middle, ring, pinky
PALM_POINTS = (0, 5, 9, 13, 17)

# variant -> side -> v0.3 command name (only for MotionEvent.action; bindings decide what runs, D-004)
DEFAULT_ACTIONS = {
    "nav": {"left": "back", "right": "forward"},
    "tab": {"left": "tab_prev", "right": "tab_next"},
}


def hand_features(landmarks: Sequence[Sequence[float]]) -> Dict[str, Any]:
    """Finger curl ratios (tip-to-wrist / MCP-to-wrist, 3D like the pinch engine), thumb tuck and palm centre."""
    pts = [(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0) for p in landmarks[:21]]

    def d3(a, b):
        return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))

    def d2(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    wrist = pts[WRIST]
    curl = []
    for mcp, tip in FINGER_JOINTS:
        base = d3(pts[mcp], wrist)
        curl.append(d3(pts[tip], wrist) / base if base > 1e-6 else 9.9)
    palm_len = max(1e-6, d2(wrist, pts[MIDDLE_MCP]))
    tuck = min(d2(pts[THUMB_TIP], pts[13]), d2(pts[THUMB_TIP], pts[17])) / palm_len
    thumb_up = (pts[INDEX_MCP][1] - pts[THUMB_TIP][1]) / palm_len      # image y grows downwards
    cx = sum(pts[i][0] for i in PALM_POINTS) / len(PALM_POINTS)
    cy = sum(pts[i][1] for i in PALM_POINTS) / len(PALM_POINTS)
    return {"curl": tuple(curl), "thumb_tuck": tuck, "thumb_up": thumb_up, "palm": (cx, cy)}


@dataclass
class MotionEvent:
    kind: str                       # "enter" | "exit" | "swipe"
    info: Dict[str, Any] = field(default_factory=dict)
    action: Optional[str] = None    # swipe: v0.3 command name (back / forward / tab_prev / tab_next)


class MotionCore:
    """v0.3 `MotionMode` with the settings under their v0.4 short names (`c["enter_hold"]` = `motion.enter_hold`).

    Kept from v0.3: every threshold, the order of the checks, `active`, `last_miss`, `last_swipe`. Changed:
    the swipe's `action` is the gesture id's direction/variant (`_actions` maps to v0.3 names only so the
    parity test can compare); the caller (the feature) turns events into intents."""

    def __init__(self, c: Optional[Dict[str, Any]] = None) -> None:
        self.c: Dict[str, Any] = dict(DEFAULTS if c is None else c)
        self._actions: Dict[str, Dict[str, str]] = {v: dict(m) for v, m in DEFAULT_ACTIONS.items()}
        self.active = False
        self._entered_at = 0.0
        self._last_activity = 0.0
        self._last_hand_t = 0.0
        self._enter_since: Optional[float] = None
        self._enter_anchor: Optional[Tuple[float, float]] = None
        self._enter_last: Optional[Tuple[float, float]] = None
        self._enter_path = 0.0
        self._enter_lost_since: Optional[float] = None
        self._exit_since: Optional[float] = None
        self._exit_anchor: Optional[Tuple[float, float]] = None
        self._exit_lost_since: Optional[float] = None
        self._exit_armed = False         # the entry pose was let go since entering
        self._thumb_state: Optional[str] = None
        self._lows: List[float] = []
        self._settled = True
        self._pause_since: Optional[float] = None
        self._paused_ok = True           # rested long enough since the last swipe: the opposite direction is allowed
        self.last_miss: Optional[Dict[str, Any]] = None   # the biggest stroke that was NOT sent, and why
        self._hist: Deque[Tuple[float, float, float, bool, Optional[str], float, float]] = deque()
        self._last_swipe_t = -1e9
        self._last_swipe_dir = 0
        self._last_swipe_speed = 0.0
        self.last_features: Optional[Dict[str, Any]] = None
        self.last_swipe: Optional[Dict[str, Any]] = None
        self.entered_by = ""

    # ------------------------------------------------------------------ helpers
    def remaining(self, now: float) -> float:
        """Seconds until the timeout ends motion mode (0 when it is off)."""
        timeout = self.c["timeout"]
        if not self.active or timeout <= 0:
            return 0.0
        return max(0.0, timeout - (now - self._last_activity))

    def entering(self, now: float) -> float:
        """0..1 while the entry pose is being held (to show 'entering...'), 0 otherwise."""
        if self.active or self._enter_since is None:
            return 0.0
        return max(0.0, min(1.0, (now - self._enter_since) / self.c["enter_hold"]))

    def leaving(self, now: float) -> float:
        """0..1 while the pose is held again to switch the mode off, 0 otherwise."""
        if not self.active or self._exit_since is None:
            return 0.0
        return max(0.0, min(1.0, (now - self._exit_since) / self.c["enter_hold"]))

    def thumb_variant(self, tuck: float) -> Optional[str]:
        if tuck >= self.c["thumb_nav"]:
            return "nav"
        if tuck <= self.c["thumb_tab"]:
            return "tab"
        return None

    def entry_pose(self, f: Dict[str, Any]) -> bool:
        """Thumbs-up: thumb stuck out AND pointing up, the other four fingers folded."""
        return (f["thumb_tuck"] >= self.c["thumb_out"]
                and f.get("thumb_up", 9.9) >= self.c["thumb_up"]
                and max(f["curl"]) <= self.c["fold_ratio"])

    def open_hand(self, f: Dict[str, Any]) -> bool:
        """All four fingers fully extended (a swipe is only read from a completely open hand, so that other
        hand shapes stay free for other motions)."""
        return min(f["curl"]) >= self.c["open_ratio"]

    def pose(self, f: Dict[str, Any]) -> str:
        """A coarse name of the hand shape: entry | open | fist | other (for logs and later motions)."""
        if self.entry_pose(f):
            return "entry"
        if self.open_hand(f):
            return "open"
        if max(f["curl"]) <= self.c["fold_ratio"]:
            return "fist"
        return "other"

    def _enter(self, now: float, how: str) -> MotionEvent:
        self.active = True
        self.entered_by = how
        self._entered_at = self._last_activity = self._last_hand_t = now
        self._enter_since = None
        self._exit_since = None
        self._exit_armed = False
        self._thumb_state = None
        self._lows = []
        self._settled = True
        self._pause_since = None
        self._paused_ok = True
        self._hist.clear()
        self._last_swipe_t = -1e9
        self._last_swipe_dir = 0
        self._last_swipe_speed = 0.0
        return MotionEvent("enter", {"how": how})

    def _exit(self, now: float, why: str) -> MotionEvent:
        self.active = False
        self._hist.clear()
        self._enter_since = None
        self._exit_since = None
        return MotionEvent("exit", {"why": why, "seconds": round(now - self._entered_at, 2)})

    # ------------------------------------------------------------------ per frame
    def update(self, now: float, landmarks: Optional[Sequence[Any]], request: str = "",
               blocked: bool = False) -> Optional[MotionEvent]:
        """`request`: "toggle" | "on" | "off" (the button / a key). `blocked`: a pinch, scroll or drag is
        in progress or just ended - the entry pose is ignored then."""
        feats = hand_features(landmarks) if landmarks is not None else None
        self.last_features = feats
        if request in ("toggle", "on", "off"):
            if self.active and request in ("toggle", "off"):
                return self._exit(now, "request")
            if not self.active and request in ("toggle", "on"):
                return self._enter(now, "button")
        if not self.active:
            return self._watch_entry(now, feats, blocked)
        # ---- active
        lost_exit, timeout = self.c["hand_lost_exit"], self.c["timeout"]
        if feats is None:
            self._exit_since = None
            if lost_exit > 0 and now - self._last_hand_t >= lost_exit:
                return self._exit(now, "hand lost")
            return None
        self._last_hand_t = now
        if timeout > 0 and now - self._last_activity >= timeout:
            return self._exit(now, "timeout")
        off = self._watch_exit_pose(now, feats)
        if off is not None:
            return off
        return self._watch_swipe(now, feats)

    def _watch_exit_pose(self, now: float, feats: Dict[str, Any]) -> Optional[MotionEvent]:
        """The entry pose, held still again (after it was let go once), switches the mode off."""
        if not self.entry_pose(feats):
            if self._exit_since is not None and self._exit_armed:
                if self._exit_lost_since is None:
                    self._exit_lost_since = now
                if now - self._exit_lost_since <= self.c["enter_grace"]:
                    return None           # a noisy frame in the middle of the hold
            self._exit_armed = True
            self._exit_since = None
            self._exit_lost_since = None
            return None
        self._exit_lost_since = None
        if not self._exit_armed:
            return None                   # still holding the pose that switched the mode on
        palm = feats["palm"]
        if self._exit_since is None or math.hypot(palm[0] - self._exit_anchor[0], palm[1] - self._exit_anchor[1]) \
                > self.c["enter_still"]:
            self._exit_since, self._exit_anchor = now, palm
            return None
        if now - self._exit_since >= self.c["enter_hold"]:
            return self._exit(now, "pose")
        return None

    def _watch_entry(self, now: float, feats: Optional[Dict[str, Any]], blocked: bool) -> Optional[MotionEvent]:
        if feats is None or blocked or not self.entry_pose(feats):
            # a noisy frame must not throw away a hold in progress: the pose may drop out for motion_enter_grace
            if self._enter_since is not None and feats is not None and not blocked:
                if self._enter_lost_since is None:
                    self._enter_lost_since = now
                if now - self._enter_lost_since <= self.c["enter_grace"]:
                    return None
            self._enter_since = None
            self._enter_lost_since = None
            return None
        self._enter_lost_since = None
        palm = feats["palm"]
        if self._enter_since is None:
            self._enter_since, self._enter_anchor, self._enter_last, self._enter_path = now, palm, palm, 0.0
            return None
        self._enter_path += math.hypot(palm[0] - self._enter_last[0], palm[1] - self._enter_last[1])
        self._enter_last = palm
        if (math.hypot(palm[0] - self._enter_anchor[0], palm[1] - self._enter_anchor[1]) > self.c["enter_still"]
                or self._enter_path > self.c["enter_path"]):
            # the hand is travelling (or moving back and forth in place): the hold starts over
            self._enter_since, self._enter_anchor, self._enter_last, self._enter_path = now, palm, palm, 0.0
            return None
        if now - self._enter_since >= self.c["enter_hold"]:
            return self._enter(now, "pose")
        return None

    def _watch_swipe(self, now: float, f: Dict[str, Any]) -> Optional[MotionEvent]:
        win = self.c["swipe_window"]
        if self.entry_pose(f):
            self._hist.clear()                # the toggle pose is not a swipe pose (and it is not "a bent finger")
            self._lows = []
            return None
        tuck = f["thumb_tuck"]
        # a state with hysteresis: a frame in between keeps the last decision
        if tuck >= self.c["thumb_nav"]:
            self._thumb_state = "nav"
        elif tuck <= self.c["thumb_tab"]:
            self._thumb_state = "tab"
        # The finger ratios are noisy, most of all when the thumb is tucked over the palm (one real session: only
        # 26-35 % of the thumb-tucked strokes passed "all four extended" against ~60 % with the thumb out, and a
        # single noisy frame refused a stroke). The lowest finger ratio is the median of the last three frames.
        low_raw = min(f["curl"])
        self._lows = (self._lows + [low_raw])[-3:]
        low = sorted(self._lows)[len(self._lows) // 2]
        self._hist.append((now, f["palm"][0], f["palm"][1], low >= self.c["open_ratio"], self._thumb_state,
                           tuck, low))
        while self._hist and now - self._hist[0][0] > win:
            self._hist.popleft()
        if len(self._hist) >= 3:
            a, b = self._hist[-3], self._hist[-1]
            vx = abs(b[1] - a[1]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
            if not self._settled and vx < self.c["settle_speed"]:
                self._settled = True      # the stroke is over; without this its second half read as another swipe
            # Repeating a swipe means bringing the hand back, and that movement reads as the opposite swipe.
            # The opposite direction therefore only counts after the hand has RESTED for a moment.
            if self._settled and not self._paused_ok:
                if vx < self.c["pause_speed"]:
                    self._pause_since = self._pause_since if self._pause_since is not None else now
                    if now - self._pause_since >= self.c["reverse_pause"]:
                        self._paused_ok = True
                else:
                    self._pause_since = None
        if not self._settled or now - self._last_swipe_t < self.c["swipe_cooldown"]:
            return None
        h = list(self._hist)
        best_miss: Optional[Dict[str, Any]] = None
        for start in range(len(h) - 3):
            ev, miss = self._stroke(now, h[start:], h)
            if ev is not None:
                self.last_miss = None
                return ev
            if miss is not None and (best_miss is None or abs(miss["dx"]) > abs(best_miss["dx"])):
                best_miss = miss
        self.last_miss = best_miss
        return None

    def _stroke(self, now: float, seg: List[Tuple[float, float, float, bool, Optional[str], float, float]],
                window: List[Tuple[float, float, float, bool, Optional[str], float, float]]
                ) -> Tuple[Optional[MotionEvent], Optional[Dict[str, Any]]]:
        """(event, None) for a swipe, (None, miss) for a stroke of at least 0.1 that was refused, else (None, None)."""
        t0, x0, y0 = seg[0][0], seg[0][1], seg[0][2]
        t1, x1, y1 = seg[-1][0], seg[-1][1], seg[-1][2]
        dt, dx, dy = t1 - t0, x1 - x0, y1 - y0
        if abs(dx) < 0.10 or dt <= 0:
            return None, None
        path = sum(math.hypot(seg[k + 1][1] - seg[k][1], seg[k + 1][2] - seg[k][2]) for k in range(len(seg) - 1))
        speed = abs(dx) / dt
        direction = 1 if dx > 0 else -1
        why: List[str] = []
        if dt < self.c["swipe_min_time"]:
            why.append("brief")
        if abs(dx) < self.c["swipe_min_dx"]:
            why.append("short")
        if abs(dy) > self.c["swipe_max_ratio"] * abs(dx):
            why.append("vertical")
        if speed < self.c["swipe_min_speed"]:
            why.append("slow")
        if path > self.c["swipe_straight"] * math.hypot(dx, dy):
            why.append("curved")
        # The variant first: with the thumb tucked over the palm the finger ratios read lower (one real session:
        # the same open hand gave a median lowest-finger ratio of 1.0 vs 1.2 with the thumb out), so the
        # "fully extended" and "bent" lines for a tab stroke sit a little lower.
        tucked = sum(1 for w in window if w[5] <= self.c["thumb_tab"])
        variant = "tab" if tucked >= self.c["tab_frames"] else seg[-1][4]
        suffix = "_tab" if variant == "tab" else ""
        open_thr, bent_thr = self.c["open_ratio" + suffix], self.c["open_min" + suffix]
        if not (seg[0][6] >= open_thr and seg[-1][6] >= open_thr):
            why.append("ends_closed")
        elif sum(1 for s in seg if s[6] >= open_thr) / len(seg) < self.c["swipe_open_frac"]:
            why.append("not_open")
        if min(w[6] for w in window) < bent_thr:
            why.append("bent")                # a finger was really folded at some moment of the last 0.6 s
        # the hand must still be travelling that way: a stroke that was refused a moment ago (the return
        # stroke) stays in the window and must not be accepted later, while the hand rests
        recent = [s for s in seg if t1 - s[0] <= 0.15]
        if len(recent) < 2 or (recent[-1][1] - recent[0][1]) * direction < self.c["recent_move"]:
            why.append("stale")
        # (The swing itself moves the thumb: on left strokes it popped OUT, on right strokes it tucked IN, so
        # "tucked" counts if the thumb was clearly tucked at any time in the window, a few frames, not one.)
        if variant is None:
            why.append("thumb_unclear")
        if self._last_swipe_dir == -direction and now - self._last_swipe_t < self.c["return_block"]:
            # the hand coming back: no rest after the last swipe, or clearly slower than that swipe was
            if self.c["reverse_pause"] > 0 and not self._paused_ok:
                why.append("return_stroke")
            elif speed < self.c["reverse_speed"] * self._last_swipe_speed:
                why.append("return_stroke")
        if why:
            return None, {"dx": round(dx, 3), "dt": round(dt, 2), "speed": round(speed, 2), "why": why}
        side = "right" if direction > 0 else "left"
        action = self._actions[variant][side]
        self._last_swipe_t, self._last_swipe_dir, self._last_activity = now, direction, now
        self._last_swipe_speed = speed
        self._settled = False
        self._paused_ok = False
        self._pause_since = None
        self._hist.clear()
        info = {"variant": variant, "side": side, "action": action, "dx": round(dx, 3), "dy": round(dy, 3),
                "seconds": round(dt, 2), "speed": round(speed, 2), "frames": len(seg),
                "straight": round(path / max(1e-6, math.hypot(dx, dy)), 2)}
        self.last_swipe = info
        return MotionEvent("swipe", info, action), None
