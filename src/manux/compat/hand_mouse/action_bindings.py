"""
Customizable Gesture & Temporal Motion -> OS Shortcut / App / Media Action Binding Engine.

Provides:
1. Visible shortcut badges for every static posture, composite gesture, and temporal motion.
2. User-customizable mappings to:
   - Built-in mouse/window actions (`mouse_action`: left_click, right_click, scroll_grab, etc.)
   - Arbitrary Windows keyboard shortcuts (`hotkey`: e.g. "ctrl+c", "win+shift+s", "alt+left")
   - YouTube / Media function keys (`youtube_key`: play_pause, seek_forward_10, fullscreen, mute, etc.)
   - Launching external programs or URLs (`launch_app`: e.g. "notepad.exe", "calc.exe", "https://www.youtube.com")
3. Foreground application-specific shortcut overrides (`app_overrides`):
   - When a target program (matched by process name or window title substring) is in the foreground,
     executes the app-specific shortcut/command instead of the global default.
4. Registry & persistence (`config/gesture_bindings.json`) for:
   - Per-gesture action bindings & app overrides
   - Composite hand gestures (base pinch/posture + directional swipe / hold / double-tap)
   - User-registered temporal (time-series) motion gestures.
"""
from __future__ import annotations

from manux.compat.hand_mouse.paths import DATA_ROOT

import copy
import json
import math
import os
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


ROOT = DATA_ROOT
DEFAULT_BINDINGS_PATH = ROOT / "config" / "gesture_bindings.json"

# ---------------------------------------------------------------------------
# Presets for UI selection & badge display
# ---------------------------------------------------------------------------
YOUTUBE_KEY_PRESETS: Dict[str, Dict[str, Any]] = {
    "yt_play_pause": {"keys": ["k"], "label": "유튜브 재생/일시정지 (K)", "badge": "YT 재생/정지 (K)"},
    "k": {"keys": ["k"], "label": "유튜브 재생/일시정지 (K)", "badge": "YT 재생/정지 (K)"},
    "yt_rewind_10": {"keys": ["j"], "label": "유튜브 10초 뒤로 (J)", "badge": "YT -10초 (J)"},
    "j": {"keys": ["j"], "label": "유튜브 10초 뒤로 (J)", "badge": "YT -10초 (J)"},
    "yt_forward_10": {"keys": ["l"], "label": "유튜브 10초 앞으로 (L)", "badge": "YT +10초 (L)"},
    "l": {"keys": ["l"], "label": "유튜브 10초 앞으로 (L)", "badge": "YT +10초 (L)"},
    "yt_rewind_5": {"keys": ["left"], "label": "유튜브 5초 뒤로 (←)", "badge": "YT -5초 (←)"},
    "yt_forward_5": {"keys": ["right"], "label": "유튜브 5초 앞으로 (→)", "badge": "YT +5초 (→)"},
    "yt_fullscreen": {"keys": ["f"], "label": "유튜브 전체화면 (F)", "badge": "YT 전체화면 (F)"},
    "f": {"keys": ["f"], "label": "유튜브 전체화면 (F)", "badge": "YT 전체화면 (F)"},
    "yt_theater": {"keys": ["t"], "label": "유튜브 영화관 모드 (T)", "badge": "YT 영화관 (T)"},
    "t": {"keys": ["t"], "label": "유튜브 영화관 모드 (T)", "badge": "YT 영화관 (T)"},
    "yt_mute": {"keys": ["m"], "label": "유튜브 음소거 (M)", "badge": "YT 음소거 (M)"},
    "m": {"keys": ["m"], "label": "유튜브 음소거 (M)", "badge": "YT 음소거 (M)"},
    "yt_captions": {"keys": ["c"], "label": "유튜브 자막 켜기/끄기 (C)", "badge": "YT 자막 (C)"},
    "c": {"keys": ["c"], "label": "유튜브 자막 켜기/끄기 (C)", "badge": "YT 자막 (C)"},
    "yt_next_video": {"keys": ["shift", "n"], "label": "유튜브 다음 영상 (Shift+N)", "badge": "YT 다음영상 (Shift+N)"},
    "yt_prev_video": {"keys": ["shift", "p"], "label": "유튜브 이전 영상 (Shift+P)", "badge": "YT 이전영상 (Shift+P)"},
    "yt_speed_up": {"keys": ["shift", "."], "label": "유튜브 배속 빠르게 (Shift+>)", "badge": "YT 배속+ (Shift+>)"},
    "yt_speed_down": {"keys": ["shift", ","], "label": "유튜브 배속 느리게 (Shift+<)", "badge": "YT 배속- (Shift+<)"},
    "yt_vol_up": {"keys": ["up"], "label": "유튜브 볼륨 크게 (↑)", "badge": "YT 볼륨+ (↑)"},
    "yt_vol_down": {"keys": ["down"], "label": "유튜브 볼륨 작게 (↓)", "badge": "YT 볼륨- (↓)"},
    "media_play_pause": {"keys": ["playpause"], "label": "시스템 미디어 재생/일시정지", "badge": "미디어 재생/정지"},
    "media_vol_up": {"keys": ["volumeup"], "label": "시스템 볼륨 올리기", "badge": "시스템 볼륨+"},
    "media_vol_down": {"keys": ["volumedown"], "label": "시스템 볼륨 내리기", "badge": "시스템 볼륨-"},
    "media_vol_mute": {"keys": ["volumemute"], "label": "시스템 음소거", "badge": "시스템 음소거"},
}

WINDOWS_HOTKEY_PRESETS: List[Dict[str, str]] = [
    {"value": "alt+left", "label": "뒤로 가기 (Alt + ←)", "badge": "Alt + ←"},
    {"value": "alt+right", "label": "앞으로 가기 (Alt + →)", "badge": "Alt + →"},
    {"value": "ctrl+shift+tab", "label": "이전 탭 (Ctrl + Shift + Tab)", "badge": "Ctrl+Shift+Tab"},
    {"value": "ctrl+tab", "label": "다음 탭 (Ctrl + Tab)", "badge": "Ctrl + Tab"},
    {"value": "ctrl+w", "label": "현재 탭 닫기 (Ctrl + W)", "badge": "Ctrl + W"},
    {"value": "ctrl+t", "label": "새 탭 열기 (Ctrl + T)", "badge": "Ctrl + T"},
    {"value": "ctrl+shift+t", "label": "닫은 탭 복구 (Ctrl + Shift + T)", "badge": "Ctrl+Shift+T"},
    {"value": "win+d", "label": "바탕화면 보기 (Win + D)", "badge": "Win + D"},
    {"value": "win+tab", "label": "작업 보기 (Win + Tab)", "badge": "Win + Tab"},
    {"value": "alt+tab", "label": "창 전환 (Alt + Tab)", "badge": "Alt + Tab"},
    {"value": "alt+f4", "label": "창 닫기 (Alt + F4)", "badge": "Alt + F4"},
    {"value": "win+shift+s", "label": "화면 캡처 (Win + Shift + S)", "badge": "Win+Shift+S"},
    {"value": "win+e", "label": "파일 탐색기 (Win + E)", "badge": "Win + E"},
    {"value": "ctrl+c", "label": "복사 (Ctrl + C)", "badge": "Ctrl + C"},
    {"value": "ctrl+v", "label": "붙여넣기 (Ctrl + V)", "badge": "Ctrl + V"},
    {"value": "ctrl+z", "label": "실행 취소 (Ctrl + Z)", "badge": "Ctrl + Z"},
    {"value": "ctrl+f", "label": "찾기 (Ctrl + F)", "badge": "Ctrl + F"},
    {"value": "ctrl+plus", "label": "화면 확대 (Ctrl + +)", "badge": "Ctrl + +"},
    {"value": "ctrl+minus", "label": "화면 축소 (Ctrl + -)", "badge": "Ctrl + -"},
    {"value": "f5", "label": "새로고침 (F5)", "badge": "F5"},
    {"value": "space", "label": "스페이스바 (Space)", "badge": "Space"},
    {"value": "enter", "label": "엔터 (Enter)", "badge": "Enter"},
    {"value": "esc", "label": "취소/닫기 (Esc)", "badge": "Esc"},
    {"value": "pageup", "label": "페이지 위로 (PageUp)", "badge": "PageUp"},
    {"value": "pagedown", "label": "페이지 아래로 (PageDown)", "badge": "PageDown"},
]

# More shortcuts (in groups) and media keys: src/gesture_presets.py
from manux.compat.hand_mouse.gesture_presets import EXTRA_YOUTUBE_KEY_PRESETS, hotkey_presets  # noqa: E402

