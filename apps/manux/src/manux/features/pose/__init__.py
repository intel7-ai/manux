"""`pose`: a per-frame pose model that assists the TCN, it never decides (docs/pose-assist.md).

Two parts, both off by default (`pose_assist.enabled`):

`PoseFeature` is a `Feature` that only produces **evidence**. Every frame it turns the hand into the 33
`pose-angles-v1` values (`perception.pose_angles`), asks a window=1 model for class probabilities and
keeps a `PoseEvidence`. It is **silent** (no evidence) when the hand or its world joints are missing,
when the model fails, or when the best class is below `threshold`. It never returns an `Intent`: this
module cannot start, hold or end a gesture by itself (v1 has no "start hint").

`PoseGuard` is an `IntentFilter` (contracts section 6) that reads that evidence and can only **drop**
intents, never create or change one. It sits last in the filter chain, after `tcn_gate`:

- **Veto.** For each gesture in `veto_gestures` that the model knows (the card's `gestures` maps a pose
  class to a gesture id), a `tap` or `begin` is dropped when for `k_frames` frames in a row the model's
  best class was a *different* class with probability >= `tau_veto`. Not enough frames, a missing or
  weaker evidence, or a gesture the model does not know: the intent passes untouched.
- **Rest (idle) evidence.** While the best class was `idle_class` (default `none`) with probability
  >= `tau_idle` for `k_frames` frames in a row, a new `begin` (and a `tap` when `idle_taps`) from an
  intent source in `idle_sources` (default the direct TCN, `tcn`) or of a gesture in `idle_gestures`
  is dropped.
- A dropped `begin` takes its `hold`/`end` with it (as `tcn_gate` does); a passed `begin` keeps its
  `hold`/`end`. Mode intents (`<id>.enter`/`<id>.exit`) and all other gestures always pass.
- Every drop leaves a note for the Snapshot (`pose:veto:<gesture>:<phase>`, `pose:idle:...`): the
  engine collects it through the optional `drain_events()` of a filter.

The class-to-gesture table is the model card's `gestures` (D-004: a card names gestures, never actions).
`create_pose_assist()` returns no assist, and why, when the model or its card is wrong; the app then
runs without it (contracts section 10).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from manux.core.contracts import GESTURE_ID_RE, Activity, FrameContext, HandObservation, Intent
from manux.ml import REASON_TEXT, ModelCard, ModelError, Predictor, load_model
from manux.perception.pose_angles import FEATURE_VERSION, FEATURES, observation_pose_angles

__all__ = ["ID", "GUARD_ID", "SETTINGS", "RANGES", "ROLE", "PoseEvidence", "PoseFeature", "PoseGuard",
           "PoseAssist", "PoseLoad", "create_pose_assist"]

ID = "pose_assist"          # feature id and settings section
GUARD_ID = "pose_guard"     # filter id
PREFIX = ID + "."
ROLE = "pose_evidence"      # card["role"]: this model is evidence for the guard, not a TCN

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "model": "",                                              # path of the .onnx (card next to it)
    "tau_veto": 0.9,
    "tau_idle": 0.9,
    "k_frames": 3,
    "veto_gestures": "pinch.thumb_ring,pointer.right_click",  # comma separated gesture ids
    "idle_class": "none",
    "idle_sources": "tcn",                                    # comma separated intent sources
    "idle_gestures": "",                                      # comma separated gesture ids
    "idle_taps": False,                                       # also hold back taps while resting
}
SETTINGS: Mapping[str, Any] = MappingProxyType({PREFIX + k: v for k, v in DEFAULTS.items()})
RANGES: Mapping[str, Tuple[float, float]] = MappingProxyType({
    PREFIX + "tau_veto": (0.5, 0.999), PREFIX + "tau_idle": (0.5, 0.999), PREFIX + "k_frames": (1, 30),
})
_MODES = frozenset({"pointer", "pinch", "motion"})


def _short(values: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return {(k[len(PREFIX):] if k.startswith(PREFIX) else k): v for k, v in (values or {}).items()}


def _items(text: Any) -> Tuple[str, ...]:
    """'a.b, c.d' -> ('a.b', 'c.d'); blanks dropped, order kept, duplicates removed."""
    if not isinstance(text, str):
        return ()
    seen: Dict[str, None] = {}
    for part in text.split(","):
        part = part.strip()
        if part:
            seen.setdefault(part, None)
    return tuple(seen)


# ----------------------------------------------------------------------------- evidence

@dataclass(frozen=True)
class PoseEvidence:
    """What the pose model said about one frame (only built when it was confident enough)."""
    t: float
    classes: Tuple[str, ...]
    probs: Tuple[float, ...]
    top: str
    confidence: float          # probability of `top`
    version: str               # feature version the model reads (`pose-angles-v1`)

    def prob(self, name: str) -> float:
        return self.probs[self.classes.index(name)] if name in self.classes else 0.0


class PoseFeature:
    """Evidence only. A `Feature` so the engine steps it with the hand; it returns no intents."""

    id = ID
    modes = _MODES
    gestures: Tuple[str, ...] = ()

    def __init__(self, predictor: Predictor, card: ModelCard, threshold: float = 0.9) -> None:
        if card.window != 1 or card.features != FEATURES or card.feature_version != FEATURE_VERSION:
            raise ModelError("shape", f"a pose model reads one frame of {FEATURES} {FEATURE_VERSION} values")
        if tuple(predictor.classes) != card.classes:
            raise ModelError("shape", "predictor classes differ from the card")
        if not 0.0 < float(threshold) <= 1.0:
            raise ValueError("threshold must be within (0, 1]")
        self.predictor, self.card = predictor, card
        self.threshold = float(threshold)
        self.evidence: Optional[PoseEvidence] = None
        self.errors = 0
        self.frames = 0
        self.spoke = 0

    def step(self, hand: Optional[HandObservation], ctx: FrameContext) -> Sequence[Intent]:
        self.evidence = None
        row = observation_pose_angles(hand)
        if row is None:
            return ()
        self.frames += 1
        try:
            probs = np.asarray(self.predictor(row[None, :]), dtype=np.float64).reshape(-1)
            if (probs.shape != (len(self.card.classes),) or not np.isfinite(probs).all()
                    or (probs < 0).any() or (probs > 1).any() or not np.isclose(probs.sum(), 1.0, atol=1e-3)):
                raise ValueError("invalid pose probabilities")
        except Exception:      # advisory only: a model error means no evidence this frame, never a stopped frame
            self.errors += 1
            return ()
        best = int(probs.argmax())
        if probs[best] < self.threshold:
            return ()
        self.spoke += 1
        self.evidence = PoseEvidence(ctx.frame.t, self.card.classes, tuple(float(p) for p in probs),
                                     self.card.classes[best], float(probs[best]), self.card.feature_version)
        return ()

    def activity(self) -> Activity:
        return Activity()

    def reset(self, reason: str) -> None:
        self.evidence = None


# ----------------------------------------------------------------------------- the guard

class PoseGuard:
    """Drops risky intents the pose evidence contradicts (see the module docstring)."""

    id = GUARD_ID

    def __init__(self, source: PoseFeature, settings: Optional[Mapping[str, Any]] = None) -> None:
        cfg = {**DEFAULTS, **_short(settings)}
        self.source = source
        self.tau_veto = float(cfg["tau_veto"])
        self.tau_idle = float(cfg["tau_idle"])
        self.k = int(cfg["k_frames"])
        if not (0.0 < self.tau_veto <= 1.0 and 0.0 < self.tau_idle <= 1.0 and self.k >= 1):
            raise ValueError("tau_veto and tau_idle must be within (0, 1] and k_frames at least 1")
        card = source.card
        self._class_of: Dict[str, str] = {gid: cls for cls, gid in card.gestures.items()}   # gesture id -> pose class
        wanted = [g for g in _items(cfg["veto_gestures"]) if GESTURE_ID_RE.match(g)]
        self.veto_gestures: Tuple[str, ...] = tuple(g for g in wanted if g in self._class_of)
        self.unknown_veto: Tuple[str, ...] = tuple(g for g in wanted if g not in self._class_of)
        idle = str(cfg["idle_class"])
        self.idle_class: Optional[str] = idle if idle in card.classes else None
        self.idle_sources = frozenset(_items(cfg["idle_sources"]))
        self.idle_gestures = frozenset(g for g in _items(cfg["idle_gestures"]) if GESTURE_ID_RE.match(g))
        self.idle_phases = frozenset({"begin", "tap"} if cfg["idle_taps"] else {"begin"})
        self._veto_run: Dict[str, int] = {g: 0 for g in self.veto_gestures}
        self._idle_run = 0
        self._open: Set[str] = set()       # gestures whose `begin` passed
        self._dropped: Set[str] = set()    # gestures whose `begin` was dropped (until their `end`)
        self._events: List[str] = []
        self.vetoed = 0
        self.idled = 0
        self.passed = 0

    # ---------------------------------------------------------------- evidence per frame
    def observe(self, hand: Optional[HandObservation], ctx: FrameContext) -> None:
        ev = self.source.evidence if hand is not None else None
        if ev is not None and ev.t != ctx.frame.t:      # not this frame's evidence (the feature was not stepped)
            ev = None
        for gesture in self.veto_gestures:
            against = ev is not None and ev.top != self._class_of[gesture] and ev.confidence >= self.tau_veto
            self._veto_run[gesture] = self._veto_run[gesture] + 1 if against else 0
        resting = (ev is not None and self.idle_class is not None and ev.top == self.idle_class
                   and ev.confidence >= self.tau_idle)
        self._idle_run = self._idle_run + 1 if resting else 0

    def vetoing(self, gesture: str) -> bool:
        return self._veto_run.get(gesture, 0) >= self.k

    @property
    def resting(self) -> bool:
        return self._idle_run >= self.k

    # ---------------------------------------------------------------- the check
    def filter(self, intents: Sequence[Intent], ctx: FrameContext) -> Sequence[Intent]:
        out: List[Intent] = []
        for intent in intents:
            if intent.gesture.split(".", 1)[1] in ("enter", "exit"):
                out.append(intent)                      # mode intents are the arbiter's, never ours
                continue
            kept = self._check(intent)
            if kept:
                out.append(intent)
        return out

    def _why(self, intent: Intent) -> Optional[str]:
        """'veto' / 'idle' when this tap or begin must be dropped now, else None."""
        g, phase = intent.gesture, intent.phase
        if g in self._veto_run and self.vetoing(g):
            return "veto"
        if (self.resting and phase in self.idle_phases
                and (intent.source in self.idle_sources or g in self.idle_gestures)):
            return "idle"
        return None

    def _check(self, intent: Intent) -> bool:
        g, phase = intent.gesture, intent.phase
        if phase in ("tap", "begin"):
            if phase == "begin":
                self._open.discard(g)
                self._dropped.discard(g)
            why = self._why(intent)
            if why is not None:
                if phase == "begin":
                    self._dropped.add(g)
                self._note(why, g, phase)
                return False
            if phase == "begin":
                self._open.add(g)
            self.passed += 1
            return True
        # hold / end
        if g in self._dropped:
            if phase == "end":
                self._dropped.discard(g)
            return False
        if phase == "end":
            self._open.discard(g)
        return True

    def _note(self, why: str, gesture: str, phase: str) -> None:
        if why == "veto":
            self.vetoed += 1
        else:
            self.idled += 1
        self._events.append(f"pose:{why}:{gesture}:{phase}")

    # ---------------------------------------------------------------- engine hooks
    def drain_events(self) -> List[str]:
        """Notes for the Snapshot's `events` (the engine calls this after `filter`); clears them."""
        events, self._events = self._events, []
        return events

    def reset(self, reason: str) -> None:
        for gesture in self._veto_run:
            self._veto_run[gesture] = 0
        self._idle_run = 0
        self._open.clear()
        self._dropped.clear()

    def status(self) -> Dict[str, Any]:
        """For the screen (S6): counts and the current state. No evidence history."""
        return {"vetoed": self.vetoed, "idled": self.idled, "passed": self.passed, "resting": self.resting,
                "vetoing": sorted(g for g in self._veto_run if self.vetoing(g)), "errors": self.source.errors,
                "veto_gestures": list(self.veto_gestures), "unknown_veto": list(self.unknown_veto),
                "idle_class": self.idle_class, "kind": self.source.card.kind}


