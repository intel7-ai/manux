"""Registered gestures in plain hand-pose space: no neural network, no torch, instant.

The encoder is trained to tell its own classes apart and folds an unseen hand shape into "none", so prototypes of its embedding
recognised new gestures poorly (docs/online-learning-phase3.md). Here a frame is the 63 numbers of the 21 joints in the hand's own frame.
A frame is a registered gesture only when it lies within that gesture's spread AND closer to it than to the poses of every known class.
Adding one is `class_added`: nothing neural changes.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.online.memory import UNKNOWN_LABEL, ClassEntry
from src.tcn_features import FEATURE_VERSION, frame_features

POSE_FILE = "pose_memory.json"
POSE_DIM = 63
SCHEMA = "hand-mouse-pose-memory/1"
CUSTOM_PROTOTYPES = 3
LIMIT_QUANTILE, LIMIT_FACTOR = .9, 1.5
RUN_FRAMES = 4
CANDIDATE_FRAMES = 2


def pose_features(frames: Sequence[Dict[str, Any]], aspect: float) -> np.ndarray:
    """(n, 63): frames with a lost hand are all zeros, like a hand nobody would make."""
    return frame_features(frames, aspect)[:, :POSE_DIM].astype(np.float32)


def distances(x: np.ndarray, prototypes: np.ndarray) -> np.ndarray:
    """Root mean square joint distance to the nearest prototype, in hand sizes."""
    x = np.atleast_2d(np.asarray(x, dtype=np.float32))
    return np.sqrt(((x[:, None, :] - prototypes[None, :, :]) ** 2).sum(-1)).min(axis=1) / np.sqrt(21.0)


def pose_kmeans(x: np.ndarray, k: int, seed: int = 0, rounds: int = 8, max_points: int = 3000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=np.float32)
    if len(x) > max_points:
        x = x[rng.choice(len(x), max_points, replace=False)]
    c = x[rng.integers(len(x))][None]
    while len(c) < min(k, len(x)):
        c = np.vstack([c, x[int(((x[:, None] - c[None]) ** 2).sum(-1).min(axis=1).argmax())]])
    for _ in range(rounds):
        owner = ((x[:, None] - c[None]) ** 2).sum(-1).argmin(axis=1)
        c = np.stack([x[owner == i].mean(axis=0) if (owner == i).any() else c[i] for i in range(len(c))])
    return c


class PoseMemory:
    def __init__(self, feature_version: str = FEATURE_VERSION) -> None:
        self.feature_version = feature_version
        self.classes: Dict[str, ClassEntry] = {}

    @property
    def labels(self) -> List[str]:
        return list(self.classes)

    def add_known(self, label: str, poses: np.ndarray, *, k: int = 4, seed: int = 0, source: str = "guided") -> ClassEntry:
        """A class the app already has: other gestures must beat it."""
        entry = ClassEntry(label, pose_kmeans(poses, k, seed), threshold=float("inf"), samples=len(poses), source=source)
        self.classes[label] = entry
        return entry

    def add_class(self, label: str, embeddings: np.ndarray, *, demo_ids: Optional[Sequence[int]] = None, source: str = "", seed: int = 0,
                  extra: Optional[Dict[str, Any]] = None) -> ClassEntry:
        """A registered gesture. Its distance limit comes from each demonstration measured against the others, so it is a fair spread."""
        if not label or label == UNKNOWN_LABEL:
            raise ValueError("a class needs a real name")
        x = np.asarray(embeddings, dtype=np.float32)
        if x.ndim != 2 or len(x) == 0:
            raise ValueError("no poses")
        ids = np.zeros(len(x), dtype=int) if demo_ids is None else np.asarray(demo_ids)
        groups = np.unique(ids)
        if len(groups) >= 2:
            spread = np.concatenate([distances(x[ids == g], pose_kmeans(x[ids != g], CUSTOM_PROTOTYPES, seed)) for g in groups])
        else:
            spread = distances(x, pose_kmeans(x, CUSTOM_PROTOTYPES, seed))
        prototypes = pose_kmeans(x, CUSTOM_PROTOTYPES, seed)
        limit = float(np.quantile(spread, LIMIT_QUANTILE)) * LIMIT_FACTOR
        entry = ClassEntry(label, prototypes, threshold=max(limit, 1e-3), samples=len(x), demos=len(groups), source=source,
                           distance_mean=float(spread.mean()), distance_std=float(spread.std()), extra=dict(extra or {}))
        self.classes[label] = entry
        return entry

    def remove_class(self, label: str) -> bool:
        return self.classes.pop(label, None) is not None

    def classify_arrays(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Class index per frame: a registered gesture when it wins, otherwise the nearest known class, -1 when there is none."""
        x = np.atleast_2d(np.asarray(x, dtype=np.float32))
        entries = list(self.classes.values())
        d = np.stack([distances(x, e.prototypes) for e in entries], axis=1)
        limits = np.array([e.threshold for e in entries])
        allowed = np.where(d <= limits, d, np.inf)
        best = allowed.argmin(axis=1)
        rows = np.arange(len(x))
        index = np.where(np.isfinite(allowed[rows, best]), best, -1)
        return index, d[rows, best], np.zeros(len(x))

    def classify(self, pose: np.ndarray) -> Dict[str, Any]:
        index, dist, _ = self.classify_arrays(pose)
        i = int(index[0])
        return {"label": self.labels[i] if i >= 0 else UNKNOWN_LABEL, "distance": float(dist[0])}

    def to_dict(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "feature_version": self.feature_version,
                "classes": [{"label": e.label, "prototypes": np.round(e.prototypes, 5).tolist(), "threshold": None if np.isinf(e.threshold) else e.threshold,
                             "samples": e.samples, "demos": e.demos, "source": e.source, "distance_mean": e.distance_mean,
                             "distance_std": e.distance_std, "extra": e.extra} for e in self.classes.values()]}

    @classmethod
    def from_dict(cls, doc: Dict[str, Any]) -> "PoseMemory":
        if doc.get("schema") != SCHEMA:
            raise ValueError("not a pose memory")
        memory = cls(doc.get("feature_version", ""))
        for row in doc["classes"]:
            memory.classes[row["label"]] = ClassEntry(
                row["label"], np.asarray(row["prototypes"], dtype=np.float32), float("inf") if row["threshold"] is None else float(row["threshold"]),
                row["samples"], row["demos"], row["source"], "", row["distance_mean"], row["distance_std"], row.get("extra") or {})
        return memory

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return path

    @classmethod
    def load(cls, path: Path | str) -> "PoseMemory":
        memory = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        if memory.feature_version != FEATURE_VERSION:
            raise ValueError(f"pose memory uses features {memory.feature_version}, the code has {FEATURE_VERSION}")
        return memory


class PoseRecognizer:
    """Frame by frame: a registered gesture that has won `run` frames in a row. Known classes only compete; they are never reported."""

    def __init__(self, memory: PoseMemory, aspect: float = 720 / 1280, run: int = RUN_FRAMES) -> None:
        self.memory, self.aspect, self.run = memory, aspect, run
        self._label: Optional[str] = None
        self._count = 0
        self.current: Optional[str] = None

    def reset(self) -> None:
        self._label, self._count, self.current = None, 0, None

    @property
    def candidate(self) -> Optional[str]:
        """The gesture being made, a little before it counts: two frames in a row are enough to stop the cursor."""
        return self._label if self._count >= CANDIDATE_FRAMES else None

    def step(self, t: float, lm: Any, world: Any, source: str = "user_registered") -> Optional[str]:
        if lm is None or world is None:
            self.reset()
            return None
        pose = pose_features([{"t": t, "lm": lm, "world": world}], self.aspect)
        label = self.memory.classify(pose)["label"]
        entry = self.memory.classes.get(label)
        if entry is None or entry.source != source:
            self.reset()
            return None
        if label == self._label:
            self._count += 1
        else:
            self._label, self._count, self.current = label, 1, None
        if self._count == self.run:
            self.current = label
            return label
        return None
