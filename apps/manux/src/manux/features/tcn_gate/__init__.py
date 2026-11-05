"""Filter `tcn_gate`: a trained gesture model confirms what the rules decided (contracts section 6).

Ported from Dan's `hand-mouse-gesture-overhaul@7448e4a` `src/gesture_gate.py`. Not a `Feature`:
an `IntentFilter` that sits between the features and the router. It only passes, holds or drops
intents. It never creates a gesture and never changes one (a late intent is the same `Intent`
with only `t` moved to the current frame).

Every frame, `observe()` pushes the hand into `local3d-v1` features (`perception.local3d`) and asks
the model for class probabilities. A class is **confirmed** when its probability was
>= `threshold` for `frames_in_a_row` frames in a row within the last `lookback` s (0.5).

Which gestures it checks comes from the model card's `gestures` (class -> gesture id), e.g.
`thumb_ring -> pinch.thumb_ring`. Gesture ids are plain strings: this package does not import the
features that send them. A card without `gestures` (Dan's) gets `LEGACY_GESTURES` when its classes
are exactly Dan's three; otherwise no gate is made (`no_gestures`).

For a checked gesture:

| phase | model confirmed | not (yet) |
| ----- | --------------- | --------- |
| `tap` | passes | held up to `wait` s (0.3); sent late (t = now) once confirmed, dropped after |
| `begin` | passes, the gesture is open | held up to `wait` s; its `hold`s meanwhile are dropped (not queued); sent late once confirmed, else dropped with all its `hold`/`end` |
| `hold` / `end` | pass while the gesture is open (`end` closes it) | dropped (no open `begin`) |

A new `tap` of a gesture already waiting replaces the waiting one (nothing piles up). Gestures the
card does not name pass untouched. A model error in a frame counts as "not confirmed" in that frame.

Difference from v0.3 (deliberate, contracts section 6 "보류에는 최대 시간이 있다"): v0.3 kept a
scroll grab waiting for as long as the rules held it; here a `begin` waits at most `wait` s.

`create_gate()` returns no gate (and the reason) when the model, its card, onnxruntime, the
feature version or the shapes are wrong (contracts section 10). The app then runs on the rules alone.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from manux.core.contracts import FrameContext, HandObservation, Intent
from manux.ml import REASON_TEXT, ModelCard, ModelError, Predictor, load_model
from manux.perception.local3d import FEATURE_VERSION, FEATURES, Local3dStream

__all__ = ["ID", "SETTINGS", "LEGACY_GESTURES", "TcnGate", "GateLoad", "create_gate", "gestures_for"]

ID = "tcn_gate"
PREFIX = ID + "."
DEFAULTS: Dict[str, Any] = {
    "wait": 0.3,        # s a held tap / begin may wait for the model (v0.3 gate_wait)
    "lookback": 0.5,    # s of model output a confirmation may come from (v0.3 gate_lookback)
}
SETTINGS: Mapping[str, Any] = MappingProxyType({PREFIX + k: v for k, v in DEFAULTS.items()})
# also accepted (default: the card): "tcn_gate.threshold" (v0.3 gate_threshold), "tcn_gate.frames" (gate_frames)

LEGACY_CLASSES = ("none", "thumb_index", "thumb_ring")
LEGACY_GESTURES: Mapping[str, str] = MappingProxyType({
    "thumb_ring": "pinch.thumb_ring",     # v0.3 right click
    "thumb_index": "pinch.scroll",   # v0.3 scroll grab
})
_HISTORY = 60   # frames of model output kept (2 s at 30 fps; lookback is 0.5 s)


def _short(values: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (values or {}).items():
        out[key[len(PREFIX):] if key.startswith(PREFIX) else key] = value
    return out


def gestures_for(card: ModelCard) -> Dict[str, str]:
    """class -> gesture id the gate checks for this card."""
    if card.gestures:
        return dict(card.gestures)
    if tuple(card.classes) == LEGACY_CLASSES:
        return dict(LEGACY_GESTURES)
    return {}


@dataclass
class _Waiting:
    intent: Intent
    since: float


class TcnGate:
    id = ID

    def __init__(self, predictor: Predictor, card: ModelCard, settings: Optional[Mapping[str, Any]] = None,
                 gestures: Optional[Mapping[str, str]] = None) -> None:
        cfg = {**DEFAULTS, **_short(settings)}
        self.predictor = predictor
        self.card = card
        self.threshold = float(cfg.get("threshold", card.threshold))
        self.k = int(cfg.get("frames", card.frames_in_a_row))
        self.lookback = float(cfg["lookback"])
        self.wait = float(cfg["wait"])
        mapping = dict(gestures) if gestures is not None else gestures_for(card)
        self.checks: Dict[str, int] = {}
        for cls, gid in mapping.items():
            if cls not in card.classes or cls == "none":
                raise ValueError(f"gate cannot check class {cls!r} (classes {card.classes})")
            self.checks[gid] = card.index(cls)
        if not self.checks:
            raise ValueError("the gate has no gesture to check")
        self._stream = Local3dStream(card.window)
        self._probs: Deque[Tuple[float, Optional[np.ndarray]]] = deque(maxlen=_HISTORY)
        self._waiting: Dict[str, _Waiting] = {}     # gesture id -> held tap / begin
        self._open: set = set()                     # gestures whose begin was passed
        self._dropped: set = set()                  # gestures whose begin was dropped (until their end)
        self.last: Optional[np.ndarray] = None
        self.passed = 0
        self.late = 0
        self.blocked = 0
        self.errors = 0
        self.last_note = ""

    # ---------------------------------------------------------------- model
    def observe(self, hand: Optional[HandObservation], ctx: FrameContext) -> None:
        t = ctx.frame.t
        window = self._stream.push(hand, t, ctx.frame.aspect)
        try:
            probs = np.asarray(self.predictor(window), dtype=np.float64).reshape(-1)
            if probs.shape != (len(self.card.classes),) or not np.isfinite(probs).all():
                raise ValueError(f"bad output {probs.shape}")
        except Exception:   # contracts section 6: an inference error = no probabilities this frame
            probs = None
            self.errors += 1
        self._probs.append((t, probs))
        self.last = probs

    def confirmed(self, gesture: str, now: float) -> bool:
        """The model saw the gesture's class for k frames in a row within the last `lookback` s."""
        c = self.checks[gesture]
        run = 0
        for t, p in self._probs:
            if now - t > self.lookback:
                continue
            run = run + 1 if p is not None and p[c] >= self.threshold else 0
            if run >= self.k:
                return True
        return False

    # ---------------------------------------------------------------- the check
    def filter(self, intents: Sequence[Intent], ctx: FrameContext) -> Sequence[Intent]:
        now = ctx.frame.t
        out: List[Intent] = []
        for gesture in list(self._waiting):                 # held from earlier frames
            w = self._waiting[gesture]
            if self.confirmed(gesture, now):
                del self._waiting[gesture]
                out.append(replace(w.intent, t=now))
                if w.intent.phase == "begin":
                    self._open.add(gesture)
                self.passed += 1
                self.late += 1
                self.last_note = f"TCN 확인: {gesture} (조금 늦게)"
            elif now - w.since > self.wait:
                del self._waiting[gesture]
                if w.intent.phase == "begin":
                    self._dropped.add(gesture)
                self.blocked += 1
                self.last_note = f"TCN: {gesture} 막음 (의도한 동작 아님)"
        for intent in intents:
            if intent.gesture not in self.checks:
                out.append(intent)
                continue
            got = self._check(intent, now)
            if got is not None:
                out.append(got)
        return out

    def _check(self, intent: Intent, now: float) -> Optional[Intent]:
        g, phase = intent.gesture, intent.phase
        if phase == "tap":
            if self.confirmed(g, now):
                self.passed += 1
                self.last_note = f"TCN 확인: {g}"
                return intent
            if g in self._waiting:
                self.blocked += 1                           # the older one is replaced, not queued
            self._waiting[g] = _Waiting(intent, now)
            self.last_note = f"TCN: {g} 확인 중"
            return None
        if phase == "begin":
            self._open.discard(g)
            self._dropped.discard(g)
            self._waiting.pop(g, None)
            if self.confirmed(g, now):
                self._open.add(g)
                self.passed += 1
                self.last_note = f"TCN 확인: {g}"
                return intent
            self._waiting[g] = _Waiting(intent, now)
            self.last_note = f"TCN: {g} 보류 (확인 중)"
            return None
        # hold / end
        if g in self._open:
            if phase == "end":
                self._open.discard(g)
            return intent
        if phase == "end":
            if g in self._waiting and self._waiting[g].intent.phase == "begin":
                del self._waiting[g]
                self.blocked += 1
                self.last_note = f"TCN: {g} 막음 (확인 전에 끝남)"
            self._dropped.discard(g)
        return None

    def reset(self, reason: str) -> None:
        self._stream.reset()
        self._probs.clear()
        self._waiting.clear()
        self._open.clear()
        self._dropped.clear()
        self.last = None

    def status(self) -> Dict[str, Any]:
        """For the Snapshot (S6): counts and the last note. No probabilities history."""
        return {"passed": self.passed, "late": self.late, "blocked": self.blocked, "errors": self.errors,
                "waiting": sorted(self._waiting), "open": sorted(self._open), "note": self.last_note,
                "kind": self.card.kind}


@dataclass(frozen=True)
class GateLoad:
    gate: Optional[TcnGate]
    reason: str = "ok"       # "ok" or a REASON_TEXT key

    @property
    def text(self) -> str:
        return "" if self.gate is not None else REASON_TEXT.get(self.reason, self.reason)


def create_gate(model_path: Path, settings: Optional[Mapping[str, Any]] = None,
                gestures: Optional[Mapping[str, str]] = None,
                session_factory: Optional[Callable[[Path], Any]] = None,
                *, eligible: Optional[set[str]] = None) -> GateLoad:
    """A gate for `<model>.onnx` + `<model>.json`, or no gate and why (never raises for a bad model)."""
    try:
        model = load_model(model_path, FEATURE_VERSION, FEATURES, session_factory=session_factory)
    except ModelError as exc:
        return GateLoad(None, exc.code)
    if eligible is not None:
        mapping = gestures_for(model.card) if gestures is None else gestures
        gestures = {name: gid for name, gid in mapping.items() if gid in eligible}
    try:
        return GateLoad(TcnGate(model.predictor, model.card, settings, gestures))
    except ValueError:
        return GateLoad(None, "no_gestures")
