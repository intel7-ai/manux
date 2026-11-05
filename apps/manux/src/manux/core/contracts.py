"""MANUX v0.4 module contracts (code form).

This file and docs/contracts.md describe the same contract. Change both in the same PR
(docs/contracts.md section 13). Standard library only: every layer may import this module.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Literal, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

N_LANDMARKS = 21

Landmark = Tuple[float, float, float]
Point = Tuple[float, float]
GestureId = str
ActionId = str
Phase = Literal["tap", "begin", "hold", "end"]
PHASES: Tuple[str, ...] = ("tap", "begin", "hold", "end")

GESTURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
ACTION_ID_RE = GESTURE_ID_RE

RESET_REASONS: Tuple[str, ...] = (
    "hand_lost", "stage_change", "mode_change", "pause", "settings", "shutdown", "error",
)


def _frozen_map(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def _check_point(name: str, p: Optional[Point]) -> None:
    if p is None:
        return
    if len(p) != 2 or not _finite(*p):
        raise ValueError(f"{name} must be (x, y) finite floats, got {p!r}")


def _check_landmarks(name: str, pts: Sequence[Landmark]) -> Tuple[Landmark, ...]:
    pts = tuple(tuple(p) for p in pts)  # type: ignore[misc]
    if len(pts) != N_LANDMARKS:
        raise ValueError(f"{name} needs {N_LANDMARKS} points, got {len(pts)}")
    for p in pts:
        if len(p) != 3 or not _finite(*p):
            raise ValueError(f"{name} has a non-finite or malformed point: {p!r}")
    return pts  # type: ignore[return-value]


# ----------------------------------------------------------------------------- 2. frame

@dataclass(frozen=True)
class FrameInfo:
    seq: int
    t: float
    width: int
    height: int
    mirrored: bool
    rotation: int = 0

    def __post_init__(self) -> None:
        if self.seq < 0 or not _finite(self.t):
            raise ValueError("seq must be >= 0 and t finite")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width and height must be positive")
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180 or 270")

    @property
    def aspect(self) -> float:
        return self.height / self.width


# ----------------------------------------------------------------------------- 3. hand

@dataclass(frozen=True)
class HandObservation:
    frame: FrameInfo
    landmarks: Tuple[Landmark, ...]
    handedness: Literal["Left", "Right"]
    score: float
    world: Optional[Tuple[Landmark, ...]] = None
    from_crop: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "landmarks", _check_landmarks("landmarks", self.landmarks))
        if self.world is not None:
            object.__setattr__(self, "world", _check_landmarks("world", self.world))
        if self.handedness not in ("Left", "Right"):
            raise ValueError("handedness must be 'Left' or 'Right' (the physical hand)")
        if not _finite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be within 0..1")


# ----------------------------------------------------------------------------- 5. features

@dataclass(frozen=True)
class Intent:
    t: float
    source: str
    gesture: GestureId
    phase: Phase
    point: Optional[Point] = None
    confidence: Optional[float] = None
    info: Mapping[str, object] = field(default_factory=dict)
    # Per-frame speed factor (v0.3 hand-shape / long-press multiplier). The router multiplies it into the
    # binding's "gain" for actions that take a gain; other actions ignore it. None = 1.
    scale: Optional[float] = None

    def __post_init__(self) -> None:
        if self.scale is not None and not (_finite(self.scale) and self.scale > 0):
            raise ValueError("scale must be a positive finite number")
        if not GESTURE_ID_RE.match(self.gesture):
            raise ValueError(f"gesture id must look like '<feature>.<name>', got {self.gesture!r}")
        if self.gesture.split(".", 1)[0] != self.source:
            raise ValueError(f"gesture {self.gesture!r} must start with its source feature id {self.source!r}")
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}")
        _check_point("point", self.point)
        if self.confidence is not None and not (_finite(self.confidence) and 0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be within 0..1")
        object.__setattr__(self, "info", _frozen_map(self.info))


@dataclass(frozen=True)
class Activity:
    holding: bool = False
    last_release_t: float = -math.inf


class Stage(str, Enum):
    SPLASH = "splash"
    WELCOME = "welcome"
    CAPTURE = "capture"
    TUTORIAL = "tutorial"
    MAIN = "main"


class OutputTarget(str, Enum):
    NONE = "none"
    PRACTICE = "practice"
    WINDOWS = "windows"


@dataclass(frozen=True)
class FrameContext:
    frame: FrameInfo
    stage: Stage
    output: OutputTarget
    mode: str
    settings: Mapping[str, object] = field(default_factory=dict)
    others: Mapping[str, Activity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _frozen_map(self.settings))
        object.__setattr__(self, "others", _frozen_map(self.others))


@runtime_checkable
class Feature(Protocol):
    id: str
    modes: frozenset
    gestures: Tuple[GestureId, ...]

    def step(self, hand: Optional[HandObservation], ctx: FrameContext) -> Sequence[Intent]: ...

    def activity(self) -> Activity: ...

    def reset(self, reason: str) -> None: ...


# ----------------------------------------------------------------------------- 6. intent filters

@runtime_checkable
class IntentFilter(Protocol):
    id: str

    def observe(self, hand: Optional[HandObservation], ctx: FrameContext) -> None: ...

    def filter(self, intents: Sequence[Intent], ctx: FrameContext) -> Sequence[Intent]: ...

    def reset(self, reason: str) -> None: ...


# ----------------------------------------------------------------------------- 7. actions

@dataclass(frozen=True)
class ActionCommand:
    t: float
    action: ActionId
    phase: Phase
    gesture: GestureId
    point: Optional[Point] = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not ACTION_ID_RE.match(self.action):
            raise ValueError(f"action id must look like '<area>.<name>', got {self.action!r}")
        if not GESTURE_ID_RE.match(self.gesture):
            raise ValueError(f"gesture id must look like '<feature>.<name>', got {self.gesture!r}")
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}")
        _check_point("point", self.point)
        object.__setattr__(self, "params", _frozen_map(self.params))


# ----------------------------------------------------------------------------- 8. output

OUTPUT_REASONS: Tuple[str, ...] = ("ok", "unsupported", "blocked")


@dataclass(frozen=True)
class OutputResult:
    accepted: bool
    reason: str = "ok"

    def __post_init__(self) -> None:
        if self.reason not in OUTPUT_REASONS and not self.reason.startswith("error:"):
            raise ValueError(f"reason must be one of {OUTPUT_REASONS} or 'error:<detail>'")
        if self.accepted != (self.reason == "ok"):
            raise ValueError("accepted is True exactly when reason is 'ok'")


@runtime_checkable
class OutputSink(Protocol):
    target: OutputTarget

    def apply(self, cmd: ActionCommand) -> OutputResult: ...

    def release_all(self, reason: str) -> None: ...

    def close(self) -> None: ...


# Which output target each stage may use (docs/contracts.md section 8). Paused, calibrating,
# enrolling and recording inside MAIN also map to NONE; the engine decides that from its state.
STAGE_OUTPUT: Mapping[Stage, OutputTarget] = MappingProxyType({
    Stage.SPLASH: OutputTarget.NONE,
    Stage.WELCOME: OutputTarget.NONE,
    Stage.CAPTURE: OutputTarget.NONE,
    Stage.TUTORIAL: OutputTarget.PRACTICE,
    Stage.MAIN: OutputTarget.WINDOWS,
})
