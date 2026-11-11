"""[키 기억하기]: the one key combination the user presses within a few seconds after asking for it.

Used as "저렇게 / 이 키" in src/llm_binding.py. Only that one combination is kept, and only after the button: there is no
background key logging. Windows only (GetAsyncKeyState polling, so Win combinations are seen too; they still do what
they do in Windows, e.g. Win+D shows the desktop).
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Dict, FrozenSet, Iterable, Optional

MODIFIERS: Dict[int, str] = {0x11: "ctrl", 0x10: "shift", 0x12: "alt", 0x5B: "win", 0x5C: "win"}
KEYS: Dict[int, str] = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x20: "space", 0x21: "pageup", 0x22: "pagedown",
    0x23: "end", 0x24: "home", 0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down", 0x2C: "printscreen",
    0x2D: "insert", 0x2E: "delete",
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    0xAD: "volumemute", 0xAE: "volumedown", 0xAF: "volumeup", 0xB0: "nexttrack", 0xB1: "prevtrack", 0xB3: "playpause",
}
KEYS.update({0x41 + i: chr(ord("a") + i) for i in range(26)})
KEYS.update({0x30 + i: str(i) for i in range(10)})
KEYS.update({0x70 + i: f"f{i + 1}" for i in range(24)})
ORDER = ("ctrl", "alt", "shift", "win")


def combo_text(mods: Iterable[str], key: str) -> str:
    """'ctrl+shift+t' (src/action_bindings.parse_hotkey_keys format, modifiers in a fixed order)."""
    m = set(mods)
    return "+".join([o for o in ORDER if o in m] + [key])


class ComboTracker:
    """Feeds on the set of keys held at each poll; the first combination (modifiers + one key) once all are let go.
    Keys already held when it starts (the click on the button, a modifier) are ignored until released."""

    def __init__(self, held_at_start: FrozenSet[int] = frozenset()) -> None:
        self.ignored = set(held_at_start)
        self.mods: set = set()
        self.key: Optional[str] = None
        self.combo: Optional[str] = None

    def feed(self, held: FrozenSet[int]) -> Optional[str]:
        self.ignored &= set(held)                       # a released key counts again next time
        live = set(held) - self.ignored
        for vk in live:
            if vk in MODIFIERS:
                self.mods.add(MODIFIERS[vk])
            elif vk in KEYS and self.key is None:
                self.key = KEYS[vk]
                self.mods |= {MODIFIERS[v] for v in live if v in MODIFIERS}
        if not live:
            if self.key is not None:
                self.combo = combo_text(self.mods, self.key)
                return self.combo
            self.mods.clear()                           # modifiers alone, let go: not a combination
        return None


def _held_keys() -> FrozenSet[int]:
    import ctypes
    state = ctypes.windll.user32.GetAsyncKeyState
    return frozenset(vk for vk in list(MODIFIERS) + list(KEYS) if state(vk) & 0x8000)


def capture(timeout: float = 8.0, poll: float = .01, held: Callable[[], FrozenSet[int]] = None,
            clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> Optional[str]:
    """Blocks up to `timeout` s (run it in a thread). The combination, or None."""
    if held is None:
        if sys.platform != "win32":
            return None
        held = _held_keys
    tracker = ComboTracker(held())
    end = clock() + timeout
    while clock() < end:
        combo = tracker.feed(held())
        if combo:
            return combo
        sleep(poll)
    return None