YOUTUBE_KEY_PRESETS.update({k: v for k, v in EXTRA_YOUTUBE_KEY_PRESETS.items() if k not in YOUTUBE_KEY_PRESETS})
WINDOWS_HOTKEY_PRESETS[:] = hotkey_presets(WINDOWS_HOTKEY_PRESETS)

MOUSE_ACTION_LABELS: Dict[str, Dict[str, str]] = {
    "left_click": {"label": "마우스 좌클릭", "badge": "마우스 좌클릭"},
    "right_click": {"label": "마우스 우클릭", "badge": "마우스 우클릭"},
    "double_click": {"label": "마우스 더블클릭", "badge": "마우스 더블클릭"},
    "scroll_grab": {"label": "페이지 잡기 스크롤", "badge": "잡기 스크롤"},
    "drag_hold": {"label": "마우스 드래그 유지", "badge": "드래그 유지"},
    "task_switcher": {"label": "창 전환기 (Alt+Tab 선택)", "badge": "Alt+Tab 창선택"},
    "show_desktop": {"label": "바탕화면 보기 (Win+D)", "badge": "Win + D (바탕화면)"},
    "close_window": {"label": "활성 창 닫기 (Alt+F4)", "badge": "Alt + F4 (창닫기)"},
    "toggle_control": {"label": "마우스 제어 켜기/끄기 (Space)", "badge": "제어 ON/OFF"},
    "toggle_motion": {"label": "모션 모드 켜기/끄기", "badge": "모션모드 ON/OFF"},
    "toggle_draw": {"label": "그리기 모드 켜기/끄기", "badge": "그리기 모드 ON/OFF"},
    "back": {"label": "뒤로 가기 (Alt + ←)", "badge": "Alt + ← (뒤로)"},
    "forward": {"label": "앞으로 가기 (Alt + →)", "badge": "Alt + → (앞으로)"},
    "tab_prev": {"label": "이전 탭 이동 (Ctrl+Shift+Tab)", "badge": "Ctrl+Shift+Tab (이전탭)"},
    "tab_next": {"label": "다음 탭 이동 (Ctrl+Tab)", "badge": "Ctrl + Tab (다음탭)"},
    "move_cursor": {"label": "포인터 이동", "badge": "커서 이동"},
    "none": {"label": "동작 없음 (비활성)", "badge": "사용 안 함"},
}

DEFAULT_GESTURE_BINDINGS: Dict[str, Dict[str, Any]] = {
    "thumb_middle": {
        "gesture_key": "thumb_middle",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "left_click",
        "enabled": True,
        "app_overrides": [],
    },
    "thumb_ring": {
        "gesture_key": "thumb_ring",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "right_click",
        "enabled": True,
        "app_overrides": [],
    },
    "thumb_index": {
        "gesture_key": "thumb_index",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "scroll_grab",
        "enabled": True,
        "app_overrides": [],
    },
    "thumb_middle_drag": {
        "gesture_key": "thumb_middle_drag",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "drag_hold",
        "enabled": True,
        "app_overrides": [],
    },
    "thumb_index_middle": {
        "gesture_key": "thumb_index_middle",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "drag_hold",
        "enabled": True,
        "app_overrides": [],
    },
    "thumb_pinky": {
        "gesture_key": "thumb_pinky",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "task_switcher",
        "enabled": True,
        "app_overrides": [],
    },
    "palm_side": {
        "gesture_key": "palm_side",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "show_desktop",
        "enabled": True,
        "app_overrides": [],
    },
    "v_sign": {
        "gesture_key": "v_sign",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "toggle_control",
        "enabled": True,
        "app_overrides": [],
    },
    "rock_sign": {
        "gesture_key": "rock_sign",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "close_window",
        "enabled": True,
        "app_overrides": [],
    },
    "thumbs_up": {
        "gesture_key": "thumbs_up",
        "track": "static",
        "action_type": "mouse_action",
        "action_value": "toggle_motion",
        "enabled": True,
        "app_overrides": [],
    },
    "swipe_left": {
        "gesture_key": "swipe_left",
        "track": "motion",
        "action_type": "hotkey",
        "action_value": "alt+left",
        "enabled": True,
        "app_overrides": [
            {
                "app_match": "YouTube",
                "action_type": "youtube_key",
                "action_value": "yt_rewind_10",
            }
        ],
    },
    "swipe_right": {
        "gesture_key": "swipe_right",
        "track": "motion",
        "action_type": "hotkey",
        "action_value": "alt+right",
        "enabled": True,
        "app_overrides": [
            {
                "app_match": "YouTube",
                "action_type": "youtube_key",
                "action_value": "yt_forward_10",
            }
        ],
    },
    "swipe_tab_left": {
        "gesture_key": "swipe_tab_left",
        "track": "motion",
        "action_type": "hotkey",
        "action_value": "ctrl+shift+tab",
        "enabled": True,
        "app_overrides": [],
    },
    "swipe_tab_right": {
        "gesture_key": "swipe_tab_right",
        "track": "motion",
        "action_type": "hotkey",
        "action_value": "ctrl+tab",
        "enabled": True,
        "app_overrides": [],
    },
    "swipe_down": {
        "gesture_key": "swipe_down",
        "track": "motion",
        "action_type": "hotkey",
        "action_value": "win+down",
        "enabled": True,
        "app_overrides": [],
    },
}

# The hand on the screen (virtual hand mode, src/virtual_hand.py) makes its clicks with other hand shapes than palm mode:
# thumb + index = left click, thumb + index + middle = right click, thumb folded with four fingers up = window switcher.
# They used to borrow palm mode's bindings (thumb + middle, thumb + ring, thumb + little finger), so a command connected to
# a palm-mode pinch ran from a different hand shape here (2026-11-11: thumb + little finger -> Ctrl+W closed a tab from the
# switcher shape). Their own keys now; what they do by default is unchanged.
VIRTUAL_ACTION_KEYS: Dict[str, str] = {"left_click": "vh_pinch", "right_click": "vh_triple", "task_switcher": "vh_switch"}
PALM_ACTION_KEYS: Dict[str, str] = {"left_click": "thumb_middle", "right_click": "thumb_ring", "show_desktop": "palm_side",
                                    "task_switcher": "thumb_pinky"}
VIRTUAL_GESTURE_NAMES: Dict[str, str] = {
    "vh_pinch": "엄지+검지 집었다 떼기",
    "vh_triple": "엄지+검지+중지 함께 집기",
    "vh_switch": "엄지 접고 네 손가락 위로 (잠깐 유지)",
    "vh_pinky": "엄지+새끼 집기 (0.3초 유지)",
}
for _action, _key in VIRTUAL_ACTION_KEYS.items():
    DEFAULT_GESTURE_BINDINGS[_key] = {"gesture_key": _key, "track": "virtual", "action_type": "mouse_action",
                                      "action_value": _action, "enabled": True, "app_overrides": []}
# 2026-11-12: thumb + little finger held together, read from the hand shape (virtual_hand.ShapePinky). The two hand modes
# are separate worlds (the person's words), so it has its own key and nothing connected until the person connects one;
# palm mode's thumb + little-finger pinch ("thumb_pinky") stays palm mode's.
DEFAULT_GESTURE_BINDINGS["vh_pinky"] = {"gesture_key": "vh_pinky", "track": "virtual", "action_type": "mouse_action",
                                        "action_value": "none", "enabled": True, "app_overrides": []}
# the moves that used to be fixed (fist scroll, pointing clicks; src/default_moves.py)
from manux.compat.hand_mouse import default_moves as _default_moves  # noqa: E402
DEFAULT_GESTURE_BINDINGS.update(_default_moves.DEFAULTS)
VIRTUAL_GESTURE_NAMES["vh_fist"] = _default_moves.NAMES["vh_fist"]
# which keys belong to one hand mode only (others_with_action: the same command in the other mode never runs together)
PALM_ONLY_KEYS = frozenset(PALM_ACTION_KEYS.values()) | {"thumb_index", "thumb_middle_drag", "thumb_index_middle"}
VIRTUAL_ONLY_KEYS = frozenset(VIRTUAL_ACTION_KEYS.values()) | {"vh_fist", "vh_drag", "vh_pinky"}

# Which world a gesture lives in (2026-11-12, the person: "가상손 모드랑 손바닥 모드는 완전 별개의 세계관 … 특정 동작이
# 어디에 속하는지 명확하게 구분해서 유저가 알 수 있도록"). "virtual" / "palm": that hand mode only; "motion": only while
# motion mode is on; "all": every hand mode (hand shapes with a hold / swipe, sequences, drawings, the signs).
MODE_NAMES_KO: Dict[str, str] = {"virtual": "가상 손 모드", "palm": "손바닥 모드", "motion": "모션 모드", "all": "모든 손 모드"}