# ----------------------------------------------------------------------------- loading

@dataclass(frozen=True)
class PoseAssist:
    feature: PoseFeature
    guard: PoseGuard
    card: ModelCard


@dataclass(frozen=True)
class PoseLoad:
    assist: Optional[PoseAssist]
    reason: str = "ok"       # "ok" or a REASON_TEXT key

    @property
    def text(self) -> str:
        return "" if self.assist is not None else REASON_TEXT.get(self.reason, self.reason)


def create_pose_assist(model_path: Path, settings: Optional[Mapping[str, Any]] = None,
                       session_factory: Optional[Callable[[Path], Any]] = None) -> PoseLoad:
    """A pose assist for `<model>.onnx` + `<model>.json`, or none and why (never raises for a bad model)."""
    try:
        model = load_model(model_path, FEATURE_VERSION, FEATURES, session_factory=session_factory)
    except ModelError as exc:
        return PoseLoad(None, exc.code)
    card = model.card
    if card.window != 1 or card.raw.get("role") != ROLE:
        return PoseLoad(None, "invalid")
    if not card.gestures:
        return PoseLoad(None, "no_gestures")
    cfg = {**DEFAULTS, **_short(settings)}
    try:
        feature = PoseFeature(model.predictor, card, min(float(cfg["tau_veto"]), float(cfg["tau_idle"])))
        guard = PoseGuard(feature, settings)
    except (ModelError, ValueError):
        return PoseLoad(None, "invalid")
    return PoseLoad(PoseAssist(feature, guard, card))
