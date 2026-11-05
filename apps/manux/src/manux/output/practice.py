"""Output sink `practice`: the tutorial screen's sink (docs/contracts.md section 8).

It records the commands it accepts so the tutorial can show them, and never sends real input:
in v0.3 the tutorial blanked the result (`practice_output()`) and the Windows cursor still moved
(11/04 feedback). This sink imports nothing from Windows.

- `apply` accepts every well-formed command (`ok`), `blocked` after `close()`, never raises.
- Begun actions (`begin` without `end`, e.g. `mouse.drag`) are tracked in `held`;
  `release_all` ends them all and can be called any number of times.
- `commands` keeps only the latest `limit` entries (the engine runs for hours in a tutorial).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, FrozenSet, Optional, Tuple

from manux.core.contracts import ActionCommand, OutputResult, OutputTarget

OK = OutputResult(True, "ok")
BLOCKED = OutputResult(False, "blocked")


class PracticeSink:
    target = OutputTarget.PRACTICE

    def __init__(self, limit: int = 256) -> None:
        self.commands: Deque[ActionCommand] = deque(maxlen=max(1, int(limit)))
        self.releases: Deque[Tuple[str, Tuple[str, ...]]] = deque(maxlen=32)  # (reason, actions ended)
        self._held: dict = {}            # action id -> the begin command
        self._closed = False

    @property
    def held(self) -> FrozenSet[str]:
        return frozenset(self._held)

    @property
    def last(self) -> Optional[ActionCommand]:
        return self.commands[-1] if self.commands else None

    def apply(self, cmd: ActionCommand) -> OutputResult:
        try:
            if self._closed:
                return BLOCKED
            if not isinstance(cmd, ActionCommand):
                return OutputResult(False, f"error:not an ActionCommand ({type(cmd).__name__})")
            if cmd.phase == "begin":
                self._held[cmd.action] = cmd
            elif cmd.phase == "end":
                self._held.pop(cmd.action, None)
            self.commands.append(cmd)
            return OK
        except Exception as exc:  # the contract: apply never raises
            return OutputResult(False, f"error:{type(exc).__name__}")

    def release_all(self, reason: str) -> None:
        try:
            if self._held:
                self.releases.append((str(reason), tuple(sorted(self._held))))
            self._held.clear()
        except Exception:
            pass

    def clear(self) -> None:
        """Forget the recorded commands (e.g. when a tutorial step starts over)."""
        self.commands.clear()

    def close(self) -> None:
        self.release_all("shutdown")
        self._closed = True
