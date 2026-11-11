"""Immediate gesture addition: a memory of class prototypes in the encoder's embedding space (Snell et al. 2017 for prototypes;
Mai et al. 2021 for nearest-class-mean in online class-incremental learning). Adding a class here is `class_added`: no neural weight
changes. Nothing here is a claim of learning by gradient (that is Phase 4).

mode "ncm"       one mean per class
mode "multi"     up to `k` means per class (k-means), for gestures done in several ways
mode "exemplar"  the mean of each demonstration, nearest one wins

A window whose nearest class is too far (beyond that class's own distance spread) or too close to a second class is unknown: it
is never forced onto a class.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MODES = ("ncm", "multi", "exemplar")
UNKNOWN_LABEL = "unknown"
DEFAULT_MIN_THRESHOLD = .02


def normalize(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z, dtype=np.float32)
    return z / np.maximum(np.linalg.norm(z, axis=-1, keepdims=True), 1e-8)


def kmeans(z: np.ndarray, k: int, seed: int = 0, rounds: int = 12) -> np.ndarray:
    """Cosine k-means. Returns at most `k` normalized centres, none of them empty."""
    z = normalize(z)
    k = min(k, len(z))
    rng = np.random.default_rng(seed)
    centres = [z[rng.integers(len(z))]]
    while len(centres) < k:                       # spread the start: the point farthest from the centres so far
        far = 1 - (z @ np.stack(centres).T).max(axis=1)
        centres.append(z[int(far.argmax())])
    c = np.stack(centres)
    for _ in range(rounds):
        owner = (z @ c.T).argmax(axis=1)
        c = np.stack([normalize(z[owner == i].mean(axis=0)) if (owner == i).any() else c[i] for i in range(len(c))])
    keep = [i for i in range(len(c)) if ((z @ c.T).argmax(axis=1) == i).any()]
    return c[keep]


@dataclass
class ClassEntry:
    label: str
    prototypes: np.ndarray           # (k, d), normalized
    threshold: float                 # distance beyond which a window is not this class
    samples: int = 0
    demos: int = 0
    source: str = ""
    encoder_version: str = ""
    distance_mean: float = 0.0
    distance_std: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


class PrototypeMemory:
    def __init__(self, mode: str = "ncm", *, k: int = 3, threshold_factor: float = 1.25, quantile: float = .95,
                 min_margin: float = .0, min_threshold: float = DEFAULT_MIN_THRESHOLD, encoder_version: str = "") -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.mode, self.k, self.threshold_factor, self.quantile = mode, k, threshold_factor, quantile
        self.min_margin, self.min_threshold, self.encoder_version = min_margin, min_threshold, encoder_version
        self.classes: Dict[str, ClassEntry] = {}

    # ---------------------------------------------------------------- classes
    def _prototypes(self, z: np.ndarray, demo_ids: Optional[np.ndarray], seed: int) -> np.ndarray:
        if self.mode == "ncm":
            return normalize(z.mean(axis=0))[None, :]
        if self.mode == "multi":
            return kmeans(z, self.k, seed)
        demos = np.zeros(len(z), dtype=int) if demo_ids is None else np.asarray(demo_ids)
        return normalize(np.stack([z[demos == d].mean(axis=0) for d in np.unique(demos)]))

    def add_class(self, label: str, embeddings: np.ndarray, *, demo_ids: Optional[Sequence[int]] = None, source: str = "",
                  seed: int = 0, extra: Optional[Dict[str, Any]] = None) -> ClassEntry:
        """Windows of one class. A label already stored is replaced. Event `class_added`."""
        if label == UNKNOWN_LABEL or not label:
            raise ValueError("a class needs a real name")
        z = normalize(embeddings)
        if z.ndim != 2 or len(z) == 0:
            raise ValueError("no embeddings")
        ids = None if demo_ids is None else np.asarray(demo_ids)
        protos = self._prototypes(z, ids, seed)
        dist = 1 - (z @ protos.T).max(axis=1)
        limit = max(float(np.quantile(dist, self.quantile)) * self.threshold_factor, self.min_threshold)
        entry = ClassEntry(label, protos, limit, samples=len(z), demos=len(np.unique(ids)) if ids is not None else 0, source=source,
                           encoder_version=self.encoder_version, distance_mean=float(dist.mean()), distance_std=float(dist.std()),
                           extra=dict(extra or {}))
        self.classes[label] = entry
        return entry

    def remove_class(self, label: str) -> bool:
        return self.classes.pop(label, None) is not None

    @property
    def labels(self) -> List[str]:
        return list(self.classes)

    # ---------------------------------------------------------------- recognition
    def distances(self, embeddings: np.ndarray) -> np.ndarray:
        """(n, classes): cosine distance to each class's nearest prototype."""
        z = normalize(embeddings)
        if z.ndim == 1:
            z = z[None, :]
        return np.stack([1 - (z @ e.prototypes.T).max(axis=1) for e in self.classes.values()], axis=1)

    def classify_arrays(self, embeddings: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Class index per window, -1 = unknown; its distance; and the margin to the second class."""
        d = self.distances(embeddings)
        order = np.argsort(d, axis=1)
        rows = np.arange(len(d))
        best, best_d = order[:, 0], d[rows, order[:, 0]]
        margin = d[rows, order[:, 1]] - best_d if d.shape[1] > 1 else np.full(len(d), np.inf)
        limits = np.array([e.threshold for e in self.classes.values()])
        accepted = (best_d <= limits[best]) & (margin >= self.min_margin)
        return np.where(accepted, best, -1), best_d, margin

    def classify(self, embedding: np.ndarray) -> Dict[str, Any]:
        index, dist, margin = self.classify_arrays(np.asarray(embedding)[None, :])
        i = int(index[0])
        return {"label": self.labels[i] if i >= 0 else UNKNOWN_LABEL, "distance": float(dist[0]), "margin": float(margin[0])}

    def conflicts(self, label: str) -> List[Dict[str, Any]]:
        """Other classes whose acceptance regions overlap `label`'s: prototypes closer than the two distance limits together.
        A fixed cosine would not do: these embeddings are all positive, so every pair looks similar (it warned on 96-100% of
        registrations). Closest first."""
        entry = self.classes[label]
        out = []
        for other in self.classes.values():
            if other.label != label:
                gap = 1 - float((entry.prototypes @ other.prototypes.T).max())
                if gap < entry.threshold + other.threshold:
                    out.append({"label": other.label, "cosine": 1 - gap})
        return sorted(out, key=lambda c: -c["cosine"])

    # ---------------------------------------------------------------- saving
    def to_dict(self) -> Dict[str, Any]:
        return {"schema": "hand-mouse-prototypes/1", "mode": self.mode, "k": self.k, "threshold_factor": self.threshold_factor,
                "quantile": self.quantile, "min_margin": self.min_margin, "min_threshold": self.min_threshold,
                "encoder_version": self.encoder_version,
                "classes": [{"label": e.label, "prototypes": e.prototypes.tolist(), "threshold": e.threshold, "samples": e.samples,
                             "demos": e.demos, "source": e.source, "encoder_version": e.encoder_version, "distance_mean": e.distance_mean,
                             "distance_std": e.distance_std, "extra": e.extra} for e in self.classes.values()]}

    @classmethod
    def from_dict(cls, doc: Dict[str, Any]) -> "PrototypeMemory":
        if doc.get("schema") != "hand-mouse-prototypes/1":
            raise ValueError("not a prototype memory")
        memory = cls(doc["mode"], k=doc["k"], threshold_factor=doc["threshold_factor"], quantile=doc["quantile"],
                     min_margin=doc["min_margin"], min_threshold=doc["min_threshold"], encoder_version=doc["encoder_version"])
        for row in doc["classes"]:
            memory.classes[row["label"]] = ClassEntry(row["label"], np.asarray(row["prototypes"], dtype=np.float32), row["threshold"], row["samples"],
                                                      row["demos"], row["source"], row["encoder_version"], row["distance_mean"],
                                                      row["distance_std"], row.get("extra") or {})
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
    def load(cls, path: Path | str) -> "PrototypeMemory":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
