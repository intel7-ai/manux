"""
Guided gesture tutorial (2026-11-04): one gesture at a time, the user does it, the engine confirms.

Steps come from src/gesture_guide.py, so the tutorial teaches exactly what the main screen lists
for the current engine. A step succeeds when the engine reports one of the step's actions
(cursor movement must last a moment, so a twitch does not count). After a success the next step
follows on its own; the user can also skip or go back. No UI or input code here.
"""
from __future__ import annotations

from typing import Optional, Sequence

from src.gesture_guide import Card

MOVE_SECONDS = 1.0        # cursor movement needed for the "move" step
ADVANCE_AFTER = 1.2       # seconds the success message stays before the next step


class Tutorial:
    def __init__(self, steps: Sequence[Card]) -> None:
        self.steps = list(steps)
        self.index = 0
        self.succeeded_at: Optional[float] = None
        self.done = [False] * len(self.steps)
        self._moving = 0.0
        self._last_t: Optional[float] = None
        self._last_action = ""

    @property
    def finished(self) -> bool:
        return self.index >= len(self.steps)

    @property
    def current(self) -> Optional[Card]:
        return None if self.finished else self.steps[self.index]

    @property
    def progress(self) -> float:
        """0..1 for the move step (how long the cursor has moved), 1 once any step succeeded."""
        card = self.current
        if card is None or self.succeeded_at is not None:
            return 1.0
        if card["key"] == "move":
            return min(1.0, self._moving / MOVE_SECONDS)
        return 0.0

    def observe(self, action: str, motion_on: bool, now: float) -> bool:
        """Feed one frame's engine action. True when the current step just succeeded."""
        card = self.current
        dt = 0.0 if self._last_t is None else max(0.0, min(.2, now - self._last_t))
        self._last_t = now
        rising = action != self._last_action
        self._last_action = action
        if card is None or self.succeeded_at is not None:
            return False
        actions = card["actions"]
        if "motion" in actions:
            hit = motion_on
        elif card["key"] == "move":
            self._moving = self._moving + dt if action in actions else max(0.0, self._moving - dt / 2)
            hit = self._moving >= MOVE_SECONDS
        else:
            hit = rising and action in actions
        if hit:
            self.succeeded_at = now
            self.done[self.index] = True
        return hit

    def tick(self, now: float) -> bool:
        """Move on after a success has been shown long enough. True when the step changed."""
        if self.succeeded_at is not None and now - self.succeeded_at >= ADVANCE_AFTER:
            self.next()
            return True
        return False

    def next(self) -> None:
        if not self.finished:
            self.index += 1
        self._reset_step()

    def back(self) -> None:
        self.index = max(0, self.index - 1)
        self._reset_step()

    def _reset_step(self) -> None:
        self.succeeded_at = None
        self._moving = 0.0
