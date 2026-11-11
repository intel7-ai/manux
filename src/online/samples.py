"""Stored gesture sequences for online learning: joints and metadata, never camera pictures.

data/online/index.jsonl holds one line of metadata per sample and data/online/samples/<id>.npz its joints. Every write goes to a
temporary file first, so a crash leaves the old state. A broken index line or sample is skipped, not fatal.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from src.tcn_features import FEATURE_VERSION, frame_features

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / "data" / "online"
DEFAULT_MAX_BYTES = 2 * 1024 ** 3

USER_CONFIRMED = "user_confirmed"
USER_CORRECTION = "user_correction"
GUIDED_SCHEDULED = "guided_scheduled"       # a label from the schedule of a guided recording: nobody checked this repetition
UNLABELED_UNKNOWN = "unlabeled_unknown"
PSEUDO_CANDIDATE = "pseudo_labeled_candidate"
VALIDATED_PSEUDO = "validated_pseudo_label"
LABEL_SOURCES = (USER_CONFIRMED, USER_CORRECTION, GUIDED_SCHEDULED, UNLABELED_UNKNOWN, PSEUDO_CANDIDATE, VALIDATED_PSEUDO)
SUPERVISED_SOURCES = (USER_CONFIRMED, USER_CORRECTION)   # the only targets of online updates unless an experiment says otherwise
UNKNOWN = "unknown"


@dataclass
class Sample:
    sample_id: str
    label: str
    label_source: str
    session: str
    created: float
    duration: float
    fps: float
    frames: int
    tracking_quality: float          # share of frames with the hand
    quality_score: float
    approved: bool = False
    handedness: str = ""
    preprocessing_version: str = FEATURE_VERSION
    source_model_version: str = ""
    suggested_label: str = ""        # what the app thought it was (never a training target by itself)
    task: str = ""
    condition: str = ""
    pc: str = ""
    camera: str = ""
    aspect: float = 720 / 1280
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, doc: Dict[str, Any]) -> "Sample":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in doc.items() if k in known})


def frames_to_arrays(frames: Sequence[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    """Recording-style frames ({"t", "lm", "world"}, no joints = hand lost) -> compact arrays."""
    n = len(frames)
    t = np.zeros(n, np.float64)
    lm = np.zeros((n, 21, 3), np.float32)
    world = np.zeros((n, 21, 3), np.float32)
    has_hand = np.zeros(n, bool)
    for i, f in enumerate(frames):
        t[i] = float(f.get("t", 0.0))
        if f.get("lm") is not None and f.get("world") is not None and len(f["lm"]) == 21 and len(f["world"]) == 21:
            lm[i], world[i], has_hand[i] = f["lm"], f["world"], True
    return {"t": t, "lm": lm, "world": world, "has_hand": has_hand}


def arrays_to_frames(arrays: Dict[str, np.ndarray]) -> List[Dict[str, Any]]:
    return [{"t": float(arrays["t"][i]), "lm": arrays["lm"][i].tolist() if arrays["has_hand"][i] else None,
             "world": arrays["world"][i].tolist() if arrays["has_hand"][i] else None} for i in range(len(arrays["t"]))]


class SampleStore:
    def __init__(self, root: Path | str = DEFAULT_ROOT, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.root = Path(root)
        self.samples_dir = self.root / "samples"
        self.index_path = self.root / "index.jsonl"
        self.max_bytes = int(max_bytes)
        self._lock = threading.RLock()
        self._items: Dict[str, Sample] = {}
        self._sizes: Dict[str, int] = {}
        self._gone: set = set()          # ids this store removed (never taken back from the index on disk)
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---------------------------------------------------------------- index
    def _load(self) -> None:
        if not self.index_path.is_file():
            return
        with open(self.index_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    sample = Sample.from_dict(json.loads(line))
                except (ValueError, TypeError):
                    continue
                path = self._path(sample.sample_id)
                if path.is_file():
                    self._items[sample.sample_id] = sample
                    self._sizes[sample.sample_id] = path.stat().st_size

    def _path(self, sample_id: str) -> Path:
        return self.samples_dir / f"{sample_id}.npz"

    def _sync_with_disk(self) -> None:
        """Other stores write the same index (the collector, a registration, the update job each open their own). A
        rewrite from this store's memory alone dropped what they had added since it was loaded: a newly registered
        gesture's samples vanished from the index on the next F correction, while their files stayed on disk."""
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                sample = Sample.from_dict(json.loads(line))
            except (ValueError, TypeError):
                continue
            sid = sample.sample_id
            if sid in self._items or sid in self._gone:
                continue
            path = self._path(sid)
            if path.is_file():
                self._items[sid] = sample
                self._sizes[sid] = path.stat().st_size
        for sid in [i for i in self._items if not self._path(i).is_file()]:    # removed by another store
            self._items.pop(sid, None)
            self._sizes.pop(sid, None)

    def _rewrite_index(self) -> None:
        self._sync_with_disk()
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for sample in self._items.values():
                    fh.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")
            os.replace(tmp, self.index_path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _append_index(self, samples: Iterable[Sample]) -> None:
        with open(self.index_path, "a", encoding="utf-8") as fh:
            for sample in samples:
                fh.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- write
    def add_frames(self, frames: Sequence[Dict[str, Any]], *, label: str = UNKNOWN, label_source: str = UNLABELED_UNKNOWN,
                   session: str, sample_id: Optional[str] = None, approved: bool = False, **meta: Any) -> Optional[Sample]:
        """Stores one sequence. None if `sample_id` is already stored, or there is no room (see `_make_room`)."""
        if label_source not in LABEL_SOURCES:
            raise ValueError(f"unknown label source: {label_source}")
        if not frames:
            return None
        arrays = frames_to_arrays(frames)
        sample_id = sample_id or uuid.uuid4().hex
        t = arrays["t"]
        duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
        quality = float(arrays["has_hand"].mean())
        with self._lock:
            if sample_id in self._items:
                return None
        fd, tmp = tempfile.mkstemp(dir=self.samples_dir, suffix=".tmp")
        os.close(fd)
        try:
            with open(tmp, "wb") as fh:
                np.savez_compressed(fh, **arrays)
            size = os.path.getsize(tmp)
            sample = Sample(sample_id=sample_id, label=label, label_source=label_source, session=session, created=time.time(),
                            duration=duration, fps=(len(t) - 1) / duration if duration > 0 else 0.0, frames=len(t),
                            tracking_quality=quality, quality_score=meta.pop("quality_score", quality), approved=approved, **meta)
            with self._lock:
                if not self._make_room(size, label_source):
                    os.unlink(tmp)
                    return None
                os.replace(tmp, self._path(sample_id))
                self._items[sample_id] = sample
                self._sizes[sample_id] = size
                self._append_index([sample])
            return sample
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _make_room(self, size: int, label_source: str) -> bool:
        """Oldest unchecked samples go first. What a person labelled is never removed to make room."""
        if self.total_bytes() + size <= self.max_bytes:
            return True
        removable = sorted((s for s in self._items.values() if s.label_source == UNLABELED_UNKNOWN), key=lambda s: s.created)
        removed = False
        for old in removable:
            if self.total_bytes() + size <= self.max_bytes:
                break
            self._remove(old.sample_id)
            removed = True
        if removed:
            self._rewrite_index()
        return self.total_bytes() + size <= self.max_bytes

    def set_label(self, sample_id: str, label: str, label_source: str, *, approved: bool = True) -> Sample:
        if label_source not in LABEL_SOURCES:
            raise ValueError(f"unknown label source: {label_source}")
        with self._lock:
            sample = self._items[sample_id]
            sample.label, sample.label_source, sample.approved = label, label_source, bool(approved)
            self._rewrite_index()
            return sample

    def _remove(self, sample_id: str) -> None:
        self._gone.add(sample_id)
        self._items.pop(sample_id, None)
        self._sizes.pop(sample_id, None)
        try:
            self._path(sample_id).unlink()
        except OSError:
            pass

    def delete(self, sample_ids: Iterable[str]) -> int:
        with self._lock:
            ids = [i for i in sample_ids if i in self._items]
            for sample_id in ids:
                self._remove(sample_id)
            if ids:
                self._rewrite_index()
            return len(ids)

    def prune(self, min_quality: float = .5) -> Dict[str, int]:
        """Replay memory upkeep (spec 11): drops unchecked samples whose hand was mostly missing, and exact duplicates (the same joints
        saved twice in one session; the oldest stays). What a person labelled is never dropped for quality; of two identical labelled
        samples the
        older one stays. Returns how many went for each reason."""
        with self._lock:
            low = [s.sample_id for s in self._items.values()
                   if s.quality_score < min_quality and s.label_source not in SUPERVISED_SOURCES]
            seen: Dict[str, str] = {}
            dup: List[str] = []
            for s in sorted(self._items.values(), key=lambda s: s.created):
                if s.sample_id in low:
                    continue
                try:
                    arrays = self.arrays(s.sample_id)
                except Exception:
                    continue
                import hashlib
                digest = hashlib.sha1()
                for key in sorted(arrays):
                    digest.update(key.encode())
                    digest.update(np.ascontiguousarray(arrays[key]).tobytes())
                key = f"{s.session}|{s.label}|{digest.hexdigest()}"
                if key in seen:
                    dup.append(s.sample_id)
                else:
                    seen[key] = s.sample_id
            for sample_id in low + dup:
                self._remove(sample_id)
            if low or dup:
                self._rewrite_index()
            return {"low_quality": len(low), "duplicates": len(dup)}

    def reset(self) -> None:
        """Removes every stored sample."""
        with self._lock:
            self._items.clear()
            self._sizes.clear()
            if self.samples_dir.exists():
                shutil.rmtree(self.samples_dir)
            self.samples_dir.mkdir(parents=True, exist_ok=True)
            if self.index_path.exists():
                self.index_path.unlink()

    # ---------------------------------------------------------------- read
    def get(self, sample_id: str) -> Optional[Sample]:
        with self._lock:
            return self._items.get(sample_id)

    def samples(self, *, sources: Optional[Sequence[str]] = None, label: Optional[str] = None) -> List[Sample]:
        with self._lock:
            items = list(self._items.values())
        return [s for s in items if (sources is None or s.label_source in sources) and (label is None or s.label == label)]

    def arrays(self, sample_id: str) -> Dict[str, np.ndarray]:
        with np.load(self._path(sample_id)) as data:
            return {k: data[k] for k in data.files}

    def frames(self, sample_id: str) -> List[Dict[str, Any]]:
        return arrays_to_frames(self.arrays(sample_id))

    def features(self, sample_id: str) -> np.ndarray:
        """(frames, 133) for the current feature code. Refuses a sample stored for another feature version."""
        sample = self._items[sample_id]
        if sample.preprocessing_version != FEATURE_VERSION:
            raise ValueError(f"sample uses features {sample.preprocessing_version}, the code has {FEATURE_VERSION}")
        return frame_features(self.frames(sample_id), sample.aspect)

    def total_bytes(self) -> int:
        with self._lock:
            return sum(self._sizes.values())

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            items = list(self._items.values())
        by_source: Dict[str, int] = {}
        by_label: Dict[str, int] = {}
        for sample in items:
            by_source[sample.label_source] = by_source.get(sample.label_source, 0) + 1
            by_label[sample.label] = by_label.get(sample.label, 0) + 1
        return {"samples": len(items), "bytes": self.total_bytes(), "by_source": by_source, "by_label": by_label,
                "sessions": len({s.session for s in items})}
