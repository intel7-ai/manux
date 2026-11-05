"""The only door to real OS input: a small backend interface, plus a fake that records calls for tests.

The real implementation is `manux.output.windows.win32.Win32Backend` (ctypes SendInput / SetCursorPos).
Every other module of `output/windows` talks to an `InputBackend`, never to the OS.

Units:
- cursor: virtual-desktop pixels (may be negative on a monitor left of / above the primary).
- wheel: raw wheel units, 120 = one notch. vertical > 0 = wheel away from the user (page scrolls up),
  horizontal > 0 = tilt right. Same units v0.3 sent through `pyautogui.scroll` / `hscroll`.
- keys: the names in `KEY_NAMES`.
"""
from __future__ import annotations

from typing import List, Protocol, Tuple, runtime_checkable

BUTTONS = ("left", "right")
KEY_NAMES = ("alt", "ctrl", "shift", "win", "tab", "esc", "left", "right", "up", "down", "d")
MODIFIERS = ("alt", "ctrl", "shift", "win")


@runtime_checkable
class InputBackend(Protocol):
    def cursor_pos(self) -> Tuple[int, int]: ...

    def set_cursor_pos(self, x: int, y: int) -> None: ...

    def button(self, name: str, down: bool) -> None: ...

    def wheel(self, vertical: int, horizontal: int) -> None: ...

    def key(self, name: str, down: bool) -> None: ...


class FakeBackend:
    """Records every call as a tuple in `events`; never touches the OS.

    `fail_on` makes calls of that kind raise (to test that the sink never raises)."""

    def __init__(self, pos: Tuple[int, int] = (0, 0)) -> None:
        self.pos = (int(pos[0]), int(pos[1]))
        self.events: List[tuple] = []
        self.buttons_down: set = set()
        self.keys_down: set = set()
        self.wheel_total = [0, 0]
        self.fail_on: set = set()

    def _check(self, kind: str) -> None:
        if kind in self.fail_on:
            raise OSError(f"fake {kind} failure")

    def cursor_pos(self) -> Tuple[int, int]:
        self._check("cursor_pos")
        return self.pos

    def set_cursor_pos(self, x: int, y: int) -> None:
        self._check("move")
        self.pos = (int(x), int(y))
        self.events.append(("move", int(x), int(y)))

    def button(self, name: str, down: bool) -> None:
        self._check("button")
        if name not in BUTTONS:
            raise ValueError(f"unknown button {name!r}")
        (self.buttons_down.add if down else self.buttons_down.discard)(name)
        self.events.append(("button", name, bool(down)))

    def wheel(self, vertical: int, horizontal: int) -> None:
        self._check("wheel")
        self.wheel_total[0] += int(vertical)
        self.wheel_total[1] += int(horizontal)
        self.events.append(("wheel", int(vertical), int(horizontal)))

    def key(self, name: str, down: bool) -> None:
        self._check("key")
        if name not in KEY_NAMES:
            raise ValueError(f"unknown key {name!r}")
        (self.keys_down.add if down else self.keys_down.discard)(name)
        self.events.append(("key", name, bool(down)))

    # helpers for tests
    def kinds(self) -> List[str]:
        return [e[0] for e in self.events]

    def clicks(self) -> List[str]:
        """Buttons that went down then up, in order."""
        out, held = [], set()
        for e in self.events:
            if e[0] == "button":
                if e[2]:
                    held.add(e[1])
                elif e[1] in held:
                    held.discard(e[1])
                    out.append(e[1])
        return out
