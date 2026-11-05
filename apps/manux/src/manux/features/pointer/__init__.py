"""Feature `pointer`: the index (or palm) pointer with pinch / trigger clicks (S2, from v0.3).

Ported from v0.3 `hand-mouse/src/pointer_engine.py` + `pointer_workspace.py` (engine.py,
workspace.py) with the pinch detector it needs (pinch.py). Parity with v0.3 on the same landmark
sequences: tests/unit/features/test_pointer_parity.py.

Gestures (v0.3 `PointerFrame.action` -> intent):

| gesture id            | phase              | when (v0.3 action)                          | point            |
| --------------------- | ------------------ | ------------------------------------------- | ---------------- |
| `pointer.move`        | hold               | `move_cursor`; also the trigger rewind      | calibrated point |
| `pointer.left_click`  | tap                | `left_click`                                | None (in place)  |
| `pointer.right_click` | tap                | `right_click`                               | None (in place)  |
| `pointer.drag`        | begin / hold / end | `drag_hold` frames (trigger style only)     | calibrated point |

`pointer_wait` / `pointer_release` (cursor frozen, click pending) give no intent: the cursor stays.
`pointer.drag` ends on the first frame that is not `drag_hold`, including a frame without a hand.
`reset()` emits nothing; the engine's `release_all()` closes an open drag (contracts section 5 rule 2).

Trigger rewind (v0.3 `warp`): v0.3 recorded the real screen cursor (`record_cursor`) and warped
it back on a press. A feature cannot see the screen, so it records the last point it sent and, on
a press, sends `pointer.move` with the rewound point (info `{"warp": True}`). The parity test
feeds v0.3 the same recorded points, so the rewound values match exactly.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from manux.core.contracts import Activity, FrameContext, HandObservation, Intent
from manux.features.pointer.engine import PointerCore, PointerFrame
from manux.features.pointer.settings import SETTINGS, PointerSettings, from_mapping

__all__ = ["ID", "GESTURES", "DEFAULT_BINDINGS", "SETTINGS", "PointerFeature", "PointerSettings"]

ID = "pointer"
MOVE, LEFT_CLICK, RIGHT_CLICK, DRAG = "pointer.move", "pointer.left_click", "pointer.right_click", "pointer.drag"
GESTURES: Tuple[str, ...] = (MOVE, LEFT_CLICK, RIGHT_CLICK, DRAG)
DEFAULT_BINDINGS = {
    MOVE: "mouse.move",
    LEFT_CLICK: "mouse.left_click",
    RIGHT_CLICK: "mouse.right_click",
    DRAG: "mouse.drag",
}
_TAPS = {"left_click": LEFT_CLICK, "right_click": RIGHT_CLICK}


class _Point:
    """Landmark with `.x .y .z`, the shape the ported v0.3 code reads."""
    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z


class PointerFeature:
    id = ID
    modes = frozenset({"pointer"})
    gestures = GESTURES

    def __init__(self) -> None:
        self.core = PointerCore()
        self.last_frame: Optional[PointerFrame] = None   # diagnostics for the Snapshot (read only)
        self._cursor: Optional[Tuple[float, float]] = None   # last point sent (for the trigger rewind)
        self._dragging = False
        self._holding = False
        self._last_release_t = -math.inf
        self._last_t = -math.inf

    # ------------------------------------------------------------------ Feature protocol
    def step(self, hand: Optional[HandObservation], ctx: FrameContext) -> Sequence[Intent]:
        t = ctx.frame.t
        settings = from_mapping(ctx.settings)
        if hand is None:
            frame = self.core.step(t, None, ctx.frame.aspect, settings)
        else:
            points = [_Point(*p) for p in hand.landmarks]
            frame = self.core.step(t, points, hand.frame.aspect, settings)
        self.last_frame = frame
        intents = self._intents(t, frame)
        if frame is not None and frame.action is not None and self._cursor is not None:
            self.core.record_cursor(t, self._cursor)
        self._update_activity(t)
        return intents

    def activity(self) -> Activity:
        return Activity(holding=self._holding, last_release_t=self._last_release_t)

    def reset(self, reason: str) -> None:
        self.core.reset()
        self.last_frame = None
        self._dragging = False
        if self._holding:
            self._holding = False
            self._last_release_t = self._last_t
        # The screen cursor stays where it is, so the rewind history and `_cursor` are kept (as v0.3)

    # ------------------------------------------------------------------ v0.3 extras
    def frame_missing(self) -> None:
        """The camera gave no frame (contracts section 2): drop click state, keep the latch."""
        self.core.frame_missing()

    # ------------------------------------------------------------------ helpers
    def _intents(self, t: float, frame: Optional[PointerFrame]) -> List[Intent]:
        action = frame.action if frame is not None else None
        out: List[Intent] = []
        if self._dragging and action != "drag_hold":
            out.append(Intent(t, ID, DRAG, "end", point=self._cursor))
            self._dragging = False
        if frame is None or action is None:
            return out
        info = {"anchor": frame.pointer_source}
        if frame.warp is not None:
            self._cursor = tuple(frame.warp)
            out.append(Intent(t, ID, MOVE, "hold", point=self._cursor, info={**info, "warp": True}))
        if action == "move_cursor":
            self._cursor = frame.cursor_point
            out.append(Intent(t, ID, MOVE, "hold", point=self._cursor, info=info))
        elif action == "drag_hold":
            self._cursor = frame.cursor_point
            out.append(Intent(t, ID, DRAG, "hold" if self._dragging else "begin", point=self._cursor, info=info))
            self._dragging = True
        elif action in _TAPS:
            out.append(Intent(t, ID, _TAPS[action], "tap", info=info))
        return out

    def _update_activity(self, t: float) -> None:
        self._last_t = t
        holding = self._dragging or self.core.pressing
        if self._holding and not holding:
            self._last_release_t = t
        self._holding = holding
