"""Mode arbiter (docs/contracts.md section 5, "중재기"). Replaces v0.3 qt_runtime._motion/_motion_blocked.

- One mode at a time. A feature is stepped only while the current mode is in its `modes`.
- The base mode is the main input style ("pointer" or "pinch"). Overlay modes (e.g. "motion") are
  named after the feature that owns them.
- A feature asks for its overlay with `Intent(gesture="<id>.enter")` and leaves it with `<id>.exit`.
  These mode intents are consumed here and never routed to an action.
- Entry is refused while another stepped feature is holding (press/drag/scroll), or released less
  than `quiet` seconds ago (v0.3 `motion_enter_quiet`, 1.0 s). The refusal is decided only from
  `Activity`, never from a feature's private fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from manux.core.contracts import Activity, Intent

ENTER, EXIT = "enter", "exit"


@dataclass(frozen=True)
class ModeChange:
    old: str
    new: str
    why: str                       # "intent" | "ui" | "base" | "refused:<reason>"


def is_mode_intent(intent: Intent) -> bool:
    return intent.gesture.split(".", 1)[1] in (ENTER, EXIT)


class Arbiter:
    def __init__(self, base_mode: str, quiet: float = 1.0) -> None:
        self.base_mode = base_mode
        self.mode = base_mode
        self.quiet = float(quiet)

    def blocked(self, now: float, activities: Mapping[str, Activity], asking: str) -> Optional[str]:
        for fid, act in activities.items():
            if fid == asking:
                continue
            if act.holding:
                return f"{fid} holding"
            if now - act.last_release_t < self.quiet:
                return f"{fid} released {now - act.last_release_t:.2f}s ago"
        return None

    def _switch(self, new: str, why: str) -> Optional[ModeChange]:
        if new == self.mode:
            return None
        change = ModeChange(self.mode, new, why)
        self.mode = new
        return change

    def handle(self, now: float, intents: Sequence[Intent], activities: Mapping[str, Activity]
               ) -> Tuple[List[Intent], List[ModeChange]]:
        """Split mode intents off; return (intents to route, mode changes and refusals)."""
        rest: List[Intent] = []
        changes: List[ModeChange] = []
        for intent in intents:
            if not is_mode_intent(intent):
                rest.append(intent)
                continue
            kind = intent.gesture.split(".", 1)[1]
            if kind == ENTER:
                why = self.blocked(now, activities, intent.source)
                if why is not None:
                    changes.append(ModeChange(self.mode, self.mode, f"refused:{why}"))
                    continue
                change = self._switch(intent.source, "intent")
            else:
                change = self._switch(self.base_mode, "intent") if self.mode == intent.source else None
            if change:
                changes.append(change)
        return rest, changes

    def request(self, mode: Optional[str]) -> Optional[ModeChange]:
        """UI request: an overlay mode, or None for the base mode. Not subject to the quiet rule."""
        return self._switch(mode or self.base_mode, "ui")

    def set_base(self, mode: str) -> Optional[ModeChange]:
        on_base = self.mode == self.base_mode
        self.base_mode = mode
        return self._switch(mode, "base") if on_base else None
