"""구간 분할·중복 검사만 보존. 원본: pose-assist-v1-33dim@9b20d0d/tools/pose_data.py.
평가 도구는 무작위 split_segments 대신 파일/세션/조건/사람 전체 holdout을 사용한다.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple
import numpy as np

@dataclass(frozen=True)
class Segment:
    """Consecutive labelled frames of one class in one recording (their `(n, 33)` pose-angles rows)."""
    source: str               # recording file name
    sha256: str               # the recording file's content hash (stable id for overlap checks)
    index: int                # running number of the segment inside its recording
    person: str               # hashed alias group, "" when the recording does not say
    condition: str            # capture-plan condition key, "" when unassigned
    label: str
    rows: np.ndarray = field(repr=False)

    @property
    def frames(self) -> int:
        return len(self.rows)

    @property
    def key(self) -> Tuple[str, int]:
        return self.sha256, self.index

def split_segments(segments: Sequence[Segment], eval_fraction: float = 0.2, seed: int = 0
                   ) -> Tuple[List[Segment], List[Segment]]:
    """Move whole segments to the held-out side, class by class, until it holds ~`eval_fraction` of that class's
    frames. A class with a single segment stays entirely in training (nothing can be held out for it)."""
    if not 0.0 < eval_fraction < 1.0:
        raise ValueError("eval_fraction must be within (0, 1)")
    rng = random.Random(seed)
    train: List[Segment] = []
    held: List[Segment] = []
    for name in sorted({s.label for s in segments}):
        group = [s for s in segments if s.label == name]
        group.sort(key=lambda s: (s.sha256, s.index))
        rng.shuffle(group)
        if len(group) < 2:
            train.extend(group)
            continue
        goal = eval_fraction * sum(s.frames for s in group)
        taken = 0
        for position, segment in enumerate(group):
            left = len(group) - position
            if taken < goal and left > 1:               # always leave at least one segment in training
                held.append(segment)
                taken += segment.frames
            else:
                train.append(segment)
    return train, held

def check_disjoint(train: Sequence[Segment], held_out: Sequence[Segment]) -> None:
    """Raise ValueError when a segment, or a whole recording file, is on both sides."""
    keys = {s.key for s in train}
    clash = [s for s in held_out if s.key in keys]
    if clash:
        raise ValueError(f"{len(clash)} segment(s) are in both training and evaluation (first: {clash[0].source} #{clash[0].index})")

def check_files_disjoint(train_hashes: Iterable[str], eval_hashes: Iterable[str]) -> None:
    """Separate evaluation recordings must not be (a copy of) a training recording (as `promote_tcn` requires)."""
    clash = set(train_hashes) & set(eval_hashes)
    if clash:
        raise ValueError(f"{len(clash)} evaluation recording(s) are also training recordings")
