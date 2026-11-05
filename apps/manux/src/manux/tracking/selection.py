"""Choosing the control hand (port of hand-mouse/src/hand_selection.py, same behaviour).

The physical hand is MediaPipe's handedness label, swapped when the camera image is mirrored
(v0.3 `physical_side`, kept as-is). This rule lives only here: everything after tracking sees
physical hands. A label with a score below `min_score` counts as unsure.
"""
from __future__ import annotations

from typing import Optional, Sequence

from manux.tracking.landmarker import RawHand

MIN_SCORE = .75


def physical_side(hand: RawHand, mirrored: bool, min_score: float = MIN_SCORE) -> str:
    """'Left' / 'Right' (the user's real hand), or '' when the label is unsure."""
    if hand.label not in ("Left", "Right") or not hand.score >= min_score:
        return ""
    return {"Left": "Right", "Right": "Left"}[hand.label] if mirrored else hand.label


def select(hands: Sequence[RawHand], side: str, mirrored: bool, min_score: float = MIN_SCORE) -> Optional[RawHand]:
    """The one hand on `side`. None when there is no such hand or more than one (ambiguous)."""
    matches = [h for h in hands if side and physical_side(h, mirrored, min_score) == side]
    return matches[0] if len(matches) == 1 else None


class HandChoice:
    """The capture step: one hand held up alone for `hold_seconds` (at least 5 frames) chooses itself."""

    hold_seconds = 5.0

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.side = ""
        self.since: Optional[float] = None
        self.last_at: Optional[float] = None
        self.frames = 0

    def seconds_remaining(self, now: float) -> float:
        return self.hold_seconds if self.since is None else max(0.0, self.hold_seconds - (now - self.since))

    def observe(self, hands: Sequence[RawHand], mirrored: bool, now: float) -> str:
        side = physical_side(hands[0], mirrored) if len(hands) == 1 else ""
        if not side:
            self.reset()
            return ""
        if side != self.side or self.last_at is None or now - self.last_at > .25:
            self.reset()
            self.side, self.since = side, now
        if self.last_at is None or now > self.last_at:
            self.last_at = now
            self.frames += 1
        return side if self.frames >= 5 and self.seconds_remaining(now) == 0 else ""
