"""Undo an "always on top" flag that another program's window picks up right after the app sends Win+D.

2026-11-11: a Chrome window (playing a YouTube video) gained WS_EX_TOPMOST in the same second the app's
"open hand hold -> show desktop" fired, and stayed above every other window until the flag was cleared by hand
(the same symptom was seen earlier on the Claude desktop window). Pressing Win+D on purpose did not reproduce it
and ~50 other firings that day did not cause it, so the cause inside Windows is unknown. The app only undoes what
appeared within a couple of seconds of its own Win+D: windows that were already topmost, and the app's own
windows, are left alone."""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Dict, Iterable, List, Set

CHECKS = (0.4, 1.0, 2.0)          # seconds after Win+D at which new topmost windows are looked for
WS_EX_TOPMOST = 0x8


def newly_topmost(before: Set[int], now: Dict[int, int], own_pid: int) -> List[int]:
    """Windows topmost now that were not before and do not belong to this process. now: hwnd -> pid."""
    return [h for h, pid in now.items() if h not in before and pid != own_pid]


def _topmost_windows() -> Dict[int, int]:
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetWindowLongW.restype = ctypes.c_long
    found: Dict[int, int] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h) and u.GetWindowLongW(h, -20) & WS_EX_TOPMOST and u.GetWindowTextLengthW(h) > 0:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            found[int(h)] = pid.value
        return True
    u.EnumWindows(cb, 0)
    return found


def _clear(hwnds: Iterable[int]) -> None:
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    for h in hwnds:   # HWND_NOTOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        u.SetWindowPos(wintypes.HWND(h), wintypes.HWND(-2), 0, 0, 0, 0, 0x13)


def is_topmost(hwnd: int) -> bool:
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    u.GetWindowLongW.restype = ctypes.c_long
    return bool(u.GetWindowLongW(wintypes.HWND(hwnd), -20) & WS_EX_TOPMOST)


def clear_if_topmost(hwnd: int) -> bool:
    """The app's own MAIN window must never be "always on top" (its overlays are, on purpose; the main window is an
    ordinary window and nothing in the app asks for it). 2026-11-11 report: "앱 화면이 최상단 layer에 고정되어서 다른 앱을
    눌러도 계속 최상단" - seen on the running app: the main window carried WS_EX_TOPMOST, set by Windows, not by the app
    (the same flag other programs' windows picked up after the app's Win+D, see the module note; the guard below leaves
    the app's own windows alone and only looks for two seconds). True when the flag was there and has been taken off."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        if not is_topmost(int(hwnd)):
            return False
        _clear([int(hwnd)])
        return not is_topmost(int(hwnd))
    except Exception:
        return False


def guard(send: Callable[[], None], log: Callable[[str], None] | None = None) -> None:
    """Run send() (the Win+D key press); then, off the caller's thread, clear topmost flags that appeared."""
    if sys.platform != "win32":
        send()
        return
    try:
        before = set(_topmost_windows())
    except Exception:
        send()
        return
    send()
    own = os.getpid()

    def watch():
        undone: Set[int] = set()
        start = time.monotonic()
        for at in CHECKS:
            time.sleep(max(0.0, start + at - time.monotonic()))
            try:
                new = [h for h in newly_topmost(before, _topmost_windows(), own) if h not in undone]
                if new:
                    _clear(new)
                    undone.update(new)
                    if log:
                        log(f"Win+D 뒤 '항상 위'가 된 창 {len(new)}개를 되돌림")
            except Exception:
                return
    threading.Thread(target=watch, name="topmost-guard", daemon=True).start()
