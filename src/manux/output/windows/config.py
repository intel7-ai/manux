"""Settings of the Windows sink. Defaults are the v0.3 values (`hand-mouse/config/gestures.json`).

`settings/` (S1) owns the file format; `app.py` builds this from it. Names keep the v0.3 key names so the
S1 conversion table can map them one to one.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowsOutputConfig:
    # cursor mapping
    movement_mode: str = "relative"          # "relative" (trackpad with acceleration) | "absolute"
    smoothing: float = 0.35                  # EMA on the hand point (1.0 = none)
    active_margin: float = 0.3               # absolute: camera border that maps beyond the screen edge
    relative_gain_min: float = 0.35          # relative: gain at slow hand speed (screen widths per frame width)
    relative_gain_max: float = 1.2           # ... at fast hand speed
    relative_accel_speed_low: float = 0.1    # frame widths / s where acceleration starts
    relative_accel_speed_high: float = 0.8   # ... and where it reaches the max gain
    frame_aspect: float = 0.75               # camera height / width: y motion is compared physically
    reanchor_gap: float = 0.1                # s without a movement command -> the next one starts fresh (new in v0.4)
    palm_left: float = 0.0
    palm_right: float = 0.0                  # same as palm_left until calibrated
    palm_up: float = 0.0                     # camera-relative degrees
    palm_down: float = 0.0
    palm_x_gain: float = 1.0
    palm_y_gain: float = 1.0
    # double-click assist: the cursor holds still after a left click so a second click lands on the same pixel
    double_click_window: float = 0.45
    double_click_move_tolerance: float = 0.03
    # grab scroll (touchscreen style) and inertia
    scroll_gain: float = 8000.0              # wheel units per frame width of hand motion (calm & controllable)
    scroll_deadzone: float = 0.01
    scroll_invert: bool = False
    scroll_inertia: bool = True
    scroll_inertia_time: float = 0.25        # s, exponential slow-down
    scroll_inertia_min_speed: float = 600.0  # wheel units / s needed to coast
    scroll_inertia_max_speed: float = 1200.0
    scroll_inertia_axis_lock: float = 0.5
    scroll_smooth_tau: float = 0.03          # ScrollPump spreading time (0 = send at once)
