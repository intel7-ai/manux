"""Cursor mapping: normalized point (0..1, image coords) -> screen pixels. No OS calls.

Ported from v0.3 `MouseController.map_to_screen_absolute`, `_relative_step`, `_compute_target_screen_pos`,
`absolute_monitor`, `cycle_monitor`, `warp_to`.

- relative (default): trackpad-like deltas with pointer acceleration. Scale each axis by the
  extended desktop span relative to the primary monitor, preserving camera aspect correction.
- absolute: the camera's active box maps onto one monitor (the one under the cursor until
  `next_monitor()` picks another). `gain` != 1 adds (gain - 1) x each hand delta to an offset so the
  cursor moves faster without a jump when the gain changes.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from manux.output.windows.config import WindowsOutputConfig
from manux.platform.monitors import Monitor, ScreenLayout


class OneEuro:
    """One Euro filter (Casiez et al. 2012): heavy smoothing while still, little lag while moving.

    A fixed EMA must trade resting jitter for lag; the lag makes people overshoot and correct back."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self.value: Optional[float] = None
        self._speed = 0.0
        self._t = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, value: float, now: float) -> float:
        dt = now - self._t
        if self.value is None or not 0 < dt <= .5:
            self.value, self._speed, self._t = value, 0.0, now
            return value
        self._t = now
        speed = (value - self.value) / dt
        self._speed += self._alpha(self.d_cutoff, dt) * (speed - self._speed)
        cutoff = self.min_cutoff + self.beta * abs(self._speed)
        self.value += self._alpha(cutoff, dt) * (value - self.value)
        return self.value


# Palm input filters. Hand X is in frame widths (a deliberate move is ~0.3/s), pitch in degrees (~60°/s);
# both betas add ~3-5 Hz of bandwidth at those speeds. `smoothing` sets the resting cutoff.
PALM_X_BETA = 12.0
PALM_PITCH_BETA = .07
CLICK_GUARD_CLOSE = 1.3    # click contact ratio below this holds the palm cursor (no frame of real use went this low)
CLICK_GUARD_CLOSING_DROP = .4   # ... or when it falls this much in one frame (the hand tilts while fingers close,
CLICK_GUARD_CLOSING_BELOW = 3.  # 1-2 frames before they touch), counted only once it is this close
CLICK_GUARD_MAX_HOLD = .3       # recorded clicks touched 0.14-0.27 s; longer contact is a drag and moves again
CLICK_GUARD_SETTLE = .2  # ... and it keeps holding this long after the fingers part (the pitch jumps back)
PALM_PITCH_CUTOFF_SCALE =.5   # wrist pitch is ~2x noisier than hand X at rest (fine traces): filter it harder
PALM_PITCH_JUMP = 90.0  # deg between frames: a wrap or a bad landmark frame, not a hand movement
PALM_EASE_SECONDS = .2   # after a pause the calibrated cursor glides from where it was to the hand


PALM_HOLD_SECONDS = .2     # hand inside the neutral zone this long -> the cursor parks
PALM_HOLD_EXIT = 1.5       # leave the zone by this many radii to move again (hysteresis)
PALM_RELEASE_TAU = .5      # s: after leaving, the cursor continues from where it parked instead of jumping...
PALM_RELEASE_CATCHUP = .5  # ...and moves this much extra per pixel of hand movement until it is back on the hand
PALM_RELEASE_SNAP = .25    # one-frame hand jumps beyond this share of screen height are followed directly


# Gain curve: g(v) = precision + (1 - precision) * min(1, v / FULL) ** CURVE (v = mapped hand speed, px/s).
# With precision 0.4: 100 px/s -> 0.49, 300 -> 0.61, 600 -> 0.79, 1000+ -> 1. The earlier flat 0.4 up to 650 px/s
# (then a ramp) kept card drags and ordinary moves at 0.4x: a screen recording showed the cursor never above
# ~360 px/s and mostly creeping, which felt heavy. A smooth curve has no cliff for a flick to fall over.
PALM_PRECISION_FULL = 1000.0
PALM_PRECISION_CURVE = .85
PALM_PRECISION_SLOW = 650.0     # px/s: re-sync with the mapping starts above this...
PALM_PRECISION_FAST = 1100.0    # ... and is full above this
PALM_PRECISION_RESYNC = .25     # s to close the gap to the mapping while moving fast
PALM_PRECISION_EDGE_PX = 8.0    # a hand mapped this close to a screen edge puts the cursor on the mapping there
PALM_PRECISION_MAX_GAP = .12   # the cursor never sits further than this share of screen height off the mapping


