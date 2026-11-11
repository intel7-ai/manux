"""Custom User Gestures: user-defined gesture motions bound to arbitrary OS keyboard/mouse shortcuts.

Stored in `<config_dir>/custom_gestures.json`.
Dynamically registered into `manux.data.catalog` (GestureSpec).
External layers (actions, output) subscribe via hooks so `data` does not violate layer dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from manux.data.catalog import GestureSpec, register_custom_task, unregister_custom_task, _t, MOTION

PRESET_COMMANDS: Tuple[Dict[str, Any], ...] = (
    {"id": "ctrl_w", "name": "창/탭 닫기 (Ctrl+W)", "keys": ["ctrl", "w"], "default_name": "창/탭 닫기"},
    {"id": "f5", "name": "새로고침 (F5)", "keys": ["f5"], "default_name": "새로고침"},
    {"id": "ctrl_t", "name": "새 탭 열기 (Ctrl+T)", "keys": ["ctrl", "t"], "default_name": "새 탭 열기"},
    {"id": "ctrl_shift_t", "name": "닫은 탭 다시 열기 (Ctrl+Shift+T)", "keys": ["ctrl", "shift", "t"], "default_name": "탭 다시 열기"},
    {"id": "win_shift_s", "name": "화면 캡처 (Win+Shift+S)", "keys": ["win", "shift", "s"], "default_name": "화면 캡처"},
    {"id": "vol_up", "name": "볼륨 올리기", "keys": ["volumeup"], "default_name": "볼륨 올리기"},
    {"id": "vol_down", "name": "볼륨 내리기", "keys": ["volumedown"], "default_name": "볼륨 내리기"},
    {"id": "playpause", "name": "미디어 재생 / 일시정지", "keys": ["playpause"], "default_name": "재생/일시정지"},
    {"id": "win_d", "name": "바탕화면 보기 (Win+D)", "keys": ["win", "d"], "default_name": "바탕화면 보기"},
)

DIRECTION_CHOICES: Tuple[Dict[str, str], ...] = (
    {"id": "up", "name": "손 펴고 위로 쓸기 (Swipe Up)", "instruction": "손을 편 채 위로 한 번 쓸어 올리세요. 쉬기 때 제자리로 돌아오세요"},
    {"id": "down", "name": "손 펴고 아래로 쓸기 (Swipe Down)", "instruction": "손을 편 채 아래로 한 번 쓸어 내리세요. 쉬기 때 제자리로 돌아오세요"},
    {"id": "left", "name": "손 펴고 왼쪽 쓸기 (Swipe Left)", "instruction": "손을 편 채 왼쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요"},
    {"id": "right", "name": "손 펴고 오른쪽 쓸기 (Swipe Right)", "instruction": "손을 편 채 오른쪽으로 한 번 쓸어 멈추세요. 쉬기 때 돌아오세요"},
)

_REGISTER_HOOKS: List[Callable[[Dict[str, Any]], None]] = []
_REMOVE_HOOKS: List[Callable[[str], None]] = []


def add_custom_gesture_hook(fn: Callable[[Dict[str, Any]], None]) -> None:
    _REGISTER_HOOKS.append(fn)


def add_custom_gesture_remove_hook(fn: Callable[[str], None]) -> None:
    _REMOVE_HOOKS.append(fn)


def clear_custom_gesture_hooks() -> None:
    _REGISTER_HOOKS.clear()
    _REMOVE_HOOKS.clear()


def _file_path(config_dir: Path) -> Path:
    return Path(config_dir) / "custom_gestures.json"


def load_custom_gestures(config_dir: Path) -> List[Dict[str, Any]]:
    path = _file_path(config_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "gestures" in data:
            return list(data["gestures"])
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    return []


def save_custom_gesture(config_dir: Path, name: str, direction: str, keys: Sequence[str],
                        instruction: str = "") -> Dict[str, Any]:
    name = str(name).strip()
    if not name:
        raise ValueError("동작 이름을 입력하세요.")
    if not keys:
        raise ValueError("실행할 단축키를 하나 이상 선택하세요.")
    direction = str(direction).strip().lower()
    if direction not in ("up", "down", "left", "right"):
        direction = "up"
    if not instruction:
        dir_info = next((d for d in DIRECTION_CHOICES if d["id"] == direction), None)
        instruction = dir_info["instruction"] if dir_info else f"손을 펴고 {direction} 방향으로 움직이세요"

    # Create safe unique key
    existing = load_custom_gestures(config_dir)
    idx = len(existing) + 1
    key = f"custom_g_{idx}"
    while any(g.get("key") == key for g in existing):
        idx += 1
        key = f"custom_g_{idx}"

    action_id = f"custom.{key}"
    gesture_id = f"motion.{key}"
    clean_keys = [str(k).lower().strip() for k in keys if str(k).strip()]

    item = {
        "key": key,
        "name": name,
        "instruction": instruction,
        "direction": direction,
        "action": action_id,
        "gesture": gesture_id,
        "keys": clean_keys,
        "is_custom": True,
    }
    existing.append(item)
    path = _file_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "gestures": existing}, ensure_ascii=False, indent=2), encoding="utf-8")
    register_single_gesture(item)
    return item


def remove_custom_gesture(config_dir: Path, key: str) -> bool:
    existing = load_custom_gestures(config_dir)
    filtered = [g for g in existing if g.get("key") != key]
    if len(filtered) == len(existing):
        return False
    path = _file_path(config_dir)
    path.write_text(json.dumps({"version": 1, "gestures": filtered}, ensure_ascii=False, indent=2), encoding="utf-8")
    unregister_custom_task(key)
    for hook in _REMOVE_HOOKS:
        try:
            hook(key)
        except Exception:
            pass
    return True


def register_single_gesture(g: Dict[str, Any], tc=None) -> None:
    key = g["key"]
    name = g["name"]
    instruction = g.get("instruction", "")
    direction = g.get("direction", "up")
    gesture_id = g.get("gesture", f"motion.{key}")

    # GestureSpec in Catalog (direction must be valid in DIRECTIONS: "", "left", "right", "down")
    spec_direction = direction if direction in ("left", "right", "down") else ""
    spec = _t(key, name, instruction, MOTION, direction=spec_direction, gesture=gesture_id)
    register_custom_task(spec)

    # Training spec in TrainingCenterService if provided
    if tc is not None and hasattr(tc, "specs") and tc.specs is not None:
        tc.specs[gesture_id] = {"phases": ["tap"], "modes": ["pinch", "pointer", "motion"]}

    # External layer hooks (action spec, key chords)
    for hook in _REGISTER_HOOKS:
        try:
            hook(g)
        except Exception:
            pass


def register_all_custom_gestures(config_dir: Path, tc=None) -> None:
    for g in load_custom_gestures(config_dir):
        try:
            register_single_gesture(g, tc)
        except Exception:
            pass
