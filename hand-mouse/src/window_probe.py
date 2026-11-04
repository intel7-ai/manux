"""
Where is the middle of the window under a screen point? (Windows only; anything else returns None.)

Used by the thumb+index page scroll: the cursor is put in the middle of the window it is over and kept there while
the user browses, so the hand's up/down motion can not drag the cursor onto another window or a link.

Coordinates are screen pixels in the same space as `pyautogui` / `SetCursorPos` (the process is DPI aware).
"""
from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

# windows that are not "a page to browse": the desktop and the taskbar
IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def window_center_at(x: int, y: int, own_pid: Optional[int] = None) -> Optional[Tuple[int, int, str]]:
    """(center_x, center_y, window class) of the top-level window under (x, y), or None.

    The center is the middle of the window's CLIENT area (no title bar / frame). Windows of this program,
    the desktop and the taskbar are ignored."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not hwnd:
            return None
        root = user32.GetAncestor(hwnd, 2) or hwnd            # GA_ROOT
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(root, cls, 256)
        if cls.value in IGNORED_CLASSES:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if pid.value == (own_pid if own_pid is not None else os.getpid()):
            return None
        rect = wintypes.RECT()
        if not user32.GetClientRect(root, ctypes.byref(rect)):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 80 or height < 80:
            return None
        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(root, ctypes.byref(origin)):
            return None
        return origin.x + width // 2, origin.y + height // 2, cls.value
    except Exception:
        return None
