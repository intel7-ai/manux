"""The hand-picture CNN in "shadow" mode: it reads the hand while the app runs and only WRITES what it thinks.
Nothing it says reaches the cursor, the clicks or the gate (docs/far-hand-loss.md, 25).

Brought over from hand-mouse-1109 (2026-11-09 18:11, home-pc). It is NOT connected to the app yet: nothing imports it, and
the "crop_shadow" / "crop_shadow_models" settings it reads are not in config/gestures.json. When it is connected, `update`
must get the frame BEFORE the preview drawing (the `clean` copy in qt_runtime, docs/far-hand-loss.md 24), or the picture
carries the pinch colours again.

Each model is models/<name>.onnx + models/<name>.json: the json tells the classes, how many pictures are stacked
(frames, step) and whether only the middle of the saved 48 x 48 is used (center). There is no export script for this
format yet (the 1109 docstring named scripts/export_crop_cnn.py, which never existed).

Only numpy, cv2 (via crop_recorder) and onnxruntime: no torch, so the app process never needs the training extra.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from src.crop_recorder import crop_hand

RESET_AFTER_MISSES = 10      # frames without a hand before the picture history is dropped (a new hand is a new history)


def centre_crop(pics: np.ndarray, side: int) -> np.ndarray:
    """Same as src.crop_cnn.centre_crop (copied so this file does not import torch)."""
    full = pics.shape[-1]
    if side <= 0 or side >= full:
        return pics
    lo = (full - side) // 2
    return np.ascontiguousarray(pics[..., lo:lo + side, lo:lo + side])


class ShadowModel:
    def __init__(self, session: Any, info: Dict[str, Any], name: str) -> None:
        self.session, self.info, self.name = session, info, name
        self.classes: List[str] = list(info["classes"])
        self.frames, self.step = int(info["frames"]), int(info["step"])
        self.center = int(info.get("center", 0))
        self.input_name = session.get_inputs()[0].name

    def predict(self, history: Sequence[np.ndarray]) -> Dict[str, float]:
        n = len(history)
        picks = [max(n - 1 - self.step * k, 0) for k in range(self.frames)][::-1]       # oldest first, the first picture repeats
        stack = centre_crop(np.stack([history[i] for i in picks]), self.center)          # (frames, side, side)
        x = stack[None].astype(np.float32) / 255.0
        logits = np.asarray(self.session.run(None, {self.input_name: x})[0][0], dtype=np.float64)
        e = np.exp(logits - logits.max())
        e /= e.sum()
        return {c: round(float(p), 3) for c, p in zip(self.classes, e) if c != "none"}


def load_model(json_path: Path | str) -> ShadowModel:
    import onnxruntime as ort
    json_path = Path(json_path)
    info = json.loads(json_path.read_text(encoding="utf-8"))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(json_path.with_suffix(".onnx")), sess_options=options, providers=["CPUExecutionProvider"])
    return ShadowModel(session, info, json_path.stem.replace("crop_cnn_", ""))


class CropShadow:
    """Keeps the last pictures of the hand and asks every model for its reading of the newest frame."""

    def __init__(self, models: Sequence[ShadowModel]) -> None:
        self.models = list(models)
        depth = max(((m.frames - 1) * m.step + 1 for m in self.models), default=1)
        self.history: deque = deque(maxlen=depth)
        self.misses = 0

    @classmethod
    def from_config(cls, config: Dict[str, Any], root: Path) -> Optional["CropShadow"]:
        """None when the feature is off, a model is missing or onnxruntime is not installed: the app must never fail because of it."""
        if not config.get("crop_shadow"):
            return None
        models = []
        for rel in config.get("crop_shadow_models") or []:
            try:
                models.append(load_model(root / rel))
            except Exception:
                continue
        return cls(models) if models else None

    def reset(self) -> None:
        self.history.clear()
        self.misses = 0

    def update(self, frame_bgr: Optional[np.ndarray], landmarks: Optional[Sequence]) -> Optional[Dict[str, Dict[str, float]]]:
        """{model name: {class: probability}} for this frame, or None when there is no hand (or not enough to read).
        `frame_bgr` must be the clean frame (no preview drawing on it)."""
        picture = crop_hand(frame_bgr, landmarks) if frame_bgr is not None and landmarks else None
        if picture is None:
            self.misses += 1
            if self.misses >= RESET_AFTER_MISSES:
                self.history.clear()
            return None
        self.misses = 0
        self.history.append(picture)
        hist = list(self.history)
        return {m.name: m.predict(hist) for m in self.models}
