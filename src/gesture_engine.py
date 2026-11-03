"""
Gesture recognition engine and customizable configuration for Hand-Mouse.

Translates 21 MediaPipe hand landmarks into cursor movement and mouse actions:
- Fist (all four fingers curled): same movement as no pinch at `fist_dpi_multiplier` x DPI
- No pinch (any hand shape): fine cursor movement (relative + pointer acceleration by
  default), following a point on the wrist (#0) -> palm (middle finger MCP #9) line
- Thumb (#4) + Index (#8) pinch (with hysteresis): grab — Record & Fling: the pinch point
  path is recorded while pinched and replayed by the cursor (speed-scaled) on release
- Thumb (#4) + Index (#8) + Middle (#12) pinch: drag hold
- Thumb (#4) + Middle (#12) pinch: left click (with cooldown)
- Thumb (#4) + Ring (#16) pinch: right click (with cooldown)
- Thumb (#4) + Pinky (#20) pinch: drag toggle (with cooldown)
"""

from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "gestures.json"

VALID_ACTIONS = {
    "none",
    "move_cursor",
    "grab",
    "left_click",
    "right_click",
    "drag_hold",
    "drag_toggle",
    "alt_tab",
    "task_switcher",
    "show_desktop",
    "scroll_grab",
}

VALID_MOVEMENT_MODES = {"absolute", "relative"}

# Actions that carry a cursor point and move the cursor every frame
MOVING_ACTIONS = {"move_cursor", "drag_hold", "grab"}

# Thumb+index Record & Fling is OFF by default (it interfered with fists / clicks);
# it can be switched on in the settings window.
RECORD_FLING_DEFAULT = False

DEFAULT_CONFIG: Dict[str, Any] = {
    "record_fling_enabled": RECORD_FLING_DEFAULT,
    "grab_scroll_enabled": True,
    "scroll_gain": 24000.0,
    "scroll_deadzone": 0.01,
    "scroll_invert": False,
    "scroll_inertia": True,
    "scroll_release_margin": 0.02,
    "scroll_release_grace": 0.25,
    "hand_lost_reset": 0.3,
    "scroll_inertia_time": 0.4,
    "scroll_inertia_min_speed": 600.0,
    "pinch_threshold": 0.055,
    "release_threshold": 0.075,
    "pinch_release_grace": 0.15,
    "click_threshold": 0.052,
    "ring_click_threshold": 0.044,
    "smoothing": 0.35,
    "cursor_interpolation": True,
    "tremor_filter": True,
    "tremor_min_cutoff": 1.5,
    "tremor_beta": 8.0,
    "tremor_deadzone": 0.002,
    "active_margin": 0.30,
    "movement_mode": "relative",
    "relative_gain_min": 0.35,
    "relative_gain_max": 1.2,
    "relative_accel_speed_low": 0.1,
    "relative_accel_speed_high": 0.8,
    "cursor_anchor_ratio": 0.5,
    "fist_dpi_multiplier": 20.0,
    "pinky_tuck_ratio": 0.8,
    "gesture_exclusive_time": 1.0,
    "desktop_thumb_tuck": 0.45,
    "desktop_hold": 0.4,
    "desktop_extended_ratio": 1.45,
    "desktop_min_palm_width": 0.45,
    "desktop_min_tip_gap": 0.08,
    "pinky_click_threshold": 0.065,
    "ring_tuck_ratio": 0.8,
    "switcher_navigation": "pointer",
    "switcher_fist_multiplier": 15.0,
    "switcher_confirm_delay": 0.25,
    "switcher_step": 0.05,
    "switcher_timeout": 15.0,
    "switcher_lost_confirm": 1.0,
    "fist_curl_enter": 0.95,
    "fist_curl_exit": 1.2,
    "fist_confirm_time": 0.12,
    "index_tuck_ratio": 1.15,
    "pinch_fistlike_confirm": 0.12,
    "fling_strength": 1.0,
    "record_gain_min": 1.0,
    "record_gain_max": 4.0,
    "record_speed_low": 0.2,
    "record_speed_high": 1.2,
    "record_smoothing": 0.5,
    "record_release_trim": 0.06,
    "record_min_length": 0.01,
    "replay_min_duration": 0.15,
    "replay_max_duration": 0.45,
    "click_confirm_time": 0.06,
    "click_lockout_after_grab": 0.35,
    "click_dominance_ratio": 0.7,
    "click_release_margin": 0.02,
    "long_press_drag": True,
    "long_press_time": 0.6,
    "long_press_drag_multiplier": 15.0,
    "ui_magnet_enabled": True,
    "ui_magnet_radius": 28.0,
    "ui_magnet_min_factor": 0.7,
    "ui_magnet_max_size": 200,
    "click_tuck_ratio": 0.8,
    "click_neighbor_ratio": 0.85,
    "click_release_grace": 0.03,
    "drag_hold_threshold": 0.035,
    "drag_hold_entry_window": 0.25,
    "double_click_window": 0.45,
    "double_click_move_tolerance": 0.03,
    "fling_gain_x": 1.0,
    "fling_gain_y": 1.0,
    "gestures": {
        "fist": {
            "action": "move_cursor",
            "enabled": True,
            "description": "주먹 쥐기: 핀치 없음 이동과 같되 DPI x fist_dpi_multiplier(기본 20배)",
        },
        "no_pinch": {
            "action": "move_cursor",
            "enabled": True,
            "description": "핀치 없음(아무 손모양): 손목→손바닥 기준점으로 세부 커서 이동(상대좌표+가속)",
        },
        "thumb_tucked_open": {
            "action": "show_desktop",
            "enabled": True,
            "cooldown": 1.5,
            "description": "엄지만 접고 나머지 네 손가락 펴기 (0.4초 유지): 바탕화면 보기 Win+D",
        },
        "thumb_index_pinch": {
            "action": "grab",
            "enabled": True,
            "description": "엄지+검지 붙임: Record & Fling — 핀치한 채 그린 경로를 기록, 핀치를 떼는 순간 커서가 그 경로를 (그린 속도만큼 확대해) 그대로 그림",
        },
        "thumb_middle_pinch": {
            "action": "left_click",
            "enabled": True,
            "cooldown": 0.35,
            "description": "엄지+중지 붙임: 좌클릭",
        },
        "thumb_ring_pinch": {
            "action": "right_click",
            "enabled": True,
            "cooldown": 0.45,
            "description": "엄지+약지 붙임: 우클릭",
        },
        "thumb_index_middle_pinch": {
            "action": "drag_hold",
            "enabled": True,
            "description": "엄지+검지+중지 함께 붙임: 드래그(누른 상태 유지)",
        },
        "thumb_pinky_pinch": {
            "action": "task_switcher",
            "enabled": True,
            "cooldown": 0.6,
            "description": "엄지+소지(새끼) 붙임: 창 전환 화면 열기 → 손 움직임으로 프로그램 선택",
        },
    },
}


