"""
UI target "sticky slowdown" (aim assist, stage 1).

Small clickable things (close buttons, checkboxes, links ...) are hard to hit with a hand in
mid-air. A background thread asks Windows UI Automation which clickable controls are around the
cursor and caches their rectangles. The cursor loop then calls `factor(x, y)` every frame; it
returns a cursor-speed multiplier in (0, 1]: 1.0 far from any target, ramping smoothly down to
`ui_magnet_min_factor` on top of one. The cursor is never pulled or moved, only slowed.

`factor()` is a few arithmetic ops on cached rectangles, so the control loop never waits on UIA.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]  # left, top, right, bottom (screen pixels)

# Controls worth slowing down for. Lists/trees/panes are deliberately absent: they are big and
# slowing the whole file list would only be annoying.
CLICKABLE_TYPES = frozenset({
    "ButtonControl", "CheckBoxControl", "RadioButtonControl", "HyperlinkControl",
    "MenuItemControl", "TabItemControl", "SplitButtonControl", "ComboBoxControl",
})
# Leaf controls that usually sit INSIDE a button (its label or icon): look at their parent too.
WRAPPED_TYPES = frozenset({"TextControl", "ImageControl", "CustomControl", "GroupControl"})

RING_POINTS = 6          # probes on a ring around the cursor, plus one at the cursor itself
RECT_TTL = 1.5           # seconds a discovered rectangle stays known
MAX_RECTS = 40
ACTIVE_WINDOW = 0.5      # only poll while factor() was asked within this many seconds
MAX_DUTY = 0.4           # the UIA thread uses at most this share of one core


def rect_distance(rect: Rect, x: float, y: float) -> float:
    """Distance from a point to a rectangle (0 when inside)."""
    l, t, r, b = rect
    dx = max(l - x, 0.0, x - r)
    dy = max(t - y, 0.0, y - b)
    return (dx * dx + dy * dy) ** 0.5


def slowdown_factor(rects: Sequence[Rect], x: float, y: float, radius: float, min_factor: float) -> float:
    """1.0 beyond `radius` from every rect, smoothly down to `min_factor` inside one."""
    if radius <= 0 or not rects:
        return 1.0
    d = min(rect_distance(r, x, y) for r in rects)
    if d >= radius:
        return 1.0
    t = d / radius
    t = t * t * (3.0 - 2.0 * t)  # smoothstep: gentle start, no abrupt edge
    return min_factor + (1.0 - min_factor) * t


def ring_offsets(radius: float, n: int = RING_POINTS) -> List[Tuple[int, int]]:
    import math

    pts = [(0, 0)]
    for i in range(n):
        a = 2.0 * math.pi * i / n
        pts.append((int(round(math.cos(a) * radius)), int(round(math.sin(a) * radius))))
    return pts


def uia_probe(max_size: int) -> Callable[[int, int], Optional[Rect]]:
    """Build a function (x, y) -> rect of the clickable control under that point, or None."""
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0)

    def accept(ctrl) -> Optional[Rect]:
        if ctrl is None or ctrl.ControlTypeName not in CLICKABLE_TYPES:
            return None
        if ctrl.IsOffscreen or not ctrl.IsEnabled:
            return None
        r = ctrl.BoundingRectangle
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0 or w > max_size or h > max_size:
            return None
        return (r.left, r.top, r.right, r.bottom)

    def probe(x: int, y: int) -> Optional[Rect]:
        ctrl = auto.ControlFromPoint(x, y)
        found = accept(ctrl)
        if found is not None or ctrl is None or ctrl.ControlTypeName not in WRAPPED_TYPES:
            return found
        parent = ctrl
        for _ in range(2):  # label/icon inside a button: the button is the real target
            parent = parent.GetParentControl()
            if parent is None:
                return None
            found = accept(parent)
            if found is not None:
                return found
        return None

    return probe


class UITargetMagnet:
    def __init__(self, get_config: Callable[[], Dict[str, Any]],
                 probe_factory: Callable[[int], Callable[[int, int], Optional[Rect]]] = uia_probe) -> None:
        self._get_config = get_config
        self._probe_factory = probe_factory
        self.available = sys.platform == "win32"
        self._rects: Dict[Rect, float] = {}  # rect -> time last seen
        self._snapshot: Tuple[Rect, ...] = ()
        self._pos = (0, 0)
        self._asked_at = 0.0
        self.last_factor = 1.0
        self.poll_ms = 0.0  # time of the last full poll, for diagnostics
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ control loop (hot path)
    def factor(self, x: int, y: int) -> float:
        cfg = self._get_config()
        self._pos = (x, y)
        self._asked_at = time.perf_counter()
        if not self.available or not cfg.get("ui_magnet_enabled", True):
            self.last_factor = 1.0
            return 1.0
        radius = float(cfg.get("ui_magnet_radius", 28.0))
        min_factor = max(0.05, min(1.0, float(cfg.get("ui_magnet_min_factor", 0.7))))
        self.last_factor = slowdown_factor(self._snapshot, x, y, radius, min_factor)
        return self.last_factor

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is None and self.available:
            self._thread = threading.Thread(target=self._run, name="ui-magnet", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def poll_once(self, probe: Callable[[int, int], Optional[Rect]], cfg: Dict[str, Any]) -> None:
        x0, y0 = self._pos
        radius = float(cfg.get("ui_magnet_radius", 28.0))
        t0 = time.perf_counter()
        for dx, dy in ring_offsets(radius * 0.75):
            if self._stop.is_set():
                return
            try:
                rect = probe(x0 + dx, y0 + dy)
            except Exception:
                rect = None  # a control that vanished mid-query must not stop the thread
            if rect is not None:
                self._rects[rect] = time.perf_counter()
        now = time.perf_counter()
        self._rects = {r: t for r, t in self._rects.items() if now - t < RECT_TTL}
        if len(self._rects) > MAX_RECTS:
            newest = sorted(self._rects.items(), key=lambda kv: kv[1], reverse=True)[:MAX_RECTS]
            self._rects = dict(newest)
        self._snapshot = tuple(self._rects)
        self.poll_ms = (now - t0) * 1000.0

    def _run(self) -> None:
        try:
            import uiautomation as auto

            ctx = auto.UIAutomationInitializerInThread()
        except Exception as exc:  # uiautomation/comtypes missing: the app works without the assist
            print(f"[UI magnet] disabled: {exc}", file=sys.stderr)
            self.available = False
            return
        try:
            probe = None
            probe_size = -1
            last_pos = None
            while not self._stop.is_set():
                cfg = self._get_config()
                active = time.perf_counter() - self._asked_at < ACTIVE_WINDOW
                if not cfg.get("ui_magnet_enabled", True) or not active:
                    self._stop.wait(0.2)
                    continue
                size = int(cfg.get("ui_magnet_max_size", 200))
                if probe is None or size != probe_size:
                    probe, probe_size = self._probe_factory(size), size
                moved = last_pos is None or abs(self._pos[0] - last_pos[0]) + abs(self._pos[1] - last_pos[1]) > 3
                if moved:
                    last_pos = self._pos
                    t = time.perf_counter()
                    self.poll_once(probe, cfg)
                    busy = time.perf_counter() - t
                    self._stop.wait(max(0.03, busy * (1.0 / MAX_DUTY - 1.0)))
                else:
                    self._stop.wait(0.06)
        finally:
            del ctx
