"""Balanced full windows and hard negatives for selective training."""
from collections import Counter

import numpy as np
from manux.perception.temporal import inputs


def sample_data(segments, classes, length, *, other_as_none=False):
    lookup = {name: i for i, name in enumerate(classes)}
    xs, ys, keys = [], [], []
    for number, segment in enumerate(segments):
        windows, valid = inputs(segment.frames, segment.aspect, length)
        labels = segment.targets()
        y = np.array([lookup.get(name, lookup.get("none", -1) if other_as_none and name
                      and name not in ("idle", "transition") else -1) for name in labels], dtype=np.int64)
        keep = valid & (y >= 0)
        if keep.any():
            xs.append(windows[keep]); ys.append(y[keep])
            group = (segment.person, segment.condition, getattr(segment, "recording_id", "") or str(number))
            keys.extend([group] * int(keep.sum()))
    if not xs:
        raise ValueError("연속된 유효 3D 관절과 라벨이 있는 학습 구간이 없습니다.")
    x, y = np.concatenate(xs), np.concatenate(ys)
    counts = Counter(zip(y.tolist(), keys))
    group_counts = Counter(cls for cls, _ in counts)
    priors = np.full(len(classes), (1 - (.5 if len(classes) == 2 else .65)) / max(1, len(classes) - 1))
    priors[lookup["none"]] = .5 if len(classes) == 2 else .65
    weights = np.array([priors[cls] / group_counts[cls] / counts[(cls, group)] for cls, group in zip(y, keys)])
    return x, y, weights / weights.sum()
