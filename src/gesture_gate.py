"""
Intent check with the trained gesture model (2026-11-05): the rules still decide, the TCN confirms.

models/gesture_tcn.onnx (scripts/train_tcn.py) looks at the last 0.5 s of the hand (src/tcn_features.py) every frame
and gives probabilities for none / thumb_index / thumb_ring. When the rules want to
- right click (thumb + ring): the click goes out only if the model also saw thumb_ring (probability >= threshold for
  `frames_in_a_row` frames within the last `lookback` s). Not yet? The click waits up to `gate_wait` s for it, then
  is dropped.
- start a scroll grab (thumb + index): the grab starts only once the model saw thumb_index; until then the cursor
  just stays put. Once allowed, the grab runs to its end.
Everything else (left click, cursor, the other gestures) is untouched: the model was trained on these two only.
In the held-out postures of the first recording this cut the misfires while moving with a half fist from 51 to 6
(docs/recording.md). Switch: the "손짓 확인 (TCN)" toggle on the Qt screen, or "tcn_gate" in the config.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import numpy as np

from src.tcn_features import FEATURE_VERSION, frame_features, windows
from src.gesture_catalog import NAMES

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "gesture_tcn.onnx"
GATED = {"right_click": "thumb_ring", "left_click": "thumb_middle", "scroll_grab": "thumb_index",
         "task_switcher": "thumb_pinky", "show_desktop": "palm_side"}


def load_predictor(model_path: Path = DEFAULT_MODEL) -> Tuple[Callable[[np.ndarray], np.ndarray], Dict[str, Any]]:
    """(window -> class probabilities, model info). Raises if the model or onnxruntime is missing."""
    import onnxruntime as ort

    info = json.loads(Path(model_path).with_suffix(".json").read_text(encoding="utf-8"))
    if info.get("feature_version") != FEATURE_VERSION:
        raise ValueError(f"model features {info.get('feature_version')} != app features {FEATURE_VERSION}")
    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

    def predict(x: np.ndarray) -> np.ndarray:
        logits = sess.run(None, {"x": x.astype(np.float32)})[0][0]
        e = np.exp(logits - logits.max())
        return e / e.sum()

    return predict, info


class GestureGate:
    def __init__(self, predict: Callable[[np.ndarray], np.ndarray], info: Dict[str, Any],
                 config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.predict = predict
        self.classes: List[str] = list(info["classes"])
        self.window = int(info["window"])
        self.threshold = float(cfg.get("gate_threshold", info.get("threshold", 0.7)))
        self.k = int(cfg.get("gate_frames", info.get("frames_in_a_row", 3)))
        self.lookback = float(cfg.get("gate_lookback", 0.5))
        self.wait = float(cfg.get("gate_wait", 0.3))
        self._frames: Deque[Dict[str, Any]] = deque(maxlen=self.window + 1)
        self._probs: Deque[Tuple[float, np.ndarray]] = deque(maxlen=60)
        self.last: Optional[np.ndarray] = None
        self._pending = None     # a held rule click (action, label, since)
        self._grab_ok = False
        self._continuous = None
        self.blocked = 0                                      # rule actions the model did not confirm
        self.passed = 0
        self.last_note = ""

    def reset(self) -> None:
        self._frames.clear()
        self._probs.clear()
        self._pending = None
        self._grab_ok = False
        self._continuous = None

    # ---------------------------------------------------------------- model
    def step(self, now: float, hand: Any, aspect: float) -> Optional[np.ndarray]:
        if hand is None or not getattr(hand, "world", None):
            self._frames.append({"t": now})
        else:
            self._frames.append({"t": now, "world": hand.world, "lm": [[p.x, p.y, p.z] for p in hand.landmarks]})
        feats = frame_features(list(self._frames), aspect)
        probs = self.predict(windows(feats, self.window)[-1:])
        self._probs.append((now, probs))
        self.last = probs
        return probs

    def confirmed(self, name: str, now: float) -> bool:
        """The model saw `name` for k frames in a row within the last `lookback` s."""
        if name not in self.classes:
            return False
        c = self.classes.index(name)
        run = 0
        for t, p in self._probs:
            if now - t > self.lookback:
                continue
            run = run + 1 if p[c] >= self.threshold else 0
            if run >= self.k:
                return True
        return False

    def allows(self, name: str, now: float) -> bool:
        """Untrained gestures retain their existing rule behavior, including legacy 3-class models."""
        return name not in self.classes or self.confirmed(name, now)

    # ---------------------------------------------------------------- the check
    def filter(self, now: float, result: Any) -> Any:
        """Confirm only learned actions; continuous grabs need confirmation once at their start."""
        action = getattr(result, "action", None)
        label = GATED.get(action)
        if action == "drag_hold":
            label = "thumb_index_middle" if getattr(result, "active_gesture", "") == "thumb_index_middle_pinch" else "thumb_middle"
        continuous = action in ("scroll_grab", "drag_hold")
        if self._continuous != (action, label):
            self._continuous = None
        if action not in (None, "none", "move_cursor"):
            self._pending = None  # a new action supersedes a delayed click
        if label in self.classes:
            if self._continuous == (action, label) or self.confirmed(label, now):
                if continuous:
                    self._continuous = (action, label)
                self.passed += 1
                self.last_note = "TCN 확인: " + NAMES.get(label, label)
                return result
            if not continuous:
                self._pending = (action, label, now)
            result.action = "none"
            result.is_moving = False
            self.last_note = "TCN 확인 대기: " + NAMES.get(label, label)
            return result
        if self._pending is not None:
            kind, pending_label, since = self._pending
            if now - since > self.wait:
                self._pending = None
                self.blocked += 1
                self.last_note = "TCN: 의도 확인 안 됨"
            elif self.confirmed(pending_label, now):
                self._pending = None
                self.passed += 1
                self.last_note = "TCN 확인: " + NAMES.get(pending_label, pending_label)
                if action in ("none", "move_cursor", None):
                    result.action = kind
        return result
