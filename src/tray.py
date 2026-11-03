"""
System-tray icon (pystray). Menu clicks only enqueue command names; the main loop executes them.
"""

from __future__ import annotations

import queue
import sys
from typing import Callable, Optional

try:  # optional: without pystray/Pillow the app simply runs without a tray icon
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None


def _make_icon(color=(0, 200, 120)):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(24, 24, 32, 255), outline=color + (255,), width=4)
    # a simple hand-ish glyph: palm + four fingers
    d.rounded_rectangle((20, 30, 44, 50), radius=5, fill=color + (255,))
    for i in range(4):
        x = 20 + i * 7
        d.rounded_rectangle((x, 16 + (i % 3 == 1) * -3, x + 5, 34), radius=2, fill=color + (255,))
    return img


class TrayIcon:
    """Commands put on `.commands`: pause, hud, overlay, settings, quit."""

    def __init__(self, is_paused: Callable[[], bool], is_hud: Callable[[], bool],
                 is_overlay: Callable[[], bool]) -> None:
        self.commands: "queue.Queue[str]" = queue.Queue()
        self._icon = None
        self._is_paused, self._is_hud, self._is_overlay = is_paused, is_hud, is_overlay

    @property
    def available(self) -> bool:
        return pystray is not None

    def start(self) -> bool:
        if pystray is None:
            print("[Tray] pystray not installed: running without a tray icon.", file=sys.stderr)
            return False
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Pause / Resume mouse control", lambda: self.commands.put("pause"),
                 checked=lambda _i: self._is_paused(), default=True),
            item("Camera window (HUD)", lambda: self.commands.put("hud"), checked=lambda _i: self._is_hud()),
            item("Status overlay", lambda: self.commands.put("overlay"), checked=lambda _i: self._is_overlay()),
            item("Settings...", lambda: self.commands.put("settings")),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: self.commands.put("quit")),
        )
        try:
            self._icon = pystray.Icon("hand-mouse", _make_icon(), "Hand-Mouse", menu)
            self._icon.run_detached()
        except Exception as exc:
            print(f"[Tray] disabled: {exc}", file=sys.stderr)
            self._icon = None
            return False
        return True

    def refresh(self) -> None:
        """Re-evaluate the checkmarks and tint the icon while paused."""
        if self._icon is not None:
            try:
                self._icon.icon = _make_icon((255, 180, 0) if self._is_paused() else (0, 200, 120))
                self._icon.update_menu()
            except Exception:
                pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
