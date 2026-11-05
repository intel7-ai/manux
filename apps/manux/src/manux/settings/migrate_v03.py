"""Convert v0.3 settings (hand-mouse `config/gestures.json` and its profiles) to v0.4 keys.

Only keys whose v0.4 owner exists are renamed (table below). Every other v0.3 key is kept as
`v03.<key>` so nothing is lost; a feature that is ported later adds its own rows to RENAMES
(for example `pointer_move_speed -> pointer.move_speed`). The v0.3 files are never modified.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# v0.3 key -> v0.4 key. Extend this when a feature that owns v0.3 keys is ported.
RENAMES: Dict[str, str] = {
    "hand_lost_reset": "app.hand_lost_reset",
    "pc_alias": "app.pc_alias",
    "low_light_threshold": "camera.low_light_threshold",
    "camera_note": "camera.note",
    "tracker_min_detection_confidence": "tracking.min_detection_confidence",
    "tracker_min_presence_confidence": "tracking.min_presence_confidence",
    "tracker_min_tracking_confidence": "tracking.min_tracking_confidence",
    "tracker_detect_max_width": "tracking.detect_max_width",
    # S2 pointer-only keys.
    "pointer_move_latch": "pointer.move_latch",
    "pointer_move_speed": "pointer.move_speed",
    "pointer_still_speed": "pointer.still_speed",
    "trigger_press_ratio": "pointer.trigger_press_ratio",
    "trigger_release_ratio": "pointer.trigger_release_ratio",
    "trigger_approach_ratio": "pointer.trigger_approach_ratio",
    "trigger_long_press": "pointer.trigger_long_press",
    "trigger_drag_move": "pointer.trigger_drag_move",
    "trigger_rewind": "pointer.trigger_rewind",
    # S4 pinch (features/pinch/settings.py V03_KEYS). The pinch thresholds the v0.3 pointer also read
    # (click_threshold ...) go to pinch.*: one v0.3 key -> one row, and gesture_engine owned them.
    # motion_fold_ratio / motion_thumb_out / motion_thumb_up go to motion.* (motion_mode read them too).
    "motion_pose_freeze": "pinch.motion_pose_freeze",
    "fist_curl_enter": "pinch.fist_curl_enter",
    "fist_curl_exit": "pinch.fist_curl_exit",
    "fist_confirm_time": "pinch.fist_confirm_time",
    "fist_dpi_multiplier": "pinch.fist_dpi_multiplier",
    "index_tuck_ratio": "pinch.index_tuck_ratio",
    "shape_dpi_enabled": "pinch.shape_dpi_enabled",
    "shape_dpi_min": "pinch.shape_dpi_min",
    "shape_curl_open": "pinch.shape_curl_open",
    "shape_curl_fist": "pinch.shape_curl_fist",
    "shape_dpi_curve": "pinch.shape_dpi_curve",
    "shape_curl_smooth": "pinch.shape_curl_smooth",
    "shape_freeze_distance": "pinch.shape_freeze_distance",
    "desktop_thumb_tuck": "pinch.desktop_thumb_tuck",
    "desktop_hold": "pinch.desktop_hold",
    "desktop_still_move": "pinch.desktop_still_move",
    "desktop_after_activity": "pinch.desktop_after_activity",
    "desktop_extended_ratio": "pinch.desktop_extended_ratio",
    "desktop_min_palm_width": "pinch.desktop_min_palm_width",
    "desktop_min_tip_gap": "pinch.desktop_min_tip_gap",
    "gesture_exclusive_time": "pinch.gesture_exclusive_time",
    "grab_scroll_enabled": "pinch.grab_scroll_enabled",
    "pinch_threshold": "pinch.pinch_threshold",
    "release_threshold": "pinch.release_threshold",
    "pinch_release_grace": "pinch.pinch_release_grace",
    "pinch_hold_joint_ratio": "pinch.pinch_hold_joint_ratio",
    "pinch_rearm_time": "pinch.pinch_rearm_time",
    "pinch_fistlike_confirm": "pinch.pinch_fistlike_confirm",
    "scroll_release_margin": "pinch.scroll_release_margin",
    "scroll_release_grace": "pinch.scroll_release_grace",
    "scroll_freeze_ratio": "pinch.scroll_freeze_ratio",
    "scroll_freeze_delay": "pinch.scroll_freeze_delay",
    "scroll_cursor_hold": "pinch.scroll_cursor_hold",
    "drag_hold_threshold": "pinch.drag_hold_threshold",
    "drag_hold_entry_window": "pinch.drag_hold_entry_window",
    "click_threshold": "pinch.click_threshold",
    "ring_click_threshold": "pinch.ring_click_threshold",
    "pinky_click_threshold": "pinch.pinky_click_threshold",
    "click_confirm_time": "pinch.click_confirm_time",
    "click_lockout_after_grab": "pinch.click_lockout_after_grab",
    "click_neighbor_ratio": "pinch.click_neighbor_ratio",
    "click_release_margin": "pinch.click_release_margin",
    "click_release_grace": "pinch.click_release_grace",
    "click_hold_joint_ratio": "pinch.click_hold_joint_ratio",
    "click_tuck_ratio": "pinch.click_tuck_ratio",
    "ring_tuck_ratio": "pinch.ring_tuck_ratio",
    "pinky_tuck_ratio": "pinch.pinky_tuck_ratio",
    "pinky_ring_dominance": "pinch.pinky_ring_dominance",
    "pinky_ring_min_gap": "pinch.pinky_ring_min_gap",
    "side_pinch_exclusive": "pinch.side_pinch_exclusive",
    "drag_clear_ratio": "pinch.drag_clear_ratio",
    "drag_clear_grace": "pinch.drag_clear_grace",
    "long_press_drag": "pinch.long_press_drag",
    "long_press_time": "pinch.long_press_time",
    "long_press_drag_multiplier": "pinch.long_press_drag_multiplier",
    "approach_freeze_distance": "pinch.approach_freeze_distance",
    "cursor_anchor_ratio": "pinch.cursor_anchor_ratio",
    # S4 motion (features/motion/settings.py V03_KEYS). motion_actions stays v03.* (it is a binding now).
    "motion_enabled": "motion.enabled",
    "motion_enter_hold": "motion.enter_hold",
    "motion_enter_still": "motion.enter_still",
    "motion_enter_grace": "motion.enter_grace",
    "motion_enter_quiet": "motion.enter_quiet",
    "motion_timeout": "motion.timeout",
    "motion_hand_lost_exit": "motion.hand_lost_exit",
    "motion_thumb_out": "motion.thumb_out",
    "motion_thumb_up": "motion.thumb_up",
    "motion_enter_path": "motion.enter_path",
    "motion_fold_ratio": "motion.fold_ratio",
    "motion_open_ratio": "motion.open_ratio",
    "motion_open_min": "motion.open_min",
    "motion_open_ratio_tab": "motion.open_ratio_tab",
    "motion_open_min_tab": "motion.open_min_tab",
    "motion_thumb_nav": "motion.thumb_nav",
    "motion_thumb_tab": "motion.thumb_tab",
    "motion_swipe_min_dx": "motion.swipe_min_dx",
    "motion_swipe_max_ratio": "motion.swipe_max_ratio",
    "motion_swipe_window": "motion.swipe_window",
    "motion_swipe_min_time": "motion.swipe_min_time",
    "motion_swipe_straight": "motion.swipe_straight",
    "motion_swipe_min_speed": "motion.swipe_min_speed",
    "motion_swipe_open_frac": "motion.swipe_open_frac",
    "motion_swipe_cooldown": "motion.swipe_cooldown",
    "motion_reverse_pause": "motion.reverse_pause",
    "motion_pause_speed": "motion.pause_speed",
    "motion_reverse_speed": "motion.reverse_speed",
    "motion_return_block": "motion.return_block",
    "motion_recent_move": "motion.recent_move",
    "motion_tab_frames": "motion.tab_frames",
    "motion_settle_speed": "motion.settle_speed",
}
PASSTHROUGH = "v03."


def convert(v03: Dict[str, Any]) -> Dict[str, Any]:
    """v0.3 key/value pairs -> v0.4 key/value pairs (values unchanged; Settings validates them)."""
    out: Dict[str, Any] = {}
    for key, value in v03.items():
        out[RENAMES.get(key, PASSTHROUGH + key)] = value
    return out


def read_v03(config_dir: Path) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
    """(converted gestures.json or None, {profile name: converted values}, warnings). Reads only."""
    config_dir = Path(config_dir)
    warnings: List[str] = []
    base: Optional[Dict[str, Any]] = None
    try:
        data = json.loads((config_dir / "gestures.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = convert(data)
        else:
            warnings.append("gestures.json is not an object; skipped")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        warnings.append(f"gestures.json unreadable ({exc}); skipped")
    profiles: Dict[str, Dict[str, Any]] = {}
    for path in sorted((config_dir / "profiles").glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("updates", {})
        except (OSError, ValueError, AttributeError) as exc:
            warnings.append(f"profile {path.stem} unreadable ({exc}); skipped")
            continue
        if isinstance(raw, dict):
            profiles[path.stem] = convert(raw)
    return base, profiles, warnings
