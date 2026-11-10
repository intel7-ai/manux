"""Settings of the pinch feature: keys, v0.3 defaults and the v0.3 key names.

Keys are `pinch.<name>` (docs/contracts.md section 11). `FrameContext.settings` hands the feature the
values with the `pinch.` prefix removed; missing keys fall back to the defaults below.

`<name>` is the v0.3 `gestures.json` key unchanged, so `settings/migrate_v03.RENAMES` maps
`<key> -> pinch.<key>` (see V03_KEYS). Defaults are what a fresh v0.3 install runs with: the shipped
`hand-mouse/config/gestures.json` where it sets the key (it overrides `pinch_threshold` 0.055 -> 0.025 and
`release_threshold` 0.075 -> 0.045 of `gesture_engine.DEFAULT_CONFIG`), else the v0.3 code default.

The per-gesture `enable_*` switches replace v0.3 `gestures.<id>.enabled` (a nested dict the flat
converter keeps as `v03.gestures`). What a gesture *does* is the binding (D-004), not a setting.

Not here (owned elsewhere): `hand_lost_reset` (`app.hand_lost_reset`; the engine resets the feature),
scroll gain / inertia / cursor mapping (`output/windows`), Record & Fling (not ported, off in v0.3).
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Dict, Mapping

PREFIX = "pinch."

# v0.3 key -> default (the v0.4 key is PREFIX + the same name)
THRESHOLDS: Dict[str, Any] = {
    # thumbs-up (motion mode entry pose) freezes the cursor. v0.3: motion_pose_freeze AND motion_enabled;
    # the converter keeps motion_enabled as motion.enabled, the composition root may switch this off with it.
    "motion_pose_freeze": True,
    "motion_fold_ratio": 1.2,
    "motion_thumb_out": .85,
    "motion_thumb_up": .15,
    # fist (fast move; v0.5)
    "fist_curl_enter": .95,
    "fist_curl_exit": 1.2,
    "fist_confirm_time": .12,
    "fist_dpi_multiplier": 2.0,
    "index_tuck_ratio": 1.15,
    # hand-shape speed (docs: hand-mouse/docs/hand-shape-speed.md)
    "shape_dpi_enabled": True,
    "shape_dpi_min": 1.0,
    "shape_curl_open": 1.45,
    "shape_curl_fist": .85,
    "shape_dpi_curve": 1.0,
    "shape_curl_smooth": .65,
    "shape_freeze_distance": .03,
    # show desktop (thumb tucked, four fingers open, held)
    "desktop_thumb_tuck": .45,
    "desktop_hold": .6,
    "desktop_still_move": .02,
    "desktop_after_activity": 1.0,
    "desktop_extended_ratio": 1.45,
    "desktop_min_palm_width": .45,
    "desktop_min_tip_gap": .08,
    "desktop_cooldown": 3.0,
    "gesture_exclusive_time": 1.0,
    # thumb + index (scroll grab) and thumb + index + middle (drag)
    "grab_scroll_enabled": True,
    "pinch_threshold": .025,
    "release_threshold": .045,
    "pinch_release_grace": .15,
    "pinch_hold_joint_ratio": .6,
    "pinch_rearm_time": .03,
    "pinch_fistlike_confirm": .12,
    "scroll_release_margin": .015,
    "scroll_release_grace": .14,
    "scroll_freeze_ratio": 1.25,
    "scroll_freeze_delay": .04,
    "scroll_cursor_hold": .35,
    "drag_hold_threshold": .035,
    "drag_hold_entry_window": .25,
    # thumb + middle / ring / pinky (clicks, task switcher)
    "click_threshold": .052,
    "ring_click_threshold": .044,
    "pinky_click_threshold": .05,
    "click_confirm_time": .06,
    "click_lockout_after_grab": .35,
    "click_neighbor_ratio": .85,
    "click_release_margin": .02,
    "click_release_grace": .18,
    "click_hold_joint_ratio": .75,
    "click_tuck_ratio": .8,
    "ring_tuck_ratio": .8,
    "pinky_tuck_ratio": .8,
    "pinky_ring_dominance": .85,
    "pinky_ring_min_gap": .025,
    "side_pinch_exclusive": True,
    "drag_clear_ratio": 1.6,
    "drag_clear_grace": .12,
    # long press on the left-click pinch -> drag
    "long_press_drag": True,
    "long_press_time": 0.55,
    "long_press_drag_multiplier": 1.0,
    "drag_on_move": True,
    "drag_move_distance": 0.006,
    "drag_move_min_time": 0.0,
    # cursor
    "approach_freeze_distance": .045,
    "cursor_anchor_ratio": .5,
}

# v0.3 gestures.<id>.enabled -> pinch.enable_<id>
ENABLE: Dict[str, bool] = {
    "enable_fist": True,
    "enable_no_pinch": True,
    "enable_thumb_tucked_open": True,
    "enable_thumb_middle_pinch": True,
    "enable_thumb_ring_pinch": True,
    "enable_thumb_index_middle_pinch": True,
    "enable_thumb_pinky_pinch": True,
}

DEFAULTS: Mapping[str, Any] = MappingProxyType({**THRESHOLDS, **ENABLE})

# v0.3 gestures.json key -> v0.4 key, for settings/migrate_v03.RENAMES (kept equal by a test)
# Not mapped: desktop_cooldown (v0.3 kept it in the nested `gestures` dict) and the three motion-pose keys,
# which v0.3 `motion_mode` reads too and which go to the motion feature (one v0.3 key -> one row).
SHARED_WITH_MOTION = ("motion_fold_ratio", "motion_thumb_out", "motion_thumb_up")
V03_KEYS: Mapping[str, str] = MappingProxyType({
    k: PREFIX + k for k in THRESHOLDS if k != "desktop_cooldown" and k not in SHARED_WITH_MOTION})


# The feature's settings with their full keys (architecture.md section 5). `features` may not import
# `settings` (architecture section 4); the composition root builds `Setting`s from these defaults.
SETTINGS: Mapping[str, Any] = MappingProxyType({PREFIX + k: v for k, v in DEFAULTS.items()})


def _clean(key: str, value: Any) -> Any:
    """A bad value falls back to the default (contracts section 11: never stop the app)."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def resolve(values: Mapping[str, Any]) -> Dict[str, Any]:
    """Defaults with this frame's values on top. Keys come without the `pinch.` prefix (a prefix is accepted);
    unknown keys are ignored."""
    merged = dict(DEFAULTS)
    for key, value in (values or {}).items():
        short = key[len(PREFIX):] if key.startswith(PREFIX) else key
        if short in merged:
            merged[short] = _clean(short, value)
    return merged
