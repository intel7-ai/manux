"""A registered gesture -> a command the person chose. Saved through the app's existing bindings (src/action_bindings.py).

Recognition here is young, so a command goes through a small safety gate: the gesture must be held, it fires once per hold, and never
while control is paused. Keys that could lose a session or a window are refused. The full safety gate of the online-learning plan comes later.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.action_bindings import format_hotkey_badge, get_action_registry, parse_hotkey_keys

HOLD_SECONDS = .5
BREAK_SECONDS = .15      # a short flicker of the recognition does not restart the hold
COOLDOWN_SECONDS = 1.0
KEY_PREFIX = "user:"

COMMON_COMMANDS = (
    ("ctrl+x", "잘라내기"), ("ctrl+c", "복사"), ("ctrl+v", "붙여넣기"), ("ctrl+z", "되돌리기"),
    ("ctrl+s", "저장"), ("ctrl+a", "전체 선택"), ("enter", "Enter"), ("space", "Space"),
    # Windows takes Win combinations before the app sees them, so they cannot be pressed in "다른 키 직접 입력": buttons instead
    ("win+d", "바탕화면 보기"), ("win+shift+s", "화면 캡처"),
)
# Could end the session, close things or wipe text at once
BLOCKED = ({"alt", "f4"}, {"ctrl", "w"}, {"ctrl", "alt", "delete"}, {"ctrl", "alt", "del"}, {"win", "l"}, {"delete"}, {"del"},
           {"shift", "delete"}, {"shift", "del"})


class CommandError(ValueError):
    pass


def binding_key(label: str) -> str:
    return KEY_PREFIX + label


def check_hotkey(value: str) -> str:
    """A key combination that can be sent and is allowed; returns it in the app's form."""
    value = (value or "").strip().lower()
    keys = parse_hotkey_keys(value)
    if not keys:
        raise CommandError("이 키는 쓸 수 없어요")
    if set(keys) in BLOCKED:
        raise CommandError("위험한 키라서 연결할 수 없어요")
    return "+".join(keys)


def set_command(label: str, value: str, registry: Any = None) -> str:
    """Empty value = recognise only. Returns the text to show."""
    registry = registry or get_action_registry()
    if not value:
        registry.set_binding(binding_key(label), "none", "none", track="static")
        return "표시만"
    registry.set_binding(binding_key(label), "hotkey", check_hotkey(value), track="static")
    return format_hotkey_badge(check_hotkey(value))


def command_of(label: str, registry: Any = None) -> str:
    """The saved key combination, or "" when the gesture only shows on the screen."""
    registry = registry or get_action_registry()
    entry = registry.bindings.get(binding_key(label))
    if not entry or entry.get("action_type") != "hotkey" or not entry.get("enabled", True):
        return ""
    return str(entry.get("action_value") or "")


def forget_command(label: str, registry: Any = None) -> None:
    registry = registry or get_action_registry()
    if registry.bindings.pop(binding_key(label), None) is not None:
        registry.save()


def command_text(value: str) -> str:
    """How a command reads on the screen: its everyday name when it has one."""
    if not value:
        return "표시만"
    for key, name in COMMON_COMMANDS:
        if key == value and key not in ("enter", "space"):
            return f"{name} ({format_hotkey_badge(value)})"
    return format_hotkey_badge(value)


def choices() -> List[Dict[str, str]]:
    return [{"value": key, "label": name} for key, name in COMMON_COMMANDS]


class CommandGate:
    """Fires a gesture once per hold, after it has stood for `hold` seconds."""

    def __init__(self, hold: float = HOLD_SECONDS, break_seconds: float = BREAK_SECONDS, cooldown: float = COOLDOWN_SECONDS) -> None:
        self.hold, self.break_seconds, self.cooldown = hold, break_seconds, cooldown
        self.reset()

    def reset(self) -> None:
        self._label: Optional[str] = None
        self._since = 0.0
        self._last_seen = 0.0
        self._fired = False
        self._last_fire = float("-inf")
        self.progress = 0.0

    def step(self, t: float, label: Optional[str], allowed: bool = True) -> Optional[str]:
        """`label` = the gesture standing now. Returns it on the frame its command should run."""
        if label is not None and label != self._label:
            self._label, self._since, self._fired = label, t, False
        if label is not None:
            self._last_seen = t
        elif self._label is not None and t - self._last_seen > self.break_seconds:
            self._label, self._fired, self.progress = None, False, 0.0
        if self._label is None:
            return None
        self.progress = min(1.0, (t - self._since) / self.hold) if self.hold > 0 else 1.0
        if not allowed:
            self._since = t            # paused: holding through it must not count
            self.progress = 0.0
            return None
        if self._fired or t - self._since < self.hold or t - self._last_fire < self.cooldown:
            return None
        self._fired, self._last_fire = True, t
        return self._label
