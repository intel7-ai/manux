"""
OS Mouse Controller for Hand-Mouse.

Maps normalized hand coordinates from GestureEngine to screen pixel coordinates
using either:
- "absolute" mode (active camera margin box -> full screen with EMA smoothing)
- "relative" mode (default): trackpad-like delta movement with pointer acceleration
  (slow hand = precise low gain, fast hand = higher gain) for fine control
- "grab" action (thumb+index pinch): Record & Fling. While pinched the cursor stays put
  and the pinch point's path is recorded. On release the cursor replays that path,
  scaled up per segment by drawing speed (slow = precise, fast flick = far), animated
  smoothly so the travelled line stays visible. A new pinch stops a replay in place.

Executes OS cursor movements, clicks, and drag states via PyAutoGUI, and supports
`dry_run=True` for headless unit testing or preview-only operation.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import pyautogui

try:
    from src.cursor_interp import CursorInterpolator
    from src.filters import TremorStabilizer
    from src.gesture_engine import GestureResult
except ImportError:
    from cursor_interp import CursorInterpolator
    from filters import TremorStabilizer
    from gesture_engine import GestureResult

# Configure PyAutoGUI for zero-latency real-time control
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


class MouseController:
    """Controls the OS mouse cursor based on GestureResult and gesture configuration."""

    def __init__(
        self,
        dry_run: bool = False,
        screen_size: Optional[Tuple[int, int]] = None,
    ) -> None:
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0

        self.dry_run: bool = dry_run
        if screen_size is not None:
            self.screen_w, self.screen_h = int(screen_size[0]), int(screen_size[1])
        else:
            try:
                sz = pyautogui.size()
                self.screen_w, self.screen_h = int(sz[0]), int(sz[1])
            except Exception:
                self.screen_w, self.screen_h = 1920, 1080

        self.last_screen_pos: Tuple[int, int] = (self.screen_w // 2, self.screen_h // 2)
        self._smoothed_pos: Optional[Tuple[float, float]] = None

        self.is_dragging: bool = False
        self._hold_dragging: bool = False
        self._toggle_dragging: bool = False

        self.anchor_hand_pos: Optional[Tuple[float, float]] = None
        self.anchor_screen_pos: Optional[Tuple[float, float]] = None

        # Grab (thumb+index pinch) / fling state
        self.grab_anchor: Optional[Tuple[float, float]] = None
        self._after_grab: bool = False
        self._prev_action: str = "none"
        # Record & Fling state
        self.recorded_path: List[Tuple[float, float, float]] = []  # (t, x, y) normalized pinch pts
        self.grab_speed: float = 0.0  # current pinch-point speed (frame widths/s), for HUD
        self.last_fling_distance: float = 0.0
        self.fling_count: int = 0

        # Replay animation: (t_offset, x, y) screen points; smooth thread when not dry_run
        self._play: Optional[List[Tuple[float, float, float]]] = None
        self._play_t0: float = 0.0
        self._play_gen: int = 0
        self._play_lock = threading.RLock()
        self.smooth_playback: bool = not dry_run

        # Task switcher (thumb+pinky): Alt held, hand motion moves the selection
        self.switcher_open: bool = False
        self._sw_armed: bool = False  # opening pinch released -> confirm gestures accepted
        self._sw_anchor: Optional[Tuple[float, float]] = None
        self._sw_opened_t: float = 0.0
        self._sw_seen_t: float = 0.0
        # Confirm / cancel gestures wait a moment: closing the hand into a fist brushes the
        # thumb past the middle / ring finger, which must not close the switcher
        self._sw_pending: Optional[Tuple[str, float, Tuple[int, int]]] = None  # (kind, t, cursor)
        self.switcher_moves: List[str] = []  # navigation keys sent (for HUD / tests)

        # Grab-scroll (thumb+index pinch): hand motion -> wheel events at the cursor
        self._scroll_prev: Optional[Tuple[float, float]] = None
        self._scroll_anchor: Optional[Tuple[float, float]] = None
        self._scroll_live = False
        self._scroll_acc = [0.0, 0.0]
        self.scroll_total = [0, 0]  # sent wheel units (vertical, horizontal) for HUD / tests
        self._scroll_hist: Deque[Tuple[float, float, float]] = deque()
        self._scroll_net = [0.0, 0.0]  # total wheel amount of the current grab (direction check)
        self._scroll_resume = False    # hand came back after a short loss: re-anchor
        self._lost_since: Optional[float] = None
        self._inertia_v: Optional[List[float]] = None  # wheel units / s while coasting
        self._inertia_t = 0.0
        self._inertia_acc = [0.0, 0.0]

        # Double-click assist: after a left click the cursor is held still briefly so a
        # second pinch lands on the same pixel (Windows needs both clicks within ~4 px)
        self._dbl_until: float = -1e9
        self._dbl_hand: Optional[Tuple[float, float]] = None

        # Relative (fine control) state
        self._rel_pos: Tuple[float, float] = (0.0, 0.0)
        self._rel_t: float = 0.0

        # Smooth ~144 Hz cursor motion between 30 fps camera updates (real mode only)
        self.interpolation: bool = False
        self._interp: Optional[CursorInterpolator] = None if dry_run else CursorInterpolator()

        # Hand-tremor stabilization (One Euro filter + deadband) for cursor movement
        self._tremor = TremorStabilizer()

        # Camera frame height / width, so x and y hand motion are compared physically
        self.frame_aspect: float = 0.75

        # Screen-pixel offset added to absolute mapping (set when leaving grab)
        self.abs_offset: Tuple[float, float] = (0.0, 0.0)
        self._abs_prev: Optional[Tuple[float, float]] = None  # last mapped point (fist boost)
        self._pointer_source = None
        self.dpi_multiplier: float = 1.0  # set per frame from GestureResult

        self.event_log: List[str] = []

    def map_to_screen_absolute(
        self,
        norm_x: float,
        norm_y: float,
        active_margin: float = 0.15,
        clamp: bool = True,
    ) -> Tuple[float, float]:
        """
        Map normalized camera coordinates [active_margin, 1 - active_margin]
        to full screen coordinates [0, screen_w - 1] x [0, screen_h - 1].
        With clamp=False, points in the margin map beyond the screen edges.
        """
        margin = max(0.0, min(0.45, float(active_margin)))
        span = max(1e-6, 1.0 - 2.0 * margin)

        nx = (float(norm_x) - margin) / span
        ny = (float(norm_y) - margin) / span

        if clamp:
            nx = max(0.0, min(1.0, nx))
            ny = max(0.0, min(1.0, ny))

        sx = nx * (self.screen_w - 1)
        sy = ny * (self.screen_h - 1)
        return sx, sy

    def _relative_step(
        self,
        hand_pt: Tuple[float, float],
        config: Dict[str, Any],
        now: float,
    ) -> Tuple[int, int]:
        """Trackpad-style fine control with pointer acceleration."""
        smoothing = max(0.05, min(1.0, float(config.get("smoothing", 0.35))))
        if config.get("tremor_filter", False):
            smoothing = 1.0  # the tremor filter already smooths; stacking both adds lag
        g_min = max(0.0, float(config.get("relative_gain_min", 0.35)))
        g_max = max(g_min, float(config.get("relative_gain_max", 1.2)))
        v_lo = max(0.0, float(config.get("relative_accel_speed_low", 0.1)))
        v_hi = max(v_lo + 1e-6, float(config.get("relative_accel_speed_high", 0.8)))

        hx, hy = float(hand_pt[0]), float(hand_pt[1])
        if self.anchor_hand_pos is None:
            # (Re)start from wherever the cursor is: no jump after a click, throw or hand loss
            self.anchor_hand_pos = (hx, hy)
            self._rel_pos = (float(self.last_screen_pos[0]), float(self.last_screen_pos[1]))
            self._rel_t = now
            self._smoothed_pos = self._rel_pos
            return self.last_screen_pos

        ax, ay = self.anchor_hand_pos
        sx = ax + smoothing * (hx - ax)  # EMA on the hand point suppresses landmark jitter
        sy = ay + smoothing * (hy - ay)
        self.anchor_hand_pos = (sx, sy)

        dt = max(1e-3, min(0.1, now - self._rel_t))
        self._rel_t = now
        dx = sx - ax
        dy = (sy - ay) * self.frame_aspect  # physical units (frame widths)
        speed = math.hypot(dx, dy) / dt
        t = max(0.0, min(1.0, (speed - v_lo) / (v_hi - v_lo)))
        gain = (g_min + (g_max - g_min) * t) * self.dpi_multiplier

        px = max(0.0, min(float(self.screen_w - 1), self._rel_pos[0] + dx * self.screen_w * gain))
        py = max(0.0, min(float(self.screen_h - 1), self._rel_pos[1] + dy * self.screen_w * gain))
        self._rel_pos = (px, py)
        self._smoothed_pos = self._rel_pos
        return int(round(px)), int(round(py))

    def _compute_target_screen_pos(
        self,
        hand_pt: Tuple[float, float],
        config: Dict[str, Any],
        now: float = 0.0,
    ) -> Tuple[int, int]:
        mode = str(config.get("movement_mode", "relative")).lower()
        smoothing = max(0.05, min(1.0, float(config.get("smoothing", 0.35))))
        active_margin = float(config.get("active_margin", 0.15))

        if config.get("tremor_filter", False):
            hand_pt = self._tremor(
                hand_pt,
                now,
                min_cutoff=float(config.get("tremor_min_cutoff", 1.5)),
                beta=float(config.get("tremor_beta", 8.0)),
                deadzone=float(config.get("tremor_deadzone", 0.002)),
            )

        if mode == "relative":
            self._after_grab = False
            return self._relative_step(hand_pt, config, now)
        else:
            mx, my = self.map_to_screen_absolute(hand_pt[0], hand_pt[1], active_margin, clamp=False)
            if self._after_grab:
                # Leaving grab: shift the absolute mapping so the cursor stays put
                self.abs_offset = (
                    float(self.last_screen_pos[0]) - mx,
                    float(self.last_screen_pos[1]) - my,
                )
                self._smoothed_pos = (float(self.last_screen_pos[0]), float(self.last_screen_pos[1]))
            elif self.dpi_multiplier != 1.0 and self._abs_prev is not None:
                # Fist boost: each hand delta adds (mult - 1) x delta to the offset, so the
                # cursor moves mult x as fast and never jumps when the fist opens/closes
                k = self.dpi_multiplier - 1.0
                self.abs_offset = (
                    self.abs_offset[0] + (mx - self._abs_prev[0]) * k,
                    self.abs_offset[1] + (my - self._abs_prev[1]) * k,
                )
            self._abs_prev = (mx, my)
            raw_x = max(0.0, min(float(self.screen_w - 1), mx + self.abs_offset[0]))
            raw_y = max(0.0, min(float(self.screen_h - 1), my + self.abs_offset[1]))
        self._after_grab = False

        if config.get("tremor_filter", False):
            smoothing = 1.0  # the tremor filter already smooths; stacking both adds lag
        if self._smoothed_pos is None:
            sm_x, sm_y = raw_x, raw_y
        else:
            sm_x = self._smoothed_pos[0] + smoothing * (raw_x - self._smoothed_pos[0])
            sm_y = self._smoothed_pos[1] + smoothing * (raw_y - self._smoothed_pos[1])

        self._smoothed_pos = (sm_x, sm_y)
        final_x = int(round(max(0.0, min(float(self.screen_w - 1), sm_x))))
        final_y = int(round(max(0.0, min(float(self.screen_h - 1), sm_y))))
        return final_x, final_y

    # ------------------------------------------------------------------ task switcher
    def _key(self, *names: str) -> None:
        if not self.dry_run:
            for n in names:
                pyautogui.press(n, _pause=False)

    def open_switcher(self, now: float) -> None:
        """Open the Windows task switcher and keep it open (Alt stays held)."""
        if self.switcher_open:
            return
        self._stop_scroll_inertia()
        self._mouse_up()
        self._cancel_playback()
        self.grab_anchor = None
        self.recorded_path = []
        self.switcher_open = True
        self._sw_armed = False
        self._sw_anchor = None
        self._sw_opened_t = now
        self._sw_seen_t = now
        self.switcher_moves = []
        self.event_log.append("switcher_open")
        if not self.dry_run:
            pyautogui.keyDown("alt", _pause=False)
            pyautogui.press("tab", _pause=False)

    def close_switcher(self, confirm: bool = True) -> None:
        """Confirm (switch to the selected window) or cancel (Esc), then release Alt."""
        if not self.switcher_open:
            return
        self.switcher_open = False
        self._sw_pending = None
        self.event_log.append("switcher_confirm" if confirm else "switcher_cancel")
        if not self.dry_run:
            try:
                if not confirm:
                    pyautogui.press("esc", _pause=False)
            finally:
                pyautogui.keyUp("alt", _pause=False)
        # Movement resumes from the current cursor position without a jump
        self.anchor_hand_pos = None
        self._after_grab = True  # absolute mode re-bases here
        self._smoothed_pos = (float(self.last_screen_pos[0]), float(self.last_screen_pos[1]))

    def _switcher_navigate(self, pt: Tuple[float, float], config: Dict[str, Any]) -> None:
        """Hand motion -> arrow keys (one step per `switcher_step` frame widths)."""
        step = max(0.005, float(config.get("switcher_step", 0.05)))
        x, y = float(pt[0]), float(pt[1])
        if self._sw_anchor is None:
            self._sw_anchor = (x, y)
            return
        ax, ay = self._sw_anchor
        dx, dy = x - ax, (y - ay) * self.frame_aspect
        while abs(dx) >= step or abs(dy) >= step:
            if abs(dx) >= abs(dy):
                key = "right" if dx > 0 else "left"
                ax += step if dx > 0 else -step
            else:
                key = "down" if dy > 0 else "up"
                ay += (step if dy > 0 else -step) / max(1e-6, self.frame_aspect)
            self.switcher_moves.append(key)
            self._key(key)
            dx, dy = x - ax, (y - ay) * self.frame_aspect
        self._sw_anchor = (ax, ay)

    def _switcher_apply(self, result: GestureResult, config: Dict[str, Any], now: float) -> None:
        """
        Task switcher open (Alt held).
        pointer mode (default): the cursor keeps moving (open hand / fist) so you point at a
          window thumbnail; thumb+middle clicks it (switches), thumb+pinky confirms the
          highlighted one, thumb+ring cancels.
        arrows mode: hand motion sends arrow keys (cursor still); fist cancels.
        """
        self._sw_seen_t = now
        action = result.action
        gesture = result.active_gesture
        pointer = str(config.get("switcher_navigation", "pointer")).lower() != "arrows"
        if now - self._sw_opened_t >= float(config.get("switcher_timeout", 15.0)):
            self.close_switcher(confirm=True)
            return

        # Closing the hand into a fist often passes through (or settles in) a thumb+index
        # pinch / drag shape. Those have no job in the switcher, so every closed-hand shape
        # moves the cursor at the switcher fist speed (and never presses a mouse button).
        closed_hand = ("fist", "thumb_index_pinch", "thumb_index_middle_pinch")
        if gesture in closed_hand and self._sw_pending is not None:
            self._sw_pending = None  # that "click" was the thumb brushing a finger into a fist
        self._run_switcher_pending(now, config)
        if not self.switcher_open:
            return
        if gesture in ("no_pinch",) + closed_hand and result.cursor_point is not None:
            self._sw_armed = True  # the opening thumb+pinky pinch has been released
            if pointer:
                if gesture in closed_hand:
                    # Thumbnails are small: a much gentler fast-move than the normal fist DPI
                    self.dpi_multiplier = float(config.get("switcher_fist_multiplier", 15.0))
                x, y = self._compute_target_screen_pos(result.cursor_point, config, now)
                self._move_os_cursor(x, y)
                return
            if gesture == "no_pinch":
                self._switcher_navigate(result.cursor_point, config)
                return
        if not self._sw_armed:
            return

        if not pointer and gesture == "fist":
            self.close_switcher(confirm=False)
            return
        kind = {"left_click": "click" if pointer else "confirm", "task_switcher": "confirm",
                "alt_tab": "confirm", "right_click": "cancel"}.get(action)
        if kind is not None and self._sw_pending is None:
            self._sw_pending = (kind, now, self.last_screen_pos)
            self._run_switcher_pending(now, config)

    def _run_switcher_pending(self, now: float, config: Dict[str, Any]) -> None:
        if self._sw_pending is None:
            return
        kind, t, pos = self._sw_pending
        if now - t < float(config.get("switcher_confirm_delay", 0.25)):
            return
        self._sw_pending = None
        if kind == "click":
            # Click the thumbnail that was under the cursor at the gesture, then let go of Alt
            self.event_log.append("switcher_click")
            if not self.dry_run:
                self._move_os_cursor(pos[0], pos[1], immediate=True)
                pyautogui.click(pos[0], pos[1], button="left", _pause=False)
            self.close_switcher(confirm=True)
        elif kind == "confirm":
            self.close_switcher(confirm=True)
        else:
            self.close_switcher(confirm=False)

    # ------------------------------------------------------------------ grab-scroll
    def _send_scroll(self, v: int, h: int) -> None:
        if v:
            self.scroll_total[0] += v
            if not self.dry_run:
                pyautogui.scroll(v, _pause=False)
        if h:
            self.scroll_total[1] += h
            if not self.dry_run:
                pyautogui.hscroll(h, _pause=False)

    @staticmethod
    def _take_steps(acc: List[float], step: int = 10) -> Tuple[int, int]:
        """Pull whole `step`-sized wheel amounts out of the accumulator."""
        v, h = int(acc[0] / step) * step, int(acc[1] / step) * step
        acc[0] -= v
        acc[1] -= h
        return v, h

    def _grab_scroll(self, hand_pt: Tuple[float, float], config: Dict[str, Any], now: float = 0.0,
                     releasing: bool = False) -> None:
        """
        Touchscreen-style scrolling: pull the hand down -> the page content follows it down
        (scrolls up). 120 wheel units = one notch; smaller steps scroll smoothly in apps
        that support it (Chrome, Edge, Office, ...).
        """
        alpha = 0.5
        x, y = float(hand_pt[0]), float(hand_pt[1])
        if self._scroll_prev is None:
            self._stop_scroll_inertia()  # pinching again catches a coasting page
            self._flush_cursor()  # the page under the cursor gets the wheel events
            self._scroll_prev = self._scroll_anchor = (x, y)
            self._scroll_live = False
            self._scroll_acc = [0.0, 0.0]
            self._scroll_hist.clear()
            self._scroll_net = [0.0, 0.0]
            return
        if self._scroll_resume:
            # hand is back after a short loss: continue from where it is now (no jump)
            self._scroll_resume = False
            self._scroll_prev = (x, y)
            self._scroll_hist.clear()
            return
        px, py = self._scroll_prev
        sx, sy = px + alpha * (x - px), py + alpha * (y - py)  # light jitter smoothing
        self._scroll_prev = (sx, sy)
        if releasing:
            # Fingers are opening: freeze the page and keep the fling velocity measured
            # BEFORE the release started. (The hand springs back at the end of a flick; that
            # used to drag the page back and give the inertia the opposite direction.)
            return
        if not self._scroll_live:
            ax, ay = self._scroll_anchor
            if math.hypot(sx - ax, (sy - ay) * self.frame_aspect) < float(config.get("scroll_deadzone", 0.01)):
                return
            self._scroll_live = True  # past the deadzone: start scrolling from here on
            return
        gain = float(config.get("scroll_gain", 24000.0))
        sign = -1.0 if config.get("scroll_invert", False) else 1.0
        dy = (sy - py) * self.frame_aspect  # physical units (frame widths)
        dx = sx - px
        rv, rh = sign * dy * gain, sign * -dx * gain  # hand down -> wheel up; hand right -> scroll left
        self._scroll_acc[0] += rv
        self._scroll_acc[1] += rh
        self._scroll_net[0] += rv
        self._scroll_net[1] += rh
        # recent motion (last ~0.1 s) -> fling velocity on release
        self._scroll_hist.append((now, rv, rh))
        while len(self._scroll_hist) > 2 and now - self._scroll_hist[0][0] > 0.1:
            self._scroll_hist.popleft()
        self._send_scroll(*self._take_steps(self._scroll_acc))

    def _end_scroll(self, config: Optional[Dict[str, Any]] = None, now: float = 0.0, launch: bool = True) -> None:
        """Pinch released: a page that was still moving keeps coasting (inertia)."""
        cfg = config or {}
        self._scroll_resume = False
        if (
            launch
            and self._scroll_live
            and cfg.get("scroll_inertia", False)
            and len(self._scroll_hist) >= 2
        ):
            span = max(1e-3, self._scroll_hist[-1][0] - self._scroll_hist[0][0])
            vv = sum(r[1] for r in list(self._scroll_hist)[1:]) / span
            vh = sum(r[2] for r in list(self._scroll_hist)[1:]) / span
            # never coast against the direction the page was being moved overall
            same_way = vv * self._scroll_net[0] + vh * self._scroll_net[1] > 0
            if same_way and math.hypot(vv, vh) >= float(cfg.get("scroll_inertia_min_speed", 600.0)):
                self._inertia_v = [vv, vh]
                self._inertia_t = now
                self._inertia_acc = [0.0, 0.0]
        self._scroll_prev = self._scroll_anchor = None
        self._scroll_live = False
        self._scroll_acc = [0.0, 0.0]
        self._scroll_hist.clear()

    @property
    def scroll_coasting(self) -> bool:
        return self._inertia_v is not None

    def _stop_scroll_inertia(self) -> None:
        self._inertia_v = None

    def _tick_scroll_inertia(self, config: Dict[str, Any], now: float) -> None:
        """Exponential slow-down (frame-rate independent), like flicking a phone page."""
        if self._inertia_v is None:
            return
        tau = max(0.05, float(config.get("scroll_inertia_time", 0.4)))
        dt = max(0.0, min(0.1, now - self._inertia_t))
        self._inertia_t = now
        decay = math.exp(-dt / tau)
        vv, vh = self._inertia_v
        self._inertia_acc[0] += vv * tau * (1.0 - decay)
        self._inertia_acc[1] += vh * tau * (1.0 - decay)
        self._inertia_v = [vv * decay, vh * decay]
        self._send_scroll(*self._take_steps(self._inertia_acc))
        if math.hypot(*self._inertia_v) < 60.0:  # half a notch per second: done
            self._inertia_v = None

    # ------------------------------------------------------------------ double-click assist
    def _double_click_hold(self, result: GestureResult, config: Dict[str, Any], now: float) -> bool:
        """True while the cursor should stay still after a left click."""
        if now >= self._dbl_until or result.cursor_point is None:
            return False
        if result.active_gesture == "fist":
            self._dbl_until = -1e9
            return False
        tol = max(0.0, float(config.get("double_click_move_tolerance", 0.03)))
        if self._dbl_hand is None:
            self._dbl_hand = result.cursor_point
        dx = float(result.cursor_point[0]) - self._dbl_hand[0]
        dy = (float(result.cursor_point[1]) - self._dbl_hand[1]) * self.frame_aspect
        if math.hypot(dx, dy) > tol:
            self._dbl_until = -1e9  # clearly moving away: release the hold now
            self.anchor_hand_pos = None  # relative re-anchors here (no jump)
            return False                 # absolute simply resumes its mapping
        self.anchor_hand_pos = None
        return True

    # ------------------------------------------------------------------ record & fling
    @property
    def is_playing(self) -> bool:
        return self._play is not None

    def _start_grab(self, hand_pt: Tuple[float, float], now: float) -> None:
        """Pinch: stop any replay where it is, freeze the cursor and start recording."""
        self._cancel_playback()
        self.grab_anchor = (float(hand_pt[0]), float(hand_pt[1]))
        self.recorded_path = [(now, float(hand_pt[0]), float(hand_pt[1]))]
        self.grab_speed = 0.0
        self._after_grab = True  # absolute mapping re-bases on the next absolute frame

    def _grab_step(self, hand_pt: Tuple[float, float], config: Dict[str, Any], now: float) -> None:
        """Record the (lightly smoothed) pinch point path."""
        if self.grab_anchor is None:
            self._start_grab(hand_pt, now)
            return
        alpha = max(0.05, min(1.0, float(config.get("record_smoothing", 0.5))))
        t0, x0, y0 = self.recorded_path[-1]
        if now <= t0:
            return  # same frame that started the recording
        x = x0 + alpha * (float(hand_pt[0]) - x0)
        y = y0 + alpha * (float(hand_pt[1]) - y0)
        self.recorded_path.append((now, x, y))
        dt = now - t0
        if dt > 1e-4:
            self.grab_speed = math.hypot(x - x0, (y - y0) * self.frame_aspect) / dt

    def _build_replay(self, config: Dict[str, Any]) -> Optional[List[Tuple[float, float, float]]]:
        """Turn the recorded pinch path into a screen path (speed-scaled per segment)."""
        path = self.recorded_path
        if len(path) < 2:
            return None
        # Trim the tail: fingers separating at release shift the pinch point, and the
        # pinch release grace period keeps recording after the fingers actually opened
        trim = max(0.0, float(config.get("record_release_trim", 0.06))) + max(
            0.0, float(config.get("pinch_release_grace", 0.0))
        )
        t_end = path[-1][0] - trim
        trimmed = [pt for pt in path if pt[0] <= t_end]
        path = trimmed if len(trimmed) >= 2 else path[:2]

        strength = max(0.0, float(config.get("fling_strength", 1.0)))
        g_min = max(0.0, float(config.get("record_gain_min", 1.0)))
        g_max = max(g_min, float(config.get("record_gain_max", 4.0)))
        v_lo = max(0.0, float(config.get("record_speed_low", 0.2)))
        v_hi = max(v_lo + 1e-6, float(config.get("record_speed_high", 1.2)))
        gain_x = max(0.0, float(config.get("fling_gain_x", 1.0)))
        gain_y = max(0.0, float(config.get("fling_gain_y", 1.0)))
        min_len = max(0.0, float(config.get("record_min_length", 0.01)))

        total_len = 0.0
        x, y = float(self.last_screen_pos[0]), float(self.last_screen_pos[1])
        pts: List[Tuple[float, float, float]] = [(0.0, x, y)]
        for (ta, xa, ya), (tb, xb, yb) in zip(path, path[1:]):
            dx = xb - xa
            dy = (yb - ya) * self.frame_aspect  # physical units (frame widths)
            seg = math.hypot(dx, dy)
            total_len += seg
            dt = max(1e-4, tb - ta)
            t = max(0.0, min(1.0, (seg / dt - v_lo) / (v_hi - v_lo)))
            g = (g_min + (g_max - g_min) * t) * strength
            x = max(0.0, min(float(self.screen_w - 1), x + dx * self.screen_w * g * gain_x))
            y = max(0.0, min(float(self.screen_h - 1), y + dy * self.screen_w * g * gain_y))
            pts.append((tb - path[0][0], x, y))
        if total_len < min_len:
            return None

        # Replay timing: the recorded rhythm, fitted into [min, max] duration
        dur = pts[-1][0]
        d_min = max(0.02, float(config.get("replay_min_duration", 0.15)))
        d_max = max(d_min, float(config.get("replay_max_duration", 0.45)))
        target = max(d_min, min(d_max, dur))
        k = target / dur if dur > 1e-6 else 0.0
        pts = [(t * k, px, py) if dur > 1e-6 else (target * i / (len(pts) - 1), px, py)
               for i, (t, px, py) in enumerate(pts)]
        self.last_fling_distance = math.hypot(pts[-1][1] - pts[0][1], pts[-1][2] - pts[0][2])
        return pts

    def _end_grab(self, config: Dict[str, Any], now: float) -> None:
        """Pinch released (or hand lost): replay the recorded path."""
        if self.grab_anchor is not None:
            replay = self._build_replay(config)
            if replay is not None:
                self.fling_count += 1
                self.event_log.append("fling")
                self._start_playback(replay, now)
        self.grab_anchor = None
        self.recorded_path = []
        self.grab_speed = 0.0

    # ---- replay animation
    def _playback_pos(self, now: float) -> Tuple[float, float, bool]:
        pts = self._play or []
        t = now - self._play_t0
        if not pts or t >= pts[-1][0]:
            return (pts[-1][1], pts[-1][2], True) if pts else (*self.last_screen_pos, True)
        for (ta, xa, ya), (tb, xb, yb) in zip(pts, pts[1:]):
            if t <= tb:
                f = 0.0 if tb - ta < 1e-9 else (t - ta) / (tb - ta)
                return xa + (xb - xa) * f, ya + (yb - ya) * f, False
        return pts[-1][1], pts[-1][2], True

    def _start_playback(self, pts: List[Tuple[float, float, float]], now: float) -> None:
        with self._play_lock:
            self._play_gen += 1
            self._play = pts
            self._play_t0 = time.perf_counter() if self.smooth_playback else now
            gen = self._play_gen
        if self.smooth_playback:
            threading.Thread(target=self._playback_thread, args=(gen,), daemon=True).start()

    def _playback_thread(self, gen: int) -> None:
        """~240 Hz cursor animation so the travelled line is clearly visible."""
        while True:
            with self._play_lock:
                if self._play is None or gen != self._play_gen:
                    return
                if self._tick_playback_locked(time.perf_counter()):
                    return
            time.sleep(1 / 240)

    def _tick_playback_locked(self, now: float) -> bool:
        x, y, done = self._playback_pos(now)
        self._move_os_cursor(int(round(x)), int(round(y)), immediate=True)
        if done:
            self._play = None
            self._smoothed_pos = (x, y)
            self.anchor_hand_pos = None  # relative mode re-anchors at the landing point
            self._after_grab = True
        return done

    def _tick_playback(self, now: float) -> None:
        """Main-loop tick (dry_run / tests drive the animation through here)."""
        if self._play is None or self.smooth_playback:
            return
        with self._play_lock:
            self._tick_playback_locked(now)

    def _cancel_playback(self) -> None:
        with self._play_lock:
            if self._play is not None:
                self._play = None
                self._play_gen += 1
                self._smoothed_pos = (float(self.last_screen_pos[0]), float(self.last_screen_pos[1]))
                self.anchor_hand_pos = None

    def hand_lost(self, config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> Tuple[int, int]:
        """
        Call on frames without a hand: releases buttons; a recording in progress is
        replayed (fast flicks often blur the hand out of detection).
        """
        cfg = config or {}
        now = time.perf_counter() if now is None else float(now)
        if self.switcher_open:
            self._run_switcher_pending(now, cfg)
            if self.switcher_open and now - self._sw_seen_t >= float(cfg.get("switcher_lost_confirm", 1.0)):
                self.close_switcher(confirm=True)
            return self.last_screen_pos
        self._end_grab(cfg, now)
        # Short losses (a fast hand blurs out of detection) are NOT a release: a grabbed page
        # stays grabbed (no inertia, scrolling resumes without a jump) and a long-press drag
        # keeps holding the file. Only a longer loss ends them (without inertia).
        if self._lost_since is None:
            self._lost_since = now
        lost_for = now - self._lost_since
        if lost_for >= float(cfg.get("hand_lost_reset", 0.3)):
            if self._scroll_prev is not None:
                self._end_scroll(cfg, now, launch=False)
            if not self.is_playing:
                self._mouse_up()
            self._prev_action = "none"
        else:
            self._scroll_resume = self._scroll_prev is not None
        self._tick_scroll_inertia(cfg, now)
        self.anchor_hand_pos = None
        self.anchor_screen_pos = None
        self._tick_playback(now)
        return self.last_screen_pos

    def _move_os_cursor(self, x: int, y: int, immediate: bool = False) -> None:
        self.last_screen_pos = (x, y)
        if self.dry_run:
            return
        if self._interp is not None:
            if self.interpolation and not immediate:
                self._interp.set_target(x, y, time.perf_counter())
            else:
                self._interp.snap(x, y)
        else:
            pyautogui.moveTo(x, y, _pause=False)

    def _flush_cursor(self) -> None:
        """Put the real cursor exactly on the target before a click / button change."""
        if not self.dry_run and self._interp is not None:
            self._interp.snap(*self.last_screen_pos)

    def close(self) -> None:
        if self._interp is not None:
            self._interp.close()

    def _mouse_down(self) -> None:
        if not self.is_dragging:
            self.is_dragging = True
            self.event_log.append("mouseDown")
            if not self.dry_run:
                self._flush_cursor()
                pyautogui.mouseDown(button="left", _pause=False)

    def _mouse_up(self) -> None:
        if self.is_dragging:
            self.is_dragging = False
            self._hold_dragging = False
            self._toggle_dragging = False
            self.event_log.append("mouseUp")
            if not self.dry_run:
                self._flush_cursor()
                pyautogui.mouseUp(button="left", _pause=False)

    def release_all(self) -> None:
        """Safely release any held mouse buttons / keys and reset relative anchors."""
        self.close_switcher(confirm=True)
        self._mouse_up()
        self._hold_dragging = False
        self._toggle_dragging = False
        self.anchor_hand_pos = None
        self.anchor_screen_pos = None
        self.grab_anchor = None
        self.recorded_path = []
        self._cancel_playback()
        self._prev_action = "none"
        self._pointer_source = None
        self._tremor.reset()

    def apply(
        self,
        result: GestureResult,
        config: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
        *,
        index_tip: Optional[Tuple[float, float]] = None,
        index_sensitivity: float = 1.0,
        pointer_source: str = "index",
    ) -> Tuple[int, int]:
        """
        Execute the action described in `result` and return `(screen_x, screen_y)`.
        """
        cfg = config or {}
        action = result.action
        now = time.perf_counter() if now is None else float(now)
        self._lost_since = None
        self.interpolation = bool(cfg.get("cursor_interpolation", False))
        self.dpi_multiplier = float(getattr(result, "dpi_multiplier", 1.0) or 1.0)
        if not result.is_moving:
            self._abs_prev = None

        # Task switcher open: gestures navigate / confirm / cancel instead of moving
        if self.switcher_open:
            self._prev_action = action
            self._switcher_apply(result, cfg, now)
            return self.last_screen_pos
        if action == "task_switcher":
            self._prev_action = action
            self.open_switcher(now)
            return self.last_screen_pos

        entering_grab = action == "grab" and self._prev_action != "grab"
        if action != "grab" and self._prev_action == "grab":
            self._end_grab(cfg, now)
        if action != "scroll_grab":
            if self._scroll_prev is not None:
                self._end_scroll(cfg, now)  # released: maybe start coasting
            if action in ("left_click", "right_click", "drag_hold", "drag_toggle", "task_switcher"):
                self._stop_scroll_inertia()
            self._tick_scroll_inertia(cfg, now)
        if (index_tip is not None and action == "pointer_wait"
                and self._prev_action == "move_cursor"):
            self._flush_cursor()
        self._prev_action = action

        # If drag_hold was active in previous frame(s) and is no longer active, release mouse button
        if self._hold_dragging and action != "drag_hold":
            self._mouse_up()

        # When not in hand-following movement, clear relative clutch anchor
        if not result.is_moving or action == "grab":
            self._tremor.reset()
            self.anchor_hand_pos = None
            self.anchor_screen_pos = None
            if str(cfg.get("movement_mode", "relative")).lower() == "relative":
                self._smoothed_pos = None

        # Record & fling tracks the pinch point (fingertips move most in a wrist flick)
        grab_pt = result.pinch_point if result.pinch_point is not None else result.cursor_point

        # A new pinch stops any replay in place and starts recording
        if entering_grab and grab_pt is not None:
            self._start_grab(grab_pt, now)

        if action == "scroll_grab":
            if result.cursor_point is not None:
                self._grab_scroll(result.cursor_point, cfg, now, releasing=getattr(result, "releasing", False))
        elif action == "grab":
            # Recording: cursor stays put
            if grab_pt is not None:
                self._grab_step(grab_pt, cfg, now)
        elif self.is_playing:
            # Replaying the recorded path: hand movement is ignored until it finishes
            self._tick_playback(now)
        elif (
            index_tip is None and result.is_moving and result.cursor_point is not None
            and self._double_click_hold(result, cfg, now)
        ):
            pass  # cursor held for a possible second click
        elif result.is_moving and result.cursor_point is not None:
            use_tip = index_tip is not None and action == "move_cursor" and result.active_gesture != "fist"
            source = pointer_source if use_tip else None
            if source != self._pointer_source:
                self.anchor_hand_pos = None
                self._tremor.reset()
                self._pointer_source = source
            if use_tip:
                if self.anchor_hand_pos is None and not self.dry_run:
                    x, y = pyautogui.position()
                    self.last_screen_pos = (x, y)
                min_gain, max_gain = (.85, 2.5) if source == "palm" else (1.5, 5.0)
                pointer_config = {
                    **cfg, "movement_mode": "relative",
                    "tremor_filter": True, "tremor_min_cutoff": 2.0, "tremor_beta": 7.0,
                    "relative_gain_min": min_gain * index_sensitivity,
                    "relative_gain_max": max_gain * index_sensitivity,
                    "relative_accel_speed_low": .08, "relative_accel_speed_high": .40,
                }
                target_x, target_y = self._compute_target_screen_pos(index_tip, pointer_config, now)
            else:
                target_x, target_y = self._compute_target_screen_pos(result.cursor_point, cfg, now)
            if action == "drag_hold":
                self._hold_dragging = True
                self._mouse_down()
            self._move_os_cursor(target_x, target_y)

        # Handle discrete actions
        if action == "left_click":
            if self.is_dragging:
                self._mouse_up()
            self.event_log.append("left_click")
            self._dbl_until = (
                now + max(0.0, float(cfg.get("double_click_window", 0.45)))
                if index_tip is None else now
            )
            self._dbl_hand = result.cursor_point
            if not self.dry_run:
                self._flush_cursor()
                pyautogui.click(button="left", _pause=False)

        elif action == "right_click":
            self.event_log.append("right_click")
            if not self.dry_run:
                self._flush_cursor()
                pyautogui.click(button="right", _pause=False)

        elif action == "show_desktop":
            # Windows "show desktop" (minimize / restore all windows): Win + D
            if self.is_dragging:
                self._mouse_up()
            self.event_log.append("show_desktop")
            if not self.dry_run:
                pyautogui.keyDown("win", _pause=False)
                try:
                    pyautogui.press("d", _pause=False)
                finally:
                    pyautogui.keyUp("win", _pause=False)

        elif action == "alt_tab":
            # Windows window switch: hold Alt, tap Tab, release Alt
            if self.is_dragging:
                self._mouse_up()
            self.event_log.append("alt_tab")
            if not self.dry_run:
                pyautogui.keyDown("alt", _pause=False)
                try:
                    pyautogui.press("tab", _pause=False)
                finally:
                    pyautogui.keyUp("alt", _pause=False)

        elif action == "drag_toggle":
            if self.is_dragging:
                self._mouse_up()
            else:
                self._toggle_dragging = True
                self._mouse_down()

        return self.last_screen_pos
