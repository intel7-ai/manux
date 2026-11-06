"""
Which scrollable region is under the cursor? (browsing target of the thumb+index page scroll)

A page often holds several independent scroll areas (a sidebar, a chat list, a code pane, the page itself).
The wheel goes to the one under the cursor, so that region is the browsing target: the cursor is put in the
middle of it and kept there while the user browses (see MouseController._begin_browse).

How it is found: Windows UI Automation. Starting at the element under a screen point, walk up through its parents
and take the first one that supports the Scroll pattern and really scrolls (vertically, else horizontally); its
bounding rectangle is the region. That is the innermost scroll area, which is the one the wheel would move.

UIA can take tens of milliseconds (browsers more), so it never runs in the control loop: a background thread keeps a
small cache for the cursor position (`hint()` is called every frame and is a few assignments), and `target_at()` at
the start of a grab normally answers from the cache. If the cache has nothing it waits a short time for the thread,
then gives up (the caller falls back to the middle of the window).

Honest limit: it only finds what an application exposes through UI Automation. Native Windows apps do. Chrome/Edge
expose their page when accessibility is on (they turn it on when a UIA client asks, which can take a moment);
some apps (editors drawing their own UI, games) expose nothing and then the window fallback is used.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

Rect = Tuple[int, int, int, int]  # left, top, right, bottom (screen pixels)

MIN_W, MIN_H = 80, 60            # a scroll area smaller than this is not worth centring on
MAX_DEPTH = 14                   # ancestors looked at
CACHE_TTL = 2.0                  # s a probe result may be used
ACTIVE_WINDOW = 0.6              # only probe while hint() was called within this many seconds
MOVE_PX = 8                      # probe again after the cursor moved this far outside the cached region
MAX_DUTY = 0.4


@dataclass(frozen=True)
class ScrollTarget:
    rect: Rect
    name: str = ""
    vertical: bool = True
    window_rect: Optional[Rect] = None       # the top-level window it lives in (to tell a page-sized region from a pane)

    @property
    def area_fraction(self) -> float:
        """Share of the window this region covers (1.0 = the whole window); 0 when the window is unknown."""
        if not self.window_rect:
            return 0.0
        wa = max(1, (self.window_rect[2] - self.window_rect[0]) * (self.window_rect[3] - self.window_rect[1]))
        ra = max(0, (self.rect[2] - self.rect[0]) * (self.rect[3] - self.rect[1]))
        return ra / wa

    @property
    def center(self) -> Tuple[int, int]:
        return (self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2

    def contains(self, x: float, y: float, margin: int = 0) -> bool:
        l, t, r, b = self.rect
        return l - margin <= x <= r + margin and t - margin <= y <= b + margin


def uia_scroll_probe() -> Callable[[int, int], Optional[ScrollTarget]]:
    """Build a function (x, y) -> the innermost scrolling region under that point, or None."""
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0)

    def probe(x: int, y: int) -> Optional[ScrollTarget]:
        ctrl = auto.ControlFromPoint(x, y)
        horizontal_only: Optional[ScrollTarget] = None
        for _ in range(MAX_DEPTH):
            if ctrl is None:
                break
            try:
                pattern = ctrl.GetScrollPattern()      # None when the control has no Scroll pattern
            except Exception:
                pattern = None
            if pattern is not None:
                try:
                    vertical, horizontal = bool(pattern.VerticallyScrollable), bool(pattern.HorizontallyScrollable)
                    r = ctrl.BoundingRectangle
                except Exception:
                    vertical = horizontal = False
                    r = None
                if r is not None and (vertical or horizontal):
                    w, h = r.right - r.left, r.bottom - r.top
                    if w >= MIN_W and h >= MIN_H:
                        window_rect = None
                        try:
                            top = ctrl.GetTopLevelControl()
                            tr = top.BoundingRectangle if top is not None else None
                            if tr is not None and tr.right > tr.left:
                                window_rect = (tr.left, tr.top, tr.right, tr.bottom)
                        except Exception:
                            pass
                        target = ScrollTarget((r.left, r.top, r.right, r.bottom),
                                              f"{ctrl.ControlTypeName}:{(ctrl.Name or ctrl.ClassName or '')[:24]}", vertical,
                                              window_rect)
                        if vertical:
                            return target
                        horizontal_only = horizontal_only or target
            try:
                ctrl = ctrl.GetParentControl()
            except Exception:
                break
        return horizontal_only

    return probe


class ScrollTargetFinder:
    def __init__(self, probe_factory: Callable[[], Callable[[int, int], Optional[ScrollTarget]]] = uia_scroll_probe,
                 get_config: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        self._probe_factory = probe_factory
        self._get_config = get_config or (lambda: {})
        self.available = sys.platform == "win32"
        self._pos = (0, 0)
        self._asked_at = 0.0
        self._cache: Optional[Tuple[ScrollTarget, float]] = None   # (target, time found); None target = nothing there
        self._cache_pos: Optional[Tuple[int, int]] = None
        self._cache_none_at = -1e9
        self._probed_pos: Optional[Tuple[int, int]] = None
        self._want = threading.Event()
        self._done = threading.Condition()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.probe_ms = 0.0
        self.probes = 0

    # ------------------------------------------------------------ control loop (hot path)
    def hint(self, x: int, y: int) -> None:
        """The cursor is here. Cheap: the thread decides whether a probe is due."""
        self._pos = (int(x), int(y))
        self._asked_at = time.perf_counter()

    def cached(self, x: int, y: int) -> Optional[ScrollTarget]:
        """The target remembered for this point (fresh and containing it), else None."""
        entry = self._cache
        if entry is None:
            return None
        target, found = entry
        if time.perf_counter() - found > CACHE_TTL or not target.contains(x, y, margin=4):
            return None
        return target

    def target_at(self, x: int, y: int, wait: float = 0.15) -> Optional[ScrollTarget]:
        """The scrolling region under (x, y): from the cache, else ask the thread and wait up to `wait` seconds."""
        if not self.available:
            return None
        self.hint(x, y)
        hit = self.cached(x, y)
        if hit is not None:
            return hit
        if time.perf_counter() - self._cache_none_at < 0.4 and self._cache_pos and \
                abs(self._cache_pos[0] - x) + abs(self._cache_pos[1] - y) <= MOVE_PX:
            return None                          # just looked here and found nothing
        self.start()
        self._want.set()
        deadline = time.perf_counter() + max(0.0, wait)
        with self._done:
            while time.perf_counter() < deadline:
                hit = self.cached(x, y)
                if hit is not None:
                    return hit
                if self._cache_pos is not None and abs(self._cache_pos[0] - x) + abs(self._cache_pos[1] - y) <= MOVE_PX \
                        and time.perf_counter() - self._cache_none_at < 0.4:
                    return None
                self._done.wait(timeout=0.01)
        return self.cached(x, y)

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is None and self.available:
            self._thread = threading.Thread(target=self._run, name="scroll-target", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._want.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def poll_once(self, probe: Callable[[int, int], Optional[ScrollTarget]]) -> None:
        x, y = self._pos
        t0 = time.perf_counter()
        try:
            target = probe(x, y)
        except Exception:
            target = None        # a control that vanished mid-query must not stop the thread
        now = time.perf_counter()
        self.probe_ms = (now - t0) * 1000.0
        self.probes += 1
        self._probed_pos = (x, y)
        with self._done:
            self._cache_pos = (x, y)
            if target is not None:
                self._cache = (target, now)
            else:
                self._cache = None
                self._cache_none_at = now
            self._done.notify_all()

    def _due(self) -> bool:
        x, y = self._pos
        if self._probed_pos is None:
            return True
        entry = self._cache
        if entry is not None and entry[0].contains(x, y, margin=4) and time.perf_counter() - entry[1] < CACHE_TTL * 0.6:
            return False                      # still inside the region found a moment ago
        return abs(self._probed_pos[0] - x) + abs(self._probed_pos[1] - y) > MOVE_PX or \
            time.perf_counter() - self._cache_none_at > 1.0

    def _run(self) -> None:
        try:
            import uiautomation as auto

            ctx = auto.UIAutomationInitializerInThread()
        except Exception as exc:  # uiautomation/comtypes missing: the window fallback is used instead
            print(f"[scroll target] disabled: {exc}", file=sys.stderr)
            self.available = False
            return
        try:
            probe = self._probe_factory()
            while not self._stop.is_set():
                forced = self._want.is_set()
                self._want.clear()
                active = time.perf_counter() - self._asked_at < ACTIVE_WINDOW
                if (forced or active) and self._get_config().get("scroll_target_uia", True) and (forced or self._due()):
                    t = time.perf_counter()
                    self.poll_once(probe)
                    busy = time.perf_counter() - t
                    self._stop.wait(max(0.04, busy * (1.0 / MAX_DUTY - 1.0)))
                else:
                    self._want.wait(timeout=0.08)
        finally:
            del ctx
