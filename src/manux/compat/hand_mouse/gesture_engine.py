"""
Gesture recognition engine and customizable configuration for Hand-Mouse.

Translates 21 MediaPipe hand landmarks into cursor movement and mouse actions:
- How far the fingers are curled sets the cursor speed, continuously, between two numbers: an open hand moves
  the cursor at `shape_dpi_min` x DPI (1), a fist at `fist_dpi_multiplier` x DPI (30), and a loosely curled hand
  (a "half fist") anywhere in between, on a log scale (see `shape_dpi`). It applies where the cursor moves
  freely (no pinch); a pinch pose with curled fingers and the thumb on a fingertip is not sped up.
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
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from manux.compat.hand_mouse import profiles

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
    "scroll_gain": 36000.0,
    "scroll_deadzone": 0.01,
    "scroll_invert": False,
    "scroll_inertia": True,
    "pinch_hold_joint_ratio": 0.6,
    "click_hold_joint_ratio": 0.75,
    "drag_clear_ratio": 1.3,
    "drag_clear_grace": 0.11,
    "scroll_release_margin": 0.015,
    "scroll_freeze_ratio": 1.25,
    "scroll_freeze_delay": 0.04,
    "pinch_rearm_time": 0.03,
    "scroll_release_grace": 0.14,
    "hand_lost_reset": 0.3,
    "scroll_inertia_time": 0.4,
    "scroll_inertia_min_speed": 600.0,
    "scroll_smooth_tau": 0.03,
    "scroll_inertia_seamless": True,
    "scroll_cursor_hold": 0.35,
    "scroll_center_cursor": True,
    "scroll_center_tolerance": 24,
    "scroll_target_uia": True,
    "scroll_target_wait": 0.15,
    "scroll_center_fallback_window": False,
    "scroll_center_max_fraction": 0.6,
    "scroll_session_hold": 1.2,
    "scroll_series_gap": 4.0,
    "scroll_series_dwell": 0.4,
    "approach_freeze_distance": 0.045,
    "scroll_inertia_axis_lock": 0.5,
    "scroll_inertia_max_speed": 3600.0,
    "pinch_threshold": 0.055,
    "release_threshold": 0.075,
    "pinch_release_grace": 0.15,
    "click_threshold": 0.04,
    "ring_click_threshold": 0.044,
    "smoothing": 0.35,
    "cursor_interpolation": True,
    "tremor_filter": True,
    "tremor_min_cutoff": 2.0,
    "tremor_beta": 30.0,
    "tremor_deadzone": 0.002,
    "active_margin": 0.30,
    "movement_mode": "relative",
    "movement_scheme": "legacy_shape",   # "legacy_shape" | "velocity"
    "return_suppression": "off",         # "off" | "clutch" | "clutch_auto"
    "velocity_gain_min": 0.55,
    "velocity_gain_mid": 2.8,
    "velocity_gain_max": 6.5,
    "velocity_deadzone": 0.02,
    "velocity_stationary_speed": 0.05,
    "velocity_mid_speed": 0.40,
    "velocity_high_speed": 1.30,
    "velocity_ema_alpha": 0.35,
    "velocity_slew_up": 14.0,
    "velocity_slew_down": 18.0,
    "shape_mode_curl_smooth": 0.25,
    "shape_mode_precise_enter": 1.50,
    "shape_mode_precise_exit": 1.34,
    "shape_mode_fast_enter": 0.90,
    "shape_mode_fast_exit": 1.08,
    "shape_mode_min_hold": 0.20,
    "velocity_mode_precise_min": 0.40,
    "velocity_mode_precise_mid": 1.8,
    "velocity_mode_precise_max": 4.0,
    "velocity_mode_fast_min": 0.75,
    "velocity_mode_fast_mid": 3.8,
    "velocity_mode_fast_max": 9.0,
    "velocity_c2f_enabled": True,
    "velocity_c2f_entry_speed": 0.45,
    "velocity_c2f_aim_speed": 0.32,
    "velocity_c2f_decel_thresh": 0.80,
    "velocity_c2f_window": 0.28,
    "velocity_c2f_gain_scale": 0.75,
    "velocity_c2f_slew_boost": 2.2,
    "velocity_adaptive_filter": True,
    "velocity_filter_min_cutoff": 1.1,
    "velocity_filter_c2f_cutoff": 0.80,
    "velocity_filter_beta": 14.0,
    "ui_magnet_max_gain": 1.8,
    "ui_magnet_precision_scale": 0.85,
    "edge_reclutch_enabled": True,
    "edge_reclutch_margin": 0.06,
    "edge_reclutch_release_margin": 0.12,
    "clutch_size_ema_alpha": 0.30,
    "clutch_depth_enter_ratio": 0.80,
    "clutch_depth_exit_ratio": 0.86,
    "clutch_min_hold": 0.15,
    "clutch_max_hold": 1.20,
    "return_auto_min_fwd_dist": 0.035,
    "return_auto_min_fwd_speed": 0.16,
    "return_auto_post_window": 0.65,
    "return_auto_min_angle_deg": 120.0,
    "return_auto_buffer_s": 0.085,
    "return_auto_confirm_dist": 0.020,
    "return_auto_min_speed_ratio": 0.15,
    "return_auto_release_pause": 0.15,
    "return_auto_max_freeze": 1.10,
    "relative_gain_min": 0.35,
    "relative_gain_max": 1.2,
    "relative_accel_speed_low": 0.1,
    "relative_accel_speed_high": 0.8,
    "cursor_anchor_ratio": 0.5,
    "cursor_max_step": 0.12,
    "drag_precision": True,
    "drag_precision_min": 2.5,
    "drag_precision_low": 0.3,
    "drag_precision_high": 1.2,
    "fist_dpi_multiplier": 30.0,
    "crop_tracker": "off",           # while the detector has lost the hand: "mil" | "nano" | "vit" video tracker keeps the crop on it
    "crop_track_seconds": 3.0,       # ... for at most this long
    "crop_scale": 2.4,               # crop side = this x the hand's size; smaller = tighter zoom, but a moving hand leaves it sooner
    "crop_min_px": 96,               # smallest side (px) of the crop around a far hand; smaller = tighter zoom (docs/far-hand-loss.md)
    "distance_gain_ref": 0.11,       # hand size (frame widths) at which the cursor gain is 1; smaller = farther = more gain; 0 = off
    "distance_gain_max": 3.0,        # the largest factor for a far hand
    "distance_gain_strength": 1.0,   # 1 = the cursor covers as much screen as at the reference distance, 0.5 = half way
    "shape_dpi_enabled": True,
    "shape_dpi_min": 1.0,            # open hand
    "shape_curl_open": 1.45,         # mean finger curl ratio at/above which the hand counts as open
    "shape_curl_fist": 0.85,         # ... at/below which it counts as a fist (fist_dpi_multiplier)
    "shape_dpi_curve": 1.0,          # < 1 speeds up sooner when the fingers start to curl, > 1 later
    "shape_curl_smooth": 0.4,        # smoothing of the curl between frames (1 = none)
    "shape_freeze_distance": 0.03,   # curled hand: only a thumb this close to a fingertip freezes the cursor
    # Half fist (the fast cursor shape) while the hand is moving faster than this (frame widths / s): no "about to
    # pinch" freeze and no new pinch. Pinches are made with the hand nearly still; moving the cursor with a half fist
    # kept the thumb 0.03-0.045 from the curled fingers, which stopped the cursor (40 % of half-fist frames) and
    # started scrolls / drags (22 %) in the 2026-11-04 22:2x logs. 0 = off.
    "curled_moving_speed": 0.3,
    # Half fist: thumb+index grab (scroll) needs this close relative to the hand size (instead of pinch_size_ratio).
    "curled_pinch_size_ratio": 0.13,
    # Half fist: thumb+middle (left click) needs this close relative to the hand size, and a held thumb+middle does
    # not turn into a drag while the hand is half curled (the drags in those logs all came from that).
    "curled_click_size_ratio": 0.15,
    "pinky_tuck_ratio": 0.8,
    "gesture_exclusive_time": 1.0,
    "desktop_thumb_tuck": 0.6,
    "desktop_hold": 0.4,
    "desktop_horizontal_min": 0.7,
    # Optional: measure "sideways" against the way the open hand usually points, learned slowly (seconds
    # of the average, starting from "up in the image"). 0 = off: sideways is horizontal in the image.
    # Off by default: this user's resting hand already leans 45-60 degrees, and while practising the
    # gesture the average drifted toward the sideways hand until it stopped working (2026-11-04 logs).
    "desktop_usual_tau": 0.0,
    "desktop_still_move": 0.03,
    "side_pinch_exclusive": True,
    "desktop_after_activity": 1.0,
    "desktop_after_curl": 2.0,
    "desktop_extended_ratio": 1.55,
    "desktop_min_palm_width": 0.45,
    "desktop_min_tip_gap": 0.0,
    "pinky_click_threshold": 0.05,
    "pinky_ring_dominance": 0.85,
    "pinky_ring_min_gap": 0.025,
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
    "fist_upright": True,         # a fist turned on its side (thumb on top) also counts as a fist (upright_fist)
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
    "click_release_margin": 0.01,
    "long_press_drag": True,
    "long_press_time": 1.0,
    "long_press_drag_multiplier": 15.0,
    "drag_on_move": True,
    "drag_move_distance": 0.03,
    "drag_move_min_time": 0.15,
    "start_in_tray": False,  # next launch starts in the tray (no camera window) instead of showing it
    "ui_magnet_enabled": True,
    "ui_magnet_radius": 28.0,
    "ui_magnet_min_factor": 0.7,
    "ui_magnet_max_size": 200,
    "click_tuck_ratio": 0.8,
    "click_neighbor_ratio": 0.85,
    "motion_thumb_straight": 0.85,
    "motion_thumb_up": 0.7,
    "motion_thumb_len": 0.5,
    "motion_max_palm_ratio": 1.7,
    "click_fist_max_curl": 1.0,
    "pinch_after_curl_below": 1.15,
    "pinch_after_curl_window": 0.5,
    "pinch_after_curl_confirm": 0.3,
    "click_follow_confirm": 0.0,
    "click_rise_release": 0.012,
    "click_rise_min": 0.022,
    "click_rise_window": 0.35,
    "click_keep_after": 0.3,
    "click_keep_limit": 0.06,
    "side_pinch_gap": 0.2,
    # Thumb+ring (right click) and thumb+pinky (task switcher) do not START while the hand is half curled
    # (the fast cursor shape) or within this many seconds after: the curled ring / little finger lies next to
    # the thumb there (2026-11-04 logs: right clicks and switchers fired while moving with a half fist).
    "side_pinch_after_curl": 0.4,
    # ... and after a half fist the thumb must first leave the ring / little finger (this far relative to the hand
    # size) before a pinch with it counts: opening the hand left the thumb resting 0.17-0.2 from the ring tip, and a
    # right click fired the moment side_pinch_after_curl ran out (2026-11-04 23:1x logs). 0 = off.
    "side_rearm_ratio": 0.35,
    "click_curled_below": 1.4,
    "click_curled_threshold": 0.03,
    # A pinch STARTS only when the thumb is also this close relative to the hand size (largest of wrist -> index /
    # middle / little-finger knuckle and 1.4 x the knuckle width, in the image). The distance thresholds above are in
    # image units, so a hand that looks small (far from the camera, a phone camera) got a much looser touch: in the
    # 2026-11-04 logs many pinches never came closer than 1/4 of the hand (ring 19 of 42, pinky 23 of 78) - the
    # fingers did not touch. Releasing still uses the image-unit thresholds. 0 = off.
    "pinch_size_ratio": 0.2,
    "click_size_ratio": 0.22,
    "ring_click_size_ratio": 0.25,
    "pinky_click_size_ratio": 0.25,
    "click_release_grace": 0.03,
    "drag_hold_threshold": 0.035,
    # Thumb+index+middle drag also needs the middle tip this close relative to the hand size: grabbing the page with
    # thumb+index keeps the middle tip right beside it (0.022-0.034, 0.2-0.28 of the hand) and turned most scroll
    # strokes into drags (2026-11-04 22:1x logs). 0 = off.
    "drag_hold_size_ratio": 0.15,
    # Scrolling with thumb+index: the grab ends as soon as the fingers have opened (index this much farther than its
    # closest during the grab) AND the hand heads back from the far end of the stroke by scroll_return_distance
    # (frame widths). The return stroke for the next scroll was read as a scroll the other way: the fingers
    # were still under the release distance (0.03-0.04 vs 0.06) while the hand came back. 0 = off.
    "scroll_return_open": 0.012,
    "scroll_return_distance": 0.015,
    # After such a return release, a new grab does not start while the hand still travels back (against the last
    # stroke) faster than this (frame widths / s), for up to scroll_return_block s: the return was re-grabbed.
    "scroll_return_speed": 0.15,
    "scroll_return_block": 1.0,
    "drag_hold_entry_window": 0.25,
    "double_click_window": 0.45,
    "double_click_move_tolerance": 0.03,
    "fling_gain_x": 1.0,
    "fling_gain_y": 1.0,
    "auto_quality_check": True,
    "auto_clean": True,
    "auto_offline_augment": False,
    "auto_retrain_after_recording": False,
    "min_quality_score": 0.7,
    "offline_augment_factor": 4,
    "offline_augment_rotation_deg": 40,
    "offline_augment_scale": 0.15,
    "gestures": {
        "fist": {
            "action": "move_cursor",
            "enabled": True,
            "description": "주먹 쥐기: 핀치 없음 이동과 같되 DPI x fist_dpi_multiplier(기본 30배). 손을 덜 오그리면 오그린 정도에 따라 1배~30배 사이로 연속 적용",
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
            "description": "엄지만 접고 나머지 네 손가락 펴서 손가락이 옆(가로)을 향하게 (0.4초 유지): 바탕화면 보기 Win+D",
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
    hand_shape: str = ""  # "open" | "half" | "fist" (cursor speed step), "" when not a pointing frame


# Hand landmark names (MediaPipe order), for the cursor reference picker
HAND_JOINT_NAMES: Tuple[str, ...] = (
    "손목",
    "엄지 뿌리", "엄지 첫마디", "엄지 둘째마디", "엄지 끝",
    "검지 뿌리", "검지 첫마디", "검지 둘째마디", "검지 끝",
    "중지 뿌리", "중지 첫마디", "중지 둘째마디", "중지 끝",
    "약지 뿌리", "약지 첫마디", "약지 둘째마디", "약지 끝",
    "새끼 뿌리", "새끼 첫마디", "새끼 둘째마디", "새끼 끝",
)
# Joints that stay put while fingers pinch / curl: a cursor anchored only on these does not
# jump when you click. Finger joints (6-8, 10-12, ...) move with every pinch.
STABLE_ANCHOR_JOINTS = frozenset({0, 1, 5, 9, 13, 17})


def normalize_anchor_joints(value: Any) -> Tuple[int, ...]:
    """Landmark indices 0..20 from a list / tuple / "0,9" string; sorted, unique. Bad input -> ()."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [v for v in value.replace(";", ",").split(",") if v.strip()]
    try:
        items = list(value)
    except TypeError:
        return ()
    out = set()
    for v in items:
        try:
            i = int(str(v).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= i <= 20:
            out.add(i)
    return tuple(sorted(out))


def describe_anchor_joints(joints: Sequence[int]) -> str:
    """Short Korean name of a joint set, for buttons and status lines."""
    j = tuple(sorted(set(joints)))
    named = {
        (): "손바닥 중심 (기본)",
        (0, 9): "손바닥 중심",
        (0,): "손목",
        (5, 9, 13, 17): "손가락 뿌리 4곳",
        (0, 5, 9, 13, 17): "손바닥 전체",
        (0, 1, 5, 9, 13, 17): "손바닥 전체 (엄지 포함)",
    }
    if j in named:
        return named[j]
    if len(j) == 1:
        return HAND_JOINT_NAMES[j[0]]
    if len(j) <= 3:
        return " + ".join(HAND_JOINT_NAMES[i] for i in j) + " 가운데"
    return f"관절 {len(j)}곳의 가운데"


# written by the calibration into a profile and read by the mouse controller, but not a setting of gestures.json:
# limited to DEFAULT_CONFIG's keys, a profile lost it on loading
PROFILE_ONLY_KEYS = {"calibrated_hand_size"}


def _profile_value_ok(key: str, value: Any, cfg: Dict[str, Any]) -> bool:
    """A profile may change a setting the app knows, to a value of the same kind (number, switch, word, list, group)."""
    if key in PROFILE_ONLY_KEYS:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if key not in cfg:
        return False
    have = cfg[key]
    if isinstance(have, bool) or isinstance(value, bool):
        return isinstance(have, bool) and isinstance(value, bool)
    if isinstance(have, (int, float)):
        return isinstance(value, (int, float))
    if have is None:
        return True
    return isinstance(value, type(have))


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

        self._profile_base = {}
        self._profile_applied = {}
        self.profile_name = None
        # Joints the user picked as the cursor reference (UI setting, survives config/profile reloads).
        # () = use the config (`cursor_anchor_points`, else the wrist -> middle MCP `cursor_anchor_ratio`).
        self.cursor_anchor_override: Tuple[int, ...] = ()
        self.config: Dict[str, Any] = self.load_config(self.config_path)
        self._index_pinched: bool = False
        self._middle_hold_pinched: bool = False
        self._last_trigger_times: Dict[str, float] = {}
        # Click guards against thumb+index pinch being misread as a click
        self._secondary_since: Dict[str, float] = {}   # finger -> time its pinch was recognized
        self._secondary_off: Dict[str, float] = {}     # finger -> time it started looking released
        self._last_side_finger: str = ""
        self._press_anchor: Optional[Tuple[float, Tuple[float, float]]] = None   # (pinch start time, hand position then)
        self._side_released_at: float = -1e9      # when a middle / ring / pinky pinch last ended
        self._held_min: Dict[str, float] = {}     # closest the finger got to the thumb during the current pinch
        self._click_fresh: Dict[str, bool] = {}  # finger opened since its last click
        self._release_clear = False
        self._close_since = None
        self._last_now = 0.0
        self.last_pinky = None  # thumb+pinky acceptance conditions of the latest frame
        self._curl_s: Optional[float] = None   # smoothed mean finger curl ratio (cursor speed by hand shape)
        self._last_halffist_at: float = -1e9    # last time the hand was a half fist or tighter (mean curl <= pinch_after_curl_below)
        self._last_curled_at: float = -1e9      # last time the fingers were curled (fist, half fist, thumbs-up pose)
        self._side_block_until: float = -1e9   # half fist with the thumb on the ring / little finger: no middle pinch either
        self._side_armed: Dict[str, bool] = {"ring": True, "pinky": True, "index": True}   # the thumb left that finger since the last half fist
        self.last_hold = None   # (hold distance, release threshold, tip distance) while thumb+index is held
        self._long_fired: Dict[str, bool] = {}   # long press reached: this pinch is now a drag
        self._index_released_at: float = -1e9
        self._scroll_released_at: float = -1e9
        self._drag_since: Optional[float] = None
        self._fist: bool = False
        self._fist_candidate_since: Optional[float] = None
        self._lost_since: Optional[float] = None
        self._index_release_since: Optional[float] = None
        self._index_pinched_since: float = -1e9
        self._grab_min_d = 9.0                    # closest thumb-index distance during this grab
        self._grab_start: Optional[Tuple[float, float]] = None
        self._grab_far: Optional[Tuple[float, float]] = None
        self._grab_far_d = 0.0
        self._return_dir: Optional[Tuple[float, float]] = None   # unit direction of the stroke a return release ended
        self._return_at = -1e9
        self._prev_palm: Optional[Tuple[float, float, float]] = None   # x, y (square units), t of the previous frame
        self._pinch_candidate_since: Optional[float] = None
        self._desktop_since: Optional[float] = None
        self._desktop_anchor: Optional[Tuple[float, float]] = None
        self.last_desktop: Optional[Dict[str, Any]] = None
        self._desktop_armed: bool = True
        # Win+D and thumb+pinky (task switcher) use similar hand shapes: each one locks the
        # other out while it is in progress and for a moment after it fires
        self._pinky_fired_at: float = -1e9
        self._desktop_fired_at: float = -1e9
        # Frame height / width, set by the caller each frame: landmark x and y are normalised
        # separately, so directions must be corrected to square pixels.
        self.frame_aspect: float = 9.0 / 16.0
        # Usual pointing direction of the open hand (radians, 0 = up in the image), learned slowly.
        self._usual_dir: Optional[float] = None
        self._usual_t: Optional[float] = None
        # Discrete 3-state hand-shape mode ("precise" | "normal" | "fast") for movement_scheme="velocity"
        self._shape_mode: str = "normal"
        self._shape_mode_cand: Optional[str] = None
        self._shape_mode_cand_since: float = -1e9
        self._shape_mode_changed_at: float = -1e9
        self._shape_mode_curl_s: Optional[float] = None

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
        self._config_unreadable: Optional[Path] = None
        if target.exists():
            try:
                # utf-8-sig: Notepad's "UTF-8 with BOM" is read too. ValueError covers a JSON typo and a file saved in
                # another encoding (cp949), which used to stop the app at startup.
                with open(target, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("not a JSON object")
                self._deep_merge(cfg, loaded)
            except (OSError, ValueError) as exc:
                # the defaults are used; the next save must not silently replace the tuned file (see save_config)
                self._config_unreadable = target
                print(f"[config] {target} could not be read ({exc}); using the built-in defaults. "
                      "The file is kept as a .bak-unreadable copy if settings are saved.")
        self._profile_base: Dict[str, Any] = {}
        self._profile_applied: Dict[str, Any] = {}
        self.profile_name: Optional[str] = None
        # the default state (built-in defaults + gestures.json), before any profile: what a profile's changes are
        # measured against when they are saved
        self._base_config: Dict[str, Any] = copy.deepcopy(cfg)
        try:
            name = profiles.get_active(target.parent)
            overlay = profiles.load_values(target.parent, name) if name else None
        except ValueError:
            name, overlay = None, None
        if name and overlay is not None:
            # defaults < gestures.json < profile: the profile holds only what differs from the default state
            self.profile_name = name
            for key, value in overlay.items():
                if not _profile_value_ok(key, value, cfg):
                    continue                 # an unknown key or a value of another kind (a hand-edited file)
                self._profile_base[key] = copy.deepcopy(cfg.get(key))
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    self._deep_merge(cfg[key], value)
                elif isinstance(cfg.get(key), int) and not isinstance(cfg.get(key), bool) and float(value).is_integer():
                    cfg[key] = int(value)
                else:
                    cfg[key] = copy.deepcopy(value)
                self._profile_applied[key] = copy.deepcopy(cfg[key])
        self.config = cfg
        try:                                   # the profile's own gesture -> command bindings go with it
            from manux.compat.hand_mouse.action_bindings import use_binding_profile
            use_binding_profile(self.profile_name)
        except Exception:
            pass
        return cfg

    def set_profile(self, name: Optional[str]) -> bool:
        """Switch to a saved profile (None / "" = none) and reload the config. False if it does not exist."""
        if name and name not in profiles.list_profiles(self.config_path.parent):
            return False
        profiles.set_active(self.config_path.parent, name)
        if profiles.RUN_OVERRIDE is not None:      # a `--profile` for this run no longer wins over the choice
            profiles.RUN_OVERRIDE = name or ""
        self.load_config(self.config_path)
        return True

    def save_config(self, path: Optional[str | Path] = None) -> Path:
        """Persist current configuration to `config/gestures.json` (or custom path).

        Values a profile supplied are written back as the file had them, unless the user changed
        them since: the profile is not copied into the shared base config."""
        target = Path(path) if path is not None else self.config_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if path is None and self.profile_name:
            # 2026-11-11 (the person's rule): with a profile on, what is changed belongs to the profile. Only what
            # differs from the default state is written, into the profile; gestures.json (the default) stays as it is.
            base = getattr(self, "_base_config", {})
            changed = {k: v for k, v in self.config.items() if k not in base or base[k] != v}
            try:
                return profiles.save_values(target.parent, self.profile_name, changed)
            except (OSError, ValueError):
                return target                # the profile is gone (renamed / deleted): never into the default file
        out = dict(self.config)
        for key, applied in self._profile_applied.items():
            if out.get(key) == applied:
                base = self._profile_base.get(key)
                if base is None:
                    out.pop(key, None)
                else:
                    out[key] = base
        if getattr(self, "_config_unreadable", None) == target and target.exists():
            # this file failed to load, so `out` is the defaults: keep what the user had before writing over it
            try:
                shutil.copy2(target, target.with_name(f"{target.name}.bak-unreadable-{time.strftime('%Y%m%d-%H%M%S')}"))
            except OSError:
                pass
            self._config_unreadable = None
        # written whole or not at all (the runtime thread and the settings window both save this file)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        os.replace(tmp, target)
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
        self._curl_s = None
        self._shape_mode_cand = None
        self._shape_mode_curl_s = None

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

    def _fist_mult(self) -> float:
        return max(0.1, float(self.config.get("fist_dpi_multiplier", 30.0)))

    def shape_dpi(self, curl: float) -> float:
        """Cursor speed multiplier for a mean finger curl ratio: `shape_dpi_min` for an open hand (curl >= shape_curl_open),
        `fist_dpi_multiplier` for a fist (curl <= shape_curl_fist), log-linear in between (a loosely curled hand moves
        at a speed between the two; `shape_dpi_curve` bends the curve)."""
        if not self.config.get("shape_dpi_enabled", True):
            return 1.0
        lo, hi = max(0.1, float(self.config.get("shape_dpi_min", 1.0))), self._fist_mult()
        c_open, c_fist = float(self.config.get("shape_curl_open", 1.45)), float(self.config.get("shape_curl_fist", 0.85))
        if c_open <= c_fist:
            return lo
        t = max(0.0, min(1.0, (c_open - curl) / (c_open - c_fist)))
        t = t ** max(0.1, float(self.config.get("shape_dpi_curve", 1.0)))
        return lo * (hi / lo) ** t

    def _update_shape_mode(self, mean_curl: float, is_fist: bool, now: float) -> str:
        """
        Discrete 3-state hand-shape mode selector ("precise" | "normal" | "fast")
        with entry/exit curl hysteresis and minimum hold time (`shape_mode_min_hold`).
        Used when `movement_scheme == "velocity"` so hand shape selects the velocity
        gain curve bounds without acting as a noisy continuous accelerator pedal.
        """
        k_mode = max(0.05, min(1.0, float(self.config.get("shape_mode_curl_smooth", 0.25))))
        self._shape_mode_curl_s = (
            mean_curl
            if self._shape_mode_curl_s is None
            else self._shape_mode_curl_s + k_mode * (mean_curl - self._shape_mode_curl_s)
        )
        c = self._shape_mode_curl_s

        precise_enter = float(self.config.get("shape_mode_precise_enter", 1.50))
        precise_exit = float(self.config.get("shape_mode_precise_exit", 1.34))
        fast_enter = float(self.config.get("shape_mode_fast_enter", 0.90))
        fast_exit = float(self.config.get("shape_mode_fast_exit", 1.08))
        min_hold = max(0.0, float(self.config.get("shape_mode_min_hold", 0.20)))

        cur = self._shape_mode
        if cur == "precise":
            if is_fist or c <= fast_enter:
                desired = "fast"
            elif c <= precise_exit:
                desired = "normal"
            else:
                desired = "precise"
        elif cur == "fast":
            if not is_fist and c >= precise_enter:
                desired = "precise"
            elif not is_fist and c >= fast_exit:
                desired = "normal"
            else:
                desired = "fast"
        else:
            if is_fist or c <= fast_enter:
                desired = "fast"
            elif c >= precise_enter:
                desired = "precise"
            else:
                desired = "normal"

        if desired == cur:
            self._shape_mode_cand = None
            return cur

        if self._shape_mode_cand != desired:
            self._shape_mode_cand = desired
            self._shape_mode_cand_since = now

        if (
            (now - self._shape_mode_cand_since) >= min_hold
            and (now - self._shape_mode_changed_at) > max(min_hold, 2.5 * min_hold)
        ):
            self._shape_mode = desired
            self._shape_mode_changed_at = now
            self._shape_mode_cand = None

        return self._shape_mode

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

    # A fist turned on its side, thumb on top (2026-11-11, user: "세로 주먹도 스크롤브라우징 가능하게"). Measured on a 15 s
    # capture of the user's own upright fist (pc-rtx3060 webcam, held 139 frames): finger_curl_ratios was 0.96-1.46 (tips about
    # as far from the wrist as the knuckles, and swinging with the depth estimate while the hand moved), so not one frame was a
    # fist. What stays put is each finger's own bend - tip->knuckle over the finger's length (straight ~1.0; index 0.56-0.74,
    # the others <= 0.62 here) - the knuckle line standing upright (pinky knuckle under the index knuckle, 0.91-0.96), and the
    # thumb tip lying on the index / middle finger (0.15-0.35 knuckle spans; a half fist 0.86 median). Replayed over the 42
    # recordings these add fist frames: half-fist moving ("none") 135 of 47,256, thumb + index 21 of 12,654, idle 96 of 15,855;
    # the upright fist itself 123 of 139. KEEP: looser, once the fist is held (the bend wobbles while the hand moves).
    UPRIGHT_FIST = {"steep": .85, "index": .75, "others": .65, "thumb": .35}
    UPRIGHT_FIST_KEEP = {"steep": .7, "index": .82, "others": .75, "thumb": .5}

    def upright_fist(self, pts: Sequence[Point3D], keep: bool = False) -> Optional[Dict[str, float]]:
        """The measures of a fist on its side when they pass (see UPRIGHT_FIST), else None."""
        asp = max(0.2, min(5.0, float(self.frame_aspect or 0.5625)))
        p = [(q.x, q.y * asp, q.z) for q in pts]
        d = lambda a, b: math.dist(p[a], p[b])   # noqa: E731
        span = d(5, 17)
        if span < 1e-6:
            return None
        bend = []
        for mcp, pip, dip, tip in ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)):
            length = d(mcp, pip) + d(pip, dip) + d(dip, tip)
            bend.append(d(tip, mcp) / length if length > 1e-6 else 1.0)
        m = {"steep": (p[17][1] - p[5][1]) / max(1e-9, math.hypot(p[17][0] - p[5][0], p[17][1] - p[5][1])),
             "index": bend[0], "others": max(bend[1:]),
             "thumb": min(d(self.THUMB_TIP, j) for j in (6, 7, 10, 11)) / span,
             "curl": max(self.finger_curl_ratios(pts))}
        lim = self.UPRIGHT_FIST_KEEP if keep else self.UPRIGHT_FIST
        ok = (m["steep"] >= lim["steep"] and m["index"] <= lim["index"] and m["others"] <= lim["others"]
              and m["thumb"] <= lim["thumb"] and m["curl"] <= 1.5)
        return m if ok else None

    def anchor_joints(self) -> Tuple[int, ...]:
        """Joints the cursor follows: the user's pick, else `cursor_anchor_points` in the config, else ()."""
        return self.cursor_anchor_override or normalize_anchor_joints(self.config.get("cursor_anchor_points"))

    def palm_anchor_point(self, pts: Sequence[Point3D]) -> Tuple[float, float]:
        """
        Cursor reference point.

        With chosen joints (`anchor_joints()`): the centre of those landmarks.
        Otherwise a point on the wrist (#0) -> palm (middle MCP #9) line:
        `cursor_anchor_ratio` 0.0 = wrist, 1.0 = middle finger base, 0.5 = palm center.
        """
        joints = self.anchor_joints()
        if joints and len(pts) > max(joints):
            n = float(len(joints))
            return (sum(pts[j].x for j in joints) / n, sum(pts[j].y for j in joints) / n)
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
        if landmarks is None or len(landmarks) < 21:
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
        self._last_now = now
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
        # palm velocity (square units / s), for telling a return stroke from a new grab
        palm_v = None
        if cursor_point_early is not None:
            asp = max(0.2, min(5.0, float(self.frame_aspect or 0.5625)))
            px, py = cursor_point_early[0], cursor_point_early[1] * asp
            if self._prev_palm is not None and 1e-3 < now - self._prev_palm[2] < 0.2:
                dt = now - self._prev_palm[2]
                palm_v = ((px - self._prev_palm[0]) / dt, (py - self._prev_palm[1]) / dt)
            self._prev_palm = (px, py, now)
        hand_size = max(self._dist_2d(pts[self.WRIST], pts[5]), self._dist_2d(pts[self.WRIST], pts[self.MIDDLE_MCP]),
                        self._dist_2d(pts[self.WRIST], pts[17]), 1.4 * self._dist_2d(pts[5], pts[17]))
        distances["hand_size"] = hand_size

        def size_cap(key: str, thr: float) -> float:
            ratio = float(self.config.get(key, 0.0))
            return min(thr, ratio * hand_size) if ratio > 0 and hand_size > 1e-6 else thr

        # 0. Fist (all four fingers curled) -> fast move. Checked first: in a fist the thumb
        # rests on the index/middle fingers and must not be read as a pinch or click.
        # But a fist can never START while a thumb+index pinch is held (incl. its release
        # grace): pinching curls the other fingers and bends the index, which looks like a
        # fist and used to cut Record & Fling recordings short.
        ratios = self.finger_curl_ratios(pts)
        curl = max(ratios)
        # Thumb stuck out + the other four folded is the entry pose of motion mode (src/motion_mode.py).
        # To this engine it looks like a fist, which would run the cursor at 20x DPI while the pose is
        # held, so the cursor stays still instead. (A natural fist keeps the thumb across the fingers:
        # 2.8 % of the logged fist frames had it stuck out.)
        if self.config.get("motion_pose_freeze", True) and curl <= float(self.config.get("motion_fold_ratio", 1.05)):
            _palm = max(1e-6, self._dist_2d(pts[self.WRIST], pts[self.MIDDLE_MCP]))
            _tuck = min(self._dist_2d(thumb, pts[13]), self._dist_2d(thumb, pts[17])) / _palm
            _tlen = self._dist_2d(thumb, pts[2]) / _palm
            _pratio = self._dist_2d(pts[5], pts[17]) / _palm      # a fist pointing at the camera: 2+ (palm foreshortened)
            _segs = self._dist_3d(pts[1], pts[2]) + self._dist_3d(pts[2], pts[3]) + self._dist_3d(pts[3], pts[4])
            _straight = self._dist_3d(pts[1], pts[4]) / _segs if _segs > 1e-6 else 0.0   # same measure as motion_mode.hand_features
            if (_straight >= float(self.config.get("motion_thumb_straight", 0.85)) and _tlen >= float(self.config.get("motion_thumb_len", 0.5))
                    and _pratio <= float(self.config.get("motion_max_palm_ratio", 1.7))):
                self._fist = False
                self._fist_candidate_since = None
                self._last_curled_at = now
                self._last_halffist_at = now
                return GestureResult(
                    active_gesture="motion_pose", action="none", cursor_point=None,
                    pinch_distance=d_index, distances=distances, is_moving=False,
                )
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
        upright = bool(self.config.get("fist_upright", True))
        if not self._fist:
            plain = index_tucked and curl <= fist_enter
            side = (not plain) and upright and self.upright_fist(pts) is not None
            if (not self._index_pinched or not index_active) and (plain or side):
                # every finger curled INTO the palm, held for fist_confirm_time
                if self._fist_candidate_since is None:
                    self._fist_candidate_since = now
                if now - self._fist_candidate_since >= float(self.config.get("fist_confirm_time", 0.12)):
                    self._fist = True
                    self._fist_candidate_since = None
                    self._pinch_candidate_since = None
            else:
                self._fist_candidate_since = None
        elif curl > float(self.config.get("fist_curl_exit", 1.2)) and not (upright and self.upright_fist(pts, keep=True)):
            self._fist = False
            self._index_released_at = now  # no click from the fingers opening
        if self._fist and self._is_enabled("fist"):
            self._last_curled_at = now
            self._last_halffist_at = now
            if self._index_pinched:
                self._index_released_at = now
            self._index_pinched = False
            self._middle_hold_pinched = False
            self._drag_since = None
            self._secondary_since.clear()
            action = str(self._gesture_cfg("fist").get("action", "move_cursor"))
            is_vel = str(self.config.get("movement_scheme", "legacy_shape")).lower() == "velocity"
            fist_shape = self._update_shape_mode(sum(ratios) / 4.0, True, now) if is_vel else "fist"
            return GestureResult(
                active_gesture="fist",
                action=action,
                cursor_point=cursor_point_early,
                pinch_distance=d_index,
                distances=distances,
                is_moving=action in MOVING_ACTIONS,
                dpi_multiplier=max(0.1, float(self.config.get("fist_dpi_multiplier", 30.0))),
                hand_shape=fist_shape,
            )

        # 0.5 Show desktop (Win+D): thumb tucked across the palm, the other four fingers
        # extended, held for desktop_hold. Fires once per hold (open/close to repeat).
        # None of the pinches look like this (they all bend a finger toward the thumb).
        palm_len = max(1e-6, self._dist_2d(pts[self.WRIST], pts[self.MIDDLE_MCP]))
        thumb_tuck = min(self._dist_2d(thumb, pts[13]), self._dist_2d(thumb, pts[17])) / palm_len
        if self._dist_2d(pts[6], pts[0]) > 1e-4:
            thumb_tuck = min(
                thumb_tuck,
                min(self._dist_2d(thumb, pts[j]) for j in (5, 6, 9, 10)) / palm_len,
            )
        distances["thumb_tuck"] = thumb_tuck
        # For the failure log (a fist that is not recognised with the back of the hand down): which way the palm faces
        # (sign of the wrist->index MCP x wrist->pinky MCP cross product, mirror/handedness dependent), where the fingers
        # point, and how the 3D curl compares with the flat 2D one.
        _w, _a, _b = pts[self.WRIST], pts[5], pts[17]
        distances["palm_cross"] = ((_a.x - _w.x) * (_b.y - _w.y) - (_a.y - _w.y) * (_b.x - _w.x)) / max(1e-9, palm_len ** 2)
        distances["point_dy"] = (pts[self.MIDDLE_MCP].y - _w.y) / palm_len
        distances["curl_mean"] = sum(ratios) / 4.0
        distances["curl_2d"] = sum(self._dist_2d(pts[t], _w) / max(1e-6, self._dist_2d(pts[m], _w)) for m, t in self.FINGER_JOINTS) / 4.0
        palm_width = self._dist_2d(pts[5], pts[17]) / palm_len
        # How far the fingers are turned sideways from the way the hand usually points:
        # |sin| of the turn of wrist -> middle fingertip (1 = turned 90 degrees, 0 = as usual / reversed).
        # In square pixels; with no usual direction yet, "usual" is up in the image.
        aspect = max(0.2, min(5.0, float(self.frame_aspect or 9.0 / 16.0)))
        _fx, _fy = pts[12].x - _w.x, (pts[12].y - _w.y) * aspect
        finger_dir = math.atan2(_fx, -_fy) if math.hypot(_fx, _fy) > 1e-6 else None
        finger_horiz = 0.0
        if finger_dir is not None:
            usual = self._usual_dir if self._usual_dir is not None else 0.0
            finger_horiz = abs(math.sin(finger_dir - usual))
        distances["finger_horiz"] = finger_horiz
        exclusive = float(self.config.get("gesture_exclusive_time", 1.0))
        # The hand shape alone (four straight fingers, thumb tucked, turned sideways, palm visible).
        # Turned sideways, the tucked thumb tip shows up next to the pinky tip in the image, which
        # used to start a thumb+pinky pinch (task switcher) first; that pinch is not started in this shape.
        ext_req = float(self.config.get("desktop_extended_ratio", 1.55))
        ratios_sorted = sorted(ratios)
        desktop_extended = min(ratios) > ext_req or (
            ext_req <= 1.6
            and ratios_sorted[0] > 1.38
            and ratios_sorted[1] > 1.48
            and (sum(ratios) / 4.0) > 1.53
        )
        desktop_shape = (
            self._is_enabled("thumb_tucked_open")
            and desktop_extended
            and thumb_tuck <= float(self.config.get("desktop_thumb_tuck", 0.6))
            and finger_horiz >= float(self.config.get("desktop_horizontal_min", 0.7))
            and palm_width >= float(self.config.get("desktop_min_palm_width", 0.45))
        )
        desktop_posture = (
            not self._index_pinched
            and desktop_shape
            and "pinky" not in self._secondary_since
            and not self._secondary_since
            and min(d_index, d_middle, d_ring, d_pinky) > float(self.config.get("desktop_min_tip_gap", 0.0))
            and (now - self._pinky_fired_at) >= exclusive
        )
        # Learn the usual direction from an ordinary open hand (thumb not tucked, fingers straight).
        if (
            finger_dir is not None
            and float(self.config.get("desktop_usual_tau", 0.0)) > 0
            and not desktop_posture
            and thumb_tuck > float(self.config.get("desktop_thumb_tuck", 0.6)) + 0.15
            and min(ratios) > 1.3
        ):
            # Starts from "up" and moves slowly, so a hand briefly held sideways is not taken as the usual one.
            usual = self._usual_dir if self._usual_dir is not None else 0.0
            dt = max(0.0, min(0.5, now - (self._usual_t if self._usual_t is not None else now)))
            k = 1.0 - math.exp(-dt / max(0.1, float(self.config.get("desktop_usual_tau", 0.0))))
            self._usual_dir = usual + k * math.atan2(math.sin(finger_dir - usual), math.cos(finger_dir - usual))
            self._usual_t = now
        # The posture is also an ordinary resting/pointing open hand, so it counts only for a hand that
        # holds still (palm centre within desktop_still_move of where the hold began) and that did not
        # just finish another gesture (a scroll, drag, click or pinch within desktop_after_activity).
        palm_now = cursor_point_early
        if desktop_posture and self._desktop_anchor is not None and palm_now is not None:
            moved = math.hypot(palm_now[0] - self._desktop_anchor[0], palm_now[1] - self._desktop_anchor[1])
            if moved > float(self.config.get("desktop_still_move", 0.03)):
                self._desktop_since = now             # the hand is travelling: the hold starts over here
                self._desktop_anchor = palm_now
        last_activity = max(self._index_released_at, self._scroll_released_at, self._pinky_fired_at,
                            max(self._last_trigger_times.values(), default=-1e9))
        after_activity = (now - last_activity) >= float(self.config.get("desktop_after_activity", 1.0))
        # A hand that is just opening from a (half) fist - the usual way out of the fast cursor shapes - ends up in
        # this posture too: not for desktop_after_curl after the fingers were last curled.
        after_curl = (now - self._last_curled_at) >= float(self.config.get("desktop_after_curl", 2.0))
        self.last_desktop = None
        if desktop_posture:
            if self._desktop_since is None:
                self._desktop_since = now
                self._desktop_anchor = palm_now
            self.last_desktop = {"tuck": round(thumb_tuck, 2), "ext_min": round(min(ratios), 2),
                                 "hold": round(now - self._desktop_since, 2), "armed": self._desktop_armed,
                                 "since_activity": round(now - last_activity, 2) if last_activity > -1e8 else None,
                                 "since_curl": round(now - self._last_curled_at, 2) if self._last_curled_at > -1e8 else None}
            if (
                self._desktop_armed
                and after_activity
                and after_curl
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
            self._desktop_anchor = None
            self._desktop_armed = True

        pinch_threshold = float(self.config.get("pinch_threshold", 0.055))
        release_threshold = float(self.config.get("release_threshold", 0.075))
        click_threshold = float(self.config.get("click_threshold", 0.04))
        pinky_thr = float(self.config.get("pinky_click_threshold", 0.05))

        # The mean curl of the four fingers, lightly smoothed, sets the cursor speed (see shape_dpi).
        mean_curl = sum(ratios) / 4.0
        k = max(0.05, min(1.0, float(self.config.get("shape_curl_smooth", 0.4))))
        self._curl_s = mean_curl if self._curl_s is None else self._curl_s + k * (mean_curl - self._curl_s)
        shape_mult = self.shape_dpi(self._curl_s)
        curled = shape_mult > 1.15
        if curled and self._curl_s <= 1.25 and max(ratios[0], ratios[1]) <= 1.35:
            self._last_curled_at = now
        moving_limit = float(self.config.get("curled_moving_speed", 0.0))
        curled_moving = (curled and moving_limit > 0 and palm_v is not None
                         and math.hypot(*palm_v) > moving_limit and not self._index_pinched and not self._secondary_since)
        # Thumb+index (scroll grab) after a half fist: the thumb lay along the curled index the whole time and the grab
        # started the moment the hand opened (2026-11-05 00:xx logs). Like the ring / little finger (side_rearm_ratio):
        # the thumb must first leave the index, with the hand open for side_pinch_after_curl.
        rearm_i = float(self.config.get("side_rearm_ratio", 0.0))
        if rearm_i <= 0:
            self._side_armed["index"] = True
        elif (curled and not self._index_pinched and palm_v is not None
              and math.hypot(*palm_v) > float(self.config.get("curled_rearm_speed", 0.15))):
            # only while MOVING the cursor with the half fist: a still hand curling the other fingers to pinch
            # thumb+index on purpose (an "OK" shape) is a real grab
            self._side_armed["index"] = False
        elif (d_index > rearm_i * hand_size
              and now - self._last_curled_at >= float(self.config.get("side_pinch_after_curl", 0.4))):
            self._side_armed["index"] = True
        # (an "OK" pinch curls the other three fingers but not the index, so it is not a half fist)
        if self._curl_s <= float(self.config.get("pinch_after_curl_below", 1.15)) and ratios[0] <= 1.35:
            self._last_halffist_at = now

        # Hysteresis for primary thumb + index pinch (#4 and #8).
        # Entering uses the tips only (strict). While held, the pinch survives camera-angle
        # dropouts: tips hidden behind each other drift, but the joints next to them
        # (thumb IP #3, index DIP #7) stay close, and short gaps are bridged by a grace time.
        if not self._index_pinched:
            self.last_hold = None
            # A tucked index (thumb resting on it in a fist) is never a pinch. With the other
            # three fingers curled (fist-like hand) the pinch must hold for
            # pinch_fistlike_confirm, so a hand closing into a fist can still become one.
            # ...and not when the thumb is clearly on the MIDDLE finger instead (a thumb+middle
            # click with the index merely nearby must not grab / scroll the page). Both tips
            # together (drag) are about equally close, so that still pinches.
            neighbor_ratio = float(self.config.get("click_neighbor_ratio", 0.85))
            d_pinky_early = min(d_pinky, self._dist_2d(thumb, pts[self.PINKY_TIP - 1]))
            middle_clearly_closer = d_middle < d_index * neighbor_ratio
            side_clearly_closer = (
                min(d_ring, d_pinky_early) < d_index * neighbor_ratio
                or (d_pinky_early <= pinky_thr and d_pinky_early <= d_index * 1.15)
            )
            can_pinch = (d_index <= size_cap("curled_pinch_size_ratio" if curled else "pinch_size_ratio", pinch_threshold)
                         and not index_tucked and not middle_clearly_closer and not side_clearly_closer and not curled_moving
                         and self._side_armed["index"])
            if can_pinch and self._return_dir is not None and palm_v is not None and (
                    now - self._return_at) < float(self.config.get("scroll_return_block", 1.0)):
                back_speed = -(palm_v[0] * self._return_dir[0] + palm_v[1] * self._return_dir[1])
                if back_speed > float(self.config.get("scroll_return_speed", 0.15)):
                    can_pinch = False            # still travelling back for the next stroke: not a new grab
            # Opening a (half) fist drags the thumb across the index for a moment: logged 2026-11-04, scrolls started
            # 0.0-0.1 s after the hand was a half fist. Right after one the touch must hold longer.
            after_halffist = (now - self._last_halffist_at) < float(self.config.get("pinch_after_curl_window", 0.5))
            if can_pinch and (others_curled or after_halffist):
                if self._pinch_candidate_since is None:
                    self._pinch_candidate_since = now
                need = float(self.config.get("pinch_fistlike_confirm", 0.12))
                if after_halffist:
                    need = max(need, float(self.config.get("pinch_after_curl_confirm", 0.3)))
                can_pinch = (now - self._pinch_candidate_since) >= need
            elif not can_pinch:
                self._pinch_candidate_since = None
            if can_pinch:
                self._index_pinched = True
                self._index_pinched_since = now
                self._grab_min_d, self._grab_start, self._grab_far, self._grab_far_d = d_index, cursor_point_early, cursor_point_early, 0.0
                self._pinch_candidate_since = None
                self._index_release_since = None
        else:
            # While the page is grabbed (scrolling) the hand moves fast and the landmarks
            # jitter: require the fingers to open clearly and for longer before it counts as
            # released (a release starts the scroll inertia).
            scrolling = scroll_on
            rel_thr = release_threshold + (float(self.config.get("scroll_release_margin", 0.008)) if scrolling else 0.0)
            # The joint shortcuts (thumb to index DIP, thumb IP to index tip) only bridge a tip
            # hidden behind the other one. They count only when VERY close; an open hand keeps
            # the thumb about 0.045 from the DIP, which held the pinch for seconds with the tips
            # 0.06 apart (logged 2026-11-04).
            joint_limit = rel_thr * float(self.config.get("pinch_hold_joint_ratio", 0.6))
            joints = [d for d in (self._dist_2d(thumb, pts[self.INDEX_DIP]),
                                  self._dist_2d(pts[self.THUMB_IP], index)) if d <= joint_limit]
            d_hold = min([d_index] + joints)
            self.last_hold = (round(d_hold, 4), round(rel_thr, 4), round(d_index, 4))  # for the failure log
            # Return stroke: fingers opened and the hand heading back from the far end of the stroke -> released now.
            returning = False
            open_gap = float(self.config.get("scroll_return_open", 0.012))
            if scrolling and open_gap > 0 and self._grab_start is not None and cursor_point_early is not None:
                aspect = max(0.2, min(5.0, float(self.frame_aspect or 0.5625)))
                p = cursor_point_early
                self._grab_min_d = min(self._grab_min_d, d_index)
                far_d = math.hypot(p[0] - self._grab_start[0], (p[1] - self._grab_start[1]) * aspect)
                if far_d > self._grab_far_d:
                    self._grab_far, self._grab_far_d = p, far_d
                back = math.hypot(p[0] - self._grab_far[0], (p[1] - self._grab_far[1]) * aspect)
                returning = (d_index > self._grab_min_d + open_gap
                             and back >= float(self.config.get("scroll_return_distance", 0.015)))
            if returning:
                sx, sy = self._grab_far[0] - self._grab_start[0], (self._grab_far[1] - self._grab_start[1]) * aspect
                n = math.hypot(sx, sy)
                self._return_dir, self._return_at = ((sx / n, sy / n) if n > 1e-6 else None), now
                self._index_pinched = False
                self._index_release_since = None
                self._index_released_at = now
                self._scroll_released_at = now
            elif d_hold > rel_thr:
                self._close_since = None
                if self._index_release_since is None:
                    self._index_release_since = now
                    self._release_clear = False
                if d_hold > rel_thr * float(self.config.get("scroll_freeze_ratio", 1.25)):
                    self._release_clear = True
                grace = max(0.0, float(self.config.get("pinch_release_grace", 0.15)))
                if scrolling:
                    grace = max(grace, float(self.config.get("scroll_release_grace", 0.14)))
                if (now - self._index_release_since) >= grace:
                    self._index_pinched = False
                    self._index_release_since = None
                    if index_active or self._middle_hold_pinched:
                        self._index_released_at = now  # click lockout only after a real grab/drag
                    if scroll_on:
                        self._scroll_released_at = now  # the cursor stays put for a moment (see "No pinch")
            else:
                # A single frame back under the limit (jitter, a bridged joint) must not restart the
                # release timer: hovering around the limit kept the pinch alive for 0.4 s+ in the logs.
                # The fingers must stay under it for scroll_rearm_time to count as pinched again.
                if self._index_release_since is not None:
                    if self._close_since is None:
                        self._close_since = now
                    if (now - self._close_since) >= float(self.config.get("pinch_rearm_time", 0.03)):
                        self._index_release_since = None
                        self._close_since = None

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
            mid_thr = drag_thr
            if float(self.config.get("drag_hold_size_ratio", 0.0)) > 0:
                mid_thr = size_cap("drag_hold_size_ratio", drag_thr)
            if self._index_pinched and in_entry_window and d_middle <= mid_thr and d_index <= drag_thr:
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
        # A long-press flag belongs to the pinch that earned it. Other paths (a thumb+index grab
        # above) drop the held pinch without clearing it, and a stale flag turned the NEXT
        # thumb+middle pinch into an instant drag.
        for finger in [f for f in self._long_fired if f not in self._secondary_since]:
            self._long_fired.pop(finger, None)

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
                # freeze the page only once the fingers are clearly opening; a distance hovering
                # around the release limit (hand jitter) must not stall the scroll
                releasing=self._index_release_since is not None and (
                    self._release_clear
                    or (self._last_now - self._index_release_since) >= float(self.config.get("scroll_freeze_delay", 0.04))
                ),
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

        pinky_thr = float(self.config.get("pinky_click_threshold", 0.05))
        ring_tuck = float(self.config.get("ring_tuck_ratio", 0.8))

        margin = float(self.config.get("click_release_margin", 0.01))
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
            # A second tap of a double-click is quick (1-3 frames, the finger opens only ~0.03 in between: logged
            # 2026-11-04): right after a click the contact needs no confirmation time and a small retreat re-arms it.
            quick = finger == "middle" and (
                now - self._last_trigger_times.get(f"thumb_{finger}_pinch", -1e9)
            ) < float(self.config.get("double_click_window", 0.45)) + 0.15
            confirm_eff = float(self.config.get("click_follow_confirm", 0.0)) if quick else confirm
            # A half-fist (all four fingers curled, the cursor-speed shape) rests the thumb 0.04-0.06 from the
            # curled middle / ring tips for seconds: logged 2026-11-04, a left click and a 1 s drag while moving the
            # cursor like that. Deliberate pinches in a curled hand touch at <= 0.03, so there the contact must be closer.
            if (
                finger != "pinky"
                and self._curl_s is not None
                and self._curl_s <= float(self.config.get("click_curled_below", 1.4))
                and max(ratios[0], ratios[1]) <= 1.35
            ):
                thr = min(thr, float(self.config.get("click_curled_threshold", 0.03)))
            if finger not in self._secondary_since:
                # Only ONE of middle / ring / pinky can be pinched at a time. While another one is held
                # (a click in progress, a long-press drag) the thumb sliding toward a neighbour with the
                # hand angle must not start a second pinch: 10 of 17 right clicks in the 2026-11-04 logs
                # fired while the middle pinch was held for seconds (thumb+middle read as thumb+ring).
                if (now - self._side_released_at) < float(self.config.get("side_pinch_gap", 0.2)) and self._last_side_finger != finger:
                    return False             # the neighbour finger takes over right after a pinch ended: not a new click
                if self.config.get("side_pinch_exclusive", True) and any(
                        other != finger and other not in self._secondary_off for other in self._secondary_since):
                    return False
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
                size_key = {"middle": "curled_click_size_ratio" if curled else "click_size_ratio",
                            "ring": "ring_click_size_ratio", "pinky": "pinky_click_size_ratio"}.get(finger, "")
                entry_dist = min(dist, dip) if (finger == "pinky" and dip <= 0.024) else dist
                eff_cap = size_cap(size_key, thr)
                if finger == "pinky":
                    eff_cap = max(eff_cap, min(thr, 0.042))
                if entry_dist <= eff_cap and clear_of_index and entry_ok and not locked:
                    self._secondary_since[finger] = now
                    self._held_min.pop(finger, None)
                    self._secondary_off.pop(finger, None)
                else:
                    if dist > thr + margin:
                        self._click_fresh[finger] = True
                    return False
            else:
                limit = thr + margin
                # A pinch held for a while is on its way to a drag (long press): the hand moves and the fingertips wobble, so it
                # may open to click_keep_limit before it counts as released (logged 2026-11-04: drags broke off at 0.04-0.05 apart,
                # and the neighbour finger then took over as a right click). A tap is not affected (it is shorter than click_keep_after).
                held_for = now - self._secondary_since[finger]
                if self._long_fired.get(finger, False) or held_for >= float(self.config.get("click_keep_after", 0.3)):
                    limit = max(limit, float(self.config.get("click_keep_limit", 0.06)))
                # The DIP shortcut bridges a tip hidden behind the thumb, so it counts only when
                # the DIP itself is close. An open hand keeps the thumb ~limit away from the DIP,
                # which held the drag after the fingers had parted (logged 2026-11-04).
                joint_ok = dip <= limit * float(self.config.get("click_hold_joint_ratio", 0.75))
                held_dist = min(dist, dip) if joint_ok else dist
                self._held_min[finger] = min(self._held_min.get(finger, held_dist), held_dist)
                # retreating from the closest point by click_rise_release counts as opening (before a long-press drag
                # begins): the fingers of a quick tap-tap do not open as far as the release limit
                rose = (finger == "middle"
                        and not self._long_fired.get(finger, False)
                        and (now - self._secondary_since[finger]) < float(self.config.get("click_rise_window", 0.35))
                        and held_dist - self._held_min[finger] >= float(self.config.get("click_rise_release", 0.012))
                        and held_dist >= float(self.config.get("click_rise_min", 0.022)))
                if held_dist > limit or not keep_ok or rose:
                    off = self._secondary_off.setdefault(finger, now)
                    grace = 0.0 if rose and held_dist <= limit else click_grace
                    if self._long_fired.get(finger, False):  # dragging: a dropout must not drop the file
                        grace = max(click_grace, float(self.config.get("pinch_release_grace", 0.15)))
                        # ...but fingers clearly apart are most likely a real release: shorter grace
                        if held_dist > limit * float(self.config.get("drag_clear_ratio", 1.3)):
                            grace = min(grace, float(self.config.get("drag_clear_grace", 0.11)))
                    if now - off >= grace:
                        self._secondary_since.pop(finger, None)
                        self._secondary_off.pop(finger, None)
                        self._click_fresh[finger] = True
                        self._long_fired.pop(finger, None)
                        self._held_min.pop(finger, None)
                        self._side_released_at = now
                        self._last_side_finger = finger
                        return False
                else:
                    self._secondary_off.pop(finger, None)
            return (now - self._secondary_since[finger]) >= confirm_eff

        def dip(tip_i: int) -> float:
            return self._dist_2d(thumb, pts[tip_i - 1])

        click_tuck = float(self.config.get("click_tuck_ratio", 0.8))
        # A closing fist rests the thumb on the curled middle finger for the frames before the fist is confirmed
        # (logged 2026-11-04: a left click with all four fingers at curl 0.75-0.85, thumb 0.005 from the middle tip).
        fist_like = max(ratios) <= float(self.config.get("click_fist_max_curl", 1.0)) and mean_curl <= 0.95
        # The rock sign (index and little finger up, middle and ring folded: the close-window sign) lays the thumb on the folded
        # middle / ring fingers: logged 2026-11-04, a right click (and a left one) every time the sign was formed.
        # A thumb resting on a deeply folded middle finger of a half fist (the other three fingers curled too); a deliberate
        # pinch folds the middle finger to ~0.9 but keeps the others up (logged 2026-11-04, 16:21-16:24).
        resting_on_middle = ratios[1] < 1.0 and (ratios[0] + ratios[2] + ratios[3]) / 3.0 <= float(
            self.config.get("click_resting_others", 1.1))
        rock_like = (ratios[0] >= 1.1 and ratios[3] >= 1.3 and ratios[1] <= 1.15 and ratios[2] <= 1.15)
        after_curl_side = (now - self._last_curled_at) < float(self.config.get("side_pinch_after_curl", 0.4)) or curled_moving
        rearm = float(self.config.get("side_rearm_ratio", 0.0))
        for f_name, f_dist in (("ring", d_ring), ("pinky", d_pinky)):
            if rearm <= 0:
                self._side_armed[f_name] = True
            elif (
                curled
                and max(ratios[0], ratios[1]) <= 1.35
                and palm_v is not None
                and math.hypot(*palm_v) > float(self.config.get("curled_rearm_speed", 0.15))
                and f_name not in self._secondary_since
            ):
                self._side_armed[f_name] = False
            elif f_dist > rearm * hand_size and not after_curl_side:
                self._side_armed[f_name] = True     # away from it with the hand open for side_pinch_after_curl
        # With the ring / little finger pinch held back, a thumb resting there in a near fist would otherwise be
        # taken as a middle pinch (left click in a fist); keep the middle back too while it rests there (+0.3 s).
        # Only near a fist: in a looser half fist, thumb+middle clicks are wanted (most left clicks in the logs).
        if (after_curl_side and self._curl_s is not None
                and self._curl_s <= float(self.config.get("pinch_after_curl_below", 1.15))
                and (d_ring <= float(self.config.get("ring_click_threshold", 0.044)) or d_pinky <= pinky_thr)):
            self._side_block_until = now + 0.3
        # V sign (control on/off, src/toggle_sign.py): the thumb holds the folded ring / little finger down,
        # which held a thumb+ring pinch (and fired right clicks) and kept the sign from counting. A right click
        # can have the same curl (index and middle straight), so the V also needs index and middle spread apart:
        # tips at least v_sign_spread x their knuckle gap (V holds in the logs: 2.3-2.5; clicks with the fingers together 1.05-1.45).
        v_spread = self._dist_2d(index, middle) / max(1e-6, self._dist_2d(pts[5], pts[self.MIDDLE_MCP]))
        v_like = (ratios[0] >= 1.3 and ratios[1] >= 1.3 and ratios[2] <= 1.2 and ratios[3] <= 1.2
                  and v_spread >= float(self.config.get("v_sign_spread", 1.8)))
        # Only the finger NEAREST the thumb (of middle / ring / pinky) may start a pinch.
        # Neighbours sit right next to each other, so "clearly closer than the neighbour"
        # rules blocked real pinches (both touching -> nothing) and letting each finger
        # decide alone fired a left AND a right click together.
        d_pinky_dip = dip(self.PINKY_TIP)
        d_pinky_contact = min(d_pinky, d_pinky_dip) if d_pinky_dip <= 0.024 else d_pinky
        nearest = min((("middle", d_middle), ("ring", d_ring), ("pinky", d_pinky_contact)), key=lambda kv: kv[1])[0]
        if d_pinky_contact <= 0.022 and d_pinky_contact <= d_ring * 1.12 and d_pinky_contact < d_middle:
            nearest = "pinky"
        # A finger curled deep into the palm (fist) with the thumb resting on it is no click
        middle_ok = confirmed("middle", d_middle, dip(self.MIDDLE_TIP), click_threshold,
                              entry_ok=ratios[1] > click_tuck and nearest == "middle" and not fist_like and not rock_like and not v_like and not resting_on_middle and now >= self._side_block_until,
                              keep_ok=ratios[1] > click_tuck * 0.5)
        # thumb+ring has its own (slightly stricter) distance: the ring tip often reads as
        # touching before it really does
        ring_thr = float(self.config.get("ring_click_threshold", 0.044))
        ring_ok = confirmed("ring", d_ring, dip(self.RING_TIP), ring_thr,
                            entry_ok=(ratios[2] > ring_tuck and nearest == "ring" and not fist_like and not rock_like and not v_like
                                      and not after_curl_side and self._side_armed["ring"]),
                            keep_ok=ratios[2] > ring_tuck * 0.5 and not v_like)
        # Thumb+pinky (task switcher): the pinky tip is hard to reach and often hidden, so it
        # gets its own (larger) threshold
        # why a thumb+pinky contact is (not) accepted: kept for the failure log
        pinky_tuck_cfg = float(self.config.get("pinky_tuck_ratio", 0.8))
        pinky_tuck_need = pinky_tuck_cfg * (0.8 if (not fist_like and max(ratios[0], ratios[1]) > 1.25) else 1.0)
        pinky_dom = float(self.config.get("pinky_ring_dominance", 0.85))
        pinky_min_gap = float(self.config.get("pinky_ring_min_gap", 0.025))
        if d_pinky_contact <= 0.022 and not fist_like and max(ratios[0], ratios[1]) > 1.35:
            pinky_dom = max(pinky_dom, 1.12)
            pinky_min_gap = min(pinky_min_gap, 0.008)
        self.last_pinky = None
        if d_pinky_contact <= pinky_thr * 1.6:
            self.last_pinky = {
                "d": round(d_pinky_contact, 4), "thr": round(pinky_thr, 4), "nearest": nearest,
                "tuck": round(ratios[3], 2), "tuck_need": round(pinky_tuck_need, 2),
                "dom": round(d_pinky_contact / max(d_ring, 1e-6), 2),
                "gap": round(self._dist_2d(pts[self.RING_TIP], pts[self.PINKY_TIP]), 3),
                "after_desktop": round(now - self._desktop_fired_at, 2) if self._desktop_fired_at > -1e8 else None,
                "locked": bool(locked), "held": "pinky" in self._secondary_since,
            }
        pinky_ok = confirmed(
            "pinky", d_pinky, d_pinky_dip, pinky_thr,
            keep_ok=ratios[3] > pinky_tuck_need * 0.85 and not v_like,
            entry_ok=(
                nearest == "pinky"
                # pinky folded deep into the palm (fist): the thumb rests on it
                and ratios[3] > pinky_tuck_need
                # a curled hand makes the ring tip read as the pinky: the pinky tip must be
                # clearly closer to the thumb than the ring tip, and the two tips must be apart
                and d_pinky_contact < d_ring * pinky_dom
                and self._dist_2d(pts[self.RING_TIP], pts[self.PINKY_TIP])
                    > pinky_min_gap
                # not right after Win+D, and not in the Win+D hand shape (straight pinky, thumb tucked)
                and (now - self._desktop_fired_at) >= exclusive
                and not desktop_shape
                and not v_like
                and not after_curl_side
                and self._side_armed["pinky"]
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
            # Drag as soon as the hand MOVES while the pinch is held (like pressing and sliding a finger on a touchscreen), without
            # waiting out long_press_time: in Paint the cursor sat still for a second after the pinch and nothing could be drawn
            # (2026-11-04). The hand position at the pinch is the reference; a tap that wobbles stays well below the distance.
            moved_far = False
            if held == "middle" and self.config.get("drag_on_move", True) and cursor_point is not None:
                if self._press_anchor is None or self._press_anchor[0] != self._secondary_since["middle"]:
                    self._press_anchor = (self._secondary_since["middle"], cursor_point)
                ref = self._press_anchor[1]
                moved = math.hypot(cursor_point[0] - ref[0], (cursor_point[1] - ref[1]) * 0.5625)
                moved_far = (moved >= float(self.config.get("drag_move_distance", 0.03))
                             and now - self._secondary_since["middle"] >= float(self.config.get("drag_move_min_time", 0.15)))
            if (
                held == "middle"
                and (not curled or self._long_fired.get("middle", False))
                and self.config.get("long_press_drag", True)
                and self._gesture_cfg("thumb_middle_pinch").get("action", "left_click") == "left_click"
                and (
                    self._long_fired.get("middle", False)
                    or moved_far
                    or now - self._secondary_since["middle"] >= float(self.config.get("long_press_time", 1.0))
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
        # The approach zone does not shrink with a small release_threshold (a profile with a tight
        # release limit let the cursor drift right before the grab: 39 px p90 in the logs).
        approach = max(release_threshold, float(self.config.get("approach_freeze_distance", 0.045)))
        if curled and not self._index_pinched and not self._secondary_since:
            # curled fingers rest near the thumb without pinching: only a thumb about to touch freezes the cursor.
            # (It used to be max(release_threshold, 0.03), which with a 0.045 release limit was no tighter at all.)
            # Same closeness a half-fist click needs (relative to the hand size), so it freezes only right before one.
            approach = size_cap("curled_click_size_ratio", float(self.config.get("shape_freeze_distance", 0.03)))
        near_pinch = min(d_index, d_middle, d_ring, d_pinky) <= approach and not curled_moving
        # Right after a scroll grab ends the hand is still moving up/down (the grab IS that motion):
        # the cursor would follow it (17 px median, 65 px p90 within 0.4 s in the logs). Hold it.
        holding = (now - self._scroll_released_at) < float(self.config.get("scroll_cursor_hold", 0.35))
        if not near_pinch and not holding and self._is_enabled("no_pinch"):
            action = str(self._gesture_cfg("no_pinch").get("action", "move_cursor"))
            is_vel = str(self.config.get("movement_scheme", "legacy_shape")).lower() == "velocity"
            if is_vel:
                shape_label = self._update_shape_mode(mean_curl, False, now)
            else:
                shape_label = "fist" if shape_mult >= 0.9 * self._fist_mult() else "half" if curled else "open"
            return GestureResult(
                active_gesture="no_pinch",
                action=action,
                cursor_point=cursor_point,
                pinch_distance=d_index,
                distances=distances,
                is_moving=action in MOVING_ACTIONS,
                dpi_multiplier=shape_mult if (curled and not self._index_pinched and not self._secondary_since) else 1.0,
                hand_shape=shape_label,
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
