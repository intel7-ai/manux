"""
IME/focus-robust hotkeys for Hand-Mouse (Windows).

cv2.waitKey only sees a key when an OpenCV window has focus AND the keyboard is in
English input mode (in Korean IME mode 'o' arrives as 'ㅐ'). Hand-Mouse moves and
clicks the real cursor, so its window easily loses focus. This poller reads physical
keys with GetAsyncKeyState, which ignores the IME, and can optionally require that a
Hand-Mouse window is in the foreground.
"""

from __future__ import annotations

import sys
from typing import Dict, Iterable

VK_F2 = 0x71
VK_O = 0x4F
VK_0 = 0x30
VK_NUMPAD0 = 0x60

WINDOW_TITLE_PREFIX = "Hand-Mouse"


class KeyPoller:
    """Edge-triggered physical key polling (call once per frame)."""

    def __init__(self) -> None:
        self.available = sys.platform == "win32"
        self._prev: Dict[int, bool] = {}
        self._user32 = None
        if self.available:
            try:
                import ctypes

                self._user32 = ctypes.windll.user32
                self._ctypes = ctypes
            except Exception:
                self.available = False

    def _is_down(self, vk: int) -> bool:
        state = int(self._user32.GetAsyncKeyState(vk))
        # 0x8000 = down now, 0x0001 = pressed since the last call (catches quick taps)
        return bool(state & 0x8000) or bool(state & 0x0001)

    def _foreground_is_ours(self) -> bool:
        hwnd = self._user32.GetForegroundWindow()
        buf = self._ctypes.create_unicode_buffer(256)
        self._user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value.startswith(WINDOW_TITLE_PREFIX)

    def pressed(self, vks: Iterable[int], require_focus: bool = False) -> bool:
        """True once per physical press of any key in `vks`."""
        if not self.available:
            return False
        hit = False
        for vk in vks:
            down = self._is_down(vk)
            if down and not self._prev.get(vk, False):
                hit = True
            self._prev[vk] = down
        if hit and require_focus:
            return self._foreground_is_ours()
        return hit
