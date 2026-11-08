"""UiCommand: the only way the UI changes the engine (docs/contracts.md section 12).

The UI thread puts commands on `CommandQueue`; the engine drains it at the start of each frame.
The UI never sets engine fields directly.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import List, Optional, Union

from manux.core.contracts import Stage


@dataclass(frozen=True)
class SetStage:
    stage: Stage


@dataclass(frozen=True)
class SetControl:
    on: bool                      # PC control on/off (main stage); off = output none


@dataclass(frozen=True)
class ResetPractice:
    """Release and reset tutorial recognition before a new step (contract section 12)."""


@dataclass(frozen=True)
class SetBusy:
    kind: str                     # calibrating / enrolling / recording (engine.state.BUSY_KINDS)
    on: bool


@dataclass(frozen=True)
class RequestMode:
    mode: Optional[str]           # an overlay mode such as "motion"; None = back to the base mode


@dataclass(frozen=True)
class SetBaseMode:
    mode: str                     # "pointer" or "pinch" (the main input style)


@dataclass(frozen=True)
class Mark:
    note: str = ""                # "that was wrong" (F key): goes into the snapshot's events


UiCommand = Union[SetStage, SetControl, ResetPractice, SetBusy, RequestMode, SetBaseMode, Mark]


class CommandQueue:
    def __init__(self) -> None:
        self._q: "queue.SimpleQueue[UiCommand]" = queue.SimpleQueue()

    def put(self, command: UiCommand) -> None:
        self._q.put(command)

    def drain(self) -> List[UiCommand]:
        out: List[UiCommand] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out
