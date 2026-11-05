"""Snapshot: what the engine tells the UI after each frame (docs/contracts.md section 12).

Immutable; the UI reads only the newest one. Preview images, landmarks for drawing, camera
metrics and model status are added by the composition root's runner (S6), not by the engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from manux.core.contracts import OutputTarget, Stage


@dataclass(frozen=True)
class FeatureError:
    source: str                 # feature / filter id, or "output"
    error: str


@dataclass(frozen=True)
class Snapshot:
    seq: int
    t: float
    stage: Stage
    output: OutputTarget
    mode: str
    control_on: bool
    busy: Tuple[str, ...]
    has_hand: bool
    intents: Tuple[Tuple[str, str], ...]            # (gesture id, phase) after filters
    unrouted: Tuple[Tuple[str, str], ...]           # (gesture id, reason): unbound / phase_mismatch / ...
    results: Tuple[Tuple[str, str, str], ...]       # (action id, phase, output reason)
    activities: Tuple[Tuple[str, bool], ...]        # (feature id, holding)
    events: Tuple[str, ...]                         # hand_lost, mode:..., mark:...
    errors: Tuple[FeatureError, ...]
