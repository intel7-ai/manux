"""Feature `pinch`: palm cursor, thumb pinch clicks, grab scroll, drag and the two window gestures (S4, from v0.3).

Ported from v0.3 `hand-mouse/src/gesture_engine.py` `GestureEngine.process` (engine.py; parity with v0.3 on the
same landmark sequences: tests/unit/features/test_pinch_parity.py). Everything here shares one state
machine (the thumb+index hysteresis, the "one side pinch at a time" rule, click lockouts after a grab, the
show-desktop / task-switcher mutual lockout), so per D-003 it is one feature, window gestures included.

Gestures (v0.3 frame action -> intent):

| gesture id               | phase              | when (v0.3 action / gesture)                            | point        | default binding |
| ------------------------ | ------------------ | ------------------------------------------------------- | ------------ | --------------- |
| `pinch.move`             | hold               | `move_cursor` (no pinch, any hand shape but a fist)     | palm anchor  | `mouse.move` |
| `pinch.move_fist`        | hold               | `move_cursor` from a fist (v0.3 x`fist_dpi_multiplier`) | palm anchor  | `mouse.move` |
| `pinch.thumb_middle`     | tap                | `left_click`                                            | None         | `mouse.left_click` |
| `pinch.thumb_ring`       | tap                | `right_click`                                           | None         | `mouse.right_click` |
| `pinch.thumb_pinky`      | tap                | `task_switcher`                                         | None         | `window.task_switcher` |
| `pinch.thumb_tucked_open`| tap                | `show_desktop` (thumb tucked, four fingers open, held)  | None         | `window.show_desktop` |
| `pinch.scroll`           | begin / hold / end | `scroll_grab` frames (thumb + index)                    | palm anchor  | `mouse.scroll` |
| `pinch.drag`             | begin / hold / end | `drag_hold` (thumb+index+middle, or a long thumb+middle) | palm anchor | `mouse.drag` |

Phase rules:
- A span (`pinch.scroll`, `pinch.drag`) ends on the first frame with a hand whose action is something else.
  The `end` carries the last point sent. A frame without a hand emits nothing and keeps the span open (the
  pinch state survives short losses, as in v0.3); the engine calls `reset("hand_lost")` after
  `app.hand_lost_reset`. `reset()` emits nothing: the engine's `release_all()` closes open spans
  (contracts section 5 rule 2).
- While the scroll grab is releasing (fingers opening, inside the release grace) no `hold` is sent, so the
  page does not follow the hand's spring-back (v0.3 ignored the hand motion then). `end` follows when the
  release is confirmed.
- The thumbs-up (motion mode entry pose) gives no intent: the cursor stays (v0.3 `motion_pose_freeze`).

Speed: every move and drag intent carries `Intent.scale`, the v0.3 per-frame multiplier: the hand-shape speed
for `pinch.move` (1 = open hand, up to `fist_dpi_multiplier` for a curled hand), `fist_dpi_multiplier` for
`pinch.move_fist`, and `long_press_drag_multiplier` (15) for a long-press `pinch.drag`. The router multiplies
it into the binding's `gain` (contracts sections 5 and 7), so changing those settings changes the speed.

Not ported: Record & Fling (thumb+index `grab`, off by default in v0.3). The Alt-held task switcher of v0.3
(hand navigation inside the switcher) is not here: `window.task_switcher` opens the Windows picker with Ctrl+Alt+Tab (contracts section 7).
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from manux.core.contracts import Activity, FrameContext, HandObservation, Intent
from manux.features.pinch.engine import PinchCore, PinchFrame
from manux.features.pinch.settings import DEFAULTS, SETTINGS, V03_KEYS, resolve
from manux.perception.palm_axes import corrected_point

__all__ = ["ID", "GESTURES", "GESTURE_PHASES", "DEFAULT_BINDINGS", "SETTINGS", "V03_KEYS", "PinchFeature",
           "IntentMapper"]

ID = "pinch"
MODE = "pinch"

MOVE = "pinch.move"
MOVE_FIST = "pinch.move_fist"
THUMB_MIDDLE = "pinch.thumb_middle"
THUMB_RING = "pinch.thumb_ring"
THUMB_PINKY = "pinch.thumb_pinky"
THUMB_TUCKED_OPEN = "pinch.thumb_tucked_open"
SCROLL = "pinch.scroll"
DRAG = "pinch.drag"

GESTURES: Tuple[str, ...] = (MOVE, MOVE_FIST, THUMB_MIDDLE, THUMB_RING, THUMB_PINKY, THUMB_TUCKED_OPEN, SCROLL, DRAG)
_SPAN = frozenset({"begin", "hold", "end"})
_TAP = frozenset({"tap"})
GESTURE_PHASES: Mapping[str, frozenset] = {
    MOVE: frozenset({"hold"}), MOVE_FIST: frozenset({"hold"}),
    THUMB_MIDDLE: _TAP, THUMB_RING: _TAP, THUMB_PINKY: _TAP, THUMB_TUCKED_OPEN: _TAP,
    SCROLL: _SPAN, DRAG: _SPAN,
}
DEFAULT_BINDINGS: Mapping[str, Any] = {
    MOVE: "mouse.move",
    MOVE_FIST: "mouse.move",          # speed comes from Intent.scale (fist_dpi_multiplier), not a fixed gain
    THUMB_MIDDLE: "mouse.left_click",
    THUMB_RING: "mouse.right_click",
    THUMB_PINKY: "window.task_switcher",
    THUMB_TUCKED_OPEN: "window.show_desktop",
    SCROLL: "mouse.scroll",
    DRAG: "mouse.drag",
}
_TAPS = {"left_click": THUMB_MIDDLE, "right_click": THUMB_RING, "task_switcher": THUMB_PINKY,
         "show_desktop": THUMB_TUCKED_OPEN}
_SPANS = {"scroll_grab": SCROLL, "drag_hold": DRAG}


def _scale(multiplier: Any) -> Optional[float]:
    """The v0.3 per-frame multiplier as Intent.scale (None for 1 or an unusable value)."""
    try:
        m = float(multiplier)
    except (TypeError, ValueError):
        return None
    return m if math.isfinite(m) and m > 0 and m != 1.0 else None


class IntentMapper:
    """Turns v0.3-shaped frame results (`PinchFrame`, or a v0.3 `GestureResult` in the parity test) into intents.

    Keeps only the open span and its last point. `frame=None` means "no hand this frame"."""

    def __init__(self) -> None:
        self.open: Optional[str] = None
        self.point: Optional[Tuple[float, float]] = None
        self.scale: Optional[float] = None

    def clear(self) -> None:
        self.open = None
        self.point = None
        self.scale = None

    def map(self, t: float, frame: Any) -> List[Intent]:
        if frame is None:
            return []
        out: List[Intent] = []
        action = frame.action
        span = _SPANS.get(action)
        if self.open is not None and span != self.open:
            out.append(Intent(t, ID, self.open, "end", point=self.point, scale=self.scale))
            self.open = None
            self.scale = None
        if span is not None:
            info: Dict[str, Any] = {"gesture": frame.active_gesture}
            # long-press drag moves at long_press_drag_multiplier (v0.3); scroll speed is the scroll's own gain
            scale = _scale(frame.dpi_multiplier) if span == DRAG else None
            if self.open is None:
                self.point = frame.cursor_point
                self.open = span
                self.scale = scale
                out.append(Intent(t, ID, span, "begin", point=self.point, info=info, scale=scale))
            elif span == SCROLL and frame.releasing:
                pass                                   # page frozen while the fingers open
            else:
                self.point = frame.cursor_point
                self.scale = scale
                out.append(Intent(t, ID, span, "hold", point=self.point, info=info, scale=scale))
        elif action == "move_cursor" and frame.cursor_point is not None:
            fist = frame.active_gesture == "fist"
            out.append(Intent(t, ID, MOVE_FIST if fist else MOVE, "hold", point=frame.cursor_point,
                              info={"hand_shape": frame.hand_shape}, scale=_scale(frame.dpi_multiplier)))
        elif action in _TAPS:
            out.append(Intent(t, ID, _TAPS[action], "tap"))
        return out


class PinchFeature:
    id = ID
    modes = frozenset({MODE})
    gestures = GESTURES

    def __init__(self) -> None:
        self.core = PinchCore()
        self.mapper = IntentMapper()
        self.last_frame: Optional[PinchFrame] = None     # diagnostics for the Snapshot / failure log (read only)

    # ------------------------------------------------------------------ Feature protocol
    def step(self, hand: Optional[HandObservation], ctx: FrameContext) -> Sequence[Intent]:
        t = ctx.frame.t
        settings = resolve(ctx.settings)
        if hand is None:
            self.core.step(t, None, settings)
            self.last_frame = None
            return []
        frame = self.core.step(t, hand.landmarks, settings)
        if frame.action in ("move_cursor", "drag_hold"):
            frame = replace(frame, cursor_point=corrected_point(frame.cursor_point, ctx.frame.aspect,
                                                              ctx.settings.get("palm_axes")))
        self.last_frame = frame
        return self.mapper.map(t, frame)

    def activity(self) -> Activity:
        last = self.core.last_release
        return Activity(holding=self.core.pressing or self.mapper.open is not None,
                        last_release_t=last if last > -1e8 else -math.inf)

    def reset(self, reason: str) -> None:
        self.core.reset()
        self.mapper.clear()
        self.last_frame = None