@dataclass
class Point3D:
    """Normalized 3D landmark point."""

    x: float
    y: float
    z: float = 0.0

    @classmethod
    def from_any(cls, item: Any) -> "Point3D":
        """Convert a MediaPipe landmark, object with .x/.y, tuple, or dict into Point3D."""
        if isinstance(item, Point3D):
            return item
        if hasattr(item, "x") and hasattr(item, "y"):
            return cls(
                x=float(item.x),
                y=float(item.y),
                z=float(getattr(item, "z", 0.0)),
            )
        if isinstance(item, dict):
            return cls(
                x=float(item["x"]),
                y=float(item["y"]),
                z=float(item.get("z", 0.0)),
            )
        if isinstance(item, (tuple, list)):
            return cls(
                x=float(item[0]),
                y=float(item[1]),
                z=float(item[2]) if len(item) > 2 else 0.0,
            )
        raise TypeError(f"Unsupported landmark type: {type(item)!r}")

    @classmethod
    def from_landmark(cls, landmark: Any) -> "Point3D":
        return cls.from_any(landmark)

    @classmethod
    def from_tuple(cls, coords: Sequence[float]) -> "Point3D":
        return cls.from_any(coords)


@dataclass
class GestureResult:
    """Output of a single frame's gesture evaluation."""

    active_gesture: str = "NONE"
    action: str = "none"
    cursor_point: Optional[Tuple[float, float]] = None
    pinch_distance: float = 1.0
    distances: Dict[str, float] = field(default_factory=dict)
    is_moving: bool = False
    pinch_point: Optional[Tuple[float, float]] = None  # thumb tip / index tip midpoint
    dpi_multiplier: float = 1.0  # cursor gain multiplier (fist = fast move)
    releasing: bool = False  # pinch fingers are opening (inside the release grace period)