def palm_min_cutoff(smoothing: float) -> float:
    # Snappy on purpose: low-speed precision (palm_precision) now absorbs the resting wobble, and a slow filter
    # left the cursor sliding on after the hand stopped (felt like momentum).
    return 1.0 + 3.0 * max(0.05, min(1.0, float(smoothing)))


class CursorMapper:
    def __init__(self, screens: ScreenLayout, config: WindowsOutputConfig,
                 start: Optional[Tuple[int, int]] = None) -> None:
        self.screens = screens
        self.config = config
        self.pos: Tuple[int, int] = start if start is not None else screens.primary.center
        self._anchor: Optional[Tuple[float, float]] = None      # smoothed hand point (relative)
        self._rel_pos: Tuple[float, float] = (float(self.pos[0]), float(self.pos[1]))
        self._rel_t = 0.0
        self._smoothed: Optional[Tuple[float, float]] = None    # absolute
        self._abs_monitor: Optional[Monitor] = None
        self._abs_offset: Tuple[float, float] = (0.0, 0.0)
        self._abs_prev: Optional[Tuple[float, float]] = None
        self._rebase = False
        self._palm_pos: Optional[Tuple[float, float]] = None
        self._palm_pitch: Optional[float] = None
        self._palm_tracking = False
        self._palm_x = OneEuro(1.0, PALM_X_BETA)
        self._palm_y = OneEuro(1.0, PALM_PITCH_BETA)
        self._palm_ease_until = 0.0
        self._guard_until = float("-inf")
        self._last_contact: Optional[float] = None
        self._contact_since: Optional[float] = None
        self._guard_rebase = False
        self._capture_offset = False
        self._map_offset: Tuple[float, float] = (0.0, 0.0)
        self._reset_hold()
        self._reset_precision()

    def _reset_precision(self, pos: Optional[Tuple[float, float]] = None) -> None:
        self._prec_pos: Optional[Tuple[float, float]] = pos     # cursor under low-speed precision
        self._prec_target: Optional[Tuple[float, float]] = None
        self._prec_t = 0.0

    def _reset_hold(self) -> None:
        self._hold_pos: Optional[Tuple[float, float]] = None   # where the cursor is parked
        self._hold_ref: Tuple[float, float] = (0.0, 0.0)       # hand target when it parked
        self._hold_anchor: Optional[Tuple[float, float]] = None
        self._hold_since = 0.0
        self._hold_offset: Tuple[float, float] = (0.0, 0.0)
        self._hold_t: Optional[float] = None
        self._hold_last: Optional[Tuple[float, float]] = None

    @property
    def parked(self) -> bool:
        return self._hold_pos is not None

    @property
    def anchored(self) -> bool:
        return self._anchor is not None or self._palm_pos is not None

    # ------------------------------------------------------------------ state changes
    def pause(self) -> None:
        """The hand stopped driving the cursor: the next point starts fresh (no jump)."""
        self._anchor = None
        self._palm_pos = None
        self._palm_x.reset()
        self._palm_y.reset()
        self._reset_hold()
        # keep where precision put the cursor (a click or a short pause must not snap it back to the mapping);
        # only forget the last hand point so the gap in time is not read as one big move
        self._prec_target = None
        self._abs_prev = None
        if self.config.movement_mode != "absolute":
            self._smoothed = None

    def warp(self, x: int, y: int, rebase: bool = False) -> None:
        """The cursor is at (x, y) now: relative movement continues from there.

        Absolute mode maps the hand straight back unless `rebase` shifts the mapping so the cursor stays here."""
        self.pos = (int(x), int(y))
        self._anchor = None
        self._palm_pos = None
        self._palm_x.reset()
        self._palm_y.reset()
        self._reset_hold()
        self._reset_precision((float(x), float(y)))
        self._abs_prev = None
        self._smoothed = None
        self._rebase = bool(rebase)

    def reset(self) -> None:
        self.pause()
        self._reset_precision()
        self._map_offset, self._capture_offset = (0.0, 0.0), False
        self._abs_monitor = None  # absolute re-picks the monitor under the cursor

    @property
    def absolute_monitor(self) -> Monitor:
        if self._abs_monitor is None or self._abs_monitor not in self.screens.monitors:
            self._abs_monitor = self.screens.nearest(*self.pos)
        return self._abs_monitor

    def next_monitor(self) -> Monitor:
        """Jump to the centre of the next monitor; absolute mode maps onto it from now on."""
        target = self.screens.next_after(*self.pos)
        self._abs_monitor = target
        self._abs_offset = (0.0, 0.0)
        self.warp(*target.center)
        return target

    # ------------------------------------------------------------------ mapping
    def map_absolute(self, nx: float, ny: float, clamp: bool = True) -> Tuple[float, float]:
        """Camera box [margin, 1 - margin] -> the absolute monitor. clamp=False maps the margin beyond the edges."""
        margin = max(0.0, min(0.45, float(self.config.active_margin)))
        span = max(1e-6, 1.0 - 2.0 * margin)
        x = (float(nx) - margin) / span
        y = (float(ny) - margin) / span
        if clamp:
            x = max(0.0, min(1.0, x))
            y = max(0.0, min(1.0, y))
        m = self.absolute_monitor
        return m.left + x * (m.width - 1), m.top + y * (m.height - 1)

    def step(self, point: Tuple[float, float], now: float, gain: float = 1.0) -> Tuple[int, int]:
        """New cursor position for this hand point; also stored in `pos`."""
        if self.config.movement_mode == "absolute":
            self.pos = self._absolute(point, gain)
        else:
            self.pos = self._relative(point, now, gain)
        return self.pos

    def step_palm(self, x: float, pitch: Optional[float], now: float, gain: float = 1.0,
                  contact: Optional[float] = None, fist: bool = False) -> Tuple[int, int]:
        """Use hand X and wrist pitch for both the practice cursor and Windows output.

        `contact` is how close a click is (thumb to middle/ring tip over the click threshold; below 1 = touching).
        While the fingers touch, and briefly after they part, the cursor holds still: recorded clicks showed the
        measured wrist pitch jumping 5-7 deg in the very frame the fingers met, i.e. a 30-60 px cursor jump."""
        closing = (contact is not None and self._last_contact is not None and contact < CLICK_GUARD_CLOSING_BELOW
                   and self._last_contact - contact > CLICK_GUARD_CLOSING_DROP)
        self._last_contact = contact
        if contact is not None and (contact < CLICK_GUARD_CLOSE or closing):
            if self._contact_since is None:
                self._contact_since = now
            if now - self._contact_since <= CLICK_GUARD_MAX_HOLD:
                self._guard_until = now + CLICK_GUARD_SETTLE
                self._guard_rebase = True
                return self.pos
            # Held longer than any recorded click: a drag. Follow the hand again from here, re-anchored with a new
            # mapping offset (below) so the pinch tilt is absorbed instead of jumping, then move like any other
            # move: precise when slow, 1:1 and re-synced when fast, so a drag across the screen keeps up.
        else:
            if self._contact_since is not None:          # fingers just parted (click or drag end)
                self._contact_since = None
                self._guard_until = max(self._guard_until, now + CLICK_GUARD_SETTLE)
                self._guard_rebase = True
            if now < self._guard_until:
                return self.pos
        if self._guard_rebase:
            self._guard_rebase = False
            self.pause()       # continue from where the cursor is; the tilt that came with the pinch is dropped
            self._capture_offset = True   # ... and take the cursor's spot as the mapping's new reference
        c = self.config
        if c.palm_left == c.palm_right:
            # Uncalibrated: relative movement, smoothed by `_relative`'s own EMA (not filtered twice).
            tracked = pitch is not None
            jumped = (tracked and self._palm_pitch is not None
                      and abs(pitch - self._palm_pitch) > PALM_PITCH_JUMP)
            if tracked != self._palm_tracking or jumped:
                self.pause()
                self._palm_tracking = tracked
            if pitch is not None:
                self._palm_pitch = pitch
            y = -(self._palm_pitch or 0.0) / (150 * c.frame_aspect)
            return self.step((x, y), now, gain)

        if (pitch is not None and self._palm_y.value is not None
                and abs(pitch - self._palm_y.value) > PALM_PITCH_JUMP):
            self.pause()   # a wrap or a bad frame: glide from the cursor instead of sweeping across the screen
        cutoff = palm_min_cutoff(c.smoothing)  # config is reassigned live
        self._palm_x.min_cutoff, self._palm_y.min_cutoff = cutoff, cutoff * PALM_PITCH_CUTOFF_SCALE
        x = self._palm_x(x, now)
        if pitch is not None:
            pitch = self._palm_y(pitch, now)
        monitor = self.screens.primary
        if self._palm_pos is None:
            self._palm_pos = monitor.clamp(*self.pos)
            self._palm_ease_until = now + PALM_EASE_SECONDS
        px, py = self._palm_pos
        fx = (x - c.palm_left) / (c.palm_right - c.palm_left)
        target_x = monitor.left + (.5 + (fx - .5) * c.palm_x_gain) * (monitor.width - 1)
        target_y = py
        if pitch is not None:
            fy = (pitch - c.palm_up) / (c.palm_down - c.palm_up)
            target_y = monitor.top + (.5 + (fy - .5) * c.palm_y_gain) * (monitor.height - 1)
        target_x, target_y = monitor.clamp(target_x, target_y)
        # Mapping offset: after a click or drag the cursor's spot becomes the reference instead of being pulled
        # back to the mapped spot (that pull looked like the cursor sliding after a drop). It fades only while the
        # hand moves fast (see _precision), and a hand pushed to a screen edge drops it on that axis.
        if self._capture_offset:
            self._capture_offset = False
            base = self._prec_pos if self._prec_pos is not None else self.pos
            self._map_offset = (base[0] - target_x, base[1] - target_y)
        ox, oy = self._map_offset
        edge = PALM_PRECISION_EDGE_PX
        if target_x <= monitor.left + edge or target_x >= monitor.left + monitor.width - 1 - edge:
            ox = 0.0
        if target_y <= monitor.top + edge or target_y >= monitor.top + monitor.height - 1 - edge:
            oy = 0.0
        self._map_offset = (ox, oy)
        target_x, target_y = monitor.clamp(target_x + ox, target_y + oy)
        target_x, target_y = monitor.clamp(*self._palm_hold((target_x, target_y), now, monitor.height))
        # Only the fist gesture is precision: the open-hand gain also varies with finger curl (2.0 open .. 0.35
        # fist), and a relaxed, half-curled hand must not drop into fist mode mid-move.
        target_x, target_y = monitor.clamp(*self._precision((target_x, target_y), now, gain if fist else 1.0))
        # The filters already smooth the hand; only glide in after a restart so the cursor never jumps.
        alpha = .25 if now < self._palm_ease_until else 1.0
        self._palm_pos = (px + alpha * (target_x - px), py + alpha * (target_y - py))
        self.pos = tuple(round(value) for value in self._palm_pos)
        return self.pos

    def _precision(self, target: Tuple[float, float], now: float, gain: float = 1.0) -> Tuple[float, float]:
        """Precision at low speed on top of the calibrated (absolute) mapping.

        A slow hand moves the cursor `palm_precision` x as far as the mapping says (drift, tremor and the small
        tilt of a pinch shrink with it); a fast hand moves it 1:1 and pulls it back onto the mapping, so a
        quick sweep still reaches every edge and the hand-to-screen relation never drifts away for long.
        The gap to the mapping is capped at PALM_PRECISION_MAX_GAP x screen height.

        A command gain below 1 is the fist (precision gesture): every move is scaled by it, whatever the
        speed, and the cursor does not re-sync, so it stays where the fist put it. Gains above 1 belong to
        the uncalibrated relative mode and are ignored here."""
        fist = gain < 1.0
        g_low = max(0.05, min(1.0, float(self.config.palm_precision)))
        if fist:
            g_low = max(0.05, min(g_low, float(gain)))
        prev, prev_t = self._prec_target, self._prec_t
        self._prec_target, self._prec_t = target, now
        if self._prec_pos is None or (g_low >= 1.0 and not fist):
            self._prec_pos = target
            return target
        if prev is None:                  # first point after a pause: continue from where the cursor is
            return self._prec_pos
        dt = max(1e-3, min(.1, now - prev_t))
        dx, dy = target[0] - prev[0], target[1] - prev[1]
        if fist:
            x, y = self._prec_pos[0] + dx * g_low, self._prec_pos[1] + dy * g_low
        else:
            speed = math.hypot(dx, dy) / dt
            # Gain rises smoothly and continuously with speed (like pointer acceleration), so ordinary moves are
            # light and only the slowest ones are damped; re-sync to the mapping still only happens when fast.
            k = g_low + (1.0 - g_low) * min(1.0, speed / PALM_PRECISION_FULL) ** PALM_PRECISION_CURVE
            u = max(0.0, min(1.0, (speed - PALM_PRECISION_SLOW) / (PALM_PRECISION_FAST - PALM_PRECISION_SLOW)))
            u = u * u * (3 - 2 * u)
            x, y = self._prec_pos[0] + dx * k, self._prec_pos[1] + dy * k
            # only a fast hand re-syncs: a slow or resting one never drags the cursor on after it stops
            pull = min(1.0, u * dt / PALM_PRECISION_RESYNC)
            x, y = x + (target[0] - x) * pull, y + (target[1] - y) * pull
            if pull:                      # a fast hand also fades the click/drag reference offset
                self._map_offset = (self._map_offset[0] * (1 - pull), self._map_offset[1] * (1 - pull))
        # The mapping clamps the hand at the screen edge, so pushing further gives no more movement: a hand at an
        # edge puts the cursor on that edge, or an edge button (taskbar, close) could stay out of reach.
        m = self.screens.primary
        edge = PALM_PRECISION_EDGE_PX   # the filtered hand only approaches the range end, never quite reaches it
        if target[0] <= m.left + edge or target[0] >= m.left + m.width - 1 - edge:
            x = target[0]
        if target[1] <= m.top + edge or target[1] >= m.top + m.height - 1 - edge:
            y = target[1]
        gap = math.dist((x, y), target)
        limit = PALM_PRECISION_MAX_GAP * self.screens.primary.height
        if gap > limit:
            x, y = target[0] + (x - target[0]) * limit / gap, target[1] + (y - target[1]) * limit / gap
        self._prec_pos = (x, y)
        return self._prec_pos

    def _palm_hold(self, target: Tuple[float, float], now: float, height: int) -> Tuple[float, float]:
        """Neutral zone: a hand that stays within `palm_hold` (share of screen height) parks the cursor.

        It moves again only once the hand leaves PALM_HOLD_EXIT x that radius, and then continues from the
        parked spot (the gap closes over PALM_RELEASE_TAU) so leaving the zone never makes the cursor jump."""
        radius = max(0.0, float(self.config.palm_hold)) * height
        dt = 0.0 if self._hold_t is None else max(0.0, now - self._hold_t)
        self._hold_t = now
        ox, oy = self._hold_offset
        moved = 0.0 if self._hold_last is None else math.dist(target, self._hold_last)
        self._hold_last = target
        if ox or oy:
            # Close the gap mostly while the hand moves (the cursor runs PALM_RELEASE_CATCHUP faster than the
            # hand until it is back on it), and slowly while it rests. A huge one-frame move just follows.
            gap = math.hypot(ox, oy)
            left = (gap - PALM_RELEASE_CATCHUP * moved) * math.exp(-dt / PALM_RELEASE_TAU)
            if left < .5 or moved > PALM_RELEASE_SNAP * height:
                left = 0.0
            ox, oy = (ox * left / gap, oy * left / gap) if left else (0.0, 0.0)
            self._hold_offset = (ox, oy)
        if radius <= 0:
            self._hold_pos = None
            return target[0] + ox, target[1] + oy
        if self._hold_pos is not None:
            if math.dist(target, self._hold_ref) <= PALM_HOLD_EXIT * radius:
                return self._hold_pos
            hx, hy = self._hold_pos
            self._hold_offset = (hx - target[0], hy - target[1])
            self._hold_pos, self._hold_anchor, self._hold_since = None, target, now
            return hx, hy
        if self._hold_anchor is None or math.dist(target, self._hold_anchor) > radius:
            self._hold_anchor, self._hold_since = target, now
        elif now - self._hold_since >= PALM_HOLD_SECONDS and not (ox or oy):
            # (not while still catching up after leaving the zone: keep following until on the hand)
            self._hold_pos, self._hold_ref = target, target
            return self._hold_pos
        return target[0] + ox, target[1] + oy

    def _relative(self, point: Tuple[float, float], now: float, gain: float) -> Tuple[int, int]:
        c = self.config
        smoothing = max(0.05, min(1.0, float(c.smoothing)))
        g_min = max(0.0, float(c.relative_gain_min))
        g_max = max(g_min, float(c.relative_gain_max))
        v_lo = max(0.0, float(c.relative_accel_speed_low))
        v_hi = max(v_lo + 1e-6, float(c.relative_accel_speed_high))
        hx, hy = float(point[0]), float(point[1])
        if self._anchor is None:
            # (Re)start from wherever the cursor is: no jump after a click, a pause or hand loss
            self._anchor = (hx, hy)
            self._rel_pos = (float(self.pos[0]), float(self.pos[1]))
            self._rel_t = now
            self._rebase = False
            return self.pos
        ax, ay = self._anchor
        sx = ax + smoothing * (hx - ax)  # EMA on the hand point suppresses landmark jitter
        sy = ay + smoothing * (hy - ay)
        self._anchor = (sx, sy)
        dt = max(1e-3, min(0.1, now - self._rel_t))
        self._rel_t = now
        dx = sx - ax
        dy = (sy - ay) * c.frame_aspect  # physical units (frame widths)
        speed = math.hypot(dx, dy) / dt
        t = max(0.0, min(1.0, (speed - v_lo) / (v_hi - v_lo)))
        g = (g_min + (g_max - g_min) * t) * gain
        bounds = self.screens.bounds
        primary = self.screens.primary
        vertical_scale = primary.width * bounds.height / primary.height
        self._rel_pos = bounds.clamp(self._rel_pos[0] + dx * bounds.width * g,
                                     self._rel_pos[1] + dy * vertical_scale * g)
        px, py = self.screens.clamp(*self._rel_pos)
        return int(round(px)), int(round(py))

    def _absolute(self, point: Tuple[float, float], gain: float) -> Tuple[int, int]:
        smoothing = max(0.05, min(1.0, float(self.config.smoothing)))
        mx, my = self.map_absolute(point[0], point[1], clamp=False)
        if self._rebase:
            # after a warp: shift the mapping so the cursor stays put
            self._abs_offset = (float(self.pos[0]) - mx, float(self.pos[1]) - my)
            self._smoothed = (float(self.pos[0]), float(self.pos[1]))
            self._rebase = False
        elif gain != 1.0 and self._abs_prev is not None:
            k = gain - 1.0
            self._abs_offset = (self._abs_offset[0] + (mx - self._abs_prev[0]) * k,
                                self._abs_offset[1] + (my - self._abs_prev[1]) * k)
        self._abs_prev = (mx, my)
        mon = self.absolute_monitor
        raw_x, raw_y = mon.clamp(mx + self._abs_offset[0], my + self._abs_offset[1])
        if self._smoothed is None:
            sm_x, sm_y = raw_x, raw_y
        else:
            sm_x = self._smoothed[0] + smoothing * (raw_x - self._smoothed[0])
            sm_y = self._smoothed[1] + smoothing * (raw_y - self._smoothed[1])
        self._smoothed = (sm_x, sm_y)
        fx, fy = mon.clamp(sm_x, sm_y)
        return int(round(fx)), int(round(fy))
