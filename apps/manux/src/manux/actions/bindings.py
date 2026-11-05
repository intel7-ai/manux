"""Bindings: gesture id -> action id (+ params), and their validation (docs/contracts.md section 7).

Rules:
- An unknown action id is rejected when the bindings are read; that gesture falls back to its default
  binding (the app keeps running).
- A binding whose gesture can emit a phase the action does not accept (a `tap` gesture -> `mouse.drag`)
  is rejected the same way.
- Params may only use the keys the action declares in the catalog.
- A gesture with no binding (or bound to None) runs nothing; the router reports it as `unbound`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Tuple, Union

from manux.actions.catalog import spec
from manux.core.contracts import GESTURE_ID_RE, PHASES, ActionId, GestureId


@dataclass(frozen=True)
class Binding:
    action: ActionId
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


RawBinding = Union[None, str, Mapping[str, object], Binding]


@dataclass(frozen=True)
class BindingProblem:
    gesture: str
    reason: str          # unknown_action | phase_mismatch | bad_params | bad_gesture | bad_value
    detail: str = ""


def _parse(value: RawBinding) -> Tuple[Optional[Binding], Optional[str]]:
    """(binding, None) / (None, None) for an explicit 'no binding' / (None, problem)."""
    if value is None:
        return None, None
    if isinstance(value, Binding):
        return value, None
    if isinstance(value, str):
        return Binding(value), None
    if isinstance(value, Mapping):
        action = value.get("action")
        params = value.get("params", {})
        if action is None:
            return None, None
        if not isinstance(action, str) or not isinstance(params, Mapping):
            return None, "bad_value"
        return Binding(action, params), None
    return None, "bad_value"


def check_binding(gesture: GestureId, binding: Binding,
                  gesture_phases: Optional[frozenset] = None) -> Optional[BindingProblem]:
    """None when the binding is valid, else what is wrong with it."""
    s = spec(binding.action)
    if s is None:
        return BindingProblem(gesture, "unknown_action", binding.action)
    if gesture_phases is not None:
        extra = set(gesture_phases) - set(s.phases)
        if extra:
            return BindingProblem(gesture, "phase_mismatch",
                                  f"{binding.action} accepts {sorted(s.phases)}, gesture emits {sorted(extra)}")
    unknown = set(binding.params) - set(s.params)
    if unknown:
        return BindingProblem(gesture, "bad_params", f"{binding.action} has no params {sorted(unknown)}")
    return None


def resolve_bindings(
    defaults: Mapping[GestureId, RawBinding],
    overrides: Optional[Mapping[str, RawBinding]] = None,
    gesture_phases: Optional[Mapping[GestureId, frozenset]] = None,
) -> Tuple[Dict[GestureId, Binding], List[BindingProblem]]:
    """Defaults (from the features' DEFAULT_BINDINGS) with the profile's overrides on top.

    A rejected override leaves the default in place; a rejected default leaves the gesture unbound.
    Returns the usable bindings and every problem found (for the log / Snapshot), never raises.
    """
    phases = gesture_phases or {}
    out: Dict[GestureId, Binding] = {}
    problems: List[BindingProblem] = []

    def accept(gesture: str, raw: RawBinding) -> Optional[bool]:
        """True = bound, False = explicitly unbound, None = rejected."""
        if not isinstance(gesture, str) or not GESTURE_ID_RE.match(gesture):
            problems.append(BindingProblem(str(gesture), "bad_gesture"))
            return None
        try:
            binding, err = _parse(raw)
        except (TypeError, ValueError) as exc:
            problems.append(BindingProblem(gesture, "bad_value", str(exc)))
            return None
        if err:
            problems.append(BindingProblem(gesture, err, repr(raw)))
            return None
        if binding is None:
            out.pop(gesture, None)
            return False
        g_phases = phases.get(gesture)
        if g_phases is not None and not set(g_phases) <= set(PHASES):
            problems.append(BindingProblem(gesture, "bad_value", f"unknown phases {sorted(g_phases)}"))
            return None
        problem = check_binding(gesture, binding, g_phases)
        if problem:
            problems.append(problem)
            return None
        out[gesture] = binding
        return True

    for gesture, raw in defaults.items():
        accept(gesture, raw)
    for gesture, raw in (overrides or {}).items():
        accept(gesture, raw)
    return out, problems
