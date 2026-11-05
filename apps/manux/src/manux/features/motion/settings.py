"""Settings of the motion feature: keys, v0.3 defaults and the v0.3 key names.

Keys are `motion.<name>`; `<name>` is the v0.3 key without its `motion_` prefix (`motion_enter_hold` ->
`motion.enter_hold`). Defaults are v0.3 `motion_mode.DEFAULTS` (the shipped gestures.json sets none of them
except `motion_enabled`). v0.3 `motion_actions` (variant -> side -> command) is a binding in v0.4
(D-004) and stays `v03.motion_actions` after conversion.
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Dict, Mapping

PREFIX = "motion."

THRESHOLDS: Dict[str, Any] = {
    "enter_hold": 0.8,
    "enter_still": 0.05,
    "enter_grace": 0.2,
    "enter_quiet": 1.0,
    "timeout": 0.0,
    "hand_lost_exit": 0.0,
    "thumb_out": 0.85,
    "thumb_up": 0.15,
    "enter_path": 0.15,
    "fold_ratio": 1.2,
    "open_ratio": 1.3,
    "open_min": 1.0,
    "open_ratio_tab": 1.25,
    "open_min_tab": 0.95,
    "thumb_nav": 0.75,
    "thumb_tab": 0.45,
    "swipe_min_dx": 0.15,
    "swipe_max_ratio": 0.5,
    "swipe_window": 0.6,
    "swipe_min_time": 0.12,
    "swipe_straight": 1.5,
    "swipe_min_speed": 0.6,
    "swipe_open_frac": 0.85,
    "swipe_cooldown": 0.5,
    "reverse_pause": 0.25,
    "pause_speed": 0.15,
    "reverse_speed": 0.6,
    "return_block": 1.5,
    "recent_move": 0.02,
    "tab_frames": 3,
    "settle_speed": 0.2,
}
DEFAULTS: Mapping[str, Any] = MappingProxyType({"enabled": True, **THRESHOLDS})

# The feature's settings with their full keys (architecture.md section 5). `features` may not import
# `settings` (architecture section 4); the composition root builds `Setting`s from these defaults.
SETTINGS: Mapping[str, Any] = MappingProxyType({PREFIX + k: v for k, v in DEFAULTS.items()})

# v0.3 gestures.json key -> v0.4 key, for settings/migrate_v03.RENAMES (kept equal by a test)
V03_KEYS: Mapping[str, str] = MappingProxyType({"motion_" + k: PREFIX + k for k in DEFAULTS})


def _clean(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return int(number) if isinstance(default, int) else number


def resolve(values: Mapping[str, Any]) -> Dict[str, Any]:
    """Defaults with this frame's values on top (keys without the `motion.` prefix; a prefix is accepted)."""
    merged = dict(DEFAULTS)
    for key, value in (values or {}).items():
        short = key[len(PREFIX):] if key.startswith(PREFIX) else key
        if short in merged:
            merged[short] = _clean(short, value)
    return merged
