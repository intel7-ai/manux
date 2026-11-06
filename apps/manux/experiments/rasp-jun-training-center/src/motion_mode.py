"""
Motion mode (2026-11-04): hand swipes as commands, kept apart from the pinch controls.

Two modes. PINCH (default) is everything that existed before. MOTION is entered on purpose and is
temporary: while it is on, the pointer and the pinches are suspended (nothing moves the cursor, nothing
clicks) and an open hand swiped left or right sends a command. It is a TOGGLE: it stays on until the user
turns it off (the entry pose held again, or the button). `motion_timeout` / `motion_hand_lost_exit` can
still be set (seconds, 0 = never) for anyone who wants it to end by itself.

  toggle  thumb stuck out, the other four fingers folded, held still for `motion_enter_hold`
          (or the dashboard button / `request`). While on, the same pose held again switches it off
          (the pose must have been let go once after entering).
  swipe   all four fingers open, a fast straight horizontal stroke of the palm centre
          thumb out  (variant "nav") : left = back          right = forward
          thumb tucked (variant "tab"): left = previous tab  right = next tab
          thumb in between: no command (too ambiguous)
  mirror  landmarks are already mirrored (x grows to the user's right), so dx > 0 is "right"

Pure logic: feed (time, 21 landmarks | None); no camera, Qt or OS input here. The caller sends the
returned action. Command ids are an allow-list (ACTION_KEYS); the gesture ids and the action ids are
kept separate on purpose, so a mapping can change without touching the detector.

Honest limits: thresholds are starting values chosen from one user's logs (swipes of >= 0.20 frame
widths in 0.4 s were ~0.1 per minute of ordinary pointing/scrolling); how reliable a person's real
swipes are is not measured yet.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

WRIST, MIDDLE_MCP = 0, 9
THUMB_TIP = 4
FINGER_JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))      # (MCP, tip): index, middle, ring, pinky
PALM_POINTS = (0, 5, 9, 13, 17)

# the only commands motion mode may send (key chords for pyautogui.hotkey)
ACTION_KEYS: Dict[str, Tuple[str, ...]] = {
    "back": ("alt", "left"),
    "forward": ("alt", "right"),
    "tab_prev": ("ctrl", "shift", "tab"),
    "tab_next": ("ctrl", "tab"),
    "close_window": ("alt", "f4"),     # only after the two-step confirmation below
}
ACTION_LABELS = {"back": "뒤로", "forward": "앞으로", "tab_prev": "이전 탭", "tab_next": "다음 탭", "close_window": "창 닫기"}
DEFAULT_ACTIONS = {
    "nav": {"left": "back", "right": "forward"},
    "tab": {"left": "tab_prev", "right": "tab_next"},
}

DEFAULTS: Dict[str, Any] = {
    "motion_close_enabled": 1.0,     # 1 = a downward swipe asks to close the window (Alt+F4), a second one within motion_close_confirm does it
    "motion_close_confirm": 3.0,     # s the question stays open
    "motion_close_min_dy": 0.15,     # net downward travel of the palm centre, frame widths
    "motion_close_min_time": 0.10,
    "motion_close_min_speed": 0.7,   # frame widths per s (faster than the horizontal swipes: a hand that is merely lowered is slower)
    "motion_close_max_dx": 0.5,      # |dx| <= this * dy
    "motion_close_cooldown": 0.8,    # s between two downward strokes (the raised hand must come back up first)
    "motion_enter_hold": 0.8,        # s the entry pose must be held
    "motion_enter_still": 0.03,      # palm centre may move this much (frame widths) during the hold
    "motion_enter_grace": 0.2,       # s the pose may drop out during the hold (a noisy frame) without starting over
    "motion_enter_quiet": 1.0,       # s after the last pinch / scroll / drag before entering
    "motion_timeout": 0.0,           # s without a swipe and motion mode ends (0 = it stays on: toggle)
    "motion_hand_lost_exit": 0.0,    # s without a hand and it ends (0 = never)
    "motion_thumb_out": 0.85,        # thumb_tuck >= this = thumb stuck out (entry pose; 5 % of ordinary fists reach 0.9)
    "motion_thumb_straight": 0.85,   # the thumb counts as stretched out at this straightness (tip-to-base over the three segments): the
                                     # logs show 0.95-1.0 for a thumbs-up and 0.4-0.7 for every bent / folded / tucked thumb
    "motion_thumb_up": 0.7,          # the entry thumb must also point UP (cos of its angle to straight up >= this, ~45 deg): thumbs-up. A
                                     # stretched thumb pointing elsewhere is kept free for other commands (logs: thumbs-up 0.8-1.0)
    "motion_thumb_len": 0.6,         # the thumb tip must also be this far (palm lengths) from its base: a fist whose thumb lies on the
                                     # index is far from the ring/pinky knuckles too (tuck 1.2-1.4) but its thumb is folded (0.2-0.3).
                                     # 0.5 until 2026-11-04 21:15: a fist turned knuckles-up with the bent thumb's tip toward the face
                                     # read 0.48-0.6 and toggled the mode; real thumbs-ups 0.66-1.0
    "motion_min_palm_ratio": 0.7,    # that knuckles-up fist shows the palm edge-on: width / length 0.44-0.63 vs 0.8-1.4 for a thumbs-up
    "motion_max_palm_ratio": 1.7,    # a fist pointing at the camera foreshortens the palm (width / length 2.0-2.2 in the logs vs 0.9-1.5 for a
                                     # thumbs-up), which inflates every thumb measure and made the pose look like a thumbs-up
    "motion_fold_ratio": 1.05,       # finger curl ratio <= this = folded. A thumbs-up hand sits at 0.7-0.9 (median 0.77); a half fist
                                     # (fingers loosely curled, the hand moving the cursor at 5-15x) at 1.0-1.4: 1.2 let it toggle the mode
    "motion_open_ratio": 1.3,        # a finger counts as fully extended at this curl ratio (extended ones sit at 1.5-1.8)
    "motion_open_min": 1.0,          # a stroke with any finger below this in any frame is refused ("bent")
    "motion_open_ratio_tab": 1.1,    # the same two lines for a stroke with the thumb tucked (finger ratios read lower then;
    "motion_open_min_tab": 0.8,      # 1.25 / 0.95 until 2026-11-04 23:15: tab strokes were refused as "not open" / "bent")
    "motion_thumb_nav": 0.75,        # open hand + thumb_tuck >= this -> "nav"
    "motion_thumb_tab": 0.45,        # open hand + thumb_tuck <= this -> "tab"
    "motion_swipe_min_dx": 0.15,     # net horizontal travel, frame widths
    "motion_swipe_max_ratio": 0.5,   # |dy| <= ratio * |dx|
    "motion_swipe_window": 0.6,      # s history looked at
    "motion_swipe_min_time": 0.12,   # shortest stroke, s
    "motion_swipe_straight": 1.5,    # path length <= this * net distance
    "motion_swipe_min_speed": 0.6,   # net distance / time, frame widths per s (the hand coming back is slower: 0.37 vs 0.48 median)
    "motion_swipe_min_speed_tab": 0.4,  # the same for a stroke with the thumb tucked: tab strokes ran 0.4-0.58 in the 23:10 log
                                        # (refused as "slow"); returning strokes are still caught by the return rules below
    "motion_swipe_open_frac": 0.85,  # share of the stroke's frames with ALL FOUR fingers fully extended
    "motion_swipe_cooldown": 0.5,    # s between swipes
    "motion_reverse_pause": 0.25,    # s the hand must rest after a swipe before the OPPOSITE direction counts
    "motion_pause_speed": 0.15,      # below this (frame widths/s) the hand counts as resting
    "motion_reverse_speed": 0.6,     # ... and the opposite stroke must be at least this share of the last swipe's speed
    "motion_return_block": 1.5,      # s after a swipe in which an opposite stroke without that rest is the hand returning
    "motion_recent_move": 0.02,      # travel in the last 0.15 s (frame widths) for a stroke to count as happening now
    "motion_tab_frames": 3,          # frames with a clearly tucked thumb in the last 0.6 s that make a stroke a "tab" stroke
    "motion_settle_speed": 0.2,      # after a swipe the hand must slow below this (frame widths/s) before the next one counts
}


def _xyz(p: Any) -> Tuple[float, float, float]:
    if hasattr(p, "x"):
        return float(p.x), float(p.y), float(getattr(p, "z", 0.0))
    return float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0


def hand_features(landmarks: Sequence[Any]) -> Dict[str, Any]:
    """Finger curl ratios (tip-to-wrist / MCP-to-wrist, 3D like the pinch engine), thumb tuck and palm centre."""
    pts = [_xyz(p) for p in landmarks[:21]]

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
    cx = sum(pts[i][0] for i in PALM_POINTS) / len(PALM_POINTS)
    cy = sum(pts[i][1] for i in PALM_POINTS) / len(PALM_POINTS)
    thumb_len = d2(pts[THUMB_TIP], pts[2]) / palm_len     # thumb tip from its base: ~0.2-0.35 folded, 0.65-1.2 stuck out
    segs = d3(pts[1], pts[2]) + d3(pts[2], pts[3]) + d3(pts[3], pts[4])
    thumb_straight = d3(pts[1], pts[4]) / segs if segs > 1e-6 else 0.0   # 1.0 = the thumb is stretched out, wherever it points; 0.4-0.7 bent
    tdx, tdy = pts[THUMB_TIP][0] - pts[2][0], pts[THUMB_TIP][1] - pts[2][1]
    thumb_up = -tdy / math.hypot(tdx, tdy) if math.hypot(tdx, tdy) > 1e-6 else 0.0   # cos of the angle between the thumb and "up" on the screen
    palm_ratio = d2(pts[5], pts[17]) / palm_len           # palm width / length: ~0.9-1.3 palm to the camera, 2+ when a fist points AT it
    return {"curl": tuple(curl), "thumb_tuck": tuck, "thumb_len": thumb_len, "thumb_straight": thumb_straight, "thumb_up": thumb_up, "palm_ratio": palm_ratio, "palm": (cx, cy)}


@dataclass
class MotionEvent:
    kind: str                       # "enter" | "exit" | "swipe"
    info: Dict[str, Any] = field(default_factory=dict)
    action: Optional[str] = None    # swipe: the command id (a key of ACTION_KEYS)


class MotionMode:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = dict(DEFAULTS)
        if config:
            self.config.update({k: v for k, v in config.items() if k in DEFAULTS})
        self._actions: Dict[str, Dict[str, str]] = {v: dict(m) for v, m in DEFAULT_ACTIONS.items()}
        if config and isinstance(config.get("motion_actions"), dict):
            for variant, mapping in config["motion_actions"].items():
                if variant in self._actions and isinstance(mapping, dict):
                    for side, action in mapping.items():
                        if side in ("left", "right") and action in ACTION_KEYS and action != "close_window":
                            self._actions[variant][side] = action
        self.active = False
        self._entered_at = 0.0
        self._last_activity = 0.0
        self._last_hand_t = 0.0
        self._enter_since: Optional[float] = None
        self._enter_anchor: Optional[Tuple[float, float]] = None
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
        self._close_ask_t = -1e9         # when the "close the window?" question was asked
        self._close_last_t = -1e9
        self.last_features: Optional[Dict[str, Any]] = None
        self.last_swipe: Optional[Dict[str, Any]] = None
        self.entered_by = ""

    # ------------------------------------------------------------------ helpers
    def cfg(self, key: str) -> float:
        return float(self.config[key])

    def remaining(self, now: float) -> float:
        """Seconds until the timeout ends motion mode (0 when it is off)."""
        timeout = self.cfg("motion_timeout")
        if not self.active or timeout <= 0:
            return 0.0
        return max(0.0, timeout - (now - self._last_activity))

    def entering(self, now: float) -> float:
        """0..1 while the entry pose is being held (to show 'entering...'), 0 otherwise."""
        if self.active or self._enter_since is None:
            return 0.0
        return max(0.0, min(1.0, (now - self._enter_since) / self.cfg("motion_enter_hold")))

    def leaving(self, now: float) -> float:
        """0..1 while the pose is held again to switch the mode off, 0 otherwise."""
        if not self.active or self._exit_since is None:
            return 0.0
        return max(0.0, min(1.0, (now - self._exit_since) / self.cfg("motion_enter_hold")))

    def close_pending(self, now: float) -> float:
        """Seconds left to confirm closing the window (0 = nothing asked)."""
        return max(0.0, self.cfg("motion_close_confirm") - (now - self._close_ask_t)) if self.active else 0.0

    def thumb_variant(self, tuck: float) -> Optional[str]:
        if tuck >= self.cfg("motion_thumb_nav"):
            return "nav"
        if tuck <= self.cfg("motion_thumb_tab"):
            return "tab"
        return None

    def entry_pose(self, f: Dict[str, Any]) -> bool:
        """Thumb stuck out and the other four fingers folded."""
        # The thumb is "out" when it is stretched (straight) and long, wherever it points. It used to be "far from the ring and
        # little-finger knuckles", which depends on where the thumb is: a thumbs-up pointing sideways failed, a fist with the
        # thumb bent underneath passed.
        straight = f.get("thumb_straight")
        thumb_out = (straight >= self.cfg("motion_thumb_straight")) if straight is not None             else f["thumb_tuck"] >= self.cfg("motion_thumb_out")
        return (thumb_out
                and f.get("thumb_up", 1.0) >= self.cfg("motion_thumb_up")
                and f.get("thumb_len", 9.9) >= self.cfg("motion_thumb_len")
                and self.cfg("motion_min_palm_ratio") <= f.get("palm_ratio", 1.0) <= self.cfg("motion_max_palm_ratio")
                and max(f["curl"]) <= self.cfg("motion_fold_ratio"))

    def open_hand(self, f: Dict[str, Any]) -> bool:
        """All four fingers fully extended (a swipe is only read from a completely open hand, so that other
        hand shapes stay free for other motions)."""
        return min(f["curl"]) >= self.cfg("motion_open_ratio")

    def pose(self, f: Dict[str, Any]) -> str:
        """A coarse name of the hand shape: entry | open | fist | other (for logs and later motions)."""
        if self.entry_pose(f):
            return "entry"
        if self.open_hand(f):
            return "open"
        if max(f["curl"]) <= self.cfg("motion_fold_ratio"):
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
        self._close_ask_t = self._close_last_t = -1e9
        return MotionEvent("enter", {"how": how})

    def _exit(self, now: float, why: str) -> MotionEvent:
        self.active = False
        self._hist.clear()
        self._enter_since = None
        self._exit_since = None
        self._close_ask_t = -1e9
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
        lost_exit, timeout = self.cfg("motion_hand_lost_exit"), self.cfg("motion_timeout")
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
                if now - self._exit_lost_since <= self.cfg("motion_enter_grace"):
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
                > self.cfg("motion_enter_still"):
            self._exit_since, self._exit_anchor = now, palm
            return None
        if now - self._exit_since >= self.cfg("motion_enter_hold"):
            return self._exit(now, "pose")
        return None

    def _watch_entry(self, now: float, feats: Optional[Dict[str, Any]], blocked: bool) -> Optional[MotionEvent]:
        if feats is None or blocked or not self.entry_pose(feats):
            # a noisy frame must not throw away a hold in progress: the pose may drop out for motion_enter_grace
            if self._enter_since is not None and feats is not None and not blocked:
                if self._enter_lost_since is None:
                    self._enter_lost_since = now
                if now - self._enter_lost_since <= self.cfg("motion_enter_grace"):
                    return None
            self._enter_since = None
            self._enter_lost_since = None
            return None
        self._enter_lost_since = None
        palm = feats["palm"]
        if self._enter_since is None:
            self._enter_since, self._enter_anchor = now, palm
            return None
        if math.hypot(palm[0] - self._enter_anchor[0], palm[1] - self._enter_anchor[1]) > self.cfg("motion_enter_still"):
            self._enter_since, self._enter_anchor = now, palm       # the hand is travelling: hold starts over
            return None
        if now - self._enter_since >= self.cfg("motion_enter_hold"):
            return self._enter(now, "pose")
        return None

    def _watch_swipe(self, now: float, f: Dict[str, Any]) -> Optional[MotionEvent]:
        win = self.cfg("motion_swipe_window")
        if self.entry_pose(f):
            self._hist.clear()                # the toggle pose is not a swipe pose (and it is not "a bent finger")
            self._lows = []
            return None
        tuck = f["thumb_tuck"]
        # a state with hysteresis: a frame in between keeps the last decision
        if tuck >= self.cfg("motion_thumb_nav"):
            self._thumb_state = "nav"
        elif tuck <= self.cfg("motion_thumb_tab"):
            self._thumb_state = "tab"
        # The finger ratios are noisy, most of all when the thumb is tucked over the palm (one real session: only
        # 26-35 % of the thumb-tucked strokes passed "all four extended" against ~60 % with the thumb out, and a
        # single noisy frame refused a stroke). The lowest finger ratio is the median of the last three frames.
        low_raw = min(f["curl"])
        self._lows = (self._lows + [low_raw])[-3:]
        low = sorted(self._lows)[len(self._lows) // 2]
        self._hist.append((now, f["palm"][0], f["palm"][1], low >= self.cfg("motion_open_ratio"), self._thumb_state,
                           tuck, low))
        while self._hist and now - self._hist[0][0] > win:
            self._hist.popleft()
        if len(self._hist) >= 3:
            a, b = self._hist[-3], self._hist[-1]
            vx = abs(b[1] - a[1]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
            if not self._settled and vx < self.cfg("motion_settle_speed"):
                self._settled = True      # the stroke is over; without this its second half read as another swipe
            # Repeating a swipe means bringing the hand back, and that movement reads as the opposite swipe.
            # The opposite direction therefore only counts after the hand has RESTED for a moment.
            if self._settled and not self._paused_ok:
                if vx < self.cfg("motion_pause_speed"):
                    self._pause_since = self._pause_since if self._pause_since is not None else now
                    if now - self._pause_since >= self.cfg("motion_reverse_pause"):
                        self._paused_ok = True
                else:
                    self._pause_since = None
        if not self._settled or now - self._last_swipe_t < self.cfg("motion_swipe_cooldown"):
            return None
        h = list(self._hist)
        if self.cfg("motion_close_enabled") >= 1.0:
            down = self._down_stroke(now, h)
            if down is not None:
                return down
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

    def _down_stroke(self, now: float, h: List[Tuple[float, float, float, bool, Optional[str], float, float]]
                     ) -> Optional[MotionEvent]:
        """A fast straight DOWNWARD stroke of the open hand: the first asks to close the window, a second within
        motion_close_confirm closes it. (Image y grows downward, so dy > 0 is down.)"""
        if now - self._close_last_t < self.cfg("motion_close_cooldown"):
            return None
        for start in range(len(h) - 3):
            seg = h[start:]
            dt, dx, dy = seg[-1][0] - seg[0][0], seg[-1][1] - seg[0][1], seg[-1][2] - seg[0][2]
            if dy < self.cfg("motion_close_min_dy") or dt < self.cfg("motion_close_min_time"):
                continue
            if abs(dx) > self.cfg("motion_close_max_dx") * dy or dy / dt < self.cfg("motion_close_min_speed"):
                continue
            path = sum(math.hypot(seg[k + 1][1] - seg[k][1], seg[k + 1][2] - seg[k][2]) for k in range(len(seg) - 1))
            if path > self.cfg("motion_swipe_straight") * math.hypot(dx, dy):
                continue
            tucked = sum(1 for w in h if w[5] <= self.cfg("motion_thumb_tab"))
            suffix = "_tab" if tucked >= self.cfg("motion_tab_frames") else ""
            open_thr, bent_thr = self.cfg("motion_open_ratio" + suffix), self.cfg("motion_open_min" + suffix)
            if not (seg[0][6] >= open_thr and seg[-1][6] >= open_thr):
                continue
            if sum(1 for q in seg if q[6] >= open_thr) / len(seg) < self.cfg("motion_swipe_open_frac"):
                continue
            if min(w[6] for w in h) < bent_thr:
                continue
            recent = [q for q in seg if seg[-1][0] - q[0] <= 0.15]
            if len(recent) < 2 or recent[-1][2] - recent[0][2] < self.cfg("motion_recent_move"):
                continue
            info = {"dx": round(dx, 3), "dy": round(dy, 3), "seconds": round(dt, 2), "speed": round(dy / dt, 2)}
            self._close_last_t = self._last_activity = now
            self._settled = False
            self._hist.clear()
            if now - self._close_ask_t <= self.cfg("motion_close_confirm"):
                self._close_ask_t = -1e9
                self.last_swipe = {**info, "variant": "down", "side": "down", "action": "close_window"}
                return MotionEvent("swipe", self.last_swipe, "close_window")
            self._close_ask_t = now
            return MotionEvent("close_ask", info)
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
        if dt < self.cfg("motion_swipe_min_time"):
            why.append("brief")
        if abs(dx) < self.cfg("motion_swipe_min_dx"):
            why.append("short")
        if abs(dy) > self.cfg("motion_swipe_max_ratio") * abs(dx):
            why.append("vertical")
        # The variant first: with the thumb tucked over the palm the finger ratios read lower (one real session:
        # the same open hand gave a median lowest-finger ratio of 1.0 vs 1.2 with the thumb out), so the
        # "fully extended" and "bent" lines for a tab stroke sit a little lower - and tab strokes were slower too.
        tucked = sum(1 for w in window if w[5] <= self.cfg("motion_thumb_tab"))
        variant = "tab" if tucked >= self.cfg("motion_tab_frames") else seg[-1][4]
        suffix = "_tab" if variant == "tab" else ""
        if speed < self.cfg("motion_swipe_min_speed" + suffix):
            why.append("slow")
        if path > self.cfg("motion_swipe_straight") * math.hypot(dx, dy):
            why.append("curved")
        open_thr, bent_thr = self.cfg("motion_open_ratio" + suffix), self.cfg("motion_open_min" + suffix)
        if not (seg[0][6] >= open_thr and seg[-1][6] >= open_thr):
            why.append("ends_closed")
        elif sum(1 for s in seg if s[6] >= open_thr) / len(seg) < self.cfg("motion_swipe_open_frac"):
            why.append("not_open")
        if min(w[6] for w in window) < bent_thr:
            why.append("bent")                # a finger was really folded at some moment of the last 0.6 s
        # the hand must still be travelling that way: a stroke that was refused a moment ago (the return
        # stroke) stays in the window and must not be accepted later, while the hand rests
        recent = [s for s in seg if t1 - s[0] <= 0.15]
        if len(recent) < 2 or (recent[-1][1] - recent[0][1]) * direction < self.cfg("motion_recent_move"):
            why.append("stale")
        # (The swing itself moves the thumb: on left strokes it popped OUT, on right strokes it tucked IN, so
        # "tucked" counts if the thumb was clearly tucked at any time in the window, a few frames, not one.)
        if variant is None:
            why.append("thumb_unclear")
        if self._last_swipe_dir == -direction and now - self._last_swipe_t < self.cfg("motion_return_block"):
            # the hand coming back: no rest after the last swipe, or clearly slower than that swipe was
            if self.cfg("motion_reverse_pause") > 0 and not self._paused_ok:
                why.append("return_stroke")
            elif speed < self.cfg("motion_reverse_speed") * self._last_swipe_speed:
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
