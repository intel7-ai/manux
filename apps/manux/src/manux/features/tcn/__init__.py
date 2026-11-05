"""Direct, opt-in TCN decisions. No rules, bindings, camera or OS input here."""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Optional

import numpy as np

from manux.core.contracts import Activity, FrameContext, HandObservation, Intent, OutputTarget
from manux.ml.card import ModelCard, ModelError
from manux.ml.predictor import Predictor
from manux.perception.local3d import Local3dStream, PALM

ID = "tcn"
SETTINGS = MappingProxyType({"tcn.max_gap": 0.3})
GESTURES = ()                          # instances declare the checked card's gestures
DEFAULT_BINDINGS = MappingProxyType({}) # action choices belong to the user's bindings


class TcnFeature:
    id = ID

    def __init__(self, predictor: Predictor, card: ModelCard) -> None:
        if not card.decisions or set(card.decisions) != set(card.gestures) or "none" not in card.classes:
            raise ModelError("decisions")
        if tuple(predictor.classes) != card.classes:
            raise ModelError("shape", "predictor classes differ from the card")
        self.predictor, self.card = predictor, card
        self.gestures = tuple(card.gestures.values())
        self.modes = frozenset(m for d in card.decisions.values() for m in d.modes)
        self.gesture_phases = {card.gestures[c]: d.phases for c, d in card.decisions.items()}
        self._stream = Local3dStream(card.window)
        self.reset("settings")

    def reset(self, reason: str) -> None:
        self._stream.reset()
        self._valid = 0
        self._last_t = None
        self._mode = None
        self._candidate = None
        self._run = self._neutral_run = 0
        self._armed = False
        self._open = None
        self._engaged = False
        self._point = None
        self._release_t = -math.inf

    def activity(self) -> Activity:
        return Activity(self._open is not None or self._engaged, self._release_t)

    def _end(self, t: float) -> list[Intent]:
        if self._open is None:
            return []
        gesture = self.card.gestures[self._open]
        self._open = None
        self._release_t = t
        return [Intent(t, ID, gesture, "end", point=self._point)]

    def _point_for(self, name: str, hand: HandObservation):
        mode = self.card.decisions[name].point
        if mode == "none":
            return None
        rows = (8,) if mode == "index" else PALM
        return tuple(min(1.0, max(0.0, sum(hand.landmarks[i][axis] for i in rows) / len(rows)))
                     for axis in (0, 1))

    def step(self, hand: Optional[HandObservation], ctx: FrameContext):
        now = ctx.frame.t
        gap = float(ctx.settings.get("max_gap", 0.3))
        if self._mode != ctx.mode or self._last_t is not None and (now <= self._last_t or now - self._last_t > gap):
            ends = self._end(now)
            self.reset("mode_change")
        else:
            ends = []
        self._mode, self._last_t = ctx.mode, now
        if ctx.output is OutputTarget.NONE or hand is None or hand.world is None:
            ends += self._end(now)
            self.reset("hand_lost")
            return ends
        window = self._stream.push(hand, now, ctx.frame.aspect)
        if window[-1, -1] != 1:
            ends += self._end(now)
            self.reset("hand_lost")
            return ends
        self._valid += 1
        if self._valid < self.card.window:
            return ends
        # Let inference errors reach the engine: it releases output and resets the feature.
        scores = np.asarray(self.predictor(window), dtype=np.float64)
        if (scores.shape != (len(self.card.classes),) or not np.isfinite(scores).all()
                or (scores < 0).any() or (scores > 1).any() or not np.isclose(scores.sum(), 1.0)):
            raise ValueError("invalid TCN probabilities")
        best = int(scores.argmax())
        name, confidence = self.card.classes[best], float(scores[best])
        decision = self.card.decisions.get(name)
        eligible = decision is not None and ctx.mode in decision.modes and confidence >= decision.threshold
        if self._engaged and not eligible:
            self._release_t = now
        self._engaged = bool(eligible)
        if self._open is not None:
            if eligible and name == self._open:
                self._point = self._point_for(name, hand)
                return ends + [Intent(now, ID, self.card.gestures[name], "hold", self._point, confidence)]
            ends += self._end(now)
            self._armed = False
        neutral = name == "none" and confidence >= self.card.threshold
        self._neutral_run = self._neutral_run + 1 if neutral else 0
        if self._neutral_run >= self.card.frames_in_a_row:
            self._armed = True
        candidate = name if eligible else None
        self._run = self._run + 1 if candidate is not None and candidate == self._candidate else (1 if candidate else 0)
        self._candidate = candidate
        if not self._armed or not eligible or self._run < decision.frames_in_a_row:
            return ends
        self._armed = False
        self._point = self._point_for(name, hand)
        if decision.kind == "span":
            self._open = name
            phase = "begin"
        else:
            self._release_t = now
            phase = "tap"
        return ends + [Intent(now, ID, self.card.gestures[name], phase, self._point, confidence)]
