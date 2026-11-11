"""
Human-readable gesture status, shared by the camera HUD and the tiny corner overlay.

Colours are RGB tuples; the OpenCV HUD converts them to BGR.
"""

from __future__ import annotations

from typing import Tuple

RGB = Tuple[int, int, int]

# Same colours the HUD always used (they were BGR literals), written here as RGB.
MOVE: RGB = (128, 255, 0)
IDLE: RGB = (200, 200, 200)
PAUSE: RGB = (255, 180, 0)
FAST: RGB = (255, 200, 0)
SCROLL: RGB = (0, 220, 255)
RECORD: RGB = (0, 200, 255)
DRAG: RGB = (255, 160, 80)
SWITCH: RGB = (255, 120, 255)
ACTION: RGB = (255, 120, 0)
HELD: RGB = (180, 230, 0)
NO_HAND: RGB = (150, 150, 150)


def describe_state(
    result,
    *,
    paused: bool = False,
    switcher_open: bool = False,
    recent_action: str = "",
    grab_speed: float = 0.0,
    mode: str = "RELATIVE",
) -> Tuple[str, RGB]:
    """Long status line for the camera HUD."""
    color = MOVE if result.is_moving else IDLE
    if paused:
        return "PAUSED (Press SPACE to resume)", PAUSE
    if switcher_open:
        return "TASK SWITCHER: point at a window | thumb+middle = open | thumb+ring = cancel", SWITCH
    if result.action == "move_cursor" and result.active_gesture == "fist":
        return f"FAST MOVE x{result.dpi_multiplier:.1f} (Fist / {mode})", FAST
    if result.action == "move_cursor":
        return f"FINE MOVE (No Pinch / {mode})", color
    if result.action == "scroll_grab":
        return "SCROLLING - page grabbed (Thumb + Index), move the hand to scroll", SCROLL
    if result.action == "grab":
        return f"RECORDING - draw, release to fling  [speed {grab_speed:.2f}]", RECORD
    if result.action == "drag_hold":
        return "DRAGGING (left button held) - release the pinch to drop", DRAG
    if recent_action:
        # shown for a moment after it fires (a single frame was unreadable)
        return f"ACTION: {recent_action.upper()}", ACTION
    if result.active_gesture in ("thumb_middle_pinch", "thumb_ring_pinch", "thumb_pinky_pinch"):
        finger = result.active_gesture.split("_")[1].upper()
        return f"PINCH HELD: {finger} (open the fingers to click again)", HELD
    if result.distances:
        near = min(("index", "middle", "ring", "pinky"), key=lambda f: result.distances.get(f, 9.9))
        return f"AIMING: {near.upper()} near the thumb (cursor held for a precise click)", color
    return "NO HAND", color


def short_label(
    result,
    *,
    has_hand: bool,
    paused: bool = False,
    switcher_open: bool = False,
    recent_action: str = "",
) -> Tuple[str, RGB]:
    """Few-word status for the corner overlay."""
    if paused:
        return "PAUSED", PAUSE
    if not has_hand:
        return "NO HAND", NO_HAND
    if switcher_open:
        return "SWITCHER", SWITCH
    if result.action == "drag_hold":
        return "DRAG", DRAG
    if result.action == "scroll_grab":
        return "SCROLL", SCROLL
    if result.action == "grab":
        return "RECORD", RECORD
    if recent_action:
        return recent_action.replace("_", " ").upper(), ACTION
    if result.action == "move_cursor" and result.active_gesture == "fist":
        return "FAST MOVE", FAST
    if result.action == "move_cursor":
        return "TRACKING", MOVE
    if result.active_gesture in ("thumb_middle_pinch", "thumb_ring_pinch", "thumb_pinky_pinch"):
        return "PINCH " + result.active_gesture.split("_")[1].upper(), HELD
    return "TRACKING", MOVE