def gesture_mode(key: str, registry: Any = None) -> str:
    key = str(key or "")
    if key in VIRTUAL_ONLY_KEYS or key.startswith("vh_"):
        return "virtual"
    if key in PALM_ONLY_KEYS:
        return "palm"
    if key.startswith(("swipe_", "motion_")):
        return "motion"
    if registry is not None:
        if key in getattr(registry, "custom_motions", {}):
            return "motion"
        if (getattr(registry, "composite_gestures", {}).get(key) or {}).get("motion_only"):
            return "motion"
    return "all"


def works_in(mode: str, hand_mode: str) -> bool:
    """Whether a gesture of world `mode` answers while the hand mode `hand_mode` ("virtual" / "palm" / "index") is used."""
    return mode not in ("virtual", "palm") or mode == hand_mode

POSTURE_NAMES_KO: Dict[str, str] = {
    "open_hand": "손 펴기 (손바닥을 카메라로)",
    "open_hand_back": "손등 보이게 손 펴기",
    "open_hand_down": "손바닥을 바닥으로 손 펴기",
    "open_hand_up": "손바닥을 위로 손 펴기",
    "thumb_tucked": "엄지 접고 네 손가락 펴기",
    "thumb_index": "엄지+검지 핀치",
    "thumb_middle": "엄지+중지 핀치",
    "thumb_ring": "엄지+약지 핀치",
    "thumb_pinky": "엄지+새끼 핀치",
    "v_sign": "V 손 모양 (검지·중지를 벌려 펴기)",
    "two_fingers": "검지·중지 붙여 펴기",
    "v_sign_down": "거꾸로 V (검지·중지를 아래로)",
    "rock_sign": "검지·새끼 펴기 (락 사인)",
    "index_point": "검지만 펴기 (가리키기)",
    "l_sign": "엄지·검지 펴기 (ㄴ자)",
    "pinky_up": "새끼만 펴기",
    "shaka": "엄지·새끼 펴기 (전화 손 모양)",
    "thumbs_down": "엄지 아래로",
    "thumbs_up": "엄지 위로 세우기",
    "fist": "주먹 쥐기",
    "fingers_bent": "손가락 앞으로 굽히기 (ㄱ자)",
    "knife_hand": "손날 (손바닥을 옆으로 세우기)",
    "any": "자유 손 모양 (제한 없음)",
}

DIRECTION_NAMES_KO: Dict[str, str] = {
    "left": "왼쪽으로 쓸기 (←)",
    "right": "오른쪽으로 쓸기 (→)",
    "up": "위로 올리기 (↑)",
    "down": "아래로 내리기 (↓)",
    "diag_up_left": "좌상단 대각선 (↖)",
    "diag_up_right": "우상단 대각선 (↗)",
    "diag_down_left": "좌하단 대각선 (↙)",
    "diag_down_right": "우하단 대각선 (↘)",
}

TRIGGER_NAMES_KO: Dict[str, str] = {
    "swipe_left": "유지한 채 왼쪽 스와이프 (←)",
    "swipe_right": "유지한 채 오른쪽 스와이프 (→)",
    "swipe_up": "유지한 채 위로 스와이프 (↑)",
    "swipe_down": "유지한 채 아래로 스와이프 (↓)",
    "hold": "일정 시간 고정 유지 (Hold)",
    "double_pinch": "연속 2회 빠르게 집기 (더블 핀치)",
}


def format_hotkey_badge(hotkey_str: str) -> str:
    """Format a hotkey string like 'ctrl+shift+tab' into a clean UI badge 'Ctrl+Shift+Tab'."""
    raw = str(hotkey_str or "").strip().lower()
    if not raw:
        return "단축키 미지정"
    for preset in WINDOWS_HOTKEY_PRESETS:
        if preset["value"] == raw:
            return preset["badge"]
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    key_map = {
        "ctrl": "Ctrl",
        "control": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
        "win": "Win",
        "left": "←",
        "right": "→",
        "up": "↑",
        "down": "↓",
        "tab": "Tab",
        "space": "Space",
        "enter": "Enter",
        "esc": "Esc",
        "escape": "Esc",
        "backspace": "Backspace",
        "delete": "Del",
        "pageup": "PgUp",
        "pagedown": "PgDn",
        "plus": "+",
        "minus": "-",
    }
    return " + ".join(key_map.get(p, p.upper() if len(p) <= 3 else p.capitalize()) for p in parts)


_PROGRAM_SUFFIXES = (".exe", ".bat", ".cmd", ".lnk", ".msc", ".ps1", ".py")





def _command(value: str) -> Tuple[str, str]:
    """(program, arguments as one string) of a command line, for ShellExecute."""
    parts = shlex.split(os.path.expandvars(str(value or "")), posix=(os.name != "nt"))
    cmd = [p.strip('"') for p in parts] if parts else [str(value or "")]
    return cmd[0], subprocess.list2cmdline(cmd[1:])


def local_target(value: str) -> str:
    """The folder or document a launch value names ("" when it is a program, a command line or does not exist):
    %USERPROFILE% and ~ expanded, the whole value taken as one path (a folder name may have spaces).
    2026-11-11 report ("바탕화면에 user 폴더를 열어달라는 … 안된다"): the value was C:\\Users\\...\\Desktop, and a folder
    started as a program fails ("access denied") - folders and documents are opened the way Explorer opens them."""
    v = os.path.expandvars(os.path.expanduser(str(value or "").strip().strip('"')))
    if not v:
        return ""
    if os.path.isdir(v) or (os.path.isfile(v) and not v.lower().endswith((".exe", ".bat", ".cmd", ".com"))):
        return v
    return ""


def web_address(value: str) -> str:
    """A bare web address ("youtube.com", "www.naver.com/news") becomes https://...; programs and paths stay as they are.
    (2026-11-10: "youtube.com" was started as a program and failed.)"""
    import re
    v = str(value or "").strip()
    if (re.fullmatch(r"(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/\S*)?", v)
            and not v.lower().endswith(_PROGRAM_SUFFIXES) and "\\" not in v):
        return "https://" + v
    return v


def parse_hotkey_keys(hotkey_str: str) -> List[str]:
    """Parse 'ctrl+shift+tab' into PyAutoGUI key names ['ctrl', 'shift', 'tab']."""
    raw = str(hotkey_str or "").strip().lower()
    if not raw:
        return []
    aliases = {
        "control": "ctrl",
        "windows": "win",
        "super": "win",
        "cmd": "win",
        "escape": "esc",
        "return": "enter",
        "del": "delete",
        "pgup": "pageup",
        "pgdn": "pagedown",
        "←": "left",
        "→": "right",
        "↑": "up",
        "↓": "down",
        "+": "=",
        "plus": "=",
        "minus": "-",
    }
    keys: List[str] = []
    for part in raw.replace(" ", "").split("+"):
        if not part:
            continue
        keys.append(aliases.get(part, part))
    return keys


