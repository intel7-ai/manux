"""Replay memory for online learning. `ReservoirBuffer` follows Buzzega et al. 2020 (DER, Algorithms 1-2): every example seen has the same
chance |M|/|S| of being stored, and it keeps the example, its label, and the network's logits at the moment it was seen.
`BalancedBuffer` keeps an equal share per class instead, for comparison and for rare corrections."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np


class ReservoirBuffer:
    def __init__(self, capacity: int, seed: int = 0) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = int(capacity)
        self.rng = np.random.default_rng(seed)
        self.seen = 0
        self.size = 0
        self.x: Optional[np.ndarray] = None
        self.y = np.zeros(self.capacity, dtype=np.int64)
        self.z: Optional[np.ndarray] = None      # logits, NaN where the head had no such class yet

    def _allocate(self, x: np.ndarray, classes: int) -> None:
        if self.x is None:
            self.x = np.zeros((self.capacity, *x.shape[1:]), dtype=np.float32)
        if self.z is None:
            self.z = np.full((self.capacity, max(classes, 1)), np.nan, dtype=np.float32)
        elif classes > self.z.shape[1]:
            wider = np.full((self.capacity, classes), np.nan, dtype=np.float32)
            wider[:, :self.z.shape[1]] = self.z
            self.z = wider

    def _store(self, i: int, x: np.ndarray, y: int, z: Optional[np.ndarray]) -> None:
        self.x[i], self.y[i] = x, y
        self.z[i] = np.nan
        if z is not None:
            self.z[i, :len(z)] = z

    def add(self, x: np.ndarray, y: np.ndarray, z: Optional[np.ndarray] = None) -> None:
        """A batch of examples (`x`: batch first), labels, and optionally logits."""
        self._allocate(x, z.shape[1] if z is not None else 1)
        for j in range(len(x)):
            zj = None if z is None else z[j]
            if self.size < self.capacity:
                self._store(self.size, x[j], int(y[j]), zj)
                self.size += 1
            else:
                slot = int(self.rng.integers(self.seen + 1))
                if slot < self.capacity:
                    self._store(slot, x[j], int(y[j]), zj)
            self.seen += 1

    def sample(self, n: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if self.size == 0:
            raise ValueError("the buffer is empty")
        index = self.rng.choice(self.size, min(n, self.size), replace=False)
        return self.x[index], self.y[index], self.z[index]

    def class_counts(self) -> Dict[int, int]:
        values, counts = np.unique(self.y[:self.size], return_counts=True)
        return {int(v): int(c) for v, c in zip(values, counts)}

    def bytes(self) -> int:
        return 0 if self.x is None else int(self.x.nbytes + self.y.nbytes + self.z.nbytes)


class BalancedBuffer(ReservoirBuffer):
    """Reservoir sampling inside each class, with the capacity shared equally: a rare class keeps its share."""

    def __init__(self, capacity: int, seed: int = 0) -> None:
        super().__init__(capacity, seed)
        self._seen_class: Dict[int, int] = {}

    def add(self, x: np.ndarray, y: np.ndarray, z: Optional[np.ndarray] = None) -> None:
        self._allocate(x, z.shape[1] if z is not None else 1)
        for j in range(len(x)):
            label = int(y[j])
            zj = None if z is None else z[j]
            self._seen_class[label] = self._seen_class.get(label, 0) + 1
            self.seen += 1
            if self.size < self.capacity:
                self._store(self.size, x[j], label, zj)
                self.size += 1
                continue
            counts = self.class_counts()
            quota = max(1, self.capacity // max(1, len(self._seen_class)))
            if counts.get(label, 0) >= quota:
                # inside its share: an ordinary reservoir step among this class's own examples
                if self.rng.integers(self._seen_class[label]) >= counts[label]:
                    continue
                slots = np.flatnonzero(self.y[:self.size] == label)
            else:
                # below its share: take room from the class with the most examples
                largest = max(counts, key=counts.get)
                slots = np.flatnonzero(self.y[:self.size] == largest)
            self._store(int(self.rng.choice(slots)), x[j], label, zj)
