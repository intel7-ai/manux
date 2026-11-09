"""Mouse buttons and key chords over an `InputBackend`, with what is held tracked for `release_all`.

Ported from v0.3 `MouseController._mouse_down/_mouse_up`, the click branches of `apply`, `send_motion_action`
(`motion_mode.ACTION_KEYS`) and the Win+D / Alt+Tab branches.
"""
from __future__ import annotations

from typing import List, Mapping, Tuple

from manux.output.windows.backend import InputBackend

# action id -> keys pressed in order and released in reverse
KEY_CHORDS: Mapping[str, Tuple[str, ...]] = {
    "window.task_switcher": ("alt", "tab"),
    "window.show_desktop": ("win", "d"),
    "window.close": ("alt", "f4"),
    "nav.back": ("alt", "left"),
    "nav.forward": ("alt", "right"),
    "tab.prev": ("ctrl", "shift", "tab"),
    "tab.next": ("ctrl", "tab"),
}


class Buttons:
    def __init__(self, backend: InputBackend) -> None:
        self.backend = backend
        self.down: set = set()

    def press(self, name: str) -> None:
        if name not in self.down:
            self.backend.button(name, True)
            self.down.add(name)

    def release(self, name: str) -> None:
        if name in self.down:
            self.down.discard(name)   # forget first: a failing OS call must not leave us thinking it is held
            self.backend.button(name, False)

    def click(self, name: str) -> None:
        self.release(name)
        self.backend.button(name, True)
        self.down.add(name)
        self.release(name)

    def release_all(self) -> List[str]:
        """Release every held button; returns the errors (never raises)."""
        errors = []
        for name in sorted(self.down):
            try:
                self.release(name)
            except Exception as exc:  # noqa: BLE001 - safety path must try every button
                errors.append(f"{name}: {exc}")
        return errors


class Keys:
    def __init__(self, backend: InputBackend) -> None:
        self.backend = backend
        self.down: List[str] = []

    def chord(self, keys: Tuple[str, ...]) -> None:
        """Press `keys` in order, then release them in reverse (released even if a press fails)."""
        try:
            for k in keys:
                self.backend.key(k, True)
                self.down.append(k)
        finally:
            self.release_all(raise_errors=True)

    def release_all(self, raise_errors: bool = False) -> List[str]:
        errors = []
        while self.down:
            k = self.down.pop()
            try:
                self.backend.key(k, False)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{k}: {exc}")
        if errors and raise_errors:
            raise OSError("; ".join(errors))
        return errors
