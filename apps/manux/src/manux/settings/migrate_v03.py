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
    # S2 pointer: keys only the v0.3 pointer read. Shared pinch thresholds (click_threshold ...)
    # stay v03.* until S4 decides how pointer.* and pinch.* split them (one v0.3 key -> one row).
    "pointer_move_latch": "pointer.move_latch",
    "pointer_move_speed": "pointer.move_speed",
    "pointer_still_speed": "pointer.still_speed",
    "trigger_press_ratio": "pointer.trigger_press_ratio",
    "trigger_release_ratio": "pointer.trigger_release_ratio",
    "trigger_approach_ratio": "pointer.trigger_approach_ratio",
    "trigger_long_press": "pointer.trigger_long_press",
    "trigger_drag_move": "pointer.trigger_drag_move",
    "trigger_rewind": "pointer.trigger_rewind",
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
