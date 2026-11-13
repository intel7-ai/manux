"""Action catalog: every action id the app can execute (docs/contracts.md section 7).

An action id is `<area>.<name>`. Features never name actions; bindings map gesture ids to these ids
(D-004). Each action declares the phases it accepts and the parameter keys a binding may set.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from manux.core.contracts import ACTION_ID_RE, PHASES, ActionId

TAP = frozenset({"tap"})
HOLD = frozenset({"hold"})
SPAN = frozenset({"begin", "hold", "end"})


@dataclass(frozen=True)
class ActionSpec:
    id: ActionId
    phases: frozenset
    params: Tuple[str, ...] = ()
    v03: Tuple[str, ...] = ()          # v0.3 names this action replaces (migration of old bindings)

    def __post_init__(self) -> None:
        if not ACTION_ID_RE.match(self.id):
            raise ValueError(f"action id must look like '<area>.<name>', got {self.id!r}")
        if not self.phases or not set(self.phases) <= set(PHASES):
            raise ValueError(f"{self.id}: phases must be a non-empty subset of {PHASES}")

    @property
    def area(self) -> str:
        return self.id.split(".", 1)[0]


_SPECS = (
    ActionSpec("custom.execute", TAP | SPAN, params=("gesture_key", "native", "fallback_default")),
    ActionSpec("mouse.move", HOLD, params=("gain",), v03=("move_cursor",)),
    ActionSpec("mouse.left_click", TAP, v03=("left_click",)),
    ActionSpec("mouse.right_click", TAP, v03=("right_click",)),
    ActionSpec("mouse.drag", SPAN, params=("gain",), v03=("drag_hold", "drag_toggle")),
    ActionSpec("mouse.scroll", SPAN, params=("gain",), v03=("scroll_grab",)),
    ActionSpec("window.task_switcher", TAP, v03=("task_switcher", "alt_tab")),
    ActionSpec("window.show_desktop", TAP, v03=("show_desktop",)),
    ActionSpec("control.toggle", TAP),
    ActionSpec("window.close", TAP),
    ActionSpec("nav.back", TAP, v03=("back",)),
    ActionSpec("nav.forward", TAP, v03=("forward",)),
    ActionSpec("tab.prev", TAP, v03=("tab_prev",)),
    ActionSpec("tab.next", TAP, v03=("tab_next",)),
)

CATALOG: Mapping[ActionId, ActionSpec] = MappingProxyType({s.id: s for s in _SPECS})


def spec(action: ActionId) -> Optional[ActionSpec]:
    """The spec of `action`, or None when the id is not in the catalog."""
    return CATALOG.get(action)


def from_v03(name: str) -> Optional[ActionId]:
    """The v0.4 action id for a v0.3 action name (None if v0.3 had no such name or it is not ported)."""
    for s in _SPECS:
        if name in s.v03:
            return s.id
    return None
