"""Pinch judgement, ported from v0.3 `hand-mouse/src/gesture_engine.py` `GestureEngine.process`.

The port retains the v0.3 gesture geometry and action order, with these changes:

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
- Click candidates need continuous, unambiguous evidence. Confirmed pinches retain their finger
  until release so neighboring fingertip jitter cannot switch a click or a drag.

Landmarks are tuples `(x, y, z)` in image coordinates (contracts section 1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from manux.perception.pinch_contact import clear_pinky_contact, world_tip_distances

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
CLICK_SEPARATION_PALM = .02  # A tiny projected gap between overlapping tips is not a reliable winner.
CLICK_WORLD_ENTER = .45     # Thumb-tip gap, normalized by wrist -> middle MCP length.
CLICK_WORLD_FOLDED = .30    # A clearly touching fingertip may be folded toward the camera.
CLICK_WORLD_RELEASE = .65
SMALL_PALM_IMAGE_SPAN = .12  # Below this span, projected tip overlap can latch a false scroll.
CONTACT_RELEASE_GRACE = .12  # Confirmed 3D contact tolerates a brief estimate dropout.
DRAG_DISAGREEMENT_GRACE = .22  # Allow a brief depth/image disagreement while dragging.
PINKY_REARM_TIME = .15       # Window switching needs a fresh, fully released contact episode.


def _middle_distal_distance(world: Sequence[Pt]) -> float:
    """Normalized thumb distance to the last segment of the middle finger."""
    thumb, joint, tip = world[THUMB_TIP], world[MIDDLE_TIP - 1], world[MIDDLE_TIP]
    segment = tuple(end - start for start, end in zip(joint, tip))
    length_squared = sum(component * component for component in segment)
    if length_squared <= 1e-12:
        return math.inf
    projection = sum((point - start) * axis for point, start, axis in zip(thumb, joint, segment))
    fraction = min(1.0, max(0.0, projection / length_squared))
    nearest = tuple(start + fraction * axis for start, axis in zip(joint, segment))
    return math.dist(thumb, nearest) / math.dist(world[WRIST], world[MIDDLE_MCP])


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
        self._secondary_confirmed: set[str] = set()
        self._corroborated_last: set[str] = set()
        self._repeat_ready: set[str] = set()
        self._side_open_last = False
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
        self._pinky_armed = True
        self._pinky_clear_since: Optional[float] = None
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
        self._secondary_confirmed.clear()
        self._corroborated_last.clear()
        self._repeat_ready.clear()
        self._side_open_last = False
        self._long_fired.clear()
        self._drag_since = None
        self._fist = False
        self._fist_candidate_since = None
        self._index_release_since = None
        self._pinch_candidate_since = None
        self._desktop_since = None
        self._desktop_armed = True
        self._curl_s = None
        self._pinky_clear_since = None  # Keep the re-arm latch across hand loss and pauses.

    # ------------------------------------------------------------------ helpers
    def _drop_secondary(self, finger: str) -> None:
        self._secondary_since.pop(finger, None)
        self._secondary_off.pop(finger, None)
        self._secondary_confirmed.discard(finger)
        self._repeat_ready.discard(finger)
        self._long_fired.pop(finger, None)

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
    def step(self, now: float, pts: Optional[Sequence[Pt]], c: Mapping[str, Any],
             *, world: Optional[Sequence[Pt]] = None) -> PinchFrame:
        """One frame. `pts`: 21 (x, y, z) or None (no hand). `c`: resolved settings (settings.resolve)."""
        corroborated_last = self._corroborated_last
        self._corroborated_last = set()
        side_open_last = self._side_open_last
        self._side_open_last = False
        if pts is None or len(pts) < 21:
            self._repeat_ready.clear()
            self._pinky_clear_since = None
            # A gap cannot count toward confirming a new click; an established hold keeps its grace.
            for finger in tuple(self._secondary_since):
                if finger not in self._secondary_confirmed:
                    self._drop_secondary(finger)
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
        world_distances = world_tip_distances(world)
        if world_distances is not None:
            secondary_open = all(world_distances[f] > CLICK_WORLD_RELEASE for f in ("middle", "ring", "pinky"))
        else:
            secondary_open = all(distances[f] > float(c[key]) + float(c["click_release_margin"])
                                 for f, key in (("middle", "click_threshold"), ("ring", "ring_click_threshold"),
                                                ("pinky", "pinky_click_threshold")))
        if secondary_open:
            if self._pinky_clear_since is None:
                self._pinky_clear_since = now
            if now - self._pinky_clear_since >= PINKY_REARM_TIME:
                self._pinky_armed = True
                self._click_fresh["pinky"] = True
        else:
            self._pinky_clear_since = None
        neighbor_ratio = max(0.0, min(1.0, float(c["click_neighbor_ratio"])))
        palm_len = max(1e-6, _d2(pts[WRIST], pts[MIDDLE_MCP]))
        ratios = finger_curl_ratios(pts)
        world_contacts = set()
        if world_distances is not None:
            for finger in ("middle", "ring"):
                if not on(f"thumb_{finger}_pinch"):
                    continue
                distance = world_distances[finger]
                other = min(value for name, value in world_distances.items() if name != finger)
                # A clear 3D contact can start without an image-distance match. Once started,
                # use the existing wider contact range, but still require the same unique finger.
                limit = CLICK_WORLD_ENTER if finger in self._secondary_since else CLICK_WORLD_FOLDED
                if (distance <= limit and distance < other * neighbor_ratio
                        and other - distance >= CLICK_SEPARATION_PALM):
                    world_contacts.add(finger)

        if world_distances is not None and not world_contacts:
            # World landmarks can put the index closest even when the image clearly shows
            # a different fingertip touching. Require a unique image contact AND a world
            # gap inside the existing contact envelope; never override a clear world winner.
            # This corroboration is for left/right only, not an easier window-switch trigger.
            for finger, ratio, threshold_key, tuck_key in (
                    ("middle", ratios[1], "click_threshold", "click_tuck_ratio"),
                    ("ring", ratios[2], "ring_click_threshold", "ring_tuck_ratio")):
                distance = distances[finger]
                other = min(value for name, value in distances.items() if name != finger)
                other_click = min(world_distances[name] for name in ("middle", "ring", "pinky") if name != finger)
                if (on(f"thumb_{finger}_pinch") and ratio > float(c[tuck_key])
                        and distance <= float(c[threshold_key])
                        and distance < other * neighbor_ratio
                        and other - distance >= palm_len * CLICK_SEPARATION_PALM
                        and world_distances[finger] <= CLICK_WORLD_RELEASE
                        and world_distances[finger] < other_click * neighbor_ratio
                        and other_click - world_distances[finger] >= CLICK_SEPARATION_PALM):
                    world_contacts.add(finger)

        cursor_point_early = palm_anchor_point(pts, c["cursor_anchor_ratio"])
        # Clear contact must reach the click confirmer even when the projected hand is
        # still folded. Waiting for confirmation here deadlocks a click inside a latched fist.
        contact_claiming = bool(world_contacts or self._secondary_confirmed)

        # 0. Fist (fast move); the thumbs-up (motion mode entry pose) freezes the cursor instead
        curl = max(ratios)
        if c["motion_pose_freeze"] and not contact_claiming and curl <= float(c["motion_fold_ratio"]):
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
        if contact_claiming:
            self._fist = False
            self._fist_candidate_since = None
        elif not self._fist:
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
            # Opening a moving fist is not a scroll release and must not lock out clicks.
        if self._fist and on("fist"):
            if self._index_pinched:
                self._index_released_at = now
            self._index_pinched = False
            self._middle_hold_pinched = False
            self._drag_since = None
            self._secondary_since.clear()
            self._secondary_off.clear()
            self._secondary_confirmed.clear()
            return PinchFrame(active_gesture="fist", action="move_cursor", cursor_point=cursor_point_early,
                              pinch_distance=d_index, distances=distances, is_moving=True,
                              dpi_multiplier=max(0.1, float(c["fist_dpi_multiplier"])), hand_shape="fist")

        # 0.5 Show desktop: thumb tucked across the palm, four fingers extended, held still
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
        small_world_hand = world_distances is not None and palm_len < SMALL_PALM_IMAGE_SPAN

        mean_curl = sum(ratios) / 4.0
        k = max(0.05, min(1.0, float(c["shape_curl_smooth"])))
        self._curl_s = mean_curl if self._curl_s is None else self._curl_s + k * (mean_curl - self._curl_s)
        shape_mult = self.shape_dpi(self._curl_s, c)
        # Shape is geometry, independent of whether curling makes the speed faster or slower.
        curled = mean_curl < float(c["shape_curl_open"]) - .05

        # Thumb + index hysteresis
        if not self._index_pinched:
            self.last_hold = None
            middle_clearly_closer = d_middle < d_index * float(c["click_neighbor_ratio"])
            can_pinch = (d_index <= pinch_threshold and not index_tucked and not middle_clearly_closer
                         and not world_contacts and not self._secondary_confirmed
                         and (not small_world_hand or world_distances["index"] <= CLICK_WORLD_ENTER))
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
            # A nearby joint can still look pinched after the tips separate.
            d_hold = d_index if scrolling else min([d_index] + joints)
            self.last_hold = (round(d_hold, 4), round(rel_thr, 4), round(d_index, 4))
            world_released = small_world_hand and world_distances["index"] > CLICK_WORLD_RELEASE
            if d_hold > rel_thr or world_released:
                self._close_since = None
                if self._index_release_since is None:
                    self._index_release_since = now
                    self._release_clear = False
                if world_released or d_hold > rel_thr * float(c["scroll_freeze_ratio"]):
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
            if (self._index_pinched and in_entry_window and d_middle <= drag_thr and d_index <= drag_thr
                    and (not small_world_hand or world_distances["middle"] <= CLICK_WORLD_ENTER)):
                if self._drag_since is None:
                    self._drag_since = now
                if (now - self._drag_since) >= drag_confirm:
                    self._middle_hold_pinched = True
            else:
                self._drag_since = None
        else:
            if (not self._index_pinched or d_middle > release_threshold
                    or small_world_hand and world_distances["middle"] > CLICK_WORLD_RELEASE):
                self._middle_hold_pinched = False

        cursor_point = palm_anchor_point(pts, c["cursor_anchor_ratio"])
        pinch_point = ((thumb[0] + index[0]) * 0.5, (thumb[1] + index[1]) * 0.5)

        if self._index_pinched and index_active:
            self._secondary_since.clear()
            self._secondary_off.clear()
            self._secondary_confirmed.clear()
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
        click_tuck = float(c["click_tuck_ratio"])
        ring_thr = float(c["ring_click_threshold"])
        separation = palm_len * CLICK_SEPARATION_PALM
        nearest = min((("middle", d_middle), ("ring", d_ring), ("pinky", d_pinky)), key=lambda kv: kv[1])[0]
        entry = {
            "middle": (ratios[1] > click_tuck and nearest == "middle"
                       and d_middle < d_ring * neighbor_ratio and d_ring - d_middle >= separation),
            "ring": (ratios[2] > ring_tuck and nearest == "ring"
                     and d_ring < d_middle * neighbor_ratio and d_middle - d_ring >= separation),
            "pinky": (self._pinky_armed and nearest == "pinky" and ratios[3] > float(c["pinky_tuck_ratio"])
                      and d_pinky < d_ring * float(c["pinky_ring_dominance"])
                      and _d2(pts[RING_TIP], pts[PINKY_TIP]) > float(c["pinky_ring_min_gap"])
                      and (now - self._desktop_fired_at) >= exclusive),
        }
        thresholds = {"middle": click_threshold, "ring": ring_thr, "pinky": pinky_thr}
        all_side_fingers_open = (world_distances is not None and secondary_open
                                 and all(distances[name] > threshold + margin
                                         for name, threshold in thresholds.items()))
        self._side_open_last = all_side_fingers_open
        corroborated = set()
        if world_distances is not None:
            for finger in world_contacts:
                distance = distances[finger]
                next_tip = min(distances[name] for name in ("index", "middle", "ring", "pinky") if name != finger)
                if (distance <= thresholds[finger] and distance < next_tip * neighbor_ratio
                        and next_tip - distance >= separation
                        and world_distances[finger] <= CLICK_WORLD_FOLDED):
                    corroborated.add(finger)
        index_open = (world_distances is not None
                      and world_distances["index"] > CLICK_WORLD_RELEASE
                      and d_index > release_threshold)

        def blocked_by_scroll(finger: str) -> bool:
            # An ended scroll cannot swallow a distinct click with an open index.
            return locked and not (index_open and finger in corroborated)

        keep = {"middle": ratios[1] > click_tuck * .9, "ring": ratios[2] > ring_tuck * .9,
                "pinky": ratios[3] > float(c["pinky_tuck_ratio"]) * .9}
        if world_distances is not None:
            # Screen overlap is not contact. Prefer a clearly separated 3D fingertip; image distance
            # is a sensitivity gate for weaker evidence, not a second veto of clear 3D contact.
            for finger, curl_ratio, tuck in (("middle", ratios[1], click_tuck), ("ring", ratios[2], ring_tuck)):
                distance = world_distances[finger]
                other = min(value for name, value in world_distances.items() if name != finger)
                touching = finger in world_contacts
                entry[finger] = touching or (distance <= CLICK_WORLD_ENTER and distance < other * neighbor_ratio
                                 and other - distance >= CLICK_SEPARATION_PALM
                                 and (curl_ratio > tuck or touching))
                keep[finger] = distance <= CLICK_WORLD_RELEASE
            pinky_distance = world_distances["pinky"]
            entry["pinky"] = entry["pinky"] and clear_pinky_contact(world_distances, neighbor_ratio)
            keep["pinky"] = pinky_distance <= CLICK_WORLD_RELEASE
        middle_distal_hold = False
        if world_distances is not None and self._long_fired.get("middle", False) and not keep["middle"]:
            distal = _middle_distal_distance(world)
            other_tip = min(world_distances[name] for name in ("index", "ring", "pinky"))
            middle_distal_hold = distal <= CLICK_WORLD_FOLDED and distal < other_tip * neighbor_ratio
            keep["middle"] = middle_distal_hold

        if corroborated and world_distances is not None and not self._long_fired.get("middle", False):
            for finger in ("middle", "ring"):
                other = "ring" if finger == "middle" else "middle"
                if (finger in self._secondary_confirmed and other in corroborated
                        and distances[finger] > thresholds[finger] + margin
                        and world_distances[finger] > CLICK_WORLD_RELEASE):
                    self._drop_secondary(finger)
                    self._click_fresh[finger] = True
        # Drop stale candidates before testing any new finger: left/right evaluation order must not
        # decide which finger owns a pinch. Only a confirmed hold gets release hysteresis.
        for finger in tuple(self._secondary_since):
            if finger in self._secondary_confirmed:
                continue
            dist = distances[finger]
            clear_of_index = finger in world_contacts or not index_active or not (d_index < dist * neighbor_ratio)
            distance_ok = dist <= thresholds[finger] or finger in world_contacts
            if not (on(f"thumb_{finger}_pinch") and entry[finger] and distance_ok
                    and clear_of_index and not blocked_by_scroll(finger)):
                self._drop_secondary(finger)

        def confirmed(finger: str, dist: float, dip: float, thr: float,
                      entry_ok: bool = True, keep_ok: bool = True,
                      contact_entry: bool = False, contact_hold: bool = False,
                      confirm_time: Optional[float] = None) -> bool:
            if not on(f"thumb_{finger}_pinch"):
                self._drop_secondary(finger)
                return False
            if (finger in self._repeat_ready and finger in self._secondary_confirmed
                    and finger in corroborated):
                # Allow a repeat of the same click without freeing its ownership
                # early for a competing gesture during a brief opening.
                self._drop_secondary(finger)
                self._click_fresh[finger] = True
            elif not all_side_fingers_open:
                self._repeat_ready.discard(finger)
            if finger not in self._secondary_since:
                if c["side_pinch_exclusive"] and any(
                        other != finger for other in self._secondary_since):
                    return False
                if index_active and not contact_entry:
                    clear_of_index = not (d_index < dist * float(c["click_neighbor_ratio"]))
                else:
                    clear_of_index = True
                if (dist <= thr or contact_entry) and clear_of_index and entry_ok and not blocked_by_scroll(finger):
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
                if (held_dist > limit and not contact_hold) or not keep_ok:
                    off = self._secondary_off.setdefault(finger, now)
                    grace = click_grace
                    clearly_open = (all_side_fingers_open
                                    and held_dist > limit * float(c["drag_clear_ratio"]))
                    if (finger in ("middle", "ring") and finger in self._secondary_confirmed
                            and not self._long_fired.get(finger, False) and clearly_open
                            and side_open_last and now - off >= click_grace):
                        self._repeat_ready.add(finger)
                    if world_distances is not None and finger in self._secondary_confirmed:
                        grace = max(grace, CONTACT_RELEASE_GRACE)
                    if self._long_fired.get(finger, False):
                        grace = max(click_grace, float(c["pinch_release_grace"]))
                        image_open = held_dist > limit * float(c["drag_clear_ratio"])
                        depth_open = world_distances is not None and not keep_ok
                        if world_distances is not None and depth_open != image_open:
                            grace = max(grace, DRAG_DISAGREEMENT_GRACE)
                        elif image_open:
                            grace = min(grace, float(c["drag_clear_grace"]))
                    if now - off >= grace:
                        self._drop_secondary(finger)
                        self._click_fresh[finger] = True
                        return False
                else:
                    self._secondary_off.pop(finger, None)
            required_time = confirm if confirm_time is None else confirm_time
            # Shorten confirmation only with consecutive agreement and no nearby pinky.
            if (finger in corroborated and finger in corroborated_last
                    and world_distances["pinky"] > CLICK_WORLD_RELEASE):
                required_time *= .5
            if (now - self._secondary_since[finger]) >= required_time:
                self._secondary_confirmed.add(finger)
                return True
            return False

        def dip(tip_i: int) -> float:
            return _d2(thumb, pts[tip_i - 1])

        middle_ok = confirmed("middle", d_middle, dip(MIDDLE_TIP), click_threshold,
                              entry_ok=entry["middle"],
                              keep_ok=keep["middle"], contact_entry="middle" in world_contacts,
                              contact_hold=middle_distal_hold or (world_distances is not None
                              and world_distances["middle"] <= (CLICK_WORLD_RELEASE if "middle" in self._secondary_confirmed else CLICK_WORLD_ENTER)))
        ring_ok = confirmed("ring", d_ring, dip(RING_TIP), ring_thr,
                            entry_ok=entry["ring"],
                            keep_ok=keep["ring"], contact_entry="ring" in world_contacts,
                            contact_hold=world_distances is not None
                            and world_distances["ring"] <= (CLICK_WORLD_RELEASE if "ring" in self._secondary_confirmed else CLICK_WORLD_ENTER))
        self._corroborated_last = corroborated
        if any(f in self._secondary_since for f in ("middle", "ring")):
            self._pinky_armed = False
            self._pinky_clear_since = None
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
            keep_ok=keep["pinky"], entry_ok=entry["pinky"] and self._pinky_armed,
            confirm_time=max(confirm, float(c["pinky_confirm_time"])),
            contact_hold=world_distances is not None and "pinky" in self._secondary_confirmed
                         and world_distances["pinky"] <= CLICK_WORLD_RELEASE,
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
                    self._pinky_armed = False
                    self._pinky_clear_since = None
                    self._pinky_fired_at = now
                    self._desktop_since = None
                    return PinchFrame(active_gesture="thumb_pinky_pinch", action="task_switcher",
                                      cursor_point=cursor_point, pinch_distance=d_index, distances=distances)

        # A middle / ring / pinky pinch is held: cursor frozen; long press on thumb+middle -> drag
        if self._secondary_since and (not self._index_pinched or not index_active):
            held = next(f for f in ("middle", "ring", "pinky") if f in self._secondary_since)
            if (
                held == "middle"
                and held in self._secondary_confirmed
                and c["long_press_drag"]
                and (
                    self._long_fired.get("middle", False)
                    or "middle" not in self._secondary_off
                    and now - self._secondary_since["middle"] >= float(c["long_press_time"])
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
                dpi_multiplier=shape_mult if not self._index_pinched and not self._secondary_since else 1.0,
                hand_shape=("half" if curled else "open"),
            )

        return PinchFrame(pinch_distance=d_index, distances=distances)
