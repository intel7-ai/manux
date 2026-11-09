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


@dataclass(frozen=True)
class ExecutionDevice:
    """Reported execution choice; requested hardware is distinct from actual execution."""
    requested: str
    actual: str
    runtime: str
    name: str = ""
    reason: str = ""

Point = Tuple[float, float]
GestureId = str
ActionId = str
# Engine-owned action: resolved through bindings, consumed before an OS sink (contract section 8).
CONTROL_TOGGLE_ACTION: ActionId = "control.toggle"
# UiCommand ResetPractice (engine/commands.py) releases tutorial output and resets recognition and mode.
Phase = Literal["tap", "begin", "hold", "end"]
PHASES: Tuple[str, ...] = ("tap", "begin", "hold", "end")

GESTURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
ACTION_ID_RE = GESTURE_ID_RE
FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PROFILE_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

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


# ----------------------------------------------------------------------------- 14. phone link

@dataclass(frozen=True)
class RemoteFrameMetadata:
    """Phone JPEG metadata before transforms; contracts.md section 14 (Dan 9da1f63 port)."""

    seq: int
    captured_at: float  # estimated PC epoch seconds; never used as FrameInfo.t
    width: int
    height: int
    mirrored: bool
    rotation: int
    facing: Literal["user", "environment"]

    def __post_init__(self) -> None:
        if type(self.seq) is not int or not 0 <= self.seq <= 2**53 - 1:
            raise ValueError("invalid remote seq")
        if isinstance(self.captured_at, bool) or not _finite(self.captured_at) or self.captured_at <= 0:
            raise ValueError("invalid capture time")
        if any(type(n) is not int or not 1 <= n <= 1280 for n in (self.width, self.height)):
            raise ValueError("invalid remote dimensions")
        if self.width * self.height > 1280 * 960:
            raise ValueError("remote frame too large")
        if type(self.mirrored) is not bool or type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise ValueError("invalid remote transform")
        if self.facing not in ("user", "environment"):
            raise ValueError("invalid remote camera facing")


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
        if not isinstance(self.source, str) or not FEATURE_ID_RE.fullmatch(self.source):
            raise ValueError("source must be a lowercase feature id (the producing recognizer)")
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


@dataclass(frozen=True)
class RecordingIdentity:
    profile: str
    preset: str = "pinch"
    split: str = "train"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or not PROFILE_ALIAS_RE.fullmatch(self.profile):
            raise ValueError("별명은 영문·숫자·_·- 1~32자로 입력하세요")
        if self.preset not in ("pinch", "pointer", "trigger") or self.split not in ("train", "eval"):
            raise ValueError("녹화 방식 또는 train/eval 구분이 잘못되었습니다")


@dataclass(frozen=True)
class PracticeEvent:
    """Accepted practice event retained independently of the latest camera image (§12)."""
    seq: int
    t: float
    actions: Tuple[str, ...]
    cursor: Optional[Point]
    precision: bool
    motion_on: bool
    has_hand: bool


@dataclass(frozen=True)
class RuntimeRequest:
    kind: Literal["activate_model", "preview_model", "tcn_policy", "tcn_toggle", "next_monitor", "spatial_guard", "sensitivity", "advanced", "pointer_setup", "phone_camera"]
    path: str = ""
    values: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.kind not in ("activate_model", "preview_model", "tcn_policy", "tcn_toggle", "next_monitor", "spatial_guard", "sensitivity", "advanced", "pointer_setup", "phone_camera")
                or not isinstance(self.path, str)
                or self.kind in ("activate_model", "preview_model", "sensitivity", "advanced") and not self.path
                or self.kind in ("tcn_toggle", "spatial_guard") and self.path not in ("on", "off")
                or self.kind == "phone_camera" and self.path not in ("start", "local", "pair", "disconnect", "block", "unblock")
                or self.kind == "pointer_setup" and self.path not in ("start", "capture", "skip", "apply", "adjust", "retest")):
            raise ValueError("invalid runtime request")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


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
