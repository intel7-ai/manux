"""Stage and control state, and which output target it allows (docs/contracts.md section 8).

| state                                          | output   |
| splash / welcome / capture                     | none     |
| tutorial                                       | practice |
| main + paused, or calibrating/enrolling/recording | none  |
| main + control on                              | windows  (the only state that sends real input) |
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import FrozenSet

from manux.core.contracts import OutputTarget, Stage

BUSY_KINDS = ("calibrating", "enrolling", "recording")


@dataclass(frozen=True)
class EngineState:
    stage: Stage = Stage.SPLASH
    control_on: bool = False                      # the user switched PC control on (main stage)
    busy: FrozenSet[str] = field(default_factory=frozenset)   # subset of BUSY_KINDS

    @property
    def output(self) -> OutputTarget:
        return output_target(self)

    def with_busy(self, kind: str, on: bool) -> "EngineState":
        if kind not in BUSY_KINDS:
            raise ValueError(f"busy kind must be one of {BUSY_KINDS}")
        busy = set(self.busy)
        (busy.add if on else busy.discard)(kind)
        return replace(self, busy=frozenset(busy))


def output_target(state: EngineState) -> OutputTarget:
    if state.stage is Stage.TUTORIAL:
        return OutputTarget.PRACTICE
    if state.stage is Stage.MAIN and state.control_on and not state.busy:
        return OutputTarget.WINDOWS
    return OutputTarget.NONE
