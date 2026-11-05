"""Router: Intent -> ActionCommand through the bindings (docs/contracts.md section 7).

`phase` and `point` are copied unchanged; `params` come from the binding, never from `Intent.info`.
The one per-frame value is `Intent.scale`: for an action that takes a `gain`, the command's gain is
binding gain (default 1) x scale. Actions without a gain ignore it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple

from manux.actions.bindings import Binding
from manux.actions.catalog import spec
from manux.core.contracts import ActionCommand, GestureId, Intent

ROUTE_REASONS = ("ok", "unbound", "phase_mismatch", "unknown_action")


@dataclass(frozen=True)
class Routed:
    intent: Intent
    command: Optional[ActionCommand]
    reason: str = "ok"          # one of ROUTE_REASONS; command is None unless "ok"


class Router:
    def __init__(self, bindings: Mapping[GestureId, Binding]) -> None:
        self._bindings = dict(bindings)

    @property
    def bindings(self) -> Mapping[GestureId, Binding]:
        return dict(self._bindings)

    def set_bindings(self, bindings: Mapping[GestureId, Binding]) -> None:
        self._bindings = dict(bindings)

    def route(self, intent: Intent) -> Routed:
        binding = self._bindings.get(intent.gesture)
        if binding is None:
            return Routed(intent, None, "unbound")
        s = spec(binding.action)
        if s is None:  # bindings should have been validated; refuse anyway
            return Routed(intent, None, "unknown_action")
        if intent.phase not in s.phases:
            return Routed(intent, None, "phase_mismatch")
        params = binding.params
        if intent.scale is not None and "gain" in s.params:
            params = dict(binding.params)
            params["gain"] = float(params.get("gain", 1.0)) * intent.scale
        cmd = ActionCommand(t=intent.t, action=binding.action, phase=intent.phase,
                            gesture=intent.gesture, point=intent.point, params=params)
        return Routed(intent, cmd)

    def route_all(self, intents: Sequence[Intent]) -> Tuple[List[ActionCommand], List[Routed]]:
        """Commands to execute, plus every intent that produced none (for the Snapshot / failure log)."""
        commands: List[ActionCommand] = []
        dropped: List[Routed] = []
        for intent in intents:
            r = self.route(intent)
            if r.command is not None:
                commands.append(r.command)
            else:
                dropped.append(r)
        return commands, dropped