def format_action_badge(action_type: str, action_value: str, app_overrides: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """Return a concise, human-readable badge string describing the mapped shortcut/action."""
    atype = str(action_type or "mouse_action").strip()
    aval = str(action_value or "").strip()
    if atype == "none" or aval == "none":
        base = "사용 안 함"
    elif atype == "mouse_action":
        info = MOUSE_ACTION_LABELS.get(aval)
        base = info["badge"] if info else f"기본: {aval}"
    elif atype == "hotkey":
        base = f"단축키: {format_hotkey_badge(aval)}"
    elif atype == "system_action":
        from manux.compat.hand_mouse.pc_commands import SYSTEM_ACTIONS
        base = SYSTEM_ACTIONS.get(aval, {}).get("label", f"PC 기능: {aval}")
    elif atype == "powershell":
        base = "PowerShell: " + " ".join(aval.split())[:80]
    elif atype == "youtube_key":
        yt = YOUTUBE_KEY_PRESETS.get(aval)
        base = yt["badge"] if yt else f"YouTube: {aval}"
    elif atype == "launch_app":
        target = local_target(aval)
        inside = ""
        if target and os.path.isdir(target):
            from manux.compat.hand_mouse import app_launch
            if app_launch._is_program_folder(target):          # an installed program's folder starts that program
                try:
                    inside = app_launch._program_in(target)
                except OSError:
                    inside = ""
        if inside:
            base = f"프로그램 실행: {Path(inside).name}"
        elif target:
            base = ("폴더 열기: " if os.path.isdir(target) else "파일 열기: ") + (Path(target).name or target)
        else:
            # the program's own name, also for a quoted path with spaces ("…\\Arduino IDE.exe" was shown as "Program")
            from manux.compat.hand_mouse.app_launch import _split
            program = _split(aval)[0] if aval else ""
            base = f"프로그램 실행: {Path(program).name or aval or '미지정'}"
    elif atype == "macro":
        try:
            from manux.compat.hand_mouse.macro import describe, parse
            base = describe(parse(aval))
        except Exception:
            base = "매크로 (형식 오류)"
    else:
        base = f"{atype}: {aval}"

    overrides = [ov for ov in (app_overrides or []) if isinstance(ov, dict) and ov.get("app_match")]
    if overrides:
        first_app = str(overrides[0].get("app_match", "")).strip()
        ov_badge = format_action_badge(
            str(overrides[0].get("action_type", "hotkey")),
            str(overrides[0].get("action_value", "")),
            None,
        )
        if len(overrides) == 1:
            return f"{base}  ·  [{first_app}: {ov_badge}]"
        return f"{base}  ·  [{first_app} 외 {len(overrides) - 1}개 앱 전용]"
    return base


def format_action_detail(action_type: str, action_value: str, app_overrides: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """Return a full descriptive label for the action binding."""
    atype = str(action_type or "mouse_action").strip()
    aval = str(action_value or "").strip()
    if atype == "none" or aval == "none":
        base = "동작 없음 (비활성)"
    elif atype == "mouse_action":
        info = MOUSE_ACTION_LABELS.get(aval)
        base = info["label"] if info else f"마우스/창 동작 ({aval})"
    elif atype == "hotkey":
        matched = next((p["label"] for p in WINDOWS_HOTKEY_PRESETS if p["value"] == aval.lower()), None)
        base = matched or f"윈도우 단축키 ({format_hotkey_badge(aval)})"
    elif atype == "youtube_key":
        yt = YOUTUBE_KEY_PRESETS.get(aval)
        base = yt["label"] if yt else f"YouTube/미디어 키 ({aval})"
    elif atype == "launch_app":
        base = f"프로그램 실행 ({aval})"
    elif atype == "macro":
        try:
            from manux.compat.hand_mouse.macro import describe, parse
            base = describe(parse(aval))
        except Exception:
            base = "매크로 (형식 오류)"
    else:
        base = f"{atype}: {aval}"

    overrides = [ov for ov in (app_overrides or []) if isinstance(ov, dict) and ov.get("app_match")]
    if overrides:
        parts = []
        for ov in overrides:
            app_m = str(ov.get("app_match", "")).strip()
            ov_lbl = format_action_badge(str(ov.get("action_type", "hotkey")), str(ov.get("action_value", "")))
            parts.append(f"{app_m} 실행 중 → {ov_lbl}")
        return f"{base} (앱 조건: {', '.join(parts)})"
    return base


class ActionBindingRegistry:
    """Manages persistent gesture/motion -> OS action bindings, composite gestures, and custom temporal motions."""

    def __init__(self, path: Optional[Path | str] = None, base_path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_BINDINGS_PATH
        self.base_path = Path(base_path) if base_path is not None else None    # a profile: the default state under it
        self._base_state = None
        self._base_mtime: float = -1.0
        self.bindings: Dict[str, Dict[str, Any]] = {}
        self.composite_gestures: Dict[str, Dict[str, Any]] = {}
        self.custom_motions: Dict[str, Dict[str, Any]] = {}
        self._mtime: float = -1.0
        self.load()

    @staticmethod
    def _read(path: Optional[Path]) -> Tuple[Dict[str, Any], float]:
        """A bindings file's content and its time stamp ({} and -1 when it is missing or unreadable)."""
        if path is None or not Path(path).is_file():
            return {}, -1.0
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            return (raw if isinstance(raw, dict) else {}), Path(path).stat().st_mtime
        except Exception:
            return {}, -1.0

    @staticmethod
    def _layer(bindings: Dict[str, Any], comp: Dict[str, Any], motions: Dict[str, Any], raw: Dict[str, Any]) -> None:
        """Put one file's content on top of what is there, then make sure every motion / composite has a binding."""
        loaded_b = raw.get("bindings", {})
        if isinstance(loaded_b, dict):
            for k, v in loaded_b.items():
                if isinstance(v, dict):
                    merged = dict(bindings.get(k, {"gesture_key": k, "track": "static", "enabled": True}))
                    merged.update(v)
                    merged["gesture_key"] = k
                    merged["app_overrides"] = list(v.get("app_overrides") or [])
                    bindings[k] = merged
        loaded_c = raw.get("composite_gestures", {})
        if isinstance(loaded_c, dict):
            for k, v in loaded_c.items():
                if isinstance(v, dict):
                    comp[k] = dict(v, key=k)
        loaded_m = raw.get("custom_motions", {})
        if isinstance(loaded_m, dict):
            for k, v in loaded_m.items():
                if isinstance(v, dict):
                    motions[k] = dict(v, key=k)
        for k in raw.get("deleted") or ():          # a profile removed something the default state has
            for group in (bindings, comp, motions):
                group.pop(k, None)
        # Ensure custom motions and composite gestures also have entries in the bindings
        for m_key, m_def in motions.items():
            if m_key not in bindings:
                bindings[m_key] = {
                    "gesture_key": m_key,
                    "track": "motion",
                    "action_type": str(m_def.get("action_type", "hotkey")),
                    "action_value": str(m_def.get("action_value", "alt+right")),
                    "enabled": bool(m_def.get("enabled", True)),
                    "app_overrides": list(m_def.get("app_overrides") or []),
                }
        for c_key, c_def in comp.items():
            if c_key not in bindings:
                bindings[c_key] = {
                    "gesture_key": c_key,
                    "track": "composite",
                    "action_type": str(c_def.get("action_type", "hotkey")),
                    "action_value": str(c_def.get("action_value", "ctrl+t")),
                    "enabled": bool(c_def.get("enabled", True)),
                    "app_overrides": list(c_def.get("app_overrides") or []),
                }

    def load(self) -> None:
        """Load bindings, composite gestures, and custom motions from disk, merging with defaults.

        With `base_path` (a profile): the default state (built-in defaults + the shared file) first, then this file on
        top, which holds only what the profile changed."""
        bindings = copy.deepcopy(DEFAULT_GESTURE_BINDINGS)
        comp: Dict[str, Dict[str, Any]] = {}
        motions: Dict[str, Dict[str, Any]] = {}
        self._base_state = None
        if self.base_path is not None:
            raw, self._base_mtime = self._read(self.base_path)
            self._layer(bindings, comp, motions, raw)
            self._base_state = copy.deepcopy((bindings, comp, motions))
        raw, self._mtime = self._read(self.path)
        self._layer(bindings, comp, motions, raw)
        self.bindings = bindings
        self.composite_gestures = comp
        self.custom_motions = motions
        self._sync_catalog()

    def reload_if_changed(self) -> bool:
        try:
            changed = self.path.is_file() and self.path.stat().st_mtime != self._mtime
            if not changed and self.base_path is not None and self.base_path.is_file():
                changed = self.base_path.stat().st_mtime != self._base_mtime     # the default state was changed
            if changed:
                self.load()
                return True
        except Exception:
            pass
        return False

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._base_state is None:
            payload = {
                "version": 1,
                "bindings": self.bindings,
                "composite_gestures": self.composite_gestures,
                "custom_motions": self.custom_motions,
            }
        else:
            # a profile: only what differs from the default state, so a later change to the defaults reaches
            # everything the profile did not touch
            base_b, base_c, base_m = self._base_state
            payload = {
                "version": 1,
                "overlay": True,
                "bindings": {k: v for k, v in self.bindings.items() if base_b.get(k) != v},
                "composite_gestures": {k: v for k, v in self.composite_gestures.items() if base_c.get(k) != v},
                "custom_motions": {k: v for k, v in self.custom_motions.items() if base_m.get(k) != v},
                "deleted": sorted(k for k in set(base_b) | set(base_c) | set(base_m)
                                  if k not in self.bindings and k not in self.composite_gestures
                                  and k not in self.custom_motions),
            }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except Exception:
            pass
        self._sync_catalog()
        return self.path

    def _sync_catalog(self) -> None:
        """Register any custom temporal motions into gesture_catalog so recording & training work seamlessly."""
        try:
            from manux.compat.hand_mouse import gesture_catalog
            gesture_catalog.sync_custom_motions(self.custom_motions)
        except Exception:
            pass

    def get_binding(self, gesture_key: str) -> Dict[str, Any]:
        b = self.bindings.get(gesture_key)
        if b is None:
            b = copy.deepcopy(
                DEFAULT_GESTURE_BINDINGS.get(
                    gesture_key,
                    {
                        "gesture_key": gesture_key,
                        "track": "static",
                        "action_type": "mouse_action",
                        "action_value": "none",
                        "enabled": True,
                        "app_overrides": [],
                    },
                )
            )
        res = dict(b)
        res["badge"] = format_action_badge(res.get("action_type", "mouse_action"), res.get("action_value", ""), res.get("app_overrides"))
        res["detail"] = format_action_detail(res.get("action_type", "mouse_action"), res.get("action_value", ""), res.get("app_overrides"))
        return res

    def others_with_action(self, gesture_key: str, action_type: str, action_value: str) -> List[str]:
        """Names of the OTHER switched-on gestures that run the same command (2026-11-11: palm_side -> Win+D was
        switched off, but a composite "손 펴기 유지" bound to Win+D kept showing the desktop and the screen never
        said so)."""
        if not action_value or action_value == "none" or action_type == "none":
            return []
        try:
            from manux.compat.hand_mouse.gesture_catalog import BY_KEY
        except Exception:
            BY_KEY = {}
        out: List[str] = []
        palm, virtual = PALM_ONLY_KEYS, VIRTUAL_ONLY_KEYS
        for key, b in self.bindings.items():
            if key == gesture_key or not b.get("enabled", True):
                continue
            if (gesture_key in palm and key in virtual) or (gesture_key in virtual and key in palm):
                continue        # the same click in the other hand mode: the two never run at the same time
            if (str(b.get("action_type", "")), str(b.get("action_value", ""))) != (action_type, action_value):
                continue
            comp = self.composite_gestures.get(key)
            motion = self.custom_motions.get(key)
            if comp is not None and not comp.get("enabled", True):
                continue
            if comp is not None:
                out.append(f"{comp.get('name') or key} (복합 동작)")
            elif motion is not None:
                out.append(f"{motion.get('name') or key} (모션)")
            else:
                task = BY_KEY.get(key)
                out.append(task.name if task is not None
                           else f"{VIRTUAL_GESTURE_NAMES[key]} (가상 손)" if key in VIRTUAL_GESTURE_NAMES else key)
        return out

    def set_binding(
        self,
        gesture_key: str,
        action_type: str,
        action_value: str,
        *,
        enabled: bool = True,
        app_overrides: Optional[List[Dict[str, Any]]] = None,
        track: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = str(gesture_key or "").strip()
        if not key:
            raise ValueError("동작 키(gesture_key)가 비어 있습니다.")
        why = _default_moves.check(key, str(action_type or "").strip(), str(action_value or "").strip())
        if why:
            raise ValueError(why)
        existing = self.bindings.get(key, {})
        resolved_track = track or existing.get("track") or DEFAULT_GESTURE_BINDINGS.get(key, {}).get("track") or ("motion" if key.startswith("swipe_") or key in self.custom_motions else ("composite" if key in self.composite_gestures else "static"))
        norm_overrides: List[Dict[str, Any]] = []
        raw_overrides = app_overrides if app_overrides is not None else existing.get("app_overrides", [])
        for ov in raw_overrides or []:
            if isinstance(ov, dict) and str(ov.get("app_match", "")).strip():
                norm_overrides.append(
                    {
                        "app_match": str(ov.get("app_match", "")).strip(),
                        "action_type": str(ov.get("action_type", "hotkey")).strip(),
                        "action_value": str(ov.get("action_value", "")).strip(),
                    }
                )
        entry = {
            "gesture_key": key,
            "track": resolved_track,
            "action_type": str(action_type or "mouse_action").strip(),
            "action_value": str(action_value or "none").strip(),
            "enabled": bool(enabled),
            "app_overrides": norm_overrides,
        }
        self.bindings[key] = entry
        if key in self.custom_motions:
            self.custom_motions[key]["action_type"] = entry["action_type"]
            self.custom_motions[key]["action_value"] = entry["action_value"]
            self.custom_motions[key]["app_overrides"] = norm_overrides
            self.custom_motions[key]["enabled"] = bool(enabled)
        if key in self.composite_gestures:
            self.composite_gestures[key]["action_type"] = entry["action_type"]
            self.composite_gestures[key]["action_value"] = entry["action_value"]
            self.composite_gestures[key]["app_overrides"] = norm_overrides
            self.composite_gestures[key]["enabled"] = bool(enabled)
        self.save()
        return self.get_binding(key)

    def add_app_override(
        self,
        gesture_key: str,
        app_match: str,
        action_type: str,
        action_value: str,
    ) -> Dict[str, Any]:
        b = self.get_binding(gesture_key)
        overrides = list(b.get("app_overrides") or [])
        app_clean = str(app_match or "").strip()
        if not app_clean:
            raise ValueError("대상 프로그램 이름 또는 창 제목 키워드를 입력하세요.")
        # Replace if same app_match exists
        updated = False
        for ov in overrides:
            if str(ov.get("app_match", "")).strip().lower() == app_clean.lower():
                ov["app_match"] = app_clean
                ov["action_type"] = str(action_type or "hotkey").strip()
                ov["action_value"] = str(action_value or "").strip()
                updated = True
                break
        if not updated:
            overrides.append(
                {
                    "app_match": app_clean,
                    "action_type": str(action_type or "hotkey").strip(),
                    "action_value": str(action_value or "").strip(),
                }
            )
        return self.set_binding(
            gesture_key,
            b["action_type"],
            b["action_value"],
            enabled=b.get("enabled", True),
            app_overrides=overrides,
            track=b.get("track"),
        )

    def remove_app_override(self, gesture_key: str, app_match: str) -> Dict[str, Any]:
        b = self.get_binding(gesture_key)
        app_clean = str(app_match or "").strip().lower()
        overrides = [
            ov for ov in (b.get("app_overrides") or [])
            if str(ov.get("app_match", "")).strip().lower() != app_clean
        ]
        return self.set_binding(
            gesture_key,
            b["action_type"],
            b["action_value"],
            enabled=b.get("enabled", True),
            app_overrides=overrides,
            track=b.get("track"),
        )

    def reset_binding(self, gesture_key: str) -> Dict[str, Any]:
        base = self._base_state[0].get(gesture_key) if self._base_state is not None else None
        if base is not None:                     # a profile goes back to the default state, not the built-in value
            self.bindings[gesture_key] = copy.deepcopy(base)
            self.save()
        elif gesture_key in DEFAULT_GESTURE_BINDINGS:
            self.bindings[gesture_key] = copy.deepcopy(DEFAULT_GESTURE_BINDINGS[gesture_key])
            self.save()
        return self.get_binding(gesture_key)

    # ------------------------------------------------------------------
    # Composite Hand Gesture Registration (e.g., Pinch + Swipe / Hold)
    # ------------------------------------------------------------------
    def _new_gesture_key(self, typed: str, auto: str, prefix: str, kind: str) -> str:
        """Key for a newly registered gesture that never replaces an existing one.

        A key the user typed must be free (a built-in or an earlier registration is refused);
        an automatic key (from posture + direction) gets _2, _3, ... when it is taken."""
        def taken(k: str) -> bool:
            from manux.compat.hand_mouse.gesture_catalog import BASE_TASKS
            return (k in DEFAULT_GESTURE_BINDINGS or k in self.custom_motions or k in self.composite_gestures
                    or any(t.key == k for t in BASE_TASKS))

        typed = str(typed or "").strip().lower().replace(" ", "_")
        if typed:
            if typed in DEFAULT_GESTURE_BINDINGS or taken(typed):
                raise ValueError(f"'{typed}'는 이미 있는 동작 키입니다. 다른 키를 쓰거나 비워 두세요.")
            key = typed if typed.startswith(prefix) or (kind == "motion" and typed.startswith("swipe_")) else f"{prefix}{typed}"
            if taken(key):
                raise ValueError(f"'{key}'는 이미 있는 {('모션' if kind == 'motion' else '복합 손동작')} 키입니다.")
            return key
        key, n = auto, 2
        while taken(key):
            key, n = f"{auto}_{n}", n + 1
        return key

    def register_composite_gesture(
        self,
        key: str,
        name: str,
        base_posture: str,
        trigger_type: str,
        action_type: str,
        action_value: str,
        *,
        min_distance: float = 0.08,
        min_speed: float = 0.22,
        hold_seconds: float = 0.55,
        cooldown: float = 0.65,
        app_overrides: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        clean_key = self._new_gesture_key(key, f"comp_{base_posture}_{trigger_type}", "comp_", "composite")
        posture_ko = POSTURE_NAMES_KO.get(base_posture, base_posture)
        trig_ko = TRIGGER_NAMES_KO.get(trigger_type, trigger_type)
        display_name = str(name or "").strip() or f"{posture_ko} + {trig_ko}"
        comp_def = {
            "key": clean_key,
            "name": display_name,
            "base_posture": str(base_posture or "thumb_index").strip(),
            "trigger_type": str(trigger_type or "swipe_left").strip(),
            "min_distance": max(0.03, min(0.45, float(min_distance))),
            "min_speed": max(0.05, min(2.0, float(min_speed))),
            "hold_seconds": max(0.2, min(3.0, float(hold_seconds))),
            "cooldown": max(0.2, min(5.0, float(cooldown))),
            "action_type": str(action_type or "hotkey").strip(),
            "action_value": str(action_value or "ctrl+tab").strip(),
            "enabled": True,
            "app_overrides": list(app_overrides or []),
        }
        self.composite_gestures[clean_key] = comp_def
        self.set_binding(
            clean_key,
            comp_def["action_type"],
            comp_def["action_value"],
            enabled=True,
            app_overrides=comp_def["app_overrides"],
            track="composite",
        )
        return comp_def

    def set_composite_motion_only(self, key: str, on: bool) -> bool:
        """A composite gesture that counts only while motion mode is on (2026-11-11 request: "특정 모드(모션 모드)에서만
        작동하는 동작으로 만들 수 있는 옵션"). Off = as before: in the ordinary hand controls, not in motion mode."""
        comp = self.composite_gestures.get(str(key or "").strip())
        if comp is None:
            return False
        if on:
            comp["motion_only"] = True
        else:
            comp.pop("motion_only", None)
        self.save()
        return True

    def delete_composite_gesture(self, key: str) -> bool:
        clean_key = str(key or "").strip()
        removed = False
        if clean_key in self.composite_gestures:
            del self.composite_gestures[clean_key]
            removed = True
        if clean_key in self.bindings and self.bindings[clean_key].get("track") == "composite":
            del self.bindings[clean_key]
            removed = True
        if removed:
            self.save()
        return removed

    # ------------------------------------------------------------------
    # Temporal (Time-Series) Motion Gesture Registration
    # ------------------------------------------------------------------
    def register_motion_gesture(
        self,
        key: str,
        name: str,
        posture: str,
        direction: str,
        action_type: str,
        action_value: str,
        *,
        instruction: str = "",
        min_distance: float = 0.14,
        min_speed: float = 0.25,
        window_seconds: float = 0.65,
        require_motion_mode: bool = True,
        app_overrides: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        clean_key = self._new_gesture_key(key, f"motion_{posture}_{direction}", "motion_", "motion")
        posture_ko = POSTURE_NAMES_KO.get(posture, posture)
        dir_ko = DIRECTION_NAMES_KO.get(direction, direction)
        display_name = str(name or "").strip() or f"{posture_ko} · {dir_ko}"
        if not instruction.strip():
            instruction = f"[시계열 모션] {posture_ko} 상태로 {dir_ko} 한 번 쓸어 멈추세요. 쉬기 때 제자리로 돌아오세요"
        motion_def = {
            "key": clean_key,
            "name": display_name,
            "posture": str(posture or "open_hand").strip(),
            "direction": str(direction or "right").strip(),
            "instruction": instruction.strip(),
            "min_distance": max(0.05, min(0.50, float(min_distance))),
            "min_speed": max(0.08, min(2.0, float(min_speed))),
            "window_seconds": max(0.25, min(1.8, float(window_seconds))),
            "require_motion_mode": bool(require_motion_mode),
            "action_type": str(action_type or "hotkey").strip(),
            "action_value": str(action_value or "alt+right").strip(),
            "enabled": True,
            "app_overrides": list(app_overrides or []),
            "track": "motion",
        }
        self.custom_motions[clean_key] = motion_def
        self.set_binding(
            clean_key,
            motion_def["action_type"],
            motion_def["action_value"],
            enabled=True,
            app_overrides=motion_def["app_overrides"],
            track="motion",
        )
        return motion_def

    def delete_motion_gesture(self, key: str) -> bool:
        clean_key = str(key or "").strip()
        if clean_key in DEFAULT_GESTURE_BINDINGS:
            return False  # Built-in motion gestures cannot be deleted, only rebound/disabled
        removed = False
        if clean_key in self.custom_motions:
            del self.custom_motions[clean_key]
            removed = True
        if clean_key in self.bindings:
            del self.bindings[clean_key]
            removed = True
        if removed:
            self.save()
        return removed

    # ------------------------------------------------------------------
    # Foreground App Resolution & Action Execution
    # ------------------------------------------------------------------
    def resolve_effective_action(
        self,
        gesture_key: str,
        foreground: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolve which action should fire for `gesture_key` given the current foreground window."""
        b = self.get_binding(gesture_key)
        if not b.get("enabled", True):
            return {
                "gesture_key": gesture_key,
                "action_type": "none",
                "action_value": "none",
                "matched_app": "",
                "badge": "사용 안 함",
            }
        fg = foreground or {}
        fg_title = str(fg.get("title_full") or fg.get("title") or "").lower()
        fg_proc = str(fg.get("process") or "").lower()
        fg_cls = str(fg.get("cls") or "").lower()
        haystack = f"{fg_proc} {fg_title} {fg_cls}"

        for ov in b.get("app_overrides") or []:
            if not isinstance(ov, dict):
                continue
            app_match = str(ov.get("app_match", "")).strip()
            if not app_match:
                continue
            tokens = [t.strip().lower() for t in app_match.split("|") if t.strip()]
            if any(tok in haystack for tok in tokens):
                atype = str(ov.get("action_type", "hotkey")).strip()
                aval = str(ov.get("action_value", "")).strip()
                return {
                    "gesture_key": gesture_key,
                    "action_type": atype,
                    "action_value": aval,
                    "matched_app": app_match,
                    "badge": f"[{app_match}] {format_action_badge(atype, aval)}",
                }

        atype = str(b.get("action_type", "mouse_action")).strip()
        aval = str(b.get("action_value", "none")).strip()
        return {
            "gesture_key": gesture_key,
            "action_type": atype,
            "action_value": aval,
            "matched_app": "",
            "badge": format_action_badge(atype, aval),
        }

    def execute_resolved_action(self, resolved, *, dry_run=False, mouse_controller=None):
        from manux.compat.hand_mouse import ports
        return ports.execute(self, resolved, dry_run=dry_run, mouse_controller=mouse_controller)



class CompositeGestureDetector:
    """Detects registered composite gestures (Base Pinch/Posture + Swipe / Hold / Double-Pinch)."""

    BLANK_GRACE = .15    # s of "no shape" frames that do not end the shape being held
    # 2026-11-11 report ("youtube 페이지를 여러 개 한꺼번에 아주 늦게 여는 문제", "옆따봉을 했는데 거꾸로 V 명령이 나오거나"):
    # a hold fired once per unbroken run of its shape, and the shape of a hand held up for seconds flickers (another
    # shape or none for a few frames) - every flicker was a new run, so the command ran again and again while the user
    # waited for the browser. Now a hold that fired is spent until its shape has been gone for REARM seconds, and no
    # OTHER hold fires within OTHER_HOLD seconds of one (the hand is still in, or leaving, the first shape).
    REARM = 1.0
    OTHER_HOLD = 1.5
    TRACE = 90           # frames kept for the log of a fire (logs/composite_fires, written by the runtime)
    # 2026-11-11 report ("손 펴기 유지가 작동도 잘 안 되는데 … 손 위치가 이동하면 안 되는 거임??"), measured on the person's
    # use log (739 commands, 106,333 frames, 551 stretches of an open hand):
    #  - 122 stretches were cut by a few frames read as the knife hand (손날), 19 by a taught shape, 14 by a V: another
    #    shape for a moment ended the hold at once. Now another shape must last SHAPE_GRACE to end the one being held
    #    (it then counts from its first frame, so it is not recognised later).
    #  - of the 118 stretches that lasted the second, 22 (19 %) did not run because the palm was more than STILL from where
    #    it was when the hand OPENED - also when the hand had stopped since; only 6 of them ran later (the hand came back).
    #    Now the hold also runs when the palm has been still for the whole hold time, wherever that is (STILL_ROLLING).
    # The hold still needs a still hand: an open hand is also the hand that moves the cursor.
    SHAPE_GRACE = .15    # s another shape may show without ending the shape held for a "hold"
    STILL = .06          # frame fractions the palm may be from the anchor (or move within the hold time)
    STILL_ROLLING = True

    def __init__(self, registry: ActionBindingRegistry) -> None:
        from collections import deque
        self.registry = registry
        self._hold_spent: Dict[str, float] = {}      # hold that fired -> when its shape was last seen
        self._last_hold_fire: Tuple[str, float] = ("", -1e9)
        self.trace = deque(maxlen=self.TRACE)        # (t, posture, engine gesture, joints) of the last frames
        self._blank_since: Optional[float] = None
        self._active_posture: str = ""
        self._posture_since: float = 0.0
        self._anchor_xy: Optional[Tuple[float, float]] = None
        self._history: List[Tuple[float, float, float]] = []
        self._hold_fired_for: set = set()
        self._last_tap_time: Dict[str, float] = {}
        self._last_trigger_time: Dict[str, float] = {}
        self._other: Tuple[str, float] = ("", 0.0)   # another shape showing while one is held: (shape, since)
        self._path: List[Tuple[float, float, float]] = []   # the palm while the shape is held (for "still for the hold time")

    def reset(self) -> None:
        self._active_posture = ""
        self._posture_since = 0.0
        self._anchor_xy = None
        self._history.clear()
        self._hold_fired_for.clear()
        self._other = ("", 0.0)
        self._path.clear()

    @staticmethod
    def _detect_base_posture(landmarks: Sequence[Any], active_gesture: str, pinched_fingers: set) -> str:
        """The rules' posture, replaced by a shape the user taught when that model is sure (src/posture_teach.py)."""
        rule = CompositeGestureDetector._detect_base_posture_rules(landmarks, active_gesture, pinched_fingers)
        from manux.compat.hand_mouse.pose_probe import faced
        from manux.compat.hand_mouse.posture_teach import override
        return faced(override(landmarks, rule), landmarks, .5625)     # an open hand showing its back: its own shape

    @staticmethod
    def _detect_base_posture_rules(landmarks: Sequence[Any], active_gesture: str, pinched_fingers: set) -> str:
        """Identify the current base hand posture for composite matching."""
        # V first: the thumb holds the ring finger in a V, which the rule engine can report as a thumb + ring pinch
        if landmarks and len(landmarks) >= 21:
            from manux.compat.hand_mouse.pose_probe import _PATTERN_FINGER, pattern_posture, v_shape
            v = v_shape(landmarks, .5625)             # V, upside-down V, or index + middle held together
            if v:
                return v
            # which fingers are out (2026-11-11): the pointing hand rests the thumb on the folded middle finger, which
            # the rule engine reports as a thumb + middle pinch. Not when the engine has the out finger itself pinched
            shape = pattern_posture(landmarks, .5625)
            finger = _PATTERN_FINGER.get(shape)
            if shape and not (finger and (finger in pinched_fingers or active_gesture == f"thumb_{finger}_pinch")):
                return shape
        if "index" in pinched_fingers and "middle" in pinched_fingers:
            return "thumb_index_middle"
        if "index" in pinched_fingers or active_gesture == "thumb_index_pinch":
            return "thumb_index"
        if "middle" in pinched_fingers or active_gesture == "thumb_middle_pinch":
            return "thumb_middle"
        if "ring" in pinched_fingers or active_gesture == "thumb_ring_pinch":
            return "thumb_ring"
        if "pinky" in pinched_fingers or active_gesture == "thumb_pinky_pinch":
            return "thumb_pinky"
        if active_gesture == "thumb_tucked_open":
            return "thumb_tucked"
        if landmarks and len(landmarks) >= 21:
            # thumbs-up before fist: the engine reports a thumbs-up hand as a fist
            from manux.compat.hand_mouse.motion_mode import custom_posture_ok, hand_features
            try:
                if custom_posture_ok("thumbs_up", hand_features(landmarks)):
                    return "thumbs_up"
            except Exception:
                pass
        if active_gesture == "fist":
            from manux.compat.hand_mouse.pose_probe import fist_or_bent
            return fist_or_bent(landmarks, aspect=.5625) or "fist"
        if not landmarks or len(landmarks) < 21:
            return ""
        # Check V-sign or open hand from finger extensions
        try:
            w = landmarks[0]
            mcps = [landmarks[i] for i in (5, 9, 13, 17)]
            tips = [landmarks[i] for i in (8, 12, 16, 20)]
            ratios = []
            for m, t in zip(mcps, tips):
                dm = math.hypot(m.x - w.x, m.y - w.y)
                dt = math.hypot(t.x - w.x, t.y - w.y)
                ratios.append(dt / max(1e-5, dm))
            from manux.compat.hand_mouse.pose_probe import v_shape
            v = v_shape(landmarks, .5625)
            if v:
                return v
            if ratios[0] >= 1.55 and ratios[3] >= 1.45 and ratios[1] <= 1.25 and ratios[2] <= 1.25:
                return "rock_sign"
            if min(ratios) >= 1.55:
                from manux.compat.hand_mouse.pose_probe import KNIFE, fingers_straight, palm_sideways
                if palm_sideways(landmarks, .5625) >= KNIFE:
                    return "knife_hand"
                return "open_hand" if fingers_straight(landmarks, .5625) else ""   # both joints of every finger straight
        except Exception:
            pass
        return ""

    # An open hand that is already being held stays one while every finger is still at least OPEN_KEEP straight (it must be
    # 1.55 to start). Measured on the person's use log, same report: of the 3,757 "no shape" frames right after an open
    # hand, half had their least straight finger between 1.35 and 1.55 (most often the index, relaxing), and those frames
    # ended 200 of 551 open-hand stretches before the second was up. And it stays one when the hand turns its edge to the
    # camera (read as the knife hand, 손날: 91 more stretches) - unless a gesture is bound to the knife hand itself.
    OPEN_KEEP = 1.35

    def _open_goes_on(self, landmarks: Sequence[Any], posture: str, composites: List[Dict[str, Any]]) -> bool:
        if self.OPEN_KEEP <= 0:
            return False
        try:
            from manux.compat.hand_mouse.pose_probe import fingers_straight
            if not fingers_straight(landmarks, .5625, keep=True):
                return False                           # the fingers bent at their joints: the open hand has ended
        except Exception:
            return False
        if posture == "knife_hand":
            return not any(c.get("base_posture") == "knife_hand" for c in composites)
        if posture != "":
            return False                               # a pinch, a fist, a V ...: the hand changed
        try:
            from manux.compat.hand_mouse.pose_probe import _finger_ratios
            return min(_finger_ratios(landmarks)) >= self.OPEN_KEEP
        except Exception:
            return False

    # the hand that just moves the cursor: not a command by itself (an open hand whichever way its palm faces: the hand
    # at rest on the desk is palm down)
    MOVING_HANDS = ("", "any", "open_hand", "open_hand_back", "open_hand_down", "open_hand_up", "thumb_tucked")

    def commanding(self) -> bool:
        """The hand is showing a shape that an enabled composite gesture is bound to (as of the last step). An open hand does
        not count although a hold can be bound to it: it is also the hand that only moves the cursor."""
        posture = self._active_posture
        if posture in self.MOVING_HANDS:
            return False
        return any(c.get("enabled", True) and not c.get("motion_only") and c.get("base_posture") == posture
                   for c in self.registry.composite_gestures.values())

    def step(
        self,
        now: float,
        landmarks: Optional[Sequence[Any]],
        active_gesture: str,
        pinched_fingers: set,
        aspect: float = 0.5625,
        scope: Optional[str] = None,
        posture: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Evaluate composite gestures on the current frame. Returns triggered composite info or None.

        `scope`: "normal" = the gestures of the ordinary hand controls, "motion" = only those marked "motion mode only"
        (the runtime keeps one detector for each), None = every gesture. `posture`: the hand shape when the caller
        already knows it (motion mode: the rule engine does not run there, so the shape comes from the joints alone)."""
        composites = [c for c in self.registry.composite_gestures.values() if c.get("enabled", True)
                      and (scope is None or bool(c.get("motion_only")) == (scope == "motion"))]
        if not composites or not landmarks or len(landmarks) < 21:
            self.reset()
            return None

        if posture is None:
            posture = self._detect_base_posture(landmarks, active_gesture, pinched_fingers)
        # 손 펴기 is the palm to the camera only (2026-11-12); the back and the palm-to-the-floor hand stand in for each
        # other when only one of them is bound
        from manux.compat.hand_mouse.pose_probe import unless_bound
        posture = unless_bound(posture, {c.get("base_posture") for c in composites})
        # an open hand being held stays one while it only loosens (_open_goes_on). Since the open hand has four shapes
        # by where the palm faces (2026-11-12), that goes for each of them, and the loosened hand's facing is asked
        # again: before, only the palm-to-the-camera hand was kept, so a hold on the palm-to-the-floor hand ended with
        # the first relaxed finger (the reason 200 of 551 open-hand stretches ended early, see OPEN_KEEP)
        from manux.compat.hand_mouse.pose_probe import FACINGS, faced
        if self._active_posture in ("open_hand",) + FACINGS and posture not in ("open_hand",) + FACINGS \
                and self._open_goes_on(landmarks, posture, composites):
            posture = unless_bound(faced("open_hand", landmarks, .5625), {c.get("base_posture") for c in composites})
        self.trace.append((now, posture, active_gesture, landmarks))
        for comp in composites:                      # a spent hold stays spent while its shape is (still) seen
            if comp["key"] in self._hold_spent and comp.get("base_posture") == posture:
                if now - self._hold_spent[comp["key"]] >= self.REARM:
                    del self._hold_spent[comp["key"]]            # the shape was gone long enough: a new hold
                else:
                    self._hold_spent[comp["key"]] = now
        # a frame or two that reads as no shape (turning the hand, blur) must not restart a hold (2026-11-10: the hold
        # felt slow); a real change to another shape still counts at once
        held = self._active_posture
        if not posture and held and any(c.get("base_posture") == held and c.get("trigger_type") == "hold" for c in composites)                 and not any(c.get("base_posture") == held and c.get("trigger_type") == "double_pinch" for c in composites):
            # (only for a shape held for a "hold" gesture: letting go of a double pinch must count at once)
            if self._blank_since is None:
                self._blank_since = now
            if now - self._blank_since <= self.BLANK_GRACE:
                return None
        else:
            self._blank_since = None
        palm_x = (float(landmarks[0].x) + float(landmarks[9].x)) * 0.5
        palm_y = (float(landmarks[0].y) + float(landmarks[9].y)) * 0.5 * max(0.25, float(aspect))

        # another shape for a few frames (an open hand read as the knife hand, a taught shape, a V) must not end the shape
        # being held for a "hold" either. Not when a double pinch is involved: its taps are that short on purpose.
        since = now
        if (posture and held and posture != held and self.SHAPE_GRACE > 0
                and any(c.get("base_posture") == held and c.get("trigger_type") == "hold" for c in composites)
                and not any(c.get("base_posture") in (held, posture) and c.get("trigger_type") == "double_pinch" for c in composites)):
            if self._other[0] != posture:
                self._other = (posture, now)
            if now - self._other[1] <= self.SHAPE_GRACE:
                return None                        # the held shape goes on (its time too)
            since = self._other[1]                 # it stayed: the new shape began when it first showed
        elif posture == held:
            self._other = ("", 0.0)

        if posture != self._active_posture:
            # Check double_pinch transition (when entering a posture twice within 0.45s)
            if posture:
                for comp in composites:
                    if comp.get("base_posture") == posture and comp.get("trigger_type") == "double_pinch":
                        ckey = comp["key"]
                        cooldown = float(comp.get("cooldown", 0.65))
                        if now - self._last_trigger_time.get(ckey, -1e9) < cooldown:
                            continue
                        prev_tap = self._last_tap_time.get(ckey, -1e9)
                        if 0.06 <= (now - prev_tap) <= 0.48:
                            self._last_trigger_time[ckey] = now
                            self._last_tap_time[ckey] = -1e9
                            return comp
                        self._last_tap_time[ckey] = now
            self._active_posture = posture
            self._posture_since = since
            self._anchor_xy = (palm_x, palm_y)
            self._history = [(now, palm_x, palm_y)]
            self._path = [(now, palm_x, palm_y)]
            self._other = ("", 0.0)
            self._hold_fired_for.clear()
            return None

        if not posture:
            return None

        self._path.append((now, palm_x, palm_y))
        while len(self._path) > 2 and now - self._path[0][0] > 3.0:
            self._path.pop(0)
        self._history.append((now, palm_x, palm_y))
        while len(self._history) > 2 and (now - self._history[0][0]) > 0.65:
            self._history.pop(0)

        for comp in composites:
            if comp.get("base_posture") != posture:
                continue
            ckey = comp["key"]
            cooldown = float(comp.get("cooldown", 0.65))
            if now - self._last_trigger_time.get(ckey, -1e9) < cooldown:
                continue
            trig = str(comp.get("trigger_type", "swipe_left"))

            if trig == "hold":
                hold_sec = float(comp.get("hold_seconds", 0.55))
                if ckey in self._hold_spent:
                    continue
                other, when = self._last_hold_fire
                if other and other != ckey and now - when < self.OTHER_HOLD:
                    continue
                if ckey not in self._hold_fired_for and (now - self._posture_since) >= hold_sec:
                    # The hand must be still: near where the shape began, or (2026-11-11) not having moved for the whole
                    # hold time wherever it is now - a hand that opened while moving and then stopped never ran before.
                    anchored = (self._anchor_xy is not None
                                and math.hypot(palm_x - self._anchor_xy[0], palm_y - self._anchor_xy[1]) <= self.STILL)
                    recent = [p for p in self._path if now - p[0] <= hold_sec]
                    settled = (self.STILL_ROLLING and bool(recent) and now - recent[0][0] >= hold_sec - .1
                               and max(math.hypot(palm_x - x, palm_y - y) for _, x, y in recent) <= self.STILL)
                    if anchored or settled:
                        self._hold_fired_for.add(ckey)
                        self._last_trigger_time[ckey] = now
                        self._hold_spent[ckey] = now
                        self._last_hold_fire = (ckey, now)
                        return comp
                continue

            if trig.startswith("swipe_") and len(self._history) >= 3:
                t0, x0, y0 = self._history[0]
                dt = max(1e-3, now - t0)
                dx = palm_x - x0
                dy = palm_y - y0
                dist = math.hypot(dx, dy)
                speed = dist / dt
                min_dist = float(comp.get("min_distance", 0.08))
                min_spd = float(comp.get("min_speed", 0.20))
                if dist < min_dist or speed < min_spd:
                    continue
                target_dir = trig.replace("swipe_", "")
                matched = False
                if target_dir == "left" and dx <= -min_dist and abs(dx) >= abs(dy) * 1.25:
                    matched = True
                elif target_dir == "right" and dx >= min_dist and abs(dx) >= abs(dy) * 1.25:
                    matched = True
                elif target_dir == "up" and dy <= -min_dist and abs(dy) >= abs(dx) * 1.25:
                    matched = True
                elif target_dir == "down" and dy >= min_dist and abs(dy) >= abs(dx) * 1.25:
                    matched = True
                if matched:
                    self._last_trigger_time[ckey] = now
                    self._history = [(now, palm_x, palm_y)]
                    self._anchor_xy = (palm_x, palm_y)
                    return comp
        return None


_GLOBAL_REGISTRY: Optional[ActionBindingRegistry] = None
# 2026-11-11 (the person's choice, "프로필별로"): each calibration profile has its own gesture -> command bindings
# (with its composite gestures and registered motions, which live in the same file). No profile = the default state,
# config/gesture_bindings.json; a profile's file is config/profiles/bindings/<name>.json (that folder is git-ignored: a
# profile name can be a person's name) and holds only what the profile changed ("a profile nothing was changed in is
# the default state"), laid over the default file when it is read.
_BINDING_PROFILE: Optional[str] = None


def bindings_path(profile: Optional[str] = None) -> Path:
    name = str(profile or "").strip()
    if not name:
        return DEFAULT_BINDINGS_PATH
    from manux.compat.hand_mouse import profiles
    if not profiles.valid_name(name):
        raise ValueError(f"사용할 수 없는 프로필 이름입니다: {name!r}")
    return DEFAULT_BINDINGS_PATH.parent / profiles.PROFILES_DIR / "bindings" / f"{name}.json"


def use_binding_profile(profile: Optional[str]) -> ActionBindingRegistry:
    """Switch the bindings in use to those of `profile` (None / "" = the common ones). Asking for the profile already
    in use changes nothing, so the dashboard and both engines may all call it."""
    global _GLOBAL_REGISTRY, _BINDING_PROFILE
    name = str(profile or "").strip() or None
    if name == _BINDING_PROFILE:
        return get_action_registry()
    try:
        path = bindings_path(name)
    except ValueError:
        return get_action_registry()
    _BINDING_PROFILE = name
    _GLOBAL_REGISTRY = ActionBindingRegistry(path, base_path=DEFAULT_BINDINGS_PATH if name else None)
    return _GLOBAL_REGISTRY


def get_action_registry(path: Optional[Path | str] = None) -> ActionBindingRegistry:
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None and path is None:
        try:
            path = bindings_path(_BINDING_PROFILE)
        except ValueError:
            path = None
    if _GLOBAL_REGISTRY is None or (path is not None and Path(path) != _GLOBAL_REGISTRY.path):
        _GLOBAL_REGISTRY = ActionBindingRegistry(
            path, base_path=DEFAULT_BINDINGS_PATH if _BINDING_PROFILE and path == bindings_path(_BINDING_PROFILE) else None)
    else:
        _GLOBAL_REGISTRY.reload_if_changed()
    return _GLOBAL_REGISTRY
