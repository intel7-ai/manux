"""Settings of the pointer feature: keys, v0.3 defaults and the v0.3 key names.

Keys are `pointer.<name>` (docs/contracts.md section 11). `FrameContext.settings` hands the
feature the values with the `pointer.` prefix removed; missing keys fall back to `SETTINGS`.

Defaults are the values a fresh v0.3 install runs with: the shipped
`hand-mouse/config/gestures.json` where it sets the key (it overrides `pinch_threshold` 0.055 -> 0.025
and `release_threshold` 0.075 -> 0.045 of `gesture_engine.DEFAULT_CONFIG`), else the v0.3 code default.

The pinch thresholds below are the pointer's own copy (D-003): in v0.3 the pointer and the main
gesture engine read the same `gestures.json` keys. In v0.4 the `pinch` feature (S4) gets its own
`pinch.*` keys, so the two can be tuned apart. The S1 converter fills both from the same v0.3 key.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

PREFIX = "pointer."

CLICK_STYLES = ("pinch", "trigger", "off")
ANCHORS = ("index", "palm")

# Plain choices (v0.3 kept them in the Qt UI state, not in gestures.json)
_CHOICES: Dict[str, Any] = {
    "click_style": "pinch",      # "pinch" | "trigger" | "off"
    "anchor": "index",           # "index" (fingertip) | "palm" (palm centre)
    "tracking_only": False,      # tracking test: follow the hand, never click
    "axes": None,                # direction calibration (4 floats) or None
}

# Thresholds (v0.3 gestures.json keys, same meaning; see V03_KEYS for the renamed ones)
THRESHOLDS: Dict[str, Any] = {
    # move latch (pointer_workspace.MoveLatch)
    "move_latch": .75,
    "move_speed": .20,
    "still_speed": .08,
    # trigger click (pointer_workspace.ThumbTrigger)
    "trigger_press_ratio": .28,
    "trigger_release_ratio": .40,
    "trigger_approach_ratio": .50,
    "trigger_long_press": .55,
    "trigger_drag_move": .35,
    "trigger_rewind": .10,
    # palm anchor (gesture_engine.palm_anchor_point)
    "cursor_anchor_ratio": .5,
    # pinch click (the part of gesture_engine.GestureEngine.process the pointer used, see pinch.py)
    "pinch_threshold": .025,
    "release_threshold": .045,
    "click_threshold": .052,
    "ring_click_threshold": .044,
    "click_release_margin": .02,
    "click_release_grace": .03,
    "click_confirm_time": .06,
    "click_lockout_after_grab": .35,
    "click_neighbor_ratio": .85,
    "click_tuck_ratio": .8,
    "ring_tuck_ratio": .8,
    "click_hold_joint_ratio": .75,
    "side_pinch_exclusive": True,
    "pinch_hold_joint_ratio": .6,
    "pinch_release_grace": .15,
    "pinch_rearm_time": .03,
    "pinch_fistlike_confirm": .12,
    "index_tuck_ratio": 1.15,
    "fist_curl_enter": .95,
    "fist_curl_exit": 1.2,
    "fist_confirm_time": .12,
    "drag_hold_threshold": .035,
    "drag_hold_entry_window": .25,
    "hand_lost_reset": .3,
    # thumbs-up (motion mode entry pose) freezes the pinch detector. v0.3 condition:
    # motion_pose_freeze AND motion_enabled. The S1 converter sets this False when either is False.
    "motion_pose_freeze": True,
    "motion_fold_ratio": 1.2,
    "motion_thumb_out": .85,
    "motion_thumb_up": .15,
}

DEFAULTS: Mapping[str, Any] = MappingProxyType({**_CHOICES, **THRESHOLDS})

# The feature's settings with their full keys (architecture.md section 5)
SETTINGS: Mapping[str, Any] = MappingProxyType({PREFIX + k: v for k, v in DEFAULTS.items()})

# v0.4 short name -> v0.3 gestures.json key, where they differ (for the S1 settings converter)
V03_KEYS: Mapping[str, str] = MappingProxyType({
    "move_latch": "pointer_move_latch",
    "move_speed": "pointer_move_speed",
    "still_speed": "pointer_still_speed",
})


@dataclass
class PointerSettings:
    """Same fields as v0.3 `pointer_engine.PointerSettings`; `config` uses the v0.3 key names."""
    click_style: str = "pinch"
    anchor: str = "index"
    tracking_only: bool = False
    axes: Optional[Tuple[float, float, float, float]] = None
    config: Dict[str, Any] = field(default_factory=lambda: to_v03_config(THRESHOLDS))

    @property
    def clicks_off(self) -> bool:
        return self.tracking_only or self.click_style == "off"


def to_v03_config(values: Mapping[str, Any]) -> Dict[str, Any]:
    return {V03_KEYS.get(k, k): v for k, v in values.items() if k in THRESHOLDS}


def _clean(key: str, value: Any) -> Any:
    """A bad value falls back to the default (contracts section 11: never stop the app)."""
    default = DEFAULTS[key]
    if key == "click_style":
        return value if value in CLICK_STYLES else default
    if key == "anchor":
        return value if value in ANCHORS else default
    if key == "axes":
        try:
            axes = tuple(float(v) for v in value)
        except (TypeError, ValueError):
            return None
        return axes if len(axes) == 4 and all(map(math.isfinite, axes)) else None
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def from_mapping(values: Mapping[str, Any]) -> PointerSettings:
    """Build settings from `FrameContext.settings` (prefix already removed; a `pointer.` prefix is accepted)."""
    merged = dict(DEFAULTS)
    for key, value in values.items():
        short = key[len(PREFIX):] if key.startswith(PREFIX) else key
        if short in merged:
            merged[short] = _clean(short, value)
    return PointerSettings(
        click_style=merged["click_style"],
        anchor=merged["anchor"],
        tracking_only=merged["tracking_only"],
        axes=merged["axes"],
        config=to_v03_config(merged),
    )
