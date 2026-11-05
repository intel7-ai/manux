"""Feature `motion`: thumbs-up toggles motion mode; open-hand swipes are commands (S4, from v0.3).

Ported from v0.3 `hand-mouse/src/motion_mode.py` (detector.py; parity: tests/unit/features/test_motion_parity.py)
and the part of `qt_runtime._motion` / `_motion_blocked` that decided entry.

Mode handling (contracts section 5, arbiter):
- The feature runs in the cursor modes (`pinch`, `pointer`) and in `motion`. Whether motion mode is on is
  `ctx.mode == "motion"`; the feature never switches it itself.
- In a cursor mode it watches the entry pose and emits `motion.enter` (tap) when the pose was held. The arbiter
  decides: it switches to `motion` (release_all, reset the cursor features) or refuses. If the next frame is not
  in `motion`, the request was refused and the hold starts over.
- In `motion` it watches swipes and the exit pose and emits `motion.exit` (tap) for the pose (or the optional
  timeout / hand-lost exit); the arbiter switches back. A mode change the feature did not ask for (button,
  stage change) is taken as it comes: entering that way needs one frame to settle, as v0.3's button did.
- v0.3 read other engines' private fields to block entry (`_motion_blocked`). Now the feature reads
  `ctx.others` (`Activity`) only: entry is held back while another feature is `holding` or released within
  `motion.enter_quiet` s. The arbiter applies the same rule to `motion.enter` and adds the output's own state
  (a drag button down, scroll inertia), which v0.3 also checked and a feature cannot see.

Gestures (`tap` only, point None). The id says what the hand did; what runs is the binding (D-004):

| gesture id                   | hand                               | default binding |
| ---------------------------- | ---------------------------------- | --------------- |
| `motion.swipe_left`          | open hand, thumb out, to the left  | `nav.back` |
| `motion.swipe_right`         | open hand, thumb out, to the right | `nav.forward` |
| `motion.swipe_left_tucked`   | open hand, thumb tucked, left      | `tab.prev` |
| `motion.swipe_right_tucked`  | open hand, thumb tucked, right     | `tab.next` |
| `motion.enter`, `motion.exit` | thumbs-up held                    | none: read by the arbiter |

`activity()`: never `holding` (nothing is pressed); `last_release_t` is the last swipe.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence, Tuple

from manux.core.contracts import Activity, FrameContext, HandObservation, Intent
from manux.features.motion.detector import MotionCore, MotionEvent
from manux.features.motion.settings import DEFAULTS, SETTINGS, V03_KEYS, resolve

__all__ = ["ID", "MODE", "GESTURES", "GESTURE_PHASES", "DEFAULT_BINDINGS", "SETTINGS", "V03_KEYS",
           "MotionFeature", "entry_blocked"]

ID = "motion"
MODE = "motion"
CURSOR_MODES = ("pinch", "pointer")

SWIPE_LEFT = "motion.swipe_left"
SWIPE_RIGHT = "motion.swipe_right"
SWIPE_LEFT_TUCKED = "motion.swipe_left_tucked"
SWIPE_RIGHT_TUCKED = "motion.swipe_right_tucked"
ENTER = "motion.enter"
EXIT = "motion.exit"

GESTURES: Tuple[str, ...] = (SWIPE_LEFT, SWIPE_RIGHT, SWIPE_LEFT_TUCKED, SWIPE_RIGHT_TUCKED, ENTER, EXIT)
GESTURE_PHASES: Mapping[str, frozenset] = {g: frozenset({"tap"}) for g in GESTURES}
DEFAULT_BINDINGS: Mapping[str, Any] = {
    SWIPE_LEFT: "nav.back",
    SWIPE_RIGHT: "nav.forward",
    SWIPE_LEFT_TUCKED: "tab.prev",
    SWIPE_RIGHT_TUCKED: "tab.next",
}
_SWIPES = {("nav", "left"): SWIPE_LEFT, ("nav", "right"): SWIPE_RIGHT,
           ("tab", "left"): SWIPE_LEFT_TUCKED, ("tab", "right"): SWIPE_RIGHT_TUCKED}


def entry_blocked(others: Mapping[str, Activity], t: float, quiet: float, me: str = ID) -> bool:
    """v0.3 `_motion_blocked` on `Activity`: another feature holds a press, or released one < `quiet` s ago."""
    return any(a.holding or (t - a.last_release_t) < quiet for k, a in others.items() if k != me)


class MotionFeature:
    id = ID
    modes = frozenset({*CURSOR_MODES, MODE})
    gestures = GESTURES

    def __init__(self) -> None:
        self.core = MotionCore(dict(DEFAULTS))
        self._last_t = 0.0
        self._pending: Optional[str] = None      # "enter" / "exit" sent, the arbiter's answer is the next ctx.mode
        self.last_event: Optional[MotionEvent] = None   # diagnostics (read only)

    # ------------------------------------------------------------------ Feature protocol
    def step(self, hand: Optional[HandObservation], ctx: FrameContext) -> Sequence[Intent]:
        t = ctx.frame.t
        self._last_t = t
        c = resolve(ctx.settings)
        self.core.c = c
        self.last_event = None
        want = ctx.mode == MODE
        pending, self._pending = self._pending, None
        core = self.core
        if want != core.active:
            if want:
                core._enter(t, "mode")             # switched on from outside (button), or our exit was refused
                return []
            if pending != "enter":                 # switched off from outside: follow quietly
                core._exit(t, "mode")
                return []
            else:                                  # our enter was refused: drop it, the hold starts over
                core.active = False
                core._hist.clear()
        if not c["enabled"]:
            if core.active:
                return self._emit(t, core._exit(t, "disabled"))
            return []
        lm = hand.landmarks if hand is not None else None
        blocked = (not core.active) and entry_blocked(ctx.others, t, float(c["enter_quiet"]))
        return self._emit(t, core.update(t, lm, blocked=blocked))

    def activity(self) -> Activity:
        last = self.core._last_swipe_t
        return Activity(holding=False, last_release_t=last if last > -1e8 else -math.inf)

    def reset(self, reason: str) -> None:
        if reason == "hand_lost":
            # v0.3 kept motion mode (a toggle) through hand loss; only holds in progress drop
            self.core._enter_since = self.core._enter_lost_since = None
            self.core._exit_since = self.core._exit_lost_since = None
            return
        active = self.core.active
        self.core = MotionCore(dict(self.core.c))
        if active:                                 # the mode is the arbiter's: stay on, with a fresh state
            self.core._enter(self._last_t, "reset")    # (a held pose must be let go before it switches off)
        self._pending = None
        self.last_event = None

    # ------------------------------------------------------------------ helpers
    def _emit(self, t: float, ev: Optional[MotionEvent]) -> Sequence[Intent]:
        if ev is None:
            return []
        self.last_event = ev
        if ev.kind == "enter":
            self._pending = "enter"
            return [Intent(t, ID, ENTER, "tap", info=ev.info)]
        if ev.kind == "exit":
            self._pending = "exit"
            return [Intent(t, ID, EXIT, "tap", info=ev.info)]
        gesture = _SWIPES[(ev.info["variant"], ev.info["side"])]
        return [Intent(t, ID, gesture, "tap", info=ev.info)]
