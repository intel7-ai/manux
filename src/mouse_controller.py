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

import dataclasses
import math
import os
import sys
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import pyautogui

try:
    from src.cursor_interp import CursorInterpolator
    from src.filters import TremorStabilizer
    from src.gesture_engine import GestureResult
    from src.palm_axes import apply_axes
    from src.path_smoother import PathSmoother
    from src.screens import Monitor, ScreenLayout
except ImportError:
    from cursor_interp import CursorInterpolator
    from filters import TremorStabilizer
    from gesture_engine import GestureResult
    from palm_axes import apply_axes
    from path_smoother import PathSmoother
    from screens import Monitor, ScreenLayout

# Configure PyAutoGUI for zero-latency real-time control
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


class ScrollPump:
    """Spreads wheel amounts over time.

    The camera delivers ~30 updates a second; sent as they come, each one is a burst of 1-4 notches and
    the page steps (replay of a 2026-11-04 session: median event 130 units, p90 270, a third of the frames
    with none). The pump takes the amounts into a pending pool and hands out a share on every tick
    (`1 - exp(-dt / tau)`), so a thread ticking every ~8 ms produces a steady stream of small events.
    Totals are preserved; the added delay is about `tau` (0.03 s)."""

    def __init__(self, tau: float = 0.03) -> None:
        self.tau = tau
        self._pending = [0.0, 0.0]
        self._carry = [0.0, 0.0]
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def add(self, v: float, h: float) -> None:
        with self._lock:
            self._pending[0] += v
            self._pending[1] += h

    def clear(self) -> None:
        """Drop what has not been sent yet (the user caught the page)."""
        with self._lock:
            self._pending = [0.0, 0.0]
            self._carry = [0.0, 0.0]

    def pending(self) -> Tuple[float, float]:
        with self._lock:
            return self._pending[0], self._pending[1]

    def step(self, now: float, tau: Optional[float] = None) -> Tuple[int, int]:
        tau = self.tau if tau is None else tau
        dt = 0.0 if self._last is None else max(0.0, min(0.1, now - self._last))
        self._last = now
        share = 1.0 if tau <= 0 else 1.0 - math.exp(-dt / tau)
        with self._lock:
            take = [self._pending[0] * share, self._pending[1] * share]
            self._pending[0] -= take[0]
            self._pending[1] -= take[1]
            self._carry[0] += take[0]
            self._carry[1] += take[1]
            v, h = int(self._carry[0]), int(self._carry[1])
            self._carry[0] -= v
            self._carry[1] -= h
        return v, h

    def flush(self) -> Tuple[int, int]:
        with self._lock:
            v = int(round(self._pending[0] + self._carry[0]))
            h = int(round(self._pending[1] + self._carry[1]))
            self._pending = [0.0, 0.0]
            self._carry = [0.0, 0.0]
        return v, h



