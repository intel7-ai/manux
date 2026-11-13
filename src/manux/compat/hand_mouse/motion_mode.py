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

from manux.compat.hand_mouse.paths import DATA_ROOT

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

ROOT = DATA_ROOT
DEFAULT_MOTION_TEMPLATES_PATH = ROOT / "models" / "motion_templates.json"

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
    "close_window": ("alt", "f4"),     # the rock sign (two steps); motion mode no longer sends it
    "window_down": ("win", "down"),    # the downward stroke (2026-11-11, was the Alt+F4 question): restore / minimise
}
ACTION_LABELS = {"back": "뒤로", "forward": "앞으로", "tab_prev": "이전 탭", "tab_next": "다음 탭", "close_window": "창 닫기", "window_down": "창 내리기"}
DEFAULT_ACTIONS = {
    "nav": {"left": "back", "right": "forward"},
    "tab": {"left": "tab_prev", "right": "tab_next"},
}

DEFAULTS: Dict[str, Any] = {
    "motion_close_enabled": 1.0,     # 1 = a downward swipe brings the window down (Win+Down); the name is from when it closed the window
    "motion_close_confirm": 3.0,     # unused since 2026-11-11 (the Alt+F4 question is gone)
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
    depth_3d = max((abs(p[2] - wrist[2]) for p in pts), default=0.0)
    # thumb tip -> index / middle / ring / pinky tip, in palm lengths (a pinch is ~0.1-0.25)
    pinch = tuple(d2(pts[THUMB_TIP], pts[tip]) / palm_len for tip in (8, 12, 16, 20))
    return {"curl": tuple(curl), "thumb_tuck": tuck, "thumb_len": thumb_len, "thumb_straight": thumb_straight, "thumb_up": thumb_up, "palm_ratio": palm_ratio, "palm": (cx, cy), "depth_3d": depth_3d, "pinch": pinch,
            "lm": landmarks}           # for the shapes judged from the joints (src/pose_probe.hand_posture)


def _thumbs_up(f: Dict[str, Any]) -> bool:
    """Same test as motion mode's entry pose (four fingers folded, thumb pointing up; a knuckles-up fist is not one)."""
    global _ENTRY_CHECK
    if _ENTRY_CHECK is None:
        _ENTRY_CHECK = MotionMode()
    return bool(_ENTRY_CHECK.entry_pose(f))


_ENTRY_CHECK = None
CUSTOM_PINCH_MAX = 0.32      # thumb tip within this many palm lengths of a fingertip = that pinch
_PINCH_FINGER = {"thumb_index": 0, "thumb_middle": 1, "thumb_ring": 2, "thumb_pinky": 3}


def custom_posture_ok(posture: str, f: Dict[str, Any]) -> bool:
    """Does the hand (hand_features) have the shape a user-registered motion asks for?

    Every posture the registration form offers (action_bindings.POSTURE_NAMES_KO) is checked here;
    an unknown name never matches, so a typo cannot turn into "any hand shape"."""
    curls = f.get("curl", (1.5, 1.5, 1.5, 1.5))
    tuck = float(f.get("thumb_tuck", 0.8))
    if posture == "any":
        return True
    if posture == "open_hand":
        return min(curls) >= 1.22
    if posture == "thumb_tucked":
        return min(curls) >= 1.05 and tuck <= 0.52
    if posture == "v_sign":
        return curls[0] >= 1.45 and curls[1] >= 1.45 and curls[2] <= 1.25 and curls[3] <= 1.25
    if posture == "rock_sign":
        return curls[0] >= 1.45 and curls[3] >= 1.38 and curls[1] <= 1.25 and curls[2] <= 1.25
    if posture == "fist":
        return max(curls) <= 1.18 and not _thumbs_up(f)
    if posture == "thumbs_up":
        return _thumbs_up(f)
    if posture in ("knife_hand", "fingers_bent", "v_sign_down", "two_fingers", "index_point", "l_sign", "pinky_up", "shaka",
                   "thumbs_down", "open_hand_back", "open_hand_down", "open_hand_up") or posture.startswith("custom_"):      # custom_: a shape the user named and taught
        # shapes added 2026-11-10 (손날, ㄱ자, 거꾸로 V): the same judge as the shape probe and the sequences
        lm = f.get("lm")
        if lm is None or len(lm) < 21:
            return False
        if not hasattr(lm[0], "x"):                      # recordings keep the joints as [x, y, z] lists
            from types import SimpleNamespace
            lm = [SimpleNamespace(x=p[0], y=p[1], z=p[2] if len(p) > 2 else 0.0) for p in lm]
        from manux.compat.hand_mouse.pose_probe import hand_posture
        return hand_posture(lm, 720 / 1280) == posture
    if posture in _PINCH_FINGER:
        pinch = f.get("pinch")
        if not pinch:
            return False
        i = _PINCH_FINGER[posture]
        return pinch[i] <= CUSTOM_PINCH_MAX and pinch[i] == min(pinch)
    return False


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
        self._recent_f: Deque[Tuple[float, Dict[str, Any]]] = deque()   # hand features of the last 0.8 s
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
        thumb_out = (straight >= self.cfg("motion_thumb_straight")) if straight is not None else f["thumb_tuck"] >= self.cfg("motion_thumb_out")
        curls = sorted(float(c) for c in f["curl"])
        fold_limit = self.cfg("motion_fold_ratio")
        curl_mean = sum(curls) / max(1, len(curls))
        palm_r = f.get("palm_ratio", 1.0)
        real_3d = float(f.get("depth_3d", 0.0)) > 1e-4
        angled_thumbs_up = (
            straight is not None
            and straight >= 0.94
            and f.get("thumb_up", 1.0) >= 0.78
            and (palm_r >= 1.15 or (real_3d and straight >= 0.97 and f.get("thumb_up", 1.0) >= 0.88))
        )
        folded = (
            curls[-1] <= fold_limit
            or (
                fold_limit >= 1.0
                and len(curls) >= 4
                and curls[2] <= fold_limit
                and curls[-1] <= fold_limit + 0.22
                and curl_mean <= 1.05
            )
            or (
                angled_thumbs_up
                and fold_limit >= 1.0
                and len(curls) >= 4
                and curls[0] <= 1.12
                and curls[1] <= 1.30
                and curl_mean <= 1.28
            )
        )
        min_palm = self.cfg("motion_min_palm_ratio")
        max_palm = self.cfg("motion_max_palm_ratio")
        if straight is not None and straight >= 0.90 and (curl_mean <= 1.05 or angled_thumbs_up):
            min_palm = min(min_palm, 0.55)
            max_palm = max(max_palm, 30.0)
        return (thumb_out
                and f.get("thumb_up", 1.0) >= self.cfg("motion_thumb_up")
                and f.get("thumb_len", 9.9) >= self.cfg("motion_thumb_len")
                and min_palm <= palm_r <= max_palm
                and folded)

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
            self._enter_history = [(now, palm)]
            return None
        still_limit = self.cfg("motion_enter_still")
        if float(feats.get("depth_3d", 0.0)) > 1e-4:
            still_limit = max(still_limit, 0.06)
        hist = getattr(self, "_enter_history", None) or []
        hist.append((now, palm))
        self._enter_history = hist
        if math.hypot(palm[0] - self._enter_anchor[0], palm[1] - self._enter_anchor[1]) > still_limit:
            while len(hist) > 1 and math.hypot(palm[0] - hist[0][1][0], palm[1] - hist[0][1][1]) > still_limit:
                hist.pop(0)
            self._enter_since, self._enter_anchor = hist[0]
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
        self._recent_f.append((now, f))
        while self._recent_f and now - self._recent_f[0][0] > .8:
            self._recent_f.popleft()
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
        # the user's own motions first (2026-11-10: a registered "손날 좌하단" slash was taken by the built-in
        # "down stroke = ask to close the window" and never ran)
        custom_ev = self._custom_motion_stroke(now, h, f)
        if custom_ev is not None:
            return custom_ev
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

    def _custom_motion_stroke(
        self,
        now: float,
        h: List[Tuple[float, float, float, bool, Optional[str], float, float]],
        f: Dict[str, Any],
    ) -> Optional[MotionEvent]:
        """Evaluate user-registered temporal motion gestures (e.g. up/down/diagonal/custom posture swipes)."""
        if len(h) < 4:
            return None
        try:
            from manux.compat.hand_mouse.action_bindings import get_action_registry
            custom_motions = get_action_registry().custom_motions
        except Exception:
            return None
        if not custom_motions:
            return None

        for m_key, m_def in custom_motions.items():
            if not m_def.get("enabled", True):
                continue
            req_posture = str(m_def.get("posture", "open_hand"))
            if not custom_posture_ok(req_posture, f):
                # a fast stroke blurs the hand: the shape held for about half of the stroke is enough
                stroke_start = h[0][0]
                seen = [ff for tt, ff in self._recent_f if tt >= stroke_start]
                if not seen or sum(custom_posture_ok(req_posture, ff) for ff in seen) < max(2, len(seen) // 2):
                    continue

            min_dist = float(m_def.get("min_distance", 0.14))
            min_spd = float(m_def.get("min_speed", 0.25))
            target_dir = str(m_def.get("direction", "right"))

            for start in range(len(h) - 3):
                seg = h[start:]
                dt = seg[-1][0] - seg[0][0]
                if dt < 0.08:
                    continue
                dx = seg[-1][1] - seg[0][1]
                dy = seg[-1][2] - seg[0][2]
                dist = math.hypot(dx, dy)
                if dist < min_dist:
                    continue
                speed = dist / dt
                if speed < min_spd:
                    continue
                path = sum(math.hypot(seg[k + 1][1] - seg[k][1], seg[k + 1][2] - seg[k][2]) for k in range(len(seg) - 1))
                if path > 1.65 * dist:
                    continue
                dir_ok = False
                if target_dir == "left" and dx <= -min_dist and abs(dx) >= abs(dy) * 1.3:
                    dir_ok = True
                elif target_dir == "right" and dx >= min_dist and abs(dx) >= abs(dy) * 1.3:
                    dir_ok = True
                elif target_dir == "up" and dy <= -min_dist and abs(dy) >= abs(dx) * 1.3:
                    dir_ok = True
                elif target_dir == "down" and dy >= min_dist and abs(dy) >= abs(dx) * 1.3:
                    dir_ok = True
                elif target_dir == "diag_up_left" and dx <= -min_dist * 0.55 and dy <= -min_dist * 0.55:
                    dir_ok = True
                elif target_dir == "diag_up_right" and dx >= min_dist * 0.55 and dy <= -min_dist * 0.55:
                    dir_ok = True
                elif target_dir == "diag_down_left" and dx <= -min_dist * 0.55 and dy >= min_dist * 0.55:
                    dir_ok = True
                elif target_dir == "diag_down_right" and dx >= min_dist * 0.55 and dy >= min_dist * 0.55:
                    dir_ok = True

                if dir_ok:
                    self._last_swipe_t = self._last_activity = now
                    self._last_swipe_speed = speed
                    self._settled = False
                    self._paused_ok = False
                    self._pause_since = None
                    self._hist.clear()
                    info = {
                        "variant": req_posture,
                        "side": target_dir,
                        "action": m_key,
                        "dx": round(dx, 3),
                        "dy": round(dy, 3),
                        "seconds": round(dt, 2),
                        "speed": round(speed, 2),
                        "frames": len(seg),
                        "straight": round(path / max(1e-6, dist), 2),
                    }
                    self.last_swipe = info
                    return MotionEvent("swipe", info, m_key)
        return None

    def _down_stroke(self, now: float, h: List[Tuple[float, float, float, bool, Optional[str], float, float]]
                     ) -> Optional[MotionEvent]:
        """A fast straight DOWNWARD stroke of the open hand brings the window down (Win+Down) at once. Until 2026-11-11 it
        asked to close the window and a second stroke sent Alt+F4. (Image y grows downward, so dy > 0 is down.)"""
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
            self.last_swipe = {**info, "variant": "down", "side": "down", "action": "window_down"}
            return MotionEvent("swipe", self.last_swipe, "window_down")
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


# ---------------------------------------------------------------------------
# Dedicated Time-Series Motion Learning & Trajectory Template Engine
# (Separate from Static Single-Frame Posture Learning)
# ---------------------------------------------------------------------------
def _resample_series_1d(values: Sequence[float], steps: int = 16) -> List[float]:
    if not values:
        return [0.0] * steps
    if len(values) == 1:
        return [float(values[0])] * steps
    n = len(values)
    out: List[float] = []
    for k in range(steps):
        pos = (k / max(1, steps - 1)) * (n - 1)
        i0 = int(math.floor(pos))
        i1 = min(n - 1, i0 + 1)
        frac = pos - i0
        v = float(values[i0]) * (1.0 - frac) + float(values[i1]) * frac
        out.append(round(v, 4))
    return out


def extract_motion_window_features(
    frame_records: Sequence[Dict[str, Any]],
    *,
    resample_steps: int = 16,
) -> Optional[Dict[str, Any]]:
    """
    Extract time-series trajectory, velocity profile, and hand posture sequence features
    from a window of motion frames (each containing 't' and 'landmarks' or 'palm').

    Unlike static single-frame learning (SpatialMLP), this captures:
    1. Active stroke window isolation (trimming pre-stroke idle & post-stroke settle)
    2. 16-step normalized relative 2D trajectory [(dx_k, dy_k)]
    3. 16-step velocity profile [v_k] (acceleration -> peak -> deceleration)
    4. Net displacement, primary direction vector, path straightness, and finger/thumb state over time.
    """
    samples: List[Tuple[float, float, float, float, float, float]] = []
    for rec in frame_records:
        t = float(rec.get("t", len(samples) / 30.0))
        lm = rec.get("landmarks")
        if lm and len(lm) >= 21:
            hf = hand_features(lm)
            px, py = hf["palm"]
            curl_min = float(min(hf["curl"]))
            curl_mean = float(sum(hf["curl"]) / len(hf["curl"]))
            tuck = float(hf["thumb_tuck"])
            samples.append((t, px, py, curl_min, curl_mean, tuck))
        elif "palm" in rec and rec["palm"]:
            px, py = float(rec["palm"][0]), float(rec["palm"][1])
            samples.append((t, px, py, float(rec.get("curl_min", 1.5)), float(rec.get("curl_mean", 1.5)), float(rec.get("thumb_tuck", 0.8))))

    if len(samples) < 4:
        return None

    # Find the sub-segment with strongest directional stroke momentum
    best_seg = samples
    best_score = -1.0
    n = len(samples)
    for i in range(n - 3):
        for j in range(i + 3, n):
            dt = samples[j][0] - samples[i][0]
            if dt < 0.08 or dt > 1.25:
                continue
            dx = samples[j][1] - samples[i][1]
            dy = samples[j][2] - samples[i][2]
            dist = math.hypot(dx, dy)
            if dist < 0.05:
                continue
            path = sum(
                math.hypot(samples[k + 1][1] - samples[k][1], samples[k + 1][2] - samples[k][2])
                for k in range(i, j)
            )
            straight_ratio = path / max(1e-6, dist)
            if straight_ratio > 2.2:
                continue
            spd = dist / dt
            score = dist * (1.0 + 0.35 * min(2.5, spd)) / max(1.0, straight_ratio)
            if score > best_score:
                best_score = score
                best_seg = samples[i : j + 1]

    t0, x0, y0 = best_seg[0][0], best_seg[0][1], best_seg[0][2]
    t1, x1, y1 = best_seg[-1][0], best_seg[-1][1], best_seg[-1][2]
    dt = max(1e-3, t1 - t0)
    dx = x1 - x0
    dy = y1 - y0
    net_dist = math.hypot(dx, dy)
    if net_dist < 0.04:
        return None

    path_len = sum(
        math.hypot(best_seg[k + 1][1] - best_seg[k][1], best_seg[k + 1][2] - best_seg[k][2])
        for k in range(len(best_seg) - 1)
    )
    straightness = path_len / max(1e-6, net_dist)
    mean_speed = net_dist / dt

    inst_speeds: List[float] = [0.0]
    for k in range(len(best_seg) - 1):
        sdt = max(1e-3, best_seg[k + 1][0] - best_seg[k][0])
        sd = math.hypot(best_seg[k + 1][1] - best_seg[k][1], best_seg[k + 1][2] - best_seg[k][2])
        inst_speeds.append(sd / sdt)
    peak_speed = max(inst_speeds) if inst_speeds else mean_speed

    scale = max(0.05, net_dist)
    rel_x = [(s[1] - x0) / scale for s in best_seg]
    rel_y = [(s[2] - y0) / scale for s in best_seg]
    norm_v = [v / max(1e-3, peak_speed) for v in inst_speeds]

    traj_x = _resample_series_1d(rel_x, resample_steps)
    traj_y = _resample_series_1d(rel_y, resample_steps)
    vel_prof = _resample_series_1d(norm_v, resample_steps)

    ux, uy = dx / net_dist, dy / net_dist
    angle_deg = math.degrees(math.atan2(dy, dx))
    curl_mins = [s[3] for s in best_seg]
    curl_means = [s[4] for s in best_seg]
    tucks = [s[5] for s in best_seg]

    return {
        "duration": round(dt, 4),
        "dx": round(dx, 4),
        "dy": round(dy, 4),
        "net_dist": round(net_dist, 4),
        "path_len": round(path_len, 4),
        "straightness": round(straightness, 4),
        "mean_speed": round(mean_speed, 4),
        "peak_speed": round(peak_speed, 4),
        "direction_unit": [round(ux, 4), round(uy, 4)],
        "direction_angle_deg": round(angle_deg, 2),
        "curl_min": round(min(curl_mins), 4),
        "curl_mean": round(sum(curl_means) / len(curl_means), 4),
        "thumb_tuck_mean": round(sum(tucks) / len(tucks), 4),
        "thumb_tuck_min": round(min(tucks), 4),
        "trajectory_x": traj_x,
        "trajectory_y": traj_y,
        "velocity_profile": vel_prof,
        "frames": len(best_seg),
    }


class MotionTrajectoryModel:
    """
    Time-series motion template classifier & verifier.
    Evaluates temporal trajectory similarity, velocity envelope, and hand-shape sequence consistency.
    """

    def __init__(self, templates: Optional[Dict[str, Dict[str, Any]]] = None, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_MOTION_TEMPLATES_PATH
        self.templates: Dict[str, Dict[str, Any]] = dict(templates or {})

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "MotionTrajectoryModel":
        target = Path(path) if path is not None else DEFAULT_MOTION_TEMPLATES_PATH
        if target.is_file():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("templates"), dict):
                    return cls(templates=data["templates"], path=target)
            except Exception:
                pass
        return cls(templates={}, path=target)

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path) if path is not None else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "motion-trajectory-v1",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "templates": self.templates,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def score_stroke(self, gesture_key: str, stroke_feats: Dict[str, Any]) -> Dict[str, Any]:
        """Compute time-series trajectory match score (0.0 .. 1.0) against a learned motion template."""
        tmpl = self.templates.get(gesture_key)
        if not tmpl or not stroke_feats:
            return {"matched": False, "overall_score": 0.0, "reason": "no_template"}

        ux, uy = stroke_feats.get("direction_unit", [0.0, 0.0])
        t_ux, t_uy = tmpl.get("direction_unit", [1.0, 0.0])
        dir_cos = float(ux * t_ux + uy * t_uy)

        tx = stroke_feats.get("trajectory_x") or []
        ty = stroke_feats.get("trajectory_y") or []
        px = tmpl.get("prototype_trajectory_x") or []
        py = tmpl.get("prototype_trajectory_y") or []
        if len(tx) == len(px) and len(tx) > 0:
            mse = sum((tx[k] - px[k]) ** 2 + (ty[k] - py[k]) ** 2 for k in range(len(tx))) / len(tx)
            traj_sim = math.exp(-2.0 * mse)
        else:
            traj_sim = max(0.0, dir_cos)

        vp = stroke_feats.get("velocity_profile") or []
        pvp = tmpl.get("prototype_velocity") or []
        if len(vp) == len(pvp) and len(vp) > 0:
            v_mse = sum((vp[k] - pvp[k]) ** 2 for k in range(len(vp))) / len(vp)
            vel_sim = math.exp(-1.5 * v_mse)
        else:
            vel_sim = 0.8

        dist_ok = float(stroke_feats.get("net_dist", 0.0)) >= float(tmpl.get("min_distance_thr", 0.10))
        spd_ok = float(stroke_feats.get("mean_speed", 0.0)) >= float(tmpl.get("min_speed_thr", 0.25))
        str_ok = float(stroke_feats.get("straightness", 9.9)) <= float(tmpl.get("max_straightness_thr", 1.75))

        overall = max(0.0, min(1.0, 0.45 * max(0.0, dir_cos) + 0.35 * traj_sim + 0.20 * vel_sim))
        matched = bool(dir_cos >= 0.72 and dist_ok and spd_ok and str_ok and overall >= 0.68)
        return {
            "matched": matched,
            "overall_score": round(overall, 4),
            "direction_cosine": round(dir_cos, 4),
            "trajectory_similarity": round(traj_sim, 4),
            "velocity_similarity": round(vel_sim, 4),
            "dist_ok": dist_ok,
            "speed_ok": spd_ok,
            "straight_ok": str_ok,
        }

    def score_window(
        self,
        gesture_key: str,
        samples: Sequence[Any],
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """Score a raw time-series window of `(t, x, y)` or frame dicts against `gesture_key` template."""
        records: List[Dict[str, Any]] = []
        for s in samples:
            if isinstance(s, dict):
                records.append(s)
            elif isinstance(s, (tuple, list)) and len(s) >= 3:
                records.append({"t": float(s[0]), "palm": (float(s[1]), float(s[2]))})
        feats = extract_motion_window_features(records)
        if feats is None:
            return False, 0.0, {"matched": False, "overall_score": 0.0, "reason": "short_window"}
        res = self.score_stroke(gesture_key, feats)
        return bool(res.get("matched", False)), float(res.get("overall_score", 0.0)), res


def train_motion_templates_from_recordings(
    rec_dir: Optional[Path] = None,
    target_keys: Optional[Sequence[str]] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Dedicated Time-Series Motion Learning Pipeline.
    Scans finished recordings for temporal motion repetitions ('do' phase vs 'rest' return phase),
    builds 16-step trajectory & velocity prototypes per motion gesture, evaluates hold-out trajectory
    recognition & return-stroke suppression rates, and saves `models/motion_templates.json`.
    """
    from manux.compat.hand_mouse.gesture_catalog import BY_KEY, is_motion_task
    from manux.compat.hand_mouse.recorder import finished_recordings, read_session

    folder = Path(rec_dir) if rec_dir is not None else (ROOT / "recordings")
    out_file = Path(output_path) if output_path is not None else DEFAULT_MOTION_TEMPLATES_PATH
    model = MotionTrajectoryModel.load(out_file)

    wanted = set(target_keys) if target_keys else {k for k in BY_KEY if is_motion_task(k)}
    wanted = {k for k in wanted if k in BY_KEY and is_motion_task(k)}

    do_strokes_by_key: Dict[str, List[Dict[str, Any]]] = {k: [] for k in wanted}
    rest_strokes_by_key: Dict[str, List[Dict[str, Any]]] = {k: [] for k in wanted}
    postures_by_key: Dict[str, set] = {k: set() for k in wanted}

    complete_paths, _incomplete = finished_recordings(folder)
    for rec_path in complete_paths:
        try:
            meta, frames, _events = read_session(rec_path)
        except Exception:
            continue
        cur_task = ""
        cur_phase = ""
        cur_rep = -1
        cur_cond = str(meta.get("condition_key") or meta.get("posture") or meta.get("pc_alias") or rec_path.stem)
        buf: List[Dict[str, Any]] = []

        def flush_segment(task_key: str, phase_name: str, cond_key: str, frames_buf: List[Dict[str, Any]]) -> None:
            if task_key not in wanted or len(frames_buf) < 4:
                return
            feats = extract_motion_window_features(frames_buf)
            if feats is None:
                return
            feats["condition"] = cond_key
            if phase_name == "do":
                do_strokes_by_key[task_key].append(feats)
                postures_by_key[task_key].add(cond_key)
            elif phase_name == "rest":
                rest_strokes_by_key[task_key].append(feats)

        for row in frames:
            lbl = row.get("label") or {}
            t_key = str(lbl.get("task") or "")
            phase = str(lbl.get("phase") or "")
            rep = int(lbl.get("rep") or 0)
            cond = str(lbl.get("condition_key") or lbl.get("condition") or cur_cond or "default")
            if (t_key, phase, rep) != (cur_task, cur_phase, cur_rep):
                flush_segment(cur_task, cur_phase, cur_cond, buf)
                buf = []
                cur_task, cur_phase, cur_rep, cur_cond = t_key, phase, rep, cond
            lm = row.get("lm") or row.get("landmarks")
            if not lm and isinstance(row.get("hands"), list) and row["hands"]:
                lm = row["hands"][0].get("landmarks")
            if t_key in wanted and phase in ("do", "rest") and lm:
                buf.append({"t": float(row.get("t", 0.0)), "landmarks": lm})
        flush_segment(cur_task, cur_phase, cur_cond, buf)

    trained_summary: Dict[str, Dict[str, Any]] = {}
    for key in wanted:
        strokes = do_strokes_by_key.get(key) or []
        task = BY_KEY.get(key)
        expected_dir = str(getattr(task, "direction", "") or "right")
        dir_vec_map = {
            "left": (-1.0, 0.0),
            "right": (1.0, 0.0),
            "up": (0.0, -1.0),
            "down": (0.0, 1.0),
            "diag_up_left": (-0.7071, -0.7071),
            "diag_up_right": (0.7071, -0.7071),
            "diag_down_left": (-0.7071, 0.7071),
            "diag_down_right": (0.7071, 0.7071),
        }
        exp_ux, exp_uy = dir_vec_map.get(expected_dir, (1.0, 0.0))

        # Filter strokes that align reasonably with the intended motion direction
        valid_strokes = [
            s for s in strokes
            if (s["direction_unit"][0] * exp_ux + s["direction_unit"][1] * exp_uy) >= 0.45
        ]
        if not valid_strokes and strokes:
            valid_strokes = strokes

        if not valid_strokes:
            continue

        n_s = len(valid_strokes)
        mean_ux = sum(s["direction_unit"][0] for s in valid_strokes) / n_s
        mean_uy = sum(s["direction_unit"][1] for s in valid_strokes) / n_s
        unorm = max(1e-6, math.hypot(mean_ux, mean_uy))
        mean_ux, mean_uy = mean_ux / unorm, mean_uy / unorm

        steps = len(valid_strokes[0]["trajectory_x"])
        proto_x = [round(sum(s["trajectory_x"][k] for s in valid_strokes) / n_s, 4) for k in range(steps)]
        proto_y = [round(sum(s["trajectory_y"][k] for s in valid_strokes) / n_s, 4) for k in range(steps)]
        proto_v = [round(sum(s["velocity_profile"][k] for s in valid_strokes) / n_s, 4) for k in range(steps)]

        dists = sorted(s["net_dist"] for s in valid_strokes)
        speeds = sorted(s["mean_speed"] for s in valid_strokes)
        straights = sorted(s["straightness"] for s in valid_strokes)

        min_dist_thr = max(0.06, round(dists[max(0, int(n_s * 0.15))] * 0.82, 4))
        min_speed_thr = max(0.18, round(speeds[max(0, int(n_s * 0.15))] * 0.78, 4))
        max_straight_thr = min(2.0, max(1.35, round(straights[min(n_s - 1, int(n_s * 0.88))] * 1.12, 4)))

        tmpl = {
            "gesture_key": key,
            "name": getattr(task, "name", key),
            "track": "motion",
            "expected_direction": expected_dir,
            "trained_at": time.strftime("%Y-%m-%d %H:%M"),
            "sample_count": len(strokes),
            "positive_reps": len(strokes),
            "valid_stroke_count": n_s,
            "posture_count": len(postures_by_key.get(key) or ()),
            "direction_unit": [round(mean_ux, 4), round(mean_uy, 4)],
            "prototype_trajectory_x": proto_x,
            "prototype_trajectory_y": proto_y,
            "prototype_velocity": proto_v,
            "mean_distance": round(sum(dists) / n_s, 4),
            "min_distance_thr": min_dist_thr,
            "mean_speed": round(sum(speeds) / n_s, 4),
            "min_speed_thr": min_speed_thr,
            "mean_straightness": round(sum(straights) / n_s, 4),
            "max_straightness_thr": max_straight_thr,
            "thumb_tuck_mean": round(sum(s["thumb_tuck_mean"] for s in valid_strokes) / n_s, 4),
            "curl_min_mean": round(sum(s["curl_min"] for s in valid_strokes) / n_s, 4),
        }
        model.templates[key] = tmpl

        # Evaluate recognition rate across recorded 'do' strokes and return-stroke rejection across 'rest' strokes
        hits = sum(1 for s in strokes if model.score_stroke(key, s)["matched"])
        rec_rate = hits / max(1, len(strokes))
        rest_list = rest_strokes_by_key.get(key) or []
        false_returns = sum(1 for r in rest_list if model.score_stroke(key, r)["matched"])
        ret_supp_rate = 1.0 - (false_returns / max(1, len(rest_list))) if rest_list else 1.0

        tmpl["trajectory_recognition_rate"] = round(rec_rate, 4)
        tmpl["return_suppression_rate"] = round(ret_supp_rate, 4)
        trained_summary[key] = tmpl

    if trained_summary:
        model.save(out_file)

    return {
        "ok": len(trained_summary) > 0,
        "trained_count": len(trained_summary),
        "templates": trained_summary,
        "path": str(out_file),
    }

