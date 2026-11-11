"""A second look at the virtual hand's thumb + index clicks by the trained gesture model (TCN, models/gesture_tcn.onnx), 2026-11-10.

The hand-shape rule (src/virtual_hand.py ShapePinch) closes on any thumb + index contact, also a resting hand's. The TCN has seen
the whole movement of a deliberate thumb + index pinch in the recordings. Before a click or a drag goes out, the TCN's "thumb_index"
probability over the press (from LOOK_BACK before the fingers met until now) must reach `threshold` once; otherwise the click is
dropped. The click is sent when the fingers open, so the model has already seen the press: no added delay.

The model loads in a thread; until it is ready, or when it cannot load (no onnxruntime, no model), every click passes.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable, Deque, Optional, Tuple

LOOK_BACK = .3          # seconds before the fingers met that still count (the model reacts to the approach)
CLASS = "thumb_index"


def _load_default():
    from src.gesture_gate import DEFAULT_MODEL, GestureGate, load_predictor
    predict, info = load_predictor(DEFAULT_MODEL)
    return GestureGate(predict, info, {})


class ClickCheck:
    def __init__(self, threshold: float, loader: Optional[Callable[[], Any]] = None, background: bool = True) -> None:
        self.threshold = float(threshold)
        self._loader = loader or _load_default
        self._gate: Any = None
        self.failed = ""
        self._hist: Deque[Tuple[float, float]] = deque(maxlen=120)
        self._index = -1
        self.blocked = 0
        self.passed = 0
        if background:
            threading.Thread(target=self._load, name="click-check-load", daemon=True).start()
        else:
            self._load()

    def _load(self) -> None:
        try:
            gate = self._loader()
            self._index = list(gate.classes).index(CLASS)
            self._gate = gate
        except Exception as exc:          # the clicks work without the check
            self.failed = str(exc) or type(exc).__name__

    @property
    def ready(self) -> bool:
        return self._gate is not None

    def reset(self) -> None:
        self._hist.clear()
        if self._gate is not None:
            self._gate.reset()

    def step(self, now: float, hand: Any, aspect: float) -> None:
        gate = self._gate
        if gate is None:
            return
        gate.step(now, hand, aspect)
        tcn = gate.last_tcn
        self._hist.append((now, float(tcn[self._index]) if tcn is not None else 0.0))

    def peak(self, since: float, now: float) -> Optional[float]:
        ps = [p for t, p in self._hist if since - LOOK_BACK <= t <= now]
        return max(ps) if ps else None

    def allows(self, since: Optional[float], now: float) -> bool:
        """True = send the click. Always True while the model is not loaded or has no frames for this press."""
        if self._gate is None or since is None:
            return True
        p = self.peak(since, now)
        ok = p is None or p >= self.threshold
        if ok:
            self.passed += 1
        else:
            self.blocked += 1
        return ok
