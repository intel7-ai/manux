"""Output sink `none` (docs/contracts.md section 8): splash, welcome, hand choice, pause,
calibration, enrolment and recording.

Accepts nothing (`accepted=False, reason="blocked"`), holds nothing, imports nothing that can
reach Windows. `release_all` and `close` are no-ops and safe to call any number of times.
`blocked` counts the refused commands (diagnostics only).
"""
from __future__ import annotations

from manux.core.contracts import OutputResult, OutputTarget

BLOCKED = OutputResult(False, "blocked")


class NullSink:
    target = OutputTarget.NONE

    def __init__(self) -> None:
        self.blocked = 0

    def apply(self, cmd: object) -> OutputResult:
        self.blocked += 1
        return BLOCKED

    def release_all(self, reason: str) -> None:
        return None

    def close(self) -> None:
        return None
