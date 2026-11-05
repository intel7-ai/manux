"""Pinch judgement, ported from v0.3 `hand-mouse/src/gesture_engine.py` `GestureEngine.process`.

The arithmetic and the order of the checks are copied unchanged (the parity test compares every
field per frame with exact equality). What changed:

- Record & Fling (thumb+index `grab`, off by default in v0.3) is not ported: `record_on` is always
  False here, so thumb+index is the scroll grab (or nothing, with `grab_scroll_enabled` off).
- Gesture -> action names (`gestures.<id>.action`) are gone (D-004). `action` below is the v0.3 action a
  frame *would* have had with the default mapping; the feature turns it into gesture ids. The long press
  on thumb+middle becomes a drag whenever `long_press_drag` is on (v0.3 also required that thumb+middle was
  mapped to `left_click`).
- `enabled` comes from `enable_<v0.3 gesture id>` settings, the show-desktop cooldown from
  `desktop_cooldown`; click cooldowns were never used (finger pinches fire once per pinch).
- Hand loss: `step(t, None)` only records the time; the engine calls `reset()` after
  `app.hand_lost_reset` (v0.3 did that inside `process(None)`).
- Diagnostics v0.3 kept for its failure log (`last_pinky`, `last_desktop`, `last_hold`) are kept.

Landmarks are tuples `(x, y, z)` in image coordinates (contracts section 1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

Pt = Tuple[float, float, float]

WRIST = 0
THUMB_IP = 3
THUMB_TIP = 4
INDEX_DIP = 7
MIDDLE_MCP = 9
FINGER_JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))   # (MCP, TIP) for index, middle, ring, pinky
INDEX_TIP = 8
MIDDLE_TIP = 12
RING_TIP = 16
PINKY_TIP = 20


@dataclass
class PinchFrame:
    """One frame's judgement. Same fields and meaning as v0.3 `GestureResult`."""
    active_gesture: str = "NONE"
    action: str = "none"
    cursor_point: Optional[Tuple[float, float]] = None
    pinch_distance: float = 1.0
    distances: Dict[str, float] = field(default_factory=dict)
    is_moving: bool = False
    pinch_point: Optional[Tuple[float, float]] = None
    dpi_multiplier: float = 1.0
    releasing: bool = False
    hand_shape: str = ""