class MouseController:
    """Controls the OS mouse cursor based on GestureResult and gesture configuration."""

    def __init__(
        self,
        dry_run: bool = False,
        screen_size: Optional[Tuple[int, int]] = None,
        screens: Optional[ScreenLayout] = None,
    ) -> None:
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0

        self.dry_run: bool = dry_run
        if screens is None:
            if screen_size is not None:
                screens = ScreenLayout.single(int(screen_size[0]), int(screen_size[1]))
            else:
                try:
                    sz = pyautogui.size()
                    fallback = (int(sz[0]), int(sz[1]))
                except Exception:
                    fallback = (1920, 1080)
                screens = ScreenLayout.detect(fallback)
        # All monitors (cursor bounds). screen_w / screen_h stay the PRIMARY monitor's size:
        # they scale hand motion into pixels, so a second monitor does not double the speed.
        self.screens: ScreenLayout = screens
        self.screen_w, self.screen_h = screens.primary.width, screens.primary.height
        # Absolute mode maps the camera onto one monitor at a time (see cycle_monitor)
        self._abs_monitor: Optional[Monitor] = None

        self.last_screen_pos: Tuple[int, int] = screens.primary.center
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
        self._scroll_net = [0.0, 0.0]  # total wheel amount of the current grab
        self._scroll_recent: Deque[Tuple[float, float, float]] = deque()  # last ~0.4 s (direction check)
        self.last_inertia: Optional[Dict[str, Any]] = None  # numbers of the latest launch (failure log)
        self.inertia_launches = 0
        self._pump = ScrollPump()
        # Page scrolling keeps the cursor in the middle of the window it is browsing (see _begin_browse)
        self.window_probe: Optional[Callable[[int, int], Optional[Tuple[int, int, str]]]] = None
        self.scroll_target_finder: Any = None      # src.scroll_target.ScrollTargetFinder: the scroll region under the cursor
        self._browse_centered = False     # the current browsing session was centred: the cursor stays put
        self._browse_until = -1e9         # ... until this time (extended while grabbing / coasting)
        self.browse_target: Optional[Tuple[int, int, str]] = None     # (x, y, region|page|window|kept|series) of the last browse
        # Continuous browsing: the cursor goes back to where the first grab of the series started
        self._series_anchor: Optional[Tuple[int, int]] = None
        self._series_last = -1e9          # last time the series was active (grab / coast / lock)
        self._clock = 0.0                 # time of the frame being applied (stamps cursor moves)
        self._cursor_moved_at = -1e9      # last time the cursor moved by more than a couple of pixels
        self._provisional = False       # coasting already started while the fingers are still opening
        self._pump_thread: Optional[threading.Thread] = None
        self._pump_stop = threading.Event()
        self._scroll_tau = 0.03
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
        self._rel_carry: Tuple[float, float] = (0.0, 0.0)   # cursor distance held back by cursor_max_step, paid out in the next frames
        self.last_close_info: Dict[str, Any] = {}      # the window the last Alt+F4 request met (see foreground_info)
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
        self.hand_size: Optional[float] = None  # wrist -> longest finger base / frame width, set per frame by the runtime
        self._size_ema: Optional[float] = None
        self._size_t: float = 0.0
        self.distance_scale: float = 1.0        # cursor gain factor for a far (small) hand
        self._path: Optional[PathSmoother] = None   # clean strokes for the fist cursor (src/path_smoother.py)
        # (x, y) -> speed multiplier in (0, 1]; set by main.py (UITargetMagnet.factor)
        self.slowdown_provider: Optional[Callable[[int, int], float]] = None

        # Velocity ballistics & return-stroke suppression state
        self._vel_ema: float = 0.0
        self._effective_gain: float = 0.55
        self._raw_hand_prev: Optional[Tuple[float, float, float]] = None
        self._raw_hand_speed: float = 0.0
        self._raw_hand_delta: Tuple[float, float] = (0.0, 0.0)
        self._prev_smoothed_speed: float = 0.0
        self._peak_recent_speed: float = 0.0
        self._peak_recent_t: float = -1e9
        self._c2f_until: float = -1e9
        self._edge_touched_at: float = -1e9
        self._edge_dir: Tuple[float, float] = (0.0, 0.0)
        self._edge_reclutch_active: bool = False
        self._clutch_size_ema: Optional[float] = None
        self._clutch_size_buf: Deque[float] = deque(maxlen=90)
        self._clutch_entered_at: float = -1e9
        self._clutch_exit_cand_since: Optional[float] = None
        self._ret_fwd_start: Optional[Tuple[float, float]] = None
        self._ret_fwd_end: Optional[Tuple[float, float]] = None
        self._ret_fwd_peak_speed: float = 0.0
        self._ret_fwd_end_t: float = -1e9
        self._ret_armed_vec: Optional[Tuple[float, float]] = None
        self._ret_armed_start: Optional[Tuple[float, float]] = None
        self._ret_armed_peak: float = 0.0
        self._ret_armed_t: float = -1e9
        self._ret_pending_t0: float = -1e9
        self._ret_pending_start_pt: Optional[Tuple[float, float]] = None
        self._ret_pending_steps: List[Tuple[float, float]] = []
        self._ret_pending_peak_speed: float = 0.0
        self._ret_confirmed_at: float = -1e9
        self._ret_vec: Tuple[float, float] = (0.0, 0.0)
        self._ret_pause_since: Optional[float] = None
        self._ret_fwd_relaunch_frames: int = 0
        self.hand_shape_mode: str = "normal"  # "precise" | "normal" | "fast"
        self.clutch_state: str = "inactive"   # "inactive" | "active"
        self.return_state: str = "none"       # "none" | "pending" | "confirmed" | "released"

        self.event_log: List[str] = []

    @property
    def c2f_active(self) -> bool:
        """True while post-flick coarse-to-fine deceleration assist is active."""
        return self._clock < self._c2f_until

    def _update_depth_clutch(
        self,
        result: GestureResult,
        config: Dict[str, Any],
        now: float,
    ) -> bool:
        """
        Layer 1 Explicit Depth / Hand-Size Clutch (`return_suppression` in {"clutch", "clutch_auto"}).
        - Freezes cursor movement when EMA-smoothed `hand_size` drops below `clutch_depth_enter_ratio`
          relative to the recent moving-active median (or calibrated `calibrated_hand_size`).
        - Resumes cursor movement without a jump when `hand_size` rises above `clutch_depth_exit_ratio`
          (hysteresis) and is held for at least `clutch_min_hold` (~0.15s).
        - Never activates or stays active during drag (`drag_hold` / `is_dragging`), scroll, or `browse_lock`.
        """
        ret_mode = str(config.get("return_suppression", "off")).lower()
        if ret_mode not in ("clutch", "clutch_auto"):
            self.clutch_state = "inactive"
            self._clutch_exit_cand_since = None
            return False

        if (
            result.action in ("drag_hold", "drag_toggle")
            or self.is_dragging
            or self._scroll_prev is not None
            or self.scroll_coasting
            or self._browse_centered
        ):
            if self.clutch_state == "active":
                self.clutch_state = "inactive"
                self._clutch_exit_cand_since = None
            return False

        dists = getattr(result, "distances", None) or {}
        raw_size = float(dists.get("hand_size", 0.0) or 0.0)
        if raw_size <= 1e-6:
            return self.clutch_state == "active"

        alpha_s = max(0.05, min(1.0, float(config.get("clutch_size_ema_alpha", 0.30))))
        self._clutch_size_ema = (
            raw_size
            if self._clutch_size_ema is None
            else self._clutch_size_ema + alpha_s * (raw_size - self._clutch_size_ema)
        )
        cur_s = self._clutch_size_ema

        cal_size = float(config.get("calibrated_hand_size", 0.0) or 0.0)
        if len(self._clutch_size_buf) >= 5:
            sorted_buf = sorted(self._clutch_size_buf)
            ref_size = sorted_buf[len(sorted_buf) // 2]
        elif cal_size > 1e-4:
            ref_size = cal_size
        else:
            ref_size = cur_s

        enter_r = max(0.50, min(0.96, float(config.get("clutch_depth_enter_ratio", 0.80))))
        exit_r = max(enter_r + 0.01, min(0.99, float(config.get("clutch_depth_exit_ratio", 0.86))))
        min_hold = max(0.0, float(config.get("clutch_min_hold", 0.15)))
        max_hold = max(min_hold + 0.2, float(config.get("clutch_max_hold", 1.20)))
        size_ratio = cur_s / max(1e-6, ref_size)
        has_ref = len(self._clutch_size_buf) >= 5 or cal_size > 1e-4

        if self.clutch_state == "inactive":
            if has_ref and size_ratio <= enter_r:
                self.clutch_state = "active"
                self._clutch_entered_at = now
                self._clutch_exit_cand_since = None
                self.anchor_hand_pos = None
                self._rel_carry = (0.0, 0.0)
                self._tremor.reset()
                if self._path is not None:
                    self._path.reset()
                self._after_grab = True
                return True
            if result.is_moving:
                self._clutch_size_buf.append(cur_s)
            return False

        # Auto re-baseline if hand remains at a new farther working plane beyond `clutch_max_hold`
        if (now - self._clutch_entered_at) >= max_hold:
            self.clutch_state = "inactive"
            self._clutch_exit_cand_since = None
            self._clutch_size_buf.clear()
            for _ in range(8):
                self._clutch_size_buf.append(cur_s)
            self.anchor_hand_pos = None
            self._tremor.reset()
            return False

        # While clutch_state == "active": keep relative anchor and PathSmoother cleared
        self.anchor_hand_pos = None
        self._rel_carry = (0.0, 0.0)
        if self._path is not None:
            self._path.reset()
        self._after_grab = True

        if size_ratio >= exit_r:
            if self._clutch_exit_cand_since is None:
                self._clutch_exit_cand_since = now
            if (now - self._clutch_entered_at) >= min_hold and (now - self._clutch_exit_cand_since) >= min_hold:
                self.clutch_state = "inactive"
                self._clutch_exit_cand_since = None
                self.anchor_hand_pos = None
                self._tremor.reset()
                return False
        else:
            self._clutch_exit_cand_since = None

        return True

    def _velocity_mode_bounds(self, config: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return `(gain_min, gain_mid, gain_max)` for the active discrete `hand_shape_mode`."""
        mode = self.hand_shape_mode
        if mode in ("precise", "open"):
            g_min = float(config.get("velocity_mode_precise_min", 0.40))
            g_mid = float(config.get("velocity_mode_precise_mid", 1.8))
            g_max = float(config.get("velocity_mode_precise_max", 4.0))
        elif mode in ("fast", "fist"):
            g_min = float(config.get("velocity_mode_fast_min", 0.75))
            g_mid = float(config.get("velocity_mode_fast_mid", 3.8))
            g_max = float(config.get("velocity_mode_fast_max", 9.0))
        else:
            g_min = float(config.get("velocity_gain_min", 0.55))
            g_mid = float(config.get("velocity_gain_mid", 2.8))
            g_max = float(config.get("velocity_gain_max", 6.5))
        g_min = max(0.05, g_min)
        g_mid = max(g_min, g_mid)
        g_max = max(g_mid, g_max)
        return g_min, g_mid, g_max

    def _reset_velocity_state(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        g_min = max(0.05, float(cfg.get("velocity_gain_min", 0.55)))
        self._vel_ema = 0.0
        self._effective_gain = g_min
        self._raw_hand_prev = None
        self._raw_hand_speed = 0.0
        self._raw_hand_delta = (0.0, 0.0)
        self._prev_smoothed_speed = 0.0
        self._peak_recent_speed = 0.0
        self._peak_recent_t = -1e9
        self._c2f_until = -1e9
        self._edge_touched_at = -1e9
        self._edge_dir = (0.0, 0.0)
        self._edge_reclutch_active = False
        self._clutch_size_ema = None
        self._clutch_size_buf.clear()
        self._clutch_entered_at = -1e9
        self._clutch_exit_cand_since = None
        self._ret_fwd_start = None
        self._ret_fwd_end = None
        self._ret_fwd_peak_speed = 0.0
        self._ret_fwd_end_t = -1e9
        self._ret_armed_vec = None
        self._ret_armed_start = None
        self._ret_armed_peak = 0.0
        self._ret_armed_t = -1e9
        self._ret_pending_t0 = -1e9
        self._ret_pending_start_pt = None
        self._ret_pending_steps.clear()
        self._ret_pending_peak_speed = 0.0
        self._ret_confirmed_at = -1e9
        self._ret_vec = (0.0, 0.0)
        self._ret_pause_since = None
        self._ret_fwd_relaunch_frames = 0
        self.clutch_state = "inactive"
        self.return_state = "none"
        self.hand_shape_mode = "normal"

    def _update_auto_return_suppression(
        self,
        sx: float,
        sy: float,
        raw_dx: float,
        raw_dy: float,
        step_x: float,
        step_y: float,
        dt: float,
        now: float,
        config: Dict[str, Any],
        prev_vel_ema: float,
        prev_eff_gain: float,
    ) -> Tuple[float, float]:
        """
        Layer 2 Conservative Automatic Return-Stroke Suppression (`return_suppression == "clutch_auto"`).
        State machine (`self.return_state`): `"none"` -> `"pending"` (2–3 frame / ~85ms deferred-commit buffer)
        -> `"confirmed"` (cursor frozen, buffer discarded) -> `"released"` (re-anchored with 0 jump).
        If a `"pending"` candidate stops short or turns back into forward motion, all buffered steps are
        flushed so fine aiming loses 0 pixels of displacement.
        Never active during drag, scroll, or `browse_lock`.
        """
        ret_mode = str(config.get("return_suppression", "off")).lower()
        if (
            ret_mode != "clutch_auto"
            or self._pointer_source is not None
            or self.is_dragging
            or self._scroll_prev is not None
            or self.scroll_coasting
            or self._browse_centered
        ):
            if self._ret_pending_steps:
                self._ret_pending_steps.clear()
            self.return_state = "none"
            return step_x, step_y

        cur_pt = (sx, sy * self.frame_aspect)
        prev_pt = (cur_pt[0] - raw_dx, cur_pt[1] - raw_dy)
        step_dist = math.hypot(raw_dx, raw_dy)
        step_speed = step_dist / max(1e-3, dt)
        v_still = max(0.02, float(config.get("velocity_stationary_speed", 0.05)))
        min_fwd_dist = max(0.02, float(config.get("return_auto_min_fwd_dist", 0.035)))
        min_fwd_speed = max(0.05, float(config.get("return_auto_min_fwd_speed", 0.16)))
        post_win = max(0.15, float(config.get("return_auto_post_window", 0.65)))
        min_ang_deg = max(95.0, min(175.0, float(config.get("return_auto_min_angle_deg", 120.0))))
        cos_thresh = math.cos(math.radians(min_ang_deg))
        buf_win = max(0.03, min(0.18, float(config.get("return_auto_buffer_s", 0.085))))
        confirm_dist = max(0.008, float(config.get("return_auto_confirm_dist", 0.020)))
        min_spd_ratio = max(0.05, float(config.get("return_auto_min_speed_ratio", 0.15)))
        rel_pause = max(0.05, float(config.get("return_auto_release_pause", 0.15)))
        max_freeze = max(0.30, float(config.get("return_auto_max_freeze", 1.10)))
        center_pt = (0.5, 0.5 * self.frame_aspect)

        def _suppress_ballistics_and_path() -> None:
            self._vel_ema = prev_vel_ema * 0.85
            self._effective_gain = prev_eff_gain
            self.dpi_multiplier = prev_eff_gain
            self._rel_carry = (0.0, 0.0)
            if self._path is not None:
                self._path.reset()

        # 1. Handle CONFIRMED return stroke freeze
        if self.return_state == "confirmed":
            _suppress_ballistics_and_path()
            if self._ret_pending_start_pt is not None:
                self._ret_vec = (
                    cur_pt[0] - self._ret_pending_start_pt[0],
                    cur_pt[1] - self._ret_pending_start_pt[1],
                )
            release_now = False
            release_with_step = False

            if (now - self._ret_confirmed_at) >= max_freeze:
                release_now = True
            elif step_speed < v_still or self._raw_hand_speed < v_still:
                if self._ret_pause_since is None:
                    self._ret_pause_since = now
                if (now - self._ret_pause_since) >= rel_pause:
                    release_now = True
            else:
                self._ret_pause_since = None
                rvx, rvy = self._ret_vec
                rmag = math.hypot(rvx, rvy)
                if rmag > 1e-4 and step_dist > 1e-4:
                    cos_relaunch = (raw_dx * rvx + raw_dy * rvy) / (step_dist * rmag)
                    if cos_relaunch <= -0.15 and step_speed >= v_still * 1.2:
                        self._ret_fwd_relaunch_frames += 1
                        if self._ret_fwd_relaunch_frames >= 2:
                            release_now = True
                            release_with_step = True
                    else:
                        self._ret_fwd_relaunch_frames = 0

            if release_now:
                self.return_state = "released"
                self._ret_armed_vec = None
                self._ret_fwd_start = cur_pt
                self._ret_fwd_end = cur_pt
                self._ret_fwd_peak_speed = step_speed if release_with_step else 0.0
                self._ret_fwd_end_t = now
                self._ret_pending_steps.clear()
                self._ret_pause_since = None
                self._ret_fwd_relaunch_frames = 0
                return (step_x, step_y) if release_with_step else (0.0, 0.0)
            return 0.0, 0.0

        # 2. Handle PENDING deferred-commit buffer (~80ms / 2-3 frames)
        if self.return_state == "pending":
            self._ret_pending_steps.append((step_x, step_y))
            self._ret_pending_peak_speed = max(self._ret_pending_peak_speed, step_speed)
            p_start = self._ret_pending_start_pt or prev_pt
            cdx = cur_pt[0] - p_start[0]
            cdy = cur_pt[1] - p_start[1]
            cdist = math.hypot(cdx, cdy)
            elapsed_p = now - self._ret_pending_t0

            fvec = self._ret_armed_vec
            fstart = self._ret_armed_start
            if fvec is None or fstart is None:
                flushed_x = sum(s[0] for s in self._ret_pending_steps)
                flushed_y = sum(s[1] for s in self._ret_pending_steps)
                self._ret_pending_steps.clear()
                self.return_state = "none"
                return flushed_x, flushed_y

            fmag = max(1e-6, math.hypot(fvec[0], fvec[1]))
            cos_cand = (fvec[0] * cdx + fvec[1] * cdy) / (fmag * max(1e-6, cdist)) if cdist > 1e-5 else -1.0
            d_start_before = math.hypot(p_start[0] - fstart[0], p_start[1] - fstart[1])
            d_start_after = math.hypot(cur_pt[0] - fstart[0], cur_pt[1] - fstart[1])
            d_cent_before = math.hypot(p_start[0] - center_pt[0], p_start[1] - center_pt[1])
            d_cent_after = math.hypot(cur_pt[0] - center_pt[0], cur_pt[1] - center_pt[1])
            toward_origin_or_center = (d_start_after < d_start_before) or (d_cent_after < d_cent_before)

            # Abort pending & flush buffered steps if motion turned forward or stopped as a tiny micro-adjustment
            if cos_cand > -0.20 or (
                (step_speed < v_still or elapsed_p > buf_win * 1.4)
                and cdist < confirm_dist * 0.65
            ):
                flushed_x = sum(s[0] for s in self._ret_pending_steps)
                flushed_y = sum(s[1] for s in self._ret_pending_steps)
                self._ret_pending_steps.clear()
                self.return_state = "none"
                return flushed_x, flushed_y

            spd_ratio = self._ret_pending_peak_speed / max(0.10, self._ret_armed_peak)
            is_confirmed = (
                cos_cand <= cos_thresh
                and toward_origin_or_center
                and spd_ratio >= min_spd_ratio
                and (
                    cdist >= confirm_dist
                    or (elapsed_p >= buf_win and cdist >= confirm_dist * 0.65 and len(self._ret_pending_steps) >= 2)
                )
            )
            if is_confirmed:
                _suppress_ballistics_and_path()
                self.return_state = "confirmed"
                self._ret_confirmed_at = now
                self._ret_vec = (cdx, cdy)
                self._ret_pause_since = None
                self._ret_fwd_relaunch_frames = 0
                self._ret_pending_steps.clear()  # Discard buffered return steps!
                return 0.0, 0.0

            if elapsed_p > buf_win * 1.6:
                # Did not meet confirmation criteria within buffer window -> flush without loss
                flushed_x = sum(s[0] for s in self._ret_pending_steps)
                flushed_y = sum(s[1] for s in self._ret_pending_steps)
                self._ret_pending_steps.clear()
                self.return_state = "none"
                return flushed_x, flushed_y

            _suppress_ballistics_and_path()
            return 0.0, 0.0

        # 3. State is "none" or "released"
        if self.return_state == "released":
            self.return_state = "none"

        # Check if an armed forward stroke sees a qualifying reversal step
        if (
            self._ret_armed_vec is not None
            and self._ret_armed_start is not None
            and (now - self._ret_armed_t) <= post_win
            and step_speed >= max(0.04, v_still)
            and step_dist > 1e-4
        ):
            fvec = self._ret_armed_vec
            fstart = self._ret_armed_start
            fmag = max(1e-6, math.hypot(fvec[0], fvec[1]))
            cos_step = (fvec[0] * raw_dx + fvec[1] * raw_dy) / (fmag * step_dist)
            d_start_before = math.hypot(prev_pt[0] - fstart[0], prev_pt[1] - fstart[1])
            d_start_after = math.hypot(cur_pt[0] - fstart[0], cur_pt[1] - fstart[1])
            d_cent_before = math.hypot(prev_pt[0] - center_pt[0], prev_pt[1] - center_pt[1])
            d_cent_after = math.hypot(cur_pt[0] - center_pt[0], cur_pt[1] - center_pt[1])
            if cos_step <= cos_thresh and ((d_start_after < d_start_before) or (d_cent_after < d_cent_before)):
                _suppress_ballistics_and_path()
                self.return_state = "pending"
                self._ret_pending_t0 = now
                self._ret_pending_start_pt = prev_pt
                self._ret_pending_steps = [(step_x, step_y)]
                self._ret_pending_peak_speed = step_speed
                return 0.0, 0.0

        # Track active forward stroke
        if step_speed >= 0.04:
            if self._ret_fwd_start is None or (now - self._ret_fwd_end_t) > 0.25:
                self._ret_fwd_start = prev_pt
                self._ret_fwd_peak_speed = step_speed
            else:
                cur_fdx = cur_pt[0] - self._ret_fwd_start[0]
                cur_fdy = cur_pt[1] - self._ret_fwd_start[1]
                cur_fdist = math.hypot(cur_fdx, cur_fdy)
                if cur_fdist >= 0.025 and step_dist > 0.002:
                    dot = (cur_fdx * raw_dx + cur_fdy * raw_dy) / (cur_fdist * step_dist)
                    if dot < -0.25:
                        self._ret_fwd_start = prev_pt
                        self._ret_fwd_peak_speed = step_speed
                self._ret_fwd_peak_speed = max(self._ret_fwd_peak_speed, step_speed)

            self._ret_fwd_end = cur_pt
            self._ret_fwd_end_t = now
            fdx = cur_pt[0] - self._ret_fwd_start[0]
            fdy = cur_pt[1] - self._ret_fwd_start[1]
            fdist = math.hypot(fdx, fdy)
            if fdist >= min_fwd_dist and self._ret_fwd_peak_speed >= min_fwd_speed:
                self._ret_armed_vec = (fdx, fdy)
                self._ret_armed_start = self._ret_fwd_start
                self._ret_armed_peak = self._ret_fwd_peak_speed
                self._ret_armed_t = now

        return step_x, step_y

    def _update_velocity_gain(
        self,
        smoothed_speed: float,
        dt: float,
        config: Dict[str, Any],
        *,
        gain_min_override: Optional[float] = None,
        gain_mid_override: Optional[float] = None,
        gain_max_override: Optional[float] = None,
    ) -> float:
        """
        Update EMA hand speed and effective pointer acceleration gain with:
        - Stationary multiplier freeze when hand speed is below `velocity_stationary_speed`
        - Discrete 3-state `hand_shape_mode` ("precise" | "normal" | "fast") gain curve bounds
        - Coarse-to-fine post-flick deceleration assist (`velocity_c2f_*`)
        - Monotonic C1 S-curve gain (`compute_velocity_gain`)
        - Bounded slew-rate limiting (`velocity_slew_up`, `velocity_slew_down`)
        """
        v_still = max(0.0, float(config.get("velocity_stationary_speed", 0.05)))
        alpha_v = max(0.05, min(1.0, float(config.get("velocity_ema_alpha", 0.35))))
        raw_spd = self._raw_hand_speed
        now = self._clock

        # Stationary freeze: when raw hand motion is below the stationary threshold,
        # do not update or oscillate the multiplier even if finger curl changes.
        if raw_spd < v_still or smoothed_speed < v_still * 0.5:
            self._vel_ema *= max(0.0, 1.0 - alpha_v)
            self._prev_smoothed_speed = smoothed_speed
            return self._effective_gain

        mode_min, mode_mid, mode_max = self._velocity_mode_bounds(config)
        g_min = gain_min_override if gain_min_override is not None else mode_min
        g_mid = gain_mid_override if gain_mid_override is not None else mode_mid
        g_max = gain_max_override if gain_max_override is not None else mode_max

        accel = (smoothed_speed - self._prev_smoothed_speed) / max(1e-3, dt)
        self._prev_smoothed_speed = smoothed_speed

        # Track recent peak speed over a rolling ~0.40s window for coarse-to-fine flick detection
        if smoothed_speed >= self._peak_recent_speed or (now - self._peak_recent_t) > 0.40:
            self._peak_recent_speed = smoothed_speed
            self._peak_recent_t = now

        # Use physical smoothed delta speed for ballistics EMA
        self._vel_ema = self._vel_ema + alpha_v * (smoothed_speed - self._vel_ema)

        c2f_enabled = bool(config.get("velocity_c2f_enabled", True))
        c2f_aim = max(v_still, float(config.get("velocity_c2f_aim_speed", 0.32)))
        if c2f_enabled:
            c2f_entry = max(c2f_aim, float(config.get("velocity_c2f_entry_speed", 0.45)))
            c2f_decel = max(0.05, float(config.get("velocity_c2f_decel_thresh", 0.80)))
            c2f_win = max(0.05, float(config.get("velocity_c2f_window", 0.28)))
            if (
                self._peak_recent_speed >= c2f_entry
                and (now - self._peak_recent_t) <= 0.35
                and smoothed_speed <= c2f_aim
                and accel <= -c2f_decel
            ):
                self._c2f_until = now + c2f_win
                # Dampen EMA carry from the flick so aiming starts cleanly
                self._vel_ema = min(self._vel_ema, smoothed_speed)
            elif now < self._c2f_until and smoothed_speed > c2f_aim * 1.25 and accel > 0.5:
                self._c2f_until = -1e9

        target_gain = self.compute_velocity_gain(
            self._vel_ema,
            config,
            gain_min_override=g_min,
            gain_mid_override=g_mid,
            gain_max_override=g_max,
        )

        slew_up = max(0.5, float(config.get("velocity_slew_up", 14.0)))
        slew_down = max(0.5, float(config.get("velocity_slew_down", 18.0)))
        if c2f_enabled and now < self._c2f_until and smoothed_speed <= c2f_aim:
            c2f_scale = max(0.25, min(1.0, float(config.get("velocity_c2f_gain_scale", 0.75))))
            c2f_boost = max(1.0, float(config.get("velocity_c2f_slew_boost", 2.2)))
            # Blend target gain toward fine precision floor as speed approaches stationary
            blend = max(0.0, min(1.0, (smoothed_speed - v_still) / max(1e-6, c2f_aim - v_still)))
            fine_floor = max(0.05, g_min * c2f_scale)
            target_gain = min(target_gain, fine_floor + (target_gain - fine_floor) * (0.45 * blend))
            slew_down *= c2f_boost

        delta = target_gain - self._effective_gain
        max_up = slew_up * dt
        max_down = slew_down * dt
        if delta > max_up:
            delta = max_up
        elif delta < -max_down:
            delta = -max_down

        self._effective_gain = max(0.05, self._effective_gain + delta)
        return self._effective_gain

    @staticmethod
    def compute_velocity_gain(
        speed: float,
        config: Dict[str, Any],
        *,
        gain_min_override: Optional[float] = None,
        gain_mid_override: Optional[float] = None,
        gain_max_override: Optional[float] = None,
    ) -> float:
        """
        Compute pointer acceleration gain as a monotonic C1 S-curve function of
        EMA-smoothed hand speed (in screen-width / frame-width normalized units per second).

        Regimes:
        1. Ultra-low speed (`speed <= velocity_deadzone`, default 0.02):
           constant `velocity_gain_min` (default 0.45x) for sub-pixel precision aiming.
        2. Medium speed (`velocity_deadzone < speed <= velocity_mid_speed`, default 0.02..0.35):
           concave-up polynomial S-curve segment from `velocity_gain_min` (0.45x) to `velocity_gain_mid` (3.2x).
        3. High speed (`velocity_mid_speed < speed < velocity_high_speed`, default 0.35..1.35):
           concave-down saturation segment from `velocity_gain_mid` (3.2x) to `velocity_gain_max` (11.0x),
           saturating smoothly at `velocity_gain_max` for `speed >= velocity_high_speed`.
        """
        g_min = max(
            0.05,
            float(gain_min_override if gain_min_override is not None else config.get("velocity_gain_min", 0.55)),
        )
        g_mid = max(
            g_min,
            float(gain_mid_override if gain_mid_override is not None else config.get("velocity_gain_mid", 2.8)),
        )
        g_max = max(
            g_mid,
            float(gain_max_override if gain_max_override is not None else config.get("velocity_gain_max", 6.5)),
        )

        v_dead = max(0.0, float(config.get("velocity_deadzone", 0.02)))
        v_mid = max(v_dead + 1e-6, float(config.get("velocity_mid_speed", 0.40)))
        v_high = max(v_mid + 1e-6, float(config.get("velocity_high_speed", 1.30)))

        spd = max(0.0, float(speed))
        if spd <= v_dead:
            return g_min
        if spd <= v_mid:
            u = (spd - v_dead) / (v_mid - v_dead)
            f1 = 0.5 * u * u * (3.0 - u)
            return g_min + (g_mid - g_min) * f1
        if spd >= v_high:
            return g_max
        w = (spd - v_mid) / (v_high - v_mid)
        f2 = 0.5 * w * (3.0 - w * w)
        return g_mid + (g_max - g_mid) * f2

    def map_to_screen_absolute(
        self,
        norm_x: float,
        norm_y: float,
        active_margin: float = 0.15,
        clamp: bool = True,
    ) -> Tuple[float, float]:
        """
        Map normalized camera coordinates [active_margin, 1 - active_margin]
        to the absolute-mode monitor (the primary one until cycle_monitor() picks another).
        With clamp=False, points in the margin map beyond the screen edges.
        """
        margin = max(0.0, min(0.45, float(active_margin)))
        span = max(1e-6, 1.0 - 2.0 * margin)

        nx = (float(norm_x) - margin) / span
        ny = (float(norm_y) - margin) / span

        if clamp:
            nx = max(0.0, min(1.0, nx))
            ny = max(0.0, min(1.0, ny))

        m = self.absolute_monitor
        sx = m.left + nx * (m.width - 1)
        sy = m.top + ny * (m.height - 1)
        return sx, sy

    def _distance_scale(self, config: Dict[str, Any], now: float) -> float:
        """Far hand -> bigger cursor gain. A hand at half the distance covers half the pixels, so the same arm
        movement moves half as far on the screen and cannot reach the whole monitor. The factor is
        (distance_gain_ref / smoothed hand size) ** distance_gain_strength, never below 1, at most distance_gain_max.
        distance_gain_ref 0 turns it off (docs/far-hand-loss.md, 'Cursor reach')."""
        ref = float(config.get("distance_gain_ref", 0.11))
        size = self.hand_size
        if ref <= 0 or size is None or size <= 1e-4:
            if ref <= 0:
                self.distance_scale = 1.0
            return self.distance_scale  # no hand this frame: keep the last factor
        if self._size_ema is None or now - self._size_t > 1.0:
            self._size_ema = size
        else:
            alpha = 1.0 - math.exp(-max(0.0, now - self._size_t) / 0.4)   # ~0.4 s: the cursor gain must not flicker
            self._size_ema += alpha * (size - self._size_ema)
        self._size_t = now
        strength = max(0.0, float(config.get("distance_gain_strength", 1.0)))
        top = max(1.0, float(config.get("distance_gain_max", 3.0)))
        self.distance_scale = max(1.0, min(top, (ref / max(self._size_ema, 1e-4)) ** strength))
        return self.distance_scale

    def _relative_step(
        self,
        hand_pt: Tuple[float, float],
        config: Dict[str, Any],
        now: float,
    ) -> Tuple[int, int]:
        """Trackpad-style fine control with pointer acceleration."""
        smoothing = max(0.05, min(1.0, float(config.get("smoothing", 0.35))))
        is_vel_scheme = (
            str(config.get("movement_scheme", "legacy_shape")).lower() == "velocity"
            and self._pointer_source is None
        )
        if config.get("tremor_filter", False):
            smoothing = 1.0  # OneEuroFilter already smooths; stacking EMA adds phase lag
        g_min = max(0.0, float(config.get("relative_gain_min", 0.35)))
        g_max = max(g_min, float(config.get("relative_gain_max", 1.2)))
        v_lo = max(0.0, float(config.get("relative_accel_speed_low", 0.1)))
        v_hi = max(v_lo + 1e-6, float(config.get("relative_accel_speed_high", 0.8)))

        hx, hy = float(hand_pt[0]), float(hand_pt[1])
        if self.anchor_hand_pos is None:
            # (Re)start from wherever the cursor is: no jump after a click, throw or hand loss
            self.anchor_hand_pos = (hx, hy)
            if self._path is not None:
                self._path.reset()
            self._rel_pos = (float(self.last_screen_pos[0]), float(self.last_screen_pos[1]))
            self._rel_carry = (0.0, 0.0)
            self._rel_t = now
            self._smoothed_pos = self._rel_pos
            if is_vel_scheme:
                self.dpi_multiplier = self._effective_gain
            return self.last_screen_pos

        ax, ay = self.anchor_hand_pos
        sx = ax + smoothing * (hx - ax)  # EMA on the hand point suppresses landmark jitter
        sy = ay + smoothing * (hy - ay)
        self.anchor_hand_pos = (sx, sy)

        dt = max(1e-3, min(0.1, now - self._rel_t))
        self._rel_t = now
        dx = sx - ax
        dy = (sy - ay) * self.frame_aspect  # physical units (frame widths)
        far = self._distance_scale(config, now)  # a far hand moves few pixels: scale it back to the reference distance
        if far != 1.0:
            dx, dy = dx * far, dy * far

        # Workspace edge re-clutch assist: when the hand reaches the camera frame border
        # during a pointing stroke and reverses inward toward the center, re-anchor the
        # hand without pulling the cursor back from the edge. Never active during drag,
        # scroll, or browse_lock.
        if (
            is_vel_scheme
            and config.get("edge_reclutch_enabled", True)
            and not self.is_dragging
            and self._scroll_prev is None
            and not self.scroll_coasting
            and not self._browse_centered
        ):
            edge_m = max(0.01, min(0.25, float(config.get("edge_reclutch_margin", 0.06))))
            rel_m = max(edge_m + 0.01, min(0.35, float(config.get("edge_reclutch_release_margin", 0.12))))
            raw_pt = self._raw_hand_prev if self._raw_hand_prev is not None else (hx, hy, now)
            rx_pt, ry_pt = raw_pt[0], raw_pt[1]
            r_dx, r_dy = self._raw_hand_delta
            border_dist = min(rx_pt, 1.0 - rx_pt, ry_pt, 1.0 - ry_pt)
            out_x, out_y = rx_pt - 0.5, ry_pt - 0.5
            out_dot = out_x * r_dx + out_y * (r_dy / max(1e-6, self.frame_aspect))
            if border_dist <= edge_m and out_dot > 1e-4:
                self._edge_touched_at = now
                self._edge_dir = (out_x, out_y)
                self._edge_reclutch_active = False
            elif (
                (now - self._edge_touched_at) <= 0.45
                and border_dist <= rel_m
                and (self._edge_dir[0] * r_dx + self._edge_dir[1] * (r_dy / max(1e-6, self.frame_aspect))) < -1e-4
            ):
                self._edge_reclutch_active = True
                if self._path is not None:
                    self._path.reset()
                self._rel_carry = (0.0, 0.0)
                return self.last_screen_pos
            else:
                self._edge_reclutch_active = False
        else:
            self._edge_reclutch_active = False

        pivot = getattr(self, "elbow_pivot", None)   # src/elbow_pivot.py, set by the runtime that feeds it
        bent = None
        if self._pointer_source is None and pivot is not None and config.get("elbow_pivot", False):
            # Palm / fist cursor about the (estimated) elbow: arc about it -> screen x, bending toward it -> screen y.
            # Straightens the tilt AND the curve; until the elbow is known the step goes on as before.
            bent = pivot.convert((ax, ay), (sx, sy), self.frame_aspect)
        if bent is not None:
            dx, dy = bent
        elif self._pointer_source is None and config.get("palm_axes"):
            # Palm / fist cursor: the measured up/down and left/right of this user's hand (src/palm_axes.py) become screen
            # vertical / horizontal, e.g. a forearm swinging about an elbow on the chair no longer moves diagonally.
            dx, dy = apply_axes(dx, dy, config["palm_axes"], float(config.get("palm_axes_strength", 0.6)))

        raw_dx, raw_dy = dx, dy
        prev_vel_ema = self._vel_ema
        prev_eff_gain = self._effective_gain
        pre_smooth_speed = math.hypot(dx, dy) / dt
        if is_vel_scheme:
            eff_gain = self._update_velocity_gain(pre_smooth_speed, dt, config)
            self.dpi_multiplier = eff_gain

        if (self._pointer_source is None and config.get("path_smoothing", False) and not self.is_dragging
                and self.dpi_multiplier >= float(config.get("path_min_multiplier", 1.15))):
            # Fist / half-fist cursor: straight lines and smooth curves only (src/path_smoother.py), the wobble that
            # 35x would turn into a zigzag is dropped.
            if self._path is None:
                self._path = PathSmoother(config)
            else:       # a reloaded config, another profile or the settings sliders reach it (was: fixed at first use)
                self._path.config.update({k: float(config[k]) for k in self._path.config if k in config})
            dx, dy = self._path.step(now, dx, dy)
        elif self._path is not None:
            self._path.reset()

        speed = math.hypot(dx, dy) / dt
        if is_vel_scheme:
            v_dead = max(0.0, float(config.get("velocity_deadzone", 0.02)))
            v_still = max(v_dead, float(config.get("velocity_stationary_speed", 0.05)))
            g_min_vel, _, _ = self._velocity_mode_bounds(config)
            if self._raw_hand_speed < v_dead and pre_smooth_speed < v_dead:
                step_gain = g_min_vel * 0.5
            elif self._raw_hand_speed < v_still:
                step_gain = min(self._effective_gain, g_min_vel)
            else:
                step_gain = self._effective_gain
            magnet_cap = float(config.get("ui_magnet_max_gain", 1.8))
            is_fine_aim = self.hand_shape_mode in ("precise", "open") or (now < self._c2f_until)
            if (
                self.slowdown_provider is not None
                and (is_fine_aim or step_gain <= magnet_cap)
                and config.get("ui_magnet_enabled", True)
            ):
                m_factor = float(self.slowdown_provider(*self.last_screen_pos))
                if is_fine_aim and m_factor < 1.0:
                    p_scale = max(0.3, min(1.0, float(config.get("ui_magnet_precision_scale", 0.85))))
                    m_factor = max(0.1, m_factor * p_scale)
                step_gain *= m_factor
            gain = step_gain
        else:
            t = max(0.0, min(1.0, (speed - v_lo) / (v_hi - v_lo)))
            mult = self.dpi_multiplier
            if self.is_dragging and mult > 1.0 and config.get("drag_precision", True):
                # Dragging (drawing, writing) needs fine control at slow hand speed, a long drag across the screen needs the big
                # multiplier at fast hand speed: 1x when the hand is slow, the full multiplier when it is fast (2026-11-04: the
                # hand moved at 0.1 frame widths/s while drawing in Paint, with the full 15x the strokes were jagged scribbles).
                d_min = max(1.0, float(config.get("drag_precision_min", 2.5)))
                d_lo = max(0.0, float(config.get("drag_precision_low", 0.3)))
                d_hi = max(d_lo + 1e-6, float(config.get("drag_precision_high", 1.2)))
                td = max(0.0, min(1.0, (speed - d_lo) / (d_hi - d_lo)))
                mult = max(1.0, min(mult, d_min) + (mult - min(mult, d_min)) * td)
            gain = (g_min + (g_max - g_min) * t) * mult

        # The fast hand shapes multiply hand noise and quick flicks (up to 30x): logged 2026-11-04, one frame moved the cursor 500-1000 px
        # while the hand moved ~60 px, which looks like a jump without a path. A frame may move the cursor at most cursor_max_step of the
        # screen width; the rest is carried into the next frames (bounded, so the cursor does not coast long after the hand stops).
        step_x = dx * self.screen_w * gain + self._rel_carry[0]
        step_y = dy * self.screen_w * gain + self._rel_carry[1]
        cap = float(config.get("cursor_max_step", 0.12)) * self.screen_w
        mag = math.hypot(step_x, step_y)
        if cap > 0 and mag > cap:
            k = cap / mag
            rest = (step_x * (1 - k), step_y * (1 - k))
            rest_mag = math.hypot(*rest)
            keep = min(1.0, 2.0 * cap / rest_mag) if rest_mag > 0 else 1.0
            self._rel_carry = (rest[0] * keep, rest[1] * keep)
            step_x, step_y = step_x * k, step_y * k
        else:
            self._rel_carry = (0.0, 0.0)
        step_x, step_y = self._update_auto_return_suppression(
            sx,
            sy,
            raw_dx,
            raw_dy,
            step_x,
            step_y,
            dt,
            now,
            config,
            prev_vel_ema,
            prev_eff_gain,
        )
        px, py = self.screens.clamp(self._rel_pos[0] + step_x, self._rel_pos[1] + step_y)
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

        hx_raw, hy_raw = float(hand_pt[0]), float(hand_pt[1])
        if self._raw_hand_prev is not None:
            r_dt = max(1e-3, min(0.2, now - self._raw_hand_prev[2]))
            r_dx = hx_raw - self._raw_hand_prev[0]
            r_dy = (hy_raw - self._raw_hand_prev[1]) * self.frame_aspect
            self._raw_hand_delta = (r_dx, r_dy)
            self._raw_hand_speed = math.hypot(r_dx, r_dy) / r_dt
        else:
            self._raw_hand_delta = (0.0, 0.0)
            self._raw_hand_speed = 0.0
        self._raw_hand_prev = (hx_raw, hy_raw, now)

        is_vel_scheme = (
            str(config.get("movement_scheme", "legacy_shape")).lower() == "velocity"
            and self._pointer_source is None
        )
        if config.get("tremor_filter", False):
            if is_vel_scheme and config.get("velocity_adaptive_filter", True):
                v_still = max(1e-3, float(config.get("velocity_stationary_speed", 0.05)))
                is_fine = self.hand_shape_mode in ("precise", "open") or (now < self._c2f_until)
                min_cut = float(
                    config.get("velocity_filter_c2f_cutoff", 0.80)
                    if is_fine
                    else config.get("velocity_filter_min_cutoff", 1.1)
                )
                beta_val = float(config.get("velocity_filter_beta", 14.0))
                base_dz = float(config.get("tremor_deadzone", 0.002))
                if self._raw_hand_speed <= v_still:
                    dz_val = base_dz
                else:
                    # Smoothly shed deadband above stationary speed so fast flicks have zero deadzone lag
                    dz_val = base_dz * max(0.0, 1.0 - (self._raw_hand_speed - v_still) / (2.0 * v_still))
                hand_pt = self._tremor(
                    hand_pt,
                    now,
                    min_cutoff=min_cut,
                    beta=beta_val,
                    deadzone=dz_val,
                )
            else:
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
            elif self.dpi_multiplier * self._distance_scale(config, now) != 1.0 and self._abs_prev is not None:
                # Fist boost: each hand delta adds (mult - 1) x delta to the offset, so the
                # cursor moves mult x as fast and never jumps when the fist opens/closes
                # (a far hand adds its distance factor on top)
                k = self.dpi_multiplier * self.distance_scale - 1.0
                self.abs_offset = (
                    self.abs_offset[0] + (mx - self._abs_prev[0]) * k,
                    self.abs_offset[1] + (my - self._abs_prev[1]) * k,
                )
            self._abs_prev = (mx, my)
            raw_x, raw_y = self.absolute_monitor.clamp(mx + self.abs_offset[0], my + self.abs_offset[1])
        self._after_grab = False

        if config.get("tremor_filter", False):
            smoothing = 1.0  # the tremor filter already smooths; stacking both adds lag
        if self._smoothed_pos is None:
            sm_x, sm_y = raw_x, raw_y
        else:
            sm_x = self._smoothed_pos[0] + smoothing * (raw_x - self._smoothed_pos[0])
            sm_y = self._smoothed_pos[1] + smoothing * (raw_y - self._smoothed_pos[1])

        self._smoothed_pos = (sm_x, sm_y)
        fx, fy = (self.absolute_monitor if mode != "relative" else self.screens).clamp(sm_x, sm_y)
        final_x, final_y = int(round(fx)), int(round(fy))
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
        if config.get("switcher_external_cursor", False):
            # The virtual hand: the box places the cursor and the hand shapes give the clicks. The closed-hand rule below would
            # throw away its thumb + index click (that shape is a pinch to the rule engine), so the clicks go straight in here.
            self._run_switcher_pending(now, config)
            if not self.switcher_open:
                return
            if action not in ("task_switcher", "alt_tab"):
                self._sw_armed = True          # the opening thumb + pinky pinch has been released
            if not self._sw_armed:
                return
            kind = {"left_click": "click", "task_switcher": "confirm", "alt_tab": "confirm",
                    "right_click": "cancel"}.get(action)
            if kind is not None and self._sw_pending is None:
                self._sw_pending = (kind, now, self.last_screen_pos)
                self._run_switcher_pending(now, config)
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
                if config.get("switcher_external_cursor", False):
                    return      # the virtual hand's box already placed the cursor (src/virtual_hand.py)
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
        if h:
            self.scroll_total[1] += h
        if self.dry_run or not (v or h):
            return
        if self._scroll_tau > 0:
            self._pump.add(v, h)          # a thread hands it out in small steps (see ScrollPump)
            self._ensure_pump()
            return
        if v:
            pyautogui.scroll(v, _pause=False)
        if h:
            pyautogui.hscroll(h, _pause=False)

    def _ensure_pump(self) -> None:
        if self._pump_thread is not None and self._pump_thread.is_alive():
            return
        self._pump_stop.clear()
        self._pump_thread = threading.Thread(target=self._pump_loop, name="scroll-pump", daemon=True)
        self._pump_thread.start()

    def _pump_loop(self) -> None:
        idle = 0
        while not self._pump_stop.is_set():
            v, h = self._pump.step(time.perf_counter(), self._scroll_tau)
            try:
                if v:
                    pyautogui.scroll(v, _pause=False)
                if h:
                    pyautogui.hscroll(h, _pause=False)
            except Exception:
                pass
            pv, ph = self._pump.pending()
            idle = 0 if (v or h or abs(pv) >= 1 or abs(ph) >= 1) else idle + 1
            if idle > 250:                # ~2 s with nothing to send: let the thread end (restarted on demand)
                break
            time.sleep(0.008)

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
        self._clock = now
        self._scroll_tau = float(config.get("scroll_smooth_tau", 0.03))
        x, y = float(hand_pt[0]), float(hand_pt[1])
        if self._scroll_prev is None:
            self._stop_scroll_inertia()  # pinching again catches a coasting page
            self._flush_cursor()  # the page under the cursor gets the wheel events
            self._begin_browse(config, now)
            self._scroll_prev = self._scroll_anchor = (x, y)
            self._scroll_live = False
            self._scroll_acc = [0.0, 0.0]
            self._scroll_hist.clear()
            self._scroll_recent.clear()
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
            # Fingers are opening. The hand's own motion is ignored from here (it springs back at the
            # end of a flick; that used to drag the page back and give the inertia the opposite direction),
            # and the fling velocity measured BEFORE the release is what counts.
            # The page used to stand still until the release was confirmed (~0.15 s) and only then start
            # coasting: a visible pause between scrolling and inertia. Now it coasts from the first
            # opening frame, so the two are one movement. If the fingers close again it is cancelled.
            if (self._scroll_live and config.get("scroll_inertia", False)
                    and config.get("scroll_inertia_seamless", True)):
                if not self._provisional and self._inertia_v is None:
                    launch = self._launch_velocity(config, now)
                    if launch is not None:
                        self._inertia_v = [launch[0], launch[1]]
                        self._inertia_t = now
                        self._inertia_acc = [0.0, 0.0]
                        self._provisional = True
                        self.inertia_launches += 1
                if self._provisional:
                    self._tick_scroll_inertia(config, now)
            return
        if self._provisional:
            # a false alarm (the fingers closed again): take the coast back and continue from here
            self._provisional = False
            self._inertia_v = None
            self._pump.clear()
            self._scroll_prev = (x, y)
            self._scroll_hist.clear()
            self._scroll_recent.clear()
            return
        if not self._scroll_live:
            ax, ay = self._scroll_anchor
            if math.hypot(sx - ax, (sy - ay) * self.frame_aspect) < float(config.get("scroll_deadzone", 0.01)):
                return
            self._scroll_live = True  # past the deadzone: start scrolling from here on
            return
        gain = float(config.get("scroll_gain", 36000.0))
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
        self._scroll_recent.append((now, rv, rh))
        while len(self._scroll_recent) > 2 and now - self._scroll_recent[0][0] > 0.4:
            self._scroll_recent.popleft()
        self._send_scroll(*self._take_steps(self._scroll_acc))

    def _launch_velocity(self, cfg: Dict[str, Any], now: float) -> Optional[Tuple[float, float]]:
        """Wheel units/s to coast with, from the last ~0.1 s of the grab (None = do not coast)."""
        if not (self._scroll_live and cfg.get("scroll_inertia", False) and len(self._scroll_hist) >= 2):
            return None
        span = max(1e-3, self._scroll_hist[-1][0] - self._scroll_hist[0][0])
        vv = sum(r[1] for r in list(self._scroll_hist)[1:]) / span
        vh = sum(r[2] for r in list(self._scroll_hist)[1:]) / span
        raw = (vv, vh)
        # Follow the page's RECENT direction (last ~0.4 s), not the whole grab: after a long
        # pull down, a flick back up must coast up (the whole-grab check refused it).
        rv_sum = sum(r[1] for r in self._scroll_recent)
        rh_sum = sum(r[2] for r in self._scroll_recent)
        same_way = vv * rv_sum + vh * rh_sum > 0
        # Browsing is mostly vertical: a flick that is mostly up/down must not drift sideways
        # (hand wobble leaked 20-30 % into the horizontal wheel). Same for a sideways flick.
        lock = float(cfg.get("scroll_inertia_axis_lock", 0.5))
        if abs(vh) < abs(vv) * lock:
            vh = 0.0
        elif abs(vv) < abs(vh) * lock:
            vv = 0.0
        # A release flick can be 5-7x faster than the pull itself: cap the launch speed.
        cap = float(cfg.get("scroll_inertia_max_speed", 3600.0))
        speed = math.hypot(vv, vh)
        if speed > cap > 0:
            vv, vh = vv * cap / speed, vh * cap / speed
        launched = same_way and math.hypot(vv, vh) >= float(cfg.get("scroll_inertia_min_speed", 600.0))
        self.last_inertia = {"raw": [round(raw[0]), round(raw[1])], "used": [round(vv), round(vh)],
                             "net": [round(self._scroll_net[0]), round(self._scroll_net[1])],
                             "recent": [round(rv_sum), round(rh_sum)], "same_way": bool(same_way),
                             "launched": bool(launched), "hist_n": len(self._scroll_hist),
                             "seamless": bool(self._scroll_prev is not None and not self._provisional)}
        return (vv, vh) if launched else None

    def _begin_browse(self, config: Dict[str, Any], now: float) -> None:
        """A page grab starts: keep the cursor on the scroll area it is over for the whole browsing session.

        Scrolling is the hand's up/down motion, so the cursor used to drift over links and other areas between
        grabs (and the wheel goes to the area under the cursor). While browsing the user should not have to care
        where the cursor is: it stays while grabbing, coasting and for `scroll_session_hold` afterwards
        (continuous browsing keeps it fixed), then follows the hand again. When the scroll area under it is a pane
        the application exposes, the cursor is also moved to the middle of that pane."""
        if not config.get("scroll_center_cursor", True):
            self._browse_centered = False
            return
        cx, cy = self.last_screen_pos
        tol = float(config.get("scroll_center_tolerance", 24))
        # Continuous browsing: the first grab of a series decided the spot (where the user put the cursor, or the
        # middle of a pane the application exposes). Every later grab of the series sends the cursor back there, so
        # the hand's return trip - which drags the cursor along - costs nothing. A new series starts when the last
        # browsing is longer ago than scroll_series_gap, or when the user parked the cursor somewhere else on purpose
        # (it stood still for scroll_series_dwell before the grab, unlike a cursor still drifting with the hand).
        anchor = self._series_anchor
        if anchor is not None and now - self._series_last < float(config.get("scroll_series_gap", 4.0)):
            parked = now - self._cursor_moved_at >= float(config.get("scroll_series_dwell", 0.4))
            away = math.hypot(cx - anchor[0], cy - anchor[1]) > tol
            if not (away and parked):
                if away:
                    self.warp_to(anchor[0], anchor[1])
                    self.event_log.append(f"scroll_return:{anchor[0]},{anchor[1]}")
                self.browse_target = (anchor[0], anchor[1], "series")
                self._browse_centered = True
                self._browse_until = now + float(config.get("scroll_session_hold", 1.2))
                self._series_last = now
                return
        target: Optional[Tuple[int, int, str]] = None
        kept: Tuple[int, int, str] = (cx, cy, "kept")
        # The cursor position is what picks the label: the wheel (and so the page) moves the scroll area under the
        # cursor. Two things follow. (1) The cursor is only moved when the application exposes the scroll area and it is
        # a PANE of the window (a list, a sidebar): its middle is then the best place. (2) Whatever happens, the
        # cursor stays where it is for the whole browsing session, so it can not drift onto another label.
        # Not moved: an area that is most of the window (the page itself - it may hold inner scroll areas the
        # application does not expose, and moving to the page middle would leave them), and the case where nothing is
        # exposed (a web app's own panels are mostly not): the user put the cursor on the label, it stays there.
        if self.scroll_target_finder is not None:
            region = self.scroll_target_finder.target_at(cx, cy, wait=float(config.get("scroll_target_wait", 0.15)))
            if region is not None:
                if 0.0 < region.area_fraction < float(config.get("scroll_center_max_fraction", 0.6)):
                    target = (region.center[0], region.center[1], "region:" + region.name)
                else:
                    kept = (cx, cy, "page:" + region.name)
        if target is None and config.get("scroll_center_fallback_window", False) and self.window_probe is not None:
            found = self.window_probe(cx, cy)
            if found is not None:
                target = (found[0], found[1], "window:" + found[2])
        self.browse_target = target or kept
        if target is not None:
            tx, ty = int(target[0]), int(target[1])
            if math.hypot(tx - cx, ty - cy) > tol:
                self.warp_to(tx, ty)
                self.event_log.append(f"scroll_center:{tx},{ty}:{target[2].split(':')[0]}")
        self._series_anchor = self.last_screen_pos           # the spot the rest of this series returns to
        self._series_last = now
        self._browse_centered = True
        self._browse_until = now + float(config.get("scroll_session_hold", 1.2))

    def _browse_lock(self, config: Dict[str, Any], now: float) -> bool:
        """True while the cursor must stay where the browsing put it."""
        if not self._browse_centered:
            return False
        if self._scroll_prev is not None or self.scroll_coasting:
            self._browse_until = now + float(config.get("scroll_session_hold", 1.2))
            self._series_last = now                      # browsing is going on: the series is alive
            return True
        if now < self._browse_until:
            return True
        self._browse_centered = False
        return False

    def _end_scroll(self, config: Optional[Dict[str, Any]] = None, now: float = 0.0, launch: bool = True) -> None:
        """Pinch released: a page that was still moving keeps coasting (inertia)."""
        cfg = config or {}
        self._scroll_resume = False
        if self._provisional:
            # the coast began when the fingers started to open: confirming the release just keeps it
            # going (also after a short hand loss: the user had let go)
            self._provisional = False
        else:
            v = self._launch_velocity(cfg, now) if launch else None
            if v is not None:
                self._inertia_v = [v[0], v[1]]
                self._inertia_t = now
                self._inertia_acc = [0.0, 0.0]
                self.inertia_launches += 1
        self._scroll_prev = self._scroll_anchor = None
        self._scroll_live = False
        self._scroll_acc = [0.0, 0.0]
        self._scroll_hist.clear()
        self._scroll_recent.clear()

    @property
    def scroll_coasting(self) -> bool:
        return self._inertia_v is not None

    def _stop_scroll_inertia(self) -> None:
        self._inertia_v = None
        self._provisional = False
        self._pump.clear()

    def _tick_scroll_inertia(self, config: Dict[str, Any], now: float) -> None:
        """Exponential slow-down (frame-rate independent), like flicking a phone page."""
        if self._inertia_v is None:
            return
        self._scroll_tau = float(config.get("scroll_smooth_tau", 0.03))
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
            self._raw_hand_prev = None
            self._raw_hand_speed = 0.0
            return False                 # absolute simply resumes its mapping
        self.anchor_hand_pos = None
        self._raw_hand_prev = None
        self._raw_hand_speed = 0.0
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
            x, y = self.screens.clamp(x + dx * self.screen_w * g * gain_x,
                                      y + dy * self.screen_w * g * gain_y)
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
            self.clutch_state = "inactive"
            self._clutch_exit_cand_since = None
            self.return_state = "none"
        else:
            self._scroll_resume = self._scroll_prev is not None
        self._tick_scroll_inertia(cfg, now)
        self.anchor_hand_pos = None
        self.anchor_screen_pos = None
        self._raw_hand_prev = None
        self._raw_hand_speed = 0.0
        self._tick_playback(now)
        return self.last_screen_pos

    def _move_os_cursor(self, x: int, y: int, immediate: bool = False) -> None:
        if abs(x - self.last_screen_pos[0]) + abs(y - self.last_screen_pos[1]) > 2:
            self._cursor_moved_at = self._clock
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

    @property
    def absolute_monitor(self) -> Monitor:
        if self._abs_monitor is None or self._abs_monitor not in self.screens.monitors:
            self._abs_monitor = self.screens.nearest(*self.last_screen_pos)
        return self._abs_monitor

    def cycle_monitor(self) -> Monitor:
        """Jump to the centre of the next monitor; absolute mode maps onto it from now on."""
        target = self.screens.next_after(*self.last_screen_pos)
        self._abs_monitor = target
        self.abs_offset = (0.0, 0.0)
        self._after_grab = False
        self.warp_to(*target.center)
        self.event_log.append("next_monitor")
        return target

    def warp_to(self, x: int, y: int) -> None:
        """Put the cursor at (x, y) now and re-anchor relative movement there.

        Used by the index pointer's thumb trigger to undo the cursor shake of a press.
        """
        self._abs_prev = None
        self.anchor_hand_pos = None
        self.anchor_screen_pos = None
        self._smoothed_pos = None
        self._raw_hand_prev = None
        self._raw_hand_speed = 0.0
        self._tremor.reset()
        self._move_os_cursor(int(x), int(y), immediate=True)

    def _flush_cursor(self) -> None:
        """Put the real cursor exactly on the target before a click / button change."""
        if not self.dry_run and self._interp is not None:
            self._interp.snap(*self.last_screen_pos)

    @staticmethod
    def foreground_info() -> Dict[str, Any]:
        """What Alt+F4 or app-specific bindings would hit: window class, title, process, and whether it is this app."""
        info: Dict[str, Any] = {"cls": "", "title": "", "title_full": "", "process": "", "own": False, "ok": False}
        if sys.platform != "win32":
            return info
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return info
            buf = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, buf, 512)
            info["cls"] = buf.value
            user32.GetWindowTextW(hwnd, buf, 512)
            info["title_full"] = buf.value
            info["title"] = buf.value[:40]
            pid = ctypes.c_ulong(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            info["own"] = pid.value == os.getpid()
            info["ok"] = (not info["own"]) and info["cls"] not in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")
            if pid.value:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
                if hproc:
                    try:
                        pbuf = ctypes.create_unicode_buffer(512)
                        plen = ctypes.c_ulong(512)
                        if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(plen)):
                            raw_path = pbuf.value
                            info["process"] = os.path.basename(raw_path)
                    finally:
                        kernel32.CloseHandle(hproc)
        except Exception:
            pass
        return info

    def _safe_to_close_foreground(self) -> bool:
        """Alt+F4 only for an ordinary application window: never this app itself, the desktop or the taskbar
        (Alt+F4 on the desktop opens Windows' shut-down dialog)."""
        self.last_close_info = self.foreground_info()
        return bool(self.last_close_info["ok"])

    def send_motion_action(self, action_id: str) -> bool:
        """Send one command of motion mode (back / forward / tab_prev / tab_next or custom motion binding)."""
        from src.gesture_catalog import MOTION_TARGETS
        from src.motion_mode import ACTION_KEYS
        keys = ACTION_KEYS.get(action_id)
        gesture_key = MOTION_TARGETS.get(action_id, action_id)
        try:
            from src.action_bindings import DEFAULT_GESTURE_BINDINGS, get_action_registry
            reg = get_action_registry()
            has_custom = gesture_key in reg.custom_motions or gesture_key in reg.bindings
        except Exception:
            reg = None
            has_custom = False

        if keys is None and not has_custom:
            return False
        if action_id == "close_window" and not self.dry_run and not self._safe_to_close_foreground():
            self.event_log.append("motion:close_window_refused")
            return False

        self.event_log.append(f"motion:{action_id}")
        if reg is not None and has_custom:
            fg = self.foreground_info() if not self.dry_run else {}
            resolved = reg.resolve_effective_action(gesture_key, fg)
            default_b = DEFAULT_GESTURE_BINDINGS.get(gesture_key, {})
            is_overridden = (
                bool(resolved.get("matched_app"))
                or gesture_key in reg.custom_motions
                or resolved.get("action_type") != default_b.get("action_type")
                or resolved.get("action_value") != default_b.get("action_value")
            )
            if is_overridden:
                ok, _badge = reg.execute_resolved_action(resolved, dry_run=self.dry_run, mouse_controller=None)
                return bool(ok)

        if keys is not None:
            if not self.dry_run:
                pyautogui.hotkey(*keys, _pause=False)
            return True
        return False

    def close(self) -> None:
        self._pump_stop.set()
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
        # a scroll that was going on ends here, without a coast: its history used to survive a pause and launched
        # inertia (about 10 wheel notches) on the first frame after resuming
        self._end_scroll(launch=False)
        self._stop_scroll_inertia()
        self._hold_dragging = False
        self._toggle_dragging = False
        self.anchor_hand_pos = None
        self.anchor_screen_pos = None
        self.grab_anchor = None
        self.recorded_path = []
        self._cancel_playback()
        self._prev_action = "none"
        self._pointer_source = None
        self._reset_velocity_state()
        self._tremor.reset()
        self._abs_monitor = None  # absolute mode re-picks the monitor under the cursor

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
        self._clock = now
        if self.scroll_target_finder is not None and not self._browse_centered:
            self.scroll_target_finder.hint(*self.last_screen_pos)    # lets its thread have the region ready
        if action in ("move_cursor", "pointer_wait") and self._browse_lock(cfg, now):
            # browsing: the hand follows the page, not the cursor
            result = dataclasses.replace(result, action="none", cursor_point=None, is_moving=False)
            action = "none"
        self._lost_since = None
        self.interpolation = bool(cfg.get("cursor_interpolation", False))
        scheme = str(cfg.get("movement_scheme", "legacy_shape")).lower()
        raw_shape = str(getattr(result, "hand_shape", "") or "")
        if raw_shape in ("precise", "normal", "fast"):
            self.hand_shape_mode = raw_shape
        elif raw_shape == "open":
            self.hand_shape_mode = "precise" if scheme == "velocity" else "normal"
        elif raw_shape == "fist":
            self.hand_shape_mode = "fast"
        elif raw_shape == "half":
            self.hand_shape_mode = "normal"
        if scheme == "velocity":
            self.dpi_multiplier = self._effective_gain
        else:
            self.dpi_multiplier = float(getattr(result, "dpi_multiplier", 1.0) or 1.0)
            # Aim assist: slow the cursor near clickable UI (fine moves only: a fist is a fast travel)
            if (self.slowdown_provider is not None and action == "move_cursor"
                    and self.dpi_multiplier <= 1.5 and cfg.get("ui_magnet_enabled", True)):
                self.dpi_multiplier *= self.slowdown_provider(*self.last_screen_pos)
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

        # Layer 1 Explicit Depth / Hand-Size Clutch (`return_suppression` in {"clutch", "clutch_auto"}):
        # Freezes cursor movement when hand is pulled back toward body (`hand_size` shrinks below entry ratio),
        # and re-anchors smoothly without a jump once pushed forward above exit ratio for `clutch_min_hold`.
        clutched = self._update_depth_clutch(result, cfg, now)

        # When not in hand-following movement, clear relative clutch anchor
        if not result.is_moving or action == "grab" or clutched:
            self._tremor.reset()
            self.anchor_hand_pos = None
            self.anchor_screen_pos = None
            self._raw_hand_prev = None
            self._raw_hand_speed = 0.0
            if self._ret_pending_steps:
                self._ret_pending_steps.clear()
            if self.return_state == "confirmed":
                self.return_state = "released"
            elif self.return_state == "pending":
                self.return_state = "none"
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
        elif clutched and action == "move_cursor":
            pass  # Layer 1 depth clutch active: cursor frozen in place
        elif (
            index_tip is None and result.is_moving and result.cursor_point is not None and action != "drag_hold"
            and self._double_click_hold(result, cfg, now)
        ):
            pass  # cursor held for a possible second click
        elif result.is_moving and result.cursor_point is not None:
            use_tip = (index_tip is not None and action in ("move_cursor", "drag_hold")
                       and result.active_gesture != "fist")
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
