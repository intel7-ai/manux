"""Train/test splits by whole session: neighbouring windows of one demonstration must never land on both sides."""
from __future__ import annotations

from typing import Callable, Iterable, List, Sequence, Tuple

from src.online.samples import Sample

Split = Tuple[List[Sample], List[Sample]]


def split_by_session(samples: Iterable[Sample], test_sessions: Sequence[str]) -> Split:
    test = set(test_sessions)
    train, held = [], []
    for sample in samples:
        (held if sample.session in test else train).append(sample)
    return train, held


def split_by_condition(samples: Iterable[Sample], test_condition: str) -> Split:
    """Leave one posture out. The same session may be on both sides, so this measures posture changes, not new days."""
    train, held = [], []
    for sample in samples:
        (held if sample.condition == test_condition else train).append(sample)
    return train, held


def chronological_split(samples: Iterable[Sample], test_fraction: float = .2) -> Split:
    """The newest sessions are the test side (at least one, and at least one stays for training when there are two)."""
    items = list(samples)
    first_seen = {}
    for sample in items:
        first_seen[sample.session] = min(sample.created, first_seen.get(sample.session, sample.created))
    order = sorted(first_seen, key=lambda s: (first_seen[s], s))
    if not order:
        return [], []
    count = max(1, round(len(order) * test_fraction))
    if len(order) > 1:
        count = min(count, len(order) - 1)
    return split_by_session(items, order[-count:])


def leaked_groups(train: Iterable[Sample], test: Iterable[Sample], key: Callable[[Sample], str] = lambda s: s.session) -> List[str]:
    """Groups on both sides; empty for a clean split."""
    return sorted({key(s) for s in train} & {key(s) for s in test})
