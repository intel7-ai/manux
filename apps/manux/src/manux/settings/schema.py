"""Setting definitions and value validation (docs/contracts.md section 11).

Each layer or feature declares its settings as `Setting`s. Keys are `<owner>.<name>`
(`app.`, `camera.`, `tracking.` or a feature id). A bad value never raises out of
`Setting.coerce`: the caller gets the default back plus a warning text.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$")
KINDS = ("float", "int", "bool", "str", "choice")


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    kind: str = "float"
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Tuple[str, ...] = ()
    doc: str = ""

    def __post_init__(self) -> None:
        if not KEY_RE.match(self.key):
            raise ValueError(f"setting key must look like '<owner>.<name>', got {self.key!r}")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self.kind == "choice" and not self.choices:
            raise ValueError("a choice setting needs choices")
        value, problem = self._check(self.default)
        if problem:
            raise ValueError(f"default of {self.key} is invalid: {problem}")

    def _check(self, value: Any) -> Tuple[Any, str]:
        kind = self.kind
        if kind == "bool":
            return (value, "") if isinstance(value, bool) else (None, "not true/false")
        if kind == "str":
            return (value, "") if isinstance(value, str) else (None, "not text")
        if kind == "choice":
            return (value, "") if value in self.choices else (None, f"not one of {self.choices}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "not a number"
        if not math.isfinite(value):
            return None, "not finite"
        if kind == "int":
            if float(value) != int(value):
                return None, "not a whole number"
            value = int(value)
        else:
            value = float(value)
        if self.minimum is not None and value < self.minimum:
            return None, f"below {self.minimum}"
        if self.maximum is not None and value > self.maximum:
            return None, f"above {self.maximum}"
        return value, ""

    def coerce(self, value: Any) -> Tuple[Any, str]:
        """(valid value, "") or (default, warning)."""
        checked, problem = self._check(value)
        if problem:
            return self.default, f"{self.key}: {value!r} {problem}; using default {self.default!r}"
        return checked, ""


class Schema:
    """All known settings. Built once by the composition root from every layer's declarations."""

    def __init__(self, settings: Iterable[Setting] = ()) -> None:
        self._by_key: Dict[str, Setting] = {}
        self.add(settings)

    def add(self, settings: Iterable[Setting]) -> "Schema":
        for s in settings:
            if s.key in self._by_key:
                raise ValueError(f"setting {s.key} is declared twice")
            self._by_key[s.key] = s
        return self

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def get(self, key: str) -> Optional[Setting]:
        return self._by_key.get(key)

    def keys(self):
        return self._by_key.keys()

    def defaults(self) -> Dict[str, Any]:
        return {k: s.default for k, s in self._by_key.items()}