class GestureEngine:
    """
    Processes 21 hand landmarks to detect pinch gestures with hysteresis
    and customizable gesture-to-action mappings.
    """

    WRIST = 0
    THUMB_IP = 3
    THUMB_TIP = 4
    INDEX_DIP = 7
    MIDDLE_MCP = 9
    # (MCP, TIP) for index, middle, ring, pinky
    FINGER_JOINTS = ((5, 8), (9, 12), (13, 16), (17, 20))
    INDEX_TIP = 8
    MIDDLE_TIP = 12
    RING_TIP = 16
    PINKY_TIP = 20

    def __init__(self, config_path: Optional[str | Path] = None) -> None:
        if config_path is None:
            self.config_path = DEFAULT_CONFIG_PATH
        else:
            candidate = Path(config_path)
            if not candidate.is_absolute() and not candidate.exists():
                alt = DEFAULT_CONFIG_PATH.parent.parent / candidate
                self.config_path = alt if alt.exists() else candidate
            else:
                self.config_path = candidate

        self.config: Dict[str, Any] = self.load_config(self.config_path)
        self._index_pinched: bool = False
        self._middle_hold_pinched: bool = False
        self._last_trigger_times: Dict[str, float] = {}
        # Click guards against thumb+index pinch being misread as a click
        self._secondary_since: Dict[str, float] = {}   # finger -> time its pinch was recognized
        self._secondary_off: Dict[str, float] = {}     # finger -> time it started looking released
        self._click_fresh: Dict[str, bool] = {}  # finger opened since its last click
        self._long_fired: Dict[str, bool] = {}   # long press reached: this pinch is now a drag
        self._index_released_at: float = -1e9
        self._drag_since: Optional[float] = None
        self._fist: bool = False
        self._fist_candidate_since: Optional[float] = None
        self._lost_since: Optional[float] = None
        self._index_release_since: Optional[float] = None
        self._index_pinched_since: float = -1e9
        self._pinch_candidate_since: Optional[float] = None
        self._desktop_since: Optional[float] = None
        self._desktop_armed: bool = True
        # Win+D and thumb+pinky (task switcher) use similar hand shapes: each one locks the
        # other out while it is in progress and for a moment after it fires
        self._pinky_fired_at: float = -1e9
        self._desktop_fired_at: float = -1e9

    @property
    def is_pinched(self) -> bool:
        """Whether the primary thumb+index pinch state is currently locked."""
        return self._index_pinched

    @property
    def pinched_fingers(self) -> set:
        """Fingers whose pinch with the thumb is currently recognized (for the overlay)."""
        held = {f for f in ("middle", "ring", "pinky") if f in self._secondary_since}
        if self._index_pinched:
            held.add("index")
        return held

    def load_config(self, path: Optional[str | Path] = None) -> Dict[str, Any]:
        """Load configuration from JSON file and merge with defaults."""
        target = Path(path) if path is not None else self.config_path
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        if target.exists():
            try:
                with open(target, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._deep_merge(cfg, loaded)
            except (OSError, json.JSONDecodeError):
                pass
        self.config = cfg
        return cfg

    def save_config(self, path: Optional[str | Path] = None) -> Path:
        """Persist current configuration to `config/gestures.json` (or custom path)."""
        target = Path(path) if path is not None else self.config_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        return target

    @staticmethod
    def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                GestureEngine._deep_merge(base[k], v)
            else:
                base[k] = copy.deepcopy(v)

    def set_gesture_action(
        self,
        gesture_id: str,
        action: str,
        enabled: bool = True,
    ) -> None:
        """Customize the action mapped to a gesture ID."""
        if action not in VALID_ACTIONS:
            raise ValueError(f"Unsupported action '{action}'. Must be one of {sorted(VALID_ACTIONS)}")
        gestures = self.config.setdefault("gestures", {})
        if gesture_id not in gestures:
            gestures[gesture_id] = {
                "action": action,
                "enabled": bool(enabled),
                "description": f"사용자 지정 제스처 ({gesture_id})",
            }
        else:
            gestures[gesture_id]["action"] = action
            gestures[gesture_id]["enabled"] = bool(enabled)

    def set_movement_mode(self, mode: str) -> None:
        """Switch cursor movement mode between 'absolute' and 'relative'."""
        mode_norm = mode.strip().lower()
        if mode_norm not in VALID_MOVEMENT_MODES:
            raise ValueError(f"Invalid movement_mode '{mode}'. Choose from {sorted(VALID_MOVEMENT_MODES)}")
        self.config["movement_mode"] = mode_norm

    def reset(self) -> None:
        """Reset hysteresis and cooldown states (e.g., when hand leaves frame)."""
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

    @staticmethod
    def _dist_3d(a: Point3D, b: Point3D) -> float:
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)

    @staticmethod
    def _dist_2d(a: Point3D, b: Point3D) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def _gesture_cfg(self, gesture_id: str) -> Dict[str, Any]:
        return self.config.get("gestures", {}).get(gesture_id, {})

    def _is_enabled(self, gesture_id: str) -> bool:
        cfg = self._gesture_cfg(gesture_id)
        return bool(cfg.get("enabled", False)) and cfg.get("action", "none") != "none"

    def _can_trigger(self, gesture_id: str, now: float, finger: Optional[str] = None) -> bool:
        """
        Finger pinches (clicks / task switcher) fire ONCE per pinch, like a mouse button:
        holding the pinch never repeats (it used to re-click every cooldown, which flooded
        right-click menus). Open the fingers and pinch again to click again; a quick double
        pinch is a real double-click. Other gestures use their cooldown.
        """
        if finger is not None:
            if not self._click_fresh.get(finger, True):
                return False
            self._click_fresh[finger] = False
            self._last_trigger_times[gesture_id] = now
            return True
        cfg = self._gesture_cfg(gesture_id)
        cooldown = float(cfg.get("cooldown", 0.35))
        last_time = self._last_trigger_times.get(gesture_id, -1e9)
        if (now - last_time) >= cooldown:
            self._last_trigger_times[gesture_id] = now
            return True
        return False

    def finger_curl_ratios(self, pts: Sequence[Point3D]) -> Tuple[float, ...]:
        """
        Per finger (index..pinky): dist(tip, wrist) / dist(MCP, wrist), in 3D.
        Extended fingers are ~1.7-2.0, a finger curled into the palm is < ~1.0.
        Using MediaPipe's depth (z) keeps fingers that point toward the camera (which look
        short in 2D) from being mistaken for curled ones.
        """
        wrist = pts[self.WRIST]
        ratios = []
        for mcp_i, tip_i in self.FINGER_JOINTS:
            base = self._dist_3d(pts[mcp_i], wrist)
            ratios.append(self._dist_3d(pts[tip_i], wrist) / base if base > 1e-6 else 9.9)
        return tuple(ratios)

    def palm_anchor_point(self, pts: Sequence[Point3D]) -> Tuple[float, float]:
        """
        Cursor reference point on the wrist (#0) -> palm (middle MCP #9) line.
        `cursor_anchor_ratio` 0.0 = wrist, 1.0 = middle finger base, 0.5 = palm center.
        """
        ratio = max(0.0, min(1.0, float(self.config.get("cursor_anchor_ratio", 0.5))))
        wrist = pts[self.WRIST]
        palm = pts[self.MIDDLE_MCP]
        return (
            wrist.x + (palm.x - wrist.x) * ratio,
            wrist.y + (palm.y - wrist.y) * ratio,
        )

    def process(
        self,
        landmarks: Optional[Sequence[Any]],
        timestamp: Optional[float] = None,
    ) -> GestureResult:
        """
        Evaluate a sequence of 21 hand landmarks and return GestureResult.
        """
        if not landmarks or len(landmarks) < 21:
            # A fast-moving hand blurs out of detection for a few frames. Keep the pinch /
            # click state through short losses (a scroll or drag in progress must not count
            # as released); only forget it after hand_lost_reset seconds.
            now_lost = float(timestamp) if timestamp is not None else time.perf_counter()
            if self._lost_since is None:
                self._lost_since = now_lost
            if now_lost - self._lost_since >= float(self.config.get("hand_lost_reset", 0.3)):
                self.reset()
            return GestureResult(
                active_gesture="NONE",
                action="none",
                cursor_point=None,
                pinch_distance=1.0,
                distances={},
                is_moving=False,
            )

        now = float(timestamp) if timestamp is not None else time.perf_counter()
        self._lost_since = None

        pts = [Point3D.from_any(lm) for lm in landmarks[:21]]
        thumb = pts[self.THUMB_TIP]
        index = pts[self.INDEX_TIP]
        middle = pts[self.MIDDLE_TIP]
        ring = pts[self.RING_TIP]
        pinky = pts[self.PINKY_TIP]

        d_index = self._dist_2d(thumb, index)
        d_middle = self._dist_2d(thumb, middle)
        d_ring = self._dist_2d(thumb, ring)
        d_pinky = self._dist_2d(thumb, pinky)

        distances = {
            "index": d_index,
            "middle": d_middle,
            "ring": d_ring,
            "pinky": d_pinky,
        }

        cursor_point_early = self.palm_anchor_point(pts)

        # 0. Fist (all four fingers curled) -> fast move. Checked first: in a fist the thumb
        # rests on the index/middle fingers and must not be read as a pinch or click.
        # But a fist can never START while a thumb+index pinch is held (incl. its release
        # grace): pinching curls the other fingers and bends the index, which looks like a
        # fist and used to cut Record & Fling recordings short.
        ratios = self.finger_curl_ratios(pts)
        curl = max(ratios)
        fist_enter = float(self.config.get("fist_curl_enter", 0.95))
        # Record & Fling off: a thumb+index touch has no job, so it must not get in the way
        record_on = bool(self.config.get("record_fling_enabled", RECORD_FLING_DEFAULT)) and self._is_enabled(
            "thumb_index_pinch"
        )
        # Grab-scroll: thumb+index pinch grabs the page and scrolls it like a touchscreen.
        # Record & Fling takes priority when both are switched on.
        scroll_on = (not record_on) and bool(self.config.get("grab_scroll_enabled", True))
        index_active = record_on or scroll_on  # thumb+index pinch has a job
        # Fist vs thumb+index pinch are told apart by the INDEX finger:
        #  - fist : index tip tucked into the palm (ratio <= index_tuck_ratio)
        #  - pinch: index bent but its tip still reaches past its base to meet the thumb
        index_tucked = ratios[0] <= float(self.config.get("index_tuck_ratio", 1.15))
        # Other three fingers at least half closed (on the way to a fist, or an "OK" pinch)
        others_curled = max(ratios[1:]) <= 1.35
        if not self._fist:
            if (not self._index_pinched or not index_active) and index_tucked and curl <= fist_enter:
                # every finger curled INTO the palm, held for fist_confirm_time
                if self._fist_candidate_since is None:
                    self._fist_candidate_since = now
                if now - self._fist_candidate_since >= float(self.config.get("fist_confirm_time", 0.12)):
                    self._fist = True
                    self._fist_candidate_since = None
                    self._pinch_candidate_since = None
            else:
                self._fist_candidate_since = None
        elif curl > float(self.config.get("fist_curl_exit", 1.2)):
            self._fist = False
            self._index_released_at = now  # no click from the fingers opening
        if self._fist and self._is_enabled("fist"):
            if self._index_pinched:
                self._index_released_at = now
            self._index_pinched = False
            self._middle_hold_pinched = False
            self._drag_since = None
            self._secondary_since.clear()
            action = str(self._gesture_cfg("fist").get("action", "move_cursor"))
            return GestureResult(
                active_gesture="fist",
                action=action,
                cursor_point=cursor_point_early,
                pinch_distance=d_index,
                distances=distances,
                is_moving=action in MOVING_ACTIONS,
                dpi_multiplier=max(0.1, float(self.config.get("fist_dpi_multiplier", 20.0))),
            )

        # 0.5 Show desktop (Win+D): thumb tucked across the palm, the other four fingers
        # extended, held for desktop_hold. Fires once per hold (open/close to repeat).
        # None of the pinches look like this (they all bend a finger toward the thumb).
        palm_len = max(1e-6, self._dist_2d(pts[self.WRIST], pts[self.MIDDLE_MCP]))
        thumb_tuck = min(self._dist_2d(thumb, pts[13]), self._dist_2d(thumb, pts[17])) / palm_len
        distances["thumb_tuck"] = thumb_tuck
        palm_width = self._dist_2d(pts[5], pts[17]) / palm_len
        exclusive = float(self.config.get("gesture_exclusive_time", 1.0))
        desktop_posture = (
            not self._index_pinched
            and self._is_enabled("thumb_tucked_open")
            and min(ratios) > float(self.config.get("desktop_extended_ratio", 1.45))
            and thumb_tuck <= float(self.config.get("desktop_thumb_tuck", 0.45))
            and palm_width >= float(self.config.get("desktop_min_palm_width", 0.45))
            and "pinky" not in self._secondary_since
            and not self._secondary_since
            and min(d_index, d_middle, d_ring, d_pinky) > float(self.config.get("desktop_min_tip_gap", 0.08))
            and (now - self._pinky_fired_at) >= exclusive
        )
        if desktop_posture:
            if self._desktop_since is None:
                self._desktop_since = now
            if (
                self._desktop_armed
                and now - self._desktop_since >= float(self.config.get("desktop_hold", 0.4))
                and self._can_trigger("thumb_tucked_open", now)
            ):
                self._desktop_armed = False
                self._desktop_fired_at = now
                action = str(self._gesture_cfg("thumb_tucked_open").get("action", "show_desktop"))
                return GestureResult(
                    active_gesture="thumb_tucked_open",
                    action=action,
                    cursor_point=cursor_point_early,
                    pinch_distance=d_index,
                    distances=distances,
                    is_moving=False,
                )
        else:
            self._desktop_since = None
            self._desktop_armed = True

        pinch_threshold = float(self.config.get("pinch_threshold", 0.055))
        release_threshold = float(self.config.get("release_threshold", 0.075))
        click_threshold = float(self.config.get("click_threshold", 0.052))

        # Hysteresis for primary thumb + index pinch (#4 and #8).
        # Entering uses the tips only (strict). While held, the pinch survives camera-angle
        # dropouts: tips hidden behind each other drift, but the joints next to them
        # (thumb IP #3, index DIP #7) stay close, and short gaps are bridged by a grace time.
        if not self._index_pinched:
            # A tucked index (thumb resting on it in a fist) is never a pinch. With the other
            # three fingers curled (fist-like hand) the pinch must hold for
            # pinch_fistlike_confirm, so a hand closing into a fist can still become one.
            # ...and not when the thumb is clearly on the MIDDLE finger instead (a thumb+middle
            # click with the index merely nearby must not grab / scroll the page). Both tips
            # together (drag) are about equally close, so that still pinches.
            middle_clearly_closer = d_middle < d_index * float(self.config.get("click_neighbor_ratio", 0.85))
            can_pinch = d_index <= pinch_threshold and not index_tucked and not middle_clearly_closer
            if can_pinch and others_curled:
                if self._pinch_candidate_since is None:
                    self._pinch_candidate_since = now
                can_pinch = (now - self._pinch_candidate_since) >= float(
                    self.config.get("pinch_fistlike_confirm", 0.12)
                )
            elif not can_pinch:
                self._pinch_candidate_since = None
            if can_pinch:
                self._index_pinched = True
                self._index_pinched_since = now
                self._pinch_candidate_since = None
                self._index_release_since = None
        else:
            d_hold = min(
                d_index,
                self._dist_2d(thumb, pts[self.INDEX_DIP]),
                self._dist_2d(pts[self.THUMB_IP], index),
            )
            # While the page is grabbed (scrolling) the hand moves fast and the landmarks
            # jitter: require the fingers to open clearly and for longer before it counts as
            # released (a release starts the scroll inertia).
            scrolling = scroll_on
            rel_thr = release_threshold + (float(self.config.get("scroll_release_margin", 0.02)) if scrolling else 0.0)
            if d_hold > rel_thr:
                if self._index_release_since is None:
                    self._index_release_since = now
                grace = max(0.0, float(self.config.get("pinch_release_grace", 0.15)))
                if scrolling:
                    grace = max(grace, float(self.config.get("scroll_release_grace", 0.25)))
                if (now - self._index_release_since) >= grace:
                    self._index_pinched = False
                    self._index_release_since = None
                    if index_active or self._middle_hold_pinched:
                        self._index_released_at = now  # click lockout only after a real grab/drag
            else:
                self._index_release_since = None

        # Hysteresis for middle finger when combined with thumb+index for drag_hold.
        # Stricter than a click (middle drifts near the thumb while forming a thumb+index
        # pinch) and must be held for click_confirm_time, otherwise the mouseDown/up of a
        # brief drag_hold looks exactly like an accidental left click.
        # Drag must be formed together with the index pinch (within drag_hold_entry_window):
        # the middle finger curling toward the thumb in the middle of a Record & Fling
        # recording must not hijack it.
        drag_thr = float(self.config.get("drag_hold_threshold", 0.035))
        drag_confirm = float(self.config.get("click_confirm_time", 0.06))
        drag_window = float(self.config.get("drag_hold_entry_window", 0.25))
        in_entry_window = (now - self._index_pinched_since) <= drag_window
        if not self._middle_hold_pinched:
            # all three tips really together: an index merely NEAR the thumb during a
            # thumb+middle click must not turn it into a drag
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

        cursor_point: Tuple[float, float] = self.palm_anchor_point(pts)
        pinch_point: Tuple[float, float] = ((thumb.x + index.x) * 0.5, (thumb.y + index.y) * 0.5)

        if self._index_pinched and index_active:
            self._secondary_since.clear()

        # 1. Priority: Thumb + Index + Middle pinch -> drag_hold (or custom mapped action)
        if self._index_pinched and self._middle_hold_pinched and self._is_enabled("thumb_index_middle_pinch"):
            action = str(self._gesture_cfg("thumb_index_middle_pinch").get("action", "drag_hold"))
            return GestureResult(
                active_gesture="thumb_index_middle_pinch",
                action=action,
                cursor_point=cursor_point,
                pinch_distance=d_index,
                distances=distances,
                is_moving=True,
            )

        # 2. Thumb + Index pinch -> grab (Record & Fling) when enabled. When it is off the
        # touch is ignored: middle / ring / pinky clicks still work (the index often ends up
        # near the thumb during them) and otherwise the cursor keeps moving (see below).
        if self._index_pinched and scroll_on:
            # page grabbed: the cursor stays put, hand motion becomes scrolling
            return GestureResult(
                active_gesture="thumb_index_pinch",
                action="scroll_grab",
                cursor_point=cursor_point,
                pinch_distance=d_index,
                distances=distances,
                is_moving=False,
                pinch_point=pinch_point,
                # the fingers already opened; the pinch is only being held by the release
                # grace: the hand's follow-through / spring-back must not scroll the page
                releasing=self._index_release_since is not None,
            )
        if self._index_pinched and record_on:
            action = (
                str(self._gesture_cfg("thumb_index_pinch").get("action", "none"))
                if self._is_enabled("thumb_index_pinch")
                else "none"
            )
            is_moving = action in MOVING_ACTIONS
            return GestureResult(
                active_gesture="thumb_index_pinch",
                action=action,
                cursor_point=cursor_point,
                pinch_distance=d_index,
                distances=distances,
                is_moving=is_moving,
                pinch_point=pinch_point,
            )

        # 3. Discrete secondary finger pinches (when index is NOT pinched).
        # Guards against misreading a thumb+index pinch as a click:
        #  - the clicking finger must be clearly closer to the thumb than the index
        #  - the pinch must hold for click_confirm_time (no single-frame clicks)
        #  - no clicks right after a thumb+index pinch ends (snap/release noise)
        confirm = float(self.config.get("click_confirm_time", 0.06))
        lockout = float(self.config.get("click_lockout_after_grab", 0.35))
        ratio = float(self.config.get("click_dominance_ratio", 0.7))
        locked = (now - self._index_released_at) < lockout

        pinky_thr = float(self.config.get("pinky_click_threshold", 0.065))
        ring_tuck = float(self.config.get("ring_tuck_ratio", 0.8))

        margin = float(self.config.get("click_release_margin", 0.02))
        click_grace = float(self.config.get("click_release_grace", 0.03))

        def confirmed(finger: str, dist: float, dip: float, thr: float,
                      entry_ok: bool = True, keep_ok: bool = True) -> bool:
            """
            Same robustness as the thumb+index pinch (they used to flicker):
            - enter at `thr`, release only beyond `thr + margin` (hysteresis)
            - the "clearly closer than the neighbour" checks apply when ENTERING only
            - while held, the finger's DIP joint counts too (tip hidden by the camera angle)
            - dropouts shorter than click_release_grace are bridged
            """
            if not self._is_enabled(f"thumb_{finger}_pinch"):
                self._secondary_since.pop(finger, None)
                self._secondary_off.pop(finger, None)
                return False
            if finger not in self._secondary_since:
                # Next to the index only matters when thumb+index has a job (Record & Fling on).
                # (A curled index is NOT a reason to refuse: people fold it out of the way to
                # bring the thumb to the middle / ring finger.)
                # Only refuse when the INDEX is clearly the closer finger (then it is a thumb+index
                # grab). The index sits right next to the middle finger, so requiring the clicking
                # finger to be clearly closer than the index blocked almost every real middle click.
                # (Record & Fling, opt-in, keeps the strict rule: forming its pinch sweeps the
                # middle finger past the thumb.)
                if record_on:
                    clear_of_index = dist < d_index * ratio
                elif index_active:
                    clear_of_index = not (d_index < dist * float(self.config.get("click_neighbor_ratio", 0.85)))
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
                if min(dist, dip) > thr + margin or not keep_ok:
                    off = self._secondary_off.setdefault(finger, now)
                    grace = click_grace
                    if self._long_fired.get(finger, False):  # dragging: a dropout must not drop the file
                        grace = max(click_grace, float(self.config.get("pinch_release_grace", 0.15)))
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
            return self._dist_2d(thumb, pts[tip_i - 1])

        click_tuck = float(self.config.get("click_tuck_ratio", 0.8))
        # Only the finger NEAREST the thumb (of middle / ring / pinky) may start a pinch.
        # Neighbours sit right next to each other, so "clearly closer than the neighbour"
        # rules blocked real pinches (both touching -> nothing) and letting each finger
        # decide alone fired a left AND a right click together.
        nearest = min((("middle", d_middle), ("ring", d_ring), ("pinky", d_pinky)), key=lambda kv: kv[1])[0]
        # A finger curled deep into the palm (fist) with the thumb resting on it is no click
        middle_ok = confirmed("middle", d_middle, dip(self.MIDDLE_TIP), click_threshold,
                              entry_ok=ratios[1] > click_tuck and nearest == "middle",
                              keep_ok=ratios[1] > click_tuck * 0.9)
        # thumb+ring has its own (slightly stricter) distance: the ring tip often reads as
        # touching before it really does
        ring_thr = float(self.config.get("ring_click_threshold", 0.044))
        ring_ok = confirmed("ring", d_ring, dip(self.RING_TIP), ring_thr,
                            entry_ok=ratios[2] > ring_tuck and nearest == "ring",
                            keep_ok=ratios[2] > ring_tuck * 0.9)
        # Thumb+pinky (task switcher): the pinky tip is hard to reach and often hidden, so it
        # gets its own (larger) threshold
        pinky_ok = confirmed(
            "pinky", d_pinky, dip(self.PINKY_TIP), pinky_thr,
            keep_ok=ratios[3] > float(self.config.get("pinky_tuck_ratio", 0.8)) * 0.9,
            entry_ok=(
                nearest == "pinky"
                # pinky folded deep into the palm (fist): the thumb rests on it
                and ratios[3] > float(self.config.get("pinky_tuck_ratio", 0.8))
                # not right after Win+D
                and (now - self._desktop_fired_at) >= exclusive
            ),
        )

        if not self._index_pinched or not index_active:
            if middle_ok and self._is_enabled("thumb_middle_pinch"):
                if self._can_trigger("thumb_middle_pinch", now, "middle"):
                    action = str(self._gesture_cfg("thumb_middle_pinch").get("action", "left_click"))
                    return GestureResult(
                        active_gesture="thumb_middle_pinch",
                        action=action,
                        cursor_point=cursor_point,
                        pinch_distance=d_index,
                        distances=distances,
                        is_moving=False,
                    )

            if ring_ok and self._is_enabled("thumb_ring_pinch"):
                if self._can_trigger("thumb_ring_pinch", now, "ring"):
                    action = str(self._gesture_cfg("thumb_ring_pinch").get("action", "right_click"))
                    return GestureResult(
                        active_gesture="thumb_ring_pinch",
                        action=action,
                        cursor_point=cursor_point,
                        pinch_distance=d_index,
                        distances=distances,
                        is_moving=False,
                    )

            if pinky_ok and self._is_enabled("thumb_pinky_pinch"):
                if self._can_trigger("thumb_pinky_pinch", now, "pinky"):
                    self._pinky_fired_at = now
                    self._desktop_since = None
                    action = str(self._gesture_cfg("thumb_pinky_pinch").get("action", "task_switcher"))
                    return GestureResult(
                        active_gesture="thumb_pinky_pinch",
                        action=action,
                        cursor_point=cursor_point,
                        pinch_distance=d_index,
                        distances=distances,
                        is_moving=False,
                    )

        # A middle / ring / pinky pinch is being held (already clicked, or still confirming):
        # keep the cursor frozen and report it steadily. Freezing used to follow a different
        # distance than the held state, so the cursor / HUD flipped between moving and frozen.
        if self._secondary_since and (not self._index_pinched or not index_active):
            held = next(f for f in ("middle", "ring", "pinky") if f in self._secondary_since)
            # Long press on the left-click pinch -> drag: the left button goes down and the
            # cursor follows the hand until the pinch is released (drop). Like press-and-hold
            # on a touchscreen; ideal for dragging files.
            if (
                held == "middle"
                and self.config.get("long_press_drag", True)
                and self._gesture_cfg("thumb_middle_pinch").get("action", "left_click") == "left_click"
                and (
                    self._long_fired.get("middle", False)
                    or now - self._secondary_since["middle"] >= float(self.config.get("long_press_time", 0.6))
                )
            ):
                self._long_fired["middle"] = True
                return GestureResult(
                    active_gesture="thumb_middle_pinch",
                    action="drag_hold",
                    cursor_point=cursor_point,
                    pinch_distance=d_index,
                    distances=distances,
                    is_moving=True,
                    # held-pinch drag moves the cursor fast (like the fist)
                    dpi_multiplier=max(0.1, float(self.config.get("long_press_drag_multiplier", 15.0))),
                )
            return GestureResult(
                active_gesture=f"thumb_{held}_pinch",
                action="none",
                cursor_point=None,
                pinch_distance=d_index,
                distances=distances,
                is_moving=False,
            )

        # Record & Fling off: a thumb+index touch is ignored, the cursor keeps moving
        if self._index_pinched and not index_active:
            action = str(self._gesture_cfg("no_pinch").get("action", "move_cursor"))
            return GestureResult(
                active_gesture="no_pinch",
                action=action,
                cursor_point=cursor_point,
                pinch_distance=d_index,
                distances=distances,
                is_moving=action in MOVING_ACTIONS,
            )

        # 4. No pinch in any finger -> move cursor (any hand shape).
        # A finger approaching the thumb (within release_threshold) freezes the cursor
        # so it stays put right before a click lands.
        near_pinch = min(d_index, d_middle, d_ring, d_pinky) <= release_threshold
        if not near_pinch and self._is_enabled("no_pinch"):
            action = str(self._gesture_cfg("no_pinch").get("action", "move_cursor"))
            return GestureResult(
                active_gesture="no_pinch",
                action=action,
                cursor_point=cursor_point,
                pinch_distance=d_index,
                distances=distances,
                is_moving=action in MOVING_ACTIONS,
            )

        return GestureResult(
            active_gesture="NONE",
            action="none",
            cursor_point=None,
            pinch_distance=d_index,
            distances=distances,
            is_moving=False,
        )

    # Alias for backward compatibility
    def evaluate(
        self,
        landmarks: Optional[Sequence[Any]],
        timestamp: Optional[float] = None,
    ) -> GestureResult:
        return self.process(landmarks, timestamp=timestamp)
