"""The encoder in the running app: one embedding per frame on the CPU, read-only. No training happens here.

Torch is imported only when this is used, so an install without the "train" extras still runs the app (without these features).
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence

import numpy as np

from src.online.memory import PrototypeMemory
from src.online.samples import DEFAULT_ROOT
from src.tcn_features import FEATURE_VERSION, FEATURES, frame_features, windows

ENCODER_FILE = "encoder.pt"
ENCODER_INFO = "encoder.json"
MEMORY_FILE = "memory.json"
RUN_FRAMES = 4
HEAD_CONFIDENCE = .9     # docs/online-learning-phase3.md: distance alone took 65% of unregistered gestures for known ones, this 22%


def encoder_ready(root: Path | str = DEFAULT_ROOT) -> bool:
    root = Path(root)
    return (root / ENCODER_FILE).is_file() and (root / ENCODER_INFO).is_file() and (root / MEMORY_FILE).is_file()


def save_encoder(model: Any, info: Dict[str, Any], root: Path | str = DEFAULT_ROOT) -> str:
    """Stores weights and returns their version (a hash of the weights, so prototypes can tell which encoder made them)."""
    import torch
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
    digest = hashlib.sha256()
    for key in sorted(state):
        digest.update(key.encode())
        digest.update(state[key].numpy().tobytes())
    version = digest.hexdigest()[:16]
    tmp = root / (ENCODER_FILE + ".tmp")
    torch.save(state, tmp)
    tmp.replace(root / ENCODER_FILE)
    (root / ENCODER_INFO).write_text(json.dumps({**info, "version": version, "feature_version": FEATURE_VERSION}, ensure_ascii=False), encoding="utf-8")
    return version


class LiveEncoder:
    def __init__(self, root: Path | str = DEFAULT_ROOT, window: Optional[int] = None) -> None:
        import torch

        from src.online.models import make_model
        self._torch = torch
        root = Path(root)
        self.root = root
        self.info = json.loads((root / ENCODER_INFO).read_text(encoding="utf-8"))
        if self.info.get("feature_version") != FEATURE_VERSION:
            raise ValueError(f"encoder uses features {self.info.get('feature_version')}, the code has {FEATURE_VERSION}")
        self.window = int(window or self.info["window"])
        self.version = str(self.info["version"])
        torch.set_num_threads(1)     # one small model per frame: more threads would only compete with the camera loop
        self.model = make_model(self.info["kind"], len(self.info["classes"]), self.info["window"], self.info["embedding"])
        self.model.load_state_dict(torch.load(root / ENCODER_FILE, map_location="cpu", weights_only=True))
        self.model.eval()

    def embed_windows(self, x: np.ndarray) -> np.ndarray:
        with self._torch.no_grad():
            return self.model.embed(self._torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32))).numpy()

    def head_probabilities(self, z: np.ndarray) -> np.ndarray:
        with self._torch.no_grad():
            logits = self.model.head(self._torch.from_numpy(np.ascontiguousarray(z, dtype=np.float32)))
            return self._torch.softmax(logits, dim=1).numpy()

    def confident(self, z: np.ndarray, conf: float = HEAD_CONFIDENCE) -> np.ndarray:
        """True where the classifier is sure of one of the classes it was trained on: a new gesture must not claim those windows."""
        return self.head_probabilities(z).max(axis=1) >= conf

    def embed_frames(self, frames: Sequence[Dict[str, Any]], aspect: float, batch: int = 512) -> np.ndarray:
        """(n, embedding): one per frame, each from the window ending there."""
        x = windows(frame_features(frames, aspect), self.window)
        return np.concatenate([self.embed_windows(x[i:i + batch]) for i in range(0, len(x), batch)]) if len(x) else np.zeros((0, self.model.embedding), np.float32)


class LiveRecognizer:
    """Frame by frame: the current class if it holds `run` frames in a row. An unknown window ends the run, so nothing is forced."""

    def __init__(self, encoder: LiveEncoder, memory: PrototypeMemory, aspect: float = 720 / 1280, run: int = RUN_FRAMES,
                 confidence: float = HEAD_CONFIDENCE) -> None:
        self.encoder, self.memory, self.aspect, self.run, self.confidence = encoder, memory, aspect, run, confidence
        # one frame more than the window: the change since the frame before must be real in the window's first frame, as in training
        self._frames: Deque[Dict[str, Any]] = deque(maxlen=encoder.window + 1)
        self._label: Optional[str] = None
        self._count = 0
        self.current: Optional[str] = None     # the class standing now

    def decide(self, z: np.ndarray) -> str:
        """Several signals, never one: the nearest prototype within its distance limit, and the classifier. A class the classifier knows
        needs it to agree with confidence; a registered class needs it to be unsure. Anything else is unknown."""
        label = self.memory.classify(z[0])["label"]
        if label == "unknown":
            return label
        probs = self.encoder.head_probabilities(z)[0]
        classes = self.encoder.info["classes"]
        top = int(probs.argmax())
        if label in classes:
            return label if classes[top] == label and probs[top] >= self.confidence else "unknown"
        return label if probs[top] < self.confidence else "unknown"

    def reset(self) -> None:
        self._frames.clear()
        self._label, self._count, self.current = None, 0, None

    def step(self, t: float, lm: Any, world: Any) -> Optional[str]:
        """Returns a class name on the frame it has just stood for `run` frames, else None."""
        self._frames.append({"t": float(t), "lm": lm, "world": world})
        if lm is None or world is None:
            self._label, self._count, self.current = None, 0, None
            return None
        features = frame_features(list(self._frames), self.aspect)[-self.encoder.window:]
        pad = self.encoder.window - len(features)
        if pad > 0:
            features = np.concatenate([np.zeros((pad, FEATURES), features.dtype), features])
        z = self.encoder.embed_windows(features[None, :, :])
        label = self.decide(z)
        if label == "unknown":
            self._label, self._count, self.current = None, 0, None
            return None
        if label == self._label:
            self._count += 1
        else:
            self._label, self._count, self.current = label, 1, None
        if self._count == self.run:
            self.current = label
            return label
        return None


def memory_labels(memory: PrototypeMemory, source: str) -> List[str]:
    return [e.label for e in memory.classes.values() if e.source == source]