def _d3(a: Pt, b: Pt) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _d2(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def finger_curl_ratios(pts: Sequence[Pt]) -> Tuple[float, ...]:
    """Per finger (index..pinky): dist(tip, wrist) / dist(MCP, wrist), in 3D (open ~1.7-2.0, curled < ~1.0)."""
    wrist = pts[WRIST]
    ratios = []
    for mcp_i, tip_i in FINGER_JOINTS:
        base = _d3(pts[mcp_i], wrist)
        ratios.append(_d3(pts[tip_i], wrist) / base if base > 1e-6 else 9.9)
    return tuple(ratios)


def palm_anchor_point(pts: Sequence[Pt], ratio: float) -> Tuple[float, float]:
    """Cursor reference point on the wrist (#0) -> middle MCP (#9) line; ratio 0 = wrist, 1 = MCP."""
    ratio = max(0.0, min(1.0, float(ratio)))
    wrist = pts[WRIST]
    palm = pts[MIDDLE_MCP]
    return (wrist[0] + (palm[0] - wrist[0]) * ratio, wrist[1] + (palm[1] - wrist[1]) * ratio)


class PinchCore:
    """State machine of the v0.3 gesture engine (field names kept, so a diff against v0.3 stays readable)."""

    def __init__(self) -> None:
        self._index_pinched = False
        self._middle_hold_pinched = False
        self._last_trigger_times: Dict[str, float] = {}
        self._secondary_since: Dict[str, float] = {}
        self._secondary_off: Dict[str, float] = {}
        self._click_fresh: Dict[str, bool] = {}
        self._release_clear = False
        self._close_since: Optional[float] = None
        self._last_now = 0.0
        self.last_pinky: Optional[Dict[str, Any]] = None
        self._curl_s: Optional[float] = None
        self.last_hold: Optional[Tuple[float, float, float]] = None
        self._long_fired: Dict[str, bool] = {}
        self._index_released_at = -1e9
        self._scroll_released_at = -1e9
        self._drag_since: Optional[float] = None
        self._fist = False
        self._fist_candidate_since: Optional[float] = None
        self._lost_since: Optional[float] = None
        self._index_release_since: Optional[float] = None
        self._index_pinched_since = -1e9
        self._pinch_candidate_since: Optional[float] = None
        self._desktop_since: Optional[float] = None
        self._desktop_anchor: Optional[Tuple[float, float]] = None
        self.last_desktop: Optional[Dict[str, Any]] = None
        self._desktop_armed = True
        self._pinky_fired_at = -1e9
        self._desktop_fired_at = -1e9

    # ------------------------------------------------------------------ public state
    @property
    def pressing(self) -> bool:
        """A thumb pinch is held (thumb+index grab, or a middle / ring / pinky pinch, confirmed or not)."""
        return self._index_pinched or bool(self._secondary_since)

    @property
    def last_release(self) -> float:
        """Last time a grab / scroll ended or a click / window gesture fired (v0.3 `_motion_blocked` input)."""
        return max(self._index_released_at, self._scroll_released_at, self._pinky_fired_at,
                   max(self._last_trigger_times.values(), default=-1e9))

    @property
    def pinched_fingers(self) -> set:
        held = {f for f in ("middle", "ring", "pinky") if f in self._secondary_since}
        if self._index_pinched:
            held.add("index")
        return held

    def reset(self) -> None:
        """v0.3 `GestureEngine.reset`: drops every press / hold. The release history (timestamps, the
        per-finger "opened since the last click" flags) is kept on purpose, as in v0.3: a hand that comes back
        still pinched must not click again, and the lockouts after a grab still apply."""
        self._index_pinched = False
        self._middle_hold_pinched = False
        self._secondary_since.clear()
        self._secondary_off.clear()
        self._long_fired.clear()
        self._drag_since = None
        self._fist = False
        self._fist_candidate_since = None
        self._index_release_since = None
        self._pinch_candidate_since = None
        self._desktop_since = None
        self._desktop_armed = True
        self._curl_s = None

    # ------------------------------------------------------------------ helpers
    def _can_trigger(self, gesture_id: str, now: float, finger: Optional[str], cooldown: float = 0.35) -> bool:
        """Finger pinches fire ONCE per pinch (open and pinch again to repeat); others use their cooldown."""
        if finger is not None:
            if not self._click_fresh.get(finger, True):
                return False
            self._click_fresh[finger] = False
            self._last_trigger_times[gesture_id] = now
            return True
        last_time = self._last_trigger_times.get(gesture_id, -1e9)
        if (now - last_time) >= cooldown:
            self._last_trigger_times[gesture_id] = now
            return True
        return False

    @staticmethod
    def _fist_mult(c: Mapping[str, Any]) -> float:
        return max(0.1, float(c["fist_dpi_multiplier"]))

    def shape_dpi(self, curl: float, c: Mapping[str, Any]) -> float:
        """Cursor speed multiplier for a mean finger curl: shape_dpi_min (open) .. fist_dpi_multiplier (fist),
        log-linear in between."""
        if not c["shape_dpi_enabled"]:
            return 1.0
        lo, hi = max(0.1, float(c["shape_dpi_min"])), self._fist_mult(c)
        c_open, c_fist = float(c["shape_curl_open"]), float(c["shape_curl_fist"])
        if c_open <= c_fist:
            return lo
        t = max(0.0, min(1.0, (c_open - curl) / (c_open - c_fist)))
        t = t ** max(0.1, float(c["shape_dpi_curve"]))
        return lo * (hi / lo) ** t

    # ------------------------------------------------------------------ per frame
    def step(self, now: float, pts: Optional[Sequence[Pt]], c: Mapping[str, Any]) -> PinchFrame:
        """One frame. `pts`: 21 (x, y, z) or None (no hand). `c`: resolved settings (settings.resolve)."""
        if pts is None or len(pts) < 21:
            if self._lost_since is None:
                self._lost_since = now
            return PinchFrame()

        def on(gid: str) -> bool:
            return bool(c["enable_" + gid])

        self._last_now = now
        self._lost_since = None

        thumb = pts[THUMB_TIP]
        index = pts[INDEX_TIP]
        middle = pts[MIDDLE_TIP]
        ring = pts[RING_TIP]
        pinky = pts[PINKY_TIP]

        d_index = _d2(thumb, index)
        d_middle = _d2(thumb, middle)
        d_ring = _d2(thumb, ring)
        d_pinky = _d2(thumb, pinky)
        distances = {"index": d_index, "middle": d_middle, "ring": d_ring, "pinky": d_pinky}

        cursor_point_early = palm_anchor_point(pts, c["cursor_anchor_ratio"])

        # 0. Fist (fast move); the thumbs-up (motion mode entry pose) freezes the cursor instead
        ratios = finger_curl_ratios(pts)
        curl = max(ratios)
        if c["motion_pose_freeze"] and curl <= float(c["motion_fold_ratio"]):
            _palm = max(1e-6, _d2(pts[WRIST], pts[MIDDLE_MCP]))
            _tuck = min(_d2(thumb, pts[13]), _d2(thumb, pts[17])) / _palm
            _up = (pts[5][1] - thumb[1]) / _palm
            if _tuck >= float(c["motion_thumb_out"]) and _up >= float(c["motion_thumb_up"]):
                self._fist = False
                self._fist_candidate_since = None
                return PinchFrame(active_gesture="motion_pose", pinch_distance=d_index, distances=distances)
        fist_enter = float(c["fist_curl_enter"])
        scroll_on = bool(c["grab_scroll_enabled"])
        index_active = scroll_on
        index_tucked = ratios[0] <= float(c["index_tuck_ratio"])
        others_curled = max(ratios[1:]) <= 1.35
        if not self._fist:
            if (not self._index_pinched or not index_active) and index_tucked and curl <= fist_enter:
                if self._fist_candidate_since is None:
                    self._fist_candidate_since = now
                if now - self._fist_candidate_since >= float(c["fist_confirm_time"]):
                    self._fist = True
                    self._fist_candidate_since = None
                    self._pinch_candidate_since = None
            else:
                self._fist_candidate_since = None
        elif curl > float(c["fist_curl_exit"]):
            self._fist = False
            self._index_released_at = now
        if self._fist and on("fist"):
            if self._index_pinched:
                self._index_released_at = now
            self._index_pinched = False
            self._middle_hold_pinched = False
            self._drag_since = None
            self._secondary_since.clear()
            return PinchFrame(active_gesture="fist", action="move_cursor", cursor_point=cursor_point_early,
                              pinch_distance=d_index, distances=distances, is_moving=True,
                              dpi_multiplier=max(0.1, float(c["fist_dpi_multiplier"])), hand_shape="fist")

        # 0.5 Show desktop: thumb tucked across the palm, four fingers extended, held still
        palm_len = max(1e-6, _d2(pts[WRIST], pts[MIDDLE_MCP]))
        thumb_tuck = min(_d2(thumb, pts[13]), _d2(thumb, pts[17])) / palm_len
        distances["thumb_tuck"] = thumb_tuck
        palm_width = _d2(pts[5], pts[17]) / palm_len
        exclusive = float(c["gesture_exclusive_time"])
        desktop_posture = (
            not self._index_pinched
            and on("thumb_tucked_open")
            and min(ratios) > float(c["desktop_extended_ratio"])
            and thumb_tuck <= float(c["desktop_thumb_tuck"])
            and palm_width >= float(c["desktop_min_palm_width"])
            and "pinky" not in self._secondary_since
            and not self._secondary_since
            and min(d_index, d_middle, d_ring, d_pinky) > float(c["desktop_min_tip_gap"])
            and (now - self._pinky_fired_at) >= exclusive
        )
        palm_now = cursor_point_early
        if desktop_posture and self._desktop_anchor is not None and palm_now is not None:
            moved = math.hypot(palm_now[0] - self._desktop_anchor[0], palm_now[1] - self._desktop_anchor[1])
            if moved > float(c["desktop_still_move"]):
                self._desktop_since = now
                self._desktop_anchor = palm_now
        last_activity = self.last_release
        after_activity = (now - last_activity) >= float(c["desktop_after_activity"])
        self.last_desktop = None
        if desktop_posture:
            if self._desktop_since is None:
                self._desktop_since = now
                self._desktop_anchor = palm_now
            self.last_desktop = {"tuck": round(thumb_tuck, 2), "ext_min": round(min(ratios), 2),
                                 "hold": round(now - self._desktop_since, 2), "armed": self._desktop_armed,
                                 "since_activity": round(now - last_activity, 2) if last_activity > -1e8 else None}
            if (
                self._desktop_armed
                and after_activity
                and now - self._desktop_since >= float(c["desktop_hold"])
                and self._can_trigger("thumb_tucked_open", now, None, float(c["desktop_cooldown"]))
            ):
                self._desktop_armed = False
                self._desktop_fired_at = now
                return PinchFrame(active_gesture="thumb_tucked_open", action="show_desktop",
                                  cursor_point=cursor_point_early, pinch_distance=d_index, distances=distances)
        else:
            self._desktop_since = None
            self._desktop_anchor = None
            self._desktop_armed = True

        pinch_threshold = float(c["pinch_threshold"])
        release_threshold = float(c["release_threshold"])
        click_threshold = float(c["click_threshold"])

        mean_curl = sum(ratios) / 4.0
        k = max(0.05, min(1.0, float(c["shape_curl_smooth"])))
        self._curl_s = mean_curl if self._curl_s is None else self._curl_s + k * (mean_curl - self._curl_s)
        shape_mult = self.shape_dpi(self._curl_s, c)
        curled = shape_mult > 1.15

        # Thumb + index hysteresis
        if not self._index_pinched:
            self.last_hold = None
            middle_clearly_closer = d_middle < d_index * float(c["click_neighbor_ratio"])
            can_pinch = d_index <= pinch_threshold and not index_tucked and not middle_clearly_closer
            if can_pinch and others_curled:
                if self._pinch_candidate_since is None:
                    self._pinch_candidate_since = now
                can_pinch = (now - self._pinch_candidate_since) >= float(c["pinch_fistlike_confirm"])
            elif not can_pinch:
                self._pinch_candidate_since = None
            if can_pinch:
                self._index_pinched = True
                self._index_pinched_since = now
                self._pinch_candidate_since = None
                self._index_release_since = None
        else:
            scrolling = scroll_on
            rel_thr = release_threshold + (float(c["scroll_release_margin"]) if scrolling else 0.0)
            joint_limit = rel_thr * float(c["pinch_hold_joint_ratio"])
            joints = [d for d in (_d2(thumb, pts[INDEX_DIP]), _d2(pts[THUMB_IP], index)) if d <= joint_limit]
            d_hold = min([d_index] + joints)
            self.last_hold = (round(d_hold, 4), round(rel_thr, 4), round(d_index, 4))
            if d_hold > rel_thr:
                self._close_since = None
                if self._index_release_since is None:
                    self._index_release_since = now
                    self._release_clear = False
                if d_hold > rel_thr * float(c["scroll_freeze_ratio"]):
                    self._release_clear = True
                grace = max(0.0, float(c["pinch_release_grace"]))
                if scrolling:
                    grace = max(grace, float(c["scroll_release_grace"]))
                if (now - self._index_release_since) >= grace:
                    self._index_pinched = False
                    self._index_release_since = None
                    if index_active or self._middle_hold_pinched:
                        self._index_released_at = now
                    if scroll_on:
                        self._scroll_released_at = now
            else:
                if self._index_release_since is not None:
                    if self._close_since is None:
                        self._close_since = now
                    if (now - self._close_since) >= float(c["pinch_rearm_time"]):
                        self._index_release_since = None
                        self._close_since = None

        # Thumb + index + middle (drag hold)
        drag_thr = float(c["drag_hold_threshold"])
        drag_confirm = float(c["click_confirm_time"])
        drag_window = float(c["drag_hold_entry_window"])
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

        cursor_point = palm_anchor_point(pts, c["cursor_anchor_ratio"])
        pinch_point = ((thumb[0] + index[0]) * 0.5, (thumb[1] + index[1]) * 0.5)

        if self._index_pinched and index_active:
            self._secondary_since.clear()
        for finger in [f for f in self._long_fired if f not in self._secondary_since]:
            self._long_fired.pop(finger, None)

        # 1. Thumb + index + middle -> drag
        if self._index_pinched and self._middle_hold_pinched and on("thumb_index_middle_pinch"):
            return PinchFrame(active_gesture="thumb_index_middle_pinch", action="drag_hold",
                              cursor_point=cursor_point, pinch_distance=d_index, distances=distances, is_moving=True)

        # 2. Thumb + index -> scroll grab (page grabbed, the cursor stays put)
        if self._index_pinched and scroll_on:
            return PinchFrame(
                active_gesture="thumb_index_pinch", action="scroll_grab", cursor_point=cursor_point,
                pinch_distance=d_index, distances=distances, is_moving=False, pinch_point=pinch_point,
                releasing=self._index_release_since is not None and (
                    self._release_clear
                    or (self._last_now - self._index_release_since) >= float(c["scroll_freeze_delay"])
                ),
            )

        # 3. Middle / ring / pinky pinches (index not pinched)
        confirm = float(c["click_confirm_time"])
        lockout = float(c["click_lockout_after_grab"])
        locked = (now - self._index_released_at) < lockout
        pinky_thr = float(c["pinky_click_threshold"])
        ring_tuck = float(c["ring_tuck_ratio"])
        margin = float(c["click_release_margin"])
        click_grace = float(c["click_release_grace"])

        def confirmed(finger: str, dist: float, dip: float, thr: float,
                      entry_ok: bool = True, keep_ok: bool = True) -> bool:
            if not on(f"thumb_{finger}_pinch"):
                self._secondary_since.pop(finger, None)
                self._secondary_off.pop(finger, None)
                return False
            if finger not in self._secondary_since:
                if c["side_pinch_exclusive"] and any(
                        other != finger and other not in self._secondary_off for other in self._secondary_since):
                    return False
                if index_active:
                    clear_of_index = not (d_index < dist * float(c["click_neighbor_ratio"]))
                else:
                    clear_of_index = True
                if dist <= thr and clear_of_index and entry_ok and not locked:
                    self._secondary_since[finger] = now
                    self._secondary_off.pop(finger, None)
                else:
                    if dist > thr + margin:
                        self._click_fresh[finger] = True
                    return False
            else:
                limit = thr + margin
                joint_ok = dip <= limit * float(c["click_hold_joint_ratio"])
                held_dist = min(dist, dip) if joint_ok else dist
                if held_dist > limit or not keep_ok:
                    off = self._secondary_off.setdefault(finger, now)
                    grace = click_grace
                    if self._long_fired.get(finger, False):
                        grace = max(click_grace, float(c["pinch_release_grace"]))
                        if held_dist > limit * float(c["drag_clear_ratio"]):
                            grace = min(grace, float(c["drag_clear_grace"]))
                    if now - off >= grace:
                        self._secondary_since.pop(finger, None)
                        self._secondary_off.pop(finger, None)
                        self._click_fresh[finger] = True
                        self._long_fired.pop(finger, None)
                        return False
                else:
                    self._secondary_off.pop(finger, None)
            return (now - self._secondary_since[finger]) >= confirm

        def dip(tip_i: int) -> float:
            return _d2(thumb, pts[tip_i - 1])

        click_tuck = float(c["click_tuck_ratio"])
        nearest = min((("middle", d_middle), ("ring", d_ring), ("pinky", d_pinky)), key=lambda kv: kv[1])[0]
        middle_ok = confirmed("middle", d_middle, dip(MIDDLE_TIP), click_threshold,
                              entry_ok=ratios[1] > click_tuck and nearest == "middle",
                              keep_ok=ratios[1] > click_tuck * 0.9)
        ring_thr = float(c["ring_click_threshold"])
        ring_ok = confirmed("ring", d_ring, dip(RING_TIP), ring_thr,
                            entry_ok=ratios[2] > ring_tuck and nearest == "ring",
                            keep_ok=ratios[2] > ring_tuck * 0.9)
        self.last_pinky = None
        if d_pinky <= pinky_thr * 1.6:
            self.last_pinky = {
                "d": round(d_pinky, 4), "thr": round(pinky_thr, 4), "nearest": nearest,
                "tuck": round(ratios[3], 2), "tuck_need": float(c["pinky_tuck_ratio"]),
                "dom": round(d_pinky / max(d_ring, 1e-6), 2),
                "gap": round(_d2(pts[RING_TIP], pts[PINKY_TIP]), 3),
                "after_desktop": round(now - self._desktop_fired_at, 2) if self._desktop_fired_at > -1e8 else None,
                "locked": bool(locked), "held": "pinky" in self._secondary_since,
            }
        pinky_ok = confirmed(
            "pinky", d_pinky, dip(PINKY_TIP), pinky_thr,
            keep_ok=ratios[3] > float(c["pinky_tuck_ratio"]) * 0.9,
            entry_ok=(
                nearest == "pinky"
                and ratios[3] > float(c["pinky_tuck_ratio"])
                and d_pinky < d_ring * float(c["pinky_ring_dominance"])
                and _d2(pts[RING_TIP], pts[PINKY_TIP]) > float(c["pinky_ring_min_gap"])
                and (now - self._desktop_fired_at) >= exclusive
            ),
        )

        if not self._index_pinched or not index_active:
            if middle_ok and on("thumb_middle_pinch"):
                if self._can_trigger("thumb_middle_pinch", now, "middle"):
                    return PinchFrame(active_gesture="thumb_middle_pinch", action="left_click",
                                      cursor_point=cursor_point, pinch_distance=d_index, distances=distances)
            if ring_ok and on("thumb_ring_pinch"):
                if self._can_trigger("thumb_ring_pinch", now, "ring"):
                    return PinchFrame(active_gesture="thumb_ring_pinch", action="right_click",
                                      cursor_point=cursor_point, pinch_distance=d_index, distances=distances)
            if pinky_ok and on("thumb_pinky_pinch"):
                if self._can_trigger("thumb_pinky_pinch", now, "pinky"):
                    self._pinky_fired_at = now
                    self._desktop_since = None
                    return PinchFrame(active_gesture="thumb_pinky_pinch", action="task_switcher",
                                      cursor_point=cursor_point, pinch_distance=d_index, distances=distances)

        # A middle / ring / pinky pinch is held: cursor frozen; long press on thumb+middle -> drag
        if self._secondary_since and (not self._index_pinched or not index_active):
            held = next(f for f in ("middle", "ring", "pinky") if f in self._secondary_since)
            if (
                held == "middle"
                and c["long_press_drag"]
                and (
                    self._long_fired.get("middle", False)
                    or now - self._secondary_since["middle"] >= float(c["long_press_time"])
                )
            ):
                self._long_fired["middle"] = True
                return PinchFrame(active_gesture="thumb_middle_pinch", action="drag_hold",
                                  cursor_point=cursor_point, pinch_distance=d_index, distances=distances,
                                  is_moving=True,
                                  dpi_multiplier=max(0.1, float(c["long_press_drag_multiplier"])))
            return PinchFrame(active_gesture=f"thumb_{held}_pinch", pinch_distance=d_index, distances=distances)

        # Grab scroll off: a thumb+index touch is ignored, the cursor keeps moving
        # (v0.3 does not check `enable_no_pinch` here)
        if self._index_pinched and not index_active:
            return PinchFrame(active_gesture="no_pinch", action="move_cursor", cursor_point=cursor_point,
                              pinch_distance=d_index, distances=distances, is_moving=True)

        # 4. No pinch -> move the cursor; a finger approaching the thumb freezes it
        approach = max(release_threshold, float(c["approach_freeze_distance"]))
        if curled and not self._index_pinched and not self._secondary_since:
            approach = max(release_threshold, float(c["shape_freeze_distance"]))
        near_pinch = min(d_index, d_middle, d_ring, d_pinky) <= approach
        holding = (now - self._scroll_released_at) < float(c["scroll_cursor_hold"])
        if not near_pinch and not holding and on("no_pinch"):
            return PinchFrame(
                active_gesture="no_pinch", action="move_cursor", cursor_point=cursor_point,
                pinch_distance=d_index, distances=distances, is_moving=True,
                dpi_multiplier=shape_mult if (curled and not self._index_pinched and not self._secondary_since) else 1.0,
                hand_shape=("fist" if shape_mult >= 0.9 * self._fist_mult(c) else "half" if curled else "open"),
            )

        return PinchFrame(pinch_distance=d_index, distances=distances)
