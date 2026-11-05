"""
System-wide shortcuts (port of hand-mouse/src/global_hotkeys.py, unchanged): turn input on/off and hide the window from any program.

Uses Windows RegisterHotKey: the combination reaches MANUX before the program in front sees it,
so it wins over that program's own shortcuts, and unlike a low-level keyboard hook it costs
nothing on every other key press. Another program that registered the same combination first
keeps it; `failed` then names the action so the UI can ask for a different combination.

Combinations are text like "ctrl+alt+shift+q" (stored in the user's settings). Callbacks run on
this module's thread; keep them short and hand work to the UI thread.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable, Dict, Optional, Set, Tuple

DEFAULTS = {"toggle": "ctrl+alt+shift+q", "hide": "ctrl+alt+shift+h"}

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
NAMED_KEYS = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "pause": 0x13,
              "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E}
NAMED_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 13)})
WM_HOTKEY, WM_APP_RELOAD, WM_QUIT = 0x0312, 0x8001, 0x0012


def parse(combo: str) -> Optional[Tuple[int, int]]:
    """'ctrl+alt+shift+q' -> (modifier flags, virtual key). None when it is not a usable shortcut:
    a system-wide shortcut needs at least one modifier and exactly one other key."""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    mods, keys = 0, []
    for part in parts:
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        else:
            keys.append(part)
    if not mods or len(keys) != 1:
        return None
    key = keys[0]
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        return mods, ord(key.upper())
    if key in NAMED_KEYS:
        return mods, NAMED_KEYS[key]
    return None


def normalize(combo: str) -> Optional[str]:
    """Canonical text ('Shift+Q+Ctrl' -> 'ctrl+shift+q'), None when unusable."""
    if parse(combo) is None:
        return None
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    order = [m for m in ("ctrl", "alt", "shift", "win") if m in parts]
    return "+".join(order + [p for p in parts if p not in MODIFIERS])


def label(combo: str) -> str:
    """'ctrl+alt+shift+q' -> 'Ctrl + Alt + Shift + Q' for the screen."""
    return " + ".join(p.upper() if len(p) == 1 else p.capitalize() for p in combo.split("+"))


class GlobalHotkeys:
    """Registers {action: combo} and calls callbacks[action] when pressed anywhere."""

    def __init__(self, callbacks: Dict[str, Callable[[], None]]) -> None:
        self.callbacks = callbacks
        self.combos: Dict[str, str] = {}
        self.failed: Set[str] = set()
        self.available = sys.platform == "win32"
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._applied = threading.Event()

    def start(self, combos: Dict[str, str]) -> None:
        self.combos = dict(combos)
        if not self.available or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="global-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        self._applied.wait(2.0)

    def set(self, combos: Dict[str, str]) -> Set[str]:
        """Replace the combinations; returns the actions that could not be registered."""
        self.combos = dict(combos)
        if not self.available or not self._thread_id:
            return set()
        import ctypes
        self._applied.clear()
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP_RELOAD, 0, 0)
        self._applied.wait(2.0)
        return set(self.failed)

    def stop(self) -> None:
        if self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = 0

    # ------------------------------------------------------------------ hotkey thread
    def _register_all(self, user32, ids: Dict[int, str]) -> None:
        for hotkey_id in list(ids):
            user32.UnregisterHotKey(None, hotkey_id)
        ids.clear()
        self.failed = set()
        for n, (action, combo) in enumerate(sorted(self.combos.items()), start=1):
            parsed = parse(combo)
            if parsed is None or not user32.RegisterHotKey(None, n, parsed[0] | MOD_NOREPEAT, parsed[1]):
                self.failed.add(action)
                continue
            ids[n] = action
        self._applied.set()

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # create this thread's message queue
        self._ready.set()
        ids: Dict[int, str] = {}
        self._register_all(user32, ids)
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    callback = self.callbacks.get(ids.get(int(msg.wParam), ""))
                    if callback is not None:
                        try:
                            callback()
                        except Exception as exc:   # a broken callback must not end the hotkey thread
                            print(f"[Warn] shortcut action failed: {exc!r}", file=sys.stderr)
                elif msg.message == WM_APP_RELOAD:
                    self._register_all(user32, ids)
        finally:
            for hotkey_id in ids:
                user32.UnregisterHotKey(None, hotkey_id)
