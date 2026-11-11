"""The production online model as the mouse's safety gate (online-learning plan, section 10).

It reuses the app's intent checker (GestureGate): a click or the start of a scroll/drag that the rule engine proposes goes through only when
the model has seen the same gesture for k frames in a row above its threshold, the hand is tracked well, and (for risky gestures) it was held.
On top of that, for this model: a clear winner (top-1 minus top-2), and nothing passes for a moment after the model was swapped.
Pointer movement is not gated; gestures the model does not know keep their rule behaviour (as with the existing checker).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from src.gesture_gate import GestureGate

MARGIN = .2              # top-1 minus top-2 probability
SWAP_BLOCK_SECONDS = 1.0


class OnlineGate(GestureGate):
    def __init__(self, root: Path | str, config: Optional[Dict[str, Any]] = None, *, margin: float = MARGIN,
                 swap_block: float = SWAP_BLOCK_SECONDS, predictor: Any = None) -> None:
        if predictor is None:
            from src.online.app_job import ProductionPredictor
            predictor = ProductionPredictor(root)
        import torch
        model = predictor.model

        def predict(x: np.ndarray) -> np.ndarray:
            with torch.no_grad():
                return torch.softmax(model(torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))), 1).numpy()[0]

        info = {"classes": list(predictor.classes), "window": predictor.window, "threshold": .7, "frames_in_a_row": 3}
        super().__init__(predict, info, {**(config or {}), "spatial_mlp_enabled": False})
        self.margin, self.swap_block = margin, swap_block
        self.blocked_until: Optional[float] = None
        self.model_loaded = getattr(predictor, "loaded", time.time())

    def step(self, now: float, hand: Any, aspect: float):
        if self.blocked_until is None:
            self.blocked_until = now + self.swap_block      # just swapped in: its first frames have no history yet
        return super().step(now, hand, aspect)

    def _clear_winner(self) -> bool:
        p = self.last
        if p is None or len(p) < 2:
            return False
        top = np.sort(np.asarray(p))[::-1]
        return float(top[0] - top[1]) >= self.margin

    def confirmed(self, name: str, now: float) -> bool:
        if self.blocked_until is None or now < self.blocked_until:
            self.last_rejection_reason = "model_swap"
            return False
        if not super().confirmed(name, now):
            return False
        if not self._clear_winner():
            self.last_rejection_reason = "close_call"
            return False
        return True

    def rejection_reason_ko(self) -> str:
        if self.last_rejection_reason == "model_swap":
            return "모델을 막 바꿔서 잠시 보류"
        if self.last_rejection_reason == "close_call":
            return "두 동작 사이에서 확실하지 않음"
        return super().rejection_reason_ko()
