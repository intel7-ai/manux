"""Stored samples -> windows for training and evaluation, without copying every window (a 64-frame window of 133 numbers would not
fit in memory many times over): frames sit in one array and a window is a list of frame positions."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from src.online.samples import Sample, SampleStore
from src.tcn_features import FEATURES

SKIP_DO_FRAMES = 10   # a guided repetition starts on the schedule, the fingers arrive a moment later


@dataclass
class WindowData:
    frames: np.ndarray        # (N + 1, FEATURES), row 0 is all zeros: the padding before a sample starts
    starts: np.ndarray        # (N + 1,) first row of the sample each row belongs to
    labels: np.ndarray        # (N + 1,) class index, -1 = not a target (history only, hand lost, start of a repetition)
    sample_of: np.ndarray     # (N + 1,) index into `samples`, -1 for row 0
    times: np.ndarray         # (N + 1,) seconds
    samples: List[Sample]
    classes: List[str]
    do_start: np.ndarray      # per sample: the row where the labelled part starts


def build_dataset(store: SampleStore, samples: Sequence[Sample], classes: Sequence[str], skip_do: int = SKIP_DO_FRAMES) -> WindowData:
    index = {name: i for i, name in enumerate(classes)}
    samples = [s for s in samples if s.label in index]
    blocks, starts, labels, owner, times, do_start = [np.zeros((1, FEATURES), np.float32)], [np.zeros(1, np.int64)], [np.full(1, -1)], [np.full(1, -1)], [np.zeros(1)], []
    row = 1
    for i, sample in enumerate(samples):
        feats = store.features(sample.sample_id)
        n = len(feats)
        context = int((sample.extra or {}).get("context_frames", 0))
        skip = skip_do if (sample.extra or {}).get("kind") == "do" else 0
        y = np.full(n, index[sample.label], dtype=np.int64)
        y[:context + skip] = -1
        y[feats[:, 132] == 0] = -1
        blocks.append(feats)
        starts.append(np.full(n, row, dtype=np.int64))
        labels.append(y)
        owner.append(np.full(n, i, dtype=np.int64))
        times.append(store.arrays(sample.sample_id)["t"])
        do_start.append(row + context)
        row += n
    return WindowData(np.concatenate(blocks), np.concatenate(starts), np.concatenate(labels), np.concatenate(owner),
                      np.concatenate(times), samples, list(classes), np.asarray(do_start, dtype=np.int64))


def window_positions(data_starts: np.ndarray, positions: np.ndarray, window: int) -> np.ndarray:
    """(len(positions), window) frame rows ending at each position. Rows before the sample's start point to the zero row."""
    offsets = np.arange(-window + 1, 1)
    rows = positions[:, None] + offsets
    rows[rows < data_starts[positions][:, None]] = 0
    return rows


def target_positions(data: WindowData, sample_ids: Optional[set] = None) -> np.ndarray:
    mask = data.labels >= 0
    if sample_ids is not None:
        chosen = np.array([s.sample_id in sample_ids for s in data.samples])
        mask &= (data.sample_of >= 0) & chosen[np.clip(data.sample_of, 0, None)]
    return np.flatnonzero(mask)


def session_folds(samples: Sequence[Sample], classes: Sequence[str], k: int = 3) -> List[List[str]]:
    """`k` groups of whole sessions for testing, spread so that a class seen in several sessions stays on both sides when it can.
    Rarest classes are placed first; each session goes to the fold that holds least of its classes."""
    by_session: Dict[str, Counter] = {}
    for sample in samples:
        if sample.label in classes:
            by_session.setdefault(sample.session, Counter())[sample.label] += 1
    totals = Counter()
    for counts in by_session.values():
        totals.update(counts)
    def rarity(session: str) -> float:
        return min(totals[label] for label in by_session[session])
    folds: List[List[str]] = [[] for _ in range(k)]
    held: List[Counter] = [Counter() for _ in range(k)]
    for session in sorted(by_session, key=lambda s: (rarity(s), s)):
        counts = by_session[session]
        def load(i: int) -> tuple:
            return (sum(held[i][c] / max(totals[c], 1) for c in counts), sum(held[i].values()), i)
        best = min(range(k), key=load)
        folds[best].append(session)
        held[best].update(counts)
    return [sorted(f) for f in folds]
