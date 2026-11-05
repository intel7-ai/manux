"""The pointer's own thumb + middle / ring pinch detector (left / right click in "pinch" style).

v0.3 `PointerEngine` used the whole `gesture_engine.GestureEngine` for this, always with the same
override: only `thumb_middle_pinch` (left click) and `thumb_ring_pinch` (right click) enabled,
Record & Fling, grab-scroll and long-press drag off. `GestureEngine` (1,164 lines) moves to the
`pinch` feature in S4, and features may not import each other (D-003), so this file keeps only the
part of `GestureEngine.process` that can change what the pointer reads under that override:

- whether a left / right click fired this frame (`click`)
- whether a middle / ring pinch is being held (`pinching`)

State paths kept because they change those two answers:
- hand loss: state forgotten after `hand_lost_reset` s without a hand
- thumbs-up (motion entry pose) returns early and leaves every other state untouched
- fist detection: its end starts the click lockout (`_index_released_at`)
- thumb + index pinch hysteresis and the thumb + index + middle hold: a released three-finger hold
  also starts the click lockout
- middle / ring pinch: entry, hysteresis, release grace, one click per pinch, one side pinch at a time

Dropped because they cannot reach the pointer under that override: Record & Fling, grab-scroll,
show desktop, thumb + pinky, long-press drag, cursor speed by hand shape, the diagnostics
(`last_hold`, `last_pinky`, `last_desktop`). The expressions are copied unchanged (same float
operations) so the parity test can require exact equality with v0.3.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

WRIST = 0
THUMB_IP = 3
THUMB_TIP = 4
INDEX_DIP = 7
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_TIP = 20
# (MCP, TIP) for index, middle, ring, pinky
FINGER_JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))

LEFT, RIGHT = "left_click", "right_click"
_CLICK_OF = {"middle": LEFT, "ring": RIGHT}


def _dist_3d(a, b) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _dist_2d(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def finger_curl_ratios(pts: Sequence[Any]) -> Tuple[float, ...]:
    """Per finger (index..pinky): dist(tip, wrist) / dist(MCP, wrist), in 3D (v0.3 GestureEngine)."""
    wrist = pts[WRIST]
    ratios = []
    for mcp_i, tip_i in FINGER_JOINTS:
        base = _dist_3d(pts[mcp_i], wrist)
        ratios.append(_dist_3d(pts[tip_i], wrist) / base if base > 1e-6 else 9.9)
    return tuple(ratios)


def palm_anchor_point(pts: Sequence[Any], config: Mapping[str, Any]) -> Tuple[float, float]:
    """Point on the wrist (#0) -> middle MCP (#9) line; `cursor_anchor_ratio` 0.5 = palm centre."""
    ratio = max(0.0, min(1.0, float(config.get("cursor_anchor_ratio", 0.5))))
    wrist = pts[WRIST]
    palm = pts[MIDDLE_MCP]
    return (
        wrist.x + (palm.x - wrist.x) * ratio,
        wrist.y + (palm.y - wrist.y) * ratio,
    )


class PinchResult:
    __slots__ = ("click", "pinching")

    def __init__(self, click: Optional[str] = None, pinching: bool = False) -> None:
        self.click = click          # "left_click" / "right_click" on the frame the click fires
        self.pinching = pinching    # a middle / ring pinch is held (or fired this frame)


_NONE = PinchResult()


class PointerPinch:
    def __init__(self) -> None:
        self.config: Dict[str, Any] = {}
        self._index_pinched = False
        self._middle_hold_pinched = False
        self._secondary_since: Dict[str, float] = {}
        self._secondary_off: Dict[str, float] = {}
        # Not cleared by reset() (as in v0.3): one click per pinch, the lockout clock, hand loss clock
        self._click_fresh: Dict[str, bool] = {}
        self._index_released_at = -1e9
        self._index_pinched_since = -1e9
        self._close_since: Optional[float] = None
        self._lost_since: Optional[float] = None
        self._drag_since: Optional[float] = None
        self._fist = False
        self._fist_candidate_since: Optional[float] = None
        self._index_release_since: Optional[float] = None
        self._pinch_candidate_since: Optional[float] = None

    @property
    def is_pinched(self) -> bool:
        return self._index_pinched

    @property
    def pinched_fingers(self) -> set:
        held = {f for f in ("middle", "ring") if f in self._secondary_since}
        if self._index_pinched:
            held.add("index")
        return held

    def reset(self) -> None:
        self._index_pinched = False
        self._middle_hold_pinched = False
        self._secondary_since.clear()
        self._secondary_off.clear()
        self._drag_since = None
        self._fist = False
        self._fist_candidate_since = None
        self._index_release_since = None
        self._pinch_candidate_since = None

    def _f(self, key: str, default: float) -> float:
        return float(self.config.get(key, default))

    def process(self, pts: Optional[Sequence[Any]], now: float) -> PinchResult:
        cfg = self.config
        if pts is None:
            if self._lost_since is None:
                self._lost_since = now
            if now - self._lost_since >= self._f("hand_lost_reset", 0.3):
                self.reset()
            return _NONE
        self._lost_since = None

        thumb = pts[THUMB_TIP]
        index = pts[INDEX_TIP]
        d_index = _dist_2d(thumb, index)
        d_middle = _dist_2d(thumb, pts[MIDDLE_TIP])
        d_ring = _dist_2d(thumb, pts[RING_TIP])
        d_pinky = _dist_2d(thumb, pts[PINKY_TIP])

        ratios = finger_curl_ratios(pts)
        curl = max(ratios)
        # Thumbs-up (motion mode entry pose): v0.3 returns before touching any other state
        if cfg.get("motion_pose_freeze", True) and curl <= self._f("motion_fold_ratio", 1.2):
            _palm = max(1e-6, _dist_2d(pts[WRIST], pts[MIDDLE_MCP]))
            _tuck = min(_dist_2d(thumb, pts[13]), _dist_2d(thumb, pts[17])) / _palm
            _up = (pts[5].y - thumb.y) / _palm
            if _tuck >= self._f("motion_thumb_out", 0.85) and _up >= self._f("motion_thumb_up", 0.15):
                self._fist = False
                self._fist_candidate_since = None
                return _NONE

        # Fist: only its end matters here (no click right after the fingers open)
        fist_enter = self._f("fist_curl_enter", 0.95)
        index_tucked = ratios[0] <= self._f("index_tuck_ratio", 1.15)
        others_curled = max(ratios[1:]) <= 1.35
        if not self._fist:
            if index_tucked and curl <= fist_enter:
                if self._fist_candidate_since is None:
                    self._fist_candidate_since = now
                if now - self._fist_candidate_since >= self._f("fist_confirm_time", 0.12):
                    self._fist = True
                    self._fist_candidate_since = None
                    self._pinch_candidate_since = None
            else:
                self._fist_candidate_since = None
        elif curl > self._f("fist_curl_exit", 1.2):
            self._fist = False
            self._index_released_at = now

        pinch_threshold = self._f("pinch_threshold", 0.055)
        release_threshold = self._f("release_threshold", 0.075)
        click_threshold = self._f("click_threshold", 0.052)

        # Thumb + index pinch hysteresis (no job in the pointer, but see the three-finger hold)
        if not self._index_pinched:
            middle_clearly_closer = d_middle < d_index * self._f("click_neighbor_ratio", 0.85)
            can_pinch = d_index <= pinch_threshold and not index_tucked and not middle_clearly_closer
            if can_pinch and others_curled:
                if self._pinch_candidate_since is None:
                    self._pinch_candidate_since = now
                can_pinch = (now - self._pinch_candidate_since) >= self._f("pinch_fistlike_confirm", 0.12)
            elif not can_pinch:
                self._pinch_candidate_since = None
            if can_pinch:
                self._index_pinched = True
                self._index_pinched_since = now
                self._pinch_candidate_since = None
                self._index_release_since = None
        else:
            rel_thr = release_threshold
            joint_limit = rel_thr * self._f("pinch_hold_joint_ratio", 0.6)
            joints = [d for d in (_dist_2d(thumb, pts[INDEX_DIP]),
                                  _dist_2d(pts[THUMB_IP], index)) if d <= joint_limit]
            d_hold = min([d_index] + joints)
            if d_hold > rel_thr:
                self._close_since = None
                if self._index_release_since is None:
                    self._index_release_since = now
                grace = max(0.0, self._f("pinch_release_grace", 0.15))
                if (now - self._index_release_since) >= grace:
                    self._index_pinched = False
                    self._index_release_since = None
                    if self._middle_hold_pinched:
                        self._index_released_at = now
            else:
                if self._index_release_since is not None:
                    if self._close_since is None:
                        self._close_since = now
                    if (now - self._close_since) >= self._f("pinch_rearm_time", 0.03):
                        self._index_release_since = None
                        self._close_since = None

        # Thumb + index + middle hold (v0.3 drag_hold; off in the pointer, but its release locks clicks)
        drag_thr = self._f("drag_hold_threshold", 0.035)
        drag_confirm = self._f("click_confirm_time", 0.06)
        drag_window = self._f("drag_hold_entry_window", 0.25)
        in_entry_window = (now - self._index_pinched_since) <= drag_window
        if not self._middle_hold_pinched:
            if self._index_pinched and in_entry_window and d_middle <= drag_thr and d_index <= drag_thr:
                if self._drag_since is None:
                    self._drag_since = now
                if (now - self._drag_since) >= drag_confirm:
                    self._middle_hold_pinched = True
            else:
                self._drag_since = None
        else:
            if not self._index_pinched or d_middle > release_threshold:
                self._middle_hold_pinched = False

        # Middle / ring pinches
        confirm = self._f("click_confirm_time", 0.06)
        lockout = self._f("click_lockout_after_grab", 0.35)
        locked = (now - self._index_released_at) < lockout
        margin = self._f("click_release_margin", 0.02)
        click_grace = self._f("click_release_grace", 0.03)
        exclusive = cfg.get("side_pinch_exclusive", True)
        hold_joint = self._f("click_hold_joint_ratio", 0.75)

        def confirmed(finger: str, dist: float, dip: float, thr: float, entry_ok: bool, keep_ok: bool) -> bool:
            if finger not in self._secondary_since:
                if exclusive and any(
                        other != finger and other not in self._secondary_off for other in self._secondary_since):
                    return False
                if dist <= thr and entry_ok and not locked:
                    self._secondary_since[finger] = now
                    self._secondary_off.pop(finger, None)
                else:
                    if dist > thr + margin:
                        self._click_fresh[finger] = True
                    return False
            else:
                limit = thr + margin
                joint_ok = dip <= limit * hold_joint
                held_dist = min(dist, dip) if joint_ok else dist
                if held_dist > limit or not keep_ok:
                    off = self._secondary_off.setdefault(finger, now)
                    if now - off >= click_grace:
                        self._secondary_since.pop(finger, None)
                        self._secondary_off.pop(finger, None)
                        self._click_fresh[finger] = True
                        return False
                else:
                    self._secondary_off.pop(finger, None)
            return (now - self._secondary_since[finger]) >= confirm

        click_tuck = self._f("click_tuck_ratio", 0.8)
        ring_tuck = self._f("ring_tuck_ratio", 0.8)
        nearest = min((("middle", d_middle), ("ring", d_ring), ("pinky", d_pinky)), key=lambda kv: kv[1])[0]
        middle_ok = confirmed("middle", d_middle, _dist_2d(thumb, pts[MIDDLE_TIP - 1]), click_threshold,
                              entry_ok=ratios[1] > click_tuck and nearest == "middle",
                              keep_ok=ratios[1] > click_tuck * 0.9)
        ring_ok = confirmed("ring", d_ring, _dist_2d(thumb, pts[RING_TIP - 1]),
                            self._f("ring_click_threshold", 0.044),
                            entry_ok=ratios[2] > ring_tuck and nearest == "ring",
                            keep_ok=ratios[2] > ring_tuck * 0.9)

        for finger, ok in (("middle", middle_ok), ("ring", ring_ok)):
            # one click per pinch, like a mouse button (v0.3 GestureEngine._can_trigger)
            if ok and self._click_fresh.get(finger, True):
                self._click_fresh[finger] = False
                return PinchResult(_CLICK_OF[finger], True)
        if self._secondary_since:
            return PinchResult(None, True)
        return _NONE
