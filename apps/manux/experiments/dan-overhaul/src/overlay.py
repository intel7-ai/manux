"""
Tiny always-on-top status widget in a screen corner (Windows).

Semi-transparent and click-through (WS_EX_TRANSPARENT), so it never steals clicks or focus
from the program being controlled. Runs its own Tk thread; `set_state()` is thread-safe.
"""

from __future__ import annotations

import sys
import threading
from typing import Tuple

CORNERS = ("top-right", "top-left", "bottom-right", "bottom-left")
WIDTH, HEIGHT, MARGIN = 190, 34, 14

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000


def corner_position(corner: str, screen_w: int, screen_h: int,
                    w: int = WIDTH, h: int = HEIGHT, margin: int = MARGIN) -> Tuple[int, int]:
    x = margin if corner.endswith("left") else screen_w - w - margin
    y = margin if corner.startswith("top") else screen_h - h - margin - 40  # keep clear of the taskbar
    return x, y


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb)


class StatusOverlay:
    def __init__(self, corner: str = "top-right", alpha: float = 0.82) -> None:
        self.corner = corner if corner in CORNERS else "top-right"
        self.alpha = alpha
        self.visible = True
        self._text = "STARTING"
        self._rgb: Tuple[int, int, int] = (150, 150, 150)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ any thread
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="status-overlay", daemon=True)
            self._thread.start()

    def set_state(self, text: str, rgb: Tuple[int, int, int]) -> None:
        self._text, self._rgb = text, rgb

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)

    def shutdown(self) -> None:
        """Stop and wait for the Tk thread: a Tk root alive at interpreter exit crashes it."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------ Tk thread
    def _make_click_through(self, root) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            root.update_idletasks()
            hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            )
        except Exception:
            pass

    def _run(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", self.alpha)
            bg = "#14141a"
            root.configure(bg=bg)
            x, y = corner_position(self.corner, root.winfo_screenwidth(), root.winfo_screenheight())
            root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
            dot = tk.Canvas(root, width=14, height=14, bg=bg, highlightthickness=0)
            dot.pack(side="left", padx=(10, 6), pady=10)
            oval = dot.create_oval(1, 1, 13, 13, fill="#999999", outline="")
            label = tk.Label(root, text=self._text, bg=bg, fg="#e6e6e6",
                             font=("Segoe UI", 10, "bold"), anchor="w")
            label.pack(side="left", fill="x", expand=True)
            self._make_click_through(root)

            shown = True
            last = None

            def tick() -> None:
                nonlocal shown, last
                if self._stop.is_set():
                    root.destroy()
                    return
                if self.visible != shown:
                    shown = self.visible
                    root.deiconify() if shown else root.withdraw()
                state = (self._text, self._rgb)
                if state != last:
                    last = state
                    label.configure(text=self._text)
                    dot.itemconfigure(oval, fill=_hex(self._rgb))
                root.after(100, tick)

            tick()
            root.mainloop()
            # Drop every Tk object on THIS thread: if the garbage collector frees them later on
            # another thread, Tcl aborts the whole process ("Tcl_AsyncDelete ... wrong thread").
            label = dot = root = tick = None
            import gc

            gc.collect()
        except Exception as exc:  # no display / Tk missing: the app still works without the widget
            print(f"[Overlay] disabled: {exc}", file=sys.stderr)
